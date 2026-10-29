#!/usr/bin/env python3
"""Check the localization settings of an ERT 26 config. Read-only.

The config is loaded the way a run loads it (ErtConfig.with_plugins(get_site_plugins())). The report has:
- each FIELD, SURFACE and GEN_KW parameter and its update strategy (GLOBAL, ADAPTIVE, DISTANCE);
- with DISTANCE: the observations with and without a location, per key family. ERT 26 excludes the
  observations without a location from a distance update (error when none has a location, warning
  when some have none);
- each RADIUS against the default of 3000 m, the extent of the field and its cell size;
- a location outside the field box;
- DISTANCE on GEN_KW (ERT rejects it) and the legacy STD_ENKF LOCALIZATION and DISTANCE_LOCALIZATION switches.

Exit 0 no error and no warning, 1 an error or a warning, 2 ERT or its plugins cannot load.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import sys
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from localize_obs import key_family  # noqa: E402

DEFAULT_RADIUS = 3000.0  # ert.config._observations.DEFAULT_LOCALIZATION_RADIUS
LEGACY = {
    "LOCALIZATION has been removed": "ANALYSIS_SET_VAR STD_ENKF LOCALIZATION is the pre-ERT-26 switch."
    " ERT 26 maps it to ADAPTIVE for every type. Write ANALYSIS_SET_VAR PARAMETERS <TYPE> <STRATEGY>",
    "DISTANCE_LOCALIZATION is deprecated": "ANALYSIS_SET_VAR STD_ENKF DISTANCE_LOCALIZATION is deprecated."
    " Write ANALYSIS_SET_VAR PARAMETERS FIELD DISTANCE (and SURFACE), GEN_KW ADAPTIVE",
}


@dataclass(frozen=True)
class Finding:
    severity: str  # error, warning, note
    message: str

    def __str__(self) -> str:
        return f"{self.severity:<8} {self.message}"


@dataclass(frozen=True)
class Box:
    """The 2D box of one FIELD or SURFACE parameter in its local coordinates."""

    name: str
    xlength: float
    ylength: float
    cell: float
    origin: tuple[float, float]
    rotation: float

    @property
    def diagonal(self) -> float:
        return math.hypot(self.xlength, self.ylength)


def family_of(observation) -> str:
    key = getattr(observation, "key", None)
    if key:
        return key_family(str(key))[0]
    return str(getattr(observation, "type", type(observation).__name__)).upper()


def box_of(config) -> Box | None:
    from ert.config import Field, SurfaceConfig

    if isinstance(config, Field):
        e = config.ertbox_params
        if None in (e.xlength, e.ylength, e.xinc, e.yinc, e.origin):
            return None
        return Box(config.name, e.xlength, e.ylength, min(e.xinc, e.yinc), e.origin, e.rotation_angle or 0.0)
    if isinstance(config, SurfaceConfig):
        return Box(
            config.name,
            config.ncol * config.xinc,
            config.nrow * config.yinc,
            min(config.xinc, config.yinc),
            (config.xori, config.yori),
            config.rotation,
        )
    return None


def radius_findings(radii: list[float], boxes: list[Box]) -> list[Finding]:
    """Pure: the radius against the default, the extent and the cell size of each box."""
    out = []
    count = Counter(radii)
    if count[DEFAULT_RADIUS]:
        out.append(
            Finding(
                "warning",
                f"{count[DEFAULT_RADIUS]} observation(s) have RADIUS {DEFAULT_RADIUS:g} m, the ERT default"
                " (RADIUS not given). The radius is a modelling choice: set it and sweep it (radius_sweep.py)",
            )
        )
    if not radii:
        return out
    low, high = min(radii), max(radii)
    for box in boxes:
        if high > box.diagonal:
            out.append(
                Finding(
                    "warning",
                    f"RADIUS up to {high:g} m is larger than the extent of {box.name}"
                    f" ({box.xlength:g} x {box.ylength:g} m, diagonal {box.diagonal:.0f} m)."
                    " The taper is then close to 1 everywhere, and the update is close to GLOBAL",
                )
            )
        if low < box.cell:
            out.append(
                Finding(
                    "warning",
                    f"RADIUS {low:g} m is smaller than the cell size of {box.name} ({box.cell:g} m)."
                    " An observation then updates its own cell at most",
                )
            )
        out.append(
            Finding(
                "note",
                f"{box.name}: RADIUS {low:g}-{high:g} m = {low / box.cell:.1f}-{high / box.cell:.1f} cells"
                f" of {box.cell:g} m; the field is {box.xlength:g} x {box.ylength:g} m",
            )
        )
    return out


def outside(box: Box, points: list[tuple[str, float, float]]) -> list[str]:
    """Names of the points outside the box, in the local (rotated) coordinates of the box."""
    import numpy as np
    from ert.field_utils import transform_positions_to_local_field_coordinates

    if not points:
        return []
    east = np.array([p[1] for p in points], dtype=float)
    north = np.array([p[2] for p in points], dtype=float)
    x, y = transform_positions_to_local_field_coordinates(box.origin, box.rotation, east, north)
    return [
        name
        for (name, _, _), u, v in zip(points, x, y, strict=True)
        if not (0 <= u <= box.xlength and 0 <= v <= box.ylength)
    ]


def lint(config_path: Path, runtime_plugins) -> list[Finding]:
    from ert.config import ConfigValidationError, ErtConfig, Field, GenKwConfig, SurfaceConfig
    from ert.config.parameter_config import LocalizationType

    findings: list[Finding] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            with contextlib.chdir(config_path.parent):
                config = ErtConfig.with_plugins(runtime_plugins).from_file(config_path.name)
        except ConfigValidationError as error:
            for message in error.messages():
                text = " ".join(message.split())
                if "DISTANCE strategy is not supported for GEN_KW" in text:
                    text += ". DISTANCE needs a grid: use it for FIELD or SURFACE parameters"
                findings.append(Finding("error", text))
            config = None
    for warning in caught:
        for fragment, advice in LEGACY.items():
            if fragment in str(warning.message):
                findings.append(Finding("warning", advice))
    if config is None:
        return findings

    params = config.ensemble_config.parameter_configs
    distance, boxes = [], []
    for name, cfg in params.items():
        kind = {Field: "FIELD", SurfaceConfig: "SURFACE", GenKwConfig: "GEN_KW"}.get(
            type(cfg), type(cfg).__name__
        )
        strategy = cfg.update_strategy.name if cfg.update_strategy is not None else "not updated"
        findings.append(Finding("note", f"parameter {name} ({kind}): {strategy}"))
        if cfg.update_strategy == LocalizationType.DISTANCE:
            distance.append(name)
            if (box := box_of(cfg)) is not None:
                boxes.append(box)
    if not distance:
        findings.append(Finding("note", "no parameter uses DISTANCE. The LOCALIZATION blocks are not used"))
        return findings

    located, unlocated, radii, points = Counter(), Counter(), [], []
    for obs in config.observation_declarations:
        shape_id = getattr(obs, "shape_id", None)
        shape = config.shape_registry.get(shape_id) if shape_id is not None else None
        if shape is None:
            unlocated[family_of(obs)] += 1
            continue
        located[family_of(obs)] += 1
        radius = getattr(shape, "radius", None)
        if radius is not None:
            radii.append(float(radius))
        if getattr(shape, "east", None) is not None:
            points.append((obs.name, float(shape.east), float(shape.north)))
    n_located, n_unlocated = sum(located.values()), sum(unlocated.values())
    names = ", ".join(distance)
    findings.append(
        Finding(
            "note", f"DISTANCE on {names}: {n_located} observation(s) with a location, {n_unlocated} without"
        )
    )
    if n_located == 0:
        findings.append(
            Finding(
                "error",
                f"DISTANCE on {names}, but no observation has a LOCALIZATION block. Run localize_obs.py",
            )
        )
    if n_unlocated:
        families = ", ".join(f"{k} {v}" for k, v in sorted(unlocated.items()))
        findings.append(
            Finding(
                "warning",
                f"{n_unlocated} observation(s) have no location ({families}). ERT excludes them from the"
                f" DISTANCE update of {names}. The GLOBAL and ADAPTIVE updates still use them",
            )
        )
    findings += radius_findings(radii, boxes)
    for box in boxes:
        if away := outside(box, points):
            findings.append(
                Finding(
                    "warning",
                    f"{len(away)} observation location(s) are outside the box of {box.name}, for example {away[0]}",
                )
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("config", type=Path, help="The .ert file")
    args = parser.parse_args(argv)
    if not args.config.is_file():
        print(f"ERROR: {args.config} not found", file=sys.stderr)
        return 2
    try:
        from ert.plugins import get_site_plugins
    except ImportError as error:
        print(f"ERROR: cannot import ert ({error}). Use the Komodo Python", file=sys.stderr)
        return 2
    try:
        runtime_plugins = get_site_plugins()
    except Exception as error:  # a plugin can fail while it loads
        first = str(error).splitlines()[0] if str(error) else ""
        print(f"ERROR: ERT could not load its plugins ({type(error).__name__}: {first})", file=sys.stderr)
        return 2
    findings = lint(args.config.resolve(), runtime_plugins)
    for finding in findings:
        print(finding)
    errors = sum(f.severity == "error" for f in findings)
    warns = sum(f.severity == "warning" for f in findings)
    print(f"localization lint: {errors} error(s), {warns} warning(s)")
    return 1 if errors or warns else 0


if __name__ == "__main__":
    sys.exit(main())
