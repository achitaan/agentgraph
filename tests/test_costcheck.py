import contextlib
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agentgraph import cost, costcheck

DAY = 86400
NOW = time.mktime((2033, 5, 18, 12, 0, 0, 0, 0, -1))   # local noon, so a few hours back is the same calendar day
CHANGE = NOW - 5 * DAY
HISTORY = [{"time": CHANGE, "settings": {"compaction_buffer": None, "cache_ttl": {}}}]
WARM = dict(read=100_000, write=2_000)
COLD = dict(read=30_000, write=60_000)      # cold, but it read enough to be no "whole chat re-cached" turn
LEAK = dict(read=0, write=60_000)           # the whole chat written again


def turn(when, read=100_000, write=2_000, dollars=0.1):
    return {"cost": dollars, "input": 0, "read": read, "write": write, "output": 1000, "gap": 0.5, "between": [],
            "time": when, "model": "m", "before": 0, "failed": False}


def chat(ident, start, count, *, warm=0.1, cold=0.5, cold_every=2, kind="root", updated=None, billed=10.0):
    """`count` turns a minute apart, every `cold_every`th one cold."""
    turns = [turn(start + 60 * n, **(COLD if n % cold_every == 0 else WARM), dollars=cold if n % cold_every == 0 else warm) for n in range(count)]
    return {"id": ident, "kind": kind, "billed": billed, "turns": turns, "updated": start + 60 * count if updated is None else updated}


def rolled(session, seconds, event="rolled", **extra):
    return {"t": int(seconds * 1000), "event": event, "session": session, **extra}


def verdicts(result):
    return {row["id"]: row["verdict"] for row in result["checks"]}


def check(sessions, compactions=(), rows=(), history=HISTORY, **extra):
    return costcheck.check(sessions, list(compactions), None if rows is None else list(rows), history, now=NOW, **extra)


class RecacheTests(unittest.TestCase):
    def run_of(self, *kinds):
        return {"a": [turn(n, **kind) for n, kind in enumerate(kinds)]}

    def test_one_such_turn_is_an_ordinary_cold_turn(self):
        found = costcheck.recached(self.run_of(WARM, LEAK, WARM, LEAK, WARM))
        self.assertEqual(found, {"turns": 0, "cost": 0, "chats": 0})

    def test_two_in_a_row_count_both_turns(self):
        found = costcheck.recached(self.run_of(WARM, LEAK, LEAK, WARM))
        self.assertEqual((found["turns"], found["chats"]), (2, 1))
        self.assertAlmostEqual(found["cost"], 0.2)

    def test_a_normal_turn_breaks_the_run(self):
        self.assertEqual(costcheck.recached(self.run_of(LEAK, WARM, LEAK))["turns"], 0)
        self.assertEqual(costcheck.recached(self.run_of(LEAK, LEAK, WARM, LEAK, LEAK, LEAK))["turns"], 5)

    def test_a_run_at_the_end_of_the_chat_counts(self):
        self.assertEqual(costcheck.recached(self.run_of(WARM, LEAK, LEAK, LEAK))["turns"], 3)

    def test_runs_in_different_chats_do_not_join(self):
        chats = {"a": [turn(1, **LEAK)], "b": [turn(2, **LEAK)]}
        self.assertEqual(costcheck.recached(chats)["turns"], 0)
        chats = {"a": [turn(1, **LEAK), turn(2, **LEAK)], "b": [turn(1, **LEAK), turn(2, **LEAK)]}
        self.assertEqual((costcheck.recached(chats)["turns"], costcheck.recached(chats)["chats"]), (4, 2))

    def test_the_limits_are_exclusive(self):
        edge = lambda read, write: self.run_of(dict(read=read, write=write), dict(read=read, write=write))
        self.assertEqual(costcheck.recached(edge(24_999, 30_001))["turns"], 2)
        self.assertEqual(costcheck.recached(edge(25_000, 30_001))["turns"], 0)
        self.assertEqual(costcheck.recached(edge(24_999, 30_000))["turns"], 0)


