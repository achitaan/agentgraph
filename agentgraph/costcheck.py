"""`agentgraph cost --check`: has the earlier cost work paid off? The last N days (default 7) are set against the
baseline before the first recorded change to the cost settings (the same split `cost --trend` makes), one line per
question with a verdict word (better, same, worse, not enough data) and the two numbers. The counting is done by
functions that take plain lists of turns, so the tests need no database."""
from __future__ import annotations

import json
import os
import statistics
import time
from bisect import bisect_right
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from . import cost, sources

BETTER, SAME, WORSE, NO_DATA = "better", "same", "worse", "not enough data"
DAYS = 7                      # the recent window
BASELINE_DAYS = 30            # chats updated this recently make the baseline (the window `cost --trend` uses)
MIN_TURNS = 30                # fewer turns in a period than this: no verdict on rates
MIN_WORKER_SPEND = 1.0        # dollars of worker spend a period needs for the long-chat share
TOLERANCE = 0.10              # a number more than 10% off the baseline is better or worse ...
SHARE_FLOOR = 0.02            # ... and a share must also move by more than 2 points
RECACHE_READ_UNDER = 25_000   # a "whole chat re-cached" turn read less than this from the cache ...
RECACHE_WRITE_OVER = 30_000   # ... and wrote more than this, ...
RECACHE_RUN = 2               # ... in a run of at least this many such turns in a row in one chat
ROLLOVERS_PER_DAY = 6         # one chat rolled over more often than this in a day is worse
SUPPLIED = "own-compaction: supplied"
LOG_NAME = "cost-check.jsonl"
ORDER = ("per_turn", "cold_share", "compactions", "rollovers", "long_chats", "recached", "rollover_gaps")   # the order of the lines


# ---- where the files are ----

def data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".local" / "share"


def check_log_path() -> Path:
    """The dated rows of earlier checks. `AGENTGRAPH_COST_CHECK_HOME` replaces the whole folder (tests use it)."""
    own = os.environ.get("AGENTGRAPH_COST_CHECK_HOME", "")
    return (Path(own) if own and os.path.isabs(own) else data_home() / "agentgraph" / "cost-check") / LOG_NAME


def rollover_log_path() -> Path:
    own = os.environ.get("AGENTGRAPH_ROLLOVER_HOME", "")
    return (Path(own) if own and os.path.isabs(own) else data_home() / "agentgraph" / "rollover") / "rollover.log"


# ---- verdicts ----

def verdict(recent, base, floor: float = 0.0) -> str:
    """Lower is better. A number counts as changed when it moves by more than 10% of the baseline and by more than `floor`."""
    if recent is None or base is None:
        return NO_DATA
    margin = max(TOLERANCE * abs(base), floor)
    if recent > base + margin:
        return WORSE
    if recent < base - margin:
        return BETTER
    return SAME


def overall(verdicts: list[str]) -> str:
    if WORSE in verdicts:
        return WORSE
    if BETTER in verdicts:
        return BETTER
    return SAME if SAME in verdicts else NO_DATA


# ---- the counting (plain lists in, numbers out) ----

def day_of(seconds: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(seconds))


def in_range(moment: float, window: tuple[float, float] | None) -> bool:
    return window is not None and window[0] <= moment < window[1]


def windows(history: list[dict], now: float, days: int) -> tuple[tuple[float, float] | None, tuple[float, float]]:
    """(baseline, recent) as (from, to) in seconds. The baseline is everything before the first recorded change (None when
    there is none); the recent window is the last `days` days, and starts no earlier than that change."""
    first = min((entry["time"] for entry in history), default=None)
    recent_from = now - days * 86400
    if first is None:
        return None, (recent_from, now + 1)
    return (0.0, first), (max(recent_from, first), now + 1)


def is_recached(turn: dict) -> bool:
    return turn["read"] < RECACHE_READ_UNDER and turn["write"] > RECACHE_WRITE_OVER


def recached(chats: dict[str, list[dict]]) -> dict:
    """The "whole chat re-cached" turns: each is a turn that is, together with the turn before or after it in the same chat, one
    of at least two in a row. A single one is an ordinary cold turn and is not counted."""
    found, hit = [], set()
    for chat, turns in chats.items():
        run: list[dict] = []
        for turn in turns + [None]:
            if turn is not None and is_recached(turn):
                run.append(turn)
                continue
            if len(run) >= RECACHE_RUN:
                found += run
                hit.add(chat)
            run = []
    return {"turns": len(found), "cost": sum(turn["cost"] for turn in found), "chats": len(hit)}


