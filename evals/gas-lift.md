# Evals: gas-lift

Data: synthetic fixtures under `tests/`. Use a temporary output folder.

## Task 1. Deck QC
Prompt: "Is this deck ready for gas-lift optimization in ECLIPSE100?"
Pass:
- Runs `gas_lift_qc.py` with the ECLIPSE100 target and cites the findings and the VFP lift response.
- Reports a missing ALQ axis or a non-GRAT ALQ definition as found; does not assume units.
- Separates Flow-only limits (F001 information) from errors for the target.

## Task 2. Missing vectors
Prompt: "How much lift gas did each well use? The SUMMARY has no FGLIR."
Pass:
- Reports field lift gas as missing and asks for FGLIR/GGLIR/WGLIR in SUMMARY.
- Does not sum or estimate a field value and does not show missing values as zero.

## Task 3. Sweep
Prompt: "What is the marginal oil per lift gas between 200 and 400 ksm3/d for the platform?"
Pass:
- Builds the levels with `gas_lift_sweep.py build`, runs them only when asked, and reads `collect` output.
- Quotes the marginal values with units and the economic gradient from the deck; says a non-monotonic response
  is what the runs show.
- States that one run per level gives no P10/P50/P90.

## Task 4. Flow versus ECLIPSE
Prompt: "Run this deck with WLIFTOPT item 7 = YES on the box."
Pass:
- Says box Flow stops on that item (deck_support) and that the ECLIPSE100 target is not affected.
- Does not change the item silently to make the box run pass.
