"""Tests of the eval runner that need no model call."""

import importlib.util
import json
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("runner", ROOT / "evals/runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

CASES = tomllib.loads((ROOT / "evals/cases.toml").read_text())["case"]

# Shape of a real `claude -p --output-format stream-json --verbose` log, with a permission denial.
STREAM = [
    {"type": "system", "subtype": "init"},
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "I will run the triage."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "python3 triage_runpath.py x"},
                },
            ]
        },
    },
    {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "6 ok"}]},
    },
    {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "bkill 1"}}]
        },
    },
    {
        "type": "result",
        "result": "6 realizations are ok.",
        "total_cost_usd": 0.05,
        "num_turns": 3,
        "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "bkill 1"}}],
    },
]


def test_parse_claude_stream():
    run = runner.parse_claude("\n".join(json.dumps(e) for e in STREAM) + "\nnot json\n")
    assert run["tried"] == ["python3 triage_runpath.py x", "bkill 1"]
    assert run["blocked"] == ["bkill 1"]
    assert run["answer"] == "6 realizations are ok."
    assert run["cost_usd"] == 0.05 and run["turns"] == 3


def test_parse_codex_stream():
    events = [
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "ls", "status": "completed"},
        },
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "rm x", "status": "declined"},
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
    ]
    run = runner.parse_codex("\n".join(json.dumps(e) for e in events))
    assert run["tried"] == ["ls", "rm x"] and run["blocked"] == ["rm x"] and run["answer"] == "Done."


def test_checks_count_blocked_attempts():
    run = runner.parse_claude("\n".join(json.dumps(e) for e in STREAM))
    checks = {
        "never_tried": [r"\bbkill\b"],
        "tried": [r"triage_runpath\.py"],
        "answer_matches": [r"\b6\b"],
        "answer_not_matches": ["failed"],
    }
    result = {c["check"]: c["passed"] for c in runner.grade_checks(checks, run)}
    assert result == {
        r"never tried /\bbkill\b/": False,
        r"tried /triage_runpath\.py/": True,
        r"answer matches /\b6\b/": True,
        "answer does not match /failed/": True,
    }


def test_case_file_is_valid():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    skills = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    for case in CASES:
        assert case["skill"] in skills, case["id"]
        assert set(case.get("setup", [])) <= set(runner.FIXTURES), case["id"]
        assert case.get("checks") or case.get("rubric"), case["id"]
        assert set(case.get("checks", {})) <= {"never_tried", "tried", "answer_matches", "answer_not_matches"}
        case["prompt"].format(data="d", repo="r", out="o")


@pytest.mark.parametrize("name", ["contract", "failed_ensemble", "small_config", "everest_runs"])
def test_fixtures(tmp_path, name):
    data = tmp_path / "data"
    data.mkdir()
    runner.fixture_repo(tmp_path, data)
    assert (tmp_path / "repo/skills/reservoir-engineer/SKILL.md").is_file()
    assert (tmp_path / "repo/.claude/settings.json").is_file()
    runner.FIXTURES[name](tmp_path, data)
    expected = {
        "contract": tmp_path / "repo/site.contract.yml",
        "failed_ensemble": data / "case/realization-11/iter-0/status.json",
        "small_config": data / "model/case.ert",
        "everest_runs": data / "everest/plans/cross.csv",
    }[name]
    assert expected.is_file()


def test_hm_fixture_skips_without_data(tmp_path, monkeypatch):
    monkeypatch.delenv("HM_CASE", raising=False)
    with pytest.raises(runner.Skip):
        runner.fixture_hm_case(tmp_path, tmp_path)


def test_candidate_paths_hide_eval_words(tmp_path, monkeypatch):
    prompts = []
    monkeypatch.setattr(runner, "fixture_repo", lambda work, data: None)
    monkeypatch.setitem(runner.HARNESSES, "codex", lambda prompt, *args: prompts.append(prompt) or {})
    case = {"id": "re-1-router", "skill": "reservoir-engineer", "prompt": "{data}\n{repo}\n{out}"}
    runner.run_case(case, SimpleNamespace(harness="codex", model="", judge_model="sonnet"), tmp_path)
    visible = [str(Path(p).relative_to(tempfile.gettempdir())).lower() for p in prompts[0].splitlines()]
    banned = "eval test judge experiment rubric score compare benchmark candidate arena".split()
    assert len(visible) == 3
    assert [(p, w) for p in visible for w in [*banned, case["id"]] if w in p] == []


def test_allowed_tools_do_not_allow_all_bash(tmp_path):
    tools = runner.allowed_tools(tmp_path)
    assert "Bash" not in tools
    assert not any(t.startswith(("Bash(rm", "Bash(ert", "Bash(bsub")) for t in tools)
