"""Synthetic Everest checks; no engineering binaries."""

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills/everest-config-author-lint/scripts"


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    return {
        "controls": [
            {
                "name": "rate",
                "min": 0,
                "max": 100,
                "initial_guess": 50,
                "perturbation_magnitude": 5,
                "variables": [{"name": "a"}],
            }
        ],
        "objective_functions": [{"name": "npv"}],
        "model": {"realizations": [0, 1, 2, 3, 4]},
        "optimization": {"algorithm": "optpp_q_newton", "max_batch_num": 8, "perturbation_num": 2},
    }


def test_valid_and_traps():
    lint = load("everest_lint")
    cfg = config()
    assert not lint.lint(cfg)
    cfg["optimization"].update(
        cvar={"number_of_realizations": 2}, algorithm="scipy/optimize/slsqp", min_realizations_success=6
    )
    cfg["controls"][0].update(initial_guess=101, perturbation_magnitude=101)
    cfg["objective_functions"].append({"name": "water", "weight": 1})
    findings = lint.lint(cfg)
    messages = str(findings)
    for text in [
        "BEST N",
        "COBYLA",
        "initial_guess",
        "perturbation",
        "negative weight",
        "min_realizations_success",
    ]:
        assert text in messages
    assert sum(f["severity"] == "error" for f in findings) >= 4


def test_cost_formula():
    cost = load("everest_cost")
    result = cost.estimate(config(), sim_hours=2, cores=4, max_running=10)
    assert result["simulations"] == 120
    assert result["core_hours"] == 960
    assert result["wall_hours"] == 24


@pytest.mark.parametrize(
    "section,key,value,message,severity",
    [
        ("control", "min", 100, "min/max", "error"),
        ("control", "max", float("inf"), "min/max", "error"),
        ("control", "initial_guess", -1, "initial_guess", "error"),
        ("control", "perturbation_magnitude", 0, "perturbation_magnitude", "error"),
        ("control", "perturbation_magnitude", 101, "perturbation_magnitude", "error"),
        ("control", "perturbation_magnitude", 0.5, "1-10%", "warning"),
        ("control", "perturbation_magnitude", 20, "1-10%", "warning"),
        ("control", "scale", 0, "scaling", "error"),
        ("control", "scaling", -1, "scaling", "error"),
        ("control", "auto_scale", "yes", "scaling", "error"),
        ("objective", "weight", 0, "weight", "error"),
        ("objective", "normalization", -1, "normalization", "error"),
        ("objective", "scale", float("nan"), "scale", "error"),
        ("objective", "name", "water_cost", "negative weight", "warning"),
        ("optimization", "min_realizations_success", 6, "min_realizations_success", "error"),
        ("optimization", "min_realizations_success", 0, "min_realizations_success", "error"),
        ("optimization", "cvar", {"number_of_realizations": 2}, "selects BEST N", "error"),
        ("optimization", "cvar", {"percentile": 1.5}, "percentile", "error"),
        ("optimization", "algorithm", "scipy/optimize/slsqp", "use COBYLA", "warning"),
        ("optimization", "algorithm", "SLSQP", "use COBYLA", "warning"),
    ],
)
def test_individual_lint_checks(section, key, value, message, severity):
    cfg = config()
    target = {
        "control": cfg["controls"][0],
        "objective": cfg["objective_functions"][0],
        "optimization": cfg["optimization"],
    }[section]
    target[key] = value
    assert any(f["severity"] == severity and message in f["message"] for f in load("everest_lint").lint(cfg))


@pytest.mark.parametrize(
    "kind,item,message",
    [
        ("output_constraints", {"name": "pressure"}, "needs upper/lower"),
        ("output_constraints", {"name": "pressure", "lower_bound": 2, "upper_bound": 1}, "exceeds"),
        ("output_constraints", {"upper_bound": 2}, "needs a name"),
        ("output_constraints", {"name": "pressure", "target": float("inf")}, "finite"),
        ("input_constraints", {"upper_bound": 2, "weights": {"unknown": 1}}, "known controls"),
    ],
)
def test_constraint_errors(kind, item, message):
    cfg = config()
    cfg[kind] = [item]
    assert any(message in f["message"] and f["severity"] == "error" for f in load("everest_lint").lint(cfg))


