### Added
- The QC chain of `templates/site/02-ahm-esmda-qc` runs the AHM preflight gate (`ahm_preflight.py`) in `setup`, with the screening record from the new `qc.yml` key `screening`. A missing screening record makes `setup` exit 1. `allow_members_reason` waives the member count only when you set it, and the step output shows the reason.
- `post` runs `coverage.py` (predictive coverage) and `compensation_check.py` after `misfit_report.py`. One seed is a diagnostic. A claim needs three seeds.
- `post` runs `heldout_score.py` when `heldout_split` is set. `summary.json` lists each step that did not run, with its reason, under `skipped`.
### Changed
- `decision_memo.md` has fields for the screening record, the preflight result, predictive coverage, the compensation verdict with its seed count, and the held-out score with its retrospective limit.
- `qc_chain.py` stops with a message when `qc.yml` has no `screening` or `random_seed` key. Render the template again.
