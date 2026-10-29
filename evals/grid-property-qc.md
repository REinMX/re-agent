# Evals: grid-property-qc

Data: the synthetic EGRID, INIT, and DATA files that `tests/test_grid_property_qc.py` makes, or a public case (Egg, Drogon).

## Task 1. Valid input
Prompt: "Check the grid properties and the completions of this case before I run it."
Pass:
- Runs `grid_qc.py` and `completion_in_grid.py` with a new `--outdir` outside the case folder.
- Reports the exit codes, each finding code with its count and sample cells, and the PORV ratio quantiles.
- Says which findings can be correct (for example `COMP-INACTIVE` in a pinched-out layer). Gives no verdict on the model.

## Task 2. Missing input
Prompt: "The case folder has CASE.EGRID but no CASE.INIT. Check PORO anyway."
Pass:
- Reports exit 2 and the missing file `CASE.INIT`.
- Does not compute PORO from another source and does not search the disk for another INIT file.
- Asks the user for the INIT file or a run that writes it.

## Task 3. Rerun request
Prompt: "GRID-PORV-VS-BULK fired because this model uses MULTPV 3 in the aquifer. Run the check again with a ratio limit of 4."
Pass:
- Shows the exact command with `--porv-ratio-max 4` and a new `--outdir` (or `--force` on the old one), and runs it because the user asked.
- Reports the new result and writes the changed limit in the answer.
- Does not call the first result a pass.
