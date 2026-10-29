#!/usr/bin/env python3
"""Read result tables only. Adapted from ert_fmu/everest/everest_results.py."""

import argparse
import math
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "everest-config-author-lint/scripts"))
from everest_cost import estimate  # noqa: E402
from everest_lint import control_variables, read_yaml  # noqa: E402

SUCCESS = {"success", "ok", "completed", "finished"}
NOT_CONTROLS = {"batch_id", "realization", "realization_id", "simulation_id", "perturbation", "status"}


def control_names(frame, cfg=None, exclude=()):
    """The config controls, else the group.variable columns of frame.

    A run with input or output constraints also writes batch_<control> columns. They are not the control values
    (about 1e-6 in the Egg EG2 run), so a batch_<control> column is never a control and never a batch aggregate.
    """
    if cfg and cfg.get("controls"):
        return [name for name, _ in control_variables(cfg)]
    return [
        c
        for c in frame
        if "." in c and c not in NOT_CONTROLS and c not in exclude and not c.startswith("batch_")
    ]


def read_table(path):
    return pd.read_parquet(path) if Path(path).suffix in {".parquet", ".pq"} else pd.read_csv(path)


def find_experiment_results(path):
    path = Path(path)
    if path.is_file():
        return path, None
    found = sorted(path.glob("simulation_results/experiments/*/experiment_results.csv"))
    if not found:
        raise ValueError(f"no simulation_results/experiments/<uuid>/experiment_results.csv under {path}")
    if len(found) > 1:
        raise ValueError("several experiments, give one csv: " + ", ".join(p.parent.name for p in found))
    return found[0], path


def experiment_tables(results, cfg=None, objective=None):
    cfg = cfg or {}
    if not {"batch_id", "realization", "perturbation"} <= set(results):
        raise ValueError("experiment results need batch_id, realization, perturbation")
    perturbed = results["perturbation"].notna()
    frame = results[~perturbed]
    if frame.empty:
        raise ValueError("experiment results have no unperturbed rows")
    controls = control_names(frame, cfg)
    raw_names = [c for c in frame if f"batch_{c}" in frame and c not in controls]
    objectives = frame.drop(
        columns=[
            "realization",
            "simulation_id",
            *raw_names,
            *[f"batch_{c}" for c in controls],
            *[c for c in frame if c.startswith("grad(")],
        ],
        errors="ignore",
    )
    status = frame[["batch_id", "realization"]].assign(status="success")
    notes = [
        f"Dropped {int(perturbed.sum())} perturbation (gradient) rows; batch values use the unperturbed rows.",
        "Everest 23 writes no realization status. Each realization with an unperturbed row counts as success; "
        "failures are inferred from realizations in the config without a row.",
    ]
    if not cfg.get("model", {}).get("realizations"):
        notes.append("No config realizations: missing (failed) members are unknown.")
    if objective is None and cfg.get("optimization", {}).get("cvar") and cfg.get("objective_functions"):
        objective = cfg["objective_functions"][0]["name"]
    members = None
    if objective is not None:
        if objective not in frame:
            raise ValueError(f"objective column {objective} not in experiment results")
        members = frame[["batch_id", "realization", objective]].rename(columns={objective: "objective"})
    return objectives, status, members, notes


def experiment_failure(text):
    """None when everest.log has no 'Experiment failed', else its last error line ('' when there is none)."""
    failed = re.search(r"Experiment failed[^\n]*(?:\n[^\n]*){0,3}", text)
    if not failed:
        return None
    reason = re.findall(r"^\S*Error: .*$", failed.group(0), flags=re.MULTILINE)
    return reason[-1].strip() if reason else ""


def everest_log(output_folder):
    log = Path(output_folder) / "logs" / "everest.log"
    return log, log.read_text(encoding="utf-8", errors="replace") if log.is_file() else None


def output_logs(output_folder, run_logs):
    """logs/everest.log, then logs/everest-log-*.txt of the output folder, then the --run-log pairs not yet read.

    Everest writes the last lines of an aborted run to everest-log-<config>-yml-<timestamp>.txt; on a full disk
    everest.log can end before them.
    """
    logs = [everest_log(output_folder)]
    for log in sorted((Path(output_folder) / "logs").glob("everest-log-*.txt")):
        logs.append((log, log.read_text(encoding="utf-8", errors="replace")))
    seen = {path.resolve() for path, _ in logs}
    return logs + [(path, text) for path, text in run_logs if path.resolve() not in seen]


TOO_FEW = "Too few realizations are evaluated successfully"
SIMULATION_FAILED = r"Simulation \d+ failed"


def run_aborted_finding(logs):
    """EVD-RUN-ABORTED when a log text has TOO_FEW. logs is a list of (path, text); text None is an unread log."""
    hits = [(path, text) for path, text in logs if text is not None and TOO_FEW in text]
    if not hits:
        return None
    logging_errors = sum(text.count("--- Logging error ---") for _, text in hits)
    return finding(
        "EVD-RUN-ABORTED",
        "warning",
        f"'{TOO_FEW}' in {ids(path for path, _ in hits)}"
        + (f", with {logging_errors} '--- Logging error ---' line(s)" if logging_errors else "")
        + ". The run stopped because fewer realizations than min_realizations_success finished a batch; "
        "the result tables end before the optimizer finished (trigger: one such log line).",
        "the log does not give the cause. The usual causes are a full disk, too little memory, and "
        "forward-model errors; run ert-diagnose (triage_runpath.py --min-free-gb) on the simulation folder.",
    )


