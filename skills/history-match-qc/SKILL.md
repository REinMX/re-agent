---
name: history-match-qc
description: Check the observations and the result of a history match in ERT (assisted history matching). Use when the user asks if the observations are sound, how well the posterior fits, or whether the update helped. Also use it to find observations that do not match, or parameters that moved. Analyst role. Read-only.
version: 0.4.2
---

# History match QC

For a single run with history summary vectors, use `scripts/history_compare.py`:
```bash
python skills/history-match-qc/scripts/history_compare.py --csv <summary.csv> \
  --tolerance <fraction> --outdir <output_root>/<new-report-folder>
```
The CSV needs strictly increasing ISO DATE values and vector pairs such as WOPR:A and WOPRH:A.
Simulated and history values must use the same units. The script does not convert units.
It writes JSON and Markdown. It divides RMS error by RMS history over finite paired samples.
It reports the first date with absolute error above tolerance times that history scale.
Missing values stay missing. A zero history scale gives no normalized RMS value.
It reports unpaired vectors. It does not infer well control mode or ERT misfit.

You are the analyst. The tools work on files. They do not use the ERT storage.
They evaluate summary observations (`SUMMARY_OBSERVATION`) and `GENERAL_OBSERVATION` blocks (for example RFT).
They do not change a file. A proposal goes to the user.

| Tool | When | Needs |
|---|---|---|
| `scripts/prior_qc.py` | Before the run. Also after the run, with the posterior parameters. | Python. |
| `scripts/field_qc.py` | After the run, for FIELD parameters. | Python. GRDECL files. |
| `scripts/obs_qc.py` | Before the run. Also after an observation file changes. | Python. res2df only for `--case`. |
| `scripts/update_log_report.py` | After each update step. | Python. |
| `scripts/misfit_report.py` | After the run, on the runpaths of the ensembles. | res2df and pandas. |
| `scripts/ahm_overview.py` | After `misfit_report.py`. One overview for the agent and the user. | numpy and pandas. |
| `scripts/ahm_case_adapter.py` | For an exported case without ERT storage, deck, or `.obs` file. Makes the inputs of the two above. | numpy, scipy, PyYAML; res2df for `--run`. |
| `scripts/compensation_check.py` | After the run, with the prior and posterior parameters. A low misfit with parameters at prior edges is `compensating`: missing physics, not a match. | Python. |
| `scripts/coverage.py` | After `misfit_report.py`, on its `residuals.csv`. Quote its predictive coverage, never the share of observations inside the simulated band. | pandas. |
| `scripts/heldout_score.py` | After the run, on a hashed `ahm-heldout/1` split file. Scores the forecast of the held-out observations. | Python, NumPy, pandas. |
| `scripts/residual_correlation.py` | After `misfit_report.py`, on its `residuals.csv`. Tests each key for correlation in time, bias, and variance. The bounds are guides. With a truth file (synthetic case only), it splits noise and discrepancy. | NumPy and pandas. |
| `scripts/truth_score.py` | Synthetic case only, after `misfit_report.py` on the history and on a truth-forecast observation file. Gives the RMS ratios and the parameter recovery that truth gates such as G3, G4 and G5 are built on. | NumPy and pandas. |

`scripts/ahm_defaults.py` holds the AHM defaults (100 members, 3 seeds, radius guidance) as typed constants.
`scripts/collapse_checks.py` holds the ensemble-size and chi-square checks of `misfit_report.py`. It has no command.

Thresholds are options of the scripts. If `site.contract.yml` has an `ahm:` block with a value, use that value
for the option that its comment names. Otherwise use the default.

## Step 1. Find the inputs
1. Find `OBS_CONFIG` in the `.ert` file and its includes. Use `ert-config-author-lint` to map the config.
2. Find the GEN_KW distribution files (`GEN_KW` lines in the config).
3. Find the runpaths. The prior is usually `iter-0`. The posterior is the last iteration.
   Make a glob: `/scratch/.../realization-*/iter-<N>`.
