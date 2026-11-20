"""Add, remove or restore agents in the plugin's preset file, with undo records."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import copy
import fcntl
import hashlib
from datetime import date
import json
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata

from . import agentfiles, skillset, sources
from . import structure as structure_module
from . import teams as teams_module
from .skillset import BACKUPS, CONFIG, MAP, SkillsetError
from .snapshot_json import (FLOW_FILES_LIMIT, FLOW_ID, FLOWS_FOLDER, STRUCTURE_DIR, TEAMS_FOLDER, flow_fields, read_flow,
                            read_settings)

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
# Names that are not the plugin's to hand out. `user` is the human (sources.py never lists it as an
# agent). build, plan, general (and explore, an alias above) are OpenCode's own agents (the `agent`
# map of @opencode-ai/sdk's Config type); compaction, summary and title are its hidden system
# agents (plugin dist/index.js line 41082, SYSTEM_AGENTS).
HOST_AGENTS = ("user", "build", "plan", "general", "compaction", "summary", "title")
AGENTS_DIR = sources.CONFIG_DIR / "agents"
SEAT_PREFIX = "councillor-"

NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
MODEL = re.compile(r"[^/\s]+/\S+")  # the plugin's provider/model shape (ProviderModelIdSchema)
COLOR = re.compile(r"#[0-9a-fA-F]{6}|primary|secondary|accent|success|warning|error|info")
MAX_DESCRIPTION, MAX_PROMPT = 300, 8000
_STRUCTURE_FILE_LIMIT = 1 << 20  # a structure, flow or group file this tool would ever write is far smaller

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


def _agent_files(agents_dir):
    """The stems of the *.md files in OpenCode's agents folder (read only; none when it is missing)."""
    try:
        with os.scandir(agents_dir) as entries:
            return {Path(entry.name).stem for entry in entries if entry.name.endswith(".md")}
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise RosterError(f"cannot read the agents folder: {exc}") from exc


def _display_names(obj):
    """Every `displayName` the plugin may give an agent, as it reads them (a leading '@' dropped), lowercased: each
    preset entry's and each entry of the preset file's top-level `agents` map. The plugin merges an agent file of
    that name into the agent that has it (the n99 proof: `boss.md` turned the orchestrator's `boss` into a
    subagent with the file's model and instruction)."""
    found = set()
    groups = [*(preset for preset in obj.get("presets", {}).values() if isinstance(preset, dict)), obj.get("agents")]
    for group in groups:
        if not isinstance(group, dict):
            continue
        for entry in group.values():
            value = entry.get("displayName") if isinstance(entry, dict) else None
            if isinstance(value, str):
                found.add(value.strip().removeprefix("@").strip().lower())
    return found


def _strip_jsonc(text):
    """JSON text from JSONC: comments and trailing commas outside strings dropped."""
    out, index, length = [], 0, len(text)
    while index < length:
        char = text[index]
        if char == '"':
            end = index + 1
            while end < length and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[index:end + 1])
            index = end + 1
        elif text.startswith("//", index):
            index = text.find("\n", index) if "\n" in text[index:] else length
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
        else:
            out.append(char)
            index += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _host_names(opencode_dir):
    """The agent names OpenCode's own config gives (the keys of the `agent` map in `opencode.json` or
    `opencode.jsonc`, and their `displayName`s), lowercased; refused when such a file cannot be read."""
    names = set()
    for file in ("opencode.json", "opencode.jsonc"):
        path = Path(opencode_dir) / file
        if not path.exists():
            continue
        try:
            text = skillset._read(path, file)[0]
            data = json.loads(_strip_jsonc(text) if file.endswith("c") else text)
        except (SkillsetError, ValueError) as exc:
            raise RosterError(f"cannot read {file} to check the name against its agents: {exc}") from None
        agents = data.get("agent") if isinstance(data, dict) else None
        if isinstance(agents, dict):
            for key, entry in agents.items():
                names.add(str(key).lower())
                value = entry.get("displayName") if isinstance(entry, dict) else None
                if isinstance(value, str):
                    names.add(value.strip().removeprefix("@").strip().lower())
    return names


def _legacy_files(opencode_dir, name):
    """The files in OpenCode's legacy `agent/` folder that hold `name`."""
    try:
        with os.scandir(Path(opencode_dir) / "agent") as entries:
            return sorted(e.name for e in entries if e.name == name + ".md" or e.name.startswith(name + ".md."))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RosterError(f"cannot read the agent folder: {exc}") from exc


def _check_new_name(obj, name, selected, agents_dir=AGENTS_DIR, opencode_dir=None):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise RosterError("invalid agent name: use a lowercase letter, then lowercase letters, digits, '_' or '-' (64 at most)")
    if name in BUILTIN or name in ALIASES:
        raise RosterError(f"{name!r} is a built-in agent name or alias")
    if name.startswith(SEAT_PREFIX) or name in skillset._seat_names(obj):
        raise RosterError(f"names starting with {SEAT_PREFIX!r} belong to council seats")
    if name in RESERVED:
        raise RosterError(f"{name!r} is a key the plugin reads in presets; choose another name")
    if name in HOST_AGENTS:
        raise RosterError(f"{name!r} is reserved for the user or one of OpenCode's own agents")
    if name in _agent_files(agents_dir):
        raise RosterError(f"{name!r} is already an agent file in the agents folder")
    # OpenCode's own config folder: the one that holds the agents folder, unless given.
    opencode_dir = Path(os.path.abspath(agents_dir)).parent if opencode_dir is None else Path(opencode_dir)
    if name in _display_names(obj):
        raise RosterError(f"{name!r} is the display name of an agent in the presets; choose another name")
    legacy = _legacy_files(opencode_dir, name)
    if legacy:
        raise RosterError(f"{name!r} is already an agent file in OpenCode's agent folder ({', '.join(legacy)})")
    if name in _host_names(opencode_dir):
        raise RosterError(f"{name!r} is an agent in OpenCode's own config (opencode.json); choose another name")
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
        saved = skillset._ROW.fullmatch(hint["row"]) if hint else None
        row = hint["row"] if saved and saved[1] == name and saved[2] == after[name] else None
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


def _fsync_dir(folder):
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save_record(folder, record, mode):
    body = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", record["agent"])[:64]  # never a path part, whatever the name
    for number in range(1, 10000):
        path = folder / f"undo-{slug}-{number:03d}.json"
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
    saved = []

    def prepare():
        result = skillset._backup(config, backups, text, mode, "roster-edit")
        if record is not None:
            folder = skillset._backup_folder(config, backups, "roster-edit")
            saved.append(_save_record(folder, record, mode))
            result["record_path"] = saved[0]
        return result

    try:
        return skillset._commit([*map_files, (config, text, new_text, mode)], dirs, prepare)
    except BaseException:
        for path in saved:  # nothing changed, so the undo record would undo nothing
            Path(path).unlink(missing_ok=True)
        raise


