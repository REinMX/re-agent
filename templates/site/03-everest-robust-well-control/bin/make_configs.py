#!/usr/bin/env python3
"""Write one Everest config for each experiment in case.yml.

  make_configs.py <case.yml> --posterior-link <snippet.yml> [--out <dir>]
  make_configs.py <case.yml> --unchecked-posterior [--out <dir>]

The posterior link snippet comes from
skills/everest-config-author-lint/scripts/ahm_posterior_link.py. It holds the realizations and the
posterior source pattern with <REALIZATION_ID>. --unchecked-posterior uses everest.realizations and
the posterior pattern of case.yml instead. Use it only for a dry run: the posterior spread is then unchecked.
The configs go to <out> (default: everest/ next to case.yml). The jobs are the ones next to this script.

case.yml `jobs` selects the forward model. `everest_models` (default) uses the maintained everest-models
steps: well_constraints provides rate events, drill_planner turns the drill
priorities into drill dates, and npv computes the NPV. Their input files go to <out>/everest_models/.
The generator refuses (exit 2) when ERT does not have these steps installed. `custom` uses
jobs/well_control.py for all steps.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import shutil
import sys
from itertools import pairwise
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]
JOB = HERE / "jobs" / "well_control.py"
QUEUE_NAME_KEY = {"lsf": "lsf_queue", "slurm": "partition", "torque": "queue"}


def load_job():
    spec = importlib.util.spec_from_file_location("well_control", JOB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def everest_models(case: dict) -> bool:
    jobs = case.get("jobs", "everest_models")
    if jobs not in ("everest_models", "custom"):
        raise ValueError(f"jobs must be everest_models or custom, not {jobs!r}")
    return jobs == "everest_models"


def installed_steps() -> set[str]:
    """Query the package's ERT plugin without loading unrelated site plugins."""
    try:
        from everest_models.forward_models import installable_forward_model_steps

        return {step().name for step in installable_forward_model_steps().data}
    except (ImportError, ValueError, TypeError) as error:
        raise ValueError(f"everest_models needs ERT and everest-models in this Python: {error}") from error


def preflight(required: set[str]) -> None:
    installed = installed_steps()
    missing = sorted(required - installed)
    missing_commands = [f"fm_{name}" for name in sorted(required) if not shutil.which(f"fm_{name}")]
    if missing or missing_commands:
        raise ValueError(
            f"everest_models jobs unavailable: {missing}; executables missing from PATH: {missing_commands}. "
            "Install everest-models and activate its environment, or select jobs: custom."
        )


def model_inputs(case: dict) -> dict:
    """Portable job inputs. Signed cumulative prices and currency-to-millions multiplier."""
    job = load_job()
    start, end = (job.day(case["schedule"][k]) for k in ("start", "end"))
    e = case["economics"]
    prices = {
        "FOPT": e["oil_price"],
        "FGPT": e["gas_price"],
        "FWPT": -e["water_production_cost"],
        "FWIT": -e["water_injection_cost"],
    }
    result = {
        "npv.yml": {
            "prices": {k: [{"date": start, "value": float(v)}] for k, v in prices.items()},
            "summary_keys": list(prices),
            "multiplier": 1,
            "default_discount_rate": float(e["discount_rate"]),
            "dates": {"start_date": start, "end_date": end, "ref_date": start},
        }
    }
    periods = (
        [job.day(p) for p in case["schedule"]["periods"]]
        if case["controls"].get("injection", {}).get("per_period")
        else [start]
    )
    boundaries = [*periods, end]
    if periods[0] != start or any(a >= b for a, b in pairwise(boundaries)):
        raise ValueError("schedule periods must increase from start and end before schedule.end")
    result["injection.yml"] = {
        w: {
            i: {
                "phase": {"value": "WATER"},
                "rate": {"value": float(rate)},
                "duration": {"value": (b - a).days},
            }
            for i, (a, b) in enumerate(pairwise(boundaries), 1)
        }
        for w, rate in case["wells"]["injectors"].items()
    }
    result["injectors.json"] = [
        {"name": w, "readydate": start.isoformat()} for w in case["wells"]["injectors"]
    ]
    drill = case.get("drill")
    if drill:
        dates = [job.day(d) for d in drill["slots"]]
        if (
            len(dates) < len(drill["wells"])
            or any(a >= b for a, b in pairwise(dates))
            or dates[0] < start
            or dates[-1] >= end
        ):
            raise ValueError("drill slots must increase within the prediction with a slot for every well")
        # One-day surrogate windows preserve the existing fixed completion dates.
        origin = dates[0] - dt.timedelta(days=1)
        slots = []
        for i, date in enumerate(dates):
            blocked = []
            if date - dt.timedelta(days=1) > origin:
                blocked.append({"start": origin, "stop": date - dt.timedelta(days=2)})
            blocked.append({"start": date + dt.timedelta(days=1), "stop": end})
            slots.append({"name": f"slot{i}", "wells": list(drill["wells"]), "unavailability": blocked})
        result["drill.yml"] = {
            "start_date": origin,
            "end_date": end,
            "rigs": [{"name": "rig", "wells": list(drill["wells"]), "slots": [s["name"] for s in slots]}],
            "slots": slots,
        }
        result["drill_wells.json"] = [{"name": w, "drill_time": 1} for w in drill["wells"]]
        result["npv.yml"]["well_costs"] = [
            {"well": w, "value": float(drill["well_cost"])} for w in drill["wells"]
        ]
    return result


