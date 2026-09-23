#!/usr/bin/env python3
"""Find where the PX1 sample-view video chain breaks.

The OAV image reaches the browser through five hops, below mxcubeweb itself:

  0. mxcubeweb        :8081, behind nginx -- no app, no sample view
  1. video-streamer   ws://localhost:<port>/ws/<name>   (argus_cameras.py)
  2. argussight proxy ws://localhost:7000/ws/<name>     (argussight, uvicorn)
  3. discovery        gRPC GetProcesses :50051 -> the stream URL for the browser
  4. server.yaml      ARGUSSIGHT_* and VIDEO_FORMAT
  5. nginx            wss://<public name>/argus/<name> -> :7000

argus_cameras.py's SELF-TEST covers hops 1-2 only, so "SELF-TEST OK" with a
black pane means 0, 3, 4 or 5. Run this on the MXCuBE host, in the argussight
env (it has grpc, websockets and yaml):

    python scripts/argussight/diagnose_video.py [--config .../server.yaml]

It prints one line per hop, names the first broken one and exits 1. The fixes
it suggests are explained in README.md.

Server-side hops use localhost on purpose: argussight always dials the
streamers at ws://localhost:<port>. Only ARGUSSIGHT_PROXY_URL, which the
browser opens, must be the public wss:// name.
"""

import argparse
import ipaddress
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argus_cameras as ac  # noqa: E402  (camera list, proxy port, proxy env)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "..", "config"))
# mxcubeweb reads <hwr dir>/mxcube-web/server.yaml, so look there first.
CONFIG_CANDIDATES = (
    os.path.join(CONFIG_DIR, "mxcube-web", "server.yaml"),
    os.path.join(CONFIG_DIR, "server.yaml"),
)
DEFAULT_WEBROOT = os.path.normpath(os.path.join(HERE, "..", "..", "..", "mxcubeweb"))
LOG_DIR = os.path.join(os.path.expanduser("~"), "MXCuBElogs")
MXCUBE_LOG = os.path.join(LOG_DIR, "mxcube.log")
TIMEOUT = 5.0

MXCUBE_PORT = 8081  # hardcoded in mxcubeweb/server.py's run()
HEALTH_PATH = "/mxcube/api/v0.1/login/login_info"
VITE_PORT = 5173
PUBLIC_URL = "wss://mxcubeweb-px1.synchrotron-soleil.fr/argus"
# A browser cannot run raw .jsx, so these mean a dev server serves ui/src.
SOURCE_MARKERS = ("/@vite/client", 'src="/src/', "/node_modules/.vite/")
BUILT_MARKERS = ('src="/assets/', "/static/js/", "assets/index-")
SSO_HINTS = ("openid-connect", "iam.", "/auth/realms", "saml")
TLS_CERT_MODES = ("SIGNED", "ADHOC")
REDIRECTS = (301, 302, 303, 307, 308)

KNOWN_TRACEBACKS = [
    ("WebsocketState", "harmless: the typo in streamsproxy.py's double-close guard"),
    ('Cannot call "send"', "harmless: argussight 0.3.2 double-close on disconnect"),
    ("Unexpected ASGI message", "harmless: the same double-close, other wording"),
]

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d")
_EXC_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Closed\w*)\b")

results = []


def report(hop, status, message, fix=""):
    results.append((hop, status, message, fix))
    print(f"[{status:4}] {hop}: {message}")
    if fix and status in ("FAIL", "WARN"):
        print(f"       -> {fix}")


def note(text):
    print(f"       {text}")


# --- probes ----------------------------------------------------------------


def ssl_context(insecure):
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def ws_probe(url, insecure=False):
    """Wait for one binary frame; return (ok, http_status, detail)."""
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    kwargs = {"open_timeout": TIMEOUT}
    if url.startswith("wss://"):
        kwargs["ssl"] = ssl_context(insecure)
    try:
        try:
            ws = connect(url, proxy=None, **kwargs)  # never the site proxy
        except TypeError:  # websockets < 15 has no proxy argument
            ws = connect(url, **kwargs)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return False, status or getattr(exc, "status_code", None), exc

    deadline = time.monotonic() + TIMEOUT
    try:
        with ws:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False, 101, f"connected but no data in {TIMEOUT:.0f}s"
                try:
                    msg = ws.recv(timeout=left)
                except TimeoutError:
                    continue
                if isinstance(msg, bytes):
                    return True, 101, f"{len(msg)} bytes"
    except ConnectionClosed as exc:
        code = exc.rcvd.code if exc.rcvd else "no close frame"
        return False, 101, f"closed ({code})"
    except Exception as exc:
        return False, 101, exc


