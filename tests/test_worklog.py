import errno
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills/earned-autonomy/scripts/worklog.py"


def load():
    spec = importlib.util.spec_from_file_location("worklog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wl = load()


@pytest.fixture(autouse=True)
def outer_test_timeout():
    def expired(signum, frame):
        raise TimeoutError("synthetic worklog test exceeded its outer 12-second deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 12)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RE_WORKLOG", raising=False)
    monkeypatch.delenv("RE_WORKLOG_TASK", raising=False)
    monkeypatch.delenv("RE_AGENT_CONTRACT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / "log.jsonl"


def call(log, *args):
    return wl.main(["--log", str(log), *args])


def events(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def rows(log):
    return {r["task"]: r for r in wl.build_rows(wl.read_events(log), None)}


def test_run_records_wall_time_and_exit_code(log):
    sleeper = log.parent / "sleep.py"
    sleeper.write_text("import time\ntime.sleep(0.3)\n")
    failure = log.parent / "failure.py"
    failure.write_text("raise SystemExit(3)\n")
    assert call(log, "start", "t1") == 0
    rc = call(log, "run", "--kind", "sim", "--sims", "2", "--", sys.executable, str(sleeper))
    assert rc == 0
    assert call(log, "run", "--label", "bad", "--", sys.executable, str(failure)) == 3
    done = [e for e in events(log) if e["event"] == "cmd"]
    assert done[0]["elapsed_s"] >= 0.3 and done[0]["rc"] == 0 and done[0]["sims"] == 2
    assert done[1]["rc"] == 3 and done[1]["label"] == "bad"
    assert all("budget" not in event and "budget_outcome" not in event for event in events(log))
    row = rows(log)["t1"]
    assert row["commands"] == 2 and row["failed_commands"] == 1 and row["simulations"] == 2
    assert row["simulations_unknown"] == 1
    assert row["sim_h"] * 3600 >= 0.3


def test_missing_executable_is_127(log):
    call(log, "start", "t1")
    assert call(log, "run", "--", "no-such-binary-xyz") == 127


def interval(log, kind, start, end, basis="measured"):
    wl.append(log, "interval", "t", kind=kind, start=start, end=end, basis=basis)


def test_report_math_union_and_exclusive_phases(log):
    call(log, "start", "t", "--at", "2026-10-15T10:00:00+02:00")
    interval(log, "sim", "2026-10-15T10:00:00+02:00", "2026-10-15T11:00:00+02:00")
    interval(log, "sim", "2026-10-15T10:30:00+02:00", "2026-10-15T11:30:00+02:00")
    interval(log, "other", "2026-10-15T11:00:00+02:00", "2026-10-15T12:00:00+02:00")
    call(
        log,
        "end",
        "--status",
        "done",
        "--at",
        "2026-10-15T14:00:00+02:00",
        "--pr",
        "o/r#1=merged",
        "--pr",
        "o/r#2",
    )
    row = rows(log)["t"]
    assert row["total_h"] == pytest.approx(4.0)
    assert row["sim_h"] == pytest.approx(1.5)
    assert row["other_h"] == pytest.approx(0.5)
    assert row["analysis_h"] == pytest.approx(2.0)
    assert row["analysis_basis"] == "inferred" and row["sim_basis"] == "measured"
    assert (row["prs_opened"], row["prs_merged"]) == (2, 1)


def test_analysis_commands_alone_keep_the_rest_as_analysis(log):
    call(log, "start", "t", "--at", "2026-10-15T10:00:00+02:00")
    wl.append(log, "cmd", "t", id="p", kind="analysis", label="plot", argv=["python"],
              start="2026-10-15T10:00:00+02:00", end="2026-10-15T10:00:01+02:00", rc=0, elapsed_s=1.0)  # fmt: skip
    call(log, "end", "--status", "done", "--at", "2026-10-15T11:00:00+02:00")
    row = rows(log)["t"]
    assert row["analysis_h"] == pytest.approx(1.0) and row["analysis_basis"] == "inferred"


def test_explicit_analysis_phase_and_unaccounted(log):
    call(log, "start", "t", "--at", "2026-10-15T10:00:00+02:00")
    interval(log, "sim", "2026-10-15T10:00:00+02:00", "2026-10-15T11:00:00+02:00")
    call(log, "phase", "analysis", "start", "--at", "2026-10-15T10:30:00+02:00")
    call(log, "phase", "analysis", "stop", "--at", "2026-10-15T12:00:00+02:00")
    call(log, "end", "--status", "done", "--at", "2026-10-15T13:00:00+02:00")
    row = rows(log)["t"]
    assert row["analysis_h"] == pytest.approx(1.0)
    assert row["analysis_basis"] == "measured"
    assert row["unaccounted_h"] == pytest.approx(1.0)


def test_inferred_input_makes_number_inferred_and_open_command_is_excluded(log):
    call(log, "start", "t", "--at", "2026-10-15T10:00:00+02:00")
    interval(log, "sim", "2026-10-15T10:00:00+02:00", "2026-10-15T10:30:00+02:00", basis="inferred")
    wl.append(log, "cmd_start", "t", id="x", kind="sim", label="killed", argv=["flow"])
    call(log, "end", "--status", "partial", "--at", "2026-10-15T11:00:00+02:00", "--basis", "inferred")
    row = rows(log)["t"]
    assert row["sim_basis"] == "inferred" and row["total_basis"] == "inferred"
    assert row["unterminated_commands"] == 1 and row["commands"] == 0


def test_rate_fields_and_totals(log, capsys):
    for name in ("a", "b"):
        call(log, "start", name, "--at", "2026-10-15T10:00:00+02:00")
        call(log, "end", "--task", name, "--status", "done", "--at", "2026-10-15T12:00:00+02:00")
    assert (
        call(log, "rate", "a", "--used", "merged", "--human-hours-estimate", "10", "--review-hours", "1.5")
        == 0
    )
    assert call(log, "rate", "zzz", "--used", "merged") == 2
    table = rows(log)
    assert table["a"]["est_hours_saved"] == pytest.approx(8.5)
    assert table["b"]["est_hours_saved"] is None
    total = wl.totals(list(table.values()))
    assert total["total_h"] == pytest.approx(4.0) and total["est_hours_saved"] == pytest.approx(8.5)
    assert total["used"] == "1 of 2 used"
    assert call(log, "report", "--format", "csv") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("task,title,start,end") and out[-1].startswith("TOTAL,")
    assert call(log, "report") == 0
    assert "| TOTAL |" in capsys.readouterr().out


def test_records_hold_facts_only(log):
    call(log, "start", "t")
    with pytest.raises(wl.WorklogError):
        wl.append(log, "end", "t", status="done", summary="great work")


def test_task_selection_fails_closed(log):
    assert call(log, "phase", "sim", "start") == 2
    call(log, "start", "a")
    call(log, "start", "b")
    assert call(log, "start", "a") == 2
    assert call(log, "phase", "sim", "start") == 2
    assert call(log, "phase", "sim", "start", "--task", "b") == 0
    assert call(log, "end", "--status", "done", "--at", "noon") == 2


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def test_refuses_a_log_that_git_would_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    with pytest.raises(wl.WorklogError, match="not ignored"):
        wl.append(repo / "notes/log.jsonl", "start", "t")
    (repo / ".gitignore").write_text("worklog/\n")
    wl.append(repo / "worklog/pilot.jsonl", "start", "t")
    assert (repo / "worklog/pilot.jsonl").is_file()


def test_default_log_is_ignored_in_this_repository(tmp_path):
    # A fixture Git repo carries this repository's .gitignore, so an export without .git still runs the check.
    repo = tmp_path / "ignore-check"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            f"core.excludesFile={os.devnull}",
            "check-ignore",
            "-q",
            str(wl.DEFAULT_LOG),
        ],
        check=False,
    )
    assert result.returncode == 0


def test_log_path_from_contract(log, tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "site.contract.yml").write_text("worklog:\n  path: private/pilot.jsonl\n")
    assert wl.resolve_log(None) == tmp_path / "private/pilot.jsonl"
    (tmp_path / "site.contract.yml").write_text("worklog:\n  path:\n")
    assert wl.resolve_log(None) == wl.DEFAULT_LOG


def test_contract_example_and_schema_carry_the_key():
    yaml = pytest.importorskip("yaml")
    example = yaml.safe_load((ROOT / "site.contract.example.yml").read_text())
    assert example["worklog"] == {"path": None}
    schema = json.loads((ROOT / "site.contract.schema.json").read_text())
    assert "worklog" in schema["properties"]


SYNTHETIC = """\
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

mode, registry, marker, root = sys.argv[1:]
with open(registry, "a") as handle:
    handle.write(str(os.getpgrp()) + "\\n")
# No helper or workload starts until its group has been registered.
pids = [os.getpid()]
if mode in ("helper", "orphan"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, __file__, "sleep", registry, marker + ".helper", root])
    pids.append(child.pid)
    deadline = time.monotonic() + 3
    while not Path(marker + ".helper").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
Path(marker).write_text(json.dumps(pids))
if mode == "quick":
    raise SystemExit(0)
if mode == "fail":
    raise SystemExit(7)
if mode == "orphan":
    raise SystemExit(0)
if mode == "grow":
    Path(root).mkdir(parents=True, exist_ok=True)
    with (Path(root) / "allocated").open("wb", buffering=0) as handle:
        for _ in range(64):
            handle.write(b"x" * 65536)
            os.fsync(handle.fileno())
            time.sleep(0.02)
else:
    time.sleep(8)
"""


def live_pids_in_groups(groups):
    live = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) in groups and fields[0] != "Z":
            live.append(int(entry.name))
    return live


