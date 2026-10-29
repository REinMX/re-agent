# Egg benchmark Everest runs

Four Everest 23 runs on the public Egg benchmark model (TU Delft / ISAPP). These files are our own Everest outputs.
They hold no Egg input files. All runs use 10 posterior members and `optpp_q_newton`. EG1, EG3, and EG4 have no
output constraints. EG2 has input and output constraints.

| Path | Content |
|---|---|
| `EG1_mean_d1/config.yml` | Maximizes the mean NPV. `max_batch_num` 10. |
| `EG2_constrained_d1/config.yml` | Maximizes the mean NPV. `max_batch_num` 10. Output constraint `vrr` >= 0.9. Input constraints: the total injection of the 8 injectors in each of the 2 periods <= 700. |
| `EG3_drill_d1/config.yml` | Maximizes the mean NPV with 3 drill priorities (`generic_control` `drill`, PROD5, PROD6, PROD7). `max_batch_num` 6. |
| `EG4_cvar_d1/config.yml` | Maximizes the lower-tail CVaR of NPV, `cvar.percentile` 0.25. `max_batch_num` 16. |
| `<run>/output/simulation_results/experiments/run1/experiment_results.csv` | The full Everest result table (unperturbed and perturbation rows). |
| `<run>/output/logs/everest.log` | The Everest log. It is empty in all runs. |
| `EG1_mean_d1/simulation/batch_<n>/realization_0/evaluation_0/EGG_PRED.PRT` | Excerpts of the Flow PRT file. |

Changes from the run folders:

- In `config.yml`, the absolute paths in `environment.output_folder`, `environment.simulation_folder`, and
  `install_data.source` are replaced with the relative paths `output`, `simulation`, and
  `../egg_ahm/realization-<REALIZATION_ID>/iter-4`. The configs do not run from this folder.
- The PRT files are excerpts. `batch_1` has the lines around each Flow message "will be shut as it can not operate"
  (PROD1, PROD3, PROD4). `batch_0` has the first lines of the run and no such message.
- The EG1, EG3, and EG4 CSV files are not trimmed (EG3 has no `grad(...)` columns). The EG2 CSV has all 200 rows, but its `grad(...)` columns are
  removed (50 columns remain). Each run folder is less than 200 KB.
- The EG2 CSV has the plain control columns (`inj.INJECT1.1`) and also `batch_<control>` columns
  (`batch_inj.INJECT1.1`, `batch_prod.PROD1`), `batch_vrr`, `vrr`, and `vrr_right`. A run without constraints has no
  `batch_<control>` columns. The `batch_<control>` values are about 1e-6. They are not the control values.

Expected result of `everest_diagnostics.py` (default direction `minimize`, because Everest writes the negated total):

- EG1 with `--simulation-folder EG1_mean_d1/simulation`: `EVD-WELL-SHUT`, `EVD-BUDGET-LIMITED` (best batch 9 is the
  last of 6 batches), `EVD-UNCONSTRAINED`, and `EVD-COST-RATIO` (110 rows against the upper bound 200).
- EG2: `EVD-BUDGET-LIMITED` (best feasible batch 9 is the last of 10 batches), `EVD-COST-RATIO` (200 of 200), and
  `EVD-CONSTRAINT-INACTIVE` (`batch_vrr` 0.99972 to 0.99997, range 0.03 % and distance 10.0 % of `scale` 1).
  All batches are feasible: `batch_vrr` is 0.99972 or more, and the period injection totals are 636 or less.
  No `EVD-START-NEAR-CONSTRAINT`: the start is 0.09997 from the `vrr` bound (10.0 % of `scale` 1, threshold 5 %).
  The start injection total 636 is 64 from 700 (9.1 % of `scale` 700); the check reads output constraints only.
- EG3: `EVD-FLAT-OBJECTIVE` (NPV 10.736824 in all 6 batches; the drill priorities moved from 0.8/0.5/0.2 to
  1.0/0.594/0.061 and back to 0.808/0.504/0.194, but the order PROD5 > PROD6 > PROD7 did not change),
  `EVD-UNCONSTRAINED`, and `EVD-COST-RATIO` (80 of 180).
- EG4 with `--objective npv`: `EVD-CVAR-TAIL-ONLY-GRADIENT` (3 of 10 members), `EVD-BUDGET-LIMITED`,
  `EVD-UNCONSTRAINED`, and `EVD-COST-RATIO` (104 of 320). No `EVD-CVAR-TAIL-MISSING` or `EVD-CVAR-TAIL-COUNT`.
- The best EG1 batch (9) has mean NPV 26.38 and lower-tail CVaR(0.25) 19.54. The best EG4 batch (14) has CVaR(0.25)
  19.07. The mean plan is better also on the CVaR objective. The EG1 config has no `cvar`, so the test adds
  percentile 0.25 in memory to get the CVaR comparison.
