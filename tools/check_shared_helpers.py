from __future__ import annotations

import ast
import sys
from pathlib import Path

CONTRACT = "skills/stage-contract/scripts/contract_lib.py"
RUNPATH = "skills/stage-contract/scripts/runpath_lib.py"
OWNERS = dict.fromkeys(("load_contract", "resolve_eclbase", "split_eclbase"), CONTRACT)
OWNERS.update(
    dict.fromkeys(
        (
            "find_data_file",
            "data_file",
            "realization_number",
            "parse_realizations",
            "parse_reals",
            "expected_set",
            "find_runpaths",
        ),
        RUNPATH,
    )
)


def scripts(root: Path):
    for folder in (root / "skills", root / "tools"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.is_symlink() or path.name in {"__pycache__", "plugins"}:
                continue
            if path.is_dir():
                yield from walk(path)
            elif path.suffix == ".py":
                yield path


def walk(folder: Path):
    for path in sorted(folder.iterdir()):
        if path.is_symlink() or path.name in {"__pycache__", "plugins"}:
            continue
        if path.is_dir():
            yield from walk(path)
        elif path.suffix == ".py":
            yield path


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    count = 0
    hits = []
    for path in scripts(root):
        count += 1
        relative = path.relative_to(root).as_posix()
        for node in ast.parse(path.read_text(), filename=str(path)).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in OWNERS:
                owner = OWNERS[node.name]
                if relative != owner:
                    hits.append(f"{relative}:{node.lineno}: {node.name} is defined in {owner}; import it")
    if hits:
        print("\n".join(hits))
        return 1
    print(f"shared helpers: {count} scripts scanned, 0 redefinitions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
