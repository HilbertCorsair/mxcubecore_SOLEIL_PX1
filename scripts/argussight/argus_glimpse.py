#!/usr/bin/env python
"""Glimpse at what argussight streams on localhost.

    python argus_glimpse.py                  # 5 s of 'oav': data rate, a .ts file, one PNG frame
    python argus_glimpse.py --name hutch_1   # same for another camera (names: argus_cameras.CAMERAS)
    python argus_glimpse.py --all            # every camera in argus_cameras.CAMERAS, one after another
    python argus_glimpse.py --direct         # straight from the camera's video-streamer, bypassing argussight
    python argus_glimpse.py --play           # live window (needs ffplay and an X display)
"""
import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import time

import websockets

import argus_cameras

# The same proxy problem as everywhere in this stack: websockets >= 15 honours
# http(s)_proxy even for ws://127.0.0.1, and on the beamline that is the SOLEIL
# site proxy, which cannot reach this host. Never use it.
argus_cameras._strip_proxy(os.environ)

# ffmpeg/ffplay live in the env that runs the video-streamers (the mxcubeweb
# env), not necessarily in the one running this script.
STREAMER_BIN = os.path.dirname(argus_cameras.STREAMER_PY)

# What ffmpeg prints, harmlessly, for every frame before the first MPEG-1
# sequence header: the capture starts mid-stream, and only that header carries
# the picture size. Decoding goes on normally once it arrives.
_NOISE = ("Invalid frame dimensions 0x0", "Last message repeated")


def tool(name):
    return shutil.which(name) or shutil.which(name, path=STREAMER_BIN)


def connect(url):
    try:
        return websockets.connect(url, max_size=None, open_timeout=5, proxy=None)
    except TypeError:  # websockets < 15: no proxy support, no kwarg
        return websockets.connect(url, max_size=None, open_timeout=5)


async def pump(url, seconds, sink):
    total = msgs = 0
    async with connect(url) as ws:
        print(f"connected: {url}")
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            try:
                data = await asyncio.wait_for(ws.recv(), timeout=left)
            except asyncio.TimeoutError:
                break
            except websockets.ConnectionClosed as e:
                # Keep what arrived; the caller reports "no video" if nothing did.
                print(f"stream closed after {msgs} messages: {e}")
                break
            if isinstance(data, str):
                print(f"text message: {data[:200]!r}")
                continue
            if total == 0:
                kind = ("MPEG-TS, as JSMpeg expects" if data[:1] == b"\x47"
                        else f"NOT MPEG-TS, starts {data[:8].hex(' ')}")
                print(f"first data: {len(data)} bytes, {kind}")
            sink.write(data)
            total += len(data)
            msgs += 1
    return total, msgs


def url_for(name, direct):
    if not direct:
        return f"ws://127.0.0.1:{argus_cameras.ARGUS_PROXY_PORT}/ws/{name}"
    port = next((c["port"] for c in argus_cameras.CAMERAS if c["name"] == name), None)
    if port is None:
        sys.exit(f"{name!r} is not in argus_cameras.CAMERAS; --direct needs its port")
    # The streamers are started with `-id <name>`, so their path is the name too.
    return f"ws://127.0.0.1:{port}/ws/{name}"


def play(url, seconds):
    ffplay = tool("ffplay") or sys.exit("ffplay not found: run without --play for a PNG")
    p = subprocess.Popen([ffplay, "-loglevel", "error", "-window_title", url,
                          "-f", "mpegts", "-i", "-"], stdin=subprocess.PIPE)
    try:
        asyncio.run(pump(url, seconds or 1e9, p.stdin))
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    except Exception as e:
        print(f"FAILED: {url}: {type(e).__name__}: {e}")
    finally:
        p.terminate()


def glimpse(name, url, seconds):
    """Capture `seconds` of one stream and save a frame; return True on video."""
    tag = url.rsplit(":", 1)[1].split("/", 1)[0]  # the port
    ts = f"/tmp/argus_{name}_{tag}.ts"
    try:
        with open(ts, "wb") as f:
            total, msgs = asyncio.run(pump(url, seconds, f))
    except Exception as e:
        print(f"FAILED: {url}: {type(e).__name__}: {e}")
        return False

    if not total:
        print(f"connected, but NO video data in {seconds:.0f} s")
        return False
    print(f"{msgs} messages, {total / 1e3:.0f} kB in {seconds:.0f} s "
          f"= {total * 8 / seconds / 1e3:.0f} kbit/s -> {ts}")

    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found: no PNG, but the data above is the stream")
        return True
    png = ts[:-3] + ".png"
    r = subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "mpegts", "-i", ts,
                        "-frames:v", "1", png], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if not any(n in line for n in _NOISE):
            print(f"ffmpeg: {line}")
    if r.returncode == 0 and os.path.exists(png):
        print(f"frame saved: {png}")
    else:
        # No sequence header in the capture: the stream's keyframes are further
        # apart than `seconds` (a slow camera); capture longer.
        print("ffmpeg could not decode a frame; try a longer --seconds")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="oav")
    ap.add_argument("--all", action="store_true", help="every camera in CAMERAS")
    ap.add_argument("--direct", action="store_true",
                    help="read the camera's video-streamer, not argussight's proxy")
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--play", action="store_true")
    a = ap.parse_args()

    if a.play:
        play(url_for(a.name, a.direct), a.seconds)
        return

    names = [c["name"] for c in argus_cameras.CAMERAS] if a.all else [a.name]
    failed = []
    for name in names:
        if a.all:
            print(f"=== {name}")
        if not glimpse(name, url_for(name, a.direct), a.seconds or 5):
            failed.append(name)
    if failed:
        sys.exit(f"no video from: {', '.join(failed)}")


if __name__ == "__main__":
    main()
