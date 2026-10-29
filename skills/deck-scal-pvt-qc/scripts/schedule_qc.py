#!/usr/bin/env python3
"""Schedule QC for well references, completion bounds, history rates, and date order. Read-only.

Two parsers:
    --parser opm   (default) the OPM parser through res2df reads the deck, its INCLUDE files and DIMENS,
                   as Flow does. A text scan first reports missing and cyclic INCLUDE files (I001, I002).
                   The parser then does not run, because it skips a missing file and stops on a cycle.
                   That is L001, and so is a deck that does not parse.
    --parser text  a plain text scan that needs no res2df. It follows INCLUDE files.
The report names the parser that ran. There is no fallback from one parser to the other.
Exit code: 0 = no errors, 1 = errors, 2 = usage error (also res2df missing with --parser opm).
"""

import argparse
import fnmatch
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

WELL_KEYWORDS = {"WELSPECS", "COMPDAT", "WCONHIST", "WCONPROD", "WCONINJE", "WCONINJH"}
CONTROL_KEYWORDS = ("WCONHIST", "WCONPROD", "WCONINJE", "WCONINJH")
PARSER_NOTES = {
    "opm": "OPM parser (res2df): INCLUDE files and DIMENS as Flow reads them. No ACTNUM or unit checks.",
    "text": "Text scan only: no ACTNUM, control inference or unit checks.",
}


def records(lines):
    """Read slash records, quoted names and repeat/default fields from (file, line, text) rows."""
    keyword = None
    buffer = []
    for source, number, raw in lines:
        line = re.sub(
            r"'[^']*'|\"[^\"]*\"|--.*", lambda m: "" if m[0].startswith("--") else m[0], raw
        ).strip()
        if not line:
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", line):
            keyword = line.upper()
            buffer = []
            continue
        tokens = re.findall(r"'[^']*'|\"[^\"]*\"|/|[^\s/]+", line)
        for token in tokens:
            if token == "/":
                if buffer:
                    yield keyword, buffer, source, number
                buffer = []
                continue
            token = token.strip("'\"")
            repeat = re.fullmatch(r"(\d+)\*(.*)", token)
            buffer.extend([repeat[2] or "*"] * int(repeat[1]) if repeat else [token])


def deck_lines(path, findings, active=()):
    """Yield (file name, line, text) rows with each INCLUDE file expanded in place.

    An include path is relative to the file that holds the INCLUDE keyword.
    """
    active = (*active, path.resolve())
    waiting = False
    for number, raw in enumerate(path.read_text(encoding="latin-1").splitlines(), 1):
        line = raw.split("--", 1)[0].strip()
        match = re.match(r"INCLUDE\b\s*(.*)", line, re.I)
        if match and not waiting:
            waiting, line = True, match[1]
        elif not waiting:
            yield path.name, number, raw
            continue
        match = re.match(r"'([^']*)'|\"([^\"]*)\"|(\S+)", line)
        if not match:
            continue
        waiting = False
        target = match[1] or match[2] or match[3].rstrip("/")
        full = Path(os.path.normpath(path.parent / target))
        where = f"{path.name}:{number}"
        if not full.is_file():
            findings.append(
                dict(code="I001", severity="E", where=where, message=f"include file not found: {target}")
            )
        elif full.resolve() in active:
            findings.append(dict(code="I002", severity="E", where=where, message=f"include cycle: {target}"))
        else:
            yield from deck_lines(full, findings, active)


def check_schedule(text, dims=None):
    return check_lines(((None, number, raw) for number, raw in enumerate(text.splitlines(), 1)), dims)


def check_deck(path, dims=None):
    include_findings = []
    findings = check_lines(deck_lines(path, include_findings), dims)
    return include_findings + findings


