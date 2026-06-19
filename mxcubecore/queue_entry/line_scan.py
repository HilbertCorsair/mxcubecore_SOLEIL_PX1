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
from mxcubecore.queue_entry.grid_scan import MAX_SCAN_ATTEMPTS

__credits__ = ["MXCuBE collaboration"]
__license__ = "LGPLv3+"
__category__ = "General"


class LineScanQueueEntry(BaseQueueEntry):
    """Unattended pipeline phase: run one helical (line) scan.

    Skips immediately if an earlier phase found no spots (found_spots=False),
    so a failed scan short-circuits the pipeline straight to Unmount. Otherwise
    runs the helical scan for this entry's index, retrying up to
    MAX_SCAN_ATTEMPTS times when no spots are found. On persistent failure it
    leaves found_spots=False so the remaining phases skip.
    """

    NAME = "Line scan"
    DATA_MODEL = queue_model_objects.LineScan

    def __init__(self, view=None, data_model=None, view_set_queue_entry=True):
        BaseQueueEntry.__init__(self, view, data_model, view_set_queue_entry)

    def execute(self):
        BaseQueueEntry.execute(self)
        log = logging.getLogger("HWR")
        xc = HWR.beamline.xray_centring
        index = getattr(self.get_data_model(), "index", 0)
        log.info("[UC] LineScanQueueEntry.execute reached (index=%s)", index)

        if not getattr(xc, "found_spots", False):
            log.info("[UC] line scan %s skipped (no spots from earlier phase)", index)
            return

        try:
            for attempt in range(MAX_SCAN_ATTEMPTS):
                if xc.run_line_scan(index):
                    return
                log.warning(
                    "[UC] line scan %s attempt %d/%d found no spots",
                    index, attempt + 1, MAX_SCAN_ATTEMPTS,
                )
            log.warning(
                "[UC] line scan %s: no spots after %d attempts; skipping to unmount",
                index, MAX_SCAN_ATTEMPTS,
            )
        except Exception:
            log.exception("[UC] line scan %s failed", index)
            xc.found_spots = False

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        BaseQueueEntry.post_execute(self)

    def get_type_str(self):
        return "Line scan"
