"""Where one chat's money went: warm turns, cold turns and their causes, and
what a smaller context or a longer cache lifetime would have cost instead.

A turn is "cold" when most of its context was written to the provider's prompt
cache instead of read from it: the cache had expired or no longer matched, so
the whole conversation was paid for again at the write price.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from bisect import bisect_right
from contextlib import closing
from pathlib import Path

from . import sources

# Price of each kind of token relative to a plain input token (Anthropic's published ratios).
READ, WRITE_5M, WRITE_1H, OUTPUT = 0.1, 1.25, 2.0, 5.0
CACHE_MINUTES = 5.0            # the default cache lifetime
COLD_TOKENS = 50_000           # a cold turn writes at least this much, and more than it reads
KEPT_AFTER_COMPACTION = 40_000  # summary plus the recent conversation kept beside it
SUMMARY_OUTPUT = 4_000
BANDS = ((0, 100_000), (100_000, 200_000), (200_000, 400_000), (400_000, 10**9))
CAUSES = ("pause over an hour", "pause over 5 minutes", "settings or model changed", "after compaction", "unexplained")
SWITCHES = {"system", "model-switched", "agent-switched"}
PAUSE_CAUSES = {"pause over an hour", "pause over 5 minutes"}
CONFIG_PATH = sources.CONFIG_DIR / "opencode.json"
SETTINGS_LOG = Path(__file__).resolve().parent.parent / "cost-settings.jsonl"


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _read_turns(db, session: str) -> list[dict]:
    """The assistant turns of one chat, oldest first. SQLite pulls out only the fields that are
    needed, so the (large) messages are not parsed in Python."""
    # CASE, not AND: SQLite does not stop at the first false term, and json_type fails on invalid JSON.
    ok = "(CASE WHEN type = 'assistant' AND json_valid(data) THEN json_type(data) = 'object' ELSE 0 END)"
    fields = ("cost", "tokens.input", "tokens.cache.read", "tokens.cache.write", "tokens.output",
              "tokens.reasoning", "time.created", "model.id")
    columns = ", ".join(f"CASE WHEN {ok} THEN json_extract(data, '$.{field}') END" for field in fields)
    rows = db.execute(f"SELECT type, {ok}, {columns} FROM session_message WHERE session_id=? ORDER BY seq", (session,)).fetchall()
    turns, between, previous = [], set(), None
    for kind, valid, cost, tokens_in, read, write, output, reasoning, created, model in rows:
        if kind != "assistant":
            between.add(kind)
            continue
        if not valid:
            continue
        created = _number(created) / 1000
        turns.append({
            "cost": _number(cost), "input": int(_number(tokens_in)),
            "read": int(_number(read)), "write": int(_number(write)),
            "output": int(_number(output)) + int(_number(reasoning)),
            "gap": (created - previous) / 60 if previous and created else 0.0, "between": sorted(between),
            "time": created, "model": model if isinstance(model, str) else "",
        })
        previous, between = created or previous, set()
    return turns


def _connect(db_path: Path):
    import sqlite3
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)


def load_turns(session: str | None = None, db_path: Path = sources.DB_PATH) -> tuple[str, list[dict], dict]:
    """The assistant turns of one chat (default: the root chat used most recently), oldest first,
    and the total cost and number of the sessions it started."""
    with closing(_connect(db_path)) as db:
        if session is None:
            row = db.execute("SELECT id FROM session_v2 WHERE parent_id IS NULL ORDER BY time_updated DESC LIMIT 1").fetchone()
            if row is None:
                raise SystemExit("no chat found")
            session = row[0]
        turns = _read_turns(db, session)
        workers = db.execute("SELECT count(*), coalesce(sum(cost), 0) FROM session_v2 WHERE parent_id=?", (session,)).fetchone()
    return session, turns, {"sessions": int(workers[0]), "cost": float(workers[1])}


# ---- the cost settings in force, and how chats cost under each ----

def _default_settings() -> dict:
    return {"compaction_buffer": None, "cache_ttl": {}}


def read_settings(config_path: Path = CONFIG_PATH) -> dict:
    """The settings that change what a chat costs, from OpenCode's config file."""
    settings = _default_settings()
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return settings
    except (OSError, ValueError):
        return settings

    def get(node, *path):
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return node
    buffer = get(config, "compaction", "buffer")
    settings["compaction_buffer"] = buffer if isinstance(buffer, (int, float)) and not isinstance(buffer, bool) else None
    anthropic = get(config, "providers", "anthropic")
    ttl = get(anthropic, "settings", "cache", "ttl")
    if isinstance(ttl, str):
        settings["cache_ttl"]["*"] = ttl
    models = get(anthropic, "models")
    for model, entry in (models.items() if isinstance(models, dict) else ()):
        ttl = get(entry, "settings", "cache", "ttl")
        if isinstance(ttl, str):
            settings["cache_ttl"][model] = ttl
    return settings


