---
bump: ahm-workflow minor
---
### Added
- `screen_params.py` takes `--observations`, a CSV of the expected observations with the columns OBS, OBSERVED, ERROR, KEY, and DATE. It writes the record `ahm-screening/2` with the prior definitions and the hash of each input file. A record made without `--observations` is a diagnostic. `ahm_preflight.py` never accepts it.
### Changed
- `ahm_preflight.py` refuses a screening record when an input file changed or is missing, when a sampled prior changed, or when the record has no independent observation list. It compares prior definitions, not file bytes, so comments, number format, file order, and moved files do not matter. A dropped parameter can become CONST.
- A record `ahm-screening/1` or a malformed record is refused with a message to run `screen_params.py` again. A parameter name that two GEN_KW files declare is an error.
### Fixed
- `screen_params.py` no longer accepts a missing member or observation, a duplicate row, a SIMULATED or ERROR value that is not finite, an ERROR that is not positive, or a design REAL that is not a unique whole number. A missing prediction no longer counts as zero sensitivity. It no longer keeps the first of two conflicting duplicate rows.
