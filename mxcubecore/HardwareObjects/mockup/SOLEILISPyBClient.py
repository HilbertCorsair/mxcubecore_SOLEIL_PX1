
import logging
import requests
import os

from suds.transport.http import HttpAuthenticated
from suds.client import Client

from ISPyBClient2 import  ISPyBClient2, _CONNECTION_ERROR_MSG

class SOLEILISPyBClient(ISPyBClient2):

    def init(self):
        """
        Init method declared by HardwareObject.
        """
        self.authServerType = self.get_property("authServerType") or "ldap"
        self.loginType = self.get_property("loginType") or "proposal"
        self.loginTranslate = self.get_property("loginTranslate") or True
        
        self.session_hwobj = self.get_object_by_role('session')
        if self.authServerType == "ldap":
            # Initialize ldap
            self.ldap_connection=self.get_object_by_role('ldapServer')
            if self.ldap_connection is None:
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

        try:

            if self.ws_root:
                logging.getLogger("HWR").debug("SOLEILISPyBClient: attempting to connect to %s" % self.ws_root)
                try: 
                    self._shipping = self._wsdl_shipping_client()
                    self._collection = self._wsdl_collection_client()
                    self._tools_ws = self._wsdl_tools_client()
                    logging.getLogger("HWR").debug("SOLEILISPyBClient: extracted from ISPyB values for shipping, collection and tools")
                    self.enable()
                except: 
                    logging.getLogger("HWR").exception("SOLEILISPyBClient: %s" % _CONNECTION_ERROR_MSG)
                    self.disable()
                    return
        except:
            import traceback
            print (traceback.print_exc())
            logging.getLogger("HWR").exception(_CONNECTION_ERROR_MSG)
            return
 
        # Add the porposal codes defined in the configuration xml file
        # to a directory. Used by translate()
        try:
            proposals = self.session_hwobj['proposals']
            
            for proposal in proposals:
                code = proposal.code
                self.__translations[code] = {}
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

    def wsdl_client(self, service_name):
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
'''
def test_hwo(hwo):
   
    ISPYB specific files: /nfs/ruche/proxima1-soleil/com-proxima1/ispyb_identifiers/
   
    print '\n======================== TESTS ========================'
    print 'These are tests of the HardwareObject SOLEILISPyBClient'
    print '=======================================================\n'

    print '-------------------------------------------------------'
    print 'Defining some generic variables for all the tests'
    proposal_code = 'mx'
    proposal_type = 'ispyb'
    proposal_username = '20100023'
    proposal_username = '20160745'
    proposal_username = '2018160411011250'
#    proposal_number = '20181604'
    proposal_psd = 'jkDW6U2Zuw'

    print '  proposal code     : %s' % proposal_code
    print '  proposal new code : %s' % proposal_type
    print '  proposal username : %s' % proposal_username
    print '-------------------------------------------------------'

    print '\n-------------------------------------------------------'
    print 'Checking for the connection to ISPyB.'
    print 'This includes functions:'
    print '   - _wsdl_shipping_client'
    print '   - _wsdl_client'
    print '   - _wsdl_collection_client'
    print '   - _wsdl_tools_client'
    print '-------------------------------------------------------'
    import os
    hwr_directory = os.environ["MXCUBE_XML_PATH"]
    hwr = HardwareRepository.HardwareRepository(os.path.abspath(hwr_directory))
    hwr.connect()
    dbconnection = hwr.getHardwareObject("/dbconnection")

    print '\n-------------------------------------------------------'
    print 'checking for get_login_type function'
    print '-------------------------------------------------------'
#    loginType = hwo.get_login_type()
#    print 'found login type: %s' % loginType

    print '\n-------------------------------------------------------'
    print 'checking for get_dc_display_link function'
    print '-------------------------------------------------------'
    ispybLink = dbconnection.get_dc_display_link()
    print 'found ISPyB link: %s' % ispybLink

    print '\n-------------------------------------------------------'
    print 'checking for translate function'
    print '-------------------------------------------------------'
    new_code = dbconnection.translate(proposal_code, proposal_type)
    print 'translated the code of type : %s to be : %s' % (proposal_type, new_code)

    print '\n-------------------------------------------------------'
    print 'checking for clear_daily_email function'
    print '-------------------------------------------------------'
    try:
        clear_answer = dbconnection.clear_daily_email()
        print 'daily email status: %s' % clear_answer
    except Exception as e:
        print 'some issues happened with this function : %s' % e

    print '\n-------------------------------------------------------'
    print 'checking for send_email function'
    print '-------------------------------------------------------'
    try:
        send_answer = dbconnection.send_email()
        print 'daily email status: %s' % send_answer
    except Exception as e:
        print 'some issues happened with this function : %s' % e

    print '\n-------------------------------------------------------'
    print 'checking for get_proposal function'
    print '-------------------------------------------------------'
    proposal = dbconnection.get_proposal(proposal_code, proposal_username)
    if proposal:
        print '\nobtained all information from proposal : %s resulting in :\n %s' % (proposal_username, proposal)
    else:
        print '\ndifficulties in getting informatoin for proposal : %s' % proposal_username

    print '\n===== DEVELOPMENT PART ====='
    try:
        tmp = dbconnection.get_proposal(proposal_code, proposal_username)
        print tmp
    except Exception as e:
        print 'ERROR found : %s' % e

    print '\n-------------------------------------------------------'
    print 'need to check for folling functions:'
    print '    get_proposal_by_username(username)'
    print '    get_session_local_contact(session_id)'
    print '    _ispybLogin(loginID, psd)'
    print '    login(loginID, psd, ldap_connection=None)'
    print '    get_todays_session(prop)'
    print '    store_data_collection(*args, **kwargs)'
    print '    _store_data_collection(mx_collection, beamline_setup = None)'
    print '    store_beamline_setup(session_id, beamline_setup)'
    print '    update_data_collection(mx_collection, wait = False)'
    print '    update_bl_sample(bl_sample)'
    print '    store_image(image_dict)'
    print '    __find_sample(sample_ref_list, code = None, location = None)'
    print '    get_samples(proposal_id, session_id)'
    print '    get_session_samples(proposal_id, session_id, sample_refs)'
    print '    get_bl_sample(bl_sample_id)'
    print '    create_session(session_dict)'
    print '    update_session(session_dict)'
    print '    store_energy_scan(energyscan_dict)'
    print '    associate_bl_sample_and_energy_scan(entry_dict)'
    print '    get_data_collection(data_collection_id)'
    print '    get_data_collection_id(dc_dict)'
    print '    get_sample_last_data_collection(blsampleid)'
    print '    get_session(session_id)'
    print '    store_xfe_spectrum(xdespectrum_dict)'
    print '    disable()'
    print '    enable()'
    print '    isInhouseUser(proposal_code, proposal_number)'
    print '    find_detector(type, manufacturer, model, mode)'
    print '    store_data_collection_group(mx_collection)'
    print '    _store_data_collection_group(group_data)'
    print '    get_proposals_by_user(user_name)'
    print '    store_autoproc_program(autoproc_program_dict)'
    print '    store_workflow(*args, **kwargs)'
    print '    store_workflow_step(*args, **kwargs)'
    print '    _store_workflow(info_dict)'
    print '    _store_workflow_step(worflow_info_dict)'
    print '    store_image_quality_indicators(image_dict)'
    print '    set_image_quality_indicators_plot(collection_id, plot_path, csv_path)'
    print '    store_robot_action(robot_action_dict)'
    print '    '
    print '-------------------------------------------------------'

    return
    proposal_code = 'mx'
    #proposal_number = '20100023' 
    #proposal_psd = 'tisabet'

#    proposal_number = '20160745'
#    proposal_psd = '087D2P3252'
    proposal_number = '2018160411011250'
#    proposal_number = '20181604'
    proposal_psd = 'jkDW6U2Zuw'


    print "Trying to login to ispyb" 
    info = hwo.login(proposal_number, proposal_psd)
    print "logging in returns: ", str(info)

if __name__ == '__main__':
    test_hwo('test')
 '''