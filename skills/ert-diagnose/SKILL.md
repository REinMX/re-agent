---
name: ert-diagnose
description: Find why ERT realizations failed or did not finish. Use when the user reports failed realizations, a stuck run, missing results, or an LSF problem. Read-only. It scans the runpaths, groups the failures by cause, and gives the next check for each cause.
version: 0.2.1
---

# Diagnose failed ERT realizations

This skill reads files only. It does not start, stop, or delete anything.
To run a case again, use `ert-ensemble-run`.

## Step 1. Find the runpaths
1. Read `RUNPATH` and `NUM_REALIZATIONS` in the `.ert` file. Follow each `INCLUDE`.
2. Replace `<IENS>` and `<ITER>` with `*` to make a glob.
   Example: `/scratch/fmu/user/case/realization-*/iter-0`.
3. If the user gives only a case folder, use `--iter`.

## Step 2. Run the triage script
```bash
python skills/ert-diagnose/scripts/triage_runpath.py '<glob>' --expected <NUM_REALIZATIONS>
```
If the user started a subset, use `--realizations <spec>` instead of `--expected`. Example: `--realizations 0-4,7`.
The script checks only the realizations in the spec. It lists other folders as `extra_realizations`.
An extra folder can come from an earlier run. Do not use it as evidence.
Add `--json` when you need to parse the output.
Add `--expect '<glob>'` for each result file that a good realization must have, relative to the runpath.
Example: `--expect 'eclipse/model/*.UNSMRY'`. You can repeat the option.
The script needs only Python 3.11 or later. It writes nothing.

Exit code 0 means all realizations are ok. Exit code 1 means a problem. Exit code 2 means no runpath matched.
If the exit code is 2, stop. Ask the user for the correct path. Do not search the file system.

## Step 3. Read the summary
- `by_state`: `ok`, `failed`, or `incomplete`.
  `incomplete` means no `OK` file, no `ERROR` file, and no failed step. The run may still be active.
  Do not give a cause for an incomplete realization.
- `by_failure_class`: one class for each failed realization. See `references/failure-signatures.md`.
- `by_failed_step`: the forward-model step that failed first.
- `missing_realizations`: numbers that have no runpath. ERT may have made no folder for them.
- `findings_by_code`: the coded findings and their realizations. Report each code with its limiter.
  - `ERTD-NOT-IDEMPOTENT`: a failed realization has `FileExistsError` or `File exists` in a step log.
    A link or a file from an interrupted run is in the way. Do not submit the run again before the step is fixed.
    Propose that the step replaces its own links (for example `ln -sf`). Two steps that write the same file
    give the same error.
  - `ERTD-DONE-WITHOUT-RESULT`: `OK`, `DONE`, or `status.json` says success, but an `--expect` file is missing.
    The script reports the realization as failed with the class `missing_result`. Check the glob first.
  - `ERTD-NO-SPACE`: a step log or the `ERROR` file has "No space left on device" or "OSError: [Errno 28]".
    The file system was full when the step wrote. Free space before you submit the run again.
    The full file system can be a different one, for example `/tmp` or a home quota.
- `disk`: the free space of the file system of the first runpath (`shutil.disk_usage`).
  - `ERTD-LOW-DISK` (warning): the free space is less than `--min-free-gb` (default 5). It gives the free GB
    and the threshold. A full disk can show only as "Too few realizations are evaluated successfully" and
    "--- Logging error ---". The free space now is not the free space during the run.
  - When `free_gb` is `null`, the free space is unknown. Say so.
- `ok_with_simulator_problems`: realizations that finished, but the print file has `Problem:` lines.
  OPM Flow writes such a line when it cuts a time step and continues. These realizations did not fail.
  Many cuts can make a run slow. Report the count. Do not call it a failure.

## Step 4. Find the common cause
1. If all failures have the same class and the same step, report one cause.
2. If the classes differ, report each class with its realization numbers.
3. If the class is `unknown`, read the evidence files in the realization folder.
   Start with `<STEP>.stderr.<idx>`, then `ERROR`, then `STATUS`, then the `.PRT` file.
4. Compare `parameters.txt` of the failed realizations with the surviving ones.
   Report a difference only if it is large and you can show the values.
   A small ensemble gives weak evidence. Say so.

## Step 5. Report
Use this order:
1. One sentence with the count: failed, incomplete, ok, missing.
2. The cause for each class, with the path of the evidence file and the line.
3. What you did not check.
4. The next action for the user. Name the exact command or the exact file to change.

## Rules
- Do not state a cause as fact when you saw only a pattern in a log line. Write "the log shows".
- Do not delete runpaths, `STATUS` files, or `ERROR` files.
- Do not edit a config to make a failure disappear.
- If the user asks you to run the case again, write: "A new run needs the operator role and the skill `ert-ensemble-run`."
  Then follow that skill. It shows the exact command and runs it.
- A realization with an `OK` file can still fail the check. The script reads the `.PRT` file for `Error:` and `@--ERROR` lines.
  A `Problem:` line alone does not make a realization fail.
