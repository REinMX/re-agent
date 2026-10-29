---
name: task-to-skill
description: Create or change a skill from checked experience. Use for a requested skill, a verified correction, or a repeated task. Maintainer role.
version: 0.1.0
---

# Task to skill

Turn checked experience into a reusable procedure. Prefer a change to an existing skill over a duplicate.
Use `continuous-learning` for evidence and scope. Use `earned-autonomy` for acceptance and authority limits.

## Step 1. Check scope and permission
Read the approved local work log and relevant lessons, not complete transcripts.
Check the exact source checkout, target files, endpoint boundary, and human write grant.
Do not infer permission from access to files.
Without authoring permission, propose the change in the answer. Write no files.
With a standing draft grant, draft within its root without a new question for each lesson.
A source patch grant allows only its named files. New routing entries need separate maintainer approval.
A person reviews activation. Do not grant yourself authority.

## Step 2. Select the smallest change
An explicit user request or one verified correction can justify a skill change.
Three comparable logged tasks can identify an automation candidate. They do not prove reliability.
For an unsolicited candidate, show the count, common steps, judgment steps, evidence, and known time spent.
Ask which candidate to build unless a standing grant already covers that choice.
Unknown time is unknown, not an estimate presented as measured time.

Search the current skills and references. Patch the specific topic if a skill already owns it.
Create a new skill only for a distinct reusable workflow.
State the result, inputs, allowed actions, done checks, and what to do when unsure.
Keep case-specific rules in the local project lessons. Do not make them global rules.

## Step 3. Build and test the draft
Use `references/skill-template.md`. Write the smallest usable procedure, not a script stub.
1. Add the checked steps, tool versions, assumptions, failure modes, and evidence references.
2. Put deterministic steps in a script only when the task needs one. Prefer the standard library.
3. Write a failing test before script code. Use synthetic or public data.
4. Run the test and observe the intended failure. Implement the code, then run it on the fixture.
5. Add or update `evals/<name>.md` with at least three tasks: normal input, wrong input, and pressure to guess or hide a failure.
6. Add a regression task for the correction. Add matching headless cases when the harness supports them.
7. In the approved source checkout, add a new skill to `AGENTS.md` and the router table only within that grant.

A procedure-only skill does not need an empty script or a fake test of numerical output.
Do not weaken checks, change tolerances, or edit expected output merely to pass.
Replace duplicate rules. Preserve unrelated edits and mark superseded lessons.

## Step 4. Check and request review
In the approved development checkout, run `make skills` and the relevant tests.
Run `make check-full` with the approved development interpreter before a commit. Skips are failures.
Run relevant agent evals only with an approved endpoint and budget. Report evals that did not run.
A structural check does not prove technical correctness or deployed agent compliance.
Use a fresh reviewer context or a person. Keep independent engineering checks separate from self-review.
Report the files, evidence, checks, remaining judgment calls, and review decision.
A draft is not an active skill until a person reviews it.

## Step 5. Activate through the approved source
In the source checkout, `.claude/skills` and `.agents/skills` link to `skills/`.
Changes can be discovered there before review. Use a separate maintainer checkout, not a live field-work session.
In a Windows copy installation, these folders and `.reservoir-agent/skills` are separate frozen copies.
Do not edit one copy and claim that all copies changed.
After review, use the approved installer in a fresh workspace. Do not force it over changed snapshots.
Reload the actual extension, check skill discovery, and test the changed procedure with approved data.
Keep private grants, lessons, and receipts in their approved local root. Never copy them into portable source.
Use normal site review for commit, push, import, or install. This skill does not approve those actions.

## Rules
- Do not persist credentials, confidential paths, well names, or raw deck content in skills or fixtures.
- Write SKILL.md in Simplified Technical English. Keep long evidence and references outside its main body.
- Keep the rules for deletes, uploads, and remote transfers from `reservoir-engineer`.
- Stop at ambiguity or a failed retry limit. Keep the safe work and state the next human step.
