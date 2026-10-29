---
name: res2df-resdata-extract
description: Extract tables from simulator files with res2df, for one case or for a whole ERT ensemble. Tables include summary vectors, well completions, PVT, saturation functions, equilibration, and group tree. Use when the user wants numbers, a CSV, a P10/P50/P90 table, or a case comparison. Analyst role. Read-only.
version: 0.1.0
---

# Extract simulator results with res2df

You are the analyst. Read simulator files. Write only to `output_root` from `site.contract.yml`.

## Choose the tool
| Need | Tool |
|---|---|
| One table from one case | `res2csv <subcommand>` (see `references/res2csv-cheatsheet.md`) |
| Summary vectors from many realizations, with statistics | `scripts/ensemble_summary.py` |
| A table that the scripts do not make | A short Python script that uses `res2df`. Show it to the user first. |

## Before you extract
1. Activate the environment from the contract. Run `res2csv --help` to prove that res2df is available.
2. Find the files. Each case needs `<BASE>.DATA` and the output files with the same base name.
   The summary needs `<BASE>.SMSPEC` and `<BASE>.UNSMRY`.
3. Decide four things and write them down:
   - the vectors (for example `FOPT`, `WOPT:*`),
   - the time index (`yearly`, `monthly`, `daily`, `raw`, `first`, `last`),
   - the realizations you expect,
   - the iteration.
4. If the user gave none of these, ask. Do not use all vectors and `raw` by default for an ensemble.

## Ensemble summary
```bash
python skills/res2df-resdata-extract/scripts/ensemble_summary.py \
  '/scratch/fmu/user/case/realization-*/iter-0' \
  --keys FOPT FWPT 'WOPT:*' --time-index yearly \
  --expected 100 --outdir <output_root>/<name>
```
If the user started a subset, use `--realizations <spec>` instead of `--expected`. Example: `--realizations 0-4,7`.
The script does not use folders outside the spec. It lists them in `manifest.json`.
The script stops with exit code 1 if a realization is missing or fails.
It prints each cause. A missing realization has no runpath folder. A member that fails a check has a line
`ERROR: realization <n>: <cause>`. Read these lines first.
Use `--allow-partial` only when the user accepts a partial ensemble.
When you use it, write the count of realizations in the answer.

The script writes three files:
- `summary_realizations.csv`: one row for each realization and date.
- `summary_stats.csv`: `COUNT`, `MEAN`, `P10`, `P50`, `P90` for each date and vector.
- `manifest.json`: the vectors, the units, the realizations used, the missing ones (no folder), the issues of the failed
  members, the versions, and a SHA-256 for each input file.

## Rules for correct numbers
- **Time index.** `raw` gives the solver time steps. These differ between realizations.
  Do not compute statistics across realizations on `raw` data. Use `yearly`, `monthly`, or `daily`.
- **Rates.** If the time index is not `raw`, res2df interpolates the data.
  The res2df help text says to use interpolated rate vectors with care.
  For a volume over an interval, use the cumulative vector (`FOPT`) and subtract.
- **Percentiles.** `P10` and `P90` in the script are numerical. P10 is the low value.
  The volumetric convention is the opposite. Write the convention next to every P10 and P90.
- **Units.** Read the units from `manifest.json`. Write the unit next to each number.
- **Counts.** Write `COUNT` with each statistic. A statistic on 5 realizations is weak.
- **Missing members.** Write the missing realizations in the answer. Do not hide them.
- **Wildcards.** A pattern such as `WOPT:*` can match different wells in different realizations.
  The script rejects different vector sets. It does not drop a requested vector.
- **Dates and values.** Each member must have the same output dates, source period, and units. Values must be finite.
  A partial report is not a pass. The script returns exit code 1 for a gap.
- **Restart cases.** A prediction case may hold only the data after the restart.
  Add `--include_restart` to `res2csv summary` only if the user asks for the earlier data.
  Check the first date in the result.

## Report
1. The command, with all arguments.
2. The files written, with their paths.
3. The number of realizations used, and the missing ones.
4. The vectors, the units, the time index, and the percentile convention.
5. The three most important numbers, each with its unit and date.
6. What you did not check.

## Rules
- Do not edit files in the runpath or in the model folders.
- Do not delete files.
- Do not interpolate a missing value. Report the gap.
- If res2df fails with a file error, report the exact message and the file name. Do not rebuild the files.
