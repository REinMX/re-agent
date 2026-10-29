---
name: earned-autonomy
description: Set task limits and check evidence before more autonomy. Use for a first workflow, repeated work, skill review, or a changed integration.
version: 0.1.0
---

# Earned autonomy in VS Code

Own one result: evidence-based reservoir checks and reviewed engineering deliverables in the approved workspace.
The human keeps engineering decisions, identity, permissions, and production authority.
This skill supplies a procedure, not a scheduler, sandbox, or company authorization.

## Step 1. Define the job and today's task
Use `references/operation-templates.md` for the job contract and run receipt.
Keep the job separate from today's assignment. Define:
1. `Owns`: one result that stays meaningful for later tasks.
2. `Inputs`: approved sources, data classes, paths, and tool versions.
3. `May`: actions allowed by the site, harness, role, and write grant.
4. `Must ask before`: deletes, uploads, remote transfers, and any narrower project limits.
5. `Done when`: checks with observable results, not words such as useful or thorough.
6. `When unsure`: stop the affected step and ask before a consequential choice.

Continue independent safe steps while one step waits for the user.
Do not invent units, member sets, PVT bases, queue settings, or acceptance criteria.
For harmless presentation choices, use a stated default instead of another question.

## Step 2. Run a safe trial
Start with a frequent, reversible workflow, such as failed-realization diagnosis or public case QC.
Before a first workflow or changed integration, show the tools, commands, judgment calls, and outputs.
A requested dry run changes nothing. A plan is not an accepted engineering result.
After the user accepts the plan, finish the work. Report what did not run.
Read-only data access still needs endpoint and data-class approval.
A draft config diff is not permission to edit the model. A local simulation uses a license. Tell the user before the run.

## Step 3. Bound the run
Set success checks, runtime and cost limits before costly work. Use a narrower user limit when supplied.
Use these maximum retry limits unless the human approves a different policy:
- A transient, read-only tool failure: two retries after the first attempt.
- A malformed output: one correction, then check it again.
- A correction loop: three failed rounds, then ask with the failure evidence.
- Conflicting engineering evidence, a refused action, or an unknown outcome of a write: stop that step immediately.

Check external state before a retry of any action that can create a duplicate or spend resources.
Do not resubmit a job because a response timed out. Check its known ID without changing it.
Stop at the budget ceiling. If cost cannot be measured, say so and bound time, turns, and commands instead.
Never claim that an unavailable cost meter enforced a currency limit.

## Step 4. Check three comparable runs
1. Observe the first run. Record guesses, missing context, tool errors, and interventions.
2. Correct the failing rule. Use `continuous-learning` and test a different comparable input.
3. Test the third run without reminders about earlier corrections.

Measure acceptance, human interventions, review rounds, elapsed time, and known cost per accepted result.
A clean run passes every required check with no unresolved side effect or unplanned human correction.
Planned user confirmations do not count as failures. Record them separately.
A skipped check, staged draft, or agent self-report alone does not count as acceptance.
Use a person or a fresh reviewer context for review. Use independent calculations for material engineering claims.
Self-review is useful but does not replace these checks.

## Step 5. Keep authority per workflow
| Level | Meaning | Boundary |
|---|---|---|
| 0 | Observe and propose | Change nothing. |
| 1 | Prepare | Read approved inputs and draft within approved write roots. |
| 2 | Execute | Run the commands that the user asks for and check each result. |
| 3 | Triggered work | Optional future integration. Needs site approval and a tested trigger. |
| 4 | Coordinate | Optional specialists with approved tools, budgets, and compact handoffs. |

Default to level 1, subject to the actual harness permissions. Level 1 does not grant file writes.
The human approves each promotion. Five consecutive clean comparable runs are the minimum promotion evidence.
The three trial runs can count toward the five if they meet the same acceptance checks.
Also require passing checks, no unresolved side effects, and a tested rollback.
Test rollback on disposable files.
These counts are operating heuristics, not statistical proof of reliability.
Do not promote yourself or grant yourself more tools through a skill edit.
When quality drops, a version changes, or corrections recur, stop the affected automation and propose a lower level.

