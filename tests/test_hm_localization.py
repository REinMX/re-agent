"""hm-localization: obs file rewriting, well coordinates, the localization lint, and the sweep metrics.

Real sweep: set HM_LOC_STORAGE (an ERT 26 ENSPATH), HM_LOC_CONFIG (its .ert) and optional HM_LOC_TRUTH.
"""

from __future__ import annotations

import csv
import math
import os
import sys
from pathlib import Path

import pytest
from synth_fmu import need
from synth_grid import make_case

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/hm-localization/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import localize_obs as lo  # noqa: E402
import radius_sweep as rs  # noqa: E402

LOCATIONS = "well,east,north,source,n_connections\nOP1,150.00,150.00,compdat,2\nOP2,350.00,250.00,compdat,1\n"

OBS = """-- header comment; with { braces }
SUMMARY_OBSERVATION WOPR_OP1 { VALUE = 1; ERROR = 0.1; DATE = 2020-02-01; KEY = WOPR:OP1; };
SUMMARY_OBSERVATION WBHP_OP2
{
    VALUE = 200;  -- bar
    ERROR = 5;
    DATE  = 2020-02-01;
    KEY   = WBHP:OP2;
    LOCALIZATION { EAST = 1; NORTH = 2; RADIUS = 3000; };
};
SUMMARY_OBSERVATION FOPT_1 { VALUE = 10; ERROR = 1; DATE = 2020-02-01; KEY = FOPT; };
SUMMARY_OBSERVATION GOPR_1 { VALUE = 10; ERROR = 1; DATE = 2020-02-01; KEY = GOPR:G1; };
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def rewrite(tmp_path: Path, obs: str = OBS, *extra: str) -> tuple[int, Path]:
    source = write(tmp_path / "obs/in.obs", obs)
    locations = write(tmp_path / "loc.csv", LOCATIONS)
    out = tmp_path / "obs/out.obs"
    code = lo.main([str(source), "--locations", str(locations), "--radius", "250", "--out", str(out), *extra])
    return code, out


# localize_obs --------------------------------------------------------------------------------------------
def test_tokenizer_keeps_every_character():
    assert "".join(t.text for t in lo.tokenize(OBS)) == OBS


def test_key_family():
    assert lo.key_family("WOPR:OP1") == ("WOPR", "OP1")
    assert lo.key_family("FOPT") == ("FOPT", None)
    assert lo.key_family("GOPR:G1") == ("GOPR", None)


def test_rewrite_adds_replaces_and_keeps_layout(tmp_path, capsys):
    code, out = rewrite(tmp_path)
    text = out.read_text()
    assert code == 1  # FOPT and GOPR have no location
    assert text.splitlines()[0] == "-- header comment; with { braces }"
    assert (
        "KEY = WOPR:OP1; LOCALIZATION { EAST = 150.00; NORTH = 150.00; RADIUS = 250; }; };"
        in text.splitlines()[1]
    )
    assert "    LOCALIZATION { EAST = 350.00; NORTH = 250.00; RADIUS = 250; };\n};" in text
    assert "RADIUS = 3000" not in text and text.count("LOCALIZATION") == 2
    assert "VALUE = 200;  -- bar" in text
    report = capsys.readouterr().out
    assert "| FOPT | 0 | 0 | 1 |" in report and "| GOPR | 0 | 0 | 1 |" in report
    assert "excluded from a FIELD or SURFACE update with DISTANCE" in report


def test_rewrite_is_idempotent(tmp_path):
    _, first = rewrite(tmp_path)
    again = tmp_path / "again"
    code, second = rewrite(again, first.read_text())
    assert code == 1 and second.read_text() == first.read_text()


def test_bulk_summary_gets_well_blocks(tmp_path):
    write(
        tmp_path / "obs/values.csv",
        "keyword,well,value,error,date\nWOPR,OP1,1,0.1,2020-02-01\nWWCT,OP2,0.1,0.05,2020-02-01\nFOPT,,10,1,2020-02-01\n",
    )
    obs = "SUMMARY {\n  VALUES = values.csv;\n  WELL OP2 { LOCALIZATION { EAST = 0; NORTH = 0; }; };\n};\n"
    code, out = rewrite(tmp_path, obs)
    text = out.read_text()
    assert code == 1
    assert "WELL OP2 { LOCALIZATION { EAST = 350.00; NORTH = 250.00; RADIUS = 250; }; };" in text
    assert "  WELL OP1 { LOCALIZATION { EAST = 150.00; NORTH = 150.00; RADIUS = 250; }; };" in text


def test_missing_well_fails_and_writes_nothing(tmp_path, capsys):
    code, out = rewrite(tmp_path, OBS.replace("WOPR:OP1", "WOPR:OP9"))
    assert code == 2 and not out.exists()
    assert "OP9" in capsys.readouterr().err


def test_broken_obs_and_bad_radius_fail(tmp_path):
    assert rewrite(tmp_path, "SUMMARY_OBSERVATION A { KEY = WOPR:OP1;\n")[0] == 2
    source = write(tmp_path / "x.obs", OBS)
    locations = write(tmp_path / "l.csv", LOCATIONS)
    assert (
        lo.main([str(source), "--locations", str(locations), "--radius", "0", "--out", str(tmp_path / "o")])
        == 2
    )
    assert lo.main([str(source), "--locations", str(locations), "--radius", "1", "--out", str(source)]) == 2


def test_inplace_and_all_located_exit_zero(tmp_path):
    source = write(tmp_path / "in.obs", OBS.split("SUMMARY_OBSERVATION FOPT_1")[0])
    locations = write(tmp_path / "loc.csv", LOCATIONS)
    assert lo.main([str(source), "--locations", str(locations), "--radius", "100", "--inplace"]) == 0
    assert source.read_text().count("RADIUS = 100;") == 2


def test_ert_parser_reads_the_output(tmp_path):
    need("ert")
    _, out = rewrite(tmp_path)
    assert lo.ert_located_count(out.read_text(), str(out), out.parent) == 2


# obs_locations ---------------------------------------------------------------------------------------------
def test_well_locations_pure():
    need("numpy")
    import numpy as np
    import obs_locations as ol

    x = np.broadcast_to((np.arange(4) * 100 + 50.0)[:, None, None], (4, 3, 2))
    y = np.broadcast_to((np.arange(3) * 100 + 50.0)[None, :, None], (4, 3, 2))
    rows = ol.well_locations({"A": [(1, 1, 1), (1, 1, 1), (2, 1, 2)]}, {"A": (1, 1), "B": (4, 3)}, x, y)
    assert [(r.well, r.source, r.east, r.north, r.n_connections) for r in rows] == [
        ("A", "compdat", 100.0, 50.0, 2),
        ("A", "welspecs", 50.0, 50.0, 0),
        ("B", "welspecs", 350.0, 250.0, 0),
    ]
    with pytest.raises(ol.InputError, match="outside the grid"):
        ol.mean_centre([(5, 1, 1)], x, y)


MINI_DECK = """RUNSPEC
DIMENS
 4 3 2 /
