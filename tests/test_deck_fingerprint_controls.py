"""scenario-qc deck_fingerprint: gas lift, group tree, network, actions, coupling, and VFP changes are seen.

Public decks: set OPM_TESTS to an OPM/opm-tests checkout (network folder).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, run, write_contracts, write_profiles, yaml_dump

SCRIPTS = SKILLS / "scenario-qc/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import deck_fingerprint as dfp  # noqa: E402

QC = SCRIPTS / "scenario_qc.py"

BASE = {
    "groups": "GRUPTREE\n 'PLAT' 'FIELD' /\n 'G1' 'PLAT' /\n/\n",
    "wells": "WELSPECS\n 'P1' 'G1' 1 1 1* 'OIL' /\n 'P2' 'G1' 2 2 1* 'OIL' /\n/\n",
    "controls": "WCONPROD\n 'P1' 'OPEN' 'ORAT' 100 4* 150 /\n 'P2' 'OPEN' 'ORAT' 100 4* 150 /\n/\n",
    "lift": "LIFTOPT\n 12500 5E-3 0.0 YES /\nWLIFTOPT\n 'P1' YES 150000 1.01 -1.0 /\n/\nGLIFTOPT\n 'PLAT' 200000 /\n/\n",
    "network": "GRUPNET\n 'FIELD' 20.0 5* /\n 'PLAT' 1* 3 5* /\n/\nNETBALAN\n 0.0 0.2 6* /\n",
    "extnet": "NODEPROP\n 'PLAT' 21.0 NO NO /\n/\nBRANPROP\n 'G1' 'PLAT' 2 1* /\n/\n",
    "guide": "GUIDERAT\n 0.0 OIL 1.0 0.5 1* 1* 1* YES 1* 0.0 /\nWGRUPCON\n 'P1' YES 1.0 OIL /\n/\n",
    "udq": "UDQ\n DEFINE FU_LIFT 200000 /\n/\n",
    "action": "ACTIONX\n 'CUT' 1 /\n WWCT 'P1' > 0.9 /\n/\nWCONPROD\n 'P1' 'OPEN' 'ORAT' 50 4* 150 /\n/\nENDACTIO\n",
    "report": "RPTRST\n 'BASIC=2' /\n",
    "time": "TSTEP\n 10*30 /\n",
}


def write(path: Path, parts: dict[str, str], include: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(parts.values())
    if include:
        body = f"INCLUDE\n '{include}' /\n" + body
    path.write_text("RUNSPEC\nSCHEDULE\n" + body + "END\n", encoding="utf-8")
    return path


def diff(tmp_path: Path, **changes: str) -> dict:
    a = dfp.build_fingerprint(write(tmp_path / "a/CASE.DATA", BASE))
    b = dfp.build_fingerprint(write(tmp_path / "b/CASE.DATA", {**BASE, **changes}))
    return dfp.fingerprint_diff(a, b)


@pytest.mark.parametrize(
    "part,text,expected",
    [
        ("lift", BASE["lift"].replace("150000", "300000"), ["WLIFTOPT"]),
        ("lift", BASE["lift"].replace("200000", "100000"), ["GLIFTOPT"]),
        ("network", BASE["network"].replace("20.0", "15.0"), ["GRUPNET"]),
        ("extnet", BASE["extnet"].replace("21.0", "18.0"), ["NODEPROP"]),
        ("extnet", BASE["extnet"].replace("2 1*", "5 1*"), ["BRANPROP"]),
        ("groups", BASE["groups"].replace("'G1' 'PLAT'", "'G1' 'FIELD'"), ["GRUPTREE"]),
        ("guide", BASE["guide"].replace("OIL 1.0 0.5", "GAS 1.0 0.5"), ["GUIDERAT"]),
        ("udq", BASE["udq"].replace("200000", "250000"), ["UDQ"]),
        ("action", BASE["action"].replace("0.9", "0.8"), ["ACTIONX"]),
    ],
)
def test_control_change_is_seen_as_that_keyword(tmp_path, part, text, expected):
    result = diff(tmp_path, **{part: text})
    assert result["changed_keywords"] == expected and not result["combined_equal"]


def test_rate_change_inside_an_action_belongs_to_the_action(tmp_path):
    result = diff(tmp_path, action=BASE["action"].replace("50 4*", "40 4*"))
    assert result["changed_keywords"] == ["ACTIONX"]


def test_untracked_keyword_after_a_tracked_one_is_not_a_change(tmp_path):
    assert diff(tmp_path, report="RPTRST\n 'BASIC=3' /\n")["combined_equal"]


def test_change_in_a_nested_include_is_seen(tmp_path):
    def case(folder: str, rate: str) -> dict:
        root = tmp_path / folder
        (root / "include").mkdir(parents=True)
        (root / "include/lift.inc").write_text(BASE["lift"].replace("150000", rate))
        (root / "include/schedule.inc").write_text("INCLUDE\n 'include/lift.inc' /\n" + BASE["controls"])
        return dfp.build_fingerprint(
            write(root / "CASE.DATA", {"time": BASE["time"]}, "include/schedule.inc")
        )

    result = dfp.fingerprint_diff(case("a", "150000"), case("b", "300000"))
    assert result["changed_keywords"] == ["WLIFTOPT"]


def test_vfp_table_change_is_seen_without_storing_the_table(tmp_path):
    vfp = "VFPPROD\n 3 2000.0 LIQ WCT GOR THP ' ' METRIC BHP /\n 100 /\n 20 /\n 0 /\n 100 /\n 0 /\n 1 1 1 1 150 /\n"
    result = diff(tmp_path, vfp=vfp)
    fp = dfp.build_fingerprint(write(tmp_path / "c/CASE.DATA", {**BASE, "vfp": vfp}))
    assert result["changed_keywords"] == ["VFPPROD"]
    assert fp["keywords"]["VFPPROD"]["table_ids"] == [3] and fp["keywords"]["VFPPROD"]["normalized"] is None


def test_coupling_keywords_are_tracked(tmp_path):
    rc = "SLAVES\n 'S1' 'S1BASE' '*' 'slave1' 1 /\n/\nGRUPMAST\n 'PLAT' 'S1' 'S1PLAT' /\n/\n"
    result = diff(tmp_path, rc=rc)
    assert result["changed_keywords"] == ["GRUPMAST", "SLAVES"]


def test_lift_wells_count_as_mentioned(tmp_path):
    lift = "WLIFTOPT\n 'P9' YES 1* /\n 'OP-A01' YES 1* /\n 'OP*' NO /\n/\n"
    fp = dfp.build_fingerprint(write(tmp_path / "CASE.DATA", {"lift": lift}))
    assert fp["wells_mentioned"] == ["OP-A01", "P9"]


def test_families_of_changed_keywords(tmp_path):
    result = diff(
        tmp_path, lift=BASE["lift"].replace("150000", "300000"), extnet=BASE["extnet"].replace("21.0", "18")
    )
    assert result["changed_families"] == ["gas_lift", "network"]


def test_schema_change_is_reported():
    a = {"schema_version": 1, "combined_sha256": "x", "keywords": {}}
    b = {"schema_version": dfp.SCHEMA_VERSION, "combined_sha256": "x", "keywords": {}}
    assert dfp.fingerprint_diff(a, b)["schema_equal"] is False


def scenario(root: Path, sid: str, deck_parts: dict[str, str], meta: dict) -> None:
    scen = root / "scenarios" / sid
    deck = write(scen / "eclipse/model/TOY.DATA", deck_parts)
    proc = run(SCRIPTS / "deck_fingerprint.py", "--data", deck, "--out", scen / "deck_fingerprint.json")
    assert proc.returncode == 0, proc.stderr
    prof = scen / "deliverables/profiles"
    write_profiles(prof, n=5, p1_factor=meta.pop("p1", 1.0))
    runmeta = {
        "scenario_id": sid,
        "parent_scenario_id": "baseline",
        "profiles_dir": str(prof),
        "p10_p90": "numerical",
        "changes": {"wells": [], "constraints": [], "schedule": []},
        "changed_keywords": [],
        "touched_wells": [],
        "demo": False,
        **meta,
    }
    (scen / "runmeta.yml").write_text(yaml_dump(runmeta), encoding="utf-8")


def qc(tmp_path: Path, sid: str, meta: dict) -> dict:
    need("yaml", "numpy", "pandas")
    proj, env = write_contracts(tmp_path)
    scenario(tmp_path, "baseline", BASE, {})
    scenario(tmp_path, sid, {**BASE, "lift": BASE["lift"].replace("150000", "300000")}, meta)
    proc = run(QC, "--contract", proj, "--scenario", sid, env=env)
    report = tmp_path / "scenarios" / sid / "deliverables/scenario_qc/scenario_qc_report.json"
    return {"code": proc.returncode, **json.loads(report.read_text())}


def test_scenario_qc_fails_an_undeclared_gas_lift_change(tmp_path):
    report = qc(tmp_path, "lift_silent", {})
    assert report["code"] == 2 and "fingerprint_mismatch" in report["flags"]
    assert report["checks"]["fingerprint"]["changed_keywords"] == ["WLIFTOPT"]


def test_scenario_qc_passes_a_declared_gas_lift_change(tmp_path):
    meta = {
        "changes": {"constraints": [{"keyword": "WLIFTOPT", "well": "P1"}], "wells": [], "schedule": []},
        "changed_keywords": ["WLIFTOPT"],
        "touched_wells": ["P1"],
        "expected_changes": {
            "wells": ["P1"],
            "keywords": ["WLIFTOPT"],
            "field_vectors_may_move": ["FOPT", "FOPR"],
        },
        "p1": 1.2,
    }
    report = qc(tmp_path, "lift_more", meta)
    assert report["code"] == 0 and report["status"] == "PASS", report["flags"]


def test_scenario_qc_fails_on_mixed_fingerprint_schemas(tmp_path):
    need("yaml", "numpy", "pandas")
    proj, env = write_contracts(tmp_path)
    scenario(tmp_path, "baseline", BASE, {})
    fp = tmp_path / "scenarios/baseline/deck_fingerprint.json"
    old = json.loads(fp.read_text())
    old["schema_version"] = 1
    scenario(tmp_path, "old", BASE, {})
    (tmp_path / "scenarios/old/deck_fingerprint.json").write_text(json.dumps(old))
    proc = run(QC, "--contract", proj, "--scenario", "old", env=env)
    report = json.loads(
        (tmp_path / "scenarios/old/deliverables/scenario_qc/scenario_qc_report.json").read_text()
    )
    assert proc.returncode == 2 and "fingerprint_mismatch" in report["flags"]
    assert any("schema" in m for m in report["messages"])


def test_opm_standard_vs_extended_network():
    root = os.environ.get("OPM_TESTS")
    if not root or not (Path(root) / "network/NETWORK-01.DATA").is_file():
        pytest.skip("Set OPM_TESTS to an OPM/opm-tests checkout with the network folder")
    std = dfp.build_fingerprint(Path(root) / "network/NETWORK-01_STANDARD.DATA")
    ext = dfp.build_fingerprint(Path(root) / "network/NETWORK-01.DATA")
    result = dfp.fingerprint_diff(std, ext)
    assert {"GRUPNET", "BRANPROP", "NODEPROP"} <= set(result["changed_keywords"])
    assert "network" in result["changed_families"]


def test_moving_a_control_to_another_report_date_is_a_change(tmp_path):
    head = "RUNSPEC\nSTART\n 1 JAN 2020 /\nSCHEDULE\n" + BASE["wells"] + BASE["controls"]
    cut = "WCONPROD\n 'P1' 'OPEN' 'ORAT' 50 4* 150 /\n/\n"
    feb, mar = "DATES\n 1 FEB 2020 /\n/\n", "DATES\n 1 MAR 2020 /\n/\n"
    paths = []
    for name, body in (("a", feb + cut + mar), ("b", feb + mar + cut), ("c", "TSTEP\n 31 /\n" + cut + mar)):
        (tmp_path / name).mkdir()
        paths.append(tmp_path / name / "CASE.DATA")
        paths[-1].write_text(head + body + "TSTEP\n 30 /\nEND\n", encoding="utf-8")
    a, b, c = (dfp.build_fingerprint(p) for p in paths)
    assert a["start"] == "2020-01-01"
    assert dfp.fingerprint_diff(a, b)["changed_keywords"] == ["WCONPROD"]
    # The same dates reached by TSTEP instead of DATES: only the timing keywords differ.
    assert dfp.fingerprint_diff(a, c)["changed_keywords"] == ["DATES", "TSTEP"]


def test_start_in_an_include_after_a_missing_include_resolves(tmp_path):
    """An ERT-written include that is missing in the template deck does not hide START in a later include."""
    (tmp_path / "include").mkdir()
    (tmp_path / "include/runspec.inc").write_text("START\n 1 JAN 2020 /\n", encoding="utf-8")
    deck = tmp_path / "model/CASE.DATA"
    deck.parent.mkdir()
    deck.write_text(
        "RUNSPEC\nINCLUDE\n '../include/rock.inc' /\nINCLUDE\n '../include/runspec.inc' /\nSCHEDULE\n"
        + BASE["wells"]
        + "END\n",
        encoding="utf-8",
    )
    assert dfp.deck_start(deck) is not None
    assert dfp.build_fingerprint(deck)["start"] == "2020-01-01"


def test_scenario_qc_fails_closed_when_only_one_fingerprint_has_dates(tmp_path):
    need("yaml", "numpy", "pandas")
    proj, env = write_contracts(tmp_path)
    scenario(tmp_path, "baseline", BASE, {})
    scenario(tmp_path, "dated", BASE, {})
    deck = tmp_path / "scenarios/dated/eclipse/model/TOY.DATA"
    deck.write_text("RUNSPEC\nSTART\n 1 JAN 2020 /\nSCHEDULE\n" + "".join(BASE.values()) + "END\n")
    fp = run(
        SCRIPTS / "deck_fingerprint.py", "--data", deck, "--out", deck.parents[2] / "deck_fingerprint.json"
    )
    assert fp.returncode == 0, fp.stderr
    proc = run(QC, "--contract", proj, "--scenario", "dated", env=env)
    report = json.loads(
        (tmp_path / "scenarios/dated/deliverables/scenario_qc/scenario_qc_report.json").read_text()
    )
    assert proc.returncode == 2 and "fingerprint_mismatch" in report["flags"]
    fingerprint_messages = [m for m in report["messages"] if "fingerprint" in m or "runmeta" in m]
    assert fingerprint_messages == [
        "one fingerprint has dates and the other does not (baseline START None, scenario START 2020-01-01); "
        "rebuild both from decks whose START resolves"
    ]
