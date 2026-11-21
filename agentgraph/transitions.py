"""What a task graph may do between two states: the history `check` keeps and the rules it applies to a change.

`check` used to be a lint of one file: it had no memory, so a graph could lose passed work, rewrite its own events or
weaken its criteria and still pass. Now every graph that passes `check` leaves a small record of its state
(`.opencode/graph/.history/<file stem>.state`, a folder `sources.graph_files` never lists, written under the graph
folder's lock by `agentgraph check` and by `agentgraph graph set|event`). The next run compares the graph with that
record. With no record (a first run, or `check --no-history`) only the lint runs, so no graph is broken retroactively.

`problems(previous, raw)` are failures (`check` exits 1); `warnings(raw, path)` need no history and never fail.
GRAPH_FORMAT.md lists the rules; the codes in the comments (T2, G4, ...) are those of the audit in
`.opencode/artifacts/agentgraph-improve/n2/r1/audit-execution.md`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

VERSION = 1
HISTORY_DIR = ".history"
SUFFIX = ".state"
FLOWS_SUFFIX = ".flows"  # the flows recorded for a graph (flows_record)
STATE_LIMIT = 16 * 1024 * 1024  # bytes read back
FUTURE_SECONDS = 3600  # a new event dated further ahead than this is a clock error (E5)
STALL_ATTEMPTS = 3  # attempts at which a node that is not stalled is a "stall candidate" (T8)
MAX_PER_RULE = 5  # messages of one rule kept per graph; the rest is counted

NON_WORKER_AGENTS = ("orchestrator", "user")
NON_WORKER_TYPES = ("check", "join", "human")
DISPATCH_EVENTS = ("dispatched", "attempt")
EVALUATION_EVENTS = ("evaluated", "joined", "completed")
RESTART_EVENTS = ("dispatched", "retried", "invalidated")  # a node's work starts again after one of these
RETRY_FROM = ("failed", "blocked", "returned")


class HistoryError(Exception):
    """The history record exists but cannot be used."""


def _digest(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _dicts(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _nodes(raw: dict) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for node in _dicts(raw.get("nodes")):
        if isinstance(node.get("id"), str):
            found.setdefault(node["id"], node)
    return found


def _int(value, default: int = 0) -> int:
    return value if type(value) is int else default


def _revisions(entries, keys: tuple[str, str]) -> dict[str, int]:
    return {f"{entry.get(keys[0])}/{entry.get(keys[1])}": entry["revision"] for entry in _dicts(entries)
            if type(entry.get("revision")) is int}


def _events(raw: dict) -> list:
    return raw["events"] if isinstance(raw.get("events"), list) else []


def _event_time(event):
    time_ = event.get("time") if isinstance(event, dict) else None
    return time_ if isinstance(time_, (int, float)) and not isinstance(time_, bool) else None


# --- the record ----------------------------------------------------------------------------------------------


def state_of(raw: dict) -> dict:
    """What the rules need of a graph: per node its status, attempts, verdict, stalled, started, finished, input and
    artifact revisions and a hash of `done_when`; a hash of each objective's text; revision, amendments (count),
    created, updated; the number of events, a hash of the event list and the last event's time. Never any text."""
    events = _events(raw)
    return {
        "version": VERSION,
        "revision": _int(raw.get("revision"), 1),
        "amendments": len(raw["amendments"]) if isinstance(raw.get("amendments"), list) else 0,
        "created": raw.get("created"),
        "updated": raw.get("updated"),
        "objectives": {str(o.get("id")): _digest(o.get("text")) for o in _dicts(raw.get("objectives"))},
        "nodes": {
            node_id: {
                "status": node.get("status"),
                "attempts": _int(node.get("attempts")),
                "verdict": node.get("verdict") or "",
                "stalled": node.get("stalled") is True,
                "started": node.get("started"),
                "finished": node.get("finished"),
                "done_when": _digest(node.get("done_when")),
                "inputs": _revisions(node.get("inputs"), ("from", "artifact")),
                "artifacts": _revisions(node.get("artifacts"), ("id", "id")),
            } for node_id, node in _nodes(raw).items()},
        "events": {"count": len(events), "hash": _digest(events),
                   "last_time": _event_time(events[-1]) if events else None},
    }


