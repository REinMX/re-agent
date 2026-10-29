"""remaining-oil: remaining_oil.py on synthetic resdata cases, window_sum and the greedy pick on hand arrays.

Real views: test_resinsight_real.py.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

import pytest
from synth_fmu import need
from synth_grid import make_case, write_restart

ROOT = Path(__file__).resolve().parents[1]
for _scripts in (ROOT / "skills/remaining-oil/scripts", ROOT / "skills/resinsight-views/scripts"):
    if str(_scripts) not in sys.path:
        sys.path.insert(0, str(_scripts))

import grdecl  # noqa: E402
import remaining_oil as ro  # noqa: E402
import view_spec as vs  # noqa: E402

DIMS = (3, 2, 2)
INACTIVE = 4  # global index of the inactive cell
ACTIVE = [g for g in range(12) if g != INACTIVE]
PORV = 1e5 * 0.2  # make_case: 1e5 * poro in each active cell
SWAT = [0.1, 0.2, 0.3, 0.9, 0.5, 0.6, 0.15, 0.25, 0.35, 0.45, 1.0]  # one value for each active cell
SGAS = [0.05] * 11


def case(tmp_path: Path, swat=SWAT) -> Path:
    """Step 0: SWAT only. Step 1: SWAT and SGAS. Return BASE."""
    need("resdata", "numpy")
    folder = tmp_path / "case"
    make_case(folder, base="RO", dims=DIMS, inactive=(INACTIVE,))
    steps = [
        (dt.date(2020, 1, 1), {"SWAT": list(swat)}),
        (dt.date(2020, 2, 1), {"SWAT": list(swat), "SGAS": SGAS}),
    ]
    write_restart(folder, "RO", DIMS, 11, steps)
    return folder / "RO"


def run(tmp_path: Path, base: Path, *extra: str, outdir: Path | None = None) -> int:
    outdir = outdir or tmp_path / "out"
    return ro.main(["--case", str(base), "--outdir", str(outdir), *extra])


def read(tmp_path: Path, name: str) -> list[float]:
    keyword, values = grdecl.read_grdecl(tmp_path / f"out/arrays/{name}.grdecl", expected=12)
    assert keyword == name
    return values


def expected_mobile(sor: float, sgas: list[float] | None = None) -> list[float]:
    full = [0.0] * 12
    for n, g in enumerate(ACTIVE):
        soil = 1.0 - SWAT[n] - (sgas[n] if sgas else 0.0)
        full[g] = PORV * max(soil - sor, 0.0)
    return full


@pytest.mark.parametrize(("step", "sgas"), [("0", None), ("1", SGAS), ("2020-02-01", SGAS), ("last", SGAS)])
def test_mobile_oil_with_sor_and_sgas_of_the_step(tmp_path, step, sgas):
    """Step 0 has no SGAS (SOIL = 1 - SWAT). Step 1 reads its own SGAS, not the n-th SGAS in the file."""
    assert (
        run(tmp_path, case(tmp_path), "--step", step, "--sor", "0.2", "--radius", "0", "--min-distance", "0")
        == 0
    )
    assert read(tmp_path, "MOBOIL") == pytest.approx(expected_mobile(0.2, sgas), rel=1e-6)
    summary = json.loads((tmp_path / "out/remaining_oil.json").read_text())
    assert summary["mobile_oil_rm3"] == pytest.approx(sum(expected_mobile(0.2, sgas)), rel=1e-6)
    assert summary["step"] == (0 if step == "0" else 1) and summary["rule"].startswith("Screening only")


def test_column_and_candidate_arrays_round_trip(tmp_path):
    assert run(tmp_path, case(tmp_path), "--radius", "0", "--min-distance", "1", "--count", "2") == 0
    mobile, column, rank = read(tmp_path, "MOBOIL"), read(tmp_path, "MOBCOL"), read(tmp_path, "CANDID")
    for g in range(12):
        i, j = g % 3, (g // 3) % 2
        assert column[g] == pytest.approx(mobile[i + 3 * j] + mobile[i + 3 * j + 6], rel=1e-6)
    with (tmp_path / "out/candidates.csv").open() as handle:
        candidates = list(csv.DictReader(handle))
    for c in candidates:
        i, j = int(c["i"]) - 1, int(c["j"]) - 1
        assert rank[i + 3 * j] == rank[i + 3 * j + 6] == int(c["rank"])
    assert sorted(rank) == [0.0] * 8 + [1.0, 1.0, 2.0, 2.0]
    with (tmp_path / "out/columns.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 6 and all(float(r["mobile_oil_rm3"]) > 0 for r in rows)


def test_window_sum_is_the_clipped_square_sum():
    need("numpy")
    import numpy as np

    grid = np.arange(20, dtype=float).reshape(5, 4) ** 1.5
    result = ro.window_sum(grid, 1)
    for i in range(5):
        for j in range(4):
            assert result[i, j] == pytest.approx(grid[max(i - 1, 0) : i + 2, max(j - 1, 0) : j + 2].sum())


def column_grid():
    """7x7 columns: 10 at (3,3), 9 at (3,4), 8 at (6,6), 1 at (1,7) (1-based i, j)."""
    need("numpy")
    import numpy as np

    oil = np.zeros((7, 7))
    oil[2, 2], oil[2, 3], oil[5, 5], oil[0, 6] = 10.0, 9.0, 8.0, 1.0
    return oil


def picked(candidates):
    return [(c.rank, c.i, c.j, c.nearest_well, c.distance_cells) for c in candidates]


def test_pick_skips_columns_near_earlier_candidates_and_stops_at_zero_oil():
    result = ro.pick(column_grid(), {}, [], min_distance=2, count=10, min_share=0.0)
    assert picked(result) == [(1, 3, 3, "", 10**9), (2, 6, 6, "candidate 1", 3), (3, 1, 7, "candidate 1", 4)]
    assert result[0].share_of_total == pytest.approx(10 / 28)


def test_pick_honors_min_distance_from_wells_and_avoid():
    wells = {"P1": [(3, 3), (3, 3)], "I1": [(7, 1)]}
    result = ro.pick(column_grid(), wells, [("PLAN", 6, 5)], min_distance=2, count=10, min_share=0.0)
    assert picked(result) == [(1, 1, 7, "P1", 4)]  # (3,4) is 1 from P1; (6,6) is 1 from PLAN


def test_pick_min_share_and_count_stop():
    assert len(ro.pick(column_grid(), {}, [], 2, 10, min_share=0.3)) == 1  # 10/28 >= 0.3 > 8/28
    assert len(ro.pick(column_grid(), {}, [], 0, 2, min_share=0.0)) == 2


def test_pick_window_score_ranks_the_centre_of_a_rich_area():
    need("numpy")
    import numpy as np

    oil = np.zeros((9, 9))
    oil[1:4, 1:4] = 3.0  # a 3x3 area, 27 in total
    oil[7, 7] = 20.0  # one rich column
    (first,) = ro.pick(oil, {}, [], 0, 1, 0.0, radius=1)
    assert (first.i, first.j, first.mobile_oil_rm3) == (3, 3, 27.0)


def test_completions_and_avoid_on_the_cli(tmp_path):
    base = case(tmp_path)
    connections = tmp_path / "cig/connections.csv"
    connections.parent.mkdir()
    connections.write_text("well,i,j,k\nP1,1,1,1\nP1,1,1,2\n")
    args = ("--radius", "0", "--min-distance", "1", "--count", "5", "--completions", str(connections.parent))
    assert run(tmp_path, base, *args, "--avoid", "NEW=3,2") == 0
    summary = json.loads((tmp_path / "out/remaining_oil.json").read_text())
    assert summary["wells"] == ["P1"] and summary["avoid"] == ["NEW"]
    cells = {(c["i"], c["j"]) for c in summary["candidates"]}
    assert (1, 1) not in cells and (3, 2) not in cells and cells
    assert all(c["distance_cells"] >= 1 for c in summary["candidates"])


def test_no_candidate_is_exit_1(tmp_path, capsys):
    assert run(tmp_path, case(tmp_path, swat=[1.0] * 11)) == 1
    summary = json.loads((tmp_path / "out/remaining_oil.json").read_text())
    assert summary["candidates"] == [] and summary["mobile_oil_rm3"] == 0
    assert "| - |" in (tmp_path / "out/report.md").read_text()


def test_spec_json_passes_parse_spec(tmp_path):
    base = case(tmp_path)
    assert run(tmp_path, base, "--view-case", f"{base}.EGRID") == 0
    spec = vs.load_spec(tmp_path / "out/spec.json")
    assert [v.name for v in spec.views] == ["mobcol", "candidates", "soil"]
    assert {a.name for a in spec.arrays} == {"MOBOIL", "MOBCOL", "CANDID"}
    assert spec.views[2].time_step == "2020-02-01" and spec.out == tmp_path / "out/views"
    assert not (tmp_path / "out2").exists()
    assert run(tmp_path, base, outdir=tmp_path / "out2") == 0 and not (tmp_path / "out2/spec.json").exists()


def test_outdir_guards(tmp_path, capsys):
    base = case(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out/old.txt").write_text("x")
    assert run(tmp_path, base) == 2 and "is not empty" in capsys.readouterr().err
    assert run(tmp_path, base, "--force") == 0
    assert run(tmp_path, base, outdir=tmp_path / "case/roil") == 2
    assert "case folder" in capsys.readouterr().err and not (tmp_path / "case/roil").exists()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--sor", "1"), "--sor must be in [0, 1)"),
        (("--count", "0"), "--count >= 1"),
        (("--avoid", "NEW=3"), "--avoid NEW=3: use NAME=I,J"),
        (("--completions", "/nothing/here.csv"), "completion_in_grid.py first"),
        (("--step", "5"), "--step 5: the case has 2 report steps"),
        (("--step", "2020-01-15"), "--step 2020-01-15 is not a report step"),
    ],
)
def test_input_errors_exit_2(tmp_path, capsys, extra, message):
    assert run(tmp_path, case(tmp_path), *extra) == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_missing_restart_exit_2(tmp_path, capsys):
    base = case(tmp_path)
    Path(f"{base}.UNRST").unlink()
    assert run(tmp_path, base) == 2 and "missing RO.UNRST" in capsys.readouterr().err
