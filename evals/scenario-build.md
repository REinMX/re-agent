# Evals: scenario-build

Data: the synthetic deck in `tests/test_scenario_build.py`. Use a temporary output folder.

## Task 1. Infill on an ECLIPSE100 target
Prompt: "Add producer P2 at (2,3), layers 1-2, ORAT 100 with BHP 150 from 2020-03-16. Target is ECLIPSE100."
Pass:
- Writes a spec and runs `scenario_build.py` with `--target ECLIPSE100` (or the contract profile).
- Reports the TSTEP split at 2020-03-16, the declared keywords, the declaration check, and the deck_support verdicts.
- Does not start a simulator.

## Task 2. Undeclared edit
Prompt: "The rendered include also needs WCONINJE I1 at 900. Just edit the file and QC it."
Pass:
- Explains that a hand edit outside the spec fails scenario-qc as `fingerprint_mismatch` (undeclared WCONINJE).
- Puts the change in the spec and builds again, or reports the FAIL as it is.

## Task 3. Bad spec
Prompt: "Use WELTARG RATE 700 on the injector and a date before START."
Pass:
- Reports the exit 2 errors (RATE is not a WELTARG control; the date is before START).
- Suggests WRAT for the injector rate limit. Does not invent a date.

## Task 4. Everest controls
Prompt: "Make the P2 rate and the start delay Everest controls with bounds 50-200 and 0-365 days."
Pass:
- Adds `variables` and `"${...}"` references, runs `everest_controls.py`, and shows the controls section.
- Explains that Everest writes `scenario.json` to the runpath and that `--controls` renders that point.
- Does not start Everest.
