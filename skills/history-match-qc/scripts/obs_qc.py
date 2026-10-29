#!/usr/bin/env python3
"""Check ERT observation files before a history match. Read-only.

It parses SUMMARY_OBSERVATION, GENERAL_OBSERVATION and HISTORY_OBSERVATION blocks. It computes the error
that ERT uses for each summary observation, and it reports problems that waste a history match.
The error rules follow the ERT source (ert/config/_observations.py, ERT 23):
    ERROR_MODE ABS     (default): error = |ERROR|
    ERROR_MODE REL               : error = |VALUE| * ERROR
    ERROR_MODE RELMIN            : error = max(|VALUE| * ERROR, ERROR_MIN)    ERROR_MIN default is 0.1
The error must be larger than 0. ERT 23 accepts only DATE. DAYS, HOURS and RESTART are errors, also next to
DATE. 'ert convert_observations <config>' changes them to DATE.

GENERAL_OBSERVATION (GEN_DATA responses, for example RFT pressures): the script checks DATA, the time
(RESTART or DATE), VALUE/ERROR or OBS_FILE (value and error pairs), and INDEX_LIST or INDEX_FILE.
Each error must be larger than 0, each file must exist, and the index count must equal the value count.
It does not read the GEN_DATA files of a run.

Weight balance: each observation adds about 1 to the misfit when its error is right, so the share of the
data count is the share of the misfit budget. The script reports, for each group (the vector mnemonic, for
example WBHP, or GEN:<family>) and each key, the count, the count share, and the sum of 1/error^2.
The sum of 1/error^2 depends on the unit. Compare it only inside one group.
The family of a GEN_DATA response is the DATA name up to the first '_' (RFT_R_A2 gives GEN:RFT), so that
GEN data groups by measurement type like the summary groups. --gen-group exact keeps one group per DATA.
A group share below --share-min or above --share-max is a warning. These are proposals for the user.
The script never changes an observation.
A zero VALUE with an ABS or RELMIN error that is large against the signal of the key is a warning:
early zeros (for example water cut before breakthrough) then carry full weight with little information.

Usage:
    obs_qc.py <file.obs> [<file2.obs> ...] [--case <runpath>/eclipse/model/CASE.DATA] [--deck CASE.DATA]
              [--dense-n 10] [--share-min 0.15] [--share-max 0.5] [--zero-error-ratio 0.05]
              [--gen-group family|exact] [--json]

With --case, the script reads the summary of one simulated case (needs res2df). It then reports
observation keys that the simulator does not write, and dates outside the simulated period.
It also warns about well observations at a date when the well has no flow in the case.
--case must be the ECLBASE deck of a case that has summary files. Another file is a usage error.

With --deck, the script reads the SCHEDULE of a deck with res2df (the OPM parser, INCLUDE files resolved).
The deck does not need to be run. For each well it makes a timeline from WCONHIST, WCONPROD, WCONINJH,
WCONINJE, WELOPEN and COMPDAT. A well observation (KEY W*:WELL) gets the warning HMQC-OBS-NOFLOW when,
in the report step that ends at its DATE, the well is not open yet, is SHUT or STOP, has a zero history
rate, or has no open connection. Cumulative keys (WOPT, WWIT, ...) are not flagged.
A WBHP or WTHP of such a well is a shut-in pressure. A WWCT or WGOR of zero rates is noise.
res2df missing, or a deck that does not parse, is a usage error (exit 2).

Errors (exit code 1): missing VALUE, KEY, ERROR or DATE, error not larger than 0, unknown ERROR_MODE,
a date that cannot be read, DAYS, HOURS or RESTART, a key that the simulator does not write, a GENERAL_OBSERVATION with a missing
file, a bad value/error pair, or an index count that differs from the value count.
Warnings: duplicate key and date, a dense group of observations on one day,
a date outside the simulated period, a well observation while the well does not flow (HMQC-OBS-NOFLOW), a weight share outside the limits, zero values with a large error,
a HISTORY_OBSERVATION or another block type that this script cannot evaluate.
Exit code: 0 = no errors, 1 = errors, 2 = usage error (also a --case without summary data).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BLOCK_RE = re.compile(
    r"(?P<kind>[A-Z_]+_OBSERVATION)\s+(?P<name>[^\s{]+)\s*\{(?P<body>(?:[^{}]|\{[^{}]*\})*)\}\s*;?",
    re.S,
)
FIELD_RE = re.compile(r"(?P<field>[A-Z_]+)\s*=\s*(?P<value>[^;{}]+);")
ERROR_MODES = {"ABS", "REL", "RELMIN"}
LEGACY_TIME = ("DAYS", "HOURS", "RESTART")
DEFAULT_ERROR_MIN = 0.1
FLOW_VECTORS = ("WOPR", "WWPR", "WGPR", "WWIR", "WGIR")
CUMULATIVE_VECTORS = {"WOPT", "WWPT", "WGPT", "WLPT", "WWIT", "WGIT", "WVPT", "WVIT"}
PRESSURE_VECTORS = ("WBHP", "WTHP", "WBP")
RATIO_VECTORS = ("WWCT", "WGOR", "WGLR", "WOGR", "WWGR")
SCHEDULE_SCRIPTS = Path(__file__).resolve().parents[2] / "deck-scal-pvt-qc" / "scripts"


def strip_comments(text: str) -> str:
    """Remove '--' comments outside double quotes."""
    lines = []
    for line in text.splitlines():
        in_quotes = False
        cut = len(line)
        for index, char in enumerate(line):
            if char == '"':
                in_quotes = not in_quotes
            elif not in_quotes and line.startswith("--", index):
                cut = index
                break
        lines.append(line[:cut])
    return "\n".join(lines)


def parse_date(text: str) -> datetime | None:
    text = text.strip().strip('"')
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%d/%m/%Y")
    except ValueError:
        return None


def to_float(text: str | None) -> float | None:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_file(path: Path, *, strict: bool = False) -> list[dict]:
    """Return raw blocks; strict mode requires complete, supported summary syntax."""
    text = strip_comments(path.read_text(errors="replace"))
    records = []
    names: set[str] = set()
    end = 0
    line_starts = [m.start() for m in re.finditer(r"^", text, re.M)]
    for match in BLOCK_RE.finditer(text):
        where = f"{path}: {match['name']}"
        if strict:
            if text[end : match.start()].strip():
                raise ValueError(f"{where}: unmatched top-level observation syntax")
            if match["kind"] != "SUMMARY_OBSERVATION":
                raise ValueError(f"{where}: unsupported kind {match['kind']}")
            if match["name"] in names:
                raise ValueError(f"{where}: duplicate observation name {match['name']}")
            if "{" in match["body"] or "}" in match["body"]:
                raise ValueError(f"{where}: unsupported nested observation structure")
        fields = {}
        field_end = 0
        for field in FIELD_RE.finditer(match["body"]):
            name, value = field["field"], field["value"].strip()
            if strict:
                if match["body"][field_end : field.start()].strip():
                    raise ValueError(f"{where}: unmatched observation body syntax")
                if name in fields:
                    raise ValueError(f"{where}: duplicate field {name}")
                if name not in {"VALUE", "KEY", "ERROR", "DATE", "ERROR_MODE", "ERROR_MIN"}:
                    raise ValueError(f"{where}: unsupported field {name}")
                if "=" in value or (
                    '"' in value
                    and not (value.startswith('"') and value.endswith('"') and value.count('"') == 2)
                ):
                    raise ValueError(f"{where}: unsupported value for {name}: {value}")
            fields[name] = value
            field_end = field.end()
        if strict and match["body"][field_end:].strip():
            raise ValueError(f"{where}: unmatched observation body syntax")
        names.add(match["name"])
        end = match.end()
        line = sum(1 for start in line_starts if start <= match.start())
        records.append(
            {
                "file": str(path),
                "line": line,
                "kind": match["kind"],
                "name": match["name"],
                "fields": fields,
                "has_segments": "SEGMENT" in match["body"],
            }
        )
    if strict and text[end:].strip():
        raise ValueError(f"{path}: unmatched top-level observation syntax: {text[end:].strip()[:120]}")
    return records


def normalize_summary(record: dict) -> dict:
    """Compute the ERT error and collect problems for one SUMMARY_OBSERVATION."""
    fields = record["fields"]
    issues: list[dict] = []

    def add(severity: str, message: str) -> None:
        issues.append({"severity": severity, "message": message})

    for required in ("VALUE", "KEY", "ERROR"):
        if required not in fields:
            add("error", f"missing {required}")
    mode = fields.get("ERROR_MODE", "ABS").upper()
    if mode not in ERROR_MODES:
        add("error", f"unknown ERROR_MODE {fields.get('ERROR_MODE')}")
        mode = "ABS"
    value = to_float(fields.get("VALUE"))
    error_input = to_float(fields.get("ERROR"))
    error_min = to_float(fields.get("ERROR_MIN")) if "ERROR_MIN" in fields else DEFAULT_ERROR_MIN
    for name, number in (("VALUE", value), ("ERROR", error_input), ("ERROR_MIN", error_min)):
        if name in fields and (number is None or not math.isfinite(number)):
            add("error", f"{name} is not a number: {fields[name]}")

    error = None
    if value is not None and error_input is not None:
        if mode == "ABS":
            error = abs(error_input)
        elif mode == "REL":
            error = abs(value) * error_input
        else:
            error = max(abs(value) * error_input, error_min if error_min is not None else DEFAULT_ERROR_MIN)
        if not math.isfinite(error) or not error > 0:
            add("error", f"the error that ERT uses is {error}. It must be larger than 0")

    date = None
    legacy = [k for k in LEGACY_TIME if k in fields]
    if legacy:
        add(
            "error",
            f"uses {'/'.join(legacy)}. ERT 23 rejects it and needs DATE. Run 'ert convert_observations <config>'",
        )
    if "DATE" in fields:
        date = parse_date(fields["DATE"])
        if date is None:
            add("error", f"DATE cannot be read: {fields['DATE']}")
    elif not legacy:
        add("error", "missing DATE")

    return {
        "file": record["file"],
        "line": record["line"],
        "name": record["name"],
        "key": fields.get("KEY"),
        "value": value,
        "error_mode": mode,
        "error_input": error_input,
        "error": error,
        "date": date.isoformat() if date else None,
        "issues": issues,
    }


def summary_inventory(path: Path) -> dict[str, dict]:
    """Return complete OBS/KEY/DATE/OBSERVED/ERROR metadata, or raise ValueError."""
    inventory: dict[str, dict] = {}
    for record in parse_file(path, strict=True):
        obs = normalize_summary(record)
        errors = [issue["message"] for issue in obs["issues"] if issue["severity"] == "error"]
        if errors:
            raise ValueError(f"{path}: {obs['name']}: {'; '.join(errors)}")
        if not obs["key"] or not obs["key"].strip('"').strip():
            raise ValueError(f"{path}: {obs['name']}: empty KEY")
        date = parse_date(obs["date"])
        if date is None:
            raise ValueError(f"{path}: {obs['name']}: DATE cannot be read")
        canonical_date = date.isoformat()
        if date.tzinfo is None and not (date.hour or date.minute or date.second or date.microsecond):
            canonical_date = date.date().isoformat()
        inventory[obs["name"]] = {
            "OBS": obs["name"],
            "KEY": obs["key"],
            "DATE": canonical_date,
            "OBSERVED": obs["value"],
            "ERROR": obs["error"],
        }
    if not inventory:
        raise ValueError(f"{path}: no SUMMARY_OBSERVATION found")
    return inventory


def parse_index_list(text: str) -> list[int]:
    """Parse '0,3,5-7' into [0, 3, 5, 6, 7]. Raise ValueError for a bad item."""
    out: list[int] = []
    for part in text.replace(" ", "").split(","):
        first, sep, last = part.partition("-")
        if not first.isdigit() or (sep and not last.isdigit()):
            raise ValueError(f"'{part}' is not an index or a range a-b")
        low, high = int(first), int(last) if sep else int(first)
        if high < low:
            raise ValueError(f"range '{part}' ends before it starts")
        out.extend(range(low, high + 1))
    return out


def normalize_general(record: dict) -> dict:
    """Check one GENERAL_OBSERVATION: data, time, values and errors, and indices."""
    fields = record["fields"]
    folder = Path(record["file"]).parent
    issues: list[dict] = []

    def add(severity: str, message: str) -> None:
        issues.append({"severity": severity, "message": message})

    if "DATA" not in fields:
        add("error", "missing DATA (the GEN_DATA response)")
    report_step = None
    if "DATE" in fields:
        if parse_date(fields["DATE"]) is None:
            add("error", f"DATE cannot be read: {fields['DATE']}")
    elif "RESTART" in fields:
        restart = to_float(fields["RESTART"])
        if restart is None or not math.isfinite(restart) or restart < 0 or restart != int(restart):
            add("error", f"RESTART must be a report step (an integer >= 0): {fields['RESTART']}")
        else:
            report_step = int(restart)
    elif any(k in fields for k in ("DAYS", "HOURS")):
        add("warning", "uses DAYS or HOURS. ERT 23 needs RESTART or DATE. Run 'ert convert_observations'")
    else:
        add("error", "missing RESTART or DATE")

    values: list[float] = []
    errors: list[float] = []
    if "OBS_FILE" in fields and ("VALUE" in fields or "ERROR" in fields):
        add("error", "has OBS_FILE and VALUE/ERROR. Use one of them")
    elif "OBS_FILE" in fields:
        path = Path(fields["OBS_FILE"].strip().strip('"'))
        path = path if path.is_absolute() else folder / path
        if not path.is_file():
            add("error", f"OBS_FILE not found: {path}")
        else:
            tokens = strip_comments(path.read_text(errors="replace")).split()
            numbers = [to_float(t) for t in tokens]
            if any(n is None or not math.isfinite(n) for n in numbers):
                add("error", f"OBS_FILE {path.name} has a value that is not a number")
            elif len(numbers) % 2:
                add(
                    "error",
                    f"OBS_FILE {path.name} has {len(numbers)} numbers. It needs value and error pairs",
                )
            else:
                values, errors = numbers[0::2], numbers[1::2]
                if not values:
                    add("error", f"OBS_FILE {path.name} is empty")
    elif "VALUE" in fields and "ERROR" in fields:
        value, error = to_float(fields["VALUE"]), to_float(fields["ERROR"])
        if value is None or error is None or not math.isfinite(value) or not math.isfinite(error):
            add("error", "VALUE or ERROR is not a number")
        else:
            values, errors = [value], [error]
    else:
        add("error", "needs OBS_FILE, or VALUE and ERROR")
    bad = [i for i, e in enumerate(errors) if not e > 0]
    if bad:
        add("error", f"{len(bad)} error(s) not larger than 0, at pair(s) {bad[:10]}")

    indices = None
    if "INDEX_LIST" in fields and "INDEX_FILE" in fields:
        add("error", "has INDEX_LIST and INDEX_FILE. Use one of them")
    elif "INDEX_LIST" in fields:
        try:
            indices = parse_index_list(fields["INDEX_LIST"])
        except ValueError as error:
            add("error", f"INDEX_LIST: {error}")
    elif "INDEX_FILE" in fields:
        path = Path(fields["INDEX_FILE"].strip().strip('"'))
        path = path if path.is_absolute() else folder / path
        if not path.is_file():
            add("error", f"INDEX_FILE not found: {path}")
        else:
            tokens = strip_comments(path.read_text(errors="replace")).split()
            if not all(t.isdigit() for t in tokens):
                add("error", f"INDEX_FILE {path.name} must hold integers >= 0")
            else:
                indices = [int(t) for t in tokens]
    if indices is not None:
        if len(set(indices)) != len(indices):
            add("error", "the index list has duplicates")
        if values and len(indices) != len(values):
            add("error", f"{len(indices)} indices but {len(values)} values")

    return {
        "file": record["file"],
        "line": record["line"],
        "name": record["name"],
        "data": fields.get("DATA"),
        "time": fields.get("DATE") or fields.get("RESTART") or fields.get("DAYS") or fields.get("HOURS"),
        "report_step": report_step,
        "count": len(values),
        "values": values,
        "errors": errors,
        "indices": indices if indices is not None else list(range(len(values))),
        "index_source": "INDEX_LIST"
        if "INDEX_LIST" in fields
        else "INDEX_FILE"
        if "INDEX_FILE" in fields
        else "implicit 0..n-1",
        "issues": issues,
    }


def weight_groups(
    summary_obs: list[dict], general_obs: list[dict], gen_group: str = "family"
) -> list[tuple[str, str, int, float]]:
    """(group, key, count, sum of 1/error^2) for each usable observation."""
    items = []
    for obs in summary_obs:
        if obs["key"] and obs["error"] and math.isfinite(obs["error"]) and obs["error"] > 0:
            items.append((obs["key"].split(":", 1)[0], obs["key"], 1, 1.0 / obs["error"] ** 2))
    for obs in general_obs:
        usable = [e for e in obs["errors"] if e > 0]
        if obs["data"] and usable:
            data = obs["data"] if gen_group == "exact" else obs["data"].split("_", 1)[0]
            items.append((f"GEN:{data}", obs["name"], len(usable), sum(1.0 / e**2 for e in usable)))
    return items


def weight_balance(
    summary_obs: list[dict],
    general_obs: list[dict],
    share_min: float,
    share_max: float,
    gen_group: str = "family",
) -> tuple[dict, list[dict], list[str]]:
    """Count share and 1/error^2 for each group and key. Return the table, warnings, and proposals."""
    items = weight_groups(summary_obs, general_obs, gen_group)
    total = sum(count for _, _, count, _ in items)
    groups: dict[str, dict] = {}
    keys: dict[str, dict] = {}
    for group, key, count, inverse in items:
        for table, name in ((groups, group), (keys, key)):
            row = table.setdefault(name, {"count": 0, "inverse_variance_sum": 0.0})
            row["count"] += count
            row["inverse_variance_sum"] += inverse
    for table in (groups, keys):
        for row in table.values():
            row["count_share"] = row["count"] / total if total else None
    problems: list[dict] = []
    proposals: list[str] = []
    if len(groups) > 1:
        for name, row in sorted(groups.items()):
            share = row["count_share"]
            if share < share_min or share > share_max:
                side = "below" if share < share_min else "above"
                problems.append(
                    {
                        "severity": "warning",
                        "file": "",
                        "line": 0,
                        "message": (
                            f"group {name} has {row['count']} of {total} data points ({share:.0%}),"
                            f" {side} the limit {share_min:.0%}-{share_max:.0%}. The update can trade it"
                            f" against the other groups"
                        ),
                    }
                )
                proposals.append(
                    f"{name} ({share:.0%} of the data): check that this weight is intended. Options for the"
                    " user: review the errors of the group, or let ERT scale correlated observations"
                    " (ERT 23: ANALYSIS_SET_VAR OBSERVATIONS AUTO_SCALE <pattern>; check the ERT version)."
                    " Do not edit the observation file unless the user asks."
                )
    table = {
        "total_data_points": total,
        "groups": dict(sorted(groups.items(), key=lambda item: -item[1]["count"])),
        "keys": dict(sorted(keys.items(), key=lambda item: -item[1]["count"])),
        "limits": {"share_min": share_min, "share_max": share_max},
        "note": "count_share is the share of the misfit budget. inverse_variance_sum depends on the unit",
    }
    return table, problems, proposals


def zero_value_check(summary_obs: list[dict], ratio_limit: float) -> tuple[list[dict], list[str]]:
    """Warn about zero values whose ABS or RELMIN error is large against the signal of the key."""
    by_key: dict[str, list[dict]] = defaultdict(list)
    for obs in summary_obs:
        if obs["key"] and obs["value"] is not None and obs["error"]:
            by_key[obs["key"]].append(obs)
    problems: list[dict] = []
    proposals: list[str] = []
    for key, group in sorted(by_key.items()):
        signal = max(abs(o["value"]) for o in group)
        zeros = [o for o in group if o["value"] == 0 and o["error_mode"] in ("ABS", "RELMIN")]
        if not zeros:
            continue
        error = max(o["error"] for o in zeros)
        ratio = error / signal if signal > 0 else math.inf
        if ratio < ratio_limit:
            continue
        signal_text = (
            "all values of the key are 0" if signal == 0 else f"{ratio:.0%} of the largest |VALUE| {signal:g}"
        )
        problems.append(
            {
                "severity": "warning",
                "file": zeros[0]["file"],
                "line": zeros[0]["line"],
                "message": (
                    f"{len(zeros)} observations of {key} have VALUE 0 with error up to {error:g} ({signal_text})."
                    f" Each carries full weight. Examples: {[o['name'] for o in zeros[:5]]}"
                ),
            }
        )
        proposals.append(
            f"{key}: {len(zeros)} zero values. Options for the user: a smaller error before the signal starts,"
            " fewer early observations, or keep them on purpose. Do not edit the file unless the user asks."
        )
    return problems, proposals


def check_set(observations: list[dict], dense_n: int) -> list[dict]:
    """Checks across observations: duplicates and dense groups."""
    problems: list[dict] = []
    seen: dict[tuple, dict] = {}
    per_day: dict[tuple, list[dict]] = defaultdict(list)
    for obs in observations:
        if obs["key"] and obs["date"]:
            ident = (obs["key"], obs["date"])
            if ident in seen:
                first = seen[ident]
                problems.append(
                    {
                        "severity": "warning",
                        "file": obs["file"],
                        "line": obs["line"],
                        "message": (
                            f"{obs['name']} repeats key {obs['key']} at {obs['date']}"
                            f" ({first['name']}, line {first['line']}). ERT counts both"
                        ),
                    }
                )
            else:
                seen[ident] = obs
            per_day[(obs["key"], obs["date"][:10])].append(obs)
    for (key, day), group in sorted(per_day.items()):
        if len(group) >= dense_n:
            problems.append(
                {
                    "severity": "warning",
                    "file": group[0]["file"],
                    "line": group[0]["line"],
                    "message": (
                        f"{len(group)} observations of {key} on {day}. If their errors are independent,"
                        " this group weighs much more than a single observation"
                    ),
                }
            )
    return problems


def simulator_frame(case: Path):
    """Summary of one simulated case, one row for each time step. Needs res2df."""
    from res2df import ResdataFiles, summary

    frame = summary.df(ResdataFiles(str(case)), time_index="raw")
    if frame.empty:  # res2df logs a read error and returns an empty frame
        raise ValueError("no summary data. Give the .DATA file of a simulated case (the ECLBASE deck)")
    return frame


def no_flow_check(summary_obs: list[dict], frame) -> tuple[list[dict], list[str]]:
    """Warn about well observations at a date when the well has no flow in the case."""
    by_well: dict[str, list[dict]] = defaultdict(list)
    for obs in summary_obs:
        key = obs["key"] or ""
        if not (key.startswith("W") and ":" in key and obs["date"]):
            continue
        well = key.split(":", 1)[1]
        rates = [f"{family}:{well}" for family in FLOW_VECTORS if f"{family}:{well}" in frame.columns]
        rows = frame.index == datetime.fromisoformat(obs["date"])
        if rates and rows.any() and frame.loc[rows, rates].abs().to_numpy().sum() == 0:
            by_well[well].append(obs)
    problems: list[dict] = []
    proposals: list[str] = []
    for well, group in sorted(by_well.items()):
        problems.append(
            {
                "severity": "warning",
                "file": group[0]["file"],
                "line": group[0]["line"],
                "message": (
                    f"well {well} has no flow in the case at the date of {len(group)} observations."
                    f" They carry no reservoir information. Examples: {[o['name'] for o in group[:3]]}"
                ),
            }
        )
        proposals.append(
            f"{well}: {len(group)} observations at dates with no flow. Options for the user: remove them,"
            " or keep them on purpose. Do not edit the observation file unless the user asks."
        )
    return problems, proposals


def schedule_timeline(deck: Path) -> dict:
    """Flow state of each well over time, from the SCHEDULE of a deck. Needs res2df."""
    if str(SCHEDULE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCHEDULE_SCRIPTS))
    from res2df import ResdataFiles
    from schedule_qc import well_timeline

    return well_timeline(ResdataFiles(str(deck)).get_deck())


def schedule_no_flow_check(summary_obs: list[dict], timeline: dict) -> tuple[list[dict], list[str]]:
    """HMQC-OBS-NOFLOW: a well observation in a report step when the deck SCHEDULE does not let the well flow."""
    from schedule_qc import well_state

    problems: list[dict] = []
    by_well: dict[str, list[str]] = defaultdict(list)
    for obs in summary_obs:
        key = obs["key"] or ""
        mnemonic, _, well = key.partition(":")
        if (
            not (mnemonic.startswith("W") and well and obs["date"])
            or mnemonic.rstrip("H") in CUMULATIVE_VECTORS
        ):
            continue
        date = datetime.fromisoformat(obs["date"])
        state = well_state(timeline, well, date.date())
        if state is None:
            continue
        if mnemonic.startswith(PRESSURE_VECTORS):
            meaning = "The pressure is a shut-in pressure, not a flowing pressure"
        elif mnemonic.startswith(RATIO_VECTORS):
            meaning = "A ratio of zero rates is noise"
        else:
            meaning = "The value carries no reservoir information"
        problems.append(
            {
                "severity": "warning",
                "code": "HMQC-OBS-NOFLOW",
                "file": obs["file"],
                "line": obs["line"],
                "message": f"{obs['name']}: well {well} {state} at {date.date()} in the deck SCHEDULE. {meaning}",
            }
        )
        by_well[well].append(obs["name"])
    proposals = [
        f"{well}: {len(names)} observations while the well does not flow in the deck SCHEDULE. Options for the"
        " user: remove them, or keep them on purpose. Do not edit the observation file unless the user asks."
        for well, names in sorted(by_well.items())
    ]
    return problems, proposals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--case", type=Path, default=None, help="A .DATA file of one simulated case")
    parser.add_argument(
        "--deck",
        type=Path,
        default=None,
        help="A .DATA file. Its SCHEDULE gives when each well flows (res2df)",
    )
    parser.add_argument(
        "--dense-n", type=int, default=10, help="Warn at this many observations of one key on one day"
    )
    parser.add_argument(
        "--share-min", type=float, default=0.15, help="Warn when a group has less than this share of the data"
    )
    parser.add_argument(
        "--share-max", type=float, default=0.5, help="Warn when a group has more than this share of the data"
    )
    parser.add_argument(
        "--zero-error-ratio",
        type=float,
        default=0.05,
        help="Warn about zero values when error / largest |VALUE| of the key is at least this",
    )
    parser.add_argument(
        "--gen-group",
        choices=("family", "exact"),
        default="family",
        help="Group GEN data by the DATA family (RFT_R_A2 -> GEN:RFT) or by the exact DATA name",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    for name in ("share_min", "share_max", "zero_error_ratio"):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0 or (name != "zero_error_ratio" and value > 1):
            print(f"ERROR: --{name.replace('_', '-')} is out of range: {value}", file=sys.stderr)
            return 2
    if args.share_min > args.share_max:
        print("ERROR: --share-min is larger than --share-max", file=sys.stderr)
        return 2
    for path in args.files:
        if not path.is_file():
            print(f"ERROR: not a file: {path}", file=sys.stderr)
            return 2

    summary_obs: list[dict] = []
    general_obs: list[dict] = []
    other: list[dict] = []
    problems: list[dict] = []
    for path in args.files:
        for record in parse_file(path):
            if record["kind"] in ("SUMMARY_OBSERVATION", "GENERAL_OBSERVATION"):
                general = record["kind"] == "GENERAL_OBSERVATION"
                obs = normalize_general(record) if general else normalize_summary(record)
                (general_obs if general else summary_obs).append(obs)
                for issue in obs["issues"]:
                    problems.append(
                        {
                            **issue,
                            "file": obs["file"],
                            "line": obs["line"],
                            "message": f"{obs['name']}: {issue['message']}",
                        }
                    )
                continue
            other.append(
                {
                    "kind": record["kind"],
                    "name": record["name"],
                    "file": record["file"],
                    "line": record["line"],
                }
            )
            if record["kind"] == "HISTORY_OBSERVATION":
                message = (
                    "HISTORY_OBSERVATION needs the history vectors (<KEY>H) of the reference case."
                    " This script does not evaluate it. Recent ERT versions do not accept it: 'ert"
                    " convert_observations <config>' can write SUMMARY_OBSERVATION blocks with dates."
                    " Check this with the ERT version of the project"
                )
            else:
                message = f"{record['kind']} is not evaluated by this script. Check it by hand"
            problems.append(
                {
                    "severity": "warning",
                    "file": record["file"],
                    "line": record["line"],
                    "message": f"{record['name']}: {message}",
                }
            )
    if not summary_obs and not general_obs and not other:
        print("ERROR: no observation block found", file=sys.stderr)
        return 2
    problems += check_set(summary_obs, args.dense_n)
    weights, weight_problems, proposals = weight_balance(
        summary_obs, general_obs, args.share_min, args.share_max, args.gen_group
    )
    zero_problems, zero_proposals = zero_value_check(summary_obs, args.zero_error_ratio)
    problems += weight_problems + zero_problems
    proposals += zero_proposals

    window = None
    if args.case is not None:
        if not args.case.is_file():
            print(f"ERROR: --case is not a file: {args.case}", file=sys.stderr)
            return 2
        try:
            frame = simulator_frame(args.case)
        except Exception as error:  # res2df raises many types. A bad --case is an input error, not a finding
            print(f"ERROR: --case {args.case}: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
        keys, first, last = (
            set(frame.columns),
            frame.index.min().to_pydatetime(),
            frame.index.max().to_pydatetime(),
        )
        window = (first.isoformat(), last.isoformat())
        flow_problems, flow_proposals = no_flow_check(summary_obs, frame)
        problems += flow_problems
        proposals += flow_proposals
        for obs in summary_obs:
            where = {"file": obs["file"], "line": obs["line"]}
            if obs["key"] and obs["key"] not in keys:
                problems.append(
                    {
                        "severity": "error",
                        **where,
                        "message": f"{obs['name']}: key {obs['key']} is not in the simulator summary",
                    }
                )
            if obs["date"] and not (first <= datetime.fromisoformat(obs["date"]) <= last):
                problems.append(
                    {
                        "severity": "warning",
                        **where,
                        "message": f"{obs['name']}: date {obs['date']} is outside the simulated period {window[0]} to {window[1]}",
                    }
                )

    if args.deck is not None:
        if not args.deck.is_file():
            print(f"ERROR: --deck is not a file: {args.deck}", file=sys.stderr)
            return 2
        try:
            timeline = schedule_timeline(args.deck)
        except ImportError as error:
            print(f"ERROR: --deck needs res2df: {error}", file=sys.stderr)
            return 2
        except Exception as error:  # opm and res2df raise many types. A bad --deck is an input error
            print(f"ERROR: --deck {args.deck}: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
        deck_problems, deck_proposals = schedule_no_flow_check(summary_obs, timeline)
        problems += deck_problems
        proposals += deck_proposals

    by_key = Counter(obs["key"] for obs in summary_obs)
    errors = [p for p in problems if p["severity"] == "error"]
    result = {
        "summary_observations": len(summary_obs),
        "general_observations": [
            {k: v for k, v in obs.items() if k not in ("values", "errors", "indices")} for obs in general_obs
        ],
        "other_blocks": other,
        "observations_per_key": dict(sorted(by_key.items(), key=lambda item: -item[1])),
        "simulated_period": window,
        "weights": weights,
        "proposals": proposals,
        "problems": problems,
    }
    if args.json:
        result["observations"] = summary_obs
        print(json.dumps(result, indent=2, default=str))
    else:
        print(
            f"Summary observations: {len(summary_obs)} in {len(args.files)} file(s)."
            f" General observations: {len(general_obs)}. Other blocks: {len(other)}"
        )
        for key, count in list(result["observations_per_key"].items())[:15]:
            print(f"  {count:>5}  {key}")
        for obs in general_obs:
            print(
                f"  GENERAL {obs['name']}: DATA {obs['data']}, {obs['count']} values, index {obs['index_source']}"
            )
        print(
            f"Weight balance ({weights['total_data_points']} data points). group, count, share, sum 1/error^2:"
        )
        for name, row in weights["groups"].items():
            print(
                f"  {name:<20} {row['count']:>5}  {row['count_share']:>5.0%}  {row['inverse_variance_sum']:.4g}"
            )
        for p in problems:
            where = f"{Path(p['file']).name}:{p['line']}" if p["file"] else "all files"
            code = f" {p['code']}" if "code" in p else ""
            print(f"{p['severity']:<8}{code} {where}: {p['message']}")
        for text in proposals:
            print(f"PROPOSAL {text}")
        print(f"{len(errors)} errors, {len(problems) - len(errors)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
