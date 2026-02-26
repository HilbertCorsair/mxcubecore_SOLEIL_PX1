"""
Class for streaming MPEG1 or MJPEG video with cameras Redis Pub/Sub server
Example configuration:

<object class="RedisMpegVideo">
  <username>Camera redis</username>
  <host>localhost</host>
  <uri>redis://localhost:6379</uri>
  <cam_type>redis</cam_type>
  <port>8000</port>
  <width>1360</width>
  <height>1024</height>
  <format>MJPEG</format>
</object>
"""
import logging
import subprocess
import uuid
import psutil
from  PyTango import DeviceProxy
from PIL import Image
import io
import redis
import numpy as np
from mxcubecore.BaseHardwareObjects import HardwareObject
import logging
logger = logging.getLogger(__name__)


class RedisMpegVideo(HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self._video_stream_process = None
        self._current_stream_size = "0, 0"
        self.stream_hash = str(uuid.uuid1())
        self._quality_str = "High"
        self._QUALITY_STR_TO_INT = {"High": 4, "Medium": 10, "Low": 20, "Adaptive": -1}
        self._redis = None

    def init(self):
        super().init()
        self._last_image = None
        self._debug = self.get_property("debug", False)
        self._quality = self.get_property("compression", 10)
        self._mpeg_scale = self.get_property("mpeg_scale", 1)
        self._image_size = (self.get_width(), self.get_height() )
        self._uri = self.get_property("uri")
        self._host = self.get_property("host")
        self._port = str(self.get_property("port"))
        self._format = self.get_property("format")
        self.tangoname = self.get_property("tangoname")
        #self.crt = self.get_property("certifs")
        try:
            self._video_mode = self.get_property("video_mode", "RGB24")
            self.device = DeviceProxy(self.tangoname)
            # try a first call to get an exception if the device
            # is not exported
            self.device.ping()
        except Exception as e :
            logging.getLogger("HWR").error("%s: %s", str(self.name()), e)
        self.set_cam_redis()


    @property
    def uri(self):
        return self._uri

    @property
    def host(self):
        return self._host

    @property
    def format(self):
        return self._format

    @format.setter
    def format(self, format):
        self._format = format

    @property
    def port(self):
        return self._port

    @port.setter
    def port(self, p):
        self._port = str(p)

    def get_width(self):
        w= int(self.get_property("width"))
        return w
    def get_height(self):
        h= int(self.get_property("height"))
        return h

    def set_cam_redis(self):
        self._redis = self._uri.startswith("redis")

    def get_quality(self):
        return self._quality_str

    def set_quality(self, q):
        self._quality_str = q
        self._quality = self._QUALITY_STR_TO_INT[q]
        self.restart_streaming()

    def set_stream_size(self, w, h):
        self._current_stream_size = "%s,%s" % (int(w), int(h))

    def get_stream_size(self):
        current_size = self._current_stream_size.split(",")
        scale = float(current_size[0]) / self.get_width()
        return current_size + list((scale,))

    def get_quality_options(self):
        return list(self._QUALITY_STR_TO_INT.keys())

    def get_available_stream_sizes(self):
        try:
            w, h = self.get_width(), self.get_height()
            video_sizes = [(w, h), (int(w / 2), int(h / 2)), (int(w / 4), int(h / 4))]
        except (ValueError, AttributeError):
            video_sizes = []
        return video_sizes

    def start_video_stream_process(self):
        logger.info(f"STARTING ! Video stream on {self.host} port: {self.port} in format: {self.format}")

        if (
            not self._video_stream_process
            or self._video_stream_process.poll() is not None ):


            logger.info(f"VS PARAMS : uri {self.uri}\nhs {self.host}\n port {self.port} ")

            self._video_stream_process = subprocess.Popen(
                [
                    "video-streamer",
                    "-uri",
                    self.uri,
                    "-hs",
                    self.host,
                    "-p",
                    self.port,
                    #"-crt",
                    #self.crt,
                    "-q",
                    str(self._quality),
                    "-s",
                    self._current_stream_size,
                    "-of",
                    self.format,
                    "-id",
                    self.stream_hash,
                    "-irc",
                    "mxcubeweb"
                ],
                close_fds=True,
            )
            with open("/tmp/mxcube.pid", "a") as f:
                f.write("%s " % self._video_stream_process.pid)\

    def poll_image(self): #, device, video_mode, FORMATS):
        if self._redis:
            try:
                r = redis.Redis(host="195.221.8.84", port=6379, decode_responses=False)
                latest_frame = r.lrange("mxcubeweb", 0, 0)
                if not latest_frame:
                    logger.info("NO FRAMES")
                    return None

                # Assuming the frame data is stored directly as bytes
                frame_data = latest_frame[0]  # Just take the first element

                # Add validation for frame_data
                if not frame_data:
                    return None

                try:
                    image = Image.open(io.BytesIO(frame_data))
                except IOError as img_error:
                    logger.exception(f"Error opening image data: {str(img_error)}")
                    return None

                frame_rgb = np.array(image.convert('RGB'))
                return frame_rgb

            except redis.ConnectionError as conn_err:
                logger.exception(f"Redis connection error: {str(conn_err)}")
                return None
            except redis.RedisError as redis_err:
                logger.exception(f"Redis error: {str(redis_err)}")
                return None
            except Exception as e:
                logger.exception(f"Unexpected error: {str(e)}")
                return None


        """
        img_data = device.video_last_image

        hfmt = ">IHHqiiHHHH"
        hsize = struct.calcsize(hfmt)
        _, _, img_mode, frame_number, width, height, _, _, _, _ = struct.unpack(
            hfmt, img_data[1][:hsize]
        )

        raw_data = img_data[1][hsize:]
        _from, _to = FORMATS.get(video_mode, (None, None))

        if _from and _to:
            img = Image.frombuffer(_from, (height, width), raw_data, "raw", _from, 0, 1)

            img_bytes = io.BytesIO()
            img.save(img_bytes, format=_to)
            img = img.tobytes()
        else:
            img = raw_data
        return img, width, height
        """

    def get_last_image(self):
        rgb = self.poll_image()
        return rgb, self.get_width(), self.get_height()


    def stop_streaming(self):
        if self._video_stream_process:
            ps = psutil.Process(self._video_stream_process.pid).children() + [
                self._video_stream_process
            ]

            for p in ps:
                p.kill()

            self._video_stream_process = None

    def start_streaming(self, _format=None, size=(0, 0), port = None):

        _s = size

        if _format:
            self.format = _format

        if port:
            self.port = port

        if not size[0]:
            _s = (self.get_width(), self.get_height())
        else:
            _s = size

        self.set_stream_size(_s[0], _s[1])
        try:
            self.start_video_stream_process()
        except Exception as e:
            print(f"Cannot start video streaming process ! {e}")

    def restart_streaming(self, size):
        self.stop_streaming()
        self.start_streaming(self.format, size)