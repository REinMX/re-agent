---
name: ensemble-at-date
version: 0.1.0
description: Get ensemble values of SUMMARY vectors at one or more as-of dates (for example mean FOPT and FGPT at end-2045, at the last step, or at mid-run). Gives the value per realization, the mean, and optional P10/P50/P90 in the contract convention. Use it when the user asks for a cumulative or a rate at a date from an FMU/ERT scratch. Fails on a missing vector or a date outside the run. Analyst role.
---

# Ensemble at date

Read SMSPEC/UNSMRY from scratch. Write only under `paths.deliverables/at_date/<label>`.
Do not start ERT or Flow. Do not write inside a runpath.

## Step 1. Contract
Run `stage-contract`. Use `paths.scratch`, `paths.deliverables`, and `p10_p90`.
The script uses the shared contract lookup and `--profile`.

## Step 2. Run
Use the `python` of the contract profile (res2df, resdata, pandas):
```bash
python skills/ensemble-at-date/scripts/ensemble_at_date.py --contract site.contract.yml \
  --vectors FOPT,FGPT --as-of 2045-12-31 --percentiles --label end2045
```
Options:
- `--as-of` accepts `YYYY-MM-DD`, `last`, `mid`, or `first`. You can repeat it.
- `--vectors` accepts keys (`WOPR:<WELL>`) or families (`WOPR` gives all wells).
  The default is contract `vectors.at_date_default`, else FOPT,FGPT.
- `--reals 0-9` uses a subset. `--scratch <path>` uses another ensemble.
- `--iter 3` reads `realization-*/iter-3`. A folder name also works: `--iter pred_ref` reads the
  Drogon-style prediction runpaths `realization-*/pred_ref`. The default is contract `runpath.default_iter`.
- Restart runs (prediction that restarts from history): if the history SMSPEC/UNSMRY is reachable
  (restart chain), the values include the history. If it is not, dates before the first prediction
  step fail (exit 2). The script does not count the cumulative from zero at START.
- The deck name comes from contract `eclbase`. There is no default. `--eclbase` overrides it. A key of contract `report_dates` (for example `end_of_prediction`) is an `--as-of` token.
- The script needs the ERT `OK` file in each runpath. A runpath without it is still running or failed (exit 1).
- Below 10 realizations, P10/P50/P90 stay blank. `--min-n-percentiles` changes the limit.
- `--method auto|interp|exact`. Default `auto`: totals interpolate, rates use the step that contains the date.

## Outputs
- `per_real.csv` and `per_real.parquet`: REAL, AS_OF, KEY, VALUE, UNIT, METHOD.
- `summary.csv`: N, MEAN, STD, MIN, MAX, and P10/P50/P90 with `--percentiles`.
- `manifest.json` and `README.md`. The README states the P10/P90 convention.

## Exit codes
- 0: pass.
- 1: a realization is missing or does not read. Use `--allow-partial` only if the user agrees.
- 2: a vector is missing (see `missing_vectors.json`), or the date is outside the run.

## Rules
- A missing vector is an error. Do not fill it with zero. FGPT that is present with value 0 is not missing.
- Do not extrapolate. If the run ends before the date, say so. Give the last date of the run.
- Give N, the date, the unit, and the P10/P90 convention with each number.
- Scripts compute. Do not calculate volumes in the answer text.
