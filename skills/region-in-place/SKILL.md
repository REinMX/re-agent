---
name: region-in-place
version: 0.1.0
description: Oil, gas and water in place at surface (standard) conditions per FIP region and per date, from ECLIPSE 100 or OPM Flow summary files (FOIP/ROIP/RGIP/RWIP and FIP<xxx> sets such as ROIP_SEG). Writes an Excel workbook (one sheet per vector, rows = dates, columns = regions), initial vs any-date tables, group sums (segment, reservoir) from a mapping file, and a QC that regions add up to the field. Also generates the SUMMARY include. Use it for "remaining oil/gas per region", "STOIIP/GIIP per segment", "initial in place by zone", or when the user wants in-place tables for Excel. Analyst role.
---

# Region in place

Scripts compute every number. Do not add or subtract volumes in the answer text.
Read SMSPEC/UNSMRY (and the PRT for the initial row). Write only to `--out` or
`<contract paths.deliverables>/region_in_place/<label>`. Do not start a simulator or ERT here:
a re-run with new SUMMARY keywords is a separate step. Give the command and run it when the user asks.

## Words the user may use
"SFOIP", "STOIIP", "remaining oil", "oil in place at standard conditions" all mean **FOIP** (field) and
**ROIP** (region). There is no SFOIP keyword. FOIP at the first date is the STOIIP; GIIP is FGIP at the first date.

## Keywords (identical in ECLIPSE 100 and OPM Flow)
| Field | FIPNUM region | FIP<xxx> region (e.g. FIPSEG) | Meaning (surface conditions) |
|---|---|---|---|
| FOIP | ROIP | ROIP_SEG | oil, total = liquid + vaporised |
| FOIPL | ROIPL | ROIPLSEG * | oil in the liquid phase |
| FOIPG | ROIPG | ROIPGSEG * | oil vaporised in gas (VAPOIL only, else 0) |
| FGIP | RGIP | RGIP_SEG | gas, total = free + dissolved |
| FGIPL | RGIPL | RGIPLSEG * | gas dissolved in oil |
| FGIPG | RGIPG | RGIPGSEG * | free gas |
| FWIP | RWIP | RWIP_SEG | water |
| FPR | RPR | RPR__SEG | pressure (hydrocarbon pore-volume weighted), optional |

\* OPM Flow (checked on 2026.04) ignores the phase-split keywords for FIP<xxx> sets with
"Unhandled summary keyword". It writes them for FIPNUM. Use `--from-prt` for phase splits per FIP<xxx> set.

Units come from the SMSPEC: METRIC `SM3` for all three; FIELD `STB` (oil, water) and `MSCF` (gas).

**FIP<xxx> naming rule.** Pad `R<family>` to 5 characters with `_` and add the first 3 characters after
`FIP`: FIPSEG -> `ROIP_SEG`, `RGIP_SEG`, `ROIPLSEG`, `RPR__SEG`. Only those 3 characters identify the set,
so FIPUNIT and FIPUNIX cannot both be reported (the generator stops). Sources: OPM Flow manual 9.3 (FIP);
opm-common `SummaryConfig.cpp` (`"FIP" + keyword.substr(5, 3)`) and `FieldProps::canonical_fipreg_name`
(unique 6-character prefix, so `ROIP_UNI` finds FIPUNIT); ECLIPSE reference manual, SUMMARY region data.

**Deck requirements.**
- The region arrays must exist in REGIONS: `FIPNUM` and every `FIP<name>` (name up to 5 characters).
  If the array is missing, OPM Flow ignores the keyword with a warning; in ECLIPSE check the PRT for the error or warning.
- Dimensions: REGDIMS item 1 NTFIP >= the largest region id over all sets (or TABDIMS item 5).
  REGDIMS item 2 NMFIPR >= NTFIP + the number of FIP<xxx> sets. That value satisfies both the OPM
  manual and the "number of sets" reading. `make_fip_summary.py` warns if the deck is too small.
- An empty record `/` after a region keyword means all regions.
- Fallback source: `RPTSOL 'FIP=2' /` (SOLUTION) and `RPTSCHED 'FIP=2' /` (SCHEDULE) print field + FIPNUM
  balance reports in the PRT. `FIP=3` adds the FIP<xxx> sets. OPM Flow 2026.04 prints FIP=3 reports for FIP<xxx>
  sets, although the OPM manual still says it does not.

## Step 1. Contract
If a site contract exists, use it (`--contract`). It gives `eclbase`, `p10_p90`, `report_dates`,
`paths.scratch`, `paths.deliverables`. Without a contract, give `--case` and `--out`.

## Step 2. SUMMARY include (only if the vectors are missing)
```bash
python skills/region-in-place/scripts/make_fip_summary.py \
  --deck <RUNPATH>/<ECLBASE>.DATA --families all --with-pressure \
  -o <PROJECT>/eclipse/include/summary/fip_regions.summary --json fip_sets.json
```
It reads REGIONS (follows INCLUDE, EQUALS, COPY) and prints the sets, the largest region ids and REGDIMS advice.
Without a deck: `--region-sets FIPNUM,FIPSEG,FIPRES`. Tell the user which file to INCLUDE in SUMMARY and that
the case must be re-run. Give the re-run command.

