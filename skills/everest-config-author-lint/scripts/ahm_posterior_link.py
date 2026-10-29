#!/usr/bin/env python3
"""Prepare a posterior snippet. Source ensemble files are never changed."""

import argparse
import csv
import glob
import json
import math
import re
import statistics
from pathlib import Path

import yaml
from everest_lint import read_contract


def rows(path):
    with Path(path).open(newline="") as stream:
        data = list(csv.DictReader(stream))
    if not data or "realization" not in data[0]:
        raise ValueError("CSV requires nonempty rows and a realization column")
    ids = [int(row["realization"]) for row in data]
    if len(set(ids)) != len(ids) or any(i < 0 for i in ids):
        raise ValueError("duplicate or negative realization ids")
    for row in data:
        for key, value in row.items():
            if key == "realization":
                row[key] = int(value)
            else:
                row[key] = float(value)
                if not math.isfinite(row[key]):
                    raise ValueError(f"nonfinite parameter or response {key}")
    return data


def collapsed_flag(data):
    if isinstance(data, dict):
        return any(
            (
                key in {"collapsed", "spread_collapsed", "ensemble_collapsed", "collapse_warning"}
                and value is True
            )
            or collapsed_flag(value)
            for key, value in data.items()
        )
    if isinstance(data, list):
        return any(collapsed_flag(item) for item in data)
    return isinstance(data, str) and "collapse" in data.lower() and "no collapse" not in data.lower()


def prepare(
    posterior=None,
    runpath=None,
    prior=None,
    qc=None,
    threshold=0.1,
    override=False,
    response=None,
    data_source=None,
    template_source=None,
    target="parameters.json",
):
    if not 0 < threshold <= 1:
        raise ValueError("spread threshold must be in (0, 1]")
    if bool(posterior) == bool(runpath):
        raise ValueError("provide exactly one posterior CSV or runpath pattern")
    data = rows(posterior) if posterior else None
    reasons = []
    ratios = {}
    if qc:
        payload = json.loads(Path(qc).read_text())
        if not isinstance(payload, dict) or not payload:
            raise ValueError("QC JSON must be a nonempty mapping")
        if collapsed_flag(payload):
            reasons.append("QC JSON flags collapsed posterior spread")
        if payload.get("passed") is False or payload.get("errors"):
            reasons.append("QC JSON reports failed history-match QC")
    if prior:
        if data is None:
            raise ValueError("spread comparison requires posterior CSV")
        before = rows(prior)
        parameters = set(data[0]) - {"realization", response}
        if not parameters or not parameters <= set(before[0]) or len(data) < 2 or len(before) < 2:
            raise ValueError("spread check requires matching parameter columns and >=2 members")
        for key in sorted(parameters):
            prior_std = statistics.stdev(row[key] for row in before)
            post_std = statistics.stdev(row[key] for row in data)
            if prior_std == 0:
                raise ValueError(f"prior std is zero for {key}; cannot assess spread")
            ratios[key] = post_std / prior_std
            if ratios[key] < threshold:
                reasons.append(f"{key}: std/prior std={ratios[key]:.6g} below {threshold}")
    if not prior and not qc:
        reasons.append("posterior spread is unchecked: supply prior CSV or QC JSON")
    if reasons and not override:
        raise ValueError("refuse posterior link: " + "; ".join(reasons) + "; human --override required")
    if data is not None:
        ids = [row["realization"] for row in data]
    else:
        paths = sorted(glob.glob(str(runpath).replace("<N>", "*").replace("<K>", "*")))
        ids = []
        iterations = set()
        for path in paths:
            match = re.search(r"(?:^|/)realization-(\d+)/iter-(\d+)$", path)
            if not match or not Path(path).is_dir():
                raise ValueError(f"unmatched runpath: {path}")
            ids.append(int(match[1]))
            iterations.add(int(match[2]))
        if not ids or len(set(ids)) != len(ids) or len(iterations) != 1:
            raise ValueError("runpath must match unique realizations in one posterior iteration")
        runpath = str(runpath).replace("<N>", "*")
        if runpath.count("*") != 1:
            raise ValueError("use an explicit posterior iteration and one realization wildcard")
        data_source = data_source or runpath.replace("*", "<REALIZATION_ID>") + "/parameters.json"
    selection = {}
    if response:
        if data is None or response not in data[0]:
            raise ValueError("representative subset requires a response column in posterior CSV")
        ordered = sorted(data, key=lambda row: (row[response], row["realization"]))
        ids = []
        for percentile in (10, 50, 90):
            position = (len(ordered) - 1) * percentile / 100
            low = math.floor(position)
            high = math.ceil(position)
            value = ordered[low][response] + (position - low) * (
                ordered[high][response] - ordered[low][response]
            )
            member = min(ordered, key=lambda row: (abs(row[response] - value), row["realization"]))[
                "realization"
            ]
            selection[f"P{percentile}"] = {"value": value, "realization": member}
            if member not in ids:
                ids.append(member)
    if not data_source and not template_source:
        raise ValueError("provide --data-source or --template-source with <REALIZATION_ID>")
    snippet = {"model": {"realizations": sorted(ids)}}
    for key, source in (("install_data", data_source), ("install_templates", template_source)):
        if source:
            if "<REALIZATION_ID>" not in source:
                raise ValueError(f"{key} source must contain <REALIZATION_ID>")
            snippet[key] = [{"source": source, "target": target}]
    return snippet, {
        "spread_ratios": ratios,
        "override": override,
        "warnings": reasons,
        "subset": selection,
        "percentile_convention": "numerical: P10 = 10th percentile low value; P90 = 90th percentile high value",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--posterior", type=Path)
    parser.add_argument("--runpath")
    parser.add_argument("--prior", type=Path)
    parser.add_argument("--qc", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--threshold", type=float, default=0.1)
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--response")
    parser.add_argument("--data-source")
    parser.add_argument("--template-source")
    parser.add_argument("--target", default="parameters.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        runpath = args.runpath
        if not args.posterior and not runpath and args.contract:
            runpath = read_contract(args.contract).get("ahm_posterior_case")
        snippet, receipt = prepare(
            args.posterior,
            runpath,
            args.prior,
            args.qc,
            args.threshold,
            args.override,
            args.response,
            args.data_source,
            args.template_source,
            args.target,
        )
        args.out.write_text(yaml.safe_dump(snippet, sort_keys=False))
        print(json.dumps(receipt, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"ERROR: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
