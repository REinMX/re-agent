#!/usr/bin/env python3
"""AHM physics checklist of a simulator deck. Read-only. Standard library only.

It reads the deck and its INCLUDE files as text and checks that the physics an assisted history match
needs is in the model: an aquifer, one fault multiplier for each fault segment, kv/kh and shale barriers,
well PI multipliers, history-mode well controls, and rock compressibility.

    physics_checklist.py --deck CASE.DATA [--ert-config CASE.ert] [--json]

Each item has a status:
    present              the deck has it
    missing              the deck does not have it, or has it in a form that the update cannot use
    fixed-not-uncertain  the deck has it, but no ERT parameter makes it uncertain (needs --ert-config)
    unverified           the deck does not show it, and an include file that probably holds it is missing
    not-applicable       the deck has nothing for the check to apply to (for example no FAULTS)

A value is uncertain in the AHM when the deck has a template placeholder (<NAME> or {{ name }}),
or when the ERT config has a GEN_KW or FIELD parameter with a matching name. In an FMU case a forward model
often writes the include file. Then the file is missing in the repository and the GEN_KW name is the evidence.
Exit code: 0 = the checklist ran (missing items are warnings), 1 = the deck cannot be read, 2 = usage error.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
sys.path.insert(0, str(HERE))
from schedule_qc import deck_lines, records  # noqa: E402

SCHEMA_VERSION = 1
MAX_EVIDENCE = 8
PLACEHOLDER = re.compile(r"<([A-Za-z][A-Za-z0-9_]*)>|\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(path.stem, module)
    spec.loader.exec_module(module)
    return module


class Status(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    FIXED = "fixed-not-uncertain"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not-applicable"


WARN_STATUSES = {Status.MISSING, Status.FIXED, Status.UNVERIFIED}


@dataclass(frozen=True)
class Record:
    keyword: str
    values: tuple[str, ...]
    file: str
    line: int
    order: int

    def placeholders(self) -> list[str]:
        text = " ".join(self.values)
        return [a or b for a, b in PLACEHOLDER.findall(text)]


@dataclass(frozen=True)
class Evidence:
    file: str
    line: int
    keyword: str
    text: str

    @classmethod
    def of(cls, record: Record) -> Evidence:
        text = " ".join(record.values[:8]) + (" ..." if len(record.values) > 8 else "")
        return cls(record.file, record.line, record.keyword, text)


@dataclass
class ChecklistItem:
    code: str
    title: str
    status: Status
    reason: str
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    uncertain: bool | None = None
    info_only: bool = False

    @property
    def severity(self) -> str:
        if self.status in WARN_STATUSES:
            return "info" if self.info_only else "warning"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "status": self.status.value,
            "severity": self.severity,
            "uncertain": self.uncertain,
            "summary": self.summary,
            "reason": self.reason,
            "evidence": [asdict(e) for e in self.evidence[:MAX_EVIDENCE]],
            "evidence_count": len(self.evidence),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class IncludeFinding:
    where: str
    target: str
    kind: str  # "missing", "templated" or "cycle"


@dataclass
class Deck:
    path: Path
    records: list[Record]
    includes: list[IncludeFinding]

    def find(self, *keywords: str) -> list[Record]:
        return [r for r in self.records if r.keyword in keywords]

    def missing_like(self, hint: str) -> list[IncludeFinding]:
        """Missing or templated include files whose name matches the hint."""
        return [i for i in self.includes if i.kind != "cycle" and re.search(hint, Path(i.target).name, re.I)]

    @property
    def dims(self) -> tuple[int, int, int] | None:
        for record in self.find("DIMENS"):
            try:
                return tuple(int(v) for v in record.values[:3])
            except ValueError:
                return None
        return None


def read_deck(path: Path) -> Deck:
    found: list[dict] = []
    rows = [
        Record(keyword or "", tuple(values), source, line, order)
        for order, (keyword, values, source, line) in enumerate(records(deck_lines(path, found)))
    ]
    includes = []
    for item in found:
        target = item["message"].split(": ", 1)[-1]
        if item["code"] == "I002":
            kind = "cycle"
        elif "<" in target or "$" in target or "{{" in target:
            kind = "templated"
        else:
            kind = "missing"
        includes.append(IncludeFinding(item["where"], target, kind))
    return Deck(path, rows, includes)


@dataclass(frozen=True)
class ErtParam:
    name: str
    group: str
    file: str
    line: int
    updated: bool

    @property
    def label(self) -> str:
        return f"{self.group}:{self.name}"


@dataclass
class Ert:
    path: Path
    params: list[ErtParam]
    defines: dict[str, str]
    problems: list[str]

    def matching(self, pattern: str) -> tuple[list[ErtParam], list[ErtParam]]:
        """(uncertain, fixed) parameters whose name or group matches."""
        hits = [
            p for p in self.params if re.search(pattern, p.name, re.I) or re.search(pattern, p.group, re.I)
        ]
        return [p for p in hits if p.updated], [p for p in hits if not p.updated]

    def fixed_defines(self, pattern: str) -> list[str]:
        """DEFINE <NAME> <number> lines that fix a value the deck template would otherwise draw."""
        out = []
        for key, value in self.defines.items():
            if re.search(pattern, key.strip("<>"), re.I) and is_number(value):
                out.append(f"DEFINE {key} {value}")
        return out

    def resolve(self, name: str) -> ErtParam | str | None:
        """Follow DEFINE aliases from a deck placeholder to a parameter, a constant, or nothing."""
        seen = set()
        current = name
        while current not in seen:
            seen.add(current)
            for param in self.params:
                if param.name.upper() == current.upper():
                    return param
            value = self.defines.get(f"<{current}>")
            if value is None:
                return None
            match = re.fullmatch(r"<([A-Za-z0-9_]+)>", value.strip())
            if not match:
                return value.strip()
            current = match[1]
        return None


def read_ert(path: Path) -> Ert:
    mapper_module = _load(SKILLS / "ert-config-author-lint/scripts/ert_config_map.py")
    prior_qc = _load(SKILLS / "history-match-qc/scripts/prior_qc.py")
    mapper = mapper_module.Mapper(path)
    mapper.parse(mapper.root)
    problems = [f"{Path(p['file']).name}:{p['line']}: {p['message']}" for p in mapper.problems]
    params: list[ErtParam] = []
    for entry in mapper.parameters:
        options = entry["options"]
        updated = not any(o.upper() == "UPDATE:FALSE" for o in options)
        if entry["keyword"] == "FIELD":
            params.append(ErtParam(entry["name"], "FIELD", entry["file"], entry["line"], updated))
            continue
        if entry["keyword"] != "GEN_KW":
            continue
        positional = [o for o in options if ":" not in o and "=" not in o]
        if not positional:
            problems.append(
                f"{Path(entry['file']).name}:{entry['line']}: GEN_KW {entry['name']} has no dist file"
            )
            continue
        dist = Path(positional[-1])
        if not dist.is_absolute():
            dist = Path(entry["file"]).parent / dist
        if not dist.is_file():
            problems.append(f"{Path(entry['file']).name}:{entry['line']}: GEN_KW dist file not found: {dist}")
            continue
        found, errors = prior_qc.parse_dist(dist)
        problems += [f"{Path(e['file']).name}:{e['line']}: {e['message']}" for e in errors]
        for param in found:
            moving = updated and param["distribution"] != "CONST"
            params.append(ErtParam(param["name"], entry["name"], param["file"], param["line"], moving))
    return Ert(mapper.root, params, dict(mapper.defines), problems)


def is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def numbers(values: tuple[str, ...]) -> list[float] | None:
    try:
        return [float(v) for v in values]
    except ValueError:
        return None


def names(params: list[ErtParam]) -> str:
    return ", ".join(p.label for p in params)


@dataclass
class Uncertainty:
    """Whether a deck value is uncertain in the AHM, with the evidence for the answer."""

    uncertain: bool | None
    notes: list[str]
    params: list[str] = field(default_factory=list)
    fixed_by: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        if self.uncertain is None:
            return "no --ert-config"
        if self.uncertain:
            shown = ", ".join(self.params[:3]) + (" ..." if len(self.params) > 3 else "")
            return f"uncertain ({shown or 'template'})"
        return f"fixed ({self.fixed_by[0]})" if self.fixed_by else "no ERT parameter"


def uncertainty(found: list[Record], ert: Ert | None, pattern: str) -> Uncertainty:
    notes: list[str] = []
    marks = sorted({name for record in found for name in record.placeholders()})
    template_uncertain = False
    for name in marks:
        target = ert.resolve(name) if ert else None
        if isinstance(target, ErtParam):
            moving = "updated" if target.updated else "not updated"
            notes.append(
                f"placeholder <{name}> is {'GEN_KW' if target.group != 'FIELD' else 'FIELD'} {target.label} ({moving})"
            )
            template_uncertain |= target.updated
        elif isinstance(target, str):
            notes.append(f"placeholder <{name}> is fixed at {target} by a DEFINE")
        else:
            notes.append(f"placeholder <{name}>: template value")
            template_uncertain |= ert is None
    if ert is None:
        return Uncertainty(True if marks else None, notes, [f"<{name}>" for name in marks])
    moving, fixed = ert.matching(pattern)
    if moving:
        notes.append(f"ERT parameters: {names(moving)}")
    if fixed:
        notes.append(f"ERT parameters that do not update (UPDATE:FALSE or CONST): {names(fixed)}")
    defines = ert.fixed_defines(pattern)
    notes += [f"{line} fixes the value" for line in defines]
    labels = [*(f"<{name}>" for name in marks), *(p.label for p in moving)]
    fixed_by = [*defines, *(p.label for p in fixed)]
    return Uncertainty(template_uncertain or bool(moving), notes, labels, fixed_by)


def decide(
    item: ChecklistItem,
    deck: Deck,
    found: bool,
    hint: str,
    unc: Uncertainty | None,
    problem: str | None = None,
) -> ChecklistItem:
    """Set the status from what the readable deck shows, the missing include files, and the ERT config."""
    if unc is not None:
        item.uncertain = unc.uncertain
        item.notes += unc.notes
    hidden = deck.missing_like(hint)
    if hidden:
        item.notes.append("include files not read: " + ", ".join(i.target for i in hidden))
    if problem:
        item.status = Status.MISSING
        item.notes.insert(0, problem)
    elif found:
        item.status = Status.FIXED if unc is not None and unc.uncertain is False else Status.PRESENT
    elif unc is not None and unc.uncertain and (hidden or any(i.kind == "missing" for i in deck.includes)):
        item.status = Status.PRESENT
        item.notes.insert(0, "not in the readable deck. A forward model probably writes it")
    elif hidden:
        item.status = (
            Status.FIXED if unc is not None and unc.uncertain is False and unc.fixed_by else Status.UNVERIFIED
        )
    else:
        item.status = Status.MISSING
    return item


AQUIFERS = ("AQUFETP", "AQUCT", "AQUFLUX", "AQUFET", "AQUCHWAT", "AQUCHGAS")


def check_aquifer(deck: Deck, ert: Ert | None) -> ChecklistItem:
    analytic, numerical = deck.find(*AQUIFERS), deck.find("AQUNUM")
    ancon, con = deck.find("AQUANCON"), deck.find("AQUCON")
    item = ChecklistItem(
        "PHYS-AQUIFER",
        "Aquifer support",
        Status.MISSING,
        "Without aquifer support the update fakes pressure support and water arrival through perm, contacts and porosity.",
        "",
        evidence=[Evidence.of(r) for r in [*analytic, *numerical, *ancon, *con]],
    )
    problem = None
    connected_analytic = analytic and (ancon or all(r.keyword == "AQUFET" for r in analytic))
    found = bool(connected_analytic or (numerical and con))
    if analytic and not connected_analytic:
        problem = f"{analytic[0].keyword} has no AQUANCON. The aquifer is not connected to the grid"
    elif numerical and not con:
        problem = "AQUNUM has no AQUCON. The aquifer is not connected to the grid"
    elif (ancon or con) and not found:
        item.notes.append("an aquifer connection is in the deck, but no aquifer keyword")
    if deck.missing_like("aqu") and problem and "AQUANCON" in problem:
        problem = None  # the connection or the aquifer can be in the unread file
    kinds = sorted({r.keyword for r in [*analytic, *numerical]})
    unc = uncertainty([*analytic, *numerical], ert, r"^AQ|AQUIFER")
    decide(item, deck, found, r"aqu", unc, problem)
    item.summary = (
        f"{'+'.join(kinds) or 'no aquifer keyword'}, {len(ancon) + len(con)} connection records; {unc.text}"
    )
    return item


def segment_base(name: str) -> str:
    """F1N -> F1, F1_S -> F1, FAULT_A_SEG2 -> FAULT_A. A name without a segment suffix stays."""
    return re.sub(r"(_?SEG\d+|_?[NSEW]{1,2}|_\d+)$", "", name, flags=re.I) or name


def check_faults(deck: Deck, ert: Ert | None) -> ChecklistItem:
    faults = deck.find("FAULTS")
    multflt = deck.find("MULTFLT")
    item = ChecklistItem(
        "PHYS-FAULT-SEG",
        "Fault multiplier per segment",
        Status.MISSING,
        "One multiplier per fault forces one seal value on segments that differ.",
        "",
        evidence=[Evidence.of(r) for r in multflt],
    )
    segments = list(dict.fromkeys(r.values[0] for r in faults if r.values))
    groups: dict[str, list[str]] = defaultdict(list)
    for name in segments:
        groups[segment_base(name).upper()].append(name)
    split = {base: members for base, members in groups.items() if len(members) > 1}
    if not segments and not multflt and not deck.missing_like(r"flt|fault|grid"):
        item.status = Status.NOT_APPLICABLE
        item.summary = "no FAULTS in the deck"
        return item

    problems: list[str] = []
    value_of: dict[str, list[str]] = defaultdict(list)
    for record in multflt:
        if len(record.values) < 2:
            continue
        pattern, value = record.values[0], record.values[1]
        covered = [s for s in segments if fnmatch.fnmatchcase(s.upper(), pattern.upper())]
        if not covered and pattern.upper() in groups:
            covered = groups[pattern.upper()]  # the fault base name, not a FAULTS name
        for members in split.values():
            inside = [m for m in members if m in covered]
            if len(inside) > 1:
                problems.append(
                    f"one MULTFLT '{pattern}' ({record.file}:{record.line}) covers the segments {', '.join(inside)}"
                )
        for name in covered:
            value_of[name].append(value)
    unc = uncertainty(multflt, ert, r"MULTFLT|FAULT|^FLT")
    for members in split.values():
        values = [value_of[m][-1] for m in members if value_of.get(m)]
        if len(values) == len(members) and len(set(values)) == 1:
            if PLACEHOLDER.fullmatch(values[0]) and ert:
                problems.append(f"the segments {', '.join(members)} use the same placeholder {values[0]}")
            else:
                item.notes.append(
                    f"the segments {', '.join(members)} have the same value {values[0]}. One parameter can drive both"
                )
        if ert and all(PLACEHOLDER.fullmatch(v) for v in values) and len(values) == len(members):
            targets = {ert.resolve(m.strip("<>{} ")) for m in values}
            params = {t.label for t in targets if isinstance(t, ErtParam)}
            if len(params) == 1 and len(members) > 1:
                problems.append(f"the segments {', '.join(members)} resolve to one parameter {params.pop()}")
    unset = [s for s in segments if s not in value_of]
    if multflt and unset:
        item.notes.append(f"faults with no MULTFLT: {', '.join(unset)}")
    if ert:
        moving, _ = ert.matching(r"MULTFLT|FAULT|^FLT")
        if moving and len(moving) < len(segments):
            problems.append(f"{len(moving)} fault parameter(s) in ERT for {len(segments)} fault segments")
    decide(item, deck, bool(multflt), r"flt|fault", unc, "; ".join(problems) or None)
    if faults and not multflt and item.status is Status.MISSING and not problems:
        item.notes.insert(0, "FAULTS has no MULTFLT")
    item.summary = f"{len(segments)} FAULTS names ({len(split)} split faults), {len(multflt)} MULTFLT records; {unc.text}"
    return item


def layer_minimum(values: list[float], dims: tuple[int, int, int] | None) -> list[tuple[int, float]]:
    if not dims or len(values) != dims[0] * dims[1] * dims[2]:
        return []
    size = dims[0] * dims[1]
    return [(k + 1, min(values[k * size : (k + 1) * size])) for k in range(dims[2])]


def check_kvkh(deck: Deck, ert: Ert | None) -> ChecklistItem:
    arrays = {key: deck.find(key) for key in ("PERMX", "PERMZ", "MULTZ")}
    operations = deck.find("COPY", "MULTIPLY", "EQUALS", "ADD", "OPERATE", "MULTREGT")

    def on(target: str) -> list[Record]:
        return [
            r
            for r in operations
            if r.keyword != "MULTREGT"
            and len(r.values) > 1
            and target in (r.values[0].upper(), r.values[1].upper() if r.keyword == "COPY" else "")
        ]

    permz = [*arrays["PERMZ"], *on("PERMZ")]
    multz_ops = [r for r in on("MULTZ") if r.keyword != "COPY" or r.values[1].upper() == "MULTZ"]
    multregt = [r for r in operations if r.keyword == "MULTREGT"]
    item = ChecklistItem(
        "PHYS-KVKH",
        "kv/kh and shale barriers",
        Status.MISSING,
        "kv/kh and shale barriers control coning and so GOR and water cut.",
        "",
        evidence=[Evidence.of(r) for r in [*permz, *arrays["MULTZ"], *multz_ops, *multregt]],
    )
    dims = deck.dims
    barriers: list[str] = []
    for record in arrays["MULTZ"]:
        values = numbers(record.values)
        if values is None:
            barriers.append(f"MULTZ with placeholders ({record.file}:{record.line})")
        elif min(values) < 1:
            layers = [k for k, low in layer_minimum(values, dims) if low < 1]
            where = f" in layer {', '.join(map(str, layers))}" if layers else ""
            barriers.append(f"MULTZ down to {min(values):g}{where}")
        else:
            item.notes.append(f"MULTZ ({record.file}:{record.line}) is 1 in all cells")
    barriers += [f"{r.keyword} {' '.join(r.values[:3])} ({r.file}:{r.line})" for r in multz_ops]
    barriers += [f"MULTREGT {' '.join(r.values[:3])}" for r in multregt]
    if arrays["PERMZ"]:
        values = numbers(arrays["PERMZ"][-1].values)
        if values:
            tight = [k for k, low in layer_minimum(values, dims) if low <= 0.01 and max(values) > 1]
            barriers += [f"PERMZ near zero in layer {k}" for k in tight]
            if arrays["PERMX"]:
                horizontal = numbers(arrays["PERMX"][-1].values)
                if horizontal and len(horizontal) == len(values):
                    ratios = [z / x for z, x in zip(values, horizontal, strict=True) if x > 0]
                    if ratios:
                        item.notes.append(f"PERMZ/PERMX from {min(ratios):.3g} to {max(ratios):.3g}")
    item.notes.append(
        "barriers: " + "; ".join(barriers)
        if barriers
        else "no barrier found (MULTZ < 1, MULTREGT, or a tight PERMZ layer)"
    )
    unc = uncertainty([*permz, *arrays["MULTZ"], *multz_ops], ert, r"KVKH|KV_KH|PERMZ|MULTZ|BARRIER|SHALE")
    decide(item, deck, bool(permz), r"perm|multz|kvkh", unc)
    item.summary = f"{len(permz)} PERMZ records, {len(barriers)} barrier(s); {unc.text}"
    return item


def wells_in(records_: list[Record]) -> list[str]:
    return list(dict.fromkeys(r.values[0] for r in records_ if r.values))


def check_well_pi(deck: Deck, ert: Ert | None) -> ChecklistItem:
    wpimult = deck.find("WPIMULT")
    compdat = [r for r in deck.find("COMPDAT") if r.placeholders()]
    item = ChecklistItem(
        "PHYS-WELL-PI",
        "Well PI multiplier",
        Status.MISSING,
        "Without it BHP misfit is pushed into reservoir permeability.",
        "",
        evidence=[Evidence.of(r) for r in [*wpimult, *compdat]],
    )
    producers = wells_in(deck.find("WCONHIST", "WCONPROD"))
    if wpimult:
        patterns = [r.values[0] for r in wpimult if r.values]
        bare = [w for w in producers if not any(fnmatch.fnmatchcase(w, p) for p in patterns)]
        if bare:
            item.notes.append(f"producers with no WPIMULT: {', '.join(bare)}")
    unc = uncertainty([*wpimult, *compdat], ert, r"^PI_|^PI$|WPI|PIMULT|PI_MULT")
    decide(item, deck, bool(wpimult or compdat), r"wpi|pimult", unc)
    item.summary = f"{len(wpimult)} WPIMULT records, {len(compdat)} templated COMPDAT records; {unc.text}"
    return item


def check_history_mode(deck: Deck) -> ChecklistItem:
    hist, injh = deck.find("WCONHIST"), deck.find("WCONINJH")
    prod, inje = deck.find("WCONPROD"), deck.find("WCONINJE")
    item = ChecklistItem(
        "PHYS-WCONHIST",
        "History-mode well controls",
        Status.MISSING,
        "Real AHM practice. Rate control mixes up the well constraints with the reservoir response.",
        "",
        evidence=[Evidence.of(r) for r in [*hist[:2], *injh[:2], *prod[:2], *inje[:2]]],
    )
    problem = None
    if hist or injh:
        end = max(r.order for r in [*hist, *injh])
        history_wells = set(wells_in(hist))
        early = [r for r in prod if r.order < end and r.values and r.values[0] not in history_wells]
        if early:
            problem = f"WCONPROD in the history period for {', '.join(wells_in(early))}"
        if [r for r in inje if r.order < end] and not injh:
            item.notes.append(
                "injectors are on WCONINJE in the history period. WCONINJH also reports the history vectors"
            )
        late = [r for r in prod if r.order > end]
        if late:
            item.notes.append(
                f"WCONPROD after the last history keyword (forecast): {', '.join(wells_in(late))}"
            )
    elif prod:
        problem = f"producers on WCONPROD rate control, no WCONHIST: {', '.join(wells_in(prod))}"
    decide(item, deck, bool(hist), r"sch|hist|wcon", None, problem)
    item.summary = (
        f"WCONHIST {len(hist)}, WCONINJH {len(injh)}, WCONPROD {len(prod)}, WCONINJE {len(inje)} records"
    )
    return item


def check_rock(deck: Deck, ert: Ert | None) -> ChecklistItem:
    rock = deck.find("ROCK")
    item = ChecklistItem(
        "PHYS-ROCK",
        "Rock compressibility",
        Status.MISSING,
        "Rock compressibility is often not resolvable against the aquifer size. Information only.",
        "",
        evidence=[Evidence.of(r) for r in rock],
        info_only=True,
    )
    unc = uncertainty(rock, ert, r"ROCK|CMULT|COMPR")
    decide(item, deck, bool(rock), r"rock", unc)
    item.summary = f"{len(rock)} ROCK records; {unc.text}"
    return item


def checklist(deck: Deck, ert: Ert | None) -> list[ChecklistItem]:
    return [
        check_aquifer(deck, ert),
        check_faults(deck, ert),
        check_kvkh(deck, ert),
        check_well_pi(deck, ert),
        check_history_mode(deck),
        check_rock(deck, ert),
    ]


def report(deck: Deck, ert: Ert | None, items: list[ChecklistItem]) -> dict:
    warnings = [i.code for i in items if i.severity == "warning"]
    if deck.includes:
        warnings.append("PHYS-INCLUDE")
    return {
        "schema_version": SCHEMA_VERSION,
        "deck": str(deck.path),
        "ert_config": str(ert.path) if ert else None,
        "items": [i.to_dict() for i in items],
        "include_findings": [
            {"code": "PHYS-INCLUDE", "severity": "warning", **asdict(i)} for i in deck.includes
        ],
        "ert_problems": ert.problems if ert else [],
        "warnings": warnings,
    }


def print_table(result: dict) -> None:
    print(f"AHM physics checklist: {result['deck']}")
    if result["ert_config"]:
        print(f"ERT config: {result['ert_config']}")
    print(f"{'CODE':<15} {'STATUS':<20} SUMMARY")
    for item in result["items"]:
        print(f"{item['code']:<15} {item['status']:<20} {item['summary']}")
    for item in result["items"]:
        if item["severity"] == "ok":
            continue
        print(f"\n{item['severity'].upper()} {item['code']} {item['status']}: {item['reason']}")
        for note in item["notes"]:
            print(f"  - {note}")
        for ev in item["evidence"][:3]:
            print(f"  {ev['file']}:{ev['line']} {ev['keyword']} {ev['text']}")
    if result["include_findings"]:
        print(f"\nWARNING PHYS-INCLUDE: {len(result['include_findings'])} include file(s) not read")
        for found in result["include_findings"]:
            print(f"  {found['where']} {found['kind']}: {found['target']}")
    for problem in result["ert_problems"]:
        print(f"ERT: {problem}")
    print(f"\n{len(result['warnings'])} warning(s): {' '.join(result['warnings']) or 'none'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--deck", type=Path, required=True, help="the DATA file")
    parser.add_argument("--ert-config", type=Path, help="the .ert file, to find the uncertain parameters")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.ert_config and not args.ert_config.is_file():
        print(f"ERROR: not a file: {args.ert_config}", file=sys.stderr)
        return 2
    try:
        deck = read_deck(args.deck)
    except OSError as error:
        print(f"ERROR: cannot read the deck: {error}", file=sys.stderr)
        return 1
    ert = read_ert(args.ert_config) if args.ert_config else None
    result = report(deck, ert, checklist(deck, ert))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_table(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
