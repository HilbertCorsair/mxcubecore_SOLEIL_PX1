import os
import re
import signal
import logging
import gevent
import subprocess
import math
import errno
import time
import copy
import io
import xmltodict
from xml.dom.minidom import parseString
from mxcubecore.BaseHardwareObjects import HardwareObjectState
from enum import (
    Enum,
    unique,)
from shutil import copyfile
import numpy as np
from scipy import optimize
from scipy.ndimage.filters import gaussian_filter
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
from matplotlib import cm
from mxcubecore import HardwareRepository as HWR
from mxcubecore.HardwareObjects.abstract.AbstractXrayCentring import AbstractXrayCentring
from mxcubecore.BaseHardwareObjects import HardwareObject
from PyTango import DeviceProxy
from mxcubecore.HardwareObjects.SampleView import Shape, Line, Point, Grid
from mxcubecore.HardwareObjects.CreateDirClient import CreateDirectoryClient

log = logging.getLogger('HWR')
gevent.monkey.patch_all()

@unique
class PX1PandaCollectStates(Enum):
    """Shutter states definitions."""
    OFF = HardwareObjectState.OFF, "ON"

class DialsProcessing(object):
    def __init__(self):
        pass

    def start_processing_mesh(self, master_file):
        pass

    def start_processing_helical(self, master_file):
        pass

    def get_results(self):
        pass

    def results_ready(self):
        pass

class SimulatedProcessing(DialsProcessing):
    def __init__(self):
        pass

    def start_processing(self, master_file):
        pass

    def get_results(self):
        pass

    def results_ready(self):
        pass


class PX1XrayCentring(AbstractXrayCentring):
    #STATES = PX1PandaCollectStates

    default_prefix = 'xraycent'

    process_pc = 'process2'

    mesh_exptime = 0.1 # time (in seconds) to expose frames

    nb_helical_scans = 2
    default_omega_relative = 120
    helical_exptime = 0.1

    minspots = 10 # minimum number of spots for image to be considered

    mpl_colormap = 'hot' # matplotlib colormap used for heatmap

    processing_classes = {
        'simulated': SimulatedProcessing,
        'dials': DialsProcessing,
    }

    # values used for gaussian blur and filtered pass
    blur_repeat = 4
    sigma = 2
    filter_highpass = 0.60

