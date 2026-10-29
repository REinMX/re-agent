#!/usr/bin/env python3
"""Oil, gas and water in place at surface (standard) conditions per region, at every report step.

Reads <ECLBASE>.SMSPEC/UNSMRY (ECLIPSE 100 or OPM Flow) for one case or an FMU ensemble and
writes a long CSV, an Excel workbook (one sheet per vector: rows = dates, columns = regions, ready
for SUM in Excel), an at-dates table (initial / last / any date), optional group sums
(segment, reservoir, ...), and a QC check that the regions of each set add up to the field.

Inputs (pick one):
  --case PATH            one case: <DECK>.DATA, <DECK>.SMSPEC or the base path <dir>/<DECK>
  --runpath DIR          one FMU runpath; the deck comes from the contract eclbase (or --eclbase)
  --scratch DIR          ensemble: DIR/realization-*/<iter>/<eclbase>  (--iter, --reals)
  --contract FILE        site contract: eclbase, p10_p90, report_dates, paths.scratch, paths.deliverables.
                         With no --case/--runpath/--scratch the contract paths.scratch ensemble is used.

Volumes: FOIP/ROIP = oil (liquid + vaporised) at surface conditions, FGIP/RGIP = gas (free +
dissolved) at surface conditions, FWIP/RWIP = water. Units come from the SMSPEC (METRIC: SM3).

Initial in place: OPM Flow (and usually ECLIPSE) write no summary step at START. The `initial`
row (START date) is then taken from the PRT balance report at report 0 (RPTSOL FIP=2 or 3), marked
source=PRT. Without it, `initial` is refused (exit 2) and `first` is the first report step.

Exit codes: 0 ok; 1 a realization is missing or unreadable (use --allow-partial only if agreed);
2 missing vectors (see missing_vectors.json), bad date, bad input; 3 outputs written but the
region-sum QC failed (see qc_sum_check.csv). Missing values are never written as zero.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from importlib import import_module
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fip_keywords as fk  # noqa: E402

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
contract_lib = import_module("contract_lib")
runpath_lib = import_module("runpath_lib")

EXCEL_MAX_ROWS = 1_048_000
EXCEL_MAX_COLS = 16_000
PRT_MAX_MB_AUTO = 500
PRT_COLUMNS = {  # family -> res2df.fipreports column name (same PRT balance-report layout)
    "OIP": "STOIIP_TOTAL",
    "OIPL": "STOIIP_OIL",
    "OIPG": "ASSOCIATEDOIL_GAS",
    "WIP": "WIIP_TOTAL",
    "GIP": "GIIP_TOTAL",
    "GIPG": "GIIP_GAS",
    "GIPL": "ASSOCIATEDGAS_OIL",
}
# Region keywords OPM Flow (checked 2026.04) ignores for FIP<xxx> sets: "Unhandled summary keyword".
FLOW_UNSUPPORTED_USER_SET = {"OIPL", "OIPG", "GIPL", "GIPG"}


class Refuse(Exception):
    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


# ----------------------------------------------------------------------------- contract
def cget(contract, dotted, default=None):
    node = contract
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def case_base(path: Path) -> Path:
    p = Path(path)
    if p.suffix.upper() in (".DATA", ".SMSPEC", ".UNSMRY", ".PRT"):
        p = p.with_suffix("")
    return p


def discover_cases(args, contract) -> tuple[list[tuple[int | None, Path, Path]], str]:
    """[(realization or None, case base path, runpath or case dir)], mode."""
    ecl_raw = args.eclbase or (cget(contract, "eclbase") if contract else None)
    if args.case:
        return [(None, case_base(args.case), case_base(args.case).parent)], "single"
    if args.runpath:
        if not ecl_raw:
            raise Refuse("--runpath needs the deck name: contract `eclbase` or --eclbase")
        sub, name = contract_lib.resolve_eclbase(contract, args.eclbase)
        return [(None, Path(args.runpath) / sub / name, Path(args.runpath))], "single"
    scratch = args.scratch or (cget(contract, "paths.scratch") if contract else None)
    if not scratch:
        raise Refuse("give --case, --runpath, --scratch, or a contract with paths.scratch")
    if not ecl_raw:
        raise Refuse("ensemble mode needs the deck name: contract `eclbase` or --eclbase")
    sub, name = contract_lib.resolve_eclbase(contract, args.eclbase)
    it = args.iter
    it = f"iter-{it}" if re.fullmatch(r"\d+", str(it)) else str(it)
    wanted = runpath_lib.parse_realizations(args.reals) if args.reals else None
    ensemble = runpath_lib.read_ensemble(
        str(Path(scratch) / "realization-*" / it), expected=wanted, need_data_file=False
    )
    if not ensemble.complete:
        raise Refuse("; ".join(ensemble.problems()), 1)
    cases = [(member.realization, member.runpath / sub / name, member.runpath) for member in ensemble.found]
    if not cases:
        raise Refuse(f"no realization-*/{it} under {scratch}", 1)
    return cases, "ensemble"


# ----------------------------------------------------------------------------- PRT
PRT_DATE_RE = re.compile(r"REPORT\s+\d+\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})")
MONTHS = {
    m: i
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1
    )
}


def _prt_line(line: str):
    """Parse ':CURRENTLY IN PLACE : liq vap tot : wat : free diss tot :' (same layout as res2df)."""
    parts = line.split(":")
    if len(parts) < 5:
        return None

    def nums(s):
        out = []
        for t in s.split():
            try:
                out.append(float(t))
            except ValueError:
                out.append(np.nan)
        return out

    oil, wat, gas = nums(parts[2]), nums(parts[3]), nums(parts[4])
    rec = {}
    if len(oil) == 3:
        rec.update(OIPL=oil[0], OIPG=oil[1], OIP=oil[2])
    elif len(oil) == 2:
        rec.update(OIPL=oil[0], OIP=oil[1])
    elif len(oil) == 1:
        rec.update(OIP=oil[0])
    if wat:
        rec["WIP"] = wat[-1]
    if len(gas) == 3:
        rec.update(GIPG=gas[0], GIPL=gas[1], GIP=gas[2])
    elif len(gas) == 2:
        rec.update(GIPG=gas[0], GIP=gas[1])
    elif len(gas) == 1:
        rec.update(GIP=gas[0])
    return rec


OPM_DATE_RE = re.compile(r"Starting time step.*? date = (\d+)-(\w+)-(\d+)")
PRT_SETNAME_RE = re.compile(r"(FIP[A-Z0-9_]{1,5})\s+REPORT\s+REGION\s+\d+", re.IGNORECASE)


def _prt_date(line: str):
    m = PRT_DATE_RE.search(line) if "REPORT" in line[:12] else None
    m = m or OPM_DATE_RE.search(line)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), MONTHS[m.group(2).upper()[:3]], int(m.group(1)))
    except (KeyError, ValueError):
        return None


def read_prt(prt: Path, until: dt.date | None = None) -> pd.DataFrame:
    """CURRENTLY IN PLACE per region (res2df.fipreports) and FIELD TOTALS from PRT balance reports.

    With `until`, only the PRT up to that date is read (fast on big PRT files).
    Region rows use res2df (handles ECLIPSE 100 and OPM Flow layouts). FIELD TOTALS use a small
    colon-based parser; when it cannot read a line the field value is left out, never zero.
    """
    import tempfile

    import res2df.fipreports as fipreports

    names: dict[str, None] = {}
    field_rows = []
    date = None
    in_field = False
    tmp = None
    src = prt
    with open(prt, errors="replace") as fh:
        if until is not None:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".PRT", delete=False)
            src = Path(tmp.name)
        for line in fh:
            d = _prt_date(line)
            if d is not None:
                if until is not None and d > until:
                    break
                date = d
            if tmp is not None:
                tmp.write(line)
            m = PRT_SETNAME_RE.search(line)
            if m:
                names.setdefault(m.group(1).upper(), None)
                in_field = False
                continue
            if "FIELD TOTALS" in line:
                in_field = True
                continue
            if in_field and date and line.lstrip().startswith(":CURRENTLY IN PLACE"):
                rec = _prt_line(line)
                for fam, val in (rec or {}).items():
                    if not np.isnan(val):
                        field_rows.append((date, "FIELD", 0, fam, val))
                in_field = False
    if tmp is not None:
        tmp.close()
    frames = [pd.DataFrame(field_rows, columns=["date", "region_set", "region", "vector", "value"])]
    try:
        for name in names:
            df = fipreports.df(str(src), fipname=name)
            if df.empty:
                continue
            df = df[df.DATATYPE.astype(str).str.upper().str.strip() == "CURRENTLY IN PLACE"]
            canon = "FIPNUM" if name == "FIPNUM" else fk.canonical_set(name)
            for fam, col in PRT_COLUMNS.items():
                if col not in df.columns:
                    continue
                sub = df[["DATE", "REGION", col]].dropna(subset=[col])
                frames.append(
                    pd.DataFrame(
                        {
                            "date": pd.to_datetime(sub.DATE).dt.date,
                            "region_set": canon,
                            "region": sub.REGION.astype(int),
                            "vector": fam,
                            "value": sub[col].astype(float),
                        }
                    )
                )
    finally:
        if tmp is not None:
            Path(tmp.name).unlink(missing_ok=True)
    out = pd.concat(frames, ignore_index=True)
    if until is not None:
        out = out[out.date <= until]
    return out.drop_duplicates(["date", "region_set", "region", "vector"], keep="last").reset_index(drop=True)


# ----------------------------------------------------------------------------- summary
def read_case(base: Path, families, sets_req, args) -> dict:
    """Region + field in-place tables for one case. Raises Refuse on missing data."""
    from resdata.summary import Summary

    smspec = base.with_suffix(".SMSPEC")
    if args.from_prt:
        return read_case_prt(base, families, sets_req, args)
    if not smspec.is_file():
        raise Refuse(f"no SMSPEC: {smspec}", 1)
    try:
        s = Summary(str(base))
    except Exception as exc:  # resdata raises IOError/ValueError
        raise Refuse(f"cannot read summary {base}: {exc}", 1) from exc
    start = s.start_date
    if isinstance(start, dt.datetime):
        start = start.date()
    rdates = [d.date() if isinstance(d, dt.datetime) else d for d in s.report_dates]
    all_dates = [d.date() if isinstance(d, dt.datetime) else d for d in s.dates]
    keys = s.keys()
    try:
        own = Summary(str(base), include_restart=False)
        own_first = own.dates[0].date() if len(own.dates) else None
    except Exception:
        own_first = None
    chain_loaded = bool(own_first and all_dates and own_first > all_dates[0])
    restart_no_chain = bool(all_dates and own_first and not chain_loaded and (all_dates[0] - start).days > 31)

    present: dict[tuple[str, str], dict[int, str]] = {}
    for k in keys:
        if ":" not in k:
            continue
        kw, num = k.split(":", 1)
        hit = fk.split_region_keyword(kw, families)
        if hit and num.isdigit():
            present.setdefault(hit, {})[int(num)] = k
    # Region sets are discovered from any in-place family in the SMSPEC, not only the requested ones,
    # so a missing family on a known set is reported as a missing keyword.
    any_sets = set()
    for k in keys:
        hit = fk.split_region_keyword(k.split(":", 1)[0], fk.FAMILY_ORDER) if ":" in k else None
        if hit:
            any_sets.add(hit[1])
    found_sets = sorted(any_sets, key=lambda x: (x != "FIPNUM", x))
    sets = [fk.canonical_set(x) for x in sets_req] if sets_req else found_sets
    missing = []
    for fam in families:
        if fk.field_keyword(fam) not in keys:
            missing.append(fk.field_keyword(fam))
    if not sets:
        sets_hint = ["FIPNUM"]
        missing += [fk.region_keyword(f, "FIPNUM") for f in families]
        raise MissingVectors(missing, sets_hint, families, "no region in-place vectors in the SUMMARY")
    for st in sets:
        for fam in families:
            if (fam, st) not in present:
                missing.append(fk.region_keyword(fam, st))
    if missing:
        raise MissingVectors(missing, sets, families, "requested vectors not in SMSPEC")

    rows = []
    for (fam, st), regs in present.items():
        if st not in sets:
            continue
        for reg, key in sorted(regs.items()):
            vals = s.numpy_vector(key, report_only=True)
            unit = s.unit(key)
            rows.append(
                pd.DataFrame(
                    {
                        "date": rdates,
                        "region_set": st,
                        "region": reg,
                        "vector": fam,
                        "keyword": key.split(":")[0],
                        "value": vals.astype(float),
                        "unit": unit,
                        "source": "SUMMARY",
                    }
                )
            )
    for fam in families:
        key = fk.field_keyword(fam)
        rows.append(
            pd.DataFrame(
                {
                    "date": rdates,
                    "region_set": "FIELD",
                    "region": 0,
                    "vector": fam,
                    "keyword": key,
                    "value": s.numpy_vector(key, report_only=True).astype(float),
                    "unit": s.unit(key),
                    "source": "SUMMARY",
                }
            )
        )
    data = pd.concat(rows, ignore_index=True)
    info = {
        "start": start.isoformat(),
        "first_summary_step": all_dates[0].isoformat() if all_dates else None,
        "first_report_step": rdates[0].isoformat() if rdates else None,
        "last_report_step": rdates[-1].isoformat() if rdates else None,
        "restart_chain_loaded": chain_loaded,
        "restart_without_history": restart_no_chain,
        "initial_source": None,
        "region_sets": sets,
        "regions": {
            st: sorted({r for (f, x), regs in present.items() if x == st for r in regs}) for st in sets
        },
    }

    # Initial (START) row
    if start in all_dates and start not in rdates:
        idx = all_dates.index(start)
        extra = []
        for (fam, st), regs in present.items():
            if st in sets:
                for reg, key in regs.items():
                    extra.append(
                        (
                            start,
                            st,
                            reg,
                            fam,
                            key.split(":")[0],
                            float(s.numpy_vector(key)[idx]),
                            s.unit(key),
                            "SUMMARY",
                        )
                    )
        for fam in families:
            key = fk.field_keyword(fam)
            extra.append(
                (start, "FIELD", 0, fam, key, float(s.numpy_vector(key)[idx]), s.unit(key), "SUMMARY")
            )
        data = pd.concat([pd.DataFrame(extra, columns=data.columns), data], ignore_index=True)
        info["initial_source"] = "SUMMARY"
    elif start not in rdates and not restart_no_chain and args.initial_from_prt != "no":
        prt = base.with_suffix(".PRT")
        use = prt.is_file() and (
            args.initial_from_prt == "yes" or prt.stat().st_size / 1e6 <= PRT_MAX_MB_AUTO
        )
        if args.initial_from_prt == "yes" and not prt.is_file():
            raise Refuse(f"--initial-from-prt yes but no PRT: {prt}")
        if use:
            p = read_prt(prt, until=start)
            p = p[p.date == start]
            init = []
            units = data.groupby(["region_set", "vector"])["unit"].first().to_dict()
            kwmap = data.groupby(["region_set", "vector"])["keyword"].first().to_dict()
            ok = True
            for st in ["FIELD", *sets]:
                regs = [0] if st == "FIELD" else info["regions"][st]
                for fam in families:
                    sub = p[(p.region_set == st) & (p.vector == fam)].set_index("region")["value"]
                    if not set(regs) <= set(sub.index):
                        ok = False  # this set/vector is not in the PRT report 0: no initial row (not zero)
                        continue
                    for r in regs:
                        init.append(
                            (
                                start,
                                st,
                                r,
                                fam,
                                kwmap.get((st, fam), ""),
                                float(sub[r]),
                                units.get((st, fam), ""),
                                "PRT",
                            )
                        )
            if init:
                data = pd.concat([pd.DataFrame(init, columns=data.columns), data], ignore_index=True)
                info["initial_source"] = (
                    "PRT" if ok else "PRT (partial: some sets/vectors not in the PRT report 0)"
                )
    data = data.sort_values(["region_set", "vector", "region", "date"], kind="stable").reset_index(drop=True)
    return {"data": data, "info": info}


def read_case_prt(base: Path, families, sets_req, args) -> dict:
    prt = base.with_suffix(".PRT")
    if not prt.is_file():
        raise Refuse(f"--from-prt: no PRT file {prt}", 1)
    p = read_prt(prt)
    if p.empty:
        raise Refuse(f"{prt} has no balance reports. Add RPTSOL 'FIP=3' / and RPTSCHED 'FIP=3' / and re-run.")
    sets = (
        [fk.canonical_set(x) for x in sets_req]
        if sets_req
        else sorted(set(p.region_set) - {"FIELD"}, key=lambda x: (x != "FIPNUM", x))
    )
    missing = []
    for st in sets:
        for fam in families:
            if p[(p.region_set == st) & (p.vector == fam)].empty:
                missing.append(f"{st}:{fam} (PRT)")
    for fam in families:
        if p[(p.region_set == "FIELD") & (p.vector == fam)].empty:
            missing.append(f"FIELD:{fam} (PRT)")
    if missing:
        raise Refuse(
            "PRT balance reports lack: "
            + ", ".join(missing)
            + ". FIP=2 gives FIPNUM only; FIP=3 adds the FIP<xxx> sets."
        )
    units = {}
    with open(prt, errors="replace") as fh:
        for line in fh:
            m = re.search(r"OIL\s+(\S+)\s+-.*WAT\s+(\S+)\s+-.*GAS\s+(\S+)\s+-", line)
            if m:
                units = {"O": m.group(1), "W": m.group(2), "G": m.group(3)}
                break
    p = p[p.region_set.isin(["FIELD", *sets]) & p.vector.isin(families)].copy()
    p["keyword"] = [
        fk.field_keyword(f) if st == "FIELD" else fk.region_keyword(f, st)
        for st, f in zip(p.region_set, p.vector, strict=True)
    ]
    p["unit"] = [units.get(f[0], "") for f in p.vector]
    p["source"] = "PRT"
    p = p[["date", "region_set", "region", "vector", "keyword", "value", "unit", "source"]]
    dates = sorted(p.date.unique())
    start = dates[0]
    xcheck = pd.DataFrame()
    if base.with_suffix(".SMSPEC").is_file():
        xcheck, start = prt_vs_summary(base, p)
    info = {
        "start": start.isoformat(),
        "prt_vs_summary": xcheck,
        "first_report_step": dates[0].isoformat(),
        "last_report_step": dates[-1].isoformat(),
        "initial_source": "PRT",
        "region_sets": sets,
        "restart_without_history": False,
        "regions": {st: sorted(p[p.region_set == st].region.unique().tolist()) for st in sets},
    }
    return {
        "data": p.sort_values(["region_set", "vector", "region", "date"]).reset_index(drop=True),
        "info": info,
    }


def prt_vs_summary(base: Path, p: pd.DataFrame):
    """Compare PRT values with SMSPEC values at shared report dates (checks the keyword mapping)."""
    from resdata.summary import Summary

    s = Summary(str(base))
    start = s.start_date.date() if isinstance(s.start_date, dt.datetime) else s.start_date
    rd = [d.date() if isinstance(d, dt.datetime) else d for d in s.report_dates]
    idx = {d: i for i, d in enumerate(rd)}
    rows = []
    cache = {}
    for (st, reg, fam, kw), sub in p.groupby(["region_set", "region", "vector", "keyword"]):
        key = kw if st == "FIELD" else f"{kw}:{reg}"
        if not s.has_key(key):
            continue
        if key not in cache:
            cache[key] = s.numpy_vector(key, report_only=True)
        for d, v in zip(sub.date, sub.value, strict=True):
            if d in idx:
                sv = float(cache[key][idx[d]])
                rows.append((d, st, reg, fam, kw, v, sv, abs(v - sv) / max(abs(sv), 1.0)))
    return pd.DataFrame(
        rows, columns=["date", "region_set", "region", "vector", "keyword", "prt", "summary", "rel_diff"]
    ), start


class MissingVectors(Refuse):
    def __init__(self, missing, sets, families, why):
        super().__init__(f"{why}: {', '.join(missing)}", 2)
        self.missing, self.sets, self.families = missing, sets, families


def missing_payload(exc: MissingVectors, simulator: str | None) -> dict:
    lines = []
    for kw in exc.missing:
        lines.append(kw if kw.startswith("F") else f"{kw}\n/")
    flow_note = []
    for kw in exc.missing:
        hit = fk.split_region_keyword(kw, fk.FAMILY_ORDER) if kw.startswith("R") else None
        if hit and hit[1] != "FIPNUM" and hit[0] in FLOW_UNSUPPORTED_USER_SET:
            flow_note.append(kw)
    return {
        "missing_keywords": exc.missing,
        "reason": str(exc),
        "summary_include_to_add": "\n".join(lines) + "\n",
        "how": "Add these to the SUMMARY section (or generate the include with make_fip_summary.py --deck <DECK>.DATA), "
        "make sure the FIP<xxx> arrays exist in REGIONS, re-run the simulator. Values are never filled with zero.",
        "opm_flow_cannot_write": flow_note,
        "opm_flow_note": (
            "OPM Flow (checked 2026.04) ignores phase-split keywords for FIP<xxx> sets "
            "('Unhandled summary keyword'). Use --families OIP,GIP,WIP for those sets, or --from-prt "
            "(the PRT FIP=3 report has the phase split)."
        )
        if flow_note
        else "",
    }


# ----------------------------------------------------------------------------- dates
def resolve_dates(
    tokens: list[str], avail: list[dt.date], info: dict, contract, tol_days: int
) -> list[tuple[str, dt.date]]:
    out = []
    rd = cget(contract, "report_dates", {}) if contract else {}
    start = dt.date.fromisoformat(info["start"]) if info.get("start") else None
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok == "first":
            out.append(("first", avail[0]))
            continue
        if tok == "last":
            out.append(("last", avail[-1]))
            continue
        if tok == "initial":
            if start is None or start not in avail:
                raise Refuse(
                    "`initial` (START date) is not available: the summary has no step at START and the PRT has no "
                    "balance report at report 0 (add RPTSOL 'FIP=2' / or 'FIP=3' /), or this is a restart run "
                    f"without history. First available step: {avail[0]}. Use `first` if that is acceptable."
                )
            out.append(("initial", start))
            continue
        label = tok
        if isinstance(rd, dict) and tok in rd:
            tok = str(rd[tok])
        try:
            want = dt.date.fromisoformat(tok)
        except ValueError as exc:
            raise Refuse(
                f"bad date token {label!r}: use initial, first, last, YYYY-MM-DD or a contract report_dates key"
            ) from exc
        if want < avail[0] or want > avail[-1]:
            raise Refuse(f"date {want} is outside the run ({avail[0]} .. {avail[-1]}); no extrapolation")
        near = min(avail, key=lambda d: abs((d - want).days))
        if abs((near - want).days) > tol_days:
            before = max([d for d in avail if d <= want], default=None)
            after = min([d for d in avail if d >= want], default=None)
            raise Refuse(
                f"no report step within {tol_days} day(s) of {want}; nearest steps {before} and {after}. "
                "In-place values are states: they are not interpolated. Use --date-tol-days or pick a step."
            )
        out.append((label, near))
    return out


def yearly_dates(
    avail: list[dt.date], tol_days: int, start: dt.date | None
) -> tuple[list[dt.date], list[int]]:
    keep, gaps = [], []
    if start and start in avail:
        keep.append(start)
    for y in range(avail[0].year, avail[-1].year + 1):
        target = dt.date(y, 1, 1)
        if target < avail[0] - dt.timedelta(days=tol_days) or target > avail[-1] + dt.timedelta(
            days=tol_days
        ):
            continue
        near = min(avail, key=lambda d: abs((d - target).days))
        if abs((near - target).days) <= tol_days:
            if near not in keep:
                keep.append(near)
        else:
            gaps.append(y)
    return sorted(keep), gaps


# ----------------------------------------------------------------------------- groups
def parse_region_list(v) -> list[int]:
    if isinstance(v, int):
        return [v]
    out = []
    for part in str(v).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def load_groups(path: Path) -> pd.DataFrame:
    """Rows: grouping, region_set, region, group.

    CSV columns: region_set, region, group[, grouping]. region may be a list or range ("1-3,7").
    YAML: {grouping: {region_set: {group: [regions]}}} or {region_set: {group: [regions]}}.
    """
    rows = []
    if path.suffix.lower() in (".yml", ".yaml"):
        import yaml

        y = yaml.safe_load(path.read_text()) or {}
        flat = all(str(k).upper().startswith("FIP") for k in y)
        tree = {"group": y} if flat else y
        for grouping, sets in tree.items():
            for st, groups in (sets or {}).items():
                for g, regs in (groups or {}).items():
                    for r in parse_region_list(
                        regs if not isinstance(regs, list) else ",".join(map(str, regs))
                    ):
                        rows.append((str(grouping), fk.canonical_set(str(st)), r, str(g)))
    else:
        df = pd.read_csv(path, dtype=str).fillna("")
        need = {"region_set", "region", "group"}
        if not need <= set(c.strip().lower() for c in df.columns):
            raise Refuse(f"{path}: needs columns region_set, region, group (optional grouping)")
        df.columns = [c.strip().lower() for c in df.columns]
        for _, r in df.iterrows():
            for reg in parse_region_list(r["region"]):
                rows.append(
                    (r.get("grouping") or "group", fk.canonical_set(r["region_set"]), reg, r["group"])
                )
    g = pd.DataFrame(rows, columns=["grouping", "region_set", "region", "group"])
    dup = g[g.duplicated(["grouping", "region_set", "region"], keep=False)]
    if not dup.empty:
        raise Refuse(
            "a region is in two groups of the same grouping: "
            + "; ".join(
                f"{a} {b}:{c}"
                for a, b, c in dup[["grouping", "region_set", "region"]].drop_duplicates().values
            )
        )
    return g


def apply_groups(
    data: pd.DataFrame, groups: pd.DataFrame, regions: dict
) -> tuple[pd.DataFrame, pd.DataFrame]:
    bad = [(st, r) for st, r in groups[["region_set", "region"]].values if r not in regions.get(st, [])]
    if bad:
        raise Refuse(
            "group mapping names regions that are not in the run (no zero-fill): "
            + ", ".join(f"{a}:{b}" for a, b in bad[:30])
        )
    unm = []
    for grouping, gsub in groups.groupby("grouping"):
        for st in gsub.region_set.unique():
            mapped = set(gsub[gsub.region_set == st].region)
            for r in regions.get(st, []):
                if r not in mapped:
                    unm.append((grouping, st, r))
    unmapped = pd.DataFrame(unm, columns=["grouping", "region_set", "region"])
    reg = data[data.region_set != "FIELD"]
    m = reg.merge(groups, on=["region_set", "region"], how="inner")
    keys = [
        c
        for c in ["realization", "date", "grouping", "group", "region_set", "vector", "unit"]
        if c in m.columns
    ]
    out = (
        m.groupby(keys, dropna=False)
        .agg(
            value=("value", "sum"),
            n_regions=("region", "nunique"),
            source=("source", lambda s: "+".join(sorted(set(s)))),
        )
        .reset_index()
    )
    return out, unmapped


# ----------------------------------------------------------------------------- QC + derived
def add_change_columns(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    df = df.sort_values(keys + ["date"], kind="stable").copy()
    first = df.groupby(keys, dropna=False)["value"].transform("first")
    df["first_date"] = df.groupby(keys, dropna=False)["date"].transform("first")
    df["change_since_first"] = df["value"] - first
    with np.errstate(divide="ignore", invalid="ignore"):
        df["fraction_of_first"] = np.where(first != 0, df["value"] / first, np.nan)
    return df


def qc_sums(data: pd.DataFrame, rtol: float, atol: float) -> pd.DataFrame:
    rows = []
    gcols = ["realization"] if "realization" in data.columns else []
    field = data[data.region_set == "FIELD"]
    for key, sub in data[data.region_set != "FIELD"].groupby(gcols + ["region_set", "vector"]):
        key = key if isinstance(key, tuple) else (key,)
        fam = key[-1]
        fsel = field[field.vector == fam]
        if gcols:
            fsel = fsel[fsel.realization == key[0]]
        f = fsel.groupby("date")["value"].first()
        s = sub.groupby("date")["value"].sum()
        both = pd.concat([s.rename("regions"), f.rename("field")], axis=1, join="inner")
        if both.empty:
            rows.append((*key, 0, np.nan, np.nan, None, "NO_FIELD_VECTOR", sub.region.nunique()))
            continue
        diff = both.regions - both.field
        rel = (diff.abs() / both.field.abs().where(both.field.abs() > 0)).fillna(0.0)
        worst = diff.abs().idxmax()
        ok = bool(((diff.abs() <= atol) | (rel <= rtol)).all())
        rows.append(
            (
                *key,
                len(both),
                float(diff.abs().max()),
                float(rel.max()),
                str(worst),
                "PASS" if ok else ("FAIL_REGIONS_LOW" if diff[worst] < 0 else "FAIL_REGIONS_HIGH"),
                sub.region.nunique(),
            )
        )
    return pd.DataFrame(
        rows,
        columns=gcols
        + [
            "region_set",
            "vector",
            "n_dates",
            "max_abs_diff",
            "max_rel_diff",
            "worst_date",
            "status",
            "n_regions",
        ],
    )


BALANCE = {  # in-place family -> (produced total, injected total); balance: IP(t0) - IP(t) = produced - injected
    "OIP": ("FOPT", None),
    "GIP": ("FGPT", "FGIT"),
    "WIP": ("FWPT", "FWIT"),
}


def field_balance(base: Path, data: pd.DataFrame, start: dt.date | None) -> pd.DataFrame:
    """Info only: field IP drop since the first date vs produced - injected (FOPT, FGPT-FGIT, FWPT-FWIT).

    Differences come from aquifer influx (water), VAPOIL/condensate, oil injection, or a wrong initial row.
    """
    from resdata.summary import Summary

    try:
        s = Summary(str(base))
    except Exception:
        return pd.DataFrame()
    rd = [d.date() if isinstance(d, dt.datetime) else d for d in s.report_dates]
    rows = []
    field = data[data.region_set == "FIELD"]
    for fam, (prod, inj) in BALANCE.items():
        f = field[field.vector == fam].sort_values("date")
        if f.empty or not s.has_key(prod) or (inj and not s.has_key(inj)):
            continue
        p = dict(zip(rd, s.numpy_vector(prod, report_only=True), strict=True))
        i = dict(zip(rd, s.numpy_vector(inj, report_only=True), strict=True)) if inj else {}
        d0, v0 = f.date.iloc[0], f.value.iloc[0]
        if d0 in p:
            p0, i0 = p[d0], (i[d0] if inj else 0.0)
        elif start is not None and d0 == start:
            p0, i0 = 0.0, 0.0  # cumulatives are zero at START of a run that is not a restart
        else:
            continue
        for d, v in zip(f.date, f.value, strict=True):
            if d not in p:
                continue
            net = (p[d] - p0) - ((i.get(d, np.nan) - i0) if inj else 0.0)
            drop = v0 - v
            rows.append((d, fam, drop, net, drop - net, (drop - net) / max(abs(v0), 1.0)))
    return pd.DataFrame(
        rows,
        columns=[
            "date",
            "vector",
            "ip_drop_since_first",
            "produced_minus_injected",
            "difference",
            "difference_rel_to_first_ip",
        ],
    )


def empty_regions(data: pd.DataFrame) -> list[str]:
    reg = data[(data.region_set != "FIELD") & data.vector.isin(["OIP", "GIP", "WIP"])]
    if reg.empty:
        return []
    tot = reg.groupby(["region_set", "region"])["value"].apply(lambda v: float(np.nanmax(np.abs(v))))
    return [f"{a}:{b}" for (a, b), v in tot.items() if v == 0.0]


def pct(values: np.ndarray, conv: str) -> tuple[float, float, float]:
    p10, p50, p90 = np.percentile(values, [10, 50, 90])
    if conv == "exceedance":
        p10, p90 = p90, p10
    return float(p10), float(p50), float(p90)


def stats(df: pd.DataFrame, keys: list[str], conv: str, min_n: int) -> pd.DataFrame:
    rows = []
    for k, sub in df.groupby(keys, dropna=False):
        v = sub["value"].to_numpy(float)
        v = v[~np.isnan(v)]
        n = len(v)
        p = pct(v, conv) if n >= min_n else (np.nan, np.nan, np.nan)
        rows.append(
            (
                *(k if isinstance(k, tuple) else (k,)),
                n,
                float(v.mean()) if n else np.nan,
                float(v.std(ddof=1)) if n > 1 else np.nan,
                *p,
            )
        )
    return pd.DataFrame(rows, columns=keys + ["n", "mean", "std", "p10", "p50", "p90"])


# ----------------------------------------------------------------------------- outputs
def pivot_sheet(df: pd.DataFrame, fam: str) -> pd.DataFrame:
    sub = df[df.vector == fam].copy()
    sub["column"] = [
        ("FIELD" if st == "FIELD" else f"{st} {r}") for st, r in zip(sub.region_set, sub.region, strict=True)
    ]
    order = ["FIELD"] + [
        c
        for c in dict.fromkeys(
            f"{st} {r}"
            for st, r in sorted(
                set(zip(sub.region_set, sub.region, strict=True)),
                key=lambda x: (x[0] != "FIPNUM", x[0], x[1]),
            )
            if st != "FIELD"
        )
    ]
    pv = sub.pivot_table(index="date", columns="column", values="value", aggfunc="first")
    return pv[[c for c in order if c in pv.columns]]


def pick_label(label: str, d: dt.date) -> str:
    """Column title: '2046-01-01', 'initial 2025-01-01', '2045-12-31 (step 2046-01-01)'."""
    if label == d.isoformat():
        return label
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", label):
        return f"{label} (step {d})"
    return f"{label} {d}"


def at_dates_table(df: pd.DataFrame, picks: list[tuple[str, dt.date]]) -> pd.DataFrame:
    cols = {}
    for label, d in picks:
        s = df[df.date == d].set_index(["region_set", "region", "vector", "unit"])["value"]
        cols[pick_label(label, d)] = s
    t = pd.DataFrame(cols)
    if len(picks) >= 2:
        a, b = t.columns[0], t.columns[-1]
        t[f"change {picks[0][1]}->{picks[-1][1]}"] = t[b] - t[a]
        with np.errstate(divide="ignore", invalid="ignore"):
            t[f"ratio {picks[-1][1]}/{picks[0][1]}"] = np.where(t[a] != 0, t[b] / t[a], np.nan)
    t = t.reset_index()
    t["_o"] = [0 if s == "FIELD" else (1 if s == "FIPNUM" else 2) for s in t.region_set]
    return t.sort_values(["vector", "_o", "region_set", "region"]).drop(columns="_o")


def write_excel(path: Path, sheets: list[tuple[str, pd.DataFrame, bool]], readme: list[tuple[str, str]]):
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(readme, columns=["item", "value"]).to_excel(xw, sheet_name="README", index=False)
        for name, df, index in sheets:
            if df is None or df.empty:
                continue
            df.to_excel(xw, sheet_name=name[:31], index=index)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "B2"
            for col in ws.iter_cols(min_row=1, max_row=1):
                ws.column_dimensions[col[0].column_letter].width = max(
                    12, min(40, len(str(col[0].value or "")) + 2)
                )
        xw.book["README"].column_dimensions["B"].width = 120


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", type=Path)
    ap.add_argument("--runpath", type=Path)
    ap.add_argument("--scratch", type=Path)
    ap.add_argument("--contract", type=Path)
    ap.add_argument("--profile")
    ap.add_argument("--eclbase", help="override contract eclbase, e.g. eclipse/model/<DECK>")
    ap.add_argument("--iter", default="0", help="ensemble iteration: 0, iter-3, pred_ref ... (default 0)")
    ap.add_argument("--reals", help="ensemble subset, e.g. 0-9,12")
    ap.add_argument("--allow-partial", action="store_true", help="skip unreadable/unfinished realizations")
    ap.add_argument(
        "--families", default="OIP,GIP", help="OIP,GIP,WIP,OIPL,OIPG,GIPL,GIPG or all (default OIP,GIP)"
    )
    ap.add_argument("--region-sets", help="comma list, e.g. FIPNUM,FIPSEG (default: all sets in the SUMMARY)")
    ap.add_argument(
        "--dates", default="first,last", help="initial,first,last,YYYY-MM-DD,<contract report_dates key>"
    )
    ap.add_argument(
        "--date-tol-days", type=int, default=1, help="nearest report step allowed within N days (default 1)"
    )
    ap.add_argument("--yearly", action="store_true", help="time axis = 1 Jan report steps (+ initial)")
    ap.add_argument("--groups", type=Path, help="CSV or YAML mapping (region_set, region -> group)")
    ap.add_argument("--initial-from-prt", choices=["auto", "yes", "no"], default="auto")
    ap.add_argument(
        "--from-prt", action="store_true", help="read everything from PRT balance reports (FIP=2/3)"
    )
    ap.add_argument("--qc-rtol", type=float, default=1e-4)
    ap.add_argument("--qc-atol", type=float, default=1.0)
    ap.add_argument("--min-n-percentiles", type=int, default=10)
    ap.add_argument("--label", default="region_in_place")
    ap.add_argument(
        "--out",
        type=Path,
        help="output folder (default <contract paths.deliverables>/region_in_place/<label>)",
    )
    a = ap.parse_args(argv)

    try:
        contract = (
            contract_lib.load_contract(
                a.contract,
                a.profile,
                require_project=False,
                skip_required=("eclbase",) if a.eclbase else (),
            )
            if a.contract is not None
            else None
        )
    except contract_lib.ContractError as exc:
        print(f"error: contract: {exc}", file=sys.stderr)
        return 2
    out = a.out or (
        Path(cget(contract, "paths.deliverables")) / "region_in_place" / a.label
        if contract and cget(contract, "paths.deliverables")
        else None
    )
    if out is None:
        print("error: give --out (or a contract with paths.deliverables)", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    conv = (
        str(cget(contract, "p10_p90") or cget(contract, "percentile_convention") or "numerical").lower()
        if contract
        else "numerical"
    )
    conv_text = (
        "numerical: P10 = 10th percentile (low), P90 = 90th percentile (high)"
        if conv == "numerical"
        else "exceedance: P10 = high (10% chance of more), P90 = low"
    )
    sim = None
    if contract:
        s = cget(contract, "simulator")
        sim = s.get("name") if isinstance(s, dict) else s
    try:
        families = fk.parse_families(a.families)
        sets_req = [x.strip() for x in a.region_sets.split(",")] if a.region_sets else None
        cases, mode = discover_cases(a, contract)
        groups = load_groups(a.groups) if a.groups else None
    except (Refuse, ValueError, contract_lib.ContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return getattr(exc, "code", 2)

    results, failed = [], []
    for real, base, runpath in cases:
        if mode == "ensemble" and not (runpath / "OK").is_file():
            failed.append((real, f"no ERT OK file in {runpath} (failed or running)"))
            continue
        try:
            r = read_case(base, families, sets_req, a)
        except MissingVectors as exc:
            payload = missing_payload(exc, sim)
            payload["case"] = str(base)
            (out / "missing_vectors.json").write_text(json.dumps(payload, indent=2))
            print(f"error: {exc}\nwrote {out / 'missing_vectors.json'}", file=sys.stderr)
            if payload["opm_flow_note"]:
                print("note: " + payload["opm_flow_note"], file=sys.stderr)
            return 2
        except Refuse as exc:
            if exc.code == 1:
                failed.append((real, str(exc)))
                continue
            print(f"error: {exc}", file=sys.stderr)
            return exc.code
        r["real"] = real
        r["base"] = base
        results.append(r)
    if failed and not a.allow_partial:
        for real, why in failed:
            print(f"error: realization {real}: {why}", file=sys.stderr)
        print("Use --allow-partial only if the user agrees.", file=sys.stderr)
        return 1
    if not results:
        print("error: nothing read", file=sys.stderr)
        return 1

    # Consistent region lists across realizations
    ref = results[0]["info"]["regions"]
    for r in results[1:]:
        if r["info"]["regions"] != ref:
            print(
                f"error: realization {r['real']} has different regions than realization {results[0]['real']}",
                file=sys.stderr,
            )
            return 2
    frames = []
    for r in results:
        d = r["data"]
        if mode == "ensemble":
            d = d.assign(realization=r["real"])
        frames.append(d)
    data = pd.concat(frames, ignore_index=True)
    info0 = results[0]["info"]
    avail = sorted(set.intersection(*[set(r["data"].date.unique()) for r in results]))
    if not avail:
        print("error: realizations share no report dates", file=sys.stderr)
        return 2
    start = dt.date.fromisoformat(info0["start"]) if info0.get("start") else None
    yearly_gaps = []
    if a.yearly:
        keep, yearly_gaps = yearly_dates(avail, a.date_tol_days, start)
        data = data[data.date.isin(keep)]
        avail = keep
    data = data[data.date.isin(avail)]
    info0 = dict(info0)
    if start not in avail:
        info0["initial_note"] = "no value at START; `first` is the first report step"
    try:
        picks = resolve_dates(a.dates.split(","), avail, info0, contract, a.date_tol_days)
    except Refuse as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code

    rkeys = (["realization"] if mode == "ensemble" else []) + ["region_set", "region", "vector"]
    data = add_change_columns(data, rkeys)
    qc = qc_sums(data, a.qc_rtol, a.qc_atol)
    empties = empty_regions(data)
    qc_status = "PASS" if (not qc.empty and (qc.status == "PASS").all()) else "FAIL"

    # ---- write
    cols = (["realization"] if mode == "ensemble" else []) + [
        "date",
        "source",
        "region_set",
        "region",
        "vector",
        "keyword",
        "value",
        "unit",
        "first_date",
        "change_since_first",
        "fraction_of_first",
    ]
    data = data[cols]
    data.to_csv(out / "region_in_place_long.csv", index=False)
    qc.to_csv(out / "qc_sum_check.csv", index=False)
    first_case = data[data.realization == results[0]["real"]] if mode == "ensemble" else data
    bal = field_balance(
        results[0]["base"], first_case, start if not info0.get("restart_without_history") else None
    )
    balmax = None
    if not bal.empty:
        bal.to_csv(out / "qc_field_balance.csv", index=False)
        balmax = float(bal.difference_rel_to_first_ip.abs().max())
    xcheck = info0.pop("prt_vs_summary", None)
    xmax = None
    if isinstance(xcheck, pd.DataFrame) and not xcheck.empty:
        xcheck.to_csv(out / "qc_prt_vs_summary.csv", index=False)
        xmax = float(xcheck.rel_diff.max())
    sheets: list[tuple[str, pd.DataFrame, bool]] = []
    if mode == "single":
        at = at_dates_table(data, picks)
        at.to_csv(out / "at_dates.csv", index=False)
        sheets.append(("at_dates", at, False))
        for fam in families:
            pv = pivot_sheet(data, fam)
            if pv.shape[1] > EXCEL_MAX_COLS:
                print(
                    f"note: {fam} has {pv.shape[1]} columns; Excel sheet skipped, CSV written",
                    file=sys.stderr,
                )
            else:
                sheets.append((fam, pv, True))
            pv.to_csv(out / f"pivot_{fam}.csv")
    else:
        st = stats(data, ["date", "region_set", "region", "vector", "unit"], conv, a.min_n_percentiles)
        st.insert(0, "p10_p90", conv)
        st.to_csv(out / "stats_all_dates.csv", index=False)
        pick_dates = {d: lab for lab, d in picks}
        atd = st[st.date.isin(pick_dates)].copy()
        atd.insert(1, "label", atd.date.map(pick_dates))
        atd.to_csv(out / "at_dates_stats.csv", index=False)
        data[data.date.isin(pick_dates)].to_csv(out / "at_dates_per_real.csv", index=False)
        sheets.append(("at_dates", atd, False))
        mean = st.rename(columns={"mean": "value"})
        for fam in families:
            pv = pivot_sheet(mean, fam)
            pv.to_csv(out / f"pivot_{fam}_mean.csv")
            if pv.shape[1] <= EXCEL_MAX_COLS:
                sheets.append((f"{fam}_mean", pv, True))
    unmapped = pd.DataFrame()
    if groups is not None:
        try:
            gdf, unmapped = apply_groups(data, groups, info0["regions"])
        except Refuse as exc:
            print(f"error: {exc}", file=sys.stderr)
            return exc.code
        gk = (["realization"] if mode == "ensemble" else []) + ["grouping", "group", "region_set", "vector"]
        gdf = add_change_columns(gdf, gk)
        gdf.to_csv(out / "groups_long.csv", index=False)
        unmapped.to_csv(out / "groups_unmapped.csv", index=False)
        if mode == "single":
            g2 = gdf.rename(columns={"group": "region"}).assign(
                region_set=lambda d: d.grouping + "/" + d.region_set
            )
            gat = at_dates_table(g2, picks).rename(
                columns={"region_set": "grouping/region_set", "region": "group"}
            )
            gat.to_csv(out / "groups_at_dates.csv", index=False)
            sheets.append(("groups_at_dates", gat, False))
            gp = gdf.assign(column=gdf.grouping + ": " + gdf.group).pivot_table(
                index="date", columns=["vector", "column"], values="value", aggfunc="first"
            )
            gp.columns = [f"{v} {c}" for v, c in gp.columns]
            sheets.append(("groups", gp, True))
        else:
            gs = stats(
                gdf, ["date", "grouping", "group", "region_set", "vector", "unit"], conv, a.min_n_percentiles
            )
            gs.insert(0, "p10_p90", conv)
            gs.to_csv(out / "groups_stats_all_dates.csv", index=False)
            gat = gs[gs.date.isin({d for _, d in picks})]
            gat.to_csv(out / "groups_at_dates_stats.csv", index=False)
            sheets.append(("groups_at_dates", gat, False))
        if not unmapped.empty:
            sheets.append(("groups_unmapped", unmapped, False))
    sheets.append(("qc_sum_check", qc, False))
    if len(data) <= EXCEL_MAX_ROWS:
        sheets.append(("long", data, False))
    units = data.groupby(["vector"])["unit"].first().to_dict()
    readme = [
        ("what", "Fluids in place at SURFACE (standard) conditions per region and report date"),
        ("mode", mode + (f" ({len(results)} realizations)" if mode == "ensemble" else "")),
        ("case(s)", str(cases[0][1]) + (f" ... ({len(cases)} cases)" if len(cases) > 1 else "")),
        ("source", "PRT balance reports" if a.from_prt else "SMSPEC/UNSMRY report steps"),
        (
            "initial (START) row source",
            str(info0.get("initial_source") or "none: no START value; first = first report step"),
        ),
        ("first / last date", f"{avail[0]} / {avail[-1]}"),
        ("selected dates", ", ".join(f"{label}={date}" for label, date in picks)),
        ("region sets", ", ".join(f"{s} ({len(info0['regions'][s])} regions)" for s in info0["region_sets"])),
        ("units", ", ".join(f"{k}: {v}" for k, v in units.items())),
        ("P10/P90 convention", conv_text if mode == "ensemble" else "n/a (single case)"),
        ("QC regions sum = field", qc_status + f" (rtol {a.qc_rtol}, atol {a.qc_atol}); see qc_sum_check"),
        (
            "PRT vs SUMMARY (max rel diff, shared dates)",
            "not run (summary mode)" if xmax is None else f"{xmax:.2e} (PRT prints whole numbers)",
        ),
        (
            "field balance (info): IP drop vs produced-injected",
            "not available"
            if balmax is None
            else f"max |diff| {balmax:.2e} of first IP; see qc_field_balance (aquifer, VAPOIL, oil injection or a wrong initial row explain gaps)",
        ),
        ("empty regions (OIP/GIP/WIP zero at all dates)", ", ".join(empties) or "none"),
        (
            "change_since_first",
            "value(t) - value(first date). Negative = less in place than at the first date.",
        ),
        (
            "caveat",
            "OIP drop in a region is NOT the oil produced from it when fluid crosses region boundaries "
            "(inter-region flow). 1 - OIP(t)/OIP(t0) is a recovery factor only for a closed region.",
        ),
        (
            "missing data",
            "Never filled with zero. A missing keyword stops the script (missing_vectors.json).",
        ),
    ]
    for k, v in (("OIP", "oil, liquid + vaporised"), ("GIP", "gas, free + dissolved"), ("WIP", "water")):
        if k in families:
            readme.append((f"F{k}/R{k}", v + ", surface conditions"))
    if yearly_gaps:
        readme.append(("yearly gaps (no 1 Jan step within tolerance)", ", ".join(map(str, yearly_gaps))))
    write_excel(out / "region_in_place.xlsx", sheets, readme)
    manifest = {
        "script": "region-in-place/region_in_place.py",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items()},
        "mode": mode,
        "n_cases": len(results),
        "failed": failed,
        "families": families,
        "units": units,
        "p10_p90": conv if mode == "ensemble" else None,
        "p10_p90_text": conv_text if mode == "ensemble" else None,
        "picks": [(label, date.isoformat()) for label, date in picks],
        "qc_status": qc_status,
        "empty_regions": empties,
        "yearly_gaps": yearly_gaps,
        "info": {k: v for k, v in info0.items() if k != "regions"},
        "regions": {k: len(v) for k, v in info0["regions"].items()},
        "unmapped_regions": len(unmapped),
        "prt_vs_summary_max_rel_diff": xmax,
        "field_balance_max_rel": balmax,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(
        f"wrote {out}/region_in_place.xlsx (+ CSVs). Mode {mode}, {len(results)} case(s), sets "
        f"{info0['region_sets']}, dates {avail[0]}..{avail[-1]}, initial source {info0.get('initial_source')}. "
        f"QC {qc_status}."
    )
    if not unmapped.empty:
        print(f"note: {len(unmapped)} region(s) not in any group: see groups_unmapped.csv", file=sys.stderr)
    if qc_status != "PASS":
        print(qc[qc.status != "PASS"].to_string(index=False), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
