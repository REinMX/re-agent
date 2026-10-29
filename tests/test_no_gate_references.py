"""The approval gates were removed. Fail if their names come back in a non-history file."""

import re
from pathlib import Path

from source_inventory import source_files

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"gate_hook|gates\.rules|gate_check|approving the exact command", re.I)
HISTORY = ("docs/REVIEW_", "docs/plans/", "CHANGELOG.md", "evals/results/")
CLEANUP = {
    "tools/remove_legacy_gates.py",
    "tests/test_remove_legacy_gates.py",
    "tests/test_update.py",
    "tests/test_deployment.py",
    "tests/test_mcp_server.py",
    "tests/test_no_gate_references.py",
}


def test_no_gate_references_outside_history_and_cleanup():
    hits = []
    for name in source_files(ROOT):
        if name.startswith(HISTORY) or name in CLEANUP:
            continue
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits += [f"{name}:{n}" for n, line in enumerate(text.splitlines(), 1) if PATTERN.search(line)]
    assert hits == []
