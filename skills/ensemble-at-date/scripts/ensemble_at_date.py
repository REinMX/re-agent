#!/usr/bin/env python3
"""Ensemble value at an as-of date: per realization + mean (+ optional P10/P50/P90).

Reads SMSPEC/UNSMRY (all ministeps, time_index=raw) via res2df/resdata. Writes only
under --outdir (default <deliverables>/at_date/<label>). Never writes in a runpath.

Value at a date (method auto):
  totals (e.g. FOPT, FGPT, FWIT, WOPT)  linear interpolation between steps; the
                                         cumulative is 0 at the SMSPEC start date
                                         (non-restart runs only)
  rates  (e.g. FOPR, FWIR, WWPR)         rate of the step that contains the date
  other  (e.g. FPR, WBHP, WWCT)          linear interpolation between steps
Other methods: interp (always interpolate), exact (date must be a step date).

Fail closed:
  - Requested vector absent in any realization  -> exit 2 + missing_vectors.json.
    Missing is never filled with zero.
  - As-of date before start or after the end of a realization -> exit 2
    (no extrapolation; a 2045 horizon needs a run that reaches 2045).
  - Missing/failed realization, or a runpath without the ERT OK marker (still running
    or failed) -> exit 1 unless --allow-partial.

As-of tokens: YYYY-MM-DD, "last" (each realization's last step), "mid" (midpoint of
common start .. earliest end), "first", or a key of contract report_dates (e.g.
end_of_prediction: "2046-01-01" = end-2045 as the 1 JAN report step). --as-of can repeat.

Percentiles (--percentiles): convention from contract p10_p90.
  numerical  : P10 = np.percentile(x, 10), P90 = np.percentile(x, 90)
  exceedance : P10 = np.percentile(x, 90) (upside), P90 = np.percentile(x, 10)

Exit: 0 ok, 1 partial ensemble failure, 2 usage/input/missing-vector/date error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))
import numpy as np
import pandas as pd
import runpath_lib

warnings.filterwarnings("ignore", category=DeprecationWarning)

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402,F401

try:
    from res2df import ResdataFiles as SimFiles
    from res2df import summary as summary_module
except ImportError:  # older Komodo
    from ecl2df import EclFiles as SimFiles  # type: ignore[no-redef]
    from ecl2df import summary as summary_module  # type: ignore[no-redef]

TOTAL_RE = re.compile(r"^[FGWCR][A-Z][PI]TH?$")  # FOPT FGPT FWIT WOPT GGIT FOPTH
RATE_RE = re.compile(r"^[FGWCR][A-Z][PI]RH?$")  # FOPR FWIR WWPR (FPR is excluded: 3 chars)
DAY = np.timedelta64(1, "D")


# ---------------------------------------------------------------- helpers
def family(key: str) -> str:
    return key.split(":", 1)[0]


def kind_of(key: str) -> str:
    fam = family(key)
    if TOTAL_RE.match(fam):
        return "total"
    if RATE_RE.match(fam):
        return "rate"
    return "state"


def p10_p90_convention(contract: dict[str, Any]) -> str:
    conv = str(contract.get("p10_p90") or contract.get("percentile_convention") or "").strip().lower()
    if conv not in {"numerical", "exceedance"}:
        raise ValueError(f"contract p10_p90 must be numerical or exceedance, got {conv!r}")
    return conv


def percentiles(values: np.ndarray, conv: str) -> dict[str, float]:
    lo, mid, hi = (float(np.percentile(values, q)) for q in (10, 50, 90))
    if conv == "numerical":
        return {"P10": lo, "P50": mid, "P90": hi}
    return {"P10": hi, "P50": mid, "P90": lo}


def iter_dirname(token: str | int) -> str:
    """--iter value -> runpath folder name: 3 -> 'iter-3'; 'pred_ref' / 'iter-3' kept as given.

    Drogon-style predictions live in realization-N/pred_<name> (restart from iter-M), not iter-*.
    """
    t = str(token).strip()
    if t.isdigit():
        return f"iter-{int(t)}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", t):
        raise argparse.ArgumentTypeError(f"bad --iter {token!r} (use 0, iter-3, pred_ref ...)")
    return t


def expand_vectors(requested: list[str], keys: list[str]) -> tuple[list[str], list[str]]:
    """Return (expanded keys, missing requests). A family expands to all its keys."""
    out: list[str] = []
    missing: list[str] = []
    for req in requested:
        if req in keys:
            out.append(req)
        elif ":" not in req and any(family(k) == req and ":" in k for k in keys):
            out.extend(sorted(k for k in keys if family(k) == req))
        else:
            missing.append(req)
    seen: set[str] = set()
    return [k for k in out if not (k in seen or seen.add(k))], missing


class Member:
    """One realization's summary: start date, step dates, vectors, units."""

    def __init__(self, real: int, data_file: Path, keys_wanted: list[str] | None):
        self.real = real
        files = SimFiles(str(data_file))
        smry = files.get_summary()
        self.keys = sorted(k for k in smry.keys() if k.upper() != "YEARS")
        self.start = np.datetime64(pd.Timestamp(smry.start_date), "s")
        self.restart = getattr(smry, "restart_case", None) is not None
        # Restart run whose history summary is NOT reachable (no chain): the frame starts at the
        # first prediction step, far after START. Cumulatives there are NOT 0 and dates before the
        # first step are unknown -> fail closed instead of interpolating from 0 or clamping.
        self.chain_missing = False
        self.units: dict[str, str] = {}
        self.frame: pd.DataFrame | None = None
        if keys_wanted is not None:
            present = [k for k in keys_wanted if k in self.keys]
            for k in present:
                try:
                    self.units[k] = str(smry.unit(k))
                except Exception:  # noqa: BLE001
                    self.units[k] = ""
            if present:
                frame = summary_module.df(files, time_index="raw", column_keys=present)
                frame.index = pd.to_datetime(frame.index)
                self.frame = frame.sort_index()
                gap = (self.frame.index[0] - pd.Timestamp(self.start)) / pd.Timedelta(days=1)
                if gap > 31:
                    self.chain_missing = True
                    self.restart = True

    @property
    def data_start(self) -> np.datetime64:
        """First date with known values: START, or the first step of a restart run without chain."""
        return self.dates[0] if self.chain_missing else self.start

    @property
    def dates(self) -> np.ndarray:
        assert self.frame is not None
        return self.frame.index.values.astype("datetime64[s]")

    @property
    def end(self) -> np.datetime64:
        return self.dates[-1]

    def tdays(self, when: np.ndarray | np.datetime64) -> np.ndarray:
        return (np.asarray(when, dtype="datetime64[s]") - self.start) / DAY

    def cumulative_fn(self, key: str) -> tuple[np.ndarray, np.ndarray]:
        """Knots (t_days, cum) for a total, or the integral of a rate (rate * dt per step)."""
        t = self.tdays(self.dates)
        v = self.frame[key].to_numpy(dtype=float)
        if kind_of(key) == "total":
            if self.restart:
                return t, v
            return np.concatenate([[0.0], t]), np.concatenate([[0.0], v])
        # rate: value at step k is the rate over (t_{k-1}, t_k]
        t_base = t[0] if self.chain_missing else 0.0  # restart without chain: integrate from first step
        dt = np.diff(np.concatenate([[t_base], t]))
        cum = np.cumsum(v * dt)
        return np.concatenate([[t_base], t]), np.concatenate([[0.0], cum])

    def value_at(self, key: str, when: np.datetime64, method: str) -> tuple[float, str]:
        t = self.tdays(self.dates)
        v = self.frame[key].to_numpy(dtype=float)
        td = float(self.tdays(when))
        if td < -1e-9 or td > t[-1] + 1e-6:
            raise DateOutOfRange(
                f"realization {self.real}: as-of {str(when)[:10]} outside "
                f"{str(self.start)[:10]} .. {str(self.end)[:10]} (no extrapolation)"
            )
        if method == "exact":
            hit = np.where(np.abs(t - td) < 0.5)[0]
            if not len(hit):
                raise DateOutOfRange(f"realization {self.real}: {str(when)[:10]} is not a report step")
            return float(v[hit[-1]]), "exact"
        if self.chain_missing and td < t[0] - 1e-6:
            raise DateOutOfRange(
                f"realization {self.real}: restart run without history summary chain; first step "
                f"{str(self.dates[0])[:10]}, as-of {str(when)[:10]} is before it (read the history case "
                "or make the restart SMSPEC/UNSMRY reachable)"
            )
        kind = kind_of(key)
        if method == "auto" and kind == "rate":
            idx = int(np.searchsorted(t, td - 1e-9, side="left"))
            return float(v[min(idx, len(v) - 1)]), "rate_step"
        if kind == "total":
            kt, kv = self.cumulative_fn(key)
            return float(np.interp(td, kt, kv)), "interp_total"
        if td < t[0]:
            raise DateOutOfRange(
                f"realization {self.real}: {key} has no value before first step {str(self.dates[0])[:10]}"
            )
        return float(np.interp(td, t, v)), "interp"


