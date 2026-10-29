"""flow-diagnostics: fd_preflight and fd_metrics on synthetic resdata cases, fd_export with the fake rips.

Real runs: test_resinsight_real.py (needs RI_FD_CASE, a case with fluxes).
"""

from __future__ import annotations

import csv
import datetime as dt
import functools
import json
import sys
from pathlib import Path

import fake_rips
import pytest
from synth_fmu import need
from synth_grid import make_case, write_restart, write_summary

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/flow-diagnostics/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fd_export  # noqa: E402
import fd_metrics  # noqa: E402
import fd_preflight  # noqa: E402

grdecl = fd_export.grdecl
ri = fd_export.ri
vs_path = ROOT / "skills/resinsight-views/scripts"
if str(vs_path) not in sys.path:
    sys.path.insert(0, str(vs_path))
import view_spec as vs  # noqa: E402

DIMS = (4, 3, 2)
N = 24
DATES = [dt.date(2020, 1, 1), dt.date(2020, 2, 1), dt.date(2020, 3, 1)]


# fd_preflight ---------------------------------------------------------------------------------------
def flux_case(folder: Path, fluxes: bool = True, summary: bool = True) -> Path:
    """Step 0 has no flux arrays, step 1 zero fluxes, step 2 fluxes (the Egg layout). Return BASE."""
    need("resdata", "numpy")
    make_case(folder, base="FD", dims=DIMS)
    flux = {"FLROILI+": [1.0] * N, "FLRWATI+": [2.0] * N} if fluxes else {}
    zero = {name: [0.0] * N for name in flux}
    steps = [(DATES[0], {"SWAT": [0.2] * N}), (DATES[1], {"SWAT": [0.3] * N} | zero)]
    steps.append((DATES[2], {"SWAT": [0.4] * N} | flux))
    write_restart(folder, "FD", DIMS, N, steps)
    if summary:
        write_summary(folder, "FD", DIMS, {"WWIR:INJ1": 100.0, "WOPR:PROD1": 50.0, "WOPR:SHUT1": 0.0})
    return folder / "FD"


def preflight(tmp_path: Path, base: Path, *extra: str) -> tuple[int, dict | None]:
    out = tmp_path / "out" / "preflight.json"
    code = fd_preflight.main(["--case", str(base), "--out", str(out), *extra])
    return code, json.loads(out.read_text()) if out.is_file() else None


@pytest.mark.parametrize("step", ["last", "2", "2020-03-01"])
def test_preflight_finds_the_flux_step_and_the_wells(tmp_path, step):
    code, result = preflight(tmp_path, flux_case(tmp_path / "case"), "--step", step)
    assert code == 0
    assert (result["step"], result["date"], result["flux_steps"]) == (2, "2020-03-01", [2])
    assert result["flux_keywords"] == ["FLROILI+", "FLRWATI+"]
    assert (result["injectors"], result["producers"]) == (["INJ1"], ["PROD1"])
    assert result["wells_from"] == "summary rates at the step"


@pytest.mark.parametrize(
    ("step", "message"),
    [  # step 0 has no flux arrays: counting keyword occurrences would call it the flux step
        (
            "1",
            "step 1 (2020-02-01) has no non-zero fluxes. Steps with fluxes: 2..2. Use --step 2 (2020-03-01)",
        ),
        ("0", "step 0 (2020-01-01) has no non-zero fluxes"),
        ("3", "--step 3 is not a report step"),
        ("2020-01-15", "--step 2020-01-15 is not a report step"),
    ],
)
def test_preflight_step_errors_exit_2(tmp_path, capsys, step, message):
    code, _ = preflight(tmp_path, flux_case(tmp_path / "case"), "--step", step)
    assert code == 2 and message in capsys.readouterr().err


def test_preflight_explicit_wells_need_no_summary(tmp_path):
    base = flux_case(tmp_path / "case", summary=False)
    code, result = preflight(tmp_path, base, "--injectors", "I1,I2", "--producers", "P1")
    assert code == 0
    assert (result["injectors"], result["producers"], result["wells_from"]) == (
        ["I1", "I2"],
        ["P1"],
        "arguments",
    )


