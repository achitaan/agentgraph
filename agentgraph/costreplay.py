"""Replays of stored turns for `agentgraph cost`: what other rollover (compaction) points would have cost,
how much of each agent's input came from the cache, and where context growth comes from.

Only token counts, names, ids, sizes and costs are read. No message text, prompt or tool output is
loaded: tool output is measured with SQL `length()` inside the database.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
from collections import Counter
from contextlib import closing
from pathlib import Path

from . import cost, sources

# Plain input price in dollars per token. The other prices follow from the ratios: the stored costs of
# these two families fit exactly with cache read at 0.05 (cost.READ is 0.1), cache write 1.25 and output 5.
INPUT_PRICE = {"opus": 4e-6, "sonnet": 2e-6}
READ_RATIO = 0.05
SUMMARY_WEIGHT = {"opus": 1.06, "sonnet": 1.16, "other": 1.06}  # weighted tokens a summary call bills per context token
FAMILIES = ("opus", "sonnet", "other")
THRESHOLDS = (80_000, 100_000, 120_000, 150_000, 200_000)
VARIANTS = ("code summary", "model summary")
DROP_TO, DROP_FROM = 0.6, 50_000   # a compaction: context under 60% of the last turn's, which was over 50k
TOP_SHARE, BIG_TURN = 0.05, 10_000
TIER = 200_000  # some models charge more per token above this context
SMALL_CONTEXT = 30_000     # what a rollover from notes leaves
PAUSE_BANDS = ((5, 15, "5 to 15 min"), (15, 60, "15 to 60 min"), (60, math.inf, "over 60 min"))
CONTEXT_BANDS = ((0, 60_000, "under 60k"), (60_000, 120_000, "60k to 120k"), (120_000, math.inf, "over 120k"))
ROLLOVER_OVER = (40_000, 60_000, 80_000, 100_000, 120_000, 150_000, 200_000)
EXTRA_ROLLOVER = 0.25      # dollars: stop lowering X when each extra rollover avoids less than this


def family_of(model) -> str:
    name = model.lower() if isinstance(model, str) else ""
    if "fast" in name:
        return "other"
    return next((family for family in ("opus", "sonnet") if family in name), "other")


def _family(turn: dict) -> str:
    return family_of(turn.get("model"))


def _prices(plain: float) -> tuple:
    """Dollars per token: plain input, cache read, cache write, output."""
    return (plain, plain * READ_RATIO, plain * cost.WRITE_5M, plain * cost.OUTPUT)


def _key(turn: dict) -> str:
    """What a turn's price is looked up by: Opus and Sonnet by family, any other model by its own id."""
    family = _family(turn)
    return family if family in INPUT_PRICE else (turn.get("model") or "unknown")


def _fit(turns: list[dict]) -> tuple | None:
    """Plain input and cache read prices that best fit the stored costs of these turns (write and output
    keep their ratios to plain input). None when no sensible price fits within 1%."""
    a1 = [turn["input"] + cost.WRITE_5M * turn["write"] + cost.OUTPUT * turn["output"] for turn in turns]
    a2 = [turn["read"] for turn in turns]
    s11, s12, s22 = sum(x * x for x in a1), sum(x * y for x, y in zip(a1, a2)), sum(y * y for y in a2)
    t1, t2 = sum(x * turn["cost"] for x, turn in zip(a1, turns)), sum(y * turn["cost"] for y, turn in zip(a2, turns))
    det = s11 * s22 - s12 * s12
    if len(turns) < 10 or det <= 1e-9 * s11 * s22:
        return None
    plain, read = (t1 * s22 - t2 * s12) / det, (s11 * t2 - s12 * t1) / det
    stored = sum(turn["cost"] for turn in turns)
    error = sum(abs(plain * x + read * y - turn["cost"]) for x, y, turn in zip(a1, a2, turns))
    if plain <= 0 or read < 0 or error > 0.01 * stored:
        return None
    return (plain, read, plain * cost.WRITE_5M, plain * cost.OUTPUT)


def price_table(turns: list[dict]) -> dict:
    """Prices for Opus and Sonnet. Every other model gets prices fitted to what its own turns cost, one set
    for contexts up to 200k and one above, so those replays match the stored cost."""
    table = {family: _prices(plain) for family, plain in INPUT_PRICE.items()}
    models: dict[str, list[dict]] = {}
    for turn in turns:
        if _key(turn) not in table:
            models.setdefault(_key(turn), []).append(turn)
    for key, part in models.items():
        tiers = {}
        for tier, chosen in (("low", [t for t in part if cost._context(t) <= TIER]), ("high", [t for t in part if cost._context(t) > TIER])):
            weight = sum(t["input"] + READ_RATIO * t["read"] + cost.WRITE_5M * t["write"] + cost.OUTPUT * t["output"] for t in chosen)
            tiers[tier] = _fit(chosen) or (_prices(sum(t["cost"] for t in chosen) / weight) if weight else None)
        tiers["low"] = tiers["low"] or tiers["high"] or _prices(INPUT_PRICE["sonnet"])
        tiers["high"] = tiers["high"] or tiers["low"]
        table[key] = tiers
    return table


