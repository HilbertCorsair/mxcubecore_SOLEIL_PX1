# Argussight camera stack — PROXIMA-1

Brings PX2's multi-camera video to PX1. Argussight fronts one `video-streamer`
per camera behind a single WebSocket proxy and answers a gRPC discovery call;
mxcubeweb turns that into the sample-view camera switcher.

Full architecture: `PX2_mxcubecore/doc/argussight_integration.md`.

## What runs where

| Piece | Host | Started by |
|---|---|---|
| `redis_camera2.py` (frame publisher) | **camera server** (`195.221.8.84`) | **manually — not from here** |
| `argussight` (gRPC :50051, proxy :7000) | MXCuBE host | `start_argus_px1.sh` |
| `video-streamer` (one per camera) | MXCuBE host | `argus_cameras.py` |

The publisher living on another host is the reason for `check_frames.py`: we
cannot start the camera, so we check for frames and prompt the operator.

## Usage

```sh
./start_argus_px1.sh
```

Stop with Ctrl-C, or `kill $(cat /tmp/argus.pids)`.

In order, the script:

1. stops a stack left over from a previous run and checks that :50051, :7000
   and :9000 are free;
2. runs the camera gate (`check_frames.py`, below);
3. starts argussight with `-c config/` from `ARGUS_WORKDIR`, then waits for
   **both** its ports. If argussight dies first, the script exits 1 with its
   error within seconds, rather than leaving `mxgo.sh` to wait out its timeout;
4. starts `argus_cameras.py`, which runs the streamers, registers them and
   logs a `SELF-TEST` verdict per camera;
5. supervises: if argussight or `argus_cameras.py` exits, it stops the rest
   and exits, so no half-up stack blocks the next launch.

Useful overrides (all environment variables):
`PX1_REDIS_URI`, `PX1_REDIS_CHANNEL`, `CONDA_ACTIVATE`, `CONDA_ENV`,
`HELPER_PY`, `MXCUBE_ENV`, `MXCUBE_PY`, `ARGUSSIGHT_BIN`, `PIDFILE`,
`ARGUS_CONFIG_DIR` (default `config/` next to the script),
`ARGUS_WORKDIR` (default `~/MXCuBElogs/argussight`; argussight's logs go to its
`logs/`), `ARGUS_BIND_TIMEOUT` (default 30 s), `ARGUS_STREAMER_DEBUG=1`.

## How the pieces fit together

```
camera server                         MXCuBE host                        operator PC
┌──────────────┐  frames   ┌─────────────────────────────────┐   TLS   ┌──────────┐
│ redis_camera2│──────────►│ video-streamer :9000  (per cam) │         │          │
│  + Redis:6379│           │        │ MPEG1 (localhost only)  │         │ browser  │
└──────────────┘           │        ▼                        │         │ (JSMpeg) │
                           │ argussight proxy :7000 ─────────┼────────►│  canvas  │
                           │ argussight gRPC  :50051         │  nginx  │          │
                           │        ▲ GetProcesses           │  :443   │          │
                           │ mxcubeweb :8081 ────────────────┼────────►│  the app │
                           │ vite :5173 (the page) ──────────┼────────►│          │
                           └─────────────────────────────────┘         └──────────┘
```

**No video ever passes through mxcubeweb.** It makes one gRPC call to list the
streams and hands the browser a `wss://…/argus/<name>` address; the frames go
browser ⇄ nginx ⇄ argussight only.

