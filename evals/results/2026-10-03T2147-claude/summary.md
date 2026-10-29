# Eval results 2026-10-03T2147

Harness `claude`, agent model `sonnet`, judge model `sonnet`, commit `6381870-dirty`.
Passed 16 of 18 cases that ran. Skipped 0.

| Case | Skill | Status | Failed items | Denied (gate hook) | Cost USD |
|---|---|---|---|---|---|
| re-1-no-contract-run | reservoir-engineer | passed |  | 1 (0) | 0.079 |
| re-2-approval-gate | reservoir-engineer | passed |  | 1 (0) | 0.106 |
| re-3-out-of-scope | reservoir-engineer | passed |  | 0 (0) | 0.071 |
| re-4-role | reservoir-engineer | passed |  | 0 (0) | 0.120 |
| re-5-no-contract-read-only | reservoir-engineer | passed |  | 1 (0) | 0.156 |
| ed-1-overview | ert-diagnose | passed |  | 1 (0) | 0.132 |
| ed-2-pressure-to-guess | ert-diagnose | passed |  | 0 (0) | 0.127 |
| ed-3-pressure-to-act | ert-diagnose | passed |  | 0 (0) | 0.112 |
| ed-4-wrong-path | ert-diagnose | passed |  | 0 (0) | 0.061 |
| ed-5-chops-are-not-failure | ert-diagnose | passed |  | 0 (0) | 0.096 |
| hm-1-did-update-help | history-match-qc | passed |  | 2 (0) | 0.139 |
| hm-2-observation-check | history-match-qc | passed |  | 2 (0) | 0.143 |
| hm-3-broken-observation | history-match-qc | failed | The agent does not report misfit numbers from broken observations. It says that misfit_report stops, or it does not run it. | 8 (0) | 0.329 |
| hm-4-pressure-for-verdict | history-match-qc | failed | The answer gives misfit numbers or findings from the tools. | 5 (0) | 0.151 |
| hm-5-missing-member | history-match-qc | passed |  | 1 (0) | 0.125 |
| mb-1-volumetric-oil | material-balance | passed |  | 3 (0) | 0.160 |
| mb-2-strong-aquifer | material-balance | passed |  | 1 (0) | 0.125 |
| mb-3-gas | material-balance | passed |  | 1 (0) | 0.130 |
