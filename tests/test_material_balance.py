"""Material balance against OPM Flow runs with known in-place volumes.

tests/data/mbal holds three synthetic decks and the input that extract_case.py made from their Flow runs.
Regenerate with tests/data/mbal/make_mbal_data.py after a deck changes.
"""

import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
DATA = ROOT / "tests/data/mbal"
spec = importlib.util.spec_from_file_location("mbal", ROOT / "skills/material-balance/scripts/mbal.py")
mbal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mbal)


def run(case: str, fluid: str, *extra: str) -> tuple[int, dict]:
    reference = json.loads((DATA / case / "reference.json").read_text())
    args = [
        "--fluid", fluid,
        "--history", str(DATA / case / "history.csv"),
        "--pvt", str(DATA / case / "pvt.csv"),
        "--swi", f"{reference['swi']:.6f}",
        "--cw", str(reference["cw"]),
        "--cf", str(reference["cf"]),
        "--json", *extra,
    ]  # fmt: skip
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = mbal.main(args)
    return code, (json.loads(out.getvalue()) if code == 0 else {})


def reference(case: str) -> dict:
    return json.loads((DATA / case / "reference.json").read_text())


def test_volumetric_oil_recovers_simulator_stoiip():
    code, result = run("OILBOX", "oil")
    truth = reference("OILBOX")["stoiip_simulator"]
    fit = result["fits"]["none"]
    assert code == 0
    assert fit["in_place"] == pytest.approx(truth, rel=0.005)
    low, high = fit["in_place_range_within_data_error"]
    assert low <= truth <= high
    assert abs(result["diagnostics"]["f_over_et_trend"]) < 0.05
    assert fit["drive_indices_last_row"]["depletion"] > 0.95


def test_volumetric_gas_recovers_simulator_giip():
    code, result = run("GASBOX", "gas")
    truth = reference("GASBOX")["giip_simulator"]
    assert code == 0
    assert result["fits"]["none"]["in_place"] == pytest.approx(truth, rel=0.005)
    # The p/z line ignores rock and water compressibility, so it overestimates G a little.
    assert result["diagnostics"]["pz_line_giip"] == pytest.approx(truth, rel=0.03)
    assert result["diagnostics"]["pz_line_giip"] > truth


def test_aquifer_case_is_flagged_and_n_is_not_determined():
    code, result = run("OILBOX_AQ", "oil")
    truth = reference("OILBOX_AQ")["stoiip_simulator"]
    assert code == 0
    assert result["diagnostics"]["f_over_et_trend"] > 0.5
    # The volumetric fit puts all the aquifer energy into N.
    assert result["fits"]["none"]["in_place"] > 5 * truth
    fetkovich = result["fits"]["fetkovich"]
    low, high = fetkovich["in_place_range_within_data_error"]
    assert low <= truth <= high
    assert any("fetkovich: N is not determined" in w for w in result["warnings"])
    assert any("none: even the best fit misses F" in w for w in result["warnings"])


def test_fixed_stoiip_recovers_the_fetkovich_aquifer():
    truth = reference("OILBOX_AQ")
    code, result = run(
        "OILBOX_AQ", "oil", "--in-place", str(truth["stoiip_simulator"]), "--aquifer", "fetkovich"
    )
    fit = result["fits"]["fetkovich"]
    assert code == 0
    # Deck: AQUFETP volume 2.0E9 stb, ct 7.0E-6 1/psi, PI 20 stb/d/psi. K = ct W, tau = K / PI.
    assert fit["aquifer_constant"] == pytest.approx(2.0e9 * 7.0e-6, rel=0.03)
    assert fit["time_constant_days"] == pytest.approx(2.0e9 * 7.0e-6 / 20.0, rel=0.05)
    # Cumulative influx: the simulator reports surface volume. The fit gives reservoir volume (Bw about 1.02).
    assert fit["we_last_row"] == pytest.approx(truth["aquifer_influx_last_row"] * 1.02, rel=0.03)


def test_unit_mismatch_fails_closed(tmp_path, capsys):
    pvt = (DATA / "OILBOX/pvt.csv").read_text().splitlines()
    header, rows = pvt[0].split(","), [r.split(",") for r in pvt[1:]]
    rs = header.index("RS")
    for row in rows:
        row[rs] = str(float(row[rs]) * 1000)  # Mscf/stb written as scf/stb
    bad = tmp_path / "pvt.csv"
    bad.write_text("\n".join([",".join(header), *[",".join(r) for r in rows]]))
    code = mbal.main(
        ["--fluid", "oil", "--history", str(DATA / "OILBOX/history.csv"), "--pvt", str(bad),
         "--swi", "0.2", "--cw", "3e-6", "--cf", "4e-6"]
    )  # fmt: skip
    assert code == 1
    assert "units of RS and BG do not agree" in capsys.readouterr().err


