from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore.Command.Tango import DeviceProxy
import gevent
class EnvironmentPhase:
    TRANSFER = 0
    CENTRING = 1
    COLLECT = 2
    DEFAULT = 3
    BEAMVIEW = 4
    FLUOX = 5
    MANUAL_TRANSFER = 6
    IN_PROGRESS = 7
    VISU_SAMPLE = 8

    phase_desc = {
        "TRANSFER": TRANSFER,
        "CENTRING": CENTRING,
        "COLLECT": COLLECT,
        "DEFAULT": DEFAULT,
        "BEAMVIEW": BEAMVIEW,
        "FLUOX": FLUOX,
        "MANUAL_TRANSFER": MANUAL_TRANSFER,
        "IN_PROGRESS": IN_PROGRESS,
        "VISU_SAMPLE": VISU_SAMPLE,
    }

    @staticmethod
    def phase(phase_name):
        return EnvironmentPhase.phase_desc.get(phase_name)

class EnvironmentState:
    UNKNOWN, ON, RUNNING, ALARM, FAULT = (0, 1, 10, 13, 14)
    state_desc = {ON: "ON", RUNNING: "RUNNING", ALARM: "ALARM", FAULT: "FAULT"}

class PX1BackLight(HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self._light_state = "OFF"

    def augment(self):
        self.tangoname = self.get_property("tangoname")
        self.device = DeviceProxy(self.tangoname)

    @property
    def light(self):
        return self._light_state
    @light.setter
    def light(self, l_st):
        self._light_state = l_st

    def light_switch(self):
        if self.device.readyForVisuSample:
            self.device.GoToVisuSamplePhase
            gevent.sleep(5)
            self.update_backlight
        elif self.device.currentPhase == "VISUSAMPLE":
            self.device.GoToDefaultPhase
            gevent.sleep(5)
            self.update_backlight

        """if self.light == "ON":
            self.px1env_ho.set_phase("VISU_SAMPLE")
        elif self.light == "OFF":
            self.px1env_ho.set_phase("DEFAULT")
        else:
            print("Trigger must be either ON or OFF")"""

    def update_backlight(self):
        self.light = "ON" if self.device.currentPhase == "VISUSAMPLE" else "OFF"
        self.emit("stateChanged", (self.light, ))


    def _init_commands(self):
        if self.device is not None:
            self.cmds = {
                EnvironmentPhase.TRANSFER: self.device.GoToTransfertPhase,
                EnvironmentPhase.CENTRING: self.device.GoToCentringPhase,
                EnvironmentPhase.COLLECT: self.device.GoToCollectPhase,
                EnvironmentPhase.DEFAULT: self.device.GoToDefaultPhase,
                EnvironmentPhase.FLUOX: self.device.GoToFluoXPhase,
                EnvironmentPhase.MANUAL_TRANSFER: self.device.GoToManualTransfertPhase,
                EnvironmentPhase.VISU_SAMPLE: self.device.GoToVisuSamplePhase,
            }

    def ready_for_visu_sample(self):
        return self.device.readyForVisuSample if self.device else None