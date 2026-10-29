#!/usr/bin/env python3
"""Everest forward-model steps for a well-control prediction.

  well_control.py prepare <posterior realization>   copy the deck, link the posterior include folder and restart
  well_control.py schedule                          controls (inj.json, prod.json, drill.json) -> schedule, drill_plan.json
  well_control.py model_schedule                    maintained rate events and drill dates -> deck schedule
  well_control.py simulate                          run the simulator on the prediction deck
  well_control.py economics                         summary -> npv, fopt_mm, max_fwpr, vrr files
  well_control.py metrics                           summary -> fopt_mm, max_fwpr, vrr files (npv comes from the npv job)

Each step takes --case <case.yml> (default: case.yml in the runpath). The schedule and the economics read
the same case file, so the cost of a well and its drill date agree.
A drill priority sorts the new wells. The well with the highest priority gets the first rig slot.
With jobs: everest_models, maintained jobs provide rate events, drill dates and NPV.
This script writes deck-specific completions, pressure limits and report dates from those events.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

CASE = Path("case.yml")


def load_case(path: Path = CASE) -> dict:
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    case["_dir"] = path.resolve().parent
    return case


def day(value) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def load_controls(name: str) -> dict:
    path = Path(f"{name}.json")
    return json.loads(path.read_text()) if path.exists() else {}


def period_value(spec, period_index: int) -> float:
    if isinstance(spec, dict):
        keys = sorted(spec, key=int)
        return float(spec[keys[min(period_index, len(keys) - 1)]])
    return float(spec)


def drill_plan(case: dict, priorities: dict) -> dict[str, dt.date]:
    drill = case.get("drill") or {}
    wells = drill.get("wells") or {}
    if not wells:
        return {}
    slots = [day(s) for s in drill["slots"]]
    if len(wells) > len(slots):
        raise SystemExit(f"{len(wells)} new wells and {len(slots)} rig slots")
    priority = {w: float(priorities.get(w, spec["priority"])) for w, spec in wells.items()}
    order = sorted(wells, key=lambda w: (-priority[w], w))
    return {w: slots[i] for i, w in enumerate(order)}


def report_dates(case: dict, plan: dict[str, dt.date]) -> list[dt.date]:
    s = case["schedule"]
    start, end, step = day(s["start"]), day(s["end"]), int(s["report_months"])
    out = {day(p) for p in s["periods"]} | set(plan.values())
    d = start
    while d < end:
        out.add(d)
        month = d.month - 1 + step
        d = dt.date(d.year + month // 12, month % 12 + 1, 1)
    out.add(end)
    return sorted(x for x in out if start < x <= end)


def schedule_text(case: dict, inj: dict, prod: dict, plan: dict[str, dt.date]) -> str:
    s, wells = case["schedule"], case["wells"]
    start, end = day(s["start"]), day(s["end"])
    periods = [day(p) for p in s["periods"]]
    new_wells = (case.get("drill") or {}).get("wells") or {}
    rates = {**wells["producers"], **{w: spec["rate"] for w, spec in new_wells.items()}}
    lines = []
    for spec in new_wells.values():
        lines.append((case["_dir"] / spec["definition"]).read_text(encoding="utf-8").rstrip("\n"))

    def events(today: dt.date) -> list[str]:
        pidx = max(i for i, p in enumerate(periods) if p <= today)
        new_period = today in periods
        open_prod = list(wells["producers"]) + [w for w, d in plan.items() if d <= today]
        targets = [w for w in open_prod if new_period or plan.get(w) == today]
        out = []
        if targets:
            out.append("WCONPROD")
            for w in targets:
                rate = period_value(prod.get(w, rates[w]), pidx)
                out.append(f" '{w}' OPEN LRAT 1* 1* 1* {rate:.2f} 1* {s['bhp_min_prod']} /")
            out.append("/")
        if new_period:
            out.append("WCONINJE")
            for w, base in wells["injectors"].items():
                rate = period_value(inj.get(w, base), pidx)
                out.append(f" '{w}' WATER OPEN RATE {rate:.2f} 1* {s['bhp_max_inj']} /")
            out.append("/")
        return out

    lines += events(start)
    for d in report_dates(case, plan):
        lines += ["DATES", f" {d.day} '{d.strftime('%b').upper()}' {d.year} /", "/"]
        if d < end:
            lines += events(d)
    return "\n".join(lines) + "\n"


def schedule(case: dict) -> None:
    plan = drill_plan(case, load_controls("drill"))
    text = schedule_text(case, load_controls("inj"), load_controls("prod"), plan)
    Path(case["deck"]["schedule_file"]).write_text(text, encoding="utf-8")
    Path("drill_plan.json").write_text(json.dumps({w: d.isoformat() for w, d in plan.items()}, indent=1))
    print(
        f"{case['deck']['schedule_file']} written; drill plan {json.dumps({w: str(d) for w, d in plan.items()})}"
    )


def model_schedule(case: dict) -> None:
    """Translate maintained rate events and drill dates to deck-specific keywords."""
    records = json.loads(Path("injection_events.json").read_text())
    expected = set(case["wells"]["injectors"])
    if {w["name"] for w in records} != expected or len(records) != len(expected):
        raise ValueError("injection events must contain exactly the case injectors")
    periods = [day(p) for p in case["schedule"]["periods"]]
    inj = {}
    for well in records:
        ops = well["ops"]
        expected_dates = (
            periods
            if case["controls"].get("injection", {}).get("per_period")
            else [day(case["schedule"]["start"])]
        )
        if [day(op["date"]) for op in ops] != expected_dates:
            raise ValueError("injection event dates do not match the case periods")
        values = {}
        for i, op in enumerate(ops, 1):
            tokens = op["tokens"]
            if op["opname"] != "rate" or tokens["phase"] != "WATER":
                raise ValueError("expected WATER rate events")
            values[str(i)] = float(tokens["rate"])
        inj[well["name"]] = values
    plan = {}
    if case.get("drill"):
        wells = json.loads(Path("drill_dates.json").read_text())
        if {w["name"] for w in wells} != set(case["drill"]["wells"]) or len(wells) != len(
            case["drill"]["wells"]
        ):
            raise ValueError("drill output must contain exactly the new wells")
        plan = {w["name"]: day(w["readydate"]) for w in wells}
        slots = {day(d) for d in case["drill"]["slots"]}
        if not set(plan.values()) <= slots or len(set(plan.values())) != len(plan):
            raise ValueError("drill planner did not preserve the distinct fixed rig slots")
    text = schedule_text(case, inj, load_controls("prod"), plan)
    Path(case["deck"]["schedule_file"]).write_text(text, encoding="utf-8")
    Path("drill_plan.json").write_text(json.dumps({w: d.isoformat() for w, d in plan.items()}))


def relink(source: Path, link: Path) -> None:
    """Point link at source. A retried or resumed realization finds the link from its first attempt; replace it.
    Only the runpath is written, never the posterior."""
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.exists():
        raise SystemExit(f"{link} is a folder, not a link; refusing to replace it")
    os.symlink(source, link)


def prepare(case: dict, posterior: str) -> None:
    deck = case["deck"]
    post = Path(posterior).resolve()
    restarts = sorted(post.glob(deck["restart_glob"]))
    if len(restarts) != 1:
        raise SystemExit(f"expected one restart {deck['restart_glob']} in {post}, found {len(restarts)}")
    relink(post / deck["include_dir"], Path(deck["include_dir"]))
    data = Path(deck["data"])
    Path("model").mkdir(exist_ok=True)
    shutil.copy(data, Path("model") / data.name)
    link = Path(deck["restart_link"] + ".UNRST")
    link.parent.mkdir(parents=True, exist_ok=True)
    relink(restarts[0], link)
    print("linked posterior", post)


def simulate(case: dict) -> None:
    sim = case["simulator"]
    command = [sim["executable"], str(Path("model") / Path(case["deck"]["data"]).name), "--output-dir=model"]
    command += str(sim.get("args") or "").split()
    print("$", " ".join(command))
    sys.exit(subprocess.run(command, check=False).returncode)


def years(start: dt.date, when: dt.date) -> float:
    return (when - start).days / 365.25


def npv_value(case: dict, df, plan: dict[str, dt.date]) -> float:
    """df: summary rows from the start date, index = timestamps. NPV in millions, discounted at the end of each step."""
    e, start = case["economics"], day(case["schedule"]["start"])
    prev = df.iloc[0]
    npv = 0.0
    for when, row in df.iloc[1:].iterrows():
        cash = (
            e["oil_price"] * (row.FOPT - prev.FOPT)
            + e["gas_price"] * (row.FGPT - prev.FGPT)
            - e["water_production_cost"] * (row.FWPT - prev.FWPT)
            - e["water_injection_cost"] * (row.FWIT - prev.FWIT)
        )
        npv += cash / (1 + e["discount_rate"]) ** years(start, when.date())
        prev = row
    cost = float((case.get("drill") or {}).get("well_cost") or 0.0)
    capex = sum(cost / (1 + e["discount_rate"]) ** years(start, d) for d in plan.values())
    return (npv - capex) / 1e6


def metric_values(df) -> dict[str, float]:
    """df: summary rows from the start date with FOPT FWPR FVPR FVIR."""
    step_days = df.index.to_series().diff().dt.days.fillna(0).to_numpy()
    voidage_out = float((df.FVPR.to_numpy() * step_days).sum())
    voidage_in = float((df.FVIR.to_numpy() * step_days).sum())
    return {
        "fopt_mm": (df.iloc[-1].FOPT - df.iloc[0].FOPT) / 1e6,
        "max_fwpr": float(df.FWPR.max()),
        "vrr": voidage_in / max(voidage_out, 1.0),
    }


def economics_values(case: dict, df, plan: dict[str, dt.date]) -> dict[str, float]:
    """df: summary rows from the start date, index = timestamps, columns FOPT FGPT FWPT FWIT FWPR FVPR FVIR."""
    return {"npv": npv_value(case, df, plan), **metric_values(df)}


def prediction_summary(case: dict, keys: list[str]):
    from res2df import ResdataFiles, summary

    data = Path("model") / Path(case["deck"]["data"]).name
    df = summary.df(ResdataFiles(str(data)), column_keys=keys, time_index="raw")
    missing = sorted(set(keys) - set(df.columns))
    if missing:
        raise SystemExit(f"the prediction does not write: {', '.join(missing)}")
    return df[df.index >= day(case["schedule"]["start"]).isoformat()]


def write_values(values: dict[str, float]) -> None:
    for name, value in values.items():
        Path(name).write_text(f"{value:.6f}\n")
    print(json.dumps(values))


def economics(case: dict) -> None:
    df = prediction_summary(case, ["FOPT", "FGPT", "FWPT", "FWIT", "FWPR", "FVPR", "FVIR"])
    plan = {w: dt.date.fromisoformat(d) for w, d in json.loads(Path("drill_plan.json").read_text()).items()}
    write_values(economics_values(case, df, plan))


def metrics(case: dict) -> None:
    write_values(
        metric_values(prediction_summary(case, ["FOPT", "FGPT", "FWPT", "FWIT", "FWPR", "FVPR", "FVIR"]))
    )


def main(argv: list[str]) -> None:
    path = CASE
    if "--case" in argv:
        i = argv.index("--case")
        path, argv = Path(argv[i + 1]), argv[:i] + argv[i + 2 :]
    if not argv:
        raise SystemExit(__doc__)
    case = load_case(path)
    if argv[0] == "prepare" and len(argv) == 2:
        prepare(case, argv[1])
    elif argv[0] == "schedule":
        schedule(case)
    elif argv[0] == "model_schedule":
        model_schedule(case)
    elif argv[0] == "simulate":
        simulate(case)
    elif argv[0] == "economics":
        economics(case)
    elif argv[0] == "model_metrics":
        metrics(case)
        value = float(Path("npv_currency").read_text()) / 1e6
        if not math.isfinite(value):
            raise ValueError("npv job returned a nonfinite value")
        write_values({"npv": value})
    else:
        raise SystemExit(f"unknown step {' '.join(argv)}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
