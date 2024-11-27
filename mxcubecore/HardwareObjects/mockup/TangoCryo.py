
import logging

from mxcubecore.BaseHardwareObjects import HardwareObject
class TangoCryo(HardwareObject):

    def __init__(self, name):
        super().__init__(name)
        self.status = None

    def init(self):
        try:
            statechan = self.get_channel_object('state')
            statechan.connect_signal('update', self.stateChanged)

            statuschan = self.get_channel_object('status')
            statuschan.connect_signal('update', self.statusChanged)

            n2levelchan = self.get_channel_object('n2level')
            n2levelchan.connect_signal('update', self.levelChanged)

            tempchan = self.get_channel_object('temperature')
            tempchan.connect_signal('update', self.temperatureChanged)

            logging.getLogger().debug('%s: connected to channels', self.name())

            self.cooldown_cmd = self.get_command_object('cool')
            self.heatup_cmd = self.get_command_object('heat')

            self.heat_temp = self.get_property('heat_temperature')
            self.heat_rate = self.get_property('heat_rate')
            self.cool_temp = self.get_property('cool_temperature')

        except KeyError:
            logging.getLogger().warning('%s: cannot connect to channel', self.name())

    def stateChanged(self, value):
        self.emit('cryoStateChanged', value)

    def statusChanged(self, value):
        if value != self.status:
            self.status = value
            self.emit('cryoStatusChanged', value)

    def levelChanged(self, value):
        self.emit('levelChanged', value)

    def temperatureChanged(self, value):
        self.emit('temperatureChanged', value)

    def heatup(self):
        # target = 300K
        # ramp = ( 1 hour for 200K )  3 per minute
        try:
            self.heatup_cmd((self.heat_rate,self.heat_temp))
        except:
            import traceback
            logging.getLogger("HWR").warning('%s: HEATING UP cryo. Error while starting heating', self.name())
            logging.getLogger("HWR").warning( traceback.format_exc() )

    def cooldown(self):
        # target = 100K
        try:
            self.cooldown_cmd(self.cool_temp)
        except:
            import traceback
            logging.getLogger("HWR").warning('%s: COOLING cryo. Error while starting cooling', self.name())
            logging.getLogger("HWR").warning( traceback.format_exc() )

