"""Handoff notes for a lane that rolls over: `agentgraph handoff path|build|check|log|leads`.

A lane that has grown past the point where its context is cheap writes a notes file. `build` joins that file, word for
word, with what code can say without any model call (the task as the lane was given it, the work folder, the files
changed, the task-graph node), and prints the whole as the summary the rollover plugin hands to OpenCode in place of a
model-written one (`rollover-plugin/`, docs/ROLLOVER.md).

**Where.** The lane's notes are `<work dir>/.opencode/handoff/<session id>.md` (`--notes-dir` moves them to another folder
under `.opencode/` of the work dir; any other value, such as `.` or `src`, is ignored with a line on stderr, because the
plugin puts a .gitignore of `*` in that folder). Everything else the plugin keeps (its per-session state and its decision
log, one JSON line per decision with no message text) is in one folder outside any project, `<data home>/agentgraph/rollover/`,
where the data home is `$XDG_DATA_HOME` or `~/.local/share` (`AGENTGRAPH_ROLLOVER_HOME` replaces the whole folder). `log`
reads it. `leads` prints the agents that run child sessions of their own (hubs, council leads, team leads), which the
plugin leaves alone.

**Exit codes.** `build` and `check`: 0 notes fresh (`build` prints the summary); 3 notes missing, empty, written before
`--newer-than`, or (without `--newer-than`) older than `--max-age` seconds (default 1800) and not `--allow-stale`;
4 (`build` only) the task, the lane's first user message, could not be read (database locked, unreadable or absent), so
no summary is printed and the caller falls back to a normal one; 1 any other failure, one line on stderr; 2 bad arguments.
The first user message is read from the session database read-only and goes into the summary only: no other output of
this module, and no log line, carries message text.

**Size.** `build` stays under `--limit` characters (default 24,000). The task keeps its room first (up to 6,000
characters), then the code-written parts and the notes share the rest. Notes over their room keep their head and their
last 2,000 characters (where the next step is) and lose the middle, with a mark.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

NOTES_SUBDIR = Path(".opencode") / "handoff"
LOG_NAME = "rollover.log"
SESSION = re.compile(r"[A-Za-z0-9_-]{1,100}")
MAX_AGE, LIMIT, TASK_CHARS, EVIDENCE_CHARS, FILE_NAMES, NOTES_TAIL, MIN_LIMIT = 1800, 24_000, 6_000, 1_500, 60, 2_000, 1_000
STALE_EXIT, NO_TASK_EXIT = 3, 4
GIT_SECONDS = 5
GIT_TIMED_OUT = "git timed out"
DB_SECONDS = 1.0


class HandoffError(Exception):
    pass


class GitTimeout(Exception):
    pass


def data_home() -> Path:
    """Where OpenCode keeps its data: `$XDG_DATA_HOME` when it is an absolute path, else `~/.local/share`."""
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".local" / "share"


def default_db() -> Path:
    """The OpenCode database: under `$XDG_DATA_HOME` when that is set, else `sources.DB_PATH` (where the rest of agentgraph looks)."""
    xdg = os.environ.get("XDG_DATA_HOME", "")
    if xdg and os.path.isabs(xdg):
        return Path(xdg) / "opencode" / "opencode.db"
    from . import sources
    return sources.DB_PATH


def rollover_home() -> Path:
    """The plugin's own folder (state, decision log), outside any project."""
    own = os.environ.get("AGENTGRAPH_ROLLOVER_HOME", "")
    return Path(own) if own and os.path.isabs(own) else data_home() / "agentgraph" / "rollover"


def _session(value: str) -> str:
    if not isinstance(value, str) or not SESSION.fullmatch(value):
        raise HandoffError("bad session id")
    return value


def notes_dir_problem(value) -> str | None:
    """Why `value` cannot be a notes folder, or None. It must be a relative folder under `.opencode/` with no `..`
    (the folder gets a .gitignore of `*`, so `.`, `src` or `.opencode` itself would hide real work from git)."""
    if not isinstance(value, str) or not value:
        return "must be a folder name"
    if os.path.isabs(value) or re.match(r"^[A-Za-z]:|^[\\/]", value):
        return "must be a relative folder"
    parts = [part for part in re.split(r"[\\/]+", value) if part and part != "."]
    if ".." in parts:
        return "must not contain .."
    if len(parts) < 2 or parts[0] != ".opencode":
        return "must be a folder under .opencode/"
    return None


def notes_folder(directory, notes_dir=None) -> Path:
    """`<directory>/.opencode/handoff` or the given folder under `.opencode/`; any other value is ignored (see `notes_dir_problem`)."""
    base = Path(directory if directory else os.getcwd())
    if notes_dir and notes_dir_problem(notes_dir) is None:
        return base.joinpath(*[part for part in re.split(r"[\\/]+", notes_dir) if part and part != "."])
    return base / NOTES_SUBDIR


