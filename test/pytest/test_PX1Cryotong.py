import pytest
from tango import DeviceProxy, Except
from tango.server import Device, attribute, command
from tango.test_context import DeviceTestContext
from mxcubecore.HardwareObjects.mockup.PX1Cryotong import PX1Cryotong
from mxcubecore.HardwareObjects.mockup.PX1CatsMaint import PX1CatsMaint
from gevent.event import Event
import PyTango
from mxcubecore.CommandContainer import CommandContainer
import time

tangoname = "i10-c-cx1/ex/catscryotong"
cats_name = "i10-c-cx1/ex/cats"
device = PyTango.DeviceProxy(cats_name)
device2 = PyTango.DeviceProxy(tangoname)
def test_cats():
    
    maint = PX1CatsMaint("maint")
    maint._chnPowered = maint.add_channel(
                {
                    "type": "tango",
                    "name": "_chnPowered",
                    "tangoname": cats_name,
                    "polling": 300,
                },
                "Powered",)
    maint._test_lid_val = False
    def update_test_lid_val(val):
        maint._test_li_test_lid_vald_val = val
        maint.emit("lid1StateChanged", (val,))
        maint._update_global_state()
    

    maint._chnLid1State = maint.add_channel({ "type": "tango", 
                "name": "_chnLid1State", "tangoname": cats_name, 
                "polling": 1000, 
                }
                ,"di_Lid1Open")

    maint._chnLid1State.connect_signal("update", update_test_lid_val)


    c= 0
    while c <= 5000:
        print (f"Powered state:\nVia ChannelObject --- > {maint._chnPowered.get_value()} of type: {type(maint._chnPowered.get_value())}\nVia  DeviceProxy ---> {device.Powered}")
        print (f"LIDS state:\nVia ChannelObject --- > {maint._chnLid1State.get_value()} of type: {type(maint._chnLid1State.get_value())}\nVia  DeviceProxy ---> {device2.isLidClosed}")
        print (f"Testing Lid state via connect_signal\nLid !Lid coloed sate is ---> {maint._test_lid_val}")

        time.sleep(1)
        
        c+=1

        
if __name__ == '__main__':
    test_cats()
    
   