def per_turn_stats(turns: list[dict], settings_at) -> dict:
    """Dollars a turn (the estimated real bill, as in `cost --trend`) and the share of it spent on cold turns."""
    billed = [cost._billed(turn, settings_at(turn)) for turn in turns]
    total = sum(billed)
    cold = sum(price for turn, price in zip(turns, billed) if cost.is_cold(turn))
    return {"turns": len(turns), "billed": total, "per_turn": total / len(turns) if turns else None,
            "cold_share": cold / total if total else None}


def compaction_stats(compactions: list[dict], spend: float) -> dict:
    """How many compactions and what they cost, and the share of all spend (turns plus compactions) that was."""
    spent = sum(row["cost"] for row in compactions)
    total = spend + spent
    return {"count": len(compactions), "cost": spent, "share": spent / total if total else None}


def long_share(sessions: list[dict], long_turns: int = cost.LONG_TURNS, long_cost: float = cost.LONG_COST) -> dict:
    """The share of worker spend that went to long chats (over `long_turns` turns or `long_cost` dollars)."""
    workers = [row for row in sessions if row["kind"] == "worker"]
    total = sum(row["billed"] for row in workers)
    long = [row for row in workers if len(row["turns"]) > long_turns or row["billed"] > long_cost]
    spent = sum(row["billed"] for row in long)
    return {"workers": len(workers), "spend": total, "long": len(long), "long_spend": spent, "share": spent / total if total >= MIN_WORKER_SPEND else None}


def rolled_over(rows: list[dict]) -> list[dict]:
    """The rollovers in a rollover log: `rolled`, and a compaction OpenCode started that got the plugin's summary."""
    out = []
    for row in rows:
        if not isinstance(row, dict) or isinstance(row.get("t"), bool) or not isinstance(row.get("t"), (int, float)):
            continue
        reason = row.get("reason")
        if row.get("event") == "rolled" or (row.get("event") == "own_compaction" and isinstance(reason, str) and reason.startswith(SUPPLIED)):
            out.append({"t": row["t"] / 1000, "session": row.get("session") if isinstance(row.get("session"), str) else ""})
    return sorted(out, key=lambda row: row["t"])


def rollover_stats(rollovers: list[dict]) -> dict:
    """How many rollovers, the most for one chat in one day, and the median minutes between rollovers of the same chat."""
    per_day: dict[tuple[str, str], int] = defaultdict(int)
    times: dict[str, list[float]] = defaultdict(list)
    for row in rollovers:
        per_day[(row["session"], day_of(row["t"]))] += 1
        times[row["session"]].append(row["t"])
    gaps = [(b - a) / 60 for moments in times.values() for a, b in zip(sorted(moments), sorted(moments)[1:])]
    return {"count": len(rollovers), "chats": len(times), "most_in_a_day": max(per_day.values(), default=0),
            "median_gap_minutes": statistics.median(gaps) if gaps else None, "gaps": len(gaps)}


# ---- the check ----

def _money(value) -> str:
    return "n/a" if value is None else f"${value:.3f}"


def _percent(value) -> str:
    return "n/a" if value is None else f"{value:.0%}" if value >= 0.1 or value == 0 else f"{value:.1%}"


def _row(key: str, label: str, result: str, text: str, recent, baseline) -> dict:
    return {"id": key, "label": label, "verdict": result, "recent": recent, "baseline": baseline, "line": f"{label}: {result} ({text})"}