4. Read `NUM_REALIZATIONS`. Use it for `--expected`.
5. Read `UPDATE_LOG_PATH` in the config. If the config does not set it, ERT 23 uses `update_log` in the folder where ERT runs.
   That folder has one subfolder for each update step, with `Report.csv` and `Report.report`.

## Step 1a. Check the prior
```bash
python skills/history-match-qc/scripts/prior_qc.py <file>.dist [--truth <truth file>] --realizations <N>
```
- An error (exit code 1) is a line that the script cannot read, or a bad distribution. Report it and stop.
- "truth outside prior": a value is outside a hard bound, or outside mean ± 3 std for NORMAL.
  NORMAL has no hard bounds. Its check is a coverage warning. The user decides on the prior.
- "fewer realizations than parameters" or "small ensemble": the statistics are weak. Say so in each answer.
- "linear distribution for a permeability-like parameter": propose `LOGUNIF`. Do not edit the file.
  A name is permeability-like when it matches `--log-pattern` and does not match `--not-log-pattern`.
  The default `--not-log-pattern` removes porosity, net-to-gross, and saturation names (for example `PORO_MULT`).
- After the run, add `--posterior-runpaths '<glob iter-N>'` (or `--posterior-csv`).
  "posterior at a bound" means that the prior can be too narrow.
- With a posterior, the script compares the spread and the mean with the prior. The prior sd and the 95 % interval
  come from the distribution. Add `--prior-runpaths '<glob iter-0>'` (or `--prior-csv`) to use the prior ensemble.
  A finding lists the posterior parameters without a usable prior (missing, or fewer than 2 values).
  If no posterior parameter has a usable prior, the script stops with exit code 1. Check the names.
  The scale is log10 for `LOGUNIF` and `LOGNORMAL`. The scale is native for the other distributions.
  - `HMQC-COLLAPSE`: more than 50 % of the parameters have posterior sd / prior sd below 0.2.
    The finding gives N, the parameter count, and the sampling-noise floor of the ratio.
    A collapse with N not more than the parameter count is not evidence that the data resolve the parameters.
  - `HMQC-OUTSIDE-PRIOR`: the posterior mean of a parameter is outside the prior central 95 % interval.
  - `HMQC-TRUTH-Z`: only with `--truth` on a synthetic case. It counts the parameters with
    |truth - posterior mean| / posterior sd above 3 (`--truth-z`).
  Each coded finding gives its numbers, its threshold, and a limiter. Report the limiter with the finding.

## Step 1b. Check the FIELD parameters
```bash
python skills/history-match-qc/scripts/field_qc.py --prior '<prior GRDECL glob>' --posterior '<posterior GRDECL glob>' \
  [--keyword PERMX] [--actnum <ACTNUM GRDECL>] [--log] [--truth <truth GRDECL>] \
  --outdir <output_root>/<new-report-folder>
```
Each glob gives one GRDECL file for each realization. Use `--log` for permeability. The script then compares ln(value).
The script gives the count of active cells and the posterior sd / prior sd of each cell.
- `HMQC-FIELD-COLLAPSE`: more than 50 % of the active cells with prior spread have posterior sd / prior sd below 0.2.
  Cells with no prior spread are counted in `cells_without_prior_spread` and left out of that share.
  Cells near the observed wells can collapse correctly. A collapse far from the wells points to missing
  localization or a too small ensemble.
- `HMQC-FIELD-OUTSIDE`: more than 1 % of the active cells have a posterior mean outside the prior ensemble min-max.
  A small prior ensemble has a narrow min-max. The finding gives the chance for one cell.
- Use `--truth` only on a synthetic case with a known truth field. The script then gives, for the prior and the
  posterior, the RMSE of the ensemble mean against the truth (ln with `--log`), the share of active cells with the
  truth inside P5-P95, and the share of cells with |z| above 3. P5 and P95 are linear percentiles (type 7).
  - `HMQC-FIELD-TRUTH-WORSE`: the posterior RMSE is more than 2 % above the prior RMSE.
    The update moved the mean field away from the truth.
  - `HMQC-FIELD-TRUTH-COVERAGE`: the posterior P5-P95 coverage is below 0.8 and at least 0.1 below the prior coverage.
  The sd checks can give no finding when these findings occur. Report the truth findings first.
  The limiter gives N. One truth field is one sample, and percentiles from N members are noisy.
