---
bump: flow-preflight minor, reservoir-engineer patch
---
### Added
- `flow-preflight/scripts/deck_support.py` gives each deck keyword one status: `flow-ok`, `flow-partial`,
  `flow-unsupported`, or `eclipse-only`. An `eclipse-only` keyword is unknown to the OPM parser. A `flow-partial`
  keyword lists each item value that Flow rejects. Every flagged keyword has its file and line.
  The target simulator is `simulator.name` of the contract profile, or `--target`. For ECLIPSE100 and ECLIPSE300
  the Flow statuses do not fail the target verdict. They show what a Flow test on the home box cannot run, and
  `--box-flow` makes that verdict the exit code.
- `flow-preflight/references/flow_keyword_support.json` is a pinned table of the keywords and items that OPM Flow
  does not support or supports in part. It comes from opm-simulators c4d038b and has 687 unsupported keywords and
  220 item rules, with source URLs, sha256 values, and a license note. `scripts/flow_keyword_table.py` regenerates
  it from the two OPM source files.
