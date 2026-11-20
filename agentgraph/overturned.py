"""Verifier passes that were overturned, as evidence for the improvement loop: a `verifier` that passes work which
then has to be sent back, run again or corrected by hand is a prompt to fix.

A verifier pass is a gate node run by `verifier` that passed (verdict `confirmed`). The work it covered is its
`feedback_to` nodes, else the nodes it depends on. The pass is overturned when, after it, a covered node
- was sent back by a later gate (another gate naming it in `feedback_to` evaluated it as failed or refuted: the
  graph keeps that only in the words of the gate's `evaluated` event note, so those words are what is read, never
  shown), or
- was run again (a `dispatched`, `retried`, `attempt` or `escalated` event on it), or
- had an output edited by hand that was not undone (the edit log `edits.jsonl`, by task and node), or
if the user filed feedback naming `verifier` for that task (it overturns the task's latest pass before it).
The time of a pass is the gate's last `evaluated` event, else its `finished`; a pass with no time cannot be ordered
against anything: it counts as a pass and is never overturned by the graph or the edit log.

`summary` is what `record` stores in the run (only when the graph has a verifier pass); `signal` is what `signals`
shows: `verifier: K of N passes overturned` over the hand-edit window (default 14 days), due at 2 or more in 2 or more
tasks, silenced by an improvement recorded for `verifier` (its name in the file, summary, signals or size change).
Only plain names (handedits.OUTPUT_NAME) are stored; no text of a graph file is kept or printed."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .contract import TaskGraph
from .handedits import DAYS, LOG, LOG_READ, OUTPUT_NAME, _cutoff, _saved, _tail, default_paths

AGENT = "verifier"
OVERTURNED = 2   # overturned passes that make the signal due ...
TASKS = 2        # ... across at least this many tasks
LIST = 100       # passes kept in a run's summary
COVERS = 20      # nodes kept for each pass
RERUN = ("dispatched", "retried", "attempt", "escalated")
WAYS = ("sent_back", "run_again", "edited", "feedback")
_FAILED = re.compile(r"\b(refuted|failed|fail)\b", re.I)


def _num(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _time(node, events) -> float | None:
    times = [event.time for event in events.get(node.id, []) if event.event == "evaluated" and _num(event.time)]
    if times:
        return max(times)
    return node.finished if _num(node.finished) else None


def passes(graph: TaskGraph) -> list[dict]:
    """The verifier passes of a graph: [{"node", "time", "covers", "sent_back", "run_again"}], at most LIST."""
    events: dict[str, list] = {}
    for event in graph.events:
        events.setdefault(event.node, []).append(event)
    by_id = {node.id: node for node in graph.nodes}
    found = []
    for gate in graph.nodes:
        if not (gate.type == "gate" and gate.agent == AGENT and gate.status == "passed" and gate.verdict == "confirmed"
                and OUTPUT_NAME.fullmatch(gate.id)):
            continue
        named = gate.feedback_to or gate.depends_on
        covers = [name for name in dict.fromkeys(named) if name in by_id and name != gate.id and OUTPUT_NAME.fullmatch(name)]
        when = _time(gate, events)
        sent_back = run_again = False
        if when is not None:
            for name in covers:
                run_again = run_again or any(event.event in RERUN and _num(event.time) and event.time > when
                                             for event in events.get(name, []))
            for other in graph.nodes:
                if other.type == "gate" and other.id != gate.id and set(other.feedback_to) & set(covers):
                    sent_back = sent_back or any(
                        event.event == "evaluated" and _num(event.time) and event.time > when
                        and _FAILED.search(event.note or "") for event in events.get(other.id, []))
        found.append({"node": gate.id, "time": when, "covers": covers[:COVERS], "sent_back": sent_back, "run_again": run_again})
    return found[:LIST]


def edit_log(home: Path, backups: Path | None = None) -> list[dict]:
    """[{"time", "task", "node"}] of the edit log's edits not undone (a line whose save never reached the graph is
    not one). Missing or unreadable means none."""
    try:
        raw = _tail(Path(home) / LOG, LOG_READ)
    except OSError:
        return []
    found = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if (isinstance(item, dict) and _num(item.get("time")) and item.get("undone") is not True
                and isinstance(item.get("task"), str) and OUTPUT_NAME.fullmatch(item["task"])
                and isinstance(item.get("node"), str) and OUTPUT_NAME.fullmatch(item["node"]) and _saved(item, backups)):
            found.append({"time": item["time"], "task": item["task"], "node": item["node"]})
    return found


def ways(task: str, entries: list[dict], edits: list[dict], notes: list[dict]) -> list[set[str]]:
    """For each pass of a run, the ways it was overturned (a set of WAYS), from the graph's own flags kept with the
    pass, the edit log and the feedback notes."""
    found = [{way for way in ("sent_back", "run_again") if entry.get(way) is True} for entry in entries]
    for index, entry in enumerate(entries):
        when = entry.get("time")
        if when is None:
            continue
        covers = set(entry["covers"])
        if any(edit["task"] == task and edit["node"] in covers and edit["time"] > when for edit in edits):
            found[index].add("edited")
    for note in notes:
        if note.get("agent") != AGENT or note.get("task") != task or not _num(note.get("time")):
            continue
        before = [(entry["time"], index) for index, entry in enumerate(entries)
                  if entry.get("time") is not None and entry["time"] < note["time"]]
        if before:
            found[max(before)[1]].add("feedback")
    return found


def _valid_entry(entry) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get("node"), str) and OUTPUT_NAME.fullmatch(entry["node"]) is not None
            and (entry.get("time") is None or _num(entry["time"]))
            and isinstance(entry.get("covers"), list) and len(entry["covers"]) <= COVERS
            and all(isinstance(name, str) and OUTPUT_NAME.fullmatch(name) for name in entry["covers"])
            and all(isinstance(entry.get(way), bool) for way in ("sent_back", "run_again")))


def _entries(run: dict) -> list[dict]:
    stored = run.get("verifier")
    items = stored.get("list") if isinstance(stored, dict) else None
    return [entry for entry in items[:LIST] if _valid_entry(entry)] if isinstance(items, list) else []


def summary(graph: TaskGraph, home: Path, notes: list[dict], backups: Path | None = None) -> dict | None:
    """What `record` keeps in a run for its verifier passes (None without any): the counts, the ids of the overturned
    passes, the ways, and the passes themselves (so `signals` can add what happens after the record)."""
    entries = passes(graph)
    if not entries:
        return None
    marked = ways(graph.task, entries, edit_log(home, backups), notes)
    return {"passes": len(entries), "overturned": sum(bool(found) for found in marked),
            "nodes": [entry["node"] for entry, found in zip(entries, marked) if found],
            "ways": {way: sum(way in found for found in marked) for way in WAYS}, "list": entries}


def measure(runs: list[dict], edits: list[dict], notes: list[dict], since: float, cutoff: float | None) -> dict:
    """{"passes", "overturned", "tasks", "ways"} over the passes at or after `since` and after `cutoff`. A pass is
    timed by its own time, else by when its run was recorded."""
    total = overturned = 0
    tasks: set[str] = set()
    counts = {way: 0 for way in WAYS}
    for run in runs:
        entries = _entries(run)
        if not entries or not isinstance(run.get("task"), str):
            continue
        marked = ways(run["task"], entries, edits, notes)
        for entry, found in zip(entries, marked):
            anchor = entry["time"] if entry["time"] is not None else run["recorded"]
            if anchor < since or (cutoff is not None and anchor <= cutoff):
                continue
            total += 1
            if found:
                overturned += 1
                tasks.add(run["task"])
                for way in found:
                    counts[way] += 1
    return {"passes": total, "overturned": overturned, "tasks": len(tasks), "ways": counts}


def signal(home: Path, runs: list[dict], notes: list[dict], improvements: list[dict], now: float, *,
           days: float = DAYS, min_overturned: int = OVERTURNED, min_tasks: int = TASKS,
           backups: Path | None = None) -> dict:
    default_backups, _ = default_paths(home)
    edits = edit_log(home, default_backups if backups is None else backups)
    found = measure(runs, edits, notes, now - days * 86400, _cutoff(improvements, AGENT))
    return {**found, "days": days, "thresholds": {"overturned": min_overturned, "tasks": min_tasks},
            "due": found["overturned"] >= min_overturned and found["tasks"] >= min_tasks}


def signal_lines(report: dict) -> list[str]:
    """The line `record` and `signals` print: none when no pass was overturned in the window."""
    if not report or not report.get("overturned"):
        return []
    days = f"{report['days']:g} day" + ("s" if report["days"] != 1 else "")
    line = f"verifier: {report['overturned']} of {report['passes']} passes overturned in {days}"
    if report.get("due"):
        return [f"improvement due: {line} across {report['tasks']} tasks (sent back, run again, edited by hand or "
                f"named in feedback after the pass): propose one edit to the verifier's instruction"]
    return [line]
