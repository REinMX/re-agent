import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("pyscal")
pytest.importorskip("scipy")
pytest.importorskip("res2df")
from pyscal import WaterOil

SCRIPT = Path(__file__).parents[1] / "skills/scal-uncertainty/scripts/satfunc_fit.py"
spec = importlib.util.spec_from_file_location("satfunc_fit", SCRIPT)
fit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fit)


@pytest.mark.parametrize("kind", ["corey", "let"])
def test_recovers_known_parameters(kind):
    wo = WaterOil(swl=0.2, swcr=0.2, sorw=0.15, h=0.01)
    if kind == "corey":
        wo.add_corey_water(nw=3, krwend=0.6)
        wo.add_corey_oil(now=4, kroend=0.8)
        expected = {"NW": 3, "NOW": 4}
    else:
        wo.add_LET_water(l=2.4, e=3.2, t=1.2, krwend=0.6)
        wo.add_LET_oil(l=3.7, e=2.4, t=1.2, kroend=0.8)
        expected = {"LW": 2.4, "EW": 3.2, "TW": 1.2, "LO": 3.7, "EO": 2.4, "TO": 1.2}
    result = fit.fit_table(wo.table, kind)
    for name, value in expected.items():
        assert result["recommendation"][name] == pytest.approx(value, abs=1e-4)
    assert max(c["max_abs_error"] for c in result["curves"].values()) < 1e-6


def test_tabdims_mismatch_is_error(tmp_path):
    deck = tmp_path / "TEST.DATA"
    deck.write_text(
        "RUNSPEC\nOIL\nWATER\nTABDIMS\n2 1 24 /\nPROPS\nSWOF\n0.2 0 0.8 0\n0.4 0.1 0.4 0\n0.85 0.6 0 0\n1 1 0 0 /\n"
    )
    with pytest.raises((ValueError, RuntimeError), match="NTSFUN|table|record|keyword"):
        fit.read_tables(deck)


def test_plateau_limitation_is_reported():
    import pandas as pd

    table = pd.DataFrame(
        {
            "SW": [0.1, 0.2, 0.4, 0.6, 0.85, 1],
            "KRW": [0, 0, 0.02, 0.2, 0.6, 1],
            "KROW": [0.8, 0.8, 0.2, 0.02, 0, 0],
        }
    )
    result = fit.fit_table(table, "corey")
    assert result["limitations"]
    assert result["curves"]["KROW"]["max_abs_error"] > 0.001


def test_gas_corey_fit_uses_connate_water():
    from pyscal import GasOil

    go = GasOil(swl=0.2, sgcr=0, sorg=0.15, h=0.01)
    go.add_corey_gas(ng=3, krgend=0.6)
    go.add_corey_oil(nog=4, kroend=0.8)
    result = fit.fit_table(go.table, "corey", swl=0.2)
    assert result["recommendation"]["NG"] == pytest.approx(3, abs=1e-4)
    assert result["recommendation"]["NOG"] == pytest.approx(4, abs=1e-4)
    assert result["recommendation"]["SORG"] == pytest.approx(0.15)


def test_truncated_source_retains_true_pyscal_endpoint():
    wo = WaterOil(swl=0.2, swcr=0.2, sorw=0.15, h=0.01)
    wo.add_corey_water(nw=3, krwend=0.6)
    wo.add_corey_oil(now=4, kroend=0.8)
    source = wo.table[wo.table.SW <= 0.90001]
    result = fit.fit_table(source, "corey")
    assert result["recommendation"]["NW"] == pytest.approx(3, abs=1e-4)
    assert result["curves"]["KRW"]["max_abs_error"] < 1e-7


def test_endpoint_overrides_are_fixed_and_phase_specific():
    from pyscal import GasOil

    go = GasOil(swl=0.2, sgcr=0, sorg=0.15, h=0.01)
    go.add_corey_gas(ng=3, krgend=0.6)
    go.add_corey_oil(nog=4, kroend=0.8)
    result = fit.fit_table(go.table, "corey", swl=0.2, sgcr=0, sorg=0.15, sorw=0.4, krgend=0.6, krogend=0.8)
    assert result["recommendation"]["SORG"] == 0.15
    assert result["recommendation"]["KRGEND"] == 0.6
    assert result["recommendation"]["KROGEND"] == 0.8
    assert result["recommendation"]["NG"] == pytest.approx(3, abs=1e-4)
    assert result["recommendation"]["NOG"] == pytest.approx(4, abs=1e-4)
