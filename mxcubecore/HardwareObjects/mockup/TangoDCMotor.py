from mxcubecore import HardwareRepository as HWR
import logging
import numpy as np
from PyTango import DeviceProxy
from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore.HardwareObjects.abstract.AbstractMotor import MotorStates

class TangoDCMotor(HardwareObject):
    MOVE_STARTED = 0
    NOT_INITIALIZED = 0
    UNUSABLE = 0
    READY = 2
    MOVING = 4
    ON_LIMIT = 1

    STATE_DICT = {
        "UNKNOWN": 0,
        "OFF": 0,
        "ALARM": 1,
        "FAULT": 1,
        "STANDBY": 2,
        "RUNNING": 4,
        "MOVING": 4,
        "ON": 2,
    }

    def __init__(self, name):
        super().__init__(name)
        self.motor_states = MotorStates
        self.position_value = 0.0
        self.state_value = "UNKNOWN"
        self.threshold = 0.0018
        self.old_value = 0.0
        self.tango_name = None
        self.motor_name = None
        self.ho = None
        self.data_type = "float"
        self.limits_command = None
        self.position_chan = None
        self.state_chan = None

    def _init(self):
        self.tango_name = self.get_property("tangoname")
        self.motor_name = self.get_property("motor_name")
        self.ho = DeviceProxy(self.tango_name)

        threshold = self.get_property("threshold")
        if threshold is not None:
            try:
                self.threshold = float(threshold)
            except ValueError:
                logging.getLogger("HWR").warning("Invalid threshold value")

        self.data_type = self.get_property("datatype") or "float"

        self.set_is_ready(True)
        
        try:
            self.limits_command = self.get_command_object("limits")
        except KeyError:
            self.limits_command = None

        self.position_chan = self.get_channel_object("position")
        self.state_chan = self.get_channel_object("state")

        self.position_chan.connect_signal("update", self.position_changed)
        self.state_chan.connect_signal("update", self.motor_state_changed)

    def set_is_ready(self, ready):
        self.state = self.READY
        self.emit("deviceReady")
    
    def position_changed(self, value):
        self.position_value = value
        if abs(float(value) - self.old_value) > self.threshold:
            try:
                self.emit("valueChanged", (value,))
                self.old_value = value
            except Exception:
                logging.getLogger("HWR").error(
                    "%s: TangoDCMotor not responding", self.name()
                )
                self.old_value = value

    def is_ready(self):
        return self.state_value == "STANDBY"

    def connect_notify(self, signal):
        if signal == "hardware_object_name,stateChanged":
            self.motor_state_changed(self.STATE_DICT[self.state_value])
        elif signal == "limitsChanged":
            self.motor_limits_changed()
        elif signal == "valueChanged":
            self.motor_positions_changed(self.position_value)
        self.set_is_ready(True)

    def get_state(self):
        return self.STATE_DICT[self.state_value]

    def motor_state_changed(self, state):
        self.state_value = str(state)
        self.set_is_ready(True)
        logging.info("Motor state changed. It is %s", self.state_value)
        self.emit("stateChanged", (self.STATE_DICT[self.state_value],))

    def get_limits(self):
        try:
            limits = self.ho.getMotorLimits(self.motor_name)
            logging.getLogger("HWR").info(
                "Getting limits for %s -- %s", self.motor_name, str(limits)
            )
            if np.inf in limits:
                limits = np.array([-10000, 10000])
        except Exception:
            limits = self._get_default_limits()

        if limits is None:
            limits = self._get_property_limits()

        return limits

    def _get_default_limits(self):
        if self.motor_name in ["detector_distance", "detector_horizontal", "detector_vertical"]:
            info = self.position_chan.getInfo()
            return [float(info.min_value), float(info.max_value)]
        elif self.motor_name == "exposure":
            return [float(self.min_value), float(self.max_value)]
        return None

    def _get_property_limits(self):
        try:
            limits = self.get_property("min"), self.get_property("max")
            logging.getLogger("HWR").info(
                "TangoDCMotor.get_limits: %.4f ***** %.4f", *limits
            )
            return np.array(limits)
        except Exception:
            logging.getLogger("HWR").info(
                "Cannot get limits for %s", self.name()
            )
            return None

    def motor_limits_changed(self):
        self.emit("limitsChanged", (self.get_limits(),))

    def is_moving(self):
        return self.state_value in ["RUNNING", "MOVING"]

    def motor_move_done(self, channel_value):
        if self.state_value == "STANDBY":
            self.emit("moveDone", (self.tango_name, "tango"))

    def motor_positions_changed(self, absolute_position):
        self.emit("valueChanged", (absolute_position,))

    def sync_question_answer(self, spec_steps, controller_steps):
        return "0"  # This is only for spec motors. 0 means do not change anything on sync

    def get_value(self):
        return self.position_chan.get_value()

    def convert_value(self, value):
        logging.info("TangoDCMotor: converting value to %s", self.data_type)
        if self.data_type in ["short", "int", "long"]:
            return int(value)
        return value

    def get_motor_mnemonic(self):
        return self.name()

    def set_value(self, value):
        logging.getLogger("TangoClient").info(
            "TangoDCMotor move (%s). Trying to go to %s: type '%s'",
            self.motor_name,
            value,
            type(value),
        )
        value = float(value)
        if not isinstance(value, (float, int)):
            logging.getLogger("TangoClient").error(
                "Cannot move %s: position '%s' is not a number. It is a %s",
                self.tango_name,
                value,
                type(value),
            )
        else:
            logging.info("TangoDCMotor: move. motor will go to %s", value)
            logging.getLogger("HWR").info(
                "TangoDCMotor.move to absolute position: %.3f", value
            )
            self.position_chan.set_value(self.convert_value(value))

    def stop(self):
        logging.getLogger("HWR").info("TangoDCMotor.stop")
        stop_cmd = self.get_command_object("Stop")()
        if not stop_cmd:
            from mxcubecore.Command.Tango import TangoCommand
            stop_cmd = TangoCommand("stopcmd", "Stop", self.tango_name)
        stop_cmd()

    def is_spec_connected(self):
        logging.getLogger().debug("%s: TangoDCMotor.is_spec_connected()", self.name())
        return True

def test():
    hwr = HWR.get_hardware_repository()
    hwr.connect()
    motor = hwr.get_hardware_object("/phi")
    print(motor.get_value())

if __name__ == "__main__":
    test()