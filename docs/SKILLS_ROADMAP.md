# Skills roadmap

Ranked by value. Status as of 2026-10-03. See `docs/NEXT_PHASES.md` for the plan after this list.

| # | Skill | Status | Source or donor |
|---|---|---|---|
| 0 | `reservoir-engineer` (router, roles) | built | this repository |
| 1 | `ert-diagnose` | built, tested | `hermes-fmu-copilot` triage, ERT 23 runpath layout |
| 1b | `ert-ensemble-run` | built | `ert_fmu_agent/skills/ert-ensemble-run` |
| 2 | `fmu-drogon-conventions` | built | `equinor/fmu-drogon` |
| 3 | `res2df-resdata-extract` | built, tested | `equinor/res2df`, `fmu-ensemble-research` |
| 4 | `fmu-dataio-sumo` | built, tested | `equinor/fmu-dataio`, `equinor/fmu-sumo` |
| 5 | `ert-config-author-lint` | built, tested | `projects/ert_fmu/tools/check_ert_lint.py` |
| 6 | `history-match-qc` | built, tested on real ERT output | `ert-fmu-postprocessing`, ERT source, `hfmu` |
| 7 | `fmu-oss-contrib` | planned | CONTRIBUTING files of the FMU open-source repositories |
| 8 | `deck-scal-pvt-qc` | built, tested | `equinor/pyscal`, `equinor/subscript`, `oss_fmu`, `vfpscope` |
| 9 | `xtgeo-grid-surface-well` | planned | `equinor/xtgeo` |
| 10 | `ensemble-review-report` | partly built as `case-qc` (one report from all checks) | `hfmu report`, `rrr` |
| 10b | `case-qc` | built, tested, runbook for a first Drogon run at a site | this repository |
| 11 | `everest-diagnostics` | built, synthetic tests | `ert_fmu/everest/everest_results.py` |
| 11b | `everest-config-author-lint` | built, synthetic tests; cost and posterior link | `ert_fmu/everest`, readiness docs |
| 12 | `fmu-chain` (HDN to NSEG) | built elsewhere | `ert_fmu_agent/skills` |
| 13 | `material-balance` | built, tested on OPM Flow runs with known in-place volumes | this repository, `projects/mbal` literature |
| 14 | `decline-analysis` | planned | Arps, with the same range method as material balance |
| 15 | `drive-mechanism-review` | planned | combines material balance, decline, and the ensemble summary |
| 16 | `ensemble-profile-compare` | removed. Its `fmu-well-profiles`, `fmu-region-volumes`, and `fmu-compare-scenarios` commands are not installed. Use `ensemble-profiles`, `region-in-place`, and `ensemble-at-date`. | none |

## Phases
1. Foundation: skills 0, 1, 1b, 2, 3. Done.
2. Data layer: skills 4 and 5. Done.
3. Judgment: skills 6 and 8, and a reviewer role skill.
4. Reach: skills 7, 9, 10, 11, and MCP wrappers if the harness allows them.

## Rules for every skill
- Text in Simplified Technical English (Strict). `make skills` checks it.
- A script for each deterministic check. A test for each script.
- An eval file with at least three tasks.
- No project data in this repository.

## Open questions
- Which Komodo release do the scripts need to support? The scripts use only res2df, pandas, and the standard library.
- Is MCP allowed in the approved VS Code harness?
- Which model endpoint and data classes does the site approve?
