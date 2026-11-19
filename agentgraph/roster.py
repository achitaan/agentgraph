"""Add, remove or restore agents in the plugin's preset file, with undo records."""
from __future__ import annotations

import argparse
import copy
import hashlib
from datetime import date
import json
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata

from . import skillset, sources
from .skillset import BACKUPS, CONFIG, MAP, SkillsetError

RESTART = skillset.RESTART

# The plugin's own agents (oh-my-opencode-slim 3.0.6, dist/index.js lines 24337-24355:
# AGENT_ALIASES, SUBAGENT_NAMES, ALL_AGENT_NAMES). A preset entry with one of these names
# overrides a built-in; any other name is a custom agent (isSafeCustomAgentName, line 29159).
BUILTIN = ("orchestrator", "explorer", "oracle", "designer", "fixer", "observer", "council", "councillor")
ALIASES = {"explore": "explorer", "librarian": "explorer", "frontend-ui-ux-engineer": "designer"}
# Never removed or turned off here: protected by the plugin (line 24365), or the council itself.
PROTECTED = ("orchestrator", "council", "councillor")
# Keys the plugin reads at the preset level; a custom agent with one of these names would be
# mistaken for them. "constructor" and "prototype" are JavaScript object traps.
RESERVED = ("extends", "agents", "marketplace", "constructor", "prototype")
SEAT_PREFIX = "councillor-"

NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
MODEL = re.compile(r"[^/\s]+/\S+")  # the plugin's provider/model shape (ProviderModelIdSchema)
COLOR = re.compile(r"#[0-9a-fA-F]{6}|primary|secondary|accent|success|warning|error|info")
MAX_DESCRIPTION, MAX_PROMPT = 300, 8000

# Permission shapes. Existing implementer entries (impl-openai, impl-claude, impl-hard) carry no
# permission key at all (the host default applies), so the default shape lists every tool
# explicitly instead: the tool names are those of oracle-lite's block, writers may use them, and
# `task` stays denied. The read-only shape is oracle-lite's block, copied exactly.
WRITER_PERMISSION = {"bash": "allow", "edit": "allow", "write": "allow", "apply_patch": "allow",
                     "ast_grep_replace": "allow", "task": "deny"}
READ_ONLY_PERMISSION = {"bash": "deny", "edit": "deny", "write": "deny", "apply_patch": "deny",
                        "ast_grep_replace": "deny", "task": "deny"}

SCHEMA_GLOB = ("*/node_modules/oh-my-opencode-slim/oh-my-opencode-slim.schema.json")
SCHEMA_ROOT = Path.home() / ".cache/opencode/npm/oh-my-opencode-slim@latest"

_WS = r"[ \t\r\n]*"
_FORMS = {
    "lead": lambda before, after: before == "" and re.fullmatch(rf"{_WS},{_WS}", after),
    "trail": lambda before, after: after == "" and re.fullmatch(rf"{_WS},{_WS}", before),
    "only": lambda before, after: re.fullmatch(_WS, before) and re.fullmatch(_WS, after),
}
_BIDI = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


class RosterError(SkillsetError):
    """A refused roster operation."""


# --- validation of caller text --------------------------------------------------------------


def _clean(text, kind, *, limit, multiline=False):
    if not isinstance(text, str) or not text.strip():
        raise RosterError(f"{kind} is required")
    if len(text) > limit:
        raise RosterError(f"{kind} is too long: {len(text)} characters, the limit is {limit}")
    for char in text:
        if multiline and char in "\n\t":
            continue
        if unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} or char in _BIDI:
            raise RosterError(f"{kind} contains a control character (U+{ord(char):04X})")
    return text


def _word(text, kind, pattern, hint):
    if not isinstance(text, str) or not pattern.fullmatch(text) or any(unicodedata.category(c) == "Cc" for c in text):
        raise RosterError(f"invalid {kind}: {hint}")
    return text


def _names(values, kind):
    if values is None:
        return []
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise RosterError(f"{kind} must be a list of names")
    result = []
    for value in values:
        skillset._name(value, kind)
        if value == "*":
            raise RosterError(f"{kind} may not use a wildcard; list each name")
        if value in result:
            raise RosterError(f"duplicate {kind} name: {value!r}")
        result.append(value)
    return result


