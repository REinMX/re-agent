#!/usr/bin/env python3
"""Build a versioned source drop from git ls-files, with a SHA256 manifest. Standard library only."""

from __future__ import annotations

import argparse
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

if __package__:
    from .apply_drop import MANIFEST, Entry, manifest_text, protected, read_entry, safe_name, validate_entries
else:
    from apply_drop import MANIFEST, Entry, manifest_text, protected, read_entry, safe_name, validate_entries

ROOT = Path(__file__).resolve().parents[1]


def make_drop(source: Path, version: str, output: Path | None = None) -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("version must be a semantic version")
    source = source.resolve()
    tracked = subprocess.run(
        ["git", "-C", str(source), "ls-files", "--stage", "-z"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    entries = {}
    for record in tracked.split("\0"):
        if not record:
            continue
        fields, name = record.split("\t", 1)
        mode, _, stage = fields.split()
        safe_name(name)
        if stage != "0":
            raise ValueError("cannot package an unresolved merge")
        if protected(name) or name == MANIFEST:
            continue
        if mode == "160000":
            raise ValueError(f"submodules are not supported in drops: {name}")
        entry = read_entry(source / name)
        if mode == "120000" and not entry.link:
            entry = Entry(entry.data, stat.S_IFLNK | 0o777)
        entries[name] = entry
    entries[MANIFEST] = Entry(manifest_text({n: e.digest for n, e in entries.items()}).encode())
    validate_entries(entries)
    output = output or source / "dist" / f"reservoir-engineer-agent-{version}-drop.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never replace a previously built artifact silently.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as stream:
        for name, entry in sorted(entries.items()):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = entry.mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            stream.writestr(info, entry.data)
    print(f"Drop: {output} ({len(entries) - 1} tracked files plus {MANIFEST})")
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--version", help="Default: VERSION in the source checkout")
    parser.add_argument(
        "--output", type=Path, help="Default: dist/reservoir-engineer-agent-<version>-drop.zip"
    )
    args = parser.parse_args(argv)
    try:
        version = args.version or (args.source / "VERSION").read_text().strip()
        make_drop(args.source, version, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
