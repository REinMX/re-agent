# Reservoir engineer agent

This repository holds skills and tools for reservoir engineering work with FMU, ERT, and res2df.
Most skills check and operate FMU cases. `material-balance` is the first skill for reservoir engineering analysis.
Codex reads this file. Claude Code reads `CLAUDE.md`, which points here.

## Start
1. Load the skill `reservoir-engineer`. It selects the role and the skill for the task.
2. On Linux VS Code, use direct mode. Call the reservoir MCP server before the shell.
   It is read-only.
3. Follow `reservoir-engineer` for `site.contract.yml`. Do not invent site values.
4. Load `continuous-learning` for relevant approved lessons and end-of-task reflection.
5. Use `earned-autonomy` to define done, uncertainty, and retry limits for substantive tasks.
   Start a first or changed workflow with a text dry run.
6. On Hermes, a change to this repository (a new skill, a script, a refactor) is a different kind of task
   than a reservoir task. Load `pstack:poteto-mode` first, and copy its matched playbook into the todo list.
   `earned-autonomy` governs autonomy on reservoir work. `pstack` governs autonomy on this repository's own
   code and skills. Keep `make check` green before and after each change.
7. A case-qc command may run without a question when `tools/read_envelope.py` allows that exact command.
   The grant is `worklog/read-envelope.txt`.
8. When the user asks for a run (ERT, Everest, Flow, LSF), show the exact command and run it.
   Record the task and wrap each run with `skills/earned-autonomy/scripts/worklog.py` (`earned-autonomy` Step 7).
   Ask before a delete, a Sumo upload, a remote transfer, or sending project contents to an outside service.

## Skills
The folder `skills/` is the only source. `.claude/skills` and `.agents/skills` are links to it.

This table is the feature map. It names, for each skill, the input it needs and the check that must pass
before you trust its result. "check" is a file under `tests/` or `evals/` unless noted. Run checks with
`make check` (tests) or `python3 evals/runner.py --skill <name>` (evals).

