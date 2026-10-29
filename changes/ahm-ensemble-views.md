---
bump: reservoir-engineer patch
---
### Added
- `ahm-ensemble-views` skill: `ensemble_fields.py` compares grid arrays (default PORO and PERMX, PERM* in log10) of a prior and a posterior ensemble cell by cell with resdata. It writes the prior and posterior mean, SDRATIO and SHIFT as whole-grid GRDECL arrays, `fields.json` with the findings `AHM-FIELD-COLLAPSE`, `AHM-FIELD-SHIFT-LARGE`, `AHM-FIELD-NO-UPDATE`, `AHM-FIELD-SMALL-ENSEMBLE` and `AHM-FIELD-NO-SPREAD`, `per_layer.csv`, `report.md`, and a ViewSpec for `resinsight-views`. Cells without prior spread are out of the statistics and counted in the notes. Grids that differ and missing realizations exit 2.
- `tests/synth_grid.py` `make_case` takes one PORO and PERMX value for each active cell. `tests/test_ensemble_fields.py` tests the script on synthetic runpaths. A real render test is in `tests/test_resinsight_real.py` (marker `resinsight`).
