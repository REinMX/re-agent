#!/usr/bin/env python3
"""Gate before an ES or ES-MDA run: refuse it without a screening record that matches the config. Read-only.

Errors (exit 1, no command printed):
    AHM-PRE-NO-SCREENING   no screening record, or one that is malformed or not schema ahm-screening/2
                           (screen_params.py writes it; an older or damaged record needs a new screening)
    AHM-PRE-EVIDENCE       an input file of the screening (design, residuals, observations, influence) is
                           missing, or its hash differs from the record
    AHM-PRE-NO-OBSERVATIONS  the record was made without --observations. It is a diagnostic and is never ready
    AHM-PRE-NO-ANALYSIS    the record has no AHM_ANALYSIS result and no reason why it did not run
    AHM-PRE-DROPPED        a parameter that the screening dropped is still sampled by a GEN_KW file
    AHM-PRE-UNSCREENED     a sampled GEN_KW parameter is not in the record
    AHM-PRE-PRIOR-CHANGED  a sampled GEN_KW parameter has another distribution or argument than the screened one
    AHM-PRE-MEMBERS        NUM_REALIZATIONS below ahm_defaults.MEMBERS (100), unless --allow-members REASON
    AHM-PRE-WEIGHTS        invalid ES-MDA weights or non-finite normalization
A prior is compared by its distribution and its numbers, not by file bytes. Comments, number spelling, file
order, and moved or split files do not change it. A screened parameter can leave the files or become CONST.
Notes: the seed rule (ahm_defaults.MIN_SEEDS), and with --deck the warnings of the AHM physics checklist
(deck-scal-pvt-qc physics_checklist.py).
When there is no error it prints a shell-safe command. Native ERT loading remains unverified.

    ahm_preflight.py <config>.ert --screening <dir>/screening.json [--weights 4,2,1] [--deck <CASE>.DATA] [--json]
Exit code: 0 = ready, 1 = refused, 2 = usage error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]


def _load(name: str, folder: Path):
    spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


prior_qc = _load("prior_qc", SKILLS / "history-match-qc" / "scripts")
ahm_defaults = _load("ahm_defaults", SKILLS / "history-match-qc" / "scripts")
ert_config_map = _load("ert_config_map", SKILLS / "ert-config-author-lint" / "scripts")
ert_config_check = _load("check_ert_config", SKILLS / "ert-config-author-lint" / "scripts")
evidence = _load("screening_evidence", HERE)
obs_qc = _load("obs_qc", SKILLS / "history-match-qc" / "scripts")
SCHEMA = evidence.SCHEMA
runpath_lib = _load("runpath_lib", SKILLS / "stage-contract" / "scripts")


@dataclass(frozen=True)
class Item:
    code: str
    severity: str  # error or note
    message: str


@dataclass(frozen=True)
class ExecutionRequest:
    argv: tuple[str, ...]
    cwd: Path
    mode: str
    config: Path
    weights_text: str | None
    realizations_text: str | None


@dataclass(frozen=True)
class Preflight:
    config: str
    sampled: tuple[str, ...]
    members: int | None
    selected_members: tuple[int, ...]
    items: tuple[Item, ...]
    argv: tuple[str, ...] | None
    cwd: Path
    verification: str = "compatibility_only"
    native_status: str = "unverified"

    @property
    def ready(self) -> bool:
        return self.argv is not None and not any(i.severity == "error" for i in self.items)

    @property
    def command(self) -> str | None:
        return shlex.join(self.argv) if self.argv is not None else None


def _request_from_argv(argv: tuple[str, ...], cwd: Path) -> ExecutionRequest | None:
    if not argv:
        raise ValueError("empty command")
    executable = Path(argv[0]).name
    if executable.startswith("python") and any(argv[n : n + 2] == ("-m", "ert") for n in range(1, len(argv))):
        raise ValueError("unsupported managed form: python -m ert; use direct ert")
    if executable in {"sh", "bash", "dash", "zsh", "ksh"}:
        words = []
        for word in argv[1:]:
            try:
                words.extend(shlex.split(word))
            except ValueError:
                continue
        if any(Path(word).name == "ert" for word in words):
            raise ValueError("unsupported managed form: shell wrapper for ert; use direct ert")
    if executable != "ert":
        return None
    values: dict[str, str] = {}
    flags = {"--verbose", "--color-always", "--disable-monitoring"}
    common = {"--target-ensemble", "--realizations", "--experiment-name", "--port-range"}
    mode = None
    config = None
    n = 1
    while n < len(argv):
        word = argv[n]
        if word == "--version" and mode is None:
            return None
        if word.startswith("--"):
            option, equals, value = word.partition("=")
            allowed = {"--logdir", "--read-only"} if mode is None else common | flags
            if mode == "es_mda":
                allowed |= {"--weights", "--restart-ensemble-id"}
            if option not in allowed:
                raise ValueError(f"unknown or unsupported ERT option {option}")
            if option in values:
                raise ValueError(f"duplicate singleton option {option}")
            if option in flags | {"--read-only"}:
                if equals:
                    raise ValueError(f"flag {option} does not take a value")
                value = "true"
            elif not equals:
                n += 1
                if n == len(argv) or argv[n].startswith("--"):
                    raise ValueError(f"missing value for {option}")
                value = argv[n]
            if not value:
                raise ValueError(f"empty value for {option}")
            values[option] = value
        elif mode is None:
            mode = word
            if mode in {"ensemble_experiment", "test_run", "gui", "lint", "api", "--version"}:
                return None
            if mode not in {"es_mda", "ensemble_smoother"}:
                raise ValueError(f"unsupported or unknown ERT dispatch {mode!r}")
        elif config is None:
            if word.startswith("-"):
                raise ValueError(f"{word} is an option, not a config path")
            config = word
        else:
            raise ValueError(f"unexpected extra positional {word!r}")
        n += 1
    if mode is None or config is None:
        raise ValueError("direct ERT update needs a mode and one config positional")
    if mode == "es_mda" and "--weights" not in values:
        raise ValueError("ES-MDA omitted weights default is unverified; supply explicit --weights")
    if "--port-range" in values:
        ports = values["--port-range"].split("-")
        if (
            len(ports) != 2
            or not all(p.isdecimal() for p in ports)
            or not (0 < int(ports[0]) < int(ports[1]) < 65536)
        ):
            raise ValueError("invalid --port-range; expected a-b within 1..65535")
    return ExecutionRequest(
        argv, cwd, mode, (cwd / config).resolve(), values.get("--weights"), values.get("--realizations")
    )


def check(
    *,
    argv: tuple[str, ...],
    cwd: Path,
    screening: Path | None,
    allow_members: str | None = None,
    deck: Path | None = None,
) -> Preflight | None:
    argv, cwd = tuple(argv), Path(cwd).resolve()
    try:
        request = _request_from_argv(argv, cwd)
        if request is None:
            return None
        if allow_members is not None and not allow_members.strip():
            raise ValueError("--allow-members needs a nonblank reason")
        return _check_request(request, screening, allow_members, deck)
    except (OSError, ValueError) as error:
        return Preflight("", (), None, (), (Item("AHM-PRE-INVOCATION", "error", str(error)),), None, cwd)


def config_view(config: Path):
    mapper = ert_config_map.Mapper(config)
    mapper.parse(mapper.root)
    mapper.finish()
    files = []
    for item in mapper.parameters:
        if item["keyword"] == "GEN_KW" and item["options"]:
            path = Path(item["options"][0])
            files.append(path if path.is_absolute() else Path(item["file"]).parent / path)
    sources = [mapper.root, *(Path(item["target"]) for item in mapper.includes if item["status"] == "ok")]
    for source in sources:
        for line in source.read_text().splitlines():
            words = ert_config_map.tokens(ert_config_map.strip_comment(line))
            if words and words[0] == "DESIGN_MATRIX":
                mapper.settings["DESIGN_MATRIX"] = {"value": " ".join(words[1:])}
    return files, mapper


def gen_kw_files(config: Path) -> tuple[list[Path], dict]:
    files, mapper = config_view(config)
    return files, mapper.settings


def observation_binding(mapper, record: dict | None) -> list[Item]:
    """Bind the config's actual summary observations to the screened inventory."""
    items = [
        Item(
            "AHM-PRE-CONFIG",
            "error",
            f"{problem['message']} ({Path(problem['file']).name}:{problem['line']})",
        )
        for problem in mapper.problems
        if problem["severity"] == "error"
    ]
    refs = [ref for ref in mapper.refs if ref["kind"] == "OBS_CONFIG"]
    if not refs:
        items.append(Item("AHM-PRE-OBS", "note", "no OBS_CONFIG; assimilation observations were not bound"))
        return items
    ref = refs[-1]
    if ref["status"] != "ok":
        items.append(Item("AHM-PRE-OBS", "error", f"OBS_CONFIG {ref['path']} is {ref['status']}"))
        return items
    if record is None:
        return items
    try:
        found = obs_qc.summary_inventory(Path(ref["path"]))
    except ValueError as error:
        items.append(Item("AHM-PRE-OBS", "error", str(error)))
        return items
    expected = record.get("observation_metadata") or {}
    if set(found) != set(expected):
        items.append(
            Item(
                "AHM-PRE-OBS",
                "error",
                f"OBS_CONFIG names {sorted(found)}, screening names {sorted(expected)}",
            )
        )
        return items
    for name, meta in expected.items():
        row = found[name]
        for column in ("KEY", "DATE", "OBSERVED", "ERROR"):
            if column not in meta:
                continue
            left, right = row.get(column), meta[column]
            same = left == right if column in ("KEY", "DATE") else float(left) == float(right)
            if not same:
                items.append(
                    Item(
                        "AHM-PRE-OBS",
                        "error",
                        f"{column} of {name} is {left!r} in OBS_CONFIG and {right!r} in the screening inventory",
                    )
                )
    return items


