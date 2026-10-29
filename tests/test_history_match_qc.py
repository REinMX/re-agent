import contextlib
import importlib.util
import io
import json
import os
import sys
from argparse import Namespace
from datetime import datetime
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

SCRIPTS = Path(__file__).parents[1] / "skills/history-match-qc/scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


obs_qc = load("obs_qc")
pytest.importorskip("res2df")
misfit = load("misfit_report")


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def normalize(tmp_path: Path, text: str) -> list[dict]:
    file = write(tmp_path / "t.obs", text)
    return [
        obs_qc.normalize_summary(r) for r in obs_qc.parse_file(file) if r["kind"] == "SUMMARY_OBSERVATION"
    ]


def run_qc(*args: str) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = obs_qc.main([*args, "--json"])
    return code, json.loads(buffer.getvalue()) if buffer.getvalue().strip() else {}


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ("VALUE = 100; ERROR = 5;", 5.0),
        ("VALUE = 100; ERROR = -5;", 5.0),
        ("VALUE = -200; ERROR = 0.1; ERROR_MODE = REL;", 20.0),
        ("VALUE = 10; ERROR = 0.01; ERROR_MODE = RELMIN;", 0.1),
        ("VALUE = 1000; ERROR = 0.05; ERROR_MODE = RELMIN; ERROR_MIN = 2;", 50.0),
        ("VALUE = 1; ERROR = 0.01; ERROR_MODE = RELMIN; ERROR_MIN = 2;", 2.0),
    ],
)
def test_error_modes(tmp_path, fields, expected):
    (obs,) = normalize(tmp_path, f"SUMMARY_OBSERVATION A {{ {fields} DATE = 2020-01-01; KEY = WOPR:A; }};")
    assert obs["error"] == pytest.approx(expected)
    assert not [i for i in obs["issues"] if i["severity"] == "error"]


def test_zero_error_is_error(tmp_path):
    (obs,) = normalize(
        tmp_path,
        "SUMMARY_OBSERVATION A { VALUE = 0; ERROR = 0.1; ERROR_MODE = REL; DATE = 2020-01-01; KEY = K; };",
    )
    assert any("larger than 0" in i["message"] for i in obs["issues"])


def test_missing_fields_and_bad_date(tmp_path):
    (obs,) = normalize(tmp_path, "SUMMARY_OBSERVATION A { VALUE = 1; DATE = 2020-13-45; };")
    messages = " ".join(i["message"] for i in obs["issues"])
    assert "missing KEY" in messages and "missing ERROR" in messages and "DATE cannot be read" in messages


def test_unknown_error_mode(tmp_path):
    (obs,) = normalize(
        tmp_path,
        "SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 1; ERROR_MODE = FOO; DATE = 2020-01-01; KEY = K; };",
    )
    assert any("unknown ERROR_MODE" in i["message"] for i in obs["issues"])


@pytest.mark.parametrize(
    "time", ["DAYS = 30;", "HOURS = 720;", "RESTART = 1;", "DATE = 2020-01-31; DAYS = 30;"]
)
def test_legacy_time_keyword_is_error(tmp_path, time):
    # ERT 23 stops at parse on DAYS, HOURS or RESTART, also next to DATE. A warning let the run fail later.
    (obs,) = normalize(tmp_path, f"SUMMARY_OBSERVATION A {{ VALUE = 1; ERROR = 1; {time} KEY = K; }};")
    assert [i["severity"] for i in obs["issues"]] == ["error"]
    assert "ert convert_observations" in obs["issues"][0]["message"]


def test_ert_rejects_legacy_time_keyword(tmp_path):
    """The rule above follows ERT. If this ERT accepts DAYS, the error severity is wrong for it."""
    pytest.importorskip("ert")
    write(tmp_path / "m.obs", "SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 1; DAYS = 30; KEY = FOPR; };\n")
    write(tmp_path / "m.ert", "NUM_REALIZATIONS 1\nECLBASE X\nSUMMARY *\nOBS_CONFIG m.obs\n")
    from ert.config import ErtConfig

    with pytest.raises(Exception, match="convert_observations"):
        ErtConfig.from_file(str(tmp_path / "m.ert"))


