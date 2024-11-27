from __future__ import print_function
import logging
import gevent
import time
from mxcubecore.HardwareObjects.abstract.sample_changer import Container
import PyTango
import math


from Cats90 import (
    Cats90,
    SampleChangerState,
    BASKET_UNIPUCK,
)

from PX1Environment import EnvironmentPhase


class PX1Cryotong(Cats90):

    __TYPE__ = "Cryotong"

    default_no_lids = 1
    baskets_per_lid = 3

    default_basket_type = BASKET_UNIPUCK

    def __init__(self, *args, **kwargs):

        super(PX1Cryotong, self).__init__(*args, **kwargs)
        # Generates a list of baskets
        self.components = [ Container.Basket(self, i +1, 16) for i in range(16) ]
        self._safeNeeded = None
        self._homeOpened = None
        self.dry_and_soak_needed = False
        self.count_down = None
        self.no_of_samples_in_basket = 16
        self.soft_auth = None
        self.incoherent_state = None

    def init(self):
        super(PX1Cryotong, self).init()
        self._num_loaded = self._chnNumLoadedSample.value if self._chnNumLoadedSample.value != -1 else None
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


        for i in range(self.no_of_baskets):
            basket = Container.Basket(
                self, i + 1, samples_num = self.no_of_samples_in_basket
            )
            self._add_component(basket)
        self._chnNumLoadedSample.connect_signal("update", self._update_num_loaded)

        self._init_sc_contents()
        self._do_update_state()
        self._update_loaded_list()

    # ## CRYOTONG SPECIFIC METHODS ###
    def is_string_true (self, val):
        if isinstance (val, bool):
            return val
        else :
            return str(val) in ["True","true"]

    def _update_num_loaded(self, value):
        print(f"UPDATE NUM LOADED TRIGGERED +++ wit value {value}")
        if value !=  self._num_loaded:
            self._num_loaded = value
            self._update_loaded_list()


    def _update_loaded_list (self):
        """Upsdates the list of sample objects by changing their loaded property
        """
        if self._num_loaded :
            smp = self._num_loaded % 16
            comp_no = math.ceil(int(self._num_loaded) / 16) -1
            smp_no = smp-1 if smp != 0 else 15
            # reset all previous load
            for sample in self.components[comp_no].get_sample_list():
                sample.loaded = False
            # update loaded for new sample
            self.components[comp_no].get_sample_list()[smp_no].loaded = True


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
        print("\nTime to dry ans soak .... ")
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

        if self.has_loaded_sample() :
            if selected==self.get_loaded_sample() and not wash:
                msg = "Load aborted. Reason: \nSample " + str(self.get_loaded_sample().get_address()) + " already loaded"
                logging.getLogger("user_level_log").info(msg)
                self.emit("catsError", msg)
                self._update_state()
                raise Exception(msg)
            else:
                logging.getLogger("HWR").warning("  ==========CATS=== chained load sample, sending to cats:  %s" % argin)
                self._execute_server_task(self._cmdChainedLoad, argin)
        else:
            print(f'NO LOADED SAMPLE -- {self.has_loaded_sample()}')
            if self.cats_sample_on_diffr():
                logging.getLogger("HWR").warning("  ==========CATS=== trying to load sample, but sample detected on diffr. Exchanging samples")
                self._update_state() # remove software flags like Loading.
                self._execute_server_task(self._cmdChainedLoad, argin)
            else:
                logging.getLogger("HWR").warning("  ==========CATS=== load sample, sending to cats:  %s" % argin)
                self._execute_server_task(self._cmdLoad, argin)
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

    def _do_load(self, sample=None, wash=None):
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
        self._update_loaded_list()

    def _do_unload(self, sample=None, wash=None):
        print("\nDoing unload ... ")

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
        self._update_loaded_list()

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

        if self._chnPowered.get_value():
            return False

        return True


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


        for basket_index in range(self.no_of_baskets):
            basket = self.components[basket_index]
            datamatrix = None
            present = True
            scanned = False
            basket._set_info(present, datamatrix, scanned)

        sample_list = []
        for basket_index in range(self.no_of_baskets):
            for sample_index in range(16):
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


    def check_drysoak(self):
        print("Checking drysoak")
        if self._chnHomeOpened.get_value() is False:
            return True

        self._cmdDrySoak()

        time.sleep(3)
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
        self.environment.set_phase(EnvironmentPhase.TRANSFER)
        timeout = 10
        t0 = time.time()
        while not self.environment.ready_for_transfer():
            gevent.sleep(0.3)
            if time.time() - t0 > timeout:
                logging.getLogger("HWR").warning(
                    "CRYOTONG: timeout waiting for transfer phase"
                )
                break
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