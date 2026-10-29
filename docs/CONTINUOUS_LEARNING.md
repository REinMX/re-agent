# Continuous learning for the VS Code agent

## What this adds

This repo is still a portable skill/tool set for an approved VS Code agent, not a new framework or a Grok bot.
No Hermes installation is needed at work. Learning means improving durable instructions from explicit
corrections and checked experience. It does not train model weights or run a hidden background learner.

The loop is:

    load relevant skills and local lessons
    -> do the scoped task and check its result
    -> identify a reusable correction or procedure
    -> record its evidence, scope, and limits
    -> patch the most specific skill, or draft a distinct new one
    -> regression test and independent review
    -> human-reviewed activation and another comparable trial

The repo now separates three responsibilities:

| Skill | Responsibility |
|---|---|
| `continuous-learning` | Identify lessons, check provenance, classify scope, avoid duplicates and unsupported generalization |
| `task-to-skill` | Author new skills or patch existing skills, with regression evidence and human-reviewed activation |
| `earned-autonomy` | Define the job, done checks, uncertainty, budgets, trials, receipts, and human-approved authority |

`AGENTS.md` and `reservoir-engineer` route substantive work through these skills. The deployed extension
still has to discover and follow them. A skill is guidance, not an enforced permission system.

## Advice from the supplied post: keep, adapt, or omit

| Advice | Decision for this agent |
|---|---|
| Hire a job, not a vague task | Keep one reservoir-copilot scope. Analyst/operator/reviewer/maintainer are roles, not four new bots. |
| Owns, inputs, may, must ask, done | Keep. Add units/provenance, independent engineering checks, and an explicit uncertainty line. |
| First task frequent and reversible | Keep. Start with public failed-run diagnosis or read-only case QC, not new simulations or model tuning. |
| Text trial before execution | Keep for a first or changed workflow and when explicitly requested. Do not repeat ceremony on every established safe task. |
| Give one key, not the keyring | Adapt to approved file roots, model endpoint/data classes, tool permissions, Linux/manual topology, and queue/license access. |
| Reversibility defines authority | Adapt. Reversibility is not enough: reading confidential data and consuming licenses can still be consequential. |
| Finish the safe 90 percent | Keep. Finish independent authorized work and say what did not run. |
| Ask whenever uncertain | Adapt. Ask before engineering, security, scope, or costly choices. Use stated defaults for harmless formatting. |
| Three probation runs | Keep as an operational trial. Use distinct comparable inputs and do not manually repeat yesterday's correction. |
| Repair the rule, not just the output | Keep both. Correct today's artifact, patch the cause, add a regression, and check another input. |
| Five clean runs before promotion | Keep as a minimum human-reviewed heuristic, not statistical proof or self-granted access. |
| Weekly review and retire unused routines | Keep on demand. Review actual receipts and one artifact. Mark inactive through review instead of automatically deleting files. |
| Always-on bots and scheduled work | Omit by default. This agent lives in the approved IDE. No scheduler or trigger is added. |
| Coordinator plus several specialist bots | Omit by default. Add an independent checker when needed, then split only at a measured bottleneck. |
| Shared logins are not isolated roles | Keep. IDE panels, agent names, and Git worktrees are not credential or OS security boundaries. |
| Handoff work, not transcripts | Keep compact objective/artifact/decision/constraint/question/next-step records. |

Source of the management ideas: the text supplied by the repository owner, attributed there to @distortgeekin,
2026-09-30. That attribution and the post's product claims were not independently verified. This design
uses the management principles, not its claims about Grok capabilities.

Hermes reference for experience-based skills and progressive disclosure:
https://hermes-agent.nousresearch.com/docs/user-guide/features/skills

## Scope of learning

One verified correction can justify a rule update immediately. Three repeated tasks are useful for
finding automation candidates; they are not a requirement for remembering a clear preference or fixing a bug.

Preferences need explicit confirmation and a clear scope. Technical lessons need independent evidence,
units, assumptions, and version limits. One history match, field, well, or fluid system is not a universal
reservoir rule. Unresolved conflicts stay candidates. Silence and fluent output do not count as acceptance.

Keep these stores separate:

| Location | Contents | Authority |
|---|---|---|
| Current task and artifacts | Inputs, numerical results, temporary context | Task only |
| Approved local `worklog/lessons/` | Scoped rules with evidence and state | Only within the human-approved learning grant |
| Approved local `worklog/skill-drafts/` | Proposed skills or patches | Not in automatic discovery and not active |
| Canonical source `skills/` | Reviewed reusable instructions and references | Normal source review, tests, and approved activation |
| Site contract / harness policy | Data, tools, paths, execution and approvals | Human/site owned; never learned away |

Portable skills use generic examples and public/synthetic fixtures. Do not copy raw decks, confidential
well names, internal paths, credentials, or transcripts into source. Even a generalized lesson can disclose
sensitive operational information: check it before source promotion, model submission, or rehosting.
Gitignore only prevents ordinary tracking; it does not stop agent reads or model transfers.

## Make persistence explicit once, not a question on every task