def load_history(log_path: Path = SETTINGS_LOG) -> list[dict]:
    """The settings log, oldest first. Lines that are not a time and a settings object are skipped."""
    try:
        lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        time_ = entry.get("time") if isinstance(entry, dict) else None
        if isinstance(time_, (int, float)) and not isinstance(time_, bool) and isinstance(entry.get("settings"), dict):
            entries.append({"time": time_, "settings": entry["settings"]})
    return sorted(entries, key=lambda entry: entry["time"])


def note_settings(log_path: Path, settings: dict, when: float) -> bool:
    """Add a line to the settings log when the settings differ from the last line's."""
    history = load_history(log_path)
    if history and history[-1]["settings"] == settings:
        return False
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"time": when, "settings": settings}) + "\n")
    return True


def _ttl(turn: dict, settings: dict) -> str | None:
    ttl = settings.get("cache_ttl") or {}
    return ttl.get(turn.get("model", ""), ttl.get("*"))


def _billed(turn: dict, settings: dict) -> float:
    """The estimated real bill: OpenCode records every cache write at the five-minute price."""
    if _ttl(turn, settings) == "1h" and _weight(turn) > 0:
        return turn["cost"] * _weight(turn, WRITE_1H) / _weight(turn)
    return turn["cost"]


def trend(db_path: Path, history: list[dict], *, days: int = 30, min_cost: float = 1.0, now: float | None = None) -> list[dict]:
    """What root chats cost per turn under each period of settings, oldest period first."""
    now = time.time() if now is None else now
    history = sorted(history, key=lambda entry: entry["time"])
    since = [None] + [entry["time"] for entry in history]
    settings = [_default_settings()] + [entry["settings"] for entry in history]
    starts = [entry["time"] for entry in history]
    periods: dict[int, dict] = {}
    with closing(_connect(db_path)) as db:
        # session_v2.time_updated is in milliseconds
        sessions = db.execute("SELECT id FROM session_v2 WHERE parent_id IS NULL AND time_updated >= ? AND cost >= ? ORDER BY time_updated",
                              (int((now - days * 86400) * 1000), min_cost)).fetchall()
        for (session,) in sessions:
            for turn in _read_turns(db, session):
                index = bisect_right(starts, turn["time"])
                period = periods.setdefault(index, {"turns": [], "chats": set()})
                period["turns"].append(turn)
                period["chats"].add(session)
    result = []
    for index in sorted(periods):
        turns, config = periods[index]["turns"], settings[index]
        billed = [_billed(turn, config) for turn in turns]
        total = sum(billed)
        cold = [(turn, price) for turn, price in zip(turns, billed) if is_cold(turn)]
        pauses = [(turn, price) for turn, price in cold if cause_of(turn) in PAUSE_CAUSES]
        result.append({
            "since": since[index], "settings": config, "chats": len(periods[index]["chats"]), "turns": len(turns),
            "cost": sum(turn["cost"] for turn in turns), "billed": total, "per_turn": total / len(turns),
            "cold_share": sum(price for _, price in cold) / total if total else 0,
            "pauses": {"turns": len(pauses), "cost": sum(price for _, price in pauses)},
            "context": statistics.median(_context(turn) for turn in turns),
        })
    return result


