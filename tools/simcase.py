"""Plain text phase and unit facts; no implied unit system."""

import re

PHASES = ("OIL", "WATER", "GAS", "DISGAS", "VAPOIL")
UNITS = ("METRIC", "FIELD", "LAB", "PVT-M")
# METRIC to FIELD: bar to psi, metres to feet, sm3 to stb, sm3 to scf.
METRIC_TO_FIELD = {
    "pressure": 14.503773773,
    "length": 3.280839895,
    "liquid_volume": 6.28981077,
    "gas_volume": 35.31466672,
}


def detect(text):
    words = set()
    for line in text.splitlines():
        line = line.split("--", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*\s*/?", line):
            words.add(line.rstrip("/").strip().upper())
    units = [u for u in UNITS if u in words]
    if len(units) > 1:
        raise ValueError("conflicting unit keywords")
    return {"phases": [p for p in PHASES if p in words], "unit_system": units[0] if units else None}


def default_vectors(phases=None):
    """Unknown phases retain legacy defaults; known phases select active fluids."""
    if phases is None:
        return ["FOPT", "FWPT", "FGPT"]
    return [v for p, v in (("OIL", "FOPT"), ("WATER", "FWPT"), ("GAS", "FGPT")) if p in phases]


def convert(value, quantity, source, target):
    if source not in {"METRIC", "FIELD"} or target not in {"METRIC", "FIELD"}:
        raise ValueError("conversion supports only METRIC and FIELD")
    factor = METRIC_TO_FIELD[quantity]
    return value if source == target else value * factor if source == "METRIC" else value / factor
