---
bump: uncertainty-register patch
---

### Fixed
- Use `semeio.fmudesign` with ERT 23 when top-level `fmudesign` is absent. Keep the existing generator and native XLSX exporter. Preserve the ERT 26 import path and report backend dependency or API errors without fallback.
- Preserve numeric `CONST` controls in sensitivity designs and default values. Exclude fixed controls from low and high sensitivity rows, realization counts, attribution groups, and random draws.
- Read generated workbooks through ERT's native `DESIGN_MATRIX` configuration entry point in tests. Verify both design modes, fixed controls, missing dependencies, and the top-level backend API.
