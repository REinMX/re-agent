"""grid-property-qc: grid_qc.py and completion_in_grid.py on synthetic EGRID + INIT files made with resdata."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from synth_fmu import need

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/grid-property-qc/scripts"
GRID_QC = SCRIPTS / "grid_qc.py"
COMPLETION = SCRIPTS / "completion_in_grid.py"
DIMS = (3, 2, 2)
CELL = (10.0, 10.0, 5.0)
# Global index 4 = (i, j, k) (2, 2, 1) is inactive. Active index 4 is global index 5 = (3, 2, 1).
ACTNUM = [1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1]
N_ACTIVE = sum(ACTNUM)


def base_arrays() -> dict:
    need("numpy")
    import numpy as np

    n = N_ACTIVE
    return {
        "PORO": np.full(n, 0.2),
        "NTG": np.ones(n),
        "PERMX": np.full(n, 100.0),
        "PERMY": np.full(n, 100.0),
        "PERMZ": np.full(n, 10.0),
        "FIPNUM": np.ones(n, dtype=int),
        "SATNUM": np.ones(n, dtype=int),
        "PVTNUM": np.ones(n, dtype=int),
        "EQLNUM": np.ones(n, dtype=int),
    }


def write_case(folder: Path, arrays: dict | None = None, porv=None, omit=(), name: str = "CASE") -> Path:
    """EGRID and INIT for a 3x2x2 grid with one inactive cell. PORV = volume * PORO * NTG unless given."""
    need("resdata", "numpy")
    import numpy as np
    from resdata import ResDataType
    from resdata.grid import GridGenerator
    from resdata.resfile import FortIO, ResdataKW

    folder.mkdir(parents=True, exist_ok=True)
    base = folder / name
    GridGenerator.create_rectangular(DIMS, CELL, actnum=ACTNUM).save_EGRID(str(base) + ".EGRID")
    values = arrays or base_arrays()
    if porv is None:
        porv = np.zeros(len(ACTNUM))
        porv[np.flatnonzero(ACTNUM)] = np.prod(CELL) * values["PORO"] * values["NTG"]
    fortio = FortIO(str(base) + ".INIT", FortIO.WRITE_MODE)
    for kw_name, data in [("PORV", porv), *values.items()]:
        if kw_name in omit:
            continue
        data = np.asarray(data)
        kind = ResDataType.RD_INT if data.dtype.kind == "i" else ResDataType.RD_FLOAT
        kw = ResdataKW(kw_name, len(data), kind)
        for index, value in enumerate(data.tolist()):
            kw[index] = value
        kw.fwrite(fortio)
    fortio.close()
    return base


def run(script: Path, *args: str, python: list[str] | None = None) -> subprocess.CompletedProcess:
    command = [*(python or [sys.executable]), str(script), *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def grid_qc(base: Path, outdir: Path, *extra: str) -> tuple[int, dict]:
    done = run(GRID_QC, "--case", str(base), "--outdir", str(outdir), *extra)
    report = json.loads((outdir / "grid_qc.json").read_text()) if (outdir / "grid_qc.json").is_file() else {}
    return done.returncode, report


def codes(report: dict) -> list[str]:
    return [f["code"] for f in report["findings"]]


def test_clean_case_has_no_findings_and_writes_all_outputs(tmp_path):
    base = write_case(tmp_path / "case")
    code, report = grid_qc(base, tmp_path / "out")
    assert code == 0
    assert report["findings"] == [] and report["notes"] == []
    assert report["dims"] == list(DIMS) and report["n_active"] == N_ACTIVE
    assert report["stats"]["porv_ratio_quantiles"]["p50"] == pytest.approx(1.0, rel=1e-5)
    layers = (tmp_path / "out/per_layer.csv").read_text().splitlines()
    assert layers[0] == "k,n_active,porv_sum,poro_mean,permx_p10,permx_p50,permx_p90,ntg_mean"
    assert layers[1].startswith("1,5,500.0,0.2,") and layers[2].startswith("2,6,600.0,0.2,")
    regions = (tmp_path / "out/per_region.csv").read_text().splitlines()
    assert "FIPNUM,1,11,1100.0,0.2" in regions
    assert "No findings." in (tmp_path / "out/report.md").read_text()


def test_case_given_as_egrid_path(tmp_path):
    base = write_case(tmp_path / "case")
    code, report = grid_qc(Path(str(base) + ".EGRID"), tmp_path / "out")
    assert code == 0 and report["case"] == str(base)


@pytest.mark.parametrize(
    ("name", "index", "value", "code", "severity", "exit_code", "cells"),
    [
        ("PORO", 0, float("nan"), "GRID-NAN", "error", 1, [[1, 1, 1]]),
        ("PERMX", 1, float("inf"), "GRID-NAN", "error", 1, [[2, 1, 1]]),
        ("PORO", 4, 0.6, "GRID-RANGE-PORO", "error", 1, [[3, 2, 1]]),
        ("PORO", 4, -0.01, "GRID-RANGE-PORO", "error", 1, [[3, 2, 1]]),
        ("NTG", 4, 1.2, "GRID-RANGE-NTG", "error", 1, [[3, 2, 1]]),
        ("PERMY", 4, -1.0, "GRID-NEG-PERM", "error", 1, [[3, 2, 1]]),
        ("FIPNUM", 4, 0, "GRID-REGION-ZERO", "error", 1, [[3, 2, 1]]),
        ("EQLNUM", 4, 3, "GRID-REGION-GAP", "info", 0, []),
    ],
)
def test_each_array_finding_is_reported_at_the_right_cell(
    tmp_path, name, index, value, code, severity, exit_code, cells
):
    need("numpy")
    import numpy as np

    arrays = base_arrays()
    arrays[name][index] = value
    porv = np.zeros(len(ACTNUM))
    porv[np.flatnonzero(ACTNUM)] = 500.0 * 0.2  # PORV does not follow the edit, so only one check fires
    rc, report = grid_qc(write_case(tmp_path / "case", arrays, porv=porv), tmp_path / "out")
    assert rc == exit_code
    # a PORO or NTG edit also moves PORV / bulk pore volume; that warning is not under test here
    assert codes(report)[0] == code and set(codes(report)) <= {code, "GRID-PORV-VS-BULK"}
    found = report["findings"][0]
    assert found["severity"] == severity and found["count"] == 1 and found["cells"] == cells
    if code == "GRID-REGION-GAP":
        assert "EQLNUM numbers 1..3 with no active cell: 2" in found["message"]


def test_active_index_maps_past_the_inactive_cell(tmp_path):
    """Active index 3 is (1, 2, 1). Active index 4 skips the inactive (2, 2, 1) and is (3, 2, 1)."""
    arrays = base_arrays()
    arrays["PORO"][3] = 0.7
    arrays["PORO"][4] = 0.8
    rc, report = grid_qc(write_case(tmp_path / "case", arrays), tmp_path / "out")
    assert rc == 1
    poro = [f for f in report["findings"] if f["code"] == "GRID-RANGE-PORO"][0]
    assert poro["count"] == 2 and poro["cells"] == [[1, 2, 1], [3, 2, 1]]


def test_active_cell_with_zero_porv_is_an_error(tmp_path):
    need("numpy")
    import numpy as np

    porv = np.zeros(len(ACTNUM))
    porv[np.flatnonzero(ACTNUM)] = 100.0
    porv[5] = 0.0
    rc, report = grid_qc(write_case(tmp_path / "case", porv=porv), tmp_path / "out")
    assert rc == 1
    assert codes(report) == ["GRID-ACTIVE-ZERO-PORV"]
    assert report["findings"][0]["cells"] == [[3, 2, 1]]


def test_porv_far_from_bulk_pore_volume_is_a_warning_with_quantiles(tmp_path):
    need("numpy")
    import numpy as np

    porv = np.zeros(len(ACTNUM))
    porv[np.flatnonzero(ACTNUM)] = 100.0
    porv[11] = 300.0
    rc, report = grid_qc(write_case(tmp_path / "case", porv=porv), tmp_path / "out")
    assert rc == 0
    assert codes(report) == ["GRID-PORV-VS-BULK"]
    found = report["findings"][0]
    assert found["severity"] == "warning" and found["count"] == 1 and found["cells"] == [[3, 2, 2]]
    assert "p100 3" in found["message"]
    assert report["stats"]["porv_ratio_quantiles"]["p100"] == pytest.approx(3.0, rel=1e-5)


def test_permz_above_permx_in_a_large_fraction_is_a_warning(tmp_path):
    arrays = base_arrays()
    arrays["PERMZ"][:2] = 200.0
    rc, report = grid_qc(write_case(tmp_path / "case", arrays), tmp_path / "out")
    assert rc == 0
    assert codes(report) == ["GRID-PERMZ-GT-PERMX"]
    assert report["findings"][0]["count"] == 2
    rc, report = grid_qc(write_case(tmp_path / "case2", arrays), tmp_path / "out2", "--permz-fraction", "0.5")
    assert rc == 0 and report["findings"] == []


def test_values_on_the_limits_give_no_finding(tmp_path):
    need("numpy")
    import numpy as np

    arrays = base_arrays()
    arrays["PORO"][0], arrays["PORO"][1] = 0.5, 0.0
    arrays["NTG"][2] = 0.0
    arrays["PERMZ"][3] = 100.0
    porv = np.zeros(len(ACTNUM))
    active = np.flatnonzero(ACTNUM)
    porv[active] = 500.0 * arrays["PORO"] * arrays["NTG"]
    porv[active[0]] *= 1.9
    porv[active[1]] = porv[active[2]] = 1.0
    rc, report = grid_qc(write_case(tmp_path / "case", arrays, porv=porv), tmp_path / "out")
    # PORO 0 and NTG 0 with PORV > 0 have no bulk pore volume: that is a PORV-VS-BULK warning, not an error
    assert rc == 0
    assert codes(report) == ["GRID-PORV-VS-BULK"]
    assert report["findings"][0]["count"] == 2


def test_missing_optional_arrays_are_notes_not_findings(tmp_path):
    base = write_case(tmp_path / "case", omit=("NTG", "PERMZ", "SATNUM", "EQLNUM"))
    rc, report = grid_qc(base, tmp_path / "out")
    assert rc == 0 and report["findings"] == []
    notes = " ".join(report["notes"])
    for name in ("NTG", "PERMZ", "SATNUM", "EQLNUM"):
        assert name in notes
    assert "NTG = 1" in notes


@pytest.mark.parametrize("omit", ["PORO", "PORV"])
def test_missing_poro_or_porv_is_exit_2(tmp_path, omit):
    base = write_case(tmp_path / "case", omit=(omit,))
    done = run(GRID_QC, "--case", str(base), "--outdir", str(tmp_path / "out"))
    assert done.returncode == 2
    assert f"no {omit}" in done.stderr
    assert not (tmp_path / "out").exists()


def test_missing_init_is_exit_2(tmp_path):
    base = write_case(tmp_path / "case")
    Path(str(base) + ".INIT").unlink()
    done = run(GRID_QC, "--case", str(base), "--outdir", str(tmp_path / "out"))
    assert done.returncode == 2 and "CASE.INIT" in done.stderr


def test_missing_resdata_is_exit_2(tmp_path):
    base = write_case(tmp_path / "case")
    done = run(GRID_QC, "--case", str(base), "--outdir", str(tmp_path / "out"), python=[sys.executable, "-S"])
    assert done.returncode == 2
    assert "is not installed" in done.stderr


def test_outdir_must_be_empty_unless_force_and_outside_the_case_folder(tmp_path):
    base = write_case(tmp_path / "case")
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.txt").write_text("x")
    done = run(GRID_QC, "--case", str(base), "--outdir", str(out))
    assert done.returncode == 2 and "not empty" in done.stderr
    assert run(GRID_QC, "--case", str(base), "--outdir", str(out), "--force").returncode == 0
    done = run(GRID_QC, "--case", str(base), "--outdir", str(tmp_path / "case" / "qc"))
    assert done.returncode == 2 and "case folder" in done.stderr
    assert sorted(p.name for p in (tmp_path / "case").iterdir()) == ["CASE.EGRID", "CASE.INIT"]


# ---------------- completion_in_grid.py ----------------

DECK = """RUNSPEC
DIMENS
 3 2 2 /
