---
bump: history-match-qc minor
---
### Added
- Add `heldout_score.py`: retrospective forecast scores (bias, RMS, coverage, 90 percent interval width, CRPS) per key and unit for the observations that a hashed `ahm-heldout/1` split declares as held out. History and held-out observations must not share a KEY and DATE, and held-out dates must follow `history_end`. The split is checked against the real `SUMMARY_OBSERVATION` file, including each declared value and error. A true value is scored against the ensemble. A noisy observation is scored against the ensemble plus its stated noise. It sets no threshold and gives no verdict, and it states that it cannot prove independence at run time. On the saved public MODEL1 and MODEL6 ensembles it leaves the saved QC verdicts untouched.
