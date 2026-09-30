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
from mxcubecore.queue_entry.base_queue_entry import (
    BaseQueueEntry,
    QueueAbortedException,
    QueueSkipEntryException,
)

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

        # PX1 unattended pipeline: set the zoom and
        # run the automatic (murko) centring via the xray_centring HO. Falls back to the
        # generic diffractometer centring when no zoom is requested.
        zoom = getattr(self.get_data_model(), "zoom", None)
        xc = getattr(HWR.beamline, "xray_centring", None)
        if zoom and xc is not None and hasattr(xc, "run_optical_centring"):
            # run_optical_centring blocks until the centring (and its final
            # motor move) is over, and refuses to start while another one
            # runs, so the next phase never overlaps this one.
            # A centring fault must not abort the queue (the later phases
            # still run and Unmount still unloads), but it must not show as
            # a success either: skip the entry, which the queue continues
            # past and the client shows as a warning.
            try:
                valid = xc.run_optical_centring(zoom)
                except Exception as ex:
                if getattr(ex, "abort_queue", False):
                    # Murko is gone: no automatic centring, so no unattended
                    # collect either. Stop the queue instead of skipping on.
                    logging.getLogger("user_level_log").error(
                        "Automatic centring impossible (%s): stopping the queue" % ex
                    )
                    raise QueueAbortedException(str(ex), self)
                logging.getLogger("HWR").exception(
                    "[UC] optical centring (%s) failed", zoom
                )
                raise QueueSkipEntryException(
                    "Optical centring (%s) failed: %s" % (zoom, ex), self
                )
            if valid is False:
                raise QueueSkipEntryException(
                    "Optical centring (%s) found no position" % zoom, self
                )
            return

        dm = HWR.beamline.diffractometer
        dm.automatic_centring_try_count = self.get_data_model().try_count
        dm.start_centring_method(dm.CENTRING_METHOD_AUTO, wait=True)

    def pre_execute(self):
        BaseQueueEntry.pre_execute(self)

    def post_execute(self):
        # Qt-only view call: in the queue-driven (web) path the view is absent
        # or a stand-in without this method, and a failure here would abort the
        # unattended pipeline before it reaches Unmount.
        view = self.get_view()
        if hasattr(view, "set_checkable"):
            try:
                view.set_checkable(False)
            except Exception:
                logging.getLogger("HWR").exception(
                    "[UC] optical centring: set_checkable failed"
                )
        BaseQueueEntry.post_execute(self)

    def get_type_str(self):
        return "Optical automatic centring"