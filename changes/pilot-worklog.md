---
bump: earned-autonomy minor, ert-ensemble-run patch, flow-preflight patch
---
### Added
- `earned-autonomy/scripts/worklog.py`: an append-only work log per task. `run` times a simulator command and records its exit code. `phase` marks analysis and downtime. `report` gives sim, analysis and total hours per task as Markdown or CSV, each marked measured or inferred. `rate` holds the human's verdict and estimate of hours saved.
- `worklog.path` in the site contract. The log refuses a path that git would commit.
- `ert-ensemble-run`, `flow-preflight` and `AGENTS.md` tell the agent to start runs through the log.
