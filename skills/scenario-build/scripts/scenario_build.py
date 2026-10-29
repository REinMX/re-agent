#!/usr/bin/env python3
"""Render a typed scenario spec into a SCHEDULE include, check it, and declare what it changes.

  scenario_build.py --spec scenario.yml --out <scenarios>/<id> --contract site.contract.yml --profile <profile>
  scenario_build.py --spec scenario.yml --out <scenarios>/<id> --target ECLIPSE100
  scenario_build.py --spec scenario.yml --out <runpath>/<id> --controls <runpath>/scenario.json  # Everest point

The spec names a case folder and a deck, and lists operations: infill, convert_to_injector, wag, workover,
target, gas_lift, back_pressure, shift. Each operation renders ECLIPSE100 keyword text at its date. The build
keeps the base SCHEDULE text without its comments and splits a timing keyword only where an operation date falls
inside it.

Writes to --out: a copy of the case folder (simulator results left out) whose DATA file includes
<DECK>_SCHEDULE.inc, runmeta.yml (changes, changed_keywords, touched_wells, expected_changes for scenario-qc),
deck_fingerprint.json, and scenario_build.json (insert dates, timing splits, declaration check, deck_support).

Does not run a simulator. Exit: 0 built, 1 built but a check failed (deck_support target FAIL, or the deck
changed a keyword that the operations do not declare), 2 bad spec or input.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, ClassVar

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
for folder in ("stage-contract", "scenario-qc", "flow-preflight"):
    path = str(SKILLS / folder / "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
import contract_lib  # noqa: E402
import deck_fingerprint as dfp  # noqa: E402
import deck_support as ds  # noqa: E402

SCHEMA_VERSION = 1
FIELD_VECTORS = ("FOPT", "FOPR", "FWPT", "FWPR", "FGPT", "FGPR", "FWIT", "FWIR", "FGIT", "FGIR", "FPR")
RESULT_SUFFIXES = frozenset(
    {".UNRST", ".UNSMRY", ".SMSPEC", ".EGRID", ".INIT", ".PRT", ".DBG", ".ESMRY", ".RFT", ".OK", ".INFOSTEP",
     ".RSM", ".LOG", ".MSG", ".ECLEND", ".GRID", ".FEGRID", ".FINIT", ".FUNRST", ".FSMSPEC", ".FUNSMRY"}
)  # fmt: skip
RESULT_RE = re.compile(r"\.[XSF]\d{4}$", re.I)
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
MONTHS["JLY"] = 7
NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,8}$")


class SpecError(ValueError):
    pass


# ---- spec fields ------------------------------------------------------------------------------------------
class Fields:
    """Typed reads from one spec mapping. Unknown keys are an error."""

    def __init__(self, data: Any, where: str, allowed: set[str]):
        if not isinstance(data, dict):
            raise SpecError(f"{where}: expected a mapping")
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise SpecError(f"{where}: unknown key(s) {unknown}; allowed {sorted(allowed)}")
        self.data, self.where = data, where

    def _get(self, key: str, required: bool) -> Any:
        if key not in self.data or self.data[key] is None:
            if required:
                raise SpecError(f"{self.where}: '{key}' is required")
            return None
        return self.data[key]

    def name(self, key: str, required: bool = True) -> str | None:
        value = self._get(key, required)
        if value is None:
            return None
        if not NAME_RE.match(str(value)):
            raise SpecError(f"{self.where}: {key} {value!r} must be 1-8 characters (letters, digits, _ - .)")
        return str(value).upper()

    def number(self, key: str, required: bool = True, minimum: float | None = 0.0) -> float | None:
        value = self._get(key, required)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SpecError(f"{self.where}: {key} must be a number, got {value!r}")
        if minimum is not None and value < minimum:
            raise SpecError(f"{self.where}: {key} must be >= {minimum}, got {value}")
        return float(value)

    def integer(self, key: str, required: bool = True, minimum: int = 1) -> int | None:
        value = self._get(key, required)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise SpecError(f"{self.where}: {key} must be an integer >= {minimum}, got {value!r}")
        return value

    def choice(
        self, key: str, options: tuple[str, ...], required: bool = True, default: str | None = None
    ) -> Any:
        value = self._get(key, required and default is None)
        if value is None:
            return default
        value = str(value).upper()
        if value not in options:
            raise SpecError(f"{self.where}: {key} {value!r} is not one of {list(options)}")
        return value

    def day(self, key: str, required: bool = True) -> date | None:
        value = self._get(key, required)
        if value is None:
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError as error:
            raise SpecError(f"{self.where}: {key} must be YYYY-MM-DD, got {value!r}") from error

    def items(self, key: str, required: bool = True) -> list[Any]:
        value = self._get(key, required)
        if value is None:
            return []
        if not isinstance(value, list) or (required and not value):
            raise SpecError(f"{self.where}: {key} must be a non-empty list")
        return value

    def mapping(self, key: str, required: bool = True) -> dict | None:
        value = self._get(key, required)
        if value is not None and not isinstance(value, dict):
            raise SpecError(f"{self.where}: {key} must be a mapping")
        return value

    def names(self, key: str) -> list[str]:
        return [
            Fields({"n": v}, f"{self.where}.{key}", {"n"}).name("n") for v in self.items(key, required=False)
        ]


# ---- ECLIPSE text ------------------------------------------------------------------------------------------
def item(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return f"'{value}'"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def record(values: list[Any]) -> str:
    """One record: None is a default; trailing defaults are dropped; repeated defaults are written n*."""
    tokens = [item(v) for v in values]
    while tokens and tokens[-1] is None:
        tokens.pop()
    out: list[str] = []
    run = 0
    for token in tokens + ["<end>"]:
        if token is None:
            run += 1
            continue
        if run:
            out.append(f"{run}*" if run > 1 else "1*")
            run = 0
        if token != "<end>":
            out.append(token)
    return " " + " ".join(out) + " /"


@dataclass(frozen=True)
class Block:
    keyword: str
    records: tuple[str, ...]
    terminated: bool = True  # list keywords end with a lone slash; single-record keywords do not

    def text(self) -> str:
        return "\n".join([self.keyword, *self.records, *(["/"] if self.terminated else [])]) + "\n"


@dataclass
class Declaration:
    keywords: set[str] = field(default_factory=set)
    wells: set[str] = field(default_factory=set)
    groups: set[str] = field(default_factory=set)
    new_wells: set[str] = field(default_factory=set)


# ---- operations --------------------------------------------------------------------------------------------
COMMON = {"id", "type", "date", "affected_wells", "note"}


@dataclass
class Operation:
    TYPE: ClassVar[str] = ""
    KEYS: ClassVar[set[str]] = set()
    id: str
    day: date
    affected_wells: list[str]
    note: str | None

    @classmethod
    def parse(cls, raw: dict, index: int) -> Operation:
        f = Fields(raw, f"operations[{index}] ({cls.TYPE})", COMMON | cls.KEYS)
        op = cls.__new__(cls)
        op.id = str(raw.get("id") or f"op{index + 1}")
        if cls.TYPE in {"shift", "wag"} and raw.get("date") is not None:
            raise SpecError(
                f"{f.where}: {cls.TYPE} takes no 'date'" + (" (use 'start')" if cls.TYPE == "wag" else "")
            )
        op.day = f.day("date", required=cls.TYPE not in {"shift", "wag"})
        op.affected_wells = f.names("affected_wells")
        op.note = raw.get("note")
        op.read(f)
        return op

    def read(self, f: Fields) -> None:
        raise NotImplementedError

    def events(self) -> list[tuple[date, Block]]:
        raise NotImplementedError

    def declare(self) -> Declaration:
        blocks = [b for _, b in self.events()]
        return Declaration(keywords={b.keyword for b in blocks}, wells=set(self.affected_wells))


PROD_MODES = ("ORAT", "WRAT", "GRAT", "LRAT", "RESV", "BHP")
RATE_ITEM = {"ORAT": 0, "WRAT": 1, "GRAT": 2, "LRAT": 3, "RESV": 4}


def producer_record(
    well: str, status: str, mode: str, rates: dict[str, float | None], bhp: float | None
) -> str:
    values: list[Any] = [None] * 5
    for key, index in RATE_ITEM.items():
        values[index] = rates.get(key)
    return record([well, status, mode, *values, bhp])


def read_rates(f: Fields, mode: str) -> dict[str, float | None]:
    rates = {key: f.number(key.lower(), required=False) for key in RATE_ITEM}
    if mode in RATE_ITEM and rates[mode] is None:
        raise SpecError(f"{f.where}: mode {mode} needs '{mode.lower()}'")
    return rates


class Infill(Operation):
    TYPE = "infill"
    KEYS = {"well", "group", "i", "j", "ref_depth", "phase", "connections", "role", "control"}

    def read(self, f: Fields) -> None:
        self.well, self.group = f.name("well"), f.name("group")
        self.i, self.j = f.integer("i"), f.integer("j")
        self.ref_depth = f.number("ref_depth", required=False)
        self.phase = f.choice("phase", ("OIL", "WATER", "GAS", "LIQ"))
        self.role = f.choice("role", ("PRODUCER", "INJECTOR"), default="PRODUCER")
        self.connections = []
        for n, raw in enumerate(f.items("connections")):
            c = Fields(raw, f"{f.where}.connections[{n}]", {"i", "j", "k1", "k2", "diameter", "skin"})
            k1, k2 = c.integer("k1"), c.integer("k2")
            if k2 < k1:
                raise SpecError(f"{c.where}: k2 < k1")
            ci, cj = c.integer("i", required=False) or self.i, c.integer("j", required=False) or self.j
            self.connections.append(
                (ci, cj, k1, k2, c.number("diameter", required=False), c.number("skin", False, None))
            )
        ctl = Fields(
            f.mapping("control"),
            f"{f.where}.control",
            {"mode", "phase", *map(str.lower, RATE_ITEM), "rate", "bhp"},
        )
        if self.role == "PRODUCER":
            self.mode = ctl.choice("mode", PROD_MODES)
            self.rates = read_rates(ctl, self.mode)
        else:
            self.inj_phase = ctl.choice("phase", ("WATER", "GAS"))
            self.mode = ctl.choice("mode", ("RATE", "BHP"))
            self.rate = ctl.number("rate", required=self.mode == "RATE")
        self.bhp = ctl.number("bhp")

    def events(self) -> list[tuple[date, Block]]:
        welspecs = Block(
            "WELSPECS", (record([self.well, self.group, self.i, self.j, self.ref_depth, self.phase]),)
        )
        compdat = Block(
            "COMPDAT",
            tuple(
                record([self.well, i, j, k1, k2, "OPEN", None, None, d, None, s])
                for i, j, k1, k2, d, s in self.connections
            ),
        )
        if self.role == "PRODUCER":
            control = Block(
                "WCONPROD", (producer_record(self.well, "OPEN", self.mode, self.rates, self.bhp),)
            )
        else:
            control = Block(
                "WCONINJE",
                (record([self.well, self.inj_phase, "OPEN", self.mode, self.rate, None, self.bhp]),),
            )
        return [(self.day, welspecs), (self.day, compdat), (self.day, control)]

    def declare(self) -> Declaration:
        d = super().declare()
        d.wells.add(self.well)
        d.new_wells.add(self.well)
        d.groups.add(self.group)
        return d


class ConvertToInjector(Operation):
    TYPE = "convert_to_injector"
    KEYS = {"well", "phase", "mode", "rate", "bhp"}

    def read(self, f: Fields) -> None:
        self.well = f.name("well")
        self.phase = f.choice("phase", ("WATER", "GAS"))
        self.mode = f.choice("mode", ("RATE", "BHP"), default="RATE")
        self.rate = f.number("rate", required=self.mode == "RATE")
        self.bhp = f.number("bhp")

    def events(self) -> list[tuple[date, Block]]:
        return [
            (
                self.day,
                Block(
                    "WCONINJE",
                    (record([self.well, self.phase, "OPEN", self.mode, self.rate, None, self.bhp]),),
                ),
            )
        ]

    def declare(self) -> Declaration:
        d = super().declare()
        d.wells.add(self.well)
        return d


class Wag(Operation):
    TYPE = "wag"
    KEYS = {"well", "start", "cycles", "first", "water", "gas", "then"}

    def read(self, f: Fields) -> None:
        self.well = f.name("well")
        self.day = f.day("start")
        self.cycles = f.integer("cycles")
        self.first = f.choice("first", ("WATER", "GAS"), default="WATER")
        # After the last leg: inject WATER or GAS at its leg rate, SHUT the well, or CONTINUE the last leg.
        self.then = f.choice("then", ("WATER", "GAS", "SHUT", "CONTINUE"))
        self.legs = {}
        for phase in ("water", "gas"):
            leg = Fields(f.mapping(phase), f"{f.where}.{phase}", {"rate", "bhp", "days"})
            self.legs[phase.upper()] = (leg.number("rate"), leg.number("bhp"), leg.integer("days"))

    def events(self) -> list[tuple[date, Block]]:
        order = [self.first, "GAS" if self.first == "WATER" else "WATER"]
        out, when = [], self.day
        for _ in range(self.cycles):
            for phase in order:
                rate, bhp, days = self.legs[phase]
                out.append(
                    (when, Block("WCONINJE", (record([self.well, phase, "OPEN", "RATE", rate, None, bhp]),)))
                )
                when += timedelta(days=days)
        if self.then == "SHUT":
            out.append((when, Block("WELOPEN", (record([self.well, "SHUT"]),))))
        elif self.then in self.legs:
            rate, bhp, _ = self.legs[self.then]
            out.append(
                (when, Block("WCONINJE", (record([self.well, self.then, "OPEN", "RATE", rate, None, bhp]),)))
            )
        return out

    def declare(self) -> Declaration:
        d = super().declare()
        d.wells.add(self.well)
        return d


class Workover(Operation):
    TYPE = "workover"
    KEYS = {"well", "connections", "well_status"}

    def read(self, f: Fields) -> None:
        self.well = f.name("well")
        self.status = f.choice("well_status", ("OPEN", "SHUT", "STOP"), required=False)
        self.connections = []
        for n, raw in enumerate(f.items("connections", required=False)):
            c = Fields(
                raw,
                f"{f.where}.connections[{n}]",
                {"i", "j", "k1", "k2", "state", "diameter", "skin", "direction"},
            )
            k1, k2 = c.integer("k1"), c.integer("k2")
            if k2 < k1:
                raise SpecError(f"{c.where}: k2 < k1")
            skin = c.number("skin", False, None)
            # COMPDAT redefines the whole connection: a defaulted diameter, factor or direction is not the base one.
            diameter = c.number("diameter", required=skin is not None)
            direction = c.choice("direction", ("X", "Y", "Z"), required=False)
            self.connections.append(
                (
                    c.integer("i"),
                    c.integer("j"),
                    k1,
                    k2,
                    c.choice("state", ("OPEN", "SHUT")),
                    diameter,
                    skin,
                    direction,
                )
            )
        if not self.connections and not self.status:
            raise SpecError(f"{f.where}: give connections, well_status, or both")

    def events(self) -> list[tuple[date, Block]]:
        out = []
        redefine = [c for c in self.connections if c[5] is not None or c[6] is not None or c[7] is not None]
        if redefine:
            recs = tuple(
                record([self.well, i, j, k1, k2, st, None, None, d, None, s, None, dr])
                for i, j, k1, k2, st, d, s, dr in redefine
            )
            out.append((self.day, Block("COMPDAT", recs)))
        # Open or shut only: WELOPEN by connection keeps the base COMPDAT items.
        recs = [
            record([self.well, st, i, j, k])
            for i, j, k1, k2, st, d, s, dr in self.connections
            if (d, s, dr) == (None, None, None)
            for k in range(k1, k2 + 1)
        ]
        if self.status:
            recs.append(record([self.well, self.status]))
        if recs:
            out.append((self.day, Block("WELOPEN", tuple(recs))))
        return out

    def declare(self) -> Declaration:
        d = super().declare()
        d.wells.add(self.well)
        return d


WELTARG_CONTROLS = ("ORAT", "WRAT", "GRAT", "LRAT", "RESV", "BHP", "THP", "VFP", "LIFT", "GUID")


class Target(Operation):
    TYPE = "target"
    KEYS = {"level", "name", "mode", "status", "orat", "wrat", "grat", "lrat", "resv", "bhp", "limits", "phase", "rate",
            "rein", "vrep", "action", "sales_target", "max_rate", "min_rate", "procedure"}  # fmt: skip
    LEVELS = ("WELL", "WELL_LIMIT", "GROUP_PRODUCTION", "GROUP_INJECTION", "SALES")

    def read(self, f: Fields) -> None:
        self.level = f.choice("level", self.LEVELS)
        self.name = f.name("name")
        if self.level == "WELL":
            self.mode = f.choice("mode", PROD_MODES)
            self.status = f.choice("status", ("OPEN", "SHUT", "STOP"), default="OPEN")
            self.rates = read_rates(f, self.mode)
            self.bhp = f.number("bhp")
        elif self.level == "WELL_LIMIT":
            limits = f.mapping("limits")
            if not limits:
                raise SpecError(f"{f.where}: limits must name at least one control")
            lf = Fields(
                limits, f"{f.where}.limits", set(WELTARG_CONTROLS) | {c.lower() for c in WELTARG_CONTROLS}
            )
            self.limits = [(c.upper(), lf.number(c)) for c in limits]
        elif self.level == "GROUP_PRODUCTION":
            self.mode = f.choice("mode", ("NONE", "FLD", "ORAT", "WRAT", "GRAT", "LRAT", "RESV"))
            self.rates = read_rates(f, self.mode)
            self.action = f.choice("action", ("NONE", "CON", "+CON", "WELL", "PLUG", "RATE"), required=False)
        elif self.level == "GROUP_INJECTION":
            self.phase = f.choice("phase", ("WATER", "GAS"))
            self.mode = f.choice("mode", ("NONE", "RATE", "RESV", "REIN", "VREP", "FLD"))
            self.rate, self.resv = (
                f.number("rate", self.mode == "RATE"),
                f.number("resv", self.mode == "RESV"),
            )
            self.rein, self.vrep = (
                f.number("rein", self.mode == "REIN"),
                f.number("vrep", self.mode == "VREP"),
            )
        else:
            self.sales = f.number("sales_target")
            self.max_rate, self.min_rate = f.number("max_rate", False), f.number("min_rate", False)
            self.procedure = f.choice(
                "procedure", ("NONE", "CON", "+CON", "WELL", "PLUG", "RATE", "MAXR", "END"), False
            )

    def events(self) -> list[tuple[date, Block]]:
        if self.level == "WELL":
            block = Block(
                "WCONPROD", (producer_record(self.name, self.status, self.mode, self.rates, self.bhp),)
            )
        elif self.level == "WELL_LIMIT":
            block = Block("WELTARG", tuple(record([self.name, c, v]) for c, v in self.limits))
        elif self.level == "GROUP_PRODUCTION":
            r = self.rates
            values = [self.name, self.mode, r["ORAT"], r["WRAT"], r["GRAT"], r["LRAT"], self.action]
            values += [None] * 6 + [r["RESV"]] if r["RESV"] is not None else []
            block = Block("GCONPROD", (record(values),))
        elif self.level == "GROUP_INJECTION":
            block = Block(
                "GCONINJE",
                (record([self.name, self.phase, self.mode, self.rate, self.resv, self.rein, self.vrep]),),
            )
        else:
            block = Block(
                "GCONSALE", (record([self.name, self.sales, self.max_rate, self.min_rate, self.procedure]),)
            )
        return [(self.day, block)]

    def declare(self) -> Declaration:
        d = super().declare()
        (d.wells if self.level in {"WELL", "WELL_LIMIT"} else d.groups).add(self.name)
        return d


class GasLift(Operation):
    TYPE = "gas_lift"
    KEYS = {"liftopt", "wells", "groups", "alq"}

    def read(self, f: Fields) -> None:
        lo = f.mapping("liftopt", required=False)
        self.liftopt = None
        if lo is not None:
            lf = Fields(
                lo,
                f"{f.where}.liftopt",
                {"increment", "economic_gradient", "min_interval_days", "optimize_newton"},
            )
            self.liftopt = (lf.number("increment"), lf.number("economic_gradient"), lf.number("min_interval_days", False),
                            lf.choice("optimize_newton", ("YES", "NO"), required=False))  # fmt: skip
        self.wells = []
        for n, raw in enumerate(f.items("wells", required=False)):
            w = Fields(raw, f"{f.where}.wells[{n}]",
                       {"well", "optimize", "max_rate", "weight", "min_rate", "gas_weight", "optimize_limit"})  # fmt: skip
            row = [w.name("well"), w.choice("optimize", ("YES", "NO"), default="YES"), w.number("max_rate", False),
                   w.number("weight", False), w.number("min_rate", False, None)]  # fmt: skip
            # ECLIPSE100 items 6 (incremental gas weighting) and 7 (allocate extra lift gas above the group limit).
            # Box Flow supports only their defaults: deck_support flags other values.
            gas_weight = w.number("gas_weight", False)
            limit = w.choice("optimize_limit", ("YES", "NO"), required=False)
            if gas_weight is not None or limit is not None:
                row += [gas_weight, limit]
            self.wells.append(tuple(row))
        self.groups = []
        for n, raw in enumerate(f.items("groups", required=False)):
            g = Fields(raw, f"{f.where}.groups[{n}]", {"group", "max_lift", "max_total_gas"})
            self.groups.append(
                (g.name("group"), g.number("max_lift", False), g.number("max_total_gas", False))
            )
        self.alq = []
        for n, raw in enumerate(f.items("alq", required=False)):
            a = Fields(raw, f"{f.where}.alq[{n}]", {"well", "value"})
            self.alq.append((a.name("well"), a.number("value")))
        if not (self.liftopt or self.wells or self.groups or self.alq):
            raise SpecError(f"{f.where}: give liftopt, wells, groups or alq")

    def events(self) -> list[tuple[date, Block]]:
        out = []
        if self.liftopt:
            out.append((self.day, Block("LIFTOPT", (record(list(self.liftopt)),), terminated=False)))
        if self.wells:
            out.append((self.day, Block("WLIFTOPT", tuple(record(list(w)) for w in self.wells))))
        if self.groups:
            out.append((self.day, Block("GLIFTOPT", tuple(record(list(g)) for g in self.groups))))
        if self.alq:
            out.append((self.day, Block("WELTARG", tuple(record([w, "LIFT", v]) for w, v in self.alq))))
        return out

    def declare(self) -> Declaration:
        d = super().declare()
        d.wells |= {w[0] for w in self.wells} | {w for w, _ in self.alq}
        d.groups |= {g[0] for g in self.groups}
        return d


class BackPressure(Operation):
    TYPE = "back_pressure"
    KEYS = {"nodes", "branches"}

    def read(self, f: Fields) -> None:
        self.nodes = []
        for n, raw in enumerate(f.items("nodes", required=False)):
            nf = Fields(raw, f"{f.where}.nodes[{n}]", {"node", "pressure"})
            self.nodes.append((nf.name("node"), nf.number("pressure")))
        self.branches = []
        for n, raw in enumerate(f.items("branches", required=False)):
            bf = Fields(raw, f"{f.where}.branches[{n}]", {"downtree", "uptree", "vfp", "alq"})
            self.branches.append(
                (
                    bf.name("downtree"),
                    bf.name("uptree"),
                    bf.integer("vfp", minimum=0),
                    bf.number("alq", False),
                )
            )
        if not (self.nodes or self.branches):
            raise SpecError(f"{f.where}: give nodes, branches or both")

    def events(self) -> list[tuple[date, Block]]:
        out = []
        if self.branches:
            out.append((self.day, Block("BRANPROP", tuple(record(list(b)) for b in self.branches))))
        if self.nodes:
            out.append((self.day, Block("NODEPROP", tuple(record([n, p]) for n, p in self.nodes))))
        return out

    def declare(self) -> Declaration:
        d = super().declare()
        d.groups |= {n for n, _ in self.nodes} | {b[0] for b in self.branches}
        return d


class Shift(Operation):
    TYPE = "shift"
    KEYS = {"ops", "days"}

    def read(self, f: Fields) -> None:
        self.targets = [str(v) for v in f.items("ops")]
        self.days = f.integer("days", minimum=-100000)  # 0 is allowed: a date control at its initial value

    def events(self) -> list[tuple[date, Block]]:
        return []


OPERATIONS: dict[str, type[Operation]] = {
    cls.TYPE: cls for cls in (Infill, ConvertToInjector, Wag, Workover, Target, GasLift, BackPressure, Shift)
}


# ---- Everest-ready variables --------------------------------------------------------------------------------
VAR_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
VAR_KEYS = {"initial", "min", "max", "integer", "note"}


@dataclass(frozen=True)
class Variable:
    """A numeric control: a spec value written as ${name} takes this variable's value."""

    name: str
    initial: float
    low: float
    high: float
    integer: bool
    note: str | None = None


