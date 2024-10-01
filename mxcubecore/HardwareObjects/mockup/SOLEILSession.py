
import os
import time
import logging
from typing import Optional, Tuple, Dict

from HardwareRepository import HardwareRepository
import Session
#import queue_model_objects_v1 as queue_model_objects

log = logging.getLogger("HWR")

class SOLEILSession(Session.Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ldap_ho = None
        self.ssh_name = None
        self.latest_projuser = ""

    def init(self):
        super().init()
        self.ldap_ho = self.get_object_by_role("ldap")

    def get_username(self) -> str:
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
        starting_time = self.get_property('starting_time')

        if self.session_start_date:
            start_time = self.session_start_date.split(' ')[0]
        else:
            local_time = time.localtime()
            if local_time.tm_hour >= (float(starting_time) - 1):
                start_time = time.strftime("%Y-%m-%d")
            else:
                local_time = time.gmtime(time.time() - (float(starting_time) * 3600))
                start_time = time.strftime("%Y-%m-%d", local_time)

        if self.is_inhouse():
            return os.path.join(self.base_directory, start_time, self.get_proposal_number())
        return ""

    def get_archive_directory(self, directory: Optional[str] = None, *args) -> str:
        thedir = directory or self.get_base_data_directory()
        return thedir.replace('RAW_DATA', 'ARCHIVE') if 'RAW_DATA' in thedir else os.path.join(thedir, 'ARCHIVE')

    def get_ruche_info(self, path: str) -> str:
        usertype = 'soleil' if self.is_inhouse(self.username) else 'users'
        basedir = os.path.dirname(path) if not os.path.isdir(path) else path
        ruchepath = basedir.replace(self["file_info"].get_property('base_directory'), '').lstrip(os.path.sep)
        return f"{usertype} {self.username} {self.user_id} {self.group_id} {basedir} {ruchepath}\n"

def test_hwo(hwo):
    print('\n======================== TESTS ========================')
    print('These are tests of the HardwareObject SOLEILSession')
    print('\n[IMPORTANT] to note: the following functions are turned')
    print('  off by default')
    print('=======================================================\n')

    print('-------------------------------------------------------')
    print('Defining some generic variables for all the tests')
    print('-------------------------------------------------------')

    print('\n-------------------------------------------------------')
    print('Checking for the connection to the HWO.')
    print('-------------------------------------------------------')
    import os
    from HardwareRepository import HardwareRepository
    hwr_directory = os.environ["MXCUBE_XML_PATH"]
    hwr = HardwareRepository.HardwareRepository(os.path.abspath(hwr_directory))
    hwr.connect()
    session = hwr.get_hardware_object("/session")

    print('\nVariables and attributes digged out of session HWO:')
    file_info = session['file_info']
    properties = [
        'file_suffix', 'base_directory', 'ispyb_base_directory',
        'raw_data_folder_name', 'archive_base_directory', 'archive_folder',
        'processed_data_base_directory', 'processed_data_folder_name'
    ]
    for prop in properties:
        print(f'  {prop:<30}: {file_info.get_property(prop)}')
    print(f'  starting_time                 : {session.get_property("starting_time")}')
    print('-------------------------------------------------------')

    print('\n-------------------------------------------------------')
    print('Checking for the set_user_info function')
    print('-------------------------------------------------------')
    session.set_user_info('mx2014', '143301', '14330', '20100023')
    print(f'  username : {session.username}')
    print(f'  group_id : {session.group_id}')
    print(f'  user_id  : {session.user_id}')
    print(f'  projuser : {session.projuser}')
    print('-------------------------------------------------------')

    print('\n-------------------------------------------------------')
    print('Checking for the path_to_ispyb function')
    print('-------------------------------------------------------')
    test_path = os.path.join(file_info.get_property('archive_base_directory'),
                             file_info.get_property('archive_folder'),
                             'mx2014_2_4.snapshot.jpeg')
    ispyb_path = session.path_to_ispyb(test_path)
    print(f'Test path is         : {test_path}')
    print(f'  becomes ISPyB path : {ispyb_path}')
    print('-------------------------------------------------------')

    print('\n-------------------------------------------------------')
    print('Checking for the get_beamline_name function')
    beamline_name = session.get_beamline_name()
    print(f'  beamline name : {beamline_name}')
    print('-------------------------------------------------------')

    print('\n-------------------------------------------------------')
    print('Checking for the get_proposal_number function')
    session.proposal_number = session.projuser
    prop_number = session.get_proposal_number()
    print(f'  proposal number : {prop_number}')
    print('-------------------------------------------------------')

    print('\n-------------------------------------------------------')
    print('Checking for the get_base_data_directory function')
    print('-------------------------------------------------------')
    base_data_directory = session.get_base_data_directory()
    print(f'  base data directory : {base_data_directory}')
    print('-------------------------------------------------------')

    print('\n-------------------------------------------------------')
    print('Checking for the get_archive_directory function')
    print('-------------------------------------------------------')
    archive_directory = session.get_archive_directory()
    print(f'  archive directory : {archive_directory}')
    print('-------------------------------------------------------')

    print('\n-------------------------------------------------------')
    print('Checking for the ruche_info function')
    print('-------------------------------------------------------')
    ruche_info = session.get_ruche_info(test_path)
    print(f'  ruche info : {ruche_info}')
    print('-------------------------------------------------------')

if __name__ == '__main__':
    test_hwo('test')