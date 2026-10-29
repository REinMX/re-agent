---
bump: deck-scal-pvt-qc minor, ahm-workflow patch
---
### Added
- Add `physics_checklist.py` to deck-scal-pvt-qc. It reads a deck and its include files as text and reports the AHM physics items: aquifer, fault multiplier per segment, kv/kh and barriers, well PI multipliers, WCONHIST history controls, and ROCK. With `--ert-config` it tells which items are uncertain (GEN_KW, FIELD, or template placeholders) and which are fixed. Missing items are warnings. ahm-workflow Step 3 runs it.
