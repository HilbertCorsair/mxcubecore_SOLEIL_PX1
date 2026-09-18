#!/usr/bin/env python3
"""Find where the PX1 sample-view video chain breaks.

The OAV image reaches the browser through five hops:

  1. video-streamer   ws://localhost:<port>/ws/<name>      (argus_cameras.py)
  2. argussight proxy ws://localhost:7000/ws/<name>        (argussight, uvicorn)
  3. MXCuBE discovery gRPC GetProcesses on :50051 -> videoURL for the browser
  4. server.yaml      VIDEO_FORMAT / ARGUSSIGHT_* decide what the browser opens
  5. nginx            wss://<public name>/argus/<name> -> :7000

argus_cameras.py's SELF-TEST only covers hops 1-2, so "SELF-TEST OK" and a black
pane means hop 3, 4 or 5, or the page itself. This script checks every hop from
the MXCuBE host, plus which UI bundle mxcubeweb serves (a bundle from before the
camera switcher builds its own stream URL), prints PASS/FAIL per hop, the first
broken one with what to do, and summarises the uvicorn tracebacks in
argussight.log.

Run it on the MXCuBE host in the argussight env (it has grpc, websockets, yaml):

    python scripts/argussight/diagnose_video.py [--config .../config/server.yaml]

Server-side hops use localhost on purpose: argussight itself always dials the
streamers at ws://localhost:<port> (streamsproxy.py). Only the browser-facing
ARGUSSIGHT_PROXY_URL must be the public wss:// name.
"""

import argparse
import ipaddress
import os
import re
import ssl
import sys
import time
from collections import Counter
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argus_cameras as ac  # noqa: E402  (camera list, ports, stream probe)

HERE = os.path.dirname(os.path.abspath(__file__))
# mxgo.sh starts mxcubeweb-server with `-r ../config` from the mxcubeweb repo;
# mxcubecore sits next to it, so that is ../../../config from this script.
DEFAULT_CONFIG = os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "config", "server.yaml")
)
# The mxcubeweb repo, next to mxcubecore.
DEFAULT_WEBROOT = os.path.normpath(os.path.join(HERE, "..", "..", "..", "mxcubeweb"))
# An action type of the camera switcher: present in any bundle built from
# feature/argussight_sampleview, even minified.
NEW_BUNDLE_MARKER = b"SELECT_CAMERA"
LOG_DIR = os.path.join(os.path.expanduser("~"), "MXCuBElogs")
PROBE_TIMEOUT = 5.0

# Known uvicorn tracebacks from argussight's proxy: (pattern, verdict).
KNOWN_TRACEBACKS = [
    (
        "WebsocketState",
        "HARMLESS but noisy: typo in the hand-edited double-close guard in "
        "argussight/streamsproxy.py (WebsocketState -> WebSocketState, plus the "
        "'from starlette.websockets import WebSocketState' import). Fires when a "
        "viewer disconnects.",
    ),
    (
        'Cannot call "send" once a close message has been sent',
        "HARMLESS: argussight 0.3.2 closes an already-closed websocket when a "
        "viewer disconnects (upstream bug). Does not affect the video.",
    ),
    (
        "Unexpected ASGI message 'websocket.close'",
        "HARMLESS: same double-close on viewer disconnect (other wording).",
    ),
]

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d")
_EXC_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Closed\w*)\b")

results = []  # (hop, status, message, fix)  status in PASS/WARN/FAIL/SKIP


def report(hop, status, message, fix=""):
    results.append((hop, status, message, fix))
    print(f"[{status:4}] {hop}: {message}")
    if fix and status in ("FAIL", "WARN"):
        print(f"       -> {fix}")


# --- config (hop 4) --------------------------------------------------------


def load_yaml(path):
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        import ruamel.yaml

        with open(path, encoding="utf-8") as f:
            return ruamel.yaml.YAML(typ="safe").load(f)


def _host_kind(host):
    if not host:
        return "missing"
    if host == "localhost" or host.endswith(".localhost"):
        return "loopback"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "name"
    return "loopback" if ip.is_loopback or ip.is_unspecified else "ip"


