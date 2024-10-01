import os
import logging
import ldap
import re
import time
from typing import Tuple, List, Dict, Optional
from HardwareRepository import HardwareRepository
from mxcubecore.BaseHardwareObjects import HardwareObject
"""
<procedure class="LdapLogin">
  <ldaphost>ldaphost.mydomain</ldaphost> 195.221.10.1
  <ldapport>389</ldapport>
  <ldapdc>EXP</ldapdc>
</procedure>
"""

log = logging.getLogger("HWR")

class SOLEILLdapLogin(HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self.ldap_connection = None
        self.ldap_host = None
        self.ldap_port = None
        self.process_host = None
        self.dc_parts = None

    def init(self):
        self.ldap_host = self.get_property('ldaphost')
        self.ldap_port = self.get_property('ldapport')
        self.process_host = self.get_property('process_host')

        if self.ldap_host is None:
            log.error("SOLEILLdapLogin: you must specify the LDAP hostname")
        else:
            self.open_connection()

        ldap_dc = self.get_property('ldapdc')
        if ldap_dc is not None:
            parts = ldap_dc.split(".")
            self.dc_parts = ",".join(f"dc={part}" for part in parts)
        else:
            self.dc_parts = "dc=soleil,dc=fr"

    def open_connection(self):
        try:
            if self.ldap_port is None:
                log.info(f"SOLEILLdapLogin: connecting to LDAP server {self.ldap_host}")
                self.ldap_connection = ldap.open(self.ldap_host)
            else:
                log.info(f"SOLEILLdapLogin: connecting to LDAP server {self.ldap_host}:{self.ldap_port}")
                self.ldap_connection = ldap.open(self.ldap_host, int(self.ldap_port))
            self.ldap_connection.simple_bind_s()
        except ldap.LDAPError as err:
            log.error(f"SOLEILLdapLogin: LDAP connection error: {err}")
            self.ldap_connection = None

    def reconnect(self):
        if self.ldap_connection is not None:
            try:
                self.ldap_connection.result(timeout=0)
            except ldap.LDAPError:
                self.open_connection()

    def cleanup(self, ex: Optional[Exception] = None, msg: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        if ex is not None:
            try:
                msg = ex.args[0]['desc']
            except (IndexError, KeyError, AttributeError):
                msg = "generic LDAP error"
        log.error(f"SOLEILLdapLogin: {msg}")
        if ex is not None:
            self.reconnect()
        return False, msg

    def get_info(self, username: str) -> Tuple[bool, Optional[Dict]]:
        self.reconnect()
        found = self.search_user(username)

        if not found:
            return self.cleanup(msg=f"unknown proposal {username}")
        else:
            dn, info = found[0]
            return True, info

    def get_uid_gid(self, username: str) -> Tuple[Optional[str], Optional[str]]:
        ok, info = self.get_info(username)
 
        if ok:
            return info['uidNumber'], info['gidNumber']
        else:
            return None, None

    def login(self, username: str, password: str, retry: bool = True) -> Tuple[bool, Optional[str]]:
        if username.isdigit() and len(username) > 8:
            username = username[:8]

        if self.ldap_connection is None:
            return self.cleanup(msg="no LDAP server configured")

        found = self.search_user(username, retry)
        
        if not found:
            return self.cleanup(msg=f"unknown proposal {username}")
        
        if password == "":
            return self.cleanup(msg=f"no password for {username}")

        if not isinstance(found, list):
            log.error(f"SOLEILLdapLogin: found type: {type(found)}")
            return self.cleanup(msg=f"unknown error {username}")
        
        dn = str(found[0][0])

        log.debug(f"SOLEILLdapLogin: found: {dn}")
        log.debug(f"SOLEILLdapLogin: validating {username}")

        try:
            self.ldap_connection.simple_bind_s(dn, password)
        except ldap.INVALID_CREDENTIALS:
            return self.cleanup(msg=f"invalid password for {username}")
        except ldap.LDAPError as err:
            if retry:
                self.cleanup(ex=err)
                return self.login(username, password, retry=False)
            else:
                return self.cleanup(ex=err)

        log.info(f"SOLEILLdapLogin: searching for {username}")

        try:
            log.info("SOLEIL Login: registering ssh key for processing")
            #push_key(username, password, host=self.process_host)
        except Exception as e:
            import traceback
            log.error("SOLEIL Login - cannot add ssh key for processing")
            log.debug(traceback.format_exc())

        return True, username

    def search_user(self, username: str, retry: bool = True) -> Optional[List]:
        log.debug(f"SOLEILLdapLogin: searching for {username} (dcparts are: {self.dc_parts})")

        try:
            found = self.ldap_connection.search_s(self.dc_parts, ldap.SCOPE_SUBTREE, f"uid={username}")
        except ldap.LDAPError as err:
            log.error(f"SOLEILLdapLogin search_user: error in LDAP search: {err}")
            return self.cleanup(ex=err)
        else:
            return found

    def find_groups_for_username(self, username: str) -> Dict[str, List[str]]:
        dc_parts = "ou=Projets,ou=Groups,dc=EXP"
        filter = f"(&(objectClass=posixGroup)(memberUid={username}))"
        groupnames = {}

        found = self.ldap_connection.search_s(dc_parts, ldap.SCOPE_SUBTREE, filter)
        for item in found:
            mat = re.search(r"cn=(?P<gname>[^,]*),", item[0])
            if mat:
                groupnames[mat.group('gname')] = item[1]['memberUid']
        return groupnames
        
    def find_projectusers(self, username: str) -> List[str]:
        groups = self.find_groups_for_username(username)
        return [user for groupname, users in groups.items() for user in users if user == groupname[1:]]

    def find_description_for_user(self, username: str) -> Optional[str]:
        dc_parts = "dc=EXP"
        filter = f"uid={username}"
        found = self.ldap_connection.search_s(dc_parts, ldap.SCOPE_SUBTREE, filter)
        try:
            return found[0][1]['description'][0]
        except (IndexError, KeyError):
            return None

    def find_sessions_for_user(self, username: str) -> 'SessionList':
        sesslist = SessionList()
        for projuser in self.find_projectusers(username):
            desc = self.find_description_for_user(projuser)  
            if desc is not None: 
                sesslist.extend(self.decode_session_info(projuser, desc))
        return sesslist 

    def find_valid_sessions_for_user(self, username: str, beamline: Optional[str] = None) -> 'SessionList':
        sesslist = self.find_sessions_for_user(username)
        return sesslist.find_valid_sessions(beamline=beamline)

    def decode_session_info(self, projuser: str, session_info: str) -> 'SessionList':
        retlist = SessionList()
        
        beamlinelist = session_info.split(";")

        if len(beamlinelist) < 2:
            log.debug(f"SOLEILLdapLogin: Cannot parse session info in ldap : {session_info}")
            return retlist

        usertype = beamlinelist[0]

        try:
            for blsess in beamlinelist[1:]:
                beamline, sessionlist = blsess.split(":")
                sessions = sessionlist.split("-")
                for sess in sessions:
                    sessbeg, sessend = map(int, sess.split(","))
                    sessinfo = SessionInfo(projuser, usertype, beamline, sessbeg, sessend)
                    retlist.append(sessinfo)
        except ValueError:
            log.debug(f"SOLEILLdapLogin: Cannot parse session info in ldap : {session_info}")

        return retlist

    def show_all(self):
        try:
            found = self.ldap_connection.search_s(self.dc_parts, ldap.SCOPE_SUBTREE)
        except ldap.LDAPError as err:
            print(f"error in LDAP search: {err}")
            return self.cleanup(ex=err)
        else:
            for item in found:
                print(item)

class SessionInfo:
    def __init__(self, username: str, usertype: str, beamline: str, sessbeg: int, sessend: int):
        self.username = username
        self.usertype = usertype
        self.beamline = beamline
        self.begin = sessbeg
        self.finish = sessend

    def __repr__(self):
        return f"""
            Beamline: {self.beamline}; Username: {self.username} ({self.usertype}); 
            From: {time.asctime(time.localtime(self.begin))}: 
            To: {time.asctime(time.localtime(self.finish))}
        """
        
class SessionList(list):
    def beamline_list(self) -> List[str]:
        return list(set(session.beamline for session in self))

    def find_valid_sessions(self, timestamp: Optional[float] = None, beamline: Optional[str] = None) -> 'SessionList':
        if timestamp is None:
            timestamp = time.time()

        return SessionList(
            session for session in self
            if timestamp >= session.begin and timestamp <= session.finish
            and (beamline is None or beamline.lower() == session.beamline.lower())
        )

def test_hwo(hwo):
    print("  TEST OF LDAPLOGIN Hardware Object ")
    prop_dic = {'20100023': 'tisabet', '20160745': '087D2P3252'}
    print('\n======================== TESTS ========================')
    print(' These are tests of the HardwareObject SOLEILLdapLogin')
    print(' You can test based things based on the following accounts:')
    for prop_id, value in prop_dic.items():
        print(f'   {prop_id} : {value}')

    print('=======================================================\n')

    print('\n-------------------------------------------------------')
    print('Checking for the login function. This includes functions:')
    print('   - cleanup')
    print('   - search_user')
    print('-------------------------------------------------------')
    user = input('User ID ? ') or '20100023'
    pswd = input('User PWD? ') or 'rlener'

    print(f'\nAttempting logging in with user: {user} | password: {pswd}\n')

    login_ok, message = hwo.login(user, pswd)

    if not login_ok:
        print(f"\nCannot login as {user}: {message}")
    else:
        print(f"\nUser logged in: {user}")

    print('\n-------------------------------------------------------')
    print('Checking for the reconnect function')
    print('-------------------------------------------------------')
    print(f'LDAP connection currently: {bool(hwo.ldap_connection)}\n')

    hwo.reconnect()

    print(f'\nLDAP reconnection {"succeeded" if hwo.ldap_connection else "failed"}')

    print('\n-------------------------------------------------------')
    print(f'Checking for the get_info function with user: {user}')
    print('-------------------------------------------------------')
    success, info = hwo.get_info(user)
    print(f" Info is {info}")

    if not success:
        print(f'\nFunction get_info does not work properly for user: {user}!')
    else:
        print(f'\nInformation obtained for user: {user}')
        for keyword in sorted(info):
            print(f'{keyword:20}: {info[keyword]}')

    print('\n-------------------------------------------------------')
    print('Checking for the find_valid_sessions_for_user')
    print('This includes functions:')
    print('   - find_valid_sessions_for_user')
    print('   - find_sessions_for_user')
    print('   - find_projectusers')
    print('   - find_groups_for_username')
    print('   - decode_session_info')
    print('-------------------------------------------------------')
    valid_sessions = hwo.find_valid_sessions_for_user(user, beamline='proxima1')

    if not valid_sessions:
        print(f'\nCould not find any valid sessions for user: {user}')
        sessions = hwo.find_sessions_for_user(user)
        if not sessions:
            print('\nWorse than that: no sessions at all to be reported!!')
        else:
            print(f'\nHere is the list of sessions reported for user: {user}')
            for session in sessions:
                print(session)
    else:
        print(f'\nValid sessions obtained for user: {user}')
        print(valid_sessions)

    print('\n-------------------------------------------------------')
    print('Checking for the get_uid_gid function')
    print('-------------------------------------------------------')
    uid, gid = hwo.get_uid_gid(user)
    print(f"User: {user} - uid={uid} / gid={gid} ")
    print("  TEST OF LDAPLOGIN Hardware Object  FINISHED")

if __name__ == '__main__':
    test_hwo('test')