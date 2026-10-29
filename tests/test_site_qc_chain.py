"""Site 02-ahm-esmda-qc: the installed QC chain passes the gates of ahm-workflow and history-match-qc.

The pack is rendered from the real templates into tmp_path. The runpaths, the update log, the screening
record and the held-out split are synthetic. No ERT, Flow or network.
"""

import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("numpy")
pytest.importorskip("pandas")
pytest.importorskip("resdata")
pytest.importorskip("res2df")

import synth_fmu  # noqa: E402
import test_heldout_score as heldout_tests  # noqa: E402
import test_render_template as rendering  # noqa: E402
import test_screening_integrity as integrity  # noqa: E402

ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "skills"
HM = SKILLS / "history-match-qc/scripts"
CASE = "drogon_ahm"
WEIGHTS = "15,7.5,3.75,1.875"
SEED = 20261015
case = integrity.case
bundle = heldout_tests.bundle
POST_DIST = "A UNIFORM 0 1\nB UNIFORM 0 1\nC LOGUNIF 0.1 10\n"
OBS_DATES = ("2020-07-01", "2021-01-01", "2021-07-01", "2022-01-01")
SYNTHETIC_FOPT_RATE = 130.0
SMALL = ("num_realizations=8", "min_realizations=8")


@pytest.fixture(autouse=True)
def no_contract_env(monkeypatch):
    for name in ("RE_AGENT_CONTRACT", "RE_AGENT_PROFILE", "RE_AGENT_ALLOW_PROJECT_PROFILES"):
        monkeypatch.delenv(name, raising=False)


def load_chain(path):
    spec = importlib.util.spec_from_file_location("site_qc_chain", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pack_observations():
    """The observations of the pack OBS_CONFIG. The screening record must name the same set."""
    start = dt.date(2020, 1, 1)
    observations = []
    for day in OBS_DATES:
        value = SYNTHETIC_FOPT_RATE * (dt.date.fromisoformat(day) - start).days
        observations.append(
            {
                "OBS": f"FOPT_{day.replace('-', '')}",
                "OBSERVED": value,
                "ERROR": value * 0.05,
                "KEY": "FOPT",
                "DATE": day,
            }
        )
    return observations


def observation_blocks():
    return "".join(
        f"SUMMARY_OBSERVATION {obs['OBS']} {{ VALUE = {obs['OBSERVED']}; ERROR = {obs['ERROR']}; "
        f"DATE = {obs['DATE']}; KEY = {obs['KEY']}; }};\n"
        for obs in pack_observations()
    )


def screening_rows(observations):
    """Two endpoint members of A, as in the integrity case: low matches, high is 6 errors over each observation."""
    rows = []
    for real, factor, status in ((0, 1.0, "exact"), (1, 1.3, "interpolated")):
        for obs in observations:
            simulated = obs["OBSERVED"] * factor
            rows.append(
                {
                    "ENSEMBLE": "screen",
                    "REAL": real,
                    **{column: obs[column] for column in ("OBS", "KEY", "DATE", "OBSERVED", "ERROR")},
                    "SIMULATED": simulated,
                    "STATUS": status,
                    "RESIDUAL": (simulated - obs["OBSERVED"]) / obs["ERROR"],
                }
            )
    return rows


def make_pack(tmp_path, *sets, dist="A UNIFORM 0 1\n"):
    """01 and 02 rendered as siblings, with a synthetic prior and observation file."""
    (tmp_path / "deck/model").mkdir(parents=True)
    (tmp_path / "deck/include").mkdir()
    (tmp_path / "deck/model/DROGON_AHM.DATA").write_text("-- synthetic\n")
    contract = rendering.write_contract(tmp_path)
    packed = tmp_path / "pack"
    for name in rendering.NAMES[:2]:
        argv = [str(rendering.TEMPLATES / name), str(packed / name), "--contract", str(contract)]
        argv += rendering.sets(tmp_path, name)
        if name.startswith("02"):
            argv += [f"--set={item}" for item in sets]
        assert rendering.renderer.main(argv) == 0
    prior = packed / "01-fmu-prior-design"
    (prior / "ert/prior.dist").write_text(dist)
    (prior / "obs/history.obs").write_text(observation_blocks())
    folder = packed / "02-ahm-esmda-qc"
    chain = load_chain(folder / "bin/qc_chain.py")
    return SimpleNamespace(
        folder=folder,
        prior=prior,
        chain=chain,
        qc_file=folder / "qc.yml",
        qc=chain.load(folder / "qc.yml"),
        case_dir=tmp_path / "scratch" / CASE,
        screening=folder / "output" / CASE / "screening/screening.json",
        out=tmp_path / "qc-out",
        tmp=tmp_path,
    )


@pytest.fixture
def pack(tmp_path):
    return make_pack(tmp_path)


def names(items):
    return [name for name, _ in items]


def step(pack, stage, name, qc=None):
    return dict(pack.chain.steps(stage, qc or pack.qc, pack.out))[name]


def with_qc(pack, **changes):
    return {**pack.qc, **changes}


def write_screening(case, pack):
    case.prior = pack.prior / "ert/prior.dist"
    case.observations = pack_observations()
    case.rows = screening_rows(case.observations)
    result = integrity.run_screen(case, output=str(pack.screening.parent.relative_to(case.root)))
    assert result.returncode == 0, result.stderr
    assert pack.screening.is_file()


def run_step(argv):
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True, check=False)