def aborted_without_results(output_folder, logs):
    """Report text for an output folder without experiment_results.csv whose logs show an aborted run, else None.

    logs is the list of --run-log (path, text) pairs; the folder logs of output_logs are added first.
    """
    run_logs = logs
    logs = output_logs(output_folder, run_logs)
    finding_line = run_aborted_finding(logs)
    failed = [(path, experiment_failure(text)) for path, text in logs if text is not None]
    failed = [(path, reason) for path, reason in failed if reason is not None]
    log, text = logs[0]
    simulations = len(re.findall(SIMULATION_FAILED, text)) if text is not None else 0
    if finding_line is None and not failed and simulations:
        finding_line = finding(
            "EVD-RUN-ABORTED",
            "warning",
            f"the run left no results and {simulations} simulation{'s' if simulations > 1 else ''} failed ('Simulation N failed' in {log}). "
            "No log has 'Too few realizations are evaluated successfully' or 'Experiment failed'; a full disk "
            "can stop the log before that line (trigger: no experiment_results.csv and one or more such lines).",
            "the run can still be in progress, or stopped by the user or the queue; check that no Everest process "
            "runs. Run ert-diagnose (triage_runpath.py --min-free-gb) on the simulation folder.",
        )
    if finding_line is None:
        if not failed:
            return None
        reasons = [reason for _, reason in failed if reason]
        finding_line = finding(
            "EVD-RUN-ABORTED",
            "warning",
            f"'Experiment failed' in {ids(path for path, _ in failed)}"
            + (f" ({reasons[-1]})" if reasons else "")
            + ". The run stopped before Everest wrote experiment_results.csv (trigger: one such log line).",
            "the log does not give the cause. The usual causes are a full disk, too little memory, and "
            "forward-model errors; run ert-diagnose (triage_runpath.py --min-free-gb) on the simulation folder.",
        )
    counts = [
        f"{len(re.findall(SIMULATION_FAILED, text))} 'Simulation N failed' lines in {path}"
        for path, text in logs
        if text is not None
    ]
    return (
        f"<!-- Sources: experiment_results={output_folder}, run_log={ids(path for path, _ in run_logs)} -->\n"
        "# Everest diagnostics\n\n"
        f"No simulation_results/experiments/<uuid>/experiment_results.csv under {output_folder}. "
        "The run did not finish; there are no batch results to diagnose.\n\n"
        "## Findings\n\n"
        f"- {finding_line}\n\n"
        "## Run logs\n\n"
        + "\n".join(f"- NOTE: {count} (simulation ids include perturbed runs)." for count in counts)
        + "\n"
    )


def count_failed_simulations(output_folder):
    log, text = everest_log(output_folder)
    if text is None:
        return f"No {log}; the logged simulation failure count is unknown."
    count = len(re.findall(SIMULATION_FAILED, text))
    note = f"{count} 'Simulation N failed' lines in {log} (simulation ids include perturbed runs)."
    reason = experiment_failure(text)
    if reason is not None:
        note += (
            f" EXPERIMENT FAILED per {log.name}"
            + (f" ({reason})" if reason else "")
            + ". `everest run` can still exit 0; do not treat these results as a finished optimization."
        )
    return note


def markdown_table(frame):
    # No optional tabulate dependency.
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    return (
        "| "
        + " | ".join(map(cell, frame.columns))
        + " |\n| "
        + " | ".join("---" for _ in frame.columns)
        + " |\n"
        + "\n".join(
            "| " + " | ".join(map(cell, row)) + " |" for row in frame.itertuples(index=False, name=None)
        )
    )


def limit_violation(item, value, tolerance):
    lower, upper, target = item.get("lower_bound"), item.get("upper_bound"), item.get("target")
    if lower is not None and value < lower - tolerance:
        return f"< lower bound {lower:g}"
    if upper is not None and value > upper + tolerance:
        return f"> upper bound {upper:g}"
    if target is not None and abs(value - target) > tolerance:
        return f"!= target {target:g}"
    return None


def best_batch_note(batch, initial, total, direction, violations, constrained):
    def best_of(frame):
        return frame.loc[frame[total].idxmin() if direction == "minimize" else frame[total].idxmax()]

    def improvement(row):
        return initial[total] - row[total] if direction == "minimize" else row[total] - initial[total]

    best = best_of(batch)
    best_id, initial_id = int(best["batch_id"]), int(initial["batch_id"])
    overall = (
        f"Best batch {best_id}: {total}={best[total]:.8g}; initial batch {initial_id}: {initial[total]:.8g}. "
        f"Improvement ({direction}): {improvement(best):.8g}."
    )
    if not constrained:
        return overall
    infeasible = (
        f"Best batch overall {best_id} is INFEASIBLE ({'; '.join(violations[best_id])})."
        if violations[best_id]
        else ""
    )
    feasible = batch[batch["feasible"] == "yes"]
    if feasible.empty:
        return f"No feasible batch. {overall}\n\n{infeasible}"
    chosen = best_of(feasible)
    chosen_id = int(chosen["batch_id"])
    lines = [
        f"Best feasible batch {chosen_id}: {total}={chosen[total]:.8g}; initial batch {initial_id}: "
        f"{initial[total]:.8g}. Improvement ({direction}): {improvement(chosen):.8g}."
    ]
    if chosen_id == initial_id:
        lines.append("No feasible improvement over the initial batch.")
    if infeasible:
        lines.append(infeasible)
    return "\n\n".join(lines)


