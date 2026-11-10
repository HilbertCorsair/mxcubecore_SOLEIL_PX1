# encoding: utf-8
#
#  Project name: MXCuBE
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
"""The task rows of the queue JSON tell the TaskGroup they belong to."""

import pytest

from mxcubecore.queuelib import QueueBuilder, QueueSerializer


def _dc(subdir):
    return {
        "type": "DataCollection",
        "sampleID": "1:01",
        "parameters": {"subdir": subdir, "shape": -1},
    }


@pytest.fixture
def serializer(beamline):
    model = beamline.queue_model
    model.connect(model, "child_added", model.queue_model_child_added)
    # The test beamline.yaml has no "configuration" section, so no defaults
    beamline.config.default_acquisition_parameters = {
        "default": {
            "first_image": 1,
            "num_images": 1,
            "osc_range": 0.1,
            "exp_time": 0.1,
        }
    }
    serializer = QueueSerializer(QueueBuilder())
    serializer.queue_add_item(
        [
            {
                "type": "Sample",
                "sampleID": "1:01",
                "location": "1:01",
                "sampleName": "sample",
                "tasks": [_dc("a"), _dc("b")],
            }
        ]
    )
    yield serializer
    model.disconnect(model, "child_added", model.queue_model_child_added)
    beamline.queue_manager.clear()


def _rows(serializer):
    return serializer.queue_to_dict()["1:01"]["tasks"]


def test_one_group_per_added_task(beamline, serializer):
    rows = _rows(serializer)
    model = beamline.queue_model

    assert len({row["groupID"] for row in rows}) == len(rows) == 2

    for row in rows:
        group = model.get_node(row["groupID"])
        assert model.get_node(row["queueID"]).get_parent() is group


def test_tasks_of_one_group_share_its_id(beamline, serializer):
    first = _rows(serializer)[0]
    builder = serializer.builder
    sample, _ = beamline.queue_manager.get_entry(first["sampleQueueID"])
    group = beamline.queue_model.get_node(first["groupID"])

    dc_model = builder._create_dc()
    dc_entry = builder._attach_model_to_group(group, dc_model)
    builder.set_dc_params(dc_model, dc_entry, first, sample)

    rows = _rows(serializer)

    assert [row["groupID"] for row in rows] == [
        first["groupID"],
        first["groupID"],
        rows[2]["groupID"],
    ]
    assert rows[1]["queueID"] == dc_model._node_id


def test_rows_with_their_group_can_be_sent_back(serializer):
    row = _rows(serializer)[0]

    serializer.add_task("1:01", {**row, "queueID": -1})

    assert len(_rows(serializer)) == 3
