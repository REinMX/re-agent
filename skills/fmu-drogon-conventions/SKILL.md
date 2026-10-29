---
name: fmu-drogon-conventions
description: Find where a setting, a forward-model job, a workflow, an observation, or a parameter lives in an FMU repository that follows the fmu-drogon layout. Use when the user asks how the parts of an FMU repository connect, where to change something, or what a config line does. Read-only.
version: 0.1.0
---

# FMU repository conventions (fmu-drogon layout)

FMU repositories follow the layout of the public fmu-drogon repository.
Drogon is a synthetic field. Use it to learn the layout. Real projects differ in detail.
Always read the real files. Do not assume that a file exists.

## Step 1. Check the layout
Run `ls` in the repository root. Compare it with `references/drogon-map.md`.
Report each folder from the map that does not exist. A project may use another name.

## Step 2. Start from the ERT config
The `.ert` files in `ert/model/` are the entry points. Read one in this order:
1. `DEFINE` lines. They set the case name, the scratch path, and the simulator name.
2. `RUNPATH`, `ENSPATH`, `ECLBASE`, and `NUM_REALIZATIONS`.
3. `INCLUDE` lines. Follow each one. Most of the wiring is in `ert/input/config/`.
4. `INSTALL_JOB` lines (in `install_custom_jobs.ert`). Each one points to a job file in `ert/bin/jobs/`.
5. `HOOK_WORKFLOW` lines. The second word is the workflow. The third word is the hook point.
6. `GEN_KW`, `SURFACE`, and `GEN_DATA` lines. They define parameters and responses.
7. `FORWARD_MODEL` lines, in order. The order is the order of execution.

## Step 3. Trace one thing
Use the table in `references/drogon-map.md` to find the file. Then read it. Then answer.

| Question | Where to look first |
|---|---|
| Where is a parameter defined? | `GEN_KW` line, then the `.dist` file in `ert/input/distributions/` |
| Where does a parameter enter the simulator deck? | `RUN_TEMPLATE` lines and the `.tmpl` files |
| What does a forward-model step run? | `INSTALL_JOB` line, then the job file in `ert/bin/jobs/`, then the script in `ert/bin/scripts/` |
| What does a workflow do? | `LOAD_WORKFLOW` line, then the file in `ert/bin/workflows/` |
| Which observations does ERT use? | `OBS_CONFIG` line, then the files in `ert/input/observations/` |
| Which global variables exist? | `fmuconfig/input/global_master_config*.yml` and the generated `fmuconfig/output/global_variables.yml` |
| What does the simulator read? | `eclipse/model/*.DATA`, then `eclipse/include/` |

## Rules
- Scripts generate the files in `fmuconfig/output/`. Do not edit them. Edit the file in `fmuconfig/input/` instead, and give the user a diff.
- `<IENS>` is the realization number. `<ITER>` is the iteration. `<CONFIG_PATH>` is the folder of the `.ert` file.
- Paths in `FORWARD_MODEL` lines are often relative to `<CONFIG_PATH>`. Resolve them before you say that a file is missing.
- Case variants (`drogon_ahm.ert`, `drogon_pred_ref.ert`, `drogon_design.ert`) share most includes. Name the variant in your answer.
- A prediction config restarts from a history run. Find `RESTART_*` or `<RESTART_DIR>` in it.
- Do not edit RMS projects. Ask the user.
- Report each answer with the file path and the line number.