def write_ensemble(pack, iteration, members, shift=0.0):
    for real in range(members):
        runpath = pack.case_dir / f"realization-{real}" / f"iter-{iteration}"
        synth_fmu.write_summary(runpath / "model" / f"DROGON-{real}", real)
        spread = (real + 1) / (members + 1) - 0.5
        width = 1 - shift
        values = {
            "A": 0.5 + 0.8 * width * spread,
            "B": 0.5 - 0.8 * width * spread,
            "C": 10 ** (2 * width * spread),
        }
        (runpath / "parameters.txt").write_text(
            "".join(f"PRIOR:{name} {value}\n" for name, value in values.items())
        )
        (runpath / "OK").write_text("ok\n")


def write_update_log(pack):
    scalings = [float(w) for w in WEIGHTS.split(",")]
    for index, scaling in enumerate(scalings):
        folder = pack.folder / "output" / CASE / "update_log" / f"step-{index}"
        folder.mkdir(parents=True)
        (folder / "Report.csv").write_text(
            "observation_key,index,observations,std,obs_error_scaling,scaled_obs_error,"
            "response_mean,response_std,status,missing_realizations\n"
            "FOPT_20200701,2020-07-01,23660,1183,1.0,1183,23000,2000,Active,\n"
            "FOPT_20210101,2021-01-01,47580,2379,1.0,2379,47000,4000,Active,\n"
        )
        (folder / "Report.report").write_text(
            f"Parent ensemble: iter-{index}\nTarget ensemble: iter-{index + 1}\nAlpha: 3.0\n"
            f"Global scaling: {scaling}\nStandard cutoff: 1e-06\nActive observations: 2\n"
            "Deactivated observations: missing respons(es): 0\n"
            "Deactivated observations: ensemble_std < STD_CUTOFF: 0\n"
            "Deactivated observations: outliers: 0\n"
        )


def full_case(pack, members):
    write_ensemble(pack, 0, members)
    write_ensemble(pack, len(WEIGHTS.split(",")), members, shift=0.5)
    write_update_log(pack)


# ---- load(): qc.yml keys ----


def test_qc_yml_gains_the_screening_seed_reason_and_split_keys(pack):
    raw = yaml.safe_load(pack.qc_file.read_text())
    assert raw["screening"] == f"output/{CASE}/screening/screening.json"
    assert raw["random_seed"] == SEED
    assert raw["allow_members_reason"] is None
    assert raw["heldout_split"] is None
    assert raw["truth"] is None


def test_load_resolves_the_new_paths_against_the_template_folder(tmp_path):
    pack = make_pack(tmp_path, "heldout_split=output/drogon_ahm/heldout/split.json")
    assert pack.qc["screening"] == pack.folder / "output" / CASE / "screening/screening.json"
    assert pack.qc["heldout_split"] == pack.folder / "output/drogon_ahm/heldout/split.json"
    assert pack.qc["random_seed"] == SEED and isinstance(pack.qc["random_seed"], int)
    assert pack.qc["allow_members_reason"] is None


