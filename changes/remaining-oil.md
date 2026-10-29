---
bump: reservoir-engineer patch
---
### Added
- `remaining-oil` skill: `remaining_oil.py` (resdata: mobile oil = PORV * max(1 - SWAT - SGAS - SOR, 0) at one report step, read from the restart view of that step; column sums; a greedy pick of infill candidates by a drainage window, at least `--min-distance` cells from each connection, each `--avoid` point, and each earlier candidate; GRDECL arrays and a ViewSpec for `render_views.py`). A column with no mobile oil is never a candidate. The output folder must be outside the case folder.
- `tests/test_remaining_oil.py` tests the script on synthetic resdata cases. A real test in `tests/test_resinsight_real.py` renders its views.