def test_valid_constraints_scaling_and_cvar():
    cfg = config()
    cfg["input_constraints"] = [{"weights": {"rate.a": 1}, "upper_bound": 100}]
    cfg["output_constraints"] = [{"name": "pressure", "lower_bound": 0, "upper_bound": 10}]
    cfg["controls"][0]["auto_scale"] = True
    cfg["optimization"].update(cvar={"percentile": 0.4}, min_realizations_success=5)
    assert load("everest_lint").lint(cfg) == []


def test_indexed_controls_by_period_list_and_index():
    cfg = config()
    cfg["controls"][0]["variables"] = [
        {"name": "a", "initial_guess": [50, 60, 70]},
        {"name": "b", "index": 1, "initial_guess": 40},
        {"name": "b", "index": 2, "initial_guess": 45},
    ]
    cfg["input_constraints"] = [{"weights": {"rate.a.3": 1, "rate.b.2": 1}, "upper_bound": 100}]
    assert load("everest_lint").lint(cfg) == []
    cfg["input_constraints"] = [{"weights": {"rate.a": 1}, "upper_bound": 100}]
    cfg["controls"][0]["variables"][0]["initial_guess"] = [50, 101]
    cfg["controls"][0]["variables"][2]["index"] = 1
    messages = [f["message"] for f in load("everest_lint").lint(cfg)]
    assert messages == [
        "initial_guess[1] must be within bounds",
        "duplicate wells or variable names",
        "linear input weights must reference known controls",
    ]


@pytest.mark.parametrize("order", [["a", "a"], ["b"], [], [["a"]]])
def test_well_order(order):
    cfg = config()
    cfg["controls"][0]["well_order"] = order
    assert any("well_order" in f["message"] for f in load("everest_lint").lint(cfg))


@pytest.mark.parametrize(
    "variables,message",
    [
        ([{"name": "a", "priority": 1}, {"name": "b", "priority": 1}], "priority duplicates"),
        ([{"name": "a", "priority": 1}, {"name": "b"}], "missing wells"),
        ([{"name": "a", "priority": 1.5}], "integer"),
        ([{"name": "a"}, {"name": "a"}], "duplicate wells"),
    ],
)
def test_priorities(variables, message):
    cfg = config()
    cfg["controls"][0]["variables"] = variables
    assert any(message in f["message"] for f in load("everest_lint").lint(cfg))


def test_order_control_and_large_scale():
    cfg = config()
    cfg["controls"][0].update(name="well_order", variables=[{"name": "a"}, {"name": "b"}])
    assert "duplicate initial" in str(load("everest_lint").lint(cfg))
    cfg["controls"][0]["initial_guess"] = 1.5
    assert "must be integers" in str(load("everest_lint").lint(cfg))
    cfg = config()
    cfg["controls"][0].update(max=1e8, perturbation_magnitude=1e7)
    assert "review scaling" in str(load("everest_lint").lint(cfg))


@pytest.mark.parametrize("members", [[], [0, 0], [-1], [True], [1.5]])
def test_invalid_members(members):
    cfg = config()
    cfg["model"]["realizations"] = members
    assert any(f["severity"] == "error" for f in load("everest_lint").lint(cfg))


def test_queue_and_loader(tmp_path):
    lint = load("everest_lint")
    cfg = config()
    cfg["simulator"] = {"queue_system": {"name": "local"}}
    path = tmp_path / "contract.yml"
    path.write_text(
        'queue_system: LSF\neverest_output_root: ""\neverest_max_sims_per_run: ""\nahm_posterior_case: ""\nverified_pins:\n  ert: ""\n  ropt: ""\n'
    )
    contract = lint.read_contract(path)
    assert contract["verified_pins"] == {"ert": "", "ropt": ""}
    assert "mismatch" in str(lint.lint(cfg, contract))
    assert "mismatch" in str(lint.lint(cfg, {"profile": {"queue": {"system": "LSF"}}}))
    assert not lint.lint(cfg, {"queue_system": "LOCAL"})


