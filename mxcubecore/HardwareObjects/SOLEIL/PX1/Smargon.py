
import logging
import time

from HardwareRepository.BaseHardwareObjects import Device
from gevent import Timeout
import gevent

class Smargon(Device):

    default_polling = "events"
    position_threshold = 0.001

    motors = ['chi','omega','phi','xOffset','yOffset','zOffset','x','y','z', 'velocity']

    signals = {}
    for motor_name in motors:
        signals[motor_name] = '%sPositionChanged' % motor_name

    def __init__(self,name):
        Device.__init__(self,name)

        self.motor_channels = {}
        self.motor_positions = {}
        self.motor_limits = {}

        self.backlash_pending = {}
        self.backlash_task_running = False

        self.slots = {
           'phi': self.phi_position_changed,
           'chi': self.chi_position_changed,
           'omega': self.omega_position_changed,
           'xOffset': self.xoff_position_changed,
           'yOffset': self.yoff_position_changed,
           'zOffset': self.zoff_position_changed,
           'x': self.x_position_changed,
           'y': self.y_position_changed,
           'z': self.z_position_changed,
           'velocity': self.velocity_position_changed,
        }

    def _init(self):

        self.device_name = self.getProperty("tangoname")
        self.polling = self.getProperty("polling")

        if self.polling is None:
            self.polling = self.default_polling
            logging.getLogger("HWR").debug("Smargon. using default polling to %s" % (self.polling))

        self._state_chan = self.addChannel({
             "type": "tango", "name": "_state_chan",
                    "tangoname": self.device_name, "polling": "events",
        }, "State")

        self._freeze_chan = self.addChannel({
             "type": "tango", "name": "_freeze_chan",
                    "tangoname": self.device_name, 
        }, "freeze")

        self._stop_cmd = self.addCommand({
            "type": "tango",
            "name": "_stop_cmd",
            "tangoname": self.device_name,
        }, "Stop")

        
        for motor_name in self.motors:
            chan = self.addChannel({ "type": "tango", "name": "_%s_chan" % motor_name,
                    "tangoname": self.device_name, "polling": "events",
                   }, motor_name)
            self.motor_channels[motor_name] = chan
            chan.connectSignal("update", self.slots[motor_name])

        self._state_chan.connectSignal("update", self.state_changed)

    def connectNotify(self, signal):
        if signal == 'stateChanged':
             self.state_changed(self.get_state())
        elif signal in ['deviceReady', 'deviceNotReady']:
            pass
        else:
            motor = self.get_motor_from_signal(signal)
            pos = self.get_position(motor)  
            self.slots[motor](pos)

    def state_changed(self, newvalue):
        newstate = str(newvalue)
        
        self.emit('stateChanged', newstate)
        self.state = newstate

    def phi_position_changed(self, newpos):
        name = 'phi'
        self.position_changed(name, newpos)

    def chi_position_changed(self, newpos):
        name = 'chi'
        self.position_changed(name, newpos)

    def omega_position_changed(self, newpos):
        name = 'omega'
        self.position_changed(name, newpos)

    def xoff_position_changed(self, newpos):
        name = 'xOffset'
        self.position_changed(name, newpos)

    def yoff_position_changed(self, newpos):
        name = 'yOffset'
        self.position_changed(name, newpos)

    def zoff_position_changed(self, newpos):
        name = 'zOffset'
        self.position_changed(name, newpos)

    def x_position_changed(self, newpos):
        name = 'x'
        self.position_changed(name, newpos)

    def y_position_changed(self, newpos):
        name = 'y'
        self.position_changed(name, newpos)

    def z_position_changed(self, newpos):
        name = 'z'
        self.position_changed(name, newpos)

    def velocity_position_changed(self, newpos):
        name = 'velocity'
        self.position_changed(name, newpos)

    def position_changed(self, motor_name, newpos):
        if not newpos:
            return

        oldpos = self.motor_positions.get(motor_name,999)

        if abs(newpos - oldpos) > self.position_threshold:
            self.motor_positions[motor_name] = newpos
            signal = self.signals[motor_name]
            self.emit(signal, newpos)
            self.emit('stateChanged', self.state)
        
    def get_motors(self):
        return self.motors

    def get_signal_name(self, motor_name):
        return self.signals[motor_name]

    def get_motor_from_signal(self, signal_name):
        for motor in self.signals:
            if self.signals[motor] == signal_name:
                return motor
        return None

    def get_state(self):
        state = str( self._state_chan.getValue() )
        if state != self.state:
            self.state_changed(state)
        return state

    def get_position(self, motor_name):
        motor_chan = self.motor_channels[motor_name]
        motor_position = motor_chan.getValue()
        self.motor_positions[motor_name] = motor_position
        return motor_position
        
    def move(self, motor_name, target_pos, backlash=None, wait=False):
        logging.getLogger("HWR").debug("Smargon.py - Moving motor %s to: %.3f" % (motor_name, target_pos))
        motor_chan = self.motor_channels[motor_name]

        def sign(x):
            if x ==0: return 0

            return int(x/abs(x))

        do_backlash = False 

        if backlash is not None:
             logging.getLogger("HWR").debug("Smargon.py - in move function found backlash value")
             current_pos = motor_chan.getValue()
             move_distance = target_pos - current_pos
 
             if abs(move_distance) > 5e-3:
                 if sign(move_distance) != sign(backlash):
                     do_backlash = True 
                     final_pos = target_pos
                     target_pos -= backlash
                     self.backlash_pending[motor_name] = final_pos
                     logging.getLogger("HWR").debug("Smargon.py - Applying backlash for motor %s" % motor_name)
                     logging.getLogger("HWR").debug("Smargon.py -   moving first to: %s then %s" % (target_pos, final_pos))
        _t0 = time.time()
        motor_chan.setValue(target_pos)
        logging.getLogger("HWR").debug("Smargon.py - Time to move motor %s: %.3f sec" % (motor_name, (time.time()-_t0)))                
        if do_backlash and not self.backlash_task_running:
            self.start_backlash_task()

        if wait: 
            self.wait_ready()
        #logging.getLogger("HWR").debug("Smargon.py - Time to exit move motor %s" % (motor_name))                

    def start_backlash_task(self):
        if not self.backlash_task_running:
            logging.getLogger("HWR").debug("Smargon.py - starting backlash task")
            self.backlash_task_running = True
            self.backlash_task = gevent.spawn(self._do_backlash)
            self.backlash_task.link(self.backlash_task_done)
        else:
            logging.getLogger("HWR").debug("Smargon.py - backlash task already running")

    def _do_backlash(self):
        logging.getLogger("HWR").debug("waiting for motors to stop before applying backlash correction")
        self._wait_ready()

        logging.getLogger("HWR").debug("motors are now stopped. starting pending backlash movements") 
        for motor_name, target_pos in self.backlash_pending.items():
            logging.getLogger("HWR").debug("   backlash finish. moving %s to %s" % (motor_name, target_pos))
            self.move(motor_name, target_pos, backlash=None, wait=False)

        self.backlash_pending = {}
        return True

    def backlash_task_done(self, result):
        self.backlash_task_running = False

    def move_motors(self, motor_pos_dict, wait=False):

        self.wait_ready()

        self.set_freeze(True)
        try:
            for motor_name, target_pos in motor_pos_dict.items():
                self.move(motor_name, target_pos, wait=False)
        except:
            import traceback
            logging.getLogger("HWR").error("Smargon: Error moving motor %s" % motor_name)
            logging.getLogger("HWR").error( traceback.format_exc())

        self.set_freeze(False)
        self.wait_notready()
            
        if wait:
            self.wait_ready()

        logging.getLogger("HWR").error("Smargon: Finished moving motors")

    def set_freeze(self, onoff):
        logging.getLogger("HWR").debug( "Smargon. Setting freeze to: %s" % onoff)
        self._freeze_chan.setValue(onoff) 

    def wait_notready(self, timeout=5):
        t0 = time.time() 

        while self.is_ready():
            if (time.time() - t0) > timeout:
                raise Timeout
            gevent.sleep(0.03)

    def wait_ready(self,timeout=20):
        t0 = time.time() 

        while self.backlash_task_running: 
            logging.getLogger("HWR").debug("waiting for backlash correction to finish")
            if (time.time() - t0) > timeout:
                raise Timeout
            gevent.sleep(0.03)

        self._wait_ready(timeout)

    def _wait_ready(self, timeout=20):
        gevent.sleep(0.1)

        t0 = time.time() 

        while not self.is_ready():
            if (time.time() - t0) > timeout:
                logging.getLogger("HWR").debug("SMARGON TIMEOUT" )
                raise Timeout
            gevent.sleep(0.03)

    def is_ready(self):
        return self.get_state() == "STANDBY"

    def get_limits(self, motor_name, update=False):
        if not self.motor_limits or (update is True):
            for _motor_name in self.motor_channels:
                chan = self.motor_channels[_motor_name]
                info = chan.getInfo()
                min_value = float(info.min_value)
                max_value = float(info.max_value)
                self.motor_limits[_motor_name] = [min_value, max_value]

        return self.motor_limits[motor_name]

    def stop(self):
        if self.backlash_task_running: 
           self.backlash_task.kill(block=False) 
           self.backlash_task_running = False
        self._stop_cmd()
  
def test_hwo(hwo):
    t0 = time.time()

    print("State is: %s" % hwo.get_state())
    print hwo.get_signal_name("chi")

    for motor in hwo.get_motors():
        print("Motor % 7s is % -4.3f" % (motor,hwo.get_position(motor)))

    print("Elapsed time: %s" % (time.time() - t0))