def _check_new_name(obj, name, selected):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise RosterError("invalid agent name: use a lowercase letter, then lowercase letters, digits, '_' or '-' (64 at most)")
    if name in BUILTIN or name in ALIASES:
        raise RosterError(f"{name!r} is a built-in agent name or alias")
    if name.startswith(SEAT_PREFIX) or name in skillset._seat_names(obj):
        raise RosterError(f"names starting with {SEAT_PREFIX!r} belong to council seats")
    if name in RESERVED:
        raise RosterError(f"{name!r} is a key the plugin reads in presets; choose another name")
    taken = [preset for preset in selected if name in obj["presets"][preset]]
    if taken:
        raise RosterError(f"agent {name!r} already exists in preset {', '.join(taken)}")
    if name in _disabled(obj):
        raise RosterError(f"{name!r} is listed in disabled_agents; remove it from that list first")


def _disabled(obj):
    value = obj.get("disabled_agents", [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RosterError("config shape: disabled_agents must be an array of strings")
    return [ALIASES.get(item, item) for item in value]


# --- text edits of one JSON object member ---------------------------------------------------


def _ordered(obj):
    return json.dumps(obj, ensure_ascii=False)  # key order matters here, so no sort_keys


def _prove(old_obj, new_text, expected):
    new_obj = skillset._parse(new_text)
    if _ordered(new_obj) != _ordered(expected):
        raise RosterError("config edit proof failed: unrelated data or key order changed")
    return new_obj


def _cut(text, parent, key):
    """Remove one member; return the new text and what is needed to put it back exactly."""
    spans, members = skillset._spans(text)
    entries = members[parent]
    keys = [name for name, _ in entries]
    i = keys.index(key)
    start, end = entries[i][1], spans[(*parent, key)][1]
    if len(entries) == 1:
        opening, closing = spans[parent][0] + 1, spans[parent][1] - 1
        form, cut = "only", (opening, closing)
        before, after = text[opening:start], text[end:closing]
    elif i < len(entries) - 1:
        form, cut = "lead", (start, entries[i + 1][1])
        before, after = "", text[end:entries[i + 1][1]]
    else:
        previous = spans[(*parent, keys[i - 1])][1]
        form, cut = "trail", (previous, end)
        before, after = text[previous:start], ""
    member = text[start:end]
    if before + member + after != text[cut[0]:cut[1]]:
        raise RosterError("config edit proof failed: could not isolate the member")
    return text[:cut[0]] + text[cut[1]:], {"index": i, "form": form, "member": member, "before": before, "after": after}


def _gap_sep(text, spans, parent, entries):
    """The comma-and-whitespace this object puts between members."""
    if len(entries) >= 2:
        return text[spans[(*parent, entries[-2][0])][1]:entries[-1][1]]
    gap = text[spans[parent][0] + 1:entries[0][1]]
    return "," + gap if re.search(r"[\r\n]", gap) else ", "


def _put(text, parent, member, index, cut=None):
    """Insert a member at a position; `cut` (from _cut) restores the exact bytes when it still fits."""
    spans, members = skillset._spans(text)
    entries = members[parent]
    count = len(entries)
    exact = cut["form"] if cut else None
    if count == 0:
        opening, closing = spans[parent][0] + 1, spans[parent][1] - 1
        inner = text[opening:closing]
        if exact == "only" and not inner.strip(" \t\r\n"):
            return text[:opening] + cut["before"] + member + cut["after"] + text[closing:]
        return text[:opening] + member + text[opening:]
    if index < count:
        at = entries[index][1]
        if exact == "lead":
            return text[:at] + member + cut["after"] + text[at:]
        sep = _gap_sep(text, spans, parent, entries)
        return text[:at] + member + sep + text[at:]
    at = spans[(*parent, entries[-1][0])][1]
    if exact == "trail":
        return text[:at] + cut["before"] + member + text[at:]
    return text[:at] + _gap_sep(text, spans, parent, entries) + member + text[at:]


def _render(text, parent, key, value):
    """Format one new member the way this object's other members are written."""
    spans, members = skillset._spans(text)
    entries = members[parent]
    quoted = json.dumps(key, ensure_ascii=False)
    if not entries:
        return quoted + ": " + json.dumps(value, ensure_ascii=False)
    last, last_start = entries[-1]
    if len(entries) == 1:
        gap = text[spans[parent][0] + 1:last_start]
    else:
        gap = text[spans[(*parent, entries[-2][0])][1]:last_start].partition(",")[2]
    style = text[skillset._TOKEN.match(text, last_start).end():spans[(*parent, last)][0]]
    line = re.search(r"(\r\n|\r|\n)([ \t]*)$", gap)
    if not line:
        return quoted + style + json.dumps(value, ensure_ascii=False)
    newline, indent = line.groups()
    outer = re.match(r"[ \t]*", text[text.rfind("\n", 0, spans[parent][0]) + 1:])[0]
    step = indent[len(outer):] if indent.startswith(outer) and len(indent) > len(outer) else "  "
    body = json.dumps(value, indent=step, ensure_ascii=False).replace("\n", newline + indent)
    return quoted + style + body


def _splice(text, span, value):
    return text[:span[0]] + value + text[span[1]:]


# --- the SKILLS.md map -----------------------------------------------------------------------


def _rows(text):
    skillset._map_rows(text)  # one table, no ambiguous rows
    heading = skillset._TABLE.search(text)
    rows, i = [], heading.end()
    while text.startswith("|", i):
        row = skillset._ROW.match(text, i)
        rows.append((row[1], i, row.end(), row.span(2)))
        i = row.end()
    if rows and not text[rows[-1][2] - 1] in "\r\n":
        raise RosterError("SKILLS.md: the last table row must end with a newline")
    return rows, heading.end(), "\r\n" if "\r\n" in heading[0] else "\n"


def _map_apply(text, old, new, hint=None):
    """Edit only the rows whose agent changed; return the new text and a hint to undo it."""
    rows, table_start, newline = _rows(text)
    if {name: text[a:b] for name, _, _, (a, b) in rows} != skillset._projection(old):
        raise RosterError("SKILLS.md does not match the config; nothing was changed")
    before, after = skillset._projection(old), skillset._projection(new)
    edits, undo = [], None
    for position, (name, start, end, (cell_start, cell_end)) in enumerate(rows):
        if name not in after:
            edits.append((start, end, ""))
            undo = {"row": text[start:end], "index": position}
        elif after[name] != before[name]:
            edits.append((cell_start, cell_end, after[name]))
    for name in after:
        if name in before:
            continue
        index = len(rows) if hint is None else min(hint["index"], len(rows))
        row = hint["row"] if hint and hint["row"] == f"| `{name}` | {after[name]} |{newline}" else None
        row = row if row is not None else f"| `{name}` | {after[name]} |{newline}"
        at = rows[index][1] if index < len(rows) else (rows[-1][2] if rows else table_start)
        edits.append((at, at, row))
    updated = text
    for start, end, value in sorted(edits, reverse=True):
        updated = updated[:start] + value + updated[end:]
    new_rows, _, _ = _rows(updated)
    if {name: updated[a:b] for name, _, _, (a, b) in new_rows} != after:
        raise RosterError("SKILLS.md edit proof failed: cells do not match the config")
    return updated, undo


# --- schema ----------------------------------------------------------------------------------


def _find_schema(path=None):
    if path is not None:
        return Path(path)
    found = sorted(SCHEMA_ROOT.glob(SCHEMA_GLOB), key=lambda p: p.stat().st_mtime) if SCHEMA_ROOT.is_dir() else []
    return found[-1] if found else None


def _problems(value, schema, where="$"):
    """Problems of `value` against the JSON-Schema subset the plugin's schema file uses."""
    out = []
    if not isinstance(schema, dict):
        return out
    if "anyOf" in schema and all(_problems(value, s, where) for s in schema["anyOf"]):
        return [f"{where}: matches none of the allowed forms"]
    if "oneOf" in schema and sum(not _problems(value, s, where) for s in schema["oneOf"]) != 1:
        return [f"{where}: must match exactly one of the allowed forms"]
    if "not" in schema and not _problems(value, schema["not"], where):
        out.append(f"{where}: matches a forbidden form")
    if "const" in schema and value != schema["const"]:
        out.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where}: must be one of {schema['enum']}")
    kind = schema.get("type")
    checks = {"string": str, "boolean": bool, "array": list, "object": dict,
              "number": (int, float), "integer": int}
    if kind in checks:
        if not isinstance(value, checks[kind]) or (kind in {"number", "integer"} and isinstance(value, bool)):
            return out + [f"{where}: must be {kind}"]
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{where}: must not be empty")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{where}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            out.append(f"{where}: out of range")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{where}: too few items")
        for i, item in enumerate(value):
            out += _problems(item, schema.get("items"), f"{where}[{i}]")
    if isinstance(value, dict):
        out += [f"{where}: missing {key!r}" for key in schema.get("required", []) if key not in value]
        known = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in known:
                out += _problems(item, known[key], f"{where}.{key}")
            elif extra is False:
                out.append(f"{where}: unknown key {key!r}")
            elif isinstance(extra, dict):
                out += _problems(item, extra, f"{where}.{key}")
    return out


