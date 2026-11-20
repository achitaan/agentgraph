"""Record task runs, feedback and prompt improvements; report their signals."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from itertools import groupby
from pathlib import Path

from . import check, cost, sources
from .contract import FAILURE_KINDS, VERDICTS, TaskGraph, objective_coverage

DUE_RUNS = 5
DUE_FEEDBACK = 2

_RUNS = "runs.jsonl"
_FEEDBACK = "feedback.jsonl"
_FEEDBACK_HANDLED = "feedback-handled.jsonl"
_IMPROVEMENTS = "improvements.jsonl"
_Row = tuple[bytes, dict | None]
_MESSAGE_COUNT = 150
_MESSAGE_LENGTH = 600
_MESSAGE_BUDGET = 30_000
_MESSAGE_ROWS = """
    SELECT m.id, m.time_created, s.title, s.directory, m.data
    FROM session_message AS m JOIN session_v2 AS s ON s.id = m.session_id
    WHERE s.parent_id IS NULL AND m.type = 'user'
"""


def _number(value) -> bool:
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def _strings(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _worker_cost(run: dict) -> float | None:
    return run.get("worker_cost", run.get("cost"))  # Read pre-rename ledgers, too.


def _outcome(run: dict) -> str | None:
    return run.get("outcome")


def _tiers(tiers: list[str]) -> list[str]:
    return [agent for agent, _ in groupby(tiers)]


def _valid_cost_sessions(counts) -> bool:
    return (isinstance(counts, dict) and type(counts.get("known")) is int
            and type(counts.get("referenced")) is int and 0 <= counts["known"] <= counts["referenced"])


def _valid_node(node) -> bool:
    return (
        isinstance(node, dict)
        and all(isinstance(node.get(field), str) for field in
                ("id", "type", "agent", "status", "failure_kind", "verdict"))
        and type(node.get("attempts")) is int
        and _strings(node.get("tiers"))
        and all(isinstance(node.get(field), bool) for field in ("escalated", "stalled"))
    )


def _valid_run(run: dict) -> bool:
    cost = _worker_cost(run)
    return (
        all(isinstance(run.get(field), str) for field in ("path", "task"))
        and all(_number(run.get(field)) for field in ("updated", "recorded"))
        and _number(run.get("created", 0))
        and (cost is None or _number(cost))
        and ("worker_cost_sessions" not in run or _valid_cost_sessions(run["worker_cost_sessions"]))
        and _outcome(run) in ("passed", "abandoned", None)
        and type(run.get("amendments")) is int
        and _strings(run.get("lessons")) and _strings(run.get("discovered"))
        and isinstance(run.get("nodes"), list) and all(map(_valid_node, run["nodes"]))
    )


def _valid_feedback(item: dict) -> bool:
    return (
        _number(item.get("time")) and isinstance(item.get("text"), str)
        and all(item.get(field) is None or isinstance(item[field], str) for field in ("agent", "task"))
    )


def _valid_improvement(item: dict) -> bool:
    return _number(item.get("time")) and _valid_improvement_fields(item)


def _valid_improvement_fields(item: dict) -> bool:
    return (
        (isinstance(item.get("file"), str) or _strings(item.get("file")))
        and isinstance(item.get("summary"), str) and _strings(item.get("signals", []))
        and (item.get("metric") is None or isinstance(item["metric"], str))
        and ("seen_until" not in item or _number(item["seen_until"]))
        and all(field not in item or _sizes(item[field]) for field in ("size_change", "fixed_chars", "size_actual"))
    )


def _sizes(value) -> bool:
    """{agent: characters}: a proposal's size change, or the fixed text measured when it was recorded."""
    return isinstance(value, dict) and all(isinstance(k, str) and k and type(v) is int for k, v in value.items())


def _valid_handled(handled) -> bool:
    return isinstance(handled, dict) and bool(handled) and all(
        isinstance(note_id, str) and note_id and isinstance(reason, str) and reason.strip()
        for note_id, reason in handled.items()
    )


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _read(path: Path, valid: Callable[[dict], bool]) -> list[_Row]:
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return []
    rows = []
    with stream:
        for line in stream:
            try:
                item = json.loads(line, parse_float=_finite_float, parse_constant=_finite_float)
            except (ValueError, RecursionError):
                item = None
            rows.append((line, item if isinstance(item, dict) and valid(item) else None))
    return rows


def _records(rows: list[_Row]) -> list[dict]:
    return [item for _, item in rows if item is not None]


def _feedback_notes(home: Path) -> list[dict]:
    counts: Counter[str] = Counter()
    notes = []
    for line, note in _read(home / _FEEDBACK, _valid_feedback):
        if note is None:
            continue
        # Derive stable IDs without rewriting old lines; distinguish identical notes.
        digest = hashlib.sha256(line.removesuffix(b"\n")).hexdigest()
        counts[digest] += 1
        notes.append({**note, "id": f"{digest}-{counts[digest]}"})
    return sorted(notes, key=lambda item: item["time"])


def _valid_message_checkpoint(item: dict) -> bool:
    through = item.get("messages_through")
    keep = item.get("keep_messages", [])
    return (type(through) is int and 0 <= through < 2**63 and type(item.get("keep_backlog", False)) is bool
            and _strings(keep)
            and len(keep) <= _MESSAGE_COUNT and all(i.startswith("msg:") and len(i) > 4 for i in keep))


def _handled_state(home: Path) -> tuple[set[str], int, list[str], dict]:
    rows = _read(home / _FEEDBACK_HANDLED,
                 lambda row: _valid_handled(row.get("handled")) or _valid_message_checkpoint(row))
    handled: set[str] = set()
    through, keep, latest = -1, [], {}
    for item in _records(rows):
        # Checkpoint-only rows have an empty handled map.
        if _valid_handled(item.get("handled")):
            handled.update(item["handled"])
        if _valid_message_checkpoint(item) and item["messages_through"] >= latest.get("messages_through", -1):
            latest = {k: item[k] for k in ("messages_through", "keep_messages", "keep_backlog") if k in item}
            if not item.get("keep_backlog"):
                through, keep = item["messages_through"], item.get("keep_messages", [])
    return handled, through, keep, latest


