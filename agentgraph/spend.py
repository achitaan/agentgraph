"""A spend limit for the project, and the figures the view reads it against.

`agentgraph spend limit AMOUNT [--warn AMOUNT]` keeps one limit in dollars for the project, `spend limit --off` takes it
away, `spend show [--json]` says what is kept and what the tree has cost.

**The file.** `<project>/.opencode/spend.json`: `{"version": 1, "limit": 50.0, "warn": 45.0}`, nothing else. It is the
project's own (the folder `.opencode/` the task graphs also live in); no setting of OpenCode or the plugin is changed and
no limit is enforced: the view only reads the spend against it. A write is staged beside the file and renamed, under an
`flock` on `spend.lock` next to it; a symlink where a file or folder should be is refused.

**The numbers.** An amount is a positive number of dollars, at most 1,000,000, kept to the cent. `warn` is below `limit`
(strictly) and defaults to 90% of it; changing the limit without `--warn` sets `warn` to 90% again. A refusal is one line
(`SpendError`) and nothing is written.

**The snapshot's `spend`** (`annotate`): `{"limit": number|null, "warn": number|null, "rate": number|null, "chat": number,
"workers": number}`. `chat` is what the root session has cost, `workers` what every other session of the tree has cost
(the two add up to `totals.cost`). `rate` is dollars an hour over the last 30 minutes: the cost of the tree's assistant
messages created in that time (`session_message.time_created`, `data.cost`) times two; `null` when the database has no
such table or column. It is one indexed read of the messages of the last half hour; nothing else is read. Never raises.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import time

try:  # `python agentgraph/spend.py` runs it as a script, outside the package
    from . import osfs
except ImportError:
    from agentgraph import osfs

FILE = "spend.json"
LOCK = "spend.lock"
LIMIT_MAX = 1_000_000.0
WARN_SHARE = 0.9
RATE_WINDOW = 1800.0  # seconds
READ_LIMIT = 4096
_NUMBER = re.compile(r"-?\d{1,12}(?:\.\d{1,6})?")


class SpendError(Exception):
    """A refused request; the message says why and nothing was changed."""


def folder(project=None) -> Path:
    return Path(project if project is not None else Path.cwd()) / ".opencode"


def path(project=None) -> Path:
    return folder(project) / FILE


def amount(value, what: str = "the limit") -> float:
    """`value` (a number or the text of one) as a positive amount of dollars kept to the cent, or a refusal."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SpendError(f"{what} must be a number of dollars")
    if isinstance(value, str):
        text = value.strip().lstrip("$").strip()
        value = float(text) if _NUMBER.fullmatch(text) else math.nan
    if isinstance(value, int):  # a huge integer does not fit a float: compare it as it is
        value = max(-1, min(int(LIMIT_MAX) + 1, value))
    value = float(value)
    if not math.isfinite(value):
        raise SpendError(f"{what} must be a number of dollars")
    if value <= 0:
        raise SpendError(f"{what} must be more than $0")
    if value > LIMIT_MAX:
        raise SpendError(f"{what} must be at most ${LIMIT_MAX:,.0f}")
    cents = round(value, 2)
    if cents <= 0:
        raise SpendError(f"{what} must be at least $0.01")
    return cents


def _is_dir(target: Path) -> bool:
    info = os.lstat(target)
    return stat.S_ISDIR(info.st_mode) and not osfs.is_link(info)  # a junction is a directory to S_ISDIR


def _plain_dir(target: Path, *, create: bool = False) -> bool:
    try:
        return _is_dir(target)
    except FileNotFoundError:
        if not create:
            return False
        try:
            os.makedirs(target, 0o700, exist_ok=True)
        except OSError:
            return False
        return _is_dir(target)
    except OSError:
        return False


def _read(project=None):
    """(limit, warn) kept for the project: (None, None) when there is no file. A file that is not ours (unreadable,
    not JSON, a figure that does not fit) raises SpendError without saying what it holds."""
    target = path(project)
    try:
        fd = osfs.open(target, os.O_RDONLY | osfs.O_NOFOLLOW | osfs.O_NONBLOCK)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise SpendError(f"{target} cannot be read ({exc.strerror or 'refused'})") from None
    try:
        info = osfs.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > READ_LIMIT:
            raise SpendError(f"{target} is not a plain spend file")
        raw = os.read(fd, READ_LIMIT + 1)
    finally:
        osfs.close(fd)
    try:
        data = json.loads(raw.decode("utf-8"))
        limit = amount(data["limit"])
        warn = amount(data["warn"], "warn")
        if not isinstance(data, dict) or data.get("version") != 1 or warn >= limit:
            raise ValueError
    except (ValueError, KeyError, TypeError, SpendError, UnicodeDecodeError):
        raise SpendError(f"{target} is not a spend file this version wrote; set the limit again or remove it") from None
    return limit, warn


