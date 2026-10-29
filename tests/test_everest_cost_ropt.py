"""Everest cost estimate against the installed ropt scipy backend."""

import importlib.metadata
import importlib.util
import sys
from pathlib import Path

import pytest

ropt = pytest.importorskip("ropt.backend.scipy")

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills/everest-config-author-lint/scripts"


def load(name):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    return {
        "controls": [
            {
                "name": "rate",
                "min": 0,
                "max": 100,
                "initial_guess": 50,
                "perturbation_magnitude": 5,
                "variables": [{"name": "a"}],
            }
        ],
        "objective_functions": [{"name": "npv"}],
        "model": {"realizations": [0, 1, 2, 3, 4]},
        "optimization": {"algorithm": "optpp_q_newton", "max_batch_num": 8, "perturbation_num": 2},
    }


@pytest.mark.parametrize("method", sorted(ropt._NO_GRADIENT))
def test_ropt_derivative_free_method_ignores_perturbations(method):
    cfg = config()
    cfg["optimization"] = {"algorithm": f"scipy/{method}", "max_batch_num": 8, "perturbation_num": 2}
    assert load("everest_cost").estimate(cfg, sim_hours=1)["simulations"] == 8 * 5


def test_fallback_matches_installed_ropt(monkeypatch):
    monkeypatch.setitem(sys.modules, "ropt.backend.scipy", None)
    fallback = load("everest_cost").DERIVATIVE_FREE
    expected = {f"scipy/{m}" for m in ropt._NO_GRADIENT}
    assert fallback == expected, f"fallback differs from ropt {importlib.metadata.version('ropt')}"