def plan_tables(objectives, cfg=None, controls=None, total="batch_total_objective_value", tolerance=1e-8):
    """Batch aggregates, control trajectory, and constraint violations of one experiment."""
    cfg = cfg or {}
    if objectives.empty or not {"batch_id", total} <= set(objectives):
        raise ValueError(f"nonempty objective table needs batch_id and {total}")
    frame = objectives.copy()
    if frame["batch_id"].isna().any():
        raise ValueError("missing batch ids")
    frame["batch_id"] = pd.to_numeric(frame["batch_id"], errors="raise")
    if (
        not frame["batch_id"]
        .map(lambda value: math.isfinite(value) and value >= 0 and value == int(value))
        .all()
    ):
        raise ValueError("batch ids must be nonnegative integers")
    frame[total] = pd.to_numeric(frame[total], errors="raise")
    if not frame[total].map(math.isfinite).all():
        raise ValueError("nonfinite objective")
    bound_map = dict(control_variables(cfg)) if cfg.get("controls") else {}
    control_frame = controls.copy() if controls is not None else frame
    if "batch_id" not in control_frame:
        raise ValueError("controls table requires batch_id")
    names = control_names(control_frame, cfg, exclude={total})
    batch_controls = {f"batch_{name}" for name in names}
    objective_columns = [
        c for c in frame if c.startswith("batch_") and c != "batch_id" and c not in batch_controls
    ]
    for item in cfg.get("output_constraints", []):
        column = item["name"] if item["name"] in frame else "batch_" + item["name"]
        if column not in frame:
            raise ValueError(f"missing output constraint {column}")
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if column not in objective_columns:
            objective_columns.append(column)
    if total not in objective_columns:
        objective_columns.append(total)
    for column in objective_columns:
        if frame.groupby("batch_id")[column].nunique(dropna=False).max() > 1:
            raise ValueError(f"inconsistent batch aggregate {column}; filter perturbations first")
    batch = frame.groupby("batch_id", sort=True)[objective_columns].first().reset_index()
    trajectory = None
    if names:
        if not set(names) <= set(control_frame):
            raise ValueError("missing configured controls: " + str(set(names) - set(control_frame)))
        if control_frame.groupby("batch_id")[names].nunique(dropna=False).max().max() > 1:
            raise ValueError("controls vary within batch; filter perturbations first")
        trajectory = control_frame.groupby("batch_id")[names].first().sort_index()
        if set(trajectory.index) != set(batch["batch_id"]):
            raise ValueError("control/objective batch sets differ")
        for name in names:
            trajectory[name] = pd.to_numeric(trajectory[name], errors="raise")
            if not trajectory[name].map(math.isfinite).all():
                raise ValueError(f"nonfinite control {name}")
    constraint_lines = []
    violations = {int(batch_id): [] for batch_id in batch["batch_id"]}
    for item in cfg.get("output_constraints", []):
        column = item["name"] if item["name"] in batch else "batch_" + item["name"]
        for _, row in batch.iterrows():
            value = row[column]
            if not math.isfinite(value):
                raise ValueError(f"nonfinite constraint {column}")
            violation = limit_violation(item, value, tolerance)
            if violation:
                violations[int(row["batch_id"])].append(f"{column}={value:.8g} {violation}")
            constraint_lines.append(
                f"- Batch {int(row['batch_id'])} {column}={value}: {'VIOLATION' if violation else 'within limits'}."
            )
    for item in cfg.get("input_constraints", []):
        if trajectory is None or not set(item.get("weights", {})) <= set(trajectory):
            raise ValueError("missing controls for linear input constraint")
        for batch_id, row in trajectory.iterrows():
            value = sum(row[name] * weight for name, weight in item["weights"].items())
            violation = limit_violation(item, value, tolerance)
            if violation:
                violations[int(batch_id)].append(f"linear input total={value:.8g} {violation}")
            constraint_lines.append(
                f"- Batch {batch_id} linear input total={value}: {'VIOLATION' if violation else 'within limits'}."
            )
    bound_violation = False
    for name in names if trajectory is not None else []:
        for key, sign, word in (("min", -1, "<"), ("max", 1, ">")):
            limit = bound_map.get(name, {}).get(key)
            if limit is None:
                continue
            for batch_id, value in trajectory[name].items():
                if sign * (value - limit) > tolerance:
                    bound_violation = True
                    violations[int(batch_id)].append(f"control {name}={value:.8g} {word} {key} {limit:g}")
    constrained = bool(cfg.get("output_constraints") or cfg.get("input_constraints") or bound_violation)
    if constrained:
        batch["feasible"] = ["no" if violations[int(b)] else "yes" for b in batch["batch_id"]]
    return SimpleNamespace(
        batch=batch,
        trajectory=trajectory,
        names=names,
        bounds=bound_map,
        violations=violations,
        constraint_lines=constraint_lines,
        constrained=constrained,
    )


