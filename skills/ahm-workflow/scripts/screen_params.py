#!/usr/bin/env python3
"""Screen the uncertain parameters before an ES-MDA, and write the screening record that ahm_preflight.py needs.

Inputs:
  --design      the one-by-one design (fmudesign, make_design.py --mode onebyone): .xlsx with a DesignSheet, or
                .csv, with REAL, SENSNAME, SENSCASE and one column per parameter
  --residuals   residuals.csv of misfit_report.py on the runpaths of that design (one ensemble)
  --observations  the expected observations, made from the observation file of the config and not from
                residuals.csv: a CSV with OBS, OBSERVED, ERROR and, when residuals.csv has them, KEY and DATE.
                Without it the record is a diagnostic, and ahm_preflight.py never reports ready from it.
  --dist        the GEN_KW distribution files of the AHM config
  --influence   influence.csv of ahm_influence.py (AHM_ANALYSIS on the prior ensemble), or
  --no-ahm-analysis REASON
For each sensitivity it takes the low and the high run and computes d = (sim_high - sim_low) / error over the
observations. It reports, per parameter:
  chi2_swing    sum of d^2: the change of data misfit over the design swing. Measured.
  sd_ratio_lin  linearized joint posterior sd over prior sd: (G'G + prior precision)^-1 with the swings as the
                columns of G. A parameter that moves the data like another one keeps a ratio near 1. Inferred.
  partner, cos  the most collinear other parameter and the cosine of the two swing vectors
  ks_all, ks_groups_above_floor   from AHM_ANALYSIS, when given
Keep when chi2_swing >= --min-chi2 (4) and sd_ratio_lin <= --max-ratio (0.5). Each drop has its reasons.
On drogon-lite this dropped ROCK_CMULT: sd ratio 0.73, collinear with AQ_VOL (cos 0.97).
One-by-one ignores interactions, and the linearization ignores FIELD parameters.

    screen_params.py --design design.xlsx --residuals <dir>/residuals.csv --observations observations.csv \
        --dist a.dist b.dist --influence influence.csv --out <output_root>/screening
Writes screening.json (the record, schema ahm-screening/2) and screening.md. The record holds the prior
definitions, the member and observation lists, and the path (relative to the record) and sha256 of each input file.
It refuses, and writes nothing, when a member or an observation is missing, a row is duplicated, a SIMULATED or
ERROR value is not finite, an ERROR is not positive, a design REAL is not a unique whole number, or the
OBSERVED, ERROR, KEY or DATE of a row differs from --observations.
Exit code: 0 = written, 1 = input problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def _load(name: str, folder: Path, filename: str | None = None):
    spec = importlib.util.spec_from_file_location(name, folder / (filename or f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


prior_qc = _load("prior_qc", HERE.parents[1] / "history-match-qc" / "scripts")
evidence = _load("screening_evidence", HERE)
residual_coverage = _load(
    "screening_coverage", HERE.parents[1] / "history-match-qc" / "scripts", "coverage.py"
)
SCHEMA = evidence.SCHEMA


@dataclass(frozen=True)
class ScreenLimits:
    min_chi2: float = 4.0
    max_ratio: float = 0.5


@dataclass
class ParamScreen:
    name: str
    low: float
    high: float
    chi2_swing: float
    sd_ratio_lin: float
    partner: str | None
    cosine: float | None
    keep: bool
    reasons: list[str] = field(default_factory=list)
    ks_all: float | None = None
    ks_groups_above_floor: int | None = None


@dataclass
class ScreeningRecord:
    schema: str
    created: str
    design: str
    residuals: str
    dist_sha256: dict[str, str]
    ahm_analysis: str
    limits: dict
    observations: int
    parameters: list[ParamScreen]
    kept: list[str]
    dropped: list[str]
    priors: dict
    domain: dict
    observation_metadata: dict
    evidence: dict


def read_design(path: Path):
    import pandas as pd

    table = (
        pd.read_excel(path, sheet_name="DesignSheet")
        if path.suffix in (".xlsx", ".xls")
        else pd.read_csv(path)
    )
    missing = {"REAL", "SENSNAME", "SENSCASE"} - set(table.columns)
    if missing:
        raise ValueError(f"{path}: the design has no column {sorted(missing)}")
    try:
        real = table["REAL"].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: design REAL must be finite nonnegative integer member identities") from exc
    if not (np.isfinite(real).all() and (real >= 0).all() and (real == np.floor(real)).all()):
        raise ValueError("design REAL must be finite nonnegative integer member identities")
    if table["REAL"].duplicated().any():
        raise ValueError("design REAL must be unique")
    if table["SENSNAME"].isna().any():
        raise ValueError("design SENSNAME must be set on every row")
    return table


def swings(design, sim, error: np.ndarray) -> tuple[list[str], list[tuple[float, float]], np.ndarray]:
    """Names, (low, high) values, and the swing matrix G (observations x parameters) in error units."""
    names, ranges, columns = [], [], []
    for name, cases in design[design.SENSNAME.astype(str).str.lower() != "base"].groupby(
        "SENSNAME", sort=False
    ):
        if name not in cases.columns:
            raise ValueError(f"sensitivity {name} has no parameter column of that name")
        try:
            values = cases[name].to_numpy(dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"sensitivity {name}: the design column {name} must be numeric") from exc
        if not (np.isfinite(values).all() and values.min() < values.max()):
            raise ValueError(
                f"sensitivity {name}: the design column {name} needs finite values with a low and a high"
            )
        lo_row, hi_row = cases.loc[cases[name].idxmin()], cases.loc[cases[name].idxmax()]
        lo, hi = int(lo_row.REAL), int(hi_row.REAL)
        if lo == hi or lo not in sim.index or hi not in sim.index:
            raise ValueError(f"sensitivity {name}: no simulated low and high realization")
        d = (sim.loc[hi].to_numpy(dtype=float) - sim.loc[lo].to_numpy(dtype=float)) / error
        if not np.isfinite(d).all():
            raise ValueError(f"sensitivity {name}: the swing is not finite; check SIMULATED and ERROR")
        columns.append(d)
        names.append(str(name))
        ranges.append((float(lo_row[name]), float(hi_row[name])))
    if not names:
        raise ValueError("the design has no sensitivity besides base")
    return names, ranges, np.array(columns).T


def prior_sd_in_swing_units(param: dict, low: float, high: float) -> float:
    """Prior sd of the parameter, in units of the design swing, in the prior position scale."""
    ends = prior_qc.position(param, low), prior_qc.position(param, high)
    span = math.inf if None in ends else ends[1] - ends[0]
    if not 0 < span < math.inf:
        raise ValueError(f"{param['name']}: the design swing has no finite width in a bounded prior")
    sd_position = 1 / 6 if param["distribution"] in ("NORMAL", "LOGNORMAL") else 1 / math.sqrt(12)
    return sd_position / span


def screen(
    names: list[str],
    ranges: list[tuple[float, float]],
    g: np.ndarray,
    params: dict[str, dict],
    limits: ScreenLimits = ScreenLimits(),  # noqa: B008  frozen dataclass
    influence: dict[str, tuple[float, int]] | None = None,
) -> list[ParamScreen]:
    sd = []
    for name, (low, high) in zip(names, ranges, strict=True):
        param = prior_qc.match({name: 0.0}, params)[0]
        if not param:
            raise ValueError(f"{name}: not in the distribution files")
        sd.append(prior_sd_in_swing_units(params[next(iter(param))], low, high))
    sd = np.array(sd)
    cov = np.linalg.inv(g.T @ g + np.diag(1 / sd**2))
    norm = np.linalg.norm(g, axis=0)
    scale = np.outer(norm, norm)
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        cosine = np.where(scale > 0, (g.T @ g) / scale, 0.0)  # a column without any swing has no direction
    out = []
    for n, name in enumerate(names):
        chi2 = float(np.sum(g[:, n] ** 2))
        ratio = float(math.sqrt(cov[n, n]) / sd[n]) if cov[n, n] > 0 else math.nan
        if not (math.isfinite(chi2) and math.isfinite(ratio) and np.isfinite(cosine[n]).all()):
            raise ValueError(
                f"{name}: chi2 swing, sd ratio or cosine is not finite; check SIMULATED, ERROR and REAL"
            )
        others = [m for m in np.argsort(-np.abs(cosine[n])) if m != n]
        partner = names[others[0]] if others else None
        cos = float(cosine[n, others[0]]) if others else None
        reasons = []
        if chi2 < limits.min_chi2:
            reasons.append(f"insensitive: chi2 swing {chi2:.3g} < {limits.min_chi2:g}")
        if ratio > limits.max_ratio:
            text = f"not resolved jointly: linear sd ratio {ratio:.2f} > {limits.max_ratio:g}"
            if partner is not None:
                text += f"; moves the data like {partner} (cos {cos:.2f})"
            reasons.append(text)
        item = ParamScreen(name, *ranges[n], chi2, ratio, partner, cos, not reasons, reasons)
        if influence is not None:
            hit = prior_qc.match({name: 0.0}, {k: {} for k in influence})[0]
            if hit:
                item.ks_all, item.ks_groups_above_floor = influence[next(iter(hit))]
                if item.ks_groups_above_floor == 0:
                    item.reasons.append(
                        "AHM_ANALYSIS: no observation group above the KS noise floor (evidence only)"
                    )
        out.append(item)
    return out


def read_influence(path: Path) -> dict[str, tuple[float, int]]:
    """parameter -> (ks_all, number of groups above the noise floor), from ahm_influence.py influence.csv."""
    import pandas as pd

    table = pd.read_csv(path)
    missing = {"parameter", "ks_all", "above_floor"} - set(table.columns)
    if missing:
        raise ValueError(f"{path}: no column {sorted(missing)} (expected ahm_influence.py influence.csv)")
    out = {}
    for name, group in table.groupby("parameter"):
        above = group["above_floor"].astype(str).str.lower().eq("true").sum()
        ks_all = float(group["ks_all"].iloc[0])
        if not math.isfinite(ks_all):
            raise ValueError(f"{path}: ks_all of {name} must be finite")
        out[prior_qc.bare(str(name))] = (ks_all, int(above))
    return out


def read_observations(path: Path) -> dict[str, dict]:
    """The independent observation inventory: OBS, OBSERVED, ERROR and, when present, KEY and DATE."""
    import pandas as pd

    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = {"OBS", "OBSERVED", "ERROR"} - set(table.columns)
    if missing:
        raise ValueError(f"{path}: the observation inventory has no column {sorted(missing)}")
    text_columns = [c for c in ("KEY", "DATE") if c in table.columns]
    inventory = {}
    for row in table.to_dict("records"):
        name = row["OBS"]
        if not name or name in inventory:
            raise ValueError(f"{path}: the observation inventory has an empty or repeated OBS {name!r}")
        meta = {"OBS": name}
        for column in ("OBSERVED", "ERROR"):
            try:
                meta[column] = float(row[column])
            except ValueError:
                meta[column] = math.nan
            if not math.isfinite(meta[column]):
                raise ValueError(f"{path}: {column} of observation {name} is not a finite number")
        if not meta["ERROR"] > 0:
            raise ValueError(f"{path}: ERROR of observation {name} must be positive")
        for column in text_columns:
            if not row[column]:
                raise ValueError(f"{path}: {column} of observation {name} is empty")
            meta[column] = row[column]
        inventory[name] = meta
    if not inventory:
        raise ValueError(f"{path}: the observation inventory has no observation")
    return inventory


def residual_metadata(part) -> dict[str, dict]:
    """OBSERVED, ERROR and, when the table has them, KEY and DATE of each observation of a validated ensemble."""
    columns = ["OBSERVED", "ERROR"] + [c for c in ("KEY", "DATE") if c in part.columns]
    first = part.groupby("OBS")[columns].first()
    return {
        str(obs): {"OBS": str(obs)}
        | {c: float(row[c]) if c in ("OBSERVED", "ERROR") else str(row[c]) for c in columns}
        for obs, row in first.iterrows()
    }


def calendar_day(value) -> str:
    """The YYYY-MM-DD day, so a midnight suffix is not a new observation."""
    text = str(value)
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-" and text[:10].replace("-", "").isdigit():
        return text[:10]
    return text


def inventory_problems(found: dict[str, dict], inventory: dict[str, dict]) -> list[str]:
    """Where the metadata of the residual rows differs from the independent inventory."""

    def shown(obs: str, column: str, side: dict) -> object:
        value = side.get(column)
        if column == "DATE":
            return calendar_day(value)
        return value

    return [
        f"{column} of {obs} is {found[obs].get(column)!r} in residuals.csv and {meta.get(column)!r} in the inventory"
        for obs, meta in inventory.items()
        for column in sorted(meta.keys() | found[obs].keys())
        if shown(obs, column, found[obs]) != shown(obs, column, meta)
    ]


def markdown(record: ScreeningRecord) -> str:
    lines = [
        "# Parameter screening before ES-MDA",
        "",
        f"Kept: {', '.join(record.kept) or 'none'}. Dropped: {', '.join(record.dropped) or 'none'}.",
        f"AHM_ANALYSIS: {record.ahm_analysis}. {record.observations} observations. Swings measured, sd ratio inferred.",
        "",
    ]
    if not record.domain["observations_authoritative"]:
        lines += ["Diagnostic only: no --observations inventory. ahm_preflight.py refuses this record.", ""]
    lines += [
        "| parameter | low | high | chi2 swing | sd ratio (linear) | most collinear (cos) | KS all | keep | reasons |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for p in sorted(record.parameters, key=lambda p: -p.chi2_swing):
        ks = "n/a" if p.ks_all is None else f"{p.ks_all:.2f}"
        cos = "n/a" if p.cosine is None else f"{p.partner} ({p.cosine:.2f})"
        lines.append(
            f"| {p.name} | {p.low:.4g} | {p.high:.4g} | {p.chi2_swing:.1f} | {p.sd_ratio_lin:.2f} | {cos} | {ks} |"
            f" {'yes' if p.keep else 'no'} | {'; '.join(p.reasons)} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", type=Path, required=True)
    ap.add_argument("--residuals", type=Path, required=True)
    ap.add_argument(
        "--observations", type=Path, help="CSV of the expected observations, not made from residuals.csv"
    )
    ap.add_argument("--ensemble", help="ENSEMBLE label in residuals.csv (default: the only one)")
    ap.add_argument("--dist", type=Path, nargs="+", required=True)
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--influence", type=Path, help="influence.csv of ahm_influence.py")
    source.add_argument("--no-ahm-analysis", metavar="REASON", help="why AHM_ANALYSIS did not run")
    ap.add_argument("--min-chi2", type=float, default=ScreenLimits.min_chi2)
    ap.add_argument("--max-ratio", type=float, default=ScreenLimits.max_ratio)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.no_ahm_analysis is not None and not args.no_ahm_analysis.strip():
        print("ERROR: --no-ahm-analysis needs a reason", file=sys.stderr)
        return 2
    if not (
        math.isfinite(args.min_chi2)
        and args.min_chi2 >= 0
        and math.isfinite(args.max_ratio)
        and args.max_ratio > 0
    ):
        print(
            "ERROR: --min-chi2 must be finite and not negative, --max-ratio finite and positive",
            file=sys.stderr,
        )
        return 2
    import pandas as pd

    try:
        params = evidence.read_priors(args.dist)
        table = pd.read_csv(args.residuals)
        missing = {"ENSEMBLE", "REAL", "OBS", "OBSERVED", "ERROR", "SIMULATED"} - set(table.columns)
        if missing:
            raise ValueError(
                f"{args.residuals}: no column {sorted(missing)} (expected misfit_report.py residuals.csv)"
            )
        labels = table["ENSEMBLE"].unique().tolist()
        label = args.ensemble or (labels[0] if len(labels) == 1 else None)
        if label is None:
            raise ValueError(f"residuals.csv has ensembles {labels}; give --ensemble")
        design = read_design(args.design)
        inventory = read_observations(args.observations) if args.observations else None
        try:
            residual_coverage.from_residuals(
                table,
                label,
                expected_members={int(real) for real in design["REAL"]},
                expected_obs=None if inventory is None else set(inventory),
            )
        except ValueError as exc:
            raise ValueError(f"incomplete or invalid simulated residual coverage: {exc}") from exc
        part = table[table["ENSEMBLE"] == label]
        part = part.assign(REAL=pd.to_numeric(part["REAL"]).astype(int))
        found = residual_metadata(part)
        differs = [] if inventory is None else inventory_problems(found, inventory)
        if differs:
            raise ValueError("observation metadata differs from the inventory: " + "; ".join(differs[:5]))
        sim = part.pivot(index="REAL", columns="OBS", values="SIMULATED")
        error = part.groupby("OBS")["ERROR"].first().loc[sim.columns].to_numpy(dtype=float)
        names, ranges, g = swings(design, sim, error)
        influence = read_influence(args.influence) if args.influence else None
        limits = ScreenLimits(args.min_chi2, args.max_ratio)
        result = screen(names, ranges, g, params, limits, influence)
        sources = {"design": args.design, "residuals": args.residuals}
        sources |= {"observations": args.observations} if inventory else {}
        sources |= {"influence": args.influence} if args.influence else {}
        domain = {
            "ensemble": str(label),
            "members": [int(m) for m in sim.index],
            "observations": [str(c) for c in sim.columns],
            "observations_authoritative": inventory is not None,
        }
        record = ScreeningRecord(
            schema=SCHEMA,
            created=datetime.now().astimezone().isoformat(timespec="seconds"),
            design=str(args.design.resolve()),
            residuals=str(args.residuals.resolve()),
            dist_sha256={str(p.resolve()): evidence.sha256(p) for p in args.dist},
            ahm_analysis=str(args.influence.resolve())
            if args.influence
            else f"not run: {args.no_ahm_analysis}",
            limits=asdict(limits),
            observations=int(g.shape[0]),
            parameters=result,
            kept=[p.name for p in result if p.keep],
            dropped=[p.name for p in result if not p.keep],
            priors=evidence.prior_snapshot(params),
            domain=domain,
            observation_metadata={obs: (inventory or found)[obs] for obs in domain["observations"]},
            evidence={name: evidence.evidence_entry(args.out, path) for name, path in sources.items()},
        )
        text = json.dumps(asdict(record), indent=1, allow_nan=False) + "\n"
    except (OSError, KeyError, ValueError, np.linalg.LinAlgError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "screening.json").write_text(text)
    (args.out / "screening.md").write_text(markdown(record))
    if inventory is None:
        print(
            "WARNING: no --observations inventory. The record is a diagnostic. ahm_preflight.py refuses it",
            file=sys.stderr,
        )
    print(markdown(record))
    print(f"Wrote {args.out / 'screening.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
