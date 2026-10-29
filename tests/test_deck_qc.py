import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("res2df")

SCRIPT = Path(__file__).parents[1] / "skills/deck-scal-pvt-qc/scripts/deck_qc.py"
spec = importlib.util.spec_from_file_location("deck_qc", SCRIPT)
qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qc)


def run_include(tmp_path: Path, text: str, name: str = "t.inc") -> tuple[int, dict]:
    file = tmp_path / name
    file.write_text(text, encoding="latin-1")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = qc.main(["--include", str(file), "--json"])
    out = buffer.getvalue()
    return code, json.loads(out[out.index("{") :])


def codes(report: dict, severity: str | None = None) -> set[str]:
    return {f["code"] for f in report["findings"] if severity is None or f["severity"] == severity}


SWOF_OK = """SWOF
 0.2 0.0  1.0 0
 0.5 0.2  0.3 0
 0.8 0.6  0.0 0
 1.0 1.0  0.0 0 /
"""

SGOF_OK = """SGOF
 0.0 0.0 1.0 0
 0.3 0.2 0.3 0
 0.8 0.9 0.0 0 /
"""


def test_good_swof_has_no_findings(tmp_path):
    code, report = run_include(tmp_path, SWOF_OK)
    assert code == 0 and codes(report) - {"T001"} == set()


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("SWOF\n 0.2 0.0 1.0 0\n 0.5 0.5 0.3 0\n 0.8 0.3 0.0 0\n 1.0 1.0 0.0 0 /\n", "S003"),
        ("SWOF\n 0.2 0.0 0.5 0\n 0.5 0.2 0.9 0\n 0.8 0.6 0.0 0\n 1.0 1.0 0.0 0 /\n", "S003"),
        ("SWOF\n 0.2 0.0 1.0 0\n 0.5 0.2 0.3 0\n 0.8 0.6 0.0 0\n 1.0 1.5 0.0 0 /\n", "S002"),
        ("SWOF\n 0.2 0.0 1.0 0\n 0.5 0.2 0.3 0\n 0.5 0.6 0.0 0\n 1.0 1.0 0.0 0 /\n", "S001"),
    ],
    ids=["krw-decreases", "kro-increases", "kr-above-1", "saturation-not-increasing"],
)
def test_swof_errors(tmp_path, table, expected):
    code, report = run_include(tmp_path, table)
    assert code == 1 and expected in codes(report, "E")


def test_swof_not_ending_at_one_is_information_only(tmp_path):
    """SPE9 ends at Sw = 0.88 and it runs. This must not be a warning."""
    code, report = run_include(tmp_path, "SWOF\n 0.2 0.0 1.0 0\n 0.5 0.2 0.3 0\n 0.88 0.6 0.0 0 /\n")
    assert code == 0 and "S006" in codes(report, "I") and not codes(report, "W")


def test_krw_not_zero_at_first_row_is_warning(tmp_path):
    code, report = run_include(tmp_path, "SWOF\n 0.2 0.1 1.0 0\n 0.5 0.2 0.3 0\n 1.0 1.0 0.0 0 /\n")
    assert code == 0 and "S005" in codes(report, "W")


def test_capillary_pressure_not_monotone(tmp_path):
    code, report = run_include(
        tmp_path, "SWOF\n 0.2 0.0 1.0 3\n 0.5 0.2 0.3 1\n 0.8 0.6 0.0 2\n 1.0 1.0 0.0 0 /\n"
    )
    assert "S004" in codes(report, "W")


