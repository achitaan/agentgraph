"""Record task runs, feedback and prompt improvements; report their signals."""
from __future__ import annotations

import argparse
import fcntl
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

from . import check, sources
from .contract import VERDICTS, TaskGraph, objective_coverage

DUE_RUNS = 5
DUE_FEEDBACK = 2

_RUNS = "runs.jsonl"
_FEEDBACK = "feedback.jsonl"
_IMPROVEMENTS = "improvements.jsonl"
_Row = tuple[bytes, dict | None]


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


def _history(home: Path) -> tuple[list[_Row], list[dict], list[dict]]:
    runs = _read(home / _RUNS, _valid_run)
    responses = sorted(_records(_read(home / _FEEDBACK, _valid_feedback)), key=lambda item: item["time"])
    improvements = sorted(_records(_read(home / _IMPROVEMENTS, _valid_improvement)), key=lambda item: item["time"])
    return runs, responses, improvements


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
    with _lock(path.parent, writing=True), path.open("a+b") as stream:
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


def _summary(graph: TaskGraph, now: float, costs: dict[str, float], outcome: str) -> dict:
    path = Path(os.path.abspath(graph.path))
    delivered, total = objective_coverage(graph)
    session_ids = _session_ids(graph)
    cost_ids = session_ids & costs.keys()
    cost = sum(costs[sid] for sid in sorted(cost_ids)) if cost_ids else None
    return {
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
            notes.append(f"not recorded {run['task']}: cannot abandon a run already recorded as passed")
            merged.append((raw, old))
            refused = True
            continue
        if old["updated"] > run["updated"]:
            notes.append(f"not recorded {run['task']}: kept newer stored run (updated {old['updated']})")
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
            notes.append(f"not recorded {run['task']}: unchanged")
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
                    notes.append(f"not recorded {graph.task}: cannot abandon a passed graph")
                    code = 2
                    continue
                if not valid_created:
                    try:
                        graph = replace(graph, created=path.stat().st_mtime)
                    except OSError as exc:
                        warnings.append(f"graph: skipped {path.name}: {exc}")
                        continue
            elif graph.status != "passed":
                notes.append(f"skipped {graph.task}: unfinished ({graph.status})")
                continue
            else:
                if not valid_created:
                    identity_problem = "graph created must be a finite number greater than 0"
                    if identity_problem not in problems:
                        problems = [identity_problem, *problems]
                if problems:
                    notes.append(f"not recorded {graph.task}: {len(problems)} problems (run agentgraph check)")
                    notes.extend(f"  - {problem}" for problem in problems[:3])
                    code = max(code, 1)
                    continue
            if not all(map(_number, (graph.created, graph.updated, graph.updated - graph.created))):
                warnings.append(f"graph: skipped {path.name}: non-finite timestamps")
                continue
            selected.append(graph)

        rows, responses, improvements = _history(home)
        costs = _session_costs(db_path, selected)
        when = _now(now)
        outcome = "abandoned" if abandoned else "passed"
        rows, saved, refused = _upsert(rows, [_summary(graph, when, costs, outcome) for graph in selected], notes)
        latest = improvements[-1] if improvements else None
        runs, responses = _pending(_records(rows), responses, _watermark(latest))
        due = _due_fields(runs, responses)
        if saved:
            _replace(home / _RUNS, rows)
    return _RecordResult(saved, warnings, notes, 2 if warnings or refused else code, due)


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
) -> dict:
    """Record an applied improvement without opening or modifying its file."""
    fields = {"file": str(file) if isinstance(file, Path) else file,
              "summary": summary, "signals": signals or [], "metric": metric}
    if seen_until is not None:
        fields["seen_until"] = seen_until
    return _append(home / _IMPROVEMENTS, now, fields)


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


def _watermark(latest: dict | None) -> float | None:
    return latest.get("seen_until", latest["time"]) if latest is not None else None


def _pending(runs: list[dict], responses: list[dict], watermark: float | None) -> tuple[list[dict], list[dict]]:
    if watermark is None:
        return runs, responses
    return ([run for run in runs if run["recorded"] > watermark],
            [item for item in responses if item["time"] > watermark])


def _due_fields(runs: list[dict], responses: list[dict]) -> dict:
    return {"pending": {"runs": len(runs), "feedback": len(responses)},
            "due": len(runs) >= DUE_RUNS or len(responses) >= DUE_FEEDBACK}


