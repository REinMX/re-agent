"""Tests for the region-in-place skill. Self-contained: synthetic fixtures in tmp_path, no simulator."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

SKILL = Path(__file__).resolve().parents[1] / "skills" / "region-in-place"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fip_keywords as fk  # noqa: E402
from region_in_place_fixtures import region_values, write_prt, write_summary  # noqa: E402

pytest.importorskip("resdata")
pytest.importorskip("openpyxl")


def run(script: str, *args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True, cwd=cwd
    )


# ----------------------------------------------------------------------------- keyword rules
def test_region_keyword_naming_rule():
    assert fk.region_keyword("OIP", "FIPNUM") == "ROIP"
    assert fk.region_keyword("OIP", "FIPSEG") == "ROIP_SEG"
    assert fk.region_keyword("GIP", "FIPSEG") == "RGIP_SEG"
    assert fk.region_keyword("OIPL", "FIPSEG") == "ROIPLSEG"
    assert fk.region_keyword("PR", "FIPSEG") == "RPR__SEG"
    assert fk.region_keyword("OIP", "FIPUNIT") == "ROIP_UNI"  # only 3 characters after FIP
    assert fk.canonical_set("FIPUNIT") == "FIPUNI"


def test_split_region_keyword_roundtrip():
    fams = fk.FAMILY_ORDER
    for fam in fams:
        for st in ("FIPNUM", "FIPSEG", "FIPRES"):
            assert fk.split_region_keyword(fk.region_keyword(fam, st), fams) == (fam, st)
    assert fk.split_region_keyword("FOIP", fams) is None
    assert fk.split_region_keyword("RPR", fams) is None


def test_suffix_collision_detected():
    assert fk.check_unique_suffixes(["FIPNUM", "FIPUNIT", "FIPUNIX"])
    assert not fk.check_unique_suffixes(["FIPNUM", "FIPSEG", "FIPRES"])


def test_parse_families():
    assert fk.parse_families(None) == ["OIP", "GIP"]
    assert fk.parse_families("all") == fk.FAMILY_ORDER
    assert fk.parse_families("FOIP,rgip".upper()) == ["OIP", "GIP"]
    with pytest.raises(ValueError):
        fk.parse_families("SFOIP")


# ----------------------------------------------------------------------------- include generator
DECK = """RUNSPEC
DIMENS
  2 2 2 /
OIL
WATER
GAS
DISGAS
METRIC
TABDIMS
  1 1 20 20 1 20 /
GRID
INCLUDE
  'big/grid.inc' /
REGIONS
INCLUDE
  '../include/regions/fip.inc' /   -- quoted path with slashes
EQUALS
  FIPGL 1 1 2 1 2 1 1 /
  FIPGL 4 1 2 1 2 2 2 /
/
COPY
  FIPNUM FIPCOP /
