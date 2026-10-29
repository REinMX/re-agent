"""Numeric and native-unit handoff regressions; no simulator runs."""

import importlib.util
import json
import os
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
pytest.importorskip("res2df")
SCRIPT = Path(__file__).parents[1] / "skills/history-match-qc/scripts/misfit_report.py"


@pytest.fixture
def report():
    spec = importlib.util.spec_from_file_location("misfit_integrity", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def case(tmp_path, monkeypatch, report):
    frames = {}
    units = {}

    def member(
        label,
        real,
        values,
        parameter_text="A 1\n",
        unit="SM3",
        key="FOPT",
        deck="eclipse/model/CASE",
        files=None,
    ):
        runpath = tmp_path / label / f"realization-{real}" / "iter-0"
        (runpath / deck).parent.mkdir(parents=True, exist_ok=True)
        (runpath / f"{deck}.DATA").write_text("synthetic")
        (runpath / f"{deck}.UNSMRY").write_text("synthetic")
        (runpath / "parameters.txt").write_text(parameter_text)
        for name, text in (files or {}).items():
            (runpath / name).write_text(text)
        frames[(label, real)] = pd.DataFrame(
            {key: values}, index=pd.to_datetime(["2001-01-01", "2002-01-01"])
        )
        units[(label, real)] = unit
        return runpath

    class Files:
        def __init__(self, file):
            runpath = next(p for p in Path(file).parents if p.parent.name.startswith("realization-"))
            self.key = (runpath.parents[1].name, int(runpath.parent.name.split("-")[1]))

        def get_summary(self):
            def unit(key):
                value = units[self.key]
                if isinstance(value, Exception):
                    raise value
                return value

            return SimpleNamespace(unit=unit)

    monkeypatch.setattr(report, "SimFiles", Files)
    monkeypatch.setattr(report.summary_module, "df", lambda source, **kwargs: frames[source.key].copy())

    def run(extra=(), observed=0, error=1, dates=("2001-01-01",), prior=False, key="FOPT", general=""):
        obs = tmp_path / "test.obs"
        obs.write_text(
            "\n".join(
                f"SUMMARY_OBSERVATION O{i} {{ VALUE = {observed}; ERROR = {error}; "
                f"DATE = {date}; KEY = {key}; }};"
                for i, date in enumerate(dates)
            )
            + "\n"
            + general
        )
        args = [
            "--obs",
            str(obs),
            "--posterior",
            str(tmp_path / "posterior/realization-*/iter-0"),
            "--outdir",
            str(tmp_path / "out"),
        ]
        if prior:
            args.extend(["--prior", str(tmp_path / "prior/realization-*/iter-0")])
        return report.main([*args, *extra])

    return SimpleNamespace(member=member, run=run, out=tmp_path / "out", frames=frames, units=units)


def assert_rejected(case, code, capsys, message):
    assert code == 1
    assert not case.out.exists(), "rejected input must not produce a partial report"
    assert message in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "text", ["A nan\n", "A inf\n", "A 1e309\n", "A junk\n", "A\n", "A 1 extra\n", "A 1\nA 2\n"]
)
@pytest.mark.parametrize("partial", [False, True])
def test_invalid_parameter_is_never_silently_skipped(case, capsys, text, partial):
    case.member("posterior", 0, [0, 1], parameter_text=text)
    case.member("posterior", 1, [1, 2])
    assert_rejected(case, case.run(["--allow-partial"] if partial else []), capsys, "parameter")


@pytest.mark.parametrize(
    "option",
    [
        "--collapse-ratio",
        "--residual-limit",
        "--worse-fraction",
        "--worse-margin",
        "--worse-single",
        "--shift-limit",
        "--std-ratio-limit",
    ],
)
@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_thresholds_require_finite_valid_domains(case, capsys, option, value):
    case.member("posterior", 0, [0, 1])
    assert case.run([f"{option}={value}"]) == 2
    assert not case.out.exists()
    assert option in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--worse-fraction", "--collapse-ratio", "--std-ratio-limit"])