def model_forward(case: dict, case_path: Path, experiment: dict) -> list[str]:
    steps = [f"well_control prepare posterior --case {case_path}"]
    if case.get("drill"):
        steps.append(
            "drill_planner -i everest_models/drill_wells.json -c everest_models/drill.yml -opt drill.json -o drill_dates.json -tl 10"
        )
    rate = " -rc inj.json" if "injection" in experiment["controls"] else ""
    steps.append(
        "well_constraints -i everest_models/injectors.json -c everest_models/injection.yml -o injection_events.json"
        + rate
    )
    steps += [f"well_control model_schedule --case {case_path}", f"well_control simulate --case {case_path}"]
    summary = "model/" + Path(case["deck"]["data"]).stem
    wells = " -i drill_dates.json" if case.get("drill") else ""
    steps += [
        f"npv -s {summary} -c everest_models/npv.yml -o npv_currency" + wells,
        f"well_control model_metrics --case {case_path}",
    ]
    return steps


def queue_system(case: dict) -> dict:
    q = case["everest"]["queue"]
    system = str(q["system"]).lower()
    out = {"name": system, "max_running": int(q["max_running"])}
    if system in QUEUE_NAME_KEY and q.get("name"):
        out[QUEUE_NAME_KEY[system]] = q["name"]
    return out


def injection_periods(case: dict) -> int:
    return len(case["schedule"]["periods"]) if case["controls"]["injection"].get("per_period") else 1


def injection(case: dict) -> dict:
    spec = case["controls"]["injection"]
    if spec.get("per_period") or everest_models(case):
        n = injection_periods(case)
        variables = [
            {"name": w, "initial_guess": [float(r)] * n} for w, r in case["wells"]["injectors"].items()
        ]
    else:
        variables = [{"name": w, "initial_guess": float(r)} for w, r in case["wells"]["injectors"].items()]
    return {
        "name": "inj",
        "min": float(spec["min"]),
        "max": float(spec["max"]),
        "perturbation_magnitude": float(spec["perturbation"]),
        "variables": variables,
    }


def production(case: dict) -> dict:
    spec = case["controls"]["production"]
    rates = {
        **case["wells"]["producers"],
        **{w: s["rate"] for w, s in ((case.get("drill") or {}).get("wells") or {}).items()},
    }
    return {
        "name": "prod",
        "min": float(spec["min"]),
        "max": float(spec["max"]),
        "perturbation_magnitude": float(spec["perturbation"]),
        "variables": [{"name": w, "initial_guess": float(rates[w])} for w in spec["wells"]],
    }


def drill(case: dict) -> dict:
    spec = case["controls"]["drill"]
    return {
        "name": "drill",
        "min": 0.0,
        "max": 1.0,
        "perturbation_magnitude": float(spec["perturbation"]),
        "variables": [
            {"name": w, "initial_guess": float(s["priority"])} for w, s in case["drill"]["wells"].items()
        ],
    }


CONTROLS = {"injection": injection, "production": production, "drill": drill}


def input_constraints(case: dict) -> list[dict]:
    cap = float(case["constraints"]["injection_capacity"])
    wells = list(case["wells"]["injectors"])
    if case["controls"]["injection"].get("per_period") or everest_models(case):
        indices = range(1, injection_periods(case) + 1)
        return [
            {"weights": {f"inj.{w}.{i}": 1.0 for w in wells}, "upper_bound": cap, "scale": cap}
            for i in indices
        ]
    return [{"weights": {f"inj.{w}": 1.0 for w in wells}, "upper_bound": cap, "scale": cap}]


