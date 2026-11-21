import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentgraph import status
from tests.test_sources import make_db, row

NOW = 100_000.0


def node(node_id, status_="pending", depends_on=(), **more):
    return {"id": node_id, "type": "implement", "agent": "impl-openai", "goal": "g " + node_id,
            "status": status_, "depends_on": list(depends_on), "scope": [f"src/{node_id}/"],
            "done_when": "tests pass", **more}


def artifact(path_id, revision):
    return {"id": path_id, "path": f"a/{path_id}/r{revision}", "revision": revision}


GRAPH = {
    "task": "fixture", "goal": "g", "status": "running", "session": "owner", "revision": 1,
    "created": 1, "updated": 2,
    "objectives": [{"id": "o1", "text": "t", "nodes": ["live", "done"]}],
    "nodes": [
        node("done", "passed", artifacts=[artifact("doc", 2)], evidence="ok", attempts=1),
        node("fresh", "pending", ["done"], inputs=[{"from": "done", "artifact": "doc", "revision": 2}]),
        node("old", "pending", ["done"], inputs=[{"from": "done", "artifact": "doc", "revision": 1}]),
        node("blocked-dep", "pending", ["live"]),
        node("live", "running", session="ses_busy"),
        node("quiet", "running", session="ses_idle"),
        node("orphan", "running", session="ses_gone"),
        node("nosession", "running"),
        node("unrep", "pending", session="ses_other_busy"),
        node("back", "returned", session="ses_idle"),
        node("seen", "passed", inputs=[{"from": "done", "artifact": "doc", "revision": 1}], evidence="ok"),
        node("loopy", "failed", attempts=3),
        node("same", "running", session="ses_busy", attempts=2),
    ],
    "events": [
        {"time": 1, "node": "same", "event": "attempt", "note": "tests fail on x"},
        {"time": 2, "node": "same", "event": "retried", "note": "tests fail on x"},
        {"time": 3, "node": "fresh", "event": "attempt", "note": "once"},
    ],
}


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        graph_dir = self.dir / ".opencode" / "graph"
        graph_dir.mkdir(parents=True)
        (graph_dir / "fixture.json").write_text(json.dumps(GRAPH))
        self.graph_dir = graph_dir
        self.db = self.dir / "o.db"
        # busy: ses_busy and ses_other_busy; idle since 90s ago: ses_idle; owner idle.
        make_db(self.db, [
            row("owner", updated=1),
            row("ses_busy", "owner", agent="impl-openai", busy=5000, idle=None),
            row("ses_other_busy", "owner", agent="impl-openai", busy=5000, idle=None),
            row("ses_idle", "owner", agent="impl-openai", busy=5000, idle=int((NOW - 90) * 1000)),
        ])

    def run_status(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = status.main(["--task", "fixture", "--dir", str(self.graph_dir), "--db", str(self.db), *extra], now=NOW)
        return code, out.getvalue(), err.getvalue()

    def document(self):
        code, out, _ = self.run_status("--json")
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_lists_what_is_ready_running_and_returned(self):
        doc = self.document()
        self.assertEqual(doc["ready"], ["fresh", "unrep"])  # not `old` (stale input) nor `blocked-dep` (live is running)
        self.assertEqual(doc["returned"], ["back"])
        by_id = {item["id"]: item for item in doc["running"]}
        self.assertEqual({key: item["liveness"] for key, item in by_id.items()},
                         {"live": "live", "quiet": "quiet", "orphan": "orphan", "nosession": "orphan", "same": "live"})
        self.assertAlmostEqual(by_id["quiet"]["idle_since"], NOW - 90)
        self.assertEqual([item["id"] for item in doc["unreported"]], ["unrep"])
        self.assertEqual(doc["liveness"], {"live": 2, "quiet": 1, "orphan": 2, "unreported": 1})
        self.assertTrue(doc["database"])

    def test_stale_inputs_and_passed_nodes_whose_inputs_changed(self):
        doc = self.document()
        self.assertEqual({(item["node"], item["read"], item["current"]) for item in doc["stale_inputs"]},
                         {("old", 1, 2), ("seen", 1, 2)})
        self.assertEqual(doc["passed_inputs_changed"], ["seen"])

    def test_stall_candidates(self):
        doc = self.document()
        stalls = {item["id"]: item["reasons"] for item in doc["stall_candidates"]}
        self.assertEqual(set(stalls), {"loopy", "same"})
        self.assertEqual(stalls["loopy"], ["3 attempts"])
        self.assertEqual(stalls["same"], ["two attempts with the same note"])

    def test_check_result_is_one_line_from_the_existing_check(self):
        doc = self.document()
        self.assertFalse(doc["check"]["ok"])  # the fixture's passed `seen` reads a stale input, which check reports
        self.assertTrue(any("stale input" in problem for problem in doc["check"]["problems"]))
        self.assertTrue(doc["check"]["headline"].startswith("PROBLEMS fixture"))
        self.assertIsInstance(doc["check"]["problems"], list)
        code, text, _ = self.run_status()
        self.assertEqual(code, 0)
        self.assertTrue(any(line.startswith("check: PROBLEMS (3)") for line in text.splitlines()))

    def test_plain_text_is_short_and_names_liveness(self):
        _, text, _ = self.run_status()
        self.assertLess(len(text.splitlines()), 12)
        self.assertIn("ready: fresh unrep", text)
        self.assertIn("quiet 1m", text)  # idle 90 seconds
        self.assertIn("orphan", text)
        self.assertIn("stall candidates: loopy", text)

    def test_unreadable_database_leaves_liveness_unknown_not_orphan(self):
        self.db.unlink()
        doc = self.document()
        self.assertFalse(doc["database"])
        states = {item["id"]: item["liveness"] for item in doc["running"]}
        self.assertEqual(states["live"], None)
        self.assertEqual(states["nosession"], "orphan")
        self.assertIn("unreadable", self.run_status()[1])

    def test_is_read_only_and_reports_a_missing_task(self):
        before = (self.graph_dir / "fixture.json").read_bytes(), self.db.read_bytes()
        self.run_status()
        self.assertEqual((self.graph_dir / "fixture.json").read_bytes(), before[0])
        self.assertEqual(self.db.read_bytes(), before[1])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = status.main(["--task", "nope", "--dir", str(self.graph_dir)])
        self.assertEqual((code, out.getvalue()), (2, ""))
        self.assertIn("not found", err.getvalue())


if __name__ == "__main__":
    unittest.main()
