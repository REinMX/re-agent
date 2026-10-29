#!/usr/bin/env python3
"""Plot-first status report of an ES or ES-MDA history match from a scratch case. Read-only on the case.

    ahm_status.py --case <scratch case with realization-*/iter-*> [--obs <file.obs> ...] \
        [--ert-config <file.ert>] [--dist <GEN_KW .dist> ...] [--eclbase 'eclipse/model/NAME-<IENS>'] \
        [--expected N] [--outdir ./ahm_status | --write-share] [--title TEXT] [--wells P1,P2]

The posterior is the highest iteration on disk. The prior is iter-0, only when it is on disk. Without it the
report runs in posterior-only mode: it says that iter-0 is gone and shows no prior comparison. It never makes a
prior. With --ert-config it reads NUM_REALIZATIONS, ECLBASE, OBS_CONFIG, the GEN_KW .dist files and the ERT
storage (ENSPATH): the iterations ERT stored and the ES-MDA weights. Runpaths always come from --case.

A realization is ok when its summary loads. It is failed when the runpath exists without a loadable summary
(the ERROR file gives the reason). It is missing when the runpath does not exist.

The numbers come from the history-match-qc scripts: misfit_report.py (misfit, chi-square, coverage, findings),
residual_correlation.py (bias and variance per key) and compensation_check.py (with a prior). The AHM settings
lint (check_ert_config.py --ahm-only) and the physics checklist add review items. A SUMMARY vector that the case
does not have is reported as missing, never as 0. P10/P50/P90 are numerical: P10 is the low value.

Outputs in --outdir (default ./ahm_status), or in <case>/share/results/ahm_status with --write-share:
summary.md, report.md, report.pdf, status.json, plots/*.png, tables/*.csv, misfit/ (misfit_report folders).
The verdict is advisory. It never passes a decision gate and makes no reserves or concept call.
Exit code: 0 = report written, 1 = input problem, 2 = usage error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
HMQC = SKILLS / "history-match-qc" / "scripts"
sys.path.insert(0, str(SKILLS / "stage-contract" / "scripts"))
import runpath_lib  # noqa: E402


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(path.stem, module)
    spec.loader.exec_module(module)
    return module


obs_qc = _load(HMQC / "obs_qc.py")
compensation = _load(HMQC / "compensation_check.py")
prior_qc = compensation.prior_qc
ert_config_map = _load(SKILLS / "ert-config-author-lint" / "scripts" / "ert_config_map.py")

CONVENTION = "P10/P50/P90 convention: numerical, P10 is the LOW value."
FIELD_PANELS = (("FOPR",), ("FOPT",), ("FWPR", "FWCT"), ("FGPR", "FGOR"), ("FWIR",), ("FPR",))
HEADLINE = ("FOPT", "FWPT", "FGPT", "FPR")
WELL_VECTORS = ("WOPR", "WWCT", "WGOR", "WBHP")
PRESSURE_ORDER = ("WBP9", "WBP4", "WBP", "WBHP")
FORECAST = ("FOPT", "FGPT", "FWPT", "FWIT")
REQUIRED_FIELD = ("FOPT", "FGPT", "FWPT")
HISTORY_CONTROLLED = "history-controlled (zero spread)"
FIELD_MNEMONICS = {m for panel in FIELD_PANELS for m in panel} | set(HEADLINE) | set(FORECAST)
WELL_MNEMONICS = set(WELL_VECTORS) | set(PRESSURE_ORDER) | {"WWIR", "WWPR", "WGPR"}
WANTED = FIELD_MNEMONICS | WELL_MNEMONICS
WELL_LIMIT = 4.0
COVERAGE_LOW, COVERAGE_HIGH, COVERAGE_FAIL, COVERAGE_MISFIT = 0.80, 0.97, 0.6, 0.8
OK_SHARE_MIN = 0.8
CALIBRATION_CAVEAT = (
    "Calibration on a known-good drogon-lite setup (2026-10-28): the misfit chi-square 'above the noise band', "
    "the lint review of the member count, and compensation EDGE all fired there as false alarms. "
    "They stay in the report and do not count toward the verdict (decision 2026-10-28)."
)
ADVISORY = "Advisory only. This verdict never passes DG0 or any decision gate, and makes no reserves or concept call."


class RealStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    MISSING = "missing"


@dataclass(frozen=True)
class Realization:
    status: RealStatus
    runpath: Path | None = None
    summary: Path | None = None
    reason: str = ""


@dataclass
class Case:
    path: Path
    expected: tuple[int, ...]
    expected_source: str
    iterations: dict[int, dict[int, Realization]]
    stored: dict[int, int] = field(default_factory=dict)
    weights: str = "unknown"

    @property
    def posterior(self) -> int:
        return max(self.iterations)

    @property
    def prior(self) -> int | None:
        return 0 if 0 in self.iterations and self.posterior != 0 else None

    @property
    def mode(self) -> str:
        return "posterior-only" if self.prior is None else "full"

    @property
    def prior_deleted(self) -> bool:
        return self.prior is None and self.posterior != 0

    @property
    def no_prior_note(self) -> str:
        if self.prior_deleted:
            return "iter-0 (prior) is gone"
        return "only one iteration on disk (iter-0), so there is no earlier ensemble to compare with"

    def members(self, iteration: int, status: RealStatus = RealStatus.OK) -> list[int]:
        return sorted(r for r, real in self.iterations[iteration].items() if real.status == status)


@dataclass(frozen=True)
class ReviewItem:
    source: str
    code: str
    text: str
    calibration_false_alarm: bool = False
    info: bool = False
    not_counted_reason: str = ""
    level: str = "REVIEW"

    @property
    def counts(self) -> bool:
        return not (self.calibration_false_alarm or self.info or self.not_counted_reason)

    @property
    def label(self) -> str:
        if self.info:
            return "INFO"
        if self.calibration_false_alarm:
            return f"{self.level} (known false alarm: shown, not counted in the verdict)"
        if self.not_counted_reason:
            return f"{self.level} ({self.not_counted_reason})"
        return self.level


UNCALIBRATED_LINT = "lint, not calibrated: shown, not counted"
DESIGN_CHOICE = "design choice, shown, not counted: official Drogon has neither"
NOT_RUN = "check could not run: shown, not counted"
DESIGN_CHOICE_MISSING = {"PHYS-AQUIFER", "PHYS-WELL-PI"}


@dataclass
class ErtSettings:
    config: Path
    num_realizations: int | None = None
    eclbase: str | None = None
    obs_files: list[Path] = field(default_factory=list)
    dist_files: list[Path] = field(default_factory=list)
    gen_kw: int = 0
    enspath: Path | None = None


# ---------------------------------------------------------------- inputs


def read_ert_config(path: Path) -> ErtSettings:
    mapper = ert_config_map.Mapper(path)
    mapper.parse(mapper.root)
    config_dir = mapper.root.parent

    def setting(name: str) -> str | None:
        item = mapper.settings.get(name)
        return mapper.substitute(item["value"], config_dir) if item else None

    out = ErtSettings(config=path)
    if (value := setting("NUM_REALIZATIONS")) and value.isdigit():
        out.num_realizations = int(value)
    out.eclbase = setting("ECLBASE")
    if obs := setting("OBS_CONFIG"):
        out.obs_files = [(config_dir / obs).resolve()]
    if enspath := setting("ENSPATH"):
        out.enspath = (config_dir / enspath).resolve()
    for item in mapper.parameters:
        if item["keyword"] == "GEN_KW":
            out.gen_kw += 1
            if dist := gen_kw_dist(item["options"]):
                out.dist_files.append((Path(item["file"]).parent / dist).resolve())
    return out


def gen_kw_dist(options: list[str]) -> str | None:
    """The distribution file of GEN_KW NAME dist, or GEN_KW NAME template output dist, after KEY:value options."""
    positional = [o for o in options if not re.match(r"[A-Z_]+[:=]", o)]
    if len(positional) == 1:
        return positional[0]
    if len(positional) >= 3:
        return positional[2]
    return None


def _find_key(data, key: str):
    if isinstance(data, dict):
        if key in data:
            return data[key]
        data = list(data.values())
    if isinstance(data, list):
        for value in data:
            if (found := _find_key(value, key)) is not None:
                return found
    return None


def read_storage(enspath: Path | None) -> tuple[dict[int, int], str]:
    """Iterations stored by ERT (iteration -> ensemble_size) and the ES-MDA weights text."""
    if enspath is None or not enspath.is_dir():
        return {}, "unknown"
    stored: dict[int, int] = {}
    for index in sorted(enspath.glob("ensembles/*/index.json")):
        data = json.loads(index.read_text())
        if isinstance(data.get("iteration"), int):
            stored[data["iteration"]] = int(data.get("ensemble_size") or 0)
    weights = "unknown"
    for index in sorted(enspath.glob("experiments/*/index.json")):
        found = _find_key(json.loads(index.read_text()), "weights")
        if found:
            weights = found if isinstance(found, str) else ", ".join(str(w) for w in found)
    return stored, weights


def summary_loads(stem: Path) -> bool:
    try:
        from resdata.summary import Summary

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            Summary(str(stem))
        return True
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def classify(runpath: Path, eclbase: str | None, real: int) -> Realization:
    data, reason = runpath_lib.data_file(runpath, eclbase, real)
    error = runpath / "ERROR"
    if error.is_file():
        lines = error.read_text(errors="replace").strip().splitlines()
        reason = f"ERROR file: {lines[0] if lines else 'empty'}"
    if data is not None:
        stem = data.with_suffix("")
        if (
            data.with_suffix(".SMSPEC").is_file()
            and data.with_suffix(".UNSMRY").is_file()
            and summary_loads(stem)
        ):
            return Realization(RealStatus.OK, runpath, stem)
        reason = reason or f"no loadable summary for {data.stem}"
    return Realization(RealStatus.FAILED, runpath, None, reason or "no loadable summary")


def scan_case(
    case: Path,
    eclbase: str | None,
    expected: int | None,
    settings: ErtSettings | None,
    stored: dict[int, int],
) -> Case:
    found: dict[int, dict[int, Path]] = {}
    for path in sorted(case.glob("realization-*/iter-*")):
        real = runpath_lib.realization_number(path)
        match = re.fullmatch(r"iter-(\d+)", path.name)
        if path.is_dir() and real is not None and match:
            found.setdefault(int(match.group(1)), {})[real] = path
    if not found:
        raise ValueError(f"no realization-*/iter-* runpath in {case}")
    if expected is not None:
        members, source = tuple(range(expected)), "--expected"
    elif settings is not None and settings.num_realizations:
        members, source = tuple(range(settings.num_realizations)), "NUM_REALIZATIONS in the ERT config"
    elif stored:
        members, source = tuple(range(max(stored.values()))), "ensemble_size in the ERT storage"
    else:
        members = tuple(sorted({r for reals in found.values() for r in reals}))
        source = "realization folders on disk (no expected count given)"
    iterations: dict[int, dict[int, Realization]] = {}
    for iteration, reals in sorted(found.items()):
        iterations[iteration] = {
            real: classify(reals[real], eclbase, real) if real in reals else Realization(RealStatus.MISSING)
            for real in members
        }
    return Case(case, members, source, iterations)


def read_observations(paths: list[Path]) -> list[dict]:
    out = []
    for path in paths:
        for record in obs_qc.parse_file(path):
            if record["kind"] == "SUMMARY_OBSERVATION":
                obs = obs_qc.normalize_summary(record)
                if obs["key"] and obs["date"] and obs["error"]:
                    out.append(obs)
    return out


def load_frames(case: Case, iteration: int, observed: set[str] = frozenset()):
    """Summary of each ok member as a DATE x vector frame, only the vectors this report uses or observes."""
    from resdata.summary import Summary

    frames: dict[int, pd.DataFrame] = {}
    for real in case.members(iteration):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            summary = Summary(str(case.iterations[iteration][real].summary))
        keys = [k for k in summary.keys() if k.split(":")[0].removesuffix("H") in WANTED or k in observed]
        frame = summary.pandas_frame(column_keys=keys)
        frame.index = pd.to_datetime(frame.index)
        frame.attrs["units"] = {key: summary.unit(key) for key in keys}
        frames[real] = frame
    return frames


def read_parameters(case: Case, iteration: int) -> dict[int, dict[str, float]]:
    out = {}
    for real in case.members(iteration):
        file = case.iterations[iteration][real].runpath / "parameters.txt"
        if file.is_file():
            values = prior_qc.read_truth(file)
            out[real] = {k: v for k, v in values.items() if not k.startswith("LOG10_")}
    return out


# ---------------------------------------------------------------- tools of history-match-qc


@dataclass
class MisfitRun:
    folder: Path
    manifest: dict
    residuals: pd.DataFrame
    obs_summary: pd.DataFrame
    real_misfit: pd.DataFrame
    exit_code: int


def run_misfit(
    case: Case,
    obs: list[Path],
    posterior: int,
    prior: int | None,
    members: list[int],
    eclbase: str | None,
    ert_config: Path | None,
    outdir: Path,
) -> MisfitRun:

    command = [
        sys.executable,
        str(HMQC / "misfit_report.py"),
        *[arg for path in obs for arg in ("--obs", str(path))],
        "--posterior",
        str(case.path / "realization-*" / f"iter-{posterior}"),
        "--realizations",
        ",".join(str(m) for m in members),
        "--allow-partial",
        "--outdir",
        str(outdir),
    ]
    if prior is not None:
        command += ["--prior", str(case.path / "realization-*" / f"iter-{prior}")]
    if eclbase:
        command += ["--eclbase", eclbase]
    if ert_config:
        command += ["--ert-config", str(ert_config)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    manifest = outdir / "manifest.json"
    if result.returncode not in (0, 1) or not manifest.is_file():
        raise ValueError(
            f"misfit_report.py failed (exit {result.returncode}): {result.stderr.strip()[-2000:]}"
        )
    return MisfitRun(
        outdir,
        json.loads(manifest.read_text()),
        pd.read_csv(outdir / "residuals.csv"),
        pd.read_csv(outdir / "obs_summary.csv"),
        pd.read_csv(outdir / "real_misfit.csv"),
        result.returncode,
    )


def residual_hints(residuals: Path) -> dict:
    module = _load(HMQC / "residual_correlation.py")
    try:
        return module.analyse(residuals, "posterior")
    except (ValueError, KeyError) as error:
        return {"error": str(error), "findings": [], "review": [], "series": []}


def run_tool(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *command], capture_output=True, text=True, check=False)


# Positive weights whose sum(1/w) is not 1 are valid ES-MDA input; the lint still files them as a review.
RESCALED_WEIGHTS = re.compile(
    r"ES-MDA weights (?P<given>[\d., ]+) \([^)]*\): sum\(1/w\) = (?P<inverse>[\d.]+), not 1\. "
    r"ERT 23 rescales them to (?P<used>[\d., ]+?)\. "
)


def chi2_is_known_false_alarm(chi2: dict) -> bool:
    """Above the noise band but inside the spread-aware reference fired on the known-good setup. Above both counts."""
    return str(chi2.get("spread_verdict", "")).startswith("inside")


def lint_items(ert_config: Path, weights: str) -> list[ReviewItem]:
    command = [
        str(SKILLS / "ert-config-author-lint/scripts/check_ert_config.py"),
        "--ahm-only",
        str(ert_config),
    ]
    if re.fullmatch(r"[\d.\s,]+", weights or ""):
        command += ["--weights", weights.replace(" ", "")]
    result = run_tool(command)
    items = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        rescaled = len(parts) == 3 and RESCALED_WEIGHTS.search(parts[2])
        if rescaled:
            items.append(
                ReviewItem(
                    "check_ert_config --ahm",
                    "INFO-WEIGHTS",
                    f"ES-MDA weights {rescaled['given']} are valid. sum(1/w) = {rescaled['inverse']}, so ERT rescales "
                    f"them to w * sum(1/w) = {rescaled['used']} and runs with those.",
                    info=True,
                )
            )
            continue
        if len(parts) == 3 and parts[0] == "ahm" and parts[1] in ("review", "error"):
            text = parts[2]
            calibrated = bool(re.search(r"NUM_REALIZATIONS \d+ .*below 100", text))
            members = "NUM_REALIZATIONS" in text or "MIN_REALIZATIONS" in text
            items.append(
                ReviewItem(
                    "check_ert_config --ahm",
                    f"LINT-{parts[1].upper()}",
                    text,
                    calibration_false_alarm=calibrated,
                    not_counted_reason=UNCALIBRATED_LINT if members and not calibrated else "",
                )
            )
    return items


def physics_items(deck: Path, ert_config: Path | None) -> tuple[list[ReviewItem], list[dict]]:
    command = [str(SKILLS / "deck-scal-pvt-qc/scripts/physics_checklist.py"), "--deck", str(deck), "--json"]
    if ert_config:
        command += ["--ert-config", str(ert_config)]
    result = run_tool(command)
    if result.returncode != 0:
        return [ReviewItem("physics_checklist", "PHYS-NOT-RUN", result.stderr.strip()[-300:])], []
    checklist = json.loads(result.stdout)["items"]
    items = [
        ReviewItem(
            "physics_checklist",
            item["code"],
            f"{item['status']}: {item['summary']}",
            not_counted_reason=(
                DESIGN_CHOICE if item["code"] in DESIGN_CHOICE_MISSING and item["status"] == "missing" else ""
            ),
        )
        for item in checklist
        if item["status"] not in ("present", "not-applicable")
    ]
    return items, checklist


def write_sampled_dist(params: dict[str, dict], sampled: set[str], path: Path) -> Path:
    """The declared lines of the parameters that the runs sample. compensation_check needs every declared one."""
    lines = []
    for name in sorted(sampled):
        param = params[name]
        source = Path(param["file"]).read_text(errors="replace").splitlines()[param["line"] - 1]
        lines.append(source.split("--")[0].strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def compensation_items(
    case: Case, params: dict[str, dict], sampled: set[str], members: list[int], misfit: Path, outdir: Path
) -> tuple[list[ReviewItem], dict | None]:
    dist = write_sampled_dist(params, sampled, outdir / "compensation" / "sampled.dist")
    command = [
        str(HMQC / "compensation_check.py"),
        str(dist),
        "--prior-runpaths",
        str(case.path / "realization-*" / f"iter-{case.prior}"),
        "--posterior-runpaths",
        str(case.path / "realization-*" / f"iter-{case.posterior}"),
        "--realizations",
        ",".join(str(m) for m in members),
        "--misfit-manifest",
        str(misfit / "manifest.json"),
        "--json",
    ]
    result = run_tool(command)
    if result.returncode != 0:
        return [ReviewItem("compensation_check", "COMP-NOT-RUN", result.stderr.strip()[-300:])], None
    report = json.loads(result.stdout)
    (outdir / "compensation" / "compensation.json").write_text(json.dumps(report, indent=2))
    run = report["runs"][0]
    items = [
        ReviewItem("compensation_check", f["code"], f"{f['name']}: {f['detail']}", f["code"].endswith("EDGE"))
        for f in run["findings"]
        if f["code"].endswith(("EDGE", "PAIR"))
    ]
    if run["verdict"] != "plausible":
        items.append(ReviewItem("compensation_check", "COMP-VERDICT", f"verdict {run['verdict']}", True))
    return items, report


# ---------------------------------------------------------------- computations


def family_of(key: str) -> str:
    return key.split(":", 1)[0]


def is_block_key(key: str) -> bool:
    family, _, rest = key.partition(":")
    return family.startswith("B") and re.fullmatch(r"\d+,\d+,\d+", rest) is not None


def well_of(key: str) -> str:
    family, _, rest = key.partition(":")
    if family == "GEN":
        return rest or "GEN"
    return rest or "FIELD"


def posterior_residuals(residuals, block: bool = False):
    used = residuals[(residuals["ENSEMBLE"] == "posterior") & residuals["RESIDUAL"].notna()]
    return used[used["KEY"].map(is_block_key) == block].copy()


def well_misfit(residuals):
    used = posterior_residuals(residuals)
    used["WELL"] = used["KEY"].map(well_of)
    used["SQ"] = used["RESIDUAL"] ** 2
    table = used.groupby("WELL").agg(
        OBS=("OBS", "nunique"), MEAN_SQ_NORM_RESIDUAL=("SQ", "mean"), MEAN_NORM_RESIDUAL=("RESIDUAL", "mean")
    )
    return table.sort_values("MEAN_SQ_NORM_RESIDUAL", ascending=False).reset_index()


def heatmap_table(residuals):
    used = posterior_residuals(residuals)
    used["WELL"] = used["KEY"].map(well_of)
    used["FAMILY"] = used["KEY"].map(family_of)
    used["SQ"] = used["RESIDUAL"] ** 2
    return used.pivot_table(index="WELL", columns="FAMILY", values="SQ", aggfunc="mean")


def block_summary(run: MisfitRun) -> dict | None:
    """Pilot pressure (block) observations, such as BPR:7,8,4. They are not wells."""
    used = posterior_residuals(run.residuals, block=True)
    if used.empty:
        return None
    summary = run.obs_summary[
        (run.obs_summary["ENSEMBLE"] == "posterior") & run.obs_summary["KEY"].map(is_block_key)
    ]
    inside = summary["INSIDE_PREDICTIVE_P5_P95"].dropna().astype(bool)
    return {
        "observations": int(used["OBS"].nunique()),
        "keys": sorted(used["KEY"].unique()),
        "mean_sq_norm_residual": float((used["RESIDUAL"] ** 2).mean()),
        "mean_norm_residual": float(used["RESIDUAL"].mean()),
        "coverage_p5_p95": float(inside.mean()) if len(inside) else None,
    }


def misfit_outliers(real_misfit) -> list[dict]:
    """Posterior realizations with misfit > median + 3 * 1.4826 * MAD (Hampel rule). None when MAD is 0."""
    post = real_misfit[real_misfit["ENSEMBLE"] == "posterior"].dropna(subset=["MISFIT"])
    values = post["MISFIT"].to_numpy(dtype=float)
    if len(values) < 3:
        return []
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad == 0:
        return []
    limit = median + 3 * 1.4826 * mad
    return [
        {"realization": int(row["REAL"]), "misfit": float(row["MISFIT"]), "limit": limit}
        for _, row in post[post["MISFIT"] > limit].sort_values("MISFIT", ascending=False).iterrows()
    ]


def family_table(run: MisfitRun, hints: dict):

    summary = run.obs_summary[run.obs_summary["ENSEMBLE"] == "posterior"].copy()
    summary["FAMILY"] = summary["KEY"].map(family_of)
    by_family = run.manifest.get("misfit_by_family", {}).get("posterior", {})
    prior_family = run.manifest.get("misfit_by_family", {}).get("prior")
    flagged = {}
    for item in hints.get("findings", []):
        family = family_of(item.get("key", ""))
        flagged.setdefault(family, []).append(item.get("code", ""))
    rows = []
    for family, group in summary.groupby("FAMILY"):
        inside = group["INSIDE_PREDICTIVE_P5_P95"].dropna().astype(bool)
        codes = flagged.get(family, [])
        rows.append(
            {
                "FAMILY": family,
                "OBS": len(group),
                "MEAN_SQ_NORM_RESIDUAL": by_family.get(family, {}).get("mean_squared_residual"),
                **(
                    {"PRIOR_MEAN_SQ_NORM_RESIDUAL": prior_family.get(family, {}).get("mean_squared_residual")}
                    if prior_family
                    else {}
                ),
                "MEAN_NORM_RESIDUAL": group["MEAN_RESIDUAL"].mean(),
                "SPREAD_OVER_ERROR": group["SPREAD_RATIO"].mean(),
                "COVERAGE_P5_P95": inside.mean() if len(inside) else None,
                "KEYS_BIAS": sum(c.endswith("BIAS") for c in codes),
                "KEYS_VARIANCE": sum(c.endswith("VARIANCE") for c in codes),
            }
        )
    return pd.DataFrame(rows)


def end_of_history(observations: list[dict], residuals, frames) -> tuple[datetime | None, str]:

    dates = [datetime.fromisoformat(o["date"]) for o in observations]
    if residuals is not None and len(residuals):
        dates += list(pd.to_datetime(residuals["DATE"]).dt.to_pydatetime())
    if dates:
        return max(dates), "last observation date"
    last = None
    for frame in frames.values():
        history = [c for c in frame.columns if family_of(c).endswith("H")]
        nonzero = frame.index[(frame[history].abs() > 0).any(axis=1)] if history else []
        if len(nonzero):
            last = max(last or nonzero[-1], nonzero[-1])
    if last is not None:
        return last.to_pydatetime(), "last date with a non-zero *H history vector"
    return None, "unknown (no observation and no non-zero *H vector)"


def value_at(frame, key: str, when: datetime) -> float:

    times = frame.index.as_unit("ns").asi8.astype(float)
    return float(np.interp(pd.Timestamp(when).as_unit("ns").value, times, frame[key].to_numpy(dtype=float)))


def forecast_table(frames, eoh: datetime | None):

    last = max(frame.index[-1] for frame in frames.values()).to_pydatetime()
    dates = [("end of history", eoh)] if eoh else []
    if eoh is None or last > eoh:
        dates.append(("last simulated date", last))
    rows = []
    for vector in FORECAST:
        have = {r: f for r, f in frames.items() if vector in f.columns}
        unit = next((f.attrs.get("units", {}).get(vector) for f in have.values()), None)
        for label, when in dates:
            row = {
                "VECTOR": vector,
                "UNIT": unit,
                "AT": label,
                "DATE": when.date().isoformat(),
                "MEMBERS": len(have),
            }
            if not have:
                rows.append({**row, "STATUS": "missing in SUMMARY"})
                continue
            values = np.array(
                [value_at(f, vector, min(when, f.index[-1].to_pydatetime())) for f in have.values()]
            )
            p10, p50, p90 = np.percentile(values, [10, 50, 90])
            flat = len(values) > 1 and bool(np.isclose(p10, p90, rtol=1e-9, atol=0))
            status = HISTORY_CONTROLLED if flat else "ok"
            rows.append({**row, "STATUS": status, "P10": p10, "P50": p50, "P90": p90, "MEAN": values.mean()})
    return pd.DataFrame(rows)


def parameter_values(params: dict[str, dict], members: dict[int, dict], name: str) -> list[float]:
    """Values of one declared parameter; parameters.txt names carry a GROUP: prefix."""
    found = (prior_qc.match(member, {name: params[name]})[0].get(name) for member in members.values())
    return [value for value in found if value is not None]


def parameter_table(params: dict[str, dict], post: dict[int, dict], prior: dict[int, dict] | None):
    """Posterior position in the prior range (0 at min, 1 at max; log for LOGUNIF), from the .dist bounds."""

    limits = compensation.DEFAULT_LIMITS
    bounded = compensation.bounded_parameters(params)

    rows, sampled = [], set()
    for name in sorted(params):
        param = params[name]
        values = parameter_values(params, post, name)
        row = {
            "PARAMETER": name,
            "DISTRIBUTION": param["distribution"],
            "MIN": param["min"],
            "MAX": param["max"],
        }
        if not values:
            rows.append({**row, "STATUS": "declared in .dist, not in parameters.txt (not sampled)"})
            continue
        if name not in bounded:
            rows.append({**row, "STATUS": "no bounds", "POST_MEAN": float(np.mean(values))})
            continue
        sampled.add(name)
        pos = np.array(compensation.positions(param, values))
        near = float(np.mean((pos <= limits.edge / 2) | (pos >= 1 - limits.edge / 2)))
        mean = float(pos.mean())
        row.update(
            STATUS="ok",
            POST_MEAN=float(np.mean(values)),
            POST_MEAN_POS=mean,
            POST_SD_POS=float(pos.std(ddof=1)) if len(pos) > 1 else None,
            SHARE_NEAR_BOUND=near,
            AT_BOUND=bool(mean <= limits.edge or mean >= 1 - limits.edge or near >= limits.pile),
        )
        if prior:
            prior_values = parameter_values(params, prior, name)
            if len(prior_values) > 1:
                prior_pos = np.array(compensation.positions(param, prior_values))
                row["PRIOR_SD_POS"] = float(prior_pos.std(ddof=1))
                row["SD_RATIO"] = row["POST_SD_POS"] / row["PRIOR_SD_POS"] if row["PRIOR_SD_POS"] else None
        rows.append(row)
    return pd.DataFrame(rows), sampled


def positions_by_parameter(
    params: dict[str, dict], values: dict[int, dict], names: list[str]
) -> dict[str, list]:
    return {
        name: compensation.positions(params[name], parameter_values(params, values, name)) for name in names
    }


def pressure_choice(frames, wells: list[str]) -> dict[str, str]:
    columns = set().union(*(f.columns for f in frames.values()))
    choice = {}
    for well in wells:
        for mnemonic in PRESSURE_ORDER:
            if f"{mnemonic}:{well}" in columns:
                choice[well] = mnemonic
                break
    return choice


def history_status(frames) -> tuple[list[str], list[str]]:
    """History mnemonics (*H) present and missing, for the simulated vectors this report reads."""
    columns = set().union(*(f.columns for f in frames.values()))
    simulated = {family_of(c) for c in columns if not family_of(c).endswith("H") or family_of(c) == "WBHP"}
    simulated -= {"FPR", "WBP9", "WBP4", "WBP"} | {family_of(c) for c in columns if is_block_key(c)}
    history = {family_of(c) for c in columns} - simulated
    present = sorted(f"{m}H" for m in simulated if f"{m}H" in history)
    missing = sorted(f"{m}H" for m in simulated if f"{m}H" not in history)
    return present, missing


# ---------------------------------------------------------------- verdict


@dataclass
class Facts:
    mode: str
    expected: int
    ok_members: int
    coverage: float | None
    chi2_verdict: str
    spread_verdict: str
    mean_misfit: float | None
    required_missing: list[str]
    review_items: int
    worst_well: str | None
    worst_well_msr: float | None
    no_prior_note: str = "iter-0 (prior) is gone"
    at_bound: list[str] = field(default_factory=list)
    review_codes: list[str] = field(default_factory=list)

    @property
    def ok_share(self) -> float:
        return self.ok_members / self.expected if self.expected else 0.0


@dataclass(frozen=True)
class Rule:
    level: str
    code: str
    applies: Callable[[Facts], bool]
    evidence: Callable[[Facts], str]


NOT_READY, CAVEATS, FIT = "Not ready", "Use with caveats", "Fit for use"
RULES = (
    Rule(
        NOT_READY,
        "REALIZATIONS-LOW",
        lambda f: f.ok_share < OK_SHARE_MIN,
        lambda f: f"only {f.ok_members} of {f.expected} posterior realizations ok ({f.ok_share:.0%} < 80%)",
    ),
    Rule(
        NOT_READY,
        "COVERAGE-LOW",
        lambda f: f.coverage is not None and f.coverage < COVERAGE_FAIL,
        lambda f: f"predictive coverage P5-P95 {f.coverage:.2f} < {COVERAGE_FAIL}",
    ),
    Rule(
        NOT_READY,
        "MISFIT-ABOVE-REFERENCE",
        lambda f: (
            f.spread_verdict.startswith("above") and (f.coverage is None or f.coverage < COVERAGE_MISFIT)
        ),
        lambda f: (
            f"mean misfit {f.mean_misfit:.2f} is {f.spread_verdict}, and coverage "
            f"{'unknown' if f.coverage is None else f'{f.coverage:.2f}'} < {COVERAGE_MISFIT}"
        ),
    ),
    Rule(
        NOT_READY,
        "REQUIRED-VECTOR-MISSING",
        lambda f: bool(f.required_missing),
        lambda f: f"required field vectors missing in SUMMARY: {', '.join(f.required_missing)}",
    ),
    Rule(
        CAVEATS,
        "POSTERIOR-ONLY",
        lambda f: f.mode == "posterior-only",
        lambda f: f"{f.no_prior_note}: the report cannot show what the update changed",
    ),
    Rule(
        CAVEATS,
        "REALIZATIONS-PARTIAL",
        lambda f: OK_SHARE_MIN <= f.ok_share < 1,
        lambda f: f"{f.ok_members} of {f.expected} posterior realizations ok",
    ),
    Rule(
        CAVEATS,
        "COVERAGE-OUTSIDE",
        lambda f: f.coverage is not None and not COVERAGE_LOW <= f.coverage <= COVERAGE_HIGH,
        lambda f: f"predictive coverage P5-P95 {f.coverage:.2f} outside {COVERAGE_LOW}-{COVERAGE_HIGH}",
    ),
    Rule(
        CAVEATS,
        "COVERAGE-UNKNOWN",
        lambda f: f.coverage is None,
        lambda f: "predictive coverage P5-P95 not available from misfit_report",
    ),
    Rule(
        CAVEATS,
        "PARAMETER-AT-BOUND",
        lambda f: bool(f.at_bound),
        lambda f: f"{len(f.at_bound)} parameter(s) at a prior bound: {', '.join(f.at_bound)}",
    ),
    Rule(
        CAVEATS,
        "WELL-MISFIT-HIGH",
        lambda f: f.worst_well_msr is not None and f.worst_well_msr > WELL_LIMIT,
        lambda f: (
            f"worst well {f.worst_well}: mean squared normalized residual {f.worst_well_msr:.2f} > {WELL_LIMIT}"
        ),
    ),
    Rule(
        CAVEATS,
        "REVIEW-ITEMS",
        lambda f: f.review_items > 0,
        lambda f: (
            f"{f.review_items} counted review item(s)"
            + (f": {', '.join(f.review_codes)}" if f.review_codes else "")
            + " (section 4)"
        ),
    ),
)


def verdict(facts: Facts) -> tuple[str, list[dict]]:
    fired = [
        {"level": r.level, "code": r.code, "evidence": r.evidence(facts)} for r in RULES if r.applies(facts)
    ]
    levels = {item["level"] for item in fired}
    level = NOT_READY if NOT_READY in levels else CAVEATS if CAVEATS in levels else FIT
    return level, fired


# ---------------------------------------------------------------- report


def fmt(value, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "-"
    if isinstance(value, float):
        return f"{value:,.{digits}g}" if abs(value) < 1e4 else f"{value:,.0f}"
    return str(value)


def band_text(chi2: dict) -> str:
    band = chi2.get("band")
    return f"{band[0]:.2f}-{band[1]:.2f}" if band else "-"


def md_table(frame, columns: list[str] | None = None, digits: int = 3) -> str:
    if frame is None or len(frame) == 0:
        return "_none_\n"
    columns = [c for c in (columns or list(frame.columns)) if c in frame.columns]
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(fmt(row[c], digits) for c in columns) + " |")
    return "\n".join(lines) + "\n"


def resolve_outdir(args, case: Path) -> Path:
    share = (case / "share" / "results" / "ahm_status").resolve()
    if args.write_share:
        return share
    outdir = args.outdir.resolve()
    if outdir == case or case in outdir.parents:
        raise ValueError(f"--outdir {outdir} is inside the case. Give --write-share to write {share}")
    return outdir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--case", required=True, type=Path, help="scratch case folder with realization-*/iter-*"
    )
    parser.add_argument(
        "--obs", action="append", default=[], type=Path, help="observation file; repeat for more"
    )
    parser.add_argument(
        "--ert-config", type=Path, help="the .ert file: expected count, ECLBASE, storage, dists"
    )
    parser.add_argument("--dist", action="append", default=[], type=Path, help="GEN_KW .dist file; repeat")
    parser.add_argument(
        "--eclbase", help="ECLBASE relative to the runpath, for example eclipse/model/NAME-<IENS>"
    )
    parser.add_argument("--expected", type=int, help="expected realizations, numbered 0 to N-1")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--outdir", type=Path, default=Path("ahm_status"))
    out.add_argument("--write-share", action="store_true", help="write to <case>/share/results/ahm_status")
    parser.add_argument("--title", default=None)
    parser.add_argument("--wells", default=None, help="comma-separated wells for the well plots")
    args = parser.parse_args(argv)

    case_dir = args.case.resolve()
    if not case_dir.is_dir():
        print(f"ERROR: --case is not a folder: {case_dir}", file=sys.stderr)
        return 2
    if args.ert_config and not args.ert_config.is_file():
        print(f"ERROR: --ert-config is not a file: {args.ert_config}", file=sys.stderr)
        return 2
    try:
        outdir = resolve_outdir(args, case_dir)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    try:
        return build_report(args, case_dir, outdir)
    except (ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


def build_report(args, case_dir: Path, outdir: Path) -> int:
    plots = _load(HERE / "ahm_status_plots.py")

    settings = read_ert_config(args.ert_config) if args.ert_config else None
    eclbase = args.eclbase or (settings.eclbase if settings else None)
    obs_files = [p.resolve() for p in args.obs] or (settings.obs_files if settings else [])
    dist_files = [p.resolve() for p in args.dist]
    if not obs_files:
        raise ValueError("no observation file: give --obs, or --ert-config with OBS_CONFIG")
    for path in obs_files + dist_files:
        if not path.is_file():
            raise ValueError(f"not a file: {path}")
    unreadable_dist = []
    if not dist_files and settings:
        dist_files = [p for p in settings.dist_files if p.is_file()]
        unreadable_dist = [p.name for p in settings.dist_files if not p.is_file()]
    stored, weights = read_storage(settings.enspath if settings else None)
    case = scan_case(case_dir, eclbase, args.expected, settings, stored)
    case.stored, case.weights = stored, weights
    post, prior = case.posterior, case.prior
    post_ok = case.members(post)
    if len(post_ok) < 2:
        raise ValueError(f"iter-{post} has {len(post_ok)} ok realization(s); the report needs at least 2")

    for sub in ("plots", "tables", "misfit"):
        (outdir / sub).mkdir(parents=True, exist_ok=True)
    print(
        f"Case {case_dir}: iterations on disk {sorted(case.iterations)}, posterior iter-{post}, mode {case.mode}"
    )

    # Data match
    main_run = run_misfit(
        case, obs_files, post, prior, post_ok, eclbase, args.ert_config, outdir / "misfit" / "main"
    )
    runs = {post: main_run}
    for iteration in sorted(case.iterations):
        if iteration not in (post, prior) and len(case.members(iteration)) >= 2:
            runs[iteration] = run_misfit(
                case,
                obs_files,
                iteration,
                None,
                case.members(iteration),
                eclbase,
                args.ert_config,
                outdir / "misfit" / f"iter-{iteration}",
            )
    hints = residual_hints(main_run.folder / "residuals.csv")
    manifest = main_run.manifest
    chi2 = manifest.get("misfit_vs_chi2", {}).get("posterior", {})
    coverage = manifest.get("coverage_p5_p95", {}).get("posterior", {}).get("predictive")
    wells_table = well_misfit(main_run.residuals)
    families = family_table(main_run, hints)
    heat = heatmap_table(main_run.residuals)
    blocks = block_summary(main_run)
    outliers = misfit_outliers(main_run.real_misfit)

    # Summaries
    observations = read_observations(obs_files)
    pilot = sorted({o["key"] for o in observations if is_block_key(o["key"]) and o["key"].startswith("BPR")})
    frames = load_frames(case, post, set(pilot))
    prior_frames = load_frames(case, prior, set(pilot)) if prior is not None else None
    columns = set().union(*(f.columns for f in frames.values()))
    eoh, eoh_source = end_of_history(observations, main_run.residuals, frames)
    forecast = forecast_table(frames, eoh)
    required_missing = [v for v in REQUIRED_FIELD if v not in columns]
    field_missing = sorted({m for m in FIELD_MNEMONICS if m not in columns})
    wells = sorted({c.split(":", 1)[1] for c in columns if ":" in c and family_of(c) in WELL_MNEMONICS})
    if args.wells:
        wanted = [w.strip() for w in args.wells.split(",") if w.strip()]
        unknown = [w for w in wanted if w not in wells]
        if unknown:
            raise ValueError(f"--wells not in SUMMARY: {unknown}")
        wells = wanted
    pressure = pressure_choice(frames, wells)
    history_present, history_missing = history_status(frames)

    # Parameters
    params: dict[str, dict] = {}
    dist_errors = []
    for path in dist_files:
        parsed, errors = prior_qc.parse_dist(path)
        dist_errors += errors
        params.update({p["name"]: p for p in parsed})
    post_params = read_parameters(case, post)
    prior_params = read_parameters(case, prior) if prior is not None else None
    param_table, sampled = (
        parameter_table(params, post_params, prior_params)
        if params and post_params
        else (pd.DataFrame(), set())
    )
    at_bound = (
        list(param_table.loc[param_table["AT_BOUND"].fillna(False).astype(bool), "PARAMETER"])
        if len(param_table) and "AT_BOUND" in param_table
        else []
    )
    bound_check = parameter_bound_check(
        param_table, post_params, settings.gen_kw if settings else 0, unreadable_dist
    )

    # Review items
    review: list[ReviewItem] = []
    if bound_check["warn"]:
        review.append(
            ReviewItem(
                "parameters",
                "PARAM-BOUND-NOT-RUN",
                bound_check["text"],
                not_counted_reason=NOT_RUN,
                level="WARN",
            )
        )
    if args.ert_config:
        review += lint_items(args.ert_config, weights)
    first = case.iterations[post][post_ok[0]].summary
    physics, checklist = physics_items(first.with_suffix(".DATA"), args.ert_config)
    review += physics
    if chi2.get("verdict", "").startswith("above"):
        review.append(
            ReviewItem(
                "misfit_report",
                "CHI2-ABOVE-NOISE",
                f"mean misfit {chi2.get('mean_misfit', float('nan')):.2f} {chi2.get('verdict')} "
                f"{band_text(chi2)}; {chi2.get('spread_verdict', 'no spread-aware reference')}",
                chi2_is_known_false_alarm(chi2),
            )
        )
    for item in hints.get("findings", []):
        review.append(
            ReviewItem(
                "residual_correlation", item.get("code", ""), f"{item.get('key')}: {item.get('detail', '')}"
            )
        )
    compensation_report = None
    if prior is not None and sampled:
        items, compensation_report = compensation_items(
            case, params, sampled, post_ok, main_run.folder, outdir
        )
        review += items

    facts = Facts(
        no_prior_note=case.no_prior_note,
        mode=case.mode,
        expected=len(case.expected),
        ok_members=len(post_ok),
        coverage=coverage,
        chi2_verdict=chi2.get("verdict", "unknown"),
        spread_verdict=chi2.get("spread_verdict", "unknown"),
        mean_misfit=chi2.get("mean_misfit"),
        required_missing=required_missing,
        review_items=sum(r.counts for r in review),
        review_codes=list(dict.fromkeys(r.code for r in review if r.counts)),
        at_bound=at_bound,
        worst_well=wells_table.iloc[0]["WELL"] if len(wells_table) else None,
        worst_well_msr=float(wells_table.iloc[0]["MEAN_SQ_NORM_RESIDUAL"]) if len(wells_table) else None,
    )
    level, fired = verdict(facts)

    # Tables
    inventory = inventory_table(case)
    medians = iteration_medians(case, runs, main_run)
    findings = pd.DataFrame(
        [{"SOURCE": "misfit_report", **f} for f in manifest.get("findings", [])]
        + [{"SOURCE": r.source, "finding": r.code, "detail": r.text} for r in review]
    )
    tables = {
        "inventory": inventory,
        "well_misfit": wells_table,
        "family_misfit": families,
        "forecast_pXX": forecast,
        "parameters": param_table,
        "findings": findings,
        "iteration_misfit": medians,
    }
    for name, table in tables.items():
        table.to_csv(outdir / "tables" / f"{name}.csv", index=False)

    # Plots
    title = args.title or f"{case_dir.name} iter-{post}"
    context = plots.Context(
        title=title,
        eoh=eoh,
        observations=observations,
        prior_frames=prior_frames,
        prior_label="iter-0 (prior)" if prior is not None else None,
    )
    figures = {
        "headline_field": plots.field_grid(
            frames, [(v,) for v in HEADLINE], context, 2, "headline field profiles"
        ),
        "field": plots.field_grid(frames, FIELD_PANELS, context, 3, "field profiles"),
    }
    for mnemonic in WELL_VECTORS:
        figures[f"wells_{mnemonic}"] = plots.well_grid(frames, mnemonic, wells, context)
    rft = main_run.residuals[main_run.residuals["KEY"].str.startswith("GEN:")]
    figures["pressure"] = plots.pressure_grid(frames, pressure, wells, context, rft, pilot)
    figures["heatmap"] = plots.heatmap(heat, context)
    figures["worst_wells"] = plots.worst_wells(wells_table, context)
    figures["misfit_hist"] = plots.misfit_hist(main_run.real_misfit, medians, context, outliers)
    if len(param_table) and "POST_MEAN_POS" in param_table:
        names = list(param_table.loc[param_table["STATUS"] == "ok", "PARAMETER"])
        post_pos = positions_by_parameter(params, post_params, names)
        prior_pos = positions_by_parameter(params, prior_params, names) if prior_params else None
        figures["params"] = plots.params(post_pos, prior_pos, set(at_bound), context)
    images = {}
    for name, figure in figures.items():
        if figure is None:
            continue
        path = outdir / "plots" / f"{name}.png"
        figure.savefig(path, dpi=110)
        images[name] = path

    status = {
        "schema": "ahm-status/1",
        "title": title,
        "case": str(case_dir),
        "mode": case.mode,
        "prior_deleted": case.prior_deleted,
        "verdict": level,
        "advisory": ADVISORY,
        "evidence": fired,
        "convention": CONVENTION,
        "counts": {
            "expected": len(case.expected),
            "expected_source": case.expected_source,
            "posterior_iteration": post,
            "posterior_ok": len(post_ok),
            "posterior_failed": case.members(post, RealStatus.FAILED),
            "posterior_missing": case.members(post, RealStatus.MISSING),
            "iterations_on_disk": sorted(case.iterations),
            "iterations_in_storage": {str(k): v for k, v in sorted(stored.items())},
            "observations_used": manifest.get("observations"),
        },
        "weights": weights,
        "end_of_history": eoh.date().isoformat() if eoh else None,
        "end_of_history_source": eoh_source,
        "misfit": {"chi2": chi2, "coverage_p5_p95": coverage, "by_family": manifest.get("misfit_by_family")},
        "missing_vectors": {"field": field_missing, "required": required_missing, "history": history_missing},
        "pressure_mnemonic": pressure,
        "pressure_meaning": {m: plots.PRESSURE_MEANING[m] for m in sorted(set(pressure.values()))},
        "pilot_pressure_keys": pilot,
        "block_observations": blocks,
        "misfit_outliers": outliers,
        "history_controlled": sorted(set(forecast.loc[forecast["STATUS"] == HISTORY_CONTROLLED, "VECTOR"])),
        "parameters_at_bound": at_bound,
        "parameter_bound_check": bound_check,
        "dist_errors": dist_errors,
        "review_items": [asdict(r) for r in review],
        "calibration_caveat": CALIBRATION_CAVEAT,
        "misfit_report_exit": {str(k): r.exit_code for k, r in runs.items()},
        "compensation_verdict": compensation_report["runs"][0]["verdict"] if compensation_report else None,
    }
    (outdir / "status.json").write_text(json.dumps(status, indent=2, default=str))

    report = Report(
        case=case,
        status=status,
        level=level,
        fired=fired,
        tables=tables,
        review=review,
        images=images,
        outdir=outdir,
        manifest=manifest,
        checklist=checklist,
        history_present=history_present,
        compensation=compensation_report,
    )
    (outdir / "summary.md").write_text(report.summary_md())
    (outdir / "report.md").write_text(report.full_md())
    plots.pdf(outdir / "report.pdf", report.pdf_pages(), figures, context)
    for figure in figures.values():
        if figure is not None:
            plots.close(figure)
    print(f"VERDICT: {level} ({case.mode}). {ADVISORY}")
    for item in fired:
        print(f"  - [{item['level']}] {item['evidence']}")
    print(CONVENTION)
    print(f"Wrote {outdir}")
    return 0


def parameter_bound_check(param_table, post_params: dict, gen_kw: int, unreadable: list[str]) -> dict:
    checked = int((param_table["STATUS"] == "ok").sum()) if len(param_table) else 0
    names = set().union(*post_params.values()) if post_params else set()
    if checked:
        return {"ran": True, "checked": checked, "warn": False, "text": f"{checked} parameter(s) checked"}
    if not names and not gen_kw:
        return {
            "ran": False,
            "checked": 0,
            "warn": False,
            "text": "no GEN_KW parameter and no parameters.txt",
        }
    count = f"{len(names)} params" if names else f"{gen_kw} GEN_KW group(s)"
    text = f"parameter-at-bound check could not run: {count}, no readable dist"
    if unreadable:
        text += f" (not found: {', '.join(unreadable)})"
    return {"ran": False, "checked": 0, "warn": True, "text": text}


def inventory_table(case: Case):

    rows = []
    for iteration in sorted(set(case.iterations) | set(case.stored)):
        reals = case.iterations.get(iteration)
        row = {
            "ITERATION": iteration,
            "ON_DISK": reals is not None,
            "IN_ERT_STORAGE": iteration in case.stored,
            "STORAGE_ENSEMBLE_SIZE": case.stored.get(iteration),
            "EXPECTED": len(case.expected),
        }
        if reals is not None:
            row.update(
                OK=len(case.members(iteration)),
                FAILED=len(case.members(iteration, RealStatus.FAILED)),
                MISSING=len(case.members(iteration, RealStatus.MISSING)),
                FAILED_REALIZATIONS=" ".join(map(str, case.members(iteration, RealStatus.FAILED))),
                MISSING_REALIZATIONS=" ".join(map(str, case.members(iteration, RealStatus.MISSING))),
            )
        rows.append(row)
    return pd.DataFrame(rows)


def iteration_medians(case: Case, runs: dict[int, MisfitRun], main_run: MisfitRun):

    rows = []
    for iteration in sorted(case.iterations):
        if iteration == case.prior:
            table, label = main_run.real_misfit, "prior"
        elif iteration in runs:
            table, label = runs[iteration].real_misfit, "posterior"
        else:
            continue
        values = table.loc[table["ENSEMBLE"] == label, "MISFIT"].dropna()
        if len(values):
            rows.append({"ITERATION": iteration, "MEMBERS": len(values), "MEDIAN_MISFIT": values.median()})
    return pd.DataFrame(rows)


@dataclass
class Report:
    case: Case
    status: dict
    level: str
    fired: list[dict]
    tables: dict
    review: list[ReviewItem]
    images: dict[str, Path]
    outdir: Path
    manifest: dict
    checklist: list[dict]
    history_present: list[str]
    compensation: dict | None

    def image(self, name: str) -> str:
        return f"![{name}](plots/{name}.png)\n" if name in self.images else ""

    def evidence_md(self) -> str:
        if not self.fired:
            lines = "- No rule fired.\n"
        else:
            lines = "".join(f"- **{i['level']}** ({i['code']}): {i['evidence']}\n" for i in self.fired)
        return lines + self.not_counted_md()

    def not_counted_md(self) -> str:
        shown = [r for r in self.review if not r.counts]
        if not shown:
            return ""
        return "".join(f"- {r.label}: [{r.code}] {r.text}\n" for r in shown)

    def outliers_md(self) -> str:
        outliers = self.status["misfit_outliers"]
        if not outliers:
            return "Outlier realizations (misfit > median + 3 * 1.4826 * MAD): none.\n"
        names = ", ".join(f"{o['realization']} ({o['misfit']:.3g})" for o in outliers)
        return (
            f"Outlier realizations (misfit > median + 3 * 1.4826 * MAD = {outliers[0]['limit']:.3g}; info, not counted): "
            f"{names}.\n"
        )

    def blocks_md(self) -> str:
        blocks = self.status["block_observations"]
        if not blocks:
            return ""
        return (
            f"\nPilot pressure (block) observations, not wells: {blocks['observations']} observation(s) on "
            f"{', '.join(blocks['keys'])}. Mean squared normalized residual {fmt(blocks['mean_sq_norm_residual'])}, "
            f"mean normalized residual {fmt(blocks['mean_norm_residual'])}, predictive coverage P5-P95 "
            f"{fmt(blocks['coverage_p5_p95'])}.\n"
        )

    def pressure_md(self) -> str:
        meaning = self.status["pressure_meaning"]
        return "".join(f" {m} is {text}." for m, text in meaning.items())

    def history_controlled_md(self) -> str:
        flat = self.status["history_controlled"]
        if not flat:
            return ""
        return (
            f"\n{', '.join(flat)}: P10 = P90 ({HISTORY_CONTROLLED}). The spread is set by the history controls, "
            "not by the history match.\n"
        )

    def parameters_md(self) -> str:
        check = self.status["parameter_bound_check"]
        if check["warn"]:
            return f"\n**WARN**: {check['text']}. The parameter-at-bound check did not run.\n"
        if not check["ran"]:
            return f"\nParameter-at-bound check not run: {check['text']}.\n"
        at_bound = self.status["parameters_at_bound"]
        if not at_bound:
            return f"\nNo parameter at a bound ({check['checked']} checked).\n"
        return f"\n{len(at_bound)} parameter(s) at a bound ({check['checked']} checked): {', '.join(at_bound)}.\n"

    def inventory_line(self) -> str:
        c, post = self.case, self.case.posterior
        line = (
            f"Realizations ok in iter-{post}: **{len(c.members(post))} of {len(c.expected)}** "
            f"(failed {len(c.members(post, RealStatus.FAILED))}, missing {len(c.members(post, RealStatus.MISSING))})."
        )
        if c.prior is None:
            line += f" **{c.no_prior_note[0].upper() + c.no_prior_note[1:]}.** The report does not show a prior and does not make one."
        return line

    def prior_line(self) -> str:
        if self.case.prior is None:
            return f"No prior comparison: {self.case.no_prior_note}.\n\n"
        chi2 = self.manifest.get("misfit_vs_chi2", {}).get("prior", {})
        coverage = self.manifest.get("coverage_p5_p95", {}).get("prior", {}).get("predictive")
        return (
            f"Prior (iter-0) for comparison: mean misfit {fmt(chi2.get('mean_misfit'))}, "
            f"predictive coverage P5-P95 {fmt(coverage)}.\n\n"
        )

    def forecast_md(self, at: str | None = None) -> str:
        table = self.tables["forecast_pXX"]
        if at is not None:
            table = table[table["AT"] == at]
        return md_table(table, ["VECTOR", "UNIT", "AT", "DATE", "STATUS", "MEMBERS", "P10", "P50", "P90"], 4)

    def summary_md(self) -> str:
        s = self.status
        return (
            f"# AHM status: {s['title']}\n\n"
            f"## Verdict: {self.level}\n\n{self.evidence_md()}\n_{ADVISORY}_\n\n"
            f"{self.inventory_line()} Mode: {s['mode']}. ES-MDA weights: {s['weights']}.\n\n"
            f"{self.outliers_md()}\n"
            f"{self.image('headline_field')}\n"
            f"## Worst 5 wells (mean squared normalized residual, posterior)\n\n"
            f"{md_table(self.tables['well_misfit'].head(5), digits=3)}{self.blocks_md()}\n"
            f"## Totals at end of history ({s['end_of_history']}, {s['end_of_history_source']})\n\n"
            f"{self.forecast_md('end of history')}{self.history_controlled_md()}\n**{CONVENTION}**\n\n"
            f"Full report: report.md, report.pdf. Machine-readable: status.json.\n"
        )

    def full_md(self) -> str:
        s, t = self.status, self.tables
        chi2 = s["misfit"]["chi2"]
        coverage = s["misfit"]["coverage_p5_p95"]
        missing = s["missing_vectors"]
        pressure = s["pressure_mnemonic"]
        lines = [
            f"# AHM status report: {s['title']}\n",
            f"Case: `{s['case']}`  \nMode: **{s['mode']}**  \nVerdict: **{self.level}**\n",
            self.evidence_md(),
            f"\n_{ADVISORY}_\n",
            "\n## 1. Case inventory\n",
            self.inventory_line() + "\n",
            f"\nExpected realizations from {s['counts']['expected_source']}. ES-MDA weights: {s['weights']}.\n",
            f"Iterations on disk: {s['counts']['iterations_on_disk']}. "
            f"Iterations in the ERT storage: {list(s['counts']['iterations_in_storage']) or 'no storage read'}.\n\n",
            md_table(t["inventory"]),
            "\n## 2. Data match\n",
            f"\nObservations used: {s['counts']['observations_used']}. Mean misfit (mean squared normalized residual): "
            f"{fmt(chi2.get('mean_misfit'))}. Noise band {band_text(chi2)}: {chi2.get('verdict', '-')}. "
            f"Spread-aware reference {fmt(chi2.get('spread_upper'))}: {chi2.get('spread_verdict', '-')}. "
            f"Predictive coverage P5-P95: {fmt(coverage)} (nominal 0.90).\n\n",
            self.prior_line(),
            "Per observation family (vector mnemonic). KEYS_BIAS and KEYS_VARIANCE count the keys with a "
            "HMQC-OBSERR hint of residual_correlation.py.\n\n",
            md_table(t["family_misfit"]),
            "\nPer well, ranked (mean over members and observations of the squared normalized residual). "
            "Block observations (BPR:i,j,k and other block vectors) are not wells and are not ranked.\n\n",
            md_table(t["well_misfit"]),
            self.blocks_md(),
            "\n",
            self.outliers_md(),
            "\n",
            self.image("heatmap"),
            self.image("worst_wells"),
            self.image("misfit_hist"),
            "\n### Profiles\n",
            f"Black: history (*H vector). Red: observations with error bars. Dashed line: end of history. "
            f"P10/P90 numerical: P10 is the low value.\n\nHistory mnemonics present: "
            f"{', '.join(self.history_present) or 'none'}.  \n**History mnemonics missing (*H)**: "
            f"{', '.join(missing['history']) or 'none'}.  \nField vectors missing in SUMMARY: "
            f"{', '.join(missing['field']) or 'none'}.\n\n",
            self.image("field"),
            *[self.image(f"wells_{m}") for m in WELL_VECTORS],
            "\n### Pressure\n",
            "Mnemonic per well (order WBP9 > WBP4 > WBP > WBHP): "
            + (", ".join(f"{w} {m}" for w, m in pressure.items()) or "none")
            + "."
            + self.pressure_md()
            + (
                f" Pilot pressure (block) panels: {', '.join(s['pilot_pressure_keys'])}."
                if s["pilot_pressure_keys"]
                else ""
            )
            + "\n\n",
            self.image("pressure"),
            "\n## 3. Ensemble health\n",
            "Response spread: SPREAD_OVER_ERROR in the family table (mean ensemble sd / observation error).\n\n",
            "Parameters in prior-position units (0 at min, 1 at max; log for LOGUNIF), from the .dist bounds. ",
            "AT_BOUND: mean within 0.1 of a bound, or >= 30% of members within 0.05.\n",
            self.parameters_md(),
            "\n",
            ""
            if not len(t["parameters"])
            else md_table(
                t["parameters"],
                [
                    "PARAMETER",
                    "DISTRIBUTION",
                    "STATUS",
                    "POST_MEAN_POS",
                    "POST_SD_POS",
                    "PRIOR_SD_POS",
                    "SD_RATIO",
                    "SHARE_NEAR_BOUND",
                    "AT_BOUND",
                ],
            ),
            self.image("params"),
            "\nFindings of misfit_report.py (spread, collapse, out of reach):\n\n",
            "".join(
                f"- {f.get('finding')}: {f.get('detail')} {f.get('examples', '')}\n"
                for f in self.manifest.get("findings", [])
            )
            or "- none\n",
            "\n## 4. Physics and plausibility (review items)\n",
            f"\n> {CALIBRATION_CAVEAT}\n\n",
            "".join(f"- {r.label} [{r.source} {r.code}]: {r.text}\n" for r in self.review) or "- none\n",
            "\nPhysics checklist (all items):\n\n",
            "".join(f"- {i['code']}: {i['status']} - {i['summary']}\n" for i in self.checklist)
            or "- not run\n",
            "\n## 5. Forecast readiness\n",
            f"\nEnd of history: {s['end_of_history']} ({s['end_of_history_source']}).\n\n",
            self.forecast_md(),
            self.history_controlled_md(),
            f"\n**{CONVENTION}**\n",
            "\n## 6. Verdict\n",
            f"\n**{self.level}**\n\n",
            self.evidence_md(),
            f"\n_{ADVISORY}_\n",
            "\nRules: Not ready if < 80% realizations ok, coverage < 0.6, misfit above the spread-aware reference "
            "with coverage < 0.8, or a required field vector (FOPT, FGPT, FWPT) missing. Use with caveats if any "
            "counted review item, coverage outside 0.80-0.97, a parameter at a bound, a well above 4, posterior-only, or "
            "< 100% realizations ok. Fit for use otherwise. Known false alarms (chi-square above the noise band, "
            "the NUM_REALIZATIONS below 100 lint and compensation EDGE), other MIN_/NUM_REALIZATIONS lint items "
            "(not calibrated), a missing aquifer or well PI multiplier (design choice), a check that could not "
            "run (WARN), outlier realizations and INFO notes are shown, not counted.\n",
        ]
        return "".join(lines)

    def pdf_pages(self) -> dict:
        s = self.status
        return {
            "verdict": self.level,
            "evidence": [i["evidence"] for i in self.fired],
            "inventory": self.inventory_line().replace("**", ""),
            "advisory": ADVISORY,
            "convention": CONVENTION,
            "caveat": CALIBRATION_CAVEAT,
            "worst": self.tables["well_misfit"].head(5),
            "forecast": self.tables["forecast_pXX"],
            "families": self.tables["family_misfit"],
            "parameters": self.tables["parameters"],
            "review": [f"{r.label} [{r.source} {r.code}] {r.text}" for r in self.review],
            "not_counted": [f"[{r.code}] {r.text}" for r in self.review if not r.counts],
            "warnings": [f"WARN: {r.text}" for r in self.review if r.level == "WARN"],
            "outliers": self.outliers_md().strip(),
            "history_controlled": self.history_controlled_md().strip(),
            "eoh": f"{s['end_of_history']} ({s['end_of_history_source']})",
        }


if __name__ == "__main__":
    sys.exit(main())