def test_preflight_without_summary_or_wells_exit_2(tmp_path, capsys):
    code, _ = preflight(tmp_path, flux_case(tmp_path / "case", summary=False))
    assert code == 2 and "missing FD.SMSPEC" in capsys.readouterr().err


def test_preflight_needs_an_injector_and_a_producer(tmp_path, capsys):
    code, _ = preflight(tmp_path, flux_case(tmp_path / "case"), "--producers", "P1")
    assert code == 2 and "needs one of each" in capsys.readouterr().err


def test_preflight_without_fluxes_names_the_keyword_and_the_disk(tmp_path, capsys, monkeypatch):
    base = flux_case(tmp_path / "case", fluxes=False)
    monkeypatch.setattr(fd_preflight, "GROWTH", 1000.0)
    code, result = preflight(tmp_path, base)
    err = capsys.readouterr().err
    assert code == 2 and "RPTRST" in err and "BASIC=2 FLORES" in err
    size = (tmp_path / "case/FD.UNRST").stat().st_size
    assert f"Expected UNRST about {size * 1000.0 / 1e6:.0f} MB" in result["advice"]
    assert result["flux_keywords"] == [] and "injectors" not in result


def test_preflight_missing_files_exit_2(tmp_path, capsys):
    base = flux_case(tmp_path / "case")
    (tmp_path / "case/FD.UNRST").unlink()
    code, _ = preflight(tmp_path, Path(f"{base}.EGRID"))
    assert code == 2 and "FD.UNRST" in capsys.readouterr().err


def test_preflight_refuses_to_write_in_the_case_folder(tmp_path, capsys):
    base = flux_case(tmp_path / "case")
    code = fd_preflight.main(["--case", str(base), "--out", str(tmp_path / "case/fd/preflight.json")])
    assert code == 2 and "case folder" in capsys.readouterr().err
    assert not (tmp_path / "case/fd").exists()


# fd_export with the fake rips -----------------------------------------------------------------------
class FakeProcess:
    pid = -1
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


def _launcher(config, workdir):
    return ri.Launched(process=FakeProcess(), port=1234, log_file=workdir / "log")


CELLS = 12  # the fake case: 3x2x2, one inactive cell
FRACTION = {"I1": 0.25, "I2": 0.75, "P1": 1.0}


@pytest.fixture
def fake(monkeypatch):
    """The fake rips in console mode. Records the flow selection of each export."""
    calls: list[tuple] = []
    fail: set[str] = set()

    def values(view):
        variable, mode, injectors, producers = view.flow
        calls.append((variable, mode, tuple(injectors), tuple(producers), view.time_step))
        wells = injectors + producers
        if set(wells) & fail:
            raise fake_rips.RipsError(f"export failed for {wells}")
        if variable == "TOF":
            return [float(10 + g) for g in range(CELLS)]
        return [FRACTION[wells[0]]] * CELLS

    monkeypatch.setattr(fake_rips.Instance, "console", True)
    monkeypatch.setattr(fake_rips.Instance, "fail_connect", None)
    monkeypatch.setattr(fake_rips.Instance, "case_kwargs", {})
    monkeypatch.setattr(fake_rips.Instance, "view_function", staticmethod(values))
    monkeypatch.setattr(
        fd_export, "run", functools.partial(fd_export.run, rips=fake_rips, launcher=_launcher)
    )
    return calls, fail


def write_preflight(tmp_path: Path, **over) -> Path:
    (tmp_path / "case").mkdir(exist_ok=True)
    (tmp_path / "case/FD.EGRID").write_bytes(b"grid")
    data = {"case": str(tmp_path / "case/FD"), "step": 2, "date": "2020-03-01", "injectors": ["I1", "I2"]}
    data |= {"producers": ["P1"]} | over
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps({k: v for k, v in data.items() if v is not None}))
    return path


def export(tmp_path: Path, preflight_path: Path, outdir: Path | None = None) -> int:
    outdir = outdir or tmp_path / "out"
    return fd_export.main(
        ["--preflight", str(preflight_path), "--bin", str(tmp_path / "ri"), "--outdir", str(outdir)]
    )


