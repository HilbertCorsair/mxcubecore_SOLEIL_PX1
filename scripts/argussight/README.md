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
| 9000, 9001, … | one `video-streamer` per camera | argussight only, over `localhost` | MPEG1. Its `/ui` page works **only from the streamer's own host** — see the video-streamer stage below |
| 7000 | argussight's stream proxy | the **browser**, through nginx `/argus/` | MPEG1, one path per camera |
| 50051 | argussight's gRPC server | `argus_cameras.py` (`AddStream`), mxcubeweb (`GetProcesses`) | control and discovery, never video |
| 8000 | MXCuBE's own streamer, only with `USE_EXTERNAL_STREAMER: true` | nginx `/video` | the pre-argussight fallback; **nothing listens here in this setup** |
| 8081 | mxcubeweb (hardcoded in `mxcubeweb/server.py`'s `run()`) | nginx `/mxcube/api`, `/socket.io` | the application |
| 5173 | the Vite dev server (`ui/vite.config.js`) | nginx `/` | the page, served from `ui/src` |
| 443, **or whatever nginx publishes** (7443 on proxima1) | nginx | the browser | all of the above, over TLS. Every URL the page opens must carry this port — see "The port nginx publishes" |

The proxy only serves streams registered through :50051, so :7000 alone is an
empty proxy. Both come from the same startup (`Spawner.__init__` starts the
proxy, then `serve()` binds :50051), so if that startup fails, neither opens.

### The video path, hop by hop

**Nothing on the server side hands out decoded frames.** The camera's raw images
are compressed into a video stream on the way out, and only the browser
decompresses it.

Five stages carry the picture. A sixth path carries no video at all but decides
the address the browser will open, so it breaks the view just as effectively.
Three of the five are what `diagnose_video.py` calls hops `1`, `2` and `5`, named
below so its `FIRST BROKEN HOP` line points straight at one section.

```
        camera
          |   raw pixels
          v
  +------------------+
  | Redis :6379      |   camera server 195.221.8.84
  +------------------+
          |   raw pixels, 1360x1024 for the OAV
          v
  +------------------+
  | video-streamer   |   MXCuBE host, one process per camera, :9000 :9001 ...
  | ffmpeg -of MPEG1 |   >>> diagnose hop `1 streamer <name>`
  +------------------+
          |   MPEG-1 in MPEG-TS, over ws://<mxcube host>:9000/ws/<hash>
          v
  +------------------+
  | argussight       |   MXCuBE host, :7000 proxy + :50051 gRPC
  | stream proxy     |   >>> diagnose hop `2 proxy <name>`
  +------------------+
          |   the same bytes, over ws://<mxcube host>:7000/ws/<name>
          v
  +------------------+
  | nginx :443       |   may be a container, and may be on another host
  |                  |   >>> diagnose hop `5 nginx`
  +------------------+
          |   the same bytes, over wss://<public host>[:port]/argus/<name>
          v
  +------------------+
  | browser: JSMpeg  |   decodes and paints the canvas
  +------------------+

  side-channel, no video:  browser -> mxcubeweb :8081 -> argussight :50051
                           returns videoURL + videoHash, i.e. the address
                           the browser then opens on nginx
```

| Stage | Sends | To | `diagnose_video.py` |
|---|---|---|---|
| Redis | raw pixels | the streamers | none — use `check_frames.py` |
| video-streamer | MPEG-1 in MPEG-TS | `ws://<mxcube>:9000/ws/<hash>` | `1 streamer <name>` |
| argussight proxy | the same bytes | `ws://<mxcube>:7000/ws/<name>` | `2 proxy <name>` |
| nginx | the same bytes, over TLS | `wss://<public>[:port]/argus/<name>` | `5 nginx` |
| browser | pixels, at last | the canvas | none — devtools → Network → WS |
| discovery | JSON and gRPC | `:8081` → `:50051` | `0 mxcubeweb`, `3 discovery`, `4 config`, `page` |

#### camera → Redis

- **Does:** `redis_camera2.py` publishes every image as raw pixels.
- **Runs:** on the camera server, **started by hand** — nothing in this repo
  starts or restarts it.
- **Broken looks like:** every streamer produces nothing at once. The startup
  gate in `argus_cameras.py` catches it and refuses to start the stack (exit 3).
- **Test:** `python scripts/argussight/check_frames.py`. The frames it waits for
  are these raw ones — the only place in the chain where a frame is a picture.

#### Redis → video-streamer `:9000` — hop `1 streamer`

- **Does:** **encodes.** ffmpeg (`-of MPEG1`) turns the raw frames into MPEG-1
  video inside an MPEG-TS container and serves it over a websocket.
- **Runs:** one process per camera on the MXCuBE host, started by
  `argus_cameras.py`, in the **mxcubeweb** env (it needs ffmpeg on `PATH` and a
  Redis-capable video-streamer).
- **Broken looks like:** `streamer ... gives no video` in the self-test.
- **Test:** restart with `ARGUS_STREAMER_DEBUG=1` to get ffmpeg's stderr. Do
  **not** judge it by `http://<host>:9000/ui` from another machine: that page
  hardcodes `ws://localhost:9000/ws/<hash>` and the *browser* resolves the
  `localhost`, so it renders black everywhere except the streamer's own host.
  Probe `ws://<host>:9000/ws/<hash>` directly instead.

#### video-streamer → argussight proxy `:7000` — hop `2 proxy`

- **Does:** **relays the bytes unchanged.** One entry point per camera, plus
  switching between cameras. No image processing whatsoever.
- **Runs:** on the MXCuBE host, bound to `0.0.0.0` (`streamsproxy.py`), so it is
  reachable from other hosts without any change.
- **Registration:** a stream exists here only once `AddStream` has been called on
  `:50051`. The proxy and the gRPC server come from the same startup, so if that
  fails neither port opens, and `:7000` alone is an empty proxy.
- **The route is `@app.websocket("/ws/{path}")`, and nothing else.** Three
  consequences worth knowing before reading any error:
  - `{path}` is `[^/]+`, so `/ws/`, `/ws/oav/` and `/ws//oav` all miss the route
    → closed before accept → the client sees **403**.
  - An unregistered name closes with **4404** → also surfaces as 403.
  - There are **no HTTP routes at all**, so any plain GET — including opening
    `http://localhost:7000` in a browser — returns 404 by design. A real
    websocket handshake never returns 404.
- **Test:** a handshake by hand — `101 Switching Protocols` means the bytes are
  flowing, and this is the same command to aim at nginx one hop later:

  ```sh
  curl -i -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
       -H 'Sec-WebSocket-Version: 13' \
       -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
       http://127.0.0.1:7000/ws/oav
  ```

#### argussight proxy → nginx `:443` (`:7443` on proxima1) — hop `5 nginx`

- **Does:** forwards the same bytes over TLS. `location /argus/` with a trailing
  slash on **both** sides rewrites `/argus/<name>` to `/ws/<name>`.
- **Runs:** possibly in a container, possibly on another host. This matters more
  than anything else in this file — see the nginx section and the caveats there.
- **Needs:** the upgrade headers *and* the `map $http_upgrade $connection_upgrade`
  at `http{}` level. Without the `map`, `nginx -t` fails and **no reload since
  has taken effect**.
- **Broken looks like:**

  | Status | Cause |
  |---|---|
  | **nothing at all in nginx's log**, and a failed handshake in the browser | the browser is dialling another port: `ARGUSSIGHT_PROXY_URL` is absolute and names :443 while nginx publishes something else. See "The port nginx publishes" |
  | 502 + `connect() failed (111: Connection refused)` | instant RST: nothing listens at the `proxy_pass` address. With hop `2 proxy` passing, that address is wrong — typically `127.0.0.1` from inside a container |
  | 504, or a hang with no status | the connect is being dropped: a firewall between nginx and `:7000`, or the public VIP is unreachable from where you are testing |
  | 403 | the handshake reached argussight and it closed first: trailing slash, or an unregistered stream |
  | 404 **from nginx** (HTML body, `Server: nginx`) | nginx matched no location — `/argus/` is missing or spelled differently |
  | 404 **from argussight** (`{"detail":"Not Found"}`, `Server: uvicorn`) | the request arrived without the upgrade headers, so it was handled as plain HTTP. See "argussight logs `GET /ws/<name> … 404`" |
  | 400 or 426 | the same thing seen one layer up: nginx itself rejected a request it would not upgrade |
  | 302 to `iam.synchrotron-soleil.fr` | the SSO layer is guarding the video; exclude `location /argus/` from it |

- **Test:** the same `curl` as the hop above, against
  `https://<public host>[:port]/argus/oav`, and `nginx -T | grep -A4 'location /argus/'`
  to see the address actually in force rather than the one in the file you
  edited. On a containerized nginx that is
  `docker compose exec nginx nginx -T`, and the reachability of the upstream is
  `docker compose exec nginx sh -c 'nc -zvw3 <mxcube host> 7000'`.

#### nginx → browser

- **Does:** **decodes.** This is the only place in the chain where the bytes
  become a picture again; JSMpeg is described just below.
- **Broken looks like:** a black canvas with no error on it means JSMpeg received
  no bytes — the fault is on the path to the browser, not in the picture.
- **Test:** devtools → Network → WS → `oav`. This is the authoritative test, and
  the only one that is valid when the beamline host cannot reach its own public
  address (no NAT hairpinning, which makes hop `5 nginx` untestable from there).

#### discovery: how the browser learns the address

No video passes here, but it produces the URL hop `5 nginx` is judged on.

- mxcubeweb (`:8081`) makes **one** gRPC `GetProcesses` call to `:50051` and
  hands the page `videoURL` + `videoHash`.
- `beamline.py` splits the discovered URL at the last `/`, so `videoURL` is the
  proxy base and `videoHash` the stream name. That is deliberate: every frontend
  opens `${videoURL}/${videoHash}`, and bundles built before the camera switcher
  do so even when the hash is empty — which would give `.../oav/` and the proxy's
  403. A root-relative base survives the split unchanged (`/argus` + `oav`), and
  `initJSMpeg` resolves it against the page's origin.
- If argussight cannot be asked, discovery returns the cameras configured in
  `server.yaml` unchecked; that guessed URL is the only one that can work, since
  the direct streamer is not exposed.
- **Broken looks like:** the page opens a plausible-looking URL that nothing
  serves. Compare what devtools shows JSMpeg opening against what
  `diagnose_video.py` reports at `3 discovery`.

A `BINARY 47 41 00 ...` line in argussight's debug log is compressed video
(`0x47` is the MPEG-TS sync byte), not a picture. To look at the proxy's output
directly, something has to decode it: JSMpeg in a page, or ffmpeg
(`ffmpeg -f mpegts -i <capture>.ts -frames:v 1 out.png`). Looking at the raw
bytes proves only that data flows.

**JSMpeg** (`jsmpeg.min.js`) is a third-party MPEG-1 decoder written in
JavaScript with an embedded WebAssembly core (phoboslab/jsmpeg, MIT). Browsers
cannot play MPEG-1 from a websocket natively; JSMpeg splits the MPEG-TS stream,
decodes each frame and draws it with WebGL. Nothing generates the file: it is
copied by hand into the mxcubeweb source at
`ui/src/components/SampleView/jsmpeg.min.js`, edited into an ES module
(`export const JSMpeg`). `SampleImage.jsx` imports it and runs
`new JSMpeg.Player(<videoURL>/<videoHash>, { canvas: #sample-img })`.

It exists as a separate file **only in `ui/src`**: `pnpm build` folds it into
`ui/build/assets/index-<hash>.js`, so the served site and `ui/build` never
contain a `jsmpeg.min.js`. When searching a checkout for it, remember that `find`
does not descend into symlinked directories unless given `-L`.

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
curl -sk https://mxcubeweb-px1.synchrotron-soleil.fr:7443/ | grep -E 'src="/(src|assets)/|@vite'
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
curl -sk https://mxcubeweb-px1.synchrotron-soleil.fr:7443/ | grep -o 'src="/assets/[^"]*"'
```

nginx must then send everything to mxcubeweb instead of :5173 — the page, the
API and socket.io — using the scheme :8081 speaks (`http` unless
`server: CERT:` is `SIGNED`/`ADHOC`):

```nginx
location / {
    proxy_pass http://127.0.0.1:8081;
    # $http_host, not $host: $host drops the port, and mxcubeweb compares the
    # page's Origin against it. See "The port nginx publishes".
    proxy_set_header Host              $http_host;
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
    proxy_set_header Host       $http_host;
    proxy_read_timeout 3600s;
}
```

Without that `location /socket.io/` the app's own websocket gets a **400** and
the console repeats the failure — see "socket.io fails with 400" below.

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
  # Dialled by the BROWSER. Root-relative on purpose: the page resolves it
  # against its own origin, so the stream always uses the host, port and TLS
  # the page itself was served on. An absolute wss://... works too, but then
  # its port has to be kept in step with nginx by hand — see "The port nginx
  # publishes".
  ARGUSSIGHT_PROXY_URL: /argus
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
    # 127.0.0.1 is right only when nginx and argussight share a host. nginx in
    # a container has its own loopback, so this would address the container
    # itself and be refused; use argussight's LAN address there.
    proxy_pass http://127.0.0.1:7000/ws/;

    # These three lines must live INSIDE this location. A location that sets any
    # proxy_set_header of its own discards every proxy_set_header inherited from
    # server{} / http{} -- so an upgrade pair defined once at server level
    # silently disappears here, and argussight answers the un-upgraded request
    # with 404. Same for location /socket.io/.
    proxy_http_version 1.1;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection $connection_upgrade;

    proxy_set_header Host              $http_host;   # $host drops the port
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

Then `ARGUSSIGHT_PROXY_URL: /argus`, which discovery turns into `/argus/oav`
per stream and the page resolves against its own origin.

`127.0.0.1:7000` assumes nginx and argussight share a host. If they do not —
in particular if nginx runs in a container — use argussight's LAN address;
`streamsproxy` binds `0.0.0.0`, so it is reachable either way. Three signs that
nginx is somewhere else: its log lines carry a `nginx            | ` prefix
(docker compose output), its worker PIDs are two-digit (a fresh PID namespace),
and a neighbouring `location` already proxies to a LAN IP instead of
`127.0.0.1` — whoever wrote that one had to.

Reload and check. A containerised nginx reads a bind-mounted file, so find the
one it actually reads (`docker compose config | grep -A5 volumes`) before
editing: a copy it does not mount changes nothing.

```sh
nginx -t && systemctl reload nginx                         # nginx on this host
docker compose exec nginx nginx -t \
    && docker compose exec nginx nginx -s reload           # nginx in a container
# 101 Switching Protocols = the upgrade reached argussight
curl -isk -o /dev/null -w '%{http_code}\n' \
     -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
     -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
     https://mxcubeweb-px1.synchrotron-soleil.fr:7443/argus/oav
```

Keep the existing `/video` location: it stays the fallback whenever argussight
is disabled or down.

**No login layer may guard `/argus/`.** A browser cannot authenticate during a
websocket handshake: if an SSO/auth proxy sits in front of that location, the
handshake gets a 302 to the login server instead of a 101 and the pane stays
black. `diagnose_video.py` reports a redirect there as its own failure.

### The port nginx publishes

nginx does not have to be on :443, and on proxima1 it is not: the container
publishes **:7443**, so the page is
`https://mxcubeweb-px1.synchrotron-soleil.fr:7443/`. Everything the browser
dials must then carry that port, and two things lose it by default:

- **The video URL.** `ARGUSSIGHT_PROXY_URL` is handed to the page verbatim —
  it is the one address discovery does not derive from anything — so an
  absolute `wss://mxcubeweb-px1.synchrotron-soleil.fr/argus` sends the browser
  to **:443** while nginx listens on :7443. The handshake fails, JSMpeg gets no
  bytes, and the pane is black **with nothing in nginx's log**: the request
  never reached it. That silence is the signature, because a fault at nginx
  logs something. Write it root-relative, `/argus`, and the page resolves it
  against its own origin. socket.io never had this problem: `serverIO.js`
  builds its URL from `window.location.origin`.
- **The `Host` header.** `proxy_set_header Host $host` drops the port;
  `$http_host` keeps it. mxcubeweb compares the page's `Origin` against the
  address it believes is its own, so a portless `Host` makes every socket.io
  handshake look cross-origin. Use `$http_host` in every block.

`diagnose_video.py` cannot guess the port, so tell it:

```sh
python scripts/argussight/diagnose_video.py \
       --public-origin https://mxcubeweb-px1.synchrotron-soleil.fr:7443
```

It then checks the page, the health endpoint, `/argus/<name>` and socket.io on
that origin, and hop `4 config` fails outright when `ARGUSSIGHT_PROXY_URL`
names a different port.

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
- **Only `ARGUSSIGHT_PROXY_URL` is dialled by the browser.** Written
  root-relative (`/argus`) it needs nothing else: the page supplies the scheme,
  host and port, and `wss://` follows from the page being HTTPS. Written
  absolutely it must be `wss://` **and carry nginx's published port**, or the
  browser opens a port nothing serves. `STREAM_HOST` and `ARGUS_GRPC` in
  `argus_cameras.py` stay `localhost`; argussight dials its upstreams at
  `ws://localhost:<port>` regardless.
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
| `WebSocket connection to 'wss://…/socket.io/?EIO=4&transport=websocket' failed`, on repeat, with **400** in nginx's log | the app's own websocket is refused. The app still runs, by polling, and this does **not** blank the video | "socket.io fails with 400", below |

`diagnose_video.py` checks all of this first, as hop 0.

### socket.io fails with 400

socket.io is the app's own websocket — two of them, the `/hwr` and `/logging`
namespaces, so the console shows the failure twice. It carries no video, and
the sample view does not depend on it: the camera list and the stream URL
arrive over REST, and `transports: ['websocket', 'polling']` falls back to
polling. **A black pane is never explained by this.** What it costs is the live
updates: motor positions, the log stream, queue state.

A `400` is an answer rather than a mystery, because python-engineio puts the
reason in the response body. The command that separates nginx from mxcubeweb is
the same handshake aimed straight at :8081:

```sh
curl -i -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
     -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
     -H 'Origin: https://mxcubeweb-px1.synchrotron-soleil.fr:7443' \
     'http://127.0.0.1:8081/socket.io/?EIO=4&transport=websocket'
```

| Result | Cause | Fix |
|---|---|---|
| `101` here, 400 through nginx | nginx is not forwarding the upgrade | add `location /socket.io/` with `proxy_http_version 1.1` and the upgrade headers (see "Switching to the production build") |
| `400 "Origin not allowed"` | `server: ALLOWED_CORS_ORIGINS` does not list the page's origin. `server.py` hands that list straight to `SocketIO(cors_allowed_origins=…)`, and engineio skips the check only while it is **empty** | add the origin **with its port**, or empty the list; and use `Host $http_host` |
| `400`, no reason given, but `101` once the `Origin:` header is dropped | the same thing, unstated | as above |
| `400 "WebSocket transport not available"` | this mxcubeweb env cannot serve websockets | install `gevent-websocket` (or `simple-websocket`) into it, restart |
| 502, or no answer at all | mxcubeweb is not answering on :8081 | hop 0, above |

Two candidates worth ruling out explicitly, because they look plausible and are
not it here: an **Engine.IO version mismatch** (`Flask-SocketIO ^5.3.6` and
`socket.io-client ^4.8.1` are both EIO 4) and **sticky sessions** (one process
listens on :8081, and a URL failing without a `sid` never reached a session).

A third: if nginx sends `/` to the **vite dev server**, vite proxies
`/socket.io/` onward to whatever `ui/vite.config.js` names — currently a
different hostname, over `wss://`. Check which frontend answers first.

`diagnose_video.py` runs this whole matrix as hop `6 socket.io`.

## Troubleshooting a black sample view

**Start with the diagnostic.** It checks the whole chain from the MXCuBE host
and names the first broken hop:

```sh
conda activate argussight
# --public-origin whenever nginx does not publish :443 (it publishes 7443 here)
python scripts/argussight/diagnose_video.py \
       --public-origin https://mxcubeweb-px1.synchrotron-soleil.fr:7443
# add --config PATH if server.yaml is not at ../../../config[/mxcube-web]/server.yaml,
# and --insecure if only the TLS certificate check fails
```

The checks, in order: the `server.yaml` video keys; mxcubeweb on `:8081`; which frontend answers;
the streamer on `:9000`; the proxy on `:7000`; `GetProcesses` and the stream URL it yields; that URL
through nginx; and socket.io, through nginx and then straight at `:8081`.
It then groups the uvicorn tracebacks in `argussight.log` — uvicorn is the web server
running argussight's proxy, and its `WebsocketState` and `Cannot call "send"` tracebacks fire when a
viewer disconnects and are harmless.

If every hop passes, the problem is in the browser: check devtools → Network → WS `oav`.

### argussight logs `GET /ws/<name> HTTP/1.1 404`

This one reads as "wrong path" and never is. `streamsproxy.py` declares exactly
one route, `@app.websocket("/ws/{path}")`, and **no HTTP route at all**, so:

- a **websocket** handshake for an unknown stream is closed with 4404 before
  `accept()`, which the browser sees as **403** — never 404;
- a **plain HTTP** request to the very same, correct path can only be answered
  **404**, by Starlette's fallback, with `{"detail":"Not Found"}` and
  `Server: uvicorn`.

So a 404 in argussight's log means the request reached it **stripped of its
`Upgrade` / `Connection` headers** — nginx proxied it but did not upgrade it. The
browser gets that 404 as a failed handshake, and JSMpeg's `WSSource` retries
every 5 s, which is why the 404s arrive in a steady stream.

`/socket.io/` fails the same way for the same reason, and that is worth knowing
because it looks like a second, unrelated bug: engineio requires
`transport == upgrade_header == "websocket"` and otherwise answers
**400 "Invalid websocket upgrade"** (`engineio/server.py`). One missing pair of
headers, two console errors, and a black pane.

Which side dropped them:

```sh
# through nginx -- 404 here while the line below gives 101 is the signature
curl -isk \
     -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
     -H 'Sec-WebSocket-Version: 13' \
     -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
     'https://mxcubeweb-px1.synchrotron-soleil.fr:7443/argus/oav' | head -12

# straight at argussight -- must be 101
curl -is \
     -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
     -H 'Sec-WebSocket-Version: 13' \
     -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
     'http://127.0.0.1:7000/ws/oav' | head -12
```

Read the `Server:` header and the body, not just the number: `uvicorn` with
`{"detail":"Not Found"}` is argussight answering a plain request, `nginx` with an
HTML error page is nginx answering for itself. `diagnose_video.py`'s hop
`5 nginx` now makes exactly this distinction and names the cause.

Then, in nginx, in this order:

| Check | What it catches |
|---|---|
| `nginx -t` | a missing `map $http_upgrade $connection_upgrade` in `http{}`. It fails the whole config, so **no reload since has taken effect** |
| `nginx -T \| grep -B2 -A12 'location /argus/'` | the upgrade lines absent, or a different file in force than the one you edited |
| the same, for every `proxy_set_header` in that block | **the inheritance trap: a `location` that sets any `proxy_set_header` of its own discards every one inherited from `server{}` / `http{}`.** Add the upgrade pair *inside* each location that needs it — `/argus/` and `/socket.io/` both do |
| `docker compose config \| grep -A5 volumes` | a containerized nginx reading a config from a different path than you think |

Two results that look like a contradiction, and are not:

| What you see | What it means | Fix |
|---|---|---|
| `/argus/<name>` gives 502 through nginx, while `curl` on the beamline host gets `101` from `127.0.0.1:7000/ws/<name>` | Both are true, of two different machines: nginx is on another host or in a container, so its `127.0.0.1` is not this one. `connect() failed (111: Connection refused)` in its log is an instant RST — nothing is listening, as opposed to a timeout (110) or SELinux (13) | give `proxy_pass` argussight's LAN address (see nginx above) |
| `nginx -T` fails with `unknown "connection_upgrade" variable` | the `map` is missing from `http{}`. It is also proof that **every reload since has been rejected, so none of your edits to the config has taken effect** | add the `map` at `http{}` level, then retest every hypothesis you ruled out while it was failing |

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
| argussight: `"GET /ws/oav HTTP/1.1" 404`, repeating | The request reached argussight without its upgrade headers | nginx is not upgrading — see "argussight logs `GET /ws/<name> HTTP/1.1 404`" |
| argussight: `Max reconnection attempts reached for oav` | Four consecutive upstream failures retired the stream, and nothing re-adds it: every later client now gets 403 | restart the stack. Up to argussight 0.3.2 this also triggered after a *single* failure whenever no client was connected |
