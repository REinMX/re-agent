# Pstack agents

This repository vendors a workflow subset of pstack by Lauren Tan / poteto under MIT.
The source is the backnotprop/pstack mirror at commit
`1f0f9c3bac03c4297f6fc8f7644fa412eda57085`, from upstream cursor/plugins/pstack.
See [LICENSE](../plugins/pstack/LICENSE) and [NOTICE](../plugins/pstack/NOTICE.md)
for attribution and adaptations. No external plugin installation is needed.

The subset has 14 workflow skills, all 24 principle skills, and two agent definitions.
Excluded skills are not installed. Remaining references label them as not vendored.
Use an available equivalent. Model selection is configurable;
empty roles inherit the parent model. See [model roles](../plugins/pstack/models.example.md). Swarm uses local parallel subagents when cloud workers
are unavailable, then sequential work when parallel execution is unavailable.

## Surfaces

| Harness | Repository surfaces | Permissions |
|---|---|---|
| VS Code Copilot agent mode | `.github/copilot-instructions.md`, `.github/prompts/pstack-*.prompt.md`, `.github/agents/*.agent.md` | Copilot harness permissions |
| VS Code Agents window | Same Copilot surfaces when supported by the selected harness | Selected harness permissions; verify discovery |
| Claude Code and its VS Code extension | `.claude/skills/pstack-*`, `.claude/agents/*.md`, `CLAUDE.md` | Claude Code harness permissions |
| Codex and its VS Code extension | Generated pstack index in `AGENTS.md`, `.agents/skills/pstack-*` | Codex sandbox and approval policy from `.codex/config.toml` |
| Cloud agents | Instructions, index, and supported prompt/agent surfaces in the checkout | Platform permissions; no simulators installed |

`.claude/skills` and `.agents/skills` already link to `../skills`. The generator creates
relative directory links at `skills/pstack-*` to the canonical vendor directories.
The source export (`tools/export_source.py`) omits those links and the `plugins/pstack/`
tree; it reports them as `skipped_tracked_symlinks`. Other tracked symlinks are refused.
Names inside canonical SKILL.md files remain unchanged because they do not collide with
reservoir skills. The `pstack-` prefix namespaces discovery paths and prompts only.
Symlinks contain only a relative target, so they cannot contain a generated text header.
Generated text files carry that header after frontmatter; the generated AGENTS section has it inside its markers.
`tools/check_skills.py` validates vendor metadata and references separately from the reservoir registry.

## VS Code setup

1. Open this checkout with symlink support. Install and sign in to GitHub Copilot for Copilot sessions.
2. In Chat, select agent mode. Pick `poteto-agent` or `comment-sicko` from the custom agent picker,
   or run a `pstack-<skill>` prompt from the prompt picker. Prompt metadata uses the requested
   `mode: agent` compatibility format and also emits the current `agent: agent` field.
3. In the Agents window, open this repository and choose a supported Copilot harness.
   Check that its customization picker shows these agents before assigning work.
   Harnesses differ in prompt support; if a prompt is absent, point the session at the canonical SKILL.md.
4. For Claude Code, install its VS Code extension, open a session in this repository,
   and verify that it loads CLAUDE.md and the pstack skills and subagents.
5. For Codex, install its VS Code extension and open a session rooted in this repository.
   Read the generated AGENTS index and select the relevant pstack skill. Trust is a harness setting.

For a checkout on Windows, enable Git symlink support before cloning. A symlink placeholder is
not a working skill directory. If links are unavailable, read the canonical plugin skill paths
from AGENTS.md explicitly. This change does not alter the existing project-copy installer.

The [VS Code custom-agent guide](https://code.visualstudio.com/docs/agent-customization/custom-agents)
and [prompt guide](https://code.visualstudio.com/docs/agent-customization/prompt-files)
describe discovery and metadata. The [Agents window guide](https://code.visualstudio.com/docs/agents/run/agents-window)
describes session selection. Claude documents [subagent metadata and tools](https://code.claude.com/docs/en/sub-agents).
Official OpenAI documentation describes [skill discovery and symlink support](https://learn.chatgpt.com/docs/build-skills).

## Optional user installation

Preview before installing outside this checkout:

```sh
bash tools/install_user_skills.sh --with-pstack --dry-run
bash tools/install_user_skills.sh --with-pstack
```

The flag adds pstack links to `~/.claude/skills` and `~/.codex/skills`. Without it, the existing
reservoir-skill installation stays the default. Existing conflicting targets cause a nonzero exit;
the installer does not overwrite them. Each run also removes the approval gates that older versions
installed (`tools/remove_legacy_gates.py`). No `~/.copilot/skills`
convention is assumed. For prompts outside this repository, configure VS Code's
`chat.promptFilesLocations` to include this checkout's absolute `.github/prompts` path.
The prompts refer to this repository's source paths, so open this checkout or give the session
an explicit absolute canonical skill path when using them elsewhere.

## Regenerate and check

Edit `plugins/pstack`, then run:

```sh
make surfaces
python3 tools/build_agent_surfaces.py --check
make check
```

The generator also reads the reservoir skill catalog and AGENTS.md rules. Preserve the
`<!-- pstack:begin -->` and `<!-- pstack:end -->` markers. `--check` changes nothing and exits 1
when a generated surface is missing, changed, or stale. Generation does not call a harness,
install packages, start workers, or submit jobs. The template `plugins/pstack/templates/copilot-setup-steps.yml` is optional: copy this into .github/workflows/ via the GitHub web UI (Add file) to enable cloud agents. The Copilot setup workflow installs only
pytest and ruff, matching check.yml, and runs make check. It has no secrets or simulator installs.

## Gaps

This repository has no command hook and no Codex prefix rules. When the user asks for a run, the agent
shows the exact command and runs it. Cloud agents have no simulators installed.
Ask before deletion, Sumo registration or upload, remote access or transfers, and external disclosure
of project contents. Delegation does not grant permission.

Automated tests check packaging, metadata, drift, and installer behavior. They do
not prove extension discovery or agent compliance. No live VS Code session, cloud worker, or
simulator is started for these checks. User installation must be tried in the intended harness
before relying on discovery. Cloud setup is usable only after the workflow is available to that
platform; see GitHub's [setup documentation](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-environment).
