#!/usr/bin/env python3
"""Fit existing saturation tables to pyscal base recommendations."""

import argparse
import json
from pathlib import Path


def read_tables(path):
    from res2df import ResdataFiles, satfunc

    deck = ResdataFiles(str(path)).get_deck()
    if "TABDIMS" not in deck:
        raise ValueError("TABDIMS is required to establish NTSFUN")
    ntsfun = deck["TABDIMS"][0][0].get_int(0)
    tables = satfunc.df(deck, ntsfun=ntsfun)
    if tables.empty:
        raise ValueError("No saturation function tables found")
    for keyword, group in tables.groupby("KEYWORD"):
        count = group.SATNUM.nunique()
        if count != ntsfun:
            raise ValueError(f"TABDIMS NTSFUN={ntsfun}, but {keyword} has {count} SATNUMs")
    return tables


def fit_table(
    table,
    kind,
    *,
    swl=None,
    swcr=None,
    sorw=None,
    sgcr=None,
    sorg=None,
    krwend=None,
    krowend=None,
    krgend=None,
    krogend=None,
):
    """Fit all rows, including rows outside the mobile saturation interval."""
    import numpy as np
    import pandas as pd
    from pyscal import GasOil, WaterOil
    from scipy.optimize import least_squares

    gas = "SG" in table and table.SG.notna().any()
    saturation, phase, oil = ("SG", "KRG", "KROG") if gas else ("SW", "KRW", "KROW")
    table = table.sort_values(saturation)
    s = table[saturation].to_numpy(dtype=float)
    target = table[[phase, oil]].to_numpy(dtype=float)
    if len(s) < 4 or not np.isfinite(s).all() or not np.isfinite(target).all() or not np.all(np.diff(s) > 0):
        raise ValueError("Fit needs at least four finite rows with increasing saturation")
    if not ((target >= 0) & (target <= 1)).all():
        raise ValueError("Relative permeability must be in [0,1]")
    zeros = s[np.isclose(target[:, 0], 0, atol=1e-12)]
    oilzeros = s[np.isclose(target[:, 1], 0, atol=1e-12)]
    if not len(zeros) or not len(oilzeros):
        raise ValueError("Cannot infer endpoints without zero relative permeability rows")
    lower = float((0 if gas else s[0]) if swl is None else swl)
    critical_override = sgcr if gas else swcr
    residual_override = sorg if gas else sorw
    critical = float(zeros[-1] if critical_override is None else critical_override)
    residual = float(
        1 - (lower if gas else 0) - oilzeros[0] if residual_override is None else residual_override
    )
    valid = (
        (0 <= lower < 1 and 0 <= critical < 1 - lower - residual <= 1)
        if gas
        else (0 <= lower <= critical < 1 - residual <= 1)
    )
    if not valid:
        raise ValueError("Endpoints must satisfy 0 <= swl <= swcr < 1-sorw <= 1")
    obj = (
        GasOil(swl=lower, sgcr=critical, sorg=residual, h=0.001)
        if gas
        else WaterOil(swl=lower, swcr=critical, sorw=residual, h=0.001)
    )
    # Evaluate pyscal on the source nodes to avoid interpolation error in fitted parameters.
    original = obj.table.copy()
    nodes = np.unique(np.r_[s, original[saturation].to_numpy()])
    obj.table = pd.DataFrame(
        {column: np.interp(nodes, original[saturation], original[column]) for column in original}
    )
    obj.table[saturation] = nodes

    def evaluate(params):
        if kind == "corey":
            if gas:
                obj.add_corey_gas(ng=params[0], krgend=params[-2])
                obj.add_corey_oil(nog=params[1], kroend=params[-1])
            else:
                obj.add_corey_water(nw=params[0], krwend=params[-2])
                obj.add_corey_oil(now=params[1], kroend=params[-1])
        else:
            if gas:
                obj.add_LET_gas(l=params[0], e=params[1], t=params[2], krgend=params[-2])
            else:
                obj.add_LET_water(l=params[0], e=params[1], t=params[2], krwend=params[-2])
            obj.add_LET_oil(l=params[3], e=params[4], t=params[5], kroend=params[-1])
        return np.column_stack([np.interp(s, nodes, obj.table[column]) for column in [phase, oil]])

    initial = [2, 2] if kind == "corey" else [2, 2, 1, 2, 2, 1]
    initial += [max(0.01, float(np.interp(1 - residual, s, target[:, 0]))), float(target[0, 1])]
    fixed = {}
    for index, value in [(-2, krgend if gas else krwend), (-1, krogend if gas else krowend)]:
        if value is not None:
            if not 0 < value <= 1:
                raise ValueError("Relative permeability endpoint overrides must be in (0,1]")
            fixed[len(initial) + index] = value
    free = [i for i in range(len(initial)) if i not in fixed]

    def expand(values):
        params = np.array(initial)
        params[free] = values
        for index, value in fixed.items():
            params[index] = value
        return params

    lower_bounds = [0.01] * (len(initial) - 2) + [1e-8, 1e-8]
    upper_bounds = [99] * (len(initial) - 2) + [1, 1]
    result = least_squares(
        lambda p: (evaluate(expand(p)) - target).ravel(),
        [initial[i] for i in free],
        bounds=([lower_bounds[i] for i in free], [upper_bounds[i] for i in free]),
        ftol=1e-13,
        xtol=1e-13,
        gtol=1e-13,
        max_nfev=3000,
    )
    if not result.success:
        raise ValueError(f"{kind} optimizer did not converge: {result.message}")
    parameters = expand(result.x)
    errors = evaluate(parameters) - target
    names = (
        (["NG", "NOG"] if gas else ["NW", "NOW"])
        if kind == "corey"
        else (["LG", "EG", "TG", "LOG", "EOG", "TOG"] if gas else ["LW", "EW", "TW", "LO", "EO", "TO"])
    )
    row = dict(
        zip(
            names + (["KRGEND", "KROGEND"] if gas else ["KRWEND", "KROWEND"]),
            map(float, parameters),
            strict=True,
        )
    )
    row.update({"SWL": lower, "SGCR" if gas else "SWCR": critical, "SORG" if gas else "SORW": residual})
    limitations = []
    if s[0] < lower and not gas:
        limitations.append(
            "Source rows below SWL cannot be written by pyscal; their clamped fit errors are included"
        )
    if s[-1] > 1 - residual - (lower if gas else 0) + 1e-10:
        limitations.append(
            "Pyscal uses a linear tail beyond the residual-oil endpoint; source tail errors are included"
        )
    if not gas and lower < critical:
        limitations.append(
            "Rows below critical saturation may contain an oil plateau pyscal cannot represent"
        )
    return {
        "kind": kind,
        "recommendation": row,
        "curves": {
            name: {
                "max_abs_error": float(np.abs(errors[:, i]).max()),
                "rmse": float(np.sqrt(np.mean(errors[:, i] ** 2))),
            }
            for i, name in enumerate([phase, oil])
        },
        "limitations": limitations,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", required=True, type=Path)
    parser.add_argument("--satnum")
    parser.add_argument("--kind", choices=["let", "corey", "both"], default="both")
    for name in ["swl", "swcr", "sorw", "sgcr", "sorg", "krwend", "krowend", "krgend", "krogend"]:
        parser.add_argument(f"--{name}", type=float)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        import pandas as pd

        tables = read_tables(args.deck)
        selected = set(map(int, args.satnum.split(","))) if args.satnum else set(tables.SATNUM)
        if not selected <= set(tables.SATNUM):
            raise ValueError("Requested SATNUM is absent from deck")
        fits, rows = [], []
        for satnum in sorted(selected):
            row = {"SATNUM": int(satnum), "CASE": "base"}
            for keyword in ["SWOF", "SGOF"]:
                table = tables[(tables.SATNUM == satnum) & (tables.KEYWORD == keyword)].dropna(
                    axis=1, how="all"
                )
                if table.empty:
                    continue
                candidates = [
                    fit_table(
                        table,
                        kind,
                        swl=args.swl if args.swl is not None else row.get("SWL"),
                        **{
                            name: getattr(args, name)
                            for name in [
                                "swcr",
                                "sorw",
                                "sgcr",
                                "sorg",
                                "krwend",
                                "krowend",
                                "krgend",
                                "krogend",
                            ]
                        },
                    )
                    for kind in (["let", "corey"] if args.kind == "both" else [args.kind])
                ]
                fits.extend(dict(satnum=int(satnum), keyword=keyword, **fit) for fit in candidates)
                best = min(candidates, key=lambda fit: max(c["rmse"] for c in fit["curves"].values()))
                row.update(best["recommendation"])
            if len(row) == 2:
                raise ValueError("Only SWOF and SGOF tables are supported")
            rows.append(row)
        args.out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(args.out / "base.csv", index=False)
        (args.out / "fits.json").write_text(json.dumps({"fits": fits}, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "fits": fits,
                    "note": "SATNUM identifiers are preserved. A selected subset must be merged into a complete recommendation before full-deck generation.",
                }
            )
        )
        return 0
    except (ImportError, OSError, ValueError, RuntimeError, AssertionError) as error:
        print(f"satfunc-fit: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
