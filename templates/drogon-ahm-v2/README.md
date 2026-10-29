# Drogon AHM v2

This public example replaces zone permeability multipliers with a localized log-permeability FIELD, replaces global power-law SCAL with facies-group LET interpolation, and makes contact shifts consistent with SWATINIT. It uses ERT 26 native RFT and breakthrough observations.

The synthetic truth remains the earlier power-law model, with KRW_EXP 1.25 and KROW_EXP 0.85. Its model error relative to LET interpolation is deliberate. Summary values, the 20 noisy RFT pressures, and all their errors remain unchanged. Four new WWCT breakthrough dates use the first truth sample at or above 0.1, with an assumed 30-day error. Dates are not interpolated between samples.

## Apply the overlay

Use Python with ERT 26.0.2, pyscal 0.18.0, semeio 3.4.1, res2df 1.3.17, resdata 6.3.5, xtgeo 4.27.0, scipy, numpy, pandas, and subscript 1.16.2. Put that Python's bin folder and `flow` on PATH. Missing packages are errors, never an alternate physical model.

The input is the earlier public Drogon AHM case and its existing synthetic truth. The source case contains `deck/model/DROGON_AHM.DATA`, `deck/include/`, and `ert/obs/drogon.obs` with its `rft_*.obs` files. The truth ECLBASE has DATA, EGRID, INIT, RFT, SMSPEC, and UNSMRY files. Do not open earlier ERT storage with ERT 26.

