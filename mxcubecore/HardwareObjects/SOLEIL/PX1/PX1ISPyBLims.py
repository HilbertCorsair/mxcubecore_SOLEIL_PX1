import logging

from mxcubecore import HardwareRepository as HWR
from mxcubecore.HardwareObjects.abstract.PyISPyBDataAdapter import PyISPyBDataAdapter
from mxcubecore.HardwareObjects.abstract.PyISPyBRestClient import PyISPyBRestClient
from mxcubecore.HardwareObjects.ProposalTypeISPyBLims import ProposalTypeISPyBLims
from mxcubecore.model import queue_model_objects as qmo
from mxcubecore.model.lims_session import LimsSessionManager
from mxcubecore.model.lims_session import Session as lims_Session

PROPOSAL_CODE = "mx"


class PX1ISPyBLims(ProposalTypeISPyBLims):
    """PyISPyB (REST) client; the password is checked by SOLEILSession."""

    def __init__(self, name):
        super().__init__(name)
        self.login_type = "Proposal"
        self.adapter = None
        self.user_name = None
        self.session_manager = None
        self.icat_client = None
        self.samples = []

    def init(self):
        # super().init() is not called: it builds the SOAP adapter
        self.beamline_name = "PROXIMA1"  # self.get_property("beamline_name")
        self.site = self.get_property("site")
        self.adapter = self._create_data_adapter()
        self.ldapConnection = self.get_object_by_role("ldapServer")

    def _create_data_adapter(self):
        pyispyb_rest_root = self.get_property("pyispyb_rest_root")
        if not pyispyb_rest_root:
            raise ValueError("lims: pyispyb_rest_root is not configured")
        client = PyISPyBRestClient(
            rest_root=pyispyb_rest_root,
            keycloak_url=self.get_property("keycloak_url"),
            grant_type=self.get_property("grant_type"),
            client_id=self.get_property("client_id"),
            client_secret=self.get_property("client_secret"),
        )
        proxy_address = self.get_property("proxy_address")
        if proxy_address:
            client.update_proxies({"http": proxy_address, "https": proxy_address})
        return PyISPyBDataAdapter(client, self.beamline_name)

    def is_connected(self):
        return (
            self.session_manager is not None
            and self.session_manager.active_session is not None
        )

    def store_data_collection(self, mx_collection, bl_config=None):
        if hasattr(bl_config, "_asdict"):
            # BeamlineConfig namedtuple; undulators are objects, not JSON
            bl_config = bl_config._asdict()
            bl_config.pop("undulators", None)
        return self.adapter.store_data_collection(mx_collection, bl_config)

    def get_samples(self, lims_name=None):
        session = self.session_manager.active_session
        self.samples = self.adapter.get_samples_by_code_and_number(
            session.code, session.number
        )
        logging.getLogger("HWR").debug(
            "get_samples. %s samples for %s", len(self.samples), session.proposal_name
        )
        return self.samples

    def _todays_session(self, number) -> lims_Session:
        """The session running now, created in ISPyB if there is none."""
        sessions = self.adapter.get_sessions_by_code_and_number(
            code=PROPOSAL_CODE, number=number, beamline=self.beamline_name
        ).sessions
        for session in sessions:
            if session.is_scheduled_time:
                return session
        logging.getLogger("HWR").info(
            "No session now for %s%s, creating one", PROPOSAL_CODE, number
        )
        proposal = self.adapter.find_proposal(PROPOSAL_CODE, number)
        session = self.adapter.create_session(proposal)
        session.title = proposal.title
        return session

    def login(self, pid):
        self.user_name = pid
        session = self._todays_session(pid)
        session.start_date = session.start_datetime.strftime("%Y%m%d")
        session.start_time = session.start_datetime.strftime("%H:%M:%S")
        session.end_date = session.end_datetime.strftime("%Y%m%d")
        session.end_time = session.end_datetime.strftime("%H:%M:%S")
        # The proposal number (not its database id): SOLEILSession builds the
        # RAW_DATA path from it
        session.number = str(pid)
        session.proposal_name = f"{PROPOSAL_CODE}{pid}"
        session.beamline_name = self.beamline_name
        session.title = session.title or session.proposal_name
        session.is_rescheduled = False
        session.is_scheduled_time = True
        session.is_scheduled_beamline = True

        self.session_manager = LimsSessionManager(
            sessions=[session], active_session=session
        )
        return self.session_manager

    def get_lims_name(self):
        return [{"name": "ISPyB", "description": "PyISPyB"}]

    def set_active_session_by_id(self, session_id: str) -> lims_Session:
        for session in self.session_manager.sessions:
            if session.session_id == session_id:
                self.session_manager.active_session = session
                return session
        raise Exception(f"no session with ID {session_id} found")

    def get_default_prefix(self, sample_data, generic_name=False):
        if isinstance(sample_data, dict):
            sample = qmo.Sample()
            sample.code = sample_data.get("code", "")
            sample.name = sample_data.get("sampleName", "")
            sample.name = sample.name.replace(":", "-")
            sample.location = sample_data.get("location", "").split(":")
            sample.lims_id = sample_data.get("limsID", -1)
            sample.crystals[0].protein_acronym = sample_data.get("proteinAcronym", "")
        else:
            sample = sample_data
        return HWR.beamline.session.get_default_prefix(sample, generic_name)

    def path_to_ispyb(self, path):
        return HWR.beamline.session.path_to_ispyb(path)

    def prepare_collect_for_lims(self, mx_collect_dict):
        """Swap the snapshot paths for their ISPyB paths, then update ISPyB.

        mx_collect_dict is modified in place; the local paths are kept as
        xtalSnapshotOrigPath<n>.
        """
        for i in range(1, 5):
            prop = f"xtalSnapshotFullPath{i}"
            path = mx_collect_dict.get(prop)
            if not path:
                continue
            try:
                mx_collect_dict[prop] = self.path_to_ispyb(path)
                mx_collect_dict[f"xtalSnapshotOrigPath{i}"] = path
            except Exception:
                logging.getLogger("HWR").exception("prepare_collect_for_lims %s", prop)

        self.update_data_collection(mx_collect_dict)

    def prepare_image_for_lims(self, image_dict):
        for prop in ["jpegThumbnailFileFullPath", "jpegFileFullPath"]:
            try:
                image_dict[prop] = self.path_to_ispyb(image_dict[prop])
            except Exception:
                pass
