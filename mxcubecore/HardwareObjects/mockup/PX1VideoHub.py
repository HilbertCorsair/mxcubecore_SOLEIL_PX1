

from GenericVideoDevice import GenericVideoDevice
from QtImport import QImage, QPixmap, Qt

from matplotlib.figure import Figure
from matplotlib.backends.backend_qt4agg import FigureCanvasQTAgg as FigureCanvas
import io

import gevent
import ctypes
import cv2
import os

import logging
import time
from datetime import datetime

log = logging.getLogger("HWR")

class PX1VideoHub(GenericVideoDevice):
    def __init__(self, *args):
        GenericVideoDevice.__init__(self, *args)

        self.selected_camera = "oav"
        self.selection_pending = False
        self.oav_hold_process = None
        self.oav_hold_time = 0
        #self.camno = None
        self.cap = None
        self.video_recording = False
        self.recording_allowed = False

        self.camx_mode = None # decides whether start/stop is handled here or in Robin's code

        self._proxy_removed = False

        if "http_proxy" in os.environ:
            self.saved_proxy = os.environ["http_proxy"]
            self.restore_proxy = self._restore_proxy
            self.remove_proxy = self._remove_proxy
        else:
            self.no_proxy = True
            self.restore_proxy = self._do_nothing
            self.remove_proxy = self._do_nothing

    def init(self):
        self.oav_camera = self.getObjectByRole("oav_camera")
        self.camx_camera = self.getObjectByRole("camx_camera")
        self.session_hwo = self.getObjectByRole("session")

        self.video_ip = self.getProperty("video_ip")
        self.image_path = self.getProperty("video_url")
        self.no_cams = self.getProperty("video_no_cams")

        self.base_url = self.image_path.format(camip=self.video_ip)

        self.robot_camno = self.getProperty("video_robot", 1)
        self.dewar_camno = self.getProperty("video_dewar", 2)
        self.top_camno = self.getProperty("video_top", 3)
        self.head_camno = self.getProperty("video_head", 4)

        self.only_oav = self.getProperty("oav_only", True)
        self.recording_allowed = self.getProperty("recording_enabled",False)

        self.image_dimensions = self.oav_camera.image_dimensions
        GenericVideoDevice.init(self)

        if self.camx_camera is not None:
            self.init_nparray_display()

    
    def init_nparray_display(self):
        self.np_first = True
        self.mpl_fig = Figure()
        self.mpl_fig.set_size_inches(8,8) 
        self.mpl_canvas = FigureCanvas(self.mpl_fig)
        self.mpl_ax = self.mpl_fig.add_subplot(111)
        self.mpl_ax.get_xaxis().set_visible(False)
        self.mpl_ax.get_yaxis().set_visible(False)

    def do_image_polling(self, sleep_time=None):

        while True: 
           #if self.selection_pending:
               #self._do_select_video()
               #self.selection_pending = False

           if self.selected_camera not in ['oav', 'camx']:
               self.qimage = self.read_video_image()
               if self.qimage is not None:
                   self.emit("imageReceived", QPixmap(self.qimage))
           else:
               if self.selected_camera == 'oav':
                   img = self.oav_camera.get_last_image()
                   self.qimage = QImage(img, self.image_dimensions[0], \
                      self.image_dimensions[1], QImage.Format_RGB888)
                   self.emit("imageReceived", QPixmap(self.qimage))
               else: # camx
                   img = self.camx_camera.get_image()
                   self.qimage = self.qimage_from_nparray(img)
                   # img is a numpy 2d array
                   self.emit("imageReceived", QPixmap(self.qimage))

           gevent.sleep(0.04)

    def is_only_oav(self):
        return self.only_oav

    def select_camera(self, camera, process=None):
        camera = camera.lower()

        log.debug("PX1VideoHub - selecting camera:  %s" % camera)

        if self.only_oav:
            log.debug("PX1VideoHub - only_oav option selected.  Cannot change camera.")
            self.selected_camera = 'oav'
            return

        if camera == self.selected_camera:
            log.debug("PX1VideoHub. selecting camera %s. but is already selected")
            return
 
        ignore = False

        try:
            if process is not None:
                # this logic avoids a request from another process
                # to restore oav
    
                # meant for unmount / mount not selecting oav on mounting failure
                # due to collision. as collision process does its own selection
    
                # hold control of oav for 5 seconds
                if camera != 'oav':  # take hold always if camera not oav
                    # process get hold of oav camera
                    log.debug("PX1VideoHub - process %s getting hold of oav selection" % process)
                    self.oav_hold_time = time.time()
                    self.oav_hold_process = process
                    ignore = False
                else: 
                    if (time.time() - self.oav_hold_time > 5.0):  
                        log.debug("PX1VideoHub - oav selection hold released after 5 seconds")
                        self.oav_hold_process = None
                        ignore = False
                    elif self.oav_hold_process and process != self.oav_hold_process:
                        log.debug("PX1VideoHub - oav selection hold by %s.refusing oav selection to %s" % \
                          (self.oav_hold_process, process))
                        ignore = True
                    else:  # within 5 seconds and same process or not hold
                        ignore = False
            else:
                # remove hold if manual selection of any camera
                log.debug("PX1VideoHub - manual camera select. oav holding released")
                self.oav_hold_process = None
                ignore = False
        except:
            import traceback
            log.debug("PX1VideoHub - Error on oav hold logic.")
            log.debug(traceback.format_exc())

        if ignore is True:
            log.debug("PX1VideoHub. Trying to select %s from process (%s). But OAV selection on hold by %s" % (process, self.oav_hold_process))
            log.debug("PX1VideoHub. oav selection ignored")
            return

        self.to_select = None
        self.selection_pending = True

        if camera == 'robot':
            camno = int(self.robot_camno)
            self.to_select = camno
        elif camera == 'dewar':
            camno = int(self.dewar_camno)
            self.to_select = camno
        elif camera == 'top':
            camno = int(self.top_camno)
            self.to_select = camno
        elif camera == 'head':
            camno = int(self.head_camno)
            self.to_select = camno
        elif camera in ['camx', 'camx_ma'] :
            self.check_camx_start(camera)
            camera = "camx"
        else:
            camera = "oav"

        log.debug("PX1VideoCamera - camera changed to %s" % camera)
        self.emit("cameraChanged", camera)
        self.check_camx_stop(camera)
        self.selected_camera = camera

    def start_recording(self, filename=None, file_prefix=None):
        if not self.recording_allowed: 
            return

        if filename:
            self.video_output_file = filename
        else:
            base_video_dir = self.session_hwo.get_video_directory()
            d = datetime.now().strftime("%Y_%m_%d-%H_%M_%S")
            if file_prefix is not None:
                name = "%s_%s" % (self.selected_camera,file_prefix)
            else:
                name = self.selected_camera
            filename = "mxcube_video_%s_%s.mpg" % (name, d)
            self.video_output_file = os.path.join(base_video_dir, filename)

        if self.video_recording:
            self.stop_recording()

        try:
            vid_cod = cv2.cv.CV_FOURCC(*'MJPG')
            self.video_output = cv2.VideoWriter(self.video_output_file, vid_cod, 18.0, (640,360))
            self.video_recording = True
        except BaseException as e:
            import traceback
            log.debug("PX1VideoHub - video recording failed (%s)" % traceback.format_exc())
            self.video_recording = False

    def stop_recording(self):
        if not self.recording_allowed: 
            return

        self.video_recording = False
        self.video_output.release()

    def check_camx_start(self, camera):
        
        if camera == 'camx': # this is manual selection from combobox
            self.camx_camera.start() 

        # otherwise is camx_ma - morning alignment does not take care of start/stop 
        #   it is  done elsewhere by Robin's code

        self.camx_mode = camera

    def check_camx_stop(self, camera):
        if camera != 'camx' and self.camx_mode == 'camx':
            self.camx_camera.stop() 
    
    # this runs inside update thread
    def _do_select_video(self):

        gevent.sleep(0.05)

        # if there is a video capture going on. close it
        if self.cap is not None: 
            log.debug("PX1VideoHub - releasing %s video capture " % self.selected_camera)
            self.cap.release()         
            self.cap = None

        gevent.sleep(0.05)

        # if the selection is a video source
        if self.to_select is not None:
            url = self.base_url.format(camno=self.to_select)
            log.debug("PX1VideoHub. Camera url is: %s" % url)
            self.cap = cv2.VideoCapture(url)
            log.debug("PX1VideoHub.   - camera no %s selected. isOpened: %s" % (self.to_select, self.cap.isOpened()))

        gevent.sleep(0.05)

    # this runs inside update thread
    def read_video_image(self):

        self.remove_proxy()

        if self.selection_pending:
            self._do_select_video()
            self.selection_pending = False

        if self.cap is not None: 
            if self.cap.isOpened(): 
               ret, frame = self.cap.read()

        self.restore_proxy()

        if ret:
             if self.video_recording: # resize to half the size
                 b = cv2.resize(frame,(640,360),fx=0,fy=0, interpolation = cv2.INTER_CUBIC)
                 self.video_output.write(b)
    
             image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
             h, w, ch = image.shape

             bytesPerLine = ch * w

             ptr = ctypes.c_char.from_buffer(image.data, 0)
             rcount = ctypes.c_long.from_address(id(ptr)).value
             qimg = QImage(ptr, w, h, bytesPerLine, QImage.Format_RGB888)
             ctypes.c_long.from_address(id(ptr)).value = rcount

             img = qimg.scaled(self.image_dimensions[0], self.image_dimensions[1], Qt.KeepAspectRatio)
             return img 
        else:
             return None

    def qimage_from_nparray(self, data):
        self.mpl_ax.clear()
        self.mpl_ax.imshow(data)
        buf = io.BytesIO()
        if self.np_first:
            self.mpl_fig.tight_layout()
            self.np_first = False

        #  png is too slow but tif leaves a small frame
        #self.mpl_fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0)

        self.mpl_fig.savefig(buf, format='tif', pad_inches=0)
        buf.seek(0)
        qimg = QImage()
        qimg.loadFromData(buf.read())
        return qimg

    def _do_nothing(self):
        pass

    def _remove_proxy(self):
        if self._proxy_removed:
            return

        try:
            del os.environ["http_proxy"]
            self._proxy_removed = True
        except:
            pass

    def _restore_proxy(self):
        if self._proxy_removed:
            os.environ["http_proxy"] = self.saved_proxy
            self._proxy_removed = False
