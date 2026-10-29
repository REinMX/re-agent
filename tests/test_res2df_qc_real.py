"""Real decks through the res2df (OPM parser) paths of schedule_qc, obs_qc --deck and deck_qc.

Each test skips unless an environment variable points at public data:
    DROGON_DECK      a Drogon realization DATA file (OPM Drogon, public)
    DROGON_OBS       a glob of ERT observation files for that deck
    DROGON_G9_OBS    the observation file of the G9 lesson (20 observations while the well does not flow)
    EGG_DECK         the Egg model EGG_HIST.DATA with its include files
"""

import contextlib
import glob
import importlib.util
import io
import json
import os
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("res2df")


def load(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_json(main, *args: str) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main([*args, "--json"])
    text = buffer.getvalue()
    return code, json.loads(text[text.index("{") :])


def env_file(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value and Path(value).is_file() else None


DROGON_DECK = env_file("DROGON_DECK")
G9_OBS = env_file("DROGON_G9_OBS")
EGG_DECK = env_file("EGG_DECK")
needs_drogon = pytest.mark.skipif(
    DROGON_DECK is None, reason="Set DROGON_DECK to a Drogon realization DATA file"
)


@needs_drogon
def test_drogon_schedule_is_read_through_the_include():
    from res2df import ResdataFiles, compdat, wcon

    schedule_qc = load("skills/deck-scal-pvt-qc/scripts/schedule_qc.py", "schedule_qc_real")
    code, report = run_json(schedule_qc.main, "--deck", str(DROGON_DECK))
    assert (code, report["parser"]) == (0, "opm")
    assert "W006" not in {row["code"] for row in report["findings"]}  # G1: the INCLUDEd SCHEDULE is read
    assert "W005" not in {row["code"] for row in report["findings"]}  # G2: DIMENS comes from the deck
    source = ResdataFiles(str(DROGON_DECK))
    connections = compdat.df(source)
    assert connections["WELL"].nunique() == 11 and len(connections) == 232
    assert Counter(wcon.df(source)["KEYWORD"]) == {"WCONHIST": 278, "WCONINJH": 14}


@needs_drogon
def test_drogon_observations_against_the_schedule():
    obs_qc = load("skills/history-match-qc/scripts/obs_qc.py", "obs_qc_real")
    files = sorted(glob.glob(os.environ.get("DROGON_OBS", "")))
    if not files:
        pytest.skip("Set DROGON_OBS to a glob of observation files")
    code, report = run_json(obs_qc.main, *files, "--deck", str(DROGON_DECK))
    assert code in (0, 1)
    print("HMQC-OBS-NOFLOW:", sum(p.get("code") == "HMQC-OBS-NOFLOW" for p in report["problems"]))


@needs_drogon
@pytest.mark.skipif(G9_OBS is None, reason="Set DROGON_G9_OBS to the G9 lesson observation file")
def test_drogon_g9_observations_are_flagged():
    obs_qc = load("skills/history-match-qc/scripts/obs_qc.py", "obs_qc_g9")
    code, report = run_json(obs_qc.main, str(G9_OBS), "--deck", str(DROGON_DECK))
    flagged = [p["message"] for p in report["problems"] if p.get("code") == "HMQC-OBS-NOFLOW"]
    wells = Counter(message.split(" well ", 1)[1].split()[0] for message in flagged)
    assert wells == {"A3": 8, "A4": 8, "A5": 1, "A6": 3}


@pytest.mark.skipif(EGG_DECK is None, reason="Set EGG_DECK to EGG_HIST.DATA with its include files")
def test_egg_rock_compressibility_zero_is_not_an_error():
    deck_qc = load("skills/deck-scal-pvt-qc/scripts/deck_qc.py", "deck_qc_egg")
    code, report = run_json(deck_qc.main, "--deck", str(EGG_DECK))
    found = {(row["code"], row["severity"]) for row in report["findings"]}
    assert ("P005", "I") in found and not any(code == "P001" for code, _ in found)
    assert code == 0, report["findings"]