/
SUMMARY
SCHEDULE
END
"""


def _deck(tmp_path: Path) -> Path:
    model = tmp_path / "eclipse" / "model"
    reg = tmp_path / "eclipse" / "include" / "regions"
    model.mkdir(parents=True)
    reg.mkdir(parents=True)
    (reg / "fip.inc").write_text("FIPNUM\n  4*1 4*3 /\nFIPSEG\n  2*1 2*2 4*2 /\n")
    (model / "CASE.DATA").write_text(DECK)
    return model / "CASE.DATA"


def test_make_fip_summary_from_deck(tmp_path):
    deck = _deck(tmp_path)
    out = tmp_path / "fip.summary"
    rep = tmp_path / "rep.json"
    r = run(
        "make_fip_summary.py",
        "--deck",
        deck,
        "--families",
        "OIP,GIP",
        "--with-pressure",
        "-o",
        out,
        "--json",
        rep,
    )
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    for kw in (
        "FOIP",
        "FGIP",
        "FPR",
        "ROIP\n/",
        "RGIP\n/",
        "ROIP_SEG\n/",
        "RGIP_SEG\n/",
        "RPR__SEG\n/",
        "ROIP_GL\n/",
        "ROIP_COP\n/",
    ):
        assert kw in text, kw
    data = json.loads(rep.read_text())
    assert data["region_sets"]["FIPNUM"]["max"] == 3
    assert data["region_sets"]["FIPGL"]["max"] == 4
    assert data["region_sets"]["FIPCOP"]["max"] == 3
    # TABDIMS NTFIP = 1 < 4 -> warning about ECLIPSE dimensioning
    assert "NTFIP" in r.stderr and "WARNING" in r.stderr
    assert any("grid.inc" in s for s in data["skipped_includes"])  # GRID includes are not read


def test_make_fip_summary_region_sets_and_collision(tmp_path):
    r = run("make_fip_summary.py", "--region-sets", "FIPNUM,FIPRES", "--families", "all")
    assert r.returncode == 0
    assert "ROIPLRES" in r.stdout and "RWIP_RES" in r.stdout
    r = run("make_fip_summary.py", "--region-sets", "FIPUNIT,FIPUNIX")
    assert r.returncode == 2 and "suffix" in r.stderr


# ----------------------------------------------------------------------------- extractor, single case
def _case(tmp_path: Path, values=None) -> Path:
    return write_summary(tmp_path / "run" / "eclipse" / "model" / "CASE", values or region_values())


def test_single_case_tables_and_qc(tmp_path):
    base = _case(tmp_path)
    out = tmp_path / "out"
    r = run("region_in_place.py", "--case", f"{base}.DATA", "--dates", "first,2027-01-01,last", "--out", out)
    assert r.returncode == 0, r.stderr
    at = pd.read_csv(out / "at_dates.csv")
    row = at[(at.region_set == "FIPSEG") & (at.region == 1) & (at.vector == "OIP")].iloc[0]
    assert row["first 2025-02-01"] == pytest.approx(700.0)
    assert row["2027-01-01"] == pytest.approx(700.0 - 120)
    assert row["last 2028-01-01"] == pytest.approx(700.0 - 180)
    qc = pd.read_csv(out / "qc_sum_check.csv")
    assert set(qc.status) == {"PASS"} and set(qc.region_set) == {"FIPNUM", "FIPSEG"}
    sheets = pd.ExcelFile(out / "region_in_place.xlsx").sheet_names
    assert sheets[:3] == ["README", "at_dates", "OIP"] and "GIP" in sheets and "qc_sum_check" in sheets
    pv = pd.read_excel(out / "region_in_place.xlsx", "OIP")
    assert list(pv.columns[:4]) == ["date", "FIELD", "FIPNUM 1", "FIPNUM 2"]
    long = pd.read_csv(out / "region_in_place_long.csv")
    first = long[(long.region_set == "FIPNUM") & (long.region == 1) & (long.vector == "OIP")]
    assert first.change_since_first.tolist() == [0.0, -50.0, -100.0, -150.0]
    man = json.loads((out / "manifest.json").read_text())
    assert man["info"]["initial_source"] is None  # no PRT, no START step


def test_initial_refused_without_start_value(tmp_path):
    base = _case(tmp_path)
    r = run("region_in_place.py", "--case", base, "--dates", "initial", "--out", tmp_path / "o")
    assert r.returncode == 2 and "initial" in r.stderr and "RPTSOL" in r.stderr


def test_initial_from_prt_report0(tmp_path):
    base = _case(tmp_path)
    write_prt(
        base.with_suffix(".PRT"),
        {
            ("FIELD", 0): (1010.0, 80800.0),
            ("FIPNUM", 1): (606.0, 48480.0),
            ("FIPNUM", 2): (404.0, 32320.0),
            ("FIPSEG", 1): (707.0, 56560.0),
            ("FIPSEG", 2): (303.0, 24240.0),
        },
    )
    out = tmp_path / "o"
    r = run("region_in_place.py", "--case", base, "--dates", "initial,last", "--out", out)
    assert r.returncode == 0, r.stderr
    long = pd.read_csv(out / "region_in_place_long.csv")
    ini = long[long.date == "2025-01-01"]
    assert set(ini.source) == {"PRT"}
    assert (
        ini[(ini.region_set == "FIPSEG") & (ini.region == 1) & (ini.vector == "OIP")].value.iloc[0] == 707.0
    )
    assert ini[(ini.region_set == "FIELD") & (ini.vector == "GIP")].value.iloc[0] == 80800.0
    at = pd.read_csv(out / "at_dates.csv")
    assert "initial 2025-01-01" in at.columns
    qc = pd.read_csv(out / "qc_sum_check.csv")
    assert (qc.status == "PASS").all() and (qc.n_dates == 5).all()


def test_qc_fail_when_regions_do_not_add_up(tmp_path):
    v = region_values()
    v["ROIP_SEG:2"] = [x - 25.0 for x in v["ROIP_SEG:2"]]  # e.g. cells with FIPSEG = 0
    base = _case(tmp_path, v)
    out = tmp_path / "o"
    r = run("region_in_place.py", "--case", base, "--out", out)
    assert r.returncode == 3
    qc = pd.read_csv(out / "qc_sum_check.csv")
    bad = qc[qc.status != "PASS"]
    assert list(zip(bad.region_set, bad.vector, strict=True)) == [("FIPSEG", "OIP")]
    assert bad.status.iloc[0] == "FAIL_REGIONS_LOW"


def test_missing_vector_exit2_lists_keywords_never_zero(tmp_path):
    v = {k: x for k, x in region_values().items() if not k.startswith("RGIP")}
    base = _case(tmp_path, v)
    out = tmp_path / "o"
    r = run("region_in_place.py", "--case", base, "--families", "OIP,GIP", "--out", out)
    assert r.returncode == 2
    miss = json.loads((out / "missing_vectors.json").read_text())
    assert set(miss["missing_keywords"]) == {"RGIP", "RGIP_SEG"}
    assert "RGIP_SEG\n/" in miss["summary_include_to_add"]
    assert not (out / "region_in_place_long.csv").exists()


def test_no_region_vectors_at_all(tmp_path):
    base = _case(tmp_path, {"FOIP": [1.0, 1.0, 1.0, 1.0]})
    out = tmp_path / "o"
    r = run("region_in_place.py", "--case", base, "--families", "OIP", "--out", out)
    assert r.returncode == 2
    assert json.loads((out / "missing_vectors.json").read_text())["missing_keywords"] == ["ROIP"]


def test_flow_phase_split_note_for_user_sets(tmp_path):
    base = _case(tmp_path)
    out = tmp_path / "o"
    r = run("region_in_place.py", "--case", base, "--families", "OIPL", "--out", out)
    assert r.returncode == 2
    miss = json.loads((out / "missing_vectors.json").read_text())
    assert "ROIPLSEG" in miss["opm_flow_cannot_write"]


@pytest.mark.parametrize(
    "token,code", [("2026-01-02", 0), ("2026-06-01", 2), ("2030-01-01", 2), ("2024-12-01", 2), ("someday", 2)]
)
def test_date_tolerance_and_no_extrapolation(tmp_path, token, code):
    base = _case(tmp_path)
    r = run("region_in_place.py", "--case", base, "--dates", token, "--out", tmp_path / "o")
    assert r.returncode == code, r.stderr
    if code == 0:
        at = pd.read_csv(tmp_path / "o" / "at_dates.csv")
        assert "2026-01-02 (step 2026-01-01)" in at.columns


def test_yearly_axis(tmp_path):
    base = _case(tmp_path)
    out = tmp_path / "o"
    assert run("region_in_place.py", "--case", base, "--yearly", "--out", out).returncode == 0
    long = pd.read_csv(out / "region_in_place_long.csv")
    assert sorted(long.date.unique()) == ["2026-01-01", "2027-01-01", "2028-01-01"]


def test_contract_eclbase_report_dates_and_runpath(tmp_path):
    _case(tmp_path)
    contract = tmp_path / "site.contract.yml"
    contract.write_text(
        "eclbase: eclipse/model/CASE\np10_p90: numerical\nreport_dates:\n  mid: '2027-01-01'\n"
        f"paths:\n  deliverables: {tmp_path / 'deliv'}\n"
    )
    r = run(
        "region_in_place.py",
        "--contract",
        contract,
        "--runpath",
        tmp_path / "run",
        "--dates",
        "mid,last",
        "--label",
        "t1",
    )
    assert r.returncode == 0, r.stderr
    at = pd.read_csv(tmp_path / "deliv" / "region_in_place" / "t1" / "at_dates.csv")
    assert "mid 2027-01-01" in at.columns


def test_runpath_without_eclbase_refused(tmp_path):
    _case(tmp_path)
    r = run("region_in_place.py", "--runpath", tmp_path / "run", "--out", tmp_path / "o")
    assert r.returncode == 2 and "eclbase" in r.stderr


# ----------------------------------------------------------------------------- groups
def test_groups_sum_and_unmapped(tmp_path):
    base = _case(tmp_path)
    g = tmp_path / "g.csv"
    g.write_text('grouping,region_set,region,group\nreservoir,FIPNUM,"1,2",ALL\nsegment,FIPSEG,1,A\n')
    out = tmp_path / "o"
    r = run("region_in_place.py", "--case", base, "--groups", g, "--dates", "first,last", "--out", out)
    assert r.returncode == 0, r.stderr
    gl = pd.read_csv(out / "groups_long.csv")
    allr = gl[(gl.group == "ALL") & (gl.vector == "OIP")].sort_values("date")
    assert allr.value.tolist() == pytest.approx([1000.0, 920.0, 840.0, 760.0])
    um = pd.read_csv(out / "groups_unmapped.csv")
    assert list(zip(um.grouping, um.region_set, um.region, strict=True)) == [("segment", "FIPSEG", 2)]
    assert "groups_unmapped" in pd.ExcelFile(out / "region_in_place.xlsx").sheet_names


def test_groups_yaml_and_errors(tmp_path):
    base = _case(tmp_path)
    y = tmp_path / "g.yml"
    y.write_text("segment:\n  FIPSEG:\n    A: [1]\n    B: 2\n")
    assert run("region_in_place.py", "--case", base, "--groups", y, "--out", tmp_path / "o1").returncode == 0
    bad = tmp_path / "bad.yml"
    bad.write_text("segment:\n  FIPSEG:\n    A: 1-3\n")  # region 3 does not exist
    r = run("region_in_place.py", "--case", base, "--groups", bad, "--out", tmp_path / "o2")
    assert r.returncode == 2 and "FIPSEG:3" in r.stderr
    dup = tmp_path / "dup.yml"
    dup.write_text("segment:\n  FIPSEG:\n    A: 1\n    B: 1-2\n")
    r = run("region_in_place.py", "--case", base, "--groups", dup, "--out", tmp_path / "o3")
    assert r.returncode == 2 and "two groups" in r.stderr


# ----------------------------------------------------------------------------- ensemble
def _ensemble(tmp_path: Path, n=12, ok=True) -> Path:
    scratch = tmp_path / "scratch"
    for r in range(n):
        rp = scratch / f"realization-{r}" / "iter-0"
        write_summary(rp / "eclipse" / "model" / "CASE", region_values(scale=1.0 + 0.1 * r))
        if ok:
            (rp / "OK").write_text("")
    return scratch


def _contract(tmp_path: Path, conv: str) -> Path:
    c = tmp_path / f"c_{conv}.yml"
    c.write_text(f"eclbase: eclipse/model/CASE\np10_p90: {conv}\npaths:\n  deliverables: {tmp_path / 'd'}\n")
    return c


def test_ensemble_stats_numerical_and_exceedance(tmp_path):
    scratch = _ensemble(tmp_path)
    outs = {}
    for conv in ("numerical", "exceedance"):
        r = run(
            "region_in_place.py",
            "--contract",
            _contract(tmp_path, conv),
            "--scratch",
            scratch,
            "--dates",
            "first,last",
            "--label",
            conv,
        )
        assert r.returncode == 0, r.stderr
        outs[conv] = pd.read_csv(tmp_path / "d" / "region_in_place" / conv / "at_dates_stats.csv")
    num, exc = outs["numerical"], outs["exceedance"]

    def sel(data):
        return data[
            (data.region_set == "FIPNUM")
            & (data.region == 1)
            & (data.vector == "OIP")
            & (data.label == "first")
        ].iloc[0]

    a, b = sel(num), sel(exc)
    assert a.n == 12 and a.p10 < a.p50 < a.p90
    assert b.p10 == pytest.approx(a.p90) and b.p90 == pytest.approx(a.p10)
    assert a["mean"] == pytest.approx(600.0 * (1 + 0.1 * 5.5))
    assert set(num.p10_p90) == {"numerical"}
    readme = pd.read_excel(
        tmp_path / "d" / "region_in_place" / "numerical" / "region_in_place.xlsx", "README"
    )
    assert readme.set_index("item").loc["P10/P90 convention", "value"].startswith("numerical")


def test_ensemble_small_n_leaves_percentiles_blank(tmp_path):
    scratch = _ensemble(tmp_path)
    r = run(
        "region_in_place.py",
        "--contract",
        _contract(tmp_path, "numerical"),
        "--scratch",
        scratch,
        "--reals",
        "0-4",
        "--label",
        "small",
    )
    assert r.returncode == 0, r.stderr
    st = pd.read_csv(tmp_path / "d" / "region_in_place" / "small" / "at_dates_stats.csv")
    assert (st.n == 5).all() and st.p10.isna().all() and st["mean"].notna().all()


def test_ensemble_missing_ok_file_fails_closed(tmp_path):
    scratch = _ensemble(tmp_path, n=3, ok=False)
    (scratch / "realization-0" / "iter-0" / "OK").write_text("")
    c = _contract(tmp_path, "numerical")
    r = run("region_in_place.py", "--contract", c, "--scratch", scratch, "--label", "p")
    assert r.returncode == 1 and "realization 1" in r.stderr
    r = run("region_in_place.py", "--contract", c, "--scratch", scratch, "--allow-partial", "--label", "p2")
    assert r.returncode == 0
    man = json.loads((tmp_path / "d" / "region_in_place" / "p2" / "manifest.json").read_text())
    assert man["n_cases"] == 1 and len(man["failed"]) == 2


def test_ensemble_groups_stats(tmp_path):
    scratch = _ensemble(tmp_path)
    g = tmp_path / "g.yml"
    g.write_text("reservoir:\n  FIPNUM:\n    ALL: 1-2\n")
    r = run(
        "region_in_place.py",
        "--contract",
        _contract(tmp_path, "numerical"),
        "--scratch",
        scratch,
        "--groups",
        g,
        "--label",
        "g",
    )
    assert r.returncode == 0, r.stderr
    gs = pd.read_csv(tmp_path / "d" / "region_in_place" / "g" / "groups_at_dates_stats.csv")
    assert (gs.n == 12).all()


# ----------------------------------------------------------------------------- synthetic integration
def test_synthetic_run_qc_and_prt_crosscheck(tmp_path):
    initial = {
        ("FIELD", 0): (1000.0, 80000.0),
        ("FIPNUM", 1): (600.0, 48000.0),
        ("FIPNUM", 2): (400.0, 32000.0),
        ("FIPSEG", 1): (700.0, 56000.0),
        ("FIPSEG", 2): (300.0, 24000.0),
    }
    values = {}
    for (region_set, region), (oil, gas) in initial.items():
        for family, value in {
            "OIP": oil,
            "OIPL": oil,
            "OIPG": 0.0,
            "GIP": gas,
            "GIPL": gas,
            "GIPG": 0.0,
            "WIP": 2000.0 if region_set == "FIELD" else 1000.0,
        }.items():
            keyword = (
                fk.field_keyword(family) if region_set == "FIELD" else fk.region_keyword(family, region_set)
            )
            key = keyword if region_set == "FIELD" else f"{keyword}:{region}"
            values[key] = [value, value]
    base = write_summary(tmp_path / "run" / "CASE", values, days=[0, 31])
    write_prt(base.with_suffix(".PRT"), initial)
    r = run(
        "region_in_place.py",
        "--case",
        base,
        "--families",
        "OIP,GIP,WIP",
        "--dates",
        "initial,last",
        "--out",
        tmp_path / "s",
    )
    assert r.returncode == 0, r.stderr
    r = run("region_in_place.py", "--case", base, "--families", "all", "--from-prt", "--out", tmp_path / "p")
    assert r.returncode == 0, r.stderr
    x = pd.read_csv(tmp_path / "p" / "qc_prt_vs_summary.csv")
    assert x.rel_diff.max() < 1e-3
