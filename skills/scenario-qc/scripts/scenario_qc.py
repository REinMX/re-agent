#!/usr/bin/env python3
"""Compare a scenario vs baseline: fingerprint, runmeta, and profiles.

Flags (fail):
  fingerprint_mismatch  — runmeta claimed changes do not match fingerprint diff
  missing_vector        — required compare vector absent (never zero-fill)
  unexpected_silent     — fingerprint changed but field/touched-well results ≈ identical
  unexpected_jump       — large move on an untouched well (or field jump with
                          unchanged fingerprint)

Info (do not fail):
  expected_jump         — large move on a well/vector declared in runmeta
                          expected_changes (or coupled injector WWIR when enabled)

runmeta expected_changes (optional):
  wells: [<WELL>]
  keywords: [WCONPROD]           # when given, a changed keyword or added well not listed is fingerprint_mismatch
  field_vectors_may_move: [FOPT, FOPR, FWPT, FWPR]
  max_rel_delta: any | <float>   # optional; default any for expected wells

Contract scenario_qc.coupled_injectors_follow_producers: when true, WWIR on
injector wells is treated as expected if any producer is in expected_changes.wells.

Exit: 0 pass, 2 fail (QC gates), 1 unexpected error.

Usage:
  scenario_qc.py --contract site.contract.yml --scenario demo_schedule_tweak
  scenario_qc.py --contract ... --scenario /abs/path/to/scenarios/foo --outdir ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# Allow importing sibling deck_fingerprint when run as a script
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from deck_fingerprint import TRACKED_KEYWORDS, fingerprint_diff  # noqa: E402

_CONTRACT_LIB = Path(__file__).resolve().parents[2] / "stage-contract" / "scripts"
if str(_CONTRACT_LIB) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_LIB))
import contract_lib  # noqa: E402
from contract_lib import ContractError, add_contract_args  # noqa: E402

DEFAULT_COMPARE_VECTORS = [
    "FOPT",
    "FOPR",
    "FWPT",
    "FWPR",
    "WOPR",
    "WWPR",
    "WBHP",
    "WWCT",
]

# Keywords that imply wells, controls, groups, gas lift, network, actions, coupling or schedule changed
DYNAMIC_KEYWORDS = frozenset(TRACKED_KEYWORDS)


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        sys.stderr.write("error: PyYAML required\n")
        raise SystemExit(2) from None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected mapping in {path}")
    return data


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def mnemonic_family(key: str) -> str:
    return key.split(":", 1)[0]


def well_from_key(key: str) -> str | None:
    if ":" not in key:
        return None
    return key.split(":", 1)[1].upper()


def _root_candidates(contract: dict[str, Any]) -> list[Path]:
    """paths.scenarios parent, paths.root, then paths.project (and its parent when it ends with /project)."""
    paths = contract.get("paths") or {}
    out: list[Path] = []
    if paths.get("root"):
        out.append(Path(paths["root"]).resolve())
    if paths.get("project"):
        p_proj = Path(paths["project"]).resolve()
        out.append(p_proj.parent if p_proj.name == "project" else p_proj)
        out.append(p_proj)
    if not out:
        raise FileNotFoundError("contract needs paths.root or paths.project to find scenarios")
    return out


def resolve_scenario_dir(contract: dict[str, Any], scenario: str) -> Path:
    scen = Path(scenario)
    if scen.is_absolute() and scen.is_dir():
        return scen.resolve()
    paths = contract.get("paths") or {}
    if paths.get("scenarios"):
        cand = Path(paths["scenarios"]) / scenario
        if cand.is_dir():
            return cand.resolve()
    for base in _root_candidates(contract):
        for rel in (base / "scenarios" / scenario, base / scenario):
            if rel.is_dir():
                return rel.resolve()
    if scen.is_dir():
        return scen.resolve()
    raise FileNotFoundError(f"scenario not found: {scenario}")


def resolve_baseline_dir(contract: dict[str, Any]) -> Path:
    root_candidates = _root_candidates(contract)
    rel = contract["scenario_qc"]["baseline"]
    bp = Path(rel)
    if bp.is_dir():
        return bp.resolve()
    for base in root_candidates:
        cand = (base / rel).resolve()
        if cand.is_dir():
            return cand
    raise FileNotFoundError(f"baseline not found: {rel}")


def profiles_dir_for(scenario_dir: Path, runmeta: dict[str, Any]) -> Path:
    if runmeta.get("profiles_dir"):
        p = Path(runmeta["profiles_dir"])
        if not p.is_absolute():
            p = (scenario_dir / p).resolve()
        return p
    # default layout
    for cand in (
        scenario_dir / "deliverables" / "profiles",
        scenario_dir / "profiles",
    ):
        if cand.is_dir():
            return cand.resolve()
    raise FileNotFoundError(f"no profiles under {scenario_dir}")


def load_profiles(profiles_dir: Path) -> pd.DataFrame:
    for name in ("profiles_long.parquet", "profiles_long.csv", "field_profiles.csv"):
        path = profiles_dir / name
        if path.is_file():
            if path.suffix == ".parquet":
                df = pd.read_parquet(path)
            else:
                df = pd.read_csv(path)
            # If only field file, also try well
            if name.startswith("field_") and "KEY" in df.columns:
                well = profiles_dir / "well_profiles.csv"
                well_pq = profiles_dir / "well_profiles.parquet"
                if well_pq.is_file():
                    df = pd.concat([df, pd.read_parquet(well_pq)], ignore_index=True)
                elif well.is_file():
                    df = pd.concat([df, pd.read_csv(well)], ignore_index=True)
            required_cols = {"DATE", "KEY", "VALUE"}
            if not required_cols.issubset(df.columns):
                raise ValueError(f"{path} missing columns {required_cols - set(df.columns)}")
            return df
    raise FileNotFoundError(f"no profiles_long/field_profiles in {profiles_dir}")


def available_families(df: pd.DataFrame) -> set[str]:
    return {mnemonic_family(k) for k in df["KEY"].astype(str).unique()}


def _select_key_frame(df: pd.DataFrame, key: str) -> pd.DataFrame | None:
    sub = df[df["KEY"].astype(str) == key]
    if sub.empty:
        fam = mnemonic_family(key)
        matches = [k for k in df["KEY"].astype(str).unique() if mnemonic_family(k) == fam]
        if len(matches) == 1:
            sub = df[df["KEY"].astype(str) == matches[0]]
        else:
            return None
    return sub


def end_of_sim_mean(df: pd.DataFrame, key: str) -> float | None:
    """Mean across REAL of last VALUE for KEY (or family:well). NaN-safe."""
    sub = _select_key_frame(df, key)
    if sub is None or sub.empty:
        return None
    if "REAL" in sub.columns:
        last = sub.sort_values("DATE").groupby("REAL", sort=False).tail(1)
        vals = pd.to_numeric(last["VALUE"], errors="coerce")
        if vals.isna().all():
            return None
        return float(vals.mean())
    last = sub.sort_values("DATE").tail(1)
    return float(pd.to_numeric(last["VALUE"], errors="coerce").iloc[0])


def series_mean(df: pd.DataFrame, key: str) -> float | None:
    """Mean across REAL and DATE (catches rate-profile changes when EOS ~ 0)."""
    sub = _select_key_frame(df, key)
    if sub is None or sub.empty:
        return None
    vals = pd.to_numeric(sub["VALUE"], errors="coerce")
    if vals.isna().all():
        return None
    return float(vals.mean())


def is_cumulative_key(key: str) -> bool:
    fam = mnemonic_family(key).upper()
    return fam.endswith("T") and not fam.endswith("CT")  # FOPT/FWPT yes; WWCT no


def keys_for_family(df: pd.DataFrame, family: str) -> list[str]:
    return sorted(k for k in df["KEY"].astype(str).unique() if mnemonic_family(k) == family)


def relative_delta(a: float, b: float) -> float:
    """|b-a| / max(|a|, eps)."""
    denom = max(abs(a), 1e-12)
    return abs(b - a) / denom


def claimed_change_keywords(runmeta: dict[str, Any]) -> set[str]:
    changes = runmeta.get("changes") or {}
    claimed: set[str] = set()
    for section in ("wells", "constraints", "schedule", "keywords"):
        val = changes.get(section)
        if not val:
            continue
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.upper() in DYNAMIC_KEYWORDS:
                    claimed.add(item.upper())
                elif isinstance(item, dict) and "keyword" in item:
                    claimed.add(str(item["keyword"]).upper())
                elif isinstance(item, str):
                    # free text — map common tokens
                    u = item.upper()
                    for kw in DYNAMIC_KEYWORDS:
                        if kw in u:
                            claimed.add(kw)
        elif isinstance(val, str) and val.strip() and val.strip().lower() not in ("none", "no", ""):
            u = val.upper()
            for kw in DYNAMIC_KEYWORDS:
                if kw in u:
                    claimed.add(kw)
            if not claimed and val.strip().lower() not in ("none", "no"):
                # opaque claim counts as "something claimed"
                claimed.add("CLAIMED_OPAQUE")
    # explicit list
    for item in runmeta.get("changed_keywords") or []:
        claimed.add(str(item).upper())
    return claimed


def touched_wells_from_meta(runmeta: dict[str, Any], fp_diff: dict[str, Any]) -> set[str]:
    wells: set[str] = set()
    for w in runmeta.get("touched_wells") or []:
        wells.add(str(w).upper())
    changes = runmeta.get("changes") or {}
    for section in ("wells",):
        val = changes.get(section)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and re_well(item):
                    wells.add(item.upper())
                elif isinstance(item, dict) and item.get("well"):
                    wells.add(str(item["well"]).upper())
    # fingerprint well add/remove
    for w in fp_diff.get("wells_added") or []:
        wells.add(w)
    for w in fp_diff.get("wells_removed") or []:
        wells.add(w)
    return wells


def re_well(name: str) -> bool:
    import re

    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_\-]*$", name)) and name.upper() not in DYNAMIC_KEYWORDS


def parse_expected_changes(runmeta: dict[str, Any]) -> dict[str, Any]:
    """Parse runmeta.expected_changes; empty dict if absent."""
    raw = runmeta.get("expected_changes") or {}
    if not isinstance(raw, dict):
        return {"wells": set(), "keywords": set(), "field_vectors_may_move": set(), "max_rel_delta": None}
    wells = {str(w).upper() for w in (raw.get("wells") or [])}
    keywords = {str(k).upper() for k in (raw.get("keywords") or [])}
    field_may = {str(v).upper() for v in (raw.get("field_vectors_may_move") or [])}
    max_rel = raw.get("max_rel_delta")
    if max_rel is not None and str(max_rel).lower() != "any":
        try:
            max_rel = float(max_rel)
        except (TypeError, ValueError):
            max_rel = None
    else:
        max_rel = None  # None means any magnitude allowed for expected
    return {
        "wells": wells,
        "keywords": keywords,
        "field_vectors_may_move": field_may,
        "max_rel_delta": max_rel,
    }


def injector_wells_from_profiles(df: pd.DataFrame) -> set[str]:
    """Wells that have WWIR (or WWIT) vectors — treated as injectors for coupling."""
    out: set[str] = set()
    for k in df["KEY"].astype(str).unique():
        fam = mnemonic_family(k).upper()
        if fam in ("WWIR", "WWIT", "WGIR", "WGIT") and ":" in k:
            w = well_from_key(k)
            if w:
                out.add(w)
    return out


def producer_wells_from_profiles(df: pd.DataFrame) -> set[str]:
    out: set[str] = set()
    for k in df["KEY"].astype(str).unique():
        fam = mnemonic_family(k).upper()
        if fam in ("WOPR", "WOPT", "WGPR", "WWPR") and ":" in k:
            w = well_from_key(k)
            if w:
                out.add(w)
    return out


def run_qc(
    contract: dict[str, Any],
    scenario_dir: Path,
    baseline_dir: Path,
) -> dict[str, Any]:
    sq = contract["scenario_qc"]
    compare_vectors = list(sq.get("compare_vectors") or DEFAULT_COMPARE_VECTORS)
    headline = str(sq.get("headline_vector") or "FOPT").upper()  # FGPT for a gas field
    coupled_vectors = [str(v).upper() for v in (sq.get("coupled_vectors") or ["WWIR", "WBHP"])]
    tol = sq.get("tolerances") or {}
    field_tol = float(tol.get("field_headline_end_rel", tol.get("field_fopt_end_rel", 0.02)))
    untouched_tol = float(tol.get("untouched_well_rel", 0.01))
    require_fp_meta = bool(sq.get("require_fingerprint_match_runmeta", True))
    fail_missing = bool(sq.get("fail_on_missing_vector", True))
    couple_inj = bool(sq.get("coupled_injectors_follow_producers", False))

    scen_fp_path = scenario_dir / "deck_fingerprint.json"
    base_fp_path = baseline_dir / "deck_fingerprint.json"
    scen_meta_path = scenario_dir / "runmeta.yml"
    base_meta_path = baseline_dir / "runmeta.yml"

    for p in (scen_fp_path, base_fp_path, scen_meta_path, base_meta_path):
        if not p.is_file():
            raise FileNotFoundError(f"required scenario file missing: {p}")

    scen_fp = load_json(scen_fp_path)
    base_fp = load_json(base_fp_path)
    scen_meta = load_yaml(scen_meta_path)
    base_meta = load_yaml(base_meta_path)

    fp_diff = fingerprint_diff(base_fp, scen_fp)
    claimed = claimed_change_keywords(scen_meta)
    actual_changed = set(fp_diff["changed_keywords"])

    flags: list[str] = []
    messages: list[str] = []
    checks: dict[str, Any] = {}

    # --- fingerprint vs runmeta ---
    fingerprint_mismatch = False
    comparable = fp_diff["schema_equal"] and fp_diff["dates_comparable"]
    if not comparable:
        actual_changed = set()  # the keyword diff means nothing; fail closed with one message below
    if require_fp_meta and comparable:
        claimed_dynamic = {c for c in claimed if c != "CLAIMED_OPAQUE"}
        if claimed and not actual_changed and "CLAIMED_OPAQUE" not in claimed:
            # claimed specific keywords but fingerprint identical
            fingerprint_mismatch = True
            messages.append(
                f"runmeta claims changes {sorted(claimed_dynamic or claimed)} "
                f"but fingerprint combined_sha256 equals baseline"
            )
        elif claimed_dynamic and not (claimed_dynamic & actual_changed) and "CLAIMED_OPAQUE" not in claimed:
            fingerprint_mismatch = True
            messages.append(
                f"runmeta claimed keywords {sorted(claimed_dynamic)} "
                f"do not overlap fingerprint diff {sorted(actual_changed)}"
            )
        elif not claimed and actual_changed:
            fingerprint_mismatch = True
            messages.append(
                f"fingerprint changed keywords {sorted(actual_changed)} but runmeta.changes is empty/none"
            )
        # CLAIMED_OPAQUE with actual change is OK; opaque with no change is mismatch
        if "CLAIMED_OPAQUE" in claimed and not actual_changed:
            fingerprint_mismatch = True
            messages.append("runmeta claims a change but fingerprint equals baseline")

    declared = parse_expected_changes(scen_meta)
    if declared["keywords"]:
        undeclared = sorted(actual_changed - {k.upper() for k in declared["keywords"]})
        if undeclared:
            fingerprint_mismatch = True
            messages.append(
                f"fingerprint changed {undeclared}, which expected_changes.keywords does not declare"
            )
        new_wells = sorted(set(fp_diff["wells_added"]) - {w.upper() for w in declared["wells"]})
        if new_wells:
            fingerprint_mismatch = True
            messages.append(
                f"fingerprint adds wells {new_wells}, which expected_changes.wells does not declare"
            )
    if not fp_diff["schema_equal"]:
        fingerprint_mismatch = True
        messages.append(
            f"fingerprint schema differs (baseline {base_fp.get('schema_version')}, scenario "
            f"{scen_fp.get('schema_version')}); rebuild both with the same deck_fingerprint.py"
        )
    elif not fp_diff["dates_comparable"]:
        fingerprint_mismatch = True
        messages.append(
            f"one fingerprint has dates and the other does not (baseline START {base_fp.get('start')}, scenario "
            f"START {scen_fp.get('start')}); rebuild both from decks whose START resolves"
        )
    if fingerprint_mismatch:
        flags.append("fingerprint_mismatch")
    checks["fingerprint"] = {
        "combined_equal": fp_diff["combined_equal"],
        "dates_comparable": fp_diff["dates_comparable"],
        "changed_keywords": fp_diff["changed_keywords"],
        "changed_families": fp_diff["changed_families"],
        "claimed_keywords": sorted(claimed),
        "mismatch": fingerprint_mismatch,
    }

    # --- profiles ---
    scen_profiles = load_profiles(profiles_dir_for(scenario_dir, scen_meta))
    base_profiles = load_profiles(profiles_dir_for(baseline_dir, base_meta))

    fam_scen = available_families(scen_profiles)
    fam_base = available_families(base_profiles)

    missing: list[str] = []
    for name in compare_vectors:
        in_scen = name in fam_scen or any(
            mnemonic_family(k) == name for k in scen_profiles["KEY"].astype(str)
        )
        in_base = name in fam_base or any(
            mnemonic_family(k) == name for k in base_profiles["KEY"].astype(str)
        )
        # Also accept exact KEY
        if name not in fam_scen and not any(
            k == name or mnemonic_family(k) == name for k in scen_profiles["KEY"].astype(str).unique()
        ):
            in_scen = False
        else:
            in_scen = True
        if name not in fam_base and not any(
            k == name or mnemonic_family(k) == name for k in base_profiles["KEY"].astype(str).unique()
        ):
            in_base = False
        else:
            in_base = True
        if not in_scen or not in_base:
            missing.append(name)

    if missing and fail_missing:
        flags.append("missing_vector")
        messages.append(f"missing compare vectors (never zero-fill): {missing}")
    checks["missing_vectors"] = missing
    checks["compare_vectors"] = compare_vectors
    checks["present_families_scenario"] = sorted(fam_scen)
    checks["present_families_baseline"] = sorted(fam_base)

    # Field deltas
    field_report: dict[str, Any] = {}
    field_fams = list(dict.fromkeys([headline, "FOPT", "FWPT", "FOPR", "FWPR", "FGPT", "FGPR"]))
    for fam in field_fams:
        if fam not in compare_vectors:
            continue
        if fam in missing:
            continue
        # prefer exact field key
        keys = [k for k in keys_for_family(scen_profiles, fam) if ":" not in k]
        if not keys:
            continue
        key = keys[0]
        use_eos = is_cumulative_key(key)
        metric = end_of_sim_mean if use_eos else series_mean
        b = metric(base_profiles, key)
        s = metric(scen_profiles, key)
        if b is None or s is None:
            continue
        rel = relative_delta(b, s)
        field_report[key] = {
            "metric": "end_of_sim_mean" if use_eos else "series_mean",
            "baseline_end_mean": b,
            "scenario_end_mean": s,
            "rel_delta": rel,
            "abs_delta": s - b,
        }

    checks["field"] = field_report

    expected = parse_expected_changes(scen_meta)
    touched = touched_wells_from_meta(scen_meta, fp_diff)
    # expected_changes.wells always count as touched for jump classification
    touched |= expected["wells"]
    # If fingerprint changed WCON*/COMPDAT but no wells listed, treat all shared wells as potentially touched
    if actual_changed & DYNAMIC_KEYWORDS and not touched:
        touched = set(fp_diff.get("wells_shared") or []) | set(fp_diff.get("wells_added") or [])

    all_wells: set[str] = set()
    for k in list(scen_profiles["KEY"].astype(str).unique()) + list(
        base_profiles["KEY"].astype(str).unique()
    ):
        w = well_from_key(k)
        if w:
            all_wells.add(w)

    # Voidage coupling: injectors may move when producers are expected
    coupled_injectors: set[str] = set()
    if couple_inj and expected["wells"]:
        producers = producer_wells_from_profiles(scen_profiles) | producer_wells_from_profiles(base_profiles)
        injectors = injector_wells_from_profiles(scen_profiles) | injector_wells_from_profiles(base_profiles)
        if expected["wells"] & producers:
            coupled_injectors = injectors - expected["wells"]
            touched |= coupled_injectors

    untouched = all_wells - touched
    # Wells explicitly expected (not merely coupled) for expected_jump messaging
    expected_wells = set(expected["wells"]) | coupled_injectors
    expected_max = expected["max_rel_delta"]  # None => any
    field_may_move = expected["field_vectors_may_move"]

    well_report: dict[str, Any] = {}
    unexpected_jump = False
    expected_jump = False
    info_flags: list[str] = []
    info_messages: list[str] = []

    scen_keys = set(scen_profiles["KEY"].astype(str))
    base_keys = set(base_profiles["KEY"].astype(str))

    for well in sorted(all_wells):
        is_expected = well in expected_wells
        is_coupled = well in coupled_injectors
        well_entry: dict[str, Any] = {
            "touched": well in touched,
            "expected": is_expected and not is_coupled,
            "coupled_injector": is_coupled,
            "vectors": {},
        }
        for fam in list(
            dict.fromkeys(["WOPR", "WWPR", "WGPR", "WBHP", "WWCT", "WWIR", "WGIR", *coupled_vectors])
        ):
            if fam not in compare_vectors and fam not in coupled_vectors:
                continue
            # Coupled injectors: only WWIR (voidage) is auto-expected, not arbitrary vectors
            key = f"{fam}:{well}"
            if key not in scen_keys and key not in base_keys:
                continue
            use_eos = is_cumulative_key(key)
            metric = end_of_sim_mean if use_eos else series_mean
            b = metric(base_profiles, key)
            s = metric(scen_profiles, key)
            if b is None or s is None:
                continue
            rel = relative_delta(b, s)
            well_entry["vectors"][key] = {
                "metric": "end_of_sim_mean" if use_eos else "series_mean",
                "baseline_end_mean": b,
                "scenario_end_mean": s,
                "rel_delta": rel,
            }
            if rel <= untouched_tol:
                continue

            # Classify large delta
            vector_expected = False
            if well in expected["wells"]:
                vector_expected = True
            elif is_coupled and fam in coupled_vectors:
                # Injection rate / BHP on a coupled injector moves with the producers
                # (scenario_qc.coupled_vectors, default WWIR + WBHP).
                vector_expected = True

            if vector_expected:
                if expected_max is not None and rel > expected_max:
                    unexpected_jump = True
                    messages.append(
                        f"unexpected_jump: expected well {well} {key} rel_delta={rel:.4f} "
                        f"> expected_changes.max_rel_delta={expected_max}"
                    )
                else:
                    expected_jump = True
                    info_messages.append(
                        f"expected_jump: {well} {key} rel_delta={rel:.4f}"
                        + (" (coupled injector)" if is_coupled and fam in coupled_vectors else "")
                    )
            elif well in untouched:
                unexpected_jump = True
                messages.append(
                    f"unexpected_jump: untouched well {well} {key} rel_delta={rel:.4f} "
                    f"> tolerance {untouched_tol}"
                )
        well_report[well] = well_entry

    checks["touched_wells"] = sorted(touched)
    checks["untouched_wells"] = sorted(untouched)
    checks["expected_wells"] = sorted(expected["wells"])
    checks["coupled_injectors"] = sorted(coupled_injectors)
    checks["expected_changes"] = {
        "wells": sorted(expected["wells"]),
        "keywords": sorted(expected["keywords"]),
        "field_vectors_may_move": sorted(field_may_move),
        "max_rel_delta": expected_max if expected_max is not None else "any",
        "coupled_injectors_follow_producers": couple_inj,
    }
    checks["wells"] = well_report

    # Field vectors declared may_move: record info if they moved a lot
    for fam, stats in list(field_report.items()):
        fam_u = mnemonic_family(fam).upper()
        if fam_u in field_may_move and stats["rel_delta"] > field_tol:
            expected_jump = True
            info_messages.append(
                f"expected_jump: field {fam} rel_delta={stats['rel_delta']:.4f} (in field_vectors_may_move)"
            )

    # unexpected_silent: fingerprint changed (dynamic) but the headline field vector ~ identical
    unexpected_silent = False
    if actual_changed & DYNAMIC_KEYWORDS:
        fopt = field_report.get(headline)
        if fopt is not None and fopt["rel_delta"] <= field_tol:
            # also check touched well rates nearly flat
            silent_wells = True
            for well in touched or all_wells:
                wr = well_report.get(well, {}).get("vectors") or {}
                for _k, stats in wr.items():
                    if stats["rel_delta"] > field_tol:
                        silent_wells = False
                        break
                if not silent_wells:
                    break
            if silent_wells:
                unexpected_silent = True
                messages.append(
                    f"unexpected_silent: fingerprint changed {sorted(actual_changed & DYNAMIC_KEYWORDS)} "
                    f"but results within field_tol={field_tol}"
                )

    # Field jump when fingerprint says nothing changed (and headline not declared may_move)
    if fp_diff["combined_equal"] and not claimed:
        fopt = field_report.get(headline)
        if fopt is not None and fopt["rel_delta"] > field_tol:
            if headline in field_may_move:
                expected_jump = True
                info_messages.append(
                    f"expected_jump: {headline} rel_delta={fopt['rel_delta']:.4f} "
                    f"(field_vectors_may_move; fingerprint unchanged — unusual)"
                )
            else:
                unexpected_jump = True
                messages.append(
                    f"unexpected_jump: fingerprint unchanged but {headline} rel_delta={fopt['rel_delta']:.4f}"
                )

    if unexpected_silent:
        flags.append("unexpected_silent")
    if unexpected_jump:
        flags.append("unexpected_jump")
    if expected_jump:
        info_flags.append("expected_jump")

    # Pass/fail — info_flags do not fail
    fail_flags = set(flags)
    passed = len(fail_flags) == 0
    status = "PASS" if passed else "FAIL"

    report = {
        "skill": "scenario-qc",
        "status": status,
        "flags": flags,
        "info_flags": info_flags,
        "messages": messages,
        "info_messages": info_messages,
        "p10_p90": contract.get("p10_p90"),
        "p10_p90_note": (
            "numerical percentiles (low=P10, high=P90)"
            if contract.get("p10_p90") == "numerical"
            else "exceedance"
        ),
        "scenario_dir": str(scenario_dir),
        "baseline_dir": str(baseline_dir),
        "scenario_id": scen_meta.get("scenario_id") or scenario_dir.name,
        "parent_scenario_id": scen_meta.get("parent_scenario_id"),
        "checks": checks,
        "tolerances": {"field_fopt_end_rel": field_tol, "untouched_well_rel": untouched_tol},
    }
    return report


def write_report(report: dict[str, Any], outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "scenario_qc_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        f"# Scenario QC: {report['scenario_id']}",
        "",
        f"- Status: **{report['status']}**",
        f"- Flags: {', '.join(report['flags']) if report['flags'] else '(none)'}",
        f"- Info flags: {', '.join(report.get('info_flags') or []) or '(none)'}",
        f"- P10/P90 convention: {report['p10_p90']} ({report['p10_p90_note']})",
        f"- Baseline: `{report['baseline_dir']}`",
        f"- Scenario: `{report['scenario_dir']}`",
        "",
        "## Messages",
    ]
    if report["messages"]:
        for m in report["messages"]:
            lines.append(f"- {m}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Info messages")
    if report.get("info_messages"):
        for m in report["info_messages"]:
            lines.append(f"- {m}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Fingerprint")
    fp = report["checks"]["fingerprint"]
    lines.append(f"- combined_equal: {fp['combined_equal']}")
    lines.append(f"- changed_keywords: {fp['changed_keywords']}")
    lines.append(f"- claimed_keywords: {fp['claimed_keywords']}")
    lines.append("")
    lines.append("## Missing vectors")
    lines.append(f"- {report['checks']['missing_vectors'] or '(none)'}")
    lines.append("")
    lines.append("## Field end-of-sim (ensemble mean)")
    for k, v in (report["checks"].get("field") or {}).items():
        lines.append(
            f"- {k}: baseline={v['baseline_end_mean']:.6g} scenario={v['scenario_end_mean']:.6g} "
            f"rel_delta={v['rel_delta']:.4g}"
        )
    lines.append("")
    lines.append("## Wells")
    lines.append(f"- touched: {report['checks'].get('touched_wells')}")
    lines.append(f"- untouched: {report['checks'].get('untouched_wells')}")
    (outdir / "scenario_qc_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scenario QC vs baseline.")
    add_contract_args(parser)
    parser.add_argument("--scenario", required=True, help="Scenario id or path")
    parser.add_argument("--baseline", default=None, help="Override baseline path")
    parser.add_argument("--outdir", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        contract = contract_lib.load_contract(args.contract, args.profile)
        if not isinstance(contract.get("scenario_qc"), dict):
            raise ContractError("contract key `scenario_qc` is required for scenario QC")
    except ContractError as exc:
        sys.stderr.write(f"error: contract: {exc}\n")
        return 2

    try:
        scenario_dir = resolve_scenario_dir(contract, args.scenario)
        if args.baseline:
            baseline_dir = Path(args.baseline).resolve()
        else:
            baseline_dir = resolve_baseline_dir(contract)
        report = run_qc(contract, scenario_dir, baseline_dir)
    except FileNotFoundError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: scenario_qc failed: {exc}\n")
        return 1

    if args.outdir:
        outdir = args.outdir
    else:
        outdir = scenario_dir / "deliverables" / "scenario_qc"
    write_report(report, outdir)

    sys.stdout.write(
        f"scenario_qc: {report['status']} id={report['scenario_id']} "
        f"flags={report['flags'] or []} info={report.get('info_flags') or []} "
        f"wrote={outdir}\n"
    )
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