def test_lint_cli_and_duplicate_yaml(tmp_path, capsys, monkeypatch):
    lint = load("everest_lint")
    monkeypatch.setattr(lint.shutil, "which", lambda _: "/mock/everest")
    monkeypatch.setattr(lint.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    path = tmp_path / "config.yml"
    out = tmp_path / "report.json"
    path.write_text(yaml.safe_dump(config()))
    assert lint.main([str(path), "--format", "json", "--out", str(out)]) == 0
    assert '"findings": []' in out.read_text()
    path.write_text("controls: []\ncontrols: []\n")
    assert lint.main([str(path)]) == 1
    assert "duplicate YAML key" in capsys.readouterr().out


@pytest.mark.parametrize(
    "stderr,code,severity,message",
    [
        (
            "everest: error: Loading config failed with:\nFound 1 validation error:\n\nunknown_key\n    * Extra inputs are not permitted (type=extra_forbidden)\n",
            2,
            "error",
            "Extra inputs",
        ),
        (
            "config.py:1: ConfigWarning: The `wells` section is deprecated and will be removed in a future version.\n",
            0,
            "warning",
            "wells",
        ),
    ],
)
def test_real_lint_diagnostics(tmp_path, capsys, monkeypatch, stderr, code, severity, message):
    lint = load("everest_lint")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config()))
    monkeypatch.setattr(lint.shutil, "which", lambda _: "/mock/everest")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, code, "", stderr)

    monkeypatch.setattr(lint.subprocess, "run", run)
    assert lint.main([str(path), "--format", "json"]) == (1 if code else 0)
    report = json.loads(capsys.readouterr().out)
    assert calls == [["/mock/everest", "lint", str(path)]]
    assert any(
        f["source"] == "everest" and f["severity"] == severity and message in f["message"]
        for f in report["findings"]
    )


@pytest.mark.parametrize("fails_to_start", [False, True])
def test_real_lint_missing_incomplete(tmp_path, capsys, monkeypatch, fails_to_start):
    lint = load("everest_lint")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config()))
    monkeypatch.setattr(lint.shutil, "which", lambda _: "/mock/everest" if fails_to_start else None)

    def run(*args, **kwargs):
        if fails_to_start:
            raise OSError("cannot start")
        pytest.fail("must not start when executable is missing")

    monkeypatch.setattr(lint.subprocess, "run", run)
    assert lint.main([str(path), "--format", "json"]) == (1 if fails_to_start else 0)
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == ("error" if fails_to_start else "incomplete")
    assert report["findings"][0]["severity"] == ("error" if fails_to_start else "warning")
    assert "real lint" in report["findings"][0]["message"]


def test_cost_cli_contract_limit(tmp_path, capsys):
    cost = load("everest_cost")
    args = ["--batches", "8", "--perturbation-num", "2", "--realizations", "5", "--sim-hours", "2"]
    assert cost.main(args) == 0
    assert '"simulations": 120' in capsys.readouterr().out
    path = tmp_path / "contract.yml"
    path.write_text("everest_max_sims_per_run: 119\n")
    assert cost.main(args + ["--contract", str(path)]) == 1
    assert "exceeds" in capsys.readouterr().out
    path.write_text("everest_max_sims_per_run: 120\n")
    assert cost.main(args + ["--contract", str(path)]) == 0


@pytest.mark.parametrize("value", [0, -1, float("nan")])
def test_cost_invalid_time(value):
    with pytest.raises(ValueError):
        load("everest_cost").estimate(config(), value)


def test_cost_invalid_running():
    with pytest.raises(ValueError, match="max_running"):
        load("everest_cost").estimate(config(), 1, max_running=0)


@pytest.mark.parametrize("members", [[True], [-1], [0, 0], "012", []])
def test_cost_rejects_invalid_members(members):
    cfg = config()
    cfg["model"]["realizations"] = members
    with pytest.raises(ValueError, match="realization list"):
        load("everest_cost").estimate(cfg, 1)


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("controls", [], "nonempty control"),
        ("controls", [{"name": "rate", "variables": []}], "variables"),
        ("controls", [{}], "needs a name"),
        ("objective_functions", [], "objective list"),
    ],
)
def test_missing_required_sections(key, value, message):
    cfg = config()
    cfg[key] = value
    assert message in str(load("everest_lint").lint(cfg))


def test_duplicate_control_and_missing_variable_name():
    cfg = config()
    cfg["controls"] *= 2
    assert "duplicate control name" in str(load("everest_lint").lint(cfg))
    cfg["controls"][0]["variables"] = [{}]
    assert "variable needs a name" in str(load("everest_lint").lint(cfg))


def test_cost_yaml_cli(tmp_path, capsys):
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config()))
    assert load("everest_cost").main(["--config", str(path), "--sim-hours", "2"]) == 0
    assert '"simulations": 120' in capsys.readouterr().out


