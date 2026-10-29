"""The lesson fixtures and the scripts must agree. This keeps the eval ground truth of the coded checks honest."""

import contextlib
import importlib.util
import io
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
DATA = ROOT / "tests/data/lessons"
EGG = ROOT / "tests/data/egg"


def load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prior_qc = load("skills/history-match-qc/scripts/prior_qc.py", "prior_qc_lessons")
field_qc = load("skills/history-match-qc/scripts/field_qc.py", "field_qc_lessons")
ulog = load("skills/history-match-qc/scripts/update_log_report.py", "update_log_lessons")
triage = load("skills/ert-diagnose/scripts/triage_runpath.py", "triage_lessons")
obs_qc = load("skills/history-match-qc/scripts/obs_qc.py", "obs_qc_lessons")


def run_json(main, *args: str) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main([*args, "--json"])
    return code, json.loads(buffer.getvalue())


def test_d1_parameters():
    code, report = run_json(
        prior_qc.main,
        str(DATA / "d1/params.dist"),
        "--posterior-csv",
        str(DATA / "d1/posterior.csv"),
        "--truth",
        str(DATA / "d1/truth.txt"),
    )
    assert code == 0
    by_code = {}
    for item in report["findings"]:
        by_code.setdefault(item.get("code"), []).append(item)
    assert by_code["HMQC-COLLAPSE"][0]["detail"].startswith("18 of 20 parameters (90%)")
    assert "floor of the ratio 0.60" in by_code["HMQC-COLLAPSE"][0]["detail"]
    assert [f["parameter"] for f in by_code["HMQC-OUTSIDE-PRIOR"]] == ["P00", "P01"]
    assert by_code["HMQC-TRUTH-Z"][0]["detail"].startswith("14 of 20 parameters")


def test_field():
    code, report = run_json(
        field_qc.main,
        "--prior",
        str(DATA / "field/prior/*.grdecl"),
        "--posterior",
        str(DATA / "field/posterior/*.grdecl"),
        "--actnum",
        str(DATA / "field/ACTNUM.grdecl"),
        "--log",
    )
    assert code == 0
    assert (report["active_cells"], report["collapsed_cells"], report["outside_cells"]) == (22, 18, 2)
    assert [f["code"] for f in report["findings"]] == ["HMQC-FIELD-COLLAPSE", "HMQC-FIELD-OUTSIDE"]


def test_update_log():
    code, report = run_json(ulog.main, str(DATA / "update_log"))
    assert code == 0
    assert [round(s["std_error_ratio"]["median"], 3) for s in report["steps"]] == [0.655, 0.279, 0.202, 0.157]
    assert [f["code"] for f in report["findings"]] == ["HMQC-EARLY-SPREAD-LOSS"]


@pytest.mark.parametrize(
    ("folder", "extra", "code", "reals"),
    [
        ("restart", [], "ERTD-NOT-IDEMPOTENT", [0, 1, 2]),
        ("done", ["--expect", "eclipse/model/*.UNSMRY"], "ERTD-DONE-WITHOUT-RESULT", [1, 2]),
    ],
)
def test_runpaths(folder, extra, code, reals):
    exit_code, report = run_json(
        triage.main, str(DATA / folder / "realization-*/iter-0"), "--expected", "3", *extra
    )
    assert exit_code == 1
    assert report["summary"]["findings_by_code"] == {code: reals}


def test_fixture_files_are_small():
    assert all(p.stat().st_size < 200_000 for p in DATA.rglob("*") if p.is_file())


def egg_medians(report: dict) -> list[float]:
    return [round(step["std_error_ratio"]["median"], 3) for step in report["steps"]]


def test_egg_update_log_early_spread_loss():
    code, report = run_json(ulog.main, str(EGG / "update_log"))
    assert code == 0
    assert [(s["parent"], s["target"]) for s in report["steps"]] == [
        ("iter-0", "iter-1"),
        ("iter-1", "iter-2"),
        ("iter-2", "iter-3"),
        ("iter-3", "iter-4"),
    ]
    assert egg_medians(report) == [0.482, 0.261, 0.26, 0.28]
    assert [s["counts"]["std_cutoff"] for s in report["steps"]] == [8, 16, 20, 21]
    assert all(s["notes"] == [] for s in report["steps"])  # the header counts agree with Report.csv
    assert report["es_mda"]["consistent"] and report["es_mda"]["global_scaling"] == [15.0, 7.5, 3.75, 1.875]
    assert [f["code"] for f in report["findings"]] == ["HMQC-EARLY-SPREAD-LOSS"]
    assert report["findings"][0]["ensemble"] == "update iter-1 -> iter-2"
    assert "Medians by step: 0.482 -> 0.261 -> 0.26 -> 0.28" in report["findings"][0]["detail"]


