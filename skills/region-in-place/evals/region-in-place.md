# Evals: region-in-place

Data (lab): FROG fip_regions scenario, one OPM Flow 2026.04 run with FIPNUM (3), FIPSEG (3 fault blocks),
FIPRES (2), FIPSZN (9) and RPTSOL/RPTSCHED FIP=3. Ensemble without region vectors: FROG frog_hm iter-3.
P10/P90: numerical (contract). Fixture for runner.py: `fip_case` (copy of the run's SMSPEC/UNSMRY/PRT + groups
YAML) and `fip_ensemble_no_regions` (3 realizations with FOIP only). Both still need adding to runner.py.

## Task 1. Initial and remaining in place per segment
Prompt: "Give me oil and gas in place per segment at the start and at end-2045, and a table I can put in Excel."
Pass:
- Runs `region_in_place.py` with `--groups` (segment mapping) and `--dates initial,...2046-01-01` (end-2045 = 1 Jan 2046 step).
- Reports SM3, surface conditions, the initial row source (PRT report 0), and QC PASS.
- Gives the xlsx path; does not type volumes that the script did not write.
- Initial FOIP 10 758 615 SM3; East_block OIP 2.863e6 -> 9.16e5 SM3.

## Task 2. Ensemble without region vectors
Prompt: "P10/P90 of ROIP per region at end of history for the HM ensemble."
Pass:
- Runs the extractor on the ensemble, gets exit 2, reads `missing_vectors.json`.
- Says ROIP/RGIP (and FGIP) are not in the SUMMARY; gives the SUMMARY lines to add or the generator command.
- Does not report zeros or field numbers as region numbers. Gives the rerun command; does not start a rerun the user did not ask for.

## Task 3. Phase split for a FIP<xxx> set in OPM Flow
Prompt: "I need free gas and dissolved gas per fault block (FIPSEG)."
Pass:
- Knows the keywords RGIPGSEG / RGIPLSEG and that OPM Flow 2026.04 ignores them ("Unhandled summary keyword").
- Uses `--from-prt --families all` (PRT FIP=3 report) and quotes the PRT-vs-summary cross-check.

## Task 4. Keyword question
Prompt: "What is the SFOIP keyword for regions in Eclipse and Flow? We have a set called FIPZONE."
Pass:
- Says there is no SFOIP keyword: FOIP/ROIP are surface (stock-tank) volumes; ROIP_ZON for FIPZONE.
- Mentions the 3-character rule, the array in REGIONS and REGDIMS dimensions.

## Task 5. Recovery factor per region
Prompt: "So the East block recovery is 68%?"
Pass:
- Explains the OIP drop is not recovery when oil moves between regions: the East block has no producer.
- Suggests region-to-region flow keywords (ROFT records) before calling it recovery. Does not confirm 68% as RF.
