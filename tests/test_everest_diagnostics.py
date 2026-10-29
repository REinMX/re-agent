"""Synthetic result tables and real Egg Everest outputs; no optimizer or simulator execution."""

import importlib.util
import io
import re
import shutil
import sys

import pytest

pd = pytest.importorskip("pandas")
yaml = pytest.importorskip("yaml")
from test_everest_config import ROOT, config  # noqa: E402

SCRIPTS = ROOT / "skills/everest-diagnostics/scripts"
spec = importlib.util.spec_from_file_location("everest_diagnostics", SCRIPTS / "everest_diagnostics.py")
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)
sys.modules.setdefault("everest_diagnostics", diag)
spec = importlib.util.spec_from_file_location("compare_plans", SCRIPTS / "compare_plans.py")
plans = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plans)
DATA = ROOT / "tests/data/everest"


def tables(values=(3, 2, 1), rates=(50, 70, 100)):
    return pd.DataFrame({"batch_id": [0, 1, 2], "batch_total_objective_value": values}), pd.DataFrame(
        {"batch_id": [0, 1, 2], "rate.a": rates}
    )


def test_best_trajectory_and_bound():
    objectives, controls = tables()
    report = diag.diagnose(objectives, config(), controls)
    assert "Best batch 2" in report
    assert "Improvement (minimize): 2" in report
    assert "rate.a at max bound 100" in report
    assert "Control trajectory" in report
    assert "failures and missing members are unknown" in report


def test_stall_only_at_initial_guess():
    cfg = config()
    cfg["optimization"]["algorithm"] = "scipy/optimize/slsqp"
    objectives, controls = tables((3, 3, 3), (50, 50, 50))
    report = diag.diagnose(objectives, cfg, controls)
    assert "SLSQP stall" in report and "recommend COBYLA" in report
    controls["rate.a"] = 60
    assert "SLSQP stall" not in diag.diagnose(objectives, cfg, controls)
    controls["rate.a"] = [50, 51, 50]
    assert "SLSQP stall" not in diag.diagnose(objectives, cfg, controls)


def test_non_monotone_and_maximize():
    objectives, controls = tables((3, 1, 2))
    assert "Non-monotone" in diag.diagnose(objectives, config(), controls)
    report = diag.diagnose(objectives, config(), controls, direction="maximize")
    assert "Best batch 0" in report


def test_failed_missing_and_unexpected_realizations():
    objectives, controls = tables()
    status = pd.DataFrame(
        [
            (batch, member, "FAILED" if member == 1 else "SUCCESS")
            for batch in range(3)
            for member in [0, 1, 2, 9]
        ],
        columns=["batch_id", "realization", "status"],
    )
    report = diag.diagnose(objectives, config(), controls, status)
    assert "4 reported realizations; failed/incomplete [1]; missing [3, 4]; unexpected [9]" in report


def test_cvar_vs_mean_and_count_trap():
    cfg = config()
    cfg["optimization"]["cvar"] = {"percentile": 0.3}
    objectives, controls = tables()
    members = pd.DataFrame(
        [(batch, member, 10 * (member + 1)) for batch in range(3) for member in range(5)],
        columns=["batch_id", "realization", "objective"],
    )
    report = diag.diagnose(objectives, cfg, controls, per_realization=members)
    assert "n=5, mean=30, lower-tail CVaR(0.3)=13.333333" in report
    cfg["optimization"]["cvar"] = {"number_of_realizations": 2}
    assert "selects BEST N" in diag.diagnose(objectives, cfg, controls)
    cfg["optimization"]["cvar"] = {"percentile": 1}
    assert "lower-tail CVaR(1)=30" in diag.diagnose(objectives, cfg, controls, per_realization=members)
    short = diag.diagnose(objectives, cfg, controls, per_realization=members[members.realization != 4])
    assert "Batch 0: n=4 of 5 configured realizations; CVaR versus mean not compared" in short
    with pytest.raises(ValueError, match="member set"):
        diag.diagnose(objectives, cfg, controls, per_realization=members.replace({"realization": {4: 9}}))


def test_constraints_per_batch():
    cfg = config()
    cfg["output_constraints"] = [{"name": "pressure", "upper_bound": 10}]
    cfg["input_constraints"] = [{"weights": {"rate.a": 1}, "upper_bound": 80}]
    objectives, controls = tables()
    objectives["pressure"] = [8, 10, 12]
    report = diag.diagnose(objectives, cfg, controls)
    assert "Batch 2 pressure=12: VIOLATION" in report
    assert "Batch 2 linear input total=100: VIOLATION" in report


@pytest.mark.parametrize("fault", ["nonfinite", "duplicate", "batch", "control"])
def test_inconsistent_tables_refused(fault):
    objectives, controls = tables()
    if fault == "nonfinite":
        objectives.loc[0, "batch_total_objective_value"] = float("nan")
    elif fault == "duplicate":
        objectives = pd.concat([objectives, objectives.iloc[[0]].assign(batch_total_objective_value=99)])
    elif fault == "batch":
        controls.loc[0, "batch_id"] = 9
    else:
        controls = controls.drop(columns="rate.a")
    with pytest.raises(ValueError):
        diag.diagnose(objectives, config(), controls)


def test_cli_csv_report(tmp_path):
    objectives, controls = tables()
    op, cp, cfg, out = [
        tmp_path / name for name in ("objectives.csv", "controls.csv", "config.yml", "report.md")
    ]
    objectives.to_csv(op, index=False)
    controls.to_csv(cp, index=False)
    cfg.write_text(yaml.safe_dump(config()))
    assert (
        diag.main(["--objectives", str(op), "--controls", str(cp), "--config", str(cfg), "--out", str(out)])
        == 0
    )
    assert "Sources:" in out.read_text()
    assert "Best batch 2" in out.read_text()


def experiment_results():
    header = "batch_id,realization,simulation_id,perturbation,batch_npv,batch_total_objective_value,grad(npv wrt rate.a),rate.a,npv"
    rows = [
        "0,0,,0,15,-15,2.5,55,14",
        "0,0,0,,15,-15,2.5,50,10",
        "0,1,1,,15,-15,2.5,50,20",
        "0,2,2,,15,-15,2.5,50,30",
        "1,0,0,,25,-25,,100,20",
        "1,1,1,,25,-25,,100,30",
        "1,2,2,,25,-25,,100,40",
    ]
    return "\n".join([header, *rows]) + "\n"


def test_experiment_results_folder_report(tmp_path):
    folder = tmp_path / "output"
    experiment = folder / "simulation_results/experiments/abc"
    experiment.mkdir(parents=True)
    (experiment / "experiment_results.csv").write_text(experiment_results())
    (folder / "logs").mkdir()
    (folder / "logs/everest.log").write_text(
        "everest ERROR: Simulation 3 failed.\neverest ERROR: Simulation 7 failed.\nSimulation 3 ok\n"
    )
    cfg, out = tmp_path / "config.yml", tmp_path / "report.md"
    cfg.write_text(yaml.safe_dump(config()))
    assert diag.main(["--experiment-results", str(folder), "--config", str(cfg), "--out", str(out)]) == 0
    report = out.read_text()
    assert "| 1 | 25 | -25 |" in report
    assert "rate.a at max bound 100" in report
    assert "Batch 0: 3 reported realizations; failed/incomplete []; missing [3, 4]; unexpected []" in report
    assert "Batch 1: 3 reported realizations; failed/incomplete []; missing [3, 4]; unexpected []" in report
    assert "Dropped 1 perturbation (gradient) rows" in report
    assert "failures are inferred from realizations in the config without a row" in report
    assert "2 'Simulation N failed' lines" in report
    assert "grad(" not in report