# Trimmed synthetic messages based on captured lint output; no project identifiers.
UNKNOWN_KEY = """Found 1 validation error:

line: 16, optimization -> max_iterationz
    * Extra inputs are not permitted (type=extra_forbidden)
"""
BOUNDS = """Found 2 validation errors:

line: 4, controls -> 0
    * Value error, Control rate.a must respect min < max (type=value_error)

line: 8, controls -> 1
    * Value error, Variable rate.b must respect initial_guess bounds (type=value_error)
"""
VALID_DEPRECATED = """The `controls.type` field is deprecated and will be removed.

The `wells` section is deprecated and will be removed.

The legacy option is deprecated.

config.yml is valid
"""


@pytest.mark.parametrize(
    "output,rc,errors,warnings",
    [
        (UNKNOWN_KEY, 2, 1, 0),
        (BOUNDS, 2, 2, 0),
        (VALID_DEPRECATED, 0, 0, 3),
        ("Unexpected internal failure", 7, 1, 0),
    ],
)
def test_real_lint_cli(monkeypatch, tmp_path, capsys, output, rc, errors, warnings):
    module = load("everest_lint")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config()))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/synthetic/bin/everest")

    def run(command, **kwargs):
        assert command == ["/synthetic/bin/everest", "lint", str(path)]
        assert kwargs == {"capture_output": True, "text": True, "timeout": 300, "check": False}
        return subprocess.CompletedProcess(command, rc, "", output)

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main([str(path), "--format", "json"]) == bool(errors)
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == ("error" if errors else "pass")
    findings = report["findings"]
    assert all(f["source"] == "everest" for f in findings)
    assert sum(f["severity"] == "error" for f in findings) == errors
    assert sum(f["severity"] == "warning" for f in findings) == warnings
    if output == UNKNOWN_KEY:
        assert findings[0]["path"] == "line: 16, optimization -> max_iterationz"
        assert "extra_forbidden" in findings[0]["message"]
    elif output == BOUNDS:
        assert [f["path"] for f in findings] == ["line: 4, controls -> 0", "line: 8, controls -> 1"]
    elif rc == 7:
        assert "Unexpected internal failure" in findings[0]["message"]
    else:
        assert findings[-1]["severity"] == "info"


def test_missing_everest(monkeypatch, tmp_path, capsys):
    module = load("everest_lint")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config()))
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    assert module.main([str(path), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "incomplete"
    assert report["findings"][0]["severity"] == "warning"
    assert report["findings"][0]["message"] == "everest not found: real lint NOT run (not a pass)"
    assert report["summary"] == "real lint not run"


@pytest.mark.parametrize("executable", ["", "/synthetic/everest", "custom-everest"])
def test_real_lint_explicit_executable(monkeypatch, executable):
    module = load("everest_lint")

    def which(name):
        assert name == (executable or "everest")
        return "/synthetic/everest"

    monkeypatch.setattr(module.shutil, "which", which)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "config.yml is valid\n", ""),
    )
    findings, complete = module.real_lint("config.yml", executable=executable)
    assert findings[0]["severity"] == "info"
    assert complete


def test_timeout(monkeypatch):
    module = load("everest_lint")
    monkeypatch.setattr(module.shutil, "which", lambda name: "/synthetic/everest")

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", timeout)
    findings, summary = module.real_lint("config.yml")
    assert findings[0]["severity"] == "error"
    assert summary == "real lint did not complete"


@pytest.mark.parametrize("mode,code,severity", [("auto", 0, "warning"), ("always", 1, "error")])
def test_missing_modes(monkeypatch, tmp_path, capsys, mode, code, severity):
    module = load("everest_lint")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config()))
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    assert module.main([str(path), "--with-everest", mode, "--format", "json"]) == code
    report = json.loads(capsys.readouterr().out)
    assert report["summary"] == "real lint not run"
    assert report["findings"][0]["severity"] == severity


