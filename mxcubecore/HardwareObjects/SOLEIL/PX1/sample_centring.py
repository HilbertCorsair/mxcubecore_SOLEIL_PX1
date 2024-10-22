from scipy import optimize
import numpy
import gevent.event
import gevent
import math
import time
import logging
import os
import tempfile

try:
  import lucid3 as lucid
except ImportError:
  try:
    import lucid2 as lucid
  except ImportError:
    try:
        import lucid
    except ImportError:
        logging.warning("sample_centring: Could not find autocentring library, automatic centring is disabled")

USER_CLICKED_EVENT = None
CURRENT_CENTRING = None
SAVED_INITIAL_POSITIONS = {}
READY_FOR_NEXT_POINT = gevent.event.Event()
def multi_point_centre(z, phis):
    fitfunc = lambda p, x: p[0] * numpy.sin(x + p[1]) + p[2]
    errfunc = lambda p, x, y: fitfunc(p, x) - y
    p1, success = optimize.leastsq(errfunc, [1., 0., 0.], args=(phis, z))
    return p1

def prepare(centring_motors_dict):
    global SAVED_INITIAL_POSITIONS

    if CURRENT_CENTRING and not CURRENT_CENTRING.ready():
        raise RuntimeError("Cannot start new centring while centring in progress")

    global USER_CLICKED_EVENT
    USER_CLICKED_EVENT = gevent.event.AsyncResult()

    motors_to_move = {m.motor: m.reference_position for m in centring_motors_dict.values() if m.reference_position is not None}
    move_motors(motors_to_move)

    SAVED_INITIAL_POSITIONS = {m.motor: m.motor.get_position() for m in centring_motors_dict.values()}

    return (centring_motors_dict["phi"], centring_motors_dict["phiy"], centring_motors_dict["phiz"],
            centring_motors_dict["sampx"], centring_motors_dict["sampy"])

def start(centring_motors_dict, pixels_per_mm_hor, pixels_per_mm_ver,
          beam_xc, beam_yc, chi_angle=0, n_points=3, phi_incr=120., sample_type="LOOP"):
    global CURRENT_CENTRING

    phi, phiy, phiz, sampx, sampy = prepare(centring_motors_dict)

    CURRENT_CENTRING = gevent.spawn(px1_center, phi, phiy, phiz, sampx, sampy,
                                    pixels_per_mm_hor, pixels_per_mm_ver, 
                                    beam_xc, beam_yc, chi_angle,
                                    n_points, phi_incr, sample_type)
    return CURRENT_CENTRING

def start_plate(centring_motors_dict, pixels_per_mm_hor, pixels_per_mm_ver, 
                beam_xc, beam_yc, plate_vertical, chi_angle=0,
                n_points=3, phi_range=10, lim_pos=314.):
    global CURRENT_CENTRING

    plate_translation = centring_motors_dict.pop("plateTranslation")
    phi, phiy, phiz, sampx, sampy = prepare(centring_motors_dict)

    phi.move(lim_pos)

    CURRENT_CENTRING = gevent.spawn(centre_plate, 
                                    phi, phiy, phiz, sampx, sampy, plate_translation,
                                    pixels_per_mm_hor, pixels_per_mm_ver, 
                                    beam_xc, beam_yc, plate_vertical, chi_angle,
                                    n_points, phi_range)
    return CURRENT_CENTRING

def start_plate_1_click(centring_motors_dict, pixels_per_mm_hor, pixels_per_mm_ver, 
                        beam_xc, beam_yc, plate_vertical, phi_min, phi_max, n_points=10):
    global CURRENT_CENTRING

    phi = centring_motors_dict["phi"]
    phiy = centring_motors_dict["phiy"]
    sampx = centring_motors_dict["sampx"]
    sampy = centring_motors_dict["sampy"]
    phiz = centring_motors_dict["phiz"]

    plate_vertical()

    CURRENT_CENTRING = gevent.spawn(centre_plate_1_click, 
                                    phi, phiy, phiz, sampx, sampy,
                                    pixels_per_mm_hor, pixels_per_mm_ver, 
                                    beam_xc, beam_yc, plate_vertical,
                                    phi_min, phi_max, n_points)

    return CURRENT_CENTRING