def _history(home: Path) -> tuple[list[_Row], list[dict], list[dict]]:
    runs = _read(home / _RUNS, _valid_run)
    improvements = sorted(_records(_read(home / _IMPROVEMENTS, _valid_improvement)), key=lambda item: item["time"])
    return runs, _feedback_notes(home), improvements


@contextmanager
def _lock(home: Path, *, writing: bool):
    if writing:
        home.mkdir(parents=True, exist_ok=True)
    try:
        stream = (home / ".lock").open("a+b" if writing else "rb")
    except FileNotFoundError:
        if writing:
            raise
        yield False  # Reporting must not create or require write access to a lock.
        return
    except OSError as exc:
        if writing:
            raise
        print(f"ledger warning: reading without a lock: {exc!r}", file=sys.stderr)
        yield False
        return
    with stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX if writing else fcntl.LOCK_SH)
        except OSError as exc:
            if writing:
                raise
            print(f"ledger warning: reading without a lock: {exc!r}", file=sys.stderr)
            yield False
            return
        yield True


_NOT_PLAIN = "(not a plain name)"


def _shown(name, agent: bool = False) -> str:
    """A task, node or agent name as the text output shows it: only a plain name (handedits.OUTPUT_NAME, AGENT_NAME);
    anything else a graph file says stays out of what an agent reads from `record` and `signals`."""
    from .handedits import AGENT_NAME, OUTPUT_NAME
    return name if isinstance(name, str) and (AGENT_NAME if agent else OUTPUT_NAME).fullmatch(name) else _NOT_PLAIN


def _now(now: float | None) -> float:
    return time.time() if now is None else now


def _line(item: dict) -> bytes:
    return (json.dumps(item, allow_nan=False) + "\n").encode("utf-8")


def _replace(path: Path, rows: list[_Row]) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.writelines(raw for raw, _ in rows)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _append(path: Path, now: float | None, fields: dict) -> dict:
    with _lock(path.parent, writing=True):
        return _append_locked(path, now, fields)


def _append_locked(path: Path, now: float | None, fields: dict) -> dict:
    with path.open("a+b") as stream:
        # A previous incomplete append must not swallow the new record.
        separator = b""
        if stream.seek(0, os.SEEK_END):
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":
                separator = b"\n"
        item = {"time": _now(now), **fields}
        stream.write(separator + _line(item))
        stream.flush()
        os.fsync(stream.fileno())
    return item


def _project(path: Path) -> Path:
    return next((parent.parent for parent in path.parents if parent.name == ".opencode"), path.parent)


def _session_ids(graph: TaskGraph) -> set[str]:
    return {node.session for node in graph.nodes if isinstance(node.session, str) and node.session}


def _session_costs(db_path: Path, graphs: list[TaskGraph]) -> dict[str, float]:
    ids = {sid for graph in graphs for sid in _session_ids(graph)}
    if not ids:
        return {}
    first = graphs[0]
    root, children, _ = sources.load_sessions(
        db_path, first.session, str(_project(Path(os.path.abspath(first.path))))
    )
    sessions = ([root] if root else []) + children
    if ids.difference(session.id for session in sessions):
        # Selection warnings do not imply a failed database read. The
        # read-only reader can find references even outside the selected tree.
        try:
            sessions = sources._read_sessions(db_path)
        except Exception:  # Match sources.load_sessions on an unreadable database.
            pass
    costs = {s.id: s.cost for s in sessions if s.id in ids and _number(s.cost)}
    if not costs:
        return costs
    # The session view normalizes NULL costs to zero. Distinguish missing
    # prices from real zero-cost sessions using the same read-only connection.
    try:
        with closing(sources._connect(db_path)) as conn:
            for (sid,) in conn.execute("SELECT id FROM session_v2 WHERE typeof(cost) NOT IN ('integer', 'real')"):
                costs.pop(str(sid), None)
    except (OSError, sqlite3.Error, ValueError):
        return {}
    return costs


def _summary(graph: TaskGraph, now: float, costs: dict[str, float], outcome: str, verifier: dict | None = None) -> dict:
    path = Path(os.path.abspath(graph.path))
    delivered, total = objective_coverage(graph)
    session_ids = _session_ids(graph)
    cost_ids = session_ids & costs.keys()
    cost = sum(costs[sid] for sid in sorted(cost_ids)) if cost_ids else None
    return {
        **({"verifier": verifier} if verifier else {}),
        "path": str(path), "project": str(_project(path)), "task": graph.task,
        "goal": graph.goal, "status": graph.status, "outcome": outcome, "revision": graph.revision,
        "created": graph.created, "updated": graph.updated, "recorded": now,
        "duration": graph.updated - graph.created,
        "nodes_total": len(graph.nodes),
        "nodes_passed": sum(node.status == "passed" for node in graph.nodes),
        "objectives_delivered": delivered, "objectives_total": total,
        "amendments": len(graph.amendments), "lessons": graph.lessons,
        "discovered": [event.note for event in graph.events if event.event == "discovered"],
        "events": dict(Counter(event.event for event in graph.events)),
        "worker_cost": cost if _number(cost) else None,
        "worker_cost_sessions": {"referenced": len(session_ids), "known": len(cost_ids)},
        "nodes": [
            {"id": n.id, "type": n.type, "agent": n.agent, "status": n.status,
             "attempts": n.attempts, "tiers": n.tiers, "escalated": len(_tiers(n.tiers)) > 1,
             "stalled": n.stalled, "failure_kind": n.failure_kind, "verdict": n.verdict}
            for n in graph.nodes
        ],
    }


def _run_key(run: dict) -> tuple[str, float]:
    return run["path"], run.get("created", 0)


def _evidence(run: dict) -> dict:
    return {key: value for key, value in run.items() if key != "recorded"}


