"""The read envelope allows one named script. It never executes the command."""

import importlib.util
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools/read_envelope.py"
spec = importlib.util.spec_from_file_location("read_envelope", SCRIPT)
env = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["read_envelope"] = env
spec.loader.exec_module(env)

CASE_QC = ROOT / "skills/case-qc/scripts/case_qc.py"
TODAY = date(2026, 10, 8)
WRONG_SCRIPT = "command is not the case-qc script"


def sh(path: Path | str) -> str:
    """A path as a bash command needs it. bash drops unquoted backslashes, and so does the checker."""
    return shlex.quote(str(path))


def grant_file(tmp_path: Path, root: Path, output: Path, **overrides: str) -> Path:
    fields = {
        "owner": "repo-owner",
        "expires": "2099-01-01",
        "output_root": str(output),
        "script": "case-qc",
    }
    fields.update(overrides)
    lines = [f"{key}: {value}" for key, value in fields.items() if key != "roots"]
    for item in overrides.get("roots", [root]):
        lines.append(f"root: {item}")
    if "roots" not in overrides:
        lines.append(f"root: {root}")
    path = tmp_path / "read-envelope.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def layout(tmp_path: Path):
    root = tmp_path / "runs"
    root.mkdir()
    case = root / "case.ert"
    case.write_text("NUM_REALIZATIONS 1\n")
    output = tmp_path / "reports"
    output.mkdir()
    return root, case, output


def classify(command: str, grant: Path, cwd: Path):
    return env.classify(command, grant_path=grant, today=TODAY, cwd=cwd)


@pytest.mark.parametrize(
    "write",
    [sh, lambda path: f"'{path}'", lambda path: Path(path).as_posix()],
    ids=["shlex-quote", "single-quoted", "slashes"],
)
def test_allows_case_qc_under_named_roots(tmp_path: Path, write):
    root, case, output = layout(tmp_path)
    grant = grant_file(tmp_path, root, output)
    command = f"python3 {write(CASE_QC)} --outdir {write(output / 'r1')} --config {write(case)}"
    decision = classify(command, grant, tmp_path)
    assert decision.allowed
    assert decision.reasons == ()


def test_unquoted_backslash_path_asks(tmp_path: Path):
    # bash runs `python3 D:\a\case_qc.py` as `python3 D:acase_qc.py`. The checker must read
    # the command the same way, so it never allows a command bash would run differently.
    root, case, output = layout(tmp_path)
    grant = grant_file(tmp_path, root, output)
    script = str(CASE_QC).replace("/", "\\")
    command = f"python3 {script} --outdir {sh(output / 'r1')} --config {sh(case)}"
    decision = classify(command, grant, tmp_path)
    assert not decision.allowed
    assert decision.reasons == (WRONG_SCRIPT,)


@pytest.mark.parametrize(
    "command",
    [
        'python3 -c \'import subprocess; subprocess.run(["ert", "test_run", "x.ert"])\'',
        "python3 -m ert test_run x.ert",
        "python3 -u -m ert test_run x.ert",
        "python3 -c 'print(1)'",
    ],
)
def test_python_that_is_not_the_named_script_asks(tmp_path: Path, command: str):
    root, _, output = layout(tmp_path)
    decision = classify(command, grant_file(tmp_path, root, output), tmp_path)
    assert not decision.allowed


def test_command_outside_the_grant_asks(tmp_path: Path):
    root, case, output = layout(tmp_path)
    outside = tmp_path / "other.ert"
    outside.write_text("NUM_REALIZATIONS 1\n")
    grant = grant_file(tmp_path, root, output)
    command = f"python3 {sh(CASE_QC)} --outdir {sh(output / 'r1')} --config {sh(outside)}"
    assert classify(command, grant, tmp_path).reasons == ("an input path is outside the grant",)


def test_outdir_outside_output_root_asks(tmp_path: Path):
    root, case, output = layout(tmp_path)
    grant = grant_file(tmp_path, root, output)
    command = f"python3 {sh(CASE_QC)} --outdir {sh(tmp_path / 'elsewhere')} --config {sh(case)}"
    assert classify(command, grant, tmp_path).reasons == ("--outdir is outside output_root",)


def test_outdir_overlapping_input_root_asks(tmp_path: Path):
    root, case, output = layout(tmp_path)
    grant = grant_file(tmp_path, root, output)
    command = f"python3 {sh(CASE_QC)} --outdir {sh(root / 'report')} --config {sh(case)}"
    assert "--outdir overlaps an input root" in classify(command, grant, tmp_path).reasons


def test_expired_grant_asks(tmp_path: Path):
    root, case, output = layout(tmp_path)
    grant = grant_file(tmp_path, root, output, expires="2020-01-01")
    command = f"python3 {sh(CASE_QC)} --outdir {sh(output / 'r1')} --config {sh(case)}"
    assert classify(command, grant, tmp_path).reasons == ("grant is expired",)


def test_missing_grant_asks(tmp_path: Path):
    decision = env.classify(
        "python3 --version", grant_path=tmp_path / "missing.txt", today=TODAY, cwd=tmp_path
    )
    assert not decision.allowed
    assert any("grant" in reason for reason in decision.reasons)


def test_unknown_flag_asks(tmp_path: Path):
    root, case, output = layout(tmp_path)
    grant = grant_file(tmp_path, root, output)
    command = f"python3 {sh(CASE_QC)} --outdir {sh(output / 'r1')} --config {sh(case)} --sumo upload"
    assert classify(command, grant, tmp_path).reasons == ("unknown flag --sumo",)


def test_shell_join_asks(tmp_path: Path):
    root, case, output = layout(tmp_path)
    grant = grant_file(tmp_path, root, output)
    command = f"python3 {sh(CASE_QC)} --outdir {sh(output / 'r1')} --config {sh(case)}; ert test_run x.ert"
    assert classify(command, grant, tmp_path).reasons == ("command is more than one simple command",)


def test_glob_outside_root_asks(tmp_path: Path):
    # On Windows the glob starts with a drive letter and asks as "relative". Either way it asks
    # for a glob reason, not because the script path was misread.
    root, _, output = layout(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "run").mkdir()
    grant = grant_file(tmp_path, root, output)
    command = f"python3 {sh(CASE_QC)} --outdir {sh(output / 'r1')} --runpaths {sh(f'{other}/*')}"
    reasons = classify(command, grant, tmp_path).reasons
    assert len(reasons) == 1 and reasons[0].startswith("a glob")


def test_symlink_outside_root_asks(tmp_path: Path):
    root, _, output = layout(tmp_path)
    outside = tmp_path / "secret.ert"
    outside.write_text("NUM_REALIZATIONS 1\n")
    link = root / "linked.ert"
    link.symlink_to(outside)
    grant = grant_file(tmp_path, root, output)
    command = f"python3 {sh(CASE_QC)} --outdir {sh(output / 'r1')} --config {sh(link)}"
    assert classify(command, grant, tmp_path).reasons == ("an input path is outside the grant",)


def test_cli_exits_0_only_for_an_allowed_command(tmp_path: Path):
    root, case, output = layout(tmp_path)
    grant = grant_file(tmp_path, root, output)
    allowed = f"python3 {sh(CASE_QC)} --outdir {sh(output / 'r1')} --config {sh(case)}"
    good = subprocess.run(
        [sys.executable, str(SCRIPT), "--grant", str(grant), "--today", "2026-10-08", "--command", allowed],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert good.returncode == 0, good.stderr
    assert "allow" in good.stdout
    bad = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--grant",
            str(grant),
            "--today",
            "2026-10-08",
            "--command",
            "python3 -c 'print(1)'",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 2