class CountingTests(unittest.TestCase):
    def test_verdict_words(self):
        self.assertEqual(costcheck.verdict(1.0, 1.0), "same")
        self.assertEqual(costcheck.verdict(1.05, 1.0), "same")
        self.assertEqual(costcheck.verdict(0.5, 1.0), "better")
        self.assertEqual(costcheck.verdict(2.0, 1.0), "worse")
        self.assertEqual(costcheck.verdict(None, 1.0), "not enough data")
        self.assertEqual(costcheck.verdict(1.0, None), "not enough data")

    def test_a_share_must_also_move_by_the_floor(self):
        self.assertEqual(costcheck.verdict(0.012, 0.010, costcheck.SHARE_FLOOR), "same")   # 20% more, but a tiny move
        self.assertEqual(costcheck.verdict(0.40, 0.30, costcheck.SHARE_FLOOR), "worse")
        self.assertEqual(costcheck.verdict(0.20, 0.30, costcheck.SHARE_FLOOR), "better")

    def test_overall_verdict(self):
        self.assertEqual(costcheck.overall(["better", "worse", "same"]), "worse")
        self.assertEqual(costcheck.overall(["better", "same", "not enough data"]), "better")
        self.assertEqual(costcheck.overall(["same", "not enough data"]), "same")
        self.assertEqual(costcheck.overall(["not enough data"]), "not enough data")
        self.assertEqual(costcheck.overall([]), "not enough data")

    def test_windows(self):
        self.assertEqual(costcheck.windows([], NOW, 7), (None, (NOW - 7 * DAY, NOW + 1)))
        base, recent = costcheck.windows(HISTORY, NOW, 7)
        self.assertEqual((base, recent[0]), ((0.0, CHANGE), CHANGE))           # not before the first change
        self.assertEqual(costcheck.windows(HISTORY, NOW, 2)[1][0], NOW - 2 * DAY)
        old = [{"time": NOW - 20 * DAY, "settings": {}}]
        self.assertEqual(costcheck.windows(old, NOW, 7)[1][0], NOW - 7 * DAY)

    def test_rollovers_are_the_rolled_event_and_supplied_own_compactions(self):
        rows = [rolled("a", NOW), rolled("a", NOW + 1, "own_compaction", reason="own-compaction: supplied summary"),
                rolled("a", NOW + 2, "own_compaction", reason="own-compaction: asked"), rolled("a", NOW + 3, "asked"),
                {"event": "rolled", "session": "a"}, {"t": True, "event": "rolled"}, {"t": "5", "event": "rolled"}, "text", None]
        found = costcheck.rolled_over(rows)
        self.assertEqual([row["t"] for row in found], [NOW, NOW + 1])
        self.assertEqual(costcheck.rolled_over([{"t": 5000, "event": "rolled"}]), [{"t": 5.0, "session": ""}])

    def test_rollovers_a_day_and_the_median_gap(self):
        rows = [{"t": NOW + minutes * 60, "session": "a"} for minutes in (0, 10, 30)] + [{"t": NOW, "session": "b"}]
        found = costcheck.rollover_stats(rows)
        self.assertEqual((found["count"], found["chats"], found["most_in_a_day"], found["gaps"]), (4, 2, 3, 2))
        self.assertEqual(found["median_gap_minutes"], 15)
        # the same chat on two days is two counts of one
        far = costcheck.rollover_stats([{"t": NOW, "session": "a"}, {"t": NOW + 2 * DAY, "session": "a"}])
        self.assertEqual(far["most_in_a_day"], 1)
        none = costcheck.rollover_stats([])
        self.assertEqual((none["count"], none["most_in_a_day"], none["median_gap_minutes"]), (0, 0, None))

    def test_compaction_share_of_all_spend(self):
        found = costcheck.compaction_stats([{"cost": 2.0}, {"cost": 3.0}], 95.0)
        self.assertEqual(found["count"], 2)
        self.assertAlmostEqual(found["cost"], 5.0)
        self.assertAlmostEqual(found["share"], 0.05)
        self.assertIsNone(costcheck.compaction_stats([], 0)["share"])

    def test_long_chats_are_workers_over_the_turn_or_dollar_limit(self):
        rows = [{"kind": "worker", "billed": 10.0, "turns": [None] * 301}, {"kind": "worker", "billed": 10.0, "turns": [None] * 300},
                {"kind": "worker", "billed": 30.0, "turns": []}, {"kind": "root", "billed": 500.0, "turns": [None] * 900}]
        found = costcheck.long_share(rows)
        self.assertEqual((found["workers"], found["long"]), (3, 2))
        self.assertAlmostEqual(found["share"], 40 / 50)
        self.assertIsNone(costcheck.long_share([{"kind": "worker", "billed": 0.5, "turns": []}])["share"])   # too little spend


