---
name: hm-localization
version: 0.1.0
description: Set up and tune ERT 26 distance localization for FIELD and SURFACE updates in a history match. Writes the well coordinates (res2df and xtgeo), adds LOCALIZATION blocks to the observation file, checks the config, and sweeps the radius by a replay of one ES update from the stored prior, with no simulation. Use it when an ES or ES-MDA update collapses a field, or before a first distance-localized run. Analyst role. Read-only on the storage.
---

# HM localization

ERT 26 updates a FIELD or SURFACE parameter with `DISTANCE` only from the observations that have a location.
Observations without a location drop out of that update. GEN_KW parameters cannot use `DISTANCE`.
Run the scripts with the Komodo `python` (ERT 26, res2df, xtgeo). Put the bin folder of that Python on PATH.

## Step 1. Well coordinates
```bash
<python> skills/hm-localization/scripts/obs_locations.py --data <DECK>.DATA --grid <DECK>.EGRID --out <out>/locations.csv
```
- Source `compdat` is the mean x, y of the OPEN connection cells. Source `welspecs` is the well head cell.
- Exit 0: written. Exit 1: written, and a well has no OPEN connection. Exit 2: an input or a package is missing.

## Step 2. Localize the observations
```bash
<python> skills/hm-localization/scripts/localize_obs.py <obs file> --locations <out>/locations.csv --radius <R> --out <new obs file>
```
| Parameter | Use | Default |
|---|---|---|
| `--radius` | RADIUS in metres for each located observation, or `auto` (1.25 times the median nearest-well spacing, inferred from drogon-lite; sweep it in Step 4) | required |
| `--source` | `compdat` or `welspecs` rows of the CSV | `compdat` |
| `--out` or `--inplace` | A new file, or overwrite the input | required |

- Well keys (`W*:WELL`) get a location. Field (`F*`), group (`G*`) and other keys do not. The script lists them.
- A bulk `SUMMARY` block gets one `WELL <name> { LOCALIZATION {...}; };` entry for each well of its VALUES csv.
- ERT's own parser reads the output before the script writes it.
- Exit 0: all observations located. Exit 1: written, and some have no location. Exit 2: an error. Nothing is written.

## Step 3. Check the config
```bash
<python> skills/hm-localization/scripts/loc_lint.py <config>.ert
```
It reports the strategy of each parameter, the located and unlocated observations, and the radius against
the 3000 m default, the field extent and the cell size. Exit 0: clean. Exit 1: an error or a warning. Exit 2: ERT does not load.

## Step 4. Sweep the radius
```bash
<python> skills/hm-localization/scripts/radius_sweep.py --storage <ENSPATH> --config <config>.ert --prior iter-0 --radii 50,100,200,400,800,inf --field PERMX --out <out>/sweep
```
- The script copies the storage and opens the copy only. It runs no simulation.
- `inf` is the GLOBAL update with all observations.
- Add `--truth-field <grdecl>` on a synthetic case. The sweep then gives the truth coverage and the RMSE.
- The output is `sweep.csv` and `sweep.md`. Write "one ES step from the prior, not the ES-MDA chain" next to the numbers.
- Drop the radii with a collapse share above `--max-collapse` (default 0.05). Keep the top 1 or 2.
- Exit 0: written. Exit 2: an input error.

## Step 5. Run ES-MDA with the chosen radius
Do Step 2 again with the chosen radius. Then start the run through `ert-ensemble-run` when the user asks:
```bash
ert es_mda --weights <w1,w2,...> --target-ensemble 'iter-%d' <config>.ert
```
Compare the misfit and the field statistics of the top radii with `history-match-qc` and `ahm-ensemble-views`.

## Rules
- Use `DISTANCE` only for FIELD and SURFACE parameters. Use GLOBAL or ADAPTIVE for GEN_KW.
- Observations without a location drop out of a `DISTANCE` update. Report their count and their key families.
- The radius is a modelling choice. Sweep it, and write the radius in the decision memo.
- A GEN_KW-only case (for example Drogon) needs FIELD parameters before distance localization can help.
- Do not edit the observation file of a project in place without the user's approval. Write a new file.
