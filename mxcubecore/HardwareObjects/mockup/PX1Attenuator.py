
from mxcubecore.HardwareObjects.abstract.AbstractTransmission import (
    AbstractTransmission,
)
import logging
log = logging.getLogger("HWR")

class PX1Attenuator(AbstractTransmission):

    def __init__(self, name):
        super().__init__(name)

    state_attenuator = {'ALARM' : 'error',
                       'OFF' : 'error', 
                       'RUNNING' : 'moving',
                       'MOVING' : 'moving', 
                       'STANDBY' : 'ready', 
                       'UNKNOWN': 'changed', 
                       'EXTRACT': 'extract', 
                       'INSERT': 'insert'}

    def init(self):
        self.change_is_manual = False
        self.old_value = None

        self.state_chan = self.get_channel_object('state')
        self.factor_chan = self.get_channel_object('parser')

        if self.state_chan is not None:
            self.state_chan.connect_signal('update', self.state_changed)

        if self.factor_chan is not None:
            self.factor_chan.connect_signal('update', self.factor_changed)

        self.connected()

    def motstate_to_state(self, motstate):
        motstate = str(motstate)
        if motstate in ["ON", "STANDBY", "ALARM"]: #normal for PX11Energy
            state = self.STATES.READY
        elif motstate in ["MOVING", "RUNNING", "EXTRACT"]:
            state = self.STATES.BUSY
        elif motstate in ["FAULT", "DISABLE"]:
            state = self.STATES.FAULT
        elif motstate == "OFF":
            state = self.STATES.OFF
        else:
            state = self.STATES.UNKNOWN
        return state


    def connected(self):
        self.set_is_ready(True)
        
    def disconnected(self):
        self.set_is_ready(False)
    

    def get_state(self):
        tango_str_state = str(self.state_chan.get_value())
        HO_state = self.motstate_to_state(tango_str_state)
        return HO_state



    def get_att_state(self, value=None):
        if not self.state_chan:
            return

        if value is None:
            value = self.state_chan.get_value()

        try:
            state_str = str(value)
            retval= self.motstate_to_state[state_str]
        except:
            retval=None

        return retval


    def get_att_factor(self):
        try:
            value = round(float(self.factor_chan.get_value()),1)
        except:
            value=None

        return value
    
    def get_value(self):
        return self.get_att_factor()

    def state_changed(self, value=None):
        state_value = self.get_att_state(value)
        self.emit('attStateChanged', (state_value, ))

        if self.old_value in ["RUNNING", "MOVING"]:
            if state_value in ["READY", "STANDBY"]:
                self.change_is_manual = False

        log.debug("ATTENUATOR state value is: %s" % str(state_value))
        self.old_value = str(state_value)

    def factor_changed(self, channelValue):
        try:
            value = int(round(self.get_att_factor()))
        except:
            logging.getLogger("HWR").error('%s attFactorChanged : received value on channel is not a float value', str(self.name()))
        else:
            log.debug("PX1Attenuator. transmission factor changed to %s (manual=%s)" % (value, self.change_is_manual))
            self.emit('attFactorChanged', (value, )) 
            if self.change_is_manual:
                self.emit('positionChanged', (value, )) 
    
    def set_transmission(self,value, manual=False) :

        log.debug("PX1Attenuator. setting transmission to %s (manual=%s)" % (value, manual))

        if manual:
            self.change_is_manual = True

        try:
            self.factor_chan.set_value(value)
        except:
            logging.getLogger("HWR").error('%s set Transmission : received value on channel is not valid', str(self.name()))
            value=None
        return value

    set_value = set_transmission

def test_hwo(hwo):
    print( hwo.get_att_factor() )
