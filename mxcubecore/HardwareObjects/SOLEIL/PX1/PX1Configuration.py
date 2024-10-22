
import os
from mxcubecore.BaseHardwareObjects import HardwareObjectMixin
from mxcubecore import HardwareRepository as HWR

class PX1Configuration(HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self.use_edna_value = None
        self.pin_length = None
        self.centring_points = None
        self.centring_phi_incr = None
        self.centring_sample_type = None

    def init(self):
        self.use_edna_value = self.get_property("use_edna")
        self.pin_length = self.get_property("pin_length")
        self.centring_points = self.get_property("centring_points")
        self.centring_phi_incr = self.get_property("centring_phi_increment")
        self.centring_sample_type = self.get_property("centring_sample_type")
        
        print(f"LocalConfiguration has value sample_type={self.centring_sample_type}")

    def get_use_edna(self):
        return self.use_edna_value

    def set_use_edna(self, value):
        self.use_edna_value = value is True or value == "True"
        self.set_property("use_edna", self.use_edna_value)

    def get_pin_length(self):
        return self.pin_length

    def set_pin_length(self, value):
        self.pin_length = value
        self.set_property("pin_length", value)

    def get_centring_points(self):
        return int(self.centring_points)

    def set_centring_points(self, value):
        self.centring_points = int(value)
        self.set_property("centring_points", value)

    def get_centring_phi_increment(self):
        return float(self.centring_phi_incr)

    def set_centring_phi_increment(self, value):
        self.centring_phi_incr = float(value)
        self.set_property("centring_phi_increment", value)

    def get_centring_sample_type(self):
        return self.centring_sample_type

    def set_centring_sample_type(self, value):
        self.centring_sample_type = value
        self.set_property("centring_sample_type", value)

    def save(self):
        self.commit_changes()


if __name__ == '__main__':
    hwr_directory = os.environ["XML_FILES_PATH"]
    hwr = HWR(os.path.abspath(hwr_directory))
    hwr.connect()

    env = hwr.get_hardware_object("/px1configuration")

    print("PX1 Configuration")
    use_edna = env.get_use_edna()
    print(f"    use_edna {use_edna} / (type: {type(use_edna)})")
    print(f"    pin_length: {env.get_pin_length()}")
    print("    centring")
    print(f"       nb points: {env.get_centring_points()}")
    print(f"       phi incr: {env.get_centring_phi_increment()}")
    print(f"       sample type: {env.get_centring_sample_type()}")

    env.set_use_edna(False)
    env.set_pin_length(10)
    print(f"    use_edna: {env.get_use_edna()}")
    print(f"    pin_length: {env.get_pin_length()}")
    # env.save()