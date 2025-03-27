#import json
import logging
#from json.decoder import JSONDecodeError
from typing import (
    Dict,
    List,
    Optional,
)
import time
from datetime import datetime , timedelta
#from zeep import Client
#from zeep.transports import Transport
from zeep.helpers import serialize_object
from zeep.exceptions import Fault

#from requests import Session
#from requests.auth import HTTPBasicAuth
import logging
from urllib.error import URLError
from pprint import pformat
#from urllib.parse import urljoin
#import requests
from mxcubecore.HardwareObjects.abstract.ISPyBDataAdapter import ISPyBDataAdapter
from mxcubecore.HardwareObjects.ProposalTypeISPyBLims import ProposalTypeISPyBLims
from mxcubecore.model.lims_session import LimsSessionManager
from mxcubecore.model.lims_session import Session as lims_Session

log = logging.getLogger("ispyb_client")

LAZY_SESSION_PREFIX = "lazy"


def _get_lazy_session_id(proposal: Dict) -> str:
    prop_id = proposal["proposalId"]
    return f"{LAZY_SESSION_PREFIX}{prop_id}"


def _is_lazy_session_id(session_id: str) -> bool:
    return session_id.startswith(LAZY_SESSION_PREFIX)


def _check_ispyb_error_message(response):
    def _expected_ispyb_err_msg(error_msg):
        import re

        match = re.match("^JBAS011843: Failed instantiate.*ldap.*ispyb", error_msg)
        return match is not None

    #
    # check that we got the 'expected' error message on invalid credentials,
    # otherwise log the error message, so we don't swallow new error messages
    #
    if _expected_ispyb_err_msg(response.text):
        # all is fine
        return

    log.warning(
        "unexpected response from ISPyB\n"
        + f"{response.status_code} {response.reason}\n{response.text}"
    )


def _create_session_object(proposal, session_id: str, beamline_name: str) -> lims_Session:
    # Not to be confused with ldap Session from HardwareObjects/Session (inherited by SOLEILSession)
    return lims_Session(
        proposal_id=proposal["proposalId"],
        code=proposal["code"],
        number=proposal["number"],
        session_id=session_id,
        beamline_name=beamline_name,
        title=proposal["title"],
        #
        # At MAXIV we don't care if a session is scheduled
        # or not, mark all sessions as scheduled.
        #
        is_scheduled_time=True,
        is_scheduled_beamline=True,
    )