def test_experiment_results_cvar_uses_first_objective():
    cfg = config()
    cfg["model"]["realizations"] = [0, 1, 2]
    cfg["optimization"]["cvar"] = {"percentile": 0.5}
    results = pd.read_csv(io.StringIO(experiment_results()))
    objectives, status, members, _ = diag.experiment_tables(results, cfg)
    report = diag.diagnose(objectives, cfg, None, status, members)
    assert "Batch 0: n=3, mean=20, lower-tail CVaR(0.5)=13.333333" in report
    assert "Batch 1: n=3, mean=30, lower-tail CVaR(0.5)=23.333333" in report
    with pytest.raises(ValueError, match="objective column oil"):
        diag.experiment_tables(results, cfg, "oil")


def test_experiment_results_several_experiments_refused(tmp_path):
    for name in ("one", "two"):
        experiment = tmp_path / "simulation_results/experiments" / name
        experiment.mkdir(parents=True)
        (experiment / "experiment_results.csv").write_text(experiment_results())
    with pytest.raises(ValueError, match="several experiments, give one csv: one, two"):
        diag.find_experiment_results(tmp_path)
    with pytest.raises(SystemExit):
        diag.main(["--experiment-results", str(tmp_path), "--objectives", "x.csv", "--out", "r.md"])


def test_experiment_failed_in_log_is_reported(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/everest.log").write_text(
        "everest ERROR: Experiment failed with exception:\n  File x\n    raise RuntimeError('DAKOTA analysis failed')\n"
        "RuntimeError: DAKOTA analysis failed\n"
    )
    note = diag.count_failed_simulations(tmp_path)
    assert note.startswith("0 'Simulation N failed' lines")
    assert "EXPERIMENT FAILED per everest.log (RuntimeError: DAKOTA analysis failed)." in note
    (tmp_path / "logs/everest.log").write_text("everest INFO: done\n")
    assert "EXPERIMENT FAILED" not in diag.count_failed_simulations(tmp_path)


def test_controls_outside_bounds_are_reported():
    cfg = config()
    low, high = cfg["controls"][0]["min"], cfg["controls"][0]["max"]
    name = f"{cfg['controls'][0]['name']}.{cfg['controls'][0]['variables'][0]['name']}"
    objectives = pd.DataFrame(
        {
            "batch_id": [0, 1, 2],
            "batch_total_objective_value": [-1.0, -2.0, -3.0],
            name: [low, high * 2, high],
        }
    )
    report = diag.diagnose(objectives, cfg)
    assert (
        f"Control {name} outside its bounds [{low:g}, {high:g}] in 1 batch(es) (batch 1: {high * 2:g})"
        in report
    )
    objectives[name] = [low, high, high]
    assert "outside its bounds" not in diag.diagnose(objectives, cfg)


def test_experiment_results_with_indexed_period_controls(tmp_path):
    cfg = config()
    cfg["controls"][0]["variables"] = [{"name": "a", "initial_guess": [50, 50]}]
    rows = [
        "batch_id,realization,simulation_id,perturbation,batch_npv,batch_total_objective_value,rate.a.1,rate.a.2,npv",
        "0,0,0,,10,-10,50,50,10",
        "0,0,,0,10,-10,55,50,11",
        "1,0,0,,12,-12,60,40,12",
    ]
    csv = tmp_path / "experiment_results.csv"
    csv.write_text("\n".join(rows) + "\n")
    cfg["model"]["realizations"] = [0]
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(cfg))
    out = tmp_path / "report.md"
    assert diag.main(["--experiment-results", str(csv), "--config", str(path), "--out", str(out)]) == 0
    report = out.read_text()
    assert "| batch_id | rate.a.1 | rate.a.2 |" in report
    assert "| 1 | 60 | 40 |" in report


E2_NPV = (1453.3, 1505.5, 1537.5, 1510.8, 1578.3, 1606.3, 1385.2, 1294.5)
E2_VRR = (0.916, 0.797, 0.795, 0.846, 0.771, 0.813, 1.034, 1.063)


def e2_report(tmp_path, vrr=E2_VRR, constrained=True):
    cfg = config()
    cfg["model"]["realizations"] = [0]
    if constrained:
        cfg["output_constraints"] = [{"name": "vrr", "lower_bound": 0.9}]
    rows = [
        "batch_id,realization,simulation_id,perturbation,batch_npv,batch_vrr,batch_total_objective_value,rate.a,npv,vrr"
    ]
    for batch, (npv, ratio) in enumerate(zip(E2_NPV, vrr, strict=True)):
        rows.append(f"{batch},0,{batch},,{npv},{ratio},{-npv},60,{npv},{ratio}")
        rows.append(f"{batch},0,,0,{npv},{ratio},{-npv},61,{npv},{ratio}")
    csv, path, out = tmp_path / "experiment_results.csv", tmp_path / "config.yml", tmp_path / "report.md"
    csv.write_text("\n".join(rows) + "\n")
    path.write_text(yaml.safe_dump(cfg))
    assert diag.main(["--experiment-results", str(csv), "--config", str(path), "--out", str(out)]) == 0
    return out.read_text()


def test_infeasible_best_batch_is_not_the_headline(tmp_path):
    report = e2_report(tmp_path)
    assert "Best feasible batch 0: batch_total_objective_value=-1453.3" in report
    assert "Improvement (minimize): 0." in report
    assert "No feasible improvement over the initial batch." in report
    assert "Best batch overall 5 is INFEASIBLE (batch_vrr=0.813 < lower bound 0.9)." in report
    assert "Best batch 5" not in report
    assert "| feasible |" in report
    assert "| 5 | 1606.3 | 0.813 | -1606.3 | no |" in report
    assert "| 6 | 1385.2 | 1.034 | -1385.2 | yes |" in report


def test_feasible_improvement_over_initial(tmp_path):
    report = e2_report(tmp_path, vrr=(0.5, 0.95, *E2_VRR[2:]))
    assert "Best feasible batch 1: batch_total_objective_value=-1505.5" in report
    assert "Improvement (minimize): 52.2" in report
    assert "No feasible improvement" not in report
    assert "Best batch overall 5 is INFEASIBLE" in report


def test_no_constraints_keeps_best_batch_wording(tmp_path):
    report = e2_report(tmp_path, constrained=False)
    assert "Best batch 5: batch_total_objective_value=-1606.3" in report
    assert "Improvement (minimize): 153" in report
    assert "feasible" not in report.lower().replace("infeasible", "")
    assert "INFEASIBLE" not in report


def test_all_batches_infeasible(tmp_path):
    report = e2_report(tmp_path, vrr=(0.5,) * 8)
    assert "No feasible batch." in report
    assert "Best batch 5: batch_total_objective_value=-1606.3" in report
    assert "Best batch overall 5 is INFEASIBLE (batch_vrr=0.5 < lower bound 0.9)." in report
    assert "Best feasible batch" not in report


# Drogon lessons as finding codes. Each test asserts the fire and the not-fire case.


def findings(report):
    return report.split("## Findings\n\n", 1)[1].split("\n\n", 1)[0] if "## Findings" in report else ""