def centre_plate_1_click(phi, phiy, phiz, sampx, sampy,
                         pixels_per_mm_hor, pixels_per_mm_ver, 
                         beam_xc, beam_yc, plate_vertical,
                         phi_min, phi_max, n_points):
    global USER_CLICKED_EVENT
    
    try:
        i = 0
        previous_click_x = 99999
        previous_click_y = 99999
        dx = 99999
        dy = 99999
        
        while True:
            USER_CLICKED_EVENT = gevent.event.AsyncResult()
            try:
                x, y = USER_CLICKED_EVENT.get()
            except:
                raise RuntimeError("Aborted while waiting for point selection")
            
            # Move to beam 
            phiz.move_relative((y - beam_yc) / float(pixels_per_mm_ver))
            phiy.move_relative(-(x - beam_xc) / float(pixels_per_mm_hor))
                  
            # Distance to previous click to end centring if it converges
            dx = abs(previous_click_x - x)
            dy = abs(previous_click_y - y)
            previous_click_x = x
            previous_click_y = y

            # Alternating between phi min and phi max to gradually converge to the centring point
            if i % 2 == 0:
                phi_min = phi.get_position()
                phi.move(phi_max)
            else:
                phi_max = phi.get_position()
                phi.move(phi_min) 
            
            READY_FOR_NEXT_POINT.set()
            i += 1
    except:
        logging.exception("sample_centring: Exception while centring")
        move_motors(SAVED_INITIAL_POSITIONS)
        raise

def centre_plate(phi, phiy, phiz, sampx, sampy, plate_translation,
                 pixels_per_mm_hor, pixels_per_mm_ver, 
                 beam_xc, beam_yc, plate_vertical,
                 chi_angle, n_points, phi_range=40):
    global USER_CLICKED_EVENT
    x, y, phi_positions = [], [], []

    phi_angle = phi_range / (n_points - 1)

    try:
        i = 0
        while i < n_points:
            try:
                x_coord, y_coord = USER_CLICKED_EVENT.get()
            except:
                raise RuntimeError("Aborted while waiting for point selection")
            USER_CLICKED_EVENT = gevent.event.AsyncResult()
            x.append(x_coord / float(pixels_per_mm_hor))
            y.append(y_coord / float(pixels_per_mm_ver))
            phi_positions.append(phi.direction * math.radians(phi.get_position()))
            if i != n_points - 1:
                phi.sync_move_relative(phi.direction * phi_angle)
            READY_FOR_NEXT_POINT.set()
            i += 1
    except:
        logging.exception("sample_centring: Exception while centring")
        move_motors(SAVED_INITIAL_POSITIONS)
        raise

    chi_angle = math.radians(chi_angle)
    chi_rot_matrix = numpy.matrix([
        [math.cos(chi_angle), -math.sin(chi_angle)],
        [math.sin(chi_angle), math.cos(chi_angle)]
    ])
    z = chi_rot_matrix * numpy.matrix([x, y])
    z_vals = z[1]
    avg_pos = z[0].mean()

    r, a, offset = multi_point_centre(numpy.array(z_vals).flatten(), phi_positions)
    dy = r * numpy.sin(a)
    dx = r * numpy.cos(a)
    
    d = chi_rot_matrix.transpose() * numpy.matrix([[avg_pos], [offset]])

    d_horizontal = d[0] - (beam_xc / float(pixels_per_mm_hor))
    d_vertical = d[1] - (beam_yc / float(pixels_per_mm_ver))

    phi_pos = math.radians(phi.direction * phi.get_position())
    phi_rot_matrix = numpy.matrix([
        [math.cos(phi_pos), -math.sin(phi_pos)],
        [math.sin(phi_pos), math.cos(phi_pos)]
    ])
    vertical_move = phi_rot_matrix * numpy.matrix([[0], d_vertical])
    
    centred_pos = SAVED_INITIAL_POSITIONS.copy()
    if phiz.reference_position is None:
        centred_pos.update({
            sampx.motor: float(sampx.get_position() + sampx.direction * dx),
            sampy.motor: float(sampy.get_position() + sampy.direction * dy),
            phiz.motor: float(phiz.get_position() + phiz.direction * d_vertical[0, 0]),
            phiy.motor: float(phiy.get_position() + phiy.direction * d_horizontal[0, 0])
        })
    else:
        centred_pos.update({
            sampx.motor: float(sampx.get_position() + sampx.direction * (dx + vertical_move[0, 0])),
            sampy.motor: float(sampy.get_position() + sampy.direction * (dy + vertical_move[1, 0])),
            phiy.motor: float(phiy.get_position() + phiy.direction * d_horizontal[0, 0])
        })

    move_motors(centred_pos)
    plate_vertical()

    return centred_pos

