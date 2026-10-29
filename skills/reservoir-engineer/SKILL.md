---
name: reservoir-engineer
description: Entry point for reservoir engineering work in FMU and ERT projects. Use it first for questions about ERT runs, failed realizations, FMU repositories, simulation results, history matching, material balance, or model reviews. It selects the role and the skill.
version: 0.1.0
---

# Reservoir engineer agent

Read this skill first. Then load `continuous-learning` and the skill that matches the task.
Use `earned-autonomy` for done checks, uncertainty, trials, and retry limits.

Script paths in the skills start with `skills/`. They are relative to the agent repository.
If you work in another folder, use the folder of the skill instead. The harness shows that folder when it loads the skill.

## Step 1. Read the site contract
Read `site.contract.yml` in the project root.
If the file does not exist, tell the user. Then decide by the task:
- A read-only task on files that exist on disk can continue. Use `python3` and the standard library.
  Example: `ert-diagnose`. Say that the contract is missing.
- A maintainer task on public skill source can proceed without a site contract.
  First check the exact source checkout and write grant. Do not read field data or start engineering jobs.
- A task that starts a run, writes an engineering report, or needs res2df must wait.
  Copy `site.contract.example.yml` and ask the user to fill in each value. Do not guess a value.

Run each engineering script with the `python` of the contract, not with the system `python3`.
Use the approved development interpreter for maintainer tests.
Scripts that need res2df or pandas fail with the system Python. If a script reports a missing module, use the
`python` of the contract and run it again. If the contract has no `python`, ask the user.

## Step 2. Select the role
| Task | Role | Allowed actions |
|---|---|---|
| Read results, extract tables, compare cases | analyst | Read source files. Write only to `output_root`. |
| Start, stop, or restart an ERT run | operator | Show the exact command, then run it when the user asks. |
| Check a report, a claim, or a diff | reviewer | Read only. Report each finding with its evidence. |
| Create or change a skill | maintainer | Draft or patch only the approved source files. A person reviews activation. |

Use one role at a time. Tell the user which role you use.

## Step 3. Select the skill
| Task | Skill |
|---|---|
| Check site contract keys, profiles, and paths | `stage-contract` |
| Extract ensemble summary time series | `ensemble-profiles` |
| Compute P10/P50/P90 bands from profiles | `ensemble-bands` |
| Read ensemble rates or totals at a date | `ensemble-at-date` |
| Make yearly field or well profiles | `yearly-profiles` |
| Compute plateau length, recovery factor, VRR, gas sales, or binding constraints | `production-kpis` |
| Read oil, gas, or water in place per region | `region-in-place` |
| Build a well, target, gas-lift, network, or date scenario deck, or make its numbers Everest controls | `scenario-build` |
| Check gas-lift VFP and lift settings, read lift gas use from a run, or sweep lift-gas levels | `gas-lift` |
| Check a scenario after well or schedule changes | `scenario-qc` |
| Combine QC results for several scenarios | `scenario-matrix-qc` |
| Draft or compare an uncertainty register, make sensitivity designs, or read tornado and static/dynamic attribution results | `uncertainty-register` |
| Prepare prediction case folders without a run | `prediction-case` |
| Export ensemble summary plot evidence | `resinsight-plots` |
| Install ResInsight and rips, or check why ResInsight does not start | `resinsight-session` |
| Show a grid property, an array, a layer, a well section, or well completions in ResInsight 3D views | `resinsight-views` |
| Show where an ES or ES-MDA update moved a grid property, or where its spread collapsed | `ahm-ensemble-views` |
| Find which observation groups move which scalar parameters after an update, or find spurious correlation (AHM_ANALYSIS) | `ahm-analysis` |
| Find which injector supports which producer, or how uneven the sweep is (flow diagnostics) | `flow-diagnostics` |
| Map the remaining mobile oil, or list infill well candidates for Everest (screening) | `remaining-oil` |
| Assemble a QA/QC pre-read for a gate stage | `qa-qc-preread` |
| Package deliverables for a decision gate | `gate-packet` |
| Realizations failed, or a run does not finish | `ert-diagnose` |
| Start or monitor an ERT run | `ert-ensemble-run` |
| Prepare or run a Flow command, or inspect a Flow PRT log | `flow-preflight` |
| Check which deck keywords Flow supports, for an ECLIPSE deck or a Flow test on the home box | `flow-preflight` |
| Find where a setting, job, or file lives in an FMU repository | `fmu-drogon-conventions` |
| Read summary, grid, PVT, or well tables from simulator files | `res2df-resdata-extract` |
| Review or change an ERT config, or check that it loads | `ert-config-author-lint` |
| Review optimization, NPV, well order, or drill schedule configs; estimate Everest cost; link an ERT posterior | `everest-config-author-lint` |
| Check Everest optimization results, constraints, controls, failures, or robust objectives | `everest-diagnostics` |
| Case metadata, `ExportData`, metadata files, or Sumo | `fmu-dataio-sumo` |
| Check observations, or judge a history match (prior and posterior) | `history-match-qc` |
| Plan or review an ES or ES-MDA history match, or decide the next iteration | `ahm-workflow` |
| A plot-first status report of an ES or ES-MDA history match, also when only the last iteration is left | `ahm-status-report` |
| Set up or tune distance localization (LOCALIZATION blocks, radius) for a FIELD or SURFACE update | `hm-localization` |
| Fit, check, screen, or wire SCAL uncertainty into ERT | `scal-uncertainty` |
| Check relperm, PVT, EQUIL or VFP tables, or make SCAL input | `deck-scal-pvt-qc` |
| Check PORO, PERM, NTG, PORV, or region arrays, or check that well connections are in active cells | `grid-property-qc` |
| A full check of one case, or a first look at an unfamiliar case | `case-qc` |
| STOIIP or GIIP from production data, drive mechanism, aquifer support, or a material-balance check of a model | `material-balance` |
| Start or finish a substantive task, learn from a correction, or capture a checked procedure | `continuous-learning` |
| Set done checks, trial a workflow, bound retries, or review workflow authority | `earned-autonomy` |
| Automate a repeated task, create a skill, or change a skill from checked lessons | `task-to-skill` |
| Well profiles for each well and date | `ensemble-profiles` |
| Region volumes or regional pressure | `region-in-place` |
| Compare two ensembles at one date | `ensemble-at-date`, one run for each ensemble with `--scratch` |