def test_three_phase_consistency(tmp_path):
    good = run_include(tmp_path, SWOF_OK + SGOF_OK)[1]
    assert not (codes(good) & {"S007", "S008", "S009"})
    bad_sgof = SGOF_OK.replace("0.0 0.0 1.0 0", "0.0 0.0 0.8 0")
    assert "S008" in codes(run_include(tmp_path, SWOF_OK + bad_sgof)[1], "W")
    big_sg = SGOF_OK.replace("0.8 0.9 0.0 0", "0.9 0.9 0.0 0")
    assert "S009" in codes(run_include(tmp_path, SWOF_OK + big_sg)[1], "W")
    late_start = SGOF_OK.replace("0.0 0.0 1.0 0", "0.05 0.0 1.0 0")
    assert "S007" in codes(run_include(tmp_path, SWOF_OK + late_start)[1], "W")


def test_satnum_tables_are_checked_separately(tmp_path):
    two = (
        SWOF_OK.replace(" /\n", " /\n", 1)
        + "\n"
        + "SWOF\n 0.2 0.0 1.0 0\n 0.5 0.5 0.3 0\n 0.8 0.3 0.0 0\n 1.0 1.0 0.0 0 /\n"
    )
    two = two.replace("SWOF\n 0.2 0.0  1.0 0", "SWOF\n 0.2 0.0  1.0 0", 1)
    # put both tables under one SWOF keyword so that they become SATNUM 1 and 2
    body_ok = " 0.2 0.0 1.0 0\n 0.5 0.2 0.3 0\n 0.8 0.6 0.0 0\n 1.0 1.0 0.0 0 /\n"
    body_bad = " 0.2 0.0 1.0 0\n 0.5 0.5 0.3 0\n 0.8 0.3 0.0 0\n 1.0 1.0 0.0 0 /\n"
    code, report = run_include(tmp_path, "SWOF\n" + body_ok + body_bad)
    assert code == 1
    where = [f["where"] for f in report["findings"] if f["code"] == "S003"]
    assert where == ["SWOF SATNUM 2"]


PVTO_OK = """PVTO
 0.1 1.0   1.05 1.0
     200.0 1.04 1.1 /
 50.0 100.0 1.20 0.8
      300.0 1.15 0.9 /
/
"""


def test_good_pvto(tmp_path):
    code, report = run_include(tmp_path, PVTO_OK)
    assert code == 0 and not (codes(report) - {"T001"})


@pytest.mark.parametrize(
    ("mutation", "expected", "severity"),
    [
        (lambda t: t.replace("50.0 100.0 1.20 0.8", "0.05 100.0 1.20 0.8"), "P002", "E"),  # Rs decreases
        (lambda t: t.replace("200.0 1.04 1.1", "200.0 1.09 1.1"), "P003", "W"),  # Bo rises
        (lambda t: t.replace("300.0 1.15 0.9", "300.0 1.15 0.5"), "P003", "W"),  # viscosity falls
        (lambda t: t.replace("1.0   1.05 1.0", "1.0   -1.05 1.0"), "P001", "E"),  # negative Bo
        (lambda t: t.replace("100.0 1.20 0.8", "0.5 1.20 0.8"), "P003", "W"),  # psat falls with Rs
    ],
)
def test_pvto_rules(tmp_path, mutation, expected, severity):
    code, report = run_include(tmp_path, mutation(PVTO_OK))
    assert expected in codes(report, severity), report["findings"]
    assert (code == 1) == (severity == "E")


def test_pvdg_rising_bg_and_pvtw_rock_density(tmp_path):
    text = (
        "PVDG\n 100 0.03 0.02\n 200 0.05 0.021\n 300 0.01 0.022 /\n"
        "PVTW\n 250 1.03 -0.00004 0.3 0.0001 /\n"
        "ROCK\n 280 -5.6E-5 /\n"
        "DENSITY\n 850 1000 0 /\n"
    )
    code, report = run_include(tmp_path, text)
    found = {(f["code"], f["where"].split()[0]) for f in report["findings"]}
    assert ("P004", "PVDG") in found
    assert ("P001", "PVTW") in found and ("P001", "ROCK") in found and ("P001", "DENSITY") in found
    assert code == 1


