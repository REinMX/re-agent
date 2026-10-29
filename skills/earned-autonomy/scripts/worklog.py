#!/usr/bin/env python3
"""Append-only work log for agent tasks: what ran, how long, and what the human used.

Each line of the log is one event. `report` folds the events into one record per task.
Time per task has three parts that do not overlap:
  sim       wall time while at least one simulator command (Flow, ERT, Everest) ran
  other     wall time that the agent marked as downtime (box restart, waiting on access)
  analysis  wall time of analysis phases outside sim and other; without analysis phases,
            the rest of the task time (labelled inferred, because it also holds idle time)
Every number carries a basis: measured, or inferred when an input was reconstructed.
Human fields come only from `rate`. They are the human's own estimate.

Log path: --log, else env RE_WORKLOG, else `worklog.path` in site.contract.yml, else
./worklog/pilot.jsonl. The tool refuses a path that git would commit.

Exit: 0 ok, 2 input error, else the exit code of the command that `run` started.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))

SCHEMA = 1
KINDS = ("sim", "analysis", "other")
BASES = ("measured", "inferred")
USED = ("merged", "decided", "discarded")
STATUSES = ("done", "partial", "failed", "abandoned")
PR_STATES = ("opened", "merged", "closed")
DEFAULT_LOG = Path("worklog/pilot.jsonl")
FIELDS = {
    "start": {"title", "basis", "source"},
    "phase": {"kind", "action", "basis", "source"},
    "interval": {"kind", "start", "end", "basis", "source"},
    "cmd_start": {"id", "kind", "label", "argv", "sims", "budget"},
    "cmd": {
        "id",
        "kind",
        "label",
        "argv",
        "start",
        "end",
        "rc",
        "elapsed_s",
        "sims",
        "basis",
        "source",
        "budget",
        "budget_outcome",
        "stop_reason",
    },
    "end": {"status", "outputs", "basis", "source"},
    "pr": {"ref", "state", "basis", "source"},
    "rate": {"used", "human_hours_estimate", "review_hours"},
}


class WorklogError(Exception):
    pass


def now() -> datetime:
    return datetime.now().astimezone()


def stamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def parse_time(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WorklogError(f"not an ISO 8601 time: {text!r}") from exc
    if value.tzinfo is None:
        raise WorklogError(f"time needs a UTC offset, for example +02:00: {text!r}")
    return value


def contract_log_path() -> Path | None:
    try:
        import contract_lib
    except ImportError:
        return None
    try:
        base, overlay = contract_lib.locate_files()
    except contract_lib.ContractError:
        return None
    merged: dict[str, Any] = {}
    owner = None
    for path in (base, overlay):
        if path is not None:
            data = contract_lib.read_yaml(path)
            if contract_lib.get_dotted(data, "worklog.path"):
                owner = path
            merged = contract_lib.deep_merge(merged, data)
    value = contract_lib.get_dotted(merged, "worklog.path")
    if not value:
        return None
    path = Path(os.path.expandvars(str(value))).expanduser()
    return path if path.is_absolute() else Path(owner).resolve().parent / path


def resolve_log(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("RE_WORKLOG"):
        return Path(os.environ["RE_WORKLOG"]).expanduser()
    return contract_log_path() or DEFAULT_LOG


def ensure_not_committable(path: Path) -> None:
    folder = path.resolve().parent
    while not folder.exists():
        folder = folder.parent
    try:
        inside = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True
        )
    except FileNotFoundError:
        return
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return
    ignored = subprocess.run(["git", "-C", str(folder), "check-ignore", "-q", str(path.resolve())])
    if ignored.returncode != 0:
        raise WorklogError(
            f"{path} is inside a git work tree and is not ignored. Add it to .gitignore or set worklog.path."
        )


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise WorklogError(f"{path}:{number}: not JSON: {exc}") from exc
    return events


def append(path: Path, event: str, task: str, ts: datetime | None = None, **fields: Any) -> dict[str, Any]:
    unknown = set(fields) - FIELDS[event]
    if unknown:
        raise WorklogError(f"{event} has no field {sorted(unknown)}")
    ensure_not_committable(path)
    record = {"v": SCHEMA, "event": event, "task": task, "ts": stamp(ts or now())}
    record.update({k: v for k, v in fields.items() if v is not None})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def open_tasks(events: Iterable[dict[str, Any]]) -> list[str]:
    state: dict[str, bool] = {}
    for event in events:
        if event["event"] == "start":
            state[event["task"]] = True
        elif event["event"] == "end":
            state[event["task"]] = False
    return [task for task, is_open in state.items() if is_open]


def pick_task(path: Path, task: str | None) -> str:
    task = task or os.environ.get("RE_WORKLOG_TASK")
    events = read_events(path)
    tasks = open_tasks(events)
    if task:
        if task not in tasks:
            raise WorklogError(f"task {task!r} is not open in {path}")
        return task
    if len(tasks) != 1:
        raise WorklogError(f"name the task with --task: {len(tasks)} open tasks in {path} ({tasks})")
    return tasks[0]


def union_seconds(spans: Iterable[tuple[float, float]]) -> float:
    total, last = 0.0, None
    for begin, end in sorted(spans):
        if last is None or begin > last:
            total += end - begin
            last = end
        elif end > last:
            total += end - last
            last = end
    return total


def subtract(spans: list[tuple[float, float]], cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result = []
    for begin, end in spans:
        pieces = [(begin, end)]
        for cut_begin, cut_end in cuts:
            pieces = [
                part
                for a, b in pieces
                for part in ((a, min(b, cut_begin)), (max(a, cut_end), b))
                if part[1] > part[0]
            ]
        result.extend(pieces)
    return result


def fold(events: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for event in events:
        task = tasks.setdefault(
            event["task"],
            {"task": event["task"], "spans": [], "cmds": {}, "prs": {}, "open_phases": {}, "outputs": []},
        )
        kind, basis, ts = event["event"], event.get("basis", "measured"), parse_time(event["ts"])
        if kind == "start":
            task.update(title=event.get("title", ""), start=ts, start_basis=basis)
        elif kind == "phase" and event["action"] == "start":
            task["open_phases"][event["kind"]] = (ts, basis)
        elif kind == "phase" and event["action"] == "stop":
            begun = task["open_phases"].pop(event["kind"], None)
            if begun:
                worst = "inferred" if "inferred" in (begun[1], basis) else "measured"
                task["spans"].append((event["kind"], begun[0], ts, worst, "phase"))
        elif kind == "interval":
            task["spans"].append(
                (event["kind"], parse_time(event["start"]), parse_time(event["end"]), basis, "phase")
            )
        elif kind == "cmd_start":
            task["cmds"].setdefault(event["id"], dict(event, open=True))
        elif kind == "cmd":
            task["cmds"][event["id"]] = dict(event, open=False)
            if event.get("budget_outcome", {}).get("launched", True):
                task["spans"].append(
                    (event["kind"], parse_time(event["start"]), parse_time(event["end"]), basis, "cmd")
                )
        elif kind == "end":
            task.update(end=ts, end_basis=basis, status=event["status"])
            task["outputs"].extend(event.get("outputs", []))
        elif kind == "pr":
            task["prs"][event["ref"]] = event["state"]
        elif kind == "rate":
            task["rate"] = event
    return tasks


def summarize(task: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
    start = task.get("start")
    end = task.get("end") or at
    row: dict[str, Any] = {
        "task": task["task"],
        "title": task.get("title", ""),
        "start": start,
        "end": task.get("end"),
        "status": task.get("status", "open"),
    }
    if start is None or end is None:
        raise WorklogError(f"task {task['task']!r} has no start event")
    lo, hi = start.timestamp(), end.timestamp()

    def spans(kind: str) -> tuple[list[tuple[float, float]], str]:
        chosen = [s for s in task["spans"] if s[0] == kind]
        clipped = [(max(lo, b.timestamp()), min(hi, e.timestamp())) for _, b, e, _, _ in chosen]
        basis = "inferred" if any(s[3] == "inferred" for s in chosen) else "measured"
        return [c for c in clipped if c[1] > c[0]], basis

    sim, sim_basis = spans("sim")
    other, other_basis = spans("other")
    analysis, analysis_basis = spans("analysis")
    other_only = subtract(other, sim)
    total_s = hi - lo
    sim_s, other_s = union_seconds(sim), union_seconds(other_only)
    total_basis = "inferred" if "inferred" in (task.get("start_basis"), task.get("end_basis")) else "measured"
    if any(s[0] == "analysis" and s[4] == "phase" for s in task["spans"]):
        analysis_s = union_seconds(subtract(analysis, sim + other))
    else:
        analysis_s = max(0.0, total_s - sim_s - other_s)
        analysis_basis = "inferred"
    if "inferred" in (sim_basis, other_basis, total_basis):
        analysis_basis = "inferred"
    cmds = list(task["cmds"].values())
    done = [c for c in cmds if not c["open"]]
    sims = [
        c.get("sims")
        for c in done
        if c["kind"] == "sim" and c.get("budget_outcome", {}).get("launched", True)
    ]
    rate = task.get("rate") or {}
    estimate = rate.get("human_hours_estimate")
    review = rate.get("review_hours")
    row.update(
        total_h=total_s / 3600,
        total_basis=total_basis,
        sim_h=sim_s / 3600,
        sim_basis=sim_basis,
        analysis_h=analysis_s / 3600,
        analysis_basis=analysis_basis,
        other_h=other_s / 3600,
        other_basis=other_basis,
        unaccounted_h=max(0.0, total_s - sim_s - other_s - analysis_s) / 3600,
        simulations=sum(s for s in sims if isinstance(s, int)),
        simulations_unknown=sum(1 for s in sims if not isinstance(s, int)),
        commands=len(done),
        failed_commands=sum(1 for c in done if c.get("rc") not in (0, None)),
        unterminated_commands=sum(1 for c in cmds if c["open"]),
        outputs=list(task["outputs"]),
        prs_opened=len(task["prs"]),
        prs_merged=sum(1 for state in task["prs"].values() if state == "merged"),
        used=rate.get("used", ""),
        human_hours_estimate=estimate,
        review_hours=review,
        est_hours_saved=None if estimate is None else estimate - (review or 0.0),
    )
    return row


COLUMNS = (
    "task", "title", "start", "end", "status", "total_h", "total_basis", "sim_h", "sim_basis",
    "analysis_h", "analysis_basis", "other_h", "other_basis", "unaccounted_h", "simulations",
    "simulations_unknown", "commands", "failed_commands", "unterminated_commands", "prs_opened",
    "prs_merged", "used", "human_hours_estimate", "review_hours", "est_hours_saved", "outputs",
)  # fmt: skip


COUNTS = (
    "simulations",
    "simulations_unknown",
    "commands",
    "failed_commands",
    "unterminated_commands",
    "prs_opened",
)


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"task": "TOTAL", "title": f"{len(rows)} tasks"}
    for key in ("total_h", "sim_h", "analysis_h", "other_h", "unaccounted_h"):
        out[key] = sum(r[key] for r in rows)
        basis = key.replace("_h", "_basis")
        if basis in COLUMNS:
            out[basis] = "inferred" if any(r[basis] == "inferred" for r in rows) else "measured"
    for key in COUNTS:
        out[key] = sum(r[key] for r in rows)
    out["prs_merged"] = sum(r["prs_merged"] for r in rows)
    rated = [r["est_hours_saved"] for r in rows if r["est_hours_saved"] is not None]
    out["est_hours_saved"] = sum(rated) if rated else None
    out["used"] = f"{sum(1 for r in rows if r['used'] in ('merged', 'decided'))} of {len(rows)} used"
    return out


def cell(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M")
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value)


def hours(row: dict[str, Any], key: str) -> str:
    basis = row.get(key.replace("_h", "_basis"))
    return f"{row[key]:.2f} {'m' if basis == 'measured' else 'i'}"


def render_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([cell(row, key) for key in COLUMNS])
    return buffer.getvalue()


def render_markdown(rows: list[dict[str, Any]], zone: str) -> str:
    lines = [
        "| Task | Start | End | Status | Total h | Sim h | Analysis h | Other h | Simulations | PRs merged/opened | Used | Human estimate h | Review h | Est. saved h |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        sims = str(row["simulations"]) + (
            f" (+{row['simulations_unknown']} unknown)" if row["simulations_unknown"] else ""
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    row["task"] if row["task"] == "TOTAL" else f"`{row['task']}` {row['title']}",
                    cell(row, "start"),
                    cell(row, "end"),
                    cell(row, "status"),
                    hours(row, "total_h"),
                    hours(row, "sim_h"),
                    hours(row, "analysis_h"),
                    hours(row, "other_h"),
                    sims,
                    f"{row['prs_merged']}/{row['prs_opened']}",
                    cell(row, "used") or "not rated",
                    cell(row, "human_hours_estimate"),
                    cell(row, "review_hours"),
                    cell(row, "est_hours_saved"),
                ]
            )
            + " |"
        )
    lines += [
        "",
        f"Times are in {zone}. m = measured from logs. i = inferred (an input was reconstructed, or analysis is the rest of the task time).",
        "Sim, analysis and other do not overlap. Analysis done while a simulation ran counts as sim.",
        "Human estimate, Review h and Est. saved come from the human's `rate` entry. They are estimates, not measurements.",
        "Est. saved = human estimate minus review hours. Empty = not rated yet.",
    ]
    open_cmds = [
        f"`{r['task']}` {r['unterminated_commands']}"
        for r in rows
        if r.get("unterminated_commands") and r["task"] != "TOTAL"
    ]
    if open_cmds:
        lines.append("Commands with a start but no end (not counted): " + ", ".join(open_cmds) + ".")
    return "\n".join(lines) + "\n"


def build_rows(events: Sequence[dict[str, Any]], tasks: Sequence[str] | None) -> list[dict[str, Any]]:
    folded = fold(events)
    chosen = [folded[t] for t in (tasks or folded) if t in folded]
    missing = set(tasks or ()) - set(folded)
    if missing:
        raise WorklogError(f"no such task: {sorted(missing)}")
    at = now()
    return [summarize(task, at) for task in chosen]


class BudgetSpec(NamedTuple):
    max_seconds: float | None
    max_disk_mib: float | None
    disk_root: str | None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> BudgetSpec | None:
        for name in ("max_seconds", "max_disk_mib"):
            value = getattr(args, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise WorklogError(f"--{name.replace('_', '-')} requires a positive finite limit")
        if (args.max_disk_mib is None) != (args.disk_root is None):
            raise WorklogError("--max-disk-mib requires --disk-root, and --disk-root requires --max-disk-mib")
        if args.disk_root == "":
            raise WorklogError("--disk-root requires a nonempty directory path")
        if args.max_seconds is None and args.max_disk_mib is None:
            return None
        if sys.platform != "linux":
            raise WorklogError("run budgets require Linux process groups and allocated-block accounting")
        root = str(Path(args.disk_root).absolute()) if args.disk_root is not None else None
        return cls(args.max_seconds, args.max_disk_mib, root)


def allocated_bytes(root: str) -> int:
    """Count each inode's allocation once without following any symlink component."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    seen: set[tuple[int, int]] = set()

    def count(info: os.stat_result) -> int:
        key = (info.st_dev, info.st_ino)
        if key in seen:
            return 0
        seen.add(key)
        return info.st_blocks * 512

    def walk(fd: int) -> int:
        total = count(os.fstat(fd))
        with os.scandir(fd) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    try:
                        child = os.open(entry.name, flags, dir_fd=fd)
                    except FileNotFoundError:
                        continue
                    try:
                        total += walk(child)
                    finally:
                        os.close(child)
                else:
                    total += count(info)
        return total

    fd = os.open("/", flags)
    try:
        for part in Path(root).parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                # Only absence on the root's verified directory chain means zero.
                return 0
            os.close(fd)
            fd = child
        return walk(fd)
    finally:
        os.close(fd)