If no skill matches, start the answer with this sentence: "No skill in this agent covers this task."
Then say what you can do instead, with the skill name. Do not improvise a procedure for a task that can change a model.
Examples of tasks that no skill covers: tune relperm, PVT, or other model input to match history by hand,
change the geomodel, or write a new forward-model job.

## Run commands
When the user asks for a run, show the exact command, the target paths, and the number of realizations.
Then run the command. Do not wait for a separate approval. This applies to ERT, Everest, Flow, and LSF commands.

Ask the user before these actions:
1. Delete a file or a folder. Delete only the exact path that the user names.
2. Register a case or upload data in Sumo (`--sumo` or any upload job).
3. Use SSH or transfer files between hosts.
4. Send file contents, well names, or paths to a service outside the approved model endpoint.

The reservoir MCP server is read-only. It cannot start a run.
When VS Code runs on Linux, use direct mode. Call the reservoir MCP tools before the shell. Do not use the Windows handoff on that host.
When the agent runs on Windows, give commands for the Linux terminal. Do not assume it can reach Komodo.
In manual mode, the user runs each command. Read only the results the user is allowed to share.

Do not edit a config file that the user does not own. Give a diff instead.

## Rules for every answer
- Cite the file and the line or the command output for each claim.
- Give the number of realizations behind each statistic.
- State the percentile convention. P10 and P90 are numerical percentiles unless the user says otherwise.
- State the date, the unit, and the vector name for each number.
- Report a failure as a failure. Do not hide missing realizations.
- Do not change a tolerance or edit a generated file to make a check pass.
- Write engineering outputs to `output_root`. Keep the source model tree unchanged.
- Write lessons and drafts only within the approved local grant. Write skill patches only in the approved source checkout.
- Stop an uncertain engineering step and ask. Continue independent safe work within its grant.
- A case-qc command that `tools/read_envelope.py` allows is safe work. Run it. Do not ask.
- Do not retry a failed operation or an uncertain write without a cause. Use the limits in `earned-autonomy`.

## When a task is outside your evidence
Say what you could not check. Name the file or the command that would settle the question.
