---
name: ert-ensemble-run
description: Start and monitor an ERT run (test_run or ensemble_experiment) in an FMU project, locally or on LSF in a Komodo environment. Use when the user asks you to run, restart, or monitor an ERT config. Operator role. Shows the exact command, then runs it.
version: 0.1.0
---

# Run an ERT ensemble

You are the operator. Show the exact command, then start the run that the user asks for.

## Step 1. Preflight
1. Read `site.contract.yml`. Use its `activate` command and its `python` path.
2. Run `which ert && ert --version`. Run `ert --help` and use the command names that it lists.
3. Read the `.ert` file and follow each `INCLUDE`. Report these values:
   `NUM_REALIZATIONS`, `QUEUE_SYSTEM`, `MAX_RUNNING`, `RUNPATH`, `ENSPATH`, and the list of `FORWARD_MODEL` lines.
4. Check that each path in `RUNPATH` and `ENSPATH` is inside `scratch_root`.
   If a path is not inside `scratch_root`, stop and tell the user.
5. Check the free space on `scratch_root` with `df -h`.
6. Check that the simulator name and version in the config match the contract.

## Step 2. Show the command
Tell the user:
- the exact command,
- the number of realizations,
- the queue system and the queue name,
- the paths of `RUNPATH` and `ENSPATH`.

Then start the run. Do not wait for a separate approval.
A `test_run` also runs the forward model. It uses a license and a queue slot. Tell the user this.
If the user asks for more than 5 realizations in a first run of a new config, say so. Suggest a smaller run first.

## Step 3. Start
Run the command from the folder of the config file. Relative paths in the config start from there.
```bash
ert ensemble_experiment <config>.ert --realizations <spec> --disable-monitoring
```
A realization spec is a list such as `0-99` or `0-4,7,9-12`.
When a work log task is open, start the command through the log (see `earned-autonomy` Step 7):
`python <skills>/earned-autonomy/scripts/worklog.py run --kind sim --label <name> -- ert ...`.
Realizations outside the spec keep their old state in storage.

## Step 3a. Keep a long run resumable
A session can stop before a long run ends.
If a run can take more than 10 minutes, start it with `scripts/long_run.py start`.
Add a line with `scripts/long_run.py note` at least one time in each 10 minutes.
When you continue in a new session, run `scripts/long_run.py check` first.
For an ES or ES-MDA update, `start` needs `--screening <screening.json>` or `--no-preflight <reason>`. It runs `ahm_preflight.py` on the exact command and working directory. It refuses an invalid weight, a bad realization selection, or a screening mismatch. `--no-preflight` records an unchecked override. That override does not validate the command.
The STATUS file has one START line with the pid and the command, then progress lines, then END.

## Step 4. Monitor
1. Do not poll in a tight loop. Check the state at most once each minute.
2. Use `ert-diagnose` with `--expected` to read the state of all realizations.
3. Tell the user when the count of `ok` realizations changes or when a realization fails.
4. If more than 20 percent of the started realizations fail with the same class, stop and tell the user.
   Do not start more realizations.

## Rules
- Do not change `RUNPATH` or `ENSPATH` in a config that the user does not own.
- Do not delete runpaths. Delete only a path that the user names, one path at a time.
- Do not raise `MAX_RUNNING` or `MAX_SUBMIT` to make a run finish.
- Do not run the same command again after a failure. Find the cause with `ert-diagnose` first.
- If `fm_dispatch.py` is not found, the environment is not active. Activate it. Do not copy files.
