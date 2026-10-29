# Evals: ahm-workflow

Data: synthetic, offline. `evals/make_ahm_case.py <dir>` writes `<dir>/ahm` (the runner fixture `ahm_case`).
No simulator and no ERT run is needed. Ground truth is `GROUND_TRUTH` in that script. `tests/test_ahm_qc.py`
checks that the scripts give that ground truth.
Fixture facts: 10 GEN_KW parameters, 5 realizations, 84 summary observations (36 FOPR, 36 WWCT:P1, 12 WBHP:I1),
the first 10 WWCT:P1 values are 0 with ABS error 0.04, truth PORO_C = 0.14 outside UNIFORM 0.15-0.30,
update log with 83 active and 1 outlier (WWCT_P1_18), config comment `--weights 4,2,2`.

## Task 1. Prior misses the truth
Prompt: "Does the prior in params.dist cover the truth values in truth.txt? We plan 5 realizations."
Pass:
- Runs `prior_qc.py` with `--truth`.
- Reports PORO_C outside the prior and PERM_A near the upper bound.
- Reports 5 realizations for 10 parameters as rank deficient or weak.
- Proposes a wider prior. Does not edit the distribution file.

## Task 2. Weight imbalance
Prompt: "Check the observation weights in hm.obs before the update."
Pass:
- Runs `obs_qc.py`.
- Reports WBHP at 12 of 84 points (about 14 %), below the 15 % limit.
- Reports the 10 zero WWCT:P1 values with ABS error 0.04.
- Gives the proposals. Does not edit the observation file.

## Task 3. Update log
Prompt: "What did the update use? The update log is in update_log."
Pass:
- Runs `update_log_report.py`.
- Reports 83 active, 1 outlier (WWCT_P1_18), alpha 3.0.
- Mentions the active observations with a very small response_std/error ratio.

## Task 4. Small ensemble
Prompt: "Did the posterior parameters in iter-1 converge?"
Pass:
- Runs `prior_qc.py` with `--posterior-runpaths`.
- Says the result is weak or untrustworthy with N = 5. Does not claim convergence.
- Reports PERM_A at the upper bound.

## Task 5. ES-MDA weights
Prompt: "Review the ES-MDA setup of ahm.ert. We plan to use the weights in its first comment."
Pass:
- Runs `check_ert_config.py --ahm-only` (or `--ahm`).
- Reports sum(1/w) = 1.25 and the weights that ERT uses (5, 2.5, 2.5).
- Reports that RANDOM_SEED and UPDATE_LOG_PATH are not set, and N = 5 is below 20.
- Does not edit the config.

## Task 6. GENERAL_OBSERVATION
Prompt: "Check the RFT observations in rft.obs."
Pass:
- Runs `obs_qc.py`.
- Reports the zero error in RFT_P2 as an error. Reports RFT_P1 with 3 points and no error.

## Task 7. Rerun with more realizations
Prompt: "Just rerun ahm.ert with 100 reals."
Pass:
- Shows the exact command with 100 realizations and the change to `NUM_REALIZATIONS` as a proposal.
- Runs the command when the config change is in place. Does not stop for a separate approval of the command.
