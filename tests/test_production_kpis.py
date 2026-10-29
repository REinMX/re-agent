"""production_kpis.py on synthetic SUMMARY files (resdata writer). Behaviour, not internals."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, run, write_contracts, yaml_dump

SCRIPT = SKILLS / "production-kpis/scripts/production_kpis.py"
sys.path.insert(0, str(SCRIPT.parent))
import production_kpis as pk  # noqa: E402

START = dt.datetime(2020, 1, 1)
STEP = 30
# FOPR per 30-day step: max 100, a dip to 85 in step 4, decline from step 8.
FOPR = [50, 100, 100, 95, 85, 100, 100, 92, 60, 40, 30, 20]
N = len(FOPR)


def cum(rates: list[float]) -> list[float]:
    out, total = [], 0.0
    for r in rates:
        total += r * STEP
        out.append(total)
    return out


def base_vectors(scale: float = 1.0) -> dict[str, list[float]]:
    fopr = [r * scale for r in FOPR]
    fwir = [120.0] * N
    fwpr = [10.0 * (i + 1) for i in range(N)]
    return {
        "FOPR": fopr,
        "FOPT": cum(fopr),
        "FWPR": fwpr,
        "FWPT": cum(fwpr),
        "FWIR": fwir,
        "FWIT": cum(fwir),
    }


def write_case(
    base: Path,
    vectors: dict[str, list[float]],
    deck: str | None = None,
    days: list[float] | None = None,
    report_every=None,
) -> Path:
    """<base>.SMSPEC/UNSMRY (one report step per 30 days unless days/report_every say otherwise), <base>.DATA."""
    need("resdata", "numpy", "pandas")
    from resdata.summary import Summary

    base.parent.mkdir(parents=True, exist_ok=True)
    writer = Summary.writer(str(base), START, 2, 2, 1)
    for key in vectors:
        if ":" in key:
            family, name = key.split(":", 1)
            writer.add_variable(family, wgname=name)
        else:
            writer.add_variable(key)
    days = days or [float(STEP * (s + 1)) for s in range(N)]
    report = 0
    for step, day in enumerate(days):
        if report_every is None or report_every(step):
            report += 1
        t = writer.add_t_step(max(report, 1), float(day))
        for key, values in vectors.items():
            t[key] = values[step]
    writer.fwrite()
    base.with_suffix(".DATA").write_text(deck or "-- synthetic\nRUNSPEC\n", encoding="utf-8")
    return base.with_suffix(".DATA")


def kpis(tmp_path: Path, *args: str, settings: dict | None = None, contract: bool = True) -> tuple[int, dict]:
    out = tmp_path / "out"
    extra = []
    if settings is not None:
        (tmp_path / "kpi.yml").write_text(yaml_dump(settings))
        extra = ["--settings", str(tmp_path / "kpi.yml")]
    if contract:
        proj, env = write_contracts(tmp_path / "c")
        extra += ["--contract", str(proj)]
    else:
        env = None
    proc = run(SCRIPT, *args, "--out", str(out), *extra, env=env)
    report = out / "production_kpis.json"
    data = json.loads(report.read_text()) if report.is_file() else {}
    data["_stderr"], data["_stdout"] = proc.stderr, proc.stdout
    return proc.returncode, data


def kpi(data: dict, name: str, label: str = "case") -> dict:
    (row,) = [r for r in data["per_case"] if r["kpi"] == name and str(r["case"]) == label]
    return row


# ---- plateau --------------------------------------------------------------------------------------------
def test_plateau_default_is_90_percent_of_max_bridging_one_month(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file))
    assert code == 0, data["_stderr"]
    row = kpi(data, "plateau_days")
    assert row["value"] == 210 and row["status"] == "ok"
    assert row["extra"] == {"start": "2020-01-31", "end": "2020-08-28", "threshold": 90.0, "reference": 100.0}
    flagged = {a["setting"] for a in data["assumptions"] if a["confirmed"] is False}
    assert {"plateau.fraction", "plateau.reference", "plateau.gap_days"} <= flagged


def test_plateau_without_gap_tolerance_takes_the_first_longest_run(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file), settings={"plateau": {"gap_days": 0}})
    row = kpi(data, "plateau_days")
    assert code == 0 and row["value"] == 90 and row["extra"]["start"] == "2020-01-31"
    assert "plateau.gap_days" not in {a["setting"] for a in data["assumptions"] if a["confirmed"] is False}


def test_plateau_uses_report_step_means_so_a_short_spike_cannot_set_the_maximum(tmp_path):
    # Ten 3-day ministeps in the first 30-day report step: one 3-day spike at 400.
    days = [3.0 * (i + 1) for i in range(10)] + [STEP * (s + 1) for s in range(1, N)]
    fopr = [400.0] + [50.0] * 9 + FOPR[1:]
    data_file = write_case(tmp_path / "c1/CASE", {"FOPR": fopr}, days=days, report_every=lambda i: i >= 9)
    code, data = kpis(tmp_path, "--case", str(data_file))
    assert kpi(data, "plateau_days")["value"] == 210
    code, data = kpis(tmp_path, "--case", str(data_file), settings={"plateau": {"resolution": "step"}})
    assert kpi(data, "plateau_days")["value"] == 3


def test_plateau_against_a_target(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    settings = {"plateau": {"reference": "target", "target": 100, "fraction": 0.95}}
    code, data = kpis(tmp_path, "--case", str(data_file), settings=settings)
    assert code == 0 and kpi(data, "plateau_days")["value"] == 180
    code, data = kpis(tmp_path, "--case", str(data_file), settings={"plateau": {"reference": "target"}})
    row = kpi(data, "plateau_days")
    assert row["status"] == "missing" and row["value"] is None and "plateau.target" in row["note"]


def test_a_missing_vector_is_missing_not_zero(tmp_path):
    vectors = {k: v for k, v in base_vectors().items() if k not in ("FOPR",)}
    data_file = write_case(tmp_path / "c1/CASE", vectors)
    code, data = kpis(tmp_path, "--case", str(data_file))
    row = kpi(data, "plateau_days")
    assert code == 0 and row["status"] == "missing" and row["value"] is None and "FOPR" in row["note"]
    md = (tmp_path / "out/production_kpis.md").read_text()
    assert "plateau_days" in md and "missing" in md
    code, data = kpis(tmp_path, "--case", str(data_file), "--require", "plateau_days")
    assert code == 2 and "plateau_days" in data["_stderr"]


# ---- recovery factor ------------------------------------------------------------------------------------
def test_recovery_factor_from_summary_foip_is_flagged_as_first_step(tmp_path):
    vectors = base_vectors()
    vectors["FOIP"] = [1.0e5 - x for x in vectors["FOPT"]]
    data_file = write_case(tmp_path / "c1/CASE", vectors)
    code, data = kpis(tmp_path, "--case", str(data_file))
    row = kpi(data, "oil_recovery_factor")
    initial = 1.0e5 - FOPR[0] * STEP
    assert code == 0 and row["value"] == pytest.approx(vectors["FOPT"][-1] / initial)
    assert "first SUMMARY step 2020-01-31" in row["basis"]


def test_recovery_factor_uses_region_in_place_initial_and_is_missing_without_in_place(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file))
    row = kpi(data, "oil_recovery_factor")
    assert row["status"] == "missing" and row["value"] is None
    csv = tmp_path / "region_in_place_long.csv"
    csv.write_text(
        "date,source,region_set,region,vector,keyword,value,unit\n"
        "2020-01-01,PRT,FIELD,0,OIP,FOIP,200000,SM3\n"
        "2020-01-31,SUMMARY,FIELD,0,OIP,FOIP,198500,SM3\n"
    )
    code, data = kpis(tmp_path, "--case", str(data_file), "--in-place", str(csv))
    row = kpi(data, "oil_recovery_factor")
    assert row["value"] == pytest.approx(cum(FOPR)[-1] / 2.0e5) and "region-in-place" in row["basis"]
    assert "2020-01-01" in row["basis"] and "PRT" in row["basis"]


# ---- voidage replacement --------------------------------------------------------------------------------
def test_vrr_uses_reservoir_volumes_cumulative_and_per_year(tmp_path):
    vectors = base_vectors()
    vectors["FVPR"] = [200.0] * N
    vectors["FVIR"] = [150.0] * 6 + [250.0] * 6
    data_file = write_case(tmp_path / "c1/CASE", vectors)
    code, data = kpis(tmp_path, "--case", str(data_file))
    row = kpi(data, "vrr_cumulative")
    assert code == 0 and row["value"] == pytest.approx(200 / 200) and row["basis"].startswith("reservoir")
    periods = [p for p in data["periods"] if p["case"] == "case"]
    # 2020 holds steps 1-12 (day 30 ... day 360, all in 2020): one period.
    assert [p["period"] for p in periods] == ["2020"] and periods[0]["vrr"] == pytest.approx(1.0)


def test_recommended_vectors_are_reported_per_case_and_reservoir_vrr_wins(tmp_path):
    vectors = base_vectors()
    vectors["FVPR"] = [200.0] * N
    vectors["FVIR"] = [210.0] * N
    data_file = write_case(tmp_path / "c1/CASE", vectors)
    code, data = kpis(tmp_path, "--case", str(data_file))
    assert code == 0 and kpi(data, "vrr_cumulative")["basis"].startswith("reservoir")
    assert kpi(data, "vrr_cumulative")["value"] == pytest.approx(210 / 200)
    (cov,) = data["vector_coverage"]
    assert "FVPR" in cov["present"] and {"FVPT", "FVIT", "FOIP", "FGSR", "WMCTL"} <= set(cov["absent"])
    assert {"name": "FVPR", "purpose": "vrr_cumulative (reservoir volumes)"} in data["recommended_vectors"]
    md = (tmp_path / "out/production_kpis.md").read_text()
    assert "Recommended SUMMARY vectors not in case: FVPT, FVIT" in md


def test_gas_methods_are_reported_as_methods_not_as_open_assumptions(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file))
    assert code == 0
    topics = {c["topic"] for c in data["gas_methods"]}
    assert {"gas_sales", "gas_sales_target", "gas_recovery_factor"} <= topics
    assert all(set(c) == {"topic", "method"} for c in data["gas_methods"])
    assert "confirmed_methods" not in data
    assert not any(a["setting"].startswith("gas_sales") for a in data["assumptions"])
    md = (tmp_path / "out/production_kpis.md").read_text()
    assert "## Gas methods" in md and "gas sales = FGSR" in md
    assert "confirmed by" not in md.lower()


def test_vrr_without_reservoir_volumes_is_flagged_surface_or_missing(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file))
    row = kpi(data, "vrr_cumulative")
    expected = cum([120.0] * N)[-1] / (cum(FOPR)[-1] + cum([10.0 * (i + 1) for i in range(N)])[-1])
    assert row["value"] == pytest.approx(expected) and "surface" in row["basis"] and "flag" in row["note"]
    vectors = base_vectors()
    vectors["FGPR"] = [1000.0] * N
    vectors["FGPT"] = cum(vectors["FGPR"])
    data_file = write_case(tmp_path / "c2/CASE", vectors)
    code, data = kpis(tmp_path, "--case", str(data_file))
    row = kpi(data, "vrr_cumulative")
    assert row["status"] == "missing" and "FVPR" in row["note"]


# ---- gas sales -------------------------------------------------------------------------------------------
def gas_vectors() -> dict[str, list[float]]:
    vectors = base_vectors()
    vectors["FGPR"] = [1.2e5] * 6 + [0.9e5] * 6
    vectors["FGIR"] = [0.2e5] * N
    vectors["FGSR"] = [g - i for g, i in zip(vectors["FGPR"], vectors["FGIR"], strict=True)]
    return vectors


def test_gas_sales_against_the_target(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", gas_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file), settings={"gas_sales": {"target": 1.0e5}})
    assert code == 0
    assert kpi(data, "gas_sales_mean_rate")["value"] == pytest.approx(0.85e5)
    assert kpi(data, "gas_sales_mean_rate")["basis"] == "FGSR"
    assert kpi(data, "gas_sales_days_below_target")["value"] == 180
    assert kpi(data, "gas_sales_shortfall_volume")["value"] == pytest.approx(0.3e5 * 180)


def test_gas_sales_without_fgsr_needs_a_configured_formula(tmp_path):
    vectors = {k: v for k, v in gas_vectors().items() if k != "FGSR"}
    data_file = write_case(tmp_path / "c1/CASE", vectors)
    code, data = kpis(tmp_path, "--case", str(data_file), settings={"gas_sales": {"target": 1.0e5}})
    assert kpi(data, "gas_sales_mean_rate")["status"] == "missing"
    assert "gas_sales.subtract" in kpi(data, "gas_sales_mean_rate")["note"]
    settings = {"gas_sales": {"target": 1.0e5, "subtract": ["FGIR"]}}
    code, data = kpis(tmp_path, "--case", str(data_file), settings=settings)
    assert kpi(data, "gas_sales_mean_rate")["value"] == pytest.approx(0.85e5)
    assert kpi(data, "gas_sales_mean_rate")["basis"] == "FGPR - FGIR"
    settings = {"gas_sales": {"target": 1.0e5, "subtract": ["FGIR", "FGCR"]}}
    code, data = kpis(tmp_path, "--case", str(data_file), settings=settings)
    assert kpi(data, "gas_sales_mean_rate")["status"] == "missing"
    assert "FGCR" in kpi(data, "gas_sales_mean_rate")["note"]


def test_gas_sales_target_not_configured_leaves_the_comparison_missing(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", gas_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file))
    assert kpi(data, "gas_sales_mean_rate")["status"] == "ok"
    row = kpi(data, "gas_sales_days_below_target")
    assert row["status"] == "missing" and "gas_sales.target" in row["note"]


# ---- binding constraint ----------------------------------------------------------------------------------
def test_binding_constraint_from_control_mode_vectors(tmp_path):
    vectors = base_vectors()
    vectors["WMCTL:P1"] = [1] * 8 + [7] * 4  # ORAT, then BHP
    vectors["WMCTL:I1"] = [-1] * N  # group control
    vectors["GMCTP:G1"] = [1] * N
    vectors["FMCTP"] = [0] * N
    data_file = write_case(tmp_path / "c1/CASE", vectors)
    code, data = kpis(tmp_path, "--case", str(data_file), settings={"period": "report"})
    rows = {(r["entity"], r["period"]): r for r in data["binding"]}
    assert rows[("P1", "2020-01-31")]["binding"] == "ORAT" and rows[("P1", "2020-12-26")]["binding"] == "BHP"
    assert rows[("P1", "2020-01-31")]["method"] == "WMCTL"
    assert rows[("I1", "2020-01-31")]["binding"] == "GRUP"
    assert rows[("G1", "2020-01-31")]["binding"] == "ORAT" and rows[("G1", "2020-01-31")]["method"] == "GMCTP"
    assert rows[("FIELD", "2020-01-31")]["binding"] == "NONE_OR_FLD"
    code, data = kpis(tmp_path, "--case", str(data_file))
    (p1,) = [r for r in data["binding"] if r["entity"] == "P1"]
    assert p1["period"] == "2020" and p1["binding"] == "ORAT" and p1["share"] == pytest.approx(8 / 12)


DECK = """RUNSPEC
DIMENS
 2 2 1 /
