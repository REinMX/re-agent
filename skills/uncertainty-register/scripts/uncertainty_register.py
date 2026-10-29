#!/usr/bin/env python3
"""Draft an uncertainty register from FMU design matrix / GEN_KW priors.

Reads project files only. Never invents missing ranges — gaps stay gaps.
UNIFORM/LOGUNIF priors expose min/max (arg1/arg2). They are NOT percentiles.
Do not fabricate P10/P50/P90 from design-matrix samples.

P10/P90: when contract says numerical and a prior somehow carries percentiles,
state numerical. UNIFORM/LOGUNIF GEN_KW priors give min/max only.

Usage:
  uncertainty_register.py --contract /path/to/site.contract.yml
  uncertainty_register.py --contract ... --diff
  uncertainty_register.py --contract ... --outdir /path/to/out

Exit: 0 wrote register (gaps may be listed), 2 no prior sources / bad input, 1 unexpected.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402

COLUMNS = [
    "name",
    "group",
    "distribution",
    "min",
    "max",
    "p10",
    "p50",
    "p90",
    "source_file",
    "status",
    "notes",
]


def _blank_row(name: str = "", **kwargs: str) -> dict[str, str]:
    row = {c: "" for c in COLUMNS}
    row["name"] = name
    row["status"] = kwargs.get("status", "gap")
    for k, v in kwargs.items():
        if k in row:
            row[k] = v
    return row


def parse_parameters_csv(path: Path) -> list[dict[str, str]]:
    """Parse parameters.csv: group,name,distribution,arg1,arg2,notes."""
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            name = (raw.get("name") or "").strip()
            if not name:
                continue
            dist = (raw.get("distribution") or "").strip().upper()
            arg1 = (raw.get("arg1") or "").strip()
            arg2 = (raw.get("arg2") or "").strip()
            group = (raw.get("group") or "").strip()
            notes = (raw.get("notes") or "").strip()
            status = "ok"
            gap_notes: list[str] = []
            if not dist:
                status = "gap"
                gap_notes.append("missing distribution")
            if not arg1 or not arg2:
                status = "gap"
                gap_notes.append("missing min/max args")
            # Priors are UNIFORM/LOGUNIF min/max — not percentiles. Leave p10/p50/p90 blank.
            note_parts = [notes] if notes else []
            note_parts.append("prior is min/max (not percentiles); P10/P50/P90 left blank")
            if gap_notes:
                note_parts.extend(gap_notes)
            rows.append(
                _blank_row(
                    name=name,
                    group=group,
                    distribution=dist,
                    min=arg1 if arg1 else "",
                    max=arg2 if arg2 else "",
                    p10="",
                    p50="",
                    p90="",
                    source_file=str(path),
                    status=status,
                    notes="; ".join(p for p in note_parts if p),
                )
            )
    return rows


_PRIOR_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_]+)\s+(\S+)\s+(\S+)\s*$")


def parse_genkw_definitions(path: Path) -> list[tuple[str, str, list[str]]]:
    """Read GEN_KW names, distributions and all arguments, without interpreting them."""
    definitions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        tokens = line.split("--", 1)[0].split("#", 1)[0].split()
        if tokens:
            definitions.append((tokens[0], tokens[1].upper() if len(tokens) > 1 else "", tokens[2:]))
    return definitions


def parse_genkw_priors_txt(path: Path, group_hint: str = "") -> list[dict[str, str]]:
    """Parse ERT GEN_KW prior file: NAME DIST arg1 arg2 (comments with --)."""
    rows: list[dict[str, str]] = []
    for name, distribution, arguments in parse_genkw_definitions(path):
        stripped = " ".join([name, distribution, *arguments])
        m = _PRIOR_LINE.match(stripped)
        if not m:
            # Incomplete / unparseable non-comment → gap if it looks like a name
            toks = stripped.split()
            if toks and re.match(r"^[A-Za-z_]", toks[0]):
                rows.append(
                    _blank_row(
                        name=toks[0],
                        group=group_hint,
                        distribution=toks[1].upper() if len(toks) > 1 else "",
                        min=toks[2] if len(toks) > 2 else "",
                        max=toks[3] if len(toks) > 3 else "",
                        source_file=str(path),
                        status="gap",
                        notes="unparseable or incomplete prior line; not filled",
                    )
                )
            continue
        name, dist, a1, a2 = m.group(1), m.group(2).upper(), m.group(3), m.group(4)
        rows.append(
            _blank_row(
                name=name,
                group=group_hint,
                distribution=dist,
                min=a1,
                max=a2,
                source_file=str(path),
                status="ok",
                notes="prior is min/max (not percentiles); P10/P50/P90 left blank",
            )
        )
    return rows


def design_matrix_names(path: Path) -> list[str]:
    """Header names from design_matrix CSV (skip REAL column). Samples are not priors."""
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
    names = [h.strip() for h in header if h.strip() and h.strip().upper() != "REAL"]
    return names


def design_matrix_header(path: Path) -> list[str]:
    """Names from a CSV header, or from the first row of an .xlsx design sheet (pandas/openpyxl)."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        try:
            import pandas as pd
        except ImportError as exc:
            raise ValueError(f"reading {path.name} needs pandas + openpyxl (Komodo)") from exc
        sheets = pd.read_excel(path, sheet_name=None, nrows=0)
        sheet = "DesignSheet" if "DesignSheet" in sheets else next(iter(sheets))
        header = [str(c) for c in sheets[sheet].columns]
        return [h.strip() for h in header if h.strip() and h.strip().upper() != "REAL"]
    return design_matrix_names(path)


