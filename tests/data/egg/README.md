# Egg benchmark update log

The update log of an ES-MDA run (4 iterations, 100 realizations, FIELD PERMX) on the public Egg benchmark
model (TU Delft / ISAPP). These files are our own ERT 23 outputs. They hold no Egg input files.

| Path | Content |
|---|---|
| `update_log/step_<n>/Report.csv` | The Report.csv of the update to iteration `n` (one row per observation). |
| `update_log/step_<n>/Report.report` | The header of the ERT Report.report only (Parent, Target, Alpha, Global scaling, cutoff, counts). The table is in Report.csv. |

ERT 23 names the step folders by UUID. Here the folders are renamed to `step_<target iteration>` to make
them easy to read. `update_log_report.py` orders the steps by the Parent and Target names in Report.report, not
by the folder name. `tests/test_lesson_fixtures.py` also runs a copy with UUID folder names.

Expected result of `update_log_report.py`: median response_std / error 0.482 -> 0.261 -> 0.26 -> 0.28, sum of
1/global scaling 1 (15, 7.5, 3.75, 1.875), 8, 16, 20, 21 observations below STD_CUTOFF, and
`HMQC-EARLY-SPREAD-LOSS` in ensemble iter-1 (after update 1).
