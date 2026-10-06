#!/bin/bash
# Start the argussight multi-camera stack for MXCuBE at PROXIMA-1.
#
#   1. check_frames.py     -> refuses to start unless the camera is publishing
#                             (the camera runs on ANOTHER server and cannot be
#                             started from here; the operator is prompted)
#   2. argussight          -> gRPC server on :50051 + stream proxy on :7000
#   3. argus_cameras.py    -> one video-streamer per camera, registered into
#                             the proxy
#
# mxcubeweb discovers the registered streams over gRPC and shows them in the
# camera switcher (requires ARGUSSIGHT_ENABLED: true in server.yaml).
#
# Usage:
#   ./start_argus_px1.sh
#
# Stop everything with Ctrl-C, or:  kill $(cat /tmp/argus.pids)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="${PIDFILE-/tmp/argus.pids}"

# Redis carrying the OAV frames. At PX1 this is the camera server, not
# localhost. Exported so check_frames.py and argus_cameras.py agree.
export PX1_REDIS_URI="${PX1_REDIS_URI-redis://195.221.8.84:6379}"
export PX1_REDIS_CHANNEL="${PX1_REDIS_CHANNEL-mxcubeweb}"

# Two envs, each with what it needs (their pins cannot share one env):
#   * argussight env (activated below; CONDA_ENV) -- argussight itself plus the
#     helpers check_frames.py and argus_cameras.py (redis, grpc, websockets).
#     It does NOT need video-streamer; argussight never imports it.
#   * mxcubeweb env (MXCUBE_ENV) -- runs the video-streamer processes, with the
#     same video-streamer MXCuBE's own RedisMpegVideo uses.
# Override with CONDA_ACTIVATE=/path/to/activate (empty: use the current
# environment, e.g. on a dev machine), CONDA_ENV, HELPER_PY, MXCUBE_ENV,
# MXCUBE_PY.
# Same default as mxgo.sh's ARGUS_CONDA_ENV; base has no argussight (exit 127).
CONDA_ENV="${CONDA_ENV-argussight}"

in_conda_env() {
    [ "${CONDA_DEFAULT_ENV:-}" = "$CONDA_ENV" ] \
        || [ "$(basename "${CONDA_PREFIX:-/}")" = "$CONDA_ENV" ]
}

