# encoding: utf-8
#
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
#  You should have received a copy of the GNU General Lesser Public License
#  along with MXCuBE. If not, see <http://www.gnu.org/licenses/>.
"""The unattended collect pipeline: one task group, its tasks executed in order."""

from types import SimpleNamespace
from unittest.mock import Mock

import gevent
import pytest

from mxcubecore import queue_entry as qe
from mxcubecore.HardwareObjects.SOLEIL.PX1.PX1UnattendedCollect import (
    PX1UnattendedCollect,
)
from mxcubecore.model import queue_model_objects as qmo
from mxcubecore.queue_entry.base_queue_entry import QUEUE_ENTRY_STATUS

METHODS = [task[1] for task in qmo.UNATTENDED_TASKS]
# The methods of the unattended_collect object, "collect" is a data collection
HWOBJ_METHODS = [method for method in METHODS if method != "collect"]


def _add(beamline, parent, parent_entry, model):
    """Add model and its queue entry, the way mxcubeweb does."""
    beamline.queue_model.add_child(parent, model)
    entry = qe.MODEL_QUEUE_ENTRY_MAPPINGS[type(model)](Mock(), model)
    model.set_enabled(True)
    entry.set_enabled(True)
    parent_entry.enqueue(entry)
    return entry


@pytest.fixture
def group(beamline):
    beamline.unattended_collect.task_time = 0
    # The DataCollectionQueueEntry of this branch reads the session from LIMS
    beamline._objects["lims"] = SimpleNamespace(
        session_manager=SimpleNamespace(active_session=SimpleNamespace(session_id=1)),
        _store_data_collection_group=lambda group_data: 1,
    )
    # Session.get_archive_directory is broken, PX1 overrides it (SOLEILSession)
    beamline.session.get_archive_directory = lambda: "/tmp/archive"
    sample = qmo.Sample()
    sample.loc_str = "1:01"
    beamline.queue_model.add_child(beamline.queue_model.get_model_root(), sample)
    sample_entry = qe.SampleQueueEntry(Mock(), sample)
    beamline.queue_manager.enqueue(sample_entry)

    group = qmo.UnattendedCollect()
    group_entry = _add(beamline, sample, sample_entry, group)

    for label, method, kwargs, needs_spots in qmo.UNATTENDED_TASKS:
        if method == qmo.UnattendedDataCollection.method:
            task = qmo.UnattendedDataCollection()
            task.get_path_template().base_prefix = "prefix"
        else:
            task = qmo.UnattendedTask(label, method, kwargs, needs_spots)
        _add(beamline, group, group_entry, task)

    yield group
    beamline.queue_manager.clear()


def _run(beamline, group, calls):
    """Execute the task group, recording what is called and the group context."""
    hwobj = beamline.unattended_collect

    def record(name):
        method = getattr(type(hwobj), name)

        def wrapper(context, **kwargs):
            calls.append((name, dict(context)))
            if name == "unmount":
                return None
            return method(hwobj, context, **kwargs)

        return wrapper

    def collect(owner, param_list):
        calls.append(("collect", dict(group.context)))
        param_list[0]["xds_dir"] = ""
        return gevent.spawn(lambda: None)

    if not isinstance(hwobj, PX1UnattendedCollect):
        for name in HWOBJ_METHODS:
            setattr(hwobj, name, record(name))

    beamline.collect.collect = collect
    entry = beamline.queue_manager.get_entry_with_model(group)
    beamline.queue_manager.execute(entry)

    with gevent.Timeout(20):
        while beamline.queue_manager.is_executing():
            gevent.sleep(0.05)

    return [
        beamline.queue_manager.get_entry_with_model(p) for p in group.get_children()
    ]


def test_one_group_with_the_tasks_in_order(beamline, group):
    children = group.get_children()
    manager = beamline.queue_manager

    assert isinstance(manager.get_entry_with_model(group), qe.TaskGroupQueueEntry)
    assert [task.method for task in children] == METHODS
    assert [type(task) for task in children] == [
        qmo.UnattendedDataCollection if method == "collect" else qmo.UnattendedTask
        for method in METHODS
    ]
    assert isinstance(
        manager.get_entry_with_model(group.get_data_collection()),
        qe.DataCollectionQueueEntry,
    )


def test_tasks_run_in_order_and_pass_on_their_results(beamline, group):
    calls = []
    entries = _run(beamline, group, calls)
    seen = dict(calls)
    acq = group.get_data_collection().acquisitions[0]

    assert [name for name, _ in calls] == METHODS
    assert "grid_id" not in seen["grid_scan"]
    assert seen["line_scan"]["found_spots"]
    assert seen["line_scan"]["grid_id"] in beamline.sample_view.shapes
    assert "point_id" in seen["collect"]
    assert (
        acq.acquisition_parameters.centred_position.as_dict()
        == qmo.CentredPosition(seen["collect"]["centred_position"]).as_dict()
    )
    assert all(e.status == QUEUE_ENTRY_STATUS.SUCCESS for e in entries)
    assert all(e.started_at <= e.ended_at for e in entries)


def test_no_spots_skips_to_unmount(beamline, group):
    beamline.unattended_collect.found_spots = False
    calls = []
    entries = _run(beamline, group, calls)

    assert [name for name, _ in calls] == [*METHODS[:3], "unmount"]
    assert [e.status for e in entries[2:7]] == [QUEUE_ENTRY_STATUS.SKIPPED] * 5
    assert entries[7].status == QUEUE_ENTRY_STATUS.SUCCESS


class FakeXrayCentring:
    """Records the PX1XrayCentring calls of PX1UnattendedCollect."""

    def __init__(self, spots):
        self.spots = spots
        self.calls = []

    def run_optical_centring(self, zoom):
        self.calls.append(("optical", zoom))
        return True

    def begin_centring_session(self, prefix):
        self.calls.append(("begin", prefix))

    def run_grid_scan(self):
        self.calls.append(("grid",))
        return self.spots

    def run_line_scan(self, index):
        self.calls.append(("line", index))
        return True

    def finalize_centring(self):
        self.calls.append(("finalize",))

    def finalize_session(self, unload=True):
        self.calls.append(("end", unload))


@pytest.mark.parametrize("spots", [True, False])
def test_px1_adapter(beamline, group, spots):
    xray_centring = FakeXrayCentring(spots)
    px1 = PX1UnattendedCollect("px1_unattended_collect")
    px1.scan_attempts = 2
    beamline._objects["xray_centring"] = xray_centring
    beamline._objects["unattended_collect"] = px1
    calls = []
    entries = _run(beamline, group, calls)

    start = [("optical", "zoom1"), ("optical", "zoom2"), ("begin", "prefix")]

    if spots:
        assert xray_centring.calls == [
            *start,
            ("grid",),
            ("line", 0),
            ("line", 1),
            ("finalize",),
            ("end", True),
        ]
        assert [name for name, _ in calls] == ["collect"]
        assert all(e.status == QUEUE_ENTRY_STATUS.SUCCESS for e in entries)
    else:
        assert xray_centring.calls == [*start, ("grid",), ("grid",), ("end", True)]
        assert calls == []
        assert [e.status for e in entries[2:7]] == [QUEUE_ENTRY_STATUS.SKIPPED] * 5
