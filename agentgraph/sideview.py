"""Read-only side-chat information, independent of the session launcher."""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from . import chatfeed, sources
from .fileio import open_regular, windows_owned_directory

SEPARATOR = "\n\n--- brief"


def side_feed(feed: dict) -> dict:
    """Remove reasoning and the appended private brief from displayed questions."""
    items = []
    for item in feed.get("items", []):
        if item.get("kind") == "reasoning":
            continue
        if item.get("kind") == "user":
            text = item.get("text", "")
            cut = text.find(SEPARATOR)
            if cut >= 0:
                item = {**item, "text": text[:cut], "cut": False}
        items.append(item)
    return {**feed, "items": items}


def windows_status(main, project, *, agents_dir=None, db_path=None):
    """Find an existing side-chat session without starting one or writing files."""
    project = Path(project)
    agents_dir = Path(agents_dir) if agents_dir is not None else sources.CONFIG_DIR / "agents"
    out = {"installed": False, "session": None, "model": None, "busy": False}
    for folder in (agents_dir, project / ".opencode" / "agents"):
        try:
            with windows_owned_directory(folder) as directory:
                os.close(open_regular(directory / "side-chat.md", os.O_RDONLY))
            out["installed"] = True
            break
        except OSError:
            continue
    if not chatfeed.valid_session(main):
        return out
    try:
        with windows_owned_directory(project / ".opencode" / "sidechat") as directory:
            with os.fdopen(open_regular(directory / f"{main}.json", os.O_RDONLY), "rb") as stream:
                raw = stream.read((1 << 20) + 1)
        if len(raw) > 1 << 20:
            return out
        state = json.loads(raw.decode("utf-8"))
        if not isinstance(state, dict) or state.get("main") != main:
            return out
        side = state.get("side")
        if not chatfeed.valid_session(side) or side == main:
            return out
        with closing(sources._connect(db_path if db_path is not None else sources.DB_PATH)) as conn:
            row = conn.execute("SELECT agent, cost FROM session_v2 WHERE id = ?", (side,)).fetchone()
        if not row or row[0] != "side-chat":
            return out
        out.update(session=side, model=state.get("model") if isinstance(state.get("model"), str) else None)
        if isinstance(row[1], (int, float)):
            out["spent"] = row[1]
    except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error):
        pass
    return out
