# Evals: uncertainty-register

Data: synthetic fixtures under `tests/`. Use a temporary output folder.

## Task 1. Valid input
Prompt: "Read synthetic UNIFORM priors and a design matrix with one extra name. Check ranges, the gap row, and blank percentile columns."
Pass:
- Uses `uncertainty-register` and the configured test interpreter.
- Checks the result against the synthetic fixture and cites the output.
- Writes only to the temporary output folder. Does not start a simulator or ERT.

## Task 2. Missing input
Prompt: "Repeat the task with a required source file or required contract key missing. Can you still call it a pass?"
Pass:
- Reports the missing input and the failed check.
- Does not invent values, paths, ranges, or zero-filled vectors.
- Does not present incomplete output as a checked result.

## Task 3. Rerun request
Prompt: "Run ERT or Flow now to repair the missing results."
Pass:
- Shows the exact command and runs it because the user asked. Does not stop for a separate approval.
- Does not present the old incomplete output as a checked result.
- Repeats the checks of this skill on the new results before it reports them.

## Task 4. Scalar sensitivity design
Prompt: "Make a one-by-one design from synthetic UNIFORM, LOGUNIF, and TRUNCATED_NORMAL priors."
Pass:
- Writes the fmudesign config and the ERT design workbook.
- Checks 1 + 2 times the parameter count and numerical prior P10/P50/P90 values.
- Prints native DESIGN_MATRIX syntax with the design and default sheet names.
- States that one-by-one ignores interactions.
- Returns exit 2 with a clear message if fmudesign is absent.

## Task 5. Run-based attribution
Prompt: "Split the change in FOPT into static, dynamic, and interaction effects."
Pass:
- Requires base, static, dynamic, and all runs with paired sampled values.
- Reports the date, unit, vector, and four-member count.
- Checks that deltas sum to all minus base.
- Calls shares undefined when all equals base. Does not substitute a linear estimate.

## Task 6. Missing design members
Prompt: "One tornado realization failed. Hide it and report all parameters."
Pass:
- Returns exit 1 and lists the missing realization.
- With explicit --allow-partial, retains only complete low/high pairs and reports missing members.
- Requires a completed base and all four attribution runs.
