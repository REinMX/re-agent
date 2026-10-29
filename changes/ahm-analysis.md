---
bump: reservoir-engineer patch
---
### Added
- `ahm-analysis` skill: `run_ahm_analysis.py` runs the semeio AHM_ANALYSIS workflow on a copy of the config folder and the storage (ENSPATH, UPDATE_LOG_PATH and RUNPATH_FILE point into a work folder, the prior is the second workflow argument), collects the AhmAnalysisJob CSV files with the wall time and the ERT and semeio versions, and fails (exit 2) when the original storage, update log or config folder changed. Missing ERT or semeio is exit 2.
- `ahm_influence.py` (standard library) reads `ks.csv` against a noise floor: the exact two-sample KS critical value for the ensemble size (0.5 at N = 16; asymptotic 0.48 for reference), the top group per parameter, a leave-one-out table (KS(All_obs) - KS(All_obs-group)), and the spurious-correlation signature (one group is the top group for at least 40 % of the parameters, exit 1). `--overview-json` compares the top group family with the `ahm_overview.py` driver group and reports "not compared" for an unknown schema.
- `tests/test_ahm_analysis.py`: synthetic tests, and a real Drogon run with `AHM_REAL_CONFIG`.
