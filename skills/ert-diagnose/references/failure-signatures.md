# Failure classes

The triage script assigns one class to each failed realization.
The script tests the classes in the order of this table. The first match wins.
A class is a hint from a text pattern. It is not proof of the cause.

| Class | Typical text | First check | Likely action |
|---|---|---|---|
| `storage` | no space left, disk quota | `df -h` and `quota` on the scratch file system | The user frees space. Do not delete for the user. |
| `out_of_memory` | out of memory, bad_alloc, MemoryError | Memory use in `status.json` and in `logs/memory-profile-*.csv` | Reduce the model size or ask for a larger memory request. |
| `killed` | Killed, SIGKILL, signal 9 | Is there an LSF kill message? Did the run reach `MAX_RUNTIME`? | Read the LSF job record. Compare run time with `MAX_RUNTIME`. |
| `timeout` | MAX_RUNTIME, time limit | `MAX_RUNTIME` in the config and the step run time in `status.json` | Raise `MAX_RUNTIME` in a copy of the config. Give the user a diff. |
| `license` | license, FLEXlm, no feature | License server status. Count of running jobs. | Wait or lower `MAX_RUNNING`. Do not retry in a loop. |
| `file_exists` | FileExistsError, File exists | The link or file in the message. Was the run interrupted and submitted again? | The step is not idempotent (`ERTD-NOT-IDEMPOTENT`). Make the step replace its own links. Give the user a diff. |
| `missing_file` | no such file, cannot open | The path in the message. Is it relative to the runpath? | Find the earlier step that should create the file. |
| `command_not_found` | command not found, executable not found | `which <program>` in the activated environment | Activate the correct Komodo release. |
| `convergence` | convergence, time step cut | The `.PRT` file near the last report date | Look for a well event or a table change near that date. |
| `simulator_input` | `@--ERROR`, unknown keyword, Problem | The line in the `.PRT` file and the `.DATA` include that it names | Change the deck input. Give the user a diff. |
| `python_exception` | Traceback, SomethingError | The last 12 lines of the step stderr file | Read the traceback. Find the script in `ert/bin`. |
| `missing_result` | no text. `OK`, `DONE`, or `status.json` says success, but an `--expect` file is missing | The step that writes the file. Is the glob correct? | `ERTD-DONE-WITHOUT-RESULT`. Find why the step exits 0 without its output. |
| `unknown` | none of the above | All evidence files in the realization folder | Report what you saw. Do not guess. |

## Files in a realization folder (ERT 23)
| File | Content |
|---|---|
| `status.json` | Each step with `status`, `error`, `stdout`, `stderr`, start time, end time. |
| `STATUS` | One line for each step with the start time and end time. |
| `OK` | Written when all steps succeed. |
| `ERROR` | Written when a step fails. It names the step. |
| `<STEP>.stderr.<idx>` | Error output of one step. The index is the position in the forward-model list. |
| `<STEP>.stdout.<idx>` | Standard output of one step. |
| `jobs.json` | The command and the arguments of each step, after variable substitution. |
| `logs/` | Runner log and memory profile. |
| `eclipse/model/<ECLBASE>.PRT` | Simulator print file. |

File names can differ in other ERT versions. Run `ls` in one realization folder first.
If a name differs, read what is there. Do not assume.

## Simulator print files
| Line | Simulator | Meaning | Effect in the script |
|---|---|---|---|
| `@--ERROR` | Eclipse | Error | Failure evidence. The realization fails, also with an `OK` file. |
| `Error:` | OPM Flow | Error | Failure evidence. The realization fails, also with an `OK` file. |
| `@--PROBLEM` | Eclipse | Recoverable problem | Counted. Evidence only when the realization failed for another reason. |
| `Problem:` | OPM Flow | Recoverable problem, often a time step cut | Counted. Evidence only when the realization failed for another reason. |

A run can write errors to the print file and still return exit code 0.
OPM Flow writes `Problem: Solver convergence failure` and then `Timestep chopped to ...` when it cuts a time step.
The run continues. A test on SPE1 with 3 such lines finished with exit code 0.
