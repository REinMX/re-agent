#!/usr/bin/env python
"""Build the Drogon SCAL recommendation (low/base/high per SATNUM) and the facies groups from the deck tables.

Base: the LET fit of each SWOF and SGOF table with skills/scal-uncertainty/scripts/satfunc_fit.py. The endpoints
(swl, swcr, sorw, krwend, sgcr, sorg, krgend) and the simple-J Pc constants come from the pyscal header that the
deck carries for each SATNUM; the fit gives the LET shape. Groups: SATNUMs whose fitted shapes agree within
SHAPE_TOL and whose swl differ by at most SWL_TOL share one interpolation parameter.
Low and high: an ASSUMED engineering range (ASSUMED_RANGE). Drogon has no SCAL laboratory data.

Usage: make_scal_rec.py --deck-sattab <drogon.sattab> --out <dir> [--truth-krw-exp 1.25 --truth-krow-exp 0.85]
Writes <out>/drogon_scalrec.csv, <out>/satnum_groups.csv and <out>/scal_report.json.
Exit 0 written, 1 written but the base does not reproduce the deck (max error > --tol), 2 input error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SHAPE_TOL = 0.05
SWL_TOL = 0.05
GROUP_NAMES = {
    "Channel": "CH",
    "Crevasse": "CR",
    "Uppershoreface": "USF",
    "Lowershoreface": "LSF",
    "Offshore": "OFF",
    "Floodplain": "FP",
}
# Pessimistic (low) and optimistic (high) for oil recovery. Factors on LET L and E, shifts on endpoints.
ASSUMED_RANGE = {
    "low": {
        "SORW": +0.05,
        "KRWEND": +0.10,
        "KROEND": -0.15,
        "LET_W": 0.7,
        "LET_O": 1.3,
        "SORG": +0.05,
        "KRGEND": +0.05,
        "KROGEND": -0.15,
        "LET_G": 0.7,
        "LET_OG": 1.3,
    },
    "high": {
        "SORW": -0.05,
        "KRWEND": -0.10,
        "KROEND": 0.0,
        "LET_W": 1.3,
        "LET_O": 0.7,
        "SORG": -0.05,
        "KRGEND": -0.10,
        "KROGEND": 0.0,
        "LET_G": 1.3,
        "LET_OG": 0.7,
    },
}
HEADER_KEYS = (
    "swirr",
    "swl",
    "swcr",
    "sorw",
    "krwend",
    "krwmax",
    "sgcr",
    "sorg",
    "krgend",
    "a",
    "b",
    "poro_ref",
    "perm_ref",
    "drho",
)


def parse_headers(text: str) -> dict[int, dict]:
    """Per SATNUM: the facies name and the pyscal header values (SWOF and SGOF headers merged)."""
    out: dict[int, dict] = {}
    satnum = None
    for line in text.splitlines():
        s = line.strip()
        if m := re.match(r"--\s*SATNUM\s+(\d+)\s+(\S+)", s):
            satnum = int(m.group(1))
            out.setdefault(satnum, {"facies": m.group(2)})
            continue
        if satnum is None or not s.startswith("--"):
            continue
        for key, value in re.findall(r"([a-z_]+)=(-?[0-9.]+(?:e-?\d+)?)", s):
            if key in HEADER_KEYS and key not in out[satnum]:
                out[satnum][key] = float(value)
    for satnum, row in out.items():
        missing = [k for k in HEADER_KEYS if k not in row]
        if missing:
            raise ValueError(f"SATNUM {satnum}: header lacks {', '.join(missing)}")
    return out


def facies_of(name: str) -> str:
    return name.split("_")[0]


def group_satnums(rows: dict[int, dict]) -> dict[int, str]:
    """Group SATNUMs with equal LET shapes (within SHAPE_TOL) and swl within SWL_TOL. Single linkage."""
    shape_keys = ("LW", "EW", "TW", "LO", "EO", "TO", "LG", "EG", "TG", "LOG", "EOG", "TOG")
    satnums = sorted(rows)
    parent = {s: s for s in satnums}

    def find(s):
        while parent[s] != s:
            s = parent[s]
        return s

    for i, a in enumerate(satnums):
        for b in satnums[i + 1 :]:
            same_shape = all(
                abs(rows[a][k] - rows[b][k]) <= SHAPE_TOL * max(1.0, abs(rows[a][k])) for k in shape_keys
            )
            if same_shape and abs(rows[a]["SWL"] - rows[b]["SWL"]) <= SWL_TOL:
                parent[find(b)] = find(a)
    members: dict[int, list[int]] = {}
    for s in satnums:
        members.setdefault(find(s), []).append(s)
    names = {}
    for group in members.values():
        facies = sorted({facies_of(rows[s]["facies"]) for s in group}, key=list(GROUP_NAMES).index)
        label = "_".join(GROUP_NAMES.get(f, f.upper()) for f in facies)
        names.update({s: label for s in group})
    return names


def case_row(base: dict, case: str) -> dict:
    """One recommendation row for CASE low/base/high from the base row and ASSUMED_RANGE."""
    row = dict(base, CASE=case)
    if case == "base":
        return row
    r = ASSUMED_RANGE[case]
    row["SORW"] = base["SORW"] + r["SORW"]
    row["KRWEND"] = min(1.0, base["KRWEND"] + r["KRWEND"])
    row["KROEND"] = min(1.0, base["KROEND"] + r["KROEND"])
    row["SORG"] = base["SORG"] + r["SORG"]
    row["KRGEND"] = min(1.0, base["KRGEND"] + r["KRGEND"])
    row["KROGEND"] = min(1.0, base["KROGEND"] + r["KROGEND"])
    for keys, factor in (
        (("LW", "EW"), r["LET_W"]),
        (("LO", "EO"), r["LET_O"]),
        (("LG", "EG"), r["LET_G"]),
        (("LOG", "EOG"), r["LET_OG"]),
    ):
        for k in keys:
            row[k] = base[k] * factor
    return row


def pyscal_frame(rows: list[dict]):
    """The rows in the column names of the pyscal factory (SATNUM, CASE, wateroil and gasoil LET, simple J)."""
    import pandas as pd

    names = {"LO": "LOW", "EO": "EOW", "TO": "TOW", "KROGEND": "KROGEND"}
    frame = pd.DataFrame(rows).rename(columns=names)
    frame["KRWMAX"] = frame["KRWMAX"].clip(lower=frame["KRWEND"])
    cols = [
        "SATNUM",
        "CASE",
        "SWIRR",
        "SWL",
        "SWCR",
        "SORW",
        "KRWEND",
        "KRWMAX",
        "KROEND",
        "LW",
        "EW",
        "TW",
        "LOW",
        "EOW",
        "TOW",
        "SGCR",
        "SORG",
        "KRGEND",
        "LG",
        "EG",
        "TG",
        "LOG",
        "EOG",
        "TOG",
        "A",
        "B",
        "PORO_REF",
        "PERM_REF",
        "DRHO",
    ]
    return frame[cols]


def interpolated_tables(rec_csv: Path, wo, go=None, h=0.01):
    """SWOF and SGOF strings from the recommendation at one parameter or a list per SATNUM."""
    from pyscal.factory import create_scal_recommendation_list, load_relperm_df

    recs = create_scal_recommendation_list(load_relperm_df(str(rec_csv)), h=h)
    tables = recs.interpolate(wo, go, h=h)
    return tables.SWOF(), tables.SGOF()


def table_frame(text: str, ntsfun: int):
    """res2df satfunc frame of SWOF/SGOF text."""
    from res2df import satfunc

    return satfunc.df(text, ntsfun=ntsfun)


def max_errors(deck, model) -> dict:
    """Max abs difference per SATNUM and column, the model interpolated on the deck saturation nodes."""
    import numpy as np

    out = {}
    for (keyword, satnum), d in deck.groupby(["KEYWORD", "SATNUM"]):
        m = model[(model.KEYWORD == keyword) & (model.SATNUM == satnum)]
        s = "SW" if keyword == "SWOF" else "SG"
        cols = ["KRW", "KROW", "PCOW"] if keyword == "SWOF" else ["KRG", "KROG", "PCOG"]
        for c in cols:
            err = np.abs(np.interp(d[s], m[s], m[c]) - d[c])
            if c.startswith("PC"):
                err = err[d[s] > d[s].min() + 1e-9]  # the swl row: pyscal clips the J-function maximum
            out[f"{keyword}:{satnum}:{c}"] = float(err.max()) if len(err) else 0.0
    return out


def power_law(deck, krw_exp: float, krow_exp: float):
    """The truth curves: the D1 power transform of drogon_params.py (top * (k/top)**exp) on the deck SWOF."""
    swof = deck[deck.KEYWORD == "SWOF"].copy()
    for col, expo in (("KRW", krw_exp), ("KROW", krow_exp)):
        top = swof.groupby("SATNUM")[col].transform("max")
        swof[col] = top * (swof[col] / top) ** expo
    return swof


def coverage(truth, low, high, tol=1e-6) -> dict:
    """Share of the truth (SW, kr) points inside the low/high envelope, per SATNUM and curve."""
    import numpy as np

    out = {}
    for satnum, t in truth.groupby("SATNUM"):
        lo = low[(low.KEYWORD == "SWOF") & (low.SATNUM == satnum)]
        hi = high[(high.KEYWORD == "SWOF") & (high.SATNUM == satnum)]
        for col in ("KRW", "KROW"):
            a = np.interp(t.SW, lo.SW, lo[col])
            b = np.interp(t.SW, hi.SW, hi[col])
            inside = (t[col] >= np.minimum(a, b) - tol) & (t[col] <= np.maximum(a, b) + tol)
            out[f"{satnum}:{col}"] = float(inside.mean())
    return out


def fit_all(sattab: Path, headers: dict[int, dict]) -> tuple[dict[int, dict], list]:
    """LET fit per SATNUM with the header endpoints fixed. Returns base rows and the fit records."""
    sys.path.insert(0, str(REPO / "skills/scal-uncertainty/scripts"))
    from satfunc_fit import fit_table

    deck = table_frame(sattab.read_text(), len(headers))
    rows, fits = {}, []
    for satnum, h in sorted(headers.items()):
        wo = deck[(deck.SATNUM == satnum) & (deck.KEYWORD == "SWOF")].dropna(axis=1, how="all")
        go = deck[(deck.SATNUM == satnum) & (deck.KEYWORD == "SGOF")].dropna(axis=1, how="all")
        fw = fit_table(
            wo, "let", swl=h["swl"], swcr=h["swcr"], sorw=h["sorw"], krwend=h["krwend"], krowend=1.0
        )
        fg = fit_table(
            go, "let", swl=h["swl"], sgcr=h["sgcr"], sorg=h["sorg"], krgend=h["krgend"], krogend=1.0
        )
        fits += [dict(satnum=satnum, keyword="SWOF", **fw), dict(satnum=satnum, keyword="SGOF", **fg)]
        rw, rg = fw["recommendation"], fg["recommendation"]
        rows[satnum] = {
            "SATNUM": satnum,
            "facies": h["facies"],
            "SWIRR": h["swirr"],
            "SWL": h["swl"],
            "SWCR": h["swcr"],
            "SORW": h["sorw"],
            "KRWEND": h["krwend"],
            "KRWMAX": h["krwmax"],
            "KROEND": 1.0,
            "LW": rw["LW"],
            "EW": rw["EW"],
            "TW": rw["TW"],
            "LO": rw["LO"],
            "EO": rw["EO"],
            "TO": rw["TO"],
            "SGCR": h["sgcr"],
            "SORG": h["sorg"],
            "KRGEND": h["krgend"],
            "KROGEND": 1.0,
            "LG": rg["LG"],
            "EG": rg["EG"],
            "TG": rg["TG"],
            "LOG": rg["LOG"],
            "EOG": rg["EOG"],
            "TOG": rg["TOG"],
            "A": h["a"],
            "B": h["b"],
            "PORO_REF": h["poro_ref"],
            "PERM_REF": h["perm_ref"],
            "DRHO": h["drho"],
        }
    return rows, fits


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-sattab", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--truth-krw-exp", type=float, default=1.25)
    parser.add_argument("--truth-krow-exp", type=float, default=0.85)
    parser.add_argument("--tol", type=float, default=1e-3, help="max kr error of the base against the deck")
    args = parser.parse_args(argv)
    try:
        import pandas as pd

        text = args.deck_sattab.read_text()
        headers = parse_headers(text)
        rows, fits = fit_all(args.deck_sattab, headers)
    except (ImportError, OSError, ValueError) as error:
        print(f"make_scal_rec: {error}", file=sys.stderr)
        return 2
    groups = group_satnums(rows)
    recs = [case_row(rows[s], case) for s in sorted(rows) for case in ("low", "base", "high")]
    args.out.mkdir(parents=True, exist_ok=True)
    rec_csv = args.out / "drogon_scalrec.csv"
    pyscal_frame(recs).to_csv(rec_csv, index=False, float_format="%.6g")
    pd.DataFrame(
        {
            "SATNUM": sorted(groups),
            "GROUP": [groups[s] for s in sorted(groups)],
            "FACIES": [rows[s]["facies"] for s in sorted(groups)],
        }
    ).to_csv(args.out / "satnum_groups.csv", index=False)

    n = len(rows)
    deck = table_frame(text, n)
    tabs = {p: interpolated_tables(rec_csv, p, p) for p in (-1.0, 0.0, 1.0)}
    frames = {p: pd.concat([table_frame(w, n), table_frame(g, n)]) for p, (w, g) in tabs.items()}
    errors = max_errors(deck, frames[0.0])
    kr_error = max(v for k, v in errors.items() if ":PC" not in k)
    pc_error = max(v for k, v in errors.items() if ":PC" in k)
    truth = power_law(deck, args.truth_krw_exp, args.truth_krow_exp)
    cov = coverage(truth, frames[-1.0], frames[1.0])
    rows_per_table = int(frames[0.0].groupby(["KEYWORD", "SATNUM"]).size().max())
    report = {
        "assumed_range": ASSUMED_RANGE,
        "groups": {g: [s for s in sorted(groups) if groups[s] == g] for g in dict.fromkeys(groups.values())},
        "fits": [{"satnum": f["satnum"], "keyword": f["keyword"], "curves": f["curves"]} for f in fits],
        "base_vs_deck_max_kr_error": kr_error,
        "base_vs_deck_max_pc_error_above_swl": pc_error,
        "max_rows_per_table": rows_per_table,
        "truth_coverage": cov,
        "truth_coverage_min": min(cov.values()),
        "truth_coverage_mean": sum(cov.values()) / len(cov),
    }
    (args.out / "scal_report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "groups",
                    "base_vs_deck_max_kr_error",
                    "base_vs_deck_max_pc_error_above_swl",
                    "max_rows_per_table",
                    "truth_coverage_min",
                    "truth_coverage_mean",
                )
            },
            indent=1,
        )
    )
    return 0 if kr_error <= args.tol else 1


if __name__ == "__main__":
    raise SystemExit(main())
