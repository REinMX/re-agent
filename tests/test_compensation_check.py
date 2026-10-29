"""Compensation check: a low misfit with implausible parameters.

The bench data is drogon-lite (synthetic, tests/data/drogon_lite): the ES-MDA posteriors of the "base" setup
(old parameter set on the full-physics truth, lowest misfit) and the "screened" setup, 100 members, 3 seeds.
The tables have no member identity column. Complete seed groups are labelled by row position, which the score
does not use, so the measured positives use two seeds and never claim. Base must be flagged and complete
screened seeds must be clean. Screened seed 1001 has a missing posterior member and must refuse.
"""

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

ROOT = Path(__file__).parents[1]
DATA = ROOT / "tests/data/drogon_lite"
SEEDS = (1001, 123456, 2002)
COMPLETE = (123456, 2002)
MISFIT = {"base": (1.846, 1.836, 1.764), "screened": (1.936, 2.233, 2.400)}  # posterior mean misfit, measured


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


comp = load(ROOT / "skills/history-match-qc/scripts/compensation_check.py", "compensation_check_t")


def bench_args(tmp_path: Path, setup: str, seeds: tuple[int, ...] = SEEDS) -> list[str]:
    table = pd.read_csv(DATA / f"comp_{setup}.csv.gz")
    misfits: dict[int, float] = dict(zip(SEEDS, MISFIT[setup], strict=True))
    args = [str(DATA / f"comp_{setup}.dist")]
    for seed in seeds:
        for it, option in ((0, "--prior-csv"), (3, "--posterior-csv")):
            path = tmp_path / f"{setup}_{seed}_{it}.csv"
            group = table[(table.SEED == seed) & (table.ITER == it)].drop(columns=["SEED", "ITER"])
            group.reset_index(drop=True).to_csv(path, index_label="REAL")
            args += [option, str(path)]
        args += ["--label", f"s{seed}", "--seed", str(seed), "--misfit", str(misfits[seed])]
    return args


