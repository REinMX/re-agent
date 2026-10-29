"""Scripts read and write text as UTF-8, not in the platform encoding (cp1252 on Windows)."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPTS = [
    ROOT / "skills/everest-diagnostics/scripts/compare_plans.py",
    ROOT / "skills/everest-diagnostics/scripts/everest_diagnostics.py",
    ROOT / "skills/everest-config-author-lint/scripts/everest_lint.py",
    ROOT / "skills/history-match-qc/scripts/field_qc.py",
    ROOT / "skills/history-match-qc/scripts/prior_qc.py",
    ROOT / "skills/history-match-qc/scripts/update_log_report.py",
    ROOT / "skills/ert-diagnose/scripts/triage_runpath.py",
]
TEXT_CALLS = {"read_text", "write_text", "open"}


def default_encoding_calls(path: Path) -> list[str]:
    """read_text, write_text, and open calls in text mode without encoding=."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        # Path.read_text, Path.write_text, Path.open, and the builtin open. A local helper named read_text is not IO.
        method = isinstance(node.func, ast.Attribute) and node.func.attr in TEXT_CALLS
        if not (method or (isinstance(node.func, ast.Name) and node.func.id == "open")):
            continue
        if any(keyword.arg == "encoding" for keyword in node.keywords):
            continue
        modes = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        modes += [
            k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)
        ]
        if any("b" in mode for mode in modes):
            continue
        found.append(f"{path.name}:{node.lineno}")
    return found


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_text_io_names_utf8(path):
    assert default_encoding_calls(path) == []


def test_the_check_finds_a_default_encoding_write(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        'from pathlib import Path\nPath("x").write_text("y")\nopen("x", "rb")\nopen("x")\nread_text(Path("x"))\n',
        encoding="utf-8",
    )
    assert default_encoding_calls(bad) == ["bad.py:2", "bad.py:4"]


def test_compare_plans_runs_without_a_default_encoding_warning(tmp_path):
    pytest.importorskip("pandas")
    pytest.importorskip("yaml")
    plans = ROOT / "tests/data/everest/plans"
    out = tmp_path / "report.md"
    command = [
        sys.executable,
        "-X",
        "warn_default_encoding",
        "-W",
        "error::EncodingWarning:compare_plans",
        "-W",
        "error::EncodingWarning:everest_diagnostics",
        "-W",
        "error::EncodingWarning:everest_lint",
        str(ROOT / "skills/everest-diagnostics/scripts/compare_plans.py"),
        str(plans / "plan_a/output"),
        str(plans / "plan_b/output"),
        "--config-a",
        str(plans / "config.yml"),
        "--labels",
        "Å",
        "B",
        "--out",
        str(out),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EncodingWarning" not in result.stderr
    assert "- Plan Å: best batch 1" in out.read_bytes().decode("utf-8")
