"""Synthetic checks for Flow proposals, schedule, logs and history comparisons."""

import ast
import importlib.util
import json
import shlex
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["text", "opm"])
def parser(request):
    """schedule_qc --parser value. The OPM case needs res2df, so it skips without it (REQUIRE_FULL fails)."""
    if request.param == "opm":
        pytest.importorskip("res2df")
    return request.param


def test_phase_units():
    module = load("tools/simcase.py")
    assert module.detect("OIL\nWATER\nFIELD\n-- GAS\n") == {
        "phases": ["OIL", "WATER"],
        "unit_system": "FIELD",
    }
    assert module.default_vectors({"OIL", "WATER"}) == ["FOPT", "FWPT"]
    assert module.default_vectors({"GAS"}) == ["FGPT"]
    assert module.detect("DISGAS\nVAPOIL\nPVT-M\n")["unit_system"] == "PVT-M"
    assert module.detect("LAB\n")["unit_system"] == "LAB"
    assert module.detect("-- FIELD\n")["unit_system"] is None
    assert module.convert(1, "pressure", "METRIC", "FIELD") == pytest.approx(14.503773773)
    assert module.convert(6.28981077, "liquid_volume", "FIELD", "METRIC") == pytest.approx(1)
    with pytest.raises(ValueError):
        module.detect("METRIC\nFIELD\n")


def test_schedule(tmp_path, parser):
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    text = """DIMENS
2 2 2 /
WELSPECS
'A' 'G' 1 1 1 'OIL' /
/
WCONHIST
'B' OPEN ORAT -1 0 0 /
/
COMPDAT
'A' 3 1 1 4 OPEN /
/
DATES
2 JAN 2020 /
1 JAN 2020 /
/
"""
    deck = tmp_path / "test.DATA"
    deck.write_text(text)
    assert {r["code"] for r in module.check_schedule(text)} == {"W001", "W002", "W003", "W004"}
    assert module.main(["--deck", str(deck), "--parser", parser, "--json"]) == 1
    assert module.main(["--deck", str(tmp_path / "absent"), "--parser", parser]) == 2


def test_schedule_defaults_and_injectors():
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    text = """DIMENS
2 2 2 /
WELSPECS
'A' 'G' 1 1 1 'WATER' /
/
WCONINJE
'A' WATER OPEN RATE 1 /
/
WCONINJH
'A' WATER OPEN 1 /
/
WCONPROD
'A*' OPEN ORAT 1 /
/
WCONHIST
'A' OPEN ORAT 3* /
/
COMPDAT
'A' 0 0 1 2 OPEN /
/
DATES
1 JAN 2020 /
2 JAN 2020 /
/
"""
    assert module.check_schedule(text) == []
    assert module.check_schedule("COMPDAT\n'A' 1 1 1 1 /\n/")[1]["code"] == "W005"
    assert module.check_schedule("DIMENS\n1 2 /\nCOMPDAT\n'A' 1 1 1 1 /\n/")[0]["code"] == "W005"


