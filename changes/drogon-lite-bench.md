---
bump: ert-ensemble-run minor
---
### Added
- `tools/drogon_lite_bench.py` grades the AHM skills on a finished drogon-lite run against its synthetic truth: compensation verdict, EDGE flags that miss the truth, predictive against noise-biased coverage, physics checklist, and the preflight gate. `make check-drogonlite` runs the full ES-MDA (marker `drogonlite`, opt-in with `RUN_DROGONLITE=1`); `DROGON_LITE_RUNS` grades existing runs.
- `ert-ensemble-run`: `long_run.py start` refuses an ES or ES-MDA update without `--screening` (runs `ahm_preflight.py`) or `--no-preflight <reason>`.