def _start(config, map_path):
    return Path(config), Path(map_path)


# --- show ------------------------------------------------------------------------------------


def _listed_files(obj, agents_dir):
    """The agent files in `agents_dir` that are not preset agents: added (this toolkit's) or hand-written."""
    rows = []
    for name, info in agentfiles.listing(agents_dir).items():
        if any(name in agents for agents in obj["presets"].values()):
            continue
        parsed = info["parsed"]
        rows.append({"name": name, "kind": "added" if info["added"] else "hand-written", "editable": info["editable"],
                     "reason": info["why"], "model": parsed["model"] if parsed else None,
                     "description": parsed["description"] if parsed else None,
                     "skills": agentfiles.skills_of(parsed["permissions"]) if parsed else [],
                     "remove": "delete" if info["editable"] else None})
    return rows


def show(*, config=CONFIG, agents_dir=None):
    """Every agent per preset: built-in or custom, on or off, and what removing it would do; with `agents_dir`,
    also `agent_files`: the agents in OpenCode's agents folder, added (by this toolkit) or hand-written."""
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
            "council_seats": sorted(seats),
            **({"agent_files": _listed_files(obj, agents_dir)} if agents_dir is not None else {})}


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
        orchestrator_prompt=None, color=None, config=CONFIG, map_path=MAP, backups=BACKUPS, schema=None,
        agents_dir=AGENTS_DIR, skills_dir=None):
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
        _check_new_name(obj, name, selected, agents_dir)
        installed = skillset._catalogue(skills_dir)
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
            "backup": info["backup"], "previous": info["previous"], "message": RESTART}


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


def _plan_remove(name, obj, text, presets, map_path):
    """The config text without the agent (or with a built-in turned off), the SKILLS.md edit and the undo record.
    Refuses the orchestrator, the council, its seats and aliases."""
    canonical = ALIASES.get(name, name)
    if canonical in PROTECTED or name.startswith(SEAT_PREFIX) or name in skillset._seat_names(obj):
        raise RosterError(f"{name!r} is the orchestrator, the council or a council seat and cannot be removed here")
    if name in ALIASES:
        raise RosterError(f"{name!r} is an alias of the built-in {canonical!r}; use that name")
    if name in BUILTIN:
        new_text, expected, record = _turn_off(name, obj, text, presets)
        return new_text, _prove(obj, new_text, expected), [], record, []
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
    record = {"version": 1, "op": "remove", "agent": name, "kind": "custom", "presets": cuts, "map": undo_map}
    return new_text, new_obj, [(map_path, map_text, new_map, map_mode)], record, where


def remove(name, *, presets=None, config=CONFIG, map_path=MAP, backups=BACKUPS, schema=None):
    """Delete a custom agent's entries, or turn a built-in off; returns and saves an undo record."""
    config, map_path = _start(config, map_path)
    skillset._agent_name(name)
    with skillset._locked(config, map_path) as dirs:
        text, mode = skillset._read(config, "config")
        obj = skillset._parse(text)
        new_text, new_obj, map_files, record, where = _plan_remove(name, obj, text, presets, map_path)
        problems, _ = _schema_check(new_obj, [], name in BUILTIN, schema)
        info = _save(config, map_path, backups, text, mode, new_text, map_files, dirs, record)
    return {"agent": name, "kind": record["kind"], "action": "turned off" if name in BUILTIN else "deleted",
            "presets": where, "changed": True, "record": record, "record_path": info["record_path"],
            "backup": info["backup"], "previous": info["previous"], "warnings": problems, "message": RESTART}


# --- restore ---------------------------------------------------------------------------------


def _check_record(record):
    def bad(why):
        raise RosterError(f"invalid undo record: {why}")

    if not isinstance(record, dict) or record.get("version") != 1 or record.get("op") != "remove":
        bad("expected a version 1 remove record")
    name, kind = record.get("agent"), record.get("kind")
    skillset._agent_name(name)
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


def _plan_restore(record, obj, text, map_path, schema):
    """The config text with what `record` took out put back, the SKILLS.md edit and schema warnings."""
    name = record["agent"]
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
        _prove(obj, new_text, expected)
        return new_text, [], []
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
    entries = [(item["preset"], name) for item in record["presets"]]
    problems, _ = _schema_check(new_obj, entries, False, schema)
    return new_text, [(map_path, map_text, new_map, map_mode)], problems


def restore(record, *, config=CONFIG, map_path=MAP, backups=BACKUPS, schema=None):
    """Put back exactly what `remove` took out (a record dict or the path of a saved record)."""
    config, map_path = _start(config, map_path)
    record = _load_record(record)
    _check_record(record)
    name = record["agent"]
    with skillset._locked(config, map_path) as dirs:
        text, mode = skillset._read(config, "config")
        obj = skillset._parse(text)
        new_text, map_files, problems = _plan_restore(record, obj, text, map_path, schema)
        info = _save(config, map_path, backups, text, mode, new_text, map_files, dirs)
    return {"agent": name, "kind": record["kind"], "action": "restored",
            "presets": [item["preset"] for item in record.get("presets", [])], "changed": True,
            "backup": info["backup"], "previous": info["previous"], "warnings": problems, "message": RESTART}


# --- the structure folder: an agent's file, the flows through it and its groups ------------------
#
# Adding an agent also gives it a file in the structure folder (flows and groups only accept agents that have
# one); removing it takes out that file, every flow whose path includes it, the group it leads and its place in
# other groups, all under the one undo record. Each change is planned first (the bytes read and the bytes to
# write), then applied with a check that the file is still what was read; any failure puts back what was done.


