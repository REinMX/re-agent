---
name: flow-diagnostics
version: 0.1.0
description: Find which injector supports which producer, and how uneven the sweep is, from ResInsight flow diagnostics on one simulation case with flux arrays (RPTRST BASIC=2 FLORES). Gives injector fractions and residence time as GRDECL arrays, an inferred injector share for each producer, an F-Phi curve, and the Lorenz coefficient. Use it for injector-producer pairs, water injection support, or sweep heterogeneity. Analyst role. Read-only on the case.
---

# Flow diagnostics

ResInsight computes the flow diagnostics. resdata gives the pore volume and the connection cells.
Run `fd_preflight.py` and `fd_metrics.py` with the `python` of the contract (resdata).
Run `fd_export.py` with `resinsight.python` (rips). Do `resinsight-session` Step 2 first.
Use a new output folder outside the case folder for each script.

## Step 0. Get a case with fluxes
ResInsight needs the inter-cell fluxes in the restart file. Without them, it gives no result.
The deck keyword is:
```
RPTRST
 BASIC=2 FLORES /
```
`FLOWS` also gives the fluxes. The fluxes make the UNRST file about 29% larger (Egg: 36.7 MB to 47.4 MB).
- Ask the engineer before you add the keyword to a deck.
- Rerun one realization only, into a new folder. Do not add the keyword to an ensemble run.
- Show the exact Flow command and run it through `flow-preflight` when the user asks.

## Step 1. Preflight
```bash
<python> skills/flow-diagnostics/scripts/fd_preflight.py --case <BASE> --step last --out <out>/preflight.json
```
- `--step` is an index, a date `YYYY-MM-DD` that is a report step, or `last`.
- The script finds the injectors (WWIR or WGIR above 0) and the producers (WOPR, WWPR or WGPR above 0)
  from the summary at the step. Give `--injectors` and `--producers` (comma-separated) to override.
- Exit 0: the step has fluxes. Exit 2: no flux arrays, a step without fluxes, or a missing file.
  With no flux arrays, the message gives the keyword and the expected UNRST size. Do Step 0.
- Step 0 of a run has no flux arrays. The first step after it can have zero fluxes. Use a later step.

## Step 2. Export the ResInsight results
```bash
<resinsight.python> skills/flow-diagnostics/scripts/fd_export.py --preflight <out>/preflight.json --bin <resinsight.bin> --expect-version <resinsight.version> --outdir <out>
```
It writes in `<out>/fd`:
- `FRAC<n>.grdecl`: the fraction of the cell flow that comes from injector n.
- `FRACP<n>.grdecl`: the fraction of the cell flow that goes to producer n.
- `RESTIME.grdecl`: the residence time in days, for all wells.
- `<out>/fd_export.json` maps each keyword to its well. `<out>/spec.json` is a ViewSpec for Step 4.

Exit 1: one or more exports failed. Read `failed` in `fd_export.json`. Do not report a missing array.
Exit 2: an input, rips, or ResInsight is missing.

## Step 3. Metrics
```bash
<python> skills/flow-diagnostics/scripts/fd_metrics.py --fd-dir <out> --completions <cig-out> --outdir <out>/metrics
```
`--completions` is the output folder of `grid-property-qc` `completion_in_grid.py`. Without it, there is no allocation.
- `fphi.csv` and the Lorenz coefficient: 0 is a uniform sweep. Near 1, a small part of the pore volume
  carries the flow.
- `allocation.csv`: for each producer, the injector shares in its open connection cells.
- `report.md` has the two results. Exit 2: an input is missing or has the wrong cell count.

## Step 4. Views (optional)
```bash
<resinsight.python> skills/resinsight-views/scripts/render_views.py --spec <out>/spec.json --bin <resinsight.bin>
```
The spec has one drainage view for each producer and one sweep view for each of the first injectors.

## What comes from where
| Result | Source |
|---|---|
| `FRAC*`, `FRACP*`, `RESTIME` arrays | ResInsight flow diagnostics |
| F-Phi and the Lorenz coefficient | `fd_metrics.py` from ResInsight `RESTIME` and resdata PORV |
| Injector share for each producer | Inferred: the PORV-weighted mean of `FRAC*` in the connection cells |

- The injector share is not the ResInsight well allocation plot. That plot uses the connection fluxes.
  Write "inferred" next to each share in a report.
- ResInsight does not export F-Phi in console mode (the file has only a header). Do not use that file.
- The flow diagnostics results are not available through `active_cell_property`. `fd_export.py` exports them
  from a view as GRDECL.
- Flow diagnostics show one report step. They do not predict. Give the date with each result.
