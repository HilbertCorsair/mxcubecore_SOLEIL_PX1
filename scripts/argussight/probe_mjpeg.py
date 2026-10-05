#!/usr/bin/env python
"""Check that video-streamer can read an MJPEG (hutch) camera, step by step.

Run it with the python that runs the streamers (the mxcubeweb env), so it uses
the video-streamer actually installed there:

    $ARGUS_STREAMER_PY probe_mjpeg.py                # every http camera in argus_cameras.CAMERAS
    $ARGUS_STREAMER_PY probe_mjpeg.py --name hutch_2
    $ARGUS_STREAMER_PY probe_mjpeg.py --uri http://172.19.11.119/mjpg/1/video.mjpg

It repeats what MJPEGCamera does before ffmpeg ever sees a byte: GET the URI,
find the multipart boundary, cut out the first frames, decode them to RGB24.
Each step that fails is one reason a streamer connects but sends no video.
"""
import argparse
import io
import os
import sys
import time

import requests

import argus_cameras

argus_cameras._strip_proxy(os.environ)


def probe(name, uri, seconds):
    print(f"=== {name}: {uri}")
    try:
        import video_streamer
        from video_streamer.core.camera import MJPEGCamera
    except Exception as e:
        print(f"FAIL  cannot import video_streamer in {sys.executable}: {e}")
        return False
    print(f"      video_streamer from {os.path.dirname(video_streamer.__file__)}")
    for meth in ("_extract_boundary", "_extract_frame", "_image_to_rgb24"):
        if not hasattr(MJPEGCamera, meth):
            print(f"FAIL  this video_streamer has no MJPEGCamera.{meth}: not the PX2 "
                  "fork (px2_video_streamer_v1.9.1); MJPEG -> MPEG1 cannot work")
            return False
    cam = MJPEGCamera.__new__(MJPEGCamera)  # skip __init__: its GET has no timeout

    try:
        r = requests.get(uri, stream=True, verify=False, timeout=5)
    except requests.RequestException as e:
        print(f"FAIL  GET: {type(e).__name__}: {e}")
        return False
    ctype = r.headers.get("Content-Type", "")
    print(f"      HTTP {r.status_code}, Content-Type: {ctype!r}")
    if r.status_code != 200:
        print("FAIL  the streamer needs 200 (401: camera wants -auth/-user/-pass)")
        return False
    boundary = cam._extract_boundary(r.headers)
    if not boundary:
        print("FAIL  no boundary= in Content-Type: the streamer logs "
              "'Boundary not found' and never starts")
        return False
    print(f"      boundary {boundary!r}")

    buf, frames, sizes, raw = bytearray(), 0, set(), 0
    end = time.monotonic() + seconds
    try:
        for chunk in r.iter_content(chunk_size=8192):
            if raw == 0:
                print(f"      first bytes: {bytes(chunk[:80])!r}")
            raw += len(chunk)
            buf.extend(chunk)
            while True:
                frame, buf = cam._extract_frame(buf, boundary)
                if frame is None:
                    break
                frames += 1
                from PIL import Image
                try:
                    w, h = Image.open(io.BytesIO(bytes(frame))).size
                    rgb = cam._image_to_rgb24(bytes(frame))
                except Exception as e:
                    print(f"FAIL  frame {frames} ({len(frame)} bytes, starts "
                          f"{bytes(frame[:4]).hex(' ')}) does not decode: "
                          f"{type(e).__name__}: {e}")
                    return False
                if len(rgb) != w * h * 3:
                    print(f"FAIL  frame {frames}: {len(rgb)} RGB bytes, expected {w}x{h}x3")
                    return False
                sizes.add((w, h))
            if time.monotonic() > end or len(buf) > 20_000_000:
                break
    except requests.RequestException as e:
        print(f"FAIL  reading the stream: {type(e).__name__}: {e}")
        return False
    finally:
        r.close()

    if not frames:
        print(f"FAIL  {raw} bytes in {seconds:.0f} s but no complete frame between two "
              f"{boundary!r} markers (or no blank line after the part headers): "
              "the streamer sizes itself from the first frame, so it never starts")
        return False
    if len(sizes) > 1:
        print(f"FAIL  frame size changes {sorted(sizes)}: ffmpeg's input size is fixed")
        return False
    (w, h), = sizes
    print(f"OK    {frames} frames in {seconds:.0f} s = {frames / seconds:.1f} fps, {w}x{h}")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="one camera from argus_cameras.CAMERAS")
    ap.add_argument("--uri", help="any MJPEG URL")
    ap.add_argument("--seconds", type=float, default=3)
    a = ap.parse_args()

    if a.uri:
        cams = [("uri", a.uri)]
    else:
        cams = [(c["name"], c["uri"]) for c in argus_cameras.CAMERAS
                if c["uri"].startswith("http") and a.name in (None, c["name"])]
        if not cams:
            sys.exit(f"no http camera named {a.name!r} in argus_cameras.CAMERAS")
    failed = [n for n, u in cams if not probe(n, u, a.seconds)]
    if failed:
        sys.exit(f"cannot stream: {', '.join(failed)}")


if __name__ == "__main__":
    main()
