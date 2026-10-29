#!/usr/bin/env python3
"""Shared site-contract loader (schema v2.1, with a v1 flat-contract mapping).

Every skill script loads the contract through `load_contract()`. Nothing here falls back
to a deck name, a project folder or a viewer path. An incomplete contract is an error.

File lookup (first match is the main file):
  1. --contract PATH
  2. env RE_AGENT_CONTRACT
  3. ./site.contract.yml                       (project level)
  4. ~/.config/re-agent/site.contract.yml      (user level; holds profiles)
A project file without `profiles` is merged on top of the user file
(RE_AGENT_CONTRACT, else ~/.config/re-agent/site.contract.yml). Dicts merge deeply,
lists replace, project scalars win. A `profiles` block in a project overlay is ignored
with a warning unless RE_AGENT_ALLOW_PROJECT_PROFILES=1.

Profile selection: --profile, then env RE_AGENT_PROFILE, then `active_profile`.
An unknown or missing profile is an error. A v1 flat contract has no profiles: its flat
keys become the profile named "legacy".

Expansion: $VAR and ${VAR} from the environment, ${scratch_root} and ${output_root}
from the selected profile. `$$` is a literal `$`. Shell commands (`activate`,
`activate_bash`) and regexes are not expanded. An unresolved variable is an error.

All errors raise ContractError. CLI wrappers exit with code 2.
Needs PyYAML (it is in Komodo).
"""

from __future__ import annotations

import copy
import os
import re
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
CONTRACT_NAME = "site.contract.yml"
USER_CONTRACT = Path("~/.config/re-agent") / CONTRACT_NAME
ENV_CONTRACT = "RE_AGENT_CONTRACT"
ENV_PROFILE = "RE_AGENT_PROFILE"
ENV_ALLOW_PROJECT_PROFILES = "RE_AGENT_ALLOW_PROJECT_PROFILES"
LEGACY_PROFILE = "legacy"

# Profile level: where the agent runs (proposal section 2).
PROFILE_KEYS = frozenset(
    {
        "agent_host",
        "execution_mode",
        "data_classification",
        "model_endpoint",
        "endpoint_approved",
        "python",
        "activate",
        "activate_shell",
        "activate_bash",
        "komodo_release",
        "ert",
        "simulator",
        "queue",
        "scratch_root",
        "output_root",
        "resinsight",
        "sumo",
        "everest",
        "versions",
        "flow_cmd",
        "flow_extra_args",
        "everest_output_root",
        "everest_executable",
        "everest_max_sims_per_run",
        "ahm_posterior_case",
        "verified_pins",
    }
)
REQUIRED_PROJECT_KEYS = ("eclbase", "p10_p90", "paths.deliverables", "vectors.required_summary")
QUEUE_SYSTEMS = frozenset({"LOCAL", "LSF", "SLURM", "TORQUE"})
P10_P90 = frozenset({"numerical", "exceedance"})
ACTIVATE_SHELLS = frozenset({"bash", "tcsh"})
NO_EXPAND_KEYS = frozenset({"activate", "activate_bash", "naming_regex", "pattern"})
# ERT magic strings are not placeholders.
ERT_MAGIC = frozenset(
    {"IENS", "ITER", "CONFIG_PATH", "CONFIG_FILE", "CONFIG_FILE_BASE", "ECLBASE", "RUNPATH"}
)
PLACEHOLDER_RE = re.compile(r"<(ASK\b[^>]*|[A-Za-z_][A-Za-z0-9_ .|:/-]*)>")
VAR_RE = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
MAX_EXPAND_DEPTH = 8


