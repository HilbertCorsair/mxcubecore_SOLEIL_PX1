#!/usr/bin/env python3
"""Feed the PROXIMA-1 beamline cameras into argussight and register them.

This launches one standalone ``video-streamer`` per camera and registers each
resulting MPEG1 WebSocket stream into the running argussight stream proxy via
the ``AddStream`` gRPC call. Once registered, the streams appear in argussight's
``GetProcesses`` response, which mxcubeweb discovers to populate the camera
switcher (see ``mxcubeweb/core/util/argussight_discovery.py``).

PX1 vs PX2: the only camera configured out of the box is the OAV, whose frames
are published to Redis by ``redis_camera2.py`` running on the **camera server**
(not on the MXCuBE host, and not startable from here -- see check_frames.py).
Hutch cameras are listed below but commented out until their URLs are known;
add them here *and* to ARGUSSIGHT_CAMERAS in server.yaml, where the names must
match.

Prerequisites (started separately, see start_argus_px1.sh):
  * the PX1 camera publisher running on the camera server
  * argussight running -> gRPC on :50051, proxy on :7000

Nothing here touches the snapshot path; it is purely additive.
"""

import inspect
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("argus_cameras")

# --- Configuration ---------------------------------------------------------
# Host the video-streamers bind to. Must be reachable by the argussight proxy,
# which always dials the upstream at ``localhost`` (see streamsproxy.add_stream).
#
# Both STREAM_HOST and ARGUS_GRPC stay ``localhost``: they are only ever dialled
# on this host (streamers, argussight and this script all run here). Do NOT put
# the public name (mxcubeweb-px1.synchrotron-soleil.fr) here -- that routes
# local traffic through DNS/nginx and the site proxy. The public name belongs
# only in the browser-facing ARGUSSIGHT_PROXY_URL (wss://.../argus) in
# server.yaml.
STREAM_HOST = "localhost"

# argussight gRPC endpoint (see argussight/grpc/server.py -> "[::]:50051").
ARGUS_GRPC = "localhost:50051"

# argussight stream proxy; clients connect to ws://<host>:7000/ws/<name>.
ARGUS_PROXY_PORT = 7000

# Redis endpoint carrying the OAV frames. At PX1 this is the camera server, NOT
# localhost -- redis_camera2.py publishes to its own local redis. Must match the
# `uri` / `redis_key` in the camera's hardware-object config.
REDIS_URI = os.environ.get("PX1_REDIS_URI", "redis://195.221.8.84:6379")
OAV_REDIS_CHANNEL = os.environ.get("PX1_REDIS_CHANNEL", "mxcubeweb")

# One entry per camera. ``name`` is the argussight stream name and MUST match a
# camera ``name`` in server.yaml's ARGUSSIGHT_CAMERAS. ``size`` is "W,H" and is
# passed verbatim to video-streamer's ``-s`` argument.
CAMERAS = [
    {
        "name": "oav",
        "port": 9000,
        "uri": REDIS_URI,
        # Native camera frame size. RedisCamera locks ffmpeg's source size to
        # this -s value, so it MUST equal the published frame size or ffmpeg
        # aborts with a broken pipe. Must match the width/height in the camera's
        # hardware-object config, which is also what pixelsPerMm is calibrated
        # against.
        "size": "1360,1024",
        "in_redis_channel": OAV_REDIS_CHANNEL,
    },
    # --- Hutch cameras: placeholders -------------------------------------
    # Uncomment and fix the hostnames/paths once the PX1 camera URLs are known,
    # then add matching entries to ARGUSSIGHT_CAMERAS in server.yaml. "0,0"
    # means "whatever the source is": for an http:// uri video-streamer builds
    # an MJPEGCamera, which reads the first frame and detects the resolution
    # itself, so no guess is needed here (unlike the OAV above).
    # {"name": "hutch_1", "port": 9001, "uri": "http://camXX/mjpg/1/video.mjpg", "size": "0,0"},
    # {"name": "hutch_2", "port": 9002, "uri": "http://camXX/mjpg/2/video.mjpg", "size": "0,0"},
]

QUALITY = "10"
PORT_WAIT_TIMEOUT = 15.0  # seconds to wait for a streamer's port to open
PROBE_TIMEOUT = 10.0  # seconds to wait for the first frame in the self-test

