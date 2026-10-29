"""Case QC must forward its resolved member inventory to real compensation."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "case_comp_integrity", ROOT / "skills/case-qc/scripts/case_qc.py"
)
cq = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cq)


def case_args(tmp_path, realizations):
    dist = tmp_path / "p.dist"
    dist.write_text("A UNIFORM 0 1\n")
    for iteration in (0, 1):
        for real in (0, 1):
            folder = tmp_path / f"realization-{real}" / f"iter-{iteration}"
            folder.mkdir(parents=True)
            (folder / "parameters.txt").write_text(f"A {0.3 + 0.3 * real}\n")
    return SimpleNamespace(
        runpaths=str(tmp_path / "realization-*/iter-1"),
        prior=str(tmp_path / "realization-*/iter-0"),
        gen_kw_dists=[dist],
        outdir=tmp_path / "out",
        timeout=30,
        realizations=realizations,
        expected=None,
        eclbase=None,
        keys=None,
        time_index="yearly",
        allow_partial=False,
    )


def run_compensation_only(monkeypatch, args, expected):
    actual = cq.run_tool

    def run(name, tool_args, outdir, timeout):
        if name == "compensation":
            return actual(name, tool_args, outdir, timeout)
        return {"status": "ok", "stdout": "", "stderr": ""}

    monkeypatch.setattr(cq, "run_tool", run)
    records = {}
    cq.check_runpaths(args, records, {}, expected, [])
    return records["compensation"]


@pytest.mark.parametrize("realizations", [None, "0-1,4"])
def test_resolved_case_inventory_reaches_compensation(tmp_path, monkeypatch, realizations):
    record = run_compensation_only(monkeypatch, case_args(tmp_path, realizations), 3)
    assert record["exit_code"] == 1
    assert "member" in record["stderr"].lower()


@pytest.mark.parametrize(
    ("used", "missing", "fit"), [([0, 1], [], True), ([0], [1], None), ([0, 1, 2], [], None)]
)
def test_a_partial_or_unrelated_misfit_manifest_gives_an_unknown_fit(
    tmp_path, monkeypatch, used, missing, fit
):
    args = case_args(tmp_path, None)
    manifest = args.outdir / "misfit" / "files" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    item = {"realizations_used": used, "missing": missing}
    manifest.write_text(
        json.dumps(
            {
                "ensembles": {"prior": item, "posterior": item},
                "issues": [],
                "misfit_vs_chi2": {"posterior": {"verdict": "inside the noise band"}},
            }
        )
    )
    record = run_compensation_only(monkeypatch, args, 2)
    assert record["exit_code"] == 0, record["stderr"]
    assert json.loads(record["stdout"])["runs"][0]["fit_good"] is fit
