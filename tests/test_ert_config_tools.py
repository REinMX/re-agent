import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "skills/ert-config-author-lint/scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mapper = load("ert_config_map")
lint = load("check_ert_config")


def run_map(config: Path, *args: str) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = mapper.main([str(config), "--json", *args])
    return code, json.loads(buffer.getvalue())


def problems(report: dict, severity: str = "error") -> str:
    return "\n".join(p["message"] for p in report["problems"] if p["severity"] == severity)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_clean_config(tmp_path):
    write(tmp_path / "inc.ert", "FORWARD_MODEL COPY_FILE(<FROM>=a, <TO>=b)\n")
    config = write(tmp_path / "main.ert", "NUM_REALIZATIONS 3\nINCLUDE inc.ert\n")
    code, report = run_map(config)
    assert code == 0
    assert report["settings"]["NUM_REALIZATIONS"]["value"] == "3"
    assert [f["step"] for f in report["forward_models"]] == ["COPY_FILE"]
    assert len(report["includes"]) == 1


def test_missing_include_is_error(tmp_path):
    config = write(tmp_path / "main.ert", "NUM_REALIZATIONS 1\nINCLUDE nope.ert\n")
    code, report = run_map(config)
    assert code == 1 and "INCLUDE file not found" in problems(report)


def test_comment_and_quoted_dashes(tmp_path):
    config = write(
        tmp_path / "main.ert",
        'NUM_REALIZATIONS 2 -- comment\n-- INCLUDE nope.ert\nWF_X  a "--sumo"\nRUNPATH out/<IENS>\n',
    )
    code, report = run_map(config)
    assert code == 0
    assert report["settings"]["NUM_REALIZATIONS"]["value"] == "2"


def test_define_is_substituted_in_include(tmp_path):
    write(tmp_path / "sub/inc.ert", "NUM_CPU 2\n")
    config = write(tmp_path / "main.ert", "DEFINE <D> sub\nNUM_REALIZATIONS 1\nINCLUDE <D>/inc.ert\n")
    code, report = run_map(config)
    assert code == 0 and report["settings"]["NUM_CPU"]["value"] == "2"


def test_hook_without_loaded_workflow_is_error(tmp_path):
    config = write(tmp_path / "main.ert", "NUM_REALIZATIONS 1\nHOOK_WORKFLOW ghost PRE_SIMULATION\n")
    code, report = run_map(config)
    assert code == 1 and "no LOAD_WORKFLOW loads it" in problems(report)


def test_hook_with_loaded_workflow_passes(tmp_path):
    write(tmp_path / "wf/xhook_a", "")
    config = write(
        tmp_path / "main.ert",
        "NUM_REALIZATIONS 1\nLOAD_WORKFLOW wf/xhook_a\nHOOK_WORKFLOW xhook_a PRE_SIMULATION\n",
    )
    assert run_map(config)[0] == 0


def test_circular_include(tmp_path):
    write(tmp_path / "a.ert", "INCLUDE b.ert\n")
    write(tmp_path / "b.ert", "INCLUDE a.ert\n")
    config = write(tmp_path / "main.ert", "NUM_REALIZATIONS 1\nINCLUDE a.ert\n")
    code, report = run_map(config)
    assert code == 1 and "circular INCLUDE" in problems(report)


def test_config_path_is_main_config_folder_inside_includes(tmp_path):
    """Drogon relies on this: <CONFIG_PATH> in an included file is the folder of the main config."""
    write(tmp_path / "ecalc/model/x.tmpl", "")
    write(
        tmp_path / "ert/input/config/inc.ert", "RUN_TEMPLATE <CONFIG_PATH>/../../ecalc/model/x.tmpl out.yml\n"
    )
    config = write(tmp_path / "ert/model/main.ert", "NUM_REALIZATIONS 1\nINCLUDE ../input/config/inc.ert\n")
    code, report = run_map(config)
    assert code == 0, report["problems"]


def test_generated_file_is_warning_with_pre_experiment_hook(tmp_path):
    write(tmp_path / "wf", "")
    config = write(
        tmp_path / "main.ert",
        "NUM_REALIZATIONS 1\nLOAD_WORKFLOW wf\nHOOK_WORKFLOW wf PRE_EXPERIMENT\n"
        "RUN_TEMPLATE <CONFIG_PATH>/fmuconfig/output/g.tmpl out.yml\n",
    )
    code, report = run_map(config)
    assert code == 0 and "ERT stops until the file exists" in problems(report, "warning")


def test_generated_file_is_error_without_hook_or_flag(tmp_path):
    config = write(
        tmp_path / "main.ert",
        "NUM_REALIZATIONS 1\nRUN_TEMPLATE <CONFIG_PATH>/fmuconfig/output/g.tmpl out.yml\n",
    )
    assert run_map(config)[0] == 1
    assert run_map(config, "--generated", "fmuconfig/output")[0] == 0


def test_missing_num_realizations(tmp_path):
    config = write(tmp_path / "main.ert", "RUNPATH out/<IENS>\n")
    code, report = run_map(config)
    assert code == 1 and "NUM_REALIZATIONS" in problems(report)


def test_not_a_file_is_usage_error(tmp_path):
    assert mapper.main([str(tmp_path / "none.ert")]) == 2


def test_mapper_writes_nothing(tmp_path):
    config = write(tmp_path / "main.ert", "NUM_REALIZATIONS 1\nINCLUDE nope.ert\n")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    run_map(config)
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


DROGON = Path(os.environ.get("FMU_DROGON", Path.home() / "fmu-drogon"))


@pytest.mark.skipif(not (DROGON / "ert/model").is_dir(), reason="fmu-drogon not found. Set FMU_DROGON.")
@pytest.mark.parametrize("name", ["drogon_ahm", "drogon_pred_ref", "drogon_pred_op5", "drogon_design"])
def test_drogon_configs_have_no_errors(name):
    code, report = run_map(DROGON / "ert/model" / f"{name}.ert")
    assert code == 0, problems(report)
    assert int(report["settings"]["NUM_REALIZATIONS"]["value"]) > 0


def test_lint_script_no_ert_or_plugin_failure_is_exit_2(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "ert", None)  # makes 'import ert' raise ImportError
    monkeypatch.delitem(sys.modules, "ert.plugins", raising=False)
    config = write(tmp_path / "c.ert", "NUM_REALIZATIONS 1\n")
    assert lint.main([str(config)]) == 2


def ert_ready():
    pytest.importorskip("ert")
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]


def test_lint_script_valid_and_broken_configs(tmp_path, capsys):
    ert_ready()
    good = write(tmp_path / "good.ert", "NUM_REALIZATIONS 1\n")
    broken = write(tmp_path / "broken.ert", "NUM_REALIZATIONS 1\nINCLUDE missing_include.ert\n")
    assert lint.main([str(good)]) == 0
    assert lint.main([str(broken)]) == 1
    assert "missing_include.ert" in capsys.readouterr().out
    assert lint.main([str(tmp_path / "absent.ert")]) == 1


def test_lint_script_bad_accept_argument(tmp_path):
    ert_ready()
    good = write(tmp_path / "good.ert", "NUM_REALIZATIONS 1\n")
    assert lint.main([str(good), "--accept", "no-equals-sign"]) == 2
