---
bump: history-match-qc minor
---
### Added

- `skills/history-match-qc/scripts/ahm_overview.py` makes one overview of an ES or ES-MDA history match:
  `overview.json` for an agent and `overview.md` for the user. It builds on the `misfit_report.py` folder.
  It runs the prior_qc checks in-process and copies the coded findings of misfit_report, the update log,
  and field_qc. It adds:
  - a parameter table in the sampling scale, with a static or dynamic class;
  - prior-predictive coverage;
  - misfit by family, key, and well;
  - a linear-Gaussian influence estimate for each observation and group (update share and DFS);
  - conflicts between data groups;
  - noise checks (chi-square band per family, truth-run noise, residual autocorrelation);
  - forecast change with a statistical attribution, and a run-based static/dynamic attribution
    (`--write-attribution`, `--attribution-csv`).
  New check IDs: HMQC-PRIOR-PREDICTIVE, HMQC-OBS-OUTLIER, HMQC-OBS-DOMINANT, HMQC-OBS-CONFLICT, HMQC-PARAM-TUG,
  HMQC-SPURIOUS-UPDATE, HMQC-INFLUENCE-RANK, HMQC-NOISE-GROUP, HMQC-NOISE-TRUTH, HMQC-NOISE-CORRELATED,
  HMQC-PARAM-UNCLASSIFIED, HMQC-STATIC-DYNAMIC, HMQC-FORECAST-SHIFT, HMQC-FORECAST-COLLAPSE, HMQC-FORECAST-TRUTH,
  HMQC-ATTRIBUTION-NONLINEAR, HMQC-PARAM-TRUTH-PRIOR. Each check is in the list with `fired` true or false.
  P10/P90 are numerical (P10 is the low value). A misfit folder written with `--allow-partial` is refused (exit 1).
- `skills/history-match-qc/references/drogon-official.md` pins the official Drogon sources: fmu-drogon,
  fmu-drogon-flow-files, the conceptual model, and the ERT localization and history-matching pages.
  It compares the box Drogon case with the official setup and gives the rationale for each overview check.
