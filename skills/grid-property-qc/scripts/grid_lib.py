"""Read one simulator case (EGRID + INIT) with resdata, and the shared types of the grid checks.

INIT arrays are active-sized, except PORV, which is global-sized. GridCase keeps the map from an
active index to a global index, so each check reports the right (i, j, k).
numpy and resdata are imported inside load_case, so a missing package gives exit 2, not a traceback.
"""

from __future__ import annotations

import json
import sys
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SAMPLE_CELLS = 20
REGION_ARRAYS = ("FIPNUM", "SATNUM", "PVTNUM", "EQLNUM")
FLOAT_ARRAYS = ("PORO", "PERMX", "PERMY", "PERMZ", "NTG")
Severity = Literal["error", "warning", "info"]


class InputError(Exception):
    """A missing or unusable input. The scripts exit 2."""


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    message: str
    count: int
    cells: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass(frozen=True)
class GridCase:
    base: Path
    dims: tuple[int, int, int]
    active_global: Any  # numpy int array: global index of each active cell
    porv: Any  # numpy float array, global-sized
    arrays: dict[str, Any]  # active-sized INIT arrays that exist
    volume: Any  # numpy float array, active-sized bulk volume from the grid geometry

    @property
    def n_active(self) -> int:
        return len(self.active_global)

    def ijk(self, global_index: int) -> tuple[int, int, int]:
        """1-based (i, j, k) of a global index."""
        nx, ny, _ = self.dims
        g = int(global_index)
        return g % nx + 1, (g // nx) % ny + 1, g // (nx * ny) + 1

    def global_of(self, i: int, j: int, k: int) -> int:
        """Global index of a 1-based (i, j, k). The caller checks the bounds."""
        nx, ny, _ = self.dims
        return (i - 1) + nx * ((j - 1) + ny * (k - 1))

    def sample(self, active_mask) -> list[tuple[int, int, int]]:
        """Up to SAMPLE_CELLS (i, j, k) of the active cells where the mask is true."""
        import numpy as np

        return [self.ijk(g) for g in self.active_global[np.flatnonzero(active_mask)[:SAMPLE_CELLS]]]


def resolve_base(case: str) -> Path:
    path = Path(case)
    if path.suffix.upper() in (".EGRID", ".INIT", ".DATA"):
        path = path.with_suffix("")
    for suffix in (".EGRID", ".INIT"):
        if not path.with_name(path.name + suffix).is_file():
            raise InputError(f"missing {path.name}{suffix} in {path.parent}")
    return path


def load_case(case: str) -> GridCase:
    """Read the grid and the INIT arrays. Raise InputError when PORO or PORV is missing or a size is wrong."""
    import numpy as np
    from resdata.grid import Grid
    from resdata.resfile import ResdataFile

    warnings.filterwarnings(
        "ignore", category=DeprecationWarning
    )  # after the import: resdata sets its own filter

    base = resolve_base(case)
    grid = Grid(str(base) + ".EGRID")
    init = ResdataFile(str(base) + ".INIT")
    nx, ny, nz, n_active = grid.get_dims()
    n_global = nx * ny * nz
    actnum = np.asarray(grid.export_actnum().numpy_copy())
    active_global = np.flatnonzero(actnum & 1)
    if len(active_global) != n_active:
        raise InputError(
            f"ACTNUM has {len(active_global)} matrix-active cells, the grid header says {n_active}. "
            "Dual porosity is not supported"
        )
    for name in ("PORO", "PORV"):
        if name not in init:
            raise InputError(f"{base.name}.INIT has no {name}")
    porv = np.asarray(init["PORV"][0].numpy_copy(), dtype=float)
    if len(porv) != n_global:
        raise InputError(f"PORV has {len(porv)} values, the grid has {n_global} cells")
    arrays: dict[str, Any] = {}
    for name in (*FLOAT_ARRAYS, *REGION_ARRAYS):
        if name not in init:
            continue
        values = np.asarray(init[name][0].numpy_copy())
        if len(values) != n_active:
            raise InputError(f"{name} has {len(values)} values, the grid has {n_active} active cells")
        arrays[name] = values.astype(float) if name in FLOAT_ARRAYS else values.astype(int)
    volume = np.asarray(grid.create_volume_keyword(active_size=True).numpy_copy(), dtype=float)
    return GridCase(base, (nx, ny, nz), active_global, porv, arrays, volume)


def prepare_outdir(outdir: Path, base: Path, force: bool) -> None:
    out = outdir.resolve()
    folder = base.resolve().parent
    if out == folder or folder in out.parents:
        raise InputError(f"--outdir {outdir} is in the case folder {folder}. Choose a folder outside it")
    if out.exists() and (not out.is_dir() or (any(out.iterdir()) and not force)):
        raise InputError(f"--outdir {outdir} is not empty. Choose a new folder or give --force")
    out.mkdir(parents=True, exist_ok=True)


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(f.severity == "error" for f in findings) else 0


def finding_dicts(findings: list[Finding]) -> list[dict[str, Any]]:
    return [asdict(f) | {"cells": [list(c) for c in f.cells]} for f in findings]


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def findings_markdown(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["No findings."]
    lines = ["| Code | Severity | Count | Message | Sample cells (i,j,k) |", "|---|---|---|---|---|"]
    for f in findings:
        cells = " ".join(f"({i},{j},{k})" for i, j, k in f.cells[:5])
        lines.append(f"| {f.code} | {f.severity} | {f.count} | {f.message} | {cells} |")
    return lines


def fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 2


def need_packages(*names: str) -> str | None:
    """The first package that cannot be imported, or None."""
    import importlib.util

    for name in names:
        if importlib.util.find_spec(name) is None:
            return name
    return None
