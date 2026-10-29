"""Residual correlation diagnostic on analytic data. No simulator, no project data.

Every case builds a residuals.csv from a seeded stdlib generator, so a result is a number that can be
derived by hand: an AR(1) series has lag-1 autocorrelation rho, white noise has none, and so on.
"""

import csv
import importlib.util
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/history-match-qc/scripts/residual_correlation.py"
DATA = ROOT / "tests/data/drogon_lite"
HAVE_FRAME = all(importlib.util.find_spec(name) is not None for name in ("numpy", "pandas"))
frame = pytest.mark.skipif(not HAVE_FRAME, reason="NumPy and pandas are required")

GUIDANCE = (
    "Do not raise ERROR to make a gate pass. "
    "A correlated or biased series calls for a documented error model or a model change."
)
COLUMNS = ["ENSEMBLE", "REAL", "OBS", "KEY", "DATE", "OBSERVED", "ERROR", "SIMULATED", "STATUS", "RESIDUAL"]
AUTOCORR, BIAS, VARIANCE = "HMQC-OBSERR-AUTOCORR", "HMQC-OBSERR-BIAS", "HMQC-OBSERR-VARIANCE"
ERROR = 10.0
FIRST_DAY = date(2015, 1, 1)


def load_script():
    assert SCRIPT.is_file(), "residual_correlation.py command is missing"
    spec = importlib.util.spec_from_file_location("tested_residual_correlation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rc():
    return load_script()


def white(rng, n):
    return [rng.gauss(0, 1) for _ in range(n)]


def ar1(rng, n, rho):
    """Stationary AR(1) with unit variance: x_t = rho x_(t-1) + sqrt(1 - rho^2) e_t."""
    step = math.sqrt(1 - rho * rho)
    x = rng.gauss(0, 1)
    out = [x]
    for _ in range(n - 1):
        x = rho * x + step * rng.gauss(0, 1)
        out.append(x)
    return out


def day(i):
    return (FIRST_DAY + timedelta(days=30 * i)).isoformat()


def make_rows(series, *, members=12, scatter=0.3, seed=0, ensemble="posterior", scramble_names=False):
    """series: {KEY: {"noise": [...], "offset": [...]}}, both in units of ERROR.

    OBSERVED = TRUTH + ERROR * noise. SIMULATED = TRUTH + ERROR * (offset + member scatter).
    So the mean normalized residual of a date is offset - noise + the mean member scatter.
    """
    rng = random.Random(seed)
    rows, truth_rows = [], []
    for key, spec in series.items():
        n = len(spec["noise"])
        offset = spec.get("offset", [0.0] * n)
        ids = list(range(n))
        if scramble_names:
            random.Random(99).shuffle(ids)
        for t in range(n):
            error = ERROR * (1 + 0.5 * (t % 2))
            truth = 100.0 + 5.0 * math.sin(t / 7)
            observed = truth + error * spec["noise"][t]
            name = f"{key.replace(':', '_')}_{ids[t]:04d}"
            truth_rows.append({"OBS": name, "TRUTH": truth})
            for member in range(members):
                simulated = truth + error * (offset[t] + rng.gauss(0, scatter))
                rows.append(
                    {
                        "ENSEMBLE": ensemble,
                        "REAL": member,
                        "OBS": name,
                        "KEY": key,
                        "DATE": day(t),
                        "OBSERVED": observed,
                        "ERROR": error,
                        "SIMULATED": simulated,
                        "STATUS": "exact",
                        "RESIDUAL": (simulated - observed) / error,
                    }
                )
    return rows, truth_rows


def write_csv(path, rows, columns):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def save(folder, rows, truth_rows=None, columns=COLUMNS):
    residuals = write_csv(folder / "residuals.csv", rows, columns)
    truth = write_csv(folder / "truth.csv", truth_rows, ["OBS", "TRUTH"]) if truth_rows is not None else None
    return residuals, truth


def run(rc, capsys, *args):
    code = rc.main([str(a) for a in args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def report(rc, capsys, residuals, *extra):
    code, out, err = run(rc, capsys, "--residuals", residuals, "--json", *extra)
    assert code == 0, err
    return json.loads(out)


def pick(result, key, component="total"):
    found = [s for s in result["series"] if s["key"] == key and s["component"] == component]
    assert len(found) == 1, (key, component, [(s["key"], s["component"]) for s in result["series"]])
    return found[0]


def test_hand_computed_statistics_match_the_analytic_values(rc):
    # r = 1..8: mean 4.5; deviations sum of squares 42; lag-1 products sum 26.25; sample sd sqrt(6).
    s = rc.series_statistics([1, 2, 3, 4, 5, 6, 7, 8])
    assert s["n"] == 8 and s["status"] == "tested"
    assert s["mean"] == pytest.approx(4.5)
    assert s["z"] == pytest.approx(4.5 / (math.sqrt(6) / math.sqrt(8)))
    assert s["rho1"] == pytest.approx(26.25 / 42)
    assert s["bartlett_bound"] == pytest.approx(1.96 / math.sqrt(8))
    assert s["n_eff"] == pytest.approx(8 * (1 - 0.625) / (1 + 0.625))
    assert s["mean_square"] == pytest.approx(204 / 8)
    assert s["mean_square_bound"] == pytest.approx(1 + 3 * math.sqrt(2 / 8))
    assert sorted(s["hints"]) == [BIAS, VARIANCE]  # rho1 0.625 is below the Bartlett bound 0.693


def test_effective_sample_size_equals_n_when_rho1_is_not_positive(rc):
    s = rc.series_statistics([1, -1] * 5)
    assert s["rho1"] == pytest.approx(-0.9)
    assert s["n_eff"] == s["n"] == 10
    assert s["hints"] == []


def test_series_below_min_points_is_insufficient_and_has_no_test_statistics(rc):
    s = rc.series_statistics([0.1, -0.2, 0.3, 0.0, 0.2, -0.1, 0.1])
    assert s["status"] == "insufficient" and s["n"] == 7
    assert s["hints"] == []
    assert s["rho1"] is None and s["z"] is None and s["n_eff"] is None


def test_zero_spread_series_is_degenerate_not_passing(rc):
    s = rc.series_statistics([0.5] * 10)
    assert s["status"] == "degenerate"
    assert s["rho1"] is None and s["z"] is None


def test_white_noise_autocorr_rate_is_inside_the_binomial_band_around_nominal_2_5_percent(rc):
    # One-sided nominal level 2.5 percent. Over 400 series the count is Binomial(400, 0.025):
    # mean 10, sd 3.1. The band 2..24 is -2.6 sd to +4.5 sd, and the seeds are fixed, so it is not flaky.
    counts = {AUTOCORR: 0, BIAS: 0, VARIANCE: 0}
    rho = []
    for seed in range(400):
        s = rc.series_statistics(white(random.Random(1000 + seed), 200))
        rho.append(s["rho1"])
        for code in s["hints"] + s["review"]:
            counts[code] += 1
    assert 2 <= counts[AUTOCORR] <= 24
    assert counts[BIAS] <= 8 and counts[VARIANCE] <= 8  # nominal 0.3 percent each: about 1 of 400
    assert abs(statistics.fmean(rho)) < 0.015  # the estimator bias is about -1/n = -0.005


@frame
def test_ar1_noise_gives_the_analytic_rho1_and_effective_sample_size(rc, capsys, tmp_path):
    n, rho = 500, 0.7
    rows, _ = make_rows({"WOPR:P1": {"noise": ar1(random.Random(7), n, rho)}})
    residuals, _ = save(tmp_path, rows)
    s = pick(report(rc, capsys, residuals), "WOPR:P1")
    assert s["n"] == n
    assert abs(s["rho1"] - rho) < 0.1
    assert abs(s["n_eff"] - n * (1 - rho) / (1 + rho)) < 0.25 * n * (1 - rho) / (1 + rho)
    assert AUTOCORR in s["review"] and AUTOCORR not in s["hints"]


@frame
def test_constant_offset_is_biased_not_autocorrelated(rc, capsys, tmp_path):
    # a narrow ensemble (member scatter 0.05 ERROR), so the offset is far outside its own spread
    rows, _ = make_rows(
        {"WBHP:P1": {"noise": white(random.Random(3), 100), "offset": [0.5] * 100}}, scatter=0.05
    )
    residuals, _ = save(tmp_path, rows)
    s = pick(report(rc, capsys, residuals), "WBHP:P1")
    assert BIAS in s["hints"] and AUTOCORR not in s["hints"] + s["review"]
    assert s["mean"] == pytest.approx(0.5, abs=0.3)


@frame
def test_error_underestimated_by_a_factor_two_raises_the_variance_hint(rc, capsys, tmp_path):
    noise = [2 * v for v in white(random.Random(5), 150)]  # true noise sd is twice the stated ERROR
    rows, _ = make_rows({"WWCT:P1": {"noise": noise}})
    residuals, _ = save(tmp_path, rows)
    s = pick(report(rc, capsys, residuals), "WWCT:P1")
    assert VARIANCE in s["hints"]
    assert 3.0 < s["mean_square"] < 5.5  # analytic 4


@frame
def test_short_series_is_reported_insufficient_without_pass_wording(rc, capsys, tmp_path):
    rows, _ = make_rows({"FOPR": {"noise": white(random.Random(2), 5)}})
    residuals, _ = save(tmp_path, rows)
    code, out, err = run(rc, capsys, "--residuals", residuals)
    assert code == 0 and err == ""
    line = next(x for x in out.splitlines() if x.lstrip().startswith("FOPR"))
    assert "insufficient" in line
    assert not re.search(r"pass|\bok\b|no hint|clean|consistent", line, re.I)
    s = pick(report(rc, capsys, residuals), "FOPR")
    assert s["status"] == "insufficient" and s["hints"] == []


@frame
def test_min_points_option_changes_the_threshold(rc, capsys, tmp_path):
    rows, _ = make_rows({"FOPR": {"noise": white(random.Random(2), 5)}})
    residuals, _ = save(tmp_path, rows)
    assert pick(report(rc, capsys, residuals, "--min-points", 5), "FOPR")["status"] == "tested"


@frame
def test_perfect_model_with_ar1_observation_noise_flags_noise_not_discrepancy(rc, capsys, tmp_path):
    noise = ar1(random.Random(11), 300, 0.7)
    rows, truth_rows = make_rows({"WOPR:P1": {"noise": noise}})
    residuals, truth = save(tmp_path, rows, truth_rows)
    result = report(rc, capsys, residuals, "--truth-response", truth)
    noise_row, disc_row, total = (pick(result, "WOPR:P1", c) for c in ("noise", "discrepancy", "total"))
    assert AUTOCORR in noise_row["review"]
    assert disc_row["hints"] == []
    assert AUTOCORR in total["review"]
    expected = rc.series_statistics(noise)  # the noise component is exactly (OBSERVED - TRUTH) / ERROR
    assert noise_row["mean"] == pytest.approx(expected["mean"], abs=1e-9)
    assert noise_row["rho1"] == pytest.approx(expected["rho1"], abs=1e-9)
    assert total["mean"] == pytest.approx(disc_row["mean"] - noise_row["mean"], abs=1e-9)


@frame
def test_white_noise_with_smooth_sinusoidal_model_bias_flags_discrepancy_not_noise(rc, capsys, tmp_path):
    n = 120
    offset = [1.5 * math.sin(2 * math.pi * t / 60) for t in range(n)]
    rows, truth_rows = make_rows({"WBHP:P1": {"noise": white(random.Random(13), n), "offset": offset}})
    residuals, truth = save(tmp_path, rows, truth_rows)
    result = report(rc, capsys, residuals, "--truth-response", truth)
    disc_row, noise_row = pick(result, "WBHP:P1", "discrepancy"), pick(result, "WBHP:P1", "noise")
    assert AUTOCORR in disc_row["review"]
    assert noise_row["hints"] == []
    assert disc_row["rho1"] > 0.9


@frame
def test_without_truth_the_tool_says_it_cannot_separate_and_reports_total_only(rc, capsys, tmp_path):
    rows, _ = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 30)}})
    residuals, _ = save(tmp_path, rows)
    code, out, err = run(rc, capsys, "--residuals", residuals)
    assert code == 0 and err == ""
    assert "cannot separate" in out
    result = report(rc, capsys, residuals)
    assert result["separated"] is False
    assert {s["component"] for s in result["series"]} == {"total"}


