---
bump: ahm-analysis patch, ahm-workflow patch
---
### Fixed

- `screen_params.py` treats a midnight DATE suffix as the same calendar day. A different day is still a changed observation.
- The AHM_ANALYSIS prior name on an ERT 26 `ensemble_experiment` storage is `default`. The `iter-0` folder is not that name.
