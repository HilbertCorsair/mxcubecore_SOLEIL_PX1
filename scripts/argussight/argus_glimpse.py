#!/usr/bin/env python
"""Glimpse at what argussight streams on localhost.

    python argus_glimpse.py                  # 5 s of 'oav': data rate, a .ts file, one PNG frame
    python argus_glimpse.py --play           # live window (needs ffplay and an X display)
    python argus_glimpse.py --port 9000      # same check straight on the video-streamer
"""
import argparse
import asyncio
import shutil
import subprocess
import sys
import time

import websockets

MXCUBEWEB_BIN = "/home/experiences/proxima1/px1dev/miniconda3/envs/mxcubeweb/bin"


def tool(name):
    return shutil.which(name) or shutil.which(name, path=MXCUBEWEB_BIN)


async def pump(url, seconds, sink):
    total = msgs = 0
    async with websockets.connect(url, max_size=None, open_timeout=5) as ws:
        print(f"connected: {url}")
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            try:
                data = await asyncio.wait_for(ws.recv(), timeout=left)
            except asyncio.TimeoutError:
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="oav")
    ap.add_argument("--port", type=int, default=7000)
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--play", action="store_true")
    a = ap.parse_args()
    url = f"ws://127.0.0.1:{a.port}/ws/{a.name}"

    if a.play:
        ffplay = tool("ffplay") or sys.exit("ffplay not found: run without --play for a PNG")
        p = subprocess.Popen([ffplay, "-loglevel", "error", "-window_title", url,
                              "-f", "mpegts", "-i", "-"], stdin=subprocess.PIPE)
        try:
            asyncio.run(pump(url, a.seconds or 1e9, p.stdin))
        except (BrokenPipeError, KeyboardInterrupt):
            pass
        except Exception as e:
            print(f"FAILED: {url}: {type(e).__name__}: {e}")
        finally:
            p.terminate()
        return

    seconds = a.seconds or 5
    ts = f"/tmp/argus_{a.name}_{a.port}.ts"
    try:
        with open(ts, "wb") as f:
            total, msgs = asyncio.run(pump(url, seconds, f))
    except Exception as e:
        sys.exit(f"FAILED: {url}: {type(e).__name__}: {e}")

    if not total:
        sys.exit(f"connected, but NO video data in {seconds:.0f} s")
    print(f"{msgs} messages, {total / 1e3:.0f} kB in {seconds:.0f} s "
          f"= {total * 8 / seconds / 1e3:.0f} kbit/s -> {ts}")

    ffmpeg = tool("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found: no PNG, but the data above is the stream")
        return
    png = ts[:-3] + ".png"
    r = subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "mpegts", "-i", ts,
                        "-frames:v", "1", png])
    print(f"frame saved: {png}" if r.returncode == 0 else "ffmpeg could not decode a frame")


if __name__ == "__main__":
    main()