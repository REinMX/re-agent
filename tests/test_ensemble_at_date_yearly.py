"""ensemble-at-date + yearly-profiles on a tiny synthetic ensemble (5 reals, linear totals).

Totals are rate * days by construction, so expected values are exact. Missing SUMMARY vectors
must fail (exit 2), never zero-fill. Deck name and iteration come from the contract.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest
from synth_fmu import (
    SKILLS,
    days_to,
    make_ensemble,
    need,
    project_block,
    run,
    scale,
    write_contracts,
)

AT = SKILLS / "ensemble-at-date/scripts/ensemble_at_date.py"
YP = SKILLS / "yearly-profiles/scripts/yearly_profiles.py"
END = dt.datetime(2023, 1, 1)


@pytest.fixture(autouse=True)
def _deps():
    need("yaml", "numpy", "pandas", "resdata", "res2df")


@pytest.fixture
def lab(tmp_path: Path):
    """(contract, env, scratch) with a 5-realization ensemble under iter-0."""
    proj, env = write_contracts(tmp_path)
    scratch = tmp_path / "scratch/toy_ens"
    make_ensemble(scratch, 5)
    return proj, env, scratch


def _at(lab, *args):
    proj, env, _ = lab
    return run(AT, "--contract", proj, *args, env=env)


def _yp(lab, *args):
    proj, env, _ = lab
    return run(YP, "--contract", proj, *args, env=env)


def _mod():
    spec = importlib.util.spec_from_file_location("ensemble_at_date", AT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- pure helpers
def test_parse_reals_kinds_iter():
    mod = _mod()
    assert sorted(mod.runpath_lib.parse_realizations("0-3,7,9-10")) == [0, 1, 2, 3, 7, 9, 10]
    assert mod.kind_of("FOPT") == "total" and mod.kind_of("WWIT:INJ") == "total"
    assert mod.kind_of("FOPR") == "rate"
    assert mod.kind_of("FPR") == "state" and mod.kind_of("WBHP:P1") == "state"
    keys = ["FOPT", "WOPR:A", "WOPR:B"]
    assert mod.expand_vectors(["WOPR", "FOPT", "FGIR"], keys) == (["WOPR:A", "WOPR:B", "FOPT"], ["FGIR"])
    assert mod.iter_dirname("0") == "iter-0" and mod.iter_dirname(3) == "iter-3"
    assert mod.iter_dirname("iter-2") == "iter-2" and mod.iter_dirname("pred_ref") == "pred_ref"


def test_percentile_conventions():
    import numpy as np

    mod = _mod()
    x = np.arange(1, 101, dtype=float)
    num = mod.percentiles(x, "numerical")
    exc = mod.percentiles(x, "exceedance")
    assert num["P10"] < num["P90"]
    assert exc["P10"] == pytest.approx(num["P90"]) and exc["P90"] == pytest.approx(num["P10"])


def test_resolve_eclbase_has_no_default():
    mod = _mod()
    assert mod.contract_lib.resolve_eclbase({"eclbase": "eclipse/model/TOY"}) == ("eclipse/model", "TOY")
    assert mod.contract_lib.resolve_eclbase({"paths": {"eclbase": "TOY"}}) == ("eclipse/model", "TOY")
    assert mod.contract_lib.resolve_eclbase({"eclbase": "eclipse/model/TOY"}, "sim/X.DATA") == ("sim", "X")
    with pytest.raises(Exception, match="eclbase"):
        mod.contract_lib.resolve_eclbase({})


# ---------------------------------------------------------------- ensemble-at-date
def test_at_date_last_exact(lab, tmp_path):
    import numpy as np
    import pandas as pd

    out = tmp_path / "out"
    proc = _at(lab, "--as-of", "last", "--percentiles", "--min-n-percentiles", "5", "--outdir", out)
    assert proc.returncode == 0, proc.stderr
    summ = pd.read_csv(out / "summary.csv").set_index("KEY")
    assert set(summ.index) == {"FOPT", "FGPT"}  # vectors.at_date_default from the contract
    ref = np.array([100 * scale(r) * days_to(END) for r in range(5)])
    assert summ.loc["FOPT", "N"] == 5
    assert summ.loc["FOPT", "MEAN"] == pytest.approx(ref.mean(), rel=1e-9)
    assert summ.loc["FOPT", "P10"] == pytest.approx(np.percentile(ref, 10), rel=1e-9)
    assert summ.loc["FOPT", "P10"] < summ.loc["FOPT", "P90"]  # numerical convention
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["p10_p90"] == "numerical" and manifest["eclbase"] == "eclipse/model/TOY"
    assert not (out / "missing_vectors.json").exists()


def test_at_date_interpolates_totals(lab, tmp_path):
    import pandas as pd

    out = tmp_path / "out"
    proc = _at(lab, "--vectors", "FOPT", "--as-of", "2021-04-01", "--reals", "0-2", "--outdir", out)
    assert proc.returncode == 0, proc.stderr
    per = pd.read_csv(out / "per_real.csv")
    for real in range(3):
        expect = 100 * scale(real) * days_to(dt.datetime(2021, 4, 1))
        assert per[per.REAL == real]["VALUE"].iloc[0] == pytest.approx(expect, rel=1e-6)


def test_at_date_named_report_date_and_mid(lab, tmp_path):
    import pandas as pd

    proc = _at(lab, "--vectors", "FOPT", "--as-of", "history_end", "--outdir", tmp_path / "a")
    assert proc.returncode == 0, proc.stderr
    summ = pd.read_csv(tmp_path / "a/summary.csv")
    assert summ["AS_OF"].iloc[0].startswith("2021-01-01")
    proc = _at(lab, "--vectors", "FOPT", "--as-of", "mid", "--reals", "0-1", "--outdir", tmp_path / "b")
    assert proc.returncode == 0, proc.stderr
    summ = pd.read_csv(tmp_path / "b/summary.csv")
    assert int(summ["N"].iloc[0]) == 2 and summ["AS_OF_TOKEN"].iloc[0].startswith("mid_2021")


def test_at_date_beyond_run_end_exits_2(lab, tmp_path):
    proc = _at(lab, "--vectors", "FOPT", "--as-of", "2045-12-31", "--outdir", tmp_path)
    assert proc.returncode == 2
    assert "no extrapolation" in proc.stderr
    assert not (tmp_path / "summary.csv").exists()


def test_at_date_missing_vector_exits_2(lab, tmp_path):
    proc = _at(lab, "--vectors", "FOPT,FGIR", "--outdir", tmp_path)
    assert proc.returncode == 2
    assert json.loads((tmp_path / "missing_vectors.json").read_text())["missing_vectors"] == ["FGIR"]
    assert not (tmp_path / "summary.csv").exists()


def test_at_date_n1_skips_percentiles(lab, tmp_path):
    import pandas as pd

    proc = _at(lab, "--reals", "0", "--percentiles", "--outdir", tmp_path)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["n"] == 1 and "not computed" in manifest["percentiles_skipped"]
    assert pd.read_csv(tmp_path / "summary.csv")["P10"].isna().all()


def test_at_date_no_eclbase_fails_closed_unless_flag(tmp_path):
    proj, env = write_contracts(tmp_path, project=project_block(tmp_path, eclbase=None))
    make_ensemble(tmp_path / "scratch/toy_ens", 2)
    bad = run(AT, "--contract", proj, "--outdir", tmp_path / "a", env=env)
    assert bad.returncode == 2 and "eclbase" in bad.stderr
    ok = run(AT, "--contract", proj, "--eclbase", "eclipse/model/TOY", "--outdir", tmp_path / "b", env=env)
    assert ok.returncode == 0, ok.stderr


def test_at_date_iteration_from_contract(tmp_path):
    proj_data = project_block(tmp_path)
    proj_data["runpath"] = {"pattern": "realization-<IENS>/iter-<ITER>", "default_iter": "pred_ref"}
    proj, env = write_contracts(tmp_path, project=proj_data)
    make_ensemble(tmp_path / "scratch/toy_ens", 3, iteration="pred_ref")
    proc = run(AT, "--contract", proj, "--vectors", "FOPT", "--outdir", tmp_path / "o", env=env)
    assert proc.returncode == 0, proc.stderr
    # --iter overrides: iter-0 does not exist here
    bad = run(AT, "--contract", proj, "--iter", "0", "--vectors", "FOPT", "--outdir", tmp_path / "x", env=env)
    assert bad.returncode != 0


def test_runpath_without_ok_marker_fails_closed(tmp_path):
    proj, env = write_contracts(tmp_path)
    make_ensemble(tmp_path / "scratch/toy_ens", 2)
    (tmp_path / "scratch/toy_ens/realization-1/iter-0/OK").unlink()
    args = ("--contract", proj, "--vectors", "FOPT", "--outdir", tmp_path / "o")
    proc = run(AT, *args, env=env)
    assert proc.returncode == 1 and "no OK marker" in proc.stderr
    assert run(AT, *args, "--allow-partial", env=env).returncode == 0
    assert run(AT, *args, "--no-ok-check", env=env).returncode == 0


def test_unknown_profile_exits_2(lab, tmp_path):
    proc = _at(lab, "--profile", "nope", "--outdir", tmp_path)
    assert proc.returncode == 2 and "unknown profile" in proc.stderr


# ---------------------------------------------------------------- yearly-profiles
def test_yearly_field_sums_to_end_cumulative(lab, tmp_path):
    import pandas as pd

    proc = _yp(lab, "--outdir", tmp_path)
    assert proc.returncode == 0, proc.stderr
    per = pd.read_csv(tmp_path / "field_yearly_per_real.csv")
    assert set(per.KEY) == {"FOPT", "FGPT", "FWPT", "FWIT"}  # vectors.yearly_field_default
    assert sorted(per.YEAR.unique()) == [2020, 2021, 2022]
    for real in (0, 4):
        vol = per[(per.REAL == real) & (per.KEY == "FOPT")]["VOL"].sum()
        assert vol == pytest.approx(100 * scale(real) * days_to(END), rel=1e-6)
    y2020 = per[(per.REAL == 0) & (per.KEY == "FWIT") & (per.YEAR == 2020)]["VOL"].iloc[0]
    assert y2020 == pytest.approx(150 * 366, rel=1e-6)  # leap year at 150 sm3/d
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["gas_injection_found"] == []
    assert "FGIR" in manifest["gas_injection_absent"]
    wide = pd.read_csv(tmp_path / "field_yearly_mean_wide.csv")
    assert "FOPT_VOL" in wide.columns and len(wide) == 3


def test_yearly_explicit_missing_gas_inj_exits_2(lab, tmp_path):
    proc = _yp(lab, "--vectors", "FOPT,FGIR", "--outdir", tmp_path)
    assert proc.returncode == 2
    assert "FGIR" in json.loads((tmp_path / "missing_vectors.json").read_text())["missing_vectors"]
    assert not (tmp_path / "field_yearly_per_real.csv").exists()


def test_yearly_contract_gas_injection_key_missing_fails_closed(tmp_path):
    proj_data = project_block(tmp_path)
    proj_data["vectors"]["gas_injection"] = ["FGIT"]
    proj, env = write_contracts(tmp_path, project=proj_data)
    make_ensemble(tmp_path / "scratch/toy_ens", 2)
    proc = run(YP, "--contract", proj, "--outdir", tmp_path / "o", env=env)
    assert proc.returncode == 2
    assert "FGIT" in json.loads((tmp_path / "o/missing_vectors.json").read_text())["missing_vectors"]


def test_yearly_gas_injection_found(tmp_path):
    proj, env = write_contracts(tmp_path)
    make_ensemble(tmp_path / "scratch/toy_ens", 2, with_gas_inj=True)
    proc = run(YP, "--contract", proj, "--outdir", tmp_path / "o", env=env)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((tmp_path / "o/manifest.json").read_text())
    assert set(manifest["gas_injection_found"]) >= {"FGIR", "FGIT"}


def test_yearly_by_well_uses_summary_totals(lab, tmp_path):
    import pandas as pd

    proc = _yp(lab, "--by-well", "--wells", "P1,P2", "--outdir", tmp_path)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["well_source"] == "summary_total"
    well = pd.read_csv(tmp_path / "well_yearly_per_real.csv")
    p1 = well[(well.REAL == 0) & (well.KEY == "WOPT:P1")]["VOL"].sum()
    assert p1 == pytest.approx(60 * days_to(END), rel=1e-6)


def test_by_well_refuses_without_totals(tmp_path):
    proj, env = write_contracts(tmp_path)
    make_ensemble(tmp_path / "scratch/toy_ens", 2, drop=("WOPT:P1", "WOPT:P2", "WWPT:P1", "WWIT:I1"))
    proc = run(YP, "--contract", proj, "--by-well", "--outdir", tmp_path / "o", env=env)
    assert proc.returncode == 2
    payload = json.loads((tmp_path / "o/missing_vectors.json").read_text())
    assert "WOPT" in payload["missing_vectors"]
    assert "WOPR" in payload["available_well_rates"]
    proc = run(
        YP, "--contract", proj, "--by-well", "--well-vectors", "WOPR", "--outdir", tmp_path / "p", env=env
    )
    assert proc.returncode == 2 and "--derive-from-rates" in proc.stderr


def test_by_well_derived_matches_summary(tmp_path):
    import pandas as pd

    proj, env = write_contracts(tmp_path)
    make_ensemble(tmp_path / "scratch/toy_ens", 2, drop=("WOPT:P1", "WOPT:P2", "WWPT:P1", "WWIT:I1"))
    proc = run(
        YP,
        "--contract",
        proj,
        "--by-well",
        "--wells",
        "P1,P2",
        "--derive-from-rates",
        "--well-vectors",
        "WOPR,WBHP",
        "--outdir",
        tmp_path / "o",
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    well = pd.read_csv(tmp_path / "o/well_yearly_per_real.csv")
    oil = well[well.KEY.str.startswith("WOPR")]
    assert (oil.SOURCE == "derived_from_rate").all()
    p1 = oil[(oil.REAL == 1) & (oil.KEY == "WOPR:P1")]["VOL"].sum()
    assert p1 == pytest.approx(60 * scale(1) * days_to(END), rel=1e-6)
    bhp = well[well.KEY == "WBHP:P1"]
    assert bhp.END_VALUE.notna().all() and bhp.VOL.isna().all()


def test_by_well_unknown_well_exits_2(lab, tmp_path):
    proc = _yp(lab, "--by-well", "--wells", "P1,FOO", "--outdir", tmp_path)
    assert proc.returncode == 2
    assert "well:FOO" in json.loads((tmp_path / "missing_vectors.json").read_text())["missing_vectors"]
