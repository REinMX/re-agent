"""Update path, user skill links, and legacy gate cleanup. Temporary Git repositories and HOME only."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("git") or not shutil.which("bash"),
    reason="the update and link scripts need bash, git, and symlinks",
)


def environment(home: Path) -> dict:
    home.mkdir(exist_ok=True)
    return dict(
        os.environ,
        HOME=str(home),
        PY=sys.executable,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )


def run(args, cwd, env, check=True):
    done = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    if check:
        assert done.returncode == 0, done.stdout + done.stderr
    return done


def skill(repo: Path, name: str, text: str = "body") -> None:
    folder = repo / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\nversion: 0.1.0\n---\n{text}\n")


def make_upstream(tmp_path: Path, env: dict) -> tuple[Path, Path, Path]:
    """Return (bare remote, author checkout, user checkout)."""
    author = tmp_path / "author"
    (author / "tools").mkdir(parents=True)
    for name in ("update.sh", "install_user_skills.sh", "remove_legacy_gates.py"):
        shutil.copy2(ROOT / "tools" / name, author / "tools" / name)
    (author / "tools/check_skills.py").write_text("print('stub skill check')\n")
    (author / "tools/doctor.py").write_text("import sys\nprint('stub doctor not ready')\nsys.exit(1)\n")
    (author / "VERSION").write_text("0.1.0\n")
    skill(author, "alpha")
    skill(author, "beta")
    run(["git", "init", "-q", "-b", "main"], author, env)
    run(["git", "add", "-A"], author, env)
    run(["git", "commit", "-q", "-m", "first"], author, env)
    bare = tmp_path / "remote.git"
    run(["git", "clone", "-q", "--bare", str(author), str(bare)], tmp_path, env)
    run(["git", "remote", "add", "origin", str(bare)], author, env)
    run(["git", "fetch", "-q", "origin"], author, env)
    run(["git", "branch", "-q", "--set-upstream-to=origin/main"], author, env)
    user = tmp_path / "user"
    run(["git", "clone", "-q", str(bare), str(user)], tmp_path, env)
    return bare, author, user


def publish_change(author: Path, env: dict) -> None:
    shutil.rmtree(author / "skills/beta")
    skill(author, "gamma")
    skill(author, "alpha", "changed body")
    (author / "VERSION").write_text("0.2.0\n")
    run(["git", "add", "-A"], author, env)
    run(["git", "commit", "-q", "-m", "add gamma, remove beta"], author, env)
    run(["git", "push", "-q", "origin", "main"], author, env)


def head(repo: Path, env: dict) -> str:
    return run(["git", "rev-parse", "HEAD"], repo, env).stdout.strip()


def test_update_dry_run_then_apply(tmp_path):
    env = environment(tmp_path / "home")
    _, author, user = make_upstream(tmp_path, env)
    run(["bash", "tools/install_user_skills.sh"], user, env)
    links = tmp_path / "home/.claude/skills"
    assert (links / "beta").is_symlink()
    publish_change(author, env)
    before = head(user, env)

    dry = run(["bash", "tools/update.sh", "--dry-run"], user, env)
    assert "skill added: gamma" in dry.stdout
    assert "skill removed: beta" in dry.stdout
    assert "skill changed: alpha" in dry.stdout
    assert "add gamma, remove beta" in dry.stdout
    assert head(user, env) == before
    assert not (links / "gamma").exists()

    done = run(["bash", "tools/update.sh"], user, env)
    assert head(user, env) == head(author, env)
    assert (links / "gamma").is_symlink() and (links / "gamma/SKILL.md").is_file()
    assert not (links / "beta").is_symlink()
    assert "removed stale link" in done.stdout
    assert "stub skill check" in done.stdout
    assert "WARNING: doctor is not ready" in done.stdout
    assert "version 0.2.0" in done.stdout

    again = run(["bash", "tools/update.sh"], user, env)
    assert "Already up to date." in again.stdout


def test_update_refuses_dirty_tree(tmp_path):
    env = environment(tmp_path / "home")
    _, author, user = make_upstream(tmp_path, env)
    publish_change(author, env)
    (user / "VERSION").write_text("local edit\n")
    before = head(user, env)
    done = run(["bash", "tools/update.sh"], user, env, check=False)
    assert done.returncode == 1
    assert "local changes" in done.stderr
    assert head(user, env) == before


def test_update_refuses_diverged_branch(tmp_path):
    env = environment(tmp_path / "home")
    _, author, user = make_upstream(tmp_path, env)
    publish_change(author, env)
    (user / "local.txt").write_text("x\n")
    run(["git", "add", "local.txt"], user, env)
    run(["git", "commit", "-q", "-m", "local"], user, env)
    done = run(["bash", "tools/update.sh"], user, env, check=False)
    assert done.returncode == 1
    assert "fast-forward" in done.stderr


def legacy_home(home: Path) -> Path:
    settings = home / ".claude/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    gate = {"type": "command", "command": "python3 /old/repo/tools/gate_hook.py", "timeout": 10}
    settings.write_text(
        json.dumps({"model": "keep-me", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [gate]}]}})
    )
    rules = home / ".codex/rules/re-agent-gates.rules"
    rules.parent.mkdir(parents=True)
    rules.write_text("# old rules\n")
    return settings


def test_update_removes_legacy_user_gates(tmp_path):
    env = environment(tmp_path / "home")
    _, author, user = make_upstream(tmp_path, env)
    settings = legacy_home(tmp_path / "home")
    publish_change(author, env)
    done = run(["bash", "tools/update.sh"], user, env)
    assert "REMOVED hook python3 /old/repo/tools/gate_hook.py" in done.stdout
    assert json.loads(settings.read_text()) == {"model": "keep-me"}
    assert not (tmp_path / "home/.codex/rules/re-agent-gates.rules").exists()


def test_prune_removes_only_dangling_links_into_this_repo(tmp_path):
    env = environment(tmp_path / "home")
    links = tmp_path / "home/.claude/skills"
    links.mkdir(parents=True)
    (links / "stale").symlink_to(ROOT / "skills/no-such-skill")
    (links / "foreign").symlink_to(tmp_path / "elsewhere/missing")
    (links / "plain").mkdir()
    (links / "file.txt").write_text("keep")
    dry = run(["bash", str(ROOT / "tools/install_user_skills.sh"), "--prune", "--dry-run"], tmp_path, env)
    assert "would remove stale link" in dry.stdout
    assert (links / "stale").is_symlink()
    assert not (links / "case-qc").exists()
    run(["bash", str(ROOT / "tools/install_user_skills.sh"), "--prune"], tmp_path, env)
    assert not (links / "stale").is_symlink()
    assert (links / "foreign").is_symlink()
    assert (links / "plain").is_dir() and (links / "file.txt").read_text() == "keep"
    assert (links / "case-qc/SKILL.md").is_file()
    assert (tmp_path / "home/.codex/skills/case-qc/SKILL.md").is_file()


def test_install_user_skills_no_longer_offers_gates(tmp_path):
    header = (ROOT / "tools/install_user_skills.sh").read_text().split("set -euo pipefail")[0]
    assert "--with-gates" not in header
    env = environment(tmp_path / "home")
    done = run(["bash", str(ROOT / "tools/install_user_skills.sh"), "--with-gates"], tmp_path, env)
    assert "approval gates were removed" in done.stdout
    assert "nothing to clean" in done.stdout
    assert not (tmp_path / "home/.claude/settings.json").exists()
    assert not (tmp_path / "home/.codex/rules").exists()


def test_install_dry_run_reports_legacy_gates_and_changes_nothing(tmp_path):
    env = environment(tmp_path / "home")
    settings = legacy_home(tmp_path / "home")
    before = settings.read_text()
    done = run(["bash", str(ROOT / "tools/install_user_skills.sh"), "--dry-run"], tmp_path, env)
    assert "WOULD REMOVE hook" in done.stdout
    assert "WOULD DELETE" in done.stdout
    assert settings.read_text() == before
    assert (tmp_path / "home/.codex/rules/re-agent-gates.rules").is_file()
    assert not settings.with_name("settings.json.bak").exists()


def test_install_removes_legacy_gates_once(tmp_path):
    env = environment(tmp_path / "home")
    settings = legacy_home(tmp_path / "home")
    run(["bash", str(ROOT / "tools/install_user_skills.sh")], tmp_path, env)
    assert json.loads(settings.read_text()) == {"model": "keep-me"}
    assert "gate_hook.py" in settings.with_name("settings.json.bak").read_text()
    again = run(["bash", str(ROOT / "tools/install_user_skills.sh")], tmp_path, env)
    assert "nothing to clean" in again.stdout
    assert not settings.with_name("settings.json.bak.1").exists()
