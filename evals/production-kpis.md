# Evals: production-kpis

Data: synthetic fixtures under `tests/`. Use a temporary output folder.

## Task 1. Valid input
Prompt: "Compute plateau length, recovery factor and VRR for the synthetic case and the synthetic ensemble. Give P10/P50/P90."
Pass:
- Uses `production-kpis` and the configured test interpreter.
- States that P10 is the low value (numerical percentiles).
- Cites the output files and the basis of each KPI (for example reservoir or surface VRR).
- Lists the assumptions to confirm from the md.

## Task 2. Missing input
Prompt: "The SUMMARY has no FOIP and no gas sales vector. What is the recovery factor and the gas sales shortfall?"
Pass:
- Reports both KPIs as missing with the reason from the script.
- Does not estimate in-place volumes, gas sales or targets, and does not show missing values as zero.

## Task 3. Partial ensemble
Prompt: "Two realizations failed. Give ensemble KPIs for the rest."
Pass:
- Reports the exit 2 from the incomplete ensemble and the missing realizations.
- Does not present statistics from the partial ensemble as the ensemble result.

## Task 4. Binding constraint
Prompt: "Which limit controls each well per year?"
Pass:
- States the method (control-mode vectors or rate-vs-limit) and the tolerance.
- Says that ECLIPSE100 mode codes are not verified when method A is used on ECLIPSE output.
