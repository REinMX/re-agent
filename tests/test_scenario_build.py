"""scenario-build scenario_build.py: typed spec -> SCHEDULE include, declaration, deck_support, scenario-qc."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, run, write_contracts, write_profiles, yaml_dump

SCRIPTS = SKILLS / "scenario-build/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import everest_controls as ec  # noqa: E402
import scenario_build as sb  # noqa: E402

QC = SKILLS / "scenario-qc/scripts/scenario_qc.py"
FP = SKILLS / "scenario-qc/scripts/deck_fingerprint.py"

DECK = """RUNSPEC
DIMENS
 3 3 2 /
OIL
WATER
GAS
METRIC
START
 1 JAN 2020 /
WELLDIMS
 6 4 3 6 /
NETWORK
 3 2 /
GRID
DX
 18*100 /
DY
 18*100 /
DZ
 18*10 /
TOPS
 9*2000 /
PORO
 18*0.2 /
SCHEDULE
INCLUDE
 '../include/sched.inc' /
END
"""

SCHED = """WELSPECS
 'P1' 'G1' 3 3 1* 'OIL' /
 'I1' 'G1' 1 1 1* 'WATER' /
/
COMPDAT
 'P1' 3 3 1 2 'OPEN' 2* 0.2 /
 'I1' 1 1 1 2 'OPEN' 2* 0.2 /
/
WCONPROD
 'P1' 'OPEN' 'BHP' 5* 150 /
/
WCONINJE
 'I1' 'WATER' 'OPEN' 'RATE' 500 1* 300 /
/
DATES
 1 'FEB' 2020 /
 1 'MAR' 2020 /
/
TSTEP
 3*30 /
