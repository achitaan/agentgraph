"""Who gets which work, read from the structure folder: `agentgraph route` and the `use_when` setting.

An agent's file in the structure folder may carry `use_when: TEXT` among its settings (structure/README.md): one
line saying when the orchestrator should pick this agent. It is read with the other settings
(snapshot_json.read_settings), shown in the snapshot as `agents[].use_when` (roster.annotate) and written by
`agentgraph route use-when NAME --text TEXT` (`set_use_when`): under the structure folder's lock, refused with
StaleError when the agent's `rev` (roster.agent_rev, the one the snapshot shows) is not `--expect`, and with an undo
record of the earlier bytes that `agentgraph route restore RECORD` puts back (`restore_use_when`). Only that one line
of the agent's file changes; an empty text takes the setting away.

`agentgraph route` prints the table an orchestrator needs to delegate, from the folder alone (and SKILLS.md for each
agent's skills): agent, group, pool, escalates_to, use_when, skills, the groups it leads or reports to and the drawn
paths that start at it. `--json` prints every field. Exits 0 on success, 1 when a request is refused, 2 on a usage
error.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import unicodedata
from datetime import date
from pathlib import Path

from . import agentfiles, names, roster, skillset
from .snapshot_json import HEAD_BYTES, SETTING_KEYS, SETTING_LINES, STRUCTURE_DIR, load_structure, read_settings

USE_WHEN = "use_when"
USE_WHEN_LIMIT = 240
RECORD_FOLDER = "structure-settings"  # <backups>/<date>-structure-settings/use_when-<agent>-<hex>/record.json
_RECORD_PATH = re.compile(rf"\d{{4}}-\d{{2}}-\d{{2}}-{RECORD_FOLDER}/use_when-({names.AGENT_PATTERN})-[0-9a-f]{{12}}"
                          r"/record\.json")
_QUOTES = "`'\""


class RouteError(Exception):
    """A refused request; the message says why in plain words."""


class StaleError(RouteError):
    """The agent changed since the view showed it: nothing was written; `rev` is the current one."""

    def __init__(self, message: str, rev: str):
        super().__init__(message)
        self.rev = rev


# --- the setting ---------------------------------------------------------------------------------

def clean_use_when(text) -> str:
    """The text as it will be stored (surrounding spaces dropped), or RouteError: one line of at most
    USE_WHEN_LIMIT characters, no control character, not starting or ending with a quote or backtick (the settings
    reader would drop them). "" takes the setting away."""
    if not isinstance(text, str):
        raise RouteError("use_when must be text")
    text = text.strip()
    if len(text) > USE_WHEN_LIMIT:
        raise RouteError(f"use_when is {len(text)} characters long; at most {USE_WHEN_LIMIT} are allowed")
    if any(ch in "\n\r" for ch in text) or len(text.splitlines()) > 1:
        raise RouteError("use_when is one line: it may not hold a line break")
    bad = next((ch for ch in text if unicodedata.category(ch) in ("Cc", "Cs")), None)
    if bad is not None:
        raise RouteError(f"use_when holds the control character U+{ord(bad):04X}")
    if text and (text[0] in _QUOTES or text[-1] in _QUOTES):
        raise RouteError("use_when may not start or end with a quote or a backtick")
    return text


def use_when_of(data: bytes | None) -> str:
    """The `use_when` setting of an agent's file (its bytes), or "" when it has none."""
    if not data:
        return ""
    return next((value for key, value in read_settings(data)[0] if key == USE_WHEN), "")


def _about(data: bytes) -> str:
    _, lines, prose_from = read_settings(data)
    return " ".join(" ".join(lines[prose_from:]).split())


def _body(line: str) -> str:
    parts = line.splitlines()
    return parts[0] if parts else ""