A file with a different cell count, a missing `/`, or a value that is not larger than 0 with `--log` is an error (exit code 1).

## Step 2. Check the observations
```bash
python skills/history-match-qc/scripts/obs_qc.py <file>.obs --case <runpath>/eclipse/model/<BASE>.DATA
```
`--case` is one simulated case. It lets the script find keys and dates that the simulator does not have.
Give the ECLBASE deck. A `.DATA` file without summary files (for example `PERMVALUES.DATA`) gives exit code 2.
With `--case`, a well observation at a date when the well has no flow in the case is a warning.
The flow is the sum of WOPR, WWPR, WGPR, WWIR, and WGIR that the case has. Such observations carry no reservoir information.
- Errors (exit code 1) stop the history match. Examples: error not larger than 0, missing `VALUE`, `KEY`, `ERROR` or `DATE`,
  `DAYS`, `HOURS` or `RESTART` (ERT 23 rejects them), a key that the simulator does not write.
  For the time keywords, run `ert convert_observations <config>`. Then run the check again.
- Warnings need a decision of the engineer. Examples: a duplicate key and date,
  a date outside the simulated period, a dense group of observations on one day.
- The script computes the error as ERT does. `ABS` is `|ERROR|`. `REL` is `|VALUE|` times `ERROR`.
  `RELMIN` is the larger of that and `ERROR_MIN` (default 0.1). See `references/interpretation.md`.
- A dense group of observations with independent errors weighs much more than one observation.
  Report the group. Do not remove observations. That decision belongs to the user.
- Weight balance: the script gives the count share of each group (for example `WBHP`) and the sum of 1/error².
  A group below `--share-min` (0.15) or above `--share-max` (0.5) is a warning. The update can trade a small group
  against the large groups. Report the `PROPOSAL` lines to the user. Do not change the errors.
  A GEN data group is the family of the `DATA` name: the text before the first `_` (`RFT_R_A2` gives `GEN:RFT`).
  Use `--gen-group exact` to get one group for each `DATA` name.
- Zero values: a `VALUE` of 0 with an `ABS` error that is large against the signal of the key is a warning
  (`--zero-error-ratio`). Report it as a proposal.
- `GENERAL_OBSERVATION`: the script checks `DATA`, `RESTART` or `DATE`, `OBS_FILE` (value and error pairs) or
  `VALUE` and `ERROR`, and `INDEX_LIST` or `INDEX_FILE`. A missing file, an error that is not larger than 0,
  or an index count that is not equal to the value count is an error.
- `HISTORY_OBSERVATION` and other block types are warnings. The script does not evaluate them.

## Step 2a. Read the update log
```bash
python skills/history-match-qc/scripts/update_log_report.py <UPDATE_LOG_PATH> [--json]
```
The script reads each step folder. For each step it gives alpha, global scaling, the standard cutoff, and the count of
active, outlier, std_cutoff, and missing-response observations. It names each observation that the update did not use.
It gives the ratio response_std / error for each group. The group is the observation name without a
trailing `_<date>` or `_<number>`: `WBHP_A1_20180401` is in `WBHP_A1`. A name without that suffix is its own group:
`RFT_R_A2` is in `RFT_R_A2`. A low ratio (`--ratio-low`, 0.1) on an active observation
means that the update can move it only a little. For more than one step in one chain, it gives the sum of 1/global scaling.
ES-MDA expects 1. A file without a key or status column is an error (exit code 1).
If a text report has no table, the counts come from its header. The names and ratios are then unavailable.
`HMQC-EARLY-SPREAD-LOSS`: the response_std of a step is the spread of its parent ensemble. The finding is given when
the ensemble after update 1 or 2 has a median response_std / error below 0.3 (`--early-spread-limit`).
The spread then collapses early, and the later updates can move the responses only a little.
Read this finding before the last iteration starts. Check the observation errors, the inflation, and the localization.
The update count comes from the iteration number in the parent name (`iter-2`).
If the name has no number, the script follows the steps back (parent to target) to a numbered ensemble.
If that is not possible, the report says that the update count is unknown. The finding is then not checked.

