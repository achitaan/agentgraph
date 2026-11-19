"""Drawn loops in the structure folder: `agentgraph structure loops|link|unlink`.

A drawn loop "B checks A" is the line `checked_by: B` in A's file. `link A B`
adds B to that line and `unlink A B` removes it; the line is added or deleted
as needed, every other byte of the file is kept, the file is replaced
atomically, and repeating a command changes nothing. Exits 0 on success, 1
when the request is refused, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import threading
from pathlib import Path

try:  # POSIX; elsewhere the in-process lock is the only one
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from .snapshot_json import (SETTING_KEYS, SETTING_LINES, STRUCTURE_DIR, checked_by_names, load_structure,
                            read_settings, setting_names)

_LOCK = threading.Lock()  # one writer at a time inside a process; flock on the folder across processes
_SPLIT = re.compile(r"(?<=\n)")
# Line breaks `str.splitlines` (the graph's reader) honours but the editor does not: a file with one is refused.
_OTHER_BREAK = re.compile("\r(?!\n)|[\v\f\x1c\x1d\x1e\x85\u2028\u2029]")


class StructureError(Exception):
    """A refused request; the message says why in plain words."""


def _shape(directory: Path | None) -> dict:
    shape = load_structure(directory)
    if shape is None:
        raise StructureError(f"no structure folder at {Path(directory) if directory is not None else STRUCTURE_DIR}")
    return shape


def drawn_loops(directory: Path | None = None) -> list[dict]:
    """The drawn loops as `{"work", "check", "source": "drawn"}`; empty without a folder."""
    shape = load_structure(directory)
    return [{"work": work, "check": check, "source": "drawn"} for work, check in (shape["loops"] if shape else [])]


def _refusal(shape: dict, work: str, check: str) -> str:
    """Why "check checks work" cannot be drawn, or ""."""
    if work == check:
        return f"{work} cannot check its own work"
    for name in (work, check):
        if any(name.startswith(prefix) and name != lead for lead, prefix in shape["seats"].items()):
            return f"{name} is a council seat; a seat cannot be part of a loop"
        if name not in shape["group_of"]:
            return f"unknown agent {name}: there is no {name}.md in the structure folder"
        if shape["group_of"][name] == "hub":
            return f"{name} is the hub; the hub cannot be part of a loop"
    return ""


def _settings(lines: list[str]) -> tuple[int, int, list[int]]:
    """Read the settings block the way `load_structure` does. Returns the index
    of its first line (or of where it would start), the index just after its
    last setting line (equal to the first when there is none), and the indices
    of the `checked_by` lines."""
    start, end, found, seen = None, None, [], False
    for index, line in enumerate(lines[:SETTING_LINES]):
        content = line.rstrip("\r\n")
        if content.strip() in ("", "---"):
            if seen:
                break
            continue
        key, sep, value = content.partition(":")
        key, value = key.strip().lower(), value.strip().strip("`'\"")
        if not sep or not value or key not in SETTING_KEYS:
            if start is None:
                start = index
            break
        seen = True
        if start is None:
            start = index
        end = index + 1
        if key == "checked_by":
            found.append(index)
    if start is None:
        start = min(len(lines), SETTING_LINES)
        # Leading blank or `---` lines count as before the block only when something follows them.
        if all(line.rstrip("\r\n").strip() in ("", "---") for line in lines):
            start = 0
    return start, end if end is not None else start, found


def _names(line: str) -> list[str]:
    return setting_names(line.rstrip("\r\n").partition(":")[2].strip())


def _eol(lines: list[str], near: int) -> str:
    for line in lines[near:near + 1] + lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def edit_text(text: str, check: str, on: bool) -> str:
    """The file text with `check` added to (on) or removed from (off) its
    `checked_by` line; every other line is kept as it is."""
    lines = [line for line in _SPLIT.split(text) if line]
    start, end, found = _settings(lines)
    names: list[str] = []
    for index in found:
        names.extend(name for name in _names(lines[index]) if name not in names)
    if on == (check in names):
        return text
    names = names + [check] if on else [name for name in names if name != check]
    if found:
        first = lines[found[0]]
        eol = first[len(first.rstrip("\r\n")):]
        for index in reversed(found[1:]):
            del lines[index]
        if names:
            lines[found[0]] = "checked_by: " + ", ".join(names) + eol
        else:
            del lines[found[0]]
            # A block that held only this line loses the blank line that closed it, as `link` added it.
            alone = end - start == 1 and len(found) == 1
            if alone and found[0] + 1 < len(lines) and lines[found[0]].rstrip("\r\n") == "":
                del lines[found[0]]
        return "".join(lines)
    eol = _eol(lines, start)
    if end > start:
        # Add the line at the top of the existing settings.
        lines.insert(start, "checked_by: " + check + eol)
    else:
        # No settings yet: the line, then a blank line before the description.
        lines.insert(start, "checked_by: " + check + eol + (eol if start < len(lines) else ""))
    return "".join(lines)


def _editor_names(text: str) -> list[str]:
    lines = [line for line in _SPLIT.split(text) if line]
    names: list[str] = []
    for index in _settings(lines)[2]:
        names.extend(name for name in _names(lines[index]) if name not in names)
    return names


def _checked_edit(filename: str, text: str, raw: bytes, check: str, on: bool) -> str:
    """`edit_text`, refused unless the graph's reader (`read_settings`) agrees
    with the editor before the edit and, after it, sees exactly the intended
    `checked_by` names and every other setting unchanged."""
    if _OTHER_BREAK.search(text):
        raise StructureError(f"{filename} has a line break other than \\n or \\r\\n (such as a lone \\r); "
                             "it was left unchanged: fix its line breaks first")
    before, _, _ = read_settings(raw)
    names = checked_by_names(before)
    unsafe = (f"{filename}: its settings cannot be edited safely (the graph would read them differently); "
              "it was left unchanged: edit its checked_by line by hand")
    if _editor_names(text) != names:
        raise StructureError(unsafe)
    edited = edit_text(text, check, on)
    after, _, _ = read_settings(edited.encode("utf-8"))
    expected = (names + [check] if check not in names else names) if on else [name for name in names if name != check]
    others = [setting for setting in before if setting[0] != "checked_by"]
    if checked_by_names(after) != expected or [setting for setting in after if setting[0] != "checked_by"] != others:
        raise StructureError(unsafe)
    return edited


def _replace(path: Path, data: bytes) -> None:
    """Write next to the file, then rename over it."""
    mode = stat.S_IMODE(os.stat(path).st_mode)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def set_loop(work: str, check: str, on: bool, directory: Path | None = None) -> tuple[bool, list[dict]]:
    """Draw (on) or remove (off) the loop "check checks work". Returns whether
    a file changed and the drawn loops afterwards. Raises StructureError when
    the request is refused."""
    if not isinstance(work, str) or not isinstance(check, str) or not work or not check:
        raise StructureError("work and check must be agent names")
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    with _LOCK:
        try:
            folder = os.open(root, os.O_RDONLY)
        except OSError as exc:
            raise StructureError(f"no structure folder at {root}") from exc
        try:
            if fcntl is not None:
                fcntl.flock(folder, fcntl.LOCK_EX)
            shape = _shape(root)
            reason = _refusal(shape, work, check)
            if reason:
                raise StructureError(reason)
            path = shape["files"][work]
            if path.is_symlink() or not path.is_file():
                raise StructureError(f"{path.name} is not a regular file")
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise StructureError(f"{path.name} is not UTF-8 text; it was left unchanged") from exc
            edited = _checked_edit(path.name, text, raw, check, on)
            changed = edited != text
            if changed:
                _replace(path, edited.encode("utf-8"))
        finally:
            os.close(folder)  # closing releases the lock
    return changed, drawn_loops(root)


def _print_loops(loops: list[dict]) -> None:
    if not loops:
        print("no drawn loops")
    for loop in loops:
        print(f"{loop['check']} checks {loop['work']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph structure", description=__doc__.splitlines()[0])
    parser.add_argument("--structure", type=Path, metavar="DIR", help="structure folder (default: the orchestration's own)")
    commands = parser.add_subparsers(dest="command", required=True)
    shown = commands.add_parser("loops", help="list the drawn loops")
    shown.add_argument("--json", action="store_true", help='print {"loops": [...]}')
    for name, verb in (("link", "draw"), ("unlink", "remove")):
        command = commands.add_parser(name, help=f'{verb} the loop "CHECK checks WORK"')
        command.add_argument("work", metavar="WORK")
        command.add_argument("check", metavar="CHECK")
        command.add_argument("--json", action="store_true", help='print {"loops": [...]} afterwards')
    for command in commands.choices.values():
        command.add_argument("--structure", type=Path, metavar="DIR", default=argparse.SUPPRESS,
                             help="structure folder (default: the orchestration's own)")
    args = parser.parse_args(argv)
    try:
        if args.command == "loops":
            _shape(args.structure)
            loops = drawn_loops(args.structure)
            changed = False
        else:
            changed, loops = set_loop(args.work, args.check, args.command == "link", args.structure)
    except (StructureError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"agentgraph structure: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"loops": loops}))
    elif args.command == "loops":
        _print_loops(loops)
    else:
        state = ("linked" if changed else "already linked") if args.command == "link" else ("unlinked" if changed else "not linked")
        print(f"{state}: {args.check} checks {args.work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