def chosen_batch(plan, total, direction, exclude=()):
    """Best feasible batch id (best overall when unconstrained) not in exclude, or None when there is none."""
    batch = plan.batch[plan.batch["feasible"] == "yes"] if plan.constrained else plan.batch
    batch = batch[~batch["batch_id"].isin(set(exclude))]
    if batch.empty:
        return None
    return int(
        batch.loc[batch[total].idxmin() if direction == "minimize" else batch[total].idxmax()]["batch_id"]
    )


def finding(code, severity, message, limiter):
    return f"{code} ({severity}): {message} Limiter: {limiter}"


def ids(values):
    return ", ".join(str(value) for value in values)


def budget_finding(plan, chosen, total, direction, cfg, tolerance):
    values = plan.batch[total].tolist()
    last = int(plan.batch["batch_id"].iloc[-1])
    if chosen != last or len(values) < 3:
        return None
    steps = [b - a for a, b in zip(values[-3:-1], values[-2:], strict=True)]
    if not all((-s if direction == "minimize" else s) > tolerance for s in steps):
        return None
    limit = cfg.get("optimization", {}).get("max_batch_num")
    return finding(
        "EVD-BUDGET-LIMITED",
        "warning",
        f"Best{' feasible' if plan.constrained else ''} batch {chosen} is the last observed batch"
        + (f" ({len(values)} observed, max_batch_num {limit})" if limit is not None else "")
        + f", and {total} improved in each of the last two batches "
        f"({' -> '.join(f'{v:.8g}' for v in values[-3:])}). More batches may help "
        "(trigger: best batch = last batch and two improving steps).",
        "the optimizer can stop on its own convergence test, and a gain in the last step can be gradient noise.",
    )


def start_near_constraint_finding(plan, cfg):
    initial = plan.batch.iloc[0]
    initial_id = int(initial["batch_id"])
    if plan.violations[initial_id]:
        return None
    close = []
    for item in cfg.get("output_constraints", []):
        column = item["name"] if item["name"] in plan.batch else "batch_" + item["name"]
        value = initial[column]
        for key, sign in (("lower_bound", 1), ("upper_bound", -1)):
            bound = item.get(key)
            if bound is None:
                continue
            scale = item.get("scale")
            basis, reference = (
                (f"scale {scale:g}", abs(scale)) if scale else (f"|bound| {abs(bound):g}", abs(bound))
            )
            if reference == 0:
                continue
            margin = sign * (value - bound)
            if 0 <= margin <= 0.05 * reference:
                close.append(
                    f"{column}={value:.8g} is {margin:.3g} from its {key} {bound:g} "
                    f"({100 * margin / reference:.1f} % of {basis})"
                )
    if not close:
        return None
    return finding(
        "EVD-START-NEAR-CONSTRAINT",
        "warning",
        f"Initial batch {initial_id} is feasible, but " + "; ".join(close) + " (threshold 5 %). "
        "Steps from this start can break the constraint, and the optimizer can fail to recover feasibility.",
        "a start near a limit is not an error when the optimum lies away from it; this check does not see the gradient.",
    )


def constraint_inactive_findings(plan, cfg):
    """Output constraints that stay far from their bound and barely move over all batches."""
    if len(plan.batch) < 3:
        return []
    found = []
    for item in cfg.get("output_constraints", []):
        column = item["name"] if item["name"] in plan.batch else "batch_" + item["name"]
        values = plan.batch[column].astype(float)
        spread = values.max() - values.min()
        for key, sign in (("lower_bound", 1), ("upper_bound", -1)):
            bound = item.get(key)
            if bound is None:
                continue
            scale = item.get("scale")
            basis, reference = (
                (f"scale {scale:g}", abs(scale)) if scale else (f"|bound| {abs(bound):g}", abs(bound))
            )
            if reference == 0:
                continue
            distance = (sign * (values - bound)).min()
            if spread < 0.01 * reference and distance > 0.05 * reference:
                found.append(
                    finding(
                        "EVD-CONSTRAINT-INACTIVE",
                        "info",
                        f"{column} is {values.min():.8g} to {values.max():.8g} over {len(values)} batches: range "
                        f"{spread:.3g} ({100 * spread / reference:.2f} % of {basis}, threshold 1 %), and the "
                        f"smallest distance to its {key} {bound:g} is {distance:.3g} "
                        f"({100 * distance / reference:.1f} % of {basis}, threshold 5 %). "
                        "The constraint did not limit the plan.",
                        "a structurally inactive constraint (for example rate-controlled injectors with BHP "
                        "producers keep VRR near 1 by construction) can not protect the plan; check whether the "
                        "constraint is the right quantity.",
                    )
                )
    return found


PRIORITY = re.compile(r"drill|priority", re.IGNORECASE)


def control_span(plan, name):
    limits = plan.bounds.get(name, {})
    return abs(limits["max"] - limits["min"]) if {"min", "max"} <= set(limits) else 1.0


def priority_groups(plan):
    groups = {}
    for name in plan.names:
        group = name.split(".")[0]
        if plan.bounds.get(name, {}).get("type") == "generic_control" or PRIORITY.search(group):
            groups.setdefault(group, []).append(name)
    return groups


