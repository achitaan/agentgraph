"""One session's recent chat as capped plain text, for the "chat on every agent" panel.

    python3 -m agentgraph.chatfeed --session ID [--limit N] [--json | --text] [--chars N] [--demo] [--side]
    (bin/agentgraph chat ...)

What is read, and what never is. OpenCode 2.3.2 keeps a session's transcript in `session_message`
(`data` is one JSON document per message, up to hundreds of KB). Only these paths of `data` are ever
selected, and the text is cut in SQL (`json_each`, `json_extract`, `substr`), so the rest never reaches
Python: for an assistant message each `content[]` item's `type`, `text` (reasoning and text items), `name`,
`state.status` and `time.created`; for a user message `text`. NEVER selected: `state.input` (a tool's
input), `state.content` (a tool's output), `native` (the model's own reasoning state), `metadata`, and the
text of a `synthetic` message (the host's own notes: shell output, a sub-agent's result). A synthetic message
is shown as a bare `note`. `tests/test_chatfeed.py` proves it with a marker in each of those places and by
looking at every statement the connection runs.

One exception, for the main chat only and only through `hub_activity`: the "what is it doing now" detail of
the hub reads a few named `state.input` fields (`command`, `url`, `pattern`, `query`, `code`, `id`, `agent`,
`subagent_type`, `path`, `description`), each cut in SQL, and shows one short masked line of them (a command's
first line, a URL's host and path, a file's path). Never the `content` of a write or edit, a prompt, or a
tool's output.

A message sent with delivery "steer" lands as a `user` message once the session takes it up; until then it
sits in `session_inbox` (type `user`) with an `enqueued_seq` above the session's newest message, and is shown
as a `user` item with `pending: true`.

Plain text only, because a terminal prints it: ANSI/OSC escape sequences, control characters (newline and tab
stay), bidirectional overrides and lone surrogates are removed (agentgraph/view.py cleans per cell for drawing;
no repo helper strips a string, so this module has its own). Obvious secrets are masked (`mask_secrets`):
that is a SECOND line of defence only, a pattern list cannot know every secret; the first is that tool input
and output are never read.

Read-only connection, one second timeout, like the other readers. A missing, locked or unreadable database
gives an empty feed with a `warning`, never a traceback.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .sources import DB_PATH

DEFAULT_LIMIT = 40        # messages (an assistant message holds several items)
MAX_LIMIT = 200
TEXT_CAP = 2000           # characters in one item's text
TOTAL_CAP = 24000         # characters in the whole feed
NOTE_CAP = 160            # the "what is it doing now" line
ITEM_ROWS = 2000          # a bound on rows read for one feed
TOOL_NAME_CAP = 64
TIMEOUT = 1.0
OLDER = "... (older chat left out)"

SESSION_ID = re.compile(r"ses_[A-Za-z0-9]{1,64}")
KINDS = ("reasoning", "text", "tool", "user", "note")


def valid_session(value) -> bool:
    """Whether `value` looks like an OpenCode session id (`ses_` and letters or digits)."""
    return isinstance(value, str) and SESSION_ID.fullmatch(value) is not None


# --- plain text and secrets ------------------------------------------------------

_ESCAPES = re.compile(
    r"\x1b\][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)"      # OSC: ESC ] ... BEL or ST
    r"|\x1b[P^_X][^\x1b\x9c]*(?:\x1b\\|\x9c|$)"           # DCS, PM, APC, SOS strings
    r"|\x1b\[[0-?]*[ -/]*[@-~]"                           # CSI
    r"|\x1b[ -/]*[0-~]"                                   # two-character escapes
    r"|\x9b[0-?]*[ -/]*[@-~]"                             # 8-bit CSI
)
_CONTROLS = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_SURROGATES = re.compile("[\ud800-\udfff]")


def plain(text) -> str:
    """`text` as plain text: escape sequences and control characters removed, newline and tab kept."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n")
    text = _ESCAPES.sub("", text)
    text = _SURROGATES.sub("\ufffd", text)
    return _CONTROLS.sub("", text)


_MASK = "[masked]"
_SECRETS = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S),
     "[masked key]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1" + _MASK),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), _MASK),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), _MASK),
    (re.compile(r"\bxox[abprso]-[A-Za-z0-9-]{10,}"), _MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _MASK),
    (re.compile(r"(?i)([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[\"']?[ \t]*[:=][ \t]*[\"']?)"
                r"[^\s\"',;&]{8,}"), r"\1" + _MASK),
)


