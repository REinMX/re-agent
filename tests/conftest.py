"""With REQUIRE_FULL=1, a skipped test is a failure. `make check-full` sets it."""

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def valid_reek_ensemble(tmp_path_factory):
    """Native positive fixture: remove only upstream's deliberate FOO NaN sentinel.

    Copy first. Never edit upstream or generate substitute simulator results.
    Summary/deck bytes and all other parameter lines are unchanged.
    The unmodified upstream sentinel has its own rejection regression.
    """
    source = Path(
        os.environ.get("REEK_ENSEMBLE", Path.home() / "fmu-ensemble-upstream/tests/data/testensemble-reek001")
    )
    if not source.is_dir():
        pytest.skip("Reek test ensemble not found. Set REEK_ENSEMBLE.")
    target = tmp_path_factory.mktemp("valid-reek") / "ensemble"
    shutil.copytree(source, target)
    params = target / "realization-0/iter-0/parameters.txt"
    lines = params.read_bytes().splitlines(keepends=True)
    params.write_bytes(b"".join(line for line in lines if line.split() != [b"FOO", b"NaN"]))
    return target


OPT_IN = {"resinsight": "RUN_RESINSIGHT", "drogonlite": "RUN_DROGONLITE"}


def pytest_collection_modifyitems(config, items):
    """Real-ResInsight tests run only with RUN_RESINSIGHT=1 (make check-resinsight), and the full drogon-lite
    ES-MDA only with RUN_DROGONLITE=1 (make check-drogonlite).

    They are deselected, not skipped, so check-full does not fail on a host without ResInsight or drogon-lite.
    """
    off = {marker for marker, env in OPT_IN.items() if os.environ.get(env) != "1"}
    drop = [item for item in items if any(item.get_closest_marker(marker) for marker in off)]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = [item for item in items if item not in drop]


_skipped: list[str] = []


def _record(report) -> None:
    if report.skipped:
        _skipped.append(report.nodeid)


def pytest_collectreport(report):
    _record(report)


def pytest_runtest_logreport(report):
    _record(report)


def pytest_sessionfinish(session):
    if os.environ.get("REQUIRE_FULL") == "1" and _skipped:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    if os.environ.get("REQUIRE_FULL") == "1" and _skipped:
        terminalreporter.write_line(
            f"FAIL: REQUIRE_FULL=1 and {len(_skipped)} tests or modules skipped. "
            "Use the Komodo Python and set REEK_ENSEMBLE.",
            red=True,
        )
