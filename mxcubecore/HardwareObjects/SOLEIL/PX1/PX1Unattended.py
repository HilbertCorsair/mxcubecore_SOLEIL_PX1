"""HardwareObject used as wrapper for automatic collects

This is a temporary solution to start and test automatic sequences on MXCuBE Web

"""

import logging
import gevent
import time
import os
import sys


from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore import HardwareRepository as HWR


log = logging.getLogger("HWR")

class PX1Unattended(HardwareObject):

    def __init__(self, *args):
        HardwareObject.__init__(self, *args)
        self.flagZoomValChange = 0

    def init(self):
        print("PX1Unattended init")

    def __getBeamsize__(self):
        try:
            size_hor, size_ver = HWR.beamline.beam.get_beam_size()
            size_hor *= 1000 # Why is it multiplied by 1000
            size_ver *= 1000 # Why is it multiplied by 1000
        except Exception:
            size_hor = None
            size_ver = None
        return size_hor, size_ver

    def __bboxToCoor__(self, bbox_loop):

        x1, y1, x2, y2 = -1, -1, -1, -1

        return ((x1, y1), (x2, y2))

    def __getZoomValue__(self):
        try:
            currentZoom = HWR.beamline.diffractometer.zoom.get_value()
        except Exception:
            currentZoom = -1
        return currentZoom

    def __setZoomValue__(self, newZoom):
        try:
            HWR.beamline.diffractometer.zoom.set_value(newZoom) # Never tested
            self.flagZoomValChange = newZoom
        except Exception:
            self.flagZoomValChange = -1


    def StartAutoSequence():
        """Main method to call to start the collect of current samples in Queue

        Args:

        Returns:

        Raises:

        """

        print("We were called succesfully")

        # basket:basketPosition = location

        #HWR.beamline.sample_changer._do_load(sample="1:02", wash=False)

        # For sample in Queue:
        #   Zoom(1)                                Done
        #   Mount(sample)
        #   Murko()
        #   Zoom(3)                                Done
        #   bbox_loop = Murko()
        #   coord = __bboxToCoor__(bbox_loop)      Definition
        #   hotPoint = XrayCentring(coord)
        #   Collect(hotPoint)

        pass