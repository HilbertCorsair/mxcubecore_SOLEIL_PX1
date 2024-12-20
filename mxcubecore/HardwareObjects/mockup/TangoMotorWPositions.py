from mxcubecore.HardwareObjects.abstract.AbstractNState import AbstractNState
from enum import Enum
import xml.etree.ElementTree as ET
import PyTango
import logging
import gevent
import re

class TangoMotorWPositions(AbstractNState):
    """Used solely for zoom to specify fixed zoom positions"""

    def __init__(self, name):
        super().__init__(name)
        self.focus_ho = None
        self.positions = {}
        self.position_names = []
        self.delta = 5
        self._last_position = None
        self._zoom_command = None
        self._cmds_menu = {}

    @property
    def zoom_command(self):
        return self._zoom_command

    @zoom_command.setter
    def zoom_command(self, value):
        self._zoom_command = value

    @property
    def last_position(self):
        return self._last_position

    @last_position.setter
    def last_position(self, val):
        self._last_position = val

    # Adds a special type of command channel where the command is a variable (the zoom position)

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

        self.tango_name = self.get_property("tangoname")
        self._add_position_commands()
        self.parse_xml_config()

        # Create Enum for VALUES
        self.VALUES = Enum('ValueEnum', {name: name for name in self.positions.keys() })

        # position names on tha tango device are lowarcase without spaces
        self.position_names = [name.lower().replace(" ", '')for name in self.VALUES.__members__.keys()]
        self._add_channels()


    def _add_position_commands(self):
        for i in range(10):
            self.add_command(
                {"type": "tango", "name": f"Zoom_{i+1}", "tangoname": self.tangoname},
                f"Zoom_{i+1}",
            )
            self._cmds_menu[f"Zoom_{i+1}"] = getattr(self, f"Zoom_{i+1}")

    def _add_channels(self):
        self._chnState = self.add_channel(
            {
                "type": "tango",
                "name": "_chnState",
                "tangoname": self.tangoname,
                "polling": 300,
            },
            "State",
        )

        self._zoom_position = self.add_channel(
            {
                "type": "tango",
                "name": "current_zoom",
                "tangoname": self.tangoname,
                "polling": 300,
            },
            "current_zoom",
        )


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

        self.add_channel({'type':'tango', 'name' : '_pos_chan', 'tangoname': self.tangoname,}, "current_zoom")
        a = self.get_channel_object("_pos_chan").get_value()

        """Read the actuator position."""
        return self.get_channel_object("_pos_chan").get_value()  #self._nominal_value

    def _set_value(self, value):
        """Implementation of specific set actuator logic."""
        self.goto_position(value.name)

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
        return value.name in self.position_names

    def get_current_name(self):
        pos = self.get_value()

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


    def get_properties(self, name=None):
        pos = self.get_value()
        values = self.positions[pos] if not name else self.positions[name]

        return values


    def get_positions(self):
        return self.position_names


    def goto_position(self, name, args = None):
        logging.getLogger().debug("TangoMotorWPositions (%s) / Moving to posname %s" % (self.name(), name))

        import re
        pattern = r'zoom(\d{1,2})'
        zoom_pos = re.sub(pattern, r'Zoom_\1', name)

        _cmd = self._cmds_menu.get(zoom_pos, None)
        _cmd()

        try:
            self.update_value(name)
        except:
            import traceback
            logging.getLogger('HWR').debug("TangoMotorWPositions (%s) Error moving to offset. %s" % (self.name(), name))
            logging.getLogger('HWR').debug(traceback.format_exc())

    #moveToPosition = goto_position