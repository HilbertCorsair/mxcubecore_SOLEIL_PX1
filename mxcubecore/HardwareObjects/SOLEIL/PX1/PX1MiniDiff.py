
import logging
import gevent
import time
import os
import sys
import simplejpeg
import cv2
import datetime
from redis_camera import camera
from imageio import imwrite

from mxcubecore.HardwareObjects.GenericDiffractometer import (
    GenericDiffractometer,
)
#from mxcubecore.HardwareObjects import sample_centring

from mxcubecore import HardwareRepository as HWR
from mxcubecore.HardwareObjects import sample_centring
import numpy as np
import math

log = logging.getLogger("HWR")

murko_path = os.getenv("MURKO_PATH")
sys.path.insert(1, murko_path)
from utils import (
    get_predictions,
    plot_analysis,
)


class PX1MiniDiff(GenericDiffractometer):
    def __init__(self, name):
        super().__init__(name)

        #Attribute that holsd the "ON" "OFF" state for the light UI button
        #The button has in fact nothing to do with the light direcly
        #it should just chande the psase to VISU_SAMPLE when pressed and to DEFAULT

    default_arrow_step = 0.1   # 100 microns default for arrow movements. otherwise configure in zoom.xml with arrowStep

    grid_direction = {"fast": (1, 0),
                      "slow": (0, 1),
                      "omega_ref" : 0}

    def init(self):
        self.zoom = self.get_object_by_role("zoom")
        self.smargon = self.get_object_by_role("smargon")
        self.smargon_state = None
        self.connect(self.smargon, "stateChanged", self.smargon_state_changed)
        self.chip_mode = False
        self.light_arm = self.get_object_by_role('lightarm')
        self.px1conf_ho = self.get_object_by_role('px1configuration')
        self.px1env_ho = self.get_object_by_role('px1environment')
        self.pixels_per_mm_x = 0
        self.beam_x = None
        self.beam_y = None
        self.pixels_per_mm_y = 0
        self.arrow_step = self.default_arrow_step
        self.backlight = self.get_object_by_role("backlight")
        self.update_zoom_calibration()

        GenericDiffractometer.init(self)

        self.phase_list = [
                GenericDiffractometer.PHASE_TRANSFER,
                GenericDiffractometer.PHASE_CENTRING,
                GenericDiffractometer.PHASE_COLLECTION,
                GenericDiffractometer.PHASE_DEFAULT,
                GenericDiffractometer.PHASE_UNKNOWN,
                GenericDiffractometer.PHASE_PARTY,]

        self.centring_methods = {
             GenericDiffractometer.CENTRING_METHOD_MANUAL: \
                 self.px1_manual_centring,
             GenericDiffractometer.CENTRING_METHOD_AUTO: \
                 self.px1_automatic_centring,
             GenericDiffractometer.CENTRING_METHOD_MOVE_TO_BEAM: \
                 self.start_move_to_beam}

    def is_murko_available(self):
        """
        Returns True if murko is available
        :returns: boolean
        """
        return True

    def px1_start(
        self,
        centring_motors_dict,
        pixelsPerMm_Hor,
        pixelsPerMm_Ver,
        beam_x,
        beam_y,
        chi_angle=0,
        n_points=2,
        phi_incr=120.0,
        sample_type="LOOP",
        automatic=False,
    ):

        global CURRENT_CENTRING

        phi, phiy, phiz, sampx, sampy = sample_centring.prepare(centring_motors_dict)

        CURRENT_CENTRING = gevent.spawn(
            self.px1_center,
            phi,
            phiy,
            phiz,
            sampx,
            sampy,
            pixelsPerMm_Hor,
            pixelsPerMm_Ver,
            beam_x,
            beam_y,
            chi_angle,
            n_points,
            phi_incr,
            sample_type,
            automatic=automatic,
        )
        return CURRENT_CENTRING

    def takePictureAnalysis(self, folder='MurkoImagingTest', path=None):
        """
        would be great to integrate InFine the different lighting conditions with the ringlight
        """
        pathing = "/home/experiences/proxima1/com-proxima1/arthur_mxcube/" + folder
        os.makedirs(pathing, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        img = None
        imgName = pathing + "/img_" + timestamp + ".jpg"
        try:
            redisCamera = camera()
            img = redisCamera.get_image()
            if path != None:
                imgName = path
                os.makedirs(os.path.dirname(path), exist_ok=True)
            imwrite(imgName, img)
            if path != None:
                og_image = cv2.imread(imgName)
                height, width = og_image.shape[:2]
                center_x, center_y = width // 2, height // 2
                size = 25
                color = (0, 0, 255)
                thickness = 2
                cv2.line(og_image, (center_x - size, center_y), (center_x + size, center_y), color, thickness)
                cv2.line(og_image, (center_x, center_y - size), (center_x, center_y + size), color, thickness)
                cv2.imwrite(imgName, og_image)
            log.debug("img saved as %s" %imgName)
        except Exception as e:
            logging.getLogger("user_level_log").info("%s" %e)
        return img, imgName

    def estimate_click_murko(self, frame, forceSquaredGrid=False, imgName=None, useInsideLoop=False):
        """Gets relative coordinates from murko

        Calls the murko server running on localhost:89011 to retrieve estimated
        relative coordinates of the crystal presence in the image given

        Args:
            frame: current frame to be analysed

        Returns:
            Relative coordinates (x, y) of the click a user would do.
            If there is a crystal this would be the center of the crystal
            If not it would be the center of the loop seen in the frame
            If nothing is found would be (0.5, 0.5) being center of the frame
        """

        if (frame is None):
            frame = '/home/experiences/proxima1/com-proxima1/arthur_mxcube/img_test.jpg'
            frame = HWR.beamline.sample_view.camera.get_last_image()
            HWR.beamline.sample_view.save_snapshot("/home/experiences/proxima1/com-proxima1/arthur_mxcube/image_testing.jpg")

        """
        TODO
        Add warning in case murko isn't up
        """

        request_args = {}
        request_args["to_predict"] = frame
        request_args["description"] = [
            "foreground",
            "crystal",
            "loop_inside",
            "loop",
            ["crystal", "loop"],
            ["crystal", "loop", "stem"],
        ]
        request_args["save"] = False
        request_args["prefix"] = "predicted"
        mhost = os.getenv("MURKO_HOST")
        mport = int(os.getenv("MURKO_PORT"))
        analysis = get_predictions(request_args, host=mhost, port = mport)
        descriptions = analysis['descriptions'][0]

        analysis_good = descriptions['present']
        y_center_aoi, x_center_aoi = -1, -1

        if analysis_good == 1:
            loop_present, y_center_aoi, x_center_aoi, height_aoi, width_aoi = descriptions["aoi_bbox"]
            if loop_present:
                log.debug(
                    "Loop found! Its bounding box parameters in fractional coordianates are: center (vertical %.3f, horizontal %.3f), height %.3f, width %.3f"
                    % (y_center_aoi, x_center_aoi, height_aoi, width_aoi)
                )
            else:
                log.debug("loop not found")

            y_center_click, x_center_click = descriptions["most_likely_click"]
            log.debug("Most likely click to be at [%.3f, %.3f]" % (y_center_click, x_center_click))

            if imgName and loop_present:
                # Preparing variables and image to save
                original_image = cv2.imread(imgName)
                original_height, original_width, _ = original_image.shape
                image_drawing_predictions = original_image.copy()
                sizePrediction = 320
                P = 1 # percentage augmentation or reduction of the predicted grid

                # Computations for inside loop prediction
                if useInsideLoop:
                    prediction_factor_height, prediction_factor_width = original_height / sizePrediction, original_width / sizePrediction
                    loop_inside = descriptions["loop_inside"]
                    y_center_loop, x_center_loop = loop_inside['r'] * prediction_factor_height, loop_inside['c'] * prediction_factor_width
                    height_loop, width_loop = loop_inside['h'] * prediction_factor_height, loop_inside['w'] * prediction_factor_width
                    loop_inside_not_found =  math.isnan(y_center_loop) or math.isnan(x_center_loop) or math.isnan(height_loop) or math.isnan(width_loop)
                    if not loop_inside_not_found:
                        y1_in, x1_in = int((y_center_loop - (height_loop / 2) * P)), int((x_center_loop - (width_loop / 2) * P))
                        y2_in, x2_in = int((y_center_loop + (height_loop / 2) * P)), int((x_center_loop + (width_loop / 2) * P))

                position_click = (int(x_center_click * original_width), int(y_center_click * original_height))
                cv2.circle(image_drawing_predictions, position_click, 5, (0, 255, 255), -1) # Yellow most_likely_click

                y1, x1 = int((y_center_aoi - (height_aoi / 2) * P) * original_height), int((x_center_aoi - (width_aoi / 2) * P) * original_width)
                y2, x2 = int((y_center_aoi + (height_aoi / 2) * P) * original_height), int((x_center_aoi + (width_aoi / 2) * P) * original_width)

                position_center_aoi = (int(x_center_aoi * original_width), int(y_center_aoi * original_height))
                cv2.rectangle(image_drawing_predictions, (x1, y1), (x2, y2), (255, 0, 0), 2) # Blue bbox
                if useInsideLoop and not loop_inside_not_found:
                    cv2.rectangle(image_drawing_predictions, (x1_in, y1_in), (x2_in, y2_in), (255, 0, 255), 2) # Blue bbox
                cv2.circle(image_drawing_predictions, position_center_aoi, 5, (0, 0, 255), -1) # Red center of bbox
                cv2.circle(image_drawing_predictions, (x1, y2), 5, (0, 255, 0), -1) # Green BottomLeft angle
                tmpName = imgName[:-4] + "_analysis.jpg"
                cv2.imwrite(tmpName, image_drawing_predictions)

        else:
            logging.getLogger("user_level_log").debug("nothing found on image, click on center")
            return 0.5, 0.5, 0.5, 0.5

        log.debug("Murko finished computing position for image")

        if forceSquaredGrid:
            return width_aoi, height_aoi, y_center_aoi, x_center_aoi
        elif useInsideLoop and not loop_inside_not_found:
            return width_loop, height_loop, y_center_click, x_center_click
        else:
            return width_aoi, height_aoi, y_center_click, x_center_click

    def px1_center_murko(self, X, Y, phi_positions, phi, n_points, PHI_ANGLE_START, phi_incr):
        """ Method to center the sample using the Neural Network murko

        WIP
        Take last frame, send to murko to get coordinates and add to positions list.
        This could be improved with multithreading instead of waiting for murko results
        to start moving the motors for the next position.

        Args:
            X: list of X coords where a click event happened, should be given
                to this method empty
            Y: list of Y coords where a click event happened, should be given
                to this method empty
            PHI: list of coords phi of the sample when a click event happened,
                should be given to this method empty
            phi: variable to control the movement in phi of the sample
            n_points: number of points to be taken by the user
            PHI_ANGLE_START: original position phi of the sample, used to
                return to original location after movements
        Returns:
            No return value, coordinates stored in X, Y, PHI

        """
        for i in range(n_points):
            img, imgName = self.takePictureAnalysis()
            _, _, y_click, x_click = self.estimate_click_murko(img, imgName=imgName)
            original_width, original_height = int(os.getenv("MURKO_SIZEX")), int(os.getenv("MURKO_SIZEY"))
            x_coord = x_click * original_width
            y_coord = y_click * original_height
            log.debug("Center found at [%s;%s]", x_coord, y_coord)
            X.append(x_coord)
            Y.append(y_coord)
            phi_positions.append(phi.get_position())
            phi.sync_move_relative(phi_incr)

        logging.getLogger("user_level_log").info(
            "returning PHI to initial position %s" % PHI_ANGLE_START
        )
        phi.move(PHI_ANGLE_START)

    def px1_center_user_input(self, X, Y, phi_positions, phi, n_points, PHI_ANGLE_START, phi_incr):
        """ Method to get the user inputs for the centring of the sample

        global USER_CLICKED_EVENT is assumed declared before calling this method

        Args:
            X: list of X coords where a click event happened, should be given
                to this method empty
            Y: list of Y coords where a click event happened, should be given
                to this method empty
            PHI: list of coords phi of the sample when a click event happened,
                should be given to this method empty
            phi: variable to control the movement in phi of the sample
            n_points: number of points to be taken by the user
            PHI_ANGLE_START: original position phi of the sample, used to
                return to original location after movements
        Returns:
            No return value, coordinates stored in X, Y, PHI

        """

        # OBTAIN CLICKS
        while True:
            USER_CLICKED_EVENT = gevent.event.AsyncResult()
            user_info = USER_CLICKED_EVENT.get()
            if user_info == "abort":
                sample_centring.bort_centring()
                return None
            else:
                x, y = user_info

            USER_CLICKED_EVENT = gevent.event.AsyncResult()

            X.append(x) # X needed later
            Y.append(y) # Y needed later
            phi_positions.append(phi.get_position()) # PHI needed later

            if len(X) == n_points:
                # PHI_LAST_ANGLE = phi.get_position()
                # GO_ANGLE_START = PHI_ANGLE_START - PHI_LAST_ANGLE
                sample_centring.READY_FOR_NEXT_POINT.set()
                # phi.sync_move_relative(GO_ANGLE_START)
                break

            phi.sync_move_relative(phi_incr)
            sample_centring.READY_FOR_NEXT_POINT.set()

        logging.getLogger("user_level_log").info(
            "returning PHI to initial position %s" % PHI_ANGLE_START
        )
        phi.move(PHI_ANGLE_START)

    def px1_center_computations(self, X, Y, beam_x, beam_y, phi_positions, PhiCamera, n_points):
        """ Method to compute the positions need to center the sample based on
            X and Y coordiantes

        Args:
            X: list of X coordinates to use for centring
            Y: list of Y coordinates to use for centring
            beam_x:
            beam_y:
            phi_positions: list of PHI positions to use for centring
            PhiCamera: phi value to add to correctly compute angle
            n_points: number of points taken as input
        Return:
            x_echantillon: resulting x coordinate to center sample to
            y_echantillon: resulting y coordinate to center sample to
            z_echantillon: resulting z coordinate to center sample to

        """

        P, Q, XB, YB, ANG = [], [], [], [], []

        log.debug("sample_centring: INPUT for calculation")
        log.debug(
            "sample_centring:   beam_x = %s, beam_y = %s " % (beam_x, beam_y)
        )
        log.debug(
            "sample_centring:   X = %s, Y = %s " % (str(X), str(Y))
        )
        log.debug(
            "sample_centring:   PHI = %s, PhiCamera = %s, n_points = %s "
            % (str(phi_positions), PhiCamera, n_points)
        )

        try:
            for i in range(n_points):
                xb = X[i] - beam_x
                yb = Y[i] - beam_y
                ang = math.radians(phi_positions[i] + PhiCamera)

                XB.append(xb)
                YB.append(yb)
                ANG.append(ang)

            for i in range(n_points):
                y0 = YB[i]
                a0 = ANG[i]
                if i < (n_points - 1):
                    y1 = YB[i + 1]
                    a1 = ANG[i + 1]
                else:
                    y1 = YB[0]
                    a1 = ANG[0]

                p = (y0 * math.sin(a1) - y1 * math.sin(a0)) / math.sin(a1 - a0)
                q = (y0 * math.cos(a1) - y1 * math.cos(a0)) / math.sin(a0 - a1)

                P.append(p)
                Q.append(q)

            x_echantillon = -sum(P) / n_points
            y_echantillon = sum(Q) / n_points
            z_echantillon = -sum(XB) / n_points
        except:
            import traceback

            log.info(
                "sample_centring: error while centring: %s"
                % traceback.format_exc()
            )

        return (x_echantillon, y_echantillon, z_echantillon)

    def px1_center_move_motors(self, echantillon, sample, pixelsPerMm_Hor, PHI_ANGLE_START, phi):
        """ Method to move motors given a certain set of coordinates

        Args:
            echantillon: (x_echantillon, y_echantillon, z_echantillon)
                positions computed for the sample
            sample: (sampx, sampy, phiy)
                hwo to get current position of sample
            pixelsPerMm_Hor: conversion rate from computed position on screen
                to real world movement
            PHI_ANGLE_START: phi angle before moving the sample around to find
                center
        Returns:
            New position, which would be a centred position for the sample

        """

        (x_echantillon, y_echantillon, z_echantillon) = echantillon
        (sampx, sampy, phiy) = sample

        x_echantillon_real = x_echantillon / pixelsPerMm_Hor + sampx.get_position()
        y_echantillon_real = y_echantillon / pixelsPerMm_Hor + sampy.get_position()
        z_echantillon_real = z_echantillon / pixelsPerMm_Hor + phiy.get_position()

        if phiy.get_limits() is not None:
            if z_echantillon_real + phiy.get_position() < phiy.get_limits()[0] * 2:
                log.info(
                    "sample_centring: phiy limits: %s" % str(phiy.get_limits())
                )
                log.info(
                    "sample_centring:  requiring: %s"
                    % str(z_echantillon_real + phiy.get_position())
                )
                log.error("sample_centring: loop too long")

                self.move_motors(sample_centring.SAVED_INITIAL_POSITIONS)
                raise Exception()

        centred_pos = sample_centring.SAVED_INITIAL_POSITIONS.copy()

        centred_pos.update(
            {
                phi.motor: PHI_ANGLE_START,
                sampx.motor: x_echantillon_real,
                sampy.motor: y_echantillon_real,
                phiy.motor: z_echantillon_real,
            }
        )
        logging.getLogger("HWR").info("sample_centring: centring result")

        logging.getLogger("HWR").info(
            "sample_centring: SampX: %s" % x_echantillon_real
        )
        logging.getLogger("HWR").info(
            "sample_centring: SampY: %s" % y_echantillon_real
        )
        logging.getLogger("HWR").info(
            "sample_centring: PhiY: %s" % z_echantillon_real
        )

        return centred_pos


    def px1_center(
        self,
        phi,
        phiy,
        phiz,
        sampx,
        sampy,
        pixelsPerMm_Hor,
        pixelsPerMm_Ver,
        beam_x,
        beam_y,
        chi_angle,
        n_points,
        phi_incr,
        sample_type,
        automatic=False,
    ):

        if sample_type.upper() in ["PLATE", "CHIP"]:
            # go back half of the total range
            logging.getLogger("user_level_log").info(
                "centerig in plate mode / n_points %s / incr %s" % (n_points, phi_incr)
            )
            half_range = (phi_incr * (n_points - 1)) / 2.0
            phi.sync_move_relative(-half_range)
        else:
            logging.getLogger("user_level_log").info(
                "centerig in loop mode / n_points %s / incr %s " % (n_points, phi_incr)
            )

        # VARIABLE DEFINITION

        global USER_CLICKED_EVENT

        PHI_ANGLE_START = phi.get_position()
        PhiCamera = 90

        X, Y = [], []
        phi_positions = []

        time.sleep(2)

        try:

            # TAKE USER INPUT
            if automatic:
                self.px1_center_murko(X, Y, phi_positions, phi, n_points, PHI_ANGLE_START, phi_incr)
            else:
                self.px1_center_user_input(X, Y, phi_positions, phi, n_points, PHI_ANGLE_START, phi_incr) # Wrong but not reached here so don't touch if no break :)

            # COMPUTATIONS
            echantillon = self.px1_center_computations(X, Y, beam_x, beam_y, phi_positions, PhiCamera, n_points)
            (x_echantillon, y_echantillon, z_echantillon) = echantillon

            logging.getLogger("HWR").info(
                "sample_centring: Calculating centred position with"
            )
            logging.getLogger("HWR").info(
                "sample_centring:    / x_ech: %s / y_ech: %s / z_ech: %s"
                % (x_echantillon, y_echantillon, z_echantillon)
            )
            logging.getLogger("HWR").info(
                "sample_centring:    / sampx: %s / sampy: %s / phiy: %s"
                % (sampx.get_position(), sampy.get_position(), phiy.get_position())
            )
            logging.getLogger("HWR").info(
                "sample_centring:    / pixels_per_mm: %s " % (pixelsPerMm_Hor)
            )


            # MOVE MOTORS
            centred_pos = self.px1_center_move_motors(echantillon, (sampx, sampy, phiy), pixelsPerMm_Hor, PHI_ANGLE_START, phi)

            return centred_pos

        except gevent.GreenletExit:
            logging.getLogger("HWR").debug("sample_centring.py - Centring aborted")

            sample_centring.abort_centring()
            # return None

        except:
            import traceback

            logging.getLogger("HWR").error(
                "sample_centring: Exception. %s" % traceback.format_exc()
            )

    def set_chip_mode(self, flag):
        self.chip_mode = flag


    def update_backlight(self):
        self.back_light_phase_switch = "ON" if self.px1env_ho.device.currentPhase == "VISUSAMPLE" else "OFF"

    def phase_switch(self):
        if self.back_light_phase_switch == "ON":
            self.px1env_ho.set_phase("VISU_SAMPLE")
        elif self.back_light_phase_switch == "OFF":
            self.px1env_ho.set_phase("DEFAULT")

        self.update_backlight()

    def test_backlight (self):
        if self.back_light_phase_switch == "OFF":
            self.back_light_phase_switch =="ON"
        else:
            self.back_light_switch == "OFF"


    def in_chip_mode(self):
        return self.chip_mode

    def set_phase(self, phase, timeout=None):
        """Sets ENVIRONMENT to selected phase
        """
        translation_to_env = {"TRANSFER" :0,
                              "CENTRING" :1,
                              "COLLECT" : 2,
                              "DEFAULT" : 3,
                              "VISU_SAMPLE" : 8 }


        if timeout:
            self.px1env_ho.ready_event.clear()

            self.px1env_ho.cmds.get(translation_to_env[phase])()
            #self.px1env_ho.set_phase(phase)
            self.px1env_ho.ready_event.wait()
            self.px1env_ho.ready_event.clear()

        else:
            cmd = self.px1env_ho.cmds.get(translation_to_env[phase])
            if cmd is not None:
                logging.debug(f"PX1environment.goto_phase state {self.get_state()}")
                cmd()
        self.update_backlight()

    def prepare_centring(self, timeout=20):
        env_state = self.px1env_ho.get_state()
        self.px1env_ho.goto_centring_phase()
        if env_state != "ON" and not self.px1env_ho.ready_for_centring():
            self.px1env_ho.goto_centring_phase()
            gevent.sleep(0.1)
        if not self.px1env_ho.ready_for_centring():
            t0 = time.time()
            while True:
                env_state = self.px1env_ho.get_state()
                if env_state != "RUNNING" and self.px1env_ho.ready_for_centring():
                    break
                if time.time() - t0 > timeout:
                    logging.getLogger("HWR").debug("timeout sending supervisor to sample view phase")
                    break
                gevent.sleep(0.1)
        self.light_arm._adjust_light_level()

    def mount_finished(self, wash=False):
        if not wash:
            self.move_pin_length()

    def move_pin_length(self):
        try:
            pin_length_pos = float(self.px1conf_ho.get_pin_length())
            goto = float(pin_length_pos)
            if abs(goto) > 4:
                logging.getLogger("HWR").debug(" pin length position %s is maybe too big?" % goto)
                return

            mot_phiy = self.motor_hwobj_dict.get("phiy")
            mot_phiy.move(goto)
        except:
            import traceback
            logging.getLogger("HWR").debug(" cannot move to pin length ")
            logging.getLogger("HWR").debug( traceback.format_exc() )

    def smargon_state_changed(self, value):
        if value != self.smargon_state:
            if value != self.smargon_state:
                self.smargon_state = value
                self.emit("minidiffStateChanged",(value,))

    def is_ready(self):
        val = str(self.smargon._state_chan.get_value())
        return val == "STANDBY"

    def get_pixels_per_mm(self):
        position = self.zoom.get_value()
        x= float(self.zoom.positions[position]['calibrationData']['pixelsPerMmY'])
        y= float(self.zoom.positions[position]['calibrationData']['pixelsPerMmZ'])

        self.pixels_per_mm_x = x
        self.pixels_per_mm_y = y
        return GenericDiffractometer.get_pixels_per_mm(self)

    def update_zoom_calibration(self):
        self._update_zoom_calibration()
        if 0 not in [self.pixels_per_mm_x, self.pixels_per_mm_y]:
            self.emit("pixelsPerMmChanged", ((self.pixels_per_mm_x, self.pixels_per_mm_y),))

    def update_pixels_per_mm(self):
        self.update_zoom_calibration()

    def _update_zoom_calibration(self):
        """
        """
        if 'zoom' not in self.motor_hwobj_dict:
            # not initialized yet
            return
        zoom_motor = self.motor_hwobj_dict['zoom']
        self.get_pixels_per_mm()

        props = zoom_motor.get_properties()["calibrationData"]

        if props is None:
            logging.getLogger("HWR").debug("PX1MiniDiff. no valid zoom position. calibration is invalid")
            return

        if 'pixelsPerMmZ' in props.keys() and 'pixelsPerMmY' in props.keys():
            self.pixels_per_mm_x = float(props['pixelsPerMmY'])
            self.pixels_per_mm_y = float(props['pixelsPerMmZ'])
        else:
            self.pixels_per_mm_x = 0
            self.pixels_per_mm_y = 0

        if 'arrowStep' in props.keys():
            self.arrow_step = float(props['arrowStep']) / 1000  # in zoom.xml value is in microns
        else:
            self.arrow_step =  self.default_arrow_step

        # log.debug("  - arrow step for this zoom is %s mm" % self.arrow_step)

        if 'beamPositionX' in props.keys() and 'beamPositionY' in props.keys():
            self.beam_x = float(props['beamPositionX'])
            self.beam_y = float(props['beamPositionY'])


    def px1_manual_centring(self, sample_info=None, wait_result=None):
        """
        """
        self.emit_progress_message("Manual 3 click centring...")
        logging.getLogger("HWR").debug("   starting manual 3 click centring. phiy is %s" % str(self.centring_phiy))

        centring_points = self.px1conf_ho.get_centring_points()
        centring_phi_incr = self.px1conf_ho.get_centring_phi_increment()
        centring_sample_type = self.px1conf_ho.get_centring_sample_type()
        self.current_centring_procedure = \
                sample_centring.px1_start({"phi": self.centring_phi,
                                 "phiy": self.centring_phiy,
                                 "sampx": self.centring_sampx,
                                 "sampy": self.centring_sampy,
                                 "phiz": self.centring_phiz },
                                 self.pixels_per_mm_x,
                                 self.pixels_per_mm_y,
                                 self.beam_position[0],
                                 self.beam_position[1],
                                 n_points=centring_points, phi_incr=centring_phi_incr, sample_type=centring_sample_type)

        self.current_centring_procedure.link(self.centring_done)

    def px1_automatic_centring(self, sample_info=None, loop_only=False, wait_result=None):
        """
        """
        self.emit_progress_message("Automatic centring...")
        logging.getLogger("HWR").debug("Starting automatic centring. phiy is %s" % str(self.centring_phiy))

        centring_points = self.px1conf_ho.get_centring_points()
        centring_phi_incr = self.px1conf_ho.get_centring_phi_increment()
        centring_sample_type = self.px1conf_ho.get_centring_sample_type()
        self.current_centring_procedure = self.px1_start(
            {
                "phi": self.centring_phi,
                "phiy": self.centring_phiy,
                "sampx": self.centring_sampx,
                "sampy": self.centring_sampy,
                "phiz": self.centring_phiz,
            },
            self.pixels_per_mm_x,
            self.pixels_per_mm_y,
            self.beam_position[0],
            self.beam_position[1],
            n_points=centring_points,
            phi_incr=centring_phi_incr,
            sample_type=centring_sample_type,
            automatic=True,
        )

        self.current_centring_procedure.link(self.centring_done)
        """
        right now need to link murko with host, port, import and
        set env variables to find paths

        current version doesn't do zooms and does only 1 time 3 takes to center
        """

        """
        if (HWR.beamline.diffractometer.zoom.get_value() != "zoom1"):
            HWR.beamline.diffractometer.zoom.goto_position('zoom1')
        if (HWR.beamline.diffractometer.zoom.get_value() != "zoom4"):
            HWR.beamline.diffractometer.zoom.goto_position('zoom4')
        """


    def centring_motor_moved(self, pos):
        """
        """
        #if time.time() - self.centring_time > 4.0:
        #    self.invalidate_centring()
        self.emit_diffractometer_moved()

    def centring_done(self, centring_procedure, XYZcombined=True):
        """
        Descript. :
        """
        logging.getLogger("HWR").debug("Diffractometer: centring procedure done.")
        try:
            # Check the .get() method
            motor_pos = centring_procedure.get()
            if isinstance(motor_pos, gevent.GreenletExit):
                raise motor_pos
        except:
            logging.exception("Could not complete centring")
            self.emit_centring_failed()
        else:

            if motor_pos != None and not XYZcombined:
                for motor in motor_pos:
                    position = motor_pos[motor]
                    logging.getLogger("HWR").debug("   - motor is %s - going to %s" % (motor.name(), position))

                self.emit_progress_message("Moving sample to centred position...")
                self.emit_centring_moving()
                try:
                    self.move_to_motors_positions(motor_pos, wait=True)
                except:
                    logging.exception("Could not move to centred position")
                    self.emit_centring_failed()
                else:
                    pass

                if self.current_centring_method == GenericDiffractometer.CENTRING_METHOD_AUTO:
                    self.emit("newAutomaticCentringPoint", motor_pos)

                self.centring_time = time.time()
                self.emit_centring_successful()
                self.emit_progress_message("")
                self.ready_event.set()
            if motor_pos != None and XYZcombined:
                xyz_motors = {}
                omega_pos = None
                for motor in motor_pos:
                    position = motor_pos[motor]
                    if motor.name() != "omega":
                        xyz_motors.update({motor.name(): position})
                        logging.getLogger("HWR").debug("   - MOTOR is %s - going to %s" % (motor.name(), position))
                    else:
                        omega_pos = position

                self.emit_progress_message("Moving sample to centred position...")
                self.emit_centring_moving()

                try:
                    #if omega_pos:
                    #   logging.getLogger("HWR").info(" Moving Omega to %.3f" % omega_pos)
                    #   self.move_omega(omega_pos)
                    logging.getLogger("HWR").info(" Moving XYZ to %s" % xyz_motors)
                    self.smargon.move_XYZ(xyz_motors)
                    #self.move_to_motors_positions(motor_pos, wait=True)
                except:
                    logging.exception("Could not move to centred position")
                    self.emit_centring_failed()
                else:
                    pass

                if self.current_centring_method == GenericDiffractometer.CENTRING_METHOD_AUTO:
                    self.emit("newAutomaticCentringPoint", motor_pos)

                self.centring_time = time.time()
                self.emit_centring_successful()
                self.emit_progress_message("")
                self.ready_event.set()



    def move_to_beam(self, x,y, omega=None):

        if not (self.beam_x and self.beam_y):
            self.update_zoom_calibration()


        phi_angle = self.get_omega_position()

        mot_y = self.motor_hwobj_dict.get("sampy")
        mot_x = self.motor_hwobj_dict.get("sampx")
        mot_phiy = self.motor_hwobj_dict.get("phiy")

        dx = (x-self.beam_x) / self.pixels_per_mm_x
        dy = (y-self.beam_y) / self.pixels_per_mm_y

        d_sy = math.cos(math.radians(phi_angle)) * dy
        d_sx = math.sin(math.radians(phi_angle)) * dy

        d_phiy = -dx

        mot_phiy.move_relative(d_phiy)
        mot_x.move_relative(d_sx)
        mot_y.move_relative(d_sy)

    def move_to_centred_position(self, centred_position):
        """
        """
        self.move_to_motors_positions(centred_position)

    def move_to_motors_positions(self, motors_positions, wait=False):
        """
        """
        self.emit_progress_message("Moving to motors positions...")
        self.move_to_motors_positions_procedure = gevent.spawn(\
             self.move_motors, motors_positions)

        self.move_to_motors_positions_procedure.link(self.move_motors_done)

        if wait:
            self.wait_device_ready(10)

    def move_omega_relative(self, relative_pos, wait=True):
        omega_mot = self.motor_hwobj_dict.get("phi")
        omega_mot.sync_move_relative(relative_pos, wait)

    def move_omega(self, target_position):
        omega_mot = self.motor_hwobj_dict.get("phi")
        omega_mot.sync_move(target_position)

    def move_motors(self, motor_positions, timeout=15):
        """
        Moves diffractometer motors to the requested positions

        :param motors_dict: dictionary with motor names or hwobj
                            and target values.
        :type motors_dict: dict
        """
        from queue_model_objects_v1 import CentredPosition

        if isinstance(motor_positions,  CentredPosition):
            motor_positions = motor_positions.as_dict()

        self.wait_device_ready(timeout)
        #logging.getLogger("HWR").info("PX1MiniDiff.move_motors: motor_positions= %s" % motor_positions)

        for motor in list(motor_positions.keys()):
            #logging.getLogger("HWR").info("PX1MiniDiff.move_motors: INP motor= %s name= %s" % (motor, motor.name()))
            position = motor_positions[motor]


            # CHECK IF FUNCTIONAL !!! is it changing existing values or is it adding new ones?
            if isinstance(motor, str):
                motor_role = motor
                motor = self.motor_hwobj_dict.get(motor_role)
                del motor_positions[motor_role]
                if not motor or motor.name() == "/zoom":
                    continue
                motor_positions[motor] = position
            #logging.getLogger("HWR").info("PX1MiniDiff.move_motors: OUT motor= %s" % motor)
            self.wait_device_ready(timeout)
            try:
                motor.sync_move(position)
            except:
                import traceback
                logging.getLogger("HWR").debug("  / error moving motor on diffractometer. state is %s" % (self.smargon_state))
                logging.getLogger("HWR").debug("     / %s " % traceback.format_exc())

        self.wait_device_ready(timeout)
        self.update_zoom_calibration()

    def motor_positions_to_screen(self, centred_positions_dict):
        """
        """
        self.update_zoom_calibration()
        if None in (self.pixels_per_mm_x, self.pixels_per_mm_y):
            return 0, 0

        sampx_c = centred_positions_dict['sampx']
        sampy_c = centred_positions_dict['sampy']
        phiy_c = centred_positions_dict['phiy']

        if None in [sampx_c, sampy_c, phiy_c]:
            log.debug("Cannot calculate motors to screen")
            return

        beam_x = self.beam_position[0]
        beam_y = self.beam_position[1]

        phi_angle = self.get_omega_position()

        sampx_pos = self.motor_hwobj_dict['sampx'].get_position()
        sampy_pos = self.motor_hwobj_dict['sampy'].get_position()
        phiy_pos = self.motor_hwobj_dict['phiy'].get_position()

        sampx = sampx_c -sampx_pos
        sampy = sampy_c -sampy_pos
        phiy = (phiy_c - phiy_pos)

        cosphi = math.cos(math.radians(phi_angle))
        sinphi = math.sin(math.radians(phi_angle))

        dx = sampx * cosphi - sampy * sinphi
        dy = sampx * sinphi + sampy * cosphi

        x = beam_x - (phiy * self.pixels_per_mm_x)
        y = beam_y + dy * self.pixels_per_mm_y

        return x, y

    def get_centred_point_from_coord(self, x, y, return_by_names=None):
        if not self.beam_x or not self.beam_y:
            self.update_zoom_calibration()


        dx = (x - self.beam_x) / self.pixels_per_mm_x
        dy = (y - self.beam_y) / self.pixels_per_mm_y

        motor_pos = self.get_motor_positions()
        phi_angle = motor_pos["omega"]
        sampx = motor_pos["sampx"]
        sampy = motor_pos["sampy"]
        phiy = motor_pos["phiy"]
        phiz = motor_pos["phiz"]

        cosphi = math.cos(math.radians(phi_angle))
        sinphi = math.sin(math.radians(phi_angle))

        #rot_matrix = np.matrix([cosphi, -sinphi, sinphi, cosphi])
#
#        rot_matrix.shape = (2,2)
#        inv_matrix = np.array(rot_matrix.I)

        dsampx = dx * cosphi + dy * sinphi
        dsampy = -dx * sinphi + dy * cosphi

        #dsampx, dsampy = np.dot( np.array([0,dy]), inv_matrix )

        sampx += dsampx
        sampy += dsampy

        phiy = phiy - dx
        phiz = sampy # they are the same motor in PX1

        ret_dict = {"phi":  phi_angle,
                    "phiy":  phiy,
                    "phiz":  phiz,
                    "sampx":  sampx,
                    "sampy":  sampy}
        return ret_dict

    def get_motor_positions(self, motor_names=None):

        motor_pos = {}
        mots = [mot for n, mot in self.motor_hwobj_dict.items() if not n == "zoom"]
        for motor in mots:
            motor_pos[motor.name().replace("/", "")] = motor.get_position()
        return motor_pos

    def get_phi_position(self):
        mot = self.motor_hwobj_dict.get("phi", None)
        if mot is not None:
            pos = mot.get_position()
            return pos
        else:
            return None

    get_omega_position = get_phi_position

    def get_osc_limits(self):
        # FOR CHIP TESTS
        return [-20,20]

    def get_scan_limits(self, speed=1, num_images=2, exp_time=0.3):
        # FOR CHIP TESTS
        return [-20,20], 0.3

### start arrow methods
    def go_up(self):
        phi_angle = self.get_omega_position()
        mot_y = self.motor_hwobj_dict.get("sampy")
        mot_x = self.motor_hwobj_dict.get("sampx")

        d_sy = math.cos(math.radians(phi_angle)) * self.arrow_step
        d_sx = math.sin(math.radians(phi_angle)) * self.arrow_step

        mot_x.move_relative(d_sx)
        mot_y.move_relative(d_sy)

    def go_down(self):
        phi_angle = self.get_omega_position()

        mot_y = self.motor_hwobj_dict.get("sampy")
        mot_x = self.motor_hwobj_dict.get("sampx")
        d_sy = -math.cos(math.radians(phi_angle)) * self.arrow_step
        d_sx = -math.sin(math.radians(phi_angle)) * self.arrow_step

        mot_x.move_relative(d_sx)
        mot_y.move_relative(d_sy)


    def go_right(self):
        mot = self.motor_hwobj_dict.get("phiy")
        mot.move_relative(self.arrow_step)

    def go_left(self):
        mot = self.motor_hwobj_dict.get("phiy")
        mot.move_relative(-self.arrow_step)
### end arrow methods

def test_hwo(hwo):
    print ("Current positions are:")
    current_pos = hwo.get_motor_positions()
    for motor in current_pos.keys():
        print ("% 10s" % motor, current_pos[motor])