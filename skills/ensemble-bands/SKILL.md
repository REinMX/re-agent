---
name: ensemble-bands
version: 0.1.0
description: Compute P10/P50/P90 ensemble bands from profiles in the site.contract.yml convention (numerical or exceedance). Fail non-zero when required SUMMARY vectors are missing. Never fill missing vectors with zero. Use after ensemble-profiles for QC or gate work. Analyst role.
---

# Ensemble bands

Load profiles and the contract. Compute P10/P50/P90 in the contract `p10_p90` convention:
- `numerical`: 10th / 50th / 90th percentile of the ensemble (low = P10, high = P90).
- `exceedance`: P10 is the high case (10 % chance to exceed), P90 is the low case.

State the convention in every report.

## Step 1. Contract and profiles
Run `stage-contract`, then `ensemble-profiles`, so `deliverables/profiles/profiles_long.*` exists.

## Step 2a. Full contract check
Use the `python` of the contract profile (pandas, numpy):
```bash
python skills/ensemble-bands/scripts/ensemble_bands.py --contract site.contract.yml
```
Exit 2 if any `vectors.required_summary` name is absent.
Writes `deliverables/bands/missing_vectors.json`. Does not invent zeros.

## Step 2b. Present vectors only
```bash
python skills/ensemble-bands/scripts/ensemble_bands.py --contract site.contract.yml \
  --vectors FOPT,FOPR,FWPT,WOPR,WBHP
```
Exit 0 when those families exist. Tell the user that the full contract check is not green.

## Approach
1. End-of-sim table (`bands_end_of_sim.csv`): last DATE per REAL per KEY, then percentiles.
2. Time bands (`bands_time_series.csv`): percentiles at each DATE (from monthly profiles).
3. The console preview shows the contract `vectors.headline` keys.

A well family in the contract (`WOPR`) matches keys like `WOPR:<WELL>`.
Parquet copies are written when a parquet engine is installed. The CSV is the deliverable.

## Rules
- Missing vector = error. Never fill with zero.
- Do not start ERT/Flow. Do not write inside scratch.
- Add missing vectors to the SUMMARY section of the deck before the full-contract path can pass.
