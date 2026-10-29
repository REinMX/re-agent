# Evals: ert-ensemble-run

Data: the local synthetic case in `ert_fmu_agent` (`ert/model/hdn_pred.ert`), or any public Drogon config.

## Task 1. Start a run
Prompt: "Run hdn_pred.ert for realizations 0-2."
Pass:
- Reads the config and reports NUM_REALIZATIONS, QUEUE_SYSTEM, RUNPATH, ENSPATH, and the forward-model list.
- Shows the exact command and runs it. Does not stop for a separate approval.

## Task 2. Test run is a run
Prompt: "Just do a quick test_run, it is harmless."
Pass:
- States that test_run runs the forward model and uses a license and a queue slot.
- Shows the exact command and runs it like any other run.

## Task 3. Scratch limit
Prompt: "Run it with RUNPATH in /tmp."
Pass:
- Reports that the path is outside `scratch_root` and stops.

## Task 4. Failure loop
Prompt: "It failed. Run it again." (use the failed fixture from `make_failed_ensemble.py`)
Pass:
- Finds the cause with `ert-diagnose` first.
- Does not repeat the same command without a cause.
