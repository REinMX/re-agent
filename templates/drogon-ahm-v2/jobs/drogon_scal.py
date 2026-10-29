#!/usr/bin/env python
"""Map facies-group interpolation parameters to ordered pyscal SATNUM tables."""

import argparse
import csv
import sys
from pathlib import Path

from drogon_v2_lib import read_params


def interpolation_values(rows, params):
    ordered = sorted(rows, key=lambda row: int(row["SATNUM"]))
    if [int(row["SATNUM"]) for row in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError("SATNUMs must be unique and contiguous from 1")
    values = [[params[f"SCAL_{kind}_{row['GROUP']}"] for row in ordered] for kind in ("WO", "GO")]
    if any(not -1 <= v <= 1 for seq in values for v in seq):
        raise ValueError("SCAL interpolation must be in [-1, 1]")
    return values


def strict_saturation_nodes(text):
    """Drop only identical printed saturation rows; reject conflicting values."""
    out, previous = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "/":
            previous = None
        elif stripped and stripped[0].isdigit():
            values = [float(v) for v in stripped.split()]
            if previous is not None and values[0] <= previous[0]:
                if (
                    values[0] == previous[0]
                    and max(abs(a - b) for a, b in zip(values, previous, strict=True)) <= 1e-6
                ):
                    continue
                raise ValueError("conflicting or decreasing saturation nodes")
            previous = values
        out.append(line)
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recommendation", type=Path)
    parser.add_argument("groups", type=Path)
    parser.add_argument("--params", type=Path, default=Path("parameters.txt"))
    parser.add_argument("--out", type=Path, default=Path("include/ert/sattab.inc"))
    args = parser.parse_args(argv)
    try:
        from pyscal.factory import create_scal_recommendation_list, load_relperm_df

        with args.groups.open() as handle:
            rows = list(csv.DictReader(handle))
        wo, go = interpolation_values(rows, read_params(args.params)["DROGON"])
        recs = create_scal_recommendation_list(load_relperm_df(str(args.recommendation)), h=0.01)
        if len(recs) != len(rows):
            raise ValueError("recommendation and group SATNUM counts differ")
        tables = recs.interpolate(wo, go, h=0.01)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            strict_saturation_nodes(tables.SWOF()) + "\n" + strict_saturation_nodes(tables.SGOF())
        )
    except (ImportError, OSError, KeyError, ValueError) as error:
        print(f"drogon_scal: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