def test_equil_rules(tmp_path):
    ok = run_include(tmp_path, "EQUIL\n 2500 250 2600 0 2400 0 1 1 0 /\n")[1]
    assert not (codes(ok) & {"E001", "E002"})
    gas_below_water = run_include(tmp_path, "EQUIL\n 2500 250 2400 0 2600 0 1 1 0 /\n")[1]
    assert "E002" in codes(gas_below_water, "E")
    no_pressure = run_include(tmp_path, "EQUIL\n 2500 0 2600 0 2400 0 1 1 0 /\n")[1]
    assert "E001" in codes(no_pressure, "E")


def vfp_table(**changes) -> pd.DataFrame:
    rows = []
    for thp, base in ((10.0, 60.0), (20.0, 70.0), (30.0, 80.0)):
        for rate in (500.0, 1000.0):
            rows.append(
                {"RATE": rate, "PRESSURE": thp, "WFR": 0.0, "GFR": 50.0, "ALQ": 0.0, "TAB": base + rate / 100}
            )
    table = pd.DataFrame(rows)
    table["VFP_TYPE"], table["TABLE_NUMBER"], table["DATUM"], table["UNIT_TYPE"] = (
        "VFPPROD",
        1,
        1600.0,
        "METRIC",
    )
    for column, value in changes.items():
        table[column] = value
    return table


def vfp_findings(table, deck_units=None):
    found = qc.Findings()
    qc.check_vfp([table], found, deck_units)
    return {(r["code"], r["severity"]) for r in found.rows}


def test_vfp_rules():
    assert vfp_findings(vfp_table()) == set()
    assert ("V001", "E") in vfp_findings(vfp_table(TAB=-1.0))
    assert ("V003", "W") in vfp_findings(vfp_table(DATUM=0.0))
    assert ("V004", "W") in vfp_findings(vfp_table(UNIT_TYPE="FIELD"), deck_units="METRIC")
    falling = vfp_table()
    falling.loc[falling["PRESSURE"] == 30.0, "TAB"] = 50.0  # BHP drops when THP rises
    assert ("V002", "W") in vfp_findings(falling)
    nan = vfp_table()
    nan.loc[0, "TAB"] = float("nan")
    assert ("V001", "E") in vfp_findings(nan)


def test_usage_errors(tmp_path):
    assert qc.main([]) == 2
    assert qc.main(["--deck", str(tmp_path / "a.DATA"), "--include", str(tmp_path / "b.inc")]) == 2
    assert qc.main(["--include", str(tmp_path / "missing.inc")]) == 2


def test_latin1_include_is_read(tmp_path):
    """Prosper writes includes that are not UTF-8."""
    text = "-- Output File : C:\\Users\\Ol\xb3\\x.Ecl\n" + SWOF_OK
    code, report = run_include(tmp_path, text)
    assert code == 0


def test_script_writes_nothing(tmp_path):
    before = sorted(p.name for p in tmp_path.rglob("*"))
    run_include(tmp_path, SWOF_OK)
    assert sorted(p.name for p in tmp_path.rglob("*")) == [*before, "t.inc"]


FASTCHECK = Path(os.environ.get("OPM_FASTCHECK", Path.home() / "opm-fastcheck"))
DROGON = Path(os.environ.get("FMU_DROGON", Path.home() / "fmu-drogon"))


@pytest.mark.skipif(
    not (FASTCHECK / "spe1/SPE1CASE1.DATA").is_file(), reason="SPE1 deck not found. Set OPM_FASTCHECK."
)
def test_public_decks_have_no_errors():
    for deck in ("spe1/SPE1CASE1.DATA", "spe9/SPE9_CP.DATA"):
        if (FASTCHECK / deck).is_file():
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = qc.main(["--deck", str(FASTCHECK / deck), "--json"])
            report = json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])
            assert code == 0, report["findings"]
            assert report["unit_system"] == "FIELD" and report["rows_read"]["satfunc"] > 0


