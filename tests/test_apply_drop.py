"""Exercise the updater on disposable folders and ZIPs, including user-owned files."""

import hashlib
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools import apply_drop as drop
from tools.make_drop import make_drop

ROOT = Path(__file__).resolve().parents[1]


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, newline="")
    return path


def record(root, *names):
    entries = {name: drop.read_entry(root / name).digest for name in names}
    write(root, drop.MANIFEST, drop.manifest_text(entries))


@pytest.fixture
def folders(tmp_path):
    source, dest = tmp_path / "source", tmp_path / "installed"
    source.mkdir()
    dest.mkdir()
    write(source, "tools/helper.py", "new\n")
    write(source, "new.txt", "added\n")
    write(dest, "tools/helper.py", "old\n")
    write(dest, "old.txt", "removed\n")
    record(dest, "tools/helper.py", "old.txt")
    return source, dest


def run(source, dest, *args):
    return drop.main(["--src", str(source), "--dest", str(dest), *args])


def snapshot(root):
    return {n: drop.read_entry(p) for n, p in drop.walk_files(root)}


def test_dry_run_changes_nothing(folders, capsys):
    source, dest = folders
    before = snapshot(dest)
    assert run(source, dest) == 0
    assert snapshot(dest) == before
    assert not list(dest.parent.glob("installed.bak-*.zip"))
    text = capsys.readouterr().out
    assert "COPY tools/helper.py" in text and "REMOVED (kept; use --prune) old.txt" in text
    assert "DRY RUN" in text


def test_apply_preserves_site_ignored_and_user_files(folders):
    source, dest = folders
    write(dest, ".gitignore", "private/\n*.secret\n")
    for name in (
        "site.contract.yml",
        ".git/config",
        ".venv/settings",
        "notes.local.md",
        "private/data",
        "a.secret",
    ):
        write(source, name, "replacement")
        write(dest, name, "user")
    write(dest, "user.txt", "untracked user file")
    assert run(source, dest, "--apply") == 0
    assert (dest / "tools/helper.py").read_text() == "new\n"
    assert (dest / "new.txt").read_text() == "added\n"
    assert (dest / "old.txt").read_text() == "removed\n"
    for name in (
        "site.contract.yml",
        ".git/config",
        ".venv/settings",
        "notes.local.md",
        "private/data",
        "a.secret",
    ):
        assert (dest / name).read_text() == "user"
    assert (dest / "user.txt").read_text() == "untracked user file"


def test_backup_contains_pre_update_state(folders):
    source, dest = folders
    write(dest, "site.contract.yml", "local contract")
    assert run(source, dest, "--apply") == 0
    backups = list(dest.parent.glob("installed.bak-*.zip"))
    assert len(backups) == 1
    with zipfile.ZipFile(backups[0]) as archive:
        assert archive.read("tools/helper.py") == b"old\n"
        assert archive.read("old.txt") == b"removed\n"
        assert archive.read("site.contract.yml") == b"local contract"
        assert "new.txt" not in archive.namelist()


def test_prune_requires_flag_and_remembers_retained_files(folders):
    source, dest = folders
    assert run(source, dest, "--apply") == 0
    assert (dest / "old.txt").exists()
    assert "old.txt" in drop.parse_manifest((dest / drop.MANIFEST).read_bytes())
    assert run(source, dest, "--dry-run", "--prune") == 0
    assert (dest / "old.txt").exists()
    assert run(source, dest, "--apply", "--prune") == 0
    assert not (dest / "old.txt").exists()
    assert "old.txt" not in drop.parse_manifest((dest / drop.MANIFEST).read_bytes())


def test_local_edits_and_new_path_collisions_survive(folders):
    source, dest = folders
    write(dest, "tools/helper.py", "local modification")
    write(dest, "old.txt", "keep local modification")
    write(dest, "new.txt", "user owned collision")
    assert run(source, dest, "--apply", "--prune") == 0
    assert (dest / "tools/helper.py").read_text() == "local modification"
    assert (dest / "old.txt").read_text() == "keep local modification"
    assert (dest / "new.txt").read_text() == "user owned collision"


def test_nested_gitignore_preserves_new_files(folders):
    source, dest = folders
    write(dest, "area/.gitignore", "/private*\n")
    write(source, "area/private.txt", "drop")
    write(dest, "area/private.txt", "user")
    assert run(source, dest, "--apply") == 0
    assert (dest / "area/private.txt").read_text() == "user"


def test_recursive_ignore_patterns_also_protect_root_files(folders):
    source, dest = folders
    write(dest, ".gitignore", "**/secret.txt\n")
    write(source, "secret.txt", "drop")
    write(dest, "secret.txt", "user")
    assert run(source, dest, "--apply") == 0
    assert (dest / "secret.txt").read_text() == "user"


