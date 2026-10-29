"""Coverage boundaries on synthetic complete and deliberately incomplete evidence."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cov = load("coverage_integrity", ROOT / "skills/history-match-qc/scripts/coverage.py")


@pytest.mark.parametrize("function", [cov.coverage, cov.predictive_cdf])
@pytest.mark.parametrize(
    "sim,observed,error",
    [
        ([[-0.5, np.nan], [0.5, np.nan]], [0, 0], [1, 1]),
        ([[-0.5, 0], [0.5, np.nan]], [0, 0], [1, 1]),
        ([[0], [np.inf]], [0], [1]),
        ([[0], [1]], [np.nan], [1]),
        ([[0], [1]], [0], [np.inf]),
        ([[0]], [0], [1]),
        (np.empty((2, 0)), [], []),
        (np.zeros((2, 1)), [[0]], [[1]]),
        (np.zeros((2, 2)), [0], [1]),
        (np.zeros(2), [0], [1]),
        ([[0], [1]], [0], [0]),
        ([[0], [1]], [0], [-1]),
    ],
)
def test_coverage_and_cdf_reject_invalid_or_incomplete_domain(function, sim, observed, error):
    with pytest.raises(ValueError):
        function(sim, observed, error)


@pytest.mark.parametrize("truth", [[0, np.nan], [0], [[0, 0]], [0, np.inf]])
def test_truth_is_complete_finite_vector_over_requested_domain(truth):
    with pytest.raises(ValueError, match="truth"):
        cov.coverage([[-1, -1], [1, 1]], [0, 0], [1, 1], truth)


def residuals():
    return pd.DataFrame(
        [
            {"ENSEMBLE": "posterior", "REAL": r, "OBS": obs, "OBSERVED": 0.0, "ERROR": 1.0, "SIMULATED": sim}
            for r, sim in [(7, -0.5), (9, 0.5)]
            for obs in ["A", "B"]
        ]
    )


@pytest.mark.parametrize(
    "defect",
    ["duplicate", "missing-member", "observed", "error", "nonfinite", "null-real", "fractional-real"],
)
def test_residual_table_must_be_unambiguous_finite_and_rectangular(defect):
    table = residuals()
    if defect == "duplicate":
        table = pd.concat([table, table.iloc[[0]]], ignore_index=True)
    elif defect == "missing-member":
        table = table.iloc[1:]
    elif defect == "observed":
        table.loc[0, "OBSERVED"] = 1
    elif defect == "error":
        table.loc[0, "ERROR"] = 2
    elif defect == "nonfinite":
        table.loc[0, "SIMULATED"] = np.nan
    elif defect == "null-real":
        table.loc[0, "REAL"] = np.nan
    else:
        table["REAL"] = table["REAL"].astype(float)
        table.loc[0, "REAL"] = 0.5
    with pytest.raises(ValueError):
        cov.from_residuals(table, "posterior")


@pytest.mark.parametrize("column,value", [("KEY", "other-key"), ("DATE", "2002-01-01")])
def test_optional_observation_metadata_must_be_consistent(column, value):
    table = residuals()
    table[column] = {"KEY": "FOPT", "DATE": "2001-01-01"}[column]
    table.loc[0, column] = value
    with pytest.raises(ValueError):
        cov.from_residuals(table, "posterior")


def test_mixed_real_statuses_within_one_observation_give_the_same_coverage():
    # misfit_report sets STATUS per member: one observation can be exact for one member and step or
    # interpolated for another, depending on that member's ministeps. That is a complete valid grid.
    single = residuals()
    single.loc[single.OBS == "B", "OBSERVED"] = 2.0
    single["STATUS"] = "exact"
    mixed = single.copy()
    mixed["STATUS"] = ["exact", "step", "interpolated", "exact"]
    assert mixed.groupby("OBS")["STATUS"].nunique().max() == 2
    expected = cov.from_residuals(single, "posterior", expected_obs={"A", "B"}, expected_members={7, 9})
    result = cov.from_residuals(mixed, "posterior", expected_obs={"A", "B"}, expected_members={7, 9})
    assert result == expected
    assert result.predictive == 0.5


@pytest.mark.parametrize("status", ["outside", "missing_key", "missing_file", "missing_index"])
@pytest.mark.parametrize("simulated", [np.nan, 0.25])
def test_row_with_a_missing_prediction_status_is_refused(status, simulated):
    table = residuals()
    table["STATUS"] = "exact"
    table.loc[0, ["STATUS", "SIMULATED"]] = [status, simulated]
    with pytest.raises(ValueError):
        cov.from_residuals(table, "posterior")


def test_missing_status_set_matches_misfit_report(misfit):
    assert set(cov.MISSING_STATUS) == set(misfit.MISSING_STATUS)


def test_truth_csv_with_duplicate_obs_is_refused(tmp_path, capsys):
    residuals().to_csv(tmp_path / "residuals.csv", index=False)
    pd.DataFrame({"OBS": ["A", "B", "B"], "TRUTH": [0.0, 0.0, 5.0]}).to_csv(
        tmp_path / "truth.csv", index=False
    )
    args = ["--residuals", str(tmp_path / "residuals.csv"), "--truth-response", str(tmp_path / "truth.csv")]
    assert cov.main(args) == 1
    captured = capsys.readouterr()
    assert "duplicate" in captured.err and "'B'" in captured.err and "COVERAGE" not in captured.out


@pytest.mark.parametrize(
    "inventory", [{"expected_obs": {"A", "B", "MISSING"}}, {"expected_members": {7, 9, 11}}]
)
def test_external_inventory_detects_entirely_missing_rows(inventory):
    with pytest.raises(ValueError):
        cov.from_residuals(residuals(), "posterior", **inventory)


def test_supplied_truth_mapping_cannot_omit_an_observation():
    with pytest.raises(ValueError, match="truth"):
        cov.from_residuals(residuals(), "posterior", {"A": 0})


def test_complete_synthetic_residuals_keep_scoring_and_external_domain():
    result = cov.from_residuals(
        residuals(), "posterior", {"A": 0, "B": 0}, expected_obs={"A", "B"}, expected_members={7, 9}
    )
    assert result.n_obs == 2 and result.n_real == 2
    assert result.predictive == result.noise_biased == result.truth_response == 1


@pytest.fixture
def misfit(monkeypatch):
    # Native readers are outside these in-memory report regressions.
    native = ModuleType("res2df")
    native.ResdataFiles = object
    native.summary = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "res2df", native)
    return load("misfit_coverage_integrity", ROOT / "skills/history-match-qc/scripts/misfit_report.py")


def test_misfit_coverage_block_enforces_authoritative_inventory(misfit):
    result = misfit.coverage_block(
        residuals(), ["posterior"], expected_obs={"A", "B", "MISSING"}, expected_members={"posterior": {7, 9}}
    )
    assert "error" in result["posterior"] and "predictive" not in result["posterior"]


def test_partial_single_prediction_does_not_crash_unrelated_misfit_summary(misfit):
    table = residuals()
    table["KEY"], table["DATE"], table["STATUS"] = "FOPT", "2001-01-01", "exact"
    table["RESIDUAL"] = table["SIMULATED"]
    table.loc[0, ["SIMULATED", "RESIDUAL"]] = np.nan
    table.loc[0, "STATUS"] = "missing_key"
    summary = misfit.obs_summary(table)
    assert len(summary) == 2
    assert summary.loc[summary.OBS == "A", "PREDICTIVE_CDF"].isna().all()
    result = misfit.coverage_block(table, ["posterior"])
    assert "error" in result["posterior"]


@pytest.fixture
def report_case(tmp_path, monkeypatch, misfit):
    """misfit_report.main on in-memory member summaries (FOPT, linear time rule)."""
    frames = {}

    def member(real, dates, values):
        runpath = tmp_path / "posterior" / f"realization-{real}" / "iter-0"
        deck = runpath / "eclipse/model/CASE"
        deck.parent.mkdir(parents=True)
        for suffix in (".DATA", ".UNSMRY"):
            deck.with_suffix(suffix).write_text("synthetic")
        (runpath / "parameters.txt").write_text("A 1\n")
        frames[real] = pd.DataFrame({"FOPT": values}, index=pd.to_datetime(dates))

    class Files:
        def __init__(self, file):
            folder = next(p for p in Path(file).parents if p.name.startswith("realization-"))
            self.real = int(folder.name.split("-")[1])

        def get_summary(self):
            return SimpleNamespace(unit=lambda key: "SM3")

    monkeypatch.setattr(misfit, "SimFiles", Files)
    monkeypatch.setattr(
        misfit.summary_module, "df", lambda source, **kw: frames[source.real].copy(), raising=False
    )

    def run(*extra, date="2001-07-01"):
        obs = tmp_path / "case.obs"
        obs.write_text(f"SUMMARY_OBSERVATION O0 {{ VALUE = 0.5; ERROR = 1; DATE = {date}; KEY = FOPT; }};\n")
        pattern = str(tmp_path / "posterior/realization-*/iter-0")
        return misfit.main(
            ["--obs", str(obs), "--posterior", pattern, "--outdir", str(tmp_path / "out"), *extra]
        )

    return SimpleNamespace(member=member, run=run, out=tmp_path / "out")


def test_misfit_report_coverage_accepts_mixed_member_statuses(report_case, capsys):
    report_case.member(0, ["2001-01-01", "2002-01-01"], [0.0, 1.0])
    report_case.member(1, ["2001-01-01", "2001-07-01", "2002-01-01"], [0.0, 0.6, 1.0])
    assert report_case.run() == 0
    statuses = pd.read_csv(report_case.out / "residuals.csv")["STATUS"]
    assert sorted(statuses) == ["exact", "interpolated"]
    block = json.loads((report_case.out / "manifest.json").read_text())["coverage_p5_p95"]["posterior"]
    assert "error" not in block and block["predictive"] == 1 and block["observations"] == 1
    captured = capsys.readouterr()
    assert "COVERAGE [posterior] predictive P5-P95 1.00" in captured.out
    assert "coverage not computed" not in captured.err


def test_misfit_report_prints_coverage_error_in_strict_mode(report_case, capsys):
    report_case.member(0, ["2001-01-01", "2002-01-01"], [0.0, 1.0])
    assert report_case.run() == 0  # exit code unchanged: coverage is a diagnostic block, not a gate
    block = json.loads((report_case.out / "manifest.json").read_text())["coverage_p5_p95"]["posterior"]
    captured = capsys.readouterr()
    assert f"WARNING: coverage not computed: {block['error']}" in captured.err
    assert block["error"].startswith("posterior: ") and "2 realizations" in block["error"]
    assert "COVERAGE [" not in captured.out


def test_misfit_report_prints_coverage_error_with_allow_partial(report_case, capsys):
    report_case.member(0, ["2001-01-01", "2002-01-01"], [0.0, 1.0])
    report_case.member(1, ["2001-01-01", "2003-01-01"], [0.0, 1.0])
    assert report_case.run("--allow-partial", date="2002-06-01") == 1
    block = json.loads((report_case.out / "manifest.json").read_text())["coverage_p5_p95"]["posterior"]
    captured = capsys.readouterr()
    assert f"WARNING: coverage not computed: {block['error']}" in captured.err
    assert "no usable prediction" in block["error"] and "outside" in block["error"]
