import logging
import time
import gevent

from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore.Command.Tango import DeviceProxy


class PX1TangoLight(HardwareObject):

    def __init__(self, name):
        super().__init__(name)
        self.current_state = "unknown"

    def init(self):
        #self.tangoname = self.
        self.attrchan = self.get_channel_object("attributeName")
        self.attrchan.connect_signal("update", self.value_changed)
        self.attrchan.connect_signal("connected", self._set_ready)
        self.attrchan.connect_signal("disconnected", self._set_ready)
        self.set_in = self.get_command_object("set_in")
        self.set_in.connect_signal("connected", self._set_ready)

        self.set_in.connect_signal("disconnected", self._set_ready)
        self.set_out = self.get_command_object("set_out")

        self.px1env_hwo = self.get_object_by_role("px1environment")
        self.light_hwo = self.get_object_by_role("intensity")
        self.zoom_hwo = self.get_object_by_role("zoom")

        self.connect(self.zoom_hwo, "predefinedPositionChanged", self.zoom_changed)

        #self.set_in.connect_signal("connected", self._set_ready)



        self._set_ready()
        try:
            self.inversed = self.get_property("inversed")
        except:
            self.inversed = False

        if self.inversed:
           self.states = ["in", "out"]
        else:
           self.states = ["out", "in"]

    def _set_ready(self):
        self.setIsReady(self.attrchan.isConnected())

    def connect_notyfy(self, signal):
        if self.is_ready():
           self.value_changed(self.attrchan.getValue())


    def value_changed(self, value):
        self.current_state = value

        if value:
            mxcube.BaseHardwareObjects
            self.current_state = self.states[1]
        else:
            self.current_state = self.states[0]

        self.emit('wagoStateChanged', (self.current_state, ))

    def get_wago_state(self):
        return self.current_state

    def wagoIn(self):
        self.setIn()
    def wagoOut(self):
        self.setOut()

    def setIn(self):
        if not self.px1env_hwo.isPhaseVisuSample():
            self.px1env_hwo.gotoSampleViewPhase()
            start_phase = time.time()
            while not self.px1env_hwo.isPhaseVisuSample():
                time.sleep(0.1)
                if time.time() - start_phase > 20:
                   break

        self.adjustLightLevel()

    def setOut(self):
        self._set_ready()
        if self.is_ready():
          if self.inversed:
              self.set_in()
          else:
              self.light_hwo.move(0)
              self.set_out()

    def zoom_changed(self, position_name, pos, valid):
        if not valid:
            return

        if self.current_state == "in":
            self.adjustLightLevel()

    def adjustLightLevel(self):
        if self.zoom_hwo is None or self.light_hwo is None:
            return

        props = self.zoom_hwo.getCurrentPositionProperties()

        try:
            if 'lightLevel' in props.keys():
                light_level = float(props['lightLevel'])
                light_current = self.light_hwo.getPosition()
                if light_current != light_level:
                    logging.getLogger("HWR").debug("Setting light level to %s" % light_level)
                    self.light_hwo.move(light_level)
        except:
            logging.getLogger("HWR").debug("Cannot set light level")