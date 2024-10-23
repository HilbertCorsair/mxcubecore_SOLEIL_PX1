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


def multiPointCentre(z,phis) :
    fitfunc = lambda p,x: p[0] * numpy.sin(x+p[1]) + p[2]
    errfunc = lambda p,x,y: fitfunc(p,x) - y
    p1, success = optimize.leastsq(errfunc,[1.,0.,0.],args = (phis,z))
    return p1

USER_CLICKED_EVENT = None
CURRENT_CENTRING = None
SAVED_INITIAL_POSITIONS = {}
READY_FOR_NEXT_POINT = gevent.event.Event()

class CentringMotor:
  def __init__(self, motor, reference_position=None, direction=1):
    self.motor = motor
    self.direction = direction
    self.reference_position = reference_position
  def __getattr__(self, attr):
    # delegate to motor object
    if attr.startswith("__"):
      raise AttributeError(attr)
    else:
      return getattr(self.motor, attr)
  
def prepare(centring_motors_dict):
  global SAVED_INITIAL_POSITIONS

  if CURRENT_CENTRING and not CURRENT_CENTRING.ready():
    raise RuntimeError("Cannot start new centring while centring in progress")
  
  global USER_CLICKED_EVENT
  USER_CLICKED_EVENT = gevent.event.AsyncResult()  

  motors_to_move = dict()
  for m in centring_motors_dict.itervalues():
    if m.reference_position is not None:
      motors_to_move[m.motor] = m.reference_position
  #if sgonaxis_dev: sgonaxis_dev.freeze = False
  move_motors(motors_to_move)

  SAVED_INITIAL_POSITIONS = dict([(m.motor, m.motor.getPosition()) for m in centring_motors_dict.itervalues()])

  phi = centring_motors_dict["phi"]
  phiy = centring_motors_dict["phiy"]
  sampx = centring_motors_dict["sampx"]
  sampy = centring_motors_dict["sampy"] 
  phiz = centring_motors_dict["phiz"]

  return phi, phiy, phiz, sampx, sampy
  