@contextmanager
def owned_processes(tmp_path):
    registry = tmp_path / "owned-groups"
    registry.write_text("")
    children = []
    try:
        yield registry, children
    finally:
        groups = {int(line) for line in registry.read_text().splitlines()}
        assert os.getpgrp() not in groups
        for group in groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in children:
            child.wait(timeout=3)
        deadline = time.monotonic() + 3
        while live_pids_in_groups(groups) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not live_pids_in_groups(groups)


def start_owned(argv, registry, children):
    bootstrap = registry.parent / "gated-launch.py"
    bootstrap.write_text(
        "import os, sys\n"
        "fd = int(sys.argv[1])\n"
        "if os.read(fd, 1) != b'x':\n    raise SystemExit(1)\n"
        "os.close(fd)\n"
        "os.execv(sys.argv[2], sys.argv[2:])\n"
    )
    read_fd, write_fd = os.pipe()
    try:
        child = subprocess.Popen(
            [sys.executable, str(bootstrap), str(read_fd), *argv],
            pass_fds=(read_fd,),
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        children.append(child)
        with registry.open("a") as handle:
            handle.write(f"{child.pid}\n")
        os.write(write_fd, b"x")
    finally:
        os.close(read_fd)
        os.close(write_fd)
    return child


def budget_command(log, script, mode, registry, marker, root, *flags):
    return [
        sys.executable,
        str(SCRIPT),
        "--log",
        str(log),
        "run",
        "--kind",
        "analysis",
        *flags,
        "--",
        sys.executable,
        str(script),
        mode,
        str(registry),
        str(marker),
        str(root),
    ]


@pytest.fixture
def synthetic(log):
    script = log.parent / "synthetic.py"
    script.write_text(SYNTHETIC)
    call(log, "start", "budget")
    return script, log.parent / "marker", log.parent / "output"


@pytest.mark.parametrize(
    "flags",
    [
        ["--max-seconds", "0"],
        ["--max-seconds", "-1"],
        ["--max-seconds", "nan"],
        ["--max-seconds", "inf"],
        ["--max-seconds=-inf"],
        ["--max-disk-mib", "0", "--disk-root", "."],
        ["--max-disk-mib", "nan", "--disk-root", "."],
        ["--max-disk-mib", "inf", "--disk-root", "."],
        ["--max-disk-mib", "1"],
        ["--disk-root", "."],
        ["--max-disk-mib", "-1", "--disk-root", "."],
        ["--max-disk-mib=-inf", "--disk-root", "."],
        ["--max-disk-mib", "1", "--disk-root", ""],
    ],
)
def test_budget_invalid_limits_refuse_before_command(log, synthetic, flags):
    script, marker, root = synthetic
    with owned_processes(log.parent) as (registry, children):
        child = start_owned(
            budget_command(log, script, "quick", registry, marker, root, *flags), registry, children
        )
        _, err = child.communicate(timeout=6)
        assert child.returncode == 2
        assert b"positive finite" in err or b"requires" in err
        assert not marker.exists()
        assert [e["event"] for e in events(log)] == ["start"]


def receipt(log):
    return [e for e in events(log) if e["event"] == "cmd"][-1]


def test_budget_initial_overage_never_launches(log, synthetic):
    script, marker, root = synthetic
    root.mkdir()
    (root / "existing").write_bytes(b"x" * 131072)
    with owned_processes(log.parent) as (registry, children):
        argv = budget_command(
            log, script, "quick", registry, marker, root, "--max-disk-mib", "0.0625", "--disk-root", str(root)
        )
        child = start_owned(argv, registry, children)
        child.communicate(timeout=6)
        assert not marker.exists()
        assert child.returncode != 0
        done = receipt(log)
        assert done["stop_reason"] == "max_disk_mib"
        assert done["budget"]["max_disk_mib"] == 0.0625
        assert done["budget_outcome"]["launched"] is False
        assert done["budget_outcome"]["max_allocated_bytes"] >= 131072
        assert rows(log)["budget"]["failed_commands"] == 1


@pytest.mark.parametrize("mode,expected_rc", [("quick", 0), ("fail", 7)])
def test_budget_tiny_job_and_missing_root(log, synthetic, mode, expected_rc):
    script, marker, root = synthetic
    root = root / "not" / "created"
    with owned_processes(log.parent) as (registry, children):
        argv = budget_command(
            log,
            script,
            mode,
            registry,
            marker,
            root,
            "--max-seconds",
            "2",
            "--max-disk-mib",
            "1",
            "--disk-root",
            str(root),
        )
        child = start_owned(argv, registry, children)
        child.communicate(timeout=6)
        assert child.returncode == expected_rc
        assert marker.exists() and not root.exists()
        done = receipt(log)
        assert done["rc"] == expected_rc
        assert done.get("stop_reason") is None
        assert done["argv"] == argv[argv.index("--") + 1 :]
        assert done["budget_outcome"]["max_allocated_bytes"] == 0
        assert done["budget_outcome"]["samples"] >= 2
        assert done["budget_outcome"]["launched"] is True
        assert events(log)[1]["budget"] == done["budget"]
        assert wl.fold(events(log))["budget"]["cmds"][done["id"]]["budget_outcome"] == done["budget_outcome"]


def test_budget_real_growth_stops_aggregate_allocation(log, synthetic):
    script, marker, root = synthetic
    with owned_processes(log.parent) as (registry, children):
        argv = budget_command(
            log, script, "grow", registry, marker, root, "--max-disk-mib", "0.25", "--disk-root", str(root)
        )
        child = start_owned(argv, registry, children)
        child.communicate(timeout=6)
        assert marker.exists() and child.returncode != 0
        done = receipt(log)
        assert done["stop_reason"] == "max_disk_mib"
        assert done["budget_outcome"]["max_allocated_bytes"] >= 262144
        assert done["elapsed_s"] >= 0.05
        groups = {int(line) for line in registry.read_text().splitlines()}
        assert not live_pids_in_groups(groups)


@pytest.mark.parametrize("mode", ["helper", "orphan"])
def test_budget_runtime_stops_owned_group_and_preserves_unrelated(log, synthetic, mode):
    script, marker, root = synthetic
    with owned_processes(log.parent) as (registry, children):
        unrelated_marker = log.parent / "unrelated"
        unrelated = start_owned(
            [sys.executable, str(script), "helper", str(registry), str(unrelated_marker), str(root)],
            registry,
            children,
        )
        deadline = time.monotonic() + 3
        while not unrelated_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert unrelated_marker.exists()
        unrelated_pids = json.loads(unrelated_marker.read_text())
        argv = budget_command(log, script, mode, registry, marker, root, "--max-seconds", "0.5")
        child = start_owned(argv, registry, children)
        tick = time.monotonic()
        child.communicate(timeout=6)
        wall = time.monotonic() - tick
        assert marker.exists() and child.returncode != 0
        owned_pids = json.loads(marker.read_text())
        assert len(owned_pids) == 2
        assert not set(owned_pids) & set(live_pids_in_groups({owned_pids[0]}))
        assert unrelated.poll() is None
        assert set(unrelated_pids) <= set(live_pids_in_groups({unrelated.pid}))
        done = receipt(log)
        assert done["stop_reason"] == "max_seconds" and done["rc"] != 0
        assert 0.5 <= done["elapsed_s"] <= wall
        span = (wl.parse_time(done["end"]) - wl.parse_time(done["start"])).total_seconds()
        assert span == pytest.approx(done["elapsed_s"], abs=0.01)


def test_budget_disk_accounting_is_allocated_unique_and_does_not_follow_links(log, synthetic):
    script, marker, root = synthetic
    root.mkdir()
    outside = log.parent / "outside"
    outside.mkdir()
    (outside / "large").write_bytes(b"x" * 2097152)
    sparse = root / "sparse"
    with sparse.open("wb") as handle:
        handle.truncate(100 * 1048576)
    data = root / "data"
    data.write_bytes(b"x" * 65536)
    os.link(data, root / "hardlink")
    (root / "external").symlink_to(outside, target_is_directory=True)
    (root / "internal").symlink_to(data)
    expected = sum(
        path.lstat().st_blocks * 512 for path in [root, sparse, data, root / "external", root / "internal"]
    )
    with owned_processes(log.parent) as (registry, children):
        argv = budget_command(
            log, script, "quick", registry, marker, root, "--max-disk-mib", "0.1", "--disk-root", str(root)
        )
        child = start_owned(argv, registry, children)
        child.communicate(timeout=6)
        assert child.returncode == 0
        assert receipt(log)["budget_outcome"]["max_allocated_bytes"] == expected


@pytest.mark.parametrize("fault", ["file", "symlink", "denied"])
def test_budget_unknown_or_non_directory_root_fails_closed(log, synthetic, fault):
    script, marker, root = synthetic
    if fault == "file":
        root.write_text("not a directory")
    elif fault == "symlink":
        root.symlink_to(log.parent, target_is_directory=True)
    else:
        root.mkdir()
        root.chmod(0)
    try:
        with owned_processes(log.parent) as (registry, children):
            argv = budget_command(
                log, script, "quick", registry, marker, root, "--max-disk-mib", "1", "--disk-root", str(root)
            )
            child = start_owned(argv, registry, children)
            _, err = child.communicate(timeout=6)
            assert child.returncode != 0 and not marker.exists()
            done = receipt(log)
            assert done["stop_reason"] == "monitor_error"
            assert done["budget_outcome"]["error"]
            assert b"disk" in err and b"Traceback" not in err
    finally:
        if fault == "denied":
            root.chmod(0o700)


def test_budget_disk_observer_is_armed_before_popen(log, synthetic, monkeypatch):
    _, _, root = synthetic
    observed = []
    real_sample = wl.allocated_bytes
    real_popen = wl.subprocess.Popen

    def sample(path):
        value = real_sample(path)
        observed.append((value, threading.current_thread()))
        return value

    def launch(*args, **kwargs):
        assert observed, "Popen ran before the observer completed its first sample"
        assert observed[0][1] is not threading.current_thread() and observed[0][1].is_alive()
        raise FileNotFoundError("synthetic launch refusal")

    monkeypatch.setattr(wl, "allocated_bytes", sample)
    monkeypatch.setattr(wl.subprocess, "Popen", launch)
    # Avoid intercepting the unrelated git checks in append.
    monkeypatch.setattr(wl, "ensure_not_committable", lambda _: None)
    assert call(log, "run", "--max-disk-mib", "1", "--disk-root", str(root), "--", "missing") == 127
    assert receipt(log)["budget_outcome"]["samples"] >= 1
    monkeypatch.setattr(wl.subprocess, "Popen", real_popen)


def test_budget_live_disk_read_error_stops_group(log, synthetic, monkeypatch, capsys):
    script, marker, root = synthetic
    real_sample = wl.allocated_bytes

    def sample(path):
        if marker.exists():
            raise PermissionError("synthetic disk read denied")
        return real_sample(path)

    monkeypatch.setattr(wl, "allocated_bytes", sample)
    with owned_processes(log.parent) as (registry, children):
        assert (
            call(
                log,
                "run",
                "--max-disk-mib",
                "1",
                "--disk-root",
                str(root),
                "--max-seconds",
                "2",
                "--",
                sys.executable,
                str(script),
                "sleep",
                str(registry),
                str(marker),
                str(root),
            )
            != 0
        )
        assert marker.exists()
        done = receipt(log)
        assert done["stop_reason"] == "monitor_error"
        assert "synthetic disk read denied" in done["budget_outcome"]["error"]
        assert "disk" in capsys.readouterr().err
        groups = {int(line) for line in registry.read_text().splitlines()}
        assert not live_pids_in_groups(groups)


def test_budget_inspection_error_still_kills_and_reaps_owned_group(log, synthetic, monkeypatch):
    script, marker, root = synthetic
    signals = []
    real_killpg = os.killpg
    real_live = wl.group_live

    def live(group):
        if marker.exists():
            raise PermissionError("synthetic process inspection denied")
        return real_live(group)

    def killpg(group, sig):
        signals.append((group, sig))
        return real_killpg(group, sig)

    monkeypatch.setattr(wl, "group_live", live)
    monkeypatch.setattr(wl.os, "killpg", killpg)
    with owned_processes(log.parent) as (registry, children):
        rc = call(
            log,
            "run",
            "--max-seconds",
            "0.3",
            "--",
            sys.executable,
            str(script),
            "helper",
            str(registry),
            str(marker),
            str(root),
        )
        assert marker.exists() and rc != 0
        pids = json.loads(marker.read_text())
        assert (pids[0], signal.SIGKILL) in signals
        assert not live_pids_in_groups({pids[0]})
        done = receipt(log)
        assert "synthetic process inspection denied" in done["budget_outcome"]["error"]
        assert done["budget_outcome"]["child_rc"] == -signal.SIGKILL


def test_budget_missing_root_below_symlink_is_unknown(log, synthetic):
    script, marker, root = synthetic
    root.symlink_to(log.parent, target_is_directory=True)
    root = root / "missing"
    with owned_processes(log.parent) as (registry, children):
        child = start_owned(
            budget_command(
                log, script, "quick", registry, marker, root, "--max-disk-mib", "1", "--disk-root", str(root)
            ),
            registry,
            children,
        )
        child.communicate(timeout=6)
        assert child.returncode != 0 and not marker.exists()
        assert receipt(log)["stop_reason"] == "monitor_error"


def test_budget_missing_process_observation_is_not_a_missing_executable(log, synthetic, monkeypatch):
    script, marker, root = synthetic

    def unknown(group):
        raise FileNotFoundError("synthetic process table unavailable")

    monkeypatch.setattr(wl, "group_live", unknown)
    with owned_processes(log.parent) as (registry, children):
        rc = call(
            log,
            "run",
            "--max-seconds",
            "2",
            "--",
            sys.executable,
            str(script),
            "quick",
            str(registry),
            str(marker),
            str(root),
        )
        assert marker.exists() and rc != 0
        done = receipt(log)
        assert done["stop_reason"] == "monitor_error"
        assert "synthetic process table unavailable" in done["budget_outcome"]["error"]


def test_budget_cleanup_error_remains_visible_beside_monitor_error(log, synthetic, monkeypatch):
    script, marker, root = synthetic
    real_stop = wl.stop_group
    real_sample = wl.allocated_bytes

    def sample(path):
        if marker.exists():
            raise PermissionError("synthetic disk error")
        return real_sample(path)

    def cleanup(process):
        real_stop(process)
        raise wl.WorklogError("synthetic cleanup verification failure")

    monkeypatch.setattr(wl, "allocated_bytes", sample)
    monkeypatch.setattr(wl, "stop_group", cleanup)
    with owned_processes(log.parent) as (registry, children):
        rc = call(
            log,
            "run",
            "--max-seconds",
            "2",
            "--max-disk-mib",
            "1",
            "--disk-root",
            str(root),
            "--",
            sys.executable,
            str(script),
            "sleep",
            str(registry),
            str(marker),
            str(root),
        )
        assert rc != 0
        done = receipt(log)
        assert done["stop_reason"] == "monitor_error"
        assert "synthetic disk error" in done["budget_outcome"]["error"]
        assert "synthetic cleanup verification failure" in done["budget_outcome"]["cleanup_error"]


def test_budget_unlaunched_simulation_does_not_inflate_report(log, synthetic):
    script, marker, root = synthetic
    root.mkdir()
    (root / "existing").write_bytes(b"x" * 131072)
    with owned_processes(log.parent) as (registry, children):
        assert (
            call(
                log,
                "run",
                "--kind",
                "sim",
                "--sims",
                "3",
                "--max-disk-mib",
                "0.0625",
                "--disk-root",
                str(root),
                "--",
                sys.executable,
                str(script),
                "quick",
                str(registry),
                str(marker),
                str(root),
            )
            != 0
        )
        assert not marker.exists()
        row = rows(log)["budget"]
        assert row["simulations"] == 0
        assert row["sim_h"] == 0
        assert row["failed_commands"] == 1 and row["unterminated_commands"] == 0
        assert receipt(log)["budget_outcome"]["launched"] is False


def test_budget_runtime_is_prearmed_during_initial_disk_scan(log, synthetic, monkeypatch):
    _, marker, root = synthetic
    release = threading.Event()
    observers = []

    def slow_sample(path):
        observers.append(threading.current_thread())
        release.wait(3)
        return 0

    monkeypatch.setattr(wl, "allocated_bytes", slow_sample)
    try:
        assert (
            call(
                log,
                "run",
                "--max-seconds",
                "0.02",
                "--max-disk-mib",
                "1",
                "--disk-root",
                str(root),
                "--",
                "never-launch-this-command",
            )
            == 124
        )
        done = receipt(log)
        assert done["stop_reason"] == "max_seconds"
        assert done["budget_outcome"]["launched"] is False
        assert done["budget_outcome"]["max_allocated_bytes"] is None
        assert not marker.exists()
    finally:
        release.set()
        for observer in observers:
            observer.join(timeout=3)
            assert not observer.is_alive()


def intercept_entry_stat(monkeypatch, before_stat):
    real_scandir = os.scandir

    class Entry:
        def __init__(self, entry):
            self.entry = entry
            self.name = entry.name

        def stat(self, *, follow_symlinks):
            before_stat(self.entry)
            return self.entry.stat(follow_symlinks=follow_symlinks)

    @contextmanager
    def scandir(fd):
        with real_scandir(fd) as entries:
            yield (Entry(entry) for entry in entries)

    monkeypatch.setattr(wl.os, "scandir", scandir)


def test_budget_vanished_entry_between_scan_and_stat_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "observed"
    root.mkdir()
    vanished = root / "vanished"
    vanished.write_bytes(b"x" * 4096)

    def remove(entry):
        if entry.name == vanished.name:
            vanished.unlink()

    intercept_entry_stat(monkeypatch, remove)
    monitor = wl.BudgetMonitor(wl.BudgetSpec(None, 1, str(root)), time.monotonic())
    monitor.sample()
    assert not vanished.exists()
    assert monitor.reason is None, monitor.error
    assert monitor.samples == 1
    assert monitor.maximum == root.stat().st_blocks * 512


def test_budget_vanished_directory_between_stat_and_open_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "observed"
    root.mkdir()
    vanished = root / "vanished"
    vanished.mkdir()
    real_open = os.open

    def open_after_removal(path, flags, **kwargs):
        if path == vanished.name and "dir_fd" in kwargs:
            vanished.rmdir()
        return real_open(path, flags, **kwargs)

    monkeypatch.setattr(wl.os, "open", open_after_removal)
    monitor = wl.BudgetMonitor(wl.BudgetSpec(None, 1, str(root)), time.monotonic())
    monitor.sample()
    assert not vanished.exists()
    assert monitor.reason is None, monitor.error
    assert monitor.samples == 1
    assert monitor.maximum == root.stat().st_blocks * 512


@pytest.mark.parametrize("seam", ["stat", "open"])
@pytest.mark.parametrize("error", [errno.EACCES, errno.EIO, errno.ENOTDIR, errno.ELOOP])
def test_budget_entry_errors_other_than_absence_fail_closed(tmp_path, monkeypatch, seam, error):
    root = tmp_path / "observed"
    root.mkdir()
    (root / "entry").mkdir()

    def fail():
        raise OSError(error, "synthetic entry observation failure")

    if seam == "stat":
        intercept_entry_stat(monkeypatch, lambda _: fail())
    else:
        real_open = os.open

        def denied(path, flags, **kwargs):
            if path == "entry" and "dir_fd" in kwargs:
                fail()
            return real_open(path, flags, **kwargs)

        monkeypatch.setattr(wl.os, "open", denied)
    monitor = wl.BudgetMonitor(wl.BudgetSpec(None, 1, str(root)), time.monotonic())
    monitor.sample()
    assert monitor.reason == "monitor_error"
    assert "synthetic entry observation failure" in monitor.error
    assert monitor.samples == 0


def test_budget_bounded_real_file_and_directory_churn(tmp_path):
    root = tmp_path / "disposable-churn"
    root.mkdir()
    stable = root / "stable"
    stable.write_bytes(b"x" * 4096)
    started = threading.Event()
    finish = threading.Event()
    errors = []
    completed = []

    def churn():
        try:
            for number in range(500):
                directory = root / f"temporary-{number}"
                directory.mkdir()
                data = directory / "data"
                data.write_bytes(b"x" * 4096)
                started.set()
                data.unlink()
                directory.rmdir()
                completed.append(number)
                if finish.wait(0.001):
                    break
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=churn)
    monitor = wl.BudgetMonitor(wl.BudgetSpec(None, 10, str(root)), time.monotonic())
    worker.start()
    try:
        assert started.wait(2)
        for _ in range(300):
            monitor.sample()
            assert monitor.reason is None, monitor.error
            time.sleep(0.001)
    finally:
        finish.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert not errors
    assert completed and monitor.samples == 300
    assert stable.read_bytes() == b"x" * 4096


