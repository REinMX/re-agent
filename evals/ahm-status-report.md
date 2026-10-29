# Evals: ahm-status-report

Data: a drogon-lite ES-MDA case (ERT 26, 50 realizations, weights 4, 2, 1) in four copies:
posterior-only (`iter-3` only), all iterations (`iter-0` to `iter-3`), `iter-3` with failed realizations 7, 19 and 33,
and `iter-3` of a run with all observation errors multiplied by 0.3. Or the synthetic cases of
`tests/test_ahm_status_report.py`.

## Task 1. Posterior-only status
Prompt: "How good is the history match in <case>? Only iter-3 is left."
Pass:
- Runs `ahm_status.py` with `--case` and `--ert-config`, and a local `--outdir`. Does not use `--write-share`.
- Says "iter-0 (prior) is gone" and does not describe what the update changed.
- Gives the verdict "Use with caveats" with its evidence lines, and says that it is advisory and never passes DG0.
- Gives P10/P50/P90 at the end of history with "P10 is the LOW value".
- Says that the pressure plots use WBHP because WBP9, WBP4 and WBP are not in SUMMARY, and that WOPRH is missing.

## Task 2. Failed realizations
Prompt: "Make the status report for the case with failed runs."
Pass:
- Reports 47 of 50 realizations ok, and names realizations 7, 19 and 33 as failed, with the ERROR file reason.
- Says that the misfit uses the 47 ok members.
- Does not call the failed members good, and does not drop them from the count.

## Task 3. Bad observation errors
Prompt: "The errors were scaled by 0.3. Is the posterior fit for use?"
Pass:
- Gives the verdict "Not ready". It is never "Fit for use".
- Quotes the predictive coverage and the mean misfit against the spread-aware reference from the report.
- Names the worst wells from the ranked table and the heatmap.
- Does not propose to change the observation errors without the user.

## Task 4. Full iterations
Prompt: "All iterations are on disk. Show what the update did."
Pass:
- Reports mode "full", the prior and posterior misfit, and the median misfit for each iteration.
- Reports the compensation check result as a review item, not as a failure.
