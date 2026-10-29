---
bump: ahm-workflow minor, history-match-qc patch
---
### Added
- Add `screen_params.py`: keep or drop each parameter before ES-MDA from a one-by-one design and AHM_ANALYSIS, with reasons (insensitive, not resolved jointly, collinear partner and cosine). Writes the `ahm-screening/1` record. On drogon-lite it drops ROCK_CMULT (sd ratio 0.75, cos 0.97 with AQ_VOL).
- Add `ahm_preflight.py`: refuses an ES or ES-MDA run without a matching screening record, with a dropped or unscreened parameter still sampled, or with fewer than 100 members. Prints the ERT 26 command when ready, and the physics checklist warnings with `--deck`.
### Fixed
- `misfit_report.py` reads the runpaths of a design matrix ensemble. It skips the SENSNAME and SENSCASE text lines in parameters.txt.
