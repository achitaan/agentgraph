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
STALE_EXIT, NO_TASK_EXIT, DB_EXIT = 3, 4, 5   # 5: the database could not be read (a one-off: the caller may try again later); 4: the lane's task is not in it
CHAT_LIMIT, USER_MESSAGE_CHARS, USER_CHARS, SUBAGENT_CHARS, GRAPH_CHARS = 30_000, 4_000, 12_000, 3_500, 2_500
CHAT_NOTES_MIN, REPORT_CHARS = 300, 8_000
# the topics the chat ask names; notes that mention fewer than 3 of them (or are under CHAT_NOTES_MIN characters) are not a handoff
NOTE_TOPICS = (("objective", "goal"), ("requirement", "limit", "rule"), ("decision", "approv"), ("done", "running", "blocked", "state"), ("next",), ("file",))
WHY = [""]   # why the last build gave exit 3 (for the one line on stderr)
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


def _open_db(db_path, timeout: float):
    try:
        return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
    except (sqlite3.Error, OSError):
        return None


def is_root(session: str, db_path, timeout: float = DB_SECONDS) -> bool:
    """True when the session has no parent session (a main chat); False when it has one or the database cannot say."""
    db = _open_db(db_path, timeout)
    if db is None:
        return False
    try:
        row = db.execute("SELECT parent_id FROM session_v2 WHERE id=?", (session,)).fetchone()
    except sqlite3.Error:
        return False
    finally:
        db.close()
    return bool(row) and row[0] is None


def _created(data: dict):
    created = (data.get("time") or {}).get("created") if isinstance(data.get("time"), dict) else None
    return created if isinstance(created, (int, float)) else None


