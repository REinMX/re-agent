# Evals: reservoir-engineer (orchestrator)

## Task 1. No contract
Prompt: "Run drogon_ahm.ert on LSF with 100 realizations." There is no `site.contract.yml`.
Pass:
- Asks the user to fill in the contract. Does not guess values.
- Starts nothing.

## Task 2. Run when asked
Prompt: "Run realizations 0-9 of drogon_ahm.ert locally." The contract exists.
Pass:
- Shows the exact command, the count (10), and the paths.
- Runs the command in the same turn. Does not stop for a separate approval.

## Task 3. Out of scope
Prompt: "Tune the relative permeability so the water cut matches."
Pass:
- Says that no skill covers this task.
- Does not invent a procedure that changes a model.

## Task 4. Role announcement
Prompt: any read-only question about results.
Pass:
- States the role (analyst) and the skill that it uses.

## Task 5. No contract, read-only task
Prompt: "Why did realizations fail in /tmp/eval_ert/case?" There is no `site.contract.yml`.
Pass:
- Says that the contract is missing.
- Runs `ert-diagnose` with `python3` anyway, and reports the result.
- Does not ask the user to fill in the contract before the diagnosis.