def mask_secrets(text: str) -> str:
    """Replace obvious secrets: a PEM private key block, a long token after `Bearer`, `sk-`/`ghp_`/`xox`/`AKIA`
    style keys, and the value of `password=`/`token=`/`api_key=`/`secret=` (8 characters or more)."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def clean(text) -> str:
    return mask_secrets(plain(text))


def first_line(text, cap: int = NOTE_CAP) -> str | None:
    """The first non-empty line of `text`, cleaned and cut to `cap` characters, or None."""
    for line in clean(text).split("\n"):
        line = line.strip()
        if line:
            return line[:cap]
    return None


# --- reading ------------------------------------------------------------------------

_TYPES = "('assistant', 'user', 'synthetic')"
_CUTOFF = (f"SELECT seq FROM session_message WHERE session_id = ? AND type IN {_TYPES} "
           "ORDER BY seq DESC LIMIT 1 OFFSET ?")
# Every column read from `data` is named here. Nothing else of it is selected.
_ITEMS = (
    "SELECT m.seq, m.time_created, c.key, json_extract(m.data, c.fullkey || '.type'), "
    "substr(json_extract(m.data, c.fullkey || '.text'), 1, ?1), json_extract(m.data, c.fullkey || '.name'), "
    "json_extract(m.data, c.fullkey || '.state.status'), json_extract(m.data, c.fullkey || '.time.created') "
    "FROM session_message m, json_each(m.data, '$.content') c "
    "WHERE m.session_id = ?2 AND m.type = 'assistant' AND m.seq > ?3 "
    "UNION ALL "
    "SELECT seq, time_created, 0, 'user', substr(json_extract(data, '$.text'), 1, ?1), NULL, NULL, NULL "
    "FROM session_message WHERE session_id = ?2 AND type = 'user' AND seq > ?3 "
    "UNION ALL "
    "SELECT seq, time_created, 0, 'note', NULL, NULL, NULL, NULL "
    "FROM session_message WHERE session_id = ?2 AND type = 'synthetic' AND seq > ?3 "
    "ORDER BY 1 DESC, 3 DESC LIMIT ?4"
)
_PENDING = (
    "SELECT time_created, substr(json_extract(payload, '$.text'), 1, ?2), delivery FROM session_inbox "
    "WHERE session_id = ?1 AND type = 'user' AND enqueued_seq > "
    "COALESCE((SELECT MAX(seq) FROM session_message WHERE session_id = ?1), -1) "
    "ORDER BY enqueued_seq DESC LIMIT 20"
)
_SESSION = "SELECT agent, time_busy, time_idle FROM session_v2 WHERE id = ?"
_LAST = (
    "SELECT json_extract(m.data, c.fullkey || '.type'), json_extract(m.data, c.fullkey || '.name'), "
    "substr(json_extract(m.data, c.fullkey || '.text'), 1, ?2), json_extract(m.data, c.fullkey || '.time.created'), "
    "m.time_created FROM (SELECT time_created, data FROM session_message WHERE session_id = ?1 "
    "AND type = 'assistant' AND json_array_length(data, '$.content') > 0 ORDER BY seq DESC LIMIT 1) m, "
    "json_each(m.data, '$.content') c "
    "WHERE json_extract(m.data, c.fullkey || '.type') IN ('reasoning', 'text', 'tool') ORDER BY c.key DESC LIMIT 1"
)


def _connection(db_path, timeout: float) -> sqlite3.Connection:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    conn.text_factory = lambda raw: raw.decode("utf-8", "replace")
    return conn


def _seconds(*values) -> float | None:
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return round(value / 1000.0, 3)
    return None


def _word(value, cap: int = TOOL_NAME_CAP) -> str | None:
    """A tool name or status: letters, digits and a few signs only."""
    text = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value or ""))[:cap]
    return text or None


def _empty(session_id, warning=None, agent=None, busy=False) -> dict:
    return {"session": session_id if isinstance(session_id, str) else None, "agent": agent, "busy": busy,
            "items": [], "truncated": False, "more": False, "warning": warning}


def _clamp(limit) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, limit))


def _item(kind, when, *, text=None, name=None, status=None, cut=False, pending=False) -> dict | None:
    if kind in ("reasoning", "text", "user"):
        body = clean(text).strip()
        if not body:
            return None
        if len(body) > TEXT_CAP:
            body, cut = body[:TEXT_CAP], True
        item = {"kind": kind, "time": when, "text": body, "cut": cut}
        if pending:
            item["pending"] = True
        return item
    if kind == "tool":
        return {"kind": "tool", "time": when, "name": _word(name) or "tool", "status": _word(status, 20)}
    return {"kind": "note", "time": when}


def _size(item: dict) -> int:
    return len(item.get("text") or item.get("name") or "") + 1


def read_chat(session_id, *, limit=DEFAULT_LIMIT, db_path=DB_PATH, timeout: float = TIMEOUT) -> dict:
    """The latest `limit` messages (default 40, at most 200) of a session as a feed:
    {session, agent, busy, items (oldest first), truncated, more, warning}.
    An item is {kind, time (unix seconds), ...}: `reasoning`, `text`, `user` carry `text` (at most 2000
    characters; `cut` says it was longer; a `user` item still waiting in the inbox has `pending: true`);
    `tool` carries `name` and `status`; `note` is a host note, with no text. `truncated`: some text was cut
    or the whole-feed cap (about 24000 characters) dropped older items; `more`: older messages exist.
    Never raises: a bad id, a missing, locked or unreadable database gives an empty feed and a `warning`."""
    if not valid_session(session_id):
        return _empty(None, "chat: not a session id")
    path = Path(db_path)
    if not path.exists():
        return _empty(session_id, f"chat: database not found: {path.name}")
    limit = _clamp(limit)
    try:
        conn = _connection(path, timeout)
    except Exception as exc:  # noqa: BLE001
        return _empty(session_id, f"chat: cannot open the database: {clean(str(exc))[:120]}")
    try:
        row = conn.execute(_SESSION, (session_id,)).fetchone()
        agent, busy = (None, False)
        if row is not None:
            agent = _word(row[0]) if row[0] else None
            busy = row[1] is not None and (row[2] is None or row[1] > row[2])
        cutoff = conn.execute(_CUTOFF, (session_id, limit)).fetchone()
        rows = conn.execute(_ITEMS, (TEXT_CAP + 1, session_id, cutoff[0] if cutoff else -1, ITEM_ROWS)).fetchall()
        try:
            pending = conn.execute(_PENDING, (session_id, TEXT_CAP + 1)).fetchall()
        except sqlite3.Error:  # an older database has no inbox
            pending = []
    except Exception as exc:  # noqa: BLE001
        return _empty(session_id, f"chat: cannot read the database: {clean(str(exc))[:120]}")
    finally:
        conn.close()

    feed = _empty(session_id, None if row is not None else "chat: session not found", agent, bool(busy))
    feed["more"] = cutoff is not None
    newest_first = []  # items, newest first
    for time_created, text, delivery in pending:
        item = _item("user", _seconds(time_created), text=text, pending=True)
        if item is not None:
            item["delivery"] = _word(delivery, 10)
            newest_first.append(item)
    for seq, created, _key, kind, text, name, status, part_time in rows:
        when = _seconds(part_time, created)
        if kind in ("reasoning", "text", "user"):
            item = _item(kind, when, text=text, cut=isinstance(text, str) and len(text) > TEXT_CAP)
        elif kind == "tool":
            item = _item("tool", when, name=name, status=status)
        elif kind == "note":
            item = _item("note", when)
        else:
            continue  # a content type this panel does not show
        if item is not None:
            newest_first.append(item)
    if len(rows) >= ITEM_ROWS:
        feed["truncated"] = feed["more"] = True

    kept, total = [], 0
    for item in newest_first:
        total += _size(item)
        if total > TOTAL_CAP and kept:
            feed["truncated"] = feed["more"] = True
            break
        kept.append(item)
    feed["truncated"] = feed["truncated"] or any(item.get("cut") for item in kept)
    feed["items"] = kept[::-1]
    return feed


def activities(session_ids, *, db_path=DB_PATH, timeout: float = TIMEOUT) -> dict[str, dict]:
    """For each session that has one, what it is doing now: {kind: "tool"|"reasoning"|"text", tool: name or
    None, text: first line (160 characters) or None, time}, from the last item of its newest assistant message.
    One short indexed query per session; text, names and times only, as in `read_chat`. {} on any failure."""
    ids = [sid for sid in dict.fromkeys(session_ids) if valid_session(sid)]
    found: dict[str, dict] = {}
    if not ids or not Path(db_path).exists():
        return found
    try:
        conn = _connection(db_path, timeout)
    except Exception:  # noqa: BLE001
        return found
    try:
        for sid in ids:
            row = conn.execute(_LAST, (sid, NOTE_CAP * 4)).fetchone()
            if row is None:
                continue
            kind, name, text, part_time, created = row
            if kind == "tool":
                found[sid] = {"kind": "tool", "tool": _word(name) or "tool", "text": None,
                              "time": _seconds(part_time, created)}
            elif kind in ("reasoning", "text"):
                found[sid] = {"kind": kind, "tool": None, "text": first_line(text),
                              "time": _seconds(part_time, created)}
    except Exception:  # noqa: BLE001
        return found
    finally:
        conn.close()
    return found


# --- the main chat's live detail (zone, target, status, a short trail) ------------------------------
# The zone idea (every tool call normalised to one activity zone) and the short trail of recent actions come
# from AgentMove (github.com/FoothillSolutions/agent-move, MIT); the table and code here are our own.

ZONES = ("files", "terminal", "search", "web", "thinking", "messaging", "tasks", "spawn", "tools")
TARGET_CAP = 120
TRAIL = 5                 # recent tool calls kept besides the current one
TRAIL_MESSAGES = 6        # assistant messages looked at for them
_ZONE_NAMES = {
    "read": "files", "write": "files", "edit": "files", "multiedit": "files", "glob": "files", "patch": "files",
    "apply_patch": "files", "applypatch": "files", "list": "files", "ls": "files",
    "shell": "terminal", "bash": "terminal", "terminal": "terminal",
    "grep": "search", "websearch": "search", "codesearch": "search",
    "webfetch": "web",
    "question": "thinking", "skill": "thinking",
    "subagent": "spawn", "task": "spawn",
}
_ZONE_TOKENS = (  # first rule with a matching word of the name wins; MCP tools are `server_tool`
    ("messaging", {"message", "messages", "reply", "replies"}),
    ("tasks", {"todo", "todos", "todowrite", "todoread"}),
    ("thinking", {"plan", "think", "thinking"}),
    ("web", {"browser", "playwright", "puppeteer", "webfetch", "fetch", "navigate"}),
    ("search", {"search", "grep"}),
)
_NAMESPACE_ZONES = {"browser": "web", "context7": "search", "gh_grep": "search"}
_CALL = re.compile(r"\btools\.([A-Za-z_$][\w$]*)\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*[\"']([^\"']+)[\"']\s*\])")


def _call_of(code) -> tuple[str, str] | None:
    """The first `tools.<namespace>.<name>(...)` (or `["name"]`) of an `execute` tool's code."""
    match = _CALL.search(code) if isinstance(code, str) else None
    return (match.group(1), match.group(2) or match.group(3)) if match else None


