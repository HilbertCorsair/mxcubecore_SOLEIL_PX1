"""MPEG1/MJPEG camera backed by a Redis frame buffer.

The camera object owns two things:

1. A snapshot path used by `SampleView.take_snapshot`: the latest frame is
   pulled from Redis and returned as raw RGB bytes via :meth:`get_last_image`.
2. A live video path: :meth:`start_streaming` launches the standalone
   ``video-streamer`` process (https://github.com/mxcube/video-streamer)
   which reads the same Redis source and serves it over HTTP/WebSocket
   for the browser.

All connection parameters come from the YAML/XML configuration; nothing is
hardcoded.

Example configuration::

    <object class="RedisMpegVideo">
      <username>Camera redis</username>
      <uri>redis://195.221.8.84:6379</uri>
      <host>localhost</host>
      <port>8000</port>
      <format>MPEG1</format>
      <width>1360</width>
      <height>1024</height>
      <quality>10</quality>
      <redis_key>mxcubeweb</redis_key>
    </object>

``redis_key`` names both the pub/sub channel the video-streamer reads (``-irc``)
and the snapshot key/list.  ``width``/``height`` must be the *native* camera
resolution, because ``pixelsPerMm`` is calibrated per native pixel.
"""

import atexit
import io
import logging
import os
import signal
import subprocess
import uuid
from typing import (
    List,
    Optional,
    Tuple,
)

import psutil
import redis
from PIL import Image

from mxcubecore import BaseHardwareObjects

logger = logging.getLogger("HWR")

# Key under which the PROXIMA-1 camera publisher (``redis_camera2.py``, running
# on the camera server) stores the latest frame as raw, ravelled RGB bytes.
PX1_SNAPSHOT_KEY = "last_image_data"


