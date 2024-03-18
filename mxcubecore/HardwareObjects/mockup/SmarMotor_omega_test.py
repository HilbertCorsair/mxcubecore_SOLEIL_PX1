"""
[Name] Omega
[Desccription] Omega movement test
[Emited signals] valueChanged
"""
import gevent
import time
import ast
from mxcubecore.HardwareObjects.abstract.AbstractMotor import AbstractMotor
from mxcubecore.HardwareObjects.abstract.AbstractMotor import MotorStates

DEFAULT_VELOCITY = 100 
DEFAULT_LIMITS = (-10000 , 10000)
DEFAULT_VALUE = 10.124
DEFAULT_WARP_RANGE = None

class SmartMotor (AbstractMotor):
	SPECIFIC_STATES = MotorStates
	def __init__(self, name):
		AbstractMotor.__init__(self, name)
		self.warp_range = None
	
	def init (self)  
