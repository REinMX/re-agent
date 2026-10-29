# Reservoir engineer agent

Portable skills and deterministic tools for Claude Code or Codex in FMU/ERT reservoir projects.
This is **not** an autonomous reservoir simulator or a replacement agent framework.

## Recommended starting workflow

Linux VS Code on the execution host is the primary harness. Open this repository, trust the
workspace, and use Claude Code or Codex. The reservoir MCP server loads from this checkout. Windows/manual handoff is only for a Windows agent process.

First test, public data only: `docs/PUBLIC_FLOW_DRY_RUN.md`. Do not point that test at a
sensitivity study. Do not delete that study.

Windows VS Code agent → prepares reviewed Linux commands → you execute them in the approved
Komodo terminal → interpret only results that may enter the approved model endpoint.
Remote agent execution is optional and must be approved/tested at your site. Hermes is not required.

## What is useful today

- Failed-realization diagnosis; config, observation, deck/SCAL/PVT and metadata checks.
- Ensemble extraction and history-match diagnostics with explicit members, dates, vectors and units.
- CSV-first oil/gas tank material balance, including aquifer/identifiability diagnostics.
- Scoped copy-based workspace installation without Windows symlink privileges.
- Local contract/environment preflight that does not start jobs or contact services.
- Evidence-backed continuous learning: scoped lessons, reviewed skill patches, and tested skill creation.
- Workflow contracts, bounded retries, trial receipts, and human-reviewed workflow autonomy.

## Site translation pack

`templates/site/` holds five case templates from the checked Drogon workflow: prior design, ES-MDA with QC and a
decision memo, robust well control (mean and CVaR), drill order, and an Everest algorithm check.
`tools/render_template.py` renders one into a folder outside this repository. `{{site:<key>}}` tokens come from the
selected contract profile; `{{param:<name>}}` tokens come from `--set` or the template `params.yml`. A missing value
stops the render with file:line and nothing is written. See `templates/site/README.md`.

```bash
python tools/render_template.py templates/site/02-ahm-esmda-qc ../ahm/02-ahm-esmda-qc --profile company \
  --set agent_root=$PWD --set deck_dir=/path/to/deck
```

## Trust boundary

Read-only is a workflow policy, not an OS-enforced property of this repository. This repository has
no command hook and no sandbox or DLP system. When you ask for a run (ERT, Everest, Flow, LSF), the agent
shows the exact command and runs it. Verify actual IDE behavior and harness permissions.
`.gitignore` does not prevent data reaching a model. No company or Komodo deployment is certified here.

Strict checks reject incomplete/nonfinite summary data and missing observation predictions. Partial
reports remain failures. Case-QC reports require fresh output directories. Agent evals also require a
successful harness completion; skipped coverage fails by default. Tests reduce risk, not eliminate it.

| Read | Purpose |
|---|---|
| `docs/GITHUB_HANDOFF_REVIEW.md` | Refreshed handoff verification, review-driven fixes and remaining boundaries |
| `COPYING.md` | GPL-3.0-or-later license and third-party terms |
| `docs/REVIEW_2026-10-04.md` | Honest assessment, verified evidence and remaining limitations |
| `docs/plans/2026-10-04-komodo-vscode-readiness.md` | Implemented readiness plan and future site pilot |
| `docs/SITE_SETUP.md` | Windows/manual and optional Linux/direct setup, site contract profiles, doctor |
| `AGENTS.md` | Skill routing, commands and policies |
| `docs/CONTINUOUS_LEARNING.md` | VS Code learning, safe skill authoring, and adapted operating advice |
| `evals/README.md` | Agent-eval protocol and model/data boundary |
| `docs/NEXT_PHASES.md` | Longer-term skill-growth ideas (not deployment acceptance) |

Linux developer checks:

```bash
make check PY=.venv/bin/python             # lean; reports skips
make check-full PY=/approved/bin/python    # full; skips fail
python tools/doctor.py --mode execution --contract site.contract.yml --json
```

Check levels:

- Lean: `make check` with a Python that has pytest and ruff. The skill checks do not use tools from your
  home folder. Tests that need NumPy, pandas, SciPy, ERT, semeio or res2df skip. The skip list prints at the end.
- Scientific: `make check` with a Python that also has NumPy, pandas and SciPy runs the numeric tests.
  `make check-full` with the Komodo Python (ERT, semeio, res2df, pyscal) runs all tests. There a skipped
  test is a failure (`REQUIRE_FULL=1`).
- Prose (optional): `make ste STE_LINT=/path/to/ste-lint.py` (or `make prose`) runs the STE linter that you
  name on the `SKILL.md` and `references/**/*.md` files of this repository. It does not scan symlinked
  (vendor) skills or references. The output gives the linter path, its SHA-256 and the Python. Without
  `STE_LINT`, or with a path that is not a file, the check fails.

Pre-push hook (opt-in): `make hooks PY=/path/to/python` makes sure that PY runs and has pytest and ruff, records it as
`git config reservoirAgent.python`, and then sets `core.hooksPath`. For each pushed commit, the hook runs
`make check-here` in a clean worktree with that Python, or with `PY` from the environment for one push.
The hook removes `PYTHONPATH` and `PYTHONHOME` first. It puts the folder of PY first on `PATH`, so that ERT plugins find
the programs of that environment. Without an interpreter it stops the push.

User install and update on Linux (version in `VERSION`, changes in `CHANGELOG.md`):

```bash
tools/install_user_skills.sh               # link skills; remove gates that older versions installed
make update ARGS=--dry-run                 # preview new commits and changed skills
make update                                # fast-forward, link new skills, prune stale links, check
```

The site contract can use schema version 2 with profiles: see `profiles/*.example.yml` and
`site.contract.schema.json`. Select a profile with `doctor.py --profile <name>` or `RE_AGENT_PROFILE`.

Windows/portable installation (dry-run by default):

```powershell
python tools/install_project.py --target "C:\work\reservoir-agent-workspace"
python tools/install_project.py --target "C:\work\reservoir-agent-workspace" --apply
```

No internal models, credentials, contracts or work logs belong in this source repository.
There is currently no project LICENSE file; internal redistribution/rehosting requires an owner/site decision.
