#!/usr/bin/env python3
"""Extract ensemble summary time series from an FMU scratch.

Reads SMSPEC/UNSMRY via res2df. Writes only under --outdir (deliverables/profiles).
Does NOT invent zeros for missing SUMMARY vectors. Profiles extract whatever exists;
contract required_summary gaps are listed in the manifest as missing_vs_contract
(informational). Use ensemble-bands for the hard fail against the contract.

P10/P90 are not computed here. See ensemble-bands (numerical convention).

Usage:
    ensemble_profiles.py --contract /path/to/site.contract.yml
    ensemble_profiles.py --contract ... --scratch ... --outdir ... --eclbase <DECK> \\
        --time-index monthly --iter 0

Exit: 0 success, 1 partial ensemble failure, 2 usage/input error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"))
import pandas as pd
import runpath_lib

try:
    from res2df import ResdataFiles as SimFiles
except ImportError:
    try:
        from res2df import EclFiles as SimFiles
    except ImportError:
        from ecl2df import EclFiles as SimFiles  # type: ignore[no-redef]
try:
    from res2df import summary as summary_module
except ImportError:
    from ecl2df import summary as summary_module  # type: ignore[no-redef]

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402

VALID_TIME_INDEX = frozenset({"raw", "daily", "monthly", "yearly", "first", "last"})

# Field mnemonics vs well/group: well keys in SMSPEC look like WOPR:<WELL>.
FIELD_PREFIXES = frozenset({"F", "R"})  # rough; we classify by presence of ":"


def iter_dirname(token: str | int) -> str:
    """--iter value -> runpath folder: 3 -> iter-3; iter-3 / pred_ref kept as given."""
    t = str(token).strip()
    if t.isdigit():
        return f"iter-{int(t)}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", t):
        raise argparse.ArgumentTypeError(f"bad --iter {token!r} (use 0, iter-3, pred_ref ...)")
    return t


def list_smspec_keys(data_file: Path) -> list[str]:
    """Keys available in one member's summary (exclude YEARS helper if present)."""
    smry = SimFiles(str(data_file)).get_summary()
    keys = sorted(k for k in smry.keys() if k.upper() != "YEARS")
    return keys


def mnemonic_family(key: str) -> str:
    """WOPR:<WELL> -> WOPR; FOPT -> FOPT."""
    return key.split(":", 1)[0]