class BudgetMonitor:
    interval_s = 0.05

    def __init__(self, spec: BudgetSpec, tick: float):
        self.spec = spec
        self.tick = tick
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.reason: str | None = None
        self.error: str | None = None
        self.maximum: int | None = None
        self.samples = 0
        self.observer: threading.Thread | None = None

    def stop(self, reason: str, error: str | None = None) -> None:
        with self.lock:
            self.reason = self.reason or reason
            self.error = self.error or error

    def check_time(self) -> None:
        if self.spec.max_seconds is not None and time.monotonic() - self.tick >= self.spec.max_seconds:
            self.stop("max_seconds")

    def sample(self) -> None:
        assert self.spec.disk_root is not None and self.spec.max_disk_mib is not None
        try:
            value = allocated_bytes(self.spec.disk_root)
            with self.lock:
                self.maximum = max(self.maximum or 0, value)
                self.samples += 1
            if value / 1048576 >= self.spec.max_disk_mib:
                self.stop("max_disk_mib")
        except Exception as exc:
            self.stop("monitor_error", f"disk sample failed: {exc}")

    def observe(self) -> None:
        self.sample()
        self.ready.set()
        while not self.finished.wait(self.interval_s):
            if self.reason:
                return
            self.sample()
        if not self.reason:
            self.sample()

    def arm(self) -> None:
        if self.spec.disk_root is not None:
            self.observer = threading.Thread(target=self.observe, name="worklog-disk-budget", daemon=True)
            self.observer.start()
            wait_s = min(5.0, self.spec.max_seconds) if self.spec.max_seconds is not None else 5.0
            if not self.ready.wait(wait_s):
                self.check_time()
                self.stop("monitor_error", "disk observer did not become ready")
        self.check_time()

    def finish(self) -> dict[str, Any]:
        self.finished.set()
        if self.observer:
            self.observer.join(timeout=1.0)
            if self.observer.is_alive():
                self.stop("monitor_error", "disk observer did not stop within 1 second")
        with self.lock:
            return {"max_allocated_bytes": self.maximum, "samples": self.samples, "error": self.error}