class DateOutOfRange(Exception):
    pass


def md_table(frame: pd.DataFrame) -> str:
    try:
        return frame.to_markdown(index=False, floatfmt=".4g")
    except ImportError:  # tabulate missing
        return "```\n" + frame.to_string(index=False) + "\n```"


def resolve_as_of(
    token: str, members: list[Member], report_dates: dict[str, Any] | None = None
) -> tuple[str, dict[int, np.datetime64]]:
    """Return (label, per-real datetime). A contract report_dates key (e.g. end_of_prediction) is a token."""
    if report_dates and token.strip() in report_dates:
        named = token.strip()
        token = str(report_dates[named])
        when = np.datetime64(datetime.strptime(token, "%Y-%m-%d"), "s")
        return f"{named}_{token}", {m.real: when for m in members}
    tok = token.strip().lower()
    if tok == "last":
        return "last", {m.real: m.end for m in members}
    if tok == "first":
        return "first", {m.real: m.dates[0] for m in members}
    if tok == "mid":
        start = max(m.start for m in members)
        end = min(m.end for m in members)
        mid = start + (end - start) // 2
        mid = np.datetime64(str(mid)[:10], "s")
        return f"mid_{str(mid)[:10]}", {m.real: mid for m in members}
    try:
        when = np.datetime64(datetime.strptime(token.strip(), "%Y-%m-%d"), "s")
    except ValueError as exc:
        raise SystemExit(f"error: bad --as-of {token!r} (use YYYY-MM-DD, last, mid, first)") from exc
    return token.strip(), {m.real: when for m in members}


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_contract_args(ap)
    ap.add_argument("--scratch", type=Path, default=None, help="Override paths.scratch")
    ap.add_argument(
        "--iter",
        type=iter_dirname,
        default=None,
        dest="iteration",
        help="Iteration number (0 -> iter-0) or runpath folder name (pred_ref). "
        "Default: contract runpath.default_iter, else iter-0",
    )
    ap.add_argument(
        "--eclbase",
        default=None,
        help="Deck name or eclipse/model/NAME (default: contract eclbase; required)",
    )
    ap.add_argument(
        "--vectors",
        default=None,
        help="Comma list; a family (WOPR) expands to all wells. "
        "Default: contract vectors.at_date_default, else FOPT,FGPT",
    )
    ap.add_argument(
        "--as-of",
        action="append",
        default=None,
        help="YYYY-MM-DD | last | mid | first | a contract report_dates key (end_of_prediction)",
    )
    ap.add_argument("--method", default="auto", choices=["auto", "interp", "exact"])
    ap.add_argument("--reals", default=None, help="Subset, e.g. 0-9 or 0-4,7")
    ap.add_argument("--expected", type=int, default=None, help="Expected members (default: all found)")
    ap.add_argument("--percentiles", action="store_true", help="Add P10/P50/P90 (contract convention)")
    ap.add_argument(
        "--min-n-percentiles",
        type=int,
        default=10,
        help="Below this N, P10/P50/P90 are not computed (blank + note). Default 10",
    )
    ap.add_argument("--label", default=None, help="Output subfolder name")
    ap.add_argument("--outdir", type=Path, default=None)
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument(
        "--no-ok-check",
        action="store_true",
        help="Do not require the ERT OK marker in each runpath (default: require it)",
    )
    args = ap.parse_args(argv)

    try:
        contract = contract_lib.load_contract(
            args.contract, args.profile, skip_required=("eclbase",) if args.eclbase else ()
        )
        conv = p10_p90_convention(contract)
        ecl_sub, args.eclbase = contract_lib.resolve_eclbase(contract, args.eclbase)
        scratch = Path(args.scratch) if args.scratch else contract.path("scratch")
        outroot = None if args.outdir else contract.path("deliverables")
    except (ContractError, ValueError) as exc:
        print(f"error: contract: {exc}", file=sys.stderr)
        return 2
    for note in contract.warnings:
        print(f"note: contract: {note}", file=sys.stderr)
    if args.iteration is None:
        args.iteration = iter_dirname(contract.default_iter())
    if args.vectors is None:
        args.vectors = ",".join(contract.vectors("at_date_default", ["FOPT", "FGPT"]))
    if not scratch.is_dir():
        print(f"error: scratch not found: {scratch}", file=sys.stderr)
        return 2

    try:
        expected = runpath_lib.expected_set(args.expected, args.reals)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ensemble = runpath_lib.read_ensemble(
        str(scratch / "realization-*" / args.iteration), (ecl_sub, args.eclbase), expected=expected
    )
    wanted = sorted(expected) if expected is not None else [m.realization for m in ensemble.found]
    if not wanted and not ensemble.dropped:
        print(f"error: no realization-*/{args.iteration} under {scratch}", file=sys.stderr)
        return 2

    requested = [v.strip() for v in args.vectors.split(",") if v.strip()]
    as_of_tokens = args.as_of or ["last"]
    label = args.label or "_".join(t.replace("-", "") for t in as_of_tokens)
    outdir = Path(args.outdir) if args.outdir else Path(outroot) / "at_date" / label

    # Probe keys from first readable member, expand families.
    issues: list[str] = ensemble.problems()
    members: list[Member] = []
    expanded: list[str] | None = None
    missing_by_real: dict[str, list[int]] = {}
    for member in ensemble.found:
        real, rp, df = member.realization, member.runpath, member.data_file
        assert df is not None
        if not df.with_suffix(".UNSMRY").is_file():
            issues.append(f"realization {real}: missing UNSMRY")
            continue
        if not args.no_ok_check and not (rp / "OK").is_file():
            issues.append(f"realization {real}: no OK marker in runpath (still running or failed)")
            continue
        try:
            if expanded is None:
                probe = Member(real, df, None)
                expanded, miss = expand_vectors(requested, probe.keys)
                for req in miss:
                    missing_by_real.setdefault(req, []).append(real)
            m = Member(real, df, expanded)
        except Exception as exc:  # noqa: BLE001
            issues.append(f"realization {real}: {type(exc).__name__}: {exc}")
            continue
        for k in expanded:
            if k not in m.keys:
                missing_by_real.setdefault(k, []).append(real)
        if m.frame is None or m.frame.empty:
            issues.append(f"realization {real}: empty summary")
            continue
        members.append(m)

    if missing_by_real:
        outdir.mkdir(parents=True, exist_ok=True)
        payload = {
            "missing_vectors": sorted(missing_by_real),
            "realizations_missing": {k: v[:20] for k, v in missing_by_real.items()},
            "rule": "missing SUMMARY vector is an error, never zero",
            "scratch": str(scratch),
        }
        (outdir / "missing_vectors.json").write_text(json.dumps(payload, indent=2))
        print(
            f"error: SUMMARY vector(s) absent: {', '.join(sorted(missing_by_real))} "
            f"(see {outdir / 'missing_vectors.json'}). Not filled with zero.",
            file=sys.stderr,
        )
        return 2
    if issues and not args.allow_partial:
        for line in issues:
            print(f"error: {line}", file=sys.stderr)
        print("Use --allow-partial to continue with valid members.", file=sys.stderr)
        return 1
    if not members:
        print("error: no valid realization", file=sys.stderr)
        return 1
    assert expanded is not None

    rows: list[dict[str, Any]] = []
    for token in as_of_tokens:
        tag, per_real = resolve_as_of(token, members, contract.get("report_dates") or {})
        for m in members:
            when = per_real[m.real]
            for key in expanded:
                try:
                    val, how = m.value_at(key, when, args.method)
                except DateOutOfRange as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return 2
                rows.append(
                    {
                        "AS_OF_TOKEN": tag,
                        "AS_OF": str(when)[:19],
                        "REAL": m.real,
                        "KEY": key,
                        "VALUE": val,
                        "UNIT": m.units.get(key, ""),
                        "METHOD": how,
                        "KIND": kind_of(key),
                    }
                )
    per_real = pd.DataFrame(rows)

    stats_rows = []
    pct_skipped = None
    for (tag, key), grp in per_real.groupby(["AS_OF_TOKEN", "KEY"], sort=False):
        x = grp["VALUE"].to_numpy(dtype=float)
        row: dict[str, Any] = {
            "AS_OF_TOKEN": tag,
            "AS_OF": grp["AS_OF"].iloc[0] if grp["AS_OF"].nunique() == 1 else "per-real (see per_real.csv)",
            "KEY": key,
            "UNIT": grp["UNIT"].iloc[0],
            "N": len(x),
            "MEAN": float(x.mean()),
            "STD": float(x.std(ddof=1)) if len(x) > 1 else 0.0,
            "MIN": float(x.min()),
            "MAX": float(x.max()),
        }
        if args.percentiles:
            if len(x) >= args.min_n_percentiles:
                row.update(percentiles(x, conv))
            else:
                row.update({"P10": np.nan, "P50": np.nan, "P90": np.nan})
                pct_skipped = f"N={len(x)} < {args.min_n_percentiles}: P10/P50/P90 not computed"
        stats_rows.append(row)
    stats = pd.DataFrame(stats_rows)

    outdir.mkdir(parents=True, exist_ok=True)
    stale = outdir / "missing_vectors.json"
    if stale.exists():
        stale.unlink()
    per_real.to_csv(outdir / "per_real.csv", index=False)
    per_real.to_parquet(outdir / "per_real.parquet", index=False)
    stats.to_csv(outdir / "summary.csv", index=False)
    ends = sorted({str(m.end)[:10] for m in members})
    manifest = {
        "skill": "ensemble-at-date",
        "scratch": str(scratch),
        "iter": args.iteration,
        "eclbase": f"{ecl_sub}/{args.eclbase}",
        "realizations": [m.real for m in members],
        "n": len(members),
        "partial_issues": issues,
        "vectors": expanded,
        "as_of": as_of_tokens,
        "method": args.method,
        "p10_p90": conv if args.percentiles else None,
        "percentiles_skipped": pct_skipped,
        "sim_start": str(members[0].start)[:10],
        "sim_end_dates": ends,
        "generated": datetime.now().isoformat(timespec="seconds"),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    lines = [
        f"# Ensemble at date — {label}",
        "",
        f"- Scratch: `{scratch}` {args.iteration}, deck `{ecl_sub}/{args.eclbase}`, "
        f"N = {len(members)} realizations",
        f"- Simulation period: {manifest['sim_start']} .. {', '.join(ends)}",
        f"- Method: {args.method} (totals interpolated; rates = step containing the date)",
        f"- P10/P90 convention: **{conv}**"
        + ("" if args.percentiles else " (percentiles not requested)")
        + (f" — **{pct_skipped}**" if pct_skipped else ""),
        "",
        md_table(stats),
        "",
        "Missing SUMMARY vectors are an error (exit 2). Nothing is zero-filled.",
    ]
    (outdir / "README.md").write_text("\n".join(lines) + "\n")
    with pd.option_context("display.width", 200, "display.float_format", "{:.6g}".format):
        print(stats.to_string(index=False))
    if pct_skipped:
        print(f"note: {pct_skipped}")
    print(f"wrote {outdir}  (N={len(members)}, p10_p90={conv})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
