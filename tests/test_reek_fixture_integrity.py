"""Keep native Reek evidence intact while separating its known NaN sentinel from positives."""

import hashlib
import importlib.util
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("res2df")
SOURCE = Path(
    os.environ.get("REEK_ENSEMBLE", Path.home() / "fmu-ensemble-upstream/tests/data/testensemble-reek001")
)


def test_positive_copy_changes_only_known_parameter_sentinel(valid_reek_ensemble):
    original_files = {p.relative_to(SOURCE) for p in SOURCE.rglob("*") if p.is_file()}
    copied_files = {p.relative_to(valid_reek_ensemble) for p in valid_reek_ensemble.rglob("*") if p.is_file()}
    assert copied_files == original_files
    sentinel = Path("realization-0/iter-0/parameters.txt")
    before = (SOURCE / sentinel).read_bytes()
    for relative in original_files:
        original = (SOURCE / relative).read_bytes()
        expected = original
        if relative == sentinel:
            expected = b"".join(
                line for line in original.splitlines(keepends=True) if line.split() != [b"FOO", b"NaN"]
            )
        assert (
            hashlib.sha256((valid_reek_ensemble / relative).read_bytes()).digest()
            == hashlib.sha256(expected).digest()
        )
    # Never rewrite upstream, including the known invalid parameter record.
    assert (SOURCE / sentinel).read_bytes() == before


@pytest.mark.parametrize("partial", [False, True])
def test_native_reek_nan_parameter_fails_even_with_partial(tmp_path, valid_reek_ensemble, capsys, partial):
    script = Path(__file__).parents[1] / "skills/history-match-qc/scripts/misfit_report.py"
    spec = importlib.util.spec_from_file_location("native_reek_nan", script)
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    runpath = tmp_path / "raw/realization-0/iter-0"
    shutil.copytree(valid_reek_ensemble / "realization-0/iter-0", runpath)
    params = runpath / "parameters.txt"
    # Put the upstream-style negative sentinel in a disposable copy, not upstream.
    params.write_bytes(params.read_bytes().rstrip(b"\n") + b"\nHANDOFF_BAD NaN\n")
    obs = tmp_path / "native.obs"
    obs.write_text("SUMMARY_OBSERVATION O { VALUE = 1; ERROR = 1000; DATE = 2001-01-01; KEY = FOPT; };\n")
    out = tmp_path / "out"
    code = report.main(
        [
            "--obs",
            str(obs),
            "--posterior",
            str(tmp_path / "raw/realization-*/iter-0"),
            "--expected",
            "1",
            "--outdir",
            str(out),
            *(["--allow-partial"] if partial else []),
        ]
    )
    assert code == 1
    assert not out.exists()
    assert "parameters" in capsys.readouterr().err