def zone_of(tool, code=None) -> str:
    """The activity zone of a tool name (lower-cased; `execute` by the first namespace its code calls)."""
    name = str(tool or "").strip().lower()
    if name == "execute":
        call = _call_of(code)
        return _NAMESPACE_ZONES.get(call[0].lower(), "tools") if call else "tools"
    if name in _ZONE_NAMES:
        return _ZONE_NAMES[name]
    words = set(re.split(r"[^a-z0-9]+", name))
    if any(word.startswith(("browser", "playwright")) for word in words):
        return "web"
    for zone, wanted in _ZONE_TOKENS:
        if words & wanted:
            return zone
    return "tools"


_VALUE = r"(\"[^\"]*(?:\"|$)|'[^']*(?:'|$)|[^\s\"']+)"  # a quoted value (spaces and = allowed; an unclosed quote runs to the end) or a bare word
_NAME = r"[A-Z0-9_.-]"
_TARGET_MASKS = (
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*).*"), r"\1" + _MASK),
    (re.compile(r"(?i)\b(bearer\s+)[^\s\"']+"), r"\1" + _MASK),
    (re.compile(r"(?i)\b((?:set-)?cookie\s*:\s*)[^\"'\n]*"), r"\1" + _MASK),  # all of it, to the closing quote or the line's end
    # a name with key, token ... in it, or with pwd in it and more around it (MYSQL_PWD; a bare PWD is a folder)
    (re.compile(r"(?i)\b(" + _NAME + r"*(?:key|token|secret|passw(?:or)?d|credential|auth)" + _NAME + r"*|"
                + _NAME + r"+pwd" + _NAME + r"*|pwd" + _NAME + r"+)[ \t]*=[ \t]*" + _VALUE), lambda m: m.group(0)[:m.start(2) - m.start()] + _MASK),
    (re.compile(r"(?i)(--?[\w-]*(?:password|passwd|token|secret|api[-_]?key|auth)[\w-]*[ =][ \t]*)" + _VALUE), r"\1" + _MASK),
    (re.compile(r"(?<![\w-])(-[A-Za-z]*[uU][ ]*|--(?:proxy-)?user[ =])(\"[^\"]*:[^\"]*(?:\"|$)|'[^']*:[^']*(?:'|$)|[^\s\"']*:[^\s\"']*)"), r"\1" + _MASK),  # user:password
    (re.compile(r"(?<![\w-])(--cookie[ =])" + _VALUE), r"\1" + _MASK),
    (re.compile(r"(\w+://)[^/\s]+@"), r"\1" + _MASK + "@"),  # up to the last @ before the path
)
# `-p` is a password only after these clients, wherever they stand in the line (elsewhere it is --parents, --preserve,
# a port ...); `-b` is a cookie only for curl (git checkout -b is a branch).
_CLIENT = r"(?:^|[\s;&|/\"'(`])(%s)(?=\s)"  # also inside quotes, $( and backticks
_P_CLIENTS = re.compile(_CLIENT % "mysql|mysqldump|mysqladmin|mariadb|mariadb-dump|psql|pg_dump|pg_dumpall|pg_restore|sshpass")
_CURL = re.compile(_CLIENT % "curl")
_P_ATTACHED = re.compile(r"(?<=\s)(-p)(?=[^\s-])" + _VALUE)
_P_SEPARATE = re.compile(r"(?<=\s)(-p[ ]*)" + _VALUE)
_B_COOKIE = re.compile(r"(?<![\w-])(-[A-Za-z]*b[ ]*)" + _VALUE)  # -b, or combined as in -sSb


