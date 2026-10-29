---
bump: resinsight-plots patch, reservoir-engineer patch
---
### Added
- `resinsight-session` skill: `install_resinsight.sh` (one userspace install of ResInsight and the matching rips, with a launcher) and `ri_doctor.py` (live check: the binary runs, rips imports, major.minor match).
- Profile key `resinsight.python` (the Python that has rips) in the schema and the example contracts, with empty values.
- Pytest marker `resinsight` and `make check-resinsight RI=... RIPY=...`. These tests are deselected in `make check` and `make check-full`.
- `tools/doctor.py` checks that `resinsight.bin` and `resinsight.python` are absolute, and executable on the execution host.

### Fixed
- `resinsight.headless: offscreen` is removed from `resinsight-plots`. The Linux builds ship only the xcb Qt plugin, so offscreen never worked, and the schema did not allow it.
