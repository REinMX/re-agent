#!/usr/bin/env python3
"""Well coordinates (EAST, NORTH) for observation localization, from a deck and its grid.

res2df reads COMPDAT (with WELOPEN and defaulted I, J resolved) and WELSPECS through the OPM parser.
xtgeo gives the cell centres of the grid. For each well the script writes two rows:
- source compdat: the mean x, y of the cells that are OPEN at any date (each cell once).
- source welspecs: the centre of the well head cell (HEAD_I, HEAD_J) in layer 1.
Output CSV columns: well,east,north,source,n_connections.

Exit 0 written, 1 written but a well has no OPEN connection, 2 input error (res2df or xtgeo missing,
a connection outside the grid, no wells).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("well", "east", "north", "source", "n_connections")


class InputError(Exception):
    """A missing or unusable input. Exit 2."""


@dataclass(frozen=True)
class WellLocation:
    well: str
    east: float
    north: float
    source: str
    n_connections: int


def mean_centre(cells: Iterable[tuple[int, int, int]], x, y) -> tuple[float, float, int]:
    """Mean x, y of the unique 1-based (i, j, k) cells. x and y are (nx, ny, nz) arrays of cell centres."""
    unique = sorted(set(cells))
    nx, ny, nz = x.shape
    for i, j, k in unique:
        if not (1 <= i <= nx and 1 <= j <= ny and 1 <= k <= nz):
            raise InputError(f"cell ({i},{j},{k}) is outside the grid {nx}x{ny}x{nz}")
    east = sum(float(x[i - 1, j - 1, k - 1]) for i, j, k in unique) / len(unique)
    north = sum(float(y[i - 1, j - 1, k - 1]) for i, j, k in unique) / len(unique)
    return east, north, len(unique)


def well_locations(
    open_cells: Mapping[str, list[tuple[int, int, int]]], heads: Mapping[str, tuple[int, int]], x, y
) -> list[WellLocation]:
    """One compdat row per well with OPEN cells, one welspecs row per well head."""
    out = []
    for well in sorted(set(open_cells) | set(heads)):
        if open_cells.get(well):
            east, north, count = mean_centre(open_cells[well], x, y)
            out.append(WellLocation(well, east, north, "compdat", count))
        if well in heads:
            i, j = heads[well]
            east, north, _ = mean_centre([(i, j, 1)], x, y)
            out.append(WellLocation(well, east, north, "welspecs", 0))
    return out


def read_deck(data_file: Path) -> tuple[dict[str, list[tuple[int, int, int]]], dict[str, tuple[int, int]]]:
    """OPEN connection cells and WELSPECS heads of each well, with res2df."""
    from res2df import ResdataFiles, compdat
    from res2df.common import parse_opmio_deckrecord

    files = ResdataFiles(str(data_file))
    frame = compdat.df(files)
    cells: dict[str, list[tuple[int, int, int]]] = {}
    if not frame.empty:
        for well, i, j, k, state in zip(
            frame["WELL"], frame["I"], frame["J"], frame["K1"], frame["OP/SH"], strict=True
        ):
            if str(state).upper() == "OPEN":
                cells.setdefault(str(well), []).append((int(i), int(j), int(k)))
    heads: dict[str, tuple[int, int]] = {}
    for keyword in files.get_deck():
        if keyword.name == "WELSPECS":
            for record in keyword:
                parsed = parse_opmio_deckrecord(record, "WELSPECS")
                heads[str(parsed["WELL"])] = (int(parsed["HEAD_I"]), int(parsed["HEAD_J"]))
    return cells, heads


def cell_centres(egrid: Path):
    import xtgeo

    grid = xtgeo.grid_from_file(str(egrid))
    x, y, _ = grid.get_xyz(asmasked=False)
    return x.values, y.values


def write_csv(path: Path, rows: list[WellLocation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        for r in rows:
            writer.writerow([r.well, f"{r.east:.2f}", f"{r.north:.2f}", r.source, r.n_connections])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data", required=True, type=Path, help="The DATA file (OPM-parsable)")
    parser.add_argument("--grid", required=True, type=Path, help="The EGRID of the case")
    parser.add_argument("--out", required=True, type=Path, help="The output CSV")
    args = parser.parse_args(argv)
    missing = [name for name in ("res2df", "xtgeo", "numpy") if importlib.util.find_spec(name) is None]
    if missing:
        print(f"ERROR: {', '.join(missing)} not installed. Use the Komodo Python", file=sys.stderr)
        return 2
    try:
        for path in (args.data, args.grid):
            if not path.is_file():
                raise InputError(f"{path} not found")
        cells, heads = read_deck(args.data)
        if not heads and not cells:
            raise InputError(f"{args.data}: no WELSPECS and no COMPDAT")
        x, y = cell_centres(args.grid)
        rows = well_locations(cells, heads, x, y)
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if any(not (math.isfinite(r.east) and math.isfinite(r.north)) for r in rows):
        print("ERROR: a cell centre is not finite. Check the grid geometry", file=sys.stderr)
        return 2
    write_csv(args.out, rows)
    no_open = sorted(set(heads) - {r.well for r in rows if r.source == "compdat"})
    print(f"Wrote {args.out}: {len(rows)} rows, {len({r.well for r in rows})} wells")
    print("| Well | Source | EAST | NORTH | Connections |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(f"| {r.well} | {r.source} | {r.east:.2f} | {r.north:.2f} | {r.n_connections} |")
    if no_open:
        print(f"WARNING: no OPEN connection for {', '.join(no_open)}. Only the welspecs row exists")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
