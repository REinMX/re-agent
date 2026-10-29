"""uncertainty-register: file names from the contract; missing prior = gap, never filled."""

from __future__ import annotations

import csv
import textwrap
from pathlib import Path

import pytest
from synth_fmu import SKILLS, need, project_block, run, write_contracts, write_priors

SCRIPT = SKILLS / "uncertainty-register/scripts/uncertainty_register.py"


@pytest.fixture
def lab(tmp_path: Path):
    need("yaml")
    proj, env = write_contracts(tmp_path)
    write_priors(tmp_path)
    return proj, env


def _rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return {r["name"]: r for r in csv.DictReader(fh)}


def test_register_builds_from_contract_files(lab, tmp_path: Path):
    proj, env = lab
    out = tmp_path / "ur"
    result = run(SCRIPT, "--contract", proj, "--outdir", out, env=env)
    assert result.returncode == 0, result.stderr + result.stdout
    rows = _rows(out / "register.csv")
    assert {"PORO_MULT", "PERM_MULT", "SWL"} <= set(rows)
    for r in rows.values():
        if r["status"] == "ok":  # min/max priors: no invented percentiles
            assert r["min"] != "" and r["max"] != ""
            assert r["p10"] == r["p50"] == r["p90"] == ""
    assert "numerical" in result.stdout + (out / "register.md").read_text()


def test_missing_listed_file_exits_2(tmp_path: Path):
    need("yaml")
    proj_data = project_block(tmp_path)
    proj_data["uncertainty"]["prior_files"] = ["project/ert/input/distributions/absent_priors.txt"]
    proj, env = write_contracts(tmp_path, project=proj_data)
    write_priors(tmp_path)
    result = run(SCRIPT, "--contract", proj, "--outdir", tmp_path / "ur", env=env)
    assert result.returncode == 2
    assert "absent_priors.txt" in result.stderr


def test_missing_prior_is_gap_not_filled(lab, tmp_path: Path):
    proj, env = lab
    params = tmp_path / "parameters.csv"
    params.write_text(
        textwrap.dedent(
            """\
            group,name,distribution,arg1,arg2,notes
            ROCK,PORO_MULT,UNIFORM,0.8,1.2,ok row
            ROCK,BROKEN_PARAM,,,,incomplete on purpose
            """
        ),
        encoding="utf-8",
    )
    design = tmp_path / "design.csv"
    design.write_text("REAL,PORO_MULT,ORPHAN_PARAM\n0,1.0,1.0\n", encoding="utf-8")
    out = tmp_path / "out"
    result = run(
        SCRIPT,
        "--contract",
        proj,
        "--outdir",
        out,
        "--parameters-csv",
        params,
        "--design-matrix",
        design,
        env=env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    rows = _rows(out / "register.csv")
    assert rows["PORO_MULT"]["status"] == "ok" and rows["PORO_MULT"]["min"] == "0.8"
    assert rows["BROKEN_PARAM"]["status"] == "gap" and rows["BROKEN_PARAM"]["min"] == ""
    assert rows["ORPHAN_PARAM"]["status"] == "gap"
    assert rows["ORPHAN_PARAM"]["min"] == "" and rows["ORPHAN_PARAM"]["p50"] == ""
    md = (out / "register.md").read_text(encoding="utf-8")
    assert "ORPHAN_PARAM" in md and "Gaps" in md


def test_diff_against_previous(lab, tmp_path: Path):
    proj, env = lab
    a = tmp_path / "a.csv"
    a.write_text(
        "group,name,distribution,arg1,arg2,notes\nROCK,PORO_MULT,UNIFORM,0.8,1.2,a\n", encoding="utf-8"
    )
    b = tmp_path / "b.csv"
    b.write_text(
        "group,name,distribution,arg1,arg2,notes\n"
        "ROCK,PORO_MULT,UNIFORM,0.7,1.3,changed\n"
        "ROCK,NTG_MULT,UNIFORM,0.9,1.1,added\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    r1 = run(SCRIPT, "--contract", proj, "--outdir", out, "--parameters-csv", a, env=env)
    assert r1.returncode == 0, r1.stderr
    r2 = run(SCRIPT, "--contract", proj, "--outdir", out, "--parameters-csv", b, "--diff", env=env)
    assert r2.returncode == 0, r2.stderr + r2.stdout
    assert (out / "register_diff.json").is_file()
    diff_text = (out / "register_diff.md").read_text(encoding="utf-8")
    assert "NTG_MULT" in diff_text and "PORO_MULT" in diff_text
