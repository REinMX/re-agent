"""Installed read grants stay explicit and scoped."""

import hashlib
import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
MODES = [
    "manual",
    pytest.param("direct", marks=pytest.mark.skipif(sys.platform != "linux", reason="direct is Linux-only")),
]


def installer():
    spec = importlib.util.spec_from_file_location("install_project", ROOT / "tools/install_project.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def file_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("mode", MODES)
def test_install_plan_includes_read_envelope_without_a_grant(tmp_path, mode):
    target = tmp_path / "workspace with spaces"
    writes, _ = installer().plan_install(ROOT, target, mode, "both")
    helper = target / ".reservoir-agent/tools/read_envelope.py"
    assert helper in writes
    assert writes[helper] == (ROOT / "tools/read_envelope.py").read_bytes()
    manifest = json.loads(writes[target / ".reservoir-agent/install-manifest.json"])
    assert (
        manifest["files"][".reservoir-agent/tools/read_envelope.py"]
        == hashlib.sha256(writes[helper]).hexdigest()
    )
    assert not any(path.name == "read-envelope.txt" for path in writes)
    assert not target.exists()


@pytest.mark.parametrize("mode", MODES)
def test_installed_read_envelope_uses_explicit_external_grant_without_execution(tmp_path, mode, capsys):
    mod = installer()
    target = tmp_path / "workspace with spaces"
    target.mkdir()
    (target / "AGENTS.md").write_text("Existing team instructions\n")
    (target / "CLAUDE.md").write_text("Existing Claude rules\n")
    settings = target / ".claude/settings.json"
    settings.parent.mkdir()
    existing_hook = {"matcher": "Read", "hooks": [{"type": "command", "command": "team-check"}]}
    settings.write_text(
        json.dumps(
            {
                "env": {"TEAM_SETTING": "retain"},
                "permissions": {"deny": ["Bash(rm *)"], "ask": ["Bash(team-command *)"]},
                "hooks": {"PreToolUse": [existing_hook]},
            }
        )
    )
    codex = target / ".codex/config.toml"
    codex.parent.mkdir()
    codex.write_text('sandbox_mode = "read-only"\n# Existing team config\n')
    original_codex = codex.read_bytes()
    args = ["--target", str(target), "--mode", mode, "--apply"]
    assert mod.main(args) == 0
    capsys.readouterr()
    helper = target / ".reservoir-agent/tools/read_envelope.py"
    canonical = target / ".reservoir-agent/skills/case-qc/scripts/case_qc.py"
    assert helper.is_file()
    assert helper.read_bytes() == (ROOT / "tools/read_envelope.py").read_bytes()
    assert canonical.read_bytes() == (ROOT / "skills/case-qc/scripts/case_qc.py").read_bytes()
    assert not (target / ".reservoir-agent/worklog/read-envelope.txt").exists()
    assert not (target / "worklog/read-envelope.txt").exists()
    assert "Existing team instructions" in (target / "AGENTS.md").read_text()
    assert "Existing Claude rules" in (target / "CLAUDE.md").read_text()
    merged = json.loads(settings.read_text())
    assert merged["env"] == {"TEAM_SETTING": "retain"}
    assert merged["permissions"]["deny"] == ["Bash(rm *)"]
    assert "Bash(team-command *)" in merged["permissions"]["ask"]
    assert existing_hook in merged["hooks"]["PreToolUse"]
    assert merged["permissions"]["defaultMode"] == ("plan" if mode == "manual" else "default")
    assert ("Bash(*)" in merged["permissions"]["ask"]) == (mode == "manual")
    assert codex.read_bytes() == original_codex
    installed = file_bytes(target)
    assert mod.main(args) == 0
    assert json.loads(capsys.readouterr().out)["files"] == []
    assert file_bytes(target) == installed

    inputs = tmp_path / "synthetic inputs"
    inputs.mkdir()
    config = inputs / "case.ert"
    config.write_text("NUM_REALIZATIONS 1\n")
    output = tmp_path / "synthetic outputs"
    output.mkdir()
    grant = tmp_path / "external human grants/read-envelope.txt"
    grant.parent.mkdir()
    marker = tmp_path / "command-executed.txt"
    # Replace only disposable copies so a checker regression cannot execute engineering code.
    trap = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise SystemExit(91)\n"
    alternate_scripts = [
        target / ".agents/skills/case-qc/scripts/case_qc.py",
        target / ".claude/skills/case-qc/scripts/case_qc.py",
    ]
    for script in [canonical, *alternate_scripts]:
        script.write_text(trap)
    flags = ["--config", str(config), "--outdir", str(output / "fresh report")]
    command = shlex.join(["python3", str(canonical.relative_to(target)), *flags])

    def check(candidate, expected_code, expected_message, explicit=False):
        argv = [sys.executable, str(helper), "--today", "2026-10-08", "--command", candidate]
        if explicit:
            argv.extend(["--grant", str(grant)])
        before = file_bytes(tmp_path)
        result = subprocess.run(argv, cwd=target, capture_output=True, text=True, timeout=15, check=False)
        assert result.returncode == expected_code, (candidate, result.stdout, result.stderr)
        assert (result.stdout if expected_code == 0 else result.stderr).strip() == expected_message
        assert file_bytes(tmp_path) == before
        assert not marker.exists()
        assert not any(output.iterdir())

    def write_grant(expires):
        grant.write_text(
            f"owner: Synthetic test reviewer\nexpires: {expires}\nscript: case-qc\n"
            f"root: {inputs}\noutput_root: {output}\n"
        )

    check(command, 2, "ask: no read grant")
    check(command, 2, "ask: no read grant", explicit=True)
    write_grant("2020-01-01")
    check(command, 2, "ask: grant is expired", explicit=True)
    write_grant("2099-01-01")
    check(command, 0, "allow", explicit=True)
    check(shlex.join(["python3", str(canonical), *flags]), 0, "allow", explicit=True)
    check(command, 2, "ask: no read grant")
    check(command + " --sumo upload", 2, "ask: unknown flag --sumo", explicit=True)
    check(
        command + " && unreviewed-command", 2, "ask: command is more than one simple command", explicit=True
    )
    check("unreviewed-command", 2, "ask: command does not start with python", explicit=True)
    for script in alternate_scripts:
        check(
            shlex.join(["python3", str(script), *flags]),
            2,
            "ask: command is not the case-qc script",
            explicit=True,
        )
    outside = tmp_path / "outside inputs/case.ert"
    outside.parent.mkdir()
    outside.write_text("NUM_REALIZATIONS 1\n")
    check(
        shlex.join(["python3", str(canonical), "--config", str(outside), "--outdir", str(output / "report")]),
        2,
        "ask: an input path is outside the grant",
        explicit=True,
    )


@pytest.mark.parametrize("mode", MODES)
def test_read_envelope_conflict_refuses_install_without_partial_writes(tmp_path, mode, capsys):
    target = tmp_path / "workspace with spaces"
    helper = target / ".reservoir-agent/tools/read_envelope.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("Existing owner helper\n")
    before = file_bytes(target)
    assert installer().main(["--target", str(target), "--mode", mode, "--apply"]) == 2
    conflict = Path(".reservoir-agent/tools/read_envelope.py")
    assert f"conflict (not changed): {conflict}" in capsys.readouterr().err
    assert file_bytes(target) == before
