"""SCAL screen checks use isolated inputs and observable summary values."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/scal-uncertainty/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("scal_screen_test", SCRIPTS / "scal_screen.py")
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)


@pytest.mark.parametrize("quoted", [True, False])
def test_copy_nested_inputs(tmp_path, quoted):
    source = tmp_path / "source"
    (source / "model").mkdir(parents=True)
    (source / "include").mkdir()
    deck = source / "model/CASE.DATA"
    include = "'../include/rock.inc'" if quoted else "../include/rock.inc"
    deck.write_text(f"INCLUDE\n {include} /\n")
    (source / "include/rock.inc").write_text("PORO\n1 /\n")
    target = tmp_path / "case/model/CASE.DATA"
    screen.copy_deck_inputs(deck, target, tmp_path / "case")
    assert target.read_text() == deck.read_text()
    assert (tmp_path / "case/include/rock.inc").read_text() == "PORO\n1 /\n"


def test_copy_rejects_absolute_reference(tmp_path):
    deck = tmp_path / "CASE.DATA"
    deck.write_text("INCLUDE '/outside/file' /\n")
    with pytest.raises(ValueError, match="Absolute"):
        screen.copy_deck_inputs(deck, tmp_path / "case/model/CASE.DATA", tmp_path / "case")


def test_missing_flow_fails_closed(tmp_path):
    pytest.importorskip("pyscal")
    with pytest.raises(ValueError, match="Flow executable is missing"):
        screen.screen(
            tmp_path / "rec",
            tmp_path / "deck",
            [-1, 0, 1],
            tmp_path / "out",
            ["no-such-flow-executable-scal-test"],
        )
    assert not (tmp_path / "out").exists()


def test_missing_base_fails(tmp_path):
    pytest.importorskip("pyscal")
    with pytest.raises(ValueError, match="include base 0"):
        screen.screen(tmp_path / "rec", tmp_path / "deck", [-1, 1], tmp_path / "out", ["flow"])


def test_summary_values_and_breakthrough(monkeypatch):
    pytest.importorskip("resdata")
    import resdata.summary

    vectors = {
        "TIME": [0, 30, 60],
        "FOPT": [0, 100, 200],
        "FWPT": [0, 1, 20],
        "FOPR": [1, 9, 4],
        "FWPR": [0, 1, 1],
    }

    class Summary:
        def __init__(self, _):
            pass

        def has_key(self, key):
            return key in vectors

        dates = ["2020-01-01", "2020-01-31", "2020-03-01"]

        def unit(self, key):
            return "SM3"

        def numpy_vector(self, key):
            return vectors[key]

    monkeypatch.setattr(resdata.summary, "Summary", Summary)
    result = screen.summary_metrics(Path("synthetic"))
    assert result["FOPT"] == 200
    assert result["FWPT"] == 20
    assert result["FWCT_end"] == 0.2
    assert result["bt10_days"] == 60  # Strictly greater than 0.1.
    del vectors["FOPT"]
    with pytest.raises(ValueError, match="Missing required"):
        screen.summary_metrics(Path("synthetic"))


def test_geological_span(monkeypatch, tmp_path):
    pytest.importorskip("numpy")
    for i in range(3):
        folder = tmp_path / f"realization-{i}/iter-0"
        folder.mkdir(parents=True)
        (folder / "CASE.SMSPEC").touch()

    def metrics(path):
        return {
            "FOPT": [80, 100, 120][int(path.parent.parent.name[-1])],
            "end_date": "2020-01-01",
            "FOPT_unit": "SM3",
            "FWPT_unit": "SM3",
        }

    monkeypatch.setattr(screen, "summary_metrics", metrics)
    pattern = str(tmp_path / "realization-*/iter-0/CASE")
    result = screen.geological_context(pattern, 16)
    assert result["members"] == 3
    assert result["span_pct_P50"] == pytest.approx(32)
    assert result["scal_to_geological_span"] == pytest.approx(0.5)
    (tmp_path / "realization-1/iter-0/CASE.SMSPEC").unlink()
    with pytest.raises(ValueError, match="Expected one summary"):
        screen.geological_context(pattern, 16)
    with pytest.raises(ValueError, match="same end date"):
        screen.geological_context(
            str(tmp_path / "realization-0/iter-0/CASE"),
            16,
            {"end_date": "later", "FOPT_unit": "SM3", "FWPT_unit": "SM3"},
        )
    with pytest.raises(ValueError, match="matched no"):
        screen.geological_context(str(tmp_path / "missing*/CASE"), 16)


def test_real_public_screen(tmp_path):
    """Opt in with SCAL_REAL, SCAL_DECK, and optional SCAL_PREPARE/SCAL_COPY."""
    import os
    import shutil

    if os.environ.get("SCAL_REAL") != "1":
        pytest.skip("Set SCAL_REAL=1 for the public Flow screen")
    if shutil.which("flow") is None:
        pytest.skip("The real screen requires flow on PATH")
    deck_name = os.environ.get("SCAL_DECK")
    if not deck_name:
        pytest.skip("Set SCAL_DECK to a public runnable DATA file")
    pytest.importorskip("pyscal")
    pytest.importorskip("resdata")
    rec = Path(os.environ.get("SCAL_REC", ROOT / "tests/data/scal/egg_scalrec.csv"))
    copy = [Path(value) for value in os.environ.get("SCAL_COPY", "").split(os.pathsep) if value]
    report = screen.screen(
        rec,
        Path(deck_name),
        [-1, 0, 1],
        tmp_path / "screen",
        ["flow"],
        os.environ.get("SCAL_PREPARE"),
        copy,
        os.environ.get("SCAL_ENSEMBLE_GLOB"),
    )
    assert len(report["cases"]) == 3
    assert all(case["returncode"] == 0 and case["FOPT"] > 0 for case in report["cases"])
    assert report["scal_span"] > 0
    assert (tmp_path / "screen/screen.json").is_file()
    assert (tmp_path / "screen/screen.md").is_file()


def test_missing_recommendation_satnum_stops_before_flow(tmp_path, monkeypatch):
    pytest.importorskip("pyscal")
    import scal_rec

    deck = tmp_path / "CASE.DATA"
    deck.write_text("TABDIMS\n2 1 20 /\nSWOF\n0 0 1 0 /\n0 0 1 0 /\n")
    monkeypatch.setattr(
        scal_rec,
        "generate_tables",
        lambda *args: [
            {"param": 0.0, "tables": [{"keyword": "SWOF", "satnum": 1, "rows": 2}], "qc": {"returncode": 0}}
        ],
    )
    monkeypatch.setattr(screen.shutil, "which", lambda _: "/flow")
    with pytest.raises(ValueError, match="SATNUMs differ"):
        screen.screen(tmp_path / "rec", deck, [0], tmp_path / "out", ["flow"])
    assert not (tmp_path / "out/p0").exists()


def test_copy_cycle_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    deck = source / "CASE.DATA"
    deck.write_text("INCLUDE 'CASE.DATA' /\n")
    with pytest.raises(ValueError, match="Cyclic"):
        screen.copy_deck_inputs(deck, tmp_path / "out/model/CASE.DATA", tmp_path / "out")