def check_config(path):
    """Return the `mxcube:` section of server.yaml (or {} if unreadable)."""
    hop = "config"
    try:
        cfg = load_yaml(path) or {}
    except FileNotFoundError:
        report(
            hop, "FAIL", f"{path} not found", "pass --config <.../config/server.yaml>"
        )
        return {}
    except ImportError:
        report(hop, "SKIP", "neither yaml nor ruamel.yaml importable here")
        return {}
    except Exception as exc:
        report(hop, "FAIL", f"cannot parse {path}: {exc}")
        return {}

    app = cfg.get("mxcube") or {}
    print(f"       {path} (mxcube: section)")
    for key in (
        "ARGUSSIGHT_ENABLED",
        "VIDEO_FORMAT",
        "USE_EXTERNAL_STREAMER",
        "ARGUSSIGHT_PROXY_URL",
        "VIDEO_STREAM_URL",
        "ARGUSSIGHT_GRPC_HOST",
        "ARGUSSIGHT_GRPC_PORT",
    ):
        print(f"         {key}: {app.get(key, '<unset>')!r}")
    names = [c.get("name") for c in app.get("ARGUSSIGHT_CAMERAS") or []]
    print(f"         ARGUSSIGHT_CAMERAS names: {names or '<unset: all streams>'}")

    ok = True
    if app.get("ARGUSSIGHT_ENABLED") is not True:
        ok = False
        report(
            hop,
            "FAIL",
            "ARGUSSIGHT_ENABLED is not true: MXCuBE never asks argussight "
            "and the browser gets VIDEO_STREAM_URL (port 8000, nothing serves it)",
            "set ARGUSSIGHT_ENABLED: true under mxcube: in server.yaml, restart MXCuBE",
        )
    if str(app.get("VIDEO_FORMAT", "MPEG1")).upper() != "MPEG1":
        ok = False
        report(
            hop,
            "FAIL",
            f"VIDEO_FORMAT is {app.get('VIDEO_FORMAT')!r}: the page draws an "
            "<img> instead of the MPEG1 canvas, so the argussight stream cannot show",
            "set VIDEO_FORMAT: MPEG1, restart MXCuBE",
        )
    if app.get("USE_EXTERNAL_STREAMER") is True:
        report(
            hop,
            "WARN",
            "USE_EXTERNAL_STREAMER is true: MXCuBE starts its own "
            "streamer too (the name reads backwards)",
            "set USE_EXTERNAL_STREAMER: false with argussight",
        )

    url = app.get("ARGUSSIGHT_PROXY_URL") or ""
    parsed = urlparse(url)
    kind = _host_kind(parsed.hostname)
    if not url:
        ok = False
        report(
            hop,
            "FAIL",
            "ARGUSSIGHT_PROXY_URL is empty",
            "set it to wss://mxcubeweb-px1.synchrotron-soleil.fr/argus",
        )
    elif kind == "loopback":
        ok = False
        report(
            hop,
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} points at localhost: the BROWSER "
            "opens this URL, i.e. the operator's own PC, not this host",
            "use the public name: wss://mxcubeweb-px1.synchrotron-soleil.fr/argus",
        )
    elif parsed.scheme == "ws":
        ok = False
        report(
            hop,
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} is ws://: an https MXCuBE page "
            "blocks it as mixed content",
            "use wss://mxcubeweb-px1.synchrotron-soleil.fr/argus (nginx location /argus/)",
        )
    elif parsed.scheme != "wss":
        ok = False
        report(
            hop,
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} is not a wss:// URL",
            "use wss://mxcubeweb-px1.synchrotron-soleil.fr/argus",
        )
    elif kind == "ip":
        report(
            hop,
            "WARN",
            f"ARGUSSIGHT_PROXY_URL {url} uses a bare IP: the TLS "
            "certificate is for the host name, so the browser may refuse it",
            "use wss://mxcubeweb-px1.synchrotron-soleil.fr/argus",
        )
    if ok:
        report(hop, "PASS", "server.yaml video settings look right")
    return app


# --- the page (what the browser runs) --------------------------------------


def _bundle_has_switcher(bundle):
    for dirpath, _, files in os.walk(bundle):
        for name in files:
            if name.endswith(".js"):
                with open(os.path.join(dirpath, name), "rb") as f:
                    if NEW_BUNDLE_MARKER in f.read():
                        return True
    return False