def describe_settings(settings: dict) -> str:
    buffer = settings.get("compaction_buffer")
    ttl = settings.get("cache_ttl") or {}
    hour = sorted(model for model, value in ttl.items() if value == "1h")
    parts = ["compaction buffer " + (f"{buffer:,}".replace(",", " ") if isinstance(buffer, int) else str(buffer) if buffer is not None else "default"),
             "one-hour cache for " + ", ".join("all models" if model == "*" else model for model in hour) if hour else "five-minute cache"]
    return "; ".join(parts)


def trend_report(periods: list[dict]) -> str:
    if not periods:
        return "no chats with turns in the window"
    blocks = []
    for period in periods:
        started = "before the first recorded change" if period["since"] is None else time.strftime("from %Y-%m-%d %H:%M", time.localtime(period["since"]))
        pauses = period["pauses"]
        blocks.append("\n".join([
            f"{started}: {describe_settings(period['settings'])}",
            f"    {period['chats']} chats, {period['turns']} turns, median context {_k(int(period['context']))}",
            f"    ${period['per_turn']:.3f} a turn (${period['billed']:.2f} billed, ${period['cost']:.2f} recorded), cold share {period['cold_share']:.0%}",
            f"    pauses: {pauses['turns']} turns, ${pauses['cost']:.2f}"]))
    return "\n\n".join(blocks)


def _context(turn: dict) -> int:
    return turn["input"] + turn["read"] + turn["write"]


def _weight(turn: dict, write: float = WRITE_5M) -> float:
    return turn["input"] + READ * turn["read"] + write * turn["write"] + OUTPUT * turn["output"]


def is_cold(turn: dict) -> bool:
    return turn["write"] >= COLD_TOKENS and turn["write"] > turn["read"]


def cause_of(turn: dict) -> str:
    if "compaction" in turn["between"]:
        return "after compaction"
    if turn["gap"] > 60:
        return "pause over an hour"
    if turn["gap"] > CACHE_MINUTES:
        return "pause over 5 minutes"
    return "settings or model changed" if SWITCHES & set(turn["between"]) else "unexplained"


def _capped(turn: dict, cap: int) -> dict:
    size = _context(turn)
    if size <= cap:
        return turn
    share = cap / size
    return {**turn, **{key: turn[key] * share for key in ("input", "read", "write")}}


def _kept_warm(turn: dict) -> dict:
    """With a one-hour cache, a pause of 5 to 60 minutes reads the context instead of writing it again."""
    if is_cold(turn) and cause_of(turn) == "pause over 5 minutes":
        return {**turn, "read": turn["read"] + turn["write"], "write": 0}
    return turn


def analyse(turns: list[dict], cap: int = 250_000) -> dict:
    total = sum(turn["cost"] for turn in turns)
    cold = [turn for turn in turns if is_cold(turn)]
    causes = {name: {"turns": 0, "cost": 0.0} for name in CAUSES}
    for turn in cold:
        entry = causes[cause_of(turn)]
        entry["turns"] += 1
        entry["cost"] += turn["cost"]
    bands = []
    for low, high in BANDS:
        part = [turn for turn in turns if low <= _context(turn) < high]
        if part:
            bands.append({"from": low, "to": high, "turns": len(part), "cost": sum(turn["cost"] for turn in part)})

    def rescaled(change, write=WRITE_5M) -> float:
        """Each turn's recorded cost, scaled by how its priced tokens would change."""
        return sum(turn["cost"] * _weight(change(turn), write) / _weight(turn) for turn in turns if _weight(turn) > 0)

    # Earlier compaction means more summaries: one per (cap - kept) tokens of growth.
    growth = sum(max(0, _context(b) - _context(a)) for a, b in zip(turns, turns[1:]))
    largest = max((_context(turn) for turn in turns), default=0)
    compactions = 0 if largest <= cap else max(0, growth // max(1, cap - KEPT_AFTER_COMPACTION)
                                               - sum("compaction" in turn["between"] for turn in turns))
    weight = sum(_weight(turn) for turn in turns)
    summaries = compactions * (READ * cap + OUTPUT * SUMMARY_OUTPUT + WRITE_5M * KEPT_AFTER_COMPACTION) * (total / weight if weight else 0)
    return {
        "cost": total, "turns": len(turns),
        "warm": {"turns": len(turns) - len(cold), "cost": total - sum(turn["cost"] for turn in cold)},
        "cold": {"turns": len(cold), "cost": sum(turn["cost"] for turn in cold)},
        "causes": {name: entry for name, entry in causes.items() if entry["turns"]},
        "bands": bands, "largest_context": largest,
        "what_if": {
            "cap": cap, "extra_compactions": compactions,
            "compact_at_cap": rescaled(lambda turn: _capped(turn, cap)) + summaries,
            "one_hour_cache": rescaled(_kept_warm, WRITE_1H),
            "both": rescaled(lambda turn: _kept_warm(_capped(turn, cap)), WRITE_1H) + summaries,
        },
    }


