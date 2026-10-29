"""Schema version 2 contracts, profiles, placeholders, and version checks. Synthetic values only."""

import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("doctor_v2", ROOT / "tools/doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)
SCHEMA = json.loads((ROOT / "site.contract.schema.json").read_text())
EXAMPLES = sorted((ROOT / "profiles").glob("*.example.yml"))


@pytest.fixture(autouse=True)
def no_profile_env(monkeypatch):
    monkeypatch.delenv("RE_AGENT_PROFILE", raising=False)


def filled_home(tmp_path: Path) -> dict:
    """The home example with every placeholder replaced by a synthetic value."""
    data = yaml.safe_load((ROOT / "profiles/home.example.yml").read_text())
    home = data["profiles"]["home"]
    (tmp_path / "scratch").mkdir()
    (tmp_path / "out").mkdir()
    home.update(
        model_endpoint="synthetic-endpoint",
        endpoint_approved=True,
        python=sys.executable,
        scratch_root=str(tmp_path / "scratch"),
        output_root=str(tmp_path / "out"),
    )
    home["ert"]["version"] = ""
    home["simulator"]["version"] = "2099.01"
    data.update(project="synthetic", eclbase="eclipse/model/SYNTH", expected_realizations=3)
    return data


def write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "contract.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def run_json(capsys, *argv):
    code = doctor.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_examples_validate_structurally(example):
    data = doctor.read_contract_v2(example)
    assert doctor.validate(data, SCHEMA) == []
    assert doctor.builtin_validate(data, SCHEMA, SCHEMA) == []


def test_example_files_hold_no_workspace_paths():
    for path in [*EXAMPLES, ROOT / "site.contract.example.yml"]:
        assert "/workspace" not in path.read_text()


def test_company_example_values_are_placeholders_or_empty():
    profile = yaml.safe_load((ROOT / "profiles/company.example.yml").read_text())["profiles"]["company"]
    allowed = {"ert", "komodo", "none", "", None}

    def leaves(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from leaves(item)
        else:
            yield value

    for value in leaves(profile):
        assert isinstance(value, bool) or value == {} or doctor.is_placeholder(value) or value in allowed, (
            value
        )


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda d: d["profiles"]["home"]["queue"].update(system="PBS"), "queue.system"),
        (lambda d: d["profiles"]["home"]["simulator"].update(name="flow"), "simulator.name"),
        (lambda d: d["profiles"]["home"].update(activate_shell="zsh"), "activate_shell"),
        (lambda d: d.update(p10_p90="median"), "p10_p90"),
        (lambda d: d.pop("eclbase"), "eclbase"),
        (lambda d: d["profiles"]["home"].update(typo_key=1), "typo_key"),
        (lambda d: d["profiles"]["home"].update(endpoint_approved="true"), "endpoint_approved"),
        (lambda d: d.update(schema_version=3), "schema_version"),
    ],
)
def test_schema_rejects_bad_values(tmp_path, change, fragment):
    data = filled_home(tmp_path)
    change(data)
    builtin = doctor.builtin_validate(data, SCHEMA, SCHEMA)
    assert any(fragment in error for error in builtin), builtin
    assert doctor.validate(data, SCHEMA)


def test_placeholders_are_unfilled(capsys):
    code, report = run_json(
        capsys, "--contract", str(ROOT / "profiles/company.example.yml"), "--mode", "agent"
    )
    assert code == 1
    assert report["schema_version"] == 2 and report["profile"] == "company"
    assert "profiles.company.python" in report["unfilled"]
    assert "eclbase" in report["unfilled"]
    assert "vectors.required_summary[0]" in report["unfilled"]
    assert "runpath.pattern" not in report["unfilled"]  # <IENS> and <ITER> are ERT magic strings
    assert "unfilled: profiles.company.python" in report["errors"]
    assert not any(e.startswith("schema:") for e in report["errors"])


def test_placeholder_detection():
    assert doctor.is_placeholder("<ASK: a value>")
    assert doctor.is_placeholder("/site/<FIELD>/scratch")
    assert not doctor.is_placeholder("realization-<IENS>/iter-<ITER>")
    assert not doctor.is_placeholder("/opt/venv/bin/python")
    assert doctor.find_placeholders({"a": ["x", "<y>"], "b": {"c": "<z>"}}, "") == ["a[1]", "b.c"]


