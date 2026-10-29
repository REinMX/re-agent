---
name: fmu-dataio-sumo
description: Wire, write, and check fmu-dataio exports and Sumo registration in an FMU project. Use when the user asks about case metadata, ExportData, share/results files, metadata sidecar files, FMU Settings, or reading results from Sumo. Analyst role. Ask the user before an upload to Sumo.
version: 0.1.0
---

# fmu-dataio and Sumo

fmu-dataio writes data with a metadata file. Sumo stores the data for the whole company.
The metadata follows the FMU data standard. A JSON schema defines the standard.
Details are in `references/dataio-cheatsheet.md`.

## Choose the task
| Task | Section |
|---|---|
| Is the case metadata workflow in the ERT config? | A |
| Write or review Python that exports data | B |
| Check the metadata files of a case | C |
| Register a case in Sumo, or read from Sumo | D |

## A. Check the case metadata wiring
1. Find `WF_CREATE_CASE_METADATA` in `ert/bin/workflows/`. Open the file that names it.
2. Find `LOAD_WORKFLOW` and `HOOK_WORKFLOW` for that file in the `.ert` config and its includes.
   The hook point must be `PRE_SIMULATION`.
3. The first argument of the job is the case path. It is usually `<SCRATCH>/<USER>/<CASE_DIR>`.
4. The option `--sumo` registers the case in Sumo. In an ERT file, write it in quotes: `"--sumo"`.
   Without quotes, ERT reads `--` as a comment.
5. After a run, the file `<casepath>/share/metadata/fmu_case.yml` must exist.
6. Report each item with the file path and the line number.

## B. Write or review export code
1. Prefer a simple export when one exists for the result. Simple exports run in RMS.
   Their names are in `references/dataio-cheatsheet.md`.
2. For other data, use `ExportData`. Set `config`, `name`, `content`, and `unit`. Set `workflow` when you know it.
3. Make a new `ExportData` object for each different export. Do not change an object after you create it.
4. Call `export(obj)`. It returns the path of the data file. Do not pass extra keyword arguments. fmu-dataio deprecates them.
5. Find out where the project keeps its configuration. fmu-dataio uses the nearest `.fmu/` folder (FMU Settings) first.
   If there is none, it reads the legacy `global_variables.yml` and warns that this will be deprecated.
   Tell the user which source the project uses. Do not copy master data into the script.
6. Do not hand-write a metadata file.
7. Write the code to a file in `output_root` or give the user a diff. Do not run an export into a case folder
   unless the user approves.

## C. Check the metadata files
```bash
python skills/fmu-dataio-sumo/scripts/check_dataio_metadata.py <folder> --require-fmu --casepath <case folder>
```
- Use a folder that holds the exported files, for example `<casepath>/realization-0/iter-0/share/results`.
- Use `--require-fmu` for output of an ERT run. Without it, a missing `fmu` block is only a warning.
  A missing `fmu` block means the export ran outside a case.
- Exit code 0 means no errors. Exit code 1 means errors. Exit code 2 means the script found no metadata file.
- The script checks structure, the case uuid, the data file, and the MD5 checksum.
  It does not check the JSON schema. Say this in your answer.
- Report each error with the file name. Do not edit the metadata files.

## D. Sumo
Registration and upload send data outside the scratch disk. Ask the user before each case.
1. Do not run anything with `--sumo` or any upload job until the user names the case.
   Read `sumo_environment` in the contract. If it is empty, ask. Do not guess the environment.
   Write one line with the case name, the environment, and the number of files, and then run.
2. To read from Sumo, use `fmu.sumo.explorer.Explorer`. It needs access through AccessIT.
   The first call can open a login form in a browser.
3. Never print, store, or write a token.
4. Start a search with `Explorer().cases`. Narrow it with `.filter(...)`.
   Use the properties `names`, `tagnames`, and `contents` to see which values exist.
5. Give the number of objects, the case name, the ensemble, and the realizations in your answer.

## Rules
- Master data (model, masterdata, access, stratigraphy) is now in FMU Settings.
  If a `global_master_config.yml` still has these sections, say so. Do not edit it.
- Do not call a metadata file valid. Write "the check found no errors".
