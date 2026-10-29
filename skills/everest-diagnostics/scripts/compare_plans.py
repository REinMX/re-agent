#!/usr/bin/env python3
"""Compare the best feasible plans of two Everest runs. Read result tables only."""

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from everest_diagnostics import (  # noqa: E402
    SUCCESS,
    chosen_batch,
    everest_log,
    experiment_failure,
    experiment_tables,
    find_experiment_results,
    finding,
    markdown_table,
    plan_tables,
    read_table,
    read_yaml,
)


def incomplete_batches(status, cfg):
    """Batches with fewer successful members than min_realizations_success (default: all config realizations).

    Return ({batch_id: successful count}, the minimum, the source of the minimum).
    """
    cfg = cfg or {}
    expected = set(cfg.get("model", {}).get("realizations", []))
    ok = status[status["status"].str.lower().isin(SUCCESS)]
    if expected:
        ok = ok[ok["realization"].isin(expected)]
    counts = {int(b): int(n) for b, n in ok.groupby("batch_id")["realization"].nunique().items()}
    counts.update({int(b): 0 for b in set(status["batch_id"]) if int(b) not in counts})
    minimum = cfg.get("optimization", {}).get("min_realizations_success")
    if minimum is not None:
        source = f"min_realizations_success {minimum}"
    elif expected:
        minimum, source = len(expected), f"all {len(expected)} config realizations"
    else:
        minimum = max(counts.values())
        source = f"the largest member count in a batch ({minimum}); give the config for the realization list"
    return {b: n for b, n in sorted(counts.items()) if n < minimum}, minimum, source


def best_plan(path, cfg, total, direction, tolerance):
    csv, output_folder = find_experiment_results(path)
    objectives, status, _, _ = experiment_tables(read_table(csv), cfg)
    plan = plan_tables(objectives, cfg, None, total, tolerance)
    if plan.trajectory is None:
        raise ValueError(f"no control columns (group.variable) in {csv}")
    incomplete, minimum, source = incomplete_batches(status, cfg)
    chosen = chosen_batch(plan, total, direction, exclude=incomplete)
    if chosen is None:
        raise ValueError(f"no feasible batch with {source} successful realizations in {csv}")
    value = plan.batch.set_index("batch_id").loc[chosen, total]
    log, text = everest_log(output_folder) if output_folder is not None else ("logs/everest.log", None)
    return {
        "csv": csv,
        "batch": chosen,
        "total": value,
        "plan": plan,
        "incomplete": incomplete,
        "minimum": source,
        "log": log,
        "log_read": text is not None,
        "failure": experiment_failure(text) if text is not None else None,
    }


def run_findings(runs, labels):
    """EVD-INCOMPLETE-BATCH and EVD-EXPERIMENT-FAILED for each run, and notes on what was not checked."""
    found = []
    for label, run in zip(labels, runs, strict=True):
        if run["incomplete"]:
            batches = "; ".join(f"batch {b}: {n} successful" for b, n in run["incomplete"].items())
            found.append(
                finding(
                    "EVD-INCOMPLETE-BATCH",
                    "warning",
                    f"Plan {label}: {batches} (needs {run['minimum']}). These batches are not used to select the "
                    "plan.",
                    "Everest 23 writes no realization status; a member without an unperturbed row counts as "
                    "failed.",
                )
            )
        if not run["log_read"]:
            found.append(
                f"Plan {label}: no {run['log']} (give the Everest output folder); an experiment failure is unknown."
            )
        elif run["failure"] is not None:
            found.append(
                finding(
                    "EVD-EXPERIMENT-FAILED",
                    "warning",
                    f"Plan {label}: 'Experiment failed' in {run['log']}"
                    + (f" ({run['failure']})" if run["failure"] else "")
                    + ". The optimization did not finish; the best batch is not a converged plan.",
                    "`everest run` can exit 0 after this line; the batches before the failure are valid "
                    "evaluations.",
                )
            )
    return found


def control_bounds(a, b):
    """Config bounds per control. A control without min and max in a config has no bounds (None)."""
    names = list(a["plan"].trajectory)
    if set(names) != set(b["plan"].trajectory):
        raise ValueError(
            "the runs have different controls: " + ", ".join(sorted(set(names) ^ set(b["plan"].trajectory)))
        )
    bounds = {}
    for name in names:
        limits = {
            (run["plan"].bounds[name]["min"], run["plan"].bounds[name]["max"])
            for run in (a, b)
            if {"min", "max"} <= set(run["plan"].bounds.get(name, {}))
        }
        if len(limits) > 1:
            raise ValueError(f"the configs give different bounds for {name}: {sorted(limits)}")
        bounds[name] = limits.pop() if limits else None
    return names, bounds