@contextmanager
def _structure_locked(root):
    """The structure folder, open and locked for one writer at a time (the same flock structure.py and teams.py
    take), or None when there is no such folder."""
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        fd = None
    except OSError as exc:
        raise RosterError(f"cannot open the structure folder {root}: {exc.strerror}") from exc
    if fd is None:
        yield None
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _read_at(folder_fd, name):
    """The bytes of a regular file in a folder, never through a symlink; None when there is no such file."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RosterError(f"structure: {name} is not a regular file ({exc.strerror}); nothing was changed") from exc
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RosterError(f"structure: {name} is not a regular file; nothing was changed")
        return stream.read(_STRUCTURE_FILE_LIMIT + 1)


def _group_folders(root_fd):
    """The group folders of the structure folder, in name order: plain folders, never `flows` or `teams`."""
    folders = []
    for entry in sorted(os.listdir(root_fd)):
        if entry.startswith(".") or entry in (FLOWS_FOLDER, TEAMS_FOLDER):
            continue
        if stat.S_ISDIR(os.lstat(entry, dir_fd=root_fd).st_mode):
            folders.append(entry)
    return folders[:50]


def _group(folder):
    return re.sub(r"^\d+[-_]", "", folder)


def _open_folder(root_fd, folder, create):
    """A folder of the structure folder, opened without following a symlink; `flows` and `teams` are made when
    `create`, a group folder never is. None when it is missing and not made."""
    try:
        return os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    except FileNotFoundError:
        if not create:
            return None
        if folder not in (FLOWS_FOLDER, TEAMS_FOLDER):
            raise RosterError(f"the group folder {folder} is no longer in the structure folder; nothing was changed")
    except OSError as exc:
        raise RosterError(f"structure: {folder} is not a plain folder ({exc.strerror}); nothing was changed") from exc
    os.mkdir(folder, 0o755, dir_fd=root_fd)
    return _open_folder(root_fd, folder, False)


def _listing(root_fd, folder):
    """(name, bytes) of the `<id>.md` files in `flows` or `teams`, in id order."""
    fd = _open_folder(root_fd, folder, False)
    if fd is None:
        return []
    try:
        names = sorted(name for name in os.listdir(fd) if name.endswith(".md") and FLOW_ID.fullmatch(name[:-3]))
        return [(name, data) for name in names if (data := _read_at(fd, name)) is not None]
    finally:
        os.close(fd)


def _structure_removal(root, root_fd, name, with_file):
    """The changes that take `name` out of the structure folder: every flow through it, the group it leads, its
    place in other groups (a group left with no member goes) and, `with_file`, its own file. Each change is
    {"folder", "name", "before": bytes, "after": bytes or None (deleted)}."""
    if root_fd is None:
        return []
    changes = []
    for file, data in _listing(root_fd, FLOWS_FOLDER):
        flow = flow_fields(data)
        if flow is not None and name in flow["path"]:
            if flow["command"] and flow["name"]:  # its command file is outside this record: never left behind
                raise RosterError(f"flow {file[:-3]} through {name} has the command /{flow['name']}; turn it off first "
                                  f"(agentgraph flow command {file[:-3]} --off), then remove {name}. Nothing was changed")
            changes.append({"folder": FLOWS_FOLDER, "name": file, "before": data, "after": None})
    groups = _listing(root_fd, TEAMS_FOLDER)
    if groups:
        try:
            hub = teams_module._hub(teams_module._agents(Path(root)))
        except teams_module.TeamError:
            hub = "orchestrator"
    for file, data in groups:
        team = teams_module.read_team(data)
        if team is None:
            continue
        lead, members, text = team
        if lead != name and name not in members:
            continue
        rest = [member for member in members if member != name]
        after = None
        if lead != name and rest:
            if text == teams_module.team_text(lead, members, hub):  # a generated instruction follows the members
                text = teams_module.team_text(lead, rest, hub)
            after = teams_module.team_file(lead, rest, text)
        changes.append({"folder": TEAMS_FOLDER, "name": file, "before": data, "after": after})
    if with_file:
        for folder in _group_folders(root_fd):
            fd = _open_folder(root_fd, folder, False)
            try:
                data = _read_at(fd, name + ".md")
            finally:
                os.close(fd)
            if data is not None:
                changes.append({"folder": folder, "name": name + ".md", "before": data, "after": None})
    return changes


def _change_file(root_fd, change, check=True):
    fd = _open_folder(root_fd, change["folder"], change["after"] is not None)
    if fd is None:
        if change["before"] is None or not check:
            return
        raise RosterError(f"structure: {change['folder']} is missing; nothing was changed")
    try:
        if check and _read_at(fd, change["name"]) != change["before"]:
            raise RosterError(f"structure: {change['folder']}/{change['name']} changed while this change was being "
                              "made; nothing was changed. Try again.")
        if change["after"] is None:
            try:
                os.unlink(change["name"], dir_fd=fd)
            except FileNotFoundError:
                if check:
                    raise
        else:
            exists = _read_at(fd, change["name"]) is not None
            structure_module._write(fd, change["name"], change["after"], new=not exists)
        os.fsync(fd)
    finally:
        os.close(fd)


def _tidy(root_fd, changes):
    """`flows` and `teams` go once nothing is left in them (as structure.remove_flow and teams.remove do)."""
    for folder in dict.fromkeys(change["folder"] for change in changes):
        if folder in (FLOWS_FOLDER, TEAMS_FOLDER):
            try:
                os.rmdir(folder, dir_fd=root_fd)
            except OSError:
                pass


def _apply(root_fd, changes, failed=None):
    """Make each change after checking the file is still what was read; on a failure, put back what was done
    (the changes that could not be put back are added to `failed`)."""
    done = []
    try:
        for change in changes:
            _change_file(root_fd, change)
            done.append(change)
    except BaseException:
        missed = _revert(root_fd, done)
        if failed is not None:
            failed += missed
        raise
    _tidy(root_fd, changes)


def _revert(root_fd, changes):
    """Undo changes, newest first; returns "folder/name" of each one that could not be undone."""
    missed = []
    for change in reversed(changes):
        try:
            _change_file(root_fd, {**change, "before": change["after"], "after": change["before"]}, check=False)
        except (OSError, RosterError):
            missed.append(f"{change['folder']}/{change['name']}")
    _tidy(root_fd, changes)
    return missed


def _encode(changes):
    return [{"folder": change["folder"], "name": change["name"],
             "before": base64.b64encode(change["before"]).decode("ascii"),
             "after": None if change["after"] is None else base64.b64encode(change["after"]).decode("ascii")}
            for change in changes]


_FOLDER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9 _.-]{0,99}")


def _decode(record):
    """The structure changes of a record, checked; RosterError when they are not what `remove_agent` writes."""
    def bad(why):
        raise RosterError(f"invalid undo record: {why}")

    items = record.get("structure", [])
    name = record["agent"]
    if not isinstance(items, list) or len(items) > 2 * FLOW_FILES_LIMIT + 60:
        bad("malformed structure changes")
    changes = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"folder", "name", "before", "after"}:
            bad("malformed structure change")
        folder, file = item["folder"], item["name"]
        if not isinstance(folder, str) or not _FOLDER.fullmatch(folder) or not isinstance(file, str):
            bad("a structure change names an unusable folder")
        try:
            before = base64.b64decode(item["before"], validate=True)
            after = None if item["after"] is None else base64.b64decode(item["after"], validate=True)
        except (TypeError, ValueError):
            bad("a structure change is not base64")
        if len(before) > _STRUCTURE_FILE_LIMIT or (after is not None and len(after) > _STRUCTURE_FILE_LIMIT):
            bad("a structure change is too large")
        if folder == FLOWS_FOLDER:
            flow = read_flow(before)
            if not FLOW_ID.fullmatch(file[:-3]) or not file.endswith(".md") or flow is None or name not in flow[0] \
                    or after is not None:
                bad("a flow change is not a flow through this agent")
        elif folder == TEAMS_FOLDER:
            team = teams_module.read_team(before)
            if not FLOW_ID.fullmatch(file[:-3]) or not file.endswith(".md") or team is None \
                    or (team[0] != name and name not in team[1]) \
                    or (after is not None and (teams_module.read_team(after) is None or team[0] == name)):
                bad("a group change is not a group of this agent")
        elif file != name + ".md" or after is not None:
            bad("a structure file is not this agent's")
        changes.append({"folder": folder, "name": file, "before": before, "after": after})
    return changes


def _record_path(record, backups):
    """The path of an undo record `remove_agent` saved, checked to be one inside the backups folder."""
    if not isinstance(record, str) or not record or "\0" in record or len(record) > 4096:
        raise RosterError("invalid undo record: expected the record a removal returned")
    root = Path(os.path.abspath(backups))
    path = Path(os.path.abspath(record))
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise RosterError("invalid undo record: it is not one of the backups folder's undo records") from None
    if (len(parts) not in (2, 3) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}-roster-edit", parts[0])
            or (len(parts) == 3 and not re.fullmatch(r"[0-9a-f]{64}", parts[1]))
            or not re.fullmatch(r"undo-[A-Za-z0-9_-]{1,64}-\d{3,4}\.json", parts[-1])):
        raise RosterError("invalid undo record: it is not one of the backups folder's undo records")
    current = root
    for part in parts[:-1]:
        current /= part
        try:
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise RosterError("invalid undo record: its folder is not a plain folder")
        except FileNotFoundError:
            raise RosterError("invalid undo record: there is no such record") from None
    return path


# --- add, remove and restore with the structure folder ---------------------------------------------


def _copied_permission(value, source):
    """A copy's permission: the source's, never wider than `add` gives (no wildcard allow, `task` denied).
    No permission at all (the host default) becomes the writer shape. Returns (permission, notes)."""
    if value is None:
        return dict(WRITER_PERMISSION), []
    if not isinstance(value, dict):
        raise RosterError(f"{source}'s permission is not an object; it cannot be copied")
    rules = ("allow", "ask", "deny")
    out, notes = {}, []
    for key, rule in value.items():
        skillset._name(key, "permission")
        if key == "task":
            out[key] = "deny"
            if rule != "deny":
                notes.append("the copy may not start other agents: task is denied")
            continue
        if key == "*" and rule != "deny":
            notes.append("the copy gets no wildcard allow: the '*' rule was left out")
            continue
        if isinstance(rule, str) and rule in rules:
            out[key] = rule
        elif isinstance(rule, dict) and rule and all(isinstance(k, str) and k and v in rules for k, v in rule.items()):
            out[key] = dict(rule)
        else:
            raise RosterError(f"{source}'s permission for {key!r} is not one this tool can copy")
    out.setdefault("task", "deny")
    return out, notes


def _read_only(permission):
    return all(permission.get(tool) == "deny" for tool in ("edit", "write", "apply_patch"))


def _entries(obj, name, selected, *, model, variant, description, prompt, skills, read_only, copy_of, installed):
    """{preset: entry} for a new agent, and notes. A copy takes model, variant, description, prompt, skills and
    permission from `copy_of` (in each preset, or the active preset's entry where it has none); a field given
    overrides them. Skills given must be installed; a copy may keep skills its source already has."""
    notes, entries = [], {}
    sources_of = {}
    if copy_of is not None:
        skillset._agent_name(copy_of)
        canonical = ALIASES.get(copy_of, copy_of)
        if canonical in PROTECTED or copy_of.startswith(SEAT_PREFIX) or copy_of in skillset._seat_names(obj):
            raise RosterError(f"{copy_of!r} is the orchestrator, the council or a council seat and cannot be copied")
        have = [preset for preset in obj["presets"] if copy_of in obj["presets"][preset]]
        if not have:
            raise RosterError(f"there is no agent {copy_of!r} in the presets to copy")
        fallback = obj["preset"] if obj["preset"] in have else have[0]
        sources_of = {preset: obj["presets"][preset].get(copy_of) or obj["presets"][fallback][copy_of]
                      for preset in selected}
    for skill in skills or []:
        if skill not in installed:
            raise RosterError(f"skill {skill!r} is not installed")
    for preset in selected:
        source = sources_of.get(preset, {})
        entry = {"model": model if model else copy.deepcopy(source.get("model"))}
        if variant:
            entry["variant"] = variant
        elif not model and source.get("variant"):
            entry["variant"] = source["variant"]
        text = description if description else source.get("description")
        about = "description" if copy_of is None or description else f"description (copied from {copy_of})"
        entry["description"] = _clean(text, about, limit=MAX_DESCRIPTION)
        if skills is not None:
            entry["skills"] = list(skills)
        else:
            kept = source.get("skills", [])
            if "*" in kept:
                raise RosterError(f"{copy_of!r} has every skill ('*'); list the skills the copy should have")
            entry["skills"] = _names(kept, "skill")
        entry["mcps"] = []
        if read_only is not None:
            entry["permission"] = dict(READ_ONLY_PERMISSION if read_only else WRITER_PERMISSION)
        elif copy_of is not None:
            entry["permission"], more = _copied_permission(source.get("permission"), copy_of)
            notes += [note for note in more if note not in notes]
        else:
            entry["permission"] = dict(WRITER_PERMISSION)
        body = prompt if prompt else source.get("prompt")
        about = "prompt" if copy_of is None or prompt else f"prompt (copied from {copy_of})"
        entry["prompt"] = _clean(body, about, limit=MAX_PROMPT, multiline=True)
        entry["orchestratorPrompt"] = _orchestrator_text(name, entry["description"], _read_only(entry["permission"]))
        if not sources._model_spec(entry):
            raise RosterError("model is required (expected provider/model)")
        entries[preset] = entry
    return entries, notes


def _agent_file(root_fd, name, copy_of, description):
    """The change that gives a new agent its file in the structure folder: in `copy_of`'s group folder (with its
    `pool` and `escalates_to` settings), else the `build` group, else the first group that is not the hub.
    None without a structure folder; refused when the name already has a file there."""
    if root_fd is None:
        return None
    folders = _group_folders(root_fd)
    if not folders:
        return None
    found = {}
    for folder in folders:
        fd = _open_folder(root_fd, folder, False)
        try:
            for stem in (name, copy_of):
                if stem and stem not in found and (data := _read_at(fd, stem + ".md")) is not None:
                    found[stem] = (folder, data)
        finally:
            os.close(fd)
    if name in found:
        raise RosterError(f"{name!r} already has a file in the structure folder ({found[name][0]}/{name}.md)")
    settings = []
    if copy_of in found:
        target = found[copy_of][0]
        settings = [(key, value) for key, value in read_settings(found[copy_of][1])[0] if key in ("pool", "escalates_to")]
    else:
        target = next((f for f in folders if _group(f) == "build"), None) \
            or next((f for f in folders if _group(f) != "hub"), folders[0])
    head = "".join(f"{key}: {value}\n" for key, value in settings)
    body = (head + "\n" if head else "") + description + "\n"
    return {"folder": target, "name": name + ".md", "before": None, "after": body.encode("utf-8")}


def _optional(value, kind):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise RosterError(f"{kind} must be text")
    return value


def _source_spec(obj, name):
    """A preset agent's entry: in the active preset, else the first preset that has it; None when none has."""
    have = [preset for preset in obj["presets"] if name in obj["presets"][preset]]
    if not have:
        return None
    return obj["presets"][obj["preset"] if obj["preset"] in have else have[0]][name]


