"""ri_session: each encoded gotcha, against a fake rips and fake processes. Real runs: test_resinsight_real.py."""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import fake_rips
import pytest

ROOT = Path(__file__).resolve().parents[1]
SESSION = ROOT / "skills/resinsight-session/scripts/ri_session.py"

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ResInsight work is Linux only")


def _load():
    spec = importlib.util.spec_from_file_location("ri_session", SESSION)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ri_session"] = mod
    spec.loader.exec_module(mod)
    return mod


ri = _load()


class FakeProcess:
    def __init__(self):
        self.pid = -1
        self.returncode = None
        self.waited = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waited += 1
        self.returncode = 0
        return 0


@pytest.fixture
def fake(tmp_path, monkeypatch):
    fake_rips.Instance.fail_connect = None
    fake_rips.Instance.version = "2026.9.1"
    fake_rips.Instance.wells = ["INJ1", "PROD1"]
    fake_rips.Instance.case_kwargs = {}

    def open_session(mode="console", **over):
        fake_rips.Instance.console = mode == "console"
        process = FakeProcess()
        launcher = lambda config, workdir: ri.Launched(process=process, port=1234, log_file=workdir / "log")  # noqa: E731
        config = ri.SessionConfig(executable=tmp_path / "ri", mode=mode, **over)
        session = ri.Session(config, rips=fake_rips, launcher=launcher)
        session.fake_process = process
        return session

    return open_session


def _case(tmp_path: Path, smspec: bool = False) -> Path:
    egrid = tmp_path / "CASE.EGRID"
    egrid.write_bytes(b"grid")
    if smspec:
        (tmp_path / "CASE.SMSPEC").write_bytes(b"summary")
    return egrid


def test_rips_is_lazy_and_fails_closed(monkeypatch):
    monkeypatch.setitem(sys.modules, "rips", None)
    with pytest.raises(ri.RipsMissing, match="resinsight.python"):
        ri.import_rips()


def test_exit_and_wait_always_run(fake, tmp_path):
    session = fake()
    with pytest.raises(ZeroDivisionError), session:
        instance = fake_rips.Instance.last
        raise ZeroDivisionError("boom")
    assert instance.exited and session.fake_process.waited == 1 and session.launched is None


def test_version_mismatch_kills_the_server(fake):
    fake_rips.Instance.fail_connect = "Wrong Version of ResInsight at localhost:1234: 2026.6.0"
    session = fake()
    with pytest.raises(ri.VersionMismatch, match="major.minor"), session:
        pass
    assert session.fake_process.waited == 1


def test_contract_version_is_checked(fake):
    with (
        pytest.raises(ri.VersionMismatch, match="contract resinsight.version is 2026.06"),
        fake(expected_version="2026.06"),
    ):
        pass
    with fake(expected_version="2026.9") as session:
        assert session.version == "2026.9.1"


def test_console_refuses_images_before_calling_resinsight(fake, tmp_path):
    with fake("console") as session:
        case = session.load_case(_case(tmp_path))
        view = session.ensure_view(case)
        with pytest.raises(ri.ModeError, match="GUI mode"):
            session.snapshot(view, tmp_path / "snaps", "x_")
        assert not (tmp_path / "snaps").exists()


def test_snapshot_makes_the_folder_and_returns_the_new_file(fake, tmp_path):
    with fake("gui") as session:
        case = session.load_case(_case(tmp_path))
        view = session.ensure_view(case)
        view.apply_cell_result("STATIC_NATIVE", "PORO")
        png = session.snapshot(view, tmp_path / "new/folder", "poro_")
        assert png.is_file() and png.name.startswith("poro_") and png.suffix == ".png"


def test_silent_export_failure_is_an_error(fake, tmp_path, monkeypatch):
    with fake("gui") as session:
        case = session.load_case(_case(tmp_path))
        view = session.ensure_view(case)
        monkeypatch.setattr(view, "export_snapshot", lambda **kw: None)  # success, no file
        with pytest.raises(ri.ExportError, match="wrote no new file"):
            session.snapshot(view, tmp_path / "s", "x_")