def read_record(screening: Path) -> tuple[dict | None, list[Item]]:
    """The parsed record, or the reasons why it cannot be used. Never raises on a damaged file."""
    try:
        record = json.loads(screening.read_text())
        problems = evidence.record_problems(record)
    except (OSError, ValueError) as error:
        problems = [f"it cannot be read as JSON ({error})"]
    if problems:
        text = (
            f"{screening} is not a usable {SCHEMA} record: {'; '.join(problems)}. Run screen_params.py again"
        )
        return None, [Item("AHM-PRE-NO-SCREENING", "error", text)]
    items = [
        Item(
            "AHM-PRE-EVIDENCE", "error", f"{message}. The screening input changed. Run screen_params.py again"
        )
        for message in evidence.changed_sources(record, screening.resolve().parent)
    ]
    if not record["domain"]["observations_authoritative"]:
        items.append(Item("AHM-PRE-NO-OBSERVATIONS", "error", "the record has no independent observation"
            " inventory (a diagnostic). Run screen_params.py again with --observations"))  # fmt: skip
    return record, items


def _is_zero(text: str) -> bool:
    try:
        return float(text.strip()) == 0
    except ValueError:
        return False


def _check_request(
    request: ExecutionRequest, screening: Path | None, allow_members: str | None, deck: Path | None
) -> Preflight:
    config, weights = request.config, request.weights_text
    files, mapper = config_view(config)
    settings = mapper.settings
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise ValueError(f"GEN_KW file not found: {missing}")
    priors = evidence.read_priors(files)
    sampled = [name for name, p in priors.items() if p["distribution"] != "CONST"]
    members = None
    if "NUM_REALIZATIONS" in settings:
        try:
            members = int(settings["NUM_REALIZATIONS"]["value"])
        except ValueError:
            members = None
    items: list[Item] = []
    add = items.append
    selected: set[int] = set()
    if members is not None and members > 0:
        if request.realizations_text is None:
            selected = set(range(members))
        else:
            for part in request.realizations_text.split(","):
                group = runpath_lib.parse_realizations(part)
                if group & selected:
                    raise ValueError("duplicate or overlapping realization selection")
                if any(n >= members for n in group):
                    raise ValueError(f"realization selection out of bounds for NUM_REALIZATIONS {members}")
                selected.update(group)
    if "DESIGN_MATRIX" in settings:
        selected = set()
        add(Item("AHM-PRE-MEMBERS", "error", "DESIGN_MATRIX effective selection is unverified"))
    if weights is not None:
        values, errors = ert_config_check.parse_weights(weights)
        zeros = any(_is_zero(item) for item in weights.split(","))
        if not errors:
            inverse = sum(1.0 / value for value in values)
            used = [value * inverse for value in values]
            if not math.isfinite(inverse) or not all(math.isfinite(value) for value in used):
                errors.append("weight inverse sum or normalized weights are not finite")
            else:
                add(
                    Item(
                        "AHM-PRE-WEIGHTS",
                        "note",
                        f"ES-MDA weights: sum(1/w) = {inverse:.4g}. ERT rescales them to w * sum(1/w) = "
                        + ", ".join(f"{value:.4g}" for value in used)
                        + " and runs with those. The command keeps the original weights"
                        + (". ERT 23 drops zero weights; ERT 26 refuses them" if zeros else ""),
                    )
                )
        for message in errors:
            add(Item("AHM-PRE-WEIGHTS", "error", f"ES-MDA weights: {message}"))
    record = None
    if screening is None or not screening.is_file():
        add(Item("AHM-PRE-NO-SCREENING", "error", "no screening record. Run a one-by-one design (make_design.py"
            " --mode onebyone), misfit_report.py on it, AHM_ANALYSIS (run_ahm_analysis.py, ahm_influence.py), then"
            " screen_params.py"))  # fmt: skip
    else:
        record, found = read_record(screening)
        items.extend(found)
    if record is not None:
        current_priors = evidence.prior_snapshot(priors)
        analysis = record["ahm_analysis"]
        if not analysis or analysis.strip() in ("not run:", "not run: "):
            add(Item("AHM-PRE-NO-ANALYSIS", "error", "the record has no AHM_ANALYSIS result and no reason"))
        elif analysis.startswith("not run:"):
            add(Item("AHM-PRE-NO-ANALYSIS", "note", f"AHM_ANALYSIS {analysis}"))
        screened = {p["name"]: p for p in record["parameters"]}
        bare = {prior_qc.bare(n): n for n in screened}
        for name in sampled:
            entry = screened.get(name) or screened.get(bare.get(prior_qc.bare(name), ""))
            if entry is None:
                add(Item("AHM-PRE-UNSCREENED", "error", f"{name} is sampled but not in the screening record"))
            elif not entry["keep"]:
                add(Item("AHM-PRE-DROPPED", "error", f"{name} is sampled, but the screening dropped it: "
                    + "; ".join(entry["reasons"]) + ". Historical non-identifiability is not forecast irrelevance."
                    " Choose retain forecast uncertainty, exclude it from the update, reparameterize, or fix it"
                    " with a stated reason. Do not remove forecast uncertainty only to pass this gate"))  # fmt: skip
            else:
                screened_prior = evidence.lookup(name, record["priors"])
                if screened_prior != current_priors[name]:
                    add(Item("AHM-PRE-PRIOR-CHANGED", "error", f"{name} is sampled with a prior that differs from the"
                        f" screened one: {evidence.describe(screened_prior)} became {evidence.describe(current_priors[name])}."
                        " Run screen_params.py again"))  # fmt: skip
    items.extend(observation_binding(mapper, record))
    need = int(ahm_defaults.MEMBERS.value)
    if members is None:
        add(Item("AHM-PRE-MEMBERS", "error", "NUM_REALIZATIONS is not a literal number"))
    elif members < 1:
        add(Item("AHM-PRE-MEMBERS", "error", f"NUM_REALIZATIONS {members} is not positive"))
    elif len(selected) < need:
        if allow_members:
            add(
                Item(
                    "AHM-PRE-MEMBERS",
                    "note",
                    f"{len(selected)} selected members, below {need}, allowed: {allow_members}",
                )
            )
        else:
            add(Item("AHM-PRE-MEMBERS", "error", f"selected members {len(selected)} is below {need}"
                f" ({ahm_defaults.MEMBERS.source}). Raise it, or give --allow-members REASON"))  # fmt: skip
    seeds = int(ahm_defaults.MIN_SEEDS.value)
    seed = settings.get("RANDOM_SEED", {}).get("value", "not set")
    add(
        Item(
            "AHM-PRE-SEEDS", "note", f"RANDOM_SEED {seed}. Run {seeds} seeds before a claim about this setup"
        )
    )
    if deck is not None:
        physics = _load("physics_checklist", SKILLS / "deck-scal-pvt-qc" / "scripts")
        parsed = physics.read_deck(deck)
        ert = physics.read_ert(config)
        found = physics.report(parsed, ert, physics.checklist(parsed, ert))
        for item in found["items"]:
            if item["severity"] == "warning":
                add(Item(item["code"], "note", f"physics checklist {item['status']}: {item['reason']}"))
    launch = None if any(item.severity == "error" for item in items) else request.argv
    return Preflight(
        str(config), tuple(sampled), members, tuple(sorted(selected)), tuple(items), launch, request.cwd
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", type=Path)
    ap.add_argument("--screening", type=Path, help="screening.json of screen_params.py")
    ap.add_argument("--weights", help="ES-MDA weights, for example 4,2,1. Without: ensemble_smoother")
    ap.add_argument("--allow-members", metavar="REASON", help="accept fewer than ahm_defaults.MEMBERS")
    ap.add_argument("--deck", type=Path, help="the DATA file, for the AHM physics checklist")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.config.is_file() or (args.deck is not None and not args.deck.is_file()):
        print("ERROR: the config or the deck is not a file", file=sys.stderr)
        return 2
    if args.allow_members is not None and not args.allow_members.strip():
        print("ERROR: --allow-members needs a reason", file=sys.stderr)
        return 2
    try:
        config = args.config.resolve()
        mode = "es_mda" if args.weights is not None else "ensemble_smoother"
        command = ("ert", mode, config.name, "--target-ensemble", "iter-%d")
        if args.weights is not None:
            command += ("--weights", args.weights)
        command += ("--disable-monitoring",)
        result = check(
            argv=command,
            cwd=config.parent,
            screening=args.screening,
            allow_members=args.allow_members,
            deck=args.deck,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    assert result is not None
    if args.json:
        print(
            json.dumps(
                {**asdict(result), "ready": result.ready, "command": result.command}, indent=1, default=str
            )
        )
    else:
        for item in result.items:
            print(f"{item.severity.upper()} {item.code}: {item.message}")
        print(
            "AHM compatibility passed; native ERT loading unverified"
            if result.ready
            else "AHM compatibility refused"
        )
        print(f"PREFLIGHT {'ready' if result.ready else 'refused'}: {len(result.sampled)} sampled parameters,"
              f" {result.members} members")  # fmt: skip
        if result.command:
            print(f"COMMAND (from {args.config.resolve().parent}): {result.command}")
    return 0 if result.ready else 1


if __name__ == "__main__":
    sys.exit(main())