@frame
def test_truth_run_says_discrepancy_holds_the_estimation_error_of_the_update_too(rc, capsys, tmp_path):
    rows, truth_rows = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 12)}}, members=4)
    residuals, truth = save(tmp_path, rows, truth_rows)
    _, out, _ = run(rc, capsys, "--residuals", residuals, "--truth-response", truth)
    assert "SEPARATION: from the truth file" in out and "estimation error of the update" in out
    assert "cannot separate" not in out


@frame
def test_text_names_what_is_measured_and_what_is_inferred_and_the_correlation_limiter(rc, capsys, tmp_path):
    rows, _ = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 30)}})
    residuals, _ = save(tmp_path, rows)
    _, out, _ = run(rc, capsys, "--residuals", residuals)
    assert "Measured:" in out and "Inferred:" in out
    assert "correlated by construction" in out and "guides" in out


@frame
def test_text_and_json_carry_the_do_not_raise_error_sentence(rc, capsys, tmp_path):
    rows, _ = make_rows({"WOPR:P1": {"noise": ar1(random.Random(4), 60, 0.8)}})
    residuals, _ = save(tmp_path, rows)
    _, out, _ = run(rc, capsys, "--residuals", residuals)
    assert GUIDANCE in " ".join(out.split())
    assert report(rc, capsys, residuals)["guidance"] == GUIDANCE


