#!/usr/bin/env python3
"""Read the ERT update log and report what the update used. Read-only. Standard library only.

ERT writes one folder for each update step under UPDATE_LOG_PATH (or its default update_log folder).
Each folder holds Report.csv (one row per observation) and Report.report (a header and the same table).
The header gives the parent and target ensembles, Alpha (ENKF_ALPHA), Global scaling (the ES-MDA
inflation of the step), the standard cutoff (STD_CUTOFF), and the counts of deactivated observations.

For each step the script reports:
    active, outlier, std_cutoff, missing_response and other counts, from the table and from the header
    alpha, global scaling, standard cutoff
    response_std / observation error, for each observation and its group
    the names of the deactivated observations, with their status
The group of an observation is its name without a trailing _<date> or _<number> suffix:
WBHP_A1_20180401 -> WBHP_A1, FOPR_01 -> FOPR. A name without that suffix is its own group:
RFT_R_A2 -> RFT_R_A2 (a GENERAL_OBSERVATION has one row per data point, all with the same name).
With more than one step it reports the sum of 1/global scaling. ES-MDA expects 1.
HMQC-EARLY-SPREAD-LOSS: the response_std in a step is the spread of the parent ensemble. When the parent of a step is
the result of update 1 or 2 and its median response_std / error is below --early-spread-limit (0.3), the spread
collapses early. Read this finding before the last iteration starts. The update count of the parent comes from the
iteration number in its name, or from a chain of steps (parent -> target) back to a numbered ensemble. When neither
gives it, the report says that the count is unknown and the finding is not checked.

Usage:
    update_log_report.py <update_log folder | step folder | Report.csv | Report.report> [...] \
        [--ratio-low 0.1] [--early-spread-limit 0.3] [--json]

The column names vary between ERT versions. The script accepts common variants (for example
observation_key or key, std or error, response_std or simulated_std). A file without a key or a status
column is an error. An unknown status is counted as "other" and reported.
A low response_std / error ratio means that the ensemble cannot move much at this observation:
the update gives it little weight even when it is active.
Exit code: 0 = read, 1 = a file cannot be used, 2 = usage error (no update log found).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from pathlib import Path

COLUMN_ALIASES = {
    "key": ("observation_key", "obs_key", "key", "observation", "obs_name", "name"),
    "index": ("index", "date", "time", "obs_index"),
    "value": ("observations", "observation_value", "obs_value", "observed", "value"),
    "error": ("std", "obs_std", "error", "obs_error"),
    "scaled_error": ("scaled_obs_error", "scaled_error", "scaled_std"),
    "scaling": ("obs_error_scaling", "error_scaling", "scaling"),
    "response_mean": ("response_mean", "simulated_mean", "ens_mean", "mean"),
    "response_std": ("response_std", "simulated_std", "ens_std", "ensemble_std"),
    "status": ("status", "active"),
    "missing": ("missing_realizations",),
}
HEADER_FIELDS = {
    "parent ensemble": "parent",
    "target ensemble": "target",
    "alpha": "alpha",
    "global scaling": "global_scaling",
    "standard cutoff": "std_cutoff",
    "active observations": "header_active",
}
GROUP_RE = re.compile(r"_\d+$")
GEN_INDEX_RE = re.compile(r"^\s*\d+\s*,\s*(\d+)\s*$")


def normalize_name(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def status_class(status: str) -> str:
    """Map an ERT status text to active, outlier, std_cutoff, missing_response or other.

    ERT 23 (ert/analysis/snapshots.py) writes 'Active', 'Deactivated, outlier', 'collapsed' (ensemble std below
    STD_CUTOFF) and 'nan' (missing response). Other versions write longer texts. Both forms are accepted.
    """
    text = status.strip().lower()
    if text in {"active", "1", "true", "yes"}:
        return "active"
    if "outlier" in text:
        return "outlier"
    if "collapsed" in text or "std" in text or "cutoff" in text:
        return "std_cutoff"
    if text in {"nan", "none", ""} or "missing" in text:
        return "missing_response"
    return "other"


def obs_group(name: str) -> str:
    """The name without a trailing _<date> or _<number>: WBHP_A1_20180401 -> WBHP_A1, RFT_R_A2 -> RFT_R_A2."""
    return GROUP_RE.sub("", name) or name


def row_names(row: dict) -> list[str]:
    """The key, and <key>:<index> for a GEN_DATA row whose index is '<report step>, <index>'."""
    match = GEN_INDEX_RE.match(row["index"])
    return [row["key"], f"{row['key']}:{match.group(1)}"] if match else [row["key"]]


def to_float(text) -> float | None:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def map_columns(header: list[str]) -> dict[str, int]:
    names = [normalize_name(h) for h in header]
    mapping: dict[str, int] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in names:
                mapping[field] = names.index(alias)
                break
    return mapping


def rows_from_table(header: list[str], body: list[list[str]], source: Path) -> list[dict]:
    columns = map_columns(header)
    missing = [field for field in ("key", "status") if field not in columns]
    if missing:
        raise ValueError(f"{source}: no {' or '.join(missing)} column in {header}")
    rows = []
    for cells in body:
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(header):
            raise ValueError(f"{source}: row has {len(cells)} cells but the header has {len(header)}")

        def get(field: str, cells=cells) -> str:
            index = columns.get(field)
            return cells[index].strip() if index is not None and index < len(cells) else ""

        if not get("key"):
            raise ValueError(f"{source}: empty observation key")
        rows.append(
            {
                "key": get("key"),
                "index": get("index"),
                "value": to_float(get("value")),
                "error": to_float(get("error")),
                "scaled_error": to_float(get("scaled_error")),
                "scaling": to_float(get("scaling")),
                "response_mean": to_float(get("response_mean")),
                "response_std": to_float(get("response_std")),
                "status": get("status"),
                "missing_realizations": get("missing"),
            }
        )
    return rows


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        table = list(csv.reader(handle))
    if not table:
        raise ValueError(f"{path}: empty file")
    return rows_from_table(table[0], table[1:], path)


def read_report(path: Path) -> tuple[dict, list[dict]]:
    """Header fields and table rows of a Report.report text file."""
    header: dict = {"deactivated_header": {}}
    table_lines: list[list[str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] in "│|":
            cells = [c.strip() for c in re.split(r"[│|]", stripped)[1:-1]]
            table_lines.append(cells)
            continue
        if stripped[0] in "┌├└+-=":
            continue
        name, sep, value = stripped.partition(":")
        if not sep:
            continue
        lowered = name.strip().lower()
        if lowered == "deactivated observations":
            reason, _, count = value.rpartition(":")
            number = to_float(count)
            if number is not None:
                header["deactivated_header"][status_class(reason)] = int(number)
        elif lowered in HEADER_FIELDS:
            field = HEADER_FIELDS[lowered]
            number = to_float(value)
            header[field] = value.strip() if field in {"parent", "target"} or number is None else number
    rows = rows_from_table(table_lines[0], table_lines[1:], path) if table_lines else []
    return header, rows


def iteration_number(name: str | None) -> int | None:
    match = re.search(r"(\d+)\s*$", name or "")
    return int(match.group(1)) if match else None


def read_step(folder: Path) -> dict:
    """One update step from a folder that holds Report.csv and/or Report.report."""
    csv_file, report_file = folder / "Report.csv", folder / "Report.report"
    header: dict = {"deactivated_header": {}}
    rows: list[dict] = []
    sources = []
    if report_file.is_file():
        header, rows = read_report(report_file)
        sources.append(str(report_file))
    if csv_file.is_file():
        rows = read_csv(csv_file)  # the CSV has full precision
        sources.append(str(csv_file))
    if not sources:
        raise ValueError(f"{folder}: no Report.csv or Report.report")
    return {"folder": str(folder), "sources": sources, **header, "rows": rows}


def summarize_step(step: dict, ratio_low: float) -> dict:
    rows = step["rows"]
    counts = {name: 0 for name in ("active", "outlier", "std_cutoff", "missing_response", "other")}
    deactivated = []
    unknown_status = set()
    ratios = []
    low_active = []
    groups: dict[str, dict] = {}
    for row in rows:
        kind = status_class(row["status"])
        counts[kind] += 1
        if kind == "other":
            unknown_status.add(row["status"])
        error = row["scaled_error"] if row["scaled_error"] else row["error"]
        ratio = row["response_std"] / error if error and row["response_std"] is not None else None
        row["class"] = kind
        row["std_error_ratio"] = ratio
        group = groups.setdefault(
            obs_group(row["key"]), {"count": 0, "active": 0, "deactivated": 0, "ratios": []}
        )
        group["count"] += 1
        if kind == "active":
            group["active"] += 1
            if ratio is not None:
                ratios.append(ratio)
                group["ratios"].append(ratio)
                if ratio < ratio_low:
                    low_active.append(row)
        else:
            group["deactivated"] += 1
            deactivated.append(
                {
                    "key": row["key"],
                    "index": row["index"],
                    "status": row["status"],
                    "value": row["value"],
                    "error": error,
                    "response_mean": row["response_mean"],
                    "response_std": row["response_std"],
                }
            )
    group_table = {
        name: {
            "count": g["count"],
            "active": g["active"],
            "deactivated": g["deactivated"],
            "share_of_active": (g["active"] / counts["active"]) if counts["active"] else None,
            "median_std_error_ratio": statistics.median(g["ratios"]) if g["ratios"] else None,
        }
        for name, g in sorted(groups.items())
    }
    notes = []
    if unknown_status:
        notes.append(f"unknown status text: {sorted(unknown_status)}")
    header_counts = step.get("deactivated_header", {})
    for kind, number in header_counts.items():
        if kind in counts and counts[kind] != number:
            notes.append(f"header says {number} {kind}, the table has {counts[kind]}")
    if "header_active" in step and step["header_active"] != counts["active"]:
        notes.append(f"header says {int(step['header_active'])} active, the table has {counts['active']}")
    if not rows:
        if "header_active" not in step:
            raise ValueError(f"{step['folder']}: no observation rows or active count")
        counts["active"] = int(step["header_active"])
        counts.update({k: v for k, v in header_counts.items() if k in counts})
        notes = ["the step has no observation rows; counts come from the header"]
    return {
        "folder": step["folder"],
        "sources": step["sources"],
        "parent": step.get("parent"),
        "target": step.get("target"),
        "alpha": step.get("alpha"),
        "global_scaling": step.get("global_scaling"),
        "std_cutoff": step.get("std_cutoff"),
        "observations": len(rows) if rows else sum(counts.values()),
        "counts": counts,
        "std_error_ratio": {
            "median": statistics.median(ratios) if ratios else None,
            "min": min(ratios) if ratios else None,
            "max": max(ratios) if ratios else None,
            "active_below_limit": len(low_active),
        },
        "low_ratio_active": [
            {"key": r["key"], "index": r["index"], "std_error_ratio": r["std_error_ratio"]}
            for r in sorted(low_active, key=lambda r: r["std_error_ratio"])
        ],
        "deactivated": deactivated,
        "groups": group_table,
        "notes": notes,
        "by_key": {
            name: {"class": r["class"], "status": r["status"], "std_error_ratio": r["std_error_ratio"]}
            for r in rows
            for name in row_names(r)
        },
    }


def find_step_folders(path: Path) -> list[Path]:
    if path.is_file():
        return [path.parent] if path.name in {"Report.csv", "Report.report"} else []
    if (path / "Report.csv").is_file() or (path / "Report.report").is_file():
        return [path]
    found = {p.parent for pattern in ("*/Report.csv", "*/Report.report") for p in path.glob(pattern)}
    return sorted(found)


def updates_before(step: dict, steps: list[dict]) -> int | None:
    """How many updates made the parent ensemble of the step, or None when unknown.

    A parent with an iteration number (iter-2, 2) gives the count. Otherwise follow the steps back from target to
    parent until an ensemble with an iteration number. The list position is never used: a broken or partial chain
    gives None.
    """
    by_target = {s["target"]: s for s in steps if s["target"] is not None}
    hops, name, seen = 0, step["parent"], set()
    while name is not None and name not in seen:
        number = iteration_number(name)
        if number is not None:
            return number + hops
        seen.add(name)
        previous = by_target.get(name)
        if previous is None:
            return None
        hops, name = hops + 1, previous["parent"]
    return None


def early_spread_findings(steps: list[dict], limit: float) -> tuple[list[dict], list[str]]:
    """HMQC-EARLY-SPREAD-LOSS when the ensemble after update 1 or 2 has a median response_std / error below limit.

    Also return the steps whose update count is unknown. These are not checked.
    """
    chain = [s["std_error_ratio"]["median"] for s in steps]
    trail = " -> ".join(fmt(value) for value in chain)
    found, unknown = [], []
    for step in steps:
        median = step["std_error_ratio"]["median"]
        after = updates_before(step, steps)
        if after is None:
            unknown.append(f"update {step['parent']} -> {step['target']}")
            continue
        if found or after not in (1, 2) or median is None or median >= limit:
            continue
        found.append(
            {
                "code": "HMQC-EARLY-SPREAD-LOSS",
                "ensemble": f"update {step['parent']} -> {step['target']}",
                "finding": "response spread collapses early",
                "detail": (
                    f"median response_std/error {median:.3g} in ensemble {step['parent']} (after update {after}) is"
                    f" below {limit:g}. Medians by step: {trail}. The later updates can move the responses only a"
                    " little. Read this before the last iteration: check the observation errors, the inflation, and"
                    " the localization. Limiter: the error is the scaled (inflated) error of the step, and an"
                    " ensemble that already matches precise data can have a small ratio for a good reason"
                ),
                "examples": [],
            }
        )
    return found, unknown


def load(paths: list[Path], ratio_low: float = 0.1, early_spread_limit: float = 0.3) -> dict:
    """Read and summarize all steps. Raise FileNotFoundError when no step is found, ValueError for a bad file."""
    folders: list[Path] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"not found: {path}")
        hits = find_step_folders(path)
        if not hits:
            raise FileNotFoundError(f"no Report.csv or Report.report in {path} or its subfolders")
        folders += [h for h in hits if h not in folders]
    steps = [summarize_step(read_step(folder), ratio_low) for folder in folders]
    steps.sort(key=lambda s: (iteration_number(s["target"]) is None, iteration_number(s["target"]) or 0))
    scalings = [s["global_scaling"] for s in steps if isinstance(s["global_scaling"], float)]
    chain = all(
        a["target"] is not None and a["target"] == b["parent"] for a, b in zip(steps, steps[1:], strict=False)
    )
    esmda = None
    if len(steps) > 1 and not chain:
        esmda = {"chain": False, "note": "the steps do not form one chain of iterations. No ES-MDA check"}
    elif len(steps) > 1 and len(scalings) == len(steps) and all(a > 0 for a in scalings):
        inverse_sum = sum(1.0 / a for a in scalings)
        esmda = {
            "chain": True,
            "global_scaling": scalings,
            "sum_inverse": inverse_sum,
            "consistent": abs(inverse_sum - 1.0) <= 1e-3,
        }
    found, unknown = early_spread_findings(steps, early_spread_limit)
    return {
        "steps": steps,
        "es_mda": esmda,
        "ratio_low": ratio_low,
        "early_spread_limit": early_spread_limit,
        "findings": found,
        "early_spread_unknown": unknown,
    }


def obs_status(report: dict) -> dict[str, dict]:
    """Status of each observation name in the last step that has it."""
    status: dict[str, dict] = {}
    for step in report["steps"]:
        for key, item in step["by_key"].items():
            status[key] = {**item, "target": step["target"]}
    return status


def join_findings(report: dict, found: list[dict]) -> list[dict]:
    """Add update-log facts to misfit_report findings. Return the new findings to append.

    The existing findings get an 'update_log' field for the observations that they name.
    """
    status = obs_status(report)
    for item in found:
        notes = {}
        for example in item.get("examples", []):
            name = str(example).split(": ", 1)[0].strip()
            name = name if name in status else name.split(":", 1)[0]
            if name in status:
                info = status[name]
                ratio = info["std_error_ratio"]
                notes[name] = info["status"] + (
                    f", response_std/error {ratio:.3g}" if ratio is not None else ""
                )
        if notes:
            item["update_log"] = notes
    extra = [dict(item) for item in report.get("findings", [])]
    if report.get("early_spread_unknown"):
        extra.append(
            {
                "ensemble": "update log",
                "finding": "update count unknown",
                "detail": (
                    "HMQC-EARLY-SPREAD-LOSS not checked: no iteration number and no chain of steps back to a"
                    " numbered ensemble"
                ),
                "examples": report["early_spread_unknown"][:5],
            }
        )
    for step in report["steps"]:
        label = f"update {step['parent']} -> {step['target']}"
        if step["deactivated"]:
            extra.append(
                {
                    "ensemble": label,
                    "finding": "observation deactivated in the update",
                    "detail": f"{len(step['deactivated'])} of {step['observations']} observations were not used ({step['counts']})",
                    "examples": [f"{d['key']}: {d['status']}" for d in step["deactivated"][:5]],
                }
            )
        if step["low_ratio_active"]:
            extra.append(
                {
                    "ensemble": label,
                    "finding": "active observation with small ensemble spread",
                    "detail": (
                        f"{len(step['low_ratio_active'])} active observations have response_std/error below"
                        f" {report['ratio_low']}. The update can move them only a little"
                    ),
                    "examples": [r["key"] for r in step["low_ratio_active"][:5]],
                }
            )
    if report["es_mda"] and report["es_mda"]["chain"] and not report["es_mda"]["consistent"]:
        extra.append(
            {
                "ensemble": "update log",
                "finding": "ES-MDA inflation does not sum to 1",
                "detail": f"sum of 1/global scaling is {report['es_mda']['sum_inverse']:.4g} over {len(report['steps'])} steps",
                "examples": [],
            }
        )
    return extra


def compact(report: dict) -> dict:
    """The report without the large per-key table."""
    return {**report, "steps": [{k: v for k, v in s.items() if k != "by_key"} for s in report["steps"]]}


def fmt(value) -> str:
    return "-" if value is None else f"{value:.3g}" if isinstance(value, float) else str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--ratio-low",
        type=float,
        default=0.1,
        help="Report active observations with response_std/error below this value",
    )
    parser.add_argument(
        "--early-spread-limit",
        type=float,
        default=0.3,
        help="HMQC-EARLY-SPREAD-LOSS: median response_std/error after update 1 or 2 below this value",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    for name in ("ratio_low", "early_spread_limit"):
        value = getattr(args, name)
        if not math.isfinite(value) or value < 0:
            print(f"ERROR: --{name.replace('_', '-')} must be finite and nonnegative", file=sys.stderr)
            return 2
    try:
        report = load(args.paths, args.ratio_low, args.early_spread_limit)
    except FileNotFoundError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, IndexError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(compact(report), indent=2))
        return 0
    for step in report["steps"]:
        counts = step["counts"]
        ratio = step["std_error_ratio"]
        print(f"Update {step['parent']} -> {step['target']}  ({Path(step['folder']).name})")
        print(
            f"  alpha {fmt(step['alpha'])}, global scaling {fmt(step['global_scaling'])},"
            f" std cutoff {fmt(step['std_cutoff'])}"
        )
        print(
            f"  {step['observations']} observations: {counts['active']} active, {counts['outlier']} outlier,"
            f" {counts['std_cutoff']} std_cutoff, {counts['missing_response']} missing response, {counts['other']} other"
        )
        print(
            f"  response_std/error of active observations: median {fmt(ratio['median'])},"
            f" min {fmt(ratio['min'])}, {ratio['active_below_limit']} below {args.ratio_low}"
        )
        print("  group                      count  active  share  median std/error")
        for name, group in step["groups"].items():
            share = group["share_of_active"]
            print(
                f"  {name:<26} {group['count']:>5}  {group['active']:>6}  {'-' if share is None else f'{share:.0%}':>5}"
                f"  {fmt(group['median_std_error_ratio'])}"
            )
        for item in step["deactivated"]:
            print(f"  DEACTIVATED {item['key']} {item['index']}: {item['status']}")
        for item in step["low_ratio_active"][:10]:
            print(f"  LOW RATIO   {item['key']} {item['index']}: {item['std_error_ratio']:.3g}")
        for note in step["notes"]:
            print(f"  NOTE {note}")
    esmda = report["es_mda"]
    if esmda and not esmda["chain"]:
        print(f"NOTE {esmda['note']}")
    elif esmda:
        verdict = "as ES-MDA expects" if esmda["consistent"] else "ES-MDA expects 1"
        print(
            f"Sum of 1/global scaling over {len(report['steps'])} steps: {esmda['sum_inverse']:.4g} ({verdict})"
        )
    for item in report["findings"]:
        print(f"FINDING {item['code']} {item['finding']}: {item['detail']}")
    if report["early_spread_unknown"]:
        print(
            "NOTE HMQC-EARLY-SPREAD-LOSS not checked for "
            + ", ".join(report["early_spread_unknown"])
            + ": the update count of the parent is unknown (no iteration number in the name and no chain of steps"
            " back to a numbered ensemble)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
