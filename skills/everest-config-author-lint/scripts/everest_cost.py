#!/usr/bin/env python3
"""Upper-bound budget estimate, not a dispatch prediction or permission to start a run."""

import argparse
import csv
import json
import math
import re
from pathlib import Path

from everest_lint import number, read_contract, read_yaml

try:
    from ropt.config.constants import DEFAULT_NUMBER_OF_PERTURBATIONS
except ImportError:
    DEFAULT_NUMBER_OF_PERTURBATIONS = 5

# Methods without a gradient: Everest dispatches no perturbations for them.
try:
    from ropt.backend.scipy import _NO_GRADIENT
except ImportError:
    # Same set as ropt 0.27.1.
    _NO_GRADIENT = {"cobyla", "cobyqa", "nelder-mead", "powell", "differential_evolution"}
DERIVATIVE_FREE = {f"scipy/{m}" for m in _NO_GRADIENT}


def estimate(cfg, sim_hours, cores=1, max_running=None):
    opt = cfg.get("optimization", {})
    batches = opt.get("max_batch_num")
    derivative_free = str(opt.get("algorithm", "")).lower() in DERIVATIVE_FREE
    perturbations = 0 if derivative_free else opt.get("perturbation_num", DEFAULT_NUMBER_OF_PERTURBATIONS)
    members = cfg.get("model", {}).get("realizations", [])
    running = (
        max_running
        if max_running is not None
        else cfg.get("simulator", {}).get("queue_system", {}).get("max_running", 1)
    )
    for name, value, minimum in (
        ("max_batch_num", batches, 1),
        ("perturbation_num", perturbations, 0),
        ("cores", cores, 1),
        ("max_running", running, 1),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if (
        not isinstance(members, list)
        or not members
        or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in members)
        or len(set(members)) != len(members)
    ):
        raise ValueError("nonempty unique nonnegative integer realization list required")
    if not number(sim_hours) or sim_hours <= 0:
        raise ValueError("sim_hours must be positive and finite")
    sims = batches * (perturbations + 1) * len(members)
    formula = (
        "max_batch_num * n_realizations (derivative-free: perturbation_num ignored)"
        if derivative_free
        else "max_batch_num * (perturbation_num + 1) * n_realizations"
    )
    result = {
        "simulations_upper_bound": sims,
        "simulations": sims,
        "core_hours": sims * sim_hours * cores,
        "wall_hours": math.ceil(sims / running) * sim_hours,
        "max_running": running,
        "formula": formula,
    }
    cvar = opt.get("cvar")
    if isinstance(cvar, dict) and cvar:
        if "number_of_realizations" in cvar:
            tail = f"number_of_realizations={cvar['number_of_realizations']}"
        else:
            tail = f"ceil(percentile * n_realizations) = ceil({cvar.get('percentile')} * {len(members)})"
        result["cvar_note"] = (
            f"optimization.cvar is set: gradient batches may only simulate the tail members ({tail}), "
            "so the actual count can be far lower than the upper bound."
        )
    return result


def _index(path):
    match = re.search(r"(\d+)$", path.name)
    return (int(match.group(1)) if match else math.inf, path.name)


def _count_csv_rows(paths):
    counts = {}
    for path in paths:
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if "batch_id" not in (reader.fieldnames or []):
                raise ValueError(f"no batch_id column in {path}")
            for row in reader:
                batch = (row["batch_id"] or "").strip()
                if not batch.isdigit():
                    raise ValueError(f"batch_id {batch!r} is not a nonnegative integer in {path}")
                counts[int(batch)] = counts.get(int(batch), 0) + 1
    if not counts:
        raise ValueError(f"no rows in {', '.join(map(str, paths))}")
    return {f"batch_{batch}": counts[batch] for batch in sorted(counts)}