@frame
def test_per_well_keys_are_separate_series_ordered_by_date_not_by_name(rc, capsys, tmp_path):
    n = 80
    smooth = [math.sin(2 * math.pi * t / 40) for t in range(n)]
    series = {"WOPR:PROD2": {"noise": white(random.Random(21), n)}, "WOPR:PROD1": {"noise": smooth}}
    rows, _ = make_rows(series, scramble_names=True)
    residuals, _ = save(tmp_path, rows)
    result = report(rc, capsys, residuals)
    assert [s["key"] for s in result["series"]] == ["WOPR:PROD1", "WOPR:PROD2"]
    assert pick(result, "WOPR:PROD1")["rho1"] > 0.9  # observation names are shuffled; DATE gives the order
    assert AUTOCORR in pick(result, "WOPR:PROD1")["review"]
    assert pick(result, "WOPR:PROD2")["n"] == n
    assert (
        pick(result, "WOPR:PROD1")["spacing_days_min"] == pick(result, "WOPR:PROD1")["spacing_days_max"] == 30
    )


@frame
def test_row_order_and_member_labels_do_not_change_the_numbers(rc, capsys, tmp_path):
    series = {
        "WOPR:P1": {"noise": ar1(random.Random(8), 40, 0.6)},
        "WWCT:P1": {"noise": white(random.Random(9), 40)},
    }
    rows, truth_rows = make_rows(series)
    first, truth = save(tmp_path, rows, truth_rows)
    expected = report(rc, capsys, first, "--truth-response", truth)
    other = tmp_path / "other"
    other.mkdir()
    shuffled = [dict(r, REAL=1000 - r["REAL"]) for r in rows]  # same members under other identities
    random.Random(6).shuffle(shuffled)
    random.Random(7).shuffle(truth_rows)
    second, truth2 = save(other, shuffled, truth_rows)
    assert report(rc, capsys, second, "--truth-response", truth2) == expected


