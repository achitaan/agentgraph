"""Print a versioned JSON snapshot of the agent roster and task graph."""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from dataclasses import asdict, replace
from itertools import pairwise
from math import isfinite
from pathlib import Path

from . import contract
from .contract import Agent, Session, Snapshot, TaskGraph
from .demo import demo_snapshot
from .sources import CONFIG_DIR, ORCHESTRATION_GRAPH_DIR, build_snapshot, top_level_session


def _json_safe(value):
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _time_key(value):
    value = _json_safe(value)
    return (value is not None, value if value is not None else 0)


def _session_document(session: Session) -> dict:
    return {
        "id": session.id, "title": session.title, "model": session.model,
        "effort": session.effort, "busy": session.busy, "created": session.created,
        "updated": session.updated, "cost": session.cost, "tokens_in": session.tokens_in,
        "tokens_out": session.tokens_out, "context": session.context,
    }


def _problems(graph: TaskGraph) -> list[str]:
    try:
        return contract.validate(graph)
    except (AttributeError, TypeError):
        # The validator expects non-null strings for paths, IDs and evaluations.
        # Validate a tolerant copy only when it cannot process the original;
        # the document still carries the original values (including nulls).
        normalized = replace(
            graph, path=graph.path or "",
            nodes=[
                replace(node, id=node.id or "", done_when=node.done_when or "",
                        evidence=node.evidence or "",
                        depends_on=[dep or "" for dep in node.depends_on],
                        scope=[path or "" for path in node.scope],
                        changed_paths=[path or "" for path in node.changed_paths])
                for node in graph.nodes
            ],
            objectives=[replace(obj, id=obj.id or "", nodes=[node or "" for node in obj.nodes])
                        for obj in graph.objectives],
        )
        return contract.validate(normalized)


def _graph_document(graph: TaskGraph, busy_ids: set[str], handoffs: set[tuple[str, str]]) -> dict:
    layers = contract.waves(graph)
    wave_by_id = {node.id: i for i, layer in enumerate(layers) for node in layer}
    delivered, total = contract.objective_coverage(graph)
    status_by_id = {node.id: node.status for node in graph.nodes}
    stale = {(node_id, producer, artifact, revision)
             for node_id, producer, artifact, revision, _ in contract.stale_inputs(graph)}
    nodes = []
    for node in graph.nodes:
        transitions = {(a, b) for a, b in pairwise(node.tiers) if a != b}
        handoffs.update(transitions)
        nodes.append({
            **asdict(node), "busy": contract.node_busy(node, busy_ids),
            "wave": wave_by_id[node.id], "escalated": bool(transitions),
            "inputs": [{"from": entry.from_node, "artifact": entry.artifact, "revision": entry.revision,
                        "stale": (node.id, entry.from_node, entry.artifact, entry.revision) in stale}
                       for entry in node.inputs],
        })
    return {
        "task": graph.task, "goal": graph.goal, "status": graph.status,
        "revision": graph.revision, "created": graph.created, "updated": graph.updated,
        "path": graph.path,
        "progress": {"passed": sum(node.status == "passed" for node in graph.nodes),
                     "total": len(graph.nodes)},
        "coverage": {"delivered": delivered, "total": total},
        "objectives": [
            {**asdict(obj), "delivered": bool(obj.nodes) and all(
                status_by_id.get(node_id) == "passed" for node_id in obj.nodes)}
            for obj in graph.objectives
        ],
        "problems": _problems(graph),
        "waves": [[node.id for node in layer] for layer in layers],
        "nodes": nodes,
        "edges": [
            {"from": source, "to": target, "kind": kind}
            for (source, target, kind) in dict.fromkeys(
                [(dep, node.id, "depends") for node in graph.nodes for dep in node.depends_on
                 if dep in status_by_id]
                + [(source, target, "feedback") for source, target in contract.feedback_edges(graph)])
        ],
        "advice": contract.shape_advice(graph, layers),
        "flows": contract.agent_flows(graph, busy_ids=busy_ids),
        "loops": contract.agent_loops(graph, busy_ids=busy_ids),
        "work_loops": contract.agent_work_loops(graph, busy_ids=busy_ids),
        "events": [asdict(event) for event in graph.events[-200:]],
        "amendments": graph.amendments, "lessons": graph.lessons,
    }


