"""Tiny synthetic SMSPEC/UNSMRY/PRT fixtures for region-in-place tests (no simulator needed)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

START = dt.datetime(2025, 1, 1)
# report steps: 1 Feb 2025, 1 Jan 2026, 1 Jan 2027, 1 Jan 2028
DAYS = [31, 365, 730, 1095]


def region_values(scale: float = 1.0, n_steps: int = len(DAYS)) -> dict[str, list[float]]:
    """FIPNUM 1..2 and FIPSEG 1..2 that add up to the field at every step."""
    out: dict[str, list[float]] = {}
    for t in range(n_steps):
        r1, r2 = 600.0 * scale - 50 * t, 400.0 * scale - 30 * t
        s1, s2 = 700.0 * scale - 60 * t, (r1 + r2) - (700.0 * scale - 60 * t)
        g = 80.0
        vals = {
            "FOIP": r1 + r2,
            "ROIP:1": r1,
            "ROIP:2": r2,
            "ROIP_SEG:1": s1,
            "ROIP_SEG:2": s2,
            "FGIP": g * (r1 + r2),
            "RGIP:1": g * r1,
            "RGIP:2": g * r2,
            "RGIP_SEG:1": g * s1,
            "RGIP_SEG:2": g * s2,
            "FOPT": 80.0 * t,
            "FGPT": g * 80.0 * t,
            "FGIT": 0.0,
        }
        for k, v in vals.items():
            out.setdefault(k, []).append(v)
    return out


def write_summary(base: Path, values: dict[str, list[float]], days=DAYS, start=START, unit="SM3") -> Path:
    from resdata.summary import Summary

    base.parent.mkdir(parents=True, exist_ok=True)
    w = Summary.writer(str(base), start, 1, 1, 1)
    for key in values:
        kw, _, num = key.partition(":")
        w.add_variable(kw, num=int(num) if num else 0, unit=unit)
    for i, d in enumerate(days):
        t = w.add_t_step(i + 1, d)
        for key, vals in values.items():
            t[key] = vals[i]
    w.fwrite()
    return base


PRT_BLOCK = """                                                     ==================================================
                                                     :{title:^48}:
                                                     :        PAV  =           250  BARSA             :
                                                     :        PORV =       1000000  RM3               :
                           :--------------- OIL  SM3 ------------------:-- WAT    SM3 --:--------------  GAS     SM3 ---------------:
                           :    LIQUID         VAPOUR        TOTAL     :     TOTAL      :     FREE        DISSOLVED       TOTAL     :
 :-------------------------:-------------------------------------------:----------------:-------------------------------------------:
 :CURRENTLY IN PLACE       :{oil:>11.0f}          0  {oil:>11.0f}       :{wat:>11.0f}     :       0  {gas:>15.0f} {gas:>13.0f}  :
 :-------------------------:-------------------------------------------:----------------:-------------------------------------------:
 :ORIGINALLY IN PLACE      :{oil:>11.0f}          0  {oil:>11.0f}       :{wat:>11.0f}     :       0  {gas:>15.0f} {gas:>13.0f}  :
 ====================================================================================================================================

"""


def write_prt(path: Path, initial: dict[tuple[str, int], tuple[float, float]], date="01 Jan 2025"):
    """Flow-style PRT with a report-0 balance: initial[(set, region)] = (oil, gas); ('FIELD', 0) for totals."""
    lines = [
        "  BALANCE  AT         0  DAYS *TEST                                         *",
        f"  REPORT    0    {date}  *                                             Flow  version     2026.04  *",
        "",
    ]
    for (st, reg), (oil, gas) in initial.items():
        title = "FIELD TOTALS" if st == "FIELD" else f"{st} REPORT REGION  {reg}"
        lines.append(PRT_BLOCK.format(title=title, oil=oil, gas=gas, wat=2000.0 if st == "FIELD" else 1000.0))
    lines.append("  BALANCE  AT        31  DAYS *TEST                                         *")
    lines.append(
        "  REPORT    1    01 Feb 2025  *                                             Flow  version     2026.04  *"
    )
    path.write_text("\n".join(lines) + "\n")
