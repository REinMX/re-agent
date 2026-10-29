"""ahm-analysis: the KS noise floor, the spurious-correlation signature, the leave-one-out table,
the overview cross-check, and the copy-only runner on synthetic inputs.

Real run: set AHM_REAL_CONFIG to the Drogon AHM config (prior iter-0, N = 16, 20 GEN_KW).
"""

from __future__ import annotations

import csv
import json
import os
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest
from synth_fmu import need

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/ahm-analysis/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ahm_influence as ai  # noqa: E402
import run_ahm_analysis as ra  # noqa: E402

N = 16
GROUPS = ["WBHP_A1", "WBHP_A2", "WOPR_A1", "WOPR_A2", "WWCT_A1", "RFT_R_A1"]
PARAMS = [f"DROGON:P{i:02d}" for i in range(20)]


def write_ahm_dir(folder: Path, tops: list[str], n: int = N) -> Path:
    """ks.csv where tops[i] is the only top group of PARAMS[i] (KS 0.625, the others 0.25)."""
    folder.mkdir(parents=True, exist_ok=True)
    columns = [*GROUPS, "All_obs", *(f"All_obs-{g}" for g in GROUPS)]
    with (folder / "ks.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Parameters", *columns])
        for param, top in zip(PARAMS, tops, strict=True):
            single = [0.625 if g == top else 0.25 for g in GROUPS]
            leave_out = [0.375 if g == top else 0.75 for g in GROUPS]
            writer.writerow([param, *single, 0.75, *leave_out])
    with (folder / "prior.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Realization", *PARAMS])
        for r in range(n):
            writer.writerow([r, *([1.0] * len(PARAMS))])
    (folder / "misfit_obs_info.csv").write_text(
        "," + ",".join(GROUPS) + "\nmisfit," + ",".join(str(10 * i) for i in range(len(GROUPS))) + "\n"
    )
    return folder


DOMINANT = ["WBHP_A2"] * 11 + ["WBHP_A1", "WOPR_A1", "WOPR_A2", "WWCT_A1", "RFT_R_A1"] + ["WOPR_A1"] * 4
BALANCED = (GROUPS * 4)[:20]


def influence(tmp_path: Path, tops: list[str], *extra: str) -> tuple[int, dict, Path]:
    ahm = write_ahm_dir(tmp_path / "ahm", tops)
    out = tmp_path / "out"
    code = ai.main(["--ahm-dir", str(ahm), "--out", str(out), *extra])
    summary = json.loads((out / "influence.json").read_text()) if (out / "influence.json").exists() else {}
    return code, summary, out


def test_floor_at_sixteen_realizations():
    assert ai.ks_critical_asymptotic(0.05, 16, 16) == pytest.approx(0.480, abs=0.002)
    assert ai.ks_critical(0.05, 16, 16) == pytest.approx(0.48, abs=0.03)
    assert ai.ks_critical(0.05, 16, 16) == 0.5  # D takes the values k/16
    assert ai.ks_critical(0.05, 100, 100) == pytest.approx(
        ai.ks_critical_asymptotic(0.05, 100, 100), abs=0.01
    )
    assert ai.ks_sf(1, 3, 3) == 1.0


def test_exact_distribution_matches_scipy():
    need("scipy", "numpy")
    import numpy as np
    from scipy.stats import ks_2samp

    rng = np.random.default_rng(1)
    for n, m in ((16, 16), (7, 9)):
        for _ in range(20):
            a, b = rng.normal(size=n), rng.normal(0.8, size=m)
            result = ks_2samp(a, b, method="exact")
            t = round(result.statistic * n * m)
            assert ai.ks_sf(t, n, m) == pytest.approx(result.pvalue, abs=1e-9)


def test_spurious_signature_fires_on_a_dominant_group(tmp_path):
    code, summary, out = influence(tmp_path, DOMINANT)
    assert code == 1
    sig = summary["signature"]
    assert sig["fired"] and [g["group"] for g in sig["groups"]] == ["WBHP_A2"]
    assert sig["groups"][0]["count"] == 11 and sig["groups"][0]["share"] == pytest.approx(0.55)
    assert summary["floor"]["ks_critical"] == 0.5 and summary["params_above_floor"] == 20
    assert "WARNING: WBHP_A2 is the top group for 11 of 20 parameters" in (out / "influence.md").read_text()
    assert "Noise floor: KS >= 0.500" in (out / "influence.md").read_text()


def test_balanced_groups_do_not_fire(tmp_path):
    code, summary, _ = influence(tmp_path, BALANCED)
    assert code == 0
    assert not summary["signature"]["fired"]
    assert max(d["share"] for d in summary["dominance"]) == pytest.approx(0.2)


