### Fixed
- The pre-push hook puts the folder of its Python first on `PATH` for the clean check. ERT plugins find their programs on `PATH`, so a push from a shell without the venv on `PATH` no longer fails `check_ert_config.py` with a plugin `ValidationError`. A bare interpreter name adds nothing to `PATH`.
