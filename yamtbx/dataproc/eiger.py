"""
(c) RIKEN 2015. All rights reserved. 
Author: Keitaro Yamashita

This software is released under the new BSD License; see LICENSE.

Reference: tools/python/streamreceiver2.py by Dectris.
"""

import dxtbx.format # to set HDF5_PLUGIN_PATH in phenix environment

import h5py
import json
import struct
import numpy
import os
from yamtbx.dataproc import software_binning

def read_stream_data(frames, bss_job_mode=4):
    import lz4
    import bitshuffle
    if len(frames) != 5:
        return None, None

    header = json.loads(frames[0].bytes)
    for i in (1,3,4): header.update(json.loads(frames[i].bytes))

    if header.get("bss_job_mode", 4) != bss_job_mode:
        return None, None

    dtype = header["type"]
    shape = header["shape"][::-1]

    if dtype in ("int32","uint32"): byte = 4
    elif dtype in ("int16","uint16"): byte = 2
    else: raise RuntimeError("Unknown dtype (%s)"%dtype)

    size = byte*shape[0]*shape[1]

    if header["encoding"] == "lz4<":
        data = lz4.loads(struct.pack('<I', size) + frames[2].bytes)
        data = numpy.fromstring(data, dtype=dtype).reshape(shape)
        assert data.size * data.dtype.itemsize == size
    elif header["encoding"] == "bs32-lz4<":
        data = frames[2].bytes
        blob = numpy.fromstring(data[12:],dtype=numpy.uint8)
        # blocksize is big endian uint32 starting at byte 8, divided by element size
        blocksize = numpy.ndarray(shape=(),dtype=">u4", buffer=data[8:12])/4
        data = bitshuffle.decompress_lz4(blob, shape, numpy.dtype(dtype), blocksize)
        data = data.reshape(shape)
    elif header["encoding"] == "bs16-lz4<":
        data = frames[2].bytes
        blob = numpy.fromstring(data[12:],dtype=numpy.uint8)
        data = bitshuffle.decompress_lz4(blob, shape, numpy.dtype(dtype))
        data = data.reshape(shape)
    else:
        RuntimeError("Unknown encoding (%s)"%header["encoding"])

    data = data.astype(numpy.int32)
    data[data==2**(byte*8)-1] = -1
    return header, data
# read_stream_data()

def extract_data(h5master, frameno, apply_pixel_mask=True, return_raw=False):
    h5 = h5py.File(h5master, "r")
    data = None

    if 0:
        i_seen = 0
        for k in sorted(h5["/entry/data"].keys()):
            try:
                for i in xrange(h5["/entry/data"][k].shape[0]):
                    i_seen += 1
                    if i_seen == frameno:
                        data = h5["/entry/data"][k][i,]
            except KeyError:
                break
    else:
        for k in sorted(h5["/entry/data"].keys()):
            if not h5["/entry/data"].get(k): continue
            image_nr_low = h5["/entry/data"][k].attrs["image_nr_low"]
            image_nr_high = h5["/entry/data"][k].attrs["image_nr_high"]
            if image_nr_low <= frameno <= image_nr_high:
                idx = frameno - image_nr_low
                data = h5["/entry/data"][k][idx,]
                break

    if data is None:
        print "Data not found."
        return data

    if return_raw:
        return data

    byte = data.dtype.itemsize
    data = data.astype(numpy.int32)
    data[data==2**(byte*8)-1] = -3 # To see pixels not masked by pixel mask.
    if apply_pixel_mask and "/entry/instrument/detector/detectorSpecific/pixel_mask" in h5:
        mask = h5["/entry/instrument/detector/detectorSpecific/pixel_mask"][:]
        data[mask==1] = -1
        data[mask>1] = -2

    return data
# extract_data()

def extract_data_path(h5master, path, apply_pixel_mask=True, return_raw=False):
    h5 = h5py.File(h5master, "r")
    data = h5.get(path)
    print path
    if data is None:
        print "Data not found."
        return data

    if return_raw:
        return data

    data = data[:]
    byte = data.dtype.itemsize
    data = data.astype(numpy.int32)
    data[data==2**(byte*8)-1] = -3 # To see pixels not masked by pixel mask.
    if apply_pixel_mask and "/entry/instrument/detector/detectorSpecific/pixel_mask" in h5:
        mask = h5["/entry/instrument/detector/detectorSpecific/pixel_mask"][:]
        data[mask==1] = -1
        data[mask>1] = -2

    return data
# extract_data()

