---
bump: history-match-qc minor, case-qc patch, ahm-workflow patch
---
### Fixed

- `coverage.py`: predictive coverage no longer drops non-finite cells or averages with `nanmean`. The
  simulated/observed/error domain must be a complete finite grid with at least two realizations and one
  observation, and every error must be positive; otherwise it raises. Domain errors name the ensemble.
- `coverage.py`: `from_residuals` rejects non-integer or missing `REAL`, missing `OBS`, duplicate `REAL`/`OBS`
  rows, a ragged member set, `OBSERVED`/`ERROR`/`KEY`/`DATE` that differ within one observation, and rows whose
  `STATUS` is in `MISSING_STATUS` (`outside`, `missing_key`, `missing_file`, `missing_index`, the same set as
  `misfit_report.py`). `STATUS` may differ between the members of one observation (`exact`, `step`,
  `interpolated`). New `expected_members` and `expected_obs` arguments detect rows that are missing entirely.
  A truth value is required for every observation, and a truth CSV with more than one row for an `OBS` is
  refused.
- `misfit_report.py`: `manifest.json` `coverage_p5_p95` is computed on the configured observations and the
  expected realization set (the loaded member set when no `--expected`/`--realizations` is given). When the
  block records an error, `WARNING: coverage not computed: <reason>` goes to stderr in strict and
  `--allow-partial` mode; the rest of the report is still written and the exit code still follows the
  inventory and prediction checks.
- `misfit_report.py`: `PREDICTIVE_CDF` and `INSIDE_PREDICTIVE_P5_P95` are computed only for observations with
  at least two predictions, and the "observation out of reach" finding skips the undefined rows. Formulas and
  thresholds are unchanged.
- `compensation_check.py`: prior and posterior parameters are matched by member identity
  (`ParameterEnsemble`), not by row order. Prior and posterior member sets must be equal, and equal to
  `--expected`/`--realizations` when given. A declared bounded parameter missing from either ensemble, an
  ambiguous match and a non-finite value refuse.
- `case_qc.py`: the `--expected`/`--realizations` inventory and the run members are passed to the compensation
  step as well.
- `ahm-workflow` Step 6: the `compensation_check.py` command gives `--seed` and `--expected`, one set per seed
  run, because a claim now needs them.

### Changed

- `compensation_check.py` (compatibility): `claim_ready` needs a `--seed` for every run, distinct seeds,
  distinct prior and distinct posterior parameter fingerprints, an authoritative member inventory
  (`--expected`/`--realizations`, or `expected=` to `check_run`) for every run, and the same member set in
  every run. Otherwise the report is diagnostic and `seed_note` says why. Fingerprints detect replayed
  evidence, not statistical independence. `RunResult` adds `seed`, `member_ids`, `prior_fingerprint`,
  `posterior_fingerprint`, `inventory_checked` and `truth_missing`.
- `compensation_check.py`: repeated prior or posterior evidence refuses (exit 1) when seeds are declared. For
  unseeded diagnostic runs (for example two posteriors from one shared prior) it is a note in `seed_note`.
- `compensation_check.py` (compatibility): CSV inputs (`--prior-csv`, `--posterior-csv`) need a `REAL`,
  `REALIZATION` or `IENS` column. They are read as `utf-8-sig` (a BOM header works). Columns that are not all
  finite numbers are ignored only when their names do not match a declared parameter, for example `CASE`
  and `DATE`. Invalid exact names and bare aliases refuse even when another alias supplies valid values.
  Ambiguous ignored aliases also refuse. A missing bounded parameter keeps the missing-parameter error
  and the list of ignored columns when no valid replacement exists.
- `compensation_check.py`: `--truth` may be partial. Parameters with a finite truth value are graded; the
  bounded parameters without one are listed in `truth_missing` (JSON and text report). Non-finite truth values
  and ambiguous or duplicate matches refuse.
- `compensation_check.py`: `fit_from_manifest(path, limit, members=None)` gives an unknown fit (`None`, with the
  reason) when the misfit manifest reports issues, has no issue list or ensemble inventory, has missing members
  (`--allow-partial`), or used other members than the run. The CLI and `case_qc.py` pass the run members.
- `misfit_report.py`: `obs_summary.csv` `PREDICTIVE_CDF` (and `INSIDE_PREDICTIVE_P5_P95`) is blank when an
  observation has fewer than two members with a prediction.
