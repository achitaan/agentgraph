"""Where a task graph stands now: what is ready, what is really running, what needs a look.

Read-only. It reads the graph file and the OpenCode database and writes nothing, so an orchestrator can
resume after a restart from it instead of from memory: `agentgraph status --task T [--dir D] [--json]`.
Liveness (live, quiet, orphan, unreported) is derived from the database, never stored in the graph."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from collections import Counter
from pathlib import Path

from . import check, contract
from .contract import Node, TaskGraph
from .sources import DB_PATH, _read_checked, find_graph_dir, graph_files, graph_from_raw

STALL_ATTEMPTS = 3
_RETRY_EVENTS = ("attempt", "retried")


def find_graph(task: str, graph_dir: Path) -> tuple[TaskGraph | None, str]:
    """The graph declaring `task` in `graph_dir`, or (None, reason)."""
    try:
        files, _ = graph_files(graph_dir)
    except OSError as exc:
        return None, f"graph directory {graph_dir}: {exc}"
    for path in files:
        raw, _ = check._read_raw(path)
        if raw is not None and raw.get("task") == task:
            return graph_from_raw(raw, path), ""
    return None, f"task {task!r} not found in {graph_dir}"


def stall_reasons(node: Node, graph: TaskGraph) -> list[str]:
    """Why a node that is not passed looks stuck; empty when it does not."""
    if node.status == "passed":
        return []
    reasons = []
    if node.attempts >= STALL_ATTEMPTS:
        reasons.append(f"{node.attempts} attempts")
    if node.stalled:
        reasons.append("marked stalled")
    events = [event for event in graph.events if event.node == node.id]
    retries = Counter(event.note.strip() for event in events if event.event in _RETRY_EVENTS and event.note.strip())
    if any(count >= 2 for count in retries.values()):
        reasons.append("two attempts with the same note")
    results = Counter(event.note.strip() for event in events if event.event == "evaluated" and event.note.strip())
    if node.attempts >= 2 and any(count >= 2 for count in results.values()):
        reasons.append("evidence unchanged across retries")
    return reasons


def check_summary(task: str, graph_dir: Path) -> dict:
    """The existing `check` for this task, in one line: ok, the headline, and the problems."""
    out, code = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(out):
            code = check.main(["--task", task, "--dir", str(graph_dir)])
    except SystemExit as exc:  # argparse
        code = exc.code if isinstance(exc.code, int) else 2
    lines = out.getvalue().splitlines()
    problems = [line.strip()[2:] for line in lines if line.startswith("            - ")]
    headline = next((line for line in lines if line.split(" ", 1)[0] in ("OK", "PROBLEMS")), lines[0] if lines else "")
    return {"ok": code == 0 and not problems, "exit": code, "headline": " ".join(headline.split()),
            "problems": problems, "warnings": sum(line.startswith("warning:") for line in lines)}


def build_status(graph: TaskGraph, known, now: float | None = None, check_result: dict | None = None) -> dict:
    """The status document for one graph. `known` is every session by id, or None when the database is unreadable."""
    now = time.time() if now is None else now
    by_id = {node.id: node for node in graph.nodes}
    stale = contract.stale_inputs(graph)
    stale_nodes = {entry[0] for entry in stale}
    liveness = contract.graph_liveness(graph, known)
    counts = {kind: sum(state == kind for state, _ in liveness.values()) for kind in contract.LIVENESS}
    ready = [node.id for node in graph.nodes
             if node.status == "pending" and node.id not in stale_nodes
             and all(dep in by_id and by_id[dep].status == "passed" for dep in node.depends_on)]
    running = []
    for node in graph.nodes:
        if node.status != "running":
            continue
        state, idle_since = liveness[node.id]
        running.append({"id": node.id, "agent": node.agent, "session": node.session, "liveness": state,
                        "idle_since": idle_since,
                        "idle_seconds": max(0.0, now - idle_since) if idle_since is not None else None,
                        "started": node.started})
    return {
        "task": graph.task, "status": graph.status, "revision": graph.revision, "path": graph.path,
        "progress": {"passed": sum(node.status == "passed" for node in graph.nodes), "total": len(graph.nodes)},
        "database": known is not None,
        "check": check_result,
        "ready": ready,
        "running": running,
        "returned": [node.id for node in graph.nodes if node.status == "returned"],
        "unreported": [{"id": node.id, "status": node.status, "session": node.session}
                       for node in graph.nodes if liveness[node.id][0] == "unreported"],
        "stale_inputs": [{"node": node_id, "from": producer, "artifact": artifact, "read": read, "current": current}
                         for node_id, producer, artifact, read, current in stale],
        "passed_inputs_changed": [node.id for node in graph.nodes if node.status == "passed" and node.id in stale_nodes],
        "stall_candidates": [{"id": node.id, "reasons": reasons} for node in graph.nodes
                             if (reasons := stall_reasons(node, graph))],
        "liveness": counts,
    }


def _age(seconds: float | None) -> str:
    if seconds is None:
        return ""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def render(doc: dict) -> str:
    """Plain text, short: one line per kind of thing."""
    progress = doc["progress"]
    lines = [f"{doc['task']}  revision {doc['revision']}  {doc['status']}  {progress['passed']}/{progress['total']} passed"]
    result = doc["check"]
    if result is not None:
        lines.append("check: " + ("OK" if result["ok"] else f"PROBLEMS ({len(result['problems'])})")
                     + (f"  {result['problems'][0]}" if result["problems"] else ""))
    if not doc["database"]:
        lines.append("database: unreadable, liveness unknown")
    lines.append("ready: " + (" ".join(doc["ready"]) or "none"))
    if doc["running"]:
        parts = []
        for item in doc["running"]:
            label = item["liveness"] or "unknown"
            if item["idle_seconds"] is not None:
                label += f" {_age(item['idle_seconds'])}"
            parts.append(f"{item['id']} {label}")
        lines.append("running: " + ", ".join(parts))
    else:
        lines.append("running: none")
    if doc["returned"]:
        lines.append("returned, to evaluate: " + " ".join(doc["returned"]))
    if doc["unreported"]:
        lines.append("busy but not running in the graph: "
                     + ", ".join(f"{item['id']} ({item['status']})" for item in doc["unreported"]))
    if doc["stale_inputs"]:
        lines.append("stale inputs: " + ", ".join(
            f"{item['node']} <- {item['from']}/{item['artifact']} r{item['read']} (now r{item['current']})"
            for item in doc["stale_inputs"]))
    if doc["passed_inputs_changed"]:
        lines.append("passed, inputs changed: " + " ".join(doc["passed_inputs_changed"]))
    if doc["stall_candidates"]:
        lines.append("stall candidates: " + ", ".join(
            f"{item['id']} ({'; '.join(item['reasons'])})" for item in doc["stall_candidates"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None, now: float | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph status", description=__doc__.splitlines()[0])
    parser.add_argument("--task", metavar="NAME", required=True, help="the declared task name")
    parser.add_argument("--dir", type=Path, help="directory of graph JSON files (default: nearest .opencode/graph)")
    parser.add_argument("--db", type=Path, help="OpenCode database (default: the user's)")
    parser.add_argument("--json", action="store_true", help="print the status document as JSON")
    args = parser.parse_args(argv)
    graph_dir = args.dir if args.dir is not None else find_graph_dir(Path.cwd())
    if graph_dir is None:
        print("no .opencode/graph directory found from here", file=sys.stderr)
        return 2
    graph, reason = find_graph(args.task, graph_dir)
    if graph is None:
        print(reason, file=sys.stderr)
        return 2
    sessions, _ = _read_checked(args.db or DB_PATH)
    known = {item.id: item for item in sessions} if sessions is not None else None
    doc = build_status(graph, known, now=now, check_result=check_summary(args.task, graph_dir))
    print(json.dumps(doc, indent=2) if args.json else render(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