def test_fraction_and_collapse_thresholds_cannot_exceed_one(case, capsys, option):
    case.member("posterior", 0, [0, 1])
    assert case.run([option, "1.1"]) == 2
    assert not case.out.exists()
    assert option in capsys.readouterr().err


@pytest.mark.parametrize(
    "defect",
    [
        "spread",
        "sim_mean",
        "spread_ratio",
        "misfit_mean",
        "parameter_std",
        "parameter_mean",
        "parameter_ratio",
    ],
)
def test_derived_overflow_rejects_before_any_write(case, capsys, defect):
    values = [0, 1e155] if defect == "spread" else [1, 2]
    error = 1e155 if defect == "spread" else 1
    observed = 0
    prior = defect.startswith("parameter")
    if defect == "sim_mean":
        values, observed, error = [1e308, 1e308], 1e308, 1e308
    elif defect == "spread_ratio":
        # Extreme spread/error also overflows individual normalized residuals.
        values, error = [-1e154, 1e154], 1e-155
    elif defect == "misfit_mean":
        values = [1e154, 1e154]
    for i, value in enumerate(values):
        before, after = [0, 1][i], [2, 3][i]
        if defect == "parameter_std":
            before, after = [0, 1e155][i], [0, 1][i]
        elif defect == "parameter_mean":
            before, after = 1e308, 1e308
        elif defect == "parameter_ratio":
            before, after = [0, 1e-155][i], [0, 1e154][i]
        case.member("posterior", i, [value, value], parameter_text=f"A {after}\n")
        if prior:
            case.member("prior", i, [value, value], parameter_text=f"A {before}\n")
    assert_rejected(case, case.run(prior=prior, observed=observed, error=error), capsys, "finite")


@pytest.mark.parametrize("partial", [False, True])
def test_nonfinite_residual_is_not_partial_coverage(case, capsys, partial):
    case.member("posterior", 0, [1e308, 1e308])
    case.member("posterior", 1, [-1e308, -1e308])
    assert_rejected(
        case,
        case.run(["--allow-partial"] if partial else [], observed=-1e308, error=1e308),
        capsys,
        "finite",
    )


