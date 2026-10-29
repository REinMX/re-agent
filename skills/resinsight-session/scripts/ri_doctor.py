#!/usr/bin/env python3
"""Live ResInsight check: the binary runs, rips imports, and their major.minor versions match.

tools/doctor.py checks the contract text only. This script starts the binary once
(`--console --help`, no display, no gRPC server) and the rips Python once.
Standard library only, so any Python can run it.

Usage:
  ri_doctor.py [--contract site.contract.yml] [--profile NAME] [--bin PATH] [--python PATH] [--json]

Exit: 0 ready, 1 not ready (version mismatch, missing xvfb-run), 2 missing input (no binary, no rips python).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402

VERSION_RE = re.compile(r"ResInsight v\. *(\d+\.\d+(?:\.\d+)*)")
RIPS_PROBE = (
    "import importlib.metadata as m\n"
    "try:\n    print(m.version('rips'))\n"
    "except m.PackageNotFoundError:\n    import rips; print(rips.__version__)\n"
)


@dataclass
class Report:
    bin: str | None = None
    python: str | None = None
    headless: str | None = None
    contract_version: str | None = None
    resinsight_version: str | None = None
    rips_version: str | None = None
    errors: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.errors and not self.missing


def major_minor(version: str) -> tuple[int, int]:
    """2026.09.1 and 2026.9.1.2 both give (2026, 9). rips compares only these two parts."""
    parts = version.strip().split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(f"not a version: {version!r}")
    return int(parts[0]), int(parts[1])


def run_bounded(cmd: list[str], timeout: float) -> tuple[int | None, str]:
    """Run in its own process group; kill the whole group on timeout (ResInsight may not exit)."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as error:
        return None, str(error)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        return None, (out or "") + f"\n(timeout after {timeout} s)"


def binary_version(binary: str, timeout: float = 60.0) -> tuple[str | None, str]:
    _, out = run_bounded([binary, "--console", "--help"], timeout)
    found = VERSION_RE.search(out or "")
    return (found.group(1) if found else None), out


def rips_version(python: str, timeout: float = 60.0) -> tuple[str | None, str]:
    code, out = run_bounded([python, "-c", RIPS_PROBE], timeout)
    lines = [line for line in (out or "").splitlines() if line.strip()]
    if code == 0 and lines and re.fullmatch(r"\d+(\.\d+)+", lines[-1].strip()):
        return lines[-1].strip(), out
    return None, out


def resolve(args) -> tuple[Report, dict]:
    block: dict = {}
    if args.contract or not (args.bin and args.python):
        try:
            contract = contract_lib.load_contract(args.contract, args.profile, require_project=False)
            block = dict(contract.get("resinsight") or {})
        except contract_lib.ContractError as error:
            if args.contract:
                raise
            block = {"_contract_error": str(error)}
    # Order: flag, contract profile, then the environment (RESINSIGHT_EXECUTABLE is the rips convention).
    report = Report(
        bin=args.bin or block.get("bin") or os.environ.get("RESINSIGHT_EXECUTABLE") or None,
        python=args.python or block.get("python") or os.environ.get("RIPS_PYTHON") or None,
        headless=args.headless or block.get("headless") or None,
        contract_version=str(block.get("version") or "") or None,
    )
    return report, block


def check(report: Report, timeout: float) -> Report:
    if not report.bin:
        report.missing.append("resinsight.bin: not set (contract or --bin)")
    elif not (os.path.isfile(report.bin) and os.access(report.bin, os.X_OK)):
        report.missing.append(f"resinsight.bin: not an executable file: {report.bin}")
    if not report.python:
        report.missing.append("resinsight.python: not set (contract or --python); the view layer needs rips")
    elif not (os.path.isfile(report.python) and os.access(report.python, os.X_OK)):
        report.missing.append(f"resinsight.python: not an executable file: {report.python}")
    if report.missing:
        return report
    report.resinsight_version, out = binary_version(report.bin, timeout)
    if not report.resinsight_version:
        report.errors.append(f"ResInsight did not print its version. Output tail: {out.strip()[-300:]!r}")
    report.rips_version, out = rips_version(report.python, timeout)
    if not report.rips_version:
        report.errors.append(
            f"rips does not import with {report.python}. Output tail: {out.strip()[-300:]!r}"
        )
    if report.resinsight_version and report.rips_version:
        if major_minor(report.resinsight_version) != major_minor(report.rips_version):
            report.errors.append(
                f"rips {report.rips_version} cannot drive ResInsight {report.resinsight_version}: "
                "major.minor must match (rips raises 'Wrong Version of ResInsight'). "
                f"Install rips=={'.'.join(map(str, major_minor(report.resinsight_version)))}.*"
            )
    if report.contract_version and report.resinsight_version:
        want = report.contract_version.strip()
        got = report.resinsight_version
        if not (got == want or got.startswith(want + ".")):
            report.errors.append(f"resinsight.version is {want}, the binary is {got}")
    if report.headless == "xvfb-run" and not shutil.which("xvfb-run"):
        report.errors.append("resinsight.headless is xvfb-run, but xvfb-run is not on PATH")
    if report.headless not in (None, "xvfb-run", "none"):
        report.errors.append(f"resinsight.headless must be xvfb-run or none, not {report.headless!r}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--contract", type=Path, default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--bin", default=None, help="ResInsight executable or launcher (overrides the contract)"
    )
    parser.add_argument("--python", default=None, help="Python with rips (overrides the contract)")
    parser.add_argument("--headless", default=None, choices=("xvfb-run", "none"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report, _ = resolve(args)
    except contract_lib.ContractError as error:
        sys.stderr.write(f"error: contract: {error}\n")
        return 2
    check(report, args.timeout)
    payload = asdict(report) | {"ready": report.ready}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"ResInsight check: {'READY' if report.ready else 'NOT READY'}")
        print(f"binary: {report.bin} -> {report.resinsight_version or 'unknown'}")
        print(f"rips: {report.python} -> {report.rips_version or 'unknown'}")
        for line in report.missing + report.errors:
            print(f"ERROR: {line}")
    if report.missing:
        return 2
    return 0 if report.ready else 1


if __name__ == "__main__":
    sys.exit(main())
