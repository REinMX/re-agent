"""Complete summary inventory from public synthetic observation files only."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/history-match-qc/scripts/obs_qc.py"
spec = importlib.util.spec_from_file_location("inventory_obs_qc", SCRIPT)
obs_qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs_qc)

BODY = "VALUE = 100; ERROR = 5; DATE = 2020-01-01; KEY = WOPR:A;"


def block(body=BODY, name="A"):
    return f"SUMMARY_OBSERVATION {name} {{ {body} }};\n"


def observation_file(tmp_path, text):
    path = tmp_path / "inventory.obs"
    path.write_text(text, encoding="utf-8")
    return path


def test_inventory_canonical_native_agreement_fixture(tmp_path):
    text = (
        "-- Canonical declarations also used by test_obs_qc_agrees_with_ert.\n"
        + block(name="A_abs")
        + block(
            "VALUE = -200; ERROR = 0.1; ERROR_MODE = REL; DATE = 2020-02-01; KEY = WOPR:A;",
            "A_rel",
        )
        + block(
            "VALUE = 10; ERROR = 0.01; ERROR_MODE = RELMIN; DATE = 2020-03-01; KEY = WOPR:A;",
            "A_rm",
        )
        + block(
            "VALUE = 1000; ERROR = 0.05; ERROR_MODE = RELMIN; ERROR_MIN = 2; "
            "DATE = 2020-04-01; KEY = WOPR:A;",
            "A_rm2",
        )
    )
    path = observation_file(tmp_path, text)
    expected = {
        name: {"OBS": name, "KEY": "WOPR:A", "DATE": date, "OBSERVED": value, "ERROR": error}
        for name, date, value, error in [
            ("A_abs", "2020-01-01", 100.0, 5.0),
            ("A_rel", "2020-02-01", -200.0, 20.0),
            ("A_rm", "2020-03-01", 10.0, 0.1),
            ("A_rm2", "2020-04-01", 1000.0, 50.0),
        ]
    }
    assert obs_qc.summary_inventory(path) == expected
    for row in obs_qc.summary_inventory(path).values():
        assert isinstance(row["OBSERVED"], float)
        assert isinstance(row["ERROR"], float)
    assert path.read_text() == text


@pytest.mark.parametrize(
    ("date", "expected"),
    [
        ("15/03/2021", "2021-03-15"),
        ('"2021-03-15"', "2021-03-15"),
        ("2021-03-15T00:00:00", "2021-03-15"),
        ("2021-03-15T10:30:00", "2021-03-15T10:30:00"),
        ("2021-03-15T00:00:00.000001", "2021-03-15T00:00:00.000001"),
        ("2021-03-15T10:30:00+02:00", "2021-03-15T10:30:00+02:00"),
    ],
)
def test_inventory_canonical_dates_keep_subdaily_identity(tmp_path, date, expected):
    path = observation_file(tmp_path, block(BODY.replace("2020-01-01", date)))
    assert obs_qc.summary_inventory(path)["A"]["DATE"] == expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ("VALUE = 100; ERROR = -5;", 5.0),
        ("VALUE = 0; ERROR = 0.01; ERROR_MODE = RELMIN;", 0.1),
        ("VALUE = 1; ERROR = 0.01; ERROR_MODE = RELMIN; ERROR_MIN = 2;", 2.0),
    ],
)
def test_inventory_reuses_effective_error_modes(tmp_path, fields, expected):
    path = observation_file(tmp_path, block(fields + " DATE = 2020-01-01; KEY = WOPR:A;"))
    assert obs_qc.summary_inventory(path)["A"]["ERROR"] == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("INCLUDE extra.obs;\n" + block(), "unmatched"),
        (block() + "INCLUDE extra.obs;", "unmatched"),
        (block() + "garbage", "unmatched"),
        (block() + "SUMMARY_OBSERVATION B { VALUE = 1;", "unmatched"),
        (
            block() + "GENERAL_OBSERVATION RFT { DATA = RFT; RESTART = 1; VALUE = 1; ERROR = 1; };",
            "GENERAL_OBSERVATION",
        ),
        (block() + "HISTORY_OBSERVATION FOPR { ERROR = 1; };", "HISTORY_OBSERVATION"),
        (block() + "RFT_OBSERVATION R { VALUE = 1; };", "RFT_OBSERVATION"),
        (block(BODY + " SEGMENT S { VALUE = 1; };"), "nested"),
        (block(BODY + " nonsense;"), "unmatched"),
        (block(BODY + " UNKNOWN = 2;"), "UNKNOWN"),
        (block(BODY + " DAYS = 1;"), "DAYS"),
        (block(BODY + " ERROR = 2;"), "duplicate field ERROR"),
        (block(BODY + " KEY = OTHER;"), "duplicate field KEY"),
        (block() + block(), "duplicate observation name A"),
        (block(BODY.replace("KEY = WOPR:A;", "KEY = WOPR:A VALUE = 2;")), "unsupported value"),
        (block(BODY.replace("KEY = WOPR:A;", 'KEY = "WOPR:A;')), "unsupported value"),
    ],
)
def test_strict_parser_rejects_partial_inventory(tmp_path, text, message):
    path = observation_file(tmp_path, text)
    with pytest.raises(ValueError, match=message) as caught:
        obs_qc.parse_file(path, strict=True)
    assert str(path) in str(caught.value)
    with pytest.raises(ValueError, match=message):
        obs_qc.summary_inventory(path)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (BODY.replace("VALUE = 100;", ""), "missing VALUE"),
        (BODY.replace("ERROR = 5;", ""), "missing ERROR"),
        (BODY.replace("DATE = 2020-01-01;", ""), "missing DATE"),
        (BODY.replace("KEY = WOPR:A;", ""), "missing KEY"),
        (BODY.replace("2020-01-01", "2020-13-45"), "DATE cannot be read"),
        (BODY.replace("ERROR = 5;", "ERROR = 0;"), "larger than 0"),
        (BODY + " ERROR_MODE = UNKNOWN;", "unknown ERROR_MODE"),
        (BODY.replace("VALUE = 100;", "VALUE = nan;"), "VALUE is not a number"),
        (BODY.replace("VALUE = 100;", "VALUE = inf;"), "VALUE is not a number"),
        (BODY.replace("ERROR = 5;", "ERROR = inf;"), "ERROR is not a number"),
        (BODY + " ERROR_MIN = nan;", "ERROR_MIN is not a number"),
        (
            BODY.replace("VALUE = 100;", "VALUE = 1e308;").replace("ERROR = 5;", "ERROR = 1e308;")
            + " ERROR_MODE = REL;",
            "larger than 0",
        ),
        (BODY.replace("VALUE = 100;", "VALUE = 0;") + " ERROR_MODE = REL;", "larger than 0"),
        (BODY.replace("KEY = WOPR:A;", 'KEY = "";'), "empty KEY"),
    ],
)
def test_inventory_rejects_normalization_errors(tmp_path, body, message):
    path = observation_file(tmp_path, block(body))
    with pytest.raises(ValueError, match=message) as caught:
        obs_qc.summary_inventory(path)
    assert str(path) in str(caught.value)
    assert "A" in str(caught.value)


@pytest.mark.parametrize("text", ["", "  \n", "-- no observations\n"])
def test_inventory_rejects_empty_files(tmp_path, text):
    path = observation_file(tmp_path, text)
    with pytest.raises(ValueError, match="no SUMMARY_OBSERVATION") as caught:
        obs_qc.summary_inventory(path)
    assert str(path) in str(caught.value)


def test_inventory_preserves_names_and_keys(tmp_path):
    path = observation_file(tmp_path, block(BODY.replace("WOPR:A", "WOPR:WELL_A"), "Mixed-Case_A"))
    row = obs_qc.summary_inventory(path)["Mixed-Case_A"]
    assert row["OBS"] == "Mixed-Case_A"
    assert row["KEY"] == "WOPR:WELL_A"


def test_default_parser_keeps_permissive_diagnostics(tmp_path):
    path = observation_file(
        tmp_path,
        "INCLUDE other.obs;\n"
        + block(BODY + " ERROR = 2; UNKNOWN = ignored;")
        + "HISTORY_OBSERVATION FOPR { ERROR = 1; SEGMENT S { ERROR = 2; }; };",
    )
    records = obs_qc.parse_file(path)
    assert len(records) == 2
    assert records[0]["fields"]["ERROR"] == "2"
    assert records[0]["fields"]["UNKNOWN"] == "ignored"
    assert records[1]["kind"] == "HISTORY_OBSERVATION"
    assert records[1]["has_segments"] is True
    assert obs_qc.normalize_summary(records[0])["issues"] == []


def test_inventory_has_no_optional_runtime_imports(tmp_path, monkeypatch):
    import sys

    for name in ("ert", "resdata", "res2df", "pandas", "numpy"):
        monkeypatch.setitem(sys.modules, name, None)
    path = observation_file(tmp_path, block())
    assert obs_qc.summary_inventory(path)["A"]["OBSERVED"] == 100.0
