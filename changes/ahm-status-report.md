---
bump: reservoir-engineer patch
---
### Added

- New skill `ahm-status-report`. `skills/ahm-status-report/scripts/ahm_status.py` makes one plot-first status
  report of an ES or ES-MDA history match from a scratch case: `summary.md` (one page), `report.md`, `report.pdf`,
  `status.json`, PNG plots and CSV tables. Posterior-only mode is the main path (iter-0 deleted): the report says
  that the prior is gone and never makes one. With earlier iterations on disk it adds the prior-to-posterior and
  iteration-to-iteration comparisons. With `--ert-config` it reads NUM_REALIZATIONS, ECLBASE, OBS_CONFIG, the
  GEN_KW `.dist` files, and the stored iterations and ES-MDA weights from the ERT storage.
  The numbers come from `misfit_report.py`, `residual_correlation.py`, `compensation_check.py` (with a prior),
  `check_ert_config.py --ahm-only` and `physics_checklist.py`. The verdict (Fit for use, Use with caveats,
  Not ready) is one rule table and is advisory. A missing SUMMARY vector is reported as missing, never as 0.
  Pressure uses WBP9, WBP4, WBP, then WBHP. P10/P50/P90 are numerical (P10 is the low value).
  It writes to `<case>/share/results/ahm_status` only with `--write-share`.
- `ahm-status-report` shows the known false alarms (chi-square above the noise band but inside the spread-aware reference, the member-count lint, compensation EDGE) and does not count them toward the verdict. A parameter at a prior bound still counts (Use with caveats): it caught the narrow-prior calibration setup. Valid rescaled ES-MDA weights such as 4,2,1 are an INFO note.
