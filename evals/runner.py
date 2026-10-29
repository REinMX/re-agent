#!/usr/bin/env python3
"""Run the agent evals headless and grade them. Standard library only.

Each case in evals/cases.toml runs the agent once, in a fresh copy of this repository, with its own fixtures.
Grading has two parts:
    checks   deterministic: commands the agent ran or tried, and patterns in the final answer
    rubric   an LLM judge reads the transcript and decides each rubric item, with evidence

Usage:
    evals/runner.py [--harness claude|codex] [--model sonnet] [--case ID ...] [--skill NAME ...] [--jobs 4]

Environment:
    EVAL_PYTHON   interpreter with ert, res2df, and pandas, written into the site contract
    HM_CASE       ERT output folder with realization-*/iter-0 and iter-1 (history-match cases)
    HM_OBS        observation file for HM_CASE
A case whose fixture needs a missing variable is skipped, and the summary says so.

Output: evals/results/<stamp>-<harness>/results.json, summary.md, and transcripts/<case>.jsonl.
Exit code: 0 = all cases that ran passed, 1 = at least one case failed, 2 = usage error.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals/cases.toml"
RESULTS = ROOT / "evals/results"
AGENT_FILES = ["AGENTS.md", "CLAUDE.md", "site.contract.example.yml", "skills", "tools", ".claude", ".codex"]
# The agent may read, search, and run Python. Other shell programs need permission, so a headless run denies them.
ALLOWED_PROGRAMS = [
    "python",
    "python3",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "find",
    "wc",
    "which",
    "df",
    "cd",
    "pwd",
]


class Skip(Exception):
    """A fixture cannot be made in this environment."""


def fixture_repo(work: Path, data: Path) -> None:
    repo = work / "repo"
    repo.mkdir()
    for name in AGENT_FILES:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, repo / name, symlinks=True)
        elif source.is_file():
            shutil.copy2(source, repo / name)


def fixture_contract(work: Path, data: Path) -> None:
    python = os.environ.get("EVAL_PYTHON", sys.executable)
    (work / "out").mkdir(exist_ok=True)
    contract = {
        "project": "synthetic-eval-only",
        "komodo_release": "synthetic-eval-only-not-komodo",
        "python": python,
        "activate": ": # synthetic fixture; no environment activation",
        "scratch_root": str(data),
        "output_root": str(work / "out"),
        "queue_system": "LOCAL",
        "lsf_queue": "",
        "simulator": "FLOW",
        "simulator_version": "synthetic-eval-only-not-installed",
        "percentile_convention": "numerical",
        "sumo_environment": "",
        "data_classification": "synthetic",
        "agent_host": "linux",
        "execution_mode": "manual",
        "model_endpoint": "synthetic-eval-only://no-network",
        "endpoint_approved": True,
    }
    # JSON strings are compatible with doctor's quoted scalar subset and preserve literal paths.
    (work / "repo/site.contract.yml").write_text(
        "# Synthetic fixture only; no real endpoint or corporate approval.\n"
        "# endpoint_approved is synthetic test input, not authorization for any real service.\n"
        + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in contract.items())
        + "\n",
        encoding="utf-8",
    )


def fixture_failed_ensemble(work: Path, data: Path) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "evals/make_failed_ensemble.py"), str(data)],
        check=True,
        capture_output=True,
    )


def fixture_ahm_case(work: Path, data: Path) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "evals/make_ahm_case.py"), str(data)],
        check=True,
        capture_output=True,
    )


def fixture_small_config(work: Path, data: Path) -> None:
    model = data / "model"
    model.mkdir(parents=True)
    (model / "case.ert").write_text(
        "NUM_REALIZATIONS 10\n"
        "QUEUE_SYSTEM LOCAL\n"
        "MAX_RUNNING 2\n"
        f"RUNPATH {data}/scratch/case/realization-<IENS>/iter-<ITER>\n"
        f"ENSPATH {data}/scratch/case/storage\n"
        "ECLBASE CASE-<IENS>\n"
        "FORWARD_MODEL FLOW(<ECLBASE>=<ECLBASE>)\n"
    )


def fixture_hm_case(work: Path, data: Path) -> list[Path]:
    case, obs = os.environ.get("HM_CASE"), os.environ.get("HM_OBS")
    if not case or not obs or not Path(case).is_dir() or not Path(obs).is_file():
        raise Skip("HM_CASE and HM_OBS must name an ERT output folder and an observation file")
    (data / "hm").symlink_to(Path(case).resolve())
    shutil.copy2(obs, data / "hm.obs")
    return [Path(case).resolve()]  # the link target must be readable too


def fixture_mbal_cases(work: Path, data: Path) -> None:
    for case in ("OILBOX", "OILBOX_AQ", "GASBOX"):
        target = data / case
        target.mkdir()
        for name in ("history.csv", "pvt.csv"):
            shutil.copy2(ROOT / "tests/data/mbal" / case / name, target / name)


def fixture_lessons(work: Path, data: Path) -> None:
    """Synthetic inputs for the coded Drogon lesson checks. See tests/data/lessons/README.md."""
    shutil.copytree(ROOT / "tests/data/lessons", data / "lessons")


def fixture_everest_runs(work: Path, data: Path) -> None:
    """Synthetic Everest output folders, configs, PRT files, and a cross-evaluation table."""
    shutil.copytree(ROOT / "tests/data/everest", data / "everest")
    (work / "out").mkdir(exist_ok=True)


def fixture_broken_obs(work: Path, data: Path) -> None:
    obs = os.environ.get("HM_OBS")
    if not obs or not Path(obs).is_file():
        raise Skip("HM_OBS must name an observation file")
    text = Path(obs).read_text()
    match = re.search(r"ERROR\s*=\s*[0-9.]+", text)
    if match is None:
        raise Skip("HM_OBS has no ERROR field to break")
    text = text[: match.start()] + "ERROR = 0" + text[match.end() :]
    text += "\nSUMMARY_OBSERVATION LEGACY_1 { VALUE = 0.1; ERROR = 0.05; DAYS = 30; KEY = WWCT:PROD; };\n"
    (data / "broken.obs").write_text(text)


def fixture_flow_text(work: Path, data: Path) -> None:
    """Tiny synthetic Flow inputs. No simulator files or site settings are needed."""
    (data / "synthetic.DATA").write_text(
        "RUNSPEC\nOIL\nWATER\nFIELD\nDIMENS\n2 2 2 /\nSCHEDULE\n"
        "WELSPECS\n'A' 'G' 1 1 1 'OIL' /\n/\n"
        "WCONHIST\n'B' OPEN ORAT -1 0 0 /\n/\n"
        "COMPDAT\n'A' 3 1 1 4 OPEN /\n/\n"
        "DATES\n2 JAN 2020 /\n1 JAN 2020 /\n/\n"
    )
    (data / "synthetic.PRT").write_text(
        "Report step 2, date: 2020-01-02\nTimestep chopped\nProblem: convergence\n"
        "Newton iterations: 3\nLinear iterations: 12\nError: stopped\n"
    )
    (data / "history.csv").write_text(
        "DATE,WOPR:A,WOPRH:A\n2020-01-01,10,10\n2020-01-02,,10\n2020-01-03,20,10\n"
    )
    (data / "missing.contract.yml").write_text('output_root: ""\n')


FIXTURES = {
    "contract": fixture_contract,
    "failed_ensemble": fixture_failed_ensemble,
    "small_config": fixture_small_config,
    "hm_case": fixture_hm_case,
    "broken_obs": fixture_broken_obs,
    "mbal_cases": fixture_mbal_cases,
    "ahm_case": fixture_ahm_case,
    "flow_text": fixture_flow_text,
    "lessons": fixture_lessons,
    "everest_runs": fixture_everest_runs,
}


def allowed_tools(work: Path) -> list[str]:
    python = os.environ.get("EVAL_PYTHON", sys.executable)
    bash = [f"Bash({program} *)" for program in [*ALLOWED_PROGRAMS, python]]
    return ["Read", "Grep", "Glob", *bash, f"Edit(/{work}/out/**)", f"Edit(/{work}/repo/site.contract.yml)"]


def run_claude(prompt: str, cwd: Path, model: str, max_turns: int, log: Path, extra: list[Path]) -> dict:
    command = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(max_turns),
        "--no-session-persistence",
        "--setting-sources",
        "project",
        "--model",
        model,
        "--add-dir",
        str(cwd.parent),
        *[str(d) for d in extra],
        "--allowedTools",
        *allowed_tools(cwd.parent),
    ]
    started = time.time()
    done = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1800
    )
    log.write_text(done.stdout)
    return parse_claude(done.stdout) | {
        "seconds": round(time.time() - started, 1),
        "exit_code": done.returncode,
    }


def parse_claude(stream: str) -> dict:
    """Commands tried, commands blocked, tool results, and the final answer from a stream-json log."""
    tried: list[str] = []
    blocked: list[str] = []
    texts: list[str] = []
    results: list[str] = []
    final, cost, turns = "", None, None
    completed = False
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "assistant":
            for item in event.get("message", {}).get("content", []):
                if item.get("type") == "tool_use":
                    tried.append(tool_call_text(item.get("name", ""), item.get("input") or {}))
                elif item.get("type") == "text":
                    texts.append(item.get("text", ""))
        elif kind == "user":
            for item in event.get("message", {}).get("content", []):
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    results.append(str(item.get("content"))[:2000])
        elif kind == "result":
            completed = event.get("subtype") == "success" and event.get("is_error", False) is False
            final = event.get("result") or ""
            cost, turns = event.get("total_cost_usd"), event.get("num_turns")
            for denial in event.get("permission_denials") or []:
                blocked.append(tool_call_text(denial.get("tool_name", ""), denial.get("tool_input") or {}))
    return {
        "tried": tried,
        "blocked": blocked,
        "tool_results": results,
        "answer": final or (texts[-1] if texts else ""),
        "all_text": "\n".join(texts),
        "completed_successfully": completed,
        "cost_usd": cost,
        "turns": turns,
    }


def tool_call_text(name: str, args: dict) -> str:
    if name == "Bash":
        return str(args.get("command", ""))
    target = args.get("file_path") or args.get("path") or args.get("pattern") or ""
    return f"{name}({target})"


def run_codex(prompt: str, cwd: Path, model: str, max_turns: int, log: Path, extra: list[Path]) -> dict:
    command = ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only"]
    if model:
        command += ["--model", model]
    started = time.time()
    done = subprocess.run(
        [*command, prompt], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1800
    )
    log.write_text(done.stdout)
    return parse_codex(done.stdout) | {
        "seconds": round(time.time() - started, 1),
        "exit_code": done.returncode,
    }


def parse_codex(stream: str) -> dict:
    completed = False
    failed = False
    tried: list[str] = []
    blocked: list[str] = []
    texts: list[str] = []
    results: list[str] = []
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            completed = True
        if event.get("type") in ("turn.failed", "error"):
            failed = True
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        if item.get("type") == "command_execution":
            tried.append(str(item.get("command", "")))
            results.append(str(item.get("aggregated_output", ""))[:2000])
            if item.get("status") in ("declined", "rejected"):
                blocked.append(str(item.get("command", "")))
        elif item.get("type") == "agent_message":
            texts.append(item.get("text", ""))
    return {
        "tried": tried,
        "blocked": blocked,
        "tool_results": results,
        "answer": texts[-1] if texts else "",
        "all_text": "\n".join(texts),
        "completed_successfully": completed and not failed,
        "cost_usd": None,
        "turns": None,
    }


HARNESSES = {"claude": run_claude, "codex": run_codex}


def grade_checks(checks: dict, run: dict) -> list[dict]:
    """Deterministic checks. A tried command counts, also when the harness blocked it."""
    out: list[dict] = []
    answer = run["answer"]
    for pattern in checks.get("never_tried", []):
        hits = [c for c in run["tried"] if re.search(pattern, c)]
        out.append({"check": f"never tried /{pattern}/", "passed": not hits, "evidence": hits[:3]})
    for pattern in checks.get("tried", []):
        hits = [c for c in run["tried"] if re.search(pattern, c)]
        out.append({"check": f"tried /{pattern}/", "passed": bool(hits), "evidence": hits[:3]})
    for pattern in checks.get("answer_matches", []):
        hit = re.search(pattern, answer, re.I | re.S)
        out.append(
            {
                "check": f"answer matches /{pattern}/",
                "passed": bool(hit),
                "evidence": [hit.group(0)] if hit else [],
            }
        )
    for pattern in checks.get("answer_not_matches", []):
        hit = re.search(pattern, answer, re.I | re.S)
        out.append(
            {
                "check": f"answer does not match /{pattern}/",
                "passed": not hit,
                "evidence": [hit.group(0)] if hit else [],
            }
        )
    return out


JUDGE_PROMPT = """You grade one run of an AI agent. Judge only from the record below. Do not guess.

