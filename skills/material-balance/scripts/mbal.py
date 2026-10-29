#!/usr/bin/env python3
"""Material balance of an oil or gas reservoir (tank model). Read-only. Standard library only.

Oil (Havlena and Odeh):  F = N (Eo + m Eg + Efw) + We
    F   = Np (Bo + (Rp - Rs) Bg) + Wp Bw - Wi Bw - Gi Bg
    Eo  = (Bo - Boi) + (Rsi - Rs) Bg
    Eg  = Boi (Bg / Bgi - 1)
    Efw = (1 + m) Boi (cw Swi + cf) / (1 - Swi) (Pi - P)
Gas (dry gas):  F = G (Eg + Efw) + We
    F   = Gp Bg + Wp Bw - Wi Bw
    Eg  = Bg - Bgi
    Efw = Bgi (cw Swi + cf) / (1 - Swi) (Pi - P)

Aquifer models for the fit (We):
    none         volumetric reservoir, We = 0
    pot          We = K (Pi - P). K is the aquifer volume times its total compressibility
    schilthuis   steady state, We = C * integral of (Pi - P) dt. Needs DATE
    fetkovich    finite aquifer with a productivity index. K = ct W (aquifer volume times compressibility)
                 and the time constant tau = K / J. Needs DATE. The pot model is the limit tau -> 0.
                 The steady-state model is the limit tau -> infinity
The fit is linear least squares through the origin. It gives N (or G), the aquifer constant, and R2.
For fetkovich, We is linear in K when tau is fixed. The script searches tau (log scale) and fits N and K
by least squares for each tau. It keeps the tau with the smallest residual and N > 0, K > 0.

Usage:
    mbal.py --fluid oil --history history.csv --pvt pvt.csv --swi 0.2 --cw 3e-6 --cf 4e-6 [--m 0]
            [--pi 3500] [--aquifer none|pot|schilthuis|fetkovich|all] [--outdir DIR] [--json]

history.csv columns: P, NP (oil), GP, WP. Optional: DATE (YYYY-MM-DD), WI, GI.
pvt.csv columns: P, BG, BW. Oil also needs BO and RS. Values along the depletion path of the reservoir fluid.
Units must agree. FIELD as in a simulator deck: psia, stb, Mscf, rb/stb, Mscf/stb, rb/Mscf, 1/psi.
The script checks that RS, BG, and GP/NP agree in magnitude. It does not convert units.

Exit code: 0 = done, 1 = input problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path

MIN_POINTS = 3


class InputError(Exception):
    pass


def read_table(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = [
            {k.strip().upper(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(handle)
        ]
    if not rows:
        raise InputError(f"{path} has no rows")
    return rows


def column(rows: list[dict[str, str]], name: str, path: Path, required: bool = True) -> list[float]:
    if name not in rows[0]:
        if required:
            raise InputError(f"{path} has no column {name}")
        return [0.0] * len(rows)
    values = []
    for number, row in enumerate(rows, start=2):
        text = row.get(name, "")
        try:
            values.append(float(text) if text else (math.nan if required else 0.0))
        except ValueError as error:
            raise InputError(f"{path} line {number}: {name} '{text}' is not a number") from error
        if not math.isfinite(values[-1]):
            raise InputError(f"{path} line {number}: {name} must be finite")
    return values


class Pvt:
    """Linear interpolation in pressure. Outside the table range is an error."""

    def __init__(self, path: Path, names: list[str]):
        rows = read_table(path)
        pressure = column(rows, "P", path)
        order = sorted(range(len(pressure)), key=pressure.__getitem__)
        self.p = [pressure[i] for i in order]
        if any(b <= a for a, b in zip(self.p, self.p[1:], strict=False)):
            raise InputError(f"{path}: pressures must be different from each other")
        self.columns = {}
        for name in names:
            values = column(rows, name, path)
            self.columns[name] = [values[i] for i in order]
        self.path = path

    def __call__(self, name: str, pressure: float) -> float:
        p, v = self.p, self.columns[name]
        if not p[0] - 1e-9 <= pressure <= p[-1] + 1e-9:
            raise InputError(
                f"{self.path}: pressure {pressure:g} is outside the table ({p[0]:g} to {p[-1]:g})"
            )
        for i in range(1, len(p)):
            if pressure <= p[i]:
                w = (pressure - p[i - 1]) / (p[i] - p[i - 1])
                return v[i - 1] + w * (v[i] - v[i - 1])
        return v[-1]


def least_squares(columns: list[list[float]], y: list[float]) -> tuple[list[float], float]:
    """Least squares through the origin for 1 or 2 columns. Return (coefficients, R2 about zero)."""
    if len(columns) == 1:
        (x,) = columns
        sxx = sum(a * a for a in x)
        if sxx == 0:
            raise InputError("the expansion term is zero at all points")
        coef = [sum(a * b for a, b in zip(x, y, strict=True)) / sxx]
    else:
        x1, x2 = columns
        a11 = sum(a * a for a in x1)
        a12 = sum(a * b for a, b in zip(x1, x2, strict=True))
        a22 = sum(b * b for b in x2)
        b1 = sum(a * c for a, c in zip(x1, y, strict=True))
        b2 = sum(b * c for b, c in zip(x2, y, strict=True))
        det = a11 * a22 - a12 * a12
        if abs(det) <= 1e-12 * max(a11 * a22, 1e-300):
            raise InputError("the two fit terms are not independent. Use fewer unknowns")
        coef = [(b1 * a22 - b2 * a12) / det, (a11 * b2 - a12 * b1) / det]
    predicted = [sum(c * col[i] for c, col in zip(coef, columns, strict=True)) for i in range(len(y))]
    ss_res = sum((a - b) ** 2 for a, b in zip(y, predicted, strict=True))
    ss_tot = sum(a * a for a in y)
    return coef, (1 - ss_res / ss_tot) if ss_tot else math.nan


def days_since_start(dates: list[str]) -> list[float]:
    try:
        parsed = [dt.date.fromisoformat(d) for d in dates]
    except ValueError as error:
        raise InputError(f"DATE must be YYYY-MM-DD: {error}") from error
    if any(b <= a for a, b in zip(parsed, parsed[1:], strict=False)):
        raise InputError("DATE must be strictly increasing")
    return [float((d - parsed[0]).days) for d in parsed]


def schilthuis_integral(times: list[float], dp: list[float]) -> list[float]:
    """Trapezoid integral of (Pi - P) over time. The first row is time 0 with dp 0 unless the data says else."""
    out, total = [], 0.0
    prev_t, prev_dp = 0.0, 0.0
    for t, d in zip(times, dp, strict=True):
        total += 0.5 * (d + prev_dp) * (t - prev_t)
        out.append(total)
        prev_t, prev_dp = t, d
    return out


def fetkovich_unit_influx(times: list[float], pressures: list[float], pi: float, tau: float) -> list[float]:
    """We / K for each row (Fetkovich 1971, stepwise). times start after time 0. P at time 0 is Pi."""
    out, w, prev_t, prev_p = [], 0.0, 0.0, pi
    for t, pressure in zip(times, pressures, strict=True):
        mean_p = 0.5 * (prev_p + pressure)
        aquifer_p = pi - w
        w += (aquifer_p - mean_p) * (1 - math.exp(-(t - prev_t) / tau))
        out.append(w)
        prev_t, prev_p = t, pressure
    return out


def solve(
    et: list[float], y: list[float], aux: list[float] | None, fixed: float | None
) -> tuple[float, float, float]:
    """Fit F = N Et + K aux. With `fixed`, N is that value and only K is fitted. Return (N, K, R2)."""
    if fixed is None:
        coef, r2 = least_squares([et, aux] if aux is not None else [et], y)
        return coef[0], (coef[1] if aux is not None else 0.0), r2
    rest = [a - fixed * b for a, b in zip(y, et, strict=True)]
    k = least_squares([aux], rest)[0][0] if aux is not None else 0.0
    predicted = [fixed * b + (k * c if aux is not None else 0.0) for b, c in zip(et, aux or et, strict=True)]
    ss_res = sum((a - b) ** 2 for a, b in zip(y, predicted, strict=True))
    return fixed, k, 1 - ss_res / sum(a * a for a in y)


def fit_fetkovich(et, y, times, pressures, pi, fixed):
    """Search tau. Return a fit dict with N, K, tau, R2, and We, or None."""
    span = max(times[-1], 1.0)
    tried: list[tuple[float, float, float, float, list[float]]] = []

    def attempt(log_tau: float) -> float:
        tau = span * 10**log_tau
        w = fetkovich_unit_influx(times, pressures, pi, tau)
        try:
            n, k, r2 = solve(et, y, w, fixed)
        except InputError:
            return -math.inf
        if n <= 0 or k <= 0:
            return -math.inf
        tried.append((r2, n, k, tau, [k * x for x in w]))
        return r2

    # Coarse search from span/1000 to span*1000, then golden-section refinement around the best point.
    scores = [(g, attempt(g)) for g in (-3 + 6 * i / 120 for i in range(121))]
    g_best, score = max(scores, key=lambda item: item[1])
    if score == -math.inf:
        return None
    low, high = g_best - 0.05, g_best + 0.05
    ratio = (5**0.5 - 1) / 2
    for _ in range(40):
        a, b = high - ratio * (high - low), low + ratio * (high - low)
        if attempt(a) >= attempt(b):
            high = b
        else:
            low = a
    r2, n, k, tau, we = max(tried, key=lambda item: item[0])
    return {"in_place": n, "aquifer_constant": k, "time_constant_days": tau, "r2": r2, "we": we}


def trend(points: list[tuple[float, float]]) -> float | None:
    """Ratio of the mean of y in the last third to the mean of y in the first third, minus 1."""
    if len(points) < 6:
        return None
    third = len(points) // 3
    early = sum(y for _, y in points[:third]) / third
    late = sum(y for _, y in points[-third:]) / third
    return late / early - 1 if early else None


def compute(args) -> dict:
    for name in ("swi", "cw", "cf", "m", "pi", "in_place", "data_error", "trend_limit"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value < 0):
            raise InputError(f"--{name.replace('_', '-')} must be finite and nonnegative")
    if args.in_place is not None and args.in_place <= 0:
        raise InputError("--in-place must be positive")
    if args.data_error <= 0:
        raise InputError("--data-error must be positive")
    hist_rows = read_table(args.history)
    oil = args.fluid == "oil"
    pvt = Pvt(args.pvt, ["BO", "RS", "BG", "BW"] if oil else ["BG", "BW"])
    p = column(hist_rows, "P", args.history)
    np_ = column(hist_rows, "NP", args.history) if oil else [0.0] * len(p)
    gp = column(hist_rows, "GP", args.history)
    wp = column(hist_rows, "WP", args.history, required=False)
    wi = column(hist_rows, "WI", args.history, required=False)
    gi = column(hist_rows, "GI", args.history, required=False)
    dates = [r.get("DATE", "") for r in hist_rows] if "DATE" in hist_rows[0] else None

    pi = args.pi if args.pi is not None else p[0]
    if not 0 <= args.swi < 1:
        raise InputError("--swi must be in the range 0 to 1")
    warnings: list[str] = []
    bgi, bwi = pvt("BG", pi), pvt("BW", pi)
    boi = pvt("BO", pi) if oil else None
    rsi = pvt("RS", pi) if oil else None

    # Unit checks. They catch scf against Mscf, and rcf against rb.
    if oil:
        if not 0.9 <= boi <= 4.0:
            raise InputError(f"BO at Pi is {boi:g}. Expected 0.9 to 4 (rb/stb or rm3/sm3)")
        if not 1e-3 <= rsi * bgi <= 20:
            raise InputError(
                f"RS*BG at Pi is {rsi * bgi:g} reservoir volume per stock-tank volume. Expected 0.001 to 20. "
                "The units of RS and BG do not agree (for example scf/stb with rb/Mscf)"
            )
        first = next((i for i, n in enumerate(np_) if n > 0), None)
        if first is not None and rsi > 0:
            rp_first = gp[first] / np_[first]
            if not rsi / 5 <= rp_first <= rsi * 50:
                raise InputError(
                    f"GP/NP at the first production row is {rp_first:g}, but RS at Pi is {rsi:g}. "
                    "The units of GP and RS do not agree"
                )
    if any(b > a + 1e-6 for a, b in zip(p, p[1:], strict=False)):
        warnings.append("pressure increases between some rows. Check injection or the pressure data")

    rows = []
    for i, pressure in enumerate(p):
        produced = np_[i] if oil else gp[i]
        if produced <= 0 or pressure >= pi:
            continue
        bg, bw = pvt("BG", pressure), pvt("BW", pressure)
        dp = pi - pressure
        if oil:
            bo, rs = pvt("BO", pressure), pvt("RS", pressure)
            rp = gp[i] / np_[i]
            f = np_[i] * (bo + (rp - rs) * bg) + (wp[i] - wi[i]) * bw - gi[i] * bg
            eo = (bo - boi) + (rsi - rs) * bg
            eg = boi * (bg / bgi - 1)
            efw = (1 + args.m) * boi * (args.cw * args.swi + args.cf) / (1 - args.swi) * dp
            et = eo + args.m * eg + efw
            row = {"P": pressure, "NP": np_[i], "GP": gp[i], "WP": wp[i], "F": f, "EO": eo, "EG": eg}
        else:
            f = gp[i] * bg + (wp[i] - wi[i]) * bw
            eg = bg - bgi
            efw = bgi * (args.cw * args.swi + args.cf) / (1 - args.swi) * dp
            et = eg + efw
            row = {"P": pressure, "GP": gp[i], "WP": wp[i], "F": f, "EG": eg, "BGI_OVER_BG": bgi / bg}
        row |= {"EFW": efw, "ET": et, "DP": dp, "F_OVER_ET": f / et if et > 0 else math.nan}
        if dates:
            row["DATE"] = dates[i]
        rows.append(row)
    if len(rows) < MIN_POINTS:
        raise InputError(f"only {len(rows)} rows have production and a pressure below Pi. Need {MIN_POINTS}")
    if any(r["ET"] <= 0 for r in rows):
        raise InputError("Et is not positive at some rows. Check PVT, Pi, and the pressure data")

    y = [r["F"] for r in rows]
    et = [r["ET"] for r in rows]
    dp = [r["DP"] for r in rows]
    fits: dict[str, dict] = {}
    models = ["none", "pot", "schilthuis", "fetkovich"] if args.aquifer == "all" else [args.aquifer]
    kept_index = [i for i, pr in enumerate(p) if (np_[i] if oil else gp[i]) > 0 and pr < pi]
    times = days_since_start(dates) if dates else None
    kept_times = [times[i] for i in kept_index] if times else None
    integral = schilthuis_integral(kept_times, dp) if kept_times else None

    def fit_model(model: str, fixed: float | None) -> dict | None:
        if model == "fetkovich":
            return fit_fetkovich(et, y, kept_times, [p[i] for i in kept_index], pi, fixed)
        aux = {"none": None, "pot": dp, "schilthuis": integral}[model]
        try:
            n, k, r2 = solve(et, y, aux, fixed)
        except InputError:
            return None
        return {
            "in_place": n,
            "aquifer_constant": k,
            "r2": r2,
            "we": [k * a for a in aux] if aux else [0.0] * len(y),
        }

    for model in models:
        if model in ("schilthuis", "fetkovich") and kept_times is None:
            if args.aquifer == "all":
                continue
            raise InputError(f"the {model} aquifer needs a DATE column")
        fit = fit_model(model, args.in_place)
        if fit is None:
            warnings.append(f"{model}: no fit gives a positive in-place volume and aquifer constant")
            continue
        fit["relative_residual"] = math.sqrt(max(0.0, 1 - fit["r2"]))
        if args.in_place is None and fit["in_place"] > 0:
            # Profile: refit with N fixed at other values. Keep the N values whose RMS residual,
            # as a share of the RMS of F, is within the data error.
            inside = []
            # 0.25 to 4 times the best value on a log scale, and 0.9 to 1.1 in steps of 0.5 %.
            factors = sorted(
                {10 ** (-0.6 + 1.2 * j / 60) for j in range(61)} | {0.9 + 0.005 * j for j in range(41)}
            )
            for factor in factors:
                trial = fit["in_place"] * factor
                other = fit_model(model, trial)
                if other is not None and math.sqrt(max(0.0, 1 - other["r2"])) <= args.data_error:
                    inside.append(trial)
            if inside:
                low, high = min(inside), max(inside)
                fit["in_place_range_within_data_error"] = [low, high]
                if high / low > 1.2:
                    warnings.append(
                        f"{model}: {'N' if oil else 'G'} is not determined by this data. Values from {low:.4g} to {high:.4g} fit within "
                        f"the data error ({args.data_error:.0%} of F). Fix it with --in-place, for example from "
                        "the volumetrics, and fit only the aquifer"
                    )
            else:
                warnings.append(
                    f"{model}: even the best fit misses F by more than the data error. The model is wrong"
                )
        fits[model] = fit

    for fit in fits.values():
        n = fit["in_place"]
        last = rows[-1]
        if oil:
            parts = {
                "depletion": n * last["EO"],
                "gas_cap": n * args.m * last["EG"],
                "compaction_and_water": n * last["EFW"],
                "aquifer": fit["we"][-1],
            }
        else:
            parts = {
                "gas_expansion": n * last["EG"],
                "compaction_and_water": n * last["EFW"],
                "aquifer": fit["we"][-1],
            }
        fit["drive_indices_last_row"] = {k: v / last["F"] for k, v in parts.items()}
        fit["we_last_row"] = fit.pop("we")[-1]
    for model, fit in fits.items():
        if fit["in_place"] <= 0:
            warnings.append(
                f"{model}: the in-place volume is negative or zero. That model does not fit the data"
            )
        # A negative aquifer term smaller than 1 % of F is noise. A larger one means the model is wrong.
        if fit["drive_indices_last_row"]["aquifer"] < -0.01:
            warnings.append(
                f"{model}: the aquifer term is negative. That aquifer model does not fit the data"
            )

    diagnostics = {"f_over_et_trend": trend([(r["F"], r["F_OVER_ET"]) for r in rows])}
    t = diagnostics["f_over_et_trend"]
    if t is None:
        diagnostics["reading"] = "fewer than 6 points. No trend"
    elif t > args.trend_limit:
        diagnostics["reading"] = (
            f"F/Et increases by {t:.0%} from the first third to the last third of the points. "
            "The model misses energy: an aquifer, a larger gas cap than m, or compaction"
        )
    elif t < -args.trend_limit:
        diagnostics["reading"] = (
            f"F/Et decreases by {-t:.0%}. The model has too much energy (m too large), "
            "or the pressure or PVT data has an error, or the well data misses production"
        )
    else:
        diagnostics["reading"] = (
            f"F/Et changes by {t:+.0%}. A flat trend agrees with a volumetric reservoir"
            + (" with the given m" if oil else "")
        )
    if not oil:
        # Straight line of Bgi/Bg (p/z for a dry gas) against Gp. Valid without aquifer and compaction.
        x = [r["GP"] for r in rows]
        ys = [1 - r["BGI_OVER_BG"] for r in rows]
        (slope,), r2 = least_squares([x], ys)
        diagnostics["pz_line_giip"] = 1 / slope if slope > 0 else None
        diagnostics["pz_line_r2"] = r2

    return {
        "fluid": args.fluid,
        "inputs": {
            "history": str(args.history),
            "pvt": str(args.pvt),
            "pi": pi,
            "swi": args.swi,
            "cw": args.cw,
            "cf": args.cf,
            "m": args.m if oil else None,
            "boi": boi,
            "rsi": rsi,
            "bgi": bgi,
            "bwi": bwi,
            "points_used": len(rows),
            "in_place_fixed": args.in_place,
            "data_error": args.data_error,
        },
        "fits": fits,
        "diagnostics": diagnostics,
        "warnings": warnings,
        "rows": rows,
    }


def report(result: dict) -> str:
    oil = result["fluid"] == "oil"
    name = "N (STOIIP)" if oil else "G (GIIP)"
    inp = result["inputs"]
    lines = [
        f"Material balance, {result['fluid']}. {inp['points_used']} points. Pi {inp['pi']:g}.",
        f"Swi {inp['swi']:g}, cw {inp['cw']:g}, cf {inp['cf']:g}" + (f", m {inp['m']:g}" if oil else ""),
        "",
        f"{'aquifer':<12} {name:>16} {'aquifer const':>14} {'R2':>8}  drive indices at the last row",
    ]
    for model, fit in result["fits"].items():
        drives = ", ".join(f"{k} {v:.2f}" for k, v in fit["drive_indices_last_row"].items())
        extra = []
        if "time_constant_days" in fit:
            extra.append(f"tau {fit['time_constant_days']:.4g} d")
        if "in_place_range_within_data_error" in fit:
            low, high = fit["in_place_range_within_data_error"]
            extra.append(f"range {low:.4g} to {high:.4g}")
        tau = f" ({'; '.join(extra)})" if extra else ""
        lines.append(
            f"{model:<12} {fit['in_place']:>16.6g} {fit['aquifer_constant']:>14.4g} {fit['r2']:>8.5f}  {drives}{tau}"
        )
    if inp["in_place_fixed"] is not None:
        lines.append(f"{name} is fixed at {inp['in_place_fixed']:g}. The fits give only the aquifer.")
    lines += ["", f"Diagnostic: {result['diagnostics']['reading']}"]
    if not oil and result["diagnostics"].get("pz_line_giip"):
        lines.append(
            f"p/z line (Bgi/Bg against Gp): G {result['diagnostics']['pz_line_giip']:.6g}, "
            f"R2 {result['diagnostics']['pz_line_r2']:.5f}. Valid only without aquifer and compaction"
        )
    lines.append(
        f"Range: the {name} values whose fit misses F by less than the data error "
        f"({inp['data_error']:.0%}). Scan limits are 0.25 and 4 times the best value."
    )
    lines += [f"WARNING: {w}" for w in result["warnings"]]
    lines.append(
        "A fit is a model of the data. A good R2 does not prove the drive mechanism. Compare the models."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fluid", choices=["oil", "gas"], required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--pvt", type=Path, required=True)
    parser.add_argument("--swi", type=float, required=True, help="Initial water saturation, fraction")
    parser.add_argument("--cw", type=float, required=True, help="Water compressibility, 1/pressure unit")
    parser.add_argument("--cf", type=float, required=True, help="Pore compressibility, 1/pressure unit")
    parser.add_argument("--m", type=float, default=0.0, help="Gas cap to oil zone volume ratio (oil only)")
    parser.add_argument(
        "--pi", type=float, default=None, help="Initial pressure. Default: P of the first row"
    )
    parser.add_argument("--aquifer", choices=["none", "pot", "schilthuis", "fetkovich", "all"], default="all")
    parser.add_argument("--trend-limit", type=float, default=0.05)
    parser.add_argument(
        "--in-place",
        type=float,
        default=None,
        help="Fix N (or G), for example from the geomodel. Fit only the aquifer",
    )
    parser.add_argument(
        "--data-error",
        type=float,
        default=0.01,
        help="Expected error of F as a share (default 0.01). Sets the N range that the data cannot tell apart",
    )
    parser.add_argument("--outdir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    for path in (args.history, args.pvt):
        if not path.is_file():
            print(f"ERROR: not a file: {path}", file=sys.stderr)
            return 2
    if args.outdir:
        output = args.outdir.resolve()
        sources = [p.resolve() for p in (args.history, args.pvt)]
        if any(output == p or output in p.parents for p in sources):
            print("ERROR: --outdir overlaps --history or --pvt's directory", file=sys.stderr)
            return 2
    try:
        result = compute(args)
        if not result["fits"]:
            raise InputError("no usable material-balance fit")
        # Check every serialized numeric value, including diagnostics and derived rows.
        json.dumps(result, allow_nan=False)
    except (InputError, ArithmeticError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.outdir:
        args.outdir.mkdir(parents=True, exist_ok=True)
        keys = list(dict.fromkeys(k for r in result["rows"] for k in r))
        with (args.outdir / "mbal_table.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            writer.writerows(result["rows"])
        (args.outdir / "mbal.json").write_text(
            json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2, allow_nan=False)
        )
    print(
        json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2, allow_nan=False)
        if args.json
        else report(result)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
