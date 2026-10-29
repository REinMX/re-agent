# Acceptance integrity implementation plan

> **For Hermes:** Use subagent-driven-development to implement and review each unit.

**Goal:** Reject repeated or incomplete scientific evidence, restore reproducible local checks, and validate a reservoir-only source export.

**Architecture:** Each existing command owns its input invariants. Parameter ensembles retain member identities and fingerprints. Coverage validates its complete supplied domain before statistics. The benchmark accepts only fresh, complete, successful reports. The exporter owns any reservoir-only projection; missing vendor source alone never authorizes a relaxed checkout check.

**Tech stack:** Python 3.11, standard library, pytest, Ruff. Scientific regressions use an existing NumPy/pandas environment. ERT, res2df, Flow, Windows, live IDE behavior, and corporate acceptance are separate integration gates.

## Scope and done predicate

Base is `278fe2ceaf7c6d3a37151292bcabc687d7542141`. Original `main` stays unchanged. Work lands locally on `fix/acceptance-integrity` through isolated worktrees. No push, Actions enablement, simulator run, corporate policy change, or project-wide licensing decision belongs to this task.

Done requires all of the following:

- The original repeated-input, missing-parameter, missing-observation, and unequal-member counterexamples fail closed.
- Explicit seed identities determine claim readiness. Repeated evidence cannot become independent through labels, paths, row order, or seed declarations.
- Existing scoring formulas and scientific thresholds remain unchanged on valid complete inputs.
- The stored 99-row posterior remains unchanged and becomes a rejection fixture.
- Benchmark grading refuses subprocess failures, incomplete/malformed reports, missing RFT evidence, stale outputs, and input/output overlap.
- Sanitized lean checks and targeted richer-environment scientific regressions pass. Optional integration coverage remains visible. No suppressed scientific failures or widened tolerances.
- The actual committed source export passes its documented checks, installs into a fresh scratch workspace, and has verifiable installed files.
- The existing pre-push hook is exercised locally and configured after checks pass. GitHub Actions remains disabled.
- Spec review and then quality review have no unresolved material issues. Final acceptance comes from parent tool output, not child summaries.

## Design synthesis

Candidates A and B both select local boundary validation over a shared acceptance framework. Use that consensus. Prefer additive identity fields and an explicit parameter-ensemble structure. Coverage's external expected observation/member inventories may remain optional for standalone callers, but the supplied table must always be finite, rectangular, unambiguous, and complete. Callers with authoritative inventories, including the benchmark, must pass them.

Seed claims retain canonical prior and posterior fingerprints separately, computed from validated member-keyed parameter values. Independent-run readiness rejects duplicate declared seeds or duplication of either ensemble component. A combined pair hash alone is insufficient. Fingerprints detect replay, not statistical independence. Regression tests cover identical prior with different posterior, different prior with identical posterior, copied/reordered inputs, and distinct complete positive controls. Compare numerical results on unchanged complete fixtures before and after the fix.

For distribution, prefer exporter-owned projection with explicit delivered-byte provenance over implicit runtime fallback on missing vendor files. Any generated instructions in exported bytes are projections, not edits to the source AGENTS.md. Keep full-checkout validation strict. Do not modify source AGENTS.md or CLAUDE.md without a separate approval.

Rubric for the design cross-judge is correctness of identity/completeness, unchanged valid-input science, fail-closed benchmark output lifecycle, minimal interfaces, export/validation coherence, and executable acceptance evidence. Same-model independent review is not cross-family review; the Hermes adapter preserves the selected model route.

## Unit 1. Reproducible lean validation

**Owner:** `fix/reproducible-validation` worktree.

**Files:** `tools/check_skills.py`, `Makefile`, `tests/test_check_skills.py`, `tests/test_ahm_analysis.py`, `tests/test_flow_tools.py`, `tests/test_ahm_screening_gate.py`, focused new validation tests, `pyproject.toml` if a documented optional test extra is useful, `README.md`, and `changes/reproducible-validation.md`.

1. Write tests showing default checks must not discover host-only prose tools, explicit invalid linter requests must fail, and vendor aliases must not enter owned-prose scans.
2. Run those tests against the base and record expected RED failures.
3. Remove automatic home discovery. Expose an explicit opt-in prose check with its selected artifact identity. Keep mandatory metadata, registry, helper, and generated-file checks.
4. Guard optional NumPy/pandas imports in the owned screening test. Science owns guards in the compensation and coverage tests.
5. Make dependency-refusal tests independent of installed ERT/semeio ordering. Separate text-parser tests from optional native OPM tests without changing runtime defaults.
6. Run focused lean tests and Ruff. Record that full lean collection awaits the scientific sibling's guards. Commit verified unit changes.

## Unit 2. Scientific evidence integrity

**Owner:** `fix/qc-evidence-integrity` worktree.

**Files:** `skills/history-match-qc/scripts/compensation_check.py`, `coverage.py`, `misfit_report.py`, necessary `prior_qc.py` support only if unavoidable, `skills/case-qc/scripts/case_qc.py`, `tests/test_compensation_check.py`, `tests/test_ahm_coverage_defaults.py`, focused new scientific integrity tests, affected regression callers, and `changes/qc-evidence-integrity.md`.

