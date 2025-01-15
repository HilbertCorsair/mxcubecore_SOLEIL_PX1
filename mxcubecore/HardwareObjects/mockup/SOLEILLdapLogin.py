from HardwareRepository import HardwareRepository

from mxcubecore.BaseHardwareObjects import HardwareObject
#from HardwareRepository.BaseHardwareObjects import Procedure
import os
import logging
import ldap
import re
import time
import paramiko
from pathlib import Path


"""
<procedure class="LdapLogin">
  <ldaphost>ldaphost.mydomain</ldaphost> 195.221.10.1
  <ldapport>389</ldapport>
  <ldapdc>EXP</ldapdc>
</procedure>
"""

log = logging.getLogger("HWR")

###
### Checks the proposal password in a LDAP server
###
class SoleilLdapLogin(HardwareObject):
    def __init__(self, name):
        super().__init__(name)
        self.ldap_connection = None
        
    def init(self):
        self.ldap_host = self.getProperty('ldaphost')
        self.ldap_port = self.getProperty('ldapport')
        self.process_host = self.getProperty('process_host')
        
        if self.ldap_host is None:
            log.error("SoleilLdapLogin: you must specify the LDAP hostname")
        else:
            self.open_connection()
            
        ldap_dc = self.getProperty('ldapdc')
        if ldap_dc is not None:
            parts = ldap_dc.split(".")
            self.dc_parts = ",".join(f"dc={part}" for part in parts)
        else:
            self.dc_parts = "dc=soleil,dc=fr"
            
    def push_ssh_key(self, username: str, password: str) -> None:
        """Push SSH key to the processing host using paramiko."""
        try:
            # Generate path to default public key
            home_dir = Path.home()
            public_key_path = home_dir / ".ssh" / "id_rsa.pub"
            
            if not public_key_path.exists():
                log.error("No SSH public key found at %s", public_key_path)
                return
                
            # Create SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            try:
                # Connect to remote host
                ssh.connect(
                    self.process_host,
                    username=username,
                    password=password
                )
                
                # Read local public key
                public_key = public_key_path.read_text().strip()
                
                # Create .ssh directory and set permissions
                commands = [
                    'mkdir -p ~/.ssh',
                    'chmod 700 ~/.ssh',
                    f'echo "{public_key}" >> ~/.ssh/authorized_keys',
                    'chmod 600 ~/.ssh/authorized_keys'
                ]
                
                for cmd in commands:
                    stdin, stdout, stderr = ssh.exec_command(cmd)
                    if stderr.read():
                        log.warning("Error executing SSH command: %s", cmd)
                        
            finally:
                ssh.close()
                
        except Exception as e:
            log.error("Failed to push SSH key: %s", str(e))
            log.debug("Full traceback:", exc_info=True)

    def open_connection(self):
        try:
            if self.ldap_port is None:
                log.info("SoleilLdapLogin: connecting to LDAP server %s", self.ldap_host)
                self.ldap_connection = ldap.initialize(f"ldap://{self.ldap_host}")
            else:
                log.info("SoleilLdapLogin: connecting to LDAP server %s:%s", 
                        self.ldap_host, self.ldap_port)
                self.ldap_connection = ldap.initialize(
                    f"ldap://{self.ldap_host}:{self.ldap_port}")
            
            self.ldap_connection.simple_bind_s()
        except ldap.LDAPError as e:
            log.error("Failed to connect to LDAP: %s", str(e))
            self.ldap_connection = None

    def login(self, username: str, password: str, retry: bool = True) -> tuple:
        """Authenticate user and push SSH key if successful."""
        if username.isdigit() and len(username) > 8:
            username = username[:8]
            
        if self.ldap_connection is None:
            return self.cleanup(msg="no LDAP server configured")
            
        found = self.search_user(username, retry)
        if not found:
            return self.cleanup(msg=f"unknown proposal {username}")
            
        if not password:
            return self.cleanup(msg=f"no password for {username}")

        try:
            dn = str(found[0][0])
            self.ldap_connection.simple_bind_s(dn, password)
            
            # If authentication successful, push SSH key
            try:
                self.push_ssh_key(username, password)
            except Exception as e:
                log.error("Cannot add ssh key for processing: %s", str(e))
                log.debug("Full traceback:", exc_info=True)
                
            return True, username
            
        except ldap.INVALID_CREDENTIALS:
            return self.cleanup(msg=f"invalid password for {username}")
        except ldap.LDAPError as err:
            if retry:
                self.cleanup(ex=err)
                return self.login(username, password, retry=False)
            return self.cleanup(ex=err)

    # Creates a new connection to LDAP if there's an exception on the current connection
    def reconnect(self):
        if self.ldap_connection is not None:
            try:
                self.ldap_connection.result(timeout=0)
            except ldap.LDAPError as err:
                self.open_connection()
            
    # Logs the error message (or LDAP exception) and returns the respective tuple
    def cleanup(self,ex=None,msg=None):
        if ex is not None:
            try:
                msg=ex[0]['desc']
            except (IndexError,KeyError,ValueError,TypeError):
                msg="generic LDAP error"
        logging.getLogger("HWR").error("SOLEILLdapLogin: %s" % msg)
        if ex is not None:
            self.reconnect()
        return (False,msg)

    # Check password in LDAP
    def getinfo(self,username):

        self.reconnect()
        found = self.search_user(username)

        if not found:
            return self.cleanup(msg="unknown proposal %s" % username)
        else:
            dn, info = found[0]
            return True, info

    def get_uid_gid(self,username):
        ok, info = self.getinfo(username)
 
        if ok:
            return info['uidNumber'], info['gidNumber']
        else:
            return None, None

    def search_user(self,username,retry=True):

        logging.getLogger("HWR").debug("SOLEILLdapLogin: searching for %s (dcparts are: %s)" % (username, self.dcparts))

        try:
            found=self.ldapConnection.search_s(self.dcparts, ldap.SCOPE_SUBTREE, "uid="+username)
        except ldap.LDAPError as err:
            logging.getLogger("HWR").error("SOLEILLdapLogin search_user: error in LDAP search: %s" % err)
            return self.cleanup(ex=err)
        else:
            return found

    def find_groups_for_username(self,username):
        #dcparts = "dc=Exp"
        dcparts = "ou=Projets,ou=Groups,dc=EXP"
        filter = "(&(objectClass=posixGroup)(memberUid=%s))" % username
        groupnames = {}

        #dcparts = "ou=Groups"
        #filter = ""

        found=self.ldap_connection.search_s(dcparts, ldap.SCOPE_SUBTREE, filter)
        for item in found:
            mat = re.search("cn=(?P<gname>[^\,]*)\,",item[0])
            if mat:
                groupnames[ mat.group('gname') ] = item[1]['memberUid']
        return groupnames
        
    def find_projectusers(self, username):
        groups = self.find_groups_for_username(username)
        projusers = []
        for groupname, users in groups.iteritems():
            for user in users:
                if user == groupname[1:]:
                    projusers.append( user )
        return projusers

    def find_users_samegroup(self,username):
        pass

    def find_usernames_in_group(self,groupname, username):
        dcparts = "ou=Projets,ou=Groups,dc=EXP"
        dcparts = "cn=%sou=Projets,ou=Groups,dc=EXP" % groupname
        filter = "((memberUid=*))" % username

    def find_description_for_user(self,username):
        dcparts = "dc=EXP"
        filter = "uid=%s" % username
        found=self.ldap_connection.search_s(dcparts, ldap.SCOPE_SUBTREE, filter)
        try:
            return found[0][1]['description'][0]
        except:
            return None

    def find_sessions_for_user(self,username):
        sesslist = SessionList()
        for projuser in self.find_projectusers(username):
            desc = self.find_description_for_user(projuser)  
            if desc is not None: 
                sesslist.extend( self.decode_session_info(projuser, desc) )
        return sesslist 

    def find_valid_sessions_for_user(self,username, beamline=None):
        sesslist = self.find_sessions_for_user(username)
        return sesslist.find_valid_sessions(beamline=beamline)

    def decode_session_info(self, projuser, session_info):
        """ ext;proxima1:1266393600,1266595200-1265644800,1265846400-1425510000,1426114800 """

        retlist = SessionList()
        
        beamlinelist = session_info.split(";")

        if len(beamlinelist) <2:
            logging.getLogger("HWR").debug("SOLEILLdapLogin: Cannot parse session info in ldap : %s" % session_info)
            return retlist

        usertype = beamlinelist[0]

        try:
            for blsess in beamlinelist[1:]:
                beamline,sessionlist = blsess.split(":")
                sessions = sessionlist.split("-")
                for sess in sessions:
                    sessbeg, sessend = sess.split(",")
                    sessinfo = SessionInfo(projuser, usertype, beamline, int(sessbeg), int(sessend))
                    retlist.append(sessinfo)
        except:
            logging.getLogger("HWR").debug("SOLEILLdapLogin: Cannot parse session info in ldap : %s " % session_info)

        return retlist

    def show_all(self):
        try:
            found=self.ldap_connection.search_s(self.dcparts, ldap.SCOPE_SUBTREE)
        except ldap.LDAPError as err:
            print ("error in LDAP search",err)

            return self.cleanup(ex=err)
        else:
            for item in found:
                print (item)

