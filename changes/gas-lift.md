---
bump: scenario-build patch, scenario-qc patch, reservoir-engineer patch
---
### Added
- New skill `gas-lift`.
- `gas_lift_qc.py` checks VFPPROD ALQ axes, the ALQ definition and units, WLIFTOPT and WELTARG LIFT values against the ALQ axis, LIFTOPT, GLIFTOPT groups and the VFP lift response. It reports Flow restrictions by target through deck_support.
- `gas_lift_diag.py` reads WGLIR, GGLIR, FGLIR and WOPR from SUMMARY. It reports lift gas per well, days at the maximum, wells with no lift while optimized or lift without oil, and whether GLIFTOPT limits held. A missing vector stays missing.
- `gas_lift_sweep.py` builds scenario-build decks at several lift-gas levels. It then collects the oil gain, the marginal oil per lift gas against the LIFTOPT economic gradient, and a verdict per well.
### Changed
- `scenario-build` `gas_lift` accepts a defaulted WLIFTOPT `max_rate`, which means the largest ALQ of the VFP table. It writes the ECLIPSE100 items 6 (`gas_weight`) and 7 (`optimize_limit`), and deck_support flags them for box Flow.
### Fixed
- `scenario-qc` deck_fingerprint resolves PATHS aliases (`$ALIAS/file`) in INCLUDE paths, so scenario-build and the fingerprint can read decks such as the OPM MODEL05 gas-lift tests.