def _agent_schema(schema):
    forms = schema["properties"]["presets"]["additionalProperties"]["oneOf"]
    return next(form["properties"]["agents"]["additionalProperties"] for form in forms
                if "agents" in form.get("properties", {}))


def _schema_check(obj, entries, disabled, schema_path):
    """(problems, what was checked). Checks the changed agent entries and disabled_agents only."""
    path = _find_schema(schema_path)
    if path is None or not path.is_file():
        return [], "not checked: the plugin's schema file was not found"
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        agent = _agent_schema(schema)
        listing = schema["properties"]["disabled_agents"]
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        return [], f"not checked: the schema file could not be used ({exc})"
    problems = []
    for preset, name in entries:
        problems += _problems(obj["presets"][preset][name], agent, f"presets.{preset}.{name}")
    if disabled:
        problems += _problems(obj.get("disabled_agents", []), listing, "disabled_agents")
    checked = (f"{len(entries)} agent entr{'y' if len(entries) == 1 else 'ies'}"
               f"{' and disabled_agents' if disabled else ''} against {path.name} "
               "(types, enums, patterns, required and unknown keys)")
    return problems, checked


# --- files -----------------------------------------------------------------------------------


def _folder(config, root):
    folder = root / f"{date.today().isoformat()}-roster-edit"
    if config.resolve() != CONFIG.resolve():
        folder /= hashlib.sha256(os.fsencode(config.resolve())).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _fsync_dir(folder):
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _backup(config, folder, text, mode):
    backup = folder / config.name
    if not backup.exists():
        with skillset._staged(backup, text, mode) as staged:
            try:
                os.link(staged, backup)
            except FileExistsError:
                pass
        _fsync_dir(folder)
    if not stat.S_ISREG(backup.lstat().st_mode):
        raise RosterError("the daily config backup is not a regular file")
    return str(backup)


