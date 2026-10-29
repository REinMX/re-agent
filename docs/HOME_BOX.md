# Home box notes (Linux)

The home box is a test bench with public data only (Egg, public Drogon). Nothing here describes a work machine.
Site values stay in the git-ignored `site.contract.yml`.

## After a restart

The root filesystem is reset on restart; the work disk persists. Docker, the Flow wrapper and `make` disappear.
Run `tools/box/box_setup.sh`. It is idempotent: it installs docker if missing, starts `dockerd`, pulls the OPM image,
installs `tools/box/flow_docker.sh` as `$BOX_BIN/flow`, links `/usr/local/bin/flow` to it and puts `$BOX_BIN` on PATH.

`flow_docker.sh` uses plain `docker` when the user can reach the daemon, else passwordless `sudo -n docker`.
It never prompts. It runs Flow as the calling uid:gid, so ERT runpaths stay writable.

## FMU environment

ERT 26 is the default (`tools/box/fmu26.txt`). Keep the previous ERT 23 env for rollback.
Point `python` and `activate` in `site.contract.yml` at the env you use.

Checks after an env change (all measured 2026-10-17 on ERT 26.0.2):
- `make check PY=<env>/bin/python`
- `ert ensemble_experiment` with the semeio `PYSCAL` step, `ert ensemble_smoother` on Egg, `everest run` smoke.

ERT 26 changes that touch this repo:
- `ANALYSIS_SET_VAR STD_ENKF LOCALIZATION` is removed. Use `ANALYSIS_SET_VAR PARAMETERS <GEN_KW|FIELD|SURFACE>
  <GLOBAL|ADAPTIVE|DISTANCE>`. `check_ert_config.py --ahm` warns on the old switch.
- `ert lint` still parses without plugins and reports `COPY_FILE` and `FLOW` as missing. Use `check_ert_config.py`.
- `ert ensemble_smoother --target-ensemble` needs a `%d` in the name, for example `iter-%d`.
- `everest run` has no `--skip-prompt`; use `--disable-monitoring` for a headless run.
- Prefer `DATA_FILE <deck>` to `RUN_TEMPLATE <deck> <ECLBASE>.DATA`; ERT then reads NUM_CPU from the deck.
