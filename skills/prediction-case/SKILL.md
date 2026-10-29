---
name: prediction-case
version: 0.1.0
description: Generate prediction case folders from the project ERT template and parameters.csv named in the site contract — rendered .ert (NUM_REALIZATIONS / RANDOM_SEED / CASE_DIR, relative paths rebased) and a sampled design_matrix.csv. Never invents prior ranges. Does not start ERT or Flow. Analyst role for prediction churn.
---

# Prediction case (generate only)

`scripts/prediction_case.py` writes `<prediction_case.cases_root>/<name>/` with:

| File | Role |
|---|---|
| `<name>.ert` | Template with nreal / seed / CASE_DIR; relative paths rebased to the case folder |
| `design_matrix.csv` | Sampled from copied prior min/max (seeded) |
| `parameters.csv` | Exact copy of the priors (source of truth) |
| `case_meta.json` | Audit: seed, groups, sources |
| `README.md` | How to run later; uses the profile `activate` and a path relative to `paths.root` |
| `priors_snapshot/` | Copy of contract `uncertainty.prior_files` |

It does not launch ERT or Flow. Missing or incomplete priors give exit 2 (never invent ranges).

## When to use
- New prediction ensemble size or seed without hand-editing `.ert`
- Churn design matrices for what-if prediction cases

## Sources (from the contract)
| Contract key | Role |
|---|---|
| `prediction_case.template_ert` (else `paths.ert_config`) | Template `.ert` |
| `prediction_case.parameters_csv` (else `uncertainty.parameters_csv`) | Prior min/max |
| `prediction_case.cases_root` | Output root |
| `uncertainty.prior_files` | GEN_KW priors copied into the snapshot |

`--template`, `--parameters-csv`, and `--cases-root` override the contract.

P10/P90: design-matrix columns are samples from min/max, not percentiles.
Ensemble bands elsewhere use the contract `p10_p90` convention.

## Generate a case
Use the `python` of the contract profile (numpy):
```bash
python skills/prediction-case/scripts/prediction_case.py --contract site.contract.yml \
  --name pred_A --nreal 20 --seed 1
```

Subset of groups in the design matrix only (for example ROCK):
```bash
python skills/prediction-case/scripts/prediction_case.py --contract site.contract.yml \
  --name pred_rock_only --nreal 100 --seed 3 --groups ROCK --overwrite
```

## Rules
- Copy priors from `parameters.csv` / prior files only.
- Do not invent min/max or P10/P50/P90.
- Do not start ERT, Flow, or Everest from this skill. Use `ert-ensemble-run` when the user asks for a run.
- Overwrite only with `--overwrite`.
- RMS wells and zones are not generated here. Queue settings come from the contract profile.
