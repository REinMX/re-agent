#!/usr/bin/env python
"""ERT forward model after FLOW: SWATINIT QC volumes, then remove the restart file to save disk.

Writes swatinit_qc.json (subscript check_swatinit volumes, in rm3 and in % of the SWATINIT HC volume).
Removes <ECLBASE>.UNRST unless the realization is in --keep (default 0). Exit 2 when subscript is missing.
Usage: drogon_post.py <ECLBASE> <IENS> [--keep 0]
"""

import argparse
import json
import os
import sys
from pathlib import Path

QC_KEYS = ("HC_BELOW_FWL", "SWL_TRUNC", "PC_SCALED", "PPCWMAX", "SWATINIT_1", "FINE_EQUIL")


def volume_report(vols: dict) -> dict:
    """Volumes of check_swatinit qc_volumes as rm3 and as a signed share of the SWATINIT HC volume."""
    hc = vols["SWATINIT_HCVOL"]
    if hc <= 0:
        raise ValueError("SWATINIT HC volume is not positive")
    out = {"PORV": vols["PORV"], "SWATINIT_HCVOL": hc, "SWAT_HCVOL": vols["SWAT_HCVOL"]}
    for key in QC_KEYS:
        out[key] = vols.get(key, 0.0)
        out[f"{key}_PCT_HC"] = -100.0 * vols.get(key, 0.0) / hc
    out["HC_CHANGE_PCT"] = 100.0 * (vols["SWAT_HCVOL"] - hc) / hc
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eclbase")
    parser.add_argument("iens", type=int)
    parser.add_argument("--keep", default="0", help="comma-separated realizations that keep UNRST")
    args = parser.parse_args(argv)
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import res2df
        from subscript.check_swatinit.check_swatinit import make_qc_gridframe, qc_volumes
    except ImportError as error:
        print(f"drogon_post: subscript and res2df are required: {error}", file=sys.stderr)
        return 2
    data = Path(f"{args.eclbase}.DATA")
    frame = make_qc_gridframe(res2df.ResdataFiles(str(data)))
    report = volume_report(qc_volumes(frame))
    Path("swatinit_qc.json").write_text(json.dumps(report, indent=1) + "\n")
    print(f"HC_BELOW_FWL {report['HC_BELOW_FWL_PCT_HC']:.2f} % of HC")
    keep = {int(v) for v in args.keep.split(",") if v}
    unrst = data.with_suffix(".UNRST")
    if args.iens not in keep and unrst.exists():
        unrst.unlink()
        print(f"removed {unrst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
