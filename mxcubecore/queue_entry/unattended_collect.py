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
from mxcubecore.queue_entry.base_queue_entry import (
    BaseQueueEntry,
    QueueExecutionException,
)

__credits__ = ["MXCuBE collaboration"]
__license__ = "LGPLv3+"
__category__ = "General"


class UnattendedCollectQueueEntry(BaseQueueEntry):
    """Runs a single-sample unattended centring + data collection.

    The parent SampleQueueEntry mounts the sample before this entry executes;
    we delegate the per-sample sequence (centring -> xcentring -> collect ->
    unload) to PX1XrayCentring.unattended_collect_single().
    """

    NAME = "Unattended collect"
    DATA_MODEL = queue_model_objects.UnattendedCollect

    def __init__(self, view=None, data_model=None, view_set_queue_entry=True):
        BaseQueueEntry.__init__(self, view, data_model, view_set_queue_entry)

    def execute(self):
        BaseQueueEntry.execute(self)
        uc_model = self.get_data_model()
        # The queue hierarchy is Sample -> TaskGroup -> UnattendedCollect, so
        # get_parent() would return the TaskGroup. Walk up to the Sample node.
        sample_model = uc_model.get_sample_node()
        user_params = uc_model.get_parameters()
        try:
            HWR.beamline.xray_centring.unattended_collect_single(
                sample_model, user_params
            )
        except Exception as e:
            logging.getLogger("HWR").exception("Unattended collect failed")
            raise QueueExecutionException(str(e), self)

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        BaseQueueEntry.post_execute(self)
