
import logging
import gevent
import subprocess
import os

from CatsMaint import CatsMaint
from mxcubecore.HardwareObjects.abstract.AbstractSampleChanger import SampleChangerState

log = logging.getLogger("HWR")

class PX1CatsMaint(CatsMaint):
    def __init__(self,*args):
        CatsMaint.__init__(self, *args)
        self.home_opened = None
        self.powered = False
        self.events = {}

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

        self.cats_hwo = self.get_object_by_role("sample_changer")

        self.regulation_mode = self.get_property("regulation")
   

    def is_gonio_collision(self):
        return self._chnGonioCollision.get_value()

    def update_home_opened(self, value):
        if value != self.home_opened:
            self.home_opened = value 
            self._updateGlobalState()

    def get_global_state(self):
        state_dict, cmd_state, message = CatsMaint.get_global_state(self)
        state_dict['homeopen'] = self.home_opened
        return state_dict, cmd_state, message 

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
            #elif "WAIT for SplOn" in value:
                #self.events['debug_unmount'] = True
                #self.emit('debugUnmountRequired')

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
  
  
    def _doHomeOpen(self, unload=False):

        logging.getLogger("HWR").debug("Running the home command (home/open) now. Unload first=%s" % unload)

        if unload :
            logging.getLogger("HWR").debug("Unloading sample first")
            self.cats_hwo.unload(wait=True)
            #gevent.sleep(3)
            #trial = 0
            #while True:
            ##    gevent.sleep(0.3)
            #    if self.cats_hwo._isDeviceBusy():
            #        trial = 0
            #        continue
            #    
            #    state = SampleChangerState.tostring( self.cats_hwo._readState() )
            #    logging.getLogger("HWR").debug("cats not busy. maybe transient. wait a bit more. state is: %s" % str(state))
            #    trial += 1
#
#                if trial == 3:
#                    logging.getLogger("HWR").debug("cats not busy for 3 times. Must be true")
#                    break

        logging.getLogger("HWR").debug("Running the home command (home/open) now")
        self._cmdHome()

    def _doDrySoak(self):
        self._cmdDrySoak()

    def _doReset(self):
        logging.getLogger("HWR").debug('PX1CatsMaint: executing the _doReset function')
        self._cmdReset()

    def is_regulation_disabled(self):
        try:
            if self.regulation_mode is not None and \
                self.regulation_mode.lower() == 'disabled':
                return True
        except:
            pass
        
        return False