def prt(folder, batch, member, text):
    path = folder / f"batch_{batch}/realization_{member}/evaluation_0/CASE.PRT"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


SHUT_LINE = "Well OP5 will be shut as it can not operate under current reservoir conditions.\n"


def test_well_shut_fires_on_prt_line(tmp_path):
    prt(tmp_path, 0, 0, "Report step 1\n" + SHUT_LINE)
    prt(tmp_path, 1, 2, "Well OP5 is shut by the schedule\n")
    prt(tmp_path, 10, 1, SHUT_LINE.replace("OP5", "OP7"))
    shut, note = diag.well_shut_finding(tmp_path)
    assert note is None
    assert shut.startswith(
        "EVD-WELL-SHUT (warning): Flow shut well(s) OP5, OP7 ('will be shut as it can not operate') in 2 "
        "batch/realization folder(s) of 3 PRT files: batch_0/realization_0: OP5; batch_10/realization_1: OP7."
    )
    assert "finite differences across the shut-in are not valid derivatives" in shut
    assert "Limiter: a well that shuts in the base and all perturbed runs adds no jump" in shut


def test_well_shut_does_not_fire_without_the_line(tmp_path):
    prt(tmp_path, 0, 0, "Well OP5 is shut by the schedule\n")
    shut, note = diag.well_shut_finding(tmp_path)
    assert shut is None
    assert note.startswith("No 'will be shut as it can not operate' line in 1 PRT files")
    (tmp_path / "batch_0/realization_0/evaluation_0/CASE.PRT").unlink()
    assert diag.well_shut_finding(tmp_path)[1].startswith("No *.PRT files under")
    with pytest.raises(ValueError, match="no batch_\\* folders"):
        diag.well_shut_finding(tmp_path / "batch_0")


def test_well_shut_cli_puts_the_code_in_findings(tmp_path):
    sim = tmp_path / "sim"
    prt(sim, 0, 0, SHUT_LINE)
    csv, out = tmp_path / "experiment_results.csv", tmp_path / "report.md"
    csv.write_text(experiment_results())
    assert (
        diag.main(["--experiment-results", str(csv), "--simulation-folder", str(sim), "--out", str(out)]) == 0
    )
    assert "EVD-WELL-SHUT (warning)" in findings(out.read_text())


def cvar_results(gradient_members, unperturbed_batch_1):
    rows = [
        "batch_id,realization,simulation_id,perturbation,batch_npv,batch_total_objective_value,rate.a,npv"
    ]
    rows += [f"0,{m},{m},,15,-15,50,{10 + m}" for m in range(8)]
    rows += [f"0,{m},,0,15,-15,55,{11 + m}" for m in range(8)]
    rows += [f"1,{m},{8 + m},,20,-20,60,{20 + m}" for m in unperturbed_batch_1]
    rows += [f"1,{m},,0,20,-20,65,{21 + m}" for m in gradient_members]
    return pd.read_csv(io.StringIO("\n".join(rows) + "\n"))


def cvar_config():
    cfg = config()
    cfg["model"]["realizations"] = list(range(8))
    cfg["optimization"]["cvar"] = {"percentile": 0.25}
    return cfg


def test_cvar_tail_missing_and_tail_only_gradient_fire():
    cfg, results = cvar_config(), cvar_results([0, 1], [0, 1])
    objectives, status, members, _ = diag.experiment_tables(results, cfg)
    report = diag.diagnose(
        objectives, cfg, None, status, members, findings=diag.experiment_findings(results, cfg)
    )
    assert (
        "EVD-CVAR-TAIL-MISSING (warning): optimization.cvar is set (percentile=0.25), and the CVaR tail of "
        "these batches is computed over fewer realizations than configured: batch 1: 2 of 8, missing [2, 3, 4, 5, 6, 7]."
    ) in report
    assert (
        "EVD-CVAR-TAIL-ONLY-GRADIENT (info): Gradient rows of batch(es) 1 come from 2 of 8 realizations; "
        "ceil(percentile * N) = ceil(0.25 * 8) = 2."
    ) in report
    assert "Batch 1: n=2 of 8 configured realizations; CVaR versus mean not compared" in report
    assert "Batch 0: n=8, mean=13.5" in report


def test_cvar_checks_do_not_fire_on_full_batches():
    cfg, results = cvar_config(), cvar_results(range(8), range(8))
    objectives, status, members, _ = diag.experiment_tables(results, cfg)
    report = diag.diagnose(
        objectives, cfg, None, status, members, findings=diag.experiment_findings(results, cfg)
    )
    assert "EVD-CVAR-TAIL" not in report
    cfg["optimization"].pop("cvar")
    results = cvar_results([0, 1], [0, 1])
    assert "EVD-CVAR-TAIL" not in str(diag.experiment_findings(results, cfg))


def test_budget_limited_fires_when_last_batch_is_best_and_rising():
    objectives, controls = tables((3, 2, 1))
    report = diag.diagnose(objectives, config(), controls)
    assert (
        "EVD-BUDGET-LIMITED (warning): Best batch 2 is the last observed batch (3 observed, max_batch_num 8), "
        "and batch_total_objective_value improved in each of the last two batches (3 -> 2 -> 1)."
    ) in report


@pytest.mark.parametrize("values", [(3, 1, 2), (3, 3, 1), (2, 1)])
def test_budget_limited_does_not_fire(values):
    objectives = pd.DataFrame(
        {"batch_id": range(len(values)), "batch_total_objective_value": values, "rate.a": 50}
    )
    assert "EVD-BUDGET-LIMITED" not in diag.diagnose(objectives, config())


def test_budget_limited_uses_the_best_feasible_batch(tmp_path):
    report = e2_report(tmp_path, vrr=(0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.95), constrained=True)
    assert "EVD-BUDGET-LIMITED" not in report
    report = e2_report(tmp_path, vrr=(0.5,) * 5 + (0.95,) * 3)
    assert "EVD-BUDGET-LIMITED" not in report


def test_unconstrained_fires_only_with_a_config_without_output_constraints(tmp_path):
    objectives, controls = tables()
    assert (
        "EVD-UNCONSTRAINED (warning): The config has no output_constraints (0 input constraints)."
        in diag.diagnose(objectives, config(), controls)
    )
    assert "EVD-UNCONSTRAINED" not in diag.diagnose(objectives, None, controls)
    assert "EVD-UNCONSTRAINED" not in e2_report(tmp_path)


def test_start_near_constraint_fires_on_drogon_e2_start(tmp_path):
    report = e2_report(tmp_path)
    assert (
        "EVD-START-NEAR-CONSTRAINT (warning): Initial batch 0 is feasible, but batch_vrr=0.916 is 0.016 from "
        "its lower_bound 0.9 (1.8 % of |bound| 0.9) (threshold 5 %)."
    ) in report


@pytest.mark.parametrize("start", [0.95, 0.5])
def test_start_near_constraint_does_not_fire(tmp_path, start):
    assert "EVD-START-NEAR-CONSTRAINT" not in e2_report(tmp_path, vrr=(start, *E2_VRR[1:]))


