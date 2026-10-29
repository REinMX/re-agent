#!/usr/bin/env python3
"""Lift-gas sweep: one scenario-build spec at several lift-gas levels -> decks -> oil gain against gas used.

  gas_lift_sweep.py build   --spec scenario.yml --variable field_lift --levels 1e5,2e5,4e5 --out sweep/ [--target ECLIPSE100]
  gas_lift_sweep.py build   --spec scenario.yml --levels-csv levels.csv --out sweep/       (columns: label, <variable>...)
  (run each sweep/levels/<label>/scenario deck: Flow on the box, ERT on the cluster; this script runs nothing)
  gas_lift_sweep.py collect --sweep sweep/ [--reference CASE.DATA] [--rel-tol 0.01]

build: each level is an Everest-style control file (the spec's other variables keep their initial values) rendered
with scenario_build.py into sweep/levels/<label>/scenario. Levels must lie inside the variable bounds. A level
whose render fails (declaration check or target support) is listed with its exit code.
The variable can drive any numeric item: WELTARG LIFT (ALQ of one well), WLIFTOPT maximum lift rate, or
GLIFTOPT maximum lift gas of a group (total lift-gas level).

collect: reads the SUMMARY next to each level deck (runmeta deck_data). Per level: lift gas volume (FGLIR x
time), oil volume (FOPT), gas and water produced, change against the reference (the --reference case, else the
level with the least lift gas), and the marginal oil per lift gas between neighbouring levels (in build order), compared with the LIFTOPT economic gradient of the deck when there is one. Per well: oil and lift
gas at each level; no_benefit when the well's lift gas rises but its oil gains less than rel_tol.
A level without SUMMARY or without a vector is missing (never zero), and the marginal values next to it are missing.
Exit: 0 written, 1 build: a level failed to render, 2 bad input.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scenario-build" / "scripts"))
import gas_lift_deck as gld  # noqa: E402
import scenario_build as sb  # noqa: E402
from gas_lift_diag import DiagError, Run, summary_base  # noqa: E402

ds = sb.ds
contract_lib = sb.contract_lib
LABEL = re.compile(r"^[A-Za-z0-9_.\-]+$")


class SweepError(ValueError):
    pass


def read_levels(args: argparse.Namespace, spec: sb.Spec) -> list[dict[str, Any]]:
    if bool(args.levels) == bool(args.levels_csv):
        raise SweepError("give --variable with --levels, or --levels-csv")
    levels = []
    if args.levels:
        if not args.variable or args.variable not in spec.variables:
            raise SweepError(f"--variable must be one of the spec variables {sorted(spec.variables)}")
        for raw in args.levels.split(","):
            value = float(raw)
            levels.append(
                {"label": f"{args.variable}_{value:g}".replace("+", ""), "values": {args.variable: value}}
            )
    else:
        with args.levels_csv.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                label = (row.pop("label", "") or "").strip()
                unknown = sorted(set(row) - set(spec.variables))
                if unknown:
                    raise SweepError(f"{args.levels_csv.name}: columns {unknown} are not spec variables")
                levels.append(
                    {"label": label, "values": {k: float(v) for k, v in row.items() if v not in ("", None)}}
                )
    for lv in levels:  # check every level before rendering any, so a bad level leaves no half-built folder
        for name, value in lv["values"].items():
            var = spec.variables[name]
            if not var.low <= value <= var.high:
                raise SweepError(
                    f"level {lv['label']}: {name} = {value:g} is outside [{var.low:g}, {var.high:g}]"
                )
    labels = [lv["label"] for lv in levels]
    if not levels or len(set(labels)) != len(labels) or not all(LABEL.match(x) for x in labels):
        raise SweepError(f"levels need unique labels of letters, digits, _ . -: {labels}")
    return levels


def build(args: argparse.Namespace) -> int:
    target, source = ds.resolve_target(args)
    if target not in ds.TARGETS:
        raise SweepError(f"simulator {target!r} is not one of {sorted(ds.TARGETS)}")
    base = sb.load_spec(args.spec)
    levels = read_levels(args, base)
    if args.out.exists() and any(args.out.iterdir()):
        raise SweepError(f"output folder is not empty: {args.out}")
    rows = []
    for level in levels:
        folder = args.out / "levels" / level["label"]
        folder.mkdir(parents=True)
        values = {n: v.initial for n, v in base.variables.items()} | level["values"]
        controls = folder / "controls.json"
        controls.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        spec = sb.load_spec(args.spec, controls)
        spec.scenario_id = f"{base.scenario_id}__{level['label']}"
        report, code = sb.build(spec, folder / "scenario", target, source)
        meta = contract_lib._yaml().safe_load(
            (folder / "scenario" / "runmeta.yml").read_text(encoding="utf-8")
        )
        rows.append({"label": level["label"], "values": values, "scenario_id": spec.scenario_id,
                     "deck": meta["deck_data"], "render_exit": code,
                     "declaration_check": report["declaration_check"]["status"],
                     "deck_support": report["deck_support"]["verdicts"]})  # fmt: skip
    manifest = {"schema_version": 1, "spec": str(args.spec.resolve()), "spec_sha256": base.sha256, "target": target,
                "variables": {n: {"initial": v.initial, "min": v.low, "max": v.high} for n, v in base.variables.items()},
                "levels": rows}  # fmt: skip
    (args.out / "sweep.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for r in rows:
        print(
            f"{r['label']}: {r['values']} -> {r['deck']} (render exit {r['render_exit']}, box Flow {r['deck_support']['box_flow']})"
        )
    print(
        f"wrote {args.out / 'sweep.json'}; run each deck, then: gas_lift_sweep.py collect --sweep {args.out}"
    )
    return 1 if any(r["render_exit"] for r in rows) else 0


def end_value(run: Run, total: str, rate: str) -> tuple[float | None, str | None]:
    if run.has(total):
        return run.get(total)[-1], total
    if run.has(rate):
        return run.volume(rate), f"{rate} x time"
    return None, None


def measure(label: str, deck: Path | None, values: dict | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "label": label,
        "values": values,
        "deck": str(deck) if deck else None,
        "missing": [],
    }
    try:
        run = Run(summary_base(deck))
    except (DiagError, OSError) as error:
        row.update(status="missing", missing=[str(error)], wells={})
        return row
    row["lift_gas_volume"] = run.volume("FGLIR")
    if row["lift_gas_volume"] is None:
        row["missing"].append("FGLIR")
    for name, total, rate in (("oil", "FOPT", "FOPR"), ("gas", "FGPT", "FGPR"), ("water", "FWPT", "FWPR")):
        row[f"{name}_volume"], row[f"{name}_basis"] = end_value(run, total, rate)
        if row[f"{name}_volume"] is None:
            row["missing"].append(total)
    # Well oil and lift gas on one basis: WOPR x time and WGLIR x time. WOPT holds the WEFAC efficiency factor and
    # WGLIR does not, so WOPT against WGLIR x time would scale a well's marginal oil by its WEFAC.
    row["wells"] = {w: {"oil": run.volume(f"WOPR:{w}"), "lift": run.volume(f"WGLIR:{w}")}
                    for w in run.wells("WGLIR")}  # fmt: skip
    row["status"] = "missing" if {"FGLIR", "FOPT"} & set(row["missing"]) else "ok"
    return row


def collect(args: argparse.Namespace) -> int:
    manifest = json.loads((args.sweep / "sweep.json").read_text(encoding="utf-8"))
    rows = [measure(lv["label"], Path(lv["deck"]), lv["values"]) for lv in manifest["levels"]]
    reference = measure("reference", args.reference, None) if args.reference else None
    gradient = None
    try:
        deck = gld.read_deck(Path(manifest["levels"][0]["deck"]))
        grads = [o["economic_gradient"] for o in deck.liftopt if o["economic_gradient"]]
        gradient = grads[-1] if grads else None
    except (gld.DeckError, OSError, ValueError, RuntimeError):
        pass
    ok = [r for r in rows if r["status"] == "ok"]
    ref = (
        reference
        if reference and reference["status"] == "ok"
        else (min(ok, key=lambda r: r["lift_gas_volume"]) if ok else None)
    )
    for r in rows + ([reference] if reference else []):
        good = r["status"] == "ok" and ref is not None
        r["delta_oil"] = r["oil_volume"] - ref["oil_volume"] if good else None
        r["delta_lift_gas"] = r["lift_gas_volume"] - ref["lift_gas_volume"] if good else None
    ordered = rows  # build order, the order the user gave the levels in
    for prev, cur in zip([None] + ordered[:-1], ordered, strict=True):
        cur["marginal_oil_per_lift_gas"] = None
        cur["marginal_from"] = prev["label"] if prev else None
        cur["marginal_note"] = None
        if prev and prev["status"] == cur["status"] == "ok":
            dg = cur["lift_gas_volume"] - prev["lift_gas_volume"]
            if dg > 0:
                cur["marginal_oil_per_lift_gas"] = (cur["oil_volume"] - prev["oil_volume"]) / dg
            else:
                cur["marginal_note"] = f"lift gas volume did not rise from {prev['label']} ({dg:,.0f})"
        m = cur["marginal_oil_per_lift_gas"]
        cur["below_economic_gradient"] = (m < gradient) if (m is not None and gradient) else None
    wells = well_benefit(ok, args.rel_tol)
    result = {"schema_version": 1, "sweep": str(args.sweep), "reference": ref["label"] if ref else None,
              "economic_gradient": gradient, "levels": ordered, "reference_case": reference, "wells": wells,
              "notes": ["Marginal oil per lift gas = d(FOPT) / d(FGLIR x time) between neighbouring levels in build order, sm3/sm3 "
                        "in METRIC decks (x1000 for sm3 oil per 1000 sm3 gas).",
                        "Per-well values are WOPR x time against WGLIR x time (well rates without WEFAC); field values "
                        "use FOPT and FGLIR, which hold WEFAC.",
                        "Single deterministic runs: no P10/P50/P90. Run the sweep per realization for an ensemble "
                        "(numerical percentiles, P10 = low).",
                        f"rel_tol {args.rel_tol} for no_benefit is an engineering choice (not confirmed)."]}  # fmt: skip
    out = args.out or args.sweep
    out.mkdir(parents=True, exist_ok=True)
    cols = ["label", "status", "lift_gas_volume", "oil_volume", "gas_volume", "water_volume", "delta_lift_gas", "delta_oil",
            "marginal_from", "marginal_oil_per_lift_gas", "marginal_note", "below_economic_gradient", "missing", "deck"]  # fmt: skip
    with (out / "sweep_results.csv").open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=cols + ["values"], extrasaction="ignore")
        w.writeheader()
        for r in ordered + ([reference] if reference else []):
            w.writerow({k: "" if r.get(k) is None else json.dumps(r[k]) if isinstance(r.get(k), dict | list) else r[k]
                        for k in cols + ["values"]})  # fmt: skip
    with (out / "sweep_wells.csv").open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(
            h,
            fieldnames=["well", "lift_gain", "oil_gain", "oil_low", "verdict", "marginal", "levels"],
            extrasaction="ignore",
        )
        w.writeheader()
        for r in wells:
            w.writerow({**r, "levels": json.dumps(r["levels"]), "marginal": json.dumps(r["marginal"])})
    (out / "sweep_results.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out / "sweep_results.md").write_text(markdown(result), encoding="utf-8")
    print(f"gas_lift_sweep collect: {len(ok)}/{len(rows)} level(s) with results; wrote {out}")
    return 0


def well_benefit(ok: list[dict], tol: float) -> list[dict]:
    if len(ok) < 2:
        return []
    low, high = ok[0], ok[-1]  # first and last level in build order
    out = []
    for well in sorted(set().union(*(r["wells"] for r in ok))):
        lo, hi = low["wells"].get(well, {}), high["wells"].get(well, {})
        row: dict[str, Any] = {
            "well": well,
            "levels": {r["label"]: r["wells"].get(well) for r in ok},
            "marginal": {},
        }
        for a, b in zip(ok, ok[1:], strict=False):
            wa, wb = a["wells"].get(well, {}), b["wells"].get(well, {})
            if (
                None not in (wa.get("oil"), wb.get("oil"), wa.get("lift"), wb.get("lift"))
                and wb["lift"] > wa["lift"]
            ):
                row["marginal"][b["label"]] = (wb["oil"] - wa["oil"]) / (wb["lift"] - wa["lift"])
        if None in (lo.get("oil"), hi.get("oil"), lo.get("lift"), hi.get("lift")):
            row["verdict"] = "missing"
        else:
            row.update(lift_gain=hi["lift"] - lo["lift"], oil_gain=hi["oil"] - lo["oil"], oil_low=lo["oil"])
            if row["lift_gain"] == 0:
                row["verdict"] = "no_extra_lift"
            elif row["lift_gain"] < 0:
                row["verdict"] = "less_lift"
            elif row["oil_gain"] <= tol * max(lo["oil"], 1e-9):
                row["verdict"] = "no_benefit"
            else:
                row["verdict"] = "benefits"
        out.append(row)
    return out


def fmt(v: Any) -> str:
    if v is None:
        return "missing"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.4g}"
    return str(v)


def markdown(result: dict[str, Any]) -> str:
    lines = [f"# Lift-gas sweep: {result['sweep']}", "", f"- Reference: {result['reference']}",
             f"- LIFTOPT economic gradient in the deck: {fmt(result['economic_gradient'])}", "",
             "| level | lift gas volume | oil volume | delta oil vs ref | delta lift gas vs ref | marginal oil / lift gas | below gradient |",
             "|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in result["levels"] + ([result["reference_case"]] if result["reference_case"] else []):
        if r["status"] != "ok":
            lines.append(f"| {r['label']} | missing ({'; '.join(r['missing'])}) | | | | | |")
            continue
        lines.append(f"| {r['label']} | {fmt(r['lift_gas_volume'])} | {fmt(r['oil_volume'])} | {fmt(r['delta_oil'])} | "
                     f"{fmt(r['delta_lift_gas'])} | {fmt(r.get('marginal_oil_per_lift_gas'))} | {fmt(r.get('below_economic_gradient'))} |")  # fmt: skip
    lines += [f"- {r['label']}: {r['marginal_note']}" for r in result["levels"] if r.get("marginal_note")]
    lines += ["", "## Wells (first to last level)", ""]
    for w in result["wells"]:
        if w["verdict"] == "no_extra_lift" and not w.get("oil_gain"):
            continue
        marg = ", ".join(f"{k} {fmt(v)}" for k, v in w["marginal"].items())
        lines.append(f"- {w['well']}: {w['verdict']}" + (f" (lift gas {fmt(w['lift_gain'])}, oil {fmt(w['oil_gain'])})"
                                                         if "lift_gain" in w else "") + (f"; marginal oil / lift gas: {marg}" if marg else ""))  # fmt: skip
    lines += ["", "## Notes", ""] + [f"- {n}" for n in result["notes"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--spec", type=Path, required=True)
    b.add_argument("--variable")
    b.add_argument("--levels", help="comma-separated values of --variable")
    b.add_argument("--levels-csv", type=Path)
    b.add_argument("--out", type=Path, required=True)
    contract_lib.add_contract_args(b)
    b.add_argument("--target", help=f"override simulator.name: one of {sorted(ds.TARGETS)}")
    c = sub.add_parser("collect")
    c.add_argument("--sweep", type=Path, required=True)
    c.add_argument(
        "--reference", type=Path, help="DATA file of a reference run (for example the deck without the sweep)"
    )
    c.add_argument("--rel-tol", type=float, default=0.01)
    c.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        return build(args) if args.command == "build" else collect(args)
    except (SweepError, sb.SpecError, OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