@pytest.mark.skipif(
    not (DROGON / "eclipse/include/props/drogon.pvt").is_file(),
    reason="fmu-drogon not found. Set FMU_DROGON.",
)
def test_drogon_real_includes(tmp_path):
    buffer = io.StringIO()
    includes = [
        DROGON / "eclipse/include/props/drogon.pvt",
        *sorted((DROGON / "eclipse/include/schedule/vfp").glob("*.inc")),
    ]
    with contextlib.redirect_stdout(buffer):
        code = qc.main([*(a for p in includes for a in ("--include", str(p))), "--json"])
    report = json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])
    assert code == 0, report["findings"]
    assert report["rows_read"]["pvt"] == 389 and report["rows_read"]["VFPPROD"] == 4


@pytest.mark.skipif(
    not (DROGON / "eclipse/model/DROGON_HIST.DATA").is_file(), reason="fmu-drogon not found. Set FMU_DROGON."
)
def test_drogon_whole_deck_fails_closed_with_the_parser_message():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = qc.main(["--deck", str(DROGON / "eclipse/model/DROGON_HIST.DATA"), "--json"])
    report = json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])
    assert code == 1
    assert any(f["code"] == "L001" and "IMPORT" in f["message"] for f in report["findings"])


def pyscal_executable() -> Path | None:
    import sys

    candidate = Path(sys.executable).parent / "pyscal"
    return candidate if candidate.is_file() else None


@pytest.mark.skipif(pyscal_executable() is None, reason="pyscal command not found next to the interpreter")
def test_pyscal_output_passes_and_a_broken_copy_fails(tmp_path):
    import subprocess

    table = tmp_path / "relperm.csv"
    table.write_text(
        "SATNUM, swl, sorw, Nw, Now, swcr, sorg, Ng, Nog\n"
        "1, 0.1, 0.05, 2, 3, 0.12, 0.08, 2, 3\n"
        "2, 0.15, 0.1, 3, 2, 0.17, 0.1, 2.5, 2\n"
    )
    include = tmp_path / "relperm.inc"
    subprocess.run(
        [str(pyscal_executable()), str(table), "--delta_s", "0.1", "-o", str(include)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    text = include.read_text()
    code, report = run_include(tmp_path, text, "good.inc")
    assert code == 0 and report["rows_read"]["satfunc"] > 0
    assert not (codes(report) & {"S001", "S002", "S003", "S007", "S008", "S009"})
    lines = text.splitlines()
    first_swof_row = next(i for i, line in enumerate(lines) if line.startswith("0.1000000 0.0000000"))
    # make krw fall at a later row of SATNUM 1
    columns = lines[first_swof_row + 4].split()
    columns[1] = "0.0000100"
    lines[first_swof_row + 4] = " ".join(columns)
    code, report = run_include(tmp_path, "\n".join(lines), "broken.inc")
    assert code == 1 and any(
        f["code"] == "S003" and f["where"] == "SWOF SATNUM 1" for f in report["findings"]
    )


def test_two_tables_in_separate_includes_do_not_spoil_each_other(tmp_path):
    """Regression: PVT (2 tables) and EQUIL (7 records) in two files failed when the text was joined."""
    pvt_text = (
        "ROCKOPTS\n 1* 1* ROCKNUM /\n"
        "ROCK\n 280 5.6E-5 /\n 280 5.6E-5 /\n"
        "PVTW\n 247.7 1.03665 0.4E-04 0.29 0.9E-04 /\n 247.7 1.03665 0.4E-04 0.29 0.9E-04 /\n"
        "DENSITY\n 852.9 999.0 0.9 /\n 861.0 999.1 1.0 /\n"
    )
    equil_text = "EQUIL\n" + "".join(f" 1750 310 {1660 + i} 0 1000 0 {i + 1} {i + 1} 0 /\n" for i in range(7))
    (tmp_path / "a.pvt").write_text(pvt_text)
    (tmp_path / "b.equil").write_text(equil_text)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = qc.main(
            ["--include", str(tmp_path / "a.pvt"), "--include", str(tmp_path / "b.equil"), "--json"]
        )
    report = json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])
    assert code == 0, report["findings"]
    assert report["rows_read"]["equil"] == 7 and report["rows_read"]["pvt"] == 6


