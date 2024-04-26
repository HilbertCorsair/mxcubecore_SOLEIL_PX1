#!/usr/bin/env python
# -*- coding: utf-8 -*-

from scipy.misc import imsave

import time
import os
import sys
import numpy as np
import PyTango
import logging
import gevent

import redis
from pymba import *

class Camera(object):
    def __init__(self,
                 camera_type='prosilica',
                 y_pixels_in_detector=1024,
                 x_pixels_in_detector=1360,
                 channels=3,
                 default_exposure_time=0.05,
                 default_gain=8.,
                 pixel_format='RGB8Packed',
                 tango_address='i11-ma-cx1/ex/imag.1',
                 use_redis=True):

        self.y_pixels_in_detector = y_pixels_in_detector
        self.x_pixels_in_detector = x_pixels_in_detector
        self.channels = channels
        self.default_exposure_time = default_exposure_time
        self.current_exposure_time = None
        self.default_gain = default_gain
        self.current_gain = None
        self.pixel_format=pixel_format
        self.use_redis = use_redis
        if use_redis == True:
            self.camera = None
            self.redis = redis.StrictRedis()
        else:
            self.camera = PyTango.DeviceProxy(tango_address)

        self.camera_type = camera_type
        self.shape = (y_pixels_in_detector, x_pixels_in_detector, channels)

        self.focus_offsets = \
           {1: -0.0819,
            2: -0.0903,
            3: -0.1020,
            4: -0.1092,
            5: -0.1098,
            6: -0.1165,
            7: -0.1185,
            8: -0.1230,
            9: -0.1213,
            10: -0.1230}

        self.zoom_motor_positions = \
           {1: 34500.0,
            2: 31165.0,
            3: 27185.0,
            4: 23205.0,
            5: 19225.0,
            6: 15245.0,
            7: 11265.0,
            8: 7285.0,
            9: 3305.0,
            10: 0.0 }

        self.backlight = \
           {1: 8.5,
            2: 9.8,
            3: 10.2,
            4: 11.6,
            5: 15.0,
            6: 20.8,
            7: 28.8,
            8: 41.4,
            9: 48.8,
            10: 100.0}

        self.gain = \
           {1: 9,
            2: 9,
            3: 9,
            4: 9,
            5: 9,
            6: 9,
            7: 9,
            8: 9,
            9: 9,
            10: 7}

        self.calibrations = \
           {1: np.array([ 0.0016026, 0.0016051]),
            2: np.array([ 0.0012821, 0.0012739]),
            3: np.array([ 0.0009816, 0.00097927]),
            4: np.array([ 0.0007511, 0.0007503]),
            5: np.array([ 0.0005741, 0.0005714]),
            6: np.array([ 0.0004399, 0.000438 ]),
            7: np.array([ 0.0003348, 0.0003371]),
            8: np.array([ 0.0002582,  0.0002573]),
            9: np.array([ 0.0001963,  0.0001971]),
            10: np.array([ 0.00016108,  0.00015746])}

        self.magnifications = np.array([np.mean(self.calibrations[1]/self.calibrations[k]) for k in range(1, 11)])
        self.master = False

    def get_point(self):
        return self.get_image()

    def get_image(self, color=True):
        if color:
            return self.get_rgbimage()
        else:
            return self.get_bwimage()

    def get_image_id(self):
        if self.use_redis:
            image_id = self.redis.get('last_image_id')
        else:
            image_id = self.camera.imagecounter
        return image_id

    def get_rgbimage(self):
        if self.use_redis:
            data = self.redis.get('last_image_data')
            rgbimage = np.ndarray(buffer=data, dtype=np.uint8, shape=(1024, 1360, 3))
        else:
            rgbimage = self.camera.rgbimage.reshape((self.shape[0], self.shape[1], 3))
        return rgbimage

    def get_bwimage(self):
        rgbimage = self.get_rgbimage()
        return rgbimage.mean(axis=2)

    def save_image(self, imagename, color=True):
        if color:
            image_id, image = self.get_image_id(), self.get_rgbimage()
        else:
            image_id, image = self.get_image_id(), self.get_bwimage()
        imsave(imagename, image)
        return imagename, image, image_id

    def get_calibration(self):
        return np.array([self.get_vertical_calibration(), self.get_horizontal_calibration()])

    def get_vertical_calibration(self):
        return self.goniometer.md2.coaxcamscaley

    def get_horizontal_calibration(self):
        return self.goniometer.md2.coaxcamscalex

    def set_exposure(self, exposure=0.05):
        if not (exposure >= 3.e-6 and exposure<3):
            print('specified exposure time is out of the supported range (3e-6, 3)')
            return -1
        if not self.use_redis:
            self.camera.exposure = exposure
        if self.master:
            self.camera.ExposureTimeAbs = exposure * 1.e6
        self.redis.set('camera_exposure_time', exposure)
        self.current_exposure_time = exposure

    def get_exposure(self):
        if not self.use_redis:
            exposure = self.camera.exposure
        if self.master:
            exposure = self.camera.ExposureTimeAbs/1.e6
        else:
            exposure = float(self.redis.get('camera_exposure_time'))

    def set_exposure_time(self, exposure_time):
        self.set_exposure(exposure_time)

    def get_exposure_time(self):
        if not self.use_redis:
            return self.get_exposure()

    def get_gain(self):
        if not self.use_redis:
            gain = self.camera.gain
        elif self.master:
            gain = self.camera.GainRaw
        else:
            gain = float(self.redis.get('camera_gain'))
        return gain

    def set_gain(self, gain):
        if not (gain >= 0 and gain <=24):
            print('specified gain value out of the supported range (0, 24)')
            return -1
        if not self.use_redis:
            self.camera.gain = gain
        elif self.master:
            self.camera.GainRaw = int(gain)
        self.redis.set('camera_gain', gain)
        self.current_gain = gain

    def set_frontlightlevel(self, frontlightlevel):
        self.goniometer.md2.frontlightlevel = frontlightlevel

    def get_frontlightlevel(self):
        return self.goniometer.md2.frontlightlevel

    def set_backlightlevel(self, backlightlevel):
        self.goniometer.md2.backlightlevel = backlightlevel

    def get_backlightlevel(self):
        return self.goniometer.md2.backlightlevel

    def get_width(self):
        return self.x_pixels_in_detector

    def get_height(self):
        return self.y_pixels_in_detector

    def get_image_dimensions(self):
        return [self.get_width(), self.get_height()]

    def run_camera(self):
        self.master = True

        vimba = Vimba()
        system = vimba.getSystem()
        vimba.startup()

        if system.GeVTLIsPresent:
            system.runFeatureCommand("GeVDiscoveryAllOnce")
            gevent.sleep(3)

        cameraIds = vimba.getCameraIds()
        print('cameraIds %s' % cameraIds)
        self.camera = vimba.getCamera(cameraIds[0])
        self.camera.openCamera()
        self.camera.PixelFormat = self.pixel_format

        self.frame0 = self.camera.getFrame()    # creates a frame
        self.frame0.announceFrame()

        self.image_dimensions = (self.frame0.width, self.frame0.height)

        self.set_exposure(self.default_exposure_time)
        self.set_gain(self.default_gain)

        self.current_gain = self.get_gain()
        self.current_exposure_time = self.get_exposure_time()


        self.camera.startCapture()

        self.camera.runFeatureCommand("AcquisitionStart")

        k = 0
        last_frame_id = None
        _start = time.time()
        while self.master:
            self.frame0.waitFrameCapture()
            try:
                self.frame0.queueFrameCapture()
            except:
                print('camera: frame dropped')
                continue

            #img = self.frame0.getImage()
            if self.frame0._frame.frameID != last_frame_id:
                k+=1
                data = self.frame0.getBufferByteData()
                img = np.ndarray(buffer=data,
                                 dtype=np.uint8,
                                 shape=(self.frame0.height, self.frame0.width, self.frame0.pixel_bytes))

                self.redis.set('last_image_data', img.ravel().tostring())
                self.redis.set('last_image_timestamp', str(time.time()))
                self.redis.set('last_image_id', self.frame0._frame.frameID)
                self.redis.set('last_image_frame_timestamp', str(self.frame0._frame.timestamp))
                requested_gain = float(self.redis.get('camera_gain'))
                if requested_gain != self.current_gain:
                    self.set_gain(requested_gain)
                requested_exposure_time = float(self.redis.get('camera_exposure_time'))
                if requested_exposure_time != self.current_exposure_time:
                    self.set_exposure(requested_exposure_time)

            if k%10 == 0:
                print('camera last frame id %d fps %.3f ' % (self.frame0._frame.frameID, k/(time.time() - _start)))
                _start = time.time()
                k = 0
            gevent.sleep(0.01)

        self.camera.runFeatureCommand("AcquisitionStop")
        self.close_camera()

    def close_camera(self):
        self.master = False

        with Vimba() as vimba:
            self.camera.flushCaptureQueue()
            self.camera.endCapture()
            self.camera.revokeAllFrames()
            vimba.shutdown()

    def start_camera(self):
        return

if __name__ == '__main__':
    cam = camera()
    cam.run_camera()