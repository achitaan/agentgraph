"""Install or update the toolkit in an OpenCode config folder without losing the user's own changes.

`install.sh` is the front door; it calls this file. Standard library only, no relative imports, so it also
runs with an older python3 than the rest of the package.

What it keeps: a manifest (`<config folder>/.toolkit-install.json`) records the hash of every file the last
install wrote, and the toolkit's last `oh-my-opencode-slim.json` (the merge base). On the next run:

- a file the user never changed is replaced by the toolkit's new version (the old one is backed up);
- a file the user changed is kept, and when the toolkit's version moved on, it is written beside it as
  `<name>.toolkit-new` (listed at the end);
- a file the user created is never touched or deleted;
- `oh-my-opencode-slim.json` is merged three ways (base, toolkit, user): the toolkit's new agents and keys
  arrive, what the user changed or removed stays;
- a file that differs and has no manifest entry (a first run over an older install) counts as changed.

Backups go to `<config folder>/backups/<time>-pre-toolkit.*`; `list-backups` and `restore` read them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

MANIFEST = ".toolkit-install.json"
NEW_SUFFIX = ".toolkit-new"
PRESET_FILE = "oh-my-opencode-slim.json"
RESTORABLE = ("-pre-toolkit.", "-pre-restore.")
SKIP_NAMES = {".DS_Store", "__pycache__"}
_MISSING = object()


class InstallError(Exception):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def no_links(path: Path) -> None:
    """Refuse a path with a symlink in it (the same rule as commands/.toolkit.sh)."""
    for part in (path, *path.parents):
        if str(part) in ("", ".", "/"):
            break
        if part.is_symlink():
            raise InstallError(f"refusing symlink: {part}")


def merge3(base, theirs, mine):
    """Three-way merge of parsed JSON: `theirs` is the toolkit's new value, `mine` the user's, `base` what the
    toolkit wrote last time (`_MISSING` when unknown). The user's changes and deletions win; a value the user
    never changed follows the toolkit; keys only the toolkit has (and had not before) are added."""
    if mine == theirs:
        return mine
    if isinstance(mine, dict) and isinstance(theirs, dict):
        old = base if isinstance(base, dict) else {}
        out = {}
        for key, value in mine.items():
            out[key] = merge3(old.get(key, _MISSING), theirs[key], value) if key in theirs else value
        for key, value in theirs.items():
            if key not in mine and key not in old:
                out[key] = value
        return out
    if base is not _MISSING and mine == base:
        return theirs
    return mine


def toolkit_files(source: Path, commands: list[str]) -> list[tuple[str, Path]]:
    """(path relative to the config folder, toolkit file) for everything but the preset file."""
    found = []
    for name in commands:
        path = source / "commands" / f"{name}.md"
        if not path.is_file():
            raise InstallError(f"missing toolkit command: {name}")
        found.append((f"commands/{name}.md", path))
    skills = source / "skills"
    for skill in sorted(skills.iterdir()) if skills.is_dir() else []:
        if skill.is_dir() and skill.name != "licenses":
            found += _tree(skill, f"skills/{skill.name}")
    found += _tree(source / "config" / "oh-my-opencode-slim", "oh-my-opencode-slim")
    found.append(("AGENTS.md", source / "config" / "AGENTS.md"))
    return found


def _tree(folder: Path, rel: str) -> list[tuple[str, Path]]:
    found = []
    for path in sorted(folder.rglob("*")) if folder.is_dir() else []:
        parts = path.relative_to(folder).parts
        if path.is_file() and not path.is_symlink() and not SKIP_NAMES.intersection(parts):
            found.append((f"{rel}/{'/'.join(parts)}", path))
    return found


class Installer:
    def __init__(self, source: Path, target: Path, commands: list[str], dry: bool = False, out=print):
        self.source, self.target, self.commands, self.dry, self.out = source, target, commands, dry, out
        self.backup_root = target / "backups"
        self.backup: Path | None = None
        self.changed = 0
        self.beside: list[Path] = []
        self.added: set[str] = set()
        self.manifest = self._read_manifest()
        self.files: dict = dict(self.manifest.get("files", {}))
        self.base = self.manifest.get("merge_base", _MISSING)

    # -- manifest
    def _read_manifest(self) -> dict:
        path = self.target / MANIFEST
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("files", {}), dict):
                return data
        except (OSError, ValueError):
            pass
        self.out(f"note    {path} could not be read; every differing file counts as changed by you")
        return {}

    def _write_manifest(self, base) -> None:
        data = {"version": 1, "files": dict(sorted(self.files.items()))}
        if base is not _MISSING:
            data["merge_base"] = base
        if data == self.manifest:
            return
        path = self.target / MANIFEST
        no_links(path)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # -- writing
    def _backup(self, rel: str, dst: Path) -> None:
        if self.dry:
            return
        if self.backup is None:
            no_links(self.backup_root)
            self.backup_root.mkdir(parents=True, exist_ok=True)
            self.backup = Path(tempfile.mkdtemp(prefix=time.strftime("%Y-%m-%d-%H%M%S") + "-pre-toolkit.",
                                                dir=self.backup_root))
        keep = self.backup / rel
        no_links(keep)
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, keep)

    def _copy(self, src: Path, dst: Path) -> None:
        if self.dry:
            return
        no_links(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def _say(self, verb: str, dst: Path) -> None:
        self.out(f"{verb:<7} {dst}")
        self.changed += 1

    def _add(self, rel: str, dst: Path) -> None:
        """One line per skill, prompt folder or file, as a first install always reported."""
        parts = rel.split("/")
        unit = "/".join(parts[:2] if parts[0] == "skills" else parts[:1])
        if unit not in self.added:
            self.added.add(unit)
            self._say("add", self.target / unit)

    def _beside(self, src: Path, dst: Path) -> None:
        new = dst.with_name(dst.name + NEW_SUFFIX)
        self.beside.append(new)
        if new.is_file() and digest(new) == digest(src):
            return
        self._say("new", new)
        self._copy(src, new)

    def _drop_stale(self, dst: Path, sh: str) -> None:
        """The toolkit's copy beside a file that now matches it is no longer needed."""
        new = dst.with_name(dst.name + NEW_SUFFIX)
        if new.is_file() and not new.is_symlink() and digest(new) == sh and not self.dry:
            new.unlink()

    # -- one file
    def file(self, rel: str, src: Path) -> None:
        dst = self.target / rel
        no_links(dst)
        sh = digest(src)
        old = self.files.get(rel)
        if not dst.exists():
            if old is None:
                self._add(rel, dst)
                self._copy(src, dst)
                self.files[rel] = sh
            elif old != sh:  # you removed it and the toolkit has changed it since
                self._beside(src, dst)
            return
        if not dst.is_file():
            raise InstallError(f"refusing non-regular file: {dst}")
        have = digest(dst)
        if have == sh:
            self.files[rel] = sh
            self._drop_stale(dst, sh)
        elif old is not None and have == old:  # never changed by you
            self._say("update", dst)
            self._backup(rel, dst)
            self._copy(src, dst)
            self.files[rel] = sh
        elif old is not None and sh == old:  # you changed it, the toolkit did not
            return
        else:  # you changed it and the toolkit moved on, or it is unknown which
            self._beside(src, dst)

    # -- the preset file
    def preset_file(self) -> object:
        src = self.source / "config" / PRESET_FILE
        dst = self.target / PRESET_FILE
        no_links(dst)
        sh = digest(src)
        try:
            theirs = json.loads(src.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise InstallError(f"toolkit {PRESET_FILE} is not valid JSON: {exc}")
        if not dst.exists():
            self._say("add", dst)
            self._copy(src, dst)
            self.files[PRESET_FILE] = sh
            return theirs
        if not dst.is_file():
            raise InstallError(f"refusing non-regular file: {dst}")
        try:
            mine = json.loads(dst.read_text(encoding="utf-8"))
        except ValueError:
            mine = _MISSING
        if mine is _MISSING or not isinstance(mine, dict) or not isinstance(theirs, dict):
            if digest(dst) != sh:
                self.out(f"note    {dst} is not a JSON object; it was left alone")
                self._beside(src, dst)
            return self.base
        merged = merge3(self.base, theirs, mine)
        if merged != mine:
            self._say("merge", dst)
            self._backup(PRESET_FILE, dst)
            if not self.dry:
                dst.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.files[PRESET_FILE] = sh
        return theirs

    def run(self) -> int:
        no_links(self.target)
        items = toolkit_files(self.source, self.commands)
        if not (self.source / "config" / PRESET_FILE).is_file():
            raise InstallError(f"missing toolkit file: config/{PRESET_FILE}")
        for rel, src in items:
            if rel != "AGENTS.md":
                self.file(rel, src)
        base = self.preset_file()
        self.file("AGENTS.md", self.source / "config" / "AGENTS.md")
        if self.changed == 0:
            self.out("Nothing to change.")
        elif self.dry:
            self.out(f"{self.changed} item(s) would change. Nothing was written.")
        else:
            self.out(f"{self.changed} item(s) installed.")
        if self.beside:
            self.out("You changed these files, so they were kept. The toolkit's newer version is beside each one;")
            self.out("merge what you want, then delete the .toolkit-new file:")
            for path in self.beside:
                self.out(f"  {path}")
        if not self.dry:
            self.target.mkdir(parents=True, exist_ok=True)
            self._write_manifest(base)
            if self.backup is not None:
                self.out(f"Replaced files are in {self.backup}. Undo with: sh install.sh --restore {self.backup.name}")
            if self.changed:
                self.out("See config/PLUGINS.md to turn the visualizer on, then reload OpenCode.")
        return 0


# ---------------------------------------------------------------- backups


def backups(target: Path) -> list[Path]:
    root = target / "backups"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                  and any(mark in p.name for mark in RESTORABLE))


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


