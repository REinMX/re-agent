#!/usr/bin/env python3
"""Run the semeio AHM_ANALYSIS workflow on a copy of an ERT storage. Never writes to the original.

AHM_ANALYSIS writes a new ensemble into the storage, and ERT 26 migrates an older storage in
place when it opens it. So this script works on copies only:

1. It loads the config with the ERT plugins (as a run does) to find ENSPATH, UPDATE_LOG_PATH and
   RUNPATH_FILE.
2. In a new work folder it mirrors the folder above the config folder (--mirror-up levels):
   the config folder is copied, the folders that hold a write path are made new, and the
   other entries are links (read-only use, so that <CONFIG_PATH>/../deck still resolves).
3. It copies ENSPATH into the work folder and comments out ENSPATH, UPDATE_LOG_PATH and
   RUNPATH_FILE in the copied config and its copied includes. It appends new values that
   point into the work folder, and a one-line workflow ``AHM_ANALYSIS ahm_target <prior>``.
   ``ert workflow --ensemble`` does not reach the job, so the prior is the second argument.
4. It loads the copied config again and stops (exit 2) if a write path is outside the work folder.
5. It runs ``<python> -m ert workflow AHM_ANALYSIS_AGENT <config copy>`` and copies the
   AhmAnalysisJob CSV files (ks.csv, misfit_obs_info.csv, active_obs_info.csv, prior.csv,
   <group>.csv) into --out, with run.json (wall time, ERT and semeio versions) and the ERT log.
6. It compares a listing (size and mtime) of the original storage, update log folder and
   config folder before and after the run. A change is an error (exit 2).

Usage:
    run_ahm_analysis.py --config ert/model/case.ert --prior iter-0 --out <new dir>

Then read the result against a noise floor with ahm_influence.py.
Exit code: 0 = done, 2 = input error, ERT or semeio missing, the workflow failed, or the
original changed.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINT = HERE.parents[1] / "ert-config-author-lint" / "scripts"
WORKFLOW = "AHM_ANALYSIS_AGENT"
TARGET = "ahm_target"
WRITE_KEYS = ("ENSPATH", "UPDATE_LOG_PATH", "RUNPATH_FILE")


class InputError(Exception):
    pass


def require_modules() -> None:
    for module, why in (
        ("ert", "the ERT CLI and config parser"),
        ("semeio", "the AHM_ANALYSIS workflow job"),
    ):
        if importlib.util.find_spec(module) is None:
            raise InputError(f"{module} is not installed in {sys.executable} ({why}). Use the Komodo python.")


def load_config(config: Path):
    """Parse a config with the ERT plugins, as a run does."""
    from ert.config import ErtConfig
    from ert.plugins import get_site_plugins

    try:
        with contextlib.chdir(config.parent):
            return ErtConfig.with_plugins(get_site_plugins()).from_file(config.name)
    except Exception as error:  # a broken config or a plugin that cannot load
        raise InputError(f"ERT cannot load {config}: {type(error).__name__}: {error}") from error


def write_paths(ert_config, config_dir: Path) -> dict[str, Path]:
    raw = {
        "ENSPATH": ert_config.ens_path,
        "UPDATE_LOG_PATH": ert_config.analysis_config.log_path,
        "RUNPATH_FILE": ert_config.runpath_file,
    }
    return {key: (config_dir / value).resolve() for key, value in raw.items()}


def inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """Size and mtime of each entry under folder. An absent folder gives an empty listing."""
    if not folder.exists():
        return {}
    listing = {".": (0, folder.stat().st_mtime_ns)}
    for root, dirs, files in os.walk(folder):
        for name in dirs + files:
            path = Path(root) / name
            info = path.lstat()
            listing[str(path.relative_to(folder))] = (info.st_size, info.st_mtime_ns)
    return listing


def changes(before: dict, after: dict) -> list[str]:
    keys = sorted(set(before) | set(after))
    return [k for k in keys if before.get(k) != after.get(k)]


def mirror(src: Path, dst: Path, config_dir: Path, writes: list[Path]) -> None:
    """Copy config_dir, make new folders for the ancestors of a write path, link the rest."""
    dst.mkdir(parents=True, exist_ok=True)
    skip = {w for w in writes if inside(w, config_dir)}

    def ignore(folder: str, names: list[str]) -> list[str]:
        return [n for n in names if (Path(folder) / n).resolve() in skip]

    for child in sorted(src.iterdir()):
        real = child.resolve()
        target = dst / child.name
        if real == config_dir:
            shutil.copytree(child, target, symlinks=True, ignore=ignore)
        elif any(inside(w, real) for w in writes):
            if any(w == real for w in writes):
                continue
            mirror(child, target, config_dir, writes)
        elif inside(config_dir, real):
            mirror(child, target, config_dir, writes)
        else:
            target.symlink_to(real, target_is_directory=real.is_dir())


def config_files(config: Path) -> list[Path]:
    """The config and the INCLUDE files that ert_config_map.py finds."""
    spec = importlib.util.spec_from_file_location("ert_config_map", LINT / "ert_config_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mapper = module.Mapper(config)
    mapper.parse(mapper.root)
    return [mapper.root, *(Path(i["target"]) for i in mapper.includes if i["status"] == "ok")]


def comment_out(file: Path, keys: tuple[str, ...]) -> int:
    lines = file.read_text(errors="replace").splitlines(keepends=True)
    count = 0
    for index, line in enumerate(lines):
        parts = line.split()
        if parts and parts[0] in keys:
            lines[index] = "-- run_ahm_analysis.py: " + line
            count += 1
    if count:
        file.write_text("".join(lines))
    return count


def rewrite(config_copy: Path, copy_root: Path, work: Path, workflow_file: Path) -> list[str]:
    """Point the write keys of the copy into work. Return the edited files."""
    edited = []
    for file in config_files(config_copy):
        if inside(file.resolve(), copy_root.resolve()) and comment_out(file, WRITE_KEYS):
            edited.append(str(file))
    with config_copy.open("a") as handle:
        handle.write(
            "\n-- run_ahm_analysis.py: write into the work folder only\n"
            f"ENSPATH {work / 'storage'}\n"
            f"UPDATE_LOG_PATH {work / 'update_log'}\n"
            f"RUNPATH_FILE {work / 'runpath_file'}\n"
            f"LOAD_WORKFLOW {workflow_file} {WORKFLOW}\n"
        )
    return edited


def version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def check_out(out: Path, protected: list[Path]) -> None:
    resolved = out.resolve()
    for folder in protected:
        if inside(resolved, folder):
            raise InputError(f"--out {out} is inside {folder}. Use a new folder outside the case")
    if out.exists() and any(out.iterdir()):
        raise InputError(f"--out {out} is not empty")


def run(args: argparse.Namespace) -> int:
    require_modules()
    config = Path(args.config).resolve()
    if not config.is_file():
        raise InputError(f"{config} not found")
    original = load_config(config)
    if "AHM_ANALYSIS" not in original.workflow_jobs:
        raise InputError("ERT has no AHM_ANALYSIS workflow job. Install semeio in this environment")
    writes = write_paths(original, config.parent)
    storage = writes["ENSPATH"]
    if not storage.is_dir():
        raise InputError(f"ENSPATH {storage} is not a folder. Run the history match first")
    config_dir = config.parent
    root = config_dir
    for _ in range(args.mirror_up):
        root = root.parent
    protected = [storage, writes["UPDATE_LOG_PATH"], config_dir]
    out = Path(args.out)
    check_out(out, protected)
    watched = [storage, writes["UPDATE_LOG_PATH"], config_dir]
    before = [snapshot(folder) for folder in watched]

    work = Path(tempfile.mkdtemp(prefix="ahm_analysis_", dir=args.work_dir))
    try:
        if inside(work.resolve(), root.resolve()):
            raise InputError(f"the work folder {work} is inside {root}. Give --work-dir outside it")
        copy_root = work / "case" / root.name
        mirror(root, copy_root, config_dir.resolve(), list(writes.values()))
        config_copy = copy_root / config_dir.relative_to(root) / config.name
        shutil.copytree(storage, work / "storage", symlinks=True)
        workflow_file = work / WORKFLOW
        group_arg = f" {args.group_by}" if args.group_by != "data_key" else ""
        workflow_file.write_text(f"AHM_ANALYSIS {TARGET} {args.prior}{group_arg}\n")
        edited = rewrite(config_copy, copy_root / config_dir.relative_to(root), work, workflow_file)
        copied = write_paths(load_config(config_copy), config_copy.parent)
        stray = [f"{k} {v}" for k, v in copied.items() if not inside(v, work.resolve())]
        if stray:
            raise InputError(
                "the copied config still writes outside the work folder: "
                + "; ".join(stray)
                + ". An include outside the config folder probably sets it"
            )

        command = [sys.executable, "-m", "ert", "workflow", WORKFLOW, config_copy.name]
        print("running:", " ".join(command), f"(in {config_copy.parent})", flush=True)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                cwd=config_copy.parent,
                capture_output=True,
                text=True,
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise InputError(f"ert workflow did not finish in {args.timeout} s") from error
        wall = time.monotonic() - start
        log = proc.stdout + proc.stderr
        results = work / "update_log" / "AhmAnalysisJob"
        if proc.returncode != 0 or not (results / "ks.csv").is_file():
            tail = "\n".join(log.splitlines()[-25:])
            raise InputError(
                f"AHM_ANALYSIS gave no ks.csv (exit {proc.returncode}). End of the ERT output:\n{tail}"
            )

        out.mkdir(parents=True, exist_ok=True)
        files = sorted(results.glob("*.csv"))
        for file in files:
            shutil.copy2(file, out / file.name)
        (out / "ert_workflow.log").write_text(log)
        after = [snapshot(folder) for folder in watched]
        changed = {
            str(f): changes(b, a) for f, b, a in zip(watched, before, after, strict=True) if changes(b, a)
        }
        receipt = {
            "config": str(config),
            "prior": args.prior,
            "group_by": args.group_by,
            "workflow": workflow_file.read_text().strip(),
            "command": command,
            "wall_time_s": round(wall, 1),
            "ert": version("ert"),
            "semeio": version("semeio"),
            "files": [f.name for f in files],
            "edited_copies": edited,
            "work_dir": str(work) if args.keep_copy else None,
            "original_unchanged": not changed,
            "original_changes": changed,
        }
        (out / "run.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if changed:
            print(
                f"ERROR: the original changed during the run: {json.dumps(changed)[:2000]}", file=sys.stderr
            )
            return 2
        print(
            f"AHM_ANALYSIS done in {wall:.1f} s (ERT {receipt['ert']}, semeio {receipt['semeio']}): "
            f"{len(files)} files in {out}. Next: ahm_influence.py --ahm-dir {out} --out <dir>"
        )
        return 0
    finally:
        if args.keep_copy:
            print(f"work copy kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", required=True, help="the ERT config of the history match")
    parser.add_argument("--prior", required=True, help="name of the prior ensemble, for example iter-0")
    parser.add_argument("--out", required=True, help="new, empty output folder outside the case")
    parser.add_argument(
        "--group-by",
        choices=("data_key", "obs_key"),
        default="data_key",
        help="data_key: one group per response key (default). obs_key: one group per observation (slow)",
    )
    parser.add_argument(
        "--mirror-up", type=int, default=1, help="folder levels above the config folder to mirror"
    )
    parser.add_argument(
        "--work-dir", help="parent of the temporary work folder (default: the system temp folder)"
    )
    parser.add_argument("--keep-copy", action="store_true", help="keep the work folder with the storage copy")
    parser.add_argument("--timeout", type=int, default=3600, help="seconds before the workflow is stopped")
    args = parser.parse_args(argv)
    if args.mirror_up < 1:
        parser.error("--mirror-up must be 1 or more")
    try:
        return run(args)
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