## Step 3. Compare simulation and observations
```bash
python skills/history-match-qc/scripts/misfit_report.py --obs <file>.obs \
  --prior '<glob iter-0>' --posterior '<glob iter-N>' \
  --eclbase '<ECLBASE>' --ert-config <file>.ert --expected <N> --outdir <output_root>/<name>
```
Leave out `--prior` for a single ensemble. Repeat `--obs` for more files.
Copy `ECLBASE` from the `.ert` file, for example `'model/DROGON-<IENS>'`. Put it in quotes.
The script puts the realization number in place of `<IENS>`. The path is relative to the runpath.
A deck name without a folder also works. Without `--eclbase`, the script looks in `eclipse/model`, then `model`,
then the runpath. A folder can hold more than one `.DATA` file (`PERMVALUES.DATA` next to `SPE9_PLAY.DATA`).
Then that member has no unique deck. Give `--eclbase`.
If an ensemble has no valid members, the script prints the cause for the first 5 members.
`--ert-config` gives the `GEN_DATA` `RESULT_FILE` of each `GENERAL_OBSERVATION`. `--gen-data NAME=RESULT_FILE`
gives one result file. Use it when the config is not available. `%d` is the `RESTART` step.
The residuals then include each data point as `<OBS>:<index>`, with the key `GEN:<DATA>`.
Without these options, the script does not use the `GENERAL_OBSERVATION` blocks. It prints a `NOTE` with their names.
A missing result file or index is a missing prediction, the same as a missing summary value.
If the user started a subset, use `--realizations <spec>` instead of `--expected`.
Add `--update-log <UPDATE_LOG_PATH>` when the log exists. The findings then show the update status of each named
observation, and the deactivated and low-ratio observations.
Use the `python` of the contract. This script needs res2df and pandas.
The script stops with exit code 1 if a realization is missing or an observation is wrong.
It also stops if a requested summary vector has no native unit.
Flow can write `WWCT` with a blank unit. That vector is dimensionless. The script records the unit as `fraction` and does not scale the value.
A blank unit on any other key still stops the report. Do not guess a unit for a rate or a pressure. Do not drop an observation to force a misfit.
If an observation is wrong, report the errors and stop. Do not edit the observation file or a copy of it to make the
script run. Do not report misfit numbers from a changed file. The user corrects the observations. Do not use `--allow-partial`
unless the user accepts a partial ensemble. State the count of realizations in the answer.

Read `manifest.json` first. Then read these files:
| File | Use |
|---|---|
| `real_misfit.csv` | Misfit of each realization. A large spread means the realizations differ much in quality. |
| `obs_summary.csv` | For each observation: mean, spread, envelope, mean residual. Sort by `MEAN_RESIDUAL`. |
| `residuals.csv` | All residuals. Use it to plot or to find a time pattern. |
| `params_shift.csv` | Parameter change from prior to posterior, in prior standard deviations. |

`manifest.json` also has:
- `misfit_by_family`: for each ensemble, the observation count and the mean squared residual of each vector
  mnemonic (`WOPR`, `WBHP`). All `GENERAL_OBSERVATION` data is in the family `GEN`. The script prints a `FAMILY` line.
- `trust`: the ensemble-size gate for each ensemble. `weak` means N is below `--min-realizations` (20), or N is not more
  than the number of uncertain parameters. `untrustworthy` means both. The script prints a `GATE` line.
- `misfit_vs_chi2`: the mean misfit against the noise band 1 ± k·sqrt(2/n) (`--chi2-sigma`, k = 3), in `verdict`.
  A posterior below the band is a finding: possible overfit, or errors that are too large.
  `spread_verdict` compares it with the spread-aware reference 1 + 2 s + k·sqrt(2/n), where s is the mean over the
  observations of var over members of SIMULATED / ERROR² (ddof 1). Only a posterior above that reference is a finding
  ("misfit above the spread-aware reference"). Above the noise band but inside the reference is context, not a
  verdict: a well-calibrated wide ensemble sits there. An over-dispersed posterior widens the reference and can hide
  misfit; read coverage and the collapse checks with it. Do not read a prior or physics error from chi2: use the
  prior_qc and physics checks.
