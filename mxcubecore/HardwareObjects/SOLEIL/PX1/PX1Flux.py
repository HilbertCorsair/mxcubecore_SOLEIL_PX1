

from mxcubecore.HardwareObjects.abstract.AbstractFlux import AbstractFlux
import time
import subprocess
import logging
import gevent
import numpy as np

log = logging.getLogger("HWR")

MODEL_DOSE_FUNCTION = np.poly1d([-8.73221788e-05,  8.59598045e-03, -3.60115597e-01,  8.35440364e+00,
             -1.16701521e+02,  9.93683453e+02, -4.88848980e+03,  1.12680471e+04])
MODEL_FLUX = 1.6e12
MODEL_TIME = 90.

class PX1Flux(AbstractFlux):
    def __init__(self, name):
        super().__init__(name)
        self._calculating = False
        self.dose_latest_value = 0
        self.trans_hwo = None
        self._energy = None
        self._exp_time = None
        self._flux = None
        self._osc_start = None
        self._osc_end = None
        self._previous_value = None
        self._current_value = None
        self._delta_trig = 1e-9
    
    def init(self):
        super().init()
        self.cv = None
        self.pv = None

        try:
            self.object_channel = self.get_channel_object('channel')
            self.object_channel.connect_signal('update', self.value_changed)

            self.status_channel = self.get_channel_object('state')
            if self.status_channel is not None:
                self.status_channel.connect_signal('update', self.state_changed)
        except KeyError:
            logging.getLogger().warning('%s: cannot connect to channel', self.name())
        except Exception as e:
            import traceback 
            logging.getLogger("HWR").error("error creating channel value : %s ", traceback.format_exc())

        self.delta = 0.1
        self.trans_hwo = self.get_object_by_role("transmission")

    @property
    def delta(self):
        return self._delta_trig
    @delta.setter
    def delta(self, val):
        self._delta_trig = val

    @property
    def previous_value(self):
        return self._previous_value
    @previous_value.setter
    def previous_value(self, val):
        self._previous_value = val

    @property
    def current_value(self):
        return self._current_value
    @current_value.setter
    def current_value(self, val):
        self._current_value = val

    def motstate_to_state(self, motstate):
        motstate = str(motstate)
        if motstate in ["ON", "STANDBY"]: #normal for PX11Energy
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


    def wait_ready(self, timeout=5):
        start_wait = time.time()

        while self._calculating:
            gevent.sleep(0.05)
            if (time.time() - start_wait) > timeout:
                return False

        return True

    def get_value(self):
        self.pv = self.cv
        self.cv = self.object_channel.get_value()
        return self.cv


    def value_changed(self, value):
        
        if self.pv:
            cond = abs(self.pv - value)/self.pv > self._delta_trig
            if self._delta_trig  and cond:
                self.cv = value
                print(f"CHANGING FLUX VALUE TO {value}")
                self.emit('valueChanged', value)            

    def state_changed(self, value):
        s = self.motstate_to_state(str(value))
        self.emit('stateChanged', s)


    def get_dose_old(self, energy=None, exp_time=None, osc_start=None, osc_end=None, transmission=None):

        flux = 1e12
        if not transmission:
            return None
        else: 
            current_transmission = self.trans_hwo.get_att_factor()
            cflux = flux * transmission / current_transmission
 

        # I should put some range here. specially for flux
        if energy == self._energy and \
           exp_time == self._exp_time and \
           cflux == self._flux and \
           osc_start == self._osc_start and \
           osc_end == self._osc_end:
            return self.dose_latest_value

        self._energy = energy
        self._exp_time = exp_time
        self._flux = cflux
        self._osc_start = osc_start
        self._osc_end = osc_end

        if cflux < 10:
            return 0

        values = {
           'host': 'process2',
           'path': '/nfs/ruche/share-dev/px1dev/MXCuBE/tools/raddose.py',
           'flux': cflux,
           'energy': energy,
           'exp_time': exp_time,
           'osc_start': osc_start,
           'osc_end': osc_end,
        }
        cmd_t = "{path} -F {flux} -P {energy} -T {exp_time} -O {osc_start} -E {osc_end}"

        self._calculating = True
        p1 = subprocess.Popen(cmd_t.format(**values), shell=True, stdout=subprocess.PIPE)
        dose_value, err = p1.communicate()

        self._calculating = False

        try:
            self.dose_latest_value = "%.3f" % float(dose_value)
            return self.dose_latest_value
        except:
            log.debug("error with dose %s" % dose_value)
            import traceback
            log.debug(traceback.format_exc())
            return None

    def get_dose(self, energy=None, exp_time=None, osc_start=None, osc_end=None, transmission=None):

        flux = self.get_value()

        try:
            current_transmission = self.trans_hwo.get_att_factor()
            cflux = flux * transmission / current_transmission
        except:
            return None

        # I should put some range here. specially for flux
        if energy == self._energy and \
           exp_time == self._exp_time and \
           cflux == self._flux and \
           osc_start == self._osc_start and \
           osc_end == self._osc_end:
            return self.dose_latest_value

        self._energy = energy
        self._exp_time = exp_time
        self._flux = cflux
        self._osc_start = osc_start
        self._osc_end = osc_end

        if cflux < 10:
            return 0

        #log.debug('PX1Flux: get_DWD_from_model')
        #log.debug('PX1Flux: photon energy %.3f ' % self._energy)
        x = MODEL_DOSE_FUNCTION(self._energy) 
        #log.debug('PX1Flux: photon flux %.3f ' % self._flux)
        x *= (self._flux/MODEL_FLUX) 
        #log.debug('PX1Flux: total_exposure_time %.2f' % self._exp_time)
        x *= (self._exp_time/MODEL_TIME)
        dose_value = x

        try:
            self.dose_latest_value = "%.3f" % float(dose_value)
            return self.dose_latest_value
        except:
            log.debug("error with dose %s" % dose_value)
            import traceback
            log.debug(traceback.format_exc())
            return None

def test_hwo(hwo):
    print( hwo.get_dose(energy=10.4, exp_time=3, osc_start=10, osc_end=180, transmission=40.0) ) 