def signals(home: Path, now: float | None, *, all_items: bool = False) -> dict:
    """Read the ledger and aggregate pending or historical signals."""
    generated = _now(now)
    with _lock(home, writing=False) as locked:
        rows, responses, improvements = _history(home)
        if locked:
            generated = _now(now)
    runs = _records(rows)
    latest = improvements[-1] if improvements else None
    watermark = _watermark(latest)
    pending_runs, pending_feedback = _pending(runs, responses, watermark)
    trend = None
    if latest is not None:
        cutoff = latest["time"]
        trend = {"before": _metrics([run for run in runs if run.get("created", 0) < cutoff]),
                 "after": _metrics([run for run in runs if run.get("created", 0) >= cutoff])}
    scope = runs if all_items else pending_runs
    nodes = [node for run in scope for node in run["nodes"]]
    verdicts = Counter(node["verdict"] for node in nodes if node["type"] == "gate")
    outcomes = Counter(map(_outcome, scope))
    return {
        "generated": generated, "last_improvement": latest, "watermark": watermark,
        **_due_fields(pending_runs, pending_feedback),
        "feedback": responses if all_items else pending_feedback,
        "lessons": [{"task": run["task"], "text": text} for run in scope for text in run["lessons"]],
        "discovered": [{"task": run["task"], "note": note} for run in scope for note in run["discovered"]],
        "agents": _agent_stats(nodes), "gates": {verdict: verdicts[verdict] for verdict in VERDICTS},
        "outcomes": {"passed": outcomes["passed"], "abandoned": outcomes["abandoned"], "unknown": outcomes[None]},
        "escalations": _escalations(scope),
        "amendments": sum(run["amendments"] for run in scope), "trend": trend, "improvements": improvements,
    }


def _print_due(report: dict) -> None:
    label = "improvement due:" if report["due"] else "not due:"
    pending = report["pending"]
    print(f"{label} {pending['runs']} pending runs, {pending['feedback']} pending feedback items")


def _print_report(report: dict) -> None:
    _print_due(report)
    for title, field in (("Feedback", "feedback"), ("Lessons", "lessons"), ("Discovered", "discovered")):
        if report[field]:
            print(f"{title}:")
            for item in report[field]:
                label = "/".join(filter(None, (item.get("task"), item.get("agent"))))
                prefix = f"{label}: " if label else ""
                print(f"  - {prefix}{item.get('text', item.get('note'))}")
    if report["agents"]:
        print("Agent               Nodes  Passed  First pass (rate)  Mean attempts  Away  To  Stalled  Failures")
        for agent, stats in report["agents"].items():
            rate = stats["first_pass_rate"]
            rate_text = "—" if rate is None else f"{rate:.0%}"
            attempts = stats["mean_attempts"]
            attempts_text = "—" if attempts is None else f"{attempts:.2f}" if isinstance(attempts, float) else str(attempts)
            failures = ", ".join(f"{kind}={count}" for kind, count in sorted(stats["failure_kinds"].items()))
            first = f"{stats['first_pass']} ({rate_text})"
            print(f"{agent:<19} {stats['nodes']:>5}  {stats['passed']:>6}  {first:>17}  "
                  f"{attempts_text:>13}  {stats['escalated_away']:>4}  {stats['escalated_to']:>2}  "
                  f"{stats['stalled']:>7}  {failures or '—'}")
    if report["escalations"]:
        print("Escalations:")
        for item in report["escalations"]:
            tiers = " → ".join(item["tiers"])
            detail = ", ".join(filter(None, (item["failure_kind"], "stalled" if item["stalled"] else "")))
            suffix = f", {detail}" if detail else ""
            print(f"  - {item['task']}/{item['node']}: {tiers} ({item['attempts']} attempts{suffix})")
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


def _improvement_fields(args: argparse.Namespace) -> dict:
    fields = {"file": args.file, "summary": args.summary, "signals": args.signal,
              "metric": args.metric, "seen_until": args.seen_until}
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
    improved_parser = commands.add_parser("improved", help="record an applied improvement")
    improved_parser.add_argument("--json-file", type=Path, help="read all improvement metadata from a JSON file")
    improved_parser.add_argument("--file")
    improved_parser.add_argument("--summary")
    improved_parser.add_argument("--signal", action="append")
    improved_parser.add_argument("--metric")
    improved_parser.add_argument("--seen-until", type=_finite_float, help="generated time of the signals report used")
    signals_parser = commands.add_parser("signals", help="report improvement signals")
    signals_parser.add_argument("--json", action="store_true")
    signals_parser.add_argument("--all", action="store_true")
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
                print(f"recorded {run['task']}: {run['nodes_passed']}/{run['nodes_total']} nodes passed ({run['path']})")
            _print_due(result.due)
            return result.code
        if args.command == "feedback":
            if feedback(home, None, _feedback_text(args), agent=args.agent, task=args.task) is None:
                print("feedback text must not be empty", file=sys.stderr)
                return 2
        elif args.command == "improved":
            improved(home, None, **_improvement_fields(args))
        else:
            report = signals(home, None, all_items=args.all)
            if args.json:
                print(json.dumps(report, allow_nan=False))
            else:
                _print_report(report)
    except (OSError, ValueError, RecursionError) as exc:
        print(f"ledger: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