def test_unreadable_case_is_usage_error(tmp_path, capsys):
    # A --case without summary data (for example an include deck named .DATA) crashed with an
    # AttributeError and exit 1, the code of an observation error.
    good = write(
        tmp_path / "g.obs", "SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 1; DATE = 2020-01-01; KEY = K; };"
    )
    no_summary = write(tmp_path / "PERMVALUES.DATA", "PERMX\n 1 /\n")
    assert obs_qc.main([str(good), "--case", str(tmp_path / "none.DATA")]) == 2
    assert obs_qc.main([str(good), "--case", str(no_summary)]) == 2
    assert "--case" in capsys.readouterr().err


def test_date_formats(tmp_path):
    a, b = normalize(
        tmp_path,
        "SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 1; DATE = 15/03/2021; KEY = K; };\n"
        "SUMMARY_OBSERVATION B { VALUE = 1; ERROR = 1; DATE = 2021-03-15T10:30:00; KEY = K; };",
    )
    assert a["date"] == "2021-03-15T00:00:00" and b["date"] == "2021-03-15T10:30:00"


def test_comments_and_quoted_dashes_are_handled(tmp_path):
    text = "-- SUMMARY_OBSERVATION X { VALUE = 1; };\nSUMMARY_OBSERVATION A { VALUE = 1; ERROR = 1; DATE = 2020-01-01; KEY = K; }; -- trailing\n"
    assert len(normalize(tmp_path, text)) == 1


def test_duplicates_and_dense_group(tmp_path):
    lines = [
        f"SUMMARY_OBSERVATION D{i} {{ VALUE = {i}; ERROR = 1; DATE = 2020-01-01T00:00:{i:02d}; KEY = WBHP:A; }};"
        for i in range(12)
    ]
    lines.append(
        "SUMMARY_OBSERVATION D0b { VALUE = 5; ERROR = 1; DATE = 2020-01-01T00:00:00; KEY = WBHP:A; };"
    )
    code, report = run_qc(str(write(tmp_path / "t.obs", "\n".join(lines))))
    messages = " ".join(p["message"] for p in report["problems"])
    assert code == 0
    assert "repeats key" in messages and "13 observations of WBHP:A" in messages


def test_history_observation_is_reported_not_evaluated(tmp_path):
    text = "HISTORY_OBSERVATION FOPR { ERROR = 0.1; ERROR_MODE = REL; SEGMENT S { START = 1; STOP = 5; ERROR = 0.2; }; };"
    code, report = run_qc(str(write(tmp_path / "t.obs", text)))
    assert code == 0
    assert any("HISTORY_OBSERVATION" in p["message"] for p in report["problems"])
    assert report["other_blocks"][0]["name"] == "FOPR"


def test_cli_exit_codes(tmp_path):
    good = write(
        tmp_path / "g.obs", "SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 1; DATE = 2020-01-01; KEY = K; };"
    )
    bad = write(
        tmp_path / "b.obs", "SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 0; DATE = 2020-01-01; KEY = K; };"
    )
    empty = write(tmp_path / "e.obs", "-- nothing\n")
    assert run_qc(str(good))[0] == 0
    assert run_qc(str(bad))[0] == 1
    assert obs_qc.main([str(empty)]) == 2
    assert obs_qc.main([str(tmp_path / "none.obs")]) == 2


def test_obs_qc_agrees_with_ert(tmp_path):
    """The error and the date must equal what ERT computes for the same file."""
    pytest.importorskip("ert")
    text = (
        "SUMMARY_OBSERVATION A_abs { VALUE = 100; ERROR = 5; DATE = 2020-01-01; KEY = WOPR:A; };\n"
        "SUMMARY_OBSERVATION A_rel { VALUE = -200; ERROR = 0.1; ERROR_MODE = REL; DATE = 2020-02-01; KEY = WOPR:A; };\n"
        "SUMMARY_OBSERVATION A_rm { VALUE = 10; ERROR = 0.01; ERROR_MODE = RELMIN; DATE = 2020-03-01; KEY = WOPR:A; };\n"
        "SUMMARY_OBSERVATION A_rm2 { VALUE = 1000; ERROR = 0.05; ERROR_MODE = RELMIN; ERROR_MIN = 2; DATE = 2020-04-01; KEY = WOPR:A; };\n"
    )
    write(tmp_path / "m.obs", text)
    write(tmp_path / "m.ert", "NUM_REALIZATIONS 1\nECLBASE X\nSUMMARY *\nOBS_CONFIG m.obs\n")
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    from ert.config import ErtConfig
    from ert.plugins import get_site_plugins

    try:
        config = ErtConfig.with_plugins(get_site_plugins()).from_file(str(tmp_path / "m.ert"))
        declared = {d.name: d for d in config.observation_declarations}
    except Exception as error:  # a different ERT version can change this internal API
        pytest.skip(f"cannot read observations from this ERT version: {error}")
    mine = {o["name"]: o for o in normalize(tmp_path, text)}
    assert set(declared) == set(mine)
    for name, theirs in declared.items():
        assert mine[name]["error"] == pytest.approx(theirs.error)
        assert mine[name]["date"] == theirs.date


