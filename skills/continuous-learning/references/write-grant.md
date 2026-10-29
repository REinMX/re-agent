# Scoped write grant

Use a human-approved grant. This document is a template, not an approval.
Keep the completed grant local, normally at `worklog/learning-grant.md`.
An ordinary task request does not approve security changes.

```markdown
Owner: <person who grants access>
Scope: <one project>
Expires: <end of session or explicit date>
May read: <exact roots and allowed data classes>
May write local lessons: <exact root, or none>
May write skill drafts: <exact root, or none>
May patch source skills: <exact source checkout and files, or none>
May update routing: <exact files, or none>
Review before activation: required
Approved endpoint and data classes: <site-approved boundary>
Protected files: <security policy, hooks, permissions, source models, contracts>
Budget: <runtime, command attempts, and model cost if measurable>
Approval evidence: <human decision reference>
```

The default for every write field is `none`. Missing expiry means the grant ends with this session.
A new session must check that a project grant still applies. A user can revoke it at any time.
Do not infer a grant from file access, a Git remote, or the agent's ability to write.
Use the approved harness or human editor for writes. Do not bypass read-only or Plan mode.

Drafts can remain under `worklog/skill-drafts/`. Do not add that folder to automatic skill discovery.
A patch grant can allow small source edits for review. Keep them as unapproved changes until review.
Creation of a new skill also needs approval for its registry and router entries.
Policy, permission, and autonomy changes require a separate human decision.
Commit, push, import, and installation each need the site's normal approval.
