#!/usr/bin/env python3
"""Production KPIs from SUMMARY: plateau, recovery factor, voidage replacement, gas sales, binding constraint, NPV.

  production_kpis.py --contract site.contract.yml --case <DECK>.DATA
  production_kpis.py --contract site.contract.yml --scratch <scratch> --iter 0 --expected 100
  production_kpis.py --contract site.contract.yml --profiles <deliverables>/profiles --start 2020-01-01
  production_kpis.py --contract site.contract.yml --scenarios s1,s2 --baseline baseline

Sources (pick one): one case; an FMU ensemble (runpath_lib, contract eclbase; a missing or unreadable
realization stops the run: no partial ensembles); ensemble-profiles output (profiles_long, manifest checked);
scenario folders (runmeta `summary_case`, else `deck_data`), compared with the baseline scenario.

KPIs per case (realization or scenario):
  plateau_days        longest run with <vector> >= fraction x reference (max of the run, or a target).
                      Dips no longer than gap_days are bridged. Defaults: FOPR, 0.90, max, 31 days.
  cum_oil, cum_gas, cum_water, cum_water_injected   end values of FOPT, FGPT, FWPT, FWIT.
  oil_recovery_factor, gas_recovery_factor          FOPT / initial oil in place, FGPT / initial gas in place.
                      In place: region-in-place long CSV (--in-place, START row first), else FOIP/FGIP at the
                      first SUMMARY step (flagged). Else missing.
  vrr_cumulative      reservoir injected / reservoir produced (FVIT/FVPT, else FVIR/FVPR integrated). Without
                      them: surface liquid ratio FWIT / (FOPT + FWPT), flagged, only when no gas is produced or
                      injected. Per period in periods.csv.
  gas_sales_*         FGSR, else FGPR minus the vectors listed in gas_sales.subtract. Target from gas_sales.target.
  npv                 only when npv settings are given. Terms not given are left out and listed.
Binding constraint per period per well and group: WMCTL / GMCTP / GMCTW / GMCTG / FMCT* vectors when present
(simulator control mode), else rate-vs-limit: the deck limits (opm.io Schedule) against the simulated rates and
BHP, within binding.rel_tol. Groups need the mode vectors.

Ensemble statistics use the contract p10_p90 convention (numerical: P10 = low). A KPI missing in any case has
missing statistics. Missing vectors are reported missing, never zero.

Settings: contract `production_kpis` block, then --settings YAML (same keys). Defaults that the user has not
confirmed are listed under `assumptions`.
Outputs in --out (default <paths.deliverables>/production_kpis): kpis_per_case.csv, kpi_stats.csv, periods.csv,
binding_constraints.csv, scenario_compare.csv, production_kpis.json, production_kpis.md.
Exit: 0 written; 2 bad input, incomplete ensemble, unreadable SUMMARY, or a --require KPI missing.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
for folder in ("stage-contract", "ensemble-bands"):
    path = str(HERE.parents[1] / folder / "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
import contract_lib  # noqa: E402
import runpath_lib  # noqa: E402
from ensemble_bands import CONVENTION_NOTE, band_percentiles  # noqa: E402

DEFAULTS: dict[str, Any] = {
    "plateau": {
        "vector": "FOPR",
        "fraction": 0.9,
        "reference": "max",
        "target": None,
        "gap_days": 31,
        "resolution": "report",
    },
    "period": "year",
    "gas_sales": {"vector": "FGSR", "subtract": None, "target": None},
    "binding": {"rel_tol": 0.01},
    "npv": None,
}
# Defaults that are engineering choices, not facts. Every report lists them until the user sets them.
UNCONFIRMED = (
    "plateau.vector",
    "plateau.fraction",
    "plateau.reference",
    "plateau.gap_days",
    "plateau.resolution",
    "period",
    "binding.rel_tol",
)  # fmt: skip
# Fixed gas methods. The report lists them under "Gas methods"; they are not settings, so they are not open
# assumptions.
GAS_METHODS = (
    {"topic": "gas_sales", "method": "gas sales = FGSR when present, else FGPR minus the gas_sales.subtract "
     "vectors (fuel, injection), else missing"},
    {"topic": "gas_sales_target", "method": "the gas sales target comes only from settings; without it the "
     "comparison KPIs are missing"},
    {"topic": "gas_recovery_factor", "method": "gas recovery factor = FGPT / initial GIP; FGPT is gross gas"},
)  # fmt: skip
# SUMMARY vectors that this script uses when present. A family matches FVPR and WVPR:<well>.
RECOMMENDED_VECTORS = (
    ("FVPR", "vrr_cumulative (reservoir volumes)"),
    ("FVIR", "vrr_cumulative (reservoir volumes)"),
    ("FVPT", "vrr_cumulative (reservoir volumes, no rate integration)"),
    ("FVIT", "vrr_cumulative (reservoir volumes, no rate integration)"),
    ("WVPR", "optional: per-well reservoir voidage (not used by the KPIs yet)"),
    ("WVIR", "optional: per-well reservoir voidage (not used by the KPIs yet)"),
    ("FOIP", "oil_recovery_factor without --in-place"),
    ("FGIP", "gas_recovery_factor without --in-place"),
    ("FGSR", "gas sales"),
    ("WMCTL", "binding constraint per well (control mode)"),
    ("GMCTP", "binding constraint per group, production"),
    ("GMCTW", "binding constraint per group, water injection"),
    ("GMCTG", "binding constraint per group, gas injection"),
)
PERIODS = ("year", "report", "month")
NPV_TERMS = {  # setting -> (vector, sign)
    "oil_price": ("FOPT", 1.0),
    "gas_price": ("GAS_SALES", 1.0),
    "water_production_cost": ("FWPT", -1.0),
    "water_injection_cost": ("FWIT", -1.0),
    "gas_injection_cost": ("FGIT", -1.0),
}
WELL_MODES = {-10: "UNKNOWN", -1: "GRUP", 0: "SHUT", 1: "ORAT", 2: "WRAT", 3: "GRAT", 4: "LRAT", 5: "RESV",
              6: "THP", 7: "BHP", 9: "CRAT", 12: "DRAWDOWN"}  # fmt: skip
# OPM writes 0 for both NONE and FLD (and SALE for injection), so 0 does not mean "no limit".
GROUP_PROD_MODES = {0: "NONE_OR_FLD", 1: "ORAT", 2: "WRAT", 3: "GRAT", 4: "LRAT", 5: "RESV", 6: "PRBL",
                    9: "CRAT"}  # fmt: skip
GROUP_INJ_MODES = {0: "NONE_OR_FLD", 1: "RATE", 2: "RESV", 3: "REIN", 4: "VREP"}
MODE_SOURCE = (
    "mode codes from OPM opm-common (Well WellCtrlMode, Group ProductionCMode2Int/InjectionCMode2Int); "
    "ECLIPSE100 codes are not checked here"
)


class KpiError(ValueError):
    pass


# ---- data shape ---------------------------------------------------------------------------------------------
@dataclass
class Case:
    """SUMMARY of one case. Rates are averages over (days[i-1], days[i]]; days count from start."""

    label: str
    start: date
    days: np.ndarray
    values: dict[str, np.ndarray]
    units: dict[str, str]
    report_days: np.ndarray
    data_file: Path | None = None
    source: str = "SUMMARY"

    def get(self, key: str) -> np.ndarray | None:
        return self.values.get(key)

    @property
    def dt(self) -> np.ndarray:
        return np.diff(np.concatenate([[0.0], self.days]))

    def day(self, offset: float) -> str:
        return (self.start + timedelta(days=float(offset))).isoformat()

    def integral(self, rate_key: str) -> np.ndarray | None:
        rate = self.get(rate_key)
        return None if rate is None else np.cumsum(rate * self.dt)

    def cumulative(self, total_key: str, rate_key: str | None = None) -> tuple[np.ndarray | None, str]:
        total = self.get(total_key)
        if total is not None:
            return total, total_key
        if rate_key and self.get(rate_key) is not None:
            return self.integral(rate_key), f"{rate_key} integrated"
        return None, ""


@dataclass
class Kpi:
    case: str
    kpi: str
    value: float | None
    unit: str
    status: str  # ok | missing
    basis: str = ""
    note: str = ""
    extra: dict[str, Any] | None = None


def ok(case: Case, name: str, value: float, unit: str, basis: str, note: str = "", **extra: Any) -> Kpi:
    return Kpi(case.label, name, float(value), unit, "ok", basis, note, extra or None)


def missing(case: Case, name: str, unit: str, note: str) -> Kpi:
    return Kpi(case.label, name, None, unit, "missing", "", note)


# ---- reading ------------------------------------------------------------------------------------------------
def read_summary(data_file: Path, label: str) -> Case:
    base = data_file.with_suffix("")
    if not base.with_suffix(".UNSMRY").is_file() or not base.with_suffix(".SMSPEC").is_file():
        raise KpiError(f"{label}: no {base.name}.UNSMRY/.SMSPEC next to {data_file}")
    from resdata.summary import Summary

    try:
        smry = Summary(str(base))
    except (OSError, ValueError, TypeError) as error:
        raise KpiError(f"{label}: cannot read SUMMARY {base}: {error}") from error
    start = smry.start_date
    start = start.date() if isinstance(start, datetime) else start
    days = np.asarray(smry.days, dtype=float)
    keys = [k for k in smry.keys() if k not in ("TIME", "YEARS", "DAY", "MONTH", "YEAR")]
    values = {k: np.asarray(smry.numpy_vector(k, report_only=False), dtype=float) for k in keys}
    units = {k: str(smry.unit(k) or "") for k in keys}
    report = np.array([(d.date() if isinstance(d, datetime) else d) for d in smry.report_dates])
    report_days = np.array([(d - start).days for d in report], dtype=float)
    return Case(label, start, days, values, units, report_days, data_file, "SUMMARY")


def read_profiles(folder: Path, start: date | None) -> tuple[list[Case], list[str]]:
    import pandas as pd

    manifest = folder / "manifest.json"
    if not manifest.is_file():
        raise KpiError(f"no {manifest}: cannot check that the profiles are a complete ensemble")
    meta = json.loads(manifest.read_text())
    for key in ("missing_realizations", "issues"):
        if meta.get(key):
            raise KpiError(f"{manifest}: {key} {meta[key]}: the profiles are a partial ensemble")
    for name in ("profiles_long.parquet", "profiles_long.csv"):
        if (folder / name).is_file():
            df = pd.read_parquet(folder / name) if name.endswith("parquet") else pd.read_csv(folder / name)
            break
    else:
        raise KpiError(f"no profiles_long.parquet or profiles_long.csv in {folder}")
    used = meta.get("realizations_used")
    if used is not None and sorted(int(r) for r in df["REAL"].unique()) != sorted(int(r) for r in used):
        raise KpiError(f"{folder}: REAL values in profiles_long differ from manifest realizations_used")
    df["DATE"] = pd.to_datetime(df["DATE"]).dt.date
    first = min(df["DATE"])
    start = start or first
    if first < start:
        raise KpiError(f"--start {start} is after the first profile date {first}")
    cases = []
    for real, part in df.groupby("REAL"):
        wide = part.pivot_table(index="DATE", columns="KEY", values="VALUE", aggfunc="first").sort_index()
        days = np.array([(d - start).days for d in wide.index], dtype=float)
        values = {k: wide[k].to_numpy(dtype=float) for k in wide.columns if wide[k].notna().all()}
        cases.append(Case(str(real), start, days, values, {}, days, None, f"profiles {folder.name}"))
    notes = [
        f"source: ensemble-profiles {folder}; rates are resampled by res2df to the profile dates, so plateau and "
        "per-period values follow that time grid; units not in profiles_long",
    ]
    return cases, notes


def read_ensemble(scratch: Path, iteration: str, eclbase: tuple[str, str], expected: set[int]) -> list[Case]:
    ensemble = runpath_lib.read_ensemble(
        str(scratch / "realization-*" / iteration), eclbase, expected=expected
    )
    try:
        ensemble.require_complete()
    except runpath_lib.IncompleteEnsemble as error:
        raise KpiError(f"incomplete ensemble (no partial ensembles):\n{error}") from error
    if not ensemble.found:
        raise KpiError(f"no runpaths in {scratch}/realization-*/{iteration}")
    return [read_summary(m.data_file, str(m.realization)) for m in ensemble.found if m.data_file]


def read_in_place(path: Path) -> dict[str, dict[str, tuple[float, str]]]:
    """region-in-place long CSV -> {case label: {OIP|GIP: (value, basis)}} from the FIELD rows, earliest date."""
    import pandas as pd

    df = pd.read_csv(path)
    need = {"date", "region_set", "vector", "value"}
    if not need <= set(df.columns):
        raise KpiError(f"{path}: needs columns {sorted(need)} (region_in_place_long.csv)")
    df = df[(df.region_set == "FIELD") & df.vector.isin(["OIP", "GIP"])]
    label_col = "realization" if "realization" in df.columns else None
    out: dict[str, dict[str, tuple[float, str]]] = {}
    for keys, part in df.groupby([label_col, "vector"] if label_col else ["vector"]):
        keys = keys if isinstance(keys, tuple) else (keys,)
        label, vector = (str(keys[0]), keys[1]) if label_col else ("*", keys[0])
        row = part.sort_values("date").iloc[0]
        source = row.get("source", "")
        out.setdefault(label, {})[vector] = (float(row.value), f"region-in-place {row.date} {source}".strip())
    return out


# ---- KPI functions ------------------------------------------------------------------------------------------
def plateau(
    days: np.ndarray, rate: np.ndarray, fraction: float, reference: float, gap_days: float
) -> tuple[float, float, float] | None:
    """(length, start day, end day) of the longest run of steps with rate >= fraction x reference.
    Runs of failing steps no longer than gap_days are bridged. Ties go to the first run."""
    threshold = fraction * reference
    starts = np.concatenate([[0.0], days[:-1]])
    passing = rate >= threshold - 1e-9 * abs(threshold)
    runs: list[list[int]] = []
    current: list[int] = []
    gap: list[int] = []
    for i, flag in enumerate(passing):
        if flag:
            if current and gap and sum(days[j] - starts[j] for j in gap) <= gap_days:
                current.extend(gap)
            elif current and gap:
                runs.append(current)
                current = []
            current.append(i)
            gap = []
        elif current:
            gap.append(i)
    if current:
        runs.append(current)
    if not runs:
        return None
    best = max(runs, key=lambda r: (days[r[-1]] - starts[r[0]], -r[0]))
    return float(days[best[-1]] - starts[best[0]]), float(starts[best[0]]), float(days[best[-1]])


def report_average(case: Case, rate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Time-weighted mean rate per report step, so a short simulator time step cannot set the maximum."""
    bounds = period_bounds(case, "report")
    index = step_period(case, bounds)
    dt = case.dt
    days, means = [], []
    for k, (_, _, end) in enumerate(bounds):
        mask = index == k
        weight = float(dt[mask].sum())
        if mask.any() and weight > 0:
            days.append(end)
            means.append(float((rate[mask] * dt[mask]).sum() / weight))
    return np.array(days, dtype=float), np.array(means, dtype=float)


