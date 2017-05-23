"""
(c) RIKEN 2015. All rights reserved. 
Author: Keitaro Yamashita

This software is released under the new BSD License; see LICENSE.
"""
from yamtbx.dataproc.auto import gui_config as config
from yamtbx.dataproc.auto import gui_logger as mylog
from yamtbx.dataproc.auto import html_report
from yamtbx.dataproc.xds import xds_inp
from yamtbx.dataproc.xds import get_xdsinp_keyword
from yamtbx.dataproc.xds import idxreflp
from yamtbx.dataproc.xds import integratelp
from yamtbx.dataproc.xds import correctlp
from yamtbx.dataproc.xds import xdsstat
from yamtbx.dataproc.xds import xparm
from yamtbx.dataproc.xds.command_line import estimate_resolution_by_spotxds
from yamtbx.dataproc.adxv import Adxv
from yamtbx.dataproc.dataset import find_existing_files_in_template
from yamtbx.dataproc.bl_logfiles import BssJobLog
from yamtbx.util import batchjob, directory_included, read_path_list, safe_float, expand_wildcard_in_list
from yamtbx.util.xtal import format_unit_cell

import iotbx.phil
import libtbx.phil
from libtbx.utils import multi_out
import libtbx.easy_mp
from cctbx import sgtbx
from cctbx.crystal import reindex

import wx
#import wx.gizmos
from wx.lib.mixins.listctrl import CheckListCtrlMixin, ListCtrlAutoWidthMixin
import wx.html
import wx.lib.newevent
import wx.lib.agw.pybusyinfo
import wx.lib.scrolledpanel
import matplotlib
import matplotlib.backends.backend_wxagg
import numpy
import os
import sys
import re
import datetime
import time
import StringIO
import cPickle as pickle
import glob
import threading
import traceback

EventShowProcResult, EVT_SHOW_PROC_RESULT = wx.lib.newevent.NewEvent()
EventLogsUpdated, EVT_LOGS_UPDATED = wx.lib.newevent.NewEvent()


gui_phil_str = """\
topdir = None
 .type = path
 .help = files/subdirectories in this directory will be processed
include_dir = None
 .type = path
 .multiple = true
 .help = directories to include (those not matched will be excluded)
exclude_dir = None
 .type = path
 .multiple = true
 .help = directories to exclude (those not matched will be included)
workdir = _kamoproc
 .type = str
 .help = working directory. if relpath, workdir will be made in topdir.
adxv = None
 .type = path
 .help = adxv command

bl = 32xu 41xu 26b1 26b2 38b1 12b2 other
 .type = choice(multi=False)
 .help = Choose beamline where you start program
blconfig = []
 .type = path
 .multiple = true
mode = zoo *normal
 .type = choice(multi=True)
 .help = When Zoo, specify mode=zoo

date = "today"
 .type = str
 .help = Data collection date ("today" or %Y-%d-%m format)
checklog_daybefore = 2
 .type = int
 .help = if 2 specified, check BSS log file from given date -2 days

jobspkl = None
 .type = path
 .help = Load jobs.pkl and don't find new jobs (for review)

logwatch_interval = 30
 .type = float
 .help = interval in sec to check BSS log & processing results

logwatch_once = None
 .type = bool
 .help = find datasets only once (when program started). Default: true if bl=other, otherwise false.

auto_mode = true
 .type = bool
 .help = automatically start processing when data collection finished.
small_wedges = true
 .type = bool
 .help = Optimized for small wedge data processing

batch {
 engine = *sge sh
  .type = choice(multi=False)
 sge_pe_name = par
  .type = str
  .help = pe name (put after -pe option)
 nproc_each = 4
  .type = int
  .help = maximum number of cores used for single data processing
 sh_max_jobs = 1
  .type = int
  .help = maximum number of concurrent jobs when engine=sh
}

use_tmpdir_if_available = true
 .type = bool
 .help = Use ramdisk or tempdir if sufficient size is available

known {
 unit_cell = None
  .type = floats(size=6)
  .help = cell dimension
 space_group = None
  .type = str
  .help = space group (no. or name)
}

auto_frame_exclude_spot_based = false
 .type = bool
 .help = automatic frame exclusion from integration based on spot search result.

exclude_ice_resolutions = false
 .type = bool

anomalous = true
 .type = bool

engine = *xds dials
 .type = choice(multi=False)

xds {
 minpk = None
  .type = float
 exclude_resolution_range = None
  .type = floats(size=2)
  .multiple = true
 repeat = 1
  .type = int(value_min=1)
  .help = if more than 1, copy GXPARM.XDS to XPARM.XDS and re-integrate
 ex = []
  .type = str
  .multiple = true
  .help = extra keywords for XDS.INP
 override {
  geometry_reference = None
   .type = path
   .help = "XDS.INP or json file of dials"
  fix_geometry_when_reference_provided = False
   .type = bool
   .help = "Don't refine geometric parameters when geometry_reference= specified."

  rotation_axis = None
   .type = floats(size=3)
   .help = "override ROTATION_AXIS= "
 }
}

merging {
 cell_grouping {
  tol_length = None
   .type = float
   .help = relative_length_tolerance
  tol_angle = None
   .type = float
   .help = absolute_angle_tolerance in degree
 }
}

reverse_phi = true
 .type = bool
split_hdf_miniset = true
 .type = bool
 .help = Whether or not minisets in hdf5 are treated individually.
split_data_by_deg = None
 .type = float
 .help = Split data with specified degrees. Currently only works with bl=other.
log_root = None
 .type = path
 .help = debug log directory
"""

def read_override_config(imgdir):
    ret = {}
    cfgin = os.path.join(imgdir, "kamo_override.config")
    if not os.path.isfile(cfgin): return ret

    for l in open(cfgin):
        l = l.strip()
        if l == "": continue
        if l.startswith("wavelength="):
            ret["wavelength"] = float(l[l.index("=")+1:].strip())
        elif l.startswith("distance="):
            ret["distance"] = float(l[l.index("=")+1:].strip())
        elif l.startswith("orgx="):
            ret["orgx"] = float(l[l.index("=")+1:].strip())
        elif l.startswith("orgy="):
            ret["orgy"] = float(l[l.index("=")+1:].strip())
        elif l.startswith("osc_range="):
            ret["osc_range"] = float(l[l.index("=")+1:].strip())
        elif l.startswith("rotation_axis="):
            ret["rotation_axis"] = map(float, l[l.index("=")+1:].strip().split())
            assert len(ret["rotation_axis"]) == 3
        else:
            shikalog.warning("Unrecognized config in %s: %s" % (cfgin, l))

    mylog.info("Read override-config from %s: %s" % (cfgin, ret))
    return ret
# read_override_config()