class ContractError(Exception):
    """A contract problem. CLI tools print it and exit 2."""

    exit_code = 2


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        raise ContractError(
            "PyYAML is required to read site.contract.yml. Use the python of the contract profile (Komodo)."
        ) from exc
    return yaml


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"cannot read contract {path}: {exc}") from exc
    try:
        data = _yaml().safe_load(text)
    except Exception as exc:  # yaml.YAMLError and friends
        raise ContractError(f"{path}: YAML parse error: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ContractError(f"{path}: the top level must be a mapping")
    return data


def deep_merge(base: Mapping[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    """Dicts merge deeply, lists and scalars from `top` replace."""
    out = copy.deepcopy(dict(base))
    for key, value in top.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def get_dotted(data: Mapping[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return default
        node = node[part]
    return node


def find_placeholders(data: Any, prefix: str = "") -> list[str]:
    """Dotted keys whose string value still holds a <PLACEHOLDER> (ERT magic strings excepted)."""
    found: list[str] = []
    if isinstance(data, Mapping):
        for key, value in data.items():
            found.extend(find_placeholders(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(data, list):
        for i, value in enumerate(data):
            found.extend(find_placeholders(value, f"{prefix}[{i}]"))
    elif isinstance(data, str):
        for match in PLACEHOLDER_RE.finditer(data):
            if match.group(1).strip() not in ERT_MAGIC:
                found.append(prefix)
                break
    return found


def has_placeholder(value: Any) -> bool:
    return bool(find_placeholders(value, "x"))


def _sub_vars(text: str, variables: Mapping[str, str], env: Mapping[str, str], missing: list[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "\0DOLLAR\0"
        name = match.group(1) or match.group(2)
        if name in variables and variables[name] not in (None, ""):
            return str(variables[name])
        if name in env:
            return env[name]
        missing.append(name)
        return match.group(0)

    return VAR_RE.sub(repl, text)


def expand_string(text: str, variables: Mapping[str, str], env: Mapping[str, str], where: str = "") -> str:
    """Expand $VAR, ${VAR}; profile variables win over the environment. Unresolved = error."""
    label = f" in {where}" if where else ""
    current = text
    for _ in range(MAX_EXPAND_DEPTH):
        missing: list[str] = []
        new = _sub_vars(current, variables, env, missing)
        if missing:
            raise ContractError(
                f"unresolved variable ${{{missing[0]}}}{label}. Set it in the environment or the profile."
            )
        if new == current:
            return new.replace("\0DOLLAR\0", "$")
        current = new
    raise ContractError(f"variable expansion does not converge{label} (cycle?)")


def _expand_tree(node: Any, variables: Mapping[str, str], env: Mapping[str, str], where: str) -> Any:
    if isinstance(node, Mapping):
        return {
            k: (v if k in NO_EXPAND_KEYS else _expand_tree(v, variables, env, f"{where}.{k}" if where else k))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_expand_tree(v, variables, env, f"{where}[{i}]") for i, v in enumerate(node)]
    if isinstance(node, str) and "$" in node:
        return expand_string(node, variables, env, where)
    return node


def _legacy_profile(flat: Mapping[str, Any]) -> dict[str, Any]:
    """Build a profile from v1 flat keys (proposal section 4)."""
    prof: dict[str, Any] = {}
    for key in (
        "python",
        "activate",
        "activate_shell",
        "activate_bash",
        "komodo_release",
        "agent_host",
        "execution_mode",
        "model_endpoint",
        "endpoint_approved",
        "data_classification",
        "scratch_root",
        "output_root",
    ):
        if key in flat:
            prof[key] = flat[key]
    paths = flat.get("paths") if isinstance(flat.get("paths"), Mapping) else {}
    if "scratch_root" not in prof and paths.get("scratch_root"):
        prof["scratch_root"] = paths["scratch_root"]
    sim = flat.get("simulator")
    if isinstance(sim, Mapping):
        prof["simulator"] = dict(sim)
    elif sim is not None:
        prof["simulator"] = {"name": sim}
        if flat.get("simulator_version") is not None:
            prof["simulator"]["version"] = flat["simulator_version"]
    queue = flat.get("queue") if isinstance(flat.get("queue"), Mapping) else {}
    queue = dict(queue)
    if flat.get("queue_system") is not None:
        queue.setdefault("system", flat["queue_system"])
    if flat.get("lsf_queue") not in (None, ""):
        queue.setdefault("lsf_queue", flat["lsf_queue"])
    if queue:
        prof["queue"] = queue
    for key in ("sumo", "everest"):
        value = flat.get(key)
        if isinstance(value, Mapping):
            prof[key] = dict(value)
        elif isinstance(value, str):
            prof[key] = {"enabled": value.strip().lower() in ("enabled", "true", "yes", "on")}
        elif isinstance(value, bool):
            prof[key] = {"enabled": value}
    if flat.get("sumo_environment"):
        prof.setdefault("sumo", {})["environment"] = flat["sumo_environment"]
    return prof


class Contract(dict):
    """Flat, expanded contract: profile keys lifted next to project keys."""

    profile_name: str = ""
    sources: list[str]
    warnings: list[str]
    schema_version: int = SCHEMA_VERSION

    # ---- access -------------------------------------------------------
    def get_dotted(self, dotted: str, default: Any = None) -> Any:
        return get_dotted(self, dotted, default)

    def require(self, dotted: str) -> Any:
        value = get_dotted(self, dotted, None)
        if value is None or (isinstance(value, str | list | dict) and len(value) == 0):
            raise ContractError(f"contract key `{dotted}` is required and is missing or empty")
        if has_placeholder(value):
            raise ContractError(f"contract key `{dotted}` still holds a placeholder: {value!r}")
        return value

    @property
    def p10_p90(self) -> str:
        value = str(self.require("p10_p90")).strip().lower()
        if value not in P10_P90:
            raise ContractError(f"p10_p90 must be one of {sorted(P10_P90)}, got {value!r}")
        return value

    @property
    def required_summary(self) -> list[str]:
        return [str(v) for v in (self.get_dotted("vectors.required_summary") or [])]

    @property
    def simulator_name(self) -> str:
        sim = self.get("simulator")
        return str(sim.get("name", "")) if isinstance(sim, Mapping) else str(sim or "")

    def vectors(self, key: str, default: list[str] | None = None) -> list[str]:
        value = self.get_dotted(f"vectors.{key}")
        if value is None:
            return list(default or [])
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    # ---- paths ----------------------------------------------------------
    def expand(self, text: str) -> str:
        return expand_string(text, _profile_vars(self), os.environ)

    def resolve(self, value: str | Path) -> Path:
        """Absolute path: expand, then make relative values relative to paths.root."""
        p = Path(self.expand(str(value)) if "$" in str(value) else str(value)).expanduser()
        if p.is_absolute():
            return p
        root = self.get_dotted("paths.root")
        if not root:
            raise ContractError(f"relative path {value!r} needs `paths.root` in the contract")
        return Path(root) / p

    def path(self, key: str) -> Path:
        """paths.<key> as an absolute Path (required)."""
        return self.resolve(self.require(f"paths.{key}"))

    def eclbase(self, override: str | None = None) -> tuple[str, str]:
        """(subdir relative to the runpath, deck name). --eclbase override, else contract. No fallback."""
        return resolve_eclbase(self, override)

    def plot_required(self) -> list[str]:
        """vectors.plot_required (default FOPT, FOPR) + WOPR:<wells.plot_default> when set."""
        out = self.vectors("plot_required", ["FOPT", "FOPR"])
        well = self.get_dotted("wells.plot_default")
        if well and not has_placeholder(well) and f"WOPR:{well}" not in out:
            out.append(f"WOPR:{well}")
        return out

    def default_iter(self) -> str:
        return iter_dirname(self.get_dotted("runpath.default_iter") or "iter-0")

    # ---- environment ------------------------------------------------------
    def activate_for(self, runner: str = "bash") -> str:
        """The activate line for this runner shell. Empty string if the profile has none."""
        shell = str(self.get("activate_shell") or "bash")
        if runner == "bash" and shell != "bash" and self.get("activate_bash"):
            return str(self["activate_bash"])
        return str(self.get("activate") or "")

    def wrap_command(self, command: str, runner: str = "bash") -> list[str]:
        """argv that runs `command` in the profile environment (proposal section 7)."""
        shell = str(self.get("activate_shell") or "bash")
        if shell not in ACTIVATE_SHELLS:
            raise ContractError(f"activate_shell must be one of {sorted(ACTIVATE_SHELLS)}, got {shell!r}")
        if runner == "bash" and shell == "tcsh" and self.get("activate_bash"):
            shell, activate = "bash", str(self["activate_bash"])
        else:
            activate = str(self.get("activate") or "")
        if has_placeholder(activate):
            raise ContractError("activate still holds a placeholder; fill the profile first")
        line = f"{activate} && {command}" if activate else command
        return ["bash", "-lc", line] if shell == "bash" else ["tcsh", "-c", line]

    def command_text(self, command: str, runner: str = "bash") -> str:
        return " ".join(shlex.quote(a) for a in self.wrap_command(command, runner))


def resolve_eclbase(contract: Mapping[str, Any] | None, override: str | None = None) -> tuple[str, str]:
    """(subdir relative to the runpath, deck name) from --eclbase, else contract `eclbase`.

    Also reads the v1 key `paths.eclbase` from a raw mapping. There is no default deck name.
    """
    contract = contract or {}
    paths = contract.get("paths") if isinstance(contract.get("paths"), Mapping) else {}
    raw = override or contract.get("eclbase") or paths.get("eclbase")
    if not raw:
        raise ContractError(
            "contract key `eclbase` is required (e.g. eclipse/model/<DECK>), or pass --eclbase. "
            "There is no default deck name."
        )
    if has_placeholder(raw):
        raise ContractError(f"contract key `eclbase` still holds a placeholder: {raw!r}")
    return split_eclbase(str(raw))


def iter_dirname(token: str | int) -> str:
    """Runpath iteration folder: 3 -> 'iter-3'; 'iter-3' / 'pred_ref' kept as given."""
    t = str(token).strip()
    if t.isdigit():
        return f"iter-{int(t)}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", t):
        raise ContractError(f"bad iteration {token!r} (use 0, iter-3, pred_ref ...)")
    return t


def split_eclbase(raw: str) -> tuple[str, str]:
    raw = raw.strip().strip("/")
    if raw.upper().endswith(".DATA"):
        raw = raw[: -len(".DATA")]
    if "/" in raw:
        subdir, name = raw.rsplit("/", 1)
        return subdir, name
    return "eclipse/model", raw


def _profile_vars(data: Mapping[str, Any]) -> dict[str, str]:
    out = {}
    for key in ("scratch_root", "output_root"):
        value = data.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return out


def _same(a: Path | None, b: Path | None) -> bool:
    try:
        return a is not None and b is not None and a.resolve() == b.resolve()
    except OSError:
        return False


def locate_files(
    contract: str | Path | None = None,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path | None, Path | None]:
    """Return (base file that owns profiles, project overlay file). Either may be None, not both.

    The explicit --contract file is the overlay when it has no `profiles` block and a user-level
    file exists; otherwise it stands alone. The cwd file is not read when --contract is given.
    """
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else Path(cwd)
    user = (Path(home) / ".config/re-agent" / CONTRACT_NAME) if home else USER_CONTRACT.expanduser()
    env_file = Path(env[ENV_CONTRACT]).expanduser() if env.get(ENV_CONTRACT) else None
    local = cwd / CONTRACT_NAME
    if env_file is not None and not env_file.is_file():
        raise ContractError(f"{ENV_CONTRACT} points to a missing file: {env_file}")
    user_base = env_file or (user if user.is_file() else None)
    if contract is not None:
        main = Path(contract).expanduser()
        if not main.is_file():
            raise ContractError(f"contract file not found: {main}")
        if "profiles" in read_yaml(main) or user_base is None or _same(user_base, main):
            return main, None
        return user_base, main
    if env_file is not None:
        return env_file, (local if local.is_file() and not _same(local, env_file) else None)
    if local.is_file():
        if user_base is None or _same(user_base, local):
            return local, None
        return user_base, local
    if user.is_file():
        return user, None
    checked = [
        "--contract (not given)",
        f"env {ENV_CONTRACT} (not set)",
        f"./{CONTRACT_NAME} ({local})",
        str(user),
    ]
    raise ContractError("no site contract found. Checked, in order:\n  " + "\n  ".join(checked))


def load_contract(
    contract: str | Path | None = None,
    profile: str | None = None,
    *,
    require_project: bool = True,
    skip_required: tuple[str, ...] | list[str] = (),
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Contract:
    """Load, merge, select the profile, expand and validate. Raise ContractError on any gap.

    skip_required: required project keys that a CLI flag supplies (e.g. ("eclbase",) with --eclbase).
    """
    env = dict(os.environ if env is None else env)
    warnings: list[str] = []
    base_file, overlay_file = locate_files(contract, cwd=cwd, env=env, home=home)
    base = read_yaml(base_file) if base_file else {}
    sources = [str(base_file)] if base_file else []
    if overlay_file is not None:
        project = read_yaml(overlay_file)
        sources.append(str(overlay_file))
        if "profiles" in project:
            if env.get(ENV_ALLOW_PROJECT_PROFILES) == "1":
                warnings.append(
                    "project file redefines profiles (allowed by RE_AGENT_ALLOW_PROJECT_PROFILES=1)"
                )
            else:
                warnings.append("project file `profiles` block ignored; profiles live in the user-level file")
                project = {k: v for k, v in project.items() if k != "profiles"}
        merged = deep_merge(base, project)
    else:
        merged = copy.deepcopy(base)

    profiles = merged.pop("profiles", None)
    if profiles is not None and not isinstance(profiles, Mapping):
        raise ContractError("`profiles` must be a mapping of profile name -> settings")

    if profiles:
        name = profile or env.get(ENV_PROFILE) or merged.get("active_profile")
        if not name:
            raise ContractError(
                f"no profile selected. Use --profile, env {ENV_PROFILE}, or active_profile. "
                f"Available: {sorted(profiles)}"
            )
        if name not in profiles:
            raise ContractError(f"unknown profile {name!r}. Available: {sorted(profiles)}")
        prof = dict(profiles[name] or {})
        legacy = False
    else:
        if profile:
            raise ContractError(
                f"unknown profile {profile!r}: the contract has no `profiles` block (v1 flat)"
            )
        if env.get(ENV_PROFILE):
            warnings.append(f"{ENV_PROFILE}={env[ENV_PROFILE]} ignored: v1 flat contract has no profiles")
        name = LEGACY_PROFILE
        prof = _legacy_profile(merged)
        legacy = True
        warnings.append("v1 flat contract: mapped to profile 'legacy'. Move to schema_version 2.")

    # Lift profile keys. Profile wins for profile-level names, project wins for the rest.
    flat: dict[str, Any] = {k: v for k, v in merged.items() if k not in _LEGACY_FLAT_ONLY}
    if legacy:
        for key in ("sumo", "everest", "simulator"):
            flat.pop(key, None)
    for key, value in prof.items():
        if key in PROFILE_KEYS or key not in flat:
            flat[key] = copy.deepcopy(value)
    _apply_aliases(flat, warnings)

    variables = _profile_vars(flat)
    resolved: dict[str, str] = {}
    for key in ("scratch_root", "output_root"):  # roots may hold $USER; output_root may use ${scratch_root}
        if key in variables:
            resolved[key] = expand_string(variables[key], resolved, env, key)
    variables = resolved
    flat = _expand_tree(flat, variables, env, "")

    result = Contract(flat)
    result.profile_name = str(name)
    result.sources = sources
    result.warnings = warnings
    result.schema_version = 1 if legacy else int(merged.get("schema_version") or SCHEMA_VERSION)
    _normalise_paths(result, project_dir=Path(sources[-1]).resolve().parent)
    _validate(result, require_project, tuple(skip_required))
    return result


# v1 keys that only feed the legacy profile; they are not project keys.
_LEGACY_FLAT_ONLY = frozenset({"simulator_version", "queue_system", "lsf_queue", "sumo_environment"})


def _apply_aliases(flat: dict[str, Any], warnings: list[str]) -> None:
    # percentile_convention -> p10_p90
    pc = flat.pop("percentile_convention", None)
    if pc is not None:
        if flat.get("p10_p90") is None:
            flat["p10_p90"] = pc
        elif str(flat["p10_p90"]).lower() != str(pc).lower():
            warnings.append(
                f"percentile_convention={pc!r} differs from p10_p90={flat['p10_p90']!r}; using p10_p90"
            )
    # required_summary(_hm) -> vectors.*
    vectors = dict(flat.get("vectors") or {})
    for key in ("required_summary", "required_summary_hm"):
        if key in flat and key not in vectors:
            vectors[key] = flat[key]
    if vectors:
        flat["vectors"] = vectors
    # Compatibility view for older callers.
    if "required_summary" in vectors:
        flat["required_summary"] = list(vectors["required_summary"] or [])
    # paths.eclbase -> eclbase; paths.scratch_root -> scratch_root
    paths = dict(flat.get("paths") or {})
    if "eclbase" in paths:
        if not flat.get("eclbase"):
            flat["eclbase"] = paths["eclbase"]
        del paths["eclbase"]
    if paths.get("scratch_root") and not flat.get("scratch_root"):
        flat["scratch_root"] = paths["scratch_root"]
    paths.pop("scratch_root", None)
    if "paths" in flat or paths:
        flat["paths"] = paths
    # queue.lsf_queue -> queue.name (read-only alias)
    queue = flat.get("queue")
    if isinstance(queue, Mapping):
        queue = dict(queue)
        alias = queue.pop("lsf_queue", None)
        if alias not in (None, ""):
            if queue.get("name") not in (None, "") and queue["name"] != alias:
                warnings.append(
                    f"queue.lsf_queue={alias!r} differs from queue.name={queue['name']!r}; using queue.name"
                )
            else:
                queue["name"] = alias
        flat["queue"] = queue
    # simulator.license_server only -> hint
    sim = flat.get("simulator")
    if isinstance(sim, Mapping) and sim.get("license_server") and not sim.get("license_env"):
        warnings.append("simulator.license_server is a hint only; set simulator.license_env (variable name)")


def _normalise_paths(c: Contract, project_dir: Path) -> None:
    paths = c.get("paths")
    if not isinstance(paths, dict):
        return
    root = paths.get("root")
    if isinstance(root, str) and root and not has_placeholder(root):
        rp = Path(root).expanduser()
        paths["root"] = str(rp if rp.is_absolute() else (project_dir / rp).resolve())
    elif root is None:
        paths["root"] = str(project_dir)
    base = (
        Path(paths["root"])
        if isinstance(paths.get("root"), str) and not has_placeholder(paths["root"])
        else None
    )
    for key, value in list(paths.items()):
        if key == "root" or not isinstance(value, str) or not value or has_placeholder(value):
            continue
        p = Path(value).expanduser()
        if not p.is_absolute() and base is not None:
            paths[key] = str(base / p)


def _validate(c: Contract, require_project: bool, skip_required: tuple[str, ...] = ()) -> None:
    queue = c.get("queue")
    if isinstance(queue, Mapping):
        system = queue.get("system")
        if (
            system not in (None, "")
            and not has_placeholder(system)
            and str(system).upper() not in QUEUE_SYSTEMS
        ):
            raise ContractError(f"queue.system {system!r} is not one of {sorted(QUEUE_SYSTEMS)}")
    shell = c.get("activate_shell")
    if shell not in (None, "") and not has_placeholder(shell) and shell not in ACTIVATE_SHELLS:
        raise ContractError(f"activate_shell {shell!r} is not one of {sorted(ACTIVATE_SHELLS)}")
    p10 = c.get("p10_p90")
    if p10 is not None and str(p10).strip().lower() not in P10_P90:
        raise ContractError(f"p10_p90 must be one of {sorted(P10_P90)}, got {p10!r}")
    if require_project:
        for key in REQUIRED_PROJECT_KEYS:
            if key not in skip_required:
                c.require(key)


def select_gate_stage(
    contract: Mapping[str, Any], requested: str | None = None
) -> tuple[str, dict[str, Any]]:
    """(stage name, stage config) from contract gate_stages. Any stage names; no built-in list.

    Default: `gate_default_stage`, else the only stage. Unknown name = error.
    """
    stages = contract.get("gate_stages")
    if not isinstance(stages, Mapping) or not stages:
        raise ContractError("contract key `gate_stages` is required (a mapping of stage name -> settings)")
    name = (requested or "").strip() or contract.get("gate_default_stage")
    if not name:
        if len(stages) == 1:
            name = next(iter(stages))
        else:
            raise ContractError(
                f"no stage given. Use --stage or contract gate_default_stage. Stages: {sorted(stages)}"
            )
    if name not in stages:
        raise ContractError(f"stage {name!r} is not in contract gate_stages {sorted(stages)}")
    cfg = stages[name]
    if not isinstance(cfg, Mapping):
        raise ContractError(f"gate_stages.{name} must be a mapping")
    mode = str(cfg.get("mode_default") or "").strip().lower()
    if mode not in ("draft", "strict"):
        raise ContractError(f"gate_stages.{name}.mode_default must be 'draft' or 'strict', got {mode!r}")
    return str(name), dict(cfg)


def flag_text(value: Any) -> str:
    """'enabled' / 'disabled' from a bool, a string, or a profile block {enabled: ...}."""
    if isinstance(value, Mapping):
        value = value.get("enabled")
    if value in (True, "enabled", "true"):
        return "enabled"
    if value in (False, None, "disabled", "false"):
        return "disabled"
    return str(value)


# ---- CLI helpers ------------------------------------------------------------
def add_contract_args(parser, *, required: bool = False) -> None:
    parser.add_argument(
        "--contract",
        type=Path,
        required=required,
        default=None,
        help=f"site.contract.yml (default lookup: env {ENV_CONTRACT}, ./{CONTRACT_NAME}, ~/{USER_CONTRACT})",
    )
    parser.add_argument(
        "--profile", default=None, help=f"profile name (default: env {ENV_PROFILE}, then active_profile)"
    )


def load_or_exit(contract: str | Path | None, profile: str | None = None, **kwargs: Any) -> Contract:
    """load_contract() for CLI scripts: print the error and exit 2. Never retry with defaults."""
    try:
        c = load_contract(contract, profile, **kwargs)
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        raise SystemExit(2) from exc
    for note in c.warnings:
        sys.stderr.write(f"note: contract: {note}\n")
    return c


def main(argv: list[str] | None = None) -> int:
    """Print the resolved contract as JSON (for debugging and doctor)."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Resolve site.contract.yml and print it as JSON.")
    add_contract_args(parser)
    parser.add_argument("--no-project-check", action="store_true", help="skip required project keys")
    args = parser.parse_args(argv)
    c = load_or_exit(args.contract, args.profile, require_project=not args.no_project_check)
    print(
        json.dumps(
            {"profile": c.profile_name, "sources": c.sources, "warnings": c.warnings, "contract": dict(c)},
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
