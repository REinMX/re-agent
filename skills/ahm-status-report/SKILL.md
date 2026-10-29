---
name: ahm-status-report
description: Make one plot-first status report of an ES or ES-MDA history match from an FMU scratch case, for a person to read. Use it when the user asks "how good is this history match", "is the posterior fit for use", or wants a status page of an AHM case. Works when only the last iteration is on disk (iter-0 deleted). Gives a verdict with evidence, plots, tables, P10/P50/P90 totals, a PDF, and status.json. Analyst role. Read-only on the case.
version: 0.1.0
---

# AHM status report

You are the analyst. The script reads the runpaths and writes a report. It does not change the case.
It does not call Sumo, LSF, or a network service.

The usual FMU case keeps only the last iteration (for example `iter-3`). ERT deleted `iter-0` to `iter-2`.
This is the main path: posterior-only mode. The report then says "iter-0 (prior) is gone".
It never makes a prior. When `iter-0` and other iterations are on disk, the report adds
the prior-to-posterior and the iteration-to-iteration comparisons.

## Step 1. Find the inputs
1. The case folder holds `realization-*/iter-*`. Use it for `--case`. Do not use the RUNPATH of the config.
   The config can point `<SCRATCH>` to another folder.
2. Find the `.ert` file. Give it with `--ert-config`. The script then reads `NUM_REALIZATIONS`, `ECLBASE`,
   `OBS_CONFIG`, the GEN_KW distribution files, and the ERT storage (`ENSPATH`): the stored iterations and the
   ES-MDA weights. It reads `GEN_KW NAME dist` and `GEN_KW NAME template output dist`, with or without options
   such as `INIT_FILES:` or `UPDATE:`. The distribution file can have any extension (for example `.txt`).
   A relative path is relative to the config file that has the GEN_KW line.
3. Give `--obs` and `--dist` when the config does not name the correct files.
   The config can include a different `.dist` file than the one you expect. A `.dist` parameter that is
   not in `parameters.txt` shows as "not sampled". It does not stop the report.

## Step 2. Run the report
```bash
python skills/ahm-status-report/scripts/ahm_status.py --case <scratch case> \
  --ert-config <file.ert> [--obs <file.obs>] [--dist <file.dist> ...] \
  [--eclbase 'eclipse/model/<NAME>-<IENS>'] [--expected N] [--wells P1,P2] [--title TEXT] \
  [--outdir <new local folder> | --write-share]
```
- The default output folder is `./ahm_status`. It is local.
- `--write-share` writes to `<case>/share/results/ahm_status` (FMU convention). Use it only when the user asks.
  Without `--write-share`, an `--outdir` inside the case is refused (exit code 2).
- Exit code: 0 = report written, 1 = input problem, 2 = usage error.
- Run time: about 5 seconds for each iteration with 50 small realizations.

## Step 3. Read the result
Start with `summary.md`. It is one page: the verdict, the headline plot, the 5 worst wells,
the realization count, and P10/P50/P90 totals at the end of history.
`report.md` and `report.pdf` have six sections. `status.json` is for an agent.

1. Case inventory. Iterations on disk and in the ERT storage. Realizations ok, failed, and missing.
   A realization is ok when its summary loads. A failed realization has a runpath but no summary that loads.
   The ERROR file gives the reason. A missing realization has no runpath.
2. Data match. The numbers come from `history-match-qc/scripts/misfit_report.py`: mean misfit, chi-square band,
   spread-aware reference, and predictive coverage P5-P95. The family table adds the response spread
   (ensemble sd / observation error) and the bias and variance hints of `residual_correlation.py`.
   The wells are ranked by the mean squared normalized residual over members and observations.
   Block observations (for example `BPR:7,8,4`) are not wells. The report does not rank them and does not
   show them in the heatmap. It gives them one summary line: "pilot pressure (block) observations".
   Realizations with a misfit above median + 3 * 1.4826 * MAD (Hampel rule) are named as outliers. They do not count toward the verdict.
3. Ensemble health. Parameter positions in the prior range (0 at min, 1 at max, log for LOGUNIF),
   from the `.dist` bounds. A parameter is at a bound when its mean is within 0.1 of a bound, or when
   30 % of the members are within 0.05 of a bound. With a prior, the table gives posterior sd / prior sd.
   The report says how many parameters it checked. When the case has parameters but no distribution
   file can be read, the report shows a WARN: "parameter-at-bound check could not run".
   The WARN is on page 1 and in `summary.md`. It does not count toward the verdict.
