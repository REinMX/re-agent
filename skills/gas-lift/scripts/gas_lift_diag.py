#!/usr/bin/env python3
"""Gas-lift diagnostics from a finished run: lift gas per well and group, group lift limits, wells that get no gain.

  gas_lift_diag.py --case CASE.DATA [--deck CASE.DATA] [--out folder] [--rel-tol 0.01]

Reads SUMMARY (resdata) and the gas-lift keywords of the deck (gas_lift_deck.py). Uses WGLIR, GGLIR, FGLIR
(lift gas rate), WOPR, WALQ when present. A vector that is not in the SUMMARY is reported missing, never zero.
Rates are averages over each SUMMARY interval; volumes are rate x interval length.

Per well: lift gas volume, oil volume, average lift gas per oil (volume ratio: an average, not a marginal value),
days on lift, days at the WLIFTOPT maximum, days with lift gas and no oil, days on optimization (WLIFTOPT YES).
Flags: no_lift_while_optimized (the optimizer gave the well no lift gas, because lift gave no gain above the
economic gradient or the well did not need it), lift_without_oil, at_well_max.
Per group (GLIFTOPT): the maximum lift gas (item 2) against GGLIR (FGLIR for FIELD), and the maximum total gas
(item 3) against GGPR + GGLIR, within rel_tol. Status honored, violated or missing.
Marginal oil per lift gas needs runs at several lift-gas levels: gas_lift_sweep.py.
Exit: 0 written, 2 bad input or unreadable SUMMARY.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gas_lift_deck as gld  # noqa: E402

TOTAL_GAS_NOTE = ("GLIFTOPT item 3 is compared with GGPR + GGLIR (produced gas plus lift gas). Whether the "
                  "simulator's GGPR already holds the lift gas is not checked: confirm for ECLIPSE100.")  # fmt: skip


class DiagError(ValueError):
    pass


class Run:
    """SUMMARY of one case: time-weighted rates."""

    def __init__(self, base: Path) -> None:
        from resdata.summary import Summary

        if not base.with_suffix(".SMSPEC").is_file() or not base.with_suffix(".UNSMRY").is_file():
            raise DiagError(f"no SMSPEC/UNSMRY at {base}")
        self.base = base
        s = Summary(str(base))
        self.start = s.start_date if isinstance(s.start_date, date) else s.start_date.date()
        self.days = [float(d) for d in s.days]
        self.dt = [self.days[0]] + [b - a for a, b in zip(self.days, self.days[1:], strict=False)]
        self.values = {k: [float(v) for v in s.numpy_vector(k)] for k in s.keys()}

    def has(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str) -> list[float] | None:
        return self.values.get(key)

    def volume(self, key: str) -> float | None:
        v = self.get(key)
        return None if v is None else sum(r * d for r, d in zip(v, self.dt, strict=True))

    def interval_start(self, i: int) -> date:
        return self.start + timedelta(days=self.days[i] - self.dt[i])

    def wells(self, family: str) -> list[str]:
        return sorted(k.split(":", 1)[1] for k in self.values if k.startswith(family + ":"))


def summary_base(path: Path) -> Path:
    return path.with_suffix("") if path.suffix.upper() in (".DATA", ".SMSPEC", ".UNSMRY") else path


def well_rows(run: Run, deck: gld.GasLiftDeck | None, tol: float) -> list[dict[str, Any]]:
    rows = []
    declared = set()
    if deck:
        declared = {w for r in deck.wliftopt for w in r["wells"]} | {
            w for r in deck.lift_targets for w in r["wells"]
        }
    lifted = {w for w in run.wells("WGLIR") if any(v > 0 for v in run.get(f"WGLIR:{w}"))}
    for well in sorted(declared | lifted):
        lift = run.get(f"WGLIR:{well}")
        oil = run.get(f"WOPR:{well}")
        row: dict[str, Any] = {"well": well, "status": "ok", "flags": [], "missing": []}
        if lift is None:
            row["missing"].append(f"WGLIR:{well}")
        if oil is None:
            row["missing"].append(f"WOPR:{well}")
        if row["missing"]:
            row["status"] = "missing"
            rows.append(row)
            continue
        lift_vol = sum(r * d for r, d in zip(lift, run.dt, strict=True))
        oil_vol = sum(r * d for r, d in zip(oil, run.dt, strict=True))
        on = [i for i, r in enumerate(lift) if r > 0]
        opt_days = at_max = no_oil = 0.0
        max_rates = set()
        for i, d in enumerate(run.dt):
            settings = deck.well_settings(well, run.interval_start(i)) if deck else None
            if settings and settings["optimize"] == "YES":
                opt_days += d
                cap = settings["max_rate"]
                if cap is None:
                    table = deck.vfp.get(int(deck.vfp_table(well) or 0))
                    cap = float(table["ALQ"].max()) if table is not None else None
                if cap:
                    max_rates.add(float(cap))
                    if lift[i] >= float(cap) * (1 - tol):
                        at_max += d
            if lift[i] > 0 and oil[i] <= 0:
                no_oil += d
        row.update(lift_gas_volume=lift_vol, oil_volume=oil_vol,
                   lift_gas_per_oil=lift_vol / oil_vol if oil_vol > 0 else None,
                   days_on_lift=sum(run.dt[i] for i in on),
                   mean_lift_rate_on=(sum(lift[i] * run.dt[i] for i in on) / sum(run.dt[i] for i in on)) if on else None,
                   days_optimized=opt_days if deck else None, max_rate_setting=sorted(max_rates) or None,
                   days_at_max=at_max if deck else None, days_lift_without_oil=no_oil)  # fmt: skip
        if deck and opt_days > 0 and lift_vol == 0 and oil_vol > 0:
            row["flags"].append("no_lift_while_optimized")
        if no_oil > 0:
            row["flags"].append("lift_without_oil")
        if at_max > 0:
            row["flags"].append("at_well_max")
        if run.has(f"WALQ:{well}"):
            row["walq_max"] = max(run.get(f"WALQ:{well}"))
        rows.append(row)
    return rows


def group_rows(run: Run, deck: gld.GasLiftDeck | None, tol: float) -> list[dict[str, Any]]:
    if not deck:
        return []
    rows = []
    for n, limit in enumerate(deck.gliftopt):
        later = [g["date"] for g in deck.gliftopt[n + 1 :] if g["group"] == limit["group"]]
        until = min(later) if later else None
        g = limit["group"]
        lift_key = "FGLIR" if g == "FIELD" else f"GGLIR:{g}"
        gas_key = "FGPR" if g == "FIELD" else f"GGPR:{g}"
        for kind, cap in (("max_lift", limit["max_lift"]), ("max_total_gas", limit["max_total_gas"])):
            if cap is None:
                continue
            keys = [lift_key] if kind == "max_lift" else [gas_key, lift_key]
            row: dict[str, Any] = {"group": g, "limit": kind, "value": float(cap), "from": str(limit["date"]),
                                   "until": str(until) if until else None, "vectors": "+".join(keys)}  # fmt: skip
            missing = [k for k in keys if not run.has(k)]
            if missing:
                row.update(status="missing", missing=missing)
                rows.append(row)
                continue
            steps = [i for i in range(len(run.days)) if run.interval_start(i) >= limit["date"]
                     and (until is None or run.interval_start(i) < until)]  # fmt: skip
            used = [sum(run.get(k)[i] for k in keys) for i in steps]
            over = [i for i, u in zip(steps, used, strict=True) if u > float(cap) * (1 + tol)]
            row.update(steps=len(steps), steps_over=len(over), max_used=max(used) if used else None,
                       max_ratio=(max(used) / float(cap)) if used and cap else None,
                       first_over=str(run.start + timedelta(days=run.days[over[0]])) if over else None,
                       status="no_steps" if not steps else "violated" if over else "honored")  # fmt: skip
            rows.append(row)
    return rows


def field_block(run: Run, wells: list[dict]) -> dict[str, Any]:
    fglir = run.volume("FGLIR")
    well_sum = sum(w["lift_gas_volume"] for w in wells if w["status"] == "ok")
    complete = all(w["status"] == "ok" for w in wells) and bool(wells)
    return {"well_sum_over_fglir": (well_sum / fglir) if complete and fglir else None,"lift_gas_volume": fglir, "lift_gas_volume_status": "ok" if fglir is not None else "missing (no FGLIR)",
            "oil_volume": run.volume("FOPR"), "sum_of_well_lift_gas": well_sum if complete else None,
            "well_sum_matches_fglir": (abs(well_sum - fglir) <= 1e-6 * max(1.0, abs(fglir))) if complete and fglir is not None else None,
            "lift_gas_per_oil": (fglir / run.volume("FOPR")) if fglir is not None and run.volume("FOPR") else None}  # fmt: skip


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        w = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if r.get(k) is None else ";".join(map(str, r[k])) if isinstance(r.get(k), list) else r[k]
                        for k in columns})  # fmt: skip


def fmt(v: Any) -> str:
    if v is None:
        return "missing"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.4g}"
    return str(v)


def markdown(result: dict[str, Any]) -> str:
    f = result["field"]
    lines = [f"# Gas-lift diagnostics: {result['case']}", "",
             f"- Field lift gas volume (FGLIR x time): {fmt(f['lift_gas_volume'])} ({f['lift_gas_volume_status']})",
             f"- Field oil volume: {fmt(f['oil_volume'])}; average lift gas per oil: {fmt(f['lift_gas_per_oil'])} "
             "(volume ratio, not marginal)",
             f"- Sum of WGLIR volumes matches FGLIR: {fmt(f['well_sum_matches_fglir'])} (ratio {fmt(f['well_sum_over_fglir'])})", "",
             "## Wells", "", "| well | lift gas | oil | lift gas / oil | days on lift | days optimized | days at max | flags |",
             "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for w in result["wells"]:
        if w["status"] != "ok":
            lines.append(f"| {w['well']} | missing ({', '.join(w['missing'])}) | | | | | | |")
            continue
        lines.append(f"| {w['well']} | {fmt(w['lift_gas_volume'])} | {fmt(w['oil_volume'])} | {fmt(w['lift_gas_per_oil'])} | "
                     f"{fmt(w['days_on_lift'])} | {fmt(w['days_optimized'])} | {fmt(w['days_at_max'])} | {', '.join(w['flags']) or '-'} |")  # fmt: skip
    lines += ["", "## Group lift limits (GLIFTOPT)", ""]
    for g in result["groups"]:
        if g["status"] == "missing":
            lines.append(
                f"- {g['group']} {g['limit']} {g['value']:g} from {g['from']}: missing {g['missing']}"
            )
        else:
            lines.append(f"- {g['group']} {g['limit']} {g['value']:g} from {g['from']} ({g['vectors']}): {g['status']}, "
                         f"{g['steps_over']}/{g['steps']} steps over, max {fmt(g['max_used'])} ({fmt(g['max_ratio'])} of the limit)")  # fmt: skip
    if not result["groups"]:
        lines.append("- no GLIFTOPT in the deck")
    lines += ["", "## Notes", ""] + [f"- {n}" for n in result["notes"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--case", type=Path, required=True, help="DATA file or SUMMARY base of the run")
    parser.add_argument(
        "--deck", type=Path, help="deck for the gas-lift settings (default: --case when it is a DATA file)"
    )
    parser.add_argument(
        "--rel-tol", type=float, default=0.01, help="tolerance for limits and maxima (default 0.01)"
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        run = Run(summary_base(args.case))
        deck_path = args.deck or (args.case if args.case.suffix.upper() == ".DATA" else None)
        deck = gld.read_deck(deck_path) if deck_path else None
        wells = well_rows(run, deck, args.rel_tol)
        groups = group_rows(run, deck, args.rel_tol)
    except (DiagError, gld.DeckError, OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    notes = ["Lift gas rates WGLIR/GGLIR/FGLIR are interval averages; volumes are rate x interval length.",
             "lift_gas_per_oil is an average; marginal oil per lift gas comes from gas_lift_sweep.py.",
             "Default rel_tol 0.01 is an engineering choice (not confirmed)."]  # fmt: skip
    if any(g["limit"] == "max_total_gas" for g in groups):
        notes.append(TOTAL_GAS_NOTE)
    ratio = field_block(run, wells)["well_sum_over_fglir"]
    if ratio is not None and abs(ratio - 1) > 1e-6:
        why = (
            " The deck has WEFAC/GEFAC: efficiency factors scale group and field rates, not well rates. Well "
            "lift gas and oil volumes here are WGLIR and WOPR x time, so they are above the volumes in WOPT/FOPT."
            if deck and ({"WEFAC", "GEFAC"} & deck.schedule_keywords)
            else ""
        )
        notes.append(f"sum of WGLIR volumes is {ratio:.4g} x the FGLIR volume.{why}")
    if deck is None:
        notes.append("no deck given: optimization days, well maxima and group limits are not assessed")
    if not run.has("FGLIR"):
        notes.append(
            "FGLIR is not in the SUMMARY: field lift gas is missing (add FGLIR, GGLIR, WGLIR to SUMMARY)"
        )
    result = {"schema_version": 1, "case": str(args.case), "deck": str(deck_path) if deck_path else None,
              "rel_tol": args.rel_tol, "field": field_block(run, wells), "wells": wells, "groups": groups, "notes": notes}  # fmt: skip
    out = args.out or summary_base(args.case).parent / "gas_lift_diag"
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "wells.csv", wells, ["well", "status", "lift_gas_volume", "oil_volume", "lift_gas_per_oil", "days_on_lift",
                                         "mean_lift_rate_on", "days_optimized", "max_rate_setting", "days_at_max",
                                         "days_lift_without_oil", "flags", "missing"])  # fmt: skip
    write_csv(out / "groups.csv", groups, ["group", "limit", "value", "from", "until", "vectors", "status", "steps",
                                           "steps_over", "max_used", "max_ratio", "first_over", "missing"])  # fmt: skip
    (out / "gas_lift_diag.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (out / "gas_lift_diag.md").write_text(markdown(result), encoding="utf-8")
    print(f"gas_lift_diag: {len(wells)} well(s), {len(groups)} group limit(s); wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