| Skill | Role | Use | Inputs | Check |
|---|---|---|---|---|
| `stage-contract` | analyst | Check the site contract before ensemble or gate work. | a site contract, profile | `tests/test_stage_contract.py`, `tests/test_contract_lib.py`; `evals/stage-contract.md` |
| `ensemble-profiles` | analyst | Extract field and well summary profiles. | a contract, scratch ensemble | `tests/test_ensemble_profiles_bands.py`; `evals/ensemble-profiles.md` |
| `ensemble-bands` | analyst | Compute ensemble bands in the contract convention. | a contract, extracted profiles | `tests/test_ensemble_profiles_bands.py`; `evals/ensemble-bands.md` |
| `ensemble-at-date` | analyst | Read ensemble values at selected dates. | a contract, scratch ensemble, vectors, dates | `tests/test_ensemble_at_date_yearly.py`; `evals/ensemble-at-date.md` |
| `yearly-profiles` | analyst | Make yearly production and injection tables. | a contract, scratch ensemble, vectors | `tests/test_ensemble_at_date_yearly.py`; `evals/yearly-profiles.md` |
| `production-kpis` | analyst | Compute plateau, recovery factor, VRR, gas sales and binding constraints. | a contract, summary files or profiles, optional in-place table | `tests/test_production_kpis.py`; `evals/production-kpis.md` |
| `region-in-place` | analyst | Read region in-place volumes and check field sums. | summary files, dates, optional group map | `tests/test_region_in_place.py`; `evals/region-in-place.md` |
| `scenario-build` | analyst | Render a typed scenario spec (wells, targets, gas lift, network, dates) into an ECLIPSE100 SCHEDULE include, check it, declare its changes, and write the Everest controls section. | a contract or `--target`, a scenario spec, a case folder | `tests/test_scenario_build.py`; `evals/scenario-build.md` |
| `gas-lift` | analyst | Check gas-lift VFP ALQ axes and LIFTOPT/WLIFTOPT/GLIFTOPT, read lift gas use and group lift limits from SUMMARY, and sweep lift-gas levels for marginal oil per lift gas. | a deck, summary files, a scenario-build spec for sweeps | `tests/test_gas_lift.py`; `evals/gas-lift.md` |
| `scenario-qc` | analyst | Check one scenario against its baseline, including group, gas-lift, network, action and coupling keyword changes. | a contract, fingerprints, runmeta, profiles | `tests/test_scenario_qc.py`, `tests/test_deck_fingerprint_controls.py`; `evals/scenario-qc.md` |
| `scenario-matrix-qc` | analyst | Combine scenario checks in one matrix. | a contract, scenarios and their QC results | `tests/test_scenario_qc.py`; `evals/scenario-matrix-qc.md` |
| `uncertainty-register` | analyst | Draft a register, make sensitivity designs, and read tornado or attribution results. | a contract, prior files, design matrix, run summaries | `tests/test_uncertainty_register.py`, `tests/test_design_tornado.py`; `evals/uncertainty-register.md` |
| `prediction-case` | analyst | Render prediction cases and sample stated priors. | a contract, ERT template, parameters.csv | `tests/test_prediction_case.py`; `evals/prediction-case.md` |
| `resinsight-plots` | analyst | Export summary plot evidence. | a contract, profiles, bands, viewer settings | `tests/test_resinsight_plots.py`; `evals/resinsight-plots.md` |
| `resinsight-session` | analyst | Install ResInsight and rips, and check that they work together. | a contract profile `resinsight` block | `tests/test_resinsight_session.py`, `make check-resinsight`; `evals/resinsight-session.md` |
| `resinsight-views` | analyst | Make 3D views of one case from a ViewSpec: arrays, range filters, side cameras, well completions. Pictures only. | a case EGRID, a ViewSpec or `completion_in_grid.py` output, `resinsight.python` | `tests/test_resinsight_views.py`, `make check-resinsight`; `evals/resinsight-views.md` |
| `ahm-ensemble-views` | analyst | Compare a grid array of the prior and the posterior cell by cell: SDRATIO, SHIFT, P10/P90, collapse findings, ResInsight views. | prior and posterior runpath globs, one case EGRID, `resinsight.python` | `tests/test_ensemble_fields.py`, `make check-resinsight`; `evals/ahm-ensemble-views.md` |
| `ahm-analysis` | analyst | Run semeio AHM_ANALYSIS on a storage copy and read its KS table against the noise floor for N: top group per parameter, leave-one-out drops, spurious-correlation warning. | an ERT config, the prior ensemble name, ERT and semeio | `tests/test_ahm_analysis.py`; `evals/ahm-analysis.md` |
| `flow-diagnostics` | analyst | Injector-producer support and sweep heterogeneity from ResInsight flow diagnostics: fractions, residence time, inferred allocation, F-Phi, Lorenz. | a case with flux arrays (`RPTRST BASIC=2 FLORES`), `completion_in_grid.py` output, `resinsight.python` | `tests/test_flow_diagnostics.py`, `make check-resinsight`; `evals/flow-diagnostics.md` |
| `remaining-oil` | analyst | Remaining mobile oil per column and greedy infill candidates away from the wells, with ResInsight views. Screening only. | a case with EGRID, INIT and UNRST, SOR, optional `completion_in_grid.py` output | `tests/test_remaining_oil.py`, `make check-resinsight`; `evals/remaining-oil.md` |
| `qa-qc-preread` | analyst | Assemble a pre-read with open issues. | a contract, stage, existing deliverables | `tests/test_gate_packet.py`; `evals/qa-qc-preread.md` |
| `gate-packet` | analyst | Assemble a gate checklist and status. | a contract, stage, existing deliverables | `tests/test_gate_packet.py`; `evals/gate-packet.md` |
| `reservoir-engineer` | all | Select the role and the skill. | the task, `site.contract.yml` | `tools/check_skills.py`; `evals/reservoir-engineer.md` |
| `ert-diagnose` | analyst | Find why realizations failed. Read-only. | an ERT runpath | `tests/test_triage_runpath.py`, `tests/test_lesson_fixtures.py`; `evals/ert-diagnose.md` |
| `ert-ensemble-run` | operator | Start and monitor an ERT run when the user asks. | an `.ert` config, the run request | `tests/test_long_run.py`, `tools/check_skills.py`; `evals/ert-ensemble-run.md` |
| `fmu-drogon-conventions` | analyst | Find where a setting lives in an FMU repository. | an FMU repository path, the question | `evals/fmu-drogon-conventions.md` (reference lookup, no script) |
| `res2df-resdata-extract` | analyst | Extract tables and ensemble statistics. | a case or ensemble path, vectors, dates | `tests/test_ensemble_summary.py`, `tests/test_reek_fixture_integrity.py`; `evals/res2df-resdata-extract.md` |
| `ert-config-author-lint` | analyst | Map, check, and review ERT configs, including ES/ES-MDA update settings. Make a diff. | an `.ert` file and its includes | `tests/test_ert_config_tools.py`, `tests/test_ahm_qc.py`; `evals/ert-config-author-lint.md` |
| `everest-config-author-lint` | analyst | Check optimization configs, estimate cost, and link a posterior. | Everest YAML, a contract, posterior CSV or runpaths | `tests/test_everest_config.py`, `tests/test_everest_posterior.py`; `evals/everest-config-author-lint.md` |
| `everest-diagnostics` | analyst | Check optimization result tables, controls, failures, and CVaR. | result tables and an Everest config | `tests/test_everest_diagnostics.py`; `evals/everest-diagnostics.md` |

