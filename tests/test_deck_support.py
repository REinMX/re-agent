"""flow-preflight deck_support.py: keyword classification against the pinned OPM Flow support table.

Public decks: set OPM_TESTS to an OPM/opm-tests checkout (gaslift, network, rescoup folders).
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest
from synth_fmu import need

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/flow-preflight/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import deck_support as ds  # noqa: E402
import flow_keyword_table as fkt  # noqa: E402

HEAD = """RUNSPEC
DIMENS
 2 2 1 /
OIL
WATER
GAS
START
 1 JAN 2020 /
WELLDIMS
 3 3 2 3 /
GRID
DX
 4*100 /
DY
 4*100 /
DZ
 4*10 /
TOPS
 4*2000 /
PORO
 4*0.2 /
SCHEDULE
"""

CONTROLS = """GRUPMAST
 'G1' 'S1' 'G1S' /
/
WLIFTOPT
 'P1' YES 1000 1.0 1* 0.5 YES /
/
NETBALAN
 1.0 /
WCONPROD
 'P1' OPEN ORAT 3* /
/
END
"""


def deck(tmp_path: Path, schedule: str, name: str = "CASE.DATA") -> Path:
    path = tmp_path / name
    path.write_text(HEAD + schedule)
    return path


def run(capsys, *args: str) -> tuple[int, str]:
    code = ds.main([str(a) for a in args])
    return code, capsys.readouterr().out


def by_name(result_file: Path) -> dict[str, dict]:
    return {row["name"]: row for row in json.loads(result_file.read_text())["keywords"]}


def test_flow_target_fails_on_the_items_flow_rejects(tmp_path, capsys):
    need("opm")
    out = tmp_path / "support.json"
    code, text = run(capsys, "--deck", deck(tmp_path, CONTROLS), "--target", "FLOW", "--out", out)
    rows = by_name(out)
    assert code == 1 and "verdict target: FAIL; box Flow test: FAIL" in text
    assert rows["GRUPMAST"]["status"] == "flow-unsupported" and rows["GRUPMAST"]["critical"]
    assert [(v["item"], v["value"]) for v in rows["WLIFTOPT"]["violations"]] == [(6, 0.5), (7, "YES")]
    assert [(v["item"], v["value"]) for v in rows["NETBALAN"]["violations"]] == [(1, 1.0)]
    assert rows["WCONPROD"]["status"] == "flow-ok"
    assert rows["GRUPMAST"]["first_location"] == "CASE.DATA:23"


def test_eclipse100_target_passes_and_flags_the_box_flow_test(tmp_path, capsys):
    need("opm")
    path = deck(tmp_path, CONTROLS)
    code, text = run(capsys, "--deck", path, "--target", "ECLIPSE100")
    assert code == 0 and "verdict target: PASS; box Flow test: FAIL" in text
    assert "A box Flow test of this deck needs the critical keywords removed" in text
    assert "not what ECLIPSE accepts" in text and "these keywords are fine" not in text
    code, _ = run(capsys, "--deck", path, "--target", "ECLIPSE100", "--box-flow")
    assert code == 1


def test_defaulted_items_are_not_flagged(tmp_path, capsys):
    need("opm")
    out = tmp_path / "s.json"
    code, text = run(
        capsys,
        "--deck",
        deck(tmp_path, "WLIFTOPT\n 'P1' YES 1* 1* 1* /\n/\nEND\n"),
        "--target",
        "FLOW",
        "--out",
        out,
    )
    row = by_name(out)["WLIFTOPT"]
    assert code == 0 and "box Flow test: PASS" in text
    assert row["status"] == "flow-ok" and "partially supported" in row["note"]


def test_unknown_keyword_is_eclipse_only_with_its_include_location(tmp_path, capsys):
    need("opm")
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc/sched.inc").write_text("WCONPROD\n 'P1' OPEN ORAT 3* /\n/\nGPR2\n/\n")
    out = tmp_path / "s.json"
    code, _ = run(
        capsys,
        "--deck",
        deck(tmp_path, "INCLUDE\n 'inc/sched.inc' /\nEND\n"),
        "--target",
        "FLOW",
        "--out",
        out,
    )
    row = by_name(out)["GPR2"]
    assert code == 1
    assert row["status"] == "eclipse-only" and row["first_location"] == "inc/sched.inc:4"


def test_a_data_line_after_a_keyword_is_not_a_keyword(tmp_path, capsys):
    need("opm")
    head = HEAD.replace("RUNSPEC\n", "RUNSPEC\nTITLE\nNORNE\n")
    head = head.replace("SCHEDULE\n", "SUMMARY\nWOPR\nP1\n/\nSCHEDULE\n")
    path = tmp_path / "CASE.DATA"
    path.write_text(head + "NOECHO\nFOOBARKW\n/\nEND\n")
    out = tmp_path / "s.json"
    code, _ = run(capsys, "--deck", path, "--target", "FLOW", "--out", out)
    rows = by_name(out)
    assert code == 1 and "NORNE" not in rows and "P1" not in rows
    assert rows["FOOBARKW"]["status"] == "eclipse-only"


def test_non_critical_unsupported_is_a_warning(tmp_path, capsys):
    need("opm")
    out = tmp_path / "s.json"
    code, text = run(capsys, "--deck", deck(tmp_path, "NOECHO\nEND\n"), "--target", "FLOW", "--out", out)
    assert code == 0 and "verdict target: WARN" in text
    row = by_name(out)["NOECHO"]
    assert (row["status"], row["critical"]) == ("flow-unsupported", False)


def contract(tmp_path: Path, home: str, company: str) -> Path:
    path = tmp_path / "site.contract.yml"
    path.write_text(
        "schema_version: 2\nactive_profile: home\nprofiles:\n"
        f"  home:\n    simulator:\n      name: {home}\n"
        f"  company:\n    simulator:\n      name: {company}\n"
    )
    return path


def test_target_comes_from_the_contract_profile(tmp_path, capsys):
    need("opm")
    path, site = deck(tmp_path, CONTROLS), contract(tmp_path, "FLOW", "ECLIPSE100")
    code, text = run(capsys, "--deck", path, "--contract", site, "--profile", "company")
    assert code == 0 and "ECLIPSE100 (contract profile company)" in text
    code, text = run(capsys, "--deck", path, "--contract", site, "--profile", "home")
    assert code == 1 and "FLOW (contract profile home)" in text


def test_empty_simulator_name_is_bad_input(tmp_path, capsys):
    code = ds.main(
        ["--deck", str(deck(tmp_path, "END\n")), "--contract", str(contract(tmp_path, "''", "''"))]
    )
    assert code == 2 and "simulator.name is empty" in capsys.readouterr().err


def test_ix_target_is_not_assessed(tmp_path, capsys):
    need("opm")
    code, text = run(capsys, "--deck", deck(tmp_path, CONTROLS), "--target", "IX")
    assert code == 0 and "verdict target: NOT_ASSESSED" in text


def test_table_is_pinned(tmp_path):
    table = ds.load_table()
    assert table["source"]["commit"] == ds.PINNED_COMMIT
    assert {"SLAVES", "GRUPMAST", "GRUPSLAV", "GNETINJE", "WNETCTRL", "NETCOMPA"} <= set(table["unsupported"])
    assert table["partially_supported"]["WLIFTOPT"]["7"]["allow"] == ["NO"]
    changed = copy.deepcopy(table)
    changed["source"]["commit"] = "0" * 40
    (tmp_path / "t.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="refresh both"):
        ds.load_table(tmp_path / "t.json")


def test_rule_expressions_are_restricted():
    assert ds.rule_ok({"expr": "v <= 0.0"}, -1.0) and not ds.rule_ok({"expr": "v <= 0.0"}, 1.0)
    assert ds.rule_ok({"expr": "len(v)<=8"}, "ABCDEFGH") and not ds.rule_ok(
        {"expr": "len(v)<=8"}, "ABCDEFGHI"
    )
    assert not ds.rule_ok({"allow": []}, "X")
    with pytest.raises(ValueError, match="unsafe"):
        ds.rule_ok({"expr": "__import__('os')"}, 1)


CPP_UNSUPPORTED = """
    {"AIM", {false, std::string{"Flow only supports the fully implicit method"}}},
    {"SLAVES", {true, std::nullopt}},
