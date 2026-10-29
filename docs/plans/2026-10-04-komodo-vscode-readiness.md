# Komodo and Windows VS Code Readiness Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. If delegation is unavailable, record that limit, follow the same test-first gates locally, and do not claim independent approval.

**Goal:** Make the existing reservoir skills a trustworthy, portable assistant for a Windows VS Code user whose engineering tools live on a separate Komodo/Linux host.

**Architecture:** Keep `skills/` as the source of truth and use the existing open-source FMU readers. Separate the agent host from the execution host; support manual command handoff first, remote agents only when permitted. Add local deterministic preflight checks rather than an autonomous SSH/scheduler service.

**Tech Stack:** Python >=3.11; standard library deployment tools; existing pandas/numpy/res2df/resdata/ERT integration; pytest and Ruff.

## Baseline and boundaries

- Baseline commit: `5bd0dda`; clean `main` before work.
- `make check PY=.venv/bin/python`: 116 passed; 8 skips (including module-level skips).
- `make check-full PY=~/projects/ert_fmu/.venv/bin/python`: 222 passed, no skips, 31 dependency warnings.
- Bare `make check` fails here because the system interpreter lacks Ruff; this is interpreter discovery debt, not a failed scientific test.
- Actual corporate Komodo releases, approved endpoints, remote extension availability, LSF, Sumo and Windows runtime are not available for acceptance here.
- No internal data, simulator runs, queue jobs, global agent config edits or GitHub publication.
  Official public documentation was consulted. Independent review model requests were attempted but
  both failed with provider HTTP 402; deterministic tests/evals made no model calls.

## Roadmap

### M1 — Correctness before deployment (implement now)

Files: summary/observation/misfit/material-balance/case-QC scripts and focused tests.

Acceptance:
- A known vector plus an unknown vector cannot yield success.
- Member vector sets, requested output calendars, units and finite values must agree before statistics.
- Missing observation predictions cannot produce a green prior/posterior comparison.
- Nonfinite material-balance input, overflow and invalid dates fail before output files are written.
- Reusing a populated case-report directory is rejected before any step runs; stale manifests cannot contaminate a report.
- Existing public Reek and material-balance reference tests remain valid.

### M2 — Honest evaluation (implement now)

Files: `evals/runner.py`, `tests/test_eval_runner.py`, eval documentation.

Acceptance:
- Nonzero harness exit, missing terminal-success event or malformed judge booleans cannot pass.
- Any skipped selected case fails the overall run by default; `--allow-skips` is explicit and cannot turn an all-skipped run green.
- Harness model defaults do not force a Claude model name on Codex.
- No paid/model eval is needed for deterministic runner regression tests.

### M3 — Guardrail truth (implement now)

Files: `tools/gate_hook.py`, `.claude/settings.json`, `.codex/rules/gates.rules`, router and tests.

Acceptance:
- Direct simulator commands, remote commands and opaque shell constructs request review.
- Malformed hook input emits a deny decision instead of failing open.
- Existing safe diagnostics remain available.
- Documentation states explicitly that hooks/prefix rules are not a sandbox, data-loss-prevention system or protection against arbitrary Python/PowerShell/MCP execution.
- Offline `codex execpolicy check` exercises rules without executing the example command.

### M4 — Thin portable deployment (implement now)

Files: new `tools/install_project.py`, `tools/doctor.py`, tests, guide, Makefile and CI.

Acceptance:
- A Python copy-based project install works without symlinks, including a Windows checkout where Git represents a symlink as a text file.
- Dry-run is the default; explicit `--apply` is required; conflicting files stop the entire install before writes.
- Preserve existing instructions/settings; do not modify global user config.
- Skills appear under `.claude/skills` and `.agents/skills` in the target workspace.
- Doctor reports interpreter/package availability and validates a local site contract without activating environments, importing ERT plugins, contacting endpoints or submitting jobs.
- Separate Windows/manual and Linux/direct modes; do not test a Linux path on the Windows agent host.
- Run unit checks on Windows and Linux in CI; actual workflow approval remains a site acceptance gate.

### M5 — Site pilot (future, blocked on corporate access)

The owner and team check approved harness and endpoint, host/topology, data classification, Komodo release and queue policy. Execute doctor on the Linux host, test the actual extension's approval UI with a harmless/declined operation, then run public Drogon/Reek QC and one separately approved ERT smoke. Save local evidence, not confidential reports in this repo. Only after this add real-field read-only use; only later consider typed MCP or a restricted remote executor.

