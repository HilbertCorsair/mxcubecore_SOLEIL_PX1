#  Project: MXCuBE
#  https://github.com/mxcube.
#
#  This file is part of MXCuBE software.
#
#  MXCuBE is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  MXCuBE is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with MXCuBE.  If not, see <http://www.gnu.org/licenses/>.
"""
[Name] : PX1Collect(Method)

[Description] : Describes specific data collection methods for SOLEIL/PROXIMA-1

[Channels]
-------------------------------------------------------------------------------
| name                       | initiated at
-------------------------------------------------------------------------------
|                            |            
-------------------------------------------------------------------------------

[Commands] :
No command is called within this specific HWO

[Emited signals] :
No signal is emited within this specific HWO

[Properties]
-------------------------------------------------------------------------------
| name                   | reported value
-------------------------------------------------------------------------------
|                        | 
-------------------------------------------------------------------------------
[Hardware Objects]      
-------------------------------------------------------------------------------
| name                       | signals             | functions
|------------------------------------------------------------------------------
|                            |                     | 
|                            |                     | 
-------------------------------------------------------------------------------
"""

import os
import time
import logging
import gevent
import subprocess
import socket
from glob import glob
from typing import Union
from pathlib import Path

from PyTango import DeviceProxy

from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore.TaskUtils import * 
from mxcubecore.HardwareObjects.abstract.AbstractCollect import AbstractCollect
from CreateDirClient import CreateDirectoryClient

__author__ = "Vicente Rey Bakaikoa"
__credits__ = ["MXCuBE colaboration"]
__version__ = "2.3"

log = logging.getLogger("HWR")
user_log = logging.getLogger("user_level_log")

class PX1Collect(AbstractCollect, HardwareObject):
    """
    Descript. : Main data collection class. Inherited from AbstractMulticollect
                Collection is done by setting collection parameters and
                executing collect command
    """
    adxv_host = '127.0.0.1'
    adxv_port = 8100
    adxv_interval = 3 # minimum time (in seconds) between image refresh on adxv

    goimg_dir = "/data4/proxima1-soleil/.goimgpx1/"
    goimg_filename = "goimg.db"

# LEO
# TO DO: get this info from xml
    characterization_nb_merged_images = 10
