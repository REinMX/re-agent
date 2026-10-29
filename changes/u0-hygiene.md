---
bump: ahm-workflow patch
---
### Changed
- `tools/check_skills.py` fails when a skill has no row in `docs/FEATURE_MAP.md`. Added the missing rows for
  `everest-config-author-lint`, `everest-diagnostics` and `flow-preflight`.
- The `ahm-workflow` rules now say to start a run through `ert-ensemble-run` when the user asks, and to show the
  exact command. The old line "do not run ert/everest/flow/bsub" contradicted Step 4 and the removal of the
  simulator approval gates in 0.3.1.

### Fixed
- `tests/test_hm_localization.py::test_lint_legacy_switch_is_warning` skips on ERT older than 26, which does not
  emit the switch message. It failed on `main` with ERT 23.
