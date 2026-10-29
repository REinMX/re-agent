# Interpretation guide

Tested on a real ERT run: 5 realizations, 72 summary observations, `ensemble_smoother`, ERT 23.0.1, OPM Flow.
There the mean misfit went from 50.2 (prior) to 2.33 (posterior).
With 5 realizations this is a test of the tools. It is not evidence about a model.

## Definitions
| Name | Definition |
|---|---|
| Normalized residual | (simulated value - observed value) / error |
| Misfit of a realization | Mean of the squared normalized residuals over its observations |
| Mean residual | Mean of the normalized residuals of one observation over the realizations |
| Spread ratio | Standard deviation of the simulated values / error |
| Envelope | Minimum and maximum of the simulated values over the realizations |
| Shift | (posterior mean - prior mean) / prior standard deviation of a parameter |
| Std ratio | Posterior standard deviation / prior standard deviation of a parameter |

A misfit of 1 means that the typical miss is equal to the stated error.
Observations that carry measurement noise do not give a misfit of 0 for the true model.

## ERT error rules (ERT 23, `ert/config/_observations.py`)
| ERROR_MODE | Error that ERT uses |
|---|---|
| ABS (default) | `abs(ERROR)` |
| REL | `abs(VALUE) * ERROR` |
| RELMIN | `max(abs(VALUE) * ERROR, ERROR_MIN)`. `ERROR_MIN` is 0.1 if the file does not set it. |

The error must be larger than 0. `SUMMARY_OBSERVATION` needs `DATE`. This ERT version rejects `DAYS`, `HOURS`, and `RESTART`,
also next to `DATE`, and tells the user to run `ert convert_observations <config>`. `obs_qc.py` reports them as errors.
An older ERT can accept them. Use the conversion for all versions.
The script `obs_qc.py` gives the same errors and dates as ERT for these rules (tested against ERT).

## Findings
All thresholds are options of `misfit_report.py`. They are starting points. They are not standards.

| Finding | Meaning | First check |
|---|---|---|
| observation out of reach | The observation is outside min-max of all realizations and more than `--residual-limit` (2) errors from the mean. The ensemble cannot match it. | Is the observation or its error wrong? Is a parameter missing? Is the date right? |
| ensemble spread is much smaller than the error, and the mean misses the observation | Spread / error is below `--collapse-ratio` (0.1) and the miss is large. The ensemble has no freedom to correct it. | Compare the prior and posterior spread. Look for parameter collapse. |
| observation not used | No simulated value: the key is missing or the date is outside the simulated period. | Run `obs_qc.py --case`. |
| mean misfit is higher than in the prior | The posterior fits worse on average. | Check the update settings and the observations. Look at `obs_summary.csv`. |
| many observations moved away | More than `--worse-fraction` (0.3) of the observations have a mean residual that is more than `--worse-margin` (0.5) errors larger than in the prior. | Look for a group of observations in time or in key. |
| observation got clearly worse | An observation has a mean residual that is more than `--worse-single` (1.0) errors larger than in the prior. The finding lists the values before and after. | Look at the residual over time for that key. The update may trade one observation for others. |
| large parameter shift | A parameter moved more than `--shift-limit` (2) prior standard deviations. | Is the new value physical? Is it at the edge of its range? |
| parameter spread collapsed | Posterior std is below `--std-ratio-limit` (0.2) of the prior std. The detail adds a caveat: the std ratio that sampling noise alone can give for this N, and whether N ≤ the number of parameters. | With a small ensemble, collapse is common. Check the ensemble size and the localization. |
| ensemble size: weak / untrustworthy | N is below `--min-realizations` (20), or N is not more than the number of uncertain parameters. Both: untrustworthy. | Do not claim convergence. Propose more realizations to the user. |
| misfit below the noise level | The posterior mean misfit is below 1 − k·sqrt(2/n) (`--chi2-sigma`, k = 3). | Possible overfit, or errors that are too large. Check the errors and the parameter collapse. |
| observation deactivated in the update | From `--update-log`: the update did not use these observations (outlier, std_cutoff, or missing response). | Is the observation wrong, or is the prior too narrow to reach it? |
| active observation with small ensemble spread | From `--update-log`: response_std / error is below `--ratio-low` (0.1). | The update can move it only a little. Look at the prior spread of that key. |
| ES-MDA inflation does not sum to 1 | From `--update-log`: the sum of 1/global scaling over one chain of steps is not 1. | Compare with the planned weights. |