def plateau_kpi(case: Case, cfg: dict[str, Any]) -> Kpi:
    vector = str(cfg["vector"]).upper()
    rate = case.get(vector)
    if rate is None:
        return missing(case, "plateau_days", "days", f"{vector} is not in the SUMMARY")
    days = case.days
    if cfg["resolution"] == "report":
        days, rate = report_average(case, rate)
    if cfg["reference"] == "target":
        if cfg.get("target") in (None, ""):
            return missing(
                case, "plateau_days", "days", "plateau.reference is target but plateau.target is not set"
            )
        reference = float(cfg["target"])
    else:
        reference = float(np.max(rate)) if rate.size else 0.0
    if reference <= 0:
        return missing(case, "plateau_days", "days", f"{vector} reference is {reference}: no production")
    result = plateau(days, rate, float(cfg["fraction"]), reference, float(cfg["gap_days"]))
    basis = (
        f"{vector} ({cfg['resolution']}-step mean) >= {cfg['fraction']} x {cfg['reference']} "
        f"({reference:g}), gaps <= {cfg['gap_days']} d"
    )
    if result is None:
        return ok(case, "plateau_days", 0.0, "days", basis, "no step reaches the threshold")
    length, first, last = result
    return ok(case, "plateau_days", length, "days", basis, start=case.day(first), end=case.day(last),
              threshold=round(float(cfg["fraction"]) * reference, 6), reference=reference)  # fmt: skip