def test_the_seed_of_the_chain_is_the_seed_of_the_config(pack):
    config = (pack.folder / "ert/ahm.ert").read_text()
    assert re.search(rf"^RANDOM_SEED\s+{pack.qc['random_seed']}$", config, re.M)


@pytest.mark.parametrize("missing", ["screening", "random_seed"])
def test_load_refuses_a_qc_yml_from_before_the_gates(pack, missing):
    text = "".join(
        line for line in pack.qc_file.read_text().splitlines(keepends=True) if not line.startswith(missing)
    )
    pack.qc_file.write_text(text)
    with pytest.raises(SystemExit, match=missing):
        pack.chain.load(pack.qc_file)


# ---- steps(): names, order and argv ----


def test_setup_ends_with_the_preflight_gate(pack):
    assert names(pack.chain.steps("setup", pack.qc, pack.out)) == [
        "prior_qc",
        "obs_qc",
        "check_ert_config",
        "ahm_preflight",
    ]
    assert "--ahm" in step(pack, "setup", "check_ert_config")


def test_preflight_argv_names_the_config_the_record_and_the_weights(pack):
    assert step(pack, "setup", "ahm_preflight") == [
        str(SKILLS / "ahm-workflow/scripts/ahm_preflight.py"),
        str(pack.folder / "ert/ahm.ert"),
        "--screening",
        str(pack.folder / "output" / CASE / "screening/screening.json"),
        "--weights",
        WEIGHTS,
    ]


def test_preflight_has_no_members_waiver_by_default(pack):
    assert "--allow-members" not in step(pack, "setup", "ahm_preflight")
    assert "allow_members_reason: null" in pack.qc_file.read_text()


@pytest.mark.parametrize("reason", ["quick look", '"16 members: quick look"'])
def test_members_waiver_is_passed_and_stays_visible_in_qc_yml(tmp_path, reason):
    pack = make_pack(tmp_path, f"allow_members_reason={reason}")
    argv = step(pack, "setup", "ahm_preflight")
    assert argv[-2:] == ["--allow-members", reason.strip('"')]
    assert f"allow_members_reason: {reason}" in pack.qc_file.read_text()


def test_post_runs_coverage_and_the_compensation_check_after_the_misfit_report(pack):
    assert names(pack.chain.steps("post", pack.qc, pack.out)) == [
        "update_log_report",
        "misfit_report",
        "coverage",
        "compensation_check",
        "prior_posterior",
        "triage_runpath",
    ]


def test_coverage_reads_the_posterior_residuals_of_the_misfit_report(pack):
    assert step(pack, "post", "coverage") == [
        str(HM / "coverage.py"),
        "--residuals",
        str(pack.out / "misfit/residuals.csv"),
        "--ensemble",
        "posterior",
    ]


def test_compensation_check_gets_both_ensembles_the_seed_and_the_misfit_manifest(pack):
    case_dir = pack.qc["case_dir"]
    assert step(pack, "post", "compensation_check") == [
        str(HM / "compensation_check.py"),
        str(pack.qc["dist"]),
        "--prior-runpaths",
        str(case_dir / "realization-*" / "iter-0"),
        "--posterior-runpaths",
        str(case_dir / "realization-*" / "iter-4"),
        "--seed",
        str(SEED),
        "--expected",
        "100",
        "--misfit-manifest",
        str(pack.out / "misfit/manifest.json"),
    ]


def test_truth_reaches_the_compensation_check_only_when_the_prior_check_has_it(pack):
    for name in ("prior_posterior", "compensation_check"):
        assert "--truth" not in step(pack, "post", name)
    qc = with_qc(pack, truth=pack.folder / "truth.txt")
    for name in ("prior_posterior", "compensation_check"):
        argv = step(pack, "post", name, qc)
        assert argv[argv.index("--truth") + 1] == str(pack.folder / "truth.txt")


# ---- the optional held-out score ----


def test_heldout_is_skipped_with_a_reason_when_no_split_is_set(pack):
    assert "heldout_score" not in names(pack.chain.steps("post", pack.qc, pack.out))
    reasons = pack.chain.skipped("post", pack.qc)
    assert list(reasons) == ["heldout_score"]
    assert "heldout_split" in reasons["heldout_score"]
    assert pack.chain.skipped("setup", pack.qc) == {}