def ready(*motors):
    return not any([m.motor_is_moving() for m in motors])

def move_motors(motor_positions_dict):
    if not motor_positions_dict:
        return

    if "sampx" in motor_positions_dict:
        sgonaxis_dev = motor_positions_dict["sampx"]
    else:
        from PyTango import DeviceProxy as dp
        sgonaxis_dev = dp('i10-c-cx1/ex/sgonaxis')

    def wait_ready(timeout=None):
        with gevent.Timeout(timeout):
            while not ready(*motor_positions_dict.keys()):
                gevent.sleep(0.03)

    wait_ready(timeout=30)

    if not ready(*motor_positions_dict.keys()):
        raise RuntimeError("Motors not ready")

    if sgonaxis_dev:
        sgonaxis_dev.freeze = True

    for motor, position in motor_positions_dict.items():
        motor.move(position)

    if sgonaxis_dev:
        sgonaxis_dev.freeze = False

    wait_ready()
  
def user_click(x, y, wait=False):
    READY_FOR_NEXT_POINT.clear()
    USER_CLICKED_EVENT.set((x, y))
    if wait:
        READY_FOR_NEXT_POINT.wait()
  
def center(phi, phiy, phiz, sampx, sampy, 
           pixels_per_mm_hor, pixels_per_mm_ver, 
           beam_xc, beam_yc, chi_angle,
           n_points, phi_range=180):
    global USER_CLICKED_EVENT
    x, y, phi_positions = [], [], []

    phi_angle = phi_range / (n_points - 1)

    try:
        i = 0
        while i < n_points:
            try:
                x_coord, y_coord = USER_CLICKED_EVENT.get()
            except:
                raise RuntimeError("Aborted while waiting for point selection")
            USER_CLICKED_EVENT = gevent.event.AsyncResult()
            x.append(x_coord / float(pixels_per_mm_hor))
            y.append(y_coord / float(pixels_per_mm_ver))
            phi_positions.append(phi.direction * math.radians(phi.get_position()))
            if i != n_points - 1:
                phi.sync_move_relative(phi.direction * phi_angle)
            READY_FOR_NEXT_POINT.set()
            i += 1
    except:
        logging.exception("sample_centring: Exception while centring")
        move_motors(SAVED_INITIAL_POSITIONS)
        raise

    chi_angle = math.radians(chi_angle)
    chi_rot_matrix = numpy.matrix([
        [math.cos(chi_angle), -math.sin(chi_angle)],
        [math.sin(chi_angle), math.cos(chi_angle)]
    ])
    z = chi_rot_matrix * numpy.matrix([x, y])
    z_vals = z[1]
    avg_pos = z[0].mean()

    r, a, offset = multi_point_centre(numpy.array(z_vals).flatten(), phi_positions)
    dy = r * numpy.sin(a)
    dx = r * numpy.cos(a)
    
    d = chi_rot_matrix.transpose() * numpy.matrix([[avg_pos], [offset]])

    d_horizontal = d[0] - (beam_xc / float(pixels_per_mm_hor))
    d_vertical = d[1] - (beam_yc / float(pixels_per_mm_ver))

    phi_pos = math.radians(phi.direction * phi.get_position())
    phi_rot_matrix = numpy.matrix([
        [math.cos(phi_pos), -math.sin(phi_pos)],
        [math.sin(phi_pos), math.cos(phi_pos)]
    ])
    vertical_move = phi_rot_matrix * numpy.matrix([[0], d_vertical])
    
    centred_pos = SAVED_INITIAL_POSITIONS.copy()
    if phiz.reference_position is None:
        centred_pos.update({
            sampx.motor: float(sampx.get_position() + sampx.direction * dx),
            sampy.motor: float(sampy.get_position() + sampy.direction * dy),
            phiz.motor: float(phiz.get_position() + phiz.direction * d_vertical[0, 0]),
            phiy.motor: float(phiy.get_position() + phiy.direction * d_horizontal[0, 0])
        })
    else:
        centred_pos.update({
            sampx.motor: float(sampx.get_position() + sampx.direction * (dx + vertical_move[0, 0])),
            sampy.motor: float(sampy.get_position() + sampy.direction * (dy + vertical_move[1, 0])),
            phiy.motor: float(phiy.get_position() + phiy.direction * d_horizontal[0, 0])
        })

    return centred_pos

