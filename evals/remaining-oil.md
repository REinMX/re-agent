# Evals: remaining-oil

Data: a public case (Egg, Drogon) at its last report step, or the synthetic cases of `tests/test_remaining_oil.py`.
ResInsight and rips from `resinsight-session` for the views.

## Task 1. Valid input
Prompt: "Where is the remaining oil at the last step? Give me five infill candidates, away from the wells."
Pass:
- Runs `completion_in_grid.py` first, then `remaining_oil.py` with `--completions` and a stated `--sor` from the SCAL data.
- Reports each candidate (rank, i, j, mobile oil in the window, nearest well, distance) from `remaining_oil.json`.
- Says that the result is screening only: mobile oil in place, no pressure, no flow paths.
- Renders `spec.json` with `render_views.py` and gives the PNG paths. Takes no number from a picture.

## Task 2. Missing input
Prompt: "Rank infill candidates for this case." The case has no UNRST file, or the SOR is not known.
Pass:
- Reports exit 2 and the missing file. Does not use another case or another step.
- Asks for the SOR or reads it from the SCAL data. Does not guess it. If the user accepts 0, writes that in the report.
- Uses a new output folder outside the case folder.

## Task 3. Out of scope
Prompt: "How much oil will a new well at the first candidate produce?"
Pass:
- Says that the skill gives mobile oil in place, not a producible volume or a rate.
- Proposes a simulation of the candidate (Everest well placement or a prediction case) and does not invent a rate.
- Gives the candidates to `everest-config-author-lint` as a start list when the user asks.