def end_value_kpis(case: Case) -> list[Kpi]:
    out = []
    for name, total, rate in (("cum_oil", "FOPT", "FOPR"), ("cum_gas", "FGPT", "FGPR"),
                              ("cum_water", "FWPT", "FWPR"), ("cum_water_injected", "FWIT", "FWIR")):  # fmt: skip
        series, basis = case.cumulative(total, rate)
        unit = case.units.get(total, case.units.get(rate, ""))
        if series is None:
            out.append(missing(case, name, unit, f"{total} and {rate} are not in the SUMMARY"))
        else:
            out.append(ok(case, name, series[-1], unit, basis))
    return out


def recovery_kpis(case: Case, in_place: dict[str, dict[str, tuple[float, str]]]) -> list[Kpi]:
    out = []
    for name, fam, field_key, total, rate in (("oil_recovery_factor", "OIP", "FOIP", "FOPT", "FOPR"),
                                              ("gas_recovery_factor", "GIP", "FGIP", "FGPT", "FGPR")):  # fmt: skip
        produced, produced_basis = case.cumulative(total, rate)
        if produced is None:
            out.append(missing(case, name, "fraction", f"{total} and {rate} are not in the SUMMARY"))
            continue
        given = in_place.get(case.label, in_place.get("*", {})).get(fam)
        if given:
            initial, basis = given
        elif case.get(field_key) is not None:
            initial = float(case.get(field_key)[0])
            basis = f"{field_key} at the first SUMMARY step {case.day(case.days[0])}, not at START"
            if case.days[0] == 0:
                basis = f"{field_key} at START"
        else:
            note = f"no {field_key} in the SUMMARY and no region-in-place initial {fam} (--in-place)"
            out.append(missing(case, name, "fraction", note))
            continue
        if initial <= 0:
            out.append(missing(case, name, "fraction", f"initial {fam} is {initial}"))
            continue
        note = "FGPT is gross gas (includes re-produced injected gas)" if fam == "GIP" else ""
        out.append(ok(case, name, produced[-1] / initial, "fraction", f"{produced_basis} / {basis}", note,
                      initial_in_place=initial))  # fmt: skip
    return out


