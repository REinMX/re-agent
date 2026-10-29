#!/usr/bin/env python3
"""Validate ERT configurations the way ERT loads them for a real run. Read-only.

``ert lint`` is not a quality gate, for two reasons:

1. It always exits 0. It prints configuration errors and still reports success.
2. It parses without plugins. A real run parses with plugins. Forward model steps
   and workflow jobs that ERT, res2df, semeio and other packages register are then
   reported as missing, although the config is fine.

This script does the parse that a real run does. It turns the result into an exit code.
A step that no installed package supplies fails the check. A step that a plugin supplies
resolves, and ``--verbose`` names the package.

Run it from any folder::

    check_ert_config.py ert/model/*.ert
    check_ert_config.py --verbose ert/model/drogon_ahm.ert

The result depends on what is installed. A step from semeio needs semeio in the
environment. Without it the config cannot run in that environment either.

AHM settings (``--ahm``, standard library, no ERT needed with ``--ahm-only``)::

    check_ert_config.py --ahm --weights 4,2,1 ert/model/drogon_ahm.ert
    check_ert_config.py --ahm-only ert/model/drogon_ahm.ert

It reads the config and its includes and reviews the settings of an update:
ES-MDA weights (from ``--weights``, or from a comment such as ``-- es_mda --weights 4,2,1``),
ENKF_ALPHA, STD_CUTOFF, MIN_REALIZATIONS, NUM_REALIZATIONS, RANDOM_SEED, UPDATE_LOG_PATH,
and the ANALYSIS_SET_VAR lines. ERT 23 rescales ES-MDA weights so that sum(1/w) = 1 and ignores
a weight of 0. The script reports the weights that ERT then uses. An AHM "review" item does not
fail the gate. A weight that is not a number, or is negative, is an error.

Exit code: 0 = all configs load cleanly, 1 = a problem, 2 = ERT or its plugins cannot load.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import math
import re
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEIGHTS_RE = re.compile(r"--weights[=\s]+(?:\"([^\"]+)\"|'([^']+)'|(\S+))")
# Defaults of ERT 23 (ert/config/analysis_config.py).
ERT_DEFAULTS = {"ENKF_ALPHA": "3.0", "STD_CUTOFF": "1e-06", "UPDATE_LOG_PATH": "update_log"}

# Warnings that the user accepts on purpose. Each entry is (text fragment, reason).
# The list is empty by default. Fill it with --accept 'fragment=reason'.
ACCEPTED_WARNINGS: list[tuple[str, str]] = []


class Diagnostic:
    """One thing the parser said about one config."""

    def __init__(self, config: Path, severity: str, message: str, note: str = "") -> None:
        self.config = config
        self.severity = severity
        self.message = " ".join(message.split())
        self.note = note

    def __str__(self) -> str:
        line = f"{self.severity:<8} {self.config.name}: {self.message}"
        return f"{line}\n{'':<9}({self.note})" if self.note else line


def accepted(message: str) -> str | None:
    """Return why a warning is tolerated, or None if it should fail the gate."""
    for fragment, reason in ACCEPTED_WARNINGS:
        if fragment in message:
            return reason
    return None


def check_config(config: Path, runtime_plugins: object) -> list[Diagnostic]:
    """Parse one config exactly as a run would, and report what came back."""
    from ert.config import ConfigValidationError, ErtConfig

    diagnostics: list[Diagnostic] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            # Relative paths in a config are resolved from its own directory,
            # which is why the documented commands are run from ert/model/.
            with contextlib.chdir(config.parent):
                ErtConfig.with_plugins(runtime_plugins).from_file(config.name)
        except ConfigValidationError as error:
            diagnostics += [Diagnostic(config, "error", message) for message in error.messages()]
        except Exception as error:  # a parser crash is still a broken config
            diagnostics.append(Diagnostic(config, "error", f"{type(error).__name__}: {error}"))

    for warning in caught:
        message = str(warning.message)
        if issubclass(warning.category, DeprecationWarning):
            # Upstream's problem, not this repository's. Visible, not fatal.
            diagnostics.append(Diagnostic(config, "note", message))
        elif (reason := accepted(message)) is not None:
            diagnostics.append(Diagnostic(config, "accepted", message, reason))
        else:
            diagnostics.append(Diagnostic(config, "warning", message))
    return diagnostics


def load_mapper():
    spec = importlib.util.spec_from_file_location("ert_config_map", HERE / "ert_config_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_defaults():
    path = Path(__file__).resolve().parents[2] / "history-match-qc" / "scripts" / "ahm_defaults.py"
    spec = importlib.util.spec_from_file_location("ahm_defaults", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("ahm_defaults", module)
    spec.loader.exec_module(module)
    return module


def parse_weights(text: str) -> tuple[list[float], list[str]]:
    """Parse ES-MDA weights as ERT 23 does. Return (weights without zeros, errors)."""
    weights, errors = [], []
    items = [item.strip() for item in text.split(",") if item.strip()]
    for item in items:
        try:
            value = float(item)
        except ValueError:
            errors.append(f"weight '{item}' is not a number. ERT reads plain numbers only")
            continue
        if not math.isfinite(value) or (value > 0 and not math.isfinite(1.0 / value)):
            errors.append(f"weight {item} or its inverse is not finite")
        elif value < 0:
            errors.append(f"weight {item} is negative")
        elif value > 0:
            weights.append(value)
    if not items or (not weights and not errors):
        errors.append(f"no usable weight in '{text}'")
    return weights, errors


def weights_from_comments(files: list[Path]) -> tuple[str, str] | None:
    """The first '--weights <list>' in the config files, with its location."""
    for file in files:
        try:
            lines = file.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            match = WEIGHTS_RE.search(line)
            if match:
                return next(g for g in match.groups() if g), f"{file.name}:{number}"
    return None


def ahm_lint(
    config: Path, weights: str | None, min_realizations: int, tolerance: float = 1e-3
) -> list[Diagnostic]:
    """Review the settings of an ES or ES-MDA update. Standard library only."""
    mapper_module = load_mapper()
    mapper = mapper_module.Mapper(config)
    mapper.parse(mapper.root)
    mapper.finish()
    settings = mapper.settings
    out: list[Diagnostic] = [
        Diagnostic(config, p["severity"], f"{p['message']} ({Path(p['file']).name}:{p['line']})")
        for p in mapper.problems
    ]

    def add(severity: str, message: str) -> None:
        out.append(Diagnostic(config, severity, message))

    def where(key: str) -> str:
        item = settings[key]
        return f"{Path(item['file']).name}:{item['line']}"

    source = "--weights"
    if weights is None:
        found = weights_from_comments([mapper.root, *(Path(i["target"]) for i in mapper.includes)])
        if found:
            weights, source = found[0], f"comment at {found[1]}"
    if weights is None:
        add(
            "note",
            "no ES-MDA weights given (--weights or a '--weights' comment). The weight check did not run",
        )
    else:
        values, errors = parse_weights(weights)
        for message in errors:
            add("error", f"ES-MDA weights ({source}): {message}")
        if values and not errors:
            inverse = sum(1.0 / w for w in values)
            used = [w * inverse for w in values]
            text = ", ".join(f"{w:.4g}" for w in values)
            if abs(inverse - 1.0) > tolerance:
                add(
                    "review",
                    f"ES-MDA weights {text} ({source}): sum(1/w) = {inverse:.4g}, not 1. ERT 23 rescales them to"
                    f" {', '.join(f'{w:.4g}' for w in used)}. Give the rescaled weights so that the log and the"
                    " plan agree",
                )
            else:
                add("note", f"ES-MDA weights {text} ({source}): sum(1/w) = {inverse:.4g}")
        if len(values) < len([w for w in weights.split(",") if w.strip()]) and not errors:
            add("review", f"ES-MDA weights ({source}): ERT ignores a weight of 0")

    for key in ("ENKF_ALPHA", "STD_CUTOFF"):
        if key not in settings:
            add("note", f"{key} is not set. ERT 23 uses {ERT_DEFAULTS[key]}")
            continue
        try:
            value = float(settings[key]["value"])
        except ValueError:
            add("error", f"{key} {settings[key]['value']} is not a number ({where(key)})")
            continue
        if not math.isfinite(value) or not value > 0:
            add("error", f"{key} {value:g} must be larger than 0 ({where(key)})")
        elif key == "ENKF_ALPHA" and value < 2:
            add(
                "review",
                f"ENKF_ALPHA {value:g} ({where(key)}) is low. More observations become outliers and are not used",
            )
        else:
            add("note", f"{key} {value:g} ({where(key)})")

    num = None
    if "NUM_REALIZATIONS" in settings:
        try:
            num = int(settings["NUM_REALIZATIONS"]["value"])
        except ValueError:
            add("note", f"NUM_REALIZATIONS {settings['NUM_REALIZATIONS']['value']} is not a literal number")
    if num is not None and num < min_realizations:
        add(
            "review",
            f"NUM_REALIZATIONS {num} ({where('NUM_REALIZATIONS')}) is below {min_realizations}. The update"
            " statistics are weak. Compare with the number of uncertain parameters (prior_qc.py)",
        )
    if "MIN_REALIZATIONS" not in settings:
        add(
            "review",
            "MIN_REALIZATIONS is not set. ERT 23 then needs all realizations to succeed before the update",
        )
    else:
        text = settings["MIN_REALIZATIONS"]["value"].strip()
        try:
            least = int(text) if "%" not in text else None
            if least is None:
                percentage = float(text.rstrip("%"))
                if not math.isfinite(percentage) or percentage < 0:
                    raise ValueError("invalid percentage")
                if num is not None:
                    least = math.ceil(num * percentage / 100)
        except (ValueError, OverflowError):
            add(
                "error",
                f"MIN_REALIZATIONS {text} is not an integer or a percentage ({where('MIN_REALIZATIONS')})",
            )
            least = None
        if least is not None and least != 0 and least < min_realizations:
            add(
                "review",
                f"MIN_REALIZATIONS {text} ({where('MIN_REALIZATIONS')}) lets the update continue with {least}"
                f" realizations, below {min_realizations}",
            )
    seeds = int(load_defaults().MIN_SEEDS.value)
    if "RANDOM_SEED" not in settings:
        add(
            "review",
            "RANDOM_SEED is not set. A repeated run gives other samples and another update. Set it, and run"
            f" {seeds} seeds before a claim about the setup",
        )
    else:
        add(
            "note",
            f"RANDOM_SEED {settings['RANDOM_SEED']['value']} ({where('RANDOM_SEED')}). One seed is one sample."
            f" Run {seeds} seeds before a claim about the setup (ahm_defaults.MIN_SEEDS)",
        )
    if "UPDATE_LOG_PATH" not in settings:
        add(
            "review",
            "UPDATE_LOG_PATH is not set. ERT 23 writes the update log to 'update_log' in the folder where ERT"
            " runs. Set it so that update_log_report.py can find the log",
        )
    else:
        add("note", f"UPDATE_LOG_PATH {settings['UPDATE_LOG_PATH']['value']} ({where('UPDATE_LOG_PATH')})")
    for line in settings.get("ANALYSIS_SET_VAR", {}).get("value", []):
        add("note", f"ANALYSIS_SET_VAR {line}")
        words = line.split()
        if len(words) >= 2 and words[1].upper() in LEGACY_LOCALIZATION:
            add(
                "warning",
                f"ANALYSIS_SET_VAR {line}: {words[1]} is the pre-ERT-26 switch. ERT 26 maps LOCALIZATION True to"
                " ADAPTIVE for every type and ignores False. Write the strategy per type:"
                f" {LEGACY_LOCALIZATION[words[1].upper()]}",
            )
    adaptive_threshold_review(settings.get("ANALYSIS_SET_VAR", {}).get("value", []), num, add)
    return out


def adaptive_threshold_review(lines: list[str], members: int | None, add) -> None:
    """Review an adaptive localization threshold below the sampling noise of a correlation.

    With N members, the sample correlation of two unrelated quantities has a spread of about
    1/sqrt(N). A threshold below 2/sqrt(N) keeps many such spurious correlations, so adaptive
    localization removes little. ERT 23 and ERT 26 use 3/sqrt(N) when no threshold is set.
    """
    adaptive, threshold = False, None
    for line in lines:
        words = line.split()
        if len(words) < 3:
            continue
        key, value = words[1].upper(), words[-1].upper()
        if words[0].upper() == "PARAMETERS" and value == "ADAPTIVE":
            adaptive = True
        elif key in LEGACY_LOCALIZATION and value == "TRUE":  # both make GEN_KW adaptive
            adaptive = True
        elif key == "LOCALIZATION_CORRELATION_THRESHOLD":
            try:
                threshold = float(words[2])
            except ValueError:
                add("error", f"ANALYSIS_SET_VAR {line}: the threshold is not a number")
    if not adaptive or threshold is None or not members or members < 1:
        return
    floor, default = 2 / math.sqrt(members), min(1.0, 3 / math.sqrt(members))
    if threshold < floor:
        add(
            "review",
            f"LOCALIZATION_CORRELATION_THRESHOLD {threshold:g} with NUM_REALIZATIONS {members} is below"
            f" 2/sqrt(N) = {floor:.3f}, the sampling noise of a correlation. Adaptive localization then keeps"
            f" spurious correlations. ERT's default is 3/sqrt(N) = {default:.3f}. Remove the threshold or raise it",
        )


LEGACY_LOCALIZATION = {
    "LOCALIZATION": "ANALYSIS_SET_VAR PARAMETERS GEN_KW|FIELD|SURFACE GLOBAL|ADAPTIVE",
    "DISTANCE_LOCALIZATION": "ANALYSIS_SET_VAR PARAMETERS FIELD DISTANCE (and SURFACE), GEN_KW ADAPTIVE",
}


def failed(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """The diagnostics that make the gate red."""
    return [d for d in diagnostics if d.severity in {"error", "warning"}]


def supplying_package(registration: object) -> str:
    """Which distribution registered this step or workflow job.

    The registration objects are all ERT's own model classes, so the class
    module says nothing. What the job actually *runs* does: a workflow job
    points at an ert_script class, and a forward model step at an executable
    inside its package.
    """
    script = getattr(registration, "ert_script", None)
    if script is not None:
        return script.__module__.split(".")[0]
    executable = getattr(registration, "executable", None)
    if executable is not None:
        parts = Path(str(executable)).parts
        if "site-packages" in parts:
            return parts[parts.index("site-packages") + 1]
    return type(registration).__module__.split(".")[0]


def describe_registrations(runtime_plugins: object) -> str:
    """Which package supplies each forward model step and workflow job."""
    lines = []
    for label, registry in (
        ("forward model step", runtime_plugins.installed_forward_model_steps),
        ("workflow job", runtime_plugins.installed_workflow_jobs),
    ):
        for name in sorted(registry):
            source = supplying_package(registry[name])
            lines.append(f"  {label:<19} {name:<24} from {source}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("configs", nargs="+", type=Path, help="ERT configs to validate")
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="list every plugin-registered step and the package supplying it",
    )
    ap.add_argument(
        "--accept",
        action="append",
        default=[],
        metavar="FRAGMENT=REASON",
        help="tolerate a warning that contains FRAGMENT. Give the reason. Repeat for more.",
    )
    ap.add_argument("--ahm", action="store_true", help="also review the AHM (ES / ES-MDA) settings")
    ap.add_argument("--ahm-only", action="store_true", help="review only the AHM settings. Does not need ERT")
    ap.add_argument("--weights", default=None, help="ES-MDA weights of the planned run, for example 4,2,1")
    ap.add_argument(
        "--min-realizations",
        type=int,
        default=int(load_defaults().MEMBERS.value),
        help="AHM review: weak below this ensemble size (default ahm_defaults.MEMBERS)",
    )
    ap.add_argument("--weights-tolerance", type=float, default=1e-3, help="AHM review: |sum(1/w) - 1| limit")
    args = ap.parse_args(argv)

    for item in args.accept:
        fragment, _, reason = item.partition("=")
        if not fragment or not reason:
            print(f"ERROR: --accept needs FRAGMENT=REASON, got: {item}", file=sys.stderr)
            return 2
        ACCEPTED_WARNINGS.append((fragment, reason))
    if args.min_realizations < 1 or not math.isfinite(args.weights_tolerance) or args.weights_tolerance < 0:
        print(
            "ERROR: --min-realizations must be positive and --weights-tolerance nonnegative", file=sys.stderr
        )
        return 2

    ahm: list[Diagnostic] = []
    if args.ahm or args.ahm_only or args.weights is not None:
        for config in args.configs:
            if not config.is_file():
                if args.ahm_only:  # otherwise the load check below reports it
                    ahm.append(Diagnostic(config, "error", "no such config file"))
                continue
            ahm += ahm_lint(config.resolve(), args.weights, args.min_realizations, args.weights_tolerance)
        for diagnostic in ahm:
            print(f"ahm {diagnostic}")
        reviews = [d for d in ahm if d.severity == "review"]
        print(f"AHM review: {len(failed(ahm))} error(s), {len(reviews)} item(s) to review")
        if args.ahm_only:
            return 1 if failed(ahm) else 0
        print()

    try:
        from ert.plugins import get_site_plugins
    except ImportError as error:
        print(
            f"ERROR: cannot import ert ({error}). Activate the environment of the project.", file=sys.stderr
        )
        return 2

    try:
        runtime_plugins = get_site_plugins()
    except Exception as error:  # a plugin can fail while it loads
        print(
            f"ERROR: ERT could not load its plugins ({type(error).__name__}).\n"
            "A plugin step often needs its program on PATH. Put the bin folder of the active\n"
            "environment on PATH and run again.\n"
            f"First line of the error: {str(error).splitlines()[0] if str(error) else ''}",
            file=sys.stderr,
        )
        return 2
    if args.verbose:
        print(describe_registrations(runtime_plugins))
        print()

    diagnostics: list[Diagnostic] = list(ahm)
    for config in args.configs:
        if not config.is_file():
            diagnostics.append(Diagnostic(config, "error", "no such config file"))
            continue
        found = check_config(config.resolve(), runtime_plugins)
        for diagnostic in found:
            print(diagnostic)
        if not failed(found):
            print(f"{'ok':<8} {config.name}: loads with all steps and workflows resolved")
        diagnostics += found

    problems = failed(diagnostics)
    print()
    if problems:
        print(
            f"{len(problems)} unresolved diagnostic(s) in "
            f"{len({d.config for d in problems})} of {len(args.configs)} configs"
        )
        return 1
    print(f"all {len(args.configs)} configs load cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
