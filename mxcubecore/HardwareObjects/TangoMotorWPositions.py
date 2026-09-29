from mxcubecore.HardwareObjects.abstract.AbstractNState import AbstractNState
from enum import Enum
import xml.etree.ElementTree as ET
import PyTango
import logging
import gevent
import re
import numbers

class TangoMotorWPositions(AbstractNState):
    """Used solely for zoom to specify fixed zoom positions"""

    def __init__(self, name):
        super().__init__(name)
        self.focus_ho = None
        self.positions = {}
        self.position_names = []
        self.delta = 5
        #self._last_position = None
        self._zoom_command = None
        self._cmds_menu = {}
        # Last position name read back from the hardware (kept while moving).
        self._last_name = None

    @property
    def zoom_command(self):
        return self._zoom_command

    @zoom_command.setter
    def zoom_command(self, value):
        self._zoom_command = value

    """@property
    def last_position(self):
        return self._last_position

    @last_position.setter
    def last_position(self, val):
        self._last_position = val"""

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

        # The hardware tells the UI where the zoom is, whoever moved it.
        self._zoom_position.connect_signal("update", self._position_update)
        self._chnState.connect_signal("update", self._state_update)

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

    '''def get_value(self):
        val = self.get_channel_object("zoom_position").get_value()
        """Read the actuator position."""
        return val  #self._nominal_value'''
    def name_from_readback(self, raw):
        """The position name for a current_zoom reading, None between positions.

        Accepts a name ('zoom2', 'Zoom 2'), an integer position index (1-based)
        or a float encoder offset (matched within `delta`).
        """
        if raw is None or isinstance(raw, bool):
            return None

        names = list(self.positions)

        def norm(v):
            return str(v).lower().replace(" ", "").replace("_", "")

        if isinstance(raw, str):
            match = [n for n in names if norm(n) == norm(raw)]
            if match:
                return match[0]
            try:
                raw = float(raw)
            except ValueError:
                return None

        if isinstance(raw, numbers.Integral):
            return names[raw - 1] if 1 <= raw <= len(names) else None

        try:
            raw = float(raw)
        except (TypeError, ValueError):
            return None
        for name in names:
            if abs(raw - self.positions[name]["offset"]) <= self.delta:
                return name
        return None

    def get_value(self):
        """The position the hardware reports (the last one while moving)."""
        try:
            name = self.name_from_readback(self._zoom_position.get_value())
        except Exception:
            logging.getLogger("HWR").exception("%s: cannot read the zoom", self.name())
            name = None
        if name is not None:
            self._last_name = name
        return self._last_name

    def _position_update(self, raw=None):
        name = self.name_from_readback(raw)
        if name is not None:
            self._last_name = name
            self.update_value(name)

    def _state_update(self, state=None):
        self.update_state(self.motstate_to_state(str(state)))

    def _set_value(self, value):
        """Implementation of specific set actuator logic."""
        self.goto_position(value.name)

    def get_state(self):
        try:
            return self.motstate_to_state(str(self._chnState.get_value()))
        except Exception:
            return self.STATES.UNKNOWN

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
        raw = self._zoom_position.get_value()
        name = self.name_from_readback(raw)
        return name or "", raw, name is not None

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
        if _cmd is None:
            raise ValueError("%s: no command for zoom position %r" % (self.name(), name))
        # The readback (_position_update) reports the new position once the
        # motor is there; nothing is announced before that.
        _cmd()

    #moveToPosition = goto_position