def write_deck(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root / next(iter(files))


def schedule_json(module, capsys, *args):
    code = module.main([*args, "--json"])
    findings = json.loads(capsys.readouterr().out)["findings"]
    return code, [(r["code"], r["severity"], r["where"], r["message"]) for r in findings]


def test_schedule_follows_includes(tmp_path, capsys, parser):
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    deck = write_deck(
        tmp_path,
        {
            "model/CASE.DATA": "DIMENS\n2 2 2 /\nSCHEDULE\nINCLUDE\n  '../include/hist.sch' /\nINCLUDE\n  missing.sch /\n",
            "include/hist.sch": "WELSPECS\n'A' 'G' 1 1 1 'OIL' /\n/\nCOMPDAT\n'A' 3 1 1 1 OPEN /\n/\n"
            "INCLUDE 'more.sch' /\n",
            "include/more.sch": "DATES\n1 JLY 2020 /\n/\nINCLUDE\n'hist.sch' /\nWCONHIST\n'B' OPEN ORAT 1 /\n/\n",
        },
    )
    include_errors = [
        ("I002", "E", "more.sch:5", "include cycle: hist.sch"),
        ("I001", "E", "CASE.DATA:7", "include file not found: missing.sch"),
    ]
    after_include_errors = {
        "text": [
            ("W002", "E", "hist.sch:5 COMPDAT", "completion indices outside DIMENS or reversed K range"),
            ("W001", "E", "more.sch:7 WCONHIST", "well B has no preceding WELSPECS"),
        ],
        "opm": [("L001", "E", "CASE.DATA", "the OPM parser did not run because of the INCLUDE errors")],
    }
    assert schedule_json(module, capsys, "--deck", str(deck), "--parser", parser) == (
        1,
        [*include_errors, *after_include_errors[parser]],
    )


def test_schedule_without_well_keywords_warns(tmp_path, capsys, parser):
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    deck = write_deck(
        tmp_path, {"CASE.DATA": "SCHEDULE\nINCLUDE\n'empty.sch' /\n", "empty.sch": "TSTEP\n1 /\n"}
    )
    assert schedule_json(module, capsys, "--deck", str(deck), "--parser", parser) == (
        0,
        [("W006", "W", "SCHEDULE", "no well keywords found; nothing was checked")],
    )


def test_schedule_include_alone_and_dimens_option(tmp_path, capsys, parser):
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    sch = write_deck(
        tmp_path,
        {
            "hist.sch": "WELSPECS\n'A' 'G' 1 1 1 'OIL' /\n/\nCOMPDAT\n'A' 1 1 1 1 /\n'A' 1 1 2 2 /\n'A' 1 1 3 3 /\n/\n"
        },
    )
    unbounded, outside = {"text": ("hist.sch", "hist.sch:7 COMPDAT"), "opm": ("COMPDAT", "COMPDAT A")}[parser]
    assert schedule_json(module, capsys, "--deck", str(sch), "--parser", parser) == (
        0,
        [("W005", "W", unbounded, "DIMENS unavailable; 3 COMPDAT records not bounds-checked")],
    )
    assert schedule_json(
        module, capsys, "--deck", str(sch), "--dimens", "1", "1", "2", "--parser", parser
    ) == (
        1,
        [("W002", "E", outside, "completion indices outside DIMENS or reversed K range")],
    )


def test_schedule_restart_deck_wells(tmp_path, capsys, parser):
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    deck = write_deck(
        tmp_path,
        {"PRED.DATA": "RESTART\n'HIST' 10 /\nSCHEDULE\nWCONPROD\n'A1' OPEN LRAT /\n'A2' OPEN LRAT /\n/\n"},
    )
    assert schedule_json(module, capsys, "--deck", str(deck), "--parser", parser) == (
        0,
        [
            (
                "W001",
                "W",
                "RESTART",
                "wells A1, A2 have no WELSPECS in this deck; they can come from the restart file and were not checked",
            )
        ],
    )


def test_eval_fixture_and_cases(tmp_path):
    module = load("evals/runner.py")
    data = tmp_path / "data"
    data.mkdir()
    module.fixture_flow_text(tmp_path, data)
    assert (data / "synthetic.DATA").is_file()
    cases = tomllib.loads((ROOT / "evals/cases.toml").read_text())["case"]
    identifiers = [case["id"] for case in cases]
    assert len(identifiers) == len(set(identifiers))
    for case in cases:
        assert all(name in module.FIXTURES for name in case.get("setup", []))


def contract_file(tmp_path):
    path = tmp_path / "site.contract.yml"
    path.write_text(
        f'simulator_version: "test"\nqueue_system: LOCAL\nscratch_root: "{tmp_path.as_posix()}"\noutput_root: "{tmp_path.as_posix()}"\n'
    )
    return path


def test_preflight(tmp_path, capsys):
    module = load("skills/flow-preflight/scripts/flow_preflight.py")
    deck = tmp_path / "A.DATA"
    deck.write_text("OIL\nWATER\nMETRIC\n")
    contract = contract_file(tmp_path)
    out = tmp_path / "results"
    argv = ["--contract", str(contract), "--deck", str(deck), "--output-dir", str(out)]
    assert module.main(argv) == 0
    output = capsys.readouterr().out
    assert output.splitlines()[0] == shlex.join(["flow", f"--output-dir={out}", str(deck)])
    assert "Nothing was executed." in output
    assert not out.exists()
    with contract.open("a") as stream:
        stream.write('flow_cmd: "wrapper flow"\nflow_extra_args: "--enable-debug-output=true"\n')
    assert module.main(argv + ["--parsing-strictness", "high"]) == 0
    assert "wrapper flow" in capsys.readouterr().out
    contract.write_text('output_root: ""\n')
    assert module.main(argv) == 2
    assert "missing literal contract value" in capsys.readouterr().err


def test_preflight_rejects_outputs_and_subprocess(tmp_path):
    module = load("skills/flow-preflight/scripts/flow_preflight.py")
    deck = tmp_path / "A.DATA"
    deck.write_text("OIL\n")
    contract = contract_file(tmp_path)
    args = ["--contract", str(contract), "--deck", str(deck)]
    assert module.main(args + ["--output-dir", str(tmp_path.parent)]) == 2
    out = tmp_path / "used"
    out.mkdir()
    (out / "old").write_text("existing")
    assert module.main(args + ["--output-dir", str(out)]) == 2
    tree = ast.parse((ROOT / "skills/flow-preflight/scripts/flow_preflight.py").read_text())
    assert not any(
        isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names)
        for node in ast.walk(tree)
    )


