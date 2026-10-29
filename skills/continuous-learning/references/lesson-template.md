# Local lesson record

Use one file per lesson under the approved local root. Git must not track these records.
Use a real timestamp from the host. Leave an unknown value as `unknown`.

```markdown
# Lesson <local-id>
Type: preference | procedure | technical | project
Scope: <project, workflow, case, or approved global scope>
State: candidate | active | superseded | rejected
Recorded at: <host timestamp>
Source: <explicit correction, checked execution, or authoritative reference>
Evidence: <approved local artifact and check result>
Environment: <tool and version, or not applicable>
Rule: <one reusable rule>
Reason: <why this rule improves the work>
Limits: <assumptions, exceptions, and data boundary>
Target: <local lesson or proposed skill/reference>
Regression check: <different comparable input and result, or not run>
Review: <person and decision, or pending>
Supersedes: <local-id or none>
```

A candidate is not an active rule. A draft skill needs human review even if its local lesson is active.
Do not copy confidential evidence into the record. Use an approved local reference instead.
Check that the endpoint may receive the reference before the agent reads it.
