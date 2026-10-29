"""Exercise the opt-in installer and real hook without contacting a remote."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(repo, *command, input=None, environment=None):
    # Strip inherited make state too: under `make check`, MAKEFLAGS carries PY=... into the nested `make hooks`.
    inherited = {"PYTHONPATH", "PYTHONHOME", "PY", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"}
    env = {key: value for key, value in os.environ.items() if key not in inherited}
    env.update(environment or {})
    return subprocess.run(
        command, cwd=repo, env=env, input=input, capture_output=True, text=True, timeout=30, check=False
    )


def git(repo, *args):
    result = run(repo, "git", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@pytest.fixture
def hook_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Hook Test")
    git(repo, "config", "user.email", "hook@example.invalid")
    git(repo, "config", "core.hooksPath", "/dev/null")
    (repo / "tools/hooks").mkdir(parents=True)
    for relative in ("Makefile", "tools/clean_check.sh", "tools/hooks/pre-push"):
        shutil.copy2(ROOT / relative, repo / relative)
    return repo


@pytest.mark.parametrize(
    "selection", ["implicit", "empty", "missing", "directory", "not-executable", "without-check-tools"]
)
def test_install_requires_explicit_executable(hook_repo, selection):
    repo = hook_repo
    candidate = repo / "selected python"
    if selection == "directory":
        candidate.mkdir()
    elif selection == "not-executable":
        candidate.write_text("not executable\n")
    elif selection == "without-check-tools":
        # A working Python without site-packages: it runs, but cannot import pytest or ruff.
        candidate.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n')
        candidate.chmod(0o755)
    args = [] if selection == "implicit" else [f"PY={'' if selection == 'empty' else candidate}"]
    result = run(repo, "make", "hooks", *args)
    assert result.returncode != 0, result.stdout + result.stderr
    assert git(repo, "config", "--local", "--get", "core.hooksPath") == "/dev/null"
    assert run(repo, "git", "config", "--local", "--get", "reservoirAgent.python").returncode == 1


def install(repo):
    result = run(repo, "make", "hooks", f"PY={sys.executable}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "config", "--local", "--get", "core.hooksPath") == "tools/hooks"
    assert git(repo, "config", "--local", "--get", "reservoirAgent.python") == sys.executable


def commit_probe(repo, exit_code):
    (repo / "Makefile").write_text('check-here:\n\t"$(PY)" probe.py\n')
    (repo / "probe.py").write_text(
        "import json, os, sys\n"
        "print('probe=' + json.dumps({'python': sys.executable, "
        "'PYTHONPATH': os.environ.get('PYTHONPATH'), 'PYTHONHOME': os.environ.get('PYTHONHOME')}))\n"
        f"raise SystemExit({exit_code})\n"
    )
    git(repo, "add", "Makefile", "probe.py", "tools")
    git(repo, "commit", "-q", "-m", f"Synthetic check result {exit_code}")
    return git(repo, "rev-parse", "HEAD")


def invoke_hook(repo, sha, **environment):
    return run(
        repo,
        "tools/hooks/pre-push",
        input=f"refs/heads/test {sha} refs/heads/test {'0' * 40}\n",
        environment=environment,
    )


def test_hook_checks_committed_failure_and_success_with_configured_python(hook_repo):
    repo = hook_repo
    install(repo)
    failing = commit_probe(repo, 17)
    passing = commit_probe(repo, 0)
    (repo / "probe.py").write_text("raise SystemExit(99)\n")
    polluted = {"PYTHONPATH": str(repo / "polluted"), "PYTHONHOME": str(repo / "wrong-home")}
    failed = invoke_hook(repo, failing, **polluted)
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert failing[:12] in failed.stdout and "probe=" in failed.stdout
    passed = invoke_hook(repo, passing, **polluted)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert passing[:12] in passed.stdout
    probe = next(
        line.removeprefix("probe=") for line in passed.stdout.splitlines() if line.startswith("probe=")
    )
    assert json.loads(probe) == {"python": sys.executable, "PYTHONPATH": None, "PYTHONHOME": None}


def test_hook_py_override_takes_precedence(hook_repo):
    repo = hook_repo
    install(repo)
    passing = commit_probe(repo, 0)
    git(repo, "config", "--local", "reservoirAgent.python", str(repo / "missing-python"))
    passed = invoke_hook(repo, passing, PY=sys.executable)
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert "probe=" in passed.stdout


def test_hook_without_interpreter_fails_closed(hook_repo):
    repo = hook_repo
    passing = commit_probe(repo, 0)
    result = invoke_hook(repo, passing)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "make hooks PY=" in result.stderr


def test_hook_ignores_deleted_refs(hook_repo):
    result = invoke_hook(hook_repo, "0" * 40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "clean check" not in result.stdout


def test_hook_puts_the_interpreter_bin_first_on_path(hook_repo):
    # ERT plugins find their programs on PATH. A push from a shell without the venv on PATH must still see them.
    repo = hook_repo
    venv_bin = repo / "venv bin"
    venv_bin.mkdir()
    (venv_bin / "python").symlink_to(sys.executable)
    (repo / "Makefile").write_text('check-here:\n\t"$(PY)" probe.py\n')
    (repo / "probe.py").write_text("import os\nprint('path0=' + os.environ['PATH'].split(os.pathsep)[0])\n")
    git(repo, "add", "Makefile", "probe.py", "tools")
    git(repo, "commit", "-q", "-m", "Synthetic PATH probe")
    sha = git(repo, "rev-parse", "HEAD")
    bare = os.pathsep.join(
        sorted({str(Path(str(shutil.which(name))).parent) for name in ("git", "make", "bash")})
    )
    result = invoke_hook(repo, sha, PY=str(venv_bin / "python"), PATH=bare)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"path0={venv_bin}" in result.stdout.splitlines(), result.stdout
    (repo / "bin").mkdir()
    (repo / "bin" / "probe-python").symlink_to(sys.executable)
    named = invoke_hook(repo, sha, PY="probe-python", PATH=os.pathsep.join([str(repo / "bin"), bare]))
    assert named.returncode == 0, named.stdout + named.stderr
    assert f"path0={repo / 'bin'}" in named.stdout.splitlines(), named.stdout
