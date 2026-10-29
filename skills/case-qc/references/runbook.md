# Runbook: first run on Drogon at your site

Goal: show that the agent and the tools work in your site environment, on the synthetic Drogon model.
Drogon is public and synthetic. Use it before you use a real field model.
Items marked ASK are facts that this repository does not know. Get them from the team. Do not guess.

## Practice first: Drogon-lite
`~/drogon-lite` is a small mock of Drogon with the same layout, hooks, and data flow. It runs with OPM Flow in minutes.
Run the steps of this runbook on it first, so that you know what a clean result looks like. See its `README.md`.
```bash
cd drogon-lite && make restore && make check && make smoother && make qc
```

## 0. Before you start
| Item | Check |
|---|---|
| Harness | In the VS Code agent window, choose Claude Code or Codex. Load the skill `reservoir-engineer`. It must find `site.contract.yml` missing and say so. |
| Environment | ASK: the Komodo release, the activate command, and the Python path. Put them in `site.contract.yml`. |
| Paths | ASK: `scratch_root` (for example `/scratch/<field>/$USER`), and a folder `output_root` where the agent may write. |
| Queue | ASK: `queue_system`, `lsf_queue`, the simulator name and version for the `FORWARD_MODEL` line. |
| Model endpoint | ASK: which data classes the endpoint accepts. Drogon is synthetic. A real model may not be allowed. |
| Drogon | Clone `equinor/fmu-drogon` with Git LFS (`git lfs install` first). Run the restore workflow of the repository. |

Run `python tools/check_skills.py` in this repository. It must say `0 failures`.

## 1. Static check, no run
```bash
python skills/case-qc/scripts/case_qc.py --outdir <output_root>/drogon_static \
  --config <fmu-drogon>/ert/model/drogon_ahm.ert --deck <fmu-drogon>/eclipse/model/DROGON_HIST.DATA
```
On a fresh clone without the restore workflow, expect these results (seen on a plain clone):
- `config_map`: ok, 107 forward models, 8 hooks, 2 warnings.
- `config_load`: problems. ERT cannot find `fmuconfig/output/global_variables.yml.tmpl` and `rate_scaling.yml.tmpl`.
- `obs_qc`: ok, 100 summary observations (the file comes from `OBS_CONFIG`).
- `deck`: problems. 17 include files from RMS are missing, and the OPM parser stops at `IMPORT`.

After a restore, the missing files should exist. Then `config_load` and the include scan should be clean.
If a file is still missing, give the user its name and stop.

## 2. Small run
Use the skill `ert-ensemble-run`. Show the exact command, then run it. Start with 1 to 5 realizations.
The Drogon workflow calls RMS and eCalc. These need licenses and a queue slot.

## 3. Check the run
```bash
python skills/case-qc/scripts/case_qc.py --outdir <output_root>/drogon_run1 \
  --config <fmu-drogon>/ert/model/drogon_ahm.ert \
  --runpaths '<scratch>/<user>/01_drogon_ahm/realization-*/iter-0' --expected <N started> \
  --keys FOPT FWPT FGPT FWIT \
  --metadata '<scratch>/<user>/01_drogon_ahm/realization-0/iter-0/share/results' \
  --casepath '<scratch>/<user>/01_drogon_ahm'
```
Expected for a clean run: `triage` ok, `summary` ok, `metadata` ok.
`metadata` uses `--require-fmu`. A missing `fmu` block means an export ran outside the case.
If a realization failed, use the skill `ert-diagnose` for the cause.

## 4. History match (optional)
After a run with an update step, give `--runpaths` for the last iteration and `--prior` for `iter-0`.
The report then has the section History match with misfit for both ensembles and the findings.
Read `skills/history-match-qc/references/interpretation.md` before you read the numbers.
With fewer than 20 realizations, statistics are weak.

## 5. What to bring back
Bring back these files so that the tools can improve. Drogon is synthetic, so you may share them:
- `report.md` and `report.json` of each run.
- The `stdout.txt` and `stderr.txt` of every step that shows `problems` or `failed to run`.
- The Komodo release, the ERT version, and the res2df version.
- Any step where the tool and ERT disagree. For example, a config that ERT rejects and the mapper accepts.
- The thresholds you changed, and why.

Do not bring back files of a real field model unless the data rules allow it.