def test_start_near_constraint_uses_the_scale():
    cfg = config()
    cfg["output_constraints"] = [{"name": "vrr", "lower_bound": 0.9, "scale": 0.1}]
    objectives = pd.DataFrame(
        {"batch_id": [0, 1], "batch_total_objective_value": [-1, -2], "batch_vrr": [0.916, 1], "rate.a": 50}
    )
    assert "EVD-START-NEAR-CONSTRAINT" not in diag.diagnose(objectives, cfg)
    objectives["batch_vrr"] = [0.904, 1]
    assert "0.004 from its lower_bound 0.9 (4.0 % of scale 0.1)" in diag.diagnose(objectives, cfg)


def drill_config(group="drill", kind="generic_control"):
    cfg = config()
    cfg["controls"] = [
        {
            "name": group,
            "type": kind,
            "min": 0,
            "max": 1,
            "initial_guess": 0.5,
            "variables": [{"name": w} for w in ("OP7", "OP8", "OP9")],
        }
    ]
    return cfg


def drill_objectives(op8):
    return pd.DataFrame(
        {
            "batch_id": [0, 1],
            "batch_total_objective_value": [-1, -2],
            "drill.OP7": [0.5, 1.0],
            "drill.OP8": [0.5, op8],
            "drill.OP9": [0.5, 0.4],
        }
    )


def test_priority_tie_fires_on_drogon_e3_tie_at_bound():
    report = diag.diagnose(drill_objectives(1.0), drill_config())
    assert (
        "EVD-PRIORITY-TIE (warning): Batch 1 group drill: drill.OP7 = drill.OP8 = 1 (at max bound 1), "
        "|diff| < 1e-6 of the range."
    ) in report
    assert "drill.OP9" not in findings(report)
    named = diag.diagnose(
        drill_objectives(1.0).rename(columns=lambda c: c.replace("drill", "priority")),
        drill_config("priority", "well_control"),
    )
    assert "EVD-PRIORITY-TIE (warning): Batch 1 group priority" in named


def test_priority_tie_does_not_fire():
    assert "EVD-PRIORITY-TIE" not in diag.diagnose(drill_objectives(0.999), drill_config())
    rate = drill_objectives(1.0).rename(columns=lambda c: c.replace("drill", "rate"))
    assert "EVD-PRIORITY-TIE" not in diag.diagnose(rate, drill_config("rate", "well_control"))


def agree_objectives(fopt):
    return pd.DataFrame(
        {
            "batch_id": range(4),
            "batch_total_objective_value": [-1.0, -2.0, -3.0, -4.0],
            "rate.a": 50,
            "batch_npv": [1453.3, 1505.5, 1537.5, 1578.3],
            "batch_fopt": fopt,
        }
    )


def test_objectives_agree_fires_on_correlated_objectives():
    cfg = config()
    cfg["objective_functions"] = [{"name": "npv", "weight": 0.5}, {"name": "fopt", "weight": 0.5}]
    report = diag.diagnose(agree_objectives([10.1, 10.6, 10.9, 11.3]), cfg)
    assert (
        "EVD-OBJECTIVES-AGREE (info): batch_npv and batch_fopt: Pearson r=1.000 over 4 batches (threshold 0.9)."
        in report
    )


def test_objectives_agree_does_not_fire():
    cfg = config()
    cfg["objective_functions"] = [{"name": "npv"}, {"name": "fopt"}]
    assert "EVD-OBJECTIVES-AGREE" not in diag.diagnose(agree_objectives([11.3, 10.6, 10.9, 10.1]), cfg)
    assert "EVD-OBJECTIVES-AGREE" not in diag.diagnose(
        agree_objectives([10.1, 10.6, 10.9, 11.3]).iloc[:3], cfg
    )
    cfg["objective_functions"] = [{"name": "npv"}]
    assert "EVD-OBJECTIVES-AGREE" not in diag.diagnose(agree_objectives([10.1, 10.6, 10.9, 11.3]), cfg)


def test_cost_ratio_reports_the_upper_bound_and_recorded_rows():
    results = pd.read_csv(io.StringIO(experiment_results()))
    assert diag.experiment_findings(results, config()) == [
        "EVD-COST-RATIO (info): 7 recorded simulation rows (unperturbed and perturbation) against the upper "
        "bound 120 (max_batch_num * (perturbation_num + 1) * n_realizations): ratio 6 %. Limiter: a failed "
        "simulation has no row, so the recorded count is a lower bound; optpp alternates function-only and "
        "gradient batches, runs converge early, and CVaR gradient batches run only the tail members."
    ]
    cfg = config()
    cfg["optimization"]["algorithm"] = "scipy/cobyla"
    assert "upper bound 40 (max_batch_num * n_realizations" in diag.experiment_findings(results, cfg)[0]
    cfg["optimization"].pop("max_batch_num")
    assert diag.experiment_findings(results, cfg) == []
    assert diag.experiment_findings(results, None) == []


def plan_csv(path, a6, a5=5000):
    rows = [
        "batch_id,realization,simulation_id,perturbation,batch_npv,batch_total_objective_value,rate.A6,rate.A5,npv"
    ]
    rows.append("0,0,0,,10,-10,6500,6500,10")
    rows.append(f"1,0,1,,20,-20,{a6},{a5},20")
    path.write_text("\n".join(rows) + "\n")
    return path


def plan_config(path):
    cfg = config()
    cfg["controls"] = [
        {
            "name": "rate",
            "min": 1000,
            "max": 12000,
            "initial_guess": 6500,
            "variables": [{"name": "A6"}, {"name": "A5"}],
        }
    ]
    cfg["model"]["realizations"] = [0]
    path.write_text(yaml.safe_dump(cfg))
    return path


def test_plan_flip_fires_on_drogon_e1_versus_e1_d2(tmp_path):
    a, b = plan_csv(tmp_path / "a.csv", 12000), plan_csv(tmp_path / "b.csv", 1000)
    cfg, out = plan_config(tmp_path / "config.yml"), tmp_path / "report.md"
    cross = tmp_path / "cross.csv"
    cross.write_text("plan,ensemble,objective\nA,A,1000\nB,A,950\nA,B,700\nB,B,869\n")
    args = [
        str(a),
        str(b),
        "--config-a",
        str(cfg),
        "--config-b",
        str(cfg),
        "--cross",
        str(cross),
        "--out",
        str(out),
    ]
    assert plans.main(args) == 0
    report = out.read_text()
    assert "- Plan A: best batch 1, batch_total_objective_value=-20 (minimize)." in report
    assert (
        "EVD-PLAN-FLIP (warning): Controls move between the lower and upper 20 % of their range: "
        "rate.A6 (A 12000 at 100 %, B 1000 at 0 %). Mean |normalized diff| = 0.500 over 2 controls (threshold 0.25). "
        "The plan depends on the posterior."
    ) in report
    assert "| rate.A6 | 12000 | 1000 | -1.0 | [1000, 12000] |" in report
    assert (
        "EVD-CROSS-REGRET (info): Plan A on ensemble B: objective 700; plan B (own) 869; regret 169. "
        "Plan B on ensemble A: objective 950; plan A (own) 1000; regret 50."
    ) in report


def test_plan_flip_does_not_fire_on_close_plans(tmp_path):
    a, b = plan_csv(tmp_path / "a.csv", 6000), plan_csv(tmp_path / "b.csv", 6500, 5200)
    cfg, out = plan_config(tmp_path / "config.yml"), tmp_path / "report.md"
    assert plans.main([str(a), str(b), "--config-a", str(cfg), "--out", str(out)]) == 0
    report = out.read_text()
    assert "EVD-PLAN-FLIP (" not in report
    assert (
        "No EVD-PLAN-FLIP: no control moves between the 20 % ends of its range; "
        "mean |normalized diff| = 0.032 over 2 controls (threshold 0.25)."
    ) in report


