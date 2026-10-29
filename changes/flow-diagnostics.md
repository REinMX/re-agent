---
bump: reservoir-engineer patch
---
### Added
- `flow-diagnostics` skill: `fd_preflight.py` (resdata: finds the report steps with FLORES or FLOWS fluxes, reads each step by its own restart view, finds injectors and producers from the summary rates; with no fluxes exit 2 and the message names `RPTRST BASIC=2 FLORES` and the expected UNRST size), `fd_export.py` (rips: one GRDECL for each injector and producer fraction and for `RESTIME`, `fd_export.json`, and a ViewSpec), and `fd_metrics.py` (F-Phi and the Lorenz coefficient from `RESTIME` and resdata PORV; an inferred injector share for each producer from the connection cells).
- `tests/test_flow_diagnostics.py` tests the three scripts with synthetic resdata cases and the fake rips. A real test in `tests/test_resinsight_real.py` runs when `RI_FD_CASE` names a case with fluxes.
- `tests/fake_rips.py`: `view.export_property` writes one value for each grid cell and the tracer header line, and takes its values from `Instance.view_function`. `tests/synth_grid.py` writes a restart file and a summary with well rates.
