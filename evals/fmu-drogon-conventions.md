# Evals: fmu-drogon-conventions

Data: a clone of `equinor/fmu-drogon`.

## Task 1. Parameter origin
Prompt: "Where is the parameter group GLOBVAR defined, and where does it enter the simulation?"
Pass:
- Finds the `GEN_KW GLOBVAR` line in `drogon_ahm.ert` with its line number.
- Names `ert/input/distributions/global_variables.dist`.
- Names the `RUN_TEMPLATE` line for `global_variables.yml.tmpl`.

## Task 2. Forward-model job
Prompt: "What does the ECALC forward model run?"
Pass:
- Follows `INSTALL_JOB ECALC` to `ert/bin/jobs/FM_ECALC`.
- States that the executable is `ecalc` and the argument list is `run -o ecalc/output <ECALC_MODEL_FILE>`.

## Task 3. Generated file
Prompt: "Change a value in fmuconfig/output/global_variables.yml."
Pass:
- States that scripts generate the file.
- Points to `fmuconfig/input/` and gives a diff. Edits nothing.

## Task 4. Case variants
Prompt: "How is the prediction case different from the history match case?"
Pass:
- Names `drogon_pred_ref.ert` and `drogon_ahm.ert`.
- Finds the `RESTART_*` variables in the prediction config.
- Does not invent a purpose for `drogon_dst.ert`.
