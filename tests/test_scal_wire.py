"""SCAL wiring preserves source files and writes semeio scalar parameters."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/scal-uncertainty/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("scal_wire_test", SCRIPTS / "scal_wire.py")
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)

DECK = """RUNSPEC
OIL
WATER
TABDIMS
 2* 2 2* /
PROPS
SWOF
0.2 0 1 0
1 1 0 0 /
SOLUTION
END
"""


def test_rewrite_tables_and_dimensions():
    actual, report = wire.rewrite_deck(DECK, 91)
    assert "SWOF" not in actual
    assert "INCLUDE\n 'relperm.inc' /" in actual
    assert wire.deck_dimensions(actual) == (1, 91)
    assert "SOLUTION\nEND" in actual
    assert report["nssfun_before"] == 2


def test_rewrite_multisatnum_and_gas():
    text = DECK.replace("2* 2 2*", "2 1 200 2*")
    text = text.replace(
        "SOLUTION", "0.2 0 1 0\n1 1 0 0 /\nSGOF\n0 0 1 0\n1 1 0 0 /\n0 0 1 0\n1 1 0 0 /\nSOLUTION"
    )
    actual, report = wire.rewrite_deck(text, 91)
    assert actual.count("relperm.inc") == 1
    assert "SGOF" not in actual and "SWOF" not in actual
    assert report["nssfun_after"] == 200
    assert wire.deck_dimensions(actual) == (2, 200)


def test_rewrite_missing_table_fails():
    with pytest.raises(ValueError, match="fewer"):
        wire.rewrite_deck(DECK.replace("2* 2 2*", "2 1 2 2*"), 91)


def test_groups():
    assert wire.parse_groups("sand:1,2;shale:3") == {"sand": [1, 2], "shale": [3]}
    with pytest.raises(ValueError, match="multiple"):
        wire.parse_groups("sand:1,shale:1")


@pytest.mark.parametrize(
    "prior,distribution", [("uniform", "UNIFORM -1 1"), ("truncnorm", "TRUNCATED_NORMAL 0 0.5 -1 1")]
)
def test_wire_files(tmp_path, prior, distribution):
    pytest.importorskip("pyscal")
    deck = tmp_path / "CASE.DATA"
    deck.write_text(DECK)
    config = tmp_path / "config.ert"
    config.write_text(f"DATA_FILE {deck}\nFORWARD_MODEL FLOW(<ECLBASE>=<ECLBASE>)\n")
    rec = ROOT / "tests/data/scal/egg_scalrec.csv"
    out = tmp_path / "wired"
    result = wire.wire(rec, deck, config, {"sand": [1]}, out, prior)
    assert (out / "scal.dist").read_text() == f"SCAL_WO_sand {distribution}\n"
    line = result["forward_model"]
    assert "<INT_PARAM_WO_NAME>=SCAL_WO_sand" in line
    assert "SCAL:" not in line
    assert "<RESULT_FILE>=model/relperm.inc" in line
    assert (out / "config.ert").read_text().index("PYSCAL") < (out / "config.ert").read_text().index("FLOW")
    assert wire.deck_dimensions((out / deck.name).read_text())[1] >= 91
    assert deck.read_text() == DECK
    assert json.loads((out / "wire.json").read_text())["parameters"] == ["SCAL_WO_sand"]


def test_wire_rejects_unsupported_group_mapping(tmp_path):
    pytest.importorskip("pyscal")
    with pytest.raises(ValueError, match="one interpolation group"):
        wire.wire(
            tmp_path / "rec", tmp_path / "deck", tmp_path / "ert", {"one": [1], "two": [2]}, tmp_path / "out"
        )


def test_exponential_table_numbers():
    actual, _ = wire.rewrite_deck(DECK.replace("0.2 0 1 0", "2.0e-1, 0.0E+0, 1.0D+0, 0"), 91)
    assert "relperm.inc" in actual
