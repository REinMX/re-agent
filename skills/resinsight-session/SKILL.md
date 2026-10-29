---
name: resinsight-session
version: 0.1.0
description: Install ResInsight and its matching rips Python client, and check that they work together on Linux. Use it before any skill that makes ResInsight views, when ResInsight does not start, or when rips reports a wrong version. Numbers never come from ResInsight; it makes pictures and project files. Analyst role.
---

# ResInsight session

ResInsight shows results. It does not compute the numbers that a check uses.
Compute numbers with resdata or xtgeo. Use ResInsight for views and a project file that a person can open.

## Step 1. Install (one time per host)
Use one install for each host. Do not make a second copy.
```bash
skills/resinsight-session/scripts/install_resinsight.sh /abs/prefix
```
The script prints the profile block. Put it in `site.contract.yml` (git-ignored).
- `resinsight.bin`: the `ri` launcher. It adds the xcb libraries that GUI mode needs.
- `resinsight.python`: the Python that has rips. This is not the ERT Python.
- `resinsight.version`: the ResInsight release.
- `resinsight.headless`: `xvfb-run` for images on a host with no display.
On a host with no profile, `PREFIX/env.sh` sets `RESINSIGHT_EXECUTABLE` and `RIPS_PYTHON`.

## Step 2. Check
```bash
python skills/resinsight-session/scripts/ri_doctor.py --contract site.contract.yml
```
- Exit 0: the binary runs, rips imports, and the major.minor versions are equal.
- Exit 1: a version is wrong, or xvfb-run is missing. Do not continue. rips refuses a different major.minor.
- Exit 2: the binary or the rips Python is not set. Ask the user. Do not search the disk for a binary.
`tools/doctor.py` checks the contract text only. This script starts the binary one time.

## Step 3. Use a session in a script
Run the script with `resinsight.python`. Import `scripts/ri_session.py` and use `Session` as a context manager:
```python
with Session(SessionConfig(executable=bin, mode="gui")) as ri:
    case = ri.load_case(egrid)
    png = ri.snapshot(ri.ensure_view(case), out, "poro_")
```
- Use `console` mode for data. Use `gui` mode only for images. GUI mode starts its own Xvfb.
- Do not call `rips.Instance.find()` or `rips.Instance.launch()` directly. The session owns the process.
- Do not kill ResInsight by name. Other agents can use ResInsight on the same host.
- The docstring of `ri_session.py` lists each rips problem that the session prevents.
  When you find a new problem, add a fix and a test to the session, not a note to a skill.
- Smoke test: `ri_session.py --mode gui --case <EGRID> --out <folder>`.

## Step 4. Real tests
```bash
make check-resinsight RI=<resinsight.bin> RIPY=<resinsight.python>
```
These tests have the marker `resinsight`. `make check` and `make check-full` do not run them.

## Rules
- Linux only.
- The Linux builds have only the xcb Qt plugin. `offscreen` does not work. Use `xvfb-run` for images.
- Keep host paths in `site.contract.yml`. Do not write them in the repository.
- At a site, ResInsight must have the Python server. If you do not know, ask the user.
