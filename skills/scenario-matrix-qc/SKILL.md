---
name: scenario-matrix-qc
version: 0.1.0
description: Roll up scenario-qc results for many scenarios into one table. Use when the user ran several wells/constraints/SCHEDULE cases and needs a fail list. Analyst role. Does not start Flow or ERT.
---

# Scenario matrix QC

`scripts/scenario_matrix_qc.py` builds a CSV/JSON/MD matrix from
`<scenarios>/*/deliverables/scenario_qc/scenario_qc_report.json`.
The scenarios folder is contract `paths.scenarios`, else `<paths.root>/scenarios`.

Exit code 0 means no FAIL rows. Exit code 2 means at least one FAIL or missing report.

## Step 1. Run per-scenario QC
Use skill `scenario-qc` for each new scenario id (or pass `--run-qc` below).

## Step 2. Matrix
Use the `python` of the contract profile:
```bash
python skills/scenario-matrix-qc/scripts/scenario_matrix_qc.py --contract site.contract.yml --run-qc
```
Writes under `deliverables/scenario_qc/`:
- `scenario_matrix.csv`
- `scenario_matrix.json` (includes `headline_vector`)
- `scenario_matrix.md`

The relative change column is `headline_rel_delta` for contract `scenario_qc.headline_vector`
(default FOPT). Add `--include-baseline` to QC baseline vs itself (expect PASS).
Demo scenarios (`demo: true` in runmeta) are excluded from the gate when `scenario_qc.exclude_demo` is true.
`--include-demo` puts them back.

## Rules
- Do not invent scenario rows. Missing report is a fail.
- State `p10_p90` from the contract in the matrix header.
- Do not start simulator or ERT runs from this skill.
