#!/usr/bin/python
# -*- coding: utf-8 -*-

#from HardwareObjects.abstract.AbstractVideoDevice import AbstractVideoDevice
#from mxcubecore.BaseHardwareObjects import HardwareObject
#from mxcubecore import HardwareRepository as HWR
#import imageio
import time
import os
import sys
import numpy as np
import logging
#import gevent
#sys.path.insert(0, "/nfs/ruche/share-dev/px1dev/MXCuBE/mxcube_Dan/upgrade/mxcubecore/")
import redis
#from pymba import *
import cv2

#from mxcubecore.HardwareObjects.abstract.AbstractVideoDevice import AbstractVideoDevice

class RedisCamera(object):

    def __init__(self, camera_index=0,
                 redis_host='localhost',
                 redis_port=6379,
                 use_redis=True,
                 *args,
                 **kwargs):

        super().__init__()
        self.cap = cv2.VideoCapture(camera_index)
        self.use_redis = use_redis
        self.redis = redis.Redis(host=redis_host, port=redis_port) if use_redis else None

        self.x_pixels_in_detector = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.y_pixels_in_detector = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.channels = 3  # Assuming RGB
        self.shape = (self.y_pixels_in_detector, self.x_pixels_in_detector, self.channels)

        # Default values, adjust as necessary
        self.default_exposure_time = 0.05
        self.current_exposure_time = self.default_exposure_time
        self.default_gain = 8.0
        self.current_gain = self.default_gain


    def get_image(self):
            ret, frame = self.cap.read()
            if not ret :
                print("FAIL")
            return frame if ret else None

ce = RedisCamera()
ce.get_image()

"""
    def get_rgbimage(self):
        return self.get_image()

    def set_exposure(self, exposure):
        # OpenCV method to set exposure, depends on camera driver support
        self.cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
        self.current_exposure_time = exposure

    def get_exposure(self):
        # Retrieving exposure, note that getting the exposure might not be supported depending on the camera/driver
        return self.cap.get(cv2.CAP_PROP_EXPOSURE)

    def run_camera(self):
        while True:
            frame = self.get_rgbimage()
            if frame is not None and self.use_redis:
                self.redis.set('last_image_data', frame.tobytes())
                self.redis.set('last_image_timestamp', time.time())
            time.sleep(0.1)  # Adjust based on your capture rate

    def start_camera(self):
        # Implementation detail: you might want to run this in a separate thread or process
        self.run_camera()

# Example usage
if __name__ == '__main__':
    cam = RedisCamera("RedisCamera")
    cam.start_camera()
"""