class RedisMpegVideo(BaseHardwareObjects.HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self.stream_hash = str(uuid.uuid4())
        self._video_stream_process = None
        self._current_stream_size = (0, 0)
        self._format = "MPEG1"
        self._port = "8000"
        self._uri = None
        self._host = None
        self._width = 0
        self._height = 0
        self._quality = 10
        self._redis_key = "mxcubeweb"
        self._redis = None

    def init(self):
        super().init()
        self._uri = self.get_property("uri")
        self._host = self.get_property("host")
        self._port = str(self.get_property("port", 8000))
        self._format = self.get_property("format", "MPEG1")
        self._width = int(self.get_property("width"))
        self._height = int(self.get_property("height"))
        self._quality = int(self.get_property("quality", 10))
        self._redis_key = self.get_property("redis_key", "mxcubeweb")
        self._current_stream_size = (self._width, self._height)
        self.update_state(BaseHardwareObjects.HardwareObjectState.READY)

    @property
    def uri(self):
        return self._uri

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._port

    @port.setter
    def port(self, value):
        self._port = str(value)

    @property
    def format(self):
        return self._format

    @format.setter
    def format(self, value):
        self._format = value

    def get_width(self) -> int:
        return self._width

    def get_height(self) -> int:
        return self._height

    def get_available_stream_sizes(self) -> List[Tuple[int, int]]:
        w, h = self._width, self._height
        return [(w, h), (w // 2, h // 2), (w // 4, h // 4)]

    def set_stream_size(self, w, h) -> None:
        self._current_stream_size = (int(w), int(h))

    def get_stream_size(self) -> Tuple[int, int, float]:
        w, h = self._current_stream_size
        scale = float(w) / self._width if self._width else 1.0
        return (w, h, scale)

    def _get_redis(self):
        if self._redis is None and self._uri:
            self._redis = redis.from_url(self._uri, decode_responses=False)
        return self._redis

    def _frame_from_list(self, client) -> Optional[Tuple[bytes, int, int]]:
        """Latest JPEG frame from the Redis list written by ``LPUSH``.

        This is the PROXIMA-2 publisher's snapshot path.
        """
        frames = client.lrange(self._redis_key, 0, 0)
        if not frames or not frames[0]:
            return None
        image = Image.open(io.BytesIO(frames[0])).convert("RGB")
        return image.tobytes(), image.width, image.height

    def _frame_from_key(self, client) -> Optional[Tuple[bytes, int, int]]:
        """Latest frame from the plain key written by PROXIMA-1's publisher.

        ``redis_camera2.py`` stores ``img.ravel()`` — already raw RGB bytes at
        the native resolution — under ``last_image_data``, and never LPUSHes.
        A size mismatch means the camera is configured for a different
        resolution than this object, which would render skewed; refuse it.
        """
        raw = client.get(PX1_SNAPSHOT_KEY)
        if not raw:
            return None

        expected = self._width * self._height * 3
        if len(raw) != expected:
            logger.warning(
                "RedisMpegVideo: %s holds %d bytes, expected %d for %dx%d RGB;"
                " ignoring it",
                PX1_SNAPSHOT_KEY,
                len(raw),
                expected,
                self._width,
                self._height,
            )
            return None

        return bytes(raw), self._width, self._height

    def get_last_image(self) -> Tuple[bytes, int, int]:
        """Fetch the latest frame from Redis and return raw RGB bytes.

        Two publishers are supported, because PROXIMA-1 and PROXIMA-2 fill
        Redis differently and either may be running: the LPUSHed JPEG list
        first, then PROXIMA-1's raw ``last_image_data`` key.  Returns a black
        RGB frame at the configured size when Redis is unreachable or holds
        neither, so snapshot/queue callers don't crash off-site.
        """
        try:
            client = self._get_redis()
            if client is not None:
                for source in (self._frame_from_list, self._frame_from_key):
                    try:
                        frame = source(client)
                    except Exception:
                        logger.exception(
                            "RedisMpegVideo: %s failed", source.__name__
                        )
                        continue
                    if frame is not None:
                        return frame
                logger.warning(
                    "RedisMpegVideo: no frame in Redis (neither the '%s' list"
                    " nor the '%s' key); is the camera publisher running?",
                    self._redis_key,
                    PX1_SNAPSHOT_KEY,
                )
        except Exception:
            logger.exception("RedisMpegVideo: could not reach Redis")

        w, h = self._width, self._height
        return b"\x00" * (w * h * 3), w, h

    def start_streaming(self, _format="MPEG1", size=(0, 0), port=None) -> None:
        self._format = _format
        if port is not None:
            self._port = str(port)
        if size and size[0]:
            self.set_stream_size(int(size[0]), int(size[1]))
        elif self._current_stream_size == (0, 0):
            self.set_stream_size(self._width, self._height)
        self.start_video_stream_process()

    def start_video_stream_process(self) -> None:
        if self._video_stream_process and self._video_stream_process.poll() is None:
            return

        size_arg = "%d,%d" % self._current_stream_size
        logger.info(
            "RedisMpegVideo: starting video-streamer on %s:%s (%s, %s)",
            self._host,
            self._port,
            self._format,
            size_arg,
        )
        self._video_stream_process = subprocess.Popen(
            [
                "video-streamer",
                "-uri", self._uri,
                "-hs", self._host,
                "-p", self._port,
                "-q", str(self._quality),
                "-s", size_arg,
                "-of", self._format,
                "-id", self.stream_hash,
                "-irc", self._redis_key,
            ],
            close_fds=True,
            stdout=subprocess.DEVNULL,
        )
        atexit.register(self.stop_streaming)

    def stop_streaming(self) -> None:
        if not self._video_stream_process:
            return
        try:
            parent = psutil.Process(self._video_stream_process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        except Exception:
            logger.exception("RedisMpegVideo: error stopping video-streamer")
            try:
                os.kill(self._video_stream_process.pid, signal.SIGTERM)
            except OSError:
                pass
        self._video_stream_process = None

    def restart_streaming(self, size) -> None:
        self.stop_streaming()
        self.start_streaming(_format=self._format, size=size, port=self._port)