def _save_record(folder, record, mode):
    body = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    for number in range(1, 10000):
        path = folder / f"undo-{record['agent']}-{number:03d}.json"
        with skillset._staged(path, body, mode) as staged:
            try:
                os.link(staged, path)
            except FileExistsError:
                continue
        _fsync_dir(folder)
        return str(path)
    raise RosterError("too many undo records in one day")


def _save(config, map_path, backups, text, mode, new_text, map_files, dirs, record=None):
    config, backups = Path(config), Path(backups)

    def prepare():
        folder = _folder(config, backups)
        result = {"backup": _backup(config, folder, text, mode)}
        if record is not None:
            result["record_path"] = _save_record(folder, record, mode)
        return result

    return skillset._commit([*map_files, (config, text, new_text, mode)], dirs, prepare)


def _start(config, map_path):
    return Path(config), Path(map_path)


# --- show ------------------------------------------------------------------------------------


def show(*, config=CONFIG):
    """Every agent per preset: built-in or custom, on or off, and what removing it would do."""
    obj = skillset._parse(skillset._read(Path(config), "config")[0])
    off = set(_disabled(obj))
    seats = skillset._seat_names(obj)

    def row(name, spec):
        model, variant = sources._model_spec(spec)
        builtin = ALIASES.get(name, name) in BUILTIN
        protected = ALIASES.get(name, name) in PROTECTED
        return {"name": name, "kind": "built-in" if builtin else "custom", "off": ALIASES.get(name, name) in off,
                "model": model, "variant": variant, "description": spec.get("description", ""),
                "skills": spec.get("skills", []),
                "remove": None if protected else ("turn off" if builtin else "delete")}

    return {"active_preset": obj["preset"],
            "presets": {preset: [row(name, spec) for name, spec in agents.items()]
                        for preset, agents in obj["presets"].items()},
            "built_in": [{"name": name, "off": name in off,
                          "in_presets": [p for p, agents in obj["presets"].items() if name in agents],
                          "remove": None if name in PROTECTED else "turn off"} for name in BUILTIN],
            "disabled_agents": obj.get("disabled_agents", []),
            "council_seats": sorted(seats)}