def _open_history(folder: int, *, create: bool) -> int | None:
    if create:
        try:
            os.mkdir(HISTORY_DIR, 0o700, dir_fd=folder)
        except FileExistsError:
            pass
    try:
        return os.open(HISTORY_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
    except FileNotFoundError:
        return None


def _read_record(folder: int, name: str, what: str) -> dict | None:
    """The JSON object in `.history/<name>` of the graph folder `folder`; None when there is none."""
    try:
        history = _open_history(folder, create=False)
    except OSError as exc:
        raise HistoryError(f"history folder unusable ({exc.strerror}); ignored") from None
    if history is None:
        return None
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=history)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise HistoryError(f"{what} unusable ({exc.strerror}); ignored") from None
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise HistoryError(f"{what} is not a regular file; ignored")
            data = stream.read(STATE_LIMIT + 1)
    finally:
        os.close(history)
    try:
        record = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HistoryError(f"{what} is not a readable record; ignored") from None
    if not isinstance(record, dict) or record.get("version") != VERSION:
        raise HistoryError(f"{what} is not a readable record; ignored")
    return record


def load_state(folder: int, stem: str) -> dict | None:
    """The record of the graph file `stem` in the graph folder `folder` (an open directory descriptor); None when
    there is none. HistoryError when it cannot be used (the caller treats that as no history)."""
    state = _read_record(folder, stem + SUFFIX, f"history of {stem}")
    if state is not None and (not isinstance(state.get("nodes"), dict) or not isinstance(state.get("objectives"), dict)
                              or not isinstance(state.get("events"), dict)):
        raise HistoryError(f"history of {stem} is not a readable record; ignored")
    return state


def load_flows(folder: int, stem: str) -> dict | None:
    """The flows recorded for the graph file `stem` (`check` writes them, see `flows_record`); None when there are
    none. HistoryError when the record cannot be used."""
    record = _read_record(folder, stem + FLOWS_SUFFIX, f"recorded flows of {stem}")
    if record is not None and (not isinstance(record.get("nodes"), dict) or not isinstance(record.get("released"), dict)
                               or type(record.get("amendments")) is not int
                               or not all(_ids(v) for v in (*record["nodes"].values(), *record["released"].values()))):
        raise HistoryError(f"recorded flows of {stem} are not a readable record; ignored")
    return record


def _ids(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def save_state(folder: int, stem: str, state: dict) -> bool:
    """Write the record atomically (a temporary file in the same folder, fsync, rename), under the graph folder's
    lock the caller holds. False when the record on disk was already this one. Raises OSError."""
    return _write_record(folder, stem + SUFFIX, state)


def save_flows(folder: int, stem: str, record: dict) -> bool:
    """`save_state` for the recorded flows."""
    return _write_record(folder, stem + FLOWS_SUFFIX, record)


def _write_record(folder: int, name: str, state: dict) -> bool:
    data = (json.dumps(state, sort_keys=True, indent=1) + "\n").encode("utf-8")
    history = _open_history(folder, create=True)
    if history is None:
        raise OSError("history folder missing")
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=history)
            with os.fdopen(fd, "rb") as stream:
                if stream.read(len(data) + 1) == data:
                    return False
        except OSError:
            pass
        temporary = f".{name}.{secrets.token_hex(6)}.tmp"
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=history)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=history, dst_dir_fd=history)
            os.fsync(history)
        finally:
            try:
                os.unlink(temporary, dir_fd=history)
            except FileNotFoundError:
                pass
    finally:
        os.close(history)
    return True


