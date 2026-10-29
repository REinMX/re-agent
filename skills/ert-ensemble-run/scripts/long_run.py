#!/usr/bin/env python3
"""Run long jobs detached and record them in a STATUS file, so that a new session can resume.

Uses only the Python standard library. Linux only (reads /proc).

Usage:
    long_run.py start --status STATUS.md --name <job> --log <file> [--cwd DIR] -- <command...>
    long_run.py note --status STATUS.md <text...>
    long_run.py check --status STATUS.md [--json] [--stale-minutes 10]

STATUS line format (one record per line; blank lines and lines that start with "#" are ignored):
    <time> START <name> pid=<pid> log=<log> cmd=<shlex-joined command>
    <time> NOTE <free text>
    <time> END <name> pid=<pid> state=exited
<time> is a timezone-aware local ISO time. Free text is only allowed in NOTE lines.

start runs the command in a new session with stdin from /dev/null and stdout and stderr appended to --log.
It refuses (exit 1) when a START for the same name has no END and its process is still alive.

check reports each started job as RUNNING, EXITED (the process is gone and this check appended its END
line), or ENDED (an END line was already present). A pid counts as the job only when the process is alive
and /proc/<pid>/cmdline starts with the recorded command head, so a reused pid is not reported as the job.
It warns STALE when the newest STATUS line is older than --stale-minutes.

Exit code: 0 = the file parses and no job died unnoticed, 1 = a job died without an END line before this
check (or start refused), 2 = usage or parse error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

NAME_RE = re.compile(r"[A-Za-z0-9_.:-]+")
TAIL_BYTES = 4096


class StatusError(ValueError):
    pass


@dataclass(frozen=True)
class Start:
    time: datetime
    name: str
    pid: int
    log: str
    cmd: tuple[str, ...]


@dataclass(frozen=True)
class Note:
    time: datetime
    text: str


@dataclass(frozen=True)
class End:
    time: datetime
    name: str
    pid: int


Record = Start | Note | End


def now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def format_record(rec: Record) -> str:
    stamp = rec.time.isoformat(timespec="seconds")
    match rec:
        case Start():
            return (
                f"{stamp} START {rec.name} pid={rec.pid} log={shlex.quote(rec.log)} cmd={shlex.join(rec.cmd)}"
            )
        case Note():
            return f"{stamp} NOTE {' '.join(rec.text.split())}"
        case End():
            return f"{stamp} END {rec.name} pid={rec.pid} state=exited"


def _time(text: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise StatusError(f"bad time {text!r}") from exc
    if stamp.tzinfo is None:
        raise StatusError(f"time {text!r} has no UTC offset")
    return stamp


def _field(token: str, key: str) -> str:
    if not token.startswith(f"{key}="):
        raise StatusError(f"expected {key}=, got {token!r}")
    return token[len(key) + 1 :]


def _pid(token: str) -> int:
    value = _field(token, "pid")
    if not value.isdigit() or int(value) <= 0:
        raise StatusError(f"bad pid {value!r}")
    return int(value)


def _name(token: str) -> str:
    if not NAME_RE.fullmatch(token):
        raise StatusError(f"bad job name {token!r}")
    return token


def parse_record(line: str) -> Record:
    parts = line.rstrip("\n").split(" ", 2)
    if len(parts) < 2:
        raise StatusError(f"not a STATUS line: {line!r}")
    stamp, kind = _time(parts[0]), parts[1]
    rest = parts[2] if len(parts) == 3 else ""
    if kind == "NOTE":
        return Note(stamp, rest)
    try:
        tokens = shlex.split(rest)
    except ValueError as exc:
        raise StatusError(f"bad quoting in {line!r}") from exc
    if kind == "START":
        if len(tokens) < 4:
            raise StatusError(f"START line needs name, pid, log, cmd: {line!r}")
        cmd = (_field(tokens[3], "cmd"), *tokens[4:])
        if not cmd[0]:
            raise StatusError(f"START line has an empty cmd: {line!r}")
        return Start(stamp, _name(tokens[0]), _pid(tokens[1]), _field(tokens[2], "log"), cmd)
    if kind == "END":
        if len(tokens) != 3 or _field(tokens[2], "state") != "exited":
            raise StatusError(f"END line needs name, pid, state=exited: {line!r}")
        return End(stamp, _name(tokens[0]), _pid(tokens[1]))
    raise StatusError(f"unknown record kind {kind!r} in {line!r}")


def read_status(path: Path) -> list[Record]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        try:
            records.append(parse_record(line))
        except StatusError as exc:
            raise StatusError(f"{path}:{number}: {exc}") from None
    return records


def append_record(path: Path, rec: Record) -> None:
    with path.open("a") as handle:
        handle.write(format_record(rec) + "\n")


def open_starts(records: list[Record]) -> list[Start]:
    ended = {(r.name, r.pid) for r in records if isinstance(r, End)}
    return [r for r in records if isinstance(r, Start) and (r.name, r.pid) not in ended]


def is_job_alive(start: Start) -> bool:
    try:
        os.kill(start.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    try:
        raw = Path(f"/proc/{start.pid}/cmdline").read_bytes()
    except OSError:
        return False
    argv = [arg for arg in raw.split(b"\0") if arg]
    if not argv:
        return False  # a zombie has an empty cmdline
    head = os.path.basename(start.cmd[0]).encode()
    # A script with a shebang shows as "<interpreter> <script> ...".
    return any(os.path.basename(arg) == head for arg in argv[:2])


def log_info(log: str) -> tuple[str | None, float | None]:
    path = Path(log)
    try:
        stat = path.stat()
        age = max(0.0, time.time() - stat.st_mtime)
        with path.open("rb") as handle:
            handle.seek(max(0, stat.st_size - TAIL_BYTES))
            lines = [line for line in handle.read().decode(errors="replace").splitlines() if line.strip()]
    except OSError:
        return None, None
    return (lines[-1] if lines else ""), round(age, 1)


def cmd_start(args: argparse.Namespace) -> int:
    status = Path(args.status)
    if not NAME_RE.fullmatch(args.name):
        print(f"error: job name {args.name!r} must match {NAME_RE.pattern}", file=sys.stderr)
        return 2
    if not args.command:
        print("error: give the command after --", file=sys.stderr)
        return 2
    for start in open_starts(read_status(status)):
        if start.name == args.name and is_job_alive(start):
            print(f"error: job {args.name} is RUNNING as pid {start.pid}; see {status}", file=sys.stderr)
            return 1
    launch = update_gate(args, status)
    if isinstance(launch, int):
        return launch
    argv, cwd = launch
    log = Path(args.log).resolve()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(os.devnull, "rb") as stdin, log.open("ab") as out:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                shell=False,
                stdin=stdin,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            print(f"error: cannot start {argv[0]!r}: {exc}", file=sys.stderr)
            return 2
    append_record(status, Start(now(), args.name, proc.pid, str(log), argv))
    proc.returncode = 0  # detached on purpose; do not warn that the child still runs
    print(proc.pid)
    return 0


def update_gate(args: argparse.Namespace, status: Path) -> tuple[tuple[str, ...], Path] | int:
    """Return immutable launch inputs, or refuse before Popen."""
    argv, cwd = tuple(args.command), Path(args.cwd or ".").resolve()
    if args.no_preflight is not None:
        if not args.no_preflight.strip():
            print("error: --no-preflight needs a nonblank reason", file=sys.stderr)
            return 1
        append_record(
            status,
            Note(
                now(),
                f"{args.name} UNCHECKED unchecked override: {args.no_preflight}; "
                f"cwd={shlex.quote(str(cwd))} cmd={shlex.join(argv)}",
            ),
        )
        return argv, cwd
    path = Path(__file__).resolve().parents[2] / "ahm-workflow" / "scripts" / "ahm_preflight.py"
    spec = importlib.util.spec_from_file_location("ahm_preflight", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ahm_preflight"] = module
    spec.loader.exec_module(module)
    screening = Path(args.screening).resolve() if args.screening else None
    result = module.check(argv=argv, cwd=cwd, screening=screening, allow_members=args.allow_members)
    if result is None:
        return argv, cwd
    if not result.ready:
        for item in result.items:
            if item.severity == "error":
                print(f"error: {item.code}: {item.message}", file=sys.stderr)
        return 1
    append_record(
        status,
        Note(
            now(),
            f"{args.name} AHM compatibility passed; native ERT loading unverified; "
            f"screening={screening} cwd={shlex.quote(str(result.cwd))} cmd={result.command}",
        ),
    )
    return result.argv, result.cwd


def cmd_note(args: argparse.Namespace) -> int:
    text = " ".join(" ".join(args.text).split())
    if not text:
        print("error: a note needs text", file=sys.stderr)
        return 2
    append_record(Path(args.status), Note(now(), text))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    status = Path(args.status)
    if not status.exists():
        print(f"error: {status} not found", file=sys.stderr)
        return 2
    records = read_status(status)
    newest = max((r.time for r in records), default=None)
    current = now()
    stale = newest is None or (current - newest).total_seconds() > args.stale_minutes * 60
    open_ids = {(s.name, s.pid) for s in open_starts(records)}
    jobs = []
    died = []
    for start in (r for r in records if isinstance(r, Start)):
        if (start.name, start.pid) not in open_ids:
            state = "ENDED"
        elif is_job_alive(start):
            state = "RUNNING"
        else:
            state = "EXITED"
            append_record(status, End(current, start.name, start.pid))
            died.append(start.name)
        tail, age = log_info(start.log)
        jobs.append(
            {
                "name": start.name,
                "pid": start.pid,
                "state": state,
                "started": start.time.isoformat(),
                "log": start.log,
                "log_tail": tail,
                "log_age_s": age,
                "cmd": shlex.join(start.cmd),
            }
        )
    report = {
        "status": str(status),
        "newest": newest.isoformat() if newest else None,
        "stale": stale,
        "stale_minutes": args.stale_minutes,
        "died_unnoticed": died,
        "jobs": jobs,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for job in jobs:
            age = "no log" if job["log_age_s"] is None else f"log age {job['log_age_s']:.0f} s"
            print(f"{job['state']:<7} {job['name']} pid={job['pid']} {age}")
            if job["log_tail"]:
                print(f"        last: {job['log_tail']}")
        if not jobs:
            print("no jobs started")
    if stale:
        when = report["newest"] or "never"
        print(f"STALE: newest STATUS line ({when}) is older than {args.stale_minutes} min", file=sys.stderr)
    for name in died:
        print(f"warning: job {name} died without an END line; END appended now", file=sys.stderr)
    return 1 if died else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("start", help="start a command detached and record it")
    start.add_argument("--status", required=True)
    start.add_argument("--name", required=True)
    start.add_argument("--log", required=True)
    start.add_argument("--cwd")
    start.add_argument("--screening", help="screening.json; an ERT update run must pass ahm_preflight.py")
    start.add_argument("--no-preflight", metavar="REASON", help="start an ERT update run without the gate")
    start.add_argument("--allow-members", metavar="REASON", help="passed to ahm_preflight.py")
    start.add_argument("command", nargs=argparse.REMAINDER)
    note = sub.add_parser("note", help="append a progress note")
    note.add_argument("--status", required=True)
    note.add_argument("text", nargs="+")
    check = sub.add_parser("check", help="report the state of each started job")
    check.add_argument("--status", required=True)
    check.add_argument("--json", action="store_true")
    check.add_argument("--stale-minutes", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "start" and args.command[:1] == ["--"]:
        args.command = args.command[1:]
    try:
        return {"start": cmd_start, "note": cmd_note, "check": cmd_check}[args.action](args)
    except StatusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
