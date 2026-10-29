#!/usr/bin/env python3
"""Add or replace a LOCALIZATION { EAST; NORTH; RADIUS; } block on the well observations of an ERT obs file.

A SUMMARY_OBSERVATION or BREAKTHROUGH_OBSERVATION whose KEY is a well vector (W*:WELL) gets the
coordinates of its well from --locations (the CSV of obs_locations.py). In a bulk SUMMARY block, each
well of the VALUES csv gets a WELL <name> { LOCALIZATION {...}; }; entry. Field (F*), group (G*) and
other keys get no location. A FIELD or SURFACE update with DISTANCE does not use them (ERT 26 excludes
observations without a location from a distance update), so the script lists them.

The file is rewritten token by token: comments, layout and the other keys stay as they are.
The output is a new file (--out). --inplace overwrites the input. When ERT is installed, the output is
parsed with ERT's own observation parser, and the count of located observations must agree.

Exit 0 all observations located, 1 written but some observations have no location, 2 input error
(nothing written).
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

LOCATED_TYPES = ("SUMMARY_OBSERVATION", "BREAKTHROUGH_OBSERVATION")


class InputError(Exception):
    """A missing or unusable input. Exit 2."""


@dataclass
class Token:
    kind: str  # ws, comment, punct, word
    text: str


@dataclass
class Block:
    """A '{ ... }' body. open and close are token indexes."""

    open: int
    close: int
    decls: list[Decl] = field(default_factory=list)

    def find(self, *words: str) -> Decl | None:
        for decl in self.decls:
            if [w.upper() for w in decl.words[: len(words)]] == [w.upper() for w in words]:
                return decl
        return None

    def value(self, name: str) -> str | None:
        decl = self.find(name)
        return decl.value if decl is not None and isinstance(decl.value, str) else None


@dataclass
class Decl:
    """'NAME = value;', 'LOCALIZATION {...};' or 'WELL OP1 {...};'. start and end (the ';') are token indexes."""

    words: list[str]
    value: str | Block | None
    start: int
    end: int


@dataclass
class Statement:
    words: list[str]  # type and optional name
    body: Block | None
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    """The lexer of ERT's observation grammar: '--' at the start of a token runs to the end of the line."""
    tokens: list[Token] = []
    pos, n = 0, len(text)
    while pos < n:
        ch = text[pos]
        if ch.isspace():
            end = pos
            while end < n and text[end].isspace():
                end += 1
            tokens.append(Token("ws", text[pos:end]))
        elif text.startswith("--", pos):
            end = text.find("\n", pos)
            end = n if end < 0 else end
            tokens.append(Token("comment", text[pos:end]))
        elif ch in "{};=":
            end = pos + 1
            tokens.append(Token("punct", ch))
        else:
            end = pos
            while end < n and not text[end].isspace() and text[end] not in "{};=":
                end += 1
            tokens.append(Token("word", text[pos:end]))
        pos = end
    return tokens


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def skip(self) -> None:
        while self.pos < len(self.tokens) and self.tokens[self.pos].kind in ("ws", "comment"):
            self.pos += 1

    def peek(self) -> Token | None:
        self.skip()
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self, text: str | None = None) -> int:
        token = self.peek()
        if token is None or (text is not None and token.text != text):
            got = "the end of the file" if token is None else repr(token.text)
            raise InputError(f"obs file: expected {text or 'a token'}, got {got} (token {self.pos})")
        self.pos += 1
        return self.pos - 1

    def words(self) -> list[str]:
        out = []
        while (token := self.peek()) is not None and token.kind == "word":
            out.append(token.text)
            self.pos += 1
        return out

    def block(self) -> Block:
        block = Block(open=self.take("{"), close=-1)
        while (token := self.peek()) is not None and token.text != "}":
            start = self.pos
            words = self.words()
            if not words:
                raise InputError(f"obs file: unexpected {token.text!r} in a block")
            value: str | Block | None = None
            nxt = self.peek()
            if nxt is not None and nxt.text == "=":
                self.take("=")
                value = " ".join(self.words())
            elif nxt is not None and nxt.text == "{":
                value = self.block()
            block.decls.append(Decl(words, value, start, self.take(";")))
        block.close = self.take("}")
        return block

    def statements(self) -> list[Statement]:
        out = []
        while self.peek() is not None:
            start = self.pos
            words = self.words()
            if not words:
                raise InputError(f"obs file: expected an observation type, got {self.peek().text!r}")
            body = self.block() if (t := self.peek()) is not None and t.text == "{" else None
            out.append(Statement(words, body, start, self.take(";")))
        return out


def key_family(key: str) -> tuple[str, str | None]:
    """('WOPR', 'OP1') for a well vector, ('FOPT', None) otherwise."""
    vector, _, rest = key.partition(":")
    if vector.upper().startswith("W") and rest and ":" not in rest:
        return vector.upper(), rest
    return vector.upper(), None