## Step 3. Extract
```bash
python skills/region-in-place/scripts/region_in_place.py --contract <CONTRACT> \
  --runpath <RUNPATH> --families OIP,GIP,WIP --dates initial,<report_dates key>,last \
  [--groups <MAPPING>.yml] [--yearly] --label <LABEL>
python skills/region-in-place/scripts/region_in_place.py --case <RUN>/<DECK>.DATA --out <OUT>      # no contract
python skills/region-in-place/scripts/region_in_place.py --contract <CONTRACT> --scratch <SCRATCH> --iter 3   # ensemble
```
Options:
- `--families` OIP,GIP (default), any of OIP,GIP,WIP,OIPL,OIPG,GIPL,GIPG, or `all`.
- `--region-sets FIPNUM,FIPSEG` limits the sets (default: every set in the SMSPEC).
- `--dates`: `initial` (START), `first`, `last`, `YYYY-MM-DD`, or a contract `report_dates` key.
  A date between report steps uses the nearest step only within `--date-tol-days` (default 1), else exit 2.
  Values are states: never interpolated, never extrapolated past the run.
- `--yearly` keeps the 1 Jan report steps (+ initial) on the time axis.
- `--groups` mapping. CSV: `grouping,region_set,region,group` (region may be `1-3,7`). YAML:
  `{grouping: {FIPSZN: {SW_block: 1-3}}}`. Regions not mapped are listed in `groups_unmapped`, a region in two groups
  of one grouping or a region not in the run is an error. Groups are plain sums of region values.
- `--from-prt` reads everything from the PRT balance reports (all 7 families for every set) and cross-checks
  against the SMSPEC where both exist (`qc_prt_vs_summary.csv`).
- `--initial-from-prt auto|yes|no` (default auto: use `<DECK>.PRT` up to 500 MB).
- Ensemble: `--iter`, `--reals 0-9`, `--allow-partial` (only if the user agrees), `--min-n-percentiles` (10).

## Initial in place
OPM Flow writes no summary step at START (the first step is after the first timestep; in the lab 2025-01-02).
The `initial` row is then read from the PRT balance report at report 0 (`source = PRT`). Without that report,
`initial` is refused (exit 2) and `first` is the first report step. Do not present `first` as STOIIP without
saying its date. A prediction that restarts without its history chain has no initial row.

## Outputs
- `region_in_place.xlsx`: `README` (units, sources, QC, P10/P90 convention), `at_dates`, one sheet per vector
  (`OIP`, `GIP`, ...: rows = dates, columns = `FIELD`, `FIPNUM 1`, `FIPSEG 2`, ...), `groups_at_dates`, `groups`,
  `groups_unmapped`, `qc_sum_check`, `long` (if under the Excel row limit). Ensemble: `<vector>_mean` sheets and
  `at_dates` with n, mean, std, P10, P50, P90.
- `region_in_place_long.csv`: [realization,] date, source, region_set, region, vector, keyword, value, unit,
  first_date, change_since_first, fraction_of_first. `change_since_first` = value(t) - value(first date).
- `at_dates.csv` (single) or `at_dates_stats.csv` + `at_dates_per_real.csv` + `stats_all_dates.csv` (ensemble).
- `groups_long.csv`, `groups_at_dates.csv` / `groups_at_dates_stats.csv`, `groups_unmapped.csv`.
- `qc_sum_check.csv`, `qc_field_balance.csv` (info), `qc_prt_vs_summary.csv` (with `--from-prt`), `manifest.json`.
- `missing_vectors.json` on exit 2: the exact keywords and a SUMMARY snippet to add.

## QC
- **Regions add up to the field** for every set, vector and date (`--qc-rtol 1e-4`, `--qc-atol 1`).
  `FAIL_REGIONS_LOW` usually means cells with region 0 / not covered by the set, or a region list that is not
  complete. Exit 3, outputs written: explain before using them.
- **Field balance (info)**: FOIP drop vs FOPT, FGIP drop vs FGPT - FGIT, FWIP change vs FWIT - FWPT.
  Aquifer influx, VAPOIL or oil injection explain gaps. A gap of about one day of production means a wrong initial row.
- Empty regions (zero at all dates) are listed. They are real zeros from the simulator, not fills.

## Exit codes
0 ok. 1 a realization is missing, unfinished (no ERT `OK`) or unreadable. 2 missing vectors, bad date, bad input.
3 QC failed (outputs written).

## Rules
- Missing is never zero. A missing keyword stops the script; report the keywords from `missing_vectors.json`.
- Give the date, unit, source (SUMMARY or PRT) and, for ensembles, N and the P10/P90 convention with each number.
- **Recovery factor caveat.** The OIP drop of a region is not the oil produced from it when fluid crosses region
  boundaries. `1 - OIP(t)/OIP(t0)` is a recovery factor only for a closed region. In the synthetic lab case a fault
  block with no producer still loses 68% of its OIP (injected water pushes the oil across the fault). Quantify the
  flow with region-to-region keywords (`ROFT`, `RGFT`, `RWFT`, records `i j /`; OPM Flow supports them, also
  for FIP<xxx> sets) before calling a region drop "recovery".
- Restart runs: values are states, so a prediction-only run is right from its first step, but it has no initial row.

## Worked examples
The lab worked examples (synthetic field, OPM Flow 2026.04) live in the lab project, not in this skill:
`scenarios/fip_regions/README.md` and `deliverables/region_in_place/README.md` of that project.
