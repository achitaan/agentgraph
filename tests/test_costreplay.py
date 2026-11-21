import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentgraph import cost, costreplay as replay

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
PRICE = replay.price_table([])  # Opus and Sonnet prices are fixed


def turn(read=0, write=0, output=1000, tokens_in=0, gap=0.5, between=(), model=OPUS, **extra):
    made = {"input": tokens_in, "read": read, "write": write, "output": output, "gap": gap, "between": list(between), "model": model, "cost": 0.0, **extra}
    made["cost"] = replay.dollars(made, PRICE.get(replay.family_of(model), PRICE["opus"]))
    return made


def session(turns, kind="worker", agent="impl-claude", summaries=None, long=False, billed=0.0):
    return {"id": "ses_x", "kind": kind, "agent": agent, "turns": turns, "billed": billed, "summaries": summaries or {},
            "summary_calls": len(summaries or {}), "long": long}


def growing(count, step=2_000, start=20_000, model=OPUS):
    """A warm chat: each turn reads the last context and writes the step."""
    return [turn(write=start, model=model)] + [turn(read=start + step * n, write=step, model=model) for n in range(count - 1)]


def stats(**override):
    base = {family: {"turns": 1, "compactions": 0, "before": 0.0, "after": 40_000.0, "between": 0.0, "growth": 2_000.0} for family in replay.FAMILIES}
    for family, values in override.items():
        base[family].update(values)
    return base