def check_lines(lines, dims=None):
    wells = set()
    previous = None
    findings = []
    unchecked = {}
    well_records = 0
    restart = False
    restart_wells = set()

    def add(code, keyword, source, line, message, severity="E"):
        where = f"{keyword}:{line}" if source is None else f"{source}:{line} {keyword}"
        findings.append(dict(code=code, severity=severity, where=where, message=message))

    for keyword, values, source, line in records(lines):
        well_records += keyword in WELL_KEYWORDS
        restart = restart or keyword == "RESTART"
        if keyword == "DIMENS":
            try:
                dims = tuple(int(v) for v in values[:3])
                if len(dims) != 3 or min(dims) < 1:
                    raise ValueError
            except ValueError:
                dims = None
                add("W005", keyword, source, line, "invalid DIMENS")
        if keyword == "WELSPECS":
            wells.add(values[0])
        if keyword in {"WCONHIST", "WCONPROD", "WCONINJE", "WCONINJH", "COMPDAT"}:
            name = values[0]
            if any(fnmatch.fnmatchcase(well, name) for well in wells):
                pass
            elif restart:
                restart_wells.add(name)
            else:
                add("W001", keyword, source, line, f"well {name} has no preceding WELSPECS")
        if keyword == "COMPDAT":
            if dims is None:
                unchecked[source] = unchecked.get(source, 0) + 1
            else:
                try:
                    i, j, k1, k2 = map(int, values[1:5])
                    # I/J zero means use the WELSPECS location; do not invent that location.
                    if not (0 <= i <= dims[0] and 0 <= j <= dims[1] and 1 <= k1 <= k2 <= dims[2]):
                        add(
                            "W002",
                            keyword,
                            source,
                            line,
                            "completion indices outside DIMENS or reversed K range",
                        )
                except (ValueError, TypeError):
                    add(
                        "W005",
                        keyword,
                        source,
                        line,
                        "default or invalid completion indices; bounds not checked",
                        "W",
                    )
        if keyword == "WCONHIST":
            for value in values[3:6]:
                try:
                    if float(value.replace("D", "E")) < 0:
                        add("W003", keyword, source, line, f"negative history rate for {values[0]}")
                        break
                except ValueError:
                    if value != "*":
                        add("W005", keyword, source, line, "invalid history rate")
        if keyword == "DATES":
            try:
                # Eclipse accepts JLY for July.
                text = " ".join(values[:3]).upper().replace("JLY", "JUL")
                date = datetime.strptime(text, "%d %b %Y")
                if previous is not None and date <= previous:
                    add("W004", keyword, source, line, "DATES is not strictly increasing")
                previous = date
            except ValueError:
                add("W005", keyword, source, line, "invalid DATES record")
    for source, count in unchecked.items():
        findings.append(
            dict(
                code="W005",
                severity="W",
                where=source or "COMPDAT",
                message=f"DIMENS unavailable; {count} COMPDAT records not bounds-checked",
            )
        )
    if restart_wells:
        findings.append(
            dict(
                code="W001",
                severity="W",
                where="RESTART",
                message=f"wells {', '.join(sorted(restart_wells))} have no WELSPECS in this deck; "
                "they can come from the restart file and were not checked",
            )
        )
    if not well_records:
        findings.append(
            dict(
                code="W006",
                severity="W",
                where="SCHEDULE",
                message="no well keywords found; nothing was checked",
            )
        )
    return findings


def check_opm_deck(deck, dims=None):
    """The text rules on a deck that the OPM parser read. INCLUDE files and DIMENS are already resolved."""
    from res2df.compdat import COMPDAT_RENAMER, parse_opmio_date_rec, parse_opmio_deckrecord

    findings = []
    wells = set()
    restart_wells = set()
    well_records = 0
    unchecked = 0
    previous = None
    restart = "RESTART" in deck

    def add(code, where, message, severity="E"):
        findings.append(dict(code=code, severity=severity, where=where, message=message))

    if "DIMENS" in deck:
        record = deck["DIMENS"][0]
        dims = tuple(record[index].get_int(0) for index in range(3))
        if min(dims) < 1:
            dims = None
            add("W005", "DIMENS", "invalid DIMENS")
    for keyword in deck:
        name = keyword.name
        if name == "DATES":
            for record in keyword:
                try:
                    date = parse_opmio_date_rec(record)
                except (ValueError, KeyError):
                    add("W005", name, "invalid DATES record")
                    continue
                if previous is not None and date <= previous:
                    add("W004", f"{name} {date}", "DATES is not strictly increasing")
                previous = date
        if name not in WELL_KEYWORDS:
            continue
        for record in keyword:
            well_records += 1
            row = parse_opmio_deckrecord(record, name, renamer=COMPDAT_RENAMER if name == "COMPDAT" else None)
            well = row["WELL"]
            where = f"{name} {well}"
            if name == "WELSPECS":
                wells.add(well)
                continue
            if not any(fnmatch.fnmatchcase(known, well) for known in wells):
                if restart:
                    restart_wells.add(well)
                else:
                    add("W001", where, f"well {well} has no preceding WELSPECS")
            if name == "COMPDAT":
                i, j, k1, k2 = (row.get(column) for column in ("I", "J", "K1", "K2"))
                if dims is None:
                    unchecked += 1
                elif None in (i, j, k1, k2):
                    add("W005", where, "default or invalid completion indices; bounds not checked", "W")
                elif not (0 <= i <= dims[0] and 0 <= j <= dims[1] and 1 <= k1 <= k2 <= dims[2]):
                    add("W002", where, "completion indices outside DIMENS or reversed K range")
            if name == "WCONHIST" and any((row.get(rate) or 0) < 0 for rate in ("ORAT", "WRAT", "GRAT")):
                add("W003", where, f"negative history rate for {well}")
    if unchecked:
        add("W005", "COMPDAT", f"DIMENS unavailable; {unchecked} COMPDAT records not bounds-checked", "W")
    if restart_wells:
        add(
            "W001",
            "RESTART",
            f"wells {', '.join(sorted(restart_wells))} have no WELSPECS in this deck; "
            "they can come from the restart file and were not checked",
            "W",
        )
    if not well_records:
        add("W006", "SCHEDULE", "no well keywords found; nothing was checked", "W")
    return findings


