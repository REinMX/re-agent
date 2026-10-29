# Agent evals

The unit tests check the scripts. The evals check the agent: does it pick the right skill, report failures
honestly, and resist pressure to guess?

| File | Content |
|---|---|
| `<skill>.md` | The tasks for a person to read. Each task has a prompt and pass items. |
| `cases.toml` | The same tasks in machine form for `runner.py`. |
| `runner.py` | Runs each case headless in a fresh copy of this repository, then grades it. |
| `make_failed_ensemble.py` | Synthetic ERT case with known failures (ground truth for ert-diagnose). |
| `results/<stamp>-<harness>/` | `summary.md` (tracked), `results.json` and `transcripts/` (git-ignored). |

## Run
Run engineering model evals on the Linux execution host, not the Windows/manual agent workspace.
Their fixture contracts contain local Linux Python/scratch/output paths. CI runs on Linux only, so native
Windows behavior of the portable installer and export is not tested in CI. The synthetic
fixture endpoint/approval fields are test inputs, not real endpoint authorization.

```bash
export EVAL_PYTHON=/path/to/komodo/python                 # interpreter with ert, res2df, pandas
export HM_CASE=/path/to/ert/output/04_history_match        # optional: history-match cases
export HM_OBS=/path/to/04_history_match.obs
python3 evals/runner.py                       # all cases, Claude Code, Sonnet
python3 evals/runner.py --skill ert-diagnose  # one skill
python3 evals/runner.py --case re-2-run-when-asked
python3 evals/runner.py --harness codex --model ""   # Codex with its default model
```
Cases whose data is missing show as skipped and fail the run by default. `--allow-skips` permits partial
coverage explicitly; an all-skipped run still fails. Costs depend on the selected models and current pricing.
Codex uses its configured model unless `--model` is supplied; the rubric judge still uses Claude Code.
Run only with approved endpoints and public/synthetic `HM_CASE`/`HM_OBS`; these variables are not a data filter.

## How grading works
1. **Checks** (deterministic). `never_tried` fails also when the harness blocked the command, because the agent
   must not try it. `tried`, `answer_matches`, and `answer_not_matches` work on the commands and the final answer.
2. **Rubric** (LLM judge). A second model reads the commands, the blocked calls, and the answer. It decides each item
   and quotes evidence. Read the evidence in `results.json` when an item fails. The judge can be wrong.

A run passes only with exit code zero and a successful terminal event. Missing completion and malformed
judge verdicts fail closed; judge answers must be literal booleans with evidence.
The fixture copy and tool permissions reduce accidental actions, but are not an OS sandbox. Arbitrary Python
can access files and spawn other programs. The summary column "Denied" counts calls the harness blocked.

## Learning and authority evals

`continuous-learning.md` and `earned-autonomy.md` each have three public text-only cases in `cases.toml`.
They test scope, unsupported technical generalization, policy changes through lessons, dry runs, promotion, and missing receipts.
They do not certify live IDE discovery, persistent writes, cost-meter enforcement, or corporate approval.
Run them only with an approved endpoint and budget. Adding a case is not evidence that it passed.
For granted skill writes, use the reviewed maintainer workflow and a disposable checkout with actual harness permissions.
Do not widen the headless eval harness to allow unrestricted self-modification.

## Add a case
Add a `[[case]]` to `cases.toml`. Use a fixture from `runner.py` or add one. Keep the answer out of the agent's
folder (for example `reference.json` of the material-balance data stays out). Run the case three times before you
trust it. Models are not deterministic.
