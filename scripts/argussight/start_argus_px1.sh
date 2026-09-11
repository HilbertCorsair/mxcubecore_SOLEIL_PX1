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

# Activate the conda env holding argussight + video-streamer. Override with
# CONDA_ACTIVATE=/path/to/activate, or set it empty to skip (e.g. on a dev
# machine where both are already importable). CONDA_ENV picks the env.
CONDA_ACTIVATE="${CONDA_ACTIVATE-$HOME/miniconda3/bin/activate}"
CONDA_ENV="${CONDA_ENV-base}"

# The python helpers import redis/grpc/video_streamer, which live in the
# *mxcubeweb* env (same as the MXCuBE app). Launch them with that env's
# interpreter directly rather than switching the active env, so argussight keeps
# whatever env it needs. Override with MXCUBE_PY / MXCUBE_ENV.
CONDA_ROOT="$(dirname "$(dirname "$CONDA_ACTIVATE")")"
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
        source "$CONDA_ACTIVATE" "$CONDA_ENV"
        set -u
    else
        echo "conda activate script not found ($CONDA_ACTIVATE); using current environment" >&2
    fi
fi

ARGUSSIGHT_BIN="${ARGUSSIGHT_BIN-argussight}"

# --- 1. the camera gate ----------------------------------------------------
# Runs BEFORE anything is started. Exits non-zero if the operator cancels or if
# frames still are not arriving after they acknowledge the prompt; `set -e` then
# stops us here and MXCuBE never comes up with a dead video pane.
echo "checking that the camera is publishing frames ..."
"$MXCUBE_PY" "$HERE/check_frames.py" \
    --uri "$PX1_REDIS_URI" --channel "$PX1_REDIS_CHANNEL"

: > "$PIDFILE"

cleanup() {
    echo "stopping argussight stack ..."
    # Kill children first (argus_cameras), then argussight. Only processes we
    # started ourselves are in the pidfile.
    while read -r pid; do
        kill "$pid" 2>/dev/null || true
    done < <(tac "$PIDFILE")
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- 2. argussight ---------------------------------------------------------
echo "starting argussight (gRPC :50051, proxy :7000) ..."
"$ARGUSSIGHT_BIN" &
echo $! >> "$PIDFILE"

# Give the gRPC server + proxy a moment to bind before registering streams.
sleep 3

# --- 3. the streamers ------------------------------------------------------
echo "starting camera streamers ..."
"$MXCUBE_PY" "$HERE/argus_cameras.py" &
echo $! >> "$PIDFILE"

wait
