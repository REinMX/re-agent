# Evals: ahm-ensemble-views

Data: the synthetic runpaths that `tests/test_ensemble_fields.py` makes, or a public ES-MDA run (Drogon).
ResInsight and rips from `resinsight-session`.

## Task 1. Valid input
Prompt: "The ES-MDA run finished (iter-0 to iter-4). Where did the update move PORO and PERMX, and did the spread collapse?"
Pass:
- Runs `ensemble_fields.py` with the iter-0 and iter-4 globs, `--realizations`, `--case` (one EGRID), and a new `--outdir`.
- Reports the exit code, each finding code with its value, and the SDRATIO median and mean |SHIFT| for each property.
- Says that PERMX is compared in log10 space, and gives the note on cells with no prior spread.
- Runs `render_views.py` on `spec.json` and gives the PNG paths and `views.rsp`. Takes no number from a picture.

## Task 2. Missing input or grids that do not match
Prompt: "Compare iter-0 and iter-4. Realization 7 of iter-4 failed, and two realizations use another grid."
Pass:
- Reports exit 2 and the error text (the missing realization, or the realization with another grid or ACTNUM).
- Does not drop realizations or remove `--realizations` to make the run pass.
- Asks the user which realizations to compare, then runs again with that set.

## Task 3. Scalar parameter collapse
Prompt: "Did the GEN_KW parameters collapse after the update? Show it in ResInsight."
Pass:
- Says that the collapse of scalar parameters belongs to `history-match-qc`, and uses it.
- Uses `ahm-ensemble-views` only for grid arrays (PORO, PERMX, or the arrays that the user names).
- Does not invent a 3D view of a scalar parameter.
