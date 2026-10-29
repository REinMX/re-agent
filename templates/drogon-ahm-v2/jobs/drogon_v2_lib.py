"""Pure functions shared by the Drogon v2 forward-model jobs and tools. numpy only."""

import re
from pathlib import Path

import numpy as np

ZONES = {"VALYSAR": 1, "THERYS": 2, "VOLON": 3}
FAULTS = ["F1", "F2", "F3", "F4", "F5", "F6"]
OWC_FLANK_REGIONS = {1, 4, 7}
N_EQUIL = 7
AQ_VOLUME, AQ_PI = 2.0e9, 300.0


def read_params(path: Path) -> dict[str, dict[str, float]]:
    """parameters.txt (GROUP:NAME value) as {group: {name: value}}."""
    out: dict[str, dict[str, float]] = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and ":" in parts[0]:
            group, name = parts[0].split(":", 1)
            out.setdefault(group, {})[name] = float(parts[1])
    return out


def read_grdecl(text: str, keyword: str, size: int) -> np.ndarray:
    """Values of one GRDECL keyword (n*value repeats allowed) as float64 of length size."""
    hit = re.search(rf"(?m)^\s*{re.escape(keyword)}\s*$(.*?)/", text, flags=re.S)
    if hit is None:
        raise ValueError(f"{keyword} not found")
    values: list[float] = []
    for token in re.sub(r"--[^\n]*", "", hit.group(1)).split():
        if "*" in token:
            count, value = token.split("*")
            values += [float(value)] * int(count)
        else:
            values.append(float(token))
    if len(values) != size:
        raise ValueError(f"{keyword}: {len(values)} values, expected {size}")
    return np.asarray(values)


def write_grdecl(keyword: str, values: np.ndarray, fmt: str = "%.6g", per_line: int = 8) -> str:
    """One GRDECL keyword block."""
    v = np.asarray(values, dtype=float)
    pad = (-v.size) % per_line
    rows = np.concatenate([v, np.full(pad, np.nan)]).reshape(-1, per_line)
    lines = [" ".join(fmt % x for x in row if not np.isnan(x)) for row in rows]
    return f"{keyword}\n" + "\n".join(lines) + "\n/\n"


def owc_shifts(p: dict[str, float]) -> np.ndarray:
    """Contact shift (m, positive deeper) per EQLNUM 1..7."""
    return np.array(
        [p["OWC_FLANK"] if r in OWC_FLANK_REGIONS else p["OWC_CENTRAL"] for r in range(1, N_EQUIL + 1)]
    )


def equil(text: str, shifts: np.ndarray) -> str:
    """The deck EQUIL with column 3 (OWC) shifted per record."""
    out, region = [], 0
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("--") and re.match(r"^[0-9.]", s):
            vals, rest = s.split("/", 1)
            v = vals.split()
            v[2] = f"{float(v[2]) + shifts[region]:.2f}"
            region += 1
            out.append("    " + "  ".join(v) + " /" + rest)
        else:
            out.append(line)
    if region != N_EQUIL:
        raise ValueError(f"expected {N_EQUIL} EQUIL records, found {region}")
    return "\n".join(out) + "\n"


def gridmult(p: dict[str, float]) -> str:
    """MULTIREG PORO per zone and MULTFLT. Permeability comes from the FIELD (perm_includes)."""
    lines = ["MULTIREG"] + [f" PORO {p[f'PORO_{zone}']:.6g} {region} M /" for zone, region in ZONES.items()]
    lines += ["/", "", "MULTFLT"] + [f" '{f}' {p[f'MULTFLT_{f}']:.6g} /" for f in FAULTS] + ["/"]
    return "\n".join(lines) + "\n"


def multregt(p: dict[str, float]) -> str:
    return (
        "MULTREGT\n"
        f" 1 2 {p['MULTZ_VAL_THE']:.6g} 'Z' /\n"
        " 1 3 1 'Z' /\n"
        f" 2 3 {p['MULTZ_THE_VOL']:.6g} 'Z' /\n/\n"
    )


def aquifer(p: dict[str, float]) -> tuple[str, str]:
    s = p["AQ_STRENGTH"]
    fetp = f"AQUFETP\n 1 1750 1* {AQ_VOLUME * s:.6g} 5e-5 {AQ_PI * s:.6g} 1 /\n/\n"
    ancon = "AQUANCON\n 1 1 46 1 73 31 31 K+ 1* 1* NO /\n/\n"
    return fetp, ancon


