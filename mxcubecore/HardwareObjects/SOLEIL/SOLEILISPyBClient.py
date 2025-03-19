
import logging
import requests
import os

from suds.transport.http import HttpAuthenticated
from suds.client import Client

from ISPyBClient2 import  ISPyBClient2, _CONNECTION_ERROR_MSG

class SOLEILISPyBClient(ISPyBClient2):
    def __init__(self, name):
        super().__init__(name)

    def init(self):
        """
        Init method declared by HardwareObject.
        """
        super().init()
        self.authServerType = self.get_property("authServerType") or "ldap"
        self.loginType = self.get_property("loginType") or "proposal"
        print("\nSOLEILISPyBClient\n")
        self.session_hwobj = self.get_object_by_role('session')
        if self.authServerType == "ldap":
            # Initialize ldap

            self.ldapConnection=self.get_object_by_role('ldapServer')
            if self.ldapConnection is None:
                logging.getLogger("HWR").debug('LDAP Server is not available')

        self.beamline_name = self.session_hwobj.beamline_name

        self.ws_root = self.get_property('ws_root')
        self.ws_username = self.get_property('ws_username')
        self.ws_password = self.get_property('ws_password')

        self.ws_collection = self.get_property('ws_collection')
        self.ws_shipping = self.get_property('ws_shipping')
        self.ws_tools = self.get_property('ws_tools')

        self.identifiers_location = self.get_property("ispyb_identifiers_location")

        self.connection_timeout = self.get_property('connectionTimeout')
        if not self.connection_timeout: self.connection_timeout = 3

        logging.getLogger("HWR").info("SOLEILISPyBClient: Initializing SOLEIL ISPyB Client")
        # Add the porposal codes defined in the configuration xml file
        # to a directory. Used by translate()
        try:
            proposals = self.session_hwobj['proposals']

            for proposal in proposals:
                code = proposal.code
                self.__translations[code] = {}
                print(code)
                try:
                    self.__translations[code]['ldap'] = proposal.ldap
                except AttributeError:
                    pass
                try:
                    self.__translations[code]['ispyb'] = proposal.ispyb
                except AttributeError:
                    pass
                try:
                    self.__translations[code]['gui'] = proposal.gui
                except AttributeError:
                    pass
        except IndexError:
            pass
        except:
            pass
            #import traceback
            #traceback.print_exc()

    def get_identifiers_location(self):
        return self.identifiers_location

    def translate(self, code, what):
        """
        Given a proposal code, returns the correct code to use in the GUI,
        or what to send to LDAP, user office database, or the ISPyB database.
        """
        if what == "ispyb":
            return "mx"
        if what == "gui":
            return "mx"
        return ""
        # return code

    def _wsdl_shipping_client(self):
        return self._wsdl_client(self.ws_shipping)

    def _wsdl_tools_client(self):
        return self._wsdl_client(self.ws_tools)

    def _wsdl_collection_client(self):
        return self._wsdl_client(self.ws_collection)

    def _wsdl_client(self, service_name):
        # Create a session to handle cookies and authentication
        session = requests.Session()
        session.cookies = requests.cookies.RequestsCookieJar()
        session.auth = (self.ws_username, self.ws_password)

        # Create transport with the session
        trans = HttpAuthenticated(username=self.ws_username,
                                password=self.ws_password)

        # Set the session as the transport's opener
        trans.session = session

        # Build URLs
        ws_root = self.ws_root.strip()
        urlbase = service_name + "?wsdl"
        locbase = service_name
        url = ws_root + urlbase
        loc = ws_root + locbase

        # Create SOAP client
        ws_client = Client(url,
                        transport=trans,
                        timeout=self.connection_timeout,
                        location=loc,
                        cache=None)

        return ws_client

    def path_to_ispyb(self, path):
        return self.session_hwobj.path_to_ispyb( path )
   


    def prepare_collect_for_lims(self, mx_collect_dict):
        # Attention! directory passed by reference. modified in place

        for i in range(4):
            try:
                prop = f'xtalSnapshotFullPath{i+1}'
                orig_prop = f'xtalSnapshotOrigPath{i+1}'
                logging.getLogger("HWR").debug(f" checking for snapshot {prop}")
                path = mx_collect_dict[prop]
                ispyb_path = self.session_hwobj.path_to_ispyb(path)
                logging.debug(f"SOLEIL ISPyBClient - {prop} is {ispyb_path}")
                mx_collect_dict[orig_prop] = path
                mx_collect_dict[prop] = ispyb_path
            except KeyError:
                pass
            except:
                import traceback
                logging.getLogger("HWR").debug(f" prepare_collect_for_lims. {traceback.format_exc()}")


    def prepare_image_for_lims(self, image_dict):
        for prop in [ 'jpegThumbnailFileFullPath', 'jpegFileFullPath']:
            try:
                path = image_dict[prop]
                ispyb_path = self.session_hwobj.path_to_ispyb( path )
                image_dict[prop] = ispyb_path
            except:
                pass