def test_export_one_grdecl_per_well_and_restime(tmp_path, fake):
    calls, _ = fake
    assert export(tmp_path, write_preflight(tmp_path)) == 0
    out = tmp_path / "out"
    manifest = json.loads((out / "fd_export.json").read_text())
    assert manifest["failed"] == [] and manifest["resinsight_version"] == "2026.9.1"
    assert manifest["source"].startswith("ResInsight flow diagnostics")
    assert {k: (v["well"], v["role"], v["result"]) for k, v in manifest["arrays"].items()} == {
        "FRAC1": ("I1", "injector", "Fraction"),
        "FRAC2": ("I2", "injector", "Fraction"),
        "FRACP1": ("P1", "producer", "Fraction"),
        "RESTIME": ("all", "all", "TOF"),
    }
    for keyword, item in manifest["arrays"].items():
        assert Path(item["file"]) == out / "fd" / f"{keyword}.grdecl"
        name, values = grdecl.read_grdecl(Path(item["file"]), expected=CELLS)
        expected = (
            [float(10 + g) for g in range(CELLS)]
            if keyword == "RESTIME"
            else [FRACTION[item["well"]]] * CELLS
        )
        assert (name, values) == (keyword, expected)
    assert list((out / "fd/raw").iterdir()) == []
    assert calls == [
        ("Fraction", "FLOW_TR_BY_SELECTION", ("I1",), (), 2),
        ("Fraction", "FLOW_TR_BY_SELECTION", ("I2",), (), 2),
        ("Fraction", "FLOW_TR_BY_SELECTION", (), ("P1",), 2),
        ("TOF", "FLOW_TR_INJ_AND_PROD", (), (), 2),
    ]
    spec = vs.load_spec(out / "spec.json")
    assert [v.name for v in spec.views] == ["drain_p1", "sweep_i1", "sweep_i2", "restime"]
    assert spec.views[0].flow == {"producers": ["P1"]} and spec.views[0].time_step == 2
    assert spec.arrays == (vs.ArrayInput("RESTIME", out / "fd/RESTIME.grdecl"),)
    assert fake_rips.Instance.last.exited


def test_export_failure_is_listed_and_exit_1(tmp_path, fake, capsys):
    _, fail = fake
    fail.add("I2")
    assert export(tmp_path, write_preflight(tmp_path)) == 1
    manifest = json.loads((tmp_path / "out/fd_export.json").read_text())
    assert sorted(manifest["arrays"]) == ["FRAC1", "FRACP1", "RESTIME"]
    (failed,) = manifest["failed"]
    assert (failed["keyword"], failed["well"]) == ("FRAC2", "I2")
    assert failed["error"] == "RipsError: export failed for ['I2']"
    assert json.loads(capsys.readouterr().out)["arrays"] == 3


def test_export_without_restime_writes_no_restime_view(tmp_path, fake):
    _, fail = fake
    fail.add("I1")

    def no_tof(view):
        if view.flow[0] == "TOF":
            raise fake_rips.RipsError("no TOF")
        return [0.5] * CELLS

    fake_rips.Instance.view_function = staticmethod(no_tof)
    assert export(tmp_path, write_preflight(tmp_path)) == 1
    spec = json.loads((tmp_path / "out/spec.json").read_text())
    assert spec["arrays"] == [] and "restime" not in [v["name"] for v in spec["views"]]


def test_export_rips_missing_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "rips", None)
    assert export(tmp_path, write_preflight(tmp_path)) == 2
    assert "resinsight.python" in capsys.readouterr().err
    assert not (tmp_path / "out/fd_export.json").exists()


