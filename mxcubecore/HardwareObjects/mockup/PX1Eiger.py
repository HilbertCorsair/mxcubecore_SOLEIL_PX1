#
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
[Name] : PX1Eiger(Equipment)

[Description] : Describes specific detector for SOLEIL/PROXIMA-1

[Channels]
-------------------------------------------------------------------------------
| name                       | initiated at
-------------------------------------------------------------------------------
| state_chan                 | init()
-------------------------------------------------------------------------------
| threshold_chan             | get_threshold()
-------------------------------------------------------------------------------
| beam_x_chan                | get_beam_centre()
-------------------------------------------------------------------------------
| beam_y_chan                | get_beam_centre()
-------------------------------------------------------------------------------
| flatfield_correction_chan  | get_flatfield_correction()
-------------------------------------------------------------------------------
| pixel_mask_chan            | get_pixel_mask()
-------------------------------------------------------------------------------
| managed_mode_chan          | get_managed_mode()
-------------------------------------------------------------------------------
| data_collection_date_chan  | get_data_collection_date()
-------------------------------------------------------------------------------
| photon_energy_chan         | get_photon_energy()
-------------------------------------------------------------------------------
| wavelength_chan            | get_wavelength()
-------------------------------------------------------------------------------
| roi_mode_chan              | get_roi_mode()
-------------------------------------------------------------------------------
| detector_distance_chan     | get_detector_distance()
-------------------------------------------------------------------------------
| detector_readout_time_chan | get_detector_readout_time()
-------------------------------------------------------------------------------
| temperature_chan           | get_temperature()
-------------------------------------------------------------------------------
| humidity_chan              | get_humidity()
-------------------------------------------------------------------------------
| chi_increment_chan         | get_chi_increment()
-------------------------------------------------------------------------------
| chi_start_chan             | get_chi_start()
-------------------------------------------------------------------------------
| kappa_increment_chan       | get_kappa_increment()
-------------------------------------------------------------------------------
| kappa_start_chan           | get_kappa_start()
-------------------------------------------------------------------------------
| omega_increment_chan       | get_omega_increment()
-------------------------------------------------------------------------------
| omega_start_chan           | get_omega_start()
-------------------------------------------------------------------------------
| phi_increment_chan         | get_phi_increment()
-------------------------------------------------------------------------------
| phi_start_chan             | get_phi_start()
-------------------------------------------------------------------------------

[Commands] :
No command is called within this specific HWO

[Emited signals] :
No signal is emited within this specific HWO

[Properties]
-------------------------------------------------------------------------------
| name                   | reported value
-------------------------------------------------------------------------------
| exposure_limits        | exposure time limits of the detector
-------------------------------------------------------------------------------
| manufacturer           | manufacturer's name (i.e. ADSC, Dectris...)
-------------------------------------------------------------------------------
| type                   | detector type (i.e. Pilatus, Eiger...)
-------------------------------------------------------------------------------
| model                  | detector model (i.e. 9M, 16M...)
-------------------------------------------------------------------------------
| default_exposure_time  | default exposure time for data collection in seconds
-------------------------------------------------------------------------------
| file_suffix            | image file format generated (i.e. cbg, h5...)
-------------------------------------------------------------------------------
| px                     | pixel size in the x direction in millimeters
-------------------------------------------------------------------------------
| py                     | pixel size in the y direction in millimeters
-------------------------------------------------------------------------------
| default_roi_mode       | default ROI mode of the detector (disabled, 4M)
-------------------------------------------------------------------------------