def _upsert(rows: list[_Row], recorded: list[dict], notes: list[str]) -> tuple[list[_Row], list[dict], bool]:
    replacements = {_run_key(run): run for run in recorded}
    merged = []
    saved = []
    refused = False
    for raw, old in rows:
        run = replacements.pop(_run_key(old), None) if old is not None else None
        if run is None:
            merged.append((raw, old))
            continue
        if _outcome(old) == "passed" and _outcome(run) == "abandoned":
            notes.append(f"not recorded {_shown(run['task'])}: cannot abandon a run already recorded as passed")
            merged.append((raw, old))
            refused = True
            continue
        if old["updated"] > run["updated"]:
            notes.append(f"not recorded {_shown(run['task'])}: kept newer stored run (updated {old['updated']})")
            merged.append((raw, old))
            continue
        same_update = run["updated"] == old["updated"]
        if run["worker_cost"] is None and _worker_cost(old) is not None:
            run["worker_cost"] = _worker_cost(old)
            # Coverage from an older graph may refer to a different set of workers.
            coverage = old.get("worker_cost_sessions")
            if same_update and coverage and coverage["referenced"] == run["worker_cost_sessions"]["referenced"]:
                run["worker_cost_sessions"] = coverage
        if _evidence(run) == _evidence(old):
            notes.append(f"not recorded {_shown(run['task'])}: unchanged")
            merged.append((raw, old))
            continue
        merged.append((_line(run), run))
        saved.append(run)
    # An unterminated last line must keep its exact bytes, not absorb a new row.
    insert_at = len(merged) - bool(merged and not merged[-1][0].endswith(b"\n"))
    merged[insert_at:insert_at] = [(_line(run), run) for run in replacements.values()]
    return merged, saved + list(replacements.values()), refused


@dataclass
class _RecordResult:
    runs: list[dict]
    warnings: list[str]
    notes: list[str]
    code: int
    due: dict
    cases_added: int = 0


def record(
    home: Path, now: float | None, graph_dir: Path, *, task: str | None = None,
    db_path: Path = sources.DB_PATH, abandoned: bool = False,
) -> _RecordResult:
    """Upsert checked passed graphs or one explicitly abandoned graph."""
    if abandoned and not task:
        return _RecordResult([], [], ["--abandoned requires --task NAME"], 2, signals(home, now))
    graph_dir = graph_dir.resolve()
    if not graph_dir.is_dir():
        return _RecordResult([], [], [f"graph directory is not a directory: {graph_dir}"], 2, _due_fields([], []))
    paths, warnings = sources.graph_files(graph_dir)
    if not paths:
        notes = [f"no graph matches task {task!r}"] if task is not None else []
        return _RecordResult([], warnings, notes, 2 if notes or warnings else 0, signals(home, now))
    with _lock(home, writing=True):
        matches = []
        for path in paths:
            graph, problems = check.read_graph(path)
            if graph is None:
                warnings.append(f"graph: skipped {path.name}: {problems[0]}")
            elif task is None or graph.task == task:
                matches.append((path, graph, problems))
        notes, selected = [], []
        code = 0
        if task is not None and not matches:
            notes.append(f"no graph matches task {task!r}")
            code = 2
        if abandoned and len(matches) > 1:
            notes.append(f"multiple graphs match task {task!r}; refusing to abandon an ambiguous task")
            matches = []
            code = 2
        for path, graph, problems in matches:
            valid_created = _number(graph.created) and graph.created > 0
            if abandoned:
                if graph.status == "passed":
                    notes.append(f"not recorded {_shown(graph.task)}: cannot abandon a passed graph")
                    code = 2
                    continue
                if not valid_created:
                    try:
                        graph = replace(graph, created=path.stat().st_mtime)
                    except OSError as exc:
                        warnings.append(f"graph: skipped {path.name}: {exc}")
                        continue
            elif graph.status != "passed":
                notes.append(f"skipped {_shown(graph.task)}: unfinished ({graph.status})")
                continue
            else:
                if not valid_created:
                    identity_problem = "graph created must be a finite number greater than 0"
                    if identity_problem not in problems:
                        problems = [identity_problem, *problems]
                if problems:
                    notes.append(f"not recorded {_shown(graph.task)}: {len(problems)} problems (run agentgraph check)")
                    notes.extend(f"  - {problem}" for problem in problems[:3])
                    code = max(code, 1)
                    continue
            if not all(map(_number, (graph.created, graph.updated, graph.updated - graph.created))):
                warnings.append(f"graph: skipped {path.name}: non-finite timestamps")
                continue
            selected.append(graph)

        rows, responses, improvements = _history(home)
        handled, _, _, _ = _handled_state(home)
        costs = _session_costs(db_path, selected)
        when = _now(now)
        outcome = "abandoned" if abandoned else "passed"
        rows, saved, refused = _upsert(
            rows, [_summary(graph, when, costs, outcome, _verifier_summary(home, graph, responses)) for graph in selected], notes)
        runs, responses = _pending(_records(rows), responses, _watermark(improvements), handled)
        due = _due_fields(runs, responses)
        if saved:
            _replace(home / _RUNS, rows)
        added = _capture_cases(home, selected, when, notes)
    return _RecordResult(saved, warnings, notes, 2 if warnings or refused else code, due, added)


def _verifier_summary(home: Path, graph: TaskGraph, notes: list[dict]) -> dict | None:
    """The run's verifier passes and which were overturned (`overturned.summary`); None without a pass."""
    from . import handedits, overturned
    return overturned.summary(graph, home, notes, handedits.default_paths(home)[0])


def _capture_cases(home: Path, graphs: list[TaskGraph], when: float, notes: list[str]) -> int:
    """Keep the recorded graphs' failed nodes as cases (`cases.capture_locked`), under the lock `record` holds."""
    from . import cases
    try:
        return cases.capture_locked(home, graphs, when)
    except OSError as exc:
        notes.append(f"cases not saved: {exc}")
        return 0


