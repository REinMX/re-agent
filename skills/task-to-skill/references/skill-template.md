---
name: <name>
description: <What the skill does and when to use it. Keep the trigger specific.>
---

# <Title>

Use the <analyst | operator | reviewer | maintainer> role.
Owns: <one result, not a list of unrelated jobs>.
Do not <the main out-of-scope action>.

## Inputs and scope
<Approved inputs, provenance, units, bases, tool versions, and assumptions.>
<Allowed output roots. Do not guess a missing engineering value.>

## Procedure
<Checked steps, tools, and decision points.>
<Use a script for deterministic work when needed. Do not create a stub.>
<State the command, exit codes, failure behavior, and retry limit.>

## Permissions and uncertainty
<Allowed actions, and the actions that need the user first, from reservoir-engineer.>
<Stop the affected step and ask when evidence conflicts or an engineering choice is uncertain.>
<Continue independent authorized safe work. A skill never grants access or policy exceptions.>

## Verification
<Observable done checks, required coverage, independent checks, and regression input.>
<Where to find synthetic tests and at least three eval tasks. State untested cases.>

## Report
<Artifacts, file or command evidence, missing checks, uncertainties, and next step.>

## Lessons and limits
<Reusable rules, evidence, assumptions, and tool-version limits. No confidential case details.>
<Use continuous-learning to propose a scoped correction. A person reviews skill activation.>