def test_outdir_overlapping_inputs_is_rejected(tmp_path, capsys):
    # Finding 4 of the 2026-10-08 review: case_qc.py refuses an --outdir that overlaps its
    # inputs; mbal.py had no such guard, so mbal_table.csv/mbal.json could land next to
    # --history/--pvt. Inputs are copied to tmp_path so a regression cannot write into tests/data.
    case = tmp_path / "OILBOX"
    case.mkdir()
    for name in ("history.csv", "pvt.csv"):
        (case / name).write_bytes((DATA / "OILBOX" / name).read_bytes())
    code = mbal.main(
        ["--fluid", "oil", "--history", str(case / "history.csv"), "--pvt", str(case / "pvt.csv"),
         "--swi", "0.2", "--cw", "3e-6", "--cf", "4e-6", "--outdir", str(case)]
    )  # fmt: skip
    assert code == 2
    assert "overlaps" in capsys.readouterr().err
    assert sorted(p.name for p in case.iterdir()) == ["history.csv", "pvt.csv"]


def test_pressure_outside_pvt_table_fails_closed(tmp_path, capsys):
    history = tmp_path / "history.csv"
    history.write_text("P,NP,GP,WP\n9000,0,0,0\n8000,10,5,0\n7000,20,10,0\n6000,30,15,0\n")
    code = mbal.main(
        ["--fluid", "oil", "--history", str(history), "--pvt", str(DATA / "OILBOX/pvt.csv"),
         "--swi", "0.2", "--cw", "3e-6", "--cf", "4e-6"]
    )  # fmt: skip
    assert code == 1
    assert "outside the table" in capsys.readouterr().err


def test_too_few_points_fails_closed(tmp_path, capsys):
    history = tmp_path / "history.csv"
    history.write_text("P,NP,GP,WP\n3500,0,0,0\n3400,1000,500,0\n")
    code = mbal.main(
        ["--fluid", "oil", "--history", str(history), "--pvt", str(DATA / "OILBOX/pvt.csv"),
         "--swi", "0.2", "--cw", "3e-6", "--cf", "4e-6"]
    )  # fmt: skip
    assert code == 1
    assert "Need 3" in capsys.readouterr().err


def test_fetkovich_limits():
    times, pressures = [100.0, 200.0, 300.0], [3400.0, 3300.0, 3200.0]
    # A very short time constant behaves as a pot aquifer: We/K = Pi - P (with the step-mean pressure).
    pot = mbal.fetkovich_unit_influx(times, pressures, 3500.0, 1e-6)
    assert pot[-1] == pytest.approx(3500 - 0.5 * (3300 + 3200), rel=1e-6)
    # A very long time constant gives almost no influx.
    assert mbal.fetkovich_unit_influx(times, pressures, 3500.0, 1e9)[-1] < 1e-3


def load_extract_case():
    pytest.importorskip("res2df")
    spec = importlib.util.spec_from_file_location(
        "extract", ROOT / "skills/material-balance/scripts/extract_case.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("where", ["deck_dir", "ancestor"])
def test_extract_case_outdir_overlapping_deck_is_rejected(tmp_path, capsys, where):
    # Finding 4 of the 2026-10-08 review: same --outdir guard as case_qc.py and mbal.py.
    extract = load_extract_case()
    deck = tmp_path / "model" / "CASE.DATA"
    deck.parent.mkdir()
    deck.write_text("RUNSPEC\n")
    outdir = deck.parent if where == "deck_dir" else tmp_path
    assert extract.main([str(deck), "--outdir", str(outdir)]) == 2
    assert "overlaps" in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.rglob("*")) == ["CASE.DATA", "model"]


def test_extract_case_separate_outdir_passes_the_overlap_guard(tmp_path, capsys):
    extract = load_extract_case()
    deck = tmp_path / "model" / "CASE.DATA"
    deck.parent.mkdir()
    deck.write_text("RUNSPEC\n")
    # Exit 1 comes from the next check (no .UNSMRY), which proves the guard let it through.
    assert extract.main([str(deck), "--outdir", str(tmp_path / "out")]) == 1
    error = capsys.readouterr().err
    assert "overlaps" not in error
    assert "no summary file" in error


def test_extract_case_pvt_matches_deck():
    pytest.importorskip("res2df")
    reference_ = reference("OILBOX")
    assert reference_["rsi"] == pytest.approx(0.5)
    assert reference_["pb"] == pytest.approx(2500.0)
    assert reference_["swi"] == pytest.approx(0.2, abs=1e-3)
    spec_x = importlib.util.spec_from_file_location(
        "extract", ROOT / "skills/material-balance/scripts/extract_case.py"
    )
    extract = importlib.util.module_from_spec(spec_x)
    spec_x.loader.exec_module(extract)
    # 1/B is linear between table points, as in the simulator.
    assert extract.inverse_interp([2000, 2500], [1.5, 1.2], 2250) == pytest.approx(
        1 / (0.5 / 1.5 + 0.5 / 1.2)
    )