def localization_text(east: float, north: float, radius: float) -> str:
    return f"LOCALIZATION {{ EAST = {east:.2f}; NORTH = {north:.2f}; RADIUS = {radius:g}; }};"


def read_locations(path: Path, source: str) -> dict[str, tuple[float, float]]:
    """well -> (east, north) from the obs_locations.py CSV, rows of one source."""
    if not path.is_file():
        raise InputError(f"--locations {path} not found")
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or not {"well", "east", "north", "source"} <= set(rows[0]):
        raise InputError(f"--locations {path}: need the columns well,east,north,source")
    out = {}
    for row in rows:
        if row["source"] != source:
            continue
        east, north = float(row["east"]), float(row["north"])
        if not (math.isfinite(east) and math.isfinite(north)):
            raise InputError(f"--locations {path}: well {row['well']} has no finite coordinates")
        out[row["well"]] = (east, north)
    if not out:
        sources = sorted({row["source"] for row in rows})
        raise InputError(f"--locations {path}: no row with source {source!r} (sources: {', '.join(sources)})")
    return out


@dataclass
class Summary:
    located: Counter = field(default_factory=Counter)  # family -> count written
    kept: Counter = field(default_factory=Counter)  # family -> count with a LOCALIZATION we did not touch
    unlocated: Counter = field(default_factory=Counter)  # family -> count without a location
    missing_wells: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def n_located(self) -> int:
        return sum(self.located.values()) + sum(self.kept.values())


class Rewriter:
    def __init__(
        self, text: str, locations: dict[str, tuple[float, float]], radius: float, directory: Path
    ) -> None:
        self.tokens = tokenize(text)
        self.locations = locations
        self.radius = radius
        self.directory = directory
        self.edits: list[tuple[int, int, str]] = []  # replace tokens [start, end) with text
        self.summary = Summary()

    def indent_of(self, block: Block) -> str | None:
        """The indent of the first declaration if the block spans lines, else None."""
        if not block.decls:
            return None
        before = self.tokens[block.decls[0].start - 1] if block.decls[0].start > 0 else None
        if before is not None and before.kind == "ws" and "\n" in before.text:
            return before.text.rsplit("\n", 1)[1]
        return None

    def put(self, block: Block, text: str, replace: Decl | None) -> None:
        if replace is not None:
            self.edits.append((replace.start, replace.end + 1, text))
            return
        indent = self.indent_of(block)
        at = block.decls[-1].end + 1 if block.decls else block.open + 1
        self.edits.append((at, at, f"\n{indent}{text}" if indent is not None else f" {text}"))

    def well_location(self, well: str) -> tuple[float, float] | None:
        if well not in self.locations:
            self.summary.missing_wells.add(well)
            return None
        return self.locations[well]

    def keyed(self, body: Block) -> None:
        key = body.value("KEY")
        if key is None:
            raise InputError("obs file: an observation has no KEY")
        family, well = key_family(key)
        existing = body.find("LOCALIZATION")
        if well is None:
            (self.summary.kept if existing is not None else self.summary.unlocated)[family] += 1
            return
        if (loc := self.well_location(well)) is not None:
            self.put(body, localization_text(*loc, self.radius), existing)
            self.summary.located[family] += 1

    def bulk(self, body: Block) -> None:
        values = body.value("VALUES")
        if values is None:
            raise InputError("obs file: a bulk SUMMARY block has no VALUES")
        path = Path(values) if Path(values).is_absolute() else self.directory / values
        if not path.is_file():
            raise InputError(f"obs file: VALUES {values} not found (looked in {path})")
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle, skipinitialspace=True))
        wells: dict[str, Counter] = {}
        for row in rows:
            keyword = (row.get("keyword") or "").strip().upper()
            well = (row.get("well") or "").strip()
            if keyword.startswith("W") and well:
                wells.setdefault(well, Counter())[keyword] += 1
            else:
                self.summary.unlocated[keyword or "?"] += 1
        for well, families in wells.items():
            loc = self.well_location(well)
            if loc is None:
                continue
            new = localization_text(*loc, self.radius)
            entry = body.find("WELL", well)
            if entry is None:
                self.put(body, f"WELL {well} {{ {new} }};", None)
            else:
                assert isinstance(entry.value, Block)
                self.put(entry.value, new, entry.value.find("LOCALIZATION"))
            self.summary.located.update(families)

    def run(self) -> str:
        for statement in Parser(self.tokens).statements():
            kind = statement.words[0].upper()
            body = statement.body
            if body is None:
                continue
            if kind in LOCATED_TYPES:
                self.keyed(body)
            elif kind == "SUMMARY":
                self.bulk(body)
            elif kind == "RFT_OBSERVATION":
                self.summary.kept["RFT"] += 1
            else:
                self.summary.unlocated[kind] += 1
        if self.summary.missing_wells:
            names = ", ".join(sorted(self.summary.missing_wells))
            raise InputError(f"no coordinates for well(s) {names} in --locations. Add them, or fix the KEY")
        texts = [t.text for t in self.tokens]
        for start, end, text in sorted(self.edits, reverse=True):
            texts[start:end] = [text]
        return "".join(texts)


