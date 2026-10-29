# Local defaults. Override PY to use the Komodo interpreter.
PY ?= python3
# PATH with the interpreter's own bin first: ERT plugins find their programs on PATH (same rule as the
# pre-push hook). The path as given, not resolved. A bare name like python3 adds nothing.
PY_PATH = $(if $(findstring /,$(PY)),$(dir $(PY)):)$(PATH)

.PHONY: check check-here check-full check-resinsight check-drogonlite hooks test lint skills ste prose helpers evals doctor update surfaces release

# Quick check in a fresh worktree (HEAD + uncommitted edits), so files that exist only on this
# machine cannot hide a failure. Tests that need ERT, res2df, or test data skip. The skip list prints at the end.
check:
	tools/clean_check.sh check-here PY="$(PY)"

# The same checks in this directory, as it is.
check-here: lint skills helpers surfaces-check test

# Opt-in pre-push hook: runs the clean check on each pushed commit with the interpreter named here,
# recorded as git config reservoirAgent.python. PY=... at push time overrides it for that push.
# Example: make hooks PY=/path/to/.venv/bin/python   Undo: git config --unset core.hooksPath
hooks:
	@test "$(origin PY)" != file || { echo "hooks: name the interpreter: make hooks PY=/path/to/python" >&2; exit 2; }
	@py=$$(env -u PYTHONPATH -u PYTHONHOME "$(PY)" -c 'import importlib.util as u, sys; m = [n for n in ("pytest", "ruff") if u.find_spec(n) is None]; sys.exit("hooks: no " + " or ".join(m)) if m else print(sys.executable)') \
		&& test -n "$$py" -a -x "$$py" \
		|| { echo "hooks: PY=$(PY) is not a working Python with pytest and ruff" >&2; exit 2; }; \
	git config --local reservoirAgent.python "$$py" && git config --local core.hooksPath tools/hooks \
		&& echo "hooks: pre-push clean check on, with $$py"

# Full check. Use it before each commit. A skipped test is a failure.
# Example: make check-full PY=/path/to/komodo/bin/python
check-full: lint skills helpers
	PATH="$(PY_PATH)" REQUIRE_FULL=1 "$(PY)" -m pytest -q -rs

# Real-ResInsight tests (marker resinsight). Not part of check or check-full. Linux only.
# Example: make check-resinsight RI=/path/to/ri RIPY=/path/to/rips/python
check-resinsight:
	@test -n "$(RI)" -a -n "$(RIPY)" || { echo "check-resinsight: set RI=<ResInsight or launcher> RIPY=<rips python>" >&2; exit 2; }
	RUN_RESINSIGHT=1 RESINSIGHT_EXECUTABLE="$(RI)" RIPS_PYTHON="$(RIPY)" "$(PY)" -m pytest -q -rs -m resinsight

# Full drogon-lite ES-MDA graded on the synthetic truth (marker drogonlite). Not part of check or check-full.
# Example: make check-drogonlite DL=/path/to/drogon-lite DLVENV=/path/to/ert/venv [DLWORK=<folder Flow can see>]
check-drogonlite:
	@test -n "$(DL)" -a -n "$(DLVENV)" || { echo "check-drogonlite: set DL=<drogon-lite clone> DLVENV=<ERT venv>" >&2; exit 2; }
	RUN_DROGONLITE=1 DROGON_LITE="$(DL)" DROGON_LITE_VENV="$(DLVENV)" DROGON_LITE_WORK="$(DLWORK)" "$(PY)" -m pytest -q -rs -m drogonlite

lint:
	"$(PY)" -m ruff check .
	"$(PY)" -m ruff format --check .

helpers:
	"$(PY)" tools/check_shared_helpers.py

skills:
	"$(PY)" tools/check_skills.py

# Optional prose lint. No host tool is discovered by the mandatory skill check.
ste prose:
	@test -n "$(STE_LINT)" || { echo "Set STE_LINT=<path/to/ste-lint.py> for the optional prose check." >&2; exit 2; }
	"$(PY)" tools/check_skills.py --ste-lint "$(STE_LINT)"

.PHONY: surfaces-check
surfaces:
	"$(PY)" tools/build_agent_surfaces.py

surfaces-check:
	"$(PY)" tools/build_agent_surfaces.py --check

test:
	PATH="$(PY_PATH)" "$(PY)" -m pytest -q -rs

# Agent evals (headless, costs model tokens). See evals/README.md.
evals:
	"$(PY)" evals/runner.py

# Fast-forward from upstream, link new skills, prune stale links, check. Preview: make update ARGS=--dry-run
update:
	PY="$(PY)" bash tools/update.sh $(ARGS)

# Local preflight only; no activation, imports, endpoint calls or jobs.
doctor:
	"$(PY)" tools/doctor.py --mode execution --contract site.contract.yml

release:
	@test -n "$(VERSION)" || { echo "release: VERSION is required (make release VERSION=x.y.z)" >&2; exit 1; }
	"$(PY)" tools/release.py --version "$(VERSION)"
