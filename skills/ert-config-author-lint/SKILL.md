---
name: ert-config-author-lint
description: Check, review, or change an ERT config (.ert file and its includes) before a run. Use when the user asks to review a config, find a broken include or hook, check that forward-model steps resolve, or edit a config. Analyst role. A change to a config that the user does not own is a diff.
version: 0.1.1
---

# Check and author ERT configs

A wrong config wastes a whole queue run. Check it first. Use two tools.

| Tool | Needs | Finds |
|---|---|---|
| `scripts/ert_config_map.py` | Python only | Settings, hooks, forward models in order, missing files, hooks without a workflow. |
| `scripts/check_ert_config.py` | ERT in the active environment | Whether ERT loads the config with all plugin steps and workflow jobs resolved. |
| `scripts/check_ert_config.py --ahm-only` | Python only | A review of the update settings for ES and ES-MDA. |

Do not use `ert lint` as a check. It exits with code 0 when the config has errors.
It also parses without plugins, so it reports plugin steps as missing.

## Step 1. Map the config
```bash
python skills/ert-config-author-lint/scripts/ert_config_map.py <config>.ert
```
The script follows each `INCLUDE`. It writes the key settings with file and line, the hooks, and the forward models in order.
Exit code 0 means no errors. Exit code 1 means errors. Exit code 2 means the file does not exist.
- **PROBLEM**: a referenced file does not exist, a hook names a workflow that nothing loads, or `NUM_REALIZATIONS` is missing.
- **WARNING**: a missing file under `fmuconfig/output/` when a `PRE_EXPERIMENT` hook exists.
  The hook probably creates the file. But ERT checks `RUN_TEMPLATE` files when it loads the config, before any hook runs.
  ERT stops until the file exists, so create it with the setup script of the project first (in Drogon, `fmuconfig/bin`).
  For other paths that a workflow creates, add `--generated <text>`.
- A reference with an unresolved variable is not an error. The script lists the count.

## Step 2. Load the config as ERT does
Activate the environment from `site.contract.yml`. Put its `bin` folder on `PATH`. Then run:
```bash
python skills/ert-config-author-lint/scripts/check_ert_config.py <config>.ert
```
Add `--verbose` to see which package supplies each forward-model step and workflow job.
Exit code 0 means ERT loads each config. Exit code 1 means a problem. Exit code 2 means ERT or a plugin did not load.
If the exit code is 2, read the message. A plugin step often needs its program on `PATH`.
If a step comes from a package that is not installed (for example semeio), the config cannot run in that environment.
Say which package is missing. Do not install it.
To accept a known warning, use `--accept 'text=reason'`. Give the reason to the user.

## Step 3. Review the values
Read the values from Step 1. Report each item that is wrong, with file and line.
1. `NUM_REALIZATIONS` is at least `MIN_REALIZATIONS`.
2. `RUNPATH` contains `<IENS>` and `<ITER>`, and it is inside `scratch_root`.
3. `ECLBASE` contains `<IENS>`.
4. `QUEUE_SYSTEM` and the queue options match the contract. `MAX_RUNTIME` is long enough for the model.
5. The simulator name and version in the `FORWARD_MODEL` line match the contract.
6. The order of forward models is correct. Template and copy steps come before the simulator. Exports come after it.
7. Each `HOOK_WORKFLOW` has a `LOAD_WORKFLOW`. The hook point is the one that the task needs.
8. A workflow argument that starts with `--` is in double quotes. ERT reads `--` outside quotes as a comment.
9. Each keyword exists in the ERT version of the active environment. If you are not sure, look it up in the ERT documentation. Do not guess a keyword.

## Step 3a. Review the AHM settings
Do this step for a history match (ES or ES-MDA).
```bash
python skills/ert-config-author-lint/scripts/check_ert_config.py --ahm-only [--weights 4,2,1] <config>.ert
```
Use `--ahm` instead of `--ahm-only` to also do the load check of Step 2.
Give `--weights` with the weights of the planned `es_mda` command. Without it, the script looks for a
comment with `--weights` in the config files.
- **error** (exit code 1): a weight that is not finite or is negative, `ENKF_ALPHA` or `STD_CUTOFF` not finite or not larger than 0,
  a `MIN_REALIZATIONS` value that ERT cannot read, or a missing config include or file reference.
- **review** (exit code 0): an item that the user must decide.
  - sum(1/w) is not 1. ERT 23 rescales the weights. The script gives the weights that ERT then uses.
  - `ENKF_ALPHA` below 2. More observations become outliers.
  - `NUM_REALIZATIONS` below `--min-realizations` (20). Compare it with the parameter count from `history-match-qc`.
  - `MIN_REALIZATIONS` is not set (ERT then needs all realizations), or it lets the update continue with fewer than 20.
  - `RANDOM_SEED` is not set. A repeated run gives another update.
  - `UPDATE_LOG_PATH` is not set. ERT 23 writes to `update_log` in the folder where ERT runs.
  - Adaptive localization with `LOCALIZATION_CORRELATION_THRESHOLD` below 2/sqrt(N), N = `NUM_REALIZATIONS`. Such a threshold keeps
    correlations that are sampling noise. ERT's default is 3/sqrt(N).
- **note**: the values and defaults that the script found, and each `ANALYSIS_SET_VAR` line (localization, auto scaling).
Do not change a value for the user. Report each review item with file and line.

For `ANALYSIS_SET_VAR PARAMETERS ... DISTANCE` and the `LOCALIZATION` blocks of the observations, use `hm-localization` (`loc_lint.py`).

## Step 4. Change a config
1. Copy the config to `output_root` or give a diff. Do not edit the original.
2. Make the smallest change. Keep the comment style of the file.
3. Do not change `RUNPATH` or `ENSPATH`. Do not raise `MAX_RUNNING` or `MAX_SUBMIT` to hide a problem.
4. Run Step 1 and Step 2 on the changed copy. Show the result.
5. Do not start a run from this skill. Use `ert-ensemble-run` when the user asks for a run.

## Report
1. The config and the environment that you used.
2. The result of both tools, with exit codes.
3. Each problem, with file, line, and the change you propose.
4. What you did not check. A passing load does not prove that the model runs.
