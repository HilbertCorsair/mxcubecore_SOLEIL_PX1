#!/usr/bin/env python3
"""Gate MXCuBE startup on the PROXIMA-1 camera actually publishing frames.

At PX1 the camera publisher (``redis_camera2.py``) runs on a **different
server** from MXCuBE, so neither this script nor argussight can start it. All we
can do is check whether frames are arriving and, if not, stop and ask the
operator to start the camera.

Why this matters rather than just letting things fail later: video-streamer's
``RedisCamera`` blocks in ``_set_size`` until the first frame arrives, and it
does so inside FastAPI's startup handler -- so with no publisher the OAV
streamer never binds its port and MXCuBE comes up with a dead video pane and no
explanation.

Behaviour (see start_argus_px1.sh, which runs this first):

  * frames seen              -> exit 0, the stack starts
  * no frames -> OK/Cancel dialog
      - Cancel               -> exit CANCELLED (2), MXCuBE never starts
      - OK, frames now       -> exit 0
      - OK, still no frames  -> raise RuntimeError, exit NO_FRAMES (3)

The dialog degrades gracefully: zenity if there is a DISPLAY, else tkinter,
else a plain stdin prompt (so it still works over a bare ssh session).
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger("check_frames")

DEFAULT_URI = "redis://195.221.8.84:6379"
DEFAULT_CHANNEL = "mxcubeweb"
DEFAULT_TIMEOUT = 20.0

EXIT_OK = 0
EXIT_CANCELLED = 2
EXIT_NO_FRAMES = 3

PROMPT_TITLE = "MXCuBE - camera not running"
PROMPT_TEXT = (
    "You need to start the redis camera first.\n\n"
    "No video frames are arriving on the Redis channel, so MXCuBE would start "
    "with no image and no click-to-centre.\n\n"
    "Start the camera on the camera server, then press OK.\n"
    "Cancel will quit MXCuBE."
)


def wait_for_frames(uri, channel, timeout):
    """Return True once a frame is seen on ``channel``, else False after ``timeout``.

    A frame counts when the message carries a non-empty ``data`` field, matching
    what ``redis_camera2.py`` publishes (``{"data": <b64 jpeg>, "size": [h, w]}``)
    and what video-streamer's ``RedisCamera.poll_image`` requires. Messages that
    are not JSON are accepted too: some publishers push the raw JPEG bytes, and
    refusing those here would block a working camera.
    """
    import redis

    client = redis.from_url(uri, decode_responses=False)
    pubsub = client.pubsub()
    pubsub.subscribe(channel)
    # Deadline from a monotonic clock: this runs at beamline startup, where an
    # NTP step on the wall clock could otherwise shorten or hang the wait.
    from time import monotonic

    deadline = monotonic() + timeout
    try:
        while monotonic() < deadline:
            message = pubsub.get_message(timeout=1.0)
            if not message or message["type"] != "message":
                continue
            payload = message["data"]
            if not payload:
                continue
            try:
                frame = json.loads(payload)
            except (ValueError, TypeError):
                return True
            if isinstance(frame, dict) and frame.get("data"):
                return True
            if not isinstance(frame, dict):
                return True
    finally:
        try:
            pubsub.close()
        except Exception:
            logger.debug("ignoring error while closing pubsub", exc_info=True)
    return False


def _ask_zenity():
    zenity = shutil.which("zenity")
    if not zenity or not os.environ.get("DISPLAY"):
        return None
    try:
        completed = subprocess.run(
            [
                zenity,
                "--question",
                "--title", PROMPT_TITLE,
                "--text", PROMPT_TEXT,
                "--ok-label", "OK",
                "--cancel-label", "Cancel",
                "--width", "420",
            ],
            check=False,
        )
    except OSError:
        logger.debug("zenity failed", exc_info=True)
        return None
    return completed.returncode == 0


def _ask_tkinter():
    if not os.environ.get("DISPLAY"):
        return None
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        try:
            return bool(messagebox.askokcancel(PROMPT_TITLE, PROMPT_TEXT))
        finally:
            root.destroy()
    except Exception:
        logger.debug("tkinter dialog failed", exc_info=True)
        return None


def _ask_stdin():
    if not sys.stdin or not sys.stdin.isatty():
        # Nobody can answer -- treat it as Cancel rather than hanging a
        # detached startup forever.
        logger.error("no way to prompt (not a tty and no DISPLAY); treating as Cancel")
        return False
    print("\n%s\n%s\n" % (PROMPT_TITLE, PROMPT_TEXT), file=sys.stderr)
    try:
        answer = input("Press Enter/OK to continue, or type 'c' to cancel: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return not answer.strip().lower().startswith("c")


def ask_operator():
    """Show the OK/Cancel prompt. True for OK, False for Cancel."""
    for ask in (_ask_zenity, _ask_tkinter):
        answer = ask()
        if answer is not None:
            return answer
    return _ask_stdin()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uri", default=DEFAULT_URI, help="Redis URI carrying the frames")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL, help="pub/sub channel to watch")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help="seconds to wait for a frame on each attempt",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    logger.info("waiting up to %ss for frames on %s (%s) ...", args.timeout, args.channel, args.uri)
    if wait_for_frames(args.uri, args.channel, args.timeout):
        logger.info("frames are arriving; continuing")
        return EXIT_OK

    logger.warning("no frames on %r -- asking the operator to start the camera", args.channel)
    if not ask_operator():
        logger.error("cancelled by the operator; MXCuBE will not start")
        return EXIT_CANCELLED

    logger.info("re-checking for frames on %r ...", args.channel)
    if wait_for_frames(args.uri, args.channel, args.timeout):
        logger.info("frames are arriving; continuing")
        return EXIT_OK

    raise RuntimeError(
        "Still no frames on Redis channel %r at %s after %ss. "
        "Start the camera publisher on the camera server and try again."
        % (args.channel, args.uri, args.timeout)
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(EXIT_NO_FRAMES)
