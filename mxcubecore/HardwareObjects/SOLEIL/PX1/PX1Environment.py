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
    UNKNOWN, ON, RUNNING, ALARM, FAULT, DISABLE, MOVING = (0, 1, 10, 13, 14, 15, 16)
    state_desc = {ON: "ON", RUNNING: "RUNNING", ALARM: "ALARM", FAULT: "FAULT", DISABLE:"DISABLE", MOVING: "MOVING"}

    @staticmethod
    def to_string(state):
        return SampleChangerState.state_desc.get(state, "UNKNOWN")

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
        #self.log.debug(f"Reading motor state for {self.name} is {str(motor_state)}")
        self._motor_state_changed(motor_state)

    def _motor_state_changed(self, state=None):
        if not state:
            state = self.state_chan.get_value()
        self.update_state(self._motstate_to_state(state))

    def get_state(self):
        return str(self.state_chan.get_value())

    def is_busy(self, timeout=None):
        state = self.state_chan.get_value().name
        return not state in ['ON', "STANDBY"]

    def wait_ready(self, timeout=60):
        """True once the supervisor is idle. Bounded, and never raises.

        Called on the mount path (PX1Cryotong._do_load_operation, before a
        chained load and again after the transfer), where it used to default
        to timeout=None - a gevent.Timeout(None) never fires, so a supervisor
        that settled anywhere but ON stalled the queue greenlet for ever.

        STANDBY counts as ready because is_busy() in this class already treats
        it that way; requiring exactly ON made the two disagree.
        """
        if self.device is None or self.state_chan is None:
            return True

        ready = ("ON", "STANDBY")
        t0 = time.time()
        while True:
            try:
                value = self.state_chan.get_value()
            except Exception:
                logging.getLogger("HWR").exception(
                    "PX1Environment: cannot read the supervisor state"
                )
                return True

            # Tango hands back a DevState enum here, but _update_state() takes
            # the str() of the same value, so do not assume either shape.
            state = getattr(value, "name", None) or str(value)

            if state in ready:
                return True

            if time.time() - t0 > timeout:
                logging.getLogger("HWR").warning(
                    "PX1Environment: supervisor still not ready after %s s "
                    "(state %s, phase %s), continuing anyway",
                    timeout,
                    *self._describe()
                )
                return False

            gevent.sleep(0.2)

    def wait_not_moving(self, timeout=60):
        """True once the supervisor is out of MOVING / RUNNING.

        The device refuses every GoTo*Phase command while it is moving
        ("GoToTransfertPhase not allowed when the device is in MOVING state").
        Waiting for "not moving" rather than for ON is deliberate: in FAULT or
        ALARM the command is still accepted (and fails for a reason worth
        seeing), so blocking until ON would only turn a visible error into a
        timeout. Never raises - the caller decides what a False means.
        """
        if self.device is None or self.state_chan is None:
            return True

        busy = ("MOVING", "RUNNING")
        t0 = time.time()
        while True:
            try:
                value = self.state_chan.get_value()
            except Exception:
                logging.getLogger("HWR").exception(
                    "PX1Environment: cannot read the supervisor state"
                )
                return True

            # Tango hands back a DevState enum here, but _update_state() takes
            # the str() of the same value, so do not assume either shape.
            state = getattr(value, "name", None) or str(value)

            if state not in busy:
                return True

            if time.time() - t0 > timeout:
                return False

            gevent.sleep(0.2)

    def _wait_state(self, states, timeout=None):
        if self.device is None:
            return

        with gevent.Timeout(timeout, Exception("Timeout waiting for device ready")):
            while self.state_chan.get_value().name not in states:
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
        if self.device:
            ok =  self.device.readyForCollect
        else:
            return False
        return ok

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

    def _describe(self):
        """State and current phase for a log line, never raising.

        Used from the goto_phase warnings, which fire exactly when the device is
        unhappy - a raising read there would replace the diagnostic with its own
        traceback.
        """
        try:
            state = self.get_state()
        except Exception:
            state = "<unreadable>"
        try:
            phase = self.get_current_phase()
        except Exception:
            phase = "<unreadable>"
        return state, phase

    def goto_phase(self, phase, timeout=60):
        """Send the supervisor to <phase>, waiting until it can accept it.

        Every phase change in PX1 goes through here, so this is the one place
        the "not allowed when the device is in MOVING state" DevFailed can be
        kept out of the queue. It used to log the state and then send the
        command anyway; the queue's next mount calls env_send_transfer()
        straight after the previous sample's motion, which is exactly when the
        supervisor is still moving.
        """
        logging.debug(f"PX1environment.goto_phase {phase}")
        cmd = self.cmds.get(phase)
        if cmd is None:
            return

        log = logging.getLogger("HWR")

        if not self.wait_not_moving(timeout):
            state, current = self._describe()
            log.warning(
                "PX1Environment: still %s after %s s (phase %s); sending phase "
                "%s anyway",
                state,
                timeout,
                current,
                phase,
            )

        try:
            cmd()
        except Exception:
            # One retry: the state can go MOVING between the check and the
            # call, and the supervisor rejects the command outright rather
            # than queueing it.
            state, current = self._describe()
            log.warning(
                "PX1Environment: phase %s refused while %s (phase %s); "
                "retrying once",
                phase,
                state,
                current,
                exc_info=True,
            )
            self.wait_not_moving(timeout)
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
            # Same DevFailed as GoToTransfertPhase: the supervisor rejects a
            # phase command while it is moving. These two helpers bypass
            # goto_phase (they go through get_command_object), so the wait has
            # to be repeated here.
            self.wait_not_moving()
            self.get_command_object("GoToCentringPhase")()
            time.sleep(0.1)

    def goto_collect_phase(self):

        if not self.ready_for_collect() or self.get_phase() != "COLLECT":
            self.wait_not_moving()
            self.get_command_object("GoToCollectPhase")
            if not self.get_command_object("GoToCollectPhase"):
                try :
                    self._collect = self.add_command( {"type": "tango", "name": "GoToCollectPhase", "tangoname": self.tangoname}, "GoToCollectPhase", )
                    self._collect()
                    time.sleep(0.1)
                except :
                    print("EXEPTION GTCP 5")
                    pass
            else:
                self.get_command_object("GoToCollectPhase")()


    def goto_loading_phase(self):
        if not self.ready_for_transfer():
            self.get_command_object("GoToTransfertPhase")
            time.sleep(0.1)

    def goto_manual_loading_phase(self):
        if not self.ready_for_transfer():
            self.get_command_object("GoToManualTransfertPhase")
            time.sleep(0.1)

    def goto_default_phase(self):
        if not self.ready_for_default_position():
            self.get_command_object("GoToDefaultPhase")()
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