def flat_objective_finding(plan, total):
    """Controls move between batches but the batch objective stays the same (piecewise-constant objective)."""
    if plan.trajectory is None or len(plan.batch) < 3:
        return None
    values = plan.batch[total].astype(float)
    spread = values.max() - values.min()
    if spread > max(1e-9 * values.abs().max(), 1e-12):
        return None
    moves = {
        name: (plan.trajectory[name].max() - plan.trajectory[name].min()) / control_span(plan, name)
        for name in plan.names
    }
    moved = [name for name, move in moves.items() if move > 1e-3]
    if not moved:
        return None
    largest = max(moved, key=moves.get)
    advice = []
    for group, names in priority_groups(plan).items():
        if not set(names) & set(moved):
            continue
        orders = {tuple(sorted(names, key=lambda name: -row[name])) for _, row in plan.trajectory.iterrows()}
        advice.append(
            f"Group {group} has drill or priority controls: they matter only through the order that they set, and "
            f"{'the order did not change' if len(orders) == 1 else f'{len(orders)} orders occur'} over the batches. "
            f"Evaluate the {len(names)}! = {math.factorial(len(names))} orders directly instead of a gradient "
            "optimizer."
        )
    return finding(
        "EVD-FLAT-OBJECTIVE",
        "warning",
        f"{total} is {values.iloc[0]:.8g} in all {len(values)} batches (range {spread:.3g}), but {len(moved)} "
        f"control(s) moved; the largest change is {largest} by {moves[largest]:.3g} of its range. The gradient of "
        "a piecewise-constant objective is zero or noise (trigger: 3 or more batches, a control change > 1e-3 of "
        "the range, and an objective range < 1e-9 relative or 1e-12 absolute). " + " ".join(advice),
        "a forward model that does not read the controls (a broken template or a fixed schedule) also gives a "
        "flat objective; check that the controls reach the deck.",
    )


def priority_tie_findings(plan, chosen):
    if plan.trajectory is None or chosen is None:
        return []
    groups = priority_groups(plan)
    row = plan.trajectory.loc[chosen]

    found = []
    for group, names in groups.items():
        ordered = sorted(names, key=lambda name: row[name])
        runs = [[ordered[0]]]
        for name in ordered[1:]:
            if abs(row[name] - row[runs[-1][-1]]) < 1e-6 * control_span(plan, name):
                runs[-1].append(name)
            else:
                runs.append([name])
        for run in (run for run in runs if len(run) > 1):
            value = row[run[0]]
            limits = plan.bounds.get(run[0], {})
            at_bound = [
                f"{key} bound {limits[key]:g}"
                for key in ("min", "max")
                if key in limits
                and math.isclose(value, limits[key], rel_tol=0, abs_tol=1e-6 * control_span(plan, run[0]))
            ]
            found.append(
                finding(
                    "EVD-PRIORITY-TIE",
                    "warning" if at_bound else "info",
                    f"Batch {chosen} group {group}: {' = '.join(run)} = {value:.8g}"
                    + (f" (at {at_bound[0]})" if at_bound else "")
                    + ", |diff| < 1e-6 of the range. The tie-break sets the order of these controls, "
                    "not the optimizer.",
                    "a tie is harmless when the tied controls do not set an order or the wells are interchangeable.",
                )
            )
    return found


def objectives_agree_findings(plan, cfg):
    columns = [
        f"batch_{item['name']}"
        for item in cfg.get("objective_functions", [])
        if f"batch_{item['name']}" in plan.batch
    ]
    if len(columns) < 2 or len(plan.batch) < 4:
        return []
    found = []
    for i, first in enumerate(columns):
        for second in columns[i + 1 :]:
            r = plan.batch[first].astype(float).corr(plan.batch[second].astype(float))
            if math.isfinite(r) and r > 0.9:
                found.append(
                    finding(
                        "EVD-OBJECTIVES-AGREE",
                        "info",
                        f"{first} and {second}: Pearson r={r:.3f} over {len(plan.batch)} batches "
                        "(threshold 0.9). The weighted sum shows no tradeoff between these objectives.",
                        "the batches follow the optimizer path, not a designed sample; the objectives can "
                        "disagree elsewhere in the control space.",
                    )
                )
    return found


def cvar_tail_missing_finding(status, cfg):
    cvar = cfg.get("optimization", {}).get("cvar")
    expected = set(cfg.get("model", {}).get("realizations", []))
    if not cvar or not expected or status is None:
        return None
    member = "realization" if "realization" in status else "realization_id"
    short = []
    for batch_id, group in status.groupby("batch_id", sort=True):
        ok = group[group.status.str.lower().isin(SUCCESS)][member]
        present = expected & set(ok)
        if len(present) < len(expected):
            short.append(
                f"batch {batch_id}: {len(present)} of {len(expected)}, missing {sorted(expected - present)}"
            )
    if not short:
        return None
    return finding(
        "EVD-CVAR-TAIL-MISSING",
        "warning",
        f"optimization.cvar is set ({ids(f'{k}={v}' for k, v in cvar.items())}), and the CVaR tail of these "
        f"batches is computed over fewer realizations than configured: {'; '.join(short)}.",
        "Everest can evaluate only the tail members on purpose; a missing member can also be a failed "
        "simulation (see logs/everest.log).",
    )