def px1_start(centring_motors_dict,
              pixels_per_mm_hor, pixels_per_mm_ver,
              beam_xc, beam_yc,
              chi_angle=0,
              n_points=3, phi_incr=120., sample_type="LOOP"):

    global CURRENT_CENTRING

    phi, phiy, phiz, sampx, sampy = prepare(centring_motors_dict)

    CURRENT_CENTRING = gevent.spawn(px1_center,
                                    phi,
                                    phiy,
                                    phiz,
                                    sampx,
                                    sampy,
                                    pixels_per_mm_hor, pixels_per_mm_ver,
                                    beam_xc, beam_yc,
                                    chi_angle,
                                    n_points, phi_incr, sample_type)
    return CURRENT_CENTRING

def px1_center(phi, phiy, phiz, sampx, sampy,
               pixels_per_mm_hor, pixels_per_mm_ver,
               beam_xc, beam_yc, chi_angle,
               n_points, phi_incr, sample_type):
    global USER_CLICKED_EVENT

    phi_angle_start = phi.get_position()
    phi_camera = 90

    x, y, phi_positions = [], [], []
    p, q, xb, yb, ang = [], [], [], [], []

    if sample_type.upper() in ["PLATE", "CHIP"]:
        half_range = (phi_incr * (n_points - 1)) / 2.0
        phi.sync_move_relative(-half_range)
    else:
        logging.getLogger("user_level_log").info(f"Centering in loop mode / n_points {n_points} / incr {phi_incr}")

    try:
        while True:
            USER_CLICKED_EVENT = gevent.event.AsyncResult()
            user_info = USER_CLICKED_EVENT.get()
            if user_info == "abort":
                abort_centring()
                return None
            else:
                x_coord, y_coord = user_info

            USER_CLICKED_EVENT = gevent.event.AsyncResult()

            x.append(x_coord)
            y.append(y_coord)
            phi_positions.append(phi.get_position())

            if len(x) == n_points:
                READY_FOR_NEXT_POINT.set()
                break

            phi.sync_move_relative(phi_incr)
            READY_FOR_NEXT_POINT.set()

        logging.getLogger("user_level_log").info(f"Returning PHI to initial position {phi_angle_start}")
        phi.move(phi_angle_start)

        # Calculate centred position
        try:
            for i in range(n_points):
                xb_val = x[i] - beam_xc
                yb_val = y[i] - beam_yc
                ang_val = math.radians(phi_positions[i] + phi_camera)

                xb.append(xb_val)
                yb.append(yb_val)
                ang.append(ang_val)

            for i in range(n_points):
                y0, a0 = yb[i], ang[i]
                y1, a1 = yb[(i + 1) % n_points], ang[(i + 1) % n_points]

                p_val = (y0 * math.sin(a1) - y1 * math.sin(a0)) / math.sin(a1 - a0)
                q_val = (y0 * math.cos(a1) - y1 * math.cos(a0)) / math.sin(a0 - a1)

                p.append(p_val)
                q.append(q_val)

            x_sample = -sum(p) / n_points
            y_sample = sum(q) / n_points
            z_sample = -sum(xb) / n_points
        except Exception:
            logging.getLogger("HWR").error("Error while centering", exc_info=True)

        x_sample_real = x_sample / pixels_per_mm_hor + sampx.get_position()
        y_sample_real = y_sample / pixels_per_mm_hor + sampy.get_position()
        z_sample_real = z_sample / pixels_per_mm_hor + phiy.get_position()

        if phiy.get_limits() is not None:
            if z_sample_real + phiy.get_position() < phiy.get_limits()[0] * 2:
                logging.getLogger("HWR").error("Loop too long")
                move_motors(SAVED_INITIAL_POSITIONS)
                raise Exception("Loop too long")

        centred_pos = SAVED_INITIAL_POSITIONS.copy()
        centred_pos.update({
            phi.motor: phi_angle_start,
            sampx.motor: x_sample_real,
            sampy.motor: y_sample_real,
            phiy.motor: z_sample_real
        })

        return centred_pos

    except gevent.GreenletExit:
        logging.getLogger("HWR").debug("Centring aborted")
        abort_centring()

    except Exception:
        logging.getLogger("HWR").error("Exception in centring", exc_info=True)

