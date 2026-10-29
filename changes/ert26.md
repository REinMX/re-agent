---
bump: ert-config-author-lint minor
---
### Changed
- Templates target ERT 26: `DATA_FILE` replaces the deck `RUN_TEMPLATE`, and the AHM template sets the update strategy per parameter type (`gen_kw_update`, `field_update`) instead of the removed `STD_ENKF LOCALIZATION`.
- Home box defaults to an ERT 26 env (`tools/box/fmu26.txt`); `docs/HOME_BOX.md` lists the ERT 26 changes that touch this repo.

### Added
- `check_ert_config.py --ahm` warns on `STD_ENKF LOCALIZATION` and `DISTANCE_LOCALIZATION` and names the per-type replacement.
- `tools/box/box_setup.sh` and `tools/box/flow_docker.sh`: idempotent restore of docker, the OPM image and a `flow` wrapper after a box restart.
