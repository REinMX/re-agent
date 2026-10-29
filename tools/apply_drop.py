#!/usr/bin/env python3
"""Preview or apply a source drop, with a ZIP backup and opt-in pruning. Standard library only."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
import types
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

MANIFEST = "DROP_MANIFEST.txt"
CLEANUP = "tools/remove_legacy_gates.py"
PROTECTED = {".git", ".venv", "site.contract.yml", "worklog", ".env"}


@dataclass(frozen=True)
class Entry:
    data: bytes
    mode: int = stat.S_IFREG | 0o644

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def link(self) -> bool:
        return stat.S_ISLNK(self.mode)


def safe_name(name: str) -> str:
    parts = name.split("/")
    if not name or any(p in {"", ".", ".."} for p in parts):
        raise ValueError(f"unsafe drop path: {name!r}")
    if any(c in name for c in "\\:\r\n\0") or name.startswith("/"):
        raise ValueError(f"unsafe drop path: {name!r}")
    return name


def protected(name: str) -> bool:
    return any(
        p.lower() in PROTECTED or fnmatch.fnmatchcase(p.lower(), "*.local*") or p.lower().startswith(".env.")
        for p in PurePosixPath(name).parts
    )


def read_entry(path: Path) -> Entry:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return Entry(os.readlink(path).encode("utf-8"), mode)
    if not stat.S_ISREG(mode):
        raise ValueError(f"not a regular file or symlink: {path}")
    return Entry(path.read_bytes(), mode)


def walk_files(root: Path):
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(folder) / name
            if path.is_symlink():
                directories.remove(name)
                files.append(name)
        for name in sorted(files):
            path = Path(folder) / name
            yield path.relative_to(root).as_posix(), path


def manifest_text(entries: dict[str, str]) -> str:
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items()))


def parse_manifest(data: bytes) -> dict[str, str]:
    result = {}
    for line in data.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError("invalid DROP_MANIFEST.txt record")
        digest, name = match.groups()
        safe_name(name)
        if name == MANIFEST or name in result:
            raise ValueError(f"duplicate or recursive manifest path: {name}")
        result[name] = digest
    return result


def validate_entries(entries: dict[str, Entry]) -> None:
    for name, entry in entries.items():
        safe_name(name)
        if entry.link:
            target = entry.data.decode("utf-8")
            if not target or "\\" in target or ":" in target or target.startswith("/"):
                raise ValueError(f"unsafe symlink: {name}")
            base = list(PurePosixPath(name).parent.parts)
            for part in PurePosixPath(target).parts:
                if part == "..":
                    if not base:
                        raise ValueError(f"symlink escapes drop: {name}")
                    base.pop()
                elif part != ".":
                    base.append(part)
        if any(parent.as_posix() in entries for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise ValueError(f"drop file has a file or symlink ancestor: {name}")
    if MANIFEST in entries:
        recorded = parse_manifest(entries[MANIFEST].data)
        actual = {n: e.digest for n, e in entries.items() if n != MANIFEST}
        if recorded != actual:
            raise ValueError("drop manifest does not match its files and SHA256 values")


def load_drop(*, source: Path | None = None, archive: Path | None = None) -> dict[str, Entry]:
    if source is not None:
        if not source.is_dir():
            raise ValueError("--src must be an existing directory")
        entries = {safe_name(n): read_entry(p) for n, p in walk_files(source) if not protected(n)}
    else:
        entries = {}
        with zipfile.ZipFile(archive) as stream:
            members = [m for m in stream.infolist() if not m.is_dir()]
            names = [safe_name(m.filename) for m in members]
            # GitHub Download ZIP adds one repository directory around the files.
            prefix = ""
            if names and all("/" in n for n in names):
                first = names[0].split("/", 1)[0] + "/"
                wrapped = any(
                    n.removeprefix(first) in {MANIFEST, "AGENTS.md", "README.md", "VERSION"} for n in names
                ) or first.startswith("reservoir-engineer-agent-")
                if wrapped and all(n.startswith(first) for n in names):
                    prefix = first
            for member in members:
                name = safe_name(member.filename.removeprefix(prefix))
                if name in entries:
                    raise ValueError(f"duplicate ZIP path: {name}")
                mode = member.external_attr >> 16
                if not stat.S_IFMT(mode):
                    mode |= stat.S_IFREG | 0o644
                if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                    raise ValueError(f"unsupported ZIP member type: {name}")
                entries[name] = Entry(stream.read(member), mode)
    validate_entries(entries)
    return entries


class IgnoreRules:
    """Conservative gitignore matching without Git; Git augments it when available."""

    def __init__(self, dest: Path, entries: dict[str, Entry]):
        self.rules: list[tuple[str, str]] = []
        for name, path in walk_files(dest):
            if Path(name).name == ".gitignore" and not path.is_symlink():
                self.add(name, path.read_text(encoding="utf-8"))
        for name, entry in entries.items():
            if Path(name).name == ".gitignore" and not entry.link:
                self.add(name, entry.data.decode("utf-8"))
        extra = dest / ".git/info/exclude"
        if extra.is_file():
            self.add(".gitignore", extra.read_text(encoding="utf-8"))
        self.git_ignored: set[str] = set()
        names = list(entries)
        if names and ((dest / ".git").is_file() or (dest / ".git/HEAD").is_file()):
            try:
                result = subprocess.run(
                    ["git", "-C", str(dest), "check-ignore", "--no-index", "-z", "--stdin"],
                    input="\0".join(names).encode() + b"\0",
                    capture_output=True,
                    check=False,
                )
                if result.returncode not in (0, 1):
                    raise ValueError("Git ignore check failed")
                self.git_ignored = set(result.stdout.decode().rstrip("\0").split("\0"))
            except FileNotFoundError:
                pass

    def add(self, name: str, text: str) -> None:
        base = PurePosixPath(name).parent.as_posix()
        for line in text.splitlines():
            pattern = line.rstrip()
            # Ignoring negations protects more files, never fewer, without Git.
            if pattern and not pattern.startswith(("#", "!")):
                pattern = re.sub(r"\\(.)", r"\1", pattern)
                self.rules.append(("" if base == "." else base + "/", pattern))

    def matches(self, name: str) -> bool:
        if name in self.git_ignored:
            return True
        for base, pattern in self.rules:
            if not name.startswith(base):
                continue
            relative = name[len(base) :]
            anchored = pattern.startswith("/")
            pattern = pattern.strip("/")
            parts = relative.split("/")
            candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
            if not anchored and "/" not in pattern:
                candidates += parts
            patterns = {pattern}
            pending = [pattern]
            while pending:
                current = pending.pop()
                for match in re.finditer(r"\*\*/", current):
                    shorter = current[: match.start()] + current[match.end() :]
                    if shorter not in patterns:
                        patterns.add(shorter)
                        pending.append(shorter)
            if any(fnmatch.fnmatchcase(item, glob) for item in candidates for glob in patterns):
                return True
        return False


def blocked_ancestor(dest: Path, name: str) -> bool:
    return any(
        (dest / parent).is_symlink() or ((dest / parent).exists() and not (dest / parent).is_dir())
        for parent in PurePosixPath(name).parents
        if str(parent) != "."
    )


def backup(dest: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = dest.with_name(dest.name + ".bak-" + stamp + ".zip")
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as stream:
        for name, file in walk_files(dest):
            entry = read_entry(file)
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = entry.mode << 16
            stream.writestr(info, entry.data)
    return path


def write_entry(path: Path, entry: Entry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".drop-", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(entry.data)
        if entry.link:
            temp.unlink()
            temp.symlink_to(entry.data.decode("utf-8"))
        else:
            temp.chmod(stat.S_IMODE(entry.mode))
        os.replace(temp, path)
    finally:
        if temp.exists() or temp.is_symlink():
            temp.unlink()


def dangling_links(dest: Path, home: Path) -> list[Path]:
    result = []
    for directory in (home / ".claude/skills", home / ".codex/skills"):
        if not directory.is_dir():
            continue
        for link in directory.iterdir():
            if link.is_symlink() and not link.exists():
                target = link.resolve(strict=False)
                if any(target.is_relative_to(dest / part) for part in ("skills", "plugins")):
                    result.append(link)
    return sorted(result)


def legacy_cleanup(entries: dict[str, Entry]) -> types.ModuleType | None:
    """Load the drop's own copy of the legacy gate cleanup, if it ships one."""
    entry = entries.get(CLEANUP)
    if entry is None or entry.link:
        return None
    module = types.ModuleType("remove_legacy_gates")
    exec(compile(entry.data, CLEANUP, "exec"), module.__dict__)
    return module


