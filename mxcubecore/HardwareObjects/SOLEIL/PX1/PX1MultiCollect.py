from mxcubecore.BaseHardwareObjects import HardwareObject
from AbstractMultiCollect import *
import logging
import time
import os, copy
from PyTango import DeviceProxy

class TunableEnergy:
    @task
    def set_wavelength(self, wavelength):
        energy_obj = self.bl_control.energy
        return energy_obj.start_move_wavelength(wavelength)

    @task
    def set_energy(self, energy):
        energy_obj = self.bl_control.energy
        return energy_obj.start_move_energy(energy)

    def get_current_energy(self):
        return self.bl_control.energy.get_current_energy()

    def get_wavelength(self):
        return self.bl_control.energy.get_current_wavelength()

class PixelDetector:
    def __init__(self):
        self.shutterless = True
        self.new_acquisition = True
        self.shutterless_exptime = None
        self.shutterless_range = None

    @task
    def prepare_acquisition(self, take_dark, start, osc_range, exptime, npass, number_of_images, comment=""):
        self.new_acquisition = True
        self.cimg = self.collect_server.current_image_spi
        if osc_range < 0.0001:
            self.shutterless = False
        take_dark = 0
        if self.shutterless:
            self.shutterless_range = osc_range*number_of_images
            self.shutterless_exptime = (exptime + 0.003)*number_of_images
        logging.info("<PX1 MultiCollect> TODO - prepare acquisition")
        self.prepare_detector_header(take_dark, start, osc_range, exptime, npass, number_of_images, comment)

    def prepare_detector_header(self, take_dark, start, osc_range, exptime, npass, number_of_images, comment):
        # Setting MXSETTINGS for the cbf image headers
        ax, bx = self.bl_config.beam_ax, self.bl_config.beam_bx
        ay, by = self.bl_config.beam_ay, self.bl_config.beam_by
        dist = self.bl_control.detector_distance.get_position()
        wavlen = self.bl_control.energy.get_current_wavelength()
        kappa_angle = self.kappa_hwo.get_position()
        phi_angle = self.phi_hwo.get_position()
        chi_angle = self.chi_hwo.get_position()
        _settings = [
            ["Wavelength %.5f", wavlen],
            ["Detector_distance %.4f", dist/1000.],
            ["Beam_x %.2f", ax*dist + bx],
            ["Beam_y %.2f", ay*dist + by],
            ["Alpha %.2f", 49.64],
            ["Start_angle %.4f", start],
            ["Angle_increment %.4f", osc_range],
            ["Oscillation_axis %s", self.oscaxis],
            ["Detector_2theta %.4f", 0.0],
            ["Polarization %.3f", 0.990],
            ["Phi %.4f", phi_angle],
            ["Omega %.4f", start],
            ["Chi %.4f", chi_angle]]

        for _setting in _settings:
            str_set = (_setting[0] % _setting[1])
            logging.getLogger().info("MxSettings: " + str_set)
            self.pilatus_server.set_mx_settings(str_set)

    @task
    def set_detector_filenames(self, frame_number, start, filename, jpeg_full_path, jpeg_thumbnail_full_path):
        if self.shutterless and not self.new_acquisition:
            return
        basefile = os.path.basename(filename)
        dirname = os.path.dirname(filename)
        dirname = dirname.replace("/data1-1","/ramdisk")
        logging.info("<PX1 MultiCollect> Setting detector filenames")
        logging.info("     - frame_number: %s", frame_number)
        logging.info("     - start: %s", start)
        logging.info("     - filename: %s", basefile)
        logging.info("     - dirname: %s", dirname)
        logging.info("     - jpeg path: %s", jpeg_full_path)
        logging.info("     - thumb path: %s", jpeg_thumbnail_full_path)
        self.collect_server.image_name = basefile
        self.collect_server.image_path = dirname
        self.collect_server.prepare_collect()
        return

    @task
    def prepare_oscillation(self, start, osc_range, exptime, npass):
        if self.shutterless:
            if self.new_acquisition:
                logging.info("<PX1 MultiCollect> TODO - prepare oscillation new")
        else:
            if osc_range < 1E-4:
                # still image
                pass
            else:
                logging.info("<PX1 MultiCollect> TODO - prepare oscillation not new")
        return (start, start+osc_range)

    @task
    def start_acquisition(self, exptime, npass, first_frame):
        if not first_frame and self.shutterless:
            pass
        else:
            logging.info("<PX1 MultiCollect> TODO - start acquisition ")

    @task
    def do_oscillation(self, start, end, exptime, npass):
        if self.shutterless:
            if self.new_acquisition:
                # only do this once per collect
                npass = 1
                exptime = self.shutterless_exptime
                end = start + self.shutterless_range
                
                # make oscillation an asynchronous task => do not wait here
                logging.info("<PX1 MultiCollect> TODO - do oscillation new")
                self.collect_server.start()
                self.new_acquisition = False
                logging.getLogger("user_level_log").info("<PX1 MultiCollect> Collect server started waiting for first image")
                # wait for image number to change. normally to 0, first
                self.wait_nextimage()

            # wait for image number to change
            self.wait_nextimage()
        else:
            logging.info("<PX1 MultiCollect> TODO - do oscillation not new")

    def wait_nextimage(self):
        cimg = self.cimg
        while (cimg == self.cimg):
            if str(self.collect_server.state()) != "RUNNING":
                break
            time.sleep(0.02)
            cimg = self.collect_server.current_image_spi
        self.cimg = cimg
        logging.getLogger("user_level_log").info("<PX1 MultiCollect> end waiting for image number %s" % str(self.cimg))

    @task
    def write_image(self, last_frame):
        if last_frame:
            if self.shutterless:
                logging.info("<PX1 MultiCollect> TODO - write image ")

    def stop_acquisition(self):
        logging.info("<PX1 MultiCollect> stopping acquisition ")
        self.new_acquisition = False

    @task
    def reset_detector(self):
        if self.shutterless:
            self.stop_collect("mxCuBE")
        logging.info("<PX1 MultiCollect> TODO - reset detector ")

    def stop_collect(self, owner):
        logging.info("<PX1 MultiCollect> stopping ")
        if str(self.collect_server.state()) == "RUNNING":
            logging.info("<PX1 MultiCollect> stopping collect server ")
            self.collect_server.stop()
        AbstractMultiCollect.stop_collect(self, owner)