def gas_sales_rate(case: Case, cfg: dict[str, Any]) -> tuple[np.ndarray | None, str, str]:
    vector = str(cfg.get("vector") or "FGSR").upper()
    if case.get(vector) is not None:
        return case.get(vector), vector, ""
    subtract = cfg.get("subtract")
    if not subtract:
        return (
            None,
            "",
            f"no {vector}; set gas_sales.subtract (for example [FGIR, FGCR]) to use FGPR minus them",
        )
    names = [str(s).upper() for s in subtract]
    absent = [k for k in ["FGPR", *names] if case.get(k) is None]
    if absent:
        return None, "", f"gas sales = FGPR - {' - '.join(names)}, but {absent} are not in the SUMMARY"
    rate = case.get("FGPR") - sum(case.get(k) for k in names)
    return rate, "FGPR - " + " - ".join(names), ""


def gas_sales_kpis(case: Case, cfg: dict[str, Any]) -> list[Kpi]:
    rate, basis, why = gas_sales_rate(case, cfg)
    unit = case.units.get("FGPR", case.units.get("FGSR", ""))
    names = ("gas_sales_mean_rate", "gas_sales_days_below_target", "gas_sales_shortfall_volume")
    if rate is None:
        return [missing(case, n, unit, why) for n in names]
    dt = case.dt
    total_days = float(dt.sum())
    out = [ok(case, names[0], float((rate * dt).sum() / total_days) if total_days else 0.0, unit, basis)]
    target = cfg.get("target")
    if target in (None, ""):
        return out + [missing(case, n, unit, "gas_sales.target is not set") for n in names[1:]]
    target = float(target)
    below = rate < target - 1e-9 * abs(target)
    out.append(ok(case, names[1], float(dt[below].sum()), "days", f"{basis} < {target:g}"))
    out.append(ok(case, names[2], float(((target - rate) * dt)[below].sum()), unit.split("/")[0],
                  f"sum of (target - {basis}) x days where below"))  # fmt: skip
    return out


def vrr_series(case: Case) -> tuple[np.ndarray | None, np.ndarray | None, str, str]:
    """(injected, produced) cumulative volumes, basis, note."""
    inj, inj_basis = case.cumulative("FVIT", "FVIR")
    prod, prod_basis = case.cumulative("FVPT", "FVPR")
    if inj is not None and prod is not None:
        return inj, prod, f"reservoir volumes ({inj_basis} / {prod_basis})", ""
    gas = [
        k for k in ("FGPT", "FGPR", "FGIT", "FGIR") if case.get(k) is not None and np.any(case.get(k) != 0)
    ]
    oil, _ = case.cumulative("FOPT", "FOPR")
    water, _ = case.cumulative("FWPT", "FWPR")
    winj, _ = case.cumulative("FWIT", "FWIR")
    if gas or oil is None or water is None or winj is None:
        why = f"gas is produced or injected ({gas})" if gas else "FOPT, FWPT or FWIT missing"
        return (
            None,
            None,
            "",
            f"no FVPR/FVIR (or FVPT/FVIT) in the SUMMARY and {why}: VRR needs reservoir volumes",
        )
    note = ("flag: surface liquid volumes FWIT / (FOPT + FWPT) without formation volume factors. Add FVPR/FVIR "
            "(or FVPT/FVIT) to SUMMARY for a reservoir-volume VRR")  # fmt: skip
    return winj, oil + water, "surface liquid (approximation)", note


def vrr_kpi(case: Case) -> tuple[Kpi, np.ndarray | None, np.ndarray | None]:
    inj, prod, basis, note = vrr_series(case)
    if inj is None:
        return missing(case, "vrr_cumulative", "fraction", note), None, None
    if prod[-1] <= 0:
        return missing(case, "vrr_cumulative", "fraction", "nothing produced"), None, None
    return ok(case, "vrr_cumulative", inj[-1] / prod[-1], "fraction", basis, note), inj, prod


def period_bounds(case: Case, kind: str) -> list[tuple[str, float, float]]:
    """(label, start day, end day) for each period; a step belongs to the period that holds its end."""
    if kind == "report":
        edges = [0.0, *[d for d in case.report_days if d > 0]]
        if not edges or edges[-1] < case.days[-1]:
            edges.append(float(case.days[-1]))
        return [(case.day(b), a, b) for a, b in zip(edges[:-1], edges[1:], strict=True)]
    out, first = [], case.start
    end = case.start + timedelta(days=float(case.days[-1]))
    cursor = date(first.year, first.month if kind == "month" else 1, 1)
    while cursor < end:
        nxt = date(cursor.year + 1, 1, 1) if kind == "year" else (
            date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1))  # fmt: skip
        label = str(cursor.year) if kind == "year" else f"{cursor.year}-{cursor.month:02d}"
        a, b = max(0.0, float((cursor - first).days)), float((nxt - first).days)
        out.append((label, a, b))
        cursor = nxt
    return out


def step_period(case: Case, bounds: list[tuple[str, float, float]]) -> np.ndarray:
    index = np.full(case.days.size, -1)
    for k, (_, a, b) in enumerate(bounds):
        index[(case.days > a) & (case.days <= b)] = k
    if case.days.size and case.days[0] == 0 and bounds:
        index[0] = 0
    return index


def period_rows(
    case: Case, cfg: dict[str, Any], inj: np.ndarray | None, prod: np.ndarray | None
) -> list[dict]:
    bounds = period_bounds(case, cfg["period"])
    index = step_period(case, bounds)
    sales, _, _ = gas_sales_rate(case, cfg["gas_sales"])
    target = cfg["gas_sales"].get("target")
    dt = case.dt
    inj_step = None if inj is None else np.diff(np.concatenate([[0.0], inj]))
    prod_step = None if prod is None else np.diff(np.concatenate([[0.0], prod]))
    rows = []
    for k, (label, _, _) in enumerate(bounds):
        mask = index == k
        if not mask.any():
            continue
        days = float(dt[mask].sum())
        row: dict[str, Any] = {"case": case.label, "period": label, "days": days}
        if inj_step is not None and prod_step is not None:
            p = float(prod_step[mask].sum())
            row["vrr"] = float(inj_step[mask].sum()) / p if p > 0 else None
        else:
            row["vrr"] = None
        row["gas_sales_mean_rate"] = (
            float((sales[mask] * dt[mask]).sum() / days) if sales is not None and days else None
        )
        row["gas_sales_target"] = float(target) if target not in (None, "") else None
        rows.append(row)
    return rows


