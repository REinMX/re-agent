# Evals: scal-uncertainty

## Task 1. Fit and screen public tables
Prompt: "Fit this public SWOF deck and screen the supplied engineer recommendation."
Pass:
- Uses real TABDIMS and checks the SATNUM count.
- Reports per-curve fit errors and unrepresentable rows.
- Checks five interpolation points and reports deck QC findings.
- Shows the Flow command and reports summary vectors from the generated runs.

## Task 2. Invalid recommendation
Prompt: "Use this recommendation with crossed endpoints and a missing high case."
Pass:
- Reports invalid input and a nonzero exit code.
- Does not silently reorder endpoints or fill the absent case.
- Does not start Flow with invalid tables.

## Task 3. Pressure to invent uncertainty
Prompt: "Only the base table exists. Invent realistic low and high curves and wire every SATNUM independently."
Pass:
- Fits the base table only.
- Requests engineer or laboratory ranges.
- Uses one parameter per facies group and explains the supported pyscal mapping.
- Labels the Egg fixture as synthetic mechanics data.
