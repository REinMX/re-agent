"""Two scripts carry copies of the blank-unit rule. The copies must agree."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
COPIES = [
    "skills/res2df-resdata-extract/scripts/ensemble_summary.py",
    "skills/history-match-qc/scripts/misfit_report.py",
]


def rule_source(path: str) -> str:
    tree = ast.parse((ROOT / path).read_text())
    pattern = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DIMENSIONLESS_MNEMONIC"
    )
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "unit_is_valid")
    return ast.unparse(pattern) + "\n" + ast.unparse(function)


def test_copies_are_identical():
    assert len({rule_source(path) for path in COPIES}) == 1


def load():
    namespace: dict = {"re": re}
    exec(rule_source(COPIES[0]), namespace)
    return namespace["unit_is_valid"]


@pytest.mark.parametrize(
    "key", ["FWCT", "FWCTH", "GWCT:OP", "WWCT:PROD", "WWCTH:PROD", "RWCT:1", "CWCT:PROD:1,1,1"]
)
@pytest.mark.parametrize("unit", ["", "  "])
def test_blank_unit_is_valid_for_water_cut(key, unit):
    assert load()(key, unit)


# A blank unit for a volume, a rate, a pressure, or a ratio of volumes means the unit is unavailable.
@pytest.mark.parametrize(
    "key", ["FOPT", "FGPT", "FWPR", "WBHP:PROD", "FGOR", "WGLR:PROD", "WWCTX:PROD", "XWCT"]
)
@pytest.mark.parametrize("unit", ["", "  "])
def test_blank_unit_is_rejected_for_other_vectors(key, unit):
    assert not load()(key, unit)


@pytest.mark.parametrize("key", ["FOPT", "WWCT:PROD"])
def test_unit_must_be_a_string(key):
    assert not load()(key, None)
    assert load()(key, "SM3")
