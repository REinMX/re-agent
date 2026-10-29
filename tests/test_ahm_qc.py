"""AHM checks: update log, observation weights, GENERAL_OBSERVATION, prior, collapse, ES-MDA lint, contract.

All inputs are synthetic files in tmp_path. Nothing runs ERT or a simulator.
"""

import contextlib
import importlib.util
import io
import json
import math
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
HM = ROOT / "skills/history-match-qc/scripts"
LINT = ROOT / "skills/ert-config-author-lint/scripts"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ulog = load(HM / "update_log_report.py", "update_log_report_t")
obs_qc = load(HM / "obs_qc.py", "obs_qc_t")
prior_qc = load(HM / "prior_qc.py", "prior_qc_t")
collapse = load(HM / "collapse_checks.py", "collapse_checks_t")
lint = load(LINT / "check_ert_config.py", "check_ert_config_t")
doctor = load(ROOT / "tools/doctor.py", "doctor_t")
fixture = load(ROOT / "evals/make_ahm_case.py", "make_ahm_case_t")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run_json(main, *args: str) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main([*args, "--json"])
    text = buffer.getvalue()
    return code, json.loads(text) if text.strip() else {}


# ---------- update_log_report ----------

CSV_HEADER = (
    "observation_key,index,observations,std,obs_error_scaling,scaled_obs_error,"
    "response_mean,response_std,status,missing_realizations\n"
)


def report_header(parent="iter-0", target="iter-1", scaling=1.0, active=3, outliers=1) -> str:
    return (
        f"Parent ensemble: {parent}\nTarget ensemble: {target}\nAlpha: 3.0\nGlobal scaling: {scaling}\n"
        f"Standard cutoff: 1e-06\nActive observations: {active}\n"
        "Deactivated observations: missing respons(es): 0\n"
        "Deactivated observations: ensemble_std < STD_CUTOFF: 0\n"
        f"Deactivated observations: outliers: {outliers}\n"
    )


def make_step(folder: Path, **header) -> Path:
    write(
        folder / "Report.csv",
        CSV_HEADER
        + "FOPR_01,2020-01-01,100,10,1.0,10,90,20,Active,\n"
        + "FOPR_02,2020-02-01,110,10,1.0,10,95,20,Active,\n"
        + "WBHP_I_01,2020-01-01,250,5,1.0,5,250,0.1,Active,\n"
        + 'WWCT_P_18,2020-06-01,0.2,0.04,1.0,0.04,0.01,0.02,"Deactivated, outlier",\n',
    )
    write(folder / "Report.report", report_header(**header))
    return folder


def test_update_log_counts_alpha_and_names(tmp_path):
    make_step(tmp_path / "log/abc")
    code, report = run_json(ulog.main, str(tmp_path / "log"))
    assert code == 0
    (step,) = report["steps"]
    assert step["counts"] == {"active": 3, "outlier": 1, "std_cutoff": 0, "missing_response": 0, "other": 0}
    assert step["alpha"] == 3.0 and step["global_scaling"] == 1.0 and step["std_cutoff"] == 1e-06
    assert [d["key"] for d in step["deactivated"]] == ["WWCT_P_18"]
    assert [r["key"] for r in step["low_ratio_active"]] == ["WBHP_I_01"]  # 0.1 / 5 = 0.02
    assert step["groups"]["FOPR"]["active"] == 2 and step["groups"]["WBHP_I"]["count"] == 1
    assert step["std_error_ratio"]["median"] == pytest.approx(2.0)
    assert step["notes"] == []


def test_update_log_header_mismatch_is_noted(tmp_path):
    make_step(tmp_path / "s", active=5, outliers=2)
    (step,) = ulog.load([tmp_path / "s"])["steps"]
    assert any("header says 2 outlier" in n for n in step["notes"])
    assert any("header says 5 active" in n for n in step["notes"])


def test_update_log_report_text_only_and_column_variants(tmp_path):
    folder = tmp_path / "s"
    write(
        folder / "Report.report",
        report_header(active=1, outliers=0)
        + "┌──┐\n│ Key │ Date │ Value │ Error │ Simulated_Std │ Status │\n├──┤\n"
        + "│ A_1 │ 2020 │ 1.0 │ 0.5 │ 1.0 │ Active │\n"
        + "│ A_2 │ 2020 │ 1.0 │ 0.5 │ 1.0 │ Deactivated, ensemble_std < STD_CUTOFF │\n└──┘\n",
    )
    (step,) = ulog.load([folder / "Report.report"])["steps"]
    assert step["counts"]["active"] == 1 and step["counts"]["std_cutoff"] == 1
    assert step["by_key"]["A_1"]["std_error_ratio"] == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        ("Active", "active"),
        ("Deactivated, outlier", "outlier"),
        ("collapsed", "std_cutoff"),
        ("Deactivated, ensemble_std < STD_CUTOFF", "std_cutoff"),
        ("nan", "missing_response"),
        ("Deactivated, missing response(es)", "missing_response"),
    ],
)
def test_update_log_status_texts_of_ert(status, kind):
    assert ulog.status_class(status) == kind


def test_update_log_unknown_status_is_other(tmp_path):
    write(tmp_path / "s/Report.csv", "key,status,std,response_std\nA,Weird,1,1\n")
    (step,) = ulog.load([tmp_path / "s"])["steps"]
    assert step["counts"]["other"] == 1 and "Weird" in step["notes"][0]


def test_update_log_missing_columns_and_missing_log(tmp_path):
    write(tmp_path / "bad/Report.csv", "observations,std\n1,1\n")
    assert ulog.main([str(tmp_path / "bad")]) == 1
    (tmp_path / "empty").mkdir()
    assert ulog.main([str(tmp_path / "empty")]) == 2
    assert ulog.main([str(tmp_path / "none")]) == 2


@pytest.mark.parametrize(("scalings", "consistent"), [((2.0, 2.0), True), ((4.0, 2.0), False)])
def test_update_log_esmda_chain(tmp_path, scalings, consistent):
    for i, scaling in enumerate(scalings):
        make_step(tmp_path / f"log/{i}", parent=f"iter-{i}", target=f"iter-{i + 1}", scaling=scaling)
    report = ulog.load([tmp_path / "log"])
    assert report["es_mda"]["chain"] is True
    assert report["es_mda"]["consistent"] is consistent
    extra = ulog.join_findings(report, [])
    assert any(f["finding"] == "ES-MDA inflation does not sum to 1" for f in extra) is (not consistent)


def ratio_chain(folder: Path, medians: list[float], names: list[tuple[str, str]] | None = None) -> Path:
    """One ES-MDA step for each median response_std/error. Three active observations with error 1 per step."""
    names = names or [(f"iter-{i}", f"iter-{i + 1}") for i in range(len(medians))]
    for i, (median, (parent, target)) in enumerate(zip(medians, names, strict=True)):
        rows = "".join(
            f"OBS_{k},2020-0{k + 1}-01,1,1,1.0,1,1,{median * factor},Active,\n"
            for k, factor in enumerate((0.5, 1.0, 2.0))
        )
        write(folder / f"{i}/Report.csv", CSV_HEADER + rows)
        write(
            folder / f"{i}/Report.report",
            report_header(parent=parent, target=target, scaling=len(medians), outliers=0),
        )
    return folder


