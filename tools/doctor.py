#!/usr/bin/env python3
"""Local deployment preflight. Never activates Komodo, imports plugins, runs a simulator or contacts services.

Schema version 1: a deliberately flat YAML subset: key: scalar, quoted strings,
true/false and comments. Nested YAML, aliases, duplicate keys and unknown keys are errors.
Schema version 2 (schema_version: 2): nested YAML with profiles. It needs PyYAML. The profile
comes from --profile, then RE_AGENT_PROFILE, then active_profile; an unknown profile exits 2.
The file is checked against site.contract.schema.json (jsonschema when installed, else a small
built-in validator). Values like <ASK: ...> are reported as unfilled.
The one exception is the optional `ahm:` block: indented `key: number` lines with known AHM thresholds.
Versions describe installed distributions, not proof that a simulator or site works.
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path, PurePosixPath

REQUIRED = {
    "project",
    "komodo_release",
    "python",
    "activate",
    "scratch_root",
    "output_root",
    "queue_system",
    "simulator",
    "simulator_version",
    "percentile_convention",
    "data_classification",
    "agent_host",
    "execution_mode",
    "model_endpoint",
    "endpoint_approved",
}
OPTIONAL = {
    "lsf_queue",
    "sumo_environment",
    "ahm",
    "flow_cmd",
    "flow_extra_args",
    "everest_output_root",
    "everest_executable",
    "everest_max_sims_per_run",
    "ahm_posterior_case",
    "verified_pins",
}
# Optional thresholds for AHM scripts. An empty value means: use the default of the script.
AHM_KEYS = {"min_realizations", "weight_share_min", "weight_share_max"}
PACKAGES = ("ert", "res2df", "resdata", "pandas", "numpy", "PyYAML")
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "site.contract.schema.json"
V2_MARKER = re.compile(r"^schema_version:\s*[\"']?2[\"']?\s*(?:#.*)?$", re.M)
PLACEHOLDER = re.compile(r"<([^<>]*)>")
# ERT magic strings are not placeholders.
ERT_MAGIC = {"IENS", "ITER", "ECLBASE", "RUNPATH", "CONFIG_PATH", "CONFIG_FILE", "CONFIG_FILE_BASE"}
ERT_MAGIC |= {"USER", "CWD", "DATE", "NUM_CPU", "GEO_ID", "RUNPATH_FILE", "STORAGE", "REALIZATION"}
VARIABLE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


class ProfileError(ValueError):
    """A missing or unknown profile. doctor exits 2."""


def read_scalar(text: str, number: int):
    if text.startswith(('"', "'")):
        quote = text[0]
        # Strip comments only outside the quoted scalar.
        close = 1
        escaped = False
        while close < len(text):
            if text[close] == quote and not escaped:
                break
            escaped = text[close] == "\\" and not escaped
            close += 1
        if close == len(text) or (
            text[close + 1 :].strip() and not text[close + 1 :].strip().startswith("#")
        ):
            raise ValueError(f"line {number}: malformed quoted scalar")
        value = ast.literal_eval(text[: close + 1])
        if not isinstance(value, str):
            raise ValueError(f"line {number}: expected a string")
        return value
    text = re.split(r"(?:^|\s+)#", text, maxsplit=1)[0].strip()
    if text.startswith(("[", "{", "&", "*", "!", "|", ">")):
        raise ValueError(f"line {number}: unsupported YAML value")
    return {"true": True, "false": False}.get(text, text)


def read_contract(path: Path) -> dict:
    """Read the restricted subset; never interpret tags, commands or environment variables."""
    data = {}
    block = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[0] in " \t":
            match = re.fullmatch(r"[ ]{2,}([a-z_]+):\s*(.*)", line)
            if block is None or not match:
                raise ValueError(f"line {number}: expected a flat key: scalar")
            key, text = match.groups()
            if key in data[block]:
                raise ValueError(f"line {number}: duplicate field {block}.{key}")
            if block == "verified_pins" and (
                key not in {"ert", "ropt"} or not re.fullmatch(r"[\\w.\\-]*", str(read_scalar(text, number)))
            ):
                raise ValueError(f"line {number}: invalid literal version pin")
            data[block][key] = read_scalar(text, number)
            continue
        match = re.fullmatch(r"([a-z_]+):\s*(.*)", line)
        if not match:
            raise ValueError(f"line {number}: expected a flat key: scalar")
        key, text = match.groups()
        if key in data:
            raise ValueError(f"line {number}: duplicate field {key}")
        block = None
        if key in {"ahm", "verified_pins"} and read_scalar(text, number) == "":
            data[key] = {}
            block = key
            continue
        data[key] = read_scalar(text, number)
    return data


def check_ahm(block) -> list[str]:
    if not isinstance(block, dict):
        return ["ahm: expected a block of indented key: number lines"]
    errors = [f"unknown contract field: ahm.{key}" for key in sorted(set(block) - AHM_KEYS)]
    numbers = {}
    for key in sorted(AHM_KEYS & set(block)):
        value = block[key]
        if value in (None, ""):
            continue
        try:
            numbers[key] = int(value) if key == "min_realizations" else float(value)
        except (TypeError, ValueError):
            errors.append(f"ahm.{key}: expected a number or an empty value")
            continue
        if key == "min_realizations" and numbers[key] < 1:
            errors.append("ahm.min_realizations: must be at least 1")
        if key != "min_realizations" and not 0 <= numbers[key] <= 1:
            errors.append(f"ahm.{key}: must be a fraction from 0 to 1")
    if numbers.get("weight_share_min", 0) > numbers.get("weight_share_max", 1):
        errors.append("ahm.weight_share_min: must not be larger than weight_share_max")
    return errors


def check_contract(data: dict, mode: str) -> list[str]:
    errors = []
    for key in sorted(set(data) - REQUIRED - OPTIONAL):
        errors.append(f"unknown contract field: {key}")
    if "ahm" in data:
        errors.extend(check_ahm(data["ahm"]))
    for key in sorted(REQUIRED - {"endpoint_approved"}):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or any(c in value for c in ("<", ">", "$")):
            errors.append(f"{key}: fill in a literal value; placeholders are not allowed")
    if data.get("endpoint_approved") is not True:
        errors.append("endpoint_approved: must be the boolean true after site approval, not a string")
    for key, allowed in {
        "queue_system": {"LSF", "LOCAL"},
        "percentile_convention": {"numerical", "exceedance"},
        "agent_host": {"windows", "linux"},
        "execution_mode": {"manual", "direct"},
    }.items():
        if data.get(key) not in allowed:
            errors.append(f"{key}: expected one of {sorted(allowed)}")
    if data.get("queue_system") == "LSF":
        queue = data.get("lsf_queue")
        if not isinstance(queue, str) or not queue or any(char.isspace() or char in "<>$" for char in queue):
            errors.append("lsf_queue: a literal queue identifier is required for LSF")
    if data.get("execution_mode") == "direct" and data.get("agent_host") != "linux":
        errors.append("execution_mode: direct requires agent_host linux")
    for key in ("python", "scratch_root", "output_root"):
        value = data.get(key, "")
        if (
            not isinstance(value, str)
            or not PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts
        ):
            errors.append(f"{key}: must be an absolute Linux path without traversal")
    if mode == "execution":
        if platform.system() != "Linux":
            errors.append("execution preflight must run on the Linux/Komodo host")
        value = data.get("python")
        if isinstance(value, str) and os.path.abspath(value) != os.path.abspath(sys.executable):
            errors.append(
                "interpreter: run doctor with the exact contract Python (venv paths are not collapsed)"
            )
        for key in ("scratch_root", "output_root"):
            value = data.get(key)
            if isinstance(value, str) and not Path(value).is_dir():
                errors.append(f"{key}: existing directory required on the execution host")
        if (
            isinstance(data.get("output_root"), str)
            and Path(data["output_root"]).is_dir()
            and not os.access(data["output_root"], os.W_OK)
        ):
            errors.append("output_root: not writable")
    return errors


def is_v2(path: Path) -> bool:
    return bool(V2_MARKER.search(path.read_text(encoding="utf-8")))


def read_runtime_contract(path: Path, profile: str | None = None) -> dict:
    """Expose selected profile settings to command planners that also accept v1."""
    if not is_v2(path):
        return read_contract(path)
    data = read_contract_v2(path)
    _, selected = select_profile(data, profile)
    result = {key: value for key, value in data.items() if key != "profiles"}
    result.update(selected)
    simulator = selected.get("simulator") or {}
    queue = selected.get("queue") or {}
    result["simulator_version"] = simulator.get("version")
    result["queue_system"] = queue.get("system")
    result["lsf_queue"] = queue.get("name") or queue.get("lsf_queue")
    return result


def read_contract_v2(path: Path) -> dict:
    """Read a nested contract with PyYAML safe_load. Duplicate keys are errors."""
    try:
        import yaml
    except ImportError as error:
        raise ValueError("schema_version 2 needs PyYAML in this interpreter") from error

    class Loader(yaml.SafeLoader):
        pass

    def unique_mapping(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=True)
            if key in mapping:
                raise ValueError(f"line {key_node.start_mark.line + 1}: duplicate field {key}")
            mapping[key] = loader.construct_object(value_node, deep=True)
        return mapping

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)  # a SafeLoader subclass
    except yaml.YAMLError as error:
        raise ValueError(f"YAML: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("expected a mapping at the top level")
    return data


def select_profile(data: dict, requested: str | None) -> tuple[str, dict]:
    """Select by --profile, then RE_AGENT_PROFILE, then active_profile. Never guess."""
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ProfileError("the contract has no profiles")
    name = requested or os.environ.get("RE_AGENT_PROFILE") or data.get("active_profile")
    available = ", ".join(sorted(map(str, profiles)))
    if not name:
        raise ProfileError(
            f"no profile selected (use --profile, RE_AGENT_PROFILE or active_profile); available: {available}"
        )
    if name not in profiles:
        raise ProfileError(f"unknown profile {name!r}; available: {available}")
    profile = profiles[name]
    return name, profile if isinstance(profile, dict) else {}


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and any(m.group(1) not in ERT_MAGIC for m in PLACEHOLDER.finditer(value))


def find_placeholders(value: object, path: str) -> list[str]:
    """Return the paths of string values that still hold a <...> placeholder."""
    if isinstance(value, dict):
        return [
            p
            for key, item in value.items()
            for p in find_placeholders(item, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(value, list):
        return [p for index, item in enumerate(value) for p in find_placeholders(item, f"{path}[{index}]")]
    return [path] if is_placeholder(value) else []


def builtin_validate(value: object, schema: dict, root: dict, path: str = "") -> list[str]:
    """Check the JSON Schema keywords that site.contract.schema.json uses. Not a full validator."""
    where = path or "<root>"
    errors: list[str] = []
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        errors.extend(builtin_validate(value, target, root, path))
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(
            isinstance(value, TYPES[n]) and not (n in ("integer", "number") and isinstance(value, bool))
            for n in names
        ):
            return errors + [f"{where}: expected {' or '.join(names)}"]
    if "const" in schema and (value != schema["const"] or isinstance(value, bool)):
        errors.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if "anyOf" in schema and all(builtin_validate(value, s, root, path) for s in schema["anyOf"]):
        errors.append(f"{where}: {value!r} is not valid under any of the given schemas")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: is too short")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: does not match {schema['pattern']}")
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        errors.append(f"{where}: is less than {schema['minimum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: needs at least {schema['minItems']} items")
        for index, item in enumerate(value):
            if "items" in schema:
                errors.extend(builtin_validate(item, schema["items"], root, f"{path}[{index}]"))
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{where}: needs at least {schema['minProperties']} entries")
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: '{key}' is a required property")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if key in properties:
                errors.extend(builtin_validate(item, properties[key], root, child))
            elif extra is False:
                errors.append(f"{where}: additional property '{key}' is not allowed")
            elif isinstance(extra, dict):
                errors.extend(builtin_validate(item, extra, root, child))
    return errors


def validate(data: dict, schema: dict) -> list[str]:
    """Validate with jsonschema when it is installed; else with the built-in subset."""
    try:
        import jsonschema
    except ImportError:
        return builtin_validate(data, schema, schema)
    return sorted(
        f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
        for error in jsonschema.Draft202012Validator(schema).iter_errors(data)
    )


def expand(value: str, profile: dict) -> str:
    """Expand ${scratch_root}/${output_root} from the profile and $VAR/${VAR} from the environment."""

    def replace(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        if name in ("scratch_root", "output_root") and isinstance(profile.get(name), str):
            return profile[name]
        if name in os.environ:
            return os.environ[name]
        raise ValueError(f"unresolved variable ${name}")

    for _ in range(8):
        expanded = VARIABLE.sub(replace, value)
        if expanded == value:
            return expanded
        value = expanded
    raise ValueError("variable expansion does not end (a cycle?)")


def check_profile(name: str, profile: dict, mode: str) -> list[str]:
    """Rules that the schema cannot express. Placeholders are reported elsewhere, so skip them."""
    errors = []
    prefix = f"profiles.{name}"
    if profile.get("endpoint_approved") is not True:
        errors.append(f"{prefix}.endpoint_approved: must be the boolean true after site approval")
    if profile.get("execution_mode") == "direct" and profile.get("agent_host") != "linux":
        errors.append(f"{prefix}.execution_mode: direct requires agent_host linux")
    queue = profile.get("queue") if isinstance(profile.get("queue"), dict) else {}
    if queue.get("system") in {"LSF", "SLURM", "TORQUE"}:
        queue_name = queue.get("name") or queue.get("lsf_queue")
        if not isinstance(queue_name, str) or not queue_name.strip() or any(c.isspace() for c in queue_name):
            errors.append(f"{prefix}.queue.name: a literal queue name is required for {queue['system']}")
    paths = {}
    for key in ("python", "scratch_root", "output_root"):
        value = profile.get(key)
        if not isinstance(value, str) or not value.strip() or is_placeholder(value):
            continue
        if mode == "execution":
            try:
                value = expand(value, profile)
            except ValueError as error:
                errors.append(f"{prefix}.{key}: {error}")
                continue
        elif value.startswith("$"):
            continue  # Agent mode does not expand the execution host environment.
        if not PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
            errors.append(f"{prefix}.{key}: must be an absolute Linux path without traversal")
            continue
        paths[key] = value
    if mode == "execution":
        if platform.system() != "Linux":
            errors.append("execution preflight must run on the Linux/Komodo host")
        if "python" in paths and os.path.abspath(paths["python"]) != os.path.abspath(sys.executable):
            errors.append(
                "interpreter: run doctor with the exact contract Python (venv paths are not collapsed)"
            )
        for key in ("scratch_root", "output_root"):
            if key in paths and not Path(paths[key]).is_dir():
                errors.append(f"{prefix}.{key}: existing directory required on the execution host")
        if (
            "output_root" in paths
            and Path(paths["output_root"]).is_dir()
            and not os.access(paths["output_root"], os.W_OK)
        ):
            errors.append(f"{prefix}.output_root: not writable")
    errors.extend(check_resinsight(prefix, profile.get("resinsight"), mode))
    return errors


def check_resinsight(prefix: str, block: object, mode: str) -> list[str]:
    """Static checks of the resinsight block. Doctor does not start ResInsight.

    The live check (binary version, rips version, a console launch) is
    skills/resinsight-session/scripts/ri_doctor.py.
    """
    if not isinstance(block, dict):
        return []
    errors = []
    for key in ("bin", "python"):
        value = block.get(key)
        if not isinstance(value, str) or not value.strip() or is_placeholder(value):
            continue
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            errors.append(f"{prefix}.resinsight.{key}: must be an absolute Linux path without traversal")
        elif mode == "execution" and not (os.path.isfile(value) and os.access(value, os.X_OK)):
            errors.append(f"{prefix}.resinsight.{key}: not an executable file on this host")
    if mode == "execution" and block.get("headless") == "xvfb-run" and not shutil.which("xvfb-run"):
        errors.append(f"{prefix}.resinsight.headless: xvfb-run is not on PATH")
    return errors


def expected_versions(data: dict, profile: dict | None) -> dict[str, str]:
    """Distribution -> expected version prefix: versions maps (profile wins), then ert.version."""
    expected: dict[str, str] = {}
    for source in (data.get("versions"), (profile or {}).get("versions")):
        if isinstance(source, dict):
            expected.update({str(key): str(value) for key, value in source.items()})
    ert = (profile or {}).get("ert")
    if isinstance(ert, dict) and isinstance(ert.get("version"), str):
        expected.setdefault("ert", ert["version"])
    return expected


def version_matches(installed: str, expected: str) -> bool:
    """True when expected is installed or a prefix of it on a component boundary (14.0 matches 14.0.7)."""
    expected = expected.strip().rstrip("*").rstrip(".")
    return installed == expected or (
        installed.startswith(expected) and bool(expected) and not installed[len(expected)].isdigit()
    )


def check_versions(expected: dict[str, str], mode: str, lookup=None):
    """Compare contract versions with this interpreter. Mismatch: error in execution mode, else warning."""
    lookup = lookup or importlib.metadata.version
    errors, warnings, report = [], [], {}
    for name, want in sorted(expected.items()):
        want = want.strip()
        if not want or is_placeholder(want):
            report[name] = {"expected": None, "installed": None, "match": None}
            continue
        try:
            installed = lookup(name)
        except importlib.metadata.PackageNotFoundError:
            warnings.append(
                f"version: {name} is not installed for {sys.executable}; the contract expects {want}"
            )
            report[name] = {"expected": want, "installed": None, "match": None}
            continue
        match = version_matches(installed, want)
        report[name] = {"expected": want, "installed": installed, "match": match}
        if not match:
            message = f"version: {name} {installed} does not match the contract version {want}"
            (errors if mode == "execution" else warnings).append(message)
    return errors, warnings, report


def repo_versions() -> tuple[str | None, dict[str, str | None]]:
    """The VERSION file and the version of each skill, when this is a source checkout."""
    try:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = None
    skills = {}
    for skill in sorted((ROOT / "skills").glob("*/SKILL.md")):
        front = re.match(r"^---\n(.*?)\n---\n", skill.read_text(encoding="utf-8"), re.S)
        found = re.search(r"^version:\s*(\S+)\s*$", front.group(1), re.M) if front else None
        skills[skill.parent.name] = found.group(1) if found else None
    return version, skills


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=Path("site.contract.yml"))
    parser.add_argument("--mode", choices=("agent", "execution"), default="execution")
    parser.add_argument("--profile", help="schema version 2 only; else RE_AGENT_PROFILE, then active_profile")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    errors: list[str] = []
    warnings: list[str] = []
    exit_code = None
    data: dict = {}
    profile: dict | None = None
    schema_version = profile_name = None
    unfilled: list[str] = []
    try:
        if is_v2(args.contract):
            schema_version = 2
            data = read_contract_v2(args.contract)
            profile_name, profile = select_profile(data, args.profile)
            errors.extend(
                f"schema: {e}" for e in validate(data, json.loads(SCHEMA.read_text(encoding="utf-8")))
            )
            project = {key: value for key, value in data.items() if key != "profiles"}
            unfilled = find_placeholders(project, "") + find_placeholders(profile, f"profiles.{profile_name}")
            errors.extend(f"unfilled: {path}" for path in unfilled)
            errors.extend(check_profile(profile_name, profile, args.mode))
        else:
            schema_version = 1
            data = read_contract(args.contract)
            if args.profile:
                warnings.append("--profile is ignored: profiles need schema_version 2")
            errors.extend(check_contract(data, args.mode))
    except ProfileError as error:
        errors.append(f"contract: {error}")
        exit_code = 2
    except (OSError, ValueError, SyntaxError) as error:
        errors.append(f"contract: {error}")
    source = profile if schema_version == 2 else data
    contract_python = (source or {}).get("python")
    simulator = (source or {}).get("simulator")
    if schema_version == 2:
        simulator = simulator if isinstance(simulator, dict) else {}
        simulator_report = {"name": simulator.get("name"), "declared_version": simulator.get("version")}
    else:
        simulator_report = {"name": simulator, "declared_version": data.get("simulator_version")}
    simulator_report["checked"] = False
    version_errors, version_warnings, version_checks = check_versions(
        expected_versions(data, profile), args.mode
    )
    errors.extend(version_errors)
    warnings.extend(version_warnings)
    if (
        args.mode == "agent"
        and isinstance(contract_python, str)
        and contract_python.strip()
        and not is_placeholder(contract_python)
        and os.path.abspath(contract_python) != os.path.abspath(sys.executable)
    ):
        warnings.append(
            f"interpreter: doctor runs with {sys.executable}; the contract python is {contract_python}"
        )
    repo_version, skill_versions = repo_versions()
    versions = {}
    if sys.version_info < (3, 11):  # noqa: UP036 - standalone preflight may use a non-project interpreter
        errors.append("Python >=3.11 is required")
    if args.mode == "execution":
        for name in PACKAGES:
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = None
                errors.append(f"distribution missing: {name}")
    report = {
        "ready": not errors,
        "site_verified": False,
        "mode": args.mode,
        "platform": platform.system(),
        "interpreter": sys.executable,
        "contract_python": contract_python,
        "python_version": platform.python_version(),
        "repo_version": repo_version,
        "skill_versions": skill_versions,
        "schema_version": schema_version,
        "profile": profile_name,
        "unfilled": unfilled,
        "package_versions": versions,
        "version_checks": version_checks,
        "simulator": simulator_report,
        "errors": errors,
        "warnings": warnings,
        "limits": [
            "No endpoint, scheduler, simulator, license or IDE permission check was performed.",
            "The simulator version is the string in the contract; doctor does not run the simulator.",
            "An approval flag records a human decision; it does not authorize or prevent data transfer.",
            "Installed distribution metadata does not prove imports or ERT plugins work; run check-full on the execution host.",
            "Agent mode checks the contract, not the remote Linux paths.",
        ],
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Local preflight: {'PASS' if report['ready'] else 'NOT READY'} ({args.mode})")
        print(f"Repository version: {repo_version or 'unknown'}")
        for name, version in skill_versions.items():
            print(f"skill {name}: {version or 'no version'}")
        print(f"Python: {sys.executable} ({report['python_version']})")
        if contract_python:
            print(f"Contract python: {contract_python}")
        if schema_version == 2:
            print(f"Contract: schema version 2, profile {profile_name}")
        for name, version in versions.items():
            print(f"{name}: {version or 'missing'}")
        for name, check in version_checks.items():
            print(
                f"version {name}: installed {check['installed']}, contract {check['expected'] or 'not set'}"
            )
        print(
            f"simulator: {simulator_report['name']} {simulator_report['declared_version'] or ''}".rstrip()
            + " (declared, not checked)"
        )
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        for limit in report["limits"]:
            print(f"LIMIT: {limit}")
    if exit_code is not None:
        return exit_code
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
