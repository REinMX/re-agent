"""Public Drogon v2 jobs: physical invariants and ERT observation migration."""

import importlib.util
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "templates/drogon-ahm-v2"
sys.path.insert(0, str(EXAMPLE / "jobs"))


def load(name, folder="jobs"):
    spec = importlib.util.spec_from_file_location(name, EXAMPLE / folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lib = load("drogon_v2_lib")
scal = load("drogon_scal")
obs = load("make_obs", "tools")
fields = load("make_fields", "tools")
post = load("drogon_post")


def static():
    return dict(
        actnum=np.ones(5),
        depth=np.array([90.0, 95.0, 100.0, 105.0, 110.0]),
        eqlnum=np.ones(5),
        swatinit=np.array([0.2, 0.5, 0.8, 0.9, 1.0]),
        swl=np.full(5, 0.1),
        owc=np.array([100.0]),
        permx=np.full(5, 100.0),
        permy=np.full(5, 50.0),
        permz=np.full(5, 10.0),
    )


def test_grdecl_roundtrip_and_repeats():
    np.testing.assert_allclose(
        lib.read_grdecl("LNPERMMULT\n2*0.5 -- repeat\n 1\n/", "LNPERMMULT", 3), [0.5, 0.5, 1]
    )
    np.testing.assert_allclose(lib.read_grdecl(lib.write_grdecl("X", np.arange(11)), "X", 11), np.arange(11))


@pytest.mark.parametrize("text", ["Y\n1\n/", "X\n1\n/", "X\nnan 1\n/"])
def test_grdecl_missing_or_wrong_size(text):
    with pytest.raises(ValueError):
        lib.read_grdecl(text, "X", 3)


def test_permeability_log_field_and_vertical_multiplier():
    result = lib.perm_arrays(static(), np.full(5, np.log(2)), 0.3)
    np.testing.assert_allclose(result["PERMX"], 200)
    np.testing.assert_allclose(result["PERMY"], 100)
    np.testing.assert_allclose(result["PERMZ"], 6)


def test_active_nonfinite_field_rejected():
    with pytest.raises(ValueError, match="invalid active"):
        lib.perm_arrays(static(), np.full(5, np.nan), 1)


def test_inactive_field_is_not_used():
    data = static()
    data["actnum"][0] = 0
    field = np.zeros(5)
    field[0] = np.nan
    np.testing.assert_allclose(lib.perm_arrays(data, field, 1)["PERMX"], 100)


def test_saturation_height_is_monotone():
    h, sw = lib.sw_height_curve(np.array([1.0, 2.0, 3.0, 4.0]), np.array([0.8, 0.9, 0.4, 0.5]))
    assert np.all(np.diff(sw) <= 0)
    np.testing.assert_allclose(lib.sw_of_h((h, sw), np.array([-1.0, 0.0, 100.0])), [1.0, 1.0, 0.4])


def test_contact_shallower_removes_hydrocarbon_below_new_fwl():
    sw, _ = lib.remap_swatinit(static(), np.array([-6.0]))
    np.testing.assert_allclose(sw[1:], 1)
    assert 0.1 <= sw[0] <= 1


def test_contact_deeper_reopens_new_oil_cells():
    sw, counts = lib.remap_swatinit(static(), np.array([6.0]))
    assert sw[3] == pytest.approx(10 / 11)
    assert sw[4] == 1
    assert counts["rose_above_fwl"] == 2


def test_zero_shift_preserves_geomodel_above_contact():
    sw, _ = lib.remap_swatinit(static(), np.array([0.0]))
    np.testing.assert_allclose(sw[:2], [0.2, 0.5])
    np.testing.assert_allclose(sw[2:], 1)


def test_group_order_maps_two_independent_interpolations():
    rows = [{"SATNUM": "2", "GROUP": "B"}, {"SATNUM": "1", "GROUP": "A"}]
    assert scal.interpolation_values(
        rows, {"SCAL_WO_A": -1, "SCAL_WO_B": 1, "SCAL_GO_A": 0.2, "SCAL_GO_B": 0.3}
    ) == [[-1, 1], [0.2, 0.3]]


@pytest.mark.parametrize("rows", [[{"SATNUM": "2", "GROUP": "A"}], [{"SATNUM": "1", "GROUP": "A"}] * 2])
def test_group_mapping_refuses_missing_or_duplicate_satnums(rows):
    with pytest.raises(ValueError, match="contiguous"):
        scal.interpolation_values(rows, {})


def test_group_mapping_refuses_missing_parameter():
    with pytest.raises(KeyError):
        scal.interpolation_values([{"SATNUM": "1", "GROUP": "A"}], {})


def test_fields_are_seeded_zone_independent_and_have_requested_log_sd():
    dims = (12, 10, 4)
    zones = np.repeat([1, 2], 240)
    a = fields.gaussian_field(dims, zones, [100, 100, 2], 10)
    b = fields.gaussian_field(dims, zones, [100, 100, 2], 11)
    np.testing.assert_array_equal(a, fields.gaussian_field(dims, zones, [100, 100, 2], 10))
    assert not np.allclose(a, b)
    assert abs(a[zones == 1].mean()) > 1e-6
    ensemble = np.array([fields.gaussian_field(dims, zones, [100, 100, 2], seed) for seed in range(100)])
    assert ensemble.mean() == pytest.approx(0, abs=0.12)
    assert ensemble.std() == pytest.approx(np.log(3) / 2, abs=0.12)


def test_native_observation_values_and_ert_parser_order():
    pytest.importorskip("ert")
    from ert.config import ErtConfig
    from ert.config._observations import RFTObservation
    from ert.config.parsing.observations_parser import parse_observations

    old = "SUMMARY_OBSERVATION Q { VALUE = 12.3456; ERROR = 2; DATE = 2019-01-01; KEY = WOPR:A1; };\nGENERAL_OBSERVATION OLD { DATA = OLD; };"
    points = {"R_A2": [dict(date="2018-03-01", value=300.123, error=2, east=10.0, north=20.0, tvd=100.0)]}
    text = obs.build_observations(old, points, {"A1": (10.0, 20.0)}, {"A1": "2019-01-01"}, 1000)
    assert "VALUE = 12.3456; ERROR = 2;" in text
    assert "GENERAL_OBSERVATION" not in text
    parsed = parse_observations(text, "obs")
    assert len(parsed) == 3
    config = ErtConfig.from_dict({"NUM_REALIZATIONS": 2})
    rft = RFTObservation.from_obs_dict(".", parsed[1], config.shape_registry)[0]
    assert (rft.value, rft.error, rft.east, rft.north, rft.tvd) == (300.123, 2, 10, 20, 100)


def test_breakthrough_requires_crossing():
    pd = pytest.importorskip("pandas")
    series = pd.Series([0, 0.05, 0.2], index=pd.date_range("2020-01-01", periods=3))
    assert obs.first_crossing(series, 0.1) == "2020-01-03"
    assert obs.first_crossing(series, 0.5) is None


def test_qc_signed_volume_convention():
    result = post.volume_report({"PORV": 200, "SWATINIT_HCVOL": 100, "SWAT_HCVOL": 90, "HC_BELOW_FWL": 5})
    assert result["HC_BELOW_FWL_PCT_HC"] == -5
    assert result["HC_CHANGE_PCT"] == -10


def test_real_pyscal_per_satnum_interpolation(tmp_path):
    pytest.importorskip("pyscal")
    out = tmp_path / "tables.inc"
    params = tmp_path / "parameters.txt"
    import csv

    groups = sorted({r["GROUP"] for r in csv.DictReader((EXAMPLE / "scal/satnum_groups.csv").open())})
    params.write_text(
        "".join(
            f"DROGON:SCAL_{kind}_{g} {(-1 if i % 2 else 1)}\n"
            for i, g in enumerate(groups)
            for kind in ("WO", "GO")
        )
    )
    assert (
        scal.main(
            [
                str(EXAMPLE / "scal/drogon_scalrec.csv"),
                str(EXAMPLE / "scal/satnum_groups.csv"),
                "--params",
                str(params),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    text = out.read_text()
    assert "SWOF" in text and "SGOF" in text
    from res2df import satfunc

    frame = satfunc.df(text, ntsfun=12)
    assert frame.groupby(["KEYWORD", "SATNUM"]).ngroups == 24


def test_pyscal_printed_duplicate_node_is_removed():
    text = "SWOF\n0.1 0 1 2\n0.1 0 1 2\n0.2 0.1 .9 1\n/\n"
    assert scal.strict_saturation_nodes(text) == "SWOF\n0.1 0 1 2\n0.2 0.1 .9 1\n/\n"


def test_conflicting_saturation_duplicate_is_rejected():
    with pytest.raises(ValueError, match="conflicting"):
        scal.strict_saturation_nodes("SWOF\n0.1 0 1 2\n0.1 0 0.8 2\n/\n")


def test_ert_field_keyword_fits_eclipse_eight_character_limit():
    params = load("drogon_params_v2")
    assert params.FIELD_NAME == "LNPERM"
    assert len(params.FIELD_NAME) <= 8
    assert "FIELD LNPERM PARAMETER" in (EXAMPLE / "ert/after.ert").read_text()


def test_cells_above_transition_keep_geomodel_values():
    data = static()
    data["depth"][0] = 0
    data["swatinit"][0] = 0.17
    sw, _ = lib.remap_swatinit(data, np.array([2.0]))
    assert sw[0] == 0.17


def test_measured_coverage_and_collapse():
    compare = load("compare", "tools")
    prior = np.array([[-2.0, -2.0], [0.0, 0.0], [2.0, 2.0]])
    posterior = np.array([[-0.1, -1.0], [0.0, 0.0], [0.1, 1.0]])
    result = compare.spread_and_coverage(prior, posterior, np.array([0.0, 1.5]))
    assert result["collapse_count"] == 1
    assert result["collapse_share"] == 0.5
    assert result["prior_truth_coverage"] == 1
    assert result["posterior_truth_coverage"] == 0.5


def test_prepare_refuses_existing_original_case(tmp_path, monkeypatch):
    prepare = load("prepare", "tools")
    source = tmp_path / "original"
    source.mkdir()
    sentinel = source / "sentinel"
    sentinel.write_text("original")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare",
            "--source",
            str(source),
            "--case",
            str(source),
            "--truth",
            "unused",
            "--scratch",
            str(tmp_path / "scratch"),
        ],
    )
    with pytest.raises(SystemExit) as error:
        prepare.main()
    assert error.value.code == 2
    assert sentinel.read_text() == "original"
    assert list(source.iterdir()) == [sentinel]


def test_deck_overlay_replaces_exactly_the_two_properties():
    prepare = load("prepare", "tools")
    original = "INCLUDE\n '../include/grid/drogon.perm' /\nINCLUDE\n '../include/props/drogon.swatinit' /\n"
    assert (
        prepare.patch_deck(original)
        == "INCLUDE\n '../include/ert/perm.inc' /\nINCLUDE\n '../include/ert/swatinit.inc' /\n"
    )
    with pytest.raises(ValueError, match="exactly one"):
        prepare.patch_deck(original * 2)


def test_missing_breakthrough_is_counted_not_scored():
    compare = load("compare", "tools")
    values = np.array([[1.0, np.nan], [2.0, 2.0]])
    result = compare.score_family(values, np.array([0.0, 0.0]), np.array([1.0, 1.0]))
    assert result["n_missing"] == 1
    assert result["median"] == pytest.approx(3.25)
    with pytest.raises(ValueError):
        compare.score_family(np.array([[np.nan]]), np.array([0.0]), np.array([1.0]))


def test_breakthrough_obs_parse_and_first_crossing():
    from datetime import date

    bt = load("breakthrough_contacts", "tools")
    text = (
        "BREAKTHROUGH_OBSERVATION BT_A1 { KEY = WWCT:A1; DATE = 2019-03-22; ERROR = 30;"
        " THRESHOLD = 0.1; LOCALIZATION { EAST = 1.0; NORTH = 2.0; RADIUS = 1000; }; };\n"
    )
    assert bt.parse_breakthrough_obs(text) == {"A1": (date(2019, 3, 22), 0.1)}
    with pytest.raises(ValueError):
        bt.parse_breakthrough_obs("SUMMARY_OBSERVATION X { KEY = WOPR:A1; };")
    days = [date(2019, 1, 1), date(2019, 2, 1), date(2019, 3, 1)]
    assert bt.first_crossing(days, [0.0, 0.1, 0.5], 0.1) == date(2019, 2, 1)
    assert bt.first_crossing(days, [0.0, 0.05, 0.09], 0.1) is None
    assert bt.summarize([10, -40], 1)["within_30_days"] == 1