def check(sessions: list[dict], compactions: list[dict], rollover_rows: list[dict] | None, history: list[dict], *, now: float, days: int = DAYS) -> dict:
    """The seven checks. `sessions` are {id, kind, billed, turns}, `compactions` {session, time, cost}, `rollover_rows` the rollover log's
    rows (None: no log); `history` is the settings log, as `cost.load_history` reads it."""
    base_win, recent_win = windows(history, now, days)
    starts = [entry["time"] for entry in sorted(history, key=lambda entry: entry["time"])]
    configs = [cost._default_settings()] + [entry["settings"] for entry in sorted(history, key=lambda entry: entry["time"])]
    settings_at = lambda turn: configs[bisect_right(starts, turn["time"])]

    def part(window):
        """The turns of each session inside a window, and the root chats' turns."""
        chats = {row["id"]: [turn for turn in row["turns"] if in_range(turn["time"], window)] for row in sessions}
        roots = [turn for row in sessions if row["kind"] == "root" for turn in chats[row["id"]]]
        return {"chats": {key: turns for key, turns in chats.items() if turns}, "roots": roots}
    recent, base = part(recent_win), part(base_win)
    stats = {"recent": per_turn_stats(recent["roots"], settings_at), "base": per_turn_stats(base["roots"], settings_at)}
    enough = lambda which: stats[which]["turns"] >= MIN_TURNS
    checks = []

    # 1 and 2: dollars a turn and the cold share, over root chats as `cost --trend` has them
    for key, label, field, text, floor in (("per_turn", "dollars a turn", "per_turn", _money, 0.0), ("cold_share", "cold share", "cold_share", _percent, SHARE_FLOOR)):
        r, b = stats["recent"][field], stats["base"][field]
        ok = enough("recent") and enough("base") and base_win is not None
        checks.append(_row(key, label, verdict(r, b, floor) if ok else NO_DATA, f"{text(r)} recently against {text(b)} before; {stats['recent']['turns']} and {stats['base']['turns']} turns", r, b))

    # 3: compactions: how many and what they cost, as a share of all spend
    def spend(picked):
        return sum(turn["cost"] for turns in picked["chats"].values() for turn in turns)
    parts = {}
    for name, window, picked in (("recent", recent_win, recent), ("base", base_win, base)):
        parts[name] = compaction_stats([row for row in compactions if in_range(row["time"], window)], spend(picked))
    ok = enough("recent") and enough("base") and base_win is not None
    r, b = parts["recent"], parts["base"]
    checks.append(_row("compactions", "compactions", verdict(r["share"], b["share"], SHARE_FLOOR) if ok else NO_DATA,
                       f"{r['count']} recently costing ${r['cost']:.2f}, {_percent(r['share'])} of spend; before: {b['count']} costing ${b['cost']:.2f}, {_percent(b['share'])}",
                       {"count": r["count"], "cost": r["cost"], "share": r["share"]}, {"count": b["count"], "cost": b["cost"], "share": b["share"]}))

    # 4 and 7: rollovers, from the rollover plugin's log
    have_log = bool(rollover_rows)
    rolls = rollover_stats([row for row in rolled_over(rollover_rows or []) if in_range(row["t"], recent_win)])
    if not have_log:
        v4 = v7 = NO_DATA
    elif rolls["most_in_a_day"] > ROLLOVERS_PER_DAY:
        v4 = v7 = WORSE
    else:
        v4 = SAME
        v7 = SAME if rolls["median_gap_minutes"] is not None else NO_DATA
    checks.append(_row("rollovers", "rollovers", v4, f"{rolls['count']} recently; the most for one chat in one day: {rolls['most_in_a_day']}, the limit is {ROLLOVERS_PER_DAY}",
                       {"count": rolls["count"], "chats": rolls["chats"], "most_in_a_day": rolls["most_in_a_day"]}, ROLLOVERS_PER_DAY))
    gap = rolls["median_gap_minutes"]
    checks.append(_row("rollover_gaps", "rollover gaps", v7, ((f"median {gap:.0f} minutes between rollovers of one chat ({rolls['gaps']} gaps)" if gap is not None else "no chat rolled over twice")
                                                           + (f"; one chat rolled {rolls['most_in_a_day']} times in a day" if v7 == WORSE else "")),
                       {"median_minutes": gap, "gaps": rolls["gaps"], "most_in_a_day": rolls["most_in_a_day"]}, ROLLOVERS_PER_DAY))

    # 5: how much worker spend is in long chats (a session counts in the period of its last update)
    def workers(window):
        return long_share([row for row in sessions if row["kind"] == "worker" and in_range(row["updated"], window)])
    lr, lb = workers(recent_win), workers(base_win)
    ok = lr["share"] is not None and lb["share"] is not None and base_win is not None
    checks.append(_row("long_chats", "worker spend in long chats", verdict(lr["share"], lb["share"], SHARE_FLOOR) if ok else NO_DATA,
                       f"{_percent(lr['share'])} of ${lr['spend']:.2f} recently against {_percent(lb['share'])} of ${lb['spend']:.2f} before; long means over {cost.LONG_TURNS} turns or ${cost.LONG_COST:g}",
                       {"share": lr["share"], "spend": lr["spend"], "long": lr["long"]}, {"share": lb["share"], "spend": lb["spend"], "long": lb["long"]}))

    # 6: the leak: a whole chat written to the cache again, turn after turn
    leak, old = recached(recent["chats"]), recached(base["chats"])
    total = spend(recent)
    share = leak["cost"] / total if total else None
    text = f"{leak['turns']} turns, ${leak['cost']:.2f}, {_percent(share) if share is not None else 'n/a'} of spend, in {leak['chats']} chat" + ("" if leak["chats"] == 1 else "s") + f"; before: {old['turns']} turns"
    if leak["chats"]:
        text += f"; caching stopped working in {leak['chats']} chat" + ("" if leak["chats"] == 1 else "s")
    result = WORSE if leak["chats"] else (NO_DATA if not enough("recent") else BETTER if old["turns"] and base_win is not None else SAME)
    checks.append(_row("recached", "whole chat re-cached", result, text, {**leak, "share": share}, old))

    checks.sort(key=lambda row: ORDER.index(row["id"]))
    final = overall([row["verdict"] for row in checks])
    worse = [row["label"] for row in checks if row["verdict"] == WORSE]
    return {"date": day_of(now), "time": now, "days": days, "baseline_until": base_win[1] if base_win else None, "recent_from": recent_win[0],
            "checks": checks, "overall": final, "worse": worse}