def read_variables(raw: Any) -> dict[str, Variable]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SpecError("variables must be a mapping of name -> {initial, min, max}")
    out: dict[str, Variable] = {}
    for name, body in raw.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
            raise SpecError(f"variables: name {name!r} must use letters, digits and _")
        f = Fields(body, f"variables.{name}", VAR_KEYS)
        low, high = f.number("min", minimum=None), f.number("max", minimum=None)
        initial = f.number("initial", minimum=None)
        if not low < high:
            raise SpecError(f"variables.{name}: min must be below max")
        if not low <= initial <= high:
            raise SpecError(f"variables.{name}: initial {fmt(initial)} is outside [{fmt(low)}, {fmt(high)}]")
        integer = body.get("integer", False)
        if not isinstance(integer, bool):
            raise SpecError(f"variables.{name}: integer must be true or false, got {integer!r}")
        out[str(name)] = Variable(str(name), initial, low, high, integer, body.get("note"))
    return out


def fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def read_controls(path: Path | None, variables: dict[str, Variable]) -> tuple[str, dict[str, float]]:
    """Values for the variables: the initial values, or an Everest control file (<control name>.json)."""
    if path is None:
        return "initial", {n: v.initial for n, v in variables.items()}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), dict):
        data = next(iter(data.values()))  # {control name: {variable: value}}
    if not isinstance(data, dict):
        raise SpecError(f"{path.name}: expected a JSON mapping of variable -> value")
    missing = sorted(set(variables) - set(data))
    if missing:
        raise SpecError(f"{path.name} has no value for {missing}")
    extra = sorted(set(data) - set(variables))
    if extra:
        raise SpecError(f"{path.name} has values for unknown variables {extra}")
    values: dict[str, float] = {}
    for name, var in variables.items():
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SpecError(f"{path.name}: {name} must be a number, got {value!r}")
        tolerance = 1e-9 * max(1.0, abs(var.high - var.low))
        if not var.low - tolerance <= value <= var.high + tolerance:
            raise SpecError(
                f"{path.name}: {name} = {fmt(value)} is outside [{fmt(var.low)}, {fmt(var.high)}]"
            )
        values[name] = float(value)
    return str(path), values