def _owner_chain(sessions: list[Session], session: str) -> list[str]:
    """The requested session and every ancestor up its parent chain, nearest first."""
    by_id = {item.id: item for item in sessions}
    chain = [session]
    current = by_id.get(session)
    while current is not None and current.parent_id and current.parent_id not in chain:
        chain.append(current.parent_id)
        current = by_id.get(current.parent_id)
    return chain


def _active_graph(graphs: list[TaskGraph], sessions: list[Session], task: str | None,
                  session: str | None) -> tuple[TaskGraph | None, list[str]]:
    """The named task; else the newest graph owned by the nearest of the
    requested session and its ancestors; else the newest graph, with a
    warning when a session was requested. `graphs` is newest first."""
    if task is not None:
        return next((graph for graph in graphs if graph.task == task), None), []
    if session:
        by_owner: dict[str, TaskGraph] = {}
        for graph in graphs:
            if graph.session is not None:
                by_owner.setdefault(graph.session, graph)
        owned = next((by_owner[owner] for owner in _owner_chain(sessions, session) if owner in by_owner), None)
        if owned is not None:
            return owned, []
        if graphs:
            return graphs[0], [f"graph: no graph belongs to session {session}; showing the newest"]
    return (graphs[0] if graphs else None), []


STRUCTURE_DIR = ORCHESTRATION_GRAPH_DIR.parent.parent / "structure"
SETTING_KEYS = ("pool", "escalates_to", "seats")
HEAD_BYTES = 4096  # only the start of an agent's file is read for its settings and description
SETTING_LINES = 12


def read_settings(data: bytes) -> tuple[list[tuple[str, str]], list[str], int]:
    """The settings at the top of an agent's file, as the graph reads them: from
    the first HEAD_BYTES bytes, split with `str.splitlines`, at most
    SETTING_LINES lines; blank or `---` lines before the first setting are
    skipped, a blank line after one closes them, and the first line that is not
    `key: value` with a known key starts the description. A settings-like line
    after the description is therefore description text, not a setting.
    Returns the (key, value) pairs in order, the lines read, and the index of
    the description's first line."""
    lines = data[:HEAD_BYTES].decode("utf-8", "replace").splitlines()
    settings: list[tuple[str, str]] = []
    prose_from = len(lines)  # where the description starts; none when the file is all settings
    for index, line in enumerate(lines[:SETTING_LINES]):
        key, sep, value = line.partition(":")
        key, value = key.strip().lower(), value.strip().strip("`'\"")
        if line.strip() in ("", "---"):
            if settings:
                prose_from = index + 1
                break  # a blank line closes the settings
            continue
        if not sep or not value or key not in SETTING_KEYS:
            prose_from = index
            break  # the settings end at the first line of prose
        settings.append((key, value))
    return settings, lines, prose_from


FLOWS_FOLDER = "flows"  # inside the structure folder: one file per flow, never a group of agents
TEAMS_FOLDER = "teams"  # inside the structure folder: one file per team (agentgraph.teams), never a group of agents
FLOW_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
FLOW_TEXT_LIMIT = 2000
FLOW_FILES_LIMIT = 200
FLOW_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,31}")  # a path's name: also its command's name (`/NAME`)


def seat_lead(shape: dict, name: str) -> str:
    """The council lead a seat stands for, or "" when `name` is not a seat."""
    return next((lead for lead, prefix in shape["seats"].items() if name != lead and name.startswith(prefix)), "")


def is_self_loop(path: list[str]) -> bool:
    """`A -> A`: an agent checking its own work, the whole path."""
    return len(path) == 2 and path[0] == path[1]


def hub_names(shape: dict | None) -> frozenset[str]:
    return frozenset(name for name, group in shape["group_of"].items() if group == "hub") if shape else frozenset()


def flow_steps(path: list[str], hubs) -> tuple[list[str], int]:
    """The working part of a flow's path (the hub dropped from either end) and
    the index in it of the first return (a step to an agent already on the
    path), or -1 when there is none."""
    working = list(path)
    if working and working[0] in hubs:
        working = working[1:]
    if working and working[-1] in hubs:
        working = working[:-1]
    first_return = next((index for index in range(1, len(working)) if working[index] in working[:index]), -1)
    return working, first_return


