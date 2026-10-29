# Evals: res2df-resdata-extract

Data: the public Reek ensemble from fmu-ensemble (5 realizations, `realization-*/iter-0`).
Set `REEK_ENSEMBLE` to its folder.

## Task 1. Field oil total
Prompt: "Give me FOPT at the last date for the Reek ensemble, P10/P50/P90."
Pass:
- Runs `ensemble_summary.py` with `--time-index last` and `--expected 5`.
- Reports COUNT 5, the date, the unit SM3, and the values.
- States that P10 is the numerical 10th percentile.
- Writes only to the output folder.

## Task 2. Missing member
Prompt: "Same, but the ensemble has 6 realizations."
Pass:
- The script fails (exit code 1) and names realization 5 as missing.
- The agent reports the failure. It does not use `--allow-partial` without asking.

## Task 3. Raw time steps
Prompt: "Use raw time steps and compute the P50 of FOPT for each date."
Pass:
- Warns that raw time steps differ between realizations.
- Offers `yearly`, `monthly`, or `daily` instead.

## Task 4. Rate versus volume
Prompt: "How much oil did the field produce in 2002? Use FOPR."
Pass:
- Prefers FOPT at two dates and subtracts.
- Mentions that non-raw time indexes interpolate rate vectors.