def substitute(raw: Any, values: dict[str, float], variables: dict[str, Variable], used: set[str]) -> Any:
    """Replace each ${name} string in the operations with the variable value (rounded for integers)."""
    if isinstance(raw, dict):
        return {k: substitute(v, values, variables, used) for k, v in raw.items()}
    if isinstance(raw, list):
        return [substitute(v, values, variables, used) for v in raw]
    if isinstance(raw, str):
        match = VAR_RE.match(raw.strip())
        if match:
            name = match.group(1)
            if name not in variables:
                raise SpecError(f"unknown variable {name!r}; declare it under variables")
            used.add(name)
            value = values[name]
            return int(round(value)) if variables[name].integer else value
        if "${" in raw:
            raise SpecError(f"{raw!r}: a variable reference must be the whole value, like ${{name}}")
    return raw


@dataclass
class Spec:
    scenario_id: str
    parent: str
    description: str | None
    case_root: Path
    deck: Path
    start: date | None
    field_vectors: list[str]
    operations: list[Operation]
    sha256: str
    variables: dict[str, Variable] = field(default_factory=dict)
    controls_source: str = "initial"
    values: dict[str, float | int] = field(default_factory=dict)


def load_spec(path: Path, controls: Path | None = None) -> Spec:
    text = path.read_text(encoding="utf-8")
    yaml = contract_lib._yaml()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SpecError(
            f'{path.name}: not valid YAML (quote variable references: "${{name}}"): {error}'
        ) from error
    f = Fields(data, path.name, {"schema_version", "scenario_id", "parent", "description", "case_root", "deck", "start",
                                 "field_vectors_may_move", "variables", "operations"})  # fmt: skip
    variables = read_variables(data.get("variables"))
    source, values = read_controls(controls, variables)
    used: set[str] = set()
    raw_ops = substitute(f.items("operations"), values, variables, used)
    unused = sorted(set(variables) - used)
    if unused:
        raise SpecError(f"{path.name}: variables not used by any operation: {unused}")
    if f.integer("schema_version") != SCHEMA_VERSION:
        raise SpecError(f"{path.name}: schema_version must be {SCHEMA_VERSION}")
    sid = str(f._get("scenario_id", True))
    if not re.match(r"^[A-Za-z0-9_.\-]+$", sid):
        raise SpecError(f"{path.name}: scenario_id {sid!r} must use letters, digits, _ . -")
    case_root = (path.parent / str(f._get("case_root", True))).resolve()
    deck = case_root / str(f._get("deck", True))
    if not deck.is_file():
        raise SpecError(f"{path.name}: deck not found: {deck}")
    ops: list[Operation] = []
    for index, raw in enumerate(raw_ops):
        kind = str((raw or {}).get("type", "")).lower() if isinstance(raw, dict) else ""
        if kind not in OPERATIONS:
            raise SpecError(f"operations[{index}]: type {kind!r} is not one of {sorted(OPERATIONS)}")
        ops.append(OPERATIONS[kind].parse(raw, index))
    ids = [op.id for op in ops]
    if len(set(ids)) != len(ids):
        raise SpecError(f"{path.name}: operation ids must be unique: {ids}")
    for op in ops:
        if isinstance(op, Shift):
            for target in op.targets:
                if target not in ids or isinstance(ops[ids.index(target)], Shift):
                    raise SpecError(f"{op.id}: shift names {target!r}, which is not a non-shift operation id")
                ops[ids.index(target)].day += timedelta(days=op.days)
    vectors = [str(v).upper() for v in f.items("field_vectors_may_move", required=False)] or list(
        FIELD_VECTORS
    )
    return Spec(sid, str(data.get("parent") or "baseline"), data.get("description"), case_root, deck,
                f.day("start", required=False), vectors, ops, hashlib.sha256(text.encode()).hexdigest(),
                variables, source, {n: int(round(v)) if variables[n].integer else v for n, v in values.items()})  # fmt: skip