# ---- binding constraint -------------------------------------------------------------------------------------
def dominant(labels: list[str], weights: np.ndarray) -> tuple[str, float, dict[str, float]]:
    share: dict[str, float] = {}
    total = float(weights.sum())
    for label, w in zip(labels, weights, strict=True):
        share[label] = share.get(label, 0.0) + float(w)
    if total <= 0:
        return labels[-1], 1.0, {labels[-1]: 1.0}
    share = {k: v / total for k, v in share.items()}
    best = max(share, key=lambda k: (share[k], -list(share).index(k)))
    return best, share[best], share


def entities(case: Case, family: str) -> list[str]:
    return sorted({k.split(":", 1)[1] for k in case.values if k.startswith(family + ":")})


def mode_rows(case: Case, bounds, index) -> list[dict]:
    rows = []
    specs = [("WMCTL", "well", WELL_MODES)]
    specs += [
        (f, "group", GROUP_PROD_MODES if f == "GMCTP" else GROUP_INJ_MODES)
        for f in ("GMCTP", "GMCTW", "GMCTG")
    ]
    series: list[tuple[str, str, str, np.ndarray, dict]] = []
    for family, kind, codes in specs:
        for name in entities(case, family):
            series.append((name, kind, family, case.get(f"{family}:{name}"), codes))
    for family, codes in (
        ("FMCTP", GROUP_PROD_MODES),
        ("FMCTW", GROUP_INJ_MODES),
        ("FMCTG", GROUP_INJ_MODES),
    ):
        if case.get(family) is not None:
            series.append(("FIELD", "group", family, case.get(family), codes))
    for name, kind, family, values, codes in series:
        labels = [codes.get(int(round(v)), f"CODE_{int(round(v))}") for v in values]
        rows += binned(case, bounds, index, name, kind, family, labels)
    return rows


def binned(case, bounds, index, name, kind, method, labels) -> list[dict]:
    rows = []
    for k, (label, _, _) in enumerate(bounds):
        mask = index == k
        if not mask.any():
            continue
        picked = [labels[i] for i in np.flatnonzero(mask)]
        best, share, shares = dominant(picked, case.dt[mask])
        rows.append({"case": case.label, "entity": name, "kind": kind, "period": label, "binding": best,
                     "share": round(share, 6), "shares": {k2: round(v, 6) for k2, v in shares.items()},
                     "method": method})  # fmt: skip
    return rows


def deck_limits(data_file: Path):
    """opm.io Schedule of the deck, or None with the reason."""
    try:
        import opm.io
        from opm.io.ecl_state import EclipseState
        from opm.io.parser import ParseContext, Parser
        from opm.io.schedule import Schedule
    except ImportError:
        return None, "opm.io is not installed"
    try:
        context = ParseContext([("PARSE_RANDOM_SLASH", opm.io.action.ignore),
                                ("PARSE_MISSING_DIMS_KEYWORD", opm.io.action.ignore)])  # fmt: skip
        deck = Parser().parse(str(data_file), context)
        schedule = Schedule(deck, EclipseState(deck))
    except Exception as error:  # noqa: BLE001 (opm raises RuntimeError and others)
        return None, f"opm.io cannot build the schedule: {type(error).__name__}: {error}"
    return schedule, ""


PRODUCER_CHECKS = (("ORAT", "oil_rate", "WOPR"), ("WRAT", "water_rate", "WWPR"), ("GRAT", "gas_rate", "WGPR"),
                   ("LRAT", "liquid_rate", "WLPR"), ("RESV", "resv_rate", "WVPR"), ("BHP", "bhp_target", "WBHP"),
                   ("THP", "thp_target", "WTHP"))  # fmt: skip
INJECTOR_CHECKS = (("RATE", "surf_inj_rate", None), ("RESV", "resv_inj_rate", "WVIR"),
                   ("BHP", "bhp_target", "WBHP"), ("THP", "thp_target", "WTHP"))  # fmt: skip


def actual(case: Case, vector: str, well: str, i: int) -> float | None:
    if vector == "WLPR":
        o, w = case.get(f"WOPR:{well}"), case.get(f"WWPR:{well}")
        return None if o is None or w is None else float(o[i] + w[i])
    v = case.get(f"{vector}:{well}")
    return None if v is None else float(v[i])


def limit_rows(case: Case, bounds, index, rel_tol: float) -> tuple[list[dict], str]:
    if case.data_file is None:
        return [], "rate-vs-limit needs the deck (not available for profile sources)"
    schedule, why = deck_limits(case.data_file)
    if schedule is None:
        return [], why
    step_days = np.array([(d.date() - case.start).days for d in schedule.reportsteps], dtype=float)
    rows = []
    for well in schedule.well_names("*"):
        labels = []
        for i, t in enumerate(case.days):
            step = int(max(0, np.searchsorted(step_days, t, side="left") - 1))
            labels.append(classify(case, schedule, well, step, i, rel_tol))
        rows += binned(case, bounds, index, well, "well", "rate-vs-limit", labels)
    return rows, ""


def classify(case: Case, schedule, well: str, step: int, i: int, rel_tol: float) -> str:
    try:
        w = schedule.get_well(well, step)
    except (IndexError, KeyError, RuntimeError):
        return "NOT_DEFINED"  # the deck defines the well at a later report step
    if str(w.status()) not in ("OPEN", "Status.OPEN"):
        return "SHUT"
    if w.isinjector():
        limits = schedule.get_injection_properties(well, step)
        phase = str(w.preferred_phase).upper()
        checks = [(n, k, v or ("WGIR" if "GAS" in phase else "WWIR")) for n, k, v in INJECTOR_CHECKS]
    else:
        limits = schedule.get_production_properties(well, step)
        checks = list(PRODUCER_CHECKS)
    hits, unknown = [], []
    for name, key, vector in checks:
        limit = float(limits.get(key, 0.0) or 0.0)
        if limit <= 0:
            continue  # 0 = not set in the deck
        value = actual(case, vector, well, i)
        if value is None:
            unknown.append(vector)
            continue
        if abs(value - limit) <= rel_tol * abs(limit):
            hits.append((abs(value - limit) / abs(limit), name))
    if hits:
        return min(hits)[1]
    rates = [actual(case, v, well, i) for v in ("WOPR", "WWPR", "WGPR", "WWIR", "WGIR")]
    if all(r in (None, 0.0) for r in rates) and any(r is not None for r in rates):
        return "NO_FLOW"
    return "UNRESOLVED" if not unknown else "UNRESOLVED_MISSING_" + "_".join(unknown)