1. Add standard-library-compatible rejection tests for missing parameters/members and repeated evidence, and NumPy-specific tests for coverage strictness.
2. Run each new group against the original code and save RED output.
3. Preserve integer member identities and canonical evidence fingerprints at parsing. Validate declared parameter coverage and matched member domains before scoring.
4. Add explicit seed identity. Unseeded diagnostics may run but cannot claim readiness. Repeated evidence and duplicate supplied seeds refuse multi-seed claims.
5. Reject nonfinite inputs, incomplete supplied truth, duplicate observation/member keys, inconsistent observation metadata, and unequal member sets. Pass authoritative inventories from misfit reporting where available.
6. Migrate in-repo direct callers and tests. Preserve scored formulas, thresholds, and report keys where valid. Add optional-dependency guards before compensation/coverage test imports.
7. Keep incomplete measured data unchanged. Use it for rejection. Use clearly synthetic complete ensembles for positive controls.
8. Run focused richer-environment regressions, lean rejection tests, and Ruff. Commit verified unit changes.

## Unit 3. Fail-closed benchmark acceptance

**Owner:** A fresh worker after scientific interfaces are committed and integrated.

**Files:** `tools/drogon_lite_bench.py`, `tests/test_drogon_lite_bench.py`, new focused benchmark acceptance tests, and `changes/benchmark-acceptance.md`.

1. Add failing cases for nonzero reader exit with an existing manifest, partial/malformed manifests, omitted observations/RFT, stale output, and overlap.
2. Record RED using controlled synthetic subprocess fixtures. No simulator starts.
3. Resolve expected members/configured observations through existing readers. Pass expected counts and ERT configuration; remove permissive partial reporting.
4. Require exclusive fresh output creation inside the callable operation. Preserve failed-command logs, never reuse old evidence or emit a successful report after an input failure.
5. Validate exact expected member/observation domains before scientific grading.
6. Run focused tests and valid synthetic positive controls. Native public benchmark remains explicitly unverified. Commit verified unit changes.

## Unit 4. Coherent reservoir-only source handoff

**Owner:** `fix/export-validation` worktree. Do not edit the source `Makefile`, `tools/check_skills.py`, AGENTS.md, or CLAUDE.md concurrently with other units.

**Files:** `tools/export_source.py`, export/deployment tests, a separated checkout-only distribution test module if needed, the source-copy guide (since removed), and `changes/export-validation.md`. A narrowly justified `tools/build_agent_surfaces.py` change is allowed only if full-checkout corruption still fails and explicit verified distribution metadata governs behavior.

1. Add a failing real-target snapshot/export/extraction regression reproducing the retained vendor check.
2. Define one exporter-owned reservoir-only component projection. Record transformed delivered hashes and original source provenance. Reject malformed marked instructions.
3. Remove or correctly project unavailable vendor instructions and validation. Keep scientific tests and mandatory reservoir checks. Do not skip arbitrary failures based only on missing files.
4. Separate repository-only Git/vendor assertions from exported runtime/fixture-based checks. Document `make check-here` for ZIPs without Git; preserve strict full integration semantics.
5. Commit the candidate before testing actual committed export behavior.
6. Create a real ZIP, extract it, run its documented checks, install to a fresh scratch workspace, verify discovery trees and installed hashes, and test an identical rerun.
7. Commit verified unit changes and preserve real execution logs.

## Integration and review

Cherry-pick one completed unit at a time into the dedicated integration branch. Inspect diffs and rerun its focused command. Keep one writer per worktree. Any conflicts are resolved in the integration worktree only.

Run spec review before quality review. Reviewers inspect frozen commit identities and execute targeted tests, not concurrent full suites on this small host. Fix material findings using a fresh worker and new RED evidence. Final integration review covers cross-unit calls, manifest compatibility, export content, validation behavior, and hook enforcement.

Mandatory final commands use sanitized environment variables:

```bash
env -u PYTHONPATH -u PYTHONHOME make check PY=~/reservoir-engineer-agent/.venv/bin/python
env -u PYTHONPATH -u PYTHONHOME make test PY=~/fmu-ensemble-research/.venv/bin/python
```

The richer interpreter is a host-specific existing development environment, not proof of native integration coverage. Full native checks can remain blocked by missing dependencies and absent approved public runs; state that explicitly.

Exercise the pre-push hook with a deliberately failing synthetic local commit and no network push. After final local checks pass, configure the existing hook through `make hooks` and read back the effective setting. Do not enable Actions or modify another Hermes profile.

## Evidence and handoff

Canonical append-only decision log is outside the repository at `~/.hermes/reports/reservoir-agent-remediation-278fe2c/decisions.tsv`. Worker evidence belongs in separate files beside it. Keep reviewed source identity, RED/GREEN commands and logs, final tests, export/install results, and review verdicts in the handoff. No raw field data belongs in artifacts.

Deliver the local branch, commits, exact checks and limits. A public pilot on the actual target host, corporate endpoint approval, Windows discovery, native model runs, and publication remain separate actions.