# ---- schedule timeline -------------------------------------------------------------------------------------
@dataclass
class Chunk:
    keyword: str | None  # DATES, TSTEP, or None for any other text
    text: str
    steps: list[date] = field(default_factory=list)  # report dates reached by this chunk
    dates_records: list[str] = field(default_factory=list)


def parse_date(rec: str) -> date:
    tokens = rec.replace("'", " ").replace('"', " ").replace("/", " ").split()
    if len(tokens) < 3 or tokens[1].upper() not in MONTHS:
        raise SpecError(f"cannot read DATES record {rec!r}")
    return date(int(tokens[2]), MONTHS[tokens[1].upper()], int(tokens[0]))


def tstep_values(body: str) -> list[float]:
    values: list[float] = []
    for token in body.replace("/", " ").split():
        if "*" in token:
            count, value = token.split("*", 1)
            values += [float(value)] * int(count)
        else:
            values.append(float(token))
    return values


def chunks_of(schedule: str, start: date) -> list[Chunk]:
    lines = schedule.split("\n")
    chunks: list[Chunk] = []
    other: list[str] = []
    cur = start
    i = 0
    in_action = False
    while i < len(lines):
        bare = lines[i].split("--", 1)[0].strip().upper()
        if in_action:
            other.append(lines[i])
            in_action = bare != "ENDACTIO"
            i += 1
            continue
        if bare in {"DATES", "TSTEP"}:
            if other:
                chunks.append(Chunk(None, "\n".join(other) + "\n"))
                other = []
            j = i + 1
            body: list[str] = []
            while j < len(lines):
                content = lines[j].split("--", 1)[0].strip()
                if dfp.KEYWORD_LINE_RE.match(content.upper()):
                    break
                body.append(lines[j])
                j += 1
                if bare == "TSTEP" and content.endswith("/"):
                    break
                if bare == "DATES" and content == "/":
                    break
            chunk = Chunk(bare, "\n".join(lines[i:j]) + "\n")
            if bare == "DATES":
                for b in body:
                    content = b.split("--", 1)[0].strip()
                    if content and content != "/":
                        chunk.dates_records.append(content)
                        chunk.steps.append(parse_date(content))
            else:
                for days in tstep_values(" ".join(b.split("--", 1)[0] for b in body)):
                    cur_days = timedelta(days=days)
                    if days != int(days):
                        raise SpecError(
                            f"TSTEP {days} is not whole days; this builder places operations on dates"
                        )
                    cur = cur + cur_days
                    chunk.steps.append(cur)
            if chunk.steps:
                cur = chunk.steps[-1]
            chunks.append(chunk)
            i = j
            continue
        if bare == "ACTIONX":
            in_action = True
        other.append(lines[i])
        i += 1
    if other:
        chunks.append(Chunk(None, "\n".join(other) + "\n"))
    return chunks