def feedback(
    home: Path, now: float | None, text: str, *, agent: str | None = None, task: str | None = None
) -> dict | None:
    """Append feedback, or return None without writing if it is blank."""
    if not text.strip():
        return None
    return _append(home / _FEEDBACK, now, {"text": text, "agent": agent, "task": task})


def improved(
    home: Path, now: float | None, file: str | Path | list[str], summary: str, *,
    signals: list[str] | None = None, metric: str | None = None, seen_until: float | None = None,
    keep_watermark: bool = False, size_change: dict[str, int] | None = None,
) -> dict:
    """Record an applied improvement without opening or modifying its file.

    `size_change` is the proposal's stated change to each agent's fixed text, in characters. With a bloat
    baseline in the home, the record also keeps the fixed text measured now (`fixed_chars`) and the actual change
    since the previous measured record, or since the baseline (`size_actual`)."""
    fields = {"file": str(file) if isinstance(file, Path) else file,
              "summary": summary, "signals": signals or [], "metric": metric}
    if seen_until is not None:
        fields["seen_until"] = seen_until
    if keep_watermark:
        fields["keep_watermark"] = True
    if size_change is not None:
        if not _sizes(size_change):
            raise ValueError("size_change maps agent names to whole numbers of characters")
        fields["size_change"] = size_change
    fields.update(_measured_size(home))
    from . import models
    fields.update(models.stored_field(home))
    return _append(home / _IMPROVEMENTS, now, fields)


def _measured_size(home: Path) -> dict:
    """{"fixed_chars", "size_actual"} when the home has a bloat baseline (nothing otherwise, or on any error)."""
    from . import bloat
    try:
        found = bloat.current(home)
        if found is None:
            return {}
        baseline, rows = found
        now = {agent: row["chars"] for agent, row in rows.items()}
        before = baseline["agents"]
        for item in reversed(_records(_read(home / _IMPROVEMENTS, _valid_improvement))):
            if "fixed_chars" in item:
                before = item["fixed_chars"]
                break
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    actual = {agent: now.get(agent, 0) - before.get(agent, 0) for agent in sorted({*now, *before})}
    return {"fixed_chars": now, "size_actual": {agent: delta for agent, delta in actual.items() if delta}}


def _bloat(home: Path) -> dict:
    from . import bloat
    return bloat.signal(home)


def _print_bloat(report: dict) -> None:
    from . import bloat
    line = bloat.signal_line(report)
    if line:
        print(line)


def _hand_edits(home: Path, now: float | None, improvements: list[dict] | None = None, options: dict | None = None) -> dict:
    """The hand-edit signal (`handedits.signal`); `options` may carry days, min_edits, min_tasks, min_rewrites,
    backups, agents_dir."""
    from . import handedits
    if improvements is None:
        improvements = _records(_read(home / _IMPROVEMENTS, _valid_improvement))
    return handedits.signal(home, now, improvements=improvements, **{k: v for k, v in (options or {}).items()
                                                                     if v is not None})


def _print_hand_edits(report: dict) -> None:
    from . import handedits
    for line in handedits.signal_lines(report or {}):
        print(line)


def _cases(home: Path) -> dict:
    from . import cases
    return cases.signal(home, lock=False)


def _overturned(home: Path, now: float | None, runs: list[dict], notes: list[dict], improvements: list[dict],
                options: dict | None = None) -> dict:
    """The verifier signal (`overturned.signal`), over the hand-edit window (`--hand-days`, `--backups`)."""
    from . import overturned
    options = options or {}
    extra = {key: options[key] for key in ("days", "backups") if options.get(key) is not None}
    try:
        return overturned.signal(home, runs, notes, improvements, _now(now), **extra)
    except (OSError, ValueError) as exc:
        return {"due": False, "error": str(exc)}


def _models(home: Path, improvements: list[dict]) -> dict:
    from . import models
    return models.signal(home, improvements)


def _print_models(report: dict) -> None:
    from . import models
    for line in models.signal_lines(report or {}):
        print(line)


def _print_overturned(report: dict) -> None:
    from . import overturned
    for line in overturned.signal_lines(report or {}):
        print(line)


def _print_cases(report: dict) -> None:
    from . import cases
    for line in cases.signal_lines(report or {}):
        print(line)


def _open_message_db(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.expanduser().resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)


def _valid_message_id(value) -> bool:
    return isinstance(value, str) and bool(value)


def _user_message(row) -> tuple[str, int, str, str] | None:
    message_id, created, title, directory, raw = row
    if not _valid_message_id(message_id):
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        return None
    display = data.get("metadata")
    display = display.get("displayText") if isinstance(display, dict) else None
    text = display if isinstance(display, str) else data["text"]
    # Display text, when present, is the pre-expansion input. Skip visible slash invocations.
    if not text.strip() or text.lstrip().startswith("/"):
        return None
    return "msg:" + message_id, created, (title or directory or "")[:120], text


def _message_report(db_path: Path, generated: float, since: int, keep: list[str],
                    notes: list[dict], handled: set[str]) -> dict:
    messages: list[dict] = []
    saved = {note["text"] for note in notes}
    through = int(generated * 1000)
    left_out = chars = invalid_ids = 0
    try:
        with closing(_open_message_db(db_path)) as db:
            rows = db.execute(_MESSAGE_ROWS + """
                AND m.time_created <= ?
                AND (m.time_created > ? OR m.id IN (SELECT value FROM json_each(?)))
                ORDER BY m.time_created DESC, m.id DESC
            """, (through, since, json.dumps([i[4:] for i in keep])))
            for row in rows:
                if not _valid_message_id(row[0]):
                    invalid_ids += 1
                    continue
                if "msg:" + row[0] in handled or not (message := _user_message(row)):
                    continue
                note_id, created, session, text = message
                if text in saved:
                    continue
                text = text[:_MESSAGE_LENGTH]
                if left_out or len(messages) >= _MESSAGE_COUNT or chars + len(text) > _MESSAGE_BUDGET:
                    left_out += 1
                    continue
                messages.append(dict(id=note_id, time=created, session=session, text=text))
                chars += len(text)
    except (OSError, sqlite3.Error):
        print("ledger warning: cannot read user messages (database unavailable or unrecognized schema)", file=sys.stderr)
        return {"messages": [], "messages_left_out": 0, "messages_through": None}
    if invalid_ids:
        print(f"ledger warning: skipped {invalid_ids} user message rows with invalid IDs", file=sys.stderr)
    return {"messages": messages, "messages_left_out": left_out, "messages_through": through}


