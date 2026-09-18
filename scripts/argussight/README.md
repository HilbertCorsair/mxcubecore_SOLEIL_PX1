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

Useful overrides (all environment variables):
`PX1_REDIS_URI`, `PX1_REDIS_CHANNEL`, `CONDA_ACTIVATE`, `CONDA_ENV`,
`MXCUBE_ENV`, `MXCUBE_PY`, `ARGUSSIGHT_BIN`, `PIDFILE`.

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
hanging a detached startup.

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

## Gotchas

- **The MXCuBE web env needs `grpc` + `argussight` importable**, or discovery
  logs a warning and returns `[]` (app still runs, no switcher).
- The browser dials the proxy directly. MXCuBE at PX1 is served over **HTTPS**,
  so `ARGUSSIGHT_PROXY_URL` must be `wss://` through nginx — an HTTPS page
  cannot open a plain `ws://` socket.
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
- **MPEG1 needs `ffmpeg`** on the PATH of the env running `argus_cameras.py`
  (MJPEG never did). Without it the streamer's websocket opens but sends
  nothing. `argus_cameras.py` refuses to start if it is missing.
- **Only `ARGUSSIGHT_PROXY_URL` uses the public name.** `STREAM_HOST` and
  `ARGUS_GRPC` in `argus_cameras.py` stay `localhost`; argussight dials its
  upstreams at `ws://localhost:<port>` regardless.

## Troubleshooting a black sample view

After registering the streams, `argus_cameras.py` probes each camera, first
directly on its streamer and then through the argussight proxy, and logs one
`SELF-TEST <name>:` line per camera to `~/MXCuBElogs/argussight.log`:

| Log line | Meaning | Next step |
|---|---|---|
| `SELF-TEST oav: streaming OK` | Frames reach the proxy | Problem is browser-side: nginx `/argus/`, `ARGUSSIGHT_PROXY_URL`, discovery |
| `streamer ... gives no video` | Streamer not running, or ffmpeg produces nothing | Restart with `ARGUS_STREAMER_DEBUG=1` to get ffmpeg's stderr; check the camera size and `check_frames.py` |
| `streamer OK but the argussight proxy gives no video` | argussight dropped the stream | Look for the two lines below; check the proxy env of the argussight process |
| `ffmpeg not found on PATH` (exit 1) | ffmpeg missing | Install it into the mxcubeweb env |
| `has no RedisCamera(size=...)` | Upstream video-streamer installed | Install the fork `px2_video_streamer_v1.9.1` |
| argussight: `Upstream worker for oav failed` then `Removing stream at path /oav due to upstream failure` | Proxy could not reach the streamer | Almost always proxy env vars; see Gotchas |