def check_frontend(webroot):
    """Return (bundle_is_new, backend_splits_url); None where unknown.

    mxcubeweb/server.py serves <webroot>/mxcubeweb/ui whatever --static-folder
    says, and nothing rebuilds it. A bundle from before the camera switcher
    opens `${videoURL}/${videoHash}` even when the hash is empty, so with a
    backend that sends the whole stream URL plus an empty hash the browser asks
    for ".../oav/", which argussight's /ws/{path} route rejects.
    """
    hop = "page"
    bundle = os.path.join(webroot, "mxcubeweb", "ui")
    backend = os.path.join(webroot, "mxcubeweb", "core", "components", "beamline.py")

    splits = None
    try:
        with open(backend, encoding="utf-8") as f:
            splits = 'oav_url.rpartition("/")' in f.read()
    except OSError as exc:
        report(hop, "SKIP", f"cannot read {backend}: {exc}; pass --webroot")

    index = os.path.join(bundle, "index.html")
    if not os.path.isfile(index):
        report(hop, "SKIP", f"no {index}; pass --webroot <mxcubeweb repo>")
        return None, splits
    built = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(index)))
    real = os.path.realpath(bundle)
    where = bundle if real == bundle else f"{bundle} -> {real}"
    is_new = _bundle_has_switcher(bundle)
    print(f"       served bundle {where}, built {built}")

    if is_new:
        report(hop, "PASS", "the served UI bundle has the camera switcher")
    elif splits is False:
        report(
            hop,
            "FAIL",
            "the served UI bundle predates the camera switcher and appends "
            "'/<videoHash>' to videoURL; this mxcubeweb sends an empty hash, so "
            "the browser opens '.../argus/<name>/' and argussight rejects it",
            "pull mxcubeweb feature/argussight_sampleview (sends base + stream "
            "name) and restart MXCuBE; no UI rebuild needed",
        )
    else:
        report(
            hop,
            "WARN",
            "the served UI bundle predates the camera switcher: the OAV video "
            "works, but there is no camera selector",
            "for the selector: cd ui && pnpm install && pnpm build, then copy "
            f"ui/build/* into {bundle}",
        )
    return is_new, splits


# --- hops 1 and 2 ----------------------------------------------------------


def check_local_streams():
    for cam in ac.CAMERAS:
        name, port = cam["name"], cam["port"]
        direct = f"ws://127.0.0.1:{port}/ws/{name}"
        ok, detail = ac._probe_stream(direct, timeout=PROBE_TIMEOUT)
        if ok:
            report(f"1 streamer {name}", "PASS", f"{direct}: {detail}")
        else:
            report(
                f"1 streamer {name}",
                "FAIL",
                f"{direct}: {detail}",
                "restart argussight via mxgo.sh; if it persists run argus_cameras.py "
                "with ARGUS_STREAMER_DEBUG=1 to see ffmpeg's error",
            )
        proxied = f"ws://127.0.0.1:{ac.ARGUS_PROXY_PORT}/ws/{name}"
        ok, detail = ac._probe_stream(proxied, timeout=PROBE_TIMEOUT)
        if ok:
            report(f"2 proxy {name}", "PASS", f"{proxied}: {detail}")
        else:
            report(
                f"2 proxy {name}",
                "FAIL",
                f"{proxied}: {detail}",
                "argussight dropped or never registered the stream: look for "
                "'Removing stream' / 'Upstream worker' in argussight.log, then "
                "restart argussight via mxgo.sh",
            )


# --- hop 3 -----------------------------------------------------------------