def _model_of(spec):
    found = sources._model_spec(spec) if isinstance(spec, dict) else None
    if not found:
        return None
    return found[0] + ("#" + found[1] if found[1] else "")


def _with_variant(model, variant):
    base = model.partition("#")[0]
    return base + ("#" + variant if variant else "")


def _plan_added(obj, name, *, model, variant, description, prompt, skills, read_only, copy_of, template, agents_dir,
                installed):
    """(description, model, rules, text, notes) of a new added agent: from a template, a copy or the fields given.
    A field given overrides what the template or copy brings; permissions are never wider than the source's."""
    notes = []
    if copy_of is not None and template is not None:
        raise RosterError("give either copy_of or template, not both")
    if template is not None and template not in agentfiles.TEMPLATES:
        raise RosterError(f"unknown template {template!r}: use {' or '.join(agentfiles.TEMPLATES)}")
    base = {"description": None, "model": None, "rules": None, "text": None}
    source_skills = []
    if template == "verifier":
        spec = _source_spec(obj, "verifier")
        if spec is None:
            raise RosterError("there is no default verifier in the presets to start from")
        base["model"] = _model_of(spec)
        base["rules"], more = agentfiles.from_plugin(spec.get("permission"), [])
        notes += more
        base["description"], base["text"] = agentfiles.VERIFIER_DESCRIPTION, agentfiles.VERIFIER_TEXT
    elif copy_of is not None:
        skillset._agent_name(copy_of)
        canonical = ALIASES.get(copy_of, copy_of)
        if canonical in PROTECTED or copy_of.startswith(SEAT_PREFIX) or copy_of in skillset._seat_names(obj):
            raise RosterError(f"{copy_of!r} is the orchestrator, the council or a council seat and cannot be copied")
        spec = _source_spec(obj, copy_of)
        files = agentfiles.listing(agents_dir)
        if spec is not None:
            base["model"], base["description"] = _model_of(spec), spec.get("description")
            base["text"] = spec.get("prompt")
            source_skills = list(spec.get("skills") or [])
            base["rules"], more = agentfiles.from_plugin(spec.get("permission"), source_skills)
            notes += more
        elif copy_of in files and files[copy_of]["parsed"] is not None:
            parsed = files[copy_of]["parsed"]
            base.update(model=parsed["model"], description=parsed["description"], text=parsed["text"],
                        rules=agentfiles.narrow(parsed["permissions"]))
            source_skills = agentfiles.skills_of(parsed["permissions"])
        elif copy_of in files:
            raise RosterError(files[copy_of]["why"] + " It cannot be copied.")
        else:
            raise RosterError(f"there is no agent {copy_of!r} to copy")
        if base["text"] is None or not str(base["text"]).strip():
            base["text"] = f"You are {name}, a copy of {copy_of}."
    else:
        for value, kind in ((model, "model"), (description, "description"), (prompt, "prompt")):
            if value is None:
                raise RosterError(f"{kind} is required (or start from a template or a copy of an agent)")
    if model is not None:
        base["model"] = model if "#" in model or not variant else _with_variant(model, variant)
    if variant is not None:
        if base["model"] is None:
            raise RosterError("model is required (expected provider/model)")
        base["model"] = _with_variant(base["model"], variant)
    if base["model"] is None:
        raise RosterError("model is required (expected provider/model)")
    for value, key in ((description, "description"), (prompt, "text")):
        if value is not None:
            base[key] = value
    if base["description"] is None:
        base["description"] = f"A copy of {copy_of}." if copy_of else "An added agent."
    if read_only is not None or base["rules"] is None:
        shape = READ_ONLY_PERMISSION if read_only else WRITER_PERMISSION
        base["rules"], _ = agentfiles.from_plugin(shape, source_skills)
    for skill in skills or []:
        if skill not in installed:
            raise RosterError(f"skill {skill!r} is not installed")
    if skills is not None:
        base["rules"] = agentfiles.with_skills(base["rules"], skills)
    return base["description"], base["model"], base["rules"], base["text"], notes


