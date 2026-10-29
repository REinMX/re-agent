"""Real ResInsight checks. Marker resinsight: run with `make check-resinsight RI=... RIPY=...`.

A missing binary is a failure here, not a skip: these tests run only when asked for.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/resinsight-session/scripts"

pytestmark = pytest.mark.resinsight


def real_paths() -> tuple[str, str]:
    binary, python = os.environ.get("RESINSIGHT_EXECUTABLE"), os.environ.get("RIPS_PYTHON")
    if not binary or not python:
        pytest.fail("set RESINSIGHT_EXECUTABLE and RIPS_PYTHON (make check-resinsight RI=... RIPY=...)")
    return binary, python


def test_real_doctor_is_ready():
    binary, python = real_paths()
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "ri_doctor.py"), "--bin", binary, "--python", python, "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    report = json.loads(proc.stdout)
    assert proc.returncode == 0, report
    assert report["errors"] == [] and report["resinsight_version"] and report["rips_version"]


# ri_session against the real server -------------------------------------------------------------------
SESSION = SCRIPTS / "ri_session.py"


def _synth(tmp_path: Path, **kw) -> Path:
    sys.path.insert(0, str(Path(__file__).parent))
    from synth_grid import make_case

    return make_case(tmp_path / kw.pop("folder", "case"), inactive=(5,), **kw)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _smoke(*args: str) -> dict:
    binary, python = real_paths()
    proc = subprocess.run(
        [python, str(SESSION), "--bin", binary, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_real_console_session_loads_a_synthetic_case(tmp_path: Path):
    egrid = _synth(tmp_path)
    result = _smoke("--mode", "console", "--case", str(egrid))
    assert result["case"]["dims"] == [4, 3, 2] and result["case"]["active"] == 23
    assert not _alive(result["pid"])


def test_real_gui_snapshot_and_project_then_no_process_left(tmp_path: Path):
    egrid = _synth(tmp_path)
    result = _smoke("--mode", "gui", "--case", str(egrid), "--out", str(tmp_path / "out"))
    png, rsp = Path(result["snapshot"]), Path(result["project"])
    assert png.read_bytes()[:4] == b"\x89PNG" and png.stat().st_size > 1000
    assert rsp.read_text().lstrip().startswith("<?xml") and "ResInsightProject" in rsp.read_text()
    assert not _alive(result["pid"])


DRIVER = """
import json, sys
sys.path.insert(0, {scripts!r})
import ri_session as ri
from pathlib import Path
out = Path({out!r})
cfg = ri.SessionConfig(executable=Path({binary!r}), mode="gui")
res = {{}}
with ri.Session(cfg) as s:
    case = s.load_case({egrid!r})
    summary = s.summary_case({smspec!r})
    plot = s.new_summary_plot([summary], "FOPT")
    res["plot_png"] = str(s.export_plot(plot, out / "plots", "fopt_"))
    view = s.ensure_view(case)
    view.apply_cell_result(result_type="STATIC_NATIVE", result_variable="PORO")
    res["view_property"] = str(s.export_view_property(view, out / "props"))
    res["grdecl"] = str(s.export_property(case, 0, "PORO", out / "props/PORO.GRDECL"))
    s.set_generated(case, "TWICE", [2.0 * v for v in case.active_cell_property("STATIC_NATIVE", "PORO", 0)])
    res["generated"] = "TWICE" in s.properties(case, "GENERATED")
    res["dynamic"] = s.properties(case, "DYNAMIC_NATIVE")
    ens = s.open_summary_ensembles(out / "ens.rsp", {{"a": [{smspec!r}, {smspec2!r}]}})
    res["ensembles"] = [e.summary_collection_name for e in ens]
    try:
        s.summary_case({smspec!r})
        res["reused"] = True
    except Exception as e:
        res["reused"] = repr(e)