def present_families(keys: list[str]) -> set[str]:
    return {mnemonic_family(k) for k in keys}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_contract_args(parser)
    parser.add_argument("--scratch", type=Path, default=None, help="Override paths.scratch")
    parser.add_argument("--outdir", type=Path, default=None, help="Override deliverables/profiles")
    parser.add_argument(
        "--eclbase",
        default=None,
        help="Deck name or eclipse/model/NAME (default: contract eclbase; required)",
    )
    parser.add_argument(
        "--iter",
        type=iter_dirname,
        default=None,
        dest="iteration",
        help="Iteration (0 -> iter-0) or runpath folder (pred_ref). Default: contract runpath.default_iter",
    )
    parser.add_argument("--time-index", default="monthly", choices=sorted(VALID_TIME_INDEX))
    parser.add_argument(
        "--expected",
        type=int,
        default=None,
        help="Realizations started (default: contract expected_realizations, else all found + warning)",
    )
    parser.add_argument(
        "--vectors",
        default=None,
        help="Comma-separated keys or families to extract (default: all keys in SMSPEC)",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Continue when some realizations fail",
    )
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(
            args.contract, args.profile, skip_required=("eclbase",) if args.eclbase else ()
        )
        ecl_sub, args.eclbase = contract_lib.resolve_eclbase(contract, args.eclbase)
        scratch = Path(args.scratch) if args.scratch else contract.path("scratch")
        outdir = Path(args.outdir) if args.outdir else contract.path("deliverables") / "profiles"
        p10_p90 = contract.p10_p90
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2
    for note in contract.warnings:
        sys.stderr.write(f"note: contract: {note}\n")
    if args.iteration is None:
        args.iteration = iter_dirname(contract.default_iter())

    runpath_glob = str(scratch / "realization-*" / args.iteration)
    discovered = runpath_lib.read_ensemble(runpath_glob, need_data_file=False)
    numbers = [m.realization for m in discovered.found]
    if not numbers and not discovered.dropped:
        sys.stderr.write(f"error: no runpaths match {runpath_glob}\n")
        return 2
    if len(set(numbers)) != len(numbers):
        sys.stderr.write("error: runpaths must have unique realization-<n>\n")
        return 2

    if args.expected is None and contract.get("expected_realizations") is not None:
        args.expected = int(contract["expected_realizations"])
    if args.expected is None:
        args.expected = len(numbers)
        sys.stderr.write(
            f"warning: expected realizations not set (--expected or contract expected_realizations); "
            f"using the {len(numbers)} found\n"
        )
    expected = set(range(args.expected))
    ensemble = runpath_lib.read_ensemble(runpath_glob, (ecl_sub, args.eclbase), expected=expected)
    extra = sorted(m.realization for m in ensemble.extra)
    issues: list[dict[str, Any]] = [
        {"realization": m.realization, "issue": m.reason} for m in ensemble.dropped
    ]
    if issues and not args.allow_partial:
        for item in issues:
            sys.stderr.write(f"error: realization {item['realization']}: {item['issue']}\n")
        return 1

    probe_keys: list[str] | None = None
    for member in ensemble.found:
        data_file = member.data_file
        if data_file and data_file.with_suffix(".UNSMRY").is_file():
            try:
                probe_keys = list_smspec_keys(data_file)
                break
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"warning: could not read keys from {data_file}: {exc}\n")
    if not probe_keys:
        sys.stderr.write("error: could not read SMSPEC keys from any realization\n")
        return 2

    if args.vectors:
        requested = [v.strip() for v in args.vectors.split(",") if v.strip()]
        # Expand families to matching SMSPEC keys
        column_keys: list[str] = []
        for req in requested:
            if ":" in req or req in probe_keys:
                if req in probe_keys:
                    column_keys.append(req)
            else:
                matches = [k for k in probe_keys if mnemonic_family(k) == req]
                column_keys.extend(matches if matches else [req])  # keep exact for res2df miss
        # Deduplicate preserving order
        deduped: list[str] = []
        seen: set[str] = set()
        for k in column_keys:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        column_keys = deduped
    else:
        column_keys = list(probe_keys)
        requested = list(probe_keys)

    frames: list[pd.DataFrame] = []
    used: list[int] = []

    for member in ensemble.found:
        real, data_file = member.realization, member.data_file
        assert data_file is not None
        if not data_file.with_suffix(".UNSMRY").is_file():
            issues.append({"realization": real, "issue": "missing UNSMRY"})
            continue
        try:
            frame = summary_module.df(
                SimFiles(str(data_file)),
                time_index=args.time_index,
                column_keys=column_keys,
            )
        except Exception as exc:  # noqa: BLE001
            issues.append({"realization": real, "issue": f"{type(exc).__name__}: {exc}"})
            continue
        if frame.empty:
            issues.append({"realization": real, "issue": "empty summary frame"})
            continue
        frame = frame.rename_axis("DATE").reset_index()
        frame.insert(0, "REAL", real)
        frames.append(frame)
        used.append(real)

    missing_reals = sorted(expected - set(used))
    if (issues or missing_reals) and not args.allow_partial:
        for item in issues:
            sys.stderr.write(f"error: realization {item['realization']}: {item['issue']}\n")
        if missing_reals:
            sys.stderr.write(f"error: missing realizations: {missing_reals}\n")
        sys.stderr.write("Use --allow-partial to continue with valid members.\n")
        return 1
    if not frames:
        sys.stderr.write("error: no valid realization\n")
        return 1

    long = pd.concat(frames, ignore_index=True)
    common = set.intersection(*(set(f.columns) for f in frames))
    dropped = sorted(set(long.columns) - common)
    long = long[[c for c in long.columns if c in common]]
    vector_cols = [c for c in long.columns if c not in ("REAL", "DATE")]

    tidy = long.melt(id_vars=["REAL", "DATE"], var_name="KEY", value_name="VALUE")
    tidy["VALUE"] = pd.to_numeric(tidy["VALUE"], errors="coerce")

    field_keys = [k for k in vector_cols if ":" not in k]
    well_keys = [k for k in vector_cols if ":" in k]
    field_df = tidy[tidy["KEY"].isin(field_keys)].copy() if field_keys else tidy.iloc[0:0].copy()
    well_df = tidy[tidy["KEY"].isin(well_keys)].copy() if well_keys else tidy.iloc[0:0].copy()

    outdir.mkdir(parents=True, exist_ok=True)
    field_csv = outdir / "field_profiles.csv"
    well_csv = outdir / "well_profiles.csv"
    all_csv = outdir / "profiles_long.csv"
    field_parquet = outdir / "field_profiles.parquet"
    well_parquet = outdir / "well_profiles.parquet"
    all_parquet = outdir / "profiles_long.parquet"

    tidy.to_csv(all_csv, index=False)
    field_df.to_csv(field_csv, index=False)
    well_df.to_csv(well_csv, index=False)
    tidy.to_parquet(all_parquet, index=False)
    field_df.to_parquet(field_parquet, index=False)
    well_df.to_parquet(well_parquet, index=False)

    required = contract.required_summary
    families = present_families(vector_cols)
    # A required name is present if exact key exists OR any KEY:well matches the family.
    present_required = [r for r in required if r in families or r in vector_cols]
    missing_vs_contract = [r for r in required if r not in families and r not in vector_cols]

    manifest = {
        "skill": "ensemble-profiles",
        "contract": str(args.contract),
        "scratch": str(scratch),
        "eclbase": f"{ecl_sub}/{args.eclbase}",
        "iteration": args.iteration,
        "profile": contract.profile_name,
        "time_index": args.time_index,
        "p10_p90_convention_note": (
            f"Profiles do not compute percentiles. "
            f"Contract p10_p90={p10_p90} (numerical: P10 = low; exceedance: P10 = upside)."
        ),
        "realizations_used": sorted(used),
        "n_realizations": len(used),
        "expected": args.expected,
        "missing_realizations": missing_reals,
        "extra_realizations_not_used": extra,
        "issues": issues,
        "vectors_extracted": vector_cols,
        "vectors_dropped_not_in_every_member": dropped,
        "required_summary": required,
        "present_vs_required": present_required,
        "missing_vs_contract": missing_vs_contract,
        "missing_vs_contract_note": (
            "Informational only. ensemble-profiles does not fail on missing required_summary. "
            "ensemble-bands fails when required vectors are absent (never fill with zero)."
        ),
        "outputs": {
            "profiles_long_csv": str(all_csv),
            "profiles_long_parquet": str(all_parquet),
            "field_profiles_csv": str(field_csv),
            "field_profiles_parquet": str(field_parquet),
            "well_profiles_csv": str(well_csv),
            "well_profiles_parquet": str(well_parquet),
        },
        "res2df_version": getattr(sys.modules.get("res2df"), "__version__", None),
        "pandas_version": pd.__version__,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    readme = outdir / "README.md"
    readme.write_text(
        "# Ensemble profiles\n\n"
        f"- Scratch: `{scratch}`\n"
        f"- Realizations used: {len(used)} / expected {args.expected}\n"
        f"- Time index: `{args.time_index}`\n"
        f"- P10/P90: not computed here; contract convention is **{p10_p90}** "
        "(numerical = 10th/50th/90th percentile of the ensemble).\n"
        f"- Vectors extracted: {', '.join(vector_cols)}\n"
        f"- Missing vs contract `required_summary` (not filled with zero): "
        f"{', '.join(missing_vs_contract) if missing_vs_contract else '(none)'}\n"
        "- Hard QC against the full contract is `ensemble-bands`.\n",
        encoding="utf-8",
    )

    sys.stdout.write(
        f"Wrote {outdir}: {len(used)} reals, {len(vector_cols)} vectors, "
        f"{len(missing_vs_contract)} missing_vs_contract, {len(issues)} issues\n"
    )
    if missing_vs_contract:
        sys.stdout.write(f"missing_vs_contract: {', '.join(missing_vs_contract)}\n")
    return 1 if (issues or missing_reals) else 0


if __name__ == "__main__":
    raise SystemExit(main())
