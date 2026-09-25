#!/usr/bin/env python3
"""Find where the PX1 sample-view video chain breaks.

The OAV image reaches the browser through five hops, below mxcubeweb itself:

  0. mxcubeweb        :8081, behind nginx -- no app, no sample view
  1. video-streamer   ws://localhost:<port>/ws/<name>   (argus_cameras.py)
  2. argussight proxy ws://localhost:7000/ws/<name>     (argussight, uvicorn)
  3. discovery        gRPC GetProcesses :50051 -> the stream URL for the browser
  4. server.yaml      ARGUSSIGHT_* and VIDEO_FORMAT
  5. nginx            wss://<public name>/argus/<name> -> :7000

A sixth check carries no video: socket.io, the app's own websocket, on :8081
through the same nginx. It shares the upgrade path with hop 5, and the 400s it
returns name a configuration fault precisely, so it is checked here too.

argus_cameras.py's SELF-TEST covers hops 1-2 only, so "SELF-TEST OK" with a
black pane means 0, 3, 4 or 5. Run this on the MXCuBE host, in the argussight
env (it has grpc, websockets and yaml):

    python scripts/argussight/diagnose_video.py [--config .../server.yaml]

It prints one line per hop, names the first broken one and exits 1. The fixes
it suggests are explained in README.md.

Server-side hops use localhost on purpose: argussight always dials the
streamers at ws://localhost:<port>. Only ARGUSSIGHT_PROXY_URL, which the
browser opens, must be the public wss:// name.

The public origin is a question of deployment, not of the video chain, so pass
--public-origin whenever the page is not on :443 -- nginx in a container
usually publishes another port. Every URL the browser dials must carry that
port, which is why ARGUSSIGHT_PROXY_URL is best written root-relative
("/argus"): the page then resolves it against its own origin.
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
# The origin the BROWSER uses -- not necessarily :443. A containerized nginx
# usually publishes some other port, and then every URL the page opens must
# carry it. --public-origin overrides this.
PUBLIC_ORIGIN = "https://mxcubeweb-px1.synchrotron-soleil.fr"
# The recommended ARGUSSIGHT_PROXY_URL: root-relative, so the page resolves it
# against its own origin and its port can never drift from nginx's.
RELATIVE_PROXY_URL = "/argus"
# engineio's direct-websocket handshake, exactly as the socket.io client opens
# it. No `sid`: the client does not poll first (transports: ['websocket', ...]).
SOCKETIO_PATH = "/socket.io/?EIO=4&transport=websocket"
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


def ws_connect(connect, url, kwargs):
    """connect() across websockets versions, or raise the last TypeError.

    Two arguments moved: `proxy` exists only from 15 (we pass None, since the
    site proxy cannot reach the beamline), and the TLS context is `ssl` from 14
    but `ssl_context` before it. One `except TypeError` retry cannot tell the
    two apart -- on websockets 12 both spellings fail and the second TypeError
    escapes, which hop 5 then reports as if nginx had refused the connection.
    So drop or rename one argument at a time, newest spelling first.
    """
    attempts = [dict(kwargs, proxy=None), dict(kwargs)]
    if "ssl" in kwargs:  # websockets < 14 calls it ssl_context
        legacy = {k: v for k, v in kwargs.items() if k != "ssl"}
        legacy["ssl_context"] = kwargs["ssl"]
        attempts += [dict(legacy, proxy=None), legacy]
    last = None
    for attempt in attempts:
        try:
            return connect(url, **attempt)
        except TypeError as exc:
            last = exc
    raise last


def ws_probe(url, insecure=False):
    """Wait for one binary frame; return (ok, http_status, detail)."""
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    kwargs = {"open_timeout": TIMEOUT}
    if url.startswith("wss://"):
        kwargs["ssl"] = ssl_context(insecure)
    try:
        ws = ws_connect(connect, url, kwargs)
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


def ws_url(origin, path):
    """`path` on `origin`, as a websocket URL: https -> wss, http -> ws."""
    parts = urlparse(origin)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}{path}"


def resolve_public(url, origin):
    """The URL the browser really opens.

    A root-relative ARGUSSIGHT_PROXY_URL ("/argus") is resolved by the page
    against its own origin, so resolve it the same way here. An absolute
    ws(s):// URL is taken as it stands.
    """
    return ws_url(origin, url) if url.startswith("/") else url


def handshake(url, insecure, origin=""):
    """One websocket handshake by hand; return (status, reason, location, body).

    websockets' client throws the response body away, and for socket.io that
    body is the whole answer: engineio replies 400 with the reason inside it
    ("Origin not allowed", "WebSocket transport not available"). So write the
    request and read the reply, exactly like the curl in README.md. `status` is
    None when the connection itself failed, and `reason` then carries the error.
    """
    parts = urlparse(url)
    secure = parts.scheme in ("wss", "https")
    target = parts.path or "/"
    if parts.query:
        target += f"?{parts.query}"
    lines = [
        f"GET {target} HTTP/1.1",
        f"Host: {parts.netloc}",
        "Connection: Upgrade",
        "Upgrade: websocket",
        "Sec-WebSocket-Version: 13",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
    ]
    if origin:
        lines.append(f"Origin: {origin}")
    request = ("\r\n".join(lines) + "\r\n\r\n").encode()

    try:
        raw = socket.create_connection(
            (parts.hostname, parts.port or (443 if secure else 80)), timeout=TIMEOUT
        )
    except Exception as exc:
        return None, str(exc), "", ""
    try:
        sock = (
            ssl_context(insecure).wrap_socket(raw, server_hostname=parts.hostname)
            if secure
            else raw
        )
        sock.sendall(request)
        reply = b""
        while len(reply) < 8192:
            try:
                chunk = sock.recv(1024)
            except (TimeoutError, socket.timeout):
                break
            if not chunk:  # the server answered and hung up
                break
            reply += chunk
            head, sep, body = reply.partition(b"\r\n\r\n")
            # Stop at the headers on an upgrade (no body follows, and the
            # connection stays open), but wait for the body otherwise: that is
            # where engineio puts its reason.
            if sep and (b" 101 " in head.split(b"\r\n")[0] or body):
                break
    except Exception as exc:
        return None, str(exc), "", ""
    finally:
        raw.close()

    head, _, body = reply.partition(b"\r\n\r\n")
    head = head.decode("utf-8", "replace").splitlines()
    fields = (head[0] if head else "").split(None, 2)
    try:
        status = int(fields[1])
    except (IndexError, ValueError):
        return None, f"no HTTP status in {head[:1]}", "", ""
    location = next(
        (
            line.split(":", 1)[1].strip()
            for line in head[1:]
            if line.lower().startswith("location:")
        ),
        "",
    )
    reason = fields[2] if len(fields) > 2 else ""
    return status, reason, location, body.decode("utf-8", "replace")[:200]


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


def config_rules(app, origin):
    """(status, message, fix) for every setting that can blank the sample view."""
    url = app.get("ARGUSSIGHT_PROXY_URL") or ""
    parts = urlparse(url)
    # A root-relative value is the recommended form: the page resolves it, so
    # none of the scheme/host/port rules below can apply to it.
    relative = url.startswith("/")
    scheme = parts.scheme
    kind = host_kind(parts.hostname)
    fmt = str(app.get("VIDEO_FORMAT", "MPEG1")).upper()
    use_relative = (
        f"set ARGUSSIGHT_PROXY_URL: {RELATIVE_PROXY_URL} -- the page resolves it"
        " against its own origin, so its port cannot drift from nginx's"
    )
    page_port = urlparse(origin).port or 443
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
        (not url, "FAIL", "ARGUSSIGHT_PROXY_URL is empty", use_relative),
        (
            not relative and kind == "loopback",
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} is localhost, but the browser opens it",
            use_relative,
        ),
        (
            not relative and scheme == "ws",
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} is ws://: an https page blocks it",
            use_relative,
        ),
        (
            bool(url) and not relative and scheme not in ("ws", "wss"),
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL {url} is neither a websocket URL nor a"
            " root-relative path",
            use_relative,
        ),
        (
            not relative and kind == "ip",
            "WARN",
            f"ARGUSSIGHT_PROXY_URL {url} is a bare IP, not the certificate's name",
            use_relative,
        ),
        (
            # The fault that blanks the pane while every server-side hop passes:
            # the page is on one port and the video is dialled on another, so
            # nginx never even logs the request.
            not relative and bool(url) and (parts.port or 443) != page_port,
            "FAIL",
            f"ARGUSSIGHT_PROXY_URL is on port {parts.port or 443}, but the page"
            f" is served on {page_port}: the browser opens the video on the wrong"
            " port, where this nginx never sees it",
            use_relative,
        ),
    ]


def check_config(path, origin):
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
    # ALLOWED_CORS_ORIGINS lives in the `server:` section (config.py maps it to
    # cfg.flask), and server.py hands it straight to SocketIO.
    cors = (cfg.get("server") or {}).get("ALLOWED_CORS_ORIGINS") or []
    note(f"  server.ALLOWED_CORS_ORIGINS: {cors or '<unset: origins not checked>'}")

    broken = False
    for failed, status, message, fix in config_rules(app, origin):
        if failed:
            broken = broken or status == "FAIL"
            report(hop, status, message, fix)
    if cors and origin not in cors:
        # engineio skips the origin check only while the list is empty; with a
        # non-empty one, an unlisted origin gets 400 on every socket.io request.
        broken = True
        report(
            hop,
            "FAIL",
            f"ALLOWED_CORS_ORIGINS does not list {origin}, so socket.io is"
            " answered 400 'Origin not allowed'",
            f"add {origin} to server: ALLOWED_CORS_ORIGINS (or empty the list,"
            " which turns the check off), then restart MXCuBE",
        )
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


def check_mxcube_server(insecure, origin):
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

    # hop 6 needs it too, and probing :8081 twice for it would be wasteful.
    check_mxcube_server.scheme = scheme or "http"

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

    public = f"{origin}{HEALTH_PATH}"
    answer = http_get(public, insecure)
    status, message, fix = health_verdict("via nginx", public, *answer)
    if answer[0] is None and direct[0] == "PASS":
        # The name may simply not route from this host; the browser still may.
        status, fix = "WARN", "unreachable from this host; test it in the browser"
    report(hop, status, message, fix)


# --- the page ---------------------------------------------------------------


def check_page(webroot, origin, insecure):
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

    body = http_get(f"{origin}/", insecure)[3] if origin else ""
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


def check_public(url, insecure, origin):
    hop = "5 nginx"
    if not url:
        report(hop, "SKIP", "no browser URL to test; fix the failures above first")
        return
    url = resolve_public(url, origin)
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
            status,
            "unreachable: check DNS, the firewall, and the port nginx publishes"
            " (--public-origin)",
        )
        if fix == UPSTREAM_DOWN and proxy_hop_passed():
            fix = UPSTREAM_ELSEWHERE
    report(hop, "FAIL", f"{url}: HTTP {status or '-'}: {detail}", fix)


# --- hop 6 ------------------------------------------------------------------


# engineio answers 400 to several quite different faults, and only the body it
# returns tells them apart. Matched against that body, lowercased, in order.
SOCKETIO_BODY_FIXES = [
    (
        "not allowed",
        "engineio rejected the page's Origin: add it to ALLOWED_CORS_ORIGINS"
        " under server: in server.yaml (or empty the list to turn the check off)"
        " and restart MXCuBE. Also give nginx Host $http_host, not $host, which"
        " drops the port",
    ),
    (
        "transport",
        "this mxcubeweb env cannot serve websockets: install gevent-websocket"
        " (or simple-websocket) into it and restart. Until then socket.io falls"
        " back to polling and the console repeats the failure",
    ),
    (
        "version",
        "Engine.IO protocol mismatch: rebuild the UI (cd ui && pnpm build)"
        " against the installed Flask-SocketIO",
    ),
]

NGINX_NO_UPGRADE = (
    "nginx is not forwarding the upgrade: location /socket.io/ needs"
    " proxy_http_version 1.1, Upgrade $http_upgrade and Connection"
    " $connection_upgrade (see README.md), and Host $http_host to keep the port"
)


def check_socketio(origin, insecure):
    """The app's own websocket: no video, but its 400s are precise answers.

    A 400 here does not blank the canvas -- the camera list and the stream URL
    reach the page over REST, and the client falls back to polling -- but it
    costs every live update, and it is the one fault whose cause the server
    states out loud.
    """
    hop = "6 socket.io"
    public = ws_url(origin, SOCKETIO_PATH)
    status, reason, location, _ = handshake(public, insecure, origin=origin)
    if status == 101:
        report(hop, "PASS", f"{public}: 101 Switching Protocols")
        return
    if status is None:
        report(
            hop,
            "FAIL",
            f"{public}: unreachable: {reason}",
            "check --public-origin, DNS, and the port nginx publishes",
        )
        return
    if status in REDIRECTS:
        report(
            hop,
            "FAIL",
            f"{public}: HTTP {status} to {location[:50]}",
            "an SSO layer guards /socket.io/: a handshake cannot log in",
        )
        return

    # The same handshake straight at :8081 decides who answered the 400.
    scheme = getattr(check_mxcube_server, "scheme", "http")
    direct = ws_url(f"{scheme}://127.0.0.1:{MXCUBE_PORT}", SOCKETIO_PATH)
    d_status, d_reason, _, d_body = handshake(direct, insecure=True, origin=origin)
    if d_status == 101:
        report(
            hop,
            "FAIL",
            f"{public}: HTTP {status}, but {direct}: 101",
            NGINX_NO_UPGRADE,
        )
        return
    if d_status is None:
        report(
            hop,
            "FAIL",
            f"{direct}: unreachable: {d_reason}",
            f"MXCuBE is not answering on :{MXCUBE_PORT} (see hop 0)",
        )
        return

    body = (d_body or "").strip()
    fix = next((f for pat, f in SOCKETIO_BODY_FIXES if pat in body.lower()), "")
    if not fix and d_status == 400:
        # engineio did not say why. Retrying without the Origin header does: if
        # that one is accepted, the origin was the whole problem.
        fix = (
            SOCKETIO_BODY_FIXES[0][1]
            if handshake(direct, insecure=True)[0] == 101
            else SOCKETIO_BODY_FIXES[1][1]
        )
    report(
        hop,
        "FAIL",
        f"{direct}: HTTP {d_status} {body[:80]!r} (nginx passed it on as {status})",
        fix or "mxcubeweb refused the handshake: see the body above",
    )


# --- logs -------------------------------------------------------------------


def summarize_mxcube_log(path):
    print(f"\n--- {path}: last discovery and socket.io lines")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            hits = [
                line.rstrip()
                for line in f
                if "Argussight discovery" in line
                or "Argussight camera discovery disabled" in line
                or "Argussight GetProcesses" in line
                # engineio says both of these out loud, once per refusal.
                or "is not allowed" in line
                or "WebSocket transport not available" in line
            ]
    except OSError as exc:
        print(f"    cannot read: {exc}")
        return
    for line in hits[-5:] or ["    (none: discovery never ran)"]:
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
    parser.add_argument(
        "--public-origin",
        default=PUBLIC_ORIGIN,
        help="the origin the browser uses, WITH its port (default: %(default)s)",
    )
    parser.add_argument("--mxcube-log", default=MXCUBE_LOG)
    parser.add_argument(
        "--argussight-log", default=os.path.join(LOG_DIR, "argussight.log")
    )
    args = parser.parse_args()

    ac._strip_proxy(os.environ)  # localhost and our own nginx only

    origin = args.public_origin.rstrip("/")
    app = check_config(resolve_config(args.config), origin)
    check_mxcube_server(args.insecure, origin)
    check_page(args.webroot, origin, args.insecure)
    check_local_streams()
    check_public(check_discovery(app), args.insecure, origin)
    check_socketio(origin, args.insecure)
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
