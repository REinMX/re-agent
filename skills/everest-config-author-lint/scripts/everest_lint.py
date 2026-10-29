#!/usr/bin/env python3
"""Everest lint and deterministic YAML checks. No simulator execution."""

import argparse
import importlib.util
import json
import math
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import yaml


def read_yaml(path):
    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if key in result:
                raise ValueError(f"duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    data = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueLoader)
    if not isinstance(data, dict):
        raise ValueError("expected a YAML mapping")
    return data


def read_contract(path):
    """Use the repository loader, including its restricted contract syntax."""
    source = Path(__file__).resolve().parents[3] / "tools/doctor.py"
    spec = importlib.util.spec_from_file_location("everest_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.read_runtime_contract(Path(path))


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def control_variables(cfg):
    """Control names as constraints use them: group.variable, or group.variable.index when indexed."""
    for group in cfg.get("controls", []):
        for variable in group.get("variables", []):
            values = {**group, **variable}
            base = f"{group['name']}.{variable['name']}"
            if "index" in variable:
                yield f"{base}.{variable['index']}", values
            elif isinstance(values.get("initial_guess"), list):
                for i in range(1, len(values["initial_guess"]) + 1):
                    yield f"{base}.{i}", values
            else:
                yield base, values


def installed_job_findings(cfg):
    """Resolve maintained jobs through their ERT plugin, even in script-only mode."""
    # These names identify a missing dependency; valid installed names come from the plugin.
    maintained = {
        "npv",
        "well_constraints",
        "drill_planner",
        "well_swapping",
        "select_wells",
        "add_templates",
        "schmerge",
        "well_trajectory",
        "well_filter",
    }
    commands = [
        shlex.split(step)[0]
        for step in cfg.get("forward_model", [])
        if isinstance(step, str) and step.strip()
    ]
    custom = {job["name"] for job in cfg.get("install_jobs", [])}
    used = set(commands) - custom
    if not used:
        return []
    try:
        from everest_models.forward_models import installable_forward_model_steps

        installed = {step().name for step in installable_forward_model_steps().data}
        missing = used & maintained - installed
        missing |= {name for name in used & installed if not shutil.which(f"fm_{name}")}
        message = f"everest-models jobs or executables unavailable: {sorted(missing)}; activate the installed environment"
    except (ImportError, ValueError, TypeError) as error:
        missing = used & maintained
        message = (
            f"config uses everest-models jobs {sorted(missing)}, but its ERT plugin is unavailable: {error}"
        )
    return (
        [
            {
                "severity": "error",
                "path": "forward_model",
                "message": message,
                "source": "script",
                "code": "missing_dependency",
            }
        ]
        if missing
        else []
    )


def lint(cfg, contract=None):
    findings = installed_job_findings(cfg)

    def add(severity, path, message):
        findings.append({"severity": severity, "path": path, "message": message, "source": "script"})

    controls = cfg.get("controls")
    if not isinstance(controls, list) or not controls:
        add("error", "controls", "a nonempty control list is required")
        controls = []
    names = set()
    for index, group in enumerate(controls):
        path = f"controls[{index}]"
        if not isinstance(group, dict) or not isinstance(group.get("name"), str):
            add("error", path, "control needs a name and mapping")
            continue
        if group["name"] in names:
            add("error", path, "duplicate control name")
        names.add(group["name"])
        variables = group.get("variables", [])
        if not isinstance(variables, list) or not variables:
            add("error", path, "variables must be a nonempty list")
            continue
        wells = []
        priorities = []
        for j, variable in enumerate(variables):
            vp = f"{path}.variables[{j}]"
            if not isinstance(variable, dict) or not isinstance(variable.get("name"), str):
                add("error", vp, "variable needs a name and mapping")
                continue
            wells.append((variable["name"], variable.get("index")))
            values = {**group, **variable}
            low, high = values.get("min"), values.get("max")
            if not number(low) or not number(high) or low >= high:
                add("error", vp, "min/max must be finite and min < max")
                continue
            guess = values.get("initial_guess")
            if isinstance(guess, list) and guess and "index" not in variable:
                for k, item in enumerate(guess):
                    if not number(item) or not low <= item <= high:
                        add("error", vp, f"initial_guess[{k}] must be within bounds")
            elif not number(guess) or not low <= guess <= high:
                add("error", vp, "initial_guess must be within bounds")
            perturbation = values.get("perturbation_magnitude")
            if not number(perturbation) or perturbation <= 0 or perturbation > high - low:
                add("error", vp, "perturbation_magnitude must be positive and no larger than range")
            elif not 0.01 <= perturbation / (high - low) <= 0.10:
                add("warning", vp, "perturbation_magnitude should be about 1-10% of range")
            for key in ("scale", "scaling", "auto_scale"):
                if key not in values:
                    continue
                value = values[key]
                if key == "auto_scale":
                    valid = isinstance(value, bool)
                else:
                    valid = number(value) and value > 0
                if not valid:
                    add("error", vp, f"{key}: invalid scaling value")
            if high - low > 1e6 and not any(k in values for k in ("scale", "scaling", "auto_scale")):
                add("warning", vp, "large range: review scaling and units")
            if "priority" in variable:
                priority = variable["priority"]
                if not isinstance(priority, int) or isinstance(priority, bool):
                    add("error", vp, "priority must be an integer")
                priorities.append(priority)
        if len(wells) != len(set(wells)):
            add("error", path, "duplicate wells or variable names")
        if priorities and (len(priorities) != len(wells) or len(set(priorities)) != len(priorities)):
            add("error", path, "priority duplicates or missing wells")
        order = group.get("well_order", cfg.get("well_order"))
        if order is not None:
            if (
                not isinstance(order, list)
                or not all(isinstance(w, str) for w in order)
                or len(order) != len(set(order))
                or set(order) != {name for name, _ in wells}
            ):
                add("error", path, "well_order duplicates or missing wells")
        if "order" in group["name"].lower() or "priority" in group["name"].lower():
            guesses = [v.get("initial_guess", group.get("initial_guess")) for v in variables]
            if any(not isinstance(v, int) or isinstance(v, bool) for v in guesses):
                add("error", path, "well order initial priorities must be integers")
            elif len(guesses) != len(set(guesses)):
                add("error", path, "duplicate initial well priorities")
    objectives = cfg.get("objective_functions", [])
    if not isinstance(objectives, list) or not objectives:
        add("error", "objective_functions", "a nonempty objective list is required")
        objectives = []
    for index, item in enumerate(objectives):
        path = f"objective_functions[{index}]"
        weight = item.get("weight", 1)
        if not number(weight) or weight == 0:
            add("error", path, "weight must be finite and nonzero")
        elif (
            any(term in str(item.get("name", "")).lower() for term in ("water", "cost", "expense"))
            and weight > 0
        ):
            add("warning", path, "cost terms should have negative weight for maximization; check sign")
        for key in ("scale", "normalization"):
            if key in item and (not number(item[key]) or item[key] <= 0):
                add("error", path, f"{key} must be positive and finite")
    for kind in ("input_constraints", "output_constraints"):
        for index, item in enumerate(cfg.get(kind, [])):
            path = f"{kind}[{index}]"
            low, high, target = (item.get(k) for k in ("lower_bound", "upper_bound", "target"))
            if all(v is None for v in (low, high, target)):
                add("error", path, "constraint needs upper/lower bound or target")
            if any(v is not None and not number(v) for v in (low, high, target)):
                add("error", path, "constraint limits must be finite numbers")
            elif low is not None and high is not None and low > high:
                add("error", path, "lower_bound exceeds upper_bound")
            if kind == "input_constraints":
                weights = item.get("weights", {})
                known = {name for name, _ in control_variables(cfg)}
                if not weights or any(k not in known or not number(v) for k, v in weights.items()):
                    add("error", path, "linear input weights must reference known controls")
            elif not item.get("name"):
                add("error", path, "nonlinear output constraint needs a name")
    realizations = cfg.get("model", {}).get("realizations")
    if (
        not isinstance(realizations, list)
        or not realizations
        or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in realizations)
    ):
        add("error", "model.realizations", "nonempty nonnegative integer realization list required")
        realizations = []
    elif len(set(realizations)) != len(realizations):
        add("error", "model.realizations", "duplicate realization ids")
    opt = cfg.get("optimization", {})
    minimum = opt.get("min_realizations_success")
    if minimum is not None and (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 1
        or minimum > len(realizations)
    ):
        add(
            "error",
            "optimization.min_realizations_success",
            "min_realizations_success exceeds ensemble or is invalid",
        )
    cvar = opt.get("cvar", {})
    if "number_of_realizations" in cvar:
        add("error", "optimization.cvar", "selects BEST N realizations on this pin, use percentile")
    if "percentile" in cvar and (not number(cvar["percentile"]) or not 0 < cvar["percentile"] <= 1):
        add("error", "optimization.cvar.percentile", "percentile must be a fraction in (0, 1]")
    if "slsqp" in str(opt.get("algorithm", "")).lower():
        add("warning", "optimization.algorithm", "stalls at initial guess on verified pin, use COBYLA")
    if contract:
        queue = cfg.get("simulator", {}).get("queue_system", {})
        queue = queue.get("name", queue.get("system")) if isinstance(queue, dict) else queue
        expected = contract.get("queue_system") or contract.get("profile", {}).get("queue", {}).get("system")
        if expected and str(queue).upper() != str(expected).upper():
            add("error", "simulator.queue_system", "queue.system mismatch with site contract")
    return findings


def parse_everest_output(output, config):
    """Parse validation locations, messages, deprecation paragraphs, and validity."""
    findings = []

    def add(severity, path, message):
        findings.append({"severity": severity, "path": path, "message": message, "source": "everest"})

    location = str(config)
    validation = False
    message_lines = []

    def flush():
        if message_lines:
            add("error", location, " ".join(message_lines))
            message_lines.clear()

    for line in output.splitlines():
        stripped = line.strip()
        if re.search(r"Found \d+ validation errors?", stripped):
            flush()
            validation = True
        elif validation and re.match(r"line:\s*\d+", stripped):
            flush()
            location = stripped
        elif validation and stripped.startswith("*"):
            flush()
            message_lines.append(stripped.lstrip("* "))
        elif validation and message_lines and stripped and line[:1].isspace():
            message_lines.append(stripped)
        elif validation and stripped and not line[:1].isspace():
            flush()
            location = stripped
        else:
            flush()
    flush()
    for paragraph in re.split(r"\n\s*\n", output):
        if re.search(r"\bis (?:also )?deprecated\b|\b\w*Warning:|\bWARNING\b|\bwarning:", paragraph):
            add("warning", str(config), " ".join(paragraph.split()))
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if lines and re.search(r"\bis valid\.?$", lines[-1]):
        add("info", str(config), lines[-1])
    return findings


def real_lint(config, executable=None, mode="auto", contract=None):
    """Execute only Everest's lint subcommand, with a bounded runtime."""

    def finding(severity, message):
        return {"severity": severity, "path": str(config), "message": message, "source": "everest"}

    if mode == "never":
        return [], "real lint not run (--with-everest never)"
    configured = executable or (contract or {}).get("everest_executable", "")
    executable = shutil.which(configured.strip() if configured and configured.strip() else "everest")
    if not executable:
        return [
            finding(
                "error" if mode == "always" else "warning",
                "everest not found: real lint NOT run (not a pass)",
            )
        ], "real lint not run"
    try:
        result = subprocess.run(
            [executable, "lint", str(config)], capture_output=True, text=True, timeout=300, check=False
        )
    except FileNotFoundError:
        return [
            finding(
                "error" if mode == "always" else "warning",
                "everest not found: real lint NOT run (not a pass)",
            )
        ], "real lint not run"
    except (OSError, subprocess.TimeoutExpired) as error:
        return [finding("error", f"real lint failed: {error}")], "real lint did not complete"
    output = result.stdout + "\n" + result.stderr
    findings = parse_everest_output(result.stdout, config) + parse_everest_output(result.stderr, config)
    if result.returncode and not any(f["severity"] == "error" for f in findings):
        tail = "\n".join(output.strip().splitlines()[-20:])[-4000:]
        findings.append(finding("error", f"everest lint exited {result.returncode}: {tail}"))
    return findings, f"real lint run (exit {result.returncode})"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--everest-bin", help="Everest executable (default: find on PATH)")
    parser.add_argument("--with-everest", choices=("auto", "always", "never"), default="auto")
    args = parser.parse_args(argv)
    real_findings = []
    summary = "real lint not run"
    try:
        contract = read_contract(args.contract) if args.contract else None
        real_findings, summary = real_lint(args.config, args.everest_bin, args.with_everest, contract)
        findings = real_findings + lint(read_yaml(args.config), contract)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError) as error:
        findings = real_findings + [
            {"severity": "error", "path": str(args.config), "message": str(error), "source": "script"}
        ]
    has_errors = any(f["severity"] == "error" for f in findings)
    status = "error" if has_errors else "pass" if summary.startswith("real lint run") else "incomplete"
    if args.format == "json":
        output = json.dumps(
            {"config": str(args.config), "status": status, "summary": summary, "findings": findings}, indent=2
        )
    else:
        output = "# Everest config check\n\n" + str(args.config) + f"\n\nStatus: {status}\n{summary}\n\n"
        output += (
            "\n".join(
                f"- {f['severity'].upper()} [{f['source']}] `{f['path']}`: {f['message']}" for f in findings
            )
            or "No findings."
        )
    if args.out:
        args.out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    if any(f.get("code") == "missing_dependency" for f in findings):
        return 2
    return int(has_errors)


if __name__ == "__main__":
    raise SystemExit(main())
