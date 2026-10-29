# Drogon: official sources and how the AHM overview uses them

Drogon is an open synthetic FMU model. Use these sources as the reference for Drogon structure,
observations, and history-matching practice. Commits were pinned on 2026-10-17. Read them again before you
quote them on a later date.

| Source | Pin | What it gives |
|---|---|---|
| [equinor/fmu-drogon](https://github.com/equinor/fmu-drogon) | `eaeab10` (2026-09-18) | Full FMU project: `ert/model/*.ert`, `ert/input/{config,distributions,observations,templates}`, `rms/`, `eclipse/`, `fmuconfig/`, `sim2seis/` |
| [Drogon conceptual model](https://equinor.github.io/everest-tutorials/model_description/drogon_description.html) | web page | Geology, zones (Valysar, Therys, Volon), faults, wells, and drainage strategy (PDF) |
| [ERT: Localization with examples from Drogon](https://ert.readthedocs.io/en/latest/getting_started/howto/localization_examples.html) | ERT 26.0 docs | Drogon ES-MDA with 100 realizations and 3 updates. Compares global, adaptive, and distance-based localization on APS Gaussian fields |
| [ERT: A gentle introduction to history matching](https://ert.readthedocs.io/en/latest/theory/gentle_introduction_to_history_matching.html) | ERT 26.0 docs | Coverage, the ES equation (cross-covariance, innovation, `(Σyy + R)^-1`), spurious correlations, adaptive threshold `3/sqrt(N)`, distance localization |
| [equinor/fmu-drogon-flow-files](https://github.com/equinor/fmu-drogon-flow-files) | `9c8833c` (2025-08-15) | 100 Flow realizations (`realization-0..99`) and `drogon_flow_files.ert` |

## Official Drogon AHM setup (fmu-drogon `ert/model/drogon_ahm.ert`)
- `NUM_REALIZATIONS 100`, `RUNPATH <SCRATCH>/<USER>/<CASE_DIR>/realization-<IENS>/iter-<ITER>/`,
  `ECLBASE eclipse/model/DROGON-<IENS>`, `UPDATE_LOG_PATH ../output/<CASE_DIR>/update_log`.
- Parameters:
  - GEN_KW groups `GLOBVAR` (`global_variables.dist`), `MULTREGT`, `RATE` and `SWITCHES`, plus a design matrix.
  - Surfaces come from `ahm_surface.ert`.
  - APS Gaussian random FIELDs `aps_<Zone>_GRF1..3` on ERTBOX grids come from `ahm_field_aps.ert`.
- GEN_KW names in the official dist files include `MULTFLT_F1..F6` (LOGUNIF), `MULT_R<n>_<ZONE>_<ZONE>`
  (LOGUNIF), `KVKH_CHANNEL`, `FWL_CENTRAL`, `GOC_NORTH_HORST`, `FAULT_SEAL_SCALING`, `RELPERM_INT_WO/GO`,
  `*_APS_PROB_*` and `ISOTREND_*`.
- Localization: `ANALYSIS_SET_VAR PARAMETERS FIELD|GEN_KW|SURFACE ADAPTIVE` in `drogon_ahm.ert`. Exercise `11a` uses
  DISTANCE for fields and surfaces and GLOBAL for GEN_KW. `ANALYSIS_SET_VAR OBSERVATIONS AUTO_SCALE AMP*` scales the
  correlated 4D seismic amplitude data.
- Observations: `drogon_wbhp_rft_wct_gor_tracer_4d_plt.obs` holds 100 `SUMMARY_OBSERVATION` blocks and
  `GENERAL_OBSERVATION` blocks for 4D seismic (`AMP_*`). It also has an `RFT_OBSERVATION` block, a `CSV` block,
  and per-layer PLT keys (`WOPRL__1:A3`). Errors are mostly `ABS`.

## The box test case against the official setup
This case is a reduced copy for testing the tools. Do not read its numbers as Drogon results.
| Item | Official fmu-drogon | Box case |
|---|---|---|
| Deck | RMS builds the grid and properties in each realization | Public `DROGON_HIST` deck from OPM/opm-tests (pin `81215aa`). The geomodel is fixed |
| Parameters | GEN_KW + surfaces + APS fields (more than 10^5 values) | 20 GEN_KW scalars applied by `DROGON_PARAMS`: zone PERM/PORO multipliers, KVKH, 6 MULTFLT, 2 MULTZ, KRW/KROW exponents, 2 OWC shifts, aquifer strength |
| N / updates | 100 / ES-MDA 3 updates | 16 / ES-MDA 4 updates (weights 15, 7.5, 3.75, 1.875) |
| Localization | ADAPTIVE (ahm), DISTANCE (exercise 11a) | D1 none (global), D2 `STD_ENKF LOCALIZATION True` (adaptive, `3/sqrt(16)` = 0.75) |
| Observations | Real-like history with WBHP, WCT, GOR, RFT, tracer, 4D, PLT | Synthetic: truth run plus Gaussian noise at the stated error (seed 20261015). 160 summary values (WOPR, WWCT, WGOR, WBHP) and 20 RFT pressures. Points with no flow are removed |
| ECLBASE | `eclipse/model/DROGON-<IENS>` | `model/DROGON-<IENS>` (`runpath_lib` handles both) |
| Truth | Not known | Known (`ert/truth.txt`). Truth-based checks are possible here only |

Consequences:
- The overview's truth checks work only on synthetic cases such as the box case.
- With N = 16 and p = 20, the box case is rank deficient on purpose. The official setup has N = 100 and far more
  parameters, so spurious correlations matter there too. The ERT page shows that a global update strongly reduces
  the field sd, while localization keeps the update near the observations.
- On a real Drogon run, classify official names with `--param-class` when the defaults do not match.
  The defaults cover `FWL`/`GOC` (contacts), `MULTFLT`/`FAULT_SEAL` (fault), `MULT_R*` (multiplier),
  `RELPERM_*` (relperm), `KVKH*` (kvkh) and `aps_*`/`*_APS_PROB_*`/`ISOTREND_*` (facies, static).

## Why each overview check exists
| Check | Rationale (source) |
|---|---|
| HMQC-PRIOR-PREDICTIVE | The prior's simulated responses should span the observations ("good coverage"). Large systematic misses force large changes to the prior, and the update does not guarantee geological plausibility (gentle introduction, AHM section) |
| HMQC-SPURIOUS-UPDATE, HMQC-INFLUENCE-RANK | Sample cross-covariances are noisy when N is small relative to the number of parameters. Spurious correlations move parameters that no data constrain (gentle introduction, spurious correlations; Drogon localization page) |
| Influence (K = Σxy (Σyy + R)^-1, DFS) | The update strength is proportional to the cross-covariance and the innovation, and inversely proportional to the innovation covariance. Correlated responses carry less information (gentle introduction, ES equation) |
| HMQC-OBS-DOMINANT, HMQC-OBS-CONFLICT | Each group's contribution to the update is K restricted to that group times its innovation. Groups whose contributions point in opposite directions pull against each other |
| HMQC-NOISE-GROUP, HMQC-NOISE-TRUTH | R is set by the user and assumed diagonal. An R that is too small makes the update too strong (gentle introduction, observation error covariance) |
| HMQC-NOISE-CORRELATED | Independent errors are "somewhat reasonable" for production data, but not for correlated data. Correlated residuals mean fewer independent observations than counted (gentle introduction; official AUTO_SCALE for 4D) |
| HMQC-STATIC-DYNAMIC, attribution | ERT updates one parameter group at a time (scalar, surface, field). The overview reports how much of the parameter shift and the forecast change comes from static properties and how much from dynamic ones |
| Adaptive threshold in numbers | ERT treats a correlation as significant above `3/sqrt(N)`. At N = 16 that is 0.75, which explains why D2 barely updates |

## Public benchmark: webviz-subsurface-testdata `01_drogon_ahm`
A real Drogon ES-MDA run from 2021: iter-0 (prior, 100 realizations) and iter-3 (posterior, 99; realization-91 has
no iter-3 folder). It has parameters.txt, SMSPEC/UNSMRY and 88 summary observations (WBHP, WGOR, WWCT), but no ERT
storage, no iterations 1-2, no prediction runs and no distribution files. The scalar GEN_KW priors match
fmu-drogon `ert/input/distributions/global_variables.dist` (commit 18029b15d7) except the three MULTREGT
multipliers, which `ahm_case_adapter.py` rebuilds as LOGUNIF 1e-4 1 from the prior sample.
Fetch only what the overview needs (about 380 MB of the 1.15 GB folder; do not commit it):
```bash
git clone --filter=blob:none --no-checkout --depth 1 --sparse \
  https://github.com/equinor/webviz-subsurface-testdata.git drogon-public
cd drogon-public
git sparse-checkout set --no-cone '/01_drogon_ahm/share/observations/tables/' \
  '/01_drogon_ahm/realization-*/iter-*/parameters.txt' \
  '/01_drogon_ahm/realization-*/iter-*/eclipse/model/*.SMSPEC' \
  '/01_drogon_ahm/realization-*/iter-*/eclipse/model/*.UNSMRY'
git checkout master
```
Do not run `git ls-tree -l` in the partial clone: it fetches each blob one at a time.