def lead_names(structure_dir=None) -> list[str]:
    """The agents that run child sessions of their own: the hubs, the council leads (agents with seats) and the team leads
    of the structure folder. Raises HandoffError when the structure folder cannot be read."""
    from . import teams
    root = Path(structure_dir) if structure_dir else teams.STRUCTURE_DIR
    try:
        shape = teams._agents(root)
        found = set(shape["hubs"]) | set(shape["seats"]) | {team["lead"] for team in teams.read_teams(root)[0]}
    except (teams.TeamError, OSError) as exc:
        raise HandoffError(f"no readable structure folder at {root}") from exc
    return sorted(name for name in found if isinstance(name, str) and name)


def notes_path(session: str, directory=None, notes_dir=None) -> Path:
    return notes_folder(directory, notes_dir) / f"{_session(session)}.md"


def notes_state(path: Path, max_age: float = MAX_AGE, now: float | None = None) -> dict:
    """{state: fresh|stale|missing|empty, age: seconds or None, bytes}."""
    try:
        info = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"state": "missing", "age": None, "bytes": 0}
    age = max(0.0, (time.time() if now is None else now) - info.st_mtime)
    if not text.strip():
        return {"state": "empty", "age": age, "bytes": info.st_size}
    return {"state": "stale" if age > max_age else "fresh", "age": age, "bytes": info.st_size}


# ---- the parts code writes ----

def first_message(session: str, db_path, timeout: float = DB_SECONDS) -> tuple[str, int | None]:
    """The session's first user message and its time (milliseconds); ('', None) when it cannot be read."""
    try:
        db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
    except (sqlite3.Error, OSError):
        return "", None
    try:
        row = db.execute("SELECT data FROM session_message WHERE session_id=? AND type='user' ORDER BY seq LIMIT 1", (session,)).fetchone()
    except sqlite3.Error:
        return "", None
    finally:
        db.close()
    try:
        data = json.loads(row[0]) if row else {}
    except ValueError:
        return "", None
    if not isinstance(data, dict):
        return "", None
    text, when = data.get("text"), data.get("time")
    created = when.get("created") if isinstance(when, dict) else None
    return (text if isinstance(text, str) else ""), (created if isinstance(created, (int, float)) else None)


