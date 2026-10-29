"""Cleanup of approval gates that older versions installed. Temporary homes only."""

import json
import shutil
from pathlib import Path

from tools import apply_drop as drop
from tools import remove_legacy_gates as cleanup

ROOT = Path(__file__).resolve().parents[1]
GATE = {"type": "command", "command": "python3 '/old repo/tools/gate_hook.py'", "timeout": 10}
OTHER = {"type": "command", "command": "team-hook"}


def write_settings(home: Path, settings: dict) -> Path:
    path = home / ".claude/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings))
    return path


def legacy_settings() -> dict:
    return {
        "model": "keep-me",
        "permissions": {"deny": ["Bash(rm *)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash|PowerShell", "hooks": [GATE]},
                {"matcher": "Edit", "hooks": [OTHER, dict(GATE)]},
            ],
            "Stop": [{"hooks": [{"type": "command", "command": "notify"}]}],
        },
    }


def test_strips_only_the_gate_hook_and_keeps_the_rest(tmp_path, capsys):
    path = write_settings(tmp_path, legacy_settings())
    assert cleanup.main(["--home", str(tmp_path)]) == 0
    assert json.loads(path.read_text()) == {
        "model": "keep-me",
        "permissions": {"deny": ["Bash(rm *)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Edit", "hooks": [OTHER]}],
            "Stop": [{"hooks": [{"type": "command", "command": "notify"}]}],
        },
    }
    assert path.read_text().startswith('{\n  "model"')
    assert capsys.readouterr().out.count("REMOVED hook") == 2


def test_empty_containers_are_dropped(tmp_path):
    path = write_settings(tmp_path, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [GATE]}]}})
    cleanup.main(["--home", str(tmp_path)])
    assert json.loads(path.read_text()) == {}


def test_backup_is_written_once_and_second_run_is_a_no_op(tmp_path, capsys):
    original = legacy_settings()
    path = write_settings(tmp_path, original)
    cleanup.main(["--home", str(tmp_path)])
    backup = path.with_name("settings.json.bak")
    assert json.loads(backup.read_text()) == original
    cleaned = path.read_text()
    capsys.readouterr()
    cleanup.main(["--home", str(tmp_path)])
    assert path.read_text() == cleaned
    assert sorted(p.name for p in path.parent.iterdir()) == ["settings.json", "settings.json.bak"]
    assert capsys.readouterr().out.strip() == "nothing to clean"


def test_existing_backup_is_not_overwritten(tmp_path):
    path = write_settings(tmp_path, legacy_settings())
    path.with_name("settings.json.bak").write_text("user backup")
    cleanup.main(["--home", str(tmp_path)])
    assert path.with_name("settings.json.bak").read_text() == "user backup"
    assert "gate_hook.py" in path.with_name("settings.json.bak.1").read_text()


def test_deletes_the_user_codex_rules(tmp_path):
    rules = tmp_path / ".codex/rules/re-agent-gates.rules"
    rules.parent.mkdir(parents=True)
    rules.write_text("# old\n")
    (rules.parent / "mine.rules").write_text("# keep\n")
    cleanup.main(["--home", str(tmp_path)])
    assert not rules.exists()
    assert (rules.parent / "mine.rules").is_file()


def test_malformed_json_is_left_untouched(tmp_path, capsys):
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"hooks": gate_hook.py')
    assert cleanup.main(["--home", str(tmp_path)]) == 0
    assert path.read_text() == '{"hooks": gate_hook.py'
    assert not path.with_name("settings.json.bak").exists()
    assert "WARNING" in capsys.readouterr().out


def test_dry_run_changes_nothing(tmp_path, capsys):
    path = write_settings(tmp_path, legacy_settings())
    before = path.read_text()
    rules = tmp_path / ".codex/rules/re-agent-gates.rules"
    rules.parent.mkdir(parents=True)
    rules.write_text("# old\n")
    cleanup.main(["--home", str(tmp_path), "--dry-run"])
    assert path.read_text() == before and rules.is_file()
    assert not path.with_name("settings.json.bak").exists()
    out = capsys.readouterr().out
    assert "WOULD REMOVE hook" in out and "WOULD DELETE" in out


def make_legacy_install(dest: Path) -> None:
    for name in cleanup.DEST_FILES:
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        (dest / name).write_text("# legacy\n")
    (dest / "tools/doctor.py").write_text("# keep\n")
    write_settings(dest, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [GATE, OTHER]}]}})


def test_dest_removes_legacy_files_and_project_hook(tmp_path):
    home, dest = tmp_path / "home", tmp_path / "installed"
    home.mkdir()
    make_legacy_install(dest)
    cleanup.main(["--home", str(home), "--dest", str(dest)])
    assert not any((dest / name).exists() for name in cleanup.DEST_FILES)
    assert not (dest / ".codex/rules").exists()
    assert (dest / "tools/doctor.py").read_text() == "# keep\n"
    settings = json.loads((dest / ".claude/settings.json").read_text())
    assert settings == {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [OTHER]}]}}


def drop_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "tools").mkdir(parents=True)
    shutil.copy2(ROOT / "tools/remove_legacy_gates.py", source / "tools/remove_legacy_gates.py")
    (source / "README.md").write_text("new\n")
    return source


def test_apply_drop_previews_then_runs_the_cleanup(tmp_path, capsys):
    home, dest = tmp_path / "home", tmp_path / "installed"
    user_settings = write_settings(home, legacy_settings())
    make_legacy_install(dest)
    source = drop_source(tmp_path)
    args = ["--src", str(source), "--dest", str(dest), "--home", str(home)]

    before = user_settings.read_text()
    assert drop.main(args) == 0
    out = capsys.readouterr().out
    assert "LEGACY GATES WOULD REMOVE hook" in out and "LEGACY GATES WOULD DELETE" in out
    assert user_settings.read_text() == before
    assert (dest / "tools/gate_hook.py").is_file()

    assert drop.main([*args, "--apply"]) == 0
    out = capsys.readouterr().out
    assert "LEGACY GATES REMOVED hook" in out and "LEGACY GATES DELETED" in out
    assert "gate_hook.py" not in user_settings.read_text()
    assert not any((dest / name).exists() for name in cleanup.DEST_FILES)
    assert list(tmp_path.glob("installed.bak-*.zip"))

    assert drop.main([*args, "--apply"]) == 0
    assert "LEGACY GATES nothing to clean" in capsys.readouterr().out
