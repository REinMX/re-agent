# Model roles

Copy the empty role lines to `~/.agents/pstack-models.md`, or set the keys in
`agents.models` in the site contract. Contract values take precedence. An empty
value falls back to the user file, then to the parent model. `inherit-parent`
explicitly selects the parent model. Use only names that your harness supports.
The role keys are `code`, `hardest`, `judgment`, `review`, and `fast`.

```text
code:
hardest:
judgment:
review:
fast:
```

| Role | Purpose |
|---|---|
| code | Code changes |
| hardest | Hardest tasks and architecture |
| judgment | Judgment and prose |
| review | Review and second opinion |
| fast | Fast and mechanical work |

Documented defaults are examples, not enforced model selections:

| Harness | Documented defaults |
|---|---|
| Claude Code | `claude-opus-4-1` / `claude-sonnet-4-5` |
| Codex | `gpt-5-codex` / `gpt-5` |
| Copilot in VS Code | Pick from the model picker, e.g. `Claude Sonnet 4.5`, `GPT-5` |

Copilot agent files omit `model:` when no model is set. For a local Copilot model
selection, run `python tools/build_agent_surfaces.py --contract <contract>` or
`python tools/build_agent_surfaces.py --models <role-file>`. The code agent uses
`code`; the comment reviewer uses `review`. The contract wins over the user file.
Use the same arguments with `--check` to check that local selection. Plain
generation and `make check` use portable defaults with no local role selection.
Keep local generated selections out of source commits.

For one agent, set `model:` in its source under `plugins/pstack/agents` and
regenerate. This explicit value wins over the role. `inherit-parent` omits the
field. Do not map a model identifier from one harness to another. Claude Code and
Codex use the role selection in the skills when they spawn a worker; the generator
does not copy Copilot role identifiers into Claude agent metadata.
