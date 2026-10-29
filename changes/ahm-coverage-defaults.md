---
bump: history-match-qc minor, ert-config-author-lint minor, hm-localization minor, ahm-workflow patch
---
### Added
- Add `coverage.py`: predictive P5-P95 coverage (sim plus observation noise, exact) and truth-response coverage, with the noise-biased share labelled. Regression test on a drogon-lite posterior (biased 0.48, predictive 0.92, truth 0.89).
- Add `ahm_defaults.py`: typed AHM defaults (100 members, 3 seeds, localization radius 1.25 times the median nearest-well spacing, inferred) with their basis.
- Add `localize_obs.py --radius auto` from the well spacing.
### Changed
- `misfit_report.py` writes the predictive CDF per observation and the predictive coverage per ensemble. "observation out of reach" uses the predictive band, not min-max of the noise-free simulations.
- `check_ert_config.py --ahm` reviews an ensemble below 100 members by default and asks for 3 seeds before a claim.