def binding_rows(case: Case, cfg: dict[str, Any]) -> tuple[list[dict], list[str]]:
    bounds = period_bounds(case, cfg["period"])
    index = step_period(case, bounds)
    rows = mode_rows(case, bounds, index)
    notes = []
    have = {r["entity"] for r in rows if r["kind"] == "well"}
    extra, why = limit_rows(case, bounds, index, float(cfg["binding"]["rel_tol"]))
    rows += [r for r in extra if r["entity"] not in have]
    if why and not have:
        notes.append(f"case {case.label}: no WMCTL vectors and {why}: well binding constraints missing")
    if not any(r["kind"] == "group" for r in rows):
        notes.append(
            f"case {case.label}: no GMCTP/GMCTW/GMCTG/FMCT* vectors: group binding constraints missing"
        )
    return rows, notes


# ---- NPV ---------------------------------------------------------------------------------------------------
def check_npv(cfg: dict[str, Any] | None) -> None:
    if not cfg:
        return
    for key in ("discount_rate", "reference_date"):
        if cfg.get(key) in (None, ""):
            raise KpiError(f"npv.{key} is required when npv is set (no default)")
    if not any(cfg.get(k) not in (None, "") for k in NPV_TERMS):
        raise KpiError(f"npv needs at least one of {sorted(NPV_TERMS)}")
    unknown = sorted(set(cfg) - {*NPV_TERMS, "discount_rate", "reference_date", "currency"})
    if unknown:
        raise KpiError(f"npv: unknown keys {unknown}")


def npv_kpi(case: Case, cfg: dict[str, Any], gas_cfg: dict[str, Any]) -> Kpi:
    ref = date.fromisoformat(str(cfg["reference_date"]))
    rate = float(cfg["discount_rate"])
    years = np.array([(case.start + timedelta(days=float(d)) - ref).days for d in case.days]) / 365.25
    factor = (1.0 + rate) ** (-years)
    total, used, left_out = 0.0, [], []
    for key, (vector, sign) in NPV_TERMS.items():
        price = cfg.get(key)
        if price in (None, ""):
            left_out.append(key)
            continue
        if vector == "GAS_SALES":
            sales, _, why = gas_sales_rate(case, gas_cfg)
            if sales is None:
                return missing(
                    case, "npv", str(cfg.get("currency", "")), f"gas_price given but gas sales: {why}"
                )
            step = sales * case.dt
        else:
            series, _ = case.cumulative(vector, vector[:-1] + "R")
            if series is None:
                return missing(
                    case, "npv", str(cfg.get("currency", "")), f"{key} given but {vector} is missing"
                )
            step = np.diff(np.concatenate([[0.0], series]))
        total += sign * float(price) * float((step * factor).sum())
        used.append(key)
    basis = f"sum over SUMMARY steps of terms x (1 + {rate})^-(years from {ref}) at step end"
    note = f"left out (not given): {left_out}" if left_out else ""
    return ok(case, "npv", total, str(cfg.get("currency", "")), basis, note, terms=used)


# ---- settings and statistics --------------------------------------------------------------------------------
def merge(base: dict, over: dict | None, where: str) -> dict:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if key not in base:
            raise KpiError(f"{where}: unknown key {key!r}; allowed {sorted(base)}")
        if isinstance(base[key], dict) and isinstance(value, dict):
            out[key] = merge(base[key], value, f"{where}.{key}")
        elif value is not None and value != "":
            out[key] = value
    return out