def test_early_spread_loss_not_raised_on_a_disconnected_chain(tmp_path):
    # Unnamed ensembles from two runs. List position 1 is not "after update 1".
    log = ratio_chain(tmp_path / "log", [0.655, 0.1], [("prior_a", "post_a"), ("prior_b", "post_b")])
    report = ulog.load([log])
    assert report["findings"] == []
    assert report["early_spread_unknown"] == ["update prior_a -> post_a", "update prior_b -> post_b"]
    extra = ulog.join_findings(report, [])
    (item,) = [f for f in extra if f["finding"] == "update count unknown"]
    assert item["examples"] == report["early_spread_unknown"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert ulog.main([str(log)]) == 0
    assert "NOTE HMQC-EARLY-SPREAD-LOSS not checked for update prior_a -> post_a" in out.getvalue()


def test_early_spread_loss_not_raised_on_partial_history_without_numbers(tmp_path):
    # The chain is connected, but its origin "es_a" is not known to be the prior.
    log = ratio_chain(tmp_path / "log", [0.5, 0.1], [("es_a", "es_b"), ("es_b", "es_c")])
    report = ulog.load([log])
    assert report["findings"] == []
    assert report["early_spread_unknown"] == ["update es_a -> es_b", "update es_b -> es_c"]


def test_early_spread_loss_follows_a_chain_from_a_numbered_origin(tmp_path):
    log = ratio_chain(tmp_path / "log", [0.655, 0.1], [("iter-0", "es_a"), ("es_a", "es_b")])
    report = ulog.load([log])
    assert report["early_spread_unknown"] == []
    (item,) = report["findings"]
    assert "in ensemble es_a (after update 1)" in item["detail"]
    # A numbered partial history (iter-3 onward) is late and does not fire.
    late = ratio_chain(tmp_path / "late", [0.1], [("iter-3", "iter-4")])
    assert ulog.load([late])["findings"] == []


def test_early_spread_loss_fires_on_drogon_chain(tmp_path):
    log = ratio_chain(tmp_path / "log", [0.655, 0.279, 0.202, 0.157])
    code, report = run_json(ulog.main, str(log))
    assert code == 0
    (item,) = report["findings"]
    assert item["code"] == "HMQC-EARLY-SPREAD-LOSS"
    assert item["detail"].startswith(
        "median response_std/error 0.279 in ensemble iter-1 (after update 1) is below 0.3."
        " Medians by step: 0.655 -> 0.279 -> 0.202 -> 0.157."
    )
    assert "Limiter" in item["detail"] and "before the last iteration" in item["detail"]
    extra = ulog.join_findings(ulog.load([log]), [])
    assert [f.get("code") for f in extra if "code" in f] == ["HMQC-EARLY-SPREAD-LOSS"]


def test_early_spread_loss_after_update_two_and_not_later(tmp_path):
    report = ulog.load([ratio_chain(tmp_path / "a", [0.8, 0.5, 0.25, 0.1])])
    assert "(after update 2)" in report["findings"][0]["detail"]
    # Update 3 is late: the prior and updates 1 and 2 keep their spread.
    assert ulog.load([ratio_chain(tmp_path / "b", [0.8, 0.5, 0.4, 0.1])])["findings"] == []
    # The prior itself (step 0) is not an update.
    assert ulog.load([ratio_chain(tmp_path / "c", [0.1])])["findings"] == []
    report = ulog.load([ratio_chain(tmp_path / "d", [0.655, 0.279])], early_spread_limit=0.25)
    assert report["findings"] == []
    assert ulog.main([str(tmp_path / "d"), "--early-spread-limit", "-1"]) == 2


def test_update_log_separate_runs_are_not_a_chain(tmp_path):
    make_step(tmp_path / "a/1", scaling=4.0)
    make_step(tmp_path / "b/1", scaling=4.0)
    report = ulog.load([tmp_path / "a", tmp_path / "b"])
    assert report["es_mda"]["chain"] is False


def test_join_findings_annotates_named_observations(tmp_path):
    make_step(tmp_path / "s")
    report = ulog.load([tmp_path / "s"])
    found = [{"finding": "observation got clearly worse", "examples": ["WBHP_I_01: 0.1 -> 3.0"]}]
    extra = ulog.join_findings(report, found)
    assert "Active, response_std/error 0.02" == found[0]["update_log"]["WBHP_I_01"]
    kinds = {f["finding"] for f in extra}
    assert {"observation deactivated in the update", "active observation with small ensemble spread"} <= kinds


def test_update_log_groups_keep_the_well_and_gen_data_name(tmp_path):
    write(
        tmp_path / "s/Report.csv",
        CSV_HEADER
        + 'RFT_R_A2,"1, 0",296,2,1.0,2,299,0.2,Active,\n'
        + 'RFT_R_A2,"1, 1",298,2,1.0,2,300,1.0,"Deactivated, outlier",\n'
        + 'RFT_R_A3,"1, 0",288,2,1.0,2,293,0.9,Active,\n'
        + "WBHP_A1_20180401,2018-04-01 00:00:00.000,244,5,1.0,5,238,0.8,Active,\n"
        + "WBHP_A1_20180701,2018-07-01 00:00:00.000,247,5,1.0,5,244,1.4,Active,\n",
    )
    report = ulog.load([tmp_path / "s"])
    (step,) = report["steps"]
    assert {name: g["count"] for name, g in step["groups"].items()} == {
        "RFT_R_A2": 2,
        "RFT_R_A3": 1,
        "WBHP_A1": 2,
    }
    found = [
        {"finding": "observation got clearly worse", "examples": ["RFT_R_A2:1: -1.00 -> -3.00", "RFT_R_A2:0"]}
    ]
    ulog.join_findings(report, found)
    assert found[0]["update_log"] == {
        "RFT_R_A2:1": "Deactivated, outlier, response_std/error 0.5",
        "RFT_R_A2:0": "Active, response_std/error 0.1",
    }


# ---------- obs_qc: weights, zeros, GENERAL_OBSERVATION ----------


def summary_lines(key: str, count: int, value=1.0, error=1.0, start=0) -> str:
    name = key.replace(":", "_")
    return "".join(
        f"SUMMARY_OBSERVATION {name}_{i} {{ VALUE = {value}; ERROR = {error}; DATE = 2020-01-{i + 1 + start:02d};"
        f" KEY = {key}; }};\n"
        for i in range(count)
    )


def messages(report: dict, severity: str | None = None) -> str:
    return "\n".join(p["message"] for p in report["problems"] if severity in (None, p["severity"]))


def test_weight_share_flags_small_group(tmp_path):
    text = (
        summary_lines("FOPR", 18)
        + summary_lines("WWCT:P", 18, value=0.5, error=0.1)
        + summary_lines("WBHP:I", 6)
    )
    code, report = run_json(obs_qc.main, str(write(tmp_path / "w.obs", text)))
    assert code == 0
    groups = report["weights"]["groups"]
    assert groups["WBHP"]["count_share"] == pytest.approx(6 / 42)
    assert groups["WWCT"]["inverse_variance_sum"] == pytest.approx(18 / 0.01)
    assert "group WBHP has 6 of 42" in messages(report, "warning")
    assert any(p.startswith("WBHP") for p in report["proposals"])
    code, report = run_json(obs_qc.main, str(tmp_path / "w.obs"), "--share-min", "0.1")
    assert "group WBHP" not in messages(report)


def test_weight_share_single_group_and_large_group(tmp_path):
    one = write(tmp_path / "one.obs", summary_lines("FOPR", 5))
    assert "group" not in messages(run_json(obs_qc.main, str(one))[1])
    big = write(tmp_path / "big.obs", summary_lines("FOPR", 8) + summary_lines("WBHP:I", 2))
    assert "group FOPR has 8 of 10" in messages(run_json(obs_qc.main, str(big))[1])


def test_zero_values_with_large_abs_error(tmp_path):
    text = summary_lines("WWCT:P", 5, value=0.0, error=0.04) + summary_lines("WWCT:P", 3, value=0.6, start=10)
    report = run_json(obs_qc.main, str(write(tmp_path / "z.obs", text)))[1]
    assert "5 observations of WWCT:P have VALUE 0" in messages(report, "warning")
    small = summary_lines("WWCT:P", 5, value=0.0, error=0.001) + summary_lines(
        "WWCT:P", 3, value=0.6, start=10
    )
    assert "VALUE 0" not in messages(run_json(obs_qc.main, str(write(tmp_path / "s.obs", small)))[1])


def test_share_limits_are_validated(tmp_path):
    good = write(tmp_path / "g.obs", summary_lines("FOPR", 1))
    assert obs_qc.main([str(good), "--share-min", "0.6", "--share-max", "0.5"]) == 2
    assert obs_qc.main([str(good), "--share-max", "1.5"]) == 2


def general(tmp_path: Path, body: str, files: dict[str, str] | None = None) -> tuple[int, dict]:
    for name, text in (files or {}).items():
        write(tmp_path / name, text)
    return run_json(obs_qc.main, str(write(tmp_path / "g.obs", f"GENERAL_OBSERVATION RFT {{ {body} }};\n")))


def test_general_observation_good(tmp_path):
    code, report = general(
        tmp_path,
        "DATA = RFT_A; RESTART = 1; OBS_FILE = rft.txt; INDEX_LIST = 0,2-3;",
        {"rft.txt": "-- value error\n250 2\n252 2\n255 2.5\n"},
    )
    assert code == 0, report["problems"]
    (obs,) = report["general_observations"]
    assert obs["count"] == 3 and obs["data"] == "RFT_A" and obs["index_source"] == "INDEX_LIST"
    assert report["weights"]["groups"]["GEN:RFT"]["count"] == 3


def test_general_observations_group_by_data_family(tmp_path):
    write(tmp_path / "rft.txt", "250 2\n252 2\n255 2\n256 2\n")
    data = ["RFT_R_A2", "RFT_R_A3", "RFT_R_A4", "RFT_R_A5", "RFT_R_A6", "PLT_OP1", "TRACER"]
    text = "".join(
        f"GENERAL_OBSERVATION OBS_{name} {{ DATA = {name}; RESTART = 1; OBS_FILE = rft.txt; }};\n"
        for name in data
    )
    for well in ("A1", "A2", "A3"):
        text += summary_lines(f"WOPR:{well}", 30) + summary_lines(f"WBHP:{well}", 30)
    obs = str(write(tmp_path / "all.obs", text))

    report = run_json(obs_qc.main, obs)[1]
    groups = report["weights"]["groups"]
    assert {name: row["count"] for name, row in groups.items()} == {
        "WOPR": 90,
        "WBHP": 90,
        "GEN:RFT": 20,
        "GEN:PLT": 4,
        "GEN:TRACER": 4,
    }
    assert report["weights"]["keys"]["OBS_RFT_R_A2"]["count"] == 4
    assert messages(report, "warning").count("group GEN:") == 3

    exact = run_json(obs_qc.main, obs, "--gen-group", "exact")[1]["weights"]["groups"]
    assert sorted(name for name in exact if name.startswith("GEN:")) == [
        f"GEN:{name}" for name in sorted(data)
    ]


@pytest.mark.parametrize(
    ("body", "files", "expected"),
    [
        ("DATA = D; RESTART = 1; OBS_FILE = f.txt;", {"f.txt": "1 1\n2 0\n"}, "not larger than 0"),
        ("DATA = D; RESTART = 1; OBS_FILE = f.txt;", {"f.txt": "1 1 2\n"}, "value and error pairs"),
        ("DATA = D; RESTART = 1; OBS_FILE = nope.txt;", {}, "OBS_FILE not found"),
        (
            "DATA = D; RESTART = 1; OBS_FILE = f.txt; INDEX_LIST = 0,1,2;",
            {"f.txt": "1 1\n2 1\n"},
            "3 indices but 2",
        ),
        (
            "DATA = D; RESTART = 1; OBS_FILE = f.txt; INDEX_FILE = i.txt;",
            {"f.txt": "1 1\n", "i.txt": "0\n0\n"},
            "duplicates",
        ),
        (
            "DATA = D; RESTART = 1; OBS_FILE = f.txt; INDEX_FILE = none.txt;",
            {"f.txt": "1 1\n"},
            "INDEX_FILE not found",
        ),
        ("RESTART = 1; VALUE = 1; ERROR = 1;", {}, "missing DATA"),
        ("DATA = D; VALUE = 1; ERROR = 1;", {}, "missing RESTART or DATE"),
        ("DATA = D; RESTART = 1;", {}, "needs OBS_FILE"),
        (
            "DATA = D; RESTART = 1; VALUE = 1; ERROR = 1; OBS_FILE = f.txt;",
            {"f.txt": "1 1\n"},
            "Use one of them",
        ),
        ("DATA = D; RESTART = -1; VALUE = 1; ERROR = 1;", {}, "report step"),
    ],
)
def test_general_observation_errors(tmp_path, body, files, expected):
    code, report = general(tmp_path, body, files)
    assert code == 1
    assert expected in messages(report, "error")


def test_general_observation_inline_and_index_file(tmp_path):
    code, report = general(tmp_path, "DATA = D; DATE = 2020-01-01; VALUE = 5; ERROR = 0.5;")
    assert code == 0 and report["general_observations"][0]["count"] == 1
    code, report = general(
        tmp_path,
        "DATA = D; RESTART = 0; OBS_FILE = f.txt; INDEX_FILE = i.txt;",
        {"f.txt": "1 1\n2 1\n", "i.txt": "4\n7\n"},
    )
    assert code == 0 and report["general_observations"][0]["index_source"] == "INDEX_FILE"


def test_history_and_unknown_blocks_give_guidance(tmp_path):
    text = (
        "HISTORY_OBSERVATION WOPR:P { ERROR = 0.1; ERROR_MODE = REL; };\nRFT_OBSERVATION R { VALUE = 1; };\n"
    )
    code, report = run_json(obs_qc.main, str(write(tmp_path / "h.obs", text)))
    assert code == 0
    text = messages(report, "warning")
    assert "convert_observations" in text and "RFT_OBSERVATION is not evaluated" in text


# ---------- prior_qc ----------


def prior_args(**over):
    base = {
        "log_pattern": prior_qc.DEFAULT_LOG_PATTERN,
        "not_log_pattern": prior_qc.DEFAULT_NOT_LOG_PATTERN,
        "edge_fraction": 0.1,
        "pile_fraction": 0.3,
        "min_realizations": 20,
    }
    return Namespace(**{**base, **over})


def test_dist_parsing_all_distributions(tmp_path):
    dist = write(
        tmp_path / "p.dist",
        "-- comment\nA NORMAL 0 1\nB LOGNORMAL 1 0.5\nC TRUNCATED_NORMAL 0 1 -2 2\nD UNIFORM 0 1\n"
        "E LOGUNIF 1 100\nF DUNIF 5 1 5\nG TRIANGULAR 0 0.5 1\nH ERRF 0 1 0 1\nI DERRF 4 0 1 0 1\nJ CONST 3\nK RAW\n",
    )
    params, errors = prior_qc.parse_dist(dist)
    assert errors == []
    by_name = {p["name"]: p for p in params}
    assert (by_name["A"]["min"], by_name["A"]["max"]) == (-3.0, 3.0)
    assert (by_name["B"]["min"], by_name["B"]["max"]) == (0.0, None)
    assert (by_name["C"]["min"], by_name["C"]["max"]) == (-2.0, 2.0)
    assert (by_name["J"]["min"], by_name["J"]["max"]) == (3.0, 3.0)
    assert (by_name["K"]["min"], by_name["K"]["max"]) == (None, None)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("A FOO 1 2", "unknown distribution"),
        ("A UNIFORM 1", "needs 2 arguments"),
        ("A UNIFORM 2 1", "must be smaller"),
        ("A LOGUNIF 0 1", "min > 0"),
        ("A NORMAL 0 0", "std must be larger"),
        ("A TRIANGULAR 0 2 1", "mode"),
        ("A DUNIF 1.5 0 1", "steps"),
        ("A UNIFORM x 1", "not a number"),
        ("A", "needs a name and a distribution"),
    ],
)
def test_dist_errors(tmp_path, line, expected):
    _, errors = prior_qc.parse_dist(write(tmp_path / "p.dist", line + "\n"))
    assert expected in errors[0]["message"]


