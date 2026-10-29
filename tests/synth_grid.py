"""Tiny synthetic Eclipse-format cases (EGRID, INIT, optional summary) written with resdata.

For tests that need a real grid file: grid-aware checks and real-ResInsight smoke runs.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path


def make_case(
    folder: Path,
    base: str = "SYNTH",
    dims: tuple[int, int, int] = (4, 3, 2),
    inactive: tuple[int, ...] = (),
    poro: float | Sequence[float] = 0.2,
    permx: float | Sequence[float] = 100.0,
    summary: bool = False,
) -> Path:
    """Write BASE.EGRID and BASE.INIT (PORO, PERMX active-sized; PORV global-sized). Return the EGRID path.

    poro and permx: one value for all cells, or one value for each active cell.
    """
    import numpy as np
    from resdata import ResDataType
    from resdata.grid import GridGenerator
    from resdata.resfile import FortIO, ResdataKW, openFortIO

    folder.mkdir(parents=True, exist_ok=True)
    nx, ny, nz = dims
    n = nx * ny * nz
    actnum = [0 if g in inactive else 1 for g in range(n)]
    grid = GridGenerator.create_rectangular(dims, (100.0, 100.0, 10.0), actnum=actnum)
    egrid = folder / f"{base}.EGRID"
    grid.save_EGRID(str(egrid))
    n_active = grid.get_num_active()
    poro_a, permx_a = (np.broadcast_to(np.asarray(v, dtype=float), (n_active,)) for v in (poro, permx))
    porv = np.zeros(n)
    porv[np.flatnonzero(actnum)] = 1e5 * poro_a
    # ResInsight needs the header arrays: dims, active count, phases (3 = oil+water), simulator, start date.
    intehead = [0] * 411
    intehead[1], intehead[2] = 201802, 1  # version, unit system METRIC
    intehead[8], intehead[9], intehead[10], intehead[11] = nx, ny, nz, n_active
    intehead[14], intehead[94] = 3, 100
    intehead[64], intehead[65], intehead[66] = 1, 1, 2020
    with openFortIO(str(folder / f"{base}.INIT"), mode=FortIO.WRITE_MODE) as fio:
        for name, values, rd_type in (
            ("INTEHEAD", intehead, ResDataType.RD_INT),
            ("LOGIHEAD", [False] * 121, ResDataType.RD_BOOL),
            ("DOUBHEAD", [0.0] * 229, ResDataType.RD_DOUBLE),
        ):
            kw = ResdataKW(name, len(values), rd_type)
            for index, value in enumerate(values):
                kw[index] = value
            kw.fwrite(fio)
        for name, values, size in (
            ("PORV", porv, n),
            ("PORO", poro_a, n_active),
            ("PERMX", permx_a, n_active),
            ("PERMY", permx_a, n_active),
            ("PERMZ", permx_a / 10, n_active),
        ):
            kw = ResdataKW(name, size, ResDataType.RD_FLOAT)
            kw.numpy_view()[:] = np.asarray(values, dtype=np.float32)
            kw.fwrite(fio)
    if summary:
        from resdata.summary import Summary

        writer = Summary.writer(str(folder / base), dt.datetime(2020, 1, 1), nx, ny, nz)
        writer.add_variable("FOPT", unit="SM3")
        for step, days in enumerate((0.0, 31.0, 60.0, 91.0)):
            tstep = writer.add_t_step(step + 1, days)
            tstep["FOPT"] = 1000.0 * days
        writer.fwrite()
    return egrid


def write_restart(
    folder: Path,
    base: str,
    dims: tuple[int, int, int],
    n_active: int,
    steps: list[tuple[dt.date, dict[str, list[float]]]],
) -> Path:
    """Write BASE.UNRST: one report step per (date, {keyword: active-cell values}). Return the path."""
    import numpy as np
    from resdata import ResDataType
    from resdata.resfile import FortIO, ResdataKW, openFortIO

    nx, ny, nz = dims
    path = folder / f"{base}.UNRST"
    with openFortIO(str(path), mode=FortIO.WRITE_MODE) as fio:
        for number, (date, arrays) in enumerate(steps):
            seqnum = ResdataKW("SEQNUM", 1, ResDataType.RD_INT)
            seqnum[0] = number
            seqnum.fwrite(fio)
            intehead = ResdataKW("INTEHEAD", 411, ResDataType.RD_INT)
            head = {1: 201802, 2: 1, 8: nx, 9: ny, 10: nz, 11: n_active, 14: 3, 94: 100}
            head |= {64: date.day, 65: date.month, 66: date.year}
            for index, value in head.items():
                intehead[index] = value
            intehead.fwrite(fio)
            for name, rd_type, size in (
                ("LOGIHEAD", ResDataType.RD_BOOL, 121),
                ("DOUBHEAD", ResDataType.RD_DOUBLE, 229),
            ):
                ResdataKW(name, size, rd_type).fwrite(fio)
            for name, values in arrays.items():
                kw = ResdataKW(name, len(values), ResDataType.RD_FLOAT)
                kw.numpy_view()[:] = np.asarray(values, dtype=np.float32)
                kw.fwrite(fio)
    return path


def write_summary(
    folder: Path, base: str, dims: tuple[int, int, int], rates: dict[str, float], days=(0.0, 31.0, 60.0)
) -> Path:
    """Write BASE.SMSPEC and BASE.UNSMRY with constant well rates, for example {"WWIR:INJ1": 100.0}."""
    from resdata.summary import Summary

    writer = Summary.writer(str(folder / base), dt.datetime(2020, 1, 1), *dims)
    for key in rates:
        vector, _, well = key.partition(":")
        writer.add_variable(vector, wgname=well, unit="SM3/DAY")
    for step, day in enumerate(days):
        tstep = writer.add_t_step(step + 1, day)
        for key, value in rates.items():
            tstep[key] = value
    writer.fwrite()
    return folder / f"{base}.SMSPEC"
