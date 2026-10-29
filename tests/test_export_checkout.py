"""Authoring-checkout assertions, intentionally absent from the reservoir-only ZIP."""

import json
import subprocess
import sys
import zipfile

from test_export_source import ROOT, git, installer_reads, load


def extract_committed(tmp_path):
    tool = load()
    files, manifest = tool.snapshot(ROOT, "HEAD")
    output = tmp_path / "committed.zip"
    output.write_bytes(tool.make_zip(files, manifest))
    with zipfile.ZipFile(output) as archive:
        archive.extractall(tmp_path / "extracted")
    return tmp_path / "extracted" / tool.PREFIX, manifest


def test_installer_reads_only_committed_files():
    reads = installer_reads(ROOT)
    assert {"tools/install_project.py", "tools/remove_legacy_gates.py"} <= set(reads)
    tracked = set(git(ROOT, "ls-files", "-z").split("\0"))
    assert [name for name in reads if name not in tracked] == []


def test_committed_zip_has_executable_reservoir_validation(tmp_path):
    extracted, manifest = extract_committed(tmp_path)
    assert not (extracted / ".git").exists()
    planned = subprocess.run(
        ["make", "-n", "check-here", f"PY={sys.executable}"],
        cwd=extracted,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    for command in planned.stdout.splitlines():
        if "tools/build_agent_surfaces.py" in command:
            result = subprocess.run(
                [sys.executable, "tools/build_agent_surfaces.py", "--check"],
                cwd=extracted,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
    for required in (
        "ruff check",
        "ruff format",
        "tools/check_skills.py",
        "tools/check_shared_helpers.py",
        "pytest",
    ):
        assert required in planned.stdout
    assert "tools/build_agent_surfaces.py" not in planned.stdout
    assert "plugins/pstack" not in (extracted / "AGENTS.md").read_text()
    assert "pstack:poteto-mode" not in (extracted / "AGENTS.md").read_text()
    for name in (
        "tests/test_agent_surfaces.py",
        "tests/test_pstack_models.py",
        "tests/test_export_checkout.py",
    ):
        assert not (extracted / name).exists()
    assert "tests/test_material_balance.py" in manifest["files"]
    assert "tests/test_deployment.py" in manifest["files"]
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=extracted,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert collected.returncode == 0, collected.stdout[-2000:] + collected.stderr[-2000:]


def test_committed_zip_runs_its_git_checks_without_the_source_checkout(tmp_path):
    extracted, _ = extract_committed(tmp_path)
    checks = [
        "tests/test_no_gate_references.py",
        "tests/test_worklog.py::test_default_log_is_ignored_in_this_repository",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", *checks],
        cwd=extracted,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout
    assert "skipped" not in result.stdout, result.stdout


def test_checkout_generator_refuses_missing_vendor(tmp_path):
    import pytest

    from tools.build_agent_surfaces import surfaces

    with pytest.raises(ValueError, match="No vendored skills"):
        surfaces(tmp_path)


def test_export_succeeds_on_real_repo_checkout(tmp_path, capsys):
    """Export the real repository commit; pstack skill links must be omitted, not fatal."""
    tool = load()
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    pstack_links = [
        name.decode("utf-8") for name in tracked if name and name.decode("utf-8").startswith("skills/pstack-")
    ]
    assert pstack_links, "fixture expects vendored skills/pstack-* links on the real checkout"
    output = tmp_path / "real-repo-export.zip"
    assert tool.main(["--source", str(ROOT), "--ref", "HEAD", "--output", str(output), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["skipped_tracked_symlink_count"] == len(pstack_links)
    assert set(report["skipped_tracked_symlinks"]) == set(pstack_links)
    with zipfile.ZipFile(output) as archive:
        names = {name[len(tool.PREFIX) :] for name in archive.namelist() if name.startswith(tool.PREFIX)}
        assert not any(name.startswith("skills/pstack-") for name in names)
        assert not any(name.startswith("plugins/pstack/") for name in names)
        manifest = json.loads(archive.read(tool.PREFIX + "EXPORT_MANIFEST.json"))
        assert set(manifest["skipped_tracked_symlinks"]) == set(pstack_links)
        assert all(name not in manifest["files"] for name in pstack_links)


def test_every_tracked_path_is_delivered_private_or_declared():
    """A new tracked file must be delivered, private, or in an omitted component of the manifest."""
    tool = load()
    files, manifest = tool.snapshot(ROOT, "HEAD")
    omitted = {
        path
        for component in manifest["distribution"]["omitted_components"].values()
        for path in component["paths"]
    }
    tracked = [name for name in git(ROOT, "ls-files", "-z").split("\0") if name]
    undeclared = sorted(
        name for name in tracked if name not in files and name not in omitted and not tool.private(name)
    )
    assert undeclared == []
