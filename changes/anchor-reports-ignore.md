### Changed
- `.gitignore` anchors `reports/` to the repository root (`/reports/`). Before, the line also ignored `docs/reports/` and any other nested `reports/` folder, so evidence written there could never be committed. The root `reports/` folder stays ignored.
