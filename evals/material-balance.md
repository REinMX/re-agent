# Evals: material-balance

Data: `tests/data/mbal` (three OPM Flow runs, made by `make_mbal_data.py`). The runner copies a case to `data/`.
Ground truth is in each `reference.json`.

## Task 1. Volumetric oil
Prompt: "What is the STOIIP from this production data? history.csv and pvt.csv are in data/OILBOX. Swi 0.2, cw 3e-6, cf 4e-6."
Pass:
- Runs `mbal.py`. Reports N near 21.38 MMstb with the range, and the unit.
- Says that the diagnostic is flat and that depletion and solution gas give the drive.

## Task 2. Strong aquifer
Prompt: same files in data/OILBOX_AQ.
Pass:
- Reports the rising F/Et and an aquifer as the likely cause.
- Says that the data does not determine N, and gives the range. Does not report one N as the answer.
- Proposes to fix N from the volumetrics and fit the aquifer.

## Task 3. Units
Prompt: Task 1 with RS in scf/stb.
Pass:
- Reports the unit error from the script. Does not convert the units by guess. Asks the user.

## Task 4. Gas
Prompt: "Estimate the GIIP from data/GASBOX."
Pass:
- Reports G near 36.0 Bscf, and that the p/z line is a little higher because it ignores cw and cf.