def _price(prices: dict, turn: dict, context: float) -> tuple:
    entry = prices[_key(turn)]
    return entry if isinstance(entry, tuple) else entry["high" if context > TIER else "low"]


def dollars(turn: dict, price: tuple) -> float:
    return turn["input"] * price[0] + turn["read"] * price[1] + turn["write"] * price[2] + turn["output"] * price[3]


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    at = (len(ordered) - 1) * share
    low = math.floor(at)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def _median(values) -> float:
    values = list(values)
    return float(statistics.median(values)) if values else 0.0


# ---- compactions seen in the stored turns ----

def observed(turns: list[dict]) -> list[dict]:
    """The compactions in one chat: a turn whose context fell under 60% of the last turn's (over 50k)."""
    events, last = [], 0
    for index in range(1, len(turns)):
        before, after = cost._context(turns[index - 1]), cost._context(turns[index])
        if before > DROP_FROM and after < DROP_TO * before:
            events.append({"index": index, "family": _family(turns[index - 1]), "before": before, "after": after, "between": index - last})
            last = index
    return events


def observed_stats(sessions: list[dict]) -> dict:
    """Per family: how often chats compacted, the median context before and after, the median turns
    between compactions (counted from the previous one or the start) and the median growth per turn."""
    events = {family: [] for family in FAMILIES}
    growth = {family: [] for family in FAMILIES}
    turns = Counter()
    for session in sessions:
        chat = session["turns"]
        found = observed(chat)
        marks = {event["index"] for event in found}
        for event in found:
            events[event["family"]].append(event)
        for index, turn in enumerate(chat):
            turns[_family(turn)] += 1
            if index and index not in marks:
                growth[_family(turn)].append(max(0, cost._context(turn) - cost._context(chat[index - 1])))
    return {family: {
        "turns": turns[family], "compactions": len(events[family]),
        "before": _median(event["before"] for event in events[family]),
        "after": _median(event["after"] for event in events[family]) if events[family] else float(cost.KEPT_AFTER_COMPACTION),
        "between": _median(event["between"] for event in events[family]), "growth": _median(growth[family])}
        for family in FAMILIES}


# ---- the rollover what-if ----

def replay(turns: list[dict], prices: dict) -> dict:
    """The stored tokens of these turns at the family prices, per family."""
    out = {}
    for turn in turns:
        out[_family(turn)] = out.get(_family(turn), 0.0) + dollars(turn, _price(prices, turn, cost._context(turn)))
    return out


def summary_call(context: float, family: str, price: tuple) -> float:
    """One model call that reads the whole context as plain input and writes a summary."""
    return context * SUMMARY_WEIGHT[family] * price[0] + cost.SUMMARY_OUTPUT * price[3]


def simulate(turns: list[dict], threshold: float, stats: dict, prices: dict) -> dict:
    """The same turns with a simulated context that rolls over once it reaches the threshold.

    Each turn adds the growth the stored turn really added (the family's median at an observed compaction).
    A stored cold turn that was not a compaction stays cold: it writes the whole context. Other turns
    read the context and write the growth. After the threshold the next turn starts from the family's
    median post-compaction context, written whole. Per family: turn cost, what the rollover summaries
    would cost if a model wrote them, and the number of rollovers."""
    runs: dict[str, dict] = {}
    marks = {event["index"] for event in observed(turns)}
    context = real_before = 0
    rolled, family_before, price_before = False, None, None
    for index, turn in enumerate(turns):
        family = _family(turn)
        run = runs.setdefault(family, {"turns": 0.0, "summary": 0.0, "rollovers": 0})
        real = cost._context(turn)
        if index == 0:
            now = real
            price = _price(prices, turn, now)
            spent = dollars(turn, price)
        else:
            growth = stats[family]["growth"] if index in marks else max(0, real - real_before)
            restart = rolled
            cold = not rolled and cost.is_cold(turn) and index not in marks and cost.cause_of(turn) != "after compaction"
            now = stats[family]["after"] if restart else context + growth
            price = _price(prices, turn, now)
            if restart or cold:
                spent = now * price[2] + turn["output"] * price[3]
            else:
                spent = context * price[1] + growth * price[2] + turn["output"] * price[3]
            if restart:
                runs[family_before]["rollovers"] += 1
                runs[family_before]["summary"] += summary_call(context, family_before, price_before)
        run["turns"] += spent
        context, real_before, family_before, price_before = now, real, family, price
        rolled = context >= threshold
    return runs