def start(centring_motors_dict,
          pixelsPerMm_Hor, pixelsPerMm_Ver,
          beam_xc, beam_yc,
          chi_angle = 0,
          n_points = 3, phi_incr=120., sample_type="LOOP"):
  global CURRENT_CENTRING

  phi, phiy, phiz, sampx, sampy = prepare(centring_motors_dict)
  

  CURRENT_CENTRING = gevent.spawn(px1_center,
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

def start_plate(centring_motors_dict,
          pixelsPerMm_Hor, pixelsPerMm_Ver, 
          beam_xc, beam_yc, plate_vertical,
          chi_angle = 0,
          n_points = 3, phi_range = 10, lim_pos=314.):
  global CURRENT_CENTRING

  plateTranslation = centring_motors_dict["plateTranslation"]
  centring_motors_dict.pop("plateTranslation")
  phi, phiy,phiz, sampx, sampy = prepare(centring_motors_dict)

  phi.move(lim_pos)

  CURRENT_CENTRING = gevent.spawn(centre_plate, 
                                  phi,
                                  phiy,
                                  phiz,
                                  sampx, 
                                  sampy,
                                  plateTranslation,
                                  pixelsPerMm_Hor, pixelsPerMm_Ver, 
                                  beam_xc, beam_yc,
                                  plate_vertical,
                                  chi_angle,
                                  n_points, phi_range)
  return CURRENT_CENTRING


def start_plate_1_click(centring_motors_dict,
          pixelsPerMm_Hor, pixelsPerMm_Ver, 
          beam_xc, beam_yc, plate_vertical,
          phi_min,phi_max,n_points = 10 ):
  global CURRENT_CENTRING

  #plateTranslation = centring_motors_dict["plateTranslation"]
  #centring_motors_dict.pop("plateTranslation")
  
  #phi, phiy,phiz, sampx, sampy = prepare(centring_motors_dict)
  
  phi = centring_motors_dict["phi"]
  phiy = centring_motors_dict["phiy"]
  sampx = centring_motors_dict["sampx"]
  sampy = centring_motors_dict["sampy"] 
  phiz = centring_motors_dict["phiz"]

  #phi.move(phi_min)
  plate_vertical()

  CURRENT_CENTRING = gevent.spawn(centre_plate1Click, 
                                  phi,
                                  phiy,
                                  phiz,
                                  sampx,
                                  sampy,
                                  pixelsPerMm_Hor, pixelsPerMm_Ver, 
                                  beam_xc, beam_yc,
                                  plate_vertical,
                                  phi_min,
                                  phi_max,
                                  n_points)

  return CURRENT_CENTRING



def centre_plate1Click(phi,
                       phiy,
                       phiz,
                       sampx,
                       sampy,
                       pixelsPerMm_Hor,
                       pixelsPerMm_Ver, 
                       beam_xc,
                       beam_yc,
                       plate_vertical,
                       phi_min,
                       phi_max,
                       n_points):

  global USER_CLICKED_EVENT
  
  
  try:
    i = 0
    previous_click_x = 99999
    previous_click_y = 99999
    dx = 99999
    dy = 99999
    
    #while i < n_points and (dx > 3 or dy > 3) :
    while True:   # it is now a while true loop that can be interrupted at any time by the save button, to allow user to have a 1 click centring as precise as he wants (see HutchMenuBrick)
      USER_CLICKED_EVENT = gevent.event.AsyncResult()
      try:
        x, y = USER_CLICKED_EVENT.get()
      except:
        raise RuntimeError("Aborted while waiting for point selection")
      
      
      # Move to beam 
      phiz.moveRelative((y-beam_yc)/float(pixelsPerMm_Ver))
      phiy.moveRelative(-(x-beam_xc)/float(pixelsPerMm_Hor))
            
      # Distance to previous click to end centring if it converges
      dx = abs(previous_click_x - x)
      dy = abs(previous_click_y - y)
      previous_click_x = x
      previous_click_y = y

      # Alterning between phi min and phi max to gradually converge to the centring point
      if i%2 == 0:
        phi_min = phi.getPosition() # in case the phi range sent us to a position where sample is invisible, if user moves phi, this modifications is saved for future moves
        phi.move(phi_max)
      else:
        phi_max = phi.getPosition() # in case the phi range sent us to a position where sample is invisible, if user moves phi, this modifications is saved for future moves
        phi.move(phi_min) 
      
      READY_FOR_NEXT_POINT.set()
      i += 1
  except:
    logging.exception("sample_centring: Exception while centring")
    move_motors(SAVED_INITIAL_POSITIONS)
    raise

  plate_vertical()

  centred_pos = SAVED_INITIAL_POSITIONS.copy()
 
  centred_pos.update({ sampx.motor: float(sampx.getPosition()),
                       sampy.motor: float(sampy.getPosition()),
                    })  

  return centred_pos




def centre_plate(phi, phiy, phiz,
           sampx, sampy, plateTranslation,
           pixelsPerMm_Hor, pixelsPerMm_Ver, 
           beam_xc, beam_yc, plate_vertical,
           chi_angle,
           n_points, phi_range = 40):
  global USER_CLICKED_EVENT
  X, Y, phi_positions = [], [], []

  phi_angle = phi_range/(n_points-1)

  try:
    i = 0
    while i < n_points:
      try:
        x, y = USER_CLICKED_EVENT.get()
      except:
        raise RuntimeError("Aborted while waiting for point selection")
      USER_CLICKED_EVENT = gevent.event.AsyncResult()
      X.append(x / float(pixelsPerMm_Hor))
      Y.append(y / float(pixelsPerMm_Ver))
      phi_positions.append(phi.direction*math.radians(phi.getPosition()))
      if i != n_points-1:
        phi.syncMoveRelative(phi.direction*phi_angle)
      READY_FOR_NEXT_POINT.set()
      i += 1
  except:
    logging.exception("sample_centring: Exception while centring")
    move_motors(SAVED_INITIAL_POSITIONS)
    raise

  #logging.info("X=%s,Y=%s", X, Y)
  chi_angle = math.radians(chi_angle)
  chiRotMatrix = numpy.matrix([[math.cos(chi_angle), -math.sin(chi_angle)],
                               [math.sin(chi_angle), math.cos(chi_angle)]])
  Z = chiRotMatrix*numpy.matrix([X,Y])
  z = Z[1]; avg_pos = Z[0].mean()

  r, a, offset = multiPointCentre(numpy.array(z).flatten(), phi_positions)
  dy = r * numpy.sin(a)
  dx = r * numpy.cos(a)
  
  d = chiRotMatrix.transpose()*numpy.matrix([[avg_pos], [offset]])

  d_horizontal =  d[0] - (beam_xc / float(pixelsPerMm_Hor))
  d_vertical =  d[1] - (beam_yc / float(pixelsPerMm_Ver))


  phi_pos = math.radians(phi.direction*phi.getPosition())
  phiRotMatrix = numpy.matrix([[math.cos(phi_pos), -math.sin(phi_pos)],
                               [math.sin(phi_pos), math.cos(phi_pos)]])
  vertical_move = phiRotMatrix*numpy.matrix([[0],d_vertical])
  
  centred_pos = SAVED_INITIAL_POSITIONS.copy()
  if phiz.reference_position is None:
      centred_pos.update({ sampx.motor: float(sampx.getPosition() + sampx.direction*dx),
                           sampy.motor: float(sampy.getPosition() + sampy.direction*dy),
                           phiz.motor: float(phiz.getPosition() + phiz.direction*d_vertical[0,0]),
                           phiy.motor: float(phiy.getPosition() + phiy.direction*d_horizontal[0,0]) })
  else:
      centred_pos.update({ sampx.motor: float(sampx.getPosition() + sampx.direction*(dx + vertical_move[0,0])),
                           sampy.motor: float(sampy.getPosition() + sampy.direction*(dy + vertical_move[1,0])),
                           phiy.motor: float(phiy.getPosition() + phiy.direction*d_horizontal[0,0]) })

  
  move_motors(centred_pos)
  plate_vertical()
  """
  try:
    x, y = USER_CLICKED_EVENT.get()
  except:
    raise RuntimeError("Aborted while waiting for point selection")
  USER_CLICKED_EVENT = gevent.event.AsyncResult()
  y_offset = -(y-beam_yc)  / float(pixelsPerMm_Ver)
  plateTranslation.moveRelative(y_offset)
  """

  return centred_pos

def ready(*motors):
  return not any([m.motorIsMoving() for m in motors])

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

    for motor, position in motor_positions_dict.iteritems():
        motor.move(position)

    if sgonaxis_dev:
        sgonaxis_dev.freeze = False

    wait_ready()
  
def user_click(x,y, wait=False):
  READY_FOR_NEXT_POINT.clear()
  USER_CLICKED_EVENT.set((x,y))
  if wait:
    READY_FOR_NEXT_POINT.wait()
  
def center(phi, phiy, phiz,
           sampx, sampy, 
           pixelsPerMm_Hor, pixelsPerMm_Ver, 
           beam_xc, beam_yc,
           chi_angle,
           n_points, phi_range= 180):
  global USER_CLICKED_EVENT
  X, Y, phi_positions = [], [], []

  phi_angle = phi_range/(n_points-1)

  try:
    i = 0
    while i < n_points:
      try:
        x, y = USER_CLICKED_EVENT.get()
      except:
        raise RuntimeError("Aborted while waiting for point selection")
      USER_CLICKED_EVENT = gevent.event.AsyncResult()
      X.append(x / float(pixelsPerMm_Hor))
      Y.append(y / float(pixelsPerMm_Ver))
      phi_positions.append(phi.direction*math.radians(phi.getPosition()))
      if i != n_points-1:
        phi.syncMoveRelative(phi.direction*phi_angle)
      READY_FOR_NEXT_POINT.set()
      i += 1
  except:
    logging.exception("sample_centring: Exception while centring")
    move_motors(SAVED_INITIAL_POSITIONS)
    raise

  chi_angle = math.radians(chi_angle)
  chiRotMatrix = numpy.matrix([[math.cos(chi_angle), -math.sin(chi_angle)],
                               [math.sin(chi_angle), math.cos(chi_angle)]])
  Z = chiRotMatrix*numpy.matrix([X,Y])
  z = Z[1]; avg_pos = Z[0].mean()

  r, a, offset = multiPointCentre(numpy.array(z).flatten(), phi_positions)
  dy = r * numpy.sin(a)
  dx = r * numpy.cos(a)
  
  d = chiRotMatrix.transpose()*numpy.matrix([[avg_pos], [offset]])

  d_horizontal =  d[0] - (beam_xc / float(pixelsPerMm_Hor))
  d_vertical =  d[1] - (beam_yc / float(pixelsPerMm_Ver))


  phi_pos = math.radians(phi.direction*phi.getPosition())
  phiRotMatrix = numpy.matrix([[math.cos(phi_pos), -math.sin(phi_pos)],
                               [math.sin(phi_pos), math.cos(phi_pos)]])
  vertical_move = phiRotMatrix*numpy.matrix([[0],d_vertical])
  
  centred_pos = SAVED_INITIAL_POSITIONS.copy()
  if phiz.reference_position is None:
      centred_pos.update({ sampx.motor: float(sampx.getPosition() + sampx.direction*dx),
                           sampy.motor: float(sampy.getPosition() + sampy.direction*dy),
                           phiz.motor: float(phiz.getPosition() + phiz.direction*d_vertical[0,0]),
                           phiy.motor: float(phiy.getPosition() + phiy.direction*d_horizontal[0,0]) })
  else:
      centred_pos.update({ sampx.motor: float(sampx.getPosition() + sampx.direction*(dx + vertical_move[0,0])),
                           sampy.motor: float(sampy.getPosition() + sampy.direction*(dy + vertical_move[1,0])),
                           phiy.motor: float(phiy.getPosition() + phiy.direction*d_horizontal[0,0]) })

  return centred_pos

def px1_start(centring_motors_dict,
          pixelsPerMm_Hor, pixelsPerMm_Ver,
          beam_xc, beam_yc,
          chi_angle = 0,
          n_points = 3, phi_incr=120., sample_type="LOOP"):

  global CURRENT_CENTRING

  phi, phiy, phiz, sampx, sampy = prepare(centring_motors_dict)


  CURRENT_CENTRING = gevent.spawn(px1_center,
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

def px1_center(phi, phiy, phiz,
               sampx, sampy,
               pixelsPerMm_Hor, pixelsPerMm_Ver,
               beam_xc, beam_yc,
               chi_angle,
               n_points,phi_incr,sample_type):

    global USER_CLICKED_EVENT

    PHI_ANGLE_START = phi.getPosition()
    PhiCamera=90

    X, Y, PHI = [], [], []
    P, Q, XB, YB, ANG = [], [], [], [], []

    if sample_type.upper() in ["PLATE","CHIP"]:
        # go back half of the total range 
        logging.getLogger("user_level_log").info("centerig in plate mode / n_points %s / incr %s" % (n_points, phi_incr))
        half_range = (phi_incr * (n_points - 1))/2.0
        phi.syncMoveRelative(-half_range)
    else:
        logging.getLogger("user_level_log").info("centerig in loop mode / n_points %s / incr %s " % (n_points, phi_incr))

    try:  
        # OBTAIN CLICKS
        while True:
            USER_CLICKED_EVENT = gevent.event.AsyncResult()
            user_info = USER_CLICKED_EVENT.get()
            if user_info == "abort":
                abort_centring()
                return None
            else:   
                x,y = user_info


            USER_CLICKED_EVENT = gevent.event.AsyncResult()  
    
            X.append(x)
            Y.append(y)
            PHI.append(phi.getPosition())

            if len(X) == n_points:
                #PHI_LAST_ANGLE = phi.getPosition()
                #GO_ANGLE_START = PHI_ANGLE_START - PHI_LAST_ANGLE
                READY_FOR_NEXT_POINT.set()
                #phi.syncMoveRelative(GO_ANGLE_START)
                break
  
            phi.syncMoveRelative(phi_incr)
            READY_FOR_NEXT_POINT.set()
        
        logging.getLogger("user_level_log").info("returning PHI to initial position %s" % PHI_ANGLE_START)
        phi.move(PHI_ANGLE_START)

        #if sample_type.upper()== "PLATE":
            # make sure that final position is the same as initial one
        #    phi.syncMove(PHI_ANGLE_START)
        #else:
            #logging.getLogger("user_level_log").info("returning PHI to initial position %s" % PHI_ANGLE_START)
            
            #phi.syncMoveRelative(PHI_ANGLE_START-phi.getPosition())
        #    phi.syncMove(PHI_ANGLE_START)

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
        logging.getLogger("HWR").info("sample_centring:    / sampx: %s / sampy: %s / phiy: %s" % (sampx.getPosition(), sampy.getPosition(), phiy.getPosition()))
        logging.getLogger("HWR").info("sample_centring:    / pixels_per_mm: %s " % (pixelsPerMm_Hor))

        x_echantillon_real = x_echantillon/pixelsPerMm_Hor + sampx.getPosition()
        y_echantillon_real = y_echantillon/pixelsPerMm_Hor + sampy.getPosition()
        z_echantillon_real = z_echantillon/pixelsPerMm_Hor + phiy.getPosition()

        if phiy.getLimits() is not None:
            if (z_echantillon_real + phiy.getPosition() < phiy.getLimits()[0]*2) :
                logging.getLogger("HWR").info("sample_centring: phiy limits: %s" % str(phiy.getLimits()))
                logging.getLogger("HWR").info("sample_centring:  requiring: %s" % str(z_echantillon_real + phiy.getPosition()))
                logging.getLogger("HWR").error("sample_centring: loop too long")
                
                move_motors(SAVED_INITIAL_POSITIONS)
                raise Exception()

        centred_pos = SAVED_INITIAL_POSITIONS.copy()
        
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

        abort_centring()
        #return None

    except:
        import traceback
        logging.getLogger("HWR").error("sample_centring: Exception. %s" % traceback.format_exc())

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

def start_auto(camera,  centring_motors_dict,
               pixelsPerMm_Hor, pixelsPerMm_Ver, 
               beam_xc, beam_yc,
               chi_angle = 0,
               n_points = 3,
               msg_cb=None,
               new_point_cb=None):    
    global CURRENT_CENTRING

    phi, phiy, phiz, sampx, sampy = prepare(centring_motors_dict)

    CURRENT_CENTRING = gevent.spawn(auto_center, 
                                    camera, 
                                    phi, phiy, phiz,
                                    sampx, sampy, 
                                    pixelsPerMm_Hor, pixelsPerMm_Ver, 
                                    beam_xc, beam_yc, 
                                    chi_angle,
                                    n_points,
                                    msg_cb, new_point_cb)
    return CURRENT_CENTRING

def find_loop(camera, pixelsPerMm_Hor, chi_angle, msg_cb, new_point_cb):
  snapshot_filename = os.path.join(tempfile.gettempdir(), "mxcube_sample_snapshot.png")
  camera.takeSnapshot(snapshot_filename, bw=True)
  
  info, x, y = lucid.find_loop(snapshot_filename,IterationClosing=6)
  
  try:
    x = float(x)
    y = float(y)
  except Exception:
    return -1, -1
 
  if callable(msg_cb):
    msg_cb("Loop found: %s (%d, %d)" % (info, x, y))
  if callable(new_point_cb):
    new_point_cb((x,y))
        
  return x, y

def auto_center(camera, 
                phi, phiy, phiz,
                sampx, sampy, 
                pixelsPerMm_Hor, pixelsPerMm_Ver, 
                beam_xc, beam_yc, 
                chi_angle, 
                n_points,
                msg_cb, new_point_cb):
    imgWidth = camera.getWidth()
    imgHeight = camera.getHeight()
 
    #check if loop is there at the beginning
    i = 0
    while -1 in find_loop(camera, pixelsPerMm_Hor, chi_angle, msg_cb, new_point_cb):
        phi.syncMoveRelative(90)
        i+=1
        if i>4:
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
                                       pixelsPerMm_Hor, pixelsPerMm_Ver, 
                                       beam_xc, beam_yc, 
                                       chi_angle, 
                                       n_points)

      for a in range(n_points):
            x, y = find_loop(camera, pixelsPerMm_Hor, chi_angle, msg_cb, new_point_cb) 
            #logging.info("in autocentre, x=%f, y=%f",x,y)
            if x < 0 or y < 0:
              for i in range(1,18):
                #logging.info("loop not found - moving back %d" % i)
                phi.syncMoveRelative(5)
                x, y = find_loop(camera, pixelsPerMm_Hor, chi_angle, msg_cb, new_point_cb)
                if -1 in (x, y):
                    continue
                if x >=0:
                  if y < imgHeight/2:
                    y = 0
                    if callable(new_point_cb):
                        new_point_cb((x,y))
                    user_click(x,y,wait=True)
                    break
                  else:
                    y = imgHeight
                    if callable(new_point_cb):
                        new_point_cb((x,y))
                    user_click(x,y,wait=True)
                    break
              if -1 in (x,y):
                centring_greenlet.kill()
                raise RuntimeError("Could not centre sample automatically.")
              phi.syncMoveRelative(-i*5)
            else:
              user_click(x,y,wait=True)

      centred_pos = centring_greenlet.get()
      end(centred_pos)
                 
    return centred_pos


