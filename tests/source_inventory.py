"""The repository's own file list, from Git in a checkout or from EXPORT_MANIFEST.json in an export."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

MANIFEST = "EXPORT_MANIFEST.json"


def source_files(root: Path) -> list[str]:
    """Git-listed files in a checkout. In an extracted export, the manifest files after a hash check."""
    if (root / ".git").exists():
        assert shutil.which("git"), "a Git checkout needs git on PATH for this check"
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
        ).stdout.decode()
        return sorted(set(filter(None, listed.split("\0"))))
    manifest = root / MANIFEST
    assert manifest.is_file(), f"{root} is neither a Git checkout nor an extracted export with {MANIFEST}"
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    changed = [
        name
        for name, item in files.items()
        if not (root / name).is_file()
        or hashlib.sha256((root / name).read_bytes()).hexdigest() != item["sha256"]
    ]
    assert changed == [], f"files differ from {MANIFEST}; put the export under Git first: {changed[:5]}"
    return sorted(files)