def flows_record(previous: dict | None, applied: dict[str, list[str]], amendments, known: set[str]) -> dict:
    """The recorded flows after one more look at a graph: for each node the ids of the drawn flows it has been seen
    following or owing (`applied`: this run's), added to those recorded before, and kept whatever the node becomes
    (retyped, given to another agent, passed). Two things release a flow: the user removing it from the structure
    (it is not in `known`), and an amendment added since the last look (`amendments`, the graph's list) that
    names the node and says "flow"; a released flow is never recorded for that node again (`released`). The record
    holds ids only, never text."""
    previous = previous or {}
    nodes_before = previous.get("nodes", {})
    released = {node_id: [fid for fid in ids if fid in known] for node_id, ids in previous.get("released", {}).items()}
    amendments = [text for text in amendments if isinstance(text, str)]
    for text in amendments[min(_int(previous.get("amendments"), len(amendments)), len(amendments)):]:
        if "flow" not in text.lower():
            continue
        for node_id in set(nodes_before) | set(applied):
            if re.search(rf"(?<![\w-]){re.escape(node_id)}(?![\w-])", text):
                released[node_id] = list(dict.fromkeys(
                    released.get(node_id, []) + nodes_before.get(node_id, []) + applied.get(node_id, [])))
    nodes = {}
    for node_id in set(nodes_before) | set(applied):
        ids = [fid for fid in dict.fromkeys(nodes_before.get(node_id, []) + applied.get(node_id, []))
               if fid in known and fid not in released.get(node_id, [])]
        if ids:
            nodes[node_id] = ids
    return {"version": VERSION, "amendments": len(amendments), "nodes": nodes,
            "released": {node_id: ids for node_id, ids in released.items() if ids}}


# --- the rules -----------------------------------------------------------------------------------------------


def _capped(messages: list[str], rule: str) -> list[str]:
    if len(messages) <= MAX_PER_RULE:
        return messages
    return messages[:MAX_PER_RULE] + [f"... and {len(messages) - MAX_PER_RULE} more ({rule})"]


def _last_start(events: list, node_id: str) -> int:
    last = -1
    for i, event in enumerate(events):
        if isinstance(event, dict) and event.get("node") == node_id and event.get("event") in RESTART_EVENTS:
            last = i
    return last


def _is_worker(node: dict) -> bool:
    return node.get("agent") not in NON_WORKER_AGENTS and node.get("type") not in NON_WORKER_TYPES