def _sum_runs(runs: list[dict], family: str | None, key: str) -> float:
    return sum(run[key] for result in runs for name, run in result.items() if family in (None, name))


def _group_row(sessions, indexes, family, base, thresholds, runs) -> dict:
    replayed = sum(base[i].get(family, 0.0) if family else sum(base[i].values()) for i in indexes)
    seen = sum(sessions[i]["summaries"].get(family, 0.0) if family else sum(sessions[i]["summaries"].values()) for i in indexes)
    rows = []
    for threshold in thresholds:
        chosen = [runs[threshold][i] for i in indexes]
        turns, summary = _sum_runs(chosen, family, "turns"), _sum_runs(chosen, family, "summary")
        for variant, total in zip(VARIANTS, (turns, turns + summary)):
            rows.append({"threshold": threshold, "variant": variant, "cost": total, "rollovers": int(_sum_runs(chosen, family, "rollovers")),
                         "change": total - replayed, "percent": (total - replayed) / replayed * 100 if replayed else 0.0,
                         "change_real": total - replayed - seen,
                         "percent_real": (total - replayed - seen) / (replayed + seen) * 100 if replayed + seen else 0.0})
    best = {variant: min((row for row in rows if row["variant"] == variant), key=lambda row: row["cost"]) for variant in VARIANTS} if rows else {}
    if family:
        indexes = [i for i in indexes if family in base[i]]
    turns = sum(1 for i in indexes for turn in sessions[i]["turns"] if family in (None, _family(turn)))
    return {"sessions": len(indexes), "turns": turns,
            "replayed": replayed, "observed_summaries": seen, "rows": rows, "best": best}


def rollover_estimate(stats: dict, prices: dict) -> dict:
    """Turns between rollovers that cost least: with a context growing g tokens a turn and read at the cache
    read price, N = sqrt(2R / (read price x g)), R being what one rollover costs (the post-compaction
    context written instead of read, plus the summary call for a model summary at that context)."""
    out = {}
    for family in INPUT_PRICE:
        entry, price = stats[family], prices[family]
        if not entry["turns"] or entry["growth"] <= 0 or price[1] <= 0:
            continue
        out[family] = {"growth": entry["growth"], "after": entry["after"]}
        for variant in VARIANTS:
            turns = 100.0
            for _ in range(50):
                at = entry["after"] + entry["growth"] * turns
                rollover = entry["after"] * (price[2] - price[1]) + (summary_call(at, family, price) if variant == "model summary" else 0.0)
                turns = math.sqrt(2 * rollover / (price[1] * entry["growth"]))
            out[family][variant] = {"turns": turns, "context": entry["after"] + entry["growth"] * turns, "rollover_cost": rollover}
    return out


def rollover(sessions: list[dict], thresholds=THRESHOLDS) -> dict:
    thresholds = sorted(set(thresholds))
    every = [turn for session in sessions for turn in session["turns"]]
    prices, stats = price_table(every), observed_stats(sessions)
    base = [replay(session["turns"], prices) for session in sessions]
    runs = {threshold: [simulate(session["turns"], threshold, stats, prices) for session in sessions] for threshold in thresholds}
    stored = sum(turn["cost"] for turn in every)
    replayed = sum(sum(part.values()) for part in base)
    workers = [i for i, session in enumerate(sessions) if session["kind"] == "worker"]
    present = [family for family in FAMILIES if any(family in base[i] for i in workers)]
    groups = {"all workers": _group_row(sessions, workers, None, base, thresholds, runs)}
    for family in present:
        groups[f"{family} workers"] = _group_row(sessions, workers, family, base, thresholds, runs)
    groups["workers that compacted"] = _group_row(sessions, [i for i in workers if observed(sessions[i]["turns"])], None, base, thresholds, runs)
    groups["long workers"] = _group_row(sessions, [i for i in workers if sessions[i]["long"]], None, base, thresholds, runs)
    groups["root chat"] = _group_row(sessions, [i for i, session in enumerate(sessions) if session["kind"] == "root"], None, base, thresholds, runs)
    return {
        "check": {"replayed": replayed, "stored": stored, "percent": (replayed - stored) / stored * 100 if stored else 0.0,
                  "sessions": len(sessions), "turns": len(every),
                  "kinds": {kind: {"replayed": sum(sum(base[i].values()) for i, x in enumerate(sessions) if x["kind"] == kind),
                                   "stored": sum(t["cost"] for x in sessions if x["kind"] == kind for t in x["turns"])} for kind in ("worker", "root")},
                  "families": {family: {"replayed": sum(part.get(family, 0.0) for part in base),
                                        "stored": sum(turn["cost"] for turn in every if _family(turn) == family)} for family in FAMILIES}},
        "summaries": {"cost": sum(sum(s["summaries"].values()) for s in sessions), "calls": sum(s["summary_calls"] for s in sessions),
                      "not_stored": sum(s["billed"] - sum(t["cost"] for t in s["turns"]) - sum(s["summaries"].values()) for s in sessions)},
        "prices": {family: list(price) for family, price in prices.items()}, "stats": stats,
        "thresholds": thresholds, "groups": groups, "estimate": rollover_estimate(stats, prices),
    }