##

    def __init__(self, name):
        """
        Descript. : __init__ method

                  :param name: name of the object

                  :type  name: string
        """
        AbstractCollect.__init__(self, name)
        HardwareObject.__init__(self, name)

        self._error_msg = ""
        self.owner = None
        self.osc_id = None
        self._collecting = None
        self.aborted_by_user = False

        self.diffractometer_hwobj = None
        self.omega_hwobj = None
        self.kappa_hwobj = None
        self.phi_hwobj = None
        self.lims_client_hwobj = None
        self.machine_info_hwobj = None
        self.energy_hwobj = None
        self.resolution_hwobj = None
        self.transmission_hwobj = None
        self.detector_hwobj = None
        self.beam_info_hwobj = None
        self.autoprocessing_hwobj = None
        self.graphics_manager_hwobj = None
        self.mxlocal = None
        self.chip_manager_hwobj = None

        self.session_hwobj = None

        self.latest_imgnum = 0
        self.latest_trignum = 0

        self.helical_positions = None
        self.omega_pos_before = None

        self.total_exposure_time = 0

        self.mxcube_createdir_server = None

    def init(self):
        """
        Init method
        """

        self.collect_devname = self.get_property("tangoname")
        self.collect_device = DeviceProxy(self.collect_devname)
        self.collect_device.set_timeout_millis(20000)
        self.collect_state_chan = self.get_channel_object("state")

        self.collect_imgnum_chan = self.get_channel_object("latest_image_num")
        self.collect_imgnum_chan.connect_signal('update',
                                              self.collect_imgnum_update)

        self.collect_trignum_chan = self.get_channel_object("latest_trigger_num")
        self.collect_trignum_chan.connect_signal('update',
                                              self.collect_trignum_update)

        self.set_header_cmd = self.get_command_object("set_header")

        self.px1env_hwobj = self.get_object_by_role("environment")
        self.session_hwobj = self.get_object_by_role("session")

        self.fastshut_hwobj = self.get_object_by_role("fastshut")
        self.frontend_hwobj = self.get_object_by_role("frontend")
        self.safshut_hwobj = self.get_object_by_role("safshut")

        self.lightarm_hwobj = self.get_object_by_role("lightarm")

        self.diffractometer_hwobj = self.get_object_by_role("diffractometer")
        self.mxlocal_object = self.get_object_by_role("beamline_configuration")

        self.omega_hwobj = self.get_object_by_role("omega")
        self.kappa_hwobj = self.get_object_by_role("kappa")
        self.phi_hwobj = self.get_object_by_role("phi")

        self.lims_client_hwobj = self.get_object_by_role("lims_client")
        self.machine_info_hwobj = self.get_object_by_role("machine_info")
        self.energy_hwobj = self.get_object_by_role("energy")
        self.resolution_hwobj = self.get_object_by_role("resolution")
        self.transmission_hwobj = self.get_object_by_role("transmission")
        import pdb
        pdb.set_trace()

        self.detector_hwobj = self.get_object_by_role("detector")


        self.beam_info_hwobj = self.get_object_by_role("beam_info")
        self.autoprocessing_hwobj = self.get_object_by_role("auto_processing")
        self.graphics_manager_hwobj = self.get_object_by_role("graphics_manager")
        self.flux_hwobj = self.get_object_by_role("flux")

        self.kappa_mot_hwobj = self.get_object_by_role("kappa")
        self.kappaphi_mot_hwobj = self.get_object_by_role("phi")

        self.chip_manager_hwobj = self.get_object_by_role("chip_manager")

        self.img2jpeg = self.get_property("imgtojpeg")

        self.do_merge_hdf5 = False
        self.do_merge_cbf = False

        merge_opts = self.get_property("merge", None)
        if merge_opts is not None:
            opts = [opt.strip() for opt in merge_opts.split(",")]
            if 'hdf5' in opts:
                self.do_merge_hdf5 = True
            if 'cbf' in opts:
                self.do_merge_cbf = True
        
        self.mergetool_hdf5 = self.get_property("mergehdf5", None)
        self.mergetool_cbf = self.get_property("mergecbf", None)

        self.chip_maximum_collect_angle = self.get_property("chip_collect_angle_max")
        createdir_server_addr = self.get_property('mxcube_createdir_server')

        self.createdir_client = CreateDirectoryClient(createdir_server_addr)

        undulators = self.get_undulators()

        self.exp_type_dict = {'Mesh': 'raster',
                              'Helical': 'Helical'}

        det_px, det_py = self.detector_hwobj.get_pixel_size()

        self.set_beamline_configuration(\
             synchrotron_name="SOLEIL",
             directory_prefix=self.get_property("directory_prefix"),
             default_exposure_time=self.detector_hwobj.get_default_exposure_time(),
             minimum_exposure_time=self.detector_hwobj.get_minimum_exposure_time(),
             detector_fileext=self.detector_hwobj.get_file_suffix(),
             detector_type=self.detector_hwobj.get_detector_type(),
             detector_manufacturer=self.detector_hwobj.get_manufacturer(),
             detector_model=self.detector_hwobj.get_model(),
             detector_px=det_px,
             detector_py=det_py,
             undulators=undulators,
             focusing_optic=self.get_property('focusing_optic'),
             monochromator_type=self.get_property('monochromator'),
             beam_divergence_vertical=self.beam_info_hwobj.get_beam_divergence_hor(),
             beam_divergence_horizontal=self.beam_info_hwobj.get_beam_divergence_ver(),
             polarisation=self.get_property('polarisation'),
             input_files_server=self.get_property("input_files_server"))

        self.emit("collectConnected", (True,))
        self.emit("collectReady", (True, ))

        #try:
        #   self.adxv_connect()
        #except Exception, err:
        #   self.adxv_socket = None
        #   logging.error("PX1Collect: ADXV1: msg= %s" % err)


    def data_collection_hook(self):
        """Main collection hook
        """
        collection_type = self.current_dc_parameters['experiment_type']
        logging.getLogger("HWR").info("PX1Collect: Running PX1 data collection hook. Type is %s" % collection_type )
        self.emit("collectStarted", (None, 1))

        user_info = self.session_hwobj.get_user_info()

        self.current_dc_parameters['user_info'] = user_info

        if self.check_aborted():
            return

        ready = self.prepare_devices_for_collection()

        if not ready:
            self.collection_failed("Cannot prepare collection")
            self.stop_collect()
            return

        try:
            if collection_type != 'Characterization':  # standard
                if self.diffractometer_hwobj.in_chip_mode(): 
                     if not self.chip_range_allowed():
                         self.collection_failed("Collection range too large for chips")
                         self.stop_collect()
                         return

                prepare_ok = self.prepare_standard_collection()

                if collection_type == "OSC":
                    self.set_helical(False)

                elif collection_type == "Helical":
                    self.set_helical(True)

            else:
                if self.diffractometer_hwobj.in_chip_mode(): 
                   self.collection_failed("Characterization not available in chip mode")
                   self.stop_collect()
                   return

                self.set_helical(False)
                prepare_ok = self.prepare_characterization()

            self._collecting = True

            osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
    
            logging.getLogger("HWR").info("  / position requested for chi is: %s " % osc_seq['kappaStart'])
            logging.getLogger("HWR").info("  / position requested for phi is: %s " % osc_seq['phiStart'])

            if "kappaStart" in osc_seq:
                self.move_kappa(osc_seq["kappaStart"])

            if "phiStart" in osc_seq:
                self.move_phi(osc_seq["phiStart"])
        except:
            log = logging.getLogger("user_level_log")
            log.warning("Error while preparing data collection. ABORTED. Check app log")
            self.collect_failed('prepare collect exception')
            return

        # for progressBar brick
        #self.emit("progressInit", "Collection", osc_seq['number_of_images'])

        #
        # Run
        #

        self.prepare_directories()

        if self.check_aborted(): return 
 
        self.latest_imgnum = 0
        self.latest_trignum = 0
        self.emit("progressInit", ("Data Collection", 100))

        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        nb_images = osc_seq['number_of_images']
        exp_time = osc_seq['exposure_time'] 

        max_wait_time = self.total_exposure_time + 60
        if max_wait_time < 180:
            max_wait_time = 180

        try:
            log = logging.getLogger("HWR") 
            if collection_type != 'Characterization':  # standard or helical
                self.start_standard_collection()
                log.debug("Waiting for collect to finish. max time waiting: %s" % max_wait_time)
                if not self.wait_collect_ready(timeout=max_wait_time):
                    log.debug("Timeout waiting for end of Collection")
                    raise BaseException("Timeout waiting for collection end")

                log.debug("Collect is now finished")

                thumblist = self.thumbnails_standard()
                self.generate_thumbnails(thumblist)
                #self.generate_thumbnails()
            else:
                # CHARACTERIZATION
                self.start_characterization()
                if not self.wait_collect_ready(timeout=max_wait_time):
                    log.debug("Timeout waiting for end of Collection")
                    raise BaseException("Timeout waiting for collection end")
                # 
                thumblist = self.thumbnails_characterization()
                self.generate_thumbnails(thumblist)
                self.trigger_auto_processing("characterization",self.current_dc_parameters,-1)

            # includes
            if collection_type == 'Characterization':
                template = self.current_dc_parameters['fileinfo']['template']
                directory = self.current_dc_parameters['fileinfo']['directory']
                number_of_images = self.current_dc_parameters['oscillation_sequence'][0]['number_of_images']

                user_log.info("Waiting for last image in disk...")
                log.info("Waiting for last image in disk...")
                log.debug("   directory: %s" % directory)

                if self.do_merge_cbf:
                    waiting_for_last_image = True
                    template_glob = "%s*merged_*.%s" % (template.split("%0")[0], "cbf")
                    showing_img = ""
                    start_wait = time.time()
                    timeout = 60
                    #while waiting_for_last_image:
                    #    img_list = sorted(glob(os.path.join(directory, template_glob)))
                    #    if len(img_list) < number_of_images:
                    #        log.debug("Characterization waiting for last CBF merged image... %s" % str(img_list))
                    #        gevent.sleep(0.1)
                    #    else:
                    #        self.adxv_sync_image(img_list[-1])
                    #        waiting_for_last_image = False                    
                    #    if (time.time() - start_wait) > timeout:
                    #        user_log.error(" timeout. giving up on waiting for last image")
                    #        waiting_for_last_image = False
                    #    if len(img_list):
                    #       _limg = img_list[-1]
                    #       if (_limg != showing_img):
                    #           showing_img = _limg
                    #           self.adxv_sync_image(showing_img)

                if self.do_merge_hdf5:
                    # do not wait merged image to finish collection
                    pass

                    # code to wait for last merged image on disk code (tested)
                    #  waiting_for_last_image = True
                    #  directory = self.current_dc_parameters['fileinfo']['directory']
                    #  last_image = "%s_sum10_data_000001.h5" % (template.split("%0")[0])
                    #  last_image = os.path.join(directory,last_image)
                    #  start_waiting = time.time()
                    #  while waiting_for_last_image:
                    #      log.debug("Characterization waiting for last HDF5 merged image... %s" % str(last_image))
                    #      if os.path.exists(last_image):
                    #          log.debug("Characterization last HDF5 merged image found on disk")
                    #          waiting_for_last_image = False
                    #  
                    #      if time.time() - start_waiting > 60:
                    #          log.debug("Timeout waiting for characterization HDF5 merged image. ")
                    #          waiting_for_last_image = False
                    #      time.sleep(0.2)

            self.data_collection_end()
            self.collection_finished()

        except:
            log.warning("Error during data collection. ABORTED. Check app log")
            self.collect_failed('collect exception')
            return

    def check_aborted(self):
        return self.aborted_by_user

    def collect_imgnum_update(self, imgnum):
        if imgnum is None: # cannot read value ignore it
            return

        if self._collecting:
            if imgnum != self.latest_imgnum:
                self.latest_imgnum = imgnum
                self.latest_trignum = self.collect_trignum_chan.get_value()
                self.collect_update_progress()

    def collect_trignum_update(self, trignum):
        if trignum is None: # cannot read value ignore it
            return

        if self._collecting:
            if trignum != self.latest_trignum:
                self.latest_imgnum = self.collect_imgnum_chan.get_value()
                self.latest_trignum = trignum
                self.collect_update_progress()


    def collect_update_progress(self):
            if self.current_dc_parameters['in_interleave']:
                number_of_images = \
                    self.current_dc_parameters['in_interleave'][1]
                step_num = int(float(self.latest_imgnum)) / number_of_images
            elif self.current_dc_parameters['experiment_type'] == "Characterization":
                number_of_images = self.characterization_total_images  
                img_per_trig = self.characterization_nb_merged_images

                step_num = (self.latest_trignum - 1) * img_per_trig + self.latest_imgnum
                logging.getLogger("HWR").debug("updating progress:  img_per_trig: %s / imgnum = %s / framenum = %s / img is %s out of %s" % (img_per_trig, self.latest_imgnum, self.latest_trignum, step_num, number_of_images))
            else:
                number_of_images = \
                    self.current_dc_parameters['oscillation_sequence'][0]['number_of_images']
                step_num = int(float(self.latest_imgnum)) 

            step_num = step_num * 100.0 / number_of_images
            self.emit("progressStep", step_num)
            self.emit("collectImageTaken", int(self.latest_trignum))

    def prepare_standard_collection(self):
        #PL 11/11/18
        _templ = self.current_dc_parameters['fileinfo']['template']
        if "%" in _templ:
             self.current_dc_parameters['fileinfo']['template'] = _templ.split("%")[0][:-1]
        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        fileinfo = self.current_dc_parameters['fileinfo']
        basedir = fileinfo['directory']

        logging.getLogger("HWR").info("PX1Collect: fileinfo is %s " % str(fileinfo))

        file_template = fileinfo['template']

        if self.diffractometer_hwobj.in_chip_mode():
            row, col = self.chip_manager_hwobj.get_current_location()
            file_template = 'chip_%s_%s_' % (row,col) + file_template

        self.current_file_template = file_template

        imgname = file_template #% osc_seq['start_image_number']

        fileinfo['imageSuffix'] = self.detector_hwobj.get_file_suffix()

        # move omega to start angle
        start_angle = osc_seq['start']

        nb_images = osc_seq['number_of_images']
        osc_range = osc_seq['range']
        exp_time = osc_seq['exposure_time'] 

        self.total_exposure_time = nb_images * exp_time

        logging.getLogger("HWR").info("PX1Collect:  nb_images: %s / osc_range: %s / exp_time: %s" % \
                  (nb_images, osc_range, exp_time))

        self.collect_device.characterisation = False
        self.collect_device.mesh = False

        collection_type = self.current_dc_parameters['experiment_type']
        # self.collect_device.helicalScan = False

        self.collect_device.exposurePeriod = exp_time
        self.collect_device.nimages = nb_images
        nb_trigger = 1
        self.collect_device.nTrigger = nb_trigger

        self.collect_device.imageWidth = osc_range

        # self.collect_device.collectAxis = "Omega"

        logging.getLogger("HWR").debug("Programming collect_device with start angle = %s" % start_angle) 
        self.collect_device.startAngle = start_angle