# ARGUS_STREAMER_DEBUG=1 runs the streamers with -d, which stops video-streamer
# sending ffmpeg's stderr to /dev/null -- the only way to see why ffmpeg died.
STREAMER_DEBUG = os.environ.get("ARGUS_STREAMER_DEBUG", "") not in ("", "0")
# ---------------------------------------------------------------------------

_processes = []  # (name, Popen)


def _build_command(cam):
    """Build the video-streamer command line for one camera."""
    # Invoke video-streamer as a module through THIS interpreter rather than the
    # bare `video-streamer` console script. start_argus_px1.sh launches this
    # script with the mxcubeweb env's python while another conda env may be
    # active, so PATH may not have `video-streamer` at all. sys.executable is
    # the mxcubeweb python, so `-m video_streamer.main` always resolves in the
    # right env regardless of PATH / active conda env.
    cmd = [
        sys.executable, "-m", "video_streamer.main",
        "-uri", cam["uri"],
        "-hs", STREAM_HOST,
        "-p", str(cam["port"]),
        "-q", QUALITY,
        "-s", cam["size"],
        "-of", "MPEG1",
        "-id", cam["name"],
    ]
    # RedisCamera input (OAV): tell the streamer which pubsub channel to read.
    if cam.get("in_redis_channel"):
        cmd += ["-irc", cam["in_redis_channel"]]
    if STREAMER_DEBUG:
        cmd.append("-d")
    return cmd