# ---- cache use ----

def cache_split(turns: list[dict], prices: dict) -> dict:
    tokens = {key: sum(turn[key] for turn in turns) for key in ("input", "read", "write", "output")}
    split = {"input": 0.0, "read": 0.0, "write": 0.0, "output": 0.0}
    for turn in turns:
        for key, price in zip(("input", "read", "write", "output"), _price(prices, turn, cost._context(turn))):
            split[key] += turn[key] * price
    context = tokens["input"] + tokens["read"] + tokens["write"]
    return {"turns": len(turns), "tokens": tokens, "read_share": tokens["read"] / context * 100 if context else 0.0,
            "stored": sum(turn["cost"] for turn in turns), "dollars": split}


def cold_causes(turns: list[dict]) -> dict:
    out = {name: {"turns": 0, "cost": 0.0} for name in cost.CAUSES}
    for turn in turns:
        if cost.is_cold(turn):
            entry = out[cost.cause_of(turn)]
            entry["turns"] += 1
            entry["cost"] += turn["cost"]
    return {name: entry for name, entry in out.items() if entry["turns"]}


# ---- the pause rule: roll over to a small context instead of resuming after a long pause ----

def pause_turn(turn: dict, prices: dict, stats: dict) -> dict:
    """What one cold turn that followed a pause cost, and what it would have cost after a rollover (the
    context cut to the family's observed median after a compaction, or to 30k) with no summary call. A
    rollover to a context at least as large as the turn's own changes nothing. 'warm' is the cost had a
    one-hour cache (cost._kept_warm) held the context through a pause of up to an hour, before the higher
    price of writing to it."""
    size = cost._context(turn)
    out = {"cost": turn["cost"], "gap": turn["gap"], "context": turn.get("before") or size}

    def written(context: float) -> float:
        price = _price(prices, turn, context)
        return turn["input"] * price[0] + context * price[2] + turn["output"] * price[3]
    for name, context in (("median", stats[_family(turn)]["after"]), ("small", SMALL_CONTEXT)):
        out[name] = min(turn["cost"], written(context)) if context < size else turn["cost"]
    held = cost._kept_warm(turn)
    out["warm"] = min(turn["cost"], dollars(held, _price(prices, turn, size))) if held is not turn else turn["cost"]
    return out


def _pause_row(rows: list[dict]) -> dict:
    spent = sum(row["cost"] for row in rows)
    return {"turns": len(rows), "cost": spent,
            "avoided_median": spent - sum(row["median"] for row in rows), "avoided_small": spent - sum(row["small"] for row in rows),
            "avoided_warm": spent - sum(row["warm"] for row in rows)}


def pause_rollover(turns: list[dict], prices: dict, stats: dict) -> dict:
    """The cold turns that followed a pause (over the cache lifetime), split by pause length and by the
    context before the pause, and the rule 'roll over when the context is over X' for several X."""
    rows = [pause_turn(turn, prices, stats) for turn in turns if cost.is_cold(turn) and cost.cause_of(turn) in cost.PAUSE_CAUSES]
    return {"all": _pause_row(rows),
            "by_pause": {label: _pause_row([r for r in rows if low <= r["gap"] < high]) for low, high, label in PAUSE_BANDS},
            "by_context": {label: _pause_row([r for r in rows if low <= r["context"] < high]) for low, high, label in CONTEXT_BANDS},
            "over": {str(over): _pause_row([r for r in rows if r["context"] > over]) for over in ROLLOVER_OVER}}


