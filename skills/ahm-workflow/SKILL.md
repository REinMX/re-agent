---
name: ahm-workflow
version: 0.1.0
description: Plan and review an assisted history match (AHM) with ERT, ES or ES-MDA, from the prior to a decision memo. Use when the user asks how to set up or improve a history match, whether to run another iteration, or what to change after an update. It sequences the checks of history-match-qc and ert-config-author-lint and gives the exact run command. Analyst role. The checks are read-only. A run goes through ert-ensemble-run when the user asks for it.
---

# Assisted history match workflow

You are the analyst (planner). It puts the checks of other skills in order, sets the decision gates, and writes a
decision memo. Its two scripts gate the run: `screen_params.py` and `ahm_preflight.py`. It starts a run only when the user asks, through `ert-ensemble-run`.
It does not edit a model, a config, a prior, or an observation file.

| Step | Skill | Tool |
|---|---|---|
| 1. Prior | `history-match-qc` | `prior_qc.py` |
| 2. Observations and weights | `history-match-qc` | `obs_qc.py` |
| 3. Config and update settings | `ert-config-author-lint` | `ert_config_map.py`, `check_ert_config.py --ahm-only` |
| 3b. Screening | `uncertainty-register`, `ahm-analysis`, this skill | one-by-one design, `misfit_report.py`, AHM_ANALYSIS, `screen_params.py` |
| 4. Run | `ert-ensemble-run` | `ahm_preflight.py` prints the command; it refuses without a screening record |
| 5. Update log | `history-match-qc` | `update_log_report.py` |
| 6. Misfit and collapse | `history-match-qc` | `misfit_report.py --update-log` |
| 7. Decision memo | this skill | the template below |

Read `site.contract.yml` first, as `reservoir-engineer` tells you. If the contract has an `ahm:` block, give each value
to the option that its comment names. If the contract has no value, use the default of the script.

## Step 0. Define done
Use `earned-autonomy`. Write the job contract in the answer: the config, the inputs, the outputs in `output_root`,
the checks, and the stop points. A first AHM workflow on a new case starts as a text dry run: give the commands, then wait.

## Step 1. Check the prior
```bash
python skills/history-match-qc/scripts/prior_qc.py <GEN_KW .dist files> --realizations <N> [--truth <file>]
```
Gate 1. Stop and tell the user when:
- the script has an error,
- a truth or analog value is outside the prior,
- N is not more than the number of sampled scalar parameters. This is a scalar-rank warning.
  It is not a stop for a localized FIELD or SURFACE update. Use `hm-localization` and `field_qc.py` for those.
Propose a change (a wider prior, LOGUNIF for permeability, more realizations). Do not make the change.

## Step 2. Check the observations and their weights
```bash
python skills/history-match-qc/scripts/obs_qc.py <OBS_CONFIG file> [--case <one simulated .DATA>]
```
Gate 2. An error stops the workflow. Report each weight-share warning and each zero-value warning with the
`PROPOSAL` text. The user decides on errors and weights. Do not edit the observation file or a copy of it.

## Step 3. Check the config and the update settings
```bash
python skills/ert-config-author-lint/scripts/ert_config_map.py <config>.ert
python skills/ert-config-author-lint/scripts/check_ert_config.py --ahm-only --weights <w1,w2,...> <config>.ert
```
Gate 3. An error stops the workflow. Report each review item: the ES-MDA weights that ERT uses, `ENKF_ALPHA`,
`MIN_REALIZATIONS`, `RANDOM_SEED`, and `UPDATE_LOG_PATH`. Run Step 2 of `ert-config-author-lint` (the load check)
when ERT is in the environment.
Also run `physics_checklist.py --deck <CASE>.DATA --ert-config <config>.ert` of `deck-scal-pvt-qc`. Report each warning.

## Step 3b. Screen the parameters
Run the one-by-one design (`make_design.py --mode onebyone`) and AHM_ANALYSIS on the prior. Then:
```bash
python skills/ahm-workflow/scripts/screen_params.py --design <design.xlsx> --residuals <misfit dir>/residuals.csv \
  --observations <expected observations>.csv --dist <GEN_KW .dist files> \
  --influence <ahm_influence dir>/influence.csv --out <output_root>/screening
python skills/ahm-workflow/scripts/ahm_preflight.py <config>.ert --screening <output_root>/screening/screening.json \
  --weights <w1,w2,...> --deck <CASE>.DATA
```
`--observations` is a CSV with the columns OBS, OBSERVED, ERROR, KEY, and DATE. Make it from the observation file of the config.
Do not make it from `residuals.csv`. The screening compares each residual row with it and refuses a missing or changed observation.
A DATE of `2019-09-30` and `2019-09-30T00:00:00` is the same calendar day. The screen accepts that pair. A different day is still a changed observation.
Without `--observations`, the record is a diagnostic only. `ahm_preflight.py` refuses it.

The screening refuses these inputs and writes no record:
- a member or an observation that is missing, or a duplicate row,
- a SIMULATED or ERROR value that is not finite, or an ERROR that is not positive,
- a design REAL that is not a unique whole number of zero or more.

