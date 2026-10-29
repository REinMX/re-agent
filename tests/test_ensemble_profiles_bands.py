"""ensemble-profiles (synthetic SUMMARY) + ensemble-bands (synthetic profiles table).

Bands fail loud on absent vectors (missing is never zero). P10/P90 follow contract p10_p90.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from synth_fmu import SKILLS, make_ensemble, need, project_block, run, write_contracts, write_profiles

PROFILES = SKILLS / "ensemble-profiles/scripts/ensemble_profiles.py"
BANDS = SKILLS / "ensemble-bands/scripts/ensemble_bands.py"


def _bands_mod():
    need("numpy", "pandas", "yaml")
    spec = importlib.util.spec_from_file_location("ensemble_bands", BANDS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- ensemble-profiles
def test_profiles_extract_uses_contract(tmp_path: Path):
    need("yaml", "pandas", "resdata", "res2df")
    proj, env = write_contracts(tmp_path)
    make_ensemble(tmp_path / "scratch/toy_ens", 5)
    proc = run(PROFILES, "--contract", proj, env=env)
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "deliverables/profiles"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["eclbase"] == "eclipse/model/TOY" and manifest["iteration"] == "iter-0"
    assert manifest["n_realizations"] == 5 and manifest["expected"] == 5
    assert manifest["missing_vs_contract"] == []
    assert "WOPR:P1" in manifest["vectors_extracted"]
    assert (out / "profiles_long.csv").is_file()


def test_profiles_missing_realization_fails_unless_partial(tmp_path: Path):
    need("yaml", "pandas", "resdata", "res2df")
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, expected_realizations=6))
    make_ensemble(tmp_path / "scratch/toy_ens", 5)
    proc = run(PROFILES, "--contract", proj, "--outdir", tmp_path / "a", env=env)
    assert proc.returncode == 1
    proc = run(PROFILES, "--contract", proj, "--outdir", tmp_path / "b", "--allow-partial", env=env)
    assert proc.returncode == 1  # still flagged, but outputs written
    manifest = json.loads((tmp_path / "b/manifest.json").read_text())
    assert manifest["missing_realizations"] == [5]


def test_profiles_iter_flag(tmp_path: Path):
    need("yaml", "pandas", "resdata", "res2df")
    proj, env = write_contracts(tmp_path)
    make_ensemble(tmp_path / "scratch/toy_ens", 5, iteration="iter-1")
    proc = run(PROFILES, "--contract", proj, "--iter", "1", "--outdir", tmp_path / "o", env=env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((tmp_path / "o/manifest.json").read_text())["iteration"] == "iter-1"


# ---------------------------------------------------------------- ensemble-bands
def test_band_percentiles_conventions():
    mod = _bands_mod()
    import numpy as np

    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, np.nan])
    num = mod.band_percentiles(x, "numerical")
    exc = mod.band_percentiles(x, "exceedance")
    assert num["COUNT"] == 5 and num["P10"] < num["P90"]
    assert exc["P10"] == pytest.approx(num["P90"]) and exc["P90"] == pytest.approx(num["P10"])
    assert mod.band_percentiles(np.array([np.nan]), "numerical")["COUNT"] == 0


def _bands_lab(tmp_path: Path, **over):
    need("yaml", "numpy", "pandas")
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, **over))
    write_profiles(tmp_path / "deliverables/profiles", n=5)
    return proj, env


def test_bands_full_contract_exit_0(tmp_path: Path):
    proj, env = _bands_lab(tmp_path)
    import pandas as pd

    proc = run(BANDS, "--contract", proj, env=env)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = tmp_path / "deliverables/bands"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["p10_p90"] == "numerical"
    assert manifest["missing_vectors"] == []
    end = pd.read_csv(out / "bands_end_of_sim.csv").set_index("KEY")
    assert end.loc["FOPT", "P10"] < end.loc["FOPT", "P90"]
    assert int(end.loc["FOPT", "COUNT"]) == 5
    assert "FOPT end-of-sim numerical" in proc.stdout  # headline preview from vectors.headline


def test_bands_exceedance_swaps(tmp_path: Path):
    proj, env = _bands_lab(tmp_path, p10_p90="exceedance")
    import pandas as pd

    proc = run(BANDS, "--contract", proj, env=env)
    assert proc.returncode == 0, proc.stderr
    end = pd.read_csv(tmp_path / "deliverables/bands/bands_end_of_sim.csv").set_index("KEY")
    assert end.loc["FOPT", "P10"] > end.loc["FOPT", "P90"]


def test_bands_fail_loud_on_absent_vector(tmp_path: Path):
    proj, env = _bands_lab(tmp_path)
    out = tmp_path / "missing"
    proc = run(BANDS, "--contract", proj, "--outdir", out, "--vectors", "FOPT,FGOR", env=env)
    assert proc.returncode == 2, proc.stderr + proc.stdout
    assert "FGOR" in json.loads((out / "missing_vectors.json").read_text())["missing_vectors"]


def test_bands_required_summary_missing_fails(tmp_path: Path):
    proj_data = project_block(tmp_path)
    proj_data["vectors"]["required_summary"] = ["FOPT", "FGIT"]
    proj, env = write_contracts(tmp_path, project=proj_data)
    need("numpy", "pandas")
    write_profiles(tmp_path / "deliverables/profiles", n=5)
    proc = run(BANDS, "--contract", proj, env=env)
    assert proc.returncode == 2
