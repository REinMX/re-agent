# Evals: hm-localization

Data: the Egg ERT 26 case (FIELD PERMX, 192 well observations) or the synthetic inputs of `tests/test_hm_localization.py`.

## Task 1. Valid input
Prompt: "My ES-MDA collapses the PERMX field. Set up distance localization and tell me which radius to use."
Pass:
- Runs `obs_locations.py`, then `localize_obs.py` into a new obs file, then `loc_lint.py` on the config.
- Runs `radius_sweep.py` on the stored prior and gives the table from `sweep.md` with the wall time.
- Says that the sweep is one ES step from the prior, not the ES-MDA chain, and that it does not measure the data fit.
- Proposes the full ES-MDA for the top 1 or 2 radii and shows the exact `ert es_mda` command. Does not start it unasked.

## Task 2. Missing input
Prompt: "Add localization to my observations." The locations CSV has no row for one well of the obs file.
Pass:
- Reports exit 2 and the well name. Does not write a partial file and does not guess a coordinate.
- Proposes to fix the deck path or the KEY, and runs Step 1 again.

## Task 3. Out of scope
Prompt: "Use DISTANCE localization for my GEN_KW parameters (Drogon)."
Pass:
- Says that ERT 26 rejects DISTANCE for GEN_KW (`loc_lint.py` reports the error) and that GEN_KW uses GLOBAL or ADAPTIVE.
- Says that the case needs FIELD or SURFACE parameters first, and that this is a model change for the user to approve.
- Does not edit the config without approval.
