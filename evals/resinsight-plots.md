# Evals: resinsight-plots

Data: synthetic fixtures under `tests/`. Use a temporary output folder.

## Task 1. Valid input
Prompt: "Export evidence from synthetic profiles and bands with no viewer installed. Check fallback plots and the evidence manifest."
Pass:
- Uses `resinsight-plots` and the configured test interpreter.
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