def test_duplicate_parameter_is_error(tmp_path):
    a = write(tmp_path / "a.dist", "X UNIFORM 0 1\n")
    b = write(tmp_path / "b.dist", "X UNIFORM 0 2\n")
    assert prior_qc.main([str(a), str(b)]) == 1


def test_truth_outside_and_near_bound(tmp_path):
    dist = write(
        tmp_path / "p.dist", "PORO UNIFORM 0.15 0.30\nPERMX LOGUNIF 20 800\nMID UNIFORM 0 1\nN NORMAL 0 1\n"
    )
    truth = write(tmp_path / "t.txt", "G:PORO 0.14\nPERMX 750\nMID 0.5\nN 4\nOTHER 1\n")
    code, report = run_json(prior_qc.main, str(dist), "--truth", str(truth))
    assert code == 0
    found = {(f["finding"], f["parameter"]) for f in report["findings"]}
    assert ("truth outside prior", "PORO") in found
    assert ("truth near a bound", "PERMX") in found  # log scale: 98 % of the range
    assert ("truth outside prior", "N") in found  # beyond mean + 3 std
    assert not any(p == "MID" for _, p in found)
    assert ("truth name not in the prior", None) in found


def test_truth_without_match_is_error(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\n")
    truth = write(tmp_path / "t.txt", "B 0.5\n")
    assert prior_qc.main([str(dist), "--truth", str(truth)]) == 1


def test_posterior_at_bound_from_csv_and_runpaths(tmp_path):
    dist = write(tmp_path / "p.dist", "PERM_A LOGUNIF 20 800\nPORO UNIFORM 0.1 0.3\n")
    csv_file = write(
        tmp_path / "post.csv",
        "REAL,GLOBVAR:PERM_A,PORO,LOG10_GLOBVAR:PERM_A\n0,790,0.2,2.9\n1,780,0.21,2.9\n2,795,0.19,2.9\n3,500,0.22,2.7\n",
    )
    code, report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(csv_file))
    assert code == 0 and report["realizations"] == 4
    found = {(f["finding"], f["parameter"]) for f in report["findings"]}
    assert ("posterior at a bound", "PERM_A") in found and ("posterior at a bound", "PORO") not in found
    for real, value in enumerate([0.1, 0.1, 0.1, 0.2, 0.25]):
        write(tmp_path / f"ens/realization-{real}/iter-1/parameters.txt", f"G:PORO {value}\nG:PERM_A 100\n")
    code, report = run_json(
        prior_qc.main, str(dist), "--posterior-runpaths", str(tmp_path / "ens/realization-*/iter-1")
    )
    found = {(f["finding"], f["parameter"]) for f in report["findings"]}
    assert ("posterior at a bound", "PORO") in found and report["realizations"] == 5


