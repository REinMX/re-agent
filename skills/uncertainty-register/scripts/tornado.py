#!/usr/bin/env python3
"""Read paired design runs and write tornado or static/dynamic attribution evidence.

Exit 0 complete (or explicitly allowed partial), 1 missing runs, 2 invalid inputs.
One-by-one ignores interactions. Attribution uses run contrasts, not a linear estimate.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract/scripts"))
import runpath_lib  # noqa: E402

WARNING = "One-by-one ignores interactions."


def contrasts(rows: list[dict], values: dict[int, float], allow_partial: bool) -> list[dict]:
    ids = [int(r["REAL"]) for r in rows]
    if len(set(ids)) != len(ids) or any(i < 0 for i in ids):
        raise ValueError("REAL must contain unique nonnegative integers")
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError("Non-finite summary value")
    missing = sorted(set(ids) - values.keys())
    if missing and not allow_partial:
        raise ValueError(f"Missing realizations: {missing}")
    base_rows = [r for r in rows if r["SENSNAME"] == "base" and r["SENSCASE"] == "ref"]
    if len(base_rows) != 1 or int(base_rows[0]["REAL"]) not in values:
        raise ValueError("Exactly one completed base run is required")
    base_row = base_rows[0]
    base = values[int(base_row["REAL"])]
    cases = {}
    for row in rows:
        if row is base_row:
            continue
        key = (row["SENSNAME"], row["SENSCASE"])
        if key in cases:
            raise ValueError(f"Duplicate case {key}")
        cases[key] = int(row["REAL"])
    if any(c in {"static", "dynamic", "all"} for _, c in cases):
        expected = {(c, c) for c in ["static", "dynamic", "all"]}
        if set(cases) != expected or missing:
            raise ValueError("Attribution requires the complete base/static/dynamic/all design")
        static, dynamic, all_value = [values[cases[(c, c)]] for c in ["static", "dynamic", "all"]]
        total = all_value - base
        deltas = [static - base, dynamic - base, all_value - static - dynamic + base]
        return [
            dict(component=c, delta=d, share=d / total if total else None)
            for c, d in zip(["static", "dynamic", "interaction"], deltas, strict=True)
        ]
    result = []
    for name in dict.fromkeys(n for n, _ in cases):
        keys = [(name, c) for c in ["low", "high"]]
        if {k for k in cases if k[0] == name} != set(keys):
            raise ValueError(f"{name} needs exactly low and high cases")
        if any(cases[k] not in values for k in keys):
            continue
        low, high = [values[cases[k]] - base for k in keys]
        result.append(dict(parameter=name, low_delta=low, high_delta=high, span=abs(high - low)))
    if not result:
        raise ValueError("No complete low/high pair")
    return sorted(result, key=lambda r: (-r["span"], r["parameter"]))


def read_values(pattern: str, rows: list[dict], vector: str, date: str):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ensemble-at-date/scripts"))
    import numpy as np
    from ensemble_at_date import DateOutOfRange, Member

    paths = sorted(glob.glob(pattern + ".SMSPEC"))
    expected = {int(r["REAL"]) for r in rows}
    values, metadata = {}, {}
    for path in paths:
        real = runpath_lib.realization_number(Path(path))
        if real is None or real not in expected:
            raise ValueError(f"Unexpected realization for {path}")
        if real in values:
            raise ValueError(f"Duplicate summary for realization {real}")
        base = Path(path).with_suffix("")
        runpath = next((p for p in base.parents if p.parent.name == f"realization-{real}"), None)
        if runpath is None:
            raise ValueError(f"No iteration folder for realization {real}")
        if not (runpath / "OK").is_file() or (runpath / "ERROR").exists():
            continue
        if not Path(str(base) + ".UNSMRY").is_file():
            continue
        member = Member(real, Path(str(base) + ".DATA"), [vector])
        if vector not in member.keys:
            raise ValueError(f"realization {real}: missing vector {vector}")
        when = member.end if date == "last" else np.datetime64(date, "s")
        try:
            value, _ = member.value_at(vector, when, "auto")
        except DateOutOfRange as exc:
            raise ValueError(str(exc)) from exc
        values[real] = value
        metadata[real] = dict(date=str(when)[:10], unit=member.units[vector])
    if len({(m["date"], m["unit"]) for m in metadata.values()}) > 1:
        raise ValueError("Run dates or units differ; select a common --date")
    return values, metadata


def write_outputs(out: Path, result, rows, values, metadata, vector, missing):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    attribution = "component" in result[0]
    pd.DataFrame(result).to_csv(out / "tornado.csv", index=False)
    pd.DataFrame(
        [
            dict(
                REAL=int(r["REAL"]),
                SENSNAME=r["SENSNAME"],
                SENSCASE=r["SENSCASE"],
                value=values[int(r["REAL"])],
                **metadata[int(r["REAL"])],
            )
            for r in rows
            if int(r["REAL"]) in values
        ]
    ).to_csv(out / "responses.csv", index=False)
    info = next(iter(metadata.values()))
    context = f"{vector}, {info['unit']}, {info['date']}; N={len(values)}/{len(rows)}"
    note = (
        "Run-based attribution. Shares are signed fractions of all minus base. "
        "Shares are undefined when all equals base."
        if attribution
        else WARNING
    )
    fig, ax = plt.subplots(figsize=(8, max(3, len(result) * 0.4 + 1.5)))
    labels = [r["component" if attribution else "parameter"] for r in result]
    if attribution:
        ax.barh(labels, [r["delta"] for r in result])
    else:
        positions = list(range(len(labels)))
        ax.barh(
            [p - 0.18 for p in positions],
            [r["low_delta"] for r in result],
            height=0.34,
            label="P10 minus base",
        )
        ax.barh(
            [p + 0.18 for p in positions],
            [r["high_delta"] for r in result],
            height=0.34,
            label="P90 minus base",
        )
        ax.set_yticks(positions, labels)
        ax.legend()
    ax.axvline(0, color="black", linewidth=0.6)
    ax.invert_yaxis()
    ax.set_xlabel(f"Delta {vector} [{info['unit']}]")
    ax.set_title(context + "\n" + note, fontsize=9, wrap=True)
    fig.tight_layout()
    fig.savefig(out / "tornado.png", dpi=150)
    plt.close(fig)
    columns = list(result[0])
    lines = [
        "# Run contrasts",
        "",
        context,
        "",
        note,
        "",
        f"Missing realizations: {missing}",
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    lines.extend("| " + " | ".join(str(r[c]) for c in columns) + " |" for r in result)
    (out / "tornado.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runpath-glob", required=True, help="ECLBASE pattern, without .SMSPEC")
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--vector", required=True)
    parser.add_argument("--date", default="last")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        import pandas as pd

        if runpath_lib.realization_number(args.out.resolve()) is not None:
            raise ValueError("Do not write reports inside a realization runpath")
        frame = pd.read_excel(args.design, sheet_name="DesignSheet")
        if not {"REAL", "SENSNAME", "SENSCASE"} <= set(frame.columns) or frame.empty:
            raise ValueError("DesignSheet needs REAL, SENSNAME and SENSCASE")
        if frame["REAL"].isna().any() or any(float(i) != int(i) for i in frame["REAL"]):
            raise ValueError("REAL must contain integers")
        rows = frame.to_dict("records")
        values, metadata = read_values(args.runpath_glob, rows, args.vector, args.date)
        missing = sorted({int(r["REAL"]) for r in rows} - values.keys())
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "missing_realizations.json").write_text(json.dumps(missing) + "\n")
        if missing and not args.allow_partial:
            print(f"error: Missing realizations: {missing}", file=sys.stderr)
            return 1
        result = contrasts(rows, values, args.allow_partial)
        write_outputs(args.out, result, rows, values, metadata, args.vector, missing)
        print((args.out / "tornado.md").read_text())
        return 0
    except (ValueError, OSError, ImportError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
