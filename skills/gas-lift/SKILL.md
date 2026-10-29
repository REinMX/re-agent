---
name: gas-lift
version: 0.1.0
description: Gas-lift QC and build helpers for ECLIPSE100 decks (box Flow for tests). Check that VFPPROD tables have an ALQ axis that means lift gas rate in deck units and that LIFTOPT/WLIFTOPT/GLIFTOPT are consistent. Read lift gas use, group lift limits and wells that get no gain from SUMMARY. Sweep lift-gas levels through scenario-build and tabulate oil gain against lift gas used. Missing vectors are missing, never zero. Analyst role.
---

# Gas lift

Scripts compute every number. Report what they wrote, with the file. A missing vector or a level without results
stays missing. The production target is ECLIPSE100; box Flow runs are tests.

## Step 1. Deck QC (before a run)
```bash
python skills/gas-lift/scripts/gas_lift_qc.py --deck CASE.DATA --contract site.contract.yml   # or --target ECLIPSE100
```
- A lifted well has WLIFTOPT YES, WELTARG LIFT or a WCONPROD item 12 value. For each one the script checks:
  - the well has a VFP table (G002) and the table has an ALQ axis (G001)
  - the VFPPROD item 7 ALQ definition is GRAT (G003). IGLR, TGLR and blank give a warning; PUMP, COMP, DENO, DENG
    and BEAN fail
  - the table units match the deck (G004)
  - limits and ALQ values lie inside the ALQ axis (G005)
- Deck-wide checks: the LIFTOPT increment (G006), a LIFTOPT before any WLIFTOPT YES (G007), GLIFTOPT groups exist
  (G008), named wells exist (G010), and the OPM parser accepts the deck (G011).
- VFP lift response per table: at how many (rate, THP, WFR, GFR) points BHP falls at the first ALQ step, and the ALQ
  at minimum BHP. A table with no fall is G009. This is a tubing response, not an oil gain.
- Flow restrictions come from flow-preflight deck_support (F001): WLIFTOPT items 6 and 7, GLIFTLIM, WALQCALC.
  They are information for an ECLIPSE100 target, because only the box test is affected, and errors for a FLOW target.
- Exit 0 pass or warnings, 1 error findings, 2 bad input. Writes `gas_lift_qc.json` and `.md`.

## Step 2. Build lift scenarios
Use `scenario-build` type `gas_lift` (LIFTOPT, WLIFTOPT, GLIFTOPT, WELTARG LIFT) in ECLIPSE100 syntax. Any number can
be an Everest variable (`"${name}"`), for example a well ALQ, a WLIFTOPT maximum or a GLIFTOPT group lift limit;
`everest_controls.py` writes the controls section.

## Step 3. Diagnostics (after a run)
```bash
python skills/gas-lift/scripts/gas_lift_diag.py --case CASE.DATA [--deck CASE.DATA] [--rel-tol 0.01]
```
- Needs WGLIR, GGLIR, FGLIR (lift gas rates) and WOPR in SUMMARY; WALQ is read when present.
- Per well: lift gas and oil volumes, average lift gas per oil (not marginal), days on lift, days optimized, days at
  the WLIFTOPT maximum. Flags: `no_lift_while_optimized`, `lift_without_oil`, `at_well_max`.
- Per GLIFTOPT limit: GGLIR (FGLIR for FIELD) against the maximum lift gas, and GGPR + GGLIR against the maximum
  total gas, within rel_tol: honored, violated or missing.
- The script compares the sum of WGLIR volumes with FGLIR. WEFAC and GEFAC scale FGLIR and not WGLIR, so a deck with
  them shows a difference.

## Step 4. Lift-gas sweep (marginal oil per lift gas)
```bash
python skills/gas-lift/scripts/gas_lift_sweep.py build --spec lift.yml --variable plat_lift \
  --levels 100000,200000,400000 --out sweep/ --target ECLIPSE100
# run each sweep/levels/<label>/scenario deck (box Flow, or the site run route when the user asks)
python skills/gas-lift/scripts/gas_lift_sweep.py collect --sweep sweep/ [--reference BASE.DATA]
```
- `build` renders one scenario per level (other variables keep their initial values; levels must be inside the
  bounds) and writes `sweep.json`. It runs nothing.
- `collect` writes `sweep_results.csv/.json/.md` and `sweep_wells.csv`: lift gas volume (FGLIR x time), FOPT, change
  against the reference (or the level with the least lift gas), marginal oil per lift gas between neighbouring levels in build order,
  compared with the LIFTOPT economic gradient, and per-well marginal values and verdicts (`benefits`, `no_benefit`,
  `less_lift`, `no_extra_lift`).
- A level whose lift gas volume does not rise gets no marginal value and a note. Optimizer interactions can make
  the field response non-monotonic: report it as found.
- One deterministic run per level gives no P10/P50/P90. For an ensemble, sweep per realization; percentiles are
  numerical (P10 = low).

## Not checked (confirm for ECLIPSE100)
- A blank ALQ definition means lift gas rate only after the user confirms it.
- The diagnostics compare GLIFTOPT item 3 with GGPR plus GGLIR. They do not check whether GGPR already holds the lift
  gas.
- The default WLIFTOPT maximum (largest ALQ of the table) comes from the OPM test-suite notes (GASLIFT-08).
- Box Flow results can differ from ECLIPSE100 for the same deck (see the OPM gaslift test notes).
