"""ahm-ensemble-views: ensemble_fields.py on synthetic prior and posterior runpaths (EGRID + INIT from resdata)."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from synth_fmu import need

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/ahm-ensemble-views/scripts/ensemble_fields.py"
sys.path.insert(0, str(ROOT / "skills/resinsight-views/scripts"))

import grdecl  # noqa: E402
import view_spec  # noqa: E402

DIMS = (4, 3, 2)
INACTIVE = (5,)  # global index 5 = (i, j, k) (2, 2, 1)
N_CELLS = 24
N_ACTIVE = 23
LAYER = [1] * 11 + [2] * 12  # k of each active cell
N_MEMBERS = 10
# Member offsets: mean 0, sample sd 1 (ddof 1).
_CENTRED = [r - (N_MEMBERS - 1) / 2 for r in range(N_MEMBERS)]
OFFSETS = [x / math.sqrt(sum(y * y for y in _CENTRED) / (N_MEMBERS - 1)) for x in _CENTRED]


def write_ensemble(root: Path, iteration: str, poro, permx, base: str = "SYNTH", **grid) -> str:
    """One runpath per member: realization-<r>/<iteration>/model/BASE.{DATA,EGRID,INIT}.
    poro and permx: a function (member, active index) -> value."""
    need("resdata", "numpy")
    sys.path.insert(0, str(Path(__file__).parent))
    from synth_grid import make_case

    grid = {"dims": DIMS, "inactive": INACTIVE} | grid
    n_active = math.prod(grid["dims"]) - len(grid["inactive"])
    members = grid.pop("members", range(N_MEMBERS))
    for r in members:
        model = root / f"realization-{r}" / iteration / "model"
        make_case(
            model,
            base=base,
            poro=[poro(r, c) for c in range(n_active)],
            permx=[permx(r, c) for c in range(n_active)],
            **grid,
        )
        (model / f"{base}.DATA").write_text("-- synthetic\n")
    return str(root / "realization-*" / iteration)


def prior_poro(r, c):
    return 0.20 + 0.02 * OFFSETS[r]


def prior_perm(r, c):
    return 10 ** (2.0 + 0.5 * OFFSETS[r])


def run(
    tmp_path: Path, prior: str, posterior: str, *extra: str, outdir: Path | None = None
) -> tuple[int, Path]:
    import ensemble_fields

    out = outdir or tmp_path / "out"
    code = ensemble_fields.main(["--prior", prior, "--posterior", posterior, "--outdir", str(out), *extra])
    return code, out


@pytest.fixture(autouse=True)
def _script_path():
    sys.path.insert(0, str(SCRIPT.parent))
    yield


@pytest.fixture
def prior(tmp_path):
    return write_ensemble(tmp_path / "ens", "iter-0", prior_poro, prior_perm)


def fields(out: Path) -> dict:
    def refuse(token):
        raise AssertionError(f"fields.json has {token}: not valid JSON")

    return json.loads((out / "fields.json").read_text(), parse_constant=refuse)


def codes(out: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for item in fields(out)["findings"]:
        found.setdefault(item["code"], []).append(item["property"])
    return found


def array(out: Path, keyword: str) -> list[float]:
    name, values = grdecl.read_grdecl(out / "arrays" / f"{keyword}.grdecl", expected=N_CELLS)
    assert name == keyword
    return values


def active(values: list[float]) -> list[float]:
    return [v for g, v in enumerate(values) if g not in INACTIVE]


# findings and exit codes ------------------------------------------------------------------------------
def test_collapse_is_a_warning_and_exit_1(tmp_path, prior):
    post = write_ensemble(
        tmp_path / "ens",
        "iter-4",
        lambda r, c: 0.22 + 0.002 * OFFSETS[r],
        lambda r, c: 10 ** (2.0 + 0.05 * OFFSETS[r]),
    )
    code, out = run(tmp_path, prior, post)
    assert code == 1
    assert codes(out) == {"AHM-FIELD-COLLAPSE": ["PORO", "PERMX"]}
    assert [f["value"] for f in fields(out)["findings"]] == [1.0, 1.0]
    for value in active(array(out, "POROSDR")) + active(array(out, "PERMXSDR")):
        assert value == pytest.approx(0.1, rel=1e-3)
    assert active(array(out, "POROSHF")) == pytest.approx([1.0] * N_ACTIVE, rel=1e-3)  # (0.22 - 0.20) / 0.02


def test_large_shift_is_a_warning_without_collapse(tmp_path, prior):
    post = write_ensemble(tmp_path / "ens", "iter-4", lambda r, c: 0.26 + 0.02 * OFFSETS[r], prior_perm)
    code, out = run(tmp_path, prior, post)
    assert code == 1
    assert codes(out) == {"AHM-FIELD-SHIFT-LARGE": ["PORO"], "AHM-FIELD-NO-UPDATE": ["PERMX"]}
    assert active(array(out, "POROSHF")) == pytest.approx([3.0] * N_ACTIVE, rel=1e-3)
    assert active(array(out, "POROSDR")) == pytest.approx([1.0] * N_ACTIVE, rel=1e-3)


def test_no_update_is_info_and_exit_0(tmp_path, prior):
    post = write_ensemble(tmp_path / "ens", "iter-4", prior_poro, prior_perm)
    code, out = run(tmp_path, prior, post)
    assert code == 0
    assert codes(out) == {"AHM-FIELD-NO-UPDATE": ["PORO", "PERMX"]}
    assert all(f["severity"] == "info" for f in fields(out)["findings"])


def test_few_members_is_info(tmp_path):
    few = {"members": range(3)}
    prior = write_ensemble(tmp_path / "ens", "iter-0", prior_poro, prior_perm, **few)
    post = write_ensemble(tmp_path / "ens", "iter-4", prior_poro, prior_perm, **few)
    code, out = run(tmp_path, prior, post)
    assert code == 0
    assert codes(out)["AHM-FIELD-SMALL-ENSEMBLE"] == ["PORO", "PERMX"]
    assert fields(out)["prior"]["realizations"] == [0, 1, 2]


def test_permx_is_compared_in_log10(tmp_path, prior):
    # One decade up, same log spread: in log10, SHIFT = 1 / 0.5 = 2 and SDRATIO = 1.
    # In linear space the sd would be 10 times larger: SDRATIO 10.
    post = write_ensemble(tmp_path / "ens", "iter-4", prior_poro, lambda r, c: 10 ** (3.0 + 0.5 * OFFSETS[r]))
    code, out = run(tmp_path, prior, post)
    assert fields(out)["properties"]["PERMX"]["space"] == "log10"
    assert active(array(out, "PERMXSDR")) == pytest.approx([1.0] * N_ACTIVE, rel=1e-4)
    assert active(array(out, "PERMXSHF")) == pytest.approx([2.0] * N_ACTIVE, rel=1e-4)
    assert active(array(out, "PERMXPRM")) == pytest.approx([2.0] * N_ACTIVE, abs=1e-5)
    assert active(array(out, "PERMXPOM")) == pytest.approx([3.0] * N_ACTIVE, abs=1e-5)
    assert code == 1 and codes(out) == {"AHM-FIELD-SHIFT-LARGE": ["PERMX"], "AHM-FIELD-NO-UPDATE": ["PORO"]}


def test_cells_without_prior_spread_are_excluded_and_counted(tmp_path):
    flat = range(0, 6)  # active cells 0-5 are the same in each prior member

    def poro(r, c):
        return 0.2 if c in flat else prior_poro(r, c)

    prior = write_ensemble(tmp_path / "ens", "iter-0", poro, prior_perm)
    post = write_ensemble(
        tmp_path / "ens", "iter-4", lambda r, c: 0.3 if c in flat else prior_poro(r, c), prior_perm
    )
    code, out = run(
        tmp_path, prior, post, "--case", str(tmp_path / "ens/realization-0/iter-0/model/SYNTH.EGRID")
    )
    result = fields(out)
    assert code == 0 and codes(out) == {"AHM-FIELD-NO-UPDATE": ["PORO", "PERMX"]}
    assert result["notes"] == [
        "PORO: 6 of 23 active cells have no prior spread. They have no SDRATIO or SHIFT: "
        "0 in the arrays, out of the statistics and the findings"
    ]
    assert active(array(out, "POROSDR"))[:6] == [0.0] * 6 and active(array(out, "POROSHF"))[:6] == [0.0] * 6
    assert active(array(out, "POROSDR"))[6:] == pytest.approx([1.0] * 17)
    assert result["notes"][0] in json.loads((out / "spec.json").read_text())["notes"]
    assert "6 of 23 active cells have no prior spread" in (out / "report.md").read_text()


def test_no_prior_spread_anywhere_gives_null_not_nan(tmp_path):
    prior = write_ensemble(tmp_path / "ens", "iter-0", lambda r, c: 0.2, prior_perm)
    post = write_ensemble(tmp_path / "ens", "iter-4", lambda r, c: 0.2, prior_perm)
    code, out = run(tmp_path, prior, post)
    item = fields(out)["properties"]["PORO"]
    assert code == 0 and codes(out)["AHM-FIELD-NO-SPREAD"] == ["PORO"]
    assert item["sdratio_median"] is None and item["abs_shift_mean"] is None


# input errors: exit 2 ---------------------------------------------------------------------------------
def test_prior_and_posterior_grids_differ(tmp_path, prior, capsys):
    post = write_ensemble(tmp_path / "ens", "iter-4", prior_poro, prior_perm, inactive=(6,))
    code, out = run(tmp_path, prior, post)
    assert code == 2 and "prior and the posterior grids differ" in capsys.readouterr().err
    assert not out.exists()


def test_members_with_different_grids(tmp_path, prior, capsys):
    post = write_ensemble(tmp_path / "ens", "iter-4", prior_poro, prior_perm)
    model = tmp_path / "ens/realization-3/iter-4/model"
    sys.path.insert(0, str(Path(__file__).parent))
    from synth_grid import make_case

    make_case(model, dims=(4, 3, 2), inactive=(7,))
    code, _ = run(tmp_path, prior, post)
    assert code == 2 and "posterior realization 3: the grid or ACTNUM differs" in capsys.readouterr().err


def test_missing_realization_against_the_expected_set(tmp_path, prior, capsys):
    post = write_ensemble(tmp_path / "ens", "iter-4", prior_poro, prior_perm, members=[0, 1, 2, 4])
    code, _ = run(tmp_path, prior, post, "--realizations", "0-4")
    err = capsys.readouterr().err
    assert code == 2 and "posterior" in err and "Missing realization folders: [3]" in err


def test_case_with_another_grid(tmp_path, prior, capsys):
    sys.path.insert(0, str(Path(__file__).parent))
    from synth_grid import make_case

    other = make_case(tmp_path / "other", dims=(4, 3, 3))
    code, _ = run(tmp_path, prior, prior, "--case", str(other))
    assert code == 2 and "the grid or ACTNUM differs from the ensembles" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--layer", "3"], "--layer 3 is outside 1..2"),
        (["--properties", "PERMX,PERMXY"], "different first 5 letters"),
        (["--properties", "NTG"], "has no NTG"),
    ],
)
def test_bad_arguments(tmp_path, prior, capsys, extra, message):
    code, _ = run(tmp_path, prior, prior, *extra)
    assert code == 2 and message in capsys.readouterr().err


def test_outdir_guards(tmp_path, prior, capsys):
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.txt").write_text("keep")
    code, _ = run(tmp_path, prior, prior, outdir=out)
    assert code == 2 and "is not empty" in capsys.readouterr().err
    assert sorted(p.name for p in out.iterdir()) == ["old.txt"]
    code, _ = run(tmp_path, prior, prior, "--force", outdir=out)
    assert code == 0 and (out / "fields.json").is_file()
    afile = tmp_path / "afile"
    afile.write_text("")
    code, _ = run(tmp_path, prior, prior, "--force", outdir=afile)
    assert code == 2 and "is a file" in capsys.readouterr().err


# outputs ----------------------------------------------------------------------------------------------
def shift_layer_2(r, c):
    return prior_poro(r, c) + (0.06 if LAYER[c] == 2 else 0.0)


def test_per_layer_rows(tmp_path, prior):
    post = write_ensemble(tmp_path / "ens", "iter-4", shift_layer_2, prior_perm)
    _, out = run(tmp_path, prior, post)
    with (out / "per_layer.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["property"], r["k"], r["n_active"]) for r in rows] == [
        ("PORO", "1", "11"),
        ("PORO", "2", "12"),
        ("PERMX", "1", "11"),
        ("PERMX", "2", "12"),
    ]
    poro = {r["k"]: r for r in rows if r["property"] == "PORO"}
    assert float(poro["1"]["abs_shift_mean"]) == pytest.approx(0.0, abs=1e-4)
    assert float(poro["2"]["abs_shift_mean"]) == pytest.approx(3.0, rel=1e-3)
    assert float(poro["2"]["posterior_mean"]) == pytest.approx(0.26, rel=1e-5)
    assert float(poro["2"]["collapsed_fraction"]) == 0.0


def test_default_layer_is_the_largest_mean_abs_shift_and_layer_overrides(tmp_path, prior):
    post = write_ensemble(tmp_path / "ens", "iter-4", shift_layer_2, prior_perm)
    _, out = run(tmp_path, prior, post)
    item = fields(out)["properties"]["PORO"]
    assert item["view_layer"] == 2 and item["view_layer_rule"] == "the layer with the largest mean abs SHIFT"
    _, out = run(tmp_path, prior, post, "--layer", "1", outdir=tmp_path / "out1")
    item = fields(out)["properties"]["PORO"]
    assert item["view_layer"] == 1 and item["view_layer_rule"] == "--layer"


def test_whole_grid_arrays_round_trip_with_zero_in_inactive_cells(tmp_path, prior):
    post = write_ensemble(tmp_path / "ens", "iter-4", shift_layer_2, prior_perm)
    _, out = run(tmp_path, prior, post)
    names = sorted(p.stem for p in (out / "arrays").iterdir())
    assert names == sorted(f"{p}{t}" for p in ("PORO", "PERMX") for t in ("PRM", "POM", "SDR", "SHF"))
    assert all(len(name) <= 8 for name in names)
    for name in names:
        values = array(out, name)
        assert values[INACTIVE[0]] == 0.0
    poro_prior = array(out, "POROPRM")
    assert active(poro_prior) == pytest.approx([0.2] * N_ACTIVE, rel=1e-5)
    assert active(array(out, "POROPOM")) == pytest.approx([0.2] * 11 + [0.26] * 12, rel=1e-5)


def test_spec_passes_parse_spec_with_relative_paths(tmp_path, prior, monkeypatch):
    post = write_ensemble(tmp_path / "ens", "iter-4", shift_layer_2, prior_perm)
    monkeypatch.chdir(tmp_path)
    code, out = run(
        tmp_path, prior, post, "--case", "ens/realization-0/iter-4/model/SYNTH.EGRID", outdir=Path("rel")
    )
    assert code == 1
    spec = view_spec.load_spec(tmp_path / "rel/spec.json")
    assert spec.case == tmp_path / "ens/realization-0/iter-4/model/SYNTH.EGRID"
    assert spec.out == tmp_path / "rel/views"
    assert {a.name for a in spec.arrays} == {
        f"{p}{t}" for p in ("PORO", "PERMX") for t in ("PRM", "POM", "SDR", "SHF")
    }
    assert all(a.file.is_file() for a in spec.arrays)
    poro_views = [v for v in spec.views if v.name.startswith("poro_")]
    assert [v.name for v in poro_views] == ["poro_prm_k2", "poro_pom_k2", "poro_sdr_k2", "poro_shf_k2"]
    assert all(v.range.k == (2, 2) for v in poro_views)
    assert fields(tmp_path / "rel")["spec"] == str(tmp_path / "rel/spec.json")


def test_no_case_no_spec(tmp_path, prior):
    _, out = run(tmp_path, prior, prior)
    assert not (out / "spec.json").exists() and "spec" not in fields(out)


def test_script_does_not_import_resinsight(tmp_path, prior):
    driver = (
        "import sys, runpy; sys.argv = sys.argv[1:]\n"
        "try:\n    runpy.run_path(sys.argv[0], run_name='__main__')\n"
        "except SystemExit as e:\n    code = e.code\n"
        "print(code, sorted(m for m in sys.modules if m.split('.')[0] in ('rips', 'ri_session')))\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            driver,
            str(SCRIPT),
            "--prior",
            prior,
            "--posterior",
            prior,
            "--outdir",
            str(tmp_path / "o"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.stdout.strip().splitlines()[-1] == "0 []", proc.stderr
