"""Inspect or edit the plugin's per-agent skill lists without reformatting its files."""
from __future__ import annotations

import argparse
import copy
from contextlib import ExitStack, contextmanager
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unicodedata

from . import names, sources
from .snapshot_json import skills_document

CONFIG = sources.CONFIG_DIR / "oh-my-opencode-slim.json"
MAP = Path(__file__).resolve().parent.parent / "SKILLS.md"
BACKUPS = sources.CONFIG_DIR / "backups"
# The task profiles (which skills each agent's brief starts from), the toolkit's own copy beside SKILLS.md: the
# toolkit reads nothing from inside an installed skill, which can be removed.
PROFILES = Path(__file__).resolve().parent.parent / "task-profiles.md"
AGENTS_DIR = sources.CONFIG_DIR / "agents"  # OpenCode's agents folder: added agents keep their skills in their file
RESTART = "OpenCode reads this file when it starts; the change applies after a restart."
SEAT_REASON = ("Council seats are model calls defined in the council's own table; "
               "the plugin does not read skills for them.")


class SkillsetError(ValueError):
    """A refused skillset operation."""


def _name(value, kind):
    if (not isinstance(value, str) or not value or len(value) > 100 or value.startswith("-")
            or any(unicodedata.category(c) in {"Cc", "Cs"} for c in value)):
        raise SkillsetError(f"invalid {kind} name: use 1–100 characters, no leading '-' or control characters")
    if kind == "skill" and value != "*" and not names.SKILL.fullmatch(value):
        raise SkillsetError("invalid skill name, so it is not installed: a skill's name is a lowercase letter, then "
                            "lowercase letters, digits or '-' (64 at most)")


AGENT_NAME_REFUSAL = "invalid agent name: use a lowercase letter, then lowercase letters, digits, '_' or '-' (64 at most)"
# A preset agent whose name the pattern refuses: the snapshot's reason starts with the writer's own refusal.
AGENT_NAME_REASON = (AGENT_NAME_REFUSAL + "; agent names become file names, so this agent's skills cannot be changed "
                     "here (rename it in the preset file first)")


def _agent_name(value):
    """An agent name (agentgraph.names: the one definition), safe as a key and as part of a file name."""
    _name(value, "agent")
    if not names.valid("agent", value):
        raise SkillsetError(AGENT_NAME_REFUSAL)


def _read(path, label):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise SkillsetError(f"{label} must be a regular file, not a symlink or special file")
            return handle.read().decode("utf-8"), stat.S_IMODE(info.st_mode)
    except UnicodeDecodeError as exc:
        raise SkillsetError(f"{label} is not UTF-8") from exc
    except OSError as exc:
        raise SkillsetError(f"cannot read {label} as a regular file (no symlinks): {exc}") from exc


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key!r}")
        obj[key] = value
    return obj


