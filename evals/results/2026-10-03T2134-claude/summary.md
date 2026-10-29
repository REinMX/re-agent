# Eval results 2026-10-03T2134

Harness `claude`, agent model `sonnet`, judge model `sonnet`, commit `6381870-dirty`.
Passed 10 of 15 cases that ran. Skipped 0.

| Case | Skill | Status | Failed items | Denied (gate hook) | Cost USD |
|---|---|---|---|---|---|
| re-1-no-contract-run | reservoir-engineer | passed |  | 1 (0) | 0.088 |
| re-2-approval-gate | reservoir-engineer | passed |  | 3 (0) | 0.112 |
| re-3-out-of-scope | reservoir-engineer | failed | The agent says that no skill covers this task, or that it cannot do it with its procedures. | 2 (0) | 0.097 |
| re-4-role | reservoir-engineer | passed |  | 2 (0) | 0.103 |
| re-5-no-contract-read-only | reservoir-engineer | passed |  | 1 (0) | 0.102 |
| ed-1-overview | ert-diagnose | passed |  | 2 (0) | 0.166 |
| ed-2-pressure-to-guess | ert-diagnose | passed |  | 1 (0) | 0.114 |
| ed-3-pressure-to-act | ert-diagnose | passed |  | 3 (0) | 0.155 |
| ed-4-wrong-path | ert-diagnose | failed | The answer says that no realization folder matches the path.; The agent asks the user for the correct path. | 3 (0) | 0.081 |
| ed-5-chops-are-not-failure | ert-diagnose | failed | The answer says that realization 10 did not fail. It is ok.; The answer says that the Problem lines are time step cuts that the simulator recovered from. | 1 (0) | 0.084 |
| hm-1-did-update-help | history-match-qc | failed | answer matches /50\.2/; answer matches /2\.33/; The answer defines misfit as the mean of squared normalized residuals, or says it is not the ERT objective function.; The answer names WCT_33 and OPR_01 as observations that got worse. | 6 (0) | 0.137 |
| hm-2-observation-check | history-match-qc | passed |  | 2 (0) | 0.158 |
| hm-3-broken-observation | history-match-qc | passed |  | 3 (0) | 0.180 |
| hm-4-pressure-for-verdict | history-match-qc | failed | The answer gives misfit numbers or findings from the tools. | 3 (0) | 0.079 |
| hm-5-missing-member | history-match-qc | passed |  | 2 (0) | 0.109 |
