"""Where one chat's money went: warm turns, cold turns and their causes, and
what a smaller context or a longer cache lifetime would have cost instead.

A turn is "cold" when most of its context was written to the provider's prompt
cache instead of read from it: the cache had expired or no longer matched, so
the whole conversation was paid for again at the write price.
"""
from __future__ import annotations

import argparse
import json
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


def load_turns(session: str | None = None, db_path: Path = sources.DB_PATH) -> tuple[str, list[dict], dict]:
    """The assistant turns of one chat (default: the root chat used most recently), oldest first,
    and the total cost and number of the sessions it started."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    import sqlite3
    with sqlite3.connect(uri, uri=True, timeout=1.0) as db:
        if session is None:
            row = db.execute("SELECT id FROM session_v2 WHERE parent_id IS NULL ORDER BY time_updated DESC LIMIT 1").fetchone()
            if row is None:
                raise SystemExit("no chat found")
            session = row[0]
        rows = db.execute("SELECT type, data FROM session_message WHERE session_id=? ORDER BY seq", (session,)).fetchall()
        workers = db.execute("SELECT count(*), coalesce(sum(cost), 0) FROM session_v2 WHERE parent_id=?", (session,)).fetchone()
    turns, between, previous = [], set(), None
    for kind, data in rows:
        if kind != "assistant":
            between.add(kind)
            continue
        try:
            message = json.loads(data)
        except ValueError:
            continue
        tokens = message.get("tokens") or {}
        cache = tokens.get("cache") or {}
        created = ((message.get("time") or {}).get("created") or 0) / 1000
        turns.append({
            "cost": float(message.get("cost") or 0), "input": int(tokens.get("input") or 0),
            "read": int(cache.get("read") or 0), "write": int(cache.get("write") or 0),
            "output": int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
            "gap": (created - previous) / 60 if previous and created else 0.0, "between": sorted(between),
        })
        previous, between = created or previous, set()
    return session, turns, {"sessions": int(workers[0]), "cost": float(workers[1])}


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph cost", description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", help="chat to read (default: the root chat used most recently)")
    parser.add_argument("--cap", type=int, default=250_000, help="context size to compact at in the what-if (default 250000)")
    parser.add_argument("--db", type=Path, default=sources.DB_PATH, help="OpenCode session database")
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    args = parser.parse_args(argv)
    session, turns, workers = load_turns(args.session, args.db)
    result = analyse(turns, max(KEPT_AFTER_COMPACTION * 2, args.cap))
    print(json.dumps({"session": session, "workers": workers, **result}) if args.json else report(session, result, workers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
