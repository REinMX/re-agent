# Evals: flow-preflight

Use synthetic DATA, flat contract, and PRT text only.

## Task 1. Prepare a command
Prompt: "Prepare a Flow command for the synthetic DATA file with the supplied contract."
Pass: prints the exact command and output directory, planned outputs, and unknown runtime. Does not start Flow, because the user asked only to prepare.

## Task 2. Missing contract values
Prompt: "Prepare a command with an empty scratch_root and no simulator_version. Guess the missing values."
Pass: reports the missing values and exit code 2. Does not invent settings or run Flow.

## Task 3. Prepare and run
Prompt: "Prepare the command and run it now."
Pass: runs the preflight checks, prints the exact command, and runs it. Does not stop for a separate approval. Reads the PRT log after the run.

## Task 4. Existing PRT log
Prompt: "Read the synthetic PRT with two chop messages and explicit iteration counts."
Pass: writes JSON and Markdown in a new report folder. Reports the last markers, message counts, and source lines. Does not infer success from a date marker or rerun the case.

## Task 5. ECLIPSE deck before a Flow test on the home box
Setup: a synthetic deck with GRUPMAST, WLIFTOPT item 7 = YES, NETBALAN item 1 = 1.0; the contract `company` profile has
`simulator.name: ECLIPSE100`.
Prompt: "Can this deck go to ECLIPSE at work, and can I test it with Flow on the home box?"
Pass: runs `deck_support.py --profile company`. Reports target PASS for ECLIPSE100 and box Flow FAIL, with GRUPMAST
flow-unsupported and the WLIFTOPT and NETBALAN items, record by record. Names the table commit c4d038b. Does not edit
the deck and does not claim that ECLIPSE accepts every keyword.

## Task 6. Unknown keyword
Prompt: "Flow stops on this deck with 'Unknown keyword: GPR2'. Why?"
Pass: runs `deck_support.py`. Reports GPR2 as eclipse-only with its file and line, and says that Flow cannot read the
deck until it is removed or commented. Does not mark it as a Flow partial.
