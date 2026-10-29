---
name: everest-diagnostics
version: 0.4.6
description: Read Everest result tables and report batch objectives, constraints, control trajectories, bound hits, failures, and stalls. Compare CVaR with the mean at the same controls. Use after an optimization run. Analyst role. Read-only inputs.
---

# Check Everest results

Use the analyst role. Read the site contract and the config. Do not start or restart a run.
The script reads tables only. Source: the public donor `ert_fmu/everest/everest_results.py`.

## Step 1. Select the inputs

Use `scripts/everest_diagnostics.py` with the contract Python:

```bash
python skills/everest-diagnostics/scripts/everest_diagnostics.py --objectives objectives.csv --controls controls.csv --status status.csv --config config.yml --out report.md
```

For Everest 23, give the table that Everest writes. Use the output folder or the CSV file:

```bash
python skills/everest-diagnostics/scripts/everest_diagnostics.py --experiment-results <output_folder> --config config.yml --out report.md
```

The script finds `simulation_results/experiments/<uuid>/experiment_results.csv`.
More than one experiment is an error. Then give the CSV file.
The script removes perturbation rows and makes the objective, control, and status tables.
Everest 23 writes no status. A realization without a row failed. The config must give the realization ids.
With the output folder, the report also counts the `Simulation N failed` lines in `logs/everest.log`.
Add `--simulation-folder <folder>` to scan the Flow PRT files in `batch_*/realization_*` for well shut-in.
The script also scans each `logs/everest-log-*.txt` file of the output folder for an aborted run.
On a full disk, `everest.log` can stop before the last lines. These lines can be only in the `everest-log-*.txt` file.
Add `--run-log <file>` to scan one more log (for example the console output) for an aborted run. You can repeat it.
Do not use `--objectives`, `--status`, or `--per-realization` with this option.
If the output folder has no `experiment_results.csv`, the script reads the logs of the output folder and each `--run-log` file.
If a log has "Too few realizations are evaluated successfully" or "Experiment failed", the script writes a short report.
If `logs/everest.log` has one or more `Simulation N failed` lines, the script also writes the short report.
The report gives `EVD-RUN-ABORTED` and the count of `Simulation N failed` lines. The script then exits 1.
If no log shows an aborted run, the script writes no report and exits 1 with an error.

CSV needs pandas. Parquet also needs a pandas parquet engine. The script imports no Everest module.
Use tables from one experiment. Filter perturbation rows before this check.
The objective table needs `batch_id` and `batch_total_objective_value`.
Repeated batch rows must agree on each batch aggregate. Other `batch_*` columns appear in the report.
Control columns use `group.variable`. Supply them in the objective table or with `--controls`.
A run with input or output constraints also writes `batch_<control>` columns. They are not control values.
The script does not use them.
Each control has one value per batch. Batch sets must match.
The status table needs `batch_id`, `realization` (or `realization_id`), and `status`.
Success values are `success`, `ok`, `completed`, or `finished`, without case restrictions.
Other values count as failed or incomplete. Missing members appear separately when the config gives ids.
Without status input, the report states that failure counts are unknown.

## Step 2. Check objective and controls

Everest minimizes the optimizer total. Positive reported NPV is a separate value.
The default is `--direction minimize`. For a raw maximized objective, give `--total objective --direction maximize`.
Do not reverse the sign without evidence from the table producer.
The first observed batch is the baseline. Confirm that it is the initial evaluation before a decision.
The report gives best versus baseline, each batch, constraint limits, and the control trajectory.
With constraints, the headline gives the best feasible batch and names an infeasible overall best with its violations.
A control value outside its config `min` or `max` (more than `--tolerance`) makes the batch infeasible.
Check each constraint finding before selecting controls for a run.

Non-monotone objective values can come from rejected trials, noisy gradients, or constraint tradeoffs.
They do not alone prove a failed optimization.
For SLSQP, constant objectives and controls at the configured initial guess across batches indicate a stall.
The report recommends COBYLA based on donor pin ert 23.0.1 and ropt 0.27.1.
At a final bound, review physics and units before widening it. Do not change bounds to hide a problem.
Use `--tolerance` only with a stated numerical basis. Do not change it to force a pass.

## Step 3. Compare robust objectives

With `--experiment-results`, the script uses the first config objective when the config gives CVaR.
Use `--objective <name>` for a different objective column.
Otherwise, add `--per-realization members.csv` with `batch_id`, `realization`, and `objective`.
Each row is a raw maximized response at the same batch controls. Do not supply negated optimizer totals.
Batch sets must match the objective table. Duplicate members and members that are not in the config are errors.
A batch with fewer members than the config gets no CVaR comparison. The report gives `EVD-CVAR-TAIL-MISSING`.
The config must give `optimization.cvar.percentile` as a fraction.
The report compares mean and lower-tail CVaR at the same controls.
For a fractional member count, it uses fractional probability mass at the tail boundary.
P10 is the numerical low 10th percentile; P90 is the numerical high 90th percentile.
The report gives the member count for each comparison.
If the config uses CVaR by count, the report flags the verified BEST N selection trap.
Use `everest-config-author-lint` to correct the config copy before another run.

Do not take a zero exit code from `everest run` as success. Everest 23.0.1 can exit 0 after "Experiment failed".
With an output folder, the report flags this from `logs/everest.log`.

## Step 4. Read the finding codes

