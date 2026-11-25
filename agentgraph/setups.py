"""The default preset and saved setups: show the preset, switch it, and save, list, restore and remove a named setup,
each change undoable.

**The preset.** The plugin reads one top-level string, `preset`, in `~/.config/opencode/oh-my-opencode-slim.json`
(the name of one entry of its `presets`) when OpenCode starts. `preset switch NAME` changes only that string, in
place: every other byte of the file stays as it was (`skillset._spans` finds the value; the edit is proved to touch
nothing else). It runs under the same folder lock as the skills writer, takes a dated backup (`skillset._backup`) and
saves an undo record; `preset restore RECORD` puts the file back byte for byte, and is refused (stale) when the file
changed since. It refuses an unknown name, the preset already in use, and a switch that would not take effect: the
plugin's precedence is the environment variable `OH_MY_OPENCODE_SLIM_PRESET`, then a project config
(`.opencode/oh-my-opencode-slim.jsonc` or `.json` in the project folder or any folder above it), then the user file.
`preset show` says which of the two overrides, by name only (`env` or `project`). A session already open keeps its
models: the change applies at the next start of OpenCode.

**A saved setup** is a copy, under `<orchestration>/setups/<name>/`, of every file the toolkit's own writers manage
(the names `files/KEY` below are the keys of the manifest):

- `preset`: the plugin's preset file (`skillset.CONFIG`; written by `skills`, `agent` and `preset switch`);
- `map`: `SKILLS.md`, the table of each agent's skills (`skillset.MAP`);
- `agents/NAME.md`: each native agent file in OpenCode's agents folder that carries the toolkit's marker line
  (`agentfiles.is_added`; written by `agent add|text|rename|remove|skills`); a hand-written agent file is never read;
- `structure/GROUP/NAME.md`: the structure folder's files, one level down: each agent's place and description
  (`NN-group/AGENT.md`, `roster.py`), the groups (`teams/ID.md`, `teams.py`) and the drawn paths (`flows/ID.md`,
  `structure.py`); the folder's own README is not one of them, no writer touches it;
- `commands/NAME.md`: each path command in OpenCode's commands folder that carries the toolkit's marker line
  (`flowcommands.is_ours`; written by `flow command`). A path and its command file go together; the other command
  files are never read.

`manifest.json` lists each file with its SHA-256, size and mode, the date it was saved and the preset the file holds.
**It never reads or writes** `opencode.json`, credentials, the prompts under `oh-my-opencode-slim/` or the skill files
(skills are not copied, only which agent has which), and it never prints a file's contents: only names, counts and
hashes. A setup's name is a plain id (`names.py`, a flow id: lowercase letters, digits and `-`); a name that is not
one, such as a path, is refused. A name in use is refused unless `replace`.

`setup restore NAME` first checks every hash (a damaged setup is refused before anything is written), saves the
current state of the same file set as an undo record, then writes each file byte for byte; a managed file that exists
now and is not in the setup is removed (it is in the record), and so is a structure folder that removal emptied and the
setup did not have (the manifest lists the folders it had). The preset file and `SKILLS.md` are only ever replaced,
never removed. `setup undo RECORD` puts the whole set back byte for byte; it is refused (stale) when any file of the
set is neither as the record's change left it nor as it was before. `setup remove NAME` moves the setup into the
backups folder (`<backups>/<date>-setups/`) and is undone the same way; saving over a name keeps the old setup there
too, and a new save is undone by removing it. Undo records of `preset` and `restore` keep the newest 48; those that
hold a removed or replaced setup are never deleted by the toolkit.

Every function takes the folders as parameters (`config`, `map_path`, `agents_dir`, `structure`, `commands_dir`,
`setups`, `backups`; each defaults to the real one), so tests run on scratch copies. A write is staged beside its
target and renamed; a failure part-way puts back what was done.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys

from . import agentfiles, flowcommands, names, osfs, paths, skillset
from .snapshot_json import STRUCTURE_DIR

SETUPS = paths.resolve("setups")  # your personal folder's when there is one (agentgraph.paths)
ENV = "OH_MY_OPENCODE_SLIM_PRESET"
PRESET_MESSAGE = "The default preset applies at the next start of OpenCode; a session already open keeps its models."
SETUP_MESSAGE = ("The preset and skills apply at the next start of OpenCode; agent files apply at once; path commands "
                 "after a reload.")
FILE_LIMIT = 4 * 1024 * 1024  # one file
FILES_LIMIT = 3000            # files in one set
TOTAL_LIMIT = 64 * 1024 * 1024
LIST_LIMIT = 200              # setups listed
KEPT_RECORDS = skillset.MAX_BACKUPS


class SetupError(skillset.SkillsetError):
    """A refused request; the message says why and nothing was changed."""


@dataclass(frozen=True)
class Where:
    config: Path
    map_path: Path
    agents_dir: Path
    structure: Path
    commands_dir: Path
    setups: Path
    backups: Path


def _where(paths: dict) -> Where:
    defaults = {"config": skillset.CONFIG, "map_path": skillset.MAP, "agents_dir": agentfiles.AGENTS_DIR,
                "structure": STRUCTURE_DIR, "commands_dir": flowcommands.COMMANDS_DIR, "setups": SETUPS,
                "backups": skillset.BACKUPS}
    unknown = set(paths) - set(defaults)
    if unknown:
        raise TypeError(f"unknown folder: {sorted(unknown)}")
    return Where(**{key: Path(paths[key] if paths.get(key) is not None else value) for key, value in defaults.items()})


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the managed set -----------------------------------------------------------------------------------------------

_STEM = r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}"
_GROUP = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
_KEY = re.compile(rf"preset|map|agents/{names.AGENT_PATTERN}\.md|structure/{_GROUP}/{_STEM}\.md"
                  rf"|commands/{names.COMMAND_PATTERN}\.md")
_AGENT_FILE = re.compile(names.AGENT_PATTERN + r"\.md")
_COMMAND_FILE = re.compile(names.COMMAND_PATTERN + r"\.md")
_STRUCTURE_FILE = re.compile(_STEM + r"\.md")
_STRUCTURE_GROUP = re.compile(_GROUP)


def _path(w: Where, key: str) -> Path:
    if key == "preset":
        return w.config
    if key == "map":
        return w.map_path
    root, _, rest = key.partition("/")
    return {"agents": w.agents_dir, "structure": w.structure, "commands": w.commands_dir}[root] / rest


def _read_bytes(path: Path, label: str):
    """(bytes, mode) of a regular file read without following a symlink; None when there is no such file."""
    try:
        fd = osfs.open(path, os.O_RDONLY | osfs.O_NOFOLLOW | osfs.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        return None
    except OSError as exc:
        raise SetupError(f"cannot read {label} as a regular file (no symlinks): {exc.strerror}") from exc
    with os.fdopen(fd, "rb") as handle:
        info = osfs.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise SetupError(f"{label} must be a regular file, not a symlink or special file")
        if info.st_size > FILE_LIMIT:
            raise SetupError(f"{label} is too large to copy ({info.st_size} bytes; at most {FILE_LIMIT})")
        return handle.read(FILE_LIMIT + 1), stat.S_IMODE(info.st_mode)


def _entries(folder: Path):
    try:
        return sorted(osfs.listdir(folder))
    except (FileNotFoundError, NotADirectoryError):
        return []


def _is_plain_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _capture(w: Where, scope: str = "set") -> dict:
    """{key: (bytes, mode)} for the managed set (scope "set") or only the preset file (scope "preset")."""
    files = {}
    found = _read_bytes(w.config, "the preset file")
    if found is not None:
        files["preset"] = found
    if scope == "preset":
        return files
    found = _read_bytes(w.map_path, "SKILLS.md")
    if found is not None:
        files["map"] = found
    for entry in _entries(w.agents_dir):
        if _AGENT_FILE.fullmatch(entry):
            found = _read_bytes(w.agents_dir / entry, f"agents/{entry}")
            if found is not None and agentfiles.is_added(found[0]):
                files["agents/" + entry] = found
    for group in _entries(w.structure):
        folder = w.structure / group
        if not _STRUCTURE_GROUP.fullmatch(group) or not _is_plain_dir(folder):
            continue
        for entry in _entries(folder):
            if _STRUCTURE_FILE.fullmatch(entry):
                try:
                    if not stat.S_ISREG(os.lstat(folder / entry).st_mode):
                        continue
                except OSError:
                    continue
                found = _read_bytes(folder / entry, f"structure/{group}/{entry}")
                if found is not None:
                    files[f"structure/{group}/{entry}"] = found
    for entry in _entries(w.commands_dir):
        if _COMMAND_FILE.fullmatch(entry):
            found = _read_bytes(w.commands_dir / entry, f"commands/{entry}")
            if found is not None and flowcommands.is_ours(found[0]):
                files["commands/" + entry] = found
    if len(files) > FILES_LIMIT or sum(len(data) for data, _ in files.values()) > TOTAL_LIMIT:
        raise SetupError(f"the set is too large to copy (over {FILES_LIMIT} files or {TOTAL_LIMIT} bytes)")
    return files


def _set_rev(files: dict) -> str:
    return agentfiles.rev(*(f"{key}\0{_sha(data)}" for key, (data, _) in sorted(files.items())))


def _meta(files: dict) -> list:
    return [{"key": key, "sha256": _sha(data), "size": len(data), "mode": mode}
            for key, (data, mode) in sorted(files.items())]


def _locks(w: Where, *, set_too: bool = True) -> list:
    """The folders a change to the set holds locked, as the writers lock them (the ones that exist)."""
    candidates = [w.config]
    if set_too:
        candidates += [w.map_path, w.agents_dir / "x", w.structure / "x", w.commands_dir / "x"]
    return [path for path in candidates if path.parent.is_dir()]


# --- writing bytes ------------------------------------------------------------------------------------------------


def _ensure_parent(path: Path, created: list) -> None:
    missing = []
    folder = path.parent
    while not os.path.lexists(folder):
        missing.append(folder)
        folder = folder.parent
    if not _is_plain_dir(folder):
        raise SetupError(f"{folder.name} is not a plain folder (a symlink or a file); nothing was written")
    for folder in reversed(missing):
        osfs.mkdir(folder, 0o755)
        created.append(folder)


def _fsync(folder: Path) -> None:
    fd = osfs.open(folder, os.O_RDONLY | osfs.O_DIRECTORY)
    try:
        osfs.fsync(fd)
    finally:
        osfs.close(fd)


def _apply(w: Where, plan: list, keep: set | None = None) -> None:
    """Write `plan` ([(key, old, new)], old/new each (bytes, mode) or None; old != new) all or nothing: every new
    file is staged beside its target, each target is checked to be still `old`, then each is renamed into place (a
    deletion is an unlink). Any failure puts back what was done. A structure folder a deletion emptied goes unless
    it is in `keep` (the folders there were before; None: none goes)."""
    created: list = []
    done: list = []
    with ExitStack() as stack:
        try:
            items = []
            for key, old, new in plan:
                path = _path(w, key)
                if new is not None:
                    _ensure_parent(path, created)
                    new_staged = stack.enter_context(skillset._staged(path, new[0], new[1]))
                else:
                    new_staged = None
                old_staged = stack.enter_context(skillset._staged(path, old[0], old[1])) if old is not None else None
                items.append((key, path, old, new_staged, old_staged, new))
            for key, path, old, *_ in items:  # nothing was replaced yet: still what was read?
                current = _read_bytes(path, key)
                if (current[0] if current else None) != (old[0] if old else None):
                    raise SetupError(f"{key} changed while this was being made; nothing was written. Try again.")
            try:
                for item in items:
                    key, path, old, new_staged, old_staged, new = item
                    if new_staged is not None:
                        osfs.replace(new_staged, path)
                    else:
                        osfs.unlink(path)
                    done.append(item)
                for folder in {item[1].parent for item in items}:
                    _fsync(folder)
            except BaseException:
                for key, path, old, new_staged, old_staged, new in reversed(done):
                    if old_staged is not None:
                        osfs.replace(old_staged, path)
                    else:
                        osfs.unlink(path)
                for folder in {item[1].parent for item in items if item[1].parent.is_dir()}:
                    _fsync(folder)
                raise
        except BaseException:
            for folder in reversed(created):
                try:
                    osfs.rmdir(folder)
                except OSError:
                    pass
            raise
    for key, _, new in plan:  # a group folder this emptied goes, if the state being put back had none
        if new is None and key.startswith("structure/") and keep is not None and key.split("/")[1] not in keep:
            try:
                osfs.rmdir(_path(w, key).parent)
            except OSError:
                pass


# --- records ------------------------------------------------------------------------------------------------------

_RECORD = re.compile(rf"(\d{{4}}-\d{{2}}-\d{{2}})-setups/(?:(preset)-[0-9a-f]{{12}}"
                     rf"|(restore|remove|replace)-({names.FLOW_ID_PATTERN})-[0-9a-f]{{12}})/record\.json")


def _mkdir(path: Path) -> None:
    try:
        osfs.mkdir(path, 0o700)
    except FileExistsError:
        pass
    if not _is_plain_dir(path):
        raise SetupError(f"{path} is not a plain folder")


def _record_folder(backups: Path, kind: str, name: str | None) -> Path:
    Path(backups).mkdir(parents=True, exist_ok=True)
    day = Path(backups) / f"{date.today().isoformat()}-setups"
    _mkdir(day)
    token = secrets.token_hex(6)
    folder = day / (f"preset-{token}" if kind == "preset" else f"{kind}-{name}-{token}")
    osfs.mkdir(folder, 0o700)
    return folder


def _write_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = osfs.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | osfs.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        osfs.fsync(stream.fileno())


def _write_tree(base: Path, files: dict) -> None:
    """Write `files` ({key: (bytes, mode)}) under `base/files/KEY`, private to the user."""
    for key, (data, _) in files.items():
        target = base / "files" / key
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_file(target, data)


def _dirs(w: Where) -> list:
    """The structure folder's group folders there are now (a restore puts back files, and folders only as needed)."""
    return [group for group in _entries(w.structure) if _STRUCTURE_GROUP.fullmatch(group)
            and _is_plain_dir(w.structure / group)]


