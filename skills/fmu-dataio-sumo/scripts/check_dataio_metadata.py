#!/usr/bin/env python3
"""Check fmu-dataio metadata files for structural problems. Read-only.

This is a consistency check. It is not the schema validation of fmu-dataio.
The JSON schema that the metadata names in "$schema" stays the authority.

A metadata file sits next to its data file and has the data file name with a leading dot
and a trailing ".yml", for example ".top_volantis--depth.gri.yml".

Usage:
    check_dataio_metadata.py <folder> [--casepath <case folder>] [--require-fmu] [--no-files] [--json]

Checks for each file:
    required top-level keys, data.name, data.content, file.relative_path,
    fmu.case.uuid and fmu.case.name, the data file next to it, and its MD5 checksum.
    A missing 'fmu' block is a warning, or an error with --require-fmu.
Checks across files:
    one case uuid, one schema version, and no duplicate data identity.
    With --casepath, the case uuid must equal the one in share/metadata/fmu_case.yml.

Needs PyYAML (it is in Komodo and it is a dependency of fmu-dataio).
Exit code: 0 = no errors, 1 = errors found, 2 = usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
TOP_LEVEL = ("$schema", "class", "access", "masterdata", "source", "version")
DATA_LEVEL = ("data", "file")
PLACEHOLDER_UUID = "00000000-0000-0000-0000-000000000000"


def find_sidecars(folder: Path) -> list[Path]:
    # The glob pattern already requires the sidecar shape (leading dot, trailing ".yml");
    # an earlier extra "at least 3 dots" filter silently dropped sidecars for extensionless
    # data files (2 dots, e.g. ".EIGHTCELLS.yml" for a data file named "EIGHTCELLS"),
    # which is exactly the kind of silent skip this script's docstring promises not to do.
    return sorted(p for p in folder.rglob(".*.yml") if p.is_file())


def data_file_for(sidecar: Path) -> Path:
    return sidecar.with_name(sidecar.name[1:-4])


def md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def get(meta: dict, dotted: str):
    node = meta
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def check_one(sidecar: Path, check_files: bool, require_fmu: bool) -> tuple[dict | None, list[dict]]:
    problems: list[dict] = []

    def add(severity: str, message: str) -> None:
        problems.append({"file": str(sidecar), "severity": severity, "message": message})

    try:
        meta = yaml.safe_load(sidecar.read_text())
    except (yaml.YAMLError, OSError) as error:
        add("error", f"cannot parse: {error}")
        return None, problems
    if not isinstance(meta, dict):
        add("error", "metadata is not a mapping")
        return None, problems

    for key in TOP_LEVEL:
        if key not in meta:
            add("error", f"missing top-level key '{key}'")
    if meta.get("class") != "case":
        for key in DATA_LEVEL:
            if key not in meta:
                add("error", f"missing top-level key '{key}'")
        if not get(meta, "data.name"):
            add("error", "data.name is empty or missing")
        if not get(meta, "data.content"):
            add("error", "data.content is empty or missing")
        if not get(meta, "file.relative_path"):
            add("error", "file.relative_path is missing")
    if "fmu" not in meta:
        add(
            "error" if require_fmu else "warning",
            "no 'fmu' block. The export ran outside an FMU case, so Sumo cannot link it to a case",
        )
    else:
        uuid = get(meta, "fmu.case.uuid")
        if not uuid:
            add("error", "fmu.case.uuid is missing")
        elif not UUID_RE.match(str(uuid)):
            add("error", f"fmu.case.uuid is not a UUID: {uuid}")
        elif str(uuid) == PLACEHOLDER_UUID:
            add("warning", "fmu.case.uuid is the all-zero placeholder")
        if not get(meta, "fmu.case.name"):
            add("error", "fmu.case.name is missing")

    if check_files and meta.get("class") != "case":
        data_file = data_file_for(sidecar)
        if not data_file.is_file():
            add("error", f"data file not found next to the metadata: {data_file.name}")
        else:
            expected = get(meta, "file.checksum_md5")
            if expected and md5(data_file) != expected:
                add("error", f"MD5 of {data_file.name} differs from file.checksum_md5")
    return meta, problems


def identity(meta: dict) -> tuple:
    data = meta.get("data") or {}
    return (
        meta.get("class"),
        data.get("name"),
        data.get("tagname"),
        data.get("content"),
        json.dumps(data.get("time"), default=str, sort_keys=True),
        get(meta, "fmu.realization.id"),
        get(meta, "fmu.iteration.id"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("folder", type=Path)
    parser.add_argument("--casepath", type=Path, default=None)
    parser.add_argument(
        "--require-fmu",
        action="store_true",
        help="Treat a missing 'fmu' block as an error. Use it for output of an ERT run.",
    )
    parser.add_argument("--no-files", action="store_true", help="Skip the data file and checksum checks")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.folder.is_dir():
        print(f"ERROR: not a folder: {args.folder}", file=sys.stderr)
        return 2
    sidecars = find_sidecars(args.folder)
    if not sidecars:
        print(f"ERROR: no metadata files found under {args.folder}", file=sys.stderr)
        return 2

    problems: list[dict] = []
    metas: list[tuple[Path, dict]] = []
    for sidecar in sidecars:
        meta, found = check_one(sidecar, check_files=not args.no_files, require_fmu=args.require_fmu)
        problems += found
        if meta is not None:
            metas.append((sidecar, meta))

    uuids = {str(get(m, "fmu.case.uuid")) for _, m in metas if get(m, "fmu.case.uuid")}
    if len(uuids) > 1:
        problems.append(
            {
                "file": str(args.folder),
                "severity": "error",
                "message": f"more than one case uuid: {sorted(uuids)}",
            }
        )
    schemas = {str(m.get("$schema")) for _, m in metas if m.get("$schema")}
    if len(schemas) > 1:
        problems.append(
            {
                "file": str(args.folder),
                "severity": "warning",
                "message": f"more than one schema: {sorted(schemas)}",
            }
        )
    counts = Counter(identity(m) for _, m in metas if m.get("class") != "case")
    for ident, count in counts.items():
        if count > 1:
            problems.append(
                {
                    "file": str(args.folder),
                    "severity": "warning",
                    "message": f"{count} files share one data identity {ident[:4]}",
                }
            )
    if args.casepath is not None:
        case_file = args.casepath / "share/metadata/fmu_case.yml"
        if not case_file.is_file():
            problems.append(
                {"file": str(case_file), "severity": "error", "message": "case metadata file not found"}
            )
        else:
            case_uuid = get(yaml.safe_load(case_file.read_text()) or {}, "fmu.case.uuid")
            if uuids and uuids != {str(case_uuid)}:
                problems.append(
                    {
                        "file": str(case_file),
                        "severity": "error",
                        "message": f"case uuid {case_uuid} differs from data uuid {sorted(uuids)}",
                    }
                )

    errors = [p for p in problems if p["severity"] == "error"]
    if args.json:
        print(json.dumps({"files": len(sidecars), "problems": problems}, indent=2))
    else:
        for p in problems:
            print(f"{p['severity']:<8} {p['file']}: {p['message']}")
        print(f"{len(sidecars)} metadata files, {len(errors)} errors, {len(problems) - len(errors)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
