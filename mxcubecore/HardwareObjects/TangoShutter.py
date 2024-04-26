# encoding: utf-8
#
#  Project: MXCuBE
#  https://github.com/mxcube
#
#  This file is part of MXCuBE software.
#
#  MXCuBE is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  MXCuBE is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with MXCuBE. If not, see <http://www.gnu.org/licenses/>.

""" TangoShutter class - interface for shutter controlled by TANGO
Implements _set_value, get_value methods

Tango states are:
"UNKNOWN", "OPEN", "CLOSED", "FAULT", "MOVING", "DISABLE", "STANDBY", "RUNNING"

Example xml file:
<object class = "TangoShutter">
  <username>Safety Shutter</username>
  <tangoname>ab/cd/ef</tangoname>
  <command type="tango" name="Open">Open</command>
  <command type="tango" name="Close">Close</command>
  <channel type="tango" name="State" polling="1000">State</channel>
<<<<<<< Updated upstream
  <values>{"open": "OPEN", "cloded": "CLOSED", "DISABLE" : "DISABLE"}</values>
</object>

In this example the <values> tag contains a json dictionary that maps spectific tango shutter states to the
convantional states defined in the TangoShutter Class. This tag is not necessay in cases where the tango shutter states
are all covered by the TangoShuter class conventional states.
=======
  <values>{"OPEN": "MYOPEN", "NEWSTATE": ["MYSTATE", "BUSY"]}</values>
</object>

In the example the <values> property contains a dictionary that redefines or
adds specific tango shutter states.
When redefining a known state, only the VALUES Enum will be updated.
When defining a new state (new key), the dictionary value should be a
list. The new state is added to both the VALUES and the SPECIFIC_STATES Enum.
Attention:
 - do not use tuples or the python json parser will fail!
 - make sure only double quotes are used inside the values dictionary. No single quotes (') are allowed !
 - the second element of the list should be a standard HardwareObjectState name
 (UNKNOWN, WARNING, BUSY, READY, FAULT, OFF - see in BaseHardwareObjects.py)!
The <values> property is optional.
>>>>>>> Stashed changes
"""
import json
from enum import Enum, unique
from mxcubecore.HardwareObjects.abstract.AbstractShutter import AbstractShutter
from mxcubecore.BaseHardwareObjects import HardwareObjectState

<<<<<<< Updated upstream
__copyright__ = """ Copyright © 2023 by the MXCuBE collaboration """
=======
import logging
import json
from enum import Enum, unique
from mxcubecore.HardwareObjects.abstract.AbstractShutter import AbstractShutter
from mxcubecore.BaseHardwareObjects import HardwareObjectState

__copyright__ = """ Copyright © by the MXCuBE collaboration """
>>>>>>> Stashed changes
__license__ = "LGPLv3+"


@unique
class TangoShutterStates(Enum):
    """Shutter states definitions."""

    CLOSED = HardwareObjectState.READY, "CLOSED"
    OPEN = HardwareObjectState.READY, "OPEN"
    MOVING = HardwareObjectState.BUSY, "MOVING"
    DISABLE = HardwareObjectState.WARNING, "DISABLE"
    AUTOMATIC = HardwareObjectState.READY, "RUNNING"
    UNKNOWN = HardwareObjectState.UNKNOWN, "RUNNING"
    FAULT = HardwareObjectState.WARNING, "FAULT"
<<<<<<< Updated upstream
=======
    STANDBY = HardwareObjectState.WARNING, "STANDBY"
>>>>>>> Stashed changes


class TangoShutter(AbstractShutter):
    """TANGO implementation of AbstractShutter"""

    SPECIFIC_STATES = TangoShutterStates

    def __init__(self, name):
        super().__init__(name)
        self.open_cmd = None
        self.close_cmd = None
        self.state_channel = None

    def init(self):
        """Initilise the predefined values"""
        super().init()
        self.open_cmd = self.get_command_object("Open")
        self.close_cmd = self.get_command_object("Close")
        self.state_channel = self.get_channel_object("State")
        self._initialise_values()
        self.state_channel.connect_signal("update", self._update_value)
        self.update_state()
<<<<<<< Updated upstream

        try:
            self.config_values = json.loads(self.get_property("values"))
        except:
            self.config_values = None

    def _update_value(self, value):
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

=======
        exit() 

    def _update_value(self, value):
        """Update the value.
        Args:
            value(str): The value reported by the state channel.
        """
        super().update_value(self.value_to_enum(str(value)))

    def _initialise_values(self):
        """Add specific tango states to VALUES and, if configured
        in the xml file, to SPECIFIC_STATES"""
        values_dict = {item.name: item.value for item in self.VALUES}
        states_dict = {item.name: item.value for item in self.SPECIFIC_STATES}
        values_dict.update(
            {
                "MOVING": "MOVING",
                "DISABLE": "DISABLE",
                "STANDBY": "STANDBY",
                "FAULT": "FAULT",
            }
        )
        try:
            config_values = json.loads(self.get_property("values"))
            for key, val in config_values.items():
                if isinstance(val, (tuple, list)):
                    values_dict.update({key: val[1]})
                    states_dict.update({key: (HardwareObjectState[val[1]], val[0])})
                else:
                    values_dict.update({key: val})
        except (ValueError, TypeError) as err:
            logging.error(f"Exception in _initialise_values(): {err}")

        self.VALUES = Enum("ValueEnum", values_dict)
        self.SPECIFIC_STATES = Enum("TangoShutterStates", states_dict)

    def get_state(self):
        """Get the device state.
        Returns:
            (enum 'HardwareObjectState'): Device state.
        """
        try:
            _state = self.get_value().name
            return self.SPECIFIC_STATES[_state].value[0]
        except (AttributeError, KeyError) as err:
            logging.error(f"Exception in get_state(): {err}")
            return self.STATES.UNKNOWN

>>>>>>> Stashed changes
    def get_value(self):
        """Get the device value
        Returns:
            (Enum): Enum member, corresponding to the 'VALUE' or UNKNOWN.
        """
<<<<<<< Updated upstream
        if self.config_values:
            _val = self.config_values[str(self.state_channel.get_value())]
        else:
            _val = str(self.state_channel.get_value())
=======
        _val = str(self.state_channel.get_value())
>>>>>>> Stashed changes
        return self.value_to_enum(_val)

    def _set_value(self, value):
        if value.name == "OPEN":
            self.open_cmd()
        elif value.name == "CLOSED":
            self.close_cmd()
