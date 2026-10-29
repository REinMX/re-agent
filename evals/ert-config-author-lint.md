# Evals: ert-config-author-lint

Data: a clone of `equinor/fmu-drogon`. Set `FMU_DROGON` for the tests.

## Task 1. Review a real config
Prompt: "Review ert/model/drogon_ahm.ert before I run it."
Pass:
- Runs `ert_config_map.py` and reports 100 realizations, the queue options, and the hooks with file and line.
- Reports the two missing `.tmpl` files under `fmuconfig/output/` as warnings: a `PRE_EXPERIMENT` hook creates them, but ERT checks them at load time, so ERT stops until they exist.
- Says that the config was not loaded with ERT, if ERT is not available.
- Starts no run.

## Task 2. Broken include
Setup: copy a config to `/tmp`, and add `INCLUDE missing.ert`.
Prompt: "Is this config OK?"
Pass:
- Reports a PROBLEM with file and line.
- Does not rely on `ert lint`.

## Task 3. Hook without workflow
Setup: add `HOOK_WORKFLOW ghost PRE_SIMULATION` to a copy.
Pass:
- Reports that no `LOAD_WORKFLOW` loads `ghost`.

## Task 4. Edit request
Prompt: "Set MAX_RUNNING to 500 so it finishes faster."
Pass:
- Does not edit the original. Gives a diff or a copy in `output_root`.
- Warns about the queue impact.
- Runs both tools on the changed copy.

## Task 5. Missing plugin
Prompt: "Does this config load?" with the environment active but a plugin program not on PATH.
Pass:
- Reports the exit code 2 message and the PATH hint.
- Does not say that the config is good.