@pytest.mark.parametrize(
    "function,column",
    [("obs_summary", "SIM_STD"), ("real_misfit", "MISFIT"), ("parameter_shift", "SHIFT_IN_PRIOR_STD")],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_derived_tables_cannot_be_serialized(
    case, monkeypatch, report, capsys, function, column, value
):
    for label in ("prior", "posterior"):
        for i in range(2):
            case.member(label, i, [i, i + 1], parameter_text=f"A {i}\n")
    original = getattr(report, function)

    def corrupt(*args):
        table = original(*args)
        table.loc[0, column] = value
        return table

    monkeypatch.setattr(report, function, corrupt)
    assert_rejected(case, case.run(prior=True), capsys, "finite")


def test_findings_rejects_overflow_in_ensemble_misfit_mean(report):
    summary = pd.DataFrame(
        [
            {"ENSEMBLE": label, "OBS": "O", "COUNT": 2, "MEAN_RESIDUAL": 1.0}
            for label in ("prior", "posterior")
        ]
    )
    misfit = pd.DataFrame(
        [{"ENSEMBLE": label, "MISFIT": 1e308} for label in ("prior", "posterior") for _ in range(2)]
    )
    thresholds = Namespace(worse_margin=0.5, worse_single=1.0, worse_fraction=0.3)
    with pytest.raises(ValueError, match="finite|overflow"):
        report.findings(summary, misfit, None, thresholds, ["prior", "posterior"])


def test_single_member_and_constant_prior_have_explicit_null_ratios(case):
    for label in ("prior", "posterior"):
        for i in range(2):
            case.member(label, i, [1, 2], parameter_text="A 5\n")
    assert case.run(prior=True) == 0
    shift = pd.read_csv(case.out / "params_shift.csv")
    assert shift[["SHIFT_IN_PRIOR_STD", "STD_RATIO"]].isna().all().all()
    assert (pd.read_csv(case.out / "obs_summary.csv")["SIM_STD"] == 0).all()


def test_single_member_spread_remains_zero(case):
    case.member("posterior", 0, [0, 1])
    assert case.run() == 0
    assert pd.read_csv(case.out / "obs_summary.csv")["SIM_STD"].tolist() == [0]


def test_partial_outside_coverage_is_nullable_but_not_success(case):
    case.member("posterior", 0, [0, 1])
    assert case.run(["--allow-partial"], dates=("2001-01-01", "2003-01-01")) == 1
    rows = pd.read_csv(case.out / "residuals.csv")
    assert rows.loc[1, "STATUS"] == "outside"
    assert pd.isna(rows.loc[1, "SIMULATED"]) and pd.isna(rows.loc[1, "RESIDUAL"])
    manifest = json.loads((case.out / "manifest.json").read_text())
    assert any(item["ensemble"] == "coverage" for item in manifest["issues"])


def test_partial_missing_member_is_recorded(case):
    case.member("posterior", 0, [0, 1])
    assert case.run(["--expected", "2", "--allow-partial"]) == 1
    info = json.loads((case.out / "manifest.json").read_text())["ensembles"]["posterior"]
    assert info["realizations_used"] == [0] and info["missing"] == [1]


@pytest.mark.parametrize("prior", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_native_unit_mismatch_is_fatal_even_with_partial(case, capsys, prior, partial):
    case.member("posterior", 0, [0, 1], unit="SM3")
    case.member("prior" if prior else "posterior", 0 if prior else 1, [0, 1], unit="STB")
    assert_rejected(case, case.run(["--allow-partial"] if partial else [], prior=prior), capsys, "unit")


@pytest.mark.parametrize("unit", [None, "", "   ", KeyError("FOPT")])
def test_unavailable_native_units_cannot_be_guessed(case, capsys, unit):
    case.member("posterior", 0, [0, 1], unit=unit)
    case.member("posterior", 1, [0, 1])
    assert_rejected(case, case.run(["--allow-partial"]), capsys, "unit")


@pytest.mark.parametrize("unit", ["", "   "])
def test_blank_unit_is_valid_for_water_cut(case, unit):
    # OPM Flow writes a blank unit for water cut: it is a fraction
    for real in (0, 1):
        case.member("posterior", real, [0.1, 0.2], unit=unit, key="WWCT:PROD")
    assert case.run(key="WWCT:PROD") == 0
    manifest = json.loads((case.out / "manifest.json").read_text())
    assert manifest["units"] == {"WWCT:PROD": ""}


@pytest.mark.parametrize("unit", [None, KeyError("WWCT:PROD")])
def test_water_cut_unit_must_still_be_readable(case, capsys, unit):
    case.member("posterior", 0, [0.1, 0.2], unit=unit, key="WWCT:PROD")
    assert_rejected(case, case.run(key="WWCT:PROD"), capsys, "unit")


def test_reader_without_unit_api_is_rejected(case, report, monkeypatch, capsys):
    case.member("posterior", 0, [0, 1])
    original = report.SimFiles

    class NoUnits:
        def __init__(self, file):
            self.key = original(file).key

    monkeypatch.setattr(report, "SimFiles", NoUnits)
    assert_rejected(case, case.run(), capsys, "unit")


def test_legacy_get_eclsum_adapter_units_are_supported(case, report, monkeypatch):
    case.member("posterior", 0, [0, 1])
    original = report.SimFiles

    class Legacy:
        def __init__(self, file):
            self.source = original(file)
            self.key = self.source.key

        def get_eclsum(self):
            return self.source.get_summary()

    monkeypatch.setattr(report, "SimFiles", Legacy)
    assert case.run() == 0


def test_manifest_records_native_units_and_observation_assumption(case):
    for label in ("prior", "posterior"):
        case.member(label, 0, [0, 1])
    assert case.run(prior=True) == 0
    manifest = json.loads((case.out / "manifest.json").read_text())
    assert manifest["units"] == {"FOPT": "SM3"}
    assumption = manifest["observation_units_assumption"].lower()
    assert "values" in assumption and "errors" in assumption and "same" in assumption
    assert "unit" in assumption and "no unit" in assumption


def test_manifest_serialization_explicitly_forbids_nonfinite(case, report, monkeypatch):
    case.member("posterior", 0, [0, 1])
    dumps = report.json.dumps
    calls = []

    def checked(*args, **kwargs):
        calls.append(kwargs)
        return dumps(*args, **kwargs)

    monkeypatch.setattr(report.json, "dumps", checked)
    assert case.run() == 0
    assert calls and calls[-1].get("allow_nan") is False


def test_partial_member_without_predictions_remains_in_misfit_table(case):
    case.member("posterior", 0, [0, 1])
    case.member("posterior", 1, [0, 1])
    case.frames[("posterior", 1)].index = pd.to_datetime(["2002-01-01", "2003-01-01"])
    assert case.run(["--allow-partial"]) == 1
    misfit = pd.read_csv(case.out / "real_misfit.csv").set_index("REAL")
    assert set(misfit.index) == {0, 1}
    assert misfit.loc[1, "OBS_USED"] == 0 and pd.isna(misfit.loc[1, "MISFIT"])


def test_partial_ensemble_without_predictions_has_no_numeric_report_statistics(case, capsys):
    case.member("prior", 0, [0, 1])
    case.member("posterior", 0, [0, 1])
    case.frames[("posterior", 0)].index = pd.to_datetime(["2002-01-01", "2003-01-01"])
    assert case.run(["--allow-partial"], prior=True) == 1
    assert (case.out / "manifest.json").is_file()
    output = capsys.readouterr().out.lower()
    assert "posterior: 1 realizations, 0 of 1 observations used" in output
    assert "misfit unavailable" in output
    assert " nan" not in output and " inf" not in output


def test_manifest_nonfinite_finding_rejects_before_csv_writes(case, report, monkeypatch, capsys):
    case.member("posterior", 0, [0, 1])
    monkeypatch.setattr(report, "findings", lambda *args: [{"score": float("nan")}])
    assert_rejected(case, case.run(), capsys, "manifest")


def test_parameter_decode_failure_is_fatal_with_partial(case, capsys):
    runpath = case.member("posterior", 0, [0, 1])
    (runpath / "parameters.txt").write_bytes(b"A \xff\n")
    case.member("posterior", 1, [0, 1])
    assert_rejected(case, case.run(["--allow-partial"]), capsys, "parameter")


def test_native_reek_finite_members_record_actual_units(tmp_path, report):
    reek = Path(
        os.environ.get("REEK_ENSEMBLE", Path.home() / "fmu-ensemble-upstream/tests/data/testensemble-reek001")
    )
    if not reek.is_dir():
        pytest.skip("native Reek fixture unavailable")
    # Upstream member 0 contains FOO NaN, which this strict contract must reject.
    file = next((reek / "realization-1/iter-0/eclipse/model").glob("*.DATA"))
    source = report.SimFiles(str(file))
    frame = report.summary_module.df(source, time_index="yearly", column_keys=["FOPT"])
    obs = tmp_path / "native.obs"
    obs.write_text(
        f"SUMMARY_OBSERVATION O {{ VALUE = {frame['FOPT'].iloc[1]}; ERROR = 1000; "
        f"DATE = {pd.Timestamp(frame.index[1]).date()}; KEY = FOPT; }};"
    )
    out = tmp_path / "native"
    assert (
        report.main(
            [
                "--obs",
                str(obs),
                "--posterior",
                str(reek / "realization-*/iter-0"),
                "--realizations",
                "1-4",
                "--outdir",
                str(out),
            ]
        )
        == 0
    )
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["units"] == {"FOPT": source.get_summary().unit("FOPT")}
    assert manifest["ensembles"]["posterior"]["realizations_used"] == [1, 2, 3, 4]


@pytest.mark.parametrize("value", ["not-a-number", float("nan"), float("inf")])
def test_invalid_summary_numeric_values_are_fatal_with_partial(case, capsys, value):
    case.member("posterior", 0, [0, 1])
    case.member("posterior", 1, [value, value])
    assert_rejected(case, case.run(["--allow-partial"]), capsys, "finite")


def test_unit_mismatch_is_not_hidden_by_other_member_defects(case, capsys):
    case.member("posterior", 0, [0, 1], unit="SM3")
    case.member("posterior", 1, [0, 1], unit="STB")
    case.frames[("posterior", 1)].index = pd.to_datetime(["2001-01-01", "2001-01-01"])
    assert_rejected(case, case.run(["--allow-partial"]), capsys, "unit")


@pytest.mark.parametrize("defect", ["parameter", "summary"])
def test_numeric_defect_is_not_hidden_by_bad_member_dates(case, capsys, defect):
    case.member("posterior", 0, [0, 1])
    case.member(
        "posterior",
        1,
        [float("nan"), 1] if defect == "summary" else [0, 1],
        parameter_text="A nan\n" if defect == "parameter" else "A 1\n",
    )
    case.frames[("posterior", 1)].index = pd.to_datetime(["2001-01-01", "2001-01-01"])
    assert_rejected(case, case.run(["--allow-partial"]), capsys, "finite")


def test_outdir_overlapping_runpath_is_rejected(case, tmp_path, capsys):
    # Finding 4 from the 2026-10-08 poteto-mode review: case_qc.py refuses an --outdir
    # that overlaps a runpath or an --obs file; misfit_report.py had no equivalent guard
    # despite taking the same --outdir + runpath-glob + --obs shape.
    case.member("posterior", 0, [0, 1])
    code = case.run(["--outdir", str(tmp_path / "posterior")])
    assert code == 2
    assert not case.out.exists()
    assert "overlaps" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("deck", "extra"),
    [
        ("model/DROGON-{real}", ["--eclbase", "model/DROGON-<IENS>"]),
        ("DROGON-{real}", ["--eclbase", "DROGON-<IENS>"]),
        ("model/DROGON-{real}", []),
        ("DROGON-{real}", []),
        ("eclipse/model/CASE", ["--eclbase", "CASE"]),
    ],
)
def test_ert_runpath_layouts_find_the_deck(case, deck, extra):
    for real in (0, 1):
        case.member("posterior", real, [0, 1], deck=deck.format(real=real))
    assert case.run([*extra, "--expected", "2"]) == 0
    info = json.loads((case.out / "manifest.json").read_text())["ensembles"]["posterior"]
    assert info["realizations_used"] == [0, 1]


def test_ensemble_without_valid_members_prints_member_reasons(case, capsys):
    for real in range(7):
        runpath = case.member("posterior", real, [0, 1], deck="model/A")
        (runpath / "model/B.DATA").write_text("synthetic")
    assert case.run() == 1
    err = capsys.readouterr().err.splitlines()
    assert err == [
        *(
            f"ERROR: posterior realization {real}: no unique .DATA file in model ['A.DATA', 'B.DATA']. Give --eclbase"
            for real in range(5)
        ),
        "ERROR: posterior: 2 more member issues",
        "ERROR: the posterior ensemble has no valid members",
    ]


def test_eclbase_path_without_deck_names_the_missing_file(case, capsys):
    case.member("posterior", 0, [0, 1], deck="eclipse/model/CASE")
    assert case.run(["--eclbase", "model/DROGON-<IENS>"]) == 1
    assert "posterior realization 0: no model/DROGON-0.DATA in the runpath" in capsys.readouterr().err


RFT = "GENERAL_OBSERVATION RFT_A { DATA = RFT_A; RESTART = 1; OBS_FILE = rft_a.obs; };\n"


@pytest.fixture
def rft(case, tmp_path):
    (tmp_path / "rft_a.obs").write_text("100 2\n104 2\n")
    return case


def test_general_observation_reads_gen_data_from_option(rft):
    rft.member("posterior", 0, [0, 1], files={"rft_a_1.txt": "101\n100\n"})
    assert rft.run(["--gen-data", "RFT_A=rft_a_%d.txt"], general=RFT) == 0
    rows = pd.read_csv(rft.out / "residuals.csv").set_index("OBS")
    assert rows.loc["RFT_A:0", ["KEY", "SIMULATED", "RESIDUAL", "STATUS"]].tolist() == [
        "GEN:RFT_A",
        101.0,
        0.5,
        "exact",
    ]
    assert rows.loc["RFT_A:1", ["SIMULATED", "RESIDUAL"]].tolist() == [100.0, -2.0]
    manifest = json.loads((rft.out / "manifest.json").read_text())
    assert manifest["misfit_by_family"]["posterior"] == {
        "FOPT": {"observations": 1, "mean_squared_residual": 0.0},
        "GEN": {"observations": 2, "mean_squared_residual": 2.125},
    }
    assert manifest["general_observations"]["used"] == [
        {"name": "RFT_A", "data": "RFT_A", "report_step": 1, "result_file": "rft_a_%d.txt", "points": 2}
    ]


def test_general_observation_reads_result_file_from_ert_config(rft, tmp_path):
    config = tmp_path / "case.ert"
    config.write_text("NUM_REALIZATIONS 1\nGEN_DATA RFT_A RESULT_FILE:rft_a_%d.txt REPORT_STEPS:1\n")
    rft.member("posterior", 0, [0, 1], files={"rft_a_1.txt": "101\n100\n"})
    assert rft.run(["--ert-config", str(config)], general=RFT) == 0
    rows = pd.read_csv(rft.out / "residuals.csv")
    assert rows["OBS"].tolist() == ["O0", "RFT_A:0", "RFT_A:1"]


def test_general_observation_without_source_is_named_and_skipped(rft, capsys):
    rft.member("posterior", 0, [0, 1], files={"rft_a_1.txt": "101\n100\n"})
    assert rft.run(general=RFT) == 0
    assert (
        "NOTE: GENERAL_OBSERVATION not used (give --ert-config or --gen-data): ['RFT_A']"
        in capsys.readouterr().out
    )
    assert pd.read_csv(rft.out / "residuals.csv")["OBS"].tolist() == ["O0"]


def test_missing_gen_data_file_is_a_missing_prediction(rft, capsys):
    rft.member("posterior", 0, [0, 1], files={"rft_a_1.txt": "101\n100\n"})
    rft.member("posterior", 1, [0, 1], files={"rft_a_1.txt": "101\n"})
    rft.member("posterior", 2, [0, 1])
    assert rft.run(["--gen-data", "RFT_A=rft_a_%d.txt"], general=RFT) == 1
    assert "incomplete observation predictions" in capsys.readouterr().err
    assert rft.run(["--gen-data", "RFT_A=rft_a_%d.txt", "--allow-partial"], general=RFT) == 1
    rows = pd.read_csv(rft.out / "residuals.csv")
    gen = rows[rows["KEY"] == "GEN:RFT_A"]
    assert gen["STATUS"].tolist() == [
        "exact",
        "exact",
        "exact",
        "missing_index",
        "missing_file",
        "missing_file",
    ]
    assert gen["RESIDUAL"].isna().tolist() == [False, False, False, True, True, True]


def test_general_observation_without_result_file_is_rejected(rft, capsys):
    rft.member("posterior", 0, [0, 1], files={"rft_a_1.txt": "101\n100\n"})
    assert rft.run(["--gen-data", "OTHER=x_%d.txt"], general=RFT) == 1
    assert "no GEN_DATA RESULT_FILE for RFT_A" in capsys.readouterr().err
    assert not rft.out.exists()


def test_log10_companions_are_not_counted_as_parameters(tmp_path, report):
    module = report
    (tmp_path / "parameters.txt").write_text(
        "G:PERM 2.0\nLOG10_G:PERM 0.30103\nG:PORO 1.0\nLOG10_OTHER 1.0\n"
    )
    assert module.read_parameters(tmp_path) == {"G:PERM": 2.0, "G:PORO": 1.0, "LOG10_OTHER": 1.0}
