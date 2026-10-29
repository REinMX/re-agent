---
name: case-qc
description: Run all read-only QC checks on one FMU case with a single command and write one report. Use it for a full check of a case, a review before or after an ERT run, or a first look at an unfamiliar case. It runs the config, observation, failure, summary, history match, metadata, deck, and grid checks that have inputs. Analyst role.
version: 0.1.0
---

# Case QC

`scripts/case_qc.py` runs the other QC tools of this repository as separate processes.
It runs each step only when you give its inputs. It writes `report.md` and `report.json` to `--outdir`.
It does not edit source files or write in a runpath. Use a new empty report folder outside the source tree.
The script rejects a report folder that already holds files. Do not reuse an old report.

## Step 1. Collect the inputs
Ask the user only for what you cannot find. Read `site.contract.yml` first.
| Input | Where to find it |
|---|---|
| `--config` | The `.ert` file the user names. |
| `--runpaths` | `RUNPATH` with `<IENS>` and `<ITER>` replaced by `*`. For a history match, the last iteration. |
| `--prior` | The same glob with `iter-0`. Only for a history match. |
| `--expected` | `NUM_REALIZATIONS`. The script reads it from `--config` when you do not give it. |
| `--realizations` | Use it instead of `--expected` when the user started a subset. Example: `0-4,7`. |
| `--obs` | Not needed when `--config` has `OBS_CONFIG`. The script finds the file. |
| `--deck` or `--include` | The `.DATA` file, or the table include files. When `BASE.EGRID` and `BASE.INIT` are next to the `.DATA` file, the `grid` and `completions` steps also run. |
| `--metadata`, `--casepath` | The folder of exported files and the case folder. Only for a case with fmu-dataio exports. |
| `--keys` | The summary vectors the user wants. Each vector must exist in each realization. |

When INIT and UNRST are next to `--deck`, the initialization check also runs.
It uses `deck-scal-pvt-qc/scripts/swatinit_qc.py`. If subscript is absent, the report marks this step as skipped.

Without `--keys`, the script uses `FOPT FWPT FGPT`. It drops a default vector that no realization has.
The report shows each dropped vector. Example: an oil-water case has no `FGPT`.
| `--eclbase` | The base name of the deck. Use it when a runpath has more than one `.DATA` file, for example `SPE9_PLAY.DATA` and `PERMVALUES.DATA`. The summary and misfit steps use it. |

## Step 2. Run
```bash
python skills/case-qc/scripts/case_qc.py --outdir <output_root>/<name> \
  --config <case>.ert --runpaths '<glob>' --prior '<glob iter-0>' --expected <N>
```
Use the Python of the contract. Activate the environment first, so that ERT and its plugins load.
The run takes seconds to minutes. It starts no simulator and no ERT run.
Before you run the script, run the read-envelope checker on that exact command.
On Windows (Git Bash), write paths with `/` or in single quotes. The checker reads the command as bash does, and bash drops unquoted backslashes, so `D:\runs\case.ert` asks.
```bash
python3 tools/read_envelope.py --command '<exact command>'
```
Exit code 0 means run the script. Do not ask the user.
Exit code 2 means the command is outside the grant. Show the command and the checker message. Ask the user.
The grant file is `worklog/read-envelope.txt`. Copy the fields from `references/read-envelope.md`.

## Step 3. Read the report
1. Read the **Steps** table. A status is `ok`, `problems`, `failed to run`, `not applicable`, or `skipped`.
   `not applicable` means that the case has no SWATINIT. It is not a pass.
   `skipped` means that the step had no input. It is not a pass.
2. Read **Items that need attention**. A step with `problems` has errors. A history match finding is a hint.
3. For each problem, open the step folder (`<outdir>/<step>/stdout.txt`) and use the skill of that step:
   `ert-config-author-lint`, `history-match-qc`, `ert-diagnose`, `res2df-resdata-extract`, `fmu-dataio-sumo`, `deck-scal-pvt-qc`, or `grid-property-qc`.
4. Report the table, the items, and the list of skipped steps. Add what the report does not cover.

## Rules
- A fresh clone of a repository often fails `config_load` and `deck`. The reasons are files that a workflow creates.
  Say which files. Do not call the case broken.
- Do not give a verdict on the model. The report states this and you must too.
- Do not run the tool on confidential data unless the contract allows it for the model endpoint.
- To run the case again, switch to the operator role. To fix a finding, give a diff.

For a first run on a real case at your site, follow `references/runbook.md`.
