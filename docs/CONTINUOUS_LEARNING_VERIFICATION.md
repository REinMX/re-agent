# Continuous-learning change: verification record

Recorded at: 2026-10-04T22:09:28Z (live UTC host clock).
Branch: `improve/komodo-vscode-readiness`.
Starting/current committed HEAD: `5bd0dda473323b678cccb29203e988f208035b59`.
The feature is uncommitted working-tree content. The checkout already contained substantial unrelated
changes before this task. They were preserved. No commit, push, remote write, or site deployment was made.

## Delivered source

- `skills/continuous-learning/`: correction/experience capture, scoped evidence, lesson records, and write grants.
- `skills/earned-autonomy/`: job contracts, bounded retries, trial/promotion evidence, receipts and compact handoffs.
- `skills/task-to-skill/`: new-skill creation and existing-skill patches, regression evidence, review and copied-install activation.
- `AGENTS.md` and `skills/reservoir-engineer/SKILL.md`: routing and maintainer scope.
- `tools/check_skills.py`: registry/router column and manual-eval packaging validation.
- `tests/test_skill_learning.py`: 14 new test cases, including source-private-record exclusion and copy conflicts.
- Six public text-only headless behavior cases plus matching manual eval specifications.
- `docs/CONTINUOUS_LEARNING.md`: design, selected advice, grants, commands and site-pilot acceptance.

## Actual execution

| Check | Observed result |
|---|---|
| Pre-change full baseline, existing ERT/FMU development interpreter | 577 passed, no skips, 31 dependency warnings |
| Initial new-feature RED | 11 failures for absent skills/registration validation |
| Review-discovered wrong-column regression RED | 2 failures, confirming both false positives |
| Final focused learning/checker suite | 20 passed |
| `make check-full PY=~/projects/ert_fmu/.venv/bin/python` | 591 passed, no skips, 31 dependency warnings, exit 0 |
| `make check PY=.venv/bin/python` | 391 passed, 20 skips due to missing optional reservoir dependencies, exit 0 |
| Ruff check and format check in both environments | Passed |
| Skill checker, including available structural STE lint | 14 skills checked, 0 failures |
| `git diff --check` | Passed |
| Original operation-approval section versus pre-task content | Exactly unchanged |

The lean environment cannot pass `check-full`: its skipped reservoir coverage is a failure at that gate.
The existing local ERT/FMU environment supplied full coverage without package installation or changing shared Komodo.
The 31 full-suite warnings concern existing dependency deprecations/configuration behavior, not suppressed failures.

## Independent review

The first reviewer found a concrete registration-check false positive: mentioning a skill in another row
could count as registration. Two tests reproduced it before the fix. The checker now requires the actual
first skill-name column in `AGENTS.md` and the second destination-skill column in the router.
A supplemental synthetic fixture verifies that a fresh install excludes local lessons, grants, receipts
and the private site contract, not just that a refused reinstall preserves an existing lesson.

The final fresh reviewer returned `passed: true`, with no security concerns, logic errors or suggestions.
It reviewed only the task-scoped source diff, not all pre-existing changes. The parent then reconstructed
that diff and confirmed the current reviewed source matched it exactly. The full-suite execution above
was performed by the parent; reviewer self-report was not substituted for pytest evidence.

## Not executed or certified

- The six new model-driven behavior evals were added but not run. They consume configured agent/judge tokens.
- No corporate Komodo/endpoint policy, actual enterprise VS Code extension behavior or live skill discovery was certified.
- No simulator/ERT job, queue action, Sumo upload, external transfer, schedule, or automatic promotion was performed.
- No existing copy-installed workspace was updated. Reviewed source needs a fresh approved install and discovery check.
- No future-session lesson load, revoked-grant behavior, new-skill activation or three/five-run site trial was claimed.

Use `CONTINUOUS_LEARNING.md` for the separate approved site pilot. Markdown guidance and packaging tests
are not an OS security boundary, DLP certification, or permission to submit field data to a model.