class SessionInfo:
    def __init__(self, username, usertype, beamline, sessbeg, sessend):
        self.username = username
        self.usertype = usertype
        self.beamline = beamline
        self.begin = sessbeg
        self.finish = sessend

    def __repr__(self):
        retstr = """
            Beamline: %s; Username: %s (%s); From: %s: To: %s
""" %  (self.beamline, self.username, self.usertype, \
              time.asctime(time.localtime(self.begin)), \
              time.asctime(time.localtime(self.finish)) )
        return retstr
        
class SessionList(list):
    def beamlineList(self):
        retlist = []
        for session in self:
            if session.beamline not in retlist:
                 retlist.append( session.beamline )
        return retlist

    def find_valid_sessions(self, timestamp=None, beamline=None):
        if timestamp == None:
            timestamp = time.time()

        retlist = SessionList()

        for session in self:
            if timestamp >= session.begin and timestamp <= session.finish:
                if beamline == None or beamline.lower() == session.beamline.lower():
                    retlist.append(session)
        return retlist
   
       
'''
def test_hwo(hwo):
    print "  TEST OF LDAPLOGIN Hardware Object "
   
    ISPYB specific files: /nfs/ruche/proxima1-soleil/com-proxima1/ispyb_identifiers/
    
    prop_dic = {'20100023': 'tisabet', '20160745': '087D2P3252'}
    print '\n======================== TESTS ========================'
    print ' These are tests of the HardwareObject SOLEILLdapLogin'
    print ' You can test based things based on the following accounts:'
    for prop_id in prop_dic:
        print '   %s : %s' % (prop_id, prop_dic[prop_id])

    print '=======================================================\n'

#    print '-------------------------------------------------------'
#    print 'Checking for the availability of the LDAP'
#    print '-------------------------------------------------------'
#    import os
#    hwr_directory = os.environ["MXCUBE_XML_PATH"]
#    hwr = HardwareRepository.HardwareRepository(os.path.abspath(hwr_directory))
#    hwr.connect()
#    ldapconnection = hwr.getHardwareObject("/ldapconnection")

    print '\n-------------------------------------------------------'
    print 'Checking for the login function. This includes functions:'
    print '   - cleanup'
    print '   - search_user'
    print '-------------------------------------------------------'
    user = raw_input('User ID ? ')
    if user == '': user = '20100023'

    pswd = raw_input('User PWD? ')
    if pswd == '': pswd = 'rlener'

    print '\nAttempting logging in with user: %s | password: %s\n' % (user, pswd)

    login_ok = hwo.login(user, pswd)

    if not login_ok[0]:
        print "\nCannot login as %s" % user
    else:
        print "\nUser logged in %s" % user

    print '\n-------------------------------------------------------'
    print 'Checking for the reconnect function'
    print '-------------------------------------------------------'
    if hwo.ldapConnection:

        print 'LDAP connection currently: True\n'
    else:
        print 'LDAP connection currently: False\n'

    hwo.reconnect()

    if hwo.ldapConnection:
        print '\nLDAP reconnection succeeded'
    else:
        print '\nLDAP reconnection failed'

    print '\n-------------------------------------------------------'
    print 'Checking for the getinfo function with user: %s' % user
    print '-------------------------------------------------------'
    info = hwo.getinfo(user)
    print(" Info is %s" % str(info))

    if not info:
        print '\nFunction getinfo does not work properly for user: %s!' % user
    else:
        print '\nInformation obtained for user: %s' % user
        for keyword in sorted(info):
            print '%-20s : %s' % (keyword, info[keyword])

    print '\n-------------------------------------------------------'
    print 'Checking for the find_valid_sessions_for_user'
    print 'This includes functions:'
    print '   - find_valid_sessions_for_user'
    print '   - find_sessions_for_user'
    print '   - find_projectusers'
    print '   - find_groups_for_username'
    print '   - decode_session_info'
    print '-------------------------------------------------------'
    valid_sessions = hwo.find_valid_sessions_for_user(user, beamline = 'proxima1')

    if not valid_sessions:
        print '\nCould not find any valid sessions for user: %s' % user
        sessions = hwo.find_sessions_for_user(user)
        if not sessions:
            print '\nWorth thant that: no sessions at all to be reported!!'
        else:
            print '\nHere is the list of sessions reported for user: %s' % user
            for session in sessions: print session
    else:
        print '\nValid sessions obtained for user: %s' % user
        print valid_sessions

    print '\n-------------------------------------------------------'
    print 'Checking for the get_uid_gid function'
    print '-------------------------------------------------------'
    uid, gid = hwo.get_uid_gid(user)
    print "User: %s - uid=%s / gid=%s " %(user, uid, gid)
    print "  TEST OF LDAPLOGIN Hardware Object  FINISHED"

if __name__ == '__main__':
    test_hwo('test')
'''
