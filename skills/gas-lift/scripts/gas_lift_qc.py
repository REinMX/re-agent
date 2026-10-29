#!/usr/bin/env python3
"""Check the gas-lift setup of a deck before a run: VFPPROD ALQ axes, LIFTOPT, WLIFTOPT, GLIFTOPT.

  gas_lift_qc.py --deck CASE.DATA --contract site.contract.yml [--out folder]
  gas_lift_qc.py --deck CASE.DATA --target ECLIPSE100

Findings (E error, W warning, I information):
  G001 E  a well on lift (WLIFTOPT YES, WELTARG LIFT or WCONPROD ALQ) uses a VFPPROD table with no ALQ axis
  G002 E  a well on lift has no VFP table (WCONPROD item 11) or names a table that is not in the deck
  G003    ALQ definition (VFPPROD item 7): GRAT is lift gas rate. IGLR/TGLR (W: a ratio, while LIFTOPT and
          WLIFTOPT take rates), blank (W: meaning not stated, confirm it is lift gas rate), PUMP/COMP/DENO/DENG/BEAN
          (E: not lift gas)
  G004 W  VFP table unit system differs from the deck (the ALQ axis is then not in deck units)
  G005 W  WLIFTOPT maximum lift rate, WELTARG LIFT or WCONPROD ALQ is above the largest ALQ of the table
          (extrapolation); I when the WLIFTOPT maximum is defaulted (the largest ALQ applies)
  G006 W  LIFTOPT increment is not positive or is larger than the largest ALQ of the table
  G007 E  WLIFTOPT optimize YES with no LIFTOPT on or before that date
  G008 W  GLIFTOPT names a group that is not in WELSPECS or GRUPTREE
  G009 W  the VFP table shows no lift benefit: BHP does not fall at the first ALQ step at any table point
  G010 E  WLIFTOPT, WELTARG LIFT or WCONPROD names a well that WELSPECS does not define
  G011 E  the OPM parser rejects the deck (for example GLIFTOPT without LIFTOPT); no other check runs
  F001    a gas-lift keyword item that box Flow cannot run (flow-preflight deck_support). E when the target is
          FLOW, I when the target is ECLIPSE100/300 (only the box test is affected)

The VFP lift response (BHP against ALQ at fixed rate, THP, WFR, GFR) is reported per table. It is not an oil gain:
the oil gain needs the inflow, so use gas_lift_sweep.py for marginal oil per lift gas.
Needs opm and res2df. Reads the deck only. Exit: 0 pass or warnings, 1 an error finding, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gas_lift_deck as gld  # noqa: E402
from gas_lift_deck import ds  # noqa: E402

contract_lib = ds.contract_lib
GAS_LIFT_KEYWORDS = {
    "LIFTOPT",
    "WLIFTOPT",
    "GLIFTOPT",
    "GLIFTLIM",
    "WALQCALC",
    "VFPPROD",
    "WELTARG",
    "NODEPROP",
}
ALQ_UNIT = {"METRIC": "SM3/DAY", "FIELD": "MSCF/DAY", "LAB": "SCC/HR"}


def finding(code: str, severity: str, where: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "where": where, "message": message}


def alq_type(table: Any) -> str:
    return str(table["ALQ_TYPE"].iloc[0]).strip("'\" ").upper()


def lift_response(table: Any) -> dict[str, Any]:
    """BHP against ALQ at each (RATE, THP, WFR, GFR) point of a VFPPROD table."""
    alqs = sorted(float(a) for a in table["ALQ"].unique())
    if len(alqs) < 2:
        return {"alq_axis": alqs, "points": 0}
    benefit = 0
    best: list[float] = []
    points = 0
    for _, group in table.groupby(["RATE", "PRESSURE", "WFR", "GFR"], sort=True):
        g = group.sort_values("ALQ")
        bhp = g["TAB"].to_numpy(dtype=float)
        points += 1
        if bhp[1] < bhp[0]:
            benefit += 1
        best.append(float(g["ALQ"].to_numpy(dtype=float)[bhp.argmin()]))
    best.sort()
    return {"alq_axis": alqs, "points": points, "points_with_benefit": benefit,
            "share_with_benefit": benefit / points if points else None,
            "alq_at_min_bhp_median": best[len(best) // 2] if best else None,
            "alq_at_min_bhp_min": best[0] if best else None, "alq_at_min_bhp_max": best[-1] if best else None}  # fmt: skip


def lifted_wells(deck: gld.GasLiftDeck) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    for row in deck.wliftopt:
        if row["optimize"] == "YES":
            for w in row["wells"]:
                reasons.setdefault(w, []).append(f"WLIFTOPT YES {row['date']}")
    for row in deck.lift_targets:
        if row["alq"] and float(row["alq"]) > 0:
            for w in row["wells"]:
                reasons.setdefault(w, []).append(f"{row['source']} {row['alq']:g} {row['date']}")
    return reasons


def check(deck: gld.GasLiftDeck, target: str) -> tuple[list[dict], dict[str, Any]]:
    found: list[dict] = []
    lifted = lifted_wells(deck)
    for kind, rows in (("WLIFTOPT", deck.wliftopt), ("lift target", deck.lift_targets)):
        for row in rows:
            if not row["wells"]:
                found.append(
                    finding(
                        "G010", "E", f"{kind} {row['date']}", f"{row['pattern']} matches no well in WELSPECS"
                    )
                )
            for w in row["wells"]:
                if w not in deck.wells:
                    found.append(
                        finding("G010", "E", f"{kind} {row['date']}", f"well {w} is not in WELSPECS")
                    )
    responses = {}
    for number, table in sorted(deck.vfp.items()):
        where = f"VFPPROD {number}"
        kind = alq_type(table)
        response = lift_response(table)
        response["alq_type"] = kind or "(blank)"
        response["unit_system"] = str(table["UNIT_TYPE"].iloc[0]) if "UNIT_TYPE" in table else None
        units = response["unit_system"] if response["unit_system"] not in (None, "DEFAULT") else deck.units
        response["alq_unit"] = ALQ_UNIT.get(units) if kind == "GRAT" else None
        responses[number] = response
        if len(response["alq_axis"]) < 2:
            continue
        if kind in gld.ALQ_NOT_GAS:
            found.append(finding("G003", "E", where, f"ALQ definition {kind} is not lift gas"))
        elif kind in gld.ALQ_GAS_RATIO:
            found.append(finding("G003", "W", where, f"ALQ definition {kind} is a gas-liquid ratio; LIFTOPT increments "
                                 "and WLIFTOPT limits are gas rates: confirm the units match"))  # fmt: skip
        elif kind not in gld.ALQ_LIFT_GAS:
            found.append(finding("G003", "W", where, "ALQ definition is blank: the table does not say what ALQ is; "
                                 "confirm it is lift gas rate in deck units (ECLIPSE100 assumption, not checked)"))  # fmt: skip
        if response["unit_system"] and response["unit_system"].upper() not in ("DEFAULT", deck.units):
            found.append(
                finding("G004", "W", where, f"table units {response['unit_system']}, deck {deck.units}")
            )
        if response["points"] and not response["points_with_benefit"]:
            found.append(finding("G009", "W", where, "BHP does not fall at the first ALQ step at any table point: "
                                 "no lift benefit in this table"))  # fmt: skip
    for well, why in sorted(lifted.items()):
        number = deck.vfp_table(well)
        where = f"well {well}"
        if not number:
            found.append(finding("G002", "E", where, f"on lift ({why[0]}) but WCONPROD gives no VFP table"))
            continue
        table = deck.vfp.get(int(number))
        if table is None:
            found.append(finding("G002", "E", where, f"VFP table {number} is not in the deck"))
            continue
        axis = responses[int(number)]["alq_axis"]
        if len(axis) < 2:
            found.append(finding("G001", "E", where, f"on lift ({why[0]}) but VFPPROD {number} has no ALQ axis "
                                 f"(ALQ values {axis}): lift gas cannot change its BHP"))  # fmt: skip
            continue
        settings = deck.well_settings(well)
        if settings and settings["optimize"] == "YES":
            if settings["max_rate"] is None:
                found.append(finding("G005", "I", where, f"WLIFTOPT maximum lift rate defaulted: the largest ALQ of "
                                     f"VFPPROD {number} ({axis[-1]:g}) applies"))  # fmt: skip
            elif float(settings["max_rate"]) > axis[-1]:
                found.append(finding("G005", "W", where, f"WLIFTOPT maximum lift rate {settings['max_rate']:g} is above "
                                     f"the largest ALQ {axis[-1]:g} of VFPPROD {number}: extrapolation"))  # fmt: skip
        for target in deck.lift_targets:
            if well in target["wells"] and target["alq"] is not None and float(target["alq"]) > axis[-1]:
                found.append(finding("G005", "W", where, f"{target['source']} ALQ {float(target['alq']):g} on {target['date']} "
                                     f"is above the largest ALQ {axis[-1]:g} of VFPPROD {number}: extrapolation"))  # fmt: skip
        for opt in deck.liftopt:
            inc = opt["increment"]
            if inc is None or float(inc) <= 0 or float(inc) > axis[-1]:
                found.append(finding("G006", "W", f"LIFTOPT {opt['date']}", f"increment {inc} against the ALQ axis "
                                     f"0..{axis[-1]:g} of VFPPROD {number} ({well})"))  # fmt: skip
    for row in deck.wliftopt:
        if row["optimize"] == "YES" and not any(
            o["date"] <= row["date"] and (o["increment"] or 0) > 0 for o in deck.liftopt
        ):
            found.append(finding("G007", "E", f"WLIFTOPT {row['date']}", f"{row['pattern']} optimize YES but no LIFTOPT "
                                 "with a positive increment on or before this date"))  # fmt: skip
    for row in deck.gliftopt:
        if row["group"] not in deck.groups and row["group"] != "FIELD":
            found.append(
                finding("G008", "W", f"GLIFTOPT {row['date']}", f"group {row['group']} is not in the deck")
            )
    rows, _ = ds.classify(deck.path, ds.load_table())
    flow_rows = [r for r in rows if r["name"] in GAS_LIFT_KEYWORDS and r["status"] != "flow-ok"]
    for r in flow_rows:
        detail = r.get("message") or "; ".join(f"record {v['record']} item {v['item']} = {v['value']!r}: {v['message']}"
                                               for v in r.get("violations", []))  # fmt: skip
        severity = "E" if target == ds.FLOW else "I"
        tail = "" if target == ds.FLOW else f" (target {target}: fine there; a box Flow test cannot run it)"
        found.append(finding("F001", severity, r["name"], f"{r['status']}: {detail}{tail}"))
    dedup = list({json.dumps(f, sort_keys=True): f for f in found}.values())
    return dedup, {"vfp": responses, "lifted_wells": lifted, "flow_support": flow_rows}


def markdown(result: dict[str, Any]) -> str:
    lines = [f"# Gas-lift QC: {result['deck']}", "", f"- Target simulator: {result['target']} ({result['target_source']})",
             f"- Verdict: {result['verdict']}", f"- Deck units: {result['units']}", "",
             "## VFPPROD lift response (BHP against ALQ; not an oil gain)", "",
             "| table | ALQ type | ALQ axis | unit | points with BHP drop at first ALQ step | ALQ at min BHP (median, range) |",
             "|---|---|---|---|---|---|"]  # fmt: skip
    for number, r in result["vfp"].items():
        axis = ", ".join(f"{a:g}" for a in r["alq_axis"])
        share = f"{r['points_with_benefit']}/{r['points']}" if r["points"] else "no ALQ axis"
        best = (f"{r['alq_at_min_bhp_median']:g} ({r['alq_at_min_bhp_min']:g}..{r['alq_at_min_bhp_max']:g})"
                if r["points"] else "-")  # fmt: skip
        lines.append(
            f"| {number} | {r['alq_type']} | {axis} | {r['alq_unit'] or 'not stated'} | {share} | {best} |"
        )
    lines += ["", "## Findings", ""]
    lines += [f"- {f['severity']} {f['code']} {f['where']}: {f['message']}" for f in result["findings"]] or [
        "- none"
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--deck", type=Path, required=True)
    contract_lib.add_contract_args(parser)
    parser.add_argument("--target", help=f"override simulator.name: one of {sorted(ds.TARGETS)}")
    parser.add_argument("--out", type=Path, help="folder for gas_lift_qc.json and .md")
    args = parser.parse_args(argv)
    try:
        target, source = ds.resolve_target(args)
        if target not in ds.TARGETS:
            raise ValueError(f"simulator {target!r} is not one of {sorted(ds.TARGETS)}")
        if not args.deck.is_file():
            raise gld.DeckError(f"deck not found: {args.deck}")
        try:
            deck = gld.read_deck(args.deck)
        except RuntimeError as error:  # the OPM parser rejects the deck: a finding, not bad input
            deck = None
            found = [finding("G011", "E", "OPM parser", " ".join(str(error).split()))]
            info = {"vfp": {}, "lifted_wells": {}, "flow_support": []}
        if deck is not None:
            found, info = check(deck, target)
    except (gld.DeckError, ValueError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    verdict = (
        "FAIL"
        if any(f["severity"] == "E" for f in found)
        else "WARN"
        if any(f["severity"] == "W" for f in found)
        else "PASS"
    )
    result = {"schema_version": 1, "deck": str(args.deck), "target": target, "target_source": source,
              "units": deck.units if deck else None, "verdict": verdict, "findings": found, **info}  # fmt: skip
    out = args.out or args.deck.parent / "gas_lift_qc"
    out.mkdir(parents=True, exist_ok=True)
    (out / "gas_lift_qc.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    (out / "gas_lift_qc.md").write_text(markdown(result), encoding="utf-8")
    print(f"gas_lift_qc: {verdict}; {len(found)} finding(s); wrote {out}")
    for f in found:
        print(f"  {f['severity']} {f['code']} {f['where']}: {f['message']}")
    return 1 if verdict == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