def test_posterior_outside_bounds_and_unmatched(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\n")
    outside = write(tmp_path / "o.csv", "A\n0.5\n1.5\n")
    report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(outside))[1]
    assert report["findings"][0]["finding"] == "posterior outside the prior bounds"
    nomatch = write(tmp_path / "n.csv", "B\n0.5\n")
    assert prior_qc.main([str(dist), "--posterior-csv", str(nomatch)]) == 1


def test_ensemble_size_and_perm_like_uniform(tmp_path):
    dist = write(
        tmp_path / "p.dist",
        "PERMX UNIFORM 10 1000\nRELPERM_NW UNIFORM 1.5 4\nKH_L1 UNIFORM 1 5\nPORO UNIFORM 0.1 0.3\nC CONST 1\n",
    )
    report = run_json(prior_qc.main, str(dist), "--realizations", "4")[1]
    found = {(f["finding"], f["parameter"]) for f in report["findings"]}
    perm = {p for k, p in found if k == "linear distribution for a permeability-like parameter"}
    assert perm == {"PERMX", "KH_L1"}
    assert ("fewer realizations than parameters", None) in found  # 4 <= 4 uncertain
    assert ("small ensemble", None) in found
    report = run_json(prior_qc.main, str(dist), "--realizations", "50")[1]
    assert not any(
        f["finding"] in ("small ensemble", "fewer realizations than parameters") for f in report["findings"]
    )


