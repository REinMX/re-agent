#!/usr/bin/env python3
"""Assemble a QA/QC pre-read pack from existing deliverables.

Does not start Flow/ERT. Does not invent numbers — only cites bands CSV,
scenario matrix, plot manifests, and missing_vectors.json.

Usage:
  qa_qc_preread.py --contract /path/to/site.contract.yml --stage <STAGE>
  qa_qc_preread.py --contract ... --outdir /path/to/out   # stage = contract gate_default_stage
Stages are the keys of contract gate_stages (any names, e.g. BPBO / VPBO / DG0).

Exit: 0 wrote pack (open issues may be listed), 2 missing critical inputs, 1 unexpected.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args, flag_text  # noqa: E402

DEFAULT_HIGHLIGHTS = ("FOPT", "FWPT", "FOPR", "FWPR")


def deliverables_root(contract: dict[str, Any]) -> Path:
    d = (contract.get("paths") or {}).get("deliverables")
    if not d:
        raise ValueError("paths.deliverables missing in contract")
    return Path(d).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def collect_artifacts(deliv: Path) -> dict[str, Any]:
    profiles_dir = deliv / "profiles"
    bands_dir = deliv / "bands"
    sqc_dir = deliv / "scenario_qc"
    plots_dir = deliv / "plots"

    profiles_manifest = _read_json(profiles_dir / "manifest.json")
    bands_manifest = _read_json(bands_dir / "manifest.json")
    missing_vectors = _read_json(bands_dir / "_full_contract_fail_demo" / "missing_vectors.json")
    if missing_vectors is None:
        missing_vectors = _read_json(bands_dir / "missing_vectors.json")

    bands_eos = _read_csv_rows(bands_dir / "bands_end_of_sim.csv")
    matrix_json = _read_json(sqc_dir / "scenario_matrix.json")
    matrix_md = (
        (sqc_dir / "scenario_matrix.md").read_text(encoding="utf-8")
        if (sqc_dir / "scenario_matrix.md").is_file()
        else ""
    )
    plots_manifest = _read_json(plots_dir / "manifest.json")

    plot_paths: list[str] = []
    if plots_manifest:
        for key in ("resinsight_pngs", "evidence_pngs"):
            for p in plots_manifest.get(key) or []:
                plot_paths.append(str(p))
    else:
        for sub in ("resinsight", "evidence"):
            d = plots_dir / sub
            if d.is_dir():
                plot_paths.extend(sorted(str(p) for p in d.glob("*.png")))

    ureg_dir = deliv / "uncertainty_register"
    ureg_csv = ureg_dir / "register.csv"
    ureg_md = ureg_dir / "register.md"
    ureg_manifest = _read_json(ureg_dir / "manifest.json")

    return {
        "profiles_dir": str(profiles_dir) if profiles_dir.is_dir() else None,
        "bands_dir": str(bands_dir) if bands_dir.is_dir() else None,
        "scenario_qc_dir": str(sqc_dir) if sqc_dir.is_dir() else None,
        "plots_dir": str(plots_dir) if plots_dir.is_dir() else None,
        "uncertainty_register_dir": str(ureg_dir) if ureg_dir.is_dir() else None,
        "uncertainty_register_csv": str(ureg_csv) if ureg_csv.is_file() else None,
        "uncertainty_register_md": str(ureg_md) if ureg_md.is_file() else None,
        "uncertainty_register_manifest": ureg_manifest,
        "profiles_manifest": profiles_manifest,
        "bands_manifest": bands_manifest,
        "missing_vectors_doc": missing_vectors,
        "bands_end_of_sim": bands_eos,
        "scenario_matrix": matrix_json,
        "scenario_matrix_md": matrix_md,
        "plots_manifest": plots_manifest,
        "plot_paths": plot_paths,
        "has_profiles": bool(
            profiles_manifest
            or (profiles_dir / "profiles_long.csv").is_file()
            or (profiles_dir / "field_profiles.csv").is_file()
        ),
        "has_bands": bool(bands_eos or bands_manifest),
        "has_scenario_qc": bool(matrix_json),
        "has_plots": bool(plot_paths),
        "has_uncertainty_register": bool(ureg_csv.is_file() or ureg_manifest),
    }


def build_open_issues(
    contract: dict[str, Any],
    arts: dict[str, Any],
    stage: str,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    required_summary = list(
        contract_lib.get_dotted(contract, "vectors.required_summary")
        or contract.get("required_summary")
        or []
    )

    mv = arts.get("missing_vectors_doc") or {}
    missing = list(mv.get("missing_vectors") or [])
    if not missing and arts.get("bands_manifest"):
        # Infer from contract vs available families when fail-demo exists elsewhere
        avail = set(arts["bands_manifest"].get("available_families") or [])
        for name in required_summary:
            if name not in avail and name not in (arts["bands_manifest"].get("resolved") or {}):
                # family may be present under resolved keys
                resolved = arts["bands_manifest"].get("resolved") or {}
                if name not in resolved and name not in avail:
                    missing.append(name)
    # Prefer explicit fail-demo list
    if arts.get("missing_vectors_doc"):
        missing = list(arts["missing_vectors_doc"].get("missing_vectors") or [])

    if missing:
        issues.append(
            {
                "id": "missing_required_summary",
                "severity": "high",
                "source": "ensemble-bands / required_summary",
                "detail": (
                    "Full-contract bands fail: required SUMMARY vectors missing "
                    f"(never zero-filled): {', '.join(missing)}. "
                    "Extend SUMMARY.inc and re-run the ensemble."
                ),
            }
        )

    bm = arts.get("bands_manifest") or {}
    if bm.get("vector_mode") == "explicit --vectors":
        issues.append(
            {
                "id": "bands_present_vectors_only",
                "severity": "medium",
                "source": "deliverables/bands/manifest.json",
                "detail": (
                    "Published bands used present-vector mode "
                    f"({', '.join(bm.get('required') or [])}); "
                    "full required_summary check is not green."
                ),
            }
        )

    matrix = arts.get("scenario_matrix") or {}
    if matrix.get("any_fail"):
        fail_rows = [r for r in (matrix.get("rows") or []) if str(r.get("status", "")).upper() == "FAIL"]
        for row in fail_rows:
            sid = row.get("scenario_id", "?")
            flags = row.get("flags") or ""
            issues.append(
                {
                    "id": f"scenario_qc_fail:{sid}",
                    "severity": "high",
                    "source": "deliverables/scenario_qc/scenario_matrix.json",
                    "detail": (
                        f"Scenario '{sid}' status=FAIL flags={flags}. "
                        "Non-demo FAIL blocks strict gates; demos excluded when scenario_qc.exclude_demo is true."
                    ),
                }
            )

    stage_cfg = (contract.get("gate_stages") or {}).get(stage) or {}
    req_arts = list(stage_cfg.get("required_artifacts") or [])
    art_map = {
        "profiles": arts["has_profiles"],
        "bands": arts["has_bands"],
        "scenario_qc": arts["has_scenario_qc"],
        "plots": arts["has_plots"],
    }
    for name in req_arts:
        if name == "preread":
            continue
        if name in art_map and not art_map[name]:
            issues.append(
                {
                    "id": f"missing_artifact:{name}",
                    "severity": "high",
                    "source": "gate_stages required_artifacts",
                    "detail": f"Required artifact '{name}' missing under deliverables/.",
                }
            )

    return issues


def band_highlights(
    rows: list[dict[str, str]],
    p10_p90: str,
    prefer: tuple[str, ...] | list[str] = DEFAULT_HIGHLIGHTS,
    well: str | None = None,
) -> list[dict[str, Any]]:
    """Quote only scripted CSV numbers. Keys from contract vectors.headline (default field cumulatives)."""
    out: list[dict[str, Any]] = []
    by_key = {r.get("KEY"): r for r in rows if r.get("KEY")}
    for key in prefer:
        if key not in by_key:
            continue
        r = by_key[key]
        out.append(
            {
                "KEY": key,
                "DATE_MAX": r.get("DATE_MAX", ""),
                "MEAN": r.get("MEAN", ""),
                "P10": r.get("P10", ""),
                "P50": r.get("P50", ""),
                "P90": r.get("P90", ""),
                "COUNT": r.get("COUNT", ""),
                "p10_p90": p10_p90,
            }
        )
    # Also include one well oil rate: contract wells.plot_default, else the first WOPR:<well> found.
    for key, r in by_key.items():
        wanted = (key == f"WOPR:{well}") if well else bool(key and key.startswith("WOPR:"))
        if wanted and key not in {h["KEY"] for h in out}:
            out.append(
                {
                    "KEY": key,
                    "DATE_MAX": r.get("DATE_MAX", ""),
                    "MEAN": r.get("MEAN", ""),
                    "P10": r.get("P10", ""),
                    "P50": r.get("P50", ""),
                    "P90": r.get("P90", ""),
                    "COUNT": r.get("COUNT", ""),
                    "p10_p90": p10_p90,
                }
            )
            break
    return out


def render_markdown(
    *,
    stage: str,
    contract: dict[str, Any],
    contract_path: Path,
    arts: dict[str, Any],
    highlights: list[dict[str, Any]],
    issues: list[dict[str, str]],
    generated_at: str,
) -> str:
    p10 = contract.get("p10_p90", "unknown")
    project = contract.get("project", "")
    sim = contract.simulator_name if hasattr(contract, "simulator_name") else contract.get("simulator", "")
    lines: list[str] = []
    lines.append(f"# QA/QC pre-read — {stage}")
    lines.append("")
    lines.append(f"- Generated: `{generated_at}`")
    lines.append(f"- Project: `{project}`")
    lines.append(f"- Contract: `{contract_path}`")
    lines.append(f"- Simulator: `{sim}`")
    lines.append(f"- **P10/P90: {p10}** (state this convention; not exceedance unless contract says so)")
    lines.append("- Source rule: numbers only from deliverables scripts — never invented.")
    lines.append("")
    lines.append("## Contract summary")
    lines.append("")
    lines.append("| Key | Value |")
    lines.append("|---|---|")
    lines.append(f"| project | {project} |")
    lines.append(f"| simulator | {sim} |")
    lines.append(f"| p10_p90 | **{p10}** |")
    lines.append(f"| everest | {flag_text(contract.get('everest'))} |")
    lines.append(f"| sumo | {flag_text(contract.get('sumo'))} |")
    lines.append(f"| scratch | `{(contract.get('paths') or {}).get('scratch', '')}` |")
    req = ", ".join(contract_lib.get_dotted(contract, "vectors.required_summary") or [])
    lines.append(f"| required_summary | {req} |")
    lines.append("")
    lines.append("## Ensemble band highlights")
    lines.append("")
    lines.append(
        f"Source: `deliverables/bands/bands_end_of_sim.csv` (skill ensemble-bands; convention **{p10}**)."
    )
    lines.append("")
    if not highlights:
        lines.append("_No band rows found. Open issue if stage requires bands._")
    else:
        lines.append("| KEY | DATE_MAX | MEAN | P10 | P50 | P90 | COUNT |")
        lines.append("|---|---|---|---|---|---|---|")
        for h in highlights:
            lines.append(
                f"| {h['KEY']} | {h['DATE_MAX']} | {h['MEAN']} | {h['P10']} | "
                f"{h['P50']} | {h['P90']} | {h['COUNT']} |"
            )
    bm = arts.get("bands_manifest") or {}
    if bm:
        lines.append("")
        lines.append(
            f"Bands manifest: vector_mode=`{bm.get('vector_mode')}`, "
            f"n_realizations=`{bm.get('n_realizations')}`, "
            f"n_keys_banded=`{bm.get('n_keys_banded')}`."
        )
    lines.append("")
    lines.append("## Scenario QC matrix")
    lines.append("")
    matrix = arts.get("scenario_matrix")
    if not matrix:
        lines.append("_scenario_matrix.json not found under deliverables/scenario_qc/._")
    else:
        lines.append(
            f"- any_fail: **{matrix.get('any_fail')}** "
            f"(n_scenarios={matrix.get('n_scenarios')}, p10_p90={matrix.get('p10_p90')})"
        )
        lines.append("")
        lines.append(
            f"| scenario_id | status | flags | {matrix.get('headline_vector') or 'FOPT'} rel_delta |"
        )
        lines.append("|---|---|---|---|")
        for row in matrix.get("rows") or []:
            lines.append(
                f"| {row.get('scenario_id')} | {row.get('status')} | "
                f"{row.get('flags') or '—'} | {row.get('headline_rel_delta') or row.get('fopt_rel_delta') or '—'} |"
            )
        if arts.get("scenario_matrix_md"):
            lines.append("")
            lines.append("Matrix markdown path: `deliverables/scenario_qc/scenario_matrix.md`")
    lines.append("")
    lines.append("## Plot evidence (paths only)")
    lines.append("")
    lines.append("Plots are evidence, not a pass/fail gate. Tables and scenario-qc decide status.")
    lines.append("")
    if not arts.get("plot_paths"):
        lines.append("_No plot PNGs found._")
    else:
        for p in arts["plot_paths"]:
            lines.append(f"- `{p}`")
    lines.append("")
    lines.append("## Inputs / uncertainty register")
    lines.append("")
    ureg = arts.get("uncertainty_register_csv") or arts.get("uncertainty_register_md")
    if ureg:
        um = arts.get("uncertainty_register_manifest") or {}
        lines.append(
            f"- Register: `{ureg}` "
            f"(rows={um.get('n_rows', '?')}, ok={um.get('n_ok', '?')}, "
            f"gap={um.get('n_gap', '?')}; skill uncertainty-register)."
        )
        if arts.get("uncertainty_register_md"):
            lines.append(f"- Markdown: `{arts['uncertainty_register_md']}`")
        lines.append(
            "- Priors are project min/max (UNIFORM/LOGUNIF). "
            "Do not invent P10/P50/P90 from design-matrix samples."
        )
    else:
        lines.append(
            "_No `deliverables/uncertainty_register/register.csv` yet. "
            "Run skill `uncertainty-register` when priors must appear in the pack._"
        )
    lines.append("")
    lines.append("## Open issues")
    lines.append("")
    lines.append(
        "Mandatory section. Missing required vectors and scenario QC fails must appear here. "
        "Do not silently pass a gate while this list is non-empty (strict mode)."
    )
    lines.append("")
    if not issues:
        lines.append("_None detected from current deliverables._")
    else:
        for i, issue in enumerate(issues, 1):
            lines.append(
                f"{i}. **[{issue['severity']}]** `{issue['id']}` ({issue['source']}): {issue['detail']}"
            )
    lines.append("")
    lines.append("## Artifact checklist")
    lines.append("")
    lines.append("| Artifact | Present |")
    lines.append("|---|---|")
    lines.append(f"| profiles | {'yes' if arts['has_profiles'] else 'NO'} |")
    lines.append(f"| bands | {'yes' if arts['has_bands'] else 'NO'} |")
    lines.append(f"| scenario_qc | {'yes' if arts['has_scenario_qc'] else 'NO'} |")
    lines.append(f"| plots | {'yes' if arts['has_plots'] else 'NO'} |")
    lines.append(f"| uncertainty_register | {'yes' if arts.get('has_uncertainty_register') else 'NO'} |")
    lines.append("")
    return "\n".join(lines)


def render_html(md_title: str, outline: dict[str, Any], md_body: str) -> str:
    """Minimal HTML mirror for browser pre-read; content from outline + escaped md."""
    issues = outline.get("open_issues") or []
    issue_lis = (
        "".join(
            f"<li><strong>[{html.escape(i['severity'])}]</strong> "
            f"<code>{html.escape(i['id'])}</code>: {html.escape(i['detail'])}</li>"
            for i in issues
        )
        or "<li><em>None</em></li>"
    )
    band_rows = outline.get("band_highlights") or []
    band_trs = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(h.get(k, '')))}</td>"
            for k in ("KEY", "DATE_MAX", "MEAN", "P10", "P50", "P90", "COUNT")
        )
        + "</tr>"
        for h in band_rows
    )
    plots = (
        "".join(f"<li><code>{html.escape(p)}</code></li>" for p in (outline.get("plot_paths") or []))
        or "<li><em>None</em></li>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{html.escape(md_title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 0.35rem 0.5rem; text-align: left; }}
.issues {{ background: #fff8e6; padding: 1rem; border: 1px solid #e6c200; }}
code {{ font-size: 0.9em; }}
</style>
</head>
<body>
<h1>{html.escape(md_title)}</h1>
<p><strong>P10/P90: {html.escape(str(outline.get("p10_p90")))}</strong> —
generated {html.escape(str(outline.get("generated_at")))}</p>
<h2>Band highlights (scripted)</h2>
<table>
<thead><tr><th>KEY</th><th>DATE_MAX</th><th>MEAN</th><th>P10</th><th>P50</th><th>P90</th><th>COUNT</th></tr></thead>
<tbody>{band_trs}</tbody>
</table>
<h2>Open issues</h2>
<div class="issues"><ul>{issue_lis}</ul></div>
<h2>Plot paths</h2>
<ul>{plots}</ul>
<p>Full markdown is in <code>preread.md</code> beside this file.</p>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble QA/QC pre-read from deliverables.")
    add_contract_args(parser)
    parser.add_argument(
        "--stage",
        default=None,
        help="A key of contract gate_stages (default: contract gate_default_stage)",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Default: deliverables/gate_packets/<stage>/",
    )
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(args.contract, args.profile)
        stage, _stage_cfg = contract_lib.select_gate_stage(contract, args.stage)
        deliv = deliverables_root(contract)
    except (ContractError, ValueError) as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2
    contract_label = Path(contract.sources[-1]) if contract.sources else Path("site.contract.yml")

    if not deliv.is_dir():
        sys.stderr.write(f"error: deliverables root missing: {deliv}\n")
        return 2

    arts = collect_artifacts(deliv)
    # Critical: need at least contract + deliverables; profiles or bands for a useful pack
    if not arts["has_profiles"] and not arts["has_bands"]:
        sys.stderr.write(
            "error: neither profiles nor bands found under deliverables; refuse empty pre-read\n"
        )
        return 2

    p10 = str(contract.get("p10_p90") or "unknown")
    highlights = band_highlights(
        arts.get("bands_end_of_sim") or [],
        p10,
        contract.vectors("headline", list(DEFAULT_HIGHLIGHTS)),
        contract.get_dotted("wells.plot_default"),
    )
    issues = build_open_issues(contract, arts, stage)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    outdir = args.outdir
    if outdir is None:
        outdir = deliv / "gate_packets" / stage
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    outline: dict[str, Any] = {
        "skill": "qa-qc-preread",
        "stage": stage,
        "contract": str(contract_label),
        "contract_sources": list(contract.sources),
        "profile": contract.profile_name,
        "project": contract.get("project"),
        "simulator": contract.simulator_name,
        "p10_p90": p10,
        "p10_p90_note": (
            "numerical = 10th/50th/90th percentile of ensemble (low=P10, high=P90). Not exceedance."
            if p10 == "numerical"
            else f"convention={p10}"
        ),
        "generated_at": generated_at,
        "band_highlights": highlights,
        "scenario_matrix_summary": {
            "any_fail": (arts.get("scenario_matrix") or {}).get("any_fail"),
            "n_scenarios": (arts.get("scenario_matrix") or {}).get("n_scenarios"),
            "rows": (arts.get("scenario_matrix") or {}).get("rows"),
        },
        "plot_paths": arts.get("plot_paths") or [],
        "open_issues": issues,
        "artifacts_present": {
            "profiles": arts["has_profiles"],
            "bands": arts["has_bands"],
            "scenario_qc": arts["has_scenario_qc"],
            "plots": arts["has_plots"],
            "uncertainty_register": arts.get("has_uncertainty_register", False),
        },
        "outputs": {},
    }

    md = render_markdown(
        stage=stage,
        contract=contract,
        contract_path=contract_label,
        arts=arts,
        highlights=highlights,
        issues=issues,
        generated_at=generated_at,
    )
    md_path = outdir / "preread.md"
    md_path.write_text(md, encoding="utf-8")

    outline_path = outdir / "preread_outline.json"
    html_path = outdir / "preread.html"
    outline["outputs"] = {
        "preread_md": str(md_path),
        "preread_outline_json": str(outline_path),
        "preread_html": str(html_path),
    }
    outline_path.write_text(json.dumps(outline, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(
        render_html(f"QA/QC pre-read — {stage}", outline, md),
        encoding="utf-8",
    )

    readme = outdir / "README.md"
    readme.write_text(
        f"# Gate packet folder — {stage}\n\n"
        f"- **P10/P90:** {p10}\n"
        f"- `preread.md` — human pre-read\n"
        f"- `preread_outline.json` — machine outline\n"
        f"- `preread.html` — simple HTML mirror\n"
        f"- Open issues: {len(issues)}\n"
        f"- Assembled by `qa-qc-preread` (no Flow/ERT launch)\n",
        encoding="utf-8",
    )

    sys.stdout.write(
        f"qa-qc-preread: wrote {md_path}\n  stage={stage} p10_p90={p10} open_issues={len(issues)}\n"
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