# --- add -------------------------------------------------------------------------------------


def _orchestrator_text(name, description, read_only):
    lines = [f"@{name}", f"- Role: {description}",
             "- **Delegate when:** The task matches this role.",
             "- **Do not delegate when:** Another specialist covers it more directly, or its file edits "
             "would collide with another writer lane."]
    if read_only:
        lines.append("- Read-only: it cannot edit files or run shell commands.")
    return "\n".join(lines)


def add(name, model, variant, description, prompt, skills=(), *, read_only=False, presets=None, mcps=(),
        orchestrator_prompt=None, color=None, config=CONFIG, map_path=MAP, backups=BACKUPS, schema=None):
    """Add a custom agent to every (or the named) preset, in step with the SKILLS.md table."""
    config, map_path = _start(config, map_path)
    _clean(description, "description", limit=MAX_DESCRIPTION)
    _clean(prompt, "prompt", limit=MAX_PROMPT, multiline=True)
    orchestrator_prompt = (_orchestrator_text(name, description, read_only) if orchestrator_prompt is None
                           else _clean(orchestrator_prompt, "orchestrator prompt", limit=MAX_PROMPT, multiline=True))
    _word(model, "model", MODEL, "expected provider/model")
    if variant is not None and variant != "":
        skillset._name(variant, "variant")
    if color is not None:
        _word(color, "color", COLOR, "expected #RRGGBB or a theme color")
    skills, mcps = _names(skills, "skill"), _names(mcps, "mcp")
    with skillset._locked(config, map_path) as dirs:
        text, mode = skillset._read(config, "config")
        obj = skillset._parse(text)
        selected = skillset._selected(obj, presets)
        _check_new_name(obj, name, selected)
        installed = skillset._catalogue()
        for skill in skills:
            if skill not in installed:
                raise RosterError(f"skill {skill!r} is not installed")
        entry = {"model": model}
        if variant:
            entry["variant"] = variant
        if color:
            entry["color"] = color
        entry.update(description=description, skills=skills, mcps=mcps,
                     permission=dict(READ_ONLY_PERMISSION if read_only else WRITER_PERMISSION),
                     prompt=prompt, orchestratorPrompt=orchestrator_prompt)
        expected, new_text = copy.deepcopy(obj), text
        for preset in selected:
            parent = ("presets", preset)
            member = _render(new_text, parent, name, entry)
            new_text = _put(new_text, parent, member, len(expected["presets"][preset]))
            expected["presets"][preset][name] = copy.deepcopy(entry)
        new_obj = _prove(obj, new_text, expected)
        problems, checked = _schema_check(new_obj, [(preset, name) for preset in selected], False, schema)
        if problems:
            raise RosterError("the new entry does not fit the plugin's schema: " + "; ".join(problems))
        map_text, map_mode = skillset._read(map_path, "SKILLS.md")
        new_map, _ = _map_apply(map_text, obj, new_obj)
        info = _save(config, map_path, backups, text, mode, new_text,
                     [(map_path, map_text, new_map, map_mode)], dirs)
    return {"agent": name, "kind": "custom", "action": "added", "presets": selected, "changed": True,
            "permission": "read-only" if read_only else "writer", "schema": checked,
            "backup": info["backup"], "message": RESTART}