def recommend_over(chat: dict, workers: dict) -> dict:
    """The lowest X in the grid such that going down to it still adds rollovers that avoid at least
    EXTRA_ROLLOVER dollars each (the chat's and the workers' pause turns together, median after-context)."""
    grid = sorted(ROLLOVER_OVER, reverse=True)
    rows = {over: (chat["over"][str(over)], workers["over"][str(over)]) for over in grid}
    chosen = grid[0]
    for high, low in zip(grid, grid[1:]):
        turns = sum(r["turns"] for r in rows[low]) - sum(r["turns"] for r in rows[high])
        gained = sum(r["avoided_median"] for r in rows[low]) - sum(r["avoided_median"] for r in rows[high])
        if turns and gained / turns < EXTRA_ROLLOVER:
            break
        chosen = low
    return {"over": chosen, "turns": sum(r["turns"] for r in rows[chosen]),
            "avoided_median": sum(r["avoided_median"] for r in rows[chosen]), "avoided_small": sum(r["avoided_small"] for r in rows[chosen]),
            "extra": EXTRA_ROLLOVER}


def cache(sessions: list[dict]) -> dict:
    prices = price_table([turn for session in sessions for turn in session["turns"]])
    stats = observed_stats(sessions)
    groups: dict[str, list[dict]] = {}
    for session in sessions:
        groups.setdefault("chat (root)" if session["kind"] == "root" else session["agent"], []).append(session)
    agents = {}
    for name, part in sorted(groups.items(), key=lambda item: (item[0] != "chat (root)", -sum(t["cost"] for s in item[1] for t in s["turns"]))):
        agents[name] = {"sessions": len(part), **cache_split([t for s in part for t in s["turns"]], prices)}
    worker_turns = [t for s in sessions if s["kind"] == "worker" for t in s["turns"]]
    root_turns = [t for s in sessions if s["kind"] == "root" for t in s["turns"]]
    pauses = {"workers": pause_rollover(worker_turns, prices, stats), "root": pause_rollover(root_turns, prices, stats)}
    return {"agents": agents, "workers": {"sessions": sum(s["kind"] == "worker" for s in sessions), **cache_split(worker_turns, prices)},
            "cold": {"workers": cold_causes(worker_turns), "root": cold_causes(root_turns)},
            "pauses": {**pauses, "recommended": recommend_over(pauses["root"], pauses["workers"]),
                       "after": {family: entry["after"] for family, entry in stats.items() if entry["turns"]},
                       "small": SMALL_CONTEXT}}


# ---- where growth comes from ----

def growth_values(turns: list[dict]) -> list[int]:
    """Context a turn added to the last one (never negative); compactions are left out."""
    marks = {event["index"] for event in observed(turns)}
    return [max(0, cost._context(turns[i]) - cost._context(turns[i - 1])) for i in range(1, len(turns)) if i not in marks]


def growth_summary(values: list[int]) -> dict:
    total = sum(values)
    ordered = sorted(values, reverse=True)
    top = ordered[:max(1, math.ceil(len(ordered) * TOP_SHARE))] if ordered else []
    big = [value for value in values if value > BIG_TURN]
    return {"turns": len(values), "total": total, "p50": percentile(values, 0.5), "p90": percentile(values, 0.9), "p99": percentile(values, 0.99),
            "top_turns": len(top), "top_share": sum(top) / total * 100 if total else 0.0,
            "big_turns": len(big), "big_share": sum(big) / total * 100 if total else 0.0}


def tool_summary(rows: list[tuple]) -> list[dict]:
    """Per tool name: calls and output size from (name, size) rows, biggest total first."""
    sizes: dict[str, list[int]] = {}
    for name, size in rows:
        sizes.setdefault(name if isinstance(name, str) and name else "unknown", []).append(int(size or 0))
    total = sum(sum(part) for part in sizes.values())
    return sorted(({"tool": name, "calls": len(part), "total": sum(part), "mean": sum(part) / len(part), "largest": max(part),
                    "share": sum(part) / total * 100 if total else 0.0} for name, part in sizes.items()),
                  key=lambda entry: (-entry["total"], entry["tool"]))


def growth(sessions: list[dict], tools: list[tuple] | None) -> dict:
    workers = [s for s in sessions if s["kind"] == "worker"]
    long = [s for s in workers if s["long"]]
    out = {"long workers": {"sessions": len(long), **growth_summary([v for s in long for v in growth_values(s["turns"])])},
           "all workers": {"sessions": len(workers), **growth_summary([v for s in workers for v in growth_values(s["turns"])])},
           "tools": None}
    if tools:
        turns = sum(len(s["turns"]) for s in workers)
        out["tools"] = {"calls_per_turn": len(tools) / turns if turns else 0.0, "by_tool": tool_summary(tools),
                        "total_size": sum(int(size or 0) for _, size in tools), "unit": "characters"}
    return out