def frame(values: dict[str, list[float]], dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.to_datetime(dates))


def test_simulated_at_statuses():
    f = frame({"K": [0.0, 10.0, 20.0]}, ["2020-01-01", "2020-01-11", "2020-01-21"])
    assert misfit.simulated_at(f, "K", datetime(2020, 1, 11)) == (10.0, "exact")
    value, status = misfit.simulated_at(f, "K", datetime(2020, 1, 6))
    assert status == "interpolated" and value == pytest.approx(5.0)
    assert misfit.simulated_at(f, "K", datetime(2021, 1, 1)) == (None, "outside")
    assert misfit.simulated_at(f, "NOPE", datetime(2020, 1, 11)) == (None, "missing_key")


@pytest.mark.parametrize(
    ("key", "rule"),
    [
        ("FOPR", "step"),
        ("WOPR:OP_1", "step"),
        ("WWIR:INJ", "step"),
        ("GGPR:G1", "step"),
        ("WWCT:OP_1", "step"),
        ("WGOR:OP_1", "step"),
        ("WOPRH:OP_1", "step"),
        ("FOPT", "linear"),
        ("WOPT:OP_1", "linear"),
        ("FPR", "linear"),
        ("WBHP:OP_1", "linear"),
        ("WTHP:OP_1", "linear"),
        ("RPR:1", "linear"),
    ],
)
def test_time_rule(key, rule):
    assert misfit.time_rule(key) == rule


def test_rate_uses_value_at_end_of_step():
    # Values from OPM Flow (SPE1): ORAT changed from 20000 to 5000 at 2015-04-01. The summary keeps 20000 at
    # 2015-04-01 (mean over March) and shows 5000 at the end of the next step.
    dates = ["2015-03-01", "2015-04-01", "2015-04-11"]
    f = frame({"FOPR": [20000.0, 20000.0, 5000.0], "FOPT": [0.0, 620000.0, 670000.0]}, dates)
    assert misfit.simulated_at(f, "FOPR", datetime(2015, 4, 5)) == (5000.0, "step")
    assert misfit.simulated_at(f, "FOPR", datetime(2015, 4, 1)) == (20000.0, "exact")
    value, status = misfit.simulated_at(f, "FOPT", datetime(2015, 4, 6))
    assert status == "interpolated" and value == pytest.approx(645000.0)


def make_table(label: str, sims: list[float], observed=10.0, error=1.0, obs="O1") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ENSEMBLE": label,
                "REAL": i,
                "OBS": obs,
                "KEY": "K",
                "DATE": "2020-01-01T00:00:00",
                "OBSERVED": observed,
                "ERROR": error,
                "SIMULATED": s,
                "STATUS": "exact",
                "RESIDUAL": (s - observed) / error,
            }
            for i, s in enumerate(sims)
        ]
    )


def args(**over):
    base = {
        "collapse_ratio": 0.1,
        "residual_limit": 2.0,
        "worse_fraction": 0.3,
        "worse_margin": 0.5,
        "worse_single": 1.0,
        "shift_limit": 2.0,
        "std_ratio_limit": 0.2,
    }
    base.update(over)
    return Namespace(**base)


def analyse(prior_sims, post_sims, **over):
    table = pd.concat([make_table("prior", prior_sims), make_table("posterior", post_sims)])
    summary = misfit.obs_summary(table)
    return (
        summary,
        misfit.real_misfit(table),
        misfit.findings(summary, misfit.real_misfit(table), None, args(**over), ["prior", "posterior"]),
    )


