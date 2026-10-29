"""SCAL recommendations are validated before simulator includes are emitted."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("pyscal")
pd = pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/data/scal/egg_scalrec.csv"
SPEC = importlib.util.spec_from_file_location(
    "scal_rec", ROOT / "skills/scal-uncertainty/scripts/scal_rec.py"
)
rec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rec)


def test_check_outputs_five_interpolations_and_plot(tmp_path):
    report = rec.check_recommendation(FIXTURE, tmp_path)
    assert report["valid"], report
    assert [item["param"] for item in report["interpolations"]] == [-1, -0.5, 0, 0.5, 1]
    assert all(91 <= item["tables"][0]["rows"] <= 92 for item in report["interpolations"])
    assert (tmp_path / "curves.png").read_bytes().startswith(b"\x89PNG")
    assert (tmp_path / "check.json").is_file()


@pytest.mark.parametrize("column,value", [("SWCR", 0.95), ("SORW", -0.1), ("KRWEND", 1.1)])
def test_crossed_or_out_of_range_endpoints_rejected(tmp_path, column, value):
    frame = pd.read_csv(FIXTURE)
    frame.loc[1, column] = value
    path = tmp_path / "bad.csv"
    frame.to_csv(path, index=False)
    report = rec.check_recommendation(path, tmp_path / "check")
    assert not report["valid"]
    assert report["errors"]
    assert not (tmp_path / "check/curves.png").exists()


def test_missing_case_rejected(tmp_path):
    path = tmp_path / "missing.csv"
    pd.read_csv(FIXTURE).iloc[:2].to_csv(path, index=False)
    with pytest.raises(rec.RecommendationError, match="low/base/high"):
        rec.load_recommendation(path)


def test_generate_tables_checks_actual_includes(tmp_path):
    pytest.importorskip("res2df")
    result = rec.generate_tables(FIXTURE, [-1, 0, 1], 0.05, tmp_path)
    assert [item["param"] for item in result] == [-1, 0, 1]
    for item in result:
        assert item["tables"] == [{"keyword": "SWOF", "satnum": 1, "rows": 19}]
        assert item["qc"]["returncode"] == 0
        assert item["qc"]["findings"]["rows_read"]["satfunc"] == 19
        assert "SWOF" in Path(item["include"]).read_text()
    assert Path(result[0]["include"]).read_text() != Path(result[2]["include"]).read_text()


@pytest.mark.parametrize("params", [[2], [float("nan")], []])
def test_invalid_interpolation_parameters_fail_closed(tmp_path, params):
    with pytest.raises(rec.RecommendationError, match="parameters"):
        rec.generate_tables(FIXTURE, params, 0.01, tmp_path)


def test_negative_comma_separated_cli_parameters(tmp_path):
    pytest.importorskip("res2df")
    assert rec.main(["tables", str(FIXTURE), "--param", "-1,0,1", "--out", str(tmp_path)]) == 0
