---
name: grid-property-qc
version: 0.1.0
description: Check the grid properties of one simulator case (EGRID and INIT) with resdata, and check that each COMPDAT connection is in an active cell with pore volume and permeability. Use it before a run, after a property or grid change, or when a well does not flow. Analyst role. Read-only. Numbers come from resdata, not from ResInsight.
---

# Grid property QC

## When to use
- Before a run, after a change to the grid, the properties, or the regions.
- When a well does not flow, or Flow says that a connection is ignored.
- `case-qc` runs both scripts when `BASE.EGRID` and `BASE.INIT` are next to `--deck`.

## Step 1. Run
Use the Python of the contract. It must have resdata, numpy, and res2df.
```bash
python skills/grid-property-qc/scripts/grid_qc.py --case <BASE> --outdir <output_root>/grid
python skills/grid-property-qc/scripts/completion_in_grid.py --data <BASE>.DATA --case <BASE> \
  --outdir <output_root>/completions
```
- `--outdir` must be empty and outside the case folder. Give `--force` only for a folder that you made.
- Limits: `--poro-max`, `--permz-fraction`, `--porv-ratio-min`, `--porv-ratio-max`, `--min-perm` (mD).
  Change a limit only when the user gives a reason. Write the new limit in the report.

## Step 2. Read the exit code
- 0: no error finding. Warnings and info can exist.
- 1: one or more error findings. Report each code, its count, and the sample cells.
- 2: an input is missing (EGRID, INIT, PORO, PORV, resdata, or a deck that res2df cannot read). Tell the user what is missing. Do not guess a path.

## Step 3. Explain each code
| Code | Severity | Meaning for the engineer |
|---|---|---|
| `GRID-NAN` | error | An INIT array has NaN or inf. A property include or a script is broken. |
| `GRID-ACTIVE-ZERO-PORV` | error | An active cell has no pore volume. Check MINPV, MULTPV, and the PORV edits. |
| `GRID-RANGE-PORO` | error | PORO is below 0 or above `--poro-max`. Check the units and the property model. |
| `GRID-RANGE-NTG` | error | NTG is outside 0 to 1. |
| `GRID-NEG-PERM` | error | A permeability is negative. |
| `GRID-PERMZ-GT-PERMX` | warning | PERMZ is above PERMX in many cells. This is usual only for fractures or special facies. Ask. |
| `GRID-REGION-ZERO` | error | FIPNUM, SATNUM, PVTNUM, or EQLNUM is 0 in an active cell. |
| `GRID-REGION-GAP` | info | A region number has no active cell. The tables for it are not used. This is often correct. |
| `GRID-PORV-VS-BULK` | warning | PORV is not bulk volume times PORO times NTG. MULTPV, a PORV edit, or a numerical aquifer can cause this. Report the quantiles. |
| `COMP-OUTSIDE` | error | A connection is outside the grid, or I, J, K is not known. |
| `COMP-INACTIVE` | error | A connection is in an inactive cell. Flow ignores it. The well can lose a zone. |
| `COMP-ZERO-PORV` | error | A connection is in a cell with no pore volume. |
| `COMP-ZERO-PERM` | error | A connection is in a cell with PERMX = 0 and PERMY = 0. |
| `COMP-LOW-PERM` | warning | sqrt(PERMX * PERMY) is below `--min-perm`. The connection gives almost no flow. |
| `COMP-WELL-NO-OPEN-CELL` | error | No connection of the well is in an active cell. The well cannot flow. |

A missing optional array (NTG, PERMZ, a region array) is a note in the report. It is not a finding.

## Rules
- Report numbers from `grid_qc.json` and `completion_in_grid.json` only. Do not read numbers from ResInsight.
- A finding is a fact about the files. It is not a verdict on the model. Say if a finding can be correct, for example `COMP-INACTIVE` in a pinched-out layer.
- Before you call a limit wrong, show the values: the quantiles, `per_layer.csv`, or `connections.csv`.
- Do not write in the case folder or the deck folder. Do not edit the deck.
