"""Posterior link refusal, overrides and numerical percentile subsets."""

import json

import pytest

yaml = pytest.importorskip("yaml")
from test_everest_config import load  # noqa: E402


def csv_file(tmp_path, name, values):
    path = tmp_path / name
    path.write_text(
        "realization,param,response\n" + "".join(f"{i},{v},{10 * (i + 1)}\n" for i, v in enumerate(values))
    )
    return path


def test_spread_collapse_refused_and_override_recorded(tmp_path):
    link = load("ahm_posterior_link")
    prior = csv_file(tmp_path, "prior.csv", [0, 10, 20, 30, 40])
    post = csv_file(tmp_path, "posterior.csv", [0, 0.1, 0.2, 0.3, 0.4])
    args = dict(
        posterior=post,
        prior=prior,
        response="response",
        data_source="members/<REALIZATION_ID>/parameters.json",
    )
    with pytest.raises(ValueError, match="refuse posterior link.*std/prior std"):
        link.prepare(**args)
    snippet, receipt = link.prepare(**args, override=True)
    assert receipt["spread_ratios"]["param"] == pytest.approx(0.01)
    assert receipt["override"] and receipt["warnings"]
    assert snippet["model"]["realizations"] == [0, 2, 4]


def test_subset_numerical_convention_and_install_snippets(tmp_path):
    link = load("ahm_posterior_link")
    post = csv_file(tmp_path, "posterior.csv", [0, 10, 20, 30, 40])
    snippet, receipt = link.prepare(
        posterior=post,
        prior=post,
        response="response",
        data_source="data/<REALIZATION_ID>/params.json",
        template_source="templates/<REALIZATION_ID>/params.tmpl",
    )
    assert snippet["model"]["realizations"] == [0, 2, 4]
    assert receipt["subset"]["P10"] == {"value": 14, "realization": 0}
    assert receipt["subset"]["P90"] == {"value": 46, "realization": 4}
    assert "P10 = 10th percentile low value" in receipt["percentile_convention"]
    assert "<REALIZATION_ID>" in snippet["install_templates"][0]["source"]
    assert "<REALIZATION_ID>" in snippet["install_data"][0]["source"]


def test_qc_collapse_and_cli_no_output_on_refusal(tmp_path, capsys):
    link = load("ahm_posterior_link")
    post = csv_file(tmp_path, "posterior.csv", [1, 2, 3])
    qc = tmp_path / "qc.json"
    qc.write_text(json.dumps({"spread_collapsed": True}))
    out = tmp_path / "snippet.yml"
    args = [
        "--posterior",
        str(post),
        "--qc",
        str(qc),
        "--data-source",
        "data/<REALIZATION_ID>/params.json",
        "--out",
        str(out),
    ]
    assert link.main(args) == 1
    assert not out.exists()
    assert "human --override required" in capsys.readouterr().out
    assert link.main(args + ["--override"]) == 0
    assert yaml.safe_load(out.read_text())["model"]["realizations"] == [0, 1, 2]
    assert '"override": true' in capsys.readouterr().out


def test_runpath_contract_and_single_iteration(tmp_path):
    link = load("ahm_posterior_link")
    for member in [0, 2, 7]:
        (tmp_path / f"realization-{member}/iter-1").mkdir(parents=True)
    qc = tmp_path / "qc.json"
    qc.write_text('{"passed": true, "spread_collapsed": false}')
    contract = tmp_path / "contract.yml"
    contract.write_text(f'ahm_posterior_case: "{tmp_path}/realization-<N>/iter-1"\n')
    out = tmp_path / "snippet.yml"
    assert link.main(["--contract", str(contract), "--qc", str(qc), "--out", str(out)]) == 0
    snippet = yaml.safe_load(out.read_text())
    assert snippet["model"]["realizations"] == [0, 2, 7]
    assert "<REALIZATION_ID>/iter-1" in snippet["install_data"][0]["source"]
    (tmp_path / "realization-0/iter-2").mkdir()
    with pytest.raises(ValueError, match="one posterior iteration"):
        link.prepare(runpath=f"{tmp_path}/realization-<N>/iter-<K>", qc=qc)


def test_unchecked_spread_and_invalid_source(tmp_path):
    link = load("ahm_posterior_link")
    post = csv_file(tmp_path, "post.csv", [1, 2])
    with pytest.raises(ValueError, match="unchecked"):
        link.prepare(posterior=post, data_source="data/<REALIZATION_ID>")
    with pytest.raises(ValueError, match="must contain"):
        link.prepare(posterior=post, prior=post, data_source="data/params.json")
