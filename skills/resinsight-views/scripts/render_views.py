#!/usr/bin/env python3
"""Render a ViewSpec with ResInsight: one PNG per view, a manifest, and a .rsp project a person can open.

Run with the Python that has rips (contract resinsight.python). The numbers in the arrays come from an
earlier step (resdata, xtgeo); this script only shows them. Arrays are imported as GRDECL input properties,
so the saved project still shows them when it is opened later.

Usage: render_views.py --spec spec.json [--bin <ResInsight or launcher>] [--contract ... --profile ...]
Exit: 0 all views rendered, 1 a view failed (the rest still render), 2 bad spec or missing input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE.parents[1] / "resinsight-session" / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import grdecl  # noqa: E402
import ri_session as ri  # noqa: E402
from view_spec import SpecError, ViewSpec, load_spec, resolve_time_step  # noqa: E402


def step_dates(case) -> list[str]:
    return [f"{t.year:04d}-{t.month:02d}-{t.day:02d}" for t in case.time_steps()]


def import_arrays(session, case, spec: ViewSpec) -> dict[str, str]:
    dims = case.grid(0).dimensions()
    cells = dims.i * dims.j * dims.k
    imported = {}
    for array in spec.arrays:
        if not array.file.is_file():
            raise SpecError(f"array {array.name}: file not found: {array.file}")
        try:
            keyword, _ = grdecl.read_grdecl(array.file, expected=cells)
        except grdecl.GrdeclError as error:
            raise SpecError(f"array {array.name}: {error}") from None
        if keyword != array.name:
            raise SpecError(f"array {array.name}: the file has keyword {keyword}")
        case.import_properties(file_names=[str(array.file)])
        if array.name not in session.properties(case, "INPUT_PROPERTY"):
            raise ri.RiError(f"array {array.name}: ResInsight did not import {array.file}")
        imported[array.name] = str(array.file)
    return imported


def side_camera(direction: str, distance: float) -> list[float]:
    """A row-major world-to-camera matrix that looks north or east from `distance` (measured layout:
    the top view is the identity with the distance at index 11, and the model centre is the origin)."""
    if direction == "north":  # camera x = east, camera y = up, the viewer is south
        rows = [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, -distance]
    elif direction == "east":  # camera x = north, the viewer is west
        rows = [0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, -distance]
    else:
        raise SpecError(f"unknown camera {direction}")
    return [float(v) for v in rows] + [0.0, 0.0, 0.0, 1.0]


def apply_camera(view, camera: str, zoom: float = 1.0) -> None:
    if camera == "top":
        return
    matrix = list(view.camera_matrix)
    if len(matrix) != 16 or matrix[11] >= 0:
        raise ri.RiError(f"unexpected camera matrix {matrix}: cannot make a {camera} view")
    view.camera_matrix = side_camera(camera, -matrix[11] / zoom)
    view.update()


def apply_range(session, view, spec_view, dims) -> None:
    """An IJK range filter on the view (1-based, inclusive). Range filters do work through rips."""
    if spec_view.range is None:
        return
    filters = view.descendants(session.rips.CellFilterCollection)
    if not filters:
        raise ri.RiError("the view has no cell filter collection")
    cell_range = filters[0].add_new_object(session.rips.CellRangeFilter)
    cell_range.name = f"{spec_view.name} range"
    for axis, size in (("i", dims.i), ("j", dims.j), ("k", dims.k)):
        start, count = spec_view.range.bounds(axis, size)
        setattr(cell_range, f"start_index_{axis}", start)
        setattr(cell_range, f"cell_count_{axis}", count)
    cell_range.update()


def render_one(session, case, spec: ViewSpec, spec_view, dates: list[str]) -> dict:
    prop = spec_view.property
    if prop.result_type != "FLOW_DIAGNOSTICS" and prop.name not in session.properties(case, prop.result_type):
        raise SpecError(f"{spec_view.name}: {prop} is not in the case")
    if dates:
        step = resolve_time_step(spec_view.time_step, dates)
    elif spec_view.time_step in (0, "first", "last"):
        step = None  # a grid without restart: setTimeStep(0) fails (measured), so set none
    else:
        raise SpecError(f"{spec_view.name}: time_step {spec_view.time_step} but the case has no time steps")
    view = case.create_view()
    if prop.result_type == "FLOW_DIAGNOSTICS":
        flow = spec_view.flow or {}
        injectors, producers = list(flow.get("injectors", [])), list(flow.get("producers", []))
        mode = "FLOW_TR_BY_SELECTION" if injectors or producers else "FLOW_TR_INJ_AND_PROD"
        view.apply_flow_diagnostics_cell_result(
            result_variable=prop.name, selection_mode=mode, injectors=injectors, producers=producers
        )
    else:
        view.apply_cell_result(result_type=prop.result_type, result_variable=prop.name)
    if step is not None:
        view.set_time_step(step)
    if spec_view.z_scale is not None:
        view.grid_z_scale = spec_view.z_scale
        view.update()
    apply_range(session, view, spec_view, case.grid(0).dimensions())
    apply_camera(view, spec_view.camera, spec_view.zoom)
    png = session.snapshot(view, spec.out / "views", f"{spec_view.name}_", spec.width, spec.height)
    return {
        "name": spec_view.name,
        "property": str(prop),
        "time_step": step,
        "date": None if step is None else dates[step],
        "png": str(png),
        "camera": spec_view.camera,
        "range": None if spec_view.range is None else {k: v for k, v in vars(spec_view.range).items() if v},
    }


def render(
    spec: ViewSpec, executable: Path, expected_version: str | None = None, rips=None, launcher=None
) -> dict:
    """rips and launcher: the Session injection points (tests). Default: import rips, launch ResInsight."""
    spec.out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    manifest: dict = {
        "skill": "resinsight-views",
        "title": spec.title,
        "case": str(spec.case),
        "notes": list(spec.notes),
        "views": [],
        "failed": [],
        "rule": "Pictures only. The numbers come from the step that wrote the arrays, not from ResInsight.",
    }
    config = ri.SessionConfig(
        executable=executable, mode="gui", expected_version=expected_version, workdir=spec.out / "ri"
    )
    with ri.Session(config, rips=rips, launcher=launcher or ri.launch_process) as session:
        manifest["resinsight_version"] = session.version
        case = session.load_case(spec.case)
        manifest["arrays"] = import_arrays(session, case, spec)
        dates = step_dates(case)
        for spec_view in spec.views:
            try:
                manifest["views"].append(render_one(session, case, spec, spec_view, dates))
            except (ri.RiError, SpecError, session.rips.RipsError) as error:
                manifest["failed"].append(
                    {"name": spec_view.name, "error": f"{type(error).__name__}: {error}"}
                )
        if spec.project:
            manifest["project"] = str(session.save_project(spec.out / "views.rsp"))
    manifest["seconds"] = round(time.perf_counter() - started, 2)
    (spec.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _executable(args) -> tuple[Path | None, str | None]:
    if args.bin:
        return Path(args.bin), None
    block = {}
    if args.contract:
        sys.path.insert(0, str(HERE.parents[1] / "stage-contract" / "scripts"))
        import contract_lib  # noqa: PLC0415

        block = dict(
            contract_lib.load_or_exit(args.contract, args.profile, require_project=False).get("resinsight")
            or {}
        )
    binary = block.get("bin") or os.environ.get("RESINSIGHT_EXECUTABLE")
    return (Path(binary) if binary else None), (block.get("version") or None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument(
        "--bin", default=None, help="ResInsight or launcher (default: contract, RESINSIGHT_EXECUTABLE)"
    )
    parser.add_argument("--contract", type=Path, default=None)
    parser.add_argument("--profile", default=None)
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        if not spec.case.is_file():
            raise SpecError(f"case not found: {spec.case}")
    except SpecError as error:
        sys.stderr.write(f"error: spec: {error}\n")
        return 2
    executable, version = _executable(args)
    if executable is None:
        sys.stderr.write(
            "error: no ResInsight: pass --bin, set contract resinsight.bin, or RESINSIGHT_EXECUTABLE\n"
        )
        return 2
    try:
        manifest = render(spec, executable, version)
    except ri.RipsMissing as error:
        sys.stderr.write(f"error: {error}\n")
        return 2
    except SpecError as error:
        sys.stderr.write(f"error: spec: {error}\n")
        return 2
    except ri.RiError as error:
        sys.stderr.write(f"error: {type(error).__name__}: {error}\n")
        return 1
    print(
        json.dumps(
            {k: manifest[k] for k in ("views", "failed", "seconds")} | {"project": manifest.get("project")},
            indent=2,
        )
    )
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