- `update_log`: the summary of the update log, when you gave `--update-log`.

## Step 3a. Make the overview
Run it after Step 3. It reads the `misfit_report.py` folder and the runpath parameters. It runs the prior_qc
checks in-process and copies the coded findings of misfit_report, the update log, and field_qc.
```bash
python skills/history-match-qc/scripts/ahm_overview.py --misfit-dir <misfit folder> --dist <file>.dist \
  --prior-runpaths '<glob iter-0>' --posterior-runpaths '<glob iter-N>' [--truth <truth file>] \
  [--truth-misfit-dir <misfit folder of the truth run>] [--field-qc <field_qc.json> ...] \
  [--forecast-prior <per_real.csv> --forecast-posterior <per_real.csv> --forecast-truth KEY@AS_OF=VALUE] \
  [--attribution-csv <CASE,KEY,VALUE>] [--param-class 'REGEX=static:CATEGORY'] --outdir <new-report-folder>
```
It refuses (exit 1) a misfit folder written with `--allow-partial`: the prior and the posterior must be complete.
Read `overview.json` first. Each item in `checks` has `id`, `source`, `severity`, `fired`, `numbers`, `threshold`,
`limiter`, and `rationale`. The new checks are always in the list, also when they do not fire.
`overview.md` is the short summary for the user. P10/P90 are numerical: P10 is the low value.
- Parameters: prior and posterior mean, sd, P10/P50/P90, sd ratio, shift in prior sd, bound, and truth position.
  The scale is log10 for `LOGUNIF` and `LOGNORMAL`. Class: static (perm, kvkh, poro, ntg, facies) or dynamic (fault,
  relperm, pvt, contacts, multiplier, aquifer). Use `--param-class` for other names.
  - `HMQC-PARAM-UNCLASSIFIED`: a parameter matches no class rule.
  - `HMQC-STATIC-DYNAMIC` (info): the share of the total |shift| for each class.
  - `HMQC-PARAM-TRUTH-PRIOR`: a truth value is outside the prior ensemble min-max. Synthetic case only.
- Influence: one linear ES step from the prior ensemble, K = Cxy (Cyy + R)^-1 with normalized data.
  For each observation, family, key, and well, the overview gives its share of the update and of the DFS
  (data-space self-sensitivity). For each parameter, it gives the driving family and the push of each family.
  - `HMQC-OBS-DOMINANT`: one family has more than 50 % of the update or of the DFS.
  - `HMQC-OBS-CONFLICT`: two strong groups push the parameters in opposite directions (cosine ≤ -0.5).
  - `HMQC-PARAM-TUG` (info): different families pull one parameter up and down.
  - `HMQC-SPURIOUS-UPDATE`: a parameter moved more than 1 prior sd without a data signal it can trust.
    The linear update is small or has the other sign, or 50 % or more of it comes from responses with
    |correlation| below 3/sqrt(N). This is the ERT adaptive threshold.
  - `HMQC-INFLUENCE-RANK`: N is not more than the parameter count.
- Observations: misfit by family, key, and well. Prior-predictive coverage counts an observation as covered when
  |obs - prior mean| / sqrt(prior sd² + error²) ≤ 3.
  - `HMQC-PRIOR-PREDICTIVE`: coverage below 0.9 overall, or below 0.7 in one family.
  - `HMQC-OBS-OUTLIER`: |posterior mean residual| above 3.
- Noise:
  - `HMQC-NOISE-GROUP`: the posterior misfit of a family is below its chi-square band. The errors can be too
    large, or the update overfits.
  - `HMQC-NOISE-TRUTH`: the misfit of the truth run for a family is outside its band. The stated errors are wrong
    for that family.
  - `HMQC-NOISE-CORRELATED`: the lag-1 autocorrelation of the posterior mean residual of a key is above 0.5.
    The errors are correlated, or the model has an error. The independent observations are fewer than the count.
