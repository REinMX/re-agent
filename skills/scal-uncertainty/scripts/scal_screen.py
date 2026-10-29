#!/usr/bin/env python3
"""Run low/base/high Flow cases in isolated folders and compare their summaries."""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from scal_wire import deck_dimensions, rewrite_deck  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract/scripts"))
from runpath_lib import read_ensemble  # noqa: E402


def summary_metrics(base: Path) -> dict:
    try:
        from resdata.summary import Summary
    except ImportError as error:
        raise ImportError("resdata is required to read Flow summaries") from error
    summary = Summary(str(base))
    required = ("FOPT", "FWPT", "TIME")
    if any(not summary.has_key(key) for key in required):
        raise ValueError(f"Missing required summary vector in {base}")
    days = list(summary.numpy_vector("TIME"))
    fopt = list(summary.numpy_vector("FOPT"))
    fwpt = list(summary.numpy_vector("FWPT"))
    if summary.has_key("FWCT"):
        watercut = list(summary.numpy_vector("FWCT"))
        source = "FWCT"
    elif summary.has_key("FWPR") and summary.has_key("FOPR"):
        water = summary.numpy_vector("FWPR")
        oil = summary.numpy_vector("FOPR")
        watercut = [float(w / (w + o)) if w + o > 0 else 0.0 for w, o in zip(water, oil, strict=True)]
        source = "FWPR/(FOPR+FWPR)"
    else:
        raise ValueError(f"Missing FWCT or FOPR/FWPR in {base}")
    if not days or not all(math.isfinite(float(x)) for v in (days, fopt, fwpt, watercut) for x in v):
        raise ValueError(f"Empty or non-finite summary vectors in {base}")
    return {
        "FOPT": float(fopt[-1]),
        "FWPT": float(fwpt[-1]),
        "FWCT_end": float(watercut[-1]),
        "FWCT_source": source,
        "bt10_days": next((float(day) for day, wc in zip(days, watercut, strict=True) if wc > 0.1), None),
        "days": float(days[-1]),
        "end_date": str(summary.dates[-1]),
        "FOPT_unit": str(summary.unit("FOPT")),
        "FWPT_unit": str(summary.unit("FWPT")),
    }