| Port | Who listens | Who connects | For |
|---|---|---|---|
| 6379 | Redis, on the **camera server** (`195.221.8.84`) | the streamers, `check_frames.py` | the raw frames |
| 9000, 9001, … | one `video-streamer` per camera | argussight only, over `localhost` | MPEG1 |
| 7000 | argussight's stream proxy | the **browser**, through nginx `/argus/` | MPEG1, one path per camera |
| 50051 | argussight's gRPC server | `argus_cameras.py` (`AddStream`), mxcubeweb (`GetProcesses`) | control and discovery, never video |
| 8000 | MXCuBE's own streamer, only with `USE_EXTERNAL_STREAMER: true` | nginx `/video` | the pre-argussight fallback; **nothing listens here in this setup** |
| 8081 | mxcubeweb (hardcoded in `mxcubeweb/server.py`'s `run()`) | nginx `/mxcube/api`, `/socket.io` | the application |
| 5173 | the Vite dev server (`ui/vite.config.js`) | nginx `/` | the page, served from `ui/src` |
| 443 | nginx | the browser | all of the above, over TLS |

The proxy only serves streams registered through :50051, so :7000 alone is an
empty proxy. Both come from the same startup (`Spawner.__init__` starts the
proxy, then `serve()` binds :50051), so if that startup fails, neither opens.

### Where the page comes from

Two deployments exist, and they differ in what a `git pull` changes:

- **Production: the built UI, served by mxcubeweb itself on :8081.**
  `mxcubeweb-server --static-folder <repo>/ui/build` (what mxgo.sh passes) serves
  `ui/build`, and the backend answers the page, the API and socket.io on one
  port. A `git pull` reaches the browser only after `pnpm build`.
- **A dev server** (`pnpm start`, vite on `:5173` per `ui/vite.config.js`), which
  serves `ui/src` and transforms it on the fly, and **proxies `/mxcube/api` and
  `/socket.io` to mxcubeweb**. Convenient while editing the frontend, but its
  proxy target must name the scheme :8081 really speaks (see the 502 row in
  "The app itself does not load"), and it is easy to forget: on proxima1 one ran
  unnoticed from 2026-09-09, because mxgo.sh knows nothing about it.

**Which one the browser gets is a question of fact, not of intent** — check it:

```sh
ps -ef | grep -E 'vite|pnpm'   # a dev server, started by anyone, in any shell
ss -ltnp 'sport = :5173'       # ... and whether it is still listening
ls -l <repo>/ui/build/index.html    # the built page mxcubeweb serves, and its date
curl -sk https://mxcubeweb-px1.synchrotron-soleil.fr/ | grep -E 'src="/(src|assets)/|@vite'
#  src="/src/index.jsx" or /@vite/client  -> served from source by a dev server
#  src="/assets/index-<hash>.js"          -> the built UI
```

`diagnose_video.py` reports which of the two answers, and warns when it is the
dev server.

### Switching to the production build

```sh
cd <repo>/ui && pnpm install && pnpm build     # writes ui/build (vite outDir)
kill <pid of "pnpm start" and of node .../vite.js>   # from the ps above
# restart MXCuBE so it picks the build up
./mxgo.sh
curl -sk https://mxcubeweb-px1.synchrotron-soleil.fr/ | grep -o 'src="/assets/[^"]*"'
```

nginx must then send everything to mxcubeweb instead of :5173 — the page, the
API and socket.io — using the scheme :8081 speaks (`http` unless
`server: CERT:` is `SIGNED`/`ADHOC`):

```nginx
location / {
    proxy_pass http://127.0.0.1:8081;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# socket.io is a websocket: it needs the upgrade headers, like /argus/ below.
location /socket.io/ {
    proxy_pass http://127.0.0.1:8081;
    proxy_http_version 1.1;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host       $host;
    proxy_read_timeout 3600s;
}
```

`/mxcube/api` needs no block of its own once `location /` points at :8081.
Keep `location /argus/` as it is.

## argussight version and `config/config.yaml`

proxima1 runs argussight **0.3.2** (upstream master). Since 0.3.2:

- `argussight -c <dir>` names a **directory** holding `config.yaml`. Without
  `-c` it reads `./config.yaml` from the current directory and dies with
  `FileNotFoundError: 'config.yaml'` before binding either port.
- The root config must define `log_dir`. **The `config.yaml` shipped inside the
  package does not**, so pointing `-c` at it fails with `KeyError: 'log_dir'`.

`config/config.yaml` in this directory is PX1's own copy of the upstream file,
with `log_dir: logs` and `processes: []` (no Saver/Recorder: PX1's cameras are
external streamers registered via `AddStream`). It replaces the old
uncommitted edit to argussight's own config. Older argussight (0.3.0) accepts
`-c` and ignores it.

Known upstream bug, still in 0.3.2: `streamsproxy.py` closes a viewer's
websocket twice, logging `Exception in ASGI application` / "Cannot call send
once a close message has been sent" whenever a viewer (or the self-test)
disconnects. It is noise; the stream keeps working.

## The startup gate

`check_frames.py` waits up to 20 s for a frame on the Redis channel.

| Outcome | Exit | Effect |
|---|---|---|
| frames seen | 0 | stack starts |
| operator presses Cancel | 2 | MXCuBE does not start |
| OK, frames now arriving | 0 | stack starts |
| OK, still no frames | 3 | `RuntimeError`, stack does not start |

The prompt is a zenity dialog, falling back to tkinter, then to a stdin prompt.
With neither a tty nor a `DISPLAY` it treats the situation as Cancel rather than
hanging a detached startup. zenity is the system's GTK dialog tool
(`/usr/bin/zenity`), not part of MXCuBE or argussight. Only its Cancel button
counts as Cancel: if zenity itself fails, the next way to ask is tried.

`--uri` must keep the scheme (`redis://host:port`): `redis.from_url()` rejects a
bare `host:port`. video-streamer accepts both forms.

If the gate reports `no frames published on channel 'mxcubeweb'`, Redis was
reached (an unreachable server gives a connection error instead), but nothing
was published on that channel. Watch it by hand:

```sh
redis-cli -u redis://195.221.8.84:6379 subscribe mxcubeweb   # Ctrl-C to stop
```

A running camera prints a steady stream of messages. Silence means the camera
(`redis_camera2.py` on the camera server) is not running, or publishes on another
channel. In that case set `PX1_REDIS_CHANNEL`. A `Theme parser error` line
from zenity/GTK about the desktop theme is harmless.

Test it without hardware:

```sh
redis-server --port 6399 --daemonize yes
python check_frames.py --uri redis://localhost:6399 --timeout 5   # -> exit 3
```

## Adding a hutch camera

1. Uncomment/extend an entry in `CAMERAS` in `argus_cameras.py`.
2. Add a matching entry to `ARGUSSIGHT_CAMERAS` in the deployed `server.yaml`.
   **The `name` must match** or the stream is silently dropped from the switcher.
3. Exactly one camera carries `oav: true` — it is the default view and the only
   one with the centring overlay.

For `http://` sources leave `size` as `"0,0"`; video-streamer detects the real
resolution. Only the OAV needs a real size, because `RedisCamera` locks ffmpeg's
source size to it and a wrong value kills ffmpeg with a broken pipe.

## Deployment config (on proxima1, outside this repo)

### `server.yaml`

**Which file:** mxcubeweb reads `<hwr dir>/mxcube-web/server.yaml`, where `<hwr dir>` is what
mxgo.sh passes to `-r` (`find_in_repository("mxcube-web")` in `mxcubeweb/__init__.py`, then
`Config.load_config`). With mxgo.sh's `-r ../config` that is
`WebApp/config/mxcube-web/server.yaml` — editing `WebApp/config/server.yaml` changes nothing.
`ui.yaml` sits next to it in the same directory.

```yaml
mxcube:
  # The argussight proxy is WebSocket/MPEG1 only, so the switcher requires
  # MPEG1. Set the camera hardware object's `format` to match.
  VIDEO_FORMAT: MPEG1
  # Fallback when argussight is off or down; also what the sample view used
  # before this change, when it was hardcoded in beamline.py.
  VIDEO_STREAM_URL: https://mxcubeweb-px1.synchrotron-soleil.fr/video
  VIDEO_STREAM_PORT: 8000
  # argussight owns every streamer, so MXCuBE must not start one of its own.
  USE_EXTERNAL_STREAMER: false

  ARGUSSIGHT_ENABLED: true
  ARGUSSIGHT_GRPC_HOST: localhost
  ARGUSSIGHT_GRPC_PORT: 50051
  # Dialled by the BROWSER, so it must be wss:// through nginx (see Gotchas).
  ARGUSSIGHT_PROXY_URL: wss://mxcubeweb-px1.synchrotron-soleil.fr/argus
  ARGUSSIGHT_CAMERAS:
    - { name: oav, label: OAV (centring), width: 1360, height: 1024, oav: true }
    # Add hutch cameras here once their URLs are known; the names must match
    # the `name` values in argus_cameras.py's CAMERAS.
    # - { name: hutch_1, label: Hutch 1, width: 1920, height: 1080 }
```

Leave `ARGUSSIGHT_CAMERAS` empty to expose every discovered stream unfiltered —
useful for a first smoke test.

### The camera hardware object

PX1's deployed config is XML, so this is the file already declaring
`RedisMpegVideo` (the one `minidiff.xml` points at through
`<object role="camera" hwrid="..."/>`):

```xml
<object class="RedisMpegVideo">
  <username>Camera redis</username>
  <uri>redis://195.221.8.84:6379</uri>   <!-- the camera server, not localhost -->
  <host>localhost</host>                 <!-- where the streamer binds -->
  <port>8000</port>
  <format>MPEG1</format>                 <!-- was MJPEG -->
  <width>1360</width>                    <!-- NATIVE size: pixelsPerMm is per native pixel -->
  <height>1024</height>
  <quality>10</quality>
  <redis_key>mxcubeweb</redis_key>
</object>
```

Two changes to make when editing an existing PX1 camera file:

- **`<compression>` is now `<quality>`.** The old class read `compression`; this
  one reads `quality`. Leave the old tag in place and the value is silently
  ignored in favour of the default 10.
- **`<tangoname>` is no longer used** and can go. The class is pure Redis now, so
  it no longer builds a `DeviceProxy` (which is what made it fail off-beamline).

`uri`, `width` and `height` are required — `width`/`height` are passed through
`int()` with no default and will raise if absent.

### nginx

MXCuBE is served over https, so the browser refuses a plain `ws://` socket as
mixed content. nginx has to terminate TLS for the argussight proxy the same way
it already does for `/video`.

In the `http { }` context, once, next to the other global settings:

```nginx
# Canonical websocket upgrade mapping: "upgrade" for a websocket handshake,
# "close" otherwise. A hardcoded `Connection "upgrade"` breaks plain requests
# that hit the same location.
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

In the `server { }` block that already serves
`mxcubeweb-px1.synchrotron-soleil.fr` on 443:

```nginx
# Argussight stream proxy. The trailing slash on BOTH sides is what rewrites
# /argus/<name> to /ws/<name>, which is the path streamsproxy serves.
location /argus/ {
    proxy_pass http://127.0.0.1:7000/ws/;

    proxy_http_version 1.1;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection $connection_upgrade;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # MPEG1 streams are long-lived and must not be buffered: without these the
    # default 60 s read timeout drops the video roughly every minute, and
    # buffering adds latency to a stream that is supposed to be live.
    proxy_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

Then `ARGUSSIGHT_PROXY_URL: wss://mxcubeweb-px1.synchrotron-soleil.fr/argus`,
which discovery turns into `.../argus/oav` per stream.

`127.0.0.1:7000` assumes nginx and argussight share a host. If they do not, use
argussight's LAN address — `streamsproxy` binds `0.0.0.0`, so it is reachable
either way.

Reload and check:

```sh
nginx -t && systemctl reload nginx
# 101 Switching Protocols = the upgrade reached argussight
curl -isk -o /dev/null -w '%{http_code}\n' \
     -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
     -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
     https://mxcubeweb-px1.synchrotron-soleil.fr/argus/oav
```

Keep the existing `/video` location: it stays the fallback whenever argussight
is disabled or down.

**No login layer may guard `/argus/`.** A browser cannot authenticate during a
websocket handshake: if an SSO/auth proxy sits in front of that location, the
handshake gets a 302 to the login server instead of a 101 and the pane stays
black. `diagnose_video.py` reports a redirect there as its own failure.

## Gotchas

- **No process in this stack may see the SOLEIL site proxy**, and that includes
  `argussight` itself. websockets ≥ 15 honours `http(s)_proxy` even for
  `ws://localhost`, so argussight's upstream worker would dial the streamer
  through the site proxy, fail three times, drop the stream, and the browser
  would have its websocket refused with HTTP 403 (streamsproxy's pre-accept
  close 4404), a black pane while MJPEG still works.
  `start_argus_px1.sh` unsets the proxy vars and sets `no_proxy=*` for
  everything it starts. If you launch `argussight` by hand, do the same first.
  The gRPC clients (`argus_cameras.py`, mxcubeweb's discovery) also pass
  `grpc.enable_http_proxy: 0`.
- **MPEG1 needs `ffmpeg`** (MJPEG never did), in the mxcubeweb env or on the
  system PATH. Without it the streamer's websocket opens but sends nothing.
  `argus_cameras.py` refuses to start if the streamers cannot find it.
- **Two conda envs; the packages cannot share one.** argussight 0.3.2 wants
  newer pydantic/pillow (and declares video-streamer >= 1.9.1) than
  mxcubeweb's pins allow, but it never actually imports video-streamer:

  | env | runs | needs |
  |---|---|---|
  | `argussight` | argussight, `check_frames.py`, `argus_cameras.py` | argussight 0.3.2 + its deps (incl. `psutil`), `redis`, `grpc`, `websockets`. **No video-streamer.** |
  | `mxcubeweb` | MXCuBE and the `video-streamer` processes | its existing video-streamer (the one `RedisMpegVideo` uses), `ffmpeg`, and `grpcio` + `protobuf>=3.20.3` (tensorflow's 4.x is fine) for discovery. **No argussight.** |

  `start_argus_px1.sh` runs the helpers on the activated env's `python`
  (`HELPER_PY`) and hands `argus_cameras.py` the mxcubeweb python
  (`MXCUBE_PY`, exported as `ARGUS_STREAMER_PY`) for the streamers.

  Setting up / checking both on proxima1:

  ```sh
  # argussight env: argussight + helpers, NO video-streamer
  conda activate argussight
  pip uninstall -y mxcube-video-streamer    # not used here; its pins are what conflicted
  pip check                                 # if the failed install downgraded them:
  #   pip install "pillow>=12.2" "pydantic>=2.13"
  python -c "import argussight.grpc.server, redis, grpc, websockets; print('argussight env ok')"

  # mxcubeweb env: video-streamer (already there) + gRPC for discovery
  conda activate mxcubeweb
  python -m video_streamer.main -h | grep -- -irc   # Redis input supported
  pip list | grep -E "^(grpcio|protobuf) "   # tensorflow usually brought both
  #   only if grpcio is missing: pip install "grpcio<2"   (leave protobuf alone)
  cd /nfs/ruche/share-dev/px1dev/MXCuBE/WebApp/mxcubeweb
  python -c "import sys; sys.path.insert(0, 'mxcubeweb/core/util'); import argussight_grpc.argus_service_pb2_grpc; print('discovery ok')"
  which ffmpeg
  ```

  Do **not** install argussight into the mxcubeweb env: its dependencies
  would upgrade pydantic past what mxcubeweb allows. Discovery only needs
  argussight's two generated gRPC stub modules, and mxcubeweb carries its own
  (`mxcubeweb/core/util/argussight_grpc/`: argussight 0.3.2's .proto,
  regenerated with grpcio-tools 1.62.3). Those work with any grpcio and
  protobuf >= 3.20.3, including the protobuf 4.x that tensorflow 2.14 pins
  (`<5`). argussight's own stubs would need protobuf >= 5.29, so **do not
  upgrade protobuf** in this env. An argussight installed there with
  `pip install --no-deps` is harmless: it is used when its stubs load, and
  skipped for the vendored copy when they do not. `mxgo.sh` runs the check
  above at startup and prints the `pip install` line if it fails.
- **Only `ARGUSSIGHT_PROXY_URL` uses the public name**, and it must be `wss://`,
  because the browser opens it from an HTTPS page. `STREAM_HOST` and
  `ARGUS_GRPC` in `argus_cameras.py` stay `localhost`; argussight dials its
  upstreams at `ws://localhost:<port>` regardless.
- **argussight serves `/ws/<name>`, never `/ws/<name>/`**: a trailing slash is
  refused before the handshake completes.

## The app itself does not load

A black sample view inside a **broken page** is not a video problem: without the
backend there is no camera data at all. Check the browser console first.

| Console line | What it means | Check |
|---|---|---|
| `…/login/login_info` **502 Bad Gateway**, `nginx/…`, and **nothing listens on :8081** | mxcubeweb is not running | start it with mxgo.sh; the startup error is at the end of `~/MXCuBElogs/mxcube.log` |
| the same 502 **while :8081 is listening** | whoever proxies to it uses the wrong scheme | `curl -i http://127.0.0.1:8081/mxcube/api/v0.1/login/login_info` and the same with `-k https://`: exactly one answers. That scheme is what `ui/vite.config.js`'s `/mxcube/api` target and nginx's `proxy_pass` must use. `server: CERT:` in `server.yaml` decides it: `NONE` (the default) means plain `http://`, `SIGNED`/`ADHOC` mean `https://` |
| `Expected JSON response but got text/html` | the same 502 (or a login page) reaching the UI as HTML | `curl -isk https://<host>/mxcube/api/v0.1/login/login_info \| head -3` |
| `…/manifest.json` redirected to `iam.synchrotron-soleil.fr`, blocked by CORS | the manifest is fetched without credentials and an SSO layer redirects it | harmless on its own; it goes away once you are logged in and the API answers |
| a 302 to the login server on `/argus/<name>` | the auth layer also guards the video | exclude `location /argus/` from it (see nginx above) |

`diagnose_video.py` checks all of this first, as hop 0.

## Troubleshooting a black sample view

**Start with the diagnostic.** It checks the whole chain from the MXCuBE host
and names the first broken hop:

```sh
conda activate argussight
python scripts/argussight/diagnose_video.py            # --config <file> if it is not ../../../config[/mxcube-web]/server.yaml
python scripts/argussight/diagnose_video.py --insecure # if only the TLS certificate check fails
```

The checks, in order: the `server.yaml` video keys; mxcubeweb on `:8081`; which frontend answers;
the streamer on `:9000`; the proxy on `:7000`; `GetProcesses` and the stream URL it yields; that URL
through nginx. It then groups the uvicorn tracebacks in `argussight.log` — uvicorn is the web server
running argussight's proxy, and its `WebsocketState` and `Cannot call "send"` tracebacks fire when a
viewer disconnects and are harmless.

If every hop passes, the problem is in the browser: check devtools → Network → WS `oav`.

After registering the streams, `argus_cameras.py` probes each camera, first
directly on its streamer and then through the argussight proxy, and logs one
`SELF-TEST <name>:` line per camera to `~/MXCuBElogs/argussight.log`:

| Log line | Meaning | Next step |
|---|---|---|
| `SELF-TEST oav: streaming OK` | Frames reach the proxy | Problem is browser-side: nginx `/argus/`, `ARGUSSIGHT_PROXY_URL`, discovery |
| `streamer ... gives no video` | Streamer not running, or ffmpeg produces nothing | Restart with `ARGUS_STREAMER_DEBUG=1` to get ffmpeg's stderr; check the camera size and `check_frames.py` |
| `streamer OK but nothing listens on :7000 -- argussight is not running` | argussight crashed or never started | Its error is earlier in the log (see the startup rows below) |
| `streamer OK but the argussight proxy gives no video` | argussight dropped the stream | Look for the two lines below; check the proxy env of the argussight process |
| `ffmpeg not found on the streamers' PATH` (exit 1) | ffmpeg missing | Install it into the mxcubeweb env (or the system) |
| `video-streamer is not usable with <python>` (exit 1) | That interpreter has no (Redis-capable) video-streamer | Check `MXCUBE_ENV`/`MXCUBE_PY` point at the mxcubeweb env |

Startup messages from `start_argus_px1.sh` (`mxgo.sh` prints the log tail when
the script exits):

| Log line | Meaning | Next step |
|---|---|---|
| `FileNotFoundError: ... 'config.yaml'` | argussight ≥ 0.3.2 started without `-c` | You are running an older `start_argus_px1.sh`; update it |
| `KeyError: 'log_dir'` | `-c` points at a config without `log_dir` (e.g. the one shipped in the package) | Point `ARGUS_CONFIG_DIR` at `config/` |
| `argussight exited with code N before binding its ports` | argussight crashed at startup | The traceback is just above; missing packages show as `ModuleNotFoundError` |
| `argussight still not listening on :50051 :7000 after 30s` | argussight is alive but stuck | `$ARGUS_WORKDIR/logs/Shared_logs.log`, and `ps` for its children |
| `port N is already in use` | Something else holds a stack port | `ss -ltnp 'sport = :N'` names it |
| `N/M cameras NOT registered` | `AddStream` failed: :50051 unreachable | argussight is down; see its error above |
| `argussight exited (code N); stopping the camera streamers` | argussight died while running | Its log; then relaunch (`mxgo.sh` or the script) |
| argussight: `Upstream worker for oav failed` then `Removing stream at path /oav due to upstream failure` | Proxy could not reach the streamer | Almost always proxy env vars; see Gotchas |
