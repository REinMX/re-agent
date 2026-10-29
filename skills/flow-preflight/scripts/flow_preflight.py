#!/usr/bin/env python3
"""Print a Flow proposal. Never execute it or create simulator outputs."""

import argparse
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from doctor import read_runtime_contract  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=Path("site.contract.yml"))
    parser.add_argument("--profile", help="Contract profile; otherwise RE_AGENT_PROFILE or active_profile")
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--parsing-strictness", choices=("normal", "high", "low"))
    args = parser.parse_args(argv)
    try:
        contract = read_runtime_contract(args.contract, args.profile)
        for key in ("simulator_version", "queue_system", "scratch_root", "output_root"):
            value = contract.get(key)
            if not isinstance(value, str) or not value.strip() or any(c in value for c in "<>$"):
                raise ValueError(f"{key}: missing literal contract value")
        if contract["queue_system"] not in {"LOCAL", "LSF"}:
            raise ValueError("queue_system: expected LOCAL or LSF")
        if not args.deck.is_file() or args.deck.suffix.upper() != ".DATA":
            raise ValueError("deck: an existing .DATA file is required")
        root = Path(contract["output_root"])
        scratch = Path(contract["scratch_root"])
        if not root.is_absolute() or not scratch.is_absolute():
            raise ValueError("output_root and scratch_root must be absolute")
        out = (args.output_dir or root / args.deck.stem).resolve()
        if not out.is_relative_to(root.resolve()) or out == root.resolve():
            raise ValueError("output directory must be below output_root")
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise ValueError("output directory must be empty or new")
        ancestor = out.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        if not ancestor.is_dir() or not os.access(ancestor, os.W_OK):
            raise ValueError("output directory parent is not writable")
        cmd = shlex.split(contract.get("flow_cmd") or "flow")
        extra = shlex.split(contract.get("flow_extra_args") or "")
        if not cmd or any(a.startswith("--output-dir") for a in cmd):
            raise ValueError("flow_cmd: command is empty or overrides output-dir")
        if any(not a.startswith("--") or a.startswith("--output-dir") for a in extra):
            raise ValueError("flow_extra_args: use --option=value; output-dir is reserved")
        cmd += [f"--output-dir={out}"]
        if args.parsing_strictness:
            cmd += [f"--parsing-strictness={args.parsing_strictness}"]
        cmd += extra + [str(args.deck.resolve())]
    except (OSError, ValueError, SyntaxError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(shlex.join(cmd))
    print(f"Version: {contract['simulator_version']}; queue: {contract['queue_system']}")
    print(f"Planned output directory: {out}")
    print("Planned outputs (deck/options dependent): .PRT, .SMSPEC, .UNSMRY, .INIT, .UNRST")
    print("Runtime estimate: unknown; no simulator or version check was performed.")
    print("Nothing was executed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