def test_console_load_has_no_view_and_wells_need_one(fake, tmp_path):
    with fake("console") as session:
        case = session.load_case(_case(tmp_path))
        assert case.views() == [] and case.simulation_wells() == []
        assert session.simulation_wells(case) == ["INJ1", "PROD1"]
        assert len(case.views()) == 1
        assert session.ensure_view(case) is case.views()[0]


def test_empty_property_list_is_empty_not_an_error(fake, tmp_path):
    with fake() as session:
        case = session.load_case(_case(tmp_path))
        assert session.properties(case, "DYNAMIC_NATIVE") == []
        assert session.properties(case, "STATIC_NATIVE") == ["PORO", "PERMX"]


def test_export_property_passes_explicit_arguments(fake, tmp_path):
    with fake() as session:
        case = session.load_case(_case(tmp_path))
        out = session.export_property(case, 0, "PORO", tmp_path / "props/poro.grdecl")
        assert out.read_text().splitlines()[1] == "PORO"


def test_summary_case_is_reused_not_imported_twice(fake, tmp_path):
    with fake() as session:
        egrid = _case(tmp_path, smspec=True)
        session.load_case(egrid)
        first = session.summary_case(tmp_path / "CASE.SMSPEC")
        assert session.summary_case(tmp_path / "CASE.SMSPEC") is first


