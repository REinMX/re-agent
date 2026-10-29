#!/usr/bin/env python
"""ERT forward model: turn parameters.txt (DROGON:<NAME> value) into Drogon include files.

Writes include/ert/{gridmult,multregt,aquancon,aqufetp,sattab,equil}.inc in the runpath.
Usage: drogon_params.py [parameters.txt] [include_dir]
"""

import re
import sys
from pathlib import Path

ZONES = {"VALYSAR": 1, "THERYS": 2, "VOLON": 3}
FAULTS = ["F1", "F2", "F3", "F4", "F5", "F6"]
OWC_FLANK_REGIONS = {1, 4, 7}
AQ_VOLUME, AQ_PI = 2.0e9, 300.0


def read_params(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and ":" in parts[0]:
            group, name = parts[0].split(":", 1)
            if group == "DROGON":
                out[name] = float(parts[1])
    return out


def gridmult(p):
    lines = ["MULTIREG"]
    for zone, region in ZONES.items():
        m = p[f"PERM_{zone}"]
        lines += [
            f" PERMX {m:.6g} {region} M /",
            f" PERMY {m:.6g} {region} M /",
            f" PERMZ {m * p['KVKH_MULT']:.6g} {region} M /",
            f" PORO {p[f'PORO_{zone}']:.6g} {region} M /",
        ]
    lines += ["/", "", "MULTFLT"] + [f" '{f}' {p[f'MULTFLT_{f}']:.6g} /" for f in FAULTS] + ["/"]
    return "\n".join(lines) + "\n"


def multregt(p):
    return (
        "MULTREGT\n"
        f" 1 2 {p['MULTZ_VAL_THE']:.6g} 'Z' /\n"
        " 1 3 1 'Z' /\n"
        f" 2 3 {p['MULTZ_THE_VOL']:.6g} 'Z' /\n/\n"
    )


def aquifer(p):
    s = p["AQ_STRENGTH"]
    fetp = f"AQUFETP\n 1 1750 1* {AQ_VOLUME * s:.6g} 5e-5 {AQ_PI * s:.6g} 1 /\n/\n"
    ancon = "AQUANCON\n 1 1 46 1 73 31 31 K+ 1* 1* NO /\n/\n"
    return fetp, ancon


def power_transform(rows, col, expo):
    top = max(r[col] for r in rows)
    if top <= 0:
        return
    for r in rows:
        r[col] = top * (r[col] / top) ** expo


def sattab(text, p):
    out, rows, mode = [], [], None

    def flush():
        if rows:
            if mode == "SWOF":
                power_transform(rows, 1, p["KRW_EXP"])
                power_transform(rows, 2, p["KROW_EXP"])
            out.extend(" ".join(f"{v:.7f}" for v in r) for r in rows)
            rows.clear()

    for line in text.splitlines():
        s = line.strip()
        if s in ("SWOF", "SGOF"):
            flush()
            mode = s
            out.append(line)
        elif re.match(r"^[-+0-9.]", s) and not s.startswith("--"):
            body = s.split("/")[0].split()
            if body:
                rows.append([float(v) for v in body])
            if "/" in s:
                flush()
                out.append("/")
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out) + "\n"


def equil(text, p):
    out, region = [], 0
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("--") and re.match(r"^[0-9.]", s):
            region += 1
            vals, rest = s.split("/", 1)
            v = vals.split()
            shift = p["OWC_FLANK"] if region in OWC_FLANK_REGIONS else p["OWC_CENTRAL"]
            v[2] = f"{float(v[2]) + shift:.2f}"
            out.append("    " + "  ".join(v) + " /" + rest)
        else:
            out.append(line)
    if region != 7:
        raise SystemExit(f"expected 7 EQUIL records, found {region}")
    return "\n".join(out) + "\n"


def main():
    params = Path(sys.argv[1] if len(sys.argv) > 1 else "parameters.txt")
    inc = Path(sys.argv[2] if len(sys.argv) > 2 else "include")
    p = read_params(params)
    ert = inc / "ert"
    ert.mkdir(parents=True, exist_ok=True)
    (ert / "gridmult.inc").write_text(gridmult(p))
    (ert / "multregt.inc").write_text(multregt(p))
    fetp, ancon = aquifer(p)
    (ert / "aqufetp.inc").write_text(fetp)
    (ert / "aquancon.inc").write_text(ancon)
    (ert / "sattab.inc").write_text(sattab((inc / "props" / "drogon.sattab").read_text(), p))
    (ert / "equil.inc").write_text(equil((inc / "solution" / "drogon.equil").read_text(), p))
    print(f"wrote {len(p)} parameters into {ert}")


if __name__ == "__main__":
    main()
