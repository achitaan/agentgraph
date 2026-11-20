"""One session's recent chat as capped plain text, for the "chat on every agent" panel.

    python3 -m agentgraph.chatfeed --session ID [--limit N] [--json | --text] [--chars N] [--demo]
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
    if args.json:
        print(json.dumps(feed, allow_nan=False))
    else:
        print(digest(args.session, args.chars, feed=feed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