def _k(tokens: int) -> str:
    return f"{tokens // 1000}k"


def report(session: str, result: dict, workers: dict) -> str:
    def line(label, entry, indent=4):
        each = entry["cost"] / entry["turns"] if entry["turns"] else 0
        return f"{' ' * indent}{label:<{40 - indent}}{entry['turns']:>5} turns  ${entry['cost']:>8.2f}  ${each:.2f} a turn"
    total = result["cost"]
    lines = [f"{session}: ${total:.2f} over {result['turns']} turns, largest context {_k(result['largest_context'])}"
             + (f"; its {workers['sessions']} worker sessions cost ${workers['cost']:.2f} more" if workers["sessions"] else ""),
             "", "Where it went", line("warm (context read from the cache)", result["warm"]),
             line("cold (context written again)", result["cold"])]
    lines += [line(name, entry, 6) for name, entry in result["causes"].items()]
    lines += ["", "By context size"]
    lines += [line(("over " + _k(band["from"])) if band["to"] >= 10**9 else f"{_k(band['from'])} to {_k(band['to'])}", band) for band in result["bands"]]
    what = result["what_if"]
    lines += ["", "What the same chat would have cost (estimates from the recorded tokens)"]
    for label, key in ((f"compaction at {_k(what['cap'])} ({what['extra_compactions']} more summaries)", "compact_at_cap"),
                       ("one-hour cache", "one_hour_cache"), ("both", "both")):
        lines.append(f"    {label:<47}${what[key]:>8.2f}  {'saves' if what[key] <= total else 'adds'} ${abs(total - what[key]):.2f}")
    return "\n".join(lines)


def note_current_settings(log_path: Path = SETTINGS_LOG, config_path: Path = CONFIG_PATH) -> bool:
    try:
        when = Path(config_path).stat().st_mtime
    except OSError:
        when = time.time()
    return note_settings(log_path, read_settings(config_path), when)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph cost", description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", help="chat to read (default: the root chat used most recently)")
    parser.add_argument("--cap", type=int, default=250_000, help="context size to compact at in the what-if (default 250000)")
    parser.add_argument("--db", type=Path, default=sources.DB_PATH, help="OpenCode session database")
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    parser.add_argument("--trend", action="store_true", help="cost per turn of root chats, split by the cost settings in force")
    parser.add_argument("--days", type=int, default=30, help="with --trend: chats updated in the last N days (default 30)")
    parser.add_argument("--settings-log", type=Path, default=SETTINGS_LOG, help="log of the cost settings over time")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="OpenCode config file")
    args = parser.parse_args(argv)
    try:
        note_current_settings(args.settings_log, args.config)
    except Exception:
        pass
    if args.trend:
        periods = trend(args.db, load_history(args.settings_log), days=args.days)
        print(json.dumps(periods) if args.json else trend_report(periods))
        return 0
    session, turns, workers = load_turns(args.session, args.db)
    result = analyse(turns, max(KEPT_AFTER_COMPACTION * 2, args.cap))
    print(json.dumps({"session": session, "workers": workers, **result}) if args.json else report(session, result, workers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