class ReplayTests(unittest.TestCase):
    def test_families_and_prices(self):
        self.assertEqual([replay.family_of(m) for m in (OPUS, SONNET, "claude-opus-5-5-fast", "claude-fable-5-2", None, "")],
                         ["opus", "sonnet", "other", "other", "other", "other"])
        # read is a twentieth of plain input, write 1.25 times, output 5 times
        self.assertEqual(PRICE["opus"], (4e-6, 4e-6 * 0.05, 4e-6 * 1.25, 4e-6 * 5))

    def test_a_replay_with_no_rule_changed_equals_the_stored_cost(self):
        turns = growing(6) + [turn(write=300_000, gap=12), turn(read=5_000, write=1_000, model=SONNET, tokens_in=40)]
        prices = replay.price_table(turns)
        total = sum(sum(part.values()) for part in [replay.replay(turns, prices)])
        self.assertAlmostEqual(total, sum(t["cost"] for t in turns))
        self.assertEqual(set(replay.replay(turns, prices)), {"opus", "sonnet"})

    def test_other_models_are_priced_from_their_own_stored_cost(self):
        turns = []
        for n in range(12):
            t = turn(read=10_000 * n, write=3_000, model="some-model")
            t["cost"] = (t["input"] + 0.025 * t["read"] + 1.25 * t["write"] + 5 * t["output"]) * 8e-6
            turns.append(t)
        prices = replay.price_table(turns)
        self.assertAlmostEqual(prices["some-model"]["low"][0], 8e-6)
        self.assertAlmostEqual(prices["some-model"]["low"][1], 8e-6 * 0.025)
        self.assertAlmostEqual(sum(replay.replay(turns, prices).values()), sum(t["cost"] for t in turns))

    def test_compactions_are_found_by_the_drop_in_context(self):
        turns = [turn(write=60_000), turn(read=60_000, write=10_000), turn(write=40_000),    # 70k to 40k: under 60%
                 turn(read=40_000, write=2_000), turn(read=42_000, write=2_000),
                 turn(write=20_000),                                                        # 44k to 20k, but 44k is not a long way up
                 turn(read=20_000, write=40_000), turn(read=60_000, write=1_000)]
        found = replay.observed(turns)
        self.assertEqual([(e["index"], e["before"], e["after"], e["between"]) for e in found], [(2, 70_000, 40_000, 2), (5, 44_000, 20_000, 3)][:1])
        self.assertEqual(replay.observed([turn(write=100_000), turn(read=100_000, write=1_000)]), [])
        self.assertEqual(replay.observed([]), [])
        # exactly 60% is not a compaction
        self.assertEqual(replay.observed([turn(write=100_000), turn(write=60_000)]), [])

    def test_the_observed_numbers_are_medians_per_family(self):
        chat = ([turn(write=30_000)] + [turn(read=30_000 + 20_000 * n, write=20_000) for n in range(9)]   # grows to 210k
                + [turn(write=50_000)] + [turn(read=50_000, write=1_000)] * 3)
        found = replay.observed_stats([session(chat)])
        self.assertEqual((found["opus"]["compactions"], found["opus"]["before"], found["opus"]["after"], found["opus"]["between"]), (1, 210_000, 50_000, 10))
        self.assertEqual(found["opus"]["growth"], 20_000 if False else found["opus"]["growth"])
        self.assertEqual(found["sonnet"]["compactions"], 0)
        self.assertEqual(found["sonnet"]["after"], cost.KEPT_AFTER_COMPACTION)   # nothing seen: the default

    def test_a_threshold_nothing_reaches_changes_no_context(self):
        turns = growing(10)
        run = replay.simulate(turns, 10**9, stats(), PRICE)["opus"]
        self.assertEqual((run["rollovers"], run["summary"]), (0, 0.0))
        p = PRICE["opus"]
        expected = replay.dollars(turns[0], p) + sum((20_000 + 2_000 * n) * p[1] + 2_000 * p[2] + 1000 * p[3] for n in range(9))
        self.assertAlmostEqual(run["turns"], expected)
        # with the same growth as stored, this is the stored cost
        self.assertAlmostEqual(run["turns"], sum(t["cost"] for t in turns))

    def test_a_threshold_under_every_context_rolls_over_on_every_turn(self):
        turns = growing(5)
        run = replay.simulate(turns, 1, stats(), PRICE)["opus"]
        p = PRICE["opus"]
        self.assertEqual(run["rollovers"], 4)
        self.assertAlmostEqual(run["turns"], replay.dollars(turns[0], p) + 4 * (40_000 * p[2] + 1000 * p[3]))

    def test_code_and_model_summaries_differ_by_exactly_the_summary_calls(self):
        sessions = [session(growing(30))]
        result = replay.rollover(sessions, (60_000,))
        group = result["groups"]["all workers"]
        code, model = group["rows"]
        self.assertEqual((code["variant"], model["variant"], code["threshold"]), ("code summary", "model summary", 60_000))
        run = replay.simulate(sessions[0]["turns"], 60_000, result["stats"], replay.price_table(sessions[0]["turns"]))["opus"]
        self.assertGreater(run["rollovers"], 0)
        self.assertAlmostEqual(model["cost"] - code["cost"], run["summary"])
        # each summary reads the context at the rollover (60k to 61.99k here) at 1.06 times plain input, and writes 4000 tokens
        call = replay.summary_call(62_000, "opus", PRICE["opus"])
        self.assertAlmostEqual(call, 62_000 * 1.06 * 4e-6 + 4_000 * 20e-6)
        self.assertGreater(run["summary"], run["rollovers"] * replay.summary_call(60_000, "opus", PRICE["opus"]) - 1e-9)
        self.assertEqual(code["rollovers"], run["rollovers"])

    def test_sonnet_summaries_weigh_more_per_context_token(self):
        self.assertGreater(replay.summary_call(100_000, "sonnet", PRICE["sonnet"]) / PRICE["sonnet"][0],
                           replay.summary_call(100_000, "opus", PRICE["opus"]) / PRICE["opus"][0] - 1)
        self.assertAlmostEqual(replay.summary_call(100_000, "sonnet", PRICE["sonnet"]), 100_000 * 1.16 * 2e-6 + 4_000 * 10e-6)

    def test_a_turn_that_was_cold_in_the_stored_data_stays_cold(self):
        warm = growing(6, start=60_000)
        cold = list(warm)
        cold[3] = turn(write=warm[3]["read"] + warm[3]["write"], gap=12)   # pause: the whole context written again
        self.assertTrue(cost.is_cold(cold[3]))
        base = replay.simulate(warm, 10**9, stats(), PRICE)["opus"]["turns"]
        after = replay.simulate(cold, 10**9, stats(), PRICE)["opus"]["turns"]
        p = PRICE["opus"]
        context = 60_000 + 2_000 * 3
        self.assertAlmostEqual(after - base, context * p[2] - ((context - 2_000) * p[1] + 2_000 * p[2]))
        self.assertGreater(after, base)

    def test_a_cold_turn_after_compaction_is_not_kept_cold(self):
        turns = growing(4)
        turns[2] = turn(write=turns[2]["read"] + turns[2]["write"], between=["compaction"], gap=12)
        self.assertEqual(replay.simulate(turns, 10**9, stats(), PRICE)["opus"]["turns"],
                         replay.simulate(growing(4), 10**9, stats(), PRICE)["opus"]["turns"])

    def test_an_observed_compaction_uses_the_median_growth(self):
        chat = [turn(write=100_000), turn(read=100_000, write=2_000), turn(write=40_000, between=["compaction"], gap=12), turn(read=40_000, write=2_000)]
        run = replay.simulate(chat, 10**9, stats(opus={"growth": 3_000}), PRICE)["opus"]
        p = PRICE["opus"]
        self.assertAlmostEqual(run["turns"], replay.dollars(chat[0], p) + (100_000 * p[1] + 2_000 * p[2] + 1000 * p[3])
                               + (102_000 * p[1] + 3_000 * p[2] + 1000 * p[3]) + (105_000 * p[1] + 2_000 * p[2] + 1000 * p[3]))

    def test_the_rollover_report_compares_against_the_replay_and_the_observed_summaries(self):
        sessions = [session(growing(40), summaries={"opus": 1.5}), session(growing(20, model=SONNET), kind="root", agent="unknown")]
        result = replay.rollover(sessions, (50_000, 80_000))
        self.assertAlmostEqual(result["check"]["percent"], 0.0, places=6)
        self.assertEqual(set(result["groups"]), {"all workers", "opus workers", "workers that compacted", "long workers", "root chat"})
        group = result["groups"]["all workers"]
        self.assertAlmostEqual(group["observed_summaries"], 1.5)
        row = group["rows"][0]
        self.assertAlmostEqual(row["change"], row["cost"] - group["replayed"])
        self.assertAlmostEqual(row["change_real"], row["cost"] - group["replayed"] - 1.5)
        self.assertEqual(group["best"]["code summary"]["cost"], min(r["cost"] for r in group["rows"] if r["variant"] == "code summary"))
        self.assertEqual(result["groups"]["root chat"]["sessions"], 1)
        self.assertEqual(result["groups"]["long workers"]["sessions"], 0)
        text = replay.rollover_report("ses_x", result)
        self.assertTrue(text.startswith("Replay check: replayed $"))
        for expected in ("same turns and same task result", "context after a rollover equals the observed median", "lowest for model summary", "sqrt(2R"):
            self.assertIn(expected, text)

    def test_turns_between_rollovers_follow_the_square_root_rule(self):
        quiet = {"sonnet": {"turns": 0}}
        estimate = replay.rollover_estimate(stats(opus={"turns": 100, "growth": 1_500.0, "after": 50_000.0}, **quiet), PRICE)["opus"]
        p = PRICE["opus"]
        code = estimate["code summary"]
        self.assertAlmostEqual(code["turns"], (2 * 50_000 * (p[2] - p[1]) / (p[1] * 1_500)) ** 0.5)
        self.assertGreater(estimate["model summary"]["turns"], code["turns"])   # a dearer rollover is done less often
        self.assertEqual(replay.rollover_estimate(stats(opus={"turns": 100, "growth": 0.0}, **quiet), PRICE), {})


