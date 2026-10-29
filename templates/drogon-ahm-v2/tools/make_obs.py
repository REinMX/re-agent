#!/usr/bin/env python
"""Preserve existing noisy observations; attach native RFT locations and truth breakthrough dates."""

import argparse
import json
import re
from pathlib import Path


def select_rft_points(frame):
    latest = frame[frame.DATE == frame.DATE.max()].sort_values("DEPTH")
    if len(latest) < 4:
        raise ValueError("need at least four RFT connections")
    return latest.iloc[[round(i * (len(latest) - 1) / 3) for i in range(4)]]


def first_crossing(series, threshold):
    hit = series[series >= threshold]
    return None if hit.empty else hit.index[0].strftime("%Y-%m-%d")


def build_observations(old, rft_points, locations, breakthrough, radius):
    blocks = []
    for block in old.splitlines():
        if block.startswith("GENERAL_OBSERVATION"):
            continue
        key = re.search(r"KEY\s*=\s*([^;]+)", block)
        if key is None:
            raise ValueError("expected one summary observation per line")
        well = key[1].split(":")[1]
        east, north = locations[well]
        loc = f"LOCALIZATION {{ EAST = {east:.6f}; NORTH = {north:.6f}; RADIUS = {radius}; }};"
        blocks.append(block.rsplit("}", 1)[0] + loc + " };")
    for well, rows in rft_points.items():
        for i, row in enumerate(rows):
            blocks.append(
                f"RFT_OBSERVATION RFT_{well}_{i} {{ LOCALIZATION {{ RADIUS = {radius}; }}; WELL = {well}; PROPERTY = PRESSURE; DATE = {row['date']}; VALUE = {row['value']}; ERROR = {row['error']}; EAST = {row['east']:.6f}; NORTH = {row['north']:.6f}; TVD = {row['tvd']:.6f}; }};"
            )
    for well, date in breakthrough.items():
        if date is None:
            continue
        east, north = locations[well]
        blocks.append(
            f"BREAKTHROUGH_OBSERVATION BT_{well} {{ KEY = WWCT:{well}; DATE = {date}; ERROR = 30; THRESHOLD = 0.1; LOCALIZATION {{ EAST = {east:.6f}; NORTH = {north:.6f}; RADIUS = {radius}; }}; }};"
        )
    return "\n".join(blocks) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, required=True, help="truth ECLBASE, no suffix")
    parser.add_argument("--old-obs", type=Path, required=True)
    parser.add_argument("--locations", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--radius", type=float, default=1000)
    args = parser.parse_args()
    import numpy as np
    import pandas as pd
    from res2df import ResdataFiles, rft, summary
    from resdata.grid import Grid

    data = ResdataFiles(str(args.truth) + ".DATA")
    grid = Grid(str(args.truth) + ".EGRID")
    frame = rft.df(data)
    points = {}
    for well in ("R_A2", "R_A3", "R_A4", "R_A5", "R_A6"):
        selected = select_rft_points(frame[frame.WELL == well])
        noisy = np.loadtxt(args.old_obs.parent / f"rft_{well}.obs")
        rows = []
        for (_, row), (value, error) in zip(selected.iterrows(), noisy, strict=True):
            east, north, tvd = grid.get_xyz(
                ijk=tuple(int(row[k]) - 1 for k in ("CONIPOS", "CONJPOS", "CONKPOS"))
            )
            rows.append(dict(date=str(row.DATE), value=value, error=error, east=east, north=north, tvd=tvd))
        points[well] = rows
    locs = pd.read_csv(args.locations)
    locs = locs[locs.source == "compdat"].set_index("well")
    locations = {well: (float(row.east), float(row.north)) for well, row in locs.iterrows()}
    profiles = summary.df(data, column_keys=["WWCT:*"], time_index="raw")
    bt = {w: first_crossing(profiles[f"WWCT:{w}"], 0.1) for w in ("A1", "A2", "A3", "A4")}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_observations(args.old_obs.read_text(), points, locations, bt, args.radius))
    args.out.with_suffix(".json").write_text(
        json.dumps(dict(rft=points, breakthrough=bt, radius=args.radius), indent=2)
    )


if __name__ == "__main__":
    main()
