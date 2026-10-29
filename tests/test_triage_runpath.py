import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/ert-diagnose/scripts/triage_runpath.py"
spec = importlib.util.spec_from_file_location("triage_runpath", SCRIPT)
triage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(triage)


def make_real(
    case: Path,
    iens: int,
    *,
    ok: bool = False,
    error: str | None = None,
    steps: list[dict] | None = None,
    files: dict[str, str] | None = None,
    prt: str | None = None,
) -> Path:
    path = case / f"realization-{iens}" / "iter-0"
    path.mkdir(parents=True)
    if ok:
        (path / "OK").write_text("All jobs complete\n")
    if error is not None:
        (path / "ERROR").write_text(error)
    if steps is not None:
        (path / "status.json").write_text(json.dumps({"steps": steps}))
    for name, body in (files or {}).items():
        (path / name).write_text(body)
    if prt is not None:
        (path / "eclipse/model").mkdir(parents=True)
        (path / "eclipse/model/CASE.PRT").write_text(prt)
    return path


def step(name: str, status: str = "Success", error: str | None = None, idx: int = 0) -> dict:
    return {
        "name": name,
        "status": status,
        "error": error,
        "stdout": f"{name}.stdout.{idx}",
        "stderr": f"{name}.stderr.{idx}",
    }


@pytest.fixture
def case(tmp_path: Path) -> Path:
    return tmp_path / "case"


def free_space(monkeypatch, free_bytes: float) -> list[str]:
    """Replace shutil.disk_usage in the script. Return the paths that it was asked for."""
    asked: list[str] = []

    def disk_usage(path):
        asked.append(str(path))
        return SimpleNamespace(total=2 * free_bytes, used=free_bytes, free=free_bytes)

    monkeypatch.setattr(triage.shutil, "disk_usage", disk_usage)
    return asked


@pytest.fixture(autouse=True)
def plenty_of_space(monkeypatch):
    """The free space of the test machine must not change the results."""
    free_space(monkeypatch, 500e9)


def run(case: Path, **kwargs) -> dict:
    rows = [
        triage.triage_one(p)
        for p in [
            member.runpath
            for member in triage.runpath_lib.read_ensemble(
                str(case / "realization-*/iter-0"), need_data_file=False
            ).found
        ]
    ]
    return {row["realization"]: row for row in rows}


def test_ok_realization(case):
    make_real(case, 0, ok=True, steps=[step("FLOW")])
    assert run(case)[0]["state"] == "ok"


def test_failed_step_with_killed_signature(case):
    make_real(
        case,
        1,
        error="Job FLOW failed",
        steps=[step("COPY_FILE"), step("FLOW", "Failure", "Process killed by signal 9", idx=1)],
        files={"FLOW.stderr.1": "run.sh: line 3: 4242 Killed   flow CASE.DATA\n"},
    )
    row = run(case)[1]
    assert row["state"] == "failed"
    assert row["failed_step"] == "FLOW"
    assert row["failure_class"] == "killed"


def test_missing_include_is_missing_file(case):
    make_real(
        case,
        2,
        steps=[step("FLOW", "Failure", idx=3)],
        files={"FLOW.stderr.3": "Error: cannot open include file ../include/grid/x.grdecl\n"},
    )
    assert run(case)[2]["failure_class"] == "missing_file"


def test_license_failure(case):
    make_real(
        case,
        3,
        steps=[step("ECLIPSE100", "Failure")],
        files={"ECLIPSE100.stderr.0": "FLEXlm error: no feature ECLIPSE available\n"},
    )
    assert run(case)[3]["failure_class"] == "license"


def test_prt_error_makes_realization_failed_even_with_ok_file(case):
    make_real(
        case,
        4,
        ok=True,
        steps=[step("FLOW")],
        prt="line one\n@--ERROR  at time 100.0 days\nline three\n",
    )
    row = run(case)[4]
    assert row["state"] == "failed"
    assert row["failure_class"] == "simulator_input"