def test_heldout_runs_last_when_a_split_is_set(tmp_path):
    pack = make_pack(tmp_path, "heldout_split=output/drogon_ahm/heldout/split.json")
    assert names(pack.chain.steps("post", pack.qc, pack.out))[-1] == "heldout_score"
    assert step(pack, "post", "heldout_score") == [
        str(HM / "heldout_score.py"),
        "--split",
        str(pack.folder / "output/drogon_ahm/heldout/split.json"),
        "--out",
        str(pack.out / "heldout"),
    ]
    assert pack.chain.skipped("post", pack.qc) == {}


# ---- the preflight gate, with the real script ----


def test_preflight_refuses_a_run_without_a_screening_record(pack):
    result = run_step(step(pack, "setup", "ahm_preflight"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "AHM-PRE-NO-SCREENING" in result.stdout
    assert "PREFLIGHT refused" in result.stdout


def test_preflight_passes_with_a_valid_record_and_the_default_100_members(pack, case):
    write_screening(case, pack)
    result = run_step(step(pack, "setup", "ahm_preflight"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PREFLIGHT ready: 1 sampled parameters, 100 members" in result.stdout
    assert "AHM-PRE-MEMBERS" not in result.stdout
    assert (
        f"ert es_mda ahm.ert --target-ensemble iter-%d --weights {WEIGHTS} --disable-monitoring"
        in result.stdout
    )


def test_preflight_refuses_a_screening_record_of_other_observations(pack, case):
    case.prior = pack.prior / "ert/prior.dist"
    result = integrity.run_screen(case, output=str(pack.screening.parent.relative_to(case.root)))
    assert result.returncode == 0, result.stderr
    result = run_step(step(pack, "setup", "ahm_preflight"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR AHM-PRE-OBS: OBS_CONFIG names" in result.stdout
    assert "screening names ['O1', 'O2']" in result.stdout


def test_preflight_reads_the_gen_kw_file_through_the_prior_model_include(pack, case):
    write_screening(case, pack)
    (pack.prior / "ert/prior.dist").write_text("A UNIFORM 0 2\n")
    result = run_step(step(pack, "setup", "ahm_preflight"))
    assert result.returncode == 1
    assert "AHM-PRE-PRIOR-CHANGED" in result.stdout


def test_preflight_refuses_a_small_ensemble_until_the_reason_is_given(tmp_path, case):
    refused = make_pack(tmp_path / "plain", "num_realizations=16")
    case.root = tmp_path / "plain"
    write_screening(case, refused)
    result = run_step(step(refused, "setup", "ahm_preflight"))
    assert result.returncode == 1 and "AHM-PRE-MEMBERS" in result.stdout

    allowed = make_pack(tmp_path / "waived", "num_realizations=16", "allow_members_reason=quick look")
    case.root = tmp_path / "waived"
    write_screening(case, allowed)
    result = run_step(step(allowed, "setup", "ahm_preflight"))
    assert result.returncode == 0, result.stdout
    assert "16 selected members, below 100, allowed: quick look" in result.stdout


# ---- run(): the whole stage on a synthetic case ----


def test_setup_chain_records_the_refusal_and_exits_1(tmp_path):
    pack = make_pack(tmp_path, *SMALL, "allow_members_reason=quick look")
    write_ensemble(pack, 0, 1)
    codes = pack.chain.run("setup", pack.qc, pack.out)
    assert codes["ahm_preflight"] == 1
    assert (pack.out / "ahm_preflight.txt").read_text().count("AHM-PRE-NO-SCREENING") >= 1
    assert "exit=1" in (pack.out / "ahm_preflight.txt").read_text()
    assert pack.chain.main(["setup", "--out", str(tmp_path / "again"), "--qc", str(pack.qc_file)]) == 1
    summary = json.loads((pack.out / "summary.json").read_text())
    assert summary == {"stage": "setup", "exit_codes": codes, "skipped": {}}


def test_setup_chain_passes_with_a_record_and_shows_the_waiver(tmp_path, case):
    pack = make_pack(tmp_path, *SMALL, "allow_members_reason=quick look")
    write_ensemble(pack, 0, 1)
    write_screening(case, pack)
    codes = pack.chain.run("setup", pack.qc, pack.out)
    assert codes == {"prior_qc": 0, "obs_qc": 0, "check_ert_config": 0, "ahm_preflight": 0}, codes
    step_file = (pack.out / "ahm_preflight.txt").read_text()
    assert "--allow-members quick look" in step_file
    assert "allowed: quick look" in step_file
    assert pack.chain.main(["setup", "--out", str(tmp_path / "again"), "--qc", str(pack.qc_file)]) == 0


def read_summary(pack):
    return json.loads((pack.out / "summary.json").read_text())


def test_post_chain_runs_every_step_and_records_the_skipped_heldout(tmp_path):
    pack = make_pack(tmp_path, *SMALL, dist=POST_DIST)
    full_case(pack, 8)
    codes = pack.chain.run("post", pack.qc, pack.out)
    assert codes == dict.fromkeys(names(pack.chain.steps("post", pack.qc, pack.out)), 0), codes
    summary = read_summary(pack)
    assert summary["exit_codes"] == codes
    assert list(summary["skipped"]) == ["heldout_score"]
    assert "skipped" in (pack.out / "heldout_score.txt").read_text()
    assert not (pack.out / "heldout").exists()
    coverage = (pack.out / "coverage.txt").read_text()
    assert "COVERAGE [posterior]" in coverage and "predictive" in coverage
    labels = {line.split(",")[0] for line in (pack.out / "misfit/residuals.csv").read_text().splitlines()[1:]}
    assert labels == {"prior", "posterior"}
    compensation = (pack.out / "compensation_check.txt").read_text()
    assert "COMPENSATION [run-1]" in compensation and "1 of 3 seeds" in compensation
    assert "one sample, not a claim" in compensation
    assert pack.chain.main(["post", "--out", str(tmp_path / "again"), "--qc", str(pack.qc_file)]) == 0


def test_post_chain_scores_the_heldout_split_when_one_is_set(tmp_path, bundle):
    heldout_tests.save_bundle(bundle)
    pack = make_pack(tmp_path, *SMALL, f"heldout_split={bundle['split']}", dist=POST_DIST)
    full_case(pack, 8)
    codes = pack.chain.run("post", pack.qc, pack.out)
    assert codes["heldout_score"] == 0, (codes, (pack.out / "heldout_score.txt").read_text())
    assert set(codes.values()) == {0}
    assert read_summary(pack)["skipped"] == {}
    report = json.loads((pack.out / "heldout/heldout.json").read_text())
    assert report["schema"] == "ahm-heldout-report/1"
    assert "cannot certify absence of historical training leakage" in report["limits"]


def test_post_chain_exit_code_follows_a_failing_new_step(tmp_path):
    pack = make_pack(tmp_path, *SMALL, dist=POST_DIST)
    full_case(pack, 8)
    (pack.case_dir / "realization-3/iter-4/parameters.txt").unlink()
    codes = pack.chain.run("post", pack.qc, pack.out)
    assert codes["compensation_check"] == 1
    assert pack.chain.main(["post", "--out", str(tmp_path / "again"), "--qc", str(pack.qc_file)]) == 1


# ---- README and memo ----


def rendered(pack, name):
    return (pack.folder / name).read_text(encoding="utf-8")


def test_readme_names_the_script_of_every_step(tmp_path):
    pack = make_pack(tmp_path, "heldout_split=output/drogon_ahm/heldout/split.json")
    readme = rendered(pack, "README.md")
    for stage in ("setup", "post"):
        for name, argv in pack.chain.steps(stage, pack.qc, pack.out):
            assert Path(argv[0]).name in readme, name
    assert "screening" in readme and "heldout_split" in readme and "random_seed" in readme
    assert "MIN_SEEDS" in readme and "3 seeds" in readme


def test_memo_cites_the_file_of_every_step_and_the_limits(tmp_path):
    pack = make_pack(tmp_path, "heldout_split=output/drogon_ahm/heldout/split.json")
    memo = rendered(pack, "decision_memo.md")
    for stage in ("setup", "post"):
        for name, _ in pack.chain.steps(stage, pack.qc, pack.out):
            assert f"{name}.txt" in memo, name
    assert "screening.json" in memo and "AHM-PRE" in memo
    assert "predictive" in memo and "never the noise-biased" in memo
    assert "MIN_SEEDS" in memo and "diagnostic only" in memo
    assert "heldout/heldout.json" in memo
    assert "current files cannot prove the held-out data stayed unused during the run" in memo
    assert "{{" not in memo