def ert_located_count(text: str, filename: str, directory: Path) -> int | None:
    """Parse with ERT 26 and count the observations with a location. None if ERT is not installed."""
    try:
        from ert.config import ShapeRegistry
        from ert.config._observations import make_observations
        from ert.config.parsing.observations_parser import parse_observations
    except ImportError:
        return None
    registry = ShapeRegistry()
    observations = make_observations(str(directory), parse_observations(text, filename), registry)
    return sum(1 for obs in observations if getattr(obs, "shape_id", None) is not None)


def print_summary(summary: Summary) -> None:
    families = sorted(set(summary.located) | set(summary.kept) | set(summary.unlocated))
    print("| Key family | Located (written) | Located (kept) | No location |")
    print("|---|---|---|---|")
    for family in families:
        print(
            f"| {family} | {summary.located[family]} | {summary.kept[family]} | {summary.unlocated[family]} |"
        )
    print(f"Located: {summary.n_located}. No location: {sum(summary.unlocated.values())}.")
    if summary.unlocated:
        print(
            "WARNING: observations without a location ("
            + ", ".join(sorted(summary.unlocated))
            + ") are excluded from a FIELD or SURFACE update with DISTANCE. GLOBAL and ADAPTIVE updates use them."
        )
    for note in summary.notes:
        print(f"NOTE: {note}")


def resolve_radius(text: str, locations: dict[str, tuple[float, float]]) -> float:
    """A number, or auto: 1.25 times the median nearest-well spacing (ahm_defaults, inferred from drogon-lite)."""
    if text.strip().lower() != "auto":
        try:
            return float(text)
        except ValueError as error:
            raise InputError(f"--radius {text!r} is not a number or auto") from error
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "history-match-qc" / "scripts" / "ahm_defaults.py"
    spec = importlib.util.spec_from_file_location("ahm_defaults", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("ahm_defaults", module)
    spec.loader.exec_module(module)
    try:
        guide = module.radius_guidance(list(locations.values()))
    except ValueError as error:
        raise InputError(f"--radius auto: {error}") from error
    sweep = ", ".join(f"{r:.0f}" for r in guide.sweep_m)
    print(
        f"RADIUS auto {guide.radius_m:.0f} m = {module.RADIUS_PER_WELL_SPACING.value:g} x median nearest-well"
        f" spacing {guide.median_spacing_m:.0f} m ({guide.basis.value}). Sweep {sweep} m with radius_sweep.py"
    )
    return round(guide.radius_m)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("obs", type=Path, help="The ERT observation file")
    parser.add_argument("--locations", required=True, type=Path, help="well,east,north,source CSV")
    parser.add_argument("--source", default="compdat", help="Rows of this source (compdat or welspecs)")
    parser.add_argument(
        "--radius",
        required=True,
        help="RADIUS in metres (a modelling choice), or auto: ahm_defaults.radius_guidance of the wells (inferred)",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--out", type=Path, help="The new obs file")
    target.add_argument("--inplace", action="store_true", help="Overwrite the input file")
    args = parser.parse_args(argv)
    try:
        locations = read_locations(args.locations, args.source)
        args.radius = resolve_radius(args.radius, locations)
        if not (math.isfinite(args.radius) and args.radius > 0):
            raise InputError("--radius must be a finite number larger than 0")
        if not args.obs.is_file():
            raise InputError(f"{args.obs} not found")
        out = args.obs if args.inplace else args.out
        if not args.inplace and out.resolve() == args.obs.resolve():
            raise InputError("--out is the input file. Use --inplace to overwrite it")
        directory = args.obs.resolve().parent
        rewriter = Rewriter(args.obs.read_text(), locations, args.radius, directory)
        text = rewriter.run()
        summary = rewriter.summary
        try:
            ert_count = ert_located_count(text, str(out), directory)
        except Exception as error:  # ERT rejects the output: never write it
            raise InputError(f"ERT's observation parser rejects the output: {error}") from error
        if ert_count is None:
            summary.notes.append("ERT is not installed. The output is not checked with ERT's parser")
        elif ert_count < summary.n_located:
            raise InputError(f"ERT finds {ert_count} located observations, the rewrite {summary.n_located}")
        else:
            summary.notes.append(f"ERT's parser reads the output: {ert_count} observations with a location")
        if out.resolve().parent != directory and "VALUES" in text:
            summary.notes.append(
                "the output is in another folder. Relative VALUES paths resolve from that folder"
            )
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"Wrote {out} (RADIUS {args.radius:g} m, source {args.source})")
    print_summary(summary)
    return 1 if summary.unlocated else 0


if __name__ == "__main__":
    sys.exit(main())
