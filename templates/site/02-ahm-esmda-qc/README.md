# {{param:case_name}}: ES-MDA history match with QC

## What it is

An ES-MDA history match on the prior of `01-fmu-prior-design`, the QC chain that runs the skill scripts
before and after the run, and a decision memo that follows `skills/ahm-workflow/SKILL.md`.

- The ES-MDA weights are given already rescaled: sum(1/w) = 1. ERT 23 and 26 rescale other weights so that
  sum(1/w) = 1, and the update log then shows other weights than the plan.
- `gen_kw_update` and `field_update` set the update strategy per parameter type
  (`ANALYSIS_SET_VAR PARAMETERS <type> GLOBAL|ADAPTIVE|DISTANCE`). ERT 26 removed `STD_ENKF LOCALIZATION`.
  ADAPTIVE uses the correlation threshold 3/sqrt(N). DISTANCE works for FIELD and SURFACE only and needs
  `LOCALIZATION` blocks on the observations. Values now: GEN_KW
  `{{param:gen_kw_update}}`, FIELD `{{param:field_update}}`.
- The last ensemble is `iter-<number of weights>`.

## Files

| File | Use |
|---|---|
| `ert/ahm.ert` | ES-MDA config. Includes `{{param:prior_dir}}/ert/model.ert`. The `-- es_mda --weights` comment is what `check_ert_config.py` reads. |
| `qc.yml` | Inputs of the QC chain: config, prior, observations, runpaths, update log, weights, the screening record (`screening`), the declared `random_seed`, the optional members reason (`allow_members_reason`, null = not passed) and the optional held-out split (`heldout_split`, null = no held-out score). |
| `bin/qc_chain.py` | Runs the skill scripts in order and writes one file per step and `summary.json` (`exit_codes`, and `skipped` with the reason of each step that did not run). Read-only on the case. |
| `decision_memo.md` | Decision memo to fill after the post-run QC. |

Render `01-fmu-prior-design` first, into a sibling folder named `01-fmu-prior-design`, and run its
`bin/prior_design.py dist` and `bin/make_obs.py`. The paths `prior_dir` and `obs_config` are relative to this folder.

The screening record `output/{{param:case_name}}/screening/screening.json` comes from Step 3b of
`skills/ahm-workflow/SKILL.md` (`make_design.py --mode onebyone`, `screen_params.py`). The chain does not make it.
`ahm_preflight.py` runs in `setup` and refuses a run without it (`AHM-PRE-NO-SCREENING`): the step is recorded in
`ahm_preflight.txt` and the chain exits 1. Rescreen when the prior, the observations or a screened input changed.

## How to run at your site

Activate Komodo in a `{{site:activate_shell}}` shell:

```
{{site:activate}}
```

Queue: `{{site:queue.system}}`, queue `{{site:queue.name}}`, at most {{site:queue.max_running}} realizations at a time.
Runpaths go to `{{site:scratch_root}}/{{param:case_name}}`. The queue lines in `ert/ahm.ert` come from the site contract.
NUM_CPU above 1 means MPI for FLOW. Do not give FLOW a `--threads` argument.

```
{{site:ert.executable}} es_mda --weights {{param:esmda_weights}} --target-ensemble 'iter-%d' --disable-monitoring ert/ahm.ert
```

## Checks before you run

Run one prior realization first (for example `{{site:ert.executable}} test_run ert/ahm.ert`): `obs_qc.py --case`
needs one simulated case. Then:

```
{{site:python}} bin/qc_chain.py setup --out {{site:output_root}}/{{param:case_name}}/qc-setup
```

The chain runs these commands:

```
{{site:python}} {{param:agent_root}}/skills/history-match-qc/scripts/prior_qc.py {{param:prior_dir}}/ert/prior.dist --realizations {{param:num_realizations}}
{{site:python}} {{param:agent_root}}/skills/history-match-qc/scripts/obs_qc.py {{param:obs_config}} --case {{site:scratch_root}}/{{param:case_name}}/realization-0/iter-0/<ECLBASE>.DATA
{{site:python}} {{param:agent_root}}/skills/ert-config-author-lint/scripts/check_ert_config.py --ahm --weights {{param:esmda_weights}} ert/ahm.ert
{{site:python}} {{param:agent_root}}/skills/ahm-workflow/scripts/ahm_preflight.py ert/ahm.ert --screening output/{{param:case_name}}/screening/screening.json --weights {{param:esmda_weights}}
```

