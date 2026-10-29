### Fixed
- `make test`, `make check` and `make check-full` put the folder of `PY` first on `PATH`, like the pre-push hook. ERT plugins find their programs on `PATH`, so `check_ert_config.py` no longer fails with a plugin `ValidationError` when the venv is not on `PATH`. A bare interpreter name adds nothing.
