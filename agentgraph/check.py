"""Validate task-graph files: `agentgraph check [--dir PATH] [--task NAME]`.

Exits 1 for invalid graphs, 2 if a file cannot be read or a requested
directory or task cannot be found, and 0 otherwise.

A graph that passes leaves a record of its state (agentgraph.transitions); the next run reports what changed that a
graph may not do (passed work lost, history rewritten, criteria weakened, ...). `--no-history` skips that.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterator
from math import isfinite
from pathlib import Path
from typing import NamedTuple

from .contract import (
    EVENT_NAMES, FAILURE_KINDS, GRAPH_LIMITS, GRAPH_STATUSES, MAX_NODE_LIST, NODE_LIST_FIELDS, NODE_STATUSES,
    NODE_TYPES, VERDICTS, TaskGraph, objective_coverage, shape_advice, validate,
)
from .sources import find_graph_dir, graph_files, graph_from_raw, read_graph_text
from .snapshot_json import flow_steps, hub_names, is_self_loop, load_structure
from .teams import problems as team_problems
from . import osfs, outputs, transitions


_NODE_STRINGS = ("id", "type", "agent", "goal", "done_when", "status")
_NODE_LISTS = ("depends_on", "scope", "tiers", "changed_paths", "feedback_to")
_NOT_OBJECT = "graph must be an object"
_INVALID_TASK = "graph task must be a non-blank string"
_MAX_LIST_ISSUES = 100
RUN_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")  # a loop run's id (agentgraph.looprun)
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


E2E_RULE = "declares an e2e and is passed, but"  # what the message of the end-to-end rule contains (agentgraph.reviewcopy keys on it)
STALE_EVENTS = ("returned", "attempt", *transitions.RESTART_EVENTS)


def _review_copy_issues(label: str, node: dict, events: object) -> list[str]:
    """The node's `review_copy` (what `agentgraph review-copy` records) and the rule for a node with an `e2e`: it is not `passed`
    without a review copy whose end-to-end command exited 0, of the e2e it declares now, that left the project unchanged and whose
    copy was taken (`started`, else `at`) no earlier than the node's last `returned`, `attempt`, `dispatched`, `retried` or
    `invalidated` event. A graph with no such event for the node has nothing to compare: the result must exist and match, its age
    cannot be told."""
    issues: list[str] = []
    found = node.get("review_copy")
    if "review_copy" in node:
        if not isinstance(found, dict):
            issues.append(f"{label} review_copy must be an object")
            found = None
        else:
            if not _nonblank(found.get("result")):
                issues.append(f"{label} review_copy result must be a non-blank string (the result file's path)")
            if not _nonblank(found.get("tree")):
                issues.append(f"{label} review_copy tree must be a non-blank string")
            for key in ("at", "started"):
                if (key == "at" or key in found) and not _number(found.get(key)):
                    issues.append(f"{label} review_copy {key} must be a finite number")
            for key in ("check_exit", "e2e_exit"):
                if found.get(key) is not None and type(found[key]) is not int:
                    issues.append(f"{label} review_copy {key} must be a whole number or null")
            for key in ("check_sha256", "e2e_sha256"):
                if key in found and not isinstance(found[key], str):
                    issues.append(f"{label} review_copy {key} must be a string")
            if "project_unchanged" in found and not isinstance(found["project_unchanged"], bool):
                issues.append(f"{label} review_copy project_unchanged must be a boolean")
    if node.get("status") != "passed" or not _nonblank(node.get("e2e")):
        return issues
    why = "has no review copy"
    if isinstance(found, dict):
        last = max((e["time"] for e in events if isinstance(e, dict) and e.get("node") == node.get("id")
                    and e.get("event") in STALE_EVENTS and _number(e.get("time"))), default=None) \
            if isinstance(events, list) else None
        if found.get("e2e_exit") != 0 or type(found.get("e2e_exit")) is not int:
            why = "its review copy's e2e did not exit 0"
        elif found.get("e2e_sha256") != hashlib.sha256(node["e2e"].encode("utf-8", "surrogatepass")).hexdigest():
            why = "its review copy ran a different e2e than the node declares now"
        elif found.get("project_unchanged") is not True:
            why = "its review copy did not leave the project unchanged"
        elif not _number(found.get("at")) or (last is not None and (found["started"] if _number(found.get("started")) else found["at"]) < last):
            why = "its review copy was taken before the node's last returned, attempt or restart (dispatched, retried, invalidated) event"
        else:
            return issues
    issues.append(f"{label} {E2E_RULE} {why} (agentgraph review-copy --task T --node N runs it and records the result)")
    return issues


RESULT_LIMIT = 1 << 20  # the most of a review-copy result file that `check` reads


def _shown(value) -> str:
    return repr(value)[:40]


def _read_result(project: Path, parts: list[str]):
    """A review-copy result file decoded, or None when it is not there, is reached through a link, is not a regular file (a
    FIFO would block), is over RESULT_LIMIT or is not JSON. Never raises, never waits."""
    fd = -1
    try:
        fd = osfs.open(project, os.O_RDONLY | osfs.O_DIRECTORY)
        for part in parts[:-1]:
            fd, parent = outputs._open_dir(fd, part), fd
            osfs.close(parent)
        leaf = osfs.open(parts[-1], os.O_RDONLY | osfs.O_NOFOLLOW | osfs.O_NONBLOCK, dir_fd=fd)
        try:
            if not stat.S_ISREG(osfs.fstat(leaf).st_mode):
                return None
            with os.fdopen(leaf, "rb", closefd=False) as handle:
                body = handle.read(RESULT_LIMIT + 1)
        finally:
            osfs.close(leaf)
        return None if len(body) > RESULT_LIMIT else json.loads(body.decode("utf-8"))
    except (OSError, ValueError, RecursionError, outputs.OutputError):
        return None
    finally:
        if fd >= 0:
            osfs.close(fd)


def review_copy_warnings(raw: dict, path: Path) -> list[str]:
    """What a node's `review_copy` says that the result file it names does not: the file is not under the node's own artifacts
    folder, is not there, or its exits and command hashes differ from the node's. Warnings: a hand-written record is the thing
    this looks for, but `check` cannot tell it from a result file that was edited too."""
    project = outputs.project_of(path)
    task = raw.get("task") if isinstance(raw.get("task"), str) else path.stem
    found: list[str] = []
    for node in raw.get("nodes") or []:
        record = node.get("review_copy") if isinstance(node, dict) else None
        if not isinstance(record, dict) or project is None or not isinstance(node.get("id"), str):
            continue
        label, result = f"{node['id']} review_copy", record.get("result")
        parts = outputs._inside(task, result)
        if parts is None or len(parts) != 2 or parts[0] != node["id"] or not parts[1].startswith("review-copy-") or not parts[1].endswith(".json"):
            found.append(f"{label} result {str(result)[:80]!r} is not a file under .opencode/artifacts/{task}/{node['id']}/")
            continue
        data = _read_result(project, [".opencode", "artifacts", task, *parts])
        if data is None:
            found.append(f"{label} result {result} does not exist or is not a result file")
            continue
        commands = data.get("commands") if isinstance(data, dict) else None
        ran = {c["name"]: c for c in commands if isinstance(c, dict) and isinstance(c.get("name"), str)} if isinstance(commands, list) else {}
        for name in ("check", "e2e"):
            made = ran.get(name)
            exit_code = None if made is None or made.get("timed_out") else made.get("exit")
            if exit_code != record.get(f"{name}_exit") or (made is not None and record.get(f"{name}_sha256") not in (None, made.get("sha256"))):
                found.append(f"{label} {name} does not match {result} (the record says exit {_shown(record.get(f'{name}_exit'))}, the file says "
                             + ("it did not run" if made is None else f"exit {_shown(exit_code)}") + ")")
        for field in ("started", "at", "tree", "project_unchanged"):
            if record.get(field) != (data.get(field) if isinstance(data, dict) else None):
                found.append(f"{label} {field} does not match {result} (the record says {_shown(record.get(field))}, the file says "
                             f"{_shown(data.get(field) if isinstance(data, dict) else None)})")
    return found


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
        for field in ("started", "finished", "ended"):
            value = node.get(field)
            if value is not None and not _number(value):
                issues.append(f"{label} {field} must be a finite number or null")
        for field in ("check", "verify", "e2e"):
            if field in node and not _nonblank(node[field]):
                issues.append(f"{label} {field} must be a non-blank string")
        issues += _review_copy_issues(label, node, raw.get("events"))
        if "loop_run" in node and not (isinstance(node["loop_run"], str) and RUN_ID.fullmatch(node["loop_run"])):
            issues.append(f"{label} loop_run must be a loop run id (like 20260101-120000-ab12)")
        if "spent" in node and node["spent"] is not None and not (_number(node["spent"]) and node["spent"] >= 0):
            issues.append(f"{label} spent must be dollars: a number, 0 or more, or null when no turn's cost is known")
        if "unpriced" in node and node["unpriced"] is not None and not (type(node["unpriced"]) is int and node["unpriced"] >= 0):
            issues.append(f"{label} unpriced must be a whole number, 0 or more (turns whose cost is not known)")
        if "budget" in node:
            budget = node["budget"]
            if not isinstance(budget, dict):
                issues.append(f"{label} budget must be an object ({{\"cost\": dollars, \"attempts\": n}})")
            else:
                if "cost" in budget and not (_number(budget["cost"]) and budget["cost"] > 0):
                    issues.append(f"{label} budget cost must be a number above 0")
                if "attempts" in budget and not (type(budget["attempts"]) is int and budget["attempts"] >= 1):
                    issues.append(f"{label} budget attempts must be a whole number, 1 or more")
                unknown = [key for key in budget if key not in ("cost", "attempts")]
                if unknown:
                    issues.append(f"{label} budget has an unknown key {unknown[0]!r} (cost and attempts only)")

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


def read_graph(path: Path, rules: "Rules | None" = None) -> tuple[TaskGraph | None, list[str]]:
    """Read once and return the decoded graph and its validation problems.

    An unreadable file or non-object yields no graph and one diagnostic.
    An object with invalid fields still yields a tolerant graph for display.
    With `rules` the problems are every one `check` reports (`all_problems`), from the same read.
    """
    path = Path(path)
    raw, issues = _read_raw(path)
    if raw is None:
        return None, issues
    if rules is not None:
        return _examine_read_only(raw, path, rules)
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
        for target in dict.fromkeys(node.depends_on):  # a follower reads its predecessor: a gate's feedback_to alone is no step
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


def _missing(start, best, index, label: str) -> str:
    """The problem for `start`, which has no complete chain: the first missing step (`best`: where the search got)."""
    by_id, _, _ = index
    working = best[2]
    step, (current, chain) = best[0], best[1]
    agent = working[step]
    where = "" if current == start.id else f" after {by_id[current].agent} ({current})"
    if agent in chain:
        return (f"{start.id} ({start.agent}) is not sent back to {agent}{where}: {current} must be a gate "
                f"whose feedback_to names {chain[agent]}: flow {label}")
    return f"{start.id} ({start.agent}) is not followed by {agent}{where}: flow {label}"


def follows(graph: TaskGraph, flow: dict, hubs=frozenset(), seats=None, index=None) -> tuple[list[dict], list[str]]:
    """The one definition of a task graph following a drawn flow, used by `check` (flow_problems) and by the
    stats (flow_chains, agentgraph.flowrun). Returns (chains, problems): `chains` holds the chain ({agent: node id})
    from each node of the flow's first agent (a FLOW_START_TYPES type, any status) that has the rest of the path after
    it; `problems` one message per such node not yet passed that has none (a passed node's work is done: the flow
    may have been drawn after it). The graph follows the flow when it has a chain and no problem. A step to a new
    agent needs a node that depends on the current one (`depends_on`; a gate's `feedback_to` alone is no step: a
    follower reads its predecessor). A flow a task graph cannot show (`measurable` false) gives ([], [])."""
    chains, problems, _ = _follow(graph, flow, hubs, seats, index)
    return chains, problems


def _follow(graph: TaskGraph, flow: dict, hubs, seats, index) -> tuple[list[dict], list[str], list[str]]:
    """`follows`, and the ids of the nodes the flow applies to (those with a chain, and those without that have not
    passed): what `applied_flows` records."""
    if not measurable(flow, hubs):
        return [], [], []
    index = index if index is not None else _chain_index(graph, seats)
    _, runs, _ = index
    working, _ = flow_steps(flow["path"], hubs)
    label = " → ".join(flow["path"])
    chains: list[dict] = []
    problems: list[str] = []
    applies: list[str] = []
    for start in graph.nodes:
        if runs[start.id] != working[0] or start.type not in FLOW_START_TYPES:
            continue
        found, best, ran_out = _walk_chain(working, start, index)
        if found is not None:
            chains.append(found)
            applies.append(start.id)
            continue
        if start.status == "passed":
            continue
        applies.append(start.id)
        if ran_out:
            problems.append(f"{start.id} ({start.agent}): its chain is too tangled to check: flow {label}")
        else:
            problems.append(_missing(start, (*best, working), index, label))
    return chains, problems, applies


def applied_flows(graph: TaskGraph, flows, hubs=frozenset(), seats=None) -> dict[str, list[str]]:
    """The drawn flows that apply to each node of the graph now: {node id: [flow ids]}, for the nodes the flow
    starts from that follow it or still owe it (a passed node without a chain is not owed: the flow may have been
    drawn after it). `check` records them (transitions.flows_record) and keeps enforcing them when the node changes."""
    index = _chain_index(graph, seats)
    found: dict[str, list[str]] = {}
    for flow in _checked_flows(flows, hubs):
        for node_id in _follow(graph, flow, hubs, seats, index)[2]:
            found.setdefault(node_id, []).append(flow["id"])
    return found


def recorded_flow_problems(graph: TaskGraph, recorded: dict[str, list[str]], flows, hubs=frozenset(), seats=None) -> list[str]:
    """What the recorded flows still want of nodes that no longer start them: each node in `recorded` (retyped,
    given to another agent or passed since) needs the rest of the path as a chain, as when it started the flow. A
    node the plain rule already holds to the flow is not reported twice."""
    by_id = {flow["id"]: flow for flow in _checked_flows(flows, hubs)}
    nodes = {node.id: node for node in graph.nodes}
    index = _chain_index(graph, seats)
    _, runs, _ = index
    found = []
    for node_id, ids in recorded.items():
        node = nodes.get(node_id)
        for flow_id in ids:
            flow = by_id.get(flow_id)
            if node is None or flow is None:
                continue
            working, _ = flow_steps(flow["path"], hubs)
            if runs[node.id] == working[0] and node.type in FLOW_START_TYPES and node.status != "passed":
                continue  # the plain rule reports it
            chain, best, ran_out = _walk_chain(working, node, index)
            if chain is not None:
                continue
            label = " → ".join(flow["path"])
            why = ("its chain is too tangled to check" if ran_out else
                   _missing(node, (*best, working), index, label).split(": flow ")[0].split(f"{node.id} ({node.agent}) ", 1)[1])
            found.append(f"{node.id} ({node.agent}, {node.type}, {node.status}) was recorded as following flow {label} "
                         f"and {why} (only an amendment naming {node.id} and the flow releases it)")
    return found


def flow_problems(graph: TaskGraph, flows, hubs=frozenset(), seats=None) -> list[str]:
    """Drawn flows (the `flows` folder of the structure) that the graph does not
    follow. A flow applies to work that starts at its first agent (the second
    when the hub is first): every node of that agent of a FLOW_START_TYPES type
    that has not passed needs the rest of the path as a chain. A step forward to
    a new agent Y needs a node run by Y that depends on the chain's current node
    (a gate's feedback_to alone is no step); a return
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


def _history_step(folder: int, path: Path, raw: dict, issues: list[str], graph: TaskGraph, rules: "Rules",
                  write: bool) -> list[str]:
    """The history rules for one graph that has passed the type checks (the last passing state's, and the flows
    recorded for the graph), then, when `write` (under the graph folder's lock), its new records: the state when
    `issues` and the result are both empty, the recorded flows whatever the result. Returns the history problems.
    Without `write` nothing is written (the views and `record` compare only)."""
    stem = Path(path).stem
    found: list[str] = []

    def warn(text: str) -> None:
        if write:
            _print(text)

    try:
        previous = transitions.load_state(folder, stem)
    except transitions.HistoryError as exc:
        warn(f"warning: {exc}")
        previous = None
    if previous is not None:
        found = transitions.problems(previous, raw)
    try:
        before = transitions.load_flows(folder, stem)
    except transitions.HistoryError as exc:
        warn(f"warning: {exc}")
        before = None
    if rules.shape is not None:  # without the structure folder the recorded flows are left as they are
        record = transitions.flows_record(before, applied_flows(graph, rules.flows, rules.hubs, rules.seats),
                                          raw.get("amendments") or [],
                                          {flow["id"] for flow in _checked_flows(rules.flows, rules.hubs)})
        found += recorded_flow_problems(graph, record["nodes"], rules.flows, rules.hubs, rules.seats)
        if write and record != before and (before is not None or record["nodes"] or record["released"]):
            try:
                transitions.save_flows(folder, stem, record)
            except OSError as exc:
                _print(f"warning: recorded flows of {stem} not saved: {exc.strerror or exc}")
    if write and not issues and not found:
        try:
            transitions.save_state(folder, stem, transitions.state_of(raw))
        except OSError as exc:
            _print(f"warning: history of {stem} not saved: {exc.strerror or exc}")
    return found


class Rules(NamedTuple):
    """What a graph is held to besides its own fields: the structure folder's flows, hubs, seats, groups,
    escalation and pools, and the memory home whose edit log says which outputs were edited by hand."""
    shape: dict | None
    flows: list
    hubs: frozenset
    seats: dict
    teams: list
    escalation: list
    pools: dict
    edits: Path | None


def rules_of(shape: dict | None, edits: Path | None = None) -> Rules:
    """`Rules` for a loaded structure (`snapshot_json.load_structure`; None: no drawn flows)."""
    return Rules(shape, shape["flows"] if shape else [], hub_names(shape), shape["seats"] if shape else {},
                 shape.get("teams", []) if shape else [],  # the groups: agents that report to one lead
                 shape["escalation"] if shape else [],  # escalates_to: a problem when a node's tiers break it
                 shape["pool"] if shape else {},  # pool: a warning when more nodes of one agent can run at once
                 edits)


def default_rules(structure: Path | None = None, home: Path | None = None) -> Rules:
    """`check`'s rules with no options: the orchestration's own structure folder (or `structure`) and the edit log of
    `home` (default: $AGENTGRAPH_HOME, else the active preset's memory folder)."""
    return rules_of(load_structure(structure), outputs.edit_home(home))


def _examine(raw: dict, path: Path, rules: Rules, folder: int | None, write: bool) -> tuple[TaskGraph, list[str], list[str], list[str]]:
    """Everything `check` knows about one readable graph: (graph, problems, unsaved-edit warnings, drift warnings).
    The one place the problem list is made; `check`, `record` and the views all use it."""
    graph, issues = _graph_and_issues(raw, path, rules.flows, rules.hubs, rules.seats, rules.teams, rules.escalation)
    issues = issues + outputs.lost_edits(graph, rules.edits)  # an edit by hand the graph no longer records
    unsaved = outputs.unsaved_edits(graph, rules.edits)  # a save stopped before the graph: a warning
    drift: list[str] = []
    if not _raw_issues(raw):
        drift = transitions.warnings(raw, path) + review_copy_warnings(raw, path)
        if folder is not None:
            issues = issues + _history_step(folder, path, raw, issues, graph, rules, write)
    return graph, issues, unsaved, drift


def _examine_read_only(raw: dict, path: Path, rules: Rules) -> tuple[TaskGraph, list[str]]:
    folder = None
    try:
        folder = osfs.open(path.resolve().parent, os.O_RDONLY | osfs.O_DIRECTORY)
    except OSError:
        pass
    try:
        graph, issues, _, _ = _examine(raw, path, rules, folder, False)
        return graph, issues
    finally:
        if folder is not None:
            osfs.close(folder)


def all_problems(path: Path, rules: Rules | None = None) -> list[str] | None:
    """Every problem `check` reports for the graph file `path` (lint, flows, escalation, groups, hand edits,
    history), or None if no graph can be read. Read-only: it compares the graph with what `check` last recorded
    and writes nothing, so `check` itself is the only writer. `record` and the snapshot use it, so their problems
    are `check`'s."""
    graph, issues = read_graph(path, rules if rules is not None else default_rules())
    return issues if graph is not None else None


def file_problems(path: Path) -> list[str] | None:
    """Every problem `check` would report, or None if no graph can be read (`all_problems`)."""
    return all_problems(path)


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
    rules = rules_of(shape, edits)
    flows, pools, seats = rules.flows, rules.pools, rules.seats

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
                graph, issues, unsaved_edits, drifted = _examine(raw, path, rules, folder, True)
                unsaved[id(graph)], drift[id(graph)] = unsaved_edits, drifted
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