Non-goals: new reservoir physics, autonomous SSH, credential management, queue abstraction, replacing Komodo packages, cloud agent uploads, nightly jobs, claiming company certification.

## Execution slices (test-first)

### Task 1 — Strict ensemble output

Modify `skills/res2df-resdata-extract/scripts/ensemble_summary.py`; create `tests/test_scientific_contract.py`.

1. Add parametrized tests injecting returned reader frames for a missing requested key, differing member keys/dates/units, NaN/Inf and invalid member count. Preserve the real reader integration tests.
2. Run the focused tests with the lab Python; record expected failures before changing code.
3. Validate each frame before accepting a member; never silently intersect vector sets or coerce invalid cells away. Verify per-statistic counts and finite derived values.
4. Run focused plus existing summary tests. Validate a real Reek extraction.

### Task 2 — Observation, misfit and material-balance integrity

Modify `obs_qc.py`, `misfit_report.py`, `mbal.py`; create focused contract tests.

1. Test nonfinite observation/error input and derived errors, incomplete residual coverage, incomparable member sets, duplicate members, invalid numeric MB input/date order and overflow.
2. Observe RED.
3. Reject invalid input and incomplete comparisons; `--allow-partial` may write diagnostic evidence but returns failure. Keep valid scientific reference formulas/results unchanged.
4. Run focused tests and existing reference/integration tests.

### Task 3 — Fresh case-report boundary

Modify `case_qc.py`; create standard-library-only case-report tests.

1. Test a populated output directory and stale manifests without launching any child command.
2. Observe RED.
3. Require an empty report directory before running; reject outdir overlapping the supplied input tree/runpaths.
4. Test a fresh report and rerun refusal.

### Task 4 — Evaluation status

Modify `evals/runner.py`; extend `tests/test_eval_runner.py`.

1. Add synthetic protocol records for failure terminal status, nonzero exit, absent completion and malformed judge types; test skipped-selection result code and harness-specific defaults.
2. Observe RED.
3. Make terminal/exit validity a required independent gate. Require literal boolean judge answers. Make skipped coverage fail by default.
4. Run deterministic tests only; no LLM calls.

### Task 5 — Approval diagnostic hardening

Modify hook, settings, rules and router together; extend gate tests.

1. Add tests for Flow/Eclipse, SSH, shell `-lc`, wrappers and malformed payloads.
2. Observe RED.
3. Add conservative review categories and fail-closed input handling, while preserving ordinary safe diagnostics.
4. Run gate tests and real offline Codex policy checks. Do not execute gated commands.

### Task 6 — Project install and doctor

Create deployment tools and `tests/test_deployment.py`; update guide/CI/Makefile.

1. Test dry-run/no writes, install into a temp path with spaces, copy-based skill discovery, collision preflight/no partial install and ignored contract handling.
2. Test doctor against a synthetic contract, placeholders/unknown fields, endpoint approval false, wrong interpreter, missing directories and Windows/manual mode.
3. Observe RED; implement minimal stdlib tools; run GREEN and actual temp-workspace CLI smoke.
4. Rewrite Windows-first guide, including a copy-ready handoff prompt and local data/model boundary.

## Final acceptance and evidence

Run lint/format, skill checks, lean and full pytest, compile checks, portable install/doctor CLI smokes, public Reek extraction and material-balance CSV smoke. Compare initial/final worktree status and report commands/counts truthfully. Record open site gates in `docs/REVIEW_2026-10-04.md`. Obtain independent review when a provider is available; do not label local self-review as independent. Keep the work on a dedicated branch; publish only with user approval.

### Execution status

M1–M4 local implementation is exercised: full gate 307 passed/no skips; lean gate 191 passed/18 skips;
12 skills and Ruff checks pass. Public Reek, OILBOX CSV and temporary workspace/doctor/hook smokes are saved
under `reports/readiness-2026-10-04/`. The declared Windows CI matrix is configured, not executed.

Outstanding before workplace rollout: approved update of protected `AGENTS.md` startup/link wording,
independent review (both provider requests failed HTTP 402), licensing/rehosting decision, and M5's
actual Windows/IDE/Komodo/endpoint/queue acceptance. No automatic SSH or simulator run was added.