OIL
WATER
START
 1 JAN 2020 /
GRID
SCHEDULE
WELSPECS
 'OP1' 'G' 2 2 1* 'OIL' /
 'OP2' 'G' 4 3 1* 'OIL' /
/
COMPDAT
 'OP1' 2 2 1 2 'OPEN' /
 'OP1' 3 2 1 1 'OPEN' /
 'OP2' 4 3 1 1 'SHUT' /
/
"""


def test_obs_locations_on_a_deck_and_grid(tmp_path):
    need("res2df", "xtgeo", "resdata", "numpy")
    import obs_locations as ol

    egrid = make_case(tmp_path / "case", base="MINI", dims=(4, 3, 2))
    data = write(tmp_path / "case/MINI.DATA", MINI_DECK)
    out = tmp_path / "loc.csv"
    assert ol.main(["--data", str(data), "--grid", str(egrid), "--out", str(out)]) == 1  # OP2 is shut
    with out.open() as handle:
        rows = [
            (r["well"], r["source"], r["east"], r["north"], r["n_connections"])
            for r in csv.DictReader(handle)
        ]
    assert rows == [
        ("OP1", "compdat", "183.33", "150.00", "3"),
        ("OP1", "welspecs", "150.00", "150.00", "0"),
        ("OP2", "welspecs", "350.00", "250.00", "0"),
    ]


def test_obs_locations_missing_file(tmp_path):
    need("res2df", "xtgeo")
    import obs_locations as ol

    assert (
        ol.main(["--data", str(tmp_path / "no.DATA"), "--grid", str(tmp_path / "no.EGRID"), "--out", "x"])
        == 2
    )


# loc_lint ---------------------------------------------------------------------------------------------------
CONFIG = """NUM_REALIZATIONS 2
ECLBASE SYNTH
GRID SYNTH.EGRID
FIELD PERMX PARAMETER permx.grdecl INIT_FILES:permx_%d.grdecl INIT_TRANSFORM:LN OUTPUT_TRANSFORM:EXP
GEN_KW MULT mult.dist
SUMMARY WOPR:* FOPT
OBS_CONFIG obs.txt
"""
LINT_OBS = (
    "SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 0.1; DATE = 2020-02-01; KEY = WOPR:OP1;"
    " LOCALIZATION { EAST = 150; NORTH = 150; RADIUS = %s; }; };\n"
    "SUMMARY_OBSERVATION B { VALUE = 1; ERROR = 0.1; DATE = 2020-02-01; KEY = FOPT; };\n"
)


def lint_case(tmp_path: Path, extra: str, radius: str = "150", obs: str | None = None) -> tuple[int, str]:
    need("ert", "resdata", "xtgeo")
    import loc_lint

    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    make_case(tmp_path, base="SYNTH", dims=(4, 3, 2))
    write(tmp_path / "permx_0.grdecl", "PERMX\n" + " ".join(["1.0"] * 24) + "\n/\n")
    write(tmp_path / "mult.dist", "A UNIFORM 0 1\n")
    write(tmp_path / "obs.txt", obs if obs is not None else LINT_OBS % radius)
    config = write(tmp_path / "c.ert", CONFIG + extra)
    return loc_lint.main([str(config)]), config.name


def test_lint_distance_with_unlocated_obs(tmp_path, capsys):
    code, _ = lint_case(tmp_path, "ANALYSIS_SET_VAR PARAMETERS FIELD DISTANCE\n")
    out = capsys.readouterr().out
    assert code == 1
    assert "parameter PERMX (FIELD): DISTANCE" in out
    assert "1 observation(s) with a location, 1 without" in out
    assert "no location (FOPT 1)" in out
    assert "1.5-1.5 cells of 100 m" in out


def test_lint_all_located_is_clean(tmp_path, capsys):
    obs = LINT_OBS.split("\n")[0].replace("%s", "150") + "\n"
    code, _ = lint_case(tmp_path, "ANALYSIS_SET_VAR PARAMETERS FIELD DISTANCE\n", obs=obs)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "0 error(s), 0 warning(s)" in out


def test_lint_no_location_is_error(tmp_path, capsys):
    obs = "SUMMARY_OBSERVATION B { VALUE = 1; ERROR = 0.1; DATE = 2020-02-01; KEY = FOPT; };\n"
    code, _ = lint_case(tmp_path, "ANALYSIS_SET_VAR PARAMETERS FIELD DISTANCE\n", obs=obs)
    assert code == 1 and "no observation has a LOCALIZATION block" in capsys.readouterr().out


def test_lint_default_and_large_radius(tmp_path, capsys):
    obs = LINT_OBS.replace(" RADIUS = %s;", "")
    code, _ = lint_case(tmp_path, "ANALYSIS_SET_VAR PARAMETERS FIELD DISTANCE\n", obs=obs)
    out = capsys.readouterr().out
    assert code == 1
    assert "RADIUS 3000 m, the ERT default" in out
    assert "larger than the extent of PERMX (400 x 300 m, diagonal 500 m)" in out


def test_lint_small_radius(tmp_path, capsys):
    lint_case(tmp_path, "ANALYSIS_SET_VAR PARAMETERS FIELD DISTANCE\n", radius="20")
    assert "smaller than the cell size of PERMX (100 m)" in capsys.readouterr().out


def test_lint_gen_kw_distance_is_error(tmp_path, capsys):
    code, _ = lint_case(tmp_path, "ANALYSIS_SET_VAR PARAMETERS GEN_KW DISTANCE\n")
    out = capsys.readouterr().out
    assert code == 1 and "DISTANCE strategy is not supported for GEN_KW" in out and "1 error(s)" in out


def ert_major() -> int:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return int(version("ert").split(".")[0])
    except (PackageNotFoundError, ValueError):
        return 0


def test_lint_legacy_switch_is_warning(tmp_path, capsys):
    if ert_major() < 26:
        pytest.skip("the pre-ERT-26 switch message comes from ERT 26 config parsing")
    code, _ = lint_case(
        tmp_path, "ANALYSIS_SET_VAR PARAMETERS FIELD DISTANCE\nANALYSIS_SET_VAR STD_ENKF LOCALIZATION True\n"
    )
    out = capsys.readouterr().out
    assert code == 1 and "pre-ERT-26 switch" in out
    assert (
        "parameter PERMX (FIELD): ADAPTIVE" in out
    )  # the legacy line overrides the PARAMETERS line before it


def test_radius_findings_pure():
    need("ert")
    import loc_lint

    box = loc_lint.Box("F", 480.0, 480.0, 8.0, (0.0, 0.0), 0.0)
    assert loc_lint.radius_findings([], [box]) == []
    texts = [f.message for f in loc_lint.radius_findings([200.0], [box])]
    assert texts == ["F: RADIUS 200-200 m = 25.0-25.0 cells of 8 m; the field is 480 x 480 m"]
    assert loc_lint.outside(box, [("in", 10.0, 10.0), ("out", 500.0, 10.0)]) == ["out"]


# radius_sweep metrics ---------------------------------------------------------------------------------------
def test_parse_radii():
    assert rs.parse_radii("200, 50,inf,50") == [50.0, 200.0, math.inf]
    with pytest.raises(rs.InputError):
        rs.parse_radii("0,100")
    with pytest.raises(rs.InputError):
        rs.parse_radii(" , ")


def test_field_metrics_on_hand_arrays():
    need("numpy")
    import numpy as np

    prior = np.array([[0.0, 2.0, 4.0, 6.0], [1.0, 1.0, 1.0, 1.0], [0.0, 1.0, 2.0, 3.0]])
    posterior = np.array([[1.0, 2.0, 3.0, 4.0], [5.0, 5.0, 5.0, 5.0], [2.9, 3.0, 3.0, 3.1]])
    truth = np.array([3.0, 9.0, 3.0])
    m = rs.field_metrics(prior, posterior, truth, collapse=0.2)
    # cell 1 has no prior spread and is left out
    assert m.n_cells == 2
    assert m.median_sd_ratio == pytest.approx((0.5 + 0.0816497 / 1.2909944) / 2, rel=1e-5)
    assert m.collapse_share == 0.5
    assert m.mean_abs_shift_sd == pytest.approx((0.5 / 2.5819889 + 1.5 / 1.2909944) / 2, rel=1e-5)
    assert m.truth_in_p5_p95 == 1.0
    assert m.rmse_mean == pytest.approx(math.sqrt((0.5**2 + 0.0**2) / 2))
    with pytest.raises(rs.InputError):
        rs.field_metrics(prior[1:2], posterior[1:2])


def test_rank_rows():
    rows = [
        {"radius": "prior", "collapse_share": None},
        {"radius": 50.0, "collapse_share": 0.0, "rmse_mean": 0.9},
        {"radius": 200.0, "collapse_share": 0.01, "rmse_mean": 0.7},
        {"radius": math.inf, "collapse_share": 0.3, "rmse_mean": 0.6},
    ]
    assert [r["radius"] for r in rs.rank(rows, 0.05)] == [200.0, 50.0, math.inf]
    plain = [{k: v for k, v in r.items() if k != "rmse_mean"} for r in rows]
    assert [r["radius"] for r in rs.rank(plain, 0.05)] == [200.0, 50.0, math.inf]
    assert [r["radius"] for r in rs.rank(plain, 0.5)] == [math.inf, 200.0, 50.0]


def test_sweep_refuses_a_folder_that_is_not_a_storage(tmp_path, capsys):
    need("ert", "xtgeo")
    config = write(tmp_path / "c.ert", "NUM_REALIZATIONS 1\n")
    (tmp_path / "st").mkdir()
    code = rs.main(
        [
            "--storage",
            str(tmp_path / "st"),
            "--config",
            str(config),
            "--prior",
            "iter-0",
            "--out",
            str(tmp_path / "o"),
        ]
    )
    assert code == 2 and "not an ERT storage" in capsys.readouterr().err


# real sweep ------------------------------------------------------------------------------------------------
STORAGE = os.environ.get("HM_LOC_STORAGE")


@pytest.mark.skipif(
    not STORAGE, reason="Set HM_LOC_STORAGE (ERT 26 ENSPATH) and HM_LOC_CONFIG to run the sweep"
)
def test_real_radius_sweep(tmp_path):
    need("ert", "xtgeo")
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    field = os.environ.get("HM_LOC_FIELD", "PERMX")
    args = ["--storage", STORAGE, "--config", os.environ["HM_LOC_CONFIG"], "--prior", "iter-0"]
    args += ["--radii", "100,200,inf", "--field", field, "--out", str(tmp_path / "sweep")]
    if truth := os.environ.get("HM_LOC_TRUTH"):
        args += ["--truth-field", truth]
    assert rs.main(args) == 0
    with (tmp_path / "sweep/sweep.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert [r["radius"] for r in rows] == ["prior", "100", "200", "inf"]
    assert list(rows[0]) == list(rs.CSV_FIELDS)
    ratios = [float(r["median_sd_ratio"]) for r in rows[1:]]
    assert ratios == sorted(ratios, reverse=True), ratios
    assert all(0 < r <= 1 for r in ratios)
    assert "not the ES-MDA chain" in (tmp_path / "sweep/sweep.md").read_text()


def test_radius_auto_uses_well_spacing(tmp_path, capsys):
    source = write(tmp_path / "obs/in.obs", OBS)
    rows = ["well,east,north,source"] + [
        f"{w},{x},{y},compdat"
        for w, (x, y) in zip(
            ("OP1", "OP2", "P3", "I1", "I2"),
            [(675, 525), (225, 675), (925, 625), (375, 125), (1325, 575)],
            strict=True,
        )
    ]
    locations = write(tmp_path / "loc.csv", "\n".join(rows) + "\n")
    out = tmp_path / "obs/out.obs"
    code = lo.main([str(source), "--locations", str(locations), "--radius", "auto", "--out", str(out)])
    assert code in (0, 1)
    assert "RADIUS auto 504 m" in capsys.readouterr().out and "RADIUS = 504" in out.read_text()
    assert lo.main([str(source), "--locations", str(locations), "--radius", "far", "--out", str(out)]) == 2
