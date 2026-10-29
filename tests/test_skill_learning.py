"""Learning skill packaging/registration checks, not proof of agent compliance."""

import importlib.util
import json
import shutil
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NEW_SKILLS = ("continuous-learning", "earned-autonomy")


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def registry(tmp_path, monkeypatch):
    checker = load("check_skills")
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    (tmp_path / "skills/reservoir-engineer").mkdir(parents=True)
    (tmp_path / "evals").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/FEATURE_MAP.md").write_text(
        "| Skill | Role |\n|---|---|\n| `sample` | analyst |\n| `reservoir-engineer` | all |\n"
    )
    (tmp_path / "AGENTS.md").write_text("| `sample` | analyst | Example |\n")
    (tmp_path / "skills/reservoir-engineer/SKILL.md").write_text("| Example | `sample` |\n")
    (tmp_path / "evals/sample.md").write_text(
        "# Evals\n\n## Task 1. Normal\nPass: x\n\n## Task 2. Bad input\nPass: x\n\n## Task 3. Gate\nPass: x\n"
    )
    return checker, tmp_path


def check_registration(checker, name):
    assert hasattr(checker, "check_registration"), "skill registration check is missing"
    return checker.check_registration(name)


def test_complete_registration_passes(registry):
    checker, _ = registry
    assert check_registration(checker, "sample") == []


@pytest.mark.parametrize(
    "relative,expected",
    [
        ("AGENTS.md", "AGENTS.md"),
        ("skills/reservoir-engineer/SKILL.md", "router"),
        ("evals/sample.md", "eval"),
        ("docs/FEATURE_MAP.md", "docs/FEATURE_MAP.md"),
    ],
)
def test_missing_registration_fails(registry, relative, expected):
    checker, root = registry
    (root / relative).unlink()
    assert any(expected in e for e in check_registration(checker, "sample"))


def test_unlisted_skill_fails(registry):
    checker, _ = registry
    errors = check_registration(checker, "other")
    assert any("AGENTS.md" in e for e in errors)
    assert any("router" in e for e in errors)


@pytest.mark.parametrize(
    "relative,row,label",
    [
        ("AGENTS.md", "| `other` | analyst | See `sample` |\n", "AGENTS.md"),
        ("skills/reservoir-engineer/SKILL.md", "| Task about `sample` | `other` |\n", "router"),
        ("docs/FEATURE_MAP.md", "| `other` | analyst | See `sample` |\n", "docs/FEATURE_MAP.md"),
    ],
)
def test_name_in_wrong_column_is_not_registration(registry, relative, row, label):
    checker, root = registry
    (root / relative).write_text(row)
    assert any(label in e for e in check_registration(checker, "sample"))


def test_eval_requires_three_distinct_tasks(registry):
    checker, root = registry
    (root / "evals/sample.md").write_text("## Task 1. A\n## Task 1. Duplicate\n## Task 2. B\n")
    assert any("three distinct" in e for e in check_registration(checker, "sample"))


def test_router_does_not_need_to_route_itself(registry):
    checker, root = registry
    (root / "AGENTS.md").write_text("| `reservoir-engineer` | all | Entry |\n")
    (root / "evals/reservoir-engineer.md").write_text("## Task 1. A\n## Task 2. B\n## Task 3. C\n")
    assert check_registration(checker, "reservoir-engineer") == []


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_learning_skill_is_valid_registered_and_has_headless_cases(name):
    checker = load("check_skills")
    assert (ROOT / "skills" / name / "SKILL.md").is_file(), f"missing {name}"
    assert checker.check_skill(ROOT / "skills" / name) == []
    assert check_registration(checker, name) == []
    cases = tomllib.loads((ROOT / "evals/cases.toml").read_text())["case"]
    selected = [c for c in cases if c["skill"] == name]
    assert len(selected) >= 3
    assert len({c["id"] for c in selected}) == len(selected)
    assert all(c.get("rubric") and c.get("max_turns", 20) <= 20 for c in selected)


def test_portable_install_copies_learning_references_without_extra_authority(tmp_path):
    installer = load("install_project")
    target = tmp_path / "manual workspace"
    assert installer.main(["--target", str(target), "--apply"]) == 0
    for name in NEW_SKILLS:
        source = ROOT / "skills" / name
        assert source.is_dir(), f"missing {name}"
        files = [p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        assert len(files) >= 2
        for file in files:
            for prefix in (".reservoir-agent/skills", ".claude/skills", ".agents/skills"):
                installed = target / prefix / name / file.relative_to(source)
                assert installed.read_bytes() == file.read_bytes()
    config = (target / ".codex/config.toml").read_text()
    assert 'sandbox_mode = "read-only"' in config
    assert "rules = true" in config
    settings = json.loads((target / ".claude/settings.json").read_text())
    assert settings["permissions"]["defaultMode"] == "plan"
    assert "Bash(*)" in settings["permissions"]["ask"]


def test_fresh_install_excludes_source_lessons_grants_and_receipts(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("skills", "tools", "docs", "profiles"):
        shutil.copytree(ROOT / name, source / name)
    for name in (
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "site.contract.example.yml",
        "site.contract.schema.json",
        "VERSION",
        "CHANGELOG.md",
    ):
        shutil.copy2(ROOT / name, source / name)
    private_paths = (
        "worklog/lessons/local.md",
        "worklog/learning-grant.md",
        "worklog/receipts/local.md",
        "site.contract.yml",
    )
    marker = b"SYNTHETIC-PRIVATE-RECORD-DO-NOT-INSTALL"
    for relative in private_paths:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(marker)
    installer = load("install_project")
    target = tmp_path / "fresh-workspace"
    assert installer.main(["--source", str(source), "--target", str(target), "--apply"]) == 0
    for relative in private_paths:
        assert not (target / relative).exists()
        assert not (target / ".reservoir-agent" / relative).exists()
    assert all(marker not in p.read_bytes() for p in target.rglob("*") if p.is_file())


def test_reinstall_refuses_changed_learning_copy_and_preserves_private_lessons(tmp_path):
    installer = load("install_project")
    target = tmp_path / "workspace"
    assert installer.main(["--target", str(target), "--apply"]) == 0
    copy = target / ".agents/skills/continuous-learning/SKILL.md"
    assert copy.is_file(), "learning skill missing from installation"
    copy.write_text("Locally reviewed variant\n")
    lesson = target / "worklog/lessons/public-example.md"
    lesson.parent.mkdir(parents=True)
    lesson.write_text("Private local lesson\n")
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert installer.main(["--target", str(target), "--apply"]) == 2
    after = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert after == before
