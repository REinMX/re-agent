---
name: everest-config-author-lint
version: 0.2.3
description: Check Everest YAML controls, objectives, constraints, well order, and robust optimization settings before a run. Estimate cost and prepare a link from an ERT posterior. Analyst role. Use for optimization, NPV, or drill schedule config review.
---

# Check Everest configs

Use the analyst role. Read the site contract with `reservoir-engineer`.
Read the config and its templates. Keep source files unchanged. Write a copy or a diff to `output_root`.
This skill does not start an Everest run. When the user asks for a run, show the exact command and run it.

## Step 1. Run real Everest lint

Run `scripts/everest_lint.py` with the contract Python:

```bash
python skills/everest-config-author-lint/scripts/everest_lint.py config.yml --format markdown --out report.md
```

Add `--contract site.contract.yml` to check the queue against the site.
The loader uses the repository contract loader. The contract uses `queue_system`.
The linter also accepts a supplied mapping with `profile.queue.system`.
Use `--format json` for a machine report. Exit 1 means an error. Exit 0 can include warnings.
Malformed YAML, duplicate keys, unknown control references, and missing required values are errors.
The script runs `everest lint <config>` first when Everest is available.
The default `--with-everest auto` finds Everest on PATH, or uses the nonempty
`everest_executable` contract value. This value is a path or a command name.
Use `--everest-bin /path/to/everest` for an explicit executable override.
Use `--with-everest always` to require real lint. Use `--with-everest never` for script checks only.
Real lint checks the installed Everest schema. It does not start an optimization run.
The script also checks maintained jobs through the everest-models ERT plugin.
This check runs with `--with-everest never` too. Missing jobs or executables give exit 2.
Activate the environment that contains ERT and everest-models before this check.
Each finding has source `everest` or `script`. The report merges both checks.
If Everest is missing, auto mode gives a warning and always mode gives an error.
The summary says real lint was not run. This is not a real lint pass.
Exit 1 means either check found an error. Exit 0 can include warnings or an incomplete check.
A timeout or another execution failure is an error. The timeout is 300 seconds.
Everest `lint` and `render`, including `uv run everest lint`, are read-only checks.

## Step 2. Check script traps

Check the findings:
1. Each control has finite bounds and an initial guess within them.
   For a list initial guess, each element must be within the bounds. The finding names the index.
2. Each perturbation is positive. Use about 1 to 10 percent of the range as a starting point.
   For a priority or order control, the warning is expected. These controls are piecewise constant.
   A small perturbation gives a zero gradient, so a larger perturbation is correct there.
3. Review scaling and units across rates, pressure, and order controls.
4. Positive objective weights reward a term. A water or cost term normally needs a negative weight.
   Check the sign if the forward model already converts cost to a reward.
5. Input constraints use linear weights on known controls. Output constraints name simulator responses.
   Check units and limits in the forward model. YAML alone cannot check response units.
6. Well order and priorities have no duplicates or missing wells. Order indices are integers.
   Supply a `well_order` list and variable priorities when the config needs an explicit well set.
7. The success minimum does not exceed the realization count. Check the site queue separately.

### Select robust settings

Use all accepted posterior members when cost permits. Record the ids, member count, and posterior iteration.
A small subset can omit an important response tail. A P10/P50/P90 subset is a screening set, not a proof
of a robust result. Use clusters on the key responses when one response does not describe the uncertainty.
P10 and P90 are numerical percentiles: P10 is the low 10th percentile; P90 is the high 90th percentile.

Compare the mean and lower-tail CVaR at the same controls and on the same members.
Mean rewards the average response. CVaR protects the low tail of a maximized response.
Use `optimization.cvar.percentile` as a fraction in (0, 1], such as 0.4.
On the donor verified pin, ert 23.0.1 and ropt 0.27.1, `number_of_realizations` selects the BEST N members.
The linter treats this option as an error. Use percentile. Check tail direction again on a new pin before use.
Mean minus a spread penalty is a different objective. State its penalty and units; do not label it CVaR.

Set `min_realizations_success` from an agreed policy. Do not lower it to hide failures.
Failed members can bias the objective tail. Stop the engineering decision when this bias is unknown.

| Algorithm | Evidence from the donor pin |
|---|---|
| scipy/COBYLA | Completed the checked donor loops. |
| scipy/SLSQP | Stalled at the initial guess. The linter warns. |
| Dakota optpp_q_newton | Not verified here. |

