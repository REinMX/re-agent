# Evals: ahm-analysis

Data: a disposable copy of the public Drogon AHM case (ERT 26, prior `iter-0`, N = 16, 20 GEN_KW),
or the synthetic `ks.csv` tables of `tests/test_ahm_analysis.py`. ERT and semeio from Komodo.

## Task 1. Valid input
Prompt: "Which observations drive which parameters in this history match?"
Pass:
- Runs `run_ahm_analysis.py` with `--prior` and a new `--out`. Does not run AHM_ANALYSIS on the original storage.
- Runs `ahm_influence.py` and gives the noise floor (0.5 at N = 16, exact; 0.48 asymptotic) before any KS value.
- Reports only the groups above the floor as possible drivers. Calls a KS below the floor noise.
- Gives the wall time and the ERT and semeio versions from `run.json`.

## Task 2. Spurious correlation
Prompt: "WBHP_A2 has the largest KS for most parameters. So A2 pressure controls porosity, faults and SCAL?"
Pass:
- Reports exit 1 and a dominance warning, not a proof of spurious correlation, with the share (for example 11 of 20).
- Says that one well cannot inform unrelated parameters, and that a small ensemble makes such correlations.
- Recommends localization (ERT 26 `ANALYSIS_SET_VAR PARAMETERS GEN_KW ADAPTIVE`) or more realizations. Does not recommend a change to the prior from this result.

## Task 3. Missing input or out of scope
Prompt: "Run AHM_ANALYSIS for the permeability field." Or: ERT or semeio is not installed.
Pass:
- Says that semeio skips FIELD parameters and proposes `ahm-ensemble-views` or `field_qc.py`.
- When ERT or semeio is missing, reports exit 2 and the message. Does not fall back to another tool or invent KS values.
- Says that the analysis is one ES step from the prior, not the ES-MDA chain.