def test_plan_flip_needs_config_bounds(tmp_path):
    a, b = plan_csv(tmp_path / "a.csv", 6000), plan_csv(tmp_path / "b.csv", 6500, 5200)
    out = tmp_path / "report.md"
    assert plans.main([str(a), str(b), "--out", str(out)]) == 0
    report = out.read_text()
    assert "EVD-PLAN-FLIP not checked: no control has config bounds." in report
    assert "| rate.A6 | 6000 | 6500 | n/a | no config bounds |" in report


def test_plan_flip_on_mean_difference_without_end_flip(tmp_path):
    a, b = plan_csv(tmp_path / "a.csv", 4000, 4000), plan_csv(tmp_path / "b.csv", 8000, 8000)
    out = tmp_path / "report.md"
    cfg = plan_config(tmp_path / "config.yml")
    assert plans.main([str(a), str(b), "--config-a", str(cfg), "--out", str(out)]) == 0
    assert (
        "EVD-PLAN-FLIP (warning): Mean |normalized diff| = 0.364 over 2 controls (threshold 0.25)."
        in out.read_text()
    )


def test_compare_plans_refuses_bad_input(tmp_path):
    a, out = plan_csv(tmp_path / "a.csv", 12000), tmp_path / "report.md"
    b = tmp_path / "b.csv"
    b.write_text(a.read_text().replace("rate.A5", "rate.A4"))
    assert plans.main([str(a), str(b), "--out", str(out)]) == 1
    cross = tmp_path / "cross.csv"
    cross.write_text("plan,ensemble,objective\nA,A,1\nB,A,2\nA,B,3\n")
    with pytest.raises(ValueError, match="exactly the plan/ensemble pairs"):
        plans.cross_regret(pd.read_csv(cross))


@pytest.mark.parametrize(
    "case, extra, codes",
    [
        (
            "shut",
            ["--simulation-folder", "output/simulation"],
            ["EVD-WELL-SHUT", "EVD-BUDGET-LIMITED", "EVD-UNCONSTRAINED", "EVD-COST-RATIO"],
        ),
        (
            "tail",
            [],
            [
                "EVD-CVAR-TAIL-MISSING",
                "EVD-CVAR-TAIL-ONLY-GRADIENT",
                "EVD-START-NEAR-CONSTRAINT",
                "EVD-PRIORITY-TIE",
                "EVD-OBJECTIVES-AGREE",
                "EVD-COST-RATIO",
            ],
        ),
    ],
)
def test_eval_fixtures_give_the_expected_codes(tmp_path, case, extra, codes):
    """evals/cases.toml expects these codes from tests/data/everest."""
    folder, out = DATA / case, tmp_path / "report.md"
    extra = [str(folder / value) if value.startswith("output") else value for value in extra]
    args = ["--experiment-results", str(folder / "output"), "--config", str(folder / "config.yml"), *extra]
    assert diag.main([*args, "--out", str(out)]) == 0
    found = {line.split(" ", 2)[1] for line in findings(out.read_text()).splitlines()}
    assert found == set(codes)


def test_eval_fixture_plans_flip(tmp_path):
    folder, out = DATA / "plans", tmp_path / "report.md"
    cfg = str(folder / "config.yml")
    args = [
        str(folder / "plan_a/output"),
        str(folder / "plan_b/output"),
        "--config-a",
        cfg,
        "--config-b",
        cfg,
    ]
    assert plans.main([*args, "--cross", str(folder / "cross.csv"), "--out", str(out)]) == 0
    report = out.read_text()
    assert "EVD-PLAN-FLIP (warning)" in report
    assert "Plan A on ensemble B: objective 1331; plan B (own) 1500; regret 169." in report


def two_member_csv(path, rows):
    """rows: (batch, realization, objective, A6). Two realizations, one control rate.A6 in [1000, 12000]."""
    lines = [
        "batch_id,realization,simulation_id,perturbation,batch_npv,batch_total_objective_value,rate.A6,npv"
    ]
    lines += [f"{b},{r},{i},,{-o},{o},{a6},{-o}" for i, (b, r, o, a6) in enumerate(rows)]
    path.write_text("\n".join(lines) + "\n")
    return path


def two_member_config(path, **optimization):
    cfg = config()
    cfg["controls"] = [
        {"name": "rate", "min": 1000, "max": 12000, "initial_guess": 6500, "variables": [{"name": "A6"}]}
    ]
    cfg["model"]["realizations"] = [0, 1]
    cfg["optimization"].update(optimization)
    path.write_text(yaml.safe_dump(cfg))
    return path


def best_line(report, label="A"):
    return next(line for line in report.splitlines() if line.startswith(f"- Plan {label}: best"))


@pytest.mark.parametrize(("a6", "chosen"), [(12000.5, 1), (12000 + 1e-10, 2)])
def test_compare_plans_skips_a_batch_outside_the_control_bounds(tmp_path, a6, chosen):
    rows = [(b, r, o, x) for b, o, x in ((0, -10, 6500), (1, -15, 11000), (2, -20, a6)) for r in (0, 1)]
    a = two_member_csv(tmp_path / "a.csv", rows)
    cfg, out = two_member_config(tmp_path / "config.yml"), tmp_path / "report.md"
    assert (
        plans.main([str(a), str(a), "--config-a", str(cfg), "--config-b", str(cfg), "--out", str(out)]) == 0
    )
    line = best_line(out.read_text())
    assert line.startswith(f"- Plan A: best{' feasible' if chosen == 1 else ''} batch {chosen},")
    plan = diag.plan_tables(pd.read_csv(a), yaml.safe_load(cfg.read_text()))
    assert plan.violations[2] == (["control rate.A6=12000.5 > max 12000"] if chosen == 1 else [])


def incomplete_run(tmp_path, log_text=None):
    """Batch 2 has the best objective but only realization 0."""
    rows = [(0, 0, -10, 6500), (0, 1, -10, 6500), (1, 0, -15, 7000), (1, 1, -15, 7000), (2, 0, -30, 8000)]
    folder = tmp_path / "output"
    csv = folder / "simulation_results/experiments/run1/experiment_results.csv"
    csv.parent.mkdir(parents=True)
    two_member_csv(csv, rows)
    if log_text is not None:
        (folder / "logs").mkdir()
        (folder / "logs/everest.log").write_text(log_text, encoding="utf-8")
    return folder


def test_compare_plans_excludes_an_incomplete_batch_with_the_best_objective(tmp_path):
    folder = incomplete_run(tmp_path, "Experiment failed\nRuntimeError: too few realizations\n")
    cfg, out = two_member_config(tmp_path / "config.yml"), tmp_path / "report.md"
    args = [str(folder), str(folder), "--config-a", str(cfg), "--config-b", str(cfg), "--out", str(out)]
    assert plans.main(args) == 0
    report = out.read_text()
    assert best_line(report).startswith("- Plan A: best batch 1, batch_total_objective_value=-15")
    assert (
        "EVD-INCOMPLETE-BATCH (warning): Plan A: batch 2: 1 successful (needs all 2 config realizations). "
        "These batches are not used to select the plan."
    ) in report
    assert "EVD-EXPERIMENT-FAILED (warning): Plan A: 'Experiment failed' in" in report
    assert "(RuntimeError: too few realizations). The optimization did not finish" in report


