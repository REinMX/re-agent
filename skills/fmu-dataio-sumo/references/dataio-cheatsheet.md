# fmu-dataio cheat sheet

Source: `equinor/fmu-dataio` (commit ee50e8b, 2026-08-14), `equinor/fmu-sumo` (commit deb1462, 2026-08-06),
and `equinor/fmu-drogon`. Tested with fmu-dataio 2.30.0.

## Files
For data `top_volantis--depth.gri`, the metadata file is `.top_volantis--depth.gri.yml`.
It is in the same folder. The leading dot hides it from a normal `ls`. Use `ls -a`.

## Folders
| Folder | Content |
|---|---|
| `share/results/` | Results of the workflow. |
| `share/preprocessed/` | Data prepared before the ensemble runs. |
| `share/observations/` | Observation data. |
| `share/metadata/fmu_case.yml` | Case metadata, under the case path. |

## Case metadata
The workflow job `WF_CREATE_CASE_METADATA` comes with Komodo. Drogon uses it in `xhook_create_case_metadata`.
```
WF_CREATE_CASE_METADATA  <SCRATCH>/<USER>/<CASE_DIR>
WF_CREATE_CASE_METADATA  <SCRATCH>/<USER>/<CASE_DIR>  "--sumo"
```
Optional arguments: `--sumo` registers the case in Sumo. `--verbosity` sets the log level.
The ERT config loads the workflow and hooks it:
```
LOAD_WORKFLOW   ../../bin/workflows/xhook_create_case_metadata
HOOK_WORKFLOW   xhook_create_case_metadata  PRE_SIMULATION
```
In Drogon, `LOAD_WORKFLOW` is in `install_custom_jobs.ert` and `HOOK_WORKFLOW` is in the main config.

## ExportData
```python
from fmu.config import utilities as ut
from fmu.dataio import ExportData

CFG = ut.yaml_load("../../fmuconfig/output/global_variables.yml")  # legacy source
path = ExportData(config=CFG, name="topx", content="depth", unit="m").export(surface)
```
fmu-dataio looks for the nearest `.fmu/` folder (FMU Settings) first. It uses `global_variables.yml` only when it finds no `.fmu/` folder.
In that case it warns that reading `global_variables.yml` will be deprecated. It also warns when both sources exist.
The code above shows the legacy form from the fmu-dataio examples. Check which source the project uses before you copy it.
| Field | Meaning |
|---|---|
| `config` | The global configuration. |
| `name` | Name of the data. |
| `content` | What the data is, for example `depth` or `property`. Some contents need more fields. |
| `unit` | Unit of the data. |
| `tagname` | Extra label. It separates two exports with the same name. |
| `workflow` | Text that names the workflow, for example `rms structural model`. |
| `geometry` | Path of an exported grid. A grid property uses it to link to its grid. |
| `is_observation`, `is_prediction` | Flags for the type of data. Default for prediction is true. |
| `timedata` | Dates of the data. |
| `casepath` | Path of the case. fmu-dataio detects it when it is not set. |

Supported objects and default formats: `xtgeo.RegularSurface` (`.gri`), `xtgeo.Polygons` and `xtgeo.Points` (`.csv`),
`xtgeo.Cube` (`.segy`), `xtgeo.Grid` and `xtgeo.GridProperty` (`.roff`), Pandas DataFrame (`.csv`),
PyArrow table (`.parquet`), and Python dictionary (`.json`).

An `ExportData` object does not change after creation. Make a new object for each different export.

## Simple exports (standard results)
They run in RMS and check their input. Module: `fmu.dataio.export.rms`.
`field_outline`, `fluid_contact_outlines`, `fluid_contact_surfaces`, `grid_extracted_depth_surfaces`,
`grid_model_static`, `inplace_volumes`, `simulator_fipregions_mapping`, `structure_depth_fault_lines`,
`structure_depth_fault_surfaces`, `structure_depth_isochores`, `structure_depth_surfaces`, `structure_time_surfaces`.
Example: `structure_depth_surfaces` writes `share/results/maps/structure_depth_surface/<surface>.gri`.

## Metadata keys
Top-level keys that a data file has: `$schema`, `class`, `access`, `masterdata`, `source`, `version`,
`data`, `file`, `fmu`, `tracklog`, `display`.
- `data.name`, `data.content`: the identity of the data.
- `file.relative_path`, `file.checksum_md5`: where the file is and its MD5 checksum.
- `fmu.case.uuid`, `fmu.case.name`: the case. Every file of one case has the same uuid.
- `fmu.realization`, `fmu.iteration`, `fmu.ensemble`: present for ERT runs.

A file that fmu-dataio exports outside an FMU case has no `fmu` block.
The checker script reports this.

## FMU Settings
FMU Settings holds the master data. These sections leave `global_master_config.yml`: `model`, `masterdata`, `access`, `stratigraphy`.
These files in `fmuconfig/input` are then not used: `_masterdata.yml`, `_access.yml`, `_stratigraphy.yml`.
The Drogon repository has a `.fmu/` folder with `config.json` and `mappings.json`.

## Sumo Explorer
```python
from fmu.sumo.explorer import Explorer

sumo = Explorer()  # a login form can open in a browser
cases = sumo.cases.filter(name="MyCase")  # search context. Filters: uuid, name, status, user, asset, field
for case in cases:  # a search context is iterable
    print(case.uuid, case.name, case.status)
    surfaces = case.surfaces  # shortcut for .filter(cls="surface")
```
Shortcuts: `cases`, `surfaces`, `tables`, `cubes`, `polygons`, `dictionaries`.
Properties of a search context: `names`, `tagnames`, `dataformats`, `aggregations`, `stages`,
`vertical_domains`, `contents`, `columns`, `statuses`, `users`.
Search contexts: `Explorer`, `Case`, `Ensemble`, `Realization`. `get_object(uuid)` returns one object. Run `help(sumo.filter)` for all filter arguments.
Apply for access through AccessIT. Do not store a token in a file.