## Checks before the run
### Prior (`prior_qc.py`)
| Finding | Meaning | First check |
|---|---|---|
| truth outside prior | A truth or analog value is outside a hard bound, or outside mean ± 3 std for NORMAL. NORMAL has no hard bounds; this is a coverage warning. LOGNORMAL requires a positive value. | Review the prior, or check the truth value. The user decides. |
| truth near a bound | The value is within `--edge-fraction` (0.1) of the range from a bound. LOGUNIF uses the log scale. | Expect the posterior to pile up there. |
| posterior at a bound | At least `--pile-fraction` (0.3) of the realizations are within `--edge-fraction` of a bound. | The prior can be too narrow. |
| fewer realizations than parameters | N ≤ the number of uncertain parameters. The ensemble is rank deficient. | Expect spurious correlations and too much collapse. |
| linear distribution for a permeability-like parameter | A name that matches `--log-pattern` and not `--not-log-pattern` (PORO, NTG, saturation) has UNIFORM, DUNIF, TRIANGULAR or TRUNCATED_NORMAL. | Propose LOGUNIF or LOGNORMAL. The effect is large when max/min is 10 or more. |

### Observation weights (`obs_qc.py`)
Each observation adds about 1 to the misfit when its error is right. So the share of the data count is the share of the
misfit budget. The sum of 1/error² depends on the unit. Compare it only inside one group.
A group is the vector mnemonic (`WBHP`, `FOPR`) or `GEN:<family>` for a `GENERAL_OBSERVATION`. The family is the
`DATA` name before the first `_` (`RFT_R_A2` gives `GEN:RFT`). `--gen-group exact` gives `GEN:<DATA>`.

| Warning | Meaning | Proposal to the user |
|---|---|---|
| group share below `--share-min` (0.15) | The update can trade this group against the others. Example: 12 BHP points in 84 (14 %) got much worse after an ES update. | Review the errors of the group, or use `ANALYSIS_SET_VAR OBSERVATIONS AUTO_SCALE <pattern>` (ERT 23). |
| group share above `--share-max` (0.5) | One group controls the update. | Same as above. |
| zero values with a large error | `VALUE` 0 with an `ABS` (or `RELMIN`) error that is at least `--zero-error-ratio` (0.05) of the largest value of the key. Example: water cut 0.0 ± 0.04 before breakthrough. | A smaller error before the signal starts, fewer early points, or keep them on purpose. |

The script never changes the observation file. The user decides.

### GENERAL_OBSERVATION
| Field | Check |
|---|---|
| `DATA` | Must exist. It names the GEN_DATA response. |
| `RESTART` or `DATE` | One must exist. `RESTART` is an integer report step. `DAYS` or `HOURS` is a warning. |
| `OBS_FILE` | The file must exist. It holds value and error pairs. The count of numbers must be even. Each error must be larger than 0. |
| `VALUE` and `ERROR` | The other form for one point. Do not use it together with `OBS_FILE`. |
| `INDEX_LIST` or `INDEX_FILE` | Optional. Without it, the indices are 0 to n−1. The count must equal the count of values. No duplicates. |

The script does not read the GEN_DATA files of a run, and `misfit_report.py` does not compute their misfit.

### HISTORY_OBSERVATION
It needs the history vectors (`<KEY>H`) of the reference case. The scripts do not evaluate it.
Recent ERT versions do not accept it. `ert convert_observations <config>` can write `SUMMARY_OBSERVATION` blocks with dates.
Check this with the ERT version of the project before you propose it.

## Cautions
- Time rule: a rate or ratio vector (`*OPR`, `*WPR`, `*GPR`, `*LPR`, `*WIR`, `*GIR`, `*WCT`, `*GOR`, `*GLR`, `*OGR`, `*WGR`)
  is the mean over the time step that ends at the report time. For a date inside a step, the script uses the value at the
  end of that step. Cumulative vectors and pressures get linear interpolation. The column `STATUS` in `residuals.csv`
  says `exact`, `step`, or `interpolated`. In OPM Flow (SPE1), a rate change at 2015-04-01 showed first at the next step.
  Linear interpolation gave a value between the old and the new rate. That value never existed in the simulation.
- Dense observations: many observations of one key on one day (for example a pressure gauge) can dominate the misfit.
- Small ensembles: statistics on fewer than 20 realizations are weak. State the count.
- Iterative methods: compare `iter-0` with the last iteration. Compare intermediate iterations with extra runs of the tool.
- Not covered by `misfit_report.py`: `HISTORY_OBSERVATION`, and the misfit of `GENERAL_OBSERVATION` data (RFT,
  4D seismic, tracer). `obs_qc.py` checks the `GENERAL_OBSERVATION` blocks only. Semeio provides workflow jobs
  such as `AHM_ANALYSIS` for further analysis. Those are not part of these tools.
