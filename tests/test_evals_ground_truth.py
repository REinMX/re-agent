"""The eval fixture and the triage script must agree. This keeps the eval ground truth honest."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load("evals/make_failed_ensemble.py", "make_failed_ensemble")
triage = load("skills/ert-diagnose/scripts/triage_runpath.py", "triage_runpath")


def test_triage_matches_ground_truth(tmp_path, capsys):
    case = tmp_path / "case"
    for iens in fixture.GROUND_TRUTH:
        fixture.write_realization(case, iens)
    code = triage.main([str(case), "--iter", "0", "--expected", "12", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    for row in payload["realizations"]:
        state, step, failure = fixture.GROUND_TRUTH[row["realization"]]
        assert (row["state"], row["failed_step"], row["failure_class"]) == (state, step, failure)
    assert payload["summary"]["missing_realizations"] == []
    assert payload["summary"]["ok_with_simulator_problems"] == [10]