def _mask_flags(line: str) -> str:
    client = _P_CLIENTS.search(line)
    if client:
        head, tail = line[:client.start(1)], line[client.start(1):]
        if client.group(1) == "sshpass":
            tail = _P_SEPARATE.sub(lambda m: m.group(1) + _MASK, tail, count=1)
        else:
            tail = _P_ATTACHED.sub(lambda m: m.group(1) + _MASK, tail)
        line = head + tail
    if _CURL.search(line):
        line = _B_COOKIE.sub(lambda m: m.group(1) + _MASK, line)
    return line


def _tidy(text, cap: int = TARGET_CAP) -> str | None:
    """One short plain line: secrets masked first, whitespace collapsed, then cut to `cap` with an ellipsis."""
    line = _mask_flags(mask_secrets(plain(text)))
    for pattern, replacement in _TARGET_MASKS:
        line = pattern.sub(replacement, line)
    line = re.sub(r"\s+", " ", line).strip()
    if not line:
        return None
    return line if len(line) <= cap else line[:cap - 1].rstrip() + "\u2026"


def _tilde(text: str) -> str:
    """The home folder as `~`, where it is a whole path (after file:// or not after a name character; followed by `/`,
    the end, a space or a quote or shell mark)."""
    home = str(Path.home())
    if len(home) <= 1:
        return text
    return re.sub(r"(?:(?<=file://)|(?<![\w/.-]))" + re.escape(home) + r"(?=/|$|[\s\"';&|)\]}>,:`])", "~", text)