## Step 6. Review the work when the human requests it
For repeated work, offer a weekly review. Do not create a timer, scheduled job, or service by default.
Use actual receipts to count runs, accepted results, human corrections, time, costs, and repeated failures.
Report missing receipts as unknown. Do not invent a run history.
A person checks at least one artifact and decides keep, correct, pause, or retire.
Do not delete files to retire a workflow. Mark it inactive through the approved review process.

Use one agent with analyst, operator, reviewer, and maintainer roles first.
Add a specialist only for a real context, checking, or permission bottleneck.
Separate names do not separate credentials, tools, or files. Isolation needs real site-approved controls.
Pass the objective, artifacts, decisions, constraints, open questions, and next step, not complete transcripts.
At a login wall, stop and let the human authenticate through the approved interface. Never request a password in chat.

## Step 7. Keep the work log
Record each substantive task in the work log with `scripts/worklog.py`. Record facts only.
1. At the start: `python scripts/worklog.py start <task-id> --title "<short task name>"`.
2. Start each simulator command through the log:
   `python scripts/worklog.py run --kind sim --label <name> --sims <count> -- <command>`.
   Give `--sims` only when you know the number of simulator runs in the command.
3. Mark other work: `phase analysis start` and `phase analysis stop`. Mark downtime with `phase other`.
4. At the end: `python scripts/worklog.py end --status done --outputs <paths> --pr <ref>=<state>`.
5. Do not write `rate`. Only the human writes the verdict and the estimate of hours saved.
6. For a review, run `python scripts/worklog.py report` (Markdown) or `report --format csv`.
The log path is `worklog.path` in `site.contract.yml`, else `worklog/pilot.jsonl`.
The script stops if git can commit the log file. Do not copy the log into the repository.

For a bounded command on Linux, add `--max-seconds <seconds>` before `--`.
For an allocated-disk limit, add both `--max-disk-mib <MiB>` and `--disk-root <directory>` before `--`.
Each limit must be positive and finite. Without these options, the command has no budget.
The disk limit covers all allocated blocks in the directory, including existing files and directories.
One MiB is 1048576 bytes. The count uses `st_blocks * 512`, not logical file sizes.
The count includes each hard-linked inode once. It does not follow symbolic links.
If an entry disappears before its stat or directory open, the scan skips that entry.
Other entry read errors stop the command. Path components keep the same checks.
The directory can be absent. Its existing path components must be readable directories without symbolic links.
The observer completes an initial disk sample before the command starts. An initial limit breach or read error prevents the command.
Later samples have a 0.05-second wait between scans. The receipt gives the sampled maximum, not the true peak.
A disk scan can take longer than this wait. This check is not a filesystem quota or a sandbox.
The initial sample has a 5-second deadline, or the runtime limit if shorter. Observer shutdown waits at most 1 second.
The runtime limit uses a monotonic clock from observer setup through the command and its process group.
At a limit or monitor error, the script sends TERM to the owned group, then KILL after at most 0.5 seconds.
It waits at most 1 second for KILL cleanup, then at most 1 second to reap the direct child.
Cleanup time can exceed the runtime limit. The receipt includes that time and records any cleanup error.
The budget exit codes are 124 for runtime and 125 for disk or monitor failure.
For budgeted runs, INT, TERM, and HUP stop the owned group and close the receipt.
The first signal gives the exit code `128 + signal` and the receipt's `budget_outcome.signal` number.
An earlier budget stop reason stays unchanged. The receipt keeps the actual child exit code.
Later signals do not interrupt cleanup or receipt writes. The script then restores the prior signal handlers.
A normal command keeps its exit code. The `cmd_start` and `cmd` events retain the command arguments and limits.
The `cmd` event also records the stop reason, launch state, sample count, maximum allocation, and actual child exit code.
A prelaunch refusal counts as a failed command, not as simulator time or simulator runs.
The group check ignores exited zombies. It waits for live group members even if the command leader exits first.
Descendants that create another process group or session escape this control. Remote or queued jobs are not contained.
If the launcher receives KILL or the host stops, it cannot enforce a limit.

## Report and verification
State what finished, which checks passed, which checks did not run, and what waits for the user.
Use observed file or command evidence. Do not say a prompt enforced a security boundary.
Record the receipt only in its approved local root. Without a write grant, return it in the answer.