def test_plot_real_id_and_pdf_export(fake, tmp_path, monkeypatch):
    calls = []

    def fake_pdftoppm(cmd, check):
        calls.append(cmd)
        Path(cmd[-1] + ".png").write_bytes(b"png")

    monkeypatch.setattr(ri.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(ri.subprocess, "run", fake_pdftoppm)
    with fake("gui") as session:
        plot = session.new_summary_plot([], "FOPT")
        assert plot.id != -1
        png = session.export_plot(plot, tmp_path / "plots", "fopt_")
        assert png.suffix == ".png" and calls[0][:2] == ["pdftoppm", "-png"]
        with pytest.raises(ri.RiError, match="id is -1"):
            session.export_plot(type("P", (), {"id": -1})(), tmp_path / "plots", "bad_")


def test_generated_values_must_match_active_cells(fake, tmp_path):
    with fake() as session:
        case = session.load_case(_case(tmp_path))
        with pytest.raises(ri.RiError, match="11 active cells"):
            session.set_generated(case, "DIFF", [0.0] * 12)
        session.set_generated(case, "DIFF", [1.0] * 11)
        assert case.generated[("DIFF", 0)] == [1.0] * 11


def test_all_cell_mask_maps_to_active_cells(fake, tmp_path):
    with fake() as session:
        case = session.load_case(_case(tmp_path))  # 3x2x2, global cell 1 inactive
        mask = [0] * 12
        mask[2] = 1  # global 2 = active index 1
        active = session.active_mask(case, mask)
        assert len(active) == 11 and active.index(True) == 1
        with pytest.raises(ri.RiError, match="12 cells"):
            session.active_mask(case, [0] * 11)


def test_grid_statistics_sets_lists_and_updates(fake, tmp_path):
    files = [tmp_path / f"R{i}.EGRID" for i in range(2)]
    for f in files:
        f.write_bytes(b"g")
    with fake() as session:
        group, stat = session.grid_statistics(files, dynamic=["SWAT"], static=["PERMX"])
        assert stat.dynamic_properties_to_calculate == ["SWAT"] and stat.updated == 1 and group.computed
        with pytest.raises(ri.RiError, match="not found"):
            session.grid_statistics([tmp_path / "absent.EGRID"])


def test_ensemble_project_xml(tmp_path):
    members = []
    for i in range(2):
        p = tmp_path / f"realization-{i}/R&{i}.SMSPEC"
        p.parent.mkdir()
        p.write_bytes(b"s")
        members.append(p)
    path = ri.write_ensemble_project(tmp_path / "e.rsp", {"iter-0": members, "iter-4": members[:1]})
    text = path.read_text()
    assert "<ResInsightOilField>" in text and "RimOilField" not in text
    assert text.count("<IsEnsemble>True </IsEnsemble>") == 2 and "R&amp;0" in text
    assert "$PathId_003$" in text
    with pytest.raises(ri.RiError, match="no members"):
        ri.write_ensemble_project(tmp_path / "x.rsp", {"empty": []})
    with pytest.raises(ri.RiError, match="not found"):
        ri.write_ensemble_project(tmp_path / "x.rsp", {"a": [tmp_path / "missing.SMSPEC"]})


def test_open_summary_ensembles_counts_what_loaded(fake, tmp_path):
    p = tmp_path / "A.SMSPEC"
    p.write_bytes(b"s")
    with fake() as session:
        found = session.open_summary_ensembles(tmp_path / "e.rsp", {"prior": [p], "post": [p]})
        assert [s.summary_collection_name for s in found] == ["prior", "post"]


# real processes, fake executable -------------------------------------------------------------------------
FAKE_SERVER = """#!{py}
import os, sys, time
args = sys.argv[1:]
port_file = args[args.index("--portnumberfile") + 1]
print("fake ResInsight", " ".join(args), flush=True)
if os.environ.get("FAKE_MODE") == "die":
    sys.exit(3)
if os.environ.get("FAKE_MODE") != "silent":
    open(port_file, "w").write("43210")
time.sleep(600)
"""


def _server(tmp_path: Path) -> Path:
    exe = tmp_path / "fake_ri"
    exe.write_text(FAKE_SERVER.format(py=sys.executable))
    exe.chmod(0o755)
    return exe


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()[:1] not in ("Z", "")


def test_launch_reads_port_logs_to_file_and_kills_group(tmp_path):
    config = ri.SessionConfig(executable=_server(tmp_path), workdir=tmp_path / "w", exit_timeout=0.2)
    launched = ri.launch_process(config, tmp_path)
    try:
        assert launched.port == 43210
        time.sleep(0.2)
        log = launched.log_file.read_text()
        assert "--server 0" in log and "--console" in log
        assert os.getpgid(launched.process.pid) == launched.process.pid  # own group
    finally:
        ri._kill_group(launched.process, 0.2)
    assert not _alive(launched.process.pid)


def test_launch_reports_early_exit_and_timeout(tmp_path, monkeypatch):
    config = ri.SessionConfig(executable=_server(tmp_path), init_timeout=1.0)
    monkeypatch.setenv("FAKE_MODE", "die")
    with pytest.raises(ri.LaunchError, match="exited with code 3"):
        ri.launch_process(config, tmp_path)
    monkeypatch.setenv("FAKE_MODE", "silent")
    with pytest.raises(ri.LaunchError, match="did not write a port"):
        ri.launch_process(config, tmp_path)


def test_stale_port_file_of_an_earlier_run_is_not_used(tmp_path, monkeypatch):
    """Measured: a rerun in the same workdir read the old port and connected to nothing."""
    (tmp_path / "port.txt").write_text("37599")
    config = ri.SessionConfig(executable=_server(tmp_path), init_timeout=1.0)
    monkeypatch.setenv("FAKE_MODE", "silent")
    with pytest.raises(ri.LaunchError, match="did not write a port"):
        ri.launch_process(config, tmp_path)


def test_sigterm_still_cleans_up(tmp_path):
    """`timeout` sends SIGTERM to Python. The fake server must not outlive it."""
    script = tmp_path / "victim.py"
    script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(SESSION.parent)!r}); sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "import ri_session as ri, fake_rips\n"
        f"cfg = ri.SessionConfig(executable={str(_server(tmp_path))!r}, exit_timeout=0.2)\n"
        "with ri.Session(cfg, rips=fake_rips) as s:\n"
        "    print(s.launched.process.pid, flush=True)\n"
        "    time.sleep(600)\n"
    )
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    child = int(proc.stdout.readline())
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 128 + signal.SIGTERM
    time.sleep(0.3)
    assert not _alive(child)
