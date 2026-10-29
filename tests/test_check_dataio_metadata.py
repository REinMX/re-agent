import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SCRIPT = Path(__file__).parents[1] / "skills/fmu-dataio-sumo/scripts/check_dataio_metadata.py"
spec = importlib.util.spec_from_file_location("check_dataio_metadata", SCRIPT)
chk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chk)

UUID = "11111111-2222-3333-4444-555555555555"


def meta(name="top", content="depth", uuid=UUID, md5=None, **extra):
    base = {
        "$schema": "https://example.invalid/schemas/0.9.0/fmu_results.json",
        "class": "surface",
        "access": {"asset": {"name": "Drogon"}},
        "masterdata": {"smda": {}},
        "source": "fmu",
        "version": "0.9.0",
        "data": {"name": name, "content": content},
        "file": {"relative_path": f"share/results/maps/{name}.gri", "checksum_md5": md5},
        "fmu": {"case": {"name": "MyCase", "uuid": uuid}},
    }
    base.update(extra)
    return base


def write_pair(folder: Path, name: str, metadata: dict, payload: bytes = b"data") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.gri").write_bytes(payload)
    if metadata["file"]["checksum_md5"] is None:
        metadata["file"]["checksum_md5"] = hashlib.md5(payload).hexdigest()
    sidecar = folder / f".{name}.gri.yml"
    sidecar.write_text(yaml.safe_dump(metadata))
    return sidecar


def run(folder: Path, *args: str) -> tuple[int, dict]:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = chk.main([str(folder), "--json", *args])
    return code, json.loads(buffer.getvalue())


def messages(report: dict) -> str:
    return "\n".join(p["message"] for p in report["problems"])


def test_valid_pair_passes(tmp_path):
    write_pair(tmp_path, "top", meta())
    code, report = run(tmp_path)
    assert code == 0 and report["problems"] == []


def test_checksum_mismatch_is_error(tmp_path):
    write_pair(tmp_path, "top", meta(md5="0" * 32))
    code, report = run(tmp_path)
    assert code == 1 and "MD5" in messages(report)


def test_missing_data_file_is_error(tmp_path):
    sidecar = write_pair(tmp_path, "top", meta())
    (tmp_path / "top.gri").unlink()
    assert sidecar.exists()
    code, report = run(tmp_path)
    assert code == 1 and "data file not found" in messages(report)


def test_no_files_flag_skips_file_checks(tmp_path):
    sidecar = write_pair(tmp_path, "top", meta())
    (tmp_path / "top.gri").unlink()
    assert sidecar.exists()
    assert run(tmp_path, "--no-files")[0] == 0


def test_empty_name_and_content_are_errors(tmp_path):
    write_pair(tmp_path, "top", meta(name="", content=""))
    # name is empty, so the data file is written as '.gri'. Skip file checks here.
    code, report = run(tmp_path, "--no-files")
    assert code == 1
    assert "data.name" in messages(report) and "data.content" in messages(report)


def test_two_case_uuids_is_error(tmp_path):
    write_pair(tmp_path, "a", meta(name="a"))
    write_pair(tmp_path, "b", meta(name="b", uuid="99999999-2222-3333-4444-555555555555"))
    code, report = run(tmp_path)
    assert code == 1 and "more than one case uuid" in messages(report)


def test_duplicate_identity_is_warning(tmp_path):
    write_pair(tmp_path / "x", "top", meta())
    write_pair(tmp_path / "y", "top", meta())
    code, report = run(tmp_path)
    assert code == 0
    assert "share one data identity" in messages(report)


def test_extensionless_data_file_sidecar_is_found(tmp_path):
    # Found 2026-10-08 by adversarial review: find_sidecars() had an extra "dot count >= 3"
    # filter beyond the glob's own leading-dot/trailing-.yml shape, which silently dropped
    # sidecars for extensionless data files (2 dots total, e.g. ".EIGHTCELLS.yml" for a data
    # file named "EIGHTCELLS") instead of checking them -- a silent skip the docstring
    # explicitly disclaims. A dropped sidecar must surface as a missing-data-file problem,
    # not be invisible to the checker entirely.
    tmp_path.mkdir(exist_ok=True)
    metadata = meta(name="EIGHTCELLS")
    metadata["file"]["relative_path"] = "share/results/grids/EIGHTCELLS"
    sidecar = tmp_path / ".EIGHTCELLS.yml"
    sidecar.write_text(yaml.safe_dump(metadata))
    # No "EIGHTCELLS" data file written: if the sidecar were silently dropped (the bug),
    # this would report zero problems instead of a missing-data-file error.
    code, report = run(tmp_path)
    assert code == 1
    assert "data file not found" in messages(report)


def test_missing_fmu_block_warns_then_errors_with_flag(tmp_path):
    metadata = meta()
    del metadata["fmu"]
    write_pair(tmp_path, "top", metadata)
    assert run(tmp_path)[0] == 0
    assert run(tmp_path, "--require-fmu")[0] == 1


def test_casepath_uuid_must_match(tmp_path):
    write_pair(tmp_path / "case/realization-0/iter-0/share/results/maps", "top", meta())
    case_meta = tmp_path / "case/share/metadata"
    case_meta.mkdir(parents=True)
    (case_meta / "fmu_case.yml").write_text(
        yaml.safe_dump({"fmu": {"case": {"uuid": "99999999-2222-3333-4444-555555555555"}}})
    )
    code, report = run(tmp_path / "case/realization-0", "--casepath", str(tmp_path / "case"))
    assert code == 1 and "differs from data uuid" in messages(report)


def test_no_metadata_found_is_usage_error(tmp_path):
    assert chk.main([str(tmp_path)]) == 2


def test_script_writes_nothing(tmp_path):
    write_pair(tmp_path, "top", meta(md5="0" * 32))
    before = sorted(p.name for p in tmp_path.rglob("*"))
    run(tmp_path)
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_real_fmu_dataio_export_and_corruption(tmp_path):
    """Export with the installed fmu-dataio, then check, then corrupt the data."""
    np = pytest.importorskip("numpy")
    xtgeo = pytest.importorskip("xtgeo")
    dataio = pytest.importorskip("fmu.dataio")
    config_file = (
        Path(os.environ.get("DATAIO_EXAMPLES", Path.home() / "public-data/fmu-dataio/examples"))
        / "fmuconfig/output/global_variables.yml"
    )
    if not config_file.is_file():
        pytest.skip("fmu-dataio example global_variables.yml not found. Set DATAIO_EXAMPLES.")
    model = tmp_path / "ff/2026/rms/model"
    model.mkdir(parents=True)
    (tmp_path / "ff/2026/fmuconfig/output").mkdir(parents=True)
    shutil.copy(config_file, tmp_path / "ff/2026/fmuconfig/output/global_variables.yml")
    cfg = yaml.safe_load(config_file.read_text())
    cwd = Path.cwd()
    os.chdir(model)
    try:
        surf = xtgeo.RegularSurface(
            ncol=5, nrow=4, xinc=10, yinc=10, values=np.arange(20.0).reshape(5, 4) + 1500
        )
        exported = Path(dataio.ExportData(config=cfg, name="topx", content="depth", unit="m").export(surf))
    finally:
        os.chdir(cwd)
    code, report = run(tmp_path)
    assert code == 0, report  # outside ERT: only the 'no fmu block' warning
    assert any("no 'fmu' block" in p["message"] for p in report["problems"])
    exported.write_bytes(exported.read_bytes() + b"corrupt")
    code, report = run(tmp_path)
    assert code == 1 and "MD5" in messages(report)