def http_get(url, insecure):
    """Return (status, content_type, location, body_head); status None on error."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    handler = (
        urllib.request.HTTPSHandler(context=ssl_context(insecure))
        if url.startswith("https://")
        else urllib.request.HTTPHandler()
    )
    opener = urllib.request.build_opener(
        NoRedirect, handler, urllib.request.ProxyHandler({})
    )
    try:
        with opener.open(url, timeout=TIMEOUT) as resp:
            head = resp.read(400).decode("utf-8", "replace")
            return resp.status, resp.headers.get("Content-Type", ""), "", head
    except urllib.error.HTTPError as exc:
        headers = exc.headers or {}
        head = exc.read(400).decode("utf-8", "replace")
        return (
            exc.code,
            headers.get("Content-Type", ""),
            headers.get("Location", ""),
            head,
        )
    except Exception as exc:
        return None, str(exc), "", ""


# --- hop 4: server.yaml -----------------------------------------------------


def load_yaml(path):
    try:
        import yaml

        loader = yaml.safe_load
    except ImportError:
        import ruamel.yaml

        loader = ruamel.yaml.YAML(typ="safe").load
    with open(path, encoding="utf-8") as f:
        return loader(f)


def resolve_config(path):
    if path:
        return path
    return next(
        (p for p in CONFIG_CANDIDATES if os.path.isfile(p)), CONFIG_CANDIDATES[0]
    )


def host_kind(host):
    if not host:
        return "missing"
    if host == "localhost" or host.endswith(".localhost"):
        return "loopback"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "name"
    return "loopback" if ip.is_loopback or ip.is_unspecified else "ip"


def config_rules(app):
    """(status, message, fix) for every setting that can blank the sample view."""
    url = app.get("ARGUSSIGHT_PROXY_URL") or ""
    scheme = urlparse(url).scheme
    kind = host_kind(urlparse(url).hostname)
    fmt = str(app.get("VIDEO_FORMAT", "MPEG1")).upper()
    use_public = f"set ARGUSSIGHT_PROXY_URL: {PUBLIC_URL}"
    return [
        (
            app.get("ARGUSSIGHT_ENABLED") is not True,
            "FAIL",
            "ARGUSSIGHT_ENABLED is not true: MXCuBE never asks argussight",
            "set ARGUSSIGHT_ENABLED: true, restart MXCuBE",
        ),
        (
            fmt != "MPEG1",
            "FAIL",
            f"VIDEO_FORMAT is {fmt}: the page draws an <img>, not the MPEG1 canvas",
            "set VIDEO_FORMAT: MPEG1, restart MXCuBE",
        ),
        (
            app.get("USE_EXTERNAL_STREAMER") is True,
            "WARN",
            "USE_EXTERNAL_STREAMER is true: MXCuBE starts a streamer of its own",
            "set USE_EXTERNAL_STREAMER: false",
        ),
        (not url, "FAIL", "ARGUSSIGHT_PROXY_URL is empty", use_public),
        (
            bool(url) and kind == "loopback",
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} is localhost, but the browser opens it",
            use_public,
        ),
        (
            bool(url) and scheme == "ws",
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} is ws://: an https page blocks it",
            use_public,
        ),
        (
            bool(url) and scheme not in ("ws", "wss"),
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} is not a websocket URL",
            use_public,
        ),
        (
            kind == "ip",
            "WARN",
            f"ARGUSSIGHT_PROXY_URL {url} is a bare IP, not the certificate's name",
            use_public,
        ),
    ]


def check_config(path):
    """Return the `mxcube:` section; the whole file stays on check_config.raw."""
    hop = "4 config"
    try:
        cfg = load_yaml(path) or {}
    except FileNotFoundError:
        report(
            hop,
            "FAIL",
            f"{path} not found",
            "mxcubeweb reads <hwr dir>/mxcube-web/server.yaml; pass --config",
        )
        return {}
    except ImportError:
        report(hop, "SKIP", "no yaml module here; run in the argussight env")
        return {}
    except Exception as exc:
        report(hop, "FAIL", f"cannot parse {path}: {exc}")
        return {}

    check_config.raw = cfg
    app = cfg.get("mxcube") or {}
    note(f"{path} (mxcube: section)")
    for key in (
        "ARGUSSIGHT_ENABLED",
        "VIDEO_FORMAT",
        "USE_EXTERNAL_STREAMER",
        "ARGUSSIGHT_PROXY_URL",
        "VIDEO_STREAM_URL",
        "ARGUSSIGHT_GRPC_HOST",
        "ARGUSSIGHT_GRPC_PORT",
    ):
        note(f"  {key}: {app.get(key, '<unset>')!r}")
    names = [c.get("name") for c in app.get("ARGUSSIGHT_CAMERAS") or []]
    note(f"  ARGUSSIGHT_CAMERAS names: {names or '<unset: all streams>'}")

    broken = False
    for failed, status, message, fix in config_rules(app):
        if failed:
            broken = broken or status == "FAIL"
            report(hop, status, message, fix)
    if not broken:
        report(hop, "PASS", "the video settings in server.yaml look right")
    return app


# --- hop 0: mxcubeweb -------------------------------------------------------


def health_verdict(label, url, status, ctype, location, body):
    if status is None:
        return "FAIL", f"{label} {url}: {ctype}", "start MXCuBE with mxgo.sh"
    if status in (502, 503, 504):
        return (
            "FAIL",
            f"{label} {url}: HTTP {status}, so the proxy cannot reach mxcubeweb",
            f"start MXCuBE with mxgo.sh, or see {MXCUBE_LOG}; if it is running, "
            "whatever proxies to it is using the wrong scheme or port",
        )
    if status in REDIRECTS and any(h in location for h in SSO_HINTS):
        return (
            "FAIL",
            f"{label} {url}: HTTP {status} to {location[:50]}...",
            "an SSO layer answers instead of MXCuBE: log in, or exclude /mxcube/api",
        )
    if "json" in ctype.lower():
        return "PASS", f"{label} {url}: HTTP {status}, JSON", ""
    return (
        "FAIL",
        f"{label} {url}: HTTP {status}, {ctype or 'no content-type'}: {body[:60]!r}",
        "the UI needs JSON here; HTML means nginx or a login page answered",
    )


def spoken_scheme(insecure):
    """The scheme :8081 answers on, with that answer."""
    for scheme in ("http", "https"):
        answer = http_get(f"{scheme}://127.0.0.1:{MXCUBE_PORT}{HEALTH_PATH}", insecure)
        if answer[0] is not None:
            return scheme, answer
    return None, None


def check_mxcube_server(app, insecure):
    hop = "0 mxcubeweb"
    if not port_open(MXCUBE_PORT):
        report(
            hop,
            "FAIL",
            f"nothing listens on 127.0.0.1:{MXCUBE_PORT}",
            f"MXCuBE is not running: start it with mxgo.sh, then see {MXCUBE_LOG}",
        )

    scheme, answer = spoken_scheme(insecure=True)
    local = f"{scheme or 'http'}://127.0.0.1:{MXCUBE_PORT}{HEALTH_PATH}"
    direct = health_verdict("direct", local, *(answer or http_get(local, insecure)))
    report(hop, *direct)

    cert = str(
        (getattr(check_config, "raw", {}).get("server") or {}).get("CERT", "NONE")
    )
    if scheme:
        note(f":{MXCUBE_PORT} speaks {scheme}://, so every proxy to it must too")
        if (cert.upper() in TLS_CERT_MODES) != (scheme == "https"):
            report(
                hop,
                "WARN",
                f"server.CERT is {cert} but :{MXCUBE_PORT} answers {scheme}://",
            )

    host = urlparse(app.get("ARGUSSIGHT_PROXY_URL") or "").netloc
    if not host:
        return
    public = f"https://{host}{HEALTH_PATH}"
    answer = http_get(public, insecure)
    status, message, fix = health_verdict("via nginx", public, *answer)
    if answer[0] is None and direct[0] == "PASS":
        # The name may simply not route from this host; the browser still may.
        status, fix = "WARN", "unreachable from this host; test it in the browser"
    report(hop, status, message, fix)


# --- the page ---------------------------------------------------------------


def check_page(webroot, host, insecure):
    """Which frontend answers: mxcubeweb's built UI, or a dev server."""
    hop = "page"
    builds = [
        os.path.join(webroot, "ui", "build"),  # what mxgo.sh passes
        os.path.join(webroot, "mxcubeweb", "ui"),  # an installed mxcubeweb
    ]
    build = next(
        (b for b in builds if os.path.isfile(os.path.join(b, "index.html"))), ""
    )
    if build:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(build)))
        note(f"built UI in {build}, from {when}")

    body = http_get(f"https://{host}/", insecure)[3] if host else ""
    if any(marker in body for marker in SOURCE_MARKERS):
        report(
            hop,
            "WARN",
            f"a dev server answers, not mxcubeweb's built UI (:{VITE_PORT} is "
            + ("up here)" if port_open(VITE_PORT) else "elsewhere)"),
            "for production: pnpm build, stop the dev server, restart mxgo.sh",
        )
    elif any(marker in body for marker in BUILT_MARKERS):
        report(hop, "PASS", "mxcubeweb serves the built UI")
    elif not build:
        report(
            hop,
            "FAIL",
            f"no built UI in {' or '.join(builds)}",
            "cd ui && pnpm install && pnpm build, then restart mxgo.sh",
        )
    else:
        report(hop, "SKIP", "cannot tell what serves the page from here")


