"""Which MCP servers each agent may use, for the view: read only, and only names and states leave the settings.

The settings that name servers hold commands, arguments, environment values, URLs and headers, which can carry keys.
This module reads them and keeps nothing but, per server: its name (a plain name, `names.MCP`; any other is dropped),
where it is declared (`plugin` or `opencode`), `local` or `remote` when the file says exactly that, and on or off.
Nothing else is copied, quoted, logged or put in an error: a file that cannot be read gives no servers and no text.

The rule for what an agent may use is the plugin's own (oh-my-opencode-slim 3.0.6, `dist/index.js`):
- the servers there are (`availableMcpNames`, `buildAgentRegistry`, line 30755): the plugin's built-in ones
  (`createBuiltinMcps`, line 59336: `context7` and `gh_grep`, except those in `disabled_mcps`) and the keys of the
  `mcp` map of OpenCode's own `opencode.json` (`{ ...pluginMcps, ...host.mcp }`; a host server of a built-in's name
  replaces it, `getOverriddenBuiltinMcpKeys`, line 70845);
- an agent's list is its `mcps` in the merged agent overrides (`getAgentMcpList`, line 27295: the preset with its
  `extends` chain, under the top-level `agents`; an agent with no `mcps` takes `DEFAULT_AGENT_MCPS`, line 27271,
  and a custom agent none);
- `parseList` (line 27281) turns that list into names: `!x` entries deny, `!*` denies everything, `*` allows every
  server there is except the denied ones, otherwise the allowed names that exist. The plugin then gives each agent
  `allow` on `NAME_*` tools for those servers and `deny` on the others (line 30927), host servers included.
An agent the plugin does not manage (OpenCode's own `build` or `plan`, an agent file) has no list here: `mcps` is null.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import names, sources

MAX_FILE = 1 << 20
MAX_SERVERS = 200

# dist/index.js line 27271 (DEFAULT_AGENT_MCPS) and line 59332 (allBuiltinMcps).
DEFAULT_AGENT_MCPS = {
    "orchestrator": ["*", "!context7"], "designer": [], "oracle": [], "explorer": ["context7", "gh_grep"],
    "fixer": [], "observer": [], "council": [], "councillor": [],
}
BUILTIN_SERVERS = ("context7", "gh_grep")
# AGENT_ALIASES (line 24337): a preset may still use the old names.
ALIASES = {"explore": "explorer", "librarian": "explorer", "frontend-ui-ux-engineer": "designer"}
BUILTIN_AGENTS = tuple(DEFAULT_AGENT_MCPS)


def _read(path) -> dict | None:
    """A JSON object from `path`, or None; never an error text (the file may hold secrets)."""
    try:
        with open(path, "rb") as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            return None
        value = json.loads(data.decode("utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _deep_merge(base, override):  # the plugin's deepMerge (line 26255): records merge, anything else is replaced
    if not isinstance(base, dict):
        return override
    if not isinstance(override, dict):
        return base
    merged = dict(base)
    for key, value in override.items():
        merged[key] = _deep_merge(base.get(key), value) if isinstance(base.get(key), dict) and isinstance(value, dict) else value
    return merged


def _canonical(agents) -> dict:
    """canonicalizeAgentAliases (line 26352): an old agent name is merged into the new one, later entries winning."""
    result: dict = {}
    for name, override in (agents.items() if isinstance(agents, dict) else ()):
        if not isinstance(name, str) or not isinstance(override, dict):
            continue
        target = ALIASES.get(name, name)
        result[target] = _deep_merge(result.get(target), override)
    return result


def _preset_agents(presets, name, seen=()) -> dict:
    """resolvePreset (line 26501): the preset's agent entries over those of its `extends` chain (a cycle or a missing
    parent gives none, as the plugin ignores a preset it cannot resolve)."""
    if not isinstance(presets, dict) or name in seen or not isinstance(name, str) or not isinstance(presets.get(name), dict):
        raise KeyError(name)
    definition = presets[name]
    parent = definition.get("extends")
    inner = definition.get("agents")
    if isinstance(inner, dict) and all(isinstance(entry, dict) for entry in inner.values()):  # normalizePreset's map form
        entries = _deep_merge({key: value for key, value in definition.items() if key not in ("agents", "extends", "marketplace")}, inner)
    else:
        entries = {key: value for key, value in definition.items() if key not in ("extends", "marketplace")}
    base = _preset_agents(presets, parent, (*seen, name)) if isinstance(parent, str) else {}
    return _deep_merge(base, _canonical(entries))


def effective_agents(config: dict) -> dict:
    """RuntimeConfig.agents() (line 27514): the top-level `agents` over the active preset, by canonical name."""
    top = _canonical(config.get("agents"))
    try:
        preset = _preset_agents(config.get("presets"), config.get("preset"))
    except KeyError:
        return top
    return _deep_merge(preset, top)


def parse_list(items, available) -> list[str]:
    """The plugin's parseList (line 27281)."""
    items = [item for item in items if isinstance(item, str)] if isinstance(items, list) else []
    if not items:
        return []
    allow = [item for item in items if not item.startswith("!")]
    deny = [item[1:] for item in items if item.startswith("!")]
    if "*" in deny:
        return []
    if "*" in allow:
        return [item for item in available if item not in deny]
    return [item for item in allow if item not in deny and item in available]


