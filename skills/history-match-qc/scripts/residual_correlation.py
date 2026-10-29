#!/usr/bin/env python3
"""Temporal correlation, bias and variance of the residuals of one ensemble, per KEY. Read-only.

ERT treats the observations of an update as independent, each with its own ERROR. A time series of one
well is not independent: a model error or a correlated measurement error shows as a smooth residual.
This script tests the saved residuals for that. It does not change ERROR and it gives no verdict.

    residual_correlation.py --residuals <misfit_report outdir>/residuals.csv [--ensemble posterior] \\
        [--truth-response truth.csv] [--min-points 8] [--json]

For each KEY, in DATE order, r_t = mean over members of (SIMULATED - OBSERVED) / ERROR. Per series it
reports n, the mean of r, z = mean / (sd / sqrt(n)), the lag-1 autocorrelation rho1 (mean removed), its
Bartlett bound 1.96 / sqrt(n), n_eff = n (1 - rho1) / (1 + rho1) when rho1 > 0 (else n), the mean spread
s = mean over dates of var over members of SIMULATED / ERROR^2 (ddof 1), and mean(r^2). No p-value is computed.

The references are spread-aware. If the truth behaves like one more member, the ensemble mean misses it by
about sqrt(s) per date, and that miss can be shared by all dates of a series. So a calibrated series has
mean r with sd sqrt(1/n + s), and mean(r^2) up to 1 + 3 sqrt(2/n) + 9 s (a 3-sd shared miss). The cut-offs
are 3 sd from that model, not tuned on any case. With s = 0 they reduce to the noise-only tests.
Coded hints (exit code 0), for a series with n >= --min-points:
    HMQC-OBSERR-BIAS      |mean r| above 3 sqrt(1/n + s). The mean residual is larger than noise and spread explain.
    HMQC-OBSERR-VARIANCE  mean(r^2) above 1 + 3 sqrt(2/n) + 9 s. The scatter is larger than ERROR and spread imply.
Review item, never a hint or a finding:
    HMQC-OBSERR-AUTOCORR  rho1 above the Bartlett bound. A smooth ensemble-mean miss makes rho1 high by
                          construction, so this is a reason to look at the series, not evidence of a wrong ERROR.
The noise component (with --truth-response) has no ensemble part, so it uses s = 0.
A shorter series is reported as insufficient. It is never reported as passing.

--truth-response truth.csv (OBS and TRUTH columns, synthetic cases only) splits r into two series:
    noise         (OBSERVED - TRUTH) / ERROR
    discrepancy   mean over members of (SIMULATED - TRUTH) / ERROR
and total = discrepancy - noise. Without it the two cannot be separated, and only the total is reported.
The discrepancy is the distance of the ensemble mean from the truth response. It holds model error and
also the estimation error of the update. On real data the script never names a cause.

residuals.csv is the misfit_report.py output. The member x observation grid is read by coverage.py
(from_residuals), so a missing member, a duplicate pair or a non-finite value is an error. Every
observation also needs a KEY and an ISO DATE, and a KEY must not repeat a DATE. NumPy and pandas read the
files; --help does not need them.
Exit code: 0 = done, 1 = input problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

MIN_POINTS = 8
BARTLETT_Z = 1.96
BIAS_Z = 3.0
VARIANCE_SIGMA = 3.0
SPREAD_SHARED_MISS = 9.0  # (3 sd)^2 of a shared ensemble-mean miss, in units of the mean spread s
AUTOCORR, BIAS, VARIANCE = "HMQC-OBSERR-AUTOCORR", "HMQC-OBSERR-BIAS", "HMQC-OBSERR-VARIANCE"
REQUIRED = ("ENSEMBLE", "REAL", "OBS", "KEY", "DATE", "OBSERVED", "ERROR", "SIMULATED")
TEXT_COLUMNS = ("ENSEMBLE", "OBS", "KEY", "DATE")
NUMERIC_COLUMNS = ("REAL", "OBSERVED", "ERROR", "SIMULATED")
COMPONENTS = ("total", "noise", "discrepancy")

GUIDANCE = (
    "Do not raise ERROR to make a gate pass. "
    "A correlated or biased series calls for a documented error model or a model change."
)
LIMITERS = (
    "Observations in one series are correlated by construction (one well, one physical process), so the"
    " bounds are guides, not tests with a known error rate.",
    "z uses n. With a positive rho1 it overstates the bias. Compare n with n_eff.",
    "Dates are ordered, not spaced. rho1 treats neighbouring dates as neighbouring samples.",
)
MEASURED = (
    "Measured: n, mean r, z, rho1, n_eff, mean(r^2) and their bounds. They come from the saved residuals."
)
INFERRED = (
    "Inferred: any cause (correlated measurement error, model discrepancy, a wrong ERROR). This tool infers"
    " none. A hint says that a series behaves unlike independent noise of the stated size, not why."
    " A series without a hint is not shown to be independent."
)
NO_SEPARATION = (
    "SEPARATION: none. The tool cannot separate measurement noise from model discrepancy without"
    " --truth-response. It reports the total residual only, so a hint does not say which of the two causes it."
)
SEPARATION = (
    "SEPARATION: from the truth file. noise = (OBSERVED - TRUTH) / ERROR. discrepancy = mean over members of"
    " (SIMULATED - TRUTH) / ERROR, the distance of the ensemble mean from the truth response. It holds model"
    " error and also the estimation error of the update. total = discrepancy - noise."
    " A perfect, fully resolved model gives a discrepancy of 0, so for that component read VARIANCE as an error larger than ERROR."
)


def _module(filename):
    path = Path(__file__).resolve().with_name(filename)
    name = "_residcorr_" + path.stem + "_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[name]  # a half-loaded module must not answer the next call
            raise
    return sys.modules[name]


def series_statistics(values, min_points: int = MIN_POINTS, spread: float = 0.0) -> dict:
    """The statistics, hints and review items of one series of normalized residuals, in date order.

    spread is the mean over the dates of var over members of SIMULATED / ERROR^2 (0 for pure noise).
    """
    if min_points < 3:
        raise ValueError("min_points must be at least 3")
    spread = float(spread)
    if not math.isfinite(spread) or spread < 0:
        raise ValueError("spread must be finite and not negative")
    x = [float(v) for v in values]
    if not x or not all(math.isfinite(v) for v in x):
        raise ValueError("a series needs at least one value, and every value must be finite")
    n = len(x)
    mean = math.fsum(x) / n
    mean_square = math.fsum(v * v for v in x) / n
    out = {
        "n": n,
        "status": "insufficient",
        "mean": mean,
        "z": None,
        "rho1": None,
        "bartlett_bound": None,
        "n_eff": None,
        "mean_square": mean_square,
        "mean_square_bound": None,
        "spread": spread,
        "bias_bound": None,
        "hints": [],
        "review": [],
    }
    if n < min_points:
        return out
    out["mean_square_bound"] = 1 + VARIANCE_SIGMA * math.sqrt(2 / n) + SPREAD_SHARED_MISS * spread
    out["bias_bound"] = BIAS_Z * math.sqrt(1 / n + spread)
    deviation = [v - mean for v in x]
    spread_sq = math.fsum(d * d for d in deviation)
    sd = math.sqrt(spread_sq / (n - 1))
    # float rounding of a constant series leaves a spread near 1e-16, which is not a measurement
    if sd <= 1e-12 * max(abs(v) for v in x):
        out["status"] = "degenerate"
    else:
        rho1 = math.fsum(deviation[i] * deviation[i + 1] for i in range(n - 1)) / spread_sq
        out.update(
            status="tested",
            z=mean / (sd / math.sqrt(n)),
            rho1=rho1,
            bartlett_bound=BARTLETT_Z / math.sqrt(n),
            n_eff=n * (1 - rho1) / (1 + rho1) if rho1 > 0 else float(n),
        )
        if rho1 > out["bartlett_bound"]:
            out["review"].append(AUTOCORR)
        if abs(mean) > out["bias_bound"]:
            out["hints"].append(BIAS)
    if mean_square > out["mean_square_bound"]:
        out["hints"].append(VARIANCE)
    if not all(math.isfinite(v) for v in out.values() if isinstance(v, float)):
        raise ValueError("a statistic is not finite; the residuals are out of range")
    return out


def _parse_day(text, name):
    try:
        day = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{name}: DATE {text!r} is not an ISO date") from None
    if day.tzinfo is not None:
        raise ValueError(f"{name}: DATE {text!r} must not carry a time zone")
    return day


def _read_truth(path: Path, pd) -> dict[str, float]:
    table = pd.read_csv(path, keep_default_na=False, float_precision="round_trip", dtype={"OBS": str})
    for column in ("OBS", "TRUTH"):
        if column not in table:
            raise ValueError(f"truth file lacks the column {column}")
    repeated = sorted(set(table.loc[table["OBS"].duplicated(), "OBS"]))
    if repeated:
        raise ValueError(f"truth has duplicate OBS rows for observations {repeated[:5]}")
    try:
        values = pd.to_numeric(table["TRUTH"], errors="raise").astype(float)
    except (ValueError, TypeError) as error:
        raise ValueError("TRUTH must be numeric") from error
    return dict(zip(table["OBS"], values, strict=True))


def _read_residuals(path: Path, ensemble: str, pd):
    with path.open(newline="") as stream:
        header = next(csv.reader(stream), [])
    if len(header) != len(set(header)):
        raise ValueError("residuals.csv has a repeated column name")
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ValueError(f"residuals.csv lacks the columns {missing}")
    table = pd.read_csv(
        path, keep_default_na=False, float_precision="round_trip", dtype={c: str for c in TEXT_COLUMNS}
    )
    part = table[table["ENSEMBLE"] == ensemble].copy()
    for column in NUMERIC_COLUMNS:
        try:
            part[column] = pd.to_numeric(part[column], errors="raise")
        except (ValueError, TypeError) as error:
            raise ValueError(f"{column} must be finite numeric data") from error
    return part


def read_series(residuals: Path, ensemble: str, truth_path: Path | None = None):
    """Per KEY, the observations in date order, each with its normalized residual parts.

    Returns (n_members, {KEY: [observation, ...]}). An observation is a dict with its date, total and
    spread (var over members of SIMULATED / ERROR^2, ddof 1), and with a truth file also noise and discrepancy.
    """
    try:
        import pandas as pd

        validator = _module("coverage.py")
    except ImportError as error:
        raise ValueError(f"NumPy and pandas are required to read the files ({error})") from error
    truth = _read_truth(truth_path, pd) if truth_path else None
    part = _read_residuals(residuals, ensemble, pd)
    validator.from_residuals(part, ensemble, truth)
    part = part.assign(REAL=part["REAL"].astype(int))
    wide = part.pivot(index="REAL", columns="OBS", values="SIMULATED")
    meta = part.groupby("OBS", sort=True)[["KEY", "DATE", "OBSERVED", "ERROR"]].first()

    undated = [name for name, row in meta.iterrows() if not row["DATE"].strip()]
    if undated:
        raise ValueError(
            f"{len(undated)} observation(s) have no DATE, for example {undated[0]!r}."
            " The tool orders each series by DATE. GENERAL_OBSERVATION data (KEY GEN:...) has none"
        )
    by_key, seen = defaultdict(list), {}
    for name, row in meta.iterrows():
        key = row["KEY"].strip()
        if not key:
            raise ValueError(f"{name}: KEY is empty")
        label = row["DATE"].strip()
        day = _parse_day(label, name)
        if (key, day) in seen:
            raise ValueError(
                f"duplicate KEY and DATE: {seen[key, day]!r} and {name!r} are both {key} on {label}"
            )
        seen[key, day] = name
        simulated = wide[name].tolist()
        observed, error = float(row["OBSERVED"]), float(row["ERROR"])
        centre = math.fsum(simulated) / len(simulated)
        spread = (
            math.fsum((s - centre) ** 2 for s in simulated) / (len(simulated) - 1) / error**2
            if len(simulated) > 1
            else 0.0
        )
        item = {
            "date": day,
            "label": label,
            "total": math.fsum((s - observed) / error for s in simulated) / len(simulated),
            "spread": spread,
        }
        if truth is not None:
            item["noise"] = (observed - truth[name]) / error
            item["discrepancy"] = math.fsum((s - truth[name]) / error for s in simulated) / len(simulated)
        by_key[key].append(item)
    return len(wide.index), {key: sorted(items, key=lambda i: i["date"]) for key, items in by_key.items()}


def _finding(code, key, component, s):
    n = s["n"]
    if code == AUTOCORR:
        detail = (
            f"lag-1 autocorrelation {s['rho1']:+.2f} is above the Bartlett bound {s['bartlett_bound']:.2f}"
            f" (n {n}, n_eff {s['n_eff']:.1f}). Review only: a smooth ensemble-mean miss gives a high rho1 by"
            " construction. A diagonal ERROR treats the dates as independent"
        )
    elif code == BIAS:
        detail = (
            f"mean residual {s['mean']:+.2f} is beyond {s['bias_bound']:.2f} = 3 sqrt(1/n + s)"
            f" (n {n}, s {s['spread']:.3g}, z {s['z']:+.1f})."
            " The ensemble spread does not explain the offset"
        )
    else:
        detail = (
            f"mean(r^2) {s['mean_square']:.2f} is above {s['mean_square_bound']:.2f} = 1 + 3 sqrt(2/n) + 9 s"
            f" (n {n}, s {s['spread']:.3g}). The scatter is larger than ERROR and the ensemble spread imply"
        )
    return {"code": code, "key": key, "component": component, "detail": detail}


def analyse(residuals: Path, ensemble: str, truth_path: Path | None = None, min_points: int = MIN_POINTS):
    n_members, by_key = read_series(residuals, ensemble, truth_path)
    components = COMPONENTS if truth_path else COMPONENTS[:1]
    series, findings, review = [], [], []
    for key in sorted(by_key):
        items = by_key[key]
        gaps = [
            (b["date"] - a["date"]).total_seconds() / 86400 for a, b in zip(items, items[1:], strict=False)
        ]
        mean_spread = math.fsum(i["spread"] for i in items) / len(items)
        for component in components:
            spread = 0.0 if component == "noise" else mean_spread  # observation noise has no ensemble part
            s = series_statistics([i[component] for i in items], min_points, spread)
            series.append(
                {
                    "key": key,
                    "component": component,
                    **s,
                    "first_date": items[0]["label"],
                    "last_date": items[-1]["label"],
                    "spacing_days_min": min(gaps) if gaps else None,
                    "spacing_days_max": max(gaps) if gaps else None,
                }
            )
            findings += [_finding(code, key, component, s) for code in s["hints"]]
            review += [_finding(code, key, component, s) for code in s["review"]]
    return {
        "schema": "ahm-residual-correlation/2",
        "ensemble": ensemble,
        "n_members": n_members,
        "n_series": len(by_key),
        "min_points": min_points,
        "separated": truth_path is not None,
        "series": series,
        "findings": findings,
        "review": review,
        "limiters": list(LIMITERS),
        "measured": MEASURED,
        "inferred": INFERRED,
        "guidance": GUIDANCE,
    }


def _line(s, width):
    head = f"  {s['key']:<{width}}  {s['component']:<11}  n {s['n']:<4}"
    if s["status"] == "insufficient":
        return f"{head} insufficient: fewer dates than the minimum, no test made (mean {s['mean']:+.3f})"
    squares = f"s {s['spread']:.3g}  mean(r^2) {s['mean_square']:.2f} (bound {s['mean_square_bound']:.2f})"
    if s["status"] == "degenerate":
        return f"{head} zero spread: z and rho1 are undefined, no test made; mean {s['mean']:+.3f}; {squares}"
    result = ", ".join(s["hints"]) if s["hints"] else "no hint raised"
    if s["review"]:
        result += "; review: " + ", ".join(s["review"])
    return (
        f"{head} mean {s['mean']:+.3f} (bound {s['bias_bound']:.3f})  z {s['z']:+.1f}  rho1 {s['rho1']:+.3f} (bound {s['bartlett_bound']:.3f})"
        f"  n_eff {s['n_eff']:.1f}  {squares}  {result}"
    )


def render(result: dict) -> str:
    series = result["series"]
    count = {
        name: sum(s["status"] == name for s in series) for name in ("tested", "insufficient", "degenerate")
    }
    lines = [
        f"RESIDUAL CORRELATION [{result['ensemble']}] {result['n_members']} members, {result['n_series']} keys:"
        f" {count['tested']} series tested, {count['insufficient']} insufficient, {count['degenerate']} degenerate",
        "r = mean over members of (SIMULATED - OBSERVED) / ERROR, one series per KEY, in DATE order.",
        SEPARATION if result["separated"] else NO_SEPARATION,
        f"Rules for n >= {result['min_points']}, s = mean spread var(SIMULATED)/ERROR^2 (0 for noise):"
        f" {BIAS} if |mean| > 3 sqrt(1/n + s); {VARIANCE} if mean(r^2) > 1 + 3 sqrt(2/n) + 9 s;"
        f" {AUTOCORR} (review only, never a finding) if rho1 > 1.96/sqrt(n).",
    ]
    width = max(len(s["key"]) for s in series)
    lines += [_line(s, width) for s in series]
    lines += [f"{f['code']}: {f['key']} {f['component']}: {f['detail']}" for f in result["findings"]]
    if not result["findings"]:
        lines.append("No hint was raised.")
    lines += [f"REVIEW {f['code']}: {f['key']} {f['component']}: {f['detail']}" for f in result["review"]]
    lines += [f"Limiter: {text}" for text in result["limiters"]]
    lines += [result["measured"], result["inferred"], result["guidance"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--residuals", type=Path, required=True, help="residuals.csv of misfit_report.py")
    ap.add_argument("--ensemble", default="posterior", help="ENSEMBLE label (default posterior)")
    ap.add_argument("--truth-response", type=Path, help="CSV with OBS and TRUTH (synthetic cases only)")
    ap.add_argument(
        "--min-points", type=int, default=MIN_POINTS, help="shortest series that is tested (default 8)"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.min_points < 3:
        print("ERROR: --min-points must be at least 3", file=sys.stderr)
        return 2
    try:
        result = analyse(args.residuals, args.ensemble, args.truth_response, args.min_points)
        text = json.dumps(result, indent=1, allow_nan=False) if args.json else render(result)
    except (OSError, ValueError, KeyError, ArithmeticError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
