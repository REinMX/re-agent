#!/usr/bin/env python
"""Score two completed ERT 26 ES runs, including native RFT and breakthrough responses."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jobs"))
from drogon_v2_lib import read_grdecl, read_params  # noqa: E402


def spread_and_coverage(prior, posterior, truth):
    sd = np.std(prior, axis=0, ddof=1)
    if np.any(sd <= 0):
        raise ValueError("zero prior spread")
    ratio = np.std(posterior, axis=0, ddof=1) / sd
    result = dict(
        median_sd_ratio=float(np.median(ratio)),
        collapse_count=int(np.sum(ratio < 0.2)),
        collapse_share=float(np.mean(ratio < 0.2)),
    )
    if truth is not None:
        for label, values in [("prior", prior), ("posterior", posterior)]:
            lo, hi = np.percentile(values, [5, 95], axis=0)
            result[f"{label}_truth_coverage"] = float(np.mean((truth >= lo) & (truth <= hi)))
    return result


def score_family(values, observations, errors):
    """Mean squared normalized residual per member.

    A breakthrough response is missing when the well never crosses the threshold.
    Those entries are counted, not scored, so every number says how many it covers.
    """
    residual = (values - observations[:, None]) / errors[:, None]
    finite = np.isfinite(residual)
    if not finite.any(axis=0).all():
        raise ValueError("a member has no scorable observation")
    mse = np.nanmean(np.where(finite, residual**2, np.nan), axis=0)
    return dict(
        n_obs=len(observations),
        n_missing=int((~finite).sum()),
        mean=float(np.mean(mse)),
        median=float(np.median(mse)),
        worst=float(max(mse)),
    )


def score_storage(path, expected):
    import polars as pl
    from ert.storage import open_storage

    result = {}
    with open_storage(path, mode="r") as storage:
        ensembles = {e.name: e for e in storage.ensembles}
        for label, name in [("prior", "iter-0"), ("posterior", "iter-1")]:
            ensemble = ensembles[name]
            reals = ensemble.get_realization_list_with_responses()
            if reals != list(range(expected)):
                raise ValueError(f"{path} {name}: expected {expected} complete members, got {reals}")
            names = [
                key
                for frame in ensemble.experiment.observations.values()
                for key in frame["observation_key"].unique()
            ]
            frame = ensemble.get_observations_and_responses(names, np.array(reals))
            family = []
            for key in frame["observation_key"]:
                family.append(
                    "BREAKTHROUGH"
                    if key.startswith("BT_")
                    else "RFT"
                    if key.startswith("RFT_")
                    else key.split("_")[0]
                )
            frame = frame.with_columns(pl.Series("family", family))
            families = {}
            for fam in sorted(set(family)) + ["COMMON", "ALL"]:
                sub = (
                    frame
                    if fam == "ALL"
                    else frame.filter(pl.col("family") != "BREAKTHROUGH")
                    if fam == "COMMON"
                    else frame.filter(pl.col("family") == fam)
                )
                values = sub.select([str(i) for i in reals]).to_numpy().astype(float)
                observations = sub["observations"].to_numpy().astype(float)
                errors = sub["std"].to_numpy().astype(float)
                missing = ~np.isfinite(values)
                if fam not in ("BREAKTHROUGH", "ALL"):
                    allowed = np.zeros_like(missing)
                else:
                    is_bt = (sub["family"] == "BREAKTHROUGH").to_numpy()
                    allowed = np.broadcast_to(is_bt[:, None], missing.shape)
                if np.any(missing & ~allowed) or not np.isfinite(observations).all() or np.any(errors <= 0):
                    raise ValueError(f"nonfinite/missing response or invalid error: {label} {fam}")
                families[fam] = score_family(values, observations, errors)
            result[label] = dict(n=len(reals), families=families)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--truth-params", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=16)
    args = parser.parse_args()
    truth = read_params(args.truth_params)["DROGON"]
    static = dict(np.load(args.case / "static/drogon_static.npz"))
    active = static["actnum"] > 0
    size = len(active)
    report = dict(
        definition=(
            "mean squared normalized residual; all configured observations, including deactivated"
            " observations; a breakthrough that never happens is counted in n_missing, not scored"
        ),
        percentiles="numerical, linear P5/P95",
        n=args.n,
    )
    for setup in ("before", "after"):
        result = score_storage(args.case / f"output/{setup}/storage", args.n)
        params = {}
        for label, it in [("prior", 0), ("posterior", 1)]:
            members = [args.scratch / setup / f"realization-{i}" / f"iter-{it}" for i in range(args.n)]
            params[label] = [read_params(p / "parameters.txt")["DROGON"] for p in members]
            qc = [json.loads((p / "swatinit_qc.json").read_text()) for p in members]
            result[label]["HC_BELOW_FWL_PCT_HC"] = [r["HC_BELOW_FWL_PCT_HC"] for r in qc]
            result[label]["SWL_TRUNC_PCT_HC"] = [r["SWL_TRUNC_PCT_HC"] for r in qc]
        names = sorted(params["prior"][0])
        a = np.array([[p[k] for k in names] for p in params["prior"]])
        b = np.array([[p[k] for k in names] for p in params["posterior"]])
        ratios = np.std(b, axis=0, ddof=1) / np.std(a, axis=0, ddof=1)
        shared = [k for k in names if k in truth]
        subset = [names.index(k) for k in shared]
        result["scalars"] = spread_and_coverage(a, b, None)
        result["scalars"]["sd_ratio"] = dict(zip(names, ratios.tolist(), strict=True))
        result["shared_scalar_truth"] = dict(
            names=shared,
            **spread_and_coverage(a[:, subset], b[:, subset], np.array([truth[k] for k in shared])),
        )
        result["scalars"]["truth_note"] = (
            "SCAL interpolation parameters have no scalar truth equivalent; excluded from truth coverage"
        )
        if setup == "after":
            values = []
            for it in (0, 1):
                values.append(
                    np.array(
                        [
                            read_grdecl(
                                (
                                    args.scratch
                                    / setup
                                    / f"realization-{i}"
                                    / f"iter-{it}"
                                    / "lnpermmult.grdecl"
                                ).read_text(),
                                "LNPERM",
                                size,
                            )[active]
                            for i in range(args.n)
                        ]
                    )
                )
            field_truth = np.zeros(size)
            for z, name in [(1, "VALYSAR"), (2, "THERYS"), (3, "VOLON")]:
                field_truth[static["multnum"] == z] = np.log(truth[f"PERM_{name}"])
            result["field"] = dict(
                n_cells=int(active.sum()), **spread_and_coverage(*values, field_truth[active])
            )
        result["wall"] = (args.case / f"output/{setup}/wall.txt").read_text().strip()
        report[setup] = result
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