OIL
WATER
WELLDIMS
 8 4 2 8 /
START
 1 JAN 2020 /
GRID
DX
 12*10 /
DY
 12*10 /
DZ
 12*5 /
TOPS
 6*1000 /
SCHEDULE
WELSPECS
{welspecs}
/
COMPDAT
{compdat}
/
TSTEP
 1 /
"""


def write_deck(folder: Path, wells: dict[str, list[str]]) -> Path:
    """wells: name -> COMPDAT 'I J K1 K2' strings. WELSPECS head is the first connection."""
    welspecs, compdat = [], []
    for name, connections in wells.items():
        i, j = connections[0].split()[:2]
        i, j = (i, j) if i != "*" else ("1", "1")
        welspecs.append(f" '{name}' 'G' {i} {j} 1* OIL /")
        for c in connections:
            compdat.append(
                f" '{name}' {c.replace('*', '2*', 1) if c.startswith('*') else c} OPEN 1* 1* 0.2 /"
            )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "DECK.DATA"
    path.write_text(DECK.format(welspecs="\n".join(welspecs), compdat="\n".join(compdat)))
    return path


def completion(deck: Path, base: Path, outdir: Path, *extra: str) -> tuple[subprocess.CompletedProcess, dict]:
    done = run(COMPLETION, "--data", str(deck), "--case", str(base), "--outdir", str(outdir), *extra)
    path = outdir / "completion_in_grid.json"
    return done, json.loads(path.read_text()) if path.is_file() else {}


def test_clean_completions_and_defaulted_ij_from_welspecs(tmp_path):
    need("res2df")
    base = write_case(tmp_path / "case")
    deck = write_deck(tmp_path / "deck", {"P1": ["* 1 2"], "P2": ["3 2 1 1"]})
    done, report = completion(deck, base, tmp_path / "out")
    assert done.returncode == 0, done.stderr
    assert report["findings"] == [] and report["wells"] == 2 and report["connections"] == 3
    rows = (tmp_path / "out/connections.csv").read_text().splitlines()
    assert rows[0] == "well,date,i,j,k,status,active,porv,permx,permy,poro,flag"
    p1 = sorted(r for r in rows if r.startswith("P1,"))
    assert [r.split(",")[2:5] for r in p1] == [["1", "1", "1"], ["1", "1", "2"]]
    assert all(r.endswith(",ok") and ",OPEN,True," in r for r in rows[1:])


def test_each_completion_finding(tmp_path):
    need("res2df", "numpy")
    import numpy as np

    arrays = base_arrays()
    # active index 3 = global 3 = (1, 2, 1): zero perm. active index 6 = global 7 = (2, 1, 2): low perm.
    arrays["PERMX"][3] = arrays["PERMY"][3] = 0.0
    arrays["PERMX"][6] = arrays["PERMY"][6] = 0.001
    porv = np.zeros(len(ACTNUM))
    porv[np.flatnonzero(ACTNUM)] = 100.0
    porv[8] = 0.0  # (3, 1, 2) active with no pore volume
    base = write_case(tmp_path / "case", arrays, porv=porv)
    deck = write_deck(
        tmp_path / "deck",
        {
            "OK": ["1 1 1 1"],
            "OUT": ["3 2 1 1", "4 2 1 1"],
            "DEAD": ["2 2 1 1"],
            "ZPERM": ["1 2 1 1"],
            "LOW": ["2 1 2 2"],
            "ZPORV": ["3 1 2 2"],
        },
    )
    done, report = completion(deck, base, tmp_path / "out")
    assert done.returncode == 1, done.stderr
    by_code = {f["code"]: f for f in report["findings"]}
    assert set(by_code) == {
        "COMP-OUTSIDE",
        "COMP-INACTIVE",
        "COMP-ZERO-PORV",
        "COMP-ZERO-PERM",
        "COMP-LOW-PERM",
        "COMP-WELL-NO-OPEN-CELL",
    }
    assert by_code["COMP-OUTSIDE"]["cells"] == [[4, 2, 1]] and "OUT" in by_code["COMP-OUTSIDE"]["message"]
    assert by_code["COMP-INACTIVE"]["cells"] == [[2, 2, 1]]
    assert by_code["COMP-ZERO-PORV"]["cells"] == [[3, 1, 2]]
    assert by_code["COMP-ZERO-PERM"]["cells"] == [[1, 2, 1]]
    assert (
        by_code["COMP-LOW-PERM"]["cells"] == [[2, 1, 2]] and by_code["COMP-LOW-PERM"]["severity"] == "warning"
    )
    no_open = [f for f in report["findings"] if f["code"] == "COMP-WELL-NO-OPEN-CELL"]
    assert [f["message"] for f in no_open] == ["well DEAD: no connection is in an active cell"]
    flags = {
        r.split(",")[0]: r.split(",")[-1]
        for r in (tmp_path / "out/connections.csv").read_text().splitlines()[1:]
    }
    assert flags["OK"] == "ok" and flags["LOW"] == "COMP-LOW-PERM"
    done, report = completion(deck, base, tmp_path / "out2", "--min-perm", "0.0001")
    assert "COMP-LOW-PERM" not in [f["code"] for f in report["findings"]]


def test_low_perm_alone_is_exit_0(tmp_path):
    need("res2df")
    arrays = base_arrays()
    arrays["PERMX"][0] = arrays["PERMY"][0] = 0.001
    base = write_case(tmp_path / "case", arrays)
    done, report = completion(write_deck(tmp_path / "deck", {"LOW": ["1 1 1 1"]}), base, tmp_path / "out")
    assert done.returncode == 0
    assert [f["code"] for f in report["findings"]] == ["COMP-LOW-PERM"]


def test_deck_that_res2df_cannot_parse_is_exit_2_with_the_parser_message(tmp_path):
    need("res2df")
    base = write_case(tmp_path / "case")
    deck = write_deck(tmp_path / "deck", {"BAD": ["abc 1 1 1"]})
    done, _ = completion(deck, base, tmp_path / "out")
    assert done.returncode == 2
    assert "res2df cannot read" in done.stderr and "abc" in done.stderr
    assert not (tmp_path / "out/completion_in_grid.json").exists()


def test_deck_without_compdat_is_exit_2(tmp_path):
    need("res2df")
    base = write_case(tmp_path / "case")
    deck = tmp_path / "deck" / "EMPTY.DATA"
    deck.parent.mkdir()
    deck.write_text("RUNSPEC\nDIMENS\n 3 2 2 /\nOIL\nSTART\n 1 JAN 2020 /\nGRID\nSCHEDULE\nTSTEP\n 1 /\n")
    done, _ = completion(deck, base, tmp_path / "out")
    assert done.returncode == 2 and "no COMPDAT" in done.stderr


def test_completion_refuses_outdir_in_the_deck_folder(tmp_path):
    need("res2df")
    base = write_case(tmp_path / "case")
    deck = write_deck(tmp_path / "deck", {"OK": ["1 1 1 1"]})
    done, _ = completion(deck, base, tmp_path / "deck" / "qc")
    assert done.returncode == 2 and "deck folder" in done.stderr


# ---------------- case-qc wiring ----------------


def test_case_qc_runs_grid_steps_when_egrid_and_init_are_next_to_the_deck(tmp_path):
    need("res2df", "numpy")
    folder = tmp_path / "model"
    deck = write_deck(folder, {"OK": ["1 1 1 1"], "DEAD": ["2 2 1 1"]})
    write_case(folder, name="DECK")
    case_qc = Path(__file__).resolve().parents[1] / "skills/case-qc/scripts/case_qc.py"
    done = run(case_qc, "--outdir", str(tmp_path / "qc"), "--deck", str(deck))
    report = json.loads((tmp_path / "qc/report.json").read_text())
    assert report["steps"]["grid"]["status"] == "ok"
    assert report["steps"]["completions"]["status"] == "problems"
    assert "COMP-INACTIVE" in report["steps"]["completions"]["facts"]
    assert (tmp_path / "qc/grid/files/grid_qc.json").is_file()
    assert done.returncode == 1
    assert sorted(p.name for p in folder.iterdir()) == ["DECK.DATA", "DECK.EGRID", "DECK.INIT"]