def test_log(tmp_path):
    module = load("skills/flow-preflight/scripts/flow_log_diagnose.py")
    prt = tmp_path / "A.PRT"
    prt.write_text(
        "Report step 2, date: 2020-01-02\nTimestep chopped\nProblem: convergence\nNewton iterations: 3\nLinear iterations: 12\nError: stopped\n"
    )
    result = module.diagnose(prt.read_text())
    assert result["last_date"] == "2020-01-02"
    assert len(result["chops"]) == 2
    assert result["newton_iterations"] == [3]
    assert result["linear_iterations"] == [12]
    assert result["errors"] == [{"line": 6, "text": "Error: stopped"}]
    out = tmp_path / "report"
    assert module.main(["--prt", str(prt), "--outdir", str(out)]) == 0
    assert json.loads((out / "report.json").read_text())["last_report_step"] == 2
    assert "Line 2" in (out / "report.md").read_text()
    assert module.diagnose("no markers")["last_date"] is None


def test_log_warning_and_error_summary(tmp_path):
    module = load("skills/flow-preflight/scripts/flow_log_diagnose.py")
    prt = tmp_path / "CLEAN.PRT"
    prt.write_text(
        "Warning: Error in keyword RPTSOL, unrecognized mnemonic THPRES\n\nError summary:\nWarnings          381\n"
        "Info              858\nErrors            0\nBugs              0\nDebug             0\nProblems          0\n",
        encoding="utf-8",
    )
    out = tmp_path / "report"
    assert module.main(["--prt", str(prt), "--outdir", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["errors"] == []
    assert report["error_count"] == 0
    assert report["error_summary"] == {"Warnings": 381, "Errors": 0, "Bugs": 0, "Problems": 0}
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "Chop/problem messages: 0; errors: 0" in text
    assert "Error summary: Warnings 381, Errors 0, Bugs 0, Problems 0" in text
    failed = module.diagnose("Error: stopped\nError summary:\nWarnings 2\nErrors 1\nBugs 1\n")
    assert failed["errors"] == [{"line": 1, "text": "Error: stopped"}]
    assert failed["error_count"] == 2


def test_log_wells_shut_and_control_modes(tmp_path):
    module = load("skills/flow-preflight/scripts/flow_log_diagnose.py")
    prt = tmp_path / "A.PRT"
    prt.write_text(
        "Report step 82/100 at day 912/2557, date = 01-Jul-2020\n"
        "Starting time step 0, stepsize 3 days, at day 912/1004, date = 01-Jul-2020\n"
        "    Well A3 control mode changed from LRAT to BHP\n"
        "Well A3 will be shut as it can not operate under current reservoir conditions.\n"
        "Starting time step 1, stepsize 1.2 days, at day 915/1004, date = 04-Jul-2020\n"
        "Well OP5 will be shut as it can not operate under current reservoir conditions.\n"
        "    Well A3 control mode changed from LRAT to BHP\n"
        "    Well A1 control mode changed from ORAT to GRAT\n"
    )
    result = module.diagnose(prt.read_text())
    assert result["wells_shut_by_simulator"] == [
        {
            "line": 4,
            "well": "A3",
            "report_step": 82,
            "report_day": 912.0,
            "report_date": "01-Jul-2020",
            "time_step": 0,
            "time_step_day": 912.0,
            "time_step_date": "01-Jul-2020",
        },
        {
            "line": 6,
            "well": "OP5",
            "report_step": 82,
            "report_day": 912.0,
            "report_date": "01-Jul-2020",
            "time_step": 1,
            "time_step_day": 915.0,
            "time_step_date": "04-Jul-2020",
        },
    ]
    assert result["well_control_mode_changes"] == [
        {"well": "A3", "transition": "LRAT->BHP", "count": 2, "first_line": 3, "first_date": "01-Jul-2020"},
        {"well": "A1", "transition": "ORAT->GRAT", "count": 1, "first_line": 8, "first_date": "04-Jul-2020"},
    ]
    assert result["errors"] == []
    assert result["chops"] == []
    out = tmp_path / "report"
    assert module.main(["--prt", str(prt), "--outdir", str(out)]) == 0
    md = (out / "report.md").read_text()
    assert "Wells shut by the simulator: 2" in md
    assert (
        "- Line 6, well OP5, report step 82, date 01-Jul-2020 (day 912.0), time step 1, date 04-Jul-2020 (day 915.0)"
        in md
    )
    assert "- A3 LRAT->BHP: 2 (first line 3, date 01-Jul-2020)" in md
    assert "the objective discontinuous" in md
    empty = module.diagnose("Report step 1/2 at day 0/10, date = 01-Jan-2020\n")
    assert empty["wells_shut_by_simulator"] == []
    assert empty["well_control_mode_changes"] == []
    assert "discontinuous" not in module.markdown(empty)


def test_history(tmp_path):
    module = load("skills/history-match-qc/scripts/history_compare.py")
    csv = tmp_path / "h.csv"
    csv.write_text("DATE,WOPR:A,WOPRH:A\n2020-01-01,10,10\n2020-01-02,,10\n2020-01-03,20,10\n")
    row = module.compare(csv, 0.1)["vectors"][0]
    assert row["samples"] == 2
    assert row["first_divergence_date"] == "2020-01-03"
    assert row["normalised_rms"] == pytest.approx(0.5**0.5)
    assert row["points"][1]["simulated"] is None
    out = tmp_path / "report"
    assert module.main(["--csv", str(csv), "--tolerance", "0.1", "--outdir", str(out)]) == 0
    assert "NaN" not in (out / "report.json").read_text()


def test_history_missing_zero_and_bad_dates(tmp_path):
    module = load("skills/history-match-qc/scripts/history_compare.py")
    csv = tmp_path / "h.csv"
    csv.write_text("DATE,FGPT,FGPTH,FWPT\n2020-01-01,NaN,0,2\n2020-01-02,1,0,3\n")
    result = module.compare(csv, 0.1)
    assert result["vectors"][0]["normalised_rms"] is None
    assert result["vectors"][0]["first_divergence_date"] == "2020-01-02"
    assert result["unpaired"] == ["FWPT"]
    csv.write_text("DATE,FGPT,FGPTH\n2020-01-01,,\n")
    assert module.compare(csv, 0.1)["vectors"][0]["samples"] == 0
    csv.write_text("DATE,FGPT,FGPTH\n2020-01-02,1,1\n2020-01-01,2,2\n")
    with pytest.raises(ValueError, match="strictly increasing"):
        module.compare(csv, 0.1)


def test_log_shut_wells_with_warnings_and_error_summary(tmp_path):
    """A clean Drogon-like log: shut wells and Warning lines are findings, the error count stays 0."""
    module = load("skills/flow-preflight/scripts/flow_log_diagnose.py")
    prt = tmp_path / "PRED.PRT"
    prt.write_text(
        "Warning: Error in keyword RPTSOL, unrecognized mnemonic THPRES\n"
        "Report step 82/100 at day 912/2557, date = 01-Jul-2020\n"
        "Starting time step 0, stepsize 3 days, at day 912/1004, date = 01-Jul-2020\n"
        "Well OP5 will be shut as it can not operate under current reservoir conditions.\n"
        "    Well A3 control mode changed from RESV to BHP\n"
        "\nError summary:\nWarnings          381\nInfo              857\nErrors            0\nBugs              0\n",
        encoding="utf-8",
    )
    out = tmp_path / "report"
    assert module.main(["--prt", str(prt), "--outdir", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["error_count"] == 0 and report["errors"] == []
    assert [r["well"] for r in report["wells_shut_by_simulator"]] == ["OP5"]
    assert report["well_control_mode_changes"][0]["transition"] == "RESV->BHP"
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "Chop/problem messages: 0; errors: 0" in text
    assert "Wells shut by the simulator: 1" in text


SCHEDULE_TEXT = """WELSPECS
'A' 'G' 1 1 1* 'OIL' /
/
COMPDAT
'A' 3 1 1 1 OPEN /
'A' 1 1 1 2 OPEN /
'A' 1 1 1 1 OPEN /
/
WCONHIST
'B' OPEN ORAT -1 0 0 /
/
DATES
2 JAN 2020 /
1 JAN 2020 /
/
"""
RUNSPEC_TEXT = "RUNSPEC\nDIMENS\n2 2 1 /\nOIL\nSTART\n1 JAN 2020 /\nSCHEDULE\n"
SCHEDULE_FINDINGS = [
    ("W002", "E", "COMPDAT A", "completion indices outside DIMENS or reversed K range"),
    ("W002", "E", "COMPDAT A", "completion indices outside DIMENS or reversed K range"),
    ("W001", "E", "WCONHIST B", "well B has no preceding WELSPECS"),
    ("W003", "E", "WCONHIST B", "negative history rate for B"),
    ("W004", "E", "DATES 2020-01-01", "DATES is not strictly increasing"),
]


def test_schedule_in_include_and_inline_give_the_same_opm_findings(tmp_path, capsys):
    # G1: a SCHEDULE that is all in an INCLUDE gave a false pass. G2: a schedule include without DIMENS
    # gave one W005 for each COMPDAT. The OPM parser reads the include and DIMENS of the deck as Flow does.
    pytest.importorskip("res2df")
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    inline = write_deck(tmp_path / "inline", {"CASE.DATA": RUNSPEC_TEXT + SCHEDULE_TEXT})
    included = write_deck(
        tmp_path / "included",
        {
            "model/CASE.DATA": RUNSPEC_TEXT + "INCLUDE\n'../include/hist.sch' /\n",
            "include/hist.sch": SCHEDULE_TEXT,
        },
    )
    assert schedule_json(module, capsys, "--deck", str(inline)) == (1, SCHEDULE_FINDINGS)
    assert schedule_json(module, capsys, "--deck", str(included)) == (1, SCHEDULE_FINDINGS)
    assert module.main(["--deck", str(included)]) == 1
    assert "Parser: OPM parser (res2df)" in capsys.readouterr().out


def test_schedule_opm_parse_error_is_an_error_not_a_fallback(tmp_path, capsys):
    pytest.importorskip("res2df")
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    deck = write_deck(tmp_path, {"BAD.DATA": "RUNSPEC\nDIMENS\n a b c /\n" + SCHEDULE_TEXT})
    code, findings = schedule_json(module, capsys, "--deck", str(deck))
    assert code == 1
    assert [row[:3] for row in findings] == [("L001", "E", "BAD.DATA")]
    assert findings[0][3].startswith("the OPM parser cannot read the deck: Problem with keyword DIMENS")


def test_schedule_opm_parser_without_res2df_is_a_usage_error(tmp_path, capsys, monkeypatch):
    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    deck = write_deck(tmp_path, {"CASE.DATA": RUNSPEC_TEXT + SCHEDULE_TEXT})
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)
    assert module.main(["--deck", str(deck)]) == 2
    assert "--parser text" in capsys.readouterr().err
    assert module.main(["--deck", str(deck), "--parser", "text", "--json"]) == 1


def test_well_timeline_from_wcon_welopen_and_compdat():
    pytest.importorskip("res2df")
    from datetime import date

    from res2df import ResdataFiles

    module = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py")
    deck = ResdataFiles(str(ROOT / "tests/data/lessons/shut_wells/CASE.DATA")).get_deck()
    timeline = module.well_timeline(deck)
    assert sorted(timeline) == ["I1", "P1", "P2"]
    assert [(str(when), state) for when, state in timeline["P1"]] == [
        ("2020-01-01", None),
        ("2020-04-01", "is SHUT"),
        ("2020-06-01", None),
        ("2020-07-01", "has no open connection"),
    ]
    assert [(str(when), state) for when, state in timeline["I1"]] == [
        ("2020-01-01", None),
        ("2020-05-01", "has a zero rate in WCONINJH"),
        ("2020-06-01", None),
        ("2020-07-01", "is SHUT"),
    ]
    assert (
        module.well_state(timeline, "P2", date(2020, 3, 1))
        == "is not open yet (first schedule date 2020-03-01)"
    )
    assert module.well_state(timeline, "P2", date(2020, 3, 2)) is None
    assert module.well_state(timeline, "P9", date(2020, 3, 2)) == "has no COMPDAT or WCON keyword"
