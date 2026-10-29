---
name: qa-qc-preread
version: 0.1.0
description: Assemble a QA/QC pre-read pack from existing FMU deliverables (profiles, P10/P50/P90 bands in the contract convention, scenario QC matrix, ResInsight/evidence plot paths). Mandatory Open issues section. Use before gate-packet or when the user asks for a gate pre-read for a stage in contract gate_stages. Analyst role. Does not start Flow or ERT. Does not invent numbers.
---

# QA/QC pre-read

Build a pre-read under `deliverables/gate_packets/<stage>/` from artifacts that already exist.
Scripts cite CSV/JSON only. Do not invent volumes.

P10/P90: state the contract `p10_p90` convention in every pack.

## When to use
- Before a gate packet for a stage in contract `gate_stages`.
- When the user asks for a QA/QC slide outline or pre-read from the current ensemble.
- After `ensemble-bands`, `scenario-matrix-qc`, and `resinsight-plots`.

## Step 1. Contract
Run `stage-contract`. Stage names come only from contract `gate_stages`.
The default stage is `gate_default_stage`, else the only stage. An unknown stage is an error (exit 2).

## Step 2. Assemble
Use the `python` of the contract profile:
```bash
python skills/qa-qc-preread/scripts/qa_qc_preread.py --contract site.contract.yml --stage <STAGE>
```

Writes:
```
deliverables/gate_packets/<STAGE>/
  preread.md
  preread_outline.json
  preread.html
  README.md
```

## Contents (fixed sections)
1. Contract summary and the P10/P90 statement
2. Band highlights from `bands_end_of_sim.csv` (quoted numbers only): contract `vectors.headline`,
   then `WOPR:<wells.plot_default>`
3. Scenario QC matrix status and fail flags
4. Plot path list (evidence only)
5. Open issues (mandatory): missing `required_summary` vectors, present-vector bands mode,
   scenario FAIL rows, missing required artifacts

## Exit codes
| Code | Meaning |
|---|---|
| 0 | Pack written (open issues may be listed) |
| 2 | Critical inputs missing, unknown stage, or bad contract |

## Rules
- Do not start ERT, Everest, Flow, or deletes.
- Do not write inside scratch.
- Missing SUMMARY vectors are open issues, never zeros.
- Use `gate-packet` after this when you need strict vs draft gate status.