def sources_from_contract(contract: contract_lib.Contract) -> tuple[dict[str, Path], list[str]]:
    """Prior / design files from contract `uncertainty` (paths relative to paths.root).

    Returns (sources, missing). A listed file that does not exist is reported, never skipped.
    """
    unc = contract.get("uncertainty")
    if not isinstance(unc, dict) or not any(
        unc.get(k) for k in ("parameters_csv", "prior_files", "design_matrix")
    ):
        raise ContractError(
            "contract `uncertainty` block is required (parameters_csv, prior_files, design_matrix), "
            "or pass --parameters-csv / --priors / --design-matrix"
        )
    found: dict[str, Path] = {}
    missing: list[str] = []
    if unc.get("parameters_csv"):
        found["parameters_csv"] = contract.resolve(unc["parameters_csv"])
    for i, item in enumerate(unc.get("prior_files") or []):
        found[f"prior_{i}"] = contract.resolve(item)
    if unc.get("design_matrix"):
        found["design_matrix_csv"] = contract.resolve(unc["design_matrix"])
    for key, path in list(found.items()):
        if not path.is_file():
            missing.append(f"uncertainty.{key}: {path}")
        else:
            found[key] = path.resolve()
    return found, missing


def merge_rows(
    csv_rows: list[dict[str, str]],
    txt_rows: list[dict[str, str]],
    design_names: list[str],
) -> list[dict[str, str]]:
    """Prefer parameters.csv; fill from GEN_KW txt; gap for design names with no prior."""
    by_name: dict[str, dict[str, str]] = {}
    for r in csv_rows:
        by_name[r["name"]] = r
    for r in txt_rows:
        name = r["name"]
        if name not in by_name:
            by_name[name] = r
        else:
            # Enrich empty group from txt if csv lacked it
            if not by_name[name].get("group") and r.get("group"):
                by_name[name]["group"] = r["group"]
    for name in design_names:
        if name not in by_name:
            by_name[name] = _blank_row(
                name=name,
                status="gap",
                notes="name appears in design matrix header but no prior definition found",
            )
    # Stable order: design matrix order if present, else sorted
    order = list(design_names) if design_names else sorted(by_name.keys())
    for name in sorted(by_name.keys()):
        if name not in order:
            order.append(name)
    return [by_name[n] for n in order if n in by_name]


def write_register_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in COLUMNS})