# --- hops 1 and 2 -----------------------------------------------------------


def check_local_streams():
    for cam in ac.CAMERAS:
        name = cam["name"]
        url = f"ws://127.0.0.1:{cam['port']}/ws/{name}"
        ok, _, detail = ws_probe(url)
        report(
            f"1 streamer {name}",
            "PASS" if ok else "FAIL",
            f"{url}: {detail}",
            "restart via mxgo.sh; ARGUS_STREAMER_DEBUG=1 shows ffmpeg's error",
        )
        url = f"ws://127.0.0.1:{ac.ARGUS_PROXY_PORT}/ws/{name}"
        ok, _, detail = ws_probe(url)
        report(
            f"2 proxy {name}",
            "PASS" if ok else "FAIL",
            f"{url}: {detail}",
            "argussight dropped the stream: grep 'Removing stream' in argussight.log",
        )


# --- hop 3 ------------------------------------------------------------------


def check_discovery(app):
    """Replay MXCuBE's discovery; return the OAV stream URL it would hand over."""
    hop = "3 discovery"
    host = app.get("ARGUSSIGHT_GRPC_HOST") or "localhost"
    port = app.get("ARGUSSIGHT_GRPC_PORT") or 50051
    try:
        import grpc

        import argussight.grpc.argus_service_pb2 as pb2
        import argussight.grpc.argus_service_pb2_grpc as pb2_grpc
    except Exception as exc:
        report(hop, "SKIP", f"no gRPC stubs here ({exc}); run in the argussight env")
        return ""

    try:
        # enable_http_proxy 0: grpc would otherwise honour http(s)_proxy.
        with grpc.insecure_channel(
            f"{host}:{port}", options=[("grpc.enable_http_proxy", 0)]
        ) as channel:
            resp = pb2_grpc.SpawnerServiceStub(channel).GetProcesses(
                pb2.GetProcessesRequest(), timeout=TIMEOUT
            )
    except Exception as exc:
        report(
            hop,
            "FAIL",
            f"GetProcesses on {host}:{port} failed: {exc}",
            "argussight is not running there: restart it via mxgo.sh",
        )
        return ""

    streams = list(resp.streams)
    if resp.status != "success" or not streams:
        report(
            hop,
            "FAIL",
            f"GetProcesses status={resp.status!r} streams={streams}",
            "no stream registered: grep 'registered' in argussight.log",
        )
        return ""

    cameras = app.get("ARGUSSIGHT_CAMERAS") or []
    configured = [c.get("name") for c in cameras]
    names = [n for n in configured if n in streams] if configured else streams
    if not names:
        report(
            hop,
            "FAIL",
            f"argussight has {streams}, ARGUSSIGHT_CAMERAS allows {configured}",
            "make the ARGUSSIGHT_CAMERAS names match the stream names",
        )
        return ""

    meta = {c.get("name"): c for c in cameras}
    oav = next((n for n in names if meta.get(n, {}).get("oav")), names[0])
    base = (app.get("ARGUSSIGHT_PROXY_URL") or "").rstrip("/")
    report(hop, "PASS", f"streams {streams}; MXCuBE shows {names}; OAV = {oav}")
    if not base:
        return ""
    url = f"{base}/{oav}"
    note(f"OAV stream URL for the browser: {url}")
    return url