def _site(url) -> str | None:
    """A URL as host and path only: no scheme, user:password, query string or fragment."""
    text = str(url or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text if "://" in text else "//" + text)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    path = "" if parts.path == "/" else parts.path
    return host + (f":{port}" if port else "") + path


def target_of(tool, first, second=None) -> str | None:
    """One short line saying what a tool call acts on, or None. `first` is the call's `command`, `url`,
    `path`, `pattern`, `query`, `code`, `id` or `agent` (whichever it has); `second` its `description`."""
    name = str(tool or "").strip().lower()
    first = first if isinstance(first, str) else None
    second = second if isinstance(second, str) else None
    if not first and not (name in ("subagent", "task") and second):
        return None
    if name == "webfetch":
        text = _site(first)
    elif name in ("shell", "bash", "terminal"):
        text = next((line.strip() for line in plain(first).split("\n") if line.strip()), None)
        if text:  # a query string or fragment can hold a secret: cut every URL at the first ? or #
            text = re.sub(r"(\w+://[^\s\"'?#]*)[?#][^\s\"']*", r"\1", text[:4 * TARGET_CAP])
            # the same for an address without a scheme: a host name, localhost or an IPv4 address, with or without a path
            text = re.sub(r"((?<![\w.-])(?:[\w.-]+\.[A-Za-z]{2,}|localhost|\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?(?:/[^\s\"'?#]*)?)[?#][^\s\"']*", r"\1", text)
    elif name in ("read", "write", "edit", "multiedit", "grep", "glob", "websearch", "codesearch"):
        text = plain(first)
    elif name in ("subagent", "task"):
        who = (first or "").strip()
        text = f"{who}: {second}" if who and second else (who or second)
    elif name == "skill":
        text = first
    elif name == "execute":
        call = _call_of(first)
        text = f"{call[0]}.{call[1]}" if call else None
    else:
        return None
    return _tidy(_tilde(text[:4 * TARGET_CAP]), TARGET_CAP) if text else None


