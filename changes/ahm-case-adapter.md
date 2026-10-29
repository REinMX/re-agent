---
bump: history-match-qc minor
---
### Added

- `skills/history-match-qc/scripts/ahm_case_adapter.py` makes the inputs of `misfit_report.py` and `ahm_overview.py`
  for an exported case that has no ERT storage, no deck and no `.obs` file: runpaths with `parameters.txt` and
  SMSPEC/UNSMRY, and an fmu-tools `ert_observations.yml`. It fails closed on a partial ensemble; a known gap is
  named with `--allow-missing`, and the comparison then uses the realizations that both ensembles have.
  It rebuilds `prior.dist` from the prior sample and reference distribution files, with a provenance table and
  a KS p-value per parameter; a reference line whose bounds exclude a prior value is not used, and the
  provenance says why. A staging folder left from an earlier run with other realizations is refused. `--run` runs the misfit report and the overview.
- `references/drogon-official.md`: the public Drogon AHM benchmark (webviz-subsurface-testdata `01_drogon_ahm`)
  and a sparse fetch recipe. The data is not in the repo.