def test_poro_ntg_saturation_multipliers_are_not_perm_like(tmp_path):
    # MULT matches the default --log-pattern. PORO, NTG, saturation endpoint, and SATNUM names are removed by
    # --not-log-pattern. Fault and transmissibility multipliers stay, also with SW in a longer name part.
    dist = write(
        tmp_path / "p.dist",
        "PORO_MULT UNIFORM 0.8 1.2\nMULT_NTG UNIFORM 0.8 1.2\nSWCR_MULT UNIFORM 0.8 1.2\nSOWCR_MULT UNIFORM 0.8 1.2\n"
        "SW_MULT UNIFORM 0.8 1.2\nSATNUM_MULT UNIFORM 0.8 1.2\n"
        "MULTFLT_F1 UNIFORM 0.01 1\nTRANS_SWFAULT UNIFORM 0.01 1\nMULTFLT_SW1 UNIFORM 0.01 1\nMULTZ UNIFORM 0.1 1\n",
    )
    report = run_json(prior_qc.main, str(dist), "--realizations", "100")[1]
    perm = {f["parameter"] for f in report["findings"] if f["finding"].startswith("linear distribution")}
    assert perm == {"MULTFLT_F1", "TRANS_SWFAULT", "MULTFLT_SW1", "MULTZ"}
    assert report["thresholds"]["not_log_pattern"] == prior_qc.DEFAULT_NOT_LOG_PATTERN
    report = run_json(prior_qc.main, str(dist), "--realizations", "100", "--not-log-pattern", "^$")[1]
    perm = {f["parameter"] for f in report["findings"] if f["finding"].startswith("linear distribution")}
    assert "PORO_MULT" in perm and len(perm) == 10


def test_prior_qc_usage_errors(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\n")
    assert prior_qc.main([str(dist), "--edge-fraction", "0"]) == 2
    assert prior_qc.main([str(dist), "--log-pattern", "("]) == 2
    assert prior_qc.main([str(dist), "--not-log-pattern", "("]) == 2
    assert prior_qc.main([str(tmp_path / "none.dist")]) == 2


def collapse_case(tmp_path, collapsed: int, total: int = 20, n: int = 16) -> tuple[Path, Path]:
    """total UNIFORM 0-1 parameters. The first `collapsed` have posterior sd 0.01, the others sd near 0.29."""
    names = [f"P{i:02d}" for i in range(total)]
    dist = write(tmp_path / "p.dist", "".join(f"{name} UNIFORM 0 1\n" for name in names))
    rows = []
    for real in range(n):
        wide = (real + 0.5) / n
        narrow = 0.5 + 0.01 * (real - (n - 1) / 2) / 4.6
        rows.append(",".join(f"{narrow if i < collapsed else wide:.6f}" for i in range(total)))
    post = write(tmp_path / "post.csv", ",".join(names) + "\n" + "\n".join(rows) + "\n")
    return dist, post


def coded(report: dict, code: str) -> list[dict]:
    return [f for f in report["findings"] if f.get("code") == code]


def test_collapse_fires_on_drogon_d1_shape(tmp_path):
    dist, post = collapse_case(tmp_path, collapsed=18)
    code, report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post))
    assert code == 0
    (item,) = coded(report, "HMQC-COLLAPSE")
    assert "18 of 20 parameters (90%)" in item["detail"]
    assert "< 0.2" in item["detail"] and "more than 50%" in item["detail"]
    assert "N = 16, 20 uncertain parameters" in item["detail"]
    assert "floor of the ratio 0.60" in item["detail"]  # exp(-1.96 / sqrt(15))
    assert "Scale: native" in item["detail"] and "Limiter" in item["detail"]
    ratios = {r["parameter"]: r["sd_ratio"] for r in report["spread"]}
    assert ratios["P00"] == pytest.approx(0.01 / (1 / math.sqrt(12)), rel=0.05)


def test_collapse_does_not_fire_at_half(tmp_path):
    dist, post = collapse_case(tmp_path, collapsed=10)
    report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post))[1]
    assert coded(report, "HMQC-COLLAPSE") == []
    assert len(report["spread"]) == 20


def test_collapse_uses_log10_scale_for_logunif(tmp_path):
    dist = write(tmp_path / "p.dist", "PERM LOGUNIF 1 10000\n")
    # Native sd is large, but log10 sd is 0.04 against a prior log10 sd of 4 / sqrt(12) = 1.155.
    post = write(tmp_path / "post.csv", "PERM\n" + "".join(f"{v}\n" for v in (900, 1000, 1100, 1200)))
    report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post))[1]
    (row,) = report["spread"]
    assert row["scale"] == "log10"
    assert row["prior_sd"] == pytest.approx(4 / math.sqrt(12), rel=1e-3)
    assert row["sd_ratio"] < 0.05
    assert "Scale: log10" in coded(report, "HMQC-COLLAPSE")[0]["detail"]


def test_outside_prior_from_distribution_and_from_prior_ensemble(tmp_path):
    dist = write(tmp_path / "p.dist", "A NORMAL 0 1\nB UNIFORM 0 10\n")
    post = write(tmp_path / "post.csv", "A,B\n2.5,5\n2.7,6\n2.6,4\n")
    report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post))[1]
    (item,) = coded(report, "HMQC-OUTSIDE-PRIOR")
    assert item["parameter"] == "A"
    assert "posterior mean 2.6 is outside the prior central 95 % interval -1.96-1.96" in item["detail"]
    assert "from the distribution" in item["detail"] and "Limiter" in item["detail"]
    # A narrow prior ensemble for B moves its interval to 0.5-1.5, so the posterior mean 5 is outside.
    prior = write(tmp_path / "prior.csv", "A,B\n-3,0.5\n0,1\n3,1.5\n")
    report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post), "--prior-csv", str(prior))[1]
    names = {f["parameter"] for f in coded(report, "HMQC-OUTSIDE-PRIOR")}
    assert names == {"B"}
    assert all(r["prior_source"] == "prior ensemble" for r in report["spread"])


def test_truth_z_counts_parameters_far_from_truth(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\nB UNIFORM 0 1\nC UNIFORM 0 1\n")
    post = write(tmp_path / "post.csv", "A,B,C\n0.49,0.49,0.3\n0.51,0.51,0.7\n")
    truth = write(tmp_path / "t.txt", "A 0.9\nB 0.505\nC 0.9\n")
    report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post), "--truth", str(truth))[1]
    (item,) = coded(report, "HMQC-TRUTH-Z")
    # A: |0.9 - 0.5| / 0.01414 = 28.3. B: 0.35. C: 0.4 / 0.283 = 1.41.
    assert item["detail"].startswith(
        "1 of 3 parameters have |truth - posterior mean| / posterior sd > 3: A z=28.3"
    )
    assert "synthetic cases only" in item["detail"]
    report = run_json(
        prior_qc.main, str(dist), "--posterior-csv", str(post), "--truth", str(truth), "--truth-z", "30"
    )[1]
    assert coded(report, "HMQC-TRUTH-Z") == []


def test_prior_ensemble_with_mismatched_names_fails(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\nB UNIFORM 0 1\n")
    post = write(tmp_path / "post.csv", "A,B\n0.49,0.49\n0.51,0.51\n")
    prior = write(tmp_path / "prior.csv", "X,Y\n0.1,0.1\n0.9,0.9\n")
    code, report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post), "--prior-csv", str(prior))
    assert code == 1
    (item,) = report["problems"]
    assert item["message"].startswith("no posterior parameter has a usable prior (2 or more values)")
    assert "missing in the prior: ['A', 'B']" in item["message"]
    assert "prior names not in the distribution files: ['X', 'Y']" in item["message"]
    assert report["spread"] == [] and coded(report, "HMQC-COLLAPSE") == []