def with_use_when(data: bytes, text: str) -> bytes:
    """`data` (an agent's file in the structure folder) with its `use_when` line set to `text` ("" removes it),
    every other byte kept. RouteError when the file is not UTF-8 or the setting cannot be placed so that the
    settings reader reads it back (it reads at most SETTING_LINES lines and HEAD_BYTES bytes)."""
    try:
        full = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RouteError("the agent's file is not UTF-8 text; it was left unchanged") from None
    lines = full.splitlines(keepends=True)
    settings_at: list[int] = []
    closing = None
    for index, line in enumerate(lines[:SETTING_LINES]):
        body = _body(line)
        key, sep, value = body.partition(":")
        key, value = key.strip().lower(), value.strip().strip(_QUOTES)
        if body.strip() in ("", "---"):
            if settings_at:
                closing = index
                break
            continue
        if not sep or not value or key not in SETTING_KEYS:
            break
        settings_at.append(index)
    mine = [index for index in settings_at if _body(lines[index]).partition(":")[0].strip().lower() == USE_WHEN]
    ending = "\r\n" if settings_at and lines[settings_at[0]].endswith("\r\n") else "\n"
    new_line = f"{USE_WHEN}: {text}{ending}"
    out = list(lines)
    if text and mine:
        out[mine[0]] = new_line
        for index in reversed(mine[1:]):
            del out[index]
    elif text and settings_at:
        last = out[settings_at[-1]]
        if not last.endswith(("\n", "\r")):  # a file of settings alone, its last line without an end
            out[settings_at[-1]] = last + ending
        out.insert(settings_at[-1] + 1, new_line)
    elif text:
        out[0:0] = [new_line, ending]
    elif mine:
        for index in reversed(mine):
            del out[index]
        if len(mine) == len(settings_at) and closing is not None:
            del out[closing - len(mine)]  # no setting is left: the blank line that closed them goes too
    result = "".join(out).encode("utf-8")
    before, after = read_settings(data)[0], read_settings(result)[0]
    if ([item for item in after if item[0] != USE_WHEN] != [item for item in before if item[0] != USE_WHEN]
            or [value for key, value in after if key == USE_WHEN] != ([text] if text else [])
            or _about(result) != _about(data)):
        raise RouteError(f"use_when cannot be placed in this file so that it reads back (at most {SETTING_LINES} "
                         f"setting lines in the first {HEAD_BYTES} bytes); it was left unchanged")
    return result


# --- writing it, with an undo record ---------------------------------------------------------------

def _find_file(root_fd: int, name: str) -> tuple[str, bytes]:
    """(the group folder, the bytes) of `name`'s file in the structure folder; RouteError when it has none."""
    for folder in roster._group_folders(root_fd):
        fd = roster._open_folder(root_fd, folder, False)
        if fd is None:
            continue
        try:
            data = roster._read_at(fd, name + ".md")
        finally:
            os.close(fd)
        if data is not None:
            return folder, data
    raise RouteError(f"unknown agent {name}: there is no {name}.md in the structure folder")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _save_record(backups, name: str, record: dict) -> str:
    root = Path(backups)
    day = root / f"{date.today().isoformat()}-{RECORD_FOLDER}"
    for path in (root, day):
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise RouteError(f"{path} is not a plain folder; nothing was changed")
    folder = day / f"use_when-{name}-{secrets.token_hex(6)}"
    os.mkdir(folder, 0o700)
    path = folder / "record.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return str(path)


def _change(root_fd: int, name: str, folder: str, before: bytes, after: bytes, backups) -> str:
    """Write `after` over `before` (refused when the file is no longer `before`), with an undo record first."""
    record = _save_record(backups, name, {"version": 1, "op": USE_WHEN, "agent": name, "folder": folder,
                                          "before": base64.b64encode(before).decode("ascii"),
                                          "after": _digest(after)})
    try:
        roster._change_file(root_fd, {"folder": folder, "name": name + ".md", "before": before, "after": after})
    except BaseException:
        agentfiles.drop_record(record)
        raise
    return record


def _plain(exc: Exception) -> RouteError:
    if isinstance(exc, agentfiles.StaleError):
        return StaleError(str(exc), exc.rev)
    return RouteError(str(exc))


def set_use_when(name, text, structure_dir: Path | None = None, *, expect: str | None = None,
                 backups=skillset.BACKUPS, config=skillset.CONFIG, map_path=skillset.MAP,
                 agents_dir=roster.AGENTS_DIR) -> dict:
    """Set `name`'s use_when to `text` ("" takes it away). Given `expect` (the agent's rev the view showed:
    roster.agent_rev), refused with StaleError, nothing written, when the rev is now another. Returns {"agent",
    "use_when", "changed", "record" (when changed: `restore_use_when` undoes it), "rev" (the agent's rev now)}."""
    if not isinstance(name, str) or not names.AGENT.fullmatch(name):
        raise RouteError("the agent name must be a lowercase letter, then lowercase letters, digits, _ or -")
    text = clean_use_when(text)
    if not agentfiles.expect_ok(expect):
        raise RouteError(agentfiles.EXPECT_TEXT)
    root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
    record = None
    try:
        with roster._structure_locked(root) as root_fd:
            if root_fd is None:
                raise RouteError(f"no structure folder at {root}")
            folder, before = _find_file(root_fd, name)
            roster._expect_agent(name, expect, data=roster._agent_bytes(agents_dir, name), root_fd=root_fd,
                                 config=config, map_path=map_path)
            after = with_use_when(before, text)
            if after != before:
                record = _change(root_fd, name, folder, before, after, backups)
    except (agentfiles.AgentfilesError, skillset.SkillsetError) as exc:
        raise _plain(exc) from None
    return {"agent": name, "use_when": text, "changed": record is not None, **({"record": record} if record else {}),
            "rev": roster.agent_rev(name, config=config, map_path=map_path, agents_dir=agents_dir, structure=root)}


