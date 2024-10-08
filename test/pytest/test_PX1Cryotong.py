import pytest
from tango import DeviceProxy, Except
from tango.server import Device, attribute, command
from tango.test_context import DeviceTestContext
from mxcubecore.HardwareObjects.mockup.PX1CatsMaint import PX1CatsMaint
from gevent.event import Event

@pytest.fixture
def px1cats_maint():
    maint = PX1CatsMaint()
    maint._chnPowered = maint.add_channel(
                {
                    "type": "tango",
                    "name": "_chnPowered",
                    "tangoname": maint.cats_name,
                    "polling": 300,
                },
                "Powered",)
    yield maint

def test_cats_maint(px1cats_maint: PX1CatsMaint):
    """
    Test the state of PX1Cryotong when the robot is:
    * powered on
    * the arm is in home position
    """
    px1cats_maint.init()

    print (f"Cats test : {px1cats_maint._chnPowered.get_value()}")
    assert isinstance(px1cats_maint._chnPowered.get_value(), bool)
    
    