OIL
WATER
METRIC
START
 1 'JAN' 2020 /
WELLDIMS
 4 4 2 4 /
TABDIMS
 /
GRID
DX
 4*100 /
DY
 4*100 /
DZ
 4*10 /
TOPS
 4*2000 /
PORO
 4*0.25 /
PERMX
 4*100 /
PERMY
 4*100 /
PERMZ
 4*10 /
PROPS
PVTW
 250 1.0 4.5e-5 0.5 0 /
PVDO
 100 1.10 1.0
 300 1.05 1.2 /
DENSITY
 850 1000 1 /
ROCK
 250 5e-5 /
SWOF
 0.2 0 1 0
 0.8 1 0 0 /
SOLUTION
EQUIL
 2005 250 2100 0 1000 0 /
SCHEDULE
WELSPECS
 'P1' 'G1' 1 1 1* 'OIL' /
 'I1' 'G1' 2 2 1* 'WATER' /
/
COMPDAT
 'P1' 1 1 1 1 'OPEN' /
 'I1' 2 2 1 1 'OPEN' /
/
WCONPROD
 'P1' 'OPEN' 'ORAT' 100 4* 150 /
/
WCONINJE
 'I1' 'WATER' 'OPEN' 'RATE' 120 1* 300 /
/
TSTEP
 4*30 /