def add_agent(name, *, model=None, variant=None, description=None, prompt=None, skills=None, read_only=None,
              copy_of=None, template=None, config=CONFIG, map_path=MAP, backups=BACKUPS, structure=None,
              schema=None, agents_dir=AGENTS_DIR, skills_dir=None):
    """Add an agent as one OpenCode agent file (`NAME.md` in `agents_dir`, live at once; see agentfiles) and
    give it a file in the structure folder, as one step. It starts from `template` ("verifier": the default
    verifier's model and permissions, a checking instruction; "blank"), a copy of `copy_of` (a preset agent or an
    added one: model, variant, description, instruction, skills and permissions) or the fields given; a field
    given overrides. The preset file and SKILLS.md are read, never written."""
    config, map_path = _start(config, map_path)
    model, variant, copy_of = (_optional(model, "model"), _optional(variant, "variant"), _optional(copy_of, "copy_of"))
    description, prompt = _optional(description, "description"), _optional(prompt, "prompt")
    template = _optional(template, "template")
    if model is not None:
        _word(model, "model", agentfiles.MODEL, "expected provider/model")
    if variant is not None:
        skillset._name(variant, "variant")
        if not re.fullmatch(r"[^#\s\"\\/]+", variant):
            raise RosterError("invalid variant: no spaces, '#', '/', '\"' or '\\'")
    if read_only is not None and not isinstance(read_only, bool):
        raise RosterError("read_only must be true or false")
    if skills is not None:
        skills = _names(skills, "skill")
    agentfiles.check_name(name)
    obj = skillset._parse(skillset._read(config, "config")[0])
    _check_new_name(obj, name, list(obj["presets"]), agents_dir)
    # An agent file turned off by hand (`NAME.md.disabled`) still holds its name.
    try:
        held = sorted(entry for entry in os.listdir(agents_dir) if entry.startswith(name + ".md."))
    except FileNotFoundError:
        held = []
    except OSError as exc:
        raise RosterError(f"cannot read the agents folder: {exc}") from exc
    if held:
        raise RosterError(f"{name!r} is already an agent file in the agents folder ({', '.join(held)})")
    text_description, text_model, rules, text, notes = _plan_added(
        obj, name, model=model, variant=variant, description=description, prompt=prompt, skills=skills,
        read_only=read_only, copy_of=copy_of, template=template, agents_dir=agents_dir,
        installed=set(skillset._catalogue(skills_dir)) if skills else set())
    # The final model, whatever it came from (a copy's source, a variant): the shape OpenCode reads.
    if not isinstance(text_model, str) or not agentfiles.MODEL.fullmatch(text_model):
        raise RosterError("model must be provider/model (optionally #variant), with no spaces or quotes")
    text_description = agentfiles._clean_text(text_description, "description", agentfiles.MAX_DESCRIPTION, single=True)
    # The structure file starts with the description when the source has no settings: a description that reads
    # as a setting (`seats: impl-`) would become one.
    if read_settings((text_description + "\n").encode("utf-8"))[0]:
        raise RosterError("the description may not start like a structure setting (pool:, escalates_to: or seats:)")
    data = agentfiles.render(text_description, text_model, rules,
                             agentfiles._clean_text(text, "the instruction", agentfiles.MAX_TEXT, single=False))
    root = Path(structure) if structure is not None else STRUCTURE_DIR
    with _structure_locked(root) as root_fd:
        file = _agent_file(root_fd, name, copy_of, agentfiles.parse(data)["description"])
        changes = [file] if file else []
        _apply(root_fd, changes)
        try:
            agentfiles.replace(name, None, data, agents_dir=agents_dir)
        except BaseException:
            _revert(root_fd, changes)
            raise
    parsed = agentfiles.parse(data)
    return {"agent": name, "kind": "added", "action": "added", "presets": [], "changed": True,
            "file": str(Path(os.path.abspath(agents_dir)) / f"{name}.md"), "copy_of": copy_of, "template": template,
            "model": parsed["model"], "skills": agentfiles.skills_of(parsed["permissions"]),
            "permission": "read-only" if agentfiles.read_only(parsed["permissions"]) else "writer",
            "structure_file": f"{file['folder']}/{file['name']}" if file else None,
            "warnings": notes, "message": agentfiles.LIVE}


