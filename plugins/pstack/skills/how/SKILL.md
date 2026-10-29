---
name: how
version: 0.1.0
description: "Use for \"how does X work\", code walkthroughs before changing something, and placement / ownership / layering questions (\"where should this live\", \"which package owns this\", \"is this the right layer\"). Explains subsystem architecture, runtime flow, onboarding mental models. Use why for motivation."
---

Attribution: Lauren Tan / poteto, MIT. See [LICENSE](../../LICENSE) and [NOTICE](../../NOTICE.md).

# How

Explore the codebase to answer "how does X work?" questions. Produce architectural explanations at the level of a senior engineer onboarding onto a subsystem, enough to build a working mental model, not so much that it reads like annotated source code.

Spawn a subagent with your harness (Claude Code Task/Agent, Codex subagent, Copilot custom agent). Read `agents.models` in the site contract. If a role is empty, read the same role in `~/.agents/pstack-models.md`. Read [model roles](../../models.example.md) for the keys. An empty role or `inherit-parent` uses the parent model. Use only supported parameters. If subagents are unavailable, perform the roles sequentially. Give each writer a separate worktree or output path.

## 1. Assess complexity

If the scope is ambiguous, state your interpretation and explore. The user can redirect.

- **Simple** (a single module, a small utility, a narrow question such as "how does function X work"): no explorers. Spawn one explainer that explores and explains in one pass, built from `references/explainer-prompt.md` without the explorer-findings section. Go to step 4.
- **Complex** (a subsystem spanning multiple files or services, a cross-cutting feature, a full architectural overview): go to step 2.

When in doubt, take the simple path.

Spawn a subagent with your harness (Claude Code Task/Agent, Codex subagent, Copilot custom agent). Read `agents.models` in the site contract. If a role is empty, read the same role in `~/.agents/pstack-models.md`. Read [model roles](../../models.example.md) for the keys. An empty role or `inherit-parent` uses the parent model. Use only supported parameters. If subagents are unavailable, perform the roles sequentially. Give each writer a separate worktree or output path.

## 2. Explore (complex only)

Decompose the question into 2 to 4 angles, each a distinct slice of the subsystem. Spawn all explorers in a single message, each with `references/explorer-prompt.md` and its angle filled in.

## 3. Synthesize (complex only)

Once all explorers have returned, spawn one explainer with `references/explainer-prompt.md` and every explorer's findings filled in.

## 4. Present

Present the explainer's output. Light edits for clarity or context from the conversation are fine. Do not substantially rewrite it.

## Output format

The explanation uses the sections defined in `references/explainer-prompt.md`, dropping any that do not apply: Overview, Key Concepts, How It Works, Where Things Live, Gotchas.

Use the `judgment` role for this skill. Use only models that the harness supports. Do not translate model names between harnesses.
