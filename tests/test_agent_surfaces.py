"""Exercise generated artifacts, drift detection, and opt-in installation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import check_skills
from tools.build_agent_surfaces import HEADER, build, metadata
from tools.check_skills import check_skill

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "plugins/pstack"
SKILLS = sorted((VENDOR / "skills").glob("*/SKILL.md"))
WORKFLOWS = {
    "poteto-mode",
    "how",
    "why",
    "swarm",
    "no-comments",
    "architect",
    "tdd",
    "unslop",
    "technical-writing",
    "blast-radius",
    "interrogate",
    "correct",
    "show-me-your-work",
    "figure-it-out",
}
PRINCIPLES = {
    "attack-the-premise",
    "boundary-discipline",
    "build-the-lever",
    "encode-lessons-in-structure",
    "exhaust-the-design-space",
    "experience-first",
    "explain-the-number",
    "fix-root-causes",
    "foundational-thinking",
    "guard-the-context-window",
    "laziness-protocol",
    "make-operations-idempotent",
    "migrate-callers-then-delete-legacy-apis",
    "minimize-reader-load",
    "model-the-domain",
    "never-block-on-the-human",
    "outcome-oriented-execution",
    "prove-it-works",
    "redesign-from-first-principles",
    "separate-before-serializing-shared-state",
    "sequence-verifiable-units",
    "subtract-before-you-add",
    "test-behavior-not-implementation",
    "type-system-discipline",
}


def test_surfaces_in_sync():
    result = subprocess.run(
        [sys.executable, "tools/build_agent_surfaces.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_vendored_subset_and_attribution():
    assert {p.parent.name for p in SKILLS} == WORKFLOWS | {"principle-" + n for n in PRINCIPLES}
    license_text = (VENDOR / "LICENSE").read_text()
    assert "Copyright (c) 2026 Lauren Tan" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    notice = (VENDOR / "NOTICE.md").read_text()
    assert "1f0f9c3bac03c4297f6fc8f7644fa412eda57085" in notice
    assert "https://github.com/backnotprop/pstack" in notice
    for path in SKILLS:
        assert not check_skill(path.parent)
        text = path.read_text(encoding="utf-8")
        assert "Lauren Tan / poteto, MIT" in text
        for target in ("../../LICENSE", "../../NOTICE.md"):
            assert f"]({target})" in text
            assert (path.parent / target).is_file()
        assert "subagent_type" not in text
        assert "grok-4." not in text
        assert "claude-opus-" not in text


def test_generated_prompts_agents_and_index_have_headers():
    prompts = sorted((ROOT / ".github/prompts").glob("pstack-*.prompt.md"))
    assert len(prompts) == 38
    agents = sorted((ROOT / ".github/agents").glob("*.agent.md"))
    claude = sorted((ROOT / ".claude/agents").glob("*.md"))
    assert {p.stem for p in claude} == {"comment-sicko", "poteto-agent"}
    assert len(agents) == 2
    for path in prompts + agents + claude + [ROOT / ".github/copilot-instructions.md"]:
        text = path.read_text(encoding="utf-8")
        assert HEADER in text
        if path in prompts:
            assert 'mode: "agent"' in text
            assert "plugins/pstack/skills/" in text
        if path in agents + claude:
            assert "tools:" in text
            assert "description:" in text
    index = (ROOT / "AGENTS.md").read_text().split("<!-- pstack:begin -->")[1]
    assert HEADER in index
    for path in SKILLS:
        assert path.relative_to(ROOT).as_posix() in index
        for harness in (".claude", ".agents"):
            link = ROOT / harness / "skills" / ("pstack-" + path.parent.name)
            if not (ROOT / harness / "skills").is_symlink():
                assert (ROOT / harness / "skills").read_text().strip() == "../skills"
                link = ROOT / "skills" / ("pstack-" + path.parent.name)
            if not link.is_symlink():
                assert link.read_text().strip() == f"../plugins/pstack/skills/{path.parent.name}"
                continue
            assert link.is_symlink()
            assert (link / "SKILL.md").resolve() == path.resolve()


@pytest.fixture
def surface_repo(tmp_path):
    for relative in ("plugins/pstack", "skills"):
        shutil.copytree(
            ROOT / relative, tmp_path / relative, symlinks=True, ignore=shutil.ignore_patterns("__pycache__")
        )
    shutil.copyfile(ROOT / "AGENTS.md", tmp_path / "AGENTS.md")
    assert build(tmp_path) == 0
    return tmp_path


def test_drift_source_changes_and_repeatability(surface_repo):
    root = surface_repo
    assert build(root, check=True) == 0
    prompt = root / ".github/prompts/pstack-how.prompt.md"
    prompt.write_text("drift\n")
    assert build(root, check=True) == 1
    assert prompt.read_text() == "drift\n", "--check must not write"
    assert build(root) == 0
    assert build(root, check=True) == 0
    source = root / "plugins/pstack/skills/how/SKILL.md"
    source.write_text(source.read_text().replace('description: "', 'description: "Changed '))
    assert build(root, check=True) == 1
    assert build(root) == 0
    assert "Changed" in prompt.read_text()
    agent = root / "plugins/pstack/agents/poteto-agent.md"
    agent.write_text(agent.read_text() + "\nA new scoped instruction.\n")
    assert build(root, check=True) == 1
    assert build(root) == 0
    assert "A new scoped instruction." in (root / ".claude/agents/poteto-agent.md").read_text()


def installer(home, *args):
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["bash", str(ROOT / "tools/install_user_skills.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="POSIX installer")
def test_installer_dry_run_is_opt_in(tmp_path):
    default = installer(tmp_path, "--dry-run")
    assert default.returncode == 0
    assert "plugins/pstack" not in default.stdout
    result = installer(tmp_path, "--with-pstack", "--dry-run")
    assert result.returncode == 0, result.stderr
    for path in SKILLS:
        name = path.parent.name
        for harness in (".claude", ".codex"):
            assert f"{tmp_path}/{harness}/skills/pstack-{name} -> {VENDOR}/skills/{name}" in result.stdout
    assert "chat.promptFilesLocations" in result.stdout
    assert not list(tmp_path.iterdir()), "dry run must not create folders"


def test_cloud_setup_is_lightweight():
    workflow = (ROOT / "plugins/pstack/templates/copilot-setup-steps.yml").read_text()
    assert "  copilot-setup-steps:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "actions/checkout@v4" in workflow
    assert "actions/setup-python@v5" in workflow
    assert "pip install pytest ruff" in workflow
    assert "make check" in workflow
    assert not re.search(r"ert|everest|opm|\bflow\b|secrets\.", workflow, re.I)


def test_frontmatter_is_valid():
    for path in SKILLS:
        fields, _ = metadata(path)
        assert fields["name"] == path.parent.name
        assert fields["description"]


def test_model_roles_and_no_vendor_slugs():
    for path in SKILLS:
        text = path.read_text().lower()
        assert not re.search(r"grok[-\w.]*|cursor[-/][\w.-]*", text)
    for name in ("poteto-mode", "swarm", "how", "why", "architect", "interrogate"):
        text = (VENDOR / "skills" / name / "SKILL.md").read_text()
        assert "agents.models" in text
        assert "~/.agents/pstack-models.md" in text
        assert "inherit-parent" in text
    for path in (ROOT / ".github/agents").glob("*.agent.md"):
        assert "model:" not in path.read_text().split("---")[1]


def test_explicit_agent_model(surface_repo):
    agent = surface_repo / "plugins/pstack/agents/poteto-agent.md"
    agent.write_text(agent.read_text().replace("name: poteto-agent", "name: poteto-agent\nmodel: GPT-5"))
    assert build(surface_repo) == 0
    target = surface_repo / ".github/agents/poteto-agent.agent.md"
    assert 'model: "GPT-5"' in target.read_text()
    agent.write_text(agent.read_text().replace("model: GPT-5", "model: inherit-parent"))
    assert build(surface_repo) == 0
    assert "model:" not in target.read_text().split("---")[1]


def test_role_models_only_set_configured_copilot_agents(surface_repo):
    assert build(surface_repo, models={"review": "GPT-5"}) == 0
    agent = surface_repo / ".github/agents/comment-sicko.agent.md"
    assert 'model: "GPT-5"' in agent.read_text()
    code = surface_repo / ".github/agents/poteto-agent.agent.md"
    assert "model:" not in code.read_text().split("---")[1]
    claude = surface_repo / ".claude/agents/comment-sicko.md"
    assert "model:" not in claude.read_text().split("---")[1]
    assert build(surface_repo) == 0
    assert "model:" not in agent.read_text().split("---")[1]


def test_checker_ignores_only_cache_only_source_folders(tmp_path, monkeypatch):
    cache = tmp_path / "old-skill/scripts/__pycache__"
    cache.mkdir(parents=True)
    (cache / "old.cpython-312.pyc").write_bytes(b"old cache")
    partial = tmp_path / "partial/scripts"
    partial.mkdir(parents=True)
    (partial / "check.py").write_text("pass\n")
    monkeypatch.setattr(check_skills, "SKILLS", tmp_path)
    assert [p.name for p in check_skills.source_folders()] == ["partial"]
    assert check_skill(tmp_path / "partial") == ["partial: SKILL.md is missing"]


def test_stale_generated_outputs_and_git_placeholders(surface_repo):
    root = surface_repo
    stale = root / ".github/prompts/pstack-removed.prompt.md"
    stale.write_text(f"<!-- {HEADER} -->\nremoved\n")
    foreign = root / ".github/agents/user.agent.md"
    foreign.write_text("User owned agent\n")
    assert build(root, check=True) == 1
    assert stale.exists()
    assert build(root) == 0
    assert not stale.exists()
    assert foreign.read_text() == "User owned agent\n"
    link = root / "skills/pstack-how"
    if link.is_symlink():
        link.unlink()
    link.write_text("../plugins/pstack/skills/how")
    assert build(root, check=True) == 0
    prompt = root / ".github/prompts/pstack-how.prompt.md"
    prompt.write_bytes(prompt.read_bytes().replace(b"\n", b"\r\n"))
    assert build(root, check=True) == 0, "Git newline conversion must not cause false drift"
