#!/usr/bin/env python
"""ERT forward model (v2): turn parameters.txt and the permeability FIELD into Drogon include files.

Writes include/ert/{gridmult,multregt,aquancon,aqufetp,equil,perm,swatinit}.inc in the runpath:
  gridmult  MULTIREG PORO per zone and MULTFLT (no PERM multipliers: the FIELD carries permeability)
  perm      PERMX, PERMY = base * exp(LNPERM); PERMZ also * KVKH_MULT
  equil     the deck EQUIL with OWC_CENTRAL / OWC_FLANK shifts
  swatinit  SWATINIT remapped to the shifted contacts (drogon_v2_lib.remap_swatinit); swatinit_remap.json
Usage: drogon_params_v2.py <static.npz> [--params parameters.txt] [--field lnpermmult.grdecl] [--include include]
Exit 0 written, 2 input error (missing parameter, field size, static file).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drogon_v2_lib import (  # noqa: E402
    aquifer,
    equil,
    gridmult,
    multregt,
    owc_shifts,
    perm_arrays,
    read_grdecl,
    read_params,
    remap_swatinit,
    write_grdecl,
)

FIELD_NAME = "LNPERM"


def build(static, p: dict[str, float], ln_mult: np.ndarray, equil_text: str) -> tuple[dict[str, str], dict]:
    """All include texts for one realization, and the SWATINIT remap counts."""
    fetp, ancon = aquifer(p)
    shifts = owc_shifts(p)
    perms = perm_arrays(static, ln_mult, p["KVKH_MULT"])
    swat, counts = remap_swatinit(static, shifts)
    files = {
        "gridmult.inc": gridmult(p),
        "multregt.inc": multregt(p),
        "aqufetp.inc": fetp,
        "aquancon.inc": ancon,
        "equil.inc": equil(equil_text, shifts),
        "perm.inc": "".join(write_grdecl(k, v) for k, v in perms.items()),
        "swatinit.inc": write_grdecl("SWATINIT", swat, fmt="%.5f"),
    }
    return files, dict(counts, shifts=shifts.tolist())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("static", type=Path)
    parser.add_argument("--params", type=Path, default=Path("parameters.txt"))
    parser.add_argument("--field", type=Path, default=Path("lnpermmult.grdecl"))
    parser.add_argument("--include", type=Path, default=Path("include"))
    args = parser.parse_args(argv)
    try:
        static = dict(np.load(args.static))
        size = int(np.prod(static["dims"]))
        p = read_params(args.params)["DROGON"]
        ln_mult = read_grdecl(args.field.read_text(), FIELD_NAME, size)
        files, counts = build(static, p, ln_mult, (args.include / "solution" / "drogon.equil").read_text())
    except (OSError, KeyError, ValueError) as error:
        print(f"drogon_params_v2: {error!r}", file=sys.stderr)
        return 2
    out = args.include / "ert"
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
    Path("swatinit_remap.json").write_text(json.dumps(counts) + "\n")
    print(f"wrote {len(files)} includes into {out}; SWATINIT remap {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