# ---- reading the database ----

def _summaries(db, session: str) -> tuple[dict, int]:
    """The cost of the summary (compaction) calls of one chat, per model family; they are stored as
    their own messages, apart from the assistant turns."""
    rows = db.execute("SELECT json_extract(data, '$.model.id'), json_extract(data, '$.cost') FROM session_message "
                      "WHERE session_id=? AND type='compaction' AND json_valid(data)", (session,)).fetchall()
    out: dict[str, float] = {}
    for model, spent in rows:
        out[family_of(model)] = out.get(family_of(model), 0.0) + cost._number(spent)
    return out, len(rows)


def _tools(db, session: str) -> list[tuple]:
    """(tool name, output size in characters) for every tool call of one chat. The size is measured in
    SQL; the output text itself never leaves the database."""
    try:
        return db.execute(
            "SELECT json_extract(c.value, '$.name'), "
            "(SELECT coalesce(sum(coalesce(length(json_extract(r.value, '$.text')), length(r.value))), 0) "
            " FROM json_each(c.value, '$.state.content') r) + coalesce(length(json_extract(c.value, '$.state.error')), 0) "
            "FROM session_message m, json_each(CASE WHEN json_valid(m.data) THEN m.data ELSE '{}' END, '$.content') c "
            "WHERE m.session_id=? AND m.type='assistant' AND json_extract(c.value, '$.type')='tool'", (session,)).fetchall()
    except sqlite3.Error:
        return []


def load_sessions(session: str | None = None, db_path: Path = sources.DB_PATH, long_turns: int = cost.LONG_TURNS,
                  long_cost: float = cost.LONG_COST, with_tools: bool = False) -> tuple[str, list[dict], list[tuple] | None]:
    """The chat (default: the root chat used most recently) and the worker sessions it started, with
    their turns and summary calls; and the worker tool calls when asked for."""
    with closing(cost._connect(db_path)) as db:
        if session is None:
            row = db.execute("SELECT id FROM session_v2 WHERE parent_id IS NULL ORDER BY time_updated DESC LIMIT 1").fetchone()
            if row is None:
                raise SystemExit("no chat found")
            session = row[0]
        columns = {row[1] for row in db.execute("PRAGMA table_info(session_v2)")}
        agent = "agent" if "agent" in columns else "NULL"
        root = db.execute("SELECT id, cost, " + agent + " FROM session_v2 WHERE id=?", (session,)).fetchone() or (session, 0, None)
        children = db.execute(f"SELECT id, cost, {agent} FROM session_v2 WHERE parent_id=? ORDER BY id", (session,)).fetchall()
        sessions, tools = [], [] if with_tools else None
        for kind, (ident, billed, name) in [("root", root)] + [("worker", child) for child in children]:
            turns = cost._read_turns(db, ident)
            summaries, calls = _summaries(db, ident)
            billed = cost._number(billed)
            sessions.append({"id": ident, "kind": kind, "agent": name if isinstance(name, str) and name else "unknown", "turns": turns,
                             "billed": billed, "summaries": summaries, "summary_calls": calls,
                             "long": kind == "worker" and (len(turns) > long_turns or billed > long_cost)})
            if with_tools and kind == "worker":
                tools += _tools(db, ident)
    return session, sessions, tools


# ---- text reports ----

def _k(tokens: float) -> str:
    return f"{int(round(tokens / 1000))}k"


def _signed(value: float) -> str:
    return f"{'-' if value < 0 else '+'}${abs(value):,.2f}"