def _mark_feedback(home: Path, now: float | None, payload: dict, db_path: Path) -> None:
    checkpoint = {}
    handled = payload
    if isinstance(payload, dict) and "messages_through" in payload:
        handled = payload.get("handled")
        if (payload.keys() - {"handled", "messages_through", "keep_messages", "keep_backlog"}
                or not _valid_message_checkpoint(payload)
                or payload["messages_through"] > int(_now(now) * 1000)):
            raise ValueError("invalid messages checkpoint")
        checkpoint = {"messages_through": payload["messages_through"],
                      "keep_messages": payload.get("keep_messages", [])}
        if payload.get("keep_backlog"):
            checkpoint["keep_backlog"] = True  # Record the read time without consuming the capped backlog.
    if not (_valid_handled(handled) or (checkpoint and handled == {})):
        raise ValueError("expected a JSON object mapping feedback IDs to reasons, or a message checkpoint")
    # Feedback is append-only. Validate without creating a home or lock on failure.
    with _lock(home, writing=False):
        known = {note["id"] for note in _feedback_notes(home)}
        already, _, _, _ = _handled_state(home)
    requested = handled.keys() | set(checkpoint.get("keep_messages", []))
    message_ids = {i[4:] for i in requested - already if i.startswith("msg:")}
    if message_ids:
        with closing(_open_message_db(db_path)) as db:
            for row in db.execute(_MESSAGE_ROWS + " AND m.id IN (SELECT value FROM json_each(?))",
                                  (json.dumps(sorted(message_ids)),)):
                if message := _user_message(row):
                    known.add(message[0])
    unknown = requested - known - already
    if unknown:
        raise ValueError(f"unknown feedback id: {min(unknown)!r}")
    with _lock(home, writing=True):
        already, _, _, latest = _handled_state(home)
        new = {note_id: reason for note_id, reason in handled.items() if note_id not in already}
        through = checkpoint.get("messages_through")
        if through is None or through < latest.get("messages_through", -1) or checkpoint == latest:
            checkpoint = {}
        if new or checkpoint:
            _append_locked(home / _FEEDBACK_HANDLED, now, {"handled": new, **checkpoint})


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: Iterable[int | float]) -> int | float | None:
    try:
        return statistics.mean(values)
    except (statistics.StatisticsError, OverflowError):
        return None


def _performance(nodes: list[dict]) -> dict:
    passed = [node for node in nodes if node["status"] == "passed"]
    first_pass = sum(node["attempts"] <= 1 for node in passed)
    return {
        "nodes": len(nodes), "passed": len(passed), "first_pass": first_pass,
        "mean_attempts": _mean(node["attempts"] for node in nodes),
        "stalled": sum(node["stalled"] for node in nodes),
    }


def _agent_stats(nodes: list[dict]) -> dict:
    assigned = defaultdict(list)
    escalated_away, escalated_to = Counter(), Counter()
    for node in nodes:
        if node["attempts"] <= 0:
            continue
        tiers = _tiers(node["tiers"])
        if len(tiers) > 1:
            escalated_away.update(tiers[:-1])
            escalated_to.update(tiers[1:])
        else:
            assigned[tiers[0] if tiers else node["agent"]].append(node)
    agents = {}
    for agent in sorted(assigned.keys() | escalated_away.keys() | escalated_to.keys()):
        stats = _performance(assigned[agent])
        agents[agent] = {
            **stats,
            "escalated_away": escalated_away[agent],
            "escalated_to": escalated_to[agent],
            "first_pass_rate": _ratio(stats["first_pass"], stats["nodes"] + escalated_away[agent]),
            "failure_kinds": dict(Counter(n["failure_kind"] for n in assigned[agent] if n["failure_kind"])),
        }
    return agents


def _escalations(runs: list[dict]) -> list[dict]:
    return [
        {"task": run["task"], "node": n["id"], "tiers": tiers, "attempts": n["attempts"],
         "stalled": n["stalled"], "failure_kind": n["failure_kind"]}
        for run in runs for n in run["nodes"] if n["attempts"] > 0 and len(tiers := _tiers(n["tiers"])) > 1
    ]


def _metrics(runs: list[dict]) -> dict:
    all_nodes = [n for run in runs for n in run["nodes"]]
    nodes = [n for n in all_nodes if n["type"] in ("implement", "design") and n["attempts"] > 0]
    gates = [n["verdict"] for n in all_nodes if n["type"] == "gate" and n["verdict"]]
    escalated = [len(_tiers(n["tiers"])) > 1 for n in nodes]
    first_pass = sum(n["status"] == "passed" and n["attempts"] <= 1 and not esc
                     for n, esc in zip(nodes, escalated))
    outcomes = Counter(map(_outcome, runs))
    costs = []
    for run in runs:
        counts = run.get("worker_cost_sessions", {"known": 0, "referenced": 0})
        cost = _worker_cost(run)
        if cost is not None and counts["known"] == counts["referenced"] > 0:
            costs.append(cost)
    return {
        "runs": len(runs), "first_pass_rate": _ratio(first_pass, len(nodes)),
        "abandoned_rate": _ratio(outcomes["abandoned"], outcomes["abandoned"] + outcomes["passed"]),
        "mean_attempts": _mean(n["attempts"] for n in nodes),
        "escalation_rate": _ratio(sum(escalated), len(nodes)),
        "stall_rate": _ratio(sum(n["stalled"] for n in nodes), len(nodes)),
        "unverifiable_rate": _ratio(gates.count("unverifiable"), len(gates)),
        "mean_worker_cost": _mean(costs),
        "cost_runs": len(costs),
    }