def write_register_md(
    path: Path,
    rows: list[dict[str, str]],
    *,
    p10_p90: str,
    sources: list[str],
    generated_at: str,
    project: str,
) -> None:
    n_ok = sum(1 for r in rows if r.get("status") == "ok")
    n_gap = sum(1 for r in rows if r.get("status") == "gap")
    lines = [
        "# Uncertainty register",
        "",
        f"- Generated: `{generated_at}`",
        f"- Project: `{project}`",
        f"- **P10/P90: {p10_p90}** (contract convention for ensemble bands).",
        "- UNIFORM/LOGUNIF prior distributions give **min/max** only.",
        "  Percentile columns are blank unless a source file supplies percentiles.",
        "  Design-matrix sample values are **not** used to invent P10/P50/P90.",
        f"- Rows: {len(rows)} (ok={n_ok}, gap={n_gap})",
        "",
        "## Sources",
        "",
    ]
    for s in sources:
        lines.append(f"- `{s}`")
    if not sources:
        lines.append("_None — register should not have been written._")
    lines.extend(
        [
            "",
            "## Register",
            "",
            "| name | group | distribution | min | max | p10 | p50 | p90 | status | source_file | notes |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for r in rows:
        lines.append(
            "| {name} | {group} | {distribution} | {min} | {max} | {p10} | {p50} | {p90} | "
            "**{status}** | `{source_file}` | {notes} |".format(**{c: r.get(c, "") for c in COLUMNS})
        )
    lines.extend(["", "## Gaps", ""])
    gaps = [r for r in rows if r.get("status") == "gap"]
    if not gaps:
        lines.append("_No gaps._")
    else:
        for r in gaps:
            lines.append(f"- `{r['name']}`: {r.get('notes') or 'gap'}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def load_register_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def diff_registers(previous: list[dict[str, str]], current: list[dict[str, str]]) -> dict[str, Any]:
    prev_by = {r["name"]: r for r in previous if r.get("name")}
    curr_by = {r["name"]: r for r in current if r.get("name")}
    added = sorted(set(curr_by) - set(prev_by))
    removed = sorted(set(prev_by) - set(curr_by))
    changed: list[dict[str, Any]] = []
    watch = ("distribution", "min", "max", "p10", "p50", "p90", "status")
    for name in sorted(set(prev_by) & set(curr_by)):
        deltas = {
            k: {"from": prev_by[name].get(k, ""), "to": curr_by[name].get(k, "")}
            for k in watch
            if (prev_by[name].get(k, "") or "") != (curr_by[name].get(k, "") or "")
        }
        if deltas:
            changed.append({"name": name, "fields": deltas})
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "n_prev": len(prev_by),
        "n_curr": len(curr_by),
    }


def write_diff_md(path: Path, diff: dict[str, Any]) -> None:
    lines = [
        "# Uncertainty register diff",
        "",
        f"- Previous rows: {diff['n_prev']}",
        f"- Current rows: {diff['n_curr']}",
        "",
        "## Added",
        "",
    ]
    if not diff["added"]:
        lines.append("_None._")
    else:
        for n in diff["added"]:
            lines.append(f"- `{n}`")
    lines.extend(["", "## Removed", ""])
    if not diff["removed"]:
        lines.append("_None._")
    else:
        for n in diff["removed"]:
            lines.append(f"- `{n}`")
    lines.extend(["", "## Changed", ""])
    if not diff["changed"]:
        lines.append("_None._")
    else:
        for item in diff["changed"]:
            lines.append(f"- `{item['name']}`:")
            for field, pair in item["fields"].items():
                lines.append(f"  - {field}: `{pair['from']}` → `{pair['to']}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_contract_args(parser)
    parser.add_argument("--outdir", type=Path, default=None)
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Diff against existing register.csv before overwrite (snapshot kept).",
    )
    parser.add_argument(
        "--parameters-csv",
        type=Path,
        default=None,
        help="Override parameters.csv path (tests).",
    )
    parser.add_argument(
        "--priors",
        type=Path,
        nargs="*",
        default=None,
        help="Extra GEN_KW prior .txt files (tests).",
    )
    parser.add_argument(
        "--design-matrix",
        type=Path,
        default=None,
        help="Override design matrix CSV for name discovery (tests).",
    )
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(args.contract, args.profile)
        deliv = contract.path("deliverables").resolve()
        project = Path((contract.get("paths") or {}).get("project") or contract.path("root")).resolve()
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2

    outdir = args.outdir.resolve() if args.outdir else (deliv / "uncertainty_register")
    outdir.mkdir(parents=True, exist_ok=True)

    # Explicit overrides (tests / one-off paths) skip auto-discovery so contract
    # rock/relperm priors do not leak into a fixture-only run.
    use_overrides = bool(args.parameters_csv or args.design_matrix or args.priors)
    sources: dict[str, Path] = {}
    if use_overrides:
        if args.parameters_csv:
            sources["parameters_csv"] = args.parameters_csv.resolve()
        if args.design_matrix:
            sources["design_matrix_csv"] = args.design_matrix.resolve()
        if args.priors:
            for i, p in enumerate(args.priors):
                sources[f"prior_extra_{i}"] = p.resolve()
    else:
        try:
            sources, missing_src = sources_from_contract(contract)
        except ContractError as exc:
            sys.stderr.write(f"error: contract: {exc}\n")
            return 2
        if missing_src:
            sys.stderr.write("error: contract lists prior/design files that do not exist:\n")
            for line in missing_src:
                sys.stderr.write(f"  - {line}\n")
            return 2

    csv_rows: list[dict[str, str]] = []
    txt_rows: list[dict[str, str]] = []
    design_names: list[str] = []
    source_list: list[str] = []

    if "parameters_csv" in sources and sources["parameters_csv"].is_file():
        csv_rows = parse_parameters_csv(sources["parameters_csv"])
        source_list.append(str(sources["parameters_csv"]))

    for key, path in sources.items():
        if key in ("parameters_csv", "design_matrix_csv"):
            continue
        if not path.is_file():
            continue
        group_hint = ""
        if "rock" in path.name.lower():
            group_hint = "ROCK"
        elif "relperm" in path.name.lower():
            group_hint = "RELPERM"
        txt_rows.extend(parse_genkw_priors_txt(path, group_hint=group_hint))
        source_list.append(str(path))

    if "design_matrix_csv" in sources and sources["design_matrix_csv"].is_file():
        try:
            design_names = design_matrix_header(sources["design_matrix_csv"])
        except ValueError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2
        source_list.append(str(sources["design_matrix_csv"]) + " (names only; samples not used as priors)")

    if not csv_rows and not txt_rows and not design_names:
        sys.stderr.write(
            "error: no prior sources found "
            "(contract uncertainty.parameters_csv / prior_files / design_matrix)\n"
        )
        return 2

    rows = merge_rows(csv_rows, txt_rows, design_names)
    p10_p90 = str(contract.get("p10_p90") or "unknown")
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    project_name = str(contract.get("project") or project.name)

    reg_csv = outdir / "register.csv"
    reg_md = outdir / "register.md"
    manifest_path = outdir / "manifest.json"
    diff_json_path = outdir / "register_diff.json"
    diff_md_path = outdir / "register_diff.md"

    previous: list[dict[str, str]] = []
    if args.diff and reg_csv.is_file():
        previous = load_register_csv(reg_csv)
        snap = outdir / f"register.prev.{generated_at.replace(':', '')}.csv"
        shutil.copy2(reg_csv, snap)

    write_register_csv(reg_csv, rows)
    write_register_md(
        reg_md,
        rows,
        p10_p90=p10_p90,
        sources=source_list,
        generated_at=generated_at,
        project=project_name,
    )

    diff_doc: dict[str, Any] | None = None
    if args.diff:
        diff_doc = diff_registers(previous, rows)
        diff_json_path.write_text(json.dumps(diff_doc, indent=2) + "\n", encoding="utf-8")
        write_diff_md(diff_md_path, diff_doc)

    n_ok = sum(1 for r in rows if r["status"] == "ok")
    n_gap = sum(1 for r in rows if r["status"] == "gap")
    manifest = {
        "skill": "uncertainty-register",
        "project": project_name,
        "p10_p90": p10_p90,
        "p10_p90_note": (
            "Contract convention for ensemble bands is numerical. "
            "This register leaves p10/p50/p90 blank for UNIFORM/LOGUNIF min/max priors."
        ),
        "generated_at": generated_at,
        "sources": source_list,
        "n_rows": len(rows),
        "n_ok": n_ok,
        "n_gap": n_gap,
        "outputs": {
            "register_csv": str(reg_csv),
            "register_md": str(reg_md),
            "manifest": str(manifest_path),
        },
        "diff": diff_doc,
    }
    if args.diff:
        manifest["outputs"]["register_diff_json"] = str(diff_json_path)
        manifest["outputs"]["register_diff_md"] = str(diff_md_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    sys.stdout.write(
        f"uncertainty-register: wrote {reg_csv}\n"
        f"  p10_p90={p10_p90} rows={len(rows)} ok={n_ok} gap={n_gap}\n"
        f"  sources={len(source_list)}\n"
    )
    if args.diff:
        sys.stdout.write(
            f"  diff: added={len((diff_doc or {}).get('added') or [])} "
            f"removed={len((diff_doc or {}).get('removed') or [])} "
            f"changed={len((diff_doc or {}).get('changed') or [])}\n"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: {exc}\n")
        raise SystemExit(1) from exc