def check_discovery(app, backend_splits):
    """Replay MXCuBE's discovery; return the OAV stream URL it finds."""
    hop = "3 discovery"
    host = app.get("ARGUSSIGHT_GRPC_HOST") or "localhost"
    port = app.get("ARGUSSIGHT_GRPC_PORT") or 50051
    try:
        import grpc

        import argussight.grpc.argus_service_pb2 as pb2
        import argussight.grpc.argus_service_pb2_grpc as pb2_grpc
    except Exception as exc:
        report(
            hop,
            "SKIP",
            f"gRPC stubs not importable here ({exc}); run in the argussight env",
        )
        return ""
    try:
        with grpc.insecure_channel(
            f"{host}:{port}", options=[("grpc.enable_http_proxy", 0)]
        ) as channel:
            resp = pb2_grpc.SpawnerServiceStub(channel).GetProcesses(
                pb2.GetProcessesRequest(), timeout=PROBE_TIMEOUT
            )
    except Exception as exc:
        report(
            hop,
            "FAIL",
            f"GetProcesses on {host}:{port} failed: {exc}",
            "argussight is not running or not on that port: restart via mxgo.sh",
        )
        return ""

    streams = list(resp.streams)
    if resp.status != "success" or not streams:
        report(
            hop,
            "FAIL",
            f"GetProcesses status={resp.status!r} streams={streams}",
            "no stream registered: see 'registered <name>' lines in argussight.log",
        )
        return ""

    configured = [c.get("name") for c in app.get("ARGUSSIGHT_CAMERAS") or []]
    names = [n for n in configured if n in streams] if configured else streams
    if not names:
        report(
            hop,
            "FAIL",
            f"argussight has {streams} but ARGUSSIGHT_CAMERAS only allows "
            f"{configured}: MXCuBE filters everything out and falls back to "
            "VIDEO_STREAM_URL",
            "make the ARGUSSIGHT_CAMERAS names match the stream names above",
        )
        return ""

    base = (app.get("ARGUSSIGHT_PROXY_URL") or "").rstrip("/")
    meta = {c.get("name"): c for c in app.get("ARGUSSIGHT_CAMERAS") or []}
    oav = next((n for n in names if meta.get(n, {}).get("oav")), names[0])
    url = f"{base}/{oav}" if base else ""
    report(hop, "PASS", f"streams {streams}; MXCuBE shows {names}; OAV = {oav}")
    if url:
        video_url, _, video_hash = url.rpartition("/")
        if backend_splits is False:  # mxcubeweb before the base + name split
            video_url, video_hash = url, ""
        print(f"       OAV stream URL: {url}")
        print(
            f"       sent as videoURL={video_url!r} videoHash={video_hash!r} "
            "(the page opens videoURL/videoHash)"
        )
    else:
        print("       videoURL: <none: ARGUSSIGHT_PROXY_URL empty>")
    return url


# --- hop 5 -----------------------------------------------------------------


def _probe_public(url, insecure):
    """Like ac._probe_stream, but TLS-aware and reporting the HTTP status."""
    from websockets.sync.client import connect

    kwargs = {"open_timeout": PROBE_TIMEOUT}
    if url.startswith("wss://"):
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ctx
    try:
        try:
            ws = connect(url, proxy=None, **kwargs)
        except TypeError:  # websockets < 15
            ws = connect(url, **kwargs)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        status = status or getattr(exc, "status_code", None)
        return False, status, exc
    deadline = time.monotonic() + PROBE_TIMEOUT
    try:
        with ws:
            while time.monotonic() < deadline:
                try:
                    msg = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                except TimeoutError:
                    break
                if isinstance(msg, bytes):
                    return True, 101, f"{len(msg)} bytes"
    except Exception as exc:
        return False, 101, exc
    return False, 101, f"connected but no data in {PROBE_TIMEOUT:.0f}s"


def browser_url(url, bundle_is_new, backend_splits):
    """The URL the page really opens for the OAV stream URL `url`."""
    if url and bundle_is_new is False and backend_splits is False:
        return url + "/"  # old bundle appends '/' + the empty hash
    return url


def check_public(url, insecure):
    hop = "5 nginx (browser URL)"
    if not url:
        report(hop, "SKIP", "no browser URL to test (fix the failures above first)")
        return
    if not url.startswith(("ws://", "wss://")):
        report(hop, "FAIL", f"{url} is not a websocket URL")
        return
    ok, status, detail = _probe_public(url, insecure)
    if ok:
        report(hop, "PASS", f"{url}: {detail} (the path the browser uses works)")
        return
    fix = ""
    if isinstance(detail, ssl.SSLError) or "CERTIFICATE" in str(detail).upper():
        fix = (
            "TLS certificate not trusted from this host; rerun with --insecure to "
            "test the rest of the path (browsers use their own CA store)"
        )
    elif status == 404:
        fix = (
            "nginx has no matching location: check 'location /argus/' and "
            "'proxy_pass http://127.0.0.1:7000/ws/;' (both trailing slashes)"
        )
    elif status in (502, 503, 504):
        fix = "nginx cannot reach :7000: is argussight running? (see hop 2)"
    elif url.endswith("/"):
        fix = (
            "the trailing slash: argussight serves /ws/<name>, not /ws/<name>/ "
            "(see the 'page' line above)"
        )
    elif status == 403:
        fix = (
            "argussight refused the stream (unknown or dropped): see 'Removing "
            "stream' in argussight.log"
        )
    elif status in (400, 426):
        fix = (
            "nginx is not passing the websocket upgrade: add proxy_http_version "
            "1.1 and the Upgrade/Connection headers to location /argus/"
        )
    elif status == 101:
        fix = "the handshake works but no video arrives: compare with hop 2"
    else:
        fix = (
            "cannot reach the public name from this host (DNS / firewall / "
            "routing); test the same URL from the operator PC's browser devtools"
        )
    report(hop, "FAIL", f"{url}: HTTP {status or '-'}: {detail}", fix)


