"""ahm_overview.py on a synthetic linear-Gaussian history match (N = 40, three parameters, no res2df)."""

import contextlib
import importlib.util
import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).parents[1] / "skills/history-match-qc/scripts/ahm_overview.py"
spec = importlib.util.spec_from_file_location("ahm_overview_t", SCRIPT)
ahm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ahm)

N = 40
DATES = [f"2020-{m:02d}-01" for m in range(1, 11)]
DIST = {"A_PERM": "LOGUNIF 0.1 10", "B_KRW_EXP": "UNIFORM 1 3", "C_MULTFLT": "LOGUNIF 0.01 1"}
TRUTH = {"A_PERM": 0.6, "B_KRW_EXP": 2.0}


def wopr(a, b):
    return 10 + 5 * a


def wbhp(a, b):
    return 50 + 4 * b


def prior_scaled(seed: int = 0) -> pd.DataFrame:
    """Sampling scale: log10 for A and C, native for B."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "A_PERM": rng.uniform(-1, 1, N),
            "B_KRW_EXP": rng.uniform(1, 3, N),
            "C_MULTFLT": rng.uniform(-2, 0, N),
        },
        index=range(N),
    )


def default_posterior(prior: pd.DataFrame) -> pd.DataFrame:
    post = prior.copy()
    for name, value in TRUTH.items():
        post[name] = value + 0.3 * (prior[name] - prior[name].mean())
    return post


def native(scaled: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=scaled.index)
    for column, name in zip(scaled.columns, names, strict=True):
        log = DIST[column].startswith("LOG")
        out[f"GRP:{name}"] = 10 ** scaled[column] if log else scaled[column]
    out.insert(0, "REAL", scaled.index)
    return out


def residual_rows(label, params, observed, models, noise=None):
    rows = []
    for real, p in params.iterrows():
        for key, model in models.items():
            for i, date in enumerate(DATES):
                obs = f"{key.replace(':', '_')}_{i}"
                sim = model(p["A_PERM"], p["B_KRW_EXP"]) + (noise(real, i) if noise else 0.0)
                o = observed[key][i]
                rows.append([label, real, obs, key, date, o, 1.0, sim, "exact", (sim - o) / 1.0])
    return rows


def write_misfit(folder: Path, rows: list, findings=None, labels=("prior", "posterior")):
    folder.mkdir(parents=True)
    columns = [
        "ENSEMBLE",
        "REAL",
        "OBS",
        "KEY",
        "DATE",
        "OBSERVED",
        "ERROR",
        "SIMULATED",
        "STATUS",
        "RESIDUAL",
    ]
    pd.DataFrame(rows, columns=columns).to_csv(folder / "residuals.csv", index=False)
    manifest = {
        "ensembles": {label: {"realizations_used": list(range(N))} for label in labels},
        "trust": {label: {"level": "not limited by ensemble size", "realizations": N} for label in labels},
        "misfit_by_family": {},
        "findings": findings or [],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))


def make_case(
    tmp: Path,
    names=("A_PERM", "B_KRW_EXP", "C_MULTFLT"),
    models=None,
    observed=None,
    posterior=None,
    post_noise=None,
    findings=None,
) -> list[str]:
    models = models or {"WOPR:P1": wopr, "WBHP:P2": wbhp}
    truth_a = TRUTH["A_PERM"]
    observed = observed or {
        k: [m(truth_a, TRUTH["B_KRW_EXP"]) + 0.05 * (i % 3) for i in range(10)] for k, m in models.items()
    }
    prior = prior_scaled()
    post = (posterior or default_posterior)(prior)
    rng = np.random.default_rng(1)
    noise_table = rng.normal(0, 0.3, (N, 10))
    rows = residual_rows("prior", prior, observed, models, lambda r, i: noise_table[r, i])
    rows += residual_rows("posterior", post, observed, models, post_noise or (lambda r, i: noise_table[r, i]))
    write_misfit(tmp / "misfit", rows, findings)
    dist = tmp / "params.dist"
    dist.write_text("".join(f"{n} {DIST[c]}\n" for c, n in zip(DIST, names, strict=True)))
    native(prior, list(names)).to_csv(tmp / "prior.csv", index=False)
    native(post, list(names)).to_csv(tmp / "post.csv", index=False)
    (tmp / "truth.txt").write_text(
        "".join(
            f"GRP:{n} {10 ** TRUTH[c] if c == 'A_PERM' else TRUTH[c]}\n"
            for c, n in zip(DIST, names, strict=True)
            if c in TRUTH
        )
    )
    return [
        "--misfit-dir", str(tmp / "misfit"), "--dist", str(dist),
        "--prior-params-csv", str(tmp / "prior.csv"), "--posterior-params-csv", str(tmp / "post.csv"),
    ]  # fmt: skip


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = ahm.main(argv)
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def overview(tmp: Path, argv: list[str], name: str = "out") -> dict:
    code, out, err = run([*argv, "--outdir", str(tmp / name)])
    assert code == 0, err
    return json.loads((tmp / name / "overview.json").read_text())


def checks(doc: dict) -> dict:
    return {c["id"]: c for c in doc["checks"] if c["source"] == "ahm_overview"}


def test_influence_driver_woodbury_and_dfs(tmp_path):
    doc = overview(tmp_path, make_case(tmp_path))
    params = {r["name"]: r for r in doc["parameters"]}
    assert params["A_PERM"]["driver_group"] == "WOPR"
    assert params["B_KRW_EXP"]["driver_group"] == "WBHP"
    assert params["A_PERM"]["linear_update"] > 0.5
    assert 0 < doc["influence"]["dfs"] <= doc["influence"]["m"]
    rng = np.random.default_rng(3)
    a = rng.normal(size=(N, 3))
    d = rng.normal(size=(N, 25)) * 2
    k, s = ahm.kalman_gain(a - a.mean(0), d - d.mean(0))
    a, d = a - a.mean(0), d - d.mean(0)
    c_md, c_dd = a.T @ d / (N - 1), d.T @ d / (N - 1)
    direct = c_md @ np.linalg.inv(c_dd + np.eye(25))
    assert np.allclose(k, direct)
    assert np.allclose(s, np.diag(c_dd @ np.linalg.inv(c_dd + np.eye(25))))
    assert ((s > 0) & (s <= 1)).all()


def test_opposite_innovations_conflict(tmp_path):
    models = {"WOPR:P1": wopr, "WBHP:P2": lambda a, b: 50 + 4 * a}
    observed = {"WOPR:P1": [10 + 5 * 0.8] * 10, "WBHP:P2": [50 + 4 * -0.8] * 10}
    doc = overview(tmp_path, make_case(tmp_path, models=models, observed=observed))
    family = [c for c in doc["conflicts"]["pairs"] if c["level"] == "family"]
    assert family and family[0]["cosine"] < -0.5
    assert {family[0]["a"], family[0]["b"]} == {"WBHP", "WOPR"}
    assert family[0]["params"][0]["name"] == "A_PERM"
    assert checks(doc)["HMQC-OBS-CONFLICT"]["fired"]


def test_conflict_needs_enough_scalar_parameters(tmp_path, monkeypatch):
    monkeypatch.setattr(ahm, "MIN_CONFLICT_PARAMS", 4)
    models = {"WOPR:P1": wopr, "WBHP:P2": lambda a, b: 50 + 4 * a}
    observed = {"WOPR:P1": [10 + 5 * 0.8] * 10, "WBHP:P2": [50 + 4 * -0.8] * 10}
    doc = overview(tmp_path, make_case(tmp_path, models=models, observed=observed))
    assert doc["conflicts"]["pairs"]
    assert not checks(doc)["HMQC-OBS-CONFLICT"]["fired"]
    assert "fewer than 4 scalar parameters" in checks(doc)["HMQC-OBS-CONFLICT"]["limiter"]


def test_spurious_update_names_the_parameter(tmp_path):
    def posterior(prior):
        post = default_posterior(prior)
        post["C_MULTFLT"] = prior["C_MULTFLT"] + 2 * prior["C_MULTFLT"].std(ddof=1)
        return post

    doc = overview(tmp_path, make_case(tmp_path, posterior=posterior))
    spurious = checks(doc)["HMQC-SPURIOUS-UPDATE"]
    assert spurious["fired"]
    assert [e.split()[0] for e in spurious["examples"]] == ["C_MULTFLT"]
    assert spurious["numbers"]["localization_threshold_3_over_sqrt_n"] == pytest.approx(
        3 / math.sqrt(N), rel=1e-5
    )
    params = {r["name"]: r for r in doc["parameters"]}
    assert params["C_MULTFLT"]["shift"] == pytest.approx(2.0, rel=1e-5)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("PORO_MULT", ("static", "poro")),
        ("MULTFLT_F1", ("dynamic", "fault")),
        ("MULTZ_X", ("dynamic", "multiplier")),
        ("KRW_EXP", ("dynamic", "relperm")),
        ("OWC_FLANK", ("dynamic", "contacts")),
        ("PERM_VALYSAR", ("static", "perm")),
        ("KVKH_MULT", ("static", "kvkh")),
        ("AQ_STRENGTH", ("dynamic", "aquifer")),
        ("B_KRW_EXP", ("dynamic", "relperm")),
        ("RELPERM_INT_WO", ("dynamic", "relperm")),
        ("FWL_CENTRAL", ("dynamic", "contacts")),
        ("GOC_NORTH_HORST", ("dynamic", "contacts")),
        ("FAULT_SEAL_SCALING", ("dynamic", "fault")),
        ("MULT_R1_VALYSAR_THERYS", ("dynamic", "multiplier")),
        ("KVKH_CHANNEL", ("static", "kvkh")),
        ("VALYSAR_APS_PROB_CHANNEL", ("static", "facies")),
        ("aps_Valysar_GRF1", ("static", "facies")),
        ("DROGON:XYZ", ("unclassified", "unclassified")),
    ],
)
def test_default_classification(name, expected):
    assert ahm.classify(name, list(ahm.DEFAULT_CLASSES)) == expected


def test_unclassified_fires_and_override_wins(tmp_path):
    argv = make_case(tmp_path, names=("A_PERM", "B_KRW_EXP", "XYZ"))
    doc = overview(tmp_path, argv)
    assert checks(doc)["HMQC-PARAM-UNCLASSIFIED"]["fired"]
    assert checks(doc)["HMQC-PARAM-UNCLASSIFIED"]["examples"] == ["XYZ"]
    overrides = ["XYZ=static:other", "PERM=dynamic:x"]
    doc = overview(tmp_path, [*argv, *(f"--param-class={rule}" for rule in overrides)], "o2")
    params = {r["name"]: (r["class"], r["category"]) for r in doc["parameters"]}
    assert params == {
        "A_PERM": ("dynamic", "x"),
        "B_KRW_EXP": ("dynamic", "relperm"),
        "XYZ": ("static", "other"),
    }
    assert not checks(doc)["HMQC-PARAM-UNCLASSIFIED"]["fired"]
    assert run([*argv, "--param-class", "XYZ=other", "--outdir", str(tmp_path / "x")])[0] == 2


def test_prior_predictive_uncovered(tmp_path):
    observed = {"WOPR:P1": [10 + 5 * 5.0] * 10, "WBHP:P2": [58.0] * 10}
    doc = overview(tmp_path, make_case(tmp_path, observed=observed))
    pp = doc["observations"]["prior_predictive"]
    assert pp["covered_share"] == pytest.approx(0.5)
    assert pp["n_uncovered"] == 10
    assert all(u["KEY"] == "WOPR:P1" for u in pp["uncovered"])
    assert checks(doc)["HMQC-PRIOR-PREDICTIVE"]["fired"]
    assert "WOPR" in checks(doc)["HMQC-PRIOR-PREDICTIVE"]["numbers"]["families_below"]


def test_noise_correlated_and_truth_run(tmp_path):
    base = make_case(tmp_path / "a")
    doc = overview(tmp_path, base, "plain")
    assert not checks(doc)["HMQC-NOISE-CORRELATED"]["fired"]
    assert not checks(doc)["HMQC-NOISE-TRUTH"]["fired"]
    assert checks(doc)["HMQC-NOISE-TRUTH"]["limiter"] == "no truth run"

    argv = make_case(tmp_path / "b", post_noise=lambda r, i: -2.0 if i < 5 else 2.0)
    rng = np.random.default_rng(5)
    rows = [
        ["truth", 0, f"WOPR_P1_{i}", "WOPR:P1", DATES[i % 10], 0.0, 1.0, 0.0, "exact", float(r)]
        for i, r in enumerate(rng.normal(0, 3, 40))
    ]
    write_misfit(tmp_path / "truthrun", rows, labels=("truth",))
    doc = overview(tmp_path, [*argv, "--truth-misfit-dir", str(tmp_path / "truthrun")], "noisy")
    corr = checks(doc)["HMQC-NOISE-CORRELATED"]
    assert corr["fired"]
    assert [e.split()[0] for e in corr["examples"]] == ["WBHP:P2", "WOPR:P1"]
    assert checks(doc)["HMQC-NOISE-TRUTH"]["fired"]
    assert doc["noise"]["truth_run"]["families"][0]["verdict"] == "above band"


def test_forecast_closure_and_run_attribution(tmp_path):
    argv = make_case(tmp_path)
    prior = pd.read_csv(tmp_path / "prior.csv")
    post = pd.read_csv(tmp_path / "post.csv")
    for label, table in (("fp", prior), ("fq", post)):
        values = 2 * np.log10(table["GRP:A_PERM"])
        pd.DataFrame({"REAL": table["REAL"], "AS_OF": "2030-01-01", "KEY": "FOPT", "VALUE": values}).to_csv(
            tmp_path / f"{label}.csv", index=False
        )
    pd.DataFrame({"CASE": list(ahm.CASES), "KEY": "FOPT", "VALUE": [0.0, 2.0, 1.0, 4.0]}).to_csv(
        tmp_path / "attr.csv", index=False
    )
    argv += [
        "--forecast-prior", str(tmp_path / "fp.csv"), "--forecast-posterior", str(tmp_path / "fq.csv"),
        "--forecast-truth", "FOPT=1.2", "--attribution-csv", str(tmp_path / "attr.csv"),
    ]  # fmt: skip
    doc = overview(tmp_path, argv)
    item = doc["forecast"]["items"][0]
    assert item["id"] == "FOPT@2030-01-01"
    sa = item["statistical_attribution"]
    assert sa["closure"] == pytest.approx(1.0, abs=0.15)
    assert abs(sa["by_class"]["static"]) > 5 * abs(sa["by_class"]["dynamic"])
    assert sa["r2_linear_prior"] == pytest.approx(1.0)
    assert item["truth"] == 1.2
    attribution = doc["attribution"]["keys"][0]
    assert attribution["interaction"] == 1.0
    assert attribution["total"] == 4.0
    assert not checks(doc)["HMQC-ATTRIBUTION-NONLINEAR"]["fired"]
    doc = overview(tmp_path, [*argv, "--interaction-share", "0.2"], "o2")
    assert checks(doc)["HMQC-ATTRIBUTION-NONLINEAR"]["numbers"]["interaction_keys"] == ["FOPT"]


def test_forecast_refuses_an_unmatched_identity(tmp_path):
    argv = make_case(tmp_path)
    prior = pd.read_csv(tmp_path / "prior.csv")
    post = pd.read_csv(tmp_path / "post.csv")
    pd.DataFrame(
        {"REAL": prior["REAL"], "AS_OF": "2030-01-01", "KEY": "FOPT", "VALUE": prior["GRP:A_PERM"]}
    ).to_csv(tmp_path / "fp.csv", index=False)
    pd.DataFrame(
        {"REAL": post["REAL"], "AS_OF": "2031-01-01", "KEY": "FOPT", "VALUE": post["GRP:A_PERM"]}
    ).to_csv(tmp_path / "fq.csv", index=False)
    code, _out, err = run(
        [
            *argv,
            "--forecast-prior",
            str(tmp_path / "fp.csv"),
            "--forecast-posterior",
            str(tmp_path / "fq.csv"),
            "--outdir",
            str(tmp_path / "mismatch"),
        ]
    )
    assert code == 1
    assert "FOPT@2030-01-01" in err
    assert "FOPT@2031-01-01" in err
    assert not (tmp_path / "mismatch" / "overview.json").exists()


def test_write_attribution(tmp_path):
    argv = make_case(tmp_path)
    code, out, err = run([*argv, "--write-attribution", str(tmp_path / "attr")])
    assert code == 0, err
    prior = pd.read_csv(tmp_path / "prior.csv")
    post = pd.read_csv(tmp_path / "post.csv")
    base_a = 10 ** np.log10(prior["GRP:A_PERM"]).mean()
    post_a = 10 ** np.log10(post["GRP:A_PERM"]).mean()
    post_b = post["GRP:B_KRW_EXP"].mean()
    cases = {}
    for case in ahm.CASES:
        lines = (tmp_path / "attr" / case / "parameters.txt").read_text().split("\n")
        cases[case] = {line.split()[0]: float(line.split()[1]) for line in lines if line}
        assert list(cases[case]) == ["GRP:A_PERM", "GRP:B_KRW_EXP", "GRP:C_MULTFLT"]
    assert cases["base"]["GRP:A_PERM"] == pytest.approx(base_a, rel=1e-7)
    assert cases["static"]["GRP:A_PERM"] == pytest.approx(post_a, rel=1e-7)
    assert cases["static"]["GRP:B_KRW_EXP"] == cases["base"]["GRP:B_KRW_EXP"]
    assert cases["dynamic"]["GRP:B_KRW_EXP"] == pytest.approx(post_b, rel=1e-7)
    assert cases["dynamic"]["GRP:A_PERM"] == cases["base"]["GRP:A_PERM"]
    assert cases["all"]["GRP:A_PERM"] == cases["static"]["GRP:A_PERM"]
    plan = json.loads((tmp_path / "attr" / "attribution_plan.json").read_text())
    assert plan["parameters"]["GRP:C_MULTFLT"]["class"] == "dynamic"


def test_deterministic_output(tmp_path):
    argv = make_case(tmp_path, findings=[{"finding": "b", "detail": "x"}])
    overview(tmp_path, argv, "one")
    overview(tmp_path, argv, "two")
    one = (tmp_path / "one" / "overview.json").read_bytes()
    assert one == (tmp_path / "two" / "overview.json").read_bytes()
    assert ahm.P10_P90.encode() in one
    for name in (
        "overview.md",
        "parameters.csv",
        "observation_groups.csv",
        "observations_influence.csv",
        "conflicts.csv",
    ):
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()
    assert len((tmp_path / "one" / "overview.md").read_text().splitlines()) <= 75


def test_inconsistent_input_fails(tmp_path):
    argv = make_case(tmp_path)
    post = pd.read_csv(tmp_path / "post.csv")
    post[post["REAL"] != 7].to_csv(tmp_path / "post.csv", index=False)
    code, _, err = run([*argv, "--outdir", str(tmp_path / "out")])
    assert code == 1
    assert "missing [7]" in err
    assert not (tmp_path / "out").exists()
    argv[1] = str(tmp_path / "nowhere")
    assert run([*argv, "--outdir", str(tmp_path / "out")])[0] != 0
    assert run(argv[:4] + ["--outdir", str(tmp_path / "out")])[0] == 2


def test_manifest_findings_are_copied(tmp_path):
    findings = [
        {
            "code": "HMQC-EARLY-SPREAD-LOSS",
            "finding": "response spread collapses early",
            "detail": "d",
            "ensemble": "u",
        },
        {"finding": "observation out of reach", "detail": "e", "ensemble": "prior", "examples": ["OBS_1"]},
    ]
    doc = overview(tmp_path, make_case(tmp_path, findings=findings))
    copied = {c["id"]: c for c in doc["checks"] if c["source"] == "misfit_report"}
    assert copied["HMQC-EARLY-SPREAD-LOSS"]["fired"]
    assert copied["HMQC-EARLY-SPREAD-LOSS"]["numbers"]["detail"] == "d"
    assert copied["MISFIT:OBSERVATION-OUT-OF-REACH"]["examples"] == ["OBS_1"]
    assert all(c["fired"] in (True, False) for c in doc["checks"])
    assert {c["id"] for c in doc["checks"] if c["source"] == "ahm_overview"} == set(ahm.RATIONALE)


def test_runpaths_drop_log10_and_fail_on_extra(tmp_path):
    argv = make_case(tmp_path)
    for label, csv_file in (("iter-0", "prior.csv"), ("iter-4", "post.csv")):
        for _, row in pd.read_csv(tmp_path / csv_file).iterrows():
            folder = tmp_path / "scratch" / f"realization-{int(row['REAL'])}" / label
            folder.mkdir(parents=True)
            lines = [f"{k} {v!r}" for k, v in row.items() if k != "REAL"]
            lines.append(f"LOG10_GRP:A_PERM {math.log10(row['GRP:A_PERM'])!r}")
            (folder / "parameters.txt").write_text("\n".join(lines) + "\n")
    runpaths = argv[:4] + [
        "--prior-runpaths", str(tmp_path / "scratch/realization-*/iter-0"),
        "--posterior-runpaths", str(tmp_path / "scratch/realization-*/iter-4"),
    ]  # fmt: skip
    from_csv = overview(tmp_path, argv, "csv")
    from_runpaths = overview(tmp_path, runpaths, "rp")
    assert from_csv["parameters"] == from_runpaths["parameters"]
    (tmp_path / "scratch" / f"realization-{N}" / "iter-4").mkdir(parents=True)
    code, _, err = run([*runpaths, "--outdir", str(tmp_path / "bad")])
    assert code == 1
    assert "not in the expected set" in err


@pytest.mark.parametrize(
    ("key", "family"), [("WOPRL__1:A3", "WOPRL"), ("WBHP:A1", "WBHP"), ("GEN:RFT_R_A2", "GEN:RFT")]
)
def test_obs_family_layer_keys(key, family):
    assert ahm.obs_family(key) == family


def test_partial_misfit_folder_is_refused(tmp_path):
    argv = make_case(tmp_path)
    file = tmp_path / "misfit" / "manifest.json"
    manifest = json.loads(file.read_text())
    manifest["issues"] = [{"ensemble": "posterior", "realization": 3, "issue": "missing CASE-3.UNSMRY"}]
    file.write_text(json.dumps(manifest))
    code, _, err = run([*argv, "--outdir", str(tmp_path / "out")])
    assert code == 1
    assert "partial" in err and "realization 3" in err
    assert not (tmp_path / "out").exists()


def test_prior_predictive_needs_a_prior_spread(tmp_path):
    res = pd.DataFrame(
        {
            "ENSEMBLE": ["prior", "posterior"],
            "REAL": [0, 0],
            "OBS": ["O1", "O1"],
            "KEY": ["WOPR:P1", "WOPR:P1"],
            "DATE": ["2020-01-01", "2020-01-01"],
            "OBSERVED": [10.0, 10.0],
            "ERROR": [1.0, 1.0],
            "SIMULATED": [30.0, 10.5],
            "RESIDUAL": [20.0, 0.5],
        }
    )
    table = ahm.obs_table(res, "prior", "posterior", 3.0)
    assert math.isnan(table.loc["O1", "z_pp"])