def abort_centring():
    move_motors(SAVED_INITIAL_POSITIONS)

def end(centred_pos=None):
    if centred_pos is None:
        centred_pos = CURRENT_CENTRING.get()
    try:
        move_motors(centred_pos)
    except:
        logging.exception("sample_centring: Exception in centring 'end`, centred pos is %s", centred_pos)
        move_motors(SAVED_INITIAL_POSITIONS)
        raise

def start_auto(camera, centring_motors_dict,
               pixels_per_mm_hor, pixels_per_mm_ver, 
               beam_xc, beam_yc,
               chi_angle=0,
               n_points=3,
               msg_cb=None,
               new_point_cb=None):    
    global CURRENT_CENTRING

    phi, phiy, phiz, sampx, sampy = prepare(centring_motors_dict)

    CURRENT_CENTRING = gevent.spawn(auto_center, 
                                    camera, 
                                    phi, phiy, phiz,
                                    sampx, sampy, 
                                    pixels_per_mm_hor, pixels_per_mm_ver, 
                                    beam_xc, beam_yc, 
                                    chi_angle,
                                    n_points,
                                    msg_cb, new_point_cb)
    return CURRENT_CENTRING

def find_loop(camera, pixels_per_mm_hor, chi_angle, msg_cb, new_point_cb):
    snapshot_filename = os.path.join(tempfile.gettempdir(), "mxcube_sample_snapshot.png")
    camera.take_snapshot(snapshot_filename, bw=True)
    
    info, x, y = lucid.find_loop(snapshot_filename, iteration_closing=6)
    
    try:
        x = float(x)
        y = float(y)
    except Exception:
        return -1, -1
    
    if callable(msg_cb):
        msg_cb(f"Loop found: {info} ({x}, {y})")
    if callable(new_point_cb):
        new_point_cb((x, y))
            
    return x, y

def auto_center(camera, 
                phi, phiy, phiz,
                sampx, sampy, 
                pixels_per_mm_hor, pixels_per_mm_ver, 
                beam_xc, beam_yc, 
                chi_angle, 
                n_points,
                msg_cb, new_point_cb):
    img_width = camera.get_width()
    img_height = camera.get_height()
    
    # Check if loop is there at the beginning
    i = 0
    while -1 in find_loop(camera, pixels_per_mm_hor, chi_angle, msg_cb, new_point_cb):
        phi.sync_move_relative(90)
        i += 1
        if i > 4:
            if callable(msg_cb):
                msg_cb("No loop detected, aborting")
            return
    
    # Number of lucid2 runs increased to 3 (Olof June 26th 2015)
    for k in range(3):
        if callable(msg_cb):
            msg_cb("Doing automatic centring")
            
        centring_greenlet = gevent.spawn(center,
                                         phi, phiy, phiz,
                                         sampx, sampy, 
                                         pixels_per_mm_hor, pixels_per_mm_ver, 
                                         beam_xc, beam_yc, 
                                         chi_angle, 
                                         n_points)

        for a in range(n_points):
            x, y = find_loop(camera, pixels_per_mm_hor, chi_angle, msg_cb, new_point_cb) 
            if x < 0 or y < 0:
                for i in range(1, 18):
                    phi.sync_move_relative(5)
                    x, y = find_loop(camera, pixels_per_mm_hor, chi_angle, msg_cb, new_point_cb)
                    if -1 in (x, y):
                        continue
                    if x >= 0:
                        if y < img_height / 2:
                            y = 0
                        else:
                            y = img_height
                        if callable(new_point_cb):
                            new_point_cb((x, y))
                        user_click(x, y, wait=True)
                        break
                if -1 in (x, y):
                    centring_greenlet.kill()
                    raise RuntimeError("Could not centre sample automatically.")
                phi.sync_move_relative(-i * 5)
            else:
                user_click(x, y, wait=True)

        centred_pos = centring_greenlet.get()
        end(centred_pos)
                    
    return centred_pos