---
bump: case-qc minor, reservoir-engineer patch
---
### Added
- `grid-property-qc` skill: `grid_qc.py` checks the INIT arrays of one case with resdata (NaN, active cells with no PORV, PORO, NTG and permeability ranges, PERMZ above PERMX, region numbers, PORV against bulk volume times PORO times NTG). It writes coded findings with sample (i, j, k) cells, `per_layer.csv`, and `per_region.csv`.
- `completion_in_grid.py` checks each COMPDAT connection (read with res2df) against the grid: outside, inactive, no PORV, no or low permeability, and wells with no active connection.
- `case-qc` runs the steps `grid` and `completions` when `BASE.EGRID` and `BASE.INIT` are next to `--deck`.
