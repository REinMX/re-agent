---
bump: history-match-qc minor, case-qc minor, ahm-workflow patch
---
### Added
- Add `compensation_check.py`: flags parameters at prior edges, posterior means outside the prior bulk, and new posterior trade-offs, and calls a run `compensating` when that comes with a good fit. Bench on drogon-lite: the base setup is compensating in 3 of 3 seeds, the screened setup plausible in 3 of 3.
- case-qc runs the compensation check when it has a prior, a posterior and a config with GEN_KW files.
