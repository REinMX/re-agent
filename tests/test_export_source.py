"""Source-only GitHub handoff from committed files, without personal history/runtime data."""

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
CLAUDE_COMMANDS = (".claude/commands/linux-direct.md", ".claude/commands/worklog.md")


def load():
    path = ROOT / "tools/export_source.py"
    assert path.is_file(), "missing source-only export tool"
    spec = importlib.util.spec_from_file_location("export_source", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "main")
    files = {
        "README.md": "Reviewed source\n",
        "LICENSE": "GNU GENERAL PUBLIC LICENSE\n",
        "AGENTS.md": "Agent instructions\n",
        "CLAUDE.md": "@AGENTS.md\n",
        "pyproject.toml": '[project]\nname = "example"\n',
        "site.contract.example.yml": 'project: "public-example"\n',
        ".gitignore": "site.contract.yml\n.env\n",
        "skills/reservoir-engineer/SKILL.md": "---\nname: reservoir-engineer\ndescription: Entry\n---\n",
        "tools/install_project.py": "# public tool\n",
        "tools/doctor.py": "# public doctor\n",
        ".claude/settings.json": "{}\n",
        **{name: (ROOT / name).read_text(encoding="utf-8") for name in CLAUDE_COMMANDS},
        "tests/data/public.csv": "value\n1\n",
        "evals/cases.toml": "# public cases\n",
        "docs/SITE_SETUP.md": "Site setup\n",
        ".github/workflows/check.yml": "name: check\n",
        ".claude/skills": "../skills\n",
        ".agents/skills": "../skills\n",
        "evals/results/old/results.json": '{"personal_history": true}\n',
        "reports/private/report.md": "private result\n",
        "worklog/private.md": "private work log\n",
        "skills/demo/.env.local": "private environment\n",
        "tools/auth.json": "private credentials\n",
        "tools/private.key": "private key\n",
        "tests/__pycache__/cache.pyc": "cache\n",
        "site.contract.yml": "private contract\n",
        ".env": "private environment\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "add", "--force", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    return root


INSTALLER_READS = """
import importlib.util, json, os, sys, tempfile
from pathlib import Path

source, target = Path(sys.argv[1]), Path(sys.argv[2])
sys.pycache_prefix = tempfile.mkdtemp()  # a cached .pyc would hide the read of its source
reads = set()


def record(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.path.abspath(os.fsdecode(args[0])))
        if path.is_relative_to(source) and path.is_file():
            reads.add(path.relative_to(source).as_posix())


sys.addaudithook(record)
spec = importlib.util.spec_from_file_location("install_project", source / "tools/install_project.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
with open(os.devnull, "w") as quiet:
    sys.stdout = quiet
    for mode in ("direct", "manual"):
        assert installer.main(["--source", str(source), "--target", str(target), "--mode", mode]) == 0
sys.stdout = sys.__stdout__
print(json.dumps(sorted(reads)))
"""


def installer_reads(source: Path) -> list[str]:
    with tempfile.TemporaryDirectory() as target:
        result = subprocess.run(
            [sys.executable, "-B", "-c", INSTALLER_READS, str(source.resolve()), target],
            check=True,
            capture_output=True,
            text=True,
        )
    return json.loads(result.stdout)


@pytest.fixture
def installable_source(source):
    for name in installer_reads(ROOT):
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_bytes((ROOT / name).read_bytes())
    git(source, "add", ".")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "installable fixture",
    )
    return source