class ShareTests(unittest.TestCase):
    def test_percentiles_interpolate(self):
        self.assertEqual(replay.percentile([], 0.5), 0.0)
        self.assertEqual(replay.percentile([5], 0.99), 5)
        self.assertEqual(replay.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(replay.percentile([4, 1, 3, 2], 0.0), 1)
        self.assertAlmostEqual(replay.percentile(list(range(101)), 0.9), 90)
        self.assertAlmostEqual(replay.percentile([0, 10], 0.99), 9.9)

    def test_growth_shares(self):
        values = [1_000] * 18 + [20_000, 30_000]   # 20 turns, 5% is one turn
        out = replay.growth_summary(values)
        self.assertEqual((out["turns"], out["total"], out["top_turns"]), (20, 68_000, 1))
        self.assertAlmostEqual(out["top_share"], 30_000 / 68_000 * 100)
        self.assertEqual(out["big_turns"], 2)
        self.assertAlmostEqual(out["big_share"], 50_000 / 68_000 * 100)
        self.assertEqual(out["p50"], 1_000)
        empty = replay.growth_summary([])
        self.assertEqual((empty["total"], empty["top_share"], empty["big_share"], empty["p99"]), (0, 0.0, 0.0, 0.0))

    def test_growth_values_skip_compactions_and_never_go_negative(self):
        chat = [turn(write=100_000), turn(read=100_000, write=3_000), turn(write=40_000), turn(read=40_000, write=500), turn(read=40_500, write=0, output=5)]
        chat[4]["read"] = 40_000
        self.assertEqual(replay.growth_values(chat), [3_000, 500, 0])

    def test_tool_sizes_are_summed_per_tool(self):
        rows = [("read", 900), ("shell", 100), ("read", 100), (None, 0), ("shell", 300)]
        out = replay.tool_summary(rows)
        self.assertEqual([e["tool"] for e in out], ["read", "shell", "unknown"])
        read = out[0]
        self.assertEqual((read["calls"], read["total"], read["mean"], read["largest"]), (2, 1000, 500, 900))
        self.assertAlmostEqual(read["share"], 1000 / 1400 * 100)
        self.assertAlmostEqual(sum(e["share"] for e in out), 100)
        self.assertEqual(replay.tool_summary([]), [])

    def test_cache_share_and_cost_split(self):
        turns = [turn(read=90_000, write=9_000, tokens_in=1_000, output=2_000), turn(read=0, write=100_000, output=0)]
        out = replay.cache_split(turns, PRICE)
        self.assertEqual(out["tokens"], {"input": 1_000, "read": 90_000, "write": 109_000, "output": 2_000})
        self.assertAlmostEqual(out["read_share"], 90_000 / 200_000 * 100)
        self.assertAlmostEqual(sum(out["dollars"].values()), sum(t["cost"] for t in turns))
        p = PRICE["opus"]
        self.assertAlmostEqual(out["dollars"]["read"], 90_000 * p[1])
        self.assertAlmostEqual(out["dollars"]["output"], 2_000 * p[3])
        self.assertEqual(replay.cache_split([], PRICE)["read_share"], 0.0)

    def test_cold_turns_are_rolled_up_by_cause(self):
        turns = [turn(write=100_000, gap=90), turn(write=100_000, gap=7), turn(write=100_000, gap=8), turn(read=100_000, write=1_000)]
        out = replay.cold_causes(turns)
        self.assertEqual({k: v["turns"] for k, v in out.items()}, {"pause over an hour": 1, "pause over 5 minutes": 2})
        self.assertAlmostEqual(out["pause over 5 minutes"]["cost"], turns[1]["cost"] + turns[2]["cost"])

    def test_a_cold_turn_after_a_pause_is_repriced_at_a_small_context(self):
        cold = dict(read=0, write=300_000, gap=12, before=290_000, output=2_000)
        made = turn(**cold)
        out = replay.pause_turn(made, PRICE, stats())
        p = PRICE["opus"]
        self.assertAlmostEqual(out["median"], 40_000 * p[2] + 2_000 * p[3])
        self.assertAlmostEqual(out["small"], 30_000 * p[2] + 2_000 * p[3])
        self.assertLess(out["small"], out["median"])
        self.assertLess(out["median"], out["cost"])
        self.assertEqual(out["context"], 290_000)
        # a one-hour cache reads the whole context instead (the 5 to 60 minute pauses only)
        self.assertAlmostEqual(out["warm"], 300_000 * p[1] + 2_000 * p[3])
        self.assertEqual(replay.pause_turn(turn(**{**cold, "gap": 90}), PRICE, stats())["warm"], turn(**{**cold, "gap": 90})["cost"])
        # a context already under the small one has nothing to give back
        small = turn(read=0, write=25_000, gap=12, before=20_000)
        self.assertEqual((replay.pause_turn(small, PRICE, stats())["median"], replay.pause_turn(small, PRICE, stats())["small"]), (small["cost"], small["cost"]))

    def test_pause_turns_are_split_by_pause_length_and_by_context(self):
        turns = [turn(write=100_000, gap=7, before=90_000), turn(write=200_000, gap=30, before=190_000), turn(write=400_000, gap=90, before=380_000),
                 turn(write=400_000, gap=0.2, before=380_000),                    # cold, but not after a pause
                 turn(write=300_000, gap=8, between=["compaction"], before=1),    # after compaction: not a pause
                 turn(read=300_000, write=1_000, gap=20, before=300_000)]         # warm
        out = replay.pause_rollover(turns, PRICE, stats())
        self.assertEqual(out["all"]["turns"], 3)
        self.assertEqual([out["by_pause"][label]["turns"] for label in ("5 to 15 min", "15 to 60 min", "over 60 min")], [1, 1, 1])
        self.assertEqual([out["by_context"][label]["turns"] for label in ("under 60k", "60k to 120k", "over 120k")], [0, 1, 2])
        self.assertEqual(out["over"]["100000"]["turns"], 2)
        self.assertAlmostEqual(out["all"]["cost"], sum(t["cost"] for t in turns[:3]))
        self.assertAlmostEqual(sum(out["by_pause"][l]["avoided_small"] for l in out["by_pause"]), out["all"]["avoided_small"])
        self.assertGreater(out["all"]["avoided_small"], out["all"]["avoided_median"])
        self.assertAlmostEqual(out["over"]["200000"]["avoided_small"], out["by_context"]["over 120k"]["avoided_small"] - replay.pause_rollover(turns[1:2], PRICE, stats())["all"]["avoided_small"])
        self.assertEqual(replay.pause_rollover([], PRICE, stats())["all"], {"turns": 0, "cost": 0, "avoided_median": 0, "avoided_small": 0, "avoided_warm": 0})

    def test_the_recommended_size_stops_where_a_rollover_saves_too_little(self):
        def row(turns, saved):
            return {"turns": turns, "avoided_median": saved, "avoided_small": saved, "cost": 0, "avoided_warm": 0}
        grid = {str(over): row(0, 0.0) for over in replay.ROLLOVER_OVER}
        chat = {"over": {**grid, "200000": row(2, 20.0), "150000": row(2, 20.0), "120000": row(4, 30.0), "100000": row(10, 30.5)}}
        for over in ("80000", "60000", "40000"):
            chat["over"][over] = row(10, 30.5)
        workers = {"over": dict(grid)}
        best = replay.recommend_over(chat, workers)
        self.assertEqual((best["over"], best["turns"], best["avoided_median"]), (120_000, 4, 30.0))   # 100k adds 6 turns for $0.50
        self.assertEqual(replay.recommend_over({"over": dict(grid)}, workers)["over"], replay.ROLLOVER_OVER[0])

    def test_the_cache_report_and_json_carry_the_pause_what_if(self):
        sessions = [session(growing(5) + [turn(write=300_000, gap=12, before=290_000)], kind="root", agent="unknown"),
                    session(growing(5) + [turn(write=200_000, gap=70, before=190_000)], agent="reviewer")]
        out = replay.cache(sessions)
        self.assertEqual((out["pauses"]["root"]["all"]["turns"], out["pauses"]["workers"]["all"]["turns"]), (1, 1))
        self.assertEqual(out["pauses"]["small"], 30_000)
        self.assertIn("opus", out["pauses"]["after"])
        json.dumps(out)
        text = replay.cache_report("ses_x", out)
        self.assertIn("after a pause", text)
        self.assertIn("pause 5 to 15 min", text)
        self.assertIn("Roll over before resuming when the pause was over the cache lifetime and the context is over", text)
        self.assertEqual(replay.cache([])["pauses"]["root"]["all"]["turns"], 0)

    def test_the_cache_report_groups_workers_by_agent_and_puts_the_chat_first(self):
        sessions = [session(growing(5), agent="reviewer"), session(growing(5), kind="root", agent="unknown"), session(growing(3), agent="reviewer"),
                    session([turn(write=90_000, gap=9)], agent="explorer")]
        out = replay.cache(sessions)
        self.assertEqual(list(out["agents"])[0], "chat (root)")
        self.assertEqual(sorted(out["agents"]), ["chat (root)", "explorer", "reviewer"])
        self.assertEqual((out["agents"]["reviewer"]["sessions"], out["agents"]["reviewer"]["turns"]), (2, 8))
        self.assertEqual(out["cold"]["workers"]["pause over 5 minutes"]["turns"], 1)
        self.assertEqual(out["cold"]["root"], {})
        text = replay.cache_report("ses_x", out)
        for expected in ("chat (root)", "reviewer", "all workers", "pause over 5 minutes", "worker turns"):
            self.assertIn(expected, text)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "opencode.db"
        db = sqlite3.connect(self.db)
        db.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, cost REAL, time_updated INTEGER, agent TEXT)")
        db.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, data TEXT)")
        db.executemany("INSERT INTO session_v2 VALUES (?, ?, ?, ?, ?)",
                       [("ses_root", None, 5.0, 9, "build"), ("ses_w", "ses_root", 4.0, 5, "impl-claude"), ("ses_old", None, 1.0, 1, "build")])
        self.seq = 0
        self.db_handle = db

        def add(session, kind, **data):
            self.seq += 1
            db.execute("INSERT INTO session_message VALUES (?, ?, ?, ?)", (session, kind, self.seq, json.dumps(data)))
        secret = "x" * 1234
        tools = [{"type": "tool", "name": "read", "state": {"status": "completed", "input": {"path": "a"}, "content": [{"type": "text", "text": secret}]}},
                 {"type": "tool", "name": "shell", "state": {"status": "completed", "content": [{"type": "text", "text": "ab"}, {"type": "text", "text": "cd"}]}},
                 {"type": "tool", "name": "read", "state": {"status": "error", "error": {"message": "nope"}}},
                 {"type": "text", "text": "not a tool"}]
        add("ses_w", "assistant", cost=1.0, model={"id": OPUS}, time={"created": 1000}, tokens={"input": 0, "cache": {"read": 0, "write": 100_000}, "output": 10}, content=tools)
        add("ses_w", "compaction", cost=0.7, model={"id": OPUS}, tokens={"input": 4})
        add("ses_w", "compaction", cost=0.2, model={"id": "claude-sonnet-5-5"})
        add("ses_w", "assistant", cost=0.5, model={"id": OPUS}, time={"created": 2000}, tokens={"cache": {"read": 100_000, "write": 100}}, content=[])
        db.execute("INSERT INTO session_message VALUES ('ses_w', 'assistant', 99, 'not json')")
        add("ses_root", "assistant", cost=2.0, model={"id": SONNET}, time={"created": 1000}, tokens={"cache": {"read": 1, "write": 1}})
        db.commit()

    def test_sessions_summaries_and_tool_sizes_are_read_without_the_text(self):
        chat, sessions, tools = replay.load_sessions(None, self.db, with_tools=True)
        self.assertEqual(chat, "ses_root")
        self.assertEqual([(s["id"], s["kind"], s["agent"], len(s["turns"])) for s in sessions], [("ses_root", "root", "build", 1), ("ses_w", "worker", "impl-claude", 2)])
        worker = sessions[1]
        self.assertEqual((worker["summaries"], worker["summary_calls"], worker["billed"], worker["long"]), ({"opus": 0.7, "sonnet": 0.2}, 2, 4.0, False))
        self.assertEqual(sorted(tools), [("read", len(json.dumps({"message": "nope"}, separators=(",", ":")))), ("read", 1234), ("shell", 4)])
        self.assertIsNone(replay.load_sessions("ses_root", self.db)[2])
        self.assertTrue(replay.load_sessions("ses_root", self.db, long_cost=3.0)[1][1]["long"])
        self.assertFalse(replay.load_sessions("ses_root", self.db, long_cost=3.0)[1][0]["long"])  # the chat itself is never a long worker

    def test_the_command_prints_each_report_and_the_json_adds_keys(self):
        base = ["--session", "ses_root", "--db", str(self.db), "--settings-log", str(self.db.parent / "log"), "--config", str(self.db.parent / "none.json")]

        def run(*args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cost.main(base + list(args)), 0)
            return out.getvalue()
        text = run("--rollover", "--threshold", "30000")
        self.assertTrue(text.startswith("Replay check"))
        self.assertIn("30k", text)
        self.assertNotIn("Where it went", text)
        self.assertIn("cache read", run("--cache"))
        growth = run("--growth")
        self.assertIn("tool", growth)
        self.assertIn("read", growth)
        self.assertNotIn("x" * 20, growth)
        data = json.loads(run("--json", "--rollover", "--cache", "--growth"))
        for key in ("cost", "turns", "cold", "workers", "root", "rollover", "cache", "growth"):
            self.assertIn(key, data)
        self.assertEqual(data["growth"]["tools"]["by_tool"][0]["tool"], "read")
        self.assertIn("recommended", data["cache"]["pauses"])
        self.assertEqual(sorted(json.loads(run("--json"))), sorted(set(data) - {"rollover", "cache", "growth"}))
        self.assertIn("Where it went", run())


class NoToolDataTests(unittest.TestCase):
    def test_growth_without_tool_data_prints_only_the_percentiles(self):
        out = replay.growth([session(growing(10), long=True)], [])
        self.assertIsNone(out["tools"])
        text = replay.growth_report("ses_x", out)
        self.assertIn("no usable tool data", text)
        self.assertIn("p50", text)


if __name__ == "__main__":
    unittest.main()
