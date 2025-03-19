import json
from enum import (
    Enum,
    unique,
)
from PyTango import DeviceProxy
from mxcubecore.BaseHardwareObjects import HardwareObjectState
from mxcubecore.HardwareObjects.abstract.AbstractShutter import AbstractShutter
__copyright__ = """ Copyright © 2023 by the MXCuBE collaboration """
__license__ = "LGPLv3+"

@unique
class TangoShutterStates(Enum):
    """Shutter states definitions."""

    OUT  = HardwareObjectState.READY, "EXTRACT"
    IN = HardwareObjectState.READY, "INSERT"
    MOVING = HardwareObjectState.BUSY, "MOVING"

class PX1Tools(AbstractShutter):
    """TANGO implementation of AbstractShutter"""

    SPECIFIC_STATES = TangoShutterStates

    def __init__(self, name):
        super().__init__(name)
        self.state_channel = None

    def init(self):
        """Initilise the predefined values"""
        super().init()

        if self.name() == "/capillary":
            print ("INIT CAPIL")


        self._tool_hwo = self.get_property('tangoname')
        self.tool_device = DeviceProxy(self._tool_hwo)
        self.state_channel = self.get_channel_object("State")
        #self._initialise_values()
        self.state_channel.connect_signal("update", self._update_value)
        self.update_state()

        try:
            self.config_values = json.loads(self.get_property("values"))
        except:
            self.config_values = None

    def _update_value(self, value):

        if self.name() == "/capillary":
            print ("Updating Value Capillary")
        """Update the value.
        Args:
            value(str): The value reported by the state channel.
        """
        if self.config_values:
            value = self.config_values[str(value)]
        else:
            value = str(value)

        super().update_value(self.value_to_enum(value))

    def _initialise_values(self):
        """Add the tango states to VALUES"""
        values_dict = {item.name: item.value for item in self.VALUES}
        values_dict.update(
            {
                "MOVING": "MOVING",
                "DISABLE": "DISABLE",
                "STANDBY": "STANDBY",
                "FAULT": "FAULT",
            }
        )
        self.VALUES = Enum("ValueEnum", values_dict)

    def get_state(self):

        if self.name() == "/capillary":
            print ("Getting STATE CAPIL")
        """Get the device state.
        Returns:
            (enum 'HardwareObjectState'): Device state.
        """
        try:
            if self.config_values:
                _state = self.config_values[str(self.state_channel.get_value())]
            else:
                _state = str(self.state_channel.get_value())

        except (AttributeError, KeyError):
            return self.STATES.UNKNOWN

        return self.SPECIFIC_STATES[_state].value[0]

    def get_value(self):
        if self.name() == "/capillary":
            print ("Getting CAPIL VAL")
        """Get the device value
        Returns:
            (Enum): Enum member, corresponding to the 'VALUE' or UNKNOWN.
        """
        if self.config_values:
            _val = self.config_values[str(self.state_channel.get_value())]
            print(_val)
        else:
            _val = str(self.state_channel.get_value())
        return self.value_to_enum(_val)