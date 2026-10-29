#!/usr/bin/env python3
"""Coverage of a history-match ensemble that is not biased by the observation noise. Read-only.

An observation is the truth plus noise with sd ERROR. A simulated ensemble has no noise. So the share of
observations inside the P5-P95 band of the simulated values is low even for a correct posterior: on drogon-lite
it was 0.45 while the correct numbers were 0.85 to 0.88. Do not report that share as calibration.

Two measures do not have the bias:
  predictive      share of observations inside P5-P95 of the predictive distribution, sim + N(0, ERROR).
                  Exact: the predictive CDF at the observation is the mean over realizations of
                  Phi((obs - sim) / ERROR). Inside means 0.05 <= CDF <= 0.95. Works on real fields.
  truth_response  share of noise-free truth responses inside P5-P95 of the simulated values.
                  Synthetic cases only (--truth-response).
noise_biased is the old share. It is kept, labelled, for comparison only.

    coverage.py --residuals <misfit_report outdir>/residuals.csv [--ensemble posterior] \
        [--truth-response truth.csv] [--json]
residuals.csv is the misfit_report.py output (ENSEMBLE, REAL, OBS, OBSERVED, ERROR, SIMULATED).
truth.csv has OBS and TRUTH columns.
Coded findings (hints, exit code 0):
    HMQC-COVERAGE-PREDICTIVE  predictive coverage below --min-coverage (0.8). Posterior too narrow or biased.
    HMQC-COVERAGE-TRUTH       truth-response coverage below --min-coverage.
    HMQC-COVERAGE-BIASED      the noise-biased share is far below the predictive one. Not a problem of the
                              posterior; a reminder not to quote the biased share.
Limiter: observations in a time series are correlated, so the shares carry more sampling error than n suggests.
Exit code: 0 = done, 1 = input problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

LOW, HIGH = 0.05, 0.95
NOMINAL = HIGH - LOW
MIN_COVERAGE = 0.8
BIASED_GAP = 0.2


@dataclass(frozen=True)
class Coverage:
    n_obs: int
    n_real: int
    nominal: float
    predictive: float
    truth_response: float | None
    noise_biased: float
    findings: list[dict] = field(default_factory=list)


def _norm_cdf(x: np.ndarray) -> np.ndarray:
    erf = np.vectorize(math.erf, otypes=[float])
    return 0.5 * (1.0 + erf(x / math.sqrt(2.0)))


def _domain(sim, observed, error) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A complete rectangular domain: (realizations >= 2, observations >= 1), all finite, errors positive."""
    sim, observed, error = (np.asarray(a, dtype=float) for a in (sim, observed, error))
    if sim.ndim != 2 or observed.ndim != 1 or sim.shape[1] != observed.size or observed.shape != error.shape:
        raise ValueError("sim must be (realizations, observations) and match 1-D observed and error")
    if sim.shape[0] < 2 or sim.shape[1] == 0:
        raise ValueError("need at least 2 realizations for every observation, and at least 1 observation")
    for name, values in (("simulated", sim), ("observed", observed), ("error", error)):
        if not np.isfinite(values).all():
            raise ValueError(f"every {name} value must be finite")
    if not np.all(error > 0):
        raise ValueError("every observation error must be positive")
    return sim, observed, error


def predictive_cdf(sim: np.ndarray, observed: np.ndarray, error: np.ndarray) -> np.ndarray:
    """CDF of the predictive mixture sim + N(0, error) at each observation. sim is (realizations, observations)."""
    sim, observed, error = _domain(sim, observed, error)
    z = (observed[None, :] - sim) / error[None, :]
    return np.mean(_norm_cdf(z), axis=0)


