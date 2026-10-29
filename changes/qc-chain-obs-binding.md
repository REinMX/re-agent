### Fixed
- The site `02-ahm-esmda-qc` chain tests build the screening record from the pack's own `OBS_CONFIG` observations. The tests had paired a record for other observations with the pack, which the AHM preflight observation binding now correctly refuses. A new test checks that refusal. The tests now expect the exact command and members note that the preflight prints.
