"""
Class for streaming MPEG1 video with cameras connected to
Lima Tango Device Servers

Example configuration:

<device class="TangoLimaMpegVideo">
  <username>Prosilica 1350C</username>
  <tangoname>id23/limaccd/minidiff</tangoname>
  <bpmname>id23/limabeamviewer/minidiff</bpmname>
  <exposure_time>0.05</exposure_time>
  <video_mode>RGB24</video_mode>
</device>
"""
import os
import subprocess
import uuid
import psutil

from mxcubecore.BaseHardwareObjects import HardwareObject

class RedisMpegVideo(HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self._video_stream_process = None
        self._current_stream_size = "0, 0"
        self.stream_hash = str(uuid.uuid1())
        self._quality_str = "High"
        self._QUALITY_STR_TO_INT = {"High": 4, "Medium": 10, "Low": 20, "Adaptive": -1}

    def init(self):
        super().init()
        self._debug = self.get_property("debug", False)
        self._quality = self.get_property("compression", 10)
        self._mpeg_scale = self.get_property("mpeg_scale", 1)
        self._image_size = (self.get_width(), self.get_height())
        self._host = self.get_property("host")
        self._port = str(self.get_property("port"))
        self._format = self.get_property("format")


        # self._cam_type = self.get_property("cam_type")
        #import pdb
        #print("Check 1")
        #pdb.set_trace()
    
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

    def start_video_stream_process(self, p):
        print(f"STARTING ! Video stream on port: {self.port} in format: {self.format}")

        # first get the format from the xml file since it is MJPEG by default
        if (
            not self._video_stream_process
            or self._video_stream_process.poll() is not None ):
            #print ("~~~ Video Streamer ~~~")
            #print(f"Type of camerra: {self.get_property("cam_type").strip()}\nURI : {self._host}\nport: {self._port}")
            #exit()
            self._video_stream_process = subprocess.Popen(
                [  
                    "video-streamer",
                    "-uri",
                    "redis://195.221.8.84:6379",
                    "-hs",
                    "localhost",
                    "-p",
                    self.port,
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
                    # "-d",
                ],
                close_fds=True,
            )
            with open("/tmp/mxcube.pid", "a") as f:
                f.write("%s " % self._video_stream_process.pid)

    def stop_streaming(self):
        if self._video_stream_process:
            ps = psutil.Process(self._video_stream_process.pid).children() + [
                self._video_stream_process
            ]

            for p in ps:
                p.kill()

            self._video_stream_process = None

    def start_streaming(self, _format=None, size=(0, 0), port=None):
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
            self.start_video_stream_process(str(self.port))
        except Exception as e:
            print(f"Cannot start video streaming process ! {e}")
            exit()

    def restart_streaming(self, size):
        self.stop_streaming()
        self.start_streaming(self.format, size)