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
    """Unattended pipeline phase: clear graphics and unload the sample.

    Runs PX1XrayCentring.finalize_session(), which clears the SampleView shapes
    and unloads the sample. This is the last phase and runs regardless of
    whether the earlier phases found spots, so the changer is always left empty
    for the next sample.
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
        try:
            xc.finalize_session(sample_model)
        except Exception:
            log.exception("[UC] unmount failed")

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        BaseQueueEntry.post_execute(self)

    def get_type_str(self):
        return "Unmount"