@contextmanager
def _locked(project=None):
    where = folder(project)
    if not _plain_dir(where, create=True):
        raise SpendError(f"{where} is not a plain folder")
    fd = osfs.open(where / LOCK, os.O_RDWR | os.O_CREAT | osfs.O_NOFOLLOW, 0o600)
    try:
        osfs.lock(fd)
        yield
    finally:
        osfs.close(fd)


def _stage(project, limit: float, warn: float) -> None:
    target = path(project)
    temporary = target.parent / f".{FILE}.{secrets.token_hex(6)}.tmp"
    fd = osfs.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | osfs.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"version": 1, "limit": limit, "warn": warn}, indent=1) + "\n")
            stream.flush()
            osfs.fsync(stream.fileno())
        osfs.replace(temporary, target)
    except BaseException:
        try:
            osfs.unlink(temporary)
        except OSError:
            pass
        raise


def _previous(project):
    try:
        limit, warn = _read(project)
    except SpendError:
        return None, None  # a file that is not ours is replaced; there is nothing to put back
    return limit, warn


def show(project=None) -> dict:
    """`{"limit", "warn"}` kept for the project (both None for none). Raises SpendError for a file that is not ours."""
    limit, warn = _read(project)
    return {"limit": limit, "warn": warn}


def set_limit(limit, warn=None, *, project=None) -> dict:
    """Keep `limit` (and `warn`, default 90% of it) for the project. Answers `{"limit", "warn", "previous": {"limit",
    "warn"}, "changed", "message"}`; `previous` is what to ask for again to undo it (limit None: `remove`)."""
    limit = amount(limit)
    warn = round(limit * WARN_SHARE, 2) if warn is None else amount(warn, "warn")
    if warn >= limit:
        raise SpendError("warn must be below the limit")
    with _locked(project):
        before = _previous(project)
        if before == (limit, warn):
            return {"limit": limit, "warn": warn, "previous": {"limit": before[0], "warn": before[1]},
                    "changed": False, "message": f"The limit is already ${limit:,.2f} (warning from ${warn:,.2f})."}
        _stage(project, limit, warn)
    return {"limit": limit, "warn": warn, "previous": {"limit": before[0], "warn": before[1]}, "changed": True,
            "message": f"The limit is ${limit:,.2f}, with a warning from ${warn:,.2f}. Nothing is stopped at it; the view reads the spend against it."}


def remove(*, project=None) -> dict:
    """Take the limit away. Answers `{"limit": None, "warn": None, "previous", "changed", "message"}`."""
    with _locked(project):
        before = _previous(project)
        try:
            osfs.unlink(path(project))
        except FileNotFoundError:
            pass
    changed = before[0] is not None
    return {"limit": None, "warn": None, "previous": {"limit": before[0], "warn": before[1]}, "changed": changed,
            "message": "The limit is removed." if changed else "There was no limit."}


# --- the figures ------------------------------------------------------------------------------------------------

_RECENT = "SELECT session_id, SUM(json_extract(data, '$.cost')) FROM session_message{hint} " \
          "WHERE time_created >= ? AND type = 'assistant' AND json_valid(data) GROUP BY session_id"


def split(root, sessions) -> tuple[float, float]:
    """(chat, workers): what the root session has cost, and what every other session of the tree has."""
    chat = float(getattr(root, "cost", 0) or 0)
    return chat, sum(float(getattr(item, "cost", 0) or 0) for item in sessions if item is not root)