def test_export_retains_committed_claude_command_definitions(source, tmp_path):
    tool = load()
    expected = {
        name: subprocess.run(
            ["git", "-C", str(source), "show", f"HEAD:{name}"], check=True, capture_output=True
        ).stdout
        for name in CLAUDE_COMMANDS
    }
    for name in CLAUDE_COMMANDS:
        (source / name).write_text("uncommitted command edit\n", encoding="utf-8")
    (source / ".claude/commands/untracked.md").write_text("untracked command\n", encoding="utf-8")
    files, manifest = tool.snapshot(source, "HEAD")
    for name, content in expected.items():
        assert name in files, f"missing committed Claude command: {name}"
        assert files[name] == content
        assert manifest["files"][name]["kind"] == "file"
        assert manifest["files"][name]["git_mode"] == "100644"
    assert ".claude/commands/untracked.md" not in files
    output = tmp_path / "source.zip"
    output.write_bytes(tool.make_zip(files, manifest))
    with zipfile.ZipFile(output) as archive:
        for name, content in expected.items():
            assert archive.read(tool.PREFIX + name) == content
            assert archive.getinfo(tool.PREFIX + name).external_attr >> 16 == 0o100644
        archive.extractall(tmp_path / "extracted")
    extracted = tmp_path / "extracted" / tool.PREFIX
    for name, content in expected.items():
        assert (extracted / name).read_bytes() == content
        assert not (extracted / name).is_symlink()


