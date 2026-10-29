"""Reservoir-only delivered bytes, with their original committed provenance."""

import hashlib
import subprocess

import pytest
import test_export_source
from test_export_source import ROOT, git, load

source = test_export_source.source


def commit_files(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "projection fixture",
    )


def test_projection_preserves_source_hashes_and_reservoir_checks(source):
    originals = {name: (ROOT / name).read_bytes() for name in ("AGENTS.md", "Makefile")}
    originals.update(
        {
            "tools/build_agent_surfaces.py": b"vendor generator\n",
            "tools/pstack_models.py": b"vendor models\n",
            "tests/test_agent_surfaces.py": b"vendor checks\n",
            "tests/test_pstack_models.py": b"vendor checks\n",
            "tests/test_export_checkout.py": b"checkout checks\n",
            "docs/PSTACK_AGENTS.md": b"vendor setup\n",
            "tests/test_material_balance.py": b"scientific checks\n",
        }
    )
    commit_files(source, originals)
    tool = load()
    files, manifest = tool.snapshot(source, "HEAD")
    assert manifest["distribution"]["kind"] == "reservoir-only"
    assert manifest["distribution"]["projection_version"] == 1
    assert set(manifest["distribution"]["omitted_components"]) == {
        "pstack",
        "checkout-distribution",
        "authoring-harness",
        "release-update-guide",
    }
    for name in originals:
        if name not in {"AGENTS.md", "Makefile", "tests/test_material_balance.py"}:
            assert name not in files
    assert files["tests/test_material_balance.py"] == originals["tests/test_material_balance.py"]
    assert "pstack" not in files["AGENTS.md"].decode().lower()
    assert b"surfaces" not in files["Makefile"]
    assert b"check-here: lint skills helpers test" in files["Makefile"]
    assert b"REQUIRE_FULL=1" in files["Makefile"]
    for name, content in files.items():
        item = manifest["files"][name]
        raw = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(source), "show", f"HEAD:{name}"],
            capture_output=True,
            check=True,
        ).stdout
        assert item["source_sha256"] == hashlib.sha256(raw).hexdigest()
        assert item["source_bytes"] == len(raw)
        assert item["sha256"] == hashlib.sha256(content).hexdigest()
        assert item["bytes"] == len(content)
        assert bool(item["transforms"]) == (raw != content)
    assert (source / "AGENTS.md").read_bytes() == originals["AGENTS.md"]
    assert (source / "Makefile").read_bytes() == originals["Makefile"]


@pytest.mark.parametrize(
    "text",
    [
        "<!-- pstack:begin -->\nno end\n",
        "<!-- pstack:end -->\n",
        "<!-- pstack:end -->\n<!-- pstack:begin -->\n",
        "<!-- pstack:begin -->\n<!-- pstack:begin -->\n<!-- pstack:end -->\n",
        "<!-- pstack:begin -->\nx\n<!-- pstack:end -->\n<!-- pstack:end -->\n",
        "<!-- pstack:begin -->\nx\n<!-- pstack:end -->\nRead plugins/pstack/skills/how/SKILL.md\n",
        "Read pstack:poteto-mode first\n",
    ],
)
def test_projection_refuses_malformed_or_stranded_vendor_instructions(source, text):
    commit_files(source, {"AGENTS.md": text.encode()})
    with pytest.raises(ValueError, match="pstack"):
        load().snapshot(source, "HEAD")


def test_projection_refuses_unrecognized_generator_recipe(source):
    text = (ROOT / "Makefile").read_text()
    commit_files(
        source, {"Makefile": (text + '\nother:\n\t"$(PY)" tools/build_agent_surfaces.py --check\n').encode()}
    )
    with pytest.raises(ValueError, match="stranded"):
        load().snapshot(source, "HEAD")


def test_manifest_lists_each_omitted_path_under_its_component(source):
    commit_files(
        source,
        {
            "plugins/pstack/skills/how/SKILL.md": b"vendor skill\n",
            "tools/build_agent_surfaces.py": b"vendor generator\n",
            "tests/test_export_checkout.py": b"checkout checks\n",
            ".codex/config.toml": b'sandbox_mode = "workspace-write"\n',
            ".mcp.json": b"{}\n",
            "UPDATE.md": b"Release drop update.\n",
        },
    )
    files, manifest = load().snapshot(source, "HEAD")
    omitted = manifest["distribution"]["omitted_components"]
    assert set(omitted["pstack"]["paths"]) == {
        "plugins/pstack/skills/how/SKILL.md",
        "tools/build_agent_surfaces.py",
    }
    assert omitted["checkout-distribution"]["paths"] == ["tests/test_export_checkout.py"]
    assert omitted["authoring-harness"]["paths"] == [".codex/config.toml", ".mcp.json"]
    assert omitted["release-update-guide"]["paths"] == ["UPDATE.md"]
    assert all(item["reason"] for item in omitted.values())
    assert not {path for item in omitted.values() for path in item["paths"]} & set(files)


def test_harness_file_names_in_delivered_docs_are_not_stranded(source):
    """The installer writes the target's own .mcp.json and .codex/config.toml, so docs may name them."""
    commit_files(
        source,
        {
            ".mcp.json": b"{}\n",
            "docs/HARNESS.md": b"The installer writes .mcp.json, .vscode/mcp.json and .codex/config.toml.\n",
        },
    )
    files, _ = load().snapshot(source, "HEAD")
    assert "docs/HARNESS.md" in files


def test_delivered_docs_that_point_to_the_release_update_guide_are_stranded(source):
    commit_files(
        source, {"UPDATE.md": b"Release drop update.\n", "docs/HOW.md": b"See UPDATE.md to update.\n"}
    )
    with pytest.raises(ValueError, match="stranded.*docs/HOW.md"):
        load().snapshot(source, "HEAD")


@pytest.mark.parametrize("name", ["docs/SITE_SETUP.md", "README.md", ".claude/commands/worklog.md"])
def test_projection_refuses_delivered_docs_that_point_to_omitted_files(source, name):
    commit_files(source, {name: b"Set up harness surfaces with docs/PSTACK_AGENTS.md.\n"})
    with pytest.raises(ValueError, match=f"stranded.*{name}"):
        load().snapshot(source, "HEAD")


def test_exported_retired_reference_check_uses_manifest_without_git(source, tmp_path):
    import sys
    import zipfile

    commit_files(
        source,
        {
            "tests/test_no_gate_references.py": (ROOT / "tests/test_no_gate_references.py").read_bytes(),
            "tests/source_inventory.py": (ROOT / "tests/source_inventory.py").read_bytes(),
        },
    )
    tool = load()
    files, manifest = tool.snapshot(source, "HEAD")
    output = tmp_path / "snapshot.zip"
    output.write_bytes(tool.make_zip(files, manifest))
    with zipfile.ZipFile(output) as archive:
        archive.extractall(tmp_path / "extracted")
    extracted = tmp_path / "extracted" / tool.PREFIX
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_no_gate_references.py", "-q"],
        cwd=extracted,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