def _wait_for_port(host, port, timeout):
    """Return True once ``host:port`` accepts a TCP connection, else False."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.3)
    return False


# Proxy env vars that must never reach the streamers OR our own gRPC client.
# On the beamline these point at the SOLEIL site proxy (:8080), which cannot
# route to LAN/localhost. Everything we talk to (redis, argussight, the streamer
# ports, the hutch cameras) is LAN/localhost, so no proxy is ever wanted.
_PROXY_VARS = (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
)


def _strip_proxy(env):
    """Remove every proxy var from ``env`` and force a wildcard no_proxy."""
    for var in _PROXY_VARS:
        env.pop(var, None)
    env["no_proxy"] = env["NO_PROXY"] = "*"
    return env


def start_streamers():
    """Launch one video-streamer per camera."""
    env = _strip_proxy(os.environ.copy())
    for cam in CAMERAS:
        cmd = _build_command(cam)
        logger.info("starting %s: %s", cam["name"], " ".join(cmd))
        proc = subprocess.Popen(cmd, close_fds=True, env=env)
        _processes.append((cam["name"], proc))


def register_streams():
    """Register every streamer into the argussight proxy via AddStream gRPC."""
    import grpc

    import argussight.grpc.argus_service_pb2 as pb2
    import argussight.grpc.argus_service_pb2_grpc as pb2_grpc

    # grpc honours http_proxy/https_proxy too. Even with the parent env stripped
    # in main(), disable proxy on the channel explicitly so a stray proxy var can
    # never make us dial localhost:50051 through the SOLEIL proxy.
    with grpc.insecure_channel(
        ARGUS_GRPC, options=[("grpc.enable_http_proxy", 0)]
    ) as channel:
        stub = pb2_grpc.SpawnerServiceStub(channel)
        for cam in CAMERAS:
            name, port = cam["name"], cam["port"]
            if not _wait_for_port(STREAM_HOST, port, PORT_WAIT_TIMEOUT):
                logger.warning(
                    "%s: port %s did not open in %ss; registering anyway "
                    "(proxy retries the upstream connection)",
                    name, port, PORT_WAIT_TIMEOUT,
                )
            try:
                resp = stub.AddStream(
                    pb2.AddStreamRequest(name=name, port=str(port), stream_id=name)
                )
                logger.info("registered %s -> status=%s", name, resp.status)
            except Exception:
                logger.exception("failed to register %s", name)


def preflight():
    """Fail fast on the MPEG1-only dependencies MJPEG never needed.

    Checked against THIS interpreter and PATH, which is what the streamers
    inherit (start_streamers uses sys.executable), not the argussight env.
    """
    # video-streamer pipes frames into ffmpeg with stderr to /dev/null; without
    # ffmpeg the streamer's websocket opens and then never sends a byte.
    if shutil.which("ffmpeg") is None:
        logger.error(
            "ffmpeg not found on PATH (%s); the MPEG1 streamers cannot encode. "
            "Install it into the %s env.",
            os.environ.get("PATH", ""), sys.prefix,
        )
        sys.exit(1)

    if not any(cam.get("in_redis_channel") for cam in CAMERAS):
        return
    # The OAV needs the SOLEIL video-streamer fork (px2_video_streamer_v1.9.1):
    # upstream v1.9.1's RedisCamera sniffs the frame size and ffmpeg dies on a
    # broken pipe. The fork is recognisable by RedisCamera taking ``size``.
    try:
        from video_streamer.core.camera import RedisCamera

        has_size = "size" in inspect.signature(RedisCamera.__init__).parameters
    except Exception:
        has_size = False
    if not has_size:
        logger.warning(
            "video_streamer in %s has no RedisCamera(size=...): this is not the "
            "SOLEIL fork (px2_video_streamer_v1.9.1). The OAV stream will likely "
            "be black.", sys.prefix,
        )


def _probe_stream(url, timeout=PROBE_TIMEOUT):
    """Wait for the first binary frame on ``url``; return (ok, detail)."""
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    try:
        try:
            # websockets >= 15 would otherwise honour http(s)_proxy.
            ws = connect(url, open_timeout=timeout, proxy=None)
        except TypeError:  # websockets < 15: no proxy support, no kwarg
            ws = connect(url, open_timeout=timeout)
    except Exception as exc:
        return False, f"cannot connect ({exc})"

    deadline = time.monotonic() + timeout
    try:
        with ws:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, f"connected but no data in {timeout:.0f}s"
                try:
                    msg = ws.recv(timeout=remaining)
                except TimeoutError:
                    continue
                if isinstance(msg, bytes):
                    return True, f"{len(msg)} bytes"
    except ConnectionClosed as exc:
        rcvd = exc.rcvd
        if rcvd is None:
            return False, "closed without a close frame"
        return False, f"closed with code {rcvd.code} ({rcvd.reason or 'no reason'})"
    except Exception as exc:
        return False, f"error ({exc})"


def self_test():
    """Probe each camera direct and through the proxy; log one verdict each.

    Diagnostic only: never exits, so a slow first frame cannot take the
    stack down.
    """
    try:
        import websockets.sync.client  # noqa: F401  (websockets >= 11)
    except ImportError:
        logger.warning("SELF-TEST skipped: websockets.sync not importable here")
        return
    for cam in CAMERAS:
        name, port = cam["name"], cam["port"]
        direct = f"ws://127.0.0.1:{port}/ws/{name}"
        ok, detail = _probe_stream(direct)
        if not ok:
            logger.error(
                "SELF-TEST %s: streamer %s gives no video: %s. The streamer is not "
                "running or ffmpeg produces nothing (died / no frames from the camera). "
                "Rerun with ARGUS_STREAMER_DEBUG=1 to see ffmpeg's stderr.",
                name, direct, detail,
            )
            continue
        proxied = f"ws://127.0.0.1:{ARGUS_PROXY_PORT}/ws/{name}"
        ok, detail = _probe_stream(proxied)
        if not ok:
            logger.error(
                "SELF-TEST %s: streamer OK but the argussight proxy gives no video: "
                "%s at %s. argussight dropped the stream -- look for 'Upstream "
                "worker for %s failed' in argussight.log (proxy env vars?).",
                name, detail, proxied, name,
            )
            continue
        logger.info("SELF-TEST %s: streaming OK (direct and via proxy)", name)


def shutdown(*_):
    """Terminate all streamers (and their ffmpeg children) and exit."""
    logger.info("shutting down streamers ...")
    for _, proc in _processes:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.monotonic() + 5
    for _, proc in _processes:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
    logger.info("done")
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Strip proxy vars from our OWN process env, not just the streamer children:
    # register_streams()' gRPC client runs here and would otherwise dial
    # localhost:50051 through the SOLEIL proxy. Children inherit this clean env.
    _strip_proxy(os.environ)

    preflight()
    start_streamers()
    register_streams()
    self_test()

    logger.info("all cameras registered; supervising streamers (Ctrl-C to stop)")
    # Supervise: if a streamer dies, log it. The argussight proxy independently
    # retries/drops the upstream, so we only need to surface the failure here.
    while True:
        for name, proc in _processes:
            rc = proc.poll()
            if rc is not None:
                logger.error("streamer %s exited with code %s", name, rc)
        time.sleep(5)


if __name__ == "__main__":
    main()