@frame
def test_selected_ensemble_only_and_unknown_ensemble_is_refused(rc, capsys, tmp_path):
    prior, _ = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 30)}}, ensemble="prior")
    post, _ = make_rows({"WOPR:P1": {"noise": white(random.Random(2), 30), "offset": [2.0] * 30}})
    residuals, _ = save(tmp_path, prior + post)
    assert pick(report(rc, capsys, residuals), "WOPR:P1")["mean"] > 1.0
    assert abs(pick(report(rc, capsys, residuals, "--ensemble", "prior"), "WOPR:P1")["mean"]) < 1.0
    code, out, err = run(rc, capsys, "--residuals", residuals, "--ensemble", "nope")
    assert code == 1 and out == "" and err.startswith("ERROR:") and "Traceback" not in err


def spoil_nan_simulated(rows, truth_rows):
    rows[3]["SIMULATED"] = float("nan")


def spoil_nan_observed(rows, truth_rows):
    for r in rows:
        if r["OBS"] == rows[0]["OBS"]:
            r["OBSERVED"] = float("nan")


def spoil_infinite_error(rows, truth_rows):
    for r in rows:
        if r["OBS"] == rows[0]["OBS"]:
            r["ERROR"] = float("inf")


def spoil_zero_error(rows, truth_rows):
    for r in rows:
        if r["OBS"] == rows[0]["OBS"]:
            r["ERROR"] = 0.0


