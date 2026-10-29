#!/usr/bin/env python3
"""Shared FIP keyword rules for region-in-place (ECLIPSE 100 and OPM Flow).

Naming rule (ECLIPSE 100 reference manual, SUMMARY region data; OPM Flow manual 9.3 FIP;
opm-common SummaryConfig.cpp establishRegionContext):
  * Built-in set FIPNUM: R<family>              e.g. ROIP, RGIP, ROIPL
  * User set FIP<xxx>:   R<family> padded with '_' to 5 characters + first 3 characters
                         of the set name after "FIP"   e.g. FIPSEG -> ROIP_SEG, ROIPLSEG, RPR__SEG
  OPM reads characters 6-8 of the summary keyword and looks up "FIP" + those characters
  (unique-prefix match, so ROIP_UNI finds FIPUNIT). A keyword longer than 5 characters
  whose set is not in REGIONS is ignored with a warning by OPM Flow.
"""

from __future__ import annotations

import re

# family -> (meaning, phase of the quantity). All are surface (standard) condition volumes.
FAMILIES: dict[str, str] = {
    "OIP": "oil in place, total (liquid oil + oil vaporised in gas), surface conditions",
    "OIPL": "oil in place in the liquid (oil) phase, surface conditions",
    "OIPG": "oil in place in the gas phase (vaporised oil, needs VAPOIL), surface conditions",
    "GIP": "gas in place, total (free gas + gas dissolved in oil), surface conditions",
    "GIPL": "gas in place in the liquid (oil) phase = dissolved gas, surface conditions",
    "GIPG": "gas in place in the gas phase = free gas, surface conditions",
    "WIP": "water in place, surface conditions",
}
PRESSURE_FAMILY = "PR"  # FPR / RPR (pore-volume weighted pressure), optional
FAMILY_ORDER = ["OIP", "GIP", "WIP", "OIPL", "OIPG", "GIPL", "GIPG"]
DEFAULT_FAMILIES = ["OIP", "GIP"]
# Arrays in REGIONS whose names start with FIP but are not region sets.
NOT_REGION_SETS = {"FIPSEP", "FIP_PROBE"}
SET_NAME_RE = re.compile(r"^FIP[A-Z0-9_]{1,5}$")


def parse_families(text: str | None) -> list[str]:
    if not text:
        return list(DEFAULT_FAMILIES)
    if text.strip().lower() == "all":
        return list(FAMILY_ORDER)
    out = []
    for tok in text.replace(" ", "").upper().split(","):
        if not tok:
            continue
        # accept FOIP / ROIP spellings too
        fam = tok[1:] if tok[0] in "FR" and tok[1:] in FAMILIES else tok
        if fam not in FAMILIES:
            raise ValueError(f"unknown in-place family {tok!r}; use {FAMILY_ORDER} or 'all'")
        if fam not in out:
            out.append(fam)
    return out


def set_suffix(set_name: str) -> str:
    """'FIPSEG' -> 'SEG'; 'FIPNUM' -> '' (built-in)."""
    s = set_name.upper()
    if s == "FIPNUM":
        return ""
    if not s.startswith("FIP") or len(s) < 4:
        raise ValueError(f"region set {set_name!r} must be FIPNUM or FIP<name>")
    return s[3:6]


def canonical_set(set_name: str) -> str:
    """Name used in outputs: FIPNUM, or FIP + the 3 characters the summary keyword carries."""
    s = set_name.upper()
    return "FIPNUM" if s == "FIPNUM" else "FIP" + set_suffix(s)


def field_keyword(family: str) -> str:
    return "F" + family


def region_keyword(family: str, set_name: str) -> str:
    suf = set_suffix(set_name)
    base = "R" + family
    if not suf:
        return base
    if len(base) > 5:
        raise ValueError(f"R{family} is longer than 5 characters")
    return base.ljust(5, "_") + suf


def split_region_keyword(keyword: str, families) -> tuple[str, str] | None:
    """'ROIP_SEG' -> ('OIP', 'FIPSEG'); 'ROIP' -> ('OIP', 'FIPNUM'); else None."""
    kw = keyword.upper()
    for fam in families:
        base = "R" + fam
        if kw == base:
            return fam, "FIPNUM"
        if len(kw) > 5 and kw[:5] == base.ljust(5, "_"):
            return fam, "FIP" + kw[5:8]
    return None


def check_unique_suffixes(sets) -> list[str]:
    """Problems: two sets sharing the 3-character suffix (summary cannot tell them apart)."""
    seen: dict[str, str] = {}
    problems = []
    for s in sets:
        if s.upper() == "FIPNUM":
            continue
        suf = set_suffix(s)
        if suf in seen and seen[suf] != s.upper():
            problems.append(f"{seen[suf]} and {s.upper()} share the summary suffix {suf!r}")
        seen.setdefault(suf, s.upper())
    return problems
