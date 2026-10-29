"""Read the gas-lift setup of a deck: VFPPROD ALQ axes, LIFTOPT, WLIFTOPT, GLIFTOPT, WELTARG LIFT, WCONPROD.

Used by gas_lift_qc.py and gas_lift_diag.py. Reads the deck with the OPM parser (via flow-preflight
deck_support.parse_deck) and the VFP tables with res2df. Runs nothing.
"""

from __future__ import annotations

import fnmatch
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "flow-preflight" / "scripts"))
import deck_support as ds  # noqa: E402

MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())} | {
    "JLY": 7
}
# VFPPROD item 7 (ALQ definition), ECLIPSE100 manual names. GRAT is lift gas rate.
ALQ_LIFT_GAS = {"GRAT"}
ALQ_GAS_RATIO = {"IGLR", "TGLR"}
ALQ_NOT_GAS = {"PUMP", "COMP", "DENO", "DENG", "BEAN"}


class DeckError(ValueError):
    pass


def value(item: Any) -> Any:
    """The explicit value of a DeckItem, or None when it is defaulted or empty."""
    if not getattr(item, "__has_value")(0) or getattr(item, "__defaulted")(0):
        return None
    if item.is_string():
        return item.get_str(0).strip("'\"")
    if item.is_uda():
        uda = item.get_uda(0)
        return uda.get_double() if uda.is_double() else uda.get_string()
    if item.is_int():
        return item.get_int(0)
    return item.get_raw(0)


def items(record: Any, count: int) -> list[Any]:
    return [value(record[i]) if i < len(record) else None for i in range(count)]


@dataclass
class GasLiftDeck:
    path: Path
    start: date
    units: str | None
    wells: dict[str, str] = field(default_factory=dict)  # well -> group (WELSPECS)
    groups: set[str] = field(default_factory=set)
    liftopt: list[dict] = field(default_factory=list)
    wliftopt: list[dict] = field(default_factory=list)
    gliftopt: list[dict] = field(default_factory=list)
    lift_targets: list[dict] = field(default_factory=list)  # WELTARG LIFT and WCONPROD item 12 (ALQ)
    vfp_of_well: list[dict] = field(default_factory=list)  # WCONPROD item 11 per date
    vfp: dict[int, Any] = field(default_factory=dict)  # VFPPROD table number -> DataFrame (res2df)
    schedule_keywords: set[str] = field(default_factory=set)

    def match(self, pattern: str) -> list[str]:
        return [w for w in self.wells if fnmatch.fnmatchcase(w, pattern)] or (
            [pattern] if not any(c in pattern for c in "*?[") else []
        )

    def well_settings(self, well: str, when: date | None = None) -> dict | None:
        """The last WLIFTOPT record for a well on or before `when` (None: the last one in the deck)."""
        last = None
        for row in self.wliftopt:
            if well in row["wells"] and (when is None or row["date"] <= when):
                last = row
        return last

    def group_limits(self, group: str) -> list[dict]:
        return [g for g in self.gliftopt if g["group"] == group]

    def vfp_table(self, well: str) -> int | None:
        table = None
        for row in self.vfp_of_well:
            if well in row["wells"] and row["table"]:
                table = row["table"]
        return table


def read_deck(path: Path) -> GasLiftDeck:
    if not path.is_file():
        raise DeckError(f"deck not found: {path}")
    deck = ds.parse_deck(path)
    start = None
    units = None
    for keyword in deck:
        if keyword.name in ("METRIC", "FIELD", "LAB", "PVT-M"):
            units = keyword.name
        if keyword.name == "START":
            d, m, y = items(keyword[0], 3)
            start = date(int(y), MONTHS[str(m).upper()[:3]], int(d))
    if start is None:
        raise DeckError(f"{path.name}: no START")
    out = GasLiftDeck(path, start, units or "METRIC")
    when = start
    in_schedule = False
    for keyword in deck:
        name = keyword.name
        if name == "SCHEDULE":
            in_schedule = True
            continue
        if not in_schedule:
            continue
        out.schedule_keywords.add(name)
        if name == "DATES":
            for record in keyword:
                d, m, y = items(record, 3)
                when = date(int(y), MONTHS[str(m).upper()[:3]], int(d))
        elif name == "TSTEP":
            for step in keyword[0][0].get_raw_data_list():
                when = when + timedelta(days=float(step))
        elif name == "WELSPECS":
            for record in keyword:
                well, group = items(record, 2)
                out.wells[str(well)] = str(group)
                out.groups.add(str(group))
        elif name == "GRUPTREE":
            for record in keyword:
                child, parent = items(record, 2)
                out.groups |= {str(child), str(parent or "FIELD")}
        elif name == "LIFTOPT":
            inc, grad, interval, newton = items(keyword[0], 4)
            out.liftopt.append({"date": when, "increment": inc, "economic_gradient": grad,
                                "min_interval": interval, "optimize_newton": newton})  # fmt: skip
        elif name == "WLIFTOPT":
            for record in keyword:
                well, opt, max_rate, weight, min_rate, gas_weight, limit = items(record, 7)
                out.wliftopt.append({"date": when, "pattern": well, "wells": out.match(str(well)),
                                     "optimize": (opt or "NO").upper(), "max_rate": max_rate, "weight": weight,
                                     "min_rate": min_rate, "gas_weight": gas_weight, "optimize_limit": limit})  # fmt: skip
        elif name == "GLIFTOPT":
            for record in keyword:
                group, max_lift, max_total = items(record, 3)
                out.gliftopt.append(
                    {"date": when, "group": str(group), "max_lift": max_lift, "max_total_gas": max_total}
                )
        elif name == "WELTARG":
            for record in keyword:
                well, what, amount = items(record, 3)
                if str(what).upper() == "LIFT":
                    out.lift_targets.append({"date": when, "pattern": well, "wells": out.match(str(well)),
                                             "alq": amount, "source": "WELTARG LIFT"})  # fmt: skip
        elif name == "WCONPROD":
            for record in keyword:
                vals = items(record, 12)
                wells = out.match(str(vals[0]))
                out.vfp_of_well.append({"date": when, "pattern": vals[0], "wells": wells, "table": vals[10]})
                if vals[11] is not None:
                    out.lift_targets.append({"date": when, "pattern": vals[0], "wells": wells, "alq": vals[11],
                                             "source": "WCONPROD item 12"})  # fmt: skip
    from res2df import vfp as vfp_module

    for table in vfp_module.dfs(deck, keyword="VFPPROD"):
        out.vfp[int(table["TABLE_NUMBER"].iloc[0])] = table
    return out
