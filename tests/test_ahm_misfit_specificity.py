"""Spread-aware chi2 and OBSERR references: a well-calibrated wide ensemble does not fire, a narrow one does.

Synthetic residuals from a seeded stdlib generator. No simulator, no project data. The reference model: a
calibrated posterior treats the truth like one more member, so with s = var(SIMULATED) / ERROR^2 a member
misfit is about 1 + 2 s and the ensemble-mean miss has sd about sqrt(s), shared by the dates of a series.
"""

import csv
import importlib.util
import json
import random
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HM = ROOT / "skills/history-match-qc/scripts"
HAVE_FRAME = all(importlib.util.find_spec(name) is not None for name in ("numpy", "pandas"))
frame = pytest.mark.skipif(not HAVE_FRAME, reason="NumPy and pandas are required")
BIAS, VARIANCE, AUTOCORR = "HMQC-OBSERR-BIAS", "HMQC-OBSERR-VARIANCE", "HMQC-OBSERR-AUTOCORR"
ABOVE = "misfit above the spread-aware reference"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"spec_t_{name}", HM / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


collapse = load("collapse_checks")


def residual_rows(*, n_dates, members, member_sd, shared_miss, noise_sd, seed, key="WOPR:P1"):
    """OBSERVED = TRUTH + noise; member = TRUTH + shared_miss + member scatter, all in units of ERROR (10)."""
    rng = random.Random(seed)
    error, rows = 10.0, []
    for t in range(n_dates):
        truth = 100.0 + t
        observed = truth + error * rng.gauss(0, noise_sd)
        for real in range(members):
            simulated = truth + error * (shared_miss + rng.gauss(0, member_sd))
            rows.append(
                {
                    "ENSEMBLE": "posterior",
                    "REAL": real,
                    "OBS": f"O{t:03d}",
                    "KEY": key,
                    "DATE": f"{2015 + t // 12}-{t % 12 + 1:02d}-01",
                    "OBSERVED": observed,
                    "ERROR": error,
                    "SIMULATED": simulated,
                }
            )
    return rows


def correlation(tmp_path, rows):
    rc = load("residual_correlation")
    path = tmp_path / "residuals.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = rc.analyse(path, "posterior")
    json.dumps(result, allow_nan=False)
    return result


def member_misfit(rows):
    by_real = {}
    for r in rows:
        by_real.setdefault(r["REAL"], []).append(((r["SIMULATED"] - r["OBSERVED"]) / r["ERROR"]) ** 2)
    return sum(sum(v) / len(v) for v in by_real.values()) / len(by_real)


def member_spread(rows):
    by_obs = {}
    for r in rows:
        by_obs.setdefault(r["OBS"], []).append(r["SIMULATED"] / r["ERROR"])
    values = []
    for sims in by_obs.values():
        m = sum(sims) / len(sims)
        values.append(sum((s - m) ** 2 for s in sims) / (len(sims) - 1))
    return sum(values) / len(values)


# ---------- chi2 ----------


def test_wide_calibrated_posterior_is_context_not_a_misfit_finding():
    # The truth is one more member of a wide ensemble (member sd 1 ERROR, shared miss 1 sd): misfit near 1 + 2 s.
    rows = residual_rows(n_dates=72, members=50, member_sd=1.0, shared_miss=1.0, noise_sd=1.0, seed=1)
    chi = collapse.misfit_vs_chi2(member_misfit(rows), 72, 3.0, member_spread(rows))
    assert chi["verdict"] == "above the noise band"  # old reading, kept for compensation_check
    assert chi["spread_verdict"] == "inside the spread-aware reference"
    assert chi["spread_upper"] == pytest.approx(1 + 2 * chi["spread"] + 3 * (2 / 72) ** 0.5)
    assert ABOVE not in [f["finding"] for f in collapse.findings({}, {"posterior": chi})]


