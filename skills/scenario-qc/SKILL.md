---
name: scenario-qc
version: 0.1.0
description: QC one FMU scenario vs baseline for wells, constraints, group, gas-lift, network, action, coupling, VFP, and SCHEDULE changes. Compares deck fingerprints, runmeta claims, and SUMMARY profiles. Flags fingerprint_mismatch, missing_vector, unexpected_silent, unexpected_jump; info expected_jump when runmeta.expected_changes declares intentional moves. Use after each scenario when the user changes wells, controls, gas lift, the network, or the schedule. Analyst role. Does not start Flow or ERT.
---

# Scenario QC

`scripts/deck_fingerprint.py` hashes the SCHEDULE control keywords, grouped in `KEYWORD_FAMILIES`.
`scripts/scenario_qc.py` compares one scenario to the contract baseline.

Exit code 0 means PASS. Exit code 2 means FAIL (QC gate). Do not invent zeros for missing vectors.

## When to use
- After a deck edit to a tracked keyword. Families (`KEYWORD_FAMILIES` in `scripts/deck_fingerprint.py`):
  wells (WELSPECS, COMPDAT, WELSEGS, COMPSEGS, WELOPEN, WPIMULT, WTEST), well controls (WCON*, WELTARG, WECON,
  WCYCLE), groups (GRUPTREE, GCON*, GUIDERAT, WGRUPCON), gas lift (LIFTOPT, WLIFTOPT, GLIFTOPT), network (GRUPNET,
  NETBALAN, BRANPROP, NODEPROP), VFP (VFPPROD, VFPINJ: hash and table numbers only), actions (ACTIONX to ENDACTIO,
  UDQ), coupling (GRUPMAST, GRUPSLAV, SLAVES), timing (TSTEP, DATES). NETWORK is a RUNSPEC keyword, so the
  fingerprint does not track it.
- Before a gate packet or QA/QC pre-read that lists scenario results.
- To prove a silent wrong deck (fingerprint vs runmeta) or a silent result (change claimed, volumes flat).

## Layout
```
<scenarios>/<id>/            # contract paths.scenarios, else <paths.root>/scenarios
  deck_fingerprint.json
  runmeta.yml
  deliverables/scenario_qc/  # written by this skill
```
The baseline id comes from contract `scenario_qc.baseline`. The contract must have a `scenario_qc` block.

## Step 1. Contract
Run `stage-contract`. Read `scenario_qc.compare_vectors` and `scenario_qc.headline_vector`
(default FOPT). State the contract `p10_p90` convention in every report.

## Step 2. Fingerprint (when the deck changed)
Use the `python` of the contract profile:
```bash
python skills/scenario-qc/scripts/deck_fingerprint.py --data <deck>.DATA \
  --out <scenarios>/<id>/deck_fingerprint.json
```
To find the deck from the contract, use `--contract site.contract.yml --runpath <case dir>` instead of `--data`.
The script expands SCHEDULE includes, nested ones too. A keyword block ends at the next keyword line, so an edit to
an untracked keyword does not show as a change. A change inside ACTIONX ... ENDACTIO belongs to ACTIONX.
When the deck has START, each block records its report date, so moving a block to another date is a change.
A missing include before SCHEDULE is skipped while looking for START. If only one fingerprint has a START date,
scenario-qc flags `fingerprint_mismatch` with one message: rebuild both from decks whose START resolves.
The report lists `changed_keywords` and `changed_families`. When the baseline and scenario
fingerprints have different schema versions, scenario-qc flags `fingerprint_mismatch`. Rebuild both with the same
script.
The script reads contract `eclbase`. There is no default deck name.

## Step 3. Declare expected changes (intentional scenarios)
In `runmeta.yml`, when you intend wells, constraints, or schedule to move results:

```yaml
expected_changes:
  wells: [<WELL>]
  keywords: [WCONPROD]
  field_vectors_may_move: [FOPT, FOPR, FWPT, FWPR]  # optional
  max_rel_delta: any   # or a float; default any for expected wells
```

Without this block, large moves on wells not in `touched_wells` or the fingerprint stay `unexpected_jump` (FAIL).
With `keywords` listed, a changed keyword or a new well that the block does not list is a `fingerprint_mismatch`.
`scenario-build` writes this block from the scenario spec.

### Injector voidage coupling
Contract key `scenario_qc.coupled_injectors_follow_producers`:
- When a producer is in `expected_changes.wells`, the `scenario_qc.coupled_vectors`
  (default WWIR, WBHP) on injector wells are `expected_jump` (info), not `unexpected_jump`.
- Without the contract flag, list the injectors under `expected_changes.wells`.

## Step 4. Run QC
```bash
python skills/scenario-qc/scripts/scenario_qc.py --contract site.contract.yml --scenario <id>
```
Writes `<scenarios>/<id>/deliverables/scenario_qc/scenario_qc_report.{json,md}`.

## Metrics
- Cumulative vectors (`FOPT`, `FWPT`, `FGPT`, ...): end-of-sim ensemble mean.
- Rate / BHP / cut vectors (`FOPR`, `WOPR`, `WGPR`, `WBHP`, `WWCT`, ...): series mean
  (catches mid-run changes when end-of-sim is near 0).

## Flags
| Flag | Severity | Meaning |
|---|---|---|
| `fingerprint_mismatch` | FAIL | runmeta claims do not match fingerprint diff (or the reverse) |
| `missing_vector` | FAIL | a `compare_vectors` name is absent in baseline or scenario profiles |
| `unexpected_silent` | FAIL | fingerprint changed but field/touched-well results stay inside tolerance |
| `unexpected_jump` | FAIL | large move on an untouched well, or field jump with unchanged fingerprint |
| `expected_jump` | INFO (`info_flags`) | large move on a well/vector in `expected_changes` (or a coupled injector vector) |

## Rules
- Scripts compute. Do not recompute volumes in prose.
- Missing data is not zero. Exit 2 when `fail_on_missing_vector` is true.
- Do not start ERT, Everest, Flow ensembles, or deletes.
- Do not write inside scratch realization folders.
- Mark demo scenarios with `demo: true` in runmeta. They are not real results.
