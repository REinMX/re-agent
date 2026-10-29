#!/usr/bin/env python3
"""Generate a prediction case folder: rendered .ert + design matrix CSV.

Never invents prior ranges — reads parameters.csv (or GEN_KW prior files).
Does NOT start ERT or Flow.

Paths come from the site contract (shared loader): paths.root, prediction_case.template_ert
(else paths.ert_config), prediction_case.parameters_csv (else uncertainty.parameters_csv),
prediction_case.cases_root, uncertainty.prior_files. CLI flags override.

Usage:
  prediction_case.py --contract site.contract.yml --name pred_A --nreal 20 --seed 1
  prediction_case.py --name pred_rock_only --nreal 100 --seed 3 --groups ROCK

Exit: 0 ok, 2 bad input / missing priors, 1 unexpected error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import posixpath
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402

# Relative path token in an ERT config: optional <CONFIG_PATH>/ then one or more ../
PATH_TOKEN = re.compile(r"(?<![\w./])(<CONFIG_PATH>/)?((?:\.\./)+[^\s,)\"']*)")


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"error: {msg}\n")
    raise SystemExit(code)


def load_parameters_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        die(f"parameters.csv not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            name = (r.get("name") or "").strip()
            if not name:
                continue
            dist = (r.get("distribution") or "").strip().upper()
            arg1 = (r.get("arg1") or "").strip()
            arg2 = (r.get("arg2") or "").strip()
            if not dist or arg1 == "" or arg2 == "":
                die(f"incomplete prior for {name!r} in {path} (never invent ranges; fix parameters.csv)")
            try:
                low = float(arg1)
                high = float(arg2)
            except ValueError:
                die(f"non-numeric prior args for {name!r} in {path}")
            if low <= 0 and dist == "LOGUNIF":
                die(f"LOGUNIF low must be > 0 for {name!r}")
            if high < low:
                die(f"prior max < min for {name!r}")
            rows.append(
                {
                    "group": (r.get("group") or "").strip(),
                    "name": name,
                    "distribution": dist,
                    "min": low,
                    "max": high,
                    "notes": (r.get("notes") or "").strip(),
                }
            )
    if not rows:
        die(f"no parameters in {path}")
    return rows


def filter_groups(params: list[dict[str, Any]], groups: list[str] | None) -> list[dict[str, Any]]:
    if not groups:
        return params
    want = {g.upper() for g in groups}
    out = [p for p in params if p["group"].upper() in want]
    if not out:
        die(f"no parameters match --groups {sorted(want)}")
    return out


def sample_param(rng: np.random.Generator, p: dict[str, Any]) -> float:
    dist = p["distribution"]
    low, high = p["min"], p["max"]
    if dist == "UNIFORM":
        return float(rng.uniform(low, high))
    if dist == "LOGUNIF":
        return float(math.exp(rng.uniform(math.log(low), math.log(high))))
    die(f"unsupported distribution {dist!r} for {p['name']} (copy from project priors only)")


def sample_design_matrix(params: list[dict[str, Any]], nreal: int, seed: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for i in range(nreal):
        row: dict[str, Any] = {"REAL": i}
        for p in params:
            row[p["name"]] = sample_param(rng, p)
        rows.append(row)
    return rows


def write_design_csv(path: Path, params: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    fieldnames = ["REAL"] + [p["name"] for p in params]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def rebase_relative_paths(text: str, src_dir: str, dst_dir: str) -> str:
    """Rewrite ../ paths (and <CONFIG_PATH>/../) so they point at the same files from dst_dir."""
    src = posixpath.normpath(str(src_dir).replace("\\", "/"))
    dst = posixpath.normpath(str(dst_dir).replace("\\", "/"))

    def repl(match: re.Match[str]) -> str:
        prefix, rel = match.group(1) or "", match.group(2)
        trailing = "/" if rel.endswith("/") else ""
        target = posixpath.normpath(posixpath.join(src, rel))
        new = posixpath.relpath(target, dst)
        return f"{prefix}{new}{trailing}"

    return PATH_TOKEN.sub(repl, text)


def render_ert(
    template_text: str,
    *,
    name: str,
    nreal: int,
    seed: int,
    case_dir: str | None,
    src_dir: str | Path | None = None,
    dst_dir: str | Path | None = None,
) -> str:
    """Rewrite NUM_REALIZATIONS, RANDOM_SEED, CASE_DIR; keep GEN_KW prior paths valid.

    Relative paths are rebased from the template folder (src_dir) to the case folder
    (dst_dir). Default layout: <root>/ert/model -> <root>/ert/cases/<name>.
    """
    text = template_text
    if not re.search(r"^\s*NUM_REALIZATIONS\b", text, re.M):
        die("template missing NUM_REALIZATIONS")
    text = re.sub(
        r"^(\s*NUM_REALIZATIONS\s+)\S+",
        rf"\g<1>{nreal}",
        text,
        count=1,
        flags=re.M,
    )
    if re.search(r"^\s*RANDOM_SEED\b", text, re.M):
        text = re.sub(
            r"^(\s*RANDOM_SEED\s+)\S+",
            rf"\g<1>{seed}",
            text,
            count=1,
            flags=re.M,
        )
    else:
        # Insert after NUM_REALIZATIONS
        text = re.sub(
            r"(^\s*NUM_REALIZATIONS\s+\S+\s*$)",
            rf"\1\nRANDOM_SEED           {seed}",
            text,
            count=1,
            flags=re.M,
        )

    scratch_name = case_dir or name
    if re.search(r"DEFINE\s+<CASE_DIR>", text):
        text = re.sub(
            r"(DEFINE\s+<CASE_DIR>\s+)\S+",
            rf"\g<1>{scratch_name}",
            text,
            count=1,
        )
    else:
        text = re.sub(
            r"(^\s*RANDOM_SEED\s+\S+\s*$)",
            rf"\1\n\nDEFINE  <CASE_DIR>      {scratch_name}",
            text,
            count=1,
            flags=re.M,
        )

    # Path rewrite: template folder -> case folder (any depth).
    if src_dir is None or dst_dir is None:
        src_dir, dst_dir = "/_root/ert/model", f"/_root/ert/cases/{name}"
    text = rebase_relative_paths(text, str(src_dir), str(dst_dir))

    # Header comment for generated case
    banner = (
        f"-- GENERATED by prediction-case skill — do not hand-edit ranges here.\n"
        f"-- case={name}  NUM_REALIZATIONS={nreal}  RANDOM_SEED={seed}\n"
        f"-- Priors copied from parameters.csv / GEN_KW *priors.txt (not invented).\n"
        f"-- This file does NOT launch ERT/Flow; generate only.\n"
    )
    if text.lstrip().startswith("--"):
        # Prepend after first comment block start
        text = banner + "\n" + text
    else:
        text = banner + "\n" + text
    return text


def write_meta(
    path: Path,
    *,
    name: str,
    nreal: int,
    seed: int,
    groups: list[str] | None,
    params: list[dict[str, Any]],
    template: Path,
    parameters_csv: Path,
    design_csv: Path,
    ert_path: Path,
) -> None:
    meta = {
        "name": name,
        "nreal": nreal,
        "seed": seed,
        "groups": groups or ["ALL"],
        "param_names": [p["name"] for p in params],
        "param_count": len(params),
        "template": str(template),
        "parameters_csv_source": str(parameters_csv),
        "design_matrix": str(design_csv),
        "ert": str(ert_path),
        "p10_p90_note": (
            "Design matrix samples are from prior min/max only; "
            "do not treat columns as P10/P50/P90. Band reports state the contract p10_p90."
        ),
        "launches": False,
        "notes": (
            "GEN_KW priors in the .ert still point at the shared project prior files. "
            "design_matrix.csv is the sampled prediction matrix for inspection / "
            "DESIGN_MATRIX workflows; ERT GEN_KW sampling uses RANDOM_SEED."
        ),
    }
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def write_readme(
    path: Path,
    *,
    name: str,
    nreal: int,
    seed: int,
    groups: list[str] | None,
    params: list[dict[str, Any]],
    activate: str = "",
    case_rel: str = "",
) -> None:
    """README with run notes. Activate comes from the contract profile; the folder is relative to paths.root."""
    glist = ", ".join(groups) if groups else "ALL (from parameters.csv)"
    run = []
    if activate:
        run.append(activate)
    else:
        run.append("# activate the environment of your contract profile (field `activate`) first")
    run.append(f"cd {case_rel or name}   # relative to the project root (contract paths.root)")
    run.append(f"ert ensemble_experiment {name}.ert")
    lines = [
        f"# Prediction case `{name}`",
        "",
        f"- **NUM_REALIZATIONS:** {nreal}",
        f"- **RANDOM_SEED:** {seed}",
        f"- **Design-matrix groups:** {glist}",
        f"- **Parameters in design_matrix.csv:** {', '.join(p['name'] for p in params)}",
        "",
        "Priors were **copied** from `parameters.csv` (UNIFORM / LOGUNIF min/max).",
        "No ranges were invented. P10/P50/P90 are **not** filled from samples.",
        "",
        "## Generate only",
        "This folder was written by `prediction_case.py`. It does **not** start ERT or Flow.",
        "To run later:",
        "```",
        *run,
        "```",
        "",
        "## RMS wells/zones",
        "Not generated here. Export wells/zones from RMS, then QC them against COMPDAT.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _relative_or_abs(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def build_case(
    project: Path,
    *,
    name: str,
    nreal: int,
    seed: int,
    groups: list[str] | None,
    template: Path,
    parameters_csv: Path,
    cases_root: Path,
    overwrite: bool,
    prior_files: list[Path] | None = None,
    activate: str = "",
) -> Path:
    if nreal < 1:
        die("--nreal must be >= 1")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        die(f"invalid case name {name!r} (use alphanumerics, _, ., -)")
    if not template.is_file():
        die(f"template .ert not found: {template}")

    params_all = load_parameters_csv(parameters_csv)
    params = filter_groups(params_all, groups)

    out_dir = cases_root / name
    if out_dir.exists():
        if not overwrite:
            die(f"case dir exists (pass --overwrite): {out_dir}")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # Copy parameters.csv as-is (source of truth)
    dest_params = out_dir / "parameters.csv"
    shutil.copy2(parameters_csv, dest_params)

    rows = sample_design_matrix(params, nreal, seed)
    design_csv = out_dir / "design_matrix.csv"
    write_design_csv(design_csv, params, rows)

    ert_path = out_dir / f"{name}.ert"
    rendered = render_ert(
        template.read_text(encoding="utf-8"),
        name=name,
        nreal=nreal,
        seed=seed,
        case_dir=name,
        src_dir=template.parent.resolve(),
        dst_dir=out_dir.resolve(),
    )
    ert_path.write_text(rendered, encoding="utf-8")

    write_meta(
        out_dir / "case_meta.json",
        name=name,
        nreal=nreal,
        seed=seed,
        groups=groups,
        params=params,
        template=template,
        parameters_csv=parameters_csv,
        design_csv=design_csv,
        ert_path=ert_path,
    )
    write_readme(
        out_dir / "README.md",
        name=name,
        nreal=nreal,
        seed=seed,
        groups=groups,
        params=params,
        activate=activate,
        case_rel=_relative_or_abs(out_dir, project),
    )

    # priors snapshot for audit (copy, do not invent): contract uncertainty.prior_files
    priors_dir = out_dir / "priors_snapshot"
    priors_dir.mkdir()
    for src in prior_files or []:
        if not src.is_file():
            die(f"prior file listed in the contract does not exist: {src}")
        shutil.copy2(src, priors_dir / src.name)

    print(f"wrote {out_dir}")
    print(f"  ert:            {ert_path}")
    print(f"  design_matrix:  {design_csv} ({nreal} rows, {len(params)} params)")
    print(f"  parameters.csv: {dest_params} (copied)")
    print(f"  seed={seed} groups={groups or ['ALL']}")
    return out_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_contract_args(ap)
    ap.add_argument("--name", required=True, help="Case folder name")
    ap.add_argument("--nreal", type=int, required=True, help="NUM_REALIZATIONS")
    ap.add_argument("--seed", type=int, required=True, help="RANDOM_SEED")
    ap.add_argument(
        "--groups",
        default="",
        help="Comma-separated parameter groups for design matrix (e.g. ROCK). "
        "Default: all groups in parameters.csv",
    )
    ap.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Template .ert (default: contract prediction_case.template_ert, else paths.ert_config)",
    )
    ap.add_argument(
        "--parameters-csv",
        type=Path,
        default=None,
        help="Priors table (default: contract prediction_case.parameters_csv, else uncertainty.parameters_csv)",
    )
    ap.add_argument(
        "--cases-root",
        type=Path,
        default=None,
        help="Output root (default: contract prediction_case.cases_root)",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing case directory",
    )
    args = ap.parse_args(argv)

    try:
        contract = contract_lib.load_contract(args.contract, args.profile, require_project=False)
        project = contract.path("root").resolve()
        pc = contract.get("prediction_case") or {}
        unc = contract.get("uncertainty") or {}

        def pick(cli: Path | None, *candidates: Any, what: str) -> Path:
            if cli is not None:
                return cli.resolve()
            for cand in candidates:
                if cand:
                    return contract.resolve(cand).resolve()
            raise ContractError(f"{what} is not set in the contract and no CLI flag was given")

        template = pick(
            args.template,
            pc.get("template_ert"),
            (contract.get("paths") or {}).get("ert_config"),
            what="prediction_case.template_ert (or paths.ert_config)",
        )
        parameters_csv = pick(
            args.parameters_csv,
            pc.get("parameters_csv"),
            unc.get("parameters_csv"),
            what="prediction_case.parameters_csv (or uncertainty.parameters_csv)",
        )
        cases_root = pick(args.cases_root, pc.get("cases_root"), what="prediction_case.cases_root")
        prior_files = [contract.resolve(p) for p in (unc.get("prior_files") or [])]
        activate = contract.activate_for("bash") if contract.get("activate") else ""
        if contract_lib.has_placeholder(activate):
            activate = ""
    except ContractError as exc:
        die(f"contract: {exc}")
    for note in contract.warnings:
        sys.stderr.write(f"note: contract: {note}\n")
    if not project.is_dir():
        die(f"project root not found: {project}")
    groups = [g.strip() for g in args.groups.split(",") if g.strip()] or None

    try:
        build_case(
            project,
            name=args.name,
            nreal=args.nreal,
            seed=args.seed,
            groups=groups,
            template=template,
            parameters_csv=parameters_csv,
            cases_root=cases_root,
            overwrite=args.overwrite,
            prior_files=prior_files,
            activate=activate,
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
