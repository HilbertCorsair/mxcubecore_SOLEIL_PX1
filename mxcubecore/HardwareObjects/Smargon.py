import logging
import time
from contextlib import contextmanager

from mxcubecore.BaseHardwareObjects import HardwareObject
#from mxcubecore.HardwareObjects.abstract.AbstractMotor import AbstractMotor
from gevent import Timeout
import gevent
import gevent.lock

log = logging.getLogger("HWR")


class GoniometerTimeout(RuntimeError):
    """The Smargon did not settle, or its gate was not free, in time."""

class Smargon(HardwareObject):
    """The PX1 Smargon (sgonaxis) and the gate every goniometer command goes through.

    The device takes one command at a time: a command that arrives while a
    motion runs can leave it unresponsive. Its State also drops back to
    STANDBY for a few ms between the legs of one motion (a backlash
    approach, a supervisor phase move), so "STANDBY, then send" is not
    enough. Hence:

    - every command goes through _command(): it takes the gate, waits until
      the previous command has settled, sends, and records its targets;
    - wait_settled() only returns once State has been STANDBY, no backlash
      leg is pending and every target is reached, all continuously for
      settle_time - a blip between two legs never qualifies;
    - procedure(name) holds the gate across a whole sequence (a centring, a
      mesh, a phase change, a data collection), so nothing else can slip a
      command in between its steps.

    A command that does not wait returns at once (murko can work while omega
    turns); the next command pays the wait instead.
    """

    default_polling = 100
    position_threshold = 0.001

    settle_time = 0.3   # s of stable STANDBY + targets reached = motion over
    stuck_time = 2.0    # s of STANDBY with a target still off = stopped short
    settle_timeout = 120  # s, longest single motion (chi at slow velocity)
    gate_timeout = 900  # s, longest procedure (a data collection)
    poll_period = 0.02
    angle_axes = ("omega", "phi", "chi")
    # Aliases the centring code used to pass to move_XYZ (HO names).
    xyz_aliases = {"/sampx": "zOffset", "/sampy": "yOffset", "/phiy": "xOffset",
                   "/omega": "omega", "/phiz": None}
    motors = ['chi','omega','phi','xOffset','yOffset','zOffset','x','y','z', 'velocity']
    signals = {motor : f'{motor}PositionChanged' for motor in motors}

    def __init__(self,name):
        super().__init__(name)
        self.motor_channels = {}
        self.motor_positions = {}
        self.motor_limits = {}
        self.state = None
        self.backlash_pending = {}
        self.backlash_task_running = False
        self._state_chan = None
        self._reeze_chan = None
        self._stop_cmd = None
        self.polling = None
        self._gate = gevent.lock.RLock()
        self._procedure = None
        self._procedure_t0 = None
        self._pending = {}
        # True once something may have moved the goniometer since it was
        # last seen settled: our own commands, or a procedure (the collect
        # server and the supervisor move it on their own). While False, a
        # settled Smargon costs no settle_time.
        self._dirty = True
        self.backlash_first = {}
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

    def init(self):
        self.device_name = self.get_property("tangoname")
        self.polling = self.get_property("polling") or self.default_polling
        try:
            self.settle_time = float(self.get_property("settle_time", self.settle_time))
        except (TypeError, ValueError):
            pass
        self._state_chan = self.add_channel({
             "type": "tango", "name": "_state_chan",
                    "tangoname": self.device_name, "polling": self.default_polling,
        }, "State")

        self._freeze_chan = self.add_channel({
             "type": "tango", "name": "_freeze_chan",
                    "tangoname": self.device_name,
        }, "freeze")

        self._stop_cmd = self.add_command({
            "type": "tango",
            "name": "_stop_cmd",
            "tangoname": self.device_name,
        }, "Stop")

        for motor_name in self.motors:
            chan = self.add_channel({
                "type": "tango",
                "name": f"_{motor_name}_chan",
                "tangoname": self.device_name,
                "polling": self.default_polling,
            }, motor_name)
            self.motor_channels[motor_name] = chan
            chan.connect_signal("update",self.slots[motor_name])

        self._state_chan.connect_signal("update", self.state_changed)
        self.gather_motor_limits()
    """
    def connectNotify(self, signal):
        if signal == 'stateChanged':
            print("SMARGON state changed !")
            self.state_changed(self.get_state())
        elif signal in ['deviceReady', 'deviceNotReady']:
            print ("SMARGON DOING NOTHING in conectNotify: ")
            pass
        else:
            motor = self.get_motor_from_signal(signal)
            pos = self.get_position(motor)
            self.slots[motor](pos)
            print (f"Smargon ---> motor: {motor}, signal: {signal}")
    """


    def get_state(self):
       # if self._state_chan is None:
       #     self.init()
        try :
            state = str(self.smargon._state_chan.get_value())
        except:
            # Hack to deal with the case in which self is of class Smargon not SmargonAxis
            if isinstance (self, Smargon):
                state = str(self._state_chan.get_value())
            else :
                print("ERROR in Smargon.get_state()")

        if state != self.state:
            self.state_changed(state)
        return state

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
            self.emit('stateChanged', self._state)

    def get_motors(self):
        return self.motors

    def get_signal_name(self, motor_name):
        return self.signals[motor_name]

    def get_motor_from_signal(self, signal_name):
        return next((motor for motor, signal in self.signas.items() if signal == signal_name),None)

    def get_position(self, motor_name):
        motor_chan = self.motor_channels[motor_name]
        motor_position = motor_chan.get_value()
        self.motor_positions[motor_name] = motor_position
        return motor_position

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------

    def busy_reason(self):
        """Name of the procedure holding the goniometer, or None."""
        return self._procedure

    @contextmanager
    def procedure(self, name, timeout=None):
        """Hold the goniometer for a whole sequence of commands.

        Reentrant for the greenlet that holds it (a centring calls moves that
        take the gate again). The outermost level waits for the previous
        motion to settle before it starts and for its own last motion to
        settle before it lets anyone else in.
        """
        self._acquire(name, timeout)
        outer = self._procedure is None
        try:
            if outer:
                self._procedure = name
                self._procedure_t0 = time.time()
                log.info("[gonio] procedure %s: start", name)
                self.wait_settled()
            yield self
            if outer:
                self._dirty = True  # the collect server / supervisor may have moved it
                self.wait_settled()
        finally:
            if outer:
                log.info(
                    "[gonio] procedure %s: end (%.2f s)",
                    name, time.time() - self._procedure_t0,
                )
                self._procedure = None
            self._gate.release()

    def _acquire(self, what, timeout=None):
        timeout = self.gate_timeout if timeout is None else timeout
        if not self._gate.acquire(timeout=timeout):
            raise GoniometerTimeout(
                "goniometer busy with %s for more than %s s, not sending %s"
                % (self._procedure, timeout, what)
            )

    def _command(self, targets, send, wait=False, what="move"):
        """Send one command once the previous one has fully finished."""
        self._acquire(what)
        try:
            self.wait_settled()
            if callable(targets):
                targets = targets()  # from positions read once settled
            self._dirty = True  # before: a send that fails half-way still moved
            send(targets)
            self._pending.update(targets)
            if wait:
                self.wait_settled()
        finally:
            self._gate.release()

    def _tolerance(self, motor_name):
        if motor_name == "velocity":
            return None  # a setting, not a motion
        return 0.01 if motor_name in self.angle_axes else self.position_threshold

    def _read_state(self):
        return str(self._state_chan.get_value())

    def _off_target(self, targets):
        """Axes of <targets> not (yet) within tolerance."""
        off = []
        for motor_name, target in targets.items():
            tol = self._tolerance(motor_name)
            if tol is None:
                continue
            if abs(self.get_position(motor_name) - target) > tol:
                off.append(motor_name)
        return off

    def wait_settled(self, timeout=None, targets=None):
        """Wait until the last command has completely finished.

        Done when State is STANDBY, no backlash leg is pending and every
        target is reached, all held for settle_time. Seeing MOVING is not
        required: a zero move, or one that ends between two State polls,
        still completes. STANDBY held for stuck_time with a target still off
        (a limit, rounding) ends the wait with a warning rather than never.
        Raises GoniometerTimeout; it never lets a command through early.
        """
        own = targets is None
        targets = dict(self._pending) if own else targets
        if own and not self._dirty and not self.backlash_task_running:
            if self._read_state() == "STANDBY":
                return  # settled already, and nothing sent since
        timeout = self.settle_timeout if timeout is None else timeout
        t0 = time.time()
        standby_since = stable_since = None
        while True:
            now = time.time()
            standby = self._read_state() == "STANDBY" and not (
                own and self.backlash_task_running
            )
            if standby:
                standby_since = standby_since or now
                off = self._off_target(targets)
                if off:
                    stable_since = None
                    if now - standby_since >= self.stuck_time:
                        log.warning(
                            "[gonio] STANDBY but %s short of target %s",
                            off, {m: targets[m] for m in off},
                        )
                        break
                else:
                    stable_since = stable_since or now
                    if now - stable_since >= self.settle_time:
                        break
            else:
                standby_since = stable_since = None
            if now - t0 > timeout:
                raise GoniometerTimeout(
                    "Smargon not settled after %s s (state %s, targets %s)"
                    % (timeout, self._read_state(), targets)
                )
            gevent.sleep(self.poll_period)
        if own:
            self._pending = {}
            self._dirty = False

    # ------------------------------------------------------------------
    # Commands. All of them go through _command().
    # ------------------------------------------------------------------

    def _write(self, motor_name, target_pos, backlash=None):
        """Write one axis target. No gate: callers hold it (or, for the
        second backlash leg, the command in flight does)."""
        log.debug("Smargon.py - Moving motor %s to: %.3f" % (motor_name, target_pos))
        motor_chan = self.motor_channels[motor_name]

        def sign(x):
            if x ==0: return 0

            return int(x/abs(x))

        do_backlash = False

        if backlash is not None:
             current_pos = motor_chan.get_value()
             move_distance = target_pos - current_pos

             if abs(move_distance) > 5e-3:
                 if sign(move_distance) != sign(backlash):
                     do_backlash = True
                     final_pos = target_pos
                     target_pos -= backlash
                     self.backlash_pending[motor_name] = final_pos
                     self.backlash_first[motor_name] = target_pos
                     log.debug("Smargon.py -   backlash: moving first to: %s then %s" % (target_pos, final_pos))
        motor_chan.set_value(target_pos)
        if do_backlash and not self.backlash_task_running:
            self.start_backlash_task()

    def move(self, motor_name, target_pos, backlash=None, wait=False):
        target_pos = float(target_pos)
        self._command(
            {motor_name: target_pos},
            lambda targets: self._write(motor_name, target_pos, backlash),
            wait=wait,
            what="%s -> %.4f" % (motor_name, target_pos),
        )

    def start_backlash_task(self):
        if not self.backlash_task_running:
            logging.getLogger("HWR").debug("Smargon.py - starting backlash task")
            self.backlash_task_running = True
            self.backlash_task = gevent.spawn(self._do_backlash)
            self.backlash_task.link(self.backlash_task_done)
        else:
            logging.getLogger("HWR").debug("Smargon.py - backlash task already running")

    def _do_backlash(self):
        # The first leg is over only once it has reached its target and
        # State has stayed STANDBY: the State channel still reads STANDBY
        # right after the write, and sending the second leg then would land
        # on a moving device.
        first = dict(self.backlash_first)
        self.wait_settled(targets=first)

        for motor_name, target_pos in self.backlash_pending.items():
            logging.getLogger("HWR").debug("   backlash finish. moving %s to %s" % (motor_name, target_pos))
            self._write(motor_name, target_pos, backlash=None)

        self.backlash_pending = {}
        self.backlash_first = {}
        return True

    def backlash_task_done(self, result):
        self.backlash_task_running = False

    def move_motors(self, motor_pos_dict, wait=False, backlash=None):
        """Move several axes as ONE command (frozen, released together).

        Keys are actuator names (xOffset, yOffset, zOffset, omega, x, ...).
        """
        targets = {m: float(p) for m, p in motor_pos_dict.items()}
        if not targets:
            return
        backlash = backlash or {}

        self._command(
            targets,
            lambda targets: self._send_batch(targets, backlash),
            wait=wait,
            what="move %s" % targets,
        )

    def _send_batch(self, targets, backlash=None):
        backlash = backlash or {}
        self.set_freeze(True)
        try:
            for motor_name, target_pos in targets.items():
                self._write(motor_name, target_pos, backlash.get(motor_name))
        finally:
            self.set_freeze(False)

    def move_motors_relative(self, deltas, wait=False):
        """Relative moves of several axes as ONE command.

        The base positions are read inside the gate, once the previous
        motion has settled - read before, they could be mid-move.
        """
        deltas = {m: float(d) for m, d in deltas.items()}
        if not deltas:
            return
        self._command(
            lambda: {m: self.get_position(m) + d for m, d in deltas.items()},
            self._send_batch,
            wait=wait,
            what="move relative %s" % deltas,
        )

    def move_XYZ(self, motor_pos_dict, wait=False):
        """move_motors() that also accepts the old HO-name keys."""
        targets = {}
        for key, pos in motor_pos_dict.items():
            name = self.xyz_aliases.get(key, key)
            if name is None:
                continue
            if name not in self.motor_channels:
                log.error("Smargon.move_XYZ: unknown axis %r, not moved", key)
                continue
            targets[name] = pos
        self.move_motors(targets, wait=wait)

    def set_freeze(self, onoff):
        logging.getLogger("HWR").debug( "Smargon. Setting freeze to: %s" % onoff)
        self._freeze_chan.set_value(onoff)

    def wait_notready(self, timeout=40):
        t0 = time.time()

        while self.is_ready():
            if (time.time() - t0) > timeout:
                raise Timeout
            gevent.sleep(0.03)
        logging.getLogger("HWR").debug(f"Waited {time.time() - t0} beacuse Smargon NOT READY (wait not ready)")

    def wait_ready(self, timeout=None):
        """Wait until the last command has completely finished (see wait_settled)."""
        self.wait_settled(timeout)

    def _wait_ready(self, timeout=40):
        t0 = time.time()
        logging.getLogger("HWR").debug("SMARGON not yet ready, please wait a few seconds ..... " )

        while not self.is_ready():
            if (time.time() - t0) > timeout:
                logging.getLogger("HWR").debug("SMARGON TIMEOUT" )
                raise Timeout

            gevent.sleep(0.03)
        logging.getLogger("HWR").debug("SMARGON is now READY : moving on!" )

    def is_ready(self):
        return self.get_state() == "STANDBY"

    def gather_motor_limits(self):
        for _motor_name in self.motor_channels:
            chan = self.motor_channels[_motor_name]
            info = chan.get_info()
            min_value = float(info.min_value)
            max_value = float(info.max_value)
            self.motor_limits[_motor_name] = (min_value, max_value)

    def get_limits(self, motor_name, update=False):
        if not self.motor_limits or update :
            for _motor_name in self.motor_channels:
                chan = self.motor_channels[_motor_name]
                info = chan.get_info()
                min_value = float(info.min_value)
                max_value = float(info.max_value)
                self.motor_limits[_motor_name] = (min_value, max_value)

        return self.motor_limits[motor_name]

    def stop(self):
        # Never gated: a stop must always get through.
        self._dirty = True
        if self.backlash_task_running:
           self.backlash_task.kill(block=False)
           self.backlash_task_running = False
        self.backlash_pending = {}
        self.backlash_first = {}
        self._pending = {}
        self._stop_cmd()

def test_hwo(hwo):
    t0 = time.time()

    print("State is: %s" % hwo.get_state())
    print(hwo.get_signal_name("chi"))

    for motor in hwo.get_motors():
        print("Motor % 7s is % -4.3f" % (motor,hwo.get_position(motor)))

    print("Elapsed time: %s" % (time.time() - t0))