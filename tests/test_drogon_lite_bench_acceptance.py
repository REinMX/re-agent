"""drogon-lite bench acceptance: grade() fails closed on stale, partial or misplaced evidence.

Synthetic case under tmp_path; misfit_report.py is replaced by a fake runner. No ERT, no simulator.
"""

import contextlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("pandas")

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("drogon_lite_bench_acc", ROOT / "tools/drogon_lite_bench.py")
bench = importlib.util.module_from_spec(spec)
sys.modules["drogon_lite_bench_acc"] = bench
spec.loader.exec_module(bench)

MEMBERS = (0, 1, 2)
OBS = {"OBS_A": 10.0, "OBS_B": 5.0}
PRIOR = {"PORO_A": (0.12, 0.2, 0.28), "PERM_B": (1.1, 1.5, 1.9)}
POST = {"PORO_A": (0.18, 0.2, 0.22), "PERM_B": (1.4, 1.5, 1.6)}
TRUTH = {"PORO_A": 0.2, "PERM_B": 1.5}
OBS_BLOCK = (
    "SUMMARY_OBSERVATION {name} {{ VALUE = {value}; ERROR = 1; DATE = 2020-01-01; KEY = WOPR:{name}; }};\n"
)


def make_case(root: Path, *, members=MEMBERS, config_reals="3", obs_extra="", truth_obs=OBS, truth=TRUTH):
    case = root / "case"
    for folder in ("ert/model", "ert/input/distributions", "ert/input/observations", "eclipse/model"):
        (case / folder).mkdir(parents=True)
    (case / "ert/input/distributions/drogon.dist").write_text("PORO_A UNIFORM 0.1 0.3\nPERM_B UNIFORM 1 2\n")
    lines = [f"NUM_REALIZATIONS {config_reals}\n"] if config_reals else []
    lines.append("GEN_KW DROGON ../input/distributions/drogon.dist\n")
    (case / bench.CONFIG).write_text("".join(lines))
    obs_text = "".join(OBS_BLOCK.format(name=n, value=v) for n, v in OBS.items())
    (case / bench.OBS).write_text(obs_text + obs_extra)
    observations = [{"name": n, "truth": v} for n, v in truth_obs.items()]
    (case / bench.TRUTH).write_text(json.dumps({"parameters": truth, "observations": observations}))
    (case / bench.DECK).write_text("RUNSPEC\nDIMENS\n 1 1 1 /\nOIL\nWATER\n")
    top = case / "scratch/user/01_drogonlite_ahm"
    for iteration, values in ((0, PRIOR), (3, POST)):
        for i, real in enumerate(members):
            run = top / f"realization-{real}/iter-{iteration}"
            run.mkdir(parents=True)
            text = "".join(f"DROGON:{name} {v[i]}\n" for name, v in values.items())
            (run / "parameters.txt").write_text(text)
    return case


