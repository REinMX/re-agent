"""Tiny synthetic FMU projects for the contract-driven skill tests.

Everything is generated in a tmp folder: a v2 site contract (user-level profiles + project
file), an ensemble scratch with small SMSPEC/UNSMRY files (resdata writer, a few KB each),
profiles tables, decks and prior files. No project data, no machine paths.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills"
PY = sys.executable
CONTRACT_LIB_DIR = SKILLS / "stage-contract" / "scripts"
DECK = "TOY"
START = dt.datetime(2020, 1, 1)
# Report steps: 2020-02-01, then 1 JUL and 1 JAN each year to 2023-01-01 (end of 2022).
STEP_DATES = (
    [dt.datetime(2020, 2, 1)]
    + [dt.datetime(y, m, 1) for y in (2020, 2021, 2022) for m in (1, 7) if (y, m) != (2020, 1)]
    + [dt.datetime(2023, 1, 1)]
)


def need(*modules: str) -> None:
    """Skip with a clear reason when an engineering dependency is not installed (CI without Komodo)."""
    for name in modules:
        if importlib.util.find_spec(name) is None:
            pytest.skip(f"{name} not installed (it is in Komodo); install it to run this test")


def yaml_dump(data: Any) -> str:
    need("yaml")
    import yaml

    return yaml.safe_dump(data, sort_keys=False)


def profile_block(root: Path, **over: Any) -> dict[str, Any]:
    prof = {
        "agent_host": "linux",
        "execution_mode": "direct",
        "data_classification": "synthetic",
        "model_endpoint": "",
        "endpoint_approved": True,
        "python": PY,
        "activate": "true",
        "activate_shell": "bash",
        "komodo_release": "",
        "ert": {"executable": "ert", "version": "14"},
        "simulator": {"name": "FLOW", "executable": "flow", "wrapper": "native", "version": "test"},
        "queue": {"system": "LOCAL", "max_running": 2, "name": "", "options": {}},
        "scratch_root": str(root / "scratch"),
        "output_root": str(root / "deliverables"),
        "resinsight": {"bin": "", "headless": "none", "gui_launcher": "", "version": ""},
        "sumo": {"enabled": False, "environment": ""},
        "everest": {"enabled": False},
    }
    prof.update(over)
    return prof


def project_block(root: Path, **over: Any) -> dict[str, Any]:
    proj = {
        "schema_version": 2,
        "project": "toy",
        "field": "TOY",
        "eclbase": f"eclipse/model/{DECK}",
        "runpath": {"pattern": "realization-<IENS>/iter-<ITER>", "default_iter": "iter-0"},
        "expected_realizations": 5,
        "p10_p90": "numerical",
        "paths": {
            "root": str(root),
            "project": "project",
            "scratch": "${scratch_root}/toy_ens",
            "deliverables": "${output_root}",
            "scenarios": "scenarios",
        },
        "report_dates": {"end_of_prediction": "2023-01-01", "history_end": "2021-01-01"},
        "vectors": {
            "required_summary": ["FOPT", "FOPR", "FGPT", "FWPT", "FPR", "WOPR"],
            "headline": ["FOPT", "FGPT"],
            "at_date_default": ["FOPT", "FGPT"],
            "yearly_field_default": ["FOPT", "FGPT", "FWPT", "FWIT"],
            "gas_injection": None,
            "plot_required": ["FOPT", "FOPR"],
        },
        "wells": {"plot_default": "P1"},
        "uncertainty": {
            "parameters_csv": "project/ert/input/config/parameters.csv",
            "prior_files": ["project/ert/input/distributions/rock_priors.txt"],
            "design_matrix": None,
        },
        "prediction_case": {
            "template_ert": "project/ert/model/toy.ert",
            "parameters_csv": "project/ert/input/config/parameters.csv",
            "cases_root": "project/ert/cases",
        },
        "scenario_qc": {
            "baseline": "scenarios/baseline",
            "headline_vector": "FOPT",
            "tolerances": {"field_fopt_end_rel": 0.02, "untouched_well_rel": 0.01},
            "require_fingerprint_match_runmeta": True,
            "fail_on_missing_vector": True,
            "coupled_injectors_follow_producers": True,
            "exclude_demo": True,
            "compare_vectors": ["FOPT", "FOPR", "WOPR", "WBHP"],
        },
        "gate_default_stage": "VPBO",
        "gate_stages": {
            "BPBO": {"mode_default": "draft", "required_artifacts": ["profiles", "bands"]},
            "VPBO": {"mode_default": "draft", "required_artifacts": ["profiles", "bands"]},
            "DG0": {"mode_default": "strict", "required_artifacts": ["profiles", "bands"]},
        },
    }
    for key, value in over.items():
        if value is None and key in proj:
            del proj[key]
        else:
            proj[key] = value
    return proj


def write_contracts(
    root: Path, *, project: dict[str, Any] | None = None, profile: dict[str, Any] | None = None
) -> tuple[Path, dict[str, str]]:
    """Write user-level profiles + project contract. Return (project contract path, env)."""
    root.mkdir(parents=True, exist_ok=True)
    user = root / "home/.config/re-agent/site.contract.yml"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(
        yaml_dump(
            {
                "schema_version": 2,
                "active_profile": "home",
                "profiles": {"home": profile or profile_block(root)},
            }
        ),
        encoding="utf-8",
    )
    proj = root / "site.contract.yml"
    proj.write_text(yaml_dump(project or project_block(root)), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("RE_AGENT_")}
    env["RE_AGENT_CONTRACT"] = str(user)
    env["HOME"] = str(root / "home")
    env["USERPROFILE"] = str(root / "home")  # Windows home for ~ expansion
    return proj, env


def scale(real: int) -> float:
    return 1.0 + 0.1 * real


def vector_values(real: int, days: float, *, with_gas_inj: bool = False) -> dict[str, float]:
    s = scale(real)
    rates = {"FOPR": 100 * s, "FGPR": 1.0e4 * s, "FWPR": 50 * s, "FWIR": 150.0}
    vals = {
        **rates,
        "FOPT": rates["FOPR"] * days,
        "FGPT": rates["FGPR"] * days,
        "FWPT": rates["FWPR"] * days,
        "FWIT": rates["FWIR"] * days,
        "FPR": 250.0 - 0.01 * days,
        "WOPR:P1": 60 * s,
        "WOPR:P2": 40 * s,
        "WOPT:P1": 60 * s * days,
        "WOPT:P2": 40 * s * days,
        "WWPR:P1": 50 * s,
        "WWPT:P1": 50 * s * days,
        "WWIR:I1": 150.0,
        "WWIT:I1": 150.0 * days,
        "WBHP:P1": 150.0,
    }
    if with_gas_inj:
        vals["FGIR"] = 5.0e3
        vals["FGIT"] = 5.0e3 * days
    return vals


def write_summary(case: Path, real: int, *, drop: tuple[str, ...] = (), with_gas_inj: bool = False) -> None:
    need("resdata")
    from resdata.summary import Summary

    case.parent.mkdir(parents=True, exist_ok=True)
    keys = [k for k in vector_values(real, 0.0, with_gas_inj=with_gas_inj) if k not in drop]
    writer = Summary.writer(str(case), START, 4, 4, 2)
    for key in keys:
        if ":" in key:
            fam, well = key.split(":", 1)
            writer.add_variable(fam, wgname=well)
        else:
            writer.add_variable(key)
    for step, when in enumerate(STEP_DATES, start=1):
        days = (when - START).days
        tstep = writer.add_t_step(step, float(days))
        values = vector_values(real, float(days), with_gas_inj=with_gas_inj)
        for key in keys:
            tstep[key] = values[key]
    writer.fwrite()
    case.with_suffix(".DATA").write_text("-- synthetic test deck\nRUNSPEC\n", encoding="utf-8")


def make_ensemble(
    scratch: Path,
    n: int = 5,
    *,
    iteration: str = "iter-0",
    ok: bool = True,
    drop: tuple[str, ...] = (),
    with_gas_inj: bool = False,
) -> None:
    for real in range(n):
        runpath = scratch / f"realization-{real}" / iteration
        write_summary(runpath / "eclipse/model" / DECK, real, drop=drop, with_gas_inj=with_gas_inj)
        if ok:
            (runpath / "OK").write_text("ok\n", encoding="utf-8")


def days_to(when: dt.datetime) -> float:
    return float((when - START).days)


def write_profiles(folder: Path, *, n: int = 5, p1_factor: float = 1.0, p2_factor: float = 1.0) -> Path:
    """profiles_long.csv (REAL, DATE, KEY, VALUE) without a simulator run."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["REAL,DATE,KEY,VALUE"]
    for real in range(n):
        for when in STEP_DATES:
            vals = vector_values(real, days_to(when))
            vals["WOPR:P1"] *= p1_factor
            vals["WOPR:P2"] *= p2_factor
            vals["FOPR"] = vals["WOPR:P1"] + vals["WOPR:P2"]
            vals["FOPT"] = vals["FOPR"] * days_to(when)
            for key in ("FOPT", "FOPR", "FGPT", "FWPT", "FPR", "WOPR:P1", "WOPR:P2", "WBHP:P1"):
                lines.append(f"{real},{when.date()},{key},{vals[key]}")
    path = folder / "profiles_long.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_deck(path: Path, orat: float = 100.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "RUNSPEC\nSCHEDULE\n"
        "WELSPECS\n 'P1' 'G' 1 1 1* 'OIL' /\n 'P2' 'G' 2 2 1* 'OIL' /\n/\n"
        f"WCONPROD\n 'P1' 'OPEN' 'ORAT' {orat} 4* 150 /\n 'P2' 'OPEN' 'ORAT' 100 4* 150 /\n/\n"
        "TSTEP\n 10*30 /\nEND\n",
        encoding="utf-8",
    )
    return path