def test_narrow_posterior_with_too_small_errors_still_fires():
    # Errors stated at half the real noise, and a collapsed ensemble: the misses are not explained.
    rows = residual_rows(n_dates=72, members=50, member_sd=0.1, shared_miss=0.0, noise_sd=2.0, seed=2)
    chi = collapse.misfit_vs_chi2(member_misfit(rows), 72, 3.0, member_spread(rows))
    assert chi["spread_verdict"] == "above the spread-aware reference"
    found = collapse.findings({}, {"posterior": chi})
    assert [f["finding"] for f in found] == [ABOVE]
    assert "s = " in found[0]["detail"]


def test_no_spread_keeps_the_old_verdict_and_reports_the_reference_unavailable():
    chi = collapse.misfit_vs_chi2(50.2, 72)
    assert chi["verdict"] == "above the noise band"
    assert chi["spread_verdict"] == "unavailable" and chi["spread"] is None
    assert collapse.findings({}, {"posterior": chi}) == []
    json.dumps(chi, allow_nan=False)


@frame
def test_misfit_report_takes_the_spread_from_obs_summary_with_ddof_1():
    pytest.importorskip("res2df")
    pd = pytest.importorskip("pandas")
    misfit = load("misfit_report")
    n_obs, size = 40, 5
    table = pd.DataFrame(
        [{"ENSEMBLE": "posterior", "REAL": i, "OBS_USED": n_obs, "MISFIT": 2.5} for i in range(size)]
    )
    args = Namespace(min_realizations=20, chi2_sigma=3.0)
    for ratio, expect_finding in ((1.0, False), (0.1, True)):
        summary = pd.DataFrame(
            [
                {"ENSEMBLE": "posterior", "OBS": f"O{i}", "COUNT": size, "SPREAD_RATIO": ratio}
                for i in range(n_obs)
            ]
        )
        _, chi2, found = misfit.ensemble_checks(table, summary, {"posterior": size}, 2, args)
        assert chi2["posterior"]["spread"] == pytest.approx(ratio**2 * size / (size - 1))
        assert chi2["posterior"]["verdict"] == "above the noise band"
        assert (ABOVE in [f["finding"] for f in found]) is expect_finding


# ---------- residual_correlation ----------


@frame
def test_wide_calibrated_ensemble_with_a_shared_mean_miss_raises_no_observr_finding(tmp_path):
    # Old rule: |z| > 3 and mean(r^2) > 1 + 3 sqrt(2/n) both fire on this; the miss is inside the spread.
    rows = residual_rows(n_dates=60, members=50, member_sd=1.0, shared_miss=1.0, noise_sd=1.0, seed=3)
    result = correlation(tmp_path, rows)
    (s,) = result["series"]
    assert s["spread"] == pytest.approx(1.0, abs=0.15)
    assert abs(s["z"]) > 3  # the old trigger
    assert result["findings"] == []
    assert all(f["code"] == AUTOCORR for f in result["review"])


@frame
def test_narrow_ensemble_with_the_same_miss_and_scaled_errors_still_fires(tmp_path):
    rows = residual_rows(n_dates=60, members=50, member_sd=0.05, shared_miss=1.0, noise_sd=2.0, seed=4)
    result = correlation(tmp_path, rows)
    assert {f["code"] for f in result["findings"]} == {BIAS, VARIANCE}
    assert AUTOCORR not in {f["code"] for f in result["findings"]}


@frame
def test_autocorrelation_is_a_review_item_never_a_finding(tmp_path):
    rows = residual_rows(n_dates=60, members=20, member_sd=0.3, shared_miss=0.0, noise_sd=1.0, seed=5)
    smooth = {f"O{t:03d}": 3.0 * ((t // 15) % 2 - 0.5) for t in range(60)}  # a step pattern in the noise
    for r in rows:
        r["OBSERVED"] += r["ERROR"] * smooth[r["OBS"]]
    result = correlation(tmp_path, rows)
    assert AUTOCORR in {f["code"] for f in result["review"]}
    assert AUTOCORR not in {f["code"] for f in result["findings"]}
    assert result["schema"] == "ahm-residual-correlation/2"