class CheckTests(unittest.TestCase):
    def sessions(self, base=0.3, recent=0.1, **kwargs):
        return [chat("old", CHANGE - 3 * DAY, 40, warm=base, cold=base, **kwargs), chat("new", CHANGE + DAY, 40, warm=recent, cold=recent, **kwargs)]

    def test_dollars_a_turn_better_same_worse(self):
        self.assertEqual(verdicts(check(self.sessions(0.3, 0.1)))["per_turn"], "better")
        self.assertEqual(verdicts(check(self.sessions(0.3, 0.3)))["per_turn"], "same")
        self.assertEqual(verdicts(check(self.sessions(0.1, 0.3)))["per_turn"], "worse")
        line = check(self.sessions(0.3, 0.1))["checks"][0]["line"]
        self.assertIn("$0.100 recently against $0.300 before", line)

    def test_cold_share_follows_the_cost_of_cold_turns(self):
        sessions = [chat("old", CHANGE - 3 * DAY, 40, warm=0.1, cold=0.5, cold_every=2), chat("new", CHANGE + DAY, 40, warm=0.1, cold=0.5, cold_every=10)]
        self.assertEqual(verdicts(check(sessions))["cold_share"], "better")
        flipped = [chat("old", CHANGE - 3 * DAY, 40, warm=0.1, cold=0.5, cold_every=10), chat("new", CHANGE + DAY, 40, warm=0.1, cold=0.5, cold_every=2)]
        self.assertEqual(verdicts(check(flipped))["cold_share"], "worse")

    def test_not_enough_data(self):
        few = [chat("old", CHANGE - 3 * DAY, 40), chat("new", CHANGE + DAY, 10)]
        found = verdicts(check(few))
        self.assertEqual((found["per_turn"], found["cold_share"], found["compactions"]), ("not enough data",) * 3)
        none = verdicts(check(self.sessions(), history=[]))      # no baseline at all
        self.assertEqual((none["per_turn"], none["cold_share"], none["compactions"]), ("not enough data",) * 3)
        self.assertEqual(check([], history=[])["overall"], "not enough data")

    def test_whole_chat_re_cached_is_worse_and_says_so(self):
        leaky = chat("leak", CHANGE + DAY, 40)
        for index in (10, 11):
            leaky["turns"][index].update(LEAK)
        result = check(self.sessions() + [leaky])
        self.assertEqual(verdicts(result)["recached"], "worse")
        self.assertIn("caching stopped working in 1 chat)", result["checks"][5]["line"])
        second = chat("leak2", CHANGE + DAY, 40)
        for index in (3, 4, 5):
            second["turns"][index].update(LEAK)
        result = check(self.sessions() + [leaky, second])
        self.assertIn("caching stopped working in 2 chats", result["checks"][5]["line"])
        self.assertEqual(result["overall"], "worse")
        self.assertIn("whole chat re-cached", result["worse"])

    def test_a_single_re_cached_turn_is_not_a_leak(self):
        sessions = self.sessions()
        sessions[1]["turns"][5].update(LEAK)
        self.assertEqual(verdicts(check(sessions))["recached"], "same")

    def test_a_leak_only_before_is_better_now(self):
        sessions = self.sessions()
        for index in (4, 5):
            sessions[0]["turns"][index].update(LEAK)
        found = verdicts(check(sessions))
        self.assertEqual(found["recached"], "better")
        sessions[1]["turns"] = sessions[1]["turns"][:10]
        self.assertEqual(verdicts(check(sessions))["recached"], "not enough data")

    def test_rollovers_a_day_limit_is_six(self):
        day = lambda count, session="a": [rolled(session, NOW - 60 * n) for n in range(count)]
        six, seven = verdicts(check(self.sessions(), rows=day(6))), verdicts(check(self.sessions(), rows=day(7)))
        self.assertEqual((six["rollovers"], six["rollover_gaps"]), ("same", "same"))
        self.assertEqual((seven["rollovers"], seven["rollover_gaps"]), ("worse", "worse"))
        result = check(self.sessions(), rows=day(7))
        self.assertIn("the most for one chat in one day: 7, the limit is 6", result["checks"][3]["line"])
        self.assertIn("median 1 minutes", result["checks"][6]["line"])
        # two chats with six each are not seven in one chat
        self.assertEqual(verdicts(check(self.sessions(), rows=day(6) + day(6, "b")))["rollovers"], "same")

    def test_rollovers_without_a_log_or_a_second_rollover(self):
        for rows in (None, []):
            found = verdicts(check(self.sessions(), rows=rows))
            self.assertEqual((found["rollovers"], found["rollover_gaps"]), ("not enough data",) * 2)
        one = check(self.sessions(), rows=[rolled("a", NOW - 60)])
        self.assertEqual((verdicts(one)["rollovers"], verdicts(one)["rollover_gaps"]), ("same", "not enough data"))
        self.assertIn("no chat rolled over twice", one["checks"][6]["line"])

    def test_rollovers_outside_the_recent_window_are_not_counted(self):
        old = [rolled("a", CHANGE - DAY - 60 * n) for n in range(9)]
        found = check(self.sessions(), rows=old)
        self.assertEqual(verdicts(found)["rollovers"], "same")
        self.assertEqual(found["checks"][3]["recent"]["count"], 0)

    def test_compactions_are_a_share_of_all_spend(self):
        recent = [{"session": "new", "time": CHANGE + DAY, "cost": 2.0}] * 2
        old = [{"session": "old", "time": CHANGE - 2 * DAY, "cost": 0.0}]
        found = check(self.sessions(0.1, 0.1), recent + old)
        self.assertEqual(verdicts(found)["compactions"], "worse")
        self.assertEqual((found["checks"][2]["recent"]["count"], found["checks"][2]["baseline"]["count"]), (2, 1))
        self.assertAlmostEqual(found["checks"][2]["recent"]["share"], 0.5)
        self.assertEqual(verdicts(check(self.sessions(0.1, 0.1), old))["compactions"], "same")
        outside = [{"session": "x", "time": NOW + DAY, "cost": 9.0}]
        self.assertEqual(check(self.sessions(0.1, 0.1), outside)["checks"][2]["recent"]["count"], 0)

    def test_worker_spend_in_long_chats_by_period(self):
        worker = lambda ident, updated, billed: {"id": ident, "kind": "worker", "billed": billed, "turns": [], "updated": updated}
        before = [worker("a", CHANGE - DAY, 5.0), worker("b", CHANGE - DAY, 20.0)]
        after = [worker("c", NOW - DAY, 30.0), worker("d", NOW - DAY, 5.0)]
        found = check(self.sessions() + before + after)
        self.assertEqual(verdicts(found)["long_chats"], "worse")
        self.assertAlmostEqual(found["checks"][4]["recent"]["share"], 30 / 35)
        self.assertAlmostEqual(found["checks"][4]["baseline"]["share"], 0.0)
        flipped = check(self.sessions() + [worker("a", NOW - DAY, 5.0), worker("b", NOW - DAY, 20.0), worker("c", CHANGE - DAY, 30.0), worker("d", CHANGE - DAY, 5.0)])
        self.assertEqual(verdicts(flipped)["long_chats"], "better")
        self.assertEqual(verdicts(check(self.sessions()))["long_chats"], "not enough data")

    def test_the_report_has_seven_lines_in_order_and_an_overall(self):
        result = check(self.sessions(), rows=[rolled("a", NOW - 60 * n) for n in range(7)])
        self.assertEqual([row["id"] for row in result["checks"]], list(costcheck.ORDER))
        lines = costcheck.report(result).splitlines()
        self.assertEqual(len(lines), 9)
        self.assertTrue(lines[0].startswith("cost check, "))
        for number, label in enumerate(("dollars a turn", "cold share", "compactions", "rollovers", "worker spend in long chats",
                                        "whole chat re-cached", "rollover gaps"), 1):
            self.assertTrue(lines[number].startswith(f"{number}. {label}: "), lines[number])
        self.assertTrue(lines[8].startswith("overall: worse (2 worse: rollovers, rollover gaps)"))
        self.assertEqual(costcheck.report(check(self.sessions(), rows=None)).splitlines()[-1].split(" ")[1], "better")

    def test_the_log_row_has_numbers_and_no_text(self):
        row = costcheck.log_row(check(self.sessions(), rows=[rolled("a", NOW)]))
        self.assertEqual(set(row), {"date", "time", "days", "overall", "checks"})
        self.assertEqual(list(row["checks"]), list(costcheck.ORDER))
        self.assertEqual(set(row["checks"]["per_turn"]), {"verdict", "recent", "baseline"})
        self.assertNotIn('"line"', json.dumps(row))
        self.assertNotIn('"old"', json.dumps(row))   # no chat ids either