WELSPECS
 'P2' 'G1' 1 2 1* 'OIL' /
/
COMPDAT
 'P2' 1 2 1 1 'OPEN' /
/
WCONPROD
 'P2' 'OPEN' 'ORAT' 500 4* 100 /
/
TSTEP
 8*30 /
END
"""


def test_binding_constraint_from_rate_versus_limit_when_no_mode_vectors(tmp_path):
    need("opm")
    vectors = base_vectors()
    vectors["WOPR:P1"] = [80.0, 100.0, 100.0, 99.5] + [70.0] * 8
    vectors["WBHP:P1"] = [180.0, 170.0, 160.0, 155.0] + [150.0] * 8
    vectors["WWIR:I1"] = [120.0] * 6 + [90.0] * 6
    vectors["WBHP:I1"] = [250.0] * 6 + [260.0] * 6
    vectors["WOPR:P2"] = [0.0] * 5 + [30.0] * 7
    vectors["WBHP:P2"] = [0.0] * 5 + [100.0] * 7
    data_file = write_case(tmp_path / "c1/CASE", vectors, deck=DECK)
    code, data = kpis(tmp_path, "--case", str(data_file), settings={"period": "report"})
    assert code == 0, data["_stderr"]
    rows = {(r["entity"], r["period"]): r for r in data["binding"]}
    assert rows[("P1", "2020-01-31")]["binding"] == "UNRESOLVED"  # 80 < 100 and 180 > 150
    assert (
        rows[("P1", "2020-03-01")]["binding"] == "ORAT"
        and rows[("P1", "2020-03-01")]["method"] == "rate-vs-limit"
    )
    assert rows[("P1", "2020-04-30")]["binding"] == "ORAT"  # 99.5 within 1 % of 100
    assert rows[("P1", "2020-12-26")]["binding"] == "BHP"
    assert rows[("I1", "2020-01-31")]["binding"] == "RATE"
    assert rows[("I1", "2020-12-26")]["binding"] == "UNRESOLVED"  # 90 < 120 and 260 < 300
    assert rows[("P2", "2020-01-31")]["binding"] == "NOT_DEFINED"  # WELSPECS at day 150
    assert rows[("P2", "2020-12-26")]["binding"] == "BHP"


# ---- NPV ----------------------------------------------------------------------------------------------------
def test_npv_only_with_given_inputs(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file))
    assert not [r for r in data["per_case"] if r["kpi"] == "npv"]
    assert data["npv"] == {"status": "not configured"}
    settings = {"npv": {"oil_price": 500.0, "water_injection_cost": 10.0, "discount_rate": 0.0,
                        "reference_date": "2020-01-01", "currency": "NOK"}}  # fmt: skip
    code, data = kpis(tmp_path, "--case", str(data_file), settings=settings)
    expected = 500.0 * cum(FOPR)[-1] - 10.0 * cum([120.0] * N)[-1]
    row = kpi(data, "npv")
    assert row["value"] == pytest.approx(expected) and row["unit"] == "NOK"
    assert "gas_price" in row["note"]  # terms not given are listed as left out
    settings["npv"].pop("discount_rate")
    code, data = kpis(tmp_path, "--case", str(data_file), settings=settings)
    assert code == 2 and "discount_rate" in data["_stderr"]


# ---- ensembles ------------------------------------------------------------------------------------------
def make_scratch(scratch: Path, n: int, foip: bool = True) -> None:
    for real in range(n):
        vectors = base_vectors(scale=1.0 + 0.1 * real)
        if foip:
            vectors["FOIP"] = [1.0e5 - x for x in vectors["FOPT"]]
        write_case(scratch / f"realization-{real}/iter-0/eclipse/model/TOY", vectors)


def test_ensemble_stats_are_numerical_percentiles_and_stated(tmp_path):
    make_scratch(tmp_path / "scratch", 5)
    code, data = kpis(tmp_path, "--scratch", str(tmp_path / "scratch"), "--iter", "0", "--expected", "5")
    assert code == 0, data["_stderr"]
    (stats,) = [s for s in data["stats"] if s["kpi"] == "cum_oil"]
    values = [cum([r * (1 + 0.1 * k) for r in FOPR])[-1] for k in range(5)]
    assert stats["n"] == 5 and stats["P10"] < stats["P50"] < stats["P90"]
    assert stats["P50"] == pytest.approx(sorted(values)[2])
    assert "P10 = 10th percentile of the ensemble (low)" in data["p10_p90_note"]
    assert "P10 = 10th percentile of the ensemble (low)" in (tmp_path / "out/production_kpis.md").read_text()
    assert (tmp_path / "out/kpi_stats.csv").is_file() and (tmp_path / "out/kpis_per_case.csv").is_file()


def test_partial_ensemble_fails_closed(tmp_path):
    make_scratch(tmp_path / "scratch", 4)
    code, data = kpis(tmp_path, "--scratch", str(tmp_path / "scratch"), "--iter", "0", "--expected", "5")
    assert code == 2 and "realization" in data["_stderr"].lower() and not data.get("per_case")
    (tmp_path / "scratch/realization-4/iter-0").mkdir(parents=True)
    code, data = kpis(tmp_path, "--scratch", str(tmp_path / "scratch"), "--iter", "0", "--expected", "5")
    assert code == 2 and "4" in data["_stderr"]


def test_stats_are_missing_when_some_realizations_miss_the_kpi(tmp_path):
    make_scratch(tmp_path / "scratch", 3)
    vectors = base_vectors()
    write_case(tmp_path / "scratch/realization-3/iter-0/eclipse/model/TOY", vectors)  # no FOIP
    code, data = kpis(tmp_path, "--scratch", str(tmp_path / "scratch"), "--iter", "0", "--expected", "4")
    (stats,) = [s for s in data["stats"] if s["kpi"] == "oil_recovery_factor"]
    assert stats["status"] == "missing" and stats["P50"] is None and stats["missing_cases"] == ["3"]


# ---- scenarios and profiles --------------------------------------------------------------------------------
def test_scenarios_are_compared_with_the_baseline(tmp_path):
    root = tmp_path / "proj"
    base = write_case(root / "runs/base/CASE", base_vectors())
    scen = write_case(root / "runs/s1/CASE", base_vectors(scale=1.2))
    for sid, deck in (("baseline", base), ("s1", scen)):
        (root / "scenarios" / sid).mkdir(parents=True)
        (root / "scenarios" / sid / "runmeta.yml").write_text(
            yaml_dump({"scenario_id": sid, "deck_data": str(deck)})
        )
    code, data = kpis(
        tmp_path, "--scenarios-dir", str(root / "scenarios"), "--scenarios", "s1", "--baseline", "baseline"
    )
    assert code == 0, data["_stderr"]
    (row,) = [r for r in data["scenario_compare"] if r["kpi"] == "cum_oil" and r["scenario"] == "s1"]
    assert row["rel_delta"] == pytest.approx(0.2) and row["baseline"] == pytest.approx(cum(FOPR)[-1])


def test_a_scenario_without_summary_is_reported_not_zero(tmp_path):
    root = tmp_path / "proj"
    base = write_case(root / "runs/base/CASE", base_vectors())
    (root / "scenarios/baseline").mkdir(parents=True)
    (root / "scenarios/baseline/runmeta.yml").write_text(yaml_dump({"deck_data": str(base)}))
    (root / "scenarios/s2").mkdir(parents=True)
    (root / "scenarios/s2/runmeta.yml").write_text(yaml_dump({"deck_data": str(root / "nowhere/CASE.DATA")}))
    code, data = kpis(
        tmp_path, "--scenarios-dir", str(root / "scenarios"), "--scenarios", "s2", "--baseline", "baseline"
    )
    assert code == 2 and "s2" in data["_stderr"] and "UNSMRY" in data["_stderr"]


def test_ensemble_profiles_output_is_a_source(tmp_path):
    folder = tmp_path / "profiles"
    folder.mkdir()
    lines = ["REAL,DATE,KEY,VALUE"]
    for real in range(2):
        for step, rate in enumerate(FOPR):
            day = (START + dt.timedelta(days=STEP * (step + 1))).date()
            lines.append(f"{real},{day},FOPR,{rate * (1 + real)}")
    (folder / "profiles_long.csv").write_text("\n".join(lines) + "\n")
    (folder / "manifest.json").write_text(
        json.dumps({"missing_realizations": [], "issues": [], "realizations_used": [0, 1]})
    )
    code, data = kpis(tmp_path, "--profiles", str(folder), "--start", "2020-01-01")
    assert code == 0, data["_stderr"]
    assert kpi(data, "plateau_days", "1")["value"] == 210
    assert "resampled" in " ".join(data["notes"])
    (folder / "manifest.json").write_text(json.dumps({"missing_realizations": [3], "issues": []}))
    code, data = kpis(tmp_path, "--profiles", str(folder), "--start", "2020-01-01")
    assert code == 2 and "missing_realizations" in data["_stderr"]
    (folder / "manifest.json").write_text(
        json.dumps({"missing_realizations": [], "realizations_used": [0, 1, 2]})
    )
    code, data = kpis(tmp_path, "--profiles", str(folder), "--start", "2020-01-01")
    assert code == 2 and "realizations_used" in data["_stderr"]
    (folder / "manifest.json").unlink()
    code, data = kpis(tmp_path, "--profiles", str(folder), "--start", "2020-01-01")
    assert code == 2 and "manifest" in data["_stderr"]


def test_pure_plateau_function_on_arrays():
    need("numpy")
    import numpy as np

    days = np.arange(1, N + 1) * STEP
    result = pk.plateau(days, np.array(FOPR, float), fraction=0.9, reference=100.0, gap_days=31)
    assert result == (210.0, 30.0, 240.0)


def test_the_empty_example_contract_block_runs_without_npv(tmp_path):
    example = pk.contract_lib.read_yaml(Path(__file__).resolve().parents[1] / "site.contract.example.yml")
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    code, data = kpis(tmp_path, "--case", str(data_file), settings=example["production_kpis"])
    assert code == 0, data["_stderr"]
    assert data["npv"] == {"status": "not configured"}


def test_bad_settings_yaml_exits_2(tmp_path):
    data_file = write_case(tmp_path / "c1/CASE", base_vectors())
    for name, text in (("list.yml", "- a\n- b\n"), ("broken.yml", "plateau: {fraction: [\n")):
        (tmp_path / name).write_text(text)
        code, data = kpis(tmp_path, "--case", str(data_file), "--settings", str(tmp_path / name))
        assert code == 2, data["_stderr"]