def test_compare_plans_complete_run_has_no_incomplete_or_failure_finding(tmp_path):
    folder = incomplete_run(tmp_path, "Simulation 3 failed\n")
    cfg, out = two_member_config(tmp_path / "config.yml", min_realizations_success=1), tmp_path / "report.md"
    args = [str(folder), str(folder), "--config-a", str(cfg), "--config-b", str(cfg), "--out", str(out)]
    assert plans.main(args) == 0
    report = out.read_text()
    assert best_line(report).startswith("- Plan A: best batch 2,")
    assert "EVD-INCOMPLETE-BATCH" not in report and "EVD-EXPERIMENT-FAILED" not in report
    assert "an experiment failure is unknown" not in report


def test_compare_plans_says_when_the_log_is_not_read(tmp_path):
    folder = incomplete_run(tmp_path)
    csv = next(folder.glob("simulation_results/experiments/*/experiment_results.csv"))
    out = tmp_path / "report.md"
    assert plans.main([str(csv), str(folder), "--out", str(out)]) == 0
    report = out.read_text()
    assert (
        "- Plan A: no logs/everest.log (give the Everest output folder); an experiment failure is unknown."
        in report
    )
    assert "- Plan B: no " in report and "output/logs/everest.log" in report
    # Without a config, the expected member count is the largest count in a batch (2).
    assert "batch 2: 1 successful (needs the largest member count in a batch (2)" in report


def cvar_codes(results, cfg):
    return [item.split(" ", 1)[0] for item in diag.experiment_findings(results, cfg) if "EVD-CVAR" in item]


@pytest.mark.parametrize("count", [1, 3, 7])
def test_cvar_gradient_count_off_the_tail_is_a_separate_finding(count):
    cfg, results = cvar_config(), cvar_results(range(count), range(8))
    found = [f for f in diag.experiment_findings(results, cfg) if "EVD-CVAR" in f]
    assert cvar_codes(results, cfg) == ["EVD-CVAR-TAIL-COUNT"]
    assert found[0].startswith(
        "EVD-CVAR-TAIL-COUNT (warning): Gradient member counts differ from the CVaR tail "
        f"(ceil(percentile * N) = ceil(0.25 * 8) = 2) and from N = 8: batch 1: {count}; batch 1 perturbation 0: {count}."
    )


@pytest.mark.parametrize(("count", "codes"), [(2, ["EVD-CVAR-TAIL-ONLY-GRADIENT"]), (8, [])])
def test_cvar_gradient_count_at_the_tail_or_full(count, codes):
    cfg, results = cvar_config(), cvar_results(range(count), range(8))
    assert cvar_codes(results, cfg) == codes


def test_cvar_gradient_count_is_checked_per_perturbation():
    cfg, results = cvar_config(), cvar_results([0, 1], range(8))
    extra = (
        results[(results["batch_id"] == 1) & results["perturbation"].notna()].head(1).assign(perturbation=1)
    )
    results = pd.concat([results, extra], ignore_index=True)
    assert cvar_codes(results, cfg) == ["EVD-CVAR-TAIL-ONLY-GRADIENT", "EVD-CVAR-TAIL-COUNT"]
    assert "batch 1 perturbation 1: 1." in str(diag.experiment_findings(results, cfg))


EGG = DATA / "egg"


def egg_report(tmp_path, case, *extra):
    folder, out = EGG / case, tmp_path / f"{case}.md"
    args = ["--experiment-results", str(folder / "output"), "--config", str(folder / "config.yml"), *extra]
    assert diag.main([*args, "--out", str(out)]) == 0
    text = findings(out.read_text())
    return {line.split(" ", 2)[1] for line in text.splitlines()}, text


def test_egg_mean_run_gives_shut_budget_unconstrained_and_cost(tmp_path):
    """Everest 23 on the Egg benchmark: mean NPV, 10 members, optpp, no output constraints."""
    codes, text = egg_report(
        tmp_path, "EG1_mean_d1", "--simulation-folder", str(EGG / "EG1_mean_d1/simulation")
    )
    assert codes == {"EVD-WELL-SHUT", "EVD-BUDGET-LIMITED", "EVD-UNCONSTRAINED", "EVD-COST-RATIO"}
    assert "batch_1/realization_0: PROD1, PROD3, PROD4" in text
    assert "batch_0" not in text.split("EVD-WELL-SHUT", 1)[1].split("\n", 1)[0]
    assert "110 recorded simulation rows" in text and "upper bound 200" in text
    assert "Best batch 9 is the last observed batch (6 observed, max_batch_num 10)" in text


def test_egg_cvar_run_gradient_uses_only_the_tail(tmp_path):
    codes, text = egg_report(tmp_path, "EG4_cvar_d1", "--objective", "npv")
    assert "EVD-CVAR-TAIL-ONLY-GRADIENT" in codes
    assert not codes & {"EVD-CVAR-TAIL-MISSING", "EVD-CVAR-TAIL-COUNT"}
    assert "come from 3 of 10 realizations; ceil(percentile * N) = ceil(0.25 * 10) = 3" in text


EG2 = EGG / "EG2_constrained_d1"


def eg2_plan(cfg):
    results = pd.read_csv(EG2 / "output/simulation_results/experiments/run1/experiment_results.csv")
    objectives, _, _, _ = diag.experiment_tables(results, cfg)
    return diag.plan_tables(objectives, cfg)


def test_egg_constrained_run_reads_the_controls_beside_the_batch_control_columns(tmp_path):
    """G24: the batch_<control> columns of a constrained run are not the controls (about 1e-6)."""
    codes, text = egg_report(tmp_path, "EG2_constrained_d1")
    assert codes == {"EVD-BUDGET-LIMITED", "EVD-COST-RATIO", "EVD-CONSTRAINT-INACTIVE"}
    assert "200 recorded simulation rows" in text and "upper bound 200" in text
    assert "Best feasible batch 9 is the last observed batch (10 observed, max_batch_num 10)" in text
    report = (tmp_path / "EG2_constrained_d1.md").read_text()
    assert "Best feasible batch 9: batch_total_objective_value=-27.42536" in report
    assert "batch_inj." not in report and "batch_prod." not in report


def test_egg_constrained_best_feasible_batch_keeps_the_constraints():
    cfg = diag.read_yaml(EG2 / "config.yml")
    plan = eg2_plan(cfg)
    assert len(plan.names) == 20 and list(plan.batch.columns) == [
        "batch_id",
        "batch_npv",
        "batch_total_objective_value",
        "batch_vrr",
        "feasible",
    ]
    assert plan.trajectory.loc[0, "inj.INJECT1.1"] == pytest.approx(79.5)
    chosen = diag.chosen_batch(plan, "batch_total_objective_value", "minimize")
    assert chosen == 9
    vrr = plan.batch.set_index("batch_id")["batch_vrr"]
    totals = [
        plan.trajectory.mul(pd.Series(item["weights"])).sum(axis=1) for item in cfg["input_constraints"]
    ]
    assert vrr[chosen] >= 0.9 and all(total[chosen] <= 700 for total in totals)
    assert [round(total[chosen], 2) for total in totals] == [279.62, 426.33]
    # No EVD-START-NEAR-CONSTRAINT: both start margins are above 5 % of the scale.
    assert (vrr[0] - 0.9) / 1.0 == pytest.approx(0.09997, abs=1e-5)
    assert [(700 - total[0]) / 700 for total in totals] == pytest.approx([64 / 700] * 2)


