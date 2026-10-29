import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")
SCRIPT = Path(__file__).parents[1] / "skills/deck-scal-pvt-qc/scripts/swatinit_qc.py"
spec = importlib.util.spec_from_file_location("swatinit_qc", SCRIPT)
qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qc)


@pytest.fixture
def cells():
    return pd.DataFrame(
        {
            "PORV": [100.0, 100.0, 100.0],
            "SWATINIT": [0.2, 0.2, 0.2],
            "SWAT": [0.4, 0.3, 0.2],
            "QC_FLAG": ["HC_BELOW_FWL", "SWL_TRUNC", "PC_SCALED"],
            "PC_SCALING": [2.0, float("nan"), 30.0],
            "PPCWMAX": [10.0, 10.0, 40.0],
            "EQLNUM": [1, 1, 2],
            "SATNUM": [3, 3, 4],
        }
    )


def test_summary(cells):
    result = qc.summarize(cells)
    assert result["hc_pv_change_pct"] == pytest.approx(-12.5)
    assert result["hc_below_fwl_pct"] == pytest.approx(-100 * 20 / 240)
    assert result["swl_trunc_pct"] == pytest.approx(-100 * 10 / 240)
    assert result["maxima"]["PC_SCALING"] == {"value": 30.0, "regions": [{"EQLNUM": 2, "SATNUM": 4}]}
    assert result["flag_counts"]["SWL_TRUNC"] == 1
    assert qc.exceeded(result, 1.0, 1.0, 20.0) == ["hc_below_fwl_pct", "swl_trunc_pct", "pc_scaling_max"]
    assert qc.exceeded(result, 9.0, 5.0, 30.0) == []


def test_correlation_with_ties():
    rows = [
        {
            "status": "ok",
            "hc_below_fwl_pct": -x,
            "swl_trunc_pct": -2.0,
            "hc_pv_change_pct": -x - 2,
            "params": {"ZONE:OWC": x},
        }
        for x in [1.0, 2.0, 2.0, 4.0]
    ]
    result = qc.correlations(rows)
    assert result[0]["spearman"] == pytest.approx(-1.0)
    assert result[0]["finding"] == "contact parameter acts as a STOIIP knob"
    assert result[0]["n"] == 4


def test_api_not_applicable(monkeypatch, tmp_path):
    case = tmp_path / "X.DATA"
    case.touch()
    fake = SimpleNamespace(get_deck=lambda: {}, get_initfile=lambda: SimpleNamespace(headers=[]))
    monkeypatch.setattr(qc, "load_backend", lambda: (None, lambda _: fake))
    assert qc.analyze_case(case)[0] is None


def test_mocked_api_requires_step_zero(monkeypatch, tmp_path, cells):
    case = tmp_path / "X.DATA"
    case.touch()
    fake = SimpleNamespace(
        get_deck=lambda: {"SWATINIT": True},
        get_initfile=lambda: SimpleNamespace(headers=[]),
        get_rstfile=lambda: SimpleNamespace(report_steps=[1]),
    )
    monkeypatch.setattr(
        qc, "load_backend", lambda: (SimpleNamespace(make_qc_gridframe=lambda _: cells), lambda _: fake)
    )
    with pytest.raises(ValueError, match="step 0"):
        qc.analyze_case(case)
    fake.get_rstfile = lambda: SimpleNamespace(report_steps=[0, 1])
    assert qc.analyze_case(case)[0].equals(cells)


def test_cli_outputs_and_cells(monkeypatch, tmp_path, cells):
    case = tmp_path / "source/X.DATA"
    case.parent.mkdir()
    case.touch()
    monkeypatch.setattr(qc, "analyze_case", lambda _: (cells, None))
    out = tmp_path / "out"
    assert qc.main(["--case", str(case), "--out", str(out)]) == 1
    report = json.loads((out / "swatinit_qc.json").read_text())
    assert report["status"] == "thresholds exceeded"
    assert not list(out.glob("*cells*"))
    monkeypatch.setattr(qc, "analyze_case", lambda _: (None, "no SWATINIT"))
    out = tmp_path / "na"
    assert qc.main(["--case", str(case), "--out", str(out)]) == 0
    assert json.loads((out / "swatinit_qc.json").read_text())["status"] == "not applicable"
    assert "not applicable" in (out / "report.md").read_text()


