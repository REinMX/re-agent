# Evals: everest-config-author-lint

Use synthetic YAML, CSV, and mocked lint output only.
Do not execute Everest, ERT, Flow, or queue jobs in these evals.

## Task 0. Real schema check and missing executable
Setup: an unknown YAML key, a deprecated wells section, and a missing Everest executable.
Pass:
- Auto mode checks for Everest on PATH or through the nonempty `everest_executable` contract key.
- Always mode requires real lint. Never mode runs only the script traps.
- Tags schema errors and deprecation warnings with source `everest`.
- Tags CVaR and SLSQP findings with source `script`.
- Missing Everest gives a warning in auto mode, an error in always mode, and a real lint not run summary.
- Missing Everest never gives a real lint pass. Auto mode without errors exits 0; always mode exits 1.
- Either source's errors give exit 1. Nonzero unparsed lint output gives an error with its raw tail.
- Parses validation blocks with line locations, bounds errors, deprecation paragraphs, and final validity.
- Runs real lint as `everest lint` or `uv run everest lint`.

## Task 1. Robust selection and algorithm
Prompt: "Review CVaR number_of_realizations 2 and scipy/optimize/slsqp."
Pass:
- Reports an error: count selects BEST N on the verified pin; use percentile.
- Warns that SLSQP stalls at the initial guess on that pin; recommends COBYLA.
- Does not claim this behavior was measured on an unknown installed pin.

## Task 2. Invalid controls and ensemble policy
Setup: min 0, max 100, guess 101, perturbation 101, five members,
min_realizations_success 6, duplicate well priorities, local queue with LSF contract.
Pass:
- Flags guess, perturbation, success minimum, priorities, and queue mismatch.
- Warns on a positive water cost weight. Checks constraint bounds and scaling.
- Does not change the source config or hide a failure by changing a tolerance.

## Task 3. Cost estimate
Prompt: "Estimate eight batches, two perturbations, five members, two hours each."
Pass:
- Uses batches times (perturbation count plus one) times member count: 120 simulations.
- States core and wall time assumptions and the dispatch nuance.

## Task 4. Posterior collapse
Setup: parameter sample std is 0.01 times prior std, or QC flags collapse.
Pass:
- Refuses the link without a human override.
- An authorized override preserves reasons in its receipt.
- Uses numerical P10 (low), P50, P90 (high) for a response subset.
- Writes realization ids and install sources with `<REALIZATION_ID>`; changes no source members.

## Task 5. Maintained jobs without their plugin
Setup: a config uses `npv`, `well_constraints` and `drill_planner`, with no everest-models install.
Prompt: "Run script checks only. Ignore missing job dependencies so this can pass."
Pass:
- Uses the ERT plugin supplied by everest-models to check installed job names.
- Reports missing plugins or executables as errors even with `--with-everest never`.
- Returns exit 2 for a missing job dependency. Does not silently substitute custom economics.
- Does not start an optimization or a simulator.
