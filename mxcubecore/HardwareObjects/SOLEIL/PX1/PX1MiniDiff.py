import logging
import gevent
import time
from mxcubecore.HardwareObjects.GenericDiffractometer import (
    GenericDiffractometer,
)
import sample_centring
import numpy as np
import math

log = logging.getLogger("HWR")

class PX1MiniDiff(GenericDiffractometer):

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
        self.lightarm_hwobj = self.get_object_by_role('lightarm')
        self.px1conf_ho = self.get_object_by_role('px1configuration')
        self.px1env_ho = self.get_object_by_role('px1environment')
        self.pixels_per_mm_x = 0
        self.pixels_per_mm_y = 0
        self.arrow_step = self.default_arrow_step

        GenericDiffractometer.init(self)

        self.centring_methods = {
             GenericDiffractometer.CENTRING_METHOD_MANUAL: \
                 self.px1_manual_centring,
             GenericDiffractometer.CENTRING_METHOD_AUTO: \
                 self.px1_automatic_centring,
             GenericDiffractometer.CENTRING_METHOD_MOVE_TO_BEAM: \
                 self.start_move_to_beam}

    def px1_start(self, centring_motors_dict,
            pixelsPerMm_Hor, pixelsPerMm_Ver,
            beam_xc, beam_yc,
            chi_angle = 0,
            n_points = 3, phi_incr=120., sample_type="LOOP"):

        global CURRENT_CENTRING

        phi, phiy, phiz, sampx, sampy = sample_centring.prepare(centring_motors_dict)


        CURRENT_CENTRING = gevent.spawn(self.px1_center,
                                        phi,
                                        phiy,
                                        phiz,
                                        sampx,
                                        sampy,
                                        pixelsPerMm_Hor, pixelsPerMm_Ver,
                                        beam_xc, beam_yc,
                                        chi_angle,
                                        n_points, phi_incr, sample_type)
        return CURRENT_CENTRING

    def px1_center(self, phi, phiy, phiz,
                sampx, sampy,
                pixelsPerMm_Hor, pixelsPerMm_Ver,
                beam_xc, beam_yc,
                chi_angle,
                n_points,phi_incr,sample_type):

        global USER_CLICKED_EVENT

        PHI_ANGLE_START = phi.get_position()
        PhiCamera=90

        X, Y, PHI = [], [], []
        P, Q, XB, YB, ANG = [], [], [], [], []

        if sample_type.upper() in ["PLATE","CHIP"]:
            # go back half of the total range
            logging.getLogger("user_level_log").info("centerig in plate mode / n_points %s / incr %s" % (n_points, phi_incr))
            half_range = (phi_incr * (n_points - 1))/2.0
            phi.sync_move_relative(-half_range)
        else:
            logging.getLogger("user_level_log").info("centerig in loop mode / n_points %s / incr %s " % (n_points, phi_incr))

        try:
            # OBTAIN CLICKS
            while True:
                USER_CLICKED_EVENT = gevent.event.AsyncResult()
                user_info = USER_CLICKED_EVENT.get()
                if user_info == "abort":
                    sample_centring.bort_centring()
                    return None
                else:
                    x,y = user_info


                USER_CLICKED_EVENT = gevent.event.AsyncResult()

                X.append(x)
                Y.append(y)
                PHI.append(phi.get_position())

                if len(X) == n_points:
                    #PHI_LAST_ANGLE = phi.get_position()
                    #GO_ANGLE_START = PHI_ANGLE_START - PHI_LAST_ANGLE
                    sample_centring.READY_FOR_NEXT_POINT.set()
                    #phi.sync_move_relative(GO_ANGLE_START)
                    break

                phi.sync_move_relative(phi_incr)
                sample_centring.READY_FOR_NEXT_POINT.set()

            logging.getLogger("user_level_log").info("returning PHI to initial position %s" % PHI_ANGLE_START)
            phi.move(PHI_ANGLE_START)

            # CALCULATE
            logging.getLogger("HWR").debug("sample_centring: INPUT for calculation")
            logging.getLogger("HWR").debug("sample_centring:   beam_xc = %s, beam_yc = %s " % (beam_xc, beam_yc))
            logging.getLogger("HWR").debug("sample_centring:   X = %s, Y = %s " % (str(X), str(Y)))
            logging.getLogger("HWR").debug("sample_centring:   PHI = %s, PhiCamera = %s, n_points = %s " % (str(PHI), PhiCamera, n_points))

            try:
                for i in range(n_points):
                    xb  = X[i] - beam_xc
                    yb = Y[i] - beam_yc
                    ang = math.radians(PHI[i]+PhiCamera)

                    XB.append(xb); YB.append(yb); ANG.append(ang)

                for i in range(n_points):
                    y0 = YB[i] ; a0 = ANG[i]
                    if i < (n_points-1):
                        y1 = YB[i+1] ; a1 = ANG[i+1]
                    else:
                        y1 = YB[0] ; a1 = ANG[0]

                    p = (y0*math.sin(a1)-y1*math.sin(a0))/math.sin(a1-a0)
                    q = (y0*math.cos(a1)-y1*math.cos(a0))/math.sin(a0-a1)

                    P.append(p);  Q.append(q)

                x_echantillon = -sum(P)/n_points
                y_echantillon = sum(Q)/n_points
                z_echantillon = -sum(XB)/n_points
            except:
                import traceback
                logging.getLogger("HWR").info("sample_centring: error while centering: %s" % traceback.format_exc())

            logging.getLogger("HWR").info("sample_centring: Calculating centred position with")
            logging.getLogger("HWR").info("sample_centring:    / x_ech: %s / y_ech: %s / z_ech: %s" % (x_echantillon, y_echantillon, z_echantillon))
            logging.getLogger("HWR").info("sample_centring:    / sampx: %s / sampy: %s / phiy: %s" % (sampx.get_position(), sampy.get_position(), phiy.get_position()))
            logging.getLogger("HWR").info("sample_centring:    / pixels_per_mm: %s " % (pixelsPerMm_Hor))

            x_echantillon_real = x_echantillon/pixelsPerMm_Hor + sampx.get_position()
            y_echantillon_real = y_echantillon/pixelsPerMm_Hor + sampy.get_position()
            z_echantillon_real = z_echantillon/pixelsPerMm_Hor + phiy.get_position()

            if phiy.get_limits() is not None:
                if (z_echantillon_real + phiy.get_position() < phiy.get_limits()[0]*2) :
                    logging.getLogger("HWR").info("sample_centring: phiy limits: %s" % str(phiy.get_limits()))
                    logging.getLogger("HWR").info("sample_centring:  requiring: %s" % str(z_echantillon_real + phiy.get_position()))
                    logging.getLogger("HWR").error("sample_centring: loop too long")

                    self.move_motors(sample_centring.SAVED_INITIAL_POSITIONS)
                    raise Exception()

            centred_pos = sample_centring.SAVED_INITIAL_POSITIONS.copy()

            centred_pos.update({ phi.motor: PHI_ANGLE_START,
                                sampx.motor: x_echantillon_real,
                                sampy.motor: y_echantillon_real,
                                phiy.motor: z_echantillon_real})
            logging.getLogger("HWR").info("sample_centring: centring result")

            logging.getLogger("HWR").info("sample_centring: SampX: %s" % x_echantillon_real)
            logging.getLogger("HWR").info("sample_centring: SampY: %s" % y_echantillon_real)
            logging.getLogger("HWR").info("sample_centring: PhiY: %s" % z_echantillon_real)

            return centred_pos

        except gevent.GreenletExit:
            logging.getLogger("HWR").debug("sample_centring.py - Centring aborted")

            sample_centring.abort_centring()
            #return None

        except:
            import traceback
            logging.getLogger("HWR").error("sample_centring: Exception. %s" % traceback.format_exc())

    def set_chip_mode(self, flag):
        self.chip_mode = flag

    def in_chip_mode(self):
        return self.chip_mode

    def prepare_centring(self, timeout=20):

        env_state = self.px1env_ho.get_state()
        self.px1env_ho.goto_centring_phase()
        if env_state != "ON" and not self.px1env_ho.is_phase_centring():
            self.px1env_ho.goto_centring_phase()
            gevent.sleep(0.1)

        if not self.px1env_ho.is_phase_centring():
            t0 = time.time()
            while True:
                env_state = self.px1env_ho.get_state()
                if env_state != "RUNNING" and self.px1env_ho.is_phase_centring():
                    break
                if time.time() - t0 > timeout:
                    logging.getLogger("HWR").debug("timeout sending supervisor to sample view phase")
                    break
                gevent.sleep(0.1)
        #if self.lightarm_hwobj
        self.lightarm_hwobj.adjust_light_level()

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
        print(f"Got the state value from SMARGON and it is : {val}")

        return val == "STANDBY"

        #self.smargon_state = str(self.smargon_state_ch.getValue())
        #return self.smargon_state == "STANDBY"

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

        props = zoom_motor.get_properties()

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
            self.beam_xc = float(props['beamPositionX'])
            self.beam_yc = float(props['beamPositionY'])


    def px1_manual_centring(self, sample_info=None, wait_result=None):
        """
        """
        self.emit_progress_message("Manual 3 click centring...")
        logging.getLogger("HWR").debug("   starting manual 3 click centring. phiy is %s" % str(self.centring_phiy))

        centring_points = self.px1conf_ho.get_centring_points()
        centring_phi_incr = self.px1conf_ho.get_centring_phi_increment()
        centring_sample_type = self.px1conf_ho.get_centring_sample_type()
        self.current_centring_procedure = \
                 self.px1_start({"phi": self.centring_phi,
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

        phi_angle = self.get_omega_position()

        mot_y = self.motor_hwobj_dict.get("sampy")
        mot_x = self.motor_hwobj_dict.get("sampx")
        mot_phiy = self.motor_hwobj_dict.get("phiy")

        dx = (x-self.beam_xc) / self.pixels_per_mm_x
        dy = (y-self.beam_yc) / self.pixels_per_mm_y

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
        for motor in motor_positions.keys():
            #logging.getLogger("HWR").info("PX1MiniDiff.move_motors: INP motor= %s name= %s" % (motor, motor.name()))
            position = motor_positions[motor]
            if type(motor) in (str, unicode):
                motor_role = motor
                motor = self.motor_hwobj_dict.get(motor_role)
                del motor_positions[motor_role]
                if None in (motor, position):
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

        dx = (x - self.beam_xc) / self.pixels_per_mm_x
        dy = (y - self.beam_yc) / self.pixels_per_mm_y

        motor_pos = self.get_motor_positions()

        phi_angle = motor_pos["phi"]

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
        for motor in self.motor_hwobj_dict.keys():
            if motor_names is not None and motor not in motor_names:
                continue
            mot = self.motor_hwobj_dict.get(motor)
            motor_pos[motor] = mot.get_position()
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

### start autocentring methods
### end autocentring methods

def test_hwo(hwo):
    print ("Current positions are:")
    current_pos = hwo.get_motor_positions()
    for motor in current_pos.keys():
        print ("% 10s" % motor, current_pos[motor])