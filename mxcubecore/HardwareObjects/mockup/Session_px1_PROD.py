"""
Session hardware object.

Contains information regarding the current session and methods to
access and manipulate this information.
"""
import os
import time
import logging
from typing import List, Dict, Tuple, Optional

from HardwareRepository.BaseHardwareObjects import HardwareObject
import queue_model_objects_v1 as queue_model_objects

log = logging.getLogger("HWR")

class Session(HardwareObject):
    def __init__(self, name: str):
        super().__init__(name)
        self.synchrotron_name: Optional[str] = None
        self.beamline_name: Optional[str] = None
        self.session_id: Optional[int] = None
        self.proposal_code: Optional[str] = None
        self.proposal_number: Optional[str] = None
        self.proposal_id: Optional[str] = None
        self.in_house_users: List[Tuple[str, str]] = []
        self.endstation_name: Optional[str] = None
        self.session_start_date: Optional[str] = None
        self.user_group: str = ''
        self.template: Optional[str] = None
        self.username: str = ''
        self.group_id: str = ''
        self.user_id: str = ''
        self.projuser: str = ''
        self.master_users: Dict[str, Dict[str, str]] = {}
        self.default_precision: str = '05'
        self.suffix: Optional[str] = None
        self.base_directory: Optional[str] = None
        self.base_process_directory: Optional[str] = None
        self.raw_data_folder_name: Optional[str] = None
        self.processed_data_folder_name: Optional[str] = None

    def init(self):
        self.synchrotron_name = self.get_property('synchrotron_name')
        self.beamline_name = self.get_property('beamline_name')
        self.endstation_name = self.get_property('endstation_name').lower()
        self.suffix = self["file_info"].get_property('file_suffix')
        self.template = self["file_info"].get_property('file_template')
        self.base_directory = self["file_info"].get_property('base_directory')
        self.base_process_directory = self["file_info"].get_property('processed_data_base_directory')
        self.raw_data_folder_name = self["file_info"].get_property('raw_data_folder_name')
        self.processed_data_folder_name = self["file_info"].get_property('processed_data_folder_name')

        try:
            inhouse_proposals = self["inhouse_users"]["proposal"]
            for prop in inhouse_proposals:
                self.in_house_users.append((prop.get_property('code'),
                                            str(prop.get_property('number'))))
        except KeyError:
            log.warning("No inhouse users defined")

        queue_model_objects.PathTemplate.set_path_template_style(self.synchrotron_name, self.template)
        queue_model_objects.PathTemplate.set_data_base_path(self.base_directory)
        queue_model_objects.PathTemplate.set_archive_path(
            self['file_info'].get_property('archive_base_directory'),
            self['file_info'].get_property('archive_folder'))

        precision = self.default_precision
        try:
            precision = eval(self.session_hwobj["file_info"].get_property('precision', self.default_precision))
        except Exception:
            log.warning(f"Failed to evaluate precision, using default: {self.default_precision}")

        queue_model_objects.PathTemplate.set_precision(precision)

        master_users = self["master_users"]
        for user in master_users:
            self.master_users[user.login_name] = {
                'display_name': user.display_name,
                'password': user.password,
            }

    def is_master_user(self, username: Optional[str] = None, password: Optional[str] = None, check_password: bool = False) -> bool:
        if not username:
            username = self.username

        if username in self.master_users:
            if not check_password:
                return True
            user = self.master_users[username]
            return user['display_name'] if password == user['password'] else False
        return False

    def get_master_users(self) -> Dict[str, Dict[str, str]]:
        return self.master_users

    def set_user_info(self, username: str, user_id: Optional[str] = None, group_id: Optional[str] = None, projuser: Optional[str] = None):
        if username == '':
            log.debug(f"SESSION - User {self.username} logged out.")
        else:
            log.debug(f"SESSION - User {username} logged in. gid={group_id} / uid={user_id}")

        self.username = username
        self.group_id = group_id
        self.user_id = user_id
        self.projuser = projuser

    def get_base_data_directory(self) -> str:
        user_category = ''
        start_time = self.session_start_date.split(' ')[0].replace('-', '') if self.session_start_date else time.strftime("%Y%m%d")

        if self.synchrotron_name == "EMBL-HH":
            user = os.getenv("SUDO_USER") or os.getenv("USER")
            directory = os.path.join(self.base_directory, f"{os.getuid()}_{os.getgid()}", user, start_time)
        else:
            user_category = 'inhouse' if self.is_inhouse() else 'visitor'
            if user_category == 'inhouse':
                directory = os.path.join(self.base_directory, self.endstation_name,
                                         user_category, self.get_proposal(), start_time)
            else:
                directory = os.path.join(self.base_directory, user_category,
                                         self.get_proposal(), self.endstation_name, start_time)

        return directory

    def get_base_image_directory(self) -> str:
        return os.path.join(self.get_base_data_directory(), self.raw_data_folder_name)

    def get_base_process_directory(self) -> str:
        return os.path.join(self.get_base_data_directory(), self.processed_data_folder_name)

    def get_image_directory(self, sub_dir: Optional[str] = None) -> str:
        directory = self.get_base_image_directory()
        if sub_dir:
            sub_dir = sub_dir.replace(' ', '').replace(':', '-')
            directory = os.path.join(directory, sub_dir)
        return os.path.join(directory, '')

    def get_process_directory(self, sub_dir: Optional[str] = None) -> str:
        directory = self.get_base_process_directory()
        if sub_dir:
            sub_dir = sub_dir.replace(' ', '').replace(':', '-')
            directory = os.path.join(directory, sub_dir)
        return os.path.join(directory, '')

    def get_default_prefix(self, sample_data_node=None, generic_name: bool = False) -> str:
        proposal = self.get_proposal()
        prefix = proposal

        if sample_data_node and sample_data_node.has_lims_data():
            prefix = f"{sample_data_node.crystals[0].protein_acronym}-{sample_data_node.name}"
        elif generic_name:
            prefix = '<acronym>-<name>'

        return prefix

    def set_proposal(self, code: str = "", number: str = "", proposal_id: str = "", session_id: int = 1):
        self.proposal_code = code
        self.proposal_number = number
        self.proposal_id = proposal_id
        self.session_id = session_id

        log.debug(f"Session.py. setting proposal to {self.proposal_code}, {self.proposal_number}")

        username = f"{self.proposal_code}{self.proposal_number}"
        self.set_user_info(username, None, None, self.proposal_number)

    def get_proposal(self) -> str:
        if self.proposal_code and self.proposal_number:
            if self.proposal_code == 'ifx':
                self.proposal_code = 'fx'
            return f"{self.proposal_code}{self.proposal_number}"
        return 'local-user'

    def is_inhouse(self, proposal_code: Optional[str] = None, proposal_number: Optional[str] = None) -> bool:
        if self.is_master_user(proposal_number):
            return True

        proposal_code = proposal_code or self.proposal_code
        proposal_number = proposal_number or self.proposal_number

        return (proposal_code, proposal_number) in self.in_house_users

    def get_inhouse_user(self) -> Tuple[str, str]:
        return self.in_house_users[0]

    def set_session_start_date(self, start_date_str: str):
        self.session_start_date = start_date_str

    def get_session_start_date(self) -> Optional[str]:
        return self.session_start_date

    def set_user_group(self, group_name: str):
        self.user_group = str(group_name)

    def get_group_name(self) -> str:
        return self.user_group

def test_hwo(hwo):
    print("Is inhouse (blissadm): ", hwo.is_inhouse("mx", "blissadm"))

