import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/stage-contract/scripts"))
import runpath_lib as lib


def deck(root, real, folder="model", name=None):
    runpath = root / f"realization-{real}" / "iter-0"
    file = runpath / folder / f"{name or f'DROGON-{real}'}.DATA"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.touch()
    return runpath, file


def test_g12_and_pair(tmp_path):
    path, file = deck(tmp_path, 12)
    for base in ("model/DROGON-<IENS>", ("model", "DROGON-<IENS>")):
        result = lib.read_ensemble(str(tmp_path / "realization-*" / "iter-0"), base, {12})
        assert result.found == (lib.Member(12, path, file),)
        assert result.complete
        assert result.problems() == []
        assert result.require_complete() is None


def test_search_priority(tmp_path):
    path, file = deck(tmp_path, 0, "eclipse/model")
    deck(tmp_path, 0)
    assert lib.data_file(path, None, 0) == (file, "")
    assert lib.data_file(path, "DROGON-<IENS>", 0) == (file, "")
    assert lib.data_file(path, ("eclipse/model", "DROGON-<IENS>"), 0) == (file, "")


def test_drops_and_missing(tmp_path):
    deck(tmp_path, 0)
    path, _ = deck(tmp_path, 1)
    (path / "model/OTHER.DATA").touch()
    missing_data = tmp_path / "realization-2/iter-0"
    missing_data.mkdir(parents=True)
    unknown = tmp_path / "unknown/iter-0"
    unknown.mkdir(parents=True)
    deck(tmp_path, 7)
    result = lib.read_ensemble(str(tmp_path / "*/iter-0"), expected={0, 1, 2, 3})
    assert [member.realization for member in result.found] == [0]
    assert result.missing == (3,)
    assert len(result.dropped) == 3
    assert result.extra == (lib.Dropped(7, tmp_path / "realization-7/iter-0", "not in the expected set"),)
    reasons = "\n".join(result.problems())
    for text in ("no unique .DATA", "no *.DATA", "no realization number"):
        assert text in reasons
    with pytest.raises(lib.IncompleteEnsemble) as error:
        result.require_complete()
    assert str(error.value) == reasons
    assert "Missing realization folders: [3]" in reasons


def test_no_data_needed_and_order(tmp_path):
    for real in (12, 2):
        (tmp_path / f"realization-{real}/iter-0").mkdir(parents=True)
    (tmp_path / "other").mkdir()
    (tmp_path / "realization-1").touch()
    pattern = str(tmp_path / "*/iter-0")
    assert [real for real, _ in lib.find_runpaths(pattern)] == [2, 12]
    result = lib.read_ensemble(pattern, need_data_file=False)
    assert result.complete
    assert [member.realization for member in result.found] == [2, 12]
    assert all(member.data_file is None for member in result.found)


def test_last_component():
    assert lib.realization_number(Path("realization-1/x/realization-20/iter-0")) == 20
    assert lib.realization_number(Path("xrealization-2/iter-0")) is None


@pytest.mark.parametrize("spec", ["", "1,", "a", "3-1", "-1", "1-2-3"])
def test_bad_spec(spec):
    with pytest.raises(ValueError):
        lib.parse_realizations(spec)


def test_expected():
    assert lib.parse_realizations("0-2, 7") == {0, 1, 2, 7}
    assert lib.expected_set(3, None) == {0, 1, 2}
    assert lib.expected_set(None, "2,7") == {2, 7}
    assert lib.expected_set(None, None) is None
    with pytest.raises(ValueError, match="positive"):
        lib.expected_set(0, None)


def test_outside_expected_is_extra_and_complete(tmp_path):
    path, file = deck(tmp_path, 0)
    extra_path, _ = deck(tmp_path, 7)
    result = lib.read_ensemble(str(tmp_path / "realization-*/iter-0"), expected={0})
    assert result.found == (lib.Member(0, path, file),)
    assert result.extra == (lib.Dropped(7, extra_path, "not in the expected set"),)
    assert result.dropped == ()
    assert result.missing == ()
    assert result.complete
    assert result.problems() == []
    assert result.require_complete() is None
