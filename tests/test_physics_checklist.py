import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/deck-scal-pvt-qc/scripts/physics_checklist.py"
spec = importlib.util.spec_from_file_location("physics_checklist", SCRIPT)
pc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pc  # dataclasses look up their module
spec.loader.exec_module(pc)

# Keyword shapes of the drogon-lite decks, on a 3 x 2 x 2 grid. Layer 1 over layer 2 has a shale barrier.
GRID_FULL = """FAULTS
  'F1S' 2 2 1 1 1 2 'X' /
  'F1N' 2 2 2 2 1 2 'X' /
/
PERMX
  12*100 /
PERMY
  12*100 /
PERMZ
  12*10 /
MULTZ
  6*0.001 6*1 /
MULTFLT
  'F1N' 0.296 /
  'F1S' 0.0258 /
/
AQUANCON
  1 1 3 1 1 1 2 'J-' /
/
"""
AQUIFER = """AQUFETP
  1 2000 1* 5.4e+06 0.0001 7.3 1 /
/
"""
WELLS = """WELSPECS
  'P1' 'PROD' 1 1 1* 'OIL' /
  'I1' 'INJ'  3 2 1* 'WATER' /
/
COMPDAT
  'P1' 2* 1 2 'OPEN' 2* 0.20 /
  'I1' 2* 1 2 'OPEN' 2* 0.20 /
/
"""
WPIMULT = """WPIMULT
  'P1' 0.43 /
/
"""
HISTORY = """WCONINJE
  'I1' 'WATER' 'OPEN' 'RATE' 450 1* 280 /
/
WCONHIST
  'P1' 'OPEN' 'LRAT' 300.00 0.00 28776.9 /
/
TSTEP
  1*30 /
WCONPROD
  'P1' 'OPEN' 'LRAT' 3* 300 1* 120 /
/
TSTEP
  1*30 /
"""
OLD_GRID = GRID_FULL.split("MULTZ")[0] + "MULTFLT\n  'F1*' 0.1 /\n/\n"
OLD_HISTORY = """WCONPROD
  'P1' 'OPEN' 'LRAT' 3* 300 1* 120 /
/
WCONINJE
  'I1' 'WATER' 'OPEN' 'RATE' 450 1* 280 /
/
TSTEP
  2*30 /
"""


def deck(tmp_path: Path, includes: dict[str, str], skip: tuple[str, ...] = ()) -> Path:
    """CASE.DATA in model/ with INCLUDE '../include/<name>' lines, as the drogon-lite deck has them."""
    sections = {"grid": "GRID", "rock": "PROPS", "aquifer": "SOLUTION", "wells": "SCHEDULE"}
    lines = ["RUNSPEC", "DIMENS", "  3 2 2 /", "OIL", "WATER", "METRIC"]
    (tmp_path / "include").mkdir()
    (tmp_path / "model").mkdir()
    for name, text in includes.items():
        if name in sections:
            lines.append(sections[name])
        lines += ["INCLUDE", f"  '../include/{name}.inc' / -- exported by mock rms"]
        if name not in skip:
            (tmp_path / "include" / f"{name}.inc").write_text(text)
    path = tmp_path / "model" / "CASE.DATA"
    path.write_text("\n".join([*lines, "END", ""]))
    return path


def full(tmp_path: Path, **kwargs) -> Path:
    return deck(
        tmp_path,
        {
            "grid": GRID_FULL,
            "rock": "ROCK\n  200 5e-05 /\n",
            "aquifer": AQUIFER,
            "wells": WELLS,
            "wpimult": WPIMULT,
            "hist": HISTORY,
        },
        **kwargs,
    )


def old(tmp_path: Path) -> Path:
    return deck(
        tmp_path, {"grid": OLD_GRID, "rock": "ROCK\n  200 5e-05 /\n", "wells": WELLS, "hist": OLD_HISTORY}
    )


def run(*args: str) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = pc.main([*args, "--json"])
    return code, json.loads(buffer.getvalue())


def items(result: dict) -> dict[str, dict]:
    return {item["code"]: item for item in result["items"]}