| `fmu-dataio-sumo` | analyst | Case metadata, exports, metadata checks, and Sumo. | a case path, its dataio config | `tests/test_check_dataio_metadata.py`; `evals/fmu-dataio-sumo.md` |
| `case-qc` | analyst | Run all read-only checks on one case and write one report. Has the runbook for a first run at your site. | a case or runpath, a fresh output directory | `tests/test_case_qc.py`; `evals/case-qc.md` |
| `flow-preflight` | analyst | Prepare a Flow command, run it when the user asks, inspect PRT text, or classify deck keywords by Flow support for the target simulator. The scripts never start Flow. | a flat site contract and a DATA file, or a PRT file; a DATA file and a profile or `--target` | `tests/test_flow_tools.py`, `tests/test_deck_support.py`; `evals/flow-preflight.md` |
| `scal-uncertainty` | analyst | Fit, check, screen, and wire SCAL uncertainty with pyscal. | a deck, engineer recommendation, optional ERT config | `tests/test_scal_rec.py`, `tests/test_scal_fit.py`, `tests/test_scal_wire.py`; `evals/scal-uncertainty.md` |
| `deck-scal-pvt-qc` | analyst | Check relperm, PVT, EQUIL, and VFP tables. Make SCAL input with pyscal. | a deck and its include files | `tests/test_deck_qc.py`, `tests/test_physics_checklist.py`; `evals/deck-scal-pvt-qc.md` |
| `grid-property-qc` | analyst | Check INIT grid properties and COMPDAT cells in the grid with resdata. Read-only. | a case with EGRID and INIT, optional DATA file | `tests/test_grid_property_qc.py`; `evals/grid-property-qc.md` |
| `history-match-qc` | analyst | Check the prior, observations and weights, and the update log. Compare prior and posterior. Find observations and parameters that need a look. Make one overview with influence, conflicts, noise, and static/dynamic attribution. | an ERT case, an observation file, GEN_KW dist files, the update log | `tests/test_history_match_qc.py`, `tests/test_ahm_coverage_defaults.py`, `tests/test_compensation_check.py`, `tests/test_misfit_handoff_integrity.py`, `tests/test_ahm_qc.py`, `tests/test_field_qc.py`, `tests/test_ahm_overview.py`, `tests/test_ahm_case_adapter.py`, `tests/test_lesson_fixtures.py`; `evals/history-match-qc.md` |
| `ahm-workflow` | analyst | Plan and review an ES or ES-MDA history match: prior, weights, config, screening, run, update log, misfit, decision memo. `ahm_preflight.py` refuses a run without a screening record; ERT runs through `ert-ensemble-run` when the user asks. | an `.ert` config, its dist and obs files, a one-by-one design, runpaths, the update log | `tests/test_ahm_qc.py`, `tests/test_ahm_screening_gate.py`; `evals/ahm-workflow.md` |
| `ahm-status-report` | analyst | One plot-first status report of an ES or ES-MDA history match from a scratch case: inventory, data match, ensemble health, review items, P10/P50/P90 totals, and an advisory verdict. Works posterior-only (iter-0 deleted). | a scratch case, an `.ert` config or obs and dist files | `tests/test_ahm_status_report.py`; `evals/ahm-status-report.md` |
| `hm-localization` | analyst | ERT 26 distance localization for FIELD and SURFACE updates: well coordinates, LOCALIZATION blocks, a config check, and a radius sweep by a replay of one ES update. No simulation. | a deck and EGRID, an obs file, an `.ert` config, its ERT 26 storage | `tests/test_hm_localization.py`; `evals/hm-localization.md` |
| `material-balance` | analyst | Tank model for oil and gas. In-place volume with its range, drive indices, and aquifer fit. | `history.csv`, `pvt.csv`, Swi, cw, cf, the unit system | `tests/test_material_balance.py` and the fixture check in `skills/material-balance/SKILL.md` Step 0; `evals/material-balance.md` |
| `continuous-learning` | all | Capture checked lessons with evidence and scope. Use a human-approved write grant. | a session, a correction or a checked result | `tests/test_skill_learning.py`; `evals/continuous-learning.md` |
| `earned-autonomy` | all | Define done, bound retries, record trials, and propose human-reviewed workflow authority. | a job contract, run receipts | `evals/earned-autonomy.md` (3 cases, no script) |
| `task-to-skill` | maintainer | Create or patch skills from checked lessons. Write tested scripts when needed, not stubs. | a worklog entry, an approved source checkout | `tools/check_skills.py`; `evals/task-to-skill.md` |

