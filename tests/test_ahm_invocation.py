"""Captured launches prove the checked command is the command executed."""

import argparse
import dataclasses
import shlex
from types import SimpleNamespace

import pytest
from test_ahm_execution_readiness import screened_case  # noqa: F401
from test_long_run import long_run


@pytest.fixture
def launcher(screened_case, tmp_path, monkeypatch):  # noqa: F811
    pre, config, screening = screened_case
    calls = []

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=424242, returncode=None)

    monkeypatch.setattr(long_run.subprocess, "Popen", popen)

    def run(command, **extra):
        args = argparse.Namespace(
            status=str(tmp_path / "STATUS.md"),
            name="hm",
            log=str(tmp_path / "run.log"),
            cwd=str(config.parent),
            command=list(command),
            screening=str(screening),
            no_preflight=None,
            allow_members=None,
        )
        for key, value in extra.items():
            setattr(args, key, value)
        return long_run.cmd_start(args)

    return pre, config, screening, calls, run


@pytest.mark.parametrize(
    "tail",
    [
        ("--weights", "nan,2,1"),
        ("--weights", "0,0"),
        ("--weights", "1e-320"),
        ("--realizations", "0-9"),
        ("--realizations", "0-100"),
        ("--realizations", "0,0"),
        ("--realizations", "0-50,50-99"),
        ("--realizations", "9-0"),
        ("--realizations", "-1"),
        ("--realizations", ""),
        ("--realizations", "0,,1"),
        ("--unknown", "x"),
        ("--weights", "4,2,1", "--weights=4,2,1"),
        ("--disable-monitoring", "--disable-monitoring"),
        ("--weights",),
        ("--realizations", "--disable-monitoring"),
        ("extra.ert",),
        (),
    ],
)
def test_launcher_refuses_actual_invalid_arguments(launcher, tail, capsys):
    _, config, _, calls, run = launcher
    weights = () if not tail or any(word.startswith("--weights") for word in tail) else ("--weights", "4,2,1")
    assert run(("ert", "es_mda", config.name, *weights, *tail)) == 1
    assert calls == []
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        ("ert", "enif", "case.ert"),
        ("ert", "manual_update", "case.ert"),
        ("ert", "iterative_ensemble_smoother", "case.ert"),
        ("ert", "unknown", "case.ert"),
        ("python", "-m", "ert", "es_mda", "case.ert"),
        ("bash", "-c", "ert es_mda case.ert"),
        ("sh", "-c", "exec ert ensemble_smoother case.ert"),
        ("ert",),
    ],
)
def test_launcher_refuses_uncertain_managed_dispatch(launcher, command, capsys):
    _, _, _, calls, run = launcher
    assert run(command) == 1
    assert calls == []
    assert "error:" in capsys.readouterr().err


def test_launcher_preserves_exact_safe_argv_cwd_and_receipt(launcher):
    _, config, _, calls, run = launcher
    renamed = config.with_name("model config")
    config.rename(renamed)
    argv = (
        "/opt/bin/ert",
        "--logdir=logs here",
        "es_mda",
        "--verbose",
        "--weights=4,2,1",
        "--target-ensemble",
        "a b;%d",
        renamed.name,
        "--realizations=0-99",
        "--disable-monitoring",
        "--color-always",
        "--port-range",
        "1024-2048",
        "--experiment-name=trial",
    )
    assert run(argv) == 0
    launched, kwargs = calls[0]
    assert launched == argv and isinstance(launched, tuple)
    assert kwargs["cwd"] == config.parent.resolve()
    assert kwargs["shell"] is False
    records = long_run.read_status(config.parent / "STATUS.md")
    assert records[-1].cmd == argv
    text = (config.parent / "STATUS.md").read_text()
    assert "AHM compatibility passed; native ERT loading unverified" in text
    assert f"cwd={shlex.quote(str(config.parent))}" in text