SIGNAL_LAUNCHER = """\
import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path

script, mode, marker, *argv = sys.argv[1:]
marker = Path(marker)
spec = importlib.util.spec_from_file_location("worklog_signal_test", script)
wl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wl)
signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
previous = {sig: signal.getsignal(sig) for sig in signals}
real_stop = wl.stop_group
real_popen = wl.subprocess.Popen
real_append = wl.append
real_arm = wl.BudgetMonitor.arm

def arm(self):
    real_arm(self)
    if mode == "locked":
        with self.lock:
            os.kill(os.getpid(), signal.SIGTERM)

def stop(process):
    marker.with_suffix(".cleanup").write_text("ready")
    real_stop(process)

def launch(*args, **kwargs):
    process = real_popen(*args, **kwargs)
    if kwargs.get("start_new_session"):
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not marker.exists():
            raise RuntimeError("owned synthetic child never became ready")
        os.kill(os.getpid(), signal.SIGINT)
    return process

def append(*args, **kwargs):
    if args[1] == "cmd" and mode == "receipt":
        marker.with_suffix(".receipt").write_text("ready")
        time.sleep(0.25)
    return real_append(*args, **kwargs)

wl.stop_group = stop
wl.append = append
wl.BudgetMonitor.arm = arm
if mode == "launch":
    wl.subprocess.Popen = launch
try:
    rc = wl.main(argv)
finally:
    marker.with_suffix(".handlers").write_text(json.dumps([
        signal.getsignal(sig) == previous[sig] for sig in signals
    ]))
raise SystemExit(rc)
"""