def _count_simulation_folder(folder):
    batches = sorted((p for p in folder.glob("batch_*") if p.is_dir()), key=_index)
    if not batches:
        raise ValueError(
            f"no batch_* folders and no simulation_results/experiments/*/experiment_results.csv in {folder}"
        )
    counts = {}
    empty = 0
    for batch in batches:
        total = 0
        for member in (p for p in batch.glob("realization_*") if p.is_dir()):
            entries = list(member.iterdir())
            empty += not entries
            total += sum(1 for p in entries if p.is_dir()) or 1
        counts[batch.name] = total
    notes = []
    if empty:
        notes.append(
            f"WARNING: {empty} realization_* folders are empty, so the run folders were deleted. "
            "The count assumes 1 simulation per realization and can be low. "
            "Give the Everest output_folder (or its experiment_results.csv) to --observed for an exact count."
        )
    return counts, notes


def count_observed(path):
    """Count dispatched simulations per batch of a previous Everest run. Read-only.

    The path is an Everest output_folder, an experiment_results.csv file, or a simulation folder.
    Returns the counts per batch and the notes to print.
    """
    path = Path(path)
    if path.is_file():
        tables = [path]
    elif path.is_dir():
        tables = sorted(path.glob("simulation_results/experiments/*/experiment_results.csv"))
    else:
        raise ValueError(f"observed path not found: {path}")
    if not tables:
        return _count_simulation_folder(path)
    notes = [
        "Counted rows of experiment_results.csv (unperturbed and perturbation). A failed simulation has no row, "
        "so the count is a lower bound when any simulation failed."
    ]
    return _count_csv_rows(tables), notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--batches", type=int)
    parser.add_argument("--perturbation-num", type=int)
    parser.add_argument("--realizations", type=int)
    parser.add_argument("--sim-hours", type=float, required=True)
    parser.add_argument("--cores", type=int, default=1)
    parser.add_argument("--max-running", type=int)
    parser.add_argument("--contract", type=Path)
    parser.add_argument(
        "--observed",
        type=Path,
        metavar="PATH",
        help="count the simulations of a previous run (read-only). PATH is an Everest output_folder, "
        "an experiment_results.csv file, or a simulation folder",
    )
    args = parser.parse_args(argv)
    try:
        cfg = (
            read_yaml(args.config)
            if args.config
            else {
                "optimization": {"max_batch_num": args.batches, "perturbation_num": args.perturbation_num},
                "model": {"realizations": list(range(args.realizations or 0))},
            }
        )
        result = estimate(cfg, args.sim_hours, args.cores, args.max_running)
        contract = read_contract(args.contract) if args.contract else {}
        limit = contract.get("everest_max_sims_per_run")
        exceeded = False
        if limit not in (None, ""):
            limit = int(limit)
            if limit <= 0:
                raise ValueError("everest_max_sims_per_run must be positive")
            exceeded = result["simulations"] > limit
        observed, observed_notes = count_observed(args.observed) if args.observed else (None, [])
        print("Budget estimate (upper bound):")
        print(json.dumps(result, indent=2))
        print(
            f"The simulation count {result['simulations_upper_bound']} is an upper bound. "
            "Function-only batches (for example optpp), early convergence, cached evaluations and rejected trials "
            "give fewer simulations. Retries can add some. Wall time assumes full slots and equal simulation "
            "durations; excludes queue wait and optimizer overhead."
        )
        if "cvar_note" in result:
            print(f"CVaR: {result['cvar_note']}")
        if observed is not None:
            actual = sum(observed.values())
            print(f"Observed simulations in {args.observed}:")
            for name, count in observed.items():
                print(f"  {name}: {count}")
            print(f"  total: {actual}")
            print(f"  ratio actual/upper bound: {actual / result['simulations_upper_bound']:.2f}")
            for note in observed_notes:
                print(note)
        if exceeded:
            print(f"ERROR: estimate exceeds everest_max_sims_per_run={limit}")
        return int(exceeded)
    except (OSError, ValueError, TypeError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
