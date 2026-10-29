"""ahm_case_adapter.py on a synthetic exported case: runpaths without a .DATA deck and an fmu-tools YAML table."""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import json
import math
import shutil
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pandas")
pytest.importorskip("scipy")
yaml = pytest.importorskip("yaml")

SCRIPTS = Path(__file__).parents[1] / "skills/history-match-qc/scripts"
spec = importlib.util.spec_from_file_location("ahm_case_adapter_t", SCRIPTS / "ahm_case_adapter.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
obs_qc = adapter._load_sibling("obs_qc")

N = 12
BASE = "eclipse/model/CASE-<IENS>"
START = dt.datetime(2020, 1, 1)
DATES = [dt.datetime(2020, m, 1) for m in (2, 4, 6, 8)]
OBS_DATES = ["2020-04-01", "2020-06-01", "2020-08-01"]
TRUE_X = 0.7


def response(x: float, when: dt.datetime) -> float:
    return 200.0 + 50.0 * x + 0.1 * (when - START).days


def params(real: int, posterior: bool) -> dict[str, float]:
    u = (real + 0.5) / N
    x = TRUE_X + 0.04 * (u - 0.5) if posterior else u
    mult = 10.0 ** (-3.0 + 3.0 * u)
    return {"GV:X": x, "GV:REF": 1050.0, "GV:MULT": mult, "LOG10_GV:MULT": math.log10(mult)}


def write_member(runpath: Path, real: int, posterior: bool, summary: bool) -> None:
    runpath.mkdir(parents=True, exist_ok=True)
    values = params(real, posterior)
    (runpath / "parameters.txt").write_text("".join(f"{k} {v!r}\n" for k, v in values.items()))
    base = runpath / BASE.replace("<IENS>", str(real))
    base.parent.mkdir(parents=True, exist_ok=True)
    if not summary:
        return
    try:
        from resdata.summary import Summary
    except ImportError:
        base.with_name(base.name + ".SMSPEC").write_text("")
        base.with_name(base.name + ".UNSMRY").write_text("")
        return
    writer = Summary.writer(str(base), START, 1, 1, 1)
    writer.add_variable("WBHP", wgname="P1")
    for step, when in enumerate(DATES, start=1):
        tstep = writer.add_t_step(step, float((when - START).days))
        tstep["WBHP:P1"] = response(values["GV:X"], when)
    writer.fwrite()


def make_case(
    root: Path, missing: dict[str, set[int]] | None = None, no_summary: set[int] = frozenset()
) -> Path:
    missing = missing or {}
    for label, iteration in (("prior", "iter-0"), ("posterior", "iter-3")):
        for real in range(N):
            if real in missing.get(label, set()):
                continue
            runpath = root / f"realization-{real}" / iteration
            write_member(runpath, real, label == "posterior", real not in no_summary or label == "prior")
    table = {
        "smry": [
            {
                "key": "WBHP:P1",
                "observations": [
                    {
                        "date": d,
                        "value": response(TRUE_X, dt.datetime.fromisoformat(d)),
                        "error": 2.0,
                        "label": f"P1_WBHP_{i}",
                    }
                    for i, d in enumerate(OBS_DATES)
                ],
            }
        ],
        "rft": [{"well": "P1", "observations": []}],
    }
    obs = root / "ert_observations.yml"
    obs.write_text(yaml.safe_dump(table))
    (root / "ref.dist").write_text("X UNIFORM 0 1\nREF CONST 1050\nMULT LOGUNIF 1e-6 1e-4\n")
    return obs


def args(root: Path, out: Path, *extra: str) -> list[str]:
    return [
        "--prior-runpaths", str(root / "realization-*" / "iter-0"),
        "--posterior-runpaths", str(root / "realization-*" / "iter-3"),
        "--eclbase", BASE,
        "--observations", str(root / "ert_observations.yml"),
        "--expected", str(N),
        "--dist-reference", str(root / "ref.dist"),
        "--outdir", str(out),
        *extra,
    ]  # fmt: skip


def test_observations_become_summary_blocks(tmp_path):
    make_case(tmp_path)
    out = tmp_path / "out"
    assert adapter.main(args(tmp_path, out)) == 0
    records = obs_qc.parse_file(out / "observations.obs")
    assert [r["name"] for r in records] == ["P1_WBHP_0", "P1_WBHP_1", "P1_WBHP_2"]
    for record in records:
        normal = obs_qc.normalize_summary(record)
        assert not [i for i in normal["issues"] if i["severity"] == "error"]
        assert normal["key"] == "WBHP:P1"
    report = json.loads((out / "members.json").read_text())
    assert report["observations"] == 3
    assert report["observation_sections_not_used"] == ["rft"]


def test_partial_posterior_fails_closed_unless_named(tmp_path, capsys):
    make_case(tmp_path, missing={"posterior": {5}})
    assert adapter.main(args(tmp_path, tmp_path / "a")) == 1
    assert "partial ensemble" in capsys.readouterr().err
    assert adapter.main(args(tmp_path, tmp_path / "b", "--allow-missing", "posterior:5")) == 0
    report = json.loads((tmp_path / "b" / "members.json").read_text())
    assert report["common_realizations"] == "0-4,6-11"
    assert report["n_common"] == N - 1
    assert report["left_out"]["posterior"] == {"5": "no realization folder"}
    assert report["left_out"]["comparison"] == {"5": "only in prior"}
    with (tmp_path / "b" / "prior_params.csv").open() as handle:
        reals = [int(row["REAL"]) for row in csv.DictReader(handle)]
    assert reals == [r for r in range(N) if r != 5]
    assert not (tmp_path / "b" / "runpaths" / "prior" / "realization-5").exists()


def test_allow_missing_must_name_a_real_gap(tmp_path, capsys):
    make_case(tmp_path)
    assert adapter.main(args(tmp_path, tmp_path / "o", "--allow-missing", "posterior:3")) == 1
    assert "present" in capsys.readouterr().err


def test_missing_summary_is_never_zero(tmp_path, capsys):
    make_case(tmp_path, no_summary={2})
    assert adapter.main(args(tmp_path, tmp_path / "o")) == 1
    err = capsys.readouterr().err
    assert "realization-2" in err and ".SMSPEC" in err and ".UNSMRY" in err


def test_prior_rebuild_sources(tmp_path):
    make_case(tmp_path)
    out = tmp_path / "out"
    assert adapter.main(args(tmp_path, out)) == 0
    lines = dict(line.split(" ", 1) for line in (out / "prior.dist").read_text().splitlines())
    assert lines["X"] == "UNIFORM 0.0 1.0"
    assert lines["REF"] == "CONST 1050.0"
    assert lines["MULT"] == "LOGUNIF 0.001 1"
    with (out / "prior_provenance.csv").open() as handle:
        rows = {row["name"]: row for row in csv.DictReader(handle)}
    assert rows["X"]["source"] == "reference"
    assert rows["REF"]["source"] == "constant"
    assert rows["MULT"]["source"].startswith(
        "rebuilt from the prior sample; reference LOGUNIF 1e-06 0.0001 excludes"
    )
    assert float(rows["X"]["ks_p"]) > 0.5
    assert not [c for c in (out / "prior_params.csv").read_text().splitlines()[0].split(",") if "LOG10_" in c]


def test_staging_links_and_stub_deck(tmp_path):
    make_case(tmp_path)
    out = tmp_path / "out"
    assert adapter.main(args(tmp_path, out)) == 0
    target = out / "runpaths" / "posterior" / "realization-4" / "iter-0"
    assert (target / "parameters.txt").is_symlink()
    assert (target / "parameters.txt").resolve() == (tmp_path / "realization-4" / "iter-3" / "parameters.txt")
    assert (target / "eclipse/model/CASE-4.DATA").read_text() == ""
    assert (target / "eclipse/model/CASE-4.UNSMRY").is_symlink()


def test_bad_options(tmp_path):
    make_case(tmp_path)
    bad = args(tmp_path, tmp_path / "o")
    bad[bad.index(BASE)] = "eclipse/model/CASE"
    assert adapter.main(bad) == 2
    assert adapter.main(args(tmp_path, tmp_path / "o", "--allow-missing", "iter3:1")) == 2


def test_run_end_to_end(tmp_path):
    pytest.importorskip("resdata")
    pytest.importorskip("res2df")
    make_case(tmp_path, missing={"posterior": {7}})
    out = tmp_path / "out"
    assert adapter.main(args(tmp_path, out, "--allow-missing", "posterior:7", "--run")) == 0
    manifest = json.loads((out / "misfit" / "manifest.json").read_text())
    assert sorted(manifest["ensembles"]["prior"]["realizations_used"]) == [r for r in range(N) if r != 7]
    doc = json.loads((out / "overview" / "overview.json").read_text())
    params = {p["name"]: p for p in doc["parameters"]}
    assert set(params) == {"X", "MULT"}
    assert params["X"]["sd_ratio"] < 0.2
    groups = {g["group"]: g for g in doc["observations"]["groups"] if g["level"] == "family"}
    assert groups["WBHP"]["post_misfit"] < groups["WBHP"]["prior_misfit"]


def test_reference_that_excludes_a_prior_value_is_not_used(tmp_path):
    make_case(tmp_path)
    (tmp_path / "ref.dist").write_text("X UNIFORM 0.1 1\n")
    out = tmp_path / "out"
    assert adapter.main(args(tmp_path, out)) == 0
    with (out / "prior_provenance.csv").open() as handle:
        rows = {row["name"]: row for row in csv.DictReader(handle)}
    assert rows["X"]["source"].startswith("rebuilt from the prior sample; reference UNIFORM 0.1 1 excludes")
    assert rows["X"]["line"] == "X UNIFORM 0.04 0.96"


def test_stale_staging_is_refused(tmp_path, capsys):
    make_case(tmp_path)
    out = tmp_path / "out"
    assert adapter.main(args(tmp_path, out)) == 0
    shutil.rmtree(tmp_path / "realization-4")
    assert (
        adapter.main(args(tmp_path, out, "--allow-missing", "prior:4", "--allow-missing", "posterior:4")) == 1
    )
    assert "earlier run" in capsys.readouterr().err