# LEO : CHANGE IN CASE OF 700Hx TO MODE 3 IF DECTRIS CANNOT FIX ISSUE OF TRIGGERING
        self.collect_device.triggerMode = 2
  
        self.collect_device.imagePath = basedir
        #self.collect_device.imageName = imgname
        self.collect_device.imageName = os.path.splitext(imgname)[0]   # for EIGER do not include file prefix
# [LEO] here should be the information on the detector roi mode once clickable in the gui
#        self.collect_device.roiMode = 'disabled'
#
        gevent.sleep(0.1)
        self.detector_hwobj.wait_energy_calibration()
        self.prepare_headers()
        self.collect_device.PrepareCollect()
        ret = self.wait_collect_standby() 
        if ret is False:
            logging.getLogger("user_level_log").info("Collect server prepare error. Aborted") 
            return False
        return True

    def start_standard_collection(self):
        self.emit("collectStarted", (self.owner, 1))
        self.detector_hwobj.start_collection()
        self.collect_device.Start()

    def start_helical_collection(self):
        #if not self.collect_device.helicalScan:
        #    self.collect_device.helicalScan = True
        #self.emit("collectStarted", (self.owner, 1))
        #self.detector_hwobj.start_collection()
        #self.collect_device.Start()
        pass

    def chip_range_allowed(self):
        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        start_angle = osc_seq['start']

        nb_images = osc_seq['number_of_images']
        osc_range = osc_seq['range']

        max_angle = start_angle + nb_images * osc_range

        if max_angle > self.chip_maximum_collect_angle:
            return False
        else:
            return True

    def thumbnails_standard(self):
        imgs_per_thumb = 10

        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]

        nb_images = osc_seq['number_of_images']

        first_imgno = osc_seq['start_image_number']
        last_imgno = first_imgno + nb_images - 1 

        first_thumb = [first_imgno, imgs_per_thumb]
        last_thumb = [last_imgno-imgs_per_thumb+1, imgs_per_thumb]

        return [first_thumb, last_thumb]

    def thumbnails_characterization(self):
        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        merged_images = self.characterization_nb_merged_images
        nb_trigger = osc_seq['number_of_images']

        thumblist = []
        for trigno in range(nb_trigger):
            first_img_no = trigno * merged_images 
            thumb = [first_img_no, merged_images]
            thumblist.append(thumb)

        return thumblist

    def generate_thumbnails(self, thumblist):
    #o-- def generate_thumbnails(self):
        thumbs = {}

        template = self.current_file_template

        fileinfo = self.current_dc_parameters['fileinfo']
        archive_dir = fileinfo['archive_directory']

        log.info("generating ARCHIVE dir: %s" % archive_dir)
        self.create_directories(archive_dir)

        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        first_imgno = osc_seq['start_image_number']
        nb_images = osc_seq['number_of_images']
        last_imgno = first_imgno + nb_images - 1 

        h5_root = template 
        h5_master = "%s_master.h5" % h5_root 
        h5_masterpath = os.path.join( fileinfo['directory'], h5_master )

        thumbs['hdf5_master'] = h5_masterpath

        thumbno = 0
        for thumb_pars in thumblist:
            thumb_name = 'thumb%02d' % thumbno
            thumb_info = self.do_store_image_in_lims(thumb_pars)
            thumbs[thumb_name] = thumb_info
            thumbno += 1
   
        #-- remove this
        #o-- log.info("Storing image %s in lims\n", first_imgno)
        #o-- info = self.do_store_image_in_lims(first_imgno)
        #o-- thumbs['image0'] = info
        #o-- 
        #o-- log.info("Storing image %s in lims\n", last_imgno)
        #o-- info = self.do_store_image_in_lims(last_imgno)
        #o-- thumbs['image1'] = info
        #-- until here

        self.current_dc_parameters['thumbnails'] = thumbs

    def do_store_image_in_lims(self, thumb_info):
        imgno, nb_images = thumb_info
    #def do_store_image_in_lims(self, imgno):
        log.info("Storing image %s in lims\n", imgno)
        image_id, img_info = self.store_image_in_lims(imgno)
        info = {'image_id': image_id,
                'image_no': imgno, 
                'nb_images': nb_images, 
                'thumb_path': img_info['jpegThumbnailFileOrigPath'], 
                'jpeg_path': img_info['jpegFileOrigPath'], 
                'thumb_ispyb': img_info['jpegThumbnailFileFullPath'], 
                'jpeg_ispyb': img_info['jpegFileFullPath'], 
                }
        return info
      
    def store_first_last_in_lims(self):
        # NOT USED
        # creates archive directory
        # saves first and last image info in LIMS (ispyb) 
        #    thumbnails are later created in the path saved via a external process

        fileinfo = self.current_dc_parameters['fileinfo']
        archive_dir = fileinfo['archive_directory']
        log.info("generating ARCHIVE dir: %s" % archive_dir)
        self.create_directories(archive_dir)

        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        first_imgno = osc_seq['start_image_number']
        nb_images = osc_seq['number_of_images']
        nb_frames_file = self.collect_device.fileNbFrames

        last_imgno = first_imgno + nb_images - 1 
        if nb_images > nb_frames_file:
            # image no of first frame in last datafile
            datafile_number = abs((nb_images - 1)/ nb_frames_file)
            last_imgno = datafile_number * nb_frames_file + first_imgno  

        self.generate_thumbnails()
        
    def generate_eiger_thumbnails(self):
        # NOT USED
        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        fileinfo = self.current_dc_parameters['fileinfo']
        basedir = fileinfo['directory']
        archive_dir = fileinfo['archive_directory']
        log.info("generating ARCHIVE dir: %s" % archive_dir)
        self.create_directories(archive_dir)

        template = self.current_file_template

        jpeg_template = os.path.splitext(template)[0] + ".jpeg"
        thumb_template = os.path.splitext(template)[0] + ".thumb.jpeg"

        osc_range = osc_seq['range']
        osc_start = osc_seq['start']
        nb_images = osc_seq['number_of_images']

        log = logging.getLogger("HWR")
        log.debug("generating thumbnails for Eiger. TO DO")

        first_imgno = osc_seq['start_image_number']
        first_image_fullpath = os.path.join(basedir,template) #% first_imgno)
        first_image_jpegpath = os.path.join(archive_dir, jpeg_template) # % first_imgno)
        first_image_thumbpath = os.path.join(archive_dir, thumb_template) # % first_imgno)

        datafile_number = 1
        h5_root = template #os.path.splitext(first_image_fullpath)[0]
        h5_master = "%s_master.h5" % h5_root 
        h5_masterpath = os.path.join( fileinfo['directory'], h5_master )

        first_h5_data = "%s_data_%06d.h5" % (h5_root, datafile_number)
        first_h5_datapath = os.path.join( fileinfo['directory'], first_h5_data )

        #thumbs_up = self.generate_thumbnails(first_h5_datapath, first_image_jpegpath, first_image_thumbpath)
        #if thumbs_up:
        self.store_image_in_lims(first_imgno)

        nb_frames_file = self.collect_device.fileNbFrames
        if nb_images > nb_frames_file:
            last_imgno = first_imgno + nb_images - 1 
            datafile_number = abs((nb_images - 1)/ nb_frames_file)
            last_h5_data = "%s_data_%06d.h5" % (h5_root, datafile_number)
            last_h5_datapath = os.path.join( fileinfo['directory'], last_h5_data )

            # image no of first frame in last datafile
            imgno = datafile_number * nb_frames_file + first_imgno  
            last_image_jpegpath = os.path.join(archive_dir, jpeg_template) # % imgno)
            last_image_thumbpath = os.path.join(archive_dir, thumb_template) # % imgno)
            #thumbs_up = self.generate_thumbnails(last_h5_datapath, last_image_jpegpath, last_image_thumbpath)
            #if thumbs_up:
            self.store_image_in_lims(imgno)

    def prepare_characterization(self):
        _templ = self.current_dc_parameters['fileinfo']['template']
        if "%" in _templ:
             self.current_dc_parameters['fileinfo']['template'] = _templ.split("%")[0][:-1]

        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        fileinfo = self.current_dc_parameters['fileinfo']

        basedir = fileinfo['directory']

        fileinfo['imageSuffix'] = self.detector_hwobj.get_file_suffix()