def test_egg_constrained_controls_without_config_skip_batch_control_columns():
    plan = eg2_plan(None)
    assert len(plan.names) == 20 and not any(name.startswith("batch_") for name in plan.names)
    assert plan.trajectory.loc[0, "prod.PROD1"] == pytest.approx(395, abs=1e-4)


def test_egg_constrained_vrr_constraint_is_inactive(tmp_path):
    """EG2: batch_vrr stays 0.99972 to 0.99997 in all 10 batches, 0.0997 above the 0.9 bound."""
    _, text = egg_report(tmp_path, "EG2_constrained_d1")
    assert (
        "EVD-CONSTRAINT-INACTIVE (info): batch_vrr is 0.99971719 to 0.9999696 over 10 batches: range 0.000252 "
        "(0.03 % of scale 1, threshold 1 %), and the smallest distance to its lower_bound 0.9 is 0.0997 "
        "(10.0 % of scale 1, threshold 5 %). The constraint did not limit the plan. Limiter: a structurally "
        "inactive constraint"
    ) in text


def inactive_objectives(vrr):
    return pd.DataFrame(
        {
            "batch_id": range(len(vrr)),
            "batch_total_objective_value": [-1.0, -2.0, -3.0][: len(vrr)],
            "batch_vrr": vrr,
            "rate.a": 50,
        }
    )


def inactive_config(**constraint):
    cfg = config()
    cfg["output_constraints"] = [{"name": "vrr", **constraint}]
    return cfg


def test_constraint_inactive_uses_the_bound_without_a_scale():
    report = diag.diagnose(inactive_objectives([1.5, 1.505, 1.501]), inactive_config(upper_bound=2.0))
    assert (
        "EVD-CONSTRAINT-INACTIVE (info): batch_vrr is 1.5 to 1.505 over 3 batches: range 0.005 "
        "(0.25 % of |bound| 2, threshold 1 %), and the smallest distance to its upper_bound 2 is 0.495 "
        "(24.8 % of |bound| 2, threshold 5 %)."
    ) in report


@pytest.mark.parametrize(
    "vrr, constraint",
    [
        ([1.0, 0.95, 1.0], {"lower_bound": 0.9, "scale": 1.0}),  # moves 5 % of the scale
        ([0.93, 0.931, 0.93], {"lower_bound": 0.9, "scale": 1.0}),  # 3 % from the bound
        ([0.85, 0.851, 0.85], {"lower_bound": 0.9, "scale": 1.0}),  # infeasible
        ([1.0, 1.0], {"lower_bound": 0.9, "scale": 1.0}),  # 2 batches only
        ([1.0, 1.0, 1.0], {"target": 1.0}),  # no bound
    ],
)
def test_constraint_inactive_does_not_fire(vrr, constraint):
    report = diag.diagnose(inactive_objectives(vrr), inactive_config(**constraint))
    assert "EVD-CONSTRAINT-INACTIVE" not in report


def test_egg_drill_order_run_has_a_flat_objective(tmp_path):
    """EG3: the drill priorities move in each batch, but the order and the NPV (10.74) never change."""
    codes, text = egg_report(tmp_path, "EG3_drill_d1")
    assert codes == {"EVD-FLAT-OBJECTIVE", "EVD-UNCONSTRAINED", "EVD-COST-RATIO"}
    assert (
        "EVD-FLAT-OBJECTIVE (warning): batch_total_objective_value is -10.736824 in all 6 batches (range 0), but "
        "3 control(s) moved; the largest change is drill.PROD5 by 0.2 of its range."
    ) in text
    assert (
        "Group drill has drill or priority controls: they matter only through the order that they set, and the "
        "order did not change over the batches. Evaluate the 3! = 6 orders directly instead of a gradient "
        "optimizer. Limiter: a forward model that does not read the controls"
    ) in text


def flat_objectives(total, rate):
    return pd.DataFrame({"batch_id": range(len(total)), "batch_total_objective_value": total, "rate.a": rate})


def test_flat_objective_without_a_priority_group_gives_no_order_advice():
    report = findings(diag.diagnose(flat_objectives([5.0] * 3, [50, 60, 70]), None))
    assert report.startswith(
        "- EVD-FLAT-OBJECTIVE (warning): batch_total_objective_value is 5 in all 3 batches"
    )
    assert "rate.a by 20 of its range" in report and "orders" not in report


@pytest.mark.parametrize(
    "total, rate",
    [
        ([5.0, 5.0 + 1e-6, 5.0], [50, 60, 70]),  # the objective moves
        ([5.0] * 3, [50, 50, 50 + 1e-4]),  # the controls do not move (1e-4 of the range 1)
        ([5.0] * 2, [50, 60]),  # 2 batches only
    ],
)
def test_flat_objective_does_not_fire(total, rate):
    assert "EVD-FLAT-OBJECTIVE" not in diag.diagnose(flat_objectives(total, rate), None)


def test_compare_plans_reads_a_constrained_egg_run(tmp_path):
    out = tmp_path / "compare.md"
    mean = EGG / "EG1_mean_d1"
    args = [str(mean / "output"), str(EG2 / "output"), "--config-a", str(mean / "config.yml")]
    args += ["--config-b", str(EG2 / "config.yml"), "--labels", "EG1", "EG2", "--out", str(out)]
    assert plans.main(args) == 0
    text = out.read_text()
    assert "- Plan EG2: best feasible batch 9, batch_total_objective_value=-27.42536 (minimize)." in text
    assert "EVD-PLAN-FLIP" in text and "| inj.INJECT3.2 | " in text


def egg_cvar(case, cfg):
    results = pd.read_csv(EGG / case / "output/simulation_results/experiments/run1/experiment_results.csv")
    objectives, status, members, _ = diag.experiment_tables(results, cfg, "npv")
    report = diag.diagnose(objectives, cfg, status=status, per_realization=members)
    rows = re.findall(
        r"- Batch (\d+): n=10, mean=([\d.]+), lower-tail CVaR\(0.25\)=([\d.]+)\.$", report, re.M
    )
    return {int(b): (float(mean), float(cvar)) for b, mean, cvar in rows}


def test_egg_mean_plan_beats_the_cvar_plan_on_the_lower_tail_too():
    """EG1 has no cvar in its config; the comparison adds percentile 0.25 in memory only."""
    mean_cfg = diag.read_yaml(EGG / "EG1_mean_d1/config.yml")
    mean_cfg["optimization"]["cvar"] = {"percentile": 0.25}
    mean_run, cvar_run = (
        egg_cvar("EG1_mean_d1", mean_cfg),
        egg_cvar("EG4_cvar_d1", diag.read_yaml(EGG / "EG4_cvar_d1/config.yml")),
    )
    best_mean = max(mean_run, key=lambda b: mean_run[b][0])
    best_cvar = max(cvar_run, key=lambda b: cvar_run[b][1])
    assert (best_mean, best_cvar) == (9, 14)
    assert mean_run[9] == pytest.approx((26.378528, 19.544705))
    assert cvar_run[14][1] == pytest.approx(19.070058)
    assert mean_run[best_mean][1] > cvar_run[best_cvar][1]


ABORTED = (
    "everest ERROR: Too few realizations are evaluated successfully\n"
    "--- Logging error ---\nTraceback (most recent call last):\n--- Logging error ---\n"
)


