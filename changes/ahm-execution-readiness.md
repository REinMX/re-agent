---
bump: ahm-workflow patch, ert-ensemble-run patch, history-match-qc patch
---
### Fixed
- AHM preflight validates the command that will run. It refuses negative, non-finite, nonnumeric, and all-zero ES-MDA weights. It checks the selected realizations. A present OBS_CONFIG must match the screened summary observations. The launcher uses that validated command.
### Changed
- A config without OBS_CONFIG stays a compatibility check. The report says that the observations were not bound. Native ERT loading stays unverified.