def compare(a, b, labels=("A", "B")):
    names, bounds = control_bounds(a, b)
    rows, flips, diffs, unbounded = [], [], [], []
    for name in names:
        first, second = a["plan"].trajectory.loc[a["batch"], name], b["plan"].trajectory.loc[b["batch"], name]
        if bounds[name] is None or bounds[name][1] <= bounds[name][0]:
            unbounded.append(name)
            rows.append((name, first, second, "n/a", "no config bounds"))
            continue
        low, high = bounds[name]
        span = high - low
        diff = (second - first) / span
        diffs.append(abs(diff))
        where = [(value - low) / span for value in (first, second)]
        if min(where) <= 0.2 and max(where) >= 0.8:
            flips.append(
                f"{name} ({labels[0]} {first:.8g} at {100 * where[0]:.0f} %, "
                f"{labels[1]} {second:.8g} at {100 * where[1]:.0f} %)"
            )
        rows.append((name, first, second, round(diff, 4), f"[{low:g}, {high:g}]"))
    table = pd.DataFrame(
        rows,
        columns=[
            "control",
            labels[0],
            labels[1],
            f"normalized diff ({labels[1]}-{labels[0]})/(max-min)",
            "range",
        ],
    )
    skipped = (
        f" Not checked (give --config-a or --config-b with min and max): {', '.join(unbounded)}."
        if unbounded
        else ""
    )
    if not diffs:
        return table, "EVD-PLAN-FLIP not checked: no control has config bounds." + skipped
    mean = sum(diffs) / len(diffs)
    if flips or mean > 0.25:
        result = finding(
            "EVD-PLAN-FLIP",
            "warning",
            (
                f"Controls move between the lower and upper 20 % of their range: {'; '.join(flips)}. "
                if flips
                else ""
            )
            + f"Mean |normalized diff| = {mean:.3f} over {len(diffs)} controls (threshold 0.25). "
            "The plan depends on the posterior." + skipped,
            "a flat objective near the optimum lets controls move with little change in value; "
            "check the cross-evaluation regret before you call the plans different.",
        )
    else:
        result = (
            "No EVD-PLAN-FLIP: no control moves between the 20 % ends of its range; "
            f"mean |normalized diff| = {mean:.3f} over {len(diffs)} controls (threshold 0.25)." + skipped
        )
    return table, result


def cross_regret(cross, labels=("A", "B"), direction="maximize"):
    """Regret of each plan on the other ensemble, relative to the plan made for that ensemble."""
    if not {"plan", "ensemble", "objective"} <= set(cross):
        raise ValueError("cross table needs plan, ensemble, objective")
    cross = cross.astype({"plan": str, "ensemble": str})
    if cross.duplicated(["plan", "ensemble"]).any():
        raise ValueError("duplicate plan/ensemble rows in the cross table")
    values = {(row.plan, row.ensemble): float(row.objective) for row in cross.itertuples()}
    needed = {(p, e) for p in labels for e in labels}
    if set(values) != needed:
        raise ValueError(
            f"cross table must have exactly the plan/ensemble pairs {sorted(needed)}; got {sorted(values)}"
        )
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError("nonfinite cross objective")
    lines = []
    for plan, ensemble in ((labels[0], labels[1]), (labels[1], labels[0])):
        own, other = values[(ensemble, ensemble)], values[(plan, ensemble)]
        regret = own - other if direction == "maximize" else other - own
        lines.append(
            f"Plan {plan} on ensemble {ensemble}: objective {other:.8g}; plan {ensemble} (own) {own:.8g}; "
            f"regret {regret:.8g}."
            + (" Negative regret: the other plan does better on this ensemble." if regret < 0 else "")
        )
    return finding(
        "EVD-CROSS-REGRET",
        "info",
        " ".join(lines) + f" Direction: {direction}.",
        "each value comes from one cross-evaluation run; the regret has the noise of the ensembles.",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_a", type=Path, help="output folder or experiment_results.csv of run A")
    parser.add_argument("run_b", type=Path, help="output folder or experiment_results.csv of run B")
    parser.add_argument("--config-a", type=Path, help="config of run A (bounds and constraints)")
    parser.add_argument("--config-b", type=Path, help="config of run B (bounds and constraints)")
    parser.add_argument("--labels", nargs=2, default=("A", "B"))
    parser.add_argument("--cross", type=Path, help="CSV with plan,ensemble,objective (cross-evaluation)")
    parser.add_argument("--cross-direction", choices=("minimize", "maximize"), default="maximize")
    parser.add_argument("--total", default="batch_total_objective_value")
    parser.add_argument("--direction", choices=("minimize", "maximize"), default="minimize")
    parser.add_argument("--tolerance", type=float, default=1e-8)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    labels = tuple(args.labels)
    if labels[0] == labels[1]:
        parser.error("--labels must differ")
    try:
        runs = [
            best_plan(path, read_yaml(cfg) if cfg else None, args.total, args.direction, args.tolerance)
            for path, cfg in ((args.run_a, args.config_a), (args.run_b, args.config_b))
        ]
        table, result = compare(*runs, labels)
        report = (
            f"<!-- Sources: run_a={runs[0]['csv']}, run_b={runs[1]['csv']}, config_a={args.config_a}, "
            f"config_b={args.config_b}, cross={args.cross} -->\n# Everest plan comparison\n\n"
            + "\n".join(
                f"- Plan {label}: best{' feasible' if run['plan'].constrained else ''} batch {run['batch']}, "
                f"{args.total}={run['total']:.8g} ({args.direction})."
                for label, run in zip(labels, runs, strict=True)
            )
            + "\n\n## Findings\n\n- "
            + "\n- ".join([*run_findings(runs, labels), result])
        )
        if args.cross:
            report += "\n- " + cross_regret(read_table(args.cross), labels, args.cross_direction)
        report += "\n\n## Controls\n\n" + markdown_table(table) + "\n"
        args.out.write_text(report, encoding="utf-8")
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
