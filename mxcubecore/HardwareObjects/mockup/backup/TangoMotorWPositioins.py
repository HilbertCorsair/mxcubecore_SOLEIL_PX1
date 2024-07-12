from mxcubecore.HardwareObjects.abstract.AbstractNState import AbstractNState
from enum import Enum
import xml.etree.ElementTree as ET

import logging

class TangoMotorWPositions(AbstractNState):
    """Used solely for zoom to specify fixed zoom positions"""

    def __init__(self, name):
        super().__init__(name)
        self.focus_ho = None
        self.positions = {}
        self.position_names = []
        self.delta = 5
        self._nominal_value = None
        self.last_position = None

    def parse_xml_config(self):
        source = ET.fromstring(self.xml_source())
        for p in source.findall(".//position"):
            user = p.find("username").text
            position_data = {
                "offset" : float(p.find("offset").text),
                "focus_offset": float(p.find("focus_offset").text),
                "lightLevel" : int(p.find("lightLevel").text),
                "calibrationData" : {
                    "pixelsPerMmY": int(p.find("calibrationData/pixelsPerMmY").text),
                    "pixelsPerMmZ": int(p.find("calibrationData/pixelsPerMmZ").text),
                    "beamPositionX": int(p.find("calibrationData/beamPositionX").text),
                    "beamPositionY": int(p.find("calibrationData/beamPositionY").text),

                }


            }
            self.positions[user] = position_data


    def init(self):
        super().init()

        self.focus_ho = self.get_object_by_role("focus_motor")
        self.delta = self.get_property('delta', 5)

        self.parse_xml_config()

        # Create Enum for VALUES
        self.VALUES = Enum('ValueEnum', {name: name for name in self.positions.keys() })

        print("\n---|ZooM InitiateD|--- !\n")

    def initialise_values(self):
        values_dict = dict (**{item.name: item.value for item in self.VALUES })
        values_dict.update(
            {
                        "MOVING":"MOVING",
                        "DISABLE":"DISABLE",
                        "STANDBY": "STANDBY",
                        "FAULT" :"FAULT",

            }

        )
        for key, val in values_dict.items():
            if isinstance (val, (tuple, list)):
                values_dict.update({key: val[1]})
            else:
                values_dict.update( {key : val} )

        self.VALUES = Enum("ValueEnum", values_dict)


    def motstate_to_state(self, motstate):

        motstate = str(motstate)

        if motstate == "ON" or motstate in self.positions.keys():
            state = self.STATES.READY
        elif motstate == "MOVING":
            state = self.STATES.BUSY
        elif motstate == "FAULT":
            state = self.STATES.FAULT
        elif motstate == "OFF":
            state = self.STATES.OFF
        else:
            state = self.STATES.UNKNOWN

        return state

    def motor_state_changed(self, state=None):
        if state is None:
            state = self.chan_state.get_value()

        self.update_state(self.motstate_to_state(state))

    def set_ready(self):
        self.update_state(self.STATES.READY)

    def is_moving(self):
        return ( (self.get_state() == self.STATES.BUSY ) or (self.get_state() == self.SPECIFIC_STATES.MOVING))

    def get_value(self):
        """Read the actuator position."""
        return self._nominal_value

    def _set_value(self, value):
        """Implementation of specific set actuator logic."""
        self.goto_position(value)

    def get_state(self):
        val = self.get_value()
        return self.motstate_to_state(val)  # Assuming it's always ready for this example

    def abort(self):
        """Stops motor movement"""
        # Implement abort logic if necessary
        pass

    def get_limits(self):
        """Return actuator low and high limits."""
        return (1, len(self.positions.keys()))

    def validate_value(self, value):
        """Check if the value is one of the predefined values."""
        return value in self.position_names

    def get_current_name(self):
        pos = self._nominal_value

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

    def goto_position(self, name):
        logging.getLogger().debug("TangoMotorWPositions (%s) / Moving to posname %s" % (self.name(), name))

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
                logging.getLogger('HWR').exception(traceback.format_exc())

        try:
            self._nominal_value = abspos
            self.update_value(abspos)
            if focus_pos is not None:
                self.focus_ho.move(focus_pos)
        except:
            import traceback
            logging.getLogger('HWR').debug("TangoMotorWPositions (%s) Error moving to offset. %s" % (self.name(), abspos))
            logging.getLogger('HWR').debug(traceback.format_exc())

    moveToPosition = goto_position