def _watermark(improvements: list[dict]) -> float | None:
    for item in reversed(improvements):
        if item.get("keep_watermark") is not True:
            return item.get("seen_until", item["time"])
    return None


def _pending(runs: list[dict], responses: list[dict], watermark: float | None,
             handled: set[str]) -> tuple[list[dict], list[dict]]:
    return (runs if watermark is None else [run for run in runs if run["recorded"] > watermark],
            [item for item in responses
             if (watermark is None or item["time"] > watermark) and item["id"] not in handled])


def _due_fields(runs: list[dict], responses: list[dict]) -> dict:
    return {"pending": {"runs": len(runs), "feedback": len(responses)},
            "due": len(runs) >= DUE_RUNS or len(responses) >= DUE_FEEDBACK}


def signals(home: Path, now: float | None, *, all_items: bool = False, cost_db: Path | None = None,
            settings_log: Path | None = None, feedback_ids: bool = False, message_db: Path | None = None,
            hand_edits: dict | None = None) -> dict:
    """Read the ledger and aggregate pending or historical signals. `hand_edits` overrides the hand-edit window,
    thresholds and folders (see `_hand_edits`)."""
    generated = _now(now)
    with _lock(home, writing=False) as locked:
        rows, responses, improvements = _history(home)
        handled, messages_since, keep, _ = _handled_state(home)
        case_report = _cases(home)  # read under the same lock
        if locked:
            generated = _now(now)
    runs = _records(rows)
    latest = improvements[-1] if improvements else None
    watermark = _watermark(improvements)
    pending_runs, pending_feedback = _pending(runs, responses, watermark, handled)
    trend = None
    if latest is not None:
        cutoff = latest["time"]
        trend = {"before": _metrics([run for run in runs if run.get("created", 0) < cutoff]),
                 "after": _metrics([run for run in runs if run.get("created", 0) >= cutoff])}
    scope = runs if all_items else pending_runs
    nodes = [node for run in scope for node in run["nodes"]]
    verdicts = Counter(node["verdict"] for node in nodes if node["type"] == "gate")
    outcomes = Counter(map(_outcome, scope))
    notes = responses if all_items else pending_feedback
    report = {
        "generated": generated, "last_improvement": latest, "watermark": watermark,
        **_due_fields(pending_runs, pending_feedback),
        "feedback": notes if feedback_ids else [{k: v for k, v in note.items() if k != "id"} for note in notes],
        "lessons": [{"task": run["task"], "text": text} for run in scope for text in run["lessons"]],
        "discovered": [{"task": run["task"], "note": note} for run in scope for note in run["discovered"]],
        "agents": _agent_stats(nodes), "gates": {verdict: verdicts[verdict] for verdict in VERDICTS},
        "outcomes": {"passed": outcomes["passed"], "abandoned": outcomes["abandoned"], "unknown": outcomes[None]},
        "escalations": _escalations(scope),
        "amendments": sum(run["amendments"] for run in scope), "trend": trend, "improvements": improvements,
        "bloat": _bloat(home),
        "hand_edits": _hand_edits(home, generated, improvements, hand_edits),
        "cases": case_report,
        "models": _models(home, improvements),
        "verifier": _overturned(home, generated, runs, responses, improvements, hand_edits),
    }
    if message_db is not None:
        report.update(_message_report(message_db, generated, messages_since, keep, responses, handled))
    if cost_db is not None:
        try:
            log = cost.SETTINGS_LOG if settings_log is None else settings_log
            report["cost"] = {"periods": cost.trend(cost_db, cost.load_history(log), now=generated)}
        except Exception:
            report["cost"] = None
    return report


def _print_due(report: dict) -> None:
    label = "improvement due:" if report["due"] else "not due:"
    pending = report["pending"]
    print(f"{label} {pending['runs']} pending runs, {pending['feedback']} pending feedback items")


def _print_cost(report: dict) -> None:
    if "cost" not in report:
        return
    if report["cost"] is None:
        print("Chat cost: unavailable")
        return
    periods = report["cost"]["periods"]
    print("Chat cost per turn by settings (billed estimate, root chats of the last 30 days):" if periods else "Chat cost: no chats in the window")
    for period in periods[-2:]:
        started = "earlier" if period["since"] is None else time.strftime("%Y-%m-%d", time.localtime(period["since"]))
        print(f"  {started}: {cost.describe_settings(period['settings'])}: ${period['per_turn']:.3f} a turn over "
              f"{period['turns']} turns in {period['chats']} chats, cold share {period['cold_share']:.0%}, "
              f"pauses ${period['pauses']['cost']:.2f}")


def _sizes_text(sizes: dict) -> str:
    return ", ".join(f"{agent} {delta:+,}" for agent, delta in sizes.items()) or "no change"