def _removed(changes, name):
    flows = [c["name"][:-3] for c in changes if c["folder"] == FLOWS_FOLDER]
    led = [c["name"][:-3] for c in changes if c["folder"] == TEAMS_FOLDER
           and teams_module.read_team(c["before"])[0] == name]
    member = [c["name"][:-3] for c in changes if c["folder"] == TEAMS_FOLDER and c["name"][:-3] not in led]
    files = [f"{c['folder']}/{c['name']}" for c in changes if c["folder"] not in (FLOWS_FOLDER, TEAMS_FOLDER)]
    return {"flows": flows, "teams": led, "memberships": member, "files": files}


def _remove_added(name, *, backups, root, agents_dir):
    """Delete an added agent's file with its flows, group and memberships, under one undo record."""
    with _structure_locked(root) as root_fd:
        with agentfiles._folder(agents_dir) as fd:
            data = agentfiles._read_at(fd, name + ".md")
        if data is None or not agentfiles.is_added(data):
            raise RosterError(f"there is no added agent {name!r}")
        if agentfiles.parse(data) is None:
            raise RosterError(agentfiles.HAND_CHANGED.format(name=f"{name}.md"))
        changes = _structure_removal(root, root_fd, name, with_file=True)
        record = agentfiles.save_record(backups, "remove", name, {
            "version": 1, "op": "remove", "agent": name, "kind": "added", "file": agentfiles.b64(data),
            "structure": _encode(changes)})
        failed = []
        try:
            _apply(root_fd, changes, failed)
            try:
                agentfiles.replace(name, data, None, agents_dir=agents_dir)
            except BaseException:
                failed += _revert(root_fd, changes)
                raise
        except BaseException as exc:
            if not failed:
                agentfiles.drop_record(record)
                raise
            # Some of what was taken could not be put back: the record is the only copy of it, so it stays.
            raise RosterError(f"the removal of {name!r} failed ({exc}) and {', '.join(failed)} could not be put "
                              f"back; the undo record {record} keeps them") from exc
    agentfiles.prune_records(record)
    return {"agent": name, "kind": "added", "action": "deleted", "presets": [], "changed": True, "record": record,
            "removed": _removed(changes, name), "warnings": [], "message": agentfiles.LIVE}


def remove_agent(name, *, config=CONFIG, map_path=MAP, backups=BACKUPS, structure=None, schema=None,
                 agents_dir=AGENTS_DIR):
    """Remove an agent for real, as one undoable step, with every flow whose path includes it, the group it leads
    and its place in other groups. An added agent's file is deleted (live at once); a custom preset agent's
    entries and structure file go, a built-in is turned off (after a restart). A hand-written agent file is
    refused. Returns `record`, the path of the one undo record `restore_agent` takes."""
    config, map_path = _start(config, map_path)
    skillset._agent_name(name)
    root = Path(structure) if structure is not None else STRUCTURE_DIR
    files = agentfiles.listing(agents_dir)
    if name in files and files[name]["added"]:
        if not files[name]["editable"]:
            raise RosterError(files[name]["why"])
        return _remove_added(name, backups=backups, root=root, agents_dir=agents_dir)
    with _structure_locked(root) as root_fd:
        with skillset._locked(config, map_path) as dirs:
            text, mode = skillset._read(config, "config")
            obj = skillset._parse(text)
            if name in files and _source_spec(obj, name) is None and name not in BUILTIN:
                raise RosterError(files[name]["why"])
            new_text, new_obj, map_files, record, where = _plan_remove(name, obj, text, None, map_path)
            changes = _structure_removal(root, root_fd, name, with_file=name not in BUILTIN)
            record["structure"] = _encode(changes)
            problems, _ = _schema_check(new_obj, [], name in BUILTIN, schema)
            _apply(root_fd, changes)
            try:
                info = _save(config, map_path, backups, text, mode, new_text, map_files, dirs, record)
            except BaseException:
                _revert(root_fd, changes)
                raise
    return {"agent": name, "kind": record["kind"], "action": "turned off" if name in BUILTIN else "deleted",
            "presets": where, "changed": True, "record": info["record_path"], "removed": _removed(changes, name),
            "backup": info["backup"], "previous": info["previous"], "warnings": problems, "message": RESTART}