- Forecast: give two `ensemble-at-date` `per_real.csv` files with the same realizations as the misfit folder.
  The forecast checks need the same KEY and date in the prior and the posterior. A missing identity refuses the report. It is not dropped from the comparison.
  The overview gives prior and posterior P10/P50/P90. It also estimates the part of the change that comes from
  each class: the marginal prior slope times the parameter shift.
  - `HMQC-FORECAST-SHIFT` (info): the posterior mean is outside the prior P10-P90.
  - `HMQC-FORECAST-COLLAPSE`: the forecast sd ratio is below 0.2 and the trust gate is weak or untrustworthy.
  - `HMQC-FORECAST-TRUTH`: the truth is outside the posterior P10-P90.
- Run-based attribution: `--write-attribution <folder>` writes `parameters.txt` for the cases base (prior mean),
  static (static parameters at the posterior mean), dynamic, and all. Run the forward model for each case.
  Each run needs the user's approval when the contract asks for it. Then give the values as `--attribution-csv`.
  - `HMQC-ATTRIBUTION-NONLINEAR`: the interaction is more than 30 % of the total, or the closure of the
    statistical estimate is outside 0.5-1.5.

The influence estimate is linear. It uses the prior only. It does not model localization or the ES-MDA
inflation. Report it as a hint, with the limiter. `references/drogon-official.md` gives the sources for each check
and compares the Drogon box case with the official fmu-drogon setup.

## Step 3b. An exported case (no ERT storage, no deck, no .obs file)
Some cases have only the runpaths (`parameters.txt`, `<ECLBASE>.SMSPEC/.UNSMRY`) and an fmu-tools observation table
(`share/observations/tables/ert_observations.yml`). An example is the public Drogon AHM in webviz-subsurface-testdata
(`references/drogon-official.md` gives the fetch commands; do not commit that data).
```bash
python skills/history-match-qc/scripts/ahm_case_adapter.py --prior-runpaths '<glob iter-0>' \
  --posterior-runpaths '<glob iter-N>' --eclbase 'eclipse/model/<BASE>-<IENS>' \
  --observations <ert_observations.yml> --expected <N> [--allow-missing posterior:<REAL> ...] \
  [--dist-reference <file>.dist ...] --outdir <new-report-folder> --run
```
- A gap in either ensemble (no folder, no parameters.txt, no SMSPEC or UNSMRY) is exit 1. A missing summary is
  never zero. Name a known gap with `--allow-missing`; the comparison then uses the realizations that both ensembles
  have, and `members.json` lists each one left out. Say so in the answer.
- The case has no distribution files, so `prior.dist` is rebuilt from the prior sample. A `--dist-reference` line is
  used when its bounds hold every prior value. `prior_provenance.csv` gives the source and a KS p-value of each line.
  Report the rebuilt lines as an assumption. A rebuilt UNIFORM comes from the sample min-max, so it can be a little
  narrower than the true prior; prefer a reference line.
- Only the `smry` section is used. Other sections are listed in `members.json`.
- `--run` calls `misfit_report.py` into `misfit/` and `ahm_overview.py` into `overview/`. Without forecast runs the
  forecast checks do not fire; say that they were not checked.

## Step 4. Interpret
Use `references/interpretation.md`. Follow these rules:
1. State the definition: misfit is the mean of the squared normalized residuals. It is not the ERT objective function.
2. State the count of realizations and the `GATE` level. If the gate is `weak` or `untrustworthy`, say so first.
   Do not claim convergence with a weak gate.
3. Report numbers before judgment. Give prior and posterior misfit, the median, and the worst realization.
4. Name the observations in each finding. A finding is a hint. Look at the residuals before you give a cause.
5. A low misfit alone does not prove a good model. Check the parameter shifts and the collapse findings.
6. Do not call the history match "good" or "bad". Report what the numbers show and what you did not check.

## What the tools do not do
- They do not run the ERT update. They do not read the ERT storage.
- `ahm_overview.py` does not start a run. It does not put FIELD cells in the influence estimate.
  For fields, it uses the `field_qc.py` results.
