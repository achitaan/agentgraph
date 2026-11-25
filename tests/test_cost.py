import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentgraph import cost


def turn(read=0, write=0, output=1000, gap=0.5, between=(), dollars=None, tokens_in=0, **extra):
    made = {"input": tokens_in, "read": read, "write": write, "output": output, "gap": gap, "between": list(between), "cost": 0.0, **extra}
    made["cost"] = cost._weight(made) * 5e-6 if dollars is None else dollars
    return made


class CostTests(unittest.TestCase):
    def test_cold_turns_are_found_and_given_a_cause(self):
        self.assertFalse(cost.is_cold(turn(read=400_000, write=3_000)))
        self.assertFalse(cost.is_cold(turn(read=0, write=20_000)))  # a small first turn is not a cold one
        self.assertTrue(cost.is_cold(turn(read=10_000, write=400_000)))
        cold = dict(read=0, write=300_000)
        self.assertEqual(cost.cause_of(turn(gap=7, **cold)), "pause over 5 minutes")
        self.assertEqual(cost.cause_of(turn(gap=90, **cold)), "pause over an hour")
        self.assertEqual(cost.cause_of(turn(gap=0.2, between=["system"], **cold)), "settings or model changed")
        self.assertEqual(cost.cause_of(turn(gap=20, between=["compaction", "user"], **cold)), "after compaction")
        self.assertEqual(cost.cause_of(turn(gap=0.2, between=["user"], **cold)), "unexplained")

    def test_causes_found_in_the_data_are_named_after_the_old_ones(self):
        self.assertEqual(cost.CAUSES[:5], ("pause over an hour", "pause over 5 minutes", "settings or model changed", "after compaction", "unexplained"))
        self.assertEqual(len(set(cost.CAUSES)), len(cost.CAUSES))
        cold = dict(read=0, write=300_000, gap=0.2, between=["user"])
        # the whole last context was read: the write is new text, not a miss (priority over the later checks)
        self.assertEqual(cost.cause_of(turn(read=400_000, write=60_000, gap=0.2, before=410_000)), "large new content (no miss)")
        self.assertEqual(cost.cause_of(turn(read=0, write=300_000, gap=0.2, before=290_000)), "unexplained")  # grew, but nothing was read
        # a request that billed nothing came just before
        self.assertEqual(cost.cause_of(turn(failed=True, before=250_000, **cold)), "after a failed request")
        # the context got smaller without a compaction: an edit before the end of the cached part
        self.assertEqual(cost.cause_of(turn(before=700_000, **cold)), "context shrank")
        self.assertEqual(cost.cause_of(turn(before=700_000, failed=True, **cold)), "after a failed request")
        # settings notices still come first; so do pauses
        self.assertEqual(cost.cause_of(turn(before=700_000, gap=0.2, between=["system"], read=0, write=300_000)), "settings or model changed")
        self.assertEqual(cost.cause_of(turn(before=700_000, read=0, write=300_000, gap=9)), "pause over 5 minutes")
        # near the end of the lifetime, cold turns are 10 to 15 times as common
        self.assertEqual(cost.cause_of(turn(read=0, write=300_000, gap=4.2, before=200_000)), "pause of 3 to 5 minutes")
        self.assertEqual(cost.cause_of(turn(read=0, write=300_000, gap=2.9, before=200_000)), "unexplained")
        self.assertEqual(cost.cause_of(turn(read=0, write=300_000, gap=0.2)), "unexplained")  # no 'before' key at all

    def test_the_breakdown_adds_up_and_the_what_ifs_move_the_right_way(self):
        turns = [turn(write=30_000)]
        turns += [turn(read=100_000 * n, write=5_000) for n in range(1, 7)]
        turns += [turn(read=0, write=600_000, gap=12), turn(read=600_000, write=5_000),
                  turn(read=0, write=605_000, gap=0.3, between=["model-switched"])]
        result = cost.analyse(turns, cap=200_000)
        total = sum(item["cost"] for item in turns)
        self.assertAlmostEqual(result["cost"], total)
        self.assertEqual((result["turns"], result["warm"]["turns"], result["cold"]["turns"]), (10, 8, 2))
        self.assertAlmostEqual(result["warm"]["cost"] + result["cold"]["cost"], total)
        self.assertEqual({name: entry["turns"] for name, entry in result["causes"].items()},
                         {"pause over 5 minutes": 1, "settings or model changed": 1})
        self.assertEqual(sum(band["turns"] for band in result["bands"]), 10)
        self.assertAlmostEqual(sum(band["cost"] for band in result["bands"]), total)
        self.assertEqual(result["largest_context"], 605_000)
        what = result["what_if"]
        self.assertLess(what["compact_at_cap"], total)   # a smaller context costs less on every large turn
        self.assertLess(what["one_hour_cache"], total)   # the 12-minute pause no longer rewrites 600k tokens
        self.assertLess(what["both"], what["compact_at_cap"])
        self.assertGreaterEqual(what["extra_compactions"], 1)
        # A one-hour cache costs more when nothing ever expires: every write is dearer.
        steady = [turn(read=50_000 * n, write=20_000) for n in range(1, 6)]
        self.assertGreater(cost.analyse(steady)["what_if"]["one_hour_cache"], sum(item["cost"] for item in steady))
        # A chat that stays under the cap is not changed by it.
        self.assertAlmostEqual(cost.analyse(steady, cap=900_000)["what_if"]["compact_at_cap"], sum(item["cost"] for item in steady))
        self.assertEqual(cost.analyse([])["cost"], 0)

    def test_turns_are_read_from_the_session_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "opencode.db"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, cost REAL, time_updated INTEGER)")
            db.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, data TEXT)")
            db.executemany("INSERT INTO session_v2 VALUES (?, ?, ?, ?)",
                           [("ses_old", None, 1.0, 10), ("ses_root", None, 9.0, 20), ("ses_child", "ses_root", 2.5, 30)])

            def message(seq, kind, **data):
                db.execute("INSERT INTO session_message VALUES ('ses_root', ?, ?, ?)", (kind, seq, json.dumps(data)))
            message(1, "user")
            message(2, "assistant", time={"created": 60_000}, cost=0.5, tokens={"input": 10, "output": 20, "reasoning": 5, "cache": {"read": 100, "write": 200}})
            message(3, "system")
            message(4, "user")
            message(5, "assistant", time={"created": 660_000}, cost=1.5, tokens={"cache": {"write": 70_000}})
            message(6, "assistant", time={"created": 670_000})  # still being written: no tokens yet
            db.execute("INSERT INTO session_message VALUES ('ses_root', 'assistant', 7, 'not json')")
            db.commit()
            db.close()
            session, turns, workers = cost.load_turns(db_path=path)
            self.assertEqual((session, workers), ("ses_root", {"sessions": 1, "cost": 2.5}))
            self.assertEqual(turns[0], {"cost": 0.5, "input": 10, "read": 100, "write": 200, "output": 25, "gap": 0.0, "between": ["user"], "time": 60.0, "model": "",
                                        "before": 0, "failed": False})
            self.assertEqual((turns[1]["before"], turns[1]["failed"], turns[2]["failed"]), (310, False, False))
            self.assertEqual((turns[1]["time"], turns[2]["time"], turns[2]["model"]), (660.0, 670.0, ""))
            self.assertEqual((turns[1]["gap"], turns[1]["between"], turns[1]["write"]), (10.0, ["system", "user"], 70_000))
            self.assertEqual(len(turns), 3)
            text = cost.report(session, cost.analyse(turns), workers)
            self.assertIn("ses_root: $2.00 over 3 turns", text)
            self.assertIn("pause over 5 minutes", text)
            self.assertEqual(cost.load_turns("ses_old", path)[1], [])