class FakeMisfit:
    """Stands in for misfit_report.py: writes residuals.csv and manifest.json into --outdir."""

    def __init__(self, returncode=0, members=MEMBERS, obs=tuple(OBS), manifest=None, stdout="", stderr=""):
        self.returncode, self.members, self.obs = returncode, members, obs
        self.manifest, self.stdout, self.stderr = manifest, stdout, stderr
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        out = Path(command[command.index("--outdir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        rows = ["ENSEMBLE,REAL,OBS,OBSERVED,ERROR,SIMULATED"]
        for label, spread in (("prior", 2.0), ("posterior", 0.5)):
            for real in self.members:
                for name in self.obs:
                    rows.append(
                        f"{label},{real},{name},{OBS[name] + 0.1},1.0,{OBS[name] + spread * (real - 1)}"
                    )
        (out / "residuals.csv").write_text("\n".join(rows) + "\n")
        used = sorted(self.members)
        manifest = self.manifest or {
            "ensembles": {
                label: {"realizations_used": used, "missing": [], "extra_not_used": []}
                for label in ("prior", "posterior")
            },
            "issues": [],
            "misfit_vs_chi2": {"posterior": {"verdict": "inside the noise band", "mean_misfit": 1.0}},
        }
        (out / "manifest.json").write_text(json.dumps(manifest))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


def install(monkeypatch, fake: FakeMisfit) -> FakeMisfit:
    monkeypatch.setattr(bench.subprocess, "run", fake)
    return fake


def test_complete_synthetic_case_grades(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    fake = install(monkeypatch, FakeMisfit())
    result = bench.grade(case, 3, "plausible", tmp_path / "out")
    assert {"case", "expect", "passed", "criteria", "compensation", "coverage"} <= result.keys()
    names = {c["name"] for c in result["criteria"]}
    assert names == {
        "compensation_verdict",
        "edge_flags_are_real",
        "coverage_unbiased",
        "physics_complete",
        "coverage_calibrated",
    }
    assert (result["coverage"]["n_obs"], result["coverage"]["n_real"]) == (2, 3)
    assert len(fake.calls) == 1


def test_non_summary_observations_are_graded_around_and_listed_as_skipped(tmp_path, monkeypatch):
    extra = (
        "GENERAL_OBSERVATION GEN_1 { DATA = RFT; RESTART = 1; OBS_FILE = rft.txt; };\n"
        "BREAKTHROUGH_OBSERVATION BT_1 { KEY = WWCT:P1; DATE = 2021-07-24; ERROR = 30; THRESHOLD = 0.1; };\n"
    )
    case = make_case(tmp_path, obs_extra=extra)
    install(monkeypatch, FakeMisfit())
    result = bench.grade(case, 3, "plausible", tmp_path / "out")
    assert result["coverage"]["n_obs"] == 2
    assert "GEN_1" not in result["observations"]
    assert [(s["name"], s["kind"]) for s in result["skipped_observations"]] == [
        ("GEN_1", "GENERAL_OBSERVATION"),
        ("BT_1", "BREAKTHROUGH_OBSERVATION"),
    ]


def test_misfit_report_runs_without_allow_partial_and_with_the_configured_members(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    fake = install(monkeypatch, FakeMisfit())
    with contextlib.suppress(ValueError):
        bench.grade(case, 3, "plausible", tmp_path / "out")
    command = fake.calls[0]
    assert "--allow-partial" not in command
    assert command[command.index("--expected") + 1] == "3"


def test_failed_misfit_run_is_refused_even_with_a_manifest(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    install(monkeypatch, FakeMisfit(returncode=1))
    with pytest.raises(ValueError, match="exit code 1"):
        bench.grade(case, 3, "plausible", tmp_path / "out")


def test_failed_misfit_output_is_kept_in_a_log(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    install(monkeypatch, FakeMisfit(returncode=1, stdout="STDOUT-MARK", stderr="STDERR-MARK"))
    with pytest.raises(ValueError):
        bench.grade(case, 3, "plausible", tmp_path / "out")
    log = (tmp_path / "out" / "misfit_report.log").read_text()
    assert "STDOUT-MARK" in log and "STDERR-MARK" in log


def test_stale_manifest_in_an_existing_output_is_never_read(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    stale = tmp_path / "out" / "misfit"
    FakeMisfit()(["misfit_report.py", "--outdir", str(stale)])
    before = (stale / "manifest.json").read_text()
    fake = install(monkeypatch, FakeMisfit(returncode=1))
    with pytest.raises(FileExistsError):
        bench.grade(case, 3, "plausible", tmp_path / "out")
    assert not fake.calls
    assert (stale / "manifest.json").read_text() == before


def test_existing_empty_output_is_refused(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    (tmp_path / "out").mkdir()
    fake = install(monkeypatch, FakeMisfit())
    with pytest.raises(FileExistsError):
        bench.grade(case, 3, "plausible", tmp_path / "out")
    assert not fake.calls


def test_main_refuses_an_existing_output(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    (tmp_path / "out").mkdir()
    fake = install(monkeypatch, FakeMisfit())
    code = bench.main(["--case", str(case), "--expect", "plausible", "--out", str(tmp_path / "out")])
    assert code == 1
    assert not fake.calls
    assert not (tmp_path / "out" / "bench.json").exists()


@pytest.mark.parametrize("where", ["inside", "same", "contains"])
def test_output_overlapping_the_case_is_refused(tmp_path, monkeypatch, where):
    case = make_case(tmp_path)
    out = {"inside": case / "bench", "same": case, "contains": tmp_path}[where]
    fake = install(monkeypatch, FakeMisfit())
    with pytest.raises(ValueError, match="overlap"):
        bench.grade(case, 3, "plausible", out)
    assert not fake.calls
    assert not (case / "bench").exists()


def test_residuals_missing_a_configured_observation_are_refused(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    install(monkeypatch, FakeMisfit(obs=("OBS_A",)))
    with pytest.raises(ValueError, match="OBS_B"):
        bench.grade(case, 3, "plausible", tmp_path / "out")


def test_residuals_missing_a_configured_member_are_refused(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    install(monkeypatch, FakeMisfit(members=(0, 1)))
    with pytest.raises(ValueError, match=r"missing \[2\]|expected \[0, 1, 2\]"):
        bench.grade(case, 3, "plausible", tmp_path / "out")


def test_partial_manifest_is_refused(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    manifest = {
        "ensembles": {
            "prior": {"realizations_used": [0, 1, 2], "missing": []},
            "posterior": {"realizations_used": [0, 1], "missing": [2]},
        },
        "issues": [{"ensemble": "posterior", "realization": 2, "issue": "missing UNSMRY"}],
        "misfit_vs_chi2": {"posterior": {"verdict": "inside the noise band"}},
    }
    install(monkeypatch, FakeMisfit(manifest=manifest))
    with pytest.raises(ValueError, match="manifest"):
        bench.grade(case, 3, "plausible", tmp_path / "out")


def test_malformed_manifest_is_refused(tmp_path, monkeypatch):
    case = make_case(tmp_path)
    install(monkeypatch, FakeMisfit(manifest={"ensembles": []}))
    with pytest.raises(ValueError, match="manifest"):
        bench.grade(case, 3, "plausible", tmp_path / "out")


def test_a_run_short_of_the_configured_ensemble_is_refused(tmp_path, monkeypatch):
    """Runpaths, residuals and manifest agree on 2 members; the config says 3."""
    case = make_case(tmp_path, members=(0, 1))
    install(monkeypatch, FakeMisfit(members=(0, 1)))
    with pytest.raises(ValueError, match=r"missing \[2\]|expected \[0, 1, 2\]"):
        bench.grade(case, 3, "plausible", tmp_path / "out")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"config_reals": None}, "NUM_REALIZATIONS"),
        ({"truth_obs": {"OBS_A": 10.0}}, "OBS_B"),
    ],
)
def test_incomplete_configuration_is_refused_before_misfit_report_runs(
    tmp_path, monkeypatch, kwargs, message
):
    case = make_case(tmp_path, **kwargs)
    fake = install(monkeypatch, FakeMisfit())
    with pytest.raises(ValueError, match=message):
        bench.grade(case, 3, "plausible", tmp_path / "out")
    assert not fake.calls


def make_kvkh_case(tmp_path, *, truth=None, other_edges=False):
    if truth is None:
        truth = {**TRUTH, "KVKH_UPPER": 0.15, "KVKH_LOWER": 0.04}
    case = make_case(tmp_path, truth=truth)
    dist = case / "ert/input/distributions/drogon.dist"
    dist.write_text(dist.read_text() + "KVKH LOGUNIF 0.02 0.5\n")
    for iteration in (0, 3):
        for real in MEMBERS:
            path = case / f"scratch/user/01_drogonlite_ahm/realization-{real}/iter-{iteration}/parameters.txt"
            text = path.read_text()
            if iteration == 3 and other_edges:
                text = "DROGON:PORO_A 0.101\nDROGON:PERM_B 1.01\n"
            value = (0.05, 0.1, 0.2)[real] if iteration == 0 else 0.0201
            path.write_text(text + f"DROGON:KVKH {value}\n")
    return case


def test_only_split_kvkh_truth_is_exempt_and_the_negative_control_is_graded(tmp_path, monkeypatch, capsys):
    case = make_kvkh_case(tmp_path, other_edges=True)
    fake = install(monkeypatch, FakeMisfit())
    out = tmp_path / "out"
    code = bench.main(["--case", str(case), "--expect", "compensating", "--out", str(out)])
    assert len(fake.calls) == 1
    result = json.loads((out / "bench.json").read_text())
    assert result["truth_missing"] == ["KVKH"]
    criteria = {c["name"]: c for c in result["criteria"]}
    assert "expected compensating" in criteria["compensation_verdict"]["measured"]
    assert criteria["edge_flags_are_real"]["passed"]
    assert "2 of 2 EDGE" in criteria["edge_flags_are_real"]["measured"]
    assert "1 ungraded EDGE" in criteria["edge_flags_are_real"]["measured"]
    assert result["compensation"]["verdict"] == "compensating"
    assert result["compensation"]["score"] == 1.0
    assert code == (0 if result["passed"] else 1)
    assert "compensation_verdict" in capsys.readouterr().out


@pytest.mark.parametrize(
    "truth",
    [
        {"PORO_A": 0.2},
        {},
        {"RENAMED_PORO_A": 0.2, "RENAMED_PERM_B": 1.5},
        {"PORO_A": {"value": 0.2}, "PERM_B": {"value": 1.5}},
    ],
)
def test_missing_bounded_truth_refuses_before_subprocess(tmp_path, monkeypatch, capsys, truth):
    case = make_case(tmp_path, truth=truth)
    fake = install(monkeypatch, FakeMisfit())
    out = tmp_path / "out"
    assert bench.main(["--case", str(case), "--expect", "plausible", "--out", str(out)]) == 1
    assert "no truth value for bounded parameters" in capsys.readouterr().err
    assert not fake.calls and not out.exists()


def test_absent_parameter_truth_section_refuses_before_subprocess(tmp_path, monkeypatch, capsys):
    case = make_case(tmp_path)
    path = case / bench.TRUTH
    data = json.loads(path.read_text())
    del data["parameters"]
    path.write_text(json.dumps(data))
    fake = install(monkeypatch, FakeMisfit())
    assert bench.main(["--case", str(case), "--expect", "plausible", "--out", str(tmp_path / "out")]) == 1
    assert "no truth value for bounded parameters" in capsys.readouterr().err
    assert not fake.calls


@pytest.mark.parametrize(
    "truth",
    [
        {"DROGON:PORO_A": 0.2, "DROGON:PERM_B": 1.5},
        {"PORO_A": 0.2, "PERM_B": 1.5},
    ],
)
def test_parameter_truth_accepts_exact_or_bare_names(tmp_path, monkeypatch, truth):
    case = make_case(tmp_path, truth=truth)
    (case / "ert/input/distributions/drogon.dist").write_text(
        "DROGON:PORO_A UNIFORM 0.1 0.3\nDROGON:PERM_B UNIFORM 1 2\n"
    )
    fake = install(monkeypatch, FakeMisfit())
    result = bench.grade(case, 3, "plausible", tmp_path / "out")
    assert not result["truth_missing"] and len(fake.calls) == 1


@pytest.mark.parametrize("ambiguous", [False, True])
def test_parameter_truth_matching_errors_refuse_before_subprocess(tmp_path, monkeypatch, capsys, ambiguous):
    case = make_case(tmp_path, truth={**TRUTH, "DROGON:PORO_A": 0.2} if not ambiguous else TRUTH)
    if ambiguous:
        dist = case / "ert/input/distributions/drogon.dist"
        dist.write_text("DROGON:PORO_A UNIFORM 0.1 0.3\nOTHER:PORO_A UNIFORM 0.1 0.3\nPERM_B UNIFORM 1 2\n")
    fake = install(monkeypatch, FakeMisfit())
    assert bench.main(["--case", str(case), "--expect", "plausible", "--out", str(tmp_path / "out")]) == 1
    assert ("ambiguous parameter" if ambiguous else "duplicate match") in capsys.readouterr().err
    assert not fake.calls


@pytest.mark.parametrize("counterpart", ["KVKH_UPPER", "KVKH_LOWER"])
@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), {"value": 0.1}])
def test_kvkh_exemption_requires_both_finite_counterparts(tmp_path, monkeypatch, capsys, counterpart, value):
    truth = {**TRUTH, "KVKH_UPPER": 0.1, "KVKH_LOWER": 0.2}
    if value is None:
        del truth[counterpart]
    else:
        truth[counterpart] = value
    case = make_kvkh_case(tmp_path, truth=truth)
    fake = install(monkeypatch, FakeMisfit())
    out = tmp_path / "out"
    assert bench.main(["--case", str(case), "--expect", "compensating", "--out", str(out)]) == 1
    assert "no truth value for bounded parameters" in capsys.readouterr().err
    assert not fake.calls and not out.exists()


def test_kvkh_only_edge_is_reported_as_ungraded_not_passed(tmp_path, monkeypatch):
    case = make_kvkh_case(tmp_path)
    install(monkeypatch, FakeMisfit())
    result = bench.grade(case, 3, "compensating", tmp_path / "out")
    edge = next(c for c in result["criteria"] if c["name"] == "edge_flags_are_real")
    assert not edge["passed"]
    assert "1 ungraded EDGE" in edge["measured"]
