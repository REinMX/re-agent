"""Eval status must not be green when the harness/judge never completed."""

import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("eval_integrity", ROOT / "evals/runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
spec = importlib.util.spec_from_file_location("eval_contract_doctor", ROOT / "tools/doctor.py")
assert spec is not None and spec.loader is not None
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


@pytest.fixture
def contract_workspace(tmp_path):
    if sys.platform == "win32":
        pytest.skip("Native eval fixture contracts use Linux execution paths, not Windows drive paths")
    (tmp_path / "repo").mkdir()
    data = tmp_path / "data"
    data.mkdir()
    return tmp_path, data


@pytest.mark.parametrize("use_override", [False, True])
def test_normal_eval_contract_satisfies_agent_preflight(contract_workspace, monkeypatch, use_override):
    work, data = contract_workspace
    monkeypatch.delenv("EVAL_PYTHON", raising=False)
    if use_override:
        monkeypatch.setenv("EVAL_PYTHON", str(work / "synthetic-env/bin/python"))
    runner.fixture_contract(work, data)
    contract = doctor.read_contract(work / "repo/site.contract.yml")
    assert doctor.check_contract(contract, "agent") == []


@pytest.mark.parametrize(
    "name",
    ['python"quoted"\\bin', 'python # hash "quote"\\', "python\\new\\tab"],
)
def test_eval_contract_round_trips_literal_python_path(contract_workspace, monkeypatch, name):
    work, data = contract_workspace
    python = str(work / name)
    monkeypatch.setenv("EVAL_PYTHON", python)
    runner.fixture_contract(work, data)
    contract = doctor.read_contract(work / "repo/site.contract.yml")
    assert contract["python"] == python
    assert doctor.check_contract(contract, "agent") == []


def test_eval_contract_is_explicitly_synthetic_and_manual(contract_workspace):
    work, data = contract_workspace
    runner.fixture_contract(work, data)
    path = work / "repo/site.contract.yml"
    contract = doctor.read_contract(path)
    assert "synthetic fixture" in path.read_text().lower()
    assert contract.get("agent_host") == "linux"
    assert contract.get("execution_mode") == "manual"
    assert contract.get("model_endpoint") == "synthetic-eval-only://no-network"
    assert contract.get("endpoint_approved") is True
    assert "synthetic" in contract["komodo_release"]
    assert "synthetic" in contract["simulator_version"]
    assert contract["scratch_root"] == str(data)
    assert contract["output_root"] == str(work / "out")


@pytest.mark.parametrize("case_id", ["re-1-no-contract-run", "re-5-no-contract-read-only"])
def test_missing_contract_cases_remain_missing(tmp_path, case_id):
    cases = tomllib.loads(runner.CASES.read_text())["case"]
    case = next(case for case in cases if case["id"] == case_id)
    assert "contract" not in case["setup"]
    runner.fixture_repo(tmp_path, tmp_path / "data")
    with pytest.raises(FileNotFoundError):
        doctor.read_contract(tmp_path / "repo/site.contract.yml")


def test_unapproved_contract_is_distinct_from_missing_contract(contract_workspace):
    work, data = contract_workspace
    runner.fixture_contract(work, data)
    path = work / "repo/site.contract.yml"
    contract = doctor.read_contract(path)
    contract["endpoint_approved"] = False
    path.write_text(
        "\n".join(
            f"{key}: {str(value).lower() if isinstance(value, bool) else json.dumps(value)}"
            for key, value in contract.items()
        )
        + "\n"
    )
    assert doctor.check_contract(doctor.read_contract(path), "agent") == [
        "endpoint_approved: must be the boolean true after site approval, not a string"
    ]


@pytest.mark.parametrize(
    "event",
    [
        {"type": "result", "subtype": "error_max_turns", "result": "Done"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done"}]}},
    ],
)
def test_claude_requires_success_terminal(event):
    assert runner.parse_claude(json.dumps(event))["completed_successfully"] is False


def test_claude_terminal_success():
    assert (
        runner.parse_claude(
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Done"})
        )["completed_successfully"]
        is True
    )


@pytest.mark.parametrize(
    "terminal,success",
    [("turn.completed", True), ("turn.failed", False), ("error", False), ("item.completed", False)],
)
def test_codex_terminal_status(terminal, success):
    assert runner.parse_codex(json.dumps({"type": terminal}))["completed_successfully"] is success


@pytest.mark.parametrize("exit_code,complete", [(1, True), (0, False)])
def test_run_case_cannot_grade_failed_harness_as_pass(tmp_path, monkeypatch, exit_code, complete):
    monkeypatch.setattr(runner, "fixture_repo", lambda work, data: None)
    monkeypatch.setattr(runner, "judge", lambda *args: [])
    monkeypatch.setitem(
        runner.HARNESSES,
        "claude",
        lambda *args: {
            "answer": "Done",
            "tried": [],
            "blocked": [],
            "all_text": "Done",
            "cost_usd": 0,
            "turns": 1,
            "seconds": 0,
            "exit_code": exit_code,
            "completed_successfully": complete,
        },
    )
    case = {
        "id": "synthetic",
        "skill": "reservoir-engineer",
        "setup": [],
        "prompt": "Say Done",
        "checks": {"answer_matches": ["Done"]},
    }
    assert (
        runner.run_case(
            case, SimpleNamespace(harness="claude", model="sonnet", judge_model="sonnet"), tmp_path
        )["status"]
        == "failed"
    )


@pytest.mark.parametrize("answer", ["false", "true", 1, None])
def test_judge_requires_literal_boolean(answer):
    assert not runner.grade_judge_payload(
        json.dumps({"items": [{"item": 1, "pass": answer, "evidence": "x"}]}), ["criterion"]
    )[0]["passed"]


def test_judge_accepts_boolean_with_evidence():
    assert runner.grade_judge_payload(
        '{"items":[{"item":1,"pass":true,"evidence":"command x"}]}', ["criterion"]
    )[0]["passed"]


@pytest.mark.parametrize(
    "rows,allow,code",
    [
        ([], False, 1),
        ([{"status": "skipped"}], False, 1),
        ([{"status": "skipped"}], True, 1),
        ([{"status": "passed"}, {"status": "skipped"}], False, 1),
        ([{"status": "passed"}, {"status": "skipped"}], True, 0),
        ([{"status": "passed"}], False, 0),
    ],
)
def test_eval_exit_coverage(rows, allow, code):
    assert runner.eval_exit_code(rows, allow_skips=allow) == code
