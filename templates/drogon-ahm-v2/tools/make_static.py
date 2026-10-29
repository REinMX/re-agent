#!/usr/bin/env python
"""Precompute the static arrays that the v2 forward-model jobs need, once, into one npz file.

From a finished Flow run of the base deck (EGRID and INIT; the arrays read do not depend on the GEN_KW
parameters) and the deck include files:
  actnum, depth, eqlnum, multnum, satnum, swl, dx, dy, dz (global, 0 or nan where inactive) from EGRID and INIT
  permx, permy, permz, swatinit (global) from the deck includes (the base values, before any multiplier)
  owc (per EQLNUM) from the deck EQUIL
Usage: make_static.py --run <run>/model/<BASE> --deck <deck dir> --out static.npz
Exit 0 written, 2 input error.
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jobs"))
from drogon_v2_lib import read_grdecl  # noqa: E402


def read_owc(text: str) -> np.ndarray:
    """OWC of each EQUIL record (column 3), in record order."""
    owc = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("--") and re.match(r"^[0-9.]", s):
            owc.append(float(s.split("/")[0].split()[2]))
    if not owc:
        raise ValueError("no EQUIL records")
    return np.asarray(owc)


def to_global(actnum: np.ndarray, active_values: np.ndarray, fill) -> np.ndarray:
    out = np.full(actnum.size, fill, dtype=float)
    out[actnum > 0] = active_values
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="path to the run without suffix, e.g. model/DROGON-0")
    parser.add_argument("--deck", required=True, type=Path, help="deck folder with include/")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        from resdata.grid import Grid
        from resdata.resfile import ResdataFile
    except ImportError as error:
        print(f"make_static: resdata is required: {error}", file=sys.stderr)
        return 2
    try:
        grid = Grid(args.run + ".EGRID")
        init = ResdataFile(args.run + ".INIT")
        size = grid.get_global_size()
        actnum = np.array([grid.active(global_index=g) for g in range(size)], dtype=np.int8)
        arrays = {"actnum": actnum, "dims": np.array([grid.nx, grid.ny, grid.nz])}
        for key in ("DEPTH", "EQLNUM", "MULTNUM", "SATNUM", "SWL", "DX", "DY", "DZ"):
            fill = 0 if key in ("EQLNUM", "MULTNUM", "SATNUM") else np.nan
            arrays[key.lower()] = to_global(actnum, init[key][0].numpy_copy(), fill)
        inc = args.deck / "include"
        perm = (inc / "grid" / "drogon.perm").read_text()
        for key in ("PERMX", "PERMY", "PERMZ"):
            arrays[key.lower()] = read_grdecl(perm, key, size)
        arrays["swatinit"] = read_grdecl((inc / "props" / "drogon.swatinit").read_text(), "SWATINIT", size)
        arrays["owc"] = read_owc((inc / "solution" / "drogon.equil").read_text())
    except (OSError, ValueError, KeyError) as error:
        print(f"make_static: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, **arrays)
    print(f"wrote {args.out}: {int(actnum.sum())} active of {size} cells, OWC {arrays['owc'].tolist()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