def make_db(path, chats, messages, compactions=(), time_column=True):
    """chats: (id, parent, cost, updated); messages: (session, seq, seconds, read, write); compactions: (session, seq, seconds, cost, status)."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, cost REAL, time_updated INTEGER)")
    db.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, data TEXT" + (", time_created INTEGER)" if time_column else ")"))
    db.executemany("INSERT INTO session_v2 VALUES (?, ?, ?, ?)", [(i, p, c, int(u * 1000)) for i, p, c, u in chats])
    rows = []
    for session, seq, when, read, write in messages:
        data = {"time": {"created": int(when * 1000)}, "cost": (read * 0.1 + write * 1.25 + 2500) * 5e-6,
                "tokens": {"input": 0, "output": 1000, "cache": {"read": read, "write": write}}}
        rows.append((session, "assistant", seq, json.dumps(data), int(when * 1000)))
    for session, seq, when, spent, status in compactions:
        data = {"time": {"created": int(when * 1000)}, "status": status, **({} if spent is None else {"cost": spent})}
        rows.append((session, "compaction", seq, json.dumps(data), int(when * 1000)))
    for session, kind, seq, data, created in rows:
        if time_column:
            db.execute("INSERT INTO session_message VALUES (?, ?, ?, ?, ?)", (session, kind, seq, data, created))
        else:
            db.execute("INSERT INTO session_message VALUES (?, ?, ?, ?)", (session, kind, seq, data))
    db.commit()
    db.close()


class CommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.db = self.folder / "opencode.db"
        self.history = self.folder / "settings.jsonl"
        self.history.write_text(json.dumps(HISTORY[0]) + "\n")
        self.log = self.folder / "out" / "cost-check.jsonl"
        self.rollovers = self.folder / "rollover.log"
        self.rollovers.write_text("".join(json.dumps(rolled("a", NOW - 60 * n)) + "\n" for n in range(3)) + "not json\n")
        self.make()

    def make(self, time_column=True):
        def turns(session, start):
            return [(session, n, start + 60 * n, *(COLD if n % 2 == 0 else WARM).values()) for n in range(40)]
        messages = turns("root", CHANGE - 3 * DAY) + [(s, n + 100, w, r, wr) for s, n, w, r, wr in turns("root", CHANGE + DAY)]
        chats = [("root", None, 20.0, NOW - 3600), ("tiny", None, 0.5, NOW - 3600), ("w1", "root", 4.0, NOW - 3600)]
        compactions = [("root", 300, CHANGE + DAY, 2.0, "completed"), ("root", 301, CHANGE + DAY, 9.0, "failed"), ("root", 302, CHANGE + DAY, None, "completed")]
        if self.db.exists():
            self.db.unlink()
        make_db(self.db, chats, messages, compactions, time_column)

    def run_main(self, *extra, home=None):
        args = ["--check", "--db", str(self.db), "--settings-log", str(self.history), "--config", str(self.folder / "none.json"),
                "--check-log", str(self.log), "--rollover-log", str(self.rollovers), *extra]
        out = io.StringIO()
        with mock.patch("time.time", return_value=NOW), contextlib.redirect_stdout(out):
            code = cost.main(args)
        return code, out.getvalue()

    def test_the_command_prints_the_report_and_one_dated_row(self):
        code, text = self.run_main()
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertEqual(len(lines), 9)
        self.assertTrue(lines[1].startswith("1. dollars a turn: same"), lines[1])      # the same chat before and after
        self.assertIn("3. compactions: worse", lines[3])                                  # the failed one is left out; one with no cost counts as nothing
        self.assertIn("2 recently costing $2.00", lines[3])
        self.assertTrue(lines[4].startswith("4. rollovers: same (3 recently"), lines[4])
        saved = self.log.read_text().splitlines()
        self.assertEqual(len(saved), 1)
        row = json.loads(saved[0])
        self.assertEqual((row["date"], row["days"]), (costcheck.day_of(NOW), 7))
        self.assertNotIn('"line"', saved[0])
        self.run_main()
        self.assertEqual(len(self.log.read_text().splitlines()), 2)
        self.assertEqual([path.name for path in self.log.parent.iterdir()], ["cost-check.jsonl"])

    def test_json_output(self):
        code, text = self.run_main("--json")
        found = json.loads(text)
        self.assertEqual([row["id"] for row in found["checks"]], list(costcheck.ORDER))
        self.assertEqual((found["days"], found["overall"]), (7, "worse"))

    def test_days_sets_only_the_recent_window(self):
        _, text = self.run_main("--days", "2", "--json")
        found = json.loads(text)
        self.assertEqual(found["days"], 2)
        self.assertAlmostEqual(found["recent_from"], NOW - 2 * DAY)
        self.assertEqual(found["checks"][0]["verdict"], "not enough data")    # no turn in the last two days

    def test_trend_still_looks_back_30_days(self):
        out = io.StringIO()
        with mock.patch("time.time", return_value=NOW), contextlib.redirect_stdout(out):
            cost.main(["--trend", "--json", "--db", str(self.db), "--settings-log", str(self.history), "--config", str(self.folder / "none.json")])
        self.assertEqual(len(json.loads(out.getvalue())), 2)
        self.assertFalse(self.log.exists())

    def test_default_paths_follow_the_environment(self):
        with mock.patch.dict(os.environ, {"AGENTGRAPH_COST_CHECK_HOME": str(self.folder / "own"), "AGENTGRAPH_ROLLOVER_HOME": str(self.folder / "roll")}):
            self.assertEqual(costcheck.check_log_path(), self.folder / "own" / "cost-check.jsonl")
            self.assertEqual(costcheck.rollover_log_path(), self.folder / "roll" / "rollover.log")
            (self.folder / "roll").mkdir()
            (self.folder / "roll" / "rollover.log").write_text(json.dumps(rolled("a", NOW)) + "\n")
            out = io.StringIO()
            with mock.patch("time.time", return_value=NOW), contextlib.redirect_stdout(out):
                cost.main(["--check", "--db", str(self.db), "--settings-log", str(self.history), "--config", str(self.folder / "none.json")])
            self.assertIn("4. rollovers: same (1 recently", out.getvalue())
            self.assertEqual(len((self.folder / "own" / "cost-check.jsonl").read_text().splitlines()), 1)

    def test_a_database_without_the_time_column_gets_the_time_from_the_message(self):
        self.make(time_column=False)
        _, text = self.run_main()
        self.assertIn("2 recently costing $2.00", text.splitlines()[3])

    def test_a_missing_rollover_log_is_not_enough_data(self):
        self.rollovers.unlink()
        _, text = self.run_main()
        self.assertIn("4. rollovers: not enough data", text)

    def test_an_unwritable_log_still_prints(self):
        blocker = self.folder / "file"
        blocker.write_text("x")
        self.log = blocker / "sub" / "cost-check.jsonl"
        code, text = self.run_main()
        self.assertEqual((code, len(text.splitlines())), (0, 9))


if __name__ == "__main__":
    unittest.main()
