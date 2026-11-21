"""Validate task-graph files: `agentgraph check [--dir PATH] [--task NAME]`.

Exits 1 for invalid graphs, 2 if a file cannot be read or a requested
directory or task cannot be found, and 0 otherwise.

A graph that passes leaves a record of its state (agentgraph.transitions); the next run reports what changed that a
graph may not do (passed work lost, history rewritten, criteria weakened, ...). `--no-history` skips that.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from collections.abc import Iterator
from math import isfinite
from pathlib import Path

from .contract import (
    EVENT_NAMES, FAILURE_KINDS, GRAPH_LIMITS, GRAPH_STATUSES, MAX_NODE_LIST, NODE_LIST_FIELDS, NODE_STATUSES,
    NODE_TYPES, VERDICTS, TaskGraph, objective_coverage, shape_advice, validate,
)
from .sources import find_graph_dir, graph_files, graph_from_raw, read_graph_text
from .snapshot_json import flow_steps, hub_names, is_self_loop, load_structure
from .teams import problems as team_problems
from . import outputs, transitions


_NODE_STRINGS = ("id", "type", "agent", "goal", "done_when", "status")
_NODE_LISTS = ("depends_on", "scope", "tiers", "changed_paths", "feedback_to")
_NOT_OBJECT = "graph must be an object"
_INVALID_TASK = "graph task must be a non-blank string"
_MAX_LIST_ISSUES = 100
COMMIT_ID = re.compile(r"[0-9a-f]{7,64}")  # a node's `base`: an abbreviated or full hash, lower case (git's own output)


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _number(value: object) -> bool:
    return type(value) is int or (type(value) is float and isfinite(value))


def _entries(raw: dict, field: str, kind: str | None, issues: list[str]) -> Iterator[tuple[str, dict]]:
    items = raw.get(field)
    if not isinstance(items, list):
        return  # The container type is checked by _raw_issues.
    for i, item in enumerate(items):
        label = f"{field}[{i}]"
        if not isinstance(item, dict):
            issues.append(f"{label} must be an object")
            continue
        if kind is not None and _nonblank(item.get("id")):
            label = f"{kind} {item['id']!r}"
        yield label, item


def _oversized_lists(raw: dict) -> list[str]:
    """Lists inside a node or an objective that are over MAX_NODE_LIST."""
    issues: list[str] = []
    for container, kind, fields in (("nodes", "node", NODE_LIST_FIELDS), ("objectives", "objective", ("nodes",))):
        items = raw.get(container)
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            label = f"{kind} {item['id']!r}" if _nonblank(item.get("id")) else f"{container}[{i}]"
            for field in fields:
                value = item.get(field)
                if isinstance(value, list) and len(value) > MAX_NODE_LIST:
                    if len(issues) == _MAX_LIST_ISSUES:
                        return issues + ["... and more lists over the limit (further checks skipped)"]
                    issues.append(f"{label} has {len(value)} {field}; the limit is {MAX_NODE_LIST}")
    return issues


def _raw_issues(raw: dict) -> list[str]:
    oversized = [f"graph has {len(raw[field])} {field}; the limit is {limit}"
                 for field, limit in GRAPH_LIMITS.items()
                 if isinstance(raw.get(field), list) and len(raw[field]) > limit]
    if oversized:
        return oversized
    oversized = _oversized_lists(raw)
    if oversized:
        return oversized
    issues: list[str] = []
    if not _nonblank(raw.get("task")):
        issues.append(_INVALID_TASK)
    if not _nonblank(raw.get("goal")):
        issues.append("graph goal must be a non-blank string")
    if raw.get("status") not in GRAPH_STATUSES:
        issues.append("graph status must be one of " + ", ".join(GRAPH_STATUSES))
    if not isinstance(raw.get("nodes"), list):
        issues.append("graph nodes must be a list of objects")
    for field in ("objectives", "events"):
        if field in raw and not isinstance(raw[field], list):
            issues.append(f"graph {field} must be a list")
    for field in ("amendments", "lessons"):
        if field in raw and not _strings(raw[field]):
            issues.append(f"graph {field} must be a list of strings")
    if "revision" in raw and type(raw["revision"]) is not int:
        issues.append("graph revision must be an integer")
    session = raw.get("session")
    if session is not None and not isinstance(session, str):
        issues.append("graph session must be a string or null")
    for field in ("created", "updated"):
        value = raw.get(field)
        if not _number(value) or value <= 0:
            issues.append(f"graph {field} must be a finite number greater than 0")

    for label, node in _entries(raw, "nodes", "node", issues):
        for field in _NODE_STRINGS:
            if not _nonblank(node.get(field)):
                issues.append(f"{label} {field} must be a non-blank string")
        for field in _NODE_LISTS:
            if field in node and not _strings(node[field]):
                issues.append(f"{label} {field} must be a list of strings")
        for field, strings in (("artifacts", ("id", "path")), ("inputs", ("from", "artifact"))):
            if field not in node:
                continue
            if not isinstance(node[field], list):
                issues.append(f"{label} {field} must be a list of objects")
                continue
            for i, entry in enumerate(node[field]):
                entry_label = f"{label} {field}[{i}]"
                if not isinstance(entry, dict):
                    issues.append(f"{entry_label} must be an object")
                    continue
                for key in strings:
                    if not _nonblank(entry.get(key)):
                        issues.append(f"{entry_label} {key} must be a non-blank string")
                if type(entry.get("revision")) is not int or entry["revision"] <= 0:
                    issues.append(f"{entry_label} revision must be a positive integer")
        if "attempts" in node and type(node["attempts"]) is not int:
            issues.append(f"{label} attempts must be an integer")
        if "stalled" in node and not isinstance(node["stalled"], bool):
            issues.append(f"{label} stalled must be a boolean")
        session = node.get("session")
        if session is not None and not isinstance(session, str):
            issues.append(f"{label} session must be a string or null")
        for field, options in (("failure_kind", FAILURE_KINDS), ("verdict", VERDICTS)):
            if field in node and node[field] not in ("", *options):
                issues.append(f"{label} {field} must be a string: empty or one of " + ", ".join(options))
        for field in ("evidence", "baseline", "outputs", "pattern"):
            if field in node and not isinstance(node[field], str):
                issues.append(f"{label} {field} must be a string")
        if "base" in node and node["base"] != "" and not (isinstance(node["base"], str) and COMMIT_ID.fullmatch(node["base"])):
            issues.append(f"{label} base must be a commit id (7 to 64 lower-case hex digits) or empty")
        for field in ("started", "finished"):
            value = node.get(field)
            if value is not None and not _number(value):
                issues.append(f"{label} {field} must be a finite number or null")

    for label, objective in _entries(raw, "objectives", "objective", issues):
        for field in ("id", "text"):
            if not _nonblank(objective.get(field)):
                issues.append(f"{label} {field} must be a non-blank string")
        if not _strings(objective.get("nodes")):
            issues.append(f"{label} nodes must be a list of strings")

    for label, event in _entries(raw, "events", None, issues):
        if not _number(event.get("time")):
            issues.append(f"{label} time must be a finite number")
        if not isinstance(event.get("node"), str):
            issues.append(f"{label} node must be a string")
        if event.get("event") not in EVENT_NAMES:
            issues.append(f"{label} event must be one of " + ", ".join(EVENT_NAMES))
        if "note" in event and not isinstance(event["note"], str):
            issues.append(f"{label} note must be a string")
    return issues


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _read_json(path: Path) -> object:
    return json.loads(read_graph_text(path), parse_constant=_reject_constant)


def _print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        # JSON escapes can produce strings the terminal encoding cannot emit.
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def _read_raw(path: Path) -> tuple[dict | None, list[str]]:
    try:
        raw = _read_json(path)
    except (OSError, ValueError, RecursionError) as exc:
        return None, [str(exc)]
    if not isinstance(raw, dict):
        return None, [_NOT_OBJECT]
    return raw, []


def read_graph(path: Path) -> tuple[TaskGraph | None, list[str]]:
    """Read once and return the decoded graph and its validation problems.

    An unreadable file or non-object yields no graph and one diagnostic.
    An object with invalid fields still yields a tolerant graph for display.
    """
    path = Path(path)
    raw, issues = _read_raw(path)
    if raw is None:
        return None, issues
    return _graph_and_issues(raw, path)


FLOW_START_TYPES = ("implement", "design", "research", "decision")
_CHAIN_BUDGET = 20000  # search steps per starting node; a graph needing more is reported, never passed silently


def _chain_index(graph: TaskGraph, seats=None):
    """(nodes by id, who each node stands for (a seat for its council), the nodes that follow each node)."""
    by_id = {node.id: node for node in graph.nodes}
    prefixes = {lead: prefix for lead, prefix in (seats or {}).items() if isinstance(prefix, str) and prefix}
    runs = {node.id: next((lead for lead, prefix in prefixes.items() if node.agent != lead and node.agent.startswith(prefix)),
                          node.agent) for node in graph.nodes}  # who a node stands for: a seat for its council
    followers: dict[str, list] = {}
    for node in graph.nodes:
        targets = list(node.depends_on) + (list(node.feedback_to) if node.type == "gate" else [])
        for target in dict.fromkeys(targets):
            followers.setdefault(target, []).append(node)
    return by_id, runs, followers


def _walk_chain(working: list[str], start, index):
    """Follow `working` (a flow's agents, the hub dropped) from node `start`: (the chain, {agent: node id}, when
    it is complete, else None; the furthest step reached and the chain there; whether the search ran out)."""
    by_id, runs, followers = index
    budget = [_CHAIN_BUDGET]
    best: list = [1, (start.id, {working[0]: start.id})]  # furthest step reached, and the chain there

    def walk(step: int, current: str, chain: dict[str, str]):
        if step == len(working):
            return chain
        if step > best[0]:
            best[:] = [step, (current, dict(chain))]
        budget[0] -= 1
        if budget[0] < 0:
            return None
        agent = working[step]
        if agent in chain:  # a return
            node = by_id[current]
            return walk(step + 1, chain[agent], chain) if node.type == "gate" and chain[agent] in node.feedback_to else None
        for follower in followers.get(current, []):
            if runs[follower.id] == agent and follower.id not in chain.values():
                found = walk(step + 1, follower.id, {**chain, agent: follower.id})
                if found is not None:
                    return found
        return None

    found = walk(1, start.id, {working[0]: start.id})
    return found, best, budget[0] < 0


def _checked_flows(flows, hubs):
    # A loop of one agent on itself adds no rule: a task graph cannot show it. It is printed with the others.
    return [flow for flow in flows if measurable(flow, hubs)]


def measurable(flow: dict, hubs=frozenset()) -> bool:
    """Whether a task graph can show `flow`: not a loop of one agent on itself, nor one agent and the hub. `check`
    enforces only these, and only these have stats (agentgraph.flowrun: the others' numbers are unknown, null)."""
    return not is_self_loop(flow["path"]) and len(flow_steps(flow["path"], hubs)[0]) >= 2


def follows(graph: TaskGraph, flow: dict, hubs=frozenset(), seats=None, index=None) -> tuple[list[dict], list[str]]:
    """The one definition of a task graph following a drawn flow, used by `check` (flow_problems) and by the
    stats (flow_chains, agentgraph.flowrun). Returns (chains, problems): `chains` holds the chain ({agent: node id})
    from each node of the flow's first agent (a FLOW_START_TYPES type, any status) that has the rest of the path after
    it; `problems` one message per such node not yet passed that has none (a passed node's work is done: the flow
    may have been drawn after it). The graph follows the flow when it has a chain and no problem. A flow a task graph
    cannot show (`measurable` false) gives ([], [])."""
    if not measurable(flow, hubs):
        return [], []
    index = index if index is not None else _chain_index(graph, seats)
    by_id, runs, _ = index
    working, _ = flow_steps(flow["path"], hubs)
    label = " → ".join(flow["path"])
    chains: list[dict] = []
    problems: list[str] = []
    for start in graph.nodes:
        if runs[start.id] != working[0] or start.type not in FLOW_START_TYPES:
            continue
        found, best, ran_out = _walk_chain(working, start, index)
        if found is not None:
            chains.append(found)
            continue
        if start.status == "passed":
            continue
        step, (current, chain) = best
        if ran_out:
            problems.append(f"{start.id} ({start.agent}): its chain is too tangled to check: flow {label}")
            continue
        agent = working[step]
        where = "" if current == start.id else f" after {by_id[current].agent} ({current})"
        if agent in chain:
            problems.append(f"{start.id} ({start.agent}) is not sent back to {agent}{where}: {current} must be a gate "
                            f"whose feedback_to names {chain[agent]}: flow {label}")
        else:
            problems.append(f"{start.id} ({start.agent}) is not followed by {agent}{where}: flow {label}")
    return chains, problems


def flow_problems(graph: TaskGraph, flows, hubs=frozenset(), seats=None) -> list[str]:
    """Drawn flows (the `flows` folder of the structure) that the graph does not
    follow. A flow applies to work that starts at its first agent (the second
    when the hub is first): every node of that agent of a FLOW_START_TYPES type
    that has not passed needs the rest of the path as a chain. A step forward to
    a new agent Y needs a node run by Y that follows the chain's current node
    (lists it in depends_on, or is a gate whose feedback_to names it); a return
    to an earlier agent needs the current node to be a gate whose feedback_to
    names that agent's node on the chain, and the chain continues from there.
    The hub as the last step adds nothing. `seats` ({council lead: seat
    prefix}, the structure's) makes a node run by a seat count as its
    council's. One problem per starting node without a complete chain, naming
    the first missing step."""
    flows = _checked_flows(flows, hubs)
    if not flows:
        return []
    index = _chain_index(graph, seats)
    return [problem for flow in flows for problem in follows(graph, flow, hubs, seats, index)[1]]


def flow_chains(graph: TaskGraph, flow: dict, hubs=frozenset(), seats=None) -> list[dict[str, str]]:
    """`flow_problems` turned round: the chains ({agent: node id}) along which the graph followed `flow`, one per
    node of its first agent (of a FLOW_START_TYPES type, whatever its status) that has the whole rest of the path
    after it, when the graph follows the flow (`follows`: `check` finds no problem with it for this flow); else
    empty. Empty too for a flow `check` does not enforce (a loop of one agent on itself, or one agent and the
    hub)."""
    chains, problems = follows(graph, flow, hubs, seats)
    return [] if problems else chains


def _stands_for(agent: str, seats) -> str:
    prefixes = {lead: prefix for lead, prefix in (seats or {}).items() if isinstance(prefix, str) and prefix}
    return next((lead for lead, prefix in prefixes.items() if agent != lead and agent.startswith(prefix)), agent)


def _widest(ids: list[str], reaches: dict[str, set[str]]) -> list[str]:
    """The largest set of `ids` no two of which are ordered (neither reaches the other through depends_on): a
    maximum antichain, by Dilworth's theorem from a maximum matching (Kuhn) and König's cover."""
    after = {u: [v for v in ids if v != u and v in reaches[u]] for u in ids}
    match_right: dict[str, str] = {}

    def augment(u: str, seen: set[str]) -> bool:
        for v in after[u]:
            if v in seen:
                continue
            seen.add(v)
            if v not in match_right or augment(match_right[v], seen):
                match_right[v] = u
                return True
        return False

    for u in ids:
        augment(u, set())
    match_left = {u: v for v, u in match_right.items()}
    # König: from each unmatched left vertex, alternate along free then matched edges; Z is what is reached.
    left_z = [u for u in ids if u not in match_left]
    left_seen, right_seen = set(left_z), set()
    while left_z:
        u = left_z.pop()
        for v in after[u]:
            if v not in right_seen and match_left.get(u) != v:
                right_seen.add(v)
                w = match_right.get(v)
                if w is not None and w not in left_seen:
                    left_seen.add(w)
                    left_z.append(w)
    return [x for x in ids if x in left_seen and x not in right_seen]


def pool_problems(graph: TaskGraph, pools: dict, seats=None) -> list[str]:
    """For each agent with a `pool` in the structure folder, the nodes it runs that could all run at once (none
    depends on another, directly or through other nodes) when there are more of them than its pool: one message
    per agent. Node statuses do not matter: this is the graph's shape."""
    by_agent: dict[str, list[str]] = {}
    for node in graph.nodes:
        by_agent.setdefault(_stands_for(node.agent, seats), []).append(node.id)
    wanted = {agent: ids for agent, ids in by_agent.items()
              if isinstance(pools.get(agent), int) and len(dict.fromkeys(ids)) > pools[agent]}
    if not wanted:
        return []
    followers: dict[str, list[str]] = {}
    for node in graph.nodes:
        for target in dict.fromkeys(node.depends_on):
            followers.setdefault(target, []).append(node.id)
    found = []
    for agent, ids in wanted.items():
        ids = list(dict.fromkeys(ids))
        reaches = {}
        for start in ids:
            seen, stack = set(), list(followers.get(start, ()))
            while stack:
                current = stack.pop()
                if current not in seen:
                    seen.add(current)
                    stack.extend(followers.get(current, ()))
            reaches[start] = seen
        widest = _widest(ids, reaches)
        if len(widest) > pools[agent]:
            shown = ", ".join(widest[:8]) + (f" and {len(widest) - 8} more" if len(widest) > 8 else "")
            found.append(f"{len(widest)} nodes of {agent} can run at once ({shown}), more than its pool of {pools[agent]} "
                         "(structure folder)")
    return found


def escalation_problems(graph: TaskGraph, escalation, seats=None) -> list[str]:
    """Nodes whose `tiers` move from an agent with `escalates_to` in the structure folder to an agent other than
    that one: one message per such move. An agent without the setting may move anywhere; the same agent twice in a
    row is a resumed session, not an escalation."""
    up: dict[str, str] = {}
    for source, target in escalation or ():
        up.setdefault(source, target)
    found = []
    for node in graph.nodes:
        tiers = [_stands_for(agent, seats) for agent in node.tiers if isinstance(agent, str)]
        for before, after in zip(tiers, tiers[1:]):
            if before != after and before in up and after != up[before]:
                found.append(f"{node.id} was escalated from {before} to {after}; {before} escalates to "
                             f"{up[before]} (structure folder)")
    return found


def folder_warnings(graph: TaskGraph, pools=None, seats=None) -> list[str]:
    """What the structure folder's `pool` says that the graph does not follow: warnings, never problems. A graph on
    disk that `check` passed has more parallel nodes of one agent than its pool, and the orchestration's own policy
    starts more instances when there are more independent lanes than slots. (`escalates_to` is a problem:
    escalation_problems; no graph on disk breaks it.)"""
    return pool_problems(graph, pools or {}, seats)


def print_flows(flows) -> None:
    """The user's standing instructions, after the result; never part of it."""
    if not flows:
        return
    _print("flows: the user's standing instructions for shaping task graphs (agentgraph structure flows):")
    for flow in flows:
        _print(f"  {' → '.join(flow['path'])}  [{flow['id']}]")
        for line in flow["text"].splitlines() or [""]:
            _print(f"      {line}" if line.strip() else "")


def _graph_and_issues(raw: dict, path: Path, flows=(), hubs=frozenset(), seats=None,
                      teams=(), escalation=()) -> tuple[TaskGraph, list[str]]:
    issues = _raw_issues(raw)
    graph = graph_from_raw(raw, path)
    if issues:
        return graph, issues
    issues = (validate(graph) + flow_problems(graph, flows, hubs, seats) + team_problems(teams, graph, seats or {})
              + escalation_problems(graph, escalation, seats))
    for node in graph.nodes:
        if node.type not in NODE_TYPES:
            issues.append(f"{node.id} has unknown type {node.type!r}")
        if node.status not in NODE_STATUSES:
            issues.append(f"{node.id} has unknown status {node.status!r}")
    if not graph.objectives:
        issues.append("no objectives declared")
    return graph, issues


def _history_step(folder: int, path: Path, raw: dict, issues: list[str]) -> list[str]:
    """The history rules for one graph that has passed the type checks, then (when `issues` and the result are both
    empty) its new record. Runs under the graph folder's lock. Returns the history problems."""
    stem = Path(path).stem
    found: list[str] = []
    try:
        previous = transitions.load_state(folder, stem)
    except transitions.HistoryError as exc:
        _print(f"warning: {exc}")
        previous = None
    if previous is not None:
        found = transitions.problems(previous, raw)
    if not issues and not found:
        try:
            transitions.save_state(folder, stem, transitions.state_of(raw))
        except OSError as exc:
            _print(f"warning: history of {stem} not saved: {exc.strerror or exc}")
    return found


def file_problems(path: Path) -> list[str] | None:
    """Every problem `check` would report, or None if no graph can be read."""
    graph, issues = read_graph(path)
    return issues if graph is not None else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph check", description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, help="directory of graph JSON files (default: nearest .opencode/graph)")
    parser.add_argument("--task", metavar="NAME", help="check only the graph with this declared task name")
    parser.add_argument("--structure", type=Path, metavar="DIR",
                        help="structure folder whose drawn flows the graphs must follow (default: the orchestration's own)")
    parser.add_argument("--home", type=Path, metavar="DIR",
                        help="memory home whose edit log says which outputs were edited by hand (agentgraph.outputs; "
                             "default: $AGENTGRAPH_HOME, else the active preset's memory folder)")
    parser.add_argument("--no-history", action="store_true",
                        help="skip the rules that compare a graph with its last passing state (and do not save one)")
    args = parser.parse_args(argv)
    edits = outputs.edit_home(args.home)
    # A missing default folder means no drawn flows; a folder named on the command line must exist,
    # so a mistyped path cannot turn the flow checks off without a word.
    shape = load_structure(args.structure)
    if args.structure is not None and shape is None:
        _print(f"structure folder {args.structure} does not exist or holds no group folder")
        return 2
    flows = shape["flows"] if shape else []
    hubs = hub_names(shape)
    seats = shape["seats"] if shape else {}
    teams = shape.get("teams", []) if shape else []  # the groups: agents that report to one lead
    escalation = shape["escalation"] if shape else []  # escalates_to: a problem when a node's tiers break it
    pools = shape["pool"] if shape else {}  # pool: a warning when more nodes of one agent can run at once

    discovery_warnings: list[str] = []
    try:
        graph_dir = args.dir if args.dir is not None else find_graph_dir(Path.cwd(), warnings=discovery_warnings)
        for warning in discovery_warnings:
            _print(warning)
        if graph_dir is None:
            _print("no .opencode/graph directory found from here")
            if args.task is not None:
                _print(f"task {args.task!r} not found")
                return 2
            return 2 if discovery_warnings else 0
        if not graph_dir.is_dir():
            _print(f"graph directory {graph_dir} is not a directory")
            return 2
        files, listing_warnings = graph_files(graph_dir)
        for warning in listing_warnings:
            _print(warning)
    except OSError as exc:
        _print(f"UNREADABLE  graph directory: {exc}")
        return 2

    graphs: list[tuple[str, TaskGraph | None, list[str]]] = []
    warnings: list[str] = []
    unsaved: dict[int, list[str]] = {}
    drift: dict[int, list[str]] = {}  # history-free warnings (agentgraph.transitions.warnings)
    with contextlib.ExitStack() as stack:
        folder = None  # the graph folder, locked: a graph, its history record and the save are one step
        if not args.no_history:
            try:
                folder = stack.enter_context(outputs._GraphFolder(graph_dir))
            except outputs.OutputError as exc:
                _print(f"warning: history rules skipped: {exc}")
        for path in files:
            raw, issues = _read_raw(path)
            graph = None
            if raw is not None:
                if args.task is not None and raw.get("task") != args.task:
                    continue  # Another task's graph: never validated, so it cannot slow this check.
                graph, issues = _graph_and_issues(raw, path, flows, hubs, seats, teams, escalation)
                issues = issues + outputs.lost_edits(graph, edits)  # an edit by hand the graph no longer records
                unsaved[id(graph)] = outputs.unsaved_edits(graph, edits)  # a save stopped before the graph: a warning
                if not _raw_issues(raw):
                    drift[id(graph)] = transitions.warnings(raw, path)
                    if folder is not None:
                        issues = issues + _history_step(folder, path, raw, issues)
            if graph is None:
                # A non-object is readable JSON with a schema problem; preserve
                # the CLI's distinction between it and a read or decode failure.
                if issues == [_NOT_OBJECT]:
                    if args.task is None:
                        graphs.append((path.name, None, issues))
                else:
                    warnings.append(f"UNREADABLE  {path.name}: {issues[0]}")
                continue
            declared_task = graph.task if _INVALID_TASK not in issues else None
            if args.task is None or args.task == declared_task:
                graphs.append((declared_task or path.name, graph, issues))

    if args.task is not None and not graphs:
        for warning in warnings:
            _print(warning)
        _print(f"task {args.task!r} not found in {graph_dir}")
        return 2
    if args.task is None:
        for warning in warnings:
            _print(warning)

    failed = False
    for name, graph, issues in sorted(graphs, key=lambda item: item[0]):
        if graph is None:
            _print(f"PROBLEMS    {name}: invalid graph fields")
        else:
            passed = sum(1 for node in graph.nodes if node.status == "passed")
            delivered, total = objective_coverage(graph)
            state = "PROBLEMS" if issues else "OK"
            _print(f"{state:<10}  {name}: {passed}/{len(graph.nodes)} nodes passed, "
                   f"{delivered}/{total} objectives delivered, revision {graph.revision}")
        for issue in issues:
            _print(f"            - {issue}")
        if graph is not None:
            for advice in shape_advice(graph):
                _print(f"advice: {advice}")
            for warning in folder_warnings(graph, pools, seats) + unsaved.get(id(graph), []) + drift.get(id(graph), []):
                _print(f"warning: {warning}")
        failed = failed or bool(issues)

    print_flows(flows)
    if (warnings or listing_warnings or discovery_warnings) and args.task is None:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