def test_single_directory_zip_keeps_its_directory(folders, tmp_path):
    _, dest = folders
    archive = tmp_path / "directory.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("tools/new.py", "new\n")
    assert drop.main(["--zip", str(archive), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "tools/new.py").read_text() == "new\n"
    assert not (dest / "new.py").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink support")
def test_symlink_ancestor_cannot_write_outside_destination(folders, tmp_path):
    source, dest = folders
    outside = tmp_path / "outside"
    outside.mkdir()
    (dest / "area").symlink_to(outside, target_is_directory=True)
    write(source, "area/unsafe.txt", "drop")
    assert run(source, dest, "--apply") == 0
    assert not (outside / "unsafe.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink support")
def test_prune_links_is_opt_in_and_scoped(folders, tmp_path, monkeypatch):
    source, dest = folders
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for harness in (".claude", ".codex"):
        directory = home / harness / "skills"
        directory.mkdir(parents=True)
        (directory / "old").symlink_to(dest / "skills/absent")
        (directory / "plugin").symlink_to(dest / "plugins/absent")
        (directory / "foreign").symlink_to(tmp_path / "unrelated/absent")
        (directory / "live").symlink_to(dest / "tools")
    assert run(source, dest, "--apply") == 0
    assert (home / ".claude/skills/old").is_symlink()
    assert run(source, dest, "--prune-links") == 0
    assert (home / ".claude/skills/old").is_symlink()
    assert run(source, dest, "--apply", "--prune-links") == 0
    for harness in (".claude", ".codex"):
        directory = home / harness / "skills"
        assert not (directory / "old").is_symlink()
        assert not (directory / "plugin").is_symlink()
        assert (directory / "foreign").is_symlink()
        assert (directory / "live").is_symlink()


def test_zip_wrapper_and_manifest_integrity(folders, tmp_path):
    source, dest = folders
    archive = tmp_path / "download.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("reservoir-engineer-agent-main/VERSION", "0.3.0\n")
        stream.writestr("reservoir-engineer-agent-main/new.txt", "added\n")
    assert drop.main(["--zip", str(archive), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "VERSION").read_text() == "0.3.0\n"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("a.txt", "actual")
        stream.writestr(drop.MANIFEST, "0" * 64 + "  a.txt\n")
    before = snapshot(dest)
    assert drop.main(["--zip", str(archive), "--dest", str(dest), "--apply"]) == 2
    assert snapshot(dest) == before


@pytest.mark.parametrize("name", ["../outside", "/absolute", "root/../outside", "C:/outside"])
def test_zip_traversal_fails_before_backup(folders, tmp_path, name):
    _, dest = folders
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(name, "bad")
    assert drop.main(["--zip", str(archive), "--dest", str(dest), "--apply"]) == 2
    assert not list(dest.parent.glob("installed.bak-*.zip"))


def test_zip_unsafe_symlink_is_refused(folders, tmp_path):
    _, dest = folders
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        stream.writestr(info, "../outside")
    assert drop.main(["--zip", str(archive), "--dest", str(dest), "--apply"]) == 2


def test_cli_works_without_git_or_third_party_packages(folders):
    source, dest = folders
    command = [
        sys.executable,
        "-I",
        str(ROOT / "tools/apply_drop.py"),
        "--src",
        str(source),
        "--dest",
        str(dest),
    ]
    result = subprocess.run(command, env={**os.environ, "PATH": ""}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert (dest / "tools/helper.py").read_text() == "old\n"


def test_make_drop_uses_tracked_files_and_round_trips(tmp_path):
    source, dest = tmp_path / "checkout", tmp_path / "install"
    source.mkdir()
    dest.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    write(source, "VERSION", "0.3.0\n")
    write(source, "tracked.txt", "source\n")
    write(source, "user.txt", "not tracked")
    write(source, "site.contract.yml", "private")
    subprocess.run(
        ["git", "-C", str(source), "add", "VERSION", "tracked.txt", "site.contract.yml"], check=True
    )
    archive = make_drop(source, "0.3.0")
    with zipfile.ZipFile(archive) as stream:
        assert set(stream.namelist()) == {"VERSION", "tracked.txt", drop.MANIFEST}
        manifest = drop.parse_manifest(stream.read(drop.MANIFEST))
        assert manifest["tracked.txt"] == hashlib.sha256(b"source\n").hexdigest()
    assert drop.main(["--zip", str(archive), "--dest", str(dest), "--apply"]) == 0
    assert (dest / "tracked.txt").read_text() == "source\n"
    assert not (dest / "site.contract.yml").exists()
    backups = list(tmp_path.glob("install.bak-*.zip"))
    assert drop.main(["--zip", str(archive), "--dest", str(dest), "--apply"]) == 0
    assert list(tmp_path.glob("install.bak-*.zip")) == backups
