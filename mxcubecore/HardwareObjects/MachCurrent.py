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
#  You should have received a copy of the GNU General Lesser Public License
#  along with MXCuBE. If not, see <http://www.gnu.org/licenses/>.

"""Machine Current Tango Hardware Object
Example XML:
<object class = "MachCurrent">
  <username>label for users</username>
  <tangoname>orion:10000/fe/id(or d)/xx</tangoname>
  <channel type="tango" name="operatorMessage" polling="2000">SR_Operator_Mesg</channel>
  <channel type="tango" name="current" polling="2000">SR_Current</channel>
  <channel type="tango" name="fillingMode" polling="2000">SR_Filling_Mode</channel>
  <channel type="tango" name="lifetime" polling="2000">SR_Refill_Countdown</channel>
</object>
"""

import logging
from PyTango import DeviceProxy
from mxcubecore.HardwareObjects.abstract.AbstractMachineInfo import AbstractMachineInfo

__copyright__ = """ Copyright © 2010-2023 by the MXCuBE collaboration """
__license__ = "LGPLv3+"


class MachCurrent(AbstractMachineInfo):
    """Tango implementation"""

    def __init__(self, name):
        super().__init__(name)
        self._current = None
        self._message = None
        self._lifetime = None
        self._fillmode = None

    def init(self):
        try:
            
            self.device = DeviceProxy(self.get_property("tangoname"))
            self.current_threshold = self.get_property("current_threshold", 3)
            curr = self.get_channel_object("current")
            curr.connect_signal("update", self.value_changed)
            self.update_state(self.STATES.READY)
        except Exception as err:
            logging.getLogger("HWR").exception(err)
    
    @property
    def current(self):
        return self._current
    
    def get_current(self) -> float:
        """Read the ring current.
        Returns:
            (float): Ring current [mA]
        """
        try:
            return self.get_channel_object("current").get_value()
        except Exception as err:
            logging.getLogger("HWR").exception(err)
            print("EOL")
            return -1

    def get_message(self) -> str:
        try:
            return self.device.operatorMessage
        except Exception as err:
            logging.getLogger("HWR").exception(err)
            return ""

    def get_fill_mode(self) -> str:
        try:
            return self.device.fillingMode
        except Exception as err:
            logging.getLogger("HWR").exception(err)
            return ""
        

    def get_life_time(self) -> str:
        try:
            return self.device.lifetime
        except Exception as err:
            logging.getLogger("HWR").exception(err)
            return ""
        # Keeping aliases for backward compatibility
    getCurrent = get_current
    getMessage = get_message
    getFillMode = get_fill_mode
    getLifeTime = get_life_time

    def value_changed(self, value):
        """Get information from the control software, emit valueChanged"""
        value = value or self.get_current()

        try:
            opmsg = self.get_message()
            fillmode = self.get_fill_mode()
            fillmode = fillmode.strip()
            refill = self.get_life_time()
        except Exception as err:
            print("OH NO! ")
            logging.getLogger("HWR").exception(err)
            opmsg, fillmode, value, refill = ("", "", -1, -1)

        if opmsg and opmsg != self._message:
            values = {}
            self._message = opmsg
            self._current = self.get_current()
            self._fillmode = self.get_fill_mode()
            self._lifetime = self.get_life_time()
            values["message"] = self._message
            values["current"] = self._current
            values.update(self.get_value())
            self.update_value(values)
            logging.getLogger("user_level_log").info(self._message)
            self.emit("valueChanged", (self.current))
