---
bump: resinsight-session minor
---
### Added
- `ri_session.py`: a typed ResInsight session (context manager) that owns the process (port 0, log file, own process group, SIGTERM cleanup), starts a private Xvfb for GUI mode, checks the rips/ResInsight version, and handles 17 measured rips problems in code: silent export failures, cropped plot PNGs (PDF and pdftoppm), plot id -1, the `export_property` default-argument bug, the auto-loaded summary case, empty property lists, wells that need a view, all-cell masks, grid statistics lists, summary ensembles through project XML, the active-cell length of generated properties, and a stale port file from an earlier run.
- `tests/fake_rips.py` (a rips test double with the measured behaviour) and `tests/synth_grid.py` (EGRID, INIT with headers, and summary files that ResInsight loads).
- Real-ResInsight tests: console load, GUI snapshot and project, plot export, view property export, generated property, summary ensemble, and no process left after exit.
