import logging
import gevent
import subprocess
import os
from CatsMaint import CatsMaint
from mxcubecore import HardwareRepository as HWR
import time
from enum import Enum
from mxcubecore.BaseHardwareObjects import HardwareObjectState

log = logging.getLogger("HWR")

__copyright__ = """ Copyright © 2020 by the MXCuBE collaboration """
__license__ = "LGPLv3+"

class SpecficSates(Enum):
    """Convert exporter states to HardwareObject amd Motor states"""

    UNKNOWN = HardwareObjectState.UNKNOWN
    STANDBY = HardwareObjectState.READY
    DISABLE = HardwareObjectState.WARNING
    ALARM = HardwareObjectState.WARNING
    BUSY = HardwareObjectState.BUSY
    MOVING = HardwareObjectState.BUSY
    RUNNING = HardwareObjectState.BUSY
    INITIALIZING = HardwareObjectState.BUSY
    READY = HardwareObjectState.READY
    ON = HardwareObjectState.READY
    OFF = HardwareObjectState.READY
    CLOSED = HardwareObjectState.READY
    OPEN = HardwareObjectState.READY
    FAULT = HardwareObjectState.FAULT
    INVALID = HardwareObjectState.FAULT
    OFFLINE = HardwareObjectState.FAULT


class PX1CatsMaint(CatsMaint):
    def __init__(self, *args):
        CatsMaint.__init__(self, *args)
        self.home_opened = None
        self.powered = False
        self.events = {}
        self._SS = SpecficSates

    def init(self):
        CatsMaint.init(self)

        self.debug_cmd = self.get_property("debug_collision")
        self.video_ho = self.get_object_by_role("video")
        self._chnHomeOpened = self.add_channel({ "type": "tango",
                "name": "_chnHomeOpened", "tangoname": self.tangoname,
                "polling": "events", }, "homeOpened")
        self._chnSampleOnTool = self.get_channel_object('_chnSampleOnTool')
        self._chnGonioCollision = self.get_channel_object('_chnGonioCollision')
        self._chnDewarCollision = self.get_channel_object('_chnDewarCollision')
        self._chnGonioCollision.connect_signal("update", self.gonio_collision_changed)
        self._chnDewarCollision.connect_signal("update", self.dewar_collision_changed)
        self._chnHomeOpened.connect_signal("update", self.update_home_opened)
        self._cmdDrySoak = self.add_command({ "type": "tango",
                "name": "_cmdDrySoak", "tangoname": self.tangoname, }, "DryAndSoak")
        self._cmdReset = self.add_command({"type": "tango",
                "name": "_cmdReset", "tangoname": self.tangoname, }, "ResetError")

        self._cmdSafe = self.add_command({
            "type": "tango",
            "name": "_cmdSafe",
            "tangoname": self.tangoname,}, "Safe")

        self.cats_hwo = self.get_object_by_role("sample_changer")
        self.regulation_mode = self.get_property("regulation")
        self._soaking = self.cats_device.Position == 'Soak'
        self._tango_state_name = self._chnState.get_value().name
        print(f"Inintaial state check :{self._state}")
        self._update_state(self._SS[self._tango_state_name].value)
        print(f"State updated to :{self._state}")
        self._update_running_state(self.cats_device.pathRunning)
        self._update_powered_state(self.cats_cats.Powered)
        self._update_regulation_state(self.cats_cats.LN2Regulating)

    def send_command (self, cmd_name, args = None):
        cmds_menu= {"powerOn": self.cats_device.PowerON,
                    "powerOff": self.cats_device.PowerOFF,
                    "home": self.cats_device.HomeOpen,
                    "openlid1": self.cats_device.OpenLid,
                    "closelid1": self.cats_device.CloseLid,
                    "dry": self._cmdDrySoak,
                    "soak": self.cats_device.Soak,
                    "clear_memory": self.cats_device.ClearMemory,
                    "reset": self.cats_device.ResetError,
                    "back": None,
                    "safe": self._cmdSafe,
                    "abort": self.cats_device.Abort,
                    }

        try:
            cmd = cmds_menu.get(cmd_name, None)
            cmd()
        except Exception as e:
            logging.getLogger().error(f"Command: {cmd_name} not found! Consider adding it to cmds_menu\{e}")
        time.sleep(3)
        self._update_global_state()

    def is_string_true (self, string):
        i = str(string) in ["True","true"]
        return i

    def get_global_state(self):
        _ready = self._state.name in ("READY", "ON", "STANDBY", "OFF")
        gtg = _ready and self.cats_cats.Powered

        state_dict = {
            "toolopen": self.cats_device.toolOpen,
            "powered": self.cats_cats.Powered,
            "running":self.cats_device.pathRunning,
            "regulating":self.cats_cats.LN2Regulating,
            "lid1": self.cats_device.isLidClosed, # True if lid is closed
            "state": self._state.name,
            "homeopen": self.cats_device.homeOpened,
        }

        nr_gtg = (not state_dict["running"]) and gtg
        cmd_state = {
            "powerOn": (not state_dict["powered"] ) and _ready,
            "powerOff":  state_dict["powered"] and _ready,
            "regulon":  (not self._regulating) and _ready,
            "openlid1":   state_dict["lid1"] and (not self.cats_device.Position == 'Soak')and  gtg,
            "closelid1": ( not (state_dict["lid1"] or self.cats_device.Position == 'Soak')) and gtg,
            "dry":  nr_gtg,
            "soak": not self.cats_device.Position == 'Soak' and nr_gtg,
            "home": nr_gtg,
            "back": nr_gtg,
            "safe": nr_gtg,
            "clear_memory": False,
            "reset": True,
            "abort": True,
        }
        message = self._message
        return state_dict, cmd_state, message

    def is_gonio_collision(self):
        return self._chnGonioCollision.get_value()

    def update_home_opened(self, value):
        if value != self.home_opened:
            self.home_opened = value
            self._update_global_state()

    def get_loaded_state(self):
        lid_loaded = self._chnLidLoadedSample.get_value()
        num_loaded = self._chnNumLoadedSample.get_value()

        logging.getLogger("HWR").debug("(loaded status) lid is %s / num is %s" % (lid_loaded,num_loaded))
        if lid_loaded not in [None,-1] and num_loaded not in [None,-1]:
            has_loaded_sample = True
        else:
            has_loaded_sample = False
        return has_loaded_sample

    def run_home_open(self, wait=False):
        has_loaded_sample = self.get_loaded_state()
        if has_loaded_sample:
            unload = True
        else:
            unload = False
        self._executeTask(wait, self._doHomeOpen,unload)

    def _updateMessage(self, value):
        log.debug(" PX1CatsMaint.py - updating message %s" % value)

        # Normally the collisions are detected now with
        # corresponding bits di_VI91 (gonio), diVI92(dewar)
        # this is extra verification on Cats message attribute

        self.events['debug_unmount'] = None
        self.events['collision'] = None

        if value != self._message:
            log.debug("   - is a new message")
            if "collision" in value.lower():
                log.debug("   - is a collision")
                if 'dewar' in value.lower():
                    location = 'dewar'
                elif 'gonio' in value.lower():
                    location = 'gonio'
                else:
                    location = 'unknown'

                self.collisionDetected(location, msg=value)
            elif "WAIT for DIF_OK" in value:
                self.events['debug_unmount'] = True
                self.emit('debugUnmountRequired')

        CatsMaint._updateMessage(self,value)

    def get_events(self):
        return self.events

    def gonio_collision_changed(self, value):
        if value:
           self.collisionDetected("gonio")

    def dewar_collision_changed(self, value):
        if value:
           self.collisionDetected("dewar")

    def _update_powered_state(self, value):
        log.debug("PX1CatsMaint.py - powered state changed, it is %s" % value)
        self.powered = value
        CatsMaint._update_powered_state(self,value)

    def collisionDetected(self, location, msg=None):
        gevent.sleep(0.2) # leave time for polling to update values. is this necessary
        poweron = self.powered
        sample_on_tool = self._chnSampleOnTool.get_value()

        self.events['collision'] = [location, poweron, sample_on_tool]

        if self.video_ho is not None:
            if location == 'dewar':
                self.video_ho.select_camera("dewar", process="collision")
            else:
                self.video_ho.select_camera("head", process="collision")

        self.emit('collisionDetected', location, poweron, sample_on_tool, msg )

    def do_debug_collision(self, caller=None):

        self.caller = caller

        self.video_ho.select_camera("robot", process="debug")

        if self.debug_cmd is not None:
            if os.path.exists(self.debug_cmd):
                self.debug_task = gevent.spawn(self.debug_collision)
                self.debug_task.link(self.debug_collision_done)
                self.debug_task.link_exception(self.debug_collision_error)
                return

        log.error("PX1CatsMaint.py - debug command not found: %s " % str(self.debug_cmd))

    def debug_collision(self):
        log.debug("PX1CatsMaint.py - debugging a collision condition")
        p = subprocess.Popen(self.debug_cmd, stdout=subprocess.PIPE)

        while True:
            oline=p.stdout.readline()
            if not oline:
                break

            if self.caller is not None:
                self.caller.new_debug_msg(oline.strip())

            log.debug("DEBUG collision: %s" % oline.strip())

        self.debug_collision_done()

    def debug_collision_done(self, t1=None):
        log.debug("PX1CatsMaint.py - debugging collision finished")
        self.caller.new_debug_msg("debug collision finished")

    def debug_collision_error(self, t1=None):
        log.debug("PX1CatsMaint.py - debugging collision finished with error")
        self.caller.new_debug_msg("debug collision finished with error")


    def _do_home_open(self, unload=False):
        if unload and self.loaded:
            logging.getLogger("HWR").debug("Unloading sample first")
            self.cats_hwo._do_unload()
            time.sleep(3)
            while HWR.beamline.sample_changer._is_device_busy():
                time.sleep(0.3)

        logging.getLogger("HWR").debug("Running the home command (home/open) now")
        self._cmdHome()

    def _do_dry_soak(self):
        self._cmdDrySoak()

    def _do_reset(self):
        logging.getLogger("HWR").debug("PX1CatsMaint: executing the _do_reset function")
        self._cmdReset()


    def is_regulation_disabled(self):
        try:
            if self.regulation_mode is not None and \
                self.regulation_mode.lower() == 'disabled':
                return True
        except:
            pass

        return False