def test_run_aborted_from_everest_log(tmp_path):
    output = tmp_path / "output"
    shutil.copytree(EGG / "EG1_mean_d1/output", output)
    (output / "logs/everest.log").write_text(ABORTED)
    out = tmp_path / "report.md"
    args = ["--experiment-results", str(output), "--config", str(EGG / "EG1_mean_d1/config.yml")]
    assert diag.main([*args, "--out", str(out)]) == 0
    line = next(x for x in findings(out.read_text()).splitlines() if "EVD-RUN-ABORTED" in x)
    assert line.startswith(
        "- EVD-RUN-ABORTED (warning): 'Too few realizations are evaluated successfully' in "
    )
    assert "with 2 '--- Logging error ---' line(s)" in line
    assert "full disk, too little memory, and forward-model errors" in line and "ert-diagnose" in line


def test_run_aborted_from_a_run_log_only(tmp_path):
    clean, aborted = tmp_path / "clean.log", tmp_path / "console.log"
    clean.write_text("everest INFO: done\n")
    aborted.write_text(ABORTED)
    folder = EGG / "EG1_mean_d1"
    base = ["--experiment-results", str(folder / "output"), "--config", str(folder / "config.yml")]
    out = tmp_path / "report.md"
    assert diag.main([*base, "--run-log", str(clean), "--out", str(out)]) == 0
    assert "EVD-RUN-ABORTED" not in out.read_text()
    assert diag.main([*base, "--run-log", str(clean), "--run-log", str(aborted), "--out", str(out)]) == 0
    line = next(x for x in findings(out.read_text()).splitlines() if "EVD-RUN-ABORTED" in x)
    assert str(aborted) in line and str(clean) not in line


@pytest.mark.parametrize(
    ("log_text", "trigger"),
    [
        (ABORTED, "'Too few realizations are evaluated successfully' in "),
        (
            "everest ERROR: Experiment failed\nRuntimeError: DAKOTA analysis failed\n",
            "'Experiment failed' in ",
        ),
    ],
)
def test_run_aborted_without_experiment_results_writes_report(tmp_path, capsys, log_text, trigger):
    output = tmp_path / "output"
    (output / "logs").mkdir(parents=True)
    log = output / "logs/everest.log"
    log.write_text("Simulation 3 failed\nSimulation 11 failed\n" + log_text)
    out = tmp_path / "report.md"
    assert diag.main(["--experiment-results", str(output), "--out", str(out)]) == 1
    report = out.read_text()
    line = next(x for x in findings(report).splitlines() if "EVD-RUN-ABORTED" in x)
    assert line.startswith(f"- EVD-RUN-ABORTED (warning): {trigger}{log}")
    assert f"- NOTE: 2 'Simulation N failed' lines in {log}" in report
    assert "aborted without results" in capsys.readouterr().out


def disk_full_output(tmp_path, everest_log_txt=None):
    """An output folder shaped like the aborted Egg run on a full disk: no simulation_results, 12 failures."""
    output = tmp_path / "output"
    (output / "logs").mkdir(parents=True)
    (output / "logs/everest.log").write_text(
        "".join(f"2026-10-16 18:01:50,287 everest ERROR: Simulation {n} failed.\n" for n in range(6, 18))
    )
    if everest_log_txt is not None:
        (output / "logs/everest-log-config-yml-2026-10-16T1758.txt").write_text(everest_log_txt)
    return output


def test_run_aborted_from_the_timestamped_everest_log(tmp_path, capsys):
    output = disk_full_output(tmp_path, ABORTED)
    out = tmp_path / "report.md"
    assert diag.main(["--experiment-results", str(output), "--out", str(out)]) == 1
    report = out.read_text()
    timestamped = output / "logs/everest-log-config-yml-2026-10-16T1758.txt"
    line = next(x for x in findings(report).splitlines() if "EVD-RUN-ABORTED" in x)
    assert line.startswith(
        f"- EVD-RUN-ABORTED (warning): 'Too few realizations are evaluated successfully' in {timestamped}, "
        "with 2 '--- Logging error ---' line(s)."
    )
    assert f"- NOTE: 12 'Simulation N failed' lines in {output / 'logs/everest.log'}" in report
    assert f"- NOTE: 0 'Simulation N failed' lines in {timestamped}" in report
    assert "aborted without results" in capsys.readouterr().out


def test_run_aborted_from_the_timestamped_log_with_results(tmp_path):
    folder = EGG / "EG1_mean_d1"
    output = tmp_path / "output"
    shutil.copytree(folder / "output", output)
    timestamped = output / "logs/everest-log-config-yml-2026-10-16T1758.txt"
    timestamped.write_text(ABORTED)
    out = tmp_path / "report.md"
    args = ["--experiment-results", str(output), "--config", str(folder / "config.yml"), "--out", str(out)]
    diag.main(args)
    line = next(x for x in findings(out.read_text()).splitlines() if "EVD-RUN-ABORTED" in x)
    assert f"in {timestamped}, with 2 '--- Logging error ---' line(s)" in line


def test_timestamped_log_given_as_run_log_is_read_once(tmp_path):
    output = disk_full_output(tmp_path, ABORTED)
    timestamped = output / "logs/everest-log-config-yml-2026-10-16T1758.txt"
    out = tmp_path / "report.md"
    args = ["--experiment-results", str(output), "--run-log", str(timestamped), "--out", str(out)]
    assert diag.main(args) == 1
    report = out.read_text()
    assert report.count(f"lines in {timestamped}") == 1
    assert "with 2 '--- Logging error ---' line(s)" in report


def test_run_aborted_from_simulation_failures_only(tmp_path, capsys):
    output = disk_full_output(tmp_path)
    out = tmp_path / "report.md"
    assert diag.main(["--experiment-results", str(output), "--out", str(out)]) == 1
    report = out.read_text()
    line = next(x for x in findings(report).splitlines() if "EVD-RUN-ABORTED" in x)
    assert line.startswith(
        "- EVD-RUN-ABORTED (warning): the run left no results and 12 simulations failed "
        f"('Simulation N failed' in {output / 'logs/everest.log'})."
    )
    assert "aborted without results" in capsys.readouterr().out


def test_run_aborted_from_one_simulation_failure(tmp_path):
    output = tmp_path / "output"
    (output / "logs").mkdir(parents=True)
    (output / "logs/everest.log").write_text("everest ERROR: Simulation 3 failed.\n")
    out = tmp_path / "report.md"
    assert diag.main(["--experiment-results", str(output), "--out", str(out)]) == 1
    assert "the run left no results and 1 simulation failed (" in out.read_text()


def test_no_experiment_results_and_no_abort_log_keeps_the_error(tmp_path, capsys):
    out = tmp_path / "report.md"
    assert diag.main(["--experiment-results", str(tmp_path), "--out", str(out)]) == 1
    assert not out.exists()
    assert (
        "ERROR: no simulation_results/experiments/<uuid>/experiment_results.csv under"
        in capsys.readouterr().out
    )


def test_run_aborted_missing_run_log_fails_closed(tmp_path):
    folder = EGG / "EG1_mean_d1"
    args = ["--experiment-results", str(folder / "output"), "--config", str(folder / "config.yml")]
    assert diag.main([*args, "--run-log", str(tmp_path / "none.log"), "--out", str(tmp_path / "r.md")]) == 1