#####
# COMMENTED OUT BY LEO ON 2020-07-20 TO CHECK BEHAVIOUR
#    default_velocity = 100
#####

    def __init__(self,*args):

        HardwareObject.__init__(self,*args)

        self.sampx_mot = None
        self.sampy_mot = None
        self.phiy_mot = None
        self.kappa_mot = None
        self.kappaphi_mot = None
        self.omega_saved = None
        self.px1env_hwo = None
        self.lightarm_hwo = None
        self.graphics_manager_hwo = None
        self.session_hwo = None
        self.smargon_hwo = None
        self.groupname = None
        self.errmsg = ""
        self.state = None
        self.snapshot_files = []
        self.log_file = ""
        self.processing_method = ""
        self.waiting_grid = False
        self.proc_display = None
        self.only_helical = False
        self.shape = None
        self.found_spots = False

    def init(self):
        self.centring_task = None
        self.flag_is_centring = False
        self.collect_dev = DeviceProxy(self.get_property('tangoname'))
        self.collect_dev.set_timeout_millis(20000)
        self.collect_state_chan = self.get_channel_object("state")
        self.sgonaxis_dev = DeviceProxy(self.get_property('sgonaxis'))
        try:
            self.omega_relative = float( self.get_property('omega_increment') )
        except BaseException as e:
            self.omega_relative = self.default_omega_relative

        self.testmode = self.get_property('testmode')
        self.processing_method = self.get_property('processing')
        self.processing = self.processing_classes.get(self.processing_method, None)
        self.minidiff = HWR.beamline.diffractometer

        create_dir_address = self.get_property('mxcube_createdir_server')
        self.createdir_client = CreateDirectoryClient(create_dir_address)

        self.user_enabled = self.get_property("user_enable")

        log.debug(" XRAY CENTRING is in %s", self.testmode and "TEST MODE" or "REAL MODE")
        log.debug(" XRAY CENTRING using omega_increment %3.2f", self.omega_relative)

        self.omega_mot = self.get_object_by_role('omega')
        self.sampx_mot = self.get_object_by_role('sampx')
        self.sampy_mot = self.get_object_by_role('sampy')
        self.phiy_mot = self.get_object_by_role('phiy')
        self.phiz_mot = self.get_object_by_role('phiz')
        self.kappa_mot = self.get_object_by_role('kappa')
        self.kappaphi_mot = self.get_object_by_role('kappa_phi')

        self.motors_dict = {
            'phi': self.omega_mot,
            'phiy': self.phiy_mot,
            'sampx': self.sampx_mot,
            'sampy': self.sampy_mot,
            'phiz': self.phiz_mot,
            'kappa': self.kappa_mot,
            'kappa_phi': self.kappaphi_mot,
        }

        self.px1env_hwo = self.get_object_by_role('px1environment')
        self.lightarm_hwo  = self.get_object_by_role('lightarm')
        self.graphics_manager_hwo = self.get_object_by_role('graphics_manager')
        self._shape = None
        self.session_hwo = self.get_object_by_role('session')
        self.smargon_hwo = self.get_object_by_role('smargon')
        self.beaminfo_hwo = self.get_object_by_role('beaminfo')
        self.gevent_event = gevent.event.Event()
        self.auto_collect_counter = 0

    def define_state(self):
        motstate = self.get_channel_object("state").get_value() #self.collect_state_chan.get_value()
        ho_state = self.motstate_to_state(motstate)
        self.state = "ON" if ho_state.name == "READY" else self.state

    def motstate_to_state(self, motstate):
        motstate = str(motstate)
        if motstate in ["OFF", "STANDBY"]: #normal for PX11Energy
            state = self.STATES.READY
        elif motstate in ["MOVING", "RUNNING", "EXTRACT"]:
            state = self.STATES.BUSY
        elif motstate in ["FAULT", "DISABLE"]:
            state = self.STATES.FAULT
        else:
            state = self.STATES.UNKNOWN
        return state

    """def open_dialog(self, dict_dialog):
        # If necessary unblock dialog
        if not self.gevent_event.is_set():
            self.gevent_event.set()
        self.params_dict = dict()
        if "reviewData" in dict_dialog and "inputMap" in dict_dialog:
            review_data = dict_dialog["reviewData"]
            for dict_entry in dict_dialog["inputMap"]:
                if "value" in dict_entry:
                    value = dict_entry["value"]
                else:
                    value = dict_entry["defaultValue"]
                self.params_dict[dict_entry["variableName"]] = str(value)
            self.emit("parametersNeeded", (review_data,))
            self.state.value = "OPEN"
            self.gevent_event.clear()
            while not self.gevent_event.is_set():
                self.gevent_event.wait()
                gevent.sleep(0.1)
        return self.params_dict"""

    def get_values_map(self):
        return self.params_dict

    def set_values_map(self, params):
        self.params_dict = params
        self.gevent_event.set()

    def get_available_workflows(self):
        workflow_list = list()
        no_wf = len(self["workflow"])

        for wf_i in range(no_wf):
            wf = self["workflow"][wf_i]
            dict_workflow = dict()
            dict_workflow["name"] = str(wf.title)
            dict_workflow["path"] = str(wf.path)
            try:
                req = [r.strip() for r in wf.get_property("requires").split(",")]
                dict_workflow["requires"] = req
            except (AttributeError, TypeError):
                dict_workflow["requires"] = []
            dict_workflow["doc"] = ""
            workflow_list.append(dict_workflow)

        self.define_state()
        return workflow_list

    def start(self, list_arguments):


        # If necessary unblock dialog
        if not self.gevent_event.is_set():
            self.gevent_event.set()
        self.state.value = "RUNNING"

        self.dict_parameters = {}
        index = 0
        if len(list_arguments) == 0:
            self.error_stream("ERROR! No input arguments!")
            return
        elif len(list_arguments) % 2 != 0:
            self.error_stream("ERROR! Odd number of input arguments!")
            return
        while index < len(list_arguments):
            self.dict_parameters[list_arguments[index]] = list_arguments[index + 1]
            index += 2
        logging.info("Input arguments:")
        logging.info(print(self.dict_parameters))

        if "modelpath" in self.dict_parameters:
            modelpath = self.dict_parameters["modelpath"]
            if "." in modelpath:
                modelpath = modelpath.split(".")[0]
            self.workflow_name = os.path.basename(modelpath)
        else:
            self.error_stream("ERROR! No modelpath in input arguments!")
            return

        time0 = time.time()
        self.unattended_collect()
        time1 = time.time()
        logging.info("Time to start workflow: %f sec", time1 - time0)

    def unattended_collect(self):
        """
        The unattended_collect method aims to be a placeholder of an automatic collect of all samples

        It goes through a double for loop, one for the basket (1 to 3) and inside one for the position (1 to 16)
        This creates a sampleLocation to load with the sampleChanger

        Once loaded we set the zoom to 1 and do a first centring with murko
        We follow up with a second centring with murko at zoom 3

        Next we ask the coordinates of the bounding box of the loop at Murko and create a Grid shape based of it

        We then perferm an do_xcentring() which should find the Grid and use it for the heatmap

        Finally we prepare a params_list based on some dynamic modification and a xml file for static parameters and
        run a collect of the sample.

        ######################
        # CLUES AND WARNINGS #
        ######################

        => RedisMpegVideo has a restart_streaming(size) method, maybe we can use it when we loose camera
            sample_view._camera.restart_streaming(size=(sample_view._camera.get_width(), sample_view._camera.get_height()))
        => We can bring the light with lightarm_hwo._adjust_light_level() which puts the light based on current zoom level
            check PX1self.minidiff.py:632

        """

        log.debug("Debut de la method unattended_collect")

        #self.minidiff = HWR.beamline.diffractometer

        # hacking teh sample list by hardcoded slicing
        samples = HWR.beamline.sample_changer.get_sample_list()[:48]
        self.auto_collect_counter = 1
        with open('/home/experiences/proxima1/com-proxima1/arthur_mxcube/WebApp/config/paramRange.xml') as fd:
            doc = xmltodict.parse(fd.read())
        dataPositions = self.convert_xml_dict(doc)
        dataPositions = dataPositions['root']
        positions = self.convert_dict_range(dataPositions)


        log.debug("All positions that will be visited : %s\n", str(positions))

        start_time = time.perf_counter()
        previous_time = start_time
        all_timestamps = []

        for position in positions:

            """
            # Try restart camera with the RedisMpegVideo, maybe this can fix the issue
            sampleViewer = HWR.beamline.sample_view
            sampleViewer._camera.restart_streaming(size=(sampleViewer._camera.get_width(), sampleViewer._camera.get_height()))
            """
            zoom_position = self.minidiff.zoom.get_value()

            sample = samples[position]
            if zoom_position != "zoom1":
                self.minidiff.zoom._set_value(self.minidiff.zoom.VALUES['zoom1'])
                gevent.sleep(10)

            self.minidiff.start_centring_method(self.minidiff.CENTRING_METHOD_AUTO)
            gevent.sleep(10)
            self.minidiff.zoom._set_value(self.minidiff.zoom.VALUES['zoom2'])
            gevent.sleep(6)
            self.minidiff.start_centring_method(self.minidiff.CENTRING_METHOD_AUTO)
            gevent.sleep(15)

            x1, y1, x2, y2 = self.generateGridFromAnalysis(self.minidiff, RATIO=1, forceSquaredGrid=False, useInsideLoop=False)
            zoom_position = self.minidiff.zoom.get_value()
            beam_size_x = HWR.beamline.beam.get_beam_size()[0] * self.minidiff.zoom.positions[zoom_position]['calibrationData']['pixelsPerMmY']
            number_colums = math.ceil((x2-x1)/beam_size_x)
            x2n = x1 + number_colums*beam_size_x
            beam_size_y = HWR.beamline.beam.get_beam_size()[1] * self.minidiff.zoom.positions[zoom_position]['calibrationData']['pixelsPerMmZ']
            number_lines = math.ceil((y2-y1)/beam_size_y)
            y2n = y1 + number_lines*beam_size_y

            #Creating virtual grid

            mpos_left_top = self.minidiff.get_centred_point_from_coord(x1,y1)
            mpos_right_bottom = self.minidiff.get_centred_point_from_coord(x2n,y2n)
            mpos_list = [mpos_left_top, mpos_right_bottom]
            center_x = x1 + 1/2* (x2n -x1)
            center_y = y1 +1/2 *(y2n - y1)
            screen_coords = [center_x, center_y]

            # Create Grid object from imported Grid clas
            grid1 = Grid(mpos_list, screen_coords)
            #Grid default attributes :
            grid1.width = x2n-x1
            grid1.height = y2n- y1
            grid1.cell_count_fun = "zig-zag"
            grid1.cell_h_space = -1
            grid1.cell_height = beam_size_y
            grid1.cell_v_space = -1
            grid1.cell_width = beam_size_x
            grid1.label = "Grid"
            grid1.num_cols = number_colums
            grid1.num_rows = number_lines
            grid1.selected = False
            self.graphics_manager_hwo.add_shape(grid1)
            self.flag_is_centring = True


            self.do_xcentring(showReport=False) # This should avoid PopUp window of report
            while (self.flag_is_centring):
                time.sleep(1)

            if self.found_spots:
                # Generate a param_list to give to the collect
                # A verifier qu le ID des samples change pour chaque itration.
                param_list = self.prepareParamList( sample.get_id(), position)

                self.go_to_sampleview()
                time.sleep(3)
                imgPath1 = param_list[0]["fileinfo"]["archive_directory"] + '/' + param_list[0]["fileinfo"]["prefix"] + '_1_1.snapshot.jpeg'
                self.minidiff.takePictureAnalysis(path=imgPath1)
                time.sleep(2)
                imgPath2 = param_list[0]["fileinfo"]["archive_directory"] + '/' + param_list[0]["fileinfo"]["prefix"] + '_1_2.snapshot.jpeg'
                self.omega_mot.set_value(self.omega_mot.get_value() + 90)
                time.sleep(3)
                self.minidiff.takePictureAnalysis(path=imgPath2)
                time.sleep(2)

                # A verifier l'etat du PX1Cryotong
                # HWR.beamline.sample_changer._wait_device_ready()
                time.sleep(5)

                HWR.beamline.collect.current_dc_parameters = param_list[0]
                HWR.beamline.collect.do_collect("mxcube")
                gevent.sleep(20)

                # delete shapes and reset all counters
                self.graphics_manager_hwo.clear_all()

                self.graphics_manager_hwo._shapes = {} #delete_shape(grid_id)
                self.found_spots = False

                if self.auto_collect_counter == len(positions):
                    HWR.beamline.sample_changer._do_unload(sample, wash=False)

                    break
                HWR.beamline.sample_changer._do_load(sample=samples[positions[self.auto_collect_counter]], wash=False, souflette_time = False)
                self.auto_collect_counter += 1

            else:
                if self.auto_collect_counter == len(positions):
                    HWR.beamline.sample_changer._do_unload(sample, wash=False)
                    break
                self.graphics_manager_hwo.clear_all()
                self.graphics_manager_hwo._shapes = {}
                HWR.beamline.sample_changer._do_load(sample=samples[positions[self.auto_collect_counter]], wash=False, souflette_time = False)
                self.auto_collect_counter += 1

            end_time = time.perf_counter()
            time_for_sample = end_time - previous_time
            previous_time = end_time
            all_timestamps.append(time_for_sample)



        end_time = time.perf_counter()
        time_for_sample = end_time - previous_time
        previous_time = end_time
        all_timestamps.append(time_for_sample)

        log.debug("\nUnattended Collection finished\n\n")
        log.debug("Time per sample %s", str(all_timestamps))
        log.debug("Total time for collect was : %.4f seconds", (end_time - start_time))

    def generateGridFromAnalysis(self, minidiff, RATIO=1, forceSquaredGrid=False, useInsideLoop=False):

        # Automatic grid coordinates generation from murko analysis
        snapshot, imgName = minidiff.takePictureAnalysis()
        w, h, r, c = minidiff.estimate_click_murko(snapshot, forceSquaredGrid=False, imgName=imgName, useInsideLoop=False)
        og_h, og_w = int(os.getenv("MURKO_SIZEY")), int(os.getenv("MURKO_SIZEX"))

        zoom_position = minidiff.zoom.get_value()
        zoom_Y, zoom_Z = minidiff.zoom.positions[zoom_position]['calibrationData']['pixelsPerMmY'], minidiff.zoom.positions[zoom_position]['calibrationData']['pixelsPerMmZ']

        maxWidth = (0.4 * zoom_Y) / og_w
        maxHeight = (0.4 * zoom_Z) / og_h

        if (forceSquaredGrid):
            _, _, r, c = minidiff.estimate_click_murko(snapshot, forceSquaredGrid=False, imgName=imgName, useInsideLoop=False)
            if (r == 0.5 and c == 0.5):
                logging.getLogger("HWR").debug('There will be an issue in murko here !!!!!!!!!!!!!!!!!!!!!!!!!!!!')
            w = maxWidth
            h = maxHeight

        # (x1, y1, x2, y2) = (Left, Top, Right, Bottom)
        y1 = int((r - (h / 2) * RATIO) * og_h)
        x1 = int((c - (w / 2) * RATIO) * og_w)
        y2 = int((r + (h / 2) * RATIO) * og_h)
        x2 = int((c + (w / 2) * RATIO) * og_w)

        return x1, y1, x2, y2

    def convert_dict_range(self, dic):
        res = []
        puckNb = 0
        for el in dic.values():
            lower, upper = int(el['start_sample']), int(el['end_sample'])
            if lower > 0 and upper > 0:
                for j in range(lower - 1, upper):
                    res.append(j + puckNb * 16)
            puckNb += 1
        return res

    def createMotorDict(self):
        ordered_motors = {
            'phi': self.omega_mot.get_position(),
            'phiz': self.phiz_mot.get_position(),
            'phiy': self.phiy_mot.get_position(),
            'sampx': self.sampx_mot.get_position(),
            'sampy': self.sampy_mot.get_position(),
            'kappa': self.kappa_mot.get_position(),
            'kappa_phi': self.kappaphi_mot.get_position(),
            'beam_x': None,
            'beam_y':None,
            'zoom':None,

        }
        return ordered_motors

    def convert_xml_dict(self, xml_dict):
        if isinstance(xml_dict, dict):
            if '#text' in xml_dict:
                value = xml_dict['#text']
                type_info = xml_dict.get('@type')
                if type_info == 'int':
                    return int(value)
                elif type_info == 'float':
                    return float(value)
                elif type_info == 'bool':
                    return value.lower() == 'true'
                else:
                    return value
            elif '@type' in xml_dict and xml_dict['@type'] == 'null':
                return None
            elif all(key.startswith('@') for key in xml_dict.keys()):
                if xml_dict.get('@type') == 'dict':
                    return {}
                return ""
            else:
                if xml_dict.get('@type') == 'list':
                    if 'item' in xml_dict:
                        return [self.convert_xml_dict(xml_dict['item'])]
                    else:
                        return []

                new_dict = {}
                for key, value in xml_dict.items():
                    if not key.startswith('@'):
                        new_dict[key] = self.convert_xml_dict(value)
                return new_dict
        elif isinstance(xml_dict, list):
            return [self.convert_xml_dict(item) for item in xml_dict]
        else:
            return xml_dict

    def prepareParamList(self, sampleID, position):
        """
        Method to parse config/paramCollect.xml, convert into a python dict, override certain values and put inside param_list to be returned
        """
        with open('/home/experiences/proxima1/com-proxima1/arthur_mxcube/WebApp/config/paramCollect.xml') as fd:
            retrieved_data = xmltodict.parse(fd.read())

        param_list = self.convert_xml_dict(retrieved_data)['root']
        containerSampleChangerLocation, sampleLocation = (position // 16) + 1, (position % 16) + 1
        blSampleID = position + 6 # THIS IS HARD CODED AND WILL NEED TO BE FIXED WHEN POSSIBLE
        SamplesInContainer = [d for d in HWR.beamline.lims.get_samples() if d['containerSampleChangerLocation'] == str(containerSampleChangerLocation)]
        SampleAtLocation = [d for d in SamplesInContainer if d['sampleLocation'] == str(sampleLocation)]
        currentSample = SampleAtLocation[0]
        proteinAcronym = currentSample['proteinAcronym']
        sampleName = currentSample['sampleName']
        samplePrefix = proteinAcronym + "-" + sampleName
        exposureTime = 0.01
        oscillationRange = 0.1
        runNumber = 1
        proposal = HWR.beamline.lims.session_manager.active_session.number
        sessionID =  HWR.beamline.lims.session_manager.active_session.session_id
        template = samplePrefix + "_" + str(runNumber) + "_%004\d.h5"
        motors = self.createMotorDict()
        stringTimestamp = str(datetime.now())
        # TO DO put this in an config file
        masterPath = "/data4/proxima1-soleil/"+ "2026_Run1/" + stringTimestamp[:10] + "/" + proposal + '/'
        #protocole =  HWR.beamline.lims.session_manager.active_session.number
        # ISPyB or param_list -- a implementer HWR.beamline.lims
        smp_list = HWR.beamline.lims.get_samples()
        resolution =  smp_list[position]["diffractionPlan"]["requiredResolution"]
        param_list["detector_distance"] = HWR.beamline.resolution.resolution_to_distance(resolution, 0.979)
        param_list["fileinfo"]["prefix"] = samplePrefix
        param_list["fileinfo"]["directory"] = masterPath + "RAW_DATA/" + proteinAcronym + "/" + samplePrefix
        param_list["fileinfo"]["runNumber"] = runNumber
        param_list["fileinfo"]["archive_directory"] = masterPath + "ARCHIVE/"+ proteinAcronym + "/" + samplePrefix
        param_list["fileinfo"]["process_directory"] = masterPath + "PROCESSED_DATA/" + proteinAcronym + "/" + samplePrefix
        param_list["fileinfo"]["template"] = template
        param_list["sessionId"] = sessionID
        param_list["sample_reference"]["blSampleId"] = blSampleID
        param_list['sample_reference']['sample_name'] = sampleName
        param_list['sample_reference']['acronym'] = proteinAcronym
        param_list['oscillation_sequence'][0]['exposure_time'] = exposureTime
        param_list['oscillation_sequence'][0]['range'] = oscillationRange
        param_list["EDNA_files_dir"] = masterPath + "PROCESSED_DATA"
        param_list['motors'] = motors
        param_list['blSampleId'] = blSampleID

        return [param_list]

    def is_user_enabled(self):
        return self.user_enabled

    def set_groupname(self, groupname):
        log.debug("PX1XrayCentring / groupname is %s\n", str(groupname))
        self.groupname = str(groupname)

    def set_prefix(self, prefix):
        self.prefix = prefix

    def get_prefix(self):
        return self.prefix

    def start_xcentring(self):
        log.debug('PX1XrayCentring - starting xcentring %s', self.testmode and "SIMULATED" or "")

        self.mesh_nb_lines = 0
        self.mesh_img_per_line = 0

        self.emit('xcentringStarted')
        self.moved = False
        self.waiting_grid = True
        self.emit('xcentringInfo','user_input', 'Please select area to scan')
        self.graphics_manager_hwo.select_xcentring_area()
        self.log_msg("<Starting xray centring procedure>")

    def get_xcentring_deltas_start_end_mm(self):

        extent_dx_pix = self.shape.width
        extent_dy_pix =  self.shape.height
        cent_x_pix = self.shape_coords[0] + extent_dx_pix/2
        cent_y_pix = self.shape_coords[1] + extent_dy_pix/2
        start_dx_pix = self.shape_coords[0] - cent_x_pix
        start_dy_pix = self.shape_coords[1] - cent_y_pix
        end_dx_pix = self.shape_coords[0] + self.shape.width - cent_x_pix
        end_dy_pix = self.shape_coords[1] + self.shape.height - cent_y_pix
        start_dx_mm = -start_dx_pix / float(self.shape.pixels_per_mm[0])
        start_dy_mm = start_dy_pix / float(self.shape.pixels_per_mm[1])  # axe y opposite from grid to motor
        end_dx_mm = -end_dx_pix / float(self.shape.pixels_per_mm[0])
        end_dy_mm = end_dy_pix / float(self.shape.pixels_per_mm[1])
        extent_dx_mm = extent_dx_pix / float(self.shape.pixels_per_mm[0])
        extent_dy_mm = extent_dy_pix / float(self.shape.pixels_per_mm[1])
        return start_dx_mm, start_dy_mm, end_dx_mm, end_dy_mm, extent_dx_mm, extent_dy_mm

    def set_grid_and_continue(self):

        self.waiting_grid = False

        nlines = self.shape.num_rows
        nimgs = self.shape.num_cols * self.shape.num_rows

        start_dx, start_dy, end_dx, end_dy, extent_x, extent_y = \
              self.get_xcentring_deltas_start_end_mm()

        log.debug("GRID defined by user.  nlines: %s, nimgs: %s\n", nlines, nimgs)
        log.debug("  extent_x = %3.4f, extent_y = %3.4f", extent_x, extent_y)
        log.debug("  ps_startx = %3.4f , ps_starty = %3.4f\n", start_dx, start_dy)
        log.debug("  ps_endx = %3.4f , ps_endy = %3.4f\n", end_dx, end_dy)

        if not nlines or not nimgs:
            log.debug("WRONG Grid / no lines or no images")
            self.emit('xcentringInfo', 'error', 'Wrong GRID')
            return

        self.mesh_nb_lines = nlines
        self.mesh_img_per_line = self.shape.num_cols

        self.mesh_dx_start = start_dx
        self.mesh_dy_start = start_dy
        self.mesh_dx_end = end_dx
        self.mesh_dy_end = end_dy

        self.mesh_x_extent = extent_x
        self.mesh_y_extent = extent_y

        self.helical_nimgs = self.mesh_nb_lines * 2
        self.helical_y_extent = extent_y
        self.helical_y_step = float(self.helical_y_extent) / self.helical_nimgs
        self.helical_y_halfstep = self.helical_y_step / 2.0

        if self.mesh_nb_lines > 1:
            self.mesh_y_interval_size = extent_y / self.mesh_nb_lines
        else:
            self.mesh_y_interval_size = 0
        self.set_base_directories()

    def stop_xcentring(self):
        log.debug('PX1XrayCentring - stopping xcentring')
        self.log_msg("!! xcentring stopped by user")

        if self.waiting_grid:
            self.graphics_manager_hwo.stop_select_xcentring_area()
            self.waiting_grid = False
            self.xcentring_done()

        if self.centring_task is not None:
            self.centring_task.kill()

        if self.is_running():
            self.collect_dev.Stop()

        self.xcentring_done()

    def xcentring_done(self,task=None):
        log.debug('PX1XrayCentring - xcentring finished')
        self.log_msg("xcentring FINISHED")
        self.graphics_manager_hwo.end_xcentring()
        self.finish_centring()

    def xcentring_exception(self,task):
        log.debug('PX1XrayCentring - xcentring exception')
        self.log_msg("!! xcentring ended with error: %s" % self.errmsg)
        self.emit('xcentringFailed', self.errmsg)
        self.xcentring_restore()
        self.xcentring_done(task)

    def close_and_wrapup(self):
        self.stop_xcentring()
        self.graphics_manager_hwo.delete_xraycent_area()
        self.close_report_display()

    # MAIN CENTRING routine
    def  do_xcentring(self, showReport=True):
        try:
            log.debug('PX1XrayCentring - running xcentring')


            # self.X = []
            self.Y = []
            self.errmsg = ""

            # decide file output, directory, template
            #base_directory = self.get_base_directory()
            #output_directory = self.get_process_directory()
            # run the sequence
            self.emit('xcentringInfo', 'running', 'Preparing')
            #setting transmission to 20%
            HWR.beamline.transmission.set_value(15)

            self.prepare()
            self.prepare_report()

            # get sample snapshots
            #self.emit('xcentringInfo', 'running', 'Collecting snapshots')
            self.moved = True

            #self.collect_snapshots(output_directory)
            #self.snapshots_to_report()

            self.omega_mot.sync_move(self.omega_saved)
            gevent.sleep(1)

            if not self.wait_envready():
                self.emit('xcentringInfo', 'running', 'Error waiting for environment. Cannot continue')
                return

            # run a 2D mesh and move to best position

            if not self.only_helical:
                self.emit('xcentringInfo', 'running', 'Running mesh scan')
                self.run_mesh()
                self.zero_sgonaxis()
                x, y, spots  = self.do_mesh_analysis()
                axsnap = self.ax_snap[0]
                axheat = self.ax_heat[0]
                self.mesh_heatmap_report(axsnap, axheat, spots)

                if None in [x,y]:
                    with open('/home/experiences/proxima1/com-proxima1/arthur_mxcube/WebApp/config/paramRange.xml') as fd:
                        doc = xmltodict.parse(fd.read())
                    dataPositions = self.convert_xml_dict(doc)
                    dataPositions = dataPositions['root']
                    positions = self.convert_dict_range(dataPositions)
                    samples = HWR.beamline.sample_changer.get_sample_list()[:48]
                    self.emit('xcentringInfo', 'running', 'No result from mesh analysis')
                    self.finish_centring()
                    self.flag_is_centring = False
                    return
                    HWR.beamline.sample_changer._do_load(sample=samples[positions[self.auto_collect_counter]], wash=False, souflette_time = False)

                    # Continue with unatended data collection
                    #raise Exception('No result from mesh analyis')


                self.found_spots = True
                log.debug('PX1XrayCentring  obtaining mesh results x / y = %s / %s' %(x, y))
                self.emit('xcentringInfo', 'running', '  / best position found at x=%s/y=%s' %(x,y))
                self.emit('xcentringInfo', 'running', 'Moving to best position. x=%s/y=%s' %(x,y))


                self.move_best_position(omega=self.omega_saved)

            self.emit('xcentringInfo', 'running', 'Helical phase')

            #
            # save positions for centre calculation
            #
            self.x_saved = self.phiy_mot.get_position()

            # self.X.append(0.0)
            self.Y.append(0.0)  # distance from center. but we are at center

            # run a series of helical scans
            self.current_x = self.phiy_mot.get_position()
            self.current_y = self.current_sampy = self.sampy_mot.get_position()
            self.current_z = self.current_sampx = self.sampx_mot.get_position()
            self.current_omega = self.omega_mot.get_position()

            self.PHI = [self.omega_saved]

            self.log_msg("positions after mesh are xOffset=%3.4f, yOffset=%3.4f, zOffset=%3.4f" % \
                  (self.current_x, self.current_y, self.current_z))

            self.log_msg("                         omega=%3.4f" % self.current_omega)
            self.log_msg("helical0 (mesh) / omega %3.4f" % (self.current_omega))

            for i in range(self.nb_helical_scans):
                self.emit('xcentringInfo', 'running', 'Running helical scan %s' % (i+1))
                omega = self.omega_saved + self.omega_relative * (i+1)

                self.run_helical(omega, i+1)
                best_y, spots = self.do_helical_analysis(i,)

                self.PHI.append(omega)
                self.Y.append(best_y)

                self.emit('xcentringInfo', 'running', '  / best Y found at %s' % best_y)
                self.log_msg("helical%d        / omega %3.4f / best_y %s (pixels)" % (i+1,omega,best_y))

                ax_snap = self.ax_snap[i+1]
                ax_heat = self.ax_heat[i+1]
                self.helical_heatmap_report(ax_snap, ax_heat, i, spots)

            if None in self.Y:
                self.emit('xcentringInfo', 'running', 'No spots in helical analysis')
                raise Exception('No spots in helical analyis')

            self.emit('xcentringInfo', 'running', 'Calculating centred position')
            cpos = self.calculate_center()
            self.emit('xcentringInfo', 'running', 'Moving motors to %s' % str(cpos))
            self.move_motors(cpos)
            self.emit('xcentringInfo', 'running', 'Registering centered position')
            self.register_center_position(cpos)
        except BaseException as e:
            import traceback
            self.errmsg = str(e)
            self.errmsg = traceback.format_exc()
            log.debug(self.errmsg)
            raise(e)
        finally:
            if not self.only_helical:
                if self.fig:
                    self.fig.savefig(self.report_image)
                    display_cmd = "display %s" % self.report_image
                    if showReport:
                        self.proc_display = subprocess.Popen(display_cmd, shell=True)
                        log.debug("report display launched. process id is %s" % self.proc_display.pid)

        self.finish_centring()
        gevent.sleep(7)
        self.flag_is_centring = False

    def zero_sgonaxis(self):
        log.debug("ZEROing sgonaxis axis")
        self.log_msg("ZEROing sgonaxis axis")
        self.sgonaxis_dev.x = 0.0
        gevent.sleep(2)
        self.sgonaxis_dev.y = 0.0
        gevent.sleep(2)
        self.sgonaxis_dev.z = 0.0
        gevent.sleep(2)
        log.debug("ZEROing done x=%3.4f, y=%3.4f, z=%3.4f " % \
            (self.sgonaxis_dev.x, self.sgonaxis_dev.y, self.sgonaxis_dev.z))
        self.log_msg("ZEROing done x=%3.4f, y=%3.4f, z=%3.4f " % \
            (self.sgonaxis_dev.x, self.sgonaxis_dev.y, self.sgonaxis_dev.z))

    def close_report_display(self):
        if self.proc_display:
            try:
                # os.killpg(os.getpgid(self.proc_display.pid), signal.SIGTERM)
                self.proc_display.terminate()
            except:
                log.debug("Cannot kill process %d" % self.proc_display.pid)
                pass

    def calculate_center(self):
        self.log_msg("CALCULATE center (fit)")

        self.log_msg("   with PHIs: %s" % str(self.PHI))
        self.log_msg("          Ys: %s" % str(self.Y))

        PhiCamera = 90

        phis = [ math.radians(phi+PhiCamera) for phi in self.PHI ]
        n_points = len(phis)
        r, a, offset = self.multi_point_centre(np.array(self.Y), np.array(phis))

        dx = -r * np.sin(a)
        dy = r * np.cos(a)

        self.log_msg("   result: ")
        self.log_msg("      dx= %3.4f, dy=%3.4f" % (dx,dy))

        sampx_pos = float(self.current_sampx + dx)
        sampy_pos = float(self.current_sampy + dy)

        kappa_pos = self.kappa_mot.get_position()
        kappaphi_pos = self.kappaphi_mot.get_position()

        centred_pos = {'phi': self.omega_saved,
                       'phiy': self.current_x,
                       'sampx': sampx_pos,
                       'sampy': sampy_pos,
                       'phiz':  sampy_pos,  # phiz is not used anyway
                       'kappa':  kappa_pos,
                       'kappa_phi':  kappaphi_pos}

        return centred_pos

    def print_current_positions(self, title="CURRENT"):
        omega_pos = self.omega_mot.get_position()
        phiy_pos = self.phiy_mot.get_position()
        sampy_pos = self.sampy_mot.get_position()
        sampx_pos = self.sampx_mot.get_position()

        self.log_msg("%s positions are:" % title)
        self.log_msg("    omega                = %5.3f" % omega_pos)
        self.log_msg("    x_offset (phiy mot)  = %5.3f" % phiy_pos)
        self.log_msg("    y_offset (sampy mot) = %5.3f" % sampy_pos)
        self.log_msg("    z_offset (sampx mot) = %5.3f" % sampx_pos)


    def print_center_positions(self, pos):
        self.log_msg("CENTERED position is: %s" % str(pos))

    def multi_point_centre(self, ys, phis):
        def fitfunc(p,x):
            return p[0]*np.sin(x+p[1]) + p[2]

        def errfunc(p,x,y):
            return fitfunc(p,x) - y

        p1, success = optimize.leastsq(errfunc, [1.0,0.0,0.0], args=(phis,ys))
        return p1

    # run functions
    def prepare(self):
        self.set_base_directories()

        self.shape = self.graphics_manager_hwo.shapes
        self.shape_name = [name for name in self.shape.keys() if name.startswith("G")][0] # replace with [-1]
        self.shape_coords = self.shape[self.shape_name].screen_coord

        self.shape = self.shape[self.shape_name]

        self.best_position = None
        self.mesh_results = None
        self.snapshot_files = []
        self.fig = None

        self.save_current_pos()
        self.ps_y_saved, self.ps_z_saved = self.calc_pseudo(self.sampy_saved, self.sampx_saved, omega_pos=self.omega_saved)
        # calculate scene range in motor coordinates
        #self.scene_size = self.graphics_manager_hwo.get_scene_size_mm()

        self.left = self.phiy_saved + self.shape.width/2.0
        self.right = self.phiy_saved - self.shape.width/2.0


        self.top = self.ps_y_saved + self.shape.height/2.0
        self.bottom = self.ps_y_saved - self.shape.height/2.0
        self.total_range=[self.left, self.right, self.top, self.bottom]
        snapshot_0 = {'name': 'mesh', 'ps_y_saved': self.ps_y_saved, 'top': self.top, 'bottom': self.bottom, 'range': self.total_range }

        self.snapshot_info = [snapshot_0]

        for i in range(self.nb_helical_scans):
            omega = self.omega_saved +  self.omega_relative * (i+1)
            ps_y_saved, ps_z_saved = self.calc_pseudo(self.sampy_saved, self.sampx_saved, omega_pos=omega)
            top = ps_y_saved + self.shape.height/2.0
            bottom = ps_y_saved - self.shape.height/2.0
            total_range=[self.left, self.right, top, bottom]
            snapshot_info = {'name': 'helical%d' % (i+1),'ps_y_saved': ps_y_saved, 'top': top, 'bottom': bottom, 'range': total_range }
            self.snapshot_info.append(snapshot_info)

        for info in self.snapshot_info:
            log.debug("initial (pseudo) positions for %s:" % info['name'])
            log.debug("    ps_y_saved: %s" % info['ps_y_saved'])
            log.debug("    top: %s" % info['top'])
            log.debug("    bottom: %s" % info['bottom'])

        self.set_prefix(self.default_prefix)


        self.set_grid_and_continue()
        self.calculate_mesh()

        self.print_current_positions(title="INITIAL")
        self.fill_position_grid()

    def save_current_pos(self):
        self.omega_saved = self.omega_mot.get_position()
        self.phiy_saved = self.phiy_mot.get_position()
        self.sampy_saved = self.sampy_mot.get_position()
        self.sampx_saved = self.sampx_mot.get_position()

    def xcentring_restore(self):

        if self.omega_saved is None:
            return

        position_dict = {
           'xOffset':  self.phiy_saved,
           'yOffset':  self.sampy_saved,
           'zOffset':  self.sampx_saved,
           'omega':  self.omega_saved,
        }

        self.log_msg('RESTORING positions. Moving smargon to x=%s,y=%s,z=%s' % (self.phiy_saved,self.sampy_saved,self.sampx_saved))

        self.emit('xcentringInfo', 'running', 'restoring saved positions')
        self.smargon_hwo.move_motors(position_dict, wait=True)
        self.emit('xcentringInfo', 'finished', 'positions restored')
        self.moved = False

    def calculate_mesh(self):

        # pseudo y and z from motor positions
        #y_from_corner = self.mesh_y_interval_size / 2.0

        self.mesh_x_start = self.phiy_saved + self.mesh_dx_start
        self.mesh_x_end = self.phiy_saved + self.mesh_dx_end

        self.mesh_y_start = self.ps_y_saved + self.mesh_dy_start
        self.mesh_y_end = self.ps_y_saved + self.mesh_dy_end

        self.mesh_width = self.mesh_x_end - self.mesh_x_start
        self.mesh_height = self.mesh_y_end - self.mesh_y_start

        self.mesh_x_step = self.mesh_width / float(self.mesh_img_per_line)
        self.mesh_x_halfstep = self.mesh_x_step/2.0

        self.mesh_y_step = self.mesh_height / float(self.mesh_nb_lines)
        self.mesh_y_halfstep = self.mesh_y_step/2.0

        self.x_start, self.x_end = self.mesh_x_start, self.mesh_x_end

        first_y = self.mesh_y_start + self.mesh_y_halfstep # center of first line
        last_y = self.mesh_y_end - self.mesh_y_halfstep # center of last line

        self.y_start, self.z_start = self.calc_y_z(first_y, self.ps_z_saved, omega_pos=self.omega_saved)
        self.y_end, self.z_end = self.calc_y_z(last_y, self.ps_z_saved, omega_pos=self.omega_saved)

        return


    def fill_position_grid(self):
        # positions where the images are expected (at the center of each square)
        self.x_positions = np.arange(self.mesh_x_start+self.mesh_x_halfstep, self.mesh_x_end, self.mesh_x_step)
        self.y_positions = np.arange(self.mesh_y_start+self.mesh_y_halfstep, self.mesh_y_end, self.mesh_y_step)

        self.x_grid, self.y_grid = np.meshgrid(self.x_positions, self.y_positions)

        self.x_extended_pos = np.linspace(self.mesh_x_start+self.mesh_x_halfstep,
                                          self.mesh_x_end-self.mesh_x_halfstep,
                                          self.mesh_img_per_line * self.blur_repeat)

        self.y_extended_pos = np.linspace(self.mesh_y_start+self.mesh_y_halfstep,
                                          self.mesh_y_end-self.mesh_y_halfstep,
                                          self.mesh_nb_lines * self.blur_repeat)

        self.xe_grid, self.ye_grid = np.meshgrid(self.x_extended_pos, self.y_extended_pos)

    def run_mesh(self):
        self.emit('xcentringInfo', 'running', 'running mesh')
        while not self.is_collect_phase(): # put while here to avoid stuck because time out
            self.go_to_collect()
            gevent.sleep(2) # allow time to refresh display after

        # program mesh
        self.log_msg("MESH - Programming collect device:")
        self.log_msg('     nTrigger:    %d' % (self.mesh_nb_lines))
        self.log_msg('     nimages:     %d' % (self.mesh_img_per_line))
        self.log_msg('     startAngle:  %3.4f ' % (self.omega_saved))
        self.log_msg('     meshStart: x=%3.4f / y=%3.4f / z=%3.4f ' % (self.mesh_x_start, self.y_start, self.z_start))
        self.log_msg('     meshEnd:   x=%3.4f / y=%3.4f / z=%3.4f ' % (self.mesh_x_end, self.y_end, self.z_end))
        self.log_msg('     exposurePeriod:  %3.4f ' % (self.mesh_exptime))
        self.log_msg('     imagePath:  %s' % self.get_base_directory())
        self.log_msg('     imageName:  %s ' % self.get_prefix())

        self.collect_dev.characterisation = False
        self.collect_dev.mesh = True
        self.collect_dev.helicalScan = False
        self.collect_dev.nTrigger = self.mesh_nb_lines
        self.collect_dev.nimages = self.mesh_img_per_line
        self.collect_dev.startAngle = self.omega_saved
        self.collect_dev.meshStart = [float(x) for x in (self.mesh_x_start,self.y_start,self.z_start)]
        self.collect_dev.meshEnd = [float(x) for x in (self.mesh_x_end,self.y_end,self.z_end)]
        self.collect_dev.exposurePeriod = self.mesh_exptime
        self.collect_dev.imagePath = self.get_base_directory()
        self.collect_dev.imageName = self.get_prefix()

        # start the mesh
        self.collect_dev.prepareCollect()
        self.collect_dev.start()
        # wait collect to finish
        self.wait_collect_ready()

    def run_helical(self, omega, scan_no):

        log.debug(" Running helical %s at omega: %s\n", scan_no, omega)

        self.set_prefix("xraycent_helical%02d" % scan_no)
        py_pos, pz_pos = self.calc_pseudo(self.current_y, self.current_z, omega )

        self.helical_y0 = py_pos
        self.helical_z0 = pz_pos

        log.debug(" at %s py_pos %s, pz_pos:  %s", omega, self.helical_y0, self.helical_z0)

        self.dy_start = -self.helical_y_extent / 2.0
        self.dy_end =  self.helical_y_extent / 2.0

        py_start = self.helical_y0 + self.dy_start
        py_end = self.helical_y0 + self.dy_end

        x_start = x_end = self.current_x
        y_start, z_start = self.calc_y_z(py_start, self.helical_z0, omega_pos=omega)
        y_end, z_end = self.calc_y_z(py_end, self.helical_z0, omega_pos=omega)

        start_pos = x_start, y_start, z_start
        end_pos = x_end, y_end, z_end

        image_width = 1.0/self.helical_nimgs

        self.helical_y_deltas = np.arange(self.dy_start + self.helical_y_halfstep, self.dy_end, self.helical_y_step)

        self.log_msg("HELICAL coordinates are (in mm):")
        self.log_msg("       current xOffset (phiy).       %3.4f" % (self.current_x))
        self.log_msg("  Y:")
        self.log_msg("       current yOffset (sampy).       %3.4f" % (self.current_y))
        self.log_msg("       current zOffset (sampx).       %3.4f" % (self.current_z))
        self.log_msg("              current y screen.       %3.4f" % (self.helical_y0))
        self.log_msg("    Screen distance to center. Start: %3.4f, End: %3.4f (extent: %3.4f)" % (self.dy_start, self.dy_end, self.helical_y_extent))
        self.log_msg("     target screen vert_pos.   Start: %3.4f, End: %3.4f (height: %3.4f)" % (py_start, py_end, (py_end-py_start)))
        self.log_msg("       target  yOffset.        Start: %3.4f, End: %3.4f (  diff: %3.4f)" % (y_start, y_end, y_end - y_start))
        self.log_msg("       current zOffset (sampx).       %3.4f" % (self.current_z))
        self.log_msg("       target  zOffset.        Start: %3.4f, End: %3.4f (  diff: %3.4f)" % (z_start, z_end, z_end - z_start))
        self.log_msg("")
        self.log_msg("  Y deltas for images are:")
        self.log_msg("%s" % str(self.helical_y_deltas))


        # just for report
        self.helical_visual_positions = self.helical_y_deltas + py_pos
        self.helical_visual_start = py_pos - self.helical_y_extent / 2.0
        self.helical_visual_end = py_pos + self.helical_y_extent / 2.0
        self.helical_visual_height = self.helical_y_extent
        #
        self.collect_dev.characterisation = False
        self.collect_dev.mesh = False
        self.collect_dev.helicalScan = True
        self.collect_dev.startAngle = omega
        self.collect_dev.helicalStart = start_pos
        self.collect_dev.helicalEnd = end_pos
        self.collect_dev.imageWidth = image_width
        self.collect_dev.nimages = self.helical_nimgs
        self.collect_dev.exposurePeriod = self.helical_exptime
        self.collect_dev.imagePath = self.get_base_directory()
        self.collect_dev.imageName = self.get_prefix()
        #
        self.log_msg("HELICAL - Programming collect device:")
        self.log_msg("          omega:  %3.4f" % self.collect_dev.startAngle)
        self.log_msg("          start:  %s" % str(self.collect_dev.helicalStart))
        self.log_msg("            end:  %s" % str(self.collect_dev.helicalEnd))
        self.log_msg("          nimgs:  %d" % self.collect_dev.nimages)
        self.log_msg("     imageWidth:  %d" % self.collect_dev.imageWidth)
        self.log_msg(" exposurePeriod:  %d" % self.collect_dev.exposurePeriod)
        # start the mesh
        self.collect_dev.prepareCollect()
        self.collect_dev.start()
        #
        self.wait_collect_ready()

    def wait_collect_ready(self,timeout=480):
        t0 = time.time()
        while self.is_running():
            elapsed = time.time() - t0
            if elapsed > timeout:
                 break
            # log.debug('COLLECT IS RUNNING')
            gevent.sleep(0.5)

    def is_running(self):
        state = str(self.collect_state_chan.get_value())
        if state in ["MOVING", "RUNNING"]:
             return True
        else:
             logging.getLogger("HWR").debug("COLLECT IS NOT RUNNING. IT IS %s" % state)
             return False

    def do_mesh_analysis(self, method = "dozor"):
        log.debug('PX1XrayCentring - triggering do_mesh_analysis. %s', self.testmode and "SIMULATED" or "")

        self.emit('xcentringInfo', 'running', 'Analyzing mesh data')

        if self.testmode:


            # !!! log file name needs to be formalized

            dials_output_dir = os.path.dirname(__file__)
            dials_log_filename = os.path.join(dials_output_dir, 'log_report.txt')

        else:
            dials_output_dir = self.get_process_directory()


            dials_log_filename = "/home/experiences/proxima1/com-proxima1/progs/dozor/dozor.log/dozor_summary.log" #os.path.join(dials_output_dir, "dozor_summary.log")# 'log_report.txt')#"dials.find_spots.log") #'log_report.txt')
            # Impremet dozor analysis here
            if not method == "dozor":
                self.run_dials(dials_log_filename)
            else:
                self.run_dozor(dials_log_filename)

        dials_output_dir = self.get_process_directory()
        log.debug("PX1XrayCentring - proces directory is %s" % dials_output_dir)
        meshlog_filename = "%s_mesh" % os.path.basename(dials_log_filename)
        copyfile(dials_log_filename, os.path.join(dials_output_dir, meshlog_filename))
        spots = self.dials_get_spots_array(dials_log_filename, self.mesh_img_per_line, self.mesh_nb_lines)

        log.debug('PX1XrayCentring - getting reshaped array of spots : \n%s' % spots)


        self.log_msg('spots are: %s' % str(spots))

        spots_e = np.repeat(np.repeat(spots, self.blur_repeat, axis=0), self.blur_repeat, axis=1)
        blurred = gaussian_filter(spots_e,sigma=self.sigma)

        max_value = spots.max()
        index_max = np.unravel_index(spots.argmax(),spots.shape)

        bmax_value = np.max(blurred)
        bmax_idx = np.unravel_index(np.argmax(blurred), blurred.shape)

        self.log_msg('   max_value: %s' % max_value)
        self.log_msg('   index_max: %s' % str(index_max))

        if max_value < self.minspots:
            log.debug('PX1XrayCentring / data analysis done. not enough spots to consider data')
            self.x_best = None
            self.y_best = None
        elif index_max is not None:
            self.xs_best = self.x_grid[index_max]
            self.ys_best = self.y_grid[index_max]

            self.xg_best = self.xe_grid[bmax_idx]
            self.yg_best = self.ye_grid[bmax_idx]

            self.x_best = self.xg_best
            self.y_best = self.yg_best
            log.debug('PX1XrayCentring / data analysis done. best position is x=%s, y=%s' % (self.x_best, self.y_best))
            log.debug('      with gaussian_filter best position is x=%s, y=%s' % (self.xg_best, self.yg_best))
            log.debug('   without gaussian_filter best position is x=%s, y=%s' % (self.xs_best, self.ys_best))
        else:
            self.x_best = None
            self.y_best = None
            log.debug('PX1XrayCentring / data analysis done. cannot find best position')

        self.log_msg('    pos x:' % self.x_grid[index_max])
        self.log_msg('    pos y:' % (self.y_grid[index_max]+ self.mesh_y_halfstep))

        self.log_msg('data analysis done. best position is x=%s, y=%s' % (self.x_best, self.y_best))

        log.debug('showing heat map')
        log.debug('saving image')
        return self.x_best, self.y_best, spots

    def do_helical_analysis(self, i, method = "dozor"):
        log.debug('triggering helical data analysis. %s', self.testmode and "SIMULATED" or "")
        self.emit('xcentringInfo', 'running', 'Analyzing helical data')

        # run the analysis
        if self.testmode:
            dials_output_dir = os.path.dirname(__file__)
            dials_log_filename = os.path.join(dials_output_dir, 'dozor_summary.log')# 'log_report.txt')# 'dials.find_spots.log')
        else:
            dials_output_dir = self.get_process_directory()
            if method == "dozor":
                dials_log_filename = "/home/experiences/proxima1/com-proxima1/progs/dozor/dozor.log/dozor_summary.log"
            else:
                dials_log_filename = os.path.join(dials_output_dir, 'dials.find_spots.log')

            self.run_dozor(dials_log_filename) if method == "dozor" else self.run_dials(dials_log_filename)

        dials_output_dir = self.get_process_directory()
        helical_log_filename = "%s_helical_%s" % (os.path.basename(dials_log_filename), i)
        copyfile(dials_log_filename, os.path.join(dials_output_dir, helical_log_filename))

        # now this is a line. just get the list with spot numbers
        if method == "dozor":
            spots = self.dials_get_spots(helical=True)
        else:
            spots = self.dials_get_spots(dials_log_filename, helical=True)

        if self.testmode:
            spots = 10*spots

        max_value = spots.max()


        # find index and pseudo y for best value
        if max_value < self.minspots:
            py_max = None
        else:
            if self.testmode:
                idx_max = int(len(spots)/2.0)
                py_max = self.helical_y_deltas[idx_max]
            else:
                idx_max = spots.argmax()
                py_max = self.helical_y_deltas[idx_max]
                log.debug(" - helical analysis result before: %s" % py_max)
                py_max = self.calc_filtered_com(self.helical_y_deltas,  spots)
                log.debug(" - helical analysis result after: %s" % py_max)

        return py_max, spots

    def calc_filtered_com(self, x, y, high_pass=0.75):
        f_value = y.max() * high_pass
        y = copy.copy(y)
        y[y<f_value] = 0
        com = (x*y).sum() / y.sum()
        return com

    def move_omega(self, relative_pos):
        log.debug('PX1XrayCentring / move omega by %s' % relative_pos)
        self.omega_mot.sync_move_relative(relative_pos)

    def move_best_position(self,omega):
        if None in (self.x_best, self.y_best):
            return
        self.move_x_y(self.x_best, self.y_best, omega)

    def move_x_y(self,x,y, omega):

        log.debug('PX1XrayCentring / moving to X=%s,Y=%s' % (x,y))

        x_target = x
        y_target, z_target = self.calc_y_z(y, self.ps_z_saved, omega_pos=omega)

        position_dict = {
           'xOffset':  x_target,
           'yOffset':  y_target,
           'zOffset':  z_target,
        }

        log.debug('PX1XrayCentring / moving smargon to x=%s,y=%s,z=%s' % (x_target,y_target,z_target))
        self.log_msg('moving smargon to xOffset=%3.4f, yOffset=%3.4f, zOffset=%3.4f' % (x_target,y_target,z_target))
        self.smargon_hwo.move_motors(position_dict, wait=True)

    def move_motors(self, position_dict):
        self.smargon_hwo.set_freeze(True)
        for motor, pos in position_dict.items():
            self.motors_dict[motor].move(pos)
        self.smargon_hwo.set_freeze(False)

    def calc_pseudo(self, y, z, omega_pos=None):

        if omega_pos is None:
            omega_pos = self.omega_mot.get_position()

        omega = math.radians(omega_pos)

        py =  y*math.cos(omega) + z*math.sin(omega)
        pz =  -y*math.sin(omega) + z*math.cos(omega)

        return py,pz

    def calc_y_z(self, ypseudo, zpseudo, omega_pos=None):
        """ normally we want z to be zero """
        if omega_pos is None:
            omega_pos = self.omega_mot.get_position()

        omega = math.radians(omega_pos)

        log.debug('calc_y_z - getting values for omega %s', omega)
        y = ypseudo * math.cos(omega) - zpseudo*math.sin(omega)
        z = + ypseudo * math.sin(omega) + zpseudo*math.cos(omega)

        log.debug('   pseudo y,z %3.4f, %3.4f', ypseudo, zpseudo)
        log.debug('     real y,z %3.4f, %3.4f', y, z)


        return y,z

    def register_center_position(self, cpos):
        centring_status = {}

        centring_state = 'success'
        centring_status['motors'] = cpos

        self.emit('xcentringInfo', 'running', 'Registering point with Graphics Manager')
        self.create_centring_point(centring_state, centring_status)

    def create_centring_point(self, centring_state, centring_status, emit=True):
        """Creates a new centring position and adds it to graphics point.

        :param centring_state:
        :type centring_state: str
        :param centring_status: dictionary with motor pos and etc
        :type centring_status: dict
        :emits: centringInProgress
        """
        from mxcubecore.model import queue_model_objects
        p_dict = {}

        if "motors" in centring_status and "extraMotors" in centring_status:

            p_dict = dict(centring_status["motors"], **centring_status["extraMotors"])
        elif "motors" in centring_status:
            p_dict = dict(centring_status["motors"])

        self.emit("centringInProgress", False)
        if p_dict:
            p_dict["beam_x"] = self.shape.beam_pos[0]
            p_dict["beam_y"] = self.shape.beam_pos[1]
            p_dict["zoom"] = HWR.beamline.diffractometer.zoom.get_value()
            cpos = queue_model_objects.CentredPosition(p_dict)


            screen_pos = HWR.beamline.diffractometer.motor_positions_to_screen(
                cpos.as_dict()
            )
            #screen_coords = HWR.beamline.diffractometer.motor_positions_to_screen(cpos)
            # this list might be completly useless hereà
            mpos_list = [v for v in cpos.as_dict().values()]

            point = self.graphics_manager_hwo.add_shape_from_mpos([cpos.as_dict()], screen_pos ,"P")
            self.graphics_manager_hwo.add_shape(point)
            #cpos.set_index(point.index)
            return point



    def finish_centring(self):
        if self.moved:
            self.emit('xcentringInfo', 'done', 'Centring finished')
        else:
            self.emit('xcentringInfo', 'finished', 'Centring finished')
        #This is a good plce to add a while not ready --> Sleap 2 or wait_ready
        self.wait_envready()

    def command_failure(self):
        return False

#####
# COMMENTED OUT BY LEO ON 2020-07-20 TO CHECK BEHAVIOUR
#        self.sgonaxis_dev.velocity = self.default_velocity
#####
        self.emit('xcentringFinished')
        log.debug('Centring done. all finished')

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

    def create_checkdir(self,directory):

        if not os.path.exists(directory):
            log.debug('PX1XrayCentring / creating process directory %s' % directory)

            basedir = self.get_base_project_directory(directory)
            ret,msg = self.prepare_base_project_directory(basedir)

            if not ret:
                self.errmsg = 'Cannot create base directory for xray_centring %s' % basedir
                raise Exception(self.errmsg)
            else:
                log.debug("Directory %s created" % basedir)

            try:
                os.makedirs(directory)
                os.system("chmod 777 %s" % directory)
            except OSError as e:
                if e != errno.EEXIST:
                    import traceback
                    log.debug(traceback.format_exc())
                    self.errmsg = 'Cannot create process directory for dials'
                    raise Exception(self.errmsg)
        else:
            log.info('PX1XrayCentring - directory already exists %s' % directory)


    def set_base_directories(self):
        self.set_prefix(self.default_prefix)
        dtime = datetime.now()
        dtime_str = '{0.year}{0.month:02d}{0.day:02d}_{0.hour:02d}{0.minute:02d}'.format(dtime)
        dirname = '%s_%s' % (self.get_prefix(), dtime_str)

        base_directory = self.session_hwo.get_image_directory("")
        output_directory = self.session_hwo.get_archive_directory()

        if not self.groupname:
            self.groupname = "xraycent"

        base_directory = os.path.join(base_directory, self.groupname, dirname)
        output_directory = os.path.join(output_directory, self.groupname, dirname)

        self.set_base_directory(base_directory)
        self.set_process_directory(output_directory)

        self.report_image = os.path.join(output_directory, 'report_xraycent.png')
        self.filename_mesh = os.path.join(output_directory, 'mesh_only.png')
        self.log_file = os.path.join(output_directory, 'log_report.txt')

    def set_base_directory(self,directory):
        log.debug("PX1XrayCentring - setting base directory to be %s" % directory)

        self.create_checkdir(directory)
        self.base_directory = directory

    def set_process_directory(self, directory):
        log.debug("PX1XrayCentring - setting process directory to %s" % directory)
        self.create_checkdir(directory)
        self.process_directory = directory


    def get_base_directory(self):
        log.debug('PX1XrayCentring - returning the base directory : %s' % self.base_directory)
        return self.base_directory

    def get_process_directory(self):
        log.debug('PX1XrayCentring - returning the process directory : %s' % self.process_directory)
        return self.process_directory

    #
    # Supporting actions
    #
    '''def collect_snapshots(self,output_directory):
        """
        Descript. :
        """
        if not self.is_sampleview_phase():
            self.go_to_sampleview()
            gevent.sleep(2) # allow time to refresh display after

        self.lightarm_hwo.adjustLightLevel()
        gevent.sleep(2) # allow time to refresh display after

        snapshot_name = '%s_snapshot_0.png' % self.get_prefix()
        snapshot_filename = os.path.join(output_directory, snapshot_name)

        self.save_snapshot(snapshot_filename)
        self.snapshot_files.append(snapshot_filename)

        for i in range(self.nb_helical_scans):
            self.move_omega(self.omega_relative)
            gevent.sleep(0.1)
            snapshot_name = '%s_snapshot_%d.png' % (self.get_prefix(), self.omega_relative * (i+1))
            snapshot_filename = os.path.join(output_directory, snapshot_name)
            self.save_snapshot(snapshot_filename)
            self.snapshot_files.append(snapshot_filename)

        #  this maybe (to be tested return the image from graphics manager without the need to save and load later
        #
        #  self.snapshot_image = self.graphics_manager_hwo.save_scene_snapshot(filename_noshape, return_as_array=True, include_items=False)

    def save_snapshot(self, filename):
        self.graphics_manager_hwo.save_scene_snapshot(filename, include_items=False)
        log.debug("PX1Collect:  - snapshot saved to %s" % filename)'''

    def is_sampleview_phase(self):
        return self.px1env_hwo.is_phase_visu_sample()

    def go_to_sampleview(self, timeout=180):
        self.px1env_hwo.goto_sample_view_phase()

        gevent.sleep(0.5)

        t0 = time.time()
        while True:
            env_state = self.px1env_hwo.get_state()
            if env_state != "RUNNING" and self.is_sampleview_phase():
                break
            if time.time() - t0 > timeout:
                log.debug("PX1XrayCentring: timeout sending supervisor to sample view phase")
                break
            gevent.sleep(0.5)

        self.lightarm_hwo._adjust_light_level()
        return self.is_sampleview_phase()

    def wait_envready(self,timeout=20):
        start_time = datetime.now()
        while True:
            env_state = self.px1env_hwo.get_state()
            if env_state not in  ["RUNNING", "MOVING"]:
                break

            t0 = (datetime.now() - start_time).seconds

            if time.time() - t0 > timeout:
                log.debug("PX1XrayCentring: timeout sending supervisor to sample view phase")
                break
            gevent.sleep(0.5)
        return True


    def is_collect_phase(self):
        return self.px1env_hwo.is_phase_collect()

    def go_to_collect(self, timeout=180):
        if self.testmode:
            return

        self.px1env_hwo.goto_collect_phase()
        gevent.sleep(0.5)

        t0 = time.time()
        while True:
            env_state = self.px1env_hwo.get_state()
            if env_state != "RUNNING" and self.is_collect_phase():
                break
            if time.time() - t0 > timeout:
                logging.getLogger("HWR").debug("PX1XrayCent: timeout sending supervisor to collect phase")
                break
            gevent.sleep(0.5)

        return self.px1env_hwo.is_phase_collect()

    ###  supporting actions end

    #
    # Dials results
    #
    def run_dozor (self, dozor_log_file):
        #self.graphics_manager_hwo.stop_stream()
        log.debug('PX1XrayCentring - triggering run_dozor function')
        dials_output_dir = self.get_process_directory()
        dozor_cmd = "/home/experiences/proxima1/com-proxima1/progs/dozor_offline.sh"
        master_file = os.path.join(self.get_base_directory(), "%s_master.h5" % self.get_prefix())
        username = self.session_hwo.get_ssh_name()
        log.debug('PX1XrayCentring - username is:%s.' % username)
        if not username:
            username = "com-proxima1"

        dozor_cmd = 'cd %s ; %s %s %s' % (dials_output_dir, dozor_cmd,"-m", master_file)
        log.debug('PX1XrayCentring - sending subprocess command for dozor processing')
        log.debug('      command is: \n%s' % dozor_cmd)
        subprocess.call(dozor_cmd, shell=True)

        # wait for result file to appear on disk
        t0 = time.time()

        while not os.path.exists(dozor_log_file):
            # check for NFS cache refresh
            os.system('touch %s' % dials_output_dir)
            gevent.sleep(0.25)
            elapsed = time.time() - t0
            if elapsed > 50.0:
                self.emit('xcentringInfo', 'error', 'timeout (%3.2f secs) waiting for analysis results. aborting' % elapsed)
                raise Exception("PX1XrayCentring - timeout waiting for dozor log file (%s)" % elapsed)
            #self.graphics_manager_hwo.start_stream()
            return
        #self.graphics_manager_hwo.start_stream()


    def run_dials(self, dials_log_filename):
        log.debug('PX1XrayCentring - triggering run_dials function')

        dials_output_dir = self.get_process_directory()
        #dials_cmd = "/data2/bioxsoft/bin/dials.find_spots"
        #dials_cmd = "/data2/bioxsoft/progs/PHENIX/phenix-1.18.2-3874/build/bin/dials.find_spots"
        dials_cmd = "/usr/local/dials-v3-23-0/build/bin/dials.find_spots"
        #dials_options = 'shoebox=False per_image_statistics=True spotfinder.filter.ice_rings.filter=True nproc=244 spotfinder.filter.d_min=4'
        dials_options = 'shoebox=False per_image_statistics=True spotfinder.filter.ice_rings.filter=True nproc=244 '

        master_file = os.path.join(self.get_base_directory(), "%s_master.h5" % self.get_prefix())

        username = self.session_hwo.get_ssh_name()
        # DEBUT- PIERRE L. 2021-02-21
        log.debug('PX1XrayCentring - username is:%s.' % username)
        if not username:
            username = "com-proxima1"
        # FIN- PIERRE L. 2021-02-21
        #dials_cmd = 'ssh %s@process2 "cd %s ; %s %s %s"' % (username, dials_output_dir, dials_cmd, dials_options, master_file)
        dials_cmd = 'cd %s ; %s %s %s' % (dials_output_dir, dials_cmd, dials_options, master_file)
        log.debug('PX1XrayCentring - sending subprocess command for dials processing')
        log.debug('      command is: \n%s' % dials_cmd)
        subprocess.call(dials_cmd, shell=True)

        # wait for result file to appear on disk
        t0 = time.time()

        while not os.path.exists(dials_log_filename):
            # check for NFS cache refresh
            os.system('touch %s' % dials_output_dir)
            gevent.sleep(0.25)
            elapsed = time.time() - t0
            if elapsed > 120.0:
                self.emit('xcentringInfo', 'error', 'timeout (%3.2f secs) waiting for analysis results. aborting' % elapsed)
                raise Exception("PX1XrayCentring - timeout waiting for dials log file (%s)" % elapsed)
                return

    # Doesn't have much to do with dials
    def dials_get_spots(self, filename="/home/experiences/proxima1/com-proxima1/progs/dozor/dozor.log/dozor_summary.log", columns=(1,), helical=False):
# two dials formats are supported
#      OLD
#----------------------------------------------------
#| image | #spots | #spots_no_ice | total_intensity |
#----------------------------------------------------
#| 1     | 0      | 0             | 0               |

#      NEW
#  16.2: +---------+----------+-----------------+-------------------+
#        |   image |   #spots |   #spots_no_ice |   total_intensity |
#        |---------+----------+-----------------+-------------------|
#        |       1 |        0 |               0 |                 0 |
#

        log.debug('PX1XrayCentring - triggering dials_get_spots function')
        log.debug('\nHere is the filename : %s\n\n', filename)
        buff = open(filename).read()

        cursor = 0

        # search for first line starting with |
        #   old format:   "|   image |   #spots |    [...] "
        #   new format:   "         |   image |   #spots |    [...]"

        mat=re.search("^[ \t]*\|",buff[cursor:], re.DOTALL | re.MULTILINE)
        cursor += mat.end()

        # search for second line starting with |
        #   old format:   "| 1     | 0      | 0    [...]"
        #   new format    "     |---------+---- [...]"
        mat=re.search("^[\ \t]*\|",buff[cursor:], re.DOTALL | re.MULTILINE)
        cursor += mat.end()

        if buff[cursor+1:cursor+2] == '-':
        # if second | is followed by a - we are in new dials format.
        #    then search for a third | line
        #    new format:   "         |   image |   #spots |    [...]"
            log.debug("loading dials file with new format")
            mat=re.search("^[\ \t]*\|",buff[cursor:], re.DOTALL | re.MULTILINE)
            cursor += mat.end()
        else:
            log.debug("loading dials file with old format")

        block_starts = cursor
        # search for last line of table
        #   old format "------------[..]"
        #   new format "     +--------+----[..]"
        mat=re.search("^\s*\+*\-{7}", buff[cursor:], re.DOTALL | re.MULTILINE)

        cursor = cursor + mat.start()

        block_ends = cursor
        buff = buff[block_starts:block_ends]
        buff = buff.strip()
        buff = buff.replace('|','')

        arr = np.loadtxt(io.BytesIO(buff.encode()), usecols=columns)
        # Refactored to remove StringIO
        #arr = np.fromstring(buff, sep='\n')
        # in helical mode for test.
        #   return an array with 12 images = self.helical_nimages
        #if self.testmode and helical:  # this is a TEST ONLY feature. remove it when done
        #    arr = arr[:self.helical_nimgs]


        return arr

    def dials_get_spots_array(self, filename, nbimages, nblines, columns=(1,)):
        log.debug('PX1XrayCentring - triggering dials_get_spots_array function')
        log.debug('           filename : %s' % filename)
        log.debug('           nbimages : %s' % nbimages)
        log.debug('            nblines : %s' % nblines)

        spots = self.dials_get_spots(filename, columns=(1,))

        """if self.testmode: # get only as many as we need
            spots = spots[:nbimages*nblines]
            spots = 10*spots # to have enough number of spots"""

        log.debug('              spots : \n%s' % spots)

        return spots.reshape((nblines, nbimages))

    def prepare_report(self):
        self.fig = plt.figure()
        self.fig.set_size_inches(10,7)

        ax1 = plt.subplot2grid((2,10), (0,0), colspan=5) # snap0
        ax2 = plt.subplot2grid((2,10), (0,5), colspan=5) # mesh
        ax3 = plt.subplot2grid((2,10), (1,0), colspan=3) # snap1
        ax4 = plt.subplot2grid((2,10), (1,3), colspan=1) # heli1
        ax4.yaxis.tick_right()
        ax5 = plt.subplot2grid((2,10), (1,5), colspan=3) # snap2
        ax6 = plt.subplot2grid((2,10), (1,8), colspan=1) # heli2
        ax6.yaxis.tick_right()
        plt.subplots_adjust(wspace=0.4, hspace=0.4)

        self.fig.suptitle("Xray Centring report (%s) nb spots" % self.processing_method)

        self.ax_snap = [ax1,ax3,ax5]
        self.ax_heat = [ax2,ax4,ax6]

    def mesh_heatmap_report(self, axsnap, axheat, spots):

        self.emit('xcentringInfo', 'running', 'Creating heat map')

        xticks = [self.left, self.mesh_x_start, self.mesh_x_end, self.right]
        yticks = [self.top, self.mesh_y_start, self.mesh_y_end, self.bottom]

        xlabels = [ "%.3f" % label for label in xticks]
        ylabels = [ "%.3f" % label for label in yticks]

        axsnap.set_xticks(xticks)
        axsnap.set_yticks(yticks)
        axsnap.set_yticklabels(ylabels, fontsize=8)
        axsnap.set_xticklabels(xlabels, fontsize=8, rotation=30)

        self.show_grid(axsnap)
        self.show_spots(axsnap, spots)
        self.show_center(axsnap)

        axheat.set_title("mesh spots")
        axheat.axis([self.mesh_x_start, self.mesh_x_end,self.mesh_y_end,self.mesh_y_start])

        xlabels = [ "%.3f" % label for label in self.x_positions]
        ylabels = [ "%.3f" % label for label in self.y_positions]

        axheat.set_xticks(self.x_positions)
        axheat.set_xticklabels(xlabels, fontsize=8, rotation=30)
        axheat.set_yticks(self.y_positions)
        axheat.set_yticklabels(ylabels, fontsize=8)

        self.show_grid(axheat)
        #self.show_snapshot(axheat,0)
        self.show_spots(axheat, spots, show_values=True)
        self.show_center(axheat)

        self.emit('xcentringInfo', 'running', '  / Saving IMG report to %s' % self.report_image)

        # ax0.cla()
        self.fig2, ax2_1 = plt.subplots(1, 1, figsize=(9,6))
        ax2_1.axis([self.mesh_x_start, self.mesh_x_end,self.mesh_y_end,self.mesh_y_start])
        self.show_grid(ax2_1)
        self.show_spots(ax2_1, spots)
        ax2_1.get_xaxis().set_visible(False)
        ax2_1.get_yaxis().set_visible(False)

        self.fig2.savefig(self.filename_mesh,bbox_inches='tight', pad_inches=0)

        #self.graphics_manager_hwo.set_xray_heatmap(self.filename_mesh)

        return

    def helical_heatmap_report(self, axsnap, axheat, helicalno, spots):
        # create a colormap to be used
        min_spots = spots.min()
        max_spots = spots.max()

        nb_colors = int((max_spots-min_spots+1)*1)
        my_cmap = cm.get_cmap(self.mpl_colormap,nb_colors)

        y_positions = self.helical_visual_positions
        y_start = self.helical_visual_start
        y_end = self.helical_visual_end
        y_height = self.helical_visual_height

        log.debug("visual positions for helical scan: %s" % str(y_positions))
        log.debug("            visual helical_height: %s", self.helical_visual_height)
        log.debug("                          y_start: %s", y_start)
        log.debug("                            y_end: %s", y_end)
        log.debug("         visual helical_height(2): %s", str(y_start - y_end))

        top = self.snapshot_info[helicalno+1]['top']
        bottom = self.snapshot_info[helicalno+1]['bottom']

        if self.only_helical:
            x_0 = (self.right + self.left)/2.0
        else:
            x_0 = self.x_best
        xticks = [self.left,x_0, self.right]
        #yticks = [self.top,y_start, y_end, self.bottom]
        yticks = [top, y_start, y_end, bottom]
        log.debug("helical is %s, yticks are: %s" % (helicalno, str(yticks)))

        xlabels = [ "%.3f" % label for label in xticks]
        ylabels = [ "%.3f" % label for label in yticks]

        axsnap.set_xticks(xticks)
        axsnap.set_yticks(yticks)
        axsnap.set_xticklabels(xlabels,fontsize=8, rotation=30)
        axsnap.set_yticklabels(ylabels,fontsize=8)

        self.show_grid(axsnap, helical=True)
        self.show_grid(axheat, helical=True)

        w = self.mesh_x_step
        h = self.helical_y_step

        axheat.axis([self.x_best-w/2.0-2*w, self.x_best+w/2.0+2*w, y_end, y_start])
        #self.show_snapshot(axheat,helicalno+1)
        xticks = [self.x_best,]
        xlabels = ["%.3f" % self.x_best, ]
        ylabels = [ "%.3f" % label for label in y_positions]
        axheat.set_xticks(xticks)
        axheat.set_xticklabels(xlabels, fontsize=8)
        axheat.set_yticks(y_positions)
        axheat.set_yticklabels(ylabels, fontsize=8)


        for i in range(self.helical_nimgs):
            xc = self.x_best
            yc = y_positions[i] - self.helical_y_halfstep
            val = int(spots[i])
            xy = xc-w/2.0,yc
            spot_no = 1.0 - val / float(nb_colors)
            color = my_cmap(spot_no)
            p = Rectangle(xy,color=color,alpha=0.2,width=w, height=h)
            p2 = Rectangle(xy,color=color,alpha=0.2,width=w, height=h)
            axheat.add_patch(p)
            axsnap.add_patch(p2)
            axheat.text(xc-w/3.0,yc+h/2.0,str(val), fontsize=7)

    '''def snapshots_to_report(self):

        snap_titles = ["MESH", "Helical@ omega+120", "Helical@ omega+240"]

        for snap_no in range(len(self.ax_snap)):
            title = snap_titles[snap_no]
            ax = self.ax_snap[snap_no]
            ax.set_title(title)
            self.show_snapshot(ax, snap_no)

    def show_snapshot(self, ax, snap_no):
        snapshot_filename = self.snapshot_files[snap_no]
        image = plt.imread(snapshot_filename)
        total_range = self.snapshot_info[snap_no]['range']
        log.debug(" - showing snapshot %d - total_range is %s" % (snap_no, str(total_range)))
        ax.imshow(image, extent=total_range)'''

    def show_grid(self, mpl_axis, helical=False):
        if helical:
            x = self.x_best - self.mesh_x_halfstep
            y_start = self.helical_visual_start
            y_end = self.helical_visual_end
            y = y_start < y_end and y_start or y_end
            w = self.mesh_x_step
            h = abs(self.helical_visual_height)
        else:
            x = self.mesh_x_start < self.mesh_x_end and self.mesh_x_start or self.mesh_x_end
            y = self.mesh_y_start < self.mesh_y_end and self.mesh_y_start or self.mesh_y_end
            w = abs(self.mesh_width)
            h = abs(self.mesh_height)

        xy = [x,y]

        rect = Rectangle(xy, width=w, height=h, fill=False, alpha=0.4, color='red')
        mpl_axis.add_patch(rect)

    def show_spots(self, ax, spots, show_values=False):

        w=abs(self.mesh_x_step)
        h=abs(self.mesh_y_step)

        # create a colormap to be used
        min_spots = spots.min()
        max_spots = spots.max()

        nb_colors = int((max_spots-min_spots+1)*1)
        my_cmap = cm.get_cmap(self.mpl_colormap,nb_colors)

        # draw color rectangles for each spot

        for i in range(self.mesh_nb_lines):
            for j in range(self.mesh_img_per_line):
                coord = (i,j)
                xc = self.x_grid[coord] - abs(self.mesh_x_halfstep)
                yc = self.y_grid[coord] - abs(self.mesh_y_halfstep)

                val = int(spots[coord])
                xy = xc,yc
                spot_no = 1.0 - val / float(nb_colors)
                color = my_cmap(spot_no)
                p = Rectangle(xy,color=color,alpha=0.2,width=w, height=h)
                ax.add_patch(p)
                if show_values:
                   tx = xc - self.mesh_x_step - w/3.0
                   ty = yc + self.mesh_y_halfstep
                   ax.text(tx,ty,str(val), fontsize=7)

        # hightlight maximum value with an empty red rectangle
        index_max = np.unravel_index(spots.argmax(),spots.shape)

        val = spots[index_max]
        x = self.x_grid[index_max] - abs(self.mesh_x_halfstep)
        y = self.y_grid[index_max] - abs(self.mesh_y_halfstep)

        if val > self.minspots:
           w=abs(self.mesh_x_step)
           h=abs(self.mesh_y_step)
           p = Rectangle((x,y),color='red',fill=False,width=w, height=h)
           ax.add_patch(p)

    def show_center(self, ax):
        if self.x_best is None or self.y_best is None:
            return
        c = Circle((self.x_best, self.y_best), self.mesh_x_halfstep / 5.0, fill = True, color="green")
        ax.add_patch(c)

    def log_msg(self, msg, subsystem=None):
        if not self.log_file:
            return

        dtime = datetime.now()
        with open(self.log_file,'a') as fd:
            fd.write("%s - %s\n" % (str(dtime), msg))

    ##  dials end