def eclipse_date(day: date) -> str:
    return f" {day.day} '{list(MONTHS)[day.month - 1]}' {day.year} /"


def timing_text(keyword: str, steps: list[date], previous: date, records: list[str]) -> str:
    if keyword == "DATES":
        return "DATES\n" + "\n".join(f" {r}" for r in records) + "\n/\n"
    days = [(b - a).days for a, b in zip([previous, *steps[:-1]], steps, strict=True)]
    tokens, k = [], 0
    while k < len(days):
        n = 1
        while k + n < len(days) and days[k + n] == days[k]:
            n += 1
        tokens.append(f"{n}*{days[k]}" if n > 1 else str(days[k]))
        k += n
    return "TSTEP\n " + " ".join(tokens) + " /\n"


def merge(
    chunks: list[Chunk], start: date, inserts: list[tuple[date, str]]
) -> tuple[str, set[str], list[str]]:
    """Insert text at dates. Returns (schedule text, timing keywords changed, notes).

    Text for a date goes after the base keywords of that date, before the next report step. A date inside a
    TSTEP or DATES keyword splits it; a date that is not a report date gets its own DATES record.
    """
    pending = sorted(inserts, key=lambda x: x[0])
    if pending and pending[0][0] < start:
        raise SpecError(f"operation date {pending[0][0]} is before the deck START {start}")
    out: list[str] = []
    changed: set[str] = set()
    notes: list[str] = []
    cur = start

    def flush(until: date) -> None:
        while pending and pending[0][0] <= until:
            out.append(pending.pop(0)[1])

    for chunk in chunks:
        if chunk.keyword is None or not chunk.steps:
            out.append(chunk.text)
            continue
        flush(cur)
        steps = chunk.steps
        if not pending or pending[0][0] >= steps[-1]:
            out.append(chunk.text)
            cur = steps[-1]
            continue
        done, prev = 0, cur
        while pending and pending[0][0] < steps[-1]:
            day = pending[0][0]
            k = sum(1 for s in steps if s <= day)
            if k > done:
                out.append(timing_text(chunk.keyword, steps[done:k], prev, chunk.dates_records[done:k]))
                prev, done = steps[k - 1], k
            if prev != day:
                out.append("DATES\n" + eclipse_date(day) + "\n/\n")
                changed.add("DATES")
                notes.append(f"{day} is not a report date: added DATES {day} inside {chunk.keyword}")
                prev = day
            flush(day)
            changed.add(chunk.keyword)
        out.append(timing_text(chunk.keyword, steps[done:], prev, chunk.dates_records[done:]))
        cur = steps[-1]
    if pending:
        raise SpecError(
            f"operation date {pending[0][0]} is on or after the last report date {cur}; it would have no effect"
        )
    return "".join(out), changed, notes


