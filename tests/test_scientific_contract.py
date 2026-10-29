"""Adversarial contract tests; synthetic data only, no simulator or model calls."""

import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary_fixture(tmp_path, monkeypatch, frames, units=None, periods=None):
    pytest.importorskip("res2df")
    mod = load("summary_contract", "skills/res2df-resdata-extract/scripts/ensemble_summary.py")
    for real in frames:
        folder = tmp_path / f"realization-{real}/iter-0/eclipse/model"
        folder.mkdir(parents=True)
        (folder / "CASE.DATA").write_text("synthetic")
        (folder / "CASE.UNSMRY").write_text("synthetic")

    class Files:
        def __init__(self, file):
            self.real = int(Path(file).parents[3].name.split("-")[1])

        def get_summary(self):
            start, end = (periods or {}).get(
                self.real, (frames[self.real].index[0], frames[self.real].index[-1])
            )
            return SimpleNamespace(
                unit=lambda key: (units or {}).get(self.real, {}).get(key, "SM3"),
                start_time=start,
                end_time=end,
            )

    monkeypatch.setattr(mod, "SimFiles", Files)
    monkeypatch.setattr(mod.summary_module, "df", lambda source, **kwargs: frames[source.real].copy())
    return mod, [
        str(tmp_path / "realization-*/iter-0"),
        "--keys",
        "FOPT",
        "--time-index",
        "yearly",
        "--expected",
        "2",
        "--outdir",
        str(tmp_path / "out"),
    ]


@pytest.mark.parametrize(
    "defect",
    [
        "missing_requested",
        "different_keys",
        "different_dates",
        "nan",
        "inf",
        "duplicate_dates",
        "units",
        "source_period",
    ],
)
def test_summary_rejects_incomplete_contract(tmp_path, monkeypatch, defect):
    pd = pytest.importorskip("pandas")
    dates = pd.to_datetime(["2001-01-01", "2002-01-01"])
    frames = {i: pd.DataFrame({"FOPT": [1.0, 2.0]}, index=dates) for i in range(2)}
    units = None
    periods = None
    if defect == "different_keys":
        frames[1]["FWPT"] = [2.0, 3.0]
    elif defect == "different_dates":
        frames[1].index = pd.to_datetime(["2001-01-01", "2003-01-01"])
    elif defect in ("nan", "inf"):
        frames[1].iloc[1, 0] = float(defect)
    elif defect == "duplicate_dates":
        frames[1].index = pd.to_datetime(["2001-01-01", "2001-01-01"])
    elif defect == "units":
        units = {0: {"FOPT": "SM3"}, 1: {"FOPT": "STB"}}
    if defect == "source_period":
        periods = {1: (dates[0], pd.Timestamp("2002-01-02"))}
    mod, args = summary_fixture(tmp_path, monkeypatch, frames, units, periods)
    if defect == "missing_requested":
        args.insert(args.index("--time-index"), "NOSUCH")
    assert mod.main(args) == 1
    assert not (tmp_path / "out/summary_stats.csv").exists()