def _tool_sql(detail: bool) -> str:
    keys = ("command", "url", "pattern", "query", "code", "id", "agent", "subagent_type", "path")
    field = "json_extract(c.value, '$.state.input.%s')"
    if detail:
        first = "substr(COALESCE(" + ", ".join(field % key for key in keys) + "), 1, 2000)"
        second = "substr(" + field % "description" + ", 1, 400)"
    else:  # the zone of an `execute` needs its code; every other tool is read by name and status alone
        first = "CASE WHEN json_extract(c.value, '$.name') = 'execute' THEN substr(" + field % "code" + ", 1, 2000) END"
        second = "NULL"
    return (
        "SELECT json_extract(c.value, '$.name'), json_extract(c.value, '$.state.status'), "
        "json_extract(c.value, '$.time.created'), json_extract(c.value, '$.time.ran'), "
        "json_extract(c.value, '$.time.completed'), m.time_created, " + first + ", " + second + " "
        "FROM (SELECT seq, time_created, data FROM session_message WHERE session_id = ?1 AND type = 'assistant' "
        "ORDER BY seq DESC LIMIT ?2) m, json_each(m.data, '$.content') c "
        "WHERE json_extract(c.value, '$.type') = 'tool' ORDER BY m.seq DESC, c.key DESC LIMIT ?3"
    )


_STATUS = {"streaming": "running", "running": "running", "completed": "completed", "error": "error"}


def _call_row(row, detail: bool) -> dict:
    name, status, created, ran, completed, message_time, first, second = row
    zone = zone_of(name, first)
    done = None
    if isinstance(ran, (int, float)) and isinstance(completed, (int, float)) and completed >= ran > 0:
        done = round((completed - ran) / 1000.0, 3)
    return {"zone": zone, "tool": _word(name) or "tool", "target": target_of(name, first, second) if detail else None,
            "status": _STATUS.get(status), "time": _seconds(created, message_time), "seconds": done}


def hub_activity(session_id, base: dict, *, detail: bool = True, db_path=DB_PATH, timeout: float = TIMEOUT) -> dict:
    """`base` (one `activities()` entry of the hub's session) with `zone`, `status` and, when `detail`,
    `target` and `recent`. `zone` is `thinking` for reasoning and absent for text. With `detail` False no
    `state.input` is read except an `execute` tool's code (for its zone), and `target` and `recent` are left out.
    Any failure returns `base` unchanged."""
    if not isinstance(base, dict) or not valid_session(session_id) or not Path(db_path).exists():
        return base
    try:
        conn = _connection(db_path, timeout)
    except Exception:  # noqa: BLE001
        return base
    try:
        rows = conn.execute(_tool_sql(detail), (session_id, TRAIL_MESSAGES, TRAIL + 1 if detail else 1)).fetchall()
    except Exception:  # noqa: BLE001
        return base
    finally:
        conn.close()
    try:
        calls = [_call_row(row, detail) for row in rows]
        found = dict(base)
        if base.get("kind") == "tool":
            now = calls[0] if calls and calls[0]["tool"] == base.get("tool") else None
            found["zone"] = now["zone"] if now else zone_of(base.get("tool"))
            found["status"] = now["status"] if now else None
            if detail:
                found["target"] = now["target"] if now else None
                found["recent"] = calls[1:TRAIL + 1] if now else []
        else:
            if base.get("kind") == "reasoning":
                found["zone"] = "thinking"
            found["status"] = None
            if detail:
                found["target"] = None
                found["recent"] = calls[:TRAIL]
        return found
    except Exception:  # noqa: BLE001
        return base


