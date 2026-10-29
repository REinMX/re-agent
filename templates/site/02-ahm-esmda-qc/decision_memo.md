# Decision memo: {{param:case_name}}

Follows Step 7 of `skills/ahm-workflow/SKILL.md`. Fill every field from the QC output. Leave no number without
its source file. Do not call the history match good or bad without numbers and limits.

## Inputs and commands

| Item | Value |
|---|---|
| Config | `ert/ahm.ert` |
| Prior | `{{param:prior_dir}}/ert/prior.dist` |
| Observations | `{{param:obs_config}}` |
| ES-MDA weights (sum(1/w) = 1) | {{param:esmda_weights}} |
| Update strategy | GEN_KW {{param:gen_kw_update}}, FIELD {{param:field_update}} |
| Realizations (NUM / MIN) | {{param:num_realizations}} / {{param:min_realizations}} |
| Runpaths | `{{site:scratch_root}}/{{param:case_name}}` |
| Run command | |
| QC output folders (setup, post) | |
| Screening record | `output/{{param:case_name}}/screening/screening.json` (`ahm-screening/2`, from `screen_params.py`) |
| Random seed (`RANDOM_SEED`) | {{param:random_seed}} (see Compensation for the number of seeds) |

## Gate levels

| Gate | Source | Level and open items |
|---|---|---|
| 1. Prior | `prior_qc.txt` | |
| 2. Observations and weights | `obs_qc.txt` | |
| 3. Config and update settings | `check_ert_config.txt` | |
| 3b. Preflight (screening record, members, prior unchanged) | `ahm_preflight.txt`: `PREFLIGHT ready` or the `AHM-PRE-*` codes | |
| 5. Update log | `update_log_report.txt` | |
| 6. Misfit and collapse (`GATE` line) | `misfit_report.txt`, `misfit/manifest.json` | |
| Failed realizations | `triage_runpath.txt` | |

Preflight: write the result line, and for each `AHM-PRE-*` code the action taken. If `allow_members_reason` is set in
`qc.yml`, copy the reason here: `ahm_preflight.txt` shows it as `allowed: <reason>`.

## Numbers

State the number of realizations behind each statistic.

| Quantity | Prior | Posterior | Source |
|---|---|---|---|
| Mean misfit | | | `misfit/real_misfit.csv` |
| CHI2 line | | | `misfit_report.txt` |
| Predictive coverage (nominal 0.90). Quote predictive, never the noise-biased share | | | `coverage.txt` |
| Active / outlier / std_cutoff observations | | | `update_log_report.txt` |
| Sum of 1/global scaling | | | `update_log_report.txt` |
| Parameters at a bound | | | `prior_posterior.txt` |
| Realizations that finished | | | `triage_runpath.txt` |

## Compensation

| Seed | Verdict (`plausible`, `compensating`, `implausible`) | EDGE parameters | Source |
|---|---|---|---|
| {{param:random_seed}} | | | `compensation_check.txt` |

One seed is a diagnostic only: the report is one sample, not a claim. A claim needs `ahm_defaults.MIN_SEEDS` (3) seeds,
each with its own `--prior-runpaths`, `--posterior-runpaths`, `--seed` and `--misfit-manifest`. Seeds run: 1 of 3.
`compensating` or `implausible` blocks "Accept".

## Held-out score

Run only when `heldout_split` is set in `qc.yml`. If it is null, `heldout_score.txt` and `summary.json` (`skipped`) say so;
write "not run" here.

| Quantity | Value | Source |
|---|---|---|
| Split | | `heldout_split` of `qc.yml` |
| Scores per observation kind and unit (no pooled score) | | `heldout/heldout.json`, `heldout_score.txt` |

Limit: this is a retrospective split validation; current files cannot prove the held-out data stayed unused during the run.

## Options

Select one or more. Give the evidence for each selected option.

| Selected | Option | Evidence |
|---|---|---|
| [ ] | Accept for the stated use | |
| [ ] | Revise observation errors or weights | |
| [ ] | Widen or change the prior | |
| [ ] | Add parameters | |
| [ ] | More realizations | |
| [ ] | Change the update settings | |

## Not checked

## Next command