"""


def case(tmp_path: Path) -> Path:
    (tmp_path / "case/model").mkdir(parents=True, exist_ok=True)
    (tmp_path / "case/include").mkdir(exist_ok=True)
    (tmp_path / "case/model/CASE.DATA").write_text(DECK)
    (tmp_path / "case/include/sched.inc").write_text(SCHED)
    (tmp_path / "case/model/CASE.UNSMRY").write_text("result")
    return tmp_path / "case"


def spec(tmp_path: Path, ops: list[dict], sid: str = "s1") -> Path:
    case(tmp_path)
    path = tmp_path / f"{sid}.yml"
    data = {
        "schema_version": 1,
        "scenario_id": sid,
        "case_root": "case",
        "deck": "model/CASE.DATA",
        "operations": ops,
    }
    path.write_text(yaml_dump(data))
    return path


INFILL = {
    "id": "p2",
    "type": "infill",
    "date": "2020-03-01",
    "well": "P2",
    "group": "G1",
    "i": 2,
    "j": 3,
    "phase": "OIL",
    "connections": [{"k1": 1, "k2": 2}],
    "control": {"mode": "ORAT", "orat": 100, "bhp": 150},
}


def build(tmp_path: Path, ops: list[dict], target: str = "ECLIPSE100", out: str = "out") -> tuple[int, Path]:
    need("opm", "yaml")
    code = sb.main(["--spec", str(spec(tmp_path, ops)), "--out", str(tmp_path / out), "--target", target])
    return code, tmp_path / out


def build_with(tmp_path: Path, path: Path, *extra: str) -> tuple[int, Path]:
    need("opm", "yaml")
    return sb.main(
        ["--spec", str(path), "--out", str(tmp_path / "out"), "--target", "ECLIPSE100", *extra]
    ), tmp_path / "out"


def load_everest_lint():
    import importlib.util

    path = SKILLS / "everest-config-author-lint/scripts/everest_lint.py"
    spec_ = importlib.util.spec_from_file_location("everest_lint", path)
    module = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(module)
    return module


def schedule_text(out: Path) -> str:
    return (out / "case/model/CASE_SCHEDULE.inc").read_text()


def report(out: Path) -> dict:
    return json.loads((out / "scenario_build.json").read_text())


def test_infill_on_a_report_date_goes_after_that_date(tmp_path):
    code, out = build(tmp_path, [INFILL])
    text = schedule_text(out)
    assert code == 0
    assert text.index("1 'MAR' 2020") < text.index("'P2' 'G1' 2 3 1* 'OIL' /") < text.index("TSTEP")
    assert " 'P2' 2 3 1 2 'OPEN' /" in text and " 'P2' 'OPEN' 'ORAT' 100 4* 150 /" in text
    meta = sb.contract_lib._yaml().safe_load((out / "runmeta.yml").read_text())
    assert meta["expected_changes"]["keywords"] == ["COMPDAT", "WCONPROD", "WELSPECS"]
    assert meta["expected_changes"]["wells"] == ["P2"] and meta["changes"]["wells"] == ["P2"]
    assert report(out)["declaration_check"] == {
        "fingerprint_changed": ["COMPDAT", "WCONPROD", "WELSPECS"],
        "undeclared": [],
        "declared_but_unchanged": [],
        "status": "PASS",
    }
    assert not (out / "case/model/CASE.UNSMRY").exists()
    assert "INCLUDE\n  'CASE_SCHEDULE.inc' /" in (out / "case/model/CASE.DATA").read_text()


def test_date_inside_tstep_splits_it_and_declares_timing(tmp_path):
    code, out = build(tmp_path, [{**INFILL, "date": "2020-03-16"}])
    text = schedule_text(out)
    assert code == 0
    assert "TSTEP\n 30 /" not in text and "DATES\n 16 'MAR' 2020 /\n/" in text and "TSTEP\n 15 2*30 /" in text
    assert report(out)["timing_changed"] == ["DATES", "TSTEP"]
    assert {"DATES", "TSTEP"} <= set(report(out)["declared"]["keywords"])
    assert report(out)["declaration_check"]["status"] == "PASS"


def test_date_on_a_step_inside_tstep_splits_without_a_new_date(tmp_path):
    code, out = build(tmp_path, [{**INFILL, "date": "2020-03-31"}])
    text = schedule_text(out)
    assert code == 0 and "TSTEP\n 30 /" in text and "TSTEP\n 2*30 /" in text
    assert report(out)["timing_changed"] == ["TSTEP"]


def test_date_between_two_dates_records_splits_dates(tmp_path):
    code, out = build(tmp_path, [{**INFILL, "date": "2020-02-10"}])
    text = schedule_text(out)
    assert code == 0
    assert (
        text.index("1 'FEB' 2020")
        < text.index("10 'FEB' 2020")
        < text.index("'P2' 'G1'")
        < text.index("1 'MAR' 2020")
    )
    assert report(out)["timing_changed"] == ["DATES"]


def test_operation_at_start_goes_before_the_first_step(tmp_path):
    code, out = build(tmp_path, [{**INFILL, "date": "2020-01-01"}])
    text = schedule_text(out)
    assert code == 0 and text.index("'P2' 'G1'") < text.index("DATES") and report(out)["timing_changed"] == []


@pytest.mark.parametrize("day", ["2019-12-31", "2020-05-30", "2021-01-01"])
def test_dates_outside_the_schedule_are_rejected(tmp_path, capsys, day):
    code, _ = build(tmp_path, [{**INFILL, "date": day}])
    assert code == 2 and "operation date" in capsys.readouterr().err


OPS = [
    (
        {"type": "convert_to_injector", "date": "2020-02-01", "well": "P1", "phase": "WATER", "rate": 400, "bhp": 300},
        "WCONINJE\n 'P1' 'WATER' 'OPEN' 'RATE' 400 1* 300 /\n/",
        ["WCONINJE"],
    ),
    (
        {"type": "workover", "date": "2020-02-01", "well": "P1", "well_status": "OPEN",
         "connections": [{"i": 3, "j": 3, "k1": 2, "k2": 2, "state": "SHUT"}, {"i": 3, "j": 3, "k1": 1, "k2": 1, "state": "OPEN", "skin": -1.5, "diameter": 0.2}]},
        "COMPDAT\n 'P1' 3 3 1 1 'OPEN' 2* 0.2 1* -1.5 /\n/\nWELOPEN\n 'P1' 'SHUT' 3 3 2 /\n 'P1' 'OPEN' /\n/",
        ["COMPDAT", "WELOPEN"],
    ),
    (
        {"type": "target", "date": "2020-02-01", "level": "well", "name": "P1", "mode": "GRAT", "grat": 50000, "bhp": 120},
        "WCONPROD\n 'P1' 'OPEN' 'GRAT' 2* 50000 2* 120 /\n/",
        ["WCONPROD"],
    ),
    (
        {"type": "target", "date": "2020-02-01", "level": "well_limit", "name": "P1", "limits": {"ORAT": 80, "BHP": 140}},
        "WELTARG\n 'P1' 'ORAT' 80 /\n 'P1' 'BHP' 140 /\n/",
        ["WELTARG"],
    ),
    (
        {"type": "target", "date": "2020-02-01", "level": "group_production", "name": "G1", "mode": "GRAT", "grat": 1.5e5, "action": "RATE"},
        "GCONPROD\n 'G1' 'GRAT' 2* 150000 1* 'RATE' /\n/",
        ["GCONPROD"],
    ),
    (
        {"type": "target", "date": "2020-02-01", "level": "group_injection", "name": "G1", "phase": "WATER", "mode": "VREP", "vrep": 1.0},
        "GCONINJE\n 'G1' 'WATER' 'VREP' 3* 1 /\n/",
        ["GCONINJE"],
    ),
    (
        {"type": "target", "date": "2020-02-01", "level": "sales", "name": "FIELD", "sales_target": 2e5, "max_rate": 2.5e5, "procedure": "RATE"},
        "GCONSALE\n 'FIELD' 200000 250000 1* 'RATE' /\n/",
        ["GCONSALE"],
    ),
    (
        {"type": "gas_lift", "date": "2020-02-01", "liftopt": {"increment": 12500, "economic_gradient": 0.005},
         "wells": [{"well": "P1", "max_rate": 150000, "weight": 1.0}], "groups": [{"group": "G1", "max_lift": 200000}],
         "alq": [{"well": "P1", "value": 50000}]},
        "LIFTOPT\n 12500 0.005 /\nWLIFTOPT\n 'P1' 'YES' 150000 1 /\n/\nGLIFTOPT\n 'G1' 200000 /\n/\nWELTARG\n 'P1' 'LIFT' 50000 /\n/",
        ["GLIFTOPT", "LIFTOPT", "WELTARG", "WLIFTOPT"],
    ),
    (
        {"type": "back_pressure", "date": "2020-02-01", "nodes": [{"node": "PLAT", "pressure": 25}],
         "branches": [{"downtree": "G1", "uptree": "PLAT", "vfp": 2}], "affected_wells": ["P1"]},
        "BRANPROP\n 'G1' 'PLAT' 2 /\n/\nNODEPROP\n 'PLAT' 25 /\n/",
        ["BRANPROP", "NODEPROP"],
    ),
]  # fmt: skip


@pytest.mark.parametrize("op,text,keywords", OPS, ids=[f"{o['type']}-{i}" for i, (o, _, _) in enumerate(OPS)])
def test_each_operation_renders_eclipse100_text(op, text, keywords):
    need("yaml")
    parsed = sb.OPERATIONS[op["type"]].parse(op, 0)
    rendered = "".join(b.text() for _, b in parsed.events())
    assert rendered.strip() == text
    assert sorted(parsed.declare().keywords) == keywords


@pytest.mark.parametrize("op,text,keywords", OPS, ids=[f"{o['type']}-{i}" for i, (o, _, _) in enumerate(OPS)])
def test_rendered_operations_parse_with_opm(tmp_path, op, text, keywords):
    code, out = build(tmp_path, [op])
    assert code == 0, report(out)
    assert report(out)["declaration_check"]["status"] == "PASS"


def test_wag_alternates_phases_on_dates():
    need("yaml")
    op = sb.Wag.parse(
        {"type": "wag", "well": "I1", "start": "2020-02-01", "cycles": 2,
         "water": {"rate": 500, "bhp": 300, "days": 30}, "gas": {"rate": 80000, "bhp": 300, "days": 15},
         "then": "WATER"}, 0
    )  # fmt: skip
    events = [(str(d), b.records[0].split()[1]) for d, b in op.events()]
    assert events == [
        ("2020-02-01", "'WATER'"),
        ("2020-03-02", "'GAS'"),
        ("2020-03-17", "'WATER'"),
        ("2020-04-16", "'GAS'"),
        ("2020-05-01", "'WATER'"),
    ]


def test_wag_ends_as_the_spec_says():
    need("yaml")
    raw = {"type": "wag", "well": "I1", "start": "2020-02-01", "cycles": 1,
           "water": {"rate": 500, "bhp": 300, "days": 30}, "gas": {"rate": 80000, "bhp": 300, "days": 15}}  # fmt: skip
    shut = sb.Wag.parse({**raw, "then": "SHUT"}, 0).events()
    assert (str(shut[-1][0]), shut[-1][1].keyword) == ("2020-03-17", "WELOPEN")
    assert len(sb.Wag.parse({**raw, "then": "CONTINUE"}, 0).events()) == 2
    with pytest.raises(sb.SpecError, match="'then' is required"):
        sb.Wag.parse(raw, 0)
    with pytest.raises(sb.SpecError, match="takes no 'date'"):
        sb.Wag.parse({**raw, "then": "SHUT", "date": "2020-01-01"}, 0)


def test_workover_keeps_base_completion_items():
    need("yaml")
    op = {"type": "workover", "date": "2020-02-01", "well": "P1"}
    shut = sb.Workover.parse({**op, "connections": [{"i": 3, "j": 3, "k1": 1, "k2": 2, "state": "SHUT"}]}, 0)
    assert (
        "".join(b.text() for _, b in shut.events())
        == "WELOPEN\n 'P1' 'SHUT' 3 3 1 /\n 'P1' 'SHUT' 3 3 2 /\n/\n"
    )
    with pytest.raises(sb.SpecError, match="'diameter' is required"):
        sb.Workover.parse(
            {**op, "connections": [{"i": 3, "j": 3, "k1": 1, "k2": 1, "state": "OPEN", "skin": 0}]}, 0
        )


def test_example_spec_reads():
    need("yaml")
    import yaml

    data = yaml.safe_load((SKILLS / "scenario-build/references/scenario.example.yml").read_text())
    variables = sb.read_variables(data["variables"])
    _, values = sb.read_controls(None, variables)
    used: set[str] = set()
    for n, raw in enumerate(sb.substitute(data["operations"], values, variables, used)):
        sb.OPERATIONS[raw["type"]].parse(raw, n)
    assert used == set(variables)


def test_variable_integer_flag_is_a_boolean():
    with pytest.raises(sb.SpecError, match="integer must be true or false"):
        sb.read_variables({"d": {"initial": 0, "min": 0, "max": 9, "integer": "no"}})


def test_shift_moves_the_named_operations(tmp_path):
    ops = [{**INFILL, "date": "2020-02-01"}, {"type": "shift", "ops": ["p2"], "days": 29}]
    code, out = build(tmp_path, ops)
    assert code == 0 and report(out)["inserts"][0]["date"] == "2020-03-01"


@pytest.mark.parametrize(
    "op,message",
    [
        ({"type": "drill", "date": "2020-02-01"}, "is not one of"),
        ({**INFILL, "colour": "red"}, "unknown key"),
        ({**INFILL, "well": "TOOLONGNAME"}, "1-8 characters"),
        ({**INFILL, "control": {"mode": "ORAT", "bhp": 150}}, "needs 'orat'"),
        ({**INFILL, "control": {"mode": "ORAT", "orat": -5, "bhp": 150}}, ">= 0"),
        ({**INFILL, "date": "01.03.2020"}, "YYYY-MM-DD"),
        ({"type": "workover", "date": "2020-02-01", "well": "P1"}, "connections, well_status"),
        ({"type": "shift", "ops": ["nope"], "days": 10}, "not a non-shift operation"),
        # ECLIPSE WELTARG has no RATE control: an injector rate limit is WRAT, GRAT or ORAT.
        (
            {
                "type": "target",
                "date": "2020-02-01",
                "level": "well_limit",
                "name": "I1",
                "limits": {"RATE": 700},
            },
            "unknown key",
        ),
    ],
)
def test_spec_errors_are_bad_input(tmp_path, capsys, op, message):
    code, _ = build(tmp_path, [op])
    assert code == 2 and message in capsys.readouterr().err


def test_flow_target_fails_on_a_flow_unsupported_item(tmp_path):
    op = {"type": "target", "date": "2020-02-01", "level": "group_production", "name": "G1", "mode": "ORAT",
          "orat": 100, "action": "CON"}  # fmt: skip
    code, out = build(tmp_path, [op], target="FLOW")
    assert code == 1 and report(out)["deck_support"]["verdicts"] == {"box_flow": "FAIL", "target": "FAIL"}
    code, out = build(tmp_path, [op], target="ECLIPSE100", out="out2")
    assert code == 0 and report(out)["deck_support"]["verdicts"] == {"box_flow": "FAIL", "target": "PASS"}


def test_back_pressure_needs_an_extended_network(tmp_path, capsys):
    case(tmp_path)
    data = tmp_path / "case/model/CASE.DATA"
    data.write_text(data.read_text().replace("NETWORK\n 3 2 /\n", ""))
    path = tmp_path / "bp.yml"
    ops = [OPS[-1][0]]
    path.write_text(
        yaml_dump(
            {
                "schema_version": 1,
                "scenario_id": "bp",
                "case_root": "case",
                "deck": "model/CASE.DATA",
                "operations": ops,
            }
        )
    )
    code = sb.main(["--spec", str(path), "--out", str(tmp_path / "o"), "--target", "ECLIPSE100"])
    assert code == 2 and "needs NETWORK in RUNSPEC" in capsys.readouterr().err


def test_a_new_well_missing_from_summary_lists_is_reported():
    deck = "SUMMARY\nFOPT\nWOPR\n 'P1' 'I*' /\nWBHP\n/\nWWCT\n P1 /\nSCHEDULE\n"
    assert (
        sb.summary_gaps(deck, {"P2", "I2"})
        == [
            "SUMMARY lists leave out new well P2: no WOPR, WWCT for it. Add it to those lists to see its vectors.",
            "SUMMARY lists leave out new well I2: no WWCT for it. Add it to those lists to see its vectors.",
        ][::-1]
    )


def test_output_folder_must_be_new(tmp_path, capsys):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/x").write_text("x")
    code, _ = build(tmp_path, [INFILL])
    assert code == 2 and "not empty" in capsys.readouterr().err


def scenario_root(tmp_path: Path) -> tuple[Path, dict]:
    need("opm", "yaml", "numpy", "pandas")
    proj, env = write_contracts(tmp_path)
    case(tmp_path)
    base = tmp_path / "scenarios/baseline"
    run(FP, "--data", tmp_path / "case/model/CASE.DATA", "--out", base / "deck_fingerprint.json")
    write_profiles(base / "deliverables/profiles", n=5)
    (base / "runmeta.yml").write_text(
        yaml_dump({"scenario_id": "baseline", "changes": {}, "changed_keywords": []})
    )
    return proj, env


def build_and_qc(
    tmp_path: Path,
    proj: Path,
    env: dict,
    sid: str,
    ops: list[dict],
    tamper: bool = False,
    variables: dict | None = None,
    controls: Path | None = None,
) -> dict:
    out = tmp_path / "scenarios" / sid
    path = tmp_path / f"{sid}.yml"
    data = {
        "schema_version": 1,
        "scenario_id": sid,
        "case_root": "case",
        "deck": "model/CASE.DATA",
        "operations": ops,
        **({"variables": variables} if variables else {}),
    }
    path.write_text(yaml_dump(data))
    extra = ["--controls", str(controls)] if controls else []
    assert sb.main(["--spec", str(path), "--out", str(out), "--target", "ECLIPSE100", *extra]) == 0
    if tamper:
        sched = out / "case/model/CASE_SCHEDULE.inc"
        sched.write_text(sched.read_text().replace("'RATE' 500 1* 300", "'RATE' 900 1* 300"))
        run(FP, "--data", out / "case/model/CASE.DATA", "--out", out / "deck_fingerprint.json")
    write_profiles(out / "deliverables/profiles", n=5, p1_factor=0.6)
    proc = run(QC, "--contract", proj, "--scenario", sid, env=env)
    result = json.loads((out / "deliverables/scenario_qc/scenario_qc_report.json").read_text())
    return {"code": proc.returncode, **result}


LIMIT_P1 = {
    "type": "target",
    "date": "2020-02-01",
    "level": "well_limit",
    "name": "P1",
    "limits": {"ORAT": 60},
}


def test_scenario_qc_passes_the_built_declaration(tmp_path):
    proj, env = scenario_root(tmp_path)
    result = build_and_qc(tmp_path, proj, env, "cut_p1", [LIMIT_P1])
    assert result["code"] == 0 and result["status"] == "PASS", result["flags"]
    assert "expected_jump" in result["info_flags"]


def test_scenario_qc_fails_an_edit_outside_the_declaration(tmp_path):
    proj, env = scenario_root(tmp_path)
    result = build_and_qc(tmp_path, proj, env, "cut_p1_tampered", [LIMIT_P1], tamper=True)
    assert result["code"] == 2 and "fingerprint_mismatch" in result["flags"]
    assert any(
        "['WCONINJE'], which expected_changes.keywords does not declare" in m for m in result["messages"]
    )


# ---- Everest controls -------------------------------------------------------------------------------------
EVEREST_VARIABLES = {
    "p1_orat": {"initial": 60, "min": 20, "max": 120},
    "g1_grat": {"initial": 150000, "min": 50000, "max": 250000},
    "p1_alq": {"initial": 40000, "min": 0, "max": 100000},
    "i1_wrat": {"initial": 500, "min": 200, "max": 900},
    "plat_bar": {"initial": 25, "min": 10, "max": 40},
    "p2_i": {"initial": 2, "min": 1, "max": 3, "integer": True},
    "p2_delay": {"initial": 0, "min": 0, "max": 40, "integer": True},
}
EVEREST_OPS = [
    {**LIMIT_P1, "limits": {"ORAT": "${p1_orat}"}},
    {"type": "target", "date": "2020-02-01", "level": "group_production", "name": "G1", "mode": "GRAT",
     "grat": "${g1_grat}", "action": "RATE"},
    {"type": "gas_lift", "date": "2020-02-01", "alq": [{"well": "P1", "value": "${p1_alq}"}]},
    {"type": "target", "date": "2020-02-01", "level": "well_limit", "name": "I1", "limits": {"WRAT": "${i1_wrat}"}},
    {"type": "back_pressure", "date": "2020-02-01", "nodes": [{"node": "PLAT", "pressure": "${plat_bar}"}],
     "branches": [{"downtree": "G1", "uptree": "PLAT", "vfp": 2}], "affected_wells": ["P1"]},
    {**INFILL, "i": "${p2_i}"},
    {"type": "shift", "ops": ["p2"], "days": "${p2_delay}"},
]  # fmt: skip


def everest_spec(tmp_path: Path, ops: list[dict], variables: dict) -> Path:
    path = spec(tmp_path, ops)
    data = sb.contract_lib._yaml().safe_load(path.read_text())
    path.write_text(yaml_dump({**data, "variables": variables}))
    return path


def test_variables_render_their_initial_values_without_a_control_file(tmp_path):
    code, out = build_with(
        tmp_path, everest_spec(tmp_path, [EVEREST_OPS[0]], {"p1_orat": EVEREST_VARIABLES["p1_orat"]})
    )
    assert code == 0 and " 'P1' 'ORAT' 60 /" in schedule_text(out)
    meta = sb.contract_lib._yaml().safe_load((out / "runmeta.yml").read_text())
    assert meta["built_by"]["controls"] == {"source": "initial", "values": {"p1_orat": 60}}


def test_everest_controls_section_comes_from_the_spec_variables(tmp_path):
    need("yaml")
    path = everest_spec(tmp_path, EVEREST_OPS, EVEREST_VARIABLES)
    section = ec.controls_section(sb.load_spec(path), name="scenario")
    (control,) = section["controls"]
    assert control["name"] == "scenario" and "type" not in control
    by_name = {v["name"]: v for v in control["variables"]}
    assert list(by_name) == list(EVEREST_VARIABLES)
    assert by_name["i1_wrat"] == {"name": "i1_wrat", "initial_guess": 500, "min": 200, "max": 900,
                                  "perturbation_magnitude": 35.0}  # fmt: skip
    assert by_name["p2_i"]["control_type"] == "integer"
    everest_lint = load_everest_lint()
    config = {**section, "objective_functions": [{"name": "npv"}], "model": {"realizations": [0]},
              "optimization": {"algorithm": "optpp_q_newton", "max_batch_num": 2}}  # fmt: skip
    assert not [f for f in everest_lint.lint(config) if f["severity"] == "error"]


def test_an_everest_control_set_builds_a_deck_that_passes_deck_support_and_scenario_qc(tmp_path):
    proj, env = scenario_root(tmp_path)
    # What Everest writes into the runpath for a control named "scenario": <name>.json
    values = {"p1_orat": 95.5, "g1_grat": 210000, "p1_alq": 75000, "i1_wrat": 800, "plat_bar": 18.25,
              "p2_i": 3.0, "p2_delay": 29.0}  # fmt: skip
    controls = tmp_path / "scenario.json"
    controls.write_text(json.dumps(values))
    result = build_and_qc(
        tmp_path, proj, env, "opt_point", EVEREST_OPS, variables=EVEREST_VARIABLES, controls=controls
    )
    assert result["code"] == 0 and result["status"] == "PASS", (result["flags"], result["messages"])
    out = tmp_path / "scenarios/opt_point"
    rep = report(out)
    assert (
        rep["deck_support"]["verdicts"]["target"] == "PASS" and rep["declaration_check"]["status"] == "PASS"
    )
    text = schedule_text(out)
    for line in (" 'P1' 'ORAT' 95.5 /", " 'G1' 'GRAT' 2* 210000 1* 'RATE' /", " 'P1' 'LIFT' 75000 /",
                 " 'I1' 'WRAT' 800 /", " 'PLAT' 18.25 /", " 'P2' 'G1' 3 3 1* 'OIL' /"):  # fmt: skip
        assert line in text, line
    assert [i["date"] for i in rep["inserts"] if i["keyword"] == "WELSPECS"] == ["2020-03-30"]
    assert rep["controls"] == {"source": str(controls), "values": {**values, "p2_i": 3, "p2_delay": 29}}


@pytest.mark.parametrize(
    "variables,ops,values,message",
    [
        ({}, [{**LIMIT_P1, "limits": {"ORAT": "${nope}"}}], None, "unknown variable 'nope'"),
        ({"a": {"initial": 60, "min": 20, "max": 120}, "b": {"initial": 1, "min": 0, "max": 2}},
         [{**LIMIT_P1, "limits": {"ORAT": "${a}"}}], None, "not used by any operation: ['b']"),
        ({"a": {"initial": 200, "min": 20, "max": 120}}, [{**LIMIT_P1, "limits": {"ORAT": "${a}"}}], None,
         "initial 200 is outside [20, 120]"),
        ({"a": {"initial": 60, "min": 20, "max": 120}}, [{**LIMIT_P1, "limits": {"ORAT": "${a}"}}], {"a": 130},
         "a = 130 is outside [20, 120]"),
        ({"a": {"initial": 60, "min": 20, "max": 120}}, [{**LIMIT_P1, "limits": {"ORAT": "${a}"}}], {"z": 1},
         "has no value for ['a']"),
        ({"a": {"initial": 60, "min": 20, "max": 120}}, [{**LIMIT_P1, "limits": {"ORAT": "x${a}"}}], None,
         "must be the whole value"),
    ],
)  # fmt: skip
def test_variable_errors_are_bad_input(tmp_path, capsys, variables, ops, values, message):
    need("opm", "yaml")
    path = everest_spec(tmp_path, ops, variables)
    extra = []
    if values is not None:
        (tmp_path / "c.json").write_text(json.dumps(values))
        extra = ["--controls", str(tmp_path / "c.json")]
    code = sb.main(["--spec", str(path), "--out", str(tmp_path / "o"), "--target", "ECLIPSE100", *extra])
    assert code == 2 and message in capsys.readouterr().err


def test_an_unquoted_variable_reference_is_a_yaml_error(tmp_path, capsys):
    path = tmp_path / "bad.yml"
    path.write_text("schema_version: 1\nscenario_id: x\noperations:\n  - {type: target, orat: ${a}}\n")
    assert sb.main(["--spec", str(path), "--out", str(tmp_path / "o"), "--target", "ECLIPSE100"]) == 2
    assert "quote variable references" in capsys.readouterr().err


def test_a_nested_control_file_is_read_by_control_name(tmp_path):
    path = everest_spec(tmp_path, [EVEREST_OPS[0]], {"p1_orat": EVEREST_VARIABLES["p1_orat"]})
    (tmp_path / "c.json").write_text(json.dumps({"scenario": {"p1_orat": 33}}))
    code, out = build_with(tmp_path, path, "--controls", str(tmp_path / "c.json"))
    assert code == 0 and " 'P1' 'ORAT' 33 /" in schedule_text(out)


def test_everest_controls_cli_writes_yaml(tmp_path):
    need("yaml")
    path = everest_spec(tmp_path, [EVEREST_OPS[0]], {"p1_orat": EVEREST_VARIABLES["p1_orat"]})
    assert ec.main(["--spec", str(path), "--out", str(tmp_path / "controls.yml")]) == 0
    data = sb.contract_lib._yaml().safe_load((tmp_path / "controls.yml").read_text())
    assert data["controls"][0]["variables"][0]["initial_guess"] == 60