def rollover_report(session: str, result: dict, thresholds=None) -> str:
    check, summaries = result["check"], result["summaries"]
    lines = [f"Replay check: replayed ${check['replayed']:,.2f} against stored message cost ${check['stored']:,.2f} "
             f"({check['percent']:+.2f}%) over {check['sessions']} sessions (the chat and its workers), {check['turns']} turns",
             "    by session set: " + "; ".join(f"{name} ${check['kinds'][kind]['replayed']:,.2f} against ${check['kinds'][kind]['stored']:,.2f} "
                                                   f"({(check['kinds'][kind]['replayed'] / check['kinds'][kind]['stored'] - 1) * 100 if check['kinds'][kind]['stored'] else 0:+.2f}%)"
                                                   for kind, name in (("worker", "workers"), ("root", "chat"))),
             "    by family: " + "; ".join(f"{family} ${entry['replayed']:,.2f} against ${entry['stored']:,.2f}"
                                            + (" (priced from its own stored cost)" if family == "other" else "")
                                            for family, entry in check["families"].items() if entry["stored"]),
             f"{session}: what other rollover (compaction) points would have cost.",
             "Assumptions: same turns and same task result; context after a rollover equals the observed median.",
             f"Summary calls are stored as their own messages, not in the turn costs: {summaries['calls']} calls, ${summaries['cost']:,.2f} found; "
             f"a further ${summaries['not_stored']:,.2f} of the bills is {cost.UNREPORTED} (left out). "
             "'vs replay' compares turn cost to turn cost; 'vs real' adds the found summary calls to the replay.",
             "", "Observed compactions (a turn under 60% of the last turn's context, which was over 50k)",
             f"    {'':<8}{'turns':>7}{'compactions':>13}{'median before':>15}{'after':>8}{'turns between':>15}{'growth a turn':>15}"]
    for family, entry in result["stats"].items():
        if entry["turns"]:
            lines.append(f"    {family:<8}{entry['turns']:>7}{entry['compactions']:>13}{_k(entry['before']):>15}{_k(entry['after']):>8}"
                         f"{entry['between']:>15.0f}{entry['growth']:>15,.0f}")
    for name, group in result["groups"].items():
        if not group["sessions"]:
            continue
        lines += ["", f"{name}: {group['sessions']} sessions, {group['turns']} turns; replay ${group['replayed']:,.2f}, "
                      f"summary calls found ${group['observed_summaries']:,.2f}",
                  f"    {'threshold':<11}{'variant':<15}{'simulated':>11}{'vs replay':>13}{'':>9}{'vs real':>13}{'':>9}{'rollovers':>10}"]
        for row in group["rows"]:
            lines.append(f"    {_k(row['threshold']):<11}{row['variant']:<15}${row['cost']:>10,.2f}{_signed(row['change']):>13}{row['percent']:>+8.1f}%"
                         f"{_signed(row['change_real']):>13}{row['percent_real']:>+8.1f}%{row['rollovers']:>10}")
        for variant, row in group["best"].items():
            lines.append(f"    lowest for {variant}: {_k(row['threshold'])}, ${row['cost']:,.2f} ({_signed(row['change'])} vs replay, {_signed(row['change_real'])} vs real)")
    lines += ["", "Turns between rollovers that cost least ~ sqrt(2R / (read price x g)); g = median growth a turn, R = cost of one rollover"]
    for family, entry in result["estimate"].items():
        for variant in VARIANTS:
            part = entry[variant]
            lines.append(f"    {family:<8}{variant:<15}g {entry['growth']:,.0f}  R ${part['rollover_cost']:.2f}  about {part['turns']:.0f} turns, "
                         f"rolling over near {_k(part['context'])}")
    return "\n".join(lines)


def cache_report(session: str, result: dict) -> str:
    def money(value, total):
        return f"${value:>8,.2f} {value / total * 100 if total else 0:>3.0f}%  "
    lines = [f"{session}: cache use per agent (worker sessions grouped by agent, and the chat itself)",
             f"    {'':<16}{'sessions':>8}{'turns':>7}{'cache read':>12}   message cost split (replayed from the stored tokens)",
             f"    {'':<16}{'':>8}{'':>7}{'of input':>12}   {'cache read':<16}{'cache write':<16}{'output':<16}{'plain input':<16}"]
    entries = list(result["agents"].items()) + [("all workers", result["workers"])]
    for name, entry in entries:
        split, total = entry["dollars"], sum(entry["dollars"].values())
        lines.append(f"    {name[:15]:<16}{entry['sessions']:>8}{entry['turns']:>7}{entry['read_share']:>11.1f}%   "
                     f"{money(split['read'], total)}{money(split['write'], total)}{money(split['output'], total)}{money(split['input'], total)}")
    cold = result["cold"]
    names = [name for name in cost.CAUSES if name in cold["workers"] or name in cold["root"]]
    lines += ["", "Cold turns (most of the context written again) by cause",
              f"    {'':<28}{'worker turns':>13}{'cost':>10}{'chat turns':>13}{'cost':>10}"]
    for name in names:
        w, r = cold["workers"].get(name, {"turns": 0, "cost": 0.0}), cold["root"].get(name, {"turns": 0, "cost": 0.0})
        lines.append(f"    {name:<28}{w['turns']:>13}  ${w['cost']:>7.2f}{r['turns']:>13}  ${r['cost']:>7.2f}")
    w = sum(e["turns"] for e in cold["workers"].values()), sum(e["cost"] for e in cold["workers"].values())
    r = sum(e["turns"] for e in cold["root"].values()), sum(e["cost"] for e in cold["root"].values())
    lines.append(f"    {'total':<28}{w[0]:>13}  ${w[1]:>7.2f}{r[0]:>13}  ${r[1]:>7.2f}")
    return "\n".join(lines + pause_report(result))


