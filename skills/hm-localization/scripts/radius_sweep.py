#!/usr/bin/env python3
"""Sweep the localization radius: replay one ES update from a stored prior, once per radius. No simulation.

The storage is copied to a temporary folder first, and only the copy is opened. For each radius the script
runs ERT 26's own update (smoother_update, as semeio AHM_ANALYSIS does) from the prior ensemble into a
temporary target: weight 1, the observations of the experiment, the observation settings, ENKF truncation
and RANDOM_SEED of --config. Each FIELD and SURFACE parameter gets DISTANCE with RADIUS = R for every
located observation (the RADIUS of the obs file is overridden). R = inf is the GLOBAL update with all
observations. GEN_KW parameters are not updated: their update does not change a field update.

This is one ES step from the prior, not the ES-MDA chain. A real ES-MDA run with the weights of the plan
spreads the same data over several steps and gives other numbers. Use the sweep to rank the radii.

Metrics per radius and field, in the space that ERT updates (after INIT_TRANSFORM, for example ln PERMX),
over the active cells of the GRID with prior spread:
  median_sd_ratio   median of sd(posterior) / sd(prior)
  collapse_share    share of cells with sd ratio < --collapse (default 0.2)
  mean_abs_shift_sd mean of |mean(posterior) - mean(prior)| / sd(prior)
  with --truth-field: truth_in_p5_p95 (share of cells where the truth is inside P5-P95 of the posterior)
  and rmse_mean (RMSE of the posterior mean against the truth). The row 'prior' gives the same for the prior.
Outputs in --out: sweep.csv, sweep.md. Exit 0 written, 2 input error.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import math
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

CSV_FIELDS = (
    "radius",
    "field",
    "n_cells",
    "n_obs_used",
    "median_sd_ratio",
    "collapse_share",
    "mean_abs_shift_sd",
    "truth_in_p5_p95",
    "rmse_mean",
    "seconds",
)


class InputError(Exception):
    """A missing or unusable input. Exit 2."""


@dataclass(frozen=True)
class Metrics:
    n_cells: int
    median_sd_ratio: float
    collapse_share: float
    mean_abs_shift_sd: float
    truth_in_p5_p95: float | None
    rmse_mean: float | None


def parse_radii(text: str) -> list[float]:
    out = []
    for item in text.split(","):
        item = item.strip().lower()
        if not item:
            continue
        value = math.inf if item in ("inf", "global") else float(item)
        if not value > 0:
            raise InputError(f"--radii: {item} must be larger than 0, or inf")
        out.append(value)
    if not out:
        raise InputError("--radii is empty")
    return sorted(set(out))


def radius_label(radius: float) -> str:
    return "inf" if math.isinf(radius) else f"{radius:g}"


def coverage_rmse(ensemble, truth) -> tuple[float, float]:
    """Share of cells with the truth inside P5-P95 of the ensemble, and RMSE of the ensemble mean."""
    import numpy as np

    low, high = np.percentile(ensemble, [5, 95], axis=1)
    inside = float(np.mean((truth >= low) & (truth <= high)))
    rmse = float(np.sqrt(np.mean((ensemble.mean(axis=1) - truth) ** 2)))
    return inside, rmse


def field_metrics(prior, posterior, truth=None, collapse: float = 0.2) -> Metrics:
    """Pure. prior and posterior are (cells, realizations) in the updated space; truth is (cells,) or None.

    Cells without prior spread are left out.
    """
    import numpy as np

    sd_prior = prior.std(axis=1, ddof=1)
    keep = sd_prior > 0
    if not keep.any():
        raise InputError("no cell has prior spread")
    prior, posterior, sd_prior = prior[keep], posterior[keep], sd_prior[keep]
    ratio = posterior.std(axis=1, ddof=1) / sd_prior
    shift = np.abs(posterior.mean(axis=1) - prior.mean(axis=1)) / sd_prior
    inside = rmse = None
    if truth is not None:
        inside, rmse = coverage_rmse(posterior, truth[keep])
    return Metrics(
        int(keep.sum()),
        float(np.median(ratio)),
        float(np.mean(ratio < collapse)),
        float(np.mean(shift)),
        inside,
        rmse,
    )


def acceptable(row: dict, max_collapse: float) -> bool:
    return row["collapse_share"] <= max_collapse


def rank(rows: list[dict], max_collapse: float) -> list[dict]:
    """Pure. The acceptable rows (collapse_share <= max_collapse) first, then the rest by radius.

    With a truth, the acceptable rows rank by the RMSE of the posterior mean (lowest first). Without a
    truth, by radius (largest first: it uses the most data without an unacceptable collapse).
    """

    def key(row: dict):
        if not acceptable(row, max_collapse):
            return (1, row["radius"], 0.0)
        if row.get("rmse_mean") is not None:
            return (0, row["rmse_mean"], -row["radius"])
        return (0, -row["radius"], 0.0)

    return sorted((r for r in rows if r["radius"] != "prior"), key=key)


def copy_storage(storage: Path, target: Path) -> Path:
    if not (storage / "index.json").is_file():
        raise InputError(f"--storage {storage} is not an ERT storage (no index.json)")
    shutil.copytree(storage, target, ignore=shutil.ignore_patterns("storage.lock", "*.lock"))
    return target


def load_settings(config_path: Path):
    import contextlib

    from ert.config import ConfigValidationError, ErtConfig
    from ert.plugins import get_site_plugins

    try:
        with contextlib.chdir(config_path.parent):
            return ErtConfig.with_plugins(get_site_plugins()).from_file(config_path.name)
    except ConfigValidationError as error:
        raise InputError(
            f"--config {config_path} does not load: {'; '.join(error.messages())[:500]}"
        ) from error


def read_truth(path: Path, field_cfg) -> object:
    """The truth field in the layout and the space of the stored parameter (ERT's own reader and transform)."""
    import numpy as np
    from ert.config.field import field_transform
    from ert.field_utils import Shape, read_field

    e = field_cfg.ertbox_params
    values = read_field(path, field_cfg.name, Shape(e.nx, e.ny, e.nz))
    data = np.ma.filled(values.astype(float), np.nan)
    return np.asarray(field_transform(data, field_cfg.input_transformation), dtype=float).ravel()


def active_mask(field_cfg) -> object:
    import numpy as np
    import xtgeo

    grid = xtgeo.grid_from_file(field_cfg.grid_file)
    actnum = np.asarray(grid.get_actnum().values).ravel() > 0
    e = field_cfg.ertbox_params
    if actnum.size != e.nx * e.ny * e.nz:
        raise InputError(
            f"GRID {field_cfg.grid_file} has {actnum.size} cells, {field_cfg.name} {e.nx * e.ny * e.nz}"
        )
    return actnum


def fixed_radius_strategy(radius: float, truncation: float, param_type):
    """DISTANCE with one radius for every located observation. No cached rho (experiment=None)."""
    import numpy as np
    from ert.analysis._update_strategies import DistanceLocalizationUpdate

    class FixedRadius(DistanceLocalizationUpdate):
        def prepare(self, obs_context):
            loc = obs_context.observation_locations
            if loc is None:
                raise InputError("no observation has a location. Run localize_obs.py first, or use only inf")
            loc = dataclasses.replace(loc, main_range=np.full_like(loc.main_range, radius, dtype=float))
            super().prepare(dataclasses.replace(obs_context, observation_locations=loc))

    return FixedRadius(truncation, param_type, lambda _event: None, None)


def replay(
    prior_ens, radius: float, params: list[str], truncation: float, obs_settings, seed: int, located: set[str]
):
    """One update into a temporary storage. Return ({param: posterior array}, observations used).

    A finite radius uses the active observations with a location; inf (GLOBAL) uses all active ones.
    """
    import copy
    import os

    import numpy as np
    from ert.analysis._es_update import smoother_update
    from ert.analysis._update_strategies import GlobalESUpdate
    from ert.config import Field, SurfaceConfig
    from ert.storage import open_storage

    experiment = prior_ens.experiment
    strategy_map = {}
    for name in params:
        cfg = experiment.parameter_configuration[name]
        if math.isinf(radius):
            strategy_map[name] = GlobalESUpdate(truncation, lambda _event: None)
        else:
            strategy_map[name] = fixed_radius_strategy(
                radius, truncation, Field if isinstance(cfg, Field) else SurfaceConfig
            )
    index = np.flatnonzero(prior_ens.get_realization_mask_with_responses())
    with tempfile.TemporaryDirectory() as tmp, open_storage(os.path.join(tmp, "storage"), "w") as storage:
        target_exp = storage.create_experiment(experiment_config=experiment.experiment_config)
        target = storage.create_ensemble(target_exp, name="sweep", ensemble_size=prior_ens.ensemble_size)
        snapshot = smoother_update(
            prior_ens,
            target,
            list(experiment.observation_keys),
            copy.deepcopy(obs_settings),
            np.random.default_rng(seed),
            strategy_map,
        )
        posterior = {name: target.load_parameters_numpy(name, index) for name in params}
    frame = snapshot.observations_and_responses
    used = sum(
        1
        for key, status in zip(frame["observation_key"], frame["status"], strict=True)
        if str(status).rsplit(".", 1)[-1].upper() == "ACTIVE" and (math.isinf(radius) or key in located)
    )
    return posterior, used


def located_keys(prior_ens) -> set[str]:
    """Keys of the observations of the experiment that have a location."""
    out: set[str] = set()
    for frame in prior_ens.experiment.observations.values():
        if {"east", "observation_key"} <= set(frame.columns):
            out.update(frame.filter(frame["east"].is_not_null())["observation_key"].to_list())
    return out


def write_outputs(out: Path, rows: list[dict], args, wall: float, n_located: int | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "sweep.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: fmt(row.get(k)) if k != "radius" else row_label(row) for k in CSV_FIELDS})
    lines = [
        "# Radius sweep (one ES step from the prior, not the ES-MDA chain)",
        "",
        f"Storage copy of `{args.storage}`, prior `{args.prior}`, config `{args.config}`.",
        f"Weight 1, seed {args.seed_used}. Located observations in the experiment: {n_located}.",
        "R = inf is the GLOBAL update with all observations. A finite R uses only the observations with a location.",
        f"Collapse: sd ratio < {args.collapse:g}. Acceptable: collapse share <= {args.max_collapse:g}.",
        f"Wall time {wall:.1f} s. No simulation ran.",
        "",
        "| Rank | Radius (m) | Field | Median sd ratio | Collapse share | Mean abs shift (prior sd) | Truth in P5-P95 | RMSE of mean |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in dict.fromkeys(r["field"] for r in rows):
        own = [r for r in rows if r["field"] == name]
        lines += [table_row("prior", r) for r in own if r["radius"] == "prior"]
        for n, row in enumerate(rank(own, args.max_collapse), start=1):
            lines.append(table_row(str(n) if acceptable(row, args.max_collapse) else "-", row))
    order = "the lowest RMSE against the truth" if args.truth_field else "the largest radius"
    lines += [
        "",
        "A larger radius lets each observation update more cells: the spread falls more and the collapse share rises.",
        f"Rank: the acceptable radii by {order}. Rank '-' collapses too much.",
        "Rule: drop the radii with an unacceptable collapse share. Then run the full ES-MDA for the top 1 or 2 radii and",
        "compare their misfit and field statistics. The sweep does not measure the data fit; the ES-MDA runs do.",
        "Report the radius in the decision memo: it is a modelling choice.",
    ]
    (out / "sweep.md").write_text("\n".join(lines) + "\n")


def row_label(row: dict) -> str:
    return "prior" if row["radius"] == "prior" else radius_label(row["radius"])


def fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def table_row(rank_text: str, row: dict) -> str:
    cells = [row_label(row), row["field"], *(fmt(row.get(k)) or "-" for k in CSV_FIELDS[4:9])]
    return f"| {rank_text} | " + " | ".join(cells) + " |"


def run(args) -> int:
    import numpy as np
    from ert.config import Field, SurfaceConfig
    from ert.storage import open_storage

    start = time.perf_counter()
    radii = parse_radii(args.radii)
    if not args.storage.is_dir():
        raise InputError(f"--storage {args.storage} not found")
    if not args.config.is_file():
        raise InputError(f"--config {args.config} not found")
    config = load_settings(args.config.resolve())
    analysis = config.analysis_config
    seed = args.seed if args.seed is not None else config.random_seed
    if seed is None:
        raise InputError("the config has no RANDOM_SEED. Give --seed")
    args.seed_used = seed
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="radius_sweep_") as tmp:
        copy = copy_storage(args.storage.resolve(), Path(tmp) / "storage")
        with open_storage(copy, "r") as storage:
            prior = next((e for e in storage.ensembles if e.name == args.prior), None)
            if prior is None:
                names = sorted({e.name for e in storage.ensembles})
                raise InputError(f"--prior {args.prior} not in the storage. Ensembles: {', '.join(names)}")
            configs = prior.experiment.parameter_configuration
            params = [n for n, c in configs.items() if isinstance(c, Field | SurfaceConfig)]
            if args.field:
                if args.field not in params:
                    raise InputError(
                        f"--field {args.field} is not a FIELD or SURFACE parameter: {', '.join(params)}"
                    )
                params = [args.field]
            if not params:
                raise InputError("the experiment has no FIELD or SURFACE parameter. DISTANCE does not apply")
            if args.truth_field and not (args.field and isinstance(configs[args.field], Field)):
                raise InputError("--truth-field needs --field <a FIELD parameter>")
            index = np.flatnonzero(prior.get_realization_mask_with_responses())
            if index.size < 2:
                raise InputError(f"prior {args.prior} has {index.size} realizations with responses")
            masks, priors, truths = {}, {}, {}
            for name in params:
                cfg = configs[name]
                masks[name] = (
                    active_mask(config.ensemble_config.parameter_configs.get(name, cfg))
                    if isinstance(cfg, Field)
                    else None
                )
                priors[name] = prior.load_parameters_numpy(name, index)
            if args.truth_field:
                truth = read_truth(args.truth_field, configs[args.field])
                truths[args.field] = truth if masks[args.field] is None else truth[masks[args.field]]
                if not np.isfinite(truths[args.field]).all():
                    raise InputError(
                        f"--truth-field {args.truth_field}: values are missing or not positive for the transform"
                    )

            def select(name, array):
                return array if masks[name] is None else array[masks[name]]

            for name in params:
                p = select(name, priors[name])
                row = {"radius": "prior", "field": name, "n_cells": int((p.std(axis=1, ddof=1) > 0).sum())}
                if name in truths:
                    row["truth_in_p5_p95"], row["rmse_mean"] = coverage_rmse(p, truths[name])
                rows.append(row)
            located = located_keys(prior)
            n_located = len(located)
            for radius in radii:
                t0 = time.perf_counter()
                posterior, used = replay(
                    prior,
                    radius,
                    params,
                    analysis.es_settings.enkf_truncation,
                    analysis.observation_settings,
                    seed,
                    located,
                )
                seconds = time.perf_counter() - t0
                for name in params:
                    m = field_metrics(
                        select(name, priors[name]),
                        select(name, posterior[name]),
                        truths.get(name),
                        args.collapse,
                    )
                    rows.append(
                        {
                            "radius": radius,
                            "field": name,
                            "n_obs_used": used,
                            "seconds": seconds,
                            **dataclasses.asdict(m),
                        }
                    )
                print(f"R={radius_label(radius)}: {seconds:.1f} s", flush=True)
    wall = time.perf_counter() - start
    write_outputs(args.out, rows, args, wall, n_located)
    print((args.out / "sweep.md").read_text())
    print(f"Wrote {args.out / 'sweep.csv'} and sweep.md. Wall time {wall:.1f} s")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--storage", required=True, type=Path, help="ENSPATH of the run (copied, never opened)"
    )
    parser.add_argument("--config", required=True, type=Path, help="The .ert file of the run")
    parser.add_argument("--prior", required=True, help="Name of the prior ensemble, for example iter-0")
    parser.add_argument(
        "--radii", default="50,100,200,400,800,inf", help="Comma list in metres; inf = GLOBAL"
    )
    parser.add_argument("--field", help="Only this FIELD or SURFACE parameter (default: all)")
    parser.add_argument("--truth-field", type=Path, help="A GRDECL/ROFF truth of --field (synthetic cases)")
    parser.add_argument("--collapse", type=float, default=0.2, help="sd ratio below this is a collapse")
    parser.add_argument("--max-collapse", type=float, default=0.05, help="Acceptable collapse share")
    parser.add_argument("--seed", type=int, help="Default: RANDOM_SEED of the config")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        import ert  # noqa: F401
        import numpy  # noqa: F401
        import xtgeo  # noqa: F401
    except ImportError as error:
        print(f"ERROR: {error.name} not installed. Use the Komodo Python with ERT 26", file=sys.stderr)
        return 2
    if args.out.exists() and any(args.out.iterdir()):
        print(f"ERROR: --out {args.out} is not empty", file=sys.stderr)
        return 2
    if args.out.resolve() == args.storage.resolve() or args.storage.resolve() in args.out.resolve().parents:
        print("ERROR: --out is inside the storage", file=sys.stderr)
        return 2
    try:
        return run(args)
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
