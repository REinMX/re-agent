---
name: principle-guard-the-context-window
version: 0.1.0
description: "Apply when context is filling up: large outputs, long files, repeated reads, fan-out planning. Route bulk to subagents; keep summaries in the main thread, not raw payloads."
---

Attribution: Lauren Tan / poteto, MIT. See [LICENSE](../../LICENSE) and [NOTICE](../../NOTICE.md).

# Guard the Context Window

**Why:** Context overflow degrades reasoning quality, creates compression artifacts, and halts progress.

**Pattern:**
- **Size phases and cap scope.** Limit files per phase, set turn budgets, account for mechanism costs.
