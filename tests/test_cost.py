import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentgraph import cost


def turn(read=0, write=0, output=1000, gap=0.5, between=(), dollars=None, tokens_in=0):
    made = {"input": tokens_in, "read": read, "write": write, "output": output, "gap": gap, "between": list(between), "cost": 0.0}
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
            self.assertEqual(turns[0], {"cost": 0.5, "input": 10, "read": 100, "write": 200, "output": 25, "gap": 0.0, "between": ["user"]})
            self.assertEqual((turns[1]["gap"], turns[1]["between"], turns[1]["write"]), (10.0, ["system", "user"], 70_000))
            self.assertEqual(len(turns), 3)
            text = cost.report(session, cost.analyse(turns), workers)
            self.assertIn("ses_root: $2.00 over 3 turns", text)
            self.assertIn("pause over 5 minutes", text)
            self.assertEqual(cost.load_turns("ses_old", path)[1], [])


if __name__ == "__main__":
    unittest.main()
