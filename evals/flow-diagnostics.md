# Evals: flow-diagnostics

Data: a public case (Egg) rerun with `RPTRST BASIC=2 FLORES`, or the synthetic cases of `tests/test_flow_diagnostics.py`.
ResInsight and rips from `resinsight-session`.

## Task 1. Valid input
Prompt: "Which injectors support PROD3 at the last step, and how uneven is the sweep?"
Pass:
- Runs `fd_preflight.py`, then `fd_export.py` with `resinsight.python`, then `fd_metrics.py` with `--completions`.
- Reports the injector shares of PROD3 as inferred (PORV-weighted fractions in the connection cells), with the date.
- Reports the Lorenz coefficient from `fd_metrics.json` and says that it comes from ResInsight `RESTIME` and resdata PORV.
- Reads the `failed` list of `fd_export.json`. Exit 1 is not a success.

## Task 2. Missing input
Prompt: "Run flow diagnostics on this realization." The UNRST file has no flux arrays.
Pass:
- Runs `fd_preflight.py`, reports exit 2 and the keyword `RPTRST BASIC=2 FLORES`.
- Gives the expected UNRST size from the message. Asks the engineer before a change to the deck.
- Proposes a rerun of one realization into a new folder. Does not start Flow until the user asks.
- Does not start ResInsight on the case and does not report a result.

## Task 3. Out of scope
Prompt: "Give me the ResInsight well allocation plot numbers and the F-Phi export of ResInsight."
Pass:
- Says that the allocation of this skill is inferred from the cell fractions, not the flux-based ResInsight plot.
- Says that the F-Phi export of ResInsight writes only a header in console mode, and that `fd_metrics.py`
  computes F-Phi from `RESTIME` and PORV.
- Does not invent the plot numbers.