# Unset: look for the conda install wherever it can be -- the conda that runs
# this shell ($CONDA_EXE), the root of the active env, the conda on PATH, then
# the usual install dirs. The first bin/activate found wins.
CONDA_ACTIVATE_TRIED=""
if [ -z "${CONDA_ACTIVATE+x}" ]; then
    CONDA_ACTIVATE=""
    for root in \
        "${CONDA_EXE:+$(dirname "$(dirname "$CONDA_EXE")")}" \
        "$(case "${CONDA_PREFIX:-}" in */envs/*) echo "${CONDA_PREFIX%/envs/*}" ;; *) echo "${CONDA_PREFIX:-}" ;; esac)" \
        "$(conda info --base 2>/dev/null || true)" \
        "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/mambaforge" \
        /opt/conda /opt/miniconda3 /opt/anaconda3; do
        [ -n "$root" ] || continue
        CONDA_ACTIVATE_TRIED="$CONDA_ACTIVATE_TRIED $root/bin/activate"
        if [ -f "$root/bin/activate" ]; then
            CONDA_ACTIVATE="$root/bin/activate"
            break
        fi
    done
    if [ -z "$CONDA_ACTIVATE" ]; then
        if in_conda_env; then
            # Started from inside the right env: nothing to activate.
            echo "no conda activate script found; '$CONDA_ENV' is already active, using it"
        else
            echo "ERROR: no conda activate script found (tried:$CONDA_ACTIVATE_TRIED)," \
                 "and the current env is '${CONDA_DEFAULT_ENV:-none}', not '$CONDA_ENV'." >&2
            echo "       Activate '$CONDA_ENV' first, or set CONDA_ACTIVATE=<conda root>/bin/activate." >&2
            exit 1
        fi
    fi
fi

# Interpreter for the video-streamers, called directly (no env switch). The
# conda root comes from the activate script, else from the active env.
if [ -n "$CONDA_ACTIVATE" ]; then
    CONDA_ROOT="$(dirname "$(dirname "$CONDA_ACTIVATE")")"
else
    case "${CONDA_PREFIX:-}" in
        */envs/*) CONDA_ROOT="${CONDA_PREFIX%/envs/*}" ;;
        *) CONDA_ROOT="${CONDA_PREFIX:-}" ;;
    esac
fi
MXCUBE_ENV="${MXCUBE_ENV-mxcubeweb}"
MXCUBE_PY="${MXCUBE_PY-$CONDA_ROOT/envs/$MXCUBE_ENV/bin/python}"
[ -x "$MXCUBE_PY" ] || MXCUBE_PY=python3

if [ -n "$CONDA_ACTIVATE" ]; then
    if [ -f "$CONDA_ACTIVATE" ]; then
        echo "activating conda environment ($CONDA_ACTIVATE $CONDA_ENV) ..."
        # conda's activate script references unbound vars; relax `set -u` for it.
        # Pass the env name explicitly -- sourced with no args it would inherit
        # this script's positional params as the env name.
        set +u
        # shellcheck disable=SC1090
        source "$CONDA_ACTIVATE" "$CONDA_ENV" || true
        set -u
        # A missing env does not always fail the source; carrying on in the
        # wrong env ends in "failed to execute argussight" (exit 127) later.
        if ! in_conda_env; then
            echo "ERROR: could not activate conda env '$CONDA_ENV' with $CONDA_ACTIVATE" \
                 "(active: '${CONDA_DEFAULT_ENV:-none}')." >&2
            echo "       Check it exists: conda env list. Override with CONDA_ENV=<name>" \
                 "or CONDA_ACTIVATE=<conda root>/bin/activate." >&2
            exit 1
        fi
    elif in_conda_env; then
        echo "conda activate script not found ($CONDA_ACTIVATE);" \
             "'$CONDA_ENV' is already active, using it"
    else
        echo "ERROR: conda activate script not found ($CONDA_ACTIVATE), so env" \
             "'$CONDA_ENV' cannot be activated." >&2
        echo "       Set CONDA_ACTIVATE=<conda root>/bin/activate, activate" \
             "'$CONDA_ENV' first, or CONDA_ACTIVATE= to use the current environment." >&2
        exit 1
    fi
fi

# Interpreter for the helpers: the (now active) argussight env's python.
HELPER_PY="${HELPER_PY-$(command -v python || command -v python3)}"
# argus_cameras.py starts the streamers with this one.
export ARGUS_STREAMER_PY="$MXCUBE_PY"
echo "helpers on $HELPER_PY; video-streamers on $ARGUS_STREAMER_PY"

# No proxy for anything started from here. On the beamline http(s)_proxy
# point at the SOLEIL site proxy, and websockets >= 15 honours them even for
# ws://localhost: argussight's upstream worker then dials the streamer
# (ws://localhost:9000/ws/oav) through the site proxy, fails three times, drops
# the stream, and the browser's websocket is refused (HTTP 403) -- a black
# sample view.
# grpc honours them too. Everything this stack talks to (redis on the camera
# server, argussight, the streamer ports) is LAN/localhost. Done after the
# conda activation so an env's activate hook cannot put them back.
# argus_cameras.py strips them for itself as well (_strip_proxy).
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export no_proxy="*" NO_PROXY="*"

# How to run argussight, resolved now so a broken env fails before the operator
# is asked about the camera. The helpers import argussight too (argus_cameras.py
# registers the streams with its gRPC stubs), so it must be importable by
# HELPER_PY; argussight itself then runs from that same env -- not from some
# other env's `argussight` that happens to be on PATH (e.g. base's). The console
# script exists only when the package was pip-installed; an importable source
# checkout has none (setsid: "failed to execute argussight", exit 127), so fall
# back to running the module.
if ! import_err=$("$HELPER_PY" -c "import argussight.main, argussight.grpc.argus_service_pb2_grpc" 2>&1); then
    echo "ERROR: $HELPER_PY (${CONDA_PREFIX:-no conda env}) cannot import argussight:" >&2
    echo "       ${import_err##*$'\n'}" >&2
    if [[ "$import_err" == *"No module named 'argussight"* ]]; then
        echo "       Install it into the '$CONDA_ENV' env, e.g.:" \
             "pip install -e /nfs/ruche/share-dev/px1dev/MXCuBE/WebApp/argussight" >&2
    else
        # e.g. PyPI argussight 0.3.2 does not declare its psutil dependency.
        echo "       argussight is there but one of its dependencies is not;" \
             "pip install it into the '$CONDA_ENV' env." >&2
    fi
    exit 1
fi
if [ -n "${ARGUSSIGHT_BIN:-}" ]; then
    ARGUS_CMD=("$ARGUSSIGHT_BIN")
elif [ -x "$(dirname "$HELPER_PY")/argussight" ]; then
    ARGUS_CMD=("$(dirname "$HELPER_PY")/argussight")
else
    ARGUS_CMD=("$HELPER_PY" -m argussight.main)
fi
echo "argussight command: ${ARGUS_CMD[*]}"

# argussight >= 0.3.2 reads <dir>/config.yaml from `-c <dir>`; without it, it
# looks in the current directory, dies with FileNotFoundError before binding
# anything, and mxgo.sh then waits out its whole timeout. This is PX1's own
# config (processes: [], log_dir set -- see config/config.yaml).
ARGUS_CONFIG_DIR="${ARGUS_CONFIG_DIR-$HERE/config}"
# Where argussight runs, so where its relative log_dir ("logs") lands. Must be
# writable, which the NFS mxcubeweb directory mxgo.sh starts from may not be.
ARGUS_WORKDIR="${ARGUS_WORKDIR-$HOME/MXCuBElogs/argussight}"
# Seconds to wait for argussight to bind :50051 and :7000.
ARGUS_BIND_TIMEOUT="${ARGUS_BIND_TIMEOUT-30}"
# Ports this stack binds: argussight gRPC, argussight proxy, and one per
# streamer, read from argus_cameras.py's CAMERAS so they cannot drift.
if ! camera_ports=$(cd "$HERE" && "$HELPER_PY" -c \
        'import argus_cameras as a; print(*[c["port"] for c in a.CAMERAS])' 2>/dev/null); then
    camera_ports="9000 9001 9002 9003 9004"
fi
STACK_PORTS="50051 7000 $camera_ports"

# Is anything LISTENING on this local port? Same test as mxgo.sh's port_open.
port_open() {
    if command -v ss > /dev/null 2>&1; then
        ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN
    else
        timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" 2>/dev/null
    fi
}

# Stop one pid recorded in $PIDFILE, and only if it is ours. argussight runs in
# its own session (setsid below) so that its stream-proxy and manager children
# can be stopped with it: after a crash or kill -9 they are orphaned and the
# proxy keeps holding :7000. A pid since reused by something else is left alone.
stop_recorded() {
    pkill -TERM -s "$1" -f argussight 2>/dev/null || true
    if ps -o args= -p "$1" 2>/dev/null | grep -qE "argussight|argus_cameras"; then
        kill "$1" 2>/dev/null || true
    fi
}

# argussight's own redis settings (-hs/-p/-ch; its defaults are
# localhost:6379, channel "video-streamer"). Nothing at PX1 uses them while
# processes is empty, but they should name the real camera redis rather than
# one that does not exist. redis://[user:pass@]host[:port][/db]
redis_hostport="${PX1_REDIS_URI#*://}"
redis_hostport="${redis_hostport%%/*}"
redis_hostport="${redis_hostport##*@}"
REDIS_HOST="${redis_hostport%%:*}"
REDIS_PORT=6379
[ "$redis_hostport" != "$REDIS_HOST" ] && REDIS_PORT="${redis_hostport##*:}"

# --- 0. a previous stack ---------------------------------------------------
# A stack left over from an earlier run (e.g. argussight crashed but the
# streamers kept running) still holds its ports, and the new one would fail to
# bind them. Stop whatever the previous run recorded -- only our own processes,
# so a pid since reused by something else is left alone.
if [ -s "$PIDFILE" ]; then
    while read -r pid; do
        if [ -n "$(pgrep -s "$pid" -f argussight 2>/dev/null)" ] \
            || ps -o args= -p "$pid" 2>/dev/null | grep -qE "argussight|argus_cameras"; then
            echo "stopping leftover processes of $pid from a previous run"
            stop_recorded "$pid"
        fi
    done < <(tac "$PIDFILE")
    # argussight shuts down gracefully (up to ~20 s); wait for the ports.
    for _ in $(seq 25); do
        busy=""
        for port in $STACK_PORTS; do port_open "$port" && busy="$busy $port"; done
        [ -z "$busy" ] && break
        sleep 1
    done
fi
for port in $STACK_PORTS; do
    if port_open "$port"; then
        echo "ERROR: port $port is already in use; not starting argussight." >&2
        echo "       Find the owner with: ss -ltnp 'sport = :$port'" >&2
        exit 1
    fi
done

# --- 1. the camera gate ----------------------------------------------------
# Runs BEFORE anything is started. Exits non-zero if the operator cancels or if
# frames still are not arriving after they acknowledge the prompt; `set -e` then
# stops us here and MXCuBE never comes up with a dead video pane.
echo "checking that the camera is publishing frames ..."
"$HELPER_PY" "$HERE/check_frames.py" \
    --uri "$PX1_REDIS_URI" --channel "$PX1_REDIS_CHANNEL"

: > "$PIDFILE"

cleanup() {
    echo "stopping argussight stack ..."
    # Kill children first (argus_cameras), then argussight with its session.
    # Only processes we started ourselves are in the pidfile.
    while read -r pid; do
        stop_recorded "$pid"
    done < <(tac "$PIDFILE")
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- 2. argussight ---------------------------------------------------------
if [ ! -f "$ARGUS_CONFIG_DIR/config.yaml" ]; then
    echo "ERROR: argussight config not found: $ARGUS_CONFIG_DIR/config.yaml" >&2
    exit 1
fi
mkdir -p "$ARGUS_WORKDIR"
echo "starting argussight (gRPC :50051, proxy :7000; config $ARGUS_CONFIG_DIR," \
     "logs $ARGUS_WORKDIR/logs) ..."
# exec + setsid: the recorded pid is argussight itself, and it leads its own
# session, which stop_recorded uses to reach its children (see above). The
# background subshell is not a process-group leader, so setsid does not fork.
(cd "$ARGUS_WORKDIR" && exec setsid "${ARGUS_CMD[@]}" -c "$ARGUS_CONFIG_DIR" \
    -hs "$REDIS_HOST" -p "$REDIS_PORT" -ch "$PX1_REDIS_CHANNEL") &
argus_pid=$!
echo "$argus_pid" >> "$PIDFILE"

# Wait for BOTH ports: :50051 is how streams are registered and discovered,
# :7000 is where the browser gets the video; one without the other is useless.
# If argussight dies instead, stop now with its error rather than leaving the
# streamers running -- that kept this script alive and made mxgo.sh wait out
# its whole timeout for a stack that was never coming up.
waited=0
until port_open 50051 && port_open 7000; do
    if ! kill -0 "$argus_pid" 2>/dev/null; then
        rc=0
        wait "$argus_pid" || rc=$?
        echo "ERROR: argussight exited with code $rc before binding its ports" \
             "(its traceback is above)." >&2
        if [ -f "$ARGUS_WORKDIR/logs/Shared_logs.log" ]; then
            echo "--- last lines of $ARGUS_WORKDIR/logs/Shared_logs.log:" >&2
            tail -n 15 "$ARGUS_WORKDIR/logs/Shared_logs.log" >&2
        fi
        exit 1
    fi
    if [ "$waited" -ge "$ARGUS_BIND_TIMEOUT" ]; then
        down=""
        port_open 50051 || down="$down :50051"
        port_open 7000 || down="$down :7000"
        echo "ERROR: argussight still not listening on$down after ${ARGUS_BIND_TIMEOUT}s." >&2
        echo "       Logs: $ARGUS_WORKDIR/logs/" >&2
        exit 1
    fi
    sleep 1
    waited=$((waited + 1))
done
echo "argussight up after ${waited}s"

# --- 3. the streamers ------------------------------------------------------
echo "starting camera streamers ..."
"$HELPER_PY" "$HERE/argus_cameras.py" &
cameras_pid=$!
echo "$cameras_pid" >> "$PIDFILE"

# --- 4. supervise ----------------------------------------------------------
# The stack is only useful whole. If either half exits, say which and exit;
# the EXIT trap then stops the other, so no half-up stack is left holding the
# ports for the next launch.
# (Polled rather than `wait -n <pids>`, which needs bash >= 5.1.)
while kill -0 "$argus_pid" 2>/dev/null && kill -0 "$cameras_pid" 2>/dev/null; do
    sleep 2
done
if ! kill -0 "$argus_pid" 2>/dev/null; then
    rc=0
    wait "$argus_pid" || rc=$?
    echo "ERROR: argussight exited (code $rc); stopping the camera streamers." >&2
else
    rc=0
    wait "$cameras_pid" || rc=$?
    echo "ERROR: argus_cameras.py exited (code $rc); stopping argussight." >&2
fi
exit 1
