---
bump: reservoir-engineer patch
---
### Changed
- `collapse_checks.misfit_vs_chi2` takes the mean member spread s (var(SIMULATED) / ERROR², ddof 1) and adds `spread`, `spread_expected` (1 + 2 s), `spread_upper` (1 + 2 s + k·sqrt(2/n)) and `spread_verdict`. `verdict` keeps its noise-only meaning, so `compensation_check.py` reads it unchanged. A posterior above `spread_upper` is the new finding "misfit above the spread-aware reference". `misfit_report.py` computes s from `obs_summary` and prints both readings on the `CHI2` line.
- `residual_correlation.py` uses spread-aware references: `HMQC-OBSERR-BIAS` when |mean r| > 3·sqrt(1/n + s), `HMQC-OBSERR-VARIANCE` when mean(r²) > 1 + 3·sqrt(2/n) + 9 s. The noise component uses s = 0. `HMQC-OBSERR-AUTOCORR` moves to a new `review` list and is never a finding. Each series reports `spread` and `bias_bound`. Schema `ahm-residual-correlation/2`.
- history-match-qc SKILL.md describes both rules.
### Added
- `tests/test_ahm_misfit_specificity.py`: a well-calibrated wide ensemble no longer fires chi2 or OBSERR; a narrow ensemble with too-small errors still does; AUTOCORR is review-only.
