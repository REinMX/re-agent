"""GRDECL text read and write for whole-grid arrays (one value per cell, i fastest). Standard library only.

Reads the files that ResInsight exports too: those have a second header line (for example the tracer
name of a flow diagnostics result) before the numbers.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from pathlib import Path

_REPEAT = re.compile(r"^(\d+)\*(.+)$")


class GrdeclError(ValueError):
    pass


def write_grdecl(path: Path, keyword: str, values: Sequence[float], per_line: int = 6) -> Path:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,7}", keyword):
        raise GrdeclError(f"keyword must be 1-8 upper-case letters, digits or _: {keyword!r}")
    lines = [f"-- written by resinsight-views grdecl.py, {len(values)} values", keyword]
    for start in range(0, len(values), per_line):
        chunk = values[start : start + per_line]
        for value in chunk:
            if not math.isfinite(float(value)):
                raise GrdeclError(f"{keyword}: value {value!r} at index {start} is not finite")
        lines.append(" ".join(f"{float(v):.7g}" for v in chunk))
    lines.append("/")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_grdecl(path: Path, expected: int | None = None) -> tuple[str, list[float]]:
    """Return (keyword, values). Expand N*value. Ignore -- comments and extra header lines."""
    keyword = None
    values: list[float] = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("--", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        if keyword is None:
            keyword = line
            continue
        if not values and not _numeric(tokens[0]):
            continue  # a second header line (ResInsight: tracer or case name)
        for token in tokens:
            if token == "/":
                return _check(path, keyword, values, expected)
            match = _REPEAT.match(token)
            count, text = (int(match.group(1)), match.group(2)) if match else (1, token)
            try:
                value = float(text)
            except ValueError:
                raise GrdeclError(f"{path}: not a number: {token!r}") from None
            if not math.isfinite(value):
                raise GrdeclError(f"{path}: {keyword} value {token!r} at index {len(values)} is not finite")
            values.extend([value] * count)
    if keyword is None:
        raise GrdeclError(f"{path}: no keyword")
    return _check(path, keyword, values, expected)


def _numeric(token: str) -> bool:
    try:
        float(_REPEAT.sub(r"\2", token))
        return True
    except ValueError:
        return False


def _check(path, keyword, values, expected):
    if expected is not None and len(values) != expected:
        raise GrdeclError(f"{path}: {keyword} has {len(values)} values, expected {expected}")
    return str(keyword), values