@pytest.mark.skipif(sys.platform != "linux", reason="direct installs are Linux-only")
def test_extracted_export_installs_claude_command(installable_source, tmp_path):
    tool = load()
    output = tmp_path / "source.zip"
    files, manifest = tool.snapshot(installable_source, "HEAD")
    output.write_bytes(tool.make_zip(files, manifest))
    with zipfile.ZipFile(output) as archive:
        archive.extractall(tmp_path / "extracted")
    extracted = tmp_path / "extracted" / tool.PREFIX
    target = tmp_path / "workspace with spaces"
    result = subprocess.run(
        [
            sys.executable,
            str(extracted / "tools/install_project.py"),
            "--source",
            str(extracted),
            "--target",
            str(target),
            "--mode",
            "direct",
            "--harness",
            "claude",
            "--apply",
        ],
        cwd=extracted,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    command = ".claude/commands/linux-direct.md"
    assert (target / command).is_file(), "extracted export silently lost the installed Claude command"
    assert (target / command).read_bytes() == (installable_source / command).read_bytes()
    fragments = sorted(
        p.relative_to(installable_source) for p in (installable_source / "changes").glob("*.md")
    )
    assert fragments, "the installable source has no changes/ fragments"
    for name in fragments:
        assert (target / ".reservoir-agent" / name).read_bytes() == (installable_source / name).read_bytes()
    permissions = json.loads((target / ".claude/settings.json").read_text())["permissions"]
    assert permissions["defaultMode"] == "default"
    assert "Bash(*)" not in permissions["ask"]


def test_export_excludes_non_markdown_private_claude_commands(source, tmp_path):
    tool = load()
    excluded = [
        ".claude/commands/config.json",
        ".claude/commands/history.jsonl",
        ".claude/commands/helper.py",
        ".claude/commands/notes.txt",
        ".claude/commands/site.contract.yml",
        ".claude/commands/auth.json",
        ".claude/commands/credentials.json",
        ".claude/commands/id_rsa",
        ".claude/commands/id_ed25519",
        ".claude/commands/private.key",
        ".claude/commands/.env",
        ".claude/commands/.env.local.md",
        ".claude/commands/nested/public.md",
        ".claude/config.json",
        ".claude/history/session.md",
        ".claude/credentials/private.md",
        *[
            f".claude/commands/{part}/private.md"
            for part in ("secrets", "credentials", "reports", "worklog", "transcripts", ".venv")
        ],
    ]
    commit_paths(source, *excluded)
    files, manifest = tool.snapshot(source, "HEAD")
    assert not set(excluded).intersection(files)
    output = tmp_path / "source.zip"
    output.write_bytes(tool.make_zip(files, manifest))
    with zipfile.ZipFile(output) as archive:
        assert all(tool.PREFIX + name not in archive.namelist() for name in excluded)
        assert all(name not in manifest["files"] for name in excluded)


@pytest.mark.parametrize(
    "name",
    [
        "/.claude/commands/escape.md",
        ".claude/commands/../escape.md",
        ".claude/commands/bad\\name.md",
    ],
)
def test_claude_command_filter_rejects_unsafe_paths(name):
    with pytest.raises(ValueError, match="unsafe Git path"):
        load().included(name)


@pytest.mark.parametrize("name", ["CON.md", "bad:name.md", "control\x01.md"])
def test_export_rejects_windows_invalid_claude_command(source, tmp_path, capsys, name):
    tool = load()
    commit_paths(source, f".claude/commands/{name}")
    output = tmp_path / "new-folder/source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 2
    assert "Windows" in capsys.readouterr().err
    assert not output.parent.exists()


def test_dry_run_creates_no_output(source, tmp_path, capsys):
    tool = load()
    output = tmp_path / "new-folder/source.zip"
    assert tool.main(["--source", str(source), "--output", str(output)]) == 0
    assert not output.parent.exists()
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is False
    assert report["commit"] == git(source, "rev-parse", "HEAD")


def test_export_uses_committed_allowlisted_source_not_worktree(source, tmp_path, capsys):
    tool = load()
    (source / "README.md").write_text("uncommitted confidential edit\n")
    (source / "tools/untracked.py").write_text("untracked work\n")
    before = git(source, "status", "--porcelain")
    output = tmp_path / "source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is True
    assert report["worktree_dirty"] is True
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        prefix = "reservoir-engineer-agent/"
        assert archive.read(prefix + "README.md") == b"Reviewed source\n"
        assert prefix + "tests/data/public.csv" in names
        assert prefix + "site.contract.example.yml" in names
        assert archive.read(prefix + "LICENSE") == b"GNU GENERAL PUBLIC LICENSE\n"
        assert prefix + "docs/SITE_SETUP.md" in names
        assert not any(".git/" in name or "evals/results/" in name for name in names)
        for forbidden in (
            "site.contract.yml",
            ".env",
            "reports/private/report.md",
            "worklog/private.md",
            "skills/demo/.env.local",
            "tools/auth.json",
            "tools/private.key",
            "tools/untracked.py",
            "tests/__pycache__/cache.pyc",
        ):
            assert prefix + forbidden not in names
        manifest = json.loads(archive.read(prefix + "EXPORT_MANIFEST.json"))
        assert manifest["source_commit"] == git(source, "rev-parse", "HEAD")
        assert "excluded_tracked_paths" not in manifest
        assert "reports/private/report.md" not in json.dumps(manifest)
        assert manifest["excluded_tracked_count"] > 0
        for name, item in manifest["files"].items():
            assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == item["sha256"]
        assert archive.read(prefix + ".agents/skills").strip() == b"../skills"
        assert manifest["files"][".agents/skills"]["kind"] == "skill-link-placeholder"
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.with_suffix(".zip.sha256").read_text().split()[0] == digest
    assert git(source, "status", "--porcelain") == before


@pytest.mark.parametrize("replacement_kind", ["blob", "commit"])
def test_export_ignores_git_replacement_objects(source, tmp_path, replacement_kind):
    tool = load()
    reviewed_commit = git(source, "rev-parse", "HEAD")
    reviewed_tree = git(source, "rev-parse", f"{reviewed_commit}^{{tree}}")
    original_blob = git(source, "rev-parse", f"{reviewed_commit}:README.md")
    (source / "README.md").write_text("unreviewed replacement bytes\n")
    replacement_blob = git(source, "hash-object", "-w", "README.md")
    if replacement_kind == "blob":
        git(source, "replace", original_blob, replacement_blob)
    else:
        git(source, "add", "README.md")
        replacement_tree = git(source, "write-tree")
        replacement_commit = git(
            source,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit-tree",
            replacement_tree,
            "-p",
            reviewed_commit,
            "-m",
            "unreviewed replacement fixture",
        )
        git(source, "replace", reviewed_commit, replacement_commit)
    # Prove the fixture substitutes the reviewed bytes for ordinary Git reads.
    assert git(source, "show", f"{reviewed_commit}:README.md") == "unreviewed replacement bytes"
    before = git(source, "status", "--porcelain")
    replacements = git(source, "replace", "-l")
    output = tmp_path / "source.zip"
    assert (
        tool.main(["--source", str(source), "--ref", reviewed_commit, "--output", str(output), "--apply"])
        == 0
    )
    with zipfile.ZipFile(output) as archive:
        assert archive.read(tool.PREFIX + "README.md") == b"Reviewed source\n"
        manifest = json.loads(archive.read(tool.PREFIX + "EXPORT_MANIFEST.json"))
        assert manifest["source_commit"] == reviewed_commit
        assert manifest["source_tree"] == reviewed_tree
        assert manifest["files"]["README.md"]["sha256"] == hashlib.sha256(b"Reviewed source\n").hexdigest()
    assert git(source, "status", "--porcelain") == before
    assert git(source, "replace", "-l") == replacements


def test_export_is_deterministic(source, tmp_path):
    tool = load()
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    for path in (first, second):
        assert tool.main(["--source", str(source), "--output", str(path), "--apply"]) == 0
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("existing", ["archive", "checksum"])
def test_existing_delivery_is_never_overwritten(source, tmp_path, existing):
    tool = load()
    output = tmp_path / "source.zip"
    occupied = output if existing == "archive" else output.with_suffix(".zip.sha256")
    occupied.write_bytes(b"keep this file")
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 2
    assert occupied.read_bytes() == b"keep this file"
    assert not (output.with_suffix(".zip.sha256") if existing == "archive" else output).exists()


def test_export_rejects_unknown_ref(source, tmp_path):
    tool = load()
    output = tmp_path / "source.zip"
    assert tool.main(["--source", str(source), "--ref", "missing", "--output", str(output), "--apply"]) == 2
    assert not output.exists()


def test_export_rejects_option_as_ref(source, tmp_path):
    tool = load()
    output = tmp_path / "source.zip"
    assert tool.main(["--source", str(source), "--ref=--help", "--output", str(output), "--apply"]) == 2
    assert not output.exists()


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="Windows symlink privileges")
@pytest.mark.parametrize("name", ["tools/escape.py", ".claude/commands/escape.md"])
def test_export_refuses_arbitrary_tracked_symlink(source, tmp_path, name):
    tool = load()
    (source / name).symlink_to("../../outside/private.py")
    git(source, "add", name)
    git(
        source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "unsafe link"
    )
    output = tmp_path / "source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 2
    assert not output.exists()


