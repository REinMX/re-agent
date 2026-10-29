---
bump: ahm-status-report minor
---
### Fixed

- `ahm-status-report` reads the GEN_KW distribution file of `GEN_KW NAME template output dist` and of `GEN_KW NAME dist`, with any file extension and with trailing options such as `INIT_FILES:` or `UPDATE:`. A relative path is relative to the config file that has the GEN_KW line.
- When the case has parameters but no distribution file can be read, the report shows the WARN "parameter-at-bound check could not run" in section 3, `summary.md`, page 1, `status.json` and `findings.csv`. The WARN does not count toward the verdict. Section 3 never shows a bare `_none_`: it says how many parameters it checked.
- Block observations such as `BPR:7,8,4` are no longer ranked as wells (worst wells table and plot, WELL-MISFIT-HIGH, heatmap). They have their own "pilot pressure (block) observations" summary in report section 2 and `status.json`.
- The pressure page draws a pilot-pressure panel for each observed BPR block (observed value with error bar against the ensemble) and does not say "no RFT observations" then.
- The pressure page and report call WBHP the flowing bottom-hole pressure, not the average reservoir pressure. WBP4 and WBP are labelled as well-block fallbacks, WBP9 as the 9-point well-block average reservoir pressure.
- Only the lint "NUM_REALIZATIONS below 100" is a known false alarm. Other MIN_/NUM_REALIZATIONS lint items show as "lint, not calibrated" (`not_counted_reason` in `status.json`) and do not count.
- A missing PHYS-AQUIFER or PHYS-WELL-PI is shown as a design choice and does not count toward the verdict.
- The water-cut axes (WWCT, FWCT) follow the data, with a floor at 0. Large observation error bars no longer set the axis.

### Changed

- The report names outlier realizations (misfit > median + 3 * 1.4826 * MAD (Hampel rule)) in section 2, `summary.md`, `status.json` and on `misfit_hist.png`. Information only, not counted.
- The P10/P50/P90 tables have a UNIT column from the SMSPEC. A vector with P10 = P90 is marked "history-controlled (zero spread)".
- Page 1, `summary.md` and the REVIEW-ITEMS evidence name the codes of the counted review items.