- `heldout_score.py` gives bias, RMS, coverage, 90 percent interval width, and CRPS per key, in the native unit of the key.
  It does not pool keys with different units. It sets no threshold and gives no verdict.
  It reads only `SUMMARY_OBSERVATION` blocks. Any other block is an error.
  A true value is scored against the ensemble. A noisy observation is scored against the ensemble plus its stated noise.
  It checks the files that exist now. It cannot prove that the held-out data stayed unused during the run or the tuning.
  Say this in the report.
- `residual_correlation.py` tests the residuals of each key for correlation in time, bias, and variance.
  The references are spread-aware, with s the mean member spread var(SIMULATED) / ERROR² of the series (0 for the noise
  component). `HMQC-OBSERR-BIAS` fires when |mean r| > 3·sqrt(1/n + s). `HMQC-OBSERR-VARIANCE` fires when
  mean(r²) > 1 + 3·sqrt(2/n) + 9 s. `HMQC-OBSERR-AUTOCORR` is a review item in `review`, never a finding: a smooth
  ensemble-mean miss makes rho1 high by construction.
  On real data, it cannot separate measurement noise from model discrepancy, so a hint gives no cause.
  It does not change `ERROR`.
  It refuses a `residuals.csv` that holds `GENERAL_OBSERVATION` rows, such as RFT, because they have no DATE.
  Make a copy of the file without those rows first.
  Do not raise `ERROR` to make a gate pass.
- `truth_score.py` needs a known truth, so it is for synthetic and benchmark cases only. It sets no threshold and gives no verdict.
  It gives the mean-response RMS of the prior and the posterior and their ratio, on the history and on a truth forecast.
  The forecast file holds the truth response as VALUE, with the error the gate uses at that value.
  It gives the posterior p10 and p90 of one forecast observation and whether the truth lies between them.
  It gives the spread reduction and the truth bias in prior standard deviations of each parameter, in log10 for LOGUNIF and LOGNORMAL.
  Apply the gate that was registered before the run. Do not pick a threshold after seeing these numbers.
- For a date between two time steps, a rate or ratio vector (for example `WOPR`, `WWCT`) gets the value at the end
  of the step. The summary file stores the mean rate over each step. Other vectors get linear interpolation.
  `manifest.json` gives the rule for each key. ERT may treat an observation date differently.
- `misfit_report.py` does not evaluate `HISTORY_OBSERVATION` data. It reads `GENERAL_OBSERVATION` data only with
  `--ert-config` or `--gen-data`, and only with `RESTART`. A `GENERAL_OBSERVATION` with `DATE` is an error then.
- The update log gives alpha, the inflation (global scaling), and the deactivated observations. It does not give the
  localization. Read the `ANALYSIS_SET_VAR` lines with `ert-config-author-lint` (`check_ert_config.py --ahm-only`).

## Report
1. The files and the commands, with the number of realizations in each ensemble.
2. Prior and posterior misfit (mean, median, worst).
3. Each finding, with the observation or parameter names and the numbers.
4. What you did not check.
5. The next action for the user.

## Verified limit
A 600-cell waterflood with a known truth can cut misfit and still miss the layer permeabilities.
The sum of the layer permeabilities can sit near the truth while the layers trade off.
That was stage 04: one producer completed in all three layers. Posterior `PERMX_L2` was 4.55 of its own standard deviations from 550 mD. The layer correlations were negative.

Stage 06b used the same ten truth numbers and recovered the three layer permeabilities.
Posterior means were 120, 560, and 45 mD against truth 120, 550, and 45. Errors were 0.09, 0.34, and 0.13 posterior standard deviations. The layer correlations were positive.
Do not credit the layer-selective completions for that. Stage 06a was not run. Well count, controls, and observations also differ from stage 04.

The same 06b posterior missed `KVKH` (truth 0.08, mean 0.26, 3.75 standard deviations) and `KRW_MAX` (truth 0.55, mean 0.74, 3.27 standard deviations). Both spreads collapsed.
A misfit drop from 666 to 1.49 does not make those two right.

Do not accept a match from the misfit drop. Score the parameters against the truth when a truth case exists.
When no truth exists, treat a large misfit drop plus a collapsed or traded parameter as unresolved.
