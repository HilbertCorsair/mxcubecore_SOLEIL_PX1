import logging

from mxcubecore import HardwareRepository as HWR
from mxcubecore.HardwareObjects.BeamInfo import BeamInfo

"""
XML example file
<object class="ESRF.ESRFBeamInfo">
  <defaultBeamDivergence></defaultBeamDivergence>
  <object role="camera" hwrid="/prosilica_md2"/>
  <object role="aperture" hwrid="/udiff_aperturemot"/>
  <object role="diffractometer" hwrid="/udiff" />
  <!-- Positions and slits format: X Y -->
  <beam_position>322 243</beam_position>
  <beam_size_slits>0.04 0.04</beam_size_slits>
  <beam_divergence_vertical>6.5</beam_divergence_vertical>
  <beam_divergence_horizontal>104</beam_divergence_horizontal>
</object>
"""


class PX1BeamInfo(BeamInfo):
    def __init__(self, *args):
        BeamInfo.__init__(self, *args)
        self.beam_position = (0, 0)




    def init(self):
        self.chan_beam_size_microns = None
        self.chan_beam_shape_ellipse = None
        BeamInfo.init(self)

        self.zoomMotor = self.get_object_by_role("zoom")
        gv = self.zoomMotor.get_value() if self.zoomMotor else None
        self.current_zoom = getattr(gv, "value", gv)
        #beam_size_slits = self.get_property("beam_size_slits")


        if self.zoomMotor is not None:
            self.connect(
                self.zoomMotor, "predefinedPositionChanged", self.zoomPositionChanged
            )
            self.zoomPositionChanged()
        else:
            logging.getLogger().info("Zoom motor not defined")

        """
        if beam_size_slits:
            self.beam_size_slits = tuple(map(float, beam_size_slits.split()))

        beam_position = self.get_property("beam_position")
        if beam_position:
            self.beam_position = tuple(map(float, beam_position.split()))
        else:
            logging.getLogger("HWR").warning(
                "ESRFBeamInfo: " + "beam position not configured"
            )
        self.difrractometer_hwobj = self.get_object_by_role("difrractometer")
        self.flux = self.get_object_by_role("flux")
        """

        self.beam_definer = self.get_object_by_role("beam_definer")

    def zoomPositionChanged(self, name=None, offset=None):
        if name:
            self.current_zoom = name
        elif self.zoomMotor is not None:
            gv = self.zoomMotor.get_value()
            self.current_zoom = getattr(gv, "value", gv)
        if (
            not self.current_zoom
            or self.current_zoom not in self.zoomMotor.positions
        ):
            return
        zoom_props = self.zoomMotor.positions[self.current_zoom]["calibrationData"]

        if "beamPositionX" in zoom_props:
            self.beam_position = [
                zoom_props["beamPositionX"],
                zoom_props["beamPositionY"],
            ]
            self.positionUpdated()

    """def get_beam_position(self):
        if self.beam_position == (0, 0):
            try:
                self.beam_position = HWR.beamline.diffractometer.beam.get_value()
            except AttributeError:
                self.beam_position = (
                    HWR.beamline.sample_view.camera.get_width() / 2,
                    HWR.beamline.sample_view.camera.get_height() / 2,
                )
        return self.beam_position"""

    def positionUpdated(self):
        self.emit("beamPosChanged", (self.beam_position,))


    def get_beam_position(self):
        return self.beam_position

    def set_beam_position(self, beam_x, beam_y):
        return

    def evaluate_beam_info(self, *args):
        BeamInfo.evaluate_beam_info(self, *args)
        self.beam_info_dict["shape"] = "ellipse"
        return self.beam_info_dict
