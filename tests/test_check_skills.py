import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools/check_skills.py"
spec = importlib.util.spec_from_file_location("check_skills", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def write(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(text)
    return folder


def test_repo_skills_pass():
    assert check.main() == 0


def test_name_must_match_folder(tmp_path):
    folder = write(tmp_path / "alpha", "---\nname: beta\ndescription: x\n---\nbody\n")
    assert any("differs from the folder name" in e for e in check.check_skill(folder))


def test_missing_front_matter(tmp_path):
    folder = write(tmp_path / "alpha", "no front matter\n")
    assert check.check_skill(folder)


def test_missing_reference_is_reported(tmp_path):
    folder = write(tmp_path / "alpha", "---\nname: alpha\ndescription: x\n---\nSee `references/none.md`.\n")
    assert any("references/none.md" in e for e in check.check_skill(folder))


def test_valid_skill_passes(tmp_path):
    folder = write(
        tmp_path / "alpha", "---\nname: alpha\ndescription: Does a thing.\nversion: 0.1.0\n---\nbody\n"
    )
    assert check.check_skill(folder) == []


@pytest.mark.parametrize(
    "line", ["", "version: \n", "version: 1.0\n", "version: v1.0.0\n", "version: 01.0.0\n"]
)
def test_skill_version_must_be_semver(tmp_path, line):
    folder = write(tmp_path / "alpha", f"---\nname: alpha\ndescription: x\n{line}---\nbody\n")
    assert any("semantic version" in e for e in check.check_skill(folder))


@pytest.mark.parametrize("version", ["0.1.0", "1.2.3-rc.1", "2.0.0+build.5"])
def test_skill_version_accepts_semver(tmp_path, version):
    folder = write(tmp_path / "alpha", f"---\nname: alpha\ndescription: x\nversion: {version}\n---\nbody\n")
    assert check.check_skill(folder) == []


def test_every_repo_skill_has_a_version():
    for skill in sorted((ROOT / "skills").glob("*/SKILL.md")):
        fields, _ = check.parse_front_matter(skill.read_text())
        assert check.SEMVER_RE.match(fields.get("version", "")), skill


def test_repo_version_matches_pyproject():
    assert check.check_version(ROOT) == []


@pytest.mark.parametrize(
    ("version", "project", "message"),
    [("0.3.0", "0.2.0", "differs"), ("0.2", "0.2", "not a semantic version")],
)
def test_version_file_mismatch_is_reported(tmp_path, version, project, message):
    (tmp_path / "VERSION").write_text(version + "\n")
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{project}"\n')
    assert any(message in e for e in check.check_version(tmp_path))


def test_missing_version_file_is_reported(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    assert check.check_version(tmp_path)


def test_windows_git_link_placeholders_are_valid_source(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    for folder in (".claude", ".agents"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "skills").write_text("../skills")
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check, "SKILLS", skills)
    assert check.check_links() == []


@pytest.fixture
def git_repo(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    write(tmp_path / "skills/alpha", "---\nname: alpha\nversion: 0.1.0\n---\nBody.\n")
    (tmp_path / "VERSION").write_text("0.1.0\n")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n")
    (tmp_path / ".gitignore").write_text("ignored\n")
    (tmp_path / "changes").mkdir()
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "initial")
    return tmp_path


def test_shared_changelog_failure(git_repo):
    (git_repo / "CHANGELOG.md").write_text("Changed\n")
    assert check.check_shared_lines(git_repo, "HEAD") == [
        "CHANGELOG.md: add changes/<topic>.md instead; `make release` writes CHANGELOG.md"
    ]


def test_shared_version_failure(git_repo):
    path = git_repo / "skills/alpha/SKILL.md"
    path.write_text(path.read_text().replace("0.1.0", "0.1.1"))
    assert any("version: changes only" in e for e in check.check_shared_lines(git_repo, "HEAD"))


@pytest.mark.parametrize("tracked", [True, False])
def test_skill_change_requires_bump(git_repo, tracked):
    path = git_repo / "skills/alpha" / ("SKILL.md" if tracked else "new.py")
    path.write_text(path.read_text() + "More.\n" if tracked else "print(1)\n")
    assert check.check_shared_lines(git_repo, "HEAD") == ["alpha: changed without a fragment that bumps it"]
    (git_repo / "changes/a.md").write_text("---\nbump: alpha patch\n---\n### Fixed\n- Fix.\n")
    assert check.check_shared_lines(git_repo, "HEAD") == []


def test_release_shaped_allowance(git_repo):
    (git_repo / "VERSION").write_text("0.2.0\n")
    (git_repo / "CHANGELOG.md").write_text("Release\n")
    (git_repo / "skills/alpha/SKILL.md").write_text("---\nversion: 0.2.0\n---\nNew.\n")
    (git_repo / "changes/README.md").write_text("Instructions\n")
    assert check.check_shared_lines(git_repo, "HEAD") == []
    (git_repo / "changes/a.md").write_text("### Added\n- A.\n")
    assert check.check_shared_lines(git_repo, "HEAD")


def test_no_base_skips(git_repo, capsys):
    assert check.check_shared_lines(git_repo) == []
    assert "shared-line check skipped: no base ref" in capsys.readouterr().out


def test_default_base_uses_origin_main(git_repo):
    check.git(git_repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (git_repo / "CHANGELOG.md").write_text("Changed\n")
    assert check.check_shared_lines(git_repo)


@pytest.mark.parametrize(
    "text, message",
    [
        ("### Added\n- A.\n", None),
        ("---\nbump: alpha minor\n---\n### Fixed\n- A.\n", None),
        ("---\nbump: unknown patch\n---\n### Fixed\n- A.\n", "unknown skill"),
        ("---\nbump: alpha huge\n---\n### Fixed\n- A.\n", "bad level"),
        ("### Added\n", "without bullets"),
        ("Outside\n", "outside"),
    ],
)
def test_check_fragments(git_repo, text, message):
    (git_repo / "changes/a.md").write_text(text)
    (git_repo / "changes/README.md").write_text("Not a fragment")
    errors = check.check_fragments(git_repo)
    assert any(message in e for e in errors) if message else not errors


def test_ignored_bytecode_and_symlinks_do_not_need_bumps(git_repo):
    folder = git_repo / "skills/alpha"
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__/a.pyc").write_bytes(b"x")
    (folder / "ignored").write_text("ignored")
    (git_repo / "target").mkdir()
    (git_repo / "target/a.py").write_text("changed")
    (git_repo / "skills/link").symlink_to("../target", target_is_directory=True)
    assert check.check_shared_lines(git_repo, "HEAD") == []


def test_deleted_skill_is_checked(git_repo):
    (git_repo / "skills/alpha/SKILL.md").unlink()
    (git_repo / "skills/alpha").rmdir()
    errors = check.check_shared_lines(git_repo, "HEAD")
    assert any("version: changes only" in error for error in errors)
    assert "alpha: changed without a fragment that bumps it" in errors
    (git_repo / "changes/alpha.md").write_text("### Removed\n- `alpha`: its commands do not exist.\n")
    assert check.check_shared_lines(git_repo, "HEAD") == []


def test_new_skill_needs_no_bump(git_repo):
    write(git_repo / "skills/beta", "---\nname: beta\nversion: 0.1.0\n---\nBody.\n")
    assert check.check_shared_lines(git_repo, "HEAD") == []


@pytest.fixture
def validation_repo(git_repo):
    repo = git_repo
    tools = repo / "tools"
    tools.mkdir()
    for name in ("check_skills.py", "release.py"):
        shutil.copyfile(ROOT / "tools" / name, tools / name)
    shutil.copyfile(ROOT / "Makefile", repo / "Makefile")
    alpha = repo / "skills/alpha/SKILL.md"
    alpha.write_text(alpha.read_text().replace("name: alpha\n", "name: alpha\ndescription: Check alpha.\n"))
    write(
        repo / "skills/reservoir-engineer",
        "---\nname: reservoir-engineer\ndescription: Route the task.\nversion: 0.1.0\n---\n| analyst | `alpha` |\n",
    )
    (repo / "AGENTS.md").write_text("| `alpha` |\n| `reservoir-engineer` |\n")
    (repo / "docs").mkdir()
    (repo / "docs/FEATURE_MAP.md").write_text("| `alpha` |\n| `reservoir-engineer` |\n")
    (repo / "evals").mkdir()
    for name in ("alpha", "reservoir-engineer"):
        (repo / "evals" / f"{name}.md").write_text("## Task 1. A\n## Task 2. B\n## Task 3. C\n")
    (repo / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (repo / "changes/validation.md").write_text("---\nbump: alpha patch\n---\n### Fixed\n- Check alpha.\n")
    for folder in (".claude", ".agents"):
        (repo / folder).mkdir()
        (repo / folder / "skills").symlink_to("../skills", target_is_directory=True)
    (repo / "home").mkdir()
    return repo


def checker_cli(repo, *args, **environment):
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "STE_LINT"}}
    env.update(HOME=str(repo / "home"), **environment)
    return subprocess.run(
        [sys.executable, str(repo / "tools/check_skills.py"), "--base", "HEAD", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize("discovery", ["environment", "home"])
def test_default_check_ignores_host_ste(validation_repo, discovery):
    repo = validation_repo
    lint = repo / "home/.claude/skills/asd-ste100/scripts/ste-lint.py"
    lint.parent.mkdir(parents=True)
    marker = repo / "host-tool-ran"
    lint.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(1)\n")
    environment = {"STE_LINT": str(lint)} if discovery == "environment" else {}
    result = checker_cli(repo, **environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["missing", "directory", "broken-link"])
def test_explicit_invalid_ste_fails_closed(validation_repo, kind):
    lint = validation_repo / "requested-lint.py"
    if kind == "directory":
        lint.mkdir()
    elif kind == "broken-link":
        lint.symlink_to("absent.py")
    result = checker_cli(validation_repo, "--ste-lint", str(lint))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ste-lint" in result.stdout and "not a file" in result.stdout


@pytest.fixture
def ste_tool(validation_repo):
    lint = validation_repo / "lint tool.py"
    receipt = validation_repo / "ste-inputs.json"
    lint.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        f"Path({str(receipt)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    return lint, receipt


def test_explicit_ste_scans_owned_prose_not_vendor_links(validation_repo, ste_tool):
    repo = validation_repo
    lint, receipt = ste_tool
    vendor = write(
        repo / "plugins/pstack/skills/demo",
        "---\nname: demo\ndescription: Vendor.\nversion: 0.1.0\n---\nVendor.\n",
    )
    (vendor / "references").mkdir()
    (vendor / "references/vendor.md").write_text("Vendor reference.\n")
    (repo / "skills/pstack-demo").symlink_to("../plugins/pstack/skills/demo", target_is_directory=True)
    references = repo / "skills/alpha/references"
    references.mkdir()
    (references / "owned.md").write_text("Owned reference.\n")
    (references / "nested").mkdir()
    (references / "nested/owned.md").write_text("Nested owned reference.\n")
    (references / "vendor.md").symlink_to(vendor / "references/vendor.md")
    (references / "vendor").symlink_to(vendor / "references", target_is_directory=True)
    result = checker_cli(repo, "--ste-lint", str(lint))
    assert result.returncode == 0, result.stdout + result.stderr
    assert set(json.loads(receipt.read_text())) == {
        str(repo / "skills/alpha/SKILL.md"),
        str(repo / "skills/reservoir-engineer/SKILL.md"),
        str(references / "owned.md"),
        str(references / "nested/owned.md"),
    }


def test_explicit_ste_reports_tool_identity(validation_repo, ste_tool):
    lint, _ = ste_tool
    result = checker_cli(validation_repo, "--ste-lint", str(lint))
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(lint.resolve()) in result.stdout
    assert f"sha256={hashlib.sha256(lint.read_bytes()).hexdigest()}" in result.stdout
    assert sys.executable in result.stdout


def test_explicit_ste_failure_keeps_diagnostics(validation_repo):
    lint = validation_repo / "bad-lint.py"
    lint.write_text(
        "import sys\nprint('prose violation')\nprint('tool error', file=sys.stderr)\nraise SystemExit(7)\n"
    )
    result = checker_cli(validation_repo, "--ste-lint", str(lint))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "prose violation" in result.stdout and "tool error" in result.stdout
    assert "exit 7" in result.stdout


@pytest.mark.parametrize("target", ["ste", "prose"])
@pytest.mark.parametrize("selection", ["valid", "missing", "empty"])
def test_make_prose_requires_explicit_tool(validation_repo, ste_tool, target, selection):
    lint, receipt = ste_tool
    selected = {"valid": str(lint), "missing": str(validation_repo / "absent"), "empty": ""}[selection]
    result = subprocess.run(
        ["make", target, f"PY={sys.executable}", f"STE_LINT={selected}"],
        cwd=validation_repo,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if selection == "valid":
        assert result.returncode == 0, result.stdout + result.stderr
        assert receipt.is_file()
    else:
        assert result.returncode != 0
        assert "STE_LINT" in result.stderr or "not a file" in result.stdout
        assert not receipt.exists()