def test_summary_valid_contract_preserves_counts(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")
    frames = {
        i: pd.DataFrame({"FOPT": [1.0 + i, 2.0 + i]}, index=pd.to_datetime(["2001-01-01", "2002-01-01"]))
        for i in range(2)
    }
    mod, args = summary_fixture(tmp_path, monkeypatch, frames)
    assert mod.main(args) == 0
    stats = pd.read_csv(tmp_path / "out/summary_stats.csv")
    assert stats["COUNT"].tolist() == [2, 2]
    assert stats["P50"].tolist() == [1.5, 2.5]


@pytest.mark.parametrize(
    "field,value",
    [("VALUE", "nan"), ("VALUE", "inf"), ("ERROR", "inf"), ("ERROR_MIN", "nan"), ("ERROR", "1e308")],
)
def test_observation_nonfinite_is_an_error(field, value):
    mod = load("obs_contract", "skills/history-match-qc/scripts/obs_qc.py")
    fields = {
        "VALUE": "1e308",
        "ERROR": "1",
        "ERROR_MIN": "0.1",
        "ERROR_MODE": "RELMIN",
        "KEY": "FOPT",
        "DATE": "2001-01-01",
    }
    fields[field] = value
    normalized = mod.normalize_summary({"file": "synthetic.obs", "line": 1, "name": "O1", "fields": fields})
    assert any(i["severity"] == "error" for i in normalized["issues"])


def mbal_args(history=None, pvt=None):
    base = ROOT / "tests/data/mbal/OILBOX"
    return [
        "--fluid",
        "oil",
        "--history",
        str(history or base / "history.csv"),
        "--pvt",
        str(pvt or base / "pvt.csv"),
        "--swi",
        "0.2",
        "--cw",
        "3e-6",
        "--cf",
        "4e-6",
        "--aquifer",
        "none",
        "--json",
    ]


@pytest.mark.parametrize(
    "option,value",
    [
        ("--cw", "nan"),
        ("--cf", "inf"),
        ("--m", "-1"),
        ("--data-error", "nan"),
        ("--in-place", "-1"),
        ("--cw", "1e308"),
    ],
)
def test_mbal_invalid_options_fail_before_output(tmp_path, option, value):
    mod = load("mbal_contract", "skills/material-balance/scripts/mbal.py")
    assert mod.main([*mbal_args(), option, value, "--outdir", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("value", ["nan", "inf", "1e308"])
def test_mbal_invalid_optional_production_fails(tmp_path, value):
    mod = load("mbal_contract", "skills/material-balance/scripts/mbal.py")
    base = (ROOT / "tests/data/mbal/OILBOX/history.csv").read_text().splitlines()
    header = base[0].split(",")
    index = header.index("WP")
    row = base[-1].split(",")
    row[index] = value
    base[-1] = ",".join(row)
    history = tmp_path / "history.csv"
    history.write_text("\n".join(base))
    assert mod.main([*mbal_args(history), "--outdir", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out").exists()


def test_mbal_nonincreasing_dates_fail(tmp_path):
    mod = load("mbal_contract", "skills/material-balance/scripts/mbal.py")
    lines = (ROOT / "tests/data/mbal/OILBOX/history.csv").read_text().splitlines()
    index = lines[0].split(",").index("DATE")
    first = lines[1].split(",")[index]
    row = lines[-1].split(",")
    row[index] = first
    lines[-1] = ",".join(row)
    history = tmp_path / "history.csv"
    history.write_text("\n".join(lines))
    assert mod.main(mbal_args(history)) == 1


def test_case_qc_rejects_stale_output_before_running(tmp_path, monkeypatch):
    mod = load("case_contract", "skills/case-qc/scripts/case_qc.py")
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.json").write_text('{"stale": true}')
    obs = tmp_path / "test.obs"
    obs.write_text("synthetic")
    monkeypatch.setattr(mod, "run_tool", lambda *args: pytest.fail("must reject before launching a tool"))
    assert mod.main(["--outdir", str(out), "--obs", str(obs)]) == 2
    assert json.loads((out / "report.json").read_text()) == {"stale": True}


def test_case_qc_rejects_output_in_source(tmp_path, monkeypatch):
    mod = load("case_contract", "skills/case-qc/scripts/case_qc.py")
    model = tmp_path / "model"
    model.mkdir()
    obs = model / "test.obs"
    obs.write_text("synthetic")
    monkeypatch.setattr(mod, "run_tool", lambda *args: pytest.fail("must reject source overlap"))
    assert mod.main(["--outdir", str(model / "reports"), "--runpaths", str(model)]) == 2


def test_misfit_missing_prediction_is_not_green(tmp_path, monkeypatch):
    pytest.importorskip("res2df")
    pd = pytest.importorskip("pandas")
    mod = load("misfit_contract", "skills/history-match-qc/scripts/misfit_report.py")
    obs = tmp_path / "test.obs"
    obs.write_text(
        "SUMMARY_OBSERVATION OK { VALUE = 1; ERROR = 1; DATE = 2001-01-01; KEY = FOPT; };\nSUMMARY_OBSERVATION MISSING { VALUE = 1; ERROR = 1; DATE = 2001-01-01; KEY = FWPT; };"
    )
    frame = pd.DataFrame({"FOPT": [1.0, 2.0]}, index=pd.to_datetime(["2001-01-01", "2002-01-01"]))
    # A runpath separate from --outdir: an outdir inside a runpath is refused (review finding 4).
    runpath = tmp_path / "realization-0"
    runpath.mkdir()
    monkeypatch.setattr(mod, "load_ensemble", lambda *args: ({0: frame}, {0: {}}, [], [], []))
    assert mod.main(["--obs", str(obs), "--posterior", str(runpath), "--outdir", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out/real_misfit.csv").exists()


def test_mbal_success_is_finite(capsys):
    mod = load("mbal_contract", "skills/material-balance/scripts/mbal.py")
    assert mod.main(mbal_args()) == 0
    result = json.loads(capsys.readouterr().out)

    def check(value):
        if isinstance(value, float):
            assert math.isfinite(value)
        elif isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(result)
