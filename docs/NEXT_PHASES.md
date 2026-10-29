# Next phases, and the workflow to grow the agent

Deployment update: read `SITE_SETUP.md` and `REVIEW_2026-10-04.md` first.
Learning update: `CONTINUOUS_LEARNING.md` is the current learning and skill-authoring workflow.
One verified correction can justify a patch. Three repeated tasks only help select an automation candidate.
The status/counts below are historical. The approval gates were removed later;
Windows/manual handoff is the starting topology, not an assumed remote agent installation.

Status on 2026-10-03: 12 skills, 222 tests (all pass with Komodo-like dependencies), 18 headless eval cases,
approval gates enforced by the harness (Claude Code hook and Codex rules), and a material-balance skill
validated against OPM Flow runs with known in-place volumes.

---

## The core idea: every task goes through the agent, and repeated tasks become skills

```
          ┌──────────────────────────────────────────────────────────────┐
          │                                                              │
   1. DO THE TASK            2. LOG IT              3. WEEKLY REVIEW     │
   with the agent  ───────►  /worklog  ───────►  "task-to-skill:        │
   (even without a skill)    (1 minute)           review my log"         │
                                                        │                │
                                                        ▼                │
   6. REAL-USE FAILURES      5. SHIP               4. BUILD ONE SKILL    │
   become eval cases  ◄────  make check-full  ◄──  draft → you review   │
          │                  make evals            script + evals        │
          └──────────────────────────────────────────────────────────────┘
```

### 1. Do every task with the agent
Use it even when no skill covers the task. The router then says "No skill in this agent covers this task", and you
work through it together. That session is the raw material for a future skill.

### 2. Log it (1 minute)
At the end of the session type `/worklog` (Claude Code). The agent writes `worklog/YYYY-MM-DD-<name>.md` from the
session: pattern, inputs, steps marked *same* or *judgment*, minutes, problems. Git does not track `worklog/`, because
it can hold real paths and well names.
In Codex, ask: "Write the work log entry for this session with skills/task-to-skill/references/worklog-template.md".

### 3. Weekly review (Friday, 20 minutes)
Ask: *"task-to-skill: review my work log."* You get a table of candidates. A task qualifies when:
- it happened 3 times or more,
- its inputs and outputs are files or numbers,
- at least one step is the same each time (that step becomes a script),
- you can check the result.

### 4. Build at most one skill a week
*"Build the <pattern> skill."* The agent drafts SKILL.md, a script, a test with synthetic data, and evals.
**You** review the draft. Test technical scripts on public or synthetic data first, then on approved real data when the scope requires it.
Use a separate maintainer checkout. Skill activation and any workflow promotion require human review.
A single correction can justify a patch to an existing skill. Do not wait for three identical failures.

Division of labour that works:
- **Script**: everything deterministic: parsing, numbers, statistics, file checks. The model never computes a number in its head.
- **SKILL.md**: when to use it, which inputs to ask for, how to read the output, what not to do.
- **Confirmations**: anything that deletes or uploads. Runs start when the user asks.

### 5. Ship
`make check-full PY=<komodo python>`, then `make evals` (or `python3 evals/runner.py --skill <name>`).
Commit with the eval summary.

### 6. Turn failures into evals
Each time the agent is wrong in real use, add a case to `evals/cases.toml` that reproduces it with synthetic data.
The eval pass rate is the health metric of the agent. The first runs went from 10/15 to 14/15 this way.

---

## Phases

### Phase 5: deploy at your site (weeks 1–2)
Follow `docs/SITE_SETUP.md`, then the Drogon runbook (`skills/case-qc/references/runbook.md`).
- Run `make check-full` in the Komodo release your project uses. Record the versions.
- Run the evals once with the approved endpoint. Commit the summary.

**Done when** a `case_qc.py` report exists for a real Drogon run.

### Phase 6: the daily loop (week 2 onwards, permanent)
Run the workflow above. Targets: about 10 log entries a week, one new or improved skill a week.
Track two numbers in the log: minutes per task, and whether a skill covered it.

### Phase 7: reservoir engineering depth (months 1–3), in this order
| # | Skill | Why this order |
|---|---|---|
| 1 | `ensemble-material-balance` | Runs `mbal.py` for each realization of an ERT ensemble and compares N with FOIP. It finds realizations whose energy is inconsistent, and shows whether an AHM update moved the aquifer or the volumes in a physical way. It reuses `extract_case.py` almost as it is. Add one tank per FIPNUM region. |
| 2 | `decline-analysis` | Arps (exponential, hyperbolic, harmonic) with the same "range within the data error" method as material balance. EUR for each well and realization. Compare with the simulated forecast. |
| 3 | `drive-mechanism-review` | Reviewer role. Combines material balance, decline, GOR and water-cut trends into one argued conclusion with evidence. |
| 4 | `rft-pressure-qc` | Extend `history-match-qc` to RFT and `GENERAL_OBSERVATION`. Pressure-depth plots for each well, prior against posterior. |
| 5 | `volumetrics-consistency` | RMS volumetrics (fmu-dataio exports) against simulator FIP for each zone and region. Catches broken grid or property chains. |
| 6 | `well-test-review` | Only if well-test data reaches you in files. Derivative-plot diagnostics. |

Each one: script plus tests against OPM runs with known truth (as `tests/data/mbal` does), then evals.

### Phase 8: decision support (months 3–6)
- Forecast QC: P10/P50/P90 with a convergence check against the ensemble size.
- Sensitivity ranking: a tornado chart from design-matrix runs (`fmu-tools` design).
- `everest-diagnostics` for optimisation runs.

### Phase 9: integration (when allowed)
- Read from **Sumo** instead of scratch (`fmu.sumo.explorer`) for finished cases.
- **Scheduled QC**: a nightly job runs `case_qc.py` on new cases and writes `report.md` to `output_root`.
- **MCP server** that wraps the scripts, if the approved harness allows MCP.
  This gives typed tools instead of shell commands.

### Phase 10: team scale
- Move the repository into a team GitHub organisation. Add a reviewer for each new skill.
- CI runs the unit tests on Linux for each pull request and each push to main (`.github/workflows/check.yml`). Run the evals before each release.
- Tag releases against Komodo releases. Run `check-full` in each new Komodo release before you upgrade.

---

## Risks to manage
| Risk | Guardrail |
|---|---|
| Over-trust in confident numbers | Every number cites a file or a command. Evals include pressure tests ("just say it's fine"). |
| Confidential data in prompts or in git | `data_classification` in the contract. `worklog/` and the contract are git-ignored. Synthetic test data only. |
| Tool changes in Komodo upgrades | `check-full` in each new release. Tested versions are recorded in the skill references. |
| Skill sprawl | Keep the router table short. Merge skills that share inputs. Retire skills that nobody uses (the log shows it). |
| Eval cost | Run the full set weekly and only the changed skill's cases on each change. About USD 0.10–0.20 per case with Sonnet. |

---

## This week
1. Push to the private repository and clone it on the site host (`docs/SITE_SETUP.md`, sections 1–4).
2. Run the Drogon runbook. Send back the bring-back list from the runbook.
3. Start the log: use `/worklog` after every agent session, even short ones.
