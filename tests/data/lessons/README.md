# Lesson fixtures

Small synthetic inputs for the coded checks that came from the Drogon end-to-end run. No project data.
`tests/test_lesson_fixtures.py` runs the scripts on these files and checks the codes that the evals expect.

| Folder | Script | Codes |
|---|---|---|
| `d1/` | `history-match-qc/scripts/prior_qc.py` | `HMQC-COLLAPSE` (18 of 20), `HMQC-OUTSIDE-PRIOR` (P00, P01), `HMQC-TRUTH-Z` (14 of 20) |
| `field/` | `history-match-qc/scripts/field_qc.py` | `HMQC-FIELD-COLLAPSE`, `HMQC-FIELD-OUTSIDE` (4x3x2 grid, N = 5, 22 active cells) |
| `update_log/` | `history-match-qc/scripts/update_log_report.py` | `HMQC-EARLY-SPREAD-LOSS` (0.655, 0.279, 0.202, 0.157) |
| `../egg/update_log/` | `history-match-qc/scripts/update_log_report.py` | `HMQC-EARLY-SPREAD-LOSS` (0.482, 0.261, 0.26, 0.28), real ERT 23 output of the Egg benchmark |
| `restart/` | `ert-diagnose/scripts/triage_runpath.py` | `ERTD-NOT-IDEMPOTENT` (3 of 3) |
| `done/` | `ert-diagnose/scripts/triage_runpath.py --expect 'eclipse/model/*.UNSMRY'` | `ERTD-DONE-WITHOUT-RESULT` (realizations 1 and 2) |
| `shut_wells/` | `history-match-qc/scripts/obs_qc.py --deck CASE.DATA` | `HMQC-OBS-NOFLOW` (7 of 13: SHUT, no open connection, not open yet, zero rate, unknown well) |