## Commands
| Command | Action |
|---|---|
| `make check` | Run ruff, the skill checker, and the tests in a fresh worktree: HEAD plus your uncommitted edits. Ignored and local-only files do not count. Tests that need ERT, res2df, or test data skip. |
| `make check-here` | The same checks in this directory, as it is. |
| `make hooks` | Turn on the pre-push hook. It runs the clean check on each commit you push. Actions is off, so this hook is the CI. |
| `make check-full PY=<komodo python>` | The same checks. A skipped test is a failure. This must pass before each commit. |
| `make check-resinsight RI=<bin> RIPY=<rips python>` | Run the tests with the marker `resinsight` against a real ResInsight. Linux only. |
| `make check-drogonlite DL=<drogon-lite clone> DLVENV=<ERT venv>` | Run the full drogon-lite ES-MDA (marker `drogonlite`, about 10 minutes) and grade the AHM skills against its synthetic truth with `tools/drogon_lite_bench.py`. Linux only. |
| `make helpers` | Fail if a script defines a function that `runpath_lib.py` or `contract_lib.py` owns. |
| `make test` | Run the tests. |
| `make skills` | Check the skills and run the STE lint. |
| `make evals` | Run the agent evals headless (costs model tokens). See `evals/README.md`. |
| `make release VERSION=x.y.z` | Fold `changes/*.md` into `CHANGELOG.md`, apply the skill bumps, and set `VERSION`. |
| `make update` | Fast-forward from upstream, report changed skills, link new skills, prune stale links, run the checks. Preview: `make update ARGS=--dry-run`. |
| `tools/install_user_skills.sh` | Link the skills for the user. Remove the approval gates that older versions installed. |

The repository version is in `VERSION` (equal to `pyproject.toml`). Record each change in a new `changes/<topic>.md`
(format in `changes/README.md`). Do not edit `CHANGELOG.md`.
Each `SKILL.md` has a semver `version:`. When a skill changes, add `bump: <skill> <level>` to the fragment.
Do not edit `version:`. `make release VERSION=x.y.z` writes `CHANGELOG.md` and the versions.
The site contract schema is `site.contract.schema.json`. Examples with profiles are in `profiles/`.

## Rules
- Write each SKILL.md in Simplified Technical English (asd-ste100, Strict mode). Run `make skills` after each edit.
- Keep a SKILL.md under 500 lines. Put long reference text in `references/`.
- Put each deterministic check in a script under the skill. Use only the Python standard library when possible.
- Read runpaths, realizations, DATA files and the contract with `skills/stage-contract/scripts/runpath_lib.py` and `contract_lib.py`. Do not copy their functions. `make helpers` fails on a copy.
  Scripts that need res2df must work with the version in Komodo.
- Test each script. Use synthetic fixtures or public data (Drogon, Reek, SPE9). Never commit project data.
- Do not commit `site.contract.yml` or `worklog/`. They hold facts that belong to one project.
- Scripts that read a runpath must not write inside it.
- Fail closed. A missing member, an unknown vector, or an unmatched path is an error. It is never a silent success.
- Do not copy confidential files, well names, or paths into this repository or into an external service.

## Learn without changing authority
- Use `docs/CONTINUOUS_LEARNING.md` for the VS Code learning and review workflow.
- A learning-only grant permits local lessons or drafts, not policy, hook, routing, or permission changes.
- A maintainer grant can permit exact source skill/routing edits for review. Preserve all unrelated edits.
- A person reviews skill activation. The agent cannot promote itself or publish through a lesson.
- `worklog/` stores private lessons, grants, and receipts. Gitignore is not endpoint protection.
- In a copied IDE workspace, do not patch one discovered copy or `.reservoir-agent` in isolation.
  Change the approved source checkout, review it, install to a fresh workspace, and verify discovery.

## Add a skill
1. Make `skills/<name>/SKILL.md` with the fields `name` and `description`. The name equals the folder name.
2. Write the description so that it says what the skill does and when to use it.
3. Add an eval in `evals/<name>.md` with at least three tasks and the expected result.
4. Add the skill to the table above, to `skills/reservoir-engineer/SKILL.md`, and to `docs/FEATURE_MAP.md`.
   `tools/check_skills.py` checks all three rows. This is a small, lint-checked cross-check, not a file that
   every task edits. Each row changes once, when a skill is added or renamed.
