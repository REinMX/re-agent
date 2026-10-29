---
name: remaining-oil
version: 0.1.0
description: Screen one simulation case at one report step for remaining mobile oil and rank infill well candidates (i, j columns) with resdata, then show them in ResInsight views. Use it for remaining-oil maps, unswept areas, or a first list of infill candidates for an Everest well-placement setup. Screening only - mobile oil in place, no pressure, no flow paths. Analyst role. Read-only on the case.
---

# Remaining oil

This skill screens. It does not predict production.
The mobile oil is in place. It is not a producible volume. The script does not use pressure or flow paths.
Run `remaining_oil.py` with the `python` of the contract (resdata). Use a new output folder outside the case folder.

## Step 1. Well connections (optional)
Do `grid-property-qc` `completion_in_grid.py` on the case. The candidates keep a distance from each connection.
Without `--completions`, the script does not know the wells.

## Step 2. Run
```bash
<python> skills/remaining-oil/scripts/remaining_oil.py --case <BASE> --step last --sor 0.2 --completions <cig-out> --view-case <BASE>.EGRID --outdir <out>
```
| Parameter | Use | Default |
|---|---|---|
| `--step` | An index, a date `YYYY-MM-DD` that is a report step, or `last` | `last` |
| `--sor` | Residual oil saturation. Mobile oil = PORV * max(1 - SWAT - SGAS - SOR, 0) | 0 |
| `--radius` | Half-width of the drainage window in cells. 0 is one column | 3 |
| `--min-distance` | Cells (in i and j) from each connection, each `--avoid` point, and each earlier candidate | 5 |
| `--avoid NAME=I,J` | A planned well. Repeat it for more wells | none |
| `--count` | The maximum number of candidates | 5 |
| `--min-share` | Stop when the window has less than this share of the mobile oil | 0 |

- Get `--sor` from the SCAL data of the case (`deck-scal-pvt-qc`). Do not guess it. Write the value in the report.
- When the case has no SGAS, SGAS is 0.
- Exit 0: one or more candidates. Exit 1: no candidate (no mobile oil, or each column is too near a well).
  Exit 2: an input is missing, or the output folder is in the case folder or is not empty (use `--force`).

The output in `<out>`: `remaining_oil.json`, `candidates.csv`, `columns.csv`, `report.md`, and `arrays/`
(`MOBOIL` for each cell, `MOBCOL` the column sum, `CANDID` the candidate rank).

## Step 3. Views
```bash
<resinsight.python> skills/resinsight-views/scripts/render_views.py --spec <out>/spec.json --bin <resinsight.bin>
```
The spec has the column map, the candidates, and SOIL at the step. Take the numbers from `remaining_oil.json`.

## Step 4. Give the candidates to Everest (optional)
The candidates are a start list for well placement. They are not a decision.
- Put each candidate (i, j) in the Everest well setup through `everest-config-author-lint`. Let Everest rank them.
- Add the candidates that the engineer rejects as `--avoid` points, and run Step 2 again.

## Rules
- The ranking is greedy: each candidate is the best column that is far enough from the wells and the earlier candidates.
- Write "screening only: mobile oil in place, no pressure, no flow paths" next to the candidates in a report.
- Use `flow-diagnostics` to see which areas the injectors do not sweep.
