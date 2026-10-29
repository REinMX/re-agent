#!/usr/bin/env python3
"""Compensation check after an AHM: is a low misfit bought with implausible parameters? Read-only.

A history match can fit the data for the wrong reasons. Missing physics (an aquifer, a fault segment, kv/kh)
is then absorbed by the parameters that are there: they move to the edge of their prior and pull each other.
On drogon-lite the setup without the new physics had the lowest misfit (1.74) with porosity, relperm and a
fault segment pushed to their prior bounds. This check reads the parameters only, so it works on real fields.

Per parameter, in prior position units (0 at min, 1 at max; log scale for LOGUNIF), posterior against prior:
    EDGE   the posterior mean is within --edge (0.1) of a bound, or at least --pile (0.3) of the members are
           within --edge / 2 of a bound. Strong sign of compensation, or of a prior that is too narrow.
    BULK   the posterior mean moved more than --shift (1.2) prior sd from the prior mean, without EDGE. Soft:
           a well-informed parameter can do this.
    TRADEOFF  a pair with |posterior correlation| >= --corr (0.5) and at least 0.3 above the prior one.
           COMPENSATING-PAIR when both members of the pair are EDGE or BULK.
Plausibility score = (EDGE count + 0.5 BULK count) / parameters. Verdict per run:
    compensating   score >= --max-score (0.2) or 2+ EDGE, and the fit is good
    implausible    the same parameter evidence, and the fit is not good or unknown
    suspect        one EDGE, or score >= --max-score / 2
    plausible      otherwise
The fit is good when --misfit <= --misfit-limit (2.0, mean squared normalized residual), or when the
misfit_report manifest (--misfit-manifest) says the posterior is inside or below its noise band, or has a
mean misfit <= --misfit-limit. The fit is unknown when that manifest reports issues, has missing members
(--allow-partial), or used other members than the run.
Give one --prior/--posterior pair per seed (repeat the options) and one --seed per run. A claim needs
ahm_defaults.MIN_SEEDS distinct declared seeds, an authoritative member inventory (--expected N or
--realizations 0-4,7) and the same member set in every run; otherwise the report is diagnostic only. Prior and
posterior must have the same members, and every declared bounded parameter. A duplicate seed, or with declared
seeds a prior or posterior reused in another run (same values, any path, label or row order), is refused;
without seeds a reused component is a note. The fingerprints detect replay, not statistical independence.
CSV columns that are not all finite numbers (CASE, DATE) are ignored only when they do not match a declared
parameter. A matching invalid column refuses even when another alias supplies valid values.
With --truth (synthetic cases only) the report adds whether each parameter with a truth value misses it, and
lists the bounded parameters without one (truth_missing); the verdict never uses the truth.

    compensation_check.py <GEN_KW .dist files> --prior-runpaths '<glob iter-0>' --posterior-runpaths '<glob iter-N>' \
        --seed <seed> [--expected N | --realizations 0-4,7] [--misfit-manifest <misfit_report outdir>/manifest.json] \
        [--json] [--fail-on compensating]
Exit code: 0 = done, 1 = input problem or --fail-on reached, 2 = usage error.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERDICTS = ("plausible", "suspect", "implausible", "compensating")
sys.path.insert(0, str(HERE.parents[1] / "stage-contract/scripts"))
import runpath_lib  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


prior_qc = _load("prior_qc")
ahm_defaults = _load("ahm_defaults")


@dataclass(frozen=True)
class Limits:
    edge: float = 0.1
    pile: float = 0.3
    shift: float = 1.2
    corr: float = 0.5
    corr_gain: float = 0.3
    max_score: float = 0.2
    misfit_limit: float = 2.0


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class ParameterEnsemble:
    members: tuple[int, ...]
    values: dict[str, tuple[float, ...]]
    ignored: tuple[str, ...] = ()

    def __post_init__(self):
        if len(self.members) < 2:
            raise ValueError("need at least 2 members")
        if any(type(r) is not int or r < 0 for r in self.members):
            raise ValueError("member identities must be nonnegative integers")
        if len(set(self.members)) != len(self.members):
            raise ValueError("duplicate member identity")
        if not self.values:
            raise ValueError("no parameter values" + _ignored_note(self.ignored))
        for name, values in self.values.items():
            if len(values) != len(self.members):
                raise ValueError(f"{name}: parameter member count differs")
            if any(not math.isfinite(v) for v in values):
                raise ValueError(f"{name}: parameter values must be finite")


def _ignored_note(ignored: tuple[str, ...]) -> str:
    return f"; columns without finite numeric values were ignored: {list(ignored)}" if ignored else ""


def _match(
    values: dict, params: dict[str, dict], required: set[str], context: str, ignored: tuple[str, ...] = ()
) -> dict:
    matched = {}
    invalid = []
    for name in (*values, *ignored):
        candidates = (
            [name] if name in params else [n for n in params if prior_qc.bare(n) == prior_qc.bare(name)]
        )
        if len(candidates) > 1:
            raise ValueError(f"{context}: ambiguous parameter {name}")
        if not candidates:
            continue
        if name in ignored:
            invalid.append(name)
            continue
        key = candidates[0]
        if key in matched:
            raise ValueError(f"{context}: duplicate match for parameter {key}")
        matched[key] = values[name]
    missing = required - matched.keys()
    if missing:
        raise ValueError(
            f"{context}: missing declared bounded parameters {sorted(missing)}{_ignored_note(ignored)}"
        )
    if invalid:
        raise ValueError(f"{context}: declared parameter column {invalid[0]} lacks finite numeric values")
    return matched


def _fingerprint(values: dict[str, tuple[float, ...]]) -> str:
    """Detect replay, not statistical independence. Ignore row order and member relabelling."""
    names = sorted(values)
    rows = sorted(zip(*(values[n] for n in names), strict=True))
    canonical = [names, [[float(v if v else 0).hex() for v in row] for row in rows]]
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


@dataclass
class ParamResult:
    name: str
    prior_mean_pos: float
    post_mean_pos: float
    shift_prior_sd: float
    edge_share: float
    flag: str | None
    truth_pos: float | None = None
    truth_inside_p5_p95: bool | None = None


@dataclass
class RunResult:
    label: str
    members: int
    parameters: list[ParamResult]
    tradeoffs: list[dict]
    score: float
    edges: int
    bulk: int
    fit_good: bool | None
    fit_basis: str
    verdict: str
    findings: list[dict] = field(default_factory=list)
    seed: int | None = None
    member_ids: tuple[int, ...] = ()
    prior_fingerprint: str = ""
    posterior_fingerprint: str = ""
    inventory_checked: bool = False
    truth_missing: list[str] = field(default_factory=list)


def _corr(a: list[float], b: list[float]) -> float:
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    if sa == 0 or sb == 0:
        return 0.0
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (len(a) * sa * sb)


def positions(param: dict, values: list[float]) -> list[float]:
    out = [prior_qc.position(param, v) for v in values]
    if any(p is None or not math.isfinite(p) for p in out):
        raise ValueError(f"{param['name']}: a value has no position in the prior range")
    return out


def bounded_parameters(params: dict[str, dict]) -> set[str]:
    """Declared parameters with distinct finite bounds that are not CONST. Each run must supply all of them."""
    return {
        n
        for n, p in params.items()
        if p["min"] is not None
        and p["max"] is not None
        and p["distribution"] != "CONST"
        and p["max"] > p["min"]
    }


def check_run(
    label: str,
    params: dict[str, dict],
    prior: ParameterEnsemble,
    post: ParameterEnsemble,
    limits: Limits = DEFAULT_LIMITS,
    fit_good: bool | None = None,
    fit_basis: str = "unknown",
    truth: dict[str, float] | None = None,
    seed: int | None = None,
    expected: set[int] | None = None,
) -> RunResult:
    if not isinstance(prior, ParameterEnsemble) or not isinstance(post, ParameterEnsemble):
        raise ValueError("prior and posterior must be ParameterEnsemble values with member identities")
    if set(prior.members) != set(post.members):
        raise ValueError(f"{label}: prior and posterior member sets differ")
    if expected is not None and (set(prior.members) != expected or set(post.members) != expected):
        raise ValueError(f"{label}: member sets differ from expected {sorted(expected)}")
    if seed is not None and type(seed) is not int:
        raise ValueError("seed identity must be an integer")
    required = bounded_parameters(params)
    post_m = _match(post.values, params, required, f"{label} posterior", post.ignored)
    prior_m = _match(prior.values, params, required, f"{label} prior", prior.ignored)
    names = [n for n in post_m if n in required]
    if not names:
        raise ValueError(f"{label}: no posterior parameter matches a bounded prior with a prior ensemble")
    truth_m = _match(truth, params, set(), "truth") if truth is not None else {}
    if any(not math.isfinite(v) for v in truth_m.values()):
        raise ValueError("truth values must be finite")
    truth_missing = sorted(n for n in names if n not in truth_m) if truth is not None else []
    pos = {n: positions(params[n], post_m[n]) for n in names}
    pos0 = {n: positions(params[n], prior_m[n]) for n in names}
    results = []
    for n in names:
        p, q = pos[n], pos0[n]
        sd0 = statistics.pstdev(q) or 1e-12
        mean, mean0 = statistics.fmean(p), statistics.fmean(q)
        edge_share = sum(1 for x in p if x < limits.edge / 2 or x > 1 - limits.edge / 2) / len(p)
        shift = (mean - mean0) / sd0
        flag = None
        if mean < limits.edge or mean > 1 - limits.edge or edge_share >= limits.pile:
            flag = "EDGE"
        elif abs(shift) > limits.shift:
            flag = "BULK"
        item = ParamResult(n, mean0, mean, shift, edge_share, flag)
        if n in truth_m:
            t = truth_m[n]
            item.truth_pos = positions(params[n], [t])[0]
            ordered = sorted(post_m[n])
            lo, hi = prior_qc.quantile(ordered, 0.05), prior_qc.quantile(ordered, 0.95)
            item.truth_inside_p5_p95 = lo <= t <= hi
        results.append(item)
    flagged = {r.name for r in results if r.flag}
    tradeoffs = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            r1, r0 = _corr(pos[a], pos[b]), _corr(pos0[a], pos0[b])
            if abs(r1) >= limits.corr and abs(r1) - abs(r0) >= limits.corr_gain:
                both = a in flagged and b in flagged
                tradeoffs.append(
                    {"pair": [a, b], "posterior_r": r1, "prior_r": r0, "compensating_pair": both}
                )
    edges = sum(r.flag == "EDGE" for r in results)
    bulk = sum(r.flag == "BULK" for r in results)
    score = (edges + 0.5 * bulk) / len(results)
    strong = score >= limits.max_score or edges >= 2
    if strong:
        verdict = "compensating" if fit_good else "implausible"
    elif edges == 1 or score >= limits.max_score / 2:
        verdict = "suspect"
    else:
        verdict = "plausible"
    found = []
    for r in results:
        if r.flag:
            side = "upper" if r.post_mean_pos > 0.5 else "lower"
            found.append(
                {
                    "code": f"HMQC-COMP-{r.flag}",
                    "name": r.name,
                    "detail": f"posterior mean at position {r.post_mean_pos:.2f} ({side} side), {r.shift_prior_sd:+.1f}"
                    f" prior sd from the prior mean, {r.edge_share:.0%} of members at a bound",
                }
            )
    for t in tradeoffs:
        found.append(
            {
                "code": "HMQC-COMP-PAIR" if t["compensating_pair"] else "HMQC-COMP-TRADEOFF",
                "name": " ~ ".join(t["pair"]),
                "detail": f"posterior r {t['posterior_r']:+.2f} (prior {t['prior_r']:+.2f})",
            }
        )
    return RunResult(
        label,
        len(next(iter(pos.values()))),
        results,
        tradeoffs,
        score,
        edges,
        bulk,
        fit_good,
        fit_basis,
        verdict,
        found,
        seed,
        tuple(sorted(post.members)),
        _fingerprint({n: prior_m[n] for n in names}),
        _fingerprint({n: post_m[n] for n in names}),
        inventory_checked=expected is not None,
        truth_missing=truth_missing,
    )


def fit_from_manifest(path: Path, limit: float, members: set[int] | None = None) -> tuple[bool | None, str]:
    """Unknown (None) unless the manifest has no issue, complete ensembles and, when known, the run members."""
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return None, "misfit_report manifest is not an object"
    issues, ensembles = data.get("issues"), data.get("ensembles")
    if issues != []:
        return None, f"misfit_report reports issues {issues}" if issues else "misfit_report has no issue list"
    if not isinstance(ensembles, dict) or not ensembles:
        return None, "misfit_report has no ensemble member inventory"
    for label, item in ensembles.items():
        used = item.get("realizations_used") if isinstance(item, dict) else None
        missing = item.get("missing") if isinstance(item, dict) else None
        if not isinstance(used, list) or missing != []:
            return None, f"misfit_report {label} is partial: used {used}, missing {missing}"
        if members is not None and used != sorted(members):
            return None, f"misfit_report {label} used {used}, not the run members {sorted(members)}"
    chi2 = (data.get("misfit_vs_chi2") or {}).get("posterior") or {}
    verdict, mean = chi2.get("verdict"), chi2.get("mean_misfit")
    if verdict in ("inside the noise band", "below the noise band"):
        return True, f"misfit_report: {verdict}"
    if isinstance(mean, int | float) and math.isfinite(mean):
        return mean <= limit, f"misfit_report mean misfit {mean:.3g} against limit {limit:g}"
    return None, "misfit_report has no posterior misfit"


@dataclass
class Report:
    runs: list[RunResult]
    seeds: int
    claim_ready: bool
    seed_note: str
    consistent_flags: list[str]
    verdict: str


def summarize(runs: list[RunResult]) -> Report:
    if not runs:
        raise ValueError("no runs")
    identities = [r.seed for r in runs if r.seed is not None]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate declared seed identity")
    notes = []
    for component in ("prior", "posterior"):
        fingerprints = [getattr(r, f"{component}_fingerprint") for r in runs]
        if any(not f for f in fingerprints):
            raise ValueError(f"missing {component} evidence fingerprint")
        if len(set(fingerprints)) != len(fingerprints):
            if identities:
                raise ValueError(
                    f"repeated {component} parameter evidence; seed labels do not establish independence"
                )
            notes.append(f"repeated {component} parameter evidence across runs")
    if not all(r.inventory_checked for r in runs):
        notes.append("no authoritative member inventory (--expected or --realizations) for every run")
    if len({r.member_ids for r in runs}) != 1:
        notes.append("member sets differ across runs")
    seeds = ahm_defaults.seed_verdict(identities)
    counts: dict[str, int] = {}
    for run in runs:
        for p in run.parameters:
            if p.flag == "EDGE":
                counts[p.name] = counts.get(p.name, 0) + 1
    consistent = sorted(n for n, c in counts.items() if c * 2 > len(runs))
    worst = max((r.verdict for r in runs), key=VERDICTS.index)
    known = len(identities) == len(runs)
    note = seeds.reason if known else "Unknown seed identities. Diagnostic only, not a claim"
    note += "".join(f". Not a claim: {n}" for n in notes)
    note += ". Fingerprints detect replay, not statistical independence"
    return Report(runs, seeds.seeds, known and seeds.claim_ready and not notes, note, consistent, worst)


def _finite_floats(cells: list[str]) -> tuple[float, ...] | None:
    try:
        values = tuple(float(c) for c in cells)
    except ValueError:
        return None
    return values if all(math.isfinite(v) for v in values) else None


def read_ensemble(runpaths: str | None, csv_path: Path | None) -> ParameterEnsemble:
    if csv_path is not None:
        with csv_path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            header = next(reader, [])
            if not header or len(set(header)) != len(header):
                raise ValueError(f"{csv_path}: missing or duplicate column names")
            real_columns = [n for n in header if n.upper() in {"REAL", "REALIZATION", "IENS"}]
            if len(real_columns) != 1:
                raise ValueError(f"{csv_path}: need one REAL member identity column")
            real_index = header.index(real_columns[0])
            candidates = {
                n: i
                for i, n in enumerate(header)
                if i != real_index
                and n.upper() not in {"ITER", "ENSEMBLE", "SEED", "SENSNAME", "SENSCASE"}
                and not n.startswith("LOG10_")
            }
            members: list[int] = []
            cells: dict[str, list[str]] = {n: [] for n in candidates}
            for row in reader:
                if len(row) != len(header):
                    raise ValueError(f"{csv_path}: row length differs from header")
                token = row[real_index]
                if not token.isascii() or not token.isdigit():
                    raise ValueError(f"{csv_path}: member identity must be a nonnegative integer")
                members.append(int(token))
                for name, index in candidates.items():
                    cells[name].append(row[index])
        parsed = {n: _finite_floats(c) for n, c in cells.items()}
        ignored = tuple(n for n, v in parsed.items() if v is None)
        columns = {n: v for n, v in parsed.items() if v is not None}
        return ParameterEnsemble(tuple(members), columns, ignored)
    ensemble = runpath_lib.read_ensemble(runpaths, need_data_file=False)
    ensemble.require_complete()
    members, columns = [], {}
    names = None
    for member in ensemble.found:
        values = {
            n: v
            for n, v in prior_qc.read_truth(member.runpath / "parameters.txt").items()
            if not n.startswith("LOG10_")
        }
        if names is not None and set(values) != names:
            raise ValueError("the runpaths have different parameter sets")
        names = set(values)
        members.append(member.realization)
        for name, value in values.items():
            columns.setdefault(name, []).append(value)
    return ParameterEnsemble(tuple(members), {n: tuple(v) for n, v in columns.items()})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dist", nargs="+", type=Path, help="GEN_KW distribution files")
    ap.add_argument(
        "--prior-runpaths", action="append", default=[], help="glob of prior runpaths (one per seed)"
    )
    ap.add_argument("--posterior-runpaths", action="append", default=[], help="glob of posterior runpaths")
    ap.add_argument("--prior-csv", action="append", default=[], type=Path)
    ap.add_argument("--posterior-csv", action="append", default=[], type=Path)
    ap.add_argument("--label", action="append", default=[], help="name of each run (default run-1, ...)")
    ap.add_argument(
        "--seed", action="append", type=int, default=[], help="declared seed of each run; needed for a claim"
    )
    inventory = ap.add_mutually_exclusive_group()
    inventory.add_argument("--expected", type=int, help="expected members, numbered 0 to N-1")
    inventory.add_argument("--realizations", help="expected members as an ERT spec, for example 0-4,7")
    ap.add_argument("--misfit", action="append", type=float, default=[], help="posterior mean misfit per run")
    ap.add_argument(
        "--misfit-manifest", action="append", type=Path, default=[], help="misfit_report manifest"
    )
    ap.add_argument(
        "--truth", type=Path, help="NAME value file (synthetic cases only; not used in the verdict)"
    )
    ap.add_argument("--fail-on", choices=VERDICTS[1:], help="exit 1 when a run is at this verdict or worse")
    ap.add_argument("--json", action="store_true")
    for key, value in asdict(Limits()).items():
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=value)
    args = ap.parse_args(argv)
    priors = args.prior_csv or args.prior_runpaths
    posts = args.posterior_csv or args.posterior_runpaths
    if not posts or len(priors) != len(posts):
        print("ERROR: give one prior and one posterior ensemble per run", file=sys.stderr)
        return 2
    n = len(posts)
    for option in ("label", "seed", "misfit", "misfit_manifest"):
        if getattr(args, option) and len(getattr(args, option)) != n:
            print(f"ERROR: --{option.replace('_', '-')} must be given once per run", file=sys.stderr)
            return 2
    try:
        expected = runpath_lib.expected_set(args.expected, args.realizations)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    limits = Limits(**{k: getattr(args, k) for k in asdict(Limits())})
    try:
        params: dict[str, dict] = {}
        for path in args.dist:
            found, errors = prior_qc.parse_dist(path)
            if errors:
                raise ValueError("; ".join(e["message"] for e in errors))
            params.update({p["name"]: p for p in found})
        truth = prior_qc.read_truth(args.truth) if args.truth else None
        runs = []
        for i in range(n):
            prior = read_ensemble(
                None if args.prior_csv else priors[i], args.prior_csv[i] if args.prior_csv else None
            )
            post = read_ensemble(
                None if args.posterior_csv else posts[i],
                args.posterior_csv[i] if args.posterior_csv else None,
            )
            if args.misfit:
                fit, basis = args.misfit[i] <= limits.misfit_limit, f"--misfit {args.misfit[i]:g}"
            elif args.misfit_manifest:
                fit, basis = fit_from_manifest(
                    args.misfit_manifest[i], limits.misfit_limit, set(post.members)
                )
            else:
                fit, basis = None, "no misfit given"
            label = args.label[i] if args.label else f"run-{i + 1}"
            seed = args.seed[i] if args.seed else None
            runs.append(check_run(label, params, prior, post, limits, fit, basis, truth, seed, expected))
        report = summarize(runs)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(report), indent=1))
    else:
        for run in runs:
            print(
                f"COMPENSATION [{run.label}] {run.verdict}: score {run.score:.2f} ({run.edges} EDGE, {run.bulk} BULK"
                f" of {len(run.parameters)}), fit good {run.fit_good} ({run.fit_basis})"
            )
            for item in run.findings:
                print(f"  {item['code']} {item['name']}: {item['detail']}")
            if run.truth_missing:
                print(f"  TRUTH no truth for {', '.join(run.truth_missing)}; not graded against the truth")
        print(
            f"OVERALL {report.verdict}. EDGE in most runs: {', '.join(report.consistent_flags) or 'none'}. {report.seed_note}"
        )
    if args.fail_on and VERDICTS.index(report.verdict) >= VERDICTS.index(args.fail_on):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
