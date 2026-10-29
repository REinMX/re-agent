#!/usr/bin/env python3
"""Prior design: write the GEN_KW distribution file, or turn parameters.txt into include files.

  prior_design.py dist  [--design ert/design.yml] [--out ert/prior.dist]
  prior_design.py apply [parameters.txt] [include]  [--design ert/design.yml]

`apply` is the ERT forward-model step. It writes <include>/ert/{gridmult,multregt,aqufetp,sattab,equil}.inc.
A design block that is empty writes no file. A missing parameter or a wrong EQUIL record count is an error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
DESIGN = HERE.parent / "ert" / "design.yml"


def load_design(path: Path) -> dict:
    design = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(design, dict):
        raise SystemExit(f"{path}: the design must be a mapping")
    return design


def zone_name(design: dict, region: int | str) -> str:
    if isinstance(region, str):
        return region
    names = [z for z, r in design["zones"].items() if r == region]
    if len(names) != 1:
        raise SystemExit(f"zone region {region} must match one zone in design.zones")
    return names[0]


def multregt_name(design: dict, pair: list) -> str:
    return f"MULTREGT_{zone_name(design, pair[0])}_{zone_name(design, pair[1])}"


def parameters(design: dict) -> list[tuple[str, str]]:
    """(name, prior) in file order. The prior is a GEN_KW distribution line, for example LOGUNIF 0.33 3.0."""
    priors = design["priors"]
    out = []
    for zone in design.get("zones") or {}:
        out += [(f"PERM_{zone}", priors["perm"]), (f"PORO_{zone}", priors["poro"])]
    if design.get("zones"):
        out.append(("KVKH_MULT", priors["kvkh"]))
    out += [(f"MULTFLT_{fault}", priors["multflt"]) for fault in design.get("faults") or []]
    out += [(multregt_name(design, pair), priors["multregt"]) for pair in design.get("zone_pairs") or []]
    if design.get("sattab_source"):
        out += [("KRW_EXP", priors["krw_exp"]), ("KROW_EXP", priors["krow_exp"])]
    out += [(name, priors["contact"]) for name in design.get("contact_groups") or {}]
    if design.get("aquifer"):
        out.append(("AQ_STRENGTH", priors["aquifer"]))
    names = [name for name, _ in out]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate parameter names in the design")
    return out


def dist_text(design: dict) -> str:
    width = max(len(name) for name, _ in parameters(design)) + 2
    return "".join(f"{name:<{width}}{prior}\n" for name, prior in parameters(design))


def read_params(path: Path, group: str) -> dict[str, float]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and ":" in parts[0]:
            prefix, name = parts[0].split(":", 1)
            if prefix == group:
                out[name] = float(parts[1])
    return out


def need(p: dict[str, float], name: str) -> float:
    if name not in p:
        raise SystemExit(f"parameter {name} is not in parameters.txt")
    return p[name]


def gridmult(design: dict, p: dict[str, float]) -> str:
    lines = []
    if design.get("zones"):
        key, kvkh = design["region_keyword"], need(p, "KVKH_MULT")
        lines.append("MULTIREG")
        for zone, region in design["zones"].items():
            perm, poro = need(p, f"PERM_{zone}"), need(p, f"PORO_{zone}")
            lines += [
                f" PERMX {perm:.6g} {region} {key} /",
                f" PERMY {perm:.6g} {region} {key} /",
                f" PERMZ {perm * kvkh:.6g} {region} {key} /",
                f" PORO {poro:.6g} {region} {key} /",
            ]
        lines += ["/", ""]
    if design.get("faults"):
        lines += ["MULTFLT"] + [f" '{f}' {need(p, f'MULTFLT_{f}'):.6g} /" for f in design["faults"]] + ["/"]
    return "\n".join(lines) + "\n"


def multregt(design: dict, p: dict[str, float]) -> str:
    lines = ["MULTREGT"]
    for pair in design["zone_pairs"]:
        regions = [design["zones"][zone_name(design, r)] for r in pair]
        value = need(p, multregt_name(design, pair))
        lines.append(f" {regions[0]} {regions[1]} {value:.6g} '{design['multregt_direction']}' /")
    return "\n".join(lines + ["/"]) + "\n"


def aqufetp(design: dict, p: dict[str, float]) -> str:
    a, s = design["aquifer"], need(p, "AQ_STRENGTH")
    return (
        "AQUFETP\n"
        f" {a['id']} {a['datum']} 1* {float(a['volume']) * s:.6g} {a['compressibility']}"
        f" {float(a['pi']) * s:.6g} {a['pvt_table']} /\n/\n"
    )


def power_transform(rows: list[list[float]], col: int, exponent: float) -> None:
    top = max(r[col] for r in rows)
    if top <= 0:
        return
    for r in rows:
        r[col] = top * (r[col] / top) ** exponent


def sattab(text: str, p: dict[str, float]) -> str:
    """Power-transform krw and krow of each SWOF table. Other lines pass through unchanged.

    A table ends at its "/". Comment and blank lines inside a table keep their place and do not split it.
    """
    out: list[str] = []
    rows: list[list[float] | str] = []
    mode = None
    krw, krow = need(p, "KRW_EXP"), need(p, "KROW_EXP")

    def flush():
        numeric = [r for r in rows if isinstance(r, list)]
        if numeric:
            power_transform(numeric, 1, krw)
            power_transform(numeric, 2, krow)
        out.extend(" ".join(f"{v:.7f}" for v in r) if isinstance(r, list) else r for r in rows)
        rows.clear()

    for line in text.splitlines():
        code = line.split("--", 1)[0].strip()
        if re.match(r"^[A-Z][A-Z0-9_]*$", code):
            flush()
            mode = code
            out.append(line)
        elif mode == "SWOF" and re.match(r"^[-+0-9.]", code):
            body = code.split("/")[0].split()
            if body:
                rows.append([float(v) for v in body])
            if "/" in code:
                flush()
                out.append("/")
        elif mode == "SWOF" and rows and not code:
            rows.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out) + "\n"


def equil(text: str, design: dict, p: dict[str, float]) -> str:
    shift = {}
    for name, regions in design["contact_groups"].items():
        for region in regions:
            if region in shift:
                raise SystemExit(f"EQUIL region {region} is in more than one contact group")
            shift[region] = need(p, name)
    out, region = [], 0
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("--") and re.match(r"^[0-9.]", s):
            region += 1
            if region not in shift:
                raise SystemExit(f"EQUIL record {region} is in no contact group")
            values, rest = s.split("/", 1)
            v = values.split()
            v[2] = f"{float(v[2]) + shift[region]:.2f}"
            out.append("    " + "  ".join(v) + " /" + rest)
        else:
            out.append(line)
    if region != len(shift):
        raise SystemExit(f"expected {len(shift)} EQUIL records from contact_groups, found {region}")
    return "\n".join(out) + "\n"


def apply(design: dict, params_file: Path, include: Path) -> list[Path]:
    p = read_params(params_file, design["group"])
    if not p:
        raise SystemExit(f"no {design['group']}: parameters in {params_file}")
    target = include / "ert"
    target.mkdir(parents=True, exist_ok=True)
    texts = {}
    if design.get("zones") or design.get("faults"):
        texts["gridmult.inc"] = gridmult(design, p)
    if design.get("zone_pairs"):
        texts["multregt.inc"] = multregt(design, p)
    if design.get("aquifer"):
        texts["aqufetp.inc"] = aqufetp(design, p)
    if design.get("sattab_source"):
        texts["sattab.inc"] = sattab((include / design["sattab_source"]).read_text(encoding="utf-8"), p)
    if design.get("contact_groups"):
        texts["equil.inc"] = equil((include / design["equil_source"]).read_text(encoding="utf-8"), design, p)
    written = []
    for name, text in texts.items():
        (target / name).write_text(text, encoding="utf-8")
        written.append(target / name)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("dist", "apply"))
    ap.add_argument("paths", nargs="*", type=Path, help="apply: parameters.txt and the include folder")
    ap.add_argument("--design", type=Path, default=DESIGN)
    ap.add_argument("--out", type=Path, default=DESIGN.with_name("prior.dist"))
    args = ap.parse_args(argv)
    design = load_design(args.design)
    if args.command == "dist":
        args.out.write_text(dist_text(design), encoding="utf-8")
        print(f"wrote {len(parameters(design))} parameters to {args.out}")
        return 0
    params_file = args.paths[0] if args.paths else Path("parameters.txt")
    include = args.paths[1] if len(args.paths) > 1 else Path("include")
    written = apply(design, params_file, include)
    print(f"wrote {', '.join(w.name for w in written)} into {include / 'ert'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