def test_dimension_file_is_used_as_context(tmp_path):
    (tmp_path / "dims.inc").write_text("METRIC\nTABDIMS\n 1 2 /\n")
    (tmp_path / "t.inc").write_text(SWOF_OK)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = qc.main(
            ["--include", str(tmp_path / "t.inc"), "--include", str(tmp_path / "dims.inc"), "--json"]
        )
    report = json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])
    assert code == 0 and report["unit_system"] == "METRIC"


def make_deck(tmp_path: Path, body: str) -> Path:
    deck = tmp_path / "model" / "CASE.DATA"
    deck.parent.mkdir(parents=True)
    deck.write_text(body)
    return deck


def test_scan_finds_missing_and_existing_includes(tmp_path):
    (tmp_path / "include").mkdir()
    (tmp_path / "include/here.inc").write_text("-- a file\n")
    deck = make_deck(
        tmp_path,
        "RUNSPEC\nINCLUDE\n  '../include/here.inc' / -- exists\n"
        "INCLUDE\n  '../include/gone.inc' /\n"
        "-- INCLUDE\n--   '../include/commented.inc' /\n"
        "IMPORT '../include/binary.grdecl' /\n"
        "INCLUDE '$ROOT/x.inc' /\n",
    )
    found, missing = qc.scan_includes(deck)
    assert [p.name for p in found] == ["here.inc"]
    assert sorted((t, n) for _, n, t in missing) == [
        ("../include/binary.grdecl", 6),
        ("../include/gone.inc", 5),
    ] or sorted(t for _, _, t in missing) == ["../include/binary.grdecl", "../include/gone.inc"]
    assert not any("commented" in t or "$ROOT" in t for _, _, t in missing)


def test_scan_follows_nested_includes_and_survives_loops(tmp_path):
    deck = make_deck(tmp_path, "INCLUDE\n 'a.inc' /\n")
    (deck.parent / "a.inc").write_text("INCLUDE\n 'b.inc' /\n")
    (deck.parent / "b.inc").write_text("INCLUDE\n 'a.inc' /\nINCLUDE\n 'missing.inc' /\n")
    found, missing = qc.scan_includes(deck)
    assert {p.name for p in found} == {"a.inc", "b.inc"}
    assert [t for _, _, t in missing] == ["missing.inc"]
    assert missing[0][0].name == "b.inc"


def test_deck_mode_reports_missing_include_as_error(tmp_path):
    deck = make_deck(tmp_path, "RUNSPEC\nINCLUDE\n 'nowhere.inc' /\n")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = qc.main(["--deck", str(deck), "--json"])
    report = json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])
    assert code == 1
    assert any(f["code"] == "I001" and "nowhere.inc" in f["message"] for f in report["findings"])
    assert "_includes" not in report["rows_read"] and report["include_files_found"] == 0


@pytest.mark.skipif(
    not (DROGON / "eclipse/model/DROGON_HIST.DATA").is_file(), reason="fmu-drogon not found. Set FMU_DROGON."
)
def test_drogon_missing_rms_exports_are_listed():
    found, missing = qc.scan_includes(DROGON / "eclipse/model/DROGON_HIST.DATA")
    names = {Path(t).name for _, _, t in missing}
    assert {"drogon.grid", "drogon.sattab", "drogon.satnum", "drogon.pvtnum", "drogon_hist.sch"} <= names
    assert "drogon.tabdims" not in names  # the runspec includes exist


