
from TangoMotor import TangoMotor

import logging

class TangoMotorWPositions(TangoMotor):
    """Used soley for zoom to specify fixed zoom positions"""

    def __init__(self, name):
        TangoMotor.__init__(self, name)

    def _init(self):

        self.focus_ho = self.get_object_by_role("focus_motor")

        TangoMotor._init(self)

    def init(self):

        self.positions = {}
        self.position_names = []

        self.delta = self.get_property('delta', 5)

        all_positions = self['positions']

        for one_position in all_positions:
            username = one_position.get_property('username')
            values = {}

            for pty in one_position.get_properties():
                values[pty] = one_position.get_property(pty)

            calib_data = one_position['calibrationData']
            for pty in calib_data.get_properties():
                values[pty] = calib_data.get_property(pty)

            self.positions[username] = values


        self.position_names = list(self.positions.keys())
        #self.position_names.sort(key = self.cmp_positions )

        #print(self.position_names)
        #exit()

    def position_do_changed(self):
        self.check_predefined()

    def cmp_positions(self, x, y):
        return int(round(self.positions[x]['offset'] - self.positions[y]['offset']))

    def check_predefined(self):
        name, pos, valid = self.get_current_name()
        #logging.info('TangoMotorWPos (%s). New position, name (%s) pos=%s' % (self.name(), name, pos))
        self.emit('predefinedPositionChanged', name, pos, valid)


    def get_current_name(self):
        pos = self.get_position()

        min_dist = 1000.0
        curr_name = ''
        valid = False

        for name in self.position_names:
            offs = self.positions[name]['offset']
            dist = abs(offs - pos)
            if dist < min_dist:
                min_dist = dist
                curr_name = name

        if curr_name:
            if min_dist <= self.delta:
                valid = True

        return curr_name, pos, valid

    getCurrentPositionName = get_current_name

    def get_current_offset(self):
        name, pos, valid = self.get_current_name()

        if valid:
            offset = self.positions[name]['offset']
            return offset
        else:
            return None

    getCurrentOffset = get_current_offset

    def get_properties(self, name=None):
        if name is None:
            name, pos, valid = self.get_current_name()
            if not valid:
                return None

        values = {}

        if name in self.positions:
            values = self.positions[name]

        return values

    getCurrentPositionProperties = get_properties

    def get_positions(self):
        return self.position_names

    getPredefinedPositionsList = get_positions

    def goto_position(self,name):
        logging.getLogger().debug("TangoMotorWPositions (%s) / Moving to posname %s" % ( self.name(), name))

        if name in self.position_names:
            props = self.get_properties(name)
        else:
            logging.getLogger('HWR').exception('TangoMotorWPositions(%s). Cannot move : invalid position name %s.' % (self.name(), name))
            return

        try:
            abspos = props['offset']
        except:
            return

        focus_pos = None
        if self.focus_ho is not None:
            try:
                focus_pos = props['focus_offset']
            except:
                import traceback
                logging.getLogger('HWR').exception('TangoMotorWPositions(%s). Cannot move focus' % self.name())
                logging.getLogger('HWR').exception( traceback.format_exc())

        try:
           TangoMotor.move(self, abspos)
           if focus_pos is not None:
               self.focus_ho.move(focus_pos)
        except:
            import traceback
            logging.getLogger('HWR').debug("TangoMotorWPositions (%s) Error moving to offset. %s" % \
                   (self.name(), abspos, ))
            logging.getLogger('HWR').debug(traceback.format_exc())

    moveToPosition = goto_position

def test_hwo(hwo):
    print (hwo.get_position())
    print (hwo.get_current_name())
    print (hwo.get_current_offset())
    print (hwo.get_properties())
    # goto_position('2')