---
bump: scenario-qc minor
---
### Changed
- `scenario-qc/scripts/deck_fingerprint.py` tracks the control keywords by family: GRUPTREE, GUIDERAT, WGRUPCON,
  LIFTOPT/WLIFTOPT/GLIFTOPT, GRUPNET, NETBALAN, BRANPROP/NODEPROP, WELOPEN, WTEST, WCYCLE, ACTIONX (to ENDACTIO), UDQ,
  GRUPMAST/GRUPSLAV/SLAVES and VFPPROD/VFPINJ (hash and table numbers, not the table text). A gas-lift, network,
  group-tree or action change now changes the fingerprint. Fingerprint `schema_version` is 3.
- A keyword block ends at the next keyword line, so an edit to an untracked keyword no longer shows as a change of
  the tracked keyword before it. The script expands nested SCHEDULE includes. Well names with a hyphen (OP-A01) count as wells.
- When the deck has START, each block records the report date it applies from, so a control moved to another
  report date is a change of that keyword. A missing include before SCHEDULE (for example one that ERT writes per
  realization) is skipped while looking for START. When only one of the two fingerprints has a START date,
  scenario-qc flags `fingerprint_mismatch` with one message asking to rebuild both from decks whose START resolves.
- `scenario_qc.py` checks runmeta claims against the same keyword set, reports `changed_families`, and flags
  `fingerprint_mismatch` when the baseline and scenario fingerprints have different schema versions.
