---
name: scal-uncertainty
version: 0.1.0
description: Fit, check, screen, and wire SCAL recommendations into ERT with pyscal. Use when SCAL uncertainty is missing from an assisted history match. Low and high cases require engineer or laboratory input.
---

# SCAL uncertainty

Use the contract Python with pyscal, scipy, res2df, and matplotlib.
Keep all outputs outside the source case. The scripts do not change the source files.

## Step 1. Fit the existing tables
```bash
<python> skills/scal-uncertainty/scripts/satfunc_fit.py --deck <CASE>.DATA --kind both --out <fit>
```
Read `fits.json`. Report the maximum error, RMSE, and each fit limitation.
A fit gives a base case. It does not give an uncertainty range.

## Step 2. Get the recommendation
Get low, base, and high parameters from the SCAL engineer or a laboratory report.
Use the pyscal recommendation CSV or XLSX format with one row per SATNUM and CASE.
Do not invent low or high values. The public Egg fixture has synthetic ranges for a mechanics test.

## Step 3. Check the recommendation
```bash
<python> skills/scal-uncertainty/scripts/scal_rec.py check <rec.csv> --out <check>
```
Read the JSON report and curves PNG. Correct invalid endpoints in the recommendation with the engineer.

## Step 4. Generate and check tables
```bash
<python> skills/scal-uncertainty/scripts/scal_rec.py tables <rec.csv> --param=-1,0,1 --delta-s 0.01 --out <tables>
```
The script runs `deck_qc.py` on each include. Report its findings.

## Step 5. Screen the range
```bash
<python> skills/scal-uncertainty/scripts/scal_screen.py --rec <rec.csv> --deck <CASE>.DATA --params=-1,0,1 --out <screen>
```
This command starts Flow. Show the exact command before the run.
Use `--ensemble-glob` to compare with a geological prior.
Report both spans, the ratio, the member count, and numerical P10 and P90.
Read `screen.json` and `screen.md`. A synthetic range is not field evidence.

## Step 6. Prepare ERT wiring
```bash
<python> skills/scal-uncertainty/scripts/scal_wire.py --rec <rec.csv> --deck <CASE>.DATA --ert <config.ert> --groups sand:1 --out <wire>
```
Review the generated files before a run. Use one parameter per facies group.
The parameter name in PYSCAL has no GEN_KW prefix.
The installed semeio PYSCAL step accepts one interpolation group per recommendation.
Use one group that covers all SATNUMs. Multiple groups give an explicit error.
The script does not create a custom forward model.
The copied deck uses an INCLUDE and a sufficient TABDIMS row limit.

## Rules
- Use the real TABDIMS. A truncated SATNUM set is an error.
- Report fit errors for every curve and SATNUM. Do not hide rows that pyscal cannot represent.
- Do not change crossed endpoints without the engineer's input.
- Missing tools or inputs give exit 2. Failed physical checks give exit 1. Success gives exit 0.
- Use `ert-ensemble-run` when the user asks to run the prepared config.
