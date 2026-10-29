# Feature map

For each skill: when to use it, the input it needs, and the check that must pass before you trust its
result. "Check" is a file under `tests/` or `evals/` unless noted. Run checks with `make check` (tests) or
`python3 evals/runner.py --skill <name>` (evals). This file is a read-only view. A new skill gets a row in
the routing table in `AGENTS.md`, in the task table in `skills/reservoir-engineer/SKILL.md`, and in this file.
`make check` fails when one of the three rows is missing.

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
| `ert-ensemble-run` | operator | Start and monitor an ERT run when the user asks. | an `.ert` config, the run request | `tools/check_skills.py`; `evals/ert-ensemble-run.md` |
| `fmu-drogon-conventions` | analyst | Find where a setting lives in an FMU repository. | an FMU repository path, the question | `evals/fmu-drogon-conventions.md` (reference lookup, no script) |
| `res2df-resdata-extract` | analyst | Extract tables and ensemble statistics. | a case or ensemble path, vectors, dates | `tests/test_ensemble_summary.py`, `tests/test_reek_fixture_integrity.py`; `evals/res2df-resdata-extract.md` |
| `ert-config-author-lint` | analyst | Map, check, and review ERT configs, including ES/ES-MDA update settings. Make a diff. | an `.ert` file and its includes | `tests/test_ert_config_tools.py`, `tests/test_ahm_qc.py`; `evals/ert-config-author-lint.md` |
| `everest-config-author-lint` | analyst | Check optimization configs, estimate cost, and link a posterior. | Everest YAML, a contract, posterior CSV or runpaths | `tests/test_everest_config.py`, `tests/test_everest_posterior.py`; `evals/everest-config-author-lint.md` |
| `everest-diagnostics` | analyst | Check optimization result tables, controls, failures, and CVaR. | result tables and an Everest config | `tests/test_everest_diagnostics.py`; `evals/everest-diagnostics.md` |
| `fmu-dataio-sumo` | analyst | Case metadata, exports, metadata checks, and Sumo. | a case path, its dataio config | `tests/test_check_dataio_metadata.py`; `evals/fmu-dataio-sumo.md` |
| `case-qc` | analyst | Run all read-only checks on one case and write one report. Has the runbook for a first run at your site. | a case or runpath, a fresh output directory | `tests/test_case_qc.py`; `evals/case-qc.md` |
| `flow-preflight` | analyst | Prepare a Flow command, run it when the user asks, inspect PRT text, or classify deck keywords by Flow support for the target simulator. The scripts never start Flow. | a flat site contract and a DATA file, or a PRT file; a DATA file and a profile or `--target` | `tests/test_flow_tools.py`, `tests/test_deck_support.py`; `evals/flow-preflight.md` |
| `scal-uncertainty` | analyst | Fit, check, screen, and wire SCAL uncertainty with pyscal. | a deck, engineer recommendation, optional ERT config | `tests/test_scal_rec.py`, `tests/test_scal_fit.py`, `tests/test_scal_wire.py`; `evals/scal-uncertainty.md` |
| `deck-scal-pvt-qc` | analyst | Check relperm, PVT, EQUIL, and VFP tables. Make SCAL input with pyscal. | a deck and its include files | `tests/test_deck_qc.py`; `evals/deck-scal-pvt-qc.md` |
| `grid-property-qc` | analyst | Check INIT grid properties and COMPDAT cells in the grid with resdata. Read-only. | a case with EGRID and INIT, optional DATA file | `tests/test_grid_property_qc.py`; `evals/grid-property-qc.md` |
| `history-match-qc` | analyst | Check the prior, observations and weights, and the update log. Compare prior and posterior. Find observations and parameters that need a look. Make one overview with influence, conflicts, noise, and static/dynamic attribution. | an ERT case, an observation file, GEN_KW dist files, the update log | `tests/test_history_match_qc.py`, `tests/test_misfit_handoff_integrity.py`, `tests/test_ahm_qc.py`, `tests/test_field_qc.py`, `tests/test_ahm_overview.py`, `tests/test_ahm_case_adapter.py`, `tests/test_lesson_fixtures.py`; `evals/history-match-qc.md` |
| `ahm-workflow` | analyst | Plan and review an ES or ES-MDA history match: prior, weights, config, run, update log, misfit, decision memo. No scripts; runs ERT through `ert-ensemble-run` when the user asks. | an `.ert` config, its dist and obs files, runpaths, the update log | `tests/test_ahm_qc.py`; `evals/ahm-workflow.md` |
| `ahm-status-report` | analyst | One plot-first status report of an ES or ES-MDA history match from a scratch case: inventory, data match, ensemble health, review items, P10/P50/P90 totals, and an advisory verdict. Works posterior-only (iter-0 deleted). | a scratch case, an `.ert` config or obs and dist files | `tests/test_ahm_status_report.py`; `evals/ahm-status-report.md` |
| `hm-localization` | analyst | ERT 26 distance localization for FIELD and SURFACE updates: well coordinates, LOCALIZATION blocks, a config check, and a radius sweep by a replay of one ES update. No simulation. | a deck and EGRID, an obs file, an `.ert` config, its ERT 26 storage | `tests/test_hm_localization.py`; `evals/hm-localization.md` |
| `material-balance` | analyst | Tank model for oil and gas. In-place volume with its range, drive indices, and aquifer fit. | `history.csv`, `pvt.csv`, Swi, cw, cf, the unit system | `tests/test_material_balance.py` and the fixture check in `skills/material-balance/SKILL.md` Step 0; `evals/material-balance.md` |
| `continuous-learning` | all | Capture checked lessons with evidence and scope. Use a human-approved write grant. | a session, a correction or a checked result | `tests/test_skill_learning.py`; `evals/continuous-learning.md` |
| `earned-autonomy` | all | Define done, bound retries, record trials, and propose human-reviewed workflow authority. | a job contract, run receipts | `evals/earned-autonomy.md` (3 cases, no script) |
| `task-to-skill` | maintainer | Create or patch skills from checked lessons. Write tested scripts when needed, not stubs. | a worklog entry, an approved source checkout | `tools/check_skills.py`; `evals/task-to-skill.md` |

## Notes on checks that are not one script

- `ert-ensemble-run` and `earned-autonomy` have no deterministic script. Their check is the eval file:
  the agent shows the exact command and runs it when the user asks.
- `fmu-drogon-conventions` is a reference lookup over `references/drogon-map.md`. Its only check today is
  the manual eval file. A wrong answer here is a wrong file path, not a wrong number; treat it as a lead,
  verify the path before you act on it.
- Three scripts carry an identical copy of `parse_realizations` (`ert-diagnose`, `res2df-resdata-extract`,
  `history-match-qc`). This is deliberate: each skill script must stay portable and stdlib-only, so there is
  no shared module for it to import. `tests/test_realization_spec.py` fails if the three copies drift apart.
  Treat that test, not a refactor into a shared helper, as the fix if you ever see it fail.
