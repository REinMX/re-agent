"""ahm-status-report on tiny synthetic cases (resdata writer, no simulator)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from synth_fmu import need

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/ahm-status-report/scripts"
START = dt.datetime(2020, 1, 1)
STEPS = [dt.datetime(2020 + m // 12, m % 12 + 1, 1) for m in range(1, 36)]
OBS_DATES = [dt.datetime(2020, m, 1) for m in (3, 6, 9, 12)] + [dt.datetime(2021, m, 1) for m in (3, 6)]
UNITS = {"FOPR": "SM3/DAY", "FOPT": "SM3", "FGPT": "SM3", "FWPT": "SM3", "FPR": "BARSA", "WOPR": "SM3/DAY"}
UNITS |= {"WBHP": "BARSA", "WBP4": "BARSA", "FWIT": "SM3", "BPR": "BARSA"}


@pytest.fixture(scope="module")
def tool():
    need("resdata", "res2df", "pandas", "matplotlib")
    spec = importlib.util.spec_from_file_location("ahm_status", SCRIPTS / "ahm_status.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["ahm_status"] = module
    spec.loader.exec_module(module)
    return module


def write_member(runpath: Path, real: int, drop: tuple[str, ...] = (), pilot=False, outlier=None) -> None:
    from resdata.summary import Summary

    deck = runpath / "eclipse/model" / f"TOY-{real}"
    deck.parent.mkdir(parents=True, exist_ok=True)
    scale = 1.0 + 0.02 * (real - 2)
    keys = [k for k in ("FOPR", "FOPT", "FGPT", "FWPT", "FWIT", "FPR", "WOPR:P1", "WBHP:P1") if k not in drop]
    writer = Summary.writer(str(deck), START, 2, 2, 2)
    for key in keys:
        family, _, well = key.partition(":")
        writer.add_variable(family, wgname=well or None, unit=UNITS[family])
    if pilot:
        writer.add_variable("BPR", num=1, unit=UNITS["BPR"])
        keys.append("BPR:1,1,1")
    for step, when in enumerate(STEPS, start=1):
        days = float((when - START).days)
        values = {
            "FOPR": 100 * scale,
            "FOPT": 100 * scale * days,
            "FGPT": 1e4 * scale * days,
            "FWPT": 20 * scale * days,
            "FWIT": 50 * days,
            "FPR": 250 - 0.02 * days,
            "BPR:1,1,1": 240 - 0.01 * scale * days,
            "WOPR:P1": (150 if real == outlier else 100) * scale,
            "WBHP:P1": 150 - 0.01 * days,
        }
        tstep = writer.add_t_step(step, days)
        for key in keys:
            tstep[key] = values[key]
    writer.fwrite()
    deck.with_suffix(".DATA").write_text("RUNSPEC\n")
    (runpath / "parameters.txt").write_text(
        f"GLOBVAR:PORO {0.02 + 0.001 * real}\nGLOBVAR:KMULT {0.4 + 0.05 * real}\n"
    )
    (runpath / "OK").write_text("ok\n")


def make_case(root: Path, iterations=(3,), n: int = 5, failed=(), drop=(), pilot=False, outlier=None) -> Path:
    case = root / "scratch" / "case"
    for iteration in iterations:
        for real in range(n):
            runpath = case / f"realization-{real}" / f"iter-{iteration}"
            if real in failed:
                runpath.mkdir(parents=True)
                (runpath / "ERROR").write_text("FLOW failed\n")
            else:
                write_member(runpath, real, drop, pilot, outlier)
    obs = root / "case.obs"
    obs.write_text(
        "".join(
            f"SUMMARY_OBSERVATION WOPR_P1_{i} {{ VALUE = 101; ERROR = 10; DATE = {d:%Y-%m-%d}; KEY = WOPR:P1; }};\n"
            f"SUMMARY_OBSERVATION WBHP_P1_{i} {{ VALUE = {150 - 0.01 * (d - START).days:.3f}; ERROR = 2; "
            f"DATE = {d:%Y-%m-%d}; KEY = WBHP:P1; }};\n"
            for i, d in enumerate(OBS_DATES)
        )
        + (
            "SUMMARY_OBSERVATION BPR_PILOT { VALUE = 236; ERROR = 2; DATE = 2021-03-01; KEY = BPR:1,1,1; };\n"
            if pilot
            else ""
        )
    )
    (root / "params.dist").write_text("PORO UNIFORM 0.0 1.0\nKMULT LOGUNIF 0.1 10\n")
    return case


def run(tool, root: Path, case: Path, *extra: str) -> tuple[int, Path]:
    outdir = root / "out"
    code = tool.main(
        [
            "--case",
            str(case),
            "--obs",
            str(root / "case.obs"),
            "--dist",
            str(root / "params.dist"),
            "--eclbase",
            "eclipse/model/TOY-<IENS>",
            "--expected",
            "5",
            "--outdir",
            str(outdir),
            *extra,
        ]
    )
    return code, outdir


@pytest.fixture(scope="module")
def posterior_only(tool, tmp_path_factory):
    root = tmp_path_factory.mktemp("posterior_only")
    case = make_case(root, failed=(3,))
    code, outdir = run(tool, root, case)
    assert code == 0
    return outdir, json.loads((outdir / "status.json").read_text())


def test_posterior_only_says_prior_is_gone_and_shows_no_prior(posterior_only):
    outdir, status = posterior_only
    assert status["mode"] == "posterior-only"
    assert status["prior_deleted"] is True
    assert "iter-0 (prior) is gone" in (outdir / "summary.md").read_text()
    report = (outdir / "report.md").read_text()
    assert "No prior comparison: iter-0 (prior) is gone." in report
    manifest = json.loads((outdir / "misfit/main/manifest.json").read_text())
    assert list(manifest["ensembles"]) == ["posterior"]
    parameters = (outdir / "tables/parameters.csv").read_text().splitlines()[0]
    assert "PRIOR_SD_POS" not in parameters
    assert "POSTERIOR-ONLY" in [e["code"] for e in status["evidence"]]


def test_failed_realizations_are_counted(posterior_only):
    outdir, status = posterior_only
    assert status["counts"]["posterior_ok"] == 4
    assert status["counts"]["posterior_failed"] == [3]
    assert status["counts"]["posterior_missing"] == []
    inventory = (outdir / "tables/inventory.csv").read_text()
    assert ",4,1,0,3," in inventory
    assert "REALIZATIONS-PARTIAL" in [e["code"] for e in status["evidence"]]


def test_pressure_fallback_and_missing_history_are_reported(posterior_only):
    outdir, status = posterior_only
    assert status["pressure_mnemonic"] == {"P1": "WBHP"}
    assert "WOPRH" in status["missing_vectors"]["history"]
    assert "Mnemonic per well (order WBP9 > WBP4 > WBP > WBHP): P1 WBHP" in (outdir / "report.md").read_text()


def test_outputs_and_convention(posterior_only):
    outdir, status = posterior_only
    for name in ("summary.md", "report.md", "report.pdf", "plots/field.png", "plots/pressure.png"):
        assert (outdir / name).is_file(), name
    assert status["convention"] == "P10/P50/P90 convention: numerical, P10 is the LOW value."
    assert status["end_of_history"] == "2021-06-01"
    assert "never passes DG0" in (outdir / "summary.md").read_text()
    import pandas as pd

    forecast = pd.read_csv(outdir / "tables/forecast_pXX.csv")
    fopt = forecast[(forecast["VECTOR"] == "FOPT") & (forecast["AT"] == "end of history")].iloc[0]
    assert fopt["P10"] < fopt["P50"] < fopt["P90"]
    assert set(forecast["AT"]) == {"end of history", "last simulated date"}


def test_parameter_at_bound_from_dist_bounds(posterior_only):
    _, status = posterior_only
    assert status["parameters_at_bound"] == ["PORO"]
    evidence = {e["code"]: e for e in status["evidence"]}
    assert "PORO" in evidence["PARAMETER-AT-BOUND"]["evidence"]
    assert evidence["PARAMETER-AT-BOUND"]["level"] == "Use with caveats"


def test_missing_summary_vector_is_missing_not_zero(tool, tmp_path):
    case = make_case(tmp_path, drop=("FWPT",))
    code, outdir = run(tool, tmp_path, case)
    assert code == 0
    status = json.loads((outdir / "status.json").read_text())
    assert "FWPT" in status["missing_vectors"]["required"]
    assert status["verdict"] == "Not ready"
    import pandas as pd

    rows = pd.read_csv(outdir / "tables/forecast_pXX.csv").query("VECTOR == 'FWPT'")
    assert set(rows["STATUS"]) == {"missing in SUMMARY"}
    assert rows["P50"].isna().all()


def test_full_mode_compares_prior_and_iterations(tool, tmp_path):
    case = make_case(tmp_path, iterations=(0, 1, 2))
    code, outdir = run(tool, tmp_path, case)
    assert code == 0
    status = json.loads((outdir / "status.json").read_text())
    assert status["mode"] == "full" and status["prior_deleted"] is False
    assert status["counts"]["posterior_iteration"] == 2
    assert "Prior (iter-0) for comparison" in (outdir / "report.md").read_text()
    medians = (outdir / "tables/iteration_misfit.csv").read_text().splitlines()
    assert [line.split(",")[0] for line in medians[1:]] == ["0", "1", "2"]


def test_share_needs_write_share(tool, tmp_path):
    case = make_case(tmp_path)
    inside = case / "share" / "results" / "ahm_status"
    code = tool.main(["--case", str(case), "--obs", str(tmp_path / "case.obs"), "--outdir", str(inside)])
    assert code == 2
    assert not inside.exists()
    code = tool.main(
        [
            "--case",
            str(case),
            "--obs",
            str(tmp_path / "case.obs"),
            "--eclbase",
            "eclipse/model/TOY-<IENS>",
            "--write-share",
        ]
    )
    assert code == 0
    assert (inside / "status.json").is_file()


def facts(tool, **over):
    base = dict(
        mode="full",
        expected=100,
        ok_members=100,
        coverage=0.9,
        chi2_verdict="inside the noise band",
        spread_verdict="inside the spread-aware reference",
        mean_misfit=1.0,
        required_missing=[],
        review_items=0,
        worst_well="P1",
        worst_well_msr=1.5,
    )
    return tool.Facts(**(base | over))


@pytest.mark.parametrize(
    ("over", "level", "code"),
    [
        ({}, "Fit for use", None),
        ({"ok_members": 79}, "Not ready", "REALIZATIONS-LOW"),
        ({"coverage": 0.55}, "Not ready", "COVERAGE-LOW"),
        (
            {"coverage": 0.75, "spread_verdict": "above the spread-aware reference"},
            "Not ready",
            "MISFIT-ABOVE-REFERENCE",
        ),
        ({"required_missing": ["FOPT"]}, "Not ready", "REQUIRED-VECTOR-MISSING"),
        ({"mode": "posterior-only"}, "Use with caveats", "POSTERIOR-ONLY"),
        ({"ok_members": 95}, "Use with caveats", "REALIZATIONS-PARTIAL"),
        ({"coverage": 0.99}, "Use with caveats", "COVERAGE-OUTSIDE"),
        ({"at_bound": ["PORO"]}, "Use with caveats", "PARAMETER-AT-BOUND"),
        ({"worst_well_msr": 4.5}, "Use with caveats", "WELL-MISFIT-HIGH"),
        ({"review_items": 1}, "Use with caveats", "REVIEW-ITEMS"),
        ({"spread_verdict": "above the spread-aware reference"}, "Fit for use", None),
    ],
)
def test_verdict_rules(tool, over, level, code):
    result, fired = tool.verdict(facts(tool, **over))
    assert result == level
    codes = [f["code"] for f in fired]
    assert code in codes if code else codes == []


def test_pressure_order(tool):
    import pandas as pd

    frame = pd.DataFrame(columns=["WBP4:P1", "WBHP:P1", "WBHP:P2", "WBP9:P3", "WBP:P3"])
    assert tool.pressure_choice({0: frame}, ["P1", "P2", "P3", "P4"]) == {
        "P1": "WBP4",
        "P2": "WBHP",
        "P3": "WBP9",
    }


def test_single_iter0_is_not_called_a_deleted_prior(tool, tmp_path):
    only_iter0 = tool.Case(path=tmp_path, expected=(), expected_source="test", iterations={0: {}})
    assert only_iter0.mode == "posterior-only"
    assert not only_iter0.prior_deleted
    assert "only one iteration" in only_iter0.no_prior_note
    iter3 = tool.Case(path=tmp_path, expected=(), expected_source="test", iterations={3: {}})
    assert iter3.prior_deleted
    assert iter3.no_prior_note == "iter-0 (prior) is gone"


def test_known_false_alarms_and_info_are_shown_but_not_counted(tool):
    false_alarm = tool.ReviewItem("misfit_report", "CHI2-ABOVE-NOISE", "mean misfit 2.04", True)
    info = tool.ReviewItem("check_ert_config --ahm", "INFO-WEIGHTS", "weights valid", info=True)
    real = tool.ReviewItem("physics_checklist", "PHYS-ROCK", "fixed-not-uncertain")
    assert not false_alarm.counts and not info.counts and real.counts
    assert "not counted" in false_alarm.label and info.label == "INFO"
    level, fired = tool.verdict(facts(tool, review_items=sum(r.counts for r in (false_alarm, info))))
    assert level == "Fit for use" and fired == []


def test_rescaled_weights_lint_reads_as_info(tool, monkeypatch, tmp_path):
    line = (
        "ahm review drogonlite_ahm.ert: ES-MDA weights 4, 2, 1 (--weights): sum(1/w) = 1.75, not 1. "
        "ERT 23 rescales them to 7, 3.5, 1.75. Give the rescaled weights so that the log and the plan agree"
    )
    import subprocess

    monkeypatch.setattr(
        tool, "run_tool", lambda command: subprocess.CompletedProcess(command, 0, line + "\n", "")
    )
    (item,) = tool.lint_items(tmp_path / "x.ert", "4, 2, 1")
    assert item.info and item.code == "INFO-WEIGHTS"
    assert "are valid" in item.text and "7, 3.5, 1.75" in item.text and "Give the rescaled" not in item.text


def test_chi2_counts_only_above_the_spread_aware_reference(tool):
    assert tool.chi2_is_known_false_alarm({"spread_verdict": "inside the spread-aware reference"})
    assert not tool.chi2_is_known_false_alarm({"spread_verdict": "above the spread-aware reference"})
    assert not tool.chi2_is_known_false_alarm({})


def test_parameter_at_bound_counts_while_known_false_alarms_do_not(tool):
    edge = tool.ReviewItem("compensation_check", "EDGE", "PORO at edge", True)
    lint = tool.ReviewItem("check_ert_config --ahm", "LINT-REVIEW", "NUM_REALIZATIONS 50", True)
    chi2 = tool.ReviewItem("misfit_report", "CHI2-ABOVE-NOISE", "inside the spread-aware reference", True)
    counted = sum(r.counts for r in (edge, lint, chi2))
    assert tool.verdict(facts(tool, review_items=counted))[0] == "Fit for use"
    level, fired = tool.verdict(facts(tool, review_items=counted, at_bound=["MULTFLT_F1"]))
    assert level == "Use with caveats"
    assert [f["code"] for f in fired] == ["PARAMETER-AT-BOUND"]
    assert "MULTFLT_F1" in fired[0]["evidence"]


def test_gen_kw_dist_any_form_resolves_from_the_including_file(tool, tmp_path):
    (tmp_path / "dists").mkdir()
    (tmp_path / "include").mkdir()
    (tmp_path / "model.ert").write_text("NUM_REALIZATIONS 5\nINCLUDE include/params.ert\n")
    (tmp_path / "include/params.ert").write_text(
        "GEN_KW ROCK templates/rock.tmpl rock.inc ../dists/rock_priors.txt\n"
        "GEN_KW FLUID ../dists/fluid.dist INIT_FILES:fluid%d.txt UPDATE:FALSE\n"
        "GEN_KW PRED pred.tmpl pred.inc pred.txt FORWARD_INIT:False\n"
    )
    settings = tool.read_ert_config(tmp_path / "model.ert")
    assert settings.gen_kw == 3
    assert settings.dist_files == [
        (tmp_path / "dists/rock_priors.txt").resolve(),
        (tmp_path / "dists/fluid.dist").resolve(),
        (tmp_path / "include/pred.txt").resolve(),
    ]


def test_unreadable_dist_is_a_visible_warn_and_never_a_bare_none(tool, tmp_path):
    case = make_case(tmp_path)
    ert = tmp_path / "model.ert"
    ert.write_text("NUM_REALIZATIONS 5\nGEN_KW GLOBVAR tmpl.tmpl out.inc missing_priors.txt\n")
    outdir = tmp_path / "out"
    code = tool.main(
        ["--case", str(case), "--obs", str(tmp_path / "case.obs"), "--ert-config", str(ert)]
        + ["--eclbase", "eclipse/model/TOY-<IENS>", "--expected", "5", "--outdir", str(outdir)]
    )
    assert code == 0
    warn = "parameter-at-bound check could not run: 2 params, no readable dist"
    status = json.loads((outdir / "status.json").read_text())
    assert status["parameter_bound_check"]["warn"] and warn in status["parameter_bound_check"]["text"]
    (item,) = [r for r in status["review_items"] if r["code"] == "PARAM-BOUND-NOT-RUN"]
    assert item["level"] == "WARN" and item["not_counted_reason"]
    assert "PARAM-BOUND-NOT-RUN" not in json.dumps(status["evidence"])
    report = (outdir / "report.md").read_text()
    section3 = report.split("## 3.")[1].split("## 4.")[0]
    assert warn in section3 and "_none_" not in section3
    assert warn in (outdir / "summary.md").read_text()
    assert warn in (outdir / "tables/findings.csv").read_text()


def test_bound_check_that_ran_says_how_many_it_checked(posterior_only):
    outdir, status = posterior_only
    section3 = (outdir / "report.md").read_text().split("## 3.")[1].split("## 4.")[0]
    assert "1 parameter(s) at a bound (2 checked): PORO." in section3


@pytest.fixture(scope="module")
def pilot_case(tool, tmp_path_factory):
    root = tmp_path_factory.mktemp("pilot")
    case = make_case(root, pilot=True, outlier=4)
    code, outdir = run(tool, root, case)
    assert code == 0
    return outdir, json.loads((outdir / "status.json").read_text())


def test_block_observations_are_not_wells(tool, pilot_case):
    import pandas as pd

    outdir, status = pilot_case
    wells = pd.read_csv(outdir / "tables/well_misfit.csv")
    assert list(wells["WELL"]) == ["P1"]
    residuals = pd.read_csv(outdir / "misfit/main/residuals.csv")
    assert list(tool.heatmap_table(residuals).index) == ["P1"]
    blocks = status["block_observations"]
    assert blocks["keys"] == ["BPR:1,1,1"] and blocks["observations"] == 1
    assert blocks["mean_sq_norm_residual"] > 0 and blocks["coverage_p5_p95"] is not None
    assert (
        "Pilot pressure (block) observations, not wells: 1 observation(s) on BPR:1,1,1"
        in (outdir / "report.md").read_text()
    )


def test_pilot_pressure_panel_replaces_the_no_rft_message(tool, pilot_case):
    outdir, status = pilot_case
    assert status["pilot_pressure_keys"] == ["BPR:1,1,1"]
    plots = tool._load(SCRIPTS / "ahm_status_plots.py")
    case = Path(status["case"])
    frames = tool.load_frames(tool.scan_case(case, "eclipse/model/TOY-<IENS>", 5, None, {}), 3, {"BPR:1,1,1"})
    context = plots.Context("t", None, tool.read_observations([outdir.parent / "case.obs"]))
    figure = plots.pressure_grid(frames, {"P1": "WBHP"}, ["P1"], context, None, ["BPR:1,1,1"])
    titles = [ax.get_title() for ax in figure.axes if ax.get_visible()]
    texts = [t.get_text() for ax in figure.axes for t in ax.texts]
    assert any(t.startswith("BPR:1,1,1") for t in titles)
    assert not any("no RFT" in t for t in texts)
    pilot_ax = next(ax for ax in figure.axes if ax.get_title().startswith("BPR:1,1,1"))
    assert any(c.get_label() == "observed" for c in pilot_ax.containers)
    plots.close(figure)
    figure = plots.pressure_grid(frames, {"P1": "WBHP"}, ["P1"], context, None)
    assert any("no RFT" in t.get_text() for ax in figure.axes for t in ax.texts)
    plots.close(figure)


def test_wbhp_fallback_is_called_flowing_bottom_hole_pressure(posterior_only):
    outdir, status = posterior_only
    assert status["pressure_meaning"]["WBHP"].startswith("flowing bottom-hole pressure (WBHP)")
    assert (
        "WBHP is flowing bottom-hole pressure (WBHP), not average reservoir pressure."
        in (outdir / "report.md").read_text()
    )


def test_outlier_realization_is_named_but_not_counted(tool, pilot_case):
    outdir, status = pilot_case
    assert [o["realization"] for o in status["misfit_outliers"]] == [4]
    assert "Outlier realizations" in (outdir / "summary.md").read_text()
    assert "OUTLIER" not in json.dumps(status["evidence"]).upper()
    import pandas as pd

    flat = pd.DataFrame({"ENSEMBLE": ["posterior"] * 4, "REAL": [0, 1, 2, 3], "MISFIT": [1.0, 1.0, 1.0, 9.0]})
    assert tool.misfit_outliers(flat) == []


def test_outlier_rule_ignores_a_mild_tail_and_names_the_far_member(tool):
    import pandas as pd

    misfit = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.4, 6.0]
    frame = pd.DataFrame({"ENSEMBLE": ["posterior"] * 10, "REAL": range(10), "MISFIT": misfit})
    assert [o["realization"] for o in tool.misfit_outliers(frame)] == [9]


def test_forecast_has_smspec_units_and_marks_history_controlled_vectors(posterior_only):
    import pandas as pd

    outdir, status = posterior_only
    forecast = pd.read_csv(outdir / "tables/forecast_pXX.csv").set_index(["VECTOR", "AT"])
    assert forecast.loc[("FOPT", "end of history"), "UNIT"] == "SM3"
    assert forecast.loc[("FOPT", "end of history"), "STATUS"] == "ok"
    assert forecast.loc[("FWIT", "end of history"), "STATUS"] == "history-controlled (zero spread)"
    assert status["history_controlled"] == ["FWIT"]
    summary = (outdir / "summary.md").read_text()
    assert "| FOPT | SM3 |" in summary
    assert "set by the history controls, not by the history match" in summary


def test_water_cut_axis_follows_the_data_not_the_error_bars(tool):
    import pandas as pd

    plots = tool._load(SCRIPTS / "ahm_status_plots.py")
    dates = pd.to_datetime(STEPS[:12])
    frames = {r: pd.DataFrame({"WWCT:P1": [0.01 + 0.002 * r] * 12}, index=dates) for r in range(3)}
    obs = [{"key": "WWCT:P1", "date": "2020-06-01", "value": 0.012, "error": 0.5}]
    figure = plots.plt.figure()
    ax = figure.add_subplot()
    plots.band(ax, frames, "WWCT:P1", plots.Context("t", None, obs))
    low, high = ax.get_ylim()
    assert 0 <= low <= 0.01 and 0.014 <= high < 0.1
    plots.close(figure)


def test_missing_aquifer_and_well_pi_are_shown_not_counted(tool, monkeypatch, tmp_path):
    import subprocess

    items = [
        {"code": "PHYS-AQUIFER", "status": "missing", "summary": "no aquifer"},
        {"code": "PHYS-WELL-PI", "status": "missing", "summary": "no WPIMULT"},
        {"code": "PHYS-ROCK", "status": "fixed-not-uncertain", "summary": "1 ROCK record"},
    ]
    monkeypatch.setattr(
        tool,
        "run_tool",
        lambda command: subprocess.CompletedProcess(command, 0, json.dumps({"items": items}), ""),
    )
    review, _ = tool.physics_items(tmp_path / "X.DATA", None)
    counted = {r.code: r.counts for r in review}
    assert counted == {"PHYS-AQUIFER": False, "PHYS-WELL-PI": False, "PHYS-ROCK": True}
    assert "design choice" in review[0].label and "not counted" in review[0].label
    level, fired = tool.verdict(facts(tool, review_items=sum(r.counts for r in review[:2])))
    assert level == "Fit for use" and fired == []


def test_only_the_member_count_lint_is_a_known_false_alarm(tool, monkeypatch, tmp_path):
    import subprocess

    lines = [
        "ahm review x.ert: NUM_REALIZATIONS 50 (x.ert:18) is below 100. The update statistics are weak.",
        "ahm review x.ert: MIN_REALIZATIONS is not set. ERT 23 then needs all realizations to succeed",
        "ahm review x.ert: MIN_REALIZATIONS 80% lets the update continue with 40 realizations",
    ]
    monkeypatch.setattr(
        tool, "run_tool", lambda command: subprocess.CompletedProcess(command, 0, "\n".join(lines), "")
    )
    num, unset, share = tool.lint_items(tmp_path / "x.ert", "unknown")
    assert num.calibration_false_alarm and "known false alarm" in num.label
    for item in (unset, share):
        assert not item.calibration_false_alarm and not item.counts
        assert "known false alarm" not in item.label and "not calibrated" in item.label
    assert (
        tool.verdict(facts(tool, review_items=sum(r.counts for r in (num, unset, share))))[0] == "Fit for use"
    )


def test_counted_review_items_are_named(posterior_only):
    outdir, status = posterior_only
    (rule,) = [e for e in status["evidence"] if e["code"] == "REVIEW-ITEMS"]
    counted = [r["code"] for r in status["review_items"] if not r["not_counted_reason"] and not r["info"]]
    assert "PHYS-ROCK" in counted and "PHYS-AQUIFER" not in counted
    assert f"counted review item(s): {', '.join(dict.fromkeys(counted))}" in rule["evidence"]
    assert rule["evidence"] in (outdir / "summary.md").read_text()
