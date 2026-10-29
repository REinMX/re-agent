"""Mandatory screening before ES-MDA: screen_params.py writes the record, ahm_preflight.py refuses a run without it.

The screening data is the drogon-lite one-by-one design (37 runs, 18 parameters, P10 and P90 each) on the
full-physics model, with the AHM_ANALYSIS influence table of its prior. Synthetic model, tests/data/drogon_lite.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

ROOT = Path(__file__).parents[1]
DATA = ROOT / "tests/data/drogon_lite"
SCRIPTS = ROOT / "skills/ahm-workflow/scripts"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


screen = load(SCRIPTS / "screen_params.py", "screen_params_t")
pre = load(SCRIPTS / "ahm_preflight.py", "ahm_preflight_t")


def residuals(tmp_path: Path) -> Path:
    sim = pd.read_csv(DATA / "screen_sim.csv.gz", index_col="REAL")
    obs = pd.read_csv(DATA / "screen_obs.csv", index_col="OBS")
    long = sim.reset_index().melt(id_vars="REAL", var_name="OBS", value_name="SIMULATED")
    long = long.join(obs, on="OBS").assign(ENSEMBLE="screen")
    path = tmp_path / "residuals.csv"
    long.to_csv(path, index=False)
    return path


def run_screen(tmp_path: Path, *extra: str) -> dict:
    out = tmp_path / "screening"
    args = ["--design", str(DATA / "screen_design.csv"), "--residuals", str(residuals(tmp_path))]
    args += ["--dist", str(DATA / "screen_prior.dist"), "--out", str(out), *extra]
    args += ["--observations", str(DATA / "screen_obs.csv")]
    if "--no-ahm-analysis" not in extra:
        args += ["--influence", str(DATA / "screen_influence.csv")]
    assert screen.main(args) == 0
    return json.loads((out / "screening.json").read_text())


def test_drogon_lite_screening_drops_rock_compressibility(tmp_path, capsys):
    record = run_screen(tmp_path)
    capsys.readouterr()
    assert record["schema"] == "ahm-screening/2" and record["observations"] == 306
    assert record["dropped"] == ["ROCK_CMULT"]
    assert len(record["kept"]) == 17
    rock = next(p for p in record["parameters"] if p["name"] == "ROCK_CMULT")
    assert rock["partner"] == "AQ_VOL" and 0.96 <= rock["cosine"] <= 0.98
    assert 0.6 < rock["sd_ratio_lin"] < 0.9
    assert any("AQ_VOL (cos 0.97)" in r for r in rock["reasons"])
    aq = next(p for p in record["parameters"] if p["name"] == "AQ_VOL")
    assert aq["keep"] and aq["sd_ratio_lin"] < 0.2 and aq["ks_all"] > 0.5


def test_screen_flags_an_insensitive_parameter():
    g = np.array([[30.0, 0.01], [20.0, 0.0], [10.0, 0.01]])
    params = {n: {"name": n, "distribution": "UNIFORM", "min": 0.0, "max": 1.0} for n in ("A", "B")}
    out = screen.screen(["A", "B"], [(0.1, 0.9), (0.1, 0.9)], g, params)
    assert out[0].keep and not out[1].keep
    assert out[1].reasons[0].startswith("insensitive")


def write_config(tmp_path: Path, dist: str, members: int = 100) -> Path:
    (tmp_path / "cfg").mkdir(exist_ok=True)
    (tmp_path / "cfg/p.dist").write_text(dist)
    config = tmp_path / "cfg/c.ert"
    config.write_text(f"NUM_REALIZATIONS {members}\nRANDOM_SEED 1\nGEN_KW G p.dist\n")
    return config


SCREENED = "PORO_CH UNIFORM 0.2 0.3\nAQ_VOL LOGUNIF 1e6 1e8\nROCK_CMULT CONST 1\n"


def test_preflight_ready_with_a_matching_record(tmp_path, capsys):
    record = tmp_path / "screening" / "screening.json"
    run_screen(tmp_path)
    capsys.readouterr()
    result = pre.check(
        argv=("ert", "es_mda", str(write_config(tmp_path, SCREENED)), "--weights", "4,2,1"),
        cwd=tmp_path,
        screening=record,
    )
    assert result.ready and result.sampled == ("PORO_CH", "AQ_VOL")
    assert result.argv == ("ert", "es_mda", str(tmp_path / "cfg/c.ert"), "--weights", "4,2,1")
    assert result.selected_members == tuple(range(100))


def test_preflight_refuses_without_record_dropped_unscreened_and_small(tmp_path, capsys):
    config = write_config(tmp_path, SCREENED)
    assert pre.main([str(config), "--weights", "4,2,1"]) == 1
    assert "AHM-PRE-NO-SCREENING" in capsys.readouterr().out
    run_screen(tmp_path)
    record = tmp_path / "screening" / "screening.json"
    full = write_config(
        tmp_path, SCREENED.replace("ROCK_CMULT CONST 1", "ROCK_CMULT LOGUNIF 0.5 2\nNEW UNIFORM 0 1"), 50
    )
    result = pre.check(
        argv=("ert", "es_mda", str(full), "--weights", "4,2,1"), cwd=tmp_path, screening=record
    )
    codes = {i.code for i in result.items if i.severity == "error"}
    assert codes == {"AHM-PRE-DROPPED", "AHM-PRE-UNSCREENED", "AHM-PRE-MEMBERS"}
    dropped = next(item for item in result.items if item.code == "AHM-PRE-DROPPED")
    assert "forecast uncertainty" in dropped.message
    assert "CONST" not in dropped.message
    assert result.command is None
    small = write_config(tmp_path, SCREENED, 50)
    assert pre.check(
        argv=("ert", "ensemble_smoother", str(small)),
        cwd=tmp_path,
        screening=record,
        allow_members="quick look",
    ).ready
    capsys.readouterr()


def test_preflight_needs_ahm_analysis_or_a_reason(tmp_path, capsys):
    record = run_screen(tmp_path, "--no-ahm-analysis", "prior ensemble not run yet")
    assert record["ahm_analysis"] == "not run: prior ensemble not run yet"
    assert (
        screen.main(
            ["--design", "x", "--residuals", "y", "--dist", "z", "--out", "o", "--no-ahm-analysis", " "]
        )
        == 2
    )
    path = tmp_path / "screening" / "screening.json"
    data = json.loads(path.read_text())
    data["ahm_analysis"] = ""
    path.write_text(json.dumps(data))
    result = pre.check(
        argv=("ert", "ensemble_smoother", str(write_config(tmp_path, SCREENED))), cwd=tmp_path, screening=path
    )
    assert not result.ready and any(i.code == "AHM-PRE-NO-ANALYSIS" for i in result.items)
    capsys.readouterr()


def _pair(date_found: str, date_inventory: str) -> tuple[dict, dict]:
    found = {
        "WOPR_01": {
            "OBS": "WOPR_01",
            "OBSERVED": 1.0,
            "ERROR": 1.0,
            "KEY": "WOPR:PROD",
            "DATE": date_found,
        }
    }
    inventory = {
        "WOPR_01": {
            "OBS": "WOPR_01",
            "OBSERVED": 1.0,
            "ERROR": 1.0,
            "KEY": "WOPR:PROD",
            "DATE": date_inventory,
        }
    }
    return found, inventory


def test_screening_accepts_a_midnight_suffix_on_the_same_calendar_day():
    found, inventory = _pair("2019-09-30T00:00:00", "2019-09-30")
    assert screen.inventory_problems(found, inventory) == []


def test_screening_still_refuses_a_different_calendar_day():
    found, inventory = _pair("2019-09-30T00:00:00", "2019-10-01")
    problems = screen.inventory_problems(found, inventory)
    assert problems
    assert "DATE" in problems[0]
