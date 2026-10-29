"""ViewSpec: one typed description of the ResInsight views to render. Standard library only.

A spec is JSON. Unknown keys are errors, so a typo cannot silently drop a filter.
{
  "case": "/abs/BASE.EGRID",
  "out": "/abs/output/folder",
  "arrays": [{"name": "SDRATIO", "file": "/abs/sdratio.grdecl"}],     # whole-grid GRDECL, made by a numbers step
  "views": [
    {"name": "swat_last", "property": "DYNAMIC_NATIVE:SWAT", "time_step": "last",
     "range": {"k": [3, 3]}, "z_scale": 5}
  ],
  "width": 1400, "height": 1000, "project": true
}
Property: "TYPE:NAME". An array is shown with "INPUT_PROPERTY:<name>". time_step: an index, "first",
"last", or a date "YYYY-MM-DD" that must be a report step. Range bounds are 1-based and inclusive.
camera: "top" (default), "north" (look north: an I-K section) or "east" (look east: a J-K section).
A side camera starts at the whole-model distance; "zoom" (1 to 20) moves it closer. Use the side
cameras with a one-row or one-column range.
There is no value filter: a CellPropertyFilter added through rips shows its label but filters nothing
(measured, 2026.09.1). To show cells by value, write a 0/1 indicator array in the numbers step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RESULT_TYPES = ("STATIC_NATIVE", "DYNAMIC_NATIVE", "GENERATED", "INPUT_PROPERTY", "FLOW_DIAGNOSTICS")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class PropertyRef:
    result_type: str
    name: str

    @classmethod
    def parse(cls, text: str) -> PropertyRef:
        kind, sep, name = str(text).partition(":")
        if not sep or kind not in RESULT_TYPES or not name.strip():
            raise SpecError(f"property must be TYPE:NAME with TYPE in {RESULT_TYPES}, not {text!r}")
        return cls(kind, name.strip())

    def __str__(self) -> str:
        return f"{self.result_type}:{self.name}"


@dataclass(frozen=True)
class Range:
    i: tuple[int, int] | None = None
    j: tuple[int, int] | None = None
    k: tuple[int, int] | None = None

    def bounds(self, axis: str, size: int) -> tuple[int, int]:
        """1-based inclusive (start, count) for the axis; the whole axis when not set."""
        span = getattr(self, axis)
        if span is None:
            return 1, size
        lo, hi = span
        if not 1 <= lo <= hi <= size:
            raise SpecError(f"range {axis}={list(span)} is outside 1..{size}")
        return lo, hi - lo + 1


@dataclass(frozen=True)
class View:
    name: str
    property: PropertyRef
    time_step: int | str = 0
    range: Range | None = None
    z_scale: float | None = None
    flow: dict | None = None  # flow diagnostics: {"injectors": [...], "producers": [...]}
    camera: str = "top"
    zoom: float = 1.0  # side cameras: divide the whole-model distance by this


CAMERAS = ("top", "north", "east")


@dataclass(frozen=True)
class ArrayInput:
    name: str
    file: Path


@dataclass(frozen=True)
class ViewSpec:
    case: Path
    out: Path
    views: tuple[View, ...]
    arrays: tuple[ArrayInput, ...] = ()
    width: int = 1400
    height: int = 1000
    project: bool = True
    title: str = ""
    notes: tuple[str, ...] = field(default=())


def _keys(where: str, data: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    if not isinstance(data, dict):
        raise SpecError(f"{where}: must be an object")
    unknown = set(data) - allowed
    if unknown:
        raise SpecError(f"{where}: unknown keys {sorted(unknown)}; allowed {sorted(allowed)}")
    missing = required - set(data)
    if missing:
        raise SpecError(f"{where}: missing keys {sorted(missing)}")


def _span(where: str, value: Any) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        raise SpecError(f"{where}: must be [first, last] 1-based integers")
    if value[0] < 1 or value[1] < value[0]:
        raise SpecError(f"{where}: {value} must have 1 <= first <= last")
    return value[0], value[1]


def _time_step(where: str, value: Any) -> int | str:
    if isinstance(value, bool):
        raise SpecError(f"{where}: not a time step: {value!r}")
    if isinstance(value, int) and value >= 0:
        return value
    if value in ("first", "last") or (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
        return value
    raise SpecError(f"{where}: time_step must be an index >= 0, first, last, or YYYY-MM-DD; not {value!r}")


def _abs(where: str, value: Any) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        raise SpecError(f"{where}: must be an absolute path, not {value!r}")
    return path


def parse_spec(data: dict) -> ViewSpec:
    _keys(
        "spec",
        data,
        {"case", "out", "views", "arrays", "width", "height", "project", "title", "notes"},
        {"case", "out", "views"},
    )
    arrays = []
    names = set()
    for n, item in enumerate(data.get("arrays") or []):
        _keys(f"arrays[{n}]", item, {"name", "file"}, {"name", "file"})
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,7}", str(item["name"])):
            raise SpecError(
                f"arrays[{n}].name must be a GRDECL keyword (1-8 upper-case), not {item['name']!r}"
            )
        arrays.append(ArrayInput(str(item["name"]), _abs(f"arrays[{n}].file", item["file"])))
        names.add(str(item["name"]))
    views = []
    seen = set()
    if not data["views"]:
        raise SpecError("views: at least one view is required")
    for n, item in enumerate(data["views"]):
        where = f"views[{n}]"
        _keys(
            where,
            item,
            {"name", "property", "time_step", "range", "z_scale", "flow", "camera", "zoom"},
            {"name", "property"},
        )
        name = str(item["name"])
        if not NAME_RE.match(name) or name in seen:
            raise SpecError(f"{where}.name must be unique, letters/digits/_.-: {name!r}")
        seen.add(name)
        prop = PropertyRef.parse(item["property"])
        if prop.result_type == "INPUT_PROPERTY" and prop.name not in names:
            raise SpecError(f"{where}.property {prop} names no array in arrays[]")
        rng = None
        if item.get("range") is not None:
            _keys(f"{where}.range", item["range"], {"i", "j", "k"})
            rng = Range(**{axis: _span(f"{where}.range.{axis}", v) for axis, v in item["range"].items()})
        flow = item.get("flow")
        if flow is not None:
            _keys(f"{where}.flow", flow, {"injectors", "producers"})
            if prop.result_type != "FLOW_DIAGNOSTICS":
                raise SpecError(f"{where}.flow needs a FLOW_DIAGNOSTICS property")
        z = item.get("z_scale")
        if z is not None and not (isinstance(z, (int, float)) and not isinstance(z, bool) and 0 < z <= 100):
            raise SpecError(f"{where}.z_scale must be in (0, 100]")
        camera = item.get("camera", "top")
        if camera not in CAMERAS:
            raise SpecError(f"{where}.camera must be one of {', '.join(CAMERAS)}")
        zoom = item.get("zoom", 1.0)
        if not (isinstance(zoom, (int, float)) and not isinstance(zoom, bool) and 1 <= zoom <= 20):
            raise SpecError(f"{where}.zoom must be in [1, 20]")
        if zoom != 1 and camera == "top":
            raise SpecError(f"{where}.zoom needs camera north or east")
        views.append(
            View(
                name,
                prop,
                _time_step(f"{where}.time_step", item.get("time_step", 0)),
                rng,
                z,
                flow,
                camera,
                float(zoom),
            )
        )
    width, height = data.get("width", 1400), data.get("height", 1000)
    if not all(isinstance(v, int) and not isinstance(v, bool) and 200 <= v <= 4000 for v in (width, height)):
        raise SpecError("width and height must be in 200..4000")
    if not isinstance(data.get("project", True), bool):
        raise SpecError("project must be true or false")
    return ViewSpec(
        case=_abs("case", data["case"]),
        out=_abs("out", data["out"]),
        views=tuple(views),
        arrays=tuple(arrays),
        width=width,
        height=height,
        project=bool(data.get("project", True)),
        title=str(data.get("title", "")),
        notes=tuple(str(n) for n in data.get("notes") or ()),
    )


def load_spec(path: Path) -> ViewSpec:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SpecError(f"{path}: {error}") from error
    return parse_spec(data)


def resolve_time_step(token: int | str, dates: list[str]) -> int:
    """Index of a report step. dates are YYYY-MM-DD in step order. A date must match a step exactly."""
    if not dates:
        raise SpecError("the case has no time steps")
    if token == "first":
        return 0
    if token == "last":
        return len(dates) - 1
    if isinstance(token, int):
        if token >= len(dates):
            raise SpecError(f"time step {token} is outside 0..{len(dates) - 1}")
        return token
    if token not in dates:
        raise SpecError(f"{token} is not a report step. Steps: {dates[0]} .. {dates[-1]} ({len(dates)})")
    return dates.index(token)