print(json.dumps(res))
"""


def test_real_session_exports_plot_property_and_ensemble(tmp_path: Path):
    binary, python = real_paths()
    egrid = _synth(tmp_path, summary=True)
    second = _synth(tmp_path, folder="case2", summary=True)
    script = tmp_path / "driver.py"
    script.write_text(
        DRIVER.format(
            scripts=str(SCRIPTS),
            out=str(tmp_path / "out"),
            binary=binary,
            egrid=str(egrid),
            smspec=str(egrid.with_suffix(".SMSPEC")),
            smspec2=str(second.with_suffix(".SMSPEC")),
        )
    )
    proc = subprocess.run([python, str(script)], capture_output=True, text=True, check=False, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    assert Path(res["plot_png"]).read_bytes()[:4] == b"\x89PNG"
    assert "PORO" in Path(res["grdecl"]).read_text()
    assert Path(res["view_property"]).stat().st_size > 0
    assert res["generated"] is True and "SWAT" not in res["dynamic"] and len(res["ensembles"]) == 1
    assert res["reused"] is True


# resinsight-views against the real server ---------------------------------------------------------------
VIEWS = ROOT / "skills/resinsight-views/scripts"


def _ours_running(workdir: Path) -> list[int]:
    """PIDs whose working folder is our session workdir (ResInsight runs there) or that name its port file."""
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cwd = os.readlink(proc / "cwd")
            cmdline = (proc / "cmdline").read_bytes().decode(errors="replace")
        except OSError:
            continue
        if cwd == str(workdir) or str(workdir / "port.txt") in cmdline:
            if _alive(int(proc.name)):
                found.append(int(proc.name))
    return found


def test_real_render_views_array_range_and_north_camera(tmp_path: Path):
    binary, python = real_paths()
    sys.path.insert(0, str(VIEWS))
    import grdecl

    egrid = _synth(tmp_path)
    array = grdecl.write_grdecl(tmp_path / "sdratio.grdecl", "SDRATIO", [float(g % 4) for g in range(24)])
    out = tmp_path / "out"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "case": str(egrid),
                "out": str(out),
                "arrays": [{"name": "SDRATIO", "file": str(array)}],
                "views": [
                    {"name": "sd_top", "property": "INPUT_PROPERTY:SDRATIO", "range": {"k": [1, 1]}},
                    {
                        "name": "poro_north",
                        "property": "STATIC_NATIVE:PORO",
                        "range": {"j": [2, 2]},
                        "camera": "north",
                        "zoom": 2,
                    },
                ],
            }
        )
    )
    proc = subprocess.run(
        [python, str(VIEWS / "render_views.py"), "--spec", str(spec), "--bin", binary],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["failed"] == [] and [v["name"] for v in manifest["views"]] == ["sd_top", "poro_north"]
    for view in manifest["views"]:
        png = Path(view["png"])
        assert png.read_bytes()[:4] == b"\x89PNG" and png.stat().st_size > 1000
    assert manifest["arrays"] == {"SDRATIO": str(array)}
    rsp = out / "views.rsp"
    assert Path(manifest["project"]) == rsp and "SDRATIO" in rsp.read_text()
    assert _ours_running(out / "ri") == []


# ahm-ensemble-views: ensemble_fields (resdata) -> render_views (ResInsight) -------------------------------
FIELDS = ROOT / "skills/ahm-ensemble-views/scripts/ensemble_fields.py"


def test_real_ensemble_fields_spec_renders(tmp_path: Path):
    binary, python = real_paths()
    sys.path.insert(0, str(Path(__file__).parent))
    from synth_grid import make_case

    for iteration, shift, spread in (("iter-0", 0.0, 0.02), ("iter-4", 0.03, 0.002)):
        for r in range(4):
            model = tmp_path / f"ens/realization-{r}/{iteration}/model"
            poro = [0.2 + shift * (c // 11) + spread * (r - 1.5) for c in range(23)]
            make_case(model, inactive=(5,), poro=poro, permx=[100.0 * 10 ** (spread * 10 * (r - 1.5))] * 23)
            (model / "SYNTH.DATA").write_text("-- synthetic\n")
    out = tmp_path / "fields"
    proc = subprocess.run(
        [
            sys.executable,
            str(FIELDS),
            "--prior",
            str(tmp_path / "ens/realization-*/iter-0"),
            "--posterior",
            str(tmp_path / "ens/realization-*/iter-4"),
            "--case",
            str(tmp_path / "ens/realization-0/iter-4/model/SYNTH.EGRID"),
            "--outdir",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 1, proc.stderr  # collapse warnings
    proc = subprocess.run(
        [python, str(VIEWS / "render_views.py"), "--spec", str(out / "spec.json"), "--bin", binary],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    manifest = json.loads((out / "views/manifest.json").read_text())
    assert manifest["failed"] == [] and len(manifest["views"]) == 8
    for view in manifest["views"]:
        png = Path(view["png"])
        assert png.read_bytes()[:4] == b"\x89PNG" and png.stat().st_size > 1000
    assert "POROSDR" in (out / "views/views.rsp").read_text()
    assert _ours_running(out / "views/ri") == []


# flow-diagnostics against the real server ---------------------------------------------------------------
FD = ROOT / "skills/flow-diagnostics/scripts"


def _run(python: str, script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [python, str(script), *args], capture_output=True, text=True, check=False, timeout=900
    )


def test_real_flow_diagnostics_preflight_export_metrics(tmp_path: Path):
    """Needs a case with FLORES or FLOWS fluxes (RPTRST BASIC=2 FLORES). Set RI_FD_CASE to its BASE."""
    case = os.environ.get("RI_FD_CASE")
    if not case:
        pytest.skip("set RI_FD_CASE to the BASE of a case with flux arrays (RPTRST BASIC=2 FLORES)")
    binary, python = real_paths()
    pre = tmp_path / "preflight.json"
    proc = _run(sys.executable, FD / "fd_preflight.py", "--case", case, "--out", str(pre))
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = tmp_path / "fd"
    proc = _run(python, FD / "fd_export.py", "--preflight", str(pre), "--bin", binary, "--outdir", str(out))
    assert proc.returncode == 0, proc.stderr[-2000:]
    manifest = json.loads((out / "fd_export.json").read_text())
    preflight = json.loads(pre.read_text())
    assert manifest["failed"] == [] and manifest["step"] == preflight["step"]
    expected = {f"FRAC{n}" for n in range(1, len(preflight["injectors"]) + 1)}
    expected |= {f"FRACP{n}" for n in range(1, len(preflight["producers"]) + 1)} | {"RESTIME"}
    assert set(manifest["arrays"]) == expected

    sys.path.insert(0, str(VIEWS))
    import grdecl
    from resdata.grid import Grid

    nx, ny, nz, _ = Grid(f"{case}.EGRID").get_dims()
    for keyword, item in manifest["arrays"].items():
        name, values = grdecl.read_grdecl(Path(item["file"]), expected=nx * ny * nz)
        assert name == keyword
        if keyword == "RESTIME":
            assert max(values) > 0
        else:
            assert 0 <= min(values) and max(values) <= 1 + 1e-6 and max(values) > 0
    proc = _run(
        sys.executable, FD / "fd_metrics.py", "--fd-dir", str(out), "--outdir", str(tmp_path / "metrics")
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    result = json.loads((tmp_path / "metrics/fd_metrics.json").read_text())
    assert 0 < result["lorenz"] < 1, result
    assert _ours_running(out / "ri") == []


# remaining-oil views against the real server ------------------------------------------------------------
def test_real_remaining_oil_views_on_a_synthetic_case(tmp_path: Path):
    """remaining_oil.py (resdata) writes the arrays and spec.json; render_views.py (rips) draws them."""
    import datetime as dt

    binary, python = real_paths()
    from synth_grid import write_restart

    egrid = _synth(tmp_path)  # 4x3x2, cell 5 inactive
    swat = [0.2 + 0.03 * n for n in range(23)]
    write_restart(egrid.parent, "SYNTH", (4, 3, 2), 23, [(dt.date(2020, 1, 1), {"SWAT": swat})])
    out = tmp_path / "roil"
    proc = _run(
        sys.executable,
        ROOT / "skills/remaining-oil/scripts/remaining_oil.py",
        *("--case", str(egrid), "--radius", "0", "--min-distance", "2", "--view-case", str(egrid)),
        *("--outdir", str(out)),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    proc = _run(python, VIEWS / "render_views.py", "--spec", str(out / "spec.json"), "--bin", binary)
    assert proc.returncode == 0, proc.stderr[-2000:]
    manifest = json.loads((out / "views/manifest.json").read_text())
    assert manifest["failed"] == [] and [v["name"] for v in manifest["views"]] == [
        "mobcol",
        "candidates",
        "soil",
    ]
    for view in manifest["views"]:
        png = Path(view["png"])
        assert png.read_bytes()[:4] == b"\x89PNG" and png.stat().st_size > 1000
    assert set(manifest["arrays"]) == {"MOBOIL", "MOBCOL", "CANDID"}
    assert _ours_running(out / "views/ri") == []
