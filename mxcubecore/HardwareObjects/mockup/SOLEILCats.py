"""
#  Project: MXCuBE
#  https://github.com/mxcube
#
#  This file is part of MXCuBE software.
#
#  MXCuBE is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  MXCuBE is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with MXCuBE. If not, see <http://www.gnu.org/licenses/>.
"""

import time
import logging
import gevent

from mxcubecore.HardwareObjects.Cats90 import *

from cats import cats
from goniometer import goniometer
cats_api = cats()

class SoleilPuck(Basket):
    def __init__(self, container, number, samples_num=16, name="UniPuck", parent=None):
        super(Basket, self).__init__(
            self.__TYPE__, container, Basket.get_basket_address(number), True
        )

        self._name = name
        self.samples_num = samples_num
        self.parent = parent
        for i in range(samples_num):
            slot = Pin(self, number, i + 1)
            self._add_component(slot)
            if self.parent is not None:
                self.parent.component_by_adddress[slot.get_address()] = slot
        
class SOLEILCats(Cats90):
    """
    Actual implementation of the CATS Sample Changer,
       BESSY BL14.1 installation with 3 lids and 90 samples

    """

    __TYPE__ = "CATS"

    default_no_lids = 3
    baskets_per_lid = 3
    default_samples_per_basket = 16
    default_no_of_baskets = 9
    default_basket_type = BASKET_UNIPUCK
    DETECT_PUCKS = True
    default_soak_lid = 2
    
    def init(self):

        self.cats_api = cats_api
        self.goniometer = goniometer()
        self._selected_sample = None
        self._selected_basket = None
        self._scIsCharging = None

        self.read_datamatrix = False
        self.unipuck_tool = TOOL_UNIPUCK

        self.former_loaded = None
        self.cats_device = None
        self.component_by_adddress = {}
        
        self.cats_datamatrix = ""
        self.cats_loaded_lid = None
        self.cats_loaded_num = None

        # Default values
        self.cats_powered = False
        self.cats_status = ""
        self.cats_running = False
        self.cats_state = 'Unknown'
        self.cats_lids_closed = False

        self.basket_types = None
        
        self._toolopen = None
        self._powered = None
        self._running = None
        self._regulating = None
        self._lid1state = None
        self._lid2state = None
        self._lid3state = None
        #self.no_of_baskets = None
        self._message = None
        
        # add support for CATS dewars with variable number of lids

        # Create channels from XML
        self.tangoname = self.get_property("tangoname")
        logging.getLogger("HWR").debug('tangoname %s' % self.tangoname)
        self.polling = self.get_property("polling")
        logging.getLogger("HWR").debug('polling %s ' % self.polling)
        self.no_of_lids = self.get_property("no_of_lids%", self.default_no_lids)
        logging.getLogger("HWR").debug('no_of_lids %s ' %  self.no_of_lids)
        self.no_of_baskets = self.get_property("no_of_baskets", self.default_no_of_baskets)
        logging.getLogger("HWR").debug('no_of_baskets %s ' % self.no_of_baskets)
        self.samples_per_basket = self.get_property("samples_per_basket", self.default_samples_per_basket)
        logging.getLogger("HWR").debug('samples_per_basket %s ' % self.samples_per_basket)
        self.do_detect_pucks = self.get_property('detect_pucks', SOLEILCats.DETECT_PUCKS)
        logging.getLogger("HWR").debug('do_detect_pucks %s ' % self.do_detect_pucks)
        self.use_update_timer = self.get_property('update_timer', True)
        logging.getLogger("HWR").debug('use_update_timer %s ' % self.use_update_timer)
        self.soak_lid = self.get_property("no_soak_lid", self.default_soak_lid)
        logging.getLogger("HWR").debug('soak_lid %s ' % self.soak_lid)
        
        # find number of baskets and number of samples per basket
        self.basket_types = [None] * self.no_of_baskets

        # declare channels to detect basket presence changes
        self.basket_channels = []
        
        # Create channels
        # device_name, internal_name, update_method_name
        channel_attributes = \
            (
                ("State", "State", "_update_state"),
                #("Status", "Status", "_update_status"),
                ("Powered", "Powered", "_update_powered_state"),
                ("PathRunning", "PathRunning", "_update_running_state"),
                ("NumSampleOnDiff", "NumLoadedSample", "_update_loaded_sample"), 
                ("LidSampleOnDiff", "LidLoadedSample", "_update_loaded_sample"), 
                ("Barcode", "SampleBarcode", "_update_barcode"), 
                ("di_AllLidsClosed", "AllLidsClosed", "_update_global_state"), 
                ("Message", "Message", "_update_message"),
                ("LN2Regulating", "LN2RegulationDewar1", "_update_regulation_state"),
                ("Tool", "CurrentTool","_update_tool_state")
            )
        channel_attributes += tuple((("di_Lid%dOpen" % k, "lid%d_state" % k, "_update_lid%d_state" % k) for k in range(1, self.no_of_lids+1))) 
        channel_attributes += tuple((("di_Cassette%dPresence" % k, "Basket%dState" % k, "_update_basket%d_state" % k) for k in range(1, self.no_of_baskets+1)))
    
        for channel_attribute in channel_attributes:
            if type(channel_attribute) == tuple:
                channel_name_in_device = channel_attribute[0]
                _channel_name = "_chn%s" % channel_attribute[1]
                channel_name = channel_attribute[1]
                _update_method_name = channel_attribute[2]
            else:
                channel_name_in_device = channel_attribute
                _channel_name = "_chn%s" % channel_attribute
                channel_name = channel_attribute
                _update_method_name = "_update_%s" % channel_attribute
                
            channel = self.add_channel(
                {
                    "type": "tango",
                    "name": channel_name,
                    "tangoname": self.tangoname,
                    "polling": self.polling,
                },
                 channel_name_in_device)
            logging.getLogger("HWR").debug('adding channel %s %s' % (_channel_name, str(channel)))
            setattr(self, _channel_name, channel)
            
            if "Basket" in _channel_name or "Cassette" in _channel_name:
                self.basket_channels.append(channel)
            elif "status" in _channel_name.lower():
                pass
            elif "NumLoadedSample" in _channel_name:
                pass            
            else:
                logging.debug('connecting signal update from %s to %s' % (_channel_name, _update_method_name))
                #channel_object = self.get_channel_object(_channel_name)
                channel_object = getattr(self, _channel_name)
                if channel_object is not None:
                    getattr(self, _update_method_name)(channel_object.get_value())
                    channel_object.connect_signal("update", getattr(self, _update_method_name))
                else:
                    logging.warning('connecting signal update from %s to %s did not work' % (_channel_name, _update_method_name))
            
        #self._chnSampleIsDetected = self.get_channel_object("_chnSampleIsDetected")
        #self._chnSampleIsDetected.connect_signal("update", self._update_loaded_sample)
        
        #self._chnState.connect_signal("update", self.cats_state_changed)
        #self._chnPathRunning.connect_signal("update", self.cats_pathrunning_changed)
        #self._chnPowered.connect_signal("update", self.cats_powered_changed)
        #self._chnPathSafe.connect_signal("update", self.cats_pathsafe_changed)
        #self._chnAllLidsClosed.connect_signal("update", self.cats_lids_closed_changed)
        #self._chnNumLoadedSample.connect_signal("update", self._update_loaded_sample)
        
        ##"LidLoadedSample", "SampleBarcode"
        
        #for channel_name in ("State", "Powered", "PathRunning", "NumLoadedSample", "SampleIsDetected",  "TotalLidState", "Message", "LN2RegulationDewar1"):
            #try:
                #chn = "_chn%s" % channel_name
                #logging.getLogger('HWR').info('initializing %s ' % chn)
                #setattr(self, chn, self.get_channel_object(chn))
                #if getattr(self, chn) is not None:
                    #getattr(self, chn).connect_signal("update", getattr(self, '_update%s' % channel_name))
            #except:
                #logging.getLogger("HWR").warning("Cannot create channel for %s. Check xml" % channel_name)
                #logging.getLogger('HWR').exception(traceback.format_exc())
                        
        #for lid_index in range(self.no_of_lids):
            #try:
                #channel_name = "_chnLid%dState" % (lid_index + 1)
                #setattr(self, channel_name, self.get_channel_object(channel_name))
                #if getattr(self, channel_name) is not None:
                    #getattr(self, channel_name).connect_signal("update", getattr(self, "_updateLid%dState" % (lid_index + 1)))
            #except:
                #logging.getLogger("HWR").warning("Cannot create channel for %s. Check xml" % channel_name)
                #logging.getLogger('HWR').exception(traceback.format_exc())
                
        # Create commands
        command_attributes = \
            (
                ("Load", "put"),
                ("Unload", "get"),
                ("ChainedLoad", "getput"),
                "Abort",
                ("ScanSample", "barcode"),
                "PowerOn",
                "PowerOff",
                "RegulOn",
                "RegulOff",
                "Reset",
                "Back",
                "Safe",
                "Home",
                ("Dry", "dry_soak"),
                ("DrySoak", "dry_soak"),
                "Soak",
                ("ResetParameters", "reset_parameters"),
                ("ClearMemory", "clear_memory"),
                ("AckSampleMemory", "ack_sample_memory"),
                "OpenTool",
                "ToolCal",
                "OpenLid1",
                ("OpenLid2", "home_openlid2"),
                "OpenLid3",
                "CloseLid1",
                "CloseLid2",
                "CloseLid3"
            )
        
        for command_attribute in command_attributes:
            if type(command_attribute) == tuple:
                command_name = command_attribute[1]
                _command_name = "_cmd%s" % command_attribute[0]
            else:
                command_name = command_attribute.lower()
                _command_name = "_cmd%s" % command_attribute
            command = self.add_command(
                {
                    "type": "tango",
                    "name": _command_name,
                    "tangoname": self.tangoname,
                },
                command_name,
            )
            setattr(self, _command_name, command)
           
        self.cats_model = "CATS"
        self.basket_presence = [True] * self.no_of_baskets
        self._init_sc_contents()

        #
        # connect channel signals to update info
        #

        self.use_update_timer = False  # do not use update_timer for Cats

        # connect presence channels
        if self.do_detect_pucks:
            if self.basket_channels is not None:  # old device server
                for basket_index in range(self.no_of_baskets):
                    channel = self.basket_channels[basket_index]
                    channel.connect_signal("update", self.cats_basket_presence_changed)
            else:  # new device server with global CassettePresence attribute
                self._chnBasketPresence.connect_signal("update", self.cats_baskets_changed)

        # Read other XML properties
        read_datamatrix = self.get_property("read_datamatrix")
        if read_datamatrix:
            self.set_read_barcode(True)

        unipuck_tool = self.get_property("unipuck_tool")
        try:
            unipuck_tool = int(unipuck_tool)
            if unipuck_tool:
                self.set_unipuck_tool(unipuck_tool)
        except Exception:
            pass

    def get_basket_list(self):
        basket_list = []
        dewar_content = self.cats_api.get_dewar_content()
        k = 0
        for basket in self.get_components():
            if isinstance(basket, Basket):
                basket._name = dewar_content[k]
                k+=1
                basket_list.append(basket)
        return basket_list
    
    def _get_by_address(self, address):
        try:
            component = self.component_by_adddress[address]
        except KeyError:
            component = self.get_component_by_address(address)
            self.component_by_adddress[address] = component
        return component 
    
    def _init_sc_contents(self, separator="_"):
        """
        Initializes the sample changer content with default values.

        :returns: None
        :rtype: None
        """
        _start = time.time()
        logging.getLogger("HWR").info("initializing contents self %s" % self)

        for i in range(self.no_of_baskets):
            if self.basket_types[i] == BASKET_SPINE:
                basket = SpineBasket(self, i + 1)
            elif self.basket_types[i] == BASKET_UNIPUCK:
                basket = UnipuckBasket(self, i + 1)
            else:
                basket = SoleilPuck(self, i + 1, samples_num=self.samples_per_basket, parent=self)

            self._add_component(basket)
            self.component_by_adddress[basket.get_address()] = basket
            
        # write the default basket information into permanent Basket objects
        for basket_index in range(self.no_of_baskets):
            basket = self.get_components()[basket_index]
            datamatrix = None
            present = scanned = False
            basket._set_info(present, datamatrix, scanned)

        # create temporary list with default sample information and indices
        sample_list = []
        for basket_index in range(self.no_of_baskets):
            basket = self.get_components()[basket_index]
            for sample_index in range(basket.get_number_of_samples()):
                sample_list.append(
                    ("", basket_index + 1, sample_index + 1, 1, Pin.STD_HOLDERLENGTH)
                )

        # write the default sample information into permanent Pin objects
        for spl in sample_list:
            address = "%d%s%02d" % (spl[1], separator, spl[2])
            sample = self._get_by_address(address)
            datamatrix = None
            present = scanned = loaded = _has_been_loaded = False
            sample._set_info(present, datamatrix, scanned)
            sample._set_loaded(loaded, _has_been_loaded)
            sample._set_holder_length(spl[4])

        logging.getLogger("HWR").info("initializing contents took %.6f" % (time.time()-_start))
        
    def _do_update_cats_contents(self, separator="_"):
        """
        Updates the sample changer content. The state of the puck positions are
        read from the respective channels in the CATS Tango DS.
        The CATS sample sample does not have an detection of each individual sample, so all
        samples are flagged as 'Present' if the respective puck is mounted.

        :returns: None
        :rtype: None
        """

        for basket_index in range(self.no_of_baskets):
            # get presence information from the device server
            if self.do_detect_pucks:
                channel = self.basket_channels[basket_index]
                is_present = channel.get_value()
            else:
                is_present = True
            self.basket_presence[basket_index] = is_present

        self._update_cats_contents(separator=separator)
    
    def _update_cats_contents(self, separator="_"):
        _start = time.time()
        logging.getLogger("HWR").info(
            "Updating contents %s" % str(self.basket_presence)
        )
        for basket_index in range(self.no_of_baskets):
            # get saved presence information from object's internal bookkeeping
            basket = self.get_components()[basket_index]
            is_present = self.basket_presence[basket_index]

            if is_present is None:
                continue

            # check if the basket presence has changed
            if is_present ^ basket.is_present():
                # a mounting action was detected ...
                if is_present:
                    # basket was mounted
                    present = True
                    scanned = False
                    datamatrix = None
                    basket._set_info(present, datamatrix, scanned)
                else:
                    # basket was removed
                    present = False
                    scanned = False
                    datamatrix = None
                    basket._set_info(present, datamatrix, scanned)

                # set the information for all dependent samples
                for sample_index in range(basket.get_number_of_samples()):
                    address = Pin.get_sample_address((basket_index + 1), (sample_index + 1), separator=separator)
                    sample = self._get_by_address(address)
                        
                    present = sample.get_container().is_present()
                    if present:
                        datamatrix = "          "
                    else:
                        datamatrix = None
                    scanned = False
                    sample._set_info(present, datamatrix, scanned)

                    # forget about any loaded state in newly mounted or removed basket)
                    loaded = _has_been_loaded = False
                    sample._set_loaded(loaded, _has_been_loaded)

        self._trigger_contents_updated_event()
        self._update_loaded_sample()
        logging.getLogger("HWR").debug('_update_cats_contents took %.6f' % (time.time() - _start))
        
    def check_power_on(self):
        if not self._chnPowered.get_value():
            logging.getLogger().info("CATS power is not enabled. Switching on the arm power ...")
            try:
                self.cats_api.on()
            except:
                logging.getLogger('HWR').info('in powerOn exception %s' % traceback.format_exc())

    def load(self, separator="_", sample=None, wait=True):
        """
        Load a sample.
            overwrite original load() from AbstractSampleChanger to allow finer decision
            on command to use (with or without barcode / or allow for wash in some cases)
            Implement that logic in _do_load()
            Add initial verification about the Powered:
            (NOTE) In fact should be already as the power is considered in the state handling
        """

        self._update_state()  # remove software flags like Loading.
        logging.getLogger().info('in load')
        self.assert_not_charging()
        self.check_power_on()
        location = sample
        logging.getLogger('HWR').info('load, location %s' % str(location))

        if type(location) == str:
            puck, sample = map(int, location.split(separator))
        else:
            puck, sample = location
            
        lid = (puck - 1) / self.no_of_lids + 1
        sample_in_lid = ((puck - 1) % self.no_of_lids) * self.no_of_samples_in_basket + sample
        lid = int(lid)
        sample_in_lid = int(sample_in_lid)
        logging.getLogger('HWR').info('load, puck %d, sample %d (lid: %d, sample in lid: %d)' % (puck, sample, lid, sample_in_lid))
        
        self.cats_api.getput(lid, sample_in_lid, wait=True)
        self._trigger_info_changed_event()

    def unload(self, sample_slot=None, wait=True):
        logging.getLogger().info('in unload')
        self.assert_not_charging()
        self.check_power_on()
        self.cats_api.get(wait=True)
 
    def _update_loaded_sample(self, sample_num=None, lid=None, separator="_"):
        _start = time.time()
        if None in [sample_num, lid]:
            loadedSampleNum = self._chnNumLoadedSample.get_value()
            loadedSampleLid = self._chnLidLoadedSample.get_value()
        else:
            loadedSampleNum = sample_num
            loadedSampleLid = lid

        self.cats_loaded_lid = loadedSampleLid
        self.cats_loaded_num = loadedSampleNum

        logging.getLogger("HWR").debug(
            "Updating loaded sample %d%s%02d" % (loadedSampleLid, separator, loadedSampleNum)
        )

        if -1 not in [loadedSampleLid, loadedSampleNum]:
            basket, sample = self.lidsample_to_basketsample(
                loadedSampleLid, loadedSampleNum
            )
            address = "%d%s%02d" % (basket, separator, sample)
            new_sample = self._get_by_address(address)
        else:
            basket, sample = None, None
            new_sample = None
            address="None"
            
        logging.getLogger("HWR").info(
            "Updating loaded sample %s" % (address)
        )
        
        logging.getLogger("HWR").debug(
            "about to call get_loaded_sample")
        old_sample = self.get_loaded_sample(puck=basket, sample=sample)
        
        logging.getLogger("HWR").debug(
            "get_loaded_sample returned %s" % old_sample)
        
        logging.getLogger("HWR").debug(
            "new_sample is %s" % new_sample)
        if old_sample != new_sample:
            # remove 'loaded' flag from old sample but keep all other information

            if old_sample is not None:
                # there was a sample on the gonio
                loaded = False
                has_been_loaded = True
                old_sample._set_loaded(loaded, has_been_loaded)

            if new_sample is not None:
                loaded = True
                has_been_loaded = True
                new_sample._set_loaded(loaded, has_been_loaded)

            if (
                (old_sample is None)
                or (new_sample is None)
                or (old_sample.get_address() != new_loaded.get_address())
            ):
                self._trigger_loaded_sample_changed_event(new_sample)
                self._trigger_info_changed_event()
        #self.update_info()
        self._trigger_info_changed_event()
        logging.getLogger('HWR').debug('_update_loaded_sample took %.4f' % (time.time() - _start))
    
    def cats_state_changed(self, value=None):
        logging.debug('cats_state_changed %s' % value)
        self.cats_state = value
        self._update_state()
        
    def has_loaded_sample(self):
        #self._update_loaded_sample()
        #sample_mounted = self.cats_api.sample_mounted()
        return self.goniometer.sample_is_loaded()
    
        #LoadedSample = self._chnNumLoadedSample.get_value()
        #logging.getLogger('HWR').debug('has_loaded_sample, self.cats_loaded_lid %s, self.cats_loaded_num %s, LoadedSample %s' % (self.cats_loaded_lid, self.cats_loaded_num, LoadedSample))
        #if self.cats_loaded_lid != -1 or self.cats_loaded_num != -1 or LoadedSample>0:
            #return True
        #else:
            #return False

    def get_loaded_sample(self, separator="_", puck=None, sample=None):
        if puck is None or sample is None:
            logging.getLogger("HWR").debug('in get_loaded_sample, querying cats device for NumLoadedSample and LidLoadedSample')
            loadedSampleNum = int(self._chnNumLoadedSample.get_value())
            loadedSampleLid = int(self._chnLidLoadedSample.get_value())
            logging.getLogger("HWR").debug('NumLoadedSample %d, LidLoadedSample %d' % (loadedSampleNum, loadedSampleLid))
            puck, sample = self.lidsample_to_basketsample(
                loadedSampleLid, loadedSampleNum
            )
            if loadedSampleLid is None or loadedSampleLid is None:
                logging.getLogger("HWR").info('in get_loaded_sample, querying cats_api get_mounted_puck_and_sample')
                puck, sample = self.cats_api.get_mounted_puck_and_sample()
                logging.getLogger("HWR").info('sample %d, puck %d' % (sample, puck))
            
        address = '%d%s%02d' % (puck, separator, sample)
        logging.getLogger("HWR").debug('in get_loaded_sample, address %s' % address)
        return self.get_component_by_address(address)

    def assert_not_charging(self):
        """
        Raises:
            (Exception): If sample changer is not charging
        """
        #if self.state == SampleChangerState.Charging:
        if self.cats_running:
            raise Exception("Sample Changer is in Charging mode")
        
        
    ### from CatsMaint
    ################################################################################

    def back_traj(self):
        """
        Moves a sample from the gripper back into the dewar to its logged position.
        """
        return self._execute_task(False, self._do_back)

    def safe_traj(self):
        """
        Safely Moves the robot arm and the gripper to the home position
        """
        return self._execute_task(False, self._do_safe)

    def _do_abort(self):
        """
        Launch the "abort" trajectory on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        self._cmdAbort()

    def _do_home(self):
        """
        Launch the "abort" trajectory on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        tool = self.get_current_tool()
        self._cmdHome(tool)

    def _do_reset(self):
        """
        Launch the "reset" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        logging.getLogger("HWR").debug("CatsMaint. doing reset")
        return
        self._cmdReset()

    def _do_reset_memory(self):
        """
        Launch the "reset memory" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        self._cmdClearMemory()
        gevent.sleep(1)
        self._cmdResetParameters()
        gevent.sleep(1)

    def _do_reset_motion(self):
        """
        Launch the "reset_motion" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        self._cmdResetMotion()

    def _do_recover_failure(self):
        """
        Launch the "recoverFailure" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        self._cmdRecoverFailure()

    def _do_calibration(self):
        """
        Launch the "toolcalibration" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        tool = self.get_current_tool()
        self._cmdCalibration([tool])

    def _do_open_tool(self):
        """
        Launch the "opentool" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        self._cmdOpenTool()

    def _do_close_tool(self):
        """
        Launch the "closetool" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        self._cmdCloseTool()

    def _do_dry_gripper(self):
        """
        Launch the "dry" command on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        tool = self.get_current_tool()
        self._cmdDrySoak([str(tool), str(self.soak_lid)])

        
    def _do_set_on_diff(self, sample):
        """
        Launch the "setondiff" command on the CATS Tango DS, an example of sample value is 2:05

        :returns: None
        :rtype: None
        """

        if sample is None:
            raise Exception("No sample selected")
        else:
            str_tmp = str(sample)
            sample_tmp = str_tmp.split(":")
            # calculate CATS specific lid/sample number
            lid = (int(sample_tmp[0]) - 1) / 3 + 1
            puc_pos = ((int(sample_tmp[0]) - 1) % 3) * 10 + int(sample_tmp[1])
            argin = [str(lid), str(puc_pos), "0"]
            logging.getLogger().info("to SetOnDiff %s", argin)
            self._execute_server_task(self._cmdSetOnDiff, argin)

    def _do_back(self):
        """
        Launch the "back" trajectory on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        tool = self.get_current_tool()
        argin = [str(tool), "0"]  # to send string array with two arg...
        self._execute_server_task(self._cmdBack, argin)

    def _do_safe(self):
        """
        Launch the "safe" trajectory on the CATS Tango DS

        :returns: None
        :rtype: None
        """
        argin = self.get_current_tool()
        self._execute_server_task(self._cmdSafe, argin)

    def _do_power_state(self, state=False):
        """
        Switch on CATS power if >state< == True, power off otherwise

        :returns: None
        :rtype: None
        """
        logging.getLogger("HWR").debug("   running power state command ")
        if state:
            self._cmdPowerOn()
        else:
            self._cmdPowerOff()

        #self.do_state_action("power", state)

    def _do_enable_regulation(self):
        """
        Switch on CATS regulation

        :returns: None
        :rtype: None
        """
        self._cmdRegulOn()

    def _do_disable_regulation(self):
        """
        Switch off CATS regulation

        :returns: None
        :rtype: None
        """
        self._cmdRegulOff()

    def _do_lid1_state(self, state=True):
        """
        Opens lid 1 if >state< == True, closes the lid otherwise

        :returns: None
        :rtype: None
        """
        if state:
            self._execute_server_task(self._cmdOpenLid1)
        else:
            self._execute_server_task(self._cmdCloseLid1)

    def _do_lid2_state(self, state=True):
        """
        Opens lid 2 if >state< == True, closes the lid otherwise

        :returns: None
        :rtype: None
        """
        if state:
            self._execute_server_task(self._cmdOpenLid2)
        else:
            self._execute_server_task(self._cmdCloseLid2)

    def _do_lid3_state(self, state=True):
        """
        Opens lid 3 if >state< == True, closes the lid otherwise

        :returns: None
        :rtype: None
        """
        logging.debug('_do_lid3_state state %s' % state)
        if state:
            self._execute_server_task(self._cmdOpenLid3)
        else:
            self._execute_server_task(self._cmdCloseLid3)

    def _do_magnet_on(self):
        self._execute_server_task(self._cmdMagnetOn)

    def _do_magnet_off(self):
        self._execute_server_task(self._cmdMagnetOff)

    def _do_tool_open(self):
        self._execute_server_task(self._cmdToolOpen)

    def _do_tool_close(self):
        self._execute_server_task(self._cmdToolClose)

    # ########################          PROTECTED          #########################

    def _execute_task(self, wait, method, *args):
        ret = self._run(method, wait=False, *args)
        if wait:
            return ret.get()
        else:
            return ret

    @task
    def _run(self, method, *args):
        exception = None
        ret = None
        try:
            ret = method(*args)
        except Exception as ex:
            exception = ex
        if exception is not None:
            raise exception
        return ret

    # ########################           PRIVATE           #########################

    def _update_running_state(self, value):
        self._running = value
        self.emit("runningStateChanged", (value,))
        self._update_global_state()

    def _update_powered_state(self, value):
        self._powered = value
        self.emit("powerStateChanged", (value,))
        self._update_global_state()

    def _update_tool_state(self, value):
        self._toolopen = value
        self.emit("toolStateChanged", (value,))
        self._update_global_state()

    def _update_message(self, value):
        self._message = value
        self.emit("messageChanged", (value,))
        self._update_global_state()

    def _update_regulation_state(self, value):
        self._regulating = value
        self.emit("regulationStateChanged", (value,))
        self._update_global_state()

    def _update_barcode(self, value):
        self._barcode = value
        self.emit("barcodeChanged", (value,))

    def _update_state(self, value=None, value2=None):
        logging.debug('_update_state %s, %s' % (value, value2))
        self._state = value
        self._update_global_state()

    def _update_lid1_state(self, value):
        self._lid1state = value
        self.emit("lid1StateChanged", (value,))
        self._update_global_state()

    def _update_lid2_state(self, value):
        self._lid2state = value
        self.emit("lid2StateChanged", (value,))
        self._update_global_state()

    def _update_lid3_state(self, value):
        self._lid3state = value
        self.emit("lid3StateChanged", (value,))
        self._update_global_state()

    def _update_operation_mode(self, value):
        self._charging = not value

    def _update_global_state(self, *args):
        logging.debug("_update_global_state %s" % str(args))
        state_dict, cmd_state, message = self.get_global_state()
        self.emit("globalStateChanged", (state_dict, cmd_state, message))

    def get_global_state(self):
        """
           Update clients with a global state that
           contains different:

           - first param (state_dict):
               collection of state bits

           - second param (cmd_state):
               list of command identifiers and the
               status of each of them True/False
               representing whether the command is
               currently available or not

           - message
               a message describing current state information
               as a string
        """
        _ready = str(self._state) in ("READY", "ON")

        if self._running:
            state_str = "MOVING"
        elif not (self._powered) and _ready:
            state_str = "DISABLED"
        elif _ready:
            state_str = "READY"
        else:
            state_str = str(self._state)

        state_dict = {
            "toolopen": self._toolopen,
            "powered": self._powered,
            "running": self._running,
            "regulating": self._regulating,
            "lid1": self._lid1state,
            "lid2": self._lid2state,
            "lid3": self._lid3state,
            "state": state_str,
        }

        cmd_state = {
            "powerOn": (not self._powered) and _ready,
            "powerOff": (self._powered) and _ready,
            "regulon": (not self._regulating) and _ready,
            "openlid1": (not self._lid1state) and self._powered and _ready,
            "closelid1": self._lid1state and self._powered and _ready,
            "dry": (not self._running) and self._powered and _ready,
            "soak": (not self._running) and self._powered and _ready,
            "home": (not self._running) and self._powered and _ready,
            "back": (not self._running) and self._powered and _ready,
            "safe": (not self._running) and self._powered and _ready,
            "clear_memory": True,
            "reset": True,
            "abort": True,
        }

        message = self._message
        logging.debug('get_global_state %s %s %s' % (state_dict, cmd_state, message))
        return state_dict, cmd_state, message

    def re_emit_values(self):
        channel_attributes = \
            (
                ("State", "State", "_update_state"),
                #("Status", "Status", "_update_status"),
                ("Powered", "Powered", "_update_powered_state"),
                ("PathRunning", "PathRunning", "_update_running_state"),
                ("NumSampleOnDiff", "NumLoadedSample", "_update_loaded_sample"), 
                #("LidSampleOnDiff", "LidLoadedSample", "_update_loaded_sample"), 
                ("Barcode", "SampleBarcode", "_update_barcode"), 
                ("di_AllLidsClosed", "AllLidsClosed", "_update_global_state"), 
                ("Message", "Message", "_update_message"),
                ("LN2Regulating", "LN2RegulationDewar1", "_update_regulation_state"),
                ("Tool", "CurrentTool","_update_tool_state")
            )
        channel_attributes += tuple((("di_Lid%dOpen" % k, "lid%d_state" % k, "_update_lid%d_state" % k) for k in range(1, self.no_of_lids+1)))
        channel_attributes += tuple((("di_Cassette%dPresence" % k, "Basket%dState" % k, "_update_basket%d_state" % k) for k in range(1, self.no_of_baskets+1)))
    
        for channel_attribute in channel_attributes:
            if type(channel_attribute) == tuple:
                channel_name_in_device = channel_attribute[0]
                _channel_name = "_chn%s" % channel_attribute[1]
                channel_name = channel_attribute[1]
                _update_method_name = channel_attribute[2]
            else:
                channel_name_in_device = channel_attribute
                _channel_name = "_chn%s" % channel_attribute
                channel_name = channel_attribute
                _update_method_name = "_update_%s" % channel_attribute
                
            if "Basket" in _channel_name or "Cassette" in _channel_name:
                pass
            elif "status" in _channel_name.lower():
                pass
            elif "NumLoadedSample" in _channel_name:
                pass            
            else:
                channel_object = getattr(self, _channel_name)
                if channel_object is not None:
                    getattr(self, _update_method_name)(channel_object.get_value())
                else:
                    logging.info('connecting signal update from %s to %s did not work' % (_channel_name, _update_method_name))
                    
    def get_cmd_info(self):
        """ return information about existing commands for this object
           the information is organized as a list
           with each element contains
           [ cmd_name,  display_name, category ]
        """
        """ [cmd_id, cmd_display_name, nb_args, cmd_category, description ] """
        cmd_list = [
            [
                "Power",
                [
                    ["powerOn", "PowerOn", "Switch Power On"],
                    ["powerOff", "PowerOff", "Switch Power Off"],
                    ["regulon", "Regulation On", "Swich LN2 Regulation On"],
                ],
            ],
            [
                "Lid",
                [
                    ["openlid1", "Open Lid", "Open Lid"],
                    ["closelid1", "Close Lid", "Close Lid"],
                ],
            ],
            [
                "Actions",
                [
                    ["home", "Home", "Actions", "Home (trajectory)"],
                    ["dry", "Dry", "Actions", "Dry (trajectory)"],
                    ["soak", "Soak", "Actions", "Soak (trajectory)"],
                ],
            ],
            [
                "Recovery",
                [
                    [
                        "clear_memory",
                        "Clear Memory",
                        "Clear Info in Robot Memory "
                        " (includes info about sample on Diffr)",
                    ],
                    ["reset", "Reset Message", "Reset Cats State"],
                    ["back", "Back", "Reset Cats State"],
                    ["safe", "Safe", "Reset Cats State"],
                ],
            ],
            ["Abort", [["abort", "Abort", "Abort Execution of Command"]]],
        ]
        return cmd_list

    def _execute_server_task(self, method, *args):
        logging.debug("_execute_server_task method %s" % method)
        task_id = method(*args)
        ret = None
        # introduced wait because it takes some time before the attribute PathRunning is set
        # after launching a transfer
        # after setting refresh in the Tango DS to 0.1 s a wait of 1s is enough
        gevent.sleep(1.0)
        while str(self._chnPathRunning.get_value()).lower() == "true":
            gevent.sleep(0.1)
        ret = True
        return ret

    def send_command(self, cmd_name, args=None):

        #
        lid = 1
        toolcal = 0
        tool = self.get_current_tool()

        if cmd_name in ["dry", "safe", "home"]:
            if tool is not None:
                args = [tool]
            else:
                raise Exception("Cannot detect type of TOOL in Cats. Command ignored")

        if cmd_name == "soak":
            if tool in [TOOL_DOUBLE, TOOL_UNIPUCK]:
                args = [str(tool), str(lid)]
            else:
                raise Exception("Can SOAK only when UNIPUCK tool is mounted")

        if cmd_name == "back":
            if tool is not None:
                args = [tool, toolcal]
            else:
                raise Exception("Cannot detect type of TOOL in Cats. Command ignored")

        cmd = getattr(self.cats_device, cmd_name)

        try:
            if args is not None:
                if len(args) > 1:
                    ret = cmd(map(str, args))
                else:
                    ret = cmd(*args)
            else:
                ret = cmd()
            return ret
        except Exception as exc:
            import traceback

            traceback.print_exc()
            msg = exc[0].desc
            raise Exception(msg)