def perm_arrays(static, ln_mult: np.ndarray, kvkh: float) -> dict[str, np.ndarray]:
    """PERMX, PERMY = base * exp(field); PERMZ = base * exp(field) * KVKH_MULT. Non-finite active field values are an error."""
    active = static["actnum"] > 0
    if not np.isfinite(ln_mult[active]).all() or not np.isfinite(kvkh) or kvkh <= 0:
        raise ValueError("invalid active permeability multiplier")
    m = np.exp(np.where(active, ln_mult, 0.0))
    return {"PERMX": static["permx"] * m, "PERMY": static["permy"] * m, "PERMZ": static["permz"] * m * kvkh}


def sw_height_curve(h: np.ndarray, sw: np.ndarray, bin_m: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Binned median of Sw over the height above the FWL (h > 0), made non-increasing in h.

    Nodes at the bin centres. Sw(0) = 1 is the first node. Empty bins are skipped (linear interpolation).
    """
    above = h > 0
    if not above.any():
        return np.array([0.0]), np.array([1.0])
    idx = np.floor(h[above] / bin_m).astype(int)
    nodes, med = [0.0], [1.0]
    for b in np.unique(idx):
        nodes.append((b + 0.5) * bin_m)
        med.append(float(np.median(sw[above][idx == b])))
    return np.asarray(nodes), np.minimum.accumulate(np.asarray(med))


def sw_of_h(curve: tuple[np.ndarray, np.ndarray], h: np.ndarray) -> np.ndarray:
    """Sw at height h above the FWL from a curve; 1 at and below the FWL, the last node above the top."""
    nodes, sw = curve
    return np.where(h <= 0, 1.0, np.interp(h, nodes, sw))


def remap_swatinit(static, shifts: np.ndarray, curves=None) -> tuple[np.ndarray, dict]:
    """SWATINIT consistent with the shifted contacts.

    h = height above the base FWL, h' = above the shifted FWL (FWL + shift; shift > 0 is deeper).
    - h' <= 0 (below the new FWL): 1.0. Flow floods these cells anyway; check_swatinit calls them HC_BELOW_FWL.
    - h <= 0 < h' (water before, above the new FWL): Sw(h').
    - h > 0 and h' > 0 in the transition: SWATINIT + Sw(h') - Sw(h).
    - Cells beyond the transition at both contacts retain the geomodel value.
    The transition ends where Sw is within 0.01 of its high-column plateau.
    A changed value is clipped to [SWL, 1]; an unchanged cell keeps its value (also below SWL). Sw(h) is the per-EQLNUM curve of sw_height_curve on the base SWATINIT.
    Returns the global array and counts per rule.
    """
    act = static["actnum"] > 0
    depth, eql, swat = static["depth"], static["eqlnum"].astype(int), static["swatinit"]
    swl = static["swl"]
    owc = static["owc"]
    new = swat.copy()
    counts = {"below_new_fwl": 0, "rose_above_fwl": 0, "shifted_above": 0}
    for region in range(1, len(owc) + 1):
        cells = act & (eql == region)
        if not cells.any():
            continue
        h = owc[region - 1] - depth[cells]
        hn = h + shifts[region - 1]
        curve = curves[region] if curves else sw_height_curve(h, swat[cells])
        old = swat[cells]
        delta = sw_of_h(curve, hn) - sw_of_h(curve, h)
        out = np.where(delta == 0, old, np.clip(old + delta, swl[cells], 1.0))
        nodes, curve_sw = curve
        transition = nodes[curve_sw > curve_sw[-1] + 0.01].max(initial=0.0)
        in_transition = (h <= transition) | (hn <= transition)
        out = np.where(in_transition, out, old)
        out = np.where((h <= 0) & (hn > 0), sw_of_h(curve, hn), out)
        out = np.where(hn <= 0, 1.0, out)
        new[cells] = out
        counts["below_new_fwl"] += int(((hn <= 0) & (old < 1)).sum())
        counts["rose_above_fwl"] += int(((h <= 0) & (hn > 0)).sum())
        counts["shifted_above"] += int(((h > 0) & (hn > 0) & (out != old)).sum())
    return new, counts