def ert_config(tmp_path: Path, dist: str, extra: str = "") -> Path:
    folder = tmp_path / "ert"
    folder.mkdir()
    (folder / "params.dist").write_text(dist)
    path = folder / "case.ert"
    path.write_text(f"NUM_REALIZATIONS 10\n{extra}GEN_KW GLOBVAR params.dist\n")
    return path


FULL_DIST = """AQ_VOL LOGUNIF 1.0e6 1.0e8
MULTFLT_F1N LOGUNIF 0.001 1
MULTFLT_F1S LOGUNIF 0.001 1
KVKH LOGUNIF 0.02 0.5
MULTZ_BARRIER LOGUNIF 0.0001 1
PI_P1 LOGUNIF 0.25 4
"""


def test_full_physics_deck_has_no_warnings(tmp_path):
    code, result = run("--deck", str(full(tmp_path)))
    assert code == 0
    assert result["warnings"] == []
    assert {c: i["status"] for c, i in items(result).items()} == dict.fromkeys(
        ["PHYS-AQUIFER", "PHYS-FAULT-SEG", "PHYS-KVKH", "PHYS-WELL-PI", "PHYS-WCONHIST", "PHYS-ROCK"],
        "present",
    )
    kvkh = items(result)["PHYS-KVKH"]
    assert "PERMZ/PERMX from 0.1 to 0.1" in kvkh["notes"]
    assert "barriers: MULTZ down to 0.001 in layer 1" in kvkh["notes"]
    assert "WCONPROD after the last history keyword (forecast): P1" in items(result)["PHYS-WCONHIST"]["notes"]


def test_old_shape_deck_warns_on_aquifer_faults_history_and_pi(tmp_path):
    code, result = run("--deck", str(old(tmp_path)))
    assert code == 0
    assert set(result["warnings"]) == {"PHYS-AQUIFER", "PHYS-FAULT-SEG", "PHYS-WCONHIST", "PHYS-WELL-PI"}
    found = items(result)
    assert "covers the segments F1S, F1N" in found["PHYS-FAULT-SEG"]["notes"][0]
    assert found["PHYS-WCONHIST"]["notes"][0] == "producers on WCONPROD rate control, no WCONHIST: P1"
    assert found["PHYS-KVKH"]["status"] == "present"
    assert found["PHYS-KVKH"]["notes"][-1].startswith("no barrier found")


def test_missing_include_is_reported_and_makes_the_item_unverified(tmp_path):
    code, result = run("--deck", str(full(tmp_path, skip=("aquifer",))))
    assert code == 0
    assert result["include_findings"] == [
        {
            "code": "PHYS-INCLUDE",
            "severity": "warning",
            "where": "CASE.DATA:15",
            "target": "../include/aquifer.inc",
            "kind": "missing",
        }
    ]
    aquifer = items(result)["PHYS-AQUIFER"]
    assert aquifer["status"] == "unverified"
    assert "include files not read: ../include/aquifer.inc" in aquifer["notes"]
    assert set(result["warnings"]) == {"PHYS-AQUIFER", "PHYS-INCLUDE"}


def test_json_schema_is_stable(tmp_path):
    _, result = run("--deck", str(full(tmp_path)))
    assert list(result) == [
        "schema_version",
        "deck",
        "ert_config",
        "items",
        "include_findings",
        "ert_problems",
        "warnings",
    ]
    assert result["schema_version"] == 1
    assert [i["code"] for i in result["items"]] == [
        "PHYS-AQUIFER", "PHYS-FAULT-SEG", "PHYS-KVKH", "PHYS-WELL-PI", "PHYS-WCONHIST", "PHYS-ROCK",
    ]  # fmt: skip
    for item in result["items"]:
        assert list(item) == [
            "code", "title", "status", "severity", "uncertain", "summary", "reason", "evidence", "evidence_count",
            "notes",
        ]  # fmt: skip
        assert item["status"] in {s.value for s in pc.Status}
        assert item["severity"] in {"ok", "warning", "info"}
        for evidence in item["evidence"]:
            assert list(evidence) == ["file", "line", "keyword", "text"]
    assert items(result)["PHYS-AQUIFER"]["evidence"][0] == {
        "file": "aquifer.inc",
        "line": 2,
        "keyword": "AQUFETP",
        "text": "1 2000 * 5.4e+06 0.0001 7.3 1",
    }


