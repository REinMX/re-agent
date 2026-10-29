---
name: material-balance
description: Do a material balance (tank model) of an oil or gas reservoir. It estimates the in-place volume, finds the drive mechanism, and fits an aquifer (pot, Schilthuis, or Fetkovich). Use it for STOIIP or GIIP from production data, the drive mechanism, or aquifer support. Also use it to check a simulation model against the material balance. Analyst role. Read-only.
version: 0.1.0
---

# Material balance

You are the analyst. Two scripts do the work.

| Script | Needs | Does |
|---|---|---|
| `scripts/extract_case.py` | res2df and a finished simulator run | Writes `history.csv`, `pvt.csv`, and `reference.json` from the summary and the deck. |
| `scripts/mbal.py` | Python only | Computes F, Eo, Eg, Efw. Fits the in-place volume and the aquifer. Gives the diagnostic and the drive indices. |

The method, the equations, and the limits are in `references/method.md`. Read it before you interpret a result.

## Step 0. Check the tool once
Run this command before you use the tool on real data. It uses `tests/data/mbal/OILBOX`, a synthetic
OPM Flow run with a known in-place volume.
```bash
python skills/material-balance/scripts/mbal.py --fluid oil \
  --history tests/data/mbal/OILBOX/history.csv --pvt tests/data/mbal/OILBOX/pvt.csv \
  --swi 0.2 --cw 3e-6 --cf 4e-6 --aquifer none --json
```
Exit code 0. Field `fits.none.in_place` is near 21 380 759 stb. The simulator value is 21 372 924 stb, in
`tests/data/mbal/OILBOX/reference.json`. The difference is about 0.04 %, inside the range in `references/method.md`.
If the exit code is not 0, or the number is far from this value, stop. Do not run the tool on real data.
Report the exact command and the exact output.
The same fixture runs inside `make check` as `tests/test_material_balance.py`. That test is the one check
this skill must pass before and after a change to `mbal.py` or `extract_case.py`.

## Step 1. Get the input
Choose the source.
1. **A simulator case.** Run:
   ```bash
   python skills/material-balance/scripts/extract_case.py <CASE>.DATA --outdir <output_root>/<name>
   ```
   The summary must have `FPR` and the field cumulatives. The script prints the next command with Swi, cw, and cf.
   `reference.json` holds the in-place volume of the simulator. Use it only for a comparison. Do not give it to `mbal.py`.
2. **Field data from the user.** Ask for these items. Do not guess a value:
   - average reservoir pressure for each date, and the cumulative production and injection,
   - a PVT table along the depletion path (P, BO, RS, BG, BW for oil, or P, BG, BW for gas),
   - Swi, cw, cf, and the gas cap ratio m (oil),
   - the unit system.

Write the CSV files to `output_root`. Keep the units of the source. The script does not convert units.

## Step 2. Run
```bash
python skills/material-balance/scripts/mbal.py --fluid oil --history history.csv --pvt pvt.csv \
  --swi 0.2 --cw 3e-6 --cf 4e-6 --aquifer all --outdir <output_root>/<name>
```
Exit code 0 means done. Exit code 1 means an input problem. Read the message. A unit error stops the script.
Use `--data-error` for the expected error of the data (default 0.01, which is 1 % of F). Ask the user for this value
when the data is field data.

## Step 3. Read the result
1. Read the **Diagnostic** line first. F/Et that increases means that the model misses energy.
   F/Et that decreases means too much energy, or a data error.
2. Compare the aquifer models. A model with a warning does not fit. Do not report its numbers as a result.
3. Read the **range** of the in-place volume. It is the set of values that fit within the data error.
   If the range is wide, the data does not determine the in-place volume. Say so. Report the range, not one value.
4. With a strong aquifer, fix the in-place volume with `--in-place` (from the volumetrics or the simulator).
   Then fit only the aquifer. Report K, tau, and the cumulative influx.
5. For a simulator case, compare the result with `reference.json`. A difference larger than the range shows
   energy that the tank model does not have, more than one PVT region, or a pressure average problem.

## Rules
- A good R2 does not prove a drive mechanism. Report the models that fit, and say which data would tell them apart.
- Do not mix units. FIELD as in a deck: psia, stb, Mscf, rb/stb, Mscf/stb, rb/Mscf, 1/psi.
- Do not use a gas cap ratio m that you guessed. Ask the user.
- The tank model has one pressure. A reservoir with separate compartments or a strong pressure gradient breaks it.
  Say so when the user gives such a case.
- Volatile oil and gas condensate are not covered. Say so, and stop.

## Report
1. The input files, the source of each value, and the units.
2. The diagnostic, and the in-place volume with its range for each model that fits.
3. The drive indices at the last date for the model that you report.
4. For a simulator case, the comparison with `reference.json`.
5. What you did not check, and the data that would make the answer better.