[Hardware Objects]      
-------------------------------------------------------------------------------
| name                       | signals             | functions
|------------------------------------------------------------------------------
| self.distance_motor_hwobj  |                     | getPosition()
|                            |                     | getLimits()
-------------------------------------------------------------------------------
"""
import logging
import time

from mxcubecore.HardwareObjects.abstract.AbstractDetector import AbstractDetector

from mxcubecore.BaseHardwareObjects import HardwareObject

__author__ = "Proxima1"
__credits__ = ["SOLEIL"]
__version__ = "2.3."
__category__ = "General"

class PX1Eiger(AbstractDetector, HardwareObject):
    """
    Descript. : Detector class. Contains all information about detector
                - states are 'OK', and 'BAD'
                - status is busy, exposing, ready, etc.
                - physical property is RH for pilatus, P for rayonix
    """

    def __init__(self, name):
        """
        Descript. : __init__ method

                  :param name: name of the object

                  :type  name: string
        """
        AbstractDetector.__init__(self)
        HardwareObject.__init__(self, name)

        self.distance_motor_hwobj = None
        self.default_distance = None
        self.default_distance_limits = None

        self.exp_time_limits = None

        self.headers = {}

    def init(self):
        """
        Descript. : Init method
        """
        self.distance_motor_hwobj = self.get_object_by_role("distance_motor")

        self.state_chan = self.get_channel_object("state")

        exp_time_limits = self.get_property("exposure_limits")
        self.exp_time_limits = map(float, exp_time_limits.strip().split(","))

    def state_changed(self, state):
        """
        Descript. : Reports changes in the state value
                    Function skipped and executed from the HWO Collect

                  :param state: state of the device serveur

                  :type  state: string
        """
        pass

    def photon_energy_changed(self, photon_energy):
        """
        Descript. : Reports changes in the photon energy value
                    Function skipped since the Eiger and executed from the HWO Collect

                  :param photon_energy: calibrated photon energy in eV

                  :type  photon_energy: string
        """
        pass

    def prepare_acquisition(self):
        """
        Descript. : Prepares the acquisition
                    Function skipped and executed from the HWO Collect
        """
        pass

    def start_acquisition(self):
        """
        Descript. : Starts the acquisition
                    Function skipped and executed from the HWO Collect
        """
        pass

    def stop_acquisition(self):
        """
        Descript. : Stops the acquisition
                    Function skipped and executed from the HWO Collect
        """
        pass

    def get_distance(self):
        """
        Descript. : Returns detector distance in mm
        """
        distance = self.default_distance
        try:
            if self.distance_motor_hwobj is not None:
                distance = self.distance_motor_hwobj.get_position()

        except:
            pass

        return distance

    def move_distance(self, value):
        """
        Descript. : Moves the detector distance.
                    Function skipped since the installation of the Eiger.

                  :param value: distance where to send the detector in mm

                  :type  value: string
        """
        pass

    def get_distance_limits(self):
        """
        Descript. : Returns detector distance limits in mm
        """
        if self.distance_motor_hwobj is not None:
            return self.distance_motor_hwobj.get_limits()
        else:
            return self.default_distance_limits

    def get_state(self):
        """
        Descript. : Returns state of the detector device
        """
        return self.state_chan.get_value()

    def read_state(self):
        """
        Descript. : Returns state of the detector device
        """
        return str(self.get_state())

    def is_fault_state(self):
        """
        Descript. : Returns boolean on the FAULT state of the device
        """
        return ( str(self.get_state()) == 'FAULT' )

    def get_threshold(self):
        """
        Descript. : Returns the value of the photon energy threshold
        """
        threshold = ''
        try:
            self.threshold_chan = self.get_channel_object("threshold")
            if self.threshold_chan is not None:
                threshold = self.threshold_chan.get_value()

        except:
            pass

        return threshold

    def get_threshold_gain(self):
        """
        Descript. : Returns the value of the threshold gain
                    Function skipped since the installation of the Eiger
        """
        pass

    def has_shutterless(self):
        """
        Descript. : Return True if has shutterless mode
        """
        return True

    def get_beam_centre(self):
        """
        Descript. : Returns beam center coordinates
        """
        beam_x = 0
        beam_y = 0

        try:
            self.beam_x_chan = self.get_channel_object("beam_x")
            if self.beam_x_chan is not None:
                beam_x = self.beam_x_chan.get_value()

            self.beam_y_chan = self.get_channel_object("beam_y")
            if self.beam_y_chan is not None:
                beam_y = self.beam_y_chan.get_value()

        except:
            pass

        return beam_x, beam_y

    def get_manufacturer(self):
        """
        Descript. : Returns the name of the detector manufacturer
        """
        manufacturer = ''
        try:
             man_property = self.get_property("manufacturer")
             if man_property is not None:
                manufacturer = man_property

        except:
            pass

        return manufacturer

    def get_model(self):
        """
        Descript. : Returns the model of the detector
        """
        model = ''
        try:
            model_property = self.get_property("model")
            if model_property is not None:
                model = model_property

        except:
            pass

        return model

    def get_detector_type(self):
        """
        Descript. : Returns the type of the detector
        """
        detector_type = ''
        try:
            detector_type_property = self.get_property("type")
            if detector_type_property is not None:
                detector_type = detector_type_property

        except:
            pass

        return detector_type

    def get_default_exposure_time(self):
        """
        Descript. : Returns the exposure time by default in sec
        """
        default_exp_time = ''
        try:
            default_exp_time_property = self.get_property("default_exposure_time")
            if default_exp_time_property is not None:
                default_exp_time = default_exp_time_property

        except:
            pass

        return default_exp_time

    def get_minimum_exposure_time(self):
        """
        Descript. : Returns the minimum exposure time possible
        """
        minim_exp_time = ''
        try:
            exp_time_limits = self.get_exposure_time_limits()
            if exp_time_limits is not None:
                minim_exp_time = exp_time_limits[0]

        except:
            pass

        return minim_exp_time

    def get_exposure_time_limits(self):
        """
        Descript. : Returns exposure time limits as list with two floats
        """
        return self.exp_time_limits

    def get_file_suffix(self):
        """
        Descript. : Returns the siffix of the images
        """
        file_suffix = ''
        try:
            file_suffix_property = self.get_property("file_suffix")
            if file_suffix_property is not None:
                file_suffix = file_suffix_property

        except:
            pass

        return file_suffix

    def get_pixel_size(self):
        """
        Descript. : Returns the values for the pixel sizes
        """
        px = ''
        py = ''
        try:
            px_property = self.get_property("px")
            if px_property is not None:
                px = px_property

            py_property = self.get_property("py")
            if py_property is not None:
                py = py_property

        except:
            pass

        return px, py

    def get_flatfield_correction(self):
        """
        Descript. : Returns the value for the attribute flatfieldCorrection
        """
        flatfield_correction = ''
        try:
            flatfield_correction_chan = self.get_channel_object("flatfield_correction")
            if flatfield_correction_chan is not None:
                flatfield_correction = flatfield_correction_chan.get_value()

        except:
            pass

        return flatfield_correction

    def get_pixel_mask(self):
        """
        Descript. : Returns the value for the attribute pixelMask
        """
        pixel_mask = ''
        try:
            pixel_mask_chan = self.get_channel_object("pixel_mask")
            if pixel_mask_chan is not None:
                pixel_mask = pixel_mask_chan.get_value()

        except:
            pass

        return pixel_mask

    def get_managed_mode(self):
        """
        Descript. : Returns the value for the attribute managedMode
        """
        managed_mode = ''
        try:
            managed_mode_chan = self.get_channel_object("managed_mode")
            if managed_mode_chan is not None:
                managed_mode = managed_mode_chan.get_value()

        except:
            pass

        return managed_mode

    def get_data_collection_date(self):
        """
        Descript. : Returns the data of the latest data collection
        """
        data_collection_date = ''
        try :
            data_collection_date_chan = self.get_channel_object("data_collection_date")
            if data_collection_date_chan is not None:
                data_collection_date = data_collection_date_chan.get_value()

        except:
            pass

        return data_collection_date

    def get_photon_energy(self):
        """
        Descript. : Returns the value for the attribute photon energy
        """
        photon_energy = ''
        try:
            photon_energy_chan = self.get_channel_object("photon_energy")
            if photon_energy_chan is not None:
                photon_energy = photon_energy_chan.get_value()

        except:
            pass

        return photon_energy

    def get_wavelength(self):
        """
        Descript. : Returns the value for the attribute wavelength
        """
        wavelength = ''
        try:
            wavelength_chan = self.get_channel_object("wavelength")
            if wavelength_chan is not None:
                wavelength = wavelength_chan.get_value()

        except:
            pass

        return wavelength

    def get_roi_mode(self):
        """
        Descript. : Returns the ROI mode
        """
        roi_mode = ''
        try:
            roi_mode_chan = self.get_channel_object("roi_mode")
            if roi_mode_chan is not None:
                roi_mode = roi_mode_chan.get_value()

        except:
            pass

        return roi_mode

    def get_default_roi_mode(self):
        """
        Descript. : Returns the default ROI mode of the detector
                    Possibilities are: disabled
                                       4M
        """
        default_roi_mode = ''
        try:
            default_roi_mode_property = self.get_property("default_roi_mode")
            if default_roi_mode_property is not None:
                default_roi_mode = default_roi_mode_property

        except:
            pass

        return default_roi_mode
        
    def get_detector_distance(self):
        """
        Descript. : Returns the value for the attribute detectorDistance
        """
        detector_distance = ''
        try:
            detector_distance_chan = self.get_channel_object("detector_distance")
            if detector_distance_chan is not None:
                detector_distance = detector_distance_chan.get_value()

        except:
            pass

        return detector_distance

    def get_detector_readout_time(self):
        """
        Descript. : Returns the value for the attribute detectorReadoutTime
        """
        detector_readout_time = ''
        try:
            detector_readout_time_chan = self.get_channel_object("detector_readout_time")
            if detector_readout_time_chan is not None:
                detector_readout_time = detector_readout_time_chan.get_value()

        except:
            pass

        return detector_readout_time

    def get_temperature(self):
        """
        Descript. : Returns the value for the attribute temperature
        """
        temperature = ''
        try:
            temperature_chan = self.get_channel_object("temperature")
            if temperature_chan is not None:
                temperature = temperature_chan.get_value()

        except:
            pass

        return temperature

    def get_humidity(self):
        """
        Descript. : Returns the value for the attribute humidity
        """
        humidity = ''
        try:
            humidity_chan = self.get_channel_object("humidity")
            if humidity_chan is not None:
                humidity = humidity_chan.get_value()

        except:
            pass

        return humidity

    def get_chi_increment(self):
        """
        Descript. : Returns the value for the attribute chiIncrement
        """
        chi_increment = ''
        try:
            chi_increment_chan = self.get_channel_object("chi_increment")
            if chi_increment_chan is not None:
                chi_increment = chi_increment_chan.get_value()

        except:
            pass

        return chi_increment

    def get_chi_start(self):
        """
        Descript. : Returns the value for the attribute chiStart
        """
        chi_start = ''
        try:
            chi_start_chan = self.get_channel_object("chi_start")
            if chi_start_chan is not None:
                chi_start = chi_start_chan.get_value()

        except:
            pass

        return chi_start

    def get_kappa_increment(self):
        """
        Descript. : Returns the value for the attribute kappaIncrement
        """
        kappa_increment = ''
        try:
            kappa_increment_chan = self.get_channel_object("kappa_increment")
            if kappa_increment_chan is not None:
                kappa_increment = kappa_increment_chan.get_value()

        except:
            pass

        return kappa_increment

    def get_kappa_start(self):
        """
        Descript. : Returns the value for the attribute kappaStart
        """
        kappa_start = ''
        try:
            kappa_start_chan = self.get_channel_object("kappa_start")
            if kappa_start_chan is not None:
                kappa_start = kappa_start_chan.get_value()

        except:
            pass

        return kappa_start

    def get_omega_increment(self):
        """
        Descript. : Returns the value for the attribute omegaIncrement
        """
        omega_increment = ''
        try:
            omega_increment_chan = self.get_channel_object("omega_increment")
            if omega_increment_chan is not None:
                omega_increment = omega_increment_chan.get_value()

        except:
            pass

        return omega_increment

    def get_omega_start(self):
        """
        Descript. : Returns the value for the attribute omegaStart
        """
        omega_start = ''
        try:
            omega_start_chan = self.get_channel_object("omega_start")
            if omega_start_chan is not None:
                omega_start = omega_start_chan.get_value()

        except:
            pass

        return omega_start

    def get_phi_increment(self):
        """
        Descript. : Returns the value for the attribute phiIncrement
        """
        phi_increment = ''
        try:
            phi_increment_chan = self.get_channel_object("phi_increment")
            if phi_increment_chan is not None:
                phi_increment = phi_increment_chan.get_value()

        except:
            pass

        return phi_increment

    def get_phi_start(self):
        """
        Descript. : Returns the value for the attribute phiStart
        """
        phi_start = ''
        try:
            phi_start_chan = self.get_channel_object("phi_start")
            if phi_start_chan is not None:
                phi_start = phi_start_chan.get_value()

        except:
            pass

        return phi_start

    def prepare_collection(self, dcpars):
        """
        Descript. : Prepare for data collection and calibrate detector

                  :param dcpars: list of data collection parameters

                  :type  dcpars: list
        """
        return True

    def start_collection(self):
        """
        Descript. : Starts data collection
                    Function skipped since the installation of the Eiger
        """
        pass

    def stop_collection(self):
        """
        Descript. : Stops data collection
                    Function skipped since the installation of the Eiger
        """
        pass

    def set_image_headers(self, image_headers):
        """
        Descript. : Set the image header
                    Function skipped since the installation of the Eiger

                  :param image_headers: list of parameters to send in image header

                  :type  image_headers: list
        """
        pass

    def do_energy_calibration(self, energy):
        """
        Descript. : Performs the energy calibration of the detector
                    Function skipped since the installation of the Eiger

                  :param energy: energy at which to calibrate the detector in eV

                  :type  energy: string
        """
        pass

    def wait_energy_calibration(self):
        """
        Descript. : Waits for the energy calibration of the detector to be done
                    Function skipped since the installation of the Eiger
        """
        pass
'''
def test_hwo(hwo):
    
    Descript. : PX1Eiger.py HardwareObject, test module
   
    print '\n======================== TESTS ========================'
    print 'These are tests of the HardwareObject PX1Eiger'
    print '\n[IMPORTANT] to note: the following functions are turned'
    print '  off by default'
    print ' - prepare_acquisition'
    print ' - start_acquisition'
    print ' - stop_acquisition'
    print ' - move_distance'
    print ' - state_changed'
    print ' - get_threshold_gain'
    print ' - do_energy_calibration'
    print ' - wait_energy_calibration'
    print ' - start_collection'
    print ' - stop_collection'
    print ' - set_image_headers'
    print '=======================================================\n'

    print '-------------------------------------------------------'
    print 'Defining some generic variables for all the tests'

    print '-------------------------------------------------------'

    print '\n-------------------------------------------------------'
    print 'Checking for the connection to Eiger.'
    print '-------------------------------------------------------'
    from HardwareRepository import HardwareRepository
    import os
    hwr_directory = os.environ["MXCUBE_XML_PATH"]
    hwr = HardwareRepository.HardwareRepository(os.path.abspath(hwr_directory))
    hwr.connect()
    eiger = hwr.getHardwareObject("/eiger")

    print '\nVariables and attributes digged out of Eiger HWO:'
    exp_time_limits = eiger.exp_time_limits
    default_distance = eiger.default_distance
    default_distance_limits = eiger.default_distance_limits
    print '  exposure time limits    : %s' % exp_time_limits
    print '  default distance        : %s' % default_distance
    print '  default distance limits : %s' % default_distance_limits

    print '\n-------------------------------------------------------'
    print 'Checking for the state of the device'
    print 'This includes functions:'
    print '   - get_state'
    print '   - read_state'
    print '   - is_fault_state'
    print '-------------------------------------------------------'
    state = eiger.read_state()
    fault_state = eiger.is_fault_state()
    print 'State of the device : %s' % state
    print 'Device in fault     : %s' % fault_state

    print '\n-------------------------------------------------------'
    print 'Checking for the detector distance and limits'
    print '-------------------------------------------------------'
    distance = eiger.get_distance()
    distance_limits = eiger.get_distance_limits()
    print 'Detector distance (mm)        : %s' % distance
    print 'Detector distance limits (mm) : %s' % distance_limits

    print '\n-------------------------------------------------------'
    print 'Checking for the beam center coordinates'
    print '-------------------------------------------------------'
    beam_x, beam_y = eiger.get_beam_centre()
    print 'Beam center coordinates x : %s' % beam_x
    print '                        y : %s' % beam_y

    print '\n-------------------------------------------------------'
    print 'Checking for the detector manufacturing specs'
    print 'This includes functions:'
    print '   - get_manufacturer'
    print '   - get_model'
    print '   - get_detector_type'
    print '   - get_file_suffix'
    print '   - get_pixel_size'
    print '-------------------------------------------------------'
    manufacturer = eiger.get_manufacturer()
    model = eiger.get_model()
    detector_type = eiger.get_detector_type()
    file_suffix = eiger.get_file_suffix()
    px, py = eiger.get_pixel_size()
    default_roi_mode = eiger.get_default_roi_mode()
    print 'Detector manufacturer : %s' % manufacturer
    print '         type         : %s' % detector_type
    print '         model        : %s' % model
    print '         file suffix  : %s' % file_suffix
    print '         pixel size x : %s mm' % px
    print '                    y : %s mm' % py
    print '         default ROI  : %s' % default_roi_mode

    print '\n-------------------------------------------------------'
    print 'Checking for some default properties vs set properties'
    print 'This includes functions:'
    print '   - get_default_exposure_time'
    print '   - get_minimum_exposure_time'
    print '   - get_exposure_time_limits'
    print '-------------------------------------------------------'
    default_exp_time = eiger.get_default_exposure_time()
    minim_exp_time = eiger.get_minimum_exposure_time()
    print 'Exposure time default : %s sec' % default_exp_time
    print '              minimum : %s sec' % minim_exp_time

    print '\n-------------------------------------------------------'
    print 'Checking for the prepare_collecton function'
    print '-------------------------------------------------------'
    prepare_collect = eiger.prepare_collection('')
    print 'Prepare collection : %s' % prepare_collect

    print '\n-------------------------------------------------------'
    print 'Lists up all the attribute values within the device'
    print 'This includes functions:'
    print '   - get_threshold'
    print '   - get_flatfield_correction'
    print '   - get_pixel_mask'
    print '   - get_managed_mode'
    print '   - get_data_collection_date'
    print '   - get_photon_energy'
    print '   - get_wavelength'
    print '   - get_roi_mode'
    print '   - get_default_roi_mode'
    print '   - get_detector_distance'
    print '   - get_detector_readout_time'
    print '   - get_temperature'
    print '   - get_humidity'
    print '   - get_chi_increment'
    print '   - get_chi_start'
    print '   - get_kappa_increment'
    print '   - get_kappa_start'
    print '   - get_omega_increment'
    print '   - get_omega_start'
    print '   - get_phi_increment'
    print '   - get_phi_start'
    print '-------------------------------------------------------'
    flatfield_correction = eiger.get_flatfield_correction()
    pixel_mask = eiger.get_pixel_mask()
    managed_mode = eiger.get_managed_mode()
    data_collection_date = eiger.get_data_collection_date()
    threshold = eiger.get_threshold()
    photon_energy = eiger.get_photon_energy()
    wavelength = eiger.get_wavelength()
    roi_mode = eiger.get_roi_mode()
    detector_distance = eiger.get_detector_distance()
    detector_readout_time = eiger.get_detector_readout_time()
    temperature = eiger.get_temperature()
    humidity = eiger.get_humidity()
    chi_increment = eiger.get_chi_increment()
    chi_start = eiger.get_chi_start()
    kappa_increment = eiger.get_kappa_increment()
    kappa_start = eiger.get_kappa_start()
    omega_increment = eiger.get_omega_increment()
    omega_start = eiger.get_omega_start()
    phi_increment = eiger.get_phi_increment()
    phi_start = eiger.get_phi_start()
    print 'Flatfield correction    : %s' % flatfield_correction
    print 'Pixel mask              : %s' % pixel_mask
    print 'Managed mode            : %s' % managed_mode
    print 'Data collection date    : %s' % data_collection_date
    print 'Threshold               : %s eV' % threshold
    print 'Photon energy           : %s eV' % photon_energy
    print 'Wavelength              : %s A'  % wavelength
    print 'ROI mode                : %s' % roi_mode
    print 'Beam X                  : %s' % beam_x
    print 'Beam Y                  : %s' % beam_y
    print 'Detector distance       : %s m' % detector_distance
    print 'Detector readout time   : %s s' % detector_readout_time
    print 'Temperature             : %s C' % temperature
    print 'Humidity                : %s' % humidity
    print 'Chi   increment / start : %s / %s' % (chi_increment, chi_start)
    print 'Kappa increment / start : %s / %s' % (kappa_increment, kappa_start)
    print 'Omega increment / start : %s / %s' % (omega_increment, omega_start)
    print 'Phi   increment / start : %s / %s' % (phi_increment, phi_start)

    print ''

if __name__ == '__main__':
    test_hwo('test')
 '''