def test_filled_home_profile_is_ready_in_agent_mode(tmp_path, capsys):
    path = write(tmp_path, filled_home(tmp_path))
    code, report = run_json(capsys, "--contract", str(path), "--mode", "agent")
    assert report["errors"] == []
    assert code == 0
    assert report["profile"] == "home"
    assert report["simulator"] == {"name": "FLOW", "declared_version": "2099.01", "checked": False}


@pytest.mark.skipif(sys.platform != "linux", reason="execution preflight belongs on Linux")
def test_filled_home_profile_execution_mode(tmp_path, capsys):
    data = filled_home(tmp_path)
    data["profiles"]["home"]["output_root"] = "${OUT_FOR_TEST}"
    path = write(tmp_path, data)
    code, report = run_json(capsys, "--contract", str(path), "--mode", "execution")
    assert any("unresolved variable $OUT_FOR_TEST" in e for e in report["errors"])


def test_profile_selection_order(tmp_path, monkeypatch):
    data = {"active_profile": "a", "profiles": {"a": {"x": 1}, "b": {"x": 2}, "c": {"x": 3}}}
    assert doctor.select_profile(data, None)[0] == "a"
    monkeypatch.setenv("RE_AGENT_PROFILE", "b")
    assert doctor.select_profile(data, None)[0] == "b"
    assert doctor.select_profile(data, "c") == ("c", {"x": 3})


@pytest.mark.parametrize(
    "data",
    [
        {"active_profile": "missing", "profiles": {"a": {}}},
        {"profiles": {"a": {}}},
        {"active_profile": "a", "profiles": {}},
    ],
)
def test_unknown_or_missing_profile_raises(data):
    with pytest.raises(doctor.ProfileError):
        doctor.select_profile(data, None)


def test_unknown_profile_exits_2(tmp_path, capsys, monkeypatch):
    path = write(tmp_path, filled_home(tmp_path))
    code, report = run_json(capsys, "--contract", str(path), "--mode", "agent", "--profile", "nowhere")
    assert code == 2
    assert any("unknown profile 'nowhere'" in e and "home" in e for e in report["errors"])
    monkeypatch.setenv("RE_AGENT_PROFILE", "elsewhere")
    assert doctor.main(["--contract", str(path), "--mode", "agent"]) == 2


def test_v2_duplicate_key_is_an_error(tmp_path):
    path = tmp_path / "contract.yml"
    path.write_text("schema_version: 2\nprofiles: {}\nprofiles: {}\n")
    with pytest.raises(ValueError, match="duplicate"):
        doctor.read_contract_v2(path)


def test_v2_without_pyyaml_is_an_error(tmp_path, monkeypatch, capsys):
    path = write(tmp_path, filled_home(tmp_path))
    monkeypatch.setitem(sys.modules, "yaml", None)
    code, report = run_json(capsys, "--contract", str(path), "--mode", "agent")
    assert code == 1
    assert any("needs PyYAML" in e for e in report["errors"])


def test_builtin_validator_is_used_without_jsonschema(tmp_path, monkeypatch):
    data = filled_home(tmp_path)
    data["profiles"]["home"]["queue"]["system"] = "PBS"
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    assert any("queue.system" in e for e in doctor.validate(data, SCHEMA))


def test_lsf_profile_needs_a_queue_name(tmp_path):
    profile = filled_home(tmp_path)["profiles"]["home"]
    profile["queue"].update(system="SLURM", name="")
    assert any("queue.name" in e for e in doctor.check_profile("home", profile, "agent"))


def test_expand_uses_profile_roots_then_environment(monkeypatch):
    monkeypatch.setenv("USER", "tester")
    profile = {"output_root": "/opt/out/$USER"}
    assert doctor.expand("${output_root}/reports", profile) == "/opt/out/tester/reports"
    monkeypatch.delenv("NOT_SET_FOR_TEST", raising=False)
    with pytest.raises(ValueError, match="NOT_SET_FOR_TEST"):
        doctor.expand("/x/$NOT_SET_FOR_TEST", profile)


