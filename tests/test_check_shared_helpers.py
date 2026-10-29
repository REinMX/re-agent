import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools/check_shared_helpers.py"


def test_repo_passes():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scripts scanned, 0 redefinitions" in result.stdout


def test_copy_fails(tmp_path):
    folder = tmp_path / "skills/example/scripts"
    folder.mkdir(parents=True)
    (folder / "copy.py").write_text("\ndef data_file():\n    pass\n")
    result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == (
        "skills/example/scripts/copy.py:2: data_file is defined in "
        "skills/stage-contract/scripts/runpath_lib.py; import it\n"
    )


def test_ignores_symlinks_and_nested_definitions(tmp_path):
    folder = tmp_path / "skills/example"
    folder.mkdir(parents=True)
    (folder / "nested.py").write_text("def outer():\n    def data_file():\n        pass\n")
    ignored = tmp_path / "copies"
    ignored.mkdir()
    (ignored / "copy.py").write_text("def data_file():\n    pass\n")
    (tmp_path / "skills/linked").symlink_to(ignored, target_is_directory=True)
    result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "shared helpers: 1 scripts scanned, 0 redefinitions\n"
