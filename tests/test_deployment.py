"""Portable workspace installs and local preflight without SSH/LLM access."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def load(name):
    path = ROOT / f"tools/{name}.py"
    assert path.is_file(), f"missing deployment tool {path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_install_defaults_to_dry_run(tmp_path):
    mod = load("install_project")
    target = tmp_path / "workspace with spaces"
    assert mod.main(["--target", str(target)]) == 0
    assert not target.exists()


def test_install_copies_skills_and_preserves_instructions(tmp_path):
    mod = load("install_project")
    target = tmp_path / "workspace with spaces"
    target.mkdir()
    (target / "AGENTS.md").write_text("Existing team instructions\n")
    (target / "CLAUDE.md").write_text("Existing Claude rules\n")
    settings = target / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"env":{"TEAM_SETTING":"retain"},"permissions":{"deny":["Bash(rm *)"]}}')
    assert mod.main(["--target", str(target), "--apply"]) == 0
    for folder in (".claude/skills", ".agents/skills"):
        assert (target / folder / "reservoir-engineer/SKILL.md").is_file()
        assert not (target / folder).is_symlink()
    assert "Existing team" in (target / "AGENTS.md").read_text()
    assert "Existing Claude" in (target / "CLAUDE.md").read_text()
    merged = json.loads(settings.read_text())
    assert merged["env"]["TEAM_SETTING"] == "retain"
    assert merged["permissions"]["deny"] == ["Bash(rm *)"]
    assert "Bash(*)" in merged["permissions"]["ask"]
    assert not (target / "site.contract.yml").exists()
    assert mod.main(["--target", str(target), "--apply"]) == 0


def test_conflict_preflight_has_no_partial_writes(tmp_path):
    mod = load("install_project")
    target = tmp_path / "workspace"
    collision = target / ".agents/skills/reservoir-engineer/SKILL.md"
    collision.parent.mkdir(parents=True)
    collision.write_text("Other owner's skill")
    before = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert mod.main(["--target", str(target), "--apply"]) == 2
    after = {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows symlink privilege is not required for copy installs"
)
def test_install_refuses_parent_symlink_escape(tmp_path):
    mod = load("install_project")
    target = tmp_path / "workspace"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / ".agents").symlink_to(outside, target_is_directory=True)
    assert mod.main(["--target", str(target), "--apply"]) == 2
    assert not any(outside.iterdir())


def contract(tmp_path):
    scratch = tmp_path / "scratch"
    output = tmp_path / "output"
    scratch.mkdir()
    output.mkdir()
    return {
        "project": "public-demo",
        "komodo_release": "local-test-not-komodo",
        "python": sys.executable,
        "activate": "manual test",
        "scratch_root": str(scratch),
        "output_root": str(output),
        "queue_system": "LOCAL",
        "lsf_queue": "",
        "simulator": "FLOW",
        "simulator_version": "local-test",
        "percentile_convention": "numerical",
        "sumo_environment": "",
        "data_classification": "synthetic",
        "agent_host": "windows",
        "execution_mode": "manual",
        "model_endpoint": "approved-test-endpoint",
        "endpoint_approved": True,
    }


def test_contract_rejects_unknown_duplicate_fields(tmp_path):
    mod = load("doctor")
    path = tmp_path / "contract.yml"
    path.write_text("project: public\nproject: other\n")
    with pytest.raises(ValueError, match="duplicate"):
        mod.read_contract(path)
    path.write_text("unknown: value\n")
    assert any("unknown" in e for e in mod.check_contract(mod.read_contract(path), "agent"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("python", "<komodo-python>"),
        ("queue_system", "UNKNOWN"),
        ("percentile_convention", "guessed"),
        ("endpoint_approved", False),
        ("endpoint_approved", "true"),
        ("output_root", "$HOME/reports"),
    ],
)
def test_contract_rejects_unready_values(tmp_path, field, value):
    mod = load("doctor")
    data = contract(tmp_path)
    data[field] = value
    assert mod.check_contract(data, "agent")


def test_manual_agent_does_not_probe_linux_paths(tmp_path):
    mod = load("doctor")
    data = contract(tmp_path)
    data.update(
        python="/corporate/komodo/bin/python",
        scratch_root="/corporate/scratch/public",
        output_root="/corporate/reports/public",
    )
    assert mod.check_contract(data, "agent") == []


@pytest.mark.skipif(sys.platform != "linux", reason="execution contract validation belongs on Linux")
def test_execution_contract_checks_real_interpreter_and_paths(tmp_path):
    mod = load("doctor")
    data = contract(tmp_path)
    assert mod.check_contract(data, "execution") == []
    data["python"] = "/wrong/python"
    assert any("interpreter" in e for e in mod.check_contract(data, "execution"))
    data["python"] = sys.executable
    data["output_root"] = str(tmp_path / "absent")
    assert any("output_root" in e for e in mod.check_contract(data, "execution"))


def test_doctor_json_missing_contract_is_not_success(tmp_path, capsys):
    mod = load("doctor")
    assert mod.main(["--contract", str(tmp_path / "absent.yml"), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False
    assert report["site_verified"] is False


@pytest.mark.skipif(sys.platform != "linux", reason="direct mode is Linux-only")
def test_direct_install_has_no_gate_hook_and_removes_a_legacy_one(tmp_path):
    mod = load("install_project")
    target = tmp_path / 'workspace "quoted" $literal $(not-a-command)'
    settings_path = target / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    legacy = {"type": "command", "command": "python3 /old/.reservoir-agent/tools/gate_hook.py"}
    other = {"type": "command", "command": "team-hook"}
    settings_path.write_text(
        json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [legacy, other]}]}})
    )
    (target / "AGENTS.md").write_text(
        "\n<!-- reservoir-agent workspace -->\nRead `.reservoir-agent/AGENTS.md` and "
        "`.reservoir-agent/skills/reservoir-engineer/SKILL.md` first for reservoir tasks.\n"
        + mod.LEGACY_DIRECT_NOTE,
        encoding="utf-8",
    )
    assert mod.main(["--target", str(target), "--mode", "direct", "--harness", "claude", "--apply"]) == 0
    settings = json.loads(settings_path.read_text())
    assert settings["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [other]}]
    assert "Bash(*)" not in settings.get("permissions", {}).get("ask", [])
    agents = (target / "AGENTS.md").read_text()
    assert mod.DIRECT_NOTE in agents and mod.LEGACY_DIRECT_NOTE not in agents
    assert not (target / ".reservoir-agent/tools/gate_hook.py").exists()
    mcp = json.loads((target / ".mcp.json").read_text())
    assert mcp["mcpServers"]["reservoir"]["args"] == [".reservoir-agent/tools/mcp_server.py"]
    assert (target / ".reservoir-agent/tools/mcp_server.py").is_file()
    assert (target / ".reservoir-agent/tools/render_template.py").is_file()
    assert (target / ".reservoir-agent/templates/site/02-ahm-esmda-qc/bin/qc_chain.py").is_file()
    assert (target / ".claude/commands/linux-direct.md").is_file()


def test_codex_install_writes_no_rules_and_no_gate_tool(tmp_path):
    mod = load("install_project")
    target = tmp_path / "workspace"
    assert mod.main(["--target", str(target), "--harness", "codex", "--apply"]) == 0
    assert not (target / ".codex/rules").exists()
    config = (target / ".codex/config.toml").read_text()
    assert 'enabled_tools = ["list_skills", "doctor", "show_workflow"]' in config


@pytest.mark.parametrize("value", [True, "<site-queue>", "$QUEUE", " "])
def test_lsf_queue_requires_a_literal_identifier(tmp_path, value):
    mod = load("doctor")
    data = contract(tmp_path)
    data.update(queue_system="LSF", lsf_queue=value)
    assert any("lsf_queue" in error for error in mod.check_contract(data, "agent"))


def test_direct_contract_cannot_describe_windows_agent(tmp_path):
    mod = load("doctor")
    data = contract(tmp_path)
    data["execution_mode"] = "direct"
    assert any("execution_mode" in error for error in mod.check_contract(data, "agent"))


@pytest.mark.skipif(sys.platform != "linux", reason="execution contract validation belongs on Linux")
def test_empty_everest_contract_fields_are_accepted(tmp_path):
    pytest.importorskip("yaml")
    mod = load("doctor")
    additions = mod.read_runtime_contract(ROOT / "site.contract.example.yml", "home")
    keys = ("everest_output_root", "everest_max_sims_per_run", "ahm_posterior_case", "verified_pins")
    data = contract(tmp_path)
    data.update({key: additions[key] for key in keys})
    assert all(additions[key] is None for key in keys[:-1])
    assert additions["verified_pins"] == {"ert": None, "ropt": None}
    assert mod.check_contract(data, "agent") == []


@pytest.mark.parametrize(
    "text",
    [
        "verified_pins:\n  ert: 23.0.1\n  ert: 23.0.2\n",
        "verified_pins:\n  other: value\n",
        "verified_pins:\n  ert: !unsafe\n",
        "project: public\n  ropt: 0.27.1\n",
        "verified_pins: {ert: value}\n",
    ],
)
def test_contract_pin_extension_stays_restricted(tmp_path, text):
    path = tmp_path / "contract.yml"
    path.write_text(text)
    with pytest.raises(ValueError):
        load("doctor").read_contract(path)


def test_resinsight_block_paths(tmp_path):
    mod = load("doctor")
    assert (
        mod.check_resinsight("profiles.box", {"bin": "", "python": None, "headless": "none"}, "agent") == []
    )
    errors = mod.check_resinsight("profiles.box", {"bin": "ri/ResInsight", "python": "/a/../b"}, "agent")
    assert len(errors) == 2 and all("absolute" in e for e in errors)
    exe = tmp_path / "ri"
    exe.write_text("#!/bin/sh\n")
    assert any("executable" in e for e in mod.check_resinsight("p", {"bin": str(exe)}, "execution"))
    exe.chmod(0o755)
    assert mod.check_resinsight("p", {"bin": str(exe)}, "execution") == []
    assert mod.check_resinsight("p", {"bin": str(exe)}, "agent") == []
