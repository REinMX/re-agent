---
bump: reservoir-engineer patch, ert-config-author-lint patch
---
### Added
- `hm-localization` skill for ERT 26 distance localization of FIELD and SURFACE updates.
  - `obs_locations.py`: well EAST and NORTH from res2df COMPDAT (mean of the OPEN cells) and WELSPECS heads, with xtgeo cell centres.
  - `localize_obs.py`: adds or replaces `LOCALIZATION` blocks on well observations (also bulk `SUMMARY` WELL entries) token by token. Lists field and group keys, which a DISTANCE update excludes. Checks the output with ERT's own parser before it writes.
  - `loc_lint.py`: update strategy per parameter, located and unlocated observations, the radius against the 3000 m default, the field extent and the cell size, locations outside the field box, GEN_KW with DISTANCE, legacy `STD_ENKF LOCALIZATION`.
  - `radius_sweep.py`: replays one ES update per radius from a copy of the storage with ERT's `smoother_update` (no simulation). Writes the sd ratio, the collapse share, the shift and, with a truth field, the coverage and the RMSE.
- `tests/test_hm_localization.py`. The real sweep test runs with `HM_LOC_STORAGE` and `HM_LOC_CONFIG`.
