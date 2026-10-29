---
name: flow-preflight
version: 0.2.0
description: Prepare an exact OPM Flow command and run it when the user asks, read a Flow PRT log, or classify each deck keyword by Flow support for the target simulator of the contract. Use before a Flow run, before a Flow test of an ECLIPSE deck on the home box, or to inspect progress and convergence messages. Analyst role. The scripts never start Flow.
---

# Flow preflight

Use the analyst role. Read the site contract and the deck that the user names.
The scripts do not start Flow. When the user asks for a run, run the exact command from Step 2.

## Step 0. Check keyword support
Run this before a Flow test of a deck on the home box, and for any deck written for ECLIPSE:
```bash
python skills/flow-preflight/scripts/deck_support.py --deck <CASE>.DATA --contract site.contract.yml \
  --profile <home|company> --out <output_root>/<new-folder>/deck_support.json
```
`scripts/deck_support.py` parses the deck with opm.io and gives each keyword one status:
- `flow-ok`: Flow runs the keyword as written.
- `flow-partial`: an explicit item value is outside Flow's support. The report gives the occurrence, record, and item.
- `flow-unsupported`: OPM lists the keyword as unsupported. Flow stops on a critical one and ignores the others.
- `eclipse-only`: the OPM parser does not know the keyword, so Flow cannot read the deck.

The script skips defaulted items, as OPM does.
The target is `simulator.name` of the profile, or `--target`. For `FLOW` the flagged keywords decide the verdict.
For `ECLIPSE100` or `ECLIPSE300` the target verdict is PASS. The box Flow verdict then shows what a Flow test on the
home box cannot run. Add `--box-flow` to exit 1 when that verdict is FAIL. The script does not assess `IX`.
The table `references/flow_keyword_support.json` is a pinned snapshot of OPM's validation lists at opm-simulators
commit c4d038b. It says what Flow rejects, not what ECLIPSE accepts. Report the commit with the result.
To refresh the pin, run `scripts/flow_keyword_table.py` on the two source files of a new commit and change
`PINNED_COMMIT` in `deck_support.py` in the same commit.

## Step 1. Prepare the command
Run the script with the Python of the contract:
```bash
python skills/flow-preflight/scripts/flow_preflight.py --contract site.contract.yml \
  --deck <CASE>.DATA --output-dir <output_root>/<new-folder>
```
`scripts/flow_preflight.py` reads the flat contract format.
It needs literal `simulator_version`, `queue_system`, `scratch_root`, and `output_root` values.
It checks the deck and the output path. It creates no folder.
Missing values cause exit code 2. Ask for the missing values. Do not guess them.
An empty `flow_cmd` uses `flow`. An empty `flow_extra_args` adds no arguments.
Use `--parsing-strictness normal`, `high`, or `low` only when the user gives that setting.
Extra arguments must use `--option=value`. They cannot replace the output path.
The version is a contract fact. The script does not check the installed version.

## Step 2. Report the proposal
Give the exact command, output directory, planned files, and the contract version.
State that the runtime is unknown. The deck and options determine which files Flow writes.
If the user asked only to prepare the command, stop here.
If the user asked to run it, run the exact command. Do not wait for a separate approval.
When a work log task is open, start the command through `earned-autonomy/scripts/worklog.py run --kind sim --sims 1 --`.
Then read the PRT log with Step 3.

## Step 3. Read an existing log
```bash
python skills/flow-preflight/scripts/flow_log_diagnose.py --prt <CASE>.PRT \
  --outdir <output_root>/<new-report-folder>
```
`scripts/flow_log_diagnose.py` writes `report.json` and `report.md` to a new folder.
Report the observed date and report step markers, chop and Problem messages, explicit iteration counts, and errors.
Use the source line numbers. A Problem line can be a recovered timestep cut.
A line that starts with `Warning:` is not an error. When the log has an `Error summary:` block, the report gives its counts.
Then the error count is Errors plus Bugs from that block.
Report the wells that Flow shut by itself, with the report step, the time step, the date, and the day.
Report the well control mode changes, with the count for each well and transition.
These are findings, not errors. They do not change the error count.
For an optimization, a well that the simulator shuts makes the objective discontinuous.
Compare the result across realizations or batches before you trust a gradient.
The last marker does not prove that the run finished. Missing counts remain absent.
The script reads PRT text only. It does not read INFOSTEP or DBG files.

## Verification
Run `tests/test_flow_tools.py` and `tests/test_deck_support.py` in the source checkout.
Set `OPM_TESTS` to an OPM/opm-tests checkout to include the public gaslift, network, and rescoup decks.
Use `evals/flow-preflight.md` for the manual checks.