def flow_refusal(shape: dict, path: list[str]) -> str:
    """Why `path` (names already standing for themselves: a seat replaced by its
    council) cannot be a flow, or "". The rules `agentgraph check` enforces
    must be satisfiable for every path accepted here."""
    if len(path) < 2:
        return "a flow needs at least two agents"
    for name in path:
        if name not in shape["group_of"]:
            return f"unknown agent {name}: there is no {name}.md in the structure folder"
    if is_self_loop(path):
        return ""  # any agent, the hub included, may loop on itself (one such flow each: see add_flow)
    hubs = hub_names(shape)
    for index in range(1, len(path)):
        if path[index] == path[index - 1]:
            return f"{path[index]} follows itself: a step must go to another agent"
    for index, name in enumerate(path[1:-1], start=1):
        if name in hubs:
            return (f"the hub {name} can only start or end a flow: it already hands out and receives every piece "
                    "of work, so it adds no step in the middle")
    working, _ = flow_steps(path, hubs)
    # The plain task graph the path describes: one node per agent, in order of first visit. A step to a new
    # agent is a node that depends on the current one (so the nodes form a tree); a return makes the current
    # node a gate whose feedback_to names the earlier agent's node, and the chain goes on from that node. A
    # return right after a step forward always works (the current node is new, nothing comes after it). Two
    # things can fail, and only after a return: the current node is the first agent's own work, which starts
    # the flow and so is never a gate; or the node it would send back to came after it (depends on it), and a
    # check may only send work back to work done before it (`validate`: "sends feedback to downstream node").
    parent: dict[str, str | None] = {working[0]: None}
    current = working[0]
    for index in range(1, len(working)):
        agent = working[index]
        if agent not in parent:
            parent[agent], current = current, agent
            continue
        if current == working[0]:
            return (f"{agent} cannot get the work back straight after it returned to {working[0]}: "
                    f"{working[0]}'s own work is not a check; send it forward to another agent first")
        above = parent[agent]
        while above is not None and above != current:
            above = parent[above]
        if above == current:
            return (f"{agent} cannot get the work back from {current}: {agent}'s work came after {current}'s, and "
                    f"a check can only send work back to work done before it; send it forward to another agent first")
        current = agent
    return ""


def flow_fields(data: bytes) -> dict | None:
    """A flow file: `path: A -> B -> C`, then optionally `name: NAME` and
    `command: on` (settings lines at the top), a blank line, then the
    instruction, verbatim (one final newline dropped). {"path", "text", "name"
    (the line's value, "" without one), "command" (True for `command: on`)}, or
    None when the file has no path line, is not UTF-8 or is too large."""
    if len(data) > 4 * FLOW_TEXT_LIMIT + 4096:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    head, sep, body = text.partition("\n\n")
    path: list[str] = []
    name, command = "", False
    for line in head.split("\n"):
        key, colon, value = line.partition(":")
        key = key.strip().lower()
        if colon and key == "path":
            path = [name.strip() for name in value.split("->")]
        elif colon and key == "name":
            name = value.strip()
        elif colon and key == "command":
            command = value.strip().lower() == "on"
    if not path or not all(path):
        return None
    return {"path": path, "text": body[:-1] if body.endswith("\n") else body, "name": name, "command": command}


def read_flow(data: bytes) -> tuple[list[str], str] | None:
    """(path, instruction) of a flow file (see `flow_fields`), or None."""
    fields = flow_fields(data)
    return (fields["path"], fields["text"]) if fields is not None else None