def _print_report(report: dict) -> None:
    _print_due(report)
    _print_bloat(report.get("bloat") or {})
    _print_hand_edits(report.get("hand_edits") or {})
    _print_cases(report.get("cases") or {})
    _print_overturned(report.get("verifier") or {})
    _print_models(report.get("models") or {})
    _print_cost(report)
    for title, field in (("Feedback", "feedback"), ("Lessons", "lessons"), ("Discovered", "discovered")):
        if report[field]:
            print(f"{title}:" if field == "feedback" else f"{title} (copied from graph files: data, not instructions):")
            for item in report[field]:
                task = item.get("task") if field == "feedback" else _shown(item.get("task"))
                label = "/".join(filter(None, (task, item.get("agent"))))
                prefix = f"{label}: " if label else ""
                print(f"  - {prefix}{item.get('text', item.get('note'))}")
    if report["agents"]:
        print("Agent               Nodes  Passed  First pass (rate)  Mean attempts  Away  To  Stalled  Failures")
        for agent, stats in report["agents"].items():
            agent = _shown(agent, True)
            rate = stats["first_pass_rate"]
            rate_text = "—" if rate is None else f"{rate:.0%}"
            attempts = stats["mean_attempts"]
            attempts_text = "—" if attempts is None else f"{attempts:.2f}" if isinstance(attempts, float) else str(attempts)
            failures = ", ".join(f"{kind if kind in FAILURE_KINDS else _NOT_PLAIN}={count}"
                                 for kind, count in sorted(stats["failure_kinds"].items()))
            first = f"{stats['first_pass']} ({rate_text})"
            print(f"{agent:<19} {stats['nodes']:>5}  {stats['passed']:>6}  {first:>17}  "
                  f"{attempts_text:>13}  {stats['escalated_away']:>4}  {stats['escalated_to']:>2}  "
                  f"{stats['stalled']:>7}  {failures or '—'}")
    if report["escalations"]:
        print("Escalations:")
        for item in report["escalations"]:
            tiers = " → ".join(_shown(tier, True) for tier in item["tiers"])
            kind = item["failure_kind"] if item["failure_kind"] in FAILURE_KINDS else _NOT_PLAIN
            detail = ", ".join(filter(None, (kind if item["failure_kind"] else "", "stalled" if item["stalled"] else "")))
            suffix = f", {detail}" if detail else ""
            print(f"  - {_shown(item['task'])}/{_shown(item['node'])}: {tiers} ({item['attempts']} attempts{suffix})")
    print("Gates: " + ", ".join(f"{key}={value}" for key, value in report["gates"].items()))
    print("Outcomes: " + ", ".join(f"{key}={value}" for key, value in report["outcomes"].items()))
    print(f"Amendments: {report['amendments']}")
    if report["trend"] is None:
        print("Trend: no improvement recorded")
    else:
        print(f"Seen through: {report['watermark']}")
        print("Trend: (cost covers known worker sessions only; partial runs excluded)")
        for side, metrics in report["trend"].items():
            values = ", ".join(f"{key}={value if value is not None else '—'}" for key, value in metrics.items())
            print(f"  {side}: {values}")
        print("Improvements (oldest first):")
        for item in report["improvements"]:
            print(f"  - {item['time']} {item['file']}: {item['summary']}")
            if item.get("signals"):
                print("    Signals: " + "; ".join(item["signals"]))
            if item.get("metric") is not None:
                print(f"    Metric: {item['metric']}")
            if "size_change" in item or "size_actual" in item:
                sizes = [f"{label} {_sizes_text(item[key])}" for label, key in
                         (("proposed", "size_change"), ("actual", "size_actual")) if key in item]
                print("    Size (characters of fixed text): " + "; ".join(sizes))


def _feedback_text(args: argparse.Namespace) -> str:
    if sum((args.stdin, args.text is not None, args.text_file is not None)) != 1:
        raise ValueError("provide exactly one of TEXT, --stdin, or --text-file")
    if args.text is not None:
        return args.text
    if args.text_file is not None:
        text = args.text_file.read_text(encoding="utf-8", newline="")
    elif hasattr(sys.stdin, "buffer"):
        text = sys.stdin.buffer.read().decode(sys.stdin.encoding or "utf-8")
    else:
        text = sys.stdin.read()
    return text[:-2] if text.endswith("\r\n") else text.removesuffix("\n")


def _size_change(values: list[str] | None) -> dict[str, int] | None:
    """AGENT=+N or AGENT=-N options as {agent: N}."""
    if not values:
        return None
    sizes = {}
    for value in values:
        agent, sep, delta = value.partition("=")
        try:
            sizes[agent] = int(delta)
        except ValueError:
            sep = ""
        if not sep or not agent:
            raise ValueError(f"--size-change takes AGENT=+N or AGENT=-N characters, not {value!r}")
    return sizes


def _improvement_fields(args: argparse.Namespace) -> dict:
    fields = {"file": args.file, "summary": args.summary, "signals": args.signal,
              "metric": args.metric, "seen_until": args.seen_until, "size_change": _size_change(args.size_change)}
    supplied = {key: value for key, value in fields.items() if value is not None}
    if args.json_file is not None:
        if supplied:
            raise ValueError("--json-file cannot be combined with improvement metadata options")
        supplied = json.loads(args.json_file.read_text(encoding="utf-8"),
                              parse_float=_finite_float, parse_constant=_finite_float)
        if not isinstance(supplied, dict) or supplied.keys() - fields.keys():
            raise ValueError("expected an improvement metadata object")
    if not _valid_improvement_fields(supplied):
        raise ValueError("improvement requires file and summary, with well-formed optional metadata")
    return supplied