# --- hop 5 ------------------------------------------------------------------


# What a 5xx means depends on hop 2. If the proxy answered there, :7000 is up
# on this host and nginx is connecting to some other :7000 -- it runs on
# another host, or in a container, where 127.0.0.1 is the container itself.
UPSTREAM_DOWN = "nginx cannot reach :7000: is argussight running? (hop 2)"
UPSTREAM_ELSEWHERE = (
    "hop 2 proved :7000 answers here, so nginx is reaching a different one: it "
    "runs on another host or in a container (127.0.0.1 is then the container "
    "itself). Give proxy_pass argussight's LAN address instead of 127.0.0.1"
)

PUBLIC_FIXES = [
    (404, "nginx has no such location: check 'location /argus/' and its proxy_pass"),
    (502, UPSTREAM_DOWN),
    (503, UPSTREAM_DOWN),
    (504, UPSTREAM_DOWN),
    (403, "argussight refused the stream: grep 'Removing stream' in argussight.log"),
    (400, "nginx is not forwarding the websocket upgrade headers"),
    (426, "nginx is not forwarding the websocket upgrade headers"),
    (101, "the handshake works but no video arrives: compare with hop 2"),
]


def proxy_hop_passed():
    """True when hop 2 reached argussight's stream proxy on this host."""
    return any(
        hop.startswith("2 proxy") and status == "PASS" for hop, status, _, _ in results
    )


