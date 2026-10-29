---
bump: earned-autonomy minor
---

### Added
- Add opt-in Linux runtime and aggregate allocated-disk budgets to the existing worklog `run` command. Complete an initial disk sample before launch, refuse unknown or over-budget roots, and stop the owned process group on a limit or monitor error.
- Record budget limits, stop reasons, sampled maximum allocation, actual child exit code, and cleanup errors without changing unbounded runs or old event reports. Exclude prelaunch refusals from simulator time and run counts. Document sampling and process-group containment limits.
- Test budget validation, initial refusal, fresh output roots, real file growth, sparse files, hard links, symbolic links, runtime stops, helper cleanup, inspection failures, and unrelated-process survival with harmless synthetic processes.

### Fixed
- Skip entries that disappear between directory enumeration, stat, and child-directory open. Keep other observation failures fatal and retain symbolic-link and path-component checks.
- Handle INT, TERM, and HUP during budgeted runs through group cleanup and receipt writing. Record the first signal and return `128 + signal` while retaining the actual child status and any earlier budget stop reason. Restore prior signal handlers after completion or error. Keep unbounded runs unchanged.
- Add deterministic disappearance tests, bounded real file and directory churn, signal tests with independent owned-group cleanup, repeated-interrupt and launch-window regressions, handler restoration, and unbounded exit-code controls.
