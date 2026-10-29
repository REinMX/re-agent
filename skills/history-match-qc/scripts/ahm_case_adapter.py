#!/usr/bin/env python3
"""Turn an exported AHM case (no ERT storage) into the inputs of misfit_report.py and ahm_overview.py.

Read-only on the case. Deterministic. For a case that has only runpath folders and a table of observations,
for example a webviz test-data folder: realization-<N>/iter-<K>/parameters.txt, <ECLBASE>.SMSPEC/.UNSMRY
without the .DATA deck, and share/observations/tables/ert_observations.yml (the fmu-tools YAML format).

Usage:
    ahm_case_adapter.py --prior-runpaths GLOB --posterior-runpaths GLOB --eclbase eclipse/model/DROGON-<IENS> \
        --observations ert_observations.yml --expected N [--allow-missing posterior:91 ...] \
        [--dist-reference FILE.dist ...] --outdir DIR [--run]

Steps:
    1. Members. Each ensemble must have every realization 0..N-1 with parameters.txt, SMSPEC and UNSMRY.
       A gap fails (exit 1) unless --allow-missing names it. The comparison uses the realizations that both
       ensembles have, and members.json records each one left out and why. A missing summary is never zero.
    2. Staging. runpaths/<label>/realization-<N>/iter-0 holds links to parameters.txt and the summary files,
       and an empty <ECLBASE>.DATA stub so that runpath_lib finds the case. No simulator file is copied.
    3. Observations. The smry section becomes SUMMARY_OBSERVATION blocks (ERROR_MODE ABS) in observations.obs.
       A section that the tools cannot use (for example rft without GEN_DATA files) is listed in members.json.
    4. Parameters. prior_params.csv and posterior_params.csv (REAL plus GROUP:NAME; LOG10_ columns dropped).
    5. Prior. The case has no distribution files, so prior.dist is rebuilt from the prior sample:
       constant -> CONST; a name in --dist-reference whose bounds hold every prior value -> that line;
       otherwise LOGUNIF (the case wrote LOG10_<name>) or UNIFORM with bounds rounded outward.
       prior_provenance.csv gives the source and a Kolmogorov-Smirnov p-value against the chosen prior.
    --run then calls misfit_report.py into misfit/ and ahm_overview.py into overview/.

Exit code: 0 = written, 1 = incomplete or inconsistent case, 2 = bad options.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))
import numpy as np
import runpath_lib
import yaml
from scipy import stats

HERE = Path(__file__).resolve().parent
LABELS = ("prior", "posterior")


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prior_qc = _load_sibling("prior_qc")


class CaseError(ValueError):
    """Incomplete or inconsistent case: exit 1."""


def realization_spec(numbers: list[int]) -> str:
    parts, start = [], None
    for i, n in enumerate(numbers):
        if start is None:
            start = n
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            parts.append(str(start) if start == n else f"{start}-{n}")
            start = None
    return ",".join(parts)


def parse_allow(items: list[str]) -> dict[str, set[int]]:
    allowed = {label: set() for label in LABELS}
    for item in items:
        label, sep, real = item.partition(":")
        if label not in allowed or not sep or not real.isdigit():
            raise ValueError(f"--allow-missing {item}: use prior:<N> or posterior:<N>")
        allowed[label].add(int(real))
    return allowed


def summary_files(runpath: Path, eclbase: str, real: int) -> tuple[Path, list[str]]:
    base = runpath / eclbase.replace("<IENS>", str(real))
    missing = [s for s in (".SMSPEC", ".UNSMRY") if not base.with_name(base.name + s).is_file()]
    if not (runpath / "parameters.txt").is_file():
        missing.append("parameters.txt")
    return base, missing


def members(pattern: str, eclbase: str, expected: set[int], allowed: set[int], label: str) -> dict:
    ensemble = runpath_lib.read_ensemble(pattern, expected=expected, need_data_file=False)
    if ensemble.dropped:
        raise CaseError(f"{label}: " + "; ".join(ensemble.problems()))
    found, left_out = {}, {}
    seen = set()
    for member in ensemble.found:
        if member.realization in seen:
            raise CaseError(f"{label}: realization {member.realization} matches more than one folder")
        seen.add(member.realization)
        base, missing = summary_files(member.runpath, eclbase, member.realization)
        if missing:
            left_out[member.realization] = f"{member.runpath}: missing {', '.join(missing)}"
        else:
            found[member.realization] = (member.runpath, base)
    for real in ensemble.missing:
        left_out[real] = "no realization folder"
    unexpected = sorted(set(left_out) - allowed)
    if unexpected:
        raise CaseError(
            f"{label}: partial ensemble, not allowed by --allow-missing: "
            + "; ".join(f"{r}: {left_out[r]}" for r in unexpected)
        )
    stale = sorted(allowed - set(left_out))
    if stale:
        raise CaseError(f"{label}: --allow-missing names realizations that are present: {stale}")
    return {"found": found, "left_out": left_out, "extra": sorted(d.realization for d in ensemble.extra)}


def stage(outdir: Path, label: str, found: dict, common: list[int], eclbase: str) -> str:
    root = outdir / "runpaths" / label
    stale = sorted(
        real for real, _ in runpath_lib.find_runpaths(str(root / "realization-*")) if real not in common
    )
    if stale:
        raise CaseError(f"{root}: realizations {stale} are left from an earlier run; use a new --outdir")
    for real in common:
        runpath, base = found[real]
        target = root / f"realization-{real}" / "iter-0"
        stem = Path(eclbase.replace("<IENS>", str(real)))
        (target / stem.parent).mkdir(parents=True, exist_ok=True)
        links = {target / "parameters.txt": runpath / "parameters.txt"}
        for suffix in (".SMSPEC", ".UNSMRY"):
            links[target / f"{stem}{suffix}"] = base.with_name(base.name + suffix)
        for link, source in links.items():
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(source.resolve())
        (target / f"{stem}.DATA").write_text("")
    return str(root / "realization-*" / "iter-0")


def observations(path: Path) -> tuple[str, int, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("smry"), list):
        raise CaseError(f"{path}: needs a 'smry' list")
    unused = sorted(k for k in data if k != "smry")
    blocks, names = [], set()
    for vector in data["smry"]:
        key = vector.get("key")
        if not key or not isinstance(vector.get("observations"), list):
            raise CaseError(f"{path}: smry item without key or observations: {vector}")
        for obs in vector["observations"]:
            name = str(obs.get("label", "")).strip()
            if not name or name in names or not name.replace("_", "").isalnum():
                raise CaseError(f"{path}: {key}: label '{name}' is empty, repeated or not a plain name")
            names.add(name)
            try:
                value, error = float(obs["value"]), float(obs["error"])
                date = str(obs["date"])
            except (KeyError, TypeError, ValueError) as err:
                raise CaseError(f"{path}: {name}: needs date, value and error ({err})") from err
            if not (math.isfinite(value) and math.isfinite(error) and error > 0):
                raise CaseError(f"{path}: {name}: value and error must be finite and the error above 0")
            blocks.append(
                f"SUMMARY_OBSERVATION {name}\n{{\n    VALUE = {value!r};\n    ERROR = {error!r};\n"
                f"    ERROR_MODE = ABS;\n    DATE = {date};\n    KEY = {key};\n}};\n"
            )
    return "\n".join(blocks), len(blocks), unused


def read_params(found: dict, common: list[int]) -> tuple[dict[int, dict], set[str]]:
    rows = {real: prior_qc.read_truth(found[real][0] / "parameters.txt") for real in common}
    names = set().union(*(set(r) for r in rows.values()))
    if any(set(r) != names for r in rows.values()):
        raise CaseError("the realizations have different parameter sets")
    logged = {n[len("LOG10_") :] for n in names if n.startswith("LOG10_")}
    keep = sorted(n for n in names if not n.startswith("LOG10_"))
    return {real: {n: rows[real][n] for n in keep} for real in common}, logged


def write_params(path: Path, rows: dict[int, dict]) -> None:
    names = sorted(next(iter(rows.values())))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["REAL", *names])
        for real in sorted(rows):
            writer.writerow([real, *(repr(rows[real][n]) for n in names)])


def outward(low: float, high: float) -> tuple[float, float]:
    step = 10.0 ** (math.floor(math.log10(high - low)) - 1)
    return math.floor(low / step) * step, math.ceil(high / step) * step


def cdf(p: dict, values: np.ndarray) -> np.ndarray:
    return np.array([prior_qc.position(p, v) for v in values], dtype=float)


def rebuild_prior(rows: dict[int, dict], logged: set[str], references: dict[str, dict]) -> list[dict]:
    names = sorted(next(iter(rows.values())))
    out = []
    for name in names:
        bare = name.split(":", 1)[-1]
        values = np.array([rows[r][name] for r in sorted(rows)], dtype=float)
        low, high = float(values.min()), float(values.max())
        if high - low <= 1e-9 * max(1.0, abs(high)):
            out.append(
                {
                    "name": bare,
                    "column": name,
                    "line": f"{bare} CONST {float(values[0])!r}",
                    "source": "constant",
                }
            )
            continue
        ref = references.get(bare)
        rejected = ""
        if ref and ref["distribution"] in ("UNIFORM", "LOGUNIF"):
            a, b = float(ref["min"]), float(ref["max"])
            if a <= low and high <= b:
                line = f"{bare} {ref['distribution']} {a!r} {b!r}"
                out.append({"name": bare, "column": name, "line": line, "source": "reference"})
                continue
            rejected = f"; reference {ref['distribution']} {a:g} {b:g} excludes a prior value"
        elif ref:
            rejected = f"; reference {ref['distribution']} is not UNIFORM or LOGUNIF"
        if bare in logged or name in logged:
            if low <= 0:
                raise CaseError(f"{name}: LOG10_ was written but a value is not positive")
            a, b = 10.0 ** math.floor(math.log10(low)), 10.0 ** math.ceil(math.log10(high))
            line = f"{bare} LOGUNIF {a:.6g} {b:.6g}"
        else:
            a, b = outward(low, high)
            line = f"{bare} UNIFORM {a:.6g} {b:.6g}"
        out.append(
            {"name": bare, "column": name, "line": line, "source": "rebuilt from the prior sample" + rejected}
        )
    for item in out:
        values = np.array([rows[r][item["column"]] for r in sorted(rows)], dtype=float)
        parsed = parse_line(item["line"])
        if parsed["distribution"] == "CONST":
            item.update(ks_p=None, n=len(values))
            continue
        u = cdf(parsed, values)
        item.update(ks_p=float(stats.kstest(u, "uniform").pvalue), n=len(values))
    return out


def parse_line(line: str) -> dict:
    parts = line.split()
    bounds = [float(x) for x in parts[2:4]] if parts[1] != "CONST" else [None, None]
    return {"name": parts[0], "distribution": parts[1], "min": bounds[0], "max": bounds[1]}


def read_references(files: list[Path]) -> dict[str, dict]:
    refs = {}
    for path in files:
        found, errors = prior_qc.parse_dist(path)
        if errors:
            raise CaseError("; ".join(f"{e['file']}:{e['line']}: {e['message']}" for e in errors))
        refs.update({p["name"]: p for p in found})
    return refs


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--prior-runpaths", required=True)
    parser.add_argument("--posterior-runpaths", required=True)
    parser.add_argument("--eclbase", required=True, help="Summary base relative to the runpath, with <IENS>")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--allow-missing", action="append", default=[], metavar="LABEL:REAL")
    parser.add_argument("--dist-reference", type=Path, action="append", default=[])
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    return parser.parse_args(argv)


def build(args) -> dict:
    allowed = parse_allow(args.allow_missing)
    expected = set(range(args.expected))
    patterns = {"prior": args.prior_runpaths, "posterior": args.posterior_runpaths}
    ens = {lab: members(patterns[lab], args.eclbase, expected, allowed[lab], lab) for lab in LABELS}
    common = sorted(set(ens["prior"]["found"]) & set(ens["posterior"]["found"]))
    if not common:
        raise CaseError("no realization is in both ensembles")
    text, count, unused = observations(args.observations)
    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir / "observations.obs").write_text(text, encoding="utf-8")
    staged = {lab: stage(args.outdir, lab, ens[lab]["found"], common, args.eclbase) for lab in LABELS}
    params = {}
    for lab in LABELS:
        rows, logged = read_params(ens[lab]["found"], common)
        write_params(args.outdir / f"{lab}_params.csv", rows)
        params[lab] = (rows, logged)
    if set(next(iter(params["prior"][0].values()))) != set(next(iter(params["posterior"][0].values()))):
        raise CaseError("prior and posterior have different parameter names")
    prior = rebuild_prior(*params["prior"], read_references(args.dist_reference))
    (args.outdir / "prior.dist").write_text("\n".join(p["line"] for p in prior) + "\n", encoding="utf-8")
    with (args.outdir / "prior_provenance.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, ["name", "column", "line", "source", "ks_p", "n"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(prior)
    report = {
        "schema": "ahm-case-adapter/1",
        "expected": args.expected,
        "common_realizations": realization_spec(common),
        "n_common": len(common),
        "left_out": {lab: {str(r): why for r, why in sorted(ens[lab]["left_out"].items())} for lab in LABELS}
        | {
            "comparison": {
                str(r): "only in " + ("prior" if r in ens["prior"]["found"] else "posterior")
                for r in sorted(set(ens["prior"]["found"]) ^ set(ens["posterior"]["found"]))
            }
        },
        "observations": count,
        "observation_sections_not_used": unused,
        "staged_runpaths": staged,
        "uncertain_parameters": sum(1 for p in prior if p["source"] != "constant"),
        "prior_sources": {
            s: sum(1 for p in prior if p["source"] == s) for s in sorted({p["source"] for p in prior})
        },
        "missing_summary_policy": "a realization without SMSPEC/UNSMRY is left out and listed; never zero",
    }
    (args.outdir / "members.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def run(args, report: dict) -> int:
    misfit = _load_sibling("misfit_report")
    overview = _load_sibling("ahm_overview")
    out = args.outdir
    code = misfit.main(
        [
            "--obs", str(out / "observations.obs"),
            "--prior", report["staged_runpaths"]["prior"],
            "--posterior", report["staged_runpaths"]["posterior"],
            "--eclbase", args.eclbase,
            "--realizations", report["common_realizations"],
            "--outdir", str(out / "misfit"),
        ]
    )  # fmt: skip
    if code != 0:
        return code
    return overview.main(
        [
            "--misfit-dir", str(out / "misfit"),
            "--dist", str(out / "prior.dist"),
            "--prior-params-csv", str(out / "prior_params.csv"),
            "--posterior-params-csv", str(out / "posterior_params.csv"),
            "--outdir", str(out / "overview"),
        ]
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.expected <= 0 or "<IENS>" not in args.eclbase:
            print("ERROR: --expected must be positive and --eclbase must contain <IENS>", file=sys.stderr)
            return 2
        parse_allow(args.allow_missing)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    try:
        report = build(args)
    except (CaseError, OSError, yaml.YAMLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps({k: report[k] for k in ("n_common", "common_realizations", "observations", "left_out")}))
    return run(args, report) if args.run else 0


if __name__ == "__main__":
    sys.exit(main())