def test_improvement_is_not_reported_as_worse():
    """Regression: ensemble order was alphabetical, which swapped prior and posterior."""
    _, _, found = analyse([20.0, 21.0, 22.0], [10.2, 9.8, 10.0])
    text = " ".join(f["finding"] for f in found)
    assert "misfit is higher" not in text and "got clearly worse" not in text


def test_degradation_is_reported():
    _, _, found = analyse([10.2, 9.8, 10.0], [20.0, 21.0, 22.0])
    assert any(f["finding"] == "mean misfit is higher than in the prior" for f in found)
    single = [f for f in found if f["finding"] == "observation got clearly worse"]
    assert single and "O1" in single[0]["examples"][0]


def test_tiny_worsening_is_not_reported():
    """Regression: a change of 0.005 errors on zero observations is noise."""
    _, _, found = analyse([10.001, 10.0, 9.999], [10.006, 10.0, 9.994])
    assert not any("worse" in f["finding"] or "moved away" in f["finding"] for f in found)


def test_out_of_reach_needs_distance_in_errors():
    near = analyse([10.4, 10.5, 10.6], [10.4, 10.5, 10.6])[2]  # outside min-max, but only 0.5 errors away
    far = analyse([14.0, 15.0, 16.0], [14.0, 15.0, 16.0])[2]
    assert not any(f["finding"] == "observation out of reach" for f in near)
    assert any(f["finding"] == "observation out of reach" for f in far)


def test_collapse_away_from_observation():
    _, _, found = analyse([15.0, 15.001, 14.999], [15.0, 15.001, 14.999])
    assert any("spread is much smaller" in f["finding"] for f in found)


def test_misfit_is_mean_of_squares():
    table = make_table("posterior", [12.0, 8.0, 10.0])  # residuals 2, -2, 0
    out = misfit.real_misfit(table).set_index("REAL")["MISFIT"]
    assert out.to_dict() == {0: 4.0, 1: 4.0, 2: 0.0}


def test_parameter_shift_numbers():
    prior = {i: {"A": float(v), "B": 5.0} for i, v in enumerate([0, 2, 4, 6])}
    post = {i: {"A": float(v), "B": 5.0} for i, v in enumerate([5, 5.1, 4.9, 5.0])}
    out = misfit.parameter_shift(prior, post).set_index("PARAMETER")
    prior_std = (sum((v - 3) ** 2 for v in [0, 2, 4, 6]) / 4) ** 0.5
    assert out.loc["A", "SHIFT_IN_PRIOR_STD"] == pytest.approx((5.0 - 3.0) / prior_std)
    assert out.loc["A", "STD_RATIO"] < 0.2
    assert pd.isna(out.loc["B", "SHIFT_IN_PRIOR_STD"])  # prior std is 0


REEK = Path(
    os.environ.get("REEK_ENSEMBLE", Path.home() / "fmu-ensemble-upstream/tests/data/testensemble-reek001")
)
needs_reek = pytest.mark.skipif(not REEK.is_dir(), reason="Reek test ensemble not found. Set REEK_ENSEMBLE.")


def reek_obs_file(tmp_path: Path, reek: Path = REEK) -> Path:
    """Observations equal to realization 0 at three report dates. The error is 1000 SM3."""
    files = sorted((reek / "realization-0/iter-0/eclipse/model").glob("*.DATA"))
    f = misfit.summary_module.df(misfit.SimFiles(str(files[0])), time_index="yearly", column_keys=["FOPT"])
    lines = [
        f"SUMMARY_OBSERVATION O{i} {{ VALUE = {f['FOPT'].iloc[i]}; ERROR = 1000; DATE = {pd.Timestamp(f.index[i]).date()}; KEY = FOPT; }};"
        for i in (1, 2, 3)
    ]
    return write(tmp_path / "reek.obs", "\n".join(lines))


@needs_reek
def test_reek_single_ensemble_real_zero_has_zero_misfit(tmp_path, valid_reek_ensemble):
    obs = reek_obs_file(tmp_path, valid_reek_ensemble)
    out = tmp_path / "qc"
    code = misfit.main(
        [
            "--obs",
            str(obs),
            "--posterior",
            str(valid_reek_ensemble / "realization-*/iter-0"),
            "--expected",
            "5",
            "--outdir",
            str(out),
        ]
    )
    assert code == 0
    table = pd.read_csv(out / "real_misfit.csv").set_index("REAL")
    assert table.loc[0, "MISFIT"] == pytest.approx(0.0, abs=1e-6)
    assert (table.drop(index=0)["MISFIT"] > 0).all()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["ensembles"]["posterior"]["realizations_used"] == [0, 1, 2, 3, 4]
    assert (out / "residuals.csv").exists() and not (out / "params_shift.csv").exists()


