# fmu-drogon map

Source: the `equinor/fmu-drogon` repository, read on 2026-10-03.
The repository uses Git LFS. A plain clone needs a restore workflow before it is complete.
RMS content is an archive. Do not edit it.

## Folders
| Folder | Content |
|---|---|
| `ert/model/` | ERT config files. One file for each case variant. `0readme` lists them. |
| `ert/input/config/` | Included ERT snippets: custom jobs, folders, tables export, maps export, RFT, SIM2SEIS, GEOSEIS, plotting. |
| `ert/input/distributions/` | `.dist` files for `GEN_KW`, and design matrices as `.xlsx`. |
| `ert/input/observations/` | Observation files. |
| `ert/input/templates/` | `.tmpl` files that ERT fills with parameter values. |
| `ert/bin/jobs/` | Job files. Names start with `FM_` (forward model) or `WF_` (workflow). |
| `ert/bin/scripts/` | The scripts that the job files run. |
| `ert/bin/workflows/` | Workflow files. Names starting with `xhook_` run at a hook point. |
| `fmuconfig/input/` | Master config and shared inputs: dates, relperm, horizons, zones, seismic. |
| `fmuconfig/output/` | Generated global variables. Do not edit. |
| `fmuconfig/bin/` | Shell scripts that update the generated files. |
| `eclipse/model/` | Simulator `.DATA` files. |
| `eclipse/include/` | Deck includes, one folder for each section: grid, props, runspec, schedule, solution, summary. |
| `eclipse/include_pred/` | Schedule includes for prediction cases. |
| `rms/` | RMS project, scripts, and inputs. |
| `sim2seis/` | Petro-elastic model and seismic forward model. |
| `ecalc/` | Energy and emission calculation model and inputs. |
| `share/` | Shared outputs: `webviz`, `resinsight`, `external`. |
| `.fmu/` | FMU settings: `config.json` and `mappings.json`. A recent commit moved the FMU config from YAML includes to this folder. |

## Case variants in `ert/model/`
| File | Purpose |
|---|---|
| `drogon_ahm.ert` | Header says: Assisted History Match set-up. |
| `drogon_design.ert` | Header says: Sensitivity set-up. |
| `drogon_pred_ref.ert` | Header says: Prediction case - simple. It restarts from a history iteration. |
| `drogon_pred_op5.ert` | Header says: Prediction case with planned well. |
| `drogon_dst.ert` | The header gives no purpose. Read the file before you describe it. |
| `drogon_exercise_*.ert` | Course exercises. |

## Jobs and workflows named in `install_custom_jobs.ert`
Forward models: `TRACER_BREAKTHROUGH`, `XSECT_WELLS`, `SYMLINK_HIST_RESULT_FILES`, `FM_MERGE_PARAMETERS`, `ECALC`.
Workflows: `echo_config_file`, `run_fmuconfig`, `run_fmuconfig_pred`, `run_fmuconfig_rate`, `wf_fmuobs`, and the `xhook_` workflows for case metadata.

## Order of events in `drogon_ahm.ert`
1. Workflows with hook point `PRE_EXPERIMENT` run before the experiment starts: `run_fmuconfig`, `run_fmuconfig_rate`, `wf_fmuobs`.
2. `GEN_KW` draws parameter values. ERT does this before any pre-simulation workflow.
3. Workflows with hook point `PRE_SIMULATION` run: `echo_config_file`, `xhook_create_case_metadata`.
4. `RUN_TEMPLATE` lines fill the templates in the runpath.
5. `FORWARD_MODEL` lines run in file order: copy files, RMS, `ECLCOMPRESS`, `FLOW`, symlinks, then exports and quality control.

Check this order in the file you read. A project can differ.

## Files that a fresh clone lacks
- `fmuconfig/output/global_variables.yml.tmpl` and `rate_scaling.yml.tmpl` are `RUN_TEMPLATE` sources in the AHM, design, and DST configs.
  A `PRE_EXPERIMENT` hook creates them, but ERT checks them when it loads the config. A fresh clone cannot load these configs
  in ERT until the files exist.
- `DROGON_HIST.DATA` includes 17 files that RMS exports (grid, properties, regions, schedule). They are not in the repository.
- The restored bundle `share/external/24.3.0_external.tar.gz` is a Git LFS file (3.2 GB). A plain clone holds only a pointer.
- The OPM parser cannot read `DROGON_HIST.DATA` because of the keyword `IMPORT`.

## Tutorial configs
`ert/model/tutorial_examples/*.ert` use paths such as `../input/config/...`. These resolve from `ert/model/`.
Copy a tutorial config to `ert/model/` before you use it. In place, each one shows many missing files.
All 15 have no missing file when they sit in `ert/model/`.

## Prediction cases
`drogon_pred_ref.ert` sets `RESTART_DIR`, `RESTART_CASE`, and `RESTART_REPORT`.
It also sets the environment variable `RESTART_FROM_PATH`. fmu-dataio uses this variable.