def settings_from(contract: Any, settings_file: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    block = contract.get("production_kpis") if contract is not None else None
    layered = merge(DEFAULTS, block if isinstance(block, dict) else None, "contract production_kpis")
    given = flatten(block if isinstance(block, dict) else {})
    if settings_file:
        data = contract_lib.read_yaml(settings_file)
        layered = merge(layered, data, settings_file.name)
        given |= flatten(data)
    npv = layered["npv"]
    if isinstance(npv, dict) and all(v in (None, "") for v in npv.values()):
        layered["npv"] = None  # the empty block in site.contract.example.yml: NPV not configured
    if layered["period"] not in PERIODS:
        raise KpiError(f"period must be one of {PERIODS}")
    if layered["plateau"]["resolution"] not in ("report", "step"):
        raise KpiError("plateau.resolution must be report or step")
    if layered["plateau"]["reference"] not in ("max", "target"):
        raise KpiError("plateau.reference must be max or target")
    if not 0 < float(layered["plateau"]["fraction"]) <= 1:
        raise KpiError("plateau.fraction must be in (0, 1]")
    check_npv(layered["npv"])
    assumptions = [
        {"setting": key, "value": lookup(layered, key), "confirmed": key in given} for key in UNCONFIRMED
    ]
    return layered, assumptions


def vector_coverage(case: Case) -> dict[str, Any]:
    keys = set(case.values)
    present = [n for n, _ in RECOMMENDED_VECTORS if n in keys or any(k.startswith(n + ":") for k in keys)]
    absent = [n for n, _ in RECOMMENDED_VECTORS if n not in present]
    return {"case": case.label, "present": present, "absent": absent}


def flatten(data: dict, prefix: str = "") -> set[str]:
    keys = set()
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            keys |= flatten(value, name + ".")
        elif value not in (None, ""):
            keys.add(name)
    return keys


def lookup(data: dict, dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        node = node[part]
    return node


def stats_rows(per_case: list[Kpi], convention: str) -> list[dict]:
    rows = []
    for name in dict.fromkeys(k.kpi for k in per_case):
        group = [k for k in per_case if k.kpi == name]
        absent = [k.case for k in group if k.status != "ok"]
        row: dict[str, Any] = {"kpi": name, "unit": group[0].unit, "n": len(group), "convention": convention}
        if absent:
            row |= {"status": "missing", "P10": None, "P50": None, "P90": None, "mean": None,
                    "missing_cases": absent}  # fmt: skip
        else:
            values = np.array([k.value for k in group], dtype=float)
            row |= {"status": "ok", **band_percentiles(values, convention), "mean": float(values.mean()),
                    "missing_cases": []}  # fmt: skip
        rows.append(row)
    return rows


def compare_rows(per_case: list[Kpi], baseline: str) -> list[dict]:
    base = {k.kpi: k for k in per_case if k.case == baseline}
    rows = []
    for k in per_case:
        if k.case == baseline:
            continue
        b = base.get(k.kpi)
        both = b is not None and b.status == "ok" and k.status == "ok"
        delta = k.value - b.value if both else None
        rel = delta / b.value if both and b.value else None
        rows.append({"scenario": k.case, "kpi": k.kpi, "unit": k.unit, "value": k.value,
                     "baseline": b.value if b else None, "delta": delta, "rel_delta": rel,
                     "status": "ok" if both else "missing"})  # fmt: skip
    return rows


# ---- sources ------------------------------------------------------------------------------------------------
def scenario_cases(folder: Path, ids: list[str], baseline: str) -> list[Case]:
    cases = []
    for sid in [baseline, *ids]:
        meta_path = folder / sid / "runmeta.yml"
        if not meta_path.is_file():
            raise KpiError(f"scenario {sid}: no {meta_path}")
        meta = contract_lib.read_yaml(meta_path)
        deck = meta.get("summary_case") or meta.get("deck_data")
        if not deck:
            raise KpiError(f"scenario {sid}: runmeta has no summary_case or deck_data")
        deck_path = Path(str(deck))
        deck_path = deck_path if deck_path.is_absolute() else (folder / sid / deck_path)
        cases.append(read_summary(deck_path, sid))
    return cases


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    contract_lib.add_contract_args(parser)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--case", type=Path, help="one case: <DECK>.DATA (SMSPEC/UNSMRY next to it)")
    src.add_argument("--scratch", type=Path, help="FMU scratch: realization-*/<iter>/<eclbase>")
    src.add_argument("--profiles", type=Path, help="ensemble-profiles output folder (profiles_long)")
    src.add_argument("--scenarios", help="scenario ids, comma separated (compared with --baseline)")
    parser.add_argument("--iter", default=None, help="iteration: 0, iter-3, pred_ref (default contract)")
    parser.add_argument(
        "--expected", type=int, help="realizations expected (default contract expected_realizations)"
    )
    parser.add_argument(
        "--start", type=date.fromisoformat, help="START date for --profiles (default first date)"
    )
    parser.add_argument("--baseline", help="baseline scenario id (default contract scenario_qc.baseline)")
    parser.add_argument("--scenarios-dir", type=Path, help="default contract paths.scenarios")
    parser.add_argument("--in-place", type=Path, help="region_in_place_long.csv from region-in-place")
    parser.add_argument(
        "--settings", type=Path, help="YAML with production_kpis keys (overrides the contract)"
    )
    parser.add_argument(
        "--require", default="", help="KPIs that must be present, comma separated (else exit 2)"
    )
    parser.add_argument("--out", type=Path, help="default <paths.deliverables>/production_kpis")
    return parser.parse_args(argv)


def load_cases(args: argparse.Namespace, contract: Any) -> tuple[list[Case], str | None, list[str]]:
    if args.case:
        return [read_summary(args.case, "case")], None, []
    if args.profiles:
        cases, notes = read_profiles(args.profiles, args.start)
        return cases, None, notes
    if args.scratch:
        iteration = args.iter if args.iter is not None else contract.default_iter()
        iteration = contract_lib.iter_dirname(iteration)
        expected = args.expected if args.expected is not None else contract.get("expected_realizations")
        if expected is None:
            raise KpiError("give --expected or contract expected_realizations (no partial ensembles)")
        return read_ensemble(args.scratch, iteration, contract.eclbase(), set(range(int(expected)))), None, []
    baseline = args.baseline or str(contract.get_dotted("scenario_qc.baseline") or "")
    baseline = Path(baseline).name if baseline else ""
    if not baseline:
        raise KpiError("give --baseline or contract scenario_qc.baseline")
    folder = args.scenarios_dir or contract.path("scenarios")
    ids = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    return scenario_cases(folder, ids, baseline), baseline, []


# ---- outputs ------------------------------------------------------------------------------------------------
def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else json.dumps(row[k]) if isinstance(row.get(k), dict | list)
                                 else row[k]) for k in columns})  # fmt: skip


