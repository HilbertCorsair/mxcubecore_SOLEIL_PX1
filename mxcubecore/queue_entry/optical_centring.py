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


import logging

import gevent

from mxcubecore import HardwareRepository as HWR
from mxcubecore.queue_entry.base_queue_entry import BaseQueueEntry

__credits__ = ["MXCuBE collaboration"]
__license__ = "LGPLv3+"
__category__ = "General"


class OpticalCentringQueueEntry(BaseQueueEntry):
    """
    Entry for automatic sample centring with lucid
    """

    def __init__(self, view=None, data_model=None):
        BaseQueueEntry.__init__(self, view, data_model)

    def execute(self):
        BaseQueueEntry.execute(self)

        # PX1 unattended pipeline: when a zoom level is set, set the zoom and
        # run the automatic (murko) centring via the xray_centring HO so the
        # zoom1/zoom2 settling matches the legacy driver. Falls back to the
        # generic diffractometer centring when no zoom is requested.
        zoom = getattr(self.get_data_model(), "zoom", None)
        xc = getattr(HWR.beamline, "xray_centring", None)
        if zoom and xc is not None and hasattr(xc, "run_optical_centring"):
            settle = getattr(self.get_data_model(), "zoom_settle", 10)
            # Robust for the unattended pipeline: a centring fault must not
            # abort the queue (the later phases skip and Unmount still runs).
            try:
                xc.run_optical_centring(zoom, settle=settle)
            except Exception:
                logging.getLogger("HWR").exception(
                    "[UC] optical centring (%s) failed", zoom
                )
            return

        HWR.beamline.diffractometer.automatic_centring_try_count = (
            self.get_data_model().try_count
        )

        HWR.beamline.diffractometer.start_centring_method(
            HWR.beamline.diffractometer.CENTRING_METHOD_AUTO, wait=True
        )

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        self.get_view().set_checkable(False)
        BaseQueueEntry.post_execute(self)

    def get_type_str(self):
        return "Optical automatic centring"
