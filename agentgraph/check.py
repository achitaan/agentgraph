"""Validate task-graph files: `agentgraph check [--dir PATH] [--task NAME]`.

Exits 1 for invalid graphs, 2 if a file cannot be read or a requested
directory or task cannot be found, and 0 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from math import isfinite
from pathlib import Path

from .contract import (
    EVENT_NAMES, FAILURE_KINDS, GRAPH_LIMITS, GRAPH_STATUSES, MAX_NODE_LIST, NODE_LIST_FIELDS, NODE_STATUSES,
    NODE_TYPES, VERDICTS, TaskGraph, objective_coverage, shape_advice, validate,
)
from .sources import find_graph_dir, graph_files, graph_from_raw, read_graph_text
from .snapshot_json import load_structure
from .structure import drawn_loops


_NODE_STRINGS = ("id", "type", "agent", "goal", "done_when", "status")
_NODE_LISTS = ("depends_on", "scope", "tiers", "changed_paths", "feedback_to")
_NOT_OBJECT = "graph must be an object"
_INVALID_TASK = "graph task must be a non-blank string"
_MAX_LIST_ISSUES = 100


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


def loop_problems(graph: TaskGraph, loops) -> list[str]:
    """Drawn loops (`checked_by` in the structure folder) that the graph does not
    honour: every `implement` or `design` node that has not passed, whose agent
    has drawn checkers, needs a `gate` node run by each checker whose
    `feedback_to` names it. `loops` holds (work, check) pairs."""
    checkers: dict[str, list[str]] = {}
    for work, check in loops:
        checkers.setdefault(work, [])
        if check not in checkers[work]:
            checkers[work].append(check)
    if not checkers:
        return []
    gates = {(node.agent, target) for node in graph.nodes if node.type == "gate" for target in node.feedback_to}
    return [f"{node.id} ({node.agent}) has no check by {check}: the structure says {check} checks {node.agent}"
            for node in graph.nodes
            if node.type in ("implement", "design") and node.status != "passed"
            for check in checkers.get(node.agent, [])
            if (check, node.id) not in gates]


def _graph_and_issues(raw: dict, path: Path, loops=()) -> tuple[TaskGraph, list[str]]:
    issues = _raw_issues(raw)
    graph = graph_from_raw(raw, path)
    if issues:
        return graph, issues
    issues = validate(graph) + loop_problems(graph, loops)
    for node in graph.nodes:
        if node.type not in NODE_TYPES:
            issues.append(f"{node.id} has unknown type {node.type!r}")
        if node.status not in NODE_STATUSES:
            issues.append(f"{node.id} has unknown status {node.status!r}")
    if not graph.objectives:
        issues.append("no objectives declared")
    return graph, issues


def file_problems(path: Path) -> list[str] | None:
    """Every problem `check` would report, or None if no graph can be read."""
    graph, issues = read_graph(path)
    return issues if graph is not None else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph check", description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, help="directory of graph JSON files (default: nearest .opencode/graph)")
    parser.add_argument("--task", metavar="NAME", help="check only the graph with this declared task name")
    parser.add_argument("--structure", type=Path, metavar="DIR",
                        help="structure folder whose drawn loops the graphs must honour (default: the orchestration's own)")
    args = parser.parse_args(argv)
    # A missing default folder means no drawn loops; a folder named on the command line must exist,
    # so a mistyped path cannot turn the loop checks off without a word.
    if args.structure is not None and load_structure(args.structure) is None:
        _print(f"structure folder {args.structure} does not exist or holds no group folder")
        return 2
    loops = [(loop["work"], loop["check"]) for loop in drawn_loops(args.structure)]

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
    for path in files:
        raw, issues = _read_raw(path)
        graph = None
        if raw is not None:
            if args.task is not None and raw.get("task") != args.task:
                continue  # Another task's graph: never validated, so it cannot slow this check.
            graph, issues = _graph_and_issues(raw, path, loops)
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
        failed = failed or bool(issues)

    if (warnings or listing_warnings or discovery_warnings) and args.task is None:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
