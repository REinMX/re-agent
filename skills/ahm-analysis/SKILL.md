---
name: ahm-analysis
version: 0.1.0
description: Run the semeio AHM_ANALYSIS workflow on a copy of an ERT storage and read its KS table against a noise floor for the ensemble size. Use it after an ES or ES-MDA history match to see which observation groups move which scalar parameters, and to find spurious correlation (one group that drives unrelated parameters). Gives the floor, the top group per parameter, a leave-one-out table, and a spurious-correlation warning. Analyst role. Never writes to the original storage.
---

# AHM analysis

AHM_ANALYSIS (semeio) does one ES update from the prior for each observation group alone, for all groups,
and for all groups minus one. It gives the KS distance between the prior and each posterior for each scalar parameter.
A KS value is not an influence. This skill adds the noise floor and the spurious-correlation check.

## Step 1. Run AHM_ANALYSIS on a copy
Use the Komodo python. It must have ERT and semeio.
```bash
<python> skills/ahm-analysis/scripts/run_ahm_analysis.py --config <ert config> --prior iter-0 --out <new folder>
```
| Parameter | Use | Default |
|---|---|---|
| `--prior` | The name of the prior ensemble in the storage | required |
| `--group-by` | `data_key`: one group for each response key. `obs_key`: one group for each observation (slow) | `data_key` |
| `--mirror-up` | Folder levels above the config folder that the copy holds (for `<CONFIG_PATH>/../..` paths) | 1 |
| `--work-dir` | The parent of the temporary work folder | system temp |
| `--keep-copy` | Keep the work folder with the storage copy | off |

On ERT 26, `ensemble_experiment` names that ensemble `default`. The runpath folder is still `iter-0`. `--prior iter-0` then fails. Use `--prior default`. An ES or ES-MDA storage can still name the prior `iter-0`. Read the storage name. Do not read it from the folder.

- The script copies the config folder and the storage, and points ENSPATH, UPDATE_LOG_PATH and RUNPATH_FILE to the copy.
  It compares the original storage, update log and config folder before and after the run.
- An ERT 23 storage is also copied first. ERT 26 migrates the copy, not the original.
- The prior name is the second argument of the workflow line. `ert workflow --ensemble` does not reach the job.
- Exit 0: done. Exit 2: ERT or semeio is missing, an input is missing, the workflow failed, or the original changed.
- The output: `ks.csv`, `misfit_obs_info.csv`, `active_obs_info.csv`, `prior.csv`, one CSV for each group, `run.json` (wall time, ERT and semeio versions), `ert_workflow.log`.

## Step 2. Read the result against the noise floor
Standard library only.
```bash
python skills/ahm-analysis/scripts/ahm_influence.py --ahm-dir <Step 1 out> --out <new folder>
```
| Parameter | Use | Default |
|---|---|---|
| `--n` | The number of realizations in the prior | the rows of `prior.csv` |
| `--alpha` | The significance level of the floor | 0.05 |
| `--share` | The top-group share that fires the spurious-correlation signature | 0.4 |
| `--min-params` | The smallest number of parameters that the dominant group must lead | 3 |
| `--overview-json` | The `overview.json` of `ahm_overview.py`, for a cross-check of the top group family | none |

- The floor is the exact two-sample KS critical value for n = m = N. At N = 16 it is 0.5 (asymptotic 0.48).
- The script does not compute a shuffled-response floor. That needs one ES update for each shuffle.
- Exit 0: no signature. Exit 1: the spurious-correlation signature fires. Exit 2: an input error.
- The output: `influence.csv` (KS, above floor, top group, leave-one-out drop for each parameter and group), `influence.md`, `influence.json`.
- An unknown `--overview-json` schema gives "not compared". It does not stop the script.

## Rules
- Scalar parameters only. semeio skips FIELD parameters. For fields, use `ahm-ensemble-views` or `history-match-qc` (`field_qc.py`).
- The analysis is one ES step from the prior. It is not the ES-MDA chain.
- A KS below the independent-sample floor is a small reference value. It is not proof that the update is noise.
  The posterior members are transforms of the prior members. Do not report a below-floor value as an influence.
- One group that is the top group for many parameters is a dominance hypothesis, not proof of spurious correlation.
  Check whether those parameters share a physical driver. If they do not, write "possible spurious correlation, not established influence".
  Recommend localization (ERT 26 `ANALYSIS_SET_VAR PARAMETERS GEN_KW ADAPTIVE`, see `ert-config-author-lint`) or more realizations.
- Never run AHM_ANALYSIS on the original storage. Use Step 1.
