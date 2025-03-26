
import os
import time
import glob
import logging
from typing import Optional, Tuple, Dict
from mxcubecore import HardwareRepository as HWR
#from HardwareRepository import HardwareRepository
from mxcubecore.HardwareObjects.Session import Session
from mxcubecore.model import queue_model_objects


log = logging.getLogger("HWR")

class SOLEILSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ldap_ho = None
        self.ssh_name = None
        self.latest_projuser = ""
        self.set_test_user_info()

    def init(self):
        super().init()
        self.ldap_ho = self.get_object_by_role("ldapServer")
        archive_base_directory = self["file_info"].get_property(
            "archive_base_directory"
        )
        if archive_base_directory:
            archive_folder = os.path.join(
                self["file_info"].get_property("archive_folder"), time.strftime("%Y")
            )
            queue_model_objects.PathTemplate.set_archive_path(
                archive_base_directory, archive_folder
            )

    def get_full_path (self):
        full_path = self.get_base_data_directory()
        process_path = self.get_processed_directory()

        return full_path, process_path


    def get_username(self) -> str:
        print("*******")

        return self.username

    def get_projuser(self) -> str:
        return self.projuser

    def get_latest_projuser(self) -> str:
        return self.latest_projuser

    def set_ssh_name(self, name: str):
        self.ssh_name = name
        log.debug(f"SOLEILSession.py - setting ssh name to : {self.ssh_name}")

    def get_ssh_name(self) -> str:
        return self.ssh_name if self.ssh_name is not None else self.projuser

    def set_user_info(self, username: str, user_id: Optional[str] = None, group_id: Optional[str] = None, projuser: Optional[str] = None):
        uid = user_id
        gid = group_id

        log.debug(f"SOLEILSession set_user_info. username={username}, projuser={projuser}")
        if username and not uid and not gid:
            uid, gid = self.ldap_ho.get_uid_gid(projuser)

        if projuser:
            self.latest_projuser = projuser

        uid = uid[0] if isinstance(uid, list) else uid
        gid = gid[0] if isinstance(gid, list) else gid

        log.debug(f"SOLEILSession set_user_info. uid={uid}, gid={gid}")

        super().set_user_info(username, uid, gid, projuser)

    def set_test_user_info(self):
        self.username = "idtest0"
        self.projuser = "idtest0"
        self.user_id = "5265"
        self.group_id = "g5265"

    def get_user_info(self) -> Dict[str, str]:
        return {
            'proposal': self.username,
            'user': self.projuser,
            'uid': self.user_id,
            'gid': self.group_id,
        }

    def path_to_ispyb(self, path: str) -> str:
        projuser = self.get_proposal_number()
        ispyb_base = self["file_info"].get_property('ispyb_base_directory') % {'projuser': projuser}
        base_dir = self["file_info"].get_property('base_directory')

        arch_parts = path[len(base_dir)+1:].split(os.path.sep)
        ispyb_arch_path = os.path.sep.join([arch_parts[0]] + arch_parts[2:])
        return os.path.join(ispyb_base, ispyb_arch_path)

    def get_video_directory(self) -> str:
        directory = "/tmp/mxcube_video"
        os.makedirs(directory, exist_ok=True)
        return directory

    def get_beamline_name(self) -> str:
        return self.get_property("beamline_name")

    def get_proposal_number(self) -> str:
        """
        Returns the proposal number or 'local-user' if no proposal is available.

        :return: The proposal number
        :rtype: str
        """
        return self.proposal_number or "local-user"

    def get_base_directory(self) -> str:
        return self.base_directory

    def get_base_data_directory(self) -> str:
        """
        Returns the base data directory taking the 'contextual'
        information into account, such as if the current user
        is inhouse.

        :return: The base data path.
        :rtype: str
        """

        starting_time = time.time()/3600 #self.get_property('starting_time')

        if self.session_start_date:
            start_time = self.session_start_date.split(' ')[0]
        else:
            local_time = time.localtime()
            if local_time.tm_hour >= (float(starting_time) - 1):
                start_time = time.strftime("%Y-%m-%d")
            else:
                local_time = time.gmtime(time.time() - (float(starting_time) * 3600))
                start_time = time.strftime("%Y-%m-%d", local_time)


        return os.path.join(self.base_directory, start_time, self.get_proposal_number())


    def get_archive_directory(self, directory: Optional[str] = None, *args) -> str:
        thedir = directory or self.get_base_data_directory()
        return thedir.replace('RAW_DATA', 'ARCHIVE') if 'RAW_DATA' in thedir else os.path.join(thedir, 'ARCHIVE')

    def get_processed_directory(self, directory: Optional[str] = None, *args) -> str:
        thedir = directory or self.get_base_data_directory()
        return thedir.replace('RAW_DATA', 'PROCESSED_DATA') if 'RAW_DATA' in thedir else os.path.join(thedir, 'PROCESSED_DATA')


    def get_ruche_info(self, path: str) -> str:
        usertype = 'soleil' if self.is_inhouse(self.username) else 'users'
        basedir = os.path.dirname(path) if not os.path.isdir(path) else path
        ruchepath = basedir.replace(self["file_info"].get_property('base_directory'), '').lstrip(os.path.sep)
        return f"{usertype} {self.username} {self.user_id} {self.group_id} {basedir} {ruchepath}\n"
