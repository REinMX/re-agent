#!/usr/bin/env python3
"""Yearly production/injection profiles from an FMU ensemble: field and (optional) by well.

For each realization and calendar year Y (period Jan 1 Y .. Jan 1 Y+1, clipped to the
simulation start/end; a clipped last year is PARTIAL=True):
  totals  (FOPT, FGPT, FWPT, FWIT, WOPT ...)  CUM_END = cumulative at period end
                                              VOL     = CUM_END - cumulative at period start
                                              AVG_RATE = VOL / days in period
  rates   (FGIR, WOPR ...) when requested     VOL from step-wise integration of the rate
                                              (rate at step k holds over (t_k-1, t_k])
  states  (FPR, WBHP, WWCT)                   END_VALUE = value at period end (interpolated)

Gas-injection probe (field): FGIR, FGIT, FGIRL, FGIRT, FGIRH, FGITH are checked and the
found/absent list is written to manifest.json. Only vectors present in SMSPEC are used.
Nothing is invented (there is no "FGIRLT" in standard SUMMARY; ask for the exact keyword).

By well (--by-well): uses well totals (WOPT, WWPT, WGPT, WWIT, WGIT) when SMSPEC has them.
If no well totals exist the script exits 2 — unless --derive-from-rates is given, then
well volumes are integrated from WOPR/WWPR/WGPR/WWIR/WGIR and labelled SOURCE=derived_from_rate.
The derivation is QC'd against field totals (FOPR->FOPT etc.); exit 2 if the relative
error exceeds --derive-tol.

Ensemble stats per (KEY, YEAR, METRIC): N, MEAN, P10, P50, P90 (contract p10_p90).
Note: per-year percentiles are per-year statistics, not one coherent profile.

Outputs under --outdir (default <deliverables>/yearly_profiles/<label>):
  field_yearly_per_real.{csv,parquet}  field_yearly_stats.csv  field_yearly_mean_wide.csv
  well_yearly_per_real.{csv,parquet}   well_yearly_stats.csv   well_yearly_mean_wide.csv
  qc_derivation.json (only with --derive-from-rates)  manifest.json  README.md

Exit: 0 ok, 1 partial ensemble failure, 2 input error / missing vector / failed QC.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))
import contract_lib
import numpy as np
import pandas as pd
import runpath_lib

_AT_DATE = Path(__file__).resolve().parents[2] / "ensemble-at-date" / "scripts"
sys.path.insert(0, str(_AT_DATE))
from ensemble_at_date import (  # noqa: E402
    ContractError,
    Member,
    add_contract_args,
    expand_vectors,
    family,
    iter_dirname,
    kind_of,
    md_table,
    p10_p90_convention,
    percentiles,
)

GAS_INJ_PROBE = ["FGIR", "FGIT", "FGIRL", "FGIRT", "FGIRH", "FGITH"]
WELL_TOTALS = ["WOPT", "WWPT", "WGPT", "WWIT", "WGIT"]
WELL_RATES = ["WOPR", "WWPR", "WGPR", "WWIR", "WGIR"]
FIELD_QC_PAIRS = [("FOPR", "FOPT"), ("FWPR", "FWPT"), ("FGPR", "FGPT"), ("FWIR", "FWIT"), ("FGIR", "FGIT")]


def year_periods(
    start: np.datetime64, end: np.datetime64
) -> list[tuple[int, np.datetime64, np.datetime64, bool]]:
    """(YEAR, period_start, period_end, partial) for each calendar year touched by the run."""
    y0 = int(str(start)[:4])
    y1 = int(str(end)[:4])
    out = []
    for y in range(y0, y1 + 1):
        a = max(np.datetime64(f"{y}-01-01T00:00:00", "s"), start)
        b_full = np.datetime64(f"{y + 1}-01-01T00:00:00", "s")
        b = min(b_full, end)
        if b <= a:
            continue
        partial = bool(b < b_full or a > np.datetime64(f"{y}-01-01T00:00:00", "s"))
        out.append((y, a, b, partial))
    return out


def unit_volume(rate_unit: str) -> str:
    u = rate_unit.strip()
    return u[: -len("/DAY")] if u.upper().endswith("/DAY") else (u + "*DAY" if u else "")


def yearly_rows(m: Member, keys: list[str], source: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    periods = year_periods(m.data_start, m.end)  # restart run without chain: from first step
    t_last = float(m.tdays(m.end))
    for key in keys:
        kind = kind_of(key)
        unit = m.units.get(key, "")
        if kind in ("total", "rate"):
            kt, kv = m.cumulative_fn(key)
            vol_unit = unit if kind == "total" else unit_volume(unit)
            for y, a, b, partial in periods:
                ta, tb = float(m.tdays(a)), float(m.tdays(b))
                ca, cb = float(np.interp(ta, kt, kv)), float(np.interp(tb, kt, kv))
                days = tb - ta
                rows.append(
                    {
                        "REAL": m.real,
                        "KEY": key,
                        "YEAR": y,
                        "PERIOD_START": str(a)[:10],
                        "PERIOD_END": str(b)[:10],
                        "DAYS": round(days, 4),
                        "PARTIAL": partial,
                        "CUM_END": cb,
                        "VOL": cb - ca,
                        "AVG_RATE": (cb - ca) / days if days > 0 else np.nan,
                        "END_VALUE": np.nan,
                        "UNIT": vol_unit,
                        "SOURCE": source.get(
                            key, "summary_total" if kind == "total" else "derived_from_rate"
                        ),
                    }
                )
        else:
            t = m.tdays(m.dates)
            v = m.frame[key].to_numpy(dtype=float)
            for y, a, b, partial in periods:
                tb = min(float(m.tdays(b)), t_last)
                rows.append(
                    {
                        "REAL": m.real,
                        "KEY": key,
                        "YEAR": y,
                        "PERIOD_START": str(a)[:10],
                        "PERIOD_END": str(b)[:10],
                        "DAYS": round(float(m.tdays(b) - m.tdays(a)), 4),
                        "PARTIAL": partial,
                        "CUM_END": np.nan,
                        "VOL": np.nan,
                        "AVG_RATE": np.nan,
                        "END_VALUE": float(np.interp(tb, t, v)),
                        "UNIT": unit,
                        "SOURCE": "summary_state",
                    }
                )
    return rows


def stats_table(per_real: pd.DataFrame, conv: str, min_n: int = 10) -> pd.DataFrame:
    out = []
    for (key, year), grp in per_real.groupby(["KEY", "YEAR"], sort=True):
        for metric in ("CUM_END", "VOL", "AVG_RATE", "END_VALUE"):
            x = grp[metric].to_numpy(dtype=float)
            if np.all(np.isnan(x)):
                continue
            row = {
                "KEY": key,
                "YEAR": year,
                "PERIOD_END": grp["PERIOD_END"].iloc[0],
                "PARTIAL": bool(grp["PARTIAL"].iloc[0]),
                "METRIC": metric,
                "UNIT": grp["UNIT"].iloc[0],
                "SOURCE": grp["SOURCE"].iloc[0],
                "N": int(np.sum(~np.isnan(x))),
                "MEAN": float(np.nanmean(x)),
            }
            xs = x[~np.isnan(x)]
            row.update(
                percentiles(xs, conv) if len(xs) >= min_n else {"P10": np.nan, "P50": np.nan, "P90": np.nan}
            )
            out.append(row)
    return pd.DataFrame(out)


def mean_wide(stats: pd.DataFrame) -> pd.DataFrame:
    """YEAR rows, columns like FOPT_VOL, FOPT_CUM_END, WBHP:PROD_END_VALUE (ensemble mean)."""
    if stats.empty:
        return stats
    s = stats.copy()
    s["COL"] = s["KEY"] + "_" + s["METRIC"]
    wide = s.pivot_table(
        index=["YEAR", "PERIOD_END", "PARTIAL"], columns="COL", values="MEAN", aggfunc="first"
    )
    return wide.reset_index()


def derivation_qc(members: list[Member], tol: float) -> dict[str, Any]:
    """Integrate field rates and compare with field totals at the end of each realization."""
    checks = []
    for rate, total in FIELD_QC_PAIRS:
        if not all(rate in m.keys and total in m.keys for m in members):
            continue
        errs = []
        for m in members:
            kt, kv = m.cumulative_fn(rate)
            integ = float(kv[-1])
            ref = float(m.frame[total].iloc[-1]) if total in m.frame else None
            if ref is None:
                continue
            denom = abs(ref) if abs(ref) > 0 else 1.0
            errs.append(abs(integ - ref) / denom if abs(ref) > 0 else abs(integ - ref))
        if errs:
            checks.append({"rate": rate, "total": total, "max_rel_err": max(errs), "n": len(errs)})
    status = "pass" if checks and all(c["max_rel_err"] <= tol for c in checks) else "fail"
    if not checks:
        status = "fail"
    return {
        "tolerance_rel": tol,
        "status": status,
        "checks": checks,
        "note": "Well volumes from rates are derived, not SUMMARY totals. Add WOPT/WWPT/WWIT to SUMMARY.",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_contract_args(ap)
    ap.add_argument("--scratch", type=Path, default=None)
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
        help="Field vectors (all must exist). Default: contract vectors.yearly_field_default, "
        "else FOPT,FGPT,FWPT,FWIT",
    )
    ap.add_argument("--no-gas-inj-probe", action="store_true", help="Skip FGIR/FGIT/... probe")
    ap.add_argument("--by-well", action="store_true")
    ap.add_argument("--wells", default=None, help="Comma list (default: all wells in SMSPEC)")
    ap.add_argument(
        "--well-vectors",
        default=None,
        help="Well families, e.g. WOPT,WWPT,WBHP (default: totals present, or rates with --derive-from-rates)",
    )
    ap.add_argument(
        "--derive-from-rates",
        action="store_true",
        help="Integrate well rates when well totals are absent (labelled derived)",
    )
    ap.add_argument(
        "--derive-tol", type=float, default=0.005, help="Max rel. error field rate-integral vs total"
    )
    ap.add_argument("--reals", default=None)
    ap.add_argument("--expected", type=int, default=None)
    ap.add_argument("--label", default="field")
    ap.add_argument("--outdir", type=Path, default=None)
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument(
        "--no-ok-check",
        action="store_true",
        help="Do not require the ERT OK marker in each runpath (default: require it)",
    )
    ap.add_argument(
        "--min-n-percentiles",
        type=int,
        default=10,
        help="Below this N, P10/P50/P90 are blank (not computed). Default 10",
    )
    args = ap.parse_args(argv)

    try:
        contract = contract_lib.load_contract(
            args.contract, args.profile, skip_required=("eclbase",) if args.eclbase else ()
        )
        conv = p10_p90_convention(contract)
        ecl_sub, args.eclbase = contract_lib.resolve_eclbase(contract, args.eclbase)
        scratch = Path(args.scratch) if args.scratch else contract.path("scratch")
        outdir = (
            Path(args.outdir)
            if args.outdir
            else contract.path("deliverables") / "yearly_profiles" / args.label
        )
    except (ContractError, ValueError) as exc:
        print(f"error: contract: {exc}", file=sys.stderr)
        return 2
    for note in contract.warnings:
        print(f"note: contract: {note}", file=sys.stderr)
    if args.iteration is None:
        args.iteration = iter_dirname(contract.default_iter())
    if args.vectors is None:
        args.vectors = ",".join(contract.vectors("yearly_field_default", ["FOPT", "FGPT", "FWPT", "FWIT"]))
    gas_inj = contract.get_dotted("vectors.gas_injection")
    gas_probe = ([gas_inj] if isinstance(gas_inj, str) else list(gas_inj)) if gas_inj else GAS_INJ_PROBE
    if not scratch.is_dir():
        print(f"error: scratch not found: {scratch}", file=sys.stderr)
        return 2

    try:
        subset = sorted(runpath_lib.parse_realizations(args.reals)) if args.reals is not None else None
        expected = runpath_lib.expected_set(args.expected, args.reals)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ensemble = runpath_lib.read_ensemble(
        str(scratch / "realization-*" / args.iteration), (ecl_sub, args.eclbase), expected=expected
    )
    found = {m.realization: m.runpath for m in ensemble.found}
    if ensemble.dropped and not args.allow_partial:
        for line in ensemble.problems():
            print(f"error: {line}", file=sys.stderr)
        return 1
    wanted = (
        subset if subset is not None else (list(range(args.expected)) if args.expected else sorted(found))
    )
    if not wanted:
        print(f"error: no realizations under {scratch}", file=sys.stderr)
        return 2

    # Probe one member for the key list.
    probe = None
    for real in wanted:
        rp = found.get(real)
        df = runpath_lib.data_file(rp, (ecl_sub, args.eclbase), real)[0] if rp else None
        if df is not None and df.with_suffix(".UNSMRY").is_file():
            probe = Member(real, df, None)
            break
    if probe is None:
        print(f"error: no readable {ecl_sub}/{args.eclbase}.DATA/UNSMRY under {scratch}", file=sys.stderr)
        return 2
    keys = probe.keys

    field_req = [v.strip() for v in args.vectors.split(",") if v.strip()]
    field_keys, field_missing = expand_vectors(field_req, keys)
    gas_found = [] if args.no_gas_inj_probe else [k for k in gas_probe if k in keys]
    gas_absent = [] if args.no_gas_inj_probe else [k for k in gas_probe if k not in keys]
    field_keys += [k for k in gas_found if k not in field_keys]

    missing: list[str] = list(field_missing)
    if gas_inj and not args.no_gas_inj_probe:
        # A configured gas-injection keyword is required, not probed. Missing is never zero.
        missing += [k for k in gas_probe if k not in keys]
    well_keys: list[str] = []
    source: dict[str, str] = {}
    wells_all = sorted({k.split(":", 1)[1] for k in keys if k.startswith("W") and ":" in k})
    wells: list[str] = []
    if args.by_well:
        wells = [w.strip() for w in args.wells.split(",")] if args.wells else wells_all
        unknown = [w for w in wells if w not in wells_all]
        if unknown:
            missing += [f"well:{w}" for w in unknown]
        if args.well_vectors:
            fams = [v.strip() for v in args.well_vectors.split(",") if v.strip()]
            rate_fams = [f for f in fams if kind_of(f) == "rate"]
            if rate_fams and not args.derive_from_rates:
                print(
                    f"error: {', '.join(rate_fams)} are rates; yearly volumes from rates are derived. "
                    "Add --derive-from-rates to accept that (QC vs field totals runs).",
                    file=sys.stderr,
                )
                return 2
            for f in rate_fams:
                for k in keys:
                    if family(k) == f:
                        source[k] = "derived_from_rate"
        else:
            fams = [f for f in WELL_TOTALS if any(family(k) == f for k in keys)]
            if not fams:
                if not args.derive_from_rates:
                    rates = sorted({family(k) for k in keys if family(k) in WELL_RATES})
                    outdir.mkdir(parents=True, exist_ok=True)
                    (outdir / "missing_vectors.json").write_text(
                        json.dumps(
                            {
                                "missing_vectors": WELL_TOTALS,
                                "available_well_rates": rates,
                                "rule": "missing SUMMARY vector is an error, never zero",
                                "fix": "add WOPT/WWPT/WWIT (WGPT/WGIT) to SUMMARY, or rerun with --derive-from-rates",
                            },
                            indent=2,
                        )
                    )
                    print(
                        "error: no well totals (WOPT/WWPT/WGPT/WWIT/WGIT) in SUMMARY. Available well rates: "
                        f"{', '.join(rates) or 'none'}. Use --derive-from-rates (labelled derived) or add "
                        "well totals to SUMMARY.",
                        file=sys.stderr,
                    )
                    return 2
                fams = [f for f in WELL_RATES if any(family(k) == f for k in keys)]
                for f in fams:
                    for k in keys:
                        if family(k) == f:
                            source[k] = "derived_from_rate"
        for f in fams:
            for w in wells:
                k = f"{f}:{w}"
                if k in keys:
                    well_keys.append(k)
            if not any(family(k) == f for k in keys):
                missing.append(f)
        # A well may legitimately lack a family (e.g. WWIR on a producer); require each well has >=1 key.
        for w in wells:
            if w in wells_all and not any(k.endswith(f":{w}") for k in well_keys):
                missing.append(f"{'/'.join(fams)}:{w}")

    if missing:
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "missing_vectors.json").write_text(
            json.dumps(
                {
                    "missing_vectors": sorted(set(missing)),
                    "gas_injection_absent": gas_absent,
                    "rule": "missing SUMMARY vector is an error, never zero",
                },
                indent=2,
            )
        )
        print(
            f"error: absent in SUMMARY: {', '.join(sorted(set(missing)))}. Not filled with zero.",
            file=sys.stderr,
        )
        return 2

    all_keys = field_keys + well_keys
    members: list[Member] = []
    issues: list[str] = ensemble.problems()
    for real in wanted:
        rp = found.get(real)
        df = runpath_lib.data_file(rp, (ecl_sub, args.eclbase), real)[0] if rp else None
        if df is None or not df.with_suffix(".UNSMRY").is_file():
            issues.append(f"realization {real}: no {ecl_sub}/{args.eclbase}.DATA/UNSMRY")
            continue
        if not args.no_ok_check and not (rp / "OK").is_file():
            issues.append(f"realization {real}: no OK marker in runpath (still running or failed)")
            continue
        qc_extra = [k for pair in FIELD_QC_PAIRS for k in pair if k in keys] if args.derive_from_rates else []
        try:
            m = Member(real, df, list(dict.fromkeys(all_keys + qc_extra)))
        except Exception as exc:  # noqa: BLE001
            issues.append(f"realization {real}: {type(exc).__name__}: {exc}")
            continue
        absent = [k for k in all_keys if k not in m.keys]
        if absent:
            print(f"error: realization {real} lacks {', '.join(absent)} (not zero-filled)", file=sys.stderr)
            return 2
        members.append(m)
    if issues and not args.allow_partial:
        for line in issues:
            print(f"error: {line}", file=sys.stderr)
        return 1
    if not members:
        print("error: no valid realization", file=sys.stderr)
        return 1

    outdir.mkdir(parents=True, exist_ok=True)
    stale = outdir / "missing_vectors.json"
    if stale.exists():
        stale.unlink()

    qc = None
    if args.by_well and any(v == "derived_from_rate" for v in source.values()):
        qc = derivation_qc(members, args.derive_tol)
        (outdir / "qc_derivation.json").write_text(json.dumps(qc, indent=2))
        if qc["status"] != "pass":
            print(f"error: derived well volumes failed QC: {json.dumps(qc['checks'])}", file=sys.stderr)
            return 2

    field_rows: list[dict[str, Any]] = []
    well_rows: list[dict[str, Any]] = []
    for m in members:
        field_rows += yearly_rows(m, field_keys, {})
        if well_keys:
            well_rows += yearly_rows(m, well_keys, source)

    written: list[str] = []
    for name, rows in (("field", field_rows), ("well", well_rows)):
        if not rows:
            continue
        per = pd.DataFrame(rows)
        if name == "well":
            per.insert(1, "WELL", per["KEY"].str.split(":").str[1])
        per.to_csv(outdir / f"{name}_yearly_per_real.csv", index=False)
        per.to_parquet(outdir / f"{name}_yearly_per_real.parquet", index=False)
        st = stats_table(per, conv, args.min_n_percentiles)
        st.to_csv(outdir / f"{name}_yearly_stats.csv", index=False)
        mw = mean_wide(st)
        mw.to_csv(outdir / f"{name}_yearly_mean_wide.csv", index=False)
        written += [f"{name}_yearly_per_real.csv", f"{name}_yearly_stats.csv", f"{name}_yearly_mean_wide.csv"]

    ends = sorted({str(m.end)[:10] for m in members})
    manifest = {
        "skill": "yearly-profiles",
        "scratch": str(scratch),
        "iter": args.iteration,
        "eclbase": f"{ecl_sub}/{args.eclbase}",
        "realizations": [m.real for m in members],
        "n": len(members),
        "partial_issues": issues,
        "field_vectors": field_keys,
        "gas_injection_found": gas_found,
        "gas_injection_absent": gas_absent,
        "by_well": args.by_well,
        "wells": wells,
        "well_vectors": well_keys,
        "well_source": "derived_from_rate" if source else ("summary_total" if well_keys else None),
        "derivation_qc": qc["status"] if qc else None,
        "p10_p90": conv,
        "percentiles_skipped": (
            f"N={len(members)} < {args.min_n_percentiles}: P10/P50/P90 not computed"
            if len(members) < args.min_n_percentiles
            else None
        ),
        "sim_start": str(members[0].start)[:10],
        "sim_end_dates": ends,
        "year_rule": "calendar year Jan 1 .. Jan 1; clipped first/last year flagged PARTIAL",
        "files": written,
        "generated": datetime.now().isoformat(timespec="seconds"),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    fstats = pd.read_csv(outdir / "field_yearly_stats.csv")
    vol = fstats[fstats["METRIC"] == "VOL"][
        ["KEY", "YEAR", "PARTIAL", "UNIT", "N", "MEAN", "P10", "P50", "P90"]
    ]
    lines = [
        f"# Yearly profiles — {args.label}",
        "",
        f"- Scratch: `{scratch}` {args.iteration}, deck `{ecl_sub}/{args.eclbase}`, N = {len(members)}",
        f"- Simulation period: {manifest['sim_start']} .. {', '.join(ends)} (last year PARTIAL if clipped)",
        f"- Field vectors: {', '.join(field_keys)}",
        f"- Gas injection probe: found {gas_found or 'none'}; absent {gas_absent}",
        f"- P10/P90 convention: **{conv}** (per-year statistics)"
        + (f" — **{manifest['percentiles_skipped']}**" if manifest["percentiles_skipped"] else ""),
    ]
    if args.by_well:
        lines.append(f"- Wells: {', '.join(wells)}; well keys: {', '.join(well_keys)}")
        lines.append(
            f"- Well volume source: {manifest['well_source']}"
            + (
                f" (QC {qc['status']}, max rel err {max(c['max_rel_err'] for c in qc['checks']):.2e})"
                if qc
                else ""
            )
        )
    lines += [
        "",
        "## Field yearly volumes (VOL)",
        "",
        md_table(vol),
        "",
        "Missing SUMMARY vectors are an error (exit 2). Nothing is zero-filled.",
    ]
    (outdir / "README.md").write_text("\n".join(lines) + "\n")
    with pd.option_context(
        "display.width", 220, "display.float_format", "{:.6g}".format, "display.max_rows", 200
    ):
        print(vol.to_string(index=False))
    print(f"wrote {outdir}  (N={len(members)}, p10_p90={conv}, gas_inj_found={gas_found})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
