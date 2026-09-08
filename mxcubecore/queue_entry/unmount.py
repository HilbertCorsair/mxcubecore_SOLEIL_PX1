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


class UnmountQueueEntry(BaseQueueEntry):
    """Unattended pipeline phase: clear graphics and, on the last sample, unload.

    Runs PX1XrayCentring.finalize_session(), which clears the SampleView shapes
    and - only when no further sample will be mounted in this run - unloads the
    pin. Leaving it on the goniometer is what turns the next sample's mount into
    a chained load (CATS Exchange) instead of an unload followed by a plain
    load: half the transfers, half the dry/soak cycles, and none of the tight
    unload -> load turnaround. This phase runs regardless of whether the earlier
    phases found spots.
    """

    NAME = "Unmount"
    DATA_MODEL = queue_model_objects.Unmount

    def __init__(self, view=None, data_model=None, view_set_queue_entry=True):
        BaseQueueEntry.__init__(self, view, data_model, view_set_queue_entry)

    def execute(self):
        BaseQueueEntry.execute(self)
        log = logging.getLogger("HWR")
        xc = HWR.beamline.xray_centring
        sample_model = self.get_data_model().get_sample_node()
        log.info("[UC] UnmountQueueEntry.execute reached")

        last = self._is_last_sample()
        if last:
            log.info(
                "[UC] sample %s is the last of this run, unloading",
                getattr(sample_model, "loc_str", sample_model),
            )
        else:
            log.info(
                "[UC] another sample follows %s, leaving the pin on the "
                "goniometer for the chained load",
                getattr(sample_model, "loc_str", sample_model),
            )

        try:
            xc.finalize_session(sample_model, unload=last)
        except Exception:
            log.exception("[UC] unmount failed")

    def _is_last_sample(self):
        """True when no further sample will be mounted in this run.

        Decided the same way QueueManager.__execute_entry decides what to run -
        on is_enabled() and nothing else. Deliberately no is_executed() filter:
        a sample re-enabled for a second run still carries is_executed() == True
        on its model, and the manager will run it regardless.
        """
        qm = self.get_queue_controller()
        if qm is None:
            return True

        # Single-entry execution (PUT /queue/<sid>/<tindex>/execute) runs this
        # entry's subtree and nothing else, so the pin has to come off here.
        # The single-*sample* route disables the other sample entries and starts
        # a normal whole-queue run, which the scan below already handles.
        if getattr(qm, "_run_root_entry", None) is not None:
            return True

        entry = self
        while isinstance(entry.get_container(), BaseQueueEntry):
            entry = entry.get_container()

        top = qm.get_queue_entry_list()
        try:
            index = top.index(entry)
        except ValueError:
            return True

        for later in top[index + 1:]:
            # A sample entry with no enabled task node mounts nothing:
            # SampleQueueEntry.execute() returns early when it has no children.
            if later.is_enabled() and any(
                child.is_enabled() for child in later.get_queue_entry_list()
            ):
                return False

        return True

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        BaseQueueEntry.post_execute(self)

    def get_type_str(self):
        return "Unmount"