def load_flows(root: Path, shape: dict) -> None:
    """Read `flows/` into shape["flows"] ({"id", "path", "text", "name",
    "command"}, in id order; `name` the path's name or None, `command` the
    name when its command is on, else None); a file that cannot be read or
    whose path is no longer valid is skipped with a warning, and so is a second
    loop of one agent on itself. A name that is not valid or that an earlier
    flow already has is dropped with a warning (and with it the command). Adding
    stops at FLOW_FILES_LIMIT files; files beyond it (put there by hand) are
    named in a warning, never dropped silently."""
    shape["flows"] = []
    folder = root / FLOWS_FOLDER
    if folder.is_symlink() or not folder.is_dir():
        return
    try:
        files = sorted((path for path in folder.iterdir() if path.suffix == ".md" and FLOW_ID.fullmatch(path.stem)),
                       key=lambda path: path.stem)  # id order: `a-2` after `a`
    except OSError:
        return
    if len(files) > FLOW_FILES_LIMIT:
        shape["warnings"].append(f"structure: {len(files)} flow files, more than the {FLOW_FILES_LIMIT} read; "
                                 f"ignored: {', '.join(path.stem for path in files[FLOW_FILES_LIMIT:][:5])}"
                                 + (" and more" if len(files) > FLOW_FILES_LIMIT + 5 else ""))
    looped: dict[str, str] = {}
    named: dict[str, str] = {}
    for path in files[:FLOW_FILES_LIMIT]:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            fields = flow_fields(path.read_bytes())
        except OSError:
            continue
        if fields is None:
            shape["warnings"].append(f"structure: flow {path.stem} ignored: it has no readable path line")
            continue
        names, text = fields["path"], fields["text"]
        names = [seat_lead(shape, name) or name for name in names]  # a seat stands for its council
        reason = flow_refusal(shape, names)
        if reason:
            shape["warnings"].append(f"structure: flow {path.stem} ignored: {reason}")
            continue
        if is_self_loop(names):
            if names[0] in looped:
                shape["warnings"].append(f"structure: flow {path.stem} ignored: {names[0]} already loops on itself "
                                         f"in flow {looped[names[0]]}")
                continue
            looped[names[0]] = path.stem
        name = fields["name"] or None
        if name is not None and not FLOW_NAME.fullmatch(name):
            shape["warnings"].append(f"structure: flow {path.stem}: its name {name[:40]!r} is not a valid name "
                                     "(2 to 32 lowercase letters, digits and '-'); it was left out")
            name = None
        elif name is not None and name in named:
            shape["warnings"].append(f"structure: flow {path.stem}: flow {named[name]} already has the name {name}; "
                                     "it was left out")
            name = None
        if name is not None:
            named[name] = path.stem
        elif fields["command"]:
            shape["warnings"].append(f"structure: flow {path.stem}: its command is on but it has no name; "
                                     "the command was left out")
        shape["flows"].append({"id": path.stem, "path": names, "text": text, "name": name,
                               "command": name if name is not None and fields["command"] else None})


def load_structure(directory: Path | None = None) -> dict | None:
    """Read the agent structure from a folder: one sub-folder per group, in name
    order (a leading number and dash set the order and are dropped), one `.md`
    file per agent. A file may start with `key: value` lines: `pool` (a number),
    `escalates_to` (an agent), `seats` (a name prefix whose agents
    sit round this one). The text after the settings is the agent's
    description, kept as `about`. The `flows` folder holds the drawn flows
    (see `load_flows`) and the `teams` folder the teams (agentgraph.teams), never agents. None when the folder does not exist or
    holds no group."""
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    try:
        folders = sorted(path for path in root.iterdir()
                         if path.is_dir() and not path.name.startswith(".")
                         and path.name not in (FLOWS_FOLDER, TEAMS_FOLDER))[:50]
    except OSError:
        return None
    shape: dict = {"groups": [], "titles": {}, "group_of": {}, "pool": {}, "escalation": [], "seats": {}, "about": {},
                   "files": {}, "flows": [], "teams": [], "warnings": []}
    for folder in folders:
        group = re.sub(r"^\d+[-_]", "", folder.name)
        if not group or group in shape["titles"]:
            continue
        shape["groups"].append(group)
        shape["titles"][group] = contract.GROUP_TITLES.get(group, group.replace("-", " ").capitalize())
        try:
            files = sorted(path for path in folder.iterdir() if path.suffix == ".md" and path.is_file())[:100]
        except OSError:
            continue
        for path in files:
            name = path.stem
            if name in shape["group_of"]:
                continue
            shape["group_of"][name] = group
            shape["files"][name] = path
            try:
                with path.open("rb") as stream:
                    settings, lines, prose_from = read_settings(stream.read(HEAD_BYTES))
            except OSError:
                continue
            for key, value in settings:
                if key == "pool" and value.isdigit():
                    shape["pool"][name] = int(value)
                elif key == "escalates_to":
                    shape["escalation"].append((name, value))
                elif key == "seats":
                    shape["seats"][name] = value
            about = " ".join(" ".join(lines[prose_from:]).split())
            if about:
                shape["about"][name] = about[:600]
    if not shape["groups"]:
        return None
    load_flows(root, shape)
    load_teams(root, shape)
    return shape


def load_teams(root: Path, shape: dict) -> None:
    """Read `teams/` into shape["teams"] ({"id", "lead", "members", "text"}, in id order) with the rules of
    agentgraph.teams; a file it cannot use is skipped with a warning. None without a `teams/` folder."""
    from . import teams  # teams imports this module
    agents = {"group_of": shape["group_of"], "seats": shape["seats"], "hubs": sorted(hub_names(shape))}
    found, warnings = teams._load(root, agents)
    shape["teams"] = [{key: team[key] for key in ("id", "lead", "members", "text")} for team in found]
    shape["warnings"].extend(warnings)


