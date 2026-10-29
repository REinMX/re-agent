# Public SPE9 dry run

Read this at work. It is a test of the Linux harness and of Flow. It is not a field study.

The repository is private. Do not make it public. Do not paste a sensitivity study, a well name, or a real path into chat.

## Do not touch the sensitivity study

Your sensitivity data must not be deleted, moved, or overwritten.

- Do not run `rm`, `git clean`, or `rsync --delete`.
- Do not point Flow, ERT, or a copy command at the sensitivity folder.
- Make a new folder. Write only there.
- Give the assistant the exact public folder. Stop it if it names another path.

## What the pieces do

You open this repository in VS Code on Linux. Claude or Codex is the assistant.

- A skill is a procedure card. It tells the assistant which script to run. It does not start Flow by itself.
- The MCP server is a small toolbox. It can list skills and run a local preflight. It cannot start Flow.
- When you ask for a run, the assistant shows the exact command and runs it. There is no approval stop.
- `/linux-direct` tells the assistant: you are on this Linux machine. Do not use the Windows handoff.

## 0. Open the harness

Clone or pull this private repository. Open that folder in VS Code on the Linux host. Trust the workspace. Allow the `reservoir` MCP server when asked. Reload Claude Code or Codex.

In Claude, type `/linux-direct`.

Ask the assistant: "Prepare `flow SPE9.DATA` for the public folder. Do not run it yet."

It should show the exact command and the folder, and it should not start Flow.

## 1. Fill the Komodo line

Ask your team for the activate command. Do not guess it. Do not install packages into shared Komodo.

Example shape, not a real path:

```bash
source /the/command/your/team/gave/you
which flow
flow --version
which python3
```

`which flow` must print a path. If it does not, stop. Do not search the sensitivity study for a binary.

## 2. Copy public SPE9 into a new folder

SPE9 is a public OPM test deck. It is not your field.

```bash
mkdir -p "$HOME/scratch/public-spe9-dry-run"
git clone --depth 1 https://github.com/OPM/opm-tests.git "$HOME/scratch/opm-tests-public"
cp -a "$HOME/scratch/opm-tests-public/spe9/." "$HOME/scratch/public-spe9-dry-run/"
cd "$HOME/scratch/public-spe9-dry-run"
ls SPE9.DATA
```

If `opm-tests` is already on the machine, copy from that public checkout instead of cloning again. Still copy into the new folder. Do not run Flow inside the git checkout, and do not run it inside the sensitivity study.

## 3. The command to run

Read this exact command before you ask the assistant to run it. The folder must be the new public folder.

```bash
cd "$HOME/scratch/public-spe9-dry-run"
flow SPE9.DATA
```

Ask for the run only if the path is `public-spe9-dry-run` and the deck is `SPE9.DATA`. Run no other Flow or ERT command in this test.

## 4. What a clean public run looked like

This was run at home on 2026-10-06 with Flow 2026.04, not with your Komodo release. Treat it as a shape check, not as proof that Komodo matches.

- Deck: public `OPM/opm-tests` `spe9/SPE9.DATA`
- Active cells: 9000
- Simulation time: about 30 seconds on a small host
- PRT error summary: Errors 0, Bugs 0, Problems 0, Warnings 1
- The one warning is `NOECHO` / `ECHO`, which Flow 2026.04 prints as unsupported. It is not a failed run.
- `SPE9.UNSMRY` was written (80616 bytes in that run)

On your machine, open `SPE9.PRT` and find `Error summary`. You want Errors 0 and Problems 0. `SPE9.UNSMRY` must exist in the new folder.

Komodo may print a different warning count. Do not "fix" the public deck to silence `NOECHO`. Do not copy the result into the sensitivity study.

## 5. Stop

This test is finished when the public folder has a PRT with Errors 0 and Problems 0.

Do not start ERT. Do not launch an ensemble. Do not point the assistant at the sensitivity study in this session.

For a real case later, check the exact command and the exact folder before you ask for the run.