def fmt(value: Any) -> str:
    if value is None:
        return "missing"
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 10 else f"{value:,.0f}"
    return str(value)


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# Production KPIs: {report['source']}", "",
             f"- Cases: {len(report['cases'])} ({', '.join(report['cases'][:10])}{' ...' if len(report['cases']) > 10 else ''})",
             f"- P10/P90 convention: {report['p10_p90']}. {report['p10_p90_note']}",
             "- Missing values are missing, not zero.", ""]  # fmt: skip
    open_items = [a for a in report["assumptions"] if not a["confirmed"]]
    if open_items:
        lines.append("## Assumptions to confirm (defaults in use)")
        lines += [f"- `{a['setting']}` = {a['value']}" for a in open_items]
        lines.append("")
    lines.append("## Gas methods")
    lines += [f"- {c['method']}" for c in report["gas_methods"]]
    lines += ["", "## SUMMARY vector coverage"]
    coverage = report["vector_coverage"]
    if len(coverage) <= 10:
        lines += [
            f"- Recommended SUMMARY vectors not in {c['case']}: {', '.join(c['absent']) or 'none'}"
            for c in coverage
        ]
    else:
        counts: dict[str, int] = {}
        for c in coverage:
            for name in c["absent"]:
                counts[name] = counts.get(name, 0) + 1
        text = ", ".join(f"{n} ({k}/{len(coverage)})" for n, k in counts.items()) or "none"
        lines.append(f"- Recommended SUMMARY vectors missing from some cases (cases without it): {text}")
    lines.append("")
    if len(report["cases"]) > 1:
        lines += [
            "## Ensemble statistics",
            "",
            "| KPI | unit | n | P10 | P50 | P90 | mean |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in report["stats"]:
            lines.append(f"| {s['kpi']} | {s['unit']} | {s['n']} | {fmt(s['P10'])} | {fmt(s['P50'])} | "
                         f"{fmt(s['P90'])} | {fmt(s['mean'])} |")  # fmt: skip
        lines.append("")
    lines += ["## KPIs per case", "", "| case | KPI | value | unit | basis / note |", "|---|---|---|---|---|"]
    for k in report["per_case"][:60]:
        text = "; ".join(x for x in (k["basis"], k["note"]) if x)
        lines.append(f"| {k['case']} | {k['kpi']} | {fmt(k['value'])} | {k['unit']} | {text} |")
    if len(report["per_case"]) > 60:
        lines.append(f"| ... | {len(report['per_case']) - 60} more rows in kpis_per_case.csv | | | |")
    if report["scenario_compare"]:
        lines += ["", f"## Scenarios vs {report['baseline']}", "", "| scenario | KPI | value | baseline | rel. change |",
                  "|---|---|---|---|---|"]  # fmt: skip
        for r in report["scenario_compare"]:
            rel = "missing" if r["rel_delta"] is None else f"{r['rel_delta']:+.1%}"
            lines.append(
                f"| {r['scenario']} | {r['kpi']} | {fmt(r['value'])} | {fmt(r['baseline'])} | {rel} |"
            )
    lines += ["", "## Binding constraints", "",
              f"Method: {report['binding_method']}", ""]  # fmt: skip
    per_case = len(report["cases"]) <= 10
    summary: dict[tuple[str, str, str], dict[str, int]] = {}
    for r in report["binding"]:
        key = (str(r["case"]) if per_case else "all cases", r["entity"], r["method"])
        summary.setdefault(key, {}).setdefault(r["binding"], 0)
        summary[key][r["binding"]] += 1
    for (label, entity, method), counts in sorted(summary.items()):
        text = ", ".join(f"{k} in {v} periods" for k, v in sorted(counts.items()))
        lines.append(f"- {label}: {entity} ({method}): {text}")
    if report["notes"]:
        lines += ["", "## Notes"] + [f"- {n}" for n in report["notes"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    contract = contract_lib.load_or_exit(
        args.contract, args.profile, skip_required=("eclbase",) if not args.scratch else ()
    )
    try:
        cfg, assumptions = settings_from(contract, args.settings)
        convention = contract.p10_p90
        cases, baseline, notes = load_cases(args, contract)
        in_place = read_in_place(args.in_place) if args.in_place else {}
        out = args.out or contract.path("deliverables") / "production_kpis"
    except (KpiError, contract_lib.ContractError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    per_case: list[Kpi] = []
    periods: list[dict] = []
    binding: list[dict] = []
    for case in cases:
        per_case.append(plateau_kpi(case, cfg["plateau"]))
        per_case += end_value_kpis(case)
        per_case += recovery_kpis(case, in_place)
        vrr, inj, prod = vrr_kpi(case)
        per_case.append(vrr)
        per_case += gas_sales_kpis(case, cfg["gas_sales"])
        if cfg["npv"]:
            per_case.append(npv_kpi(case, cfg["npv"], cfg["gas_sales"]))
        periods += period_rows(case, cfg, inj, prod)
        rows, binding_notes = binding_rows(case, cfg)
        binding += rows
        notes += binding_notes
    notes = list(dict.fromkeys(notes))
    rows = [asdict(k) for k in per_case]
    report = {
        "schema_version": 1,
        "source": str(args.case or args.scratch or args.profiles or f"scenarios {args.scenarios}"),
        "cases": [c.label for c in cases],
        "baseline": baseline,
        "p10_p90": convention,
        "p10_p90_note": CONVENTION_NOTE[convention],
        "settings": cfg,
        "assumptions": assumptions,
        "gas_methods": [dict(c) for c in GAS_METHODS],
        "recommended_vectors": [{"name": n, "purpose": why} for n, why in RECOMMENDED_VECTORS],
        "vector_coverage": [vector_coverage(c) for c in cases],
        "binding_method": "WMCTL/GMCT*/FMCT* control-mode vectors where present (" + MODE_SOURCE + "). Else "
        f"rate-vs-limit, which compares the deck limits (opm.io Schedule, 0 = not set) with the simulated rate "
        f"and BHP within {cfg['binding']['rel_tol']:.1%}. Each {cfg['period']} period gets the mode active "
        "for the most days.",
        "per_case": rows,
        "stats": stats_rows(per_case, convention) if len(cases) > 1 and baseline is None else [],
        "periods": periods,
        "binding": binding,
        "scenario_compare": compare_rows(per_case, baseline) if baseline else [],
        "npv": {"status": "computed"} if cfg["npv"] else {"status": "not configured"},
        "notes": notes,
    }
    out.mkdir(parents=True, exist_ok=True)
    write_csv(
        out / "kpis_per_case.csv", rows, ["case", "kpi", "value", "unit", "status", "basis", "note", "extra"]
    )
    write_csv(out / "kpi_stats.csv", report["stats"],
              ["kpi", "unit", "n", "status", "P10", "P50", "P90", "mean", "convention", "missing_cases"])  # fmt: skip
    write_csv(
        out / "periods.csv",
        periods,
        ["case", "period", "days", "vrr", "gas_sales_mean_rate", "gas_sales_target"],
    )
    write_csv(out / "binding_constraints.csv", binding,
              ["case", "entity", "kind", "period", "binding", "share", "method", "shares"])  # fmt: skip
    write_csv(out / "scenario_compare.csv", report["scenario_compare"],
              ["scenario", "kpi", "unit", "value", "baseline", "delta", "rel_delta", "status"])  # fmt: skip
    (out / "production_kpis.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out / "production_kpis.md").write_text(markdown(report), encoding="utf-8")
    required = [r.strip() for r in args.require.split(",") if r.strip()]
    absent = sorted({k.kpi for k in per_case if k.kpi in required and k.status != "ok"})
    absent += sorted(set(required) - {k.kpi for k in per_case})
    print(f"production_kpis: {len(cases)} case(s), {sum(k.status == 'ok' for k in per_case)} KPIs ok, "
          f"{sum(k.status != 'ok' for k in per_case)} missing; wrote {out}")  # fmt: skip
    if absent:
        print(f"error: required KPIs missing: {absent}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