def test_export_requires_router(source, tmp_path):
    tool = load()
    git(source, "rm", "skills/reservoir-engineer/SKILL.md")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "remove router",
    )
    output = tmp_path / "source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 2
    assert not output.exists()


def commit_paths(source, *names):
    """Add Git paths without materializing potentially Windows-invalid worktree names."""
    blob = git(source, "rev-parse", "HEAD:README.md")
    for name in names:
        git(
            source,
            "-c",
            "core.protectNTFS=false",
            "-c",
            "core.ignoreCase=false",
            "update-index",
            "--add",
            "--cacheinfo",
            "100644",
            blob,
            name,
        )
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "path portability fixture",
    )


WINDOWS_INVALID_COMPONENTS = [
    *[
        base + suffix
        for base in (
            "CON",
            "prn",
            "AuX",
            "nul",
            *[f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"],
        )
        for suffix in ("", ".txt")
    ],
    *[f"bad{character}name" for character in ':<>"|?*'],
    *[f"bad{chr(code)}name" for code in range(1, 32)],
    f"bad{chr(127)}name",
    f"bad{chr(133)}name",
    "trailing.",
    "trailing ",
    "CON .txt",
]


@pytest.mark.parametrize("component", WINDOWS_INVALID_COMPONENTS)
def test_export_rejects_windows_invalid_filenames(source, tmp_path, capsys, component):
    tool = load()
    commit_paths(source, f"docs/{component}")
    output = tmp_path / "new-folder/source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 2
    assert "Windows" in capsys.readouterr().err
    assert not output.parent.exists()


@pytest.mark.parametrize("component", ["NUL.txt", "COM¹", "bad:name", "trailing.", "trailing "])
def test_export_rejects_windows_invalid_directories(source, tmp_path, capsys, component):
    tool = load()
    commit_paths(source, f"docs/{component}/valid.md")
    output = tmp_path / "new-folder/source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 2
    assert "Windows" in capsys.readouterr().err
    assert not output.parent.exists()


@pytest.mark.parametrize("name", ["docs/CON.txt", f"docs/bad{chr(0)}name", "docs/bad\\name"])
def test_zip_rejects_windows_invalid_paths_before_creation(monkeypatch, name):
    tool = load()

    def unexpected_zip(*args, **kwargs):
        pytest.fail("created ZIP before validating exported paths")

    monkeypatch.setattr(tool.zipfile, "ZipFile", unexpected_zip)
    with pytest.raises(ValueError, match="Windows"):
        tool.make_zip({name: b"source\n"}, {"files": {}})


def test_excluded_windows_invalid_names_do_not_block_export(source, tmp_path):
    tool = load()
    excluded = [
        "evals/results/CON.json",
        "reports/bad:name.txt",
        "docs/secrets/NUL.json",
        "tools/CON/auth.json",
        "skills/demo/.env.bad:name",
    ]
    commit_paths(source, *excluded)
    output = tmp_path / "source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 0
    with zipfile.ZipFile(output) as archive:
        assert all(tool.PREFIX + name not in archive.namelist() for name in excluded)


def test_export_accepts_windows_valid_names(source, tmp_path):
    tool = load()
    names = [
        "docs/COM0.md",
        "docs/COM10.md",
        "docs/LPT0.md",
        "docs/LPT10.md",
        "docs/CONsole.md",
        "docs/café.md",
        "docs/shared/one.md",
        "docs/shared/two.md",
    ]
    commit_paths(source, *names)
    output = tmp_path / "source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 0
    with zipfile.ZipFile(output) as archive:
        assert all(archive.read(tool.PREFIX + name) == b"Reviewed source\n" for name in names)


CASEFOLD_COLLISIONS = [
    ("docs/Guide.md", "docs/guide.md"),
    (".claude/commands/Linux.md", ".claude/commands/linux.md"),
    ("docs/Release/one.md", "docs/release/two.md"),
    ("docs/Entry", "docs/entry/data.md"),
    ("docs/Entry/data.md", "docs/entry"),
    ("docs/Straße/one.md", "docs/STRASSE/two.md"),
]


@pytest.mark.parametrize("names", CASEFOLD_COLLISIONS)
def test_export_rejects_casefold_collisions(source, tmp_path, capsys, names):
    tool = load()
    commit_paths(source, *names)
    output = tmp_path / "new-folder/source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 2
    assert "case-insensitive" in capsys.readouterr().err
    assert not output.parent.exists()


@pytest.mark.parametrize(
    "names",
    [
        *CASEFOLD_COLLISIONS,
        ("export_manifest.json",),
        ("EXPORT_MANIFEST.json/child.txt",),
        ("docs/shared", "docs/shared/child.md"),
    ],
)
def test_zip_rejects_casefold_collisions_before_creation(monkeypatch, names):
    tool = load()

    def unexpected_zip(*args, **kwargs):
        pytest.fail("created ZIP before validating exported paths")

    monkeypatch.setattr(tool.zipfile, "ZipFile", unexpected_zip)
    with pytest.raises(ValueError, match="case-insensitive"):
        tool.make_zip(dict.fromkeys(names, b"source\n"), {"files": {}})


def test_excluded_casefold_collisions_do_not_block_export(source, tmp_path):
    tool = load()
    included = "docs/data/public.md"
    excluded = [
        "docs/DATA/secrets/private.md",
        "evals/results/Release/one.json",
        "evals/results/release/two.json",
    ]
    commit_paths(source, included, *excluded)
    output = tmp_path / "source.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 0
    with zipfile.ZipFile(output) as archive:
        assert archive.read(tool.PREFIX + included) == b"Reviewed source\n"
        assert all(tool.PREFIX + name not in archive.namelist() for name in excluded)


def test_export_includes_every_template_file():
    tool = load()
    files = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "templates").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    assert "templates/site/01-fmu-prior-design/params.yml" in files
    assert [name for name in files if not tool.included(name)] == []