def spoil_duplicate_pair(rows, truth_rows):
    rows.append(dict(rows[0]))


def spoil_ragged_grid(rows, truth_rows):
    del rows[5]


def spoil_bad_date(rows, truth_rows):
    for r in rows:
        if r["OBS"] == rows[0]["OBS"]:
            r["DATE"] = "2015-13-45"


def spoil_text_date(rows, truth_rows):
    for r in rows:
        if r["OBS"] == rows[0]["OBS"]:
            r["DATE"] = "last Tuesday"


def spoil_empty_date(rows, truth_rows):
    for r in rows:
        if r["OBS"] == rows[0]["OBS"]:
            r["DATE"] = ""


def spoil_same_key_and_date(rows, truth_rows):
    second = next(r["OBS"] for r in rows if r["DATE"] != rows[0]["DATE"])
    for r in rows:
        if r["OBS"] == second:
            r["DATE"] = rows[0]["DATE"]


def spoil_empty_key(rows, truth_rows):
    for r in rows:
        if r["OBS"] == rows[0]["OBS"]:
            r["KEY"] = ""


def spoil_missing_truth_row(rows, truth_rows):
    truth_rows.pop(2)


def spoil_nan_truth(rows, truth_rows):
    truth_rows[1]["TRUTH"] = float("nan")


def spoil_duplicate_truth(rows, truth_rows):
    truth_rows.append(dict(truth_rows[0]))


@frame
@pytest.mark.parametrize(
    "spoil,message",
    [
        (spoil_nan_simulated, r"finite"),
        (spoil_nan_observed, r"finite"),
        (spoil_infinite_error, r"finite"),
        (spoil_zero_error, r"positive"),
        (spoil_duplicate_pair, r"duplicate"),
        (spoil_ragged_grid, r"member set"),
        (spoil_bad_date, r"DATE"),
        (spoil_text_date, r"DATE"),
        (spoil_empty_date, r"DATE"),
        (spoil_same_key_and_date, r"duplicate"),
        (spoil_empty_key, r"KEY"),
        (spoil_missing_truth_row, r"truth"),
        (spoil_nan_truth, r"truth"),
        (spoil_duplicate_truth, r"duplicate"),
    ],
)
def test_bad_input_exits_1_with_a_message_and_no_traceback_or_partial_report(
    rc, capsys, tmp_path, spoil, message
):
    rows, truth_rows = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 12)}}, members=4)
    spoil(rows, truth_rows)
    residuals, truth = save(tmp_path, rows, truth_rows)
    code, out, err = run(rc, capsys, "--residuals", residuals, "--truth-response", truth)
    assert code == 1
    assert out == ""
    assert err.startswith("ERROR:") and re.search(message, err, re.I), err
    assert "Traceback" not in err


@frame
@pytest.mark.parametrize("missing", ["KEY", "DATE", "SIMULATED"])
def test_missing_column_exits_1(rc, capsys, tmp_path, missing):
    rows, _ = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 12)}}, members=4)
    columns = [c for c in COLUMNS if c != missing]
    residuals, _ = save(tmp_path, rows, columns=columns)
    code, out, err = run(rc, capsys, "--residuals", residuals)
    assert code == 1 and out == "" and missing in err and "Traceback" not in err


@frame
def test_missing_file_and_truth_without_the_truth_column_exit_1(rc, capsys, tmp_path):
    code, out, err = run(rc, capsys, "--residuals", tmp_path / "absent.csv")
    assert code == 1 and err.startswith("ERROR:") and "Traceback" not in err
    rows, _ = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 12)}}, members=4)
    residuals, _ = save(tmp_path, rows)
    bad = write_csv(tmp_path / "bad_truth.csv", [{"OBS": "x", "VALUE": 1}], ["OBS", "VALUE"])
    code, out, err = run(rc, capsys, "--residuals", residuals, "--truth-response", bad)
    assert code == 1 and "TRUTH" in err and "Traceback" not in err


@frame
def test_truth_rows_for_other_observations_are_ignored(rc, capsys, tmp_path):
    rows, truth_rows = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 12)}}, members=4)
    truth_rows.append({"OBS": "SOMETHING_ELSE", "TRUTH": 1.0})
    residuals, truth = save(tmp_path, rows, truth_rows)
    assert report(rc, capsys, residuals, "--truth-response", truth)["separated"] is True