# LEO : modification of start angle as it stands because of bug in Collect device
        start_angle = osc_seq['start']
        start_angle = 0
##
        file_template = fileinfo['template']
        self.current_file_template = file_template
        imgname = file_template

        merged_images = self.characterization_nb_merged_images
        exp_time = float(osc_seq['exposure_time'])/ merged_images
        osc_range = float(osc_seq['range'])/ merged_images
        nb_trigger = osc_seq['number_of_images']

        self.total_exposure_time = nb_trigger * merged_images * exp_time

        self.collect_device.nimages = int(merged_images)
        self.collect_device.exposurePeriod = exp_time
        self.collect_device.imagePath = basedir
        self.collect_device.imageWidth = osc_range
        self.collect_device.roiMode = 'disabled'
        self.collect_device.nTrigger = nb_trigger
        self.characterization_total_images = nb_trigger * int(merged_images)

        self.collect_device.startAngle = start_angle
        self.collect_device.triggerMode = 2
        self.collect_device.imageName = os.path.splitext(imgname)[0]

        self.collect_device.characterisation = True
        self.collect_device.mesh = False
        self.collect_device.helicalScan = False

        gevent.sleep(0.1)
        self.detector_hwobj.wait_energy_calibration()
        self.prepare_headers()
        self.detector_hwobj.set_image_headers(["Angle_increment %.4f" % osc_range])

        self.collect_device.prepareCollect()

        ret = self.wait_collect_standby()
        if ret is False:
            logging.getLogger("user_level_log").info("Collect server prepare error. Aborted")
            return False

        return True

    def start_characterization(self):
        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]
        fileinfo = self.current_dc_parameters['fileinfo']

        image_template = fileinfo['template']
        nb_trigger = osc_seq['number_of_images']

        self.emit("collectStarted", (self.owner, 1))
        self.emit("progressInit", ("Characterization", 100))
        self.detector_hwobj.start_collection()
        self.collect_device.start()

        h5_root = os.path.splitext(image_template)[0]
        h5_master = "%s_master.h5" % h5_root
        h5_masterpath = os.path.join(fileinfo['directory'], h5_master)
        h5_datapath = os.path.join(fileinfo['directory'], h5_root)
        start_angle = osc_seq['start']

        # merge cbf
        if self.do_merge_cbf:
            log.debug("PX1Collect - Merging CBF")
            for trig_number in range(nb_trigger):
                h5_data = "%s_data_00000%s.h5" % (h5_root, trig_number + 1)
                h5_data_file = os.path.join(fileinfo['directory'], h5_data)
                self.merge_eiger_cbf(h5_masterpath, h5_data_file, start_angle)
                start_angle += 90

        if self.do_merge_hdf5:
            log.debug("PX1Collect - Merging HDF5")
            h5_data = "%s_data_00000%s.h5" % (h5_root, nb_trigger)
            h5_data_file = os.path.join(fileinfo['directory'], h5_data)
            self.merge_eiger_hdf5(h5_masterpath, h5_data_file)

    def merge_eiger_cbf(self, masterfile, datafile, start): 
        logging.info(" MERGE Images for characterization to be done: %s %s %s" % (masterfile, datafile, start))
        gevent.spawn(self._merge_eiger_cbf, masterfile, datafile, start)
        logging.info(" MERGE characterization submitted")
        return

    def _merge_eiger_cbf(self, masterfile, datafile, angle_start):
        log.info("PX1Collect:   merge_eiger cbf. waiting to finish image series for angle %s ",angle_start)
        log.info("     - waiting for file %s" % datafile)

        if self.wait_image_on_disk(datafile,timeout=60):
            log.info("     - file found starting merge tool")
            gevent.sleep(2) 
            subprocess.Popen([self.mergetool_cbf,datafile])
        else:
            log.info("     - file not found. merge aborted")
           
    def merge_eiger_hdf5(self, masterfile, datafile): 
        logging.info(" MERGE Images for characterization to be done: %s %s" % (masterfile, datafile))
        gevent.spawn(self._merge_eiger_hdf5, masterfile, datafile)
        logging.info(" MERGE characterization submitted")
        return

    def _merge_eiger_hdf5(self, masterfile, datafile):
        log.info("PX1Collect:   merge_eiger hdf5. waiting for latest file on disk. file %s ",datafile)
        if self.wait_image_on_disk(datafile,timeout=60):
            log.info("   %s %s" % (self.mergetool_hdf5,masterfile))
            gevent.sleep(2)      
            log.info("PX1Collect:  starting mergetool") 
            subprocess.Popen([self.mergetool_hdf5,masterfile])
        else:
            log.info("     - file not found. merge aborted")

    def stop_data_collection(self):
        self.aborted_by_user = True
        logging.getLogger("HWR").info("PX1Collect: stopping data collection ")
        try:
            self.collect_device.Stop()
        except BaseException as e:
            logging.getLogger("HWR").info("PX1Collect: collect server cannot be stopped" + str(e))
            
        try:
            self.fastshut_hwobj.closeShutter()
        except BaseException as e:
            logging.getLogger("HWR").info("PX1Collect: fast shutter cannot be closed" + str(e))

    def data_collection_end(self):
        #
        # data collection end (or abort)
        #
        logging.getLogger("HWR").info("PX1Collect: finishing data collection ")
        # return omega to initial position
        if self.omega_pos_before:
            self.omega_hwobj.syncMove(self.omega_pos_before)
        self.fastshut_hwobj.closeShutter()

        # self.graphics_manager_hwobj.select_camera('oav')

        self.emit("progressStop")

    def data_collection_failed(self):
        logging.getLogger("HWR").info("PX1Collect: Data collection failed. recovering sequence should go here")

    def collect_finished(self, green):
        logging.info("PX1Collect: Data collection finished")

    def collect_failed(self, failed_msg=None):
        logging.exception("PX1Collect: Data collection failed")

        try:
            self.detector_hwobj.stop_collection()
            self.collect_device.Stop()
            self.omega_hwobj.stop()  
            self.data_collection_end()
        except BaseException as e :
            logging.getLogger("HWR").info("PX1Collect: collect failed.Error while stopping devices" + str(e))

        self.collection_failed(failed_msg)

    def set_helical_pos(self, arg):
        """
        Descript. : 6 floats describe
        p1X, p1Y, p1Z
        p2X, p2Y, p2Z
        """
        logging.info("PX1Collect: set_helical_pos0")
        try:
            XYZ_start = arg["1"]["phiy"], arg["1"]["phiz"], arg["1"]["sampx"]
            XYZ_end   = arg["2"]["phiy"], arg["2"]["phiz"], arg["2"]["sampx"]
            self.collect_device.helicalStart = map(float, XYZ_start)
            self.collect_device.helicalEnd =   map(float, XYZ_end)
            #self.helical_positions = XYZ_start + XYZ_end
            logging.info("PX1Collect: set_helical_pos done.")
        except Exception as err:
            logging.info("PX1Collect Helical_Pos: ERR = %s" % err)

    def set_helical(self, arg):
        logging.info("PX1Collect: set helical %s" % (arg))
        if self.collect_device.helicalScan != arg:
                self.collect_device.helicalScan = arg

    def set_mesh_scan_parameters(self, num_lines, num_images_per_line, mesh_range):
        """
        Descript. :
        """
        pass

    def trigger_auto_processing(self, process_event, collect_pars, frame_number):

        collection_type = collect_pars['experiment_type']

        if collection_type == 'Helical':
            log.info("Triggering auto-processing for Helical collection")

        if self.diffractometer_hwobj.in_chip_mode():
            return

        runit = False
        if self.autoprocessing_hwobj is None: 
            log.info("No autoprocessing hwobj")
            return

        if self.run_processing_after:
            runit = True

        if process_event == "characterization":
            # always start it if characterization
            collect_pars['auto_processing'] = False
            runit = True
        else:
            collect_pars['auto_processing'] = runit

        #if True:  # run it always. relay on 'auto_processing' to decide if really needs to be done
                   # or only file transfer and thumbnail generation
        if runit:
            try:
                log.debug("Launching autoprocessing")
                self.autoprocessing_hwobj.start_autoprocessing(collect_pars)
                log.debug("Done.")
            except:
                import traceback
                logging.getLogger("HWR").debug(" Something went wrong with autoprocessing") 
                logging.getLogger("HWR").debug( traceback.format_exc() )

    ## generate snapshots and data thumbnails ##
    @task
    def _take_crystal_snapshot(self, filename):
        """
        Descript. :
        """
        if not self.is_sampleview_phase():
            #self.go_to_sampleview()
            #time.sleep(2) # allow time to refresh display after
            logging.getLogger("HWR").debug("PX1Collect:  - not taking snapshot: LIGHTARM not DOWN.")
            logging.getLogger("HWR").debug("PX1Collect:  - PX1Env state is:  %s" % self.px1env_hwobj.get_state())
            self.current_dc_parameters["take_snapshots"] = 0
            return
        self.lightarm_hwobj.adjustLightLevel() 
        gevent.sleep(0.3) # allow time to refresh display after

        self.graphics_manager_hwobj.save_scene_snapshot(filename)
        filename_noshape = filename.replace("snapshot.jpeg", "snapshot_noshape.jpeg")
        self.graphics_manager_hwobj.save_scene_snapshot(filename_noshape, include_items=False)
        logging.getLogger("HWR").debug("PX1Collect:  - snapshot saved to %s" % filename)

    def generate_thumbnails_old(self, filename, jpeg_filename, thumbnail_filename):
        # 
        # write info on LIMS

        try:
            logging.info("PX1Collect: Generating thumbnails for %s" % filename)
            logging.info("PX1Collect:       jpeg file: %s" % jpeg_filename)
            logging.info("PX1Collect:  thumbnail file: %s" % thumbnail_filename)

            self.wait_image_on_disk(filename)
            if os.path.exists( filename ):
                subprocess.Popen([ self.img2jpeg, filename, jpeg_filename, '0.3' ])
                subprocess.Popen([ self.img2jpeg, filename, thumbnail_filename, '0.06' ])
                # IF USING "eiger_thumbnail" uncomment this
                # LEO MODIF!!!
                subprocess.Popen([ self.img2jpeg, filename, "-o " + jpeg_filename, '-b 3' ])
                subprocess.Popen([ self.img2jpeg, filename, "-o " + thumbnail_filename, '-b 10' ])
                return True
            else:
                logging.info("PX1Collect: Oopps.  Trying to generate thumbs but  image is not on disk")
                return False
        except:
            import traceback
            logging.error("PX1Collect: Cannot generate thumbnails for %s" % filename)
            logging.error(traceback.format_exc())
            return False

    ## generate snapshots and data thumbnails (END) ##

    def store_image_in_lims(self, frame_number, motor_position_id=None):
        """
        Descript. :
        """
        if self.lims_client_hwobj and not self.current_dc_parameters['in_interleave']:
            file_location = self.current_dc_parameters["fileinfo"]["directory"]
            image_file_template = self.current_dc_parameters['fileinfo']['template']
            #o-- filename = image_file_template #% frame_number
            filename = image_file_template + "_%04d" % frame_number
            lims_image = {'dataCollectionId': self.current_dc_parameters.get("collection_id"),
                          'fileName': filename,
                          'fileLocation': file_location,
                          'imageNumber': frame_number,
                          'measuredIntensity': self.get_measured_intensity(),
                          'synchrotronCurrent': self.get_machine_current(),
                          'machineMessage': self.get_machine_message(),
                          'temperature': self.get_cryo_temperature()}
            archive_directory = self.current_dc_parameters['fileinfo']['archive_directory']

            if archive_directory:
                jpeg_filename = "%s.jpeg" % os.path.splitext(filename)[0]
                thumb_filename = "%s.thumb.jpeg" % os.path.splitext(filename)[0]
                jpeg_file_template = os.path.join(archive_directory, jpeg_filename)
                jpeg_thumbnail_file_template = os.path.join(archive_directory, thumb_filename)
                jpeg_full_path = jpeg_file_template #% frame_number
                jpeg_thumbnail_full_path = jpeg_thumbnail_file_template #% frame_number
                lims_image['jpegFileFullPath'] = jpeg_full_path
                lims_image['jpegThumbnailFileFullPath'] = jpeg_thumbnail_full_path

            if motor_position_id:
                lims_image['motorPositionId'] = motor_position_id

            image_id = self.lims_client_hwobj.store_image(lims_image) 

            # saving changed the FullPath to the ispyb path (ruche). keep original
            lims_image['jpegFileOrigPath'] = jpeg_full_path
            lims_image['jpegThumbnailFileOrigPath'] = jpeg_thumbnail_full_path
            return image_id, lims_image


    ## FILE SYSTEM ##
    def wait_image_on_disk(self, filename, timeout=40.0):
        start_wait = time.time()
        logging.info("PX1Collect: Waiting for image %s" % filename) 
        while not os.path.exists(filename):
            if (time.time() - start_wait) > timeout:
               logging.info("PX1Collect: Giving up waiting for image. Timeout")
               self.collection_failed("Giving up waiting for image on disk. Timeout")
               self.stop_collect()
               return 0
            gevent.sleep(0.1)
        logging.info("PX1Collect: Waiting for image %s ended in  %3.2f secs" % (filename, time.time()-start_wait))
        return 1

    def check_directory(self, basedir):
        if not os.path.exists(basedir):
            logging.getLogger("HWR").info(" Creating directory - %s" % basedir)
            try:
                os.makedirs(basedir)
            except OSError as e:
               print (e)
        else:
            logging.getLogger("HWR").info(" Directory - %s - already exists" % basedir)

    def prepare_directories(self) -> None:
        """
        Prepare directories for data collection by setting up processing directory
        and creating necessary files.
        
        The function:
        1. Gets the base directory from current DC parameters
        2. Creates a processing directory
        3. Sets appropriate permissions
        4. Creates a goimg file in the processing directory
        """
        try:
            fileinfo = self.current_dc_parameters['fileinfo']
            basedir = fileinfo['directory']
            
            # Using pathlib for more robust path handling
            process_dir = Path(basedir.replace('RAW_DATA', 'PROCESSED_DATA'))
            
            # Create the directory if it doesn't exist
            process_dir.mkdir(parents=True, exist_ok=True)
            
            # Set permissions (0o777 is the octal notation in Python 3)
            process_dir.chmod(0o777)
            
        except Exception as e:
            logger = logging.getLogger("HWR")
            logger.error("PX1Collect: Error preparing processing directory")
            logger.error(f"Error details: {str(e)}", exc_info=True)
        
        try:
            # Continue with creating goimg file even if permission setting failed
            self.create_goimg_file(str(process_dir))
        except Exception as e:
            logger = logging.getLogger("HWR")
            logger.error("PX1Collect: Error creating goimg file")
            logger.error(f"Error details: {str(e)}", exc_info=True)

    def create_goimg_file(self, dirname: Union[str, Path]) -> None:
        """
        Create a goimg file with the specified directory name and set permissions.
        
        Args:
            dirname (Union[str, Path]): The directory name to write to the goimg file
            
        Raises:
            OSError: If there are issues with file operations
            PermissionError: If there are permission issues
        """
        try:
            # Convert paths to Path objects for better handling
            goimg_path = Path(self.goimg_dir) / self.goimg_filename
            
            # Delete the file if it exists
            goimg_path.unlink(missing_ok=True)
            
            # Write the directory name to the file
            goimg_path.write_text(str(dirname), encoding='utf-8')
            
            # Set permissions using octal notation (0o777 in Python 3)
            goimg_path.chmod(0o777)
            
        except Exception as e:
            # Log the error (assuming you have a logger set up)
            logger = logging.getLogger("HWR")
            logger.error(f"Error creating goimg file at {goimg_path}")
            logger.error(f"Error details: {str(e)}", exc_info=True)
            raise  # Re-raise the exception after logging

    def create_file_directories(self):
        """
        Method create directories for raw files and processing files.
        Directorie for xds.input and auto_processing are created
        """
        raw_dir = self.current_dc_parameters['fileinfo']['directory']
        process_dir = self.current_dc_parameters['fileinfo']['process_directory']

        proj_dir = self.get_base_project_directory(raw_dir)

        # try creating the directory via the mxcube_createdir 
        ok = self.prepare_base_project_directory(proj_dir)
        if not ok:
            self.collection_failed("Cannot create directories. Check permissions")
            self.stop_collect()
            return

        try:
            self.create_directories(raw_dir, process_dir,
                 self.current_dc_parameters['fileinfo']['archive_directory'])
        except OSError:
            self.collection_failed("Cannot create directories. Check permissions")
            self.stop_collect()
            return

        """create processing directories and img links"""
        xds_directory, auto_directory = self.prepare_input_files()
        try:
            #self.create_directories(xds_directory, auto_directory)
            #os.system("chmod -R 777 %s %s" % (xds_directory, auto_directory))
            try:
                os.symlink(raw_dir, os.path.join(process_dir, "imglink"))
            except: # os.error, e:
                #if e.errno != errno.EEXIST:
                #    raise
                pass
            #os.symlink(files_directory, os.path.join(process_directory, "img"))
        except:
            logging.exception("PX1Collect: Could not create processing file directory")
            return

        if xds_directory:
            self.current_dc_parameters["xds_dir"] = xds_directory

        if auto_directory:
            self.current_dc_parameters["auto_dir"] = auto_directory

    def get_base_project_directory(self, dirname):
        """ 
        extract project base from dirname
           keep the first '5' path components for example:

           from:
             /data4/proxima1-soleil/2019_Run3/2019-05-30/20170814/RAW_DATA/AR
           extract: 
             /data4/proxima1-soleil/2019_Run3/2019-05-30/20170814
        """ 
        path_c = dirname.split(os.sep)
        if len(path_c) >= 6:
            proj_dir = os.sep.join(path_c[:6]) 
        else:
            proj_dir = dirname
        return proj_dir

    def prepare_base_project_directory(self, project_dir):
        ret, msg = self.createdir_client.create(project_dir)

        if ret:
            log.debug("     - base project directory created.")
        else:
            log.debug("     - base project directory error: %s" % msg)

        return ret,msg

        #from xmlrpclib import ServerProxy