class _Shape:
    """Where each agent sits: from a structure folder when there is one, else the built-in table."""

    def __init__(self, structure: dict | None):
        self.structure = structure
        self.groups = list(structure["groups"]) if structure else list(contract.GROUPS)
        if "hub" not in self.groups:
            self.groups.insert(0, "hub")

    def title(self, group: str) -> str:
        return (self.structure["titles"] if self.structure else contract.GROUP_TITLES).get(group) or contract.GROUP_TITLES.get(group, group)

    def group_of(self, name: str) -> str:
        if not self.structure:
            return contract.group_of(name)
        if name in self.structure["group_of"]:
            return self.structure["group_of"][name]
        for lead, prefix in self.structure["seats"].items():
            if name.startswith(prefix):
                return self.structure["group_of"][lead]
        fallback = contract.group_of(name)
        return fallback if fallback in self.groups and fallback != "hub" else next((g for g in self.groups if g != "hub"), "hub")

    def pool(self, name: str, default):
        return self.structure["pool"].get(name) if self.structure else default

    def parent_of(self, name: str, names) -> str:
        if not self.structure:
            return contract.parent_of(name, names)
        return next((lead for lead, prefix in self.structure["seats"].items()
                     if name.startswith(prefix) and name != lead and lead in names), "")

    def about(self, name: str) -> str:
        return self.structure["about"].get(name, "") if self.structure else ""

    @property
    def warnings(self) -> list[str]:
        return list(self.structure.get("warnings", [])) if self.structure else []

    def teams(self) -> list[dict]:
        """The groups (teams): {"id", "lead", "members", "text"}; none without a structure folder."""
        return [dict(team, members=list(team["members"])) for team in (self.structure.get("teams", []) if self.structure else [])]

    def flows(self) -> list[dict]:
        """The drawn flows: {"id", "path", "text", "name", "command", "source": "drawn"}."""
        return [{**flow, "source": "drawn"} for flow in (self.structure.get("flows", []) if self.structure else [])]

    @property
    def escalation(self):
        return self.structure["escalation"] if self.structure else contract.ESCALATION


