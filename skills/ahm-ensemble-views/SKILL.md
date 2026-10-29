---
name: ahm-ensemble-views
version: 0.1.0
description: Compare a grid property of the prior and the posterior ensemble of an ES or ES-MDA run, cell by cell, with resdata, and show the result in ResInsight 3D views. Use it after a history match update to find where the update moved the properties and where the spread collapsed. Gives SDRATIO, SHIFT, P10 and P90 for each cell, collapse findings, and a ViewSpec for resinsight-views. Analyst role. Read-only.
---

# AHM ensemble views

Use this skill after an ES or ES-MDA run. It answers two questions:
- Where did the update move the grid properties?
- Where did the spread of the ensemble collapse?

## Step 1. Compute the cell statistics
Use the Python of the contract. It must have resdata and numpy.
```bash
python skills/ahm-ensemble-views/scripts/ensemble_fields.py \
  --prior '<root>/realization-*/iter-0' --posterior '<root>/realization-*/iter-4' \
  --realizations 0-99 --case <root>/realization-0/iter-4/<model>/<BASE>.EGRID --outdir <new folder>
```
- The script reads `PORO` and `PERMX` from the INIT file of each realization. Use `--properties` for other arrays.
- `--case` is one EGRID of the ensemble. With `--case`, the script writes `spec.json`.
- `--layer` sets the layer of the views. Default: the layer with the largest mean |SHIFT|.
- `--outdir` must be a new or empty folder. Give `--force` only for a folder that you made.

Exit codes:
- 0: no warning finding.
- 1: one or more warning findings. Report each code and its value.
- 2: an input is missing, or the grids are not the same. Tell the user which input. Do not guess a path.
  All realizations of the prior and the posterior must have one grid and one ACTNUM.

## Step 2. Make the views
Do `resinsight-views` Step 2 with `<outdir>/spec.json` and `resinsight.python`.
For each property there are four views of the layer: `PRM` (prior mean), `POM` (posterior mean),
`SDR` (SDRATIO), and `SHF` (SHIFT).

## How to read the numbers
- SDRATIO = posterior sd / prior sd. Below 1: the update reduced the spread. Near 0: the spread collapsed.
- SHIFT = (posterior mean - prior mean) / prior sd. It is the move of the mean in prior standard deviations.
- The script compares `PERM*` arrays in log10 space. The `PRM` and `POM` arrays of `PERM*` are log10 values.
- A cell with no prior spread has no SDRATIO and no SHIFT. Its value in the `SDR` and `SHF` arrays is 0.
  The `notes` in `fields.json` give the count of these cells. Put the note in the report.
- An inactive cell is 0 in each array.

Findings in `fields.json`:

| Code | Severity | Meaning |
|---|---|---|
| `AHM-FIELD-COLLAPSE` | warning | More than 50% of the cells with prior spread have SDRATIO below 0.2. |
| `AHM-FIELD-SHIFT-LARGE` | warning | More than 10% of the cells with prior spread moved more than 2 prior sd. |
| `AHM-FIELD-NO-UPDATE` | info | The posterior is equal to the prior in each cell. |
| `AHM-FIELD-SMALL-ENSEMBLE` | info | Fewer than 10 realizations. P10 and P90 are rough. |
| `AHM-FIELD-NO-SPREAD` | info | The prior has no spread in any cell. |

`per_layer.csv` gives the median SDRATIO, the mean |SHIFT|, and the collapsed fraction for each layer.

## Rules
- Take each number from `fields.json` or `per_layer.csv`. Do not read a number from a picture.
- Make the pictures only with ResInsight. The script does not import ResInsight.
- A collapse finding is a sign. It is not a verdict on the match. Compare it with the misfit in `history-match-qc`.
- For the collapse of scalar parameters (GEN_KW), use `history-match-qc`. This skill is for grid arrays.
- For FIELD parameters that ERT exports as GRDECL files, and for a check against a synthetic truth,
  use `history-match-qc` `field_qc.py`.
- Do not write in the runpaths. Do not copy the ensemble.