# --- remove ----------------------------------------------------------------------------------


def _turn_off(name, obj, text, presets):
    if presets is not None:
        raise RosterError(f"{name!r} is built in: it is turned off for every preset, not per preset")
    disabled = _disabled(obj)
    if name in disabled:
        raise RosterError(f"{name!r} is already turned off")
    expected = copy.deepcopy(obj)
    if "disabled_agents" in obj:
        span = skillset._spans(text)[0][("disabled_agents",)]
        new_text = _splice(text, span, skillset._array(text[span[0]:span[1]], [*obj["disabled_agents"], name]))
        expected["disabled_agents"] = [*obj["disabled_agents"], name]
        created = False
    else:
        count = len(skillset._spans(text)[1][()])
        new_text = _put(text, (), _render(text, (), "disabled_agents", [name]), count)
        expected["disabled_agents"] = [name]
        created = True
    return new_text, expected, {"version": 1, "op": "remove", "agent": name, "kind": "built-in",
                                "disabled": {"created": created}}


def remove(name, *, presets=None, config=CONFIG, map_path=MAP, backups=BACKUPS, schema=None):
    """Delete a custom agent's entries, or turn a built-in off; returns and saves an undo record."""
    config, map_path = _start(config, map_path)
    skillset._name(name, "agent")
    with skillset._locked(config, map_path) as dirs:
        text, mode = skillset._read(config, "config")
        obj = skillset._parse(text)
        canonical = ALIASES.get(name, name)
        if canonical in PROTECTED or name.startswith(SEAT_PREFIX) or name in skillset._seat_names(obj):
            raise RosterError(f"{name!r} is the orchestrator, the council or a council seat and cannot be removed here")
        if name in ALIASES:
            raise RosterError(f"{name!r} is an alias of the built-in {canonical!r}; use that name")
        map_files, undo_map = [], None
        if name in BUILTIN:
            new_text, expected, record = _turn_off(name, obj, text, presets)
            new_obj = _prove(obj, new_text, expected)
            where = []
        else:
            selected = skillset._selected(obj, presets)
            where = [preset for preset in selected if name in obj["presets"][preset]]
            if not where:
                raise RosterError(f"agent {name!r} is not in any selected preset")
            expected, new_text, cuts = copy.deepcopy(obj), text, []
            for preset in where:
                new_text, info = _cut(new_text, ("presets", preset), name)
                parsed = json.loads("{" + info["member"] + "}")
                if parsed != {name: obj["presets"][preset][name]}:
                    raise RosterError("config edit proof failed: the removed member is not the agent entry")
                cuts.append({"preset": preset, **info, "value": copy.deepcopy(obj["presets"][preset][name])})
                del expected["presets"][preset][name]
            new_obj = _prove(obj, new_text, expected)
            map_text, map_mode = skillset._read(map_path, "SKILLS.md")
            new_map, undo_map = _map_apply(map_text, obj, new_obj)
            map_files = [(map_path, map_text, new_map, map_mode)]
            record = {"version": 1, "op": "remove", "agent": name, "kind": "custom",
                      "presets": cuts, "map": undo_map}
        problems, _ = _schema_check(new_obj, [], name in BUILTIN, schema)
        info = _save(config, map_path, backups, text, mode, new_text, map_files, dirs, record)
    return {"agent": name, "kind": record["kind"], "action": "turned off" if name in BUILTIN else "deleted",
            "presets": where, "changed": True, "record": record, "record_path": info["record_path"],
            "backup": info["backup"], "warnings": problems, "message": RESTART}


# --- restore ---------------------------------------------------------------------------------