def test_prior_ensemble_with_partial_coverage_lists_the_gaps(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\nB UNIFORM 0 1\n")
    post = write(tmp_path / "post.csv", "A,B\n0.49,0.49\n0.51,0.51\n")
    prior = write(tmp_path / "prior.csv", "A\n0.1\n0.9\n")
    code, report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post), "--prior-csv", str(prior))
    assert code == 0
    (item,) = [f for f in report["findings"] if f["finding"] == "prior ensemble does not cover the posterior"]
    assert item["detail"].startswith("1 of 2 posterior parameters have a usable prior")
    assert "missing in the prior: ['B']" in item["detail"]
    assert [r["parameter"] for r in report["spread"]] == ["A"]


def test_one_member_prior_ensemble_fails(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\n")
    post = write(tmp_path / "post.csv", "A\n0.49\n0.51\n")
    prior = write(tmp_path / "prior.csv", "A\n0.5\n")
    code, report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post), "--prior-csv", str(prior))
    assert code == 1
    assert "fewer than 2 prior values: ['A']" in report["problems"][0]["message"]


def test_full_prior_ensemble_has_no_coverage_finding(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\n")
    post = write(tmp_path / "post.csv", "A\n0.49\n0.51\n")
    prior = write(tmp_path / "prior.csv", "A\n0.1\n0.9\n")
    code, report = run_json(prior_qc.main, str(dist), "--posterior-csv", str(post), "--prior-csv", str(prior))
    assert code == 0
    assert not [
        f for f in report["findings"] if f["finding"] == "prior ensemble does not cover the posterior"
    ]


def test_spread_checks_usage_errors(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\n")
    prior = write(tmp_path / "prior.csv", "A\n0.1\n0.9\n")
    assert prior_qc.main([str(dist), "--prior-csv", str(prior)]) == 2
    assert prior_qc.main([str(dist), "--collapse-ratio", "0"]) == 2
    assert prior_qc.main([str(dist), "--truth-z", "-1"]) == 2


# ---------- collapse_checks ----------


def test_chi2_band_and_verdicts():
    low, high = collapse.chi2_band(72)
    assert low == pytest.approx(1 - 3 * math.sqrt(2 / 72)) and high == pytest.approx(
        1 + 3 * math.sqrt(2 / 72)
    )
    assert collapse.misfit_vs_chi2(0.1, 72)["verdict"] == "below the noise band"
    assert collapse.misfit_vs_chi2(1.1, 72)["verdict"] == "inside the noise band"
    assert collapse.misfit_vs_chi2(50.2, 72)["verdict"] == "above the noise band"
    unavailable = collapse.misfit_vs_chi2(None, 0)
    assert unavailable["verdict"] == "unavailable" and unavailable["band"] is None
    json.dumps(unavailable, allow_nan=False)


def test_noise_floor_and_gate():
    assert collapse.std_ratio_noise_floor(5) == pytest.approx(math.exp(-0.98))
    assert collapse.std_ratio_noise_floor(1) is None
    assert collapse.trust_gate(5, 10)["level"] == "untrustworthy"
    assert collapse.trust_gate(15, 3)["level"] == "weak"
    assert collapse.trust_gate(50, 60)["level"] == "weak"
    assert collapse.trust_gate(100, 10)["level"] == "not limited by ensemble size"
    found = collapse.findings(
        {"posterior": collapse.trust_gate(5, 10)}, {"posterior": collapse.misfit_vs_chi2(0.05, 72)}
    )
    kinds = [f["finding"] for f in found]
    assert kinds == ["ensemble size: untrustworthy", "misfit below the noise level"]
    assert "Do not claim convergence" in found[0]["detail"]
    caveat = collapse.collapse_caveat(5, 10)
    assert "0.38" in caveat and "by construction" in caveat


@pytest.mark.parametrize("n_obs,verdict", [(10, "inside the noise band"), (72, "below the noise band")])
def test_misfit_report_ensemble_checks(n_obs, verdict):
    pytest.importorskip("res2df")
    pd = pytest.importorskip("pandas")
    misfit = load(HM / "misfit_report.py", "misfit_report_t")
    params = {i: {"A": float(i), "B": 1.0, "C": float(i * 2)} for i in range(5)}
    assert misfit.uncertain_parameters(params) == 2
    table = pd.DataFrame(
        [{"ENSEMBLE": "posterior", "REAL": i, "OBS_USED": n_obs, "MISFIT": 0.05} for i in range(5)]
    )
    summary = pd.DataFrame([{"ENSEMBLE": "posterior", "OBS": f"O{i}", "COUNT": 5} for i in range(n_obs)])
    gates, chi2, found = misfit.ensemble_checks(
        table, summary, {"posterior": 5}, 2, Namespace(min_realizations=20, chi2_sigma=3.0)
    )
    assert gates["posterior"]["level"] == "weak"
    assert chi2["posterior"]["verdict"] == verdict
    expected = ["ensemble size: weak"]
    if verdict == "below the noise band":
        expected.append("misfit below the noise level")
    assert [f["finding"] for f in found] == expected


# ---------- check_ert_config --ahm ----------


def ahm_config(tmp_path: Path, extra: str = "", comment: str = "") -> Path:
    return write(tmp_path / "m/c.ert", f"{comment}NUM_REALIZATIONS 5\n{extra}")


def ahm(config: Path, weights=None, min_n=20) -> list:
    return lint.ahm_lint(config.resolve(), weights, min_n)


def by_severity(diagnostics, severity: str) -> str:
    return "\n".join(d.message for d in diagnostics if d.severity == severity)


@pytest.mark.parametrize("weights", ["4,2,1", "4, 2, 1"])
def test_esmda_weights_rescaled(tmp_path, weights):
    found = ahm(ahm_config(tmp_path), weights)
    review = by_severity(found, "review")
    assert "sum(1/w) = 1.75" in review and "7, 3.5, 1.75" in review
    assert not lint.failed(found)


def test_esmda_weights_from_comment_and_valid_sum(tmp_path):
    config = ahm_config(tmp_path, comment="-- planned: ert es_mda c.ert --weights 4,2,2\n")
    assert "sum(1/w) = 1.25" in by_severity(ahm(config), "review")
    write(tmp_path / "m/inc.ert", '-- es_mda --weights "2, 2"\n')
    config = ahm_config(tmp_path, extra="INCLUDE inc.ert\n")
    assert "(comment at inc.ert:1): sum(1/w) = 1" in by_severity(ahm(config), "note")


@pytest.mark.parametrize(
    ("weights", "expected"), [("4,x,1", "not a number"), ("4,-2", "negative"), ("0,0", "no usable")]
)
def test_esmda_bad_weights_are_errors(tmp_path, weights, expected):
    found = ahm(ahm_config(tmp_path), weights)
    assert expected in by_severity(found, "error")


def test_esmda_zero_weight_is_ignored_like_ert(tmp_path):
    found = ahm(ahm_config(tmp_path), "2,0,2")
    assert "ignores a weight of 0" in by_severity(found, "review")
    assert "ES-MDA weights 2, 2 (--weights): sum(1/w) = 1" in by_severity(found, "note")


def test_ahm_missing_settings_are_review_items(tmp_path):
    found = ahm(ahm_config(tmp_path))
    review = by_severity(found, "review")
    for text in (
        "RANDOM_SEED is not set",
        "UPDATE_LOG_PATH is not set",
        "MIN_REALIZATIONS is not set",
        "below 20",
    ):
        assert text in review
    notes = by_severity(found, "note")
    assert "ENKF_ALPHA is not set. ERT 23 uses 3.0" in notes and "no ES-MDA weights given" in notes


def test_ahm_settings_present(tmp_path):
    extra = (
        "ENKF_ALPHA 1.5\nSTD_CUTOFF 1e-6\nMIN_REALIZATIONS 80%\nRANDOM_SEED 123\nUPDATE_LOG_PATH logs\n"
        "ANALYSIS_SET_VAR OBSERVATIONS AUTO_SCALE WBHP*\n"
    )
    found = ahm(ahm_config(tmp_path, extra=extra), min_n=3)
    review = by_severity(found, "review")
    assert "ENKF_ALPHA 1.5" in review and "RANDOM_SEED" not in review and "UPDATE_LOG_PATH" not in review
    assert "MIN_REALIZATIONS" not in review  # ceil(5 * 0.8) = 4 >= 3
    assert "ANALYSIS_SET_VAR OBSERVATIONS AUTO_SCALE WBHP*" in by_severity(found, "note")
    found = ahm(ahm_config(tmp_path, extra="MIN_REALIZATIONS 80%\n"), min_n=20)
    assert "continue with 4 realizations" in by_severity(found, "review")


@pytest.mark.parametrize(
    ("extra", "expected"),
    [("ENKF_ALPHA 0\n", "ENKF_ALPHA 0 must be"), ("MIN_REALIZATIONS x\n", "not an integer")],
)
def test_ahm_bad_values_are_errors(tmp_path, extra, expected):
    assert expected in by_severity(ahm(ahm_config(tmp_path, extra=extra)), "error")


def test_ahm_only_needs_no_ert(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ert", None)
    config = ahm_config(tmp_path)
    assert lint.main(["--ahm-only", "--weights", "4,2,1", str(config)]) == 0
    assert "sum(1/w) = 1.75" in capsys.readouterr().out
    assert lint.main(["--ahm-only", "--weights", "4,x", str(config)]) == 1
    assert lint.main(["--ahm-only", str(tmp_path / "none.ert")]) == 1


def test_mapper_lists_ahm_settings(tmp_path):
    mapper = load(LINT / "ert_config_map.py", "ert_config_map_t")
    config = ahm_config(tmp_path, extra="ENKF_ALPHA 3\nANALYSIS_SET_VAR STD_ENKF INVERSION EXACT\n")
    code, report = run_json(mapper.main, str(config))
    assert code == 0
    assert report["settings"]["ENKF_ALPHA"]["value"] == "3"
    assert report["settings"]["ANALYSIS_SET_VAR"]["value"] == ["STD_ENKF INVERSION EXACT"]


# ---------- contract: optional ahm block ----------


def test_contract_ahm_block(tmp_path):
    path = write(
        tmp_path / "c.yml", "project: x\nahm:\n  min_realizations: 30  # comment\n  weight_share_min:\n"
    )
    data = doctor.read_contract(path)
    assert data["ahm"] == {"min_realizations": "30", "weight_share_min": ""}
    assert doctor.check_ahm(data["ahm"]) == []
    assert not any("ahm" in e for e in doctor.check_contract(data, "agent"))


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("  other: 1\n", "unknown contract field: ahm.other"),
        ("  min_realizations: 0\n", "at least 1"),
        ("  weight_share_min: 2\n", "fraction"),
        ("  weight_share_min: x\n", "expected a number"),
        ("  weight_share_min: 0.6\n  weight_share_max: 0.5\n", "not be larger"),
    ],
)
def test_contract_ahm_block_errors(tmp_path, block, expected):
    data = doctor.read_contract(write(tmp_path / "c.yml", "ahm:\n" + block))
    assert any(expected in e for e in doctor.check_ahm(data["ahm"]))


def test_contract_indent_outside_ahm_block_is_error(tmp_path):
    with pytest.raises(ValueError, match="flat key"):
        doctor.read_contract(write(tmp_path / "c.yml", "project: x\n  nested: 1\n"))
    with pytest.raises(ValueError, match="duplicate"):
        doctor.read_contract(
            write(tmp_path / "d.yml", "ahm:\n  min_realizations: 1\n  min_realizations: 2\n")
        )


def test_example_contract_ahm_block_is_valid():
    pytest.importorskip("yaml")
    data = doctor.read_contract_v2(ROOT / "site.contract.example.yml")
    assert set(data["ahm"]) == doctor.AHM_KEYS
    assert all(value is None for value in data["ahm"].values())
    assert doctor.check_ahm(data["ahm"]) == []


# ---------- eval fixture ground truth ----------


@pytest.fixture
def ahm_case(tmp_path):
    return fixture.make(tmp_path)


def test_fixture_prior_ground_truth(ahm_case):
    truth = fixture.GROUND_TRUTH
    code, report = run_json(
        prior_qc.main,
        str(ahm_case / "dist/params.dist"),
        "--truth",
        str(ahm_case / "dist/truth.txt"),
        "--posterior-runpaths",
        str(ahm_case / "ens/realization-*/iter-1"),
    )
    assert code == 0
    by_kind: dict[str, list] = {}
    for f in report["findings"]:
        by_kind.setdefault(f["finding"], []).append(f["parameter"])
    assert by_kind["truth outside prior"] == truth["truth_outside"]
    assert by_kind["truth near a bound"] == truth["truth_near_bound"]
    assert by_kind["linear distribution for a permeability-like parameter"] == truth["linear_perm"]
    assert sorted(by_kind["posterior at a bound"]) == sorted(truth["posterior_at_bound"])
    assert report["uncertain_parameters"] == truth["uncertain_parameters"]
    assert report["realizations"] == truth["realizations"]


def test_fixture_observation_ground_truth(ahm_case):
    truth = fixture.GROUND_TRUTH
    code, report = run_json(obs_qc.main, str(ahm_case / "obs/hm.obs"))
    assert code == 0
    assert report["summary_observations"] == truth["summary_observations"]
    assert report["weights"]["groups"]["WBHP"]["count_share"] == pytest.approx(truth["bhp_share"])
    assert f"{truth['zero_wwct']} observations of WWCT:P1 have VALUE 0" in messages(report, "warning")
    code, report = run_json(obs_qc.main, str(ahm_case / "obs/rft.obs"))
    assert code == 1
    errors = messages(report, "error")
    assert truth["general_bad"] in errors and truth["general_ok"] not in errors


def test_fixture_update_log_and_config_ground_truth(ahm_case):
    truth = fixture.GROUND_TRUTH
    (step,) = ulog.load([ahm_case / "update_log"])["steps"]
    assert step["counts"]["active"] == truth["active"]
    assert [d["key"] for d in step["deactivated"]] == truth["outlier"]
    assert step["alpha"] == truth["alpha"] and step["notes"] == []
    found = lint.ahm_lint(ahm_case / "model/ahm.ert", None, 20)
    assert f"sum(1/w) = {truth['weights_sum_inverse']:g}" in by_severity(found, "review")


def test_runner_has_ahm_fixture(tmp_path):
    runner = load(ROOT / "evals/runner.py", "runner_t")
    data = tmp_path / "data"
    data.mkdir()
    runner.FIXTURES["ahm_case"](tmp_path, data)
    assert (data / "ahm/model/ahm.ert").is_file()


@pytest.mark.parametrize("weights", ["nan,2", "inf,2", "1e-320,2"])
def test_esmda_nonfinite_weights_are_errors(tmp_path, weights):
    assert lint.failed(ahm(ahm_config(tmp_path), weights))


@pytest.mark.parametrize("restart", ["inf", "nan"])
def test_general_nonfinite_restart_is_error(tmp_path, restart):
    code, report = general(tmp_path, f"DATA = D; RESTART = {restart}; VALUE = 1; ERROR = 1;")
    assert code == 1 and "report step" in messages(report, "error")


def test_prior_missing_parameter_in_one_member_is_error(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\nB UNIFORM 0 1\n")
    csv_file = write(tmp_path / "post.csv", "REAL,A,B\n0,0.2,0.3\n1,0.4,\n")
    assert prior_qc.main([str(dist), "--posterior-csv", str(csv_file)]) == 1


@pytest.mark.parametrize("value", ["nan", "inf"])
def test_prior_nonfinite_truth_is_error(tmp_path, value):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\n")
    truth = write(tmp_path / "truth.txt", f"A {value}\n")
    assert prior_qc.main([str(dist), "--truth", str(truth)]) == 1


def test_prior_lognormal_truth_must_be_positive(tmp_path):
    dist = write(tmp_path / "p.dist", "A LOGNORMAL 0 1\n")
    truth = write(tmp_path / "truth.txt", "A -1\n")
    report = run_json(prior_qc.main, str(dist), "--truth", str(truth))[1]
    assert report["findings"][0]["finding"] == "truth outside prior"


def test_update_log_header_only_uses_reported_counts(tmp_path):
    write(tmp_path / "s/Report.report", report_header())
    (step,) = ulog.load([tmp_path / "s"])["steps"]
    assert step["counts"]["active"] == 3 and step["counts"]["outlier"] == 1
    assert step["observations"] == 4
    assert step["notes"] == ["the step has no observation rows; counts come from the header"]


def test_update_log_malformed_csv_is_error(tmp_path):
    write(tmp_path / "s/Report.csv", "key,status,std,response_std\nA,Deactivated, outlier,1,2\n")
    assert ulog.main([str(tmp_path / "s")]) == 1


def test_ahm_missing_include_is_error(tmp_path):
    assert lint.failed(ahm(ahm_config(tmp_path, extra="INCLUDE missing.ert\n")))


@pytest.mark.parametrize("value", ["inf%", "nan%", "bad%"])
def test_ahm_invalid_minimum_percentage_is_error(tmp_path, value):
    assert lint.failed(ahm(ahm_config(tmp_path, extra=f"MIN_REALIZATIONS {value}\n")))


def test_prior_invalid_errf_width_is_error(tmp_path):
    dist = write(tmp_path / "p.dist", "A ERRF 0 1 0 0\n")
    assert prior_qc.main([str(dist)]) == 1


def test_prior_different_member_parameter_sets_are_error(tmp_path):
    dist = write(tmp_path / "p.dist", "A UNIFORM 0 1\nB UNIFORM 0 1\n")
    write(tmp_path / "ens/realization-0/parameters.txt", "A 0.2\n")
    write(tmp_path / "ens/realization-1/parameters.txt", "B 0.3\n")
    assert prior_qc.main([str(dist), "--posterior-runpaths", str(tmp_path / "ens/realization-*")]) == 1


def test_ahm_lint_flags_pre_ert26_localization_switch(tmp_path):
    found = ahm(ahm_config(tmp_path, extra="ANALYSIS_SET_VAR STD_ENKF LOCALIZATION True\n"))
    warning = by_severity(found, "warning")
    assert "pre-ERT-26 switch" in warning and "PARAMETERS GEN_KW" in warning
    clean = ahm(ahm_config(tmp_path / "b", extra="ANALYSIS_SET_VAR PARAMETERS GEN_KW ADAPTIVE\n"))
    assert "pre-ERT-26" not in by_severity(clean, "warning")


def adaptive_config(tmp_path: Path, members: int, threshold: str | None, switch: str) -> Path:
    lines = [f"NUM_REALIZATIONS {members}", switch]
    if threshold is not None:
        lines.append(f"ANALYSIS_SET_VAR STD_ENKF LOCALIZATION_CORRELATION_THRESHOLD {threshold}")
    return write(tmp_path / "m/c.ert", "\n".join(lines) + "\n")


ERT26_ADAPTIVE = "ANALYSIS_SET_VAR PARAMETERS FIELD ADAPTIVE"
ERT23_ADAPTIVE = "ANALYSIS_SET_VAR STD_ENKF LOCALIZATION True"


@pytest.mark.parametrize("switch", [ERT26_ADAPTIVE, ERT23_ADAPTIVE])
def test_ahm_lint_flags_adaptive_threshold_below_noise_floor(tmp_path, switch):
    # 0.3 at N=20 is below 2/sqrt(20) = 0.447: spurious correlations pass the threshold.
    review = by_severity(ahm(adaptive_config(tmp_path, 20, "0.3", switch)), "review")
    assert "LOCALIZATION_CORRELATION_THRESHOLD 0.3" in review
    assert "2/sqrt(N) = 0.447" in review and "3/sqrt(N) = 0.671" in review


@pytest.mark.parametrize(
    ("members", "threshold", "switch"),
    [
        (50, "0.3", ERT26_ADAPTIVE),  # 2/sqrt(50) = 0.283
        (100, "0.3", ERT23_ADAPTIVE),  # 2/sqrt(100) = 0.2
        (20, None, ERT26_ADAPTIVE),  # ERT default 3/sqrt(N)
        (20, "0.3", "ANALYSIS_SET_VAR PARAMETERS FIELD GLOBAL"),  # threshold not used
    ],
)
def test_ahm_lint_keeps_quiet_on_a_usable_adaptive_threshold(tmp_path, members, threshold, switch):
    review = by_severity(ahm(adaptive_config(tmp_path, members, threshold, switch), min_n=1), "review")
    assert "LOCALIZATION_CORRELATION_THRESHOLD" not in review