def list_backups(target: Path, out=print) -> int:
    found = backups(target)
    if not found:
        out("No toolkit backups.")
        return 0
    for folder in found:
        out(f"{folder.name}  ({len(_files(folder))} file(s))")
    out("Put one back with: sh install.sh --restore <name>   (add --dry-run to look first)")
    return 0


def restore(target: Path, name: str, dry: bool = False, out=print) -> int:
    root = target / "backups"
    folder = Path(name) if "/" in name else root / name
    if folder.is_symlink():
        raise InstallError(f"refusing symlink: {folder}")
    folder = folder.resolve() if folder.exists() else folder
    if not folder.is_dir() or folder.parent != root.resolve() or not any(m in folder.name for m in RESTORABLE):
        raise InstallError(f"not a toolkit backup of {root}: {name} (see --list-backups)")
    no_links(target)
    saved: Path | None = None
    count = 0
    for path in _files(folder):
        if path.is_symlink():
            raise InstallError(f"refusing symlink in backup: {path}")
        rel = path.relative_to(folder)
        dst = target / rel
        no_links(dst)
        if dst.exists() and (not dst.is_file() or digest(dst) == digest(path)):
            if not dst.is_file():
                raise InstallError(f"refusing non-regular file: {dst}")
            continue
        out(f"restore {dst}")
        count += 1
        if dry:
            continue
        if dst.exists():
            if saved is None:
                saved = Path(tempfile.mkdtemp(prefix=time.strftime("%Y-%m-%d-%H%M%S") + "-pre-restore.", dir=root))
            keep = saved / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, keep)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
    if count == 0:
        out("Nothing to restore: every file already matches the backup.")
    elif dry:
        out(f"{count} file(s) would be restored. Nothing was written.")
    else:
        out(f"{count} file(s) restored from {folder.name}.")
        if saved is not None:
            out(f"What they replaced is in {saved}.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="installer", description=__doc__.split("\n")[0])
    parser.add_argument("mode", choices=("install", "list-backups", "restore"))
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--commands", default="", help="comma separated toolkit command names")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup", help="the backup to restore")
    args = parser.parse_args(argv)
    try:
        if args.mode == "list-backups":
            return list_backups(args.target)
        if args.mode == "restore":
            if not args.backup:
                raise InstallError("restore needs a backup name")
            return restore(args.target, args.backup, args.dry_run)
        commands = [name for name in args.commands.split(",") if name]
        return Installer(args.source, args.target, commands, args.dry_run).run()
    except InstallError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
