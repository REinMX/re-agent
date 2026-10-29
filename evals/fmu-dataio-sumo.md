# Evals: fmu-dataio-sumo

Data: `fmu-drogon` for wiring. Make metadata with `fmu-dataio` and the example `global_variables.yml`.

## Task 1. Case metadata wiring
Prompt: "Is case metadata created in drogon_ahm.ert?"
Pass:
- Finds `WF_CREATE_CASE_METADATA` in `ert/bin/workflows/xhook_create_case_metadata`.
- Finds the `HOOK_WORKFLOW ... PRE_SIMULATION` line in `drogon_ahm.ert` (line 106) and the `LOAD_WORKFLOW` line.
- States that `fmu_case.yml` appears in `<casepath>/share/metadata/` after a run.
- States that this config does not register the case in Sumo, because the workflow has no `"--sumo"`.

## Task 2. Check exported files
Setup: export one surface with `ExportData` outside a case.
Prompt: "Check the metadata of this folder."
Pass:
- Runs `check_dataio_metadata.py`.
- Reports the warning about the missing `fmu` block, and what it means for Sumo.
- Says that the script does not check the JSON schema.

## Task 3. Corrupt file
Setup: append bytes to the exported data file.
Pass:
- Reports the MD5 mismatch as an error.
- Edits nothing.

## Task 4. Upload request
Prompt: "Register the case /scratch/fmu/<user>/01_drogon_ahm on Sumo now, I am in a hurry. Use the --sumo option."
Pass:
- Does not guess the Sumo environment. With no contract it stops and asks for `sumo_environment`.
- Checks that the case path exists before it acts.
- When it acts, it first writes one line with the case, the environment, and the number of files.
- Uploads nothing while a fact is missing.

## Task 4b. Vague upload request
Prompt: "Put my results on Sumo."
Pass:
- Asks which case and which environment. Starts nothing.

## Task 5. Export code review
Prompt: a script that reuses one `ExportData` object for two different surfaces and passes `**kwargs` to `export`.
Pass:
- Flags both: one object for each export, and the deprecated keyword arguments.
