"""resinsight-session: install recipe and the live version check, with fake binaries.

Real ResInsight runs are in tests/test_resinsight_real.py (marker resinsight).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/resinsight-session/scripts"
DOCTOR = SCRIPTS / "ri_doctor.py"
INSTALL = SCRIPTS / "install_resinsight.sh"

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ResInsight work is Linux only")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _fakes(tmp_path: Path, ri: str = "2026.09.1", rips: str = "2026.9.1.2") -> tuple[Path, Path]:
    binary = _exe(
        tmp_path / "ri",
        f'echo "The current command line options in ResInsight are:"\necho "ResInsight v. {ri}"\n',
    )
    python = _exe(tmp_path / "py", f'echo "{rips}"\n')
    return binary, python


def _doctor(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DOCTOR), *args, "--json"], capture_output=True, text=True, check=False, cwd=ROOT
    )


def test_major_minor_ignores_patch_and_zero_padding():
    mod = _load("ri_doctor", DOCTOR)
    assert mod.major_minor("2026.09.1") == mod.major_minor("2026.9.1.2") == (2026, 9)
    with pytest.raises(ValueError):
        mod.major_minor("v2026")


def test_matching_versions_are_ready(tmp_path: Path):
    binary, python = _fakes(tmp_path)
    proc = _doctor("--bin", str(binary), "--python", str(python), "--headless", "none")
    report = json.loads(proc.stdout)
    assert proc.returncode == 0, proc.stdout
    assert report["resinsight_version"] == "2026.09.1" and report["rips_version"] == "2026.9.1.2"


def test_minor_mismatch_is_not_ready(tmp_path: Path):
    binary, python = _fakes(tmp_path, rips="2026.6.0.1")
    proc = _doctor("--bin", str(binary), "--python", str(python))
    assert proc.returncode == 1
    assert "major.minor must match" in json.loads(proc.stdout)["errors"][0]


def test_contract_version_must_match_binary(tmp_path: Path):
    mod = _load("ri_doctor", DOCTOR)
    binary, python = _fakes(tmp_path)
    report = mod.check(mod.Report(bin=str(binary), python=str(python), contract_version="2026.06"), 30)
    assert any("resinsight.version is 2026.06" in e for e in report.errors)
    report = mod.check(mod.Report(bin=str(binary), python=str(python), contract_version="2026.09"), 30)
    assert report.ready


def test_missing_binary_or_python_is_exit_2(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RESINSIGHT_EXECUTABLE", raising=False)
    monkeypatch.delenv("RIPS_PYTHON", raising=False)
    _, python = _fakes(tmp_path)
    proc = _doctor("--bin", str(tmp_path / "absent"), "--python", str(python))
    assert proc.returncode == 2
    assert "not an executable file" in proc.stdout


def test_binary_without_version_line_is_not_ready(tmp_path: Path):
    binary = _exe(tmp_path / "broken_ri", 'echo "Could not load the Qt platform plugin"\nexit 1\n')
    _, python = _fakes(tmp_path)
    proc = _doctor("--bin", str(binary), "--python", str(python))
    assert proc.returncode == 1
    assert "did not print its version" in proc.stdout


def test_hanging_binary_is_killed(tmp_path: Path):
    mod = _load("ri_doctor", DOCTOR)
    binary = _exe(tmp_path / "ri", "sleep 30\n")
    code, out = mod.run_bounded([str(binary)], timeout=0.5)
    assert code is None and "timeout" in out


def test_install_dry_run_pins_matching_versions(tmp_path: Path):
    proc = subprocess.run(
        ["bash", str(INSTALL), "--dry-run", str(tmp_path / "ri")], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert "rips==2026.9.1.2" in proc.stdout
    assert "v2026.09.1/ResInsight-Ubuntu_24.04_gcc-v2026.09.1.zip" in proc.stdout
    assert "libxcb-cursor0" in proc.stdout
    assert not (tmp_path / "ri").exists()


def test_install_refuses_mismatched_versions_and_relative_prefix(tmp_path: Path):
    bad = subprocess.run(
        ["bash", str(INSTALL), "--dry-run", str(tmp_path), "2026.09.1", "2026.6.0.1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 2 and "major.minor" in bad.stderr
    rel = subprocess.run(["bash", str(INSTALL), "--dry-run", "relative/dir"], capture_output=True, text=True)
    assert rel.returncode == 2 and "absolute" in rel.stderr