def _invalid_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _parse(text):
    try:
        obj = json.loads(text, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise SkillsetError(f"config is not parseable JSON: {exc}") from exc
    presets = obj.get("presets") if isinstance(obj, dict) else None
    active = obj.get("preset") if isinstance(obj, dict) else None
    if not isinstance(presets, dict) or not isinstance(active, str) or active not in presets:
        raise SkillsetError("config shape must have a presets object and an existing active preset")
    for name, entries in presets.items():
        if not isinstance(entries, dict):
            raise SkillsetError(f"config shape: preset {name!r} must be an object")
        for agent, spec in entries.items():
            if not sources._model_spec(spec):
                raise SkillsetError(f"config shape: {name!r}/{agent!r} must be an agent with a usable model")
            value = spec.get("skills", [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise SkillsetError(f"config shape: {name!r}/{agent!r} skills must be an array of strings")
    council = obj.get("council", {})
    if not isinstance(council, dict):
        raise SkillsetError("config shape: council must be an object")
    seats = council.get("presets", {})
    default = council.get("default_preset")
    if (not isinstance(seats, dict) or any(not isinstance(group, dict) for group in seats.values())
            or (default is not None and (not isinstance(default, str) or default not in seats))):
        raise SkillsetError("config shape: expected council presets and an existing council default preset")
    return obj


def _seat_names(obj, *, active_only=False):
    council = obj.get("council", {})
    groups = council.get("presets", {})
    if active_only:
        groups = {"active": groups.get(council.get("default_preset"), {})}
    return {f"councillor-{name}" for group in groups.values() for name, spec in group.items()
            if sources._model_spec(spec)}


def _selected(obj, presets):
    if presets is None:
        return list(obj["presets"])
    names = []
    for name in (presets,) if isinstance(presets, str) else presets:
        _name(name, "preset")
        if name not in obj["presets"]:
            raise SkillsetError(f"unknown preset: {name!r}")
        if name not in names:
            names.append(name)
    return names


def _description(obj, agent, selected, *, native=False):
    active = obj["preset"]
    entries = obj["presets"]
    by_preset = {name: agents[agent].get("skills", []) for name, agents in entries.items() if agent in agents}
    seat = agent in _seat_names(obj)
    if not seat and not native and not any(name in by_preset for name in selected):
        raise SkillsetError(f"agent {agent!r} is not in any selected preset")
    skills = [] if seat else by_preset.get(active, [])
    result = {"agent": agent, "preset": active, "skills": skills,
              "editable": not seat and bool(by_preset),
              "differing_presets": [] if seat else [
                  name for name in entries if name != active and by_preset.get(name) != by_preset.get(active)]}
    if seat:
        result["reason"] = SEAT_REASON
    elif result["editable"] and not names.valid("agent", agent):
        result["editable"] = False  # the writers refuse it (_agent_name): the view says so too
        result["reason"] = AGENT_NAME_REASON
    elif not result["editable"]:
        result["reason"] = "This agent has no entry in the plugin's agent presets."
    return result


def _roster(obj, config, agents_dir=None):
    # The roster reader supports optional native agent files, but only for the
    # canonical config basename. For arbitrary --config names, use its model
    # rules on the already safely read document instead.
    if config.name == CONFIG.name or agents_dir is not None:
        agents, _, _ = sources._load_roster(config.parent, config_path=config, agents_dir=agents_dir)
        return [agent.name for agent in agents if agent.name != "user"]
    disabled = set(sources._disabled_agents(obj, [])) | {"user"}
    return [name for name in [*obj["presets"][obj["preset"]], *sorted(_seat_names(obj, active_only=True))]
            if name not in disabled]


def _file_agent(obj, agent, agents_dir):
    """The agents-folder entry for an agent that is not in any preset ({"added", "editable", "why", "parsed"}),
    or None."""
    if agents_dir is None or any(agent in entries for entries in obj["presets"].values()) or agent in _seat_names(obj):
        return None
    from . import agentfiles  # agentfiles imports this module
    return agentfiles.listing(agents_dir).get(agent)


def _file_description(agent, info):
    from . import agentfiles
    result = {"agent": agent, "preset": "agent file", "differing_presets": [],
              "skills": agentfiles.skills_of(info["parsed"]["permissions"]) if info["parsed"] else [],
              "editable": bool(info["added"] and info["parsed"])}
    if not result["editable"]:
        result["reason"] = info["why"]
    return result


def show(agent, *, config=CONFIG, presets=None, agents_dir=AGENTS_DIR):
    """Report the active list and presets that differ, without writing. An added agent's skills come from its
    agent file in `agents_dir`."""
    _agent_name(agent)
    config = Path(config)
    obj = _parse(_read(config, "config")[0])
    info = _file_agent(obj, agent, agents_dir)
    if info is not None:
        return _file_description(agent, info)
    selected = _selected(obj, presets)
    known = any(agent in entries for entries in obj["presets"].values()) or agent in _seat_names(obj)
    return _description(obj, agent, selected, native=not known and agent in _roster(obj, config))


def show_all(*, config=CONFIG, agents_dir=None):
    """Report editability and skills for every configured agent in the active snapshot roster (agent files read
    from `agents_dir`, default the config's own folder)."""
    config = Path(config)
    obj = _parse(_read(config, "config")[0])
    return [_description(obj, agent, obj["presets"], native=True) for agent in _roster(obj, config, agents_dir)]


_PROFILE_ROW = re.compile(r"^[-*|]\s*`([a-z0-9-]+)`\s*[:|]\s*(.+)$", re.M)
# A skill named in a profile row: any name in backticks shaped like a skill's folder, hyphen or not; only an
# installed skill's name counts (`_profile_refs`), so an ordinary word in backticks never does.
_PROFILE_SKILL = re.compile(r"`([a-z0-9]+(?:-[a-z0-9]+)*)`")


def _profile_refs(profiles, installed):
    try:
        text = Path(profiles).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise SkillsetError(f"cannot read task profiles: {exc}") from exc
    return list(dict.fromkeys((agent, skill) for agent, rest in _PROFILE_ROW.findall(text)
                              for skill in _PROFILE_SKILL.findall(rest) if skill in installed))


def _mismatches(obj, names, refs):
    for preset in names:
        for agent, skill in refs:
            spec = obj["presets"][preset].get(agent)
            if spec is not None and not {"*", skill}.intersection(spec.get("skills", [])):
                yield (preset, agent, skill)


def _warnings(mismatches, profiles):
    grouped = {}
    for preset, agent, skill in mismatches:
        grouped.setdefault((agent, skill), []).append(preset)
    return [f"{Path(profiles).name}: the {agent} profile still names {skill} "
            f"(missing in {', '.join(names)})."
            for (agent, skill), names in grouped.items()]


def _catalogue(skills_dir=None):
    """The installed skill names (in `skills_dir`, default the real skills folder)."""
    return list(dict.fromkeys(row["name"] for row in skills_document(skills_dir=skills_dir)))


def profile_mismatches(*, config=CONFIG, profiles=PROFILES, presets=None, skills_dir=None):
    """Warnings for profile references missing from the active (or named) presets (installed skills: those in
    `skills_dir`, default the real skills folder)."""
    obj = _parse(_read(Path(config), "config")[0])
    names = [obj["preset"]] if presets is None else _selected(obj, presets)
    refs = _profile_refs(profiles, set(_catalogue(skills_dir)) | {"ui-ux-pro-max"})
    return _warnings(_mismatches(obj, names, refs), profiles)


# The stdlib parser is authoritative. This lexer only locates object member
# spans in already validated JSON; it never searches for a key inside strings.
_STRING = r'"(?:[^"\\]|\\.)*"'
_TOKEN = re.compile(rf'{_STRING}|[{{}}\[\]:,]|[^ \t\r\n{{}}\[\]:,]+')


def _spans(text):
    tokens = list(_TOKEN.finditer(text))
    spans, members = {}, {}

    def visit(index, path):
        start = tokens[index].start()
        first = tokens[index].group()
        index += 1
        if first == "{":
            entries = members[path] = []
            while tokens[index].group() != "}":
                key = json.loads(tokens[index].group())
                entries.append((key, tokens[index].start()))
                index = visit(index + 2, (*path, key))
                if tokens[index].group() == ",":
                    index += 1
            index += 1
        elif first == "[":
            depth = 1
            while depth:
                depth += {"[": 1, "]": -1}.get(tokens[index].group(), 0)
                index += 1
        spans[path] = (start, tokens[index - 1].end())
        return index

    visit(0, ())
    return spans, members


def _replace(text, edits):
    for start, end, value in sorted(edits, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def _outside(text, spans):
    last = 0
    chunks = []
    for start, end in sorted(spans):
        if start < last or end < start:
            raise SkillsetError("edit proof failed: overlapping spans")
        chunks.append(text[last:start].encode("utf-8"))
        last = end
    return [*chunks, text[last:].encode("utf-8")]


def _array(raw, values):
    terms = list(re.finditer(_STRING, raw[1:-1]))
    if not terms:
        padding = raw[1:-1]
        line = re.search(r"\r\n|\r|\n", padding)
        if line:
            before, after = padding + "  ", padding
        else:
            middle = (len(padding) + 1) // 2
            before, after = padding[:middle], padding[middle:]
        return "[" + before + (("," + before) if line else ", ").join(map(json.dumps, values)) + after + "]"
    contents = raw[1:-1]
    before, after = contents[:terms[0].start()], contents[terms[-1].end():]
    separators = [contents[a.end():b.start()] for a, b in zip(terms, terms[1:])]
    separator = separators[-1] if separators else ("," + before if "\n" in before or "\r" in before else ", ")
    if not values:
        return "[" + (after if "\n" in before or "\r" in before else before + after) + "]"
    original = {}
    for i, term in reversed(list(enumerate(terms))):
        original[json.loads(term.group())] = i, term.group()
    parts = []
    previous = None
    for value in values:
        index, token = original.get(value, (None, json.dumps(value)))
        if parts:
            parts.append(separators[index - 1] if index is not None and previous is not None and index > previous
                         else separator)
        parts.append(token)
        previous = index
    return "[" + before + "".join(parts) + after + "]"


def _config_edit(text, old, changes):
    spans, members = _spans(text)
    edits = []
    old_masks = []
    expected = copy.deepcopy(old)
    for path, values in changes.items():
        _, preset, agent, key = path
        expected["presets"][preset][agent][key] = values
        if path in spans:
            start, end = spans[path]
            replacement = _array(text[start:end], values)
        else:
            parent = path[:-1]
            entries = members[parent]
            last_key, last_start = entries[-1]
            last_value_start, end = spans[(*parent, last_key)]
            start = end
            if len(entries) == 1:
                gap = text[spans[parent][0] + 1:last_start]
            else:
                previous_end = spans[(*parent, entries[-2][0])][1]
                gap = text[previous_end:last_start].partition(",")[2]
            key_style = text[_TOKEN.match(text, last_start).end():last_value_start]
            line = re.search(r"(\r\n|\r|\n)([ \t]*)$", gap)
            if line:
                newline, indent = line.groups()
                outer = re.match(r"[ \t]*", text[text.rfind("\n", 0, spans[parent][0]) + 1:])[0]
                step = indent[len(outer):] if indent.startswith(outer) and len(indent) > len(outer) else "  "
                array = _array("[" + newline + indent + step + '""' + newline + indent + "]", values)
            else:
                array = json.dumps(values)
            replacement = "," + gap + json.dumps(key) + key_style + array
        edits.append((start, end, replacement))
        old_masks.append((start, end))
    new_text = _replace(text, edits)
    new_obj = _parse(new_text)
    new_spans, new_members = _spans(new_text)
    new_masks = []
    for path in changes:
        start, end = new_spans[path]
        if path not in spans:
            parent = path[:-1]
            old_keys = [key for key, _ in members[parent]]
            if [key for key, _ in new_members[parent]] != [*old_keys, path[-1]]:
                raise SkillsetError("config edit proof failed: missing-key insertion affected other members")
            start = new_spans[(*parent, old_keys[-1])][1]
        new_masks.append((start, end))
    if new_obj != expected or _outside(text, old_masks) != _outside(new_text, new_masks):
        raise SkillsetError("config edit proof failed: unrelated data or bytes changed")
    return new_text, new_obj


_TABLE = re.compile(r"^\| Agent \| Skills \|[ \t]*\r?\n\|[ \t]*-+[ \t]*\|[ \t]*-+[ \t]*\|[ \t]*\r?\n", re.M)
_ROW = re.compile(r"\|[ \t]*`([^`|\r\n]+)`[ \t]*\|[ \t]*(.*?)[ \t]*\|[ \t]*(?:\r?\n|$)")


def _map_rows(text):
    headings = list(_TABLE.finditer(text))
    if len(headings) != 1:
        raise SkillsetError("SKILLS.md must have one Agent / Skills table")
    rows = {}
    i = headings[0].end()
    while text.startswith("|", i):
        row = _ROW.match(text, i)
        if row is None or row[1] in rows or "|" in row[2]:
            raise SkillsetError("SKILLS.md has an ambiguous Agent / Skills row")
        rows[row[1]] = row.span(2)
        i = row.end()
    return rows


def _cell_text(values):
    if not values:
        return "none"
    if values == ("*",):
        return "all"
    if any(re.search(r"[`|\r\n]", name) for name in values):
        raise SkillsetError("a name cannot be represented safely in the SKILLS.md skills table")
    return ", ".join(f"`{name}`" for name in values)


def _projection(obj):
    agents = {}
    for preset, specs in obj["presets"].items():
        for agent, spec in specs.items():
            groups = agents.setdefault(agent, {})
            groups.setdefault(tuple(spec.get("skills", [])), []).append(preset)
    result = {}
    for agent, groups in agents.items():
        if len(groups) == 1:
            result[agent] = _cell_text(next(iter(groups)))
        else:
            if any(re.search(r"[`|\r\n]", name) for names in groups.values() for name in names):
                raise SkillsetError("a preset name cannot be represented safely in the SKILLS.md skills table")
            result[agent] = "<br>".join(", ".join(f"`{name}`" for name in names) + ": " + _cell_text(values)
                                       for values, names in groups.items())
    return result


def _map_cells(text, rows):
    return {name: text[start:end] for name, (start, end) in rows.items()}


def map_matches(*, config=CONFIG, map_path=MAP):
    """Whether the SKILLS.md table documents every configured agent's preset lists."""
    config, map_path = Path(config), Path(map_path)
    with _locked(config, map_path):
        obj = _parse(_read(config, "config")[0])
        text, _ = _read(map_path, "SKILLS.md")
        return _map_cells(text, _map_rows(text)) == _projection(obj)


def _map_edit(text, old, new, agent):
    rows = _map_rows(text)
    if _map_cells(text, rows) != _projection(old):
        raise SkillsetError("SKILLS.md does not match the config; nothing was changed")
    expected = _projection(new)
    start, end = rows[agent]
    updated = _replace(text, [(start, end, expected[agent])])
    new_rows = _map_rows(updated)
    if (_map_cells(updated, new_rows) != expected
            or _outside(text, [(start, end)]) != _outside(updated, [new_rows[agent]])):
        raise SkillsetError("SKILLS.md edit proof failed: unrelated cells or bytes changed")
    return updated


@contextmanager
def _locked(*paths, wrap=True):
    # Directory inodes remain stable when the files are replaced. Lock in
    # inode order so runs that share either file serialize without deadlocking.
    # `wrap` False: an OSError raised in the body passes through as it is (only
    # opening and locking are reported as a SkillsetError).
    if not wrap:
        with ExitStack() as stack:
            try:
                directories = {}
                for path in paths:
                    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    stack.callback(os.close, fd)
                    info = os.fstat(fd)
                    directories[info.st_dev, info.st_ino] = fd
                for _, fd in sorted(directories.items()):
                    fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                raise SkillsetError(f"skillset file operation failed: {exc}") from exc
            yield list(directories.values())
        return
    try:
        with ExitStack() as stack:
            directories = {}
            for path in paths:
                fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                directories[info.st_dev, info.st_ino] = fd
            for _, fd in sorted(directories.items()):
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield list(directories.values())
    except OSError as exc:
        raise SkillsetError(f"skillset file operation failed: {exc}") from exc


@contextmanager
def _staged(path, text, mode):
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        yield name
    finally:
        Path(name).unlink(missing_ok=True)


# A busy day keeps at most 1 + MAX_BACKUPS copies of one file (the day's first state, then up to
# MAX_BACKUPS numbered copies of what later writes replaced; the oldest numbered copy goes first).
MAX_BACKUPS = 48


def _backup_folder(config, root, kind="skills-edit"):
    folder = root / f"{date.today().isoformat()}-{kind}"
    if config.resolve() != CONFIG.resolve():
        # Tests and alternate configs may share both a backup root and basename.
        folder /= hashlib.sha256(os.fsencode(config.resolve())).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _link_new(folder, path, text, mode):
    """Write `text` to `path` only if nothing is there yet; return whether it did."""
    with _staged(path, text, mode) as staged:
        try:
            os.link(staged, path)
        except FileExistsError:
            return False
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _backup(config, root, text, mode, kind="skills-edit"):
    """Save what a write is about to replace. `backup` is the day's first state of the file and
    `previous` the copy of exactly `text` (the same file when nothing changed since it was saved)."""
    folder = _backup_folder(config, root, kind)
    base = folder / config.name
    if not base.exists():
        _link_new(folder, base, text, mode)
    numbered = re.compile(re.escape(config.name) + r"\.(\d{4,})\Z")
    extras = sorted(((int(m[1]), path) for path in folder.iterdir() if (m := numbered.match(path.name))),
                    key=lambda item: item[0])
    for _, path in [(0, base), *extras]:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise SkillsetError("a config backup is not a regular file")
    latest = extras[-1][1] if extras else base
    if latest.read_bytes() == text.encode("utf-8"):
        return {"backup": str(base), "previous": str(latest)}
    number = extras[-1][0] + 1 if extras else 1
    while not _link_new(folder, folder / f"{config.name}.{number:04d}", text, mode):
        number += 1
    previous = folder / f"{config.name}.{number:04d}"
    extras.append((number, previous))
    for _, old in extras[:max(len(extras) - MAX_BACKUPS, 0)]:
        old.unlink(missing_ok=True)
    return {"backup": str(base), "previous": str(previous)}


def _verify(files):
    """Refuse when a file is no longer what this edit read (another process wrote it meanwhile)."""
    for path, old, _, _ in files:
        if _read(path, path.name)[0] != old:
            raise SkillsetError(f"{path.name} changed while this edit was being made; nothing was written. "
                                "Try again.")


def _commit(files, dir_fds, prepare):
    """Stage both files before replacing either; roll back any failed commit."""
    with ExitStack() as stack:
        ready = [(path, stack.enter_context(_staged(path, new, mode)),
                  stack.enter_context(_staged(path, old, mode)))
                 for path, old, new, mode in files]
        _verify(files)
        backup = prepare()
        _verify(files)  # again, right before the first replace: the backup step takes time
        replaced = []
        try:
            for path, new, old in ready:
                os.replace(new, path)
                replaced.append((old, path))
            for fd in dir_fds:
                os.fsync(fd)
        except BaseException:
            for old, path in reversed(replaced):
                os.replace(old, path)
            for fd in dir_fds:
                os.fsync(fd)
            raise
        return backup


def _file_change(op, agent, skill, info, *, agents_dir, backups, skills_dir, check=None):
    """Change an added agent's skills in its agent file (live at once); `check`: see agentfiles._change."""
    from . import agentfiles
    if not info["added"] or not info["parsed"]:
        raise SkillsetError(info["why"])
    catalogue = _catalogue(skills_dir) if op != "all" else []
    if op != "all" and (skill == "*" or skill not in catalogue):
        raise SkillsetError(f"skill {skill!r} is not installed")

    def new(old):  # from the skills the file has as it is written (agentfiles.set_skills), never an earlier read
        if op == "all":
            return ["*"]
        if op == "add":
            return old if "*" in old or skill in old else [*old, skill]
        return [name for name in (catalogue if "*" in old else old) if name != skill]

    shown, record, changed = agentfiles.set_skills(agent, new, agents_dir=agents_dir, backups=backups, check=check)
    return {"agent": agent, "preset": "agent file", "skills": shown["skills"], "editable": True,
            "differing_presets": [], "changed": changed, "changed_presets": [], "warnings": [],
            **({"record": record} if record else {}), "message": agentfiles.LIVE if changed else "No change needed."}


def _change(op, agent, skill, *, config, presets, map_path, backups, profiles, skills_dir=None,
            agents_dir=AGENTS_DIR, expect=None, structure=None):
    """Given `expect` (the agent's rev the view showed: roster.agent_rev, over `structure`, default the roster's
    structure folder), refused with agentfiles.StaleError, nothing written, when the agent's rev is another."""
    _agent_name(agent)
    if op != "all":
        _name(skill, "skill")
    config, map_path, backups = Path(config), Path(map_path), Path(backups)
    from . import roster  # roster imports this module
    structure = Path(structure) if structure is not None else roster.STRUCTURE_DIR
    info = _file_agent(_parse(_read(config, "config")[0]), agent, agents_dir)
    if info is not None:
        # The skill is checked as installed under the preset file's lock, which a skill's deletion holds too
        # (skillfiles.delete), so it cannot be deleted between the check and the write.
        with roster.expecting(agent, expect, config=config, map_path=map_path, structure=structure) as check, \
                _locked(config, map_path, wrap=False):
            return _file_change(op, agent, skill, info, agents_dir=agents_dir, backups=backups, skills_dir=skills_dir,
                                check=check)
    with _locked(config, map_path) as dirs:
        if expect is not None:
            from . import agentfiles
            agentfiles.check_expect(expect, roster.agent_rev(agent, config=config, map_path=map_path,
                                                             agents_dir=agents_dir, structure=structure),
                                    f"{agent!r}")
        text, mode = _read(config, "config")
        obj = _parse(text)
        if agent in _seat_names(obj):
            raise SkillsetError(SEAT_REASON)
        selected = _selected(obj, presets)
        _description(obj, agent, selected)
        catalogue = _catalogue(skills_dir) if op != "all" else []
        if op != "all" and (skill == "*" or skill not in catalogue):
            raise SkillsetError(f"skill {skill!r} is not installed")
        changes = {}
        for preset in selected:
            spec = obj["presets"][preset].get(agent)
            if spec is None:
                continue
            old = spec.get("skills", [])
            if op == "all":
                new = ["*"]
            elif op == "add":
                new = old if "*" in old or skill in old else [*old, skill]
            else:
                new = [name for name in (catalogue if "*" in old else old) if name != skill]
            if new != old:
                changes["presets", preset, agent, "skills"] = new
        if not changes:
            return {**_description(obj, agent, selected), "changed": False,
                    "changed_presets": [], "warnings": [], "message": "No change needed."}
        new_text, new_obj = _config_edit(text, obj, changes)
        map_text, map_mode = _read(map_path, "SKILLS.md")
        new_map = _map_edit(map_text, obj, new_obj, agent)
        warnings = []
        if op == "remove":
            refs = _profile_refs(profiles, set(catalogue) | {"ui-ux-pro-max"})
            before = set(_mismatches(obj, selected, refs))
            warnings = _warnings((row for row in _mismatches(new_obj, selected, refs)
                                  if row not in before), profiles)
        backup = _commit([(map_path, map_text, new_map, map_mode), (config, text, new_text, mode)], dirs,
                         lambda: _backup(config, backups, text, mode))
        return {**_description(new_obj, agent, selected), "changed": True,
                "changed_presets": [path[1] for path in changes], "warnings": warnings,
                "backup": backup["backup"], "previous": backup["previous"], "message": RESTART}


def add(agent, skill, *, config=CONFIG, presets=None, map_path=MAP, backups=BACKUPS, profiles=PROFILES,
        skills_dir=None, agents_dir=AGENTS_DIR, expect=None, structure=None):
    """Add one installed skill (one in `skills_dir`, default the real skills folder) to the selected presets, or
    to an added agent's file in `agents_dir`. `expect`, `structure`: see `_change`."""
    return _change("add", agent, skill, config=config, presets=presets, map_path=map_path,
                   backups=backups, profiles=profiles, skills_dir=skills_dir, agents_dir=agents_dir, expect=expect,
                   structure=structure)


def remove(agent, skill, *, config=CONFIG, presets=None, map_path=MAP, backups=BACKUPS, profiles=PROFILES,
           skills_dir=None, agents_dir=AGENTS_DIR, expect=None, structure=None):
    """Remove one installed skill, expanding a wildcard if needed."""
    return _change("remove", agent, skill, config=config, presets=presets, map_path=map_path,
                   backups=backups, profiles=profiles, skills_dir=skills_dir, agents_dir=agents_dir, expect=expect,
                   structure=structure)


def set_all(agent, *, config=CONFIG, presets=None, map_path=MAP, backups=BACKUPS, profiles=PROFILES,
            skills_dir=None, agents_dir=AGENTS_DIR, expect=None, structure=None):
    """Set the selected presets (or an added agent's file) to the wildcard skill list."""
    return _change("all", agent, None, config=config, presets=presets, map_path=map_path,
                   backups=backups, profiles=profiles, skills_dir=skills_dir, agents_dir=agents_dir, expect=expect,
                   structure=structure)


OUTSIDE_REASON = ("This agent is not in the plugin's roster (one of OpenCode's own agents); "
                  "its skills cannot be set here.")


def annotate(document, *, config=CONFIG, profiles=PROFILES, agents_dir=AGENTS_DIR, skills_dir=None):
    """Add to a snapshot document, in place, whether each agent's skills can be edited here
    (`agents[].skills_editable`, `agents[].skills_reason` when not) and `skill_warnings` (task
    profiles that name a skill their agent no longer has). Reads only; never raises."""
    try:
        access = {row["agent"]: row for row in show_all(config=config, agents_dir=agents_dir)}
        warnings = profile_mismatches(config=config, profiles=profiles, skills_dir=skills_dir)
        obj = _parse(_read(Path(config), "config")[0])
        for agent in document.get("agents", []):
            name = agent.get("name")
            info = _file_agent(obj, name, agents_dir) if isinstance(name, str) else None
            if info is not None:
                access[name] = _file_description(name, info)
    except (SkillsetError, OSError, ValueError, KeyError, TypeError) as exc:
        access, warnings = None, []
        failure = f"The plugin's config could not be read: {exc}"
    for agent in document.get("agents", []):
        row = access.get(agent.get("name")) if access is not None else None
        editable = bool(row and row.get("editable"))
        agent["skills_editable"] = editable
        agent["skills_reason"] = "" if editable else (
            failure if access is None else (row or {}).get("reason") or OUTSIDE_REASON)
    document["skill_warnings"] = warnings
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -P -m agentgraph.skillset", description=__doc__,
                                     allow_abbrev=False)
    commands = parser.add_subparsers(dest="op", required=True)

    def options(target, prefix):
        target.add_argument("--preset", action="append", dest=prefix + "presets")
        target.add_argument("--config", type=Path, dest=prefix + "config")
        target.add_argument("--map", type=Path, dest=prefix + "map_path")
        target.add_argument("--backups", type=Path, dest=prefix + "backups")
        target.add_argument("--agents", type=Path, dest=prefix + "agents", help="OpenCode's agents folder")
        target.add_argument("--skills-dir", type=Path, dest=prefix + "skills_dir", help="the skills folder")
        target.add_argument("--structure", type=Path, dest=prefix + "structure", help="the structure folder")
        target.add_argument("--json", action="store_true", dest=prefix + "json")

    options(parser, "root_")
    for op in ("show", "add", "remove", "all"):
        sub = commands.add_parser(op, allow_abbrev=False)
        options(sub, "")
        sub.add_argument("agent")
        if op in {"add", "remove"}:
            sub.add_argument("skill")
        if op != "show":
            sub.add_argument("--expect", metavar="REV", help="refuse, changing nothing, unless the agent's rev is REV "
                                                             "(the rev the caller last read)")
    args = parser.parse_args(argv)
    output_json = args.json or args.root_json
    kw = {"config": args.config or args.root_config or CONFIG,
          "presets": (args.root_presets or []) + (args.presets or []) or None}
    kw["agents_dir"] = args.agents or args.root_agents or AGENTS_DIR
    try:
        if args.op == "show":
            result = show(args.agent, **kw)
        else:
            kw.update(map_path=args.map_path or args.root_map_path or MAP,
                      backups=args.backups or args.root_backups or BACKUPS,
                      skills_dir=args.skills_dir or args.root_skills_dir, expect=args.expect,
                      structure=args.structure or args.root_structure)
            if args.op == "all":
                result = set_all(args.agent, **kw)
            else:
                result = (add if args.op == "add" else remove)(args.agent, args.skill, **kw)
    except (SkillsetError, OSError) as exc:
        current = getattr(exc, "rev", None)
        if output_json:
            print(json.dumps({"error": str(exc), **({"rev": current} if current is not None else {})}))
        else:
            print(f"skillset: {exc}" + (f" (rev now {current})" if current is not None else ""), file=sys.stderr)
        return 1
    if output_json:
        print(json.dumps(result))
    else:
        print(f"{result['agent']} ({result['preset']}): {', '.join(result['skills']) or 'none'}")
        if result["differing_presets"]:
            print("Differs in: " + ", ".join(result["differing_presets"]))
        if "reason" in result:
            print(result["reason"])
        if "message" in result:
            print(result["message"])
        for warning in result.get("warnings", []):
            print("Warning: " + warning)
    return 0


if __name__ == "__main__":
    # Run as `python -m agentgraph.skillset`, this file is __main__ and its classes are copies: an error the
    # package's modules raise (agentgraph.skillset.SkillsetError, agentfiles.StaleError) would escape this copy's
    # `except`. The package's own main catches them.
    from agentgraph.skillset import main as package_main
    raise SystemExit(package_main())
