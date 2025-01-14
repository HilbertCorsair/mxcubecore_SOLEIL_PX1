
import logging
import json
import os
import tempfile

from mxcubecore.BaseHardwareObjects import HardwareObject
import subprocess
import collections

log = logging.getLogger("HWR")

class ProcessingOption(object):
    def __init__(self, option_name, option_type):
        self.name = option_name
        self.option_type = option_type
        self.value = None

        #if self.option_type is 'boolean':
        #    if default_value.lower() in ['true','yes']:
        #        self.default_value = True
        #    else:
        #        self.default_value = False
        #else:
        #    self.default_value = default_value

    def get_name(self):
        return self.name

    def get_option_type(self):
        return self.option_type

    def get_value(self):
        if self.option_type is 'boolean':
            return (self.value is None) and False or self.value
        else:  # str
            return (self.value is None) and '' or self.value

    def set_value(self, value):
        self.value = value

class PX1AutoProcessing(HardwareObject):

   def init(self):
       self.exec_program = self.get_property("executable")

       self.proc_options = collections.OrderedDict()
       self.profiles = {'default': ''}

       for option in self['options']:
           opt_name = option.get_property('name')
           opt_type = option.get_property('type')
           #opt_default = option.get_property('default')
           self.proc_options[opt_name] = \
                 ProcessingOption(opt_name, opt_type)

       for profile in self['profiles']:
           prof_name = profile.get_property('name')
           prof_options = profile.get_property('options')
           self.profiles[prof_name] = prof_options

       selected_prof = self.get_property('active_profile')

       if selected_prof in self.profiles:
            self.selected_profile = selected_prof
       else:
            log.debug("PX1AutoProcessing - selected profiles %s does not exist.Using default")
            self.selected_profile = 'default'

       default_options = self.profiles[self.selected_profile]

       if not default_options:
           self.run_processing_default = False
           log.debug("PX1AutoProcessing - 'no processing' selected by default")
       else:
           self.run_processing_default = True

       # set options values taking into account the current defaults

       defaults = default_options.split(',')  
       log.debug("PX1AutoProcessing - default options are: %s" % str(defaults))

       self.default_options = {}

       for default in defaults:
           pars = default.split('=') 
           if len(pars) == 1:
               optname = pars[0]
               optval = None
           else:
               optname = pars[0]
               optval = pars[1]

           self.default_options[optname] = optval

       for optname,option in self.proc_options.items():
           if option.get_option_type() == 'boolean':
               if optname in self.default_options:
                   optval = self.default_options[optname]
                   value = True
                   option.set_value(value)
           else: # type str / default should appear for example as optname=5 in the default options
               if optname in self.default_options:
                   value = self.default_options[optname]
                   option.set_value(value)
            
   def get_run_processing_default(self):
       return self.run_processing_default
       
   def get_option_list(self):
       return self.proc_options.keys()

   def get_options(self):
       return self.proc_options

   def get_selected_profile(self):
       return self.selected_profile

   def set_options(self, option_values):

       logging.getLogger("HWR").debug("PX1AutoProcessing setting options : %s" % str(option_values))

       for option_name, value in option_values.items():
           self.set_option(option_name,value)

       logging.getLogger("HWR").debug("PX1AutoProcessing options set: %s" % self.get_options_as_string()) 
       logging.getLogger("HWR").debug("PX1AutoProcessing options set: %s" % str(self.get_options_as_dict())) 

   def set_option(self, option_name, value):
       self.proc_options[option_name].set_value(value)

   def get_options_as_string(self):
       return ','.join(["%s=%s" % (opt.get_name(), opt.get_value()) \
                            for opt in self.proc_options.values()])

   def get_options_as_dict(self):
       return { opt.get_name(): opt.get_value() \
                            for opt in self.proc_options.values() }

   def start_autoprocessing(self, collect_pars):
       logging.getLogger("HWR").debug("PX1AutoProcessing / Starting autoprocessing") 
       logging.getLogger("HWR").debug("   - executable: %s" % self.exec_program)
       logging.getLogger("HWR").debug("   - collect_pars (keys only): %s" % collect_pars.keys())

       # adapt motors entry to avoid trying json on instance
       motors = collect_pars['motors']
       collect_pars["autoproc_options"] = self.get_options_as_dict()

       motors_by_name = {} 
       for ky, val in motors.items(): 
           if type(ky) is not str:
               ky = ky.get_motor_mnemonic()
               ky = ky.replace("/","")
           motors_by_name[ky] = val
       collect_pars['motors'] = motors_by_name

       logging.getLogger("HWR").debug("\n\n   - collect_pars (all): ") 
       for ky,val in collect_pars.items():
            logging.getLogger("HWR").debug("   - % 12s : %s" % (ky, str(val))) 
       logging.getLogger("HWR").debug("\n")
 
       jsonstr = json.dumps(collect_pars)
       
       fd, name = tempfile.mkstemp(dir="/tmp")
       logging.getLogger("HWR").error("PX1AutoProcessing / saving collect pars to file %s" % name)
       os.write(fd, jsonstr)
       os.close(fd)
       
       try:
           cmd = "%s %s" % (self.exec_program, name)
           logging.getLogger("HWR").error("PX1AutoProcessing /  executing command %s" % cmd)
           p1 = subprocess.Popen(cmd,  shell=True, stdin=None,stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
           out,err = p1.communicate()
           if out:
                logging.getLogger("HWR").error("PX1AutoProcessing / <output>\n%s" % out)
           if err:
                logging.getLogger("HWR").error("PX1AutoProcessing / <error>\n%s" % err)
       except:
           import traceback
           logging.getLogger("HWR").error("PX1AutoProcessing /  error starting autoprocessing ")
           logging.getLogger("HWR").error( traceback.format_exc())
        

def test_hwo(hwo):
    dc = {'comment': '', 
          'energy': 12.699990213848348, 
          'motors': {'sampx': None, 'sampy': None, 'phi': None, 'kappa': None, 'kappa_phi': None, 'zoom': None, 'beam_x': None, 'phiz': None, 'phiy': None, 'beam_y': None}, 
          'take_snapshots': 0, 
          'take_video': False, 
          'in_interleave': None, 
          'fileinfo': {'run_number': 1, 
                       'prefix': 'local-user', 
                       'template': 'local-user_1_%05d.cbf', 
                       'archive_directory': '/data1-1/proxima1-soleil/2018_Run3/2018-08-28/local-user/ARCHIVE', 
                       'directory': '/data1-1/proxima1-soleil/2018_Run3/2018-08-28/local-user/RAW_DATA', 
                       'process_directory': '/data1-1/proxima1-soleil/2018_Run3/2018-08-28/local-user/PROCESSED_DATA'}, 
          'in_queue': False, 
          'detector_mode': [], 
          'shutterless': True, 
          'do_inducedraddam': False, 
          'sample_reference': {'cell': '0,0,0,0,0,0', 
                               'spacegroup': '', 
                               'blSampleId': -1}, 
          'status': 'Running', 
          'processing': 'True', 
          'residues': 200, 
          'dark': False, 
          'oscillation_sequence': [   {'exposure_time': 0.1, 
                                       'kappaStart': 6.80352076888e-05, 
                                       'start_image_number': 1, 
                                       'mesh_range': (), 
                                       'number_of_lines': 1, 
                                       'phiStart': 0.0, 
                                       'number_of_images': 1, 
                                       'overlap': 0.0, 
                                       'start': 0.0, 
                                       'range': 0.1, 
                                       'number_of_passes': 1}], 

          'EDNA_files_dir': '/data1-1/proxima1-soleil/2018_Run3/2018-08-28/local-user/PROCESSED_DATA', 
          'transmission': 15.0, 
          'collection_start_time': '2018-08-28 10:56:06', 
          'anomalous': False, 
          'xds_dir': '', 
          'sessionId': '', 
          'experiment_type': 'OSC', 
          'group_id': None, 
          'resolution': {'upper': 6.051412804054394}, 
          'skip_images': True,
        }

    hwo.start_autoprocessing(dc)