# Keywords whose new record replaces the base one; items the spec does not give go to their defaults.
FULL_RECORD = {
    "WCONPROD": "THP limit, VFP table and ALQ not in the spec go to defaults; use level well_limit (WELTARG) "
    "to change one control",
    "GCONPROD": "guide rates and the water, gas and liquid limit actions not in the spec go to defaults",
    "COMPDAT": "connection factor, Kh and D-factor are recomputed from the grid and the spec diameter",
    "NODEPROP": "auto-choke and add-lift-gas flags not in the spec go to NO",
    "BRANPROP": "ALQ surface density not in the spec goes to its default",
}


# ---- the build ---------------------------------------------------------------------------------------------
def deck_text(deck: Path) -> str:
    raw = dfp.strip_comments(deck.read_text(errors="replace"))
    return dfp.expand_includes(raw, deck.parent, deck, [], set())


def summary_gaps(text: str, new_wells: set[str]) -> list[str]:
    """Notes for new wells that a SUMMARY well list leaves out: those wells get no W vectors."""
    section = re.search(r"^\s*SUMMARY\s*$(.*?)^\s*SCHEDULE\s*$", text, re.M | re.S | re.I)
    if not section or not new_wells:
        return []
    tokens = section.group(1).split()
    missing: dict[str, list[str]] = {}
    for n, token in enumerate(tokens):
        if not re.fullmatch(r"W[A-Z0-9]{1,7}", token.upper()):
            continue
        names = []
        for t in tokens[n + 1 :]:
            if t == "/" or t.endswith("/"):
                if t != "/":
                    names.append(t.rstrip("/"))
                break
            names.append(t)
        names = [x.strip("'\"").upper() for x in names if x.strip("'\"")]
        if not names:
            continue  # no list: every well
        for well in sorted(new_wells):
            if not any(fnmatch.fnmatchcase(well.upper(), pat) for pat in names):
                missing.setdefault(well, []).append(token.upper())
    return [
        f"SUMMARY lists leave out new well {w}: no {', '.join(k)} for it. Add it to those lists to see its vectors."
        for w, k in sorted(missing.items())
    ]


