"""Hand edits as evidence for the improvement loop: an agent whose outputs the user keeps correcting by hand, or whose
instruction the user keeps rewriting, is a problem to fix at its source.

Two files-only measures, per agent, over a window (default the last 14 days):
- the hand edits of its outputs that were not undone, from `edits.jsonl` in the memory home (count, tasks, lines
  changed; the log never holds content);
- the rewrites of its instruction text, from the undo records the toolkit leaves in the backups folder
  (`<backups>/<date>-agent-files/text-<agent>-<id>/record.json`, timed by the file). A skills-only change, one that
  changed nothing in the text, and an edit with its own undo are not counted.
Missing or unreadable files mean zero. Nothing here writes anything.

The signal is due for the agent with the most edits when it reaches the threshold; an improvement recorded for that
agent (its name in the improvement's file, summary, signals or size change) after an edit silences the edits before it.
`python3 -m agentgraph.handedits` prints the measure."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

DAYS = 14
EDITS = 3      # output edits that make the signal due ...
TASKS = 2      # ... across at least this many tasks
REWRITES = 3   # instruction rewrites that make it due
LOG = "edits.jsonl"
LOG_READ = 8 * 1024 * 1024
RECORD_READ = 8 * 1024 * 1024
_FOLDER = re.compile(r"(?:text)-([a-z][a-z0-9_-]{0,63})-[0-9a-f]{12}")
# What the log may carry of what a graph file or a log line says; anything else is not an agent or a task here.
# AGENT_NAME is the agents folder's rule; OUTPUT_NAME is the Desktop's rule for the request fields (task, step,
# output: no whitespace or control character, no leading "-", at most 200 characters).
AGENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
OUTPUT_NAME = re.compile(r"[^\s\-\x00-\x1f\x7f][^\s\x00-\x1f\x7f]{0,199}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}-agent-files")


def _number(value) -> bool:
    return type(value) in (int, float) and value == value and abs(value) != float("inf")


def default_paths(home: Path) -> tuple[Path, Path]:
    """(backups, agents folder) beside the memory home's orchestration folder, as in `~/.config/opencode`."""
    root = Path(home).expanduser().parent.parent
    return root / "backups", root / "agents"


