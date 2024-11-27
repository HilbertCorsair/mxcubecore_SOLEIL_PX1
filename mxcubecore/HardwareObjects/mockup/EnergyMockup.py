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

"""Mockup class for testing purposes"""

import time

from mxcubecore.HardwareObjects.abstract.AbstractEnergy import AbstractEnergy
from mxcubecore.HardwareObjects.mockup.ActuatorMockup import ActuatorMockup

# Default energy value (keV)
DEFAULT_VALUE = 12.5
# Default energy limits (keV)
DEFAULT_LIMITS = (5.5, 19)


class EnergyMockup(ActuatorMockup, AbstractEnergy):
    """Energy Mockup class"""

    def init(self):
        """Initialise default properties"""
        super(EnergyMockup, self).init()
        self.energy_channel = self.get_channel_object("energy")
        self.state_channel = self.get_channel_object("state")
        self.tunable = self.get_property("tunable")
        self.default_energy = self.get_property("default_energy")
        self.minimum_energy = self.get_property("min_energy")
        self.maximum_energy = self.get_property("max_energy")
        self.energy = self.energy_channel.value #default_energy
        if None in self.get_limits():
            self.update_limits(DEFAULT_LIMITS)
        if self.default_value is None:
            print("YES self.default_value is NONE. This comes from inside the if loop in the EnergyMockup class init------\n\n\n")
            self.default_value = self.energy#DEFAULT_VALUE
            self.update_value(self.energy) #(DEFAULT_VALUE)
        self.update_state(self.STATES.READY)
        self.value = self.energy_channel.get_value()#default_energy
        #self.update_value(value = self.value)
        #DEFAULT_VALUE = self.energy

    def get_limits(self):
        return (self.minimum_energy, self.maximum_energy)

    def _move(self, value):
        """Simulated energy change
        Args:
            value (float): target energy
        """
        start_pos = self.energy #get_value()
        """if value and start_pos :
            step = -1 if value < start_pos else 1
            for _val in range(int(start_pos) + step, int(value) + step, step):
                time.sleep(0.2)
                try:
                    self.update_value(_val)
                except:
                    print ('Oh No ! The _move() method is EXCEPTIONALL !')
        time.sleep(5)
        self.update_state(self.STATES.READY)
        """
        self.energy_channel.set_value(value) 
        self.update_state(self.STATES.READY)

        return value