The public deck is [OPM/opm-tests Drogon](https://github.com/OPM/opm-tests/tree/81215aa051dea6b3a5406096cd673543ceef8e6c/drogon). Copy its `model`, `include`, and `include_pred` folders into a separate baseline case's `deck/upstream/`. `tools/make_deck_baseline.py --case <baseline>` shows the original include replacements. It builds AHM and prediction DATA files from this upstream layout and copies the upstream includes. No deck files are distributed here. The upstream ODbL and Database Contents License apply to the model and derived recommendation tables. Copyright 2022 Equinor. The `ert/truth.txt` parameters and baseline jobs reproduce the earlier parameter transformation. `tools/make_obs_baseline.py` documents its fixed noise seed and observation selection.

From the repository root, prepare a fresh copy. The destination must not exist. Preparation never reads a storage and never starts Flow.

```bash
python templates/drogon-ahm-v2/tools/prepare.py \
  --source <baseline-public-case> --case <new-case> \
  --truth <existing-truth>/model/DROGON_AHM --scratch <new-scratch>
```

The command copies the source without storage, output, or scratch folders. It installs the jobs, patches the permeability and SWATINIT includes, builds static arrays and 16 seeded fields, and creates native observations from the existing noisy values. Static cell arrays and grid files stay outside git.

To refit the recommendation from the public deck, use this command from the repository checkout. The fitter reuses `scal-uncertainty/scripts/satfunc_fit.py`.

```bash
python templates/drogon-ahm-v2/tools/make_scal_rec.py \
  --deck-sattab <baseline-public-case>/deck/include/props/drogon.sattab \
  --out <fit-output>
```

Run one smoke realization in a separate copy of `after.ert` with its own CASE name. Then run the paired experiments from `<new-case>/ert`. Each performs a prior, one ES update, and a posterior, with 16 members, seed 20261015, LOCAL queue, and MAX_RUNNING 3.

```bash
ert ensemble_smoother --target-ensemble 'iter-%d' --disable-monitoring --experiment-name before before.ert
ert ensemble_smoother --target-ensemble 'iter-%d' --disable-monitoring --experiment-name after after.ert
```

Wrap each command with `skills/earned-autonomy/scripts/worklog.py run --kind sim --sims 32` when using the repository workflow. Record wall time and exit status as `output/<name>/wall.txt`. The measured runs used `run_es.sh`, which records both. `DROGON_POST` writes subscript QC before removing each UNRST except realization 0. This restart removal is part of the experiment's disk budget. Other simulator outputs remain. Keep at least 20% disk free.

## Model choices and checks

| Component | Choice | Evidence or limitation |
|---|---|---|
| SCAL groups | CH_USF 1,3,7,9; CR 2,8; LSF 4,10; OFF_FP 5,6,11,12 | Fitted WO and GO LET shapes within 5%, SWL within 0.05; endpoints and Pc stay SATNUM-specific |
| SCAL base | LET fitted to all SWOF and SGOF curves | Maximum kr error 2.149304e-6 after CSV and table resampling; maximum Pc error above SWL 9.674032e-6 |
| SCAL range | SORW and SORG ±0.05; endpoint and LET L/E perturbations in `make_scal_rec.py` | Assumed engineering range, no laboratory data; all truth krw/krow nodes inside low/high envelope for all 12 SATNUMs |
| SCAL job | One WO and GO scalar per group, eight UNIFORM [-1,1] parameters | Maps groups to an ordered per-SATNUM list for pyscal; semeio PYSCAL accepts one group per file |
| FIELD | LNPERM is a dimensionless natural log multiplier, no ERT transforms | PERMX/PERMY = base × exp(LNPERM); PERMZ also × KVKH_MULT |
| FIELD prior | Zone-independent Gaussian FFT filter, population sd ln(3)/2, 750 m lateral and 5 m vertical scale | Seeded 20261015+i; approximate grid spacing from median DX/DY/DZ; periodic FFT boundaries; zone means remain uncertain |
| FIELD localization | DISTANCE, observation radius 1000 m, GRID is the truth EGRID | Radius is assumed, not a radius-sweep optimum. All summary, RFT, and breakthrough observations have locations |
| Scalars | GLOBAL | Earlier N=16 adaptive experiment barely updated; GLOBAL remains vulnerable to scalar collapse |
| SWATINIT | Binned median saturation-height curve per EQLNUM, monotone with height | 1 m bins; remaps crossing/transition cells and retains geomodel values beyond transition; below new FWL is exactly 1 |
| Transition limit | Largest bin with Sw more than 0.01 above the high-column plateau | Assumed screening rule; offset interpolation preserves heterogeneity inside the transition |
| RFT mechanism | Same four depth-selected connections and survey dates | On one full simulation, native pressure versus old four-decimal extractor differs by at most 4.44336e-5 bar across 20 points |
| ERT parser | RFT LOCALIZATION precedes EAST/NORTH | ERT 26.0.2 otherwise clears those coordinates; regression test checks actual observation objects |
| Table export | Remove only equal printed saturation nodes with equal values within 1e-6 | pyscal rounding can duplicate SWCR nodes; conflicting or decreasing nodes are errors |

The base geomodel has a separate SWL truncation issue of about 2% hydrocarbon volume. This example does not redefine the base SWL endpoints to remove it. Subscript percentages use the signed hydrocarbon change convention. HC_BELOW_FWL and SWL_TRUNC losses are negative percentages of the input SWATINIT hydrocarbon reservoir volume, in rm³.

## Measured experiment

Both runs finished on 2026-10-17 with ERT 26.0.2, N=16, one ES step, seed 20261015, MAX_RUNNING 3. All numbers below are measured unless the line says inferred. Raw output is in `results/comparison.json` and `results/breakthrough_contacts.json`. Wall times are 2947 s for BEFORE and 2134 s for AFTER on a shared, loaded box.

Misfit is mean squared normalized residual per member, then mean (median) over members. COMMON is the 180 observations both setups share (summary plus 20 RFT pressures).

| Family | n | BEFORE prior | BEFORE posterior | AFTER prior | AFTER posterior |
|---|---|---|---|---|---|
| COMMON | 180 | 45.79 (37.45) | 5.47 (5.46) | 63.36 (53.47) | 15.97 (13.02) |
| RFT | 20 | 10.36 | 4.02 | 9.53 | 5.33 |
| WBHP | 52 | 85.23 | 8.71 | 90.14 | 10.72 |
| WGOR | 36 | 63.75 | 3.12 | 117.95 | 42.60 |
| WOPR | 36 | 9.17 | 4.20 | 10.56 | 7.43 |
| WWCT | 36 | 27.18 | 5.23 | 52.79 | 11.36 |
| BREAKTHROUGH | 4 | not observed | not observed | 36.38 (5 missing) | 11.47 (7 missing) |

A breakthrough that never happens has no response. It is counted as missing and left out of the score, so the BREAKTHROUGH row flatters AFTER.

| Check | BEFORE | AFTER |
|---|---|---|
| Scalars with posterior/prior sd < 0.2 | 11 of 20 | 16 of 23 (8 SCAL scalars included) |
| Scalar truth inside P5-P95, prior to posterior | 0.95 to 0.35 (20) | 0.93 to 0.40 (15 shared) |
| FIELD median sd ratio, cells < 0.2 | none | 1.00, 0 of 70972 |
| FIELD truth inside P5-P95, prior to posterior | none | 0.880 to 0.871 |
| HC_BELOW_FWL % HC, prior worst | -17.65 | -0.0041 |
| HC_BELOW_FWL % HC, posterior worst | -0.018 | -0.00037 |
| SWL_TRUNC % HC, posterior range | -2.01 to -1.97 | -1.30 to -1.20 |
| OWC_CENTRAL posterior mean, P5-P95 (truth 6) | 8.91, 7.48 to 9.86 | 4.10, 2.91 to 5.33 |
| OWC_FLANK posterior mean, P5-P95 (truth -4) | 9.00, 6.93 to 10.99 | -3.79, -5.14 to -1.75 |

Posterior WWCT 0.1 breakthrough, median simulated minus truth date in days, members within 30 days, members that never break through. These come from the report-step summary, so the resolution is about one month.

| Well | BEFORE | AFTER |
|---|---|---|
| A1 | +40, 6, 0 | +25, 7, 0 |
| A2 | -37, 1, 0 | +8, 6, 0 |
| A3 | +39, 3, 0 | +135, 1, 0 |
| A4 | -63, 3, 1 | -30, 3, 7 |

Reading. AFTER is not a better history match after one step. Its COMMON misfit is about three times BEFORE, and WGOR is the main gap. AFTER largely fixes the contact bias. BEFORE reached its low misfit with OWC_CENTRAL 2.9 m and OWC_FLANK 13 m from truth, both outside P5-P95, a compensating error. AFTER puts the flank contact on truth and the central contact 1.9 m short, and SWATINIT stays consistent in every member. The FIELD barely moved. About half the active cells changed at all, by 0.05 in ln on average against a prior sd near 0.55. Inferred cause, the GLOBAL scalars take the update at N=16 and the 1000 m radius limits what the field can absorb. The WGOR gap is inferred to come partly from the deliberate model error (power-law truth, LET interpolation). Untested next steps are more members or ES-MDA, ADAPTIVE scalars and a radius sweep. Do not interpret the one-member smoke test as ensemble evidence.

`tools/breakthrough_contacts.py` reads the member summaries and parameters.

```bash
python templates/drogon-ahm-v2/tools/breakthrough_contacts.py \
  --scratch <new-scratch> --bt-obs <new-case>/ert/obs/after.obs \
  --truth-params <new-case>/ert/truth.txt --out <report>/breakthrough_contacts.json
```

`tools/compare.py` reads the new ERT 26 storages in read-only mode and uses ERT's native observation/response matching. It refuses incomplete member sets or missing predictions. Misfit is mean squared normalized residual over all configured observations, including observations deactivated during the update. COMMON excludes only breakthrough. Each scalar spread ratio uses physical parameter values from the run's `parameters.txt`. SCAL interpolation scalars have no scalar truth equivalent and are excluded from truth coverage. FIELD coverage uses the original truth zone multipliers mapped onto active cells. P5 and P95 are numerical, linear percentiles.

```bash
python templates/drogon-ahm-v2/tools/compare.py \
  --case <new-case> --scratch <new-scratch> \
  --truth-params <new-case>/ert/truth.txt --out <report>/comparison.json
```

At N=16, both ensembles are below the repository's 20-member trust threshold. A lower misfit does not establish parameter recovery or convergence.

## Repository checks

The example lives in `templates` because source export already includes that folder and excludes `examples`. It is not a skill and adds no routing entries. The installer copies templates, so these files must be tracked. The clean-worktree installer-read test verifies that constraint. `tests/test_drogon_ahm_v2.py` checks job behavior on synthetic arrays and real pyscal/ERT APIs when available. Real simulations stay outside the repository.
