#!/usr/bin/env python3
"""Export summary plot snapshots via ResInsight CLI (headless mode from the contract profile).

Uses official flags:
  ResInsight --summaryplot <VECTOR> <SMSPEC...> --snapshotfolder DIR --savesnapshots plots

Does not start Flow/ERT. Does not write inside scratch (copies PNGs to deliverables).

Exit: 0 if at least the required vectors produced non-empty PNGs; 2 if ResInsight unavailable
or required plots missing (caller may fall back to matplotlib).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402

# offscreen is not here: the Linux builds ship only the xcb Qt platform plugin (measured 2026.09.1).
HEADLESS_MODES = ("xvfb-run", "none")


def find_smspec(
    scratch: Path, real: int, eclbase: str, subdir: str = "eclipse/model", iteration: str = "iter-0"
) -> Path:
    # FMU layout: realization-N/<iter>/<subdir>/<ECLBASE>.SMSPEC (subdir from contract eclbase)
    p = scratch / f"realization-{real}" / iteration / subdir / f"{eclbase}.SMSPEC"
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return p


def headless_prefix(mode: str) -> tuple[list[str], dict[str, str]]:
    """Command prefix and extra env for the profile resinsight.headless mode."""
    if mode == "xvfb-run":
        return ["xvfb-run", "-a", "env", "LIBGL_ALWAYS_SOFTWARE=1"], {}
    return [], {}


def run_summaryplot(
    ri_bin: Path,
    vector: str,
    smspecs: list[Path],
    snap_dir: Path,
    timeout_s: int,
    headless: str = "xvfb-run",
) -> subprocess.CompletedProcess[str]:
    snap_dir.mkdir(parents=True, exist_ok=True)
    prefix, extra_env = headless_prefix(headless)
    cmd = [
        *prefix,
        str(ri_bin),
        "--summaryplot",
        vector,
        *[str(s) for s in smspecs],
        "--snapshotfolder",
        str(snap_dir),
        "--savesnapshots",
        "plots",
        "--snapshotsize",
        "1200",
        "800",
        "--size",
        "1200",
        "800",
    ]
    import os

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env={**os.environ, **extra_env},
    )


def pick_newest_png(folder: Path) -> Path | None:
    pngs = sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return pngs[0] if pngs else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_contract_args(parser)
    parser.add_argument(
        "--eclbase",
        default=None,
        help="Deck name or eclipse/model/NAME (default: contract eclbase; required)",
    )
    parser.add_argument(
        "--iter", default=None, dest="iteration", help="Runpath iteration folder (default: contract)"
    )
    parser.add_argument("--realization", type=int, default=0)
    parser.add_argument(
        "--multi-reals",
        default="",
        help="Optional comma list for multi-case FOPT overlay (e.g. 0,10,20)",
    )
    parser.add_argument(
        "--vectors",
        default=None,
        help="Comma-separated summary vectors for one-case plots (default: contract plot_required)",
    )
    parser.add_argument("--ri-bin", type=Path, default=None, help="Default: profile resinsight.bin")
    parser.add_argument(
        "--headless", choices=HEADLESS_MODES, default=None, help="Default: profile resinsight.headless"
    )
    parser.add_argument("--outdir", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(
            args.contract, args.profile, skip_required=("eclbase",) if args.eclbase else ()
        )
        ecl_sub, args.eclbase = contract_lib.resolve_eclbase(contract, args.eclbase)
        scratch = contract.path("scratch")
        deliverables = contract.path("deliverables")
        ri_cfg = contract.get("resinsight") or {}
        ri_bin = args.ri_bin or (Path(ri_cfg["bin"]) if ri_cfg.get("bin") else None)
        headless = args.headless or str(ri_cfg.get("headless") or "none")
        required_vectors = contract.plot_required()
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2
    if ri_bin is None or contract_lib.has_placeholder(str(ri_bin)):
        sys.stderr.write("error: no ResInsight binary: set profile resinsight.bin or pass --ri-bin\n")
        return 2
    if headless not in HEADLESS_MODES:
        sys.stderr.write(f"error: resinsight.headless must be one of {HEADLESS_MODES}, got {headless!r}\n")
        return 2
    if headless == "xvfb-run" and not shutil.which("xvfb-run"):
        sys.stderr.write("error: xvfb-run not found (profile resinsight.headless=xvfb-run)\n")
        return 2
    if not ri_bin.is_file():
        sys.stderr.write(f"error: ResInsight binary missing: {ri_bin}\n")
        return 2
    args.ri_bin = ri_bin
    try:
        iteration = contract_lib.iter_dirname(args.iteration) if args.iteration else contract.default_iter()
    except ContractError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    outdir = args.outdir or (deliverables / "plots" / "resinsight")
    outdir.mkdir(parents=True, exist_ok=True)

    vectors = (
        [v.strip() for v in args.vectors.split(",") if v.strip()] if args.vectors else list(required_vectors)
    )
    written: list[dict[str, Any]] = []
    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ri_plots_") as tmp:
        tmp_path = Path(tmp)
        # One vector, one realization
        try:
            smspec0 = find_smspec(scratch, args.realization, args.eclbase, ecl_sub, iteration)
        except FileNotFoundError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2

        for vector in vectors:
            snap = tmp_path / f"snap_{vector.replace(':', '_')}"
            try:
                proc = run_summaryplot(args.ri_bin, vector, [smspec0], snap, args.timeout, headless)
            except subprocess.TimeoutExpired:
                errors.append(f"timeout:{vector}")
                continue
            png = pick_newest_png(snap)
            if proc.returncode != 0 or png is None or png.stat().st_size == 0:
                errors.append(f"fail:{vector}:rc={proc.returncode}:stderr={proc.stderr[-400:]}")
                continue
            safe = vector.replace(":", "_")
            dest = outdir / f"RI_{safe}_realization-{args.realization}.png"
            shutil.copy2(png, dest)
            written.append(
                {
                    "key": vector,
                    "path": str(dest),
                    "backend": "ResInsight-CLI",
                    "kind": "summaryplot_single_real",
                    "realization": args.realization,
                    "smspec": str(smspec0),
                    "bytes": dest.stat().st_size,
                }
            )

        # Optional multi-real sample of the first required vector
        multi = [int(x) for x in args.multi_reals.split(",") if x.strip()]
        if multi:
            try:
                smspecs = [find_smspec(scratch, r, args.eclbase, ecl_sub, iteration) for r in multi]
            except FileNotFoundError as exc:
                errors.append(f"multi_smspec:{exc}")
                smspecs = []
            if smspecs:
                multi_vec = required_vectors[0]
                snap = tmp_path / "snap_multi"
                try:
                    proc = run_summaryplot(args.ri_bin, multi_vec, smspecs, snap, args.timeout, headless)
                    png = pick_newest_png(snap)
                    if proc.returncode == 0 and png and png.stat().st_size > 0:
                        dest = outdir / f"RI_{multi_vec.replace(':', '_')}_multi_real_sample.png"
                        shutil.copy2(png, dest)
                        written.append(
                            {
                                "key": multi_vec,
                                "path": str(dest),
                                "backend": "ResInsight-CLI",
                                "kind": "summaryplot_multi_real",
                                "realizations": multi,
                                "bytes": dest.stat().st_size,
                            }
                        )
                    else:
                        errors.append(f"multi:rc={proc.returncode}")
                except subprocess.TimeoutExpired:
                    errors.append("timeout:multi")

    manifest = {
        "skill": "resinsight-plots",
        "script": "try_resinsight_cli.py",
        "backend": "ResInsight-CLI",
        "ri_bin": str(args.ri_bin),
        "ri_version_hint": str(ri_cfg.get("version") or ""),
        "headless": headless,
        "iteration": iteration,
        "p10_p90_note": "Single/multi-case plots are not percentile bands; use the matplotlib evidence plots for P10/P90 bands.",
        "plots": written,
        "errors": errors,
        "note": "Evidence only — tables/QC gate pass/fail.",
    }
    (outdir / "manifest_resinsight.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"outdir": str(outdir), "n_plots": len(written), "errors": errors}, indent=2))

    required = set(required_vectors)
    have = {p["key"] for p in written if p.get("kind") == "summaryplot_single_real"}
    if not required.issubset(have):
        sys.stderr.write(f"error: ResInsight missing required vectors; have={have}\n")
        return 2
    # non-empty check
    for p in written:
        if Path(p["path"]).stat().st_size <= 0:
            sys.stderr.write(f"error: empty png {p['path']}\n")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