def wait_for_file(path):
    deadline = time.monotonic() + 3
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), f"synthetic process did not create {path}"


@pytest.mark.parametrize(
    "sent,mode",
    [
        (signal.SIGTERM, "normal"),
        (signal.SIGHUP, "normal"),
        (signal.SIGINT, "repeated"),
        (signal.SIGINT, "launch"),
        (signal.SIGTERM, "receipt"),
        (signal.SIGTERM, "budget_first"),
    ],
)
def test_budget_launcher_signals_close_receipt_and_owned_group(log, synthetic, sent, mode):
    script, marker, root = synthetic
    launcher = log.parent / "signal-launcher.py"
    launcher.write_text(SIGNAL_LAUNCHER)
    with owned_processes(log.parent) as (registry, children):
        unrelated_marker = log.parent / "unrelated"
        unrelated = start_owned(
            [sys.executable, str(script), "helper", str(registry), str(unrelated_marker), str(root)],
            registry,
            children,
        )
        wait_for_file(unrelated_marker)
        unrelated_pids = json.loads(unrelated_marker.read_text())
        limit = "0.3" if mode == "budget_first" else "4"
        argv = budget_command(log, script, "helper", registry, marker, root, "--max-seconds", limit)
        child = start_owned(
            [sys.executable, str(launcher), str(SCRIPT), mode, str(marker), *argv[2:]], registry, children
        )
        wait_for_file(marker)
        if mode == "budget_first":
            wait_for_file(marker.with_suffix(".cleanup"))
        if mode != "launch":
            os.kill(child.pid, sent)
        if mode == "repeated":
            wait_for_file(marker.with_suffix(".cleanup"))
            for _ in range(3):
                os.kill(child.pid, signal.SIGINT)
                time.sleep(0.04)
        if mode == "receipt":
            wait_for_file(marker.with_suffix(".receipt"))
            os.kill(child.pid, signal.SIGINT)
        child.wait(timeout=6)
        assert child.returncode == 128 + sent
        done = receipt(log)
        assert done["stop_reason"] == ("max_seconds" if mode == "budget_first" else "interrupted")
        assert done["rc"] == 128 + sent
        assert done["budget_outcome"]["signal"] == sent
        assert done["budget_outcome"]["child_rc"] == -signal.SIGKILL
        assert done["budget_outcome"]["launched"] is True
        assert rows(log)["budget"]["unterminated_commands"] == 0
        owned_pids = json.loads(marker.read_text())
        assert not live_pids_in_groups({owned_pids[0]})
        assert unrelated.poll() is None
        assert set(unrelated_pids) <= set(live_pids_in_groups({unrelated.pid}))
        assert json.loads(marker.with_suffix(".handlers").read_text()) == [True, True, True]