def _load_record(record, backups) -> tuple[str, dict]:
    if not isinstance(record, str) or not record or "\0" in record or len(record) > 4096:
        raise RouteError("invalid undo record: expected the record a use_when change returned")
    root = Path(os.path.abspath(backups))
    path = Path(os.path.abspath(record))
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise RouteError("invalid undo record: it is not one of the backups folder's undo records") from None
    found = _RECORD_PATH.fullmatch(relative.as_posix())
    if not found:
        raise RouteError("invalid undo record: it is not one of the backups folder's undo records")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise RouteError("invalid undo record: its folder is not a plain folder")
        except FileNotFoundError:
            raise RouteError("invalid undo record: there is no such record") from None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise RouteError("invalid undo record: there is no such record") from None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RouteError("invalid undo record: not a regular file")
        raw = stream.read(1 << 22)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RouteError(f"invalid undo record: {exc}") from None
    name = found.group(1)
    if (not isinstance(data, dict) or set(data) != {"version", "op", "agent", "folder", "before", "after"}
            or data["version"] != 1 or data["op"] != USE_WHEN or data["agent"] != name
            or not isinstance(data["folder"], str) or not isinstance(data["after"], str)
            or not isinstance(data["before"], str)):
        raise RouteError("invalid undo record: not a use_when change")
    return name, data


def restore_use_when(record, structure_dir: Path | None = None, *, backups=skillset.BACKUPS) -> dict:
    """Put back the agent file a use_when change replaced, while it is still what that change left. Returns
    {"agent", "use_when", "changed", "record"}: a record that undoes this in turn."""
    name, data = _load_record(record, backups)
    try:
        before = base64.b64decode(data["before"], validate=True)
    except (TypeError, ValueError):
        raise RouteError("invalid undo record: the earlier file is not base64") from None
    root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
    try:
        with roster._structure_locked(root) as root_fd:
            if root_fd is None:
                raise RouteError(f"no structure folder at {root}")
            folder, current = _find_file(root_fd, name)
            if folder != data["folder"] or _digest(current) != data["after"]:
                raise RouteError(f"{name}.md was changed after that edit; nothing was restored")
            undo = _change(root_fd, name, folder, current, before, backups)
    except (agentfiles.AgentfilesError, skillset.SkillsetError) as exc:
        raise _plain(exc) from None
    return {"agent": name, "use_when": use_when_of(before), "changed": True, "record": undo}


# --- the route table -----------------------------------------------------------------------------

def _skills(map_path) -> tuple[dict[str, list[str]], list[str]]:
    """{agent: its skills from SKILLS.md (["all"] for every skill)}, and warnings."""
    try:
        text = Path(map_path).read_text(encoding="utf-8")
        rows = skillset._map_rows(text)
    except (OSError, UnicodeDecodeError, skillset.SkillsetError) as exc:
        return {}, [f"skills: SKILLS.md could not be read ({exc}); skills are left out"]
    found = {}
    for name, cell in skillset._map_cells(text, rows).items():
        cell = cell.strip()
        found[name] = ["all"] if cell == "all" else re.findall(r"`([^`]+)`", cell)
    return found, []


def table(structure_dir: Path | None = None, map_path=skillset.MAP) -> dict:
    """{"agents": [...], "warnings": [...]} from the structure folder: one entry per agent file (a council seat stands
    for its council and is not listed), in the folder's order: {"name", "group", "pool", "escalates_to", "use_when",
    "seats", "skills", "leads" ([{"id", "members"}]), "reports_to" ([leads]), "paths" ([{"id", "name", "path"}]: the
    drawn paths whose first step is this agent)}. RouteError without a structure folder."""
    shape = load_structure(structure_dir)
    if shape is None:
        root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
        raise RouteError(f"no structure folder at {root}")
    skills, warnings = _skills(map_path)
    rows = []
    for name, group in shape["group_of"].items():
        if any(name != lead and name.startswith(prefix) for lead, prefix in shape["seats"].items()):
            continue  # a seat: its council stands for it
        try:
            with open(shape["files"][name], "rb") as stream:
                settings = read_settings(stream.read(HEAD_BYTES))[0]
        except OSError:
            settings = []
        first = {}
        for key, value in settings:
            first.setdefault(key, value)
        rows.append({
            "name": name, "group": group, "pool": shape["pool"].get(name), "escalates_to": first.get("escalates_to"),
            "use_when": first.get(USE_WHEN, ""), "seats": shape["seats"].get(name), "skills": skills.get(name),
            "leads": [{"id": team["id"], "members": list(team["members"])} for team in shape["teams"]
                      if team["lead"] == name],
            "reports_to": [team["lead"] for team in shape["teams"] if name in team["members"]],
            "paths": [{"id": flow["id"], "name": flow.get("name"), "path": list(flow["path"])}
                      for flow in shape["flows"] if flow["path"] and flow["path"][0] == name],
        })
    return {"agents": rows, "warnings": warnings + list(shape.get("warnings", []))}


