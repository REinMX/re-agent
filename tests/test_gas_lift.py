"""gas-lift skill: VFP ALQ QC, post-run diagnostics, lift-gas sweep; scenario-build WLIFTOPT items; PATHS includes."""

from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, run, yaml_dump

SCRIPTS = SKILLS / "gas-lift/scripts"
QC = SCRIPTS / "gas_lift_qc.py"
DIAG = SCRIPTS / "gas_lift_diag.py"
SWEEP = SCRIPTS / "gas_lift_sweep.py"
for folder in (SCRIPTS, SKILLS / "scenario-build/scripts", SKILLS / "scenario-qc/scripts"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


def vfp(
    alq: list[float], kind: str = "'GRAT'", bhp: list[list[float]] | None = None, units: str = "'METRIC'"
) -> str:
    """VFPPROD 1: rates 100, 500; THP 20; WCT 0; GOR 100; ALQ axis `alq`. BHP rows per ALQ value."""
    rows = bhp or [[200 - 10 * i, 190 - 10 * i] if i < 2 else [190, 180] for i in range(len(alq))]
    body = "\n".join(f" 1 1 1 {n + 1} {a} {b} /" for n, (a, b) in enumerate(rows))
    axis = " ".join(f"{a:g}" for a in alq)
    return f"""VFPPROD
 1 2000 'LIQ' 'WCT' 'GOR' 'THP' {kind} {units} 'BHP' /
 100 500 /
 20 /
 0 /
 100 /
 {axis} /
{body}
"""


def deck_text(schedule: str, table: str) -> str:
    return f"""RUNSPEC
DIMENS
 3 3 1 /
OIL
WATER
GAS
METRIC
START
 1 JAN 2020 /
WELLDIMS
 4 4 3 4 /
VFPPDIMS
 6 6 6 6 6 2 /
GRID
DX
 9*100 /
DY
 9*100 /
DZ
 9*10 /
TOPS
 9*2000 /
PORO
 9*0.2 /
SCHEDULE
GRUPTREE
 'G1' 'FIELD' /
/
{table}
WELSPECS
 'P1' 'G1' 3 3 1* 'OIL' /
 'P2' 'G1' 1 3 1* 'OIL' /
 'I1' 'G1' 1 1 1* 'WATER' /
/
COMPDAT
 'P1' 3 3 1 1 'OPEN' 2* 0.2 /
 'P2' 1 3 1 1 'OPEN' 2* 0.2 /
 'I1' 1 1 1 1 'OPEN' 2* 0.2 /
/
WCONPROD
 'P*' 'OPEN' 'ORAT' 500 4* 50 20 1 /
/
{schedule}
DATES
 1 'FEB' 2020 /
 1 'MAR' 2020 /
/
TSTEP
 3*30 /
END
"""


LIFT = """LIFTOPT
 12500 5E-3 0 'YES' /
WLIFTOPT
 'P*' 'YES' 80000 1.0 /
/
GLIFTOPT
 'G1' 100000 /
/
"""


def write_deck(tmp_path: Path, schedule: str = LIFT, table: str | None = None, name: str = "CASE") -> Path:
    path = tmp_path / f"{name}/model/{name}.DATA"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(deck_text(schedule, table if table is not None else vfp([0, 50000, 100000])))
    return path


def qc(tmp_path: Path, deck: Path, target: str = "ECLIPSE100") -> tuple[int, dict]:
    need("opm", "res2df")
    out = tmp_path / f"qc_{target}"
    proc = run(QC, "--deck", deck, "--target", target, "--out", out)
    report = out / "gas_lift_qc.json"
    data = json.loads(report.read_text()) if report.is_file() else {}
    data["_stderr"] = proc.stderr + proc.stdout
    return proc.returncode, data


def codes(data: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in data["findings"]:
        out.setdefault(f["code"], []).append(f["severity"])
    return out


# ---- QC ------------------------------------------------------------------------------------------------------------
def test_qc_passes_a_grat_alq_axis_and_reports_lift_response(tmp_path):
    code, data = qc(tmp_path, write_deck(tmp_path))
    assert code == 0, data["_stderr"]
    assert data["verdict"] == "PASS" and data["findings"] == []
    r = data["vfp"]["1"]
    assert (
        r["alq_axis"] == [0.0, 50000.0, 100000.0] and r["alq_type"] == "GRAT" and r["alq_unit"] == "SM3/DAY"
    )
    # BHP 200 -> 190 -> 190 (rate 100) and 190 -> 180 -> 180 (rate 500): both points benefit, minimum at 50000
    assert r["points_with_benefit"] == 2 and r["alq_at_min_bhp_median"] == 50000.0
    assert set(data["lifted_wells"]) == {"P1", "P2"}


def test_qc_fails_when_a_lifted_well_has_no_alq_axis(tmp_path):
    code, data = qc(tmp_path, write_deck(tmp_path, table=vfp([0])))
    assert code == 1 and data["verdict"] == "FAIL"
    assert codes(data)["G001"] == ["E", "E"]  # P1 and P2


@pytest.mark.parametrize(("kind", "severity"), [("'IGLR'", "W"), ("1*", "W"), ("'PUMP'", "E")])
def test_qc_checks_the_alq_definition(tmp_path, kind, severity):
    code, data = qc(tmp_path, write_deck(tmp_path, table=vfp([0, 50000, 100000], kind=kind)))
    assert codes(data)["G003"] == [severity]
    assert code == (1 if severity == "E" else 0)


def test_qc_flags_extrapolation_missing_liftopt_unknown_group_and_unknown_well(tmp_path):
    schedule = """WLIFTOPT
 'P1' 'YES' 150000 /
/
WELTARG
 'PX' 'LIFT' 1000 /
/
LIFTOPT
 12500 5E-3 /
GLIFTOPT
 'NOPE' 100000 /
/
"""
    code, data = qc(tmp_path, write_deck(tmp_path, schedule=schedule))
    c = codes(data)
    assert c["G005"] == ["W"] and c["G008"] == ["W"] and "E" in c["G010"], data["_stderr"]
    assert "G007" not in c  # LIFTOPT on the same date as WLIFTOPT YES counts
    assert code == 1


def test_qc_flags_optimization_before_any_liftopt(tmp_path):
    schedule = "WLIFTOPT\n 'P1' 'YES' 80000 /\n/\nDATES\n 15 'JAN' 2020 /\n/\nLIFTOPT\n 12500 5E-3 /\n"
    code, data = qc(tmp_path, write_deck(tmp_path, schedule=schedule))
    assert code == 1 and codes(data)["G007"] == ["E"]


def test_qc_reports_a_deck_the_opm_parser_rejects(tmp_path):
    schedule = "GLIFTOPT\n 'G1' 100000 /\n/\n"  # GLIFTOPT without LIFTOPT
    code, data = qc(tmp_path, write_deck(tmp_path, schedule=schedule))
    assert code == 1 and codes(data) == {"G011": ["E"]} and "LIFTOPT" in data["findings"][0]["message"]


def test_qc_flags_a_table_with_no_lift_benefit(tmp_path):
    rising = [[200, 190], [205, 195], [210, 200]]
    code, data = qc(tmp_path, write_deck(tmp_path, table=vfp([0, 50000, 100000], bhp=rising)))
    assert codes(data)["G009"] == ["W"] and data["vfp"]["1"]["points_with_benefit"] == 0 and code == 0


def test_qc_reports_flow_only_restrictions_by_target(tmp_path):
    schedule = LIFT.replace("'P*' 'YES' 80000 1.0 /", "'P*' 'YES' 80000 1.0 1* 0.5 'YES' /")
    deck = write_deck(tmp_path, schedule=schedule)
    code, data = qc(tmp_path, deck, "ECLIPSE100")
    assert code == 0 and codes(data)["F001"] == ["I"] and "item 6" in data["findings"][0]["message"]
    code, data = qc(tmp_path, deck, "FLOW")
    assert code == 1 and codes(data)["F001"] == ["E"]


def test_qc_bad_input_is_exit_2(tmp_path):
    assert run(QC, "--deck", tmp_path / "nope.DATA", "--target", "ECLIPSE100").returncode == 2


# ---- diagnostics -----------------------------------------------------------------------------------------------------
START = dt.datetime(2020, 1, 1)


def write_summary(base: Path, vectors: dict[str, list[float]], days: list[float]) -> None:
    need("resdata")
    from resdata.summary import Summary

    base.parent.mkdir(parents=True, exist_ok=True)
    writer = Summary.writer(str(base), START, 3, 3, 1)
    for key in vectors:
        fam, _, name = key.partition(":")
        writer.add_variable(fam, wgname=name) if name else writer.add_variable(key)
    for step, day in enumerate(days, start=1):
        t = writer.add_t_step(step, float(day))
        for key, values in vectors.items():
            t[key] = values[step - 1]
    writer.fwrite()


DAYS = [31, 60, 91, 121]  # 2020-02-01 ... 2020-05-01


def diag_case(tmp_path: Path, drop: tuple[str, ...] = (), p2_lift: list[float] | None = None) -> Path:
    deck = write_deck(tmp_path)
    vectors = {
        "FGLIR": [80000, 100000, 120000, 80000],
        "FOPR": [600, 700, 650, 500],
        "GGLIR:G1": [80000, 100000, 120000, 80000],
        "WGLIR:P1": [80000, 80000, 80000, 80000],
        "WGLIR:P2": p2_lift or [0, 20000, 40000, 0],
        "WOPR:P1": [400, 450, 400, 300],
        "WOPR:P2": [200, 250, 0, 200],
    }
    write_summary(deck.with_suffix(""), {k: v for k, v in vectors.items() if k not in drop}, DAYS)
    return deck


def diag(tmp_path: Path, deck: Path) -> tuple[int, dict]:
    out = tmp_path / "diag"
    proc = run(DIAG, "--case", deck, "--out", out)
    report = out / "gas_lift_diag.json"
    data = json.loads(report.read_text()) if report.is_file() else {}
    data["_stderr"] = proc.stderr + proc.stdout
    return proc.returncode, data


def test_diag_well_volumes_limits_and_flags(tmp_path):
    code, data = diag(tmp_path, diag_case(tmp_path))
    assert code == 0, data["_stderr"]
    wells = {w["well"]: w for w in data["wells"]}
    assert set(wells) == {"P1", "P2"}  # I1 has no lift: left out
    dts = [31, 29, 31, 30]
    assert wells["P1"]["lift_gas_volume"] == pytest.approx(80000 * sum(dts))
    assert wells["P1"]["days_at_max"] == pytest.approx(sum(dts)) and "at_well_max" in wells["P1"]["flags"]
    assert "lift_without_oil" in wells["P2"]["flags"] and wells["P2"][
        "days_lift_without_oil"
    ] == pytest.approx(31)
    (group,) = data["groups"]
    # G1 max lift 100000 from START: 120000 in the third interval is over the limit
    assert (
        group["status"] == "violated"
        and group["steps_over"] == 1
        and group["max_ratio"] == pytest.approx(1.2)
    )
    assert data["field"]["well_sum_matches_fglir"] is True


def test_diag_missing_vectors_are_missing_not_zero(tmp_path):
    code, data = diag(tmp_path, diag_case(tmp_path, drop=("FGLIR", "GGLIR:G1", "WOPR:P2")))
    assert code == 0
    assert data["field"]["lift_gas_volume"] is None and data["field"]["lift_gas_volume_status"].startswith(
        "missing"
    )
    p2 = next(w for w in data["wells"] if w["well"] == "P2")
    assert p2["status"] == "missing" and p2["missing"] == ["WOPR:P2"] and "lift_gas_volume" not in p2
    assert data["groups"][0]["status"] == "missing"
    assert any("FGLIR is not in the SUMMARY" in n for n in data["notes"])


def test_diag_flags_an_optimized_well_that_gets_no_lift(tmp_path):
    code, data = diag(tmp_path, diag_case(tmp_path, p2_lift=[0, 0, 0, 0]))
    p2 = next(w for w in data["wells"] if w["well"] == "P2")
    assert code == 0 and "no_lift_while_optimized" in p2["flags"]


def test_diag_without_summary_is_exit_2(tmp_path):
    assert diag(tmp_path, write_deck(tmp_path))[0] == 2


# ---- sweep -------------------------------------------------------------------------------------------------------
def sweep_spec(tmp_path: Path) -> Path:
    write_deck(tmp_path, schedule=LIFT.replace("GLIFTOPT\n 'G1' 100000 /\n/\n", ""))
    spec = tmp_path / "spec.yml"
    spec.write_text(yaml_dump({
        "schema_version": 1, "scenario_id": "lift", "case_root": "CASE", "deck": "model/CASE.DATA",
        "variables": {"g1_lift": {"initial": 100000, "min": 10000, "max": 500000}},
        "operations": [{"id": "g1", "type": "gas_lift", "date": "2020-02-01",
                        "groups": [{"group": "G1", "max_lift": "${g1_lift}"}]}],
    }))  # fmt: skip
    return spec


def test_sweep_build_renders_one_deck_per_level(tmp_path):
    need("opm")
    spec = sweep_spec(tmp_path)
    proc = run(SWEEP, "build", "--spec", spec, "--variable", "g1_lift", "--levels", "50000,100000,200000",
               "--out", tmp_path / "sweep", "--target", "ECLIPSE100")  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((tmp_path / "sweep/sweep.json").read_text())
    assert [lv["label"] for lv in manifest["levels"]] == ["g1_lift_50000", "g1_lift_100000", "g1_lift_200000"]
    for lv, value in zip(manifest["levels"], (50000, 100000, 200000), strict=True):
        text = "".join(p.read_text() for p in Path(lv["deck"]).parent.glob("*SCHEDULE*.inc"))
        assert f"'G1' {value} /" in text and lv["scenario_id"] == f"lift__{lv['label']}"
    out_of_bounds = run(SWEEP, "build", "--spec", spec, "--variable", "g1_lift", "--levels", "1e6",
                        "--out", tmp_path / "sweep2", "--target", "ECLIPSE100")  # fmt: skip
    assert out_of_bounds.returncode == 2 and "outside" in out_of_bounds.stderr
    mixed = run(SWEEP, "build", "--spec", spec, "--variable", "g1_lift", "--levels", "50000,1e6",
                "--out", tmp_path / "sweep3", "--target", "ECLIPSE100")  # fmt: skip
    assert (
        mixed.returncode == 2 and not (tmp_path / "sweep3").exists()
    )  # nothing rendered before the bad level


def test_sweep_collect_marginal_oil_and_missing_level(tmp_path):
    need("opm")
    spec = sweep_spec(tmp_path)
    assert run(SWEEP, "build", "--spec", spec, "--variable", "g1_lift", "--levels", "50000,100000,200000,300000",
               "--out", tmp_path / "sweep", "--target", "ECLIPSE100").returncode == 0  # fmt: skip
    manifest = json.loads((tmp_path / "sweep/sweep.json").read_text())
    # level -> (field lift rate, field oil rate, P1 oil, P2 oil); P2 gains nothing from lift
    levels = {"g1_lift_50000": (50000, 500, 300, 200), "g1_lift_100000": (100000, 600, 400, 200),
              "g1_lift_300000": (300000, 640, 440, 200)}  # 200000 has no SUMMARY  # fmt: skip
    for lv in manifest["levels"]:
        if lv["label"] in levels:
            lift, oil, p1, p2 = levels[lv["label"]]
            write_summary(Path(lv["deck"]).with_suffix(""), {
                "FGLIR": [lift] * 4, "FOPR": [oil] * 4, "WGLIR:P1": [lift / 2] * 4, "WGLIR:P2": [lift / 2] * 4,
                "WOPR:P1": [p1] * 4, "WOPR:P2": [p2] * 4}, DAYS)  # fmt: skip
    proc = run(SWEEP, "collect", "--sweep", tmp_path / "sweep")
    assert proc.returncode == 0, proc.stderr
    data = json.loads((tmp_path / "sweep/sweep_results.json").read_text())
    rows = {r["label"]: r for r in data["levels"]}
    assert rows["g1_lift_200000"]["status"] == "missing"
    assert rows["g1_lift_200000"]["marginal_oil_per_lift_gas"] is None
    assert rows["g1_lift_300000"]["marginal_oil_per_lift_gas"] is None  # its neighbour is missing
    assert rows["g1_lift_100000"]["marginal_oil_per_lift_gas"] == pytest.approx(
        100 / 50000
    )  # rates x same days
    assert rows["g1_lift_100000"]["below_economic_gradient"] is True  # 0.002 < LIFTOPT 5E-3
    assert data["reference"] == "g1_lift_50000" and rows["g1_lift_300000"]["delta_oil"] == pytest.approx(
        140 * 121
    )
    wells = {w["well"]: w["verdict"] for w in data["wells"]}
    assert wells == {"P1": "benefits", "P2": "no_benefit"}
    with (tmp_path / "sweep/sweep_results.csv").open() as h:
        assert [r["status"] for r in csv.DictReader(h)] == ["ok", "ok", "missing", "ok"]


# ---- scenario-build and includes ----------------------------------------------------------------------------------
def test_scenario_build_writes_eclipse_wliftopt_items_6_and_7_and_box_flow_flags_them(tmp_path):
    need("opm")
    import scenario_build as sb

    write_deck(tmp_path, schedule=LIFT)
    spec = tmp_path / "spec.yml"
    spec.write_text(yaml_dump({
        "schema_version": 1, "scenario_id": "items", "case_root": "CASE", "deck": "model/CASE.DATA",
        "operations": [{"id": "w", "type": "gas_lift", "date": "2020-02-01",
                        "wells": [{"well": "P1", "optimize": "YES", "weight": 1.0, "gas_weight": 0.5, "optimize_limit": "YES"}]}],
    }))  # fmt: skip
    report, code = sb.build(sb.load_spec(spec), tmp_path / "out", "ECLIPSE100", "test")
    text = "".join(p.read_text() for p in (tmp_path / "out").rglob("*SCHEDULE*.inc"))
    assert "'P1' 'YES' 1* 1 1* 0.5 'YES' /" in text
    assert code == 0 and report["deck_support"]["verdicts"] == {"box_flow": "FAIL", "target": "PASS"}


def test_fingerprint_includes_follow_paths_aliases(tmp_path):
    import deck_fingerprint as dfp

    (tmp_path / "lib").mkdir()
    (tmp_path / "lib/sched.inc").write_text("WCONPROD\n 'P1' 'OPEN' 'ORAT' 10 /\n/\n")
    data = tmp_path / "model/CASE.DATA"
    data.parent.mkdir()
    data.write_text(
        f"RUNSPEC\nPATHS\n 'LIB' '{tmp_path / 'lib'}' /\n/\nSCHEDULE\nINCLUDE\n '$LIB/sched.inc' /\nEND\n"
    )
    text, sources = dfp.resolve_includes(data)
    assert "WCONPROD" in text and str((tmp_path / "lib/sched.inc").resolve()) in sources


def test_paths_alias_whole_name(tmp_path):
    """$MOD05 resolves to the MOD05 folder even when a shorter alias MOD is defined first."""
    sys.path.insert(0, str(SKILLS / "scenario-qc" / "scripts"))
    import deck_fingerprint

    (tmp_path / "p").mkdir()
    (tmp_path / "q").mkdir()
    (tmp_path / "q" / "x.inc").write_text("-- q\n", encoding="utf-8")
    data = tmp_path / "T.DATA"
    data.write_text("RUNSPEC\nPATHS\n 'MOD' 'p' /\n 'MOD05' 'q' /\n/\n", encoding="utf-8")
    sources: list[str] = []
    deck_fingerprint.expand_includes("INCLUDE\n '$MOD05/x.inc' /\n", tmp_path, data, sources, set())
    assert sources == [str((tmp_path / "q" / "x.inc").resolve())]


def test_sweep_well_marginal_uses_rates_not_wefac_totals(tmp_path):
    """WOPT holds WEFAC and WGLIR does not: the well marginal must come from WOPR x time against WGLIR x time."""
    need("opm")
    spec = sweep_spec(tmp_path)
    assert run(SWEEP, "build", "--spec", spec, "--variable", "g1_lift", "--levels", "50000,100000",
               "--out", tmp_path / "sweep", "--target", "ECLIPSE100").returncode == 0  # fmt: skip
    manifest = json.loads((tmp_path / "sweep/sweep.json").read_text())
    dts = [31, 29, 31, 30]
    for lv, (lift, p1) in zip(manifest["levels"], ((50000, 300), (100000, 400)), strict=True):
        cum = [0.8 * p1 * sum(dts[: i + 1]) for i in range(4)]  # WEFAC 0.8 in the totals only
        write_summary(Path(lv["deck"]).with_suffix(""), {
            "FGLIR": [0.8 * lift] * 4, "FOPR": [0.8 * p1] * 4, "WGLIR:P1": [lift] * 4, "WOPR:P1": [p1] * 4,
            "WOPT:P1": cum}, DAYS)  # fmt: skip
    assert run(SWEEP, "collect", "--sweep", tmp_path / "sweep").returncode == 0
    data = json.loads((tmp_path / "sweep/sweep_results.json").read_text())
    (p1,) = [w for w in data["wells"] if w["well"] == "P1"]
    assert p1["marginal"]["g1_lift_100000"] == pytest.approx(100 / 50000)
    assert data["levels"][1]["marginal_oil_per_lift_gas"] == pytest.approx(100 / 50000)
