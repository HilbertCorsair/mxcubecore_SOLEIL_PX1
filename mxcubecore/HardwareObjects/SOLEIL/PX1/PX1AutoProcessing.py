
import logging
import json
import os
import copy
import glob
import time
import traceback
import tempfile

import gevent

from mxcubecore.BaseHardwareObjects import HardwareObject
import subprocess
import collections

log = logging.getLogger("HWR")

class ProcessingOption(object):
    def __init__(self, option_name, option_type):
        self.name = option_name
        self.option_type = option_type
        self.value = None

    def get_name(self):
        return self.name

    def get_option_type(self):
        return self.option_type

    def get_value(self):
        if isinstance (self.option_type, bool):
            return (self.value is None) and False or self.value
        else:  # str
            return (self.value is None) and '' or self.value

    def set_value(self, value):
        self.value = value

class PX1AutoProcessing(HardwareObject):

   TMPFILE_PREFIX = "mxcube_autoproc_"

   def init(self):
       self.exec_program = self.get_property("executable")

       # <blocking>True</blocking> restores the legacy behaviour: the caller's
       # greenlet waits for the processing program to exit.
       self.blocking = str(self.get_property("blocking", False)) in ("True", "true")
       self.tmpdir = self.get_property("tmpdir", "/tmp")
       self.tmpfile_ttl_days = int(self.get_property("tmpfile_ttl_days", 7))
       self._sweep_old_tmpfiles()

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

   def start_autoprocessing(self, collect_pars, wait=None):
       """Snapshot the collect parameters and launch the processing pipeline.

       The snapshot is taken synchronously in the caller's greenlet, so the
       background job owns a private copy: collect_pars IS
       HWR.beamline.collect.current_dc_parameters by reference and keeps being
       mutated (thumbnails, lims preparation) after collection_finished()
       returns, and is replaced outright by the next sample.

       The slow half - the LIMS update, the temp file and the processing
       program itself - runs in a background greenlet so the queue can go on to
       unmount the sample and load the next one.

       :param collect_pars: the live dc-parameters dict.
       :param wait: None uses the <blocking> property (default False); True runs
                    inline and returns the exit code; False always spawns.
       :returns: the gevent.Greenlet when backgrounded, the exit code otherwise.
       """
       log.debug("PX1AutoProcessing / executable: %s" % self.exec_program)
       payload = self._snapshot_collect_pars(collect_pars)
       prefix = payload.get("fileinfo", {}).get("prefix", "unknown")

       blocking = self.blocking if wait is None else wait

       if blocking:
           return self._run_job(payload, prefix)

       log.info("PX1AutoProcessing / launching processing for %s in background", prefix)
       return gevent.spawn(self._run_job, payload, prefix)

   def _snapshot_collect_pars(self, collect_pars):
       """Private, JSON-safe deep copy of collect_pars.

       Runs in the caller's greenlet because it reads live hardware objects
       (motor mnemonics, processing options, beam shape).
       """
       from mxcubecore import HardwareRepository as HWR

       motors = collect_pars.get("motors") or {}
       payload = copy.deepcopy(
           {ky: val for ky, val in collect_pars.items() if ky != "motors"}
       )

       motors_by_name = {}
       for ky, val in motors.items():
           if not isinstance(ky, str):
               ky = ky.get_motor_mnemonic().replace("/", "")
           motors_by_name[ky] = val
       payload["motors"] = motors_by_name

       payload["autoproc_options"] = self.get_options_as_dict()

       try:
           payload["beamShape"] = HWR.beamline.beam.get_beam_shape().value
       except Exception:
           log.exception("PX1AutoProcessing / could not read the beam shape")

       payload = HWR.beamline.lims.repare_bytes_dict(payload)

       # Fail here, in context, rather than inside the background greenlet.
       json.dumps(payload, default=str)

       return payload

   def _run_job(self, payload, prefix):
       """Update LIMS, write the parameter file and run the processing program."""
       from mxcubecore import HardwareRepository as HWR

       tmpfile = None

       try:
           HWR.beamline.lims.update_data_collection(payload)
           jsonstr = json.dumps(payload, default=str)

           fd, tmpfile = tempfile.mkstemp(
               dir=self.tmpdir,
               suffix=".json",
               prefix="%s%s_" % (self.TMPFILE_PREFIX, prefix),
           )
           os.write(fd, jsonstr.encode("utf-8"))
           os.close(fd)

           log.debug("PX1AutoProcessing / saved collect pars to file %s" % tmpfile)

           if log.isEnabledFor(logging.DEBUG):
               for ky, val in payload.items():
                   log.debug("   - % 12s : %s" % (ky, str(val)))

           cmd = "%s %s" % (self.exec_program, tmpfile)
           log.info("PX1AutoProcessing / executing command %s" % cmd)

           p1 = subprocess.Popen(
               cmd, shell=True, stdin=None,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
           )
           out, err = p1.communicate()

           if out:
               log.info("PX1AutoProcessing / <output>\n%s" % out)
           if err:
               log.warning("PX1AutoProcessing / <error>\n%s" % err)

           if p1.returncode:
               log.error(
                   "PX1AutoProcessing / %s exited with code %s"
                   % (prefix, p1.returncode)
               )
               logging.getLogger("user_level_log").error(
                   "Autoprocessing failed for %s (see log)" % prefix
               )

           return p1.returncode
       except gevent.GreenletExit:
           # Shutdown or explicit kill. The processing program was started
           # detached and is deliberately left to finish on its own.
           log.warning(
               "PX1AutoProcessing / abandoning %s (parameter file %s)"
               % (prefix, tmpfile)
           )
           raise
       except Exception:
           log.error("PX1AutoProcessing / error starting autoprocessing for %s" % prefix)
           log.error(traceback.format_exc())
           logging.getLogger("user_level_log").error(
               "Autoprocessing could not be started for %s (see log)" % prefix
           )

   def _sweep_old_tmpfiles(self):
       """Remove parameter files left behind by past runs.

       They are deliberately not deleted when a job finishes: exec_program is a
       wrapper that may submit a cluster job which re-reads the file after the
       wrapper exits. Sweeping only at startup guarantees nothing in flight is
       removed.
       """
       cutoff = time.time() - self.tmpfile_ttl_days * 86400

       try:
           stale = glob.glob(os.path.join(self.tmpdir, self.TMPFILE_PREFIX + "*.json"))
       except Exception:
           log.exception("PX1AutoProcessing / could not list old parameter files")
           return

       for path in stale:
           try:
               if os.path.getmtime(path) < cutoff:
                   os.unlink(path)
           except OSError:
               pass


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