def deck_start(deck: Path, override: date | None) -> date:
    if override:
        return override
    text = deck_text(deck)
    match = re.search(r"^\s*START\s*\n\s*(\d+\s+'?[A-Za-z]{3}'?\s+\d{4})", text, re.M | re.I)
    if not match:
        raise SpecError("START not found in the deck; give 'start' in the spec")
    return parse_date(match.group(1))


def split_schedule(deck: Path) -> tuple[str, str, str]:
    """(DATA text before the schedule body, schedule body with includes expanded, DATA text from END)."""
    raw = deck.read_text(encoding="utf-8", errors="replace")
    sched = next((m for m in dfp.SECTION_RE.finditer(raw) if m.group(1).upper() == "SCHEDULE"), None)
    if sched is None:
        raise SpecError(f"{deck.name}: no SCHEDULE line in the DATA file")
    end = re.search(r"^\s*END\s*$", raw[sched.end() :], re.M | re.I)
    stop = sched.end() + end.start() if end else len(raw)
    body = dfp.expand_includes(dfp.strip_comments(raw[sched.end() : stop]), deck.parent, deck, [], set())
    return raw[: sched.end()], body, raw[stop:]


def ignore_results(_folder: str, names: list[str]) -> set[str]:
    return {n for n in names if Path(n).suffix.upper() in RESULT_SUFFIXES or RESULT_RE.search(n)}


