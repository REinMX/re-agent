#!/usr/bin/env python3
"""Generate numerical P10/P50/P90 or paired static/dynamic designs with fmudesign."""

from __future__ import annotations

import argparse
import fnmatch
import math
import random
import sys
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract/scripts"))
from runpath_lib import realization_number  # noqa: E402
from uncertainty_register import parse_genkw_definitions  # noqa: E402


def read_priors(paths: list[Path]) -> dict[str, tuple[str, list[float]]]:
    priors = {}
    for path in paths:
        for name, dist, arguments in parse_genkw_definitions(path):
            if name in priors or name in {"REAL", "SENSNAME", "SENSCASE", "base"}:
                raise ValueError(f"duplicate or reserved parameter {name}")
            args = [float(a) for a in arguments]
            size = {
                "CONST": 1,
                "UNIFORM": 2,
                "LOGUNIF": 2,
                "NORMAL": 2,
                "LOGNORMAL": 2,
                "TRUNCATED_NORMAL": 4,
            }.get(dist)
            if size is None or len(args) != size or not all(math.isfinite(a) for a in args):
                raise ValueError(f"{name}: unsupported or malformed prior {dist} {arguments}")
            priors[name] = (dist, args)
            quantile(priors[name], 0.5)
    if not priors:
        raise ValueError("No priors found")
    return priors


def quantile(prior: tuple[str, list[float]], probability: float) -> float:
    dist, args = prior
    if dist == "CONST":
        return args[0]
    a, b = args[:2]
    if dist in {"UNIFORM", "LOGUNIF"}:
        if a >= b or (dist == "LOGUNIF" and a <= 0):
            raise ValueError(f"invalid bounds for {dist}")
        return (
            a + probability * (b - a)
            if dist == "UNIFORM"
            else math.exp(math.log(a) + probability * (math.log(b) - math.log(a)))
        )
    if b <= 0:
        raise ValueError(f"{dist} standard deviation must be positive")
    normal = NormalDist(a, b)
    if dist == "TRUNCATED_NORMAL":
        low, high = args[2:]
        if low >= high or normal.cdf(low) >= normal.cdf(high):
            raise ValueError("invalid TRUNCATED_NORMAL bounds")
        probability = normal.cdf(low) + probability * (normal.cdf(high) - normal.cdf(low))
    value = normal.inv_cdf(probability)
    return math.exp(value) if dist == "LOGNORMAL" else value


def design_rows(priors, mode: str, groups: str, seed: int) -> list[dict]:
    base = {name: quantile(prior, 0.5) for name, prior in priors.items()}
    rows = [dict(REAL=0, SENSNAME="base", SENSCASE="ref", **base)]
    if mode == "onebyone":
        for name, prior in priors.items():
            if prior[0] == "CONST":
                continue
            for case, probability in [("low", 0.1), ("high", 0.9)]:
                values = dict(base)
                values[name] = quantile(prior, probability)
                rows.append(dict(REAL=len(rows), SENSNAME=name, SENSCASE=case, **values))
        return rows
    patterns = {}
    for group in groups.split(";"):
        key, sep, value = group.partition("=")
        if not sep or key not in {"static", "dynamic"} or key in patterns or not value:
            raise ValueError("groups must be static=<patterns>;dynamic=<patterns>")
        patterns[key] = value.split(",")
    if set(patterns) != {"static", "dynamic"}:
        raise ValueError("both static and dynamic groups are required")
    assigned = {}
    for name, prior in priors.items():
        if prior[0] == "CONST":
            continue
        matches = [g for g, pats in patterns.items() if any(fnmatch.fnmatchcase(name, p) for p in pats)]
        if len(matches) != 1:
            raise ValueError(f"{name} must match exactly one group")
        assigned[name] = matches[0]
    if set(assigned.values()) != {"static", "dynamic"}:
        raise ValueError("each group must contain parameters")
    rng = random.Random(seed)
    sampled = {n: quantile(p, 0.5 if p[0] == "CONST" else rng.random()) for n, p in priors.items()}
    for case in ["static", "dynamic", "all"]:
        values = {n: sampled[n] if case == "all" or assigned.get(n) == case else base[n] for n in priors}
        rows.append(dict(REAL=len(rows), SENSNAME=case, SENSCASE=case, **values))
    return rows


