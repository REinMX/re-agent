---
bump: scenario-qc minor, reservoir-engineer patch
---
### Added
- New skill `scenario-build`: `scripts/scenario_build.py` renders a typed scenario spec (infill, producer to
  injector, WAG, workover/reperforation, well and group targets incl. gas rate, gas lift and ALQ, network
  back-pressure, date shifts) into an ECLIPSE100 SCHEDULE include on a copy of the case. It keeps the base SCHEDULE
  text without comments and splits TSTEP/DATES only at operation dates. It runs the flow-preflight deck_support
  check on the result and writes `runmeta.yml` with the changed keywords, touched wells and `expected_changes` for
  scenario-qc.
- Spec `variables` (`"${name}"`, with initial value and bounds) make the numbers Everest controls.
  `scripts/everest_controls.py` writes the Everest `controls:` section; `--controls <name>.json` renders one
  control point.
### Changed
- `scenario-qc`: when runmeta declares `expected_changes.keywords`, a changed keyword or a new well that the
  declaration does not list is a `fingerprint_mismatch`. Before, one declared keyword let other changes pass.