@pytest.mark.parametrize("outcome", ["success", "missing", "monitor_error", "receipt_error"])
def test_budget_signal_handlers_restored_after_completion_or_error(log, synthetic, monkeypatch, outcome):
    _, _, root = synthetic
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {sig: signal.getsignal(sig) for sig in signals}

    def sentinel(signum, frame):
        raise AssertionError("restored signal handler must not run during budget setup")

    try:
        for sig in signals:
            signal.signal(sig, sentinel)
        command = [sys.executable, "-c", "pass"]
        if outcome == "missing":
            command = ["no-such-binary-xyz"]
        if outcome == "monitor_error":
            root.write_text("not a directory")
        if outcome == "receipt_error":
            real_append = wl.append

            def append(*args, **kwargs):
                if args[1] == "cmd":
                    raise RuntimeError("synthetic receipt error")
                return real_append(*args, **kwargs)

            monkeypatch.setattr(wl, "append", append)
        argv = ["run", "--max-seconds", "2"]
        if outcome == "monitor_error":
            argv += ["--max-disk-mib", "1", "--disk-root", str(root)]
        if outcome == "receipt_error":
            with pytest.raises(RuntimeError, match="synthetic receipt error"):
                call(log, *argv, "--", *command)
        else:
            assert (
                call(log, *argv, "--", *command)
                == {"success": 0, "missing": 127, "monitor_error": 125}[outcome]
            )
        assert all(signal.getsignal(sig) is sentinel for sig in signals)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def test_budget_signal_recording_does_not_lock_monitor_or_launch_child(log, synthetic):
    script, marker, root = synthetic
    launcher = log.parent / "signal-launcher.py"
    launcher.write_text(SIGNAL_LAUNCHER)
    with owned_processes(log.parent) as (registry, children):
        argv = budget_command(log, script, "quick", registry, marker, root, "--max-seconds", "2")
        child = start_owned(
            [sys.executable, str(launcher), str(SCRIPT), "locked", str(marker), *argv[2:]],
            registry,
            children,
        )
        child.communicate(timeout=6)
        assert child.returncode == 128 + signal.SIGTERM
        done = receipt(log)
        assert done["stop_reason"] == "interrupted"
        assert done["budget_outcome"]["signal"] == signal.SIGTERM
        assert done["budget_outcome"]["launched"] is False
        assert not marker.exists()
        assert json.loads(marker.with_suffix(".handlers").read_text()) == [True, True, True]


@pytest.mark.parametrize("code,expected", [("pass", 0), ("raise SystemExit(7)", 7), (None, 127)])
def test_no_budget_keeps_signal_handlers_and_exit_codes(log, monkeypatch, code, expected):
    calls = []
    real_signal = signal.signal

    def set_handler(sig, handler):
        calls.append(sig)
        return real_signal(sig, handler)

    call(log, "start", "unbounded")
    monkeypatch.setattr(wl.signal, "signal", set_handler)
    command = [sys.executable, "-c", code] if code else ["no-such-binary-xyz"]
    assert call(log, "run", "--", *command) == expected
    assert calls == []
    assert receipt(log)["rc"] == expected
    assert "budget_outcome" not in receipt(log)
