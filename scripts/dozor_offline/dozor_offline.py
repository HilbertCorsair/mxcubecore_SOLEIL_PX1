#!/usr/bin/env python
"""Count diffraction spots per image of an Eiger dataset with dozor.

Used by PX1XrayCentring after a mesh / line scan. Writes
<output>/dozor.find_spots.log: a psql table with one row per image, in image
order, whose second column is the spot count (see PX1XrayCentring.get_spots).
"""
import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context

os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"  # must be set before h5py loads

import dozor
import h5py
import hdf5plugin  # noqa: F401  registers the bitshuffle/lz4 filters
import numpy as np
from tabulate import tabulate

DET = "/entry/instrument/detector"
SPEC = f"{DET}/detectorSpecific"
HEADERS = ("image", "spots", "score3", "dlim09")

# per-worker state, set once by _init
_dozor = _master = _mask = None
_pixel_max = _cont_size = 0


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-m", "--master", required=True, help="Eiger master file")
    p.add_argument("-M", "--mask", help="mask file (hdf5, /data); default: detector pixel_mask")
    p.add_argument("-s", "--start", type=int, default=1, help="first image (1-based)")
    p.add_argument("-e", "--end", type=int, default=-1, help="last image (default: all)")
    p.add_argument("-c", "--cut_off", type=float, default=5, help="score3 threshold for the hit rate")
    p.add_argument("-o", "--output", default="dozor_res", help="output directory")
    p.add_argument("-n", "--nproc", type=int, default=1, help="number of processes")
    return p.parse_args()


def read_header(master):
    """Return (dozor.dat text, total number of images)."""
    with h5py.File(master, "r") as f:
        g = lambda path: f[path][()]
        orgx, orgy = g(f"{DET}/beam_center_x"), g(f"{DET}/beam_center_y")
        osc = g("/entry/sample/goniometer/omega_range_average")
        config = {
            "nx": g(f"{SPEC}/x_pixels_in_detector"),
            "ny": g(f"{SPEC}/y_pixels_in_detector"),
            "pixel": 0.075,
            "pixel_max": g(f"{SPEC}/countrate_correction_count_cutoff"),
            "sigLev": 0,
            "fraction_polarization": 0.99,
            "detector_distance": g(f"{DET}/detector_distance") * 1000,
            "X-ray_wavelength": g("/entry/instrument/beam/incident_wavelength"),
            "orgx": orgx,
            "orgy": orgy,
            "spot_size": 2,
            "exposure": g(f"{DET}/frame_time"),
            "oscillation_range": osc if osc >= 0.01 else 0.0001,
            "ix_min": 1,
            "ix_max": int(orgx + 150),
            "iy_min": int(orgy - 150),
            "iy_max": int(orgy + 150),
        }
        total = int(g(f"{SPEC}/nimages") * g(f"{SPEC}/ntrigger"))
    dat = "".join(f"{k} {v}\n" for k, v in config.items()) + "end"
    return dat, total


def _init(dat_path, master, mask_file):
    global _dozor, _master, _mask, _pixel_max, _cont_size
    _dozor = dozor.Dozor(dat_path.encode())
    _pixel_max = _dozor.data_input.pixel_max
    _master = h5py.File(master, "r")
    _cont_size = _master["/entry/data/data_000001"].shape[0]
    if mask_file:
        with h5py.File(mask_file, "r") as m:
            _mask = m["/data"][()] > 0
    else:
        _mask = _master[f"{SPEC}/pixel_mask"][()] > 0


def to_uint16(img):
    if img.dtype == np.uint32:
        img[(img > 65534) & (img <= _pixel_max)] = 65534
        img[img > _pixel_max] = 0
    img = img.astype(np.uint16)
    img[_mask] = 65535
    return img


def analyse(chunk):
    """Rows for images start..end (1-based, inclusive); a failed image gives zeros."""
    start, end = chunk
    rows, containers = [], {}
    for k in range(start, end + 1):
        c, offset = divmod(k - 1, _cont_size)
        if c not in containers:
            containers[c] = _master[f"/entry/data/data_{c + 1:06d}"]
        try:
            res, _ = _dozor.do_image(to_uint16(containers[c][offset]))
            rows.append((k, res.NofR, res.score3, res.dlim09))
        except Exception as ex:
            print(f"image {k}: dozor failed: {ex}", file=sys.stderr)
            rows.append((k, 0, 0.0, 0.0))
    return rows


def histogram(spots, width=60, height=10):
    bins = np.array([b.sum() for b in np.array_split(spots, min(width, len(spots)))])
    levels = (bins / bins.max() * height).astype(int) if bins.max() else bins
    return "\n".join(
        "".join("*" if v >= lvl else " " for v in levels) for lvl in range(height, 0, -1)
    )


def main():
    args = parse_args()
    os.makedirs(args.output, exist_ok=True)
    dat, total = read_header(args.master)
    end = total if args.end < 0 else min(args.end, total)
    dat_path = os.path.join(args.output, "dozor.dat")
    with open(dat_path, "w") as f:
        f.write(dat)

    nproc = max(1, min(args.nproc, end - args.start + 1))
    step = max(1, -(-(end - args.start + 1) // (nproc * 4)))  # ~4 chunks per worker
    chunks = [(s, min(s + step - 1, end)) for s in range(args.start, end + 1, step)]
    with ProcessPoolExecutor(
        max_workers=nproc, mp_context=get_context("fork"),
        initializer=_init, initargs=(dat_path, args.master, args.mask),
    ) as pool:
        rows = [row for chunk_rows in pool.map(analyse, chunks) for row in chunk_rows]
    if not rows:
        sys.exit("dozor: no images analysed")

    table = tabulate(rows, headers=HEADERS, tablefmt="psql")
    log_path = os.path.join(args.output, "dozor.find_spots.log")
    with open(log_path + ".tmp", "w") as f:
        f.write(table)
    os.replace(log_path + ".tmp", log_path)  # the caller polls for this file

    spots = np.array([r[1] for r in rows])
    hits = sum(r[2] > args.cut_off for r in rows)
    print(table)
    print(f"{spots.sum()} spots on {len(rows)} images (max {spots.max()}), "
          f"hit rate {100 * hits / len(rows):.1f}% (score3 > {args.cut_off})")
    print(histogram(spots))
    print(f"{args.start}{' ' * (min(60, len(rows)) - len(str(args.start)) - len(str(end)))}{end}")
    print(f"written {log_path}")


if __name__ == "__main__":
    main()
