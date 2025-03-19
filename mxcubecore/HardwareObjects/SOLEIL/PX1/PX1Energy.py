from mxcubecore.BaseHardwareObjects import HardwareObject
from PyTango import DeviceProxy

from mxcubecore.HardwareObjects.abstract.AbstractEnergy import AbstractEnergy
import logging
import os
import time
import gevent

class PX1Energy(AbstractEnergy):

    def __init__(self, name):
        super().__init__(name)
    
    def init(self):
        self.moving = False
        self.doBacklashCompensation = False
        self.current_energy = None
        self.current_state = None
        try:    
            self.monodevice = DeviceProxy(self.get_property("mono_device"))
        except:    
            self.error_device_instance(self.get_property("mono_device"))

        self.und_device = DeviceProxy(self.get_property("undulator_device"))
        self.doBacklashCompensation = self.get_property("backlash")
            
        self.is_connected()

        self.energy_chan = self.get_channel_object("energy") 
        self.energy_chan.connect_signal("update", self.energy_changed)

        self.stop_cmd = self.get_command_object("stop")

        self.state_chan = self.get_channel_object("state") 
        self.state_chan.connect_signal("update", self.state_changed)
        
        
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

    def connect_notify(self, signal):

        print(f"SIgnal recived {signal}")
        if signal == 'energyChanged':
            try:
                self.energy_changed(self.get_energy())
                self.energy_limits_changed()
            except:
                logging.getLogger("HWR").error("PX1Energy. cannot read values for energy") 

        if signal == 'stateChanged':    
            try:
                self.state_changed(self.get_state())
            except:
                logging.getLogger("HWR").error("PX1Energy. cannot read state for energy") 

        self.set_is_ready(True)
         
    def state_changed(self, value):
        print (f"state_changed {value}")
        str_state = str(value)
        if str_state == 'BUSY':
            self.move_energy_cmd_started()

        if self.current_state == 'BUSY' or self.moving == True:
            if str_state != 'BUSY':
                self.move_energy_cmd_finished() 
        print(f"Current PX1EN state is {str_state}")            
        self.current_state = self.motstate_to_state(str_state)
        self.emit('stateChanged', self.current_state)
        
    def energy_changed(self, value):
        print(f"Detected energy change {value}")
        if self.current_energy is not None and abs(self.current_energy - value) < 0.0001:
            return
        self.current_energy = value     
        wav = self.get_current_wavelength()
        if wav is not None:
            self.emit('energyChanged', (value, wav))
    
    def get_value(self):
        return self.get_energy()
            
    def is_spec_connected(self):
        return True
    
    def is_connected(self):
        return True

    def s_connected(self):
        self.emit('connected', ())
      
    def s_disconnected(self):
        self.emit('disconnected', ())
    
    def is_disconnected(self):
        return True
        
    def can_move_energy(self):
        return True
        
    def get_position(self):
        return self.get_current_energy()

    def get_current_energy(self):
        return self.get_energy()
    
    def get_energy(self):
        print("get_energy <<<<")
        return self.energy_chan.get_value()

    def get_state(self):
        tango_string_state = str(self.state_chan.get_value())
        HO_state = self.motstate_to_state(tango_string_state)
        return HO_state

    def get_energy_computed_from_current_gap(self):
        return self.und_device.energy
    
    def get_current_undulator_gap(self):
        return self.und_device.gap
            
    def get_wavelength(self):
        print("Reading  WL value in PX1EN: ", self.monodevice.read_attribute("lambda").value)
        return self.monodevice.read_attribute("lambda").value

    def get_current_wavelength(self):
        print("get_current_wavelength")
        return self.get_wavelength()
        
    def get_limits(self):
        return self.get_energy_limits()

    def get_energy_limits(self):
        chan_info = self.energy_chan.get_info()
        print("Getting EN limits:" , (float(chan_info.min_value), float(chan_info.max_value)))
        return (float(chan_info.min_value), float(chan_info.max_value))
    
    def get_wavelength_limits(self):
        energy_min, energy_max = self.get_energy_limits()
       
        max_lambda = self.energy_to_lambda(energy_min)
        min_lambda = self.energy_to_lambda(energy_max)
        print('Calculating min max lambda', (min_lambda, max_lambda))

        return (min_lambda, max_lambda)
            
    def energy_to_lambda(self, value):
        self.monodevice.simEnergy = value
        return self.monodevice.simLambda

    def lambda_to_energy(self, value):
        self.monodevice.simLambda = value
        return self.monodevice.simEnergy
    
    def _callback(self, move_task):
        value = move_task.get()
        if not isinstance(value, gevent.GreenletExit):
            self._set_value(value)

    def _move(self, value):
        """Simulated energy change
        Args:
            value (float): target energy
        """
        self.energy_chan.set_value(value)
        self.energy_changed(value)

    def set_value(self, value, timeout=0):
        if not self.move_energy_cmd_ready():
            self.wait_energy_ready()
            self.move_energy(value)
        else:
            self.move_energy(value)

        """
        Set actuator to absolute value.
        This is NOT the recommended way, but for technical reasons
        overriding is necessary in this particular case
        Args:
            value (float): target value
            timeout (float): optional - timeout [s],
                             If timeout == 0: return at once and do not wait (default);
                             if timeout is None: wait forever.
        Raises:
            ValueError: Value not valid or attemp to set read-only actuator.
            RuntimeError: Timeout.
       
        if self.read_only:
            raise ValueError("Attempt to set value for read-only Actuator")
        if self.validate_value(value):
            self.update_state(self.STATES.BUSY)
            if timeout or timeout is None:
                with gevent.Timeout(
                    timeout, RuntimeError(f"Motor {self.username} timed out")
                ):
                    self._move(value)
                    self._set_value(value)
                    self.update_state(self.STATES.READY)
            else:
                self.__move_task = gevent.spawn(self._move, value)
                self.__move_task.link(self._callback)
        else:
            raise ValueError(f"Invalid value {value}; limits are {self.get_limits()}")
         """

    def move_energy(self, value, wait=False):
        value = float(value)
        print('Moving energy')
    
        backlash = 0.1  # en mm
        gaplimite = 5.5  # en mm
        
        if self.get_state() != "BUSY":
            if self.doBacklashCompensation:
                try:
                    self.und_device.energy = value
                    newgap = self.und_device.computedGap()
                    actualgap = self.und_device.gap
                    
                    while str(self.und_device.State()) == 'MOVING':
                        gevent.sleep(0.03)

                    if newgap < actualgap + backlash:
                        if newgap-backlash > gaplimite:
                            self.und_device.gap = newgap - backlash
                            while str(self.und_device.State()) == 'MOVING':
                                gevent.sleep(0.03)

                            self.energy_chan.set_value(value)
                        else:
                            self.und_device.gap = gaplimite
                            self.und_device.gap = newgap + backlash
                        gevent.sleep(0.03)
                except: 
                    logging.getLogger("HWR").error("%s: Cannot move undulator U20 : State device = %s", self.name(), str(self.und_device.State()))
                
            try:
                self.energy_chan.set_value(value)
                return value
            except:           
                logging.getLogger("HWR").error("%s: Cannot move Energy : State device = %s", self.name(), self.get_state())
            
        else: 
            logging.getLogger("HWR").error("%s: Cannot move Energy : State device = %s", self.name(), self.get_state())
            
    def move_wavelength(self, value, wait=False):
        egy_value = self.lambda_to_energy(float(value))
        logging.getLogger("HWR").debug("%s: Moving wavelength to : %s (egy to %s" % (self.name(), value, egy_value))
        self.move_energy(egy_value)
        return value
    
    def cancel_move_energy(self):
        self.stop_cmd()
        self.moving = False
            
    def energy_limits_changed(self, limits=None):
        if limits == None:
            egy_min, egy_max = self.get_limits()
        else: 
            egy_min, egy_max = limits

        lambda_min = self.energy_to_lambda(egy_min)
        lambda_max = self.energy_to_lambda(egy_max)

        wav_limits = (lambda_min, lambda_max)

        self.emit('energyLimitsChanged', (limits,))

        if None not in wav_limits:
            self.emit('wavelengthLimitsChanged', (wav_limits,))
        else:
            self.emit('wavelengthLimitsChanged', (None,))
            
    def move_energy_cmd_ready(self):
        if not self.moving:
            self.emit('moveEnergyReady', (True,))
            
    def move_energy_cmd_not_ready(self):
        if not self.moving:
            self.emit('moveEnergyReady', (False,))
            
    def move_energy_cmd_started(self):
        self.moving = True
        self.emit('moveEnergyStarted', ())
        
    def move_energy_cmd_failed(self):
        self.moving = False
        self.emit('moveEnergyFailed', ())
        
    def move_energy_cmd_aborted(self):
        self.moving = False
    
    def move_energy_cmd_finished(self):
        self.moving = False
        self.emit('moveEnergyFinished', ())
        
    def get_previous_resolution(self):
        return (None, None)
        
    def restore_resolution(self):
        return (False, "Resolution motor not defined")

    # Keeping these aliases for backward compatibility
    getEnergyLimits = get_energy_limits
    getWavelengthLimits = get_wavelength_limits
    canMoveEnergy = can_move_energy
    startMoveEnergy = move_energy
    startMoveWavelength = move_wavelength

    def wait_energy_ready(self, timeout=60):

        _state = self.get_state()
        t0 = time.time()
        last_msg_time = 0

        while _state.name != "READY":
            gevent.sleep(1)
            elapsed = time.time() - t0
            _state = self.get_state()
            if elapsed - last_msg_time > 30.0:
                logging.getLogger("user_level_log").info("   -  calibration in progress (elapsed time %s)." % elapsed)
                last_msg_time = elapsed

            if elapsed > timeout and str(_state) in ('DISABLE', 'ALARM'):
                logging.getLogger("user_level_log").warning("   -  Monochromator is DISABLED. Continue...")
                break

            logging.info("    -  <PX1Energy>  waiting for energy ready: %s" % _state)
"""
            
def test_hwo(hwo):
    print hwo.get_position()
    print hwo.get_currentWavelength()
    print hwo.get_energy_limits()
    print hwo.get_current_undulator_gap()
    print hwo.get_state()
"""