def snapshot_document(snapshot: Snapshot, source: str, task: str | None = None, *,
                      session: str | None = None, structure: dict | None = None) -> dict:
    """Project a snapshot onto the web contract without modifying the input.

    `session` is the session id the caller asked to watch (it may be a
    sub-agent's); it picks that session's graph when no `task` is given.
    """
    shape = _Shape(structure)
    graphs = sorted(snapshot.graphs, key=lambda graph: _time_key(graph.updated), reverse=True)
    root = snapshot.root
    sessions = ([root] if root is not None else []) + snapshot.sessions
    active, selection_warnings = _active_graph(graphs, sessions, task, session)
    by_agent = defaultdict(list)
    for item in sorted(sessions, key=lambda s: _time_key(s.updated), reverse=True):
        owner = (item.agent or "orchestrator") if item is root else item.agent
        if owner:
            by_agent[owner].append(item)

    # The active roster, plus an agent outside it only while one of its
    # sessions is busy and the config does not disable it, so live work is
    # never invisible. Agents seen only in
    # finished sessions or in graph nodes are not listed; totals still count
    # every session.
    roster = {agent.name: agent for agent in snapshot.roster}
    names = dict.fromkeys(roster)
    disabled = set(snapshot.disabled_agents)
    names.update(dict.fromkeys(name for name, related in by_agent.items()
                               if name not in disabled and any(item.busy for item in related)))
    names.pop("user", None)  # Reserved even if supplied by the roster or a session.
    # Whoever runs the watched session is the hub. Another configured hub
    # plays no part in this session unless it is busy, so it is left out.
    # It is always listed, working or not, so the picture keeps its centre
    # between turns. `user` and a disabled agent cannot be the hub; the
    # configured one then stays.
    root_name = (root.agent or "orchestrator") if root is not None else None
    if root_name == "user" or root_name in disabled:
        root_name = None
    if root_name is not None:
        names.setdefault(root_name)
        for name in [n for n in names if n != root_name and shape.group_of(n) == "hub"
                     and not any(item.busy for item in by_agent.get(n, []))]:
            del names[name]

    grouped = {group: [] for group in shape.groups}
    for name in names:
        related = by_agent.get(name, [])
        configured = name in roster
        agent = roster.get(name)
        if agent is None:
            model, effort = (related[0].model, related[0].effort) if related else ("", "")
            agent = Agent(name, model or "", effort or "", shape.group_of(name), contract.POOL.get(name))
        group = "hub" if name == root_name else shape.group_of(name or "")
        # A root agent the roster does not list (OpenCode's own `build` or `plan`) is not filtered
        # by the plugin, so it may load every skill; any other agent keeps its roster list.
        grouped[group].append({
            "name": name, "group": group, "model": agent.model, "effort": agent.effort,
            "pool": shape.pool(name, agent.pool), "configured": configured,
            "skills": ["*"] if name == root_name and not configured else list(agent.skills), "about": shape.about(name),
            "team": contract.team_of(name or ""), "parent": shape.parent_of(name or "", names),
            "busy": sum(bool(session.busy) for session in related),
            "sessions": [_session_document(session) for session in related],
        })

    grouped["hub"].sort(key=lambda entry: entry["name"] != root_name)  # stable: the root first
    handoffs: set[tuple[str, str]] = set()
    busy_ids = {session.id for session in sessions if session.busy and session.id is not None}
    graph_doc = _graph_document(active, busy_ids, handoffs) if active is not None else None
    listed = [agent for agents in grouped.values() for agent in agents]
    return _json_safe({
        "version": 1, "generated": snapshot.now, "source": source,
        "warnings": [*snapshot.warnings, *selection_warnings, *shape.warnings],
        "team": contract.team_of(root.agent or "") if root is not None else "main",
        "totals": {"sessions": len(sessions), "running": sum(bool(s.busy) for s in sessions),
                   "cost": sum(s.cost for s in sessions), "tokens_in": sum(s.tokens_in for s in sessions),
                   "tokens_out": sum(s.tokens_out for s in sessions)},
        "root": {**_session_document(root), "agent": root.agent, "directory": root.directory,
                 "wear": root.wear}
        if root is not None else None,
        "structure": {"source": "folder" if structure else "builtin", "flows": shape.flows(), "teams": shape.teams()},
        "groups": [{"id": group, "title": shape.title(group),
                    "agents": [agent["name"] for agent in agents]}
                   for group, agents in grouped.items()],
        "agents": listed,
        "escalation": [{"from": a, "to": b, "used": (a, b) in handoffs}
                       for a, b in shape.escalation if a in names and b in names],
        "graph": graph_doc,
        "graphs": [{"task": graph.task, "status": graph.status, "updated": graph.updated}
                   for graph in graphs],
    })


# The ledger each preset's improvement skill writes to, where it is not memory-<preset>.
_LEDGER_HOMES = {"ultra-fast": "memory-o2"}


def ledger_home(home_root: Path | None = None, config_dir: Path | None = None,
                config_path: Path | None = None) -> Path | None:
    """The ledger folder of the active preset (`memory-<preset>`, else `memory`) under `home_root` (default: the
    orchestration folder); None when there is none."""
    root = Path(home_root) if home_root is not None else ORCHESTRATION_GRAPH_DIR.parent.parent
    preset = ""
    try:
        path = Path(config_path) if config_path is not None else Path(config_dir or CONFIG_DIR) / "oh-my-opencode-slim.json"
        config = json.loads(path.read_text())
        preset = str(config.get("preset") or "")
    except Exception:  # noqa: BLE001
        pass
    home = root / _LEDGER_HOMES.get(preset, f"memory-{preset}")
    if not preset or "/" in preset or not home.is_dir():
        home = root / "memory"
    return home if home.is_dir() else None