def test_selection_count_override_does_not_hide_invalid_members(launcher):
    pre, config, screening, calls, run = launcher
    argv = ("ert", "ensemble_smoother", config.name, "--realizations", "4,7-9")
    assert run(argv, allow_members="small diagnostic") == 0
    result = pre.check(argv=argv, cwd=config.parent, screening=screening, allow_members="small diagnostic")
    assert result.selected_members == (4, 7, 8, 9)
    assert result.members == 100
    assert result.argv == argv
    assert result.verification == "compatibility_only" and result.native_status == "unverified"
    assert shlex.split(result.command) == list(argv)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.argv = ("false",)
    assert isinstance(result.items, tuple) and isinstance(result.sampled, tuple)
    assert run((*argv[:-1], "0-100"), allow_members="not a bounds waiver") == 1
    assert len(calls) == 1


def test_omission_selects_configured_members_and_es_has_no_weights(launcher):
    pre, config, screening, _, _ = launcher
    argv = ("ert", "ensemble_smoother", config.name)
    result = pre.check(argv=argv, cwd=config.parent, screening=screening)
    assert result.ready and result.selected_members == tuple(range(100))
    bad = pre.check(argv=(*argv, "--weights", "4,2,1"), cwd=config.parent, screening=screening)
    assert not bad.ready and bad.argv is None and bad.command is None


def test_design_matrix_selection_is_explicitly_unverified(launcher):
    pre, config, screening, _, _ = launcher
    with config.open("a") as stream:
        stream.write("DESIGN_MATRIX selection.xlsx\n")
    result = pre.check(argv=("ert", "ensemble_smoother", config.name), cwd=config.parent, screening=screening)
    assert not result.ready and result.argv is None
    assert result.selected_members == ()
    assert any("DESIGN_MATRIX" in item.message for item in result.items)


@pytest.mark.parametrize("field", ["allow_members", "no_preflight"])
def test_blank_override_reason_refuses(launcher, field):
    _, config, _, calls, run = launcher
    assert run(("ert", "ensemble_smoother", config.name), **{field: "   "}) == 1
    assert calls == []


def test_unchecked_override_is_audited_not_validated(launcher):
    _, config, _, calls, run = launcher
    argv = ("ert", "es_mda", config.name, "--weights", "nan")
    assert run(argv, no_preflight="operator diagnostic") == 0
    text = (config.parent / "STATUS.md").read_text()
    assert "UNCHECKED" in text and "unchecked override" in text
    assert "operator diagnostic" in text and shlex.join(argv) in text
    assert "compatibility passed" not in text
    assert calls[0][0] == argv


@pytest.mark.parametrize("count", [0, -1])
def test_count_override_cannot_validate_empty_or_negative_ensemble(launcher, count):
    _, config, _, calls, run = launcher
    config.write_text(config.read_text().replace("NUM_REALIZATIONS 100", f"NUM_REALIZATIONS {count}"))
    assert run(("ert", "ensemble_smoother", config.name), allow_members="count only") == 1
    assert calls == []


def test_unknown_short_option_is_not_a_config_positional(launcher):
    _, config, _, calls, run = launcher
    (config.parent / "-x").write_text(config.read_text())
    assert run(("ert", "ensemble_smoother", "-x")) == 1
    assert calls == []


def test_launch_inputs_survive_namespace_mutation_after_validation(launcher, monkeypatch):
    _, config, _, calls, run = launcher
    gate = long_run.update_gate

    def mutate(args, status):
        result = gate(args, status)
        args.command[:] = ["false"]
        args.cwd = "/not-the-checked-cwd"
        return result

    monkeypatch.setattr(long_run, "update_gate", mutate)
    argv = ("ert", "ensemble_smoother", config.name)
    assert run(argv) == 0
    assert calls[0][0] == argv
    assert calls[0][1]["cwd"] == config.parent
    assert long_run.read_status(config.parent / "STATUS.md")[-1].cmd == argv


def test_unmanaged_commands_remain_outside_guarantee(launcher):
    pre, config, _, _, run = launcher
    for argv in (("flow", "case.DATA"), ("ert", "ensemble_experiment", config.name), ("ert", "--version")):
        assert pre.check(argv=argv, cwd=config.parent, screening=None) is None
        assert run(argv) == 0