def cut_notes(text: str, size: int) -> str:
    """Notes cut to `size`: the head and the last NOTES_TAIL characters stay (the next step is at the end), the middle goes."""
    if size <= 0:
        return ""
    if len(text) <= size:
        return text
    mark = "\n[... notes cut here to fit: the middle is left out ...]\n"
    room = max(0, size - len(mark))
    tail = min(NOTES_TAIL, room // 2)
    return text[:room - tail] + mark + (text[len(text) - tail:] if tail else "")


def cut_middle(text: str, size: int, head_share: float = 0.7) -> str:
    if size <= 0:
        return ""
    if len(text) <= size:
        return text
    mark = "\n[... cut ...]\n"
    room = max(0, size - len(mark))
    head = int(room * head_share)
    return text[:head] + mark + (text[len(text) - (room - head):] if room - head > 0 else "")


def _git(directory: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, errors="replace",
                              timeout=GIT_SECONDS, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except subprocess.TimeoutExpired as exc:
        raise GitTimeout() from exc
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def changed_files(directory: Path, since_ms: float | None = None) -> list[str] | None:
    """Names changed in the work tree (uncommitted, or committed since `since_ms`), sorted; None when not a git work tree;
    raises GitTimeout when git takes too long."""
    status = _git(directory, "status", "--porcelain", "-z")
    if status is None:
        return None
    names, parts = set(), status.split("\0")
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 4:
            continue
        names.add(entry[3:])
        if entry[0] in "RC":
            index += 1  # the original name of a rename or copy follows
    if since_ms:
        log = _git(directory, "log", f"--since=@{int(since_ms // 1000)}", "--name-only", "--pretty=format:")
        names.update(line for line in (log or "").splitlines() if line.strip())
    skip = NOTES_SUBDIR.as_posix() + "/"  # the notes folder only; the rest of .opencode/ is the lane's work too
    return sorted(name for name in names if not name.startswith(skip))


def graph_node(directory: Path, session: str) -> dict | None:
    """The task-graph node whose `session` is this one, from `<dir>/.opencode/graph/*.json`."""
    folder = Path(directory) / ".opencode" / "graph"
    try:
        files = sorted(folder.glob("*.json"))
    except OSError:
        return None
    for file in files:
        try:
            graph = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
            if isinstance(node, dict) and node.get("session") == session:
                return {**node, "task": graph.get("task") or file.stem}
    return None


def _inside(name: str, scope: list) -> bool:
    return any(isinstance(s, str) and s and (name == s.rstrip("/") or name.startswith(s.rstrip("/") + "/")) for s in scope)


def _node_text(node: dict) -> str:
    lines = [f"Task graph `{node['task']}`, node `{node.get('id', '?')}` ({node.get('type', '?')}, agent {node.get('agent', '?')}, "
             f"status {node.get('status', '?')}, attempt {node.get('attempts', '?')})."]
    for label, key in (("Goal", "goal"), ("Done when", "done_when")):
        if node.get(key):
            lines.append(f"{label}: {node[key]}")
    scope = node.get("scope")
    lines.append("Scope (files it may write): " + (", ".join(str(s) for s in scope) if isinstance(scope, list) and scope else "none, read-only"))
    return "\n".join(lines)


def _files_text(names, scope) -> str:
    if names == GIT_TIMED_OUT:
        return "(git timed out, so the list of changed files is left out)"
    if names is None:
        return "(the work folder is not a git work tree)"
    if not names:
        return "(none)"
    shown = [n for n in names if _inside(n, scope)] if isinstance(scope, list) and scope else names
    other = len(names) - len(shown)
    out = shown[:FILE_NAMES]
    text = "\n".join(f"- {n}" for n in out)
    if len(shown) > len(out):
        text += f"\n- ... and {len(shown) - len(out)} more"
    if other > 0:
        text += f"\n({other} changed files outside the node's scope are not listed)"
    return text or "(none in the node's scope)"


def build(session: str, directory=None, *, db_path=None, notes_dir=None, limit: int = LIMIT, max_age: float | None = None,
          allow_stale: bool = False, newer_than: float | None = None, task_chars: int = TASK_CHARS,
          db_timeout: float = DB_SECONDS) -> tuple[int, str]:
    """(exit code, summary text). Exit 3 with an empty text when the notes are missing, empty, or older than `newer_than`
    (epoch seconds) or, when that is not given, than `max_age`; exit 4 when the task cannot be read."""
    _session(session)
    work = Path(os.path.abspath(directory if directory else os.getcwd()))
    path = notes_path(session, work, notes_dir)
    state = notes_state(path, MAX_AGE if max_age is None else max_age)
    if state["state"] in ("missing", "empty"):
        return STALE_EXIT, ""
    if newer_than is not None:
        try:
            if path.stat().st_mtime < newer_than:
                return STALE_EXIT, ""
        except OSError:
            return STALE_EXIT, ""
    elif state["state"] == "stale" and not allow_stale:
        return STALE_EXIT, ""
    notes = path.read_text(encoding="utf-8", errors="replace").strip()
    task, started = first_message(session, db_path or default_db(), db_timeout)
    task = task.strip()
    if not task:
        return NO_TASK_EXIT, ""
    node = graph_node(work, session)
    try:
        names = changed_files(work, started)
    except GitTimeout:
        names = GIT_TIMED_OUT

    head = ("# Handoff summary\n\nThis session was rolled over: the earlier conversation was replaced by this text, which code "
            "put together from the lane's own notes. Carry on from the next step in the notes; ask nothing that the notes already answer.\n")
    room = max(0, limit - len(head) - 40)
    task_title = "The task as the lane was given it"
    task_block = f"## {task_title}\n\n" + cut_middle(task, min(task_chars, max(200, room // 2))) + "\n"   # reserved before the notes
    notes_head = "## Notes written by the lane (word for word)\n\n"
    notes_part = notes_head + cut_notes(notes, room - len(task_block) - len(notes_head) - 4) + "\n"
    left = room - len(task_block) - len(notes_part) - 2
    sections = [("Work folder", str(work)),
                ("Task graph node", _node_text(node) if node else None),
                (task_title, None),
                ("Last evidence on the node", cut_middle(str(node["evidence"]).strip(), EVIDENCE_CHARS) if node and node.get("evidence") else None),
                ("Files changed in the work tree", _files_text(names, node.get("scope") if node else None))]
    parts = []
    for title, body in sections:  # in order of importance: what is left after the task and the notes goes to the first ones
        if title == task_title:
            parts.append(task_block)
            continue
        if body is None:
            continue
        block = f"## {title}\n\n"
        fit = left - len(block) - 2
        if fit < 40:
            continue
        text = block + cut_middle(body, fit) + "\n"
        left -= len(text) + 1
        parts.append(text)
    return 0, head + "\n" + "\n".join(parts) + "\n" + notes_part


# ---- the decision log ----

def log_path(home=None) -> Path:
    return (Path(home) if home else rollover_home()) / LOG_NAME


def read_log(home=None, limit: int = 40, session: str | None = None) -> list[dict]:
    rows = []
    try:
        lines = log_path(home).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and (session is None or row.get("session") == session):
            rows.append(row)
    return rows[-limit:] if limit > 0 else rows


def _clean(value, size: int) -> str:
    return re.sub(r"[^\x20-\x7e]", "?", str(value))[:size]


def log_table(rows: list[dict]) -> str:
    if not rows:
        return "no decisions logged"
    header = ("time", "session", "event", "context", "agent", "detail")
    body = []
    for row in rows:
        stamp, ctx = row.get("t"), row.get("context")
        when = time.strftime("%m-%d %H:%M:%S", time.localtime(stamp / 1000)) if isinstance(stamp, (int, float)) else "?"
        body.append((when, _clean(row.get("session", ""), 14), _clean(row.get("event", ""), 12),
                     f"{int(ctx):,}" if isinstance(ctx, (int, float)) else "-", _clean(row.get("agent") or "-", 14),
                     _clean(row.get("reason", ""), 60)))
    widths = [max(len(h), *(len(r[i]) for r in body)) for i, h in enumerate(header)]
    line = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([line(header), *(line(r) for r in body)])


# ---- command line ----

def _newer_than(value: str) -> float:
    number = float(value)
    return number / 1000 if number > 1e11 else number  # epoch seconds, or milliseconds as the plugin has them


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentgraph handoff", description="Handoff notes for a lane that rolls over.")
    sub = parser.add_subparsers(dest="op", required=True)
    for name, text in (("path", "print the notes file path of a session"),
                       ("build", "print the summary: code-written parts, then the lane's notes"),
                       ("check", "say in one line whether the notes are fresh"),
                       ("log", "print the plugin's decision log as a table"),
                       ("leads", "print the names of the agents that run child sessions (hubs, council and team leads), one per line")):
        sp = sub.add_parser(name, help=text)
        if name == "leads":
            sp.add_argument("--structure-dir", help="the structure folder (default: the one agentgraph uses)")
            continue
        if name == "log":
            sp.add_argument("--home", help="the plugin's folder (default: <data home>/agentgraph/rollover)")
            sp.add_argument("--session", help="only this session")
            sp.add_argument("--last", type=int, default=40, help="most rows (default 40, 0 = all)")
            continue
        sp.add_argument("--dir", help="the work folder (default: the current folder)")
        sp.add_argument("--notes-dir", help="the notes folder, if not .opencode/handoff under the work folder")
        sp.add_argument("--session", required=True, help="the session id")
        if name in ("build", "check"):
            sp.add_argument("--max-age", type=float, default=None, help=f"seconds before notes count as stale (default {MAX_AGE}; not used with --newer-than)")
            sp.add_argument("--newer-than", type=_newer_than, default=None, help="notes written before this time (epoch seconds or milliseconds) are stale")
        if name == "build":
            sp.add_argument("--allow-stale", action="store_true", help="use old notes too (not with --newer-than)")
            sp.add_argument("--db", help="the OpenCode database (default: the user's, opened read-only)")
            sp.add_argument("--limit", type=int, default=LIMIT, help=f"most characters in the summary (default {LIMIT})")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.op == "leads":
            print("\n".join(lead_names(args.structure_dir)))
            return 0
        problem = notes_dir_problem(args.notes_dir) if getattr(args, "notes_dir", None) else None
        if problem:
            print(f"handoff: --notes-dir {problem}; the default .opencode/handoff is used", file=sys.stderr)
        if args.op == "path":
            print(notes_path(args.session, args.dir, args.notes_dir))
        elif args.op == "log":
            print(log_table(read_log(args.home, args.last, args.session)))
        elif args.op == "check":
            path = notes_path(args.session, args.dir, args.notes_dir)
            state = notes_state(path, MAX_AGE if args.max_age is None else args.max_age)
            if state["age"] is None:
                print(f"missing: no notes at {path}")
                return STALE_EXIT
            if args.newer_than is not None and state["state"] != "empty":
                state["state"] = "fresh" if time.time() - state["age"] >= args.newer_than else "stale"
            print(f"{state['state']}: notes written {int(state['age'])}s ago, {state['bytes']} bytes, {path}")
            return 0 if state["state"] == "fresh" else STALE_EXIT
        else:
            if args.limit < MIN_LIMIT:
                raise HandoffError(f"--limit must be at least {MIN_LIMIT}")
            code, text = build(args.session, args.dir, db_path=args.db, notes_dir=args.notes_dir, limit=args.limit,
                               max_age=args.max_age, allow_stale=args.allow_stale, newer_than=args.newer_than)
            if code:
                print("handoff: notes missing or stale" if code == STALE_EXIT else "handoff: the task (first user message) could not be read", file=sys.stderr)
                return code
            sys.stdout.write(text)
    except HandoffError as exc:
        print(f"handoff: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"handoff: {exc.strerror or 'error'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