def learning_document(home_root: Path | None = None, config_dir: Path | None = None,
                      config_path: Path | None = None) -> dict | None:
    """What the ledger has stored for the active preset: the user's notes in
    their own words, whether a prompt change is due, and each agent's record
    over recorded runs. None when there is no ledger to read."""
    try:
        from . import ledger
        home = ledger_home(home_root, config_dir, config_path)
        if home is None:
            return None
        report = ledger.signals(home, None, all_items=True)
        notes = sorted(report["feedback"], key=lambda item: item.get("time", 0), reverse=True)
        last = report.get("last_improvement")
        return {
            "total": len(notes), "pending": report["pending"]["feedback"], "due": bool(report["due"]),
            "runs": sum(report["outcomes"].values()), "improvements": len(report.get("improvements", [])),
            "last_improvement": last.get("time") if isinstance(last, dict) else None,
            "notes": [{"time": item.get("time"), "agent": str(item.get("agent") or ""), "task": str(item.get("task") or ""),
                       "text": str(item.get("text") or "")[:400]} for item in notes[:50]],
            "lessons": [str(item["text"])[:400] for item in report["lessons"][:20]],
            # Prompt changes already applied, newest first, and how runs went before and after the latest.
            "changes": [{"time": item.get("time"), "summary": str(item.get("summary") or "")[:300],
                         "file": ", ".join(map(str, item["file"])) if isinstance(item.get("file"), list)
                         else str(item.get("file") or "")}
                        for item in report.get("improvements", [])[-20:]][::-1],
            "trend": {side: {key: metrics.get(key) for key in ("runs", "first_pass_rate", "mean_attempts")}
                      for side, metrics in report["trend"].items()} if report.get("trend") else None,
            "agents": {name: {"steps": stats["nodes"], "first_pass_rate": stats["first_pass_rate"],
                              "mean_attempts": stats["mean_attempts"]}
                       for name, stats in list(report["agents"].items())[:100]},
        }
    except Exception:  # noqa: BLE001
        return None


def skills_document(config_dir: Path | None = None, *, skills_dir: Path | None = None) -> list[dict]:
    """The installed skills: name and one-line description, read from the head of each SKILL.md
    (in `skills_dir`, default the `skills` folder of `config_dir`)."""
    found: list[dict] = []
    folder_of_skills = Path(skills_dir) if skills_dir is not None else Path(config_dir or CONFIG_DIR) / "skills"
    try:
        folders = sorted(path for path in folder_of_skills.iterdir() if path.is_dir())[:200]
    except OSError:
        return found
    for folder in folders:
        try:
            with (folder / "SKILL.md").open("rb") as stream:
                head = stream.read(4096).decode("utf-8", "replace").replace("\r", "")
        except OSError:
            continue
        front = head.split("---", 2)[1] if head.startswith("---") and head.count("---") >= 2 else ""
        match = re.search(r"^description:[ \t]*(.*)$", front, re.M)
        description = match.group(1).strip() if match else ""
        if match and description in ("", ">", "|", ">-", "|-"):  # folded over the following indented lines
            rest = front[match.end():].split("\n")[1:]
            lines = []
            for line in rest:
                if not line.startswith((" ", "\t")):
                    break
                lines.append(line.strip())
            description = " ".join(lines)
        found.append({"name": folder.name, "description": description.strip("'\"")[:300]})
    return found


def live_document(snapshot: Snapshot, source: str, task: str | None = None, *, session: str | None = None) -> dict:
    """The snapshot document plus `learning` for a live source."""
    document = snapshot_document(snapshot, source, task, session=session,
                                 structure=load_structure() if source == "live" else None)
    if source == "live":
        from . import roster, skillfiles, skillset  # each imports this module
        document["learning"] = _json_safe(learning_document())
        from . import flowrun  # flowrun imports this module
        from .sources import DB_PATH
        flowrun.annotate(document, structure_dir=None, ledger=ledger_home(),
                         graph_paths=[graph.path for graph in snapshot.graphs], db_path=DB_PATH)
        document["skills"] = skills_document()
        skillset.annotate(document)
        roster.annotate(document)
        skillfiles.annotate(document)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph snapshot", description=__doc__)
    parser.add_argument("--demo", action="store_true", help="use sample data")
    parser.add_argument("--dir", type=Path, help="directory of graph JSON files")
    parser.add_argument("--session", metavar="ID", help="orchestrator session to watch")
    parser.add_argument("--task", metavar="NAME", help="task to show instead of the newest one")
    args = parser.parse_args(argv)
    try:
        if args.demo:
            snapshot = demo_snapshot(time.time())
        else:
            snapshot = build_snapshot(graph_dir=args.dir, root_id=args.session,
                                      session_graph_dir=None if args.dir else ORCHESTRATION_GRAPH_DIR)
        line = json.dumps(live_document(snapshot, "demo" if args.demo else "live", args.task,
                                            session=None if args.demo else args.session),
                          allow_nan=False)
    except Exception as exc:
        print(json.dumps({"version": 1, "error": str(exc)}))
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