def group_live(pgid: int) -> bool:
    """Ignore exited zombies, but never interpret an inspection error as absence."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == pgid and fields[0] != "Z":
            return True
    return False


def stop_group(process: subprocess.Popen) -> None:
    inspection_error = None
    for sig, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError as exc:
            inspection_error = exc
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            process.poll()
            try:
                alive = group_live(process.pid)
            except (OSError, ValueError) as exc:
                # Unknown membership must not prevent SIGKILL and direct-child reap.
                inspection_error = exc
                break
            if not alive:
                process.wait(timeout=1.0)
                if inspection_error:
                    raise WorklogError(f"process group cleanup had an inspection error: {inspection_error}")
                return
            time.sleep(0.02)
    process.wait(timeout=1.0)
    if inspection_error:
        raise WorklogError(f"process group cleanup could not be verified: {inspection_error}")
    if group_live(process.pid):
        raise WorklogError("owned process group still has live members after SIGKILL")


def cmd_run(args: argparse.Namespace, log: Path) -> int:
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        raise WorklogError("give the command after --")
    budget = BudgetSpec.from_args(args)
    task = pick_task(log, args.task)
    run_id = uuid.uuid4().hex[:12]
    label = args.label or Path(command[0]).name
    extra: dict[str, Any] = {"budget": budget._asdict()} if budget else {}
    append(
        log, "cmd_start", task, id=run_id, kind=args.kind, label=label, argv=command, sims=args.sims, **extra
    )
    begin, tick = now(), time.monotonic()
    rc = 130
    monitor = BudgetMonitor(budget, tick) if budget else None
    process = None
    launched = False
    received_signal: int | None = None
    previous_handlers = {}

    def interrupted(signum: int, frame: Any) -> None:
        nonlocal received_signal
        # Do not raise during Popen assignment or acquire the monitor's lock here.
        received_signal = received_signal or signum

    try:
        if monitor:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                previous_handlers[sig] = signal.signal(sig, interrupted)
        try:
            if monitor is None:
                rc = subprocess.run(command).returncode
            else:
                monitor.arm()
                if received_signal:
                    monitor.stop("interrupted")
                if not monitor.reason:
                    process = subprocess.Popen(command, start_new_session=True)
                    launched = True
                    while not monitor.reason:
                        if received_signal:
                            monitor.stop("interrupted")
                        monitor.check_time()
                        child_rc = process.poll()
                        if child_rc is not None and not group_live(process.pid):
                            rc = child_rc
                            break
                        time.sleep(monitor.interval_s)
        except FileNotFoundError as exc:
            if monitor and process is not None:
                monitor.stop("monitor_error", str(exc))
            else:
                rc = 127
        except KeyboardInterrupt:
            if monitor is None:
                raise
            received_signal = received_signal or signal.SIGINT
            monitor.stop("interrupted")
        except (OSError, ValueError) as exc:
            if monitor is None:
                raise
            monitor.stop("monitor_error", str(exc))
        finally:
            if monitor:
                if received_signal:
                    monitor.stop("interrupted")
                cleanup_error = None
                if process is not None and (monitor.reason or process.poll() is None):
                    try:
                        stop_group(process)
                    except (OSError, ValueError, subprocess.TimeoutExpired, WorklogError) as exc:
                        cleanup_error = str(exc)
                        monitor.stop("cleanup_error", cleanup_error)
                outcome = monitor.finish()
                outcome["cleanup_error"] = cleanup_error
                if received_signal:
                    monitor.stop("interrupted")
                if monitor.reason:
                    rc = {"max_seconds": 124, "interrupted": 130}.get(monitor.reason, 125)
                    print(
                        f"worklog: budget stopped: {monitor.reason}"
                        + (f" ({monitor.error})" if monitor.error else ""),
                        file=sys.stderr,
                    )
                if received_signal:
                    rc = 128 + received_signal
                outcome.update(
                    launched=launched,
                    child_rc=process.returncode if process else None,
                    error=monitor.error,
                    signal=received_signal,
                )
                extra.update(budget_outcome=outcome, stop_reason=monitor.reason)
            append(
                log, "cmd", task, id=run_id, kind=args.kind, label=label, argv=command, start=stamp(begin),
                end=stamp(now()), rc=rc, elapsed_s=round(time.monotonic() - tick, 3), sims=args.sims, **extra,
            )  # fmt: skip
        return rc
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--log", help="log file (default: RE_WORKLOG, contract worklog.path, ./worklog/pilot.jsonl)"
    )
    sub = parser.add_subparsers(dest="action", required=True)

    def timed(p: argparse.ArgumentParser) -> None:
        p.add_argument("--at", help="ISO 8601 time with offset; default now")
        p.add_argument("--basis", choices=BASES, default="measured")
        p.add_argument("--source", help="where a reconstructed time came from")

    p = sub.add_parser("start", help="open a task")
    p.add_argument("task")
    p.add_argument("--title", default="")
    timed(p)
    p = sub.add_parser("phase", help="start or stop a sim, analysis or other phase")
    p.add_argument("kind", choices=KINDS)
    p.add_argument("step", choices=("start", "stop"))
    p.add_argument("--task")
    timed(p)
    p = sub.add_parser("run", help="run a command and record its wall time and exit code")
    p.add_argument("--kind", choices=KINDS, default="sim")
    p.add_argument("--task")
    p.add_argument("--label")
    p.add_argument("--sims", type=int, help="simulator runs inside the command, if known")
    p.add_argument("--max-seconds", type=float, help="optional positive finite wall-time limit, in seconds")
    p.add_argument(
        "--max-disk-mib", type=float, help="optional positive finite aggregate allocated-disk limit, in MiB"
    )
    p.add_argument("--disk-root", help="directory to monitor; requires --max-disk-mib")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("end", help="close a task")
    p.add_argument("--task")
    p.add_argument("--status", choices=STATUSES, required=True)
    p.add_argument("--outputs", nargs="*", default=[], help="artifact paths, commits, reports")
    p.add_argument("--pr", action="append", default=[], help="REF or REF=STATE (opened, merged, closed)")
    timed(p)
    p = sub.add_parser("pr", help="record a PR state for a task")
    p.add_argument("task")
    p.add_argument("ref")
    p.add_argument("--state", choices=PR_STATES, required=True)
    p = sub.add_parser("rate", help="the human's verdict and estimate for a task")
    p.add_argument("task")
    p.add_argument("--used", choices=USED, required=True)
    p.add_argument("--human-hours-estimate", type=float, help="hours the task would take without the agent")
    p.add_argument(
        "--review-hours", type=float, help="hours the human spent on the task (steering and review)"
    )
    p = sub.add_parser("report", help="one row per task, with totals")
    p.add_argument("--format", choices=("md", "csv", "jsonl"), default="md")
    p.add_argument("--task", action="append")
    p.add_argument("--out")
    args = parser.parse_args(argv)

    try:
        log = resolve_log(args.log)
        if args.action == "run":
            return cmd_run(args, log)
        if args.action == "report":
            rows = build_rows(read_events(log), args.task)
            if args.format == "jsonl":
                text = "".join(json.dumps(r, default=str) + "\n" for r in rows)
            else:
                rows = rows + [totals(rows)] if rows else rows
                zone = now().tzname() or "local time"
                text = render_csv(rows) if args.format == "csv" else render_markdown(rows, zone)
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
            return 0
        at = parse_time(args.at) if getattr(args, "at", None) else None
        extra = {"basis": args.basis, "source": args.source} if hasattr(args, "basis") else {}
        if args.action == "start":
            if args.task in open_tasks(read_events(log)):
                raise WorklogError(f"task {args.task!r} is already open")
            append(log, "start", args.task, at, title=args.title, **extra)
        elif args.action == "phase":
            append(log, "phase", pick_task(log, args.task), at, kind=args.kind, action=args.step, **extra)
        elif args.action == "end":
            task = pick_task(log, args.task)
            for item in args.pr:
                ref, _, state = item.partition("=")
                if (state or "opened") not in PR_STATES:
                    raise WorklogError(f"PR state must be one of {PR_STATES}: {item!r}")
                append(log, "pr", task, at, ref=ref, state=state or "opened", **extra)
            append(log, "end", task, at, status=args.status, outputs=args.outputs, **extra)
        elif args.action == "pr":
            append(log, "pr", args.task, ref=args.ref, state=args.state)
        elif args.action == "rate":
            if args.task not in fold(read_events(log)):
                raise WorklogError(f"no such task: {args.task!r}")
            append(
                log, "rate", args.task, used=args.used, human_hours_estimate=args.human_hours_estimate,
                review_hours=args.review_hours,
            )  # fmt: skip
        return 0
    except WorklogError as exc:
        print(f"worklog: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