def output_constraints(case: dict) -> list[dict]:
    c = case["constraints"]
    return [
        {"name": "max_fwpr", "upper_bound": float(c["max_fwpr"]), "scale": float(c["max_fwpr"])},
        {"name": "vrr", "lower_bound": float(c["min_vrr"]), "scale": 1.0},
    ]


def config(
    case: dict, case_file: Path, name: str, experiment: dict, realizations: list[int], posterior: str
) -> dict:
    ev, case_path = case["everest"], case_file.resolve()
    count = experiment.get("realizations")
    members = realizations[: int(count)] if count else realizations
    cfg = {
        "controls": [CONTROLS[c](case) for c in experiment["controls"]],
        "objective_functions": [{"name": "npv"}],
        "optimization": dict(experiment["optimization"]),
        "model": {"realizations": members},
        "environment": {
            "output_folder": f"output/{name}",
            "simulation_folder": f"{ev['simulation_root']}/{name}",
            "random_seed": int(ev["random_seed"]),
        },
        "simulator": {
            "queue_system": queue_system(case),
            "delete_run_path": not experiment.get("keep_runpath", False),
            "max_runtime": int(ev["max_runtime"]),
        },
        "install_jobs": [{"name": "well_control", "executable": str(JOB)}],
        "install_data": [{"source": posterior, "target": "posterior", "link": True}],
        "forward_model": [
            f"well_control prepare posterior --case {case_path}",
            f"well_control schedule --case {case_path}",
            f"well_control simulate --case {case_path}",
            f"well_control economics --case {case_path}",
        ],
    }
    if everest_models(case):
        cfg["forward_model"] = model_forward(case, case_path, experiment)
        cfg["install_data"].append(
            {"source": str(case_path.parent / "everest" / "everest_models"), "target": "everest_models"}
        )
    if experiment.get("constraints"):
        cfg["input_constraints"] = input_constraints(case)
        cfg["output_constraints"] = output_constraints(case)
    return cfg


def posterior_source(case: dict, link: Path | None) -> tuple[list[int], str]:
    if link is None:
        return [int(r) for r in case["everest"]["realizations"]], str(case["posterior"])
    snippet = yaml.safe_load(link.read_text(encoding="utf-8"))
    sources = [d["source"] for d in snippet.get("install_data") or [] if d.get("target") == "posterior"]
    if len(sources) != 1:
        raise SystemExit(f"{link}: expected one install_data entry with target 'posterior'")
    return [int(r) for r in snippet["model"]["realizations"]], sources[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case", type=Path)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--posterior-link", type=Path, help="snippet from ahm_posterior_link.py --target posterior"
    )
    group.add_argument(
        "--unchecked-posterior", action="store_true", help="dry run without the posterior gate"
    )
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    case = yaml.safe_load(args.case.read_text(encoding="utf-8"))
    realizations, posterior = posterior_source(case, args.posterior_link)
    if "<REALIZATION_ID>" not in posterior:
        raise SystemExit(f"the posterior source must hold <REALIZATION_ID>: {posterior}")
    out = args.out or args.case.resolve().parent / "everest"
    try:
        selected_models = everest_models(case)
    except ValueError as error:
        print(f"preflight: {error}", file=sys.stderr)
        return 2
    if selected_models:
        required = {"npv", "well_constraints"} | ({"drill_planner"} if case.get("drill") else set())
        try:
            preflight(required)
            inputs = model_inputs(case)
        except (ValueError, OSError) as error:
            print(f"preflight: {error}", file=sys.stderr)
            return 2
        assets = out.resolve() / "everest_models"
        assets.mkdir(parents=True, exist_ok=True)
        for filename, data in inputs.items():
            text = (
                json.dumps(data, indent=2)
                if filename.endswith(".json")
                else yaml.safe_dump(data, sort_keys=False)
            )
            (assets / filename).write_text(text + "\n", encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    note = (
        "posterior gated by ahm_posterior_link.py" if args.posterior_link else "posterior NOT gated (dry run)"
    )
    for name, experiment in case["experiments"].items():
        cfg = config(case, args.case, name, experiment, realizations, posterior)
        if everest_models(case):
            cfg["install_data"][-1]["source"] = str(assets)
        (out / f"{name}.yml").write_text(
            f"# generated by make_configs.py from {args.case.name}; {note}\n"
            + yaml.safe_dump(cfg, sort_keys=False),
            encoding="utf-8",
        )
        print(f"wrote {out / f'{name}.yml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
