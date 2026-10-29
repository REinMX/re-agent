---
name: uncertainty-register
version: 0.1.0
description: Draft an uncertainty register from FMU design matrix and GEN_KW priors (parameters.csv, *priors.txt) named in the site contract. Diffs against a previous register. Makes one-by-one sensitivity designs and run-based static/dynamic attribution with tornado reports. Never invents missing ranges or percentiles from design samples. Analyst role. Does not start Flow or ERT.
---

# Uncertainty register

`scripts/uncertainty_register.py` reads project prior files only and writes
`deliverables/uncertainty_register/register.csv` and `register.md`.

Exit code 0 means the register was written (gap rows may be listed).
Exit code 2 means no prior sources were found, a listed file is missing, or the contract is bad.

## When to use
- Before a gate packet or QA/QC pre-read that needs an uncertainty list.
- After someone changes `parameters.csv` or GEN_KW prior files.
- To prove a parameter has no prior (status `gap`) instead of inventing a range.

## Sources (from the contract)
| Contract key | Role |
|---|---|
| `uncertainty.parameters_csv` | Primary prior table (group, name, distribution, arg1, arg2) |
| `uncertainty.prior_files` | GEN_KW `*priors.txt` files |
| `uncertainty.design_matrix` | Names only (CSV or XLSX; samples are not used as priors) |

Relative paths resolve against `paths.root`. `--parameters-csv`, `--priors`, and `--design-matrix` override.

## Step 1. Contract
Run `stage-contract`. State the contract `p10_p90` convention for ensemble bands.
GEN_KW priors such as UNIFORM / LOGUNIF give min/max. Leave percentile columns blank. Do not fake them.

## Step 2. Draft the register
Use the `python` of the contract profile:
```bash
python skills/uncertainty-register/scripts/uncertainty_register.py --contract site.contract.yml
```

## Step 3. Diff against the previous register
```bash
python skills/uncertainty-register/scripts/uncertainty_register.py --contract site.contract.yml --diff
```
Writes `register_diff.md` and `register_diff.json`. Snapshots the previous CSV.

## Columns
`name, group, distribution, min, max, p10, p50, p90, source_file, status, notes`

`status` is `ok` or `gap`. A gap means missing distribution or min/max, or a
design-matrix name with no prior. Never fill a gap with a guessed range.

## Rules
- Read project files only. Do not invent parameter ranges.
- Do not compute P10/P50/P90 from design-matrix sample columns.
- Do not start Flow, ERT, or Everest.
- Do not write inside a runpath or scratch realization folder.

## Step 4. Make a sensitivity design

Use the configured Python with `fmudesign` for ERT 26 or `semeio.fmudesign` for ERT 23.
The script uses `fmudesign` first. If that package is absent, it uses `semeio.fmudesign`.
The script fails if both packages are absent or the selected package has an import or API error.
Use numerical prior percentiles. P10 is the low value. P90 is the high value.
`CONST` controls stay fixed in every design row and in the default values.
Only sampled parameters have low and high sensitivity rows.
These values come from the prior distribution, not from design samples.
One-by-one ignores interactions.

```bash
python skills/uncertainty-register/scripts/make_design.py --dist priors.dist --mode onebyone --out deliverables/design
```

The command writes `fmudesign.xlsx` and `design.xlsx`.
It prints `NUM_REALIZATIONS` and the native ERT `DESIGN_MATRIX` line.
Paste these lines into a copy of the ERT config.
Do not add `DESIGN2PARAMS` or `DESIGN_KW`.
ERT writes these scalar names without the old GEN_KW group prefix.
Check the parameter reader in the copied forward model.
Keep other inputs fixed across the design runs.

For attribution, use `--mode attribution --groups 'static=PORO_*,OWC_*;dynamic=PERM_*,MULTFLT_*,KR*,SCAL_*'`.
Each sampled parameter must match exactly one group. Both groups must have sampled parameters.
Fixed controls do not need a group.
The four rows are base, static, dynamic, and all.
The static and dynamic rows use the same sampled values as the all row.
Use `--seed` to repeat the sample. The default is 42.

## Step 5. Run the design

When the user requests a run, use `ert-ensemble-run`.
Show the exact command and record it with `earned-autonomy`.
Keep the source case unchanged. Write the run in the approved scratch folder.

```bash
ert ensemble_experiment sensitivity.ert
```

## Step 6. Read the run contrasts

One-by-one ignores interactions. Rank parameters by the absolute high-to-low span.
Require the ERT `OK` marker for each realization.

```bash
python skills/uncertainty-register/scripts/tornado.py --runpath-glob 'scratch/realization-*/iter-0/model/CASE-*' --design deliverables/design/design.xlsx --vector FOPT --date last --out deliverables/tornado
```

The pattern ends at ECLBASE, without a file extension.
Use `--date YYYY-MM-DD` to select a common date.
The script uses the date reader from `ensemble-at-date`.
It writes `tornado.csv`, `tornado.png`, `tornado.md`, and `responses.csv`.
The report gives the vector, unit, date, member count, and missing members.
Missing runs give exit 1. Invalid input gives exit 2. Success gives exit 0.
Use `--allow-partial` only to keep complete low/high pairs. The base is required.
Attribution requires all four runs, also with `--allow-partial`.

For attribution, the output gives static, dynamic, and interaction deltas.
The interaction is `all - static - dynamic + base`.
Each signed share is its delta divided by `all - base`.
Shares are undefined when `all` equals `base`.
The split comes from runs. It is not a linear estimate.
