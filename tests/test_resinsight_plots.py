"""resinsight-plots: binary/headless from the profile, required vectors from the contract.

ResInsight itself is replaced by a tiny fake executable (writes a PNG and logs its argv/env).
Evidence plots (matplotlib) run on synthetic bands/profiles.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from synth_fmu import (
    PY,
    SKILLS,
    make_ensemble,
    need,
    profile_block,
    project_block,
    run,
    write_contracts,
    write_profiles,
)

SCRIPTS = SKILLS / "resinsight-plots/scripts"
PLOTS = SCRIPTS / "resinsight_plots.py"
TRY = SCRIPTS / "try_resinsight_cli.py"
BANDS = SKILLS / "ensemble-bands/scripts/ensemble_bands.py"

FAKE_RI = """#!{py}
import os, sys
from pathlib import Path
args = sys.argv[1:]
snap = Path(args[args.index("--snapshotfolder") + 1])
snap.mkdir(parents=True, exist_ok=True)
(snap / "plot_1.png").write_bytes(b"\\x89PNG\\r\\n\\x1a\\nfake")
with open(os.environ["FAKE_RI_LOG"], "a") as fh:
    fh.write(" ".join(args) + " QT=" + os.environ.get("QT_QPA_PLATFORM", "") + "\\n")
"""


def _mod():
    need("yaml")
    spec = importlib.util.spec_from_file_location("try_resinsight_cli", TRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_headless_prefix_modes():
    mod = _mod()
    assert mod.headless_prefix("xvfb-run")[0][:2] == ["xvfb-run", "-a"]
    assert "offscreen" not in mod.HEADLESS_MODES  # only the xcb plugin ships on Linux
    assert mod.headless_prefix("none") == ([], {})


def test_no_binary_exits_2(tmp_path: Path):
    need("yaml")
    proj, env = write_contracts(tmp_path)
    proc = run(TRY, "--contract", proj, "--outdir", tmp_path / "ri", env=env)
    assert proc.returncode == 2
    assert "resinsight.bin" in proc.stderr


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fake ResInsight is a POSIX script")
def test_profile_binary_headless_and_iter(tmp_path: Path):
    need("yaml", "resdata")
    fake = tmp_path / "bin/ResInsight"
    fake.parent.mkdir()
    fake.write_text(FAKE_RI.format(py=PY), encoding="utf-8")
    fake.chmod(0o755)
    prof = profile_block(tmp_path, resinsight={"bin": str(fake), "headless": "none", "version": "fake"})
    proj, env = write_contracts(tmp_path, profile=prof)
    make_ensemble(tmp_path / "scratch/toy_ens", 1, iteration="iter-2")
    env["FAKE_RI_LOG"] = str(tmp_path / "ri.log")
    proc = run(TRY, "--contract", proj, "--iter", "2", "--outdir", tmp_path / "ri", env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    log = (tmp_path / "ri.log").read_text().splitlines()
    vectors = [line.split()[1] for line in log]
    assert vectors[:3] == ["FOPT", "FOPR", "WOPR:P1"]  # contract plot_required + wells.plot_default
    assert all("iter-2" in line and line.endswith("QT=") for line in log)
    pngs = sorted(p.name for p in (tmp_path / "ri").glob("*.png"))
    assert "RI_WOPR_P1_realization-0.png" in pngs


def test_offscreen_profile_is_refused(tmp_path: Path):
    """offscreen cannot work with the Linux builds; refuse it before ResInsight starts."""
    need("yaml")
    fake = tmp_path / "bin/ResInsight"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    prof = profile_block(tmp_path, resinsight={"bin": str(fake), "headless": "offscreen"})
    proj, env = write_contracts(tmp_path, profile=prof)
    proc = run(TRY, "--contract", proj, "--outdir", tmp_path / "ri", env=env)
    assert proc.returncode == 2
    assert "resinsight.headless" in proc.stderr


def _bands(tmp_path: Path, **over):
    need("yaml", "numpy", "pandas", "matplotlib")
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, **over))
    write_profiles(tmp_path / "deliverables/profiles", n=5)
    assert run(BANDS, "--contract", proj, env=env).returncode == 0
    return proj, env


def test_evidence_plots_cover_contract_vectors(tmp_path: Path):
    proj, env = _bands(tmp_path)
    proc = run(PLOTS, "--contract", proj, "--skip-resinsight", env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    plots = tmp_path / "deliverables/plots"
    manifest = json.loads((plots / "manifest.json").read_text())
    assert manifest["coverage_ok"] is True and manifest["p10_p90"] == "numerical"
    assert manifest["required_vectors"] == ["FOPT", "FOPR", "WOPR:P1"]
    for name in ("FOPT", "FOPR", "WOPR_P1"):
        png = plots / f"evidence/evidence_{name}_ensemble_bands.png"
        assert png.is_file() and png.stat().st_size > 0


def test_evidence_fails_closed_when_vector_not_banded(tmp_path: Path):
    vectors = dict(project_block(tmp_path)["vectors"])
    vectors["plot_required"] = ["FOPT", "FGIR"]
    proj, env = _bands(tmp_path, vectors=vectors)
    proc = run(PLOTS, "--contract", proj, "--skip-resinsight", env=env)
    assert proc.returncode != 0
    assert "FGIR" in proc.stderr + proc.stdout
