import logging
import time
from typing import Optional, List, Dict, Any

import gevent
from mxcubecore.BaseHardwareObjects import HardwareObject
from mxcubecore.Command.Tango import DeviceProxy

logger = logging.getLogger("HWR")

class PX1TangoLight(HardwareObject):
    """
    Hardware object for controlling Tango light system.
    Handles light states, intensity, and coordination with environment settings.
    """
    
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.current_state: str = "unknown"
        self.states: List[str] =["out", "in"]
        self.inversed: bool = False
        self.attrchan: Optional[Any] = None
        self.set_in: Optional[Any] = None
        self.set_out: Optional[Any] = None
        self.px1env_hwo: Optional[Any] = None
        self.light_hwo: Optional[Any] = None
        self.zoom_hwo: Optional[Any] = None

    def init(self) -> None:
        """Initialize the hardware object and set up communication channels."""
        self._setup_channels()
        self._setup_commands()
        self._setup_hardware_objects()
        self._configure_state_mapping()
        self._set_ready()

    def _setup_channels(self) -> None:
        """Set up communication channels and their signal connections."""
        self.attrchan = self.get_channel_object("attributeName")
        import pdb
        pdb.set_trace()
        if self.attrchan:
            self.attrchan.connect_signal("update", self._value_changed)
            self.attrchan.connect_signal("connected", self._set_ready)
            self.attrchan.connect_signal("disconnected", self._set_ready)

    def _setup_commands(self) -> None:
        """Set up command objects and their signal connections."""
        self.set_in = self.get_command_object("set_in")
        self.set_out = self.get_command_object("set_out")
        '''
        if self.set_in:
            self.set_in.connect_signal("connected", self._set_ready)
            self.set_in.connect_signal("disconnected", self._set_ready)
        '''

    def _setup_hardware_objects(self) -> None:
        """Set up references to related hardware objects."""
        self.px1env_hwo = self.get_object_by_role("px1environment")
        self.light_hwo = self.get_object_by_role("intensity")
        self.zoom_hwo = self.get_object_by_role("zoom")
        
        if self.zoom_hwo:
            self.connect(self.zoom_hwo, "predefinedPositionChanged", self._zoom_changed)

    def _configure_state_mapping(self) -> None:
        """Configure state mapping based on inversed property."""
        try:
            self.inversed = self.get_property("inversed")
        except Exception:
            self.inversed = False
            logger.warning("Failed to get 'inversed' property, using default: False")

        if self.inversed:
            self.states = ["in", "out"]

    def _set_ready(self) -> None:
        """Set the ready state based on channel connection."""
        if self.attrchan:
            self.light_hwo.set_is_ready(self.attrchan.is_connected())

    def connect_notify(self, signal: str) -> None:
        """Handle connection notification and update value if ready."""
        if self.is_ready() and self.attrchan:
            self._value_changed(self.attrchan.get_value())

    def _value_changed(self, value: bool) -> None:
        """
        Handle state value changes and emit corresponding signals.
        
        Args:
            value: Boolean indicating the new state
        """
        self.current_state = self.states[1] if value else self.states[0]
        self.emit('wago_state_changed', (self.current_state,))

    def get_state(self) -> str:
        """Get current state of the light system."""
        return self.current_state

    def move_in(self) -> None:
        """Move light system to 'in' position."""
        self.set_in()

    def move_out(self) -> None:
        """Move light system to 'out' position."""
        self.set_out()

    def set_in(self) -> None:
        """Set light system to 'in' position with proper phase checking."""
        if not self._ensure_sample_view_phase():
            logger.error("Failed to set sample view phase")
            return
            
        self._adjust_light_level()

    def set_out(self) -> None:
        """Set light system to 'out' position."""
        self._set_ready()
        if not self.is_ready():
            logger.warning("Device not ready for set_out operation")
            return

        if self.inversed:
            self.set_in()
        else:
            if self.light_hwo:
                self.light_hwo.move(0)
            if self.set_out:
                self.set_out()

    def _ensure_sample_view_phase(self) -> bool:
        """
        Ensure system is in sample view phase.
        
        Returns:
            bool: True if successfully in sample view phase, False otherwise
        """
        if not self.px1env_hwo or self.px1env_hwo.is_phase_visu_sample():
            return True

        self.px1env_hwo.goto_sample_view_phase()
        start_time = time.time()
        timeout = 20  # seconds
        
        while not self.px1env_hwo.is_phase_visu_sample():
            time.sleep(0.1)
            if time.time() - start_time > timeout:
                logger.error(f"Timeout waiting for sample view phase after {timeout}s")
                return False
        
        return True

    def _zoom_changed(self, position_name: str, pos: Any, valid: bool) -> None:
        """
        Handle zoom position changes.
        
        Args:
            position_name: Name of the position
            pos: Position value
            valid: Whether the position is valid
        """
        if not valid:
            return
            
        if self.current_state == "in":
            self._adjust_light_level()

    def _adjust_light_level(self) -> None:
        """Adjust light level based on zoom position properties."""
        if not all([self.zoom_hwo, self.light_hwo]):
            logger.debug("Cannot adjust light level - missing hardware objects")
            return

        try:
            props = self.zoom_hwo.get_current_position_properties()
            if 'lightLevel' not in props:
                return

            light_level = float(props['lightLevel'])
            current_light = self.light_hwo.get_position()
            
            if current_light != light_level:
                logger.debug(f"Setting light level to {light_level}")
                self.light_hwo.move(light_level)
                
        except Exception as e:
            logger.exception("Error adjusting light level: %s", str(e))