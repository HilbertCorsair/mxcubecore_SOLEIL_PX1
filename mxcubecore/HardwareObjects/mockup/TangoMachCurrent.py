from HardwareRepository import HardwareRepository

from mxcubecore.BaseHardwareObjects import HardwareObject
import logging

from PyTango import DeviceProxy

class TangoMachCurrent(HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self.opmsg = ''
        self.fillmode = ''

    def init(self):
        self.device = DeviceProxy(self.get_property("tangoname"))
        self.current_threshold = self.get_property("current_threshold", 3)

        self.set_is_ready(True)
        state_chan = self.get_channel_object("current")  # utile seulement si statechan n'est pas defini dans le code
        state_chan.connect_signal("update", self.value_changed)

    def updated_value(self):
        try:
            mach = self.get_current()
            lifetime = self.get_life_time()
            fillmode = self.get_fill_mode() + " filling"
            opmsg = self.get_message()
            return mach, opmsg, fillmode, lifetime
        except:
            return None

    def get_current_threshold(self):
        return self.current_threshold

    def get_current(self):
        try:
            mach = self.device.read_attribute("current").value
            return mach
        except:
            return -1

    def get_life_time(self):
        try:
            lifetime = self.device.read_attribute("lifetime").value
            return lifetime
        except:
            return -1

    def get_message(self):
        try:
            opmsg = self.device.read_attribute("operatorMessage").value
            # make sure it is unicode compatible
            try:
                opmsg = opmsg.encode('ascii', errors='ignore')
            except UnicodeError:
                opmsg = "----"
            except:
                opmsg = "mach message"
            return opmsg
        except:
            return "cannot read operator message"

    def get_fill_mode(self):
        try:
            fillmode = self.device.read_attribute("fillingMode").value
            return fillmode
        except:
            return "unknown"

    # Keeping aliases for backward compatibility
    getCurrent = get_current
    getMessage = get_message
    getFillMode = get_fill_mode
    getLifeTime = get_life_time

    def value_changed(self, value):
        mach = value
        opmsg = None
        fillmode = None
        lifetime = None
        try:
            lifetime = self.device.read_attribute("lifetime").value
            opmsg = self.device.read_attribute("operatorMessage").value
            opmsg = opmsg.strip()
            opmsg = opmsg.replace(': Faisceau disponible', ':\nFaisceau disponible')
            fillmode = self.device.read_attribute("fillingMode").value + " filling"
            fillmode = fillmode.strip()
            lifetime = "Lifetime: %3.2f h" % lifetime
            
        except AttributeError:
            logging.getLogger("HWR").info("%s: AAA AttributeError machinestatus not responding, %s", self.name(), '')
    
        except:
            logging.getLogger("HWR").error("%s: BBB machinestatus not responding, %s", self.name(), '')
            pass
        
        if opmsg and opmsg != self.opmsg:
            self.opmsg = opmsg
            logging.getLogger('HWR').info("<b>"+self.opmsg+"</b>")
        
        self.emit('valueChanged', (mach, str(opmsg), str(fillmode), str(lifetime)))

"""
def test():
    import os
    hwr_directory = os.environ["XML_FILES_PATH"]

    hwr = HardwareRepository.HardwareRepository(os.path.abspath(hwr_directory))
    hwr.connect()

    conn = hwr.getHardwareObject("/mach")

    print "Machine current is ", conn.getCurrent()
    print "Life time is ", conn.getLifeTime()
    print "Fill mode is ", conn.getFillMode()
    print "Message is ", conn.getMessage()


if __name__ == '__main__':
   test()

"""