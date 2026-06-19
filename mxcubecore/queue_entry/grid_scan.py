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

# Number of attempts (initial + retries) for a scan before giving up and
# skipping straight to the unmount phase.
MAX_SCAN_ATTEMPTS = 2


class GridScanQueueEntry(BaseQueueEntry):
    """Unattended pipeline phase: begin the centring session + run the mesh.

    Calls PX1XrayCentring.begin_centring_session() (reset state, build the grid
    shape, assemble collect parameters, prepare the report + snapshots) then
    run_grid_scan() (2D mesh scan + analysis), retrying the mesh up to
    MAX_SCAN_ATTEMPTS times when no spots are found. On persistent failure or an
    error it sets found_spots=False and returns normally, so the queue advances
    through the (skipped) remaining phases to Unmount rather than aborting.
    """

    NAME = "Grid scan"
    DATA_MODEL = queue_model_objects.GridScan

    def __init__(self, view=None, data_model=None, view_set_queue_entry=True):
        BaseQueueEntry.__init__(self, view, data_model, view_set_queue_entry)

    def execute(self):
        BaseQueueEntry.execute(self)
        log = logging.getLogger("HWR")
        xc = HWR.beamline.xray_centring
        model = self.get_data_model()
        sample_model = model.get_sample_node()
        log.info("[UC] GridScanQueueEntry.execute reached")
        try:
            xc.begin_centring_session(sample_model, model.get_parameters())
            for attempt in range(MAX_SCAN_ATTEMPTS):
                if xc.run_grid_scan():
                    return
                log.warning(
                    "[UC] grid scan attempt %d/%d found no spots",
                    attempt + 1, MAX_SCAN_ATTEMPTS,
                )
            log.warning(
                "[UC] grid scan: no spots after %d attempts; skipping to unmount",
                MAX_SCAN_ATTEMPTS,
            )
        except Exception:
            log.exception("[UC] grid scan failed")
            xc.found_spots = False

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        BaseQueueEntry.post_execute(self)

    def get_type_str(self):
        return "Grid scan"
