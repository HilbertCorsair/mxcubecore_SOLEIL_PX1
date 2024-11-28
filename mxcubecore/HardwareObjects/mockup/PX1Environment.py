import time
import logging
import gevent
from mxcubecore.HardwareObjects.abstract.AbstractMotor import AbstractMotor
from mxcubecore.Command.Tango import DeviceProxy
from mxcubecore.TaskUtils import task

from mxcubecore.BaseHardwareObjects import HardwareObject

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

    @staticmethod
    def to_string(state):
        return SampleChangerState.state_desc.get(state, "Unknown")

class PX1Environment(HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self.auth = None
        self.device = None
        self.state_chan = None
        self.chan_auth = None
        self.cmds = {}

    def init(self):
        self.device = DeviceProxy(self.get_property("tangoname"))
        self._init_channels()
        self._init_commands()
        self._update_state()

    def _init_channels(self):
        try:
            self.state_chan = self.get_channel_object("State")
            if self.state_chan is None:
                self.state_chan = self.add_channel(
                    {
                        "type": "tango",
                        "name": "state_can",
                        "tangoname": self.tangoname,
                        "polling": 300,
                    },
                    "State",
                )

            self.state_chan.connect_signal("update", self._update_state)
        except KeyError:
            logging.getLogger().warning("%s: cannot report State", self.name())
        try:
            self.chan_auth = self.get_channel_object("beamlineMvtAuthorized")
            self.chan_auth.connect_signal("update", self._set_authorization_flag)
        except KeyError:
            logging.getLogger().warning("%s: cannot report Authorization", self.name())

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

    def _motstate_to_state(self, motstate):
        motstate = str(motstate)
        state_map = {
            "ON": self.STATES.READY,
            "MOVING": self.STATES.BUSY,
            "FAULT": self.STATES.FAULT,
            "OFF": self.STATES.OFF
        }
        return state_map.get(motstate, self.STATES.UNKNOWN)

    def _update_state(self,s=None):
        gevent.sleep(0.1)
        motor_state = self.state_chan.get_value()
        self.log.debug(f"Reading motor state for {self.name} is {str(motor_state)}")
        self._motor_state_changed(motor_state)

    def _motor_state_changed(self, state=None):
        if not state:
            state = self.state_chan.get_value()
        self.update_state(self._motstate_to_state(state))

    def get_state(self):
        return str(self.state_chan.get_value())

    def is_busy(self, timeout=None):
        state = self.state_chan.get_value()
        return state not in [EnvironmentState.ON]

    def wait_ready(self, timeout=None):
        self._wait_state(["ON"], timeout)

    def _wait_state(self, states, timeout=None):
        if self.device is None:
            return

        with gevent.Timeout(timeout, Exception("Timeout waiting for device ready")):
            while self.state_chan.get_value() not in states:
                gevent.sleep(0.05)

    def is_phase_transfer(self):
        return self.device.readyForTransfert

    def is_phase_collect(self):
        return self.ready_for_collect()

    def is_phase_visu_sample(self):
        return self.device.readyForVisuSample

    def is_phase_fluo_scan(self):
        return self.device.readyForFluoScan

    def ready_for_centring(self):
        return self.device.readyForCentring if self.device else None

    def ready_for_collect(self):
        return self.device.readyForCollect if self.device else None

    def ready_for_default_position(self):
        return self.device.readyForDefaultPosition if self.device else None

    def ready_for_fluo_scan(self):
        return self.device.readyForFluoScan if self.device else None

    def ready_for_manual_transfer(self):
        return self.device.readyForManualTransfert if self.device else None

    def ready_for_transfer(self):
        return self.device.readyForTransfert if self.device else None

    def ready_for_visu_sample(self):
        return self.device.readyForVisuSample if self.device else None

    def goto_phase(self, phase):
        logging.debug(f"PX1environment.goto_phase {phase}")
        cmd = self.cmds.get(phase)
        if cmd is not None:
            logging.debug(f"PX1environment.goto_phase state {self.get_state()}")
            cmd()

    def set_phase(self, phase, timeout=120):
        self.goto_phase(phase)
        self.wait_phase(phase, timeout)

    def read_phase(self):
        if self.device is not None:
            phase_name = self.device.currentPhase
            return EnvironmentPhase.phase(phase_name)

    def get_current_phase(self):
        return self.device.currentPhase if self.device else None

    def get_phase(self):
        return self.device.currentPhase if self.device else None

    def wait_phase(self, phase, timeout=None):
        if self.device is None:
            return

        logging.debug("PX1environment: start wait_phase")
        with gevent.Timeout(timeout, Exception("Timeout waiting for environment phase")):
            while self.read_phase() != phase:
                gevent.sleep(0.05)
        logging.debug("PX1environment: end wait_phase")

    def goto_centring_phase(self):
        if not self.ready_for_centring() or self.get_phase() != "CENTRING":
            self.get_command_object("GoToCentringPhase")()
            time.sleep(0.1)

    def goto_collect_phase(self):
        if not self.ready_for_collect() or self.get_phase() != "COLLECT":
            self.get_command_object("GoToCollectPhase")()
            time.sleep(0.1)

    def goto_loading_phase(self):
        if not self.ready_for_transfer():
            self.get_command_object("GoToTransfertPhase")()
            time.sleep(0.1)

    def goto_manual_loading_phase(self):
        if not self.ready_for_transfer():
            self.get_command_object("GoToManualTransfertPhase")()
            time.sleep(0.1)

    def goto_sample_view_phase(self):
        if not self.ready_for_visu_sample():
            self.get_command_object("GoToVisuSamplePhase")()
            time.sleep(0.1)

    def goto_fluo_scan_phase(self):
        if not self.ready_for_fluo_scan():
            self.get_command_object("GoToFluoScanPhase")()
            time.sleep(0.1)

    def _set_authorization_flag(self, value):
        if value != self.auth:
            logging.getLogger("HWR").debug(
                f"PX1Environment. received authorization from cryotong: {value}"
            )
            self.auth = value
            self.emit("operation_permitted", value)

def test_hwo(hwo):
    print("PX1 Environment (state) ", hwo.get_state())
    print("               phase is ", hwo.get_current_phase())
    print("        beamstop pos is ", hwo.get_beamstop_position())