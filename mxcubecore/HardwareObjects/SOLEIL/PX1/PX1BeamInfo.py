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

        #self.zoomMotor = self.get_object_by_role("zoom")
        self.zoomMotor.init()
        self.current_zoom = self.zoomMotor.get_value()
        #beam_size_slits = self.get_property("beam_size_slits")


        if self.zoomMotor is not None:
            self.connect(
                self.zoomMotor, "predefinedPositionChanged", self.zoomPositionChanged
            )
            print("zoomPositionChanged CALLED ")
            self.zoomPositionChanged()
        else:
            logging.getLogger().info("Zoom motor not defined")
      
        self.beam_definer = self.get_object_by_role("beam_definer")

    def zoomPositionChanged(self, name=None, offset=None):
        if not self.current_zoom:
            self.current_zoom = self.zoomMotor.get_value()

        zoom_props = self.zoomMotor.positions[self.current_zoom]["calibrationData"]

        if "beamPositionX" in zoom_props:
            self.beam_position = [
                zoom_props["beamPositionX"],
                zoom_props["beamPositionY"],
            ]
            self.positionUpdated()
    
   
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
