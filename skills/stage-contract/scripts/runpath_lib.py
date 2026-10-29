from __future__ import annotations

import glob
import re
from dataclasses import dataclass
from pathlib import Path


class IncompleteEnsemble(ValueError):
    pass


@dataclass(frozen=True)
class Member:
    realization: int
    runpath: Path
    data_file: Path | None


@dataclass(frozen=True)
class Dropped:
    realization: int | None
    path: Path
    reason: str


@dataclass(frozen=True)
class Ensemble:
    expected: frozenset[int] | None
    found: tuple[Member, ...]
    dropped: tuple[Dropped, ...]
    extra: tuple[Dropped, ...] = ()

    @property
    def missing(self) -> tuple[int, ...]:
        present = {member.realization for member in self.found}
        present.update(member.realization for member in self.dropped if member.realization is not None)
        return tuple(sorted(self.expected - present)) if self.expected is not None else ()

    @property
    def complete(self) -> bool:
        return not self.dropped and not self.missing

    def problems(self) -> list[str]:
        lines = [f"realization {item.realization} ({item.path}): {item.reason}" for item in self.dropped]
        if self.missing:
            lines.append(f"Missing realization folders: {list(self.missing)}")
        return lines

    def require_complete(self) -> None:
        if not self.complete:
            raise IncompleteEnsemble("\n".join(self.problems()))


def parse_realizations(spec: str) -> set[int]:
    numbers: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            raise ValueError(f"empty item in realization spec '{spec}'")
        first, sep, last = part.partition("-")
        if not first.isdigit() or (sep and not last.isdigit()):
            raise ValueError(f"'{part}' is not a number or a range 'a-b'")
        low, high = int(first), int(last) if sep else int(first)
        if high < low:
            raise ValueError(f"range '{part}' ends before it starts")
        numbers.update(range(low, high + 1))
    return numbers


def expected_set(count: int | None, spec: str | None) -> set[int] | None:
    if spec is not None:
        return parse_realizations(spec)
    if count is not None:
        if count <= 0:
            raise ValueError("--expected must be positive")
        return set(range(count))
    return None


def realization_number(path: Path) -> int | None:
    for part in reversed(path.parts):
        match = re.fullmatch(r"realization-(\d+)", part)
        if match:
            return int(match.group(1))
    return None


def find_runpaths(pattern: str) -> list[tuple[int, Path]]:
    return sorted(
        (real, path)
        for item in glob.glob(pattern)
        if (path := Path(item)).is_dir() and (real := realization_number(path)) is not None
    )


def data_file(runpath: Path, eclbase: str | tuple[str, str] | None, real: int) -> tuple[Path | None, str]:
    if isinstance(eclbase, tuple):
        folder, name = eclbase
        base = str(Path(folder) / name) if folder else name
    else:
        base = eclbase
    base = base.replace("<IENS>", str(real)) if base else None
    if base and ("/" in base or "\\" in base):
        file = runpath / f"{base}.DATA"
        return (file, "") if file.is_file() else (None, f"no {base}.DATA in the runpath")
    pattern = f"{base}.DATA" if base else "*.DATA"
    for folder in ("eclipse/model", "model", ""):
        hits = sorted(path for path in (runpath / folder).glob(pattern) if path.is_file())
        if len(hits) == 1:
            return hits[0], ""
        if hits:
            names = [hit.name for hit in hits]
            return None, f"no unique .DATA file in {folder or 'the runpath'} {names}. Give --eclbase"
    return None, f"no {pattern} in eclipse/model, model or the runpath"


def read_ensemble(
    pattern: str,
    eclbase: str | tuple[str, str] | None = None,
    expected: set[int] | frozenset[int] | None = None,
    need_data_file: bool = True,
) -> Ensemble:
    found: list[Member] = []
    dropped: list[Dropped] = []
    extra: list[Dropped] = []
    paths = sorted(Path(item) for item in glob.glob(pattern) if Path(item).is_dir())
    for path in paths:
        real = realization_number(path)
        if real is None:
            dropped.append(Dropped(None, path, "no realization number"))
            continue
        if expected is not None and real not in expected:
            extra.append(Dropped(real, path, "not in the expected set"))
            continue
        file, reason = data_file(path, eclbase, real) if need_data_file else (None, "")
        if reason:
            dropped.append(Dropped(real, path, reason))
        else:
            found.append(Member(real, path, file))
    return Ensemble(
        frozenset(expected) if expected is not None else None,
        tuple(sorted(found, key=lambda member: (member.realization, member.runpath))),
        tuple(dropped),
        tuple(extra),
    )
