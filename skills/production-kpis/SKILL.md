---
name: production-kpis
version: 0.1.0
description: Compute production KPIs from SUMMARY (one case, a complete ensemble, ensemble-profiles output, or scenarios against a baseline). The KPIs are plateau length, recovery factor, voidage replacement ratio, gas sales against a target, the binding constraint per period per well and group, and an optional NPV when the inputs are given. Missing vectors are reported as missing and never as zero. A partial ensemble fails. Analyst role.
---

# Production KPIs

The script computes every number. Report only what it wrote. A KPI with status `missing` stays missing. Do not fill it in, estimate it, or show it as zero.

Ensemble statistics are numerical percentiles: P10 = 10th percentile (low), P50 = median, P90 = 90th percentile (high). They are not exceedance values. State this in every report.

## Step 1. Select the source
Use the `python` of the contract (resdata, pandas, numpy; opm for rate-vs-limit).

| Source | Arguments |
|---|---|
| One case | `--case <DATA or SUMMARY base>` |
| Ensemble from scratch | `--scratch <scratch>` (`--iter`, `--expected`); reads `<scratch>/realization-*/<iter>` with `runpath_lib`, fails when a realization is missing |
| ensemble-profiles output | `--profiles deliverables/profiles --start <START date>`; fails without `manifest.json`, or when it lists missing realizations or issues, or when its `realizations_used` differ from the REAL values. Rates are monthly means, so plateau lengths are coarser. |
| Scenarios against a baseline | `--scenarios-dir scenarios --baseline baseline --scenarios a,b`; reads `runmeta.yml` `summary_case` or `deck_data` (written by scenario-build) |

```bash
python skills/production-kpis/scripts/production_kpis.py --contract site.contract.yml --scratch <scratch> --iter 0 --expected 100
```
Options: `--in-place <region_in_place_long.csv>` (initial FIELD OIP/GIP from `region-in-place`), `--settings <yml>`, `--require plateau_days,oil_recovery_factor` (exit 2 if one is missing), `--out`.
Default output: `<paths.deliverables>/production_kpis`.

## Step 2. Read the outputs
- `kpis_per_case.csv`, `kpi_stats.csv` (P10/P50/P90; missing when any case lacks the KPI, with `missing_cases`), `periods.csv` (VRR and gas sales per period), `binding_constraints.csv`, `scenario_compare.csv`, `production_kpis.json`, `production_kpis.md`.
- Exit 0: written. Exit 2: bad input, incomplete ensemble, unreadable SUMMARY, or a `--require` KPI is missing.

## KPI definitions
- Plateau: the longest run where the rate is at least `fraction` x reference (`max` of the case, or `target`), with gaps up to `gap_days` bridged. `resolution: report` uses time-weighted means per report step so a single ministep cannot set the maximum.
- Recovery factor: FOPT / initial in-place. The in-place value comes from `--in-place`, else from FOIP/FGIP at the first SUMMARY step (noted as not at START), else missing. FGPT is gross gas.
- VRR: the script uses reservoir volumes whenever SUMMARY has them, FVIT/FVPT first, else FVIR/FVPR integrated over time. It reports VRR cumulative and per period. Without them, the script reports FWIT/(FOPT+FWPT) at surface conditions with a flag, and only when there is no gas production or injection. Otherwise VRR is missing. On fmu-flow-mini the surface ratio read 1.4 to 4.7 % higher than the reservoir ratio (median 3.3 %), so add the FV vectors.
- Gas sales: FGSR, else FGPR minus the `gas_sales.subtract` vectors (for example fuel and injection), else missing. The target comes only from settings; without a target the comparison KPIs are missing. Gas RF = FGPT (gross gas) / initial GIP.
- Binding constraint: method A reads WMCTL, GMCTP/GMCTW/GMCTG and FMCT* codes (codes from OPM opm-common). Method B compares simulated rates and BHP/THP with the deck limits (opm.io Schedule, 0 = not set) within `binding.rel_tol`, for wells only. Each period gets the mode active for the most days. The md states the method.
- NPV: only when `npv` gives `discount_rate`, `reference_date` and at least one price or cost. No default prices exist.

## Recommended SUMMARY vectors
Add these to the SUMMARY section (ECLIPSE100 and Flow keywords) so the KPIs do not fall back or go missing.
The report lists, per case, which of them are absent (`vector_coverage` in the JSON).

| Vectors | Used for |
|---|---|
| `FVPR`, `FVIR`, `FVPT`, `FVIT` | VRR at reservoir conditions (preferred over the flagged surface approximation) |
| `WVPR`, `WVIR` (optional) | per-well reservoir voidage; listed, not used by the KPIs yet |
| `FOIP`, `FGIP` | recovery factor without a region-in-place table (`RPTSOL FIP=2` gives the PRT initial for `region-in-place`) |
| `FGSR` | gas sales |
| `WMCTL`, `GMCTP`, `GMCTW`, `GMCTG` | binding constraint from control modes (wells, groups) |

```
FVPR
FVIR
FVPT
FVIT
FOIP
WMCTL
/
```

## Gas methods
Gas sales come from FGSR, else FGPR minus `gas_sales.subtract`. The target comes only from settings (otherwise missing). Gas RF uses gross FGPT. These methods are fixed in the script, not settings, so the report lists them under "Gas methods" (`gas_methods` in the JSON), not under "Assumptions to confirm".

## Settings
Order: built-in defaults, the contract `production_kpis` block, then `--settings`. Unknown keys are errors.
The md lists every default that the contract and `--settings` do not set under "Assumptions to confirm".
Defaults: `plateau.vector: FOPR`, `fraction: 0.9`, `reference: max`, `gap_days: 31`, `resolution: report`; `period: year`; `binding.rel_tol: 0.01`.

## Not checked
- The ECLIPSE100 control-mode codes in WMCTL/GMCT* are taken to equal the OPM codes. Confirm before using method A on ECLIPSE output.
- Group binding constraints need GMCT*/FMCT* vectors in SUMMARY.