class BssJobs:
    def __init__(self):
        self.jobs = {} # { (path+prefix, number range) as key: }
        self.jobs_prefix_lookup = {} # {prefix: number_range in keys of self.jobs}
        self.bsslogs_checked = {}

        self.procjobs = {} # key: batchjob

        # for check_bss_log()
        self._last_job_id = None
        self._job_is_running = False
        self._prev_job_finished = False
        self._current_prefix = None
        self._joblogs = []
        self._chaches = {} # chache logfile objects. {filename: [timestamp, objects..]

        self.xds_inp_overrides = []
    # __init__()

    def load_override_geometry(self, ref_file):
        import json
        try:
            json.load(open(ref_file)) # if success..
            self.xds_inp_overrides = xds_inp.import_geometry(dials_json=ref_file)
        except:
            self.xds_inp_overrides = xds_inp.import_geometry(xds_inp=ref_file)

        if self.xds_inp_overrides:
            mylog.info("Geometry reference loaded from %s" % ref_file)
            mylog.debug("Loaded geometry: %s" % self.xds_inp_overrides)
            
    def get_job(self, key): return self.jobs.get(key, None)
    def keys(self): return self.jobs.keys()
    def get_xds_workdir(self, key):
        return os.path.join(config.params.workdir,
                            os.path.relpath(key[0]+"_%d-%d"%key[1], config.params.topdir))

    def check_bss_log(self, date, daystart):
        re_job_start = re.compile("Job ID ([0-9]+) start")
        re_job_finish = re.compile("Job ID ([0-9]+) (Stopped|Success|Killed)")
        re_prefix = re.compile("^(.*)_[x\?]+") # XXX Is this safe?

        self._prev_job_finished = False

        bsslogs = []
        for dday in xrange(daystart, 1):
            for blconfig in config.params.blconfig:
                bsslog = os.path.join(blconfig, "log",
                                      (date + datetime.timedelta(days=dday)).strftime("bss_%Y%m%d.log"))
                if os.path.isfile(bsslog): bsslogs.append(bsslog)

        for bsslog in bsslogs:
            #print "reading", bsslog

            last_line = self.bsslogs_checked.get(bsslog, -1)
            read_job_flag = False
            for i, l in enumerate(open(bsslog)):
                try:
                    if i <= last_line: continue
                    if l.startswith("echo "): continue # Must skip this line.

                    if "Beamline Scheduling Software Start" in l:
                        # reset everything
                        read_job_flag = False
                        self._job_is_running = False
                        self._prev_job_finished = True
                        self._current_prefix = None
                        continue

                    r = re_job_start.search(l)
                    if r:
                        self._last_job_id = r.group(1)
                        read_job_flag = True
                        self._job_is_running = True
                        self._current_prefix = None
                        continue

                    if read_job_flag:
                        if "Data file:" in l:
                            f = l[l.index(":")+1:].strip()
                            r = re_prefix.search(os.path.splitext(f)[0])
                            if r:
                                self._current_prefix = r.group(1)
                                joblog = r.group(1) + ".log"
                                if directory_included(joblog, config.params.topdir,
                                                      config.params.include_dir, config.params.exclude_dir):
                                    self._joblogs.append([joblog, None]) # Don't care if really exists or not
                                else:
                                    self._current_prefix = None
                                    self._job_is_running = False
                            read_job_flag = False
                            continue

                    r = re_job_finish.search(l)
                    if r:
                        self._job_is_running = False
                        self._prev_job_finished = True
                        self._current_prefix = None
                        continue

                    if self._current_prefix is not None and self._current_prefix in l:
                        # like   /isilon/hoge/fuga/foo_000001.img [1/180]
                        if "start_series," in l: # 225HS shutterless
                            tmp = l[l.index("start_series,"):].split(",")[2]
                            self._joblogs[-1][1] = int(tmp)
                            self._current_prefix = None
                        elif l[l.index(self._current_prefix)+len(self._current_prefix)+2] in "0123456789":
                            tmp = l[l.index(self._current_prefix)+len(self._current_prefix)+1:].split()[0]
                            self._joblogs[-1][1] = int(os.path.splitext(tmp.replace(self._current_prefix+"_", ""))[0])
                            self._current_prefix = None
                        continue
                    elif self._current_prefix is not None and "ExtTrigger "+os.path.basename(self._current_prefix) in l:
                        tmp = os.path.basename(self._current_prefix)
                        tmp2 = "ExtTrigger " + tmp
                        if l[l.index(tmp2)+len(tmp2)+2] in "0123456789":
                            tmp3 = filter(lambda x: tmp in x, l[l.index(tmp2):].split())[0]
                            self._joblogs[-1][1] = int(os.path.splitext(tmp3.replace(tmp+"_", ""))[0])
                            self._current_prefix = None
                            continue

                except Exception, e:
                    mylog.error("Unhandled error occurred when reading %s" % bsslog)
                    mylog.error(" Line %d-> %s <" % (i, l.rstrip()))
                    mylog.error(traceback.format_exc())
                    raise e

            self.bsslogs_checked[bsslog] = i
            # Check if start number and bss log were found. If not, set line number
    # check_bss_log()

    def update_jobs(self, date, daystart=-2): #, joblogs, prev_job_finished, job_is_running):
        self.check_bss_log(date, daystart)
        mylog.debug("joblogs= %s" % self._joblogs)

        if self._prev_job_finished:
            for job in self.jobs.values():
                job.status = "finished"

        remove_idxes = []

        for i, (joblog, startnum) in enumerate(self._joblogs):
            if not os.path.isfile(joblog):
                mylog.info("Joblog not found. not created yet? pending: %s"%joblog)
                continue

            bjl = BssJobLog(joblog, remove_overwritten=True)
            prefix = os.path.splitext(joblog)[0] # XXX what if .gz etc?
            is_running_job = (self._job_is_running and i == len(self._joblogs)-1)

            looks_h5 = False
            for job in bjl.jobs:
                if job.get_master_h5_if_exists():
                    looks_h5 = True

            if startnum is None:
                if looks_h5:
                    startnum = 1
                else:
                    mylog.error("start number not known: %s"%joblog)
                    continue

            for j, job in enumerate(bjl.jobs):
                if is_running_job and j == len(bjl.jobs)-1:
                    # running job
                    if startnum == 0: startnum = 1
                    nr = (startnum, startnum + job.n_images - 1)
                    job.status = "running"
                else:
                    # not running job
                    nr = job.get_frame_num_range()
                    if nr.count(None) == 2:
                        mylog.debug("Can't get number range of finished job: %s - use expected value."%joblog)
                        # Should we check actual file existence?
                        if startnum == 0: startnum = 1
                        nr = (startnum, startnum + job.n_images - 1)
                    if None in nr:
                        mylog.error("number range not known: %s"%joblog)
                        continue
                    job.status = "finished"

                if prefix in self.jobs_prefix_lookup:
                    mylog.info("Same prefix: %s,%s. What should I do? %s" % (prefix, nr, self.jobs_prefix_lookup[prefix]))
                    tmp_in = set(reduce(lambda x,y: x+y, map(lambda x:range(x[0],x[1]+1), self.jobs_prefix_lookup[prefix])))
                    # XXX Resolve overlap!!
                    if set(range(nr[0],nr[1]+1)).intersection(tmp_in):
                        print "tmp_in=", tmp_in, nr
                        mylog.warning("Range overlapped! Discarding this data..sorry.")
                        remove_idxes.append(i)
                        continue

                if job.job_mode == "Raster scan":
                    remove_idxes.append(i)
                    continue

                master_h5 = job.get_master_h5_if_exists()
                if master_h5 is not None:
                    job.filename = master_h5.replace("_master.h5", "_??????.h5")
                    for nr2 in job.get_frame_num_ranges_for_h5():
                        self.jobs[(prefix, nr2)] = job
                        self.jobs_prefix_lookup.setdefault(prefix, set()).add(nr2)

                else:
                    # FIXME when multiple_crystals mode, what will filenames be?
                    self.jobs[(prefix, nr)] = job
                    self.jobs_prefix_lookup.setdefault(prefix, set()).add(nr)

                remove_idxes.append(i)

        remove_idxes = list(set(remove_idxes))
        for i in sorted(remove_idxes, reverse=True):
            del self._joblogs[i]

        mylog.debug("remaining joblogs= %s" % self._joblogs)

        # Dump jobs
        pickle.dump(self.jobs, open(os.path.join(config.params.workdir, "jobs.pkl"), "wb"), 2)
    # update_jobs()

    def update_jobs_from_files(self, root_dir, include_dir=[], exclude_dir=[]):
        from yamtbx.dataproc import dataset
        from yamtbx.dataproc.bl_logfiles import JobInfo
        from yamtbx.dataproc import XIO

        if include_dir == []: include_dir = [root_dir]
        # XXX what if include_dir has sub directories..

        for rd in include_dir:
            for ds in dataset.find_data_sets(rd, skip_0=True, skip_symlinks=False, split_hdf_miniset=config.params.split_hdf_miniset):
                tmpl, nr = ds[0], tuple(ds[1:3])
                prefix = tmpl[:tmpl.index("_?" if "_?" in tmpl else "?")]
                    
                if not directory_included(tmpl, root_dir, [], exclude_dir):
                    mylog.info("This directory is not in topdir or in exclude_dir. Skipped: %s"%tmpl)
                    continue

                job = JobInfo(None)
                job.filename = tmpl

                images = filter(lambda x: os.path.isfile(x), dataset.template_to_filenames(*ds))

                if len(images) == 0:
                    continue

                h = XIO.Image(images[0]).header
                job.osc_end = h.get("PhiEnd", 0)

                if len(images) > 1:
                    h_next = XIO.Image(images[1]).header
                    h_last = XIO.Image(images[-1]).header
                    job.osc_end = h_last.get("PhiEnd", 0)
                    if h_next.get("PhiStart", 0) == h.get("PhiStart", 0):
                        print "This job may be scan?:",  tmpl
                        continue

                job.wavelength = h.get("Wavelength", 0)
                job.osc_start =  h.get("PhiStart", 0)
                job.osc_step = h.get("PhiWidth", 0)
                job.status = "finished"
                job.exp_time = h.get("ExposureTime", 0)
                job.distance = h.get("Distance", 0)
                job.attenuator = None, 0
                job.detector = "?"
                job.prefix = prefix

                if job.osc_step == 0 or job.osc_end - job.osc_start == 0:
                    print "This job don't look like osc data set:",  tmpl
                    continue
                    
                if config.params.split_data_by_deg is None or job.osc_step==0:
                    self.jobs[(prefix, nr)] = job
                    self.jobs_prefix_lookup.setdefault(prefix, set()).add(nr)
                else:
                    n_per_sp = int(config.params.split_data_by_deg/job.osc_step+.5)
                    for i in xrange(nr[1]//n_per_sp+1):
                        if (i+1)*n_per_sp < nr[0]: continue
                        if nr[1] < i*n_per_sp+1: continue
                        nr2 = (max(i*n_per_sp+1, nr[0]), min((i+1)*n_per_sp, nr[1]))
                        self.jobs[(prefix, nr2)] = job # This will share the same job object.. any problem??
                        self.jobs_prefix_lookup.setdefault(prefix, set()).add(nr2)

        # Dump jobs
        pickle.dump(self.jobs, open(os.path.join(config.params.workdir, "jobs.pkl"), "wb"), 2)
    # update_jobs_from_files()

    def process_data(self, key):
        if key not in self.jobs:
            mylog.error("Unknown job: %s" % key)
            return

        if config.params.engine == "xds":
            self.process_data_xds(key)
        elif config.params.engine == "dials":
            self.process_data_dials(key)
        else:
            raise "Never reaches here"
    # process_data()

    def process_data_xds(self, key):
        job = self.jobs[key]
        prefix, nr = key

        workdir = self.get_xds_workdir(key)
        if not os.path.exists(workdir): os.makedirs(workdir)
        
        # Prepare XDS.INP
        img_files = find_existing_files_in_template(job.filename, nr[0], nr[1],
                                                    datadir=os.path.dirname(prefix), check_compressed=True)
        if len(img_files) == 0:
            mylog.error("No files found for %s %s" % (job.filename, nr))
            return

        overrides = read_override_config(os.path.dirname(job.filename))
        if "rotation_axis" not in overrides and config.params.xds.override.rotation_axis:
           overrides["rotation_axis"] = config.params.xds.override.rotation_axis

        # XXX need to update self.jobs (display on GUI)

        exclude_resolution_ranges = []
        if config.params.xds.exclude_resolution_range:
            exclude_resolution_ranges = config.params.xds.exclude_resolution_range

        if config.params.exclude_ice_resolutions:
            exclude_resolution_ranges.extend([[3.93,3.87], 
                                              [3.70,3.64], 
                                              [3.47,3.41], 
                                              [2.70,2.64], 
                                              [2.28,2.22], 
                                              [2.102,2.042], 
                                              [1.978,1.918], 
                                              [1.948,1.888], 
                                              [1.913,1.853], 
                                              [1.751,1.691], 
                                              ])

        xdsinp_str = xds_inp.generate_xds_inp(img_files=img_files,
                                              inp_dir=os.path.abspath(workdir),
                                              reverse_phi=config.params.reverse_phi,
                                              anomalous=config.params.anomalous,
                                              spot_range="all", minimum=False,
                                              integrate_nimages=None, minpk=config.params.xds.minpk,
                                              exclude_resolution_range=exclude_resolution_ranges,
                                              orgx=overrides.get("orgx",None),
                                              orgy=overrides.get("orgy",None),
                                              distance=overrides.get("distance",None),
                                              wavelength=overrides.get("wavelength",None),
                                              osc_range=overrides.get("osc_range",None),
                                              rotation_axis=overrides.get("rotation_axis",None),
                                              fstart=nr[0], fend=nr[1],
                                              extra_kwds=config.params.xds.ex,
                                              overrides=self.xds_inp_overrides,
                                              fix_geometry_when_overridden=config.params.xds.override.fix_geometry_when_reference_provided)
        open(os.path.join(workdir, "XDS.INP"), "w").write(xdsinp_str)

        opts = ["multiproc=false", "topdir=.", "nproc=%d"%config.params.batch.nproc_each, "tryhard=true",
                "make_report=true", "use_tmpdir_if_available=%s"%config.params.use_tmpdir_if_available,
                "auto_frame_exclude_spot_based=%s"%config.params.auto_frame_exclude_spot_based]
        if config.params.small_wedges: opts.append("no_scaling=true")
        if None not in (config.params.known.space_group, config.params.known.unit_cell):
            opts.append("cell_prior.cell=%s" % ",".join(map(lambda x: "%.3f"%x, config.params.known.unit_cell)))
            opts.append("cell_prior.sgnum=%d" % sgtbx.space_group_info(config.params.known.space_group).group().type().number())

        # Start batch job
        job = batchjob.Job(workdir, "xds_auto.sh", nproc=config.params.batch.nproc_each)
        job_str = """\
cd "%(wd)s" || exit 1
"%(exe)s" - <<+
from yamtbx.dataproc.auto.command_line.run_all_xds_simple import run_from_args
run_from_args([%(args)s])
for i in xrange(%(repeat)d-1):
 run_from_args([%(args)s, "mode=recycle"])
+
""" % dict(exe=sys.executable, args=",".join(map(lambda x: '"%s"'%x, opts)),
           repeat=config.params.xds.repeat,
           wd=os.path.abspath(workdir))
        
        job.write_script(job_str+"\n")
        
        batchjobs.submit(job)
        self.procjobs[key] = job
    # process_data_xds()

    def process_data_dials(self, key):
        bssjob = self.jobs[key]
        prefix, nr = key

        workdir = self.get_xds_workdir(key)
        if not os.path.exists(workdir): os.makedirs(workdir)
        
        # Prepare
        img_files = find_existing_files_in_template(bssjob.filename, nr[0], nr[1],
                                                    datadir=os.path.dirname(prefix), check_compressed=True)
        if len(img_files) == 0:
            mylog.error("No files found for %s %s" % (bssjob.filename, nr))
            return

        overrides = read_override_config(os.path.dirname(bssjob.filename))

        # Start batch job
        job = batchjob.Job(workdir, "dials_auto.sh", nproc=config.params.batch.nproc_each)
        job_str = """\
cd "%(wd)s" || exit 1
"%(exe)s" - <<+
from yamtbx.dataproc.dials.command_line import run_dials_auto
import pickle
run_dials_auto.run_dials_sequence(**pickle.load(open("args.pkl")))
+
""" % dict(exe=sys.executable, #nproc=config.params.batch.nproc_each,
           #filename=bssjob.filename, prefix=prefix, nr=nr,
           wd=os.path.abspath(workdir))
#filename_template="%(filename)s", prefix="%(prefix)s", nr_range=%(nr)s, wdir=".", nproc=%(nproc)d)

        job.write_script(job_str+"\n")
        pickle.dump(dict(filename_template=bssjob.filename,
                         prefix=prefix,
                         nr_range=nr, wdir=".",
                         known=config.params.known,
                         overrides=overrides,
                         nproc=config.params.batch.nproc_each),
                    open(os.path.join(workdir, "args.pkl"), "w"), -1)
        
        batchjobs.submit(job)
        self.procjobs[key] = job
    # process_data_dials()

    def _save_chache(self, key, filename, obj):
        self._chaches[(key,filename)] = (os.path.getmtime(filename), obj)
    # _save_chache()

    def _load_if_chached(self, key, filename):
        if (key, filename) not in self._chaches: return None
        if not os.path.isfile(filename): return None
        last_mtime, obj = self._chaches[(key, filename)]
        if last_mtime == os.path.getmtime(filename):
            return obj
        return None
    # _load_if_chached()

    def get_process_status(self, key):
        prefix, nr = key
        workdir = self.get_xds_workdir(key)

        state = None
        cmpl, sg, resn = None, None, None

        if config.params.engine == "xds":
            correct_lp = os.path.join(workdir, "CORRECT.LP")

            if key not in self.procjobs: 
                if os.path.exists(os.path.join(workdir, "decision.log")):
                    state = batchjob.STATE_FINISHED
            else:
                job = self.procjobs[key]
                batchjobs.update_state(job)
                state = job.state

            if state == batchjob.STATE_FINISHED:
                if os.path.isfile(correct_lp):
                    lp = self._load_if_chached("correctlp", correct_lp)
                    if lp is None:
                        lp = correctlp.CorrectLp(correct_lp)
                        self._save_chache("correctlp", correct_lp, lp)

                    ISa = lp.get_ISa() if lp.is_ISa_valid() else float("nan")

                    resn = lp.resolution_based_on_ios_of_error_table(min_ios=1.)
                    self._save_chache("resn", correct_lp, resn) # for html report

                    sg = lp.space_group_str()
                    cmpl = float(lp.table["all"]["cmpl"][-1]) if "all" in lp.table else float("nan")

                if not os.path.isfile(os.path.join(workdir, "XDS_ASCII.HKL")):
                    state = "giveup"

        elif config.params.engine == "dials":
            summary_pkl = os.path.join(workdir, "kamo_dials.pkl")

            if key not in self.procjobs: 
                if os.path.exists(os.path.join(workdir, "dials_sequence.log")):
                    state = batchjob.STATE_FINISHED
            else:
                job = self.procjobs[key]
                batchjobs.update_state(job)
                state = job.state

            if state == batchjob.STATE_FINISHED:
                if os.path.isfile(summary_pkl):
                    pkl = self._load_if_chached("summary_pkl", summary_pkl)
                    if pkl is None:
                        pkl = pickle.load(open(summary_pkl))
                        self._save_chache("summary_pkl", summary_pkl, pkl)

                    try: resn = float(pkl.get("d_min"))
                    except: resn = float("nan")

                    try: sg = str(pkl["symm"].space_group_info())
                    except: sg = "?"
                    try: 
                        cmpl = pkl["stats"].overall.completeness*100
                    except: cmpl = float("nan")

                if not os.path.isfile(os.path.join(workdir, "DIALS.HKL")):
                    state = "giveup"

        return state, (cmpl, sg, resn)
    # get_process_status()

    def get_process_result(self, key):
        prefix, nr = key
        workdir = self.get_xds_workdir(key)

        ret = {}
        ret["workdir"] = workdir
        ret["exclude_data_ranges"] = ()

        if config.params.engine == "xds":
            xds_inp = os.path.join(workdir, "XDS.INP")
            correct_lp = os.path.join(workdir, "CORRECT.LP")
            gxparm_xds = os.path.join(workdir, "GXPARM.XDS")
            stats_pkl = os.path.join(workdir, "merging_stats.pkl")

            if os.path.isfile(correct_lp):
                lp = correctlp.CorrectLp(correct_lp)
                ret["ISa"] = lp.get_ISa() if lp.is_ISa_valid() else float("nan")
                ret["resn"] = lp.resolution_based_on_ios_of_error_table(min_ios=1.)
                ret["sg"] = lp.space_group_str()
                ret["cmpl"] = float(lp.table["all"]["cmpl"][-1]) if "all" in lp.table else float("nan")
                if lp.unit_cell is not None:
                    ret["cell"] = lp.unit_cell
                elif os.path.isfile(gxparm_xds):
                    xp = xparm.XPARM(gxparm_xds)
                    ret["cell"] = list(xp.unit_cell)
                    ret["sg"] = xp.space_group_str()

            if os.path.isfile(stats_pkl):
                sio = StringIO.StringIO()
                pickle.load(open(stats_pkl))["stats"].show(out=sio, header=False)
                lines = sio.getvalue().replace("<","&lt;").replace(">","&gt;").splitlines()
                i_table_begin = filter(lambda x: "Statistics by resolution bin:" in x[1], enumerate(lines))
                if len(i_table_begin) == 1:
                    ret["table_html"] = "\n".join(lines[i_table_begin[0][0]+1:])

            exc_frames = filter(lambda x: x[0]=="EXCLUDE_DATA_RANGE", get_xdsinp_keyword(xds_inp))
            ret["exclude_data_ranges"] = map(lambda x: map(int, x[1].split()), exc_frames)

        elif config.params.engine == "dials":
            summary_pkl = os.path.join(workdir, "kamo_dials.pkl")
            print summary_pkl
            if os.path.isfile(summary_pkl):
                pkl = pickle.load(open(summary_pkl))
                try: ret["resn"] = float(pkl.get("d_min"))
                except: ret["resn"] = float("nan")
                
                try:
                    ret["sg"] = str(pkl["symm"].space_group_info())
                    ret["cell"] = pkl["symm"].unit_cell().parameters()
                except: ret["sg"] = "?"

                try: 
                    ret["cmpl"] = pkl["stats"].overall.completeness*100
                except: ret["cmpl"] = float("nan")

                if "stats" in pkl:
                    sio = StringIO.StringIO()
                    pkl["stats"].show(out=sio, header=False)
                    lines = sio.getvalue().replace("<","&lt;").replace(">","&gt;").splitlines()
                    i_table_begin = filter(lambda x: "Statistics by resolution bin:" in x[1], enumerate(lines))
                    print                     i_table_begin
                    if len(i_table_begin) == 1:
                        ret["table_html"] = "\n".join(lines[i_table_begin[0][0]+1:])


        print ret
        return ret
    # get_process_result()
        
# class BssJobs

# Singleton objects
bssjobs = BssJobs()
batchjobs = None # initialized in __main__
mainFrame = None

class WatchLogThread:
    def __init__(self, parent):
        self.parent = parent
        self.interval = 10
        self.thread = None

    def start(self, interval=None):
        self.stop()

        self.keep_going = True
        self.running = True
        if interval is not None:
            self.interval = interval

        self.thread = threading.Thread(None, self.run)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        if self.is_running():
            mylog.info("Stopping WatchLogThread.. Wait.")
            self.keep_going = False
            self.thread.join()
        else:
            mylog.info("WatchLogThread already stopped.")

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def run(self):
        mylog.info("WatchLogThread loop STARTED")
        counter = 0
        while self.keep_going:
            counter += 1
            if config.params.date == "today": date = datetime.datetime.today()
            else: date = datetime.datetime.strptime(config.params.date, "%Y-%m-%d")

            if not (config.params.logwatch_once and counter > 1):
                # check bsslog
                if config.params.jobspkl is not None:
                    bssjobs.jobs = pickle.load(open(config.params.jobspkl))
                    for prefix, nr in bssjobs.jobs:
                        bssjobs.jobs_prefix_lookup.setdefault(prefix, set()).add(nr)
                else:
                    if config.params.blconfig:
                        #joblogs, prev_job_finished, job_is_running = bssjobs.check_bss_log(date, -config.params.checklog_daybefore)
                        bssjobs.update_jobs(date, -config.params.checklog_daybefore) #joblogs, prev_job_finished, job_is_running)
                    else:
                        bssjobs.update_jobs_from_files(config.params.topdir,
                                                       config.params.include_dir, config.params.exclude_dir)
                # start jobs
                if config.params.auto_mode:
                    for key in bssjobs.keys():
                        status = bssjobs.get_process_status(key)[0]
                        job = bssjobs.get_job(key)
                        if job.status == "finished" and status is None:
                            if job.all_image_files_exist():
                                mylog.info("Automatically starting processing %s" % str(key))
                                bssjobs.process_data(key)
                            else:
                                mylog.info("Waiting for files: %s" % str(key))

            ev = EventLogsUpdated()
            wx.PostEvent(self.parent, ev)

            # Make html report # TODO Add DIALS support
            html_report.make_kamo_report(bssjobs, 
                                         topdir=config.params.topdir,
                                         htmlout=os.path.join(config.params.workdir, "report.html"))
            #print
            #print "Done. Open?"
            #print "firefox %s" % os.path.join(config.params.workdir, "report.html")

            if self.interval == 0: # Run only once
                self.keep_going = False
                continue

            if self.interval < 1:
                time.sleep(self.interval)
            else:
                for i in xrange(int(self.interval/.5)):
                    if self.keep_going:
                        time.sleep(.5)

        mylog.info("WatchLogThread loop FINISHED")
        self.running = False
        #wx.PostEvent(self.parent, EventDirWatcherStopped()) # Ensure the checkbox unchecked when accidentally exited.
    # run()
# class WatchLogThread

class MyCheckListCtrl(wx.ListCtrl, CheckListCtrlMixin, ListCtrlAutoWidthMixin):
    """
    http://zetcode.com/wxpython/advanced/
    """
    def __init__(self, parent):
        wx.ListCtrl.__init__(self, parent, wx.ID_ANY, style=wx.LC_REPORT|wx.LC_SINGLE_SEL|wx.LC_VIRTUAL)
        CheckListCtrlMixin.__init__(self)
        ListCtrlAutoWidthMixin.__init__(self)
        
        self.SetFont(wx.Font(12, wx.SWISS, wx.NORMAL, wx.NORMAL))
        self.InsertColumn(0, "Path", wx.LIST_FORMAT_LEFT, width=400) # with checkbox
        self.InsertColumn(1, "Sample ID", wx.LIST_FORMAT_LEFT, width=90)
        self.InsertColumn(2, "Wavelen", wx.LIST_FORMAT_LEFT, width=80)
        self.InsertColumn(3, "TotalPhi", wx.LIST_FORMAT_LEFT, width=80)
        self.InsertColumn(4, "DeltaPhi", wx.LIST_FORMAT_LEFT, width=80)
        self.InsertColumn(5, "Cstatus", wx.LIST_FORMAT_LEFT, width=70)
        self.InsertColumn(6, "Pstatus", wx.LIST_FORMAT_LEFT, width=70)
        self.InsertColumn(7, "Cmpl.", wx.LIST_FORMAT_LEFT, width=50)
        self.InsertColumn(8, "SG", wx.LIST_FORMAT_LEFT, width=100)
        self.InsertColumn(9, "Resn.", wx.LIST_FORMAT_LEFT, width=50)

        self.items = []
        self.images = []
        self._items_lookup = {} # {key: idx in self.items}
        self._sort_acend = True
        self._sort_prevcol = None

        self.Bind(wx.EVT_LIST_COL_CLICK, self.item_col_click)
    # __init__()

    def key_at(self, line): return self.items[line][0]
    def OnGetItemText(self, line, col): return self.items[line][col+2] # [0] has key, [1] has checked state
    def OnGetItemImage(self, line): return self.items[line][1]

    def SetItemImage(self, line, im): # checked state
        self.items[line][1] = im
        self.Refresh()
    # SetItemImage()

    def get_item(self, key):
        if key not in self._items_lookup: return None
        return self.items[self._items_lookup[key]][2:]
    # get_item()

    def update_item(self, key, item):
        if key not in self._items_lookup:
            self.items.append([key, 0]+item)
            self._items_lookup[key] = len(self.items)-1
        else:
            for i in xrange(len(item)):
                self.items[self._items_lookup[key]][i+2] = item[i]
    # update_item()

    def item_col_click(self, ev):
        col = ev.GetColumn()
        if col != self._sort_prevcol:
            self._sort_acend = True
        else:
            self._sort_acend = not self._sort_acend
        
        perm = range(len(self.items))
        def trans_func(idx):
            # 0:lab, 1:sample, 2:wavelen, 3:phirange, 4:deltaphi, 5,6:status, 7:cmpl, 8:sg, 9:resn
            if idx in (2, 3, 4, 7, 9): return safe_float
            return lambda x: x
        # trans_func()
        perm.sort(key=lambda x: trans_func(col)(self.items[x][col+2]),
                  reverse=not self._sort_acend)

        perm_table = dict(map(lambda x:(perm[x], x), xrange(len(perm)))) # old idx -> new idx
        for k in self._items_lookup: self._items_lookup[k] = perm_table[self._items_lookup[k]]
        self.items = map(lambda x: self.items[x], perm)

        self._sort_prevcol = col
        #self.DeleteAllItems()
        self.SetItemCount(len(self.items))
    # listctrl_item_col_click()

# class MyCheckListCtrl

class MultiPrepDialog(wx.Dialog):
    def __init__(self, parent=None, cm=None):
        wx.Dialog.__init__(self, parent=parent, id=wx.ID_ANY, title="Prep multi merge",
                          size=(1200,600), style=wx.DEFAULT_DIALOG_STYLE|wx.RESIZE_BORDER|wx.MAXIMIZE_BOX)
        mpanel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        mpanel.SetSizer(vbox)
        self.txtCM = wx.TextCtrl(mpanel, wx.ID_ANY, size=(450,25), style=wx.TE_MULTILINE)
        self.txtCM.SetFont(wx.Font(10, wx.FONTFAMILY_MODERN, wx.NORMAL, wx.NORMAL))
        self.txtCM.SetEditable(False)
        vbox.Add(self.txtCM, 1, flag=wx.EXPAND|wx.RIGHT)

        hbox1 = wx.BoxSizer(wx.HORIZONTAL)
        hbox1.Add(wx.StaticText(mpanel, wx.ID_ANY, "Choose group: "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.cmbGroup = wx.ComboBox(mpanel, wx.ID_ANY, size=(100,25), style=wx.CB_READONLY)
        hbox1.Add(self.cmbGroup)
        self.cmbGroup.Bind(wx.EVT_COMBOBOX, self.cmbGroup_select)

        hbox1.Add(wx.StaticText(mpanel, wx.ID_ANY, "Choose symmetry: "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.cmbSymmetry = wx.ComboBox(mpanel, wx.ID_ANY, size=(400,25), style=wx.CB_READONLY)
        hbox1.Add(self.cmbSymmetry)

        hbox1.Add(wx.StaticText(mpanel, wx.ID_ANY, "Workdir: "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.txtWorkdir = wx.TextCtrl(mpanel, wx.ID_ANY, size=(200,25))
        hbox1.Add(self.txtWorkdir)

        self.btnProceed = wx.Button(mpanel, wx.ID_ANY, "Proceed")
        self.btnCancel = wx.Button(mpanel, wx.ID_ANY, "Cancel")
        self.btnProceed.Bind(wx.EVT_BUTTON, self.btnProceed_click)
        self.btnCancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.OK))
        hbox1.Add(self.btnProceed)
        hbox1.Add(self.btnCancel)
        vbox.Add(hbox1)

        hbox2 = wx.BoxSizer(wx.HORIZONTAL)
        hbox2.Add(wx.StaticText(mpanel, wx.ID_ANY, "Determine cell-dimensions in specified symmetry by "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)

        self.rbReindex = wx.RadioButton(mpanel, wx.ID_ANY, "reindexing only", style=wx.RB_GROUP)
        self.rbReindex.SetValue(True)
        self.rbPostref = wx.RadioButton(mpanel, wx.ID_ANY, "post-refinement")
        hbox2.Add(self.rbReindex, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        hbox2.Add(self.rbPostref, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        hbox2.Add(wx.StaticText(mpanel, wx.ID_ANY, " using "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.txtNproc = wx.TextCtrl(mpanel, wx.ID_ANY, size=(40,25))
        hbox2.Add(self.txtNproc, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        hbox2.Add(wx.StaticText(mpanel, wx.ID_ANY, " CPU cores "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.chkPrepDials = wx.CheckBox(mpanel, wx.ID_ANY, "Prepare files for joint refinement by dials")
        hbox2.Add(self.chkPrepDials, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        vbox.Add(hbox2)

        try: import dials
        except ImportError: self.chkPrepDials.Disable()

        self.selected = (None, None, None, None, None, None)
        self.cm = cm
        self._set_default_input()
    # __init__()

    def _set_default_input(self):
        self.cmbGroup.Clear()
        if self.cm is None: return

        for i in xrange(len(self.cm.groups)): self.cmbGroup.Append("%2d"%(i+1))
        self.cmbGroup.Select(0)
        self.cmbGroup_select(None)
        self.txtWorkdir.SetValue(time.strftime("merge_%y%m%d-%H%M%S"))
    # _set_default_input()

    def cmbGroup_select(self, ev):
        self.cmbSymmetry.Clear()

        igrp = int(self.cmbGroup.GetValue()) - 1
        symms = self.cm.get_selectable_symms(igrp)
        def_idx = symms.index(max(symms, key=lambda x:x[2]))

        if len(symms) > 1 and symms[def_idx][0].group() == sgtbx.space_group_info("P1").group():
            tmp = symms.index(max(filter(lambda x: x!=symms[def_idx], symms), key=lambda x:x[2]))
            if symms[tmp][2] > 0: def_idx = tmp

        for i, (pg, cell, freq) in enumerate(symms):
            self.cmbSymmetry.Append("%-10s (%s)" % (pg, format_unit_cell(cell)))

        self.cmbSymmetry.Select(def_idx)
    # cmbGroup_select()

    def btnProceed_click(self, ev):
        prohibit_chars = set(" /*\\")

        workdir = self.txtWorkdir.GetValue()
        if prohibit_chars.intersection(workdir):
            wx.MessageDialog(None, "You can't use following characters for directory name: ' /*\\'",
                             "Error", style=wx.OK).ShowModal()
            return

        group, symmidx = int(self.cmbGroup.GetValue()), self.cmbSymmetry.GetCurrentSelection()

        # Check workdir
        workdir = os.path.join(config.params.workdir, workdir)

        try:
            os.mkdir(workdir)
        except OSError:
            wx.MessageDialog(None, "Can't make directory: %s" % os.path.basename(workdir),
                             "Error", style=wx.OK).ShowModal()
            return

        try:
            nproc = int(self.txtNproc.GetValue())
        except ValueError:
            wx.MessageDialog(None, "Invalid core number",
                             "Error", style=wx.OK).ShowModal()
            return

        self.selected = group, symmidx, workdir, "reindex" if self.rbReindex.GetValue() else "postrefine", nproc, self.chkPrepDials.GetValue()
        self.EndModal(wx.OK)
    # btnProceed_click()

    def ask(self, txt):
        self.txtCM.SetValue(txt)
        self.txtNproc.SetValue("%s"%libtbx.easy_mp.get_processes(libtbx.Auto))
        self.ShowModal()
        return self.selected
    # ask()
# class MultiPrepDialog

class MultiMergeDialog(wx.Dialog):
    def __init__(self, parent=None, xds_ascii_files=[]):
        wx.Dialog.__init__(self, parent=parent, id=wx.ID_ANY, title="Multi merge",
                          size=(600,600), style=wx.DEFAULT_DIALOG_STYLE|wx.RESIZE_BORDER)
        mpanel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        mpanel.SetSizer(vbox)

        

    # __init__()


# class MultiMergeDialog

class ControlPanel(wx.Panel):
    def __init__(self, parent=None, id=wx.ID_ANY):
        wx.Panel.__init__(self, parent=parent, id=id)

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(vbox)

        hbox1 = wx.BoxSizer(wx.HORIZONTAL)
        hbox1.Add(wx.StaticText(self, wx.ID_ANY, "Top Dir: "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.txtTopDir = wx.TextCtrl(self, wx.ID_ANY, size=(450,25))
        self.txtTopDir.SetEditable(False)
        self.txtTopDir.SetValue(config.params.topdir)
        hbox1.Add(self.txtTopDir, flag=wx.EXPAND|wx.RIGHT)
        vbox.Add(hbox1)

        self.lblDS = wx.StaticText(self, wx.ID_ANY, "?? datasets collected, ?? datasets processed")
        vbox.Add(self.lblDS, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)

        hbox2 = wx.BoxSizer(wx.HORIZONTAL)
        hbox2.Add(wx.StaticText(self, wx.ID_ANY, "Filter: "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        self.txtFilter = wx.TextCtrl(self, wx.ID_ANY, size=(200,25))
        self.cmbFilter = wx.ComboBox(self, wx.ID_ANY, size=(150,25), style=wx.CB_READONLY)
        self.cmbFilter.Bind(wx.EVT_TEXT_ENTER, self.cmbFilter_text_enter)
        for n in ("Path",): self.cmbFilter.Append(n)
        self.btnCheckAll = wx.Button(self, wx.ID_ANY, "Check all")
        self.btnUncheckAll = wx.Button(self, wx.ID_ANY, "Uncheck all")
        self.btnMultiMerge = wx.Button(self, wx.ID_ANY, "Multi-merge strategy")
        self.btnCheckAll.Bind(wx.EVT_BUTTON, self.btnCheckAll_click)
        self.btnUncheckAll.Bind(wx.EVT_BUTTON, self.btnUncheckAll_click)
        self.btnMultiMerge.Bind(wx.EVT_BUTTON, self.btnMultiMerge_click)
        hbox2.Add(self.txtFilter, flag=wx.EXPAND|wx.RIGHT)
        hbox2.Add(self.cmbFilter, flag=wx.EXPAND|wx.RIGHT)
        hbox2.Add(self.btnCheckAll, flag=wx.EXPAND|wx.RIGHT)
        hbox2.Add(self.btnUncheckAll, flag=wx.EXPAND|wx.RIGHT)
        hbox2.Add(self.btnMultiMerge, flag=wx.EXPAND|wx.RIGHT)
        vbox.Add(hbox2)

        self.listctrl = MyCheckListCtrl(self)
        self.listctrl.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.listctrl_item_right_click)
        self.listctrl.Bind(wx.EVT_LIST_ITEM_SELECTED, self.listctrl_item_selected)
        vbox.Add(self.listctrl, 1, flag=wx.EXPAND|wx.TOP)

    # __init__()

    def on_update(self, ev):
        self.update_listctrl()

        lc = self.listctrl
        n_proc = 0
        n_giveup = 0

        dumpdata = {}
        for i in xrange(lc.GetItemCount()):
            key = self.listctrl.key_at(i)
            status, (cmpl, sg, resn) = bssjobs.get_process_status(key)
            dumpdata[key] = status, (cmpl, sg, resn)
            item = lc.get_item(key)
            if status is None:
                item[6] = "never"
            else:
                item[6] = status
                if status == batchjob.STATE_FINISHED: n_proc += 1
                if status == "giveup": n_giveup += 1

            if cmpl is not None: item[7] = "%3.0f" % cmpl
            if sg is not None: item[8] = str(sg)
            if resn is not None: item[9] = "%.1f" % resn
            lc.update_item(key, item)
        
        lc.SetItemCount(len(lc.items))
        self.lblDS.SetLabel("%3d datasets collected (%3d processed, %3d failed, %3d undone) workdir: %s" % (lc.GetItemCount(), n_proc, n_giveup, lc.GetItemCount()-(n_proc+n_giveup), os.path.relpath(config.params.workdir, config.params.topdir)))

        pickle.dump(dumpdata, open(os.path.join(config.params.workdir, "proc.pkl"), "wb"), 2)
    # on_update()

    def update_listctrl(self):
        lc = self.listctrl

        for prefix, nr in bssjobs.jobs:
            lab = "%s (%.4d..%.4d)" % (os.path.relpath(prefix, config.params.topdir), nr[0], nr[1])
            job = bssjobs.jobs[(prefix, nr)]
            
            item = [lab]
            if job.sample is not None: item.append("%s(%.2d)" % job.sample)
            else: item.append("none")

            item.append("%.4f" % job.wavelength)
            item.append("%5.1f" % (job.osc_end - job.osc_start))
            item.append("%.3f" % job.osc_step)
            item.append(job.status)
            item.append("never")
            item.append("")
            item.append("")
            item.append("")

            lc.update_item((prefix, nr), item)
            assert len(item) == lc.GetColumnCount()
        
        lc.SetItemCount(len(lc.items))
    # update_listctrl()
    
    def listctrl_item_right_click(self, ev):
        lc = self.listctrl
        idx = lc.GetFirstSelected()
        key = self.listctrl.key_at(idx)

        menu = wx.Menu()
        menu.Append(0, lc.GetItem(idx, 0).GetText())
        menu.Enable(0, False)
        menu.AppendSeparator()
        menu.Append(1, "Start processing")
        menu.Append(2, "Stop processing")

        self.Bind(wx.EVT_MENU, lambda e: bssjobs.process_data(key), id=1)

        self.PopupMenu(menu)
        menu.Destroy()
    # listctrl_item_right_click()

    def listctrl_item_selected(self, ev):
        idx = self.listctrl.GetFirstSelected()
        key = self.listctrl.key_at(idx)
        ev = EventShowProcResult(key=key)
        wx.PostEvent(mainFrame, ev)
    # listctrl_item_selected()

    def btnCheckAll_click(self, ev):
        for i in xrange(self.listctrl.GetItemCount()):
            if self.listctrl.GetItem(i).GetImage() == 0: self.listctrl.SetItemImage(i, 1)
    # btnCheckAll_click()

    def btnUncheckAll_click(self, ev):
        for i in xrange(self.listctrl.GetItemCount()):
            if self.listctrl.GetItem(i).GetImage() == 1: self.listctrl.SetItemImage(i, 0)
    # btnUncheckAll_click()

    def btnMultiMerge_click(self, ev):
        keys = map(lambda i: self.listctrl.key_at(i),
                   filter(lambda i: self.listctrl.GetItem(i).GetImage() == 1, xrange(self.listctrl.GetItemCount())))
        keys = filter(lambda k: bssjobs.get_process_status(k)[0]=="finished", keys)
        mylog.info("%d finished jobs selected for merging" % len(keys))

        if len(keys) == 0:
            wx.MessageDialog(None, "No successfully finished job in the selection",
                             "Error", style=wx.OK).ShowModal()
            return

        xdsdirs = map(lambda k: bssjobs.get_xds_workdir(k), keys)

        # Check cell consistency
        from yamtbx.dataproc.auto.command_line import multi_check_cell_consistency

        cell_params = libtbx.phil.parse(input_string=multi_check_cell_consistency.master_params_str).extract()

        if config.params.merging.cell_grouping.tol_length is not None:
            cell_params.tol_length = config.params.merging.cell_grouping.tol_length
        if config.params.merging.cell_grouping.tol_angle is not None:
            cell_params.tol_angle = config.params.merging.cell_grouping.tol_angle 

        cell_params.xdsdir = xdsdirs
        sio = StringIO.StringIO()
        
        busyinfo = wx.lib.agw.pybusyinfo.PyBusyInfo("Thinking..", title="Busy KAMO")
        try: wx.SafeYield()
        except: pass
        try:
            cm = multi_check_cell_consistency.run(cell_params, out=sio)
        finally:
            busyinfo = None

        if len(cm.groups) == 0:
            wx.MessageDialog(None, "Oh, no. No data",
                             "Error", style=wx.OK).ShowModal()
            return
        
        mpd = MultiPrepDialog(cm=cm)
        group, symmidx, workdir, cell_method, nproc, prep_dials_files = mpd.ask(sio.getvalue())
        mpd.Destroy()

        if None in (group,symmidx):
            mylog.info("Canceled")
            return

        prep_log_out = multi_out()
        prep_log_out.register("log", open(os.path.join(workdir, "prep_merge.log"), "w"), atexit_send_to=None)
        prep_log_out.register("stdout", sys.stdout)
        prep_log_out.write(sio.getvalue())
        prep_log_out.flush()

        reference_symm = cm.get_reference_symm(group-1, symmidx)

        prep_log_out.write("\n\ngroup_choice= %d, symmetry= %s (%s)\n" % (group, reference_symm.space_group_info(),
                                                                          format_unit_cell(reference_symm.unit_cell())))
        
        # Scale with specified symmetry
        from yamtbx.dataproc.auto.command_line.multi_prep_merging import rescale_with_specified_symm, reindex_with_specified_symm

        symms = map(lambda i: cm.symms[i], cm.groups[group-1])
        dirs = map(lambda i: cm.dirs[i], cm.groups[group-1])
        topdir = os.path.dirname(os.path.commonprefix(dirs))
        mylog.info("group choice: %d, symmetry choice: %s (%s)" % (group, reference_symm.space_group_info(),
                                                                   format_unit_cell(reference_symm.unit_cell())))

        if cell_method == "reindex":
            cell_and_files = reindex_with_specified_symm(topdir, reference_symm, dirs, out=prep_log_out, nproc=nproc, prep_dials_files=prep_dials_files)
        elif cell_method == "postrefine":
            cell_and_files, reference_symm = rescale_with_specified_symm(topdir, dirs, symms, reference_symm=reference_symm, out=prep_log_out, nproc=nproc, prep_dials_files=prep_dials_files)
        else:
            raise "Don't know this choice: %s" % cell_method

        prep_log_out.flush()

        cosets = reindex.reindexing_operators(reference_symm, reference_symm, max_delta=10)
        reidx_ops = cosets.combined_cb_ops()

        print >>prep_log_out, "\nReference symmetry:", reference_symm.unit_cell(), reference_symm.space_group_info().symbol_and_number()
        msg_reindex = "\n"
        if len(reidx_ops) > 1:
            msg_reindex += "!! ATTENTION !! Reindex operators found. You may need to reindex some files before merging.\n"
            for rop in reidx_ops:
                msg_reindex += " operator: %-16s Cell: (%s)\n" % (rop.as_hkl(),
                                                                format_unit_cell(reference_symm.unit_cell().change_basis(rop)))
            msg_reindex += "Try kamo.resolve_indexing_ambiguity command before merging!!"

        else:
            msg_reindex += "No reindex operators found."

        print >>prep_log_out, "%s\n\n" % msg_reindex
        prep_log_out.close()

        # Make list for merging
        ofs_lst = open(os.path.join(workdir, "formerge.lst"), "w")
        ofs_dat = open(os.path.join(workdir, "cells.dat"), "w")
        ofs_dat.write("file a b c al be ga\n")

        for wd in cell_and_files:
            cell, xas = cell_and_files[wd]
            ofs_lst.write(xas+"\n")
            ofs_dat.write(xas+" "+" ".join(map(lambda x:"%7.3f"%x, cell))+"\n")

        ofs_dat.close()
        ofs_lst.close()

        open(os.path.join(workdir, "merge_blend.sh"), "w").write("""\
#!/bin/sh
# settings
dmin=2.8 # resolution
anomalous=false # true or false
lstin=formerge.lst # list of XDS_ASCII.HKL files
use_ramdisk=true # set false if there is few memory or few space in /tmp
# _______/setting

kamo.multi_merge \\
        workdir=blend_${dmin}A_framecc_b \\
        lstin=${lstin} d_min=${dmin} anomalous=${anomalous} \\
        space_group=None reference.data=None \\
        program=xscale xscale.reference=bmin \\
        reject_method=framecc+lpstats rejection.lpstats.stats=em.b \\
        clustering=blend blend.min_cmpl=90 blend.min_redun=2 blend.max_LCV=None blend.max_aLCV=None \\
        xscale.use_tmpdir_if_available=${use_ramdisk} \\
#        batch.engine=sge batch.par_run=merging batch.nproc_each=8 nproc=8 batch.sge_pe_name=%s
""" % config.params.batch.sge_pe_name)
        os.chmod(os.path.join(workdir, "merge_blend.sh"), 0755)
        open(os.path.join(workdir, "merge_ccc.sh"), "w").write("""\
#!/bin/sh
# settings
dmin=2.8 # resolution
clustering_dmin=3.5  # resolution for CC calculation
anomalous=false # true or false
lstin=formerge.lst # list of XDS_ASCII.HKL files
use_ramdisk=true # set false if there is few memory or few space in /tmp
# _______/setting

kamo.multi_merge \\
        workdir=ccc_${dmin}A_framecc_b \\
        lstin=${lstin} d_min=${dmin} anomalous=${anomalous} \\
        space_group=None reference.data=None \\
        program=xscale xscale.reference=bmin \\
        reject_method=framecc+lpstats rejection.lpstats.stats=em.b \\
        clustering=cc cc_clustering.d_min=${clustering_dmin} cc_clustering.b_scale=false cc_clustering.use_normalized=false \\
        cc_clustering.min_cmpl=90 cc_clustering.min_redun=2 \\
        xscale.use_tmpdir_if_available=${use_ramdisk} \\
#        batch.engine=sge batch.par_run=merging batch.nproc_each=8 nproc=8 batch.sge_pe_name=%s
""" % config.params.batch.sge_pe_name)
        os.chmod(os.path.join(workdir, "merge_ccc.sh"), 0755)

        open(os.path.join(workdir, "filter_cell.R"), "w").write("""\
cells <- read.table("cells.dat", h=T)
good <- subset(cells, abs(a-median(a))<IQR(a)*2.5 & abs(b-median(b))<IQR(b)*2.5 & abs(c-median(c))<IQR(c)*2.5)
write.table(good$file, "formerge_goodcell.lst", quote=F, row.names=F, col.names=F)
""")

        if prep_dials_files:
            wd_jref = os.path.join(workdir, "dials_joint_refine")
            os.mkdir(wd_jref)

            ofs_phil = open(os.path.join(wd_jref, "experiments_and_reflections.phil"), "w")
            ofs_phil.write("input {\n")
            for wd in cell_and_files:
                fe = os.path.join(wd, "experiments.json")
                fp = os.path.join(wd, "integrate_hkl.pickle")
                if os.path.isfile(fe) and os.path.isfile(fp):
                    ofs_phil.write(' experiments = "%s"\n' % fe)
                    ofs_phil.write(' reflections = "%s"\n' % fp)
            ofs_phil.write("}\n")
            ofs_phil.close()

            open(os.path.join(wd_jref, "joint_refine.sh"), "w").write("""\
#!/bin/sh

dials.combine_experiments experiments_and_reflections.phil reference_from_experiment.beam=0 reference_from_experiment.goniometer=0 average_detector=true compare_models=false
dials.refine combined_experiments.json combined_reflections.pickle auto_reduction.action=remove verbosity=9
""")

        print "\nFrom here, Do It Yourself!!\n"
        print "cd", workdir
        print "..then edit and run merge_blend.sh and/or merge_ccc.sh"
        print

        wx.MessageDialog(None, "Now ready. From here, please use command-line. Look at your terminal..\n" + msg_reindex,
                         "Ready for merging", style=wx.OK).ShowModal()


        # Merge
        #mmd = MultiMergeDialog(workdir, xds_ascii_files)
        #mmd.ShowModal()
    # btnMultiMerge_click()

    def cmbFilter_text_enter(self, ev):
        # XXX Doesn't work!

        s = self.cmbFilter.GetValue()
        if s == "": return

    # cmbFilter_text_enter()

# class ControlPanel()

class ResultLeftPanel(wx.Panel):
    def __init__(self, parent=None, id=wx.ID_ANY):
        wx.Panel.__init__(self, parent=parent, id=id)
        self.current_key = None
        self.current_workdir = None

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(vbox)

        self.summaryHtml = wx.html.HtmlWindow(self, style=wx.NO_BORDER, size=(600,90))
        self.summaryHtml.SetStandardFonts()
        vbox.Add(self.summaryHtml, 1, flag=wx.EXPAND)

        sbImage = wx.StaticBox(self, label="Check images")
        sbsImage = wx.StaticBoxSizer(sbImage, wx.VERTICAL)

        hbox1 = wx.BoxSizer(wx.HORIZONTAL)
        hbox1.Add(wx.StaticText(self, label="Raw data: frame "), flag=wx.RIGHT|wx.ALIGN_CENTER_VERTICAL)
        self.txtRawFrame = wx.TextCtrl(self, value="1")
        self.spinRawFrame = wx.SpinButton(self, style=wx.SP_VERTICAL)
        self.btnRawShow = wx.Button(self, wx.ID_ANY, "Show")
        hbox1.Add(self.txtRawFrame)
        hbox1.Add(self.spinRawFrame)
        hbox1.Add(self.btnRawShow)
        sbsImage.Add(hbox1)

        hbox2 = wx.BoxSizer(wx.HORIZONTAL)
        hbox2.Add(wx.StaticText(self, label="Prediction: frame "), flag=wx.RIGHT|wx.ALIGN_CENTER_VERTICAL)
        self.txtPredictFrame = wx.TextCtrl(self, value="1")
        self.spinPredictFrame = wx.SpinButton(self, style=wx.SP_VERTICAL)
        self.btnPredictShow = wx.Button(self, wx.ID_ANY, "Show")
        hbox2.Add(self.txtPredictFrame)
        hbox2.Add(self.spinPredictFrame)
        hbox2.Add(self.btnPredictShow)
        sbsImage.Add(hbox2)

        vbox.Add(sbsImage, flag=wx.EXPAND|wx.ALL, border=1)

        self.spinRawFrame.Bind(wx.EVT_SPIN, self.spinRawFrame_spin)
        self.spinPredictFrame.Bind(wx.EVT_SPIN, self.spinPredictFrame_spin)

        self.btnRawShow.Bind(wx.EVT_BUTTON, self.btnRawShow_click)
        self.btnPredictShow.Bind(wx.EVT_BUTTON, self.btnPredictShow_click)

    # __init__()

    def set_current_key(self, key):
        self.current_key = key
        for obj in (self.spinRawFrame, self.spinPredictFrame):
            obj.SetRange(*key[1])
            obj.SetValue(key[1][1])

        self.txtRawFrame.SetValue(str(self.spinRawFrame.GetValue()))
        self.txtPredictFrame.SetValue(str(self.spinPredictFrame.GetValue()))
    # set_current_key()

    def set_current_workdir(self, wd): self.current_workdir = wd

    def btnRawShow_click(self, ev, raise_window=True):
        frame = int(self.txtRawFrame.GetValue())
        job = bssjobs.get_job(self.current_key)
        if job is None: return

        masterh5 = job.get_master_h5_if_exists()
        if masterh5:
            mainFrame.adxv.open_hdf5(masterh5, frame, raise_window=raise_window)
        else:
            from yamtbx.dataproc.dataset import template_to_filenames
            path = template_to_filenames(job.filename, frame, frame)[0]
            mainFrame.adxv.open_image(path, raise_window=raise_window)
    # btnPredictShow_click()

    def spinRawFrame_spin(self, ev):
        self.txtRawFrame.SetValue(str(self.spinRawFrame.GetValue()))
        if mainFrame.adxv.is_alive(): 
            wx.CallAfter(self.btnRawShow_click, None, False)
    # spinRawFrame_spin()

    def btnPredictShow_click(self, ev, raise_window=True):
        frame = int(self.txtPredictFrame.GetValue())
        prefix, nr = self.current_key
        
        if self.current_workdir is None: return
        if frame == self.spinPredictFrame.GetMax():
            framecbf = os.path.join(self.current_workdir,"FRAME.cbf")
        else:
            framecbf = os.path.join(self.current_workdir, "FRAME_%.4d.cbf" % frame)
            if not os.path.isfile(framecbf): # TODO check timestamp and recalculate if needed
                from yamtbx.dataproc.xds.command_line import xds_predict_mitai
                busyinfo = wx.lib.agw.pybusyinfo.PyBusyInfo("Calculating prediction..", title="Busy KAMO")
                try: wx.SafeYield()
                except: pass
                try:
                    xds_predict_mitai.run(param_source=os.path.join(self.current_workdir, "INTEGRATE.LP"),
                                          frame_num=frame, wdir=self.current_workdir)
                finally:
                    busyinfo = None

        mainFrame.adxv.open_image(framecbf, raise_window=raise_window)
    # btnPredictShow_click()

    def spinPredictFrame_spin(self, ev):
        self.txtPredictFrame.SetValue(str(self.spinPredictFrame.GetValue()))
        if mainFrame.adxv.is_alive(): 
            wx.CallAfter(self.btnPredictShow_click, None, False)
    # spinPredictFrame_spin()

    def update_summary(self, job, result):
        prefix = os.path.relpath(job.filename, config.params.topdir)
        startframe, endframe = self.current_key[1]
        osc, exptime, clen = job.osc_step, job.exp_time, job.distance
        att = "%s %d um" % job.attenuator
        # Move this somewhere!
        len_edge = {"CCD (MX225HS)":225./2., }.get(job.detector, 0)
        if len_edge > 0: edgeresn = job.wavelength / 2. / numpy.sin(numpy.arctan(len_edge/job.distance)/2.)
        else:            edgeresn = float("nan")

        html_str = """\
<b>Quick Summary</b><br>
<table>
<tr align="left"><th>Files</th><td>%(prefix)s (%(startframe)4d .. %(endframe)4d)</td></tr>
<tr align="left"><th>Conditions</th><td>DelPhi= %(osc).3f&deg;, Exp= %(exptime).3f s, Distance= %(clen).1f mm (%(edgeresn).1f A), Att= %(att)s</td></tr>
<tr align="left"><th>Excluded frames</th><td>%(exc_ranges)s</td></tr>
"""
        
        exc_ranges_strs = []
        for lr, rr in result["exclude_data_ranges"]:
            if lr==rr: exc_ranges_strs.append("%d"%lr)
            else: exc_ranges_strs.append("%d-%d"%(lr,rr))
        
        exc_ranges = ", ".join(exc_ranges_strs)
        if not exc_ranges_strs: exc_ranges = "(none)"

        ISa = "%.2f"%result["ISa"] if "ISa" in result else "n/a"
        cell_str = ", ".join(map(lambda x: "%.2f"%x,result["cell"])) if "cell" in result else "?"
        sg = result.get("sg", "?")
        table = result.get("table_html", "empty")

        html_str += """\
<tr align="left"><th>ISa</th><td>%(ISa)s</td></tr>
<tr align="left"><th>Symmetry</th><td>%(sg)s :  %(cell_str)s</td></tr>
</table>
<pre>%(table)s</pre>
"""
        self.summaryHtml.SetPage(html_str % locals())
    # update_summary()

# class ResultLeftPanel

class PlotPanel(wx.lib.scrolledpanel.ScrolledPanel): # Why this needs to be ScrolledPanel?? (On Mac, Panel is OK, but not works on Linux..)
    def __init__(self, parent=None, id=wx.ID_ANY, nplots=4):
        wx.lib.scrolledpanel.ScrolledPanel.__init__(self, parent=parent, id=id, size=(400,1200))

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(vbox)
        self.figure = matplotlib.figure.Figure(tight_layout=True)
        self.subplots = map(lambda i: self.figure.add_subplot(nplots,1,i+1), xrange(nplots))
        self.p = map(lambda x:[], xrange(nplots))
        self.lim = map(lambda i: dict(x=[], y=[]), xrange(nplots))
        
        self.canvas = matplotlib.backends.backend_wxagg.FigureCanvasWxAgg(self, wx.ID_ANY, self.figure)
        vbox.Add(self.canvas, 1, flag=wx.ALL|wx.EXPAND)
    # __init__

    """
    def _SetSize(self):
        size = self.GetClientSize()
        print "psize=",size
        size[1] //= 2
        self.SetSize(size)
        self.canvas.SetSize(size)
        self.figure.set_size_inches(float(size[0])/self.figure.get_dpi(),
                                    float(size[1])/self.figure.get_dpi())
        #self.Fit()
    # _SetSize()
    """

    def clear_plot(self):
        for i in xrange(len(self.p)):
            for p in self.p[i]:
                for pp in p: pp.remove()
            self.p[i] = []
            self.lim[i]["x"], self.lim[i]["y"] = [], []
    # clear_plot()

    def add_plot(self, n, x, y, label="", marker="o", color="blue", show_legend=True):
        p = self.subplots[n].plot(x, y, marker=marker, label=label, color=color)
        self.p[n].append(p)

        # Define range
        for k, v in (("x",x), ("y", y)):
            if self.lim[n][k] == []: self.lim[n][k] = [min(v), max(v)]
            else: self.lim[n][k] = [min(min(v), self.lim[n][k][0]), max(max(v), self.lim[n][k][1])]

        self.subplots[n].set_xlim(*self.lim[n]["x"])
        yrange = self.lim[n]["y"][1] - self.lim[n]["y"][0]
        self.subplots[n].set_ylim(self.lim[n]["y"][0]-0.2*yrange/2, self.lim[n]["y"][0]+2.2*yrange/2) # 1-factor, 1+factor

        if show_legend:
            self.subplots[n].legend(loc='best').draggable(True)
    # plot()

    def refresh(self):
        self.SetSize((self.Size[0],self.Size[1]))
        self.canvas.draw()        

# class PlotPanel

class ResultRightPanel(wx.Panel):
    def __init__(self, parent=None, id=wx.ID_ANY):
        wx.Panel.__init__(self, parent=parent, id=id)

        self.current_key = None
        self.current_workdir = None

        vbox = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(vbox)

        self.notebook = wx.Notebook(self, id=wx.ID_ANY, style=wx.BK_DEFAULT)
        vbox.Add(self.notebook, 1, wx.ALL|wx.EXPAND, 5)

        self.plotsPanel = wx.lib.scrolledpanel.ScrolledPanel(self.notebook)
        self.plotsPanel.SetupScrolling()
        self.logPanel = wx.Panel(self.notebook)
        self.notebook.AddPage(self.plotsPanel, "Plots")
        self.notebook.AddPage(self.logPanel, "Log files")

        # Panel for plots
        pvbox = wx.BoxSizer(wx.VERTICAL)
        self.plotsPanel.SetSizer(pvbox)
        self.plots = PlotPanel(self.plotsPanel, nplots=4)
        pvbox.Add(self.plots, 0, flag=wx.ALL|wx.EXPAND)

        # Panel for log files
        lvbox = wx.BoxSizer(wx.VERTICAL)
        self.logPanel.SetSizer(lvbox)
        lhbox1 = wx.BoxSizer(wx.HORIZONTAL)
        lvbox.Add(lhbox1)

        self.cmbLog = wx.ComboBox(self.logPanel, wx.ID_ANY, style=wx.CB_READONLY)
        self.txtLog = wx.TextCtrl(self.logPanel, wx.ID_ANY, size=(450,25), style=wx.TE_MULTILINE|wx.TE_DONTWRAP|wx.TE_READONLY)
        self.txtLog.SetFont(wx.Font(10, wx.FONTFAMILY_MODERN, wx.NORMAL, wx.NORMAL))
        self.lblLog = wx.StaticText(self.logPanel, wx.ID_ANY, "")
        lhbox1.Add(wx.StaticText(self.logPanel, wx.ID_ANY, "Log file: "), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        lhbox1.Add(self.cmbLog)
        lhbox1.Add(self.lblLog, flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL, border=5)
        lvbox.Add(self.txtLog, 1, flag=wx.EXPAND|wx.ALL)

        self.cmbLog.Bind(wx.EVT_COMBOBOX, self.cmbLog_select)
    # __init__()

    def set_current_key(self, key):
        self.current_key = key
    # set_current_key()

    def set_current_workdir(self, wd):
        self.current_workdir = wd
        i_plot = -1

        # Plot stuff
        self.plots.clear_plot()

        spot_xds = os.path.join(wd, "SPOT.XDS")
        if os.path.isfile(spot_xds):
            sx = idxreflp.SpotXds(spot_xds)
            spots = sx.indexed_and_unindexed_by_frame()
            if spots:
                spots_f = map(lambda x: x[0], spots)
                spots_n = map(lambda x: x[1][0]+x[1][1], spots)
                self.plots.add_plot(0, spots_f, spots_n, label="Spots", color="blue")
                spots_n = map(lambda x: x[1][0], spots)
                if sum(spots_n) > 0:
                    self.plots.add_plot(0, spots_f, spots_n, label="Indexed", color="green")

        if config.params.engine == "xds":
            integrate_lp = os.path.join(wd, "INTEGRATE.LP")
            xdsstat_lp = os.path.join(wd, "XDSSTAT.LP")

            if os.path.isfile(integrate_lp):
                lp = integratelp.IntegrateLp(integrate_lp)

                # SgimaR
                self.plots.add_plot(1, map(int,lp.frames), map(float,lp.sigmars), label=("SigmaR"))

                # Rotations
                xs, ys = [], [[], [], []]
                for frames, v in lp.blockparams.items():
                    rots = map(float, v.get("rotation", ["nan"]*3))
                    assert len(rots) == 3
                    if len(frames) > 1:
                        xs.extend([frames[0], frames[-1]])
                        for i in xrange(3): ys[i].extend([rots[i],rots[i]])
                    else:
                        xs.append(frames[0])
                        for i in xrange(3): ys[i].append(rots[i])

                for i, y in enumerate(ys):
                    self.plots.add_plot(2, xs, y, label=("rotx","roty","rotz")[i], color=("red", "green", "blue")[i])

            if os.path.isfile(xdsstat_lp):
                lp = xdsstat.XdsstatLp(xdsstat_lp)
                if lp.by_frame:
                    # R-meas
                    self.plots.add_plot(3, map(int,lp.by_frame["frame"]),
                                                map(float,lp.by_frame["rmeas"]), label=("R-meas"))

        elif config.params.engine == "dials":
            pass

        self.plots.refresh()

        # Log file stuff
        prev_cmbLog_sel = self.cmbLog.GetValue()
        to_append = []

        if config.params.engine == "xds":
            for j in ("XYCORR", "INIT", "COLSPOT", "IDXREF", "DEFPIX", "XPLAN", "INTEGRATE", "CORRECT"):
                for f in glob.glob(os.path.join(wd, "%s*.LP"%j)):
                    to_append.append(os.path.basename(f))
        elif config.params.engine == "dials":
            for j in ("import", "find_spots", "index", "integrate", "export"):
                f = os.path.join(wd, "dials.%s.debug.log"%j)
                if os.path.isfile(f): to_append.append(os.path.basename(f))

        self.cmbLog.Clear()
        self.cmbLog.AppendItems(to_append)

        if prev_cmbLog_sel and prev_cmbLog_sel in to_append:
            self.cmbLog.Select(to_append.index(prev_cmbLog_sel))
        elif "CORRECT.LP" in to_append:
            self.cmbLog.Select(to_append.index("CORRECT.LP"))
        else:
            self.cmbLog.Select(self.cmbLog.GetCount() - 1)

        self.cmbLog_select(None)
    # set_current_workdir()

    def cmbLog_select(self, ev):
        if self.current_workdir is None: return

        lpfile = os.path.join(self.current_workdir, self.cmbLog.GetValue())
        if not os.path.isfile(lpfile): return

        self.lblLog.SetLabel("Modified: %s" % time.ctime(os.path.getmtime(lpfile)))
        self.txtLog.SetValue(open(lpfile).read())
    # cmbLog_select()
# class ResultRightPanel

class MainFrame(wx.Frame):
    def __init__(self, parent=None, id=wx.ID_ANY, topdir=None):
        wx.Frame.__init__(self, parent=parent, id=id, title="KAMO system started at %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
                          size=(1500,950))
        self.adxv = Adxv(adxv_bin=config.params.adxv)

        # Main splitter
        self.splitter = wx.SplitterWindow(self, id=wx.ID_ANY)
        self.ctrlPanel = ControlPanel(self.splitter)

        self.splitter2 = wx.SplitterWindow(self.splitter, id=wx.ID_ANY)
        self.resultLPanel = ResultLeftPanel(self.splitter2)
        self.resultRPanel = ResultRightPanel(self.splitter2)
        self.splitter2.SplitVertically(self.resultLPanel, self.resultRPanel)
        self.splitter2.SetSashGravity(0.5)
        self.splitter2.SetMinimumPaneSize(10)
        self.splitter.SplitHorizontally(self.ctrlPanel, self.splitter2)
        self.splitter.SetSashGravity(0.5)
        self.splitter.SetSashPosition(300) # want to delete this line for Mac, but then unhappy on linux..
        self.splitter.SetMinimumPaneSize(10)

        self.Bind(EVT_SHOW_PROC_RESULT, self.show_proc_result)
        self.Bind(wx.EVT_CLOSE, self.onClose)

        self.watch_log_thread = WatchLogThread(self)
        self.Bind(EVT_LOGS_UPDATED, self.ctrlPanel.on_update)
        if config.params.jobspkl is not None: config.params.logwatch_once = True
        self.watch_log_thread.start(config.params.logwatch_interval)

        self.Show()
    # __init__()

    def onClose(self, ev):
        self.Destroy()
    # onClose()

    def show_proc_result(self, ev):
        key = ev.key
        prefix, nr = key
        result = bssjobs.get_process_result(key)

        for obj in (self.resultLPanel, self.resultRPanel):
            obj.set_current_key(key)
            obj.set_current_workdir(result["workdir"])

        self.resultLPanel.update_summary(job=bssjobs.get_job(key), result=result)
    # show_proc_result()
# class MainFrame

def run_from_args(argv):
    # Not used in this script, but required in KAMO.
    #import scipy
    import networkx

    global batchjobs
    global mainFrame
    global bssjobs

    print """
KAMO (Katappashikara Atsumeta data wo Manual yorimoiikanjide Okaeshisuru) system is an automated data processing system for SPring-8 beamlines.
This is an alpha-version. If you found something wrong, please let staff know! We would appreciate your feedback.

* Use cases (options) *
 - Attention! when not small-wedge mode (normal data collection), small_wedges=false is needed!!
 - If you don't want to use SGE, batch.engine=sh is required.

1) On beamline, on-line data processing along with data collection

  bl=32xu small_wedges=false [workdir=_kamoproc]

2) With ZOO system on BL32XU

  bl=32xu mode=zoo [workdir=_kamoproc]

3) To process already-collected data (off-line & directory search mode)

  bl=other [reverse_phi=false] [include_dir=dirs.lst]

** This program must be started in the top directory of your datasets! **
   (Only processes the data in the subdirectories)

"""

    if "-h" in argv or "--help" in argv:
        print "All parameters:\n"
        iotbx.phil.parse(gui_phil_str).show(prefix="  ", attributes_level=1)
        return

    cmdline = iotbx.phil.process_command_line(args=sys.argv,
                                              master_string=gui_phil_str)
    config.params = cmdline.work.extract()
    args = cmdline.remaining_args

    if config.params.bl is None:
        print "ERROR: bl= is needed."
        return

    # Setup logging
    mylog.config(beamline=config.params.bl, log_root=config.params.log_root)
    mylog.info("Program started in %s." % os.getcwd())

    if len(config.params.include_dir) > 0 and len(config.params.exclude_dir) > 0:
        mylog.error("Can't specify both include_dir= and exclude_dir")
        return

    for arg in args:
        if config.params.topdir is None and os.path.isdir(arg):
            config.params.topdir = os.path.abspath(arg)
        elif not os.path.exists(arg):
            mylog.error("Given path does not exist: %s" % arg)
            return

    if (config.params.known.space_group, config.params.known.unit_cell).count(None) == 1:
        mylog.error("Specify both space_group and unit_cell!")
        return

    if config.params.topdir is None: config.params.topdir = os.getcwd()
    if not os.path.isabs(config.params.topdir):
        config.params.topdir = os.path.abspath(config.params.topdir)

    if len(config.params.include_dir) == 1 and os.path.isfile(config.params.include_dir[0]):
        config.params.include_dir = read_path_list(config.params.include_dir[0])
    if len(config.params.exclude_dir) == 1 and os.path.isfile(config.params.exclude_dir[0]):
        config.params.exclude_dir = read_path_list(config.params.exclude_dir[0])

    # expand wildcard
    config.params.include_dir = expand_wildcard_in_list(config.params.include_dir, sys.stdout)
    config.params.exclude_dir = expand_wildcard_in_list(config.params.exclude_dir, sys.stdout)

    for i, d in enumerate(config.params.include_dir):
        if not os.path.isabs(d): config.params.include_dir[i] = os.path.join(config.params.topdir, d)
    for i, d in enumerate(config.params.exclude_dir):
        if not os.path.isabs(d): config.params.exclude_dir[i] = os.path.join(config.params.topdir, d)        

    if not os.path.exists(config.params.workdir):
        os.makedirs(config.params.workdir)

    if not os.path.isabs(config.params.workdir):
        config.params.workdir = os.path.abspath(config.params.workdir)
    
    # Save params
    savephilpath = os.path.join(config.params.workdir, time.strftime("gui_params_%y%m%d-%H%M%S.txt"))
    libtbx.phil.parse(gui_phil_str).format(config.params).show(out=open(savephilpath, "w"),
                                                               prefix="")
    if config.params.batch.engine == "sge":
        batchjobs = batchjob.SGE(pe_name=config.params.batch.sge_pe_name)
    elif config.params.batch.engine == "sh":
        batchjobs = batchjob.ExecLocal(max_parallel=config.params.batch.sh_max_jobs)
    else:
        raise "Unknown batch engine: %s" % config.params.batch.engine

    if "normal" in config.params.mode and config.params.bl != "other":
        config.params.blconfig.append("/isilon/blconfig/bl%s" % config.params.bl)
    if "zoo" in config.params.mode:
        config.params.blconfig.append("/isilon/BL32XU/BLsoft/PPPP/10.Zoo/ZooConfig")

    if config.params.logwatch_once is None:
        config.params.logwatch_once = (config.params.bl == "other")

    if config.params.xds.override.geometry_reference:
        bssjobs.load_override_geometry(config.params.xds.override.geometry_reference)

    app = wx.App()
    mainFrame = MainFrame(parent=None, id=wx.ID_ANY)
    app.TopWindow = mainFrame
    app.MainLoop()

    mylog.info("Normal exit.")


if __name__ == "__main__":
    import sys
    run_from_args(sys.argv[1:])