"""
CPP_PARTIAL = """
   static const SupportedKeywordItems<std::string> s = {
         {
            "WLIFTOPT",
            {
               {7,{true, allow_values<std::string> {"NO"}, "WLIFTOPT(OPTLIMIT): only NO"}}, // ALLOCATE
            },
         },
   };
   static const KeywordValidation::SupportedKeywordItems<int> i = {
         {
            "EQUIL",
            {
               {9,{true, [](int x) { return x >= -20 && x <= 20; }, "EQUIL(OIP_INIT): range"}},
               {10,{false, allow_values<int> {1, 3}, "EQUIL(COMP_INIT_TYPE): only types 1 "
                                                       "and 3"}},
            },
         },
   };
   static const KeywordValidation::SupportedKeywordItems<double> d = {
         {
            "GCONPROD",
            {
               {15,{true, allow_values<double> {}, "GCONPROD(RESVFRAC): default it"}},
            },
         },
   };
"""


def test_table_extractor_reads_the_opm_source_format(tmp_path):
    (tmp_path / "UnsupportedFlowKeywords.cpp").write_text(CPP_UNSUPPORTED)
    (tmp_path / "PartiallySupportedFlowKeywords.cpp").write_text(CPP_PARTIAL)
    table = fkt.build(tmp_path, "a" * 40)
    assert table["unsupported"] == {
        "AIM": {"critical": False, "message": "Flow only supports the fully implicit method"},
        "SLAVES": {"critical": True, "message": None},
    }
    partial = table["partially_supported"]
    assert partial["WLIFTOPT"]["7"]["allow"] == ["NO"]
    assert partial["EQUIL"]["9"]["expr"] == "v >= -20 and v <= 20"
    assert partial["EQUIL"]["10"] == {
        "type": "int",
        "critical": False,
        "message": "EQUIL(COMP_INIT_TYPE): only types 1 and 3",
        "allow": [1, 3],
    }
    assert partial["GCONPROD"]["15"]["allow"] == []
    assert table["source"]["files"]["partial"]["url"].endswith(
        "/blob/" + "a" * 40 + "/opm/simulators/utils/PartiallySupportedFlowKeywords.cpp"
    )


def test_committed_table_matches_the_pinned_sources():
    src = os.environ.get("OPM_KW_SRC")
    if not src:
        pytest.skip("Set OPM_KW_SRC to a folder with the two .cpp files of the pinned commit")
    assert fkt.build(Path(src), ds.PINNED_COMMIT) == ds.load_table()


def opm_deck(relative: str) -> Path:
    root = os.environ.get("OPM_TESTS")
    if not root or not (Path(root) / relative).is_file():
        pytest.skip("Set OPM_TESTS to an OPM/opm-tests checkout with gaslift, network and rescoup")
    need("opm")
    return Path(root) / relative


@pytest.mark.parametrize(
    "relative,expected,box",
    [
        (
            "rescoup/rc_m1/master/RC-01_MAST_PRED.DATA",
            {"SLAVES": "flow-unsupported", "GRUPMAST": "flow-unsupported"},
            "FAIL",
        ),
        ("rescoup/rc_m1/mod1/RC-01_MOD1_PRED.DATA", {"GRUPSLAV": "flow-unsupported"}, "FAIL"),
        ("network/GNETINJE_GAS-01.DATA", {"GNETINJE": "flow-unsupported"}, "FAIL"),
        ("network/NETWORK-01.DATA", {"GPR2": "eclipse-only", "BRANPROP": "flow-ok"}, "FAIL"),
        ("network/NETWORK-01_STANDARD.DATA", {"GRUPNET": "flow-ok"}, "WARN"),
        ("gaslift/GASLIFT-02.DATA", {"WLIFTOPT": "flow-ok", "LIFTOPT": "flow-ok"}, "WARN"),
    ],
)
def test_opm_public_decks(tmp_path, capsys, relative, expected, box):
    out = tmp_path / "s.json"
    _, text = run(capsys, "--deck", opm_deck(relative), "--target", "ECLIPSE100", "--out", out)
    rows = by_name(out)
    assert {name: rows[name]["status"] for name in expected} == expected
    assert json.loads(out.read_text())["verdicts"] == {"box_flow": box, "target": "PASS"}
    if box == "WARN":
        assert "Flow ignores the flagged keywords" in text