def check_deck_opm(path, dims=None):
    """Include scan as text, then the OPM parser. Return the findings."""
    include_findings = []
    for _ in deck_lines(path, include_findings):
        pass
    if include_findings:  # the OPM parser stops the process on an include cycle and skips a missing file
        message = "the OPM parser did not run because of the INCLUDE errors"
        return [*include_findings, dict(code="L001", severity="E", where=path.name, message=message)]
    from res2df import ResdataFiles

    try:
        deck = ResdataFiles(str(path)).get_deck()
    except Exception as error:  # opm raises many types. A deck that does not parse is a finding
        message = "the OPM parser cannot read the deck: " + " ".join(str(error).splitlines()[:2])
        return [dict(code="L001", severity="E", where=path.name, message=message)]
    return include_findings + check_opm_deck(deck, dims)


def well_timeline(deck):
    """Flow state of each well after each schedule date, from res2df wcon, WELOPEN and COMPDAT.

    Returns {well: [(date, state)]} in date order. The state is None when the well can flow. Else it is
    the reason, for example "is SHUT", "has a zero rate in WCONHIST", or "has no open connection".
    WCONPROD and WCONINJE give a status only. Their rates are targets, not flow.
    On one date, WCON rows apply before WELOPEN. Wildcard well names match the wells of the deck.
    """
    from res2df import compdat, wcon

    tables = compdat.deck2dfs(deck)
    if not tables:
        raise ValueError("res2df cannot date the SCHEDULE (TSTEP before START?)")
    controls = wcon.df(deck)
    connections = tables["COMPDAT"]
    welopen = tables.get("WELOPEN")
    if welopen is not None and not welopen.empty:
        local = welopen[["I", "J", "K", "C1", "C2"]].notna().any(axis=1)
        welopen = welopen[~local]
    named = set(connections.get("WELL", [])) | set(controls.get("WELL", []))
    wells = {name for name in named if not any(char in name for char in "*?")}

    def matches(name):
        return [well for well in wells if fnmatch.fnmatchcase(well, name)]

    events = defaultdict(list)
    for row in controls.to_dict("records"):
        zero = None
        if row["KEYWORD"] == "WCONHIST" and sum(abs(row.get(r) or 0) for r in ("ORAT", "WRAT", "GRAT")) == 0:
            zero = "has a zero rate in WCONHIST"
        if row["KEYWORD"] == "WCONINJH" and not (row.get("RATE") or 0) > 0:
            zero = "has a zero rate in WCONINJH"
        for well in matches(row["WELL"]):
            events[well].append((row["DATE"], 0, "control", (row["STATUS"], zero)))
    for row in [] if welopen is None else welopen.to_dict("records"):
        for well in matches(row["WELL"]):
            events[well].append((row["DATE"], 1, "welopen", row["STATUS"]))
    for row in connections.to_dict("records"):
        cell = (row["I"], row["J"], row["K1"])
        events[row["WELL"]].append((row["DATE"], 2, cell, row["OP/SH"] in ("OPEN", "AUTO")))

    timeline = {}
    for well in sorted(wells):
        status = zero = None
        controlled = False
        open_cells = {}
        states = []
        for date, _, kind, value in sorted(events[well], key=lambda event: event[:2]):
            if kind == "control":
                (status, zero), controlled = value, True
            elif kind == "welopen":
                status = value
            else:
                open_cells[kind] = value
            if not controlled:
                state = "has no WCON keyword yet"
            elif status in ("SHUT", "STOP"):
                state = f"is {status}"
            elif zero or not any(open_cells.values()):
                state = zero or "has no open connection"
            else:
                state = None
            if states and states[-1][0] == date:
                states.pop()
            states.append((date, state))
        timeline[well] = states
    return timeline


def well_state(timeline, well, when):
    """The state of a well over the report step that ends at `when` (a date). None means it can flow."""
    if well not in timeline:
        return "has no COMPDAT or WCON keyword"
    before = [state for date, state in timeline[well] if date < when]
    return before[-1] if before else f"is not open yet (first schedule date {timeline[well][0][0]})"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument("--parser", choices=("opm", "text"), default="opm", help="Deck parser. Default: opm")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--dimens",
        type=int,
        nargs=3,
        metavar=("NX", "NY", "NZ"),
        help="grid size for an include without DIMENS",
    )
    args = parser.parse_args(argv)
    if args.dimens and min(args.dimens) < 1:
        parser.error("--dimens values must be positive")
    if args.parser == "opm" and importlib.util.find_spec("res2df") is None:
        print("ERROR: --parser opm needs res2df. Use --parser text for the text scan", file=sys.stderr)
        return 2
    dims = tuple(args.dimens) if args.dimens else None
    check = check_deck_opm if args.parser == "opm" else check_deck
    try:
        findings = check(args.deck, dims)
    except OSError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"parser": args.parser, "findings": findings}, indent=2))
    else:
        for row in findings:
            print(f"{row['severity']} {row['code']} [{row['where']}] {row['message']}")
        print(f"Parser: {PARSER_NOTES[args.parser]}")
    return int(any(row["severity"] == "E" for row in findings))


if __name__ == "__main__":
    sys.exit(main())