def test_export_input_errors_exit_2(tmp_path, fake, capsys):
    assert export(tmp_path, tmp_path / "none.json") == 2
    assert "Run fd_preflight.py first" in capsys.readouterr().err
    assert export(tmp_path, write_preflight(tmp_path, injectors=None)) == 2
    assert "has no injectors" in capsys.readouterr().err
    assert export(tmp_path, write_preflight(tmp_path), outdir=tmp_path / "case/fd") == 2
    assert "case folder" in capsys.readouterr().err
    (tmp_path / "case/FD.EGRID").unlink()
    path = tmp_path / "preflight.json"
    assert export(tmp_path, path) == 2 and "grid file not found" in capsys.readouterr().err


# fd_metrics -----------------------------------------------------------------------------------------
def test_fphi_uniform_sweep_has_lorenz_zero():
    need("numpy")
    import numpy as np

    phi, f, lorenz, cells = fd_metrics.fphi(np.full(50, 30.0), np.full(50, 2.0))
    assert cells == 50 and abs(lorenz) < 1e-9
    assert np.allclose(f, phi) and (phi[0], phi[-1], len(phi)) == (0.0, 1.0, 101)


def test_fphi_one_fast_cell_has_a_high_lorenz():
    """100 equal cells; one has a residence time of 1, the others 1e4. Trapezoid area by hand."""
    need("numpy")
    import numpy as np

    restime = np.full(100, 1e4)
    restime[37] = 1.0
    _, f, lorenz, _ = fd_metrics.fphi(restime, np.ones(100))
    first = 1.0 / (1.0 + 99e-4)
    area = 0.01 * first / 2 + 0.99 * (first + 1.0) / 2
    assert lorenz == pytest.approx(2 * (area - 0.5)) and lorenz > 0.95
    assert f[1] == pytest.approx(first)


def test_fphi_ignores_cells_without_time_or_pore_volume():
    need("numpy")
    import numpy as np

    restime = np.array([0.0, 5.0, np.nan, 5.0, 5.0])
    porv = np.array([1.0, 1.0, 1.0, 0.0, 1.0])
    assert fd_metrics.fphi(restime, porv)[3] == 2
    with pytest.raises(fd_metrics.InputError, match="fewer than 2 cells"):
        fd_metrics.fphi(np.array([0.0, 5.0]), np.ones(2))


def write_connections(path: Path, rows: list[tuple]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["well", "i", "j", "k", "status", "flag"])
        writer.writerows(rows)
    return path


def test_allocation_shares_per_producer_on_a_toy_grid(tmp_path):
    """Grid 3x1x1, PORV 1, 3, 2. P1 connects cells 1 and 2; cell 3 is SHUT and COMP-INACTIVE."""
    need("numpy")
    import numpy as np

    arrays = {
        "FRAC1": {"well": "I1", "role": "injector", "values": np.array([1.0, 0.0, 0.5])},
        "FRAC2": {"well": "I2", "role": "injector", "values": np.array([0.0, 1.0, 0.5])},
        "FRACP1": {"well": "P1", "role": "producer", "values": np.ones(3)},
        "FRACP2": {"well": "P2", "role": "producer", "values": np.ones(3)},
    }
    rows = [("P1", 1, 1, 1, "OPEN", "ok"), ("P1", 2, 1, 1, "OPEN", "ok"), ("P1", 3, 1, 1, "SHUT", "ok")]
    rows.append(("P1", 3, 1, 1, "OPEN", "COMP-INACTIVE"))
    connections = write_connections(tmp_path / "connections.csv", rows)
    result = fd_metrics.allocation(arrays, connections, np.array([1.0, 3.0, 2.0]), (3, 1, 1))
    p1 = [r for r in result if r["producer"] == "P1"]
    assert [(r["injector"], r["share"]) for r in p1] == [("I2", 0.75), ("I1", 0.25)]
    assert sum(r["share"] for r in p1) == pytest.approx(1.0) and all(r["note"] == "" for r in p1)
    assert [r for r in result if r["producer"] == "P2"] == [
        {"producer": "P2", "injector": "", "share": None, "note": "no open connection cell"}
    ]