def write_design(priors, mode: str, groups: str, seed: int, out: Path) -> Path:
    import pandas as pd

    backend = "fmudesign"
    try:
        try:
            from fmudesign._excel_to_dict import excel_to_dict
            from fmudesign.create_design import DesignMatrix
        except ModuleNotFoundError as exc:
            if exc.name != "fmudesign":
                raise
            backend = "semeio.fmudesign"
            try:
                from semeio.fmudesign._excel_to_dict import excel_to_dict
                from semeio.fmudesign.create_design import DesignMatrix
            except ModuleNotFoundError as exc:
                if exc.name not in {"semeio", "semeio.fmudesign"}:
                    raise
                raise ValueError(
                    "fmudesign is required. Use fmudesign (ERT 26) or semeio.fmudesign (ERT 23)."
                ) from exc
    except ImportError as exc:
        raise ValueError(
            f"{backend} import/API error: {exc}. Check the backend API and its dependencies."
        ) from exc
    if realization_number(out.resolve()) is not None:
        raise ValueError("Do not write designs inside a realization runpath")
    rows = design_rows(priors, mode, groups, seed)
    names = list(priors)
    config_rows = [dict(sensname="base", type="ref", param_name=None)]
    if mode == "onebyone":
        varying = [name for name in names if priors[name][0] != "CONST"]
        for i, name in enumerate(varying):
            config_rows.append(
                dict(
                    sensname=name,
                    type="scenario",
                    param_name=name,
                    senscase1="low",
                    value1=rows[1 + 2 * i][name],
                    senscase2="high",
                    value2=rows[2 + 2 * i][name],
                )
            )
    else:
        for row in rows[1:]:
            for i, name in enumerate(names):
                config_rows.append(
                    dict(
                        sensname=row["SENSCASE"] if i == 0 else None,
                        type="scenario",
                        param_name=name,
                        senscase1=row["SENSCASE"],
                        value1=row[name],
                        senscase2=None,
                        value2=None,
                    )
                )
    out.mkdir(parents=True, exist_ok=True)
    config = out / "fmudesign.xlsx"
    with pd.ExcelWriter(config) as writer:
        pd.DataFrame(
            [["designtype", "onebyone"], ["repeats", 1], ["distribution_seed", seed], ["rms_seeds", "None"]]
        ).to_excel(writer, sheet_name="general_input", header=False, index=False)
        pd.DataFrame(config_rows).to_excel(writer, sheet_name="designinput", index=False)
        pd.DataFrame([[n, rows[0][n]] for n in names], columns=["parameter", "value"]).to_excel(
            writer, sheet_name="defaultvalues", index=False
        )
    try:
        design = DesignMatrix()
        design.generate(excel_to_dict(str(config)))
        for name, prior in priors.items():
            if prior[0] == "CONST" and name not in design.designvalues:
                design.designvalues[name] = prior[1][0]
        expected = pd.DataFrame(rows)
        actual = design.designvalues
        if len(actual) != len(expected) or not all(
            math.isclose(float(actual.iloc[i][n]), float(expected.iloc[i][n]), rel_tol=1e-12, abs_tol=1e-12)
            for i in range(len(rows))
            for n in names
        ):
            raise ValueError("fmudesign output does not match requested paired design")
        destination = out / "design.xlsx"
        design.to_xlsx(str(destination), designsheet="DesignSheet", defaultsheet="DefaultValues")
    except (ImportError, AttributeError, TypeError) as exc:
        raise ValueError(f"{backend} API error: {exc}. Check the backend API and its dependencies.") from exc
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, nargs="+", required=True)
    parser.add_argument("--mode", choices=["onebyone", "attribution"], required=True)
    parser.add_argument("--groups", default="static=PORO_*,OWC_*;dynamic=PERM_*,MULTFLT_*,KR*,SCAL_*")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        priors = read_priors(args.dist)
        path = write_design(priors, args.mode, args.groups, args.seed, args.out.resolve())
        count = (
            1 + 2 * sum(prior[0] != "CONST" for prior in priors.values()) if args.mode == "onebyone" else 4
        )
        print(
            f"NUM_REALIZATIONS {count}\nDESIGN_MATRIX {path} DESIGN_SHEET:DesignSheet DEFAULT_SHEET:DefaultValues"
        )
        if args.mode == "onebyone":
            print("Numerical P10/P50/P90. One-by-one ignores interactions.")
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
