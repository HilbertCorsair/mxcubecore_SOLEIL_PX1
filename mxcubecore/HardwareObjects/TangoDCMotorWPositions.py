"""Zoom / N-position actuator using a numeric Tango `position` readback and Zoom_N commands."""

from __future__ import annotations

import logging
import re
from enum import Enum

import gevent

from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore.HardwareObjects.abstract.AbstractActuator import AbstractActuator
from mxcubecore.HardwareObjects.TangoMotorWPositions import TangoMotorWPositions


class TangoDCMotorWPositions(TangoMotorWPositions):
    """
    Like TangoMotorWPositions but uses XML-defined ``position`` / ``state`` channels
    (polling) instead of a ``current_zoom`` attribute. Emits ``valueChanged`` and
    ``predefinedPositionChanged`` when the encoder readback moves so the web UI can sync.
    """

    def init(self):
        HardwareObject.init(self)
        self.tango_name = self.get_property("tangoname")
        self.tangoname = self.tango_name
        dp = self.get_property("delta")
        if dp is not None:
            try:
                self.delta = float(dp)
            except (TypeError, ValueError):
                pass

        AbstractActuator.init(self)

        self._add_position_commands()
        self.parse_xml_config()

        self.VALUES = Enum(
            "ValueEnum",
            {"P%d" % i: username for i, username in enumerate(self.positions.keys())},
        )
        self.position_names = [
            username.lower().replace(" ", "") for username in self.positions.keys()
        ]
        self.initialise_values()

        if self.default_value is not None and not isinstance(self.default_value, Enum):
            self.default_value = self.value_to_enum(self.default_value)
            self.update_value(self.default_value)

        self._last_predefined_username = None
        self.zoom = None

        pos_ch = self.get_channel_object("position")
        st_ch = self.get_channel_object("state")
        if st_ch is not None:
            st_ch.connect_signal("update", self._state_channel_update)
        if pos_ch is not None:
            pos_ch.connect_signal("update", self._position_channel_update)

        gevent.spawn(self._bootstrap_readback)

    def motstate_to_state(self, motstate):
        ms = str(motstate).upper()
        if ms in ("ON", "STANDBY", "IDLE"):
            return self.STATES.READY
        if ms in ("MOVING", "RUNNING"):
            return self.STATES.BUSY
        if ms in ("FAULT", "ALARM"):
            return self.STATES.FAULT
        if ms == "OFF":
            return self.STATES.OFF
        return self.STATES.UNKNOWN

    def _add_channels(self):
        """Channels are declared in XML (``position``, ``state``)."""

    def _bootstrap_readback(self):
        gevent.sleep(0.15)
        try:
            pos_ch = self.get_channel_object("position")
            if pos_ch is not None:
                self._position_channel_update(pos_ch.get_value())
        except Exception:
            logging.getLogger("HWR").exception(
                "%s: initial position readback failed", self.name()
            )

    def _nearest_username(self, pos):
        if pos is None:
            return None, None, False
        try:
            fpos = float(pos)
        except (TypeError, ValueError):
            return None, None, False
        best_u = None
        min_dist = float("inf")
        for username, pdata in self.positions.items():
            offs = float(pdata["offset"])
            dist = abs(offs - fpos)
            if dist < min_dist:
                min_dist = dist
                best_u = username
        valid = best_u is not None and min_dist <= float(self.delta)
        return best_u, fpos, valid

    def _state_channel_update(self, state=None):
        if state is None:
            ch = self.get_channel_object("state")
            state = ch.get_value() if ch is not None else None
        if state is None:
            return
        self.update_state(self.motstate_to_state(str(state)))

    def _position_channel_update(self, value):
        username, pos, valid = self._nearest_username(value)
        self._state_channel_update()
        if username is None:
            return
        ev = self.value_to_enum(username)
        if ev is self.VALUES.UNKNOWN:
            return
        self.zoom = None
        # Always push nearest discrete zoom so the web UI tracks readback (even
        # while moving or just outside `delta`); avoids stale label until reload.
        self.update_value(ev)
        if valid and username != self._last_predefined_username:
            self._last_predefined_username = username
            self.emit("predefinedPositionChanged", (username, pos))

    def get_value(self):
        pos_ch = self.get_channel_object("position")
        if pos_ch is None:
            return super().get_value()
        username, _, valid = self._nearest_username(pos_ch.get_value())
        if valid and username is not None:
            ev = self.value_to_enum(username)
            if ev is not self.VALUES.UNKNOWN:
                return ev
        return self.VALUES.UNKNOWN

    def get_state(self):
        st_ch = self.get_channel_object("state")
        if st_ch is not None:
            try:
                return self.motstate_to_state(str(st_ch.get_value()))
            except Exception:
                logging.getLogger("HWR").exception(
                    "%s: get_state from Tango failed", self.name()
                )
        return self.STATES.UNKNOWN

    def _set_value(self, value):
        username = value.value if hasattr(value, "value") else value
        if username not in self.positions:
            raise ValueError("Invalid zoom value %r" % (value,))
        norm = username.lower().replace(" ", "")
        self.goto_position(norm)
        # Encoder readback lags behind the Tango command; notify UIs immediately.
        self.update_value(value)

    def goto_position(self, name, args=None):
        logging.getLogger("HWR").debug(
            "TangoDCMotorWPositions (%s) move to %s", self.name(), name
        )
        zoom_pos = re.sub(r"zoom(\d{1,2})", r"Zoom_\1", name)
        _cmd = self._cmds_menu.get(zoom_pos, None)
        if _cmd is None:
            logging.getLogger("HWR").warning(
                "%s: no Tango command for position name %r -> %r",
                self.name(),
                name,
                zoom_pos,
            )
            return
        _cmd()

    def validate_value(self, value):
        if hasattr(value, "value"):
            return value.value in self.positions
        return False

    def force_emit_signals(self):
        AbstractActuator.force_emit_signals(self)
        try:
            pos_ch = self.get_channel_object("position")
            if pos_ch is not None:
                self._position_channel_update(pos_ch.get_value())
            else:
                self.re_emit_values()
        except Exception:
            logging.getLogger("HWR").exception(
                "%s: force_emit_signals failed", self.name()
            )