def _check_record(record):
    def bad(why):
        raise RosterError(f"invalid undo record: {why}")

    if not isinstance(record, dict) or record.get("version") != 1 or record.get("op") != "remove":
        bad("expected a version 1 remove record")
    name, kind = record.get("agent"), record.get("kind")
    skillset._name(name, "agent")
    if kind == "built-in":
        if name not in BUILTIN or name in PROTECTED:
            bad("not a built-in agent that can be turned off")
        if not isinstance(record.get("disabled"), dict) or not isinstance(record["disabled"].get("created"), bool):
            bad("missing disabled_agents details")
        return
    if kind != "custom" or name in BUILTIN or name in ALIASES or name.startswith(SEAT_PREFIX):
        bad("not a custom agent")
    entries = record.get("presets")
    if not isinstance(entries, list) or not entries:
        bad("no preset entries")
    seen = set()
    for item in entries:
        if (not isinstance(item, dict) or not isinstance(item.get("preset"), str) or item["preset"] in seen
                or not isinstance(item.get("index"), int) or isinstance(item["index"], bool) or item["index"] < 0
                or item.get("form") not in _FORMS or not isinstance(item.get("value"), dict)
                or not all(isinstance(item.get(k), str) for k in ("member", "before", "after"))):
            bad("malformed preset entry")
        seen.add(item["preset"])
        if not _FORMS[item["form"]](item["before"], item["after"]) or item["member"] != item["member"].strip():
            bad("malformed spacing around an entry")
        try:
            parsed = json.loads("{" + item["member"] + "}", object_pairs_hook=skillset._unique)
        except ValueError:
            bad("an entry is not valid JSON")
        if parsed != {name: item["value"]} or _ordered(parsed[name]) != _ordered(item["value"]):
            bad("an entry does not match its saved value")
        if not sources._model_spec(item["value"]):
            bad("an entry has no usable model")
    hint = record.get("map")
    if hint is not None:
        row = skillset._ROW.fullmatch(hint.get("row", "")) if isinstance(hint, dict) else None
        if (row is None or row[1] != name or not isinstance(hint.get("index"), int) or hint["index"] < 0
                or not hint["row"].startswith("|")):
            bad("malformed SKILLS.md row")


def _load_record(source):
    if isinstance(source, dict):
        return copy.deepcopy(source)
    text, _ = skillset._read(Path(source), "undo record")
    try:
        return json.loads(text, object_pairs_hook=skillset._unique)
    except ValueError as exc:
        raise RosterError(f"undo record is not parseable JSON: {exc}") from exc


def restore(record, *, config=CONFIG, map_path=MAP, backups=BACKUPS, schema=None):
    """Put back exactly what `remove` took out (a record dict or the path of a saved record)."""
    config, map_path = _start(config, map_path)
    record = _load_record(record)
    _check_record(record)
    name = record["agent"]
    with skillset._locked(config, map_path) as dirs:
        text, mode = skillset._read(config, "config")
        obj = skillset._parse(text)
        map_files = []
        if record["kind"] == "built-in":
            names = obj.get("disabled_agents")
            if not isinstance(names, list) or names.count(name) != 1:
                raise RosterError(f"{name!r} is not turned off now; nothing to restore")
            expected = copy.deepcopy(obj)
            if record["disabled"]["created"] and names == [name]:
                new_text, _ = _cut(text, (), "disabled_agents")
                del expected["disabled_agents"]
            else:
                span = skillset._spans(text)[0][("disabled_agents",)]
                rest = [item for item in names if item != name]
                new_text = _splice(text, span, skillset._array(text[span[0]:span[1]], rest))
                expected["disabled_agents"] = rest
            new_obj = _prove(obj, new_text, expected)
            problems, entries = [], []
        else:
            for item in record["presets"]:
                if item["preset"] not in obj["presets"]:
                    raise RosterError(f"preset {item['preset']!r} no longer exists; nothing was restored")
                if name in obj["presets"][item["preset"]]:
                    raise RosterError(f"agent {name!r} already exists in preset {item['preset']}; nothing was restored")
            if name in _disabled(obj):
                raise RosterError(f"{name!r} is listed in disabled_agents; nothing was restored")
            expected, new_text = copy.deepcopy(obj), text
            for item in record["presets"]:
                parent = ("presets", item["preset"])
                new_text = _put(new_text, parent, item["member"], item["index"], item)
                members = list(expected["presets"][item["preset"]].items())
                members.insert(min(item["index"], len(members)), (name, copy.deepcopy(item["value"])))
                expected["presets"][item["preset"]] = dict(members)
            new_obj = _prove(obj, new_text, expected)
            map_text, map_mode = skillset._read(map_path, "SKILLS.md")
            new_map, _ = _map_apply(map_text, obj, new_obj, record.get("map"))
            map_files = [(map_path, map_text, new_map, map_mode)]
            entries = [(item["preset"], name) for item in record["presets"]]
            problems, _ = _schema_check(new_obj, entries, False, schema)
        info = _save(config, map_path, backups, text, mode, new_text, map_files, dirs)
    return {"agent": name, "kind": record["kind"], "action": "restored",
            "presets": [item["preset"] for item in record.get("presets", [])], "changed": True,
            "backup": info["backup"], "warnings": problems, "message": RESTART}


