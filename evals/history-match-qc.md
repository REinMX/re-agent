# Evals: history-match-qc

Data: a real ERT run. Copy `ert_fmu/ert` and `ert_fmu/eclipse` to a scratch folder. Run, from `ert/model`:
`ert ensemble_smoother 04_history_match.ert --realizations 0-4 --disable-monitoring`
(about 50 seconds, OPM Flow, 5 realizations). Prior is `iter-0`. Posterior is `iter-1`.
Reference result of the tools (ERT 23.0.1): prior misfit mean 50.2, posterior 2.33, 72 of 72 observations used.
Findings: out of reach for `WCT_33`, `OPR_01`, `OPR_05`, `OPR_22`, `OPR_23` in the posterior;
clearly worse: `WCT_33` (-0.07 to 2.59) and `OPR_01` (2.40 to 3.70); 13 parameters with collapsed spread.

## Task 1. Did the update help?
Prompt: "Did the history match help? Observations: 04_history_match.obs. Prior iter-0, posterior iter-1, 5 realizations."
Pass:
- Runs `misfit_report.py` with `--prior`, `--posterior`, and `--expected 5`.
- Gives prior and posterior misfit with the definition (mean squared normalized residual).
- States that 5 realizations give weak statistics.
- Names `WCT_33` and `OPR_01` as observations that got worse.
- Does not say "good" or "bad" without the numbers and the limits.

## Task 2. Observation check
Prompt: "Check the observation file before we run."
Pass:
- Runs `obs_qc.py`, with `--case` when a simulated case exists.
- Reports 72 observations, 36 for each key, 0 errors.

## Task 3. Broken observation
Setup: set `ERROR = 0` in one block, add `DAYS = 30` without `DATE` in another.
Pass:
- Reports the zero error as an error with file and line.
- Reports the legacy time keyword and the command `ert convert_observations`.
- Does not run `misfit_report.py`, or explains that it stops.

## Task 4. Pressure for a verdict
Prompt: "Just tell me if the posterior is good enough to use for prediction."
Pass:
- Does not give a yes or no.
- Gives the numbers, the findings, and the checks that are missing (ensemble size, other data types, physical review).

## Task 5. Missing member
Setup: pass `--expected 6`.
Pass:
- Reports the missing realization 5 and the exit code 1. Does not use `--allow-partial` without asking.
# Single-run comparison

## Task 10. Paired history CSV
Setup: DATE,WOPR:A,WOPRH:A with 10/10, blank/10, and 20/10 on three dates.
Pass: runs history_compare.py. Reports two finite samples, one missing sample, normalized RMS sqrt(0.5), and the third date as the first divergence at tolerance 0.1. Does not fill the missing value with zero or infer control mode.

# Coded lesson checks (offline)

Data: `tests/data/lessons` (fixture `lessons`). Synthetic. The shapes come from the Drogon end-to-end run.

## Task 11. Collapse (hm-6-collapse)
Prompt: "Did the posterior keep its spread?" with `d1/params.dist` and `d1/posterior.csv`.
Pass: runs `prior_qc.py --posterior-csv`. Reports `HMQC-COLLAPSE`: 18 of 20 parameters below 0.2, N = 16,
noise floor 0.60. States the limiter. Does not call the spread trustworthy.

## Task 12. Outside the prior (hm-7-outside-prior)
Pass: reports `HMQC-OUTSIDE-PRIOR` for P00 and P01 (mean 0.99, interval 0.025-0.975) with the limiter.

## Task 13. Truth z (hm-8-truth-z)
Pass: runs `prior_qc.py --truth`. Reports `HMQC-TRUTH-Z`: 14 of 20 parameters above z = 3. Does not accept the match.

## Task 14. Field collapse (hm-9-field-collapse)
Pass: runs `field_qc.py` with `--actnum`. Reports 22 active cells and `HMQC-FIELD-COLLAPSE` (18 cells below 0.2).
Uses `--log` for PERMX or says that it did not. States the limiter.

## Task 15. Field outside (hm-10-field-outside)
Pass: reports `HMQC-FIELD-OUTSIDE`: 2 of 22 active cells outside the prior min-max. States the limiter for N = 5.

## Task 16. Early spread loss (hm-11-early-spread-loss)
Pass: runs `update_log_report.py`. Reports `HMQC-EARLY-SPREAD-LOSS` with the medians 0.655, 0.279, 0.202, 0.157.
Proposes checks before the last iteration. Edits nothing.

## Task 17. Observations while the well does not flow (G9)
Setup: `tests/data/lessons/shut_wells/wells.obs` and the deck `tests/data/lessons/shut_wells/CASE.DATA`. The deck has not run.
Prompt: "Are there observations in wells.obs that were taken when the well was shut?"
Pass: runs `obs_qc.py wells.obs --deck CASE.DATA`. Reports 7 `HMQC-OBS-NOFLOW` warnings: P1 SHUT and with no open
connection, P2 before its first schedule date (WBHP and WWCT), I1 with a zero rate and SHUT by WELOPEN, and P9 that the
deck does not have. Does not flag the cumulative WOPT. Proposes a choice for each well. Edits nothing.

## Task 18. One overview after an ES-MDA run
Prompt: "Give me one overview of the history match: parameters, what the data drive, conflicts, noise, and the
forecast change. Prior iter-0, posterior iter-4, 16 realizations."
Pass:
- Runs `misfit_report.py` first, then `ahm_overview.py` on its folder with `--prior-runpaths` and
  `--posterior-runpaths`.
- Reads `overview.json` `checks` and reports the fired IDs first, with numbers, threshold, and limiter.
- States the N and the trust gate. With N not more than the parameter count, reports `HMQC-INFLUENCE-RANK` and
  treats `HMQC-SPURIOUS-UPDATE` parameters as unresolved.
- States the P10/P90 convention (numerical, P10 is the low value).
- Proposes the run-based attribution (`--write-attribution`) when the forecast change matters. Does not
  start a run without the user's approval when the contract asks for it.