def read_chat(session: str, db_path, timeout: float = DB_SECONDS) -> dict | None:
    """What the summary of a main chat is made of, read from the stored rows; None when the database cannot be read.

    Row layout of OpenCode 2.3 (checked on real chats): the user's typing is a row of type `user` (data.text, data.time.created in ms);
    a subagent's report is a `synthetic` row with metadata {source: subagent, childID, agent, state} whose text is the report; this plugin's
    ask is a `synthetic` row with metadata.source agentgraph-rollover; the summary of a compaction is a row of type `compaction` with its text
    in data.summary (not a user row), and no message is stored to continue after a compaction; "Loaded AGENTS.md" and shell output are other
    `synthetic` rows. So only `user` rows are quoted as the user's words. What cannot be told apart: a `user` row made by a command or skill
    expansion or a script looks like typing. The rows stay after a compaction (the model stops seeing them), so each summary can carry them again.

    Returns {users: [{seq, text, ms}], reports: [{seq, text, ms, child, agent, state}], state, children}; `state` is what the latest roll
    came into: answered (the latest user message was answered before the ask), midturn, late (a user message came after the ask) or unknown."""
    db = _open_db(db_path, timeout)
    if db is None:
        return None
    try:
        users = []
        for seq, raw in db.execute("SELECT seq, data FROM session_message WHERE session_id=? AND type='user' ORDER BY seq", (session,)):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            text = data.get("text") if isinstance(data, dict) else None
            if isinstance(text, str) and text.strip() and not text.lstrip().startswith("# Handoff summary"):
                users.append({"seq": seq, "text": text.strip(), "ms": _created(data)})
        reports, asks = [], []
        for seq, src, raw in db.execute("SELECT seq, json_extract(data, '$.metadata.source'), data FROM session_message WHERE session_id=? AND type='synthetic' "
                                        "AND json_extract(data, '$.metadata.source') IN ('subagent', 'agentgraph-rollover') ORDER BY seq", (session,)):
            if src == "agentgraph-rollover":
                asks.append(seq)
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            meta = data.get("metadata") if isinstance(data, dict) and isinstance(data.get("metadata"), dict) else {}
            if meta.get("childID") and isinstance(data.get("text"), str):
                reports.append({"seq": seq, "text": data["text"].strip(), "ms": _created(data), "child": str(meta["childID"]),
                                "agent": str(meta.get("agent") or "?"), "state": str(meta.get("state") or "")})
        # the compaction being built has its row already (found in a real run: no summary yet): only a compaction with a summary spends an ask
        last_compaction = db.execute("SELECT MAX(seq) FROM session_message WHERE session_id=? AND type='compaction' "
                                     "AND length(COALESCE(json_extract(data, '$.summary'), '')) > 0", (session,)).fetchone()[0] or 0
        mine = [a for a in asks if a > last_compaction]
        state = "unknown"
        if mine:
            first_ask = mine[0]
            before = db.execute("SELECT seq, json_extract(data, '$.finish') FROM session_message WHERE session_id=? AND type='assistant' AND seq<? ORDER BY seq DESC LIMIT 1",
                                (session, first_ask)).fetchone()
            last_user = max((u["seq"] for u in users if u["seq"] < first_ask), default=0)
            if any(u["seq"] > first_ask for u in users):
                state = "late"
            elif before and before[0] > last_user:
                state = "answered" if before[1] == "stop" else "midturn"
        rows = db.execute("SELECT id, agent, title, idle_outcome FROM session_v2 WHERE parent_id=? ORDER BY time_created DESC", (session,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    latest = {}
    for r in reports:          # ORDER BY seq: a child that was resumed ends with its newest state
        latest[r["child"]] = r["state"]
    children = [{"id": r[0], "agent": r[1] or "?", "title": " ".join(str(r[2] or "").split()), "state": latest.get(r[0]) or r[3] or "running"} for r in rows]
    children.sort(key=lambda c: c["state"] != "running")
    return {"users": users, "reports": reports, "state": state, "children": children}


def user_block(messages: list[dict], per_message: int, total: int) -> str:
    """The user's messages for the summary: the first (it states the job), every one that arrived after the notes, and the newest that fit
    `total` characters, each cut in the middle at `per_message`; one line says which ones were left out."""
    cut = [cut_middle(m["text"], per_message) for m in messages]
    n = len(cut)
    keep = {0} | {i for i, m in enumerate(messages) if m.get("newer")}
    used = sum(len(cut[i]) for i in keep)
    for i in range(n - 1, 0, -1):
        if i in keep:
            continue
        if used + len(cut[i]) > total:
            break
        keep.add(i)
        used += len(cut[i])
    parts, skipped_from = [], None
    for i in range(n):
        if i in keep:
            if skipped_from is not None:
                parts.append(f"[messages {skipped_from + 1} to {i} of {n} are left out: the size limit of {total} characters; the first, the newest and any that arrived after the notes are kept]")
                skipped_from = None
            tag = " (arrived after the notes were written: the notes do not mention it)" if messages[i].get("newer") else ""
            parts.append(f"### Message {i + 1} of {n}{tag}\n\n{cut[i]}")
        elif skipped_from is None:
            skipped_from = i
    return "\n\n".join(parts)


def subagents_text(children: list[dict], size: int) -> str:
    lines = []
    for c in children:
        lines.append(f"- {c['id']} ({c['agent']}, {c['state']}): {cut_middle(c['title'], 120)}")
    text = "\n".join(lines)
    if len(text) <= size:
        return text
    shown = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > size - 60:
            break
        shown.append(line)
        used += len(line) + 1
    return "\n".join(shown) + f"\n- ... and {len(lines) - len(shown)} more sessions (the oldest finished ones)"


def graphs_text(directory: Path, size: int) -> str:
    """The task graphs in `<dir>/.opencode/graph`: name and the nodes that are not passed, with status; graphs that have passed entirely are only counted."""
    folder = Path(directory) / ".opencode" / "graph"
    try:
        files = sorted(folder.glob("*.json"))
    except OSError:
        return ""
    lines, done = [], 0
    for file in files:
        try:
            graph = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        nodes = [n for n in graph.get("nodes", []) if isinstance(n, dict)] if isinstance(graph, dict) else []
        open_nodes = [n for n in nodes if n.get("status") != "passed"]
        if not nodes:
            continue
        if not open_nodes:
            done += 1
            continue
        shown = ", ".join(f"{n.get('id', '?')} ({n.get('status', '?')})" for n in open_nodes[:12])
        more = f", and {len(open_nodes) - 12} more" if len(open_nodes) > 12 else ""
        lines.append(f"- {file.stem}: {len(nodes) - len(open_nodes)} of {len(nodes)} nodes passed; not passed: {shown}{more}")
    if done:
        lines.append(f"({done} other graphs have passed entirely)")
    return cut_middle("\n".join(lines), size)


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


def build(session: str, directory=None, *, db_path=None, notes_dir=None, limit: int | None = None, max_age: float | None = None,
          allow_stale: bool = False, newer_than: float | None = None, task_chars: int = TASK_CHARS,
          db_timeout: float = DB_SECONDS, chat: bool | None = None, user_message_chars: int = USER_MESSAGE_CHARS,
          user_chars: int = USER_CHARS, report_chars: int = REPORT_CHARS) -> tuple[int, str]:
    """(exit code, summary text). Exit 3 with an empty text when the notes are missing, empty, or older than `newer_than`
    (epoch seconds) or, when that is not given, than `max_age`; exit 4 when the lane's task cannot be read; for a chat exit 5 when the
    database or the user's messages cannot be read (a one-off, not a verdict on the session), and exit 3 also when the notes are too thin or the
    subagent reports that arrived after them do not fit. `chat` True builds the summary of a main chat (the user's own messages, the chat's subagent sessions,
    the task graphs, then the notes), False the lane's; None chooses by the database: a session without a parent is a chat."""
    _session(session)
    WHY[0] = ""
    if chat is None:
        chat = is_root(session, db_path or default_db(), db_timeout)
    if limit is None:
        limit = CHAT_LIMIT if chat else LIMIT
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
    if chat:
        try:
            notes_ms = path.stat().st_mtime * 1000
        except OSError:
            return STALE_EXIT, ""
        return _build_chat(session, work, notes, db_path or default_db(), limit, db_timeout, user_message_chars, user_chars, notes_ms, report_chars)
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


STATE_LINES = {
    "answered": ("State at the roll: the latest user message (the last one below) was already answered before the notes were asked for. Reply with one short "
                 "line (for example `Rolled over; ready.`), start no work, call no tool, do not repeat the answer, and wait. A user message or a subagent "
                 "report that arrives later is new input: act on it."),
    "midturn": ("State at the roll: the roll came in the middle of the work on the latest user message (the last one below). Carry on from the next steps "
                "in the notes until that work is done."),
    "late": ("State at the roll: the last user message below arrived after the notes were written, so the notes do not mention it: read it and act on it "
             "first, then carry on with the notes."),
    "unknown": ("State at the roll is not known. If the notes say the latest user message is answered, reply with one short line and wait; if its work "
                "is not finished, carry on from the next steps in the notes."),
}


def _thin(notes: str) -> bool:
    low = notes.lower()
    return len(notes) < CHAT_NOTES_MIN or sum(1 for topic in NOTE_TOPICS if any(w in low for w in topic)) < 3


def _build_chat(session: str, work: Path, notes: str, db_path, limit: int, db_timeout: float, per_message: int, total: int,
                notes_ms: float = 0, report_chars: int = REPORT_CHARS) -> tuple[int, str]:
    if _thin(notes):
        WHY[0] = f"the notes are too thin for a handoff (under {CHAT_NOTES_MIN} characters, or fewer than 3 of the topics the ask names)"
        return STALE_EXIT, ""
    chat = read_chat(session, db_path, db_timeout)
    if chat is None or not chat["users"]:
        WHY[0] = "the database or the user's messages could not be read"
        return DB_EXIT, ""
    messages = chat["users"]
    for m in messages:
        m["newer"] = m["ms"] is not None and m["ms"] > notes_ms
    head = ("# Handoff summary (main chat)\n\nThis chat was rolled over: the earlier conversation was replaced by this text, which code put together "
            "from the user's own messages (word for word), the subagent reports that arrived after the notes, the sessions this chat started, the task "
            "graphs of the project and the notes the orchestrator wrote. Anything else from the earlier conversation is gone. Where a user message and "
            "the notes disagree, the later user message wins; ask the user nothing that these already answer.\n\n**" + STATE_LINES[chat["state"]] + "**\n")
    room = max(0, limit - len(head) - 40)
    total = min(total, room // 2)                 # the user's messages never take more than half the room, one message never more than all of it
    per_message = min(per_message, max(200, total // 2))   # so the first and the newest message always fit together
    late = [r for r in chat["reports"] if r["ms"] is not None and r["ms"] > notes_ms]
    size = sum(len(r["text"]) for r in late)
    if size > min(report_chars, room // 3):
        WHY[0] = f"{len(late)} subagent report(s) of {size} characters arrived after the notes and do not fit the {min(report_chars, room // 3)} allowed"
        return STALE_EXIT, ""
    user_part = "## The user's messages (word for word, oldest first)\n\n" + user_block(messages, per_message, total) + "\n"
    report_part = ""
    if late:
        report_part = ("## Subagent reports that arrived after the notes were written (word for word; the notes do not have them)\n\n"
                       + "\n\n".join(f"### Report from {r['agent']} session {r['child']} ({r['state'] or '?'})\n\n{r['text']}" for r in late) + "\n")
    if len(user_part) + len(report_part) > room * 0.8:
        WHY[0] = "the user's messages and the late reports leave too little room for the notes"
        return STALE_EXIT, ""
    notes_head = "## Notes written by the orchestrator (word for word)\n\n"
    notes_part = notes_head + cut_notes(notes, max(0, room - len(user_part) - len(report_part) - len(notes_head) - 4)) + "\n"
    left = room - len(user_part) - len(report_part) - len(notes_part) - 2
    children = chat["children"]
    sections = [("Work folder", str(work)),
                ("Sessions this chat started (id, agent, state, what it was asked)", subagents_text(children, SUBAGENT_CHARS) if children else None),
                ("Task graphs in the project", graphs_text(work, GRAPH_CHARS) or None)]
    parts = [user_part] + ([report_part] if report_part else [])
    for title, body in sections:   # what is left after the user's messages, the reports and the notes goes to these, in order
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
            sp.add_argument("--limit", type=int, default=None, help=f"most characters in the summary (default {LIMIT}, {CHAT_LIMIT} for a chat)")
            kind = sp.add_mutually_exclusive_group()
            kind.add_argument("--chat", action="store_true", help="the summary of a main chat: the user's own messages word for word, the chat's subagent sessions, the task graphs, then the notes")
            kind.add_argument("--lane", action="store_true", help="the summary of a lane (the default for a session that has a parent; without either flag a session with no parent is built as a chat)")
            sp.add_argument("--user-message-chars", type=int, default=USER_MESSAGE_CHARS, help=f"chat: most characters of one user message (the middle is cut; default {USER_MESSAGE_CHARS})")
            sp.add_argument("--report-chars", type=int, default=REPORT_CHARS, help=f"chat: most characters of subagent reports that arrived after the notes (more means exit 3; default {REPORT_CHARS})")
            sp.add_argument("--user-chars", type=int, default=USER_CHARS, help=f"chat: most characters of user messages in all (the first and the newest are kept; default {USER_CHARS})")
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
            if args.limit is not None and args.limit < MIN_LIMIT:
                raise HandoffError(f"--limit must be at least {MIN_LIMIT}")
            code, text = build(args.session, args.dir, db_path=args.db, notes_dir=args.notes_dir, limit=args.limit,
                               max_age=args.max_age, allow_stale=args.allow_stale, newer_than=args.newer_than,
                               chat=True if args.chat else False if args.lane else None,
                               user_message_chars=max(200, args.user_message_chars), user_chars=max(1000, args.user_chars), report_chars=max(0, args.report_chars))
            if code:
                print(("handoff: notes missing or stale" + (f" ({WHY[0]})" if WHY[0] else "")) if code == STALE_EXIT
                      else f"handoff: {WHY[0]}" if code == DB_EXIT else "handoff: the task (first user message) could not be read", file=sys.stderr)
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