4. Physics and plausibility. Review items from the AHM lint (`check_ert_config.py --ahm-only`),
   the physics checklist, the chi-square band, `residual_correlation.py`, and `compensation_check.py` (only with a prior).
5. Forecast readiness. P10/P50/P90 of FOPT, FGPT, FWPT, and FWIT at the end of history, and at the last
   simulated date when it is later. The end of history is the last observation date.
   The UNIT column comes from the SMSPEC file. A vector with P10 = P90 is "history-controlled (zero spread)":
   the history controls set its value, not the history match.
6. Verdict: "Fit for use", "Use with caveats", or "Not ready", with the rules that fired.

Rules for each answer:
- Give the verdict with its evidence lines. Say that the verdict is advisory.
  It never passes DG0 or any decision gate. It makes no reserves or concept call.
- Say "P10/P50/P90 convention: numerical, P10 is the LOW value." with each P10/P50/P90 number.
- In posterior-only mode, say "iter-0 (prior) is gone". Do not describe what the update changed.
- A SUMMARY vector that the case does not have is "missing". Never report it as 0.
- Say which pressure mnemonic the report used. The order is WBP9, WBP4, WBP, WBHP.
  WBP9 is the 9-point well-block average reservoir pressure. WBP4 and WBP are fallbacks.
  WBHP is the flowing bottom-hole pressure. Do not call it the average reservoir pressure.
- When the observations have block pressures (BPR), the pressure page has one pilot-pressure panel for each block.
- Name the history vectors (*H) that are missing. The plots then show no black history line for them.
- Three checks fired as false alarms on a known-good drogon-lite setup (calibration 2026-10-28):
  the chi-square "above the noise band" while inside the spread-aware reference, the lint review of the
  member count, and compensation EDGE.
  The report shows them, marked "known false alarm", and they do not count toward the verdict.
  A chi-square above the spread-aware reference counts.
- Only the lint "NUM_REALIZATIONS below 100" is a known false alarm. The other `MIN_REALIZATIONS` and
  `NUM_REALIZATIONS` lint items are not calibrated. The report shows them as "lint, not calibrated".
  They do not count toward the verdict.
- A missing aquifer (PHYS-AQUIFER) and a missing well PI multiplier (PHYS-WELL-PI) are a design choice.
  The official Drogon has neither. The report shows them and does not count them.
- Page 1 and `summary.md` name the codes of the counted review items.
- A parameter at a prior bound (from the posterior and the `.dist` bounds, no prior needed) counts toward
  the verdict (PARAMETER-AT-BOUND, Use with caveats). It uses the same limits as compensation EDGE, but it
  caught the narrow-prior drogon-lite setup (B2) on calibration, so it stays counted (decision 2026-10-28).
- ES-MDA weights whose sum(1/w) is not 1, such as 4,2,1, are valid. ERT rescales them to w * sum(1/w).
  The report shows this as an INFO note, not as a review item.

## Verdict rules
The rules are one table in `scripts/ahm_status.py` (`RULES`).

| Verdict | A rule that fires |
|---|---|
| Not ready | less than 80 % of the expected realizations ok; coverage P5-P95 below 0.6; misfit above the spread-aware reference and coverage below 0.8; FOPT, FGPT, or FWPT missing |
| Use with caveats | a counted review item (the evidence names the codes); coverage outside 0.80-0.97; a parameter at a bound; a well above 4; posterior-only; less than 100 % realizations ok |
| Fit for use | no rule fires |

## Outputs
In the output folder: `summary.md`, `report.md`, `report.pdf`, `status.json`, `plots/*.png`,
`tables/*.csv` (inventory, well_misfit, family_misfit, forecast_pXX, parameters, findings, iteration_misfit),
and `misfit/` (the `misfit_report.py` folders). With a prior, `compensation/` has the `compensation_check.py` result.

## Limits
- The report uses only summary observations and GEN_DATA observations that `misfit_report.py` can read.
- The misfit uses only the ok members. The failed members are counted in section 1.
- One seed is one sample. The report does not compare seeds.
- Plots are evidence for a person. The tables and `status.json` hold the numbers.