def _band(sim: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.percentile(sim, 100 * LOW, axis=0), np.percentile(sim, 100 * HIGH, axis=0)


def coverage(
    sim: np.ndarray,
    observed: np.ndarray,
    error: np.ndarray,
    truth: np.ndarray | None = None,
    min_coverage: float = MIN_COVERAGE,
) -> Coverage:
    sim, observed, error = _domain(sim, observed, error)
    cdf = predictive_cdf(sim, observed, error)
    lo, hi = _band(sim)
    predictive = float(np.mean((cdf >= LOW) & (cdf <= HIGH)))
    biased = float(np.mean((observed >= lo) & (observed <= hi)))
    truth_share = None
    if truth is not None:
        t = np.asarray(truth, dtype=float)
        if t.shape != observed.shape or not np.isfinite(t).all():
            raise ValueError("truth must be a finite value for every observation")
        truth_share = float(np.mean((t >= lo) & (t <= hi)))
    found = []
    if predictive < min_coverage:
        found.append(
            {
                "code": "HMQC-COVERAGE-PREDICTIVE",
                "detail": f"predictive P5-P95 coverage {predictive:.2f} is below {min_coverage:g} (nominal"
                f" {NOMINAL:.2f}). The posterior is too narrow or biased. Limiter: correlated observations",
            }
        )
    if truth_share is not None and truth_share < min_coverage:
        found.append(
            {
                "code": "HMQC-COVERAGE-TRUTH",
                "detail": f"truth-response P5-P95 coverage {truth_share:.2f} is below {min_coverage:g}",
            }
        )
    if predictive - biased > BIASED_GAP:
        found.append(
            {
                "code": "HMQC-COVERAGE-BIASED",
                "detail": f"observations inside the simulated P5-P95 band {biased:.2f} against predictive"
                f" {predictive:.2f}. The first share is biased low by the observation noise. Quote the predictive"
                " (and on a synthetic case the truth-response) coverage",
            }
        )
    return Coverage(int(observed.size), int(sim.shape[0]), NOMINAL, predictive, truth_share, biased, found)


# Per-observation metadata that must be identical on every row of one observation. STATUS is not here:
# misfit_report.py sets it per member (exact, step or interpolated, from that member's own time steps).
METADATA = ("OBSERVED", "ERROR", "KEY", "DATE")
# STATUS values of misfit_report.py (its MISSING_STATUS) that mean the member has no usable prediction.
MISSING_STATUS = frozenset({"outside", "missing_key", "missing_file", "missing_index"})


def from_residuals(
    table,
    ensemble: str,
    truth: dict[str, float] | None = None,
    *,
    expected_members: set[int] | None = None,
    expected_obs: set[str] | None = None,
    **kwargs,
) -> Coverage:
    """Coverage of one ensemble of a misfit_report.py residuals table (pandas DataFrame).

    The rows must form one complete REAL x OBS grid, one row per pair, with one OBSERVED, ERROR, KEY and DATE
    per observation. STATUS may differ between members; a STATUS in MISSING_STATUS is refused.
    expected_members and expected_obs, when given, are the whole domain.
    """
    part = table[table["ENSEMBLE"] == ensemble]
    if part.empty:
        raise ValueError(f"no rows for ensemble {ensemble!r}")
    try:
        reals = part["REAL"].to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{ensemble}: REAL must be integer member identities") from error
    if not (np.isfinite(reals).all() and (reals >= 0).all() and (reals == np.floor(reals)).all()):
        raise ValueError(f"{ensemble}: REAL must be finite nonnegative integer member identities")
    if part["OBS"].isna().any():
        raise ValueError(f"{ensemble}: every row needs an OBS name")
    part = part.assign(REAL=reals.astype(int))
    if part.duplicated(["REAL", "OBS"]).any():
        raise ValueError(f"{ensemble}: duplicate REAL/OBS rows")
    if "STATUS" in part:
        unusable = part[part["STATUS"].isin(MISSING_STATUS)]
        if len(unusable):
            raise ValueError(
                f"{ensemble}: {len(unusable)} rows have no usable prediction"
                f" (STATUS {sorted(set(unusable['STATUS']))}, e.g. OBS {unusable['OBS'].iloc[0]!r})"
            )
    for column in (c for c in METADATA if c in part):
        if (part.groupby("OBS")[column].nunique(dropna=False) > 1).any():
            raise ValueError(f"{ensemble}: {column} differs between the rows of one observation")
    members, names = set(part["REAL"]), set(part["OBS"])
    if len(part) != len(members) * len(names):
        raise ValueError(f"{ensemble}: the observations do not have the same member set")
    if expected_members is not None and members != set(expected_members):
        raise ValueError(
            f"{ensemble}: member set differs from the expected one: missing {sorted(set(expected_members) - members)},"
            f" extra {sorted(members - set(expected_members))}"
        )
    if expected_obs is not None and names != set(expected_obs):
        raise ValueError(
            f"{ensemble}: observations differ from the expected ones: missing {sorted(set(expected_obs) - names)},"
            f" extra {sorted(names - set(expected_obs))}"
        )
    wide = part.pivot(index="REAL", columns="OBS", values="SIMULATED")
    meta = part.groupby("OBS")[["OBSERVED", "ERROR"]].first().loc[wide.columns]
    t = None
    if truth is not None:
        missing = [n for n in wide.columns if n not in truth]
        if missing:
            raise ValueError(f"truth has no value for observations {missing[:5]}")
        t = np.array([truth[n] for n in wide.columns], dtype=float)
    try:
        return coverage(wide.to_numpy(), meta["OBSERVED"].to_numpy(), meta["ERROR"].to_numpy(), t, **kwargs)
    except ValueError as error:
        raise ValueError(f"{ensemble}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--residuals", type=Path, required=True, help="residuals.csv of misfit_report.py")
    ap.add_argument("--ensemble", default="posterior", help="ENSEMBLE label (default posterior)")
    ap.add_argument("--truth-response", type=Path, help="CSV with OBS and TRUTH (synthetic cases only)")
    ap.add_argument("--min-coverage", type=float, default=MIN_COVERAGE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not 0 < args.min_coverage <= 1:
        print("ERROR: --min-coverage must be in (0, 1]", file=sys.stderr)
        return 2
    import pandas as pd

    try:
        table = pd.read_csv(args.residuals)
        truth = None
        if args.truth_response:
            t = pd.read_csv(args.truth_response)
            repeated = sorted(set(t.loc[t["OBS"].duplicated(), "OBS"]))
            if repeated:
                raise ValueError(f"truth has duplicate OBS rows for observations {repeated[:5]}")
            truth = dict(zip(t["OBS"], t["TRUTH"].astype(float), strict=True))
        result = from_residuals(table, args.ensemble, truth, min_coverage=args.min_coverage)
    except (OSError, KeyError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(result), indent=1))
        return 0
    truth_text = "n/a" if result.truth_response is None else f"{result.truth_response:.2f}"
    print(
        f"COVERAGE [{args.ensemble}] n_obs {result.n_obs} n_real {result.n_real} nominal {result.nominal:.2f}:"
        f" predictive {result.predictive:.2f}, truth_response {truth_text},"
        f" noise_biased {result.noise_biased:.2f} (do not quote)"
    )
    for item in result.findings:
        print(f"{item['code']}: {item['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
