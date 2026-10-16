# encoding: utf-8
#
# This file is part of MXCuBE.
#
# MXCuBE is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MXCuBE is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with MXCuBE.  If not, see <https://www.gnu.org/licenses/>.
"""The unattended collect tasks on PX1, run by PX1XrayCentring and PX1MiniDiff.

Configuration, role unattended_collect:

    <object class="SOLEIL.PX1.PX1UnattendedCollect">
      <object href="/px1-xray-centring" role="xray_centring"/>
    </object>

Without the xray_centring role HWR.beamline.xray_centring is used.
"""

from mxcubecore import HardwareRepository as HWR
from mxcubecore.HardwareObjects.abstract.AbstractUnattendedCollect import (
    AbstractUnattendedCollect,
)

__copyright__ = """ Copyright © by MXCuBE Collaboration """
__license__ = "LGPLv3+"


class PX1UnattendedCollect(AbstractUnattendedCollect):
    """Each scan runs once: a sample that shows no spots is not scanned again."""

    @property
    def xray_centring(self):
        return self.get_object_by_role("xray_centring") or HWR.beamline.xray_centring

    def optical_centring(self, context, zoom):
        return self.xray_centring.run_optical_centring(f"zoom{zoom}")

    def grid_scan(self, context):
        self.xray_centring.begin_centring_session(self._prefix())
        context["found_spots"] = self.xray_centring.run_grid_scan()
        return context["found_spots"]

    def line_scan(self, context, index):
        return self.xray_centring.run_line_scan(index)

    def finalize_centring(self, context):
        self.xray_centring.finalize_centring()
        # A copy, get_positions() returns the dictionary it keeps up to date
        context["centred_position"] = dict(HWR.beamline.diffractometer.get_positions())
        return True

    def unmount(self, context, unload=True):
        self.xray_centring.finalize_session(unload=unload)

    @staticmethod
    def _prefix():
        """File prefix of the data collection of the running unattended collect."""
        task = HWR.beamline.queue_manager.get_current_entry().get_data_model()
        return task.get_parent().get_data_collection().get_path_template().get_prefix()