def activity_of(item: dict) -> dict | None:
    """The `activity` for a feed's last item (used for the demo, which has no database)."""
    kind = item.get("kind")
    if kind == "tool":
        return {"kind": "tool", "tool": item.get("name"), "text": None, "time": item.get("time")}
    if kind in ("reasoning", "text"):
        return {"kind": kind, "tool": None, "text": first_line(item.get("text")), "time": item.get("time")}
    return None


def demo_feed(session_id, now: float | None = None) -> dict | None:
    """The built-in example chat of a demo session (agentgraph/demo.py), or None for another id."""
    from .demo import demo_chat
    return demo_chat(session_id, now)


# --- text ----------------------------------------------------------------------------

def _lines(feed: dict) -> list[str]:
    out = []
    for item in feed["items"]:
        kind = item["kind"]
        if kind == "tool":
            out.append(f"[tool] {item['name']}" + (f" ({item['status']})" if item.get("status") else ""))
        elif kind == "note":
            out.append("[note]")
        else:
            label = {"user": "user", "reasoning": "thinking", "text": "says"}[kind]
            if item.get("pending"):
                label += " (waiting)"
            text = item["text"] + (" ..." if item.get("cut") else "")
            out.append(f"[{label}] {text}")
    return out


def digest(session_id, chars: int = TOTAL_CAP, *, limit=DEFAULT_LIMIT, db_path=DB_PATH,
           feed: dict | None = None) -> str:
    """The feed as one plain-text block of at most `chars` characters, for a small model's context. When
    it is longer the newest content is kept and a line says older chat is left out."""
    feed = feed if feed is not None else read_chat(session_id, limit=limit, db_path=db_path)
    chars = max(80, int(chars))
    head = f"session {feed['session']}"
    if feed["agent"]:
        head += f" ({feed['agent']}{', working' if feed['busy'] else ''})"
    if feed.get("warning"):
        head += f"\n{feed['warning']}"
    lines = _lines(feed)
    budget = chars - len(head) - 1
    kept, used = [], 0  # newest first
    for line in reversed(lines):
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    omitted = len(kept) < len(lines) or feed["more"]
    if omitted:
        while kept and used + len(OLDER) + 1 > budget:
            used -= len(kept.pop()) + 1  # the oldest kept line makes room for the marker
        if not kept and lines:  # even the newest line is too long: keep its end
            kept = [lines[-1][-max(1, budget - len(OLDER) - 1):]]
    body = ([OLDER] if omitted else []) + kept[::-1]
    return (head + ("\n" + "\n".join(body) if body else ""))[:chars]


# --- the command ---------------------------------------------------------------------

def _count(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph chat", description="One session's recent chat as plain text "
                                     "(reasoning, messages, tool names; never tool input or output).")
    parser.add_argument("--session", metavar="ID", required=True, help="the session (ses_...)")
    parser.add_argument("--limit", type=_count, default=DEFAULT_LIMIT, help=f"latest messages (default {DEFAULT_LIMIT}, at most {MAX_LIMIT})")
    form = parser.add_mutually_exclusive_group()
    form.add_argument("--json", action="store_true", help="the feed as one line of JSON")
    form.add_argument("--text", action="store_true", help="plain text (the default)")
    parser.add_argument("--chars", type=_count, default=TOTAL_CAP, help=f"longest text output (default {TOTAL_CAP})")
    parser.add_argument("--demo", action="store_true", help="the built-in example chat (ses_root, ses_g, ses_a ...)")
    parser.add_argument("--side", action="store_true", help="the side chat's own session: each question cut before its "
                        "brief, no reasoning (as GET /api/chat shows it)")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not valid_session(args.session):
        print("agentgraph chat: --session must look like ses_ followed by letters and digits", file=sys.stderr)
        return 2
    if args.demo:
        feed = demo_feed(args.session)
        if feed is None:
            print("agentgraph chat: no such demo session", file=sys.stderr)
            return 1
    else:
        feed = read_chat(args.session, limit=args.limit, db_path=args.db)
    if args.side:
        from .sidechat import side_feed  # sidechat imports this module
        feed = side_feed(feed)
    if args.json:
        print(json.dumps(feed, allow_nan=False))
    else:
        print(digest(args.session, args.chars, feed=feed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
