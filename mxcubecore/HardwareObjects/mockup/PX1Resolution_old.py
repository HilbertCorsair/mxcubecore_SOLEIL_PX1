
import math
import logging
import time
import gevent
from gevent import Timeout

from HardwareRepository.Command.Tango import DeviceProxy

from HardwareRepository.BaseHardwareObjects import Equipment

DETECTOR_DIAMETER = 424.

NOTINITIALIZED, UNUSABLE, READY, MOVESTARTED, MOVING, ONLIMIT = (0,1,2,3,4,5)

log = logging.getLogger("HWR")

class PX1Resolution(Equipment):
        
    stateDict = {
         "UNKNOWN": 0,
         "ALARM":   1,
         "STANDBY": 2,
         "RUNNING": 4,
         "MOVING":  4,
         "FAULT":   1,
         "1":       1,
         "2":       2}
   
    
    def _init(self):

        self.currentResolution = None
        self.currentDistance = None
        self.moving_manual = False
        self.saved_state = None

        self.connect("equipmentReady", self.equipmentReady)
        self.connect("equipmentNotReady", self.equipmentNotReady)
        
        self.distance_chan = self.getChannelObject("distance") 
        self.resolution_chan = self.getChannelObject("resolution") 
        self.minimum_res_chan = self.getChannelObject("minimum_resolution") 
        self.maximum_res_chan = self.getChannelObject("maximum_resolution") 
        self.minimum_dist_chan = self.getChannelObject("minimum_distance") 
        self.state_chan = self.getChannelObject("state") 

        self.stop_command = self.getCommandObject("stop")
        self.calc_limits_command = self.getCommandObject("calc_limits")
   
        self.distance_chan.connectSignal("update", self.distanceChanged)
        self.resolution_chan.connectSignal("update", self.resolutionChanged)
        self.minimum_res_chan.connectSignal("update", self.minimumResolutionChanged)
        self.maximum_res_chan.connectSignal("update", self.maximumResolutionChanged)
        self.minimum_dist_chan.connectSignal("update", self.minimumDistanceChanged)
        self.state_chan.connectSignal("update", self.stateChanged)
        
        self.currentDistance = self.distance_chan.getValue()
        self.currentResolution = self.resolution_chan.getValue()

        return Equipment._init(self)
        
    def connectNotify(self, signal):
        if signal == "stateChanged":
            self.stateChanged()
        elif signal == 'distanceChanged':
            self.distanceChanged()
        elif signal == 'resolutionChanged':
            self.resolutionChanged()
        elif signal == 'distanceLimitsChanged':
            self.minimumResolutionChanged()
        elif signal == 'resolutionLimitsChanged':
            self.minimumResolutionChanged()

    def equipmentReady(self):
        self.emit("deviceReady")

    def equipmentNotReady(self):
        self.emit("deviceNotReady")

    def getState(self, value=None):
        if value is None:
            value = self.state_chan.getValue()
        state_str = str(value)
        #return self.stateDict[state_str]
        return state_str

    def getResolution(self):
        if self.currentResolution is None:
            self.recalculateResolution()
        return self.currentResolution

    def getDistance(self):
        if self.currentResolution is None:
            self.recalculateResolution()
        return self.currentDistance

    def minimumResolutionChanged(self, value=None):
        self.emit('resolutionLimitsChanged', (self.getResolutionLimits(),))
        self.emit('limitsChanged', (self.getResolutionLimits(),))

    def maximumResolutionChanged(self, value=None):
        self.emit('resolutionLimitsChanged', (self.getResolutionLimits(),))
        self.emit('limitsChanged', (self.getResolutionLimits(),))

    def minimumDistanceChanged(self, value=None):
        self.emit('distanceLimitsChanged', (self.getDistanceLimits(),))

    def stateChanged(self, state=None):
        if self.saved_state != str(state): 
            log.debug("Resolution state was %s, now is %s" % (self.saved_state, str(state)))
            self.saved_state = str(state) 
            if self.saved_state == "STANDBY":
                self.moving_manual = False

        self.emit('stateChanged', (self.getState(state), ))

    def distanceChanged(self, value=None):
        self.recalculateResolution()

    def resolutionChanged(self, value=None):
        self.recalculateResolution()

    def recalculateResolution(self):
        distance = self.distance_chan.getValue()
        resolution = self.resolution_chan.getValue()

        if resolution is None or distance is None:
            return

        if (self.currentResolution is not None) and \
                  abs(resolution - self.currentResolution) > 0.002:
            self.currentResolution = resolution
            self.emit("resolutionChanged", (resolution, ))
            if self.moving_manual:
                self.emit("positionChanged", (resolution, ))

        if (self.currentDistance is not None) and \
                  abs(distance - self.currentDistance) > 0.005:
            self.currentDistance = distance
            self.emit("distanceChanged", (distance, ))

    def getDistanceLimits(self):

        chan_info = self.distance_chan.getInfo()

        high = float(chan_info.max_value)
        low = self.minimum_dist_chan.getValue()

        return [low,high]

    def getResolutionLimits(self, energy=None, lightin=False):
        if energy is None: 
            high = self.maximum_res_chan.getValue()
            low = self.minimum_res_chan.getValue()
        else:
            if self.calc_limits_command is not None:
                # energy should be given in eV
                energy = 1000 * energy
                logging.getLogger("HWR").debug("resolution hwo. calculating limits for energy %s" % energy)
                low,high = map(float,self.calc_limits_command(energy))
                logging.getLogger("HWR").debug("resolution hwo. calculating limits for energy %s / %s, %s" % (energy, low,high))

        return [low,high]

    get_resolution_limits = getResolutionLimits

    def moveResolution(self, res, manual=False):
        if manual:
            self.moving_manual = True

        try:
           self.resolution_chan.setValue( res )
        except:
           import traceback
           traceback.print_exc()

    def moveDistance(self, dist, manual=False):
        if manual:
            self.moving_manual = True

        self.distance_chan.setValue( dist )

    def syncMoveResolution(self,res):
        curr_res = self.resolution_chan.getValue()
        if abs(curr_res - res) > 0.01:
            self.resolution_chan.setValue( res )
            try:
                self.wait_notready()
            except Timeout:
                pass
            self.wait_ready()

    def stop(self):
        try:
            self.stop_command()
        except:
            logging.getLogger("HWR").err("%s: PX1Resolution.stop: error while trying to stop!", self.name())

    def wait_notready(self, timeout=4):
        t0 = time.time()
        while self.is_ready():
            if (time.time() - t0) > timeout:
                raise Timeout
            gevent.sleep(0.03)

    def wait_ready(self, timeout=120):
        t0 = time.time()

        while not self.is_ready():
            if (time.time() - t0) > timeout:
                raise Timeout
            gevent.sleep(0.03)

    def is_ready(self):
        return self.getState() == "STANDBY"

    def update_values(self):
        self.stateChanged()
        self.distanceChanged()
        self.resolutionChanged()
        self.minimumResolutionChanged()
        self.minimumResolutionChanged()

    getLimits = getResolutionLimits
    getPosition = getResolution
    move = moveResolution
    syncMove = syncMoveResolution
        
def test_hwo(hwo):
    print "Distance [limits]", hwo.getDistance(), hwo.getDistanceLimits()
    print "Resolution [limits]", hwo.getResolution(), hwo.getResolutionLimits()
    print "is ready? ", hwo.is_ready()
    hwo.syncMove(3)
    print "is ready? ", hwo.is_ready()
    