def _save_file_record(w: Where, kind: str, name: str | None, scope: str, before: dict, after: dict) -> str:
    """An undo record of a change of the files: `before` ({key: (bytes, mode)}) is copied, `after` is {key: sha or
    None}, the state the change leaves. Returns the record's path."""
    folder = _record_folder(w.backups, kind, name)
    try:
        _write_tree(folder, before)
        record = {"version": 1, "op": kind, "name": name, "scope": scope, "files": _meta(before), "after": after,
                  "dirs": _dirs(w) if scope == "set" else [], "created": _now()}
        _write_file(folder / "record.json", (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return str(folder / "record.json")


def _drop_record(record: str) -> None:
    folder = Path(record).parent
    shutil.rmtree(folder, ignore_errors=True)
    try:
        folder.parent.rmdir()  # the day's folder, when that was the only record
    except OSError:
        pass


def _prune(backups: Path) -> None:
    """Keep the newest KEPT_RECORDS undo records of preset and restore changes (tidying: never raises). The records
    that hold a setup are the user's only copy of it and stay."""
    try:
        found = []
        for day in Path(backups).iterdir():
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-setups", day.name) or not _is_plain_dir(day):
                continue
            for folder in day.iterdir():
                if re.fullmatch(r"(?:preset|restore-[a-z0-9-]+)-[0-9a-f]{12}", folder.name) and _is_plain_dir(folder):
                    found.append((day.name, folder.lstat().st_mtime_ns, folder))
        found.sort(key=lambda item: item[:2])
        for _, _, folder in found[:max(len(found) - KEPT_RECORDS, 0)]:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                folder.parent.rmdir()
            except OSError:
                pass
    except OSError:
        pass


def _newest_record(backups: Path):
    """The path of the newest undo record in the backups folder, or None (a listing only: nothing is read)."""
    try:
        days = sorted((day for day in Path(backups).iterdir()
                       if re.fullmatch(r"\d{4}-\d{2}-\d{2}-setups", day.name) and _is_plain_dir(day)), reverse=True)
        for day in days:
            best = None
            for folder in day.iterdir():
                record = folder / "record.json"
                if (re.fullmatch(r"(?:preset|restore|remove|replace)(?:-[a-z0-9-]+)?-[0-9a-f]{12}", folder.name)
                        and record.is_file() and not record.is_symlink()):
                    stamp = record.stat().st_mtime_ns
                    if best is None or stamp > best[0]:
                        best = (stamp, record)
            if best is not None:
                return str(best[1])
    except OSError:
        pass
    return None


def _load_record(record, backups: Path):
    """(kind, name, data, folder) of an undo record inside the backups folder; refused otherwise."""
    if not isinstance(record, str) or not record or "\0" in record or len(record) > 4096:
        raise SetupError("invalid undo record: expected the record a change returned")
    root = Path(os.path.abspath(backups))
    path = Path(os.path.abspath(record))
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise SetupError("invalid undo record: it is not one of the backups folder's undo records") from None
    found = _RECORD.fullmatch(relative)
    if not found:
        raise SetupError("invalid undo record: it is not one of the backups folder's undo records")
    current = root
    for part in path.relative_to(root).parts[:-1]:
        current /= part
        if not _is_plain_dir(current):
            raise SetupError("invalid undo record: there is no such record")
    try:
        fd = osfs.open(path, os.O_RDONLY | osfs.O_NOFOLLOW | osfs.O_NONBLOCK)
    except OSError:
        raise SetupError("invalid undo record: there is no such record") from None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(osfs.fstat(stream.fileno()).st_mode):
            raise SetupError("invalid undo record: not a regular file")
        raw = stream.read(8 * 1024 * 1024 + 1)
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=skillset._unique)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SetupError(f"invalid undo record: {exc}") from exc
    kind = found.group(2) or found.group(3)
    name = found.group(4)
    if (not isinstance(data, dict) or data.get("version") != 1 or data.get("op") != kind
            or data.get("name") != name):
        raise SetupError("invalid undo record: it does not match its file name")
    return kind, name, data, path.parent


def _blob(folder: Path, entry: dict, what: str) -> bytes:
    """The bytes of one saved file, checked against its hash and size; refused when it does not match."""
    key = entry.get("key") if isinstance(entry, dict) else None
    if (not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(entry.get("sha256"), str)
            or not isinstance(entry.get("size"), int) or isinstance(entry.get("size"), bool)
            or not isinstance(entry.get("mode"), int) or isinstance(entry.get("mode"), bool)
            or not 0 <= entry["mode"] <= 0o777):
        raise SetupError(f"{what} is damaged: a file entry is malformed")
    found = _read_bytes(folder / "files" / key, f"{what}'s {key}")
    if found is None:
        raise SetupError(f"{what} is damaged: {key} is missing")
    if len(found[0]) != entry["size"] or _sha(found[0]) != entry["sha256"]:
        raise SetupError(f"{what} is damaged: {key} does not match its hash")
    return found[0]


def _record_files(folder: Path, data: dict) -> dict:
    entries = data.get("files")
    if not isinstance(entries, list):
        raise SetupError("invalid undo record: it lists no files")
    files = {}
    for entry in entries:
        blob = _blob(folder, entry, "the undo record")
        if entry["key"] in files:
            raise SetupError("invalid undo record: a file is listed twice")
        files[entry["key"]] = (blob, entry["mode"])
    return files


def _check_plan_targets(w: Where, current: dict, target: dict) -> None:
    """A file the set would gain must not land on one that is there and is not the toolkit's own."""
    for key in target:
        if key not in current and os.path.lexists(_path(w, key)):
            raise SetupError(f"{key} exists and is not a file this toolkit manages; nothing was changed")


def _change(w: Where, current: dict, target: dict) -> list:
    plan = []
    for key in sorted(set(current) | set(target)):
        old, new = current.get(key), target.get(key)
        if new is None and key in ("preset", "map"):
            continue  # the preset file and SKILLS.md are replaced, never removed
        if (old[0] if old else None) != (new[0] if new else None):
            plan.append((key, old, new))
    return plan


def _undo_files(w: Where, record: str, kind: str, name, data: dict, folder: Path) -> dict:
    scope = data.get("scope")
    after = data.get("after")
    dirs = data.get("dirs")
    if scope not in ("preset", "set") or not isinstance(dirs, list) or not all(
            isinstance(group, str) and _STRUCTURE_GROUP.fullmatch(group) for group in dirs) or not isinstance(
            after, dict) or not all(
            isinstance(key, str) and _KEY.fullmatch(key) and (sha is None or isinstance(sha, str))
            for key, sha in after.items()):
        raise SetupError("invalid undo record: it is malformed")
    before = _record_files(folder, data)
    with skillset._locked(*_locks(w, set_too=scope == "set")):
        current = _capture(w, scope)
        for key in set(current) | set(before) | set(after):
            have = _sha(current[key][0]) if key in current else None
            if have not in (_sha(before[key][0]) if key in before else None, after.get(key)):
                raise agentfiles.StaleError("the files this record covers", _set_rev(current))
        _check_plan_targets(w, current, before)
        plan = _change(w, current, before)
        if not plan:
            return {"name": name, "changed": False, "message": "Already as it was; nothing to undo."}
        redo_after = {key: _sha(before[key][0]) if key in before else None for key in set(current) | set(before)}
        redo = _save_file_record(w, kind, name, scope, current, redo_after)
        try:
            _apply(w, plan, keep=set(dirs))
        except BaseException:
            _drop_record(redo)
            raise
    _prune(w.backups)
    return {"name": name, "changed": True, "written": sum(1 for _, _, new in plan if new is not None),
            "removed": sum(1 for _, _, new in plan if new is None), "record": redo,
            "message": PRESET_MESSAGE if kind == "preset" else SETUP_MESSAGE}


# --- the override of the preset ------------------------------------------------------------------------------------


def _jsonc(text: str) -> str:
    """JSON from JSONC: comments and trailing commas outside strings dropped."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _project_preset(project, env) -> str | None:
    """The preset a project config sets (the deepest folder wins; the plugin's own search: each folder from the
    project up to the root, `.opencode/oh-my-opencode-slim.jsonc`, else `.json`), or None."""
    if (env.get("OPENCODE_DISABLE_PROJECT_CONFIG") or "").lower() in ("true", "1"):
        return None
    folder = Path(os.path.abspath(project if project is not None else Path.cwd()))
    chain = [folder, *folder.parents]
    value = None
    for base in reversed(chain):
        for suffix in (".jsonc", ".json"):
            path = base / ".opencode" / f"oh-my-opencode-slim{suffix}"
            if not path.is_file():
                continue
            try:
                found = _read_bytes(path, "a project config")
                layer = json.loads(_jsonc(found[0].decode("utf-8"))) if found else None
            except (SetupError, ValueError, UnicodeDecodeError, RecursionError):
                layer = None
            if isinstance(layer, dict) and isinstance(layer.get("preset"), str) and layer["preset"].strip():
                value = layer["preset"].strip()
            break
    return value


def _override(project, env):
    """(kind, preset name) of what takes precedence over the user file: ("env", name), ("project", name) or
    (None, None). Only the name of the preset it sets is kept."""
    env = os.environ if env is None else env
    value = (env.get(ENV) or "").strip()
    if value:
        return "env", value
    value = _project_preset(project, env)
    return ("project", value) if value else (None, None)


def _config(w: Where):
    text, mode = skillset._read(w.config, "config")
    return text, mode, skillset._parse(text)


def show_preset(*, project=None, env=None, **paths) -> dict:
    """The default preset, the preset names, and what overrides the user file (`override`: "env", "project" or None;
    `effective`: the preset OpenCode will use). Reads only."""
    w = _where(paths)
    _, _, obj = _config(w)
    kind, value = _override(project, env)
    return {"preset": obj["preset"], "presets": list(obj["presets"]), "override": kind,
            "effective": value or obj["preset"]}


def _preset_edit(text: str, old: dict, name: str):
    """`text` with the top-level `preset` string set to `name` and nothing else changed, proved byte for byte."""
    spans, members = skillset._spans(text)
    start, end = spans[("preset",)]
    key_start = dict(members[("presets",)])[name]
    token = skillset._TOKEN.match(text, key_start).group()  # the name as the file itself writes it
    new_text = skillset._replace(text, [(start, end, token)])
    new_obj = skillset._parse(new_text)
    new_spans, _ = skillset._spans(new_text)
    if new_obj != {**old, "preset": name} or skillset._outside(text, [(start, end)]) != skillset._outside(
            new_text, [new_spans[("preset",)]]):
        raise SetupError("preset edit proof failed: unrelated data or bytes changed")
    return new_text, new_obj


def switch_preset(name, *, project=None, env=None, **paths) -> dict:
    """Make `name` the default preset: change only the `preset` string, in place, with a dated backup and an undo
    record (`record`; `preset restore`). Refused for an unknown name, the one already in use, and when the
    environment variable or a project config sets another preset (the change would not take effect)."""
    w = _where(paths)
    if not isinstance(name, str) or not name or len(name) > 100 or name.startswith("-"):
        raise SetupError("invalid preset name: a preset is named by a non-empty text, 100 characters at most, "
                         "with no leading '-'")
    with skillset._locked(w.config) as dirs:
        text, mode, obj = _config(w)
        if name not in obj["presets"]:
            raise SetupError(f"unknown preset {name!r}; the presets are: {', '.join(obj['presets'])}")
        if name == obj["preset"]:
            raise SetupError(f"{name!r} is already the default preset; nothing was changed")
        kind, value = _override(project, env)
        if kind == "env" and value != name:
            raise SetupError(f"the environment variable {ENV} sets another preset and takes precedence, so the "
                             f"change to {name!r} would not take effect; unset it first")
        if kind == "project" and value != name:
            raise SetupError("a project config (.opencode/oh-my-opencode-slim) sets another preset and takes "
                             f"precedence, so the change to {name!r} would not take effect; change it there")
        new_text, _ = _preset_edit(text, obj, name)
        holder = []

        def prepare():
            backup = skillset._backup(w.config, w.backups, text, mode, "preset-switch")
            holder.append(_save_file_record(w, "preset", None, "preset", {"preset": (text.encode("utf-8"), mode)},
                                            {"preset": _sha(new_text.encode("utf-8"))}))
            return backup

        try:
            backup = skillset._commit([(w.config, text, new_text, mode)], dirs, prepare)
        except BaseException:
            for record in holder:
                _drop_record(record)
            raise
    _prune(w.backups)
    return {"preset": name, "was": obj["preset"], "changed": True, "backup": backup["backup"],
            "previous": backup["previous"], "record": holder[0], "message": PRESET_MESSAGE}


def restore_preset(record, **paths) -> dict:
    """Undo a preset change from its record: the file goes back byte for byte; refused (stale) when it changed
    since."""
    return undo(record, _only="preset", **paths)


# --- saved setups -------------------------------------------------------------------------------------------------


def _setup_name(name) -> str:
    if not names.valid("flow id", name):
        raise SetupError("invalid setup name: use a lowercase letter or digit, then lowercase letters, digits or "
                         "'-' (80 characters at most)")
    return name


def _setup_root(w: Where) -> Path:
    w.setups.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not _is_plain_dir(w.setups):
        raise SetupError("the setups folder is not a plain folder")
    return w.setups


def _manifest(folder: Path):
    """(manifest, raw bytes) of the setup in `folder`, or None when it has none; refused when it is unreadable."""
    found = _read_bytes(folder / "manifest.json", "the manifest")
    if found is None:
        return None
    try:
        manifest = json.loads(found[0].decode("utf-8"), object_pairs_hook=skillset._unique)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SetupError(f"the manifest is damaged: {exc}") from exc
    if (not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("files"), list)
            or not isinstance(manifest.get("saved"), str) or not isinstance(manifest.get("name"), str)
            or not isinstance(manifest.get("preset"), str) or not isinstance(manifest.get("dirs"), list)
            or not all(isinstance(group, str) and _STRUCTURE_GROUP.fullmatch(group) for group in manifest["dirs"])):
        raise SetupError("the manifest is damaged: it is malformed")
    return manifest, found[0]


def _manifest_rev(folder: Path):
    if not os.path.lexists(folder):
        return None
    try:
        found = _manifest(folder)
    except SetupError:
        return agentfiles.rev("damaged")
    return agentfiles.rev(found[1]) if found else agentfiles.rev("damaged")


def _verified(folder: Path, name: str):
    """(manifest, rev, {key: (bytes, mode)}) of a setup after every hash is checked; a damaged setup is refused."""
    what = f"setup {name!r}"
    if not _is_plain_dir(folder):
        raise SetupError(f"there is no saved setup {name!r}")
    found = _manifest(folder)
    if found is None:
        raise SetupError(f"{what} is damaged: it has no manifest")
    manifest, raw = found
    if manifest["name"] != name:
        raise SetupError(f"{what} is damaged: its manifest names another setup")
    files = {}
    for entry in manifest["files"]:
        blob = _blob(folder, entry, what)
        if entry["key"] in files:
            raise SetupError(f"{what} is damaged: {entry['key']} is listed twice")
        files[entry["key"]] = (blob, entry["mode"])
    if "preset" not in files or len(files) > FILES_LIMIT:
        raise SetupError(f"{what} is damaged: it holds no preset file or too many files")
    listed = set(files)
    tree = folder / "files"
    for base, folders, entries in os.walk(tree, followlinks=False):
        for entry in entries:
            relative = (Path(base) / entry).relative_to(tree).as_posix()
            if relative not in listed:
                raise SetupError(f"{what} is damaged: {relative} is not in its manifest")
    try:
        held = skillset._parse(files["preset"][0].decode("utf-8"))["preset"]
    except (UnicodeDecodeError, skillset.SkillsetError) as exc:
        raise SetupError(f"{what} is damaged: its preset file is not usable ({exc})") from exc
    if held != manifest["preset"]:
        raise SetupError(f"{what} is damaged: its manifest names another preset")
    return manifest, agentfiles.rev(raw), files


def _move_dir(source: Path, target: Path) -> None:
    try:
        osfs.rename(source, target)
    except OSError:
        shutil.copytree(source, target)
        shutil.rmtree(source)


def _swap(w: Where, name: str, incoming: Path | None) -> str:
    """Under the setups lock: move the setup now named `name` (if any) into a new undo record, and move `incoming`
    (if any) into its place. Returns the record's path; the record undoes exactly this."""
    root = _setup_root(w)
    current = root / name
    held = os.path.lexists(current)
    after = _manifest_rev(incoming) if incoming is not None else None
    kind = "replace" if incoming is not None else "remove"
    folder = _record_folder(w.backups, kind, name)
    moved = False
    try:
        if held:
            _move_dir(current, folder / "setup")
            moved = True
        if incoming is not None:
            _move_dir(incoming, current)
        record = {"version": 1, "op": kind, "name": name, "held": held, "after": after, "created": _now()}
        _write_file(folder / "record.json", (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    except BaseException:
        if moved and not os.path.lexists(current):
            _move_dir(folder / "setup", current)
        elif moved and incoming is not None and not os.path.lexists(incoming):
            _move_dir(current, incoming)
            _move_dir(folder / "setup", current)
        if not os.path.lexists(folder / "setup"):
            shutil.rmtree(folder, ignore_errors=True)
        raise
    return str(folder / "record.json")


def _undo_swap(w: Where, name: str, data: dict, folder: Path) -> dict:
    held, after = data.get("held"), data.get("after")
    if not isinstance(held, bool) or not (after is None or isinstance(after, str)):
        raise SetupError("invalid undo record: it is malformed")
    root = _setup_root(w)
    with skillset._locked(root / "x"):
        current = _manifest_rev(root / name)
        if current != after:
            raise agentfiles.StaleError(f"setup {name!r}", current)
        incoming = None
        if held:
            _verified(folder / "setup", name)  # a damaged setup is refused before anything moves
            incoming = folder / "setup"
        record = _swap(w, name, incoming)
        if held:
            shutil.rmtree(folder, ignore_errors=True)
            try:
                folder.parent.rmdir()
            except OSError:
                pass
    return {"name": name, "changed": True, "record": record,
            "message": "The setup is back." if held else "The setup is removed again."}


def undo(record, *, _only=None, **paths) -> dict:
    """Undo a change from its record (`setup restore`, `remove`, a save over a name, `preset switch`): everything it
    changed goes back byte for byte. The answer has a record that undoes this in turn. Refused (stale) when
    anything it changed was changed since."""
    w = _where(paths)
    kind, name, data, folder = _load_record(record, w.backups)
    if _only is not None and kind != _only:
        raise SetupError("that is not a preset change's record; use `setup undo` for it")
    if kind in ("preset", "restore"):
        result = _undo_files(w, record, kind, name, data, folder)
        if kind == "preset" and result.get("changed"):
            try:
                result["preset"] = _config(w)[2]["preset"]
            except (SetupError, OSError):
                pass
        return result
    return _undo_swap(w, name, data, folder)


def save(name, *, replace=False, expect=None, **paths) -> dict:
    """Save the managed set (see the module's list) as the setup `name`. A name in use is refused unless `replace`
    (the old setup is kept in the backups folder); `expect`: the rev of the setup being replaced. The answer has the
    `record` that undoes the save."""
    w = _where(paths)
    _setup_name(name)
    if expect is not None and not agentfiles.expect_ok(expect):
        raise SetupError(agentfiles.EXPECT_TEXT)
    root = _setup_root(w)
    with skillset._locked(*_locks(w), root / "x"):
        taken = os.path.lexists(root / name)
        if taken and not replace:
            raise SetupError(f"a setup named {name!r} already exists; save it under another name or replace it")
        if expect is not None:
            agentfiles.check_expect(expect, _manifest_rev(root / name), f"setup {name!r}")
        files = _capture(w)
        if "preset" not in files:
            raise SetupError("there is no preset file to save")
        try:
            preset = skillset._parse(files["preset"][0].decode("utf-8"))["preset"]
        except (UnicodeDecodeError, skillset.SkillsetError) as exc:
            raise SetupError(f"the preset file cannot be saved as it is: {exc}") from exc
        staging = root / f".{name}.{secrets.token_hex(6)}.tmp"
        osfs.mkdir(staging, 0o700)
        try:
            _write_tree(staging, files)
            manifest = {"version": 1, "name": name, "saved": _now(), "preset": preset, "files": _meta(files),
                        "dirs": _dirs(w)}
            _write_file(staging / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
            record = _swap(w, name, staging)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    counts = _counts(manifest["files"])
    return {"name": name, "changed": True, "replaced": taken, "preset": preset, "files": len(files), "counts": counts,
            "record": record, "message": f"Saved {len(files)} files as {name!r}."}


def _counts(entries: list) -> dict:
    counts: dict = {}
    for entry in entries:
        group = entry["key"].partition("/")[0]
        counts[group] = counts.get(group, 0) + 1
    return counts


def list_setups(**paths) -> list:
    """The saved setups, by name: `{"name", "saved", "preset", "files", "rev"}` (a setup whose manifest cannot be
    read has `"damaged"` too). Reads only the manifests."""
    w = _where(paths)
    found = []
    for entry in _entries(w.setups)[:LIST_LIMIT * 2]:
        if len(found) >= LIST_LIMIT:
            break
        if not names.valid("flow id", entry) or not _is_plain_dir(w.setups / entry):
            continue
        try:
            read = _manifest(w.setups / entry)
            if read is None:
                raise SetupError("it has no manifest")
            manifest, raw = read
            found.append({"name": entry, "saved": manifest["saved"], "preset": manifest["preset"],
                          "files": len(manifest["files"]), "rev": agentfiles.rev(raw)})
        except SetupError as exc:
            found.append({"name": entry, "saved": None, "preset": None, "files": 0, "rev": None,
                          "damaged": str(exc)})
    return found


def show_setup(name, **paths) -> dict:
    """One setup's name, date, preset, counts, size and the names of its files; never a file's contents."""
    w = _where(paths)
    _setup_name(name)
    folder = w.setups / name
    if not _is_plain_dir(folder):
        raise SetupError(f"there is no saved setup {name!r}")
    read = _manifest(folder)
    if read is None:
        raise SetupError(f"setup {name!r} is damaged: it has no manifest")
    manifest, raw = read
    entries = [entry for entry in manifest["files"] if isinstance(entry, dict) and isinstance(entry.get("key"), str)]
    return {"name": name, "saved": manifest["saved"], "preset": manifest["preset"], "files": len(entries),
            "counts": _counts(entries), "size": sum(entry.get("size", 0) for entry in entries
                                                    if isinstance(entry.get("size"), int)),
            "keys": [entry["key"] for entry in entries], "rev": agentfiles.rev(raw)}


def remove(name, *, expect=None, **paths) -> dict:
    """Move the setup `name` into the backups folder; the answer's `record` undoes it (`setup undo`)."""
    w = _where(paths)
    _setup_name(name)
    if expect is not None and not agentfiles.expect_ok(expect):
        raise SetupError(agentfiles.EXPECT_TEXT)
    root = _setup_root(w)
    with skillset._locked(root / "x"):
        if not os.path.lexists(root / name):
            raise SetupError(f"there is no saved setup {name!r}")
        if expect is not None:
            agentfiles.check_expect(expect, _manifest_rev(root / name), f"setup {name!r}")
        record = _swap(w, name, None)
    return {"name": name, "changed": True, "record": record,
            "message": f"Removed {name!r}; `setup undo` brings it back."}


def restore(name, *, expect=None, **paths) -> dict:
    """Put the setup `name` back: every hash is checked first (a damaged setup is refused before anything is
    written), the current state of the set is saved as an undo record, then each file is written byte for byte and
    a managed file that is not in the setup is removed. `expect`: the setup's rev."""
    w = _where(paths)
    _setup_name(name)
    if expect is not None and not agentfiles.expect_ok(expect):
        raise SetupError(agentfiles.EXPECT_TEXT)
    root = _setup_root(w)
    with skillset._locked(*_locks(w), root / "x"):
        manifest, rev, target = _verified(root / name, name)
        if expect is not None:
            agentfiles.check_expect(expect, rev, f"setup {name!r}")
        current = _capture(w)
        _check_plan_targets(w, current, target)
        plan = _change(w, current, target)
        if not plan:
            return {"name": name, "changed": False, "preset": manifest["preset"], "written": 0, "removed": 0,
                    "message": "No change needed: the files are already as saved."}
        after = {key: _sha(target[key][0]) if key in target else _sha(current[key][0]) if key in ("preset", "map")
                 else None for key in set(current) | set(target)}
        record = _save_file_record(w, "restore", name, "set", current, after)
        try:
            _apply(w, plan, keep=set(manifest["dirs"]))
        except BaseException:
            _drop_record(record)
            raise
    _prune(w.backups)
    return {"name": name, "changed": True, "preset": manifest["preset"],
            "written": sum(1 for _, _, new in plan if new is not None),
            "removed": sum(1 for _, _, new in plan if new is None), "record": record, "message": SETUP_MESSAGE}


# --- the snapshot -------------------------------------------------------------------------------------------------


def annotate(document: dict, *, project=None, env=None, **paths) -> dict:
    """Add `setup` to a snapshot document: `{"preset", "presets", "override", "saved": [{"name", "saved", "preset",
    "rev"}], "undo"}` (`undo`: the newest undo record, or None). Cheap: one file parsed, the manifests listed, one
    folder listed; nothing is hashed. Never raises."""
    setup = {"preset": None, "presets": [], "override": None, "saved": [], "undo": None}
    try:
        w = _where(paths)
        try:
            shown = show_preset(project=project, env=env, **paths)
            setup.update(preset=shown["preset"], presets=shown["presets"], override=shown["override"])
        except (SetupError, OSError, ValueError, KeyError) as exc:
            setup["error"] = f"The preset file could not be read: {exc}"
        setup["saved"] = [{key: item[key] for key in ("name", "saved", "preset", "rev", "damaged") if key in item}
                          for item in list_setups(**paths)]
        setup["undo"] = _newest_record(w.backups)
    except (OSError, ValueError, TypeError):
        pass
    document["setup"] = setup
    return document


# --- the command line ---------------------------------------------------------------------------------------------


def _parser():
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--config-dir", type=Path, help="OpenCode's config folder (preset file, agents, commands, "
                                                       "backups)")
    common.add_argument("--orchestration", type=Path, help="the orchestration folder (SKILLS.md, structure, setups)")
    for flag, help_text in (("config", "the plugin's preset file"), ("map", "SKILLS.md"), ("agents", "the agents folder"),
                            ("structure", "the structure folder"), ("commands", "the commands folder"),
                            ("setups", "the setups folder"), ("backups", "the backups folder")):
        common.add_argument("--" + flag, type=Path, help=help_text)
    common.add_argument("--dir", type=Path, help="the project folder checked for a preset override (default: here)")
    common.add_argument("--json", action="store_true")
    parser = argparse.ArgumentParser(prog="agentgraph", description="The default preset and saved setups.",
                                     allow_abbrev=False)
    groups = parser.add_subparsers(dest="group", required=True)
    preset = groups.add_parser("preset", allow_abbrev=False).add_subparsers(dest="op", required=True)
    preset.add_parser("show", parents=[common], allow_abbrev=False)
    preset.add_parser("switch", parents=[common], allow_abbrev=False).add_argument("name")
    preset.add_parser("restore", parents=[common], allow_abbrev=False).add_argument("record")
    setup = groups.add_parser("setup", allow_abbrev=False).add_subparsers(dest="op", required=True)
    saving = setup.add_parser("save", parents=[common], allow_abbrev=False)
    saving.add_argument("name")
    saving.add_argument("--replace", action="store_true", help="replace a setup of that name (the old one is kept "
                                                                "in the backups folder)")
    for op in ("show", "restore", "remove"):
        setup.add_parser(op, parents=[common], allow_abbrev=False).add_argument("name")
    setup.add_parser("list", parents=[common], allow_abbrev=False)
    setup.add_parser("undo", parents=[common], allow_abbrev=False).add_argument("record")
    for op in ("save", "restore", "remove"):
        setup.choices[op].add_argument("--expect", metavar="REV", help="refuse unless the setup's rev is REV")
    return parser


def _paths(args) -> dict:
    paths = {}
    if args.config_dir:
        paths.update(config=args.config_dir / skillset.CONFIG.name, agents_dir=args.config_dir / "agents",
                     commands_dir=args.config_dir / "commands", backups=args.config_dir / "backups")
    if args.orchestration:
        paths.update(map_path=args.orchestration / "SKILLS.md", structure=args.orchestration / "structure",
                     setups=args.orchestration / "setups")
    for flag, key in (("config", "config"), ("map", "map_path"), ("agents", "agents_dir"), ("structure", "structure"),
                      ("commands", "commands_dir"), ("setups", "setups"), ("backups", "backups")):
        if getattr(args, flag):
            paths[key] = getattr(args, flag)
    return paths


def _print(args, result) -> None:
    if args.json:
        print(json.dumps(result))
        return
    group, op = args.group, args.op
    if (group, op) == ("preset", "show"):
        print(f"Default preset: {result['preset']}")
        print("Presets: " + ", ".join(result["presets"]))
        if result["override"]:
            print(f"Overridden by: {result['override']}; OpenCode will use {result['effective']}")
    elif (group, op) == ("setup", "list"):
        for item in result:
            print(f"{item['name']}  {item['saved'] or 'damaged'}  preset {item['preset'] or '-'}  "
                  f"{item['files']} files")
        if not result:
            print("No saved setups.")
    elif (group, op) == ("setup", "show"):
        print(f"{result['name']}  saved {result['saved']}  preset {result['preset']}")
        print(f"{result['files']} files ({result['size']} bytes): "
              + ", ".join(f"{count} {kind}" for kind, count in result["counts"].items()))
    else:
        if result.get("preset") and (group, op) == ("preset", "switch"):
            print(f"Default preset: {result['was']} -> {result['preset']}")
        elif "written" in result:
            print(f"{result['name'] or 'the preset file'}: {result['written']} written, {result['removed']} removed")
        for key in ("record", "backup"):
            if result.get(key):
                print(f"{key}: {result[key]}")
        print(result["message"])


def main(argv=None) -> int:
    osfs.utf8_stdout()
    args = _parser().parse_args(argv)
    try:
        paths = _paths(args)
        extra = {"expect": getattr(args, "expect", None)}
        if args.group == "preset":
            if args.op == "show":
                result = show_preset(project=args.dir, **paths)
            elif args.op == "switch":
                result = switch_preset(args.name, project=args.dir, **paths)
            else:
                result = restore_preset(args.record, **paths)
        elif args.op == "save":
            result = save(args.name, replace=args.replace, **extra, **paths)
        elif args.op == "list":
            result = list_setups(**paths)
        elif args.op == "show":
            result = show_setup(args.name, **paths)
        elif args.op == "restore":
            result = restore(args.name, **extra, **paths)
        elif args.op == "remove":
            result = remove(args.name, **extra, **paths)
        else:
            result = undo(args.record, **paths)
    except (skillset.SkillsetError, OSError) as exc:
        current = getattr(exc, "rev", None)
        if args.json:
            print(json.dumps({"error": str(exc), **({"rev": current} if current is not None else {})}))
        else:
            print(f"setups: {exc}" + (f" (rev now {current})" if current is not None else ""), file=sys.stderr)
        return 1
    _print(args, result)
    return 0


if __name__ == "__main__":
    # As agentgraph.skillset: run as a script, this file's classes are copies of the package's.
    from agentgraph.setups import main as package_main
    raise SystemExit(package_main())