def extract_data_range_sum(h5master, frames):
    h5 = h5py.File(h5master, "r")
    i_seen = 0
    i_found = 0
    data = None
    for k in sorted(h5["/entry/data"].keys()):
        try:
            for i in xrange(h5["/entry/data"][k].shape[0]):
                i_seen += 1
                if i_seen in frames:
                    i_found += 1
                    tmp = h5["/entry/data"][k][i,]
                    badnum = 2**(tmp.dtype.itemsize*8)-1
                    tmp = tmp.astype(numpy.int32)
                    tmp[tmp==badnum] = -1 # XXX if not always 'bad' pixel...
                    if data is None: data = tmp.astype(numpy.int32)
                    else: data += tmp
        except KeyError:
            break

    if data is None:
        print "Data not found."
        return data

    data[data<0] = -3 # To see pixels not masked by pixel mask.
    # Apply pixel mask
    if "/entry/instrument/detector/detectorSpecific/pixel_mask" in h5:
        mask = h5["/entry/instrument/detector/detectorSpecific/pixel_mask"][:]
        data[mask==1] = -1
        data[mask>1] = -2

    if i_found != len(frames): return None
    return data
# extract_data()

def extract_to_minicbf(h5master, frameno_or_path, cbfout, binning=1):
    from yamtbx.dataproc import cbf
    from yamtbx.dataproc.XIO.plugins import eiger_hdf5_interpreter

    if type(frameno_or_path) in (tuple, list):
        data = extract_data_range_sum(h5master, frameno_or_path)
        nframes = len(frameno_or_path)
    elif type(frameno_or_path) is int:
        data = extract_data(h5master, frameno_or_path)
        nframes = 1
    else:
        data = extract_data_path(h5master, frameno_or_path)
        nframes = 1
        

    if data is None:
        raise RuntimeError("Cannot extract frame %s from %s"%(frameno_or_path, h5master))

    h = eiger_hdf5_interpreter.Interpreter().getRawHeadDict(h5master)
    h5 = h5py.File(h5master, "r")

    if binning>1:
        beamxy = h["BeamX"], h["BeamY"]
        data, (h["BeamX"], h["BeamY"]) = software_binning(data, binning, beamxy)

    h["Detector"] = h5["/entry/instrument/detector/description"].value
    h["ExposurePeriod"] = h5["/entry/instrument/detector/frame_time"].value
    h["PhiWidth"] *= nframes
    cbf.save_numpy_data_as_cbf(data.flatten(), size1=data.shape[1], size2=data.shape[0], title="",
                               cbfout=cbfout,
                               pilatus_header="""\
# Detector: %(Detector)s, S/N %(SerialNumber)s
# Pixel_size %(PixelX)e m x %(PixelY)e m
# %(SensorMaterial)s sensor, thickness %(SensorThickness).3e m
# Exposure_time %(ExposureTime).6f s
# Exposure_period %(ExposurePeriod).6f s
# Count_cutoff %(Overload)d counts
# Wavelength %(Wavelength).6f A
# Detector_distance %(Distance).3e m
# Beam_xy (%(BeamX).1f, %(BeamY).1f) pixels
# Start_angle %(PhiStart).6f deg.
# Angle_increment %(PhiWidth).6f deg.
""" % h)
# extract_to_minicbf()

def compress_h5data(h5obj, path, data, chunks, compression="bslz4"):
    import bitshuffle.h5

    if compression=="bslz4":
        dataset = h5obj.create_dataset(path, data.shape,
                                       compression=bitshuffle.h5.H5FILTER,
                                       compression_opts=(0, bitshuffle.h5.H5_COMPRESS_LZ4),
                                       chunks=chunks, dtype=data.dtype, data=data)
    elif compression=="shuf+gz":
        dataset = h5obj.create_dataset(path, data.shape,
                                       compression="gzip",shuffle=True,
                                       chunks=chunks, dtype=data.dtype, data=data)
    else:
        raise "Unknwon compression name (%s)" % compression

    return dataset
# compress_h5data()

def create_data_file(outfile, data, chunks, nrlow, nrhigh):
    h5 = h5py.File(outfile, "w")
    h5.create_group("/entry")
    h5["/entry"].attrs["NX_class"] = "NXentry"
    h5.create_group("/entry/data")
    h5["/entry/data"].attrs["NX_class"] = "NXdata"

    dataset = compress_h5data(h5, "/entry/data/data", data, chunks)
    dataset.attrs["image_nr_low"] = nrlow
    dataset.attrs["image_nr_high"] = nrhigh

    h5.close()
# create_data_file()

def get_masterh5_related_filenames(masterh5):
    ret = [masterh5]

    h5 = h5py.File(masterh5, "r")
    for k in h5["/entry/data"]:
        ret.append(os.path.join(os.path.dirname(masterh5),
                                h5["/entry/data"].get(k, getlink=True).filename))

    return ret
# get_masterh5_related_filenames()
