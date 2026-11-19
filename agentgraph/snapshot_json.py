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


def snapshot_document(snapshot: Snapshot, source: str, task: str | None = None, *,
                      session: str | None = None) -> dict:
    """Project a snapshot onto the web contract without modifying the input.

    `session` is the session id the caller asked to watch (it may be a
    sub-agent's); it picks that session's graph when no `task` is given.
    """
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
        for name in [n for n in names if n != root_name and contract.group_of(n) == "hub"
                     and not any(item.busy for item in by_agent.get(n, []))]:
            del names[name]

    grouped = {group: [] for group in contract.GROUPS}
    for name in names:
        related = by_agent.get(name, [])
        configured = name in roster
        agent = roster.get(name)
        if agent is None:
            model, effort = (related[0].model, related[0].effort) if related else ("", "")
            agent = Agent(name, model or "", effort or "", contract.group_of(name), contract.POOL.get(name))
        group = "hub" if name == root_name else contract.group_of(name or "")
        grouped[group].append({
            "name": name, "group": group, "model": agent.model, "effort": agent.effort,
            "pool": agent.pool, "configured": configured, "checks": name in contract.CHECKERS,
            "skills": list(agent.skills),
            "team": contract.team_of(name or ""), "parent": contract.parent_of(name or "", names),
            "busy": sum(bool(session.busy) for session in related),
            "sessions": [_session_document(session) for session in related],
        })

    grouped["hub"].sort(key=lambda entry: entry["name"] != root_name)  # stable: the root first
    handoffs: set[tuple[str, str]] = set()
    busy_ids = {session.id for session in sessions if session.busy and session.id is not None}
    graph_doc = _graph_document(active, busy_ids, handoffs) if active is not None else None
    return _json_safe({
        "version": 1, "generated": snapshot.now, "source": source,
        "warnings": [*snapshot.warnings, *selection_warnings],
        "team": contract.team_of(root.agent or "") if root is not None else "main",
        "totals": {"sessions": len(sessions), "running": sum(bool(s.busy) for s in sessions),
                   "cost": sum(s.cost for s in sessions), "tokens_in": sum(s.tokens_in for s in sessions),
                   "tokens_out": sum(s.tokens_out for s in sessions)},
        "root": {**_session_document(root), "agent": root.agent, "directory": root.directory}
        if root is not None else None,
        "groups": [{"id": group, "title": contract.GROUP_TITLES[group],
                    "agents": [agent["name"] for agent in agents]}
                   for group, agents in grouped.items()],
        "agents": [agent for agents in grouped.values() for agent in agents],
        "escalation": [{"from": a, "to": b, "used": (a, b) in handoffs}
                       for a, b in contract.ESCALATION if a in names and b in names],
        "graph": graph_doc,
        "graphs": [{"task": graph.task, "status": graph.status, "updated": graph.updated}
                   for graph in graphs],
    })


def learning_document(home_root: Path | None = None, config_dir: Path | None = None) -> dict | None:
    """What the ledger has stored for the active preset: the user's notes in
    their own words, whether a prompt change is due, and each agent's record
    over recorded runs. None when there is no ledger to read."""
    try:
        from . import ledger
        root = Path(home_root) if home_root is not None else ORCHESTRATION_GRAPH_DIR.parent.parent
        preset = ""
        try:
            config = json.loads((Path(config_dir or CONFIG_DIR) / "oh-my-opencode-slim.json").read_text())
            preset = str(config.get("preset") or "")
        except Exception:  # noqa: BLE001
            pass
        home = root / f"memory-{preset}"
        if not preset or "/" in preset or not home.is_dir():
            home = root / "memory"
        if not home.is_dir():
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


def skills_document(config_dir: Path | None = None) -> list[dict]:
    """The installed skills: name and one-line description, read from the head of each SKILL.md."""
    found: list[dict] = []
    try:
        folders = sorted(path for path in (Path(config_dir or CONFIG_DIR) / "skills").iterdir() if path.is_dir())[:200]
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
    document = snapshot_document(snapshot, source, task, session=session)
    if source == "live":
        document["learning"] = _json_safe(learning_document())
        document["skills"] = skills_document()
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