def write_priors(root: Path) -> None:
    cfg = root / "project/ert/input/config"
    dist = root / "project/ert/input/distributions"
    cfg.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    (cfg / "parameters.csv").write_text(
        "group,name,distribution,arg1,arg2,notes\n"
        "ROCK,PORO_MULT,UNIFORM,0.8,1.2,porosity multiplier\n"
        "ROCK,PERM_MULT,LOGUNIF,0.5,2.0,permeability multiplier\n"
        "RELPERM,SWL,UNIFORM,0.1,0.2,connate water\n",
        encoding="utf-8",
    )
    (dist / "rock_priors.txt").write_text(
        "PORO_MULT UNIFORM 0.8 1.2\nPERM_MULT LOGUNIF 0.5 2.0\n", encoding="utf-8"
    )


def write_ert_template(root: Path) -> Path:
    model = root / "project/ert/model"
    model.mkdir(parents=True, exist_ok=True)
    (root / "project/ert/input/templates").mkdir(parents=True, exist_ok=True)
    (root / "project/ert/input/templates/rock.tmpl").write_text("<PORO_MULT>\n", encoding="utf-8")
    ert = model / "toy.ert"
    ert.write_text(
        "NUM_REALIZATIONS      10\n"
        "RANDOM_SEED           1\n"
        "QUEUE_SYSTEM          LOCAL\n"
        "DEFINE  <CASE_DIR>      toy_ens\n"
        "DEFINE  <SCRATCH>       <CONFIG_PATH>/../../../scratch\n"
        "RUNPATH  <SCRATCH>/<CASE_DIR>/realization-<IENS>/iter-<ITER>\n"
        "ECLBASE  eclipse/model/TOY\n"
        "GEN_KW  ROCK  ../input/templates/rock.tmpl  eclipse/include/rock.inc  ../input/distributions/rock_priors.txt\n"
        "FORWARD_MODEL COPY_DIRECTORY(<FROM>=<CONFIG_PATH>/../../eclipse/include, <TO>=eclipse)\n"
        "FORWARD_MODEL FLOW\n",
        encoding="utf-8",
    )
    return ert


def run(script: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    import subprocess

    return subprocess.run(
        [PY, str(script), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        check=False,
    )
