---
bump: history-match-qc minor, deck-scal-pvt-qc minor, reservoir-engineer patch
---
### Added
- `obs_qc.py --deck <DATA>`: a well timeline from the deck SCHEDULE (res2df `wcon`, `WELOPEN`, `COMPDAT`). A well observation in a report step when the well is not open yet, SHUT or STOP, has a zero history rate, or has no open connection is the warning `HMQC-OBS-NOFLOW` (lesson G9). The rule is in `skills/history-match-qc/references/schedule-noflow.md`. res2df missing or a deck that does not parse is exit code 2.
- `schedule_qc.py --parser opm` (the default): the schedule rules on the deck that the OPM parser reads through res2df, with INCLUDE files and DIMENS as Flow reads them (lessons G1 and G2). A parse failure is `L001`, not a fallback. `--parser text` keeps the text scan. The report names the parser. `well_timeline` and `well_state` give the flow state of each well.
- `deck_qc.py` `T002` (more tables than `TABDIMS` NTSFUN or NTPVT; res2df reads only the first ones) and `T003` (a PVT table with more rows or records than NPPVT or NRPVT; Flow accepts it, Eclipse stops). Saturation rows above NSSFUN stay `S010`.
- `tests/test_res2df_qc_real.py`: Drogon and Egg runs that skip unless `DROGON_DECK`, `DROGON_OBS`, `DROGON_G9_OBS`, or `EGG_DECK` is set. Lesson fixture `tests/data/lessons/shut_wells/`.

### Changed
- `deck_qc.py --deck` runs the schedule rules on the parsed deck with its INCLUDE files, not on the text of the main file.
- `tools/check_skills.py` accepts a deleted skill when a `### Removed` bullet names it.

### Removed
- `ensemble-profile-compare`: its `fmu-well-profiles`, `fmu-region-volumes`, and `fmu-compare-scenarios` commands are not installed and no repository defines them. The router sends well profiles to `ensemble-profiles`, region volumes and pressure to `region-in-place`, and two-ensemble comparisons to `ensemble-at-date`.