def copy_deck_inputs(source: Path, target: Path, case_root: Path, seen=None, active=None) -> None:
    """Copy reachable deck includes, preserving relative paths under the new case."""
    seen = set() if seen is None else seen
    source = source.resolve()
    active = set() if active is None else active
    if source in active:
        raise ValueError(f"Cyclic INCLUDE reference: {source.name}")
    active.add(source)
    identity = (source, target.resolve())
    if identity in seen:
        active.remove(source)
        return
    seen.add(identity)
    if not target.resolve().is_relative_to(case_root.resolve()):
        raise ValueError(f"Include escapes isolated case folder: {source.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    text = source.read_text(encoding="latin-1")
    clean = re.sub(r"--[^\n]*", "", text)
    for match in re.finditer(r"\b(?:INCLUDE|IMPORT)\s+(?:['\"]([^'\"]+)['\"]|([^\s]+))\s*/", clean, re.I):
        relative = Path(match.group(1) or match.group(2))
        if relative.is_absolute():
            raise ValueError("Absolute INCLUDE paths cannot be isolated. Use relative includes.")
        included = source.parent / relative
        # --prepare can supply generated includes; Flow will fail if they remain absent.
        if included.is_file():
            copy_deck_inputs(included, target.parent / relative, case_root, seen, active)
    active.remove(source)


def geological_context(pattern: str, scal_span: float, reference: dict | None = None) -> dict:
    from numpy import percentile

    pattern = pattern if pattern.endswith(".SMSPEC") else pattern + ".SMSPEC"
    folders = str(Path(pattern).parent)
    ensemble = read_ensemble(folders, need_data_file=False)
    if not ensemble.found:
        raise ValueError("Ensemble glob matched no realization folders")
    ensemble.require_complete()
    summary_name = Path(pattern).name
    bases = []
    for member in ensemble.found:
        matches = sorted(member.runpath.glob(summary_name))
        if len(matches) != 1:
            raise ValueError(
                f"Expected one summary for realization {member.realization}, found {len(matches)}"
            )
        bases.append(matches[0].with_suffix(""))
    metrics = [summary_metrics(base) for base in bases]
    reference = reference or metrics[0]
    for item in metrics:
        if any(item[key] != reference[key] for key in ("end_date", "FOPT_unit", "FWPT_unit")):
            raise ValueError("SCAL and geological cases must have the same end date and volume units")
    values = [item["FOPT"] for item in metrics]
    p10, p50, p90 = map(float, percentile(values, [10, 50, 90]))
    if p50 <= 0 or p90 <= p10:
        raise ValueError("Geological prior must have positive P50 and nonzero FOPT spread")
    return {
        "members": len(values),
        "convention": "numerical percentiles",
        "end_date": reference["end_date"],
        "FOPT_unit": reference["FOPT_unit"],
        "FOPT_P10": p10,
        "FOPT_P50": p50,
        "FOPT_P90": p90,
        "span_pct_P50": 100 * (p90 - p10) / p50,
        "scal_to_geological_span": scal_span / (p90 - p10),
    }


def screen(
    rec: Path,
    deck: Path,
    params: list[float],
    out: Path,
    flow_cmd: list[str],
    prepare: str | None = None,
    copy: list[Path] | None = None,
    ensemble_glob: str | None = None,
    delta_s: float = 0.01,
) -> dict:
    from scal_rec import generate_tables

    if not params or 0 not in params or len(set(params)) != len(params):
        raise ValueError("Parameters must be unique and include base 0")
    if any(not math.isfinite(value) or abs(value) > 1 for value in params):
        raise ValueError("Interpolation parameters must lie in [-1, 1]")
    if not flow_cmd or shutil.which(flow_cmd[0]) is None:
        raise ValueError("Flow executable is missing; put flow on PATH or set contract flow_cmd")
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output directory must be empty or new")
    out.mkdir(parents=True, exist_ok=True)
    tables = generate_tables(rec, params, delta_s, out / "tables")
    results = []
    deck_text = deck.read_text(encoding="latin-1")
    ntsfun, _ = deck_dimensions(deck_text)
    gas_required = bool(re.search(r"(?mi)^\s*SGOF\b", deck_text))
    for entry in tables:
        expected = set(range(1, ntsfun + 1))
        for keyword in ("SWOF", "SGOF"):
            found = {table["satnum"] for table in entry["tables"] if table["keyword"] == keyword}
            if (keyword == "SWOF" or gas_required or found) and found != expected:
                raise ValueError("Recommendation SATNUMs differ from TABDIMS NTSFUN")
        if entry["qc"]["returncode"] != 0:
            raise ValueError("Generated tables failed deck QC; inspect tables reports")
        run = out / f"p{entry['param']:g}"
        run.mkdir()
        target = run / "model" / deck.name
        copy_deck_inputs(deck, target, run)
        for path in copy or []:
            if not path.is_file():
                raise ValueError(f"Copy input is not a file: {path}")
            shutil.copyfile(path, run / path.name)
        if prepare:
            with (run / "prepare.log").open("w") as log:
                prepared = subprocess.run(
                    prepare, shell=True, cwd=run, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            if prepared.returncode:
                raise ValueError(f"Preparation failed (rc {prepared.returncode}); see {run / 'prepare.log'}")
        rows = max(table["rows"] for table in entry["tables"])
        text, _ = rewrite_deck(target.read_text(encoding="latin-1"), rows)
        target.write_text(text, encoding="latin-1")
        shutil.copyfile(entry["include"], target.parent / "relperm.inc")
        command = [*flow_cmd, target.name, "--output-dir=.", "--threads-per-process=1"]
        print(f"Run in {target.parent}: {shlex.join(command)}", flush=True)
        start = time.monotonic()
        with (target.parent / "flow.log").open("w") as log:
            process = subprocess.run(
                command, cwd=target.parent, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        elapsed = time.monotonic() - start
        if process.returncode:
            raise ValueError(f"Flow failed (rc {process.returncode}); see {target.parent / 'flow.log'}")
        results.append(
            {
                "param": entry["param"],
                "wall_seconds": elapsed,
                "returncode": process.returncode,
                **summary_metrics(target.with_suffix("")),
            }
        )
    for row in results:
        if any(row[key] != results[0][key] for key in ("end_date", "FOPT_unit", "FWPT_unit")):
            raise ValueError("SCAL cases must have the same end date and volume units")
    base = next(row["FOPT"] for row in results if row["param"] == 0)
    if base <= 0:
        raise ValueError("Base FOPT must be positive to report a relative SCAL spread")
    for row in results:
        row["dFOPT"] = row["FOPT"] - base
        row["dFOPT_pct_base"] = 100 * row["dFOPT"] / base
    span = max(row["FOPT"] for row in results) - min(row["FOPT"] for row in results)
    report = {
        "cases": results,
        "base_FOPT": base,
        "scal_span": span,
        "scal_span_pct_base": 100 * span / base,
        "geological_prior": geological_context(ensemble_glob, span, results[0]) if ensemble_glob else None,
    }
    (out / "screen.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    lines = [
        "# SCAL screen",
        "",
        f"End date {results[0]['end_date']}. FOPT unit {results[0]['FOPT_unit']}; "
        f"FWPT unit {results[0]['FWPT_unit']}. Time is in days.",
        "",
        "| Parameter | FOPT | FWPT | End FWCT | WCT > 0.1 (days) | dFOPT (%) | Wall (s) |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in results:
        lines.append(
            f"| {row['param']:g} | {row['FOPT']:.6g} | {row['FWPT']:.6g} | "
            f"{row['FWCT_end']:.4f} | {row['bt10_days']} | {row['dFOPT_pct_base']:+.2f} | "
            f"{row['wall_seconds']:.2f} |"
        )
    lines.extend(["", f"SCAL FOPT span is {report['scal_span_pct_base']:.2f}% of base."])
    prior = report["geological_prior"]
    if prior:
        lines.extend(
            [
                f"Geological prior ({prior['members']} members) P90-P10 is {prior['span_pct_P50']:.2f}% of P50 "
                "(numerical percentile convention).",
                f"SCAL / geological FOPT span is {prior['scal_to_geological_span']:.3f}.",
            ]
        )
    else:
        lines.append("Geological prior span was not supplied.")
    (out / "screen.md").write_text("\n".join(lines) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rec", type=Path, required=True)
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument("--params", default="-1,0,1", help="Use --params=-1,0,1")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ensemble-glob")
    parser.add_argument("--prepare", help="Shell command run in each isolated case before Flow")
    parser.add_argument("--copy", type=Path, action="append", default=[])
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--delta-s", type=float, default=0.01)
    arguments = list(sys.argv[1:] if argv is None else argv)
    for index, value in enumerate(arguments[:-1]):
        if value == "--params" and arguments[index + 1].startswith("-"):
            arguments[index : index + 2] = ["--params=" + arguments[index + 1]]
            break
    args = parser.parse_args(arguments)
    try:
        command = ["flow"]
        if args.contract:
            from doctor import read_runtime_contract

            contract = read_runtime_contract(args.contract, args.profile)
            command = shlex.split(contract.get("flow_cmd") or "flow")
        screen(
            args.rec.resolve(),
            args.deck.resolve(),
            [float(p) for p in args.params.split(",")],
            args.out.resolve(),
            command,
            args.prepare,
            args.copy,
            args.ensemble_glob,
            args.delta_s,
        )
    except (ImportError, OSError, ValueError, TypeError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"Results: {args.out / 'screen.json'} and {args.out / 'screen.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