@needs_reek
def test_reek_prior_and_posterior_identical_gives_no_degradation(tmp_path, valid_reek_ensemble):
    obs = reek_obs_file(tmp_path, valid_reek_ensemble)
    glob_ = str(valid_reek_ensemble / "realization-*/iter-0")
    code = misfit.main(
        [
            "--obs",
            str(obs),
            "--prior",
            glob_,
            "--posterior",
            glob_,
            "--expected",
            "5",
            "--outdir",
            str(tmp_path / "qc"),
        ]
    )
    assert code == 0
    found = json.loads((tmp_path / "qc/manifest.json").read_text())["findings"]
    assert not any("worse" in f["finding"] or "higher" in f["finding"] for f in found)


@needs_reek
def test_reek_missing_member_fails_closed(tmp_path, valid_reek_ensemble):
    obs = reek_obs_file(tmp_path, valid_reek_ensemble)
    code = misfit.main(
        [
            "--obs",
            str(obs),
            "--posterior",
            str(valid_reek_ensemble / "realization-*/iter-0"),
            "--expected",
            "6",
            "--outdir",
            str(tmp_path / "qc"),
        ]
    )
    assert code == 1 and not (tmp_path / "qc/residuals.csv").exists()


@needs_reek
def test_reek_realization_spec_skips_extra_members(tmp_path, valid_reek_ensemble):
    obs = reek_obs_file(tmp_path, valid_reek_ensemble)
    out = tmp_path / "qc"
    code = misfit.main(
        [
            "--obs",
            str(obs),
            "--posterior",
            str(valid_reek_ensemble / "realization-*/iter-0"),
            "--realizations",
            "0-2",
            "--outdir",
            str(out),
        ]
    )
    assert code == 0
    info = json.loads((out / "manifest.json").read_text())["ensembles"]["posterior"]
    assert info["realizations_used"] == [0, 1, 2]
    assert info["extra_not_used"] == [3, 4]


@needs_reek
def test_reek_unknown_key_fails_closed(tmp_path, valid_reek_ensemble):
    obs = write(
        tmp_path / "x.obs",
        "SUMMARY_OBSERVATION O { VALUE = 1; ERROR = 1; DATE = 2001-01-01; KEY = NOSUCHKEY; };",
    )
    code = misfit.main(
        [
            "--obs",
            str(obs),
            "--posterior",
            str(valid_reek_ensemble / "realization-*/iter-0"),
            "--outdir",
            str(tmp_path / "qc"),
        ]
    )
    assert code == 1


def test_bad_observation_blocks_the_report(tmp_path):
    obs = write(
        tmp_path / "bad.obs", "SUMMARY_OBSERVATION O { VALUE = 1; ERROR = 0; DATE = 2001-01-01; KEY = K; };"
    )
    assert (
        misfit.main(
            ["--obs", str(obs), "--posterior", "/nope/realization-*/iter-0", "--outdir", str(tmp_path / "q")]
        )
        == 1
    )


def test_no_runpath_is_usage_error(tmp_path):
    obs = write(
        tmp_path / "o.obs", "SUMMARY_OBSERVATION O { VALUE = 1; ERROR = 1; DATE = 2001-01-01; KEY = K; };"
    )
    assert (
        misfit.main(
            ["--obs", str(obs), "--posterior", "/nope/realization-*/iter-0", "--outdir", str(tmp_path / "q")]
        )
        == 2
    )


class _Summary:
    def __init__(self, units):
        self._units = units

    def unit(self, key):
        return self._units[key]


class _Source:
    def __init__(self, units):
        self._summary = _Summary(units)

    def get_summary(self):
        return self._summary


def test_blank_wwct_unit_is_preserved():
    units = misfit.read_units(_Source({"WWCT:PRODU2": ""}), ["WWCT:PRODU2"])
    assert units == {"WWCT:PRODU2": ""}