def metrics_case(tmp_path: Path, restime: list[float] | None = None, omit: str = "") -> Path:
    """A synthetic case (cell 5 inactive) and an fd_export.json with two injectors and one producer."""
    need("resdata", "numpy")
    make_case(tmp_path / "case", base="FD", dims=DIMS, inactive=(5,))
    fd = tmp_path / "fd"
    arrays = {
        "FRAC1": ("I1", "injector", [0.8] * N),
        "FRAC2": ("I2", "injector", [0.2] * N),
        "FRACP1": ("P1", "producer", [1.0] * N),
        "RESTIME": ("all", "all", restime or [0.0] + [40.0] * (N - 1)),
    }
    manifest = {
        "case": str(tmp_path / "case/FD"),
        "step": 2,
        "date": "2020-03-01",
        "arrays": {},
        "failed": [],
    }
    for keyword, (well, role, values) in arrays.items():
        if keyword != omit:
            path = grdecl.write_grdecl(fd / "fd" / f"{keyword}.grdecl", keyword, values)
            manifest["arrays"][keyword] = {
                "file": str(path),
                "result": "Fraction",
                "well": well,
                "role": role,
            }
    (fd / "fd_export.json").write_text(json.dumps(manifest))
    write_connections(
        tmp_path / "connections.csv", [("P1", 1, 1, 1, "OPEN", "ok"), ("P1", 2, 1, 1, "OPEN", "ok")]
    )
    return fd


def metrics(tmp_path: Path, fd: Path, *extra: str) -> int:
    return fd_metrics.main(["--fd-dir", str(fd), "--outdir", str(tmp_path / "metrics"), *extra])


def test_metrics_end_to_end_on_a_uniform_case(tmp_path):
    fd = metrics_case(tmp_path)
    assert metrics(tmp_path, fd, "--completions", str(tmp_path)) == 0
    out = tmp_path / "metrics"
    result = json.loads((out / "fd_metrics.json").read_text())
    assert (result["lorenz"], result["f_at_phi_0_2"], result["fphi_cells"]) == (0.0, 0.2, 22)
    assert result["allocation"].startswith("inferred")
    with (out / "fphi.csv").open() as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["phi", "f"] and len(rows) == 102 and rows[-1] == ["1.0000", "1.000000"]
    with (out / "allocation.csv").open() as handle:
        shares = [(r["producer"], r["injector"], float(r["share"])) for r in csv.DictReader(handle)]
    assert shares == [("P1", "I1", 0.8), ("P1", "I2", 0.2)]
    report = (out / "report.md").read_text()
    assert "Lorenz coefficient: 0.000 over 22 cells" in report and "| P1 | I1 | 80% |" in report


def test_metrics_without_completions_has_no_allocation(tmp_path):
    assert metrics(tmp_path, metrics_case(tmp_path)) == 0
    assert not (tmp_path / "metrics/allocation.csv").exists()
    assert "Injector support" not in (tmp_path / "metrics/report.md").read_text()


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("no fd dir", "missing"),
        ("no RESTIME", "has no RESTIME"),
        ("array file gone", "FRAC1.grdecl"),
        ("wrong count", "has 3 values, expected 24"),
        ("no completions", "connections.csv"),
        ("outdir in case", "case folder"),
    ],
)
def test_metrics_input_errors_exit_2(tmp_path, capsys, setup, message):
    fd = metrics_case(tmp_path, omit="RESTIME" if setup == "no RESTIME" else "")
    extra: list[str] = []
    if setup == "no fd dir":
        fd = tmp_path / "nothing"
    elif setup == "array file gone":
        (fd / "fd/FRAC1.grdecl").unlink()
    elif setup == "wrong count":
        grdecl.write_grdecl(fd / "fd/FRAC2.grdecl", "FRAC2", [0.1, 0.2, 0.3])
    elif setup == "no completions":
        (tmp_path / "cig").mkdir()
        extra = ["--completions", str(tmp_path / "cig")]
    if setup == "outdir in case":
        code = fd_metrics.main(["--fd-dir", str(fd), "--outdir", str(tmp_path / "case/metrics")])
    else:
        code = metrics(tmp_path, fd, *extra)
    assert code == 2 and message in capsys.readouterr().err
    assert not (tmp_path / "metrics/fd_metrics.json").exists()
