"""Shared site-contract loader (schema v2.1): lookup, profiles, merge, expansion, aliases."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from synth_fmu import CONTRACT_LIB_DIR, PY, need, profile_block, project_block, yaml_dump

pytestmark = pytest.mark.skipif(importlib.util.find_spec("yaml") is None, reason="PyYAML not installed")

if str(CONTRACT_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(CONTRACT_LIB_DIR))
import contract_lib as cl  # noqa: E402
from contract_lib import ContractError  # noqa: E402


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_dump(data), encoding="utf-8")
    return path


def _user(tmp: Path, profiles: dict | None = None, active: str | None = "home") -> Path:
    data: dict = {"schema_version": 2}
    if active:
        data["active_profile"] = active
    data["profiles"] = profiles or {
        "home": profile_block(tmp, scratch_root=(tmp / "data/home/scratch").as_posix(), output_root="/out"),
        "work": profile_block(
            tmp,
            scratch_root=(tmp / "data/work").as_posix() + "/$RE_TEST_USER",
            output_root="${scratch_root}/out",
            activate="source /opt/env/enable.csh",
            activate_shell="tcsh",
            activate_bash="source /opt/env/enable",
            queue={"system": "LSF", "name": "normal", "max_running": 50},
        ),
    }
    return _write(tmp / "home/.config/re-agent/site.contract.yml", data)


def _project(tmp: Path, **over) -> Path:
    proj = project_block(tmp)
    proj["paths"] = {
        "deliverables": "${output_root}/toy",
        "scratch": "${scratch_root}/toy_ens",
        "project": "project",
    }
    proj.update(over)
    return _write(tmp / "proj/site.contract.yml", proj)


def _load(tmp: Path, *, contract=None, profile=None, env=None, cwd=None, **kw):
    env = {"RE_TEST_USER": "alice", **(env or {})}
    return cl.load_contract(contract, profile, cwd=cwd or tmp / "proj", env=env, home=tmp / "home", **kw)


# ---------------------------------------------------------------- lookup order
def test_lookup_order(tmp_path: Path):
    user = _user(tmp_path)
    proj = _project(tmp_path)
    other = _write(
        tmp_path / "other.yml", {"profiles": {"x": profile_block(tmp_path)}, "active_profile": "x"}
    )
    # 1) --contract wins; a file with profiles stands alone (cwd file not read)
    assert cl.locate_files(other, cwd=proj.parent, env={}, home=tmp_path / "home") == (other, None)
    # --contract without profiles overlays the user-level file
    assert cl.locate_files(proj, cwd=tmp_path, env={}, home=tmp_path / "home") == (user, proj)
    # 2) env RE_AGENT_CONTRACT, with ./site.contract.yml as overlay
    assert cl.locate_files(
        None, cwd=proj.parent, env={"RE_AGENT_CONTRACT": str(other)}, home=tmp_path / "home"
    ) == (
        other,
        proj,
    )
    # 3) ./site.contract.yml over 4) the user-level file
    assert cl.locate_files(None, cwd=proj.parent, env={}, home=tmp_path / "home") == (user, proj)
    # 4) only the user-level file
    assert cl.locate_files(None, cwd=tmp_path / "empty", env={}, home=tmp_path / "home") == (user, None)


def test_lookup_nothing_found_lists_order(tmp_path: Path):
    with pytest.raises(ContractError, match="Checked, in order"):
        cl.locate_files(None, cwd=tmp_path, env={}, home=tmp_path / "nohome")


def test_env_contract_missing_file_is_error(tmp_path: Path):
    with pytest.raises(ContractError, match="missing file"):
        cl.locate_files(
            None, cwd=tmp_path, env={"RE_AGENT_CONTRACT": str(tmp_path / "nope.yml")}, home=tmp_path
        )


# ---------------------------------------------------------------- profile selection
def test_profile_selection_arg_env_active(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path)
    assert _load(tmp_path).profile_name == "home"  # active_profile
    assert _load(tmp_path, env={"RE_AGENT_PROFILE": "work"}).profile_name == "work"  # env
    assert _load(tmp_path, profile="home", env={"RE_AGENT_PROFILE": "work"}).profile_name == "home"  # arg


def test_unknown_profile_is_error(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path)
    with pytest.raises(ContractError, match="unknown profile 'nope'") as err:
        _load(tmp_path, profile="nope")
    assert err.value.exit_code == 2


def test_no_profile_selected_is_error(tmp_path: Path):
    _user(tmp_path, active=None)
    _project(tmp_path)
    with pytest.raises(ContractError, match="no profile selected"):
        _load(tmp_path)


def test_cli_unknown_profile_exits_2(tmp_path: Path):
    need("yaml")
    user = _user(tmp_path)
    proc = subprocess.run(
        [PY, str(CONTRACT_LIB_DIR / "contract_lib.py"), "--contract", str(user), "--profile", "nope"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "unknown profile" in proc.stderr


# ---------------------------------------------------------------- merge
def test_merge_dicts_deep_lists_replace(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path, vectors={"required_summary": ["FOPT"], "headline": ["FOPT"]})
    c = _load(tmp_path)
    # profile keys lifted next to project keys
    assert c["simulator"]["name"] == "FLOW" and c["queue"]["system"] == "LOCAL"
    # lists replace (the project list is used as is)
    assert c.required_summary == ["FOPT"] and c.vectors("headline") == ["FOPT"]
    assert c.sources[-1].endswith("proj/site.contract.yml")
    assert cl.deep_merge({"a": {"b": 1, "c": [1, 2]}}, {"a": {"c": [3]}}) == {"a": {"b": 1, "c": [3]}}


def test_project_profiles_block_ignored_unless_allowed(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path, profiles={"home": profile_block(tmp_path, scratch_root="/evil")})
    c = _load(tmp_path)
    assert c["scratch_root"] == (tmp_path / "data/home/scratch").as_posix()
    assert any("ignored" in w for w in c.warnings)
    c2 = _load(tmp_path, env={"RE_AGENT_ALLOW_PROJECT_PROFILES": "1"})
    assert c2["scratch_root"] == "/evil"


# ---------------------------------------------------------------- expansion
def test_expansion_env_and_profile_roots(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path)
    c = _load(tmp_path, profile="work")
    work = (tmp_path / "data/work").as_posix()
    assert c["scratch_root"] == f"{work}/alice"
    assert c["output_root"] == f"{work}/alice/out"
    assert c.path("scratch") == Path(f"{work}/alice/toy_ens")
    assert c.path("deliverables") == Path(f"{work}/alice/out/toy")
    # activate / runpath.pattern are not expanded
    assert c["activate"] == "source /opt/env/enable.csh"
    assert c["runpath"]["pattern"] == "realization-<IENS>/iter-<ITER>"


def test_expansion_unresolved_is_error(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path, paths={"deliverables": "$NOT_SET_ANYWHERE/out"})
    with pytest.raises(ContractError, match="NOT_SET_ANYWHERE"):
        _load(tmp_path)


def test_expansion_double_dollar_is_literal():
    assert cl.expand_string("a$$b/${X}/$X", {}, {"X": "x"}) == "a$b/x/x"
    with pytest.raises(ContractError):
        cl.expand_string("${scratch_root}/a", {}, {})
    assert cl.expand_string("${scratch_root}/a", {"scratch_root": "/s"}, {}) == "/s/a"


def test_relative_paths_resolve_against_root(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path)
    c = _load(tmp_path)
    assert c.path("root") == (tmp_path / "proj").resolve()
    assert c.path("project") == (tmp_path / "proj").resolve() / "project"


# ---------------------------------------------------------------- aliases / legacy
def test_lsf_queue_alias(tmp_path: Path):
    prof = profile_block(tmp_path, queue={"system": "LSF", "lsf_queue": "short"})
    _user(tmp_path, profiles={"home": prof})
    _project(tmp_path)
    c = _load(tmp_path)
    assert c["queue"]["name"] == "short" and "lsf_queue" not in c["queue"]
    prof["queue"] = {"system": "LSF", "lsf_queue": "short", "name": "normal"}
    _user(tmp_path, profiles={"home": prof})
    c = _load(tmp_path)
    assert c["queue"]["name"] == "normal"
    assert any("lsf_queue" in w for w in c.warnings)


def test_bad_enums_are_errors(tmp_path: Path):
    _user(tmp_path, profiles={"home": profile_block(tmp_path, queue={"system": "PBS"})})
    _project(tmp_path)
    with pytest.raises(ContractError, match="queue.system"):
        _load(tmp_path)
    _user(tmp_path)
    _project(tmp_path, p10_p90="optimistic")
    with pytest.raises(ContractError, match="p10_p90"):
        _load(tmp_path)


def test_v1_flat_maps_to_legacy_profile(tmp_path: Path):
    flat = _write(
        tmp_path / "flat/site.contract.yml",
        {
            "project": "toy",
            "python": "/opt/py/bin/python",
            "simulator": "FLOW",
            "simulator_version": "2025.04",
            "queue_system": "LSF",
            "lsf_queue": "normal",
            "percentile_convention": "exceedance",
            "required_summary": ["FOPT"],
            "paths": {"eclbase": "eclipse/model/TOY", "deliverables": "out", "scratch_root": "/scr"},
        },
    )
    c = cl.load_contract(flat, env={}, home=tmp_path / "nohome")
    assert c.profile_name == cl.LEGACY_PROFILE and c.schema_version == 1
    assert c.simulator_name == "FLOW" and c["simulator"]["version"] == "2025.04"
    assert c["queue"] == {"system": "LSF", "name": "normal"}
    assert c.p10_p90 == "exceedance" and c.required_summary == ["FOPT"]
    assert c.eclbase() == ("eclipse/model", "TOY") and c["scratch_root"] == "/scr"
    assert c.path("deliverables") == tmp_path / "flat/out"
    with pytest.raises(ContractError, match="no `profiles` block"):
        cl.load_contract(flat, "home", env={}, home=tmp_path / "nohome")


def test_required_keys_and_skip_required(tmp_path: Path):
    _user(tmp_path)
    proj = project_block(tmp_path)
    del proj["eclbase"]
    _write(tmp_path / "proj/site.contract.yml", proj)
    with pytest.raises(ContractError, match="eclbase"):
        _load(tmp_path)
    c = _load(tmp_path, skip_required=("eclbase",))
    with pytest.raises(ContractError, match="no default deck"):
        c.eclbase()
    assert c.eclbase("sim/X.DATA") == ("sim", "X")
    assert _load(tmp_path, require_project=False).profile_name == "home"


def test_placeholder_in_required_key_is_error(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path, eclbase="<ASK: deck path>")
    with pytest.raises(ContractError, match="placeholder"):
        _load(tmp_path)
    assert cl.has_placeholder("<ASK>") and not cl.has_placeholder("realization-<IENS>/iter-<ITER>")


def test_resolve_eclbase_rules():
    assert cl.resolve_eclbase({"eclbase": "eclipse/model/TOY"}) == ("eclipse/model", "TOY")
    assert cl.resolve_eclbase({"paths": {"eclbase": "TOY"}}) == ("eclipse/model", "TOY")
    assert cl.resolve_eclbase({"eclbase": "eclipse/model/TOY"}, "sim/X.DATA") == ("sim", "X")
    with pytest.raises(ContractError):
        cl.resolve_eclbase({})


# ---------------------------------------------------------------- gate stages / commands
def test_select_gate_stage():
    c = {
        "gate_stages": {"A": {"mode_default": "draft"}, "B": {"mode_default": "strict"}},
        "gate_default_stage": "B",
    }
    assert cl.select_gate_stage(c)[0] == "B"
    assert cl.select_gate_stage(c, "A") == ("A", {"mode_default": "draft"})
    with pytest.raises(ContractError, match="not in contract gate_stages"):
        cl.select_gate_stage(c, "DG0")
    with pytest.raises(ContractError, match="no stage given"):
        cl.select_gate_stage({"gate_stages": c["gate_stages"]})
    assert cl.select_gate_stage({"gate_stages": {"ONLY": {"mode_default": "draft"}}})[0] == "ONLY"
    with pytest.raises(ContractError, match="mode_default"):
        cl.select_gate_stage({"gate_stages": {"X": {"mode_default": "maybe"}}})
    with pytest.raises(ContractError, match="gate_stages"):
        cl.select_gate_stage({})


def test_wrap_command_bash_tcsh(tmp_path: Path):
    _user(tmp_path)
    _project(tmp_path)
    work = _load(tmp_path, profile="work")
    assert work.wrap_command("ert --version", runner="tcsh") == [
        "tcsh",
        "-c",
        "source /opt/env/enable.csh && ert --version",
    ]
    # bash runner prefers activate_bash on a tcsh profile
    assert work.wrap_command("ert --version") == ["bash", "-lc", "source /opt/env/enable && ert --version"]
    assert work.activate_for("bash") == "source /opt/env/enable"
    home = _load(tmp_path)
    assert home.wrap_command("ert --version") == ["bash", "-lc", "true && ert --version"]
    assert home.plot_required() == ["FOPT", "FOPR", "WOPR:P1"]
    assert home.default_iter() == "iter-0"


def test_repo_example_contract_refuses_empty_settings(tmp_path: Path):
    example = Path(__file__).resolve().parents[1] / "site.contract.example.yml"
    data = cl.read_yaml(example)
    assert data["eclbase"] is None and data["p10_p90"] is None
    assert data["paths"]["scratch"] is None and data["vectors"]["required_summary"] is None
    with pytest.raises(ContractError, match="eclbase"):
        cl.load_contract(example, profile="home", env={"HOME": str(tmp_path)}, home=tmp_path / "nohome")
    flat = dict(data)
    flat.pop("profiles")
    empty = _write(tmp_path / "empty.yml", flat)
    with pytest.raises(ContractError, match="eclbase"):
        cl.load_contract(empty, env={}, home=tmp_path / "nohome")
    filled = cl.deep_merge(data, project_block(tmp_path))
    filled.update(profile_block(tmp_path))
    filled["profiles"] = {
        "home": profile_block(tmp_path),
        "work": profile_block(tmp_path, output_root="<ASK: output folder>"),
    }
    filled["active_profile"] = "home"
    copy = _write(tmp_path / "site.contract.yml", filled)
    c = cl.load_contract(copy, env={"HOME": str(tmp_path)}, home=tmp_path / "nohome", skip_required=())
    assert c.profile_name == "home" and c.eclbase() == ("eclipse/model", "TOY")
    assert c.path("scratch") == tmp_path / "scratch/toy_ens"
    assert sorted(c["gate_stages"]) == ["BPBO", "DG0", "VPBO"]
    with pytest.raises(ContractError, match="placeholder"):
        cl.load_contract(copy, "work", env={"HOME": str(tmp_path), "USER": "u"}, home=tmp_path / "nohome")


def test_migrated_runtime_settings_come_from_the_profile(tmp_path: Path):
    selected = profile_block(tmp_path)
    selected.update(
        flow_cmd="synthetic-flow",
        flow_extra_args="--synthetic=true",
        everest_executable="synthetic-everest",
        everest_output_root="/out",
        everest_max_sims_per_run=5,
        ahm_posterior_case="synthetic-posterior",
        verified_pins={"ert": "test", "ropt": "test"},
        versions={"ert": "test"},
    )
    _user(tmp_path, profiles={"home": selected})
    _project(tmp_path, flow_cmd="wrong-project-value", everest_executable="wrong-project-value")
    loaded = _load(tmp_path)
    assert loaded["flow_cmd"] == "synthetic-flow"
    assert loaded["everest_executable"] == "synthetic-everest"
    assert loaded["everest_max_sims_per_run"] == 5
    assert loaded["verified_pins"] == {"ert": "test", "ropt": "test"}
    assert loaded["versions"] == {"ert": "test"}
