import importlib.util
import subprocess

import pytest
from test_check_skills import ROOT

spec = importlib.util.spec_from_file_location("release", ROOT / "tools/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "changes").mkdir()
    skill = tmp_path / "skills/alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: alpha\nversion: 0.4.6\n---\nBody stays.\n")
    (tmp_path / "VERSION").write_text("0.3.1\n")
    (tmp_path / "pyproject.toml").write_text(
        '# Keep this\n[project]\nname = "x"\nversion = "0.3.1" # keep\n\n[other]\nversion = "9"\n'
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- Legacy fix.\n  Wrapped legacy.\n\n"
        "## [0.3.1] - 2026-01-01\n\n### Added\n\n- Old.\n"
    )
    (tmp_path / "changes/README.md").write_text("Not a fragment.\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "initial"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def snapshot(root):
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def run(root, *args):
    return release.main(["--root", str(root), "--version", "0.4.0", "--date", "2026-10-16", *args])


def test_release_folds_in_order_and_preserves_text(repo):
    (repo / "changes/b.md").write_text(
        "---\nbump: alpha patch\n---\n### Added\n\n- Second.\n### Fixed\n\n- New fix.\n"
    )
    (repo / "changes/a.md").write_text(
        "---\nbump: alpha minor\n---\n### Added\n\n- First.\n  Wrapped `text`.\n    More text.\n"
    )
    assert run(repo) == 0
    assert (repo / "CHANGELOG.md").read_text() == (
        "# Changelog\n\n## [Unreleased]\n\n## [0.4.0] - 2026-10-16\n\n"
        "### Added\n\n- First.\n  Wrapped `text`.\n    More text.\n- Second.\n\n"
        "### Fixed\n\n- Legacy fix.\n  Wrapped legacy.\n- New fix.\n\n"
        "## [0.3.1] - 2026-01-01\n\n### Added\n\n- Old.\n"
    )
    assert (repo / "skills/alpha/SKILL.md").read_text() == (
        "---\nname: alpha\nversion: 0.5.0\n---\nBody stays.\n"
    )
    assert (repo / "VERSION").read_text() == "0.4.0\n"
    assert (repo / "pyproject.toml").read_text() == (
        '# Keep this\n[project]\nname = "x"\nversion = "0.4.0" # keep\n\n[other]\nversion = "9"\n'
    )
    assert sorted(p.name for p in (repo / "changes").iterdir()) == ["README.md"]


def test_dry_run_writes_nothing(repo, capsys):
    (repo / "changes/a.md").write_text("---\nbump: alpha major\n---\n### Added\n- New.\n")
    before = snapshot(repo)
    assert run(repo, "--dry-run") == 0
    assert snapshot(repo) == before
    output = capsys.readouterr().out
    assert "## [0.4.0] - 2026-10-16" in output
    assert "0.4.6 -> 1.0.0" in output
    assert "VERSION, pyproject.toml" in output


@pytest.mark.parametrize("level, expected", [("patch", "0.4.7"), ("minor", "0.5.0"), ("major", "1.0.0")])
def test_bump_levels(repo, level, expected):
    (repo / "changes/a.md").write_text(f"---\nbump: alpha {level}\n---\n### Added\n- New.\n")
    assert run(repo) == 0
    assert f"version: {expected}\n" in (repo / "skills/alpha/SKILL.md").read_text()


@pytest.mark.parametrize(
    "text, message",
    [
        ("---\nbump: missing patch\n---\n### Added\n- New.\n", "unknown skill"),
        ("---\nbump: alpha huge\n---\n### Added\n- New.\n", "bad level"),
        ("Outside.\n### Added\n- New.\n", "outside"),
        ("### Unknown\n- New.\n", "unknown section"),
        ("### Added\n", "without bullets"),
        ("", "without bullets"),
        ("---\nbump: alpha\n---\n### Added\n- New.\n", "invalid bump"),
        ("---\nbump: alpha patch\n", "unclosed"),
    ],
)
def test_fragment_errors_write_nothing(repo, text, message, capsys):
    (repo / "changes/a.md").write_text("### Added\n- Valid first.\n")
    (repo / "changes/b.md").write_text(text)
    before = snapshot(repo)
    assert run(repo) == 1
    assert snapshot(repo) == before
    assert message in capsys.readouterr().err


def test_empty_release_fails(repo, capsys):
    (repo / "CHANGELOG.md").write_text("## [Unreleased]\n\n")
    before = snapshot(repo)
    assert run(repo) == 1
    assert snapshot(repo) == before
    assert "no fragments and empty Unreleased" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["bad", "01.2.3", "0.3.1", "0.3.0", "0.3.1+build"])
def test_invalid_or_nonincreasing_version(repo, version):
    before = snapshot(repo)
    assert run(repo, "--version", version) == 1
    assert snapshot(repo) == before


@pytest.mark.parametrize("date", ["bad", "2026-02-30", "20261016"])
def test_invalid_date(repo, date):
    before = snapshot(repo)
    assert run(repo, "--date", date) == 1
    assert snapshot(repo) == before


def test_semver_precedence():
    versions = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0-rc.1", "1.0.0", "1.0.1"]
    assert sorted(reversed(versions), key=release.version_key) == versions


def test_all_sections_and_multiple_skill_bumps(repo):
    beta = repo / "skills/beta"
    beta.mkdir()
    (beta / "SKILL.md").write_text("---\nversion: 2.3.4\n---\nBeta.\n")
    body = "\n".join(f"### {section}\n\n- {section}.\n" for section in reversed(release.SECTIONS))
    (repo / "changes/a.md").write_text("---\nbump: alpha patch, beta major, alpha minor\n---\n" + body)
    assert run(repo) == 0
    text = (repo / "CHANGELOG.md").read_text().split("## [0.3.1]")[0]
    positions = [text.index(f"### {section}") for section in release.SECTIONS]
    assert positions == sorted(positions)
    assert "version: 0.5.0" in (repo / "skills/alpha/SKILL.md").read_text()
    assert "version: 3.0.0" in (beta / "SKILL.md").read_text()


def test_bullet_paragraph_spacing_is_preserved(repo):
    (repo / "changes/a.md").write_text("### Security\n\n- First.\n\n  Another paragraph.\n\n- Second.\n")
    assert run(repo) == 0
    assert "- First.\n\n  Another paragraph.\n\n- Second.\n" in (repo / "CHANGELOG.md").read_text()


def test_late_validation_error_writes_nothing(repo):
    (repo / "changes/a.md").write_text("---\nbump: alpha patch\n---\n### Added\n- New.\n")
    (repo / "pyproject.toml").write_text('[other]\nversion = "0.3.1"\n')
    before = snapshot(repo)
    assert run(repo) == 1
    assert snapshot(repo) == before