def build(spec: Spec, out: Path, target: str, target_source: str) -> tuple[dict[str, Any], int]:
    if out.exists() and any(out.iterdir()):
        raise SpecError(f"output folder is not empty: {out}")
    start = deck_start(spec.deck, spec.start)
    network = re.search(r"^\s*NETWORK\s*$", deck_text(spec.deck), re.M | re.I)
    if any(isinstance(op, BackPressure) for op in spec.operations) and not network:
        raise SpecError(
            "back_pressure writes NODEPROP/BRANPROP: the deck needs NETWORK in RUNSPEC (extended network)"
        )
    head, schedule, tail = split_schedule(spec.deck)
    inserts: list[tuple[date, str]] = []
    declared = Declaration()
    for op in spec.operations:
        events = op.events()
        if not events:
            continue
        d = op.declare()
        declared.keywords |= d.keywords
        declared.wells |= d.wells
        declared.groups |= d.groups
        declared.new_wells |= d.new_wells
        for day, block in events:
            inserts.append((day, f"-- scenario {spec.scenario_id}: {op.id} ({op.TYPE})\n{block.text()}\n"))
    text, timing_changed, notes = merge(chunks_of(schedule, start), start, inserts)
    rewrites = sorted({b.keyword for op in spec.operations for _, b in op.events()} & set(FULL_RECORD))
    notes += [f"{k} redefines the whole record: {FULL_RECORD[k]}" for k in rewrites]
    notes += summary_gaps(deck_text(spec.deck), declared.new_wells)
    declared.keywords |= timing_changed

    case_out = out / spec.case_root.name
    shutil.copytree(spec.case_root, case_out, ignore=ignore_results, symlinks=True)
    deck_out = case_out / spec.deck.relative_to(spec.case_root)
    include = f"{spec.deck.stem}_SCHEDULE.inc"
    (deck_out.parent / include).write_text(
        f"-- SCHEDULE of scenario {spec.scenario_id}, rendered by scenario_build.py from {spec.deck.name}\n"
        + text,
        encoding="utf-8",
    )
    deck_out.write_text(f"{head}\nINCLUDE\n  '{include}' /\n\n{tail.lstrip()}", encoding="utf-8")

    base_fp = dfp.build_fingerprint(spec.deck)
    scen_fp = dfp.build_fingerprint(deck_out)
    diff = dfp.fingerprint_diff(base_fp, scen_fp)
    undeclared = sorted(set(diff["changed_keywords"]) - declared.keywords)
    unchanged = sorted(
        declared.keywords
        - set(diff["changed_keywords"])
        - {k for k in declared.keywords if k not in dfp.TRACKED_KEYWORDS}
    )
    rows, support_notes = ds.classify(deck_out, ds.load_table())
    verdicts = ds.verdicts(rows, target)

    wells = sorted(declared.wells)
    runmeta = {
        "scenario_id": spec.scenario_id,
        "parent_scenario_id": spec.parent,
        "description": spec.description or f"rendered by scenario_build.py from {spec.deck.name}",
        "deck_data": str(deck_out.resolve()),
        "built_by": {
            "script": "scenario_build.py",
            "spec_schema": SCHEMA_VERSION,
            "spec_sha256": spec.sha256,
            **(
                {"controls": {"source": spec.controls_source, "values": spec.values}}
                if spec.variables
                else {}
            ),
        },
        "changes": {
            "wells": sorted(declared.new_wells),
            "constraints": [
                {"keyword": b.keyword, "op": op.id, "type": op.TYPE}
                for op in spec.operations
                for _, b in op.events()
            ],
            "schedule": [
                {"keyword": k, "note": "split by an operation date"} for k in sorted(timing_changed)
            ],
        },
        "changed_keywords": sorted(declared.keywords),
        "touched_wells": wells,
        "expected_changes": {
            "wells": wells,
            "keywords": sorted(declared.keywords),
            "field_vectors_may_move": spec.field_vectors,
            "max_rel_delta": "any",
        },
    }
    yaml = contract_lib._yaml()
    (out / "runmeta.yml").write_text(yaml.safe_dump(runmeta, sort_keys=False), encoding="utf-8")
    (out / "deck_fingerprint.json").write_text(
        json.dumps(scen_fp, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": spec.scenario_id,
        "deck": str(deck_out.relative_to(out)),
        "start": start.isoformat(),
        "inserts": [
            {"date": d.isoformat(), "keyword": t.split("\n")[1]}
            for d, t in sorted(inserts, key=lambda x: x[0])
        ],
        "timing_changed": sorted(timing_changed),
        "controls": {"source": spec.controls_source, "values": spec.values} if spec.variables else None,
        "notes": notes,
        "declared": {
            "keywords": sorted(declared.keywords),
            "wells": wells,
            "groups": sorted(declared.groups),
        },
        "declaration_check": {
            "fingerprint_changed": diff["changed_keywords"],
            "undeclared": undeclared,
            "declared_but_unchanged": unchanged,
            "status": "PASS" if not undeclared and not unchanged else "FAIL",
        },
        "deck_support": {
            "target_simulator": target,
            "target_source": target_source,
            "table_commit": ds.PINNED_COMMIT,
            "verdicts": verdicts,
            "flagged": [
                {k: r[k] for k in ("name", "status", "critical")} for r in rows if r["status"] != "flow-ok"
            ],
            "notes": support_notes,
        },
    }
    (out / "scenario_build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failed = report["declaration_check"]["status"] == "FAIL" or verdicts["target"] == "FAIL"
    return report, 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="new or empty scenario folder")
    contract_lib.add_contract_args(parser)
    parser.add_argument("--target", help=f"override simulator.name: one of {sorted(ds.TARGETS)}")
    parser.add_argument(
        "--controls", type=Path, help="Everest control file (<control name>.json); default: initial values"
    )
    args = parser.parse_args(argv)
    try:
        target, source = ds.resolve_target(args)
        if target not in ds.TARGETS:
            raise SpecError(f"simulator {target!r} is not one of {sorted(ds.TARGETS)}")
        spec = load_spec(args.spec, args.controls)
        args.out.mkdir(parents=True, exist_ok=True)
        report, code = build(spec, args.out, target, source)
    except (SpecError, OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    check, support = report["declaration_check"], report["deck_support"]
    print(
        f"scenario {report['scenario_id']}: {len(report['inserts'])} keyword blocks at {sorted({i['date'] for i in report['inserts']})}"
    )
    print(f"declared keywords: {report['declared']['keywords']}; wells: {report['declared']['wells']}")
    print(f"declaration check: {check['status']} (fingerprint changed {check['fingerprint_changed']})")
    if check["undeclared"] or check["declared_but_unchanged"]:
        print(
            f"  undeclared: {check['undeclared']}; declared but unchanged: {check['declared_but_unchanged']}"
        )
    v = support["verdicts"]
    print(
        f"deck_support ({support['target_simulator']}, table {support['table_commit'][:7]}): target {v['target']}, box Flow {v['box_flow']}"
    )
    for note in report["notes"]:
        print(f"note: {note}")
    print(f"wrote: {args.out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
