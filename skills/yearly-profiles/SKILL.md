---
name: yearly-profiles
version: 0.1.0
description: Make yearly production and injection profiles from an FMU/ERT ensemble for the field (FOPT, FGPT, FWPT, FWIT, and gas injection vectors that exist) and, with --by-well, for each well. Gives yearly volume, cumulative at year end, and average rate per realization, plus mean and P10/P50/P90. Use it when the user asks for yearly profiles or a profile table for a report. Missing vectors fail. Analyst role.
---

# Yearly profiles

Read SMSPEC/UNSMRY from scratch. Write only under `paths.deliverables/yearly_profiles/<label>`.
Do not start ERT or Flow. Do not write inside a runpath.

## Step 1. Contract
Run `stage-contract`. Use `paths.scratch`, `paths.deliverables`, and `p10_p90`.
The script uses the shared contract lookup and `--profile`.

## Step 2. Field profiles
Use the `python` of the contract profile (res2df, resdata, pandas):
```bash
python skills/yearly-profiles/scripts/yearly_profiles.py --contract site.contract.yml \
  --vectors FOPT,FGPT,FWPT,FWIT --label field
```
The default `--vectors` is contract `vectors.yearly_field_default`.
Gas injection: when contract `vectors.gas_injection` lists keywords, the script uses them and fails
(exit 2) if one is missing. Without the key, it probes FGIR, FGIT, FGIRL, FGIRT, FGIRH, and FGITH and
adds the vectors that exist. It writes the found and absent list in `manifest.json`.

## Prediction and restart runpaths
- `--iter pred_ref` (or any folder name) reads `realization-*/pred_ref`. `--iter 3` reads `iter-3`.
  The default is contract `runpath.default_iter`.
- A restart run with a reachable restart chain gives all years from START. Without the chain, the
  first year starts at the first prediction step and has `PARTIAL=True`. Years before it are not
  written. They are not zero.

## Step 3. Well profiles
```bash
python skills/yearly-profiles/scripts/yearly_profiles.py --contract site.contract.yml \
  --by-well --wells <WELL1>,<WELL2> --label wells
```
- The script uses well totals (WOPT, WWPT, WGPT, WWIT, WGIT) when SUMMARY has them.
- If SUMMARY has no well totals, the script stops with exit 2.
- With `--derive-from-rates`, the script integrates well rates. It labels the result `derived_from_rate`.
  It compares integrated field rates with field totals. It stops if the error is more than `--derive-tol`.
- `--well-vectors WOPR,WWPR,WWIR,WBHP,WWCT` adds year-end WBHP and WWCT.
- Tell the user that derived volumes are not SUMMARY totals. Recommend WOPT, WWPT, and WWIT in SUMMARY.

## Outputs
- `field_yearly_per_real.csv|parquet`: REAL, KEY, YEAR, PERIOD_END, PARTIAL, CUM_END, VOL, AVG_RATE, END_VALUE.
- `field_yearly_stats.csv`: N, MEAN, P10, P50, P90 for each KEY, YEAR, and METRIC.
- `field_yearly_mean_wide.csv`: one row for each year. Use it for a table in a report.
- The same three files with the prefix `well_` for `--by-well`.
- `qc_derivation.json` (derived only), `manifest.json`, and `README.md`.

## Project settings
- The deck name comes from contract `eclbase`. There is no default. `--eclbase` overrides it.
- The script needs the ERT `OK` file in each runpath. A runpath without it is still running or failed (exit 1).
- Below 10 realizations, P10/P50/P90 stay blank. `--min-n-percentiles` changes the limit.

## Year rule
A year starts on 1 January and stops on 1 January of the next year.
The first and last year stop at the start and end of the run. These years have `PARTIAL=True`.
Per-year P10 and P90 are statistics for each year. They are not one realization profile.

## Exit codes
- 0: pass.
- 1: a realization is missing. Use `--allow-partial` only if the user agrees.
- 2: a vector or a well is missing, or the derivation QC failed.

## Rules
- A missing vector is an error. Do not fill it with zero.
- Give N, the unit, the year rule, and the P10/P90 convention with each table.
- Scripts compute. Do not calculate volumes in the answer text.