The report has a `## Findings` section. Each line has a stable code, a severity, the numbers, the trigger, and a limiter.
The limiter tells you when the finding can be a false alarm. Read the limiter before you act on a finding.

| Code | Severity | Trigger |
|---|---|---|
| `EVD-WELL-SHUT` | warning | A PRT line "will be shut as it can not operate" (needs `--simulation-folder`). |
| `EVD-CVAR-TAIL-MISSING` | warning | CVaR is set and a batch has fewer successful members than the config. |
| `EVD-CVAR-TAIL-ONLY-GRADIENT` | info | The perturbation rows of a batch come from exactly ceil(percentile * N) members, fewer than N. |
| `EVD-CVAR-TAIL-COUNT` | warning | The members of a gradient batch or of one perturbation are neither ceil(percentile * N) nor N. |
| `EVD-BUDGET-LIMITED` | warning | The best (feasible) batch is the last batch, and the last two batches improved. |
| `EVD-UNCONSTRAINED` | warning | The config has no `output_constraints`. |
| `EVD-START-NEAR-CONSTRAINT` | warning | The initial batch is feasible and within 5 % of an output constraint bound (of `scale`, else of the bound). |
| `EVD-PRIORITY-TIE` | warning at a bound, else info | At the best batch, two controls of a `generic_control` group, or a group with "drill" or "priority" in its name, differ by less than 1e-6 of the range. |
| `EVD-CONSTRAINT-INACTIVE` | info | Three or more batches. Over all batches, an output constraint moves less than 1 % and stays more than 5 % from its bound (of `scale`, else of the bound). |
| `EVD-FLAT-OBJECTIVE` | warning | Three or more batches. A control changes by more than 1e-3 of its range, and the batch objective range is less than 1e-9 relative (or 1e-12 absolute). |
| `EVD-OBJECTIVES-AGREE` | info | Two or more objectives and four or more batches, with Pearson r > 0.9 between the batch objectives. |
| `EVD-COST-RATIO` | info | The config has `max_batch_num`. The report gives the recorded rows, the `everest_cost.py` upper bound, and the ratio. |
| `EVD-RUN-ABORTED` | warning | `logs/everest.log`, a `logs/everest-log-*.txt` file, or a `--run-log` file has "Too few realizations are evaluated successfully". Without `experiment_results.csv`, "Experiment failed" or one or more `Simulation N failed` lines in `logs/everest.log` also give this code. |

The script scans only the PRT lines that contain "shut". A shut-in makes the objective discontinuous.
Do not use a finite-difference gradient across a shut-in as a derivative.
For `EVD-UNCONSTRAINED`, check the operating limits on the plan before you use it.
For `EVD-PRIORITY-TIE`, the tie-break sets the order of the tied controls, not the optimizer.
For `EVD-FLAT-OBJECTIVE`, the gradient of a piecewise-constant objective is zero or noise.
Drill and priority controls change the result only when the order changes. For such a group, the report gives the order count.
Then evaluate the n! orders directly. Do not use a gradient optimizer. Also make sure that the controls get to the deck.
For `EVD-CONSTRAINT-INACTIVE`, the constraint did not limit the plan. A constraint that the model holds by its design
can not protect the plan. Make sure that the constraint is the correct quantity.
For `EVD-RUN-ABORTED`, the log does not give the cause. The usual causes are a full disk, too little memory,
and forward-model errors. Run `ert-diagnose` with `--min-free-gb` on the simulation folder.
A full disk can show only this line and "--- Logging error ---" lines.
The codes come from the Drogon optimization suite and the Egg benchmark. They are not an acceptance test.

## Step 5. Compare two plans

Use `scripts/compare_plans.py` to compare the best feasible plans of two runs, for example two posteriors:

```bash
python skills/everest-diagnostics/scripts/compare_plans.py <output_folder_a> <output_folder_b> --config-a a.yml --config-b b.yml --cross cross.csv --out plans.md
```

Each run is an output folder or an `experiment_results.csv` file. The script selects the best feasible batch as in Step 2.
It does not select a batch with fewer successful members than `min_realizations_success`.
The default is all config realizations. Without a config, it is the largest member count in a batch.
`EVD-INCOMPLETE-BATCH` (warning) gives these batches.
With an output folder, `EVD-EXPERIMENT-FAILED` (warning) occurs when `logs/everest.log` has "Experiment failed".
Without the log, the report says that an experiment failure is unknown.
It gives each control in A and in B, and the difference divided by (max - min) from the config bounds.
A control without config bounds gets "n/a". The flip check does not use observed ranges.
The two configs must give the same bounds. The runs must have the same controls.
`EVD-PLAN-FLIP` (warning) occurs when a control moves from the lower 20 % of its range to the upper 20 %,
or when the mean absolute normalized difference is more than 0.25. Then the plan depends on the posterior.
`--cross` is a CSV with `plan`, `ensemble`, and `objective` for the four pairs of the two `--labels` (default A and B).
`EVD-CROSS-REGRET` (info) gives the regret of each plan on the other ensemble. The default `--cross-direction` is `maximize`.

## Report and done check

Write `--out` to `everest_output_root` when set, otherwise to the approved `output_root`.
Give input paths, objective direction, units from the producer, member counts, failures, and unchecked items.
Exit 1 means invalid or inconsistent input. Exit 0 means the report was written; findings can remain.
Done means the tables agree, the report exists, and each finding has evidence the user can review.
Do not infer engineering acceptance from a successful report exit code.