def test_egg_update_log_orders_uuid_folders_by_report_names(tmp_path):
    # ERT 23 names the step folders by UUID. These names sort as iteration 4, 3, 2, 1.
    names = {
        4: "0a1b2c3d-0000-4000-8000-000000000004",
        3: "5e6f7a8b-0000-4000-8000-000000000003",
        2: "9c0d1e2f-0000-4000-8000-000000000002",
        1: "f3a4b5c6-0000-4000-8000-000000000001",
    }
    for step, name in names.items():
        shutil.copytree(EGG / f"update_log/step_{step}", tmp_path / name)
    code, report = run_json(ulog.main, str(tmp_path))
    assert code == 0
    assert [s["target"] for s in report["steps"]] == ["iter-1", "iter-2", "iter-3", "iter-4"]
    assert [Path(s["folder"]).name for s in report["steps"]] == [names[i] for i in (1, 2, 3, 4)]
    assert egg_medians(report) == [0.482, 0.261, 0.26, 0.28]
    assert [f["code"] for f in report["findings"]] == ["HMQC-EARLY-SPREAD-LOSS"]


def test_egg_update_log_without_report_header_does_not_guess(tmp_path):
    # Report.csv alone has no Parent and Target. The script must not use the folder order for the update count.
    for step in (1, 2, 3, 4):
        (tmp_path / f"step_{step}").mkdir()
        shutil.copy(EGG / f"update_log/step_{step}/Report.csv", tmp_path / f"step_{step}")
    code, report = run_json(ulog.main, str(tmp_path))
    assert code == 0 and report["findings"] == []
    assert len(report["early_spread_unknown"]) == 4


def test_egg_fixture_is_small():
    total = sum(p.stat().st_size for p in EGG.rglob("*") if p.is_file())
    assert total < 200_000, total


def test_shut_wells():
    # G9: observations while the well does not flow. obs_qc did not flag them. The deck SCHEDULE now does.
    pytest.importorskip("res2df")
    code, report = run_json(
        obs_qc.main, str(DATA / "shut_wells/wells.obs"), "--deck", str(DATA / "shut_wells/CASE.DATA")
    )
    assert code == 0
    flagged = [p["message"] for p in report["problems"] if p.get("code") == "HMQC-OBS-NOFLOW"]
    assert flagged == [
        "WOPR_P1_0501: well P1 is SHUT at 2020-05-01 in the deck SCHEDULE."
        " The value carries no reservoir information",
        "WOPR_P1_0801: well P1 has no open connection at 2020-08-01 in the deck SCHEDULE."
        " The value carries no reservoir information",
        "WBHP_P2_0201: well P2 is not open yet (first schedule date 2020-03-01) at 2020-02-01 in the deck"
        " SCHEDULE. The pressure is a shut-in pressure, not a flowing pressure",
        "WWCT_P2_0301: well P2 is not open yet (first schedule date 2020-03-01) at 2020-03-01 in the deck"
        " SCHEDULE. A ratio of zero rates is noise",
        "WWIR_I1_0601: well I1 has a zero rate in WCONINJH at 2020-06-01 in the deck SCHEDULE."
        " The value carries no reservoir information",
        "WBHP_I1_0801: well I1 is SHUT at 2020-08-01 in the deck SCHEDULE."
        " The pressure is a shut-in pressure, not a flowing pressure",
        "WOPR_P9_0201: well P9 has no COMPDAT or WCON keyword at 2020-02-01 in the deck SCHEDULE."
        " The value carries no reservoir information",
    ]
    assert [p.split(":")[0] for p in report["proposals"] if "does not flow" in p] == ["I1", "P1", "P2", "P9"]
    code, report = run_json(obs_qc.main, str(DATA / "shut_wells/wells.obs"))
    assert code == 0 and not [p for p in report["problems"] if p.get("code") == "HMQC-OBS-NOFLOW"]