def test_share_threshold_is_configurable(tmp_path):
    code, summary, _ = influence(tmp_path, DOMINANT, "--share", "0.6")
    assert code == 0 and not summary["signature"]["fired"]


def test_leave_one_out_table(tmp_path):
    _, summary, out = influence(tmp_path, DOMINANT)
    with (out / "influence.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 20 * len(GROUPS)
    row = next(r for r in rows if r["parameter"] == "DROGON:P00" and r["group"] == "WBHP_A2")
    assert float(row["ks"]) == 0.625 and row["above_floor"] == "True" and row["top"] == "True"
    assert float(row["loo_drop"]) == pytest.approx(0.75 - 0.375)
    other = next(r for r in rows if r["parameter"] == "DROGON:P00" and r["group"] == "WOPR_A1")
    assert float(other["loo_drop"]) == pytest.approx(0.0) and other["above_floor"] == "False"
    assert summary["per_param"]["DROGON:P00"]["largest_loo_drop"] == {"group": "WBHP_A2", "drop": 0.375}
    assert summary["misfit"]["WBHP_A2"] == "10"


def test_below_floor_values(tmp_path):
    ahm = write_ahm_dir(tmp_path / "ahm", BALANCED, n=4)
    out = tmp_path / "out"
    assert ai.main(["--ahm-dir", str(ahm), "--out", str(out)]) == 0
    summary = json.loads((out / "influence.json").read_text())
    assert summary["floor"]["ks_critical"] == 1.0  # four realizations cannot reach 5 %
    assert summary["params_above_floor"] == 0


@pytest.mark.parametrize(
    "case",
    ["no_ks", "n_mismatch", "not_a_number"],
)
def test_input_errors(tmp_path, case, capsys):
    ahm = write_ahm_dir(tmp_path / "ahm", BALANCED)
    args = ["--ahm-dir", str(ahm), "--out", str(tmp_path / "out")]
    if case == "no_ks":
        (ahm / "ks.csv").unlink()
    elif case == "n_mismatch":
        args += ["--n", "20"]
    else:
        text = (ahm / "ks.csv").read_text().replace("0.625", "x", 1)
        (ahm / "ks.csv").write_text(text)
    assert ai.main(args) == 2
    assert "ERROR" in capsys.readouterr().err


def test_overview_cross_check(tmp_path):
    overview = tmp_path / "overview.json"
    drivers = {p.split(":")[1]: {"driver_group": "WBHP" if i < 15 else "WOPR"} for i, p in enumerate(PARAMS)}
    overview.write_text(json.dumps({"influence": {"parameters": drivers}}))
    code, summary, out = influence(tmp_path, DOMINANT, "--overview-json", str(overview))
    cc = summary["cross_check"]
    assert code == 1 and cc["status"] == "compared" and cc["compared"] == 20
    # P00-P11 lead with WBHP (agree), P12-P15 with WOPR, WWCT, RFT (disagree), P16-P19 with WOPR (agree).
    assert cc["agree"] == 16 and cc["percent"] == 80.0
    assert "Agreement on the top family: 16 of 20" in (out / "influence.md").read_text()


def test_overview_unknown_schema_is_not_compared(tmp_path):
    overview = tmp_path / "overview.json"
    overview.write_text(json.dumps({"something": [1, 2]}))
    code, summary, _ = influence(tmp_path, BALANCED, "--overview-json", str(overview))
    assert code == 0 and summary["cross_check"]["status"] == "not compared"
    overview.write_text("not json")
    code, summary, _ = influence(tmp_path / "again", BALANCED, "--overview-json", str(overview))
    assert code == 0 and summary["cross_check"]["status"] == "not compared"


def test_family_names():
    assert ai.family("WBHP_A2") == ai.family("WBHP:A2") == "WBHP"
    assert ai.family("GEN:RFT_R_A2") == ai.family("RFT_R_A2") == "RFT"


@pytest.mark.parametrize("missing", ["ert", "semeio"])
def test_runner_fails_closed_without_module(tmp_path, monkeypatch, capsys, missing):
    # The other module is reported present, so the result does not depend on what this Python has.
    def find_spec(name, *args):
        return None if name == missing else ModuleSpec(name, None)

    monkeypatch.setattr(ra.importlib.util, "find_spec", find_spec)
    code = ra.main(["--config", str(tmp_path / "x.ert"), "--prior", "iter-0", "--out", str(tmp_path / "o")])
    assert code == 2
    assert f"ERROR: {missing} is not installed" in capsys.readouterr().err


def synthetic_case(tmp_path: Path) -> Path:
    root = tmp_path / "case"
    (root / "deck").mkdir(parents=True)
    (root / "deck/MODEL.DATA").write_text("RUNSPEC\n")
    (root / "output/storage").mkdir(parents=True)
    (root / "output/storage/index.json").write_text("{}")
    (root / "output/notes.txt").write_text("keep")
    (root / "ert").mkdir()
    (root / "ert/settings.ert").write_text(
        "ENSPATH <CONFIG_PATH>/../output/storage\nUPDATE_LOG_PATH ../output/log\n"
    )
    (root / "ert/case.ert").write_text(
        "NUM_REALIZATIONS 4\nINCLUDE settings.ert\nRUNPATH_FILE ../output/rpf\n"
    )
    return root


def test_mirror_and_rewrite_touch_only_the_copy(tmp_path):
    root = synthetic_case(tmp_path)
    before = ra.snapshot(root)
    writes = [root / "output/storage", root / "output/log", root / "output/rpf"]
    work = tmp_path / "work"
    copy = work / "case" / root.name
    ra.mirror(root, copy, (root / "ert").resolve(), [w.resolve() for w in writes])
    assert (copy / "deck").is_symlink() and (copy / "deck/MODEL.DATA").is_file()
    assert (copy / "output").is_dir() and not (copy / "output").is_symlink()
    assert not (copy / "output/storage").exists()
    assert (copy / "output/notes.txt").is_symlink()
    assert not (copy / "ert").is_symlink()

    wf = work / ra.WORKFLOW
    edited = ra.rewrite(copy / "ert/case.ert", copy / "ert", work, wf)
    assert sorted(Path(e).name for e in edited) == ["case.ert", "settings.ert"]
    settings = (copy / "ert/settings.ert").read_text().splitlines()
    assert all(line.startswith("-- run_ahm_analysis.py: ") for line in settings)
    tail = (copy / "ert/case.ert").read_text()
    assert f"ENSPATH {work / 'storage'}" in tail and f"LOAD_WORKFLOW {wf} {ra.WORKFLOW}" in tail
    assert ra.changes(before, ra.snapshot(root)) == []


def test_snapshot_detects_a_change(tmp_path):
    root = synthetic_case(tmp_path)
    before = ra.snapshot(root / "output/storage")
    (root / "output/storage/new.json").write_text("{}")
    assert "new.json" in ra.changes(before, ra.snapshot(root / "output/storage"))


def test_out_must_be_new_and_outside(tmp_path):
    root = synthetic_case(tmp_path)
    with pytest.raises(ra.InputError, match="inside"):
        ra.check_out(root / "output/storage/x", [(root / "output/storage").resolve()])
    with pytest.raises(ra.InputError, match="not empty"):
        ra.check_out(root / "deck", [])
    ra.check_out(tmp_path / "fresh", [(root / "output/storage").resolve()])


REAL = os.environ.get("AHM_REAL_CONFIG")


@pytest.mark.skipif(not REAL, reason="set AHM_REAL_CONFIG to a disposable copy of the Drogon AHM config")
def test_real_drogon_run(tmp_path):
    need("ert", "semeio")
    config = Path(REAL)
    original = ra.load_config(config)
    storage = ra.write_paths(original, config.parent)["ENSPATH"]
    before = ra.snapshot(storage)
    out = tmp_path / "ahm"
    prior = os.environ.get("AHM_REAL_PRIOR", "iter-0")
    assert (
        ra.main(["--config", str(config), "--prior", prior, "--out", str(out), "--work-dir", str(tmp_path)])
        == 0
    )
    assert ra.changes(before, ra.snapshot(storage)) == []
    receipt = json.loads((out / "run.json").read_text())
    assert receipt["original_unchanged"] and receipt["workflow"] == f"AHM_ANALYSIS ahm_target {prior}"
    with (out / "ks.csv").open() as handle:
        assert len([row for row in csv.reader(handle)][1:]) == 20

    result = tmp_path / "influence"
    assert ai.main(["--ahm-dir", str(out), "--out", str(result)]) == 1
    summary = json.loads((result / "influence.json").read_text())
    assert summary["floor"]["n"] == 16
    assert summary["signature"]["groups"][0]["group"] == "WBHP_A2"
    print(
        f"wall {receipt['wall_time_s']} s; above floor {summary['params_above_floor']}/{summary['params']}; "
        f"top groups {[(d['group'], d['count']) for d in summary['dominance'][:3]]}"
    )


def test_mirror_skips_a_storage_inside_the_config_folder(tmp_path):
    root = synthetic_case(tmp_path)
    (root / "ert/storage").mkdir()
    (root / "ert/storage/big.bin").write_text("x")
    copy = tmp_path / "work/case" / root.name
    ra.mirror(root, copy, (root / "ert").resolve(), [(root / "ert/storage").resolve()])
    assert (copy / "ert/case.ert").is_file() and not (copy / "ert/storage").exists()