def _tail(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > limit:
            stream.seek(size - limit)
            stream.readline()  # a cut line
        return stream.read(limit)


def _saved(item: dict, backups: Path | None) -> bool:
    """False for a line whose save was stopped before the graph was written: no line counts and no undo record
    (`<backups>/<date>-outputs/edit-<id>/record.json`). Without `backups`, every line counts."""
    if backups is None or type(item.get("lines_added")) is int or type(item.get("lines_removed")) is int:
        return True
    edit_id = item.get("id")
    if not isinstance(edit_id, str) or not re.fullmatch(r"[0-9a-f]{12}", edit_id):
        return False
    try:
        return any((day / f"edit-{edit_id}" / "record.json").is_file() for day in Path(backups).glob("*-outputs"))
    except OSError:
        return False


def output_edits(home: Path, since: float, backups: Path | None = None) -> dict[str, list[dict]]:
    """{agent: [{"time", "task", "lines"}]} of the log's edits at or after `since` that were not undone. An edit
    whose agent or task is not a plain name (AGENT_NAME, OUTPUT_NAME) is under no agent and is not counted. With
    `backups`, a line whose save never reached the graph (see `_saved`) is not an edit."""
    try:
        raw = _tail(Path(home) / LOG, LOG_READ)
    except OSError:
        return {}
    found: dict[str, list[dict]] = {}
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not (isinstance(item, dict) and _number(item.get("time")) and item["time"] >= since
                and item.get("undone") is not True and isinstance(item.get("agent"), str) and AGENT_NAME.fullmatch(item["agent"])
                and isinstance(item.get("task"), str) and OUTPUT_NAME.fullmatch(item["task"])
                and _saved(item, backups)):
            continue
        lines = sum(item[key] for key in ("lines_added", "lines_removed") if type(item.get(key)) is int)
        found.setdefault(item["agent"], []).append({"time": item["time"], "task": item["task"], "lines": lines})
    return found


def _text_of(data: bytes | None) -> str | None:
    if data is None:
        return None
    try:
        from . import agentfiles
        parsed = agentfiles.parse(data)
    except Exception:
        return None
    return None if parsed is None else parsed["text"]


def _record(path: Path) -> dict | None:
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            data = json.loads(stream.read(RECORD_READ + 1).decode("utf-8"))
        before = base64.b64decode(data["before"], validate=True) if data.get("before") is not None else None
        after = data["after"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, binascii.Error):
        return None
    if not isinstance(data, dict) or data.get("op") != "text" or not isinstance(after, str) or before is None:
        return None
    return {"time": info.st_mtime, "path": path, "name": data.get("agent"), "before": before,
            "before_digest": hashlib.sha256(before).hexdigest(), "after": after}


def _agent_records(backups: Path) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    try:
        days = sorted(day for day in Path(backups).iterdir() if _DAY.fullmatch(day.name))
    except OSError:
        return found
    for day in days:
        try:
            folders = list(day.iterdir())
        except OSError:
            continue
        for folder in folders:
            match = _FOLDER.fullmatch(folder.name)
            record = match and _record(folder / "record.json")
            if record and record["name"] == match.group(1):
                found.setdefault(match.group(1), []).append(record)
    for records in found.values():
        records.sort(key=lambda item: (item["time"], str(item["path"])))
    return found


def _rewrites(records: list[dict], current: bytes | None) -> list[float]:
    """Times of the records that changed the instruction text and were not undone by a later record."""
    cancelled: set[int] = set()
    for index, record in enumerate(records):
        for earlier in range(index - 1, -1, -1):
            other = records[earlier]
            if earlier not in cancelled and other["after"] == record["before_digest"] \
                    and record["after"] == other["before_digest"]:
                cancelled.update((earlier, index))
                break
    times = []
    for index, record in enumerate(records):
        if index in cancelled:
            continue
        after = None
        if index + 1 < len(records) and records[index + 1]["before_digest"] == record["after"]:
            after = records[index + 1]["before"]
        elif current is not None and hashlib.sha256(current).hexdigest() == record["after"]:
            after = current
        before_text, after_text = _text_of(record["before"]), _text_of(after)
        if before_text is None or after_text is None or before_text != after_text:
            times.append(record["time"])
    return times


def instruction_edits(backups: Path, agents_dir: Path | None, since: float) -> dict[str, list[float]]:
    """{agent: [time]} of instruction rewrites at or after `since`."""
    found = {}
    for name, records in _agent_records(backups).items():
        current = None
        if agents_dir is not None:
            try:
                current = (Path(agents_dir) / f"{name}.md").read_bytes()
            except OSError:
                pass
        times = [t for t in _rewrites(records, current) if t >= since]
        if times:
            found[name] = times
    return found


def _mentions(improvement: dict, agent: str) -> bool:
    parts = [improvement.get("summary"), improvement.get("metric")]
    for field in ("file", "signals"):
        value = improvement.get(field)
        parts += value if isinstance(value, list) else [value]
    parts += list(improvement.get("size_change") or [])
    pattern = re.compile(rf"(?<![a-z0-9-]){re.escape(agent)}(?![a-z0-9-])", re.I)
    return any(isinstance(part, str) and pattern.search(part) for part in parts)


def _cutoff(improvements: list[dict], agent: str) -> float | None:
    times = [item["time"] for item in improvements if _number(item.get("time")) and _mentions(item, agent)]
    return max(times, default=None)


def measure(home: Path, now: float | None = None, *, days: float = DAYS, backups: Path | None = None,
            agents_dir: Path | None = None, improvements: list[dict] | None = None) -> dict:
    """{"days", "since", "outputs": {agent: {"edits", "tasks", "lines", "latest"}}, "instructions": {agent: {"edits",
    "latest"}}}. With `improvements`, an agent's edits at or before the latest improvement naming it are left out."""
    now = time.time() if now is None else now
    since = now - days * 86400
    default_backups, default_agents = default_paths(home)
    backups = default_backups if backups is None else Path(backups)
    agents_dir = default_agents if agents_dir is None else Path(agents_dir)
    outputs: dict[str, dict] = {}
    for agent, edits in output_edits(home, since, backups).items():
        cutoff = _cutoff(improvements or [], agent)
        edits = [edit for edit in edits if cutoff is None or edit["time"] > cutoff]
        if edits:
            outputs[agent] = {"edits": len(edits), "tasks": sorted({edit["task"] for edit in edits}),
                              "lines": sum(edit["lines"] for edit in edits),
                              "latest": max(edit["time"] for edit in edits)}
    instructions: dict[str, dict] = {}
    for agent, times in instruction_edits(backups, agents_dir, since).items():
        cutoff = _cutoff(improvements or [], agent)
        times = [t for t in times if cutoff is None or t > cutoff]
        if times:
            instructions[agent] = {"edits": len(times), "latest": max(times)}
    return {"days": days, "since": since, "outputs": dict(sorted(outputs.items())),
            "instructions": dict(sorted(instructions.items()))}


def signal(home: Path, now: float | None = None, *, days: float = DAYS, min_edits: int = EDITS,
           min_tasks: int = TASKS, min_rewrites: int = REWRITES, backups: Path | None = None,
           agents_dir: Path | None = None, improvements: list[dict] | None = None) -> dict:
    """The measure plus `due`, and for each form the agent with the most edits that reaches its threshold."""
    try:
        found = measure(home, now, days=days, backups=backups, agents_dir=agents_dir, improvements=improvements)
    except (OSError, ValueError) as exc:
        return {"due": False, "error": str(exc)}
    outputs = [(row["edits"], len(row["tasks"]), row["lines"], agent) for agent, row in found["outputs"].items()
               if row["edits"] >= min_edits and len(row["tasks"]) >= min_tasks]
    rewrites = [(row["edits"], agent) for agent, row in found["instructions"].items() if row["edits"] >= min_rewrites]
    top_output = max(outputs, key=lambda item: (item[0], item[1], item[2], [-ord(c) for c in item[3]]), default=None)
    top_rewrite = max(rewrites, key=lambda item: (item[0], [-ord(c) for c in item[1]]), default=None)
    return {
        "due": bool(top_output or top_rewrite), "days": days,
        "thresholds": {"edits": min_edits, "tasks": min_tasks, "rewrites": min_rewrites},
        "outputs": None if top_output is None else {"agent": top_output[3], **found["outputs"][top_output[3]]},
        "instruction": None if top_rewrite is None else {"agent": top_rewrite[1], **found["instructions"][top_rewrite[1]]},
        "agents": {"outputs": found["outputs"], "instructions": found["instructions"]},
    }


def _days(days: float) -> str:
    return f"{days:g} day" + ("s" if days != 1 else "")


def signal_lines(report: dict) -> list[str]:
    """The lines `record` and `signals` print (none when nothing is due)."""
    if report.get("error"):
        return [f"hand edits: not measured ({report['error']})"]
    lines = []
    if report.get("outputs"):
        top = report["outputs"]
        lines.append(f"improvement due: hand edits: {top['agent']}'s outputs were corrected by hand {top['edits']} "
                     f"times in {_days(report['days'])} across {len(top['tasks'])} tasks")
    if report.get("instruction"):
        top = report["instruction"]
        lines.append(f"improvement due: hand edits: {top['agent']}'s instruction was rewritten by hand "
                     f"{top['edits']} times in {_days(report['days'])}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph.handedits", description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=Path, help="memory home holding edits.jsonl (default: $AGENTGRAPH_HOME or "
                                                  "~/.config/opencode/orchestration/memory)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--days", type=float, default=DAYS)
    parser.add_argument("--edits", type=int, default=EDITS)
    parser.add_argument("--tasks", type=int, default=TASKS)
    parser.add_argument("--rewrites", type=int, default=REWRITES)
    parser.add_argument("--backups", type=Path)
    parser.add_argument("--agents-dir", type=Path)
    args = parser.parse_args(argv)
    home = (args.home or Path(os.environ.get("AGENTGRAPH_HOME", "~/.config/opencode/orchestration/memory"))).expanduser()
    report = signal(home, days=args.days, min_edits=args.edits, min_tasks=args.tasks, min_rewrites=args.rewrites,
                    backups=args.backups, agents_dir=args.agents_dir)
    if args.json:
        print(json.dumps(report, allow_nan=False))
        return 0
    lines = signal_lines(report)
    print("\n".join(lines) if lines else "hand edits: nothing due")
    for form, label in (("outputs", "outputs corrected"), ("instructions", "instruction rewritten")):
        for agent, row in (report.get("agents") or {}).get(form, {}).items():
            extra = f", {len(row['tasks'])} tasks, {row['lines']} lines" if form == "outputs" else ""
            print(f"  {agent}: {label} {row['edits']} times{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