def _structure_back(root_fd, root, removed):
    """The changes that put back what a removal took from the structure folder, checked to be still possible."""
    changes = [{**change, "before": change["after"], "after": change["before"]} for change in reversed(removed)]
    if changes and root_fd is None:
        raise RosterError(f"no structure folder at {root}; nothing was restored")
    for change in changes:
        fd = _open_folder(root_fd, change["folder"], False)
        current = None
        if fd is not None:
            try:
                current = _read_at(fd, change["name"])
            finally:
                os.close(fd)
        if current != change["before"]:
            what = "group" if change["folder"] == TEAMS_FOLDER else (
                "flow" if change["folder"] == FLOWS_FOLDER else "structure file")
            raise RosterError(f"the {what} {change['folder']}/{change['name']} was changed or added since the "
                              "agent was removed; nothing was restored")
    return changes


def _restore_added(record, *, backups, root, agents_dir):
    kind, name, data = agentfiles.load_record(record, backups)
    if kind == "text":
        return {"agent": name, "kind": "added", "action": "restored the instruction",
                **agentfiles.restore_text(record, agents_dir=agents_dir, backups=backups)}
    if set(data) != {"version", "op", "agent", "kind", "file", "structure"} or data["kind"] != "added":
        raise RosterError("invalid undo record: not an added agent's removal")
    file = agentfiles.unb64(data["file"], "the agent file")
    if file is None or not agentfiles.is_added(file) or agentfiles.parse(file) is None:
        raise RosterError("invalid undo record: the file is not an added agent's")
    removed = _decode(data)
    with _structure_locked(root) as root_fd:
        with agentfiles._folder(agents_dir, create=True) as fd:
            taken = agentfiles._taken(fd, name)
        if taken:
            raise RosterError(f"{name!r} is already an agent file in the agents folder ({', '.join(taken)}); "
                              "nothing was restored")
        changes = _structure_back(root_fd, root, removed)
        agentfiles.replace(name, None, file, agents_dir=agents_dir)
        try:
            _apply(root_fd, changes)
        except BaseException:
            agentfiles.replace(name, file, None, agents_dir=agents_dir)
            raise
    return {"agent": name, "kind": "added", "action": "restored", "presets": [], "changed": True,
            "restored": _removed(removed, name), "warnings": [], "message": agentfiles.LIVE}


def restore_agent(record, *, config=CONFIG, map_path=MAP, backups=BACKUPS, structure=None, schema=None,
                  agents_dir=AGENTS_DIR):
    """Put back everything a removal took out, from the record path it returned: an added agent's file byte for
    byte, or the preset entries and the SKILLS.md row; with the structure file, the flows with their text and the
    groups. Refused (and nothing changed) when any of it was changed or taken again since. The record of an
    instruction change restores the earlier instruction exactly."""
    config, map_path = _start(config, map_path)
    root = Path(structure) if structure is not None else STRUCTURE_DIR
    if isinstance(record, str) and "-agent-files" + os.sep in record:
        return _restore_added(record, backups=backups, root=root, agents_dir=agents_dir)
    path = _record_path(record, backups)
    data = _load_record(path)
    _check_record(data)
    removed = _decode(data)
    with _structure_locked(root) as root_fd:
        with skillset._locked(config, map_path) as dirs:
            text, mode = skillset._read(config, "config")
            obj = skillset._parse(text)
            new_text, map_files, problems = _plan_restore(data, obj, text, map_path, schema)
            changes = _structure_back(root_fd, root, removed)
            _apply(root_fd, changes)
            try:
                info = _save(config, map_path, backups, text, mode, new_text, map_files, dirs)
            except BaseException:
                _revert(root_fd, changes)
                raise
    name = data["agent"]
    return {"agent": name, "kind": data["kind"], "action": "restored",
            "presets": [item["preset"] for item in data.get("presets", [])], "changed": True,
            "restored": _removed(removed, name), "backup": info["backup"], "previous": info["previous"],
            "warnings": problems, "message": RESTART}


def read_agent(name, *, agents_dir=AGENTS_DIR):
    """{"name", "description", "model", "text", "skills", "permissions"} of an added agent (its instruction is the
    text; `permissions` is what it may do: agentfiles.summary)."""
    skillset._agent_name(name)
    shown = agentfiles.read(name, agents_dir=agents_dir)
    return {"agent": name, "action": "read", **{key: shown[key] for key in ("name", "description", "model", "text",
                                                                            "skills", "permissions")}}


def text_agent(name, text, *, agents_dir=AGENTS_DIR, backups=BACKUPS):
    """Replace an added agent's instruction; `record` undoes it exactly (restore_agent)."""
    skillset._agent_name(name)
    done = agentfiles.set_text(name, text, agents_dir=agents_dir, backups=backups)
    return {"agent": name, "action": "changed the instruction", **done}


# --- the snapshot's fields -------------------------------------------------------------------------

PROTECTED_REASON = "The orchestrator, the council and council seats cannot be removed."
HOST_REASON = "This is one of OpenCode's own agents, not one in the plugin's presets."
OUTSIDE_REASON = "This agent is not in the plugin's presets (it comes from OpenCode's agents folder); remove it there."


def _models(obj):
    order = [obj["preset"], *[preset for preset in obj["presets"] if preset != obj["preset"]]]
    seen = {}
    for preset in order:
        for spec in obj["presets"][preset].values():
            found = sources._model_spec(spec)
            if found:
                seen.setdefault(found[0] + ("#" + found[1] if found[1] else ""), None)
    return list(seen)


