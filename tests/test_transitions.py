"""The history rules of `agentgraph check` (agentgraph.transitions): each rule on a graph that changed since the last
state that passed, the record itself, and `check` end to end."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from agentgraph import outputs, transitions
from agentgraph.check import main
from agentgraph.sources import graph_files

T = 1795037000


def node(i, typ, agent, deps=(), status="pending", **kw):
    n = {"id": i, "type": typ, "agent": agent, "goal": f"goal {i}", "depends_on": list(deps), "scope": [],
         "done_when": "pytest -q passes", "status": status, "attempts": 0, "tiers": [agent], "evidence": "",
         "session": None, "started": None, "finished": None, "stalled": False, "verdict": "",
         "artifacts": [{"id": "handoff", "path": f".opencode/artifacts/t/{i}/r1/handoff.md", "revision": 1}],
         "inputs": [{"from": d, "artifact": "handoff", "revision": 1} for d in deps]}
    n.update(kw)
    return n


def base(done=False):
    """n1 passed (explorer); n2 implement running (or passed); n3 gate pending (or passed)."""
    return {
        "task": "t", "goal": "demo", "status": "passed" if done else "running", "revision": 1,
        "objectives": [{"id": "o1", "text": "feature works", "nodes": ["n2"]}], "amendments": [], "lessons": [],
        "created": T, "updated": T + 100,
        "events": [{"time": T, "node": "", "event": "declared", "note": "3 nodes"},
                   {"time": T + 5, "node": "n1", "event": "dispatched", "note": "explorer"},
                   {"time": T + 50, "node": "n1", "event": "evaluated", "note": "ok"},
                   {"time": T + 60, "node": "n2", "event": "dispatched", "note": "impl-hard"}],
        "nodes": [
            node("n1", "research", "explorer", status="passed", evidence="3 files", attempts=1, session="ses_1",
                 started=T + 5, finished=T + 50),
            node("n2", "implement", "impl-hard", ["n1"], "passed" if done else "running", scope=["src/a.py", ".opencode/artifacts/t/n2/"],
                 evidence="ok" if done else "", attempts=1, session="ses_2", started=T + 60, tiers=["impl-hard"]),
            node("n3", "gate", "reviewer", ["n2"], "passed" if done else "pending", evidence="ok" if done else "",
                 verdict="confirmed" if done else "", feedback_to=["n2"], pattern="evaluate", attempts=1 if done else 0,
                 session="ses_3" if done else None)],
    }


def nd(g, i):
    return next(n for n in g["nodes"] if n["id"] == i)


def ev(g, node_id, kind, at, note=""):
    g["events"].append({"time": at, "node": node_id, "event": kind, "note": note})


class Rules(unittest.TestCase):
    def found(self, before, change, now=T + 1000):
        previous = transitions.state_of(before)
        after = copy.deepcopy(before)
        change(after)
        return transitions.problems(previous, after, now)

    def rejects(self, before, change, words):
        found = self.found(before, change)
        self.assertTrue(any(words in line for line in found), (words, found))

    def test_unchanged_graph_has_no_problem(self):
        self.assertEqual(self.found(base(), lambda g: None), [])
        self.assertEqual(self.found(base(True), lambda g: None), [])

    def test_a_normal_pass_is_accepted(self):
        def finish(g):
            ev(g, "n2", "evaluated", T + 200, "tests green")
            nd(g, "n2").update(status="passed", evidence="12 pass", finished=T + 200)
            g["updated"] = T + 200
        self.assertEqual(self.found(base(), finish), [])

    def test_passed_work_cannot_change_or_vanish_without_invalidated(self):
        self.rejects(base(True), lambda g: nd(g, "n1").update(status="pending"), "n1 was passed and is now pending")
        self.rejects(base(True), lambda g: g["nodes"].pop(0), "n1 was passed and is gone")

        def reopen(g):
            nd(g, "n1")["status"] = "pending"
            ev(g, "n1", "invalidated", T + 300, "input changed")
        self.assertEqual(self.found(base(True), reopen), [])

    def test_newly_passed_needs_passed_dependencies(self):
        def early(g):
            ev(g, "n3", "dispatched", T + 100)
            ev(g, "n3", "evaluated", T + 101)
            nd(g, "n3").update(status="passed", evidence="x", verdict="confirmed", attempts=1)
        self.rejects(base(), early, "n3 is newly passed while its dependency n2 is running")

    def test_worker_pass_needs_dispatch_and_a_session_or_attempt(self):
        listed = base()
        listed["nodes"].append(node("n4", "research", "explorer"))

        def never(g):
            nd(g, "n4").update(status="passed", evidence="x", attempts=1)
            ev(g, "n4", "evaluated", T + 100)
        self.rejects(listed, never, "n4 (explorer) is newly passed but was never dispatched")

        def ghost(g):
            ev(g, "n2", "dispatched", T + 90)
            ev(g, "n2", "evaluated", T + 100)
            nd(g, "n2").update(status="passed", evidence="x", session=None, attempts=0)
        self.rejects(base(), ghost, "no session and no attempts")

    def test_orchestrator_and_check_nodes_need_no_dispatch(self):
        def local(g):
            g["nodes"].append(node("n4", "check", "orchestrator", ["n2"], "passed", evidence="ran it"))
            g["objectives"][0]["nodes"].append("n4")
            nd(g, "n2").update(status="passed", evidence="x")
            ev(g, "n2", "dispatched", T + 90)
            ev(g, "n2", "evaluated", T + 100)
            ev(g, "n4", "evaluated", T + 101)
        self.assertEqual(self.found(base(), local), [])

    def test_pass_needs_an_evaluation_event_since_it_last_ran(self):
        def bare(g):
            nd(g, "n2").update(status="passed", evidence="ok")
        self.rejects(base(), bare, "no completion without evaluation")

        def stale(g):  # an evaluation of an earlier run does not count after a new dispatch
            ev(g, "n2", "evaluated", T + 90)
            ev(g, "n2", "retried", T + 95)
            nd(g, "n2").update(status="passed", evidence="ok")
        self.rejects(base(), stale, "no completion without evaluation")

        def early_event(g):  # evaluated written before the status flips is fine
            ev(g, "n2", "evaluated", T + 90)
        previous = transitions.state_of(base())
        after = base()
        early_event(after)
        self.assertEqual(transitions.problems(previous, after, T + 1000), [])

    def test_retry_needs_attempts_to_go_up(self):
        failed = base()
        nd(failed, "n2").update(status="failed", evidence="red", failure_kind="code")
        self.rejects(failed, lambda g: nd(g, "n2").update(status="running"), "without attempts going up")
        self.assertEqual(self.found(failed, lambda g: nd(g, "n2").update(status="running", attempts=2)), [])

    def test_attempts_do_not_go_down(self):
        three = base()
        nd(three, "n2")["attempts"] = 3
        self.rejects(three, lambda g: nd(g, "n2").update(attempts=1), "attempts went down from 3 to 1")

    def test_stalled_on_a_passed_node_and_confirmed_pending_gate(self):
        def stall(g):
            ev(g, "n2", "evaluated", T + 100)
            nd(g, "n2").update(status="passed", evidence="x", stalled=True)
        self.rejects(base(), stall, "newly marked stalled")
        self.rejects(base(), lambda g: nd(g, "n3").update(verdict="confirmed"), "pending but already carries the verdict")

    def test_events_are_append_only_in_order_about_known_nodes(self):
        self.rejects(base(), lambda g: g.update(events=g["events"][:1]), "events were deleted")
        self.rejects(base(), lambda g: g["events"][0].update(note="rewritten"), "earlier events were edited")
        self.rejects(base(), lambda g: ev(g, "n2", "attempt", T + 10), "dated before the event before it")
        self.rejects(base(), lambda g: ev(g, "n99", "attempt", T + 100), "unknown node 'n99'")
        self.rejects(base(), lambda g: ev(g, "", "completed", T + 100), "the graph is not passed")
        self.rejects(base(), lambda g: ev(g, "n2", "attempt", T + 10 ** 9), "in the future")

    def test_completed_while_passed_and_event_for_a_removed_node(self):
        def done(g):
            g["status"] = "passed"
            for n in g["nodes"]:
                n.update(status="passed", evidence="ok", attempts=1, session=n["session"] or "s")
            nd(g, "n3")["verdict"] = "confirmed"
            ev(g, "n2", "evaluated", T + 100)
            ev(g, "n3", "dispatched", T + 101)
            ev(g, "n3", "evaluated", T + 102)
            ev(g, "", "completed", T + 103)
        self.assertEqual(self.found(base(), done), [])

    def test_revision_amendments_and_criteria(self):
        self.rejects(base(), lambda g: g.update(revision=0), "revision went down")
        self.rejects(base(), lambda g: g["objectives"][0].update(text="easier"), "objective o1 text changed without an amendment")
        self.rejects(base(), lambda g: nd(g, "n2").update(done_when="true"), "n2 done_when changed without an amendment")
        self.rejects(base(), lambda g: (nd(g, "n2").update(done_when="true"), g.update(revision=2)), "without an amendment")

        def amend(g):
            nd(g, "n2")["done_when"] = "true"
            g["revision"] = 2
            g["amendments"].append("n2 done_when: was 'pytest -q passes' (approved by user)")
        self.assertEqual(self.found(base(), amend), [])

    def test_updated_and_started_finished(self):
        self.rejects(base(), lambda g: g.update(updated=T + 5), "updated went back")
        self.rejects(base(), lambda g: g.update(updated=T - 5), "before created")
        self.rejects(base(), lambda g: nd(g, "n1").update(started=T + 500, finished=T + 10), "n1 started after it finished")

    def test_artifact_revisions(self):
        high = base()
        nd(high, "n1")["artifacts"][0].update(revision=2, path=".opencode/artifacts/t/n1/r2/handoff.md")
        nd(high, "n2")["inputs"][0]["revision"] = 2
        self.rejects(high, lambda g: nd(g, "n1")["artifacts"][0].update(revision=1), "went back from revision 2 to 1")
        done = base(True)
        self.rejects(done, lambda g: (nd(g, "n1")["artifacts"][0].update(revision=3, path=".opencode/artifacts/t/n1/r3/h.md"),
                                      nd(g, "n2")["inputs"][0].update(revision=3)), "with no new attempt")

        def rerun(g):
            nd(g, "n1")["artifacts"][0].update(revision=3, path=".opencode/artifacts/t/n1/r3/h.md")
            nd(g, "n2")["inputs"][0]["revision"] = 3
            nd(g, "n2")["attempts"] = 2
        self.assertEqual(self.found(done, rerun), [])

    def test_a_node_added_already_passed_is_held_only_to_its_dependencies(self):
        def import_(g):
            g["nodes"].append(node("n4", "research", "explorer", status="passed", evidence="x"))
        self.assertEqual(self.found(base(), import_), [])
        self.rejects(base(), lambda g: g["nodes"].append(node("n4", "research", "explorer", ["n2"], status="passed", evidence="x")),
                     "n4 is newly passed while its dependency n2 is running")

    def test_a_new_node_may_be_added_pending(self):
        def add(g):
            g["nodes"].append(node("n4", "research", "explorer"))
            g["objectives"][0]["nodes"].append("n4")
        self.assertEqual(self.found(base(), add), [])

    def test_many_violations_are_capped(self):
        def many(g):
            for i in range(20):
                ev(g, f"x{i}", "attempt", T + 100)
        found = self.found(base(), many)
        self.assertEqual(len(found), transitions.MAX_PER_RULE + 1)
        self.assertIn("15 more", found[-1])


class Warnings(unittest.TestCase):
    def test_history_free_warnings(self):
        with tempfile.TemporaryDirectory() as folder:
            graph_dir = Path(folder) / ".opencode" / "graph"
            graph_dir.mkdir(parents=True)
            raw = base(True)
            raw["task"] = "other"
            nd(raw, "n3").update(session="ses_2")  # a reviewer and an implementer on one session
            nd(raw, "n1").update(attempts=1)
            running = base()
            nd(running, "n2")["attempts"] = 3
            found = transitions.warnings(raw, graph_dir / "t.json")
            self.assertTrue(any("differs from the file name" in w for w in found), found)
            self.assertTrue(any("does not exist" in w for w in found), found)
            self.assertTrue(any("session ses_2 is used by" in w for w in found), found)
            (graph_dir.parent / "artifacts" / "t" / "n1" / "r1").mkdir(parents=True)
            (graph_dir.parent / "artifacts" / "t" / "n1" / "r1" / "handoff.md").write_text("x")
            self.assertFalse(any("n1 artifact" in w for w in transitions.warnings(raw, graph_dir / "t.json")))
            stall = transitions.warnings(running, graph_dir / "t.json")
            self.assertTrue(any("n2 has 3 attempts" in w and "stall candidate" in w for w in stall), stall)
            nd(running, "n2")["stalled"] = True
            self.assertFalse(any("stall candidate" in w for w in transitions.warnings(running, graph_dir / "t.json")))


class Record(unittest.TestCase):
    def test_state_keeps_what_the_rules_need_and_no_text(self):
        state = transitions.state_of(base(True))
        self.assertEqual(state["nodes"]["n2"]["attempts"], 1)
        self.assertEqual(state["nodes"]["n3"]["verdict"], "confirmed")
        self.assertEqual(state["events"]["count"], 4)
        self.assertEqual(state["amendments"], 0)
        self.assertNotIn("feature works", json.dumps(state))
        self.assertNotIn("pytest", json.dumps(state))

    def test_save_load_and_listing(self):
        with tempfile.TemporaryDirectory() as folder:
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertIsNone(transitions.load_state(fd, "t"))
                state = transitions.state_of(base())
                self.assertTrue(transitions.save_state(fd, "t", state))
                self.assertFalse(transitions.save_state(fd, "t", state))  # same bytes: not rewritten
                self.assertEqual(transitions.load_state(fd, "t"), json.loads(json.dumps(state)))
            finally:
                os.close(fd)
            self.assertEqual(sorted(os.listdir(Path(folder) / ".history")), ["t.state"])
            (Path(folder) / "t.json").write_text("{}")
            files, _ = graph_files(Path(folder))
            self.assertEqual([p.name for p in files], ["t.json"])  # the history is never taken for a graph

    def test_an_unreadable_record_is_a_warning_not_a_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".history").mkdir()
            (Path(folder) / ".history" / "t.state").write_text("not json")
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(transitions.HistoryError):
                    transitions.load_state(fd, "t")
            finally:
                os.close(fd)


class CheckEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.graph_dir = Path(self.tmp.name) / ".opencode" / "graph"
        self.graph_dir.mkdir(parents=True)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.structure = Path(self.tmp.name) / "no-structure"
        self.structure.mkdir()
        (self.structure / "agents").mkdir()

    def write(self, raw):
        (self.graph_dir / "t.json").write_text(json.dumps(raw, indent=1))

    def check(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--dir", str(self.graph_dir), "--home", str(self.home), "--structure", str(self.structure), *extra])
        return code, out.getvalue()

    def state_file(self):
        return self.graph_dir / ".history" / "t.state"

    def test_first_run_is_lint_only_and_saves_the_history(self):
        self.write(base(True))
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.state_file().exists())

    def test_a_regression_fails_and_the_history_is_kept(self):
        self.write(base(True))
        self.assertEqual(self.check()[0], 0)
        saved = self.state_file().read_bytes()
        broken = base(True)
        nd(broken, "n1")["status"] = "pending"
        broken["status"] = "running"
        self.write(broken)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("n1 was passed and is now pending", out)
        self.assertEqual(self.state_file().read_bytes(), saved)
        code, out = self.check()  # still compared with the last good state
        self.assertEqual(code, 1, out)

    def test_no_history_skips_the_rules_and_the_record(self):
        self.write(base(True))
        self.assertEqual(self.check("--no-history")[0], 0)
        self.assertFalse(self.state_file().exists())
        self.check()
        broken = base(True)
        nd(broken, "n1")["status"] = "pending"
        broken["status"] = "running"
        self.write(broken)
        code, out = self.check("--no-history")
        self.assertEqual(code, 0, out)

    def test_other_problems_stop_the_record_being_saved(self):
        raw = base(True)
        nd(raw, "n2")["evidence"] = ""  # passed without evidence: a lint problem
        self.write(raw)
        self.assertEqual(self.check()[0], 1)
        self.assertFalse(self.state_file().exists())

    def test_warnings_print_and_do_not_fail(self):
        raw = base()
        nd(raw, "n2")["attempts"] = 4
        self.write(raw)
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("warning: n2 has 4 attempts and is not marked stalled", out)

    def test_corrupt_history_is_ignored_with_a_warning_and_replaced(self):
        self.write(base())
        (self.graph_dir / ".history").mkdir()
        self.state_file().write_text("{broken")
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("warning:", out)
        self.assertEqual(json.loads(self.state_file().read_text())["version"], transitions.VERSION)

    def test_task_filter_does_not_touch_other_graphs_history(self):
        self.write(base())
        other = base()
        other["task"] = "other"
        (self.graph_dir / "other.json").write_text(json.dumps(other))
        self.assertEqual(self.check("--task", "t")[0], 0)
        self.assertEqual(sorted(p.name for p in (self.graph_dir / ".history").iterdir()), ["t.state"])


if __name__ == "__main__":
    unittest.main()