def cvar_gradient_findings(results, percentile, n):
    """Compare the members of each gradient batch and each perturbation with the tail size ceil(percentile * N)."""
    tail = math.ceil(percentile * n)
    perturbed = results[results["perturbation"].notna()]
    rule = f"ceil(percentile * N) = ceil({percentile} * {n}) = {tail}"
    by_batch = perturbed.groupby("batch_id")["realization"].nunique()
    by_perturbation = perturbed.groupby(["batch_id", "perturbation"])["realization"].nunique()
    tail_only = [b for b, count in by_batch.items() if count == tail < n]
    wrong = [f"batch {b}: {count}" for b, count in by_batch.items() if count not in (tail, n)]
    wrong += [
        f"batch {b} perturbation {p:g}: {count}"
        for (b, p), count in by_perturbation.items()
        if count not in (tail, n)
    ]
    found = []
    if tail_only:
        found.append(
            finding(
                "EVD-CVAR-TAIL-ONLY-GRADIENT",
                "info",
                f"Gradient rows of batch(es) {ids(tail_only)} come from {tail} of {n} realizations; {rule}. "
                "The gradient uses the CVaR tail members only.",
                "this is the expected Everest CVaR behavior; it matters when the tail members change "
                "between batches or the tail is small.",
            )
        )
    if wrong:
        found.append(
            finding(
                "EVD-CVAR-TAIL-COUNT",
                "warning",
                f"Gradient member counts differ from the CVaR tail ({rule}) and from N = {n}: {'; '.join(wrong)}. "
                "The gradient is not computed over the tail that the config gives.",
                "a failed perturbed simulation has no row; check logs/everest.log before you suspect the "
                "CVaR implementation.",
            )
        )
    return found


def experiment_findings(results, cfg):
    """Findings that need the perturbation rows of experiment_results.csv."""
    cfg = cfg or {}
    found = []
    opt = cfg.get("optimization", {})
    members = cfg.get("model", {}).get("realizations", [])
    percentile = (opt.get("cvar") or {}).get("percentile")
    if percentile is not None and members:
        found += cvar_gradient_findings(results, percentile, len(members))
    if opt.get("max_batch_num") is not None:
        try:
            cost = estimate(cfg, 1.0)
        except ValueError as error:
            found.append(finding("EVD-COST-RATIO", "info", f"Upper bound not computed: {error}.", "none."))
        else:
            bound, recorded = cost["simulations_upper_bound"], len(results)
            found.append(
                finding(
                    "EVD-COST-RATIO",
                    "info",
                    f"{recorded} recorded simulation rows (unperturbed and perturbation) against the upper "
                    f"bound {bound} ({cost['formula']}): ratio {100 * recorded / bound:.0f} %.",
                    "a failed simulation has no row, so the recorded count is a lower bound; optpp alternates "
                    "function-only and gradient batches, runs converge early, and CVaR gradient batches run "
                    "only the tail members.",
                )
            )
    return found


def natural(item):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", "/".join(item[0]))]


WELL_SHUT = re.compile(rb"Well (\S+) will be shut as it can not operate")


def well_shut_finding(simulation_folder, limit=20):
    """Scan batch_*/realization_*/**/*.PRT. Only lines that contain 'shut' are parsed."""
    folder = Path(simulation_folder)
    if not any(p.is_dir() for p in folder.glob("batch_*")):
        raise ValueError(f"no batch_* folders in simulation folder {folder}")
    files = {
        p
        for pattern in ("batch_*/realization_*/**/*.PRT", "batch_*/realization_*/**/*.prt")
        for p in folder.glob(pattern)
        if p.is_file()
    }
    shut = {}
    for path in sorted(files):
        parts = path.relative_to(folder).parts
        with path.open("rb") as handle:
            for line in handle:
                if b"shut" in line:
                    match = WELL_SHUT.search(line)
                    if match:
                        key = (parts[0], parts[1])
                        shut.setdefault(key, set()).add(match.group(1).decode("utf-8", "replace"))
    if not files:
        return None, f"No *.PRT files under {folder}/batch_*/realization_*; well shut-in is unchecked."
    if not shut:
        return None, f"No 'will be shut as it can not operate' line in {len(files)} PRT files under {folder}."
    items = [f"{b}/{r}: {ids(sorted(wells))}" for (b, r), wells in sorted(shut.items(), key=natural)]
    wells = sorted(set().union(*shut.values()))
    return (
        finding(
            "EVD-WELL-SHUT",
            "warning",
            f"Flow shut well(s) {ids(wells)} ('will be shut as it can not operate') in {len(shut)} "
            f"batch/realization folder(s) of {len(files)} PRT files: {'; '.join(items[:limit])}"
            + (f"; +{len(items) - limit} more" if len(items) > limit else "")
            + ". The objective can be discontinuous at these controls, and finite differences across the "
            "shut-in are not valid derivatives (trigger: one such PRT line).",
            "a well that shuts in the base and all perturbed runs adds no jump; compare the shut runs with "
            "their perturbation rows.",
        ),
        None,
    )


