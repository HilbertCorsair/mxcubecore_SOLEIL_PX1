import logging
import time
import gevent
# from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore.HardwareObjects.abstract.AbstractMotor import AbstractMotor
from mxcubecore.HardwareObjects.mockup.TangoMotor import TangoMotor

from mxcubecore.HardwareObjects.mockup.Smargon import Smargon
from mxcubecore.Command.Tango import TangoCommand

log = logging.getLogger("HWR")

class SmargonAxis(AbstractMotor, Smargon):

    MOVESTARTED    = 0
    NOTINITIALIZED = 0
    UNUSABLE       = 0
    READY          = 2
    MOVING         = 4
    ONLIMIT       = 1

    state_dict = {
        "UNKNOWN": 0,
        "OFF":     0,
        "ALARM":   1,
        "FAULT":   1,
        "STANDBY": 2,
        "RUNNING": 4,
        "MOVING":  4,
        "ON":      2,
    }

    def __init__(self, name):
        # State values as expected by Motor bricks
        super().__init__(name)

    def _init(self):
        self.current_position = 0
        self.state = 'UNKNOWN'

        self.motor_name = self.get_property("motor_name")
        self.smargon = self.get_object_by_role("smargon")
        self.backlash = self.get_property("backlash")
        self.limits = self.get_property("limits")

        self.velocity_default = self.smargon.get_property("velocity_default")
        self.velocity_slow = self.smargon.get_property("velocity_slow")

        self.signal_name = self.smargon.get_signal_name(self.motor_name)

        self.connect(self.smargon, self.signal_name, self.position_changed)
        self.connect(self.smargon, "stateChanged", self.state_changed)
        log.debug("SmargonAxis. Initiating motor: %s" % (self.motor_name))
        print(f'SmargonAxis initiating \n============================\n')


    #def position_changed(self, value):
    #    if value != self.current_position:
    #        self.current_position = value
    #        self.emit('positionChanged', (value,))

    def is_ready(self):
        prtint('Checking rediness .... ')
        return self.state == 'STANDBY'

    def connect_notify(self, signal):
        if signal == 'hardwareObjectName,stateChanged':
            self.state = self.smargon.get_state(self.motor_name)
            self.state_changed(self.state_to_num())
            print(f"SMARGONAXIS {self.motor_name} STATE change : ")
        elif signal == "positionChanged":

            print(f"SMARGONAXIS {self.motor_name} POSITON change : ")
            pos = self.smargon.get_position(self.motor_name)
            #self.current_position = self.zero_neg(pos)
            print("Tosition changed to : ", pos)
            self.position_changed(pos)
        #self.setIsReady(True)

    def state_changed(self, state):
        self.state = state
        print(f"S.gonAxis l80 motor - {self.motor_name} - state changed to -> {state}")

        self.emit('stateChanged', (self.state_to_num() ) )

    def state_to_num(self, state=None):

        if state is None:
           state = self.state

        return self.state_dict.get(state,0)

    def get_value (self):
        return self.smargon.motor_chanels[self.motorname].get_valeu()

    def get_limits(self):
        if self.limits:
            vals = map(float,self.limits.split(","))
            return tuple(vals)

        return self.smargon.get_limits(self.motor_name)

    def get_motor_mnemonic(self):
        return self.name()

    def is_moving(self):
        print (f'Checking if Motor {self.name()} is in motion !')
        state = self.smargon.get_state()
        self.state = state
        print(state == "MOVING")
        return( state == "MOVING" )

    def position_changed(self, newpos):
        newpos = self.zero_neg(newpos)
        print(f"{self.name()} at {self.current_position} moving to {newpos}")

        if newpos != self.current_position:
            # avoid near 0 negative values
            self.current_position = newpos
            self.emit('positionChanged', (newpos, ))

    def zero_neg(self, pos):
        if -0.005 < pos < 0:
             return 0.0
        return pos

    def getPosition(self):
        pos = self.smargon.get_position(self.motor_name)
        pos = self.zero_neg(pos)

        if pos != self.current_position:
            self.current_position = pos
            # self.position_changed(pos)

        return pos

    def syncMove(self, position, wait=True):
        self.smargon.wait_ready()
        self.smargon.move(self.motor_name, position, backlash=self.backlash)
        if wait:
            self.smargon.wait_ready()

    def syncMoveRelative(self, position, wait=True):
        new_pos = self.getPosition() + position
        self.syncMove(new_pos, wait)

    def move(self, target_position):
        """Move the motor to the required position

        Arguments:
        absolutePosition -- position to move to
        """
        target_position = float(target_position)
        current_position = self.smargon.get_position(self.motor_name)
        print(f"AsgonAxis moving motor {self.motor_name}")

        if abs(target_position - current_position) < 0.001:
            log.debug("SmargonAxis.py -  Movement for %s too small. Not moving" % self.motor_name)
            return

        if self.motor_name == 'chi':
            log.debug("SmargonAxis.py -  Moving chi to %s" % target_position)
            self.smargon.move('velocity', self.velocity_slow)
            self.smargon.wait_ready()
            gevent.sleep(0.2)
            log.debug("SmargonAxis.py -  Current value for velocity is %s" % self.smargon.get_position('velocity'))

        self.smargon.move(self.motor_name, target_position, backlash=self.backlash)

        if self.motor_name == 'chi':
            log.debug("SmargonAxis.py -  Moving chi restoring default velocity after move")
            self.restore_task = gevent.spawn(self.restore_default_velocity)
            gevent.sleep(0.2)
            log.debug("SmargonAxis.py -  Current value for velocity is %s" % self.smargon.get_position('velocity'))

    def restore_default_velocity(self):
        self.smargon.wait_ready()
        self.smargon.move('velocity', self.velocity_default)
        self.smargon.wait_ready()

    def moveRelative(self, position):
        new_pos = self.getPosition() + position
        self.smargon.move(self.motor_name, new_pos, backlash=self.backlash)

    def waitReady(self):
        self.smargon.wait_ready()

    def stop(self):
        self.smargon.stop()

def test_hwo(hwo):
    print( hwo.get_motor_mnemonic())
    print( hwo.getPosition())
    print( hwo.getLimits())