def agent_list(name: str, agents: dict) -> list:
    """getAgentMcpList (line 27295): the agent's own `mcps`, else the built-in default, else none."""
    entry = agents.get(name)
    if isinstance(entry, dict) and "mcps" in entry:
        return entry["mcps"] if isinstance(entry["mcps"], list) else []
    return DEFAULT_AGENT_MCPS.get(name, [])


def _enabled(entry) -> bool:  # isMcpEnabled (line 30206)
    return not isinstance(entry, dict) or (entry.get("enabled") is not False and entry.get("disabled") is not True)


def servers(config: dict | None, opencode: dict | None) -> list[dict]:
    """Every server there is, in the plugin's order (built-ins, then OpenCode's), names and flags only. A name that
    is not a plain name is not in the list, but still counts for `*` (it is not shown, nor named anywhere)."""
    disabled = config.get("disabled_mcps") if isinstance(config, dict) else None
    disabled = [item for item in disabled if isinstance(item, str)] if isinstance(disabled, list) else []
    host = opencode.get("mcp") if isinstance(opencode, dict) else None
    host = host if isinstance(host, dict) else {}
    if isinstance(host.get("servers"), dict):
        host = host["servers"]
    found: dict[str, dict] = {}
    for name in BUILTIN_SERVERS:
        if name not in disabled and name not in host:
            found[name] = {"name": name, "source": "plugin", "kind": "remote", "enabled": True}
    for name, entry in host.items():
        kind = entry.get("type") if isinstance(entry, dict) else None
        found[name] = {"name": name, "source": "opencode", "kind": kind if kind in ("local", "remote") else "",
                       "enabled": _enabled(entry)}
    return list(found.values())


def mcp_document(config_path, opencode_path, agent_names) -> tuple[list[dict], dict]:
    """(the servers as the view shows them, each agent's list); an agent not in the second is not managed."""
    config, opencode = _read(config_path), _read(opencode_path)
    every = servers(config, opencode)
    available = [entry["name"] for entry in every]
    allowed: dict[str, list[str]] = {}
    if config is not None:
        agents = effective_agents(config)
        managed = {*BUILTIN_AGENTS, *agents}
        for name in agent_names:
            if name in managed or (isinstance(name, str) and name.startswith("councillor-")):
                allowed[name] = [item for item in parse_list(agent_list(name, agents), available) if names.valid("mcp", item)][:MAX_SERVERS]
    shown = [entry for entry in every if names.valid("mcp", entry["name"])][:MAX_SERVERS]
    rows = [{"name": entry["name"], "source": entry["source"], "kind": entry["kind"], "enabled": entry["enabled"],
             "state": "on" if entry["enabled"] else "off", "reason": "", "live": False,
             "agents": [agent for agent in agent_names if entry["name"] in allowed.get(agent, ())]} for entry in sorted(shown, key=lambda e: e["name"])]
    return rows, allowed


def annotate(document, *, config=sources.CONFIG_DIR / "oh-my-opencode-slim.json", opencode=None) -> dict:
    """Add to a snapshot document, in place, `mcp` (the servers: name, source, kind, enabled, state, reason, live,
    agents) and `agents[].mcps` (the names that agent may use, or null when the plugin does not manage it).
    Reads only; never raises, and never carries anything from the settings but the names and flags above."""
    agents = [agent for agent in document.get("agents", []) if isinstance(agent, dict)]
    try:
        path = Path(opencode) if opencode is not None else sources.CONFIG_DIR / "opencode.json"
        rows, allowed = mcp_document(config, path, [agent.get("name") for agent in agents])
    except Exception:  # noqa: BLE001 - no text from the settings goes into an error
        rows, allowed = [], {}
    for agent in agents:
        agent["mcps"] = allowed.get(agent.get("name"))
    document["mcp"] = rows
    return document
