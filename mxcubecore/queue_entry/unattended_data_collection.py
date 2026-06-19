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


class UnattendedDataCollectionQueueEntry(BaseQueueEntry):
    """Unattended pipeline phase: snapshots + data collection + autoprocessing.

    Runs PX1XrayCentring.collect_with_params() (refresh motors, two diffraction
    snapshots, do_collect), guarded by found_spots. Autoprocessing is triggered
    in post_execute, because the PX1Collect OSC/Helical hook does not trigger it
    itself (only the Characterization branch does).
    """

    NAME = "Unattended data collection"
    DATA_MODEL = queue_model_objects.UnattendedDataCollection

    def __init__(self, view=None, data_model=None, view_set_queue_entry=True):
        BaseQueueEntry.__init__(self, view, data_model, view_set_queue_entry)
        self._collected = False

    def execute(self):
        BaseQueueEntry.execute(self)
        log = logging.getLogger("HWR")
        xc = HWR.beamline.xray_centring
        self._collected = False
        log.info("[UC] UnattendedDataCollectionQueueEntry.execute reached")

        if not getattr(xc, "found_spots", False):
            log.info("[UC] data collection skipped (no spots)")
            return

        try:
            xc.collect_with_params()
            self._collected = True
        except Exception:
            log.exception("[UC] data collection failed")

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        BaseQueueEntry.post_execute(self)
        if not self._collected:
            return
        try:
            collect = HWR.beamline.collect
            collect.trigger_auto_processing(
                "standard", collect.current_dc_parameters, -1
            )
        except Exception:
            logging.getLogger("HWR").exception(
                "[UC] autoprocessing trigger failed"
            )

    def get_type_str(self):
        return "Unattended data collection"