# Version checks.


def fake_lookup(installed):
    def lookup(name):
        if name not in installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return installed[name]

    return lookup


@pytest.mark.parametrize(
    ("installed", "expected", "match"),
    [
        ("14.0.7", "14.0.7", True),
        ("14.0.7", "14.0", True),
        ("14.0.7", "14", True),
        ("14.0.7", "14.*", True),
        ("140.1", "14", False),
        ("14.1.0", "14.0", False),
        ("26.1.0+local", "26.1.0", True),
    ],
)
def test_version_prefix_match(installed, expected, match):
    assert doctor.version_matches(installed, expected) is match


def test_version_mismatch_is_an_error_in_execution_mode():
    lookup = fake_lookup({"ert": "13.2.0", "res2df": "1.3.0"})
    expected = {"ert": "14.0", "res2df": "1.3", "resdata": "5", "xtgeo": "<ASK>"}
    errors, warnings, report = doctor.check_versions(expected, "execution", lookup)
    assert errors == ["version: ert 13.2.0 does not match the contract version 14.0"]
    assert any("resdata is not installed" in w for w in warnings)
    assert report["res2df"]["match"] is True
    assert report["xtgeo"] == {"expected": None, "installed": None, "match": None}


def test_version_mismatch_is_a_warning_in_agent_mode():
    errors, warnings, _ = doctor.check_versions({"ert": "14"}, "agent", fake_lookup({"ert": "13.0"}))
    assert errors == []
    assert warnings == ["version: ert 13.0 does not match the contract version 14"]


def test_expected_versions_from_contract():
    data = {"versions": {"res2df": "1.3", "ert": "13"}}
    profile = {"ert": {"version": "14.0"}, "versions": {"resdata": "5.1"}}
    assert doctor.expected_versions(data, profile) == {"res2df": "1.3", "ert": "13", "resdata": "5.1"}
    assert doctor.expected_versions(data, {"ert": {"version": "14.0"}})["ert"] == "13"
    assert doctor.expected_versions({}, {"ert": {"version": "14.0"}}) == {"ert": "14.0"}


def test_main_version_check_uses_installed_metadata(tmp_path, capsys, monkeypatch):
    data = filled_home(tmp_path)
    data["profiles"]["home"]["ert"]["version"] = "99.0"
    data["profiles"]["home"]["versions"] = {"pandas": "1.0"}
    path = write(tmp_path, data)
    monkeypatch.setattr(importlib.metadata, "version", fake_lookup({"ert": "14.0.7", "pandas": "1.0.5"}))
    code, report = run_json(capsys, "--contract", str(path), "--mode", "agent")
    assert code == 0
    assert report["version_checks"]["ert"] == {"expected": "99.0", "installed": "14.0.7", "match": False}
    assert report["version_checks"]["pandas"]["match"] is True
    assert any("ert 14.0.7 does not match" in w for w in report["warnings"])


def test_report_has_repo_and_skill_versions(tmp_path, capsys):
    code, report = run_json(capsys, "--contract", str(tmp_path / "absent.yml"), "--mode", "agent")
    assert code == 1
    assert report["repo_version"] == (ROOT / "VERSION").read_text().strip()
    assert set(report["skill_versions"]) == {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    assert all(report["skill_versions"].values())


def test_text_report_prints_versions(tmp_path, capsys):
    doctor.main(["--contract", str(tmp_path / "absent.yml"), "--mode", "agent"])
    out = capsys.readouterr().out
    assert "Repository version: " in out
    assert "skill reservoir-engineer: " in out


def test_v1_agent_mode_warns_when_interpreter_differs(tmp_path, capsys):
    path = tmp_path / "contract.yml"
    path.write_text('python: "/opt/other/bin/python"\n')
    _, report = run_json(capsys, "--contract", str(path), "--mode", "agent")
    assert report["schema_version"] == 1
    assert any(w.startswith("interpreter: doctor runs with") for w in report["warnings"])
    assert report["contract_python"] == "/opt/other/bin/python"
