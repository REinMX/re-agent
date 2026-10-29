"""Optional keys and blank units in ensemble_summary.py. Synthetic members; no simulator files are read."""

import fnmatch
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("res2df")
SCRIPT = Path(__file__).parents[1] / "skills/res2df-resdata-extract/scripts/ensemble_summary.py"


@pytest.fixture
def case(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ensemble_summary_keys", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    members = {}

    def member(real, units):
        """One member. `units` maps each vector that the member has to its native unit."""
        model = tmp_path / f"realization-{real}/iter-0/eclipse/model"
        model.mkdir(parents=True)
        (model / "CASE.DATA").write_text("synthetic")
        (model / "CASE.UNSMRY").write_text("synthetic")
        members[real] = units

    class Files:
        def __init__(self, file):
            self.units = members[int(Path(file).parents[3].name.split("-")[1])]

        def get_summary(self):
            stamp = pd.Timestamp("2001-01-01")
            return SimpleNamespace(start_time=stamp, end_time=stamp, unit=self.units.__getitem__)

    def df(source, time_index, column_keys):
        # like res2df: a key that matches no vector gives no column and no error
        columns = [v for v in source.units if any(fnmatch.fnmatchcase(v, k) for k in column_keys)]
        return pd.DataFrame({v: [1.0] for v in columns}, index=pd.to_datetime(["2001-01-01"]))

    monkeypatch.setattr(module, "SimFiles", Files)
    monkeypatch.setattr(module.summary_module, "df", df)
    out = tmp_path / "out"

    def run(*args):
        glob = str(tmp_path / "realization-*/iter-0")
        return module.main([glob, *args, "--time-index", "last", "--outdir", str(out)])

    def manifest():
        return json.loads((out / "manifest.json").read_text())

    return SimpleNamespace(member=member, run=run, out=out, manifest=manifest)


OIL_WATER = {"FOPT": "SM3", "FWPT": "SM3", "WWCT:PROD": ""}


def test_optional_key_that_no_member_has_is_dropped_with_a_warning(case, capsys):
    for real in (0, 1):
        case.member(real, OIL_WATER)
    assert case.run("--optional-keys", "FOPT", "FWPT", "FGPT", "--expected", "2") == 0
    manifest = case.manifest()
    assert manifest["vectors_returned"] == ["FOPT", "FWPT"]
    assert manifest["optional_keys_dropped"] == ["FGPT"]
    assert manifest["issues"] == []
    assert "FGPT" in capsys.readouterr().err


def test_explicit_key_that_no_member_has_fails_closed(case, capsys):
    for real in (0, 1):
        case.member(real, OIL_WATER)
    assert case.run("--keys", "FOPT", "FGPT") == 1
    assert not case.out.exists()
    assert "no vector matches requested key FGPT" in capsys.readouterr().err


def test_key_in_both_lists_stays_strict(case):
    case.member(0, OIL_WATER)
    assert case.run("--keys", "FOPT", "FGPT", "--optional-keys", "FGPT") == 1
    assert not case.out.exists()


def test_only_absent_optional_keys_fails_closed(case):
    case.member(0, OIL_WATER)
    assert case.run("--optional-keys", "FGPT") == 1
    assert not case.out.exists()


def test_optional_key_in_some_members_only_is_an_error(case, capsys):
    case.member(0, OIL_WATER)
    case.member(1, {"FOPT": "SM3"})
    assert case.run("--optional-keys", "FOPT", "FWPT") == 1
    assert not case.out.exists()
    assert "member vector sets differ" in capsys.readouterr().err


def test_no_key_is_usage_error(case):
    case.member(0, OIL_WATER)
    with pytest.raises(SystemExit) as stop:
        case.run()
    assert stop.value.code == 2


@pytest.mark.parametrize("unit", ["", "  "])
def test_blank_unit_is_valid_for_water_cut(case, unit):
    # OPM Flow writes a blank unit for water cut: it is a fraction
    for real in (0, 1):
        case.member(real, {"WWCT:PROD": unit, "FOPT": "SM3"})
    assert case.run("--keys", "WWCT:PROD", "FOPT") == 0
    assert case.manifest()["units"] == {"WWCT:PROD": "", "FOPT": "SM3"}


@pytest.mark.parametrize("unit", ["", "  ", None])
def test_blank_unit_is_rejected_for_other_vectors(case, capsys, unit):
    case.member(0, {"FOPT": unit})
    assert case.run("--keys", "FOPT") == 1
    assert not case.out.exists()
    assert "units are unavailable" in capsys.readouterr().err


def test_water_cut_unit_must_still_be_readable(case):
    case.member(0, {"WWCT:PROD": None})
    assert case.run("--keys", "WWCT:PROD") == 1
    assert not case.out.exists()
