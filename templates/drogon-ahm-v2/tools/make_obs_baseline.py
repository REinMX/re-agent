"""Build the synthetic Drogon observations from the truth run.

Usage: python tools/make_obs.py <truth_runpath_model_dir> <ECLBASE>
Writes ert/obs/drogon.obs and ert/obs/rft_<WELL>.obs. A point is skipped when the well has no flow
in the truth at that date: a shut well's BHP or rate carries no information about the reservoir. Noise is Gaussian with the stated error,
seed 20261015, so the file is reproducible.
"""

from pathlib import Path

import numpy as np
from res2df import ResdataFiles, summary

HERE = Path(__file__).resolve().parents[1]
DATES = [
    "2018-04-01",
    "2018-07-01",
    "2018-10-01",
    "2019-01-01",
    "2019-04-01",
    "2019-07-01",
    "2019-10-01",
    "2020-01-01",
    "2020-04-01",
    "2020-07-01",
]
PRODUCERS = ["A1", "A2", "A3", "A4"]
INJECTORS = ["A5", "A6"]
RFT_WELLS = ["R_A2", "R_A3", "R_A4", "R_A5", "R_A6"]
RFT_ERROR = 2.0


def error(vector: str, value: float) -> float:
    kind = vector.split(":")[0]
    if kind == "WOPR":
        return max(0.10 * abs(value), 20.0)
    if kind == "WWCT":
        return max(0.10 * abs(value), 0.02)
    if kind == "WGOR":
        return max(0.10 * abs(value), 5.0)
    if kind == "WBHP":
        return 5.0
    raise ValueError(vector)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("base")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    model, base = args.model, args.base
    rng = np.random.default_rng(20261015)
    vectors = [f"{k}:{w}" for w in PRODUCERS for k in ("WOPR", "WWCT", "WGOR", "WBHP")]
    vectors += [f"WBHP:{w}" for w in INJECTORS]
    flow_keys = [f"{k}:{w}" for w in PRODUCERS for k in ("WOPR", "WWPR", "WGPR")] + [
        f"WWIR:{w}" for w in INJECTORS
    ]
    df = summary.df(
        ResdataFiles(str(model / f"{base}.DATA")), column_keys=vectors + flow_keys, time_index="raw"
    )
    df.index = df.index.normalize()
    blocks, skipped = [], 0
    for vec in vectors:
        well = vec.split(":")[1]
        flow = [f"WWIR:{well}"] if well in INJECTORS else [f"{k}:{well}" for k in ("WOPR", "WWPR", "WGPR")]
        for d in DATES:
            if sum(float(df.loc[df.index == d, f].iloc[-1]) for f in flow) <= 0:
                skipped += 1
                continue
            true = float(df.loc[df.index == d, vec].iloc[-1])
            err = error(vec, true)
            obs = true + rng.normal(0, err)
            if vec.startswith("WWCT"):
                obs = min(max(obs, 0.0), 1.0)
            name = f"{vec.replace(':', '_')}_{d.replace('-', '')}"
            blocks.append(
                f"SUMMARY_OBSERVATION {name} {{ VALUE = {obs:.4f}; ERROR = {err:.4f}; DATE = {d}; KEY = {vec}; }};"
            )
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for well in RFT_WELLS:
        true = np.loadtxt(model / f"rft_{well}_1.txt")
        obs = true + rng.normal(0, RFT_ERROR, size=true.shape)
        (out / f"rft_{well}.obs").write_text("".join(f"{v:.3f} {RFT_ERROR}\n" for v in obs))
        blocks.append(
            f"GENERAL_OBSERVATION RFT_{well} {{ DATA = RFT_{well}; RESTART = 1; OBS_FILE = rft_{well}.obs; }};"
        )
    (out / "drogon.obs").write_text("\n".join(blocks) + "\n")
    n_summary = len(blocks) - len(RFT_WELLS)
    print(
        f"wrote {len(blocks)} observation blocks ({n_summary} summary points, {4 * len(RFT_WELLS)} RFT points); "
        f"skipped {skipped} points where the well was not flowing"
    )


if __name__ == "__main__":
    main()
