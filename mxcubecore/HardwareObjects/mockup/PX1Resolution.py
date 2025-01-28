import logging
from mxcubecore.HardwareObjects.abstract.AbstractResolution import AbstractResolution

DETECTOR_DIAMETER = 424.0
NOTINITIALIZED, UNUSABLE, READY, MOVESTARTED, MOVING, ONLIMIT = (0, 1, 2, 3, 4, 5)
from PyTango import DeviceProxy
from mxcubecore import HardwareRepository as HWR
class PX1Resolution(AbstractResolution):
    state_dict = {
        "UNKNOWN": 0,
        "ALARM": 1,
        "STANDBY": 2,
        "RUNNING": 4,
        "MOVING": 4,
        "FAULT": 1,
        "1": 1,
        "2": 2,
    }

    def _init(self):
        self._nominal_value = None
        self.current_distance = None
        self.distance_chan = self.get_channel_object("distance")
        self.resolution_chan = self.get_channel_object("resolution")
        self.minimum_res_chan = self.get_channel_object("minimum_resolution")
        self.maximum_res_chan = self.get_channel_object("maximum_resolution")
        self.minimum_dist_chan = self.get_channel_object("minimum_distance")
        self.state_chan = self.get_channel_object("state")
        self.stop_command = self.get_command_object("stop")
        self._detector = self.get_object_by_role("detector")

        self.distance_chan.connect_signal("update", self.distance_changed)
        self.resolution_chan.connect_signal("update", self.resolution_changed)
        self.minimum_res_chan.connect_signal("update", self.minimum_resolution_changed)
        self.maximum_res_chan.connect_signal("update", self.maximum_resolution_changed)
        self.minimum_dist_chan.connect_signal("update", self.minimum_distance_changed)
        self.state_chan.connect_signal("update", self.state_changed)

        self.current_distance = self.distance_chan.get_value()
        self._nominal_value = self.resolution_chan.get_value()
        return AbstractResolution._init(self)

    def connect_notify(self, signal):
        if signal == "stateChanged":
            self.state_changed()
        elif signal == "distanceChanged":
            self.distance_changed()
        elif signal == "resolutionChanged":
            self.resolution_changed()
        elif signal == "distanceLimitsChanged":
            self.minimum_resolution_changed()
        elif signal == "resolutionLimitsChanged":
            self.minimum_resolution_changed()

    def equipment_ready(self):
        self.emit("deviceReady")
        from mxcubecore import HardwareRepository as HWR
    def equipment_not_ready(self):
        self.emit("deviceNotReady")

    def motstate_to_state(self, motstate):
        motstate = str(motstate)
        if motstate in ["ON", "STANDBY"]:
            state = self.STATES.READY
        elif motstate == "MOVING":
            state = self.STATES.BUSY
        elif motstate == "FAULT":
            state = self.STATES.FAULT
        elif motstate == "OFF":
            state = self.STATES.OFF
        else:
            state = self.STATES.UNKNOWN
        return state

    def get_state(self, value=None):
        if value is None:
            value = self.state_chan.get_value()
            HO_state = self.motstate_to_state(str(value))
            return HO_state
        

    def calculate_resolution(self, radius=None, distance=None, wavelength=None):
        return self.get_value()

    def get_value(self):
        if self._nominal_value is None:
            self._nominal_value = self.resolution_chan.get_value()
        return self._nominal_value

    def get_distance(self):
        if self._nominal_value is None:
            self.recalculate_resolution()
        return self.current_distance

    def minimum_resolution_changed(self, value=None):
        self.emit("resolutionLimitsChanged", (self.get_limits(),))

    def maximum_resolution_changed(self, value=None):
        self.emit("resolutionLimitsChanged", (self.get_limits(),))

    def minimum_distance_changed(self, value=None):
        self.emit("distanceLimitsChanged", (self.get_distance_limits(),))

    def state_changed(self, state=None):
        self.emit("stateChanged", (self.get_state(state),))

    def distance_changed(self, value=None):
        self.recalculate_resolution()

    def resolution_changed(self, value=None):
        self.recalculate_resolution()


    def recalculate_resolution(self):
        print("PRINT 3 recalculate resolution")
        distance = self.distance_chan.get_value()
        resolution = self.resolution_chan.get_value()

        if resolution is None or distance is None:
            return

        if (self._nominal_value is not None) and abs(
            resolution - self._nominal_value
        ) > 0.001:
            self._nominal_value = resolution
            self.emit("resolutionChanged", (resolution,))

        if (self.current_distance is not None) and abs(
            distance - self.current_distance
        ) > 0.001:
            self.current_distance = distance
            self.emit("distanceChanged", (distance,))

        '''
        self.det_width = self._detector.get_property("width")
        self.det_height = self._detector.get_property("height")
        beam_x = DeviceProxy(self._detector).beamCenterX
        beam_y = DeviceProxy(self._detector).beamCenterY
        #beam_x, beam_y = self._detector.get_beam_centre()

        radius =  min(self.det_width - beam_x, self.det_height - beam_y, beam_x, beam_y)
        import pdb 
        pdb.set_trace()

        wl = self._detector.get_wavelength()
        dist = self.get_channel_object("distance").get_value()
        self._calculate_resolution(radius=radius, wavelength= wl, distance = dist)
        """
        distance = self.distance_chan.get_value()
        resolution = self.resolution_chan.get_value()
        if resolution is None or distance is None:
            return
        if (self._nominal_value is not None) and abs(
            resolution - self._nominal_value
        ) > 0.001:
            self._nominal_value = resolution
            self.emit("resolutionChanged", (resolution,))
        if (self.current_distance is not None) and abs(
            distance - self.current_distance
        ) > 0.001:
            self.current_distance = distance
            self.emit("distanceChanged", (distance,))
        """
        '''

    def get_distance_limits(self):
        chan_info = self.distance_chan.get_info()
        high = float(chan_info.max_value)
        low = self.minimum_dist_chan.get_value()
        return [low, high]

    def get_limits(self):
        high = self.maximum_res_chan.get_value()
        low = self.minimum_res_chan.get_value()
        return (low, high)

    def move_resolution(self, res):
        self.resolution_chan.set_value(res)

    def move_distance(self, dist):
        self.distance_chan.set_value(dist)

    def stop(self):
        try:
            self.stop_command()
        except Exception:
            logging.getLogger("HWR").err(
                "%s: PX1Resolution.stop: error while trying to stop!", self.name()
            )

    def re_emit_values(self):
        self.state_changed()
        self.distance_changed()
        self.resolution_changed()
        self.minimum_resolution_changed()
        self.minimum_resolution_changed()

    move = move_resolution

def test_hwo(hwo):
    print("Distance [limits]", hwo.get_distance(), hwo.get_distance_limits())
    print("Resolution [limits]", hwo.get_value(), hwo.get_limits())