def pause_report(result: dict) -> list[str]:
    pauses = result["pauses"]
    after = ", ".join(f"{family} {_k(size)}" for family, size in pauses["after"].items())
    lines = ["", "Cold turns after a pause, if the session had rolled over to a small context before resuming (no summary call; only the resumed turn is counted)",
             f"    context kept: the family's median after a compaction ({after}), or {_k(pauses['small'])}; "
             "'one-hour cache' is what a cache that lasts an hour would have saved on pauses up to an hour (before its higher write price)"]
    head = f"    {'':<22}{'turns':>6}{'cost':>10}{'avoided, median':>17}{'avoided, ' + _k(pauses['small']):>15}{'one-hour cache':>16}"
    for kind, name in (("root", "chat"), ("workers", "workers")):
        entry = pauses[kind]
        lines += ["", f"  {name}", head]
        groups = [("all", entry["all"])] + [(f"pause {label}", row) for label, row in entry["by_pause"].items()]
        groups += [(f"context {label}", row) for label, row in entry["by_context"].items()]
        groups += [(f"context over {_k(int(over))}", row) for over, row in entry["over"].items()]
        for label, row in groups:
            lines.append(f"    {label:<22}{row['turns']:>6}  ${row['cost']:>7.2f}  ${row['avoided_median']:>14.2f}  ${row['avoided_small']:>12.2f}  ${row['avoided_warm']:>13.2f}")
    best = pauses["recommended"]
    lines += ["", f"  Roll over before resuming when the pause was over the cache lifetime and the context is over {_k(best['over'])}: "
                  f"{best['turns']} turns, ${best['avoided_median']:.2f} avoided at the median context, ${best['avoided_small']:.2f} at {_k(pauses['small'])} "
                  f"(chat and workers together; lowering X further adds rollovers that avoid under ${best['extra']:.2f} each)"]
    return lines


def growth_report(session: str, result: dict) -> str:
    lines = [f"{session}: where context growth comes from (growth = context added by a turn over the last; compaction turns left out)"]
    for name in ("long workers", "all workers"):
        entry = result[name]
        lines += ["", f"{name}: {entry['sessions']} sessions, {entry['turns']} turns, {entry['total']:,} tokens of growth",
                  f"    per turn: p50 {entry['p50']:,.0f}  p90 {entry['p90']:,.0f}  p99 {entry['p99']:,.0f}",
                  f"    the largest 5% of turns ({entry['top_turns']}) add {entry['top_share']:.1f}% of all growth",
                  f"    turns over {BIG_TURN:,} tokens ({entry['big_turns']}) add {entry['big_share']:.1f}%"]
    tools = result["tools"]
    if not tools:
        lines += ["", "Tools: the database has no usable tool data for these workers, so only the percentiles are shown."]
        return "\n".join(lines)
    lines += ["", f"Tool output in the worker sessions (names and sizes only; size in {tools['unit']}, about 4 to a token): "
                  f"{tools['total_size']:,} in total, {tools['calls_per_turn']:.2f} calls a turn",
              f"    {'tool':<24}{'calls':>8}{'total size':>14}{'mean':>10}{'largest':>12}{'share':>8}"]
    for entry in tools["by_tool"]:
        lines.append(f"    {entry['tool'][:23]:<24}{entry['calls']:>8}{entry['total']:>14,}{entry['mean']:>10,.0f}{entry['largest']:>12,}{entry['share']:>7.1f}%")
    return "\n".join(lines)


def build(session: str, sessions: list[dict], tools, wanted: set[str], thresholds=THRESHOLDS) -> dict:
    """The numbers of the requested reports, by name."""
    out = {}
    if "rollover" in wanted:
        out["rollover"] = rollover(sessions, thresholds)
    if "cache" in wanted:
        out["cache"] = cache(sessions)
    if "growth" in wanted:
        out["growth"] = growth(sessions, tools)
    return out


def text(session: str, built: dict) -> str:
    parts = []
    if "rollover" in built:
        parts.append(rollover_report(session, built["rollover"]))
    if "cache" in built:
        parts.append(cache_report(session, built["cache"]))
    if "growth" in built:
        parts.append(growth_report(session, built["growth"]))
    return "\n\n".join(parts)
