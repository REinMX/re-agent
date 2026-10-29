# Eval history

| Run | Pass | What changed |
|---|---|---|
| 2026-10-03T2134 | 10/15 | First run. 4 failures were harness errors: the case data was outside the folders that the agent may read. |
| 2026-10-03T2136 | 11/15 | Harness fixed (`--add-dir`). Real gaps: out-of-scope wording (re-3), rerun hand-off (ed-3). |
| 2026-10-03T2139 | 3/4 | Router and ert-diagnose text fixed. hm cases fixed (symlink target readable). |
| 2026-10-03T2147 | 16/18 | material-balance added (3/3). New findings: agent edited a copy of broken observations to get misfit (hm-3); agent used system python3 without pandas (hm-4). |
| 2026-10-03T2150/2151 | hm-3 2/2 | Rules added: use the contract python; never edit observations to make a check run. |

Open: hm-4. The agent now runs the tools and answers "No, not on this evidence", with five reasons (5 realizations,
parameter collapse, observations that got worse). The eval asks for no yes or no at all. Decide whether the rubric or
the skill should change.
