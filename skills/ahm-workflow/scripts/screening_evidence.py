"""Prior definitions and input evidence shared by the screening producer and its read-only gate."""

from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "ahm-screening/2"
EVIDENCE = ("design", "residuals", "observations", "influence")
_spec = importlib.util.spec_from_file_location(
    "screening_prior_qc", HERE.parents[1] / "history-match-qc/scripts/prior_qc.py"
)
prior_qc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prior_qc)


def read_priors(files: list[Path]) -> dict[str, dict]:
    """Every parameter of the GEN_KW files. A name declared twice is an error, also as GROUP:NAME and NAME."""
    params, declared = {}, {}
    for path in files:
        found, errors = prior_qc.parse_dist(path)
        if errors:
            raise ValueError("; ".join(e["message"] for e in errors))
        for param in found:
            where = f"{param['file']}:{param['line']}"
            first = declared.setdefault(prior_qc.bare(param["name"]), where)
            if first != where:
                raise ValueError(f"{param['name']}: declared twice ({first} and {where})")
            params[param["name"]] = param
    return params


def prior_snapshot(params: dict[str, dict]) -> dict[str, dict]:
    return {
        name: {"distribution": p["distribution"], "arguments": p["arguments"]} for name, p in params.items()
    }


def lookup(name: str, mapping: dict):
    """The value of name in mapping, with or without a GROUP: prefix. None when there is none."""
    if name in mapping:
        return mapping[name]
    by_bare = {prior_qc.bare(key): key for key in mapping}
    return mapping.get(by_bare.get(prior_qc.bare(name)))


def describe(prior: dict | None) -> str:
    if not prior:
        return "no prior"
    return " ".join([prior["distribution"], *(f"{k}={v:g}" for k, v in prior["arguments"].items())])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_entry(folder: Path, path: Path) -> dict[str, str]:
    """The path of a source file relative to the folder of the record, and its hash."""
    relative = os.path.relpath(path.resolve(), folder.resolve())
    return {"path": Path(relative).as_posix(), "sha256": sha256(path)}


def _number(value, positive: bool = False) -> bool:
    ok = isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
    return ok and (value > 0 or not positive)


def _optional(value, check) -> bool:
    return value is None or check(value)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parameter_problems(parameters) -> list[str]:
    if not isinstance(parameters, list) or not parameters:
        return ["parameters is not a list of screened parameters"]
    problems, names = [], []
    for item in parameters:
        if not isinstance(item, dict) or not _text(item.get("name")):
            return ["a parameter has no name"]
        name = item["name"]
        names.append(name)
        if not all(_number(item.get(key)) for key in ("low", "high", "chi2_swing", "sd_ratio_lin")):
            problems.append(f"{name}: low, high, chi2_swing and sd_ratio_lin must be finite numbers")
        if not isinstance(item.get("keep"), bool):
            problems.append(f"{name}: keep must be true or false")
        reasons = item.get("reasons")
        if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
            problems.append(f"{name}: reasons must be a list of text")
        if not (_optional(item.get("cosine"), _number) and _optional(item.get("ks_all"), _number)):
            problems.append(f"{name}: cosine and ks_all must be finite numbers or null")
    if len(names) != len(set(names)) or len({prior_qc.bare(n) for n in names}) != len(names):
        problems.append("a parameter is screened twice")
    return problems


def _prior_problems(priors) -> list[str]:
    if not isinstance(priors, dict):
        return ["priors is missing"]
    for name, prior in priors.items():
        arguments = prior.get("arguments") if isinstance(prior, dict) else None
        if (
            not isinstance(prior, dict)
            or not isinstance(prior.get("distribution"), str)
            or prior["distribution"] not in prior_qc.ARGUMENTS
            or not isinstance(arguments, dict)
            or not all(_number(v) for v in arguments.values())
        ):
            return [f"prior of {name} is not a distribution with finite arguments"]
    return []


def _domain_problems(record: dict) -> list[str]:
    domain, metadata = record.get("domain"), record.get("observation_metadata")
    if not isinstance(domain, dict) or not isinstance(metadata, dict):
        return ["domain or observation_metadata is missing"]
    members, names = domain.get("members"), domain.get("observations")
    if not (
        _text(domain.get("ensemble"))
        and isinstance(domain.get("observations_authoritative"), bool)
        and isinstance(members, list)
        and members
        and all(isinstance(m, int) and not isinstance(m, bool) and m >= 0 for m in members)
        and len(set(members)) == len(members)
        and isinstance(names, list)
        and names
        and all(_text(n) for n in names)
        and len(set(names)) == len(names)
    ):
        return ["domain needs an ensemble, unique member numbers, unique observation names and a flag"]
    if set(metadata) != set(names) or record.get("observations") != len(names):
        return ["observation_metadata and observations do not match the domain"]
    for name, row in metadata.items():
        if not (
            isinstance(row, dict)
            and _number(row.get("OBSERVED"))
            and _number(row.get("ERROR"), positive=True)
            and all(isinstance(row[k], str) for k in ("KEY", "DATE") if k in row)
        ):
            return [f"observation_metadata of {name} needs OBSERVED, a positive ERROR, and text KEY and DATE"]
    return []


def _evidence_problems(record: dict) -> list[str]:
    sources = record.get("evidence")
    if not isinstance(sources, dict) or not set(sources) <= set(EVIDENCE):
        return [f"evidence must hold only {', '.join(EVIDENCE)}"]
    for name, entry in sources.items():
        path = entry.get("path") if isinstance(entry, dict) else None
        if not (
            _text(path)
            and not Path(path).is_absolute()
            and re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256")))
        ):
            return [f"evidence {name} needs a relative path and a sha256"]
    analysis = record["ahm_analysis"]
    wanted = ["design", "residuals"]
    wanted += ["observations"] if record["domain"]["observations_authoritative"] else []
    wanted += ["influence"] if analysis.strip() and not analysis.startswith("not run:") else []
    missing = [name for name in wanted if name not in sources]
    return [f"evidence has no {', '.join(missing)}"] if missing else []


def record_problems(record) -> list[str]:
    """Why a parsed screening.json is not a usable record of this schema. Empty when it is."""
    if not isinstance(record, dict):
        return ["it is not a JSON object"]
    if record.get("schema") != SCHEMA:
        return [f"its schema is {record.get('schema')!r}, not {SCHEMA}"]
    limits = record.get("limits")
    if not isinstance(limits, dict) or not all(_number(limits.get(k)) for k in ("min_chi2", "max_ratio")):
        return ["limits is missing"]
    if not isinstance(record.get("ahm_analysis"), str):
        return ["ahm_analysis is missing"]
    problems = _parameter_problems(record.get("parameters"))
    if not problems:
        kept = [p["name"] for p in record["parameters"] if p["keep"]]
        dropped = [p["name"] for p in record["parameters"] if not p["keep"]]
        if record.get("kept") != kept or record.get("dropped") != dropped:
            problems.append("kept and dropped do not match the parameters")
    problems += _prior_problems(record.get("priors"))
    if not problems:
        problems = _domain_problems(record) or _evidence_problems(record)
    return problems


def changed_sources(record: dict, folder: Path) -> list[str]:
    """The evidence files that are missing or differ from their recorded hash."""
    out = []
    for name, entry in record["evidence"].items():
        source = folder / entry["path"]
        try:
            if sha256(source) != entry["sha256"]:
                out.append(f"evidence {name}: {entry['path']} changed (sha256 differs)")
        except OSError:
            out.append(f"evidence {name}: {entry['path']} is missing or cannot be read")
    return out
