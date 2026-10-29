#!/usr/bin/env python3
"""Summarise observed PRT text, without inferring run success."""

import argparse
import json
import re
import sys
from pathlib import Path

SUMMARY_COUNTS = ("Warnings", "Errors", "Bugs", "Problems")


def diagnose(text):
    result = dict(
        report_steps=[],
        dates=[],
        chops=[],
        errors=[],
        newton_iterations=[],
        linear_iterations=[],
        last_date=None,
        error_summary=None,
        wells_shut_by_simulator=[],
        well_control_mode_changes=[],
    )
    step = None
    date = None
    in_summary = False
    report = dict(report_step=None, report_day=None, report_date=None)
    time_step = dict(time_step=None, time_step_day=None, time_step_date=None)
    changes = {}
    for number, line in enumerate(text.splitlines(), 1):
        if in_summary:
            match = re.fullmatch(r"\s*([A-Za-z]+)\s+(\d+)\s*", line)
            if match:
                if match[1] in SUMMARY_COUNTS:
                    result["error_summary"][match[1]] = int(match[2])
                continue
            in_summary = False
        if re.match(r"\s*Error summary:", line, re.I):
            in_summary = True
            result["error_summary"] = {}
            continue
        match = re.search(r"report\s+step\s*[:=]?\s*(\d+)", line, re.I)
        if match:
            step = int(match[1])
            result["report_steps"].append(step)
        match = re.search(r"Report step (\d+)/\d+ at day ([\d.]+)/[\d.]+, date = (\S+)", line)
        if match:
            report = dict(report_step=int(match[1]), report_day=float(match[2]), report_date=match[3])
            time_step = dict(time_step=None, time_step_day=None, time_step_date=None)
        match = re.search(r"Starting time step (\d+), .*at day ([\d.]+)/[\d.]+, date = (\S+)", line)
        if match:
            time_step = dict(time_step=int(match[1]), time_step_day=float(match[2]), time_step_date=match[3])
        match = re.search(r"Well (\S+) will be shut as it can not operate", line)
        if match:
            result["wells_shut_by_simulator"].append(dict(line=number, well=match[1], **report, **time_step))
        match = re.search(r"Well (\S+) control mode changed from (\S+) to (\S+)", line)
        if match:
            key = (match[1], f"{match[2]}->{match[3]}")
            if key not in changes:
                first_date = time_step["time_step_date"] or report["report_date"]
                changes[key] = dict(
                    well=key[0], transition=key[1], count=0, first_line=number, first_date=first_date
                )
            changes[key]["count"] += 1
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[- /][A-Za-z]{3}[- /]\d{4}\b", line)
        if match:
            date = match[0]
            result["dates"].append(date)
            result["last_date"] = date
        if re.search(r"Timestep chopped|Problem:", line, re.I):
            result["chops"].append(dict(line=number, report_step=step, date=date, text=line.strip()))
        if not re.match(r"\s*Warning:", line, re.I) and re.search(r"\b(error|fatal|exception)\b", line, re.I):
            result["errors"].append(dict(line=number, text=line.strip()))
        for label in ("newton", "linear"):
            match = re.search(rf"{label}\s+iterations?\s*[:=]\s*(\d+)", line, re.I)
            if match:
                result[f"{label}_iterations"].append(int(match[1]))
    result["last_report_step"] = step
    result["well_control_mode_changes"] = list(changes.values())
    summary = result["error_summary"]
    if summary and ("Errors" in summary or "Bugs" in summary):
        result["error_count"] = summary.get("Errors", 0) + summary.get("Bugs", 0)
    else:
        result["error_count"] = len(result["errors"])
    result["limits"] = (
        "Dates and report steps are log markers, not proof of completion. The error count is Errors plus Bugs from the final Error summary when the log has one; otherwise it is the count of error lines. Problem lines may overlap chop messages. Iterations are only explicit labelled counts; missing counts remain absent. Wells shut by the simulator and control mode changes are findings, not errors."
    )
    return result


def markdown(result):
    lines = [
        "# Flow log diagnosis",
        f"Last date marker: {result['last_date']}",
        f"Last report step marker: {result['last_report_step']}",
        f"Chop/problem messages: {len(result['chops'])}; errors: {result['error_count']}",
        f"Newton counts: {result['newton_iterations']}",
        f"Linear counts: {result['linear_iterations']}",
    ]
    if result["error_summary"] is not None:
        counts = ", ".join(f"{k} {v}" for k, v in result["error_summary"].items())
        lines.append(f"Error summary: {counts}")
    lines += [
        f"- Line {r['line']}, date {r['date']}, step {r['report_step']}: {r['text']}" for r in result["chops"]
    ]
    lines += [f"- Line {r['line']}: {r['text']}" for r in result["errors"]]
    lines.append(f"Wells shut by the simulator: {len(result['wells_shut_by_simulator'])}")
    lines += [
        f"- Line {r['line']}, well {r['well']}, report step {r['report_step']}, "
        f"date {r['report_date']} (day {r['report_day']}), time step {r['time_step']}, "
        f"date {r['time_step_date']} (day {r['time_step_day']})"
        for r in result["wells_shut_by_simulator"]
    ]
    if result["wells_shut_by_simulator"]:
        lines.append(
            "For an optimization, a well shut by the simulator makes the objective discontinuous, "
            "so compare across realizations or batches before trusting a gradient."
        )
    lines.append(f"Well control mode changes: {len(result['well_control_mode_changes'])}")
    lines += [
        f"- {r['well']} {r['transition']}: {r['count']} (first line {r['first_line']}, date {r['first_date']})"
        for r in result["well_control_mode_changes"]
    ]
    return "\n\n".join(lines + [result["limits"]]) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prt", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = diagnose(args.prt.read_text(encoding="latin-1"))
        args.outdir.mkdir(parents=True, exist_ok=False)
        (args.outdir / "report.json").write_text(json.dumps(result, indent=2))
        (args.outdir / "report.md").write_text(markdown(result))
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