def rate(db_path, ids, now: float | None = None):
    """Dollars an hour over the last half hour for the sessions `ids`, or None when the database cannot say."""
    since = int(((time.time() if now is None else now) - RATE_WINDOW) * 1000)
    try:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
    except (sqlite3.Error, OSError):
        return None
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_message)")}
        if not {"session_id", "type", "time_created", "data"} <= columns:
            return None
        try:  # the index on the time makes it a read of the last half hour only
            rows = conn.execute(_RECENT.format(hint=" INDEXED BY session_message_time_created_idx"), (since,)).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(_RECENT.format(hint=""), (since,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    wanted = set(ids)
    spent = sum(float(cost) for session, cost in rows if session in wanted and isinstance(cost, (int, float)))
    value = spent * 3600.0 / RATE_WINDOW
    return round(value, 4) if math.isfinite(value) else None


def figures(project, root, sessions, db_path, now: float | None = None) -> dict:
    """The snapshot's `spend` object (see the module doc)."""
    try:
        limit, warn = _read(project)
    except SpendError:
        limit = warn = None
    chat, workers = split(root, sessions)
    ids = [item.id for item in [root, *sessions] if item is not None and getattr(item, "id", None)]
    return {"limit": limit, "warn": warn, "rate": rate(db_path, ids, now) if db_path is not None and ids else None,
            "chat": round(chat, 6), "workers": round(workers, 6)}


def annotate(document: dict, *, project=None, db_path=None, root=None, sessions=(), now: float | None = None) -> dict:
    """Add `spend` to a snapshot document. Never raises."""
    try:
        document["spend"] = figures(project, root, list(sessions), db_path, now)
    except Exception:  # noqa: BLE001
        document["spend"] = {"limit": None, "warn": None, "rate": None, "chat": 0.0, "workers": 0.0}
    return document


# --- the command line ---------------------------------------------------------------------------------------------


def _parser():
    parser = argparse.ArgumentParser(prog="agentgraph spend", description="The project's spend limit.", allow_abbrev=False)
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument("--dir", type=Path, help="the project folder (default: here)")
    common.add_argument("--json", action="store_true")
    groups = parser.add_subparsers(dest="op", required=True)
    limit = groups.add_parser("limit", parents=[common], allow_abbrev=False, help="keep, change or remove the limit")
    limit.add_argument("amount", nargs="?", help="dollars")
    limit.add_argument("--warn", help="dollars: the attention colour starts here (default: 90%% of the limit)")
    limit.add_argument("--off", action="store_true", help="remove the limit")
    showing = groups.add_parser("show", parents=[common], allow_abbrev=False, help="the limit and what the tree has cost")
    showing.add_argument("--db", type=Path, help="OpenCode's database (default: the real one)")
    showing.add_argument("--session", metavar="ID", help="the orchestrator session (default: the newest of this project)")
    return parser


def _live(args) -> dict:
    from .sources import DB_PATH, build_snapshot
    db = args.db if args.db is not None else DB_PATH
    snapshot = build_snapshot(root_id=args.session, cwd=args.dir, db_path=db)
    document = annotate({}, project=args.dir, db_path=db, root=snapshot.root, sessions=snapshot.sessions,
                        now=snapshot.now)
    spent = sum(item.cost for item in [snapshot.root, *snapshot.sessions] if item is not None)
    return {**document["spend"], "spent": round(spent, 6)}


def main(argv=None) -> int:
    osfs.utf8_stdout()
    args = _parser().parse_args(argv)
    try:
        if args.op == "limit":
            if args.off:
                if args.amount is not None or args.warn is not None:
                    raise SpendError("--off takes no amount")
                result = remove(project=args.dir)
            else:
                if args.amount is None:
                    raise SpendError("give the limit in dollars, or --off")
                result = set_limit(args.amount, args.warn, project=args.dir)
        else:
            result = _live(args)
    except (SpendError, OSError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"spend: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
    elif args.op == "limit":
        print(result["message"])
    else:
        limit, warn, rate_now = result["limit"], result["warn"], result["rate"]
        print("limit: " + (f"${limit:,.2f} (warning from ${warn:,.2f})" if limit is not None else "none"))
        print(f"spent: ${result['spent']:,.2f} (chat ${result['chat']:,.2f}, workers ${result['workers']:,.2f})")
        print("rate: " + (f"${rate_now:,.2f} an hour over the last 30 minutes" if rate_now is not None else "unknown"))
    return 0


if __name__ == "__main__":
    # As agentgraph.setups: run as a script, this file's classes are copies of the package's.
    from agentgraph.spend import main as package_main
    raise SystemExit(package_main())