One perturbation is a low-cost StoSAG choice. Two or more can help check gradient noise at higher cost.
Review convergence tolerance and batch limits. For a small case, compare against a reference scan.
Run a scan or a pin probe only when the user asks for it.

## Step 3. Estimate cost

Run `scripts/everest_cost.py`:

```bash
python skills/everest-config-author-lint/scripts/everest_cost.py --config config.yml --sim-hours 2 --cores 4 --max-running 10 --contract site.contract.yml
```

Without a config, supply `--batches`, `--perturbation-num`, and `--realizations`.
The planning formula is batches times (perturbation count plus one) times realization count.
For 8 batches, 2 perturbations, and 5 realizations it gives 120 simulations.
For the derivative-free scipy methods (COBYLA, COBYQA, Nelder-Mead, Powell, differential_evolution), the script ignores `perturbation_num`.
These algorithms do not use a gradient. The formula is batches times realization count.
If a gradient config has no `perturbation_num`, the script uses the ropt default of 5.
Core-hours equal simulations times simulation hours times cores per simulation.
Wall hours equal the rounded-up simulation waves times simulation hours.
This assumes full slots and equal duration. It excludes queue wait and optimizer overhead.
Dispatch can differ due to cached results, early convergence, retries, line searches, and rejected trials.

The simulation count is an upper bound. On Drogon, the actual count was 31% to 79% of the bound.
Optpp alternates function-only batches and gradient batches. Early convergence stops the run before `max_batch_num`.
With `optimization.cvar`, gradient batches can simulate only the tail members. The script then writes `cvar_note`.
To calibrate, add `--observed <path>` from a previous run of a similar config.
The script gives the count for each batch and the ratio of actual count to upper bound.
The best path is the Everest `output_folder` or its `experiment_results.csv` file.
The script then counts the rows of `simulation_results/experiments/*/experiment_results.csv` for each `batch_id`.
A failed simulation has no row. Thus, if a simulation failed, the count is a lower bound.
A simulation folder also gives a count. The script counts the subfolders of each `realization_*` folder.
If a `realization_*` folder is empty, the run folders were deleted. The script then gives a WARNING.
The count then uses 1 simulation for each realization and can be too low. Use the output folder.
An estimate above `everest_max_sims_per_run` exits 1. An empty key sets no threshold.
The output is a budget estimate. Show the exact run command separately.

## Step 4. Link an accepted posterior

Run `scripts/ahm_posterior_link.py`:

```bash
python skills/everest-config-author-lint/scripts/ahm_posterior_link.py --posterior posterior.csv --prior prior.csv --data-source 'members/<REALIZATION_ID>/parameters.json' --out posterior-link.yml
```

CSV columns are `realization` and numeric parameter columns. Add a numeric response column for `--response`.
The script compares sample standard deviations. The default collapse threshold is 0.1 of prior spread.
Alternatively, supply `--qc` with a nonempty QC JSON mapping. It reads collapse flags and failed QC findings.
Review that JSON: a file without a collapse finding is not proof of an accepted history match.
Use history-match-qc to check misfit, spread, and member completeness before this link.
Without prior spread or QC evidence, the script refuses the link.
Only a human decision can authorize `--override`. Report the reason and the unchecked evidence.

For runpaths use `--runpath 'case/realization-*/iter-1'` and `--qc`.
Use one explicit posterior iteration. `<N>` is also accepted for the realization wildcard.
Paths can come from `ahm_posterior_case` with `--contract`.
The script writes ids and install snippets. It does not copy parameter files or check their simulator content.
Supply `--template-source` for an install_templates snippet. Sources must contain `<REALIZATION_ID>`.
Use `--target` to set the installed file name. A CSV source needs an explicit data or template source.
With `--response`, select nearest members to numerical P10/P50/P90. Ties can give fewer than three ids.

## Report and done check

Give source paths, findings, exit codes, realization ids, cost assumptions, and unchecked items.
A passing YAML check does not prove that the simulator runs or that an optimum is useful.
Write reports to `everest_output_root` when set, otherwise to `output_root`.
Do not guess a site path. Ask for missing engineering output paths.
Done means the checks pass, warnings have a stated disposition, and the user can review the proposed diff.
Use `everest-diagnostics` for result tables. Use `ert-ensemble-run` for the run procedure.