def run_json(capsys, args: list[str]) -> dict:
    assert comp.main([*args, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_bench_base_is_compensating(tmp_path, capsys):
    report = run_json(capsys, [*bench_args(tmp_path, "base", COMPLETE), "--expected", "100"])
    assert not report["claim_ready"] and report["seeds"] == 2
    assert [r["verdict"] for r in report["runs"]] == ["compensating"] * 2
    assert {"SCAL_GO", "SCAL_WO_FP"} <= set(report["consistent_flags"])
    for run in report["runs"]:
        assert run["member_ids"] == list(range(100))
        assert run["prior_fingerprint"] != run["posterior_fingerprint"]
        assert sum(p["flag"] == "EDGE" for p in run["parameters"]) >= 2


def test_bench_base_truth_grades_and_lists_the_kvkh_without_truth(tmp_path, capsys):
    # The full-physics truth has KVKH_UPPER and KVKH_LOWER, so the single base KVKH has no truth value.
    args = [
        *bench_args(tmp_path, "base", COMPLETE),
        "--expected",
        "100",
        "--truth",
        str(DATA / "comp_truth.txt"),
    ]
    report = run_json(capsys, args)
    assert [r["verdict"] for r in report["runs"]] == ["compensating"] * 2
    for run in report["runs"]:
        assert "KVKH" in run["truth_missing"]
        assert next(p for p in run["parameters"] if p["name"] == "KVKH")["truth_inside_p5_p95"] is None
        edges = [p for p in run["parameters"] if p["flag"] == "EDGE"]
        assert len(edges) >= 2
        # the flags are real: an EDGE parameter misses the truth in most cases (graded with the synthetic truth)
        assert sum(p["truth_inside_p5_p95"] is False for p in edges) * 2 > len(edges)


def test_synthetic_truth_grades_flagged_parameters(tmp_path, capsys):
    rng = random.Random(7)
    post = {"A": [0.97 + 0.03 * rng.random() for _ in range(100)]}
    post.update({k: [rng.random() for _ in range(100)] for k in "BD"})
    post["C"] = [10 ** (-2 + 2 * rng.random()) for _ in range(100)]
    truth = tmp_path / "truth.txt"
    truth.write_text("A 0.2\nB 0.5\nC 0.1\nD 0.5\n")
    run = run_json(capsys, [*synthetic(tmp_path, post), "--truth", str(truth)])["runs"][0]
    graded = {p["name"]: p["truth_inside_p5_p95"] for p in run["parameters"]}
    assert graded == {"A": False, "B": True, "C": True, "D": True}


def test_bench_complete_screened_seeds_are_clean(tmp_path, capsys):
    report = run_json(capsys, [*bench_args(tmp_path, "screened", COMPLETE), "--expected", "100"])
    assert [r["verdict"] for r in report["runs"]] == ["plausible"] * 2
    assert all(r["edges"] == 0 and r["score"] < 0.1 for r in report["runs"])
    assert report["consistent_flags"] == [] and not report["claim_ready"]


def test_bench_screened_missing_member_refuses(tmp_path, capsys):
    table = pd.read_csv(DATA / "comp_screened.csv.gz")
    assert len(table[(table.SEED == 1001) & (table.ITER == 0)]) == 100
    assert len(table[(table.SEED == 1001) & (table.ITER == 3)]) == 99
    assert comp.main(bench_args(tmp_path, "screened")) == 1
    assert "member" in capsys.readouterr().err.lower()


def test_fail_on_gates_the_exit_code(tmp_path, capsys):
    assert comp.main([*bench_args(tmp_path, "base", COMPLETE), "--fail-on", "compensating"]) == 1
    assert comp.main([*bench_args(tmp_path, "screened", COMPLETE), "--fail-on", "suspect"]) == 0
    capsys.readouterr()


def synthetic(tmp_path: Path, post_values: dict[str, list[float]], n: int = 100) -> list[str]:
    rng = random.Random(4)
    dist = tmp_path / "p.dist"
    dist.write_text("A UNIFORM 0 1\nB UNIFORM 0 1\nC LOGUNIF 0.01 1\nD UNIFORM 0 1\n")
    prior = {"A": [rng.random() for _ in range(n)], "B": [rng.random() for _ in range(n)]}
    prior["C"] = [10 ** (-2 + 2 * rng.random()) for _ in range(n)]
    prior["D"] = [rng.random() for _ in range(n)]
    paths = []
    for name, data in (("prior", prior), ("post", post_values)):
        path = tmp_path / f"{name}.csv"
        pd.DataFrame(data).rename_axis("REAL").to_csv(path)
        paths.append(path)
    return [str(dist), "--prior-csv", str(paths[0]), "--posterior-csv", str(paths[1])]


def test_edge_bulk_and_pair(tmp_path, capsys):
    rng = random.Random(5)
    a = [0.97 + 0.03 * rng.random() for _ in range(100)]  # piled at the upper bound
    b = [1.0 - x + 0.02 * rng.random() for x in a]  # pulled by A to the lower bound
    c = [
        10 ** (-0.28 + 0.04 * rng.random()) for _ in range(100)
    ]  # moved well up in log space, inside the range
    d = [rng.random() for _ in range(100)]
    report = run_json(capsys, [*synthetic(tmp_path, {"A": a, "B": b, "C": c, "D": d}), "--misfit", "1.1"])
    run = report["runs"][0]
    flags = {p["name"]: p["flag"] for p in run["parameters"]}
    assert flags == {"A": "EDGE", "B": "EDGE", "C": "BULK", "D": None}
    assert run["verdict"] == "compensating"
    assert any(f["code"] == "HMQC-COMP-PAIR" and f["name"] == "A ~ B" for f in run["findings"])
    assert not report["claim_ready"]


def test_without_a_good_fit_the_verdict_is_implausible(tmp_path, capsys):
    rng = random.Random(6)
    post = {k: [0.99 + 0.01 * rng.random() for _ in range(100)] for k in "ABD"}
    post["C"] = [0.5] * 100
    assert run_json(capsys, synthetic(tmp_path, post))["runs"][0]["verdict"] == "implausible"
    assert (
        run_json(capsys, [*synthetic(tmp_path, post), "--misfit", "9"])["runs"][0]["verdict"] == "implausible"
    )


def manifest_data(verdict="inside the noise band", mean=None, used=range(3), missing=(), issues=()):
    chi2 = {"verdict": verdict} | ({} if mean is None else {"mean_misfit": mean})
    ensembles = {
        k: {"realizations_used": list(used), "missing": list(missing)} for k in ("prior", "posterior")
    }
    return {"ensembles": ensembles, "issues": list(issues), "misfit_vs_chi2": {"posterior": chi2}}


def test_misfit_manifest(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_data()))
    assert comp.fit_from_manifest(manifest, 2.0)[0] is True
    assert comp.fit_from_manifest(manifest, 2.0, {0, 1, 2})[0] is True
    manifest.write_text(json.dumps(manifest_data("above", 3.0)))
    assert comp.fit_from_manifest(manifest, 2.0)[0] is False


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"used": (0, 1), "missing": (2,)}, "missing"),
        ({"issues": ({"ensemble": "posterior", "issue": "missing UNSMRY"},)}, "issues"),
    ],
)
def test_partial_misfit_manifest_gives_an_unknown_fit(tmp_path, kwargs, reason):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_data(**kwargs)))
    fit, basis = comp.fit_from_manifest(manifest, 2.0)
    assert fit is None and reason in basis


def test_unrelated_or_uninventoried_misfit_manifest_gives_an_unknown_fit(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_data()))
    fit, basis = comp.fit_from_manifest(manifest, 2.0, {0, 1, 2, 3})
    assert fit is None and "run members" in basis
    manifest.write_text(json.dumps({"misfit_vs_chi2": {"posterior": {"verdict": "inside the noise band"}}}))
    fit, basis = comp.fit_from_manifest(manifest, 2.0)
    assert fit is None and "issue" in basis


def test_main_checks_the_misfit_manifest_against_the_run_members(tmp_path, capsys):
    rng = random.Random(8)
    post = {k: [rng.random() for _ in range(100)] for k in "ABD"}
    post["C"] = [0.5] * 100
    args = synthetic(tmp_path, post)
    manifest = tmp_path / "manifest.json"
    for used, fit in ((range(100), True), (range(99), None)):
        manifest.write_text(json.dumps(manifest_data(used=used)))
        assert run_json(capsys, [*args, "--misfit-manifest", str(manifest)])["runs"][0]["fit_good"] is fit


def test_usage_errors(tmp_path, capsys):
    args = synthetic(tmp_path, {"A": [0.5] * 10, "B": [0.5] * 10, "C": [0.1] * 10, "D": [0.5] * 10})
    assert comp.main([args[0], "--posterior-csv", args[4]]) == 2
    assert comp.main([*args, "--misfit", "1", "--misfit", "2"]) == 2
    capsys.readouterr()


def test_no_matching_parameter_is_an_input_problem(tmp_path, capsys):
    args = synthetic(tmp_path, {"X": [0.5] * 100})
    assert comp.main(args) == 1
    assert "missing" in capsys.readouterr().err


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_bound_values_have_positions(value):
    param = {"name": "A", "distribution": "UNIFORM", "min": 0.0, "max": 1.0}
    assert comp.positions(param, [value]) == [value]
