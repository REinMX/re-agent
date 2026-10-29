---
name: scenario-build
version: 0.1.0
description: Render a typed scenario spec (infill, producer to injector, WAG, workover, rate and group targets incl. gas, gas lift and ALQ, network back-pressure, date shifts) into an ECLIPSE100 SCHEDULE include on a copy of the case. Checks the deck with deck_support and declares the expected changes for scenario-qc. Spec variables become Everest controls. Analyst role. Does not run a simulator.
---

# Scenario build

`scripts/scenario_build.py` reads a scenario spec (YAML) and writes a new scenario folder: a copy of the case
whose DATA file includes `<DECK>_SCHEDULE.inc`, `runmeta.yml`, `deck_fingerprint.json`, and
`scenario_build.json`. `scripts/everest_controls.py` writes the Everest `controls:` section from the spec
variables. Neither script starts Flow, ECLIPSE, ERT, or Everest.

## When to use
- The user asks for a well, control, gas-lift, network, or schedule scenario on an existing deck.
- Before scenario-qc. The build declares what the scenario changes, so scenario-qc passes the intended changes
  and fails the others.
- To make the numbers of a scenario Everest controls.

## Step 1. Contract and target
Run `stage-contract`. The target simulator is `simulator.name` of the contract profile (ECLIPSE100 at the site).
Use `--target` only to override it. State the contract `p10_p90` convention in every report
(numerical: P10 is the low value).

## Step 2. Write the spec
Start from `references/scenario.example.yml`. Unknown keys and wrong types are errors (exit 2).

| type | Writes | Keys (besides id, date, affected_wells, note) |
|---|---|---|
| `infill` | WELSPECS, COMPDAT, WCONPROD (or WCONINJE with `role: injector`) | well, group, i, j, ref_depth, phase, connections, control |
| `convert_to_injector` | WCONINJE | well, phase, mode, rate, bhp |
| `wag` | WCONINJE, alternating WATER and GAS from `start`, then `then` | well, start, cycles, water and gas {rate, bhp, days}, then (WATER, GAS, SHUT or CONTINUE) |
| `workover` | WELOPEN by connection (open or shut only), COMPDAT (skin, diameter, direction), WELOPEN (well status) | well, connections, well_status |
| `target` | WCONPROD (well), WELTARG (well_limit), GCONPROD, GCONINJE, GCONSALE | level, name, mode, rates, bhp, limits, action |
| `gas_lift` | LIFTOPT, WLIFTOPT, GLIFTOPT, WELTARG LIFT (ALQ) | liftopt, wells (well, optimize, max_rate, weight, min_rate, gas_weight, optimize_limit), groups (group, max_lift, max_total_gas), alq |
| `back_pressure` | BRANPROP, NODEPROP (needs NETWORK in RUNSPEC) | nodes, branches |
| `shift` | moves the named operations by `days` | ops, days |

- WELTARG controls are the ECLIPSE ones: ORAT, WRAT, GRAT, LRAT, RESV, BHP, THP, VFP, LIFT, GUID. An injector rate
  limit is WRAT or GRAT, not RATE.
- WLIFTOPT follows ECLIPSE100: a defaulted `max_rate` means the largest ALQ of the well's VFP table. Items 6
  (`gas_weight`) and 7 (`optimize_limit`) are ECLIPSE-only: box Flow stops on values other than the defaults, and
  deck_support reports `box Flow FAIL` with the ECLIPSE target still PASS. Check the lift setup with `gas-lift`.
- WAG is alternating WCONINJE, not WCYCLE (WCYCLE opens and shuts a well; it does not switch phase).
  `then` says what the well does after the last leg.
- A connection with skin needs `diameter`. COMPDAT replaces the whole connection, so give the base diameter and
  direction. A connection that only opens or shuts is written as WELOPEN and keeps the base COMPDAT items.
- WCONPROD, GCONPROD, NODEPROP and BRANPROP records replace the base record. Items the spec does not give go to
  their defaults (for WCONPROD: THP limit, VFP table, ALQ). The build lists these in its notes. To change one
  well control, use `level: well_limit` (WELTARG).
- List offset wells you expect to move under `affected_wells`. Without them, a move on an offset well is
  `unexpected_jump` in scenario-qc.

## Step 3. Build
```bash
python skills/scenario-build/scripts/scenario_build.py --spec scenario.yml --out <scenarios>/<id> \
  --contract site.contract.yml
```
- The build keeps the base SCHEDULE text and drops its comments. A block goes after the base keywords of its date. A date
  inside a TSTEP or DATES block splits it; a date that is not a report date gets a new DATES record. The split
  timing keywords are declared too.
- Dates before START or on or after the last report date are errors.
- `scenario_build.json` lists inserts, timing splits, notes, the declaration check (fingerprint diff against the
  declared keywords and new wells), and the deck_support verdicts (table pinned in flow-preflight).
- Exit 0 means the build passed. Exit 1 means the build wrote the folder but the declaration check or the
  deck_support target verdict failed. Do not run that deck. Exit 2 means a bad spec or input. The output folder must be new or empty.
- A note lists new wells that the SUMMARY well lists leave out: they get no well vectors. This skill only
  writes SCHEDULE; tell the user to add the well to SUMMARY.

## Step 4. Run and QC
Run the deck only when the user asks, through `flow-preflight` (box Flow) or the site run route. Extract
profiles with `ensemble-profiles`, then run `scenario-qc` on the scenario id. Report the scenario-qc flags as they
are. Missing vectors are not zero.

## Step 5. Everest controls
A spec value `"${name}"` takes the value of variable `name` (`initial`, `min`, `max`, optional `integer: true`).
Any numeric item can be a variable: rates (incl. gas), ALQ, injection rates, node pressures, i/j, shift days.
Every variable must be used. Write the controls section:
```bash
python skills/scenario-build/scripts/everest_controls.py --spec scenario.yml --out controls.yml
```
Everest writes the values of each simulation to `<control name>.json` (default `scenario.json`) in the runpath.
Render that point with `--controls <runpath>/scenario.json`. Values outside the bounds are an error.
Check the full Everest config with `everest-config-author-lint`. Forward-model templates are not part of this
skill.

## Rules
- The scripts write every keyword and number. Do not edit the rendered include by hand; change the spec and build again.
- Do not write into scratch realization folders or the source case.
- Do not start a simulator, ERT, or Everest from this skill.