#
        #logging.getLogger("HWR").debug("PX1Collect.py / Creating base project directory: %s" % \
                   ##project_dir)
        #username = os.getenv('USER')
#
        #try:
            #serv = ServerProxy(self.mxcube_createdir_server)
            #ret, msg = serv.mxcube_createdir(project_dir, username)
            #if ret.lower() == 'error':
                #logging.getLogger("HWR").debug("     - base project directory error. %s" % msg)
                #return False, msg
            #else:
                #logging.getLogger("HWR").debug("     - base project directory created.")
                #return True
        #except BaseException, e:
            #logging.getLogger("HWR").debug("     - base project directory excep. %s" % str(e))
            #return False, "error talking with mxcube_createdir_server: " + str(e)
         
    def prepare_input_files(self):
        """
        Descript. :
        """
        i = 1
        log = logging.getLogger("user_level_log")
        while True:
            xds_input_file_dirname = "xds_%s_%s_%d" % (\
                self.current_dc_parameters['fileinfo']['prefix'],
                self.current_dc_parameters['fileinfo']['run_number'],
                i)
            xds_directory = os.path.join(\
                self.current_dc_parameters['fileinfo']['process_directory'],
                xds_input_file_dirname)
            if not os.path.exists(xds_directory):
                break
            i += 1
        self.current_dc_parameters["xds_dir"] = xds_directory

        mosflm_input_file_dirname = "mosflm_%s_run%s_%d" % (\
                self.current_dc_parameters['fileinfo']['prefix'],
                self.current_dc_parameters['fileinfo']['run_number'],
                i)
        mosflm_directory = os.path.join(\
                self.current_dc_parameters['fileinfo']['process_directory'],
                mosflm_input_file_dirname)

        log.info("  - xds: %s / mosflm: %s" % (xds_directory, mosflm_directory))
        return xds_directory, mosflm_directory

    ## FILE SYSTEM (END) ##

    def prepare_devices_for_collection(self):

        fileinfo = self.current_dc_parameters['fileinfo']
        basedir = fileinfo['directory']

        logging.getLogger("HWR").info(" Checking directory - %s" % basedir)
        self.check_directory(basedir)

        # save initial omega position
        self.omega_pos_before = self.omega_hwobj.get_position()
        logging.getLogger("HWR").info(" Saving initial omega position to be %s" % self.omega_pos_before)

        # check fast shutter closed. others opened
        shutok = self.check_shutters()

        if self.get_property("ignore_shutters") is True:
            shutok = True

        if not shutok:
            logging.getLogger("user_level_log").warning(" Shutters not ready for collection. Aborted")
            return False

        logging.getLogger("HWR").info(" SHutters are now ready s" )

        detok = self.detector_hwobj.prepare_collection(self.current_dc_parameters)
        if not detok:
            logging.getLogger("user_level_log").warning("Cannot prepare detector for collection. Aborted")
            return False

        #adxv_ok = self.adxv_connect()

        logging.getLogger("HWR").info(" SHutters are now ready s" )
        logging.getLogger("HWR").info(" Preparing diffractometer" )
        diff_ok = self.diffractometer_prepare_collection()
        if not diff_ok:
            logging.getLogger("user_level_log").info("Cannot prepare diffractometer for collection. Aborted")
            return False
        logging.getLogger("HWR").info(" Preparing diffractometer DONE" )

        self.energy_hwobj.wait_energy_ready()
        self.do_set_resolution()

        logging.getLogger("HWR").info(" All  devices ready for collection  " )
        return True

    def diffractometer_prepare_collection(self):
        self.diffractometer_hwobj.wait_device_ready(timeout=10)

        # go to collect phase
        if not self.is_collect_phase():
            logging.getLogger("HWR").info(" Moving diffractometer to collect phase " )
            success = self.go_to_collect()
            if not success:
                logging.getLogger("HWR").info("PX1Collect: Cannot set COLLECT phase")
                return False

        # self.graphics_manager_hwobj.select_camera('mount')

        return True

    def prepare_headers(self):

        logging.getLogger("HWR").info("PX1Collect: prepare_headers")
        osc_seq = self.current_dc_parameters['oscillation_sequence'][0]

        ax, ay, bx, by = self.get_beam_configuration()

        dist   = self.resolution_hwobj.get_distance()
        wavlen = self.energy_hwobj.get_wavelength()
        logging.getLogger("HWR").info("PX1Collect: prepare_headers. det distance is %s" % dist)
        start_angle = osc_seq['start']
        nb_images = osc_seq['number_of_images']
        collection_type = self.current_dc_parameters['experiment_type']
        if collection_type != 'Characterization':
            img_range = osc_seq['range']
        else:
            img_range = float(osc_seq['range']) / self.characterization_nb_merged_images
        exp_time = osc_seq['exposure_time']

        kappa_angle = self.kappa_hwobj.get_position()

        if self.detector_hwobj.get_detector_type() == 'pilatus':
            _settings = [
                ["Wavelength %.5f", wavlen],
                ["Detector_distance %.4f", dist/1000.],
                ["Beam_x %.2f", ax*dist + bx],
                ["Beam_y %.2f", ay*dist + by],
                ["Alpha %.2f", 49.64],
                ["Start_angle %.4f", start_angle],
                ["Angle_increment %.4f", img_range],
                ["Oscillation_axis %s", "Omega"],
                ["Detector_2theta %.4f", 0.0],
                ["Polarization %.3f", 0.990],
                ["Kappa %.4f", kappa_angle],
                ["Phi %.4f", self.phi_hwobj.get_position()],
                ["Chi %.4f", start_angle],
                ]

            self.detector_hwobj.set_image_headers(_settings)

        elif self.detector_hwobj.get_detector_type() == 'Eiger_X':

            chi_start = osc_seq['kappaStart']
            phi_start = osc_seq['phiStart']

            _settings = [
                str(start_angle),
                str(img_range),
                str(dist/1000),
                str(wavlen),
                str(ax*dist + bx),
                str(ay*dist + by),
                str(chi_start),
                str(phi_start),
                ]

            self.set_image_headers(_settings)
            self.wait_not_disabled()

    def check_shutters(self):
        # Check safety shutter
        if self.check_shutter_opened(self.safshut_hwobj, "Safety shutter") and \
           self.check_shutter_opened(self.frontend_hwobj, "Front end"):
              return True
        else:
              return False

    def check_shutter_opened(self, shut_hwo, shutter_name="shutter"):
        if shut_hwo.isShutterOpened():
            return True

        if shut_hwo.get_state() == 'disabled':
            logging.getLogger("user_level_log").warning("%s disabled. NO BEAM" % shutter_name)
            return False 

        elif shut_hwo.get_state() in ['fault', 'alarm', 'error']:
            logging.getLogger("user_level_log").warning("%s is in fault state. NO BEAM" % shutter_name)
            return False
        #elif shut_hwo.isShutterClosed():
        #    shut_hwo.openShutter()
        #    return shut_hwo.waitShutter('opened')
        else: 
            logging.getLogger("user_level_log").warning("%s is in an unhandled state. BEAM delivery uncertain" % shutter_name)
            return False

    def close_fast_shutter(self):
        self.fastshut_hwobj.closeShutter()

    def close_safety_shutter(self):
        pass

    ## COLLECT SERVER STATE ##
    def wait_collect_standby(self, timeout=10):
        t0 = time.time()
        while not self.is_standby():
            elapsed = time.time() - t0
            if elapsed > timeout:
                return False
            gevent.sleep(0.05)
        return True
        
    def wait_collect_moving(self, timeout=10):
        t0 = time.time()
        while not self.is_moving():
            elapsed = time.time() - t0
            if elapsed > timeout:
                return False
            gevent.sleep(0.05)
        return True

    def wait_collect_ready(self, timeout=10):
        collection_type = self.current_dc_parameters['experiment_type']

        t0 = time.time()
        while self.is_moving():
            #if collection_type == 'Characterization':
            #    self.adxv_show_latest()
            elapsed = time.time() - t0
            if elapsed > timeout:
                return False
            gevent.sleep(0.05)
        return True

    def wait_not_disabled(self, timeout=30):
        t0 = time.time()
        while self.is_disabled():
            elapsed = time.time() - t0
            if elapsed > timeout:
                 break
            gevent.sleep(0.05)
        
    def is_standby(self):
        return str(self.collect_state_chan.get_value()) == "STANDBY"

    def is_moving(self):
        state = str(self.collect_state_chan.get_value()) 
        if state in ["MOVING", "RUNNING"]:
             return True
        else:
             logging.getLogger("HWR").debug("IT IS NOT MOVING. IT IS %s" % state)
             return False

    def is_disabled(self):
        return str(self.collect_state_chan.get_value()) in ["DISABLE"]

    ## COLLECT SERVER STATE (END) ##

    ## PX1 ENVIRONMENT PHASE HANDLING ##
    def is_collect_phase(self):
        return self.px1env_hwobj.isPhaseCollect()

    def go_to_collect(self, timeout=180):
        self.px1env_hwobj.gotoCollectPhase()
        gevent.sleep(0.5)

        t0 = time.time()
        while True:
            env_state = self.px1env_hwobj.get_state()
            if env_state != "RUNNING" and self.is_collect_phase():
                break
            if time.time() - t0 > timeout:
                logging.getLogger("HWR").debug("PX1Collect: timeout sending supervisor to collect phase")
                break
            gevent.sleep(0.2)

        return self.px1env_hwobj.isPhaseCollect()

    def is_sampleview_phase(self):
        return self.px1env_hwobj.isPhaseVisuSample()

    def go_to_sampleview(self, timeout=180):
        self.px1env_hwobj.gotoSampleViewPhase()

        gevent.sleep(0.5)

        t0 = time.time()
        while True:
            env_state = self.px1env_hwobj.get_state()
            if env_state != "RUNNING" and self.is_sampleview_phase():
                break
            if time.time() - t0 > timeout:
                logging.getLogger("HWR").debug("PX1Collect: timeout sending supervisor to sample view phase")
                break
            gevent.sleep(0.2)

        self.lightarm_hwobj.adjust_light_level() 
        return self.is_sampleview_phase()

    ## PX1 ENVIRONMENT PHASE HANDLING (END) ##

    ## OTHER HARDWARE OBJECTS ##
    def set_energy(self, value):
        """
        Descript. :
        """
        curr_energy = self.get_energy()
        if abs(curr_energy - value) > 0.0005:
            self.energy_hwobj.move_energy(value)

    def set_wavelength(self, value):
        """
        Descript. :
        """
        self.energy_hwobj.move_wavelength(value)

    def get_energy(self):
        return self.energy_hwobj.get_energy()

    def set_transmission(self, value):
        """
        Descript. :
        """
        self.transmission_hwobj.set_value(value)

    def set_resolution(self, value):
        """
        Descript. : resolution is a motor in out system
        """
        #self.resolution_hwobj.move(value)
        # just store the value. 
        # delay move resolution after preparing diff phase
        self.resolution_target = value

    def do_set_resolution(self):
        value = self.resolution_target
        self.resolution_hwobj.sync_move(value)

    def move_detector(self,value):
        self.detector_hwobj.move_distance(value)

    def move_kappa(self,value):
        self.kappa_mot_hwobj.sync_move(value)

    def move_phi(self,value):
        self.kappaphi_mot_hwobj.sync_move(value)

    @task
    def move_motors(self, motor_position_dict):
        """
        Descript. :
        """
        self.diffractometer_hwobj.move_motors(motor_position_dict)

    def get_wavelength(self):
        """
        Descript. :
            Called to save wavelength in lims
        """
        if self.energy_hwobj is not None:
            return self.energy_hwobj.get_wavelength()

    def get_detector_distance(self):
        """
        Descript. :
            Called to save detector_distance in lims
        """
        if self.detector_hwobj is not None:
            return self.detector_hwobj.get_distance()

    def get_resolution(self):
        """
        Descript. :
            Called to save resolution in lims
        """
        if self.resolution_hwobj is not None:
            return self.resolution_hwobj.get_position()

    def get_transmission(self):
        """
        Descript. :
            Called to save transmission in lims
        """
        if self.transmission_hwobj is not None:
            return self.transmission_hwobj.get_att_factor()

    def get_undulators_gaps(self):
        """
        Descript. : return gaps as dict. In our case we have one gap,
                    others are collect_device0
        """
        if self.energy_hwobj:
            try:
                u20_gap = self.energy_hwobj.get_current_undulator_gap()
                return {'u20': u20_gap} 
            except:
                return {}
        else:
            return {}

    def get_beam_size(self):
        """
        Descript. :
        """
        if self.beam_info_hwobj is not None:
            return self.beam_info_hwobj.get_beam_size()

    def get_slit_gaps(self):
        """
        Descript. :
        """
        if self.beam_info_hwobj is not None:
            return self.beam_info_hwobj.get_slits_gap()
        return None,None

    def get_beam_shape(self):
        """
        Descript. :
        """
        if self.beam_info_hwobj is not None:
            return self.beam_info_hwobj.get_beam_shape()

    def get_measured_intensity(self):
        """
        Descript. :
        """
        if self.flux_hwobj is not None:
            flux = self.flux_hwobj.get_value()
        else:    
            flux = 0.0
        return float("%.3e" % flux)

    def get_machine_current(self):
        """
        Descript. :
        """
        if self.machine_info_hwobj:
            return self.machine_info_hwobj.get_current()
        else:
            return 0

    def get_machine_message(self):
        """
        Descript. :
        """
        if self.machine_info_hwobj:
            return self.machine_info_hwobj.get_message()
        else:
            return ''

    def get_machine_fill_mode(self):
        """
        Descript. :
        """
        if self.machine_info_hwobj:
            return self.machine_info_hwobj.get_fill_mode()
        else:
            return ''

    def get_beam_configuration(self):
        pars_beam = self.mxlocal_object['SPEC_PARS']['beam']
        ax = pars_beam.get_property('ax')
        ay = pars_beam.get_property('ay')
        bx = pars_beam.get_property('bx')
        by = pars_beam.get_property('by')
        return [ax,ay,bx,by]

    def get_undulators(self):
        return [U20(),]

    def get_flux(self):
        """
        Descript. :
        """
        return self.get_measured_intensity()

    ## OTHER HARDWARE OBJECTS (END) ##

    ## ADXV display images ##
    def adxv_connect(self):
        self.latest_shown = None
        self.adxv_latest_refresh = time.time()
        #  connect every time?? maybe we can do better
        try:
            res = socket.getaddrinfo(self.adxv_host, self.adxv_port, 0, socket.SOCK_STREAM)
            af, socktype, proto, canonname, sa = res[0]
            self.adxv_socket = socket.socket(af, socktype, proto)
            self.adxv_socket.connect((self.adxv_host, self.adxv_port))
            logging.getLogger().info("PX1Collect: ADXV Visualization connected.")
        except Exception as err:
            self.adxv_socket = None
            logging.getLogger().info("PX1Collect: WARNING: Can't connect to ADXV: %s" % err)

    def adxv_show_latest(self):
        elapsed = time.time() - self.adxv_latest_refresh

        if elapsed >= self.adxv_interval:
            template = self.current_dc_parameters['fileinfo']['template']
            directory = self.current_dc_parameters['fileinfo']['directory']
            template_glob = "%s*merged_*.%s" % (template.split("%0")[0], "cbf")
            img_list = sorted(glob(os.path.join(directory, template_glob)))

            if img_list and self.latest_shown != img_list[-1]:
                self.adxv_sync_image(img_list[-1])
                self.latest_shown = img_list[-1]
                self.is_firstimg = False
                self.adxv_latest_refresh = time.time()

    def adxv_sync_image(self, imgname):

        adxv_send_cmd = "\nload_image %s\n" + chr(32)

        try:
            if not self.adxv_socket:
                try:
                    logging.getLogger().info("PX1Collect: ADXV Visualization RE-connecting.")
                    self.adxv_connect()
                except Exception as err:
                    self.adxv_socket = None
                    logging.info("PX1Collect: ADXV: Warning: Can't connect to adxv socket to follow collect.")
                    logging.error("PX1Collect: ADXV0: msg= %s" % err)
            else:
                logging.getLogger().info("PX1Collect: ADXV_SOCKET_MSG: %s" % adxv_send_cmd % imgname)
                self.adxv_socket.send(adxv_send_cmd % imgname)
                logging.info(("PX1Collect: ADXV: "+ adxv_send_cmd[1:-2]) % imgname)
        except Exception as err:
            logging.error("PX1Collect: ADXV1: msg= %s" % err)
            try:
               del self.adxv_socket
               self.adxv_connect()
            except Exception as err:
               self.adxv_socket = None
               logging.error("PX1Collect: ADXV1: msg= %s" % err)

    ## ADXV display images (END) ##

    def set_image_headers(self, image_headers):
        """
        Set the image header
        """
        self.set_header_cmd(image_headers)

