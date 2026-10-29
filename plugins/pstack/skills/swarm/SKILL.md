---
name: swarm
version: 0.1.0
description: "Fan out N parallel workers, drain them, and return one report. Use for swarm skill, 'swarm this', or parallel coverage, races, gauntlets, and exploration."
---

Attribution: Lauren Tan / poteto, MIT. See [LICENSE](../../LICENSE) and [NOTICE](../../NOTICE.md).

# Swarm

Fan out N parallel workers. They may cover separate slices, race the same brief, or mix both. The parent waits, aggregates, and returns one report.

## Start

Open a todolist with one entry per phase before launching anything.

1. Frame
2. Fan out
3. Aggregate
4. Report

## Phase A: Frame

1. State the done predicate and the artifact or report the swarm must return.
2. Choose separate slices, a race on the same brief, or both. For a race, declare first pass, rank all, or best-of before work.
3. Set the total worker count from the user's scope and the available capacity.
4. Read `agents.models` in the site contract. If a role is empty, read the same role in `~/.agents/pstack-models.md`. Read [model roles](../../models.example.md) for the keys. An empty role or `inherit-parent` uses the parent model. Name any configured model race before work.
5. Give each writer a separate worktree or output path. Verification briefs name exact commit SHAs. Measurement briefs also name the method, sample count, and order. Workers record these in their results.

## Phase B: Fan out

Spawn a subagent with your harness (Claude Code Task/Agent, Codex subagent, Copilot custom agent).
Use only supported parameters and respect its concurrency limit.
When cloud workers are unavailable, fall back to local parallel subagents.
If local parallel subagents are unavailable, execute sequentially.
Do not push or transfer source to obtain cloud workers without authorization.

Every brief stands alone. Include the goal, scope, exact slice or race arm, how to verify, and what to report. Reports use `PASS`, `ISSUES`, or `BLOCKED` with evidence. A worker that can prove a defect reports `ISSUES` and lists every issue it can prove, not only the first.

If a worker drops out, proceed with N-1 and note it.

## Phase C: Aggregate

Read the terminal results. Drop a result that does not record the SHAs and method its brief names, and respawn that worker once. After a second miss, record a gap. A gap does not count as a pass. For coverage, every required slice needs a result. For a race, apply the selection rule declared up front. Use first pass, rank all, or best-of. Do not paste raw worker dumps.

Keep a compact result table, one-line evidenced issues, and explicit gaps or dropouts.

## Phase D: Report

Return one consolidated in-chat report with the table, issue one-liners, gaps or dropouts, and the race rule when used.

Use the `fast` role for this skill. Use only models that the harness supports. Do not translate model names between harnesses.
