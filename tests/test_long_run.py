import hashlib
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/ert-ensemble-run/scripts/long_run.py"
spec = importlib.util.spec_from_file_location("long_run", SCRIPT)
long_run = importlib.util.module_from_spec(spec)
sys.modules["long_run"] = long_run
spec.loader.exec_module(long_run)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="long_run.py reads /proc")


def check(status: Path, capsys, *extra: str) -> tuple[int, dict]:
    capsys.readouterr()
    rc = long_run.main(["check", "--status", str(status), "--json", *extra])
    return rc, json.loads(capsys.readouterr().out)


def start(tmp_path: Path, capsys, name: str, *cmd: str) -> tuple[int, str]:
    capsys.readouterr()
    log = tmp_path / f"{name}.log"
    rc = long_run.main(
        ["start", "--status", str(tmp_path / "STATUS.md"), "--name", name, "--log", str(log), "--", *cmd]
    )
    return rc, capsys.readouterr().out.strip()


def wait_gone(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not Path(f"/proc/{pid}/cmdline").read_bytes():
                return
        except FileNotFoundError:
            return
        time.sleep(0.05)
    raise AssertionError(f"pid {pid} still running")


def test_start_check_alive_then_exited_and_end_once(tmp_path, capsys):
    status = tmp_path / "STATUS.md"
    code = "import time; print('step 1', flush=True); time.sleep(0.6); print('done', flush=True)"
    rc, out = start(tmp_path, capsys, "flow-base", sys.executable, "-c", code)
    assert rc == 0
    pid = int(out)

    time.sleep(0.2)
    rc, report = check(status, capsys)
    assert rc == 0
    (job,) = report["jobs"]
    assert (job["name"], job["pid"], job["state"]) == ("flow-base", pid, "RUNNING")
    assert job["log_tail"] == "step 1"

    wait_gone(pid)
    rc, report = check(status, capsys)
    assert rc == 1
    assert report["died_unnoticed"] == ["flow-base"]
    assert report["jobs"][0]["state"] == "EXITED"
    assert report["jobs"][0]["log_tail"] == "done"

    rc, report = check(status, capsys)
    assert rc == 0
    assert report["jobs"][0]["state"] == "ENDED"
    kinds = [line.split()[1] for line in status.read_text().splitlines()]
    assert kinds == ["START", "END"]


def test_refuses_duplicate_running_name(tmp_path, capsys):
    rc, out = start(tmp_path, capsys, "ert-prior", "sleep", "2")
    assert rc == 0
    pid = int(out)
    rc, out = start(tmp_path, capsys, "ert-prior", "sleep", "2")
    assert rc == 1
    assert out == ""
    assert len((tmp_path / "STATUS.md").read_text().splitlines()) == 1
    os.kill(pid, 9)
    wait_gone(pid)
    rc, out = start(tmp_path, capsys, "ert-prior", "true")
    assert rc == 0
    wait_gone(int(out))


def test_cwd_and_note(tmp_path, capsys):
    work = tmp_path / "model"
    work.mkdir()
    status = tmp_path / "STATUS.md"
    rc = long_run.main(
        [
            "start",
            "--status",
            str(status),
            "--name",
            "pwd",
            "--log",
            str(tmp_path / "pwd.log"),
            "--cwd",
            str(work),
            "--",
            "pwd",
        ]
    )
    assert rc == 0
    wait_gone(int(capsys.readouterr().out))
    assert (
        long_run.main(["note", "--status", str(status), "realization 3 of 10 ok,", "next check 14:10"]) == 0
    )
    records = long_run.read_status(status)
    assert records[1] == long_run.Note(records[1].time, "realization 3 of 10 ok, next check 14:10")
    assert (tmp_path / "pwd.log").read_text().strip() == str(work)


def test_record_round_trip():
    stamp = datetime.now().astimezone().replace(microsecond=0)
    records = [
        long_run.Start(
            stamp,
            "hm.iter-1",
            4242,
            "/scratch/my logs/run.log",
            ("ert", "ensemble_experiment", "a b.ert", "it's"),
        ),
        long_run.Note(stamp, "Free text: with = signs and 'quotes'"),
        long_run.End(stamp, "hm.iter-1", 4242),
    ]
    for rec in records:
        line = long_run.format_record(rec)
        assert long_run.parse_record(line) == rec
        assert long_run.format_record(long_run.parse_record(line)) == line
    assert long_run.format_record(records[0]).startswith(stamp.isoformat() + " START hm.iter-1 pid=4242 log=")


@pytest.mark.parametrize(
    "line",
    [
        "2026-01-01T10:00:00 NOTE no offset",
        "2026-01-01T10:00:00+01:00 RUN x pid=1 log=a cmd=b",
        "2026-01-01T10:00:00+01:00 START x pid=abc log=a cmd=b",
        "2026-01-01T10:00:00+01:00 START x pid=1 log=a",
        "2026-01-01T10:00:00+01:00 END x pid=1 state=done",
        "free text outside a NOTE",
    ],
)
def test_bad_lines_fail_closed(tmp_path, capsys, line):
    status = tmp_path / "STATUS.md"
    status.write_text(f"# Job status\n\n{line}\n")
    assert long_run.main(["check", "--status", str(status)]) == 2
    assert f"{status}:3:" in capsys.readouterr().err


def test_stale_warning(tmp_path, capsys):
    status = tmp_path / "STATUS.md"
    old = datetime.now().astimezone().replace(microsecond=0) - timedelta(minutes=30)
    long_run.append_record(status, long_run.Note(old, "last progress"))
    rc, report = check(status, capsys, "--stale-minutes", "10")
    assert (rc, report["stale"]) == (0, True)
    long_run.main(["check", "--status", str(status)])
    assert "STALE" in capsys.readouterr().err
    rc, report = check(status, capsys, "--stale-minutes", "60")
    assert report["stale"] is False


def test_reused_pid_is_not_the_job(tmp_path, capsys):
    status = tmp_path / "STATUS.md"
    stamp = datetime.now().astimezone().replace(microsecond=0)
    long_run.append_record(
        status,
        long_run.Start(stamp, "flow-pred", os.getpid(), str(tmp_path / "none.log"), ("flow", "CASE.DATA")),
    )
    rc, report = check(status, capsys)
    assert rc == 1
    job = report["jobs"][0]
    assert (job["state"], job["log_tail"], job["log_age_s"]) == ("EXITED", None, None)
    rc, out = start(tmp_path, capsys, "flow-pred", "true")
    assert rc == 0
    wait_gone(int(out))


def screening_v2(folder: Path, keep: bool) -> dict:
    """A minimal ahm-screening/2 record with real input files. Its sampled prior is A UNIFORM 0 1."""
    sources = {}
    for name in ("design", "residuals", "observations"):
        (folder / f"{name}.csv").write_text(f"{name}\n")
        sources[name] = {"path": f"{name}.csv", "sha256": hashlib.sha256(f"{name}\n".encode()).hexdigest()}
    parameter = {
        "name": "A",
        "low": 0.0,
        "high": 1.0,
        "chi2_swing": 52.0,
        "sd_ratio_lin": 0.4,
        "partner": None,
    }
    parameter |= {"cosine": None, "keep": keep, "reasons": [] if keep else ["insensitive"]}
    return {
        "schema": "ahm-screening/2",
        "ahm_analysis": "not run: unit test",
        "limits": {"min_chi2": 4.0, "max_ratio": 0.5},
        "observations": 1,
        "parameters": [parameter],
        "kept": ["A"] if keep else [],
        "dropped": [] if keep else ["A"],
        "priors": {"A": {"distribution": "UNIFORM", "arguments": {"min": 0.0, "max": 1.0}}},
        "domain": {
            "ensemble": "screen",
            "members": [0, 1],
            "observations": ["O1"],
            "observations_authoritative": True,
        },
        "observation_metadata": {"O1": {"OBSERVED": 1.0, "ERROR": 1.0}},
        "evidence": sources,
    }


def test_update_run_needs_the_preflight(tmp_path, capsys):
    (tmp_path / "p.dist").write_text("A UNIFORM 0 1\n")
    (tmp_path / "c.ert").write_text("NUM_REALIZATIONS 100\nGEN_KW G p.dist\n")
    fake_ert = tmp_path / "ert"  # stands in for ERT: the gate looks at the command, not the binary
    fake_ert.write_text("#!/bin/sh\nexit 0\n")
    fake_ert.chmod(0o755)
    status = tmp_path / "STATUS.md"
    base = [
        "start",
        "--status",
        str(status),
        "--name",
        "hm",
        "--log",
        str(tmp_path / "hm.log"),
        "--cwd",
        str(tmp_path),
    ]
    cmd = ["--", str(fake_ert), "es_mda", "c.ert", "--weights", "4,2,1"]
    capsys.readouterr()
    assert long_run.main([*base, *cmd]) == 1
    assert "AHM-PRE-NO-SCREENING" in capsys.readouterr().err
    record = tmp_path / "screening.json"
    record.write_text(json.dumps(screening_v2(tmp_path, keep=False)))
    assert long_run.main([*base, "--screening", str(record), *cmd]) == 1
    assert "AHM-PRE-DROPPED" in capsys.readouterr().err
    record.write_text(json.dumps(screening_v2(tmp_path, keep=True)))
    assert long_run.main([*base, "--screening", str(record), *cmd]) == 0
    assert "AHM compatibility passed; native ERT loading unverified" in status.read_text()
    assert (
        long_run.main([*base[:4], "hm2", *base[5:], "--no-preflight", "rerun of a checked case", *cmd]) == 0
    )
    assert "UNCHECKED unchecked override: rerun of a checked case" in status.read_text()
