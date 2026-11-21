"""Change a task graph without rewriting the file: `agentgraph graph set|event`.

    graph set TASK NODE [--status S] [--evidence TEXT | --evidence-file F|-] [--session ID] [--attempts +1|N]
                        [--verdict V] [--stalled true|false] [--note TEXT] [--invalidate]
                        [--expect-updated T] [--dir D]
    graph event TASK [NODE] KIND [--note TEXT] [--expect-updated T] [--dir D]

One call is one write under the graph folder's lock (the one `agentgraph output write` uses): it reads the file
as it is, refuses when `--expect-updated` is not the file's `updated` (somebody wrote in between), changes the node,
appends the event the change calls for (running: `dispatched` from pending, else `retried`; returned: `returned`;
passed: `evaluated`, `joined` for a join node; failed: `attempt`; blocked: `blocked`), sets `started` and `finished`,
raises `updated`, and checks the result with `agentgraph check`'s rules against the graph's history
(agentgraph.transitions). A write that would add a problem is refused, with the reason, and the file is left as it
was. Writing the whole file by hand stays valid; this only makes the usual steps safe.

`graph event TASK completed` also marks the graph passed (the event is refused while the graph is not passed, so the
helper makes it one step); `graph event TASK NODE invalidated` reopens the node as `set --invalidate` does.
Exit 0 written, 1 refused, 2 task, node or arguments not usable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import outputs, transitions
from .check import _graph_and_issues, _raw_issues
from .contract import EVENT_NAMES, NODE_STATUSES, VERDICTS


class Refused(outputs.OutputError):
    """The write would break a rule, or another writer got in first; the file was left as it was."""


_STATUS_EVENT = {"returned": "returned", "failed": "attempt", "blocked": "blocked", "pending": "changed"}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _find(raw: dict, node_id: str) -> dict:
    found = [n for n in raw.get("nodes", []) if isinstance(n, dict) and n.get("id") == node_id] \
        if isinstance(raw.get("nodes"), list) else []
    if not found:
        raise outputs.OutputError(f"the graph has no node {node_id[:80]!r}")
    if len(found) > 1:
        raise outputs.OutputError(f"the graph has {len(found)} nodes with the id {node_id[:80]!r}")
    return found[0]


def _attempts(spec: str, current: int) -> int:
    match = re.fullmatch(r"([+-]?)(\d+)", spec.strip())
    if not match:
        raise outputs.OutputError(f"--attempts must be +N or a number, not {spec[:40]!r}")
    return current + int(match.group(2)) if match.group(1) == "+" else (
        current - int(match.group(2)) if match.group(1) == "-" else int(match.group(2)))


def _append(raw: dict, kind: str, node_id: str, note: str, now: float) -> None:
    events = raw.setdefault("events", [])
    last = events[-1].get("time") if events and isinstance(events[-1], dict) else None
    at = max(now, last) if _number(last) else now
    events.append({"time": at, "node": node_id, "event": kind, "note": note})


def apply(raw: dict, node_id: str | None, change: dict, now: float) -> str:
    """Change `raw` (a decoded graph) in place as `change` says; returns the one line that shows the result.
    Keys of `change`: status, evidence, session, attempts (a spec), verdict, stalled, note, invalidate, kind (an
    event to append for the graph or the node)."""
    note = change.get("note") or ""
    kind = change.get("kind")
    node = _find(raw, node_id) if node_id else None
    if node is None:
        if kind == "completed":
            raw["status"] = "passed"
        _append(raw, kind, "", note, now)
        raw["updated"] = _updated(raw, now)
        return f"graph: {kind} (status {raw.get('status')}, updated {raw['updated']})"
    old_status = node.get("status")
    old_attempts = node.get("attempts") if type(node.get("attempts")) is int else 0
    status = change.get("status")
    if status is not None and status not in NODE_STATUSES:
        raise outputs.OutputError(f"status must be one of {', '.join(NODE_STATUSES)}")
    invalidate = bool(change.get("invalidate")) or kind == "invalidated"
    appended: list[tuple[str, str]] = []
    if invalidate:
        appended.append(("invalidated", note or "reopened"))
        if status is None and old_status == "passed":
            status = "pending"
    if "evidence" in change and change["evidence"] is not None:
        node["evidence"] = change["evidence"]
    if change.get("session") is not None:
        node["session"] = change["session"] or None
    if change.get("attempts") is not None:
        node["attempts"] = _attempts(change["attempts"], old_attempts)
    if change.get("verdict") is not None:
        if change["verdict"] not in ("", *VERDICTS):
            raise outputs.OutputError(f"verdict must be one of {', '.join(VERDICTS)} (or empty)")
        node["verdict"] = change["verdict"]
    newly_stalled = change.get("stalled") is True and node.get("stalled") is not True
    if change.get("stalled") is not None:
        node["stalled"] = change["stalled"]
    summary = note or (node.get("evidence") or "")[:200]
    if status is not None and status != old_status:
        node["status"] = status
        if status == "pending" and invalidate:
            node["finished"] = None  # the invalidated event says it
        elif status == "running":
            appended.append(("dispatched", note or str(node.get("agent")))
                            if old_status == "pending" else ("retried", summary))
            if not _number(node.get("started")):
                node["started"] = now
            node["finished"] = None
        elif status == "passed":
            appended.append(("joined" if node.get("type") == "join" else "evaluated", summary))
            node["finished"] = max(now, node["started"]) if _number(node.get("started")) else now
        else:
            appended.append((_STATUS_EVENT[status], summary))
            if old_status == "passed":
                node["finished"] = None
    elif node.get("attempts", 0) != old_attempts and not appended:
        appended.append(("attempt", summary))
    if newly_stalled:
        appended.append(("stalled", note or "attempts repeat without new evidence"))
    if kind and kind not in ("invalidated",) and not any(k == kind for k, _ in appended):
        appended.append((kind, note))
    if not appended and note:
        appended.append(("changed", note))
    for event, text in appended:
        _append(raw, event, node_id, text, now)
    raw["updated"] = _updated(raw, now)
    return (f"{node_id}: {node.get('status')} attempts {node.get('attempts', 0)} session {node.get('session') or '-'} "
            f"updated {raw['updated']}")


def _updated(raw: dict, now: float):
    old = raw.get("updated")
    if not _number(old):
        return now
    return max(now, round(old + 0.001, 3))


def _problems(raw: dict, path: Path, previous: dict | None) -> list[str]:
    found = _graph_and_issues(raw, path)[1]
    if previous is not None and not _raw_issues(raw):
        found = found + transitions.problems(previous, raw)
    return found


def edit(task: str, node_id: str | None, change: dict, *, dirs, expect_updated=None, now: float | None = None) -> str:
    """Make the change under the lock; the line that shows it. OutputError when the graph, task or node is not
    usable; Refused when a rule or `expect_updated` says no. Nothing is written then."""
    path = outputs.find_graph(task, dirs)
    stem = path.stem
    now = round(time.time() if now is None else now, 3)
    result: dict = {}
    with outputs._GraphFolder(path.parent) as folder:
        def make(current: bytes) -> bytes:
            raw = outputs._parse(current, path.name)
            if expect_updated is not None and not (_number(raw.get("updated")) and abs(raw["updated"] - expect_updated) < 5e-4):
                raise Refused(f"refused: {path.name} changed since you read it (updated is now {raw.get('updated')}, "
                              f"you expected {expect_updated}); read it again. Nothing was written.")
            try:
                previous = transitions.load_state(folder, stem)
            except transitions.HistoryError:
                previous = None
            before_state = previous if previous is not None else transitions.state_of(raw)
            before = _problems(raw, path, previous)
            new = json.loads(json.dumps(raw))
            result["line"] = apply(new, node_id, change, now)
            after = _problems(new, path, before_state)
            added = [problem for problem in after if problem not in before]
            if added:
                raise Refused("refused: this write would add " + ("a problem" if len(added) == 1 else f"{len(added)} problems")
                              + f" to {path.name}; nothing was written:\n" + "\n".join(f"  - {p}" for p in added))
            result["clean"] = not after
            result["state"] = transitions.state_of(new)
            return outputs._serialize(new)
        outputs._replace_graph(folder, path.name, make)
        if result.get("clean"):
            try:
                transitions.save_state(folder, stem, result["state"])
            except OSError as exc:
                print(f"warning: history of {stem} not saved: {exc.strerror or exc}", file=sys.stderr)
    return result["line"]


def _bool(text: str) -> bool:
    if text.lower() in ("true", "yes", "1"):
        return True
    if text.lower() in ("false", "no", "0"):
        return False
    raise argparse.ArgumentTypeError("true or false")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentgraph graph", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--note", metavar="TEXT", help="the event's note (what changed -> result)")
        p.add_argument("--expect-updated", type=float, metavar="T",
                       help="refuse when the graph's `updated` is not T (it was written since you read it)")
        p.add_argument("--dir", type=Path, metavar="D", help="graph folder (default: the nearest .opencode/graph)")

    one = sub.add_parser("set", help="change one node")
    one.add_argument("task")
    one.add_argument("node")
    one.add_argument("--status", choices=NODE_STATUSES)
    text = one.add_mutually_exclusive_group()
    text.add_argument("--evidence", metavar="TEXT")
    text.add_argument("--evidence-file", metavar="F", help="read the evidence from F ('-': stdin)")
    one.add_argument("--session", metavar="ID", help="the sub-agent session ('' clears it)")
    one.add_argument("--attempts", metavar="+1|N", help="add to the attempts (+N) or set them (N)")
    one.add_argument("--verdict", choices=("", *VERDICTS))
    one.add_argument("--stalled", type=_bool, metavar="true|false")
    one.add_argument("--invalidate", action="store_true", help="reopen passed work: an invalidated event and status pending")
    common(one)

    event = sub.add_parser("event", help="append an event: TASK [NODE] KIND")
    event.add_argument("words", nargs="+", metavar="TASK [NODE] KIND")
    common(event)
    return parser


def _read_text(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in and args_in[0] == "graph":
        args_in = args_in[1:]
    args = _parser().parse_args(args_in)
    try:
        if args.command == "set":
            evidence = args.evidence
            if args.evidence_file is not None:
                evidence = _read_text(args.evidence_file).strip()
            change = {"status": args.status, "evidence": evidence, "session": args.session, "attempts": args.attempts,
                      "verdict": args.verdict, "stalled": args.stalled, "note": args.note, "invalidate": args.invalidate}
            task, node_id = args.task, args.node
        else:
            if len(args.words) not in (2, 3):
                print("usage: graph event TASK [NODE] KIND --note TEXT", file=sys.stderr)
                return 2
            task, kind = args.words[0], args.words[-1]
            node_id = args.words[1] if len(args.words) == 3 else None
            if kind not in EVENT_NAMES:
                print(f"KIND must be one of {', '.join(EVENT_NAMES)}", file=sys.stderr)
                return 2
            change = {"kind": kind, "note": args.note}
        print(edit(task, node_id, change, dirs=outputs.graph_dirs(args.dir), expect_updated=args.expect_updated))
        return 0
    except Refused as exc:
        print(exc)
        return 1
    except (outputs.OutputError, OSError) as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