def test_export_includes_changelog_fragments():
    tool = load()
    files = [p.relative_to(ROOT).as_posix() for p in (ROOT / "changes").glob("*.md")]
    assert "changes/README.md" in files
    assert [name for name in files if not tool.included(name)] == []


def test_is_vendored_pstack_skill_link_accepts_only_skills_pstack_targets():
    tool = load()
    assert tool.is_vendored_pstack_skill_link(
        "skills/pstack-architect", b"../plugins/pstack/skills/architect"
    )
    assert tool.is_vendored_pstack_skill_link("skills/pstack-why", b"../plugins/pstack/skills/why\n")
    # Wrong path prefix: still refuse even if the target sits under plugins/pstack.
    assert not tool.is_vendored_pstack_skill_link(
        "tools/pstack-architect", b"../plugins/pstack/skills/architect"
    )
    # Name matches but target points elsewhere.
    assert not tool.is_vendored_pstack_skill_link("skills/pstack-architect", b"../skills/reservoir-engineer")
    # Absolute or escaping targets are not skipped.
    assert not tool.is_vendored_pstack_skill_link(
        "skills/pstack-architect", b"/plugins/pstack/skills/architect"
    )
    assert not tool.is_vendored_pstack_skill_link(
        "skills/pstack-architect", b"../../outside/plugins/pstack/skills/architect"
    )


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="Windows symlink privileges")
def test_export_skips_vendored_pstack_skill_links_and_refuses_others(source, tmp_path, capsys):
    tool = load()
    vendor = source / "plugins/pstack/skills/architect"
    vendor.mkdir(parents=True)
    (vendor / "SKILL.md").write_text("vendored\n", encoding="utf-8")
    link = source / "skills/pstack-architect"
    link.symlink_to("../plugins/pstack/skills/architect")
    decoy = source / "tools/escape.py"
    decoy.symlink_to("../../outside/private.py")
    git(
        source,
        "add",
        "plugins/pstack/skills/architect/SKILL.md",
        "skills/pstack-architect",
        "tools/escape.py",
    )
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "pstack link and decoy",
    )
    # First: with only the pstack link, export succeeds and reports the skip.
    git(source, "rm", "-f", "tools/escape.py")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "remove decoy",
    )
    output = tmp_path / "pstack-skip.zip"
    assert tool.main(["--source", str(source), "--output", str(output), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["skipped_tracked_symlink_count"] == 1
    assert report["skipped_tracked_symlinks"] == ["skills/pstack-architect"]
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert tool.PREFIX + "skills/pstack-architect" not in names
        assert tool.PREFIX + "plugins/pstack/skills/architect/SKILL.md" not in names
        manifest = json.loads(archive.read(tool.PREFIX + "EXPORT_MANIFEST.json"))
        assert manifest["skipped_tracked_symlinks"] == ["skills/pstack-architect"]
        assert "skills/pstack-architect" not in manifest["files"]

    # Second: an arbitrary tracked symlink is still refused.
    (source / "tools/escape.py").symlink_to("../../outside/private.py")
    git(source, "add", "tools/escape.py")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "decoy again",
    )
    refused = tmp_path / "refused.zip"
    assert tool.main(["--source", str(source), "--output", str(refused), "--apply"]) == 2
    assert "tracked symlink refused: tools/escape.py" in capsys.readouterr().err
    assert not refused.exists()