def problems(prev: dict, raw: dict, now: float | None = None) -> list[str]:
    """The ways `raw` may not follow from `prev` (a record `state_of` made of the last graph that passed `check`).
    `raw` must already have passed `check`'s type checks. One plain sentence each."""
    now = time.time() if now is None else now
    found: list[str] = []
    nodes = _nodes(raw)
    events = _events(raw)
    before = prev["events"]
    count = _int(before.get("count"))
    known = set(nodes) | set(prev["nodes"])
    new_events = list(enumerate(events))[count:]

    # events: append-only, in time order, about known nodes
    if len(events) < count:
        found.append(f"events were deleted: {count} were recorded before, {len(events)} remain (events are append-only)")
    elif _digest(events[:count]) != before.get("hash"):
        found.append("earlier events were edited (events are append-only: add a new event instead)")
    timing, unknown, future = [], [], []
    for i, event in new_events:
        if not isinstance(event, dict):
            continue
        label = f"event {i + 1} ({event.get('event')} {event.get('node') or 'graph'})"
        at = _event_time(event)
        earlier = before.get("last_time") if i == count else _event_time(events[i - 1])
        if at is not None and isinstance(earlier, (int, float)) and at < earlier:
            timing.append(f"{label} is dated before the event before it")
        if event.get("node") and event["node"] not in known:
            unknown.append(f"{label} names the unknown node {event['node']!r}")
        if event.get("event") == "completed" and raw.get("status") != "passed":
            found.append(f"{label}: completed, but the graph is not passed")
        if at is not None and at > now + FUTURE_SECONDS:
            future.append(f"{label} is dated more than an hour in the future")
    found += _capped(timing, "event order") + _capped(unknown, "event nodes") + _capped(future, "event times")

    invalidated = {e.get("node") for _, e in new_events if isinstance(e, dict) and e.get("event") == "invalidated"}

    # passed work stays passed
    lost = []
    for node_id, old in prev["nodes"].items():
        if old.get("status") != "passed" or node_id in invalidated:
            continue
        if node_id not in nodes:
            lost.append(f"{node_id} was passed and is gone from the graph (an invalidated event is needed to remove passed work)")
        elif nodes[node_id].get("status") != "passed":
            lost.append(f"{node_id} was passed and is now {nodes[node_id].get('status')} (an invalidated event is needed "
                        "to reopen passed work)")
    found += _capped(lost, "passed work")

    passed_problems, retries, lowered, stalls, gates, spans, artifacts, reads = [], [], [], [], [], [], [], []
    for node_id, node in nodes.items():
        old = prev["nodes"].get(node_id)
        status = node.get("status")
        attempts = _int(node.get("attempts"))
        if status == "passed" and (old is None or old.get("status") != "passed"):
            for dep in node.get("depends_on") or []:
                if dep in nodes and nodes[dep].get("status") != "passed":
                    passed_problems.append(f"{node_id} is newly passed while its dependency {dep} is {nodes[dep].get('status')}")
            mine = [e for e in events if isinstance(e, dict) and e.get("node") == node_id]
            if _is_worker(node):
                if not any(e.get("event") in DISPATCH_EVENTS for e in mine):
                    passed_problems.append(f"{node_id} ({node.get('agent')}) is newly passed but was never dispatched "
                                           "(no dispatched or attempt event)")
                elif not node.get("session") and attempts == 0:
                    passed_problems.append(f"{node_id} ({node.get('agent')}) is newly passed with no session and no attempts")
            start = _last_start(events, node_id)
            if not any(isinstance(e, dict) and e.get("node") == node_id and e.get("event") in EVALUATION_EVENTS
                       for e in events[start + 1:]):
                passed_problems.append(f"{node_id} is newly passed with no evaluated, joined or completed event since it "
                                       "last ran (no completion without evaluation)")
        old = old or {}
        if old.get("status") in RETRY_FROM and status == "running" and attempts <= _int(old.get("attempts")):
            retries.append(f"{node_id} went from {old['status']} to running without attempts going up (still {attempts})")
        if attempts < _int(old.get("attempts")):
            lowered.append(f"{node_id} attempts went down from {old['attempts']} to {attempts}")
        if status == "passed" and node.get("stalled") is True and not old.get("stalled"):
            stalls.append(f"{node_id} is passed but newly marked stalled")
        if node.get("type") == "gate" and status == "pending" and node.get("verdict") == "confirmed" \
                and old.get("verdict") != "confirmed":
            gates.append(f"gate {node_id} is still pending but already carries the verdict confirmed")
        started, finished = node.get("started"), node.get("finished")
        if isinstance(started, (int, float)) and isinstance(finished, (int, float)) and started > finished \
                and (old.get("started"), old.get("finished")) != (started, finished):
            spans.append(f"{node_id} started after it finished")
        for key, revision in _revisions(node.get("artifacts"), ("id", "id")).items():
            was = old.get("artifacts", {}).get(key)
            if isinstance(was, int) and revision < was:
                artifacts.append(f"{node_id} artifact {key.split('/')[0]!r} went back from revision {was} to {revision}")
        if status == "passed" and old.get("status") == "passed" and attempts <= _int(old.get("attempts")):
            for key, revision in _revisions(node.get("inputs"), ("from", "artifact")).items():
                was = old.get("inputs", {}).get(key)
                if isinstance(was, int) and revision > was:
                    reads.append(f"{node_id} is still passed but now reads {key} revision {revision} (was {was}) with no "
                                 "new attempt: it has to run again to have read it")
    found += (_capped(passed_problems, "new passes") + _capped(retries, "retries") + _capped(lowered, "attempts")
              + _capped(stalls, "stalled") + _capped(gates, "gate verdicts") + _capped(spans, "times")
              + _capped(artifacts, "artifact revisions") + _capped(reads, "input revisions"))

    # the graph itself: revision, amendments, times
    revision = _int(raw.get("revision"), 1)
    amendments = len(raw["amendments"]) if isinstance(raw.get("amendments"), list) else 0
    if revision < _int(prev.get("revision"), 1):
        found.append(f"revision went down from {prev.get('revision')} to {revision}")
    changed = [f"objective {o_id} text" for o_id, digest in prev["objectives"].items()
               if o_id not in {str(o.get("id")) for o in _dicts(raw.get("objectives"))}
               or _digest(next(o.get("text") for o in _dicts(raw.get("objectives")) if str(o.get("id")) == o_id)) != digest]
    changed += [f"{node_id} done_when" for node_id, old in prev["nodes"].items()
                if node_id in nodes and _digest(nodes[node_id].get("done_when")) != old.get("done_when")]
    if changed and not (revision > _int(prev.get("revision"), 1) and amendments > _int(prev.get("amendments"))):
        found.append(f"{', '.join(changed[:5])}{' and more' if len(changed) > 5 else ''} changed without an amendment "
                     f"(revision must go up and amendments must get a new line: revision {revision}, "
                     f"{amendments} amendments, was {prev.get('revision')} and {prev.get('amendments')})")
    created, updated = raw.get("created"), raw.get("updated")
    if isinstance(updated, (int, float)) and isinstance(prev.get("updated"), (int, float)) and updated < prev["updated"]:
        found.append(f"updated went back from {prev['updated']} to {updated}")
    if isinstance(updated, (int, float)) and isinstance(created, (int, float)) and updated < created:
        found.append(f"updated ({updated}) is before created ({created})")
    return found


