"""`agentgraph graph set|event`: one locked, validated write per call (agentgraph.graphedit)."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests.launch import bare_env
from agentgraph import graphedit, transitions
from agentgraph.check import main as check_main

T = 1795037000
ORCH = Path(__file__).resolve().parents[1]


def graph():
    def node(i, typ, agent, deps=(), **kw):
        n = {"id": i, "type": typ, "agent": agent, "goal": f"goal {i}", "depends_on": list(deps), "scope": [],
             "done_when": "pytest -q passes", "status": "pending", "attempts": 0, "tiers": [agent], "evidence": "",
             "session": None, "started": None, "finished": None, "stalled": False, "verdict": ""}
        n.update(kw)
        return n
    return {"task": "t", "goal": "demo", "status": "running", "revision": 1,
            "objectives": [{"id": "o1", "text": "feature works", "nodes": ["n2", "n3"]}], "amendments": [], "lessons": [],
            "created": T, "updated": T + 1, "events": [{"time": T, "node": "", "event": "declared", "note": "3 nodes"}],
            "nodes": [node("n1", "research", "explorer"), node("n2", "implement", "impl-hard", ["n1"], scope=["src/a.py"]),
                      node("n3", "gate", "reviewer", ["n2"], feedback_to=["n2"]),
                      node("n4", "join", "orchestrator", ["n3"])]}


class Edit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / ".opencode" / "graph"
        self.dir.mkdir(parents=True)
        self.file = self.dir / "t.json"
        self.file.write_text(json.dumps(graph(), indent=2), encoding="utf-8")

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = graphedit.main([*args, "--dir", str(self.dir)])
        return code, out.getvalue()

    def raw(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def node(self, i):
        return next(n for n in self.raw()["nodes"] if n["id"] == i)

    def events(self, node_id=None):
        return [e["event"] for e in self.raw()["events"] if node_id is None or e["node"] == node_id]

    def check(self):
        out = io.StringIO()
        structure = Path(self.tmp.name) / "s"
        (structure / "g").mkdir(parents=True, exist_ok=True)
        with contextlib.redirect_stdout(out):
            code = check_main(["--dir", str(self.dir), "--home", self.tmp.name, "--structure", str(structure)])
        return code, out.getvalue()

    def refused(self, *args):
        before = self.file.read_bytes()
        code, out = self.run_cli(*args)
        self.assertEqual(code, 1, out)
        self.assertIn("refused", out)
        self.assertEqual(self.file.read_bytes(), before, "a refused write must leave the file as it was")
        return out

    def dispatch_pass(self, i, attempts="+1", evidence="tests pass", *more):
        self.assertEqual(self.run_cli("set", "t", i, "--status", "running", "--session", f"ses_{i}", "--attempts", attempts)[0], 0)
        self.assertEqual(self.run_cli("set", "t", i, "--status", "returned", "--note", "1 pass -> green")[0], 0)
        return self.run_cli("set", "t", i, "--status", "passed", "--evidence", evidence, *more)

    def test_dispatch_return_pass_sequence_writes_events_and_times(self):
        code, line = self.run_cli("set", "t", "n1", "--status", "running", "--session", "ses_a", "--attempts", "+1")
        self.assertEqual(code, 0, line)
        self.assertIn("n1: running attempts 1 session ses_a", line)
        self.assertEqual(self.events("n1"), ["dispatched"])
        self.assertIsNotNone(self.node("n1")["started"])
        self.assertEqual(self.run_cli("set", "t", "n1", "--status", "returned", "--note", "found 3 files")[0], 0)
        code, line = self.run_cli("set", "t", "n1", "--status", "passed", "--evidence", "3 files")
        self.assertEqual(code, 0, line)
        self.assertEqual(self.events("n1"), ["dispatched", "returned", "evaluated"])
        self.assertEqual(self.node("n1")["evidence"], "3 files")
        self.assertGreaterEqual(self.node("n1")["finished"], self.node("n1")["started"])
        self.assertEqual(self.check()[0], 0, self.check()[1])
        self.assertTrue((self.dir / ".history" / "t.state").exists())

    def test_failed_then_retried_and_join_event_names(self):
        self.assertEqual(self.dispatch_pass("n1")[0], 0)
        self.run_cli("set", "t", "n2", "--status", "running", "--session", "s2", "--attempts", "+1")
        self.assertEqual(self.run_cli("set", "t", "n2", "--status", "failed", "--evidence", "red", "--note", "edit -> 3 failures")[0], 0)
        self.refused("set", "t", "n2", "--status", "running")  # a retry must raise the attempts
        self.assertEqual(self.run_cli("set", "t", "n2", "--status", "running", "--attempts", "+1")[0], 0)
        self.assertEqual(self.events("n2"), ["dispatched", "attempt", "retried"])
        self.dispatch_pass("n2", "+1")
        self.assertEqual(self.dispatch_pass("n3", "+1", "ok", "--verdict", "confirmed")[0], 0)
        self.assertEqual(self.dispatch_pass("n4")[0], 0)
        self.assertEqual(self.events("n4")[-1], "joined")

    def test_refuses_to_reopen_passed_work_without_invalidate(self):
        self.dispatch_pass("n1")
        out = self.refused("set", "t", "n1", "--status", "pending")
        self.assertIn("n1 was passed and is now pending", out)
        out = self.refused("set", "t", "n1", "--status", "running", "--attempts", "+1")
        self.assertIn("n1 was passed", out)
        code, line = self.run_cli("set", "t", "n1", "--invalidate", "--note", "input changed")
        self.assertEqual(code, 0, line)
        self.assertEqual(self.node("n1")["status"], "pending")
        self.assertEqual(self.events("n1")[-1], "invalidated")
        code, _ = self.run_cli("event", "t", "n1", "invalidated", "--note", "again")
        self.assertEqual(code, 0)  # a pending node: the event alone is recorded

    def test_refuses_pass_before_dependency_and_lower_attempts(self):
        out = self.refused("set", "t", "n3", "--status", "passed", "--evidence", "x", "--session", "s", "--attempts", "+1")
        self.assertIn("n3 is newly passed while its dependency n2 is pending", out)
        self.run_cli("set", "t", "n1", "--status", "running", "--session", "s1", "--attempts", "3")
        out = self.refused("set", "t", "n1", "--attempts", "1")
        self.assertIn("attempts went down from 3 to 1", out)

    def test_refuses_a_pass_that_is_not_a_dispatch_first(self):
        out = self.refused("set", "t", "n1", "--status", "passed", "--evidence", "x")
        self.assertIn("never dispatched", out)

    def test_a_pass_without_evidence_and_a_confirmed_pending_gate_are_refused(self):
        self.dispatch_pass("n1")
        self.run_cli("set", "t", "n2", "--status", "running", "--session", "s2", "--attempts", "+1")
        self.assertIn("no evidence", self.refused("set", "t", "n2", "--status", "passed"))
        self.refused("set", "t", "n3", "--verdict", "confirmed")

    def test_expect_updated_catches_a_concurrent_writer(self):
        updated = self.raw()["updated"]
        self.assertEqual(self.run_cli("set", "t", "n1", "--status", "running", "--session", "a", "--attempts", "+1",
                                      "--expect-updated", str(updated))[0], 0)
        out = self.refused("set", "t", "n1", "--note", "late writer", "--expect-updated", str(updated))
        self.assertIn("changed since you read it", out)
        now = self.raw()["updated"]
        self.assertGreater(now, updated)
        self.assertEqual(self.run_cli("set", "t", "n1", "--note", "ok", "--expect-updated", str(now))[0], 0)

    def test_two_writers_with_the_same_expectation_one_wins(self):
        updated = self.raw()["updated"]
        results = []

        def go(session):
            results.append(graphedit.main(["set", "t", "n1", "--status", "running", "--session", session,
                                           "--attempts", "+1", "--expect-updated", str(updated), "--dir", str(self.dir)]))
        threads = [threading.Thread(target=go, args=(f"s{i}",)) for i in range(4)]
        with contextlib.redirect_stdout(io.StringIO()):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(sorted(results), [0, 1, 1, 1])
        self.assertEqual(self.events("n1"), ["dispatched"])

    def test_event_command_and_completion(self):
        self.assertEqual(self.run_cli("event", "t", "n1", "discovered", "--note", "found a bug elsewhere")[0], 0)
        self.assertEqual(self.events()[-1], "discovered")
        self.assertEqual(self.run_cli("event", "t", "attempt", "--note", "graph level")[0], 0)
        code, out = self.run_cli("event", "t", "n99", "attempt")
        self.assertIn(code, (1, 2))  # unknown node: nothing written
        self.assertEqual(self.run_cli("event", "t", "nonsense")[0], 2)
        self.refused("event", "t", "completed", "--note", "done")  # nodes are not passed: the graph cannot be marked passed

    def test_whole_graph_completes(self):
        for i in ("n1", "n2", "n3", "n4"):
            more = ("--verdict", "confirmed") if i == "n3" else ()
            self.assertEqual(self.dispatch_pass(i, "+1", "tests pass", *more)[0], 0, i)
        self.assertEqual(self.run_cli("event", "t", "completed", "--note", "all passed")[0], 0)
        self.assertEqual(self.raw()["status"], "passed")
        self.assertEqual(self.check()[0], 0, self.check()[1])

    def test_evidence_file_and_stalled(self):
        evidence = Path(self.tmp.name) / "evidence.txt"
        evidence.write_text("long evidence\n", encoding="utf-8")
        self.assertEqual(self.run_cli("set", "t", "n1", "--evidence-file", str(evidence), "--stalled", "true",
                                      "--note", "same output twice")[0], 0)
        self.assertEqual(self.node("n1")["evidence"], "long evidence")
        self.assertTrue(self.node("n1")["stalled"])
        self.assertEqual(self.events("n1"), ["stalled"])

    def test_unknown_task_node_and_bad_values_exit_2(self):
        self.assertEqual(self.run_cli("set", "nope", "n1", "--note", "x")[0], 2)
        self.assertEqual(self.run_cli("set", "t", "n99", "--note", "x")[0], 2)
        self.assertEqual(self.run_cli("set", "t", "n1", "--attempts", "lots")[0], 2)

    def test_pre_existing_problems_do_not_block_an_unrelated_edit_and_the_history_is_not_saved(self):
        raw = graph()
        raw["nodes"][0].update(status="passed", evidence="")  # already invalid: passed without evidence
        self.file.write_text(json.dumps(raw), encoding="utf-8")
        code, out = self.run_cli("set", "t", "n2", "--note", "unrelated")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.dir / ".history" / "t.state").exists())

    def test_history_from_a_hand_edit_is_what_the_helper_compares_with(self):
        self.dispatch_pass("n1")
        raw = self.raw()
        raw["nodes"][0]["attempts"] = 0  # a hand edit lowered the attempts after the last state
        self.file.write_text(json.dumps(raw), encoding="utf-8")
        code, out = self.run_cli("set", "t", "n1", "--note", "touch")
        self.assertEqual(code, 0, out)  # already broken before this write: not added by it
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("attempts went down", out)

    def test_runs_as_a_module(self):
        result = subprocess.run([sys.executable, "-P", "-m", "agentgraph.graphedit", "set", "t", "n1", "--note", "hi",
                                 "--dir", str(self.dir)], cwd=ORCH, capture_output=True, text=True,
                                env=bare_env(PYTHONPATH=str(ORCH)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("n1: pending", result.stdout)


if __name__ == "__main__":
    unittest.main()