class U20(object):
    def __init__(self):
        self.type = 'u20'
'''
def test_hwo(hwo):
    
    Descript. : PX1Collect.py HardwareObject, test module
   
    print '\n======================== TESTS ========================'
    print 'These are tests of the HardwareObject PX1Collect'
    print '\n[IMPORTANT] to note: the following functions are turned'
    print '  off by default'
    print ' - ???'
    print '=======================================================\n'

    print '-------------------------------------------------------'
    print 'Defining some generic variables for all the tests'

    print '-------------------------------------------------------'

    print '\n-------------------------------------------------------'
    print 'Checking for the connection to Eiger.'
    print '-------------------------------------------------------'

#    print "Energy: ",hwo.get_energy()
#    print "Transm: ",hwo.get_transmission()
#    print "Resol: ",hwo.get_resolution()
#    print "PX1Environemnt (collect phase): ", hwo.is_collect_phase()
#    print "Shutters (ready for collect): ",hwo.check_shutters()
#    print "Flux: ",hwo.get_measured_intensity()
#    print "is collect? ", hwo.is_collect_phase()
#    print "is samplevisu? ", hwo.is_sampleview_phase()
#    print "goint to sample visu"
#    # hwo.go_to_sampleview()
#    # print "goint to collect"
#    # hwo.go_to_collect()
#    print "is collect? ", hwo.is_collect_phase()
#    print "is samplevisu? ", hwo.is_sampleview_phase()

if __name__ == '__main__':
    test_hwo('test')

 '''