HOUR = 3600
NOW = 1_000_000.0


def make_db(path, chats, messages):
    """chats: (id, parent, cost, updated_seconds); messages: (session, seq, time_seconds, model, write, read)."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, cost REAL, time_updated INTEGER)")
    db.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, data TEXT)")
    db.executemany("INSERT INTO session_v2 VALUES (?, ?, ?, ?)", [(i, p, c, int(u * 1000)) for i, p, c, u in chats])
    for session, seq, when, model, write, read in messages:
        data = {"time": {"created": int(when * 1000)}, "cost": (read * 0.1 + write * 1.25 + 1000 * 5) * 5e-6,
                "tokens": {"input": 0, "output": 1000, "cache": {"read": read, "write": write}}}
        if model:
            data["model"] = {"id": model}
        db.execute("INSERT INTO session_message VALUES (?, 'assistant', ?, ?)", (session, seq, json.dumps(data)))
    db.commit()
    db.close()


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_settings_are_read_from_the_config(self):
        path = self.folder / "opencode.json"
        path.write_text(json.dumps({"compaction": {"buffer": 750000}, "providers": {"anthropic": {
            "settings": {"cache": {"ttl": "5m"}},
            "models": {"fable": {"settings": {"cache": {"ttl": "1h"}}}, "plain": {}}}}}), encoding="utf-8")
        self.assertEqual(cost.read_settings(path), {"compaction_buffer": 750000, "cache_ttl": {"*": "5m", "fable": "1h"}})
        path.write_text("{}", encoding="utf-8")
        self.assertEqual(cost.read_settings(path), {"compaction_buffer": None, "cache_ttl": {}})

    def test_a_missing_or_invalid_config_gives_the_defaults(self):
        empty = {"compaction_buffer": None, "cache_ttl": {}}
        self.assertEqual(cost.read_settings(self.folder / "missing.json"), empty)
        for text in ("{not json", "[1, 2]", '{"compaction": 5, "providers": {"anthropic": {"models": 3}}}'):
            (self.folder / "bad.json").write_text(text, encoding="utf-8")
            self.assertEqual(cost.read_settings(self.folder / "bad.json"), empty)

    def test_the_log_gets_a_line_only_when_the_settings_change(self):
        log = self.folder / "sub" / "log.jsonl"
        one, two = {"compaction_buffer": None, "cache_ttl": {}}, {"compaction_buffer": 5, "cache_ttl": {"*": "1h"}}
        self.assertEqual(cost.load_history(log), [])
        self.assertTrue(cost.note_settings(log, one, 10))
        self.assertFalse(cost.note_settings(log, one, 20))
        with log.open("a", encoding="utf-8") as handle:
            handle.write("garbage\n{\"time\": \"x\", \"settings\": {}}\n[]\n")
        self.assertFalse(cost.note_settings(log, one, 25))   # bad lines do not count as the last line
        self.assertTrue(cost.note_settings(log, two, 30))
        self.assertFalse(cost.note_settings(log, two, 40))
        self.assertTrue(cost.note_settings(log, one, 50))
        self.assertEqual(cost.load_history(log), [{"time": 10, "settings": one}, {"time": 30, "settings": two}, {"time": 50, "settings": one}])

    def test_current_settings_are_noted_with_the_config_time(self):
        config, log = self.folder / "opencode.json", self.folder / "log.jsonl"
        config.write_text(json.dumps({"compaction": {"buffer": 7}}), encoding="utf-8")
        self.assertTrue(cost.note_current_settings(log, config))
        self.assertFalse(cost.note_current_settings(log, config))
        self.assertEqual(cost.load_history(log)[0]["time"], config.stat().st_mtime)


class TrendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.db = self.folder / "opencode.db"
        base = NOW - 10 * 24 * HOUR
        # one chat with six turns an hour apart (all cold: 60k written, nothing read), two models alternating
        messages = [("ses_a", n, base + n * HOUR, "fable" if n % 2 else "plain", 60_000, 0) for n in range(6)]
        messages += [("ses_b", n, base + (n + 7) * HOUR, "plain", 1_000, 100_000) for n in range(2)]
        messages += [("ses_old", 0, base, "plain", 60_000, 0), ("ses_cheap", 0, base, "plain", 60_000, 0),
                     ("ses_child", 0, base, "plain", 60_000, 0)]
        make_db(self.db, [("ses_a", None, 5.0, NOW - HOUR), ("ses_b", None, 2.0, NOW - HOUR),
                          ("ses_old", None, 9.0, NOW - 40 * 24 * HOUR), ("ses_cheap", None, 0.5, NOW - HOUR),
                          ("ses_child", "ses_a", 9.0, NOW - HOUR)], messages)
        self.base = base

    def test_a_chat_is_split_between_periods_and_a_period_reports_its_numbers(self):
        split = self.base + 2.5 * HOUR   # turns 0-2 before it, turns 3-5 of ses_a and all of ses_b after
        history = [{"time": split, "settings": {"compaction_buffer": 1, "cache_ttl": {}}}]
        periods = cost.trend(self.db, history, now=NOW)
        self.assertEqual([p["since"] for p in periods], [None, split])
        before, after = periods
        self.assertEqual((before["chats"], before["turns"]), (1, 3))
        self.assertEqual((after["chats"], after["turns"]), (2, 5))
        self.assertEqual(before["settings"], {"compaction_buffer": None, "cache_ttl": {}})
        self.assertEqual(after["settings"], history[0]["settings"])
        cold_cost = (60_000 * 1.25 + 5000) * 5e-6
        self.assertAlmostEqual(before["cost"], 3 * cold_cost)
        self.assertAlmostEqual(before["billed"], before["cost"])
        self.assertAlmostEqual(before["per_turn"], cold_cost)
        # the first turn has no earlier turn so no pause; the two after it follow a 60 minute gap
        self.assertEqual(before["pauses"]["turns"], 2)
        self.assertAlmostEqual(before["pauses"]["cost"], 2 * cold_cost)
        self.assertAlmostEqual(before["cold_share"], 1.0)
        self.assertEqual(before["context"], 60_000)
        warm_cost = (1_000 * 1.25 + 100_000 * 0.1 + 5000) * 5e-6
        self.assertAlmostEqual(after["cost"], 3 * cold_cost + 2 * warm_cost)
        self.assertAlmostEqual(after["cold_share"], 3 * cold_cost / (3 * cold_cost + 2 * warm_cost))
        self.assertEqual(after["context"], 60_000)

    def test_the_window_and_the_minimum_cost_filter_chats(self):
        self.assertEqual(sum(p["chats"] for p in cost.trend(self.db, [], now=NOW)), 2)
        self.assertEqual(sum(p["chats"] for p in cost.trend(self.db, [], now=NOW, days=60)), 3)
        self.assertEqual(sum(p["chats"] for p in cost.trend(self.db, [], now=NOW, min_cost=0.1)), 3)
        self.assertEqual(cost.trend(self.db, [], now=NOW, days=0), [])

    def test_the_one_hour_price_applies_only_to_the_listed_model(self):
        listed = [{"time": 0, "settings": {"compaction_buffer": None, "cache_ttl": {"fable": "1h"}}}]
        (period,) = cost.trend(self.db, listed, now=NOW)
        weight = lambda model: 60_000 * (2.0 if model == "fable" else 1.25) + 5000
        fable_cost = (60_000 * 1.25 + 5000) * 5e-6
        self.assertAlmostEqual(period["cost"], 6 * fable_cost + 2 * (1_000 * 1.25 + 10_000 + 5000) * 5e-6)
        expected = 3 * fable_cost * weight("fable") / weight("plain") + 3 * fable_cost \
            + 2 * (1_000 * 1.25 + 10_000 + 5000) * 5e-6
        self.assertAlmostEqual(period["billed"], expected)
        self.assertGreater(period["billed"], period["cost"])
        star = [{"time": 0, "settings": {"compaction_buffer": None, "cache_ttl": {"*": "1h"}}}]
        self.assertGreater(cost.trend(self.db, star, now=NOW)[0]["billed"], period["billed"])

    def test_the_command_prints_the_periods_and_notes_the_settings(self):
        config, log = self.folder / "opencode.json", self.folder / "log.jsonl"
        config.write_text(json.dumps({"compaction": {"buffer": 750000},
                                      "providers": {"anthropic": {"models": {"fable": {"settings": {"cache": {"ttl": "1h"}}}}}}}), encoding="utf-8")
        # the config's time is in the past of every turn, so one period with the current settings
        os.utime(config, (self.base - HOUR, self.base - HOUR))
        args = ["--trend", "--db", str(self.db), "--settings-log", str(log), "--config", str(config), "--days", "100000"]
        for _ in range(2):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cost.main(args), 0)
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)
        self.assertIn("one-hour cache for fable", out.getvalue())
        self.assertIn("compaction buffer 750 000", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cost.main([*args, "--json"])
        (period,) = json.loads(out.getvalue())
        self.assertEqual(period["settings"]["cache_ttl"], {"fable": "1h"})
        self.assertEqual(period["turns"], 9)   # the old chat is inside this window


class BillingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / "opencode.db"
        db = sqlite3.connect(self.db)
        db.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, cost REAL, time_updated INTEGER, time_created INTEGER, agent TEXT, model TEXT)")
        db.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, data TEXT)")
        opus, sonnet = '{"id":"claude-opus-5-5","providerID":"anthropic","variant":"high"}', '{"id":"claude-sonnet-5-5"}'
        db.executemany("INSERT INTO session_v2 VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("ses_root", None, 10.0, 5, 1, "build", opus),
            ("ses_a", "ses_root", 30.0, 5, 2, "impl-claude", opus),     # long by cost: 3 turns, $30 billed, $27 in messages
            ("ses_b", "ses_root", 2.0, 5, 3, "impl-openai", sonnet),
            ("ses_c", "ses_root", 1.0, 5, 4, "impl-openai", sonnet),     # no messages: all of it unreported
            ("ses_other", None, 99.0, 5, 5, "build", opus)])
        self.seq = 0

        def add(session, kind, **data):
            self.seq += 1
            db.execute("INSERT INTO session_message VALUES (?, ?, ?, ?)", (session, kind, self.seq, json.dumps(data)))

        def assistant(session, dollars, write=0, when=1000):
            add(session, "assistant", cost=dollars, time={"created": when}, tokens={"cache": {"write": write, "read": 0}})
        add("ses_root", "user"), assistant("ses_root", 4.0), add("ses_root", "compaction"), assistant("ses_root", 3.0)
        add("ses_a", "user"), add("ses_a", "user")
        for n in range(3):
            assistant("ses_a", 9.0, write=60_000 if n == 0 else 0, when=1000 + n)
        add("ses_b", "user"), assistant("ses_b", 1.5), add("ses_b", "compaction"), add("ses_b", "compaction")
        assistant("ses_other", 50.0)
        db.commit()
        db.close()

    def test_a_chat_and_its_workers_are_billed_against_their_messages(self):
        billing = cost.load_billing("ses_root", self.db)
        root, workers = billing["root"], billing["workers"]
        self.assertEqual((root["billed"], root["messages"], root["unreported"], root["compactions"], root["user_messages"]), (10.0, 7.0, 3.0, 1, 1))
        self.assertEqual((workers["sessions"], workers["turns"], workers["billed"], workers["messages"], workers["unreported"], workers["compactions"], workers["cold_turns"]),
                         (3, 4, 33.0, 28.5, 4.5, 2, 1))
        # the other chat is not counted, and every session adds up to its parts
        self.assertAlmostEqual(sum(row["unreported"] for row in workers["list"]), workers["unreported"])
        self.assertEqual([row["id"] for row in workers["list"]], ["ses_a", "ses_b", "ses_c"])

    def test_workers_are_split_by_agent_and_model(self):
        workers = cost.load_billing("ses_root", self.db)["workers"]
        self.assertEqual(list(workers["by_agent"]), ["impl-claude", "impl-openai"])   # dearest first
        openai = workers["by_agent"]["impl-openai"]
        self.assertEqual((openai["sessions"], openai["turns"], openai["billed"], openai["messages"], openai["compactions"]), (2, 1, 3.0, 1.5, 2))
        self.assertAlmostEqual(openai["per_turn"], 3.0)
        self.assertEqual(workers["by_agent"]["impl-claude"]["cold_turns"], 1)
        self.assertEqual({name: entry["sessions"] for name, entry in workers["by_model"].items()},
                         {"claude-opus-5-5": 1, "claude-sonnet-5-5": 2})

    def test_long_sessions_are_those_over_the_turn_or_the_cost_limit(self):
        long = cost.load_billing("ses_root", self.db)["workers"]
        self.assertEqual([row["id"] for row in long["long"]], ["ses_a"])
        self.assertEqual((long["long"][0]["user_messages"], long["long"][0]["turns"]), (2, 3))
        self.assertEqual(long["long_limits"], {"turns": 300, "cost": 25.0})
        by_turns = cost.load_billing("ses_root", self.db, long_turns=2, long_cost=1000)["workers"]["long"]
        self.assertEqual([row["id"] for row in by_turns], ["ses_a"])
        self.assertEqual(cost.load_billing("ses_root", self.db, long_turns=0, long_cost=1.5)["workers"]["long"][0]["id"], "ses_a")
        self.assertEqual([row["id"] for row in cost.load_billing("ses_root", self.db, long_turns=0, long_cost=1.5)["workers"]["long"]], ["ses_a", "ses_b"])
        self.assertEqual(cost.load_billing("ses_root", self.db, long_turns=5, long_cost=1000)["workers"]["long"], [])

    def test_a_database_without_agent_and_model_columns_still_works(self):
        plain = self.db.parent / "plain.db"
        db = sqlite3.connect(plain)
        db.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, cost REAL, time_updated INTEGER)")
        db.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, data TEXT)")
        db.executemany("INSERT INTO session_v2 VALUES (?, ?, ?, ?)", [("r", None, 1.0, 1), ("w", "r", 2.0, 1)])
        db.commit()
        db.close()
        workers = cost.load_billing("r", plain)["workers"]
        self.assertEqual((workers["sessions"], workers["billed"], list(workers["by_agent"]), list(workers["by_model"])), (1, 2.0, ["unknown"], ["unknown"]))

    def test_the_command_prints_and_the_json_keeps_its_old_keys(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cost.main(["--session", "ses_root", "--db", str(self.db), "--json", "--settings-log", str(self.db.parent / "log"),
                                        "--config", str(self.db.parent / "none.json")]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual((data["cost"], data["turns"], data["cold"]["turns"], data["cold"]["cost"]), (7.0, 2, 0, 0))
        self.assertEqual((data["workers"]["cost"], data["workers"]["sessions"]), (33.0, 3))
        self.assertEqual((data["workers"]["billed"], data["workers"]["unreported"], data["root"]["unreported"]), (33.0, 4.5, 3.0))
        self.assertEqual(data["workers"]["long"][0]["id"], "ses_a")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cost.main(["--session", "ses_root", "--db", str(self.db), "--settings-log", str(self.db.parent / "log"),
                       "--config", str(self.db.parent / "none.json"), "--long-cost", "1.5"])
        text = out.getvalue()
        for expected in ("not stored as messages", "Worker sessions by agent", "Worker sessions by model", "impl-claude", "claude-opus-5-5",
                         "Long worker sessions (over 300 turns or $1.5): 2", "ses_a", "2 user messages"):
            self.assertIn(expected, text)

    def test_the_trend_spreads_the_unreported_part_over_a_chat_s_turns(self):
        base = NOW - 10 * 24 * HOUR
        path = self.db.parent / "trend.db"
        make_db(path, [("ses_a", None, 5.0, NOW - HOUR)], [("ses_a", n, base + n * HOUR, "m", 60_000, 0) for n in range(4)])
        recorded = 4 * (60_000 * 1.25 + 5000) * 5e-6
        (period,) = cost.trend(path, [], now=NOW)
        self.assertAlmostEqual(period["unreported"], 5.0 - recorded)
        split = [{"time": base + 1.5 * HOUR, "settings": {"compaction_buffer": None, "cache_ttl": {}}}]
        before, after = cost.trend(path, split, now=NOW)
        self.assertAlmostEqual(before["unreported"] + after["unreported"], 5.0 - recorded)
        self.assertAlmostEqual(before["unreported"], after["unreported"])
        self.assertIn("not stored as messages", cost.trend_report([before]))


if __name__ == "__main__":
    unittest.main()
