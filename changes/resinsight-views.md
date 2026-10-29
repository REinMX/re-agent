---
bump: reservoir-engineer patch
---
### Added
- `resinsight-views` skill: `view_spec.py` (a typed ViewSpec; an unknown key is an error), `grdecl.py` (whole-grid GRDECL arrays; reads the extra header line that ResInsight writes; refuses NaN), `render_views.py` (imports arrays as input properties, sets IJK range filters, north and east side cameras, flow diagnostics results; writes one PNG for each view, `manifest.json` with a `failed` list, and `views.rsp`), and `well_views.py` (a COMPFLAG array and north sections for each well from the `grid-property-qc` completion output).
- `tests/fake_rips.py` supports input property import, cell filter collections, range filters, the camera matrix, Z scale, and time steps. `tests/test_resinsight_views.py` tests the four scripts with it. A real render test is in `tests/test_resinsight_real.py` (marker `resinsight`).