class PilatusDetector(PixelDetector):
    pass

class PX1MultiCollect(AbstractMultiCollect, HardwareObject):
    def __init__(self, name):
        AbstractMultiCollect.__init__(self)
        HardwareObject.__init__(self, name)
        self._detector = PilatusDetector()
        self._tunable_bl = TunableEnergy()
        self._centring_status = None

    def execute_command(self, command_name, *args, **kwargs):
        wait = kwargs.get("wait", True)
        cmd_obj = self.get_command_object(command_name)
        return cmd_obj(*args, wait=wait)

    def init(self):
        self.collect_server = DeviceProxy(self.get_property("collectname"))
        self.pilatus_server = DeviceProxy(self.get_property("pilatusname"))
        self.set_control_objects(
            diffractometer=self.get_object_by_role("diffractometer"),
            sample_changer=self.get_object_by_role("sample_changer"),
            lims=self.get_object_by_role("dbserver"),
            fast_shutter=self.get_object_by_role("fast_shutter"),
            safety_shutter=self.get_object_by_role("safety_shutter"),
            machine_current=self.get_object_by_role("machine_current"),
            cryo_stream=self.get_object_by_role("cryo_stream"),
            energy=self.get_object_by_role("energy"),
            resolution=self.get_object_by_role("resolution"),
            detector_distance=self.get_object_by_role("detector_distance"),
            transmission=self.get_object_by_role("transmission"),
            undulators=self.get_object_by_role("undulators"),
            flux=self.get_object_by_role("flux"))

        kappa_hwo = self.get_object_by_role("kappa")
        phi_hwo = self.get_object_by_role("phi")
        omega_hwo = self.get_object_by_role("omega")
        mxlocal_ho = self.get_object_by_role("beamline_configuration")
        bcm_pars = mxlocal_ho["BCM_PARS"]
        spec_pars = mxlocal_ho["SPEC_PARS"]

        try:
            undulators = bcm_pars["undulator"]
        except IndexError:
            undulators = []

        self.set_beamline_configuration(
            directory_prefix=self.get_property("directory_prefix"),
            default_exposure_time=bcm_pars.get_property("default_exposure_time"),
            default_number_of_passes=bcm_pars.get_property("default_number_of_passes"),
            maximum_radiation_exposure=bcm_pars.get_property("maximum_radiation_exposure"),
            nominal_beam_intensity=bcm_pars.get_property("nominal_beam_intensity"),
            minimum_exposure_time=bcm_pars.get_property("minimum_exposure_time"),
            minimum_phi_speed=bcm_pars.get_property("minimum_phi_speed"),
            minimum_phi_oscillation=bcm_pars.get_property("minimum_phi_oscillation"),
            maximum_phi_speed=bcm_pars.get_property("maximum_phi_speed"),
            detector_fileext=bcm_pars.get_property("FileSuffix"),
            detector_type=bcm_pars["detector"].get_property("type"),
            detector_mode=spec_pars["detector"].get_property("binning"),
            detector_manufacturer=bcm_pars["detector"].get_property("manufacturer"),
            detector_model=bcm_pars["detector"].get_property("model"),
            detector_px=bcm_pars["detector"].get_property("px"),
            detector_py=bcm_pars["detector"].get_property("py"),
            beam_ax=spec_pars["beam"].get_property("ax"),
            beam_ay=spec_pars["beam"].get_property("ay"),
            beam_bx=spec_pars["beam"].get_property("bx"),
            beam_by=spec_pars["beam"].get_property("by"),
            undulators=undulators,
            focusing_optic=bcm_pars.get_property('focusing_optic'),
            monochromator_type=bcm_pars.get_property('monochromator'),
            beam_divergence_vertical=bcm_pars.get_property('beam_divergence_vertical'),
            beam_divergence_horizontal=bcm_pars.get_property('beam_divergence_horizontal'),
            polarisation=bcm_pars.get_property('polarisation'),
            auto_processing_server=None,
            input_files_server=None)

        self.oscaxis = self.get_property("oscaxis")
        self._detector.collect_server = self.collect_server
        self._detector.pilatus_server = self.pilatus_server
        self._detector.bl_control = self.bl_control
        self._detector.bl_config = self.bl_config
        self._detector.kappa_hwo = kappa_hwo
        self._detector.phi_hwo = phi_hwo
        self._detector.omega_hwo = omega_hwo
        self._detector.oscaxis = self.oscaxis

        self._tunable_bl.bl_control = self.bl_control
        self.emit("collect_connected", (True,))
        self.emit("collect_ready", (True,))

    @task
    def take_crystal_snapshots(self):
        self.bl_control.diffractometer.take_snapshots(wait=True)

    @task
    def data_collection_hook(self, data_collect_parameters):
        self.dcpars = copy.copy(data_collect_parameters)
        return

    @task
    def set_transmission(self, transmission_percent):
        self.bl_control.transmission.set_transmission(transmission_percent)

    def set_wavelength(self, wavelength):
        return self._tunable_bl.set_wavelength(wavelength)

    def set_energy(self, energy):
        return self._tunable_bl.set_energy(energy)

    @task
    def set_resolution(self, new_resolution):
        return self.bl_control.resolution.move(new_resolution)

    @task
    def move_detector(self, detector_distance):
        logging.info("<PX1 MultiCollect> TEST - move detector")
        self.bl_control.detector_distance = detector_distance
        return

    @task
    def data_collection_cleanup(self):
        self.close_fast_shutter()

    @task 
    def close_fast_shutter(self):
        logging.info("<PX1 MultiCollect> close fast shutter ")
        self.bl_control.fast_shutter.close_shutter()
        t0 = time.time()
        while self.bl_control.fast_shutter.get_shutter_state() != 'closed':
            time.sleep(0.1)
            if (time.time() - t0) > 4:
                logging.getLogger("HWR").error("Timeout on closing fast shutter")
                break


    @task
    def open_fast_shutter(self):
        logging.info("<PX1 MultiCollect> open fast shutter ")
        self.bl_control.fast_shutter.open_shutter()
        t0 = time.time()
        while self.bl_control.fast_shutter.get_shutter_state() == 'closed':
            time.sleep(0.1)
            if (time.time() - t0) > 4:
                logging.getLogger("HWR").error("Timeout on opening fast shutter")
                break

    @task
    def move_motors(self, motor_position_dict):
        for motor in motor_position_dict.keys():
            position = motor_position_dict[motor]
            logging.getLogger().info("PX1 MultiCollect / move_motors: %s to %s " % (motor, position))
            if isinstance(motor, str):
                motor_role = motor
                motor = self.bl_control.diffractometer.get_device_by_role(motor_role)
                del motor_position_dict[motor_role]
                if motor is None:
                    continue
                motor_position_dict[motor] = position

            logging.getLogger("HWR").info("Moving motor '%s' to %f", motor.get_motor_mnemonic(), position)
            motor.move(position)

        while any([motor.motor_is_moving() for motor in motor_position_dict.iterkeys()]):
            logging.getLogger("HWR").info("Waiting for end of motors motion")
            time.sleep(0.5)

    @task
    def open_safety_shutter(self):
        self.bl_control.safety_shutter.open_shutter()
        t0 = time.time()
        while self.bl_control.safety_shutter.get_shutter_state() != 'opened':
            time.sleep(0.1)
            if (time.time() - t0) > 4:
                logging.getLogger("HWR").error("Timeout on opening safety shutter")
                break

    def safety_shutter_opened(self):
        return self.bl_control.safety_shutter.get_shutter_state() == "opened"

    @task
    def close_safety_shutter(self):
        self.bl_control.safety_shutter.close_shutter()
        t0 = time.time()
        while self.bl_control.safety_shutter.get_shutter_state() == 'opened':
            time.sleep(0.1)
            if (time.time() - t0) > 4:
                logging.getLogger("HWR").error("Timeout on closing safety shutter")
                break

    @task
    def prepare_intensity_monitors(self):
        logging.info("<PX1 MultiCollect> TODO - prepare intensity monitors")

    def prepare_acquisition(self, take_dark, start, osc_range, exptime, npass, number_of_images, comment=""):
        self.collect_server.exposure_period = exptime
        self.collect_server.number_of_images = number_of_images
        self.collect_server.image_width = osc_range
        self.collect_server.collect_axis = self.oscaxis
        self.collect_server.start_angle = start
        self.collect_server.trigger_mode = 2

        self.bl_control.diffractometer.prepare_for_acquisition()

        return self._detector.prepare_acquisition(take_dark, start, osc_range, exptime, npass, number_of_images, comment)

    def set_detector_filenames(self, frame_number, start, filename, jpeg_full_path, jpeg_thumbnail_full_path):
        return self._detector.set_detector_filenames(frame_number, start, filename, jpeg_full_path, jpeg_thumbnail_full_path)

    def prepare_oscillation(self, start, osc_range, exptime, npass):
        return self._detector.prepare_oscillation(start, osc_range, exptime, npass)

    def do_oscillation(self, start, end, exptime, npass):
        return self._detector.do_oscillation(start, end, exptime, npass)
    
    def start_acquisition(self, exptime, npass, first_frame):
        return self._detector.start_acquisition(exptime, npass, first_frame)
      
    def write_image(self, last_frame):
        return self._detector.write_image(last_frame)

    def stop_acquisition(self):
        return self._detector.stop_acquisition()
        
    @task
    def finalize_acquisition(self):
        logging.info("<PX1 MultiCollect> TODO - finalize acquisition")
        return

    def reset_detector(self):
        return self._detector.reset_detector()

    def prepare_input_files(self, files_directory, prefix, run_number, process_directory):
        return ("/tmp", "/tmp", "/tmp")

    @task
    def write_input_files(self, collection_id):
        pass

    def get_wavelength(self):
        return self._tunable_bl.get_wavelength()
      
    def get_detector_distance(self):
        logging.info("<PX1 MultiCollect> TODO - get detector distance")
        return
       
    def get_resolution(self):
        return self.bl_control.resolution.get_position()

    def get_transmission(self):
        return self.bl_control.transmission.get_att_factor()

    def get_undulators_gaps(self):
        all_gaps = {'Unknown': None}
        _gaps = {}
        try:
            _gaps = self.bl_control.undulators.get_undulator_gaps()
        except:
            logging.getLogger("HWR").exception("Could not get undulator gaps")
        all_gaps.clear()
        for key in _gaps:
            if '_Position' in key:
                nkey = key[:-9]
                all_gaps[nkey] = _gaps[key]
            else:
                all_gaps = _gaps
        return all_gaps

    def get_resolution_at_corner(self):
        logging.info("<PX2 MultiCollect> TODO - get resolution at corner")
        return

    def get_beam_size(self):
        logging.info("<PX2 MultiCollect> TODO - get beam size")
        return (None, None)

    def get_slit_gaps(self):
        logging.info("<PX2 MultiCollect> TODO - get slit gaps")
        return 

    def get_beam_shape(self):
        logging.info("<PX2 MultiCollect> TODO - get beam shape")
        return 
    
    def get_measured_intensity(self):
        logging.info("<PX2 MultiCollect> TODO - get measured intensity")
        try:
            val = self.get_channel_object("image_intensity").get_value()
            return float(val)
        except:
            return 0

    def get_machine_current(self):
        if self.bl_control.machine_current:
            return self.bl_control.machine_current.get_current()
        else:
            return 0

    def get_machine_message(self):
        if self.bl_control.machine_current:
            return self.bl_control.machine_current.get_message()
        else:
            return ''

    def get_machine_fill_mode(self):
        if self.bl_control.machine_current:
            return self.bl_control.machine_current.get_fill_mode()
        else:
            return ''

    def get_cryo_temperature(self):
        logging.info("<PX2 MultiCollect> TODO - get cryo temperature")
        return

    def get_current_energy(self):
        return self._tunable_bl.get_current_energy()

    def get_beam_centre(self):
        logging.info("<PX2 MultiCollect> TODO - get beam centre")
        return
    
    def get_beamline_configuration(self, *args):
        return self.bl_config._asdict()

    def is_connected(self):
        return True

    def is_ready(self):
        return True
 
    def sample_changer_ho(self):
        return self.bl_control.sample_changer

    def diffractometer(self):
        return self.bl_control.diffractometer

    def db_server_ho(self):
        return self.bl_control.lims

    def sanity_check(self, collect_params):
        return
    
    def set_brick(self, brick):
        return

    def directory_prefix(self):
        return self.bl_config.directory_prefix

    def store_image_in_lims(self, frame, first_frame, last_frame):
        if isinstance(self._detector, PixelDetector):
            if first_frame or last_frame:
                return True

    def get_flux(self):
        logging.info("<PX1 MultiCollect> TODO - get flux")
        return 10e12

    def get_oscillation(self, oscillation_id):
        return self.oscillations_history[oscillation_id - 1]
       
    def sample_accept_centring(self, accepted, centring_status):
        self.sample_centring_done(accepted, centring_status)

    def set_centring_status(self, centring_status):
        self._centring_status = centring_status

    def get_oscillations(self, session_id):
        return []

    def set_helical(self, onoff):
        logging.getLogger().info("<PX1 MultiCollect> TODO - set_helical (%s)" % str(onoff))
        return 

    def get_archive_directory(self, directory):
        logging.getLogger().info("<PX1 MultiCollect> TODO - get archive directory (using /tmp for now)")
        return "/tmp"