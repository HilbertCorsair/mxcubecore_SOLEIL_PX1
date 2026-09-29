"""The SampleView "backlight" button on PX1.

PX1 has no backlight switch of its own: the backlight comes in with the
supervisor's VISU_SAMPLE phase. So ON sends PX1Environment to VISU_SAMPLE and
OFF to DEFAULT, and the value shown is the phase the supervisor reports, not
the last click.

Configuration (backlight.xml), referenced from minidiff.xml as
<object role="backlight" href="/backlight"/>:

    <object class="PX1BackLight">
      <username>Backlight</username>
      <object role="environment" href="/px1environment"/>
    </object>
"""

import logging
from enum import Enum

import gevent

from mxcubecore.HardwareObjects.abstract.AbstractNState import AbstractNState

log = logging.getLogger("HWR")

# PX1Environment phase numbers (see EnvironmentPhase in PX1Environment.py).
PHASE_DEFAULT = 3
PHASE_VISU_SAMPLE = 8


class PX1BackLight(AbstractNState):
    VALUES = Enum("ValueEnum", {"ON": "ON", "OFF": "OFF", "UNKNOWN": "UNKNOWN"})

    poll_period = 0.5  # s; the phase can also change outside MXCuBE

    def __init__(self, name):
        super().__init__(name)
        self.env = None
        self._poller = None

    def init(self):
        super().init()
        self.env = self.get_object_by_role("environment")
        if self.env is None:
            log.error("PX1BackLight: no 'environment' object configured")
            self.update_state(self.STATES.FAULT)
            return
        self._poller = gevent.spawn(self._poll)

    def _read(self):
        phase = str(self.env.get_phase()).upper().replace("_", "")
        value = self.VALUES.ON if phase == "VISUSAMPLE" else self.VALUES.OFF
        env_state = str(self.env.get_state()).upper()
        busy = env_state in ("MOVING", "RUNNING")
        return value, self.STATES.BUSY if busy else self.STATES.READY

    def _poll(self):
        failing = False
        while True:
            try:
                value, state = self._read()
                self.update_value(value)
                self.update_state(state)
                failing = False
            except Exception:
                if not failing:
                    log.exception("PX1BackLight: cannot read the supervisor phase")
                    failing = True
                self.update_state(self.STATES.UNKNOWN)
            gevent.sleep(self.poll_period)

    def get_value(self):
        try:
            return self._read()[0]
        except Exception:
            return self.VALUES.UNKNOWN

    def _set_value(self, value):
        phase = PHASE_VISU_SAMPLE if value == self.VALUES.ON else PHASE_DEFAULT
        self.update_state(self.STATES.BUSY)
        # goto_phase waits for the supervisor to stop moving before sending
        # the command; the poll above reports the result when it is there.
        gevent.spawn(self.env.goto_phase, phase)
