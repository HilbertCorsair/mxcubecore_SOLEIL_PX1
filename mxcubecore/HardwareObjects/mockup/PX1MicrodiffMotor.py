from MicrodiffMotor import MicrodiffMotor
import logging
from gevent import spawn
from goniometer import goniometer

class PX1MicrodiffMotor(MicrodiffMotor):
    
    def __init__(self, name):
        MicrodiffMotor.__init__(self, name)
        self.goniometer = goniometer()
        
    def init(self):
        MicrodiffMotor.init(self)
        
    def move(self, position, wait=True, timeout=None):
        if abs(self.get_position() - position) >= self.motor_resolution:
            if hasattr(self.goniometer, 'set_%s_position' % self.motor_name.lower()):
                spawn(getattr(self.goniometer, 'set_%s_position' % self.motor_name.lower()), position)
            else:
                spawn(self.goniometer.set_position, {self.motor_name: position})

