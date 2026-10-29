#!/usr/bin/env python3
"""Run the QC tools of this repository on one FMU case and write one report. Read-only.

Each tool runs as its own process. A step runs only when you give its inputs.
The script writes to --outdir only. It never writes in a runpath, a config folder, or a deck folder.

Usage:
    case_qc.py --outdir <dir> [--config <case>.ert] [--runpaths '<glob>'] [--prior '<glob>']
               [--obs <file.obs> ...] [--expected N | --realizations 0-4,7] [--keys FOPT ...] [--time-index yearly]
               [--eclbase <base>] [--deck CASE.DATA | --include a.inc ...] [--metadata <folder>] [--casepath <case folder>]

Steps and inputs:
    config_map     --config
    config_load    --config            (needs ERT and its plugins in the active environment)
    obs_qc         --obs, or OBS_CONFIG from --config
    triage         --runpaths
    summary        --runpaths          (summary vectors and numerical P10/P50/P90)
    misfit         --runpaths and observations. With --prior, a prior and posterior comparison
    metadata       --metadata          (fmu-dataio metadata files)
    deck           --deck or --include
    grid           --deck, with BASE.EGRID and BASE.INIT next to it (grid property checks)
    completions    --deck, with BASE.EGRID and BASE.INIT next to it (COMPDAT cells in the grid)
    swatinit       --deck, with BASE.INIT and BASE.UNRST next to it (needs subscript)

--expected defaults to NUM_REALIZATIONS of --config. For a run of a subset, give --realizations with the ERT spec.
Output: report.md, report.json, and a folder for each step with its log and files.
Exit code: 0 = no step found a problem, 1 = at least one step found a problem or could not run, 2 = usage error.
A step that has no input is "skipped". It is not a problem.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS.parent / "tools"))
from simcase import default_vectors  # noqa: E402

TOOLS = {
    "config_map": SKILLS / "ert-config-author-lint/scripts/ert_config_map.py",
    "config_load": SKILLS / "ert-config-author-lint/scripts/check_ert_config.py",
    "obs_qc": SKILLS / "history-match-qc/scripts/obs_qc.py",
    "triage": SKILLS / "ert-diagnose/scripts/triage_runpath.py",
    "summary": SKILLS / "res2df-resdata-extract/scripts/ensemble_summary.py",
    "misfit": SKILLS / "history-match-qc/scripts/misfit_report.py",
    "compensation": SKILLS / "history-match-qc/scripts/compensation_check.py",
    "metadata": SKILLS / "fmu-dataio-sumo/scripts/check_dataio_metadata.py",
    "swatinit": SKILLS / "deck-scal-pvt-qc/scripts/swatinit_qc.py",
    "deck": SKILLS / "deck-scal-pvt-qc/scripts/deck_qc.py",
    "grid": SKILLS / "grid-property-qc/scripts/grid_qc.py",
    "completions": SKILLS / "grid-property-qc/scripts/completion_in_grid.py",
}
ORDER = [
    "config_map",
    "config_load",
    "obs_qc",
    "triage",
    "summary",
    "misfit",
    "compensation",
    "metadata",
    "deck",
    "grid",
    "completions",
    "swatinit",
]
# The user did not ask for these. A default key that no member has (FGPT in an oil-water case) is dropped
# with a note. A key from --keys stays strict.
DEFAULT_KEYS = default_vectors()


def run_tool(name: str, args: list[str], outdir: Path, timeout: int) -> dict:
    """Run one tool. Save its output. Return the status record."""
    step_dir = outdir / name
    step_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")  # plugin programs
    command = [sys.executable, "-W", "ignore", str(TOOLS[name]), *args]
    started = time.time()
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env, check=False)
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", f"timed out after {timeout} s"
    (step_dir / "stdout.txt").write_text(out)
    (step_dir / "stderr.txt").write_text(err)
    status = {0: "ok", 1: "problems"}.get(code, "failed to run")
    if name == "swatinit" and code == 0:
        try:
            result = json.loads((step_dir / "files/swatinit_qc.json").read_text())
            if result["status"] == "not applicable":
                status = "not applicable"
        except (OSError, ValueError, KeyError) as exc:
            code, status, err = 2, "failed to run", f"invalid SWATINIT report: {exc}"
            (step_dir / "stderr.txt").write_text(err)
    return {
        "step": name,
        "status": status,
        "exit_code": code,
        "seconds": round(time.time() - started, 1),
        "command": " ".join(command),
        "stdout": out,
        "stderr": err,
        "dir": str(step_dir),
    }


def json_of(record: dict) -> dict | None:
    text = record["stdout"]
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


NOISE = ("DeprecationWarning", "class ResdataVersion", "warnings.warn")


def last_lines(text: str, count: int = 3) -> str:
    """The last lines of a log, without warning lines from dependencies."""
    lines = [line for line in text.strip().splitlines() if line.strip() and not any(n in line for n in NOISE)]
    return " | ".join(lines[-count:])


def check_config(args, records: dict, skipped: dict, notes: list[str], expected, obs_files: list[Path]):
    if args.config:
        records["config_map"] = run_tool(
            "config_map", [str(args.config), "--json"], args.outdir, args.timeout
        )
        info = json_of(records["config_map"]) or {}
        if expected is None and args.realizations is None and "NUM_REALIZATIONS" in info.get("settings", {}):
            expected = int(info["settings"]["NUM_REALIZATIONS"]["value"])
            notes.append(f"--expected is {expected}, from NUM_REALIZATIONS in the config")
        for item in info.get("parameters_and_responses", []):
            if item["keyword"] == "GEN_KW" and item["options"]:
                dist = Path(item["options"][0])
                if not dist.is_absolute():
                    dist = Path(item["file"]).parent / dist
                if dist.is_file():
                    args.gen_kw_dists.append(dist)
        if not obs_files:
            for ref in info.get("references", []):
                if ref["kind"] == "OBS_CONFIG" and ref["status"] == "ok":
                    obs_files.append(Path(ref["path"]))
                    notes.append(f"observation file from OBS_CONFIG: {ref['path']}")
        records["config_load"] = run_tool("config_load", [str(args.config)], args.outdir, args.timeout)
    else:
        skipped["config_map"] = skipped["config_load"] = "no --config"
    return expected, obs_files


def check_observations(args, records: dict, skipped: dict, obs_files: list[Path]) -> None:
    if obs_files:
        records["obs_qc"] = run_tool("obs_qc", [*map(str, obs_files), "--json"], args.outdir, args.timeout)
    else:
        skipped["obs_qc"] = "no observation file"


def check_runpaths(args, records: dict, skipped: dict, expected, obs_files: list[Path]) -> None:
    if args.realizations is not None:
        extra = ["--realizations", args.realizations]
    else:
        extra = ["--expected", str(expected)] if expected is not None else []
    if args.runpaths:
        records["triage"] = run_tool("triage", [args.runpaths, *extra, "--json"], args.outdir, args.timeout)
        eclbase = ["--eclbase", args.eclbase] if args.eclbase else []
        key_args = ["--keys", *args.keys] if args.keys else ["--optional-keys", *DEFAULT_KEYS]
        summary_args = [args.runpaths, *key_args, "--time-index", args.time_index, *extra, *eclbase]
        summary_args += ["--outdir", str(args.outdir / "summary" / "files")]
        if args.allow_partial:
            summary_args.append("--allow-partial")
        records["summary"] = run_tool("summary", summary_args, args.outdir, args.timeout)
        if obs_files:
            misfit_args = [a for f in obs_files for a in ("--obs", str(f))] + ["--posterior", args.runpaths]
            if args.prior:
                misfit_args += ["--prior", args.prior]
            misfit_args += [*extra, *eclbase, "--outdir", str(args.outdir / "misfit" / "files")]
            if args.allow_partial:
                misfit_args.append("--allow-partial")
            records["misfit"] = run_tool("misfit", misfit_args, args.outdir, args.timeout)
        else:
            skipped["misfit"] = "no observation file"
    else:
        skipped["triage"] = skipped["summary"] = skipped["misfit"] = "no --runpaths"
    check_compensation(args, records, skipped, extra)


def check_compensation(args, records: dict, skipped: dict, inventory: list[str]) -> None:
    """Parameters at prior edges with a good fit (compensation_check.py). Needs a prior and the GEN_KW files."""
    if not (args.runpaths and args.prior and args.gen_kw_dists):
        skipped["compensation"] = "needs --runpaths, --prior and a --config with GEN_KW files"
        return
    comp_args = [
        *map(str, args.gen_kw_dists),
        "--prior-runpaths",
        args.prior,
        "--posterior-runpaths",
        args.runpaths,
        *inventory,
    ]
    manifest = args.outdir / "misfit" / "files" / "manifest.json"
    if manifest.is_file():
        comp_args += ["--misfit-manifest", str(manifest)]
    records["compensation"] = run_tool("compensation", [*comp_args, "--json"], args.outdir, args.timeout)


def check_metadata_and_deck(args, records: dict, skipped: dict) -> None:
    if args.metadata:
        meta_args = [str(args.metadata), "--json"]
        if args.casepath:
            meta_args += ["--casepath", str(args.casepath), "--require-fmu"]
        records["metadata"] = run_tool("metadata", meta_args, args.outdir, args.timeout)
    else:
        skipped["metadata"] = "no --metadata"
    if args.deck or args.include:
        deck_args = (
            ["--deck", str(args.deck)]
            if args.deck
            else [a for p in args.include for a in ("--include", str(p))]
        )
        records["deck"] = run_tool("deck", [*deck_args, "--json"], args.outdir, args.timeout)
    else:
        skipped["deck"] = "no --deck or --include"
    if not args.deck:
        skipped["swatinit"] = "no --deck"
    elif importlib.util.find_spec("subscript") is None:
        skipped["swatinit"] = "subscript not installed"
    elif not all(args.deck.with_suffix(suffix).is_file() for suffix in (".INIT", ".UNRST")):
        skipped["swatinit"] = "no INIT and UNRST next to --deck"
    else:
        records["swatinit"] = run_tool(
            "swatinit",
            ["--case", str(args.deck), "--out", str(args.outdir / "swatinit" / "files")],
            args.outdir,
            args.timeout,
        )
    base = args.deck.with_suffix("") if args.deck else None
    if base and all(base.with_name(base.name + s).is_file() for s in (".EGRID", ".INIT")):
        records["grid"] = run_tool(
            "grid",
            ["--case", str(base), "--outdir", str(args.outdir / "grid" / "files")],
            args.outdir,
            args.timeout,
        )
        completion_args = ["--data", str(args.deck), "--case", str(base)]
        completion_args += ["--outdir", str(args.outdir / "completions" / "files")]
        records["completions"] = run_tool("completions", completion_args, args.outdir, args.timeout)
    else:
        skipped["grid"] = skipped["completions"] = "no --deck with BASE.EGRID and BASE.INIT next to it"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--runpaths", help="Quoted glob of runpaths. For a history match, the posterior.")
    parser.add_argument("--prior", help="Quoted glob of runpaths of the prior")
    parser.add_argument("--obs", action="append", type=Path, default=[])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--expected", type=int, help="Expected number of realizations, numbered 0 to N-1")
    group.add_argument("--realizations", help="Expected realizations as an ERT spec, for example 0-4,7")
    parser.add_argument(
        "--keys",
        nargs="+",
        help=f"Summary vectors. Each must exist. Default: {' '.join(DEFAULT_KEYS)}, where the case has them",
    )
    parser.add_argument("--eclbase", help="Deck base name when a runpath has multiple DATA files")
    parser.add_argument("--time-index", default="yearly")
    parser.add_argument("--deck", type=Path)
    parser.add_argument("--include", action="append", type=Path, default=[])
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--casepath", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds for each step")
    args = parser.parse_args(argv)

    if args.deck and args.include:
        print("ERROR: give --deck or --include, not both", file=sys.stderr)
        return 2
    for path in [args.config, args.deck, *args.obs, *args.include]:
        if path is not None and not path.is_file():
            print(f"ERROR: not a file: {path}", file=sys.stderr)
            return 2
    if not any([args.config, args.runpaths, args.obs, args.deck, args.include, args.metadata]):
        print("ERROR: give at least one input", file=sys.stderr)
        return 2
    output = args.outdir.resolve()
    sources = [p.resolve() for p in (args.config, args.deck, *args.obs, *args.include) if p]
    trees = [p.resolve() for p in (args.metadata, args.casepath) if p]
    for pattern in (args.runpaths, args.prior):
        if pattern:
            trees.extend(Path(p).resolve() for p in glob.glob(pattern) if Path(p).is_dir())
    if any(output == p or output in p.parents for p in sources) or any(
        output == tree or tree in output.parents or output in tree.parents for tree in trees
    ):
        print("ERROR: --outdir overlaps an input tree or contains an input file", file=sys.stderr)
        return 2
    if args.outdir.exists() and (not args.outdir.is_dir() or any(args.outdir.iterdir())):
        print("ERROR: --outdir must be empty; choose a new report directory", file=sys.stderr)
        return 2
    args.outdir.mkdir(parents=True, exist_ok=True)

    args.gen_kw_dists = []
    records: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    notes: list[str] = []
    expected = args.expected
    obs_files = list(args.obs)

    expected, obs_files = check_config(args, records, skipped, notes, expected, obs_files)
    check_observations(args, records, skipped, obs_files)
    check_runpaths(args, records, skipped, expected, obs_files)
    check_metadata_and_deck(args, records, skipped)

    report = build_report(records, skipped, notes, args)
    (args.outdir / "report.md").write_text(report["markdown"])
    (args.outdir / "report.json").write_text(json.dumps(report["data"], indent=2, default=str))
    print(report["markdown"])
    print(f"Wrote {args.outdir / 'report.md'}")
    bad = [r for r in records.values() if r["status"] not in ("ok", "not applicable")]
    return 1 if bad else 0


# ---------------- report ----------------


def facts(name: str, record: dict) -> str:
    """One line of key numbers for a step."""
    data = json_of(record)
    out, err = record["stdout"], record["stderr"]
    if name == "config_map" and data:
        errors = [p for p in data["problems"] if p["severity"] == "error"]
        return (
            f"{len(data['forward_models'])} forward models, {len(data['hooks'])} hooks, "
            f"{len(errors)} problems, {len(data['problems']) - len(errors)} warnings"
        )
    if name == "config_load":
        return last_lines(out or err, 2)
    if name == "obs_qc" and data:
        errors = [p for p in data["problems"] if p["severity"] == "error"]
        return f"{data['summary_observations']} summary observations, {len(errors)} errors, {len(data['problems']) - len(errors)} warnings"
    if name == "triage" and data:
        s = data["summary"]
        return f"{s['count']} realizations: {s['by_state']}; causes {s['by_failure_class'] or 'none'}; missing {s['missing_realizations'] or 'none'}"
    if name == "metadata" and data:
        errors = [p for p in data["problems"] if p["severity"] == "error"]
        return f"{data['files']} metadata files, {len(errors)} errors, {len(data['problems']) - len(errors)} warnings"
    if name == "deck" and data:
        return f"{data['errors']} errors, {data['warnings']} warnings; rows read {data['rows_read']}"
    if name in ("grid", "completions"):
        head = [line for line in out.splitlines() if line.startswith(("grid_qc:", "completion_in_grid:"))]
        codes = sorted({line.split()[1] for line in out.splitlines() if line.startswith("FINDING")})
        return (
            f"{head[0].split(': ', 1)[1].split('. Wrote')[0]}; codes {codes or 'none'}"
            if head
            else last_lines(out or err, 2)
        )
    if name == "summary":
        match = re.search(r"(\d+) realizations, (\d+) vectors, (\d+) issues", out)
        return (
            f"{match.group(1)} realizations, {match.group(2)} vectors, {match.group(3)} issues"
            if match
            else last_lines(out or err, 2)
        )
    if name == "compensation" and data:
        runs = data["runs"]
        flags = sorted({f["name"] for r in runs for f in r["findings"] if f["code"] == "HMQC-COMP-EDGE"})
        return f"verdict {data['verdict']}; EDGE {', '.join(flags) or 'none'}; {data['seed_note']}"
    if name == "misfit":
        rows = [line for line in out.splitlines() if line.startswith(("prior:", "posterior:"))]
        count = sum(1 for line in out.splitlines() if line.startswith("FINDING"))
        return " ; ".join(rows) + f" ; {count} findings" if rows else last_lines(out or err, 2)
    return last_lines(out or err, 2)


def build_report(records: dict, skipped: dict, notes: list[str], args) -> dict:
    lines = ["# Case QC report", ""]
    lines.append(
        "This report runs read-only checks. It does not run a simulator. It gives no verdict on the model."
    )
    lines.append("")
    lines.append("## Steps")
    lines.append("| Step | Status | Key facts |")
    lines.append("|---|---|---|")
    for name in ORDER:
        if name in records:
            r = records[name]
            lines.append(f"| {name} | {r['status']} | {facts(name, r)} |")
        else:
            lines.append(f"| {name} | skipped | {skipped.get(name, '')} |")
    if notes:
        lines += ["", "## How inputs were chosen", *[f"- {n}" for n in notes]]

    attention: list[str] = []
    for name, r in records.items():
        if r["status"] == "failed to run":
            attention.append(
                f"{name}: could not run (exit code {r['exit_code']}). {last_lines(r['stderr'] or r['stdout'])}"
            )
        elif r["status"] == "problems":
            attention.append(f"{name}: found problems. {facts(name, r)}")

    # findings of the misfit step
    mf = args.outdir / "misfit" / "files" / "manifest.json"
    findings = []
    if mf.is_file():
        manifest = json.loads(mf.read_text())
        findings = manifest.get("findings", [])
        lines += ["", "## History match"]
        for label, info in manifest["ensembles"].items():
            lines.append(
                f"- {label}: {len(info['realizations_used'])} realizations used, missing {info['missing'] or 'none'}"
            )
        misfit_log = records["misfit"]["stdout"].splitlines()
        lines += [f"- {line}" for line in misfit_log if line.startswith(("prior:", "posterior:"))]
        for item in findings:
            lines.append(
                f"- finding [{item['ensemble']}] {item['finding']}: {item['detail']} {item['examples']}"
            )
        lines.append(
            "- Misfit is the mean of squared normalized residuals. It is not the ERT objective function."
        )
    sm = args.outdir / "summary" / "files" / "manifest.json"
    if sm.is_file():
        manifest = json.loads(sm.read_text())
        lines += [
            "",
            "## Summary vectors",
            f"- vectors {manifest['vectors_returned']}, units {manifest['units']}, time index {manifest['time_index']}",
            f"- realizations used {len(manifest['realizations_used'])}, missing {manifest['missing_realizations'] or 'none'}",
            "- P10 and P90 are numerical percentiles. P10 is the low value. This is not the volumetric convention.",
        ]
        if manifest.get("optional_keys_dropped"):
            lines.append(
                f"- default vectors that no realization has, not reported: {manifest['optional_keys_dropped']}"
            )
    lines += ["", "## Items that need attention"]
    if findings:
        attention.append(
            f"misfit: {len(findings)} findings about observations and parameters (see History match). They are hints, not errors"
        )
    lines += [f"- {a}" for a in attention] or ["- No step found a problem and no finding was reported."]
    lines += [
        "",
        "## What this report does not cover",
        "- Whether the simulator accepts the deck. Only a run proves it.",
        "- The ERT update step, the localization, and the inflation.",
        "- Observation types other than summary observations.",
        "- Any step that shows as skipped above.",
        "",
        f"Step logs and files: `{args.outdir}`. Each step folder holds `stdout.txt` and `stderr.txt`.",
    ]
    data = {
        "default_keys_dropped": manifest.get("optional_keys_dropped", []) if sm.is_file() else [],
        "steps": {
            name: {k: v for k, v in rec.items() if k not in ("stdout", "stderr")}
            | {"facts": facts(name, rec)}
            for name, rec in records.items()
        },
        "skipped": skipped,
        "notes": notes,
        "attention": attention,
        "findings": findings,
    }
    return {"markdown": "\n".join(lines) + "\n", "data": data}


if __name__ == "__main__":
    sys.exit(main())