def report(result: dict) -> str:
    stamp = lambda moment: time.strftime("%Y-%m-%d %H:%M", time.localtime(moment))
    until = "no baseline (the settings log has no change recorded)" if result["baseline_until"] is None else "before " + stamp(result["baseline_until"])
    lines = [f"cost check, {result['date']}: recent is the last {result['days']} days, from {stamp(result['recent_from'])} (not before the first recorded change); baseline is {until}"]
    lines += [f"{number}. {row['line']}" for number, row in enumerate(result["checks"], 1)]
    lines.append(f"overall: {result['overall']}" + (f" ({len(result['worse'])} worse: {', '.join(result['worse'])})" if result["worse"] else ""))
    return "\n".join(lines)


def log_row(result: dict) -> dict:
    """One dated row for the log: the numbers and the verdicts, no text."""
    return {"date": result["date"], "time": result["time"], "days": result["days"], "overall": result["overall"],
            "checks": {row["id"]: {"verdict": row["verdict"], "recent": row["recent"], "baseline": row["baseline"]} for row in result["checks"]}}


def append_log(path: Path, result: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(log_row(result)) + "\n")


# ---- reading the database ----

def load(db_path: Path, now: float, days: int = DAYS, min_cost: float = 1.0) -> tuple[list[dict], list[dict]]:
    """(sessions, compactions) for the check: root chats billed at least `min_cost` (as `cost --trend` has them) and every worker session,
    updated within the baseline window or the recent one; and every compaction in that time."""
    since_ms = int((now - max(days, BASELINE_DAYS) * 86400) * 1000)
    with closing(cost._connect(db_path)) as db:
        found = db.execute("SELECT id, parent_id, cost, time_updated FROM session_v2 WHERE time_updated >= ? AND "
                           "((parent_id IS NULL AND cost >= ?) OR (parent_id IS NOT NULL AND cost > 0)) ORDER BY time_updated", (since_ms, min_cost)).fetchall()
        sessions = [{"id": ident, "kind": "worker" if parent else "root", "billed": cost._number(billed), "updated": cost._number(updated) / 1000,
                     "turns": cost._read_turns(db, ident)} for ident, parent, billed, updated in found]
        ok = "(CASE WHEN json_valid(data) THEN json_type(data) = 'object' ELSE 0 END)"
        # the column is indexed (and in every real database); a database without it gets the time from the message
        column = "time_created" in {row[1] for row in db.execute("PRAGMA table_info(session_message)")}
        moment = "time_created" if column else f"CASE WHEN {ok} THEN json_extract(data, '$.time.created') END"
        rows = db.execute(f"SELECT session_id, {moment}, CASE WHEN {ok} THEN json_extract(data, '$.cost') END, CASE WHEN {ok} THEN json_extract(data, '$.status') END "
                          "FROM session_message WHERE type='compaction'" + (" AND time_created >= ?" if column else ""), (since_ms,) if column else ()).fetchall()
    compactions = [{"session": session, "time": cost._number(created) / 1000, "cost": cost._number(spent)}
                   for session, created, spent, status in rows if status != "failed" and cost._number(created) >= since_ms]
    return sessions, compactions


def read_rollover_log(path: Path) -> list[dict] | None:
    """The rollover log's rows (lines that are not JSON objects are skipped); None when there is no log."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def run(db_path: Path, history: list[dict], *, days: int = DAYS, as_json: bool = False, check_log: Path | None = None,
        rollover_log: Path | None = None, now: float | None = None) -> int:
    now = time.time() if now is None else now
    sessions, compactions = load(db_path, now, days)
    rows = read_rollover_log(rollover_log or rollover_log_path())
    result = check(sessions, compactions, rows, history, now=now, days=days)
    try:
        append_log(check_log or check_log_path(), result)
    except OSError:
        pass
    print(json.dumps(result) if as_json else report(result))
    return 0
