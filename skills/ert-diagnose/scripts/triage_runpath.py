#!/usr/bin/env python3
"""Read-only triage of ERT realization runpaths.

Uses only the Python standard library, so it runs in any Komodo or venv.
It never writes inside a runpath and never deletes anything.

Usage:
    triage_runpath.py '/scratch/fmu/user/case/realization-*/iter-0' [--json] [--expected N | --realizations 0-4,7]
    triage_runpath.py /scratch/fmu/user/case --iter 0 [--expect 'eclipse/model/*.UNSMRY' ...] [--min-free-gb 5]

Coded findings (each gives the count, the realizations, and a limiter):
    ERTD-NOT-IDEMPOTENT       a failed realization has FileExistsError or "File exists" in a forward-model
                              stderr, stdout, or ERROR file. The step is not safe to submit again: a link or a file
                              from an interrupted run is in the way. Make the step replace its own links.
    ERTD-DONE-WITHOUT-RESULT  an OK or DONE file, or a status.json with only successful steps, says success, but no
                              file matches an --expect glob (relative to the runpath). The realization is failed.
    ERTD-NO-SPACE             a forward-model stderr, stdout, or ERROR file has "No space left on device" or
                              "OSError: [Errno 28]". The file system was full when the step wrote.
    ERTD-LOW-DISK             (warning) the file system of the first runpath has less free space than --min-free-gb.
                              A full disk can show only as "Too few realizations" and "--- Logging error ---".

Exit code: 0 = all realizations ok, 1 = at least one problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))
import runpath_lib

TAIL_LINES = 12
MAX_FILE_BYTES = 2_000_000

# Order matters: the first class whose pattern matches the evidence wins.
SIGNATURES: list[tuple[str, re.Pattern[str]]] = [
    ("storage", re.compile(r"no space left|disk quota|quota exceeded|filesystem full", re.I)),
    (
        "out_of_memory",
        re.compile(r"\boom\b|out of memory|cannot allocate memory|memoryerror|std::bad_alloc", re.I),
    ),
    ("killed", re.compile(r"\bsigkill\b|\bkilled\b|exit code -?9\b|terminated by signal", re.I)),
    ("timeout", re.compile(r"max_runtime|run time limit|time limit exceeded|timed out|\btimeout\b", re.I)),
    ("license", re.compile(r"licen[sc]e|flexlm|lmgrd|no feature", re.I)),
    ("file_exists", re.compile(r"FileExistsError|\bFile exists\b")),
    (
        "missing_file",
        re.compile(
            r"no such file|file not found|cannot open|could not open|missing include|filenotfounderror", re.I
        ),
    ),
    (
        "command_not_found",
        re.compile(
            r"command not found|executable .* not found|fm_dispatch\.py not found|not found in path", re.I
        ),
    ),
    (
        "convergence",
        re.compile(
            r"convergence|time ?step cut|timestep cut|solver failed|too many (newton|linear) iterations", re.I
        ),
    ),
    (
        "simulator_input",
        re.compile(r"@--\s*error|unknown keyword|invalid keyword|parse error|\bproblem:", re.I),
    ),
    ("python_exception", re.compile(r"traceback \(most recent call last\)|\w+error:|\w+exception:", re.I)),
]

# A fatal line stops the simulator. A problem line is a recoverable event, for example a time step chop.
# OPM Flow writes "Problem:" for each convergence failure that it recovers from. A run with such lines can succeed.
PRT_ERROR = re.compile(r"@--\s*ERROR|^\s*Error:", re.I)
PRT_PROBLEM = re.compile(r"@--\s*PROBLEM|^\s*Problem:", re.I)
MAX_PRT_LINES = 5
FILE_EXISTS = re.compile(r"FileExistsError|\bFile exists\b")
NO_SPACE = re.compile(r"No space left on device|OSError: \[Errno 28\]")
LOG_GLOBS = ("*.stderr*", "*.stdout*", "ERROR")
OK_STATUS = {"success"}
ACTIVE_STATUS = {"running", "waiting", "pending", "submitted", "unknown"}


def read_text(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def tail(text: str, count: int = TAIL_LINES) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-count:])


def load_status_json(path: Path) -> dict | None:
    text = read_text(path / "status.json")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def first_failed_step(status: dict | None) -> dict | None:
    if not status:
        return None
    for step in status.get("steps", []):
        if str(step.get("status", "")).lower() not in OK_STATUS | ACTIVE_STATUS:
            return step
    return None


def prt_scan(path: Path) -> tuple[list[str], list[str], int]:
    """Return (error lines, problem lines, problem count). The line lists hold at most MAX_PRT_LINES."""
    errors: list[str] = []
    problems: list[str] = []
    count = 0
    for prt in sorted(path.glob("eclipse/model/*.PRT")) + sorted(path.glob("*.PRT")):
        for line in read_text(prt).splitlines():
            if PRT_ERROR.search(line):
                if len(errors) < MAX_PRT_LINES:
                    errors.append(f"{prt.name}: {line.strip()[:200]}")
            elif PRT_PROBLEM.search(line):
                count += 1
                if len(problems) < MAX_PRT_LINES:
                    problems.append(f"{prt.name}: {line.strip()[:200]}")
    return errors, problems, count


def classify(evidence: str) -> str:
    for name, pattern in SIGNATURES:
        if pattern.search(evidence):
            return name
    return "unknown"


def first_signature_line(evidence: str, failure_class: str) -> str | None:
    pattern = dict(SIGNATURES).get(failure_class)
    if pattern is None:
        return None
    for line in evidence.splitlines():
        if pattern.search(line):
            return line.strip()[:300]
    return None


def says_success(path: Path, status: dict | None) -> str | None:
    """The marker that says the realization succeeded: OK, DONE, or status.json with only successful steps."""
    for name in ("OK", "DONE"):
        if (path / name).is_file():
            return name
    steps = (status or {}).get("steps", [])
    if steps and all(str(step.get("status", "")).lower() in OK_STATUS for step in steps):
        return "status.json"
    return None


def missing_results(path: Path, expect: list[str]) -> list[str]:
    return [pattern for pattern in expect if not any(p.is_file() for p in path.glob(pattern))]


def log_line(path: Path, pattern: re.Pattern[str]) -> str | None:
    """The first line that matches pattern in a forward-model log, as 'file: line'."""
    for name in LOG_GLOBS:
        for log in sorted(path.glob(name)):
            for line in read_text(log).splitlines():
                if pattern.search(line):
                    return f"{log.name}: {line.strip()[:200]}"
    return None


def disk_check(path: Path, min_free_gb: float) -> dict:
    """Free space of the file system that holds path. code is ERTD-LOW-DISK below min_free_gb, else None."""
    result: dict = {"path": str(path), "free_gb": None, "min_free_gb": min_free_gb, "code": None}
    try:
        free = shutil.disk_usage(path).free / 1e9
    except OSError as error:
        result["error"] = str(error)
        return result
    result["free_gb"] = round(free, 2)
    if free < min_free_gb:
        result["code"] = "ERTD-LOW-DISK"
    return result


def missing_detail(result: dict) -> str:
    return next(f["detail"] for f in result["findings"] if f["code"] == "ERTD-DONE-WITHOUT-RESULT")


def triage_one(path: Path, expect: list[str] | None = None) -> dict:
    has_ok = (path / "OK").is_file()
    has_error = (path / "ERROR").is_file()
    status = load_status_json(path)
    step = first_failed_step(status)
    prt, problems, problem_count = prt_scan(path)

    result: dict = {
        "realization": runpath_lib.realization_number(path),
        "path": str(path),
        "state": "ok",
        "failed_step": None,
        "failure_class": None,
        "signature": None,
        "evidence": [],
        "simulator_problems": problem_count,
        "findings": [],
    }
    no_space = log_line(path, NO_SPACE)
    if no_space:
        result["findings"].append({"code": "ERTD-NO-SPACE", "detail": no_space})
    marker = says_success(path, status)
    missing = missing_results(path, expect or []) if marker else []
    if missing:
        result["findings"].append(
            {
                "code": "ERTD-DONE-WITHOUT-RESULT",
                "detail": f"{marker} says success, but no file matches: {', '.join(missing)}",
            }
        )

    if has_ok and not has_error and step is None and not prt:
        if missing:
            result["state"] = "failed"
            result["failure_class"] = "missing_result"
            result["signature"] = missing_detail(result)
            result["evidence"] = [marker]
        return result

    pieces: list[str] = []
    evidence: list[str] = []
    if step is not None:
        result["failed_step"] = step.get("name")
        if step.get("error"):
            pieces.append(str(step["error"]))
        for key in ("stderr", "stdout"):
            name = step.get(key)
            if name and (path / name).is_file():
                body = tail(read_text(path / name))
                if body:
                    pieces.append(body)
                    evidence.append(name)
    if has_error:
        pieces.append(tail(read_text(path / "ERROR")))
        evidence.append("ERROR")
    if prt:
        pieces.extend(prt)
        evidence.append("PRT")
    if problems:
        # Problem lines are evidence for a failed realization only. They do not make a realization fail.
        pieces.extend(problems)
        if "PRT" not in evidence:
            evidence.append("PRT")
    evidence_text = "\n".join(pieces)

    if step is None and not has_error and not prt:
        # No OK file and no failure evidence. The run is still active, or it died silently.
        if missing:
            result["state"] = "failed"
            result["failure_class"] = "missing_result"
            result["signature"] = missing_detail(result)
            result["evidence"] = [marker]
        else:
            result["state"] = "incomplete"
        return result

    result["state"] = "failed"
    result["failure_class"] = classify(evidence_text)
    result["signature"] = first_signature_line(evidence_text, result["failure_class"])
    if result["signature"] is None and pieces:
        result["signature"] = pieces[0].splitlines()[-1].strip()[:300]
    result["evidence"] = evidence
    exists = log_line(path, FILE_EXISTS)
    if exists:
        result["findings"].append({"code": "ERTD-NOT-IDEMPOTENT", "detail": exists})
    return result


def summarize(
    rows: list[dict], expected: set[int] | None, extra: list[int] | None = None, disk: dict | None = None
) -> dict:
    states = Counter(row["state"] for row in rows)
    classes = Counter(row["failure_class"] for row in rows if row["failure_class"])
    steps = Counter(row["failed_step"] for row in rows if row["failed_step"])
    found = {row["realization"] for row in rows if row["realization"] is not None}
    missing = sorted(expected - found) if expected is not None else []
    with_problems = sorted(
        row["realization"] for row in rows if row["state"] == "ok" and row["simulator_problems"]
    )
    by_code: dict[str, list[int | None]] = {}
    for row in rows:
        for item in row["findings"]:
            by_code.setdefault(item["code"], []).append(row["realization"])
    return {
        "findings_by_code": by_code,
        "count": len(rows),
        "by_state": dict(states),
        "by_failure_class": dict(classes),
        "by_failed_step": dict(steps),
        "missing_realizations": missing,
        "extra_realizations": extra or [],
        "ok_with_simulator_problems": with_problems,
        "disk": disk,
    }


def render_text(rows: list[dict], summary: dict) -> str:
    out = [f"Realizations found: {summary['count']}", f"By state: {summary['by_state']}"]
    ok = [row["realization"] for row in rows if row["state"] == "ok"]
    if ok:
        out.append(f"OK realizations: {ok}")
    if summary["by_failure_class"]:
        out.append(f"By failure class: {summary['by_failure_class']}")
    if summary["by_failed_step"]:
        out.append(f"By failed step: {summary['by_failed_step']}")
    if summary["missing_realizations"]:
        out.append(f"Missing realization folders: {summary['missing_realizations']}")
    if summary["extra_realizations"]:
        out.append(
            "Realization folders outside the expected set (not checked, maybe from an earlier run): "
            f"{summary['extra_realizations']}"
        )
    if summary["ok_with_simulator_problems"]:
        out.append(
            "Realizations that are ok but have simulator problem lines (for example time step chops): "
            f"{summary['ok_with_simulator_problems']}"
        )
    disk = summary.get("disk")
    if disk and disk["free_gb"] is None:
        out.append(f"Free disk space of {disk['path']} is unknown: {disk.get('error')}")
    elif disk and disk["code"]:
        out.append(
            f"FINDING ERTD-LOW-DISK (warning): {disk['free_gb']:g} GB free on the file system of {disk['path']}"
            f" (threshold: --min-free-gb {disk['min_free_gb']:g}). A full disk can show only as 'Too few"
            " realizations are evaluated successfully' and '--- Logging error ---'. Free space before you"
            " submit again. Limiter: the free space now is not the free space during the run; another user or"
            " a quota can fill the disk"
        )
    for code, reals in summary["findings_by_code"].items():
        failed = summary["by_state"].get("failed", 0)
        if code == "ERTD-NO-SPACE":
            out.append(
                f"FINDING {code}: {len(reals)} realizations have 'No space left on device' or 'OSError: [Errno 28]'"
                f" in a forward-model log (threshold: 1): {reals}. The file system was full when the step wrote."
                " Free space, then submit again. Limiter: the full file system can be a different one from the"
                " runpath, for example /tmp or a home quota"
            )
        elif code == "ERTD-NOT-IDEMPOTENT":
            out.append(
                f"FINDING {code}: {len(reals)} of {failed} failed realizations have FileExistsError or 'File exists'"
                f" in a forward-model log (threshold: 1): {reals}. The step is not safe to submit again: a link or"
                " a file from an interrupted run is in the way. Make the step replace its own links (ln -sf, or"
                " remove the old link first). Limiter: two steps that write the same file give the same error"
            )
        else:
            out.append(
                f"FINDING {code}: {len(reals)} realizations say success, but an --expect file is missing"
                f" (threshold: 1): {reals}. Limiter: the glob can be wrong, or a later step or workflow writes"
                " the file"
            )
    for row in rows:
        if row["state"] == "ok":
            continue
        out.append(
            f"- realization {row['realization']}: {row['state']}"
            f" | step={row['failed_step']} | class={row['failure_class']}"
            f" | {row['signature']}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("target", help="Runpath glob, or a case directory that holds realization-*/iter-*")
    parser.add_argument(
        "--iter", dest="iteration", default=None, help="Iteration number, for a case directory"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--expected", type=int, default=None, help="Expected number of realizations, numbered 0 to N-1"
    )
    group.add_argument(
        "--realizations", default=None, help="Expected realizations as an ERT spec, for example 0-4,7"
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="GLOB",
        help="A result file that a successful realization must have, relative to the runpath. Repeat it",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=5.0,
        help="ERTD-LOW-DISK when the runpath file system has less free space (GB). Default 5",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON")
    args = parser.parse_args(argv)
    if not args.min_free_gb >= 0:
        print("ERROR: --min-free-gb must be 0 or more", file=sys.stderr)
        return 2

    pattern = args.target
    if not any(char in pattern for char in "*?["):
        iteration = args.iteration if args.iteration is not None else "*"
        pattern = str(Path(pattern) / f"realization-*/iter-{iteration}")
    try:
        expected = runpath_lib.expected_set(args.expected, args.realizations)
    except ValueError as error:
        print(f"ERROR: --realizations: {error}", file=sys.stderr)
        return 2
    ensemble = runpath_lib.read_ensemble(pattern, expected=expected, need_data_file=False)
    matched = [member.runpath for member in ensemble.found] + [
        drop.path for drop in (*ensemble.dropped, *ensemble.extra)
    ]
    if not matched:
        print(f"ERROR: no realization folders match: {args.target}", file=sys.stderr)
        return 2
    first = matched[0]
    runpaths = [member.runpath for member in ensemble.found]
    extra = sorted(drop.realization for drop in ensemble.extra)
    for problem in ensemble.problems():
        print(f"ERROR: {problem}", file=sys.stderr)
    rows = [triage_one(path, args.expect) for path in runpaths]
    summary = summarize(rows, expected, extra, disk_check(first, args.min_free_gb))
    if args.json:
        print(json.dumps({"summary": summary, "realizations": rows}, indent=2))
    else:
        print(render_text(rows, summary))
    problems = summary["by_state"].get("failed", 0) + summary["by_state"].get("incomplete", 0)
    no_space = summary["findings_by_code"].get("ERTD-NO-SPACE")
    return 1 if problems or no_space or not ensemble.complete else 0


if __name__ == "__main__":
    sys.exit(main())