The default is session-only learning. A standing grant can allow local logging and skill drafting within
exact roots for one project and a defined period. It can separately authorize source patches for review.
Use `skills/continuous-learning/references/write-grant.md`. Missing write fields mean no permission.
The agent checks that the grant still applies in each new session and respects revocation.

A copy-ready request, after the site/endpoint decision and with actual owned paths substituted:

> Load reservoir-engineer and continuous-learning. For this project, keep generalized, non-secret lessons
> under the approved local worklog root I name. This grant lasts for this session. Use the lesson template
> and record evidence, scope, limits, and superseded rules. You may draft skill changes under the draft root
> I name, but do not edit active skills, routing, contracts, permissions, hooks, source models, or publish.
> Correct current results first. Ask before consequential uncertainty. Keep ordinary formatting choices simple.

To grant source edits, use a separate maintainer checkout and name the exact source files and registry
entries that may change. A learning-only grant never authorizes `AGENTS.md`, hook, or policy edits.
A standing source-authoring grant can remove repeated drafting questions, but human review still decides activation.

The default installed Plan/read-only harness can block writes even when a prompt permits them. Do not
work around it with shell code or permission bypass. Use the company's approved edit/review interface,
a human editor, or a separately approved maintainer environment. If writes remain blocked, return a
copy-ready draft without claiming persistence.

## Create or improve a skill

In the approved source development checkout:

1. Read the current skill and relevant local evidence. Correct and recheck the current artifact.
2. Find the smallest reusable rule and scope. Prefer an existing skill/reference.
3. Patch only authorized files, preserving unrelated edits. New skill registration needs its own grant.
4. For a needed deterministic script, write a failing synthetic test first, then implement and execute it.
   Procedure-only skills need no empty script stub.
5. Add normal, malformed-input, and pressure-to-guess eval tasks plus a regression for the lesson.
6. Run actual checks and obtain a fresh review or human review. State every skipped or unrun check.
7. Have a person review activation. Keep commits/import/install under the site's normal process.
8. Reload the deployed extension, verify discovery, and try a different comparable input.

Development commands, using the approved development interpreter rather than upgrading shared Komodo:

```bash
make skills PY=/approved/dev/bin/python
/approved/dev/bin/python -m pytest tests/test_skill_learning.py -q
make check-full PY=/approved/dev/bin/python
```

The skill checker rejects missing registry/router entries and fewer than three distinct manual eval task
headings. This is packaging validation, not a semantic safety or engineering-correctness proof.
The six new headless cases can be selected with:

```bash
/approved/dev/bin/python evals/runner.py --skill continuous-learning --skill earned-autonomy
```

That command uses model tokens and the configured agent/judge endpoints. Review the endpoint, budget,
harness and public fixtures first. It does not certify enterprise VS Code behavior or grant persistence.
The write-path pilot needs actual approved IDE permissions, independent review, and permitted fixture data.

## Copied Windows installations need a reviewed update path

In the source checkout, both harness skill directories link to `skills/`. That means a source patch can
be visible before review. Maintain drafts in a separate checkout and do not use it for live field work.

The Windows installer creates three frozen copies: `.reservoir-agent/skills`, `.claude/skills`, and
`.agents/skills`. Editing one does not update the others or the installer manifest. This implementation
deliberately does not add automatic synchronization or overwrite changed installed snapshots.

After review, install the approved source into a fresh workspace with `tools/install_project.py`, as in
`SITE_SETUP.md`. Reload the actual deployed extension and check skill discovery. Do not copy
private lessons or grants into the portable source. Preserve them locally only through a separately
approved transfer and review. Existing workspaces are not changed by editing this repository.

## Trial, review, and promotion

Start with public/manual level-1 preparation within real harness limits. Record three comparable trial
runs. Measure acceptance, interventions, review rounds, runtime and measured costs; unknown is not zero.
For five clean runs, require all acceptance checks, no unresolved side effects, and tested disposable rollback.
A human decides promotion. Earlier trials can count if they met those checks.

Use `skills/earned-autonomy/references/operation-templates.md` for job contracts, receipts, reviews, and
compact handoffs. The agent can summarize receipts but cannot be the only judge of its work. Offer a
weekly review when asked; no cron, service, automatic promotion, or additional integration is installed.
If tools change or quality drops, stop affected automation and propose reassessment rather than repeating failures.

## Definition of done for the site pilot

This source change is complete when skill routing/references/eval packaging and local tests pass, copy
installs include the new files without weaker permissions, and conflicts preserve changed local files.
The site pilot is a separate gate:

- Approved extension, endpoint, data classes, agent/execution topology, and actual write permissions.
- A confirmed local lesson is loaded in a fresh permitted session; a revoked grant is respected.
- One skill patch and one distinct new skill follow the real draft/test/review/activation path.
- A conflicting technical claim and a permission-escalation request are not promoted.
- A delete or upload request waits for the user's confirmation.
- Three comparable public/manual runs produce reviewed receipts; five clean runs are needed for a promotion proposal.

Do not report these corporate/live-agent checks as passed until observed at the approved site.