def test_incomplete_has_no_guess(case):
    make_real(case, 5, steps=[step("FLOW", "Running")])
    row = run(case)[5]
    # A running step is not a failure. The script reports it as incomplete and names no cause.
    assert row["state"] == "incomplete"
    assert row["failure_class"] is None


def test_no_evidence_is_incomplete(case):
    make_real(case, 6)
    row = run(case)[6]
    assert row["state"] == "incomplete"
    assert row["failure_class"] is None


def test_word_oom_does_not_match_room(case):
    make_real(
        case,
        7,
        steps=[step("FLOW", "Failure")],
        files={"FLOW.stderr.0": "no room left in the zoom buffer\n"},
    )
    assert run(case)[7]["failure_class"] != "out_of_memory"


def test_cli_exit_codes_and_missing(case, capsys):
    make_real(case, 0, ok=True, steps=[step("FLOW")])
    make_real(case, 2, ok=True, steps=[step("FLOW")])
    code = triage.main([str(case), "--iter", "0", "--expected", "3", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["missing_realizations"] == [1]
    assert code == 1


def test_cli_all_ok_returns_zero(case):
    make_real(case, 0, ok=True, steps=[step("FLOW")])
    assert triage.main([str(case), "--iter", "0", "--expected", "1"]) == 0


def test_cli_no_match_fails_closed(tmp_path, capsys):
    assert triage.main([str(tmp_path / "nothing")]) == 2
    assert "no realization folders" in capsys.readouterr().err


def test_script_never_writes_in_runpath(case):
    path = make_real(case, 0, error="x", steps=[step("FLOW", "Failure")])
    before = sorted(p.name for p in path.rglob("*"))
    triage.main([str(case), "--iter", "0"])
    assert sorted(p.name for p in path.rglob("*")) == before


OPM_CHOP = "Problem: Solver convergence failure - Iteration limit reached\nTimestep chopped to 6.4 days\n"


def test_opm_chops_in_finished_run_are_not_a_failure(case):
    make_real(
        case, 8, ok=True, steps=[step("FLOW")], prt="Starting\n" + OPM_CHOP * 3 + "Problems          3\n"
    )
    row = run(case)[8]
    assert row["state"] == "ok"
    assert row["failure_class"] is None
    assert row["simulator_problems"] == 3


def test_eclipse_problem_lines_are_not_a_failure(case):
    make_real(case, 9, ok=True, steps=[step("ECLIPSE100")], prt="@--PROBLEM  AT TIME 10.0 DAYS\n")
    assert run(case)[9]["state"] == "ok"


def test_problem_lines_are_evidence_for_a_failed_run(case):
    make_real(case, 10, error="Step FLOW failed", steps=[step("FLOW", "Failure")], prt=OPM_CHOP * 2)
    row = run(case)[10]
    assert row["state"] == "failed"
    assert row["failure_class"] == "convergence"
    assert "PRT" in row["evidence"]


def test_ok_runs_with_problems_listed_in_summary(case, capsys):
    make_real(case, 0, ok=True, steps=[step("FLOW")], prt=OPM_CHOP)
    make_real(case, 1, ok=True, steps=[step("FLOW")])
    code = triage.main([str(case), "--iter", "0", "--expected", "2", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["summary"]["ok_with_simulator_problems"] == [0]


def test_realization_spec_ignores_extra_folders(case, capsys):
    for iens in (0, 1, 3, 7):
        make_real(case, iens, ok=True, steps=[step("FLOW")])
    make_real(case, 9, error="old run", steps=[step("FLOW", "Failure")])
    code = triage.main([str(case), "--iter", "0", "--realizations", "0-1,3,7", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["summary"]["missing_realizations"] == []
    assert payload["summary"]["extra_realizations"] == [9]
    assert {row["realization"] for row in payload["realizations"]} == {0, 1, 3, 7}


def test_realization_spec_reports_missing(case, capsys):
    make_real(case, 0, ok=True, steps=[step("FLOW")])
    code = triage.main([str(case), "--iter", "0", "--realizations", "0,4", "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["summary"]["missing_realizations"] == [4]


def test_bad_realization_spec_is_usage_error(case, capsys):
    make_real(case, 0, ok=True, steps=[step("FLOW")])
    assert triage.main([str(case), "--iter", "0", "--realizations", "3-1"]) == 2
    assert "ends before it starts" in capsys.readouterr().err


def test_expected_and_realizations_exclude_each_other(case):
    make_real(case, 0, ok=True, steps=[step("FLOW")])
    with pytest.raises(SystemExit):
        triage.main([str(case), "--iter", "0", "--expected", "1", "--realizations", "0"])


FILE_EXISTS_TRACE = (
    "Traceback (most recent call last):\n"
    '  File "copy_links.py", line 12, in <module>\n'
    "    os.symlink(source, target)\n"
    "FileExistsError: [Errno 17] File exists: 'eclipse/include/grid.grdecl'\n"
)


def test_file_exists_after_restart_is_not_idempotent(case, capsys):
    for iens in (0, 1):
        make_real(
            case,
            iens,
            steps=[step("LINK_INPUT", "Failure", "exit code 1")],
            files={"LINK_INPUT.stderr.0": FILE_EXISTS_TRACE},
        )
    make_real(case, 2, error="ln: failed to create symbolic link 'model': File exists\n")
    rows = run(case)
    assert {n: (r["state"], r["failure_class"]) for n, r in rows.items()} == {
        0: ("failed", "file_exists"),
        1: ("failed", "file_exists"),
        2: ("failed", "file_exists"),
    }
    assert rows[0]["findings"] == [
        {
            "code": "ERTD-NOT-IDEMPOTENT",
            "detail": "LINK_INPUT.stderr.0: FileExistsError: [Errno 17] File exists: 'eclipse/include/grid.grdecl'",
        }
    ]
    assert triage.main([str(case), "--iter", "0"]) == 1
    text = capsys.readouterr().out
    assert "FINDING ERTD-NOT-IDEMPOTENT: 3 of 3 failed realizations" in text
    assert "(threshold: 1): [0, 1, 2]" in text and "Limiter" in text


def test_other_failures_and_ok_runs_are_not_flagged_as_not_idempotent(case):
    make_real(case, 0, steps=[step("FLOW", "Failure", "killed")], files={"FLOW.stderr.0": "Killed\n"})
    make_real(case, 1, ok=True, files={"LINK.stderr.0": "File exists, kept\n"})
    rows = run(case)
    assert rows[0]["findings"] == [] and rows[1]["findings"] == []
    assert rows[1]["state"] == "ok"


def test_ok_file_without_expected_result_is_failed(case, capsys):
    real = make_real(case, 0, ok=True)
    (real / "eclipse/model").mkdir(parents=True)
    (real / "eclipse/model/CASE-0.UNSMRY").write_text("x")
    make_real(case, 1, ok=True)
    make_real(case, 2, steps=[step("FLOW")])  # status.json says success, no OK file
    rows = {
        r["realization"]: r
        for r in (
            triage.triage_one(p, ["eclipse/model/*.UNSMRY"])
            for p in [
                member.runpath
                for member in triage.runpath_lib.read_ensemble(
                    str(case / "realization-*/iter-0"), need_data_file=False
                ).found
            ]
        )
    }
    assert rows[0]["state"] == "ok" and rows[0]["findings"] == []
    assert (rows[1]["state"], rows[1]["failure_class"]) == ("failed", "missing_result")
    assert rows[1]["findings"] == [
        {
            "code": "ERTD-DONE-WITHOUT-RESULT",
            "detail": "OK says success, but no file matches: eclipse/model/*.UNSMRY",
        }
    ]
    assert rows[2]["findings"][0]["detail"].startswith("status.json says success")
    assert triage.main([str(case), "--iter", "0", "--expect", "eclipse/model/*.UNSMRY"]) == 1
    text = capsys.readouterr().out
    assert "FINDING ERTD-DONE-WITHOUT-RESULT: 2 realizations say success" in text and "[1, 2]" in text


def test_without_expect_an_ok_realization_stays_ok(case):
    make_real(case, 0, ok=True)
    make_real(case, 1, steps=[step("FLOW", "Running")])
    rows = run(case)
    assert rows[0]["state"] == "ok" and rows[0]["findings"] == []
    assert rows[1]["state"] == "incomplete"
    assert triage.main([str(case), "--iter", "0", "--realizations", "0", "--expect", "OK"]) == 0


def test_low_disk_is_a_warning_with_the_free_space_and_the_threshold(case, capsys, monkeypatch):
    make_real(case, 0, ok=True)
    asked = free_space(monkeypatch, 2.5e9)
    assert triage.main([str(case), "--iter", "0", "--json"]) == 0
    disk = json.loads(capsys.readouterr().out)["summary"]["disk"]
    assert disk == {
        "path": str(case / "realization-0" / "iter-0"),
        "free_gb": 2.5,
        "min_free_gb": 5.0,
        "code": "ERTD-LOW-DISK",
    }
    assert asked == [disk["path"]]
    assert triage.main([str(case), "--iter", "0"]) == 0
    text = capsys.readouterr().out
    assert "FINDING ERTD-LOW-DISK (warning): 2.5 GB free on the file system of" in text
    assert "(threshold: --min-free-gb 5)" in text and "Limiter" in text


def test_low_disk_follows_min_free_gb(case, capsys, monkeypatch):
    make_real(case, 0, ok=True)
    free_space(monkeypatch, 2.5e9)
    assert triage.main([str(case), "--iter", "0", "--min-free-gb", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["disk"]["code"] is None
    assert triage.main([str(case), "--iter", "0", "--min-free-gb", "-1"]) == 2


def test_unknown_free_space_is_said_not_hidden(case, capsys, monkeypatch):
    make_real(case, 0, ok=True)

    def broken(path):
        raise PermissionError("denied")

    monkeypatch.setattr(triage.shutil, "disk_usage", broken)
    assert triage.main([str(case), "--iter", "0"]) == 0
    assert "Free disk space of" in capsys.readouterr().out


NO_SPACE_TRACE = (
    "Traceback (most recent call last):\n"
    '  File "copy.py", line 3, in <module>\n'
    "OSError: [Errno 28] No space left on device: 'eclipse/model/CASE.UNRST'\n"
)


def test_no_space_in_a_step_log_or_error_file(case, capsys):
    make_real(
        case,
        0,
        steps=[step("FLOW", "Failure", "exit code 1")],
        files={"FLOW.stderr.0": NO_SPACE_TRACE},
    )
    make_real(case, 1, error="cp: error writing 'model.EGRID': No space left on device\n")
    make_real(case, 2, ok=True)
    rows = run(case)
    assert {n: (r["state"], r["failure_class"]) for n, r in rows.items()} == {
        0: ("failed", "storage"),
        1: ("failed", "storage"),
        2: ("ok", None),
    }
    assert rows[0]["findings"] == [
        {
            "code": "ERTD-NO-SPACE",
            "detail": "FLOW.stderr.0: OSError: [Errno 28] No space left on device: 'eclipse/model/CASE.UNRST'",
        }
    ]
    assert triage.main([str(case), "--iter", "0", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["findings_by_code"] == {"ERTD-NO-SPACE": [0, 1]}
    triage.main([str(case), "--iter", "0"])
    assert "FINDING ERTD-NO-SPACE: 2 realizations have 'No space left on device'" in capsys.readouterr().out


def test_no_space_in_an_ok_realization_is_still_a_finding(case, capsys):
    make_real(case, 0, ok=True, files={"COPY.stdout.0": "OSError: [Errno 28] while writing a log\n"})
    assert run(case)[0]["findings"][0]["code"] == "ERTD-NO-SPACE"
    assert triage.main([str(case), "--iter", "0"]) == 1


def test_other_failures_are_not_no_space(case):
    make_real(case, 0, error="Killed by signal 9\n")
    make_real(case, 1, ok=True, files={"FLOW.stdout.0": "Errno 280 is not 28\n"})
    assert all(not row["findings"] for row in run(case).values())