def test_never_skips_lookup_and_execution(monkeypatch, tmp_path, capsys):
    module = load("everest_lint")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config()))
    monkeypatch.setattr(module.shutil, "which", lambda _: pytest.fail("must not look up executable"))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    assert module.main([str(path), "--with-everest", "never", "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "real lint not run" in report["summary"]
    assert report["status"] == "incomplete"
    assert report["findings"] == []


@pytest.mark.parametrize("executable", ["", "/synthetic/everest", "custom-everest"])
def test_contract_executable(monkeypatch, tmp_path, capsys, executable):
    module = load("everest_lint")
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config()))
    contract_path = tmp_path / "contract.yml"
    contract_path.write_text(f'everest_executable: "{executable}"\n')

    def which(name):
        assert name == (executable or "everest")
        return "/synthetic/everest"

    def run(command, **kwargs):
        assert command == ["/synthetic/everest", "lint", str(path)]
        assert kwargs["timeout"] == 300
        return subprocess.CompletedProcess(command, 0, "config.yml is valid\n", "")

    monkeypatch.setattr(module.shutil, "which", which)
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main([str(path), "--contract", str(contract_path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"] == "real lint run (exit 0)"


def test_valid_stdout_with_deprecation_stderr(monkeypatch):
    module = load("everest_lint")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/synthetic/everest")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, "config.yml is valid\n", "The `wells` section is deprecated.\n"
        ),
    )
    findings, summary = module.real_lint("config.yml")
    assert {f["severity"] for f in findings} == {"info", "warning"}
    assert summary == "real lint run (exit 0)"


def test_real_and_script_findings_merge(monkeypatch, tmp_path, capsys):
    module = load("everest_lint")
    cfg = config()
    cfg["optimization"]["cvar"] = {"number_of_realizations": 2}
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/synthetic/everest")
    monkeypatch.setattr(
        module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, VALID_DEPRECATED, "")
    )
    assert module.main([str(path)]) == 1
    text = capsys.readouterr().out
    assert "WARNING [everest]" in text
    assert "ERROR [script]" in text
    assert "Status: error" in text


def test_list_initial_guess_checks_each_element(tmp_path, capsys):
    cfg = config()
    cfg["controls"][0].update(min=1000, max=12000, perturbation_magnitude=500)
    cfg["controls"][0]["variables"] = [
        {"name": "a", "initial_guess": [6500.0, 6500.0, 6500.0, 6500.0]},
        {"name": "b", "max": 7000, "initial_guess": [6500.0, 6500.0, 8000.0]},
    ]
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    load("everest_lint").main([str(path), "--format", "json", "--with-everest", "never"])
    errors = [f for f in json.loads(capsys.readouterr().out)["findings"] if f["severity"] == "error"]
    assert [(f["path"], f["message"]) for f in errors] == [
        ("controls[0].variables[1]", "initial_guess[2] must be within bounds")
    ]


def test_cost_derivative_free_ignores_perturbations(tmp_path, capsys):
    cfg = config()
    cfg["optimization"] = {"algorithm": "scipy/COBYLA", "max_batch_num": 8}
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert load("everest_cost").main(["--config", str(path), "--sim-hours", "2", "--max-running", "3"]) == 0
    out = capsys.readouterr().out
    result = json.loads(out[out.index("{") : out.index("}") + 1])
    assert result["simulations"] == 40
    assert result["wall_hours"] == 28
    assert result["formula"] == "max_batch_num * n_realizations (derivative-free: perturbation_num ignored)"


def test_cost_gradient_default_perturbation_num():
    cfg = config()
    del cfg["optimization"]["perturbation_num"]
    result = load("everest_cost").estimate(cfg, sim_hours=1)
    assert result["simulations"] == 8 * 6 * 5
    assert result["formula"] == "max_batch_num * (perturbation_num + 1) * n_realizations"


def test_cost_states_upper_bound(capsys):
    args = ["--batches", "8", "--perturbation-num", "2", "--realizations", "5", "--sim-hours", "2"]
    assert load("everest_cost").main(args) == 0
    out = capsys.readouterr().out
    result = json.loads(out[out.index("{") : out.index("}") + 1])
    assert result["simulations_upper_bound"] == result["simulations"] == 120
    assert "120 is an upper bound" in out
    assert "cvar" not in result and "CVaR" not in out


def test_cost_cvar_note(tmp_path, capsys):
    cfg = config()
    cfg["optimization"]["cvar"] = {"percentile": 0.25}
    cfg["model"]["realizations"] = list(range(8))
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert load("everest_cost").main(["--config", str(path), "--sim-hours", "1"]) == 0
    out = capsys.readouterr().out
    result = json.loads(out[out.index("{") : out.index("}") + 1])
    assert result["simulations_upper_bound"] == 8 * 3 * 8
    assert "ceil(0.25 * 8)" in result["cvar_note"]
    assert "CVaR: optimization.cvar is set" in out


