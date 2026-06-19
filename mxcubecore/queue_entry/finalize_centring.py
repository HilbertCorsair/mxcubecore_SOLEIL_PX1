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

from mxcubecore import HardwareRepository as HWR
from mxcubecore.model import queue_model_objects
from mxcubecore.queue_entry.base_queue_entry import BaseQueueEntry

__credits__ = ["MXCuBE collaboration"]
__license__ = "LGPLv3+"
__category__ = "General"


class FinalizeCentringQueueEntry(BaseQueueEntry):
    """Unattended pipeline phase: fit the accumulated scans and centre.

    Runs PX1XrayCentring.finalize_centring() (fit over all accumulated mesh +
    helical results, move to the centred position, register the point, save the
    report). Skips if no spots were found by the scan phases.
    """

    NAME = "Finalize centring"
    DATA_MODEL = queue_model_objects.FinalizeCentring

    def __init__(self, view=None, data_model=None, view_set_queue_entry=True):
        BaseQueueEntry.__init__(self, view, data_model, view_set_queue_entry)

    def execute(self):
        BaseQueueEntry.execute(self)
        log = logging.getLogger("HWR")
        xc = HWR.beamline.xray_centring
        log.info("[UC] FinalizeCentringQueueEntry.execute reached")

        if not getattr(xc, "found_spots", False):
            log.info("[UC] finalize centring skipped (no spots)")
            return

        try:
            xc.finalize_centring()
        except Exception:
            log.exception("[UC] finalize centring failed")
            xc.found_spots = False

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        BaseQueueEntry.post_execute(self)

    def get_type_str(self):
        return "Finalize centring"
