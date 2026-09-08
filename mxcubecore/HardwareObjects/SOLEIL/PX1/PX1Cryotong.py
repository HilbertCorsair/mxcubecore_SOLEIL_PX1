from __future__ import print_function
import logging
import gevent
import gevent.event
import time
from mxcubecore.HardwareObjects.abstract.sample_changer import Container
import PyTango


from Cats90 import (
    Cats90,
    SampleChangerState,
    BASKET_UNIPUCK,
)
from mxcubecore import HardwareRepository as HWR
from SOLEIL.PX1.PX1Environment import EnvironmentPhase
gevent.monkey.patch_all()

class PX1Cryotong(Cats90):

    __TYPE__ = "Cryotong"

    default_no_lids = 1
    baskets_per_lid = 3

    default_basket_type = BASKET_UNIPUCK

    def __init__(self, *args, **kwargs):

        super(PX1Cryotong, self).__init__(*args, **kwargs)
        # No baskets are built here. _init_sc_contents() is the single place
        # that builds them; doing it here as well left the changer holding 22
        # Basket objects with duplicate sample addresses, which every lookup
        # then resolved by list order rather than by address.
        self._safeNeeded = None
        self._homeOpened = None
        self.dry_and_soak_needed = False
        self.count_down = None
        self.soft_auth = None
        self.incoherent_state = None
        # Post-mount souflette (blower) drying settle. Run in the background so
        # the queue greenlet is released as soon as the pin is physically on the
        # goniometer; see _start_souflette.
        self._souflette_task = None
        self._souflette_done = gevent.event.Event()
        self._souflette_done.set()
        self._souflette_deadline = 0.0
        self.souflette_seconds = 45.0
        self.souflette_blocking = False
        self.transfer_ready_timeout = 180.0

    def init(self):
        super(PX1Cryotong, self).init()
        self.environment = self.get_object_by_role("environment")
        self.tangoname = self.get_property("tangoname")
        if self.environment is None:
            logging.error(
                "PX1Cats. environment object not available. Sample changer cannot operate. Info.mode only"
            )
            self.infomode = True
        else:
            self.infomode = False

        for channel_name in (
            "_chnSoftAuth",
            "_chnHomeOpened",
            "_chnDryAndSoakNeeded",
            "_chnIncoherentGonioSampleState",
            "_chnCountDown",
        ):
            setattr(self, channel_name, self.get_channel_object(channel_name))

        self._chnNumLoadedSample = self.get_channel_object("_chnNumLoadedSample")
        if self._chnNumLoadedSample is None:
            self._chnNumLoadedSample = self.add_channel({
                    "type": "tango", "name": "_chnNumLoadedSample",
                    "tangoname": self.tangoname, "polling": 1000,
                }, "NumSampleOnDiff")

        self._chnSoftAuth.connect_signal("update", self._software_authorization)
        self._chnHomeOpened.connect_signal("update", self._update_home_opened)
        self._chnIncoherentGonioSampleState.connect_signal("update", self._update_ack_sample_memory)
        self._chnDryAndSoakNeeded.connect_signal("update", self._dry_and_soak_needed)
        self._chnSampleIsDetected.connect_signal( "update", self._update_sample_is_detected)
        self._chnCountDown.connect_signal("update", self._update_count_down)

        self._cmdDrySoak = self.add_command({
                    "type": "tango",
                    "name": "_cmdDrySoak",
                    "tangoname": self.tangoname,
                }, "DryAndSoak")

        self._cmdSoak = self.add_command({
                    "type": "tango",
                    "name": "_cmdSoak",
                    "tangoname": self.tangoname,
                }, "Soak")

        self._cmdSafe = self.add_command({
                    "type": "tango",
                    "name": "_cmdSafe",
                    "tangoname": self.tangoname,
                }, "Safe")

        self._cmdReset = self.add_command({
                    "type": "tango",
                    "name": "_cmdReset",
                    "tangoname": self.tangoname,
                }, "ResetError")


        # _chnNumLoadedSample is already connected in Cats90.init(), to
        # cats_loaded_num_changed -> _update_loaded_sample. A second handler on
        # the same channel only raced with it.

        # <souflette_time>45</souflette_time> / <souflette_blocking>False</...>
        # souflette_blocking restores the legacy behaviour (the queue waits out
        # the drying time) without a code change.
        self.souflette_seconds = float(self.get_property("souflette_time", 45))
        self.souflette_blocking = self.is_string_true(
            self.get_property("souflette_blocking", False)
        )
        # How long _wait_transfer_ready may wait for the changer to leave its
        # post-transfer drying cycle before giving up.
        self.transfer_ready_timeout = float(
            self.get_property("transfer_ready_timeout", 180)
        )

        self._init_sc_contents()
        self._do_update_state()
        self._update_state()
        self._do_update_loaded_sample()

    # ## CRYOTONG SPECIFIC METHODS ###
    def is_string_true (self, val):
        if isinstance (val, bool):
            return val
        else :
            return str(val) in ["True","true"]

    def cats_basket_presence_changed(self, value):
        pass

    def _do_update_state(self):
        """
        Updates the state of the hardware object

        :returns: None
        :rtype: None
        """
        self.cats_running = self.is_string_true(self.cats_device.pathRunning)
        self.cats_powered = self.is_string_true(self.cats_cats.Powered)
        self.cats_lids_closed = self.is_string_true(self.cats_device.isLidClosed)
        self.cats_status = self._chnStatus.get_value()
        self.cats_state = self._chnState.get_value()

    def _update_state(self):
        has_loaded = self.has_loaded_sample()
        on_diff = self._chnSampleIsDetected.get_value()

        state = self._decide_state(
            self.cats_state,
            self.cats_powered,
            self.cats_lids_closed,
            has_loaded,
            on_diff,
        )
        status = SampleChangerState.tostring(state)
        self._set_state(state, status)

    def _read_state(self):
        """
        Read the state of the Tango DS and translate the state to the SampleChangerState Enum

        :returns: Sample changer state
        :rtype: AbstractSampleChanger.SampleChangerState
        """
        _state = self._chnState.get_value()
        _powered = self.cats_cats.Powered
        _lids_closed = self.cats_device.isLidClosed
        _has_loaded = self.has_loaded_sample()
        _on_diff = self.cats_device.sampleIsDetected

        # hack for transient states
        trials = 0
        while _state in [PyTango.DevState.ALARM, PyTango.DevState.FAULT, PyTango.DevState.RUNNING, PyTango.DevState.MOVING]:
            time.sleep(0.1)
            trials += 1
            logging.getLogger("HWR").warning(
                "SAMPLE CHANGER could be in transient state. trying again"
            )
            _state = self._chnState.get_value()
            if trials > 2:
                break

        state = self._decide_state(
            _state, _powered, _lids_closed, _has_loaded, _on_diff
        )
        return state

    def _decide_state(self, dev_state, powered, lids_closed, has_loaded, on_diff):

        powered =self.cats_cats.Powered
        if dev_state == PyTango.DevState.ALARM:
            _state = SampleChangerState.Alarm

        elif dev_state == PyTango.DevState.FAULT:
            _state =  SampleChangerState.Fault

        elif dev_state in [PyTango.DevState.DISABLE, PyTango.DevState.OFF, PyTango.DevState.INIT]:
            logging.getLogger("HWR").warning("SAMPLE CHANGER disabled. Reason - state is %s" % str(dev_state))
            _state = SampleChangerState.Disabled

        elif not powered:
            logging.getLogger("HWR").warning("SAMPLE CHANGER disabled. Reason - power is off")
            _state = SampleChangerState.Disabled

        elif (not powered ) or dev_state in [ PyTango.DevState.ON , PyTango.DevState.STANDBY]:
            _state = SampleChangerState.Ready

        elif dev_state in [PyTango.DevState.RUNNING, PyTango.DevState.MOVING]:
            if self.state not in [
                SampleChangerState.Loading,
                SampleChangerState.Unloading,
            ]:
                _state = SampleChangerState.Moving
            else:
                _state = self.state

        elif dev_state == PyTango.DevState.UNKNOWN:
            _state = SampleChangerState.Unknown

        elif has_loaded ^ on_diff:
            # go to Unknown state if a sample is detected on the gonio but not registered in the internal database
            # or registered but not on the gonio anymore
            logging.getLogger("HWR").warning(
                "SAMPLE CHANGER Unknown 2 (hasLoaded: %s / detected: %s)"
                % (self.has_loaded_sample(), self.cats_device.sampleIsDetected)
            )

            _state = SampleChangerState.Unknown

        else:
            _state = SampleChangerState.Unknown
        return _state


    def _software_authorization(self, value):
        if value != self.soft_auth:
            self.soft_auth = value
            self.emit("softwareAuthorizationChanged", (value,))

    def _update_home_opened(self, value=None):
        if self._homeOpened != value:
            self._homeOpened = value
            self.emit("homeOpened", (value,))

    def _update_sample_is_detected(self, value):
        print(f"\nUpdating sample is detected ... <{value}>")
        self.emit("sampleIsDetected", (value,))

    def _update_ack_sample_memory(self, value=None):
        if value is None:
            value = self._chnIncoherentGonioSampleState.get_value()

        if value != self.incoherent_state:
            # automatically acknowledge the error. send a warning to the GUI
            if self.incoherent_state is not None:
                logging.getLogger("user_level_log").warning(
                    "CATS: Requested Sample could not be loaded."
                )
                self.emit("loadError", value)
                try:
                    self._cmdAckSampleMemory()
                except Exception:
                    """ do nothing if cmd not to acknowledge not in xml """
                    pass
            self.incoherent_state = value

    def _dry_and_soak_needed(self, value=None):
        self.dry_and_soak_needed = value

    def do_dry_and_soak(self):
        print("\nTime to dry and soak .... ")
        homeOpened = self._chnHomeOpened.get_value()

        if not homeOpened:
            self._do_dry_soak()
        else:
            logging.getLogger("user_level_log").warning(
                "CATS: You must Dry_and_Soak the gripper."
            )

    def _update_count_down(self, value=None):
        if value is None:
            value = self._chnCountDown.get_value()

        if value != self.count_down:
            logging.getLogger("HWR").info(
                "PX1Cats. CountDown changed. Now is: %s" % value
            )
            self.count_down = value
            self.emit("countdownSignal", value)

    def _do_dry_soak(self):
        """
        Launch the "DrySoak" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        if self.infomode:
            logging.warning("PX1Cats. It is in info mode only. DrySoak command ignored")
            return

        self._cmdDrySoak()
        self._dry_and_soak_needed(False)

    def _do_safe(self):
        """
        Launch the "safe" trajectory on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        if self.infomode:
            logging.warning(
                "PX1Cryotong. It is in info mode only. Command 'safe' ignored"
            )
            return

        ret = self.env_send_transfer()

        if not ret:
            logging.getLogger("user_level_log").error(
                "PX1 Environment cannot set transfer phase"
            )
            raise Exception(
                "Cryotong cannot get to transfer phase. Aborting sample changer operation"
            )

        self._execute_server_task(
            self._cmdSafe,
            "Safe",
            states=[SampleChangerState.Ready, SampleChangerState.Alarm],
        )

    # ## (END) CRYOTONG SPECIFIC METHODS ###

    # ## OVERLOADED CATS90 methods ####
    def cats_pathrunning_changed(self, value):
        # Hack to prevent a numerical value being passd on.
        if not isinstance(value, bool):
            value = self._chnDryAndSoakNeeded.get_value()

        Cats90.cats_pathrunning_changed(self, value)
        if self.cats_running is False and self.dry_and_soak_needed:
            self.do_dry_and_soak()

    def _do_load_operation(self, sample, wash=False, shifts=None):
        selected=self.get_selected_sample()
        if sample is not None:
            if sample != selected:
                self._do_select(sample)
                selected=self.get_selected_sample()
        else:
            if selected is not None:
                 sample = selected
            else:
               raise Exception("No sample selected")

        basketno = selected.get_basket_no()
        sampleno = selected.get_vial_no()

        logging.getLogger("HWR").debug("  ***** CATS *** doLoad basket:sample=%s:%s (wash=%s)" % (basketno, sampleno,wash))

        lid, sample = self.basketsample_to_lidsample(basketno,sampleno)

        # we should now check basket type on diffr to see if tool is different... then decide what to do
        logging.getLogger("HWR").debug("  ***** CATS *** shifts are %s" % str(shifts))

        if shifts is None:
            xshift, yshift, zshift = ["0", "0", "0" ]
        else:
            xshift, yshift, zshift = map(str,shifts)

        # prepare argin values
        argin = ['1', str(int(lid)), str(sample), "1", "0", xshift, yshift, zshift]
        logging.getLogger("HWR").debug("  ***** CATS *** doLoad argin:  %s / %s:%s" % (argin, basketno, sampleno))

        #self.videohub_ho.select_camera("Robot", process="mount")
        #self.videohub_ho.start_recording(file_prefix="mount")

        # Chained load (Exchange) vs plain load is decided by the goniometer
        # detector, not by our own loaded flag. Sending an Exchange to an empty
        # goniometer is rejected by the CATS and leaves it in Alarm/Disabled,
        # which then kills the whole queue run - and a stale flag is exactly
        # what used to put us there. The flag is only used to name the pin.
        on_diff = self.cats_sample_on_diffr() == 1
        loaded = self.get_loaded_sample()

        if loaded is not None and not on_diff:
            logging.getLogger("HWR").warning(
                "  ==========CATS=== %s is flagged loaded but nothing is detected"
                " on the goniometer; loading %s:%s as a plain load"
                % (loaded.get_address(), basketno, sampleno)
            )

        if on_diff:
            if loaded is not None and selected == loaded and not wash:
                msg = "Load aborted. Reason: \nSample " + str(loaded.get_address()) + " already loaded"
                logging.getLogger("user_level_log").info(msg)
                self.emit("catsError", msg)
                self._update_state()
                raise Exception(msg)

            logging.getLogger("HWR").warning("  ==========CATS=== chained load sample, sending to cats:  %s" % argin)
            self.environment.wait_ready()
            self._execute_server_task(self._cmdChainedLoad, argin)
        else:
            if self.cats_sample_on_diffr() == -1:
                # Conflicting loaded-sample info from the CATS. Loading blind
                # here is how a pin gets crushed; make the operator resolve it.
                raise Exception(
                    "CATS reports conflicting loaded-sample information. "
                    "Please clear it (AckIncoherentGonioSampleState) before loading."
                )
            logging.getLogger("HWR").warning("  ==========CATS=== load sample, sending to cats:  %s" % argin)
            self._execute_server_task(self._cmdLoad, argin)

        self.environment.wait_ready()
        HWR.beamline.diffractometer.mount_finished()
        #self.videohub_ho.select_camera("OAV", process="mount")

    def wait_countdown(self, timeout=20):
        t0 = time.time()
        count_down = self._chnCountDown.get_value()
        while count_down != 0:
            gevent.sleep(1)
            elapsed = time.time() - t0
            logging.getLogger('HWR').warning("CRYOTONG: waiting countdown to finish / %s secs " % elapsed)
            count_down = self._chnCountDown.get_value()
            if count_down == 0:
                break

            if elapsed > timeout:
                break
        return count_down

    def _do_load(self, sample=None, wash=None, souflette_time = True):


        ret = self.wait_countdown(22)

        if ret != 0:
            self.emit('loadError', "SC is counting down for too long. Aborted")
            raise Exception("CRYOTONG Cannot load. Counting down in progress")

        ret = self.check_power_on()
        if ret is False:
            logging.getLogger("user_level_log").error("CRYOTONG Cannot be powered")
            raise Exception(
                "CRYOTONG Cannot be powered. Aborting sample changer operation"
            )

        ret = self.check_drysoak()
        if ret is False:
            logging.getLogger("user_level_log").error(
                "CRYOTONG Home Open / DryAndSoak not valid for loading"
            )
            raise Exception("CRYOTONG Home Open / DryAndSoak not valid for loading")

        ret = self.env_send_transfer()
        if ret is False:
            logging.getLogger("user_level_log").error(
                "PX1 Environment cannot set transfer phase"
            )
            raise Exception(
                "Cryotong cannot get to transfer phase. Aborting sample changer operation"
            )

        self._do_load_operation(sample, wash)
        # Check the value of the CATSCRYOTONG attribute dryAndSoakNeeded to warn
        # user if it is True
        dryAndSoak = self._chnDryAndSoakNeeded.get_value()
        if dryAndSoak:
            logging.getLogger("user_level_log").warning(
                "CATS: It is recommended to Dry_and_Soak the gripper."
            )

        incoherentSample = self._chnIncoherentGonioSampleState.get_value()
        if incoherentSample:
            logging.getLogger("user_level_log").info(
                "CATS: Load/Unload Error. Please try again."
            )
            self.emit("loadError", incoherentSample)
        # Must stay synchronous: base_queue_entry.mount_sample() gates on
        # has_loaded_sample(), and AbstractSampleChanger._run's update_info()
        # needs fresh flags to emit loadedSampleChanged. Reads both CATS
        # attributes with a real read_attribute, not the 1 s poll cache.
        self._do_update_loaded_sample()

        # The drying time is not a data-freshness wait: _do_load_operation has
        # already waited for the CATS path, environment.wait_ready() and
        # diffractometer.mount_finished(). Backgrounding it lets the next queue
        # phase (optical centring) overlap the blower.
        if souflette_time:
            if self.souflette_blocking:
                gevent.sleep(self.souflette_seconds)
                self._do_update_loaded_sample()
            else:
                self._start_souflette(self.souflette_seconds)

        return True

    # ## SOUFLETTE (post-mount drying settle) ###

    def _start_souflette(self, seconds):
        """Arm the background drying timer. Returns immediately.

        Cancels any timer still pending from a previous load, so at most one
        souflette greenlet exists at a time.
        """
        self.cancel_souflette(reason="new load")
        self._souflette_done.clear()
        self._souflette_deadline = time.time() + seconds
        task = gevent.spawn(self._souflette_run, seconds)
        self._souflette_task = task
        # Identity-checked: kill(block=False) is asynchronous, so a cancelled
        # timer's callback can still fire after a newer one was armed and must
        # not signal that the new drying time is over.
        task.link(self._souflette_finished)
        logging.getLogger("HWR").info(
            "PX1Cryotong: souflette drying running in background for %s s", seconds
        )

    def _souflette_finished(self, task):
        """Release wait_souflette() when the current timer ends."""
        if self._souflette_task is task:
            self._souflette_done.set()

    def _souflette_run(self, seconds):
        """Sleep out the drying time, then re-confirm the CATS bookkeeping.

        The re-confirmation is skipped when this greenlet is no longer the
        current timer (a newer load, an unload or an abort took over), so a
        stale timer can never re-flag a sample that has since been unmounted.
        """
        try:
            gevent.sleep(seconds)
        except gevent.GreenletExit:
            return

        if self._souflette_task is not gevent.getcurrent():
            logging.getLogger("HWR").info(
                "PX1Cryotong: stale souflette timer, skipping late update"
            )
            return

        try:
            self._do_update_loaded_sample()
        except Exception:
            logging.getLogger("HWR").exception(
                "PX1Cryotong: souflette final update failed"
            )

    def cancel_souflette(self, reason=""):
        """Kill a pending drying timer. Idempotent."""
        task, self._souflette_task = self._souflette_task, None

        if task is not None and not task.ready():
            logging.getLogger("HWR").info(
                "PX1Cryotong: cancelling souflette timer (%s)", reason
            )
            task.kill(block=False)

        self._souflette_done.set()
        self._souflette_deadline = 0.0

    def souflette_remaining(self):
        """Seconds of drying time left; 0.0 when nothing is pending."""
        if self._souflette_done.is_set():
            return 0.0

        return max(0.0, self._souflette_deadline - time.time())

    def wait_souflette(self, timeout=None):
        """Block until the drying settle time elapsed.

        :returns: True if the wait completed or nothing was pending, False on
                  timeout.
        """
        self._souflette_done.wait(timeout)
        return self._souflette_done.is_set()

    def abort(self):
        self.cancel_souflette(reason="abort")
        super(PX1Cryotong, self).abort()

    # ## TRANSFER READINESS BARRIER ###

    def _wait_transfer_ready(self, timeout=None):
        """Block until the CATS can actually accept a transfer command.

        After every Put/Get the pathRunning handler fires do_dry_and_soak() and
        the CATS reports DevState DISABLE for the whole drying cycle; a command
        sent in that window is rejected by assert_can_execute_task() with
        "bad state (Disabled)". Every readiness check this class owns
        (wait_countdown, check_power_on, check_drysoak, env_send_transfer) lives
        inside _do_load, i.e. AFTER that assert, so the barrier has to sit here,
        in front of it.

        The authoritative signal is the CATS state, not our souflette timer: the
        drying cycle's real length is a hardware property. In a normal queue run
        this costs nothing - the previous sample's phases take minutes, so the
        changer is long since idle by the time the next transfer starts.
        """
        log = logging.getLogger("HWR")
        timeout = self.transfer_ready_timeout if timeout is None else timeout

        remaining = self.souflette_remaining()
        if remaining > 0:
            log.info(
                "PX1Cryotong: waiting %.0f s for the post-mount drying to finish",
                remaining,
            )

        t0 = time.time()
        # One budget for the whole barrier, not one per step.
        with gevent.Timeout(
            timeout,
            RuntimeError(
                "Sample changer not ready for a transfer after %s s" % timeout
            ),
        ):
            self.wait_souflette()
            # The CATS dry/soak countdown, if one is running.
            self.wait_countdown(timeout)

            # The PX1 supervisor is the other half of "can a transfer start
            # now": _do_load/_do_unload send it to the transfer phase, and it
            # refuses that command outright while it is moving. Non-raising, so
            # a busy supervisor still reaches env_send_transfer and produces the
            # message that names it.
            if not self.environment.wait_not_moving(timeout):
                log.warning(
                    "PX1Cryotong: environment still busy (%s), continuing anyway",
                    self.environment._describe(),
                )

            while True:
                # Nothing else refreshes these synchronously: _do_update_state()
                # is otherwise only called once at init, and self.state is left
                # at Loading after a task because _run's _set_state(Ready) is
                # commented out. Without this the barrier would decide on
                # whatever the 300 ms poller last cached.
                self._do_update_state()
                self._update_state()

                if self.is_ready():
                    break

                gevent.sleep(0.5)

        waited = time.time() - t0
        if waited > 1:
            log.info(
                "PX1Cryotong: sample changer ready after %.0f s (%s)",
                waited,
                SampleChangerState.tostring(self.state),
            )

        self.check_power_on()

    def load(self, sample=None, wait=True, wash=True):
        """Load, waiting first for the changer to be able to accept the command.

        Cats90.load() goes straight to _execute_task, whose
        assert_can_execute_task() rejects anything sent during the post-mount
        drying cycle. See _wait_transfer_ready.
        """
        self._wait_transfer_ready()

        return Cats90.load(self, sample=sample, wait=wait, wash=wash)

    def unload(self, sample=None, wait=True, wash=False):
        """Unload through the sample changer state machine.

        AbstractSampleChanger.unload() cannot be reused here: it raises when
        nothing is loaded and drops the PX1 specific wash flag. Going through
        _execute_task is what makes update_info() run and the web client learn
        that the goniometer is empty again.
        """
        self._wait_transfer_ready()
        self.cancel_souflette(reason="unload")
        self._update_state()
        sample = self._resolve_component(sample)
        self.assert_not_charging()

        return self._execute_task(
            SampleChangerState.Unloading, wait, self._do_unload, sample, wash
        )

    def _do_unload(self, sample=None, wash=None):
        print("\nDoing unload ... ")
        # The pin the pending timer is about to re-confirm is being removed.
        self.cancel_souflette(reason="unload")

        ret = self.check_power_on()
        if ret is False:
            logging.getLogger("user_level_log").error("CRYOTONG Cannot be powered")
            raise Exception(
                "CRYOTONG Cannot be powered. Aborting sample changer operation"
            )

        ret = self.env_send_transfer()

        if ret is False:
            logging.getLogger("user_level_log").error(
                "PX1 Environment cannot set transfer phase"
            )
            raise Exception(
                "Cryotong cannot get to transfer phase. Aborting sample changer operation"
            )

        self._do_unload_operation(sample)
        # Symmetric with _do_load: refresh the flags from the CATS attributes so
        # has_loaded_sample() and the loadedSampleChanged signal are correct as
        # soon as the unload returns.
        self._do_update_loaded_sample()

        return True

    def _do_unload_operation(self,sample_slot=None, shifts=None):
        # if not self.hasLoadedSample() or not self._chnSampleIsDetected.getValue():
        if not self.has_loaded_sample():
            msg = "Trying to unload sample, but it does not seem to be any on diffractometer"
            self.emit("catsError", msg)
            logging.getLogger("HWR").warning(msg)
            return

        if (sample_slot is not None):
            self._do_select(sample_slot)

        if shifts is None:
            xshift, yshift, zshift = ["0", "0", "0"]
        else:
            xshift, yshift, zshift = map(str,shifts)

        #loaded_lid = self._chnLidLoadedSample.get_value()
        argin = ["1", "0", xshift, yshift, zshift]
        #self.videohub_ho.select_camera("Robot", process="unmount")
        #self.videohub_ho.start_recording(file_prefix="unmount")
        logging.getLogger("HWR").warning("  ==========CATS=== unload sample, sending to cats:  %s" % argin)
        self._execute_server_task(self._cmdUnload, argin)
        #self.videohub_ho.select_camera("OAV", process="unmount")
        self.update_info()


    def check_power_on(self):
        print("\nChecking power on ....")
        if self._chnPowered.get_value():
            return True

        self._cmdPowerOn()

        timeout = 3
        t0 = time.time()

        while not self._chnPowered.get_value():
            gevent.sleep(0.3)
            if time.time() - t0 > timeout:
                logging.getLogger("HWR").warning(
                    "CRYOTONG: timeout waiting for power on"
                )
                break

        # Inverted until 2026-09-08: a successful PowerOn returned False, i.e.
        # "cannot be powered", and _do_load/_do_unload both abort on a False.
        return bool(self._chnPowered.get_value())


    def _init_sc_contents(self):
        """
        Initializes the sample changer content with default values.

        :returns: None
        :rtype: None
        """
        named_samples = {}
        if self.has_object("test_sample_names"):
            for tag, val in self["test_sample_names"].get_properties().items():
                named_samples[val] = tag

        # This override shadows Cats90._init_sc_contents, which is where the
        # baskets and basket_presence are normally set up - so do both here,
        # and only here. Both counts come from cryotong.xml (<no_of_baskets>,
        # <samples_per_basket>) and Cats90.init() has already resolved them by
        # the time it calls us. Without basket_presence, _update_cats_contents
        # raises AttributeError the first time anything connects to infoChanged.
        self.basket_presence = [None] * self.number_of_baskets

        self._clear_components()
        for basket_index in range(self.number_of_baskets):
            self._add_component(
                Container.Basket(
                    self, basket_index + 1, samples_num=self.samples_per_basket
                )
            )

        for basket_index in range(self.number_of_baskets):
            basket = self.components[basket_index]
            datamatrix = None
            present = True
            scanned = False
            basket._set_info(present, datamatrix, scanned)

        sample_list = []
        for basket_index in range(self.number_of_baskets):
            for sample_index in range(self.samples_per_basket):
                sample_list.append(
                    (
                        "",
                        basket_index + 1,
                        sample_index + 1,
                        1,
                        Container.Pin.STD_HOLDERLENGTH,
                    )
                )

        for spl in sample_list:
            address = Container.Pin.get_sample_address(spl[1], spl[2])
            sample = self.get_component_by_address(address)
            sample_name = named_samples.get(address)
            if sample_name is not None:
                sample._name = sample_name
            datamatrix = "matr%d_%d" % (spl[1], spl[2])
            present = scanned = loaded = has_been_loaded = False
            sample._set_info(present, datamatrix, scanned)
            sample._set_loaded(loaded, has_been_loaded)
            sample._set_holder_length(spl[4])

        self._set_state(SampleChangerState.Ready)

    def check_drysoak(self):
        print("Checking drysoak")
        if self._chnHomeOpened.get_value() is False:
            return True

        self._cmdDrySoak()

        gevent.sleep(3)
        t0 = time.time()
        wait_n = 0
        while self._is_device_busy():
            if wait_n % 10 == 3:
                logging.getLogger("HWR").warning(
                    "CRYOTONG: waiting for dry and soak to complete"
                )
            gevent.sleep(0.3)
            wait_n += 1

        if self._is_device_ready() and self._chnHomeOpened.get_value() is False:
            return True
        else:
            return False

    def env_send_transfer(self):
        print("\nEnv_send_transfer ... (px1cryotong)")

        if self.environment.ready_for_transfer():
            return True
        logging.getLogger("user_level_log").warning(
            "CRYOTONG: Not ready for transfer. sending it"
        )
        try:
            self.environment.set_phase(EnvironmentPhase.TRANSFER)
        except Exception:
            # A refused phase command used to propagate out of load() and be
            # reported as "Error loading sample, please check sample changer",
            # which named the wrong device. Returning False gets the caller's
            # "Cryotong cannot get to transfer phase" message instead.
            logging.getLogger("HWR").exception(
                "CRYOTONG: could not send the environment to transfer phase (%s)",
                self.environment._describe(),
            )
            return False
        timeout = 10
        t0 = time.time()
        while not self.environment.ready_for_transfer():
            gevent.sleep(0.3)
            if time.time() - t0 > timeout:
                logging.getLogger("HWR").warning(
                    "CRYOTONG: timeout waiting for transfer phase"
                )
                return False

            logging.getLogger("HWR").warning(
                "CRYOTONG: waiting for transfer phase to be set"
            )
        if not self.environment.ready_for_transfer():
            return False

        logging.getLogger("HWR").warning("CRYOTONG: ready for transfer now")
        return True

    # ## (END) OVERLOADED CATS90 methods ####

def test_hwo(hwo):
    import gevent

    basket_list = hwo.get_basket_list()
    sample_list = hwo.get_sample_list()
    print("Baskets/Samples in CATS: %s/%s" % (len(basket_list), len(sample_list)))
    gevent.sleep(2)
    sample_list = hwo.get_sample_list()
    print("No of samples is ", len(sample_list))

    for s in sample_list:
        if s.is_loaded():
            print("Sample %s loaded" % s.get_address())
            break

    if hwo.has_loaded_sample():
        print(
            "Currently loaded (%s): %s"
            % (hwo.has_loaded_sample(), hwo.get_loaded_sample().get_address())
        )

    print("\nCATS model is: ", hwo.cats_model)
    print("CATS state is: ", hwo.state)
    print("Sample on Magnet : ", hwo.cats_sample_on_diffr())
    print("All lids closed: ", hwo._chnAllLidsClosed.get_value())

    print("Sample Changer State is: ", hwo.get_status())
    for basketno in range(hwo.number_of_baskets):
        no = basketno + 1
        print("Tool for basket %d is: %d" % (no, hwo.tool_for_basket(no)))