def test_cost_observed_counts_simulations(tmp_path, capsys):
    sim = tmp_path / "simulation_folder"
    for batch, members in enumerate([8, 2, 8, 2]):
        for member in range(members):
            folder = sim / f"batch_{batch}" / f"realization_{member}"
            if batch % 2:
                (folder / "evaluation_0").mkdir(parents=True)
            else:
                folder.mkdir(parents=True)
                (folder / "OK").write_text("", encoding="utf-8")
    (sim / "batch_1" / "notes.txt").write_text("", encoding="utf-8")
    before = sorted(p.relative_to(sim) for p in sim.rglob("*"))
    cost = load("everest_cost")
    assert cost.count_observed(sim) == ({"batch_0": 8, "batch_1": 2, "batch_2": 8, "batch_3": 2}, [])
    args = ["--batches", "4", "--perturbation-num", "1", "--realizations", "8", "--sim-hours", "1"]
    assert cost.main(args + ["--observed", str(sim)]) == 0
    out = capsys.readouterr().out
    assert "batch_1: 2" in out
    assert "total: 20" in out
    assert "ratio actual/upper bound: 0.31" in out
    assert "WARNING" not in out
    assert sorted(p.relative_to(sim) for p in sim.rglob("*")) == before


def test_cost_observed_counts_perturbation_folders(tmp_path):
    member = tmp_path / "batch_0" / "realization_0"
    for name in ("evaluation_0", "perturbation_0", "perturbation_1"):
        (member / name).mkdir(parents=True)
    assert load("everest_cost").count_observed(tmp_path) == ({"batch_0": 3}, [])


def test_cost_observed_warns_on_empty_realization_folders(tmp_path, capsys):
    for batch in range(2):
        for member in range(3):
            (tmp_path / f"batch_{batch}" / f"realization_{member}").mkdir(parents=True)
    args = ["--batches", "2", "--perturbation-num", "1", "--realizations", "3", "--sim-hours", "1"]
    assert load("everest_cost").main(args + ["--observed", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "total: 6" in out
    assert "WARNING: 6 realization_* folders are empty, so the run folders were deleted." in out
    assert "assumes 1 simulation per realization" in out
    assert "output_folder" in out


def write_experiment_results(path, rows):
    path.parent.mkdir(parents=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["batch_id", "realization", "perturbation", "npv"])
        writer.writerows(rows)


EXPERIMENT_ROWS = (
    [[0, r, "", 1.0] for r in range(2)]
    + [[0, r, 0, 1.0] for r in range(2)]
    + [[1, r, "", 1.0] for r in range(2)]
    + [[10, 0, 0, 1.0]]
)


@pytest.mark.parametrize("direct", [False, True])
def test_cost_observed_counts_experiment_results_rows(tmp_path, capsys, direct):
    output = tmp_path / "everest_output"
    table = output / "simulation_results" / "experiments" / "abc" / "experiment_results.csv"
    write_experiment_results(table, EXPERIMENT_ROWS)
    (output / "batch_0" / "realization_0").mkdir(parents=True)
    before = table.read_bytes()
    cost = load("everest_cost")
    counts, notes = cost.count_observed(table if direct else output)
    assert counts == {"batch_0": 4, "batch_1": 2, "batch_10": 1}
    assert "lower bound" in notes[0]
    args = ["--batches", "11", "--perturbation-num", "1", "--realizations", "2", "--sim-hours", "1"]
    assert cost.main(args + ["--observed", str(table if direct else output)]) == 0
    out = capsys.readouterr().out
    assert "total: 7" in out
    assert "A failed simulation has no row, so the count is a lower bound" in out
    assert "WARNING" not in out
    assert table.read_bytes() == before


@pytest.mark.parametrize("header", ["realization,npv\n0,1\n", "batch_id,npv\n", "batch_id,npv\nx,1\n"])
def test_cost_observed_csv_fails_closed(tmp_path, capsys, header):
    table = tmp_path / "experiment_results.csv"
    table.write_text(header, encoding="utf-8")
    args = ["--batches", "1", "--perturbation-num", "1", "--realizations", "1", "--sim-hours", "1"]
    assert load("everest_cost").main(args + ["--observed", str(table)]) == 1
    assert "ERROR" in capsys.readouterr().out


@pytest.mark.parametrize("make_batch", [False, True])
def test_cost_observed_fails_closed(tmp_path, capsys, make_batch):
    if make_batch:
        (tmp_path / "other").mkdir()
    folder = tmp_path if make_batch else tmp_path / "missing"
    args = ["--batches", "1", "--perturbation-num", "1", "--realizations", "1", "--sim-hours", "1"]
    assert load("everest_cost").main(args + ["--observed", str(folder)]) == 1
    assert "ERROR" in capsys.readouterr().out