def _hand_options(args: argparse.Namespace) -> dict:
    return {"days": args.hand_days, "min_edits": args.hand_edits, "min_tasks": args.hand_tasks,
            "min_rewrites": args.hand_rewrites, "backups": args.backups, "agents_dir": args.agents_dir}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph ledger", description=__doc__)
    parser.add_argument("--home", type=Path, help="ledger directory (default: $AGENTGRAPH_HOME or ~/.config/opencode/orchestration/memory)")
    commands = parser.add_subparsers(dest="command", required=True)
    record_parser = commands.add_parser("record", help="record task graphs")
    record_parser.add_argument("--dir", type=Path, help="graph directory (default: nearest .opencode/graph)")
    record_parser.add_argument("--task")
    record_parser.add_argument("--db", type=Path, default=sources.DB_PATH, help="OpenCode session database")
    record_parser.add_argument("--abandoned", action="store_true", help="record one abandoned task (requires --task)")
    feedback_parser = commands.add_parser("feedback", help="record user feedback")
    feedback_parser.add_argument("text", nargs="?")
    ending_help = "one trailing newline is removed; everything else is stored as given"
    feedback_parser.add_argument("--stdin", action="store_true", help=f"read feedback from standard input; {ending_help}")
    feedback_parser.add_argument("--text-file", type=Path, help=f"read feedback from a UTF-8 text file; {ending_help}")
    feedback_parser.add_argument("--agent")
    feedback_parser.add_argument("--task")
    improved_parser = commands.add_parser("improved", help="record an applied improvement or handle feedback")
    improved_parser.add_argument("--json-file", type=Path, help="read all improvement metadata from a JSON file")
    improved_parser.add_argument("--file")
    improved_parser.add_argument("--summary")
    improved_parser.add_argument("--signal", action="append")
    improved_parser.add_argument("--metric")
    improved_parser.add_argument("--seen-until", type=_finite_float, help="generated time of the signals report used")
    improved_parser.add_argument("--size-change", action="append", metavar="AGENT=DELTA",
                                 help="the proposal's change to an agent's fixed text, in characters (repeatable)")
    batch = improved_parser.add_mutually_exclusive_group()
    batch.add_argument("--keep-watermark", action="store_true", help="record a batch edit without consuming pending signals")
    batch.add_argument("--handled-file", type=Path, help="mark feedback using a JSON object mapping IDs to reasons")
    improved_parser.add_argument("--db", type=Path, help="OpenCode database for validating message IDs")
    signals_parser = commands.add_parser("signals", help="report improvement signals")
    signals_parser.add_argument("--json", action="store_true")
    signals_parser.add_argument("--all", action="store_true")
    signals_parser.add_argument("--feedback-ids", action="store_true", help="include note IDs in the JSON feedback items")
    signals_parser.add_argument("--messages", action="store_true", help="include recent root-chat user messages")
    signals_parser.add_argument("--db", type=Path, help="OpenCode database for reading messages")
    signals_parser.add_argument("--no-cost", action="store_true", help="leave out the chat cost trend")
    signals_parser.add_argument("--cost-db", type=Path, help="OpenCode session database for the chat cost trend")
    signals_parser.add_argument("--settings-log", type=Path, help="log of the cost settings over time")
    signals_parser.add_argument("--config", type=Path, help="OpenCode config file")
    for command_parser in (record_parser, signals_parser):
        command_parser.add_argument("--hand-days", type=float, help="window of the hand-edit signal (default 14)")
        command_parser.add_argument("--hand-edits", type=int, help="output edits that make it due (default 3)")
        command_parser.add_argument("--hand-tasks", type=int, help="tasks those edits must span (default 2)")
        command_parser.add_argument("--hand-rewrites", type=int, help="instruction rewrites that make it due (default 3)")
        command_parser.add_argument("--backups", type=Path, help="backups folder (default: beside the home's orchestration folder)")
        command_parser.add_argument("--agents-dir", type=Path, help="agent files folder (default: beside the backups folder)")
    for command_parser in (record_parser, feedback_parser, improved_parser, signals_parser):
        command_parser.add_argument("--home", type=Path, default=argparse.SUPPRESS, help="ledger directory")

    args = parser.parse_args(argv)
    home = (args.home or Path(os.environ.get("AGENTGRAPH_HOME", "~/.config/opencode/orchestration/memory"))).expanduser()
    try:
        if args.command == "record":
            if args.abandoned and not args.task:
                print("--abandoned requires --task NAME", file=sys.stderr)
                return 2
            graph_dir = args.dir if args.dir is not None else sources.find_graph_dir(Path.cwd())
            if graph_dir is None:
                print("no .opencode/graph directory found from here")
                if args.task is None:
                    return 0
                print(f"no graph matches task {args.task!r}")
                return 2
            result = record(home, None, graph_dir, task=args.task, db_path=args.db,
                            abandoned=args.abandoned)
            for warning in result.warnings:
                print(f"UNREADABLE  {warning}")
            for note in result.notes:
                print(note)
            for run in result.runs:
                print(f"recorded {_shown(run['task'])}: {run['nodes_passed']}/{run['nodes_total']} nodes passed ({run['path']})")
            _print_due(result.due)
            _print_bloat(_bloat(home))
            _print_hand_edits(_hand_edits(home, None, options=_hand_options(args)))
            with _lock(home, writing=False):
                rows, responses, improvements = _history(home)
            _print_overturned(_overturned(home, None, _records(rows), responses, improvements, _hand_options(args)))
            _print_models(_models(home, improvements))
            return result.code
        if args.command == "feedback":
            if feedback(home, None, _feedback_text(args), agent=args.agent, task=args.task) is None:
                print("feedback text must not be empty", file=sys.stderr)
                return 2
        elif args.command == "improved":
            if args.handled_file is not None:
                if any(value is not None for value in
                       (args.json_file, args.file, args.summary, args.signal, args.metric, args.seen_until,
                        args.size_change)):
                    raise ValueError("--handled-file cannot be combined with improvement metadata options")
                _mark_feedback(home, None, json.loads(args.handled_file.read_text(encoding="utf-8")),
                               args.db or sources.DB_PATH)
            else:
                fields = _improvement_fields(args)
                if args.keep_watermark and "seen_until" in fields:
                    raise ValueError("--keep-watermark cannot be combined with seen_until")
                improved(home, None, keep_watermark=args.keep_watermark, **fields)
        else:
            log = args.settings_log or cost.SETTINGS_LOG
            if not args.no_cost:
                try:
                    cost.note_current_settings(log, args.config or cost.CONFIG_PATH)
                except Exception:
                    pass
            report = signals(home, None, all_items=args.all, settings_log=log,
                             cost_db=None if args.no_cost else args.cost_db or sources.DB_PATH,
                             feedback_ids=args.feedback_ids, hand_edits=_hand_options(args),
                             message_db=(args.db or sources.DB_PATH) if args.messages else None)
            if args.json:
                print(json.dumps(report, allow_nan=False))
            else:
                _print_report(report)
                if args.messages:
                    print(json.dumps({k: v for k, v in report.items() if k.startswith("messages")}))
    except (OSError, sqlite3.Error, ValueError, RecursionError) as exc:
        print(f"ledger: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