# --- command ---------------------------------------------------------------------------------


def _read_prompt(source):
    if source == "-":
        text = sys.stdin.read(MAX_PROMPT * 4 + 1)
    else:
        text = skillset._read(Path(source), "prompt file")[0]
    return text.rstrip("\n")


def _lines(result):
    if "active_preset" in result:
        out = [f"active preset: {result['active_preset']}"]
        for preset, rows in result["presets"].items():
            out.append(f"{preset}:")
            out += [f"  {r['name']}  {r['kind']}{', off' if r['off'] else ''}  {r['model']}"
                    f"{('#' + r['variant']) if r['variant'] else ''}  (remove: {r['remove'] or 'not allowed'})"
                    for r in rows]
        out.append("built-in: " + ", ".join(f"{b['name']}{' (off)' if b['off'] else ''}" for b in result["built_in"]))
        return out
    out = [f"{result['agent']}: {result['action']}" + (f" in {', '.join(result['presets'])}" if result["presets"] else "")]
    for key, label in (("record_path", "Undo record"), ("backup", "Backup"), ("schema", "Schema")):
        if result.get(key):
            out.append(f"{label}: {result[key]}")
    out += [f"Warning: {w}" for w in result.get("warnings", [])]
    out.append(result["message"])
    return out


def _csv(values):
    return [item for value in values or [] for item in value.split(",") if item] or None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -P -m agentgraph.roster", description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="op", required=True)

    def options(target, prefix):
        target.add_argument("--presets", action="append", dest=prefix + "presets", help="comma-separated; repeatable")
        target.add_argument("--config", type=Path, dest=prefix + "config")
        target.add_argument("--map", type=Path, dest=prefix + "map_path")
        target.add_argument("--backups", type=Path, dest=prefix + "backups")
        target.add_argument("--json", action="store_true", dest=prefix + "json")

    options(parser, "root_")
    for op in ("show", "add", "remove", "restore"):
        sub = commands.add_parser(op, allow_abbrev=False)
        options(sub, "")
        if op in {"add", "remove"}:
            sub.add_argument("name")
        if op == "restore":
            sub.add_argument("record", help="path of a saved undo record")
        if op == "add":
            sub.add_argument("--model", required=True)
            sub.add_argument("--variant", default="")
            sub.add_argument("--description", required=True)
            sub.add_argument("--prompt-file", required=True, help="a file, or - for stdin")
            sub.add_argument("--skills", action="append")
            sub.add_argument("--mcps", action="append")
            sub.add_argument("--color")
            sub.add_argument("--read-only", action="store_true")
    args = parser.parse_args(argv)
    as_json = args.json or args.root_json
    files = {"config": args.config or args.root_config or CONFIG}
    presets = _csv((args.root_presets or []) + (args.presets or []))
    try:
        if args.op == "show":
            result = show(**files)
        else:
            files.update(map_path=args.map_path or args.root_map_path or MAP,
                         backups=args.backups or args.root_backups or BACKUPS)
            if args.op == "add":
                result = add(args.name, args.model, args.variant, args.description, _read_prompt(args.prompt_file),
                             _csv(args.skills) or [], read_only=args.read_only, presets=presets,
                             mcps=_csv(args.mcps) or [], color=args.color, **files)
            elif args.op == "remove":
                result = remove(args.name, presets=presets, **files)
            else:
                result = restore(args.record, **files)
    except SkillsetError as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"roster: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result) if as_json else "\n".join(_lines(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
