#!/usr/bin/env python3
"""Make material-balance input from one simulator case. Read-only. Needs res2df.

It writes three files to --outdir:
    history.csv     DATE, P, NP, GP, WP, WI, GI from the summary (FPR and field cumulatives)
    pvt.csv         P, BO, RS, BG, BW along the depletion path of the initial oil (or P, BG, BW for gas)
    reference.json  the in-place volumes of the simulator, Pi, Rsi, Pb, Swi, cw, cf, and the aquifer influx

Use it to check a simulation model against the material balance: does the tank model give the same
in-place volume from the production data? A large difference shows energy that the tank model lacks
(aquifer, gas cap) or a problem in the model or the pressure average.

Rules:
- FPR is the hydrocarbon pore volume weighted pressure. The tank model uses it as the average pressure.
- The summary has no row at time 0. The script extrapolates P and the date to zero production
  from the first two rows. The first row of history.csv is that point.
- Oil: Rsi is FGIP/FOIP at the first row. This is correct only without an initial gas cap.
- PVT is interpolated as the simulator does: 1/B is linear in pressure. The table is dense (201 rows).
- Only PVT region --pvtnum is used. A model with more regions gives a field average that one table cannot match.

Usage: extract_case.py CASE.DATA --outdir DIR [--pvtnum 1]
Exit code: 0 = done, 1 = input problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import warnings
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from res2df import ResdataFiles, pvt, summary

warnings.filterwarnings("ignore", category=DeprecationWarning)

GRID_ROWS = 201


class InputError(Exception):
    pass


def inverse_interp(xs: list[float], bs: list[float], x: float) -> float:
    """Interpolate B so that 1/B is linear in x. Extrapolate with the end segments."""
    if len(xs) == 1:
        return bs[0]
    i = 1
    while i < len(xs) - 1 and x > xs[i]:
        i += 1
    x0, x1 = xs[i - 1], xs[i]
    w = (x - x0) / (x1 - x0)
    inv = 1 / bs[i - 1] + w * (1 / bs[i] - 1 / bs[i - 1])
    return 1 / inv


def linear(xs: list[float], ys: list[float], x: float) -> float:
    if len(xs) == 1:
        return ys[0]
    i = 1
    while i < len(xs) - 1 and x > xs[i]:
        i += 1
    w = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return ys[i - 1] + w * (ys[i] - ys[i - 1])


def oil_table(pvto, rsi: float, p_min: float, p_max: float) -> tuple[list[dict], float]:
    """Bo and Rs along the depletion path of the oil with Rsi. Return (rows, bubble point)."""
    records = []
    for rs, group in pvto.groupby("RS", sort=True):
        group = group.sort_values("PRESSURE")
        records.append((float(rs), group["PRESSURE"].tolist(), group["VOLUMEFACTOR"].tolist()))
    rs_sat = [r[0] for r in records]
    p_sat = [r[1][0] for r in records]
    bo_sat = [r[2][0] for r in records]
    if not rs_sat[0] <= rsi <= rs_sat[-1]:
        raise InputError(f"Rsi {rsi:g} is outside the RS range of PVTO ({rs_sat[0]:g} to {rs_sat[-1]:g})")
    pb = linear(rs_sat, p_sat, rsi)
    bob = inverse_interp(rs_sat, bo_sat, rsi)
    # Undersaturated branch: the record with Rsi, or the nearest record above it with more than one row.
    shape = next((r for r in records if abs(r[0] - rsi) < 1e-9 and len(r[1]) > 1), None)
    if shape is None:
        shape = next((r for r in records if r[0] >= rsi and len(r[1]) > 1), None)
    if shape is None:
        raise InputError("PVTO has no undersaturated rows at or above Rsi")
    _, shape_p, shape_bo = shape

    def bo_under(p: float) -> float:
        # Shift the record so that its saturated point is at Pb, and scale it to Bob.
        offset = p - pb
        return bob * inverse_interp(shape_p, shape_bo, shape_p[0] + offset) / shape_bo[0]

    rows = []
    for k in range(GRID_ROWS):
        p = p_min + (p_max - p_min) * k / (GRID_ROWS - 1)
        if p <= pb:
            rows.append({"P": p, "BO": inverse_interp(p_sat, bo_sat, p), "RS": linear(p_sat, rs_sat, p)})
        else:
            rows.append({"P": p, "BO": bo_under(p), "RS": rsi})
    return rows, pb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("deck", type=Path)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--pvtnum", type=int, default=1)
    args = parser.parse_args(argv)
    if not args.deck.is_file():
        print(f"ERROR: not a file: {args.deck}", file=sys.stderr)
        return 2
    output = args.outdir.resolve()
    source = args.deck.resolve()
    if output == source or output in source.parents or source in output.parents:
        print("ERROR: --outdir overlaps the deck file's directory", file=sys.stderr)
        return 2
    if not args.deck.with_suffix(".UNSMRY").is_file():
        print(
            f"ERROR: no summary file {args.deck.with_suffix('.UNSMRY').name}. Run the case first",
            file=sys.stderr,
        )
        return 1
    try:
        return extract(args)
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def extract(args) -> int:
    files = ResdataFiles(str(args.deck))
    smry = summary.df(files, time_index="raw")
    if "FPR" not in smry:
        raise InputError("the summary has no FPR. Add FPR to the SUMMARY section")
    table = pvt.df(files)
    table = table[table["PVTNUM"] == args.pvtnum]
    keywords = set(table["KEYWORD"])
    oil = "PVTO" in keywords
    if not oil and "PVDG" not in keywords:
        raise InputError(
            "the deck has neither PVTO nor PVDG. Only live oil (PVTO) and dry gas (PVDG) are supported"
        )
    needed = ["FOPT", "FOIP"] if oil else ["FGPT", "FGIP"]
    missing = [k for k in [*needed, "FWIP"] if k not in smry]
    if missing:
        raise InputError(f"the summary has no {', '.join(missing)}")
    if len(smry) < 3:
        raise InputError("the summary has fewer than 3 rows")

    def col(name):
        return [float(v) for v in smry[name]] if name in smry else [0.0] * len(smry)

    p, fopt, fgpt, fwpt = col("FPR"), col("FOPT"), col("FGPT"), col("FWPT")
    fwit, fgit = col("FWIT"), col("FGIT")
    main_cum = fopt if oil else fgpt
    dates = list(smry.index)

    # Extrapolate to zero production from the first two rows.
    c0, c1 = main_cum[0], main_cum[1]
    if c1 <= c0:
        raise InputError("production does not increase between the first two rows")
    share = c0 / (c1 - c0)
    pi = p[0] + (p[0] - p[1]) * share
    date0 = dates[0] - (dates[1] - dates[0]) * share

    pvdg = table[table["KEYWORD"] == "PVDG"].sort_values("PRESSURE")
    pvtw = table[table["KEYWORD"] == "PVTW"]
    rock = table[table["KEYWORD"] == "ROCK"]
    if pvdg.empty or pvtw.empty or rock.empty:
        raise InputError("the deck needs PVDG, PVTW, and ROCK for this tool")
    pw_ref, bw_ref, cw = (float(pvtw.iloc[0][k]) for k in ("PRESSURE", "VOLUMEFACTOR", "COMPRESSIBILITY"))
    cf = float(rock.iloc[0]["COMPRESSIBILITY"])
    pg, bg = pvdg["PRESSURE"].tolist(), pvdg["VOLUMEFACTOR"].tolist()

    def bw_at(pressure: float) -> float:
        x = cw * (pressure - pw_ref)
        return bw_ref / (1 + x + x * x / 2)

    p_min, p_max = min(min(p), min(pg)), max(pi, max(pg))
    reference: dict = {"deck": args.deck.name, "fluid": "oil" if oil else "gas", "pi": pi, "cw": cw, "cf": cf}
    if oil:
        foip, fgip = col("FOIP"), col("FGIP")
        n_sim = foip[0] + fopt[0]
        rsi = fgip[0] / foip[0] if foip[0] else 0.0
        pvto = table[table["KEYWORD"] == "PVTO"]
        rows, pb = oil_table(pvto, rsi, p_min, p_max)
        boi = next(r["BO"] for r in rows if r["P"] >= pi - 1e-9) if rows else None
        boi = linear([r["P"] for r in rows], [r["BO"] for r in rows], pi)
        hc_volume = foip[0] * boi
        reference |= {"stoiip_simulator": n_sim, "rsi": rsi, "pb": pb, "boi": boi}
    else:
        fgip = col("FGIP")
        g_sim = fgip[0] + fgpt[0] - fgit[0]
        rows = [{"P": p_min + (p_max - p_min) * k / (GRID_ROWS - 1)} for k in range(GRID_ROWS)]
        bgi = inverse_interp(pg, bg, pi)
        hc_volume = fgip[0] * bgi
        reference |= {"giip_simulator": g_sim, "bgi": bgi}
    for row in rows:
        row["BG"] = inverse_interp(pg, bg, row["P"])
        row["BW"] = bw_at(row["P"])
    water = col("FWIP")[0] * bw_at(pi)
    reference["swi"] = water / (water + hc_volume)
    aquifer = [k for k in smry.columns if k.startswith("AAQT")]
    if aquifer:
        reference["aquifer_influx_last_row"] = float(sum(smry[k].iloc[-1] for k in aquifer))
    reference["notes"] = [
        "Pi and the first date come from an extrapolation of the first two summary rows to zero production",
        "Rsi is FGIP/FOIP at the first row. It is correct only without an initial gas cap" if oil else "",
        f"PVT region {args.pvtnum} only",
    ]

    args.outdir.mkdir(parents=True, exist_ok=True)
    with (args.outdir / "history.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["DATE", "P", "NP", "GP", "WP", "WI", "GI"])
        writer.writerow([date0.date().isoformat(), f"{pi:.6g}", 0, 0, 0, 0, 0])
        for i, date in enumerate(dates):
            writer.writerow(
                [
                    date.date().isoformat(),
                    f"{p[i]:.6g}",
                    f"{fopt[i]:.8g}",
                    f"{fgpt[i]:.8g}",
                    f"{fwpt[i]:.8g}",
                    f"{fwit[i]:.8g}",
                    f"{fgit[i]:.8g}",
                ]
            )
    keys = ["P", "BO", "RS", "BG", "BW"] if oil else ["P", "BG", "BW"]
    with (args.outdir / "pvt.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(keys)
        for row in rows:
            writer.writerow([f"{row[k]:.8g}" for k in keys])
    (args.outdir / "reference.json").write_text(json.dumps(reference, indent=2))
    fluid = "oil" if oil else "gas"
    print(json.dumps(reference, indent=2))
    print(
        f"Next: mbal.py --fluid {fluid} --history {args.outdir}/history.csv --pvt {args.outdir}/pvt.csv "
        f"--swi {reference['swi']:.4f} --cw {cw:g} --cf {cf:g} --aquifer all"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
