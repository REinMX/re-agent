---
name: ensemble-profiles
version: 0.1.0
description: Extract field and well summary time series from an FMU Flow/ERT scratch ensemble with res2df. Write CSV and parquet under deliverables/profiles. Use after stage-contract when you need profiles from existing realizations. Does not invent zeros for missing SUMMARY vectors. Analyst role.
---

# Ensemble profiles

Extract SUMMARY time series from scratch. Write only under `paths.deliverables/profiles`.
Do not start ERT or Flow. Do not write inside a runpath.

## Step 1. Contract
Run `stage-contract`. The script uses the same contract lookup (`--contract`, env
`RE_AGENT_CONTRACT`, `./site.contract.yml`, user file) and `--profile`.
Use `paths.scratch` and `paths.deliverables`. Percentiles are not computed here.
State the contract `p10_p90` convention; `ensemble-bands` uses it later.

## Step 2. Extract
Use the `python` of the contract profile (res2df, pandas):
```bash
python skills/ensemble-profiles/scripts/ensemble_profiles.py --contract site.contract.yml \
  --time-index monthly
```
- The deck name comes from contract `eclbase` (for example `eclipse/model/<DECK>`). There is no default.
  `--eclbase` overrides it.
- `--iter 3` reads `realization-*/iter-3`. `--iter pred_ref` reads a prediction folder.
  The default is contract `runpath.default_iter`.
- `--expected` defaults to contract `expected_realizations`. Without both, the script uses the number found and warns.
- `--vectors FOPT,FWPT,WOPR` limits keys. The default extracts all SMSPEC keys.

## Outputs
Under `deliverables/profiles/`:
- `profiles_long.csv` / `.parquet`: REAL, DATE, KEY, VALUE
- `field_profiles.*` / `well_profiles.*`
- `manifest.json`: vectors extracted, iteration, profile, `missing_vs_contract` (informational)
- `README.md`: states the P10/P90 convention for later bands

## Missing SUMMARY vectors
This skill lists gaps against `vectors.required_summary` in the manifest. It does not fill zero.
The hard fail is in `ensemble-bands`.

## Rules
- Scripts compute. Do not invent rates or volumes in prose.
- Fail closed on missing realizations unless `--allow-partial`.
- Do not delete scratch. Do not launch ERT/Flow from this skill.