def warnings(raw: dict, path) -> list[str]:
    """What looks wrong without any history; never fails `check`. The task name against the file's name, a passed
    node's declared artifact file missing on disk, two agents sharing one session, a node with many attempts that is
    not marked stalled."""
    from .outputs import project_of  # outputs imports the contract; keep this module cheap to import
    found: list[str] = []
    path = Path(path)
    nodes = _nodes(raw)
    if isinstance(raw.get("task"), str) and raw["task"] != path.stem:
        found.append(f"task {raw['task']!r} differs from the file name {path.name!r}")
    project = project_of(path)
    if project is not None:
        missing = [f"{node_id} artifact {entry.get('id')!r}: {entry.get('path')} does not exist"
                   for node_id, node in nodes.items() if node.get("status") == "passed"
                   for entry in _dicts(node.get("artifacts"))
                   if isinstance(entry.get("path"), str) and not os.path.lexists(project / entry["path"])]
        found += _capped(missing, "missing artifacts")
    by_session: dict[str, list[tuple[str, str]]] = {}
    for node_id, node in nodes.items():
        if isinstance(node.get("session"), str) and node["session"]:
            by_session.setdefault(node["session"], []).append((node_id, str(node.get("agent"))))
    shared = [f"session {session} is used by {', '.join(f'{i} ({a})' for i, a in users)}"
              for session, users in by_session.items() if len({agent for _, agent in users}) > 1]
    found += _capped(shared, "shared sessions")
    candidates = [f"{node_id} has {_int(node.get('attempts'))} attempts and is not marked stalled (stall candidate: "
                  "same evidence again? mark it stalled or change the approach)"
                  for node_id, node in nodes.items()
                  if node.get("status") != "passed" and _int(node.get("attempts")) >= STALL_ATTEMPTS
                  and node.get("stalled") is not True]
    found += _capped(candidates, "stall candidates")
    return found
