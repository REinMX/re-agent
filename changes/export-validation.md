### Changed

- `tools/export_source.py` builds a reservoir-only bundle. One inventory, `OMITTED_COMPONENTS`, names what
  the ZIP leaves out: the optional pstack component (`plugins/pstack/`, the `skills/pstack-*` links, the
  surface generator, its generated agent and prompt files, its tests and `docs/PSTACK_AGENTS.md`) and the
  checkout-only test module. The manifest lists each omitted path and its reason under `distribution`.
- The delivered `AGENTS.md` and `Makefile` are rewritten copies. The exporter drops
  the generated pstack block, the pstack maintainer instruction, the `surfaces` targets and their
  `check-here` dependency. The source files are unchanged. Each manifest file entry keeps `source_sha256`
  and `source_bytes` from the commit next to the delivered `sha256`, and a `transforms` list.
- Assertions about the authoring Git checkout and the real vendor tree moved to
  `tests/test_export_checkout.py`, which the bundle omits.

### Fixed

- `make check-here` runs in an extracted ZIP with no `.git`. Before, it called the omitted surface generator.
- `tests/test_no_gate_references.py` reads the hash-checked `EXPORT_MANIFEST.json` when there is no `.git`,
  and `test_default_log_is_ignored_in_this_repository` uses a fixture Git repository. Both skipped in an
  export before.
- The export fails if the pstack markers in `AGENTS.md` are malformed, if a shared Makefile rule names a
  generator target, or if a delivered instruction still names an omitted file.
- The pstack-generated agent and prompt files (`.claude/agents/`, `.github/agents/`,
  `.github/prompts/pstack-*`, `.github/copilot-instructions.md`) are declared under the `pstack` component.
  Before, the allow-list dropped them without a record.
- A checkout-only test fails when a tracked file is not delivered, not private and not in an omitted
  component. Two components declare the rest: `authoring-harness` (`.codex/config.toml`, `.mcp.json`,
  `.vscode/mcp.json`; the installer writes the target's own) and `release-update-guide` (the release-drop
  update guide). Delivered docs that name that guide are refused as stranded. The default-deny allow-list
  is unchanged. The extracted-bundle test also collects the full test suite.
- A retained installer test no longer copies the checkout's `.codex/` folder. The bundle does not contain it
  and the installer does not read it.
