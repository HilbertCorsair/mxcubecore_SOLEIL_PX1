"""
This module serves to connect to and Ldap server.

It works in principle for ESRF, Soleil Proxima and MAXIV beamlines
"""
from mxcubecore.BaseHardwareObjects import Procedure

class LdapLogin (Procedure):
    def __init__(self, name):
        super().__init__(name)
        self.ldapConnection = None
    def init(self):
        ldaphost = self.get_property('ldaphost')

    def login (seflf, username, password, retry = True):
        return (true, username)