def check_public(url, insecure):
    hop = "5 nginx"
    if not url:
        report(hop, "SKIP", "no browser URL to test; fix the failures above first")
        return
    ok, status, detail = ws_probe(url, insecure)
    if ok:
        report(hop, "PASS", f"{url}: {detail}")
        return
    if isinstance(detail, ssl.SSLError) or "CERTIFICATE" in str(detail).upper():
        fix = "certificate not trusted here; rerun with --insecure to test the rest"
    elif status in REDIRECTS:
        fix = "an SSO layer guards /argus/: a websocket handshake cannot log in"
    else:
        fix = dict(PUBLIC_FIXES).get(
            status, "unreachable: check DNS, firewall and nginx"
        )
        if fix == UPSTREAM_DOWN and proxy_hop_passed():
            fix = UPSTREAM_ELSEWHERE
    report(hop, "FAIL", f"{url}: HTTP {status or '-'}: {detail}", fix)


# --- logs -------------------------------------------------------------------


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
    for line in hits[-3:] or ["    (none: discovery never ran)"]:
        print(f"    {line}")


def summarize_argussight_log(path):
    print(f"\n--- {path}: latest run")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [_ANSI.sub("", line.rstrip("\n")) for line in f]
    except OSError as exc:
        print(f"    cannot read: {exc}")
        return
    starts = [
        i for i, line in enumerate(lines) if "mxgo.sh starting argussight" in line
    ]
    lines = lines[starts[-1] :] if starts else lines

    finals = Counter()
    i = 0
    while i < len(lines):
        if "Exception in ASGI application" not in lines[i]:
            i += 1
            continue
        final = "(no exception line)"
        i += 1
        while i < len(lines) and not _TIMESTAMP.match(lines[i]):
            if _EXC_LINE.match(lines[i]):
                final = lines[i].strip()
            i += 1
        finals[final] += 1

    if not finals:
        print("    no uvicorn tracebacks")
    for final, count in finals.most_common():
        verdict = next((v for pat, v in KNOWN_TRACEBACKS if pat in final), None)
        print(f"    {count:4}x {final}")
        print(f"          {verdict or 'UNKNOWN: paste this traceback'}")
    for line in [
        line
        for line in lines
        if "Removing stream" in line
        or "Upstream worker" in line
        or "SELF-TEST" in line
        or "exited with code" in line
    ][-6:]:
        print(f"    {line}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default="", help="server.yaml path")
    parser.add_argument("--webroot", default=DEFAULT_WEBROOT, help="the mxcubeweb repo")
    parser.add_argument(
        "--insecure", action="store_true", help="do not verify TLS certificates"
    )
    parser.add_argument("--mxcube-log", default=MXCUBE_LOG)
    parser.add_argument(
        "--argussight-log", default=os.path.join(LOG_DIR, "argussight.log")
    )
    args = parser.parse_args()

    ac._strip_proxy(os.environ)  # localhost and our own nginx only

    app = check_config(resolve_config(args.config))
    check_mxcube_server(app, args.insecure)
    check_page(
        args.webroot,
        urlparse(app.get("ARGUSSIGHT_PROXY_URL") or "").netloc,
        args.insecure,
    )
    check_local_streams()
    check_public(check_discovery(app), args.insecure)
    summarize_mxcube_log(args.mxcube_log)
    summarize_argussight_log(args.argussight_log)

    print()
    failed = [r for r in results if r[1] == "FAIL"]
    if failed:
        hop, _, message, fix = failed[0]
        print(f"FIRST BROKEN HOP: {hop}\n  {message}\n  -> {fix}")
        return 1
    print(
        "All server-side hops PASS. If the pane is still black it is the browser:"
        " hard-reload, then devtools -> Network -> WS argus/<name> should show 101"
        " and a growing frame count."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
