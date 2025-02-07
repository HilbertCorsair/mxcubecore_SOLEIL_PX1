
import logging
import time

from typing import OrderedDict as TOrderedDict
from typing import (
    Tuple,
    Type,
    Union,
)
from Beamline import Beamline

from mxcubecore.BaseHardwareObjects import HardwareObject
log = logging.getLogger("HWR")

class PX1Beamline(Beamline, HardwareObject):

    def __init__(self, name):
        Beamline.__init__(self, name)
        HardwareObject.__init__(self, name)

        #self.__objects_names: list[Union[str, None]] = []

    def get_run_processing_default(self):
        return self.autoprocessing_hwobj.get_run_processing_default()
    
    def name(self):
        return self.name



    def close_and_quit(self):

        log.debug("PX1Beamline. closing")

        try:
            if self.mailer_hwobj is not None:
                 latest_user = str( self.session_hwobj.get_latest_projuser() )
                 txt = "MXCuBE session has been closed and closing procedure started.\nLatest login user was: %s" % latest_user
                 self.mailer_hwobj.send_msg("MXCuBE Session closed for user=%s" % latest_user,  txt)

            if self.light_hwobj is not None:
                log.debug("PX1Beamline. moving light level to 0")
                self.light_hwobj.move(0)

            if self.environment_hwobj is not None:
                log.debug("PX1Beamline. moving px1environment to default phase ")
                self.environment_hwobj.goto_default_phase()
            
            time.sleep(1) # allow some time on quit to allow actions to be triggered
        except BaseException as e:
            import traceback
            log.error("Error while launching beamline close procedure. %s" % str(e))
            log.error( traceback.format_exc())

        return "PX1 beamline close procedure launched"
       