class CustomISPyBDataAdapter(ISPyBDataAdapter):
    def __init__(self, ws_root, ws_username, ws_password, beamline_name):
        super().__init__(ws_root, ws_username, ws_password, beamline_name)
        self.beamline_name = beamline_name
        self.site = None

    """
    Extend the standard ISPyB data adapter with MAXIV specific logic of how to
    deal with proposal sessions.
    """
    def trace(fun):
        def _trace(*args):
            log_msg = "lims client " + fun.__name__ + " called with: "

            for arg in args[1:]:
                try:
                    log_msg += pformat(arg, indent = 4, width = 80) + ', '
                except:
                    pass

            logging.getLogger("ispyb_client").debug(log_msg)
            result = fun(*args)

            try:
                result_msg = "lims client " + fun.__name__ + \
                    " returned  with: " + pformat(result, indent = 4, width = 80)
            except:
                pass

            logging.getLogger("ispyb_client").debug(result_msg)
            return result

        return _trace


    def convert_to_dict(self, zeep_obj):
        # Convert the zeep object to a dictionary
        proposal_dict = serialize_object(zeep_obj, dict)

        def utf_encode(obj):
            if isinstance(obj, str):
                return obj.encode('utf-8')
            elif isinstance(obj, dict):
                return {k: utf_encode(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [utf_encode(item) for item in obj]
            return obj

        # Then wrap it in the 'Proposal' key and encode
        return utf_encode(proposal_dict)

    @trace
    def get_proposal(self, proposal_number, proposal_code = "mx"):
        #print (f"FETCHING proposal: code {proposal_code}, no {proposal_number}")

        """
        Returns the tuple (Proposal, Person, Laboratory, Session, Status).
        Containing the data from the coresponding tables in the database
        the status of the database operations are returned in Status.

        :param proposal_code: The proposal code
        :type proposal_code: str
        :param proposal_number: The proposal number
        :type propsoal_number: int

        :returns: The dict (Proposal, Person, Laboratory, Sessions, Status).
        :rtype: dict
        """
        logging.getLogger("HWR").debug("ISPyB. Obtaining proposal for code=%s / prop_number=%s" % (proposal_code, proposal_number))

        if self._shipping:
            try:
                try:
                    person = self._shipping.service.\
                             findPersonByProposal(proposal_code,
                                                  proposal_number)

                    if not person:
                        person = {}
                    logging.getLogger("HWR").debug("ISPyB. person is = %s" % (person))

                except Fault as e:
                    logging.getLogger("ispyb_client").error("=======================PERSON==============================")
                    logging.getLogger("ispyb_client").exception(str(e))
                    logging.getLogger("ispyb_client").error("===========================================================")
                    person = {}

                try:
                    proposal = self._shipping.service.\
                        findProposal(proposal_code,
                                     proposal_number)

                    if proposal:
                        proposal.code = proposal_code
                    else:
                        return {'Proposal': {},
                                'Person': {},
                                'Laboratory': {},
                                'Session': {},
                                'status': {'code':'error'}}

                    logging.getLogger("HWR").debug("ISPyB. proposal is = %s" % proposal.proposalId)

                except Fault as e:
                    logging.getLogger("ispyb_client").error("=======================PROPOSAL==============================")
                    logging.getLogger("ispyb_client").exception(str(e))
                    logging.getLogger("ispyb_client").error("=============================================================")
                    proposal = {}

                except Exception as e:
                    logging.getLogger("ispyb_client").error("=======================PROPOSAL==============================")
                    logging.getLogger("ispyb_client").exception(str(e))
                    logging.getLogger("ispyb_client").error("=============================================================")
                    return {'Proposal': {},
                            'Person': {},
                            'Laboratory': {},
                            'Session': {},
                            'status': {'code':'error'}}

                try:
                    lab = self._shipping.service.\
                        findLaboratoryByCodeAndNumber(proposal_code,
                                                      proposal_number)
                    if not lab:
                        lab = {}

                    logging.getLogger("HWR").debug("ISPyB. lab is = %s" % lab)

                except Fault as e:
                    logging.getLogger("ispyb_client").error("=======================LAB==============================")
                    logging.getLogger("ispyb_client").exception(str(e))
                    logging.getLogger("ispyb_client").error("========================================================")

                    lab = {}
                try:
                    res_sessions = self._collection.service.\
                        findSessionsByProposalAndBeamLine(proposal_code,
                                                          proposal_number,
                                                          self.beamline_name)
                    sessions = []

                    # Handels a list of sessions
                    for session in res_sessions:
                        if session is not None :
                            try:
                                session.startDate = \
                                    datetime.strftime(session.startDate,
                                                      "%Y-%m-%d %H:%M:%S")
                                session.endDate = \
                                    datetime.strftime(session.endDate,
                                                      "%Y-%m-%d %H:%M:%S")
                            except:
                                pass

                            sessions.append(self.convert_to_dict(session))

                    if not sessions:
                        sessions = []
                except Fault as e:
                    logging.getLogger("ispyb_client").error("=======================SESSION==============================")
                    logging.getLogger("ispyb_client").exception(str(e))
                    logging.getLogger("ispyb_client").error("============================================================")
                    sessions = []

            except URLError:
                logging.getLogger("ispyb_client").error("=======================CONNECTION==============================")
                logging.getLogger("ispyb_client").exception(_CONNECTION_ERROR_MSG)
                logging.getLogger("ispyb_client").error("===============================================================")
                return {'Proposal': {},
                        'Person': {},
                        'Laboratory': {},
                        'Session': {},
                        'status': {'code':'error'}}

            return  {'Proposal': self.convert_to_dict(proposal),
                     'Person': self.convert_to_dict(person),
                     'Laboratory': self.convert_to_dict(lab),
                     'Session': sessions,
                     'status': {'code':'ok'}}

        else:

            logging.getLogger("ispyb_client").\
                exception("Error in get_proposal: Could not connect to server," + \
                          " returning empty proposal")

            return {'Proposal': {},
                    'Person': {},
                    'Laboratory': {},
                    'Session': {},
                    'status': {'code':'error'}}


    @trace
    def get_proposal_by_username(self, username):

        print(f"FEATCHING proposal by userName: {username}")

        proposal_code   = ""
        proposal_number = 0

        empty_dict = {'Proposal': {}, 'Person': {}, 'Laboratory': {}, 'Session': {}, 'status': {'code':'error'}}

        if not self._shipping:
           logging.getLogger("ispyb_client").\
                warning("Error in get_proposal: Could not connect to server," + \
                          " returning empty proposal")
           return empty_dict


        try:
            try:
                person = self._shipping.service.findPersonByLogin(username, self.beamline_name)
            except Fault as e:
                logging.getLogger("ispyb_client").warning(str(e))
                person = {}

            try:
                proposal = self._shipping.service.findProposalByLoginAndBeamline(username, self.beamline_name)
                if not proposal:
                    logging.getLogger("ispyb_client").warning("Error in get_proposal: No proposal has been found to  the user, returning empty proposal")
                    return empty_dict
                proposal_code   = proposal.code
                proposal_number = proposal.number
            except Fault as  e:
                logging.getLogger("ispyb_client").warning(str(e))
                proposal = {}

            try:
                lab = self._shipping.service.findLaboratoryByCodeAndNumber(proposal_code, proposal_number)
            except Fault as e:
                logging.getLogger("ispyb_client").warning(str(e))
                lab = {}

            try:
                res_sessions = self._collection.service.\
                    findSessionsByProposalAndBeamLine(proposal_code,
                                                           proposal_number,
                                                           self.beamline_name)
                sessions = []

                # Handels a list of sessions
                for session in res_sessions:
                    if session is not None :
                        try:
                            session.startDate = \
                                datetime.strftime(session.startDate,
                                                  "%Y-%m-%d %H:%M:%S")
                            session.endDate = \
                                datetime.strftime(session.endDate,
                                                  "%Y-%m-%d %H:%M:%S")
                        except:
                            pass

                        sessions.append(utf_encode(dict(session)))

            except Fault as e:
                logging.getLogger("ispyb_client").warning(str(e))
                sessions = []

        except URLError:
            logging.getLogger("ispyb_client").warning(_CONNECTION_ERROR_MSG)
            return empty_dict


        logging.getLogger("ispyb_client").info( str(sessions) )

        return  {'Proposal': self.convert_to_dict(proposal),
                    'Person': self.convert_to_dict(person),
                    'Laboratory': self.convert_to_dict(lab),
                    'Session': sessions,
                    'status': {'code':'ok'}}

    def check_to_string(self, b_obj):
        if isinstance(b_obj, str):
            s = b_obj
        else:
            s = b_obj.decode("utf-8")
        return s

    def get_todays_session(self, prop):
        print( "getting todays session")

        try:
            sessions=prop['Session']
        except KeyError:
            sessions=None

        # Check if there are sessions in the proposal
        todays_session=None
        if sessions is None or len(sessions)==0:
            pass
        else:
            # Check for today's session
            for session in sessions:

                beamline=self.check_to_string(session['beamlineName'])
                start_date="%s 08:00:00" % self.check_to_string(session['startDate'])
                end_date="%s 23:59:59" % self.check_to_string(session['endDate'])
                start_date = start_date.split()[0]
                end_date = end_date.split()[0]

                try:
                    start_struct=time.strptime(start_date,"%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass
                else:
                    try:
                        end_struct=time.strptime(end_date,"%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass
                    else:
                        start_time=time.mktime(start_struct)
                        end_time=time.mktime(end_struct)
                        current_time=time.time()
                        # Check beamline name
                        if beamline==self.beamline_name:

                            # Check date
                            if current_time>=start_time and current_time<=end_time:
                                todays_session=session
                                # Adding extra info to pass along
                                todays_session['proposalNumber']=prop['Proposal']['number']
                                todays_session['proposalTitle']=prop['Proposal']['title']
                                todays_session['proposalCode']=prop['Proposal']['code']

                                break


        if todays_session:
            print("FOUND A SESSION FOR TODAY : NEW SESSION FLAG FALSE")
            new_session_flag= False
            session_id=todays_session['sessionId']
            logging.getLogger('HWR').debug('getting local contact for %s' % session_id)
            #localcontact=self.get_session_local_contact(session_id)

        else :
            new_session_flag= True
            current_time=time.localtime()
            start_time=time.strftime("%Y-%m-%d 00:00:00", current_time)
            end_time=time.mktime(current_time)+60*60*24
            tomorrow=time.localtime(end_time)
            end_time=time.strftime("%Y-%m-%d 07:59:59", tomorrow)


            # Create a session
            new_session_dict={}

            new_session_dict['proposalId']=prop['Proposal']['proposalId']
            new_session_dict['proposalNumber']=prop['Proposal']['number']
            new_session_dict['proposalTitle']=prop['Proposal']['title']
            new_session_dict['proposalCode']=prop['Proposal']['code']


            new_session_dict['startDate']=start_time
            new_session_dict['endDate']=end_time
            new_session_dict['beamlineName']=self.beamline_name
            new_session_dict['scheduled']=0
            new_session_dict['nbShifts']=3
            new_session_dict['comments']="Session created by the BCM"
            session_id=self.create_session(new_session_dict)
            new_session_dict['sessionId']=session_id

            todays_session=new_session_dict
            localcontact=None
            logging.getLogger('HWR').debug('create new session')

        # Hack to bypass self.session_hwobj which is not available here for now
        is_inhouse = True #self.session_hwobj.is_inhouse(prop['Proposal']["code"], prop['Proposal']["number"])
        return {"session": todays_session,"new_session_flag":new_session_flag, "is_inhouse": is_inhouse}


    def __find_sample(self, sample_ref_list, code = None, location = None):
        """
        Returns the sample with the matching "search criteria" <code> and/or
        <location> with-in the list sample_ref_list.

        The sample_ref object is defined in the head of the file.

        :param sample_ref_list: The list of sample_refs to search.
        :type sample_ref: list

        :param code: The vial datamatrix code (or bar code)
        :param type: str

        :param location: A tuple (<basket>, <vial>) to search for.
        :type location: tuple
        """
        for sample_ref in sample_ref_list:

            if code and location:
                if sample_ref.code == code and \
                        sample_ref.container_reference == location[0] and \
                        sample_ref.sample_reference == location[1]:
                    return sample_ref
            elif code:
                if sample_ref.code == code:
                    return sample_ref
            elif location:
                if sample_ref.container_reference == location[0] and \
                       sample_ref.sample_reference == location[1]:
                    return sample_ref

        return None


    #@trace



    @trace
    def get_session_samples(self, proposal_id, session_id, sample_refs):
        """
        Retrives the list of samples associated with the session <session_id>.
        The samples from ISPyB is cross checked with the ones that are
        currently in the sample changer.

        The datamatrix code read by the sample changer is used in case
        of conflict.

        :param proposal_id: ISPyB proposal id.
        :type proposal_id: int

        :param session_id: ISPyB session id to retreive samples for.
        :type session_id: int

        :param sample_refs: The list of samples currently in the
                            sample changer. As a list of sample_ref
                            objects
        :type sample_refs: list (of sample_ref objects).

        :returns: A list with sample_ref objects.
        :rtype: list
        """
        if self._tools_ws:
            sample_references = []
            session = self.get_session(session_id)
            response_samples = []

            for sample_ref in sample_refs:
                sample_reference = SampleReference(*sample_ref)
                sample_references.append(sample_reference)

            try:
                response_samples = self._tools_ws.service.\
                    findSampleInfoLightForProposal(proposal_id,
                                                   self.beamline_name)

            except Fault as e:
                logging.getLogger("ispyb_client").exception(str(e))
            except URLError:
                logging.getLogger("ispyb_client").exception(_CONNECTION_ERROR_MSG)

            samples = []
            for sample in response_samples:
                try:
                    loc = [None, None]
                    try:
                      loc[0]=int(sample.containerSampleChangerLocation)
                    except:
                      pass
                    try:
                      loc[1]=int(sample.sampleLocation)
                    except:
                      pass

                    # Unmatched sample, just catch and do nothing
                    # (dont remove from sample_ref)
                    if not sample.code and \
                            not sample.sampleLocation:
                        pass
                    # Sample location and code was found in ISPyB and they match
                    # with the sample changer.
                    elif sample.code and sample.sampleLocation:
                        sc_sample = \
                            self.__find_sample(sample_references,
                                               code = sample.code,
                                               location = loc)

                        # The sample codes dose not match
                        if not sc_sample:
                            sc_sample = self.__find_sample(sample_references,
                                                           location = loc)

                            if sc_sample.code != '':
                                sample.code = sc_sample.code

                        sample_references.remove(sc_sample)


                    # Only location was found, update with the code
                    # from sample changer if it exists.
                    elif sample.sampleLocation:
                        sc_sample = \
                            self.__find_sample(sample_references,
                                               location = loc)
                        if sc_sample:
                            sample.sampleCode = sc_sample.code
                            sample_references.remove(sc_sample)

                    # Sample code was found in ISPyB but dosent match with
                    # the samplechanger at given location
                    #
                    # Use the information from the sample changer.
                    else:
                        #Use sample changer code for sample  ?
                        sample.containerSampleChangerLocation = \
                            sample_references.containter_referance
                        sample.sampleLocation = \
                            sample_references.sample_reference

                        loc = (int(sample.containerSampleChangerLocation),
                               int(sample.sampleLocation))

                        sc_sample = \
                            self.__find_sample(sample_references,
                                               location = loc)
                        if sc_sample:
                            sample.code = sc_sample.code
                            sample_references.remove(sc_sample)


                    samples.append(utf_encode(dict(sample)))

                except:
                    pass


            # Add the unmatched samples to the result from ISPyB
            for sample_ref in sample_references:
                samples.append(
                    {'code': sample_ref.code,
                     'location': sample_ref.sample_reference,
                     'containerSampleChangerLocation': sample_ref.container_reference})
                #  samples.append(
            return {'loaded_sample': samples,
                    'status': {'code':'ok'}}
        else:
            logging.getLogger("ispyb_client").\
                exception("Error in get_session_samples: could not connect " + \
                          "to server")


    @trace
    def get_bl_sample(self, bl_sample_id):
        """
        Fetch the BLSample entry with the id bl_sample_id

        :param bl_sample_id:
        :type bl_sample_id: int

        :returns: A BLSampleWSValue, defined in the wsdl.
        :rtype: BLSampleWSValue

        """

        if self._tools_ws:

            try:
                result = self._tools_ws.service.findBLSample(bl_sample_id)
            except Fault as e:
                logging.getLogger("ispyb_client").exception(str(e))
            except URLError:
                logging.getLogger("ispyb_client").exception(_CONNECTION_ERROR_MSG)

            return utf_encode(dict(result))
        else:
            logging.getLogger("ispyb_client").\
                exception("Error in get_bl_sample: could not connect to server")

    @trace
    def create_session(self, session_dict):
        """
        Create a new session for "current proposal", the attribute
        porposalId in <session_dict> has to be set (and exist in ISPyB).

        :param session_dict: Dictonary with session parameters.
        :type session_dict: dict

        :returns: The session id of the created session.
        :rtype: int
        """
        if self._collection:

            try:
                # The old API used date formated strings and the new
                # one uses DateTime objects.
                session_dict["startDate"]  = datetime.\
                    strptime(session_dict["startDate"] , "%Y-%m-%d %H:%M:%S")
                session_dict["endDate"] = datetime.\
                    strptime(session_dict["endDate"], "%Y-%m-%d %H:%M:%S")

                session = self._collection.service.\
                    storeOrUpdateSession(session_dict)

                # changing back to string representation of the dates,
                # since the session_dict is used after this method is called,
                session_dict["startDate"]  = datetime.\
                    strftime(session_dict["startDate"] , "%Y-%m-%d %H:%M:%S")
                session_dict["endDate"] = datetime.\
                    strftime(session_dict["endDate"], "%Y-%m-%d %H:%M:%S")

            except Fault as e:
                session = {}
                logging.getLogger("ispyb_client").exception(str(e))
            except URLError:
                logging.getLogger("ispyb_client").exception(_CONNECTION_ERROR_MSG)

            return session
        else:
            logging.getLogger("ispyb_client").\
                exception("Error in create_session: could not connect to server")


    @trace
    def update_session(self, session_dict):
        """
        Update the session with the data in <session_dict>, the attribute
        sessionId in <session_dict> must be set.

        Warning: Missing attibutes in <session_dict> will set to null,
                 this could leed to loss of data.

        :param session_dict: The session to update.
        :type session_dict: dict

        :returns: None
        """
        if self._collection:
            return self.create_session(session_dict)
        else:
            logging.getLogger("ispyb_client").\
                exception("Error in update_session: could not connect to server")


    """def _get_proposals(self, username: str):
        proposals = json.loads(
            self._shipping.service.findProposalsByLoginName(username)
        )

        for proposal in proposals:
            if proposal["type"].upper() not in ["MX", "MB"]:
                continue
            if proposal.get("state", "Open") != "Open":
                continue

            yield proposal

    def _get_sessions(self, username: str, beamline_name: str) -> List[Session]:


        def list_sessions():

            for proposal in self._get_proposals(username):
                sessions = self._collection.service.findSessionsByProposalAndBeamLine(
                    proposal["code"], proposal["number"], beamline_name
                )

                for sesssion in sessions:
                    yield _create_session_object(
                        proposal, sesssion["sessionId"], beamline_name
                    )

                #
                # A hack to lazily create new sessions.
                #
                # At MAXIV we don't schedule sessions for proposals ahead of time. Instead, we
                # lazily create them as needed.
                #
                # If a proposal does not contain any active session, create a Session object
                # with a special session ID.
                #
                # If user selects such a session, then we will ask ISPyB to create this session.
                #
                if len(sessions) == 0:
                    yield _create_session_object(
                        proposal, _get_lazy_session_id(proposal), beamline_name
                    )

        return sorted(list_sessions(), key=lambda s: f"{s.code}{s.number}")

    def get_sessions_by_username(
        self, username: str, beamline_name: str
    ) -> LimsSessionManager:
        try:
            sessions = list(self._get_sessions(username, beamline_name))
            return LimsSessionManager(sessions=sessions)
        except Fault as e:
            log.exception(e.message)"""


class PX1ISPyBLims(ProposalTypeISPyBLims):
    def __init__(self, name):
        super().__init__(name)
        self.login_type = "Proposal"
        self.adapter = None
        self.user_name = None
        self.session_manager = None

    def init(self):
        self.beamline_name = "PROXIMA1"#self.get_property("beamline_name")
        self.site = self.get_property("site")
        self.adapter = self._create_data_adapter()
        self.ldapConnection = self.get_object_by_role("ldapServer")

    def _create_data_adapter(self) -> ISPyBDataAdapter:
        print("Creating curstom data adapter")

        data_adapter  = CustomISPyBDataAdapter(self.ws_root.strip(),
                                               self.ws_username,
                                               self.ws_password,
                                               self.beamline_name,)

        if not data_adapter._shipping :
            data_adapter.initialize_services()

        print(f"Created data_adapter of type {data_adapter}")
        return data_adapter

    def get_samples(self, lims_name):

        response_samples = None
        proposal_id = self.session_manager.active_session.proposal_id
        print(f"====================================PROPOSAL id updated to {proposal_id}")

        # at this point the proposal id is 4
        # Zeep SOAP request fails with pointer erro
        # also happens for prpoposal id 20100023

        if self.adapter._tools_ws:
            try:
                    response_samples = self.adapter._tools_ws.service.\
                    findSampleInfoLightForProposal(proposal_id,
                                                   self.beamline_name)


            except Fault as e:
                response_samples = []
                logging.getLogger("ispyb_client").exception(str(e))
                return []
            except URLError:
                return []
                logging.getLogger("ispyb_client").exception(_CONNECTION_ERROR_MSG)
        else:
            logging.getLogger("ispyb_client").\
                exception("Error in get_samples: could not connect to server")

        # Raw data from ISPyB contains bytes objects : needs preprocessing
        time.sleep(10)
        if response_samples :
            response_samples = [self.adapter.convert_to_dict(z_obj)for z_obj in response_samples]
            response_samples = [self.repare_bytes_dict(d) for d in response_samples]

        print(f"====================================PX1ISPyBLims  get_samples {response_samples[0]}\n========================== dict exemple")


        return response_samples

    def repare_bytes_dict (self, dct):

        bytes_entries = [(self.check_to_string(k),k,
                      self.check_to_string(v))
                      for k, v in dct.items()
                      if (isinstance(k, bytes)
                      or isinstance(v, bytes))]

        for i in bytes_entries :
            if isinstance(i[1], bytes):
                print(f"CONVERTING dict entry {i[1]}")
                del dct[i[1]]
                dct[i[0]] = dct[i[2]]

            else:
                dct[i[0]] = i[2]

        dict_entries = [(k, self.repare_bytes_dict(d) )for k,d in [(l,v) for l,v in dct.items() if isinstance(v, dict)]]

        for e in range(len(dict_entries)):
            dct[dict_entries[e][0]] = dict_entries[e][1]
        return dct



    """def convert_to_dict(self, zeep_obj):
        # Convert the zeep object to a dictionary
        proposal_dict = serialize_object(zeep_obj, dict)

        def utf_encode(obj):
            if isinstance(obj, str):
                return obj.encode('utf-8')
            elif isinstance(obj, dict):
                return {k: utf_encode(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [utf_encode(item) for item in obj]
            return obj"""


        # Then wrap it in the 'Proposal' key and encode
        #return utf_encode(proposal_dict)
    def check_to_string(self, b_obj):
        if isinstance(b_obj, bytes):
            s = b_obj.decode("utf-8")
        else:
            s = b_obj
        return s

    def login(self, pid):
        self.user_name = pid
        #self.data_adapter = self._create_data_adapter()
        proposal = self.adapter.get_proposal(pid)
        todays_session = self.adapter.get_todays_session(proposal)
        start_datetime_str = self.check_to_string(todays_session["session"]['startDate'])
        # Parse the string into a datetime object
        start_dt_object = datetime.strptime(start_datetime_str, '%Y-%m-%d %H:%M:%S')
        start_date_str = start_dt_object.strftime("%Y%m%d")
        start_time_str = start_dt_object.strftime("%H:%M:%S")
        end_datetime_str = self.check_to_string(todays_session["session"]['endDate'])
        end_dt_object = datetime.strptime(end_datetime_str, '%Y-%m-%d %H:%M:%S')
        end_date_str = end_dt_object.strftime("%Y%m%d")
        end_time_str = end_dt_object.strftime("%H:%M:%S")
        lims_session_object = lims_Session()
        lims_session_object.start_date = start_date_str
        lims_session_object.start_time = start_time_str

        lims_session_object.end_date = end_date_str
        lims_session_object.end_time = end_time_str

        lims_session_object.session_id = todays_session["session"]['sessionId']
        lims_session_object.beamline_name = self.beamline_name

        lims_session_object.proposal_id = todays_session["session"]["proposalId"]
        lims_session_object.proposal_name = f"mx{pid}"
        lims_session_object.title = self.check_to_string(todays_session["session"]["proposalTitle"])


        lims_session_object.code = self.check_to_string(todays_session["session"]["proposalCode"])
        lims_session_object.number = self.check_to_string(todays_session["session"]["proposalNumber"])


        lims_session_object.actual_start_date = ""
        lims_session_object.actual_start_time = ""
        lims_session_object.actual_end_date = ""
        lims_session_object.actual_end_time = ""
        lims_session_object.start_datetime = datetime.now()
        lims_session_object.end_datetime = datetime.now() + timedelta(days=1)



        lims_session_object.nb_shifts = "3"
        lims_session_object.scheduled  = "3"

        # status of the session depending on wether it has been rescheduled or moved
        lims_session_object.is_rescheduled = False
        lims_session_object.is_scheduled_time = True
        lims_session_object.is_scheduled_beamline = True


        LSM = LimsSessionManager()
        LSM.active_session = lims_session_object
        self.session_manager = LSM
        print(f"trturning Lims Session Manager {LSM}\n\n\n this is tghe active sesstion---->{LSM.active_session}")

        return LSM #session #return super().login(loginID, psd)


    def get_lims_name(self):
        return ["ISPyB"]

    def set_active_session_by_id(self, session_id: str) -> lims_Session:

        """
        Sets session with session_id to active session

        Args:
            session_id: session id
        """

        def find_session() -> Optional[lims_Session]:
            for session in self.session_manager.sessions:
                if session.session_id == session_id:
                    self.session_manager.active_session = session

                    return session

            # session not found
            return None

        def replace_lazy(sessions: List[lims_Session], new_session: lims_Session):
            def gen():
                for session in sessions:
                    if session.session_id == session_id:
                        yield new_session
                    else:
                        yield session

            return list(gen())

        session = find_session()
        if session is None:
            raise Exception(f"no session with ID {session_id} found")



        #
        # user selected a session that does not exist yet,
        # ask ISPyB to create it
        #
        if _is_lazy_session_id(session_id):
            session = self.adapter.create_session(
                session.proposal_id, session.beamline_name
            )
            # replace the old lazy-session object,
            # with the new proper-session object
            self.session_manager.sessions = replace_lazy(
                self.session_manager.sessions, session
            )

        return session
    """
    def get_full_user_name(self) -> str:
        if not self.adapter:
            self.adapter = self._create_data_adapter()
        person = self.adapter.get_person_by_username(self.user_name)

        given_name = person["givenName"]
        family_name = person["familyName"]

        return f"{given_name} {family_name}
class ISPyBClient2(HardwareObject):
    def get_samples(self, proposal_id, session_id):
        response_samples = None

        if self._tools_ws:
            try:
                response_samples = self._tools_ws.service.\
                      findSampleInfoLightForProposal(proposal_id,
                                                     self.beamline_name)
            except WebFault as e:
                logging.getLogger("ispyb_client").exception(str(e))
            except URLError:
                logging.getLogger("ispyb_client").exception(_CONNECTION_ERROR_MSG)
        else:
            logging.getLogger("ispyb_client").\
                exception("Error in get_samples: could not connect to server")

        return response_samples  """