# --- logs ------------------------------------------------------------------


def _last_run(path, marker):
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [_ANSI.sub("", line.rstrip("\n")) for line in f]
    starts = [i for i, line in enumerate(lines) if marker in line]
    return lines[starts[-1] :] if starts else lines


def summarize_mxcube_log(path):
    print(f"\n--- {path}: last discovery lines")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            hits = [
                line.rstrip()
                for line in f
                if "Argussight discovery" in line
                or "Argussight camera discovery disabled" in line
                or "Argussight GetProcesses" in line
            ]
    except OSError as exc:
        print(f"    cannot read: {exc}")
        return
    for line in hits[-3:] or [
        "    (none: MXCuBE never ran discovery -- page not loaded yet, "
        "or ARGUSSIGHT_ENABLED false)"
    ]:
        print(f"    {line}")


def summarize_argussight_log(path):
    print(f"\n--- {path}: latest run")
    try:
        lines = _last_run(path, "mxgo.sh starting argussight")
    except OSError as exc:
        print(f"    cannot read: {exc}")
        return
    finals = Counter()
    i = 0
    while i < len(lines):
        if "Exception in ASGI application" in lines[i]:
            final = "(no exception line found)"
            j = i + 1
            while j < len(lines) and not _TIMESTAMP.match(lines[j]):
                if _EXC_LINE.match(lines[j]):
                    final = lines[j].strip()
                j += 1
            finals[final] += 1
            i = j
        else:
            i += 1
    if not finals:
        print("    no uvicorn 'Exception in ASGI application' tracebacks")
    for final, count in finals.most_common():
        verdict = next((v for pat, v in KNOWN_TRACEBACKS if pat in final), None)
        print(f"    {count:4}x {final}")
        print(f"          {verdict or 'UNKNOWN: paste this traceback for analysis'}")
    dropped = [
        line
        for line in lines
        if "Removing stream" in line
        or "Upstream worker" in line
        or "SELF-TEST" in line
        or "exited with code" in line
    ]
    for line in dropped[-6:]:
        print(f"    {line}")


# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="server.yaml path")
    parser.add_argument(
        "--webroot",
        default=DEFAULT_WEBROOT,
        help="the mxcubeweb repo MXCuBE runs from (for the served UI bundle)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="do not verify the TLS certificate of the public URL",
    )
    parser.add_argument("--mxcube-log", default=os.path.join(LOG_DIR, "mxcube.log"))
    parser.add_argument(
        "--argussight-log", default=os.path.join(LOG_DIR, "argussight.log")
    )
    args = parser.parse_args()

    # Everything here is localhost or the site's own nginx: never the SOLEIL
    # proxy (websockets >= 15 and grpc would otherwise honour http(s)_proxy).
    ac._strip_proxy(os.environ)

    app = check_config(args.config)
    bundle_is_new, backend_splits = check_frontend(args.webroot)
    check_local_streams()
    url = check_discovery(app, backend_splits)
    check_public(browser_url(url, bundle_is_new, backend_splits), args.insecure)
    summarize_mxcube_log(args.mxcube_log)
    summarize_argussight_log(args.argussight_log)

    print()
    failed = [r for r in results if r[1] == "FAIL"]
    if failed:
        hop, _, message, fix = failed[0]
        print(f"FIRST BROKEN HOP: {hop}\n  {message}\n  -> {fix}")
        return 1
    print(
        "All server-side hops PASS. If the pane is still black, it is the browser: "
        "hard-reload (Ctrl+Shift+R), open devtools -> Network -> WS, and check the "
        "argus/<name> socket shows 101 and a growing number of frames; any error is "
        "in the Console tab."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
