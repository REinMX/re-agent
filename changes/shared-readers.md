---
bump: ensemble-at-date patch, ensemble-bands patch, ensemble-profiles patch, ert-diagnose patch, history-match-qc patch, region-in-place patch, res2df-resdata-extract patch, resinsight-plots patch, scenario-qc patch, stage-contract patch, yearly-profiles patch
---
### Changed

- `skills/stage-contract/scripts/runpath_lib.py` is the one reader for runpaths, realization specs and DATA files.
  `read_ensemble` returns the members found, the members dropped with a reason (no realization number, no or
  more than one DATA file), the expected members with no folder, and the members outside the expected set
  (reported, not used). The ERT ECLBASE layout `model/DROGON-<IENS>` now works in ensemble-at-date,
  ensemble-profiles, yearly-profiles and res2df-resdata-extract, which looked only in `eclipse/model`.
  Ten scripts import it or `contract_lib.py` instead of keeping their own copies. Realization specs are parsed
  strictly everywhere: `5-2` and `0-2,,4` are errors, not an empty or silent selection. region-in-place
  requires `contract_lib.py` and no longer falls back to plain YAML. The 11 migrated skills get a patch bump.
- `make helpers` (part of `make check`) fails when a script defines a function that `runpath_lib.py` or
  `contract_lib.py` owns.
