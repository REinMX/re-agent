"""Realization specifications use the shared strict parser."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/stage-contract/scripts"))
import pytest
import runpath_lib


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("0-4", {0, 1, 2, 3, 4}), ("0-4,7,9-10", {0, 1, 2, 3, 4, 7, 9, 10}), ("5", {5}), (" 1 , 3 ", {1, 3})],
)
def test_valid_specs(spec, expected):
    assert runpath_lib.parse_realizations(spec) == expected


@pytest.mark.parametrize("spec", ["", "1,,2", "a", "1-", "-3", "4-2", "1-2-3"])
def test_bad_specs(spec):
    with pytest.raises(ValueError):
        runpath_lib.parse_realizations(spec)
