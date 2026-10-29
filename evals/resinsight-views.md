# Evals: resinsight-views

Data: the synthetic EGRID and INIT files that `tests/synth_grid.py` makes, or a public case (Drogon, Reek).
ResInsight and rips from `resinsight-session`.

## Task 1. Valid input
Prompt: "Show layer 3 of the SDRATIO array that you computed, and a north section of PORO through row 2."
Pass:
- Writes the array as a whole-grid GRDECL file with `grdecl.py` and a ViewSpec with absolute paths and a new `out`.
- Uses `INPUT_PROPERTY:SDRATIO` with `range.k [3, 3]`, and `STATIC_NATIVE:PORO` with `range.j [2, 2]` and `camera north`.
- Runs `render_views.py` with `resinsight.python`, reports the exit code, the PNG paths, and `views.rsp`.
- Takes no number from the picture. Numbers come from the step that computed SDRATIO.

## Task 2. Missing input or unsupported request
Prompt: "Make a view that shows only the cells with SWAT above 0.6 at the last step."
Pass:
- Does not add a `value` key or a property filter. Says that a property filter through rips filters no cell.
- Computes a 0/1 indicator array with resdata in a numbers step, writes it as GRDECL, and shows that array.
- If the case has no restart (no time steps), reports the `failed` entry for a dated view and does not invent a step.

## Task 3. Well completions after a flag
Prompt: "grid-property-qc flagged COMP-LOW-PERM and COMP-INACTIVE in two wells. Show me where they are."
Pass:
- Runs `well_views.py --only-flagged` on the `completion_in_grid.py` output folder, then `render_views.py` on the spec.
- Reports the COMPFLAG codes, and names each `COMP-INACTIVE` or `COMP-OUTSIDE` cell from the spec notes,
  because ResInsight does not draw those cells.
- Reads the manifest `failed` list and reports each failed view. Exit 1 is not a success.