The record (`ahm-screening/2`) holds the prior definitions and the hash of each input file.
`ahm_preflight.py` refuses a record when an input file changed or when a sampled prior changed. Rescreen then.
A comment, a different number format, a different file order, or a moved file does not change a prior.
A parameter that the screening dropped can stay uncertain. CONST is one possible decision, not the default. A record `ahm-screening/1` is not accepted. Make it again.

Report each dropped parameter with its reasons. A drop is a historical-sensitivity finding.
It is not proof that the parameter is irrelevant to the forecast. Ask the user to retain the uncertainty,
exclude it from the update, reparameterize, or fix it with a stated reason. Do not make that change.
An ES or ES-MDA run starts only from the `COMMAND` line of a `PREFLIGHT ready` result.
`ahm_preflight.py` checks that exact command. It refuses a negative, non-finite, or nonnumeric weight.
Weights whose sum(1/w) is not 1, such as 4,2,1, are valid. ERT rescales them to w * sum(1/w), and preflight shows those weights in an AHM-PRE-WEIGHTS note. Readiness does not change.
It checks the selected realizations. When the config has `OBS_CONFIG`, those summary observations must match the screening inventory.
A missing observation source also refuses the run. A config with no `OBS_CONFIG` is a compatibility check only.

## Step 4. Give the run command
Use the `ert-ensemble-run` procedure. Tell the user:
1. the exact command, for example
   `ert es_mda <config>.ert --weights <w1,w2,...> --realizations 0-<N-1> --disable-monitoring`
   or `ert ensemble_smoother <config>.ert --realizations 0-<N-1> --disable-monitoring`,
2. the number of realizations and the number of iterations,
3. the queue system, `RUNPATH`, and `ENSPATH`,
4. the open items from Gates 1 to 3.

If the user asked for the run, start it with `ert-ensemble-run`. Do not wait for a separate approval.
For a request such as "just rerun with 100 realizations", give the exact command with the changed realization count
and the changes that it needs (for example `NUM_REALIZATIONS` in the config, as a diff). Run it when the change is in place.

## Step 5. Read the update log
After the user reports that the run finished:
```bash
python skills/history-match-qc/scripts/update_log_report.py <UPDATE_LOG_PATH>
```
Report for each step: alpha, global scaling, the counts of active, outlier, and std_cutoff observations, and the
names of the deactivated observations. For ES-MDA, report the sum of 1/global scaling.

## Step 6. Check the misfit and the collapse
```bash
python skills/history-match-qc/scripts/misfit_report.py --obs <file>.obs --prior '<glob iter-0>' \
  --posterior '<glob iter-N>' --expected <N> --update-log <UPDATE_LOG_PATH> --outdir <output_root>/<name>
python skills/history-match-qc/scripts/prior_qc.py <GEN_KW .dist files> --posterior-runpaths '<glob iter-N>'
python skills/history-match-qc/scripts/compensation_check.py <GEN_KW .dist files> --prior-runpaths '<glob iter-0>' \
  --posterior-runpaths '<glob iter-N>' --seed <RANDOM_SEED> --expected <N> \
  --misfit-manifest <output_root>/<name>/manifest.json
```
For each seed run, give one `--prior-runpaths`, `--posterior-runpaths`, `--seed` and `--misfit-manifest`.
Without `--seed` and `--expected`, the result is diagnostic only. It is not a claim.
A `COMPENSATION` verdict of `compensating` or `implausible` blocks "Accept". Select "Add parameters" or
"Widen or change the prior" and name the EDGE parameters. Read the `COVERAGE` line. Quote the predictive coverage (nominal 0.90). On a synthetic case add `coverage.py
--truth-response`. The noise-biased share is low even for a correct posterior.
Gate 6. Read the `GATE` line first. If it is `weak` or `untrustworthy`, say so at the start of the answer.
Do not claim convergence. Report the `CHI2` line and each finding with its names and numbers.

## Step 7. Write the decision memo
Write the memo to `output_root`. Use this template. Select one or more options. Give the evidence for each option.
| Option | Evidence that supports it |
|---|---|
| Accept for the stated use | Compensation verdict plausible over 3 seeds, gate not weak, misfit inside the noise band, no observation group traded away, no parameter at a bound. |
| Revise observation errors or weights | A group below the share limit got worse, zero values with large errors, many deactivated observations. |
| Widen or change the prior | Truth outside the prior, posterior at a bound, observations out of reach. |
| Add parameters | Observations out of reach with a good prior coverage of the existing parameters. |
| More realizations | Gate `weak` or `untrustworthy`, N not more than the parameter count, strong collapse. |
| Change the update settings | ES-MDA weights that do not sum to 1, low `ENKF_ALPHA`, no localization with many parameters. |

The memo has: the inputs and commands, the gate levels, the numbers, the selected options, what you did not check,
and the next command (if any). Do not call the history match good or bad without numbers and limits.

## Rules
- Use the scripts of `history-match-qc` and `ert-config-author-lint`. Do not copy them or write new versions.
- Start a run only when the user asks for it, through `ert-ensemble-run` (Step 4). Show the exact command.
- Do not edit a prior, an observation file, or a config. Give a proposal or a diff.
- State the number of realizations behind each statistic.
