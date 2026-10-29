---
bump: ert-ensemble-run minor
---
### Added
- `ert-ensemble-run`: `scripts/long_run.py` starts a long ERT or Flow run detached and records it in a STATUS file (`start`, `note`, `check`), so that a new session can resume. `check` detects a reused pid, appends one END line when a job died, and warns when the file is stale.
