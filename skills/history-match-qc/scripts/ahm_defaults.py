"""Defaults of an assisted history match (AHM), as typed constants. Standard library only.

The numbers come from the drogon-lite sweeps (30x20x5 grid, 5 wells, ES-MDA 4,2,1). Each constant names
its basis: measured on drogon-lite, or inferred from it for other fields.
- 100 members: truth coverage 0.99 against 0.90 with 50 (split fault, 3 seeds each). Measured.
- 3 seeds: one drogon-lite seed missed the west contact in every setup. Fewer seeds hide that. Measured.
- Localization radius 500 m: best near-well permeability over 250, 500, 1000, 2000 m and none (3 seeds).
  Measured. The scale rule, 1.25 times the median nearest-well spacing, is inferred from that one model
  (median spacing 403 m), so check it with a radius sweep (hm-localization) on each real field.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum


class Basis(StrEnum):
    MEASURED = "measured"
    INFERRED = "inferred"


@dataclass(frozen=True)
class Default:
    value: float
    basis: Basis
    source: str


MEMBERS = Default(100, Basis.MEASURED, "drogon-lite: truth coverage 0.99 with 100 members, 0.90 with 50")
MIN_SEEDS = Default(3, Basis.MEASURED, "drogon-lite: one of 3 seeds missed the west contact in every setup")
DROGON_LITE_RADIUS_M = Default(500.0, Basis.MEASURED, "drogon-lite radius sweep, 3 seeds per radius")
RADIUS_PER_WELL_SPACING = Default(1.25, Basis.INFERRED, "500 m over the 403 m median nearest-well spacing")
RADIUS_SWEEP_FACTORS = (0.5, 1.0, 2.0)  # sweep these multiples of the guided radius before you trust it


@dataclass(frozen=True)
class RadiusGuidance:
    radius_m: float
    median_spacing_m: float
    sweep_m: tuple[float, ...]
    basis: Basis
    note: str


def median_nearest_spacing(wells: Sequence[tuple[float, float]]) -> float:
    """Median over the wells of the map distance to the nearest other well."""
    if len(wells) < 2:
        raise ValueError("need at least 2 well locations")
    nearest = []
    for n, (x, y) in enumerate(wells):
        nearest.append(min(math.hypot(x - u, y - v) for m, (u, v) in enumerate(wells) if m != n))
    nearest.sort()
    mid = len(nearest) // 2
    return nearest[mid] if len(nearest) % 2 else 0.5 * (nearest[mid - 1] + nearest[mid])


def radius_guidance(wells: Sequence[tuple[float, float]]) -> RadiusGuidance:
    """A starting localization radius for a field from its well locations (x, y in metres)."""
    spacing = median_nearest_spacing(wells)
    if not spacing > 0:
        raise ValueError("the wells share one location")
    radius = RADIUS_PER_WELL_SPACING.value * spacing
    return RadiusGuidance(
        radius_m=radius,
        median_spacing_m=spacing,
        sweep_m=tuple(f * radius for f in RADIUS_SWEEP_FACTORS),
        basis=Basis.INFERRED,
        note="Inferred from one model (drogon-lite). Confirm with a radius sweep before you use it",
    )


@dataclass(frozen=True)
class SeedVerdict:
    seeds: int
    claim_ready: bool
    reason: str


def seed_verdict(seeds: Iterable[int]) -> SeedVerdict:
    """A claim about an AHM setup needs MIN_SEEDS distinct seeds."""
    distinct = len(set(seeds))
    need = int(MIN_SEEDS.value)
    if distinct >= need:
        return SeedVerdict(distinct, True, f"{distinct} seeds")
    return SeedVerdict(
        distinct, False, f"{distinct} of {need} seeds. Report the result as one sample, not a claim"
    )