def diagnose(
    objectives,
    cfg=None,
    controls=None,
    status=None,
    per_realization=None,
    total="batch_total_objective_value",
    direction="minimize",
    tolerance=1e-8,
    findings=(),
):
    cfg = cfg or {}
    if direction not in {"minimize", "maximize"} or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("invalid direction or tolerance")
    plan = plan_tables(objectives, cfg, controls, total, tolerance)
    batch, trajectory, names, bound_map = plan.batch, plan.trajectory, plan.names, plan.bounds
    initial = batch.iloc[0]
    if status is not None:
        member = "realization" if "realization" in status else "realization_id"
        if not {"batch_id", member, "status"} <= set(status) or status.empty:
            raise ValueError("status needs batch_id, realization, status")
        if status.duplicated(["batch_id", member]).any() or set(status.batch_id) != set(batch.batch_id):
            raise ValueError("duplicate statuses or different batch sets")
    notes = []
    delta = batch[total].diff().dropna()
    if (delta > tolerance).any() if direction == "minimize" else (delta < -tolerance).any():
        notes.append(
            "Non-monotone objective: review rejected trials, gradient noise and constraint tradeoffs; this alone does not prove failure."
        )
    for name in names if trajectory is not None else []:
        low, high = bound_map.get(name, {}).get("min"), bound_map.get(name, {}).get("max")
        if low is not None and high is not None:
            values = trajectory[name]
            outside = values[(values < low - tolerance) | (values > high + tolerance)]
            if not outside.empty:
                shown = ", ".join(f"batch {b}: {v:g}" for b, v in outside.items())
                notes.append(
                    f"Control {name} outside its bounds [{low:g}, {high:g}] in {len(outside)} batch(es) ({shown}). "
                    "The simulator ran these values. scipy/COBYLA treats bounds as constraints, so its trial "
                    "points can leave the box; check facility limits before using such a run."
                )
        final = trajectory.iloc[-1][name]
        for key in ("min", "max"):
            limit = bound_map.get(name, {}).get(key)
            if limit is not None and math.isclose(final, limit, rel_tol=0, abs_tol=tolerance):
                notes.append(
                    f"Final control {name} at {key} bound {limit}: review physics before widening the bound."
                )
    opt = cfg.get("optimization", {})
    if "number_of_realizations" in opt.get("cvar", {}):
        notes.append(
            "ERROR: CVaR number_of_realizations selects BEST N realizations on this pin, use percentile."
        )
    if "slsqp" in str(opt.get("algorithm", "")).lower() and len(batch) > 1 and trajectory is not None:
        unchanged = all(
            (trajectory[name] - trajectory.iloc[0][name]).abs().max() <= tolerance for name in names
        )
        matches_guess = all(
            "initial_guess" in bound_map.get(name, {})
            and math.isclose(
                trajectory.iloc[0][name], bound_map[name]["initial_guess"], abs_tol=tolerance, rel_tol=0
            )
            for name in names
        )
        if (batch[total] - initial[total]).abs().max() <= tolerance and unchanged and matches_guess:
            notes.append(
                "SLSQP stall: objective and controls unchanged from initial guess across batches; recommend COBYLA (verified pin ert 23.0.1 / ropt 0.27.1)."
            )
    notes.insert(0, best_batch_note(batch, initial, total, direction, plan.violations, plan.constrained))
    chosen = chosen_batch(plan, total, direction)
    codes = [
        *findings,
        cvar_tail_missing_finding(status, cfg),
        budget_finding(plan, chosen, total, direction, cfg, tolerance),
        start_near_constraint_finding(plan, cfg),
        *constraint_inactive_findings(plan, cfg),
        flat_objective_finding(plan, total),
        *priority_tie_findings(plan, chosen),
        *objectives_agree_findings(plan, cfg),
    ]
    if cfg and not cfg.get("output_constraints"):
        codes.append(
            finding(
                "EVD-UNCONSTRAINED",
                "warning",
                f"The config has no output_constraints ({len(cfg.get('input_constraints', []))} input "
                "constraints). The plan is unconstrained in the simulated responses; check operating limits "
                "(for example voidage replacement, rates, pressures) before use.",
                "limits that the deck enforces (well and group controls) do not show in the config.",
            )
        )
    codes = [code for code in codes if code]
    report = (
        "# Everest diagnostics\n\n"
        + f"Objective direction: {direction}. Units are those of the input tables; no unit conversion. First observed batch is the initial baseline.\n\n"
        + "\n\n".join(notes)
        + ("\n\n## Findings\n\n" + "\n".join(f"- {code}" for code in codes) if codes else "")
        + "\n\n"
        + markdown_table(batch)
        + "\n\n"
        + "\n".join(plan.constraint_lines)
    )
    if trajectory is not None:
        report += "\n\n## Control trajectory\n\n" + markdown_table(trajectory.reset_index())
    expected = set(cfg.get("model", {}).get("realizations", []))
    if status is not None:
        report += "\n\n## Realization status\n\n"
        for batch_id, group in status.groupby("batch_id", sort=True):
            failed = group[~group.status.str.lower().isin(SUCCESS)][member].tolist()
            missing = expected - set(group[member])
            unexpected = set(group[member]) - expected if expected else set()
            report += f"- Batch {batch_id}: {len(group)} reported realizations; failed/incomplete {failed}; missing {sorted(missing)}; unexpected {sorted(unexpected)}.\n"
    else:
        report += "\n\nRealization status not supplied; failures and missing members are unknown.\n"
    if per_realization is not None:
        percentile = opt.get("cvar", {}).get("percentile")
        if percentile is None or not 0 < percentile <= 1:
            raise ValueError("CVaR comparison needs config percentile in (0, 1]")
        member = "realization" if "realization" in per_realization else "realization_id"
        if not {"batch_id", member, "objective"} <= set(per_realization) or per_realization.empty:
            raise ValueError(
                "per-realization table needs batch_id, realization, objective (raw maximized units)"
            )
        if per_realization.duplicated(["batch_id", member]).any() or set(per_realization.batch_id) != set(
            batch.batch_id
        ):
            raise ValueError("duplicate per-realization objectives or different batch sets")
        report += "\n\n## CVaR versus mean at the same controls\n\nRaw objective is maximized. Lower-tail CVaR uses fractional mass at the percentile boundary. Numerical P10 is the low 10th percentile.\n\n"
        for batch_id, group in per_realization.groupby("batch_id", sort=True):
            if expected and not set(group[member]) <= expected:
                raise ValueError("per-realization objective member set differs from config")
            if expected and set(group[member]) != expected:
                report += (
                    f"- Batch {batch_id}: n={len(group)} of {len(expected)} configured realizations; "
                    "CVaR versus mean not compared (EVD-CVAR-TAIL-MISSING).\n"
                )
                continue
            values = sorted(float(value) for value in group.objective)
            if not all(math.isfinite(v) for v in values):
                raise ValueError("nonfinite per-realization objective")
            mass = len(values) * percentile
            full = math.floor(mass)
            tail = (
                sum(values[:full]) + (mass - full) * values[full] if full < len(values) else sum(values)
            ) / mass
            report += f"- Batch {batch_id}: n={len(values)}, mean={sum(values) / len(values):.8g}, lower-tail CVaR({percentile})={tail:.8g}.\n"
    return report + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--objectives", type=Path)
    inputs.add_argument(
        "--experiment-results",
        type=Path,
        help="Everest 23 experiment_results.csv or the output_folder that holds it",
    )
    parser.add_argument("--objective", help="raw objective column for the CVaR comparison")
    parser.add_argument("--controls", type=Path)
    parser.add_argument("--status", type=Path)
    parser.add_argument("--per-realization", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--simulation-folder",
        type=Path,
        help="Everest simulation folder with batch_*/realization_*; its *.PRT files are scanned for well shut-in",
    )
    parser.add_argument(
        "--run-log",
        type=Path,
        action="append",
        default=[],
        help="Another Everest or ERT log (for example the console output) to scan for an aborted run. Repeat it",
    )
    parser.add_argument("--total", default="batch_total_objective_value")
    parser.add_argument("--direction", choices=("minimize", "maximize"), default="minimize")
    parser.add_argument("--tolerance", type=float, default=1e-8)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.experiment_results and (args.status or args.per_realization):
        parser.error("--experiment-results builds the status and per-realization tables")
    if args.objective and not args.experiment_results:
        parser.error("--objective needs --experiment-results")
    try:
        cfg = read_yaml(args.config) if args.config else None
        notes, findings = [], []
        logs = [(path, path.read_text(encoding="utf-8", errors="replace")) for path in args.run_log]
        if args.simulation_folder:
            shut, note = well_shut_finding(args.simulation_folder)
            findings += [shut] if shut else []
            notes += [note] if note else []
        if args.experiment_results and args.experiment_results.is_dir():
            if not any(
                args.experiment_results.glob("simulation_results/experiments/*/experiment_results.csv")
            ):
                report = aborted_without_results(args.experiment_results, logs)
                if report is not None:
                    args.out.write_text(report, encoding="utf-8")
                    print(f"ERROR: the Everest run aborted without results; report in {args.out}")
                    return 1
        if args.experiment_results:
            csv, output_folder = find_experiment_results(args.experiment_results)
            results = read_table(csv)
            objectives, status, members, table_notes = experiment_tables(results, cfg, args.objective)
            notes += table_notes
            findings += experiment_findings(results, cfg)
            if output_folder is not None:
                notes.append(count_failed_simulations(output_folder))
                logs = output_logs(output_folder, logs)
            sources = f"experiment_results={csv}, controls={args.controls}, config={args.config}, simulation_folder={args.simulation_folder}, run_log={ids(args.run_log)}"
        else:
            objectives = read_table(args.objectives)
            status = read_table(args.status) if args.status else None
            members = read_table(args.per_realization) if args.per_realization else None
            sources = f"objectives={args.objectives}, controls={args.controls}, status={args.status}, config={args.config}, per_realization={args.per_realization}, run_log={ids(args.run_log)}"
        findings += [aborted] if (aborted := run_aborted_finding(logs)) else []
        report = diagnose(
            objectives,
            cfg,
            read_table(args.controls) if args.controls else None,
            status,
            members,
            args.total,
            args.direction,
            args.tolerance,
            findings,
        )
        report = f"<!-- Sources: {sources} -->\n" + report
        if notes:
            report += "\n## Experiment results\n\n" + "\n".join(f"- NOTE: {note}" for note in notes) + "\n"
        args.out.write_text(report, encoding="utf-8")
        return 0
    except (OSError, ValueError, TypeError, KeyError, ImportError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
