# Evals: everest-diagnostics

Use synthetic result tables. No optimizer or simulator is needed.

## Task 1. SLSQP stall
Setup: three batches, equal optimizer totals, controls equal the configured initial guess.
Pass:
- Reports the stall and recommends COBYLA with the verified pin scope.
- Does not infer a stall when controls move or differ from the initial guess.
- Starts no run, even when asked to restart immediately.

## Task 2. Bounds, failures, and constraints
Setup: final rate 100 at its upper bound; failed member 1 and missing member 4;
output pressure 12 exceeds upper bound 10.
Pass:
- Reports the final bound and recommends review of physics before changing it.
- Reports failed and missing members separately, with counts.
- Reports batch constraint violations and best versus initial objective.
- Does not hide failures or claim the best total is necessarily feasible.

## Task 3. CVaR versus mean
Setup: five maximized member values 10, 20, 30, 40, 50 at the same controls;
percentile 0.4. Optimizer totals use the opposite sign.
Pass:
- Gives n=5, mean 30, lower-tail CVaR 15 in raw maximized units.
- States direction and numerical percentile convention.
- Flags CVaR by count as selecting BEST N on the verified pin.
- Refuses inconsistent batch sets and unknown members; does not mix perturbation controls.

## Task 4. Well shut-in and budget
Setup: `tests/data/everest/shut`. Three batches, no output constraints, the last batch is the best,
and a PRT file in `batch_1/realization_1` has "Well A6 will be shut as it can not operate".
Pass:
- Runs `everest_diagnostics.py` with `--simulation-folder`.
- Reports `EVD-WELL-SHUT`, `EVD-BUDGET-LIMITED`, `EVD-UNCONSTRAINED`, and `EVD-COST-RATIO` (18 of 24, 75 %).
- Gives the limiter of each finding. Starts no run.

## Task 5. CVaR tail, start near a constraint, tie, and agreeing objectives
Setup: `tests/data/everest/tail`. CVaR percentile 0.25 with 8 members; batch 1 has 2 members.
VRR starts at 0.916 with lower bound 0.9. In the best feasible batch 3, OP7 = OP8 = 1 at the max bound. NPV and FOPT agree.
Pass:
- Reports `EVD-CVAR-TAIL-MISSING`, `EVD-CVAR-TAIL-ONLY-GRADIENT`, `EVD-START-NEAR-CONSTRAINT`,
  `EVD-PRIORITY-TIE`, and `EVD-OBJECTIVES-AGREE` with their numbers.
- Does not report a CVaR comparison for batch 1.

## Task 6. Two plans
Setup: `tests/data/everest/plans`. Plan A sets rate.A6 = 12000 (max); plan B sets 1000 (min).
The cross table gives the regret of plan A on ensemble B as 169 and of plan B on ensemble A as 80.
Pass:
- Runs `compare_plans.py` and reports `EVD-PLAN-FLIP` and `EVD-CROSS-REGRET`.
- Says that the plan depends on the posterior. Does not call one plan the truth.

## Task 7. Real Egg runs
Setup: `tests/data/everest/egg`. Everest 23 outputs on the public Egg benchmark. `EG1_mean_d1` maximizes the
mean NPV of 10 members. A PRT file in `batch_1` has Flow shut-in lines for PROD1, PROD3, and PROD4.
`EG4_cvar_d1` maximizes the lower-tail CVaR with percentile 0.25 over the same members.
`EG2_constrained_d1` has the output constraint `vrr` >= 0.9 and two input constraints (injection total <= 700).
Its CSV has `batch_<control>` columns beside the control columns.
`EG3_drill_d1` optimizes 3 drill priorities (PROD5, PROD6, PROD7). The NPV is 10.736824 in all 6 batches.
Pass:
- Runs `everest_diagnostics.py` on the four runs, with `--simulation-folder` for EG1.
- Reports `EVD-WELL-SHUT` for EG1 (batch_1/realization_0) and `EVD-COST-RATIO` (110 of 200).
- Reports `EVD-CVAR-TAIL-ONLY-GRADIENT` for EG4 (3 of 10 members) and no `EVD-CVAR-TAIL-MISSING`.
- Reads the 20 EG2 controls from the plain columns, not from the `batch_<control>` columns.
  Reports best feasible batch 9, `EVD-BUDGET-LIMITED`, and `EVD-COST-RATIO` (200 of 200). Does not report
  `EVD-START-NEAR-CONSTRAINT` (the start is 10 % of the scale from the `vrr` bound).
- Reports `EVD-CONSTRAINT-INACTIVE` for EG2: `vrr` is 0.99972 to 0.99997, 0.0997 above the 0.9 bound.
  Says that the constraint did not limit the plan and asks if `vrr` is the correct quantity.
- Reports `EVD-FLAT-OBJECTIVE` for EG3: the controls moved, but the order and the NPV did not change.
  Proposes to evaluate the 3! = 6 drill orders directly instead of a gradient optimizer.