def text(found: dict) -> str:
    """The plain table: one line per agent, the fields that are set, nothing else."""
    lines = ["Routing (agentgraph route; --json for every field): agent [group, pool, escalates to]: use when; "
             "skills; groups; paths that start at it."]
    for row in found["agents"]:
        tags = [row["group"]]
        if row["pool"] is not None:
            tags.append(f"pool {row['pool']}")
        if row["escalates_to"]:
            tags.append(f"up {row['escalates_to']}")
        if row["seats"]:
            tags.append(f"seats {row['seats']}*")
        parts = [f"{row['name']} [{', '.join(tags)}]: {row['use_when'] or '-'}"]
        if row["skills"] is not None:
            parts.append("skills " + (", ".join(row["skills"]) or "none"))
        for team in row["leads"]:
            parts.append("leads " + ", ".join(team["members"]))
        if row["reports_to"]:
            parts.append("reports to " + ", ".join(row["reports_to"]))
        if row["paths"]:
            parts.append("paths " + "; ".join(" → ".join(flow["path"]) for flow in row["paths"]))
        lines.append("; ".join(parts))
    return "\n".join(lines) + "\n"


# --- command -------------------------------------------------------------------------------------

def _read_text(source: str) -> str:
    stream = sys.stdin.buffer if source == "-" else open(source, "rb")
    try:
        data = stream.read(4 * USE_WHEN_LIMIT + 16)
    finally:
        if source != "-":
            stream.close()
    try:
        return data.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError:
        raise RouteError("use_when is not UTF-8 text") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph route", description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--structure", type=Path, metavar="DIR", help="structure folder (default: the orchestration's own)")
    parser.add_argument("--map", type=Path, metavar="PATH", default=skillset.MAP,
                        help="SKILLS.md, for each agent's skills (default: the orchestration's own)")
    parser.add_argument("--json", action="store_true", help='print {"agents": [...]}')
    commands = parser.add_subparsers(dest="command")
    setting = commands.add_parser("use-when", help="set (or, with an empty text, take away) an agent's use_when",
                                  allow_abbrev=False)
    setting.add_argument("name", metavar="NAME")
    given = setting.add_mutually_exclusive_group(required=True)
    given.add_argument("--text", metavar="TEXT")
    given.add_argument("--text-file", metavar="F", help="the text from file F, or - for standard input")
    setting.add_argument("--expect", metavar="REV", help="refuse, changing nothing, unless the agent's rev is REV")
    setting.add_argument("--config", type=Path, default=skillset.CONFIG, help=argparse.SUPPRESS)
    setting.add_argument("--agents-dir", type=Path, default=roster.AGENTS_DIR, help=argparse.SUPPRESS)
    restore = commands.add_parser("restore", help="undo a use_when change from its RECORD", allow_abbrev=False)
    restore.add_argument("record", metavar="RECORD")
    for command in (setting, restore):
        command.add_argument("--structure", type=Path, metavar="DIR", default=argparse.SUPPRESS,
                             help="structure folder (default: the orchestration's own)")
        command.add_argument("--backups", type=Path, metavar="DIR", default=skillset.BACKUPS,
                             help="where undo records go (default: OpenCode's backups folder)")
        command.add_argument("--map", type=Path, metavar="PATH", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print the answer as JSON")
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            found = table(args.structure, args.map)
            if args.json:
                print(json.dumps(found, ensure_ascii=False))
            else:
                for warning in found["warnings"]:
                    print(warning, file=sys.stderr)
                print(text(found), end="")
            return 0
        if args.command == "use-when":
            value = args.text if args.text is not None else _read_text(args.text_file)
            result = set_use_when(args.name, value, args.structure, expect=args.expect, backups=args.backups,
                                  config=args.config, map_path=args.map, agents_dir=args.agents_dir)
        else:
            result = restore_use_when(args.record, args.structure, backups=args.backups)
    except (RouteError, OSError) as exc:
        current = getattr(exc, "rev", None)
        if args.json:
            print(json.dumps({"error": str(exc), **({"rev": current} if current is not None else {})}))
        else:
            print(f"agentgraph route: {exc}" + (f" (rev now {current})" if current is not None else ""),
                  file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif not result["changed"]:
        print(f"{result['agent']}: use_when unchanged")
    else:
        what = f"use_when: {result['use_when']}" if result["use_when"] else "no use_when"
        print(f"{result['agent']}: {what} (undo: agentgraph route restore {result['record']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