@pytest.mark.parametrize(("value", "severity", "code"), [(0, "W", "P005"), (-1e-5, "E", "P001")])
def test_rock_zero_is_legal_warning(tmp_path, value, severity, code):
    status, report = run_include(tmp_path, f"ROCK\n 400 {value} /\n")
    assert status == (1 if severity == "E" else 0)
    assert code in codes(report, severity)


def test_satfunc_row_limit_reports_overflow():
    frame = pd.DataFrame({"KEYWORD": ["SWOF"] * 4, "SATNUM": [1] * 4})
    found = qc.Findings()
    qc.check_satfunc_rows(frame, 3, found)
    assert found.rows == [
        {
            "code": "S010",
            "severity": "W",
            "where": "SWOF SATNUM 1",
            "message": "4 rows exceed TABDIMS NSSFUN 3",
        }
    ]


def test_deck_mode_reads_tabdims_row_limit(tmp_path):
    deck = tmp_path / "CASE.DATA"
    deck.write_text("RUNSPEC\nOIL\nWATER\nTABDIMS\n1 1 2 /\nPROPS\n" + SWOF_OK)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        status = qc.main(["--deck", str(deck), "--json"])
    report = json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])
    assert status == 0
    assert "S010" in codes(report, "W")
    assert "L001" not in codes(report)


def run_deck(deck: Path) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = qc.main(["--deck", str(deck), "--json"])
    return code, json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])


def swof_rows(count: int) -> str:
    rows = [
        f" {0.1 + 0.8 * i / (count - 1):.4f} {(i / (count - 1)) ** 2:.4f} {(1 - i / (count - 1)) ** 2:.4f} 0"
        for i in range(count)
    ]
    return "\n".join(rows) + " /\n"


def test_more_rows_than_nssfun_is_s010_not_t003(tmp_path):
    # PR3 owns saturation row overflow (S010). T003 covers PVT only, so the finding is not repeated.
    deck = make_deck(
        tmp_path,
        "RUNSPEC\nDIMENS\n 1 1 1 /\nOIL\nWATER\nTABDIMS\n 2* 4 2* /\nPROPS\nSWOF\n" + swof_rows(6),
    )
    code, report = run_deck(deck)
    assert "T003" not in codes(report)
    assert [(f["code"], f["severity"]) for f in report["findings"] if f["code"] == "S010"] == [("S010", "W")]


def test_more_tables_than_ntsfun_is_an_error(tmp_path):
    # res2df trusts TABDIMS and reads only the first NTSFUN tables. The text count sees all of them.
    file = tmp_path / "t.inc"
    file.write_text("TABDIMS\n 1 /\nSWOF\n" + swof_rows(3) + swof_rows(3) + swof_rows(3))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = qc.main(["--include", str(file), "--json"])
    report = json.loads(buffer.getvalue()[buffer.getvalue().index("{") :])
    assert code == 1
    assert [
        (f["code"], f["severity"], f["where"]) for f in report["findings"] if f["code"].startswith("T00")
    ][:1] == [("T002", "E", "SWOF")]
    assert report["rows_read"]["satfunc"] == 3


def test_tabdims_counts_pvto_records_and_rows():
    text = (
        "TABDIMS\n 1 1 20 2 1 1 /\nPVTO\n 10 100 1.1 1.0\n     200 1.05 1.1\n     300 1.03 1.2 /\n"
        " 20 150 1.2 0.9 /\n/\nPVDG\n 100 0.03 0.02\n 200 0.02 0.021 /\n"
    )
    found = qc.Findings()
    qc.check_tabdims(text, found, use_defaults=False)
    assert [(f["code"], f["where"], f["message"].split(".")[0]) for f in found.rows] == [
        ("T003", "PVTO table 1", "2 records, more than TABDIMS NRPVT 1"),
        ("T003", "PVTO table 1", "3 rows, more than TABDIMS NPPVT 2"),
    ]
    assert qc.tabdims("SWOF\n 0 0 1 0 /\n") is None