def test_ert_parameters_make_items_uncertain_and_a_define_fixes_rock(tmp_path):
    config = ert_config(tmp_path, FULL_DIST, "DEFINE <ROCK_CMULT> 1.0\n")
    code, result = run("--deck", str(full(tmp_path)), "--ert-config", str(config))
    assert code == 0
    assert result["warnings"] == []
    found = items(result)
    assert all(found[c]["uncertain"] for c in ("PHYS-AQUIFER", "PHYS-FAULT-SEG", "PHYS-KVKH", "PHYS-WELL-PI"))
    assert found["PHYS-AQUIFER"]["summary"].endswith("uncertain (GLOBVAR:AQ_VOL)")
    assert found["PHYS-ROCK"]["status"] == "fixed-not-uncertain"
    assert found["PHYS-ROCK"]["severity"] == "info"
    assert "DEFINE <ROCK_CMULT> 1.0 fixes the value" in found["PHYS-ROCK"]["notes"]


def test_fixed_aquifer_and_one_fault_parameter_for_two_segments(tmp_path):
    config = ert_config(tmp_path, "MULTFLT_F1 LOGUNIF 0.001 1\nKVKH LOGUNIF 0.02 0.5\nPI_P1 LOGUNIF 0.25 4\n")
    _, result = run("--deck", str(full(tmp_path)), "--ert-config", str(config))
    found = items(result)
    assert found["PHYS-AQUIFER"]["status"] == "fixed-not-uncertain"
    assert found["PHYS-FAULT-SEG"]["status"] == "missing"
    assert found["PHYS-FAULT-SEG"]["notes"][0] == "1 fault parameter(s) in ERT for 2 fault segments"
    assert set(result["warnings"]) == {"PHYS-AQUIFER", "PHYS-FAULT-SEG"}


def test_placeholders_that_resolve_to_one_parameter_are_a_single_multiplier(tmp_path):
    grid = GRID_FULL.replace("0.296", "<MULTFLT_F1N>").replace("0.0258", "<MULTFLT_F1S>")
    path = full(tmp_path)
    (tmp_path / "include" / "grid.inc").write_text(grid)
    _, alone = run("--deck", str(path))
    assert items(alone)["PHYS-FAULT-SEG"]["status"] == "present"
    assert items(alone)["PHYS-FAULT-SEG"]["uncertain"] is True
    defines = "DEFINE <MULTFLT_F1N> <MULTFLT_F1>\nDEFINE <MULTFLT_F1S> <MULTFLT_F1>\n"
    config = ert_config(
        tmp_path, FULL_DIST.replace("MULTFLT_F1N", "MULTFLT_F1").replace("MULTFLT_F1S", "X"), defines
    )
    _, result = run("--deck", str(path), "--ert-config", str(config))
    fault = items(result)["PHYS-FAULT-SEG"]
    assert fault["status"] == "missing"
    assert "the segments F1S, F1N resolve to one parameter GLOBVAR:MULTFLT_F1" in fault["notes"][0]


def test_aquifer_without_a_connection_is_missing(tmp_path):
    path = full(tmp_path)
    grid = tmp_path / "include" / "grid.inc"
    grid.write_text(grid.read_text().split("AQUANCON")[0])
    _, result = run("--deck", str(path))
    aquifer = items(result)["PHYS-AQUIFER"]
    assert aquifer["status"] == "missing"
    assert aquifer["notes"][0] == "AQUFETP has no AQUANCON. The aquifer is not connected to the grid"


def test_exit_codes(tmp_path, capsys):
    assert pc.main(["--deck", str(tmp_path / "nothing.DATA")]) == 1
    assert pc.main(["--deck", str(full(tmp_path)), "--ert-config", str(tmp_path / "no.ert")]) == 2
    with pytest.raises(SystemExit) as stop:
        pc.main([])
    assert stop.value.code == 2


def test_table_names_each_warning(tmp_path, capsys):
    assert pc.main(["--deck", str(old(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "WARNING PHYS-AQUIFER missing: Without aquifer support" in out
    assert out.rstrip().endswith("4 warning(s): PHYS-AQUIFER PHYS-FAULT-SEG PHYS-WELL-PI PHYS-WCONHIST")
