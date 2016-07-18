"""
(c) RIKEN 2015. All rights reserved. 
Author: Keitaro Yamashita

This software is released under the new BSD License; see LICENSE.
"""
import re
import os
import numpy
from cctbx import crystal
from cctbx import miller
from cctbx import uctbx
from cctbx.array_family import flex
from libtbx.utils import null_out
from yamtbx.dataproc.xds import re_xds_kwd

def is_xds_ascii(filein):
    if not os.path.isfile(filein): return False

    line = open(filein).readline()
    return "FORMAT=XDS_ASCII" in line
# is_xds_ascii()

class XDS_ASCII:

    def __init__(self, filein, log_out=None, read_data=True, i_only=False):
        self._log = null_out() if log_out is None else log_out
        self._filein = filein
        self.indices = flex.miller_index()
        self.i_only = i_only
        self.iobs, self.sigma_iobs, self.xd, self.yd, self.zd, self.rlp, self.peak, self.corr = [flex.double() for i in xrange(8)]
        self.iframe = flex.int()
        self.iset = flex.int() # only for XSCALE
        self.input_files = {} # only for XSCALE [iset:(filename, wavelength), ...]

        self.read_header()
        if read_data:
            self.read_data()

    def read_header(self):
        re_item = re.compile("!ITEM_([^=]+)=([0-9]+)")

        colindex = {} # {"H":1, "K":2, "L":3, ...}
        nitemfound = 0
        flag_data_start = False
        num_hkl = 0

        headers = []

        for line in open(self._filein):
            if flag_data_start:
                if line.startswith("!END_OF_DATA"):
                    break
                num_hkl += 1
                continue

            if line.startswith('!END_OF_HEADER'):
                flag_data_start = True
                continue

            if line.startswith("! ISET="):
                pars = dict(re_xds_kwd.findall(line))
                iset = int(pars["ISET"])
                if iset not in self.input_files: self.input_files[iset] = [None, None]
                if "INPUT_FILE" in pars:
                    self.input_files[iset][0] = pars["INPUT_FILE"]
                elif "X-RAY_WAVELENGTH" in pars:
                    tmp = pars["X-RAY_WAVELENGTH"]
                    if " (" in tmp: tmp = tmp[:tmp.index(" (")]
                    self.input_files[iset][1] = tmp
            else:
                headers.extend(re_xds_kwd.findall(line[line.index("!")+1:]))

        self.nx, self.ny, self.anomalous, self.zmin, self.zmax = (None,)*5


        for key, val in headers:
            if key == "NUMBER_OF_ITEMS_IN_EACH_DATA_RECORD":
                nitem = int(val.strip())
                print >>self._log, 'number of items according to header is', nitem
            elif key == "UNIT_CELL_CONSTANTS":
                a, b, c, al, be, ga = map(lambda x:float(x), val.strip().split())
            elif key == "UNIT_CELL_A-AXIS":
                self.a_axis = tuple(map(float, val.split()))
            elif key == "UNIT_CELL_B-AXIS":
                self.b_axis = tuple(map(float, val.split()))
            elif key == "UNIT_CELL_C-AXIS":
                self.c_axis = tuple(map(float, val.split()))
            elif key.startswith("ITEM_"):
                item, ind = key[len("ITEM_"):], int(val)
                colindex[item] = ind - 1
                nitemfound += 1
            elif key == "NX":
                self.nx = int(val)
            elif key == "NY":
                self.ny = int(val)
            elif key == "QX":
                self.qx = float(val)
            elif key == "QY":
                self.qy = float(val)
            elif key == "ORGX":
                self.orgx = float(val)
            elif key == "ORGY":
                self.orgy = float(val)
            elif key == "DATA_RANGE":
                self.zmin, self.zmax = map(lambda x:int(x), val.strip().split())
            elif key == "SPACE_GROUP_NUMBER":
                ispgrp = int(val.strip())
            elif key == "FRIEDEL'S_LAW":
                assert val.strip() in ("TRUE", "FALSE")
                self.anomalous = val.strip() == "FALSE"
            elif key == "DETECTOR_DISTANCE":
                self.distance = float(val)
            elif key == "X-RAY_WAVELENGTH":
                self.wavelength = float(val.split()[0])
            elif key == "INCIDENT_BEAM_DIRECTION":
                self.incident_axis = tuple(map(float, val.split()))
            elif key == "ROTATION_AXIS":
                self.rotation_axis = tuple(map(float, val.split()))
            elif key == "VARIANCE_MODEL":
                self.variance_model = tuple(map(float, val.split()))

        assert nitem == len(colindex)

        self._colindex = colindex
        self._num_hkl = num_hkl
        self.symm = crystal.symmetry(unit_cell=(a, b, c, al, be, ga),
                                     space_group=ispgrp)

        self.symm.show_summary(self._log)
        print >>self._log, 'data_range=', self.zmin, self.zmax

    # read_header()
    
    def read_data(self):
        colindex = self._colindex
        is_xscale = "RLP" not in colindex
        flag_data_start = False

        col_H, col_K, col_L = colindex["H"], colindex["K"], colindex["L"]
        col_i, col_sig, col_xd, col_yd, col_zd = colindex["IOBS"], colindex["SIGMA(IOBS)"], colindex["XD"], colindex["YD"], colindex["ZD"]
        col_rlp, col_peak, col_corr, col_iset = colindex.get("RLP", None), colindex.get("PEAK", None), colindex.get("CORR", None), colindex.get("ISET", None)
        self.indices = []
        self.xd, self.yd, self.zd = [], [], []
        self.iframe, self.rlp, self.peak, self.corr, self.iset = [], [], [], [], []
        for line in open(self._filein):
            if flag_data_start:
                if line.startswith("!END_OF_DATA"):
                    break

                sp = line.split()
                h, k, l = int(sp[col_H]), int(sp[col_K]), int(sp[col_L])
                self.indices.append([h,k,l])

                self.iobs.append(float(sp[col_i]))
                self.sigma_iobs.append(float(sp[col_sig]))
                if not self.i_only:
                    self.xd.append(float(sp[col_xd]))
                    self.yd.append(float(sp[col_yd]))
                    self.zd.append(float(sp[col_zd]))
                    self.iframe.append(int(self.zd[-1])+1)
                    if not is_xscale:
                        self.rlp.append(float(sp[col_rlp]))
                        self.peak.append(float(sp[col_peak]))
                        self.corr.append(float(sp[col_corr]))
                    else:
                        self.iset.append(int(sp[col_iset]))
                    #res = symm.unit_cell().d((h,k,l))

                    if self.iframe[-1] < 0:
                        self.iframe[-1] = 0
                        print >>self._log, 'reflection with surprisingly low z-value:', self.zd[-1]

            if line.startswith('!END_OF_HEADER'):
                flag_data_start = True

        self.indices = flex.miller_index(self.indices)
        self.iobs, self.sigma_iobs, self.xd, self.yd, self.zd, self.rlp, self.peak, self.corr = [flex.double(x) for x in (self.iobs, self.sigma_iobs, self.xd, self.yd, self.zd, self.rlp, self.peak, self.corr)]
        self.iframe = flex.int(self.iframe)
        self.iset = flex.int(self.iset) # only for XSCALE

        print >>self._log, "Reading data done.\n"

    # read_data()

    def get_frame_range(self): 
        """quick function only to get frame number range"""

        flag_data_start = False
        col_zd = self._colindex["ZD"]
        min_frame, max_frame = 0, 0
        for line in open(self._filein):
            if flag_data_start:
                if line.startswith("!END_OF_DATA"):
                    break

                sp = line.split()
                iframe = int(float(sp[col_zd]))+1
                if iframe > 0 and iframe < min_frame: min_frame = iframe 
                if iframe > max_frame: max_frame = iframe

            if line.startswith('!END_OF_HEADER'):
                flag_data_start = True

        return min_frame, max_frame
    # get_frame_range()

    def as_miller_set(self, anomalous_flag=None):
        if anomalous_flag is None:
            anomalous_flag = self.anomalous

        return miller.set(crystal_symmetry=self.symm,
                          indices=self.indices,
                          anomalous_flag=anomalous_flag)
    # as_miller_set()

    def i_obs(self, anomalous_flag=None):
        array_info = miller.array_info(source_type="xds_ascii")#, wavelength=)
        return miller.array(self.as_miller_set(anomalous_flag),
                            data=self.iobs, sigmas=self.sigma_iobs).set_info(array_info).set_observation_type_xray_intensity()
    # i_obs()

    def remove_selection(self, sel):
        params = ("indices", "iobs", "sigma_iobs")
        if not self.i_only:
            params += ("xd", "yd", "zd", "rlp", "peak", "corr", "iframe")

        for p in params:
            if not getattr(self, p): continue
            setattr(self, p, getattr(self, p).select(~sel))
    # remove_selection()

    def remove_rejected(self):
        sel = self.sigma_iobs <= 0
        self.remove_selection(sel)
    # remove_rejected()

    def write_selected(self, sel, hklout):
        ofs = open(hklout, "w")

        data_flag = False
        count = 0
        for line in open(self._filein):
            if line.startswith('!END_OF_HEADER'):
                ofs.write(line)
                data_flag = True
            elif line.startswith("!END_OF_DATA"):
                ofs.write(line)
                break
            elif not data_flag:
                ofs.write(line)
            elif data_flag:
                if sel[count]: ofs.write(line)
                count += 1
    # write_selected()

    def write_reindexed(self, op, hklout, space_group=None):
        """
        XXX Assuming hkl has 6*3 width!!
        """
        ofs = open(hklout, "w")
        col_H, col_K, col_L = map(lambda x:self._colindex[x], "HKL")
        assert col_H==0 and col_K==1 and col_L==2

        tr_mat = numpy.array(op.c_inv().r().as_double()).reshape(3,3).transpose()
        transformed = numpy.dot(tr_mat, numpy.array([self.a_axis, self.b_axis, self.c_axis]))
        
        data_flag = False

        for line in open(self._filein):
            if line.startswith('!UNIT_CELL_CONSTANTS='):
                # XXX split by fixed columns
                cell = uctbx.unit_cell(line[line.index("=")+1:].strip())
                cell_tr = cell.change_basis(op)
                if space_group is not None: cell_tr = space_group.average_unit_cell(cell_tr)
                ofs.write("!UNIT_CELL_CONSTANTS=%10.3f%10.3f%10.3f%8.3f%8.3f%8.3f\n" % cell_tr.parameters())
            elif line.startswith('!SPACE_GROUP_NUMBER=') and space_group is not None:
                ofs.write("!SPACE_GROUP_NUMBER=%5d \n" % space_group.type().number())
            elif line.startswith("!UNIT_CELL_A-AXIS="):
                ofs.write("!UNIT_CELL_A-AXIS=%10.3f%10.3f%10.3f\n" % tuple(transformed[0,:]))
            elif line.startswith("!UNIT_CELL_B-AXIS="):
                ofs.write("!UNIT_CELL_B-AXIS=%10.3f%10.3f%10.3f\n" % tuple(transformed[1,:]))
            elif line.startswith("!UNIT_CELL_C-AXIS="):
                ofs.write("!UNIT_CELL_C-AXIS=%10.3f%10.3f%10.3f\n" % tuple(transformed[2,:]))
            elif line.startswith('!END_OF_HEADER'):
                ofs.write(line)
                data_flag = True
            elif line.startswith("!END_OF_DATA"):
                ofs.write(line)
                break
            elif not data_flag:
                ofs.write(line)
            elif data_flag:
                hkl = tuple(map(int, line[:18].split()))
                hkl = op.apply(hkl)
                ofs.write("%6d%6d%6d"%hkl)
                ofs.write(line[18:])

        return cell_tr
    # write_selected()

#class XDS_ASCII