def apply_drop(
    entries: dict[str, Entry], dest: Path, *, apply=False, prune=False, prune_links=False, home=None
) -> dict:
    dest = dest.absolute()
    if dest.is_symlink() or not dest.is_dir():
        raise ValueError("--dest must be an existing directory, not a symlink")
    dest = dest.resolve()
    manifest = dest / MANIFEST
    if manifest.is_symlink():
        raise ValueError("destination manifest must not be a symlink")
    previous = parse_manifest(manifest.read_bytes()) if manifest.is_file() else {}
    incoming = {name: entry for name, entry in entries.items() if name != MANIFEST}
    ignores = IgnoreRules(dest, {**incoming, **{n: Entry(b"") for n in previous if n not in incoming}})
    changes, deletions, preserved = {}, [], []
    ownership = dict(previous)
    for name, entry in sorted(incoming.items()):
        path = dest / name
        if (
            protected(name)
            or ignores.matches(name)
            or blocked_ancestor(dest, name)
            or path.is_dir()
            and not path.is_symlink()
        ):
            preserved.append(name)
            continue
        exists = path.exists() or path.is_symlink()
        current = read_entry(path) if exists else None
        if current and manifest.is_file() and name not in previous:
            preserved.append(name)
            continue
        if current and (current.digest != entry.digest or current.link != entry.link):
            if manifest.is_file() and (name not in previous or current.digest != previous[name]):
                preserved.append(name)
                continue
        ownership[name] = entry.digest
        if current != entry:
            changes[name] = entry
    for name in sorted(previous.keys() - incoming.keys()):
        path = dest / name
        if protected(name) or ignores.matches(name) or blocked_ancestor(dest, name):
            preserved.append(name)
        elif not (path.exists() or path.is_symlink()):
            ownership.pop(name, None)
        elif path.is_dir() and not path.is_symlink() or read_entry(path).digest != previous[name]:
            preserved.append(name)
        else:
            deletions.append(name)
            if prune:
                ownership.pop(name, None)
    links = dangling_links(dest, home or Path.home()) if prune_links else []
    for label, names in (
        ("COPY", changes),
        ("DELETE" if prune else "REMOVED (kept; use --prune)", deletions),
        ("PRESERVE", preserved),
    ):
        for name in names:
            print(f"{label} {name}")
    for link in links:
        print(f"PRUNE LINK {link}")
    content = manifest_text(ownership).encode()
    manifest_changed = not manifest.is_file() or manifest.read_bytes() != content
    backup_path = None
    if apply and (changes or prune and deletions or links or manifest_changed):
        backup_path = backup(dest)
        print(f"Backup: {backup_path}")
        for name, entry in changes.items():
            write_entry(dest / name, entry)
        if prune:
            for name in deletions:
                (dest / name).unlink()
        if prune_links:
            # Recompute after file pruning; a removed skill can make an installed link dangling.
            links = dangling_links(dest, home or Path.home())
            for link in links:
                link.unlink()
        write_entry(manifest, Entry(content))
    cleanup = legacy_cleanup(entries)
    actions: list[str] = []
    if cleanup is not None:
        home = home or Path.home()
        actions = cleanup.clean(home, dest, dry_run=True)
        if apply and actions:
            if backup_path is None:
                backup_path = backup(dest)
                print(f"Backup: {backup_path}")
            actions = cleanup.clean(home, dest, dry_run=False)
        for line in actions or ["nothing to clean"]:
            print(f"LEGACY GATES {line}")
    result = {
        "copies": len(changes),
        "deletions": len(deletions),
        "preserved": len(preserved),
        "links": len(links),
        "backup": backup_path,
        "cleanup": actions,
    }
    print(
        f"{'APPLIED' if apply else 'DRY RUN'}: {len(changes)} copies, {len(deletions)} removed files ({'pruned' if prune else 'kept'}), {len(preserved)} preserved, {len(links)} dangling links"
    )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", type=Path, dest="archive")
    source.add_argument("--src", type=Path)
    parser.add_argument("--dest", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Default: report without changes")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--prune", action="store_true", help="Delete unchanged files removed from the previous manifest"
    )
    parser.add_argument(
        "--prune-links", action="store_true", help="Remove dangling user skill links into this destination"
    )
    parser.add_argument(
        "--home", type=Path, default=Path.home(), help="User folder for links and legacy gate cleanup"
    )
    args = parser.parse_args(argv)
    try:
        if args.src and (
            args.src.resolve().is_relative_to(args.dest.resolve())
            or args.dest.resolve().is_relative_to(args.src.resolve())
        ):
            raise ValueError("source and destination directories must not overlap")
        entries = load_drop(source=args.src, archive=args.archive)
        apply_drop(
            entries,
            args.dest,
            apply=args.apply,
            prune=args.prune,
            prune_links=args.prune_links,
            home=args.home,
        )
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