@pytest.mark.skipif(
    not os.environ.get("SWATINIT_REAL_GLOB"),
    reason="Set SWATINIT_REAL_GLOB for four public case realizations",
)
def test_real_prior(tmp_path):
    out = Path(os.environ.get("SWATINIT_REAL_OUT", tmp_path / "real-test"))
    assert qc.main(["--ensemble-glob", os.environ["SWATINIT_REAL_GLOB"], "--out", str(out)]) == 1
    result = json.loads((out / "swatinit_qc.json").read_text())
    assert len(result["realizations"]) == 4
    assert all(r["hc_below_fwl_pct"] < 0 for r in result["realizations"])
    assert any(c["parameter"].endswith("OWC_CENTRAL") and c["finding"] for c in result["correlations"])
    for row in result["realizations"]:
        print(
            {
                k: row[k]
                for k in ("realization", "hc_below_fwl_pct", "swl_trunc_pct", "pc_scaling_max", "seconds")
            }
        )


@pytest.mark.parametrize(
    "error", [FileNotFoundError("INIT missing"), ValueError("UNRST must contain report step 0")]
)
def test_cli_input_failure(monkeypatch, tmp_path, error):
    case = tmp_path / "source/X.DATA"
    case.parent.mkdir()
    case.touch()

    def fail(_):
        raise error

    monkeypatch.setattr(qc, "analyze_case", fail)
    out = tmp_path / "out"
    assert qc.main(["--case", str(case), "--out", str(out)]) == 2
    assert not out.exists()


def test_missing_backend_and_headless(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "subscript.check_swatinit":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setenv("MPLBACKEND", "QtAgg")
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    with pytest.raises(ValueError, match="must be installed"):
        qc.load_backend()
    assert os.environ["MPLBACKEND"] == "Agg"
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"


@pytest.mark.parametrize(
    "column,value",
    [
        ("SWAT", float("nan")),
        ("SWATINIT", 2.0),
        ("PORV", -1.0),
        ("PC_SCALING", float("inf")),
        ("QC_FLAG", None),
    ],
)
def test_invalid_cells_fail(cells, column, value):
    cells.loc[0, column] = value
    with pytest.raises(ValueError):
        qc.summarize(cells)


def test_contact_parameters(tmp_path):
    case = tmp_path / "model/X.DATA"
    case.parent.mkdir()
    params = tmp_path / "parameters.txt"
    params.write_text("GROUP:OWC_A -4\nGROUP:GOC 5\nOTHER 2\n")
    assert qc.contact_parameters(case, None, None) == ({"GROUP:OWC_A": -4.0, "GROUP:GOC": 5.0}, None)
    assert qc.contact_parameters(case, "../parameters.txt", ["GOC"]) == ({"GROUP:GOC": 5.0}, None)
    with pytest.raises(ValueError, match="missing"):
        qc.contact_parameters(case, None, ["FWL"])
    params.write_text("GROUP:OWC_A nan\n")
    with pytest.raises(ValueError, match="non-finite"):
        qc.contact_parameters(case, None, None)


def test_ensemble_and_keep_cells(monkeypatch, tmp_path, cells):
    for i in range(4):
        folder = tmp_path / f"source/realization-{i}/iter-0/model"
        folder.mkdir(parents=True)
        (folder / "X.DATA").touch()
        (folder.parent / "parameters.txt").write_text(f"GEN:OWC {i}\n")
    monkeypatch.setattr(qc, "analyze_case", lambda _: (cells, None))
    out = tmp_path / "out"
    pattern = str(tmp_path / "source/realization-*/iter-0/model/X")
    assert qc.main(["--ensemble-glob", pattern, "--out", str(out), "--keep-cells"]) == 1
    assert len(list(out.glob("cells-*.csv"))) == 4
    result = json.loads((out / "swatinit_qc.json").read_text())
    assert result["correlations"][0]["spearman"] is None  # Constant QC response.
    assert result["correlations"][0]["finding"] is None
    assert pd.read_csv(out / "swatinit_qc.csv")["hc_below_fwl_pct"].tolist() == pytest.approx(
        [-100 * 20 / 240] * 4
    )
    assert qc.main(["--ensemble-glob", pattern + "_missing", "--out", str(tmp_path / "none")]) == 2
    assert qc.main(["--ensemble-glob", pattern, "--out", str(out)]) == 2
    assert qc.main(["--ensemble-glob", pattern, "--out", str(folder / "unsafe")]) == 2


def test_ok_thresholds(monkeypatch, tmp_path, cells):
    case = tmp_path / "source/X.DATA"
    case.parent.mkdir()
    case.touch()
    monkeypatch.setattr(qc, "analyze_case", lambda _: (cells, None))
    out = tmp_path / "out"
    assert (
        qc.main(
            [
                "--case",
                str(case.with_suffix("")),
                "--out",
                str(out),
                "--max-hc-below-fwl",
                "9",
                "--max-swl-trunc",
                "5",
                "--max-pc-scaling",
                "30",
            ]
        )
        == 0
    )
    assert json.loads((out / "swatinit_qc.json").read_text())["status"] == "ok"