`ahm_preflight.py` gets `--allow-members <reason>` only when `allow_members_reason` is set in `qc.yml` (render it with
`--set allow_members_reason=<reason>`). The default {{param:num_realizations}} realizations are checked against
`ahm_defaults.MEMBERS` (100); a smaller run needs that reason, and the reason shows in `qc.yml` and in `ahm_preflight.txt`.
Start the ES-MDA only from the `COMMAND` line of a `PREFLIGHT ready` result.

After the run:

```
{{site:python}} bin/qc_chain.py post --out {{site:output_root}}/{{param:case_name}}/qc-post
```

The chain runs `update_log_report.py`, `misfit_report.py --update-log`, `coverage.py --ensemble posterior`,
`compensation_check.py`, `prior_qc.py --posterior-runpaths`, and `triage_runpath.py --iter <last>`. When `heldout_split`
is set it ends with `heldout_score.py --split <split.json> --out <out>/heldout`. When it is null the step is recorded
as skipped, with its reason, in `heldout_score.txt` and in `summary.json`.

```
{{site:python}} {{param:agent_root}}/skills/history-match-qc/scripts/coverage.py --residuals <out>/misfit/residuals.csv --ensemble posterior
{{site:python}} {{param:agent_root}}/skills/history-match-qc/scripts/compensation_check.py {{param:prior_dir}}/ert/prior.dist --prior-runpaths '{{site:scratch_root}}/{{param:case_name}}/realization-*/iter-0' --posterior-runpaths '{{site:scratch_root}}/{{param:case_name}}/realization-*/iter-<last>' --seed {{param:random_seed}} --expected {{param:num_realizations}} --misfit-manifest <out>/misfit/manifest.json
```

Read the `GATE` line of `misfit_report.txt` first. If it is `weak` or `untrustworthy`, do not claim convergence.
Quote the predictive coverage of `coverage.txt` (nominal 0.90), never the noise-biased share.
A `COMPENSATION` verdict of `compensating` or `implausible` blocks "Accept".

One seed (`random_seed` {{param:random_seed}}) is a diagnostic only: `compensation_check.py` reports one sample, not a
claim. A claim about the setup needs `ahm_defaults.MIN_SEEDS` (3 seeds): render the pack three times with different
`random_seed` values, run each, and give `compensation_check.py` one `--prior-runpaths`, `--posterior-runpaths`,
`--seed` and `--misfit-manifest` per run.

The held-out score (`heldout_score.py`) is retrospective split validation, not execution-time independence: current files
cannot prove that the held-out data stayed unused during the run, nor rule out tuning on it. It defines no pooled score
and no acceptance verdict.

Then fill `decision_memo.md`.

`ert lint` loads no plugins and always exits 0. `check_ert_config.py` is the gate.

## What the Drogon run showed

Drogon, ES-MDA weights 15, 7.5, 3.75, 1.875, N = 16, two designs. Sources: `docs/drogon-everest-report.md` section 3.

| | D1 no localization | D2 adaptive localization |
|---|---|---|
| Mean misfit, prior → posterior (truth run 0.88) | 45.8 → 2.22 | 45.8 → 17.6 |
| Parameters with sd ratio < 0.2 | 18 of 20 | 1 of 20 |
| Truth > 3 posterior sd away | 14 of 20 | 0 of 20 |
| Wall time, 80 Flow runs (5 ensembles x 16), MAX_RUNNING 3 | 6537 s | 4509 s |

- D1 is confident and wrong (biased in many parameters): OWC_FLANK 11.1 ± 0.35 against a truth of -4, compensated by porosity and krow.
  `misfit_report.py` (PR #17) flags CHI2 2.22 as above the noise band, and `prior_qc --posterior-runpaths` flags 4
  parameters at a prior bound.
- D2 barely updates: at N = 16, the adaptive-localization threshold is about 3/sqrt(N) = 0.75, and 7 parameters keep
  their prior sd.
- `update_log_report.py` shows the collapse early: the median response sd / error goes 0.655 → 0.279 → 0.202 → 0.157 in D1.
  Read it after iteration 2, not at the end.

Gate: do not take a posterior to Everest when `misfit_report` CHI2 is outside the noise band *and* sd ratios are below
0.2 for most parameters. Rerun with a larger N.
