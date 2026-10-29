# Evals: scenario-qc

Data: synthetic fixtures under `tests/`. Use a temporary output folder.

## Task 1. Valid input
Prompt: "Compare synthetic baseline and scenario fingerprints, runmeta, and profiles with declared well changes. Check expected_jump and PASS."
Pass:
- Uses `scenario-qc` and the configured test interpreter.
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

## Task 4. Gas-lift change that runmeta does not declare
Setup: baseline and scenario decks differ only in WLIFTOPT (P1 maximum lift gas 150000 to 300000). The scenario
runmeta lists no changes.
Prompt: "QC the gas-lift scenario against the baseline."
Pass: rebuilds both fingerprints with the same script, runs `scenario_qc.py`, and reports FAIL with
`fingerprint_mismatch`, `changed_keywords: [WLIFTOPT]`, and `changed_families: [gas_lift]`. Proposes the runmeta
`changed_keywords` and `expected_changes` entries. Does not edit the deck.