TASK PROMPT:
{prompt}

COMMANDS AND TOOL CALLS THE AGENT TRIED (in order):
{tried}

CALLS THAT THE HARNESS BLOCKED:
{blocked}

TEXT THAT THE AGENT WROTE BEFORE ITS FINAL ANSWER:
{all_text}

FINAL ANSWER:
{answer}

RUBRIC. Decide each item. An item passes only if the record shows it.
{rubric}

Reply with JSON only, no other text, in this form:
{{"items": [{{"item": 1, "pass": true, "evidence": "a short quote or command from the record"}}]}}
"""


def judge(case: dict, run: dict, model: str) -> list[dict]:
    rubric = case.get("rubric", [])
    if not rubric:
        return []
    prompt = JUDGE_PROMPT.format(
        prompt=case["prompt_text"],
        tried="\n".join(f"- {c[:400]}" for c in run["tried"]) or "(none)",
        blocked="\n".join(f"- {c[:400]}" for c in run["blocked"]) or "(none)",
        all_text=run["all_text"][-6000:] or "(none)",
        answer=run["answer"][-6000:] or "(empty)",
        rubric="\n".join(f"{i}. {item}" for i, item in enumerate(rubric, 1)),
    )
    with tempfile.TemporaryDirectory() as empty:
        done = subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--output-format",
                "json",
                "--model",
                model,
                "--tools",
                "",
                "--no-session-persistence",
                "--setting-sources",
                "project",
            ],
            cwd=empty,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=600,
        )
    try:
        if done.returncode != 0:
            raise ValueError(f"judge exit code {done.returncode}")
        outer = json.loads(done.stdout)
        if outer.get("subtype") != "success" or outer.get("is_error", False) is not False:
            raise ValueError("judge has no successful result")
        text = outer["result"]
        return grade_judge_payload(text, rubric)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
        return [{"item": r, "passed": False, "evidence": f"judge output not usable: {error}"} for r in rubric]


def grade_judge_payload(text: str, rubric: list[str]) -> list[dict]:
    try:
        items = json.loads(text[text.index("{") : text.rindex("}") + 1])["items"]
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        by_number = {}
        for item in items:
            if not isinstance(item, dict) or type(item.get("item")) is not int:
                raise ValueError("invalid rubric item number")
            number = item["item"]
            if number in by_number or not 1 <= number <= len(rubric):
                raise ValueError("duplicate or unexpected rubric item")
            if (
                type(item.get("pass")) is not bool
                or not isinstance(item.get("evidence"), str)
                or not item["evidence"].strip()
            ):
                raise ValueError("rubric verdict needs a boolean and evidence")
            by_number[number] = item
    except (json.JSONDecodeError, KeyError, ValueError, TypeError, AttributeError) as error:
        return [{"item": r, "passed": False, "evidence": f"judge output not usable: {error}"} for r in rubric]
    return [
        {
            "item": text_,
            "passed": by_number.get(n, {}).get("pass") is True,
            "evidence": by_number.get(n, {}).get("evidence", "judge gave no answer for this item"),
        }
        for n, text_ in enumerate(rubric, 1)
    ]


def run_case(case: dict, args, outdir: Path) -> dict:
    record = {"id": case["id"], "skill": case["skill"]}
    with tempfile.TemporaryDirectory(prefix="work-") as tmp:
        work = Path(tmp)
        data = work / "data"
        data.mkdir()
        try:
            fixture_repo(work, data)
            extra: list[Path] = []
            for name in case.get("setup", []):
                extra += FIXTURES[name](work, data) or []
        except Skip as reason:
            return record | {"status": "skipped", "reason": str(reason)}
        prompt = case["prompt"].format(data=data, repo=work / "repo", out=work / "out")
        case = case | {"prompt_text": prompt}
        log = outdir / "transcripts" / f"{case['id']}.jsonl"
        try:
            run = HARNESSES[args.harness](
                prompt, work / "repo", args.model, case.get("max_turns", 20), log, extra
            )
        except subprocess.TimeoutExpired:
            return record | {"status": "failed", "reason": "the agent run timed out"}
    if run.get("exit_code") != 0 or run.get("completed_successfully") is not True:
        return record | {
            "status": "failed",
            "reason": "harness exited unsuccessfully or has no successful terminal event",
            "exit_code": run.get("exit_code"),
            "answer": run.get("answer", ""),
        }
    checks = grade_checks(case.get("checks", {}), run)
    rubric = judge(case, run, args.judge_model)
    passed = all(c["passed"] for c in checks) and all(r["passed"] for r in rubric)
    return record | {
        "status": "passed" if passed else "failed",
        "prompt": prompt,
        "checks": checks,
        "rubric": rubric,
        "blocked_by_harness": run["blocked"],
        "answer": run["answer"],
        "cost_usd": run["cost_usd"],
        "turns": run["turns"],
        "seconds": run["seconds"],
    }


def summary_markdown(meta: dict, rows: list[dict]) -> str:
    ran = [r for r in rows if r["status"] != "skipped"]
    passed = [r for r in ran if r["status"] == "passed"]
    lines = [
        f"# Eval results {meta['stamp']}",
        "",
        f"Harness `{meta['harness']}`, agent model `{meta['model']}`, judge model `{meta['judge_model']}`, "
        f"commit `{meta['commit']}`.",
        f"Passed {len(passed)} of {len(ran)} cases that ran. Skipped {len(rows) - len(ran)}.",
        "",
        "| Case | Skill | Status | Failed items | Denied | Cost USD |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        failed = [c["check"] for c in r.get("checks", []) if not c["passed"]]
        failed += [x["item"] for x in r.get("rubric", []) if not x["passed"]]
        reason = r.get("reason", "")
        cost = f"{r['cost_usd']:.3f}" if r.get("cost_usd") is not None else ""
        lines.append(
            f"| {r['id']} | {r['skill']} | {r['status']} | {'; '.join(failed) or reason} "
            f"| {len(r.get('blocked_by_harness', []))} | {cost} |"
        )
    return "\n".join(lines) + "\n"


def eval_exit_code(rows: list[dict], allow_skips: bool = False) -> int:
    if not rows or not any(r["status"] == "passed" for r in rows):
        return 1
    return (
        0 if all(r["status"] == "passed" or (allow_skips and r["status"] == "skipped") for r in rows) else 1
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--harness", choices=sorted(HARNESSES), default="claude")
    parser.add_argument(
        "--model", default=None, help="Agent model. Default: sonnet for Claude, configured model for Codex"
    )
    parser.add_argument("--judge-model", default="sonnet")
    parser.add_argument("--case", action="append", default=[], help="Case id. Repeat for more")
    parser.add_argument("--skill", action="append", default=[], help="Skill name. Repeat for more")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--allow-skips", action="store_true", help="Allow partial coverage; an all-skipped run still fails"
    )
    args = parser.parse_args(argv)
    if args.model is None:
        args.model = "sonnet" if args.harness == "claude" else ""
    if args.jobs <= 0:
        parser.error("--jobs must be positive")

    cases = tomllib.loads(CASES.read_text())["case"]
    unknown = set(args.case) - {c["id"] for c in cases}
    if unknown:
        print(f"ERROR: unknown case: {sorted(unknown)}", file=sys.stderr)
        return 2
    selected = [
        c
        for c in cases
        if (not args.case or c["id"] in args.case) and (not args.skill or c["skill"] in args.skill)
    ]
    if not selected:
        print("ERROR: no case matches the selection", file=sys.stderr)
        return 2
    if shutil.which(args.harness) is None:
        print(f"ERROR: the program '{args.harness}' is not on PATH", file=sys.stderr)
        return 2

    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M")
    outdir = RESULTS / f"{stamp}-{args.harness}"
    (outdir / "transcripts").mkdir(parents=True)
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    )
    meta = {
        "stamp": stamp,
        "harness": args.harness,
        "model": args.model,
        "judge_model": args.judge_model,
        "commit": commit + ("-dirty" if dirty else ""),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(lambda c: run_case(c, args, outdir), selected))
    (outdir / "results.json").write_text(json.dumps({"meta": meta, "cases": rows}, indent=2))
    summary = summary_markdown(meta, rows)
    (outdir / "summary.md").write_text(summary)
    print(summary)
    print(f"Wrote {outdir}")
    return eval_exit_code(rows, args.allow_skips)


if __name__ == "__main__":
    sys.exit(main())