def test_blank_rate_unit_still_fails():
    with pytest.raises(misfit.IntegrityError, match="unit is unavailable"):
        misfit.read_units(_Source({"FOPR": ""}), ["FOPR"])


def test_observations_of_a_well_without_flow_are_flagged(tmp_path):
    from resdata.summary import Summary

    case = tmp_path / "SHUT"
    writer = Summary.writer(str(case), datetime(2020, 1, 1), 1, 1, 1)
    rates = {("WOPR", "P1"): (0.0, 100.0), ("WBHP", "P1"): (200.0, 180.0), ("WWIR", "I1"): (0.0, 0.0)}
    rates[("WBHP", "X1")] = (150.0, 150.0)
    for family, well in rates:
        writer.add_variable(family, wgname=well)
    for step, days in enumerate((31.0, 60.0), start=1):
        tstep = writer.add_t_step(step, days)
        for (family, well), values in rates.items():
            tstep[f"{family}:{well}"] = values[step - 1]
    writer.fwrite()
    data = write(tmp_path / "SHUT.DATA", "RUNSPEC\n")
    obs = write(
        tmp_path / "s.obs",
        "SUMMARY_OBSERVATION P1_BHP_FEB { VALUE = 1.4; ERROR = 5; DATE = 2020-02-01; KEY = WBHP:P1; };\n"
        "SUMMARY_OBSERVATION P1_OPR_FEB { VALUE = -32; ERROR = 20; DATE = 2020-02-01; KEY = WOPR:P1; };\n"
        "SUMMARY_OBSERVATION P1_OPR_MAR { VALUE = 98; ERROR = 10; DATE = 2020-03-01; KEY = WOPR:P1; };\n"
        "SUMMARY_OBSERVATION I1_WIR_MAR { VALUE = 0; ERROR = 10; DATE = 2020-03-01; KEY = WWIR:I1; };\n"
        "SUMMARY_OBSERVATION X1_BHP_MAR { VALUE = 150; ERROR = 5; DATE = 2020-03-01; KEY = WBHP:X1; };\n",
    )

    code, result = run_qc(str(obs), "--case", str(data))

    shut = [p["message"] for p in result["problems"] if "no flow" in p["message"]]
    assert code == 0
    assert shut == [
        "well I1 has no flow in the case at the date of 1 observations. They carry no reservoir"
        " information. Examples: ['I1_WIR_MAR']",
        "well P1 has no flow in the case at the date of 2 observations. They carry no reservoir"
        " information. Examples: ['P1_BHP_FEB', 'P1_OPR_FEB']",
    ]
    assert [p for p in result["proposals"] if p.startswith(("I1:", "P1:"))] == [
        "I1: 1 observations at dates with no flow. Options for the user: remove them, or keep them on purpose."
        " Do not edit the observation file unless the user asks.",
        "P1: 2 observations at dates with no flow. Options for the user: remove them, or keep them on purpose."
        " Do not edit the observation file unless the user asks.",
    ]


def test_deck_usage_errors_fail_closed(tmp_path, capsys, monkeypatch):
    good = write(
        tmp_path / "g.obs",
        "SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 1; DATE = 2020-01-01; KEY = WOPR:P1; };",
    )
    bad_deck = write(tmp_path / "BAD.DATA", "RUNSPEC\nDIMENS\n a b c /\n")
    assert obs_qc.main([str(good), "--deck", str(tmp_path / "none.DATA")]) == 2
    assert obs_qc.main([str(good), "--deck", str(bad_deck)]) == 2
    assert "--deck" in capsys.readouterr().err
    monkeypatch.setitem(sys.modules, "res2df", None)
    assert obs_qc.main([str(good), "--deck", str(bad_deck)]) == 2
    assert "--deck needs res2df" in capsys.readouterr().err
    code, result = run_qc(str(good))
    assert code == 0 and not [p for p in result["problems"] if p.get("code") == "HMQC-OBS-NOFLOW"]


def test_design_matrix_labels_in_parameters_are_skipped(tmp_path):
    """Regression: a one-by-one design run (DESIGN_MATRIX) writes SENSNAME and SENSCASE as text."""
    (tmp_path / "parameters.txt").write_text("SENSNAME PORO_CH\nSENSCASE low\nPORO_CH 0.21\n")
    assert misfit.read_parameters(tmp_path) == {"PORO_CH": 0.21}
