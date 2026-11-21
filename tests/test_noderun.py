"""agentgraph node run|status|wait|cancel (agentgraph.noderun): one node of a task graph worked as a loop, on the scripted executor with
real check scripts in a temp project. No test starts OpenCode."""
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from agentgraph import __main__ as entry
from agentgraph import check, looprun, noderun

# fails twice, passes on the third call: the counter lives in the project, outside what a scripted worker touches
# (the counter file is named in the environment, so that the command names no file of the project)
CHECK_THIRD = 'f="$NODE_TEST_COUNT"; n=$(cat "$f" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$f"; echo "round $n"; [ "$n" -ge 3 ]'
TASK = "demo"


def graph(**changes):
    """A task graph with n0 passed, n1 (the node to run) pending behind it, and n2 pending behind n1."""
    nodes = [
        {"id": "n0", "type": "implement", "agent": "impl-openai", "goal": "Earlier work", "status": "passed", "depends_on": [], "scope": [],
         "done_when": "done", "evidence": "done", "changed_paths": []},
        {"id": "n1", "type": "implement", "agent": "impl-openai", "goal": "Make the counter reach three", "status": "pending",
         "depends_on": ["n0"], "scope": ["src/a.py"], "done_when": "the check passes", "evidence": "", "changed_paths": []},
        {"id": "n2", "type": "implement", "agent": "impl-openai", "goal": "Later work", "status": "pending", "depends_on": ["n1"], "scope": [],
         "done_when": "done", "evidence": "", "changed_paths": []},
    ]
    data = {"task": TASK, "goal": "Run a node as a loop", "status": "running", "created": 1, "updated": 2, "revision": 1,
            "objectives": [{"id": "o1", "text": "Deliver", "nodes": ["n1", "n2"]}], "nodes": nodes, "events": []}
    data["nodes"][1].update(changes)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(noderun, "POLL", 0.1))  # the node is kept current every tenth of a second
        self.project = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.directory = self.project / ".opencode" / "graph"
        self.directory.mkdir(parents=True)
        self.path = self.directory / f"{TASK}.json"
        self.write(graph())
        self.enterContext(mock.patch.dict(os.environ, {"NODE_TEST_COUNT": str(self.project.parent / f"{self.project.name}.count")}))
        self.addCleanup(lambda: Path(os.environ["NODE_TEST_COUNT"]).unlink(missing_ok=True))

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def read(self):
        return json.loads(self.path.read_text())

    def node(self, node_id="n1"):
        return next(n for n in self.read()["nodes"] if n["id"] == node_id)

    def run_node(self, script, wait=True, hook=None, **kw):
        priced = lambda items: [{"cost": 0.01, **item} for item in items]  # a spend limit needs every turn priced
        executor = looprun.ScriptedExecutor({k: priced(v) for k, v in script.items()} if isinstance(script, dict) else priced(script), hook=hook)
        kw.setdefault("check", CHECK_THIRD)
        if not self.node().get("budget"):
            kw.setdefault("cost", 5.0)  # a node that has a budget keeps it
        run_id, line = noderun.run(TASK, "n1", directory=self.directory, wait=wait, executor=executor, **kw)
        return run_id, line, executor

    def events(self):
        return [(e["node"], e["event"]) for e in self.read()["events"]]

    def check_graph(self, history=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(self.directory), *([] if history else ["--no-history"])])
        return code, out.getvalue()

    def refused(self, **kw):
        with self.assertRaises(noderun.NodeRefused) as caught:
            noderun.run(TASK, kw.pop("node", "n1"), directory=self.directory, executor=looprun.ScriptedExecutor([{}]), wait=True, **kw)
        return str(caught.exception)

    def run_in_thread(self, script, **kw):
        """A run that goes on in this process until it is cancelled (a worker that never answers)."""
        done = {}

        def go():
            try:
                done["result"] = self.run_node(script, **kw)
            except BaseException as exc:  # noqa: BLE001
                done["error"] = exc
        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 30)
        self.addCleanup(self.stop_runs)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.runs():
            time.sleep(0.05)
        self.assertTrue(self.runs(), done)
        return thread, done

    def runs(self):
        folder = looprun.loops_folder(self.project)
        return sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []

    def stop_runs(self):
        for folder in self.runs():
            if looprun.read_run(folder).get("status") not in looprun.FINAL:
                with contextlib.suppress(looprun.LoopError):
                    looprun.cancel(folder)

    def wait_until(self, test, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if test():
                return True
            time.sleep(0.05)
        return False


class ThreeRounds(Base):
    def test_fail_fail_pass_ends_returned_with_everything_filled_in(self):
        run_id, line, executor = self.run_node([{"cost": 0.1, "sessions": 1}, {"cost": 0.1}, {"cost": 0.1}])
        self.assertTrue(line.startswith("passed-check attempts=3 cost=$0.3000 seconds="), line)
        self.assertEqual(len(line.splitlines()), 1)
        relative = f".opencode/artifacts/{TASK}/n1/node-run-{run_id}.md"
        self.assertTrue(line.endswith(f"report={relative}"), line)
        node = self.node()
        self.assertEqual(node["status"], "returned")  # never passed: the orchestrator evaluates
        self.assertEqual(node["attempts"], 3)
        self.assertEqual(node["loop_run"], run_id)
        self.assertAlmostEqual(node["spent"], 0.3)
        self.assertIsInstance(node["started"], float)
        self.assertGreaterEqual(node["ended"], node["started"])
        self.assertEqual(node["check"], CHECK_THIRD)
        self.assertEqual(node["budget"], {"cost": 5.0, "attempts": 3})
        self.assertIn("the check passed in round 3", node["evidence"])
        self.assertIn(relative, node["evidence"])
        self.assertEqual(self.read()["status"], "running")
        self.assertIn(("n1", "dispatched"), self.events())
        self.assertIn(("n1", "returned"), self.events())
        self.assertNotIn(("n1", "evaluated"), self.events())
        self.assertEqual(self.node("n2")["status"], "pending")

    def test_the_report_has_rounds_check_output_cost_and_sessions(self):
        def write(n, call):
            if n == 1:
                (self.project / "src").mkdir(exist_ok=True)
                (self.project / "src" / "a.py").write_text("x = 1\n")
        run_id, line, executor = self.run_node([{"cost": 0.1}, {"cost": 0.1}, {"cost": 0.1}], hook=write)
        text = (self.project / ".opencode" / "artifacts" / TASK / "n1" / f"node-run-{run_id}.md").read_text()
        for expected in ("### Round 1", "### Round 3", "check: exit 1 (fail)", "check: exit 0 (pass)", "round 2", "worker impl-openai",
                         "## Files changed", "- src/a.py", "## Sessions", "$0.3000"):
            self.assertIn(expected, text)

    def test_the_worker_gets_the_goal_and_then_the_check_tail_in_one_session(self):
        brief = self.project / "brief.md"
        brief.write_text("Use the standard library only.")
        run_id, line, executor = self.run_node([{}, {}, {}], brief=str(brief))
        first, second = executor.calls[0], executor.calls[1]
        for expected in ("Run a node as a loop", "Make the counter reach three", "the check passes", "src/a.py", "Use the standard library only."):
            self.assertIn(expected, first["message"])
        self.assertIn("round 1", second["message"])
        self.assertEqual(len({call["session"] for call in executor.calls}), 1)
        self.assertEqual(first["cwd"], str(self.project))

    def test_the_graph_stays_valid_under_check(self):
        self.run_node([{}, {}, {}])
        code, output = self.check_graph()
        self.assertEqual(code, 0, output)

    def test_a_node_can_be_run_again_after_it_failed(self):
        self.run_node([{}], attempts=1)
        self.assertEqual(self.node()["status"], "failed")
        self.assertEqual(self.node()["attempts"], 1)
        run_id, line, _ = self.run_node([{}, {}], attempts=3, check="true", raise_budget=True, amend_check=True)
        self.assertTrue(line.startswith("passed-check attempts=1"), line)
        node = self.node()
        self.assertEqual((node["status"], node["attempts"]), ("returned", 2))  # the graph counts rounds over both runs
        self.assertIn(("n1", "retried"), self.events())
        self.assertEqual(self.check_graph()[0], 0)
        self.assertNotIn("failure_kind", self.node())  # the earlier failure is not the node's now
        self.assertIs(self.node()["stalled"], False)


class Verifier(Base):
    def test_a_rejection_goes_back_to_the_worker(self):
        script = {"impl-openai": [{"cost": 0.1}, {"cost": 0.1}],
                  "reviewer": [{"final": "The edge case is missing.\nVERDICT: FAIL", "cost": 0.05}, {"final": "VERDICT: PASS", "cost": 0.05}]}
        run_id, line, executor = self.run_node(script, check="true", verify="reviewer")
        self.assertTrue(line.startswith("passed-check attempts=2 cost=$0.3000"), line)
        self.assertEqual(len(executor.by_agent("impl-openai")), 2)
        self.assertEqual(len(executor.by_agent("reviewer")), 2)
        self.assertIn("The edge case is missing", executor.by_agent("impl-openai")[1]["message"])
        node = self.node()
        self.assertEqual((node["status"], node["verify"]), ("returned", "reviewer"))
        self.assertIn("verifier passed it", node["evidence"])
        text = (self.project / ".opencode" / "artifacts" / TASK / "n1" / f"node-run-{run_id}.md").read_text()
        self.assertIn("verifier reviewer", text)

    def test_the_check_passes_before_the_verifier_is_asked(self):
        script = {"impl-openai": [{}, {}, {}], "reviewer": [{"final": "VERDICT: PASS"}]}
        run_id, line, executor = self.run_node(script, verify="reviewer")
        self.assertEqual(len(executor.by_agent("reviewer")), 1)
        self.assertEqual(len(executor.by_agent("impl-openai")), 3)

    def test_the_verifier_may_not_be_the_worker(self):
        self.assertIn("different agent", self.refused(check="true", cost=1, verify="impl-openai"))

    def test_the_node_may_name_its_own_verifier_check_and_budget(self):
        data = graph(check="true", verify="reviewer", budget={"cost": 2.0, "attempts": 2})
        self.write(data)
        script = {"impl-openai": [{}], "reviewer": [{"final": "VERDICT: PASS"}]}
        executor = looprun.ScriptedExecutor({"impl-openai": [{"cost": 0.1}], "reviewer": [{"final": "VERDICT: PASS", "cost": 0.1}]})
        run_id, line = noderun.run(TASK, "n1", directory=self.directory, wait=True, executor=executor)
        self.assertTrue(line.startswith("passed-check attempts=1"), line)
        self.assertEqual(looprun.read_run(looprun.run_dir(self.project, run_id))["limits"]["attempts"], 2)


class Limits(Base):
    def test_the_cost_limit_stops_the_run_with_the_word_limit(self):
        run_id, line, _ = self.run_node([{"cost": 0.4}] * 5, cost=0.5, attempts=5, check="false")
        self.assertTrue(line.startswith("limit attempts=2 "), line)
        node = self.node()
        self.assertEqual(node["status"], "failed")
        self.assertIn("limit after 2 rounds", node["evidence"])
        self.assertAlmostEqual(node["spent"], 0.8)
        self.assertEqual(node["failure_kind"], "limit")  # not a fault in the code: the money ran out
        self.assertIsInstance(node["ended"], float)

    def test_the_attempt_limit_stops_the_run_with_the_word_failed(self):
        run_id, line, _ = self.run_node([{"cost": 0.01}] * 5, attempts=2, check="false")
        self.assertTrue(line.startswith("failed attempts=2 "), line)
        self.assertEqual(self.node()["status"], "failed")
        self.assertIn("failed after 2 rounds", self.node()["evidence"])

    def test_a_worker_turn_that_hits_the_time_limit_is_a_limit_not_a_stall(self):
        run_id, line, _ = self.run_node([{"hang": True}], attempts=1, attempt_seconds=0.3, check="false")
        self.assertTrue(line.startswith("limit "), line)
        node = self.node()
        self.assertEqual((node["status"], node["stalled"]), ("failed", False))  # the controller saw no stall: the worker was cut off
        self.assertEqual(node["failure_kind"], "limit")
        self.assertNotIn(("n1", "stalled"), self.events())
        self.assertIn("limit after 1 round", node["evidence"])
        self.assertEqual(self.check_graph()[0], 0)

    def test_an_agent_that_fails_blocks_the_node(self):
        run_id, line, _ = self.run_node([{"error": "provider down"}], check="false")
        self.assertTrue(line.startswith("blocked "), line)
        self.assertEqual(self.node()["status"], "blocked")


class Refusals(Base):
    def untouched(self, before):
        self.assertEqual(self.read(), before)
        self.assertEqual(self.runs(), [])

    def test_a_node_whose_dependency_has_not_passed(self):
        before = graph()
        before["nodes"][0]["status"] = "returned"
        self.write(before)
        self.assertIn("n0 has not passed", self.refused(check="true", cost=1))
        self.untouched(before)

    def test_a_node_that_is_running_or_passed(self):
        for status in ("running", "passed"):
            with self.subTest(status=status):
                before = graph(status=status, session="ses_x") if status == "running" else graph(status=status)
                self.write(before)
                self.assertIn(f"already {status}", self.refused(check="true", cost=1))
                self.untouched(before)

    def test_a_node_without_a_check(self):
        self.assertIn("no check command", self.refused(cost=1))
        self.untouched(graph())

    def test_a_run_without_a_spend_limit(self):
        self.assertIn("spend limit", self.refused(check="true"))
        self.untouched(graph())

    def test_a_node_without_a_workable_agent(self):
        for change in ({"agent": "orchestrator"}, {"agent": "user"}, {"type": "human"}, {"agent": "Not An Agent"}):
            with self.subTest(change=change):
                before = graph(**change)
                self.write(before)
                self.assertIn("no agent that can do the work", self.refused(check="true", cost=1))
                self.untouched(before)

    def test_a_node_the_graph_does_not_have(self):
        self.assertIn("no node 'zz'", self.refused(node="zz", check="true", cost=1))

    def test_the_command_line_says_it_in_words_and_exits_with_one(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main(["run", "--task", TASK, "--node", "n2", "--dir", str(self.directory), "--check", "true", "--cost", "1"])
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue().strip(), "node n2 is not ready: n1 has not passed")


class Cancel(Base):
    def test_cancel_ends_the_run_and_blocks_the_node(self):
        thread, done = self.run_in_thread([{"hang": True}])
        self.assertTrue(self.wait_until(lambda: self.node()["status"] == "running" and self.node().get("loop_run")))
        self.assertTrue(noderun.status(TASK, "n1", self.directory).startswith("running "))
        line = noderun.cancel(TASK, "n1", self.directory)
        self.assertTrue(line.startswith("blocked "), line)
        thread.join(20)
        node = self.node()
        self.assertEqual(node["status"], "blocked")
        self.assertIn("blocked after", node["evidence"])
        self.assertIsInstance(node["ended"], float)
        self.assertIn("report=.opencode/artifacts/", noderun.status(TASK, "n1", self.directory))

    def test_cancel_of_a_node_with_no_run_is_refused(self):
        with self.assertRaises(noderun.NodeRefused) as caught:
            noderun.cancel(TASK, "n1", self.directory)
        self.assertIn("has no run", str(caught.exception))

    def test_cancel_of_a_finished_run_is_refused(self):
        self.run_node([{}, {}, {}])
        with self.assertRaises(noderun.NodeRefused) as caught:
            noderun.cancel(TASK, "n1", self.directory)
        self.assertIn("no run that is going on", str(caught.exception))


class TwoRuns(Base):
    def test_the_second_run_of_a_node_is_refused_and_leaves_no_run_behind(self):
        thread, done = self.run_in_thread([{"hang": True}])
        self.assertTrue(self.wait_until(lambda: self.node()["status"] == "running"))
        first = self.runs()
        message = self.refused(check="true", cost=1)
        self.assertIn("already running", message)
        self.assertIn(first[0].name, message)
        self.assertEqual(self.runs(), first)
        noderun.cancel(TASK, "n1", self.directory)

    def test_two_runs_started_together_leave_one(self):
        results = []

        def go():
            try:
                noderun.run(TASK, "n1", directory=self.directory, check="true", cost=1, wait=True, executor=looprun.ScriptedExecutor([{"cost": 0.1}]))
                results.append("ran")
            except noderun.NodeRefused as exc:
                results.append(str(exc))
        threads = [threading.Thread(target=go) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(results.count("ran"), 1, results)
        self.assertEqual(len(self.runs()), 1)


class KilledController(Base):
    def start_detached(self):
        script = self.project / "turns.json"
        script.write_text(json.dumps([{"hang": True}]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main(["run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--check", "false", "--cost", "1",
                                 "--executor", f"scripted:{script}"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("started", out.getvalue())
        (run,) = self.runs()

        def cleanup():
            with contextlib.suppress(Exception):
                pid = looprun.read_run(run).get("pid")
                if pid:
                    os.killpg(pid, signal.SIGKILL)
        self.addCleanup(cleanup)
        self.assertTrue(self.wait_until(lambda: looprun.read_run(run).get("status") == "running" and looprun.read_run(run).get("pid")))
        return run

    def test_a_detached_run_that_is_killed_is_reported_as_it_is(self):
        run = self.start_detached()
        self.assertTrue(noderun.status(TASK, "n1", self.directory).startswith("running "))
        os.killpg(looprun.read_run(run)["pid"], signal.SIGKILL)
        self.assertTrue(self.wait_until(lambda: not looprun._held(run)))
        line = noderun.status(TASK, "n1", self.directory)
        self.assertTrue(line.startswith("blocked "), line)  # no controller holds the run, and the run is not over
        self.assertEqual(self.node()["status"], "running")  # nothing is invented: the files say what they said
        code, output = self.check_graph()
        self.assertEqual(code, 0, output)
        self.assertIn(f"warning: n1 is running, but its loop run {run.name} is interrupted: no controller holds it", output)
        self.assertEqual(noderun.wait(TASK, "n1", self.directory, timeout=5), line)

    def test_cancel_ends_a_run_whose_controller_is_gone(self):
        run = self.start_detached()
        os.killpg(looprun.read_run(run)["pid"], signal.SIGKILL)
        self.assertTrue(self.wait_until(lambda: not looprun._held(run)))
        line = noderun.cancel(TASK, "n1", self.directory)
        self.assertTrue(line.startswith("blocked "), line)
        self.assertEqual(self.node()["status"], "blocked")
        code, output = self.check_graph()
        self.assertEqual(code, 0, output)
        self.assertNotIn("interrupted", output)


class Detached(Base):
    def test_a_detached_run_ends_the_node_and_wait_prints_the_line(self):
        script = self.project / "turns.json"
        script.write_text(json.dumps([{"cost": 0.1}, {"cost": 0.1}, {"cost": 0.1}]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main(["run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--check", CHECK_THIRD, "--cost", "1",
                                 "--executor", f"scripted:{script}"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertRegex(out.getvalue(), r"^run \d{8}-\d{6}-[0-9a-f]{4} started")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main(["wait", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--timeout", "60"])
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("passed-check attempts=3 cost=$0.3000"), out.getvalue())
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertEqual(self.node()["status"], "returned")


class CommandLine(Base):
    def test_node_run_wait_status_through_the_main_entry(self):
        script = self.project / "turns.json"
        script.write_text(json.dumps([{"cost": 0.01}] * 3))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = entry.main(["node", "run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--check", CHECK_THIRD, "--cost", "1",
                               "--wait", "--executor", f"scripted:{script}"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertTrue(out.getvalue().startswith("passed-check attempts=3"), out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = entry.main(["node", "status", "--task", TASK, "--node", "n1", "--dir", str(self.directory)])
        self.assertEqual((code, out.getvalue().split()[0]), (0, "passed-check"))

    def test_status_of_a_node_with_no_run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main(["status", "--task", TASK, "--node", "n1", "--dir", str(self.directory)])
        self.assertEqual(code, 1)
        self.assertIn("has no run", out.getvalue())

    def test_the_status_line_never_carries_message_text(self):
        self.run_node([{"text": "SECRET-WORKER-TEXT", "final": "SECRET-FINAL"}] * 3)
        line = noderun.status(TASK, "n1", self.directory)
        self.assertNotIn("SECRET", line)
        self.assertRegex(line, r'^passed-check attempts=3 cost=(\$[0-9.]+|unknown) seconds=\d+ check="[^"]*" sha256=[0-9a-f]{64} report=\S+\.md$')


class Budget(Base):
    """The budget belongs to the node: every run draws on the same one."""

    def test_a_second_run_gets_what_is_left_and_the_run_after_that_is_refused_at_zero(self):
        thread, done = self.run_in_thread([{"cost": 0.1}, {"hang": True}], check="false", attempts=5, cost=2.0)
        self.assertTrue(self.wait_until(lambda: self.node().get("attempts") == 2 and self.node().get("spent")))
        noderun.cancel(TASK, "n1", self.directory)
        thread.join(20)
        first = self.node()
        self.assertEqual((first["status"], first["attempts"]), ("blocked", 2))
        self.assertAlmostEqual(first["spent"], 0.11)
        run_id, line, _ = self.run_node([{}] * 5, check="false")  # no --cost or --attempts: the node's own budget
        limits = looprun.read_run(looprun.run_dir(self.project, run_id))["limits"]
        self.assertEqual(limits["attempts"], 3)  # five less the two the node has used
        self.assertAlmostEqual(limits["cost"], 2.0 - first["spent"])
        self.assertTrue(line.startswith("failed attempts=3 "), line)
        node = self.node()
        self.assertEqual(node["attempts"], 5)
        self.assertAlmostEqual(node["spent"], first["spent"] + 0.03)  # a running total over both runs
        message = self.refused(check="false")
        self.assertIn("used 5 of its 5 attempts", message)
        self.assertIn("--raise-budget", message)
        self.assertEqual(len(self.runs()), 2)  # the refused run left nothing behind

    def test_a_run_is_refused_when_the_money_is_gone(self):
        self.run_node([{"cost": 0.4}] * 5, cost=0.5, attempts=5, check="false")
        self.assertAlmostEqual(self.node()["spent"], 0.8)
        message = self.refused(check="false")
        self.assertIn("spent $0.8000 of its $0.5 budget", message)
        self.assertIn("--raise-budget", message)

    def test_cost_and_attempts_do_not_change_a_budget_the_node_has(self):
        self.run_node([{}], attempts=1, cost=5.0, check="false")
        message = self.refused(check="false", cost=9.0)
        self.assertIn("--raise-budget", message)
        self.assertEqual(self.node()["budget"], {"cost": 5.0, "attempts": 1})
        run_id, line, _ = self.run_node([{}], attempts=2, cost=9.0, check="false", raise_budget=True)
        self.assertEqual(self.node()["budget"], {"cost": 9.0, "attempts": 2})
        self.assertIn(("n1", "amended"), self.events())
        note = next(e["note"] for e in self.read()["events"] if e["event"] == "amended")
        self.assertIn("cost $5 -> $9", note)
        self.assertEqual(self.check_graph()[0], 0)

    def test_the_same_numbers_again_need_no_amendment(self):
        self.run_node([{}], attempts=3, cost=5.0, check="true")
        self.run_node([{}], attempts=3, cost=5.0, check="true")
        self.assertNotIn(("n1", "amended"), self.events())

    def test_spent_stays_unset_until_a_cost_is_known(self):
        executor = looprun.ScriptedExecutor([{}])  # a turn with no price
        noderun.run(TASK, "n1", directory=self.directory, check="false", cost=5.0, attempts=1, wait=True, executor=executor)
        node = self.node()
        self.assertNotIn("spent", node)  # unknown is not nought
        self.assertEqual(node.get("unpriced"), 1)
        self.assertEqual(self.check_graph()[0], 0)

    def test_a_cost_that_is_partly_unknown_says_at_least(self):
        executor = looprun.ScriptedExecutor([{"cost": 0.1}, {}, {}, {}])
        run_id, line = noderun.run(TASK, "n1", directory=self.directory, check="false", cost=5.0, attempts=3, wait=True, executor=executor)
        self.assertIn("cost=at least $0.1000 (1 turn with no known cost)", line)
        node = self.node()
        self.assertAlmostEqual(node["spent"], 0.1)
        self.assertEqual(node["unpriced"], 1)
        self.assertIn("at least $0.1000 (1 turn with no known cost)", node["evidence"])
        code, output = self.check_graph()
        self.assertEqual(code, 0, output)
        self.assertIn("n1 has 1 turn with no known cost: it spent at least $0.10", output)


class Amendment(Base):
    def test_another_check_after_the_first_dispatch_is_refused_without_amend_check(self):
        self.run_node([{}], attempts=1, check="false")
        message = self.refused(check="true", raise_budget=True, attempts=3)
        self.assertIn("--amend-check", message)
        self.assertIn("sha256", message)
        self.assertEqual(self.node()["check"], "false")

    def test_amend_check_writes_the_event_and_an_amendment_and_the_graph_stays_valid(self):
        self.run_node([{}], attempts=1, check="false")
        revision = self.read()["revision"]
        run_id, line, _ = self.run_node([{}], attempts=3, check="true", raise_budget=True, amend_check=True)
        self.assertEqual(self.node()["check"], "true")
        data = self.read()
        self.assertEqual(data["revision"], revision + 1)
        self.assertEqual(len(data["amendments"]), 1)
        self.assertIn("n1 check changed", data["amendments"][0])
        self.assertIn(("n1", "amended"), self.events())
        code, output = self.check_graph()
        self.assertEqual(code, 0, output)

    def test_a_check_edited_into_the_graph_without_an_amendment_is_an_error_for_check(self):
        self.run_node([{}], attempts=1, check="false")
        data = self.read()
        data["nodes"][1]["check"] = "true"
        self.write(data)
        code, output = self.check_graph(history=True)
        self.assertEqual(code, 1, output)
        self.assertIn("n1 check changed without an amendment", output)

    def test_a_node_never_dispatched_may_take_another_check(self):
        self.write(graph(check="false"))
        run_id, line, _ = self.run_node([{}], attempts=1, check="true")
        self.assertTrue(line.startswith("passed-check "), line)
        self.assertEqual(self.node()["check"], "true")

    def test_another_verifier_is_an_amendment_too(self):
        executor = looprun.ScriptedExecutor({"impl-openai": [{"cost": 0.1}], "reviewer": [{"final": "VERDICT: FAIL", "cost": 0.1}]})
        noderun.run(TASK, "n1", directory=self.directory, check="true", verify="reviewer", cost=5, attempts=1, wait=True, executor=executor)
        message = self.refused(check="true", verify="other-reviewer", raise_budget=True, attempts=3)
        self.assertIn("--amend-check", message)


class PinnedAndFlagged(Base):
    def edit_node(self, **fields):
        data = self.read()
        data["nodes"][1].update(fields)
        self.write(data)

    def flagged(self, script, hook, **kw):
        run_id, line, _ = self.run_node(script, hook=hook, **kw)
        text = (self.project / noderun.report_path(TASK, "n1", run_id)).read_text()
        return run_id, line, text

    def test_the_line_the_evidence_and_the_report_carry_the_command_and_its_hash(self):
        run_id, line, _ = self.run_node([{}], attempts=1, check="echo hello-from-the-check; true")
        digest = noderun._sha("echo hello-from-the-check; true")
        self.assertIn(f'check="echo hello-from-the-check; true" sha256={digest} ', line)
        self.assertIn(digest, self.node()["evidence"])
        self.assertIn('check "echo hello-from-the-check; true"', self.node()["evidence"])
        text = (self.project / noderun.report_path(TASK, "n1", run_id)).read_text()
        self.assertIn(f"(sha256 {digest})", text)

    def test_a_long_command_is_cut_short_in_the_line_but_not_the_hash(self):
        command = "true; " + "echo " + "x" * 200
        run_id, line, _ = self.run_node([{}], attempts=1, check=command)
        self.assertIn("...", line)
        self.assertNotIn("x" * 100, line)
        self.assertIn(noderun._sha(command), line)

    def test_the_check_is_pinned_at_the_start_and_a_change_in_the_graph_flags_the_pass(self):
        run_id, line, text = self.flagged([{}, {}, {}], lambda n, call: self.edit_node(check="true") if n == 1 else None)
        self.assertTrue(line.startswith("passed-check-flagged attempts=3 "), line)  # the pinned command ran every round and passed in round 3
        self.assertIn(noderun._sha(CHECK_THIRD), line)
        self.assertEqual(self.node()["status"], "returned")
        self.assertIn("FLAGGED", self.node()["evidence"])
        self.assertIn("the node's check was changed in the graph during the run", text)
        self.assertEqual(noderun.exit_code(line), 2)

    def test_a_file_the_check_names_changing_during_the_run_flags_the_pass(self):
        (self.project / "check.sh").write_text("exit 1\n")
        run_id, line, text = self.flagged([{}, {}], lambda n, call: (self.project / "check.sh").write_text("exit 0\n") if n == 2 else None,
                                          check="sh check.sh", attempts=2, scope_extra=None) if False else (None, None, None)
        executor = looprun.ScriptedExecutor([{"cost": 0.01}, {"cost": 0.01}],
                                            hook=lambda n, call: (self.project / "check.sh").write_text("exit 0\n") if n == 2 else None)
        run_id, line = noderun.run(TASK, "n1", directory=self.directory, check="sh check.sh", cost=5, attempts=2, wait=True, executor=executor)
        text = (self.project / noderun.report_path(TASK, "n1", run_id)).read_text()
        self.assertTrue(line.startswith("passed-check-flagged "), line)
        self.assertIn("a file the check command names changed during the run: check.sh", text)
        self.assertIn("check.sh", self.node()["evidence"])

    def test_the_task_graph_changing_during_the_run_flags_the_pass(self):
        def hook(n, call):
            data = self.read()
            data["nodes"][1]["done_when"] = "anything at all"
            self.write(data)
        run_id, line, text = self.flagged([{}], hook, check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check-flagged "), line)
        self.assertIn("the node's own definition", text)

    def test_the_graph_changing_in_its_run_fields_only_does_not(self):
        def hook(n, call):
            noderun._edit(self.path, "n2", {"note": "the orchestrator wrote a note"})
        run_id, line, text = self.flagged([{}], hook, check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check "), line)

    def test_another_task_graph_file_changing_does_not_flag_the_pass(self):
        other = self.directory / "other.json"
        other.write_text(json.dumps({**graph(), "task": "other"}))
        run_id, line, text = self.flagged([{}], lambda n, call: other.write_text("{}"), check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check "), line)
        self.assertIn("- .opencode/graph/other.json", text.split("Also changed in the project")[1])  # listed, not flagged

    def test_files_outside_the_scope_flag_the_pass_and_are_listed_apart(self):
        def hook(n, call):
            (self.project / "src").mkdir(exist_ok=True)
            (self.project / "src" / "a.py").write_text("x = 1\n")
            (self.project / "elsewhere.txt").write_text("not the node's\n")
        run_id, line, text = self.flagged([{}], hook, check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check-flagged "), line)
        self.assertEqual(self.node()["status"], "returned")
        self.assertIn("1 changed file lies outside the node's scope (src/a.py), in no running node's scope, not ignored by git: elsewhere.txt", text)
        inside = text.split("Inside the node's scope (the node's):")[1].split("Also changed in the project")[0]
        rest = text.split("Also changed in the project during the run (may be other work):")[1]
        self.assertIn("- src/a.py", inside)
        self.assertNotIn("elsewhere.txt", inside)
        self.assertIn("- elsewhere.txt", rest)
        self.assertNotIn("src/a.py", rest.split("## Sessions")[0])

    def test_no_more_than_twenty_files_are_named_in_a_flag(self):
        def hook(n, call):
            for index in range(25):
                (self.project / f"stray{index:02d}.txt").write_text("x")
        run_id, line, text = self.flagged([{}], hook, check="true", attempts=1)
        flag = next(l for l in text.splitlines() if "lie outside the node's scope" in l)
        self.assertIn("25 changed files lie outside", flag)
        self.assertEqual(flag.count("stray"), 20)
        self.assertIn("and 5 more", flag)

    def test_a_pass_with_nothing_unusual_is_not_flagged_and_exits_zero(self):
        run_id, line, _ = self.run_node([{}, {}, {}])
        self.assertTrue(line.startswith("passed-check "), line)
        self.assertEqual(noderun.exit_code(line), 0)

    def test_at_the_file_cap_the_list_says_it_is_incomplete_and_names_no_deletion(self):
        for index in range(6):
            (self.project / f"keep{index}.txt").write_text("x")
        self.enterContext(mock.patch.object(noderun, "FILE_LIMIT", 4))
        run_id, line, text = self.flagged([{}], lambda n, call: (self.project / "keep0.txt").unlink(), check="true", attempts=1)
        self.assertIn("this list is incomplete, and no deletion is reported", text)
        self.assertNotIn("Deleted:", text)

    def test_a_deletion_is_named_when_the_walk_was_complete(self):
        (self.project / "gone.txt").write_text("x")
        run_id, line, text = self.flagged([{}], lambda n, call: (self.project / "gone.txt").unlink(), check="true", attempts=1)
        self.assertIn("Deleted:", text)
        self.assertIn("- gone.txt", text)
        self.assertNotIn("incomplete", text)

    def test_the_snapshot_looks_into_the_graph_folder_and_nothing_else_of_opencode(self):
        (self.directory / ".history").mkdir()
        (self.directory / ".history" / "x.json").write_text("{}")
        (self.project / ".opencode" / "loops" / "r").mkdir(parents=True)
        (self.project / ".opencode" / "loops" / "r" / "run.json").write_text("{}")
        (self.project / ".git").mkdir()
        (self.project / ".git" / "HEAD").write_text("x")
        (self.project / "top.txt").write_text("x")
        found, cut = noderun._snapshot(self.project)
        self.assertEqual(sorted(found), [".opencode/graph/demo.json", "top.txt"])
        self.assertFalse(cut)


class FlagNoise(Base):
    """`passed-check-flagged` is for three things only; the ordinary traffic of a busy tree does not raise it."""

    def lane(self, status="running", scope=("lane2/",)):
        data = self.read()
        data["nodes"].append({"id": "n3", "type": "implement", "agent": "impl-openai", "goal": "The other lane", "status": status, "depends_on": [],
                              "scope": list(scope), "done_when": "done", "evidence": "", "started": 1.5, "session": "ses_other", "changed_paths": []})
        data["objectives"][0]["nodes"].append("n3")
        self.write(data)

    def flagged(self, script, hook, **kw):
        run_id, line, _ = self.run_node(script, hook=hook, **kw)
        return line, (self.project / noderun.report_path(TASK, "n1", run_id)).read_text()

    def write_files(self, *names):
        def hook(n, call):
            for name in names:
                target = self.project / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x\n")
        return hook

    def test_two_lanes_in_one_tree_the_other_lanes_files_do_not_flag_but_a_file_in_nobodys_scope_does(self):
        self.lane()
        line, text = self.flagged([{}], self.write_files("lane2/theirs.py"), check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check "), line)
        self.assertNotIn("Flagged", text)
        self.assertIn("- lane2/theirs.py", text.split("Also changed in the project")[1])  # listed, not flagged
        self.assertEqual(noderun.exit_code(line), 0)

    def test_a_file_in_nobodys_scope_flags_even_beside_the_other_lanes_files(self):
        self.lane()
        line, text = self.flagged([{}], self.write_files("lane2/theirs.py", "nobody.txt"), check="true", attempts=1, cost=5)
        self.assertTrue(line.startswith("passed-check-flagged "), line)
        flag = next(l for l in text.splitlines() if "outside the node's scope" in l)
        self.assertIn("nobody.txt", flag)
        self.assertNotIn("lane2/theirs.py", flag)
        self.assertEqual(noderun.exit_code(line), 2)

    def test_a_lane_that_was_not_running_does_not_excuse_its_files(self):
        self.lane(status="pending")
        line, text = self.flagged([{}], self.write_files("lane2/theirs.py"), check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check-flagged "), line)

    def test_a_lane_that_ran_and_finished_during_the_run_still_excuses_its_files(self):
        self.lane()

        def hook(n, call):
            data = self.read()
            data["nodes"][3]["status"] = "returned"  # the other lane ended while this run went
            data["nodes"][3]["evidence"] = "done"
            data["nodes"][3]["finished"] = time.time()
            self.write(data)
            self.write_files("lane2/theirs.py")(n, call)
        line, text = self.flagged([{}], hook, check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check "), line)

    def test_a_node_with_no_scope_is_not_compared_and_says_so_once(self):
        self.write(graph(scope=[]))
        line, text = self.flagged([{}], self.write_files("anything.txt", "more/things.txt"), check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check "), line)
        self.assertEqual(text.count("No scope set, so changed files were not compared with it."), 1)
        rest = text.split("Also changed in the project")[1]
        self.assertIn("- anything.txt", rest)
        self.assertIn("- more/things.txt", rest)

    def test_a_node_with_a_scope_does_not_get_the_empty_scope_line(self):
        line, text = self.flagged([{}], lambda n, call: None, check="true", attempts=1)
        self.assertNotIn("No scope set", text)

    def test_another_node_added_to_the_graph_during_the_run_does_not_flag(self):
        def hook(n, call):
            noderun_graph = self.read()
            noderun_graph["nodes"].append({"id": "n9", "type": "implement", "agent": "impl-openai", "goal": "Added by the orchestrator mid-run",
                                           "status": "pending", "depends_on": ["n1"], "scope": [], "done_when": "done", "evidence": "",
                                           "changed_paths": []})
            noderun_graph["objectives"][0]["nodes"].append("n9")
            self.write(noderun_graph)
        line, text = self.flagged([{}], hook, check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check "), line)

    def test_another_nodes_definition_changing_does_not_flag(self):
        def other(n, call):
            data = self.read()
            data["nodes"][2]["goal"] = "Later work, reworded"
            self.write(data)
        line, text = self.flagged([{}], other, check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check "), line)

    def test_this_nodes_own_verify_changing_flags(self):
        def mine(n, call):
            data = self.read()
            data["nodes"][1]["verify"] = "reviewer"
            self.write(data)
        line, text = self.flagged([{}], mine, check="true", attempts=1)
        self.assertTrue(line.startswith("passed-check-flagged "), line)
        self.assertIn("the node's own definition", text)

    def test_a_folder_the_check_names_is_watched_below_it(self):
        self.write(graph(scope=["tests/"]))
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_old.py").write_text("x\n")
        line, text = self.flagged([{}], self.write_files("tests/test_new.py"), check="ls tests/ && true", attempts=1)
        self.assertTrue(line.startswith("passed-check-flagged "), line)  # in the node's scope, but the check runs it: a weaker test is possible
        self.assertIn("a file the check command names changed during the run: tests/test_new.py", text)

    def test_the_watched_names_of_a_command_include_folders_with_a_trailing_slash(self):
        (self.project / "tests").mkdir()
        (self.project / "tests" / "t.py").write_text("x\n")
        (self.project / "script.sh").write_text("x\n")
        self.assertEqual(noderun._watched("pytest tests/ -q && sh script.sh; echo .", self.project), ["tests/", "script.sh"])
        self.assertEqual(noderun._touched(["tests/a/b.py", "testsx/a.py", "script.sh", "other.txt"], ["tests/", "script.sh"]), ["tests/a/b.py", "script.sh"])


def have_git():
    return shutil.which("git") is not None


@unittest.skipUnless(have_git(), "git is needed")
class FlagIgnoredFiles(Base):
    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / ".gitignore").write_text(".coverage\nbuild/\n.ruff_cache/\n")
        (self.project / "gen.sh").write_text("echo x > .coverage; mkdir -p build .ruff_cache; echo y > build/out.o; echo z > .ruff_cache/c\n")

    def test_a_check_that_writes_ignored_files_outside_the_scope_does_not_flag(self):
        run_id, line, _ = self.run_node([{}], check="sh gen.sh", attempts=1)
        self.assertTrue(line.startswith("passed-check "), line)
        text = (self.project / noderun.report_path(TASK, "n1", run_id)).read_text()
        self.assertIn("- .coverage", text.split("Also changed in the project")[1])  # still listed
        self.assertNotIn("Flagged", text)

    def test_a_file_git_does_not_ignore_flags_in_a_git_tree(self):
        def hook(n, call):
            (self.project / "stray.txt").write_text("x\n")
            (self.project / "build").mkdir(exist_ok=True)
            (self.project / "build" / "o").write_text("x\n")
        executor = looprun.ScriptedExecutor([{"cost": 0.01}], hook=hook)
        run_id, line = noderun.run(TASK, "n1", directory=self.directory, check="true", cost=5, attempts=1, wait=True, executor=executor)
        text = (self.project / noderun.report_path(TASK, "n1", run_id)).read_text()
        self.assertTrue(line.startswith("passed-check-flagged "), line)
        flag = next(l for l in text.splitlines() if "outside the node's scope" in l)
        self.assertIn("stray.txt", flag)
        self.assertNotIn("build/o", flag)

    def named_folder(self):
        """A project whose check runs the folder `tests/`: one tracked file, and git ignores caches below it."""
        (self.project / ".gitignore").write_text(".coverage\nbuild/\n.ruff_cache/\ntests/.cache/\n*.pyc\n")
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_a.py").write_text("x = 1\n")
        subprocess.run(["git", "-C", str(self.project), "add", "-f", "tests/test_a.py", ".gitignore"], check=True)
        self.write(graph(scope=["tests/"]))

    def run_in_named_folder(self, hook):
        executor = looprun.ScriptedExecutor([{"cost": 0.01}], hook=hook)
        run_id, line = noderun.run(TASK, "n1", directory=self.directory, check="ls tests/ && true", cost=5, attempts=1, wait=True, executor=executor)
        return line, (self.project / noderun.report_path(TASK, "n1", run_id)).read_text()

    def test_git_ignored_files_below_a_named_folder_do_not_flag(self):
        self.named_folder()

        def hook(n, call):
            (self.project / "tests" / ".cache").mkdir()
            (self.project / "tests" / ".cache" / "state").write_text("x\n")
            (self.project / "tests" / "mod.pyc").write_text("x\n")
        line, text = self.run_in_named_folder(hook)
        self.assertTrue(line.startswith("passed-check "), line)
        self.assertIn("- tests/mod.pyc", text.split("Inside the node's scope")[1])  # still listed

    def test_a_tracked_file_or_an_unignored_new_file_below_a_named_folder_still_flags(self):
        self.named_folder()

        def hook(n, call):
            (self.project / "tests" / "test_a.py").write_text("x = 2\n")  # tracked
            (self.project / "tests" / "test_new.py").write_text("x = 3\n")  # untracked, not ignored
            (self.project / "tests" / "mod.pyc").write_text("x\n")  # ignored
        line, text = self.run_in_named_folder(hook)
        self.assertTrue(line.startswith("passed-check-flagged "), line)
        flag = next(l for l in text.splitlines() if "a file the check command names changed" in l)
        self.assertIn("tests/test_a.py", flag)
        self.assertIn("tests/test_new.py", flag)
        self.assertNotIn("mod.pyc", flag)

    def test_an_ignored_file_that_the_check_names_itself_still_flags(self):
        (self.project / ".gitignore").write_text("ignored.cfg\n")
        (self.project / "ignored.cfg").write_text("a\n")
        executor = looprun.ScriptedExecutor([{"cost": 0.01}], hook=lambda n, call: (self.project / "ignored.cfg").write_text("b\n"))
        run_id, line = noderun.run(TASK, "n1", directory=self.directory, check="cat ignored.cfg && true", cost=5, attempts=1, wait=True, executor=executor)
        self.assertTrue(line.startswith("passed-check-flagged "), line)  # a named file, not a file found below a folder

    def test_files_in_a_skip_folder_below_a_named_folder_do_not_flag(self):
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_a.py").write_text("x\n")
        self.write(graph(scope=["tests/"]))  # so that only the named-folder rule can flag
        real = noderun._snapshot

        def snapshot(project):
            found, cut = real(project)
            if mock_calls:
                found["tests/node_modules/pkg/x.js"] = [1, 2]
                found["tests/__pycache__/a.cpython.pyc"] = [1, 2]
            mock_calls.append(1)
            return found, cut
        mock_calls: list = []
        with mock.patch.object(noderun, "_snapshot", snapshot):
            executor = looprun.ScriptedExecutor([{"cost": 0.01}])
            run_id, line = noderun.run(TASK, "n1", directory=self.directory, check="ls tests/ && true", cost=5, attempts=1, wait=True, executor=executor)
        self.assertTrue(line.startswith("passed-check "), line)

    def test_outside_a_git_tree_the_ignore_question_is_not_asked(self):
        shutil.rmtree(self.project / ".git")
        with mock.patch.object(noderun.subprocess, "run", wraps=noderun.subprocess.run) as spy:
            self.assertIsNone(noderun._ignored(self.project, ["a.txt"]))
        self.assertTrue(any("check-ignore" in call.args[0] for call in spy.call_args_list))

    def test_one_git_call_covers_the_whole_list(self):
        with mock.patch.object(noderun.subprocess, "run", wraps=noderun.subprocess.run) as spy:
            found = noderun._ignored(self.project, [".coverage", "a.txt", "build/x", "b.txt"])
        self.assertEqual(found, {".coverage", "build/x"})
        self.assertEqual(len(spy.call_args_list), 1)


class OldRunSettled(Base):
    """A node whose earlier loop run is not over and has no controller: the next `node run` ends it first and counts what it did."""

    def start_detached(self, turns):
        script = self.project / "turns.json"
        script.write_text(json.dumps(turns))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main(["run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--check", "false", "--cost", "2",
                                 "--attempts", "5", "--executor", f"scripted:{script}"])
        self.assertEqual(code, 0, out.getvalue())
        (run,) = self.runs()

        def cleanup():
            with contextlib.suppress(Exception):
                os.killpg(looprun.read_run(run).get("pid"), signal.SIGKILL)
        self.addCleanup(cleanup)
        return run

    def run_and_say(self, script, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = self.run_node(script, **kw)
        return result, out.getvalue()

    def test_graph_set_after_a_kill_then_node_run_cancels_the_old_run_and_counts_its_totals(self):
        run = self.start_detached([{"cost": 0.1}, {"hang": True}])
        self.assertTrue(self.wait_until(lambda: looprun.read_run(run).get("totals", {}).get("cost") == 0.1 and looprun.read_run(run).get("pid")))
        os.killpg(looprun.read_run(run)["pid"], signal.SIGKILL)
        self.assertTrue(self.wait_until(lambda: not looprun._held(run)))
        noderun.graphedit.edit(TASK, "n1", {"status": "failed", "evidence": "the orchestrator gave up on the stuck run"}, dirs=[self.directory])
        data = self.read()  # the follower had not yet written what the run did: the node does not know it
        data["nodes"][1].pop("spent", None)
        data["nodes"][1].pop("unpriced", None)
        self.write(data)
        (run_id, line, _), said = self.run_and_say([{}] * 5, check="false")
        self.assertEqual(looprun.read_run(run)["status"], "cancelled")  # it cannot come back with `loop resume`
        self.assertIn(f"the earlier loop run {run.name} was interrupted and is now cancelled", said)
        self.assertEqual(len(said.strip().splitlines()), 1)  # one line
        limits = looprun.read_run(looprun.run_dir(self.project, run_id))["limits"]
        self.assertAlmostEqual(limits["cost"], 2.0 - 0.1)  # B gets what is left of the node's budget, A's money counted
        self.assertEqual(limits["attempts"], 5 - looprun.read_run(run)["attempts"].__len__())
        node = self.node()
        self.assertAlmostEqual(node["spent"], 0.1 + 0.01 * limits["attempts"])
        self.assertEqual(node["attempts"], 5)
        self.assertEqual(self.check_graph(history=True)[0], 0)

    def test_a_paused_run_is_cancelled_the_same_way(self):
        thread, done = self.run_in_thread([{"sleep": 1.0}, {}], check="false", attempts=5)
        (run,) = self.runs()
        self.assertTrue(self.wait_until(lambda: looprun.read_run(run).get("status") == "running"))
        looprun.pause(run)
        thread.join(30)
        self.assertEqual(looprun.read_run(run)["status"], "paused")
        noderun.graphedit.edit(TASK, "n1", {"status": "failed", "evidence": "paused for too long"}, dirs=[self.directory])
        (run_id, line, _), said = self.run_and_say([{}] * 5, check="false")
        self.assertEqual(looprun.read_run(run)["status"], "cancelled")
        self.assertIn(f"the earlier loop run {run.name} was paused and is now cancelled", said)
        self.assertNotEqual(run_id, run.name)

    def test_an_old_run_that_ended_by_itself_says_nothing(self):
        self.run_node([{}], check="true", attempts=3, cost=5.0)
        (run_id, line, _), said = self.run_and_say([{}], check="true")
        self.assertEqual(said, "")

    def test_the_refusal_for_a_node_that_is_running_names_cancel_and_wait(self):
        thread, done = self.run_in_thread([{"hang": True}])
        message = self.refused(check="true", cost=1)
        self.assertIn(f"agentgraph node cancel --task {TASK} --node n1", message)
        self.assertIn(f"agentgraph node wait --task {TASK} --node n1", message)
        noderun.cancel(TASK, "n1", self.directory)
        thread.join(20)


class UnpricedTurns(Base):
    def first_run_unpriced(self):
        executor = looprun.ScriptedExecutor([{}])  # a turn that has no price
        noderun.run(TASK, "n1", directory=self.directory, check="true", cost=5.0, attempts=3, wait=True, executor=executor)
        node = self.node()
        self.assertNotIn("spent", node)
        self.assertEqual(node["unpriced"], 1)

    def test_a_run_where_every_turn_is_unpriced_blocks_a_rerun_until_it_is_accepted(self):
        self.first_run_unpriced()
        message = self.refused(check="true")
        self.assertIn("had 1 turn with no known cost", message)
        self.assertIn("--accept-unpriced", message)
        self.assertEqual(len(self.runs()), 1)  # the refused run left nothing behind
        run_id, line, _ = self.run_node([{}], check="true", accept_unpriced=True)
        self.assertTrue(line.startswith("passed-check "), line)
        note = next(e["note"] for e in self.read()["events"] if e["event"] == "amended")
        self.assertIn("continued with 1 turn of no known cost (--accept-unpriced)", note)

    def test_raise_budget_also_lets_an_unpriced_node_run_again(self):
        self.first_run_unpriced()
        run_id, line, _ = self.run_node([{}], check="true", raise_budget=True, cost=9.0, attempts=3)
        self.assertTrue(line.startswith("passed-check "), line)
        self.assertEqual(self.node()["budget"]["cost"], 9.0)

    def test_the_flag_is_on_the_command_line(self):
        self.first_run_unpriced()
        script = self.project / "turns.json"
        script.write_text(json.dumps([{"cost": 0.01}]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            refused = noderun.main(["run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--check", "true", "--wait",
                                    "--executor", f"scripted:{script}"])
            code = noderun.main(["run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--check", "true", "--wait", "--accept-unpriced",
                                 "--executor", f"scripted:{script}"])
        self.assertEqual(refused, 1, out.getvalue())
        self.assertEqual(code, 0, out.getvalue())


class RaiseBelowWhatIsSpent(Base):
    def test_raise_budget_refuses_a_cost_below_what_is_spent_and_attempts_below_what_is_used(self):
        self.run_node([{"cost": 0.4}] * 2, cost=1.0, attempts=2, check="false")
        self.assertAlmostEqual(self.node()["spent"], 0.8)
        message = self.refused(check="false", raise_budget=True, cost=0.5, attempts=9)
        self.assertIn("already spent $0.8000", message)
        self.assertIn("below what is spent", message)
        message = self.refused(check="false", raise_budget=True, cost=9.0, attempts=1)
        self.assertIn("already used 2 attempts", message)
        self.assertEqual(self.node()["budget"], {"cost": 1.0, "attempts": 2})  # nothing was changed
        self.assertNotIn(("n1", "amended"), self.events())
        run_id, line, _ = self.run_node([{}], check="false", raise_budget=True, cost=9.0, attempts=3)  # above both: allowed
        self.assertEqual(self.node()["budget"], {"cost": 9.0, "attempts": 3})


def agent_script(project, name):
    script = project.parent / f"{project.name}-{name}"
    script.write_text("#!/bin/sh\nsleep 300\n")
    script.chmod(0o755)
    return script


class LeftoverKillIsNarrow(Base):
    """`_kill_leftovers` stops a process only when it leads its own process group and nothing is its parent any more."""

    def setUp(self):
        super().setUp()
        self.session = f"ses_spare_{os.getpid()}_{time.monotonic_ns()}"
        self.script = agent_script(self.project, "agent")
        self.procs = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for proc in self.procs:
            subprocess.run(["pkill", "-KILL", "-P", str(proc.pid)], check=False)  # the script's `sleep`, whichever group it is in
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL) if os.getpgid(proc.pid) == proc.pid else proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(5)
        for pid in noderun._leftovers([self.session]):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        self.script.unlink(missing_ok=True)

    def start(self, **kw):
        proc = subprocess.Popen([str(self.script), "run", "--session", self.session], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, **kw)
        self.procs.append(proc)
        self.assertTrue(self.wait_until(lambda: proc.pid in noderun._leftovers([self.session]), 10))
        return proc

    def test_a_process_that_is_not_a_group_leader_is_spared(self):
        proc = self.start()  # in this test's own process group, with a parent that is alive
        self.assertNotEqual(os.getpgid(proc.pid), proc.pid)
        stopped, spared = noderun._kill_leftovers([self.session])
        self.assertEqual((stopped, spared), (0, [proc.pid]))
        self.assertIsNone(proc.poll())

    def test_a_group_leader_whose_parent_is_alive_is_spared(self):
        proc = self.start(start_new_session=True)
        self.assertEqual(os.getpgid(proc.pid), proc.pid)
        stopped, spared = noderun._kill_leftovers([self.session])
        self.assertEqual((stopped, spared), (0, [proc.pid]))
        self.assertIsNone(proc.poll())

    def test_an_orphaned_group_leader_is_stopped_with_its_group(self):
        maker = ("import os,sys\n"
                 "if os.fork() == 0:\n"
                 "    os.setsid()\n"
                 f"    os.execv({str(self.script)!r}, [{str(self.script)!r}, 'run', '--session', {self.session!r}])\n")
        subprocess.run([sys.executable, "-c", maker], check=True)
        self.assertTrue(self.wait_until(lambda: noderun._leftovers([self.session]), 10))
        (pid,) = noderun._leftovers([self.session])
        if not self.wait_until(lambda: noderun._processes([self.session])[0][1] == 1, 5):
            self.skipTest("this system does not hand orphans to pid 1")
        stopped, spared = noderun._kill_leftovers([self.session])
        self.assertEqual((stopped, spared), (1, []))
        self.assertTrue(self.wait_until(lambda: not noderun._leftovers([self.session]), 10))

    def test_cancel_says_which_process_it_left_alone(self):
        proc = self.start(start_new_session=True)
        folder = looprun.loops_folder(self.project) / "20261121-000000-abcd"
        folder.mkdir(parents=True)
        looprun._write_atomic(folder / "run.json", looprun._json({"status": "queued", "created": time.time() - 600, "run": folder.name,
                                                                   "sessions": [self.session]}))
        self.write(graph(status="blocked", loop_run=folder.name, attempts=1, check="true"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            noderun.run(TASK, "n1", directory=self.directory, check="true", cost=5, wait=True, executor=looprun.ScriptedExecutor([{"cost": 0.01}]))
        self.assertIn(f"NOT stopped (not an orphaned group leader): pid {proc.pid}", out.getvalue())
        self.assertIsNone(proc.poll())


class Fields(Base):
    """A node field of the wrong type is a plain refusal, never a crash."""

    def test_a_verify_that_is_not_text(self):
        self.write(graph(verify=5))
        self.assertIn("verify must be text", self.refused(check="true", cost=1))

    def test_other_fields_of_the_wrong_type(self):
        for fields, said in (({"check": ["a"]}, "check must be text"), ({"scope": "src"}, "scope must be a list of text"),
                             ({"depends_on": "n0"}, "depends_on must be a list of text"), ({"attempts": "3"}, "attempts must be a whole number"),
                             ({"spent": "lots"}, "spent must be dollars"), ({"budget": "big"}, "budget must be an object"),
                             ({"budget": {"cost": "a lot"}}, "budget cost must be a number above 0"),
                             ({"budget": {"attempts": 1.5}}, "budget attempts must be a whole number"), ({"loop_run": 7}, "loop_run must be text"),
                             ({"agent": 3}, "agent must be text"), ({"goal": {}}, "goal must be text")):
            with self.subTest(fields=fields):
                self.write(graph(**fields))
                self.assertIn(said, self.refused(check="true", cost=1))
        self.assertEqual(self.runs(), [])

    def test_the_command_line_says_it_in_one_line(self):
        self.write(graph(verify=5))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main(["run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--check", "true", "--cost", "1"])
        self.assertEqual(code, 1)
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertNotIn("Traceback", out.getvalue())


class SecondController(Base):
    def test_an_outside_graph_set_cancels_the_run_and_a_new_run_waits_until_it_is_over(self):
        thread, done = self.run_in_thread([{"hang": True}])
        self.assertTrue(self.wait_until(lambda: self.node().get("loop_run")))
        (run,) = self.runs()
        graphedit = noderun.graphedit
        graphedit.edit(TASK, "n1", {"status": "blocked", "note": "the orchestrator took the node"}, dirs=[self.directory])
        message = self.refused(check="true", cost=1)  # the node says blocked, but the run is still going
        self.assertIn("is still going", message)
        self.assertIn(run.name, message)
        self.assertEqual(self.runs(), [run])
        self.assertTrue(self.wait_until(lambda: looprun.read_run(run)["status"] in looprun.FINAL), "the run was not cancelled")
        self.assertEqual(looprun.read_run(run)["status"], "cancelled")
        thread.join(20)
        self.assertEqual(self.node()["status"], "blocked")  # the node is still what the orchestrator made it
        text = (self.project / noderun.report_path(TASK, "n1", run.name)).read_text()
        self.assertIn("no longer belongs to this run", text)
        self.assertIn("asked to stop", text)
        run_id, line, _ = self.run_node([{}, {}, {}])  # and now it is final: a new run is allowed
        self.assertEqual(self.node()["status"], "returned")

    def test_a_queued_run_that_a_controller_may_still_take_blocks_a_new_one(self):
        folder = looprun.loops_folder(self.project) / "20261121-000000-abcd"
        folder.mkdir(parents=True)
        looprun._write_atomic(folder / "run.json", looprun._json({"status": "queued", "created": time.time(), "run": folder.name}))
        self.write(graph(status="blocked", loop_run=folder.name, attempts=1, check="true"))
        self.assertIn("is still going (queued)", self.refused(check="true", cost=1))
        state = looprun.read_run(folder)
        state["created"] = time.time() - 600  # nobody took it within the grace time
        looprun._write_atomic(folder / "run.json", looprun._json(state))
        run_id, line, _ = self.run_node([{}], check="true", attempts=3)
        self.assertTrue(line.startswith("passed-check"), line)

    def test_the_first_look_at_a_run_does_not_touch_a_node_that_already_says_what_the_run_says(self):
        thread, done = self.run_in_thread([{"hang": True}], check="false", attempts=5)
        self.assertTrue(self.wait_until(lambda: self.node().get("attempts") == 1))  # the node has caught up with the run
        time.sleep(0.4)
        updated = self.read()["updated"]
        time.sleep(0.6)  # several looks at a run that has said nothing new
        self.assertEqual(self.read()["updated"], updated)
        stop = threading.Event()  # and a new follower, whose first look it is
        follower = threading.Thread(target=noderun._follow, args=(self.runs()[0], self.path, "n1", stop), daemon=True)
        follower.start()
        time.sleep(0.5)
        stop.set()
        follower.join(5)
        self.assertEqual(self.read()["updated"], updated)
        noderun.cancel(TASK, "n1", self.directory)
        thread.join(20)

    def test_following_a_run_does_not_touch_a_node_that_already_says_the_same(self):
        thread, done = self.run_in_thread([{"cost": 0.1}, {"hang": True}], check="false", attempts=5)
        self.assertTrue(self.wait_until(lambda: self.node().get("attempts") == 2 and self.node().get("spent")))
        updated = self.read()["updated"]
        time.sleep(0.6)  # several looks at the run, with nothing new in it
        self.assertEqual(self.read()["updated"], updated)
        noderun.cancel(TASK, "n1", self.directory)
        thread.join(20)


class Endings(Base):
    def test_paused_leaves_the_node_running_and_check_says_what_to_do(self):
        thread, done = self.run_in_thread([{"sleep": 1.0}, {}], check="false", attempts=5)
        (run,) = self.runs()
        self.assertTrue(self.wait_until(lambda: looprun.read_run(run).get("status") == "running"))
        looprun.pause(run)
        thread.join(30)
        self.assertEqual(looprun.read_run(run)["status"], "paused")
        line = done["result"][1]
        self.assertTrue(line.startswith("blocked "), line)  # not over, and nobody is working on it
        self.assertEqual(self.node()["status"], "running")
        code, output = self.check_graph()
        self.assertEqual(code, 0, output)
        self.assertIn(f"agentgraph node cancel --task {TASK} --node n1", output)
        self.assertIn("prefer it to loop resume", output)
        self.assertEqual(noderun.exit_code(line), 2)
        noderun.cancel(TASK, "n1", self.directory)
        self.assertEqual(self.node()["status"], "blocked")

    def test_a_check_that_cannot_decide_blocks_the_node(self):
        run_id, line, _ = self.run_node([{}], check="sleep 5", check_timeout=0.3, attempts=1)
        self.assertEqual(looprun.read_run(looprun.run_dir(self.project, run_id))["status"], "inconclusive")
        self.assertTrue(line.startswith("blocked "), line)
        node = self.node()
        self.assertEqual(node["status"], "blocked")
        self.assertIn("blocked after 1 round", node["evidence"])
        self.assertNotIn("failure_kind", node)
        self.assertEqual(self.check_graph()[0], 0)

    def test_a_check_that_cannot_run_is_an_environment_failure(self):
        run_id, line, _ = self.run_node([{}], check="exit 127", attempts=1)
        self.assertEqual(looprun.read_run(looprun.run_dir(self.project, run_id))["status"], "check_error")
        node = self.node()
        self.assertEqual((node["status"], node["failure_kind"]), ("blocked", "environment"))
        self.assertTrue(line.startswith("blocked "), line)
        self.assertEqual(self.check_graph()[0], 0)

    def test_the_result_is_never_taken_back_to_fewer_attempts_than_the_graph_has(self):
        def hook(n, call):
            noderun.graphedit.edit(TASK, "n1", {"attempts": "9"}, dirs=[self.directory])
        run_id, line, _ = self.run_node([{}], hook=hook, check="true", attempts=3)
        node = self.node()
        self.assertEqual((node["status"], node["attempts"]), ("returned", 9))

    def test_a_result_the_graph_refuses_blocks_the_node_with_the_refusal_as_the_reason(self):
        real = noderun._edit
        calls = []

        def refuse_returned(path, node_id, change, *args, **kw):
            calls.append(change.get("status"))
            if change.get("status") == "returned":
                raise noderun.graphedit.Refused("this write would add a problem to demo.json: invented for the test")
            return real(path, node_id, change, *args, **kw)
        with mock.patch.object(noderun, "_edit", refuse_returned):
            run_id, line, _ = self.run_node([{}], check="true", attempts=3)
        node = self.node()
        self.assertEqual(node["status"], "blocked")  # not left running
        self.assertIn("the graph refused the node's result: this write would add a problem to demo.json: invented for the test", node["evidence"])
        self.assertEqual(node["failure_kind"], "environment")
        self.assertEqual(self.check_graph()[0], 0)


class ExitCodes(Base):
    def main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main([*argv])
        return code, out.getvalue()

    def run_main(self, turns, *extra):
        script = self.project / "turns.json"
        script.write_text(json.dumps(turns))
        return self.main("run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--wait", "--cost", "1", "--executor", f"scripted:{script}", *extra)

    def test_the_words_and_their_codes(self):
        for line, code in (("passed-check attempts=1", 0), ("failed attempts=1", 1), ("stalled attempts=1", 1), ("limit attempts=1", 1),
                           ("blocked attempts=1", 2), ("passed-check-flagged attempts=1", 2), ("running attempts=1", 3)):
            self.assertEqual(noderun.exit_code(line), code, line)

    def test_run_wait_and_status_and_wait_exit_by_the_word(self):
        code, out = self.run_main([{"cost": 0.1}], "--check", "false", "--attempts", "1")
        self.assertEqual((code, out.split()[0]), (1, "failed"))
        self.assertEqual(self.main("status", "--task", TASK, "--node", "n1", "--dir", str(self.directory))[0], 1)
        self.assertEqual(self.main("wait", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--timeout", "5")[0], 1)

    def test_blocked_exits_two_and_passed_zero(self):
        code, out = self.run_main([{"error": "provider down"}], "--check", "false", "--attempts", "1")
        self.assertEqual((code, out.split()[0]), (2, "blocked"))
        self.assertEqual(self.main("status", "--task", TASK, "--node", "n1", "--dir", str(self.directory))[0], 2)

    def test_a_flagged_pass_exits_two_by_status(self):
        self.run_node([{}], hook=lambda n, call: (self.project / "stray.txt").write_text("x"), check="true", attempts=1)
        code, out = self.main("status", "--task", TASK, "--node", "n1", "--dir", str(self.directory))
        self.assertEqual((code, out.split()[0]), (2, "passed-check-flagged"))

    def test_a_wait_that_times_out_exits_three_and_a_running_status_too(self):
        thread, done = self.run_in_thread([{"hang": True}])
        self.assertTrue(self.wait_until(lambda: self.node().get("loop_run")))
        code, out = self.main("wait", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--timeout", "0.3")
        self.assertEqual(code, 3)
        self.assertIn("still running after 0.3 seconds", out)
        code, out = self.main("status", "--task", TASK, "--node", "n1", "--dir", str(self.directory))
        self.assertEqual((code, out.split()[0]), (3, "running"))
        noderun.cancel(TASK, "n1", self.directory)
        thread.join(20)


class CancelLeavesNothingRunning(Base):
    """A controller that was killed leaves the `opencode run --session` it started (its own process group): cancel stops it."""

    def fake_opencode(self):
        script = self.project.parent / f"{self.project.name}-opencode"
        script.write_text("#!/bin/sh\nsleep 300\n")
        script.chmod(0o755)
        self.addCleanup(script.unlink, True) if False else self.addCleanup(lambda: script.unlink(missing_ok=True))
        return script

    def test_cancel_after_the_controller_was_killed_stops_the_agent_process(self):
        script = self.fake_opencode()
        db = self.project / "none.db"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = noderun.main(["run", "--task", TASK, "--node", "n1", "--dir", str(self.directory), "--check", "false", "--cost", "1",
                                 "--executable", str(script), "--db", str(db)])
        self.assertEqual(code, 0, out.getvalue())
        (run,) = self.runs()
        self.addCleanup(lambda: subprocess_kill(run))
        self.assertTrue(self.wait_until(lambda: looprun.read_run(run).get("status") == "running" and looprun.read_run(run).get("pid")))
        self.assertTrue(self.wait_until(lambda: noderun._leftovers(looprun.read_run(run).get("sessions") or [])), "the agent process never started")
        sessions = looprun.read_run(run)["sessions"]
        os.killpg(looprun.read_run(run)["pid"], signal.SIGKILL)  # the controller and its group: the agent has its own group and stays
        self.assertTrue(self.wait_until(lambda: not looprun._held(run)))
        self.assertTrue(noderun._leftovers(sessions), "the agent process died with its controller: nothing to test")
        line = noderun.cancel(TASK, "n1", self.directory)
        self.assertTrue(line.startswith("blocked "), line)
        self.assertTrue(self.wait_until(lambda: not noderun._leftovers(sessions), 10), "the agent process is still running after node cancel")


def subprocess_kill(run):
    with contextlib.suppress(Exception):
        for pid in noderun._leftovers(looprun.read_run(run).get("sessions") or []):
            os.kill(pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