def annotate(document, *, config=CONFIG, map_path=MAP, agents_dir=AGENTS_DIR):
    """Add to a snapshot document, in place: per agent `added` (an agent file this toolkit wrote), whether Delete
    can remove it (`removable`, `removable_reason`, "" when it can) and, for an added agent, its `skills`; and
    `roster` ({"editable", "why" when not, "models": the distinct "provider/model#variant" the presets and added
    agents use}). A hand-written agent file is listed but read-only, with the reason. Reads only; never raises."""
    obj, why = None, ""
    try:
        obj = skillset._parse(skillset._read(Path(config), "config")[0])
        map_text = skillset._read(Path(map_path), "SKILLS.md")[0]
        if skillset._map_cells(map_text, skillset._map_rows(map_text)) != skillset._projection(obj):
            why = "SKILLS.md does not match the plugin's presets; bring them in step before adding or removing agents."
    except (SkillsetError, OSError, ValueError, KeyError, TypeError) as exc:
        why = why or f"The plugin's config could not be read: {exc}"
    files = agentfiles.listing(agents_dir)
    models = []
    if obj is not None:
        try:
            models = _models(obj)
        except (KeyError, TypeError, AttributeError):
            models = []
    for info in files.values():
        if info["parsed"] and info["parsed"]["model"] not in models:
            models.append(info["parsed"]["model"])
    document["roster"] = {"editable": not why, **({"why": why} if why else {}), "models": models}
    seats = skillset._seat_names(obj) if obj is not None else set()
    try:
        off = set(_disabled(obj)) if obj is not None else set()
    except RosterError:
        off = set()
    for agent in document.get("agents", []):
        name = agent.get("name") if isinstance(agent.get("name"), str) else ""
        canonical = ALIASES.get(name, name)
        info = files.get(name)
        in_presets = obj is not None and any(name in agents for agents in obj["presets"].values())
        agent["added"] = bool(info and info["added"]) and not in_presets
        if agent["added"]:
            reason = info["why"]
            if info["parsed"]:
                agent["skills"] = agentfiles.skills_of(info["parsed"]["permissions"])
        elif canonical in PROTECTED or name.startswith(SEAT_PREFIX) or name in seats:
            reason = PROTECTED_REASON
        elif info and not in_presets:
            reason = info["why"]
        elif why:
            reason = why
        elif name in ALIASES:
            reason = f"{name!r} is an alias of the built-in {canonical!r}."
        elif name in BUILTIN:
            reason = "" if name not in off else f"{name!r} is already turned off."
        elif in_presets:
            reason = ""
        elif name in HOST_AGENTS:
            reason = HOST_REASON
        else:
            reason = OUTSIDE_REASON
        agent["removable"] = not reason
        agent["removable_reason"] = reason
    return document


# --- the `agent` command ---------------------------------------------------------------------------


def agent_main(argv=None):
    """`agentgraph agent list|show|add|text|remove|restore`: exits 0 on success, 1 when refused, 2 on a usage
    error."""
    parser = argparse.ArgumentParser(prog="agentgraph agent", description="Add, change, remove or restore an agent.",
                                     allow_abbrev=False)
    commands = parser.add_subparsers(dest="op", required=True)

    def options(target, prefix):
        target.add_argument("--config", type=Path, dest=prefix + "config", help="the plugin's preset file")
        target.add_argument("--map", type=Path, dest=prefix + "map_path", help="SKILLS.md")
        target.add_argument("--backups", type=Path, dest=prefix + "backups", help="where backups and undo records go")
        target.add_argument("--structure", type=Path, dest=prefix + "structure", help="the structure folder")
        target.add_argument("--agents", type=Path, dest=prefix + "agents", help="OpenCode's agents folder")
        target.add_argument("--skills-dir", type=Path, dest=prefix + "skills_dir", help="the skills folder")
        target.add_argument("--json", action="store_true", dest=prefix + "json")

    options(parser, "root_")
    for op in ("list", "show", "add", "text", "remove", "restore"):
        sub = commands.add_parser(op, allow_abbrev=False)
        options(sub, "")
        if op in {"show", "add", "text", "remove"}:
            sub.add_argument("name")
        if op == "restore":
            sub.add_argument("record", help="the undo record a removal or an instruction change printed")
        if op == "text":
            sub.add_argument("--text-file", required=True, help="the instruction from a file, or - for stdin")
        if op == "add":
            sub.add_argument("--model")
            sub.add_argument("--variant")
            sub.add_argument("--description")
            sub.add_argument("--prompt-file", help="the instruction from a file, or - for stdin")
            sub.add_argument("--skills", action="append", help="comma-separated; repeatable")
            sub.add_argument("--read-only", action=argparse.BooleanOptionalAction, default=None)
            sub.add_argument("--copy-of", metavar="AGENT")
            sub.add_argument("--template", choices=agentfiles.TEMPLATES)
    args = parser.parse_args(argv)
    pick = lambda key, default: getattr(args, key) or getattr(args, "root_" + key) or default
    as_json = args.json or args.root_json
    files = {"config": pick("config", CONFIG)}
    agents_dir = pick("agents", AGENTS_DIR)
    try:
        if args.op == "list":
            result = show(**files, agents_dir=agents_dir)
        elif args.op == "show":
            result = read_agent(args.name, agents_dir=agents_dir)
        elif args.op == "text":
            result = text_agent(args.name, _read_prompt(args.text_file), agents_dir=agents_dir,
                                backups=pick("backups", BACKUPS))
        else:
            files.update(map_path=pick("map_path", MAP), backups=pick("backups", BACKUPS),
                         structure=pick("structure", None), agents_dir=agents_dir)
            if args.op == "add":
                skills = None if args.skills is None else _csv(args.skills) or []
                result = add_agent(args.name, model=args.model, variant=args.variant, description=args.description,
                                   prompt=_read_prompt(args.prompt_file) if args.prompt_file else None, skills=skills,
                                   read_only=args.read_only, copy_of=args.copy_of, template=args.template,
                                   skills_dir=pick("skills_dir", None), **files)
            elif args.op == "remove":
                result = remove_agent(args.name, **files)
            else:
                result = restore_agent(str(args.record), **files)
    except (SkillsetError, OSError, UnicodeError) as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"agentgraph agent: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(result))
    elif args.op == "list":
        print("\n".join(_lines(result)))
    elif args.op == "show":
        print(f"{result['name']}: {result['description']}\nmodel: {result['model']}\n"
              f"skills: {', '.join(result['skills']) or 'none'}\n\n{result['text']}")
    else:
        out = [f"{result['agent']}: {result['action']}"]
        gone = result.get("removed") or result.get("restored") or {}
        for key, label in (("flows", "flows"), ("teams", "groups led"), ("memberships", "group memberships"),
                           ("files", "structure files")):
            if gone.get(key):
                out.append(f"  {label}: {', '.join(gone[key])}")
        if result.get("file"):
            out.append(f"Agent file: {result['file']}")
        if result.get("structure_file"):
            out.append(f"Structure file: {result['structure_file']}")
        if result.get("record"):
            out.append(f"Undo record: {result['record']}")
        out += [f"Warning: {w}" for w in result.get("warnings", [])]
        out.append(result["message"])
        print("\n".join(out))
    return 0


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
        if "agent_files" in result:
            out.append("agent files:" + ("" if result["agent_files"] else " none"))
            out += [f"  {r['name']}  {r['kind']}" + (f"  {r['model']}" if r["model"] else "")
                    + ("" if r["editable"] else f"  (read-only: {r['reason']})") for r in result["agent_files"]]
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
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["agent"]:
        return agent_main(argv[1:])
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
            sub.add_argument("--agents", type=Path, help="OpenCode's agents folder (default: the real one)")
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
                             mcps=_csv(args.mcps) or [], color=args.color,
                             agents_dir=args.agents or AGENTS_DIR, **files)
            elif args.op == "remove":
                result = remove(args.name, presets=presets, **files)
            else:
                result = restore(args.record, **files)
    except (SkillsetError, OSError) as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"roster: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result) if as_json else "\n".join(_lines(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
