"""Integration test. Needs res2df and the public Reek test ensemble from fmu-ensemble."""

import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("res2df")

DATA = Path(
    os.environ.get("REEK_ENSEMBLE", Path.home() / "fmu-ensemble-upstream/tests/data/testensemble-reek001")
)
GLOB = str(DATA / "realization-*/iter-0")
SCRIPT = Path(__file__).parents[1] / "skills/res2df-resdata-extract/scripts/ensemble_summary.py"

pytestmark = pytest.mark.skipif(not DATA.is_dir(), reason="Reek test ensemble not found. Set REEK_ENSEMBLE.")

spec = importlib.util.spec_from_file_location("ensemble_summary", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_stats_manifest_and_units(tmp_path):
    code = mod.main(
        [GLOB, "--keys", "FOPT", "--time-index", "last", "--expected", "5", "--outdir", str(tmp_path)]
    )
    assert code == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["percentile_convention"] == "numerical"
    assert manifest["realizations_used"] == [0, 1, 2, 3, 4]
    assert manifest["units"]["FOPT"] == "SM3"
    assert all(len(item["sha256"]) == 64 for item in manifest["sources"])
    stats = (tmp_path / "summary_stats.csv").read_text().splitlines()
    assert stats[0] == "DATE,KEY,COUNT,MEAN,P10,P50,P90"
    row = stats[1].split(",")
    assert row[2] == "5"
    assert float(row[4]) <= float(row[5]) <= float(row[6])


def test_missing_member_fails_closed(tmp_path):
    code = mod.main(
        [GLOB, "--keys", "FOPT", "--time-index", "last", "--expected", "6", "--outdir", str(tmp_path)]
    )
    assert code == 1
    assert not (tmp_path / "summary_stats.csv").exists()


def test_allow_partial_reports_and_still_signals(tmp_path):
    code = mod.main(
        [
            GLOB,
            "--keys",
            "FOPT",
            "--time-index",
            "last",
            "--expected",
            "6",
            "--allow-partial",
            "--outdir",
            str(tmp_path),
        ]
    )
    assert code == 1
    assert json.loads((tmp_path / "manifest.json").read_text())["missing_realizations"] == [5]


def test_unknown_vector_fails_closed(tmp_path):
    assert mod.main([GLOB, "--keys", "NOSUCH", "--time-index", "last", "--outdir", str(tmp_path)]) == 1


def test_member_that_fails_a_check_is_not_called_missing(tmp_path, capsys):
    # All 5 folders exist. The old last line "missing realizations: [0, 1, 2, 3, 4]" hid the real cause.
    code = mod.main(
        [GLOB, "--keys", "NOSUCH", "--time-index", "last", "--expected", "5", "--outdir", str(tmp_path)]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "no vector matches --keys" in err
    assert "missing realizations" not in err


def test_manifest_keeps_failed_members_apart_from_missing_folders(tmp_path):
    ensemble = tmp_path / "ensemble"
    for real in (0, 1):
        source = DATA / f"realization-{real}/iter-0/eclipse/model"
        target = ensemble / f"realization-{real}/iter-0/eclipse/model"
        target.mkdir(parents=True)
        for suffix in (".DATA", ".SMSPEC", ".UNSMRY"):
            shutil.copy2(source / f"2_R001_REEK-{real}{suffix}", target)
    (ensemble / "realization-1/iter-0/eclipse/model/2_R001_REEK-1.UNSMRY").unlink()
    out = tmp_path / "out"
    code = mod.main(
        [
            str(ensemble / "realization-*/iter-0"),
            "--keys",
            "FOPT",
            "--time-index",
            "last",
            "--expected",
            "3",
            "--allow-partial",
            "--outdir",
            str(out),
        ]
    )
    manifest = json.loads((out / "manifest.json").read_text())
    assert code == 1
    assert manifest["realizations_used"] == [0]
    assert manifest["missing_realizations"] == [2]  # no folder
    assert [item["realization"] for item in manifest["issues"]] == [1]  # folder, but no UNSMRY


def test_no_runpath_is_usage_error(tmp_path):
    assert (
        mod.main(["/nope/realization-*", "--keys", "FOPT", "--time-index", "last", "--outdir", str(tmp_path)])
        == 2
    )


def test_realization_spec_skips_extra_members(tmp_path):
    code = mod.main(
        [GLOB, "--keys", "FOPT", "--time-index", "last", "--realizations", "1,3", "--outdir", str(tmp_path)]
    )
    assert code == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["realizations_used"] == [1, 3]
    assert manifest["extra_realizations_not_used"] == [0, 2, 4]


@pytest.mark.parametrize("where", ["runpath", "inside_runpath", "ancestor"])
def test_outdir_overlapping_runpath_is_usage_error(tmp_path, capsys, where):
    # Finding 4 of the 2026-10-08 review: the docstring promises "Writes only to --outdir",
    # but nothing stopped --outdir from overlapping a runpath it reads (case_qc.py has this
    # guard). Synthetic runpaths under tmp_path: a regression cannot write into the Reek data.
    runpath = tmp_path / "realization-0" / "iter-0"
    runpath.mkdir(parents=True)
    outdir = {"runpath": runpath, "inside_runpath": runpath / "out", "ancestor": tmp_path}[where]
    pattern = str(tmp_path / "realization-*/iter-0")
    assert mod.main([pattern, "--keys", "FOPT", "--time-index", "last", "--outdir", str(outdir)]) == 2
    assert "overlaps" in capsys.readouterr().err
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []
