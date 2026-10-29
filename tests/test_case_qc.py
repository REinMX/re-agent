import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("res2df")
pytest.importorskip("ert")

SCRIPT = Path(__file__).parents[1] / "skills/case-qc/scripts/case_qc.py"
spec = importlib.util.spec_from_file_location("case_qc", SCRIPT)
cq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cq)

REEK = Path(
    os.environ.get("REEK_ENSEMBLE", Path.home() / "fmu-ensemble-upstream/tests/data/testensemble-reek001")
)
needs_reek = pytest.mark.skipif(not REEK.is_dir(), reason="Reek test ensemble not found. Set REEK_ENSEMBLE.")
GLOB = str(REEK / "realization-*/iter-0")


def reek_obs(tmp_path: Path) -> Path:
    import pandas as pd
    from res2df import ResdataFiles, summary

    data = sorted((REEK / "realization-0/iter-0/eclipse/model").glob("*.DATA"))[0]
    frame = summary.df(ResdataFiles(str(data)), time_index="yearly", column_keys=["FOPT"])
    lines = [
        f"SUMMARY_OBSERVATION O{i} {{ VALUE = {frame['FOPT'].iloc[i]}; ERROR = 1000; "
        f"DATE = {pd.Timestamp(frame.index[i]).date()}; KEY = FOPT; }};"
        for i in (1, 2, 3)
    ]
    path = tmp_path / "reek.obs"
    path.write_text("\n".join(lines))
    return path


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def run(tmp_path: Path, *args: str) -> tuple[int, dict, str]:
    out = tmp_path / "out"
    code = cq.main(["--outdir", str(out), *args])
    return code, json.loads((out / "report.json").read_text()), (out / "report.md").read_text()


def test_usage_errors(tmp_path):
    assert cq.main(["--outdir", str(tmp_path / "o")]) == 2
    assert cq.main(["--outdir", str(tmp_path / "o"), "--config", str(tmp_path / "none.ert")]) == 2
    deck = tmp_path / "a.DATA"
    deck.write_text("")
    assert cq.main(["--outdir", str(tmp_path / "o"), "--deck", str(deck), "--include", str(deck)]) == 2


def test_only_obs_runs_one_step_and_skips_the_rest(tmp_path):
    obs = tmp_path / "x.obs"
    obs.write_text("SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 1; DATE = 2020-01-01; KEY = K; };")
    code, data, report = run(tmp_path, "--obs", str(obs))
    assert code == 0
    assert data["steps"]["obs_qc"]["status"] == "ok"
    assert set(data["skipped"]) == {
        "config_map",
        "config_load",
        "triage",
        "summary",
        "misfit",
        "compensation",
        "metadata",
        "deck",
        "grid",
        "completions",
        "swatinit",
    }
    assert "No step found a problem" in report


def test_broken_observation_is_a_problem(tmp_path):
    obs = tmp_path / "x.obs"
    obs.write_text("SUMMARY_OBSERVATION A { VALUE = 1; ERROR = 0; DATE = 2020-01-01; KEY = K; };")
    code, data, report = run(tmp_path, "--obs", str(obs))
    assert code == 1 and data["steps"]["obs_qc"]["status"] == "problems"
    assert "obs_qc: found problems" in report


@needs_reek
def test_full_run_on_reek_with_config(tmp_path, valid_reek_ensemble):
    obs = reek_obs(tmp_path)
    config = tmp_path / "case.ert"
    config.write_text(f"NUM_REALIZATIONS 5\nECLBASE X\nSUMMARY *\nFORWARD_MODEL FLOW\nOBS_CONFIG {obs}\n")
    before = tree_hash(REEK)
    valid_before = tree_hash(valid_reek_ensemble)
    code, data, report = run(
        tmp_path,
        "--config",
        str(config),
        "--runpaths",
        str(valid_reek_ensemble / "realization-*/iter-0"),
        "--keys",
        "FOPT",
        "FWPT",
    )
    assert code == 0, report
    assert {name: s["status"] for name, s in data["steps"].items()} == {
        "config_map": "ok",
        "config_load": "ok",
        "obs_qc": "ok",
        "triage": "ok",
        "summary": "ok",
        "misfit": "ok",
    }
    assert any("--expected is 5" in n for n in data["notes"])  # from NUM_REALIZATIONS
    assert any("OBS_CONFIG" in n for n in data["notes"])  # observation file found through the config
    assert "numerical percentiles" in report and "not the ERT objective" in report
    assert (tmp_path / "out/summary/files/summary_stats.csv").exists()
    assert tree_hash(REEK) == before
    assert tree_hash(valid_reek_ensemble) == valid_before


@needs_reek
def test_missing_member_is_reported_and_exit_is_one(tmp_path):
    obs = reek_obs(tmp_path)
    code, data, report = run(
        tmp_path, "--runpaths", GLOB, "--obs", str(obs), "--expected", "6", "--keys", "FOPT"
    )
    assert code == 1
    assert data["steps"]["triage"]["status"] == "problems"
    assert data["steps"]["summary"]["status"] == "problems"
    assert data["steps"]["misfit"]["status"] == "problems"
    assert "missing" in report


@needs_reek
def test_findings_are_listed_as_hints_not_errors(tmp_path, valid_reek_ensemble):
    obs = reek_obs(tmp_path)
    # an observation far from every realization gives an "out of reach" finding, but no failed step
    far = tmp_path / "far.obs"
    far.write_text(
        obs.read_text()
        + "\nSUMMARY_OBSERVATION FAR { VALUE = 1; ERROR = 1; DATE = 2002-01-01; KEY = FOPT; };"
    )
    code, data, report = run(
        tmp_path,
        "--runpaths",
        str(valid_reek_ensemble / "realization-*/iter-0"),
        "--obs",
        str(far),
        "--expected",
        "5",
        "--keys",
        "FOPT",
    )
    assert code == 0
    assert data["findings"] and "hints, not errors" in report


@needs_reek
def test_default_key_that_the_case_does_not_have_is_dropped(tmp_path, monkeypatch):
    # an oil-water case has no FGPT. The default keys must not fail the summary step
    monkeypatch.setattr(cq, "DEFAULT_KEYS", ["FOPT", "FWPT", "NOSUCH"])
    code, data, report = run(tmp_path, "--runpaths", GLOB, "--expected", "5")
    assert code == 0, report
    assert data["steps"]["summary"]["status"] == "ok"
    assert data["default_keys_dropped"] == ["NOSUCH"]
    assert "default vectors that no realization has, not reported: ['NOSUCH']" in report


@needs_reek
def test_explicit_key_that_the_case_does_not_have_is_a_problem(tmp_path):
    code, data, _ = run(tmp_path, "--runpaths", GLOB, "--expected", "5", "--keys", "FOPT", "NOSUCH")
    assert code == 1
    assert data["steps"]["summary"]["status"] == "problems"
    assert data["default_keys_dropped"] == []


@needs_reek
@pytest.mark.parametrize(("eclbase", "status"), [("2_R001_REEK-*", "ok"), ("NOSUCH", "problems")])
def test_eclbase_reaches_summary_and_misfit(tmp_path, valid_reek_ensemble, eclbase, status):
    # A runpath with two .DATA files (SPE9_PLAY and PERMVALUES) needs --eclbase in both steps.
    obs = reek_obs(tmp_path)
    code, data, report = run(
        tmp_path,
        "--runpaths",
        str(valid_reek_ensemble / "realization-*/iter-0"),
        "--obs",
        str(obs),
        "--expected",
        "5",
        "--eclbase",
        eclbase,
    )
    for step in ("summary", "misfit"):
        assert f"--eclbase {eclbase}" in data["steps"][step]["command"]
        assert data["steps"][step]["status"] == status, report
    # The default keys have no FGPT: oil-water decks do not write it, and the summary step failed on them.
    assert "--optional-keys FOPT FWPT FGPT --time-index" in data["steps"]["summary"]["command"]


def test_deck_step_with_include_files(tmp_path):
    include = tmp_path / "relperm.inc"
    include.write_text(
        "SWOF\n 0.2 0.0 1.0 0\n 0.5 0.5 0.3 0\n 0.8 0.3 0.0 0\n 1.0 1.0 0.0 0 /\n"
    )  # krw falls
    code, data, report = run(tmp_path, "--include", str(include))
    assert code == 1 and data["steps"]["deck"]["status"] == "problems"
    assert "1 errors" in data["steps"]["deck"]["facts"]


def test_dependency_warnings_are_not_shown_as_facts():
    text = "x.py:1: DeprecationWarning: old\n  class ResdataVersion(Version):\nreal message\n"
    assert cq.last_lines(text) == "real message"


def test_swatinit_optional_step(monkeypatch, tmp_path):
    from types import SimpleNamespace

    deck = tmp_path / "source/X.DATA"
    deck.parent.mkdir()
    deck.touch()
    for suffix in (".INIT", ".UNRST"):
        deck.with_suffix(suffix).touch()
    args = SimpleNamespace(deck=deck, include=[], metadata=None, outdir=tmp_path / "out", timeout=10)
    calls = []
    monkeypatch.setattr(cq, "run_tool", lambda name, *args: calls.append(name) or {"status": "ok"})
    monkeypatch.setattr(cq.importlib.util, "find_spec", lambda _: None)
    records, skipped = {}, {}
    cq.check_metadata_and_deck(args, records, skipped)
    assert skipped["swatinit"] == "subscript not installed"
    assert "swatinit" not in calls
    monkeypatch.setattr(cq.importlib.util, "find_spec", lambda _: object())
    records, skipped = {}, {}
    cq.check_metadata_and_deck(args, records, skipped)
    assert "swatinit" in calls
    assert records["swatinit"]["status"] == "ok"


def test_swatinit_not_applicable_status(monkeypatch, tmp_path):
    from types import SimpleNamespace

    files = tmp_path / "swatinit/files"
    files.mkdir(parents=True)
    (files / "swatinit_qc.json").write_text('{"status": "not applicable"}')
    monkeypatch.setattr(
        cq.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="SWATINIT QC: not applicable", stderr=""),
    )
    record = cq.run_tool("swatinit", [], tmp_path, 10)
    assert record["exit_code"] == 0
    assert record["status"] == "not applicable"
    report = cq.build_report({"swatinit": record}, {}, [], SimpleNamespace(outdir=tmp_path))
    assert report["data"]["steps"]["swatinit"]["status"] == "not applicable"
    assert report["data"]["attention"] == []


def test_compensation_step_reads_gen_kw_files_from_the_config(tmp_path):
    import random
    from argparse import Namespace

    rng = random.Random(1)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/p.dist").write_text("A UNIFORM 0 1\nB UNIFORM 0 1\n")
    for it, low in ((0, 0.0), (1, 0.97)):
        for real in range(30):
            folder = tmp_path / f"run/realization-{real}/iter-{it}"
            folder.mkdir(parents=True)
            values = [low + (1 - low) * rng.random() for _ in "AB"]
            folder.joinpath("parameters.txt").write_text(f"G:A {values[0]}\nG:B {values[1]}\n")
    out = tmp_path / "out"
    out.mkdir()
    args = Namespace(
        runpaths=str(tmp_path / "run/realization-*/iter-1"),
        prior=str(tmp_path / "run/realization-*/iter-0"),
        gen_kw_dists=[tmp_path / "dist/p.dist"],
        outdir=out,
        timeout=60,
    )
    records, skipped = {}, {}
    cq.check_compensation(args, records, skipped, ["--expected", "30"])
    assert records["compensation"]["status"] == "ok"
    assert "--expected 30" in records["compensation"]["command"]
    assert "verdict implausible; EDGE A, B" in cq.facts("compensation", records["compensation"])
    args.prior = None
    cq.check_compensation(args, records, skipped, [])
    assert "needs --runpaths" in skipped["compensation"]