5. Run `make check`.

<!-- pstack:begin -->
<!-- GENERATED by tools/build_agent_surfaces.py - edit plugins/pstack instead -->

## pstack

Use these optional workflow skills for repository work.

- `architect`: `plugins/pstack/skills/architect/SKILL.md`.
- `blast-radius`: `plugins/pstack/skills/blast-radius/SKILL.md`.
- `correct`: `plugins/pstack/skills/correct/SKILL.md`.
- `figure-it-out`: `plugins/pstack/skills/figure-it-out/SKILL.md`.
- `how`: `plugins/pstack/skills/how/SKILL.md`.
- `interrogate`: `plugins/pstack/skills/interrogate/SKILL.md`.
- `no-comments`: `plugins/pstack/skills/no-comments/SKILL.md`.
- `poteto-mode`: `plugins/pstack/skills/poteto-mode/SKILL.md`.
- `principle-attack-the-premise`: `plugins/pstack/skills/principle-attack-the-premise/SKILL.md`.
- `principle-boundary-discipline`: `plugins/pstack/skills/principle-boundary-discipline/SKILL.md`.
- `principle-build-the-lever`: `plugins/pstack/skills/principle-build-the-lever/SKILL.md`.
- `principle-encode-lessons-in-structure`: `plugins/pstack/skills/principle-encode-lessons-in-structure/SKILL.md`.
- `principle-exhaust-the-design-space`: `plugins/pstack/skills/principle-exhaust-the-design-space/SKILL.md`.
- `principle-experience-first`: `plugins/pstack/skills/principle-experience-first/SKILL.md`.
- `principle-explain-the-number`: `plugins/pstack/skills/principle-explain-the-number/SKILL.md`.
- `principle-fix-root-causes`: `plugins/pstack/skills/principle-fix-root-causes/SKILL.md`.
- `principle-foundational-thinking`: `plugins/pstack/skills/principle-foundational-thinking/SKILL.md`.
- `principle-guard-the-context-window`: `plugins/pstack/skills/principle-guard-the-context-window/SKILL.md`.
- `principle-laziness-protocol`: `plugins/pstack/skills/principle-laziness-protocol/SKILL.md`.
- `principle-make-operations-idempotent`: `plugins/pstack/skills/principle-make-operations-idempotent/SKILL.md`.
- `principle-migrate-callers-then-delete-legacy-apis`: `plugins/pstack/skills/principle-migrate-callers-then-delete-legacy-apis/SKILL.md`.
- `principle-minimize-reader-load`: `plugins/pstack/skills/principle-minimize-reader-load/SKILL.md`.
- `principle-model-the-domain`: `plugins/pstack/skills/principle-model-the-domain/SKILL.md`.
- `principle-never-block-on-the-human`: `plugins/pstack/skills/principle-never-block-on-the-human/SKILL.md`.
- `principle-outcome-oriented-execution`: `plugins/pstack/skills/principle-outcome-oriented-execution/SKILL.md`.
- `principle-prove-it-works`: `plugins/pstack/skills/principle-prove-it-works/SKILL.md`.
- `principle-redesign-from-first-principles`: `plugins/pstack/skills/principle-redesign-from-first-principles/SKILL.md`.
- `principle-separate-before-serializing-shared-state`: `plugins/pstack/skills/principle-separate-before-serializing-shared-state/SKILL.md`.
- `principle-sequence-verifiable-units`: `plugins/pstack/skills/principle-sequence-verifiable-units/SKILL.md`.
- `principle-subtract-before-you-add`: `plugins/pstack/skills/principle-subtract-before-you-add/SKILL.md`.
- `principle-test-behavior-not-implementation`: `plugins/pstack/skills/principle-test-behavior-not-implementation/SKILL.md`.
- `principle-type-system-discipline`: `plugins/pstack/skills/principle-type-system-discipline/SKILL.md`.
- `show-me-your-work`: `plugins/pstack/skills/show-me-your-work/SKILL.md`.
- `swarm`: `plugins/pstack/skills/swarm/SKILL.md`.
- `tdd`: `plugins/pstack/skills/tdd/SKILL.md`.
- `technical-writing`: `plugins/pstack/skills/technical-writing/SKILL.md`.
- `unslop`: `plugins/pstack/skills/unslop/SKILL.md`.
- `why`: `plugins/pstack/skills/why/SKILL.md`.

Claude Code discovers `skills/pstack-*` through `.claude/skills`. Codex discovers the same entries through `.agents/skills`. See `docs/PSTACK_AGENTS.md` for harness setup and limitations.
<!-- pstack:end -->
