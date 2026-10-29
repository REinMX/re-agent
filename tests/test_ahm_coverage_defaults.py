"""Coverage without the noise bias, and the typed AHM defaults.

The regression data is one drogon-lite ES-MDA posterior (50 members, 315 summary and RFT observations, seed
123456, radius 500 m). The old report quoted 0.45 for it (observations inside the simulated P5-P95). The
predictive and truth-response coverages were 0.85 to 0.88 over 3 seeds.
"""

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

ROOT = Path(__file__).parents[1]
DATA = ROOT / "tests/data/drogon_lite"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cov = load(ROOT / "skills/history-match-qc/scripts/coverage.py", "coverage_t")
defaults = load(ROOT / "skills/history-match-qc/scripts/ahm_defaults.py", "ahm_defaults_t")
lint = load(ROOT / "skills/ert-config-author-lint/scripts/check_ert_config.py", "check_ert_config_cov_t")


def drogon_lite_posterior():
    obs = pd.read_csv(DATA / "coverage_obs.csv")
    sim = pd.read_csv(DATA / "coverage_posterior_sim.csv.gz")[obs["OBS"]].to_numpy()
    return obs, sim


def test_drogon_lite_regression_biased_share_against_predictive():
    obs, sim = drogon_lite_posterior()
    result = cov.coverage(sim, obs["OBSERVED"], obs["ERROR"], obs["TRUTH"])
    assert result.n_obs == 315 and result.n_real == 50
    assert 0.40 <= result.noise_biased <= 0.55  # the number that was reported as overconfidence
    assert result.predictive >= 0.85
    assert result.truth_response >= 0.85
    codes = {f["code"] for f in result.findings}
    assert codes == {"HMQC-COVERAGE-BIASED"}


def test_correct_posterior_is_not_called_overconfident():
    rng = np.random.default_rng(1)
    truth = np.linspace(100, 200, 400)
    error = np.full_like(truth, 10.0)
    observed = truth + rng.normal(0, 10, truth.size)
    sim = truth[None, :] + rng.normal(0, 3, (100, truth.size))  # narrow but centred: a well-matched posterior
    result = cov.coverage(sim, observed, error, truth)
    assert result.noise_biased < 0.5
    assert abs(result.predictive - 0.90) < 0.04
    assert result.truth_response > 0.85


def test_biased_posterior_is_flagged():
    truth = np.linspace(100, 200, 200)
    error = np.full_like(truth, 5.0)
    sim = (truth + 20)[None, :] + np.random.default_rng(2).normal(0, 1, (50, truth.size))
    result = cov.coverage(sim, truth, error, truth)
    codes = {f["code"] for f in result.findings}
    assert {"HMQC-COVERAGE-PREDICTIVE", "HMQC-COVERAGE-TRUTH"} <= codes


def test_predictive_cdf_matches_sampling():
    rng = np.random.default_rng(3)
    sim = rng.normal(0, 1, (40, 1))
    exact = cov.predictive_cdf(sim, np.array([0.7]), np.array([0.5]))[0]
    draws = (sim + rng.normal(0, 0.5, (40, 20000))).ravel()
    assert abs(exact - np.mean(draws <= 0.7)) < 0.01


def test_rejects_bad_errors():
    with pytest.raises(ValueError):
        cov.predictive_cdf(np.zeros((3, 1)), np.zeros(1), np.zeros(1))


def test_cli_on_residuals(tmp_path, capsys):
    obs, sim = drogon_lite_posterior()
    rows = [
        {
            "ENSEMBLE": "posterior",
            "REAL": r,
            "OBS": o.OBS,
            "OBSERVED": o.OBSERVED,
            "ERROR": o.ERROR,
            "SIMULATED": s,
        }
        for r in range(10)
        for o, s in zip(obs.itertuples(), sim[r], strict=True)
    ]
    pd.DataFrame(rows).to_csv(tmp_path / "residuals.csv", index=False)
    obs[["OBS", "TRUTH"]].to_csv(tmp_path / "truth.csv", index=False)
    args = ["--residuals", str(tmp_path / "residuals.csv"), "--truth-response", str(tmp_path / "truth.csv")]
    assert cov.main([*args, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n_real"] == 10 and data["predictive"] > data["noise_biased"]
    assert cov.main([*args, "--ensemble", "prior"]) == 1


def test_radius_guidance_reproduces_drogon_lite():
    wells = [
        (675, 525),
        (225, 675),
        (925, 625),
        (375, 125),
        (1325, 575),
    ]  # drogon-lite cell centres, 50 m cells
    guide = defaults.radius_guidance(wells)
    assert guide.basis is defaults.Basis.INFERRED
    assert math.isclose(guide.median_spacing_m, 403.1, abs_tol=0.1)
    assert abs(guide.radius_m - defaults.DROGON_LITE_RADIUS_M.value) < 10
    assert guide.sweep_m[0] < guide.radius_m < guide.sweep_m[-1]
    with pytest.raises(ValueError):
        defaults.radius_guidance([(0, 0)])


def test_seed_verdict():
    assert not defaults.seed_verdict([1001]).claim_ready
    assert not defaults.seed_verdict([1, 1, 2]).claim_ready
    assert defaults.seed_verdict([1, 2, 3]).claim_ready


def test_defaults_are_typed():
    assert defaults.MEMBERS.value == 100 and defaults.MEMBERS.basis is defaults.Basis.MEASURED
    assert defaults.MIN_SEEDS.value == 3
    assert defaults.RADIUS_PER_WELL_SPACING.basis is defaults.Basis.INFERRED


def test_ahm_lint_default_wants_100_members_and_seeds(tmp_path, capsys):
    config = tmp_path / "c.ert"
    config.write_text("NUM_REALIZATIONS 50\nMIN_REALIZATIONS 80%\nRANDOM_SEED 1001\nUPDATE_LOG_PATH log\n")
    assert lint.main([str(config), "--ahm-only"]) == 0
    out = capsys.readouterr().out
    assert "NUM_REALIZATIONS 50" in out and "below 100" in out
    assert "Run 3 seeds" in out
    config.write_text("NUM_REALIZATIONS 100\nMIN_REALIZATIONS 100\nUPDATE_LOG_PATH log\n")
    assert lint.main([str(config), "--ahm-only"]) == 0
    out = capsys.readouterr().out
    assert "below 100" not in out and "RANDOM_SEED is not set" in out