def test_usage_errors_exit_2(rc, capsys, tmp_path):
    assert run(rc, capsys, "--residuals", tmp_path / "residuals.csv", "--min-points", 2)[0] == 2
    with pytest.raises(SystemExit) as caught:
        rc.main([])
    assert caught.value.code == 2


@frame
def test_inputs_are_not_changed_and_nothing_is_written(rc, capsys, tmp_path):
    rows, truth_rows = make_rows({"WOPR:P1": {"noise": white(random.Random(1), 20)}}, members=4)
    residuals, truth = save(tmp_path, rows, truth_rows)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    report(rc, capsys, residuals, "--truth-response", truth)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def clean_env():
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}


BLOCKER = """
import runpy
import sys

sys.modules["numpy"] = None
sys.modules["pandas"] = None
script, *rest = sys.argv[1:]
sys.argv = [script, *rest]
runpy.run_path(script, run_name="__main__")
"""


def test_help_works_without_numpy_and_pandas_and_a_run_fails_with_a_message(tmp_path):
    runner = tmp_path / "blocked.py"
    runner.write_text(BLOCKER)
    helped = subprocess.run(
        [sys.executable, str(runner), str(SCRIPT), "--help"], capture_output=True, text=True, env=clean_env()
    )
    assert helped.returncode == 0 and "--residuals" in helped.stdout and "--truth-response" in helped.stdout
    (tmp_path / "residuals.csv").write_text("ENSEMBLE\n")
    failed = subprocess.run(
        [sys.executable, str(runner), str(SCRIPT), "--residuals", str(tmp_path / "residuals.csv")],
        capture_output=True,
        text=True,
        env=clean_env(),
    )
    assert failed.returncode == 1 and failed.stderr.startswith("ERROR:") and "pandas" in failed.stderr
    assert "Traceback" not in failed.stderr


@frame
def test_real_command_line_run(tmp_path):
    rows, truth_rows = make_rows({"WOPR:P1": {"noise": ar1(random.Random(7), 60, 0.7)}})
    residuals, truth = save(tmp_path, rows, truth_rows)
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--residuals", str(residuals), "--truth-response", str(truth)],
        capture_output=True,
        text=True,
        env=clean_env(),
    )
    assert done.returncode == 0 and done.stderr == ""
    assert AUTOCORR in done.stdout and GUIDANCE in " ".join(done.stdout.split())


@frame
def test_saved_drogon_lite_posterior_runs_and_total_is_discrepancy_minus_noise(rc, capsys, tmp_path):
    """The fixture has no dates: the month number in each OBS name stands in as a DATE. Structure only."""
    pd = pytest.importorskip("pandas")
    obs = pd.read_csv(DATA / "coverage_obs.csv")
    sim = pd.read_csv(DATA / "coverage_posterior_sim.csv.gz")
    obs = obs[obs["KEY"].str.startswith(("WOPR", "WBHP", "WWCT", "WGOR"))]
    rows = []
    for real in range(len(sim)):
        for o in obs.itertuples():
            months = int(o.OBS.rsplit("_", 1)[1])
            rows.append(
                {
                    "ENSEMBLE": "posterior",
                    "REAL": real,
                    "OBS": o.OBS,
                    "KEY": o.KEY,
                    "DATE": (FIRST_DAY + timedelta(days=30 * months)).isoformat(),
                    "OBSERVED": o.OBSERVED,
                    "ERROR": o.ERROR,
                    "SIMULATED": sim.loc[real, o.OBS],
                }
            )
    residuals = write_csv(tmp_path / "residuals.csv", rows, COLUMNS[:8])
    truth = write_csv(tmp_path / "truth.csv", obs[["OBS", "TRUTH"]].to_dict("records"), ["OBS", "TRUTH"])
    result = report(rc, capsys, residuals, "--truth-response", truth)
    assert result["n_members"] == len(sim)
    assert {s["key"] for s in result["series"]} == set(obs["KEY"])
    for key in set(obs["KEY"]):
        total, noise, disc = (pick(result, key, c) for c in ("total", "noise", "discrepancy"))
        assert total["n"] == noise["n"] == disc["n"] == int((obs["KEY"] == key).sum())
        assert total["mean"] == pytest.approx(disc["mean"] - noise["mean"], abs=1e-9)
