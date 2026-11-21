"""Recovery, pause and cost (release 3 of agentgraph.looprun): tests R1 to R9 of `design2.md` on the scripted executor, with real check
scripts in temp folders, one test that SIGKILLs a real detached controller process, and the history / trial / CLI tests. A controller
that dies is simulated by raising a `BaseException` inside the executor (or the check, or between two steps): the record is then left
as a SIGKILL would leave it. The scripted executor's `reconcile` reads the answer from a file the tests write. Every test that sends
something counts the executor's calls. No test starts OpenCode; a real compaction is not provoked."""
import contextlib
import fcntl
import hashlib
import io
import json
import os
import signal
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from agentgraph import looprun

CHECK_SECOND = 'n=$(cat .count 2>/dev/null || echo 0); n=$((n+1)); echo $n > .count; echo "check call $n"; [ "$n" -ge 2 ]'
PASS = "Looks right to me.\nVERDICT: PASS"


class Kill(BaseException):
    """A controller that dies on the spot: not an Exception, so nothing in the controller turns it into a record."""


def kill_at(n):
    def hook(count, call):
        if count == n:
            raise Kill()
    return hook


def tree_hash(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.structure = self.tmp / "structure"
        (self.structure / "30-build").mkdir(parents=True)
        for name in ("worker", "reviewer"):
            (self.structure / "30-build" / f"{name}.md").write_text(f"{name}.\n")
        (self.structure / "flows").mkdir()
        (self.structure / "flows" / "self.md").write_text("path: worker -> worker\n\nSelf.\n")
        (self.structure / "flows" / "duo.md").write_text("path: worker -> reviewer -> worker\n\nOne judge.\n")
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.reconcile = self.tmp / "reconcile.json"

    def settings(self, flow="self", check=CHECK_SECOND, attempts=5, **extra) -> dict:
        limits = {"attempts": attempts, **{k: extra.pop(k) for k in ("cost", "seconds", "attempt_seconds") if k in extra}}
        out = {"version": 1, "flow": flow, "limits": limits, **extra}
        if check is not None:
            out["check"] = {"command": check, "timeout": 20}
        return out

    def save(self, flow="self", **kw) -> dict:
        settings = self.settings(flow, **kw)
        looprun.save_settings(self.structure, flow, settings)
        return settings

    def executor(self, script=None, hook=None, answer=None, **extra):
        if answer is not None:
            self.reconcile.write_text(json.dumps({"answer": answer, **extra}))
        return looprun.ScriptedExecutor(script or [{}] * 10, hook=hook, reconcile=self.reconcile)

    def start(self, executor, flow="self", goal="Make the check pass.", **kw):
        return looprun.start(self.project, flow, structure=self.structure, goal=goal, executor=executor, wait=True, **kw)

    def crash(self, executor, flow="self", **kw) -> str:
        """Run until the executor (or a patched step) raises Kill; the id of the run that was left behind."""
        before = set(looprun.list_runs(self.project))
        with self.assertRaises(Kill):
            self.start(executor, flow, **kw)
        run, = set(looprun.list_runs(self.project)) - before
        return run

    def folder(self, run) -> Path:
        return looprun.run_dir(self.project, run)

    def record(self, run) -> dict:
        return looprun.read_run(self.folder(run))

    def shown(self, run) -> str:
        return looprun.shown_status(self.folder(run), self.record(run))

    def resume(self, run, executor, choice=None) -> str:
        executor.hook = None
        looprun.resume(self.folder(run), choice, wait=True, executor=executor)
        return self.record(run)["status"]

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = looprun.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def steps(self, run) -> list[tuple]:
        return [(a["n"], e["step"], e["outcome"], bool(e.get("recovered"))) for a in self.record(run)["attempts"] for e in a["steps"]]


class RecoveryTests(Base):
    def test_r1_a_controller_killed_between_steps_reruns_nothing_that_finished(self):
        self.save()
        executor = self.executor()
        original = looprun.Controller._begin

        def begin(controller):
            if len(controller.state["attempts"]) == 1:
                raise Kill()
            return original(controller)
        with mock.patch.object(looprun.Controller, "_begin", begin):
            run = self.crash(executor)
        record = self.record(run)
        self.assertEqual((record["status"], self.shown(run)), ("running", "interrupted"))
        self.assertEqual(record["cursor"]["phase"], "done")
        self.assertEqual((record["cursor"]["step"], record["cursor"]["next"], record["cursor"]["attempt"]), ("check", "work", 1))
        first = json.dumps([record["attempts"][0]["steps"], record["attempts"][0]["check"]], sort_keys=True)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(self.resume(run, executor), "passed")
        after = self.record(run)
        self.assertEqual(len(executor.calls), 2)  # the finished turn of attempt 1 was not sent again; only attempt 2's was
        self.assertEqual(json.dumps([after["attempts"][0]["steps"], after["attempts"][0]["check"]], sort_keys=True), first)  # the finished attempt's steps and check are as they were
        self.assertEqual([a["check"]["exit"] for a in after["attempts"]], [1, 0])
        self.assertEqual(executor.calls[0]["session"], executor.calls[1]["session"])
        self.assertEqual((self.folder(run) / ".count").exists() or (self.project / ".count").read_text().strip(), "2")  # the check ran once per attempt

    def test_r1_a_check_that_was_running_runs_again_and_nothing_before_it_does(self):
        self.save()
        executor = self.executor()
        real, calls = looprun.run_check, []

        def check(*args, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise Kill()
            return real(*args, **kw)
        with mock.patch.object(looprun, "run_check", check):
            run = self.crash(executor)
        self.assertEqual((self.record(run)["cursor"]["phase"], self.record(run)["cursor"]["step"]), ("started", "check"))
        self.assertEqual(self.resume(run, executor), "passed")
        self.assertEqual(len(executor.calls), 2)  # the work turn before the killed check was not sent again; the check ran again
        self.assertEqual([a["check"]["exit"] for a in self.record(run)["attempts"]], [0 if False else 1, 0])
        self.assertEqual([c["first"] for c in executor.calls], [True, False])

    def test_r1_a_judge_step_killed_in_flight_resends_only_the_judge_and_the_worker_stays_done(self):
        self.save("duo", check="exit 0")
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}] * 2}, hook=kill_at(2), answer="not_sent")  # the killed call used the first item
        run = self.crash(executor, "duo")
        self.assertEqual([c["agent"] for c in executor.calls], ["worker", "reviewer"])
        self.assertEqual(self.resume(run, executor), "passed")
        self.assertEqual([c["agent"] for c in executor.calls], ["worker", "reviewer", "reviewer"])
        self.assertEqual(executor.calls[1]["message"], executor.calls[2]["message"])
        self.assertEqual(self.record(run)["passed_by"], "check+judges")

    def test_r2_not_sent_sends_exactly_once(self):
        self.save()
        executor = self.executor(hook=kill_at(2), answer="not_sent")
        run = self.crash(executor)
        sha = self.record(run)["cursor"]["message_sha256"]
        self.assertEqual(sha, hashlib.sha256(executor.calls[1]["message"].encode()).hexdigest())
        self.assertEqual(self.resume(run, executor), "passed")
        self.assertEqual(len(executor.calls), 3)  # one from before the kill (never reached the agent), one after
        self.assertEqual(executor.reconciles, [(executor.calls[1]["session"], sha)])
        self.assertEqual(executor.calls[2]["message"], executor.calls[1]["message"])
        self.assertEqual(executor.calls[2]["session"], executor.calls[1]["session"])
        self.assertEqual(self.steps(run), [(1, "work", "returned", False), (1, "check", "fail", False), (2, "work", "returned", False), (2, "check", "pass", False)])

    def test_r3_finished_adopts_the_reply_and_sends_nothing(self):
        self.save()
        executor = self.executor(hook=kill_at(2), answer="finished", text="I changed the file.", cost=0.03, sessions=1)
        run = self.crash(executor)
        self.assertEqual(self.resume(run, executor), "passed")
        self.assertEqual(len(executor.calls), 2)  # nothing was sent after the kill
        record = self.record(run)
        entry = record["attempts"][1]["steps"][0]
        self.assertEqual((entry["outcome"], entry["recovered"], entry["cost"]), ("returned", True, 0.03))
        self.assertEqual((self.folder(run) / entry["reply"]).read_text(), "I changed the file.")
        self.assertEqual(record["attempts"][1]["agent"]["reply"], "I changed the file.")
        self.assertEqual(record["recoveries"][0]["answer"], "finished")
        self.assertEqual(record["recoveries"][0]["action"], "adopt")
        self.assertAlmostEqual(record["totals"]["cost"], 0.03)
        self.assertEqual(record["totals"]["cost_unknown_steps"], 1)  # the first, never priced turn

    def test_r4_unknown_pauses_with_the_reason_and_sends_nothing_and_redo_then_sends_once(self):
        self.save()
        executor = self.executor(hook=kill_at(2), answer="unknown", reason="the session is still busy")
        run = self.crash(executor)
        self.assertEqual(self.resume(run, executor), "paused")
        record = self.record(run)
        self.assertIn("still busy", record["reason"])
        self.assertEqual((self.shown(run), len(executor.calls)), ("paused", 2))
        self.assertEqual(self.resume(run, executor), "paused")  # asking again changes nothing while it is still unknown
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(self.resume(run, executor, "redo"), "passed")
        self.assertEqual(len(executor.calls), 3)  # exactly one send, and only because the person said so
        self.assertEqual(self.record(run)["recoveries"][-1]["action"], "redo")

    def test_r4_without_a_reconcile_answer_the_step_is_unknown(self):
        self.save()
        executor = self.executor(hook=kill_at(2))  # the file was never written
        run = self.crash(executor)
        self.assertEqual(self.resume(run, executor), "paused")
        self.assertEqual(len(executor.calls), 2)

    def test_r4_accept_takes_the_step_as_returned_and_goes_to_the_check(self):
        self.save()
        executor = self.executor(hook=kill_at(2), answer="unknown", reason="cannot tell")
        run = self.crash(executor)
        self.assertEqual(self.resume(run, executor), "paused")
        self.assertEqual(self.resume(run, executor, "accept"), "passed")
        self.assertEqual(len(executor.calls), 2)  # nothing sent
        entry = self.record(run)["attempts"][1]["steps"][0]
        self.assertEqual((entry["outcome"], entry["recovered"], entry.get("accepted")), ("returned", True, True))
        self.assertIsNone(entry["cost"])

    def test_the_choices_only_apply_to_a_step_that_may_have_run(self):
        self.save()
        executor = self.executor()
        original = looprun.Controller._begin

        def begin(controller):
            if len(controller.state["attempts"]) == 1:
                raise Kill()
            return original(controller)
        with mock.patch.object(looprun.Controller, "_begin", begin):
            run = self.crash(executor)
        before = tree_hash(self.folder(run))
        with self.assertRaises(looprun.LoopError):
            looprun.resume(self.folder(run), "redo", wait=True, executor=executor)
        with self.assertRaises(looprun.LoopError):
            looprun.resume(self.folder(run), "sideways", wait=True, executor=executor)
        self.assertEqual(self.shown(run), "interrupted")  # refused: nothing changed
        self.assertEqual({k: v for k, v in tree_hash(self.folder(run)).items() if k != "lock"}, {k: v for k, v in before.items() if k != "lock"})

    def test_resume_refuses_a_final_run_a_queued_run_and_a_run_whose_controller_is_alive(self):
        self.save()
        executor = self.executor()
        done = self.start(executor)
        with self.assertRaises(looprun.Busy) as raised:
            looprun.resume(self.folder(done), wait=True, executor=executor)
        self.assertIn("passed", str(raised.exception))
        queued = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=self.executor())
        with self.assertRaises(looprun.Busy):
            looprun.resume(self.folder(queued), wait=True, executor=executor)
        run = self.crash(self.executor(hook=kill_at(1)))
        lock = open(self.folder(run) / "lock", "a+")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            self.assertEqual(self.shown(run), "running")
            with self.assertRaises(looprun.Busy) as raised:
                looprun.resume(self.folder(run), wait=True, executor=executor)
            self.assertIn("alive", str(raised.exception))
            self.assertEqual(self.cli("resume", run, "--project", str(self.project))[0], 1)
        finally:
            lock.close()
        self.assertEqual(self.shown(run), "interrupted")

    def test_a_resumed_run_uses_the_pinned_definition_not_the_saved_loop(self):
        self.save(attempts=5)

        def edit(count, call):
            if count == 1:
                looprun.save_settings(self.structure, "self", self.settings(check="exit 0", attempts=1))  # an edit while the run is in progress
            if count == 2:
                looprun.remove_settings(self.structure, "self")
                raise Kill()
        executor = self.executor(hook=edit, answer="not_sent")
        run = self.crash(executor)
        pinned = (self.folder(run) / "definition.json").read_bytes()
        self.assertEqual(self.record(run)["attempts"][0]["check"]["exit"], 1)  # the check in effect was the pinned one, not the edit
        self.assertEqual(self.resume(run, executor), "passed")
        self.assertEqual((self.folder(run) / "definition.json").read_bytes(), pinned)
        record = self.record(run)
        self.assertEqual((len(record["attempts"]), record["limits"]["attempts"]), (2, 5))
        self.assertEqual([a["check"]["exit"] for a in record["attempts"]], [1, 0])
        self.assertIsNone(looprun.load_settings(self.structure, "self"))

    def test_r8_no_agent_turn_starts_past_a_limit_after_a_resume(self):
        self.save(check="exit 1", cost=1.0)
        executor = self.executor([{"cost": 1.2}] * 5)
        original = looprun.Controller._record

        def record(controller, step, outcome):
            route = original(controller, step, outcome)
            if step["id"] == "check":
                raise Kill()
            return route
        with mock.patch.object(looprun.Controller, "_record", record):
            run = self.crash(executor)
        self.assertEqual(self.record(run)["cursor"]["next"], "work")
        self.assertEqual(self.resume(run, executor), "limit")
        self.assertEqual(len(executor.calls), 1)
        self.assertIn("cost limit", self.record(run)["reason"])

    def test_a_resumed_run_counts_its_attempts_across_the_restart(self):
        self.save(check="exit 1", attempts=3)
        executor = self.executor(hook=kill_at(2), answer="not_sent")
        run = self.crash(executor)
        self.assertEqual(self.resume(run, executor), "failed")
        self.assertEqual((len(self.record(run)["attempts"]), len(executor.calls)), (3, 4))  # 3 attempts; call 2 was sent again after the kill

    def test_every_message_carries_the_goal_the_criteria_and_the_last_result_so_nothing_depends_on_the_session(self):
        self.save(check="echo wrong header; exit 1", attempts=3)
        executor = self.executor(hook=kill_at(2), answer="not_sent")
        run = self.crash(executor, goal="Write the report with a header.")
        self.resume(run, executor)
        for call in executor.calls[1:]:
            for needle in ("Write the report with a header.", "echo wrong header; exit 1", "exit code 1", "wrong header"):
                self.assertIn(needle, call["message"])
        self.assertIn("Write the report with a header.", executor.calls[0]["message"])

    def test_a_real_controller_process_killed_with_sigkill_mid_step_is_resumed(self):
        self.save()
        self.reconcile.write_text(json.dumps({"answer": "not_sent"}))
        spec = {"kind": "scripted", "attempts": [{}, {"hang": True}], "reconcile": str(self.reconcile)}
        run = looprun.start(self.project, "self", structure=self.structure, goal="Make it pass.", executor_spec=spec)
        folder = self.folder(run)
        end = time.time() + 30
        while time.time() < end:
            try:
                cursor = looprun.read_run(folder).get("cursor") or {}
            except looprun.LoopError:
                cursor = {}
            if (cursor.get("attempt"), cursor.get("step"), cursor.get("phase")) == (2, "work", "started"):
                break
            time.sleep(0.05)
        else:
            self.fail(f"the controller never reached the second turn: {looprun.read_run(folder)}")
        pid = looprun.read_run(folder)["pid"]
        os.kill(pid, signal.SIGKILL)
        end = time.time() + 10
        while time.time() < end and looprun._held(folder):
            time.sleep(0.05)
        self.assertEqual(self.shown(run), "interrupted")
        self.assertIn("interrupted", self.cli("status", run, "--project", str(self.project))[1])
        self.assertEqual(self.cli("resume", run, "--project", str(self.project))[0], 0)
        end = time.time() + 30
        while time.time() < end and self.record(run)["status"] not in looprun.FINAL:
            time.sleep(0.05)
        record = self.record(run)
        self.assertEqual(record["status"], "passed", record)
        self.assertEqual(len(record["attempts"]), 2)
        self.assertEqual(record["recoveries"][0]["answer"], "not_sent")
        self.assertEqual([a["check"]["exit"] for a in record["attempts"]], [1, 0])


class PauseCancelAnswerTests(Base):
    def test_r5_pause_lets_the_running_step_finish_and_resume_goes_on_from_the_next_step(self):
        self.save()
        started = threading.Event()
        executor = self.executor([{"sleep": 0.6}, {}], hook=lambda n, call: started.set())
        thread = threading.Thread(target=lambda: self.start(executor))
        thread.start()
        self.addCleanup(thread.join, 20)
        self.assertTrue(started.wait(10))
        run, = looprun.list_runs(self.project)
        looprun.pause(self.folder(run))
        thread.join(20)
        record = self.record(run)
        self.assertEqual((record["status"], self.shown(run), len(executor.calls)), ("paused", "paused", 1))
        self.assertEqual(record["attempts"][0]["steps"][0]["outcome"], "returned")  # the step in flight was not cut short
        self.assertEqual((record["cursor"]["phase"], record["cursor"]["next"]), ("done", "check"))
        self.assertEqual(len(record["attempts"][0]["steps"]), 1)  # the check had not started
        self.assertFalse((self.folder(run) / "pause").exists() and False)
        self.assertEqual(self.resume(run, executor), "passed")
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual([(e["step"], e["outcome"]) for e in self.record(run)["attempts"][0]["steps"]], [("work", "returned"), ("check", "fail")])

    def test_pause_is_refused_unless_a_controller_is_running_it(self):
        self.save()
        run = self.start(self.executor())
        with self.assertRaises(looprun.Busy):
            looprun.pause(self.folder(run))
        run = self.crash(self.executor(hook=kill_at(1)))
        with self.assertRaises(looprun.Busy):
            looprun.pause(self.folder(run))
        self.assertFalse((self.folder(run) / "pause").exists())

    def test_cancel_works_on_a_paused_run_and_on_an_interrupted_one_without_a_controller(self):
        self.save(check="exit 1")
        executor = self.executor(hook=kill_at(2), answer="unknown", reason="cannot tell")
        run = self.crash(executor)
        self.assertEqual(self.resume(run, executor), "paused")
        code, out, _ = self.cli("cancel", run, "--project", str(self.project))
        record = self.record(run)
        self.assertEqual((code, record["status"], self.shown(run)), (0, "cancelled", "cancelled"))
        self.assertIsNone(record.get("passed_by"))
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(self.cli("cancel", run, "--project", str(self.project))[0], 1)  # over now
        self.assertEqual(self.cli("resume", run, "--project", str(self.project))[0], 1)
        other = self.crash(self.executor(hook=kill_at(1)))
        self.assertEqual(self.shown(other), "interrupted")
        self.assertEqual(self.cli("cancel", other, "--project", str(self.project))[0], 0)
        self.assertEqual(self.record(other)["status"], "cancelled")

    def test_cancel_of_a_run_with_a_live_controller_is_still_a_request(self):
        self.save()
        run = self.crash(self.executor(hook=kill_at(1)))
        lock = open(self.folder(run) / "lock", "a+")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            self.assertEqual(looprun.cancel(self.folder(run)), "requested")
            self.assertTrue((self.folder(run) / "cancel").is_file())
            self.assertEqual(self.record(run)["status"], "running")
        finally:
            lock.close()

    def test_answer_without_a_controller_is_recorded_and_resume_picks_it_up(self):
        self.save("duo", check="exit 0", approval="before_pass")
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}]})
        original = looprun.Controller._answer_file
        seen = []

        def answer_file(controller, path):
            seen.append(1)
            raise Kill()
        with mock.patch.object(looprun.Controller, "_answer_file", answer_file):
            run = self.crash(executor, "duo")
        record = self.record(run)
        self.assertEqual((record["status"], self.shown(run)), ("waiting", "waiting"))  # the page shows waiting either way
        self.assertEqual(self.cli("answer", run, "approve", "--note", "ship it", "--project", str(self.project))[0], 0)
        self.assertTrue((self.folder(run) / "answer.json").is_file())
        self.assertEqual(self.resume(run, executor), "passed")
        record = self.record(run)
        self.assertEqual((record["passed_by"], record["answers"][0]["answer"], record["answers"][0]["note"]), ("check+judges+approval", "approve", "ship it"))
        self.assertEqual([c["agent"] for c in executor.calls], ["worker", "reviewer"])  # no step ran again
        self.assertIs(original, looprun.Controller._answer_file)

    def test_a_waiting_run_can_be_cancelled_without_a_controller(self):
        self.save("duo", check="exit 0", approval="before_pass")
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}]})

        def answer_file(controller, path):
            raise Kill()
        with mock.patch.object(looprun.Controller, "_answer_file", answer_file):
            run = self.crash(executor, "duo")
        self.assertEqual(self.cli("cancel", run, "--project", str(self.project))[0], 0)
        self.assertEqual((self.record(run)["status"], self.record(run)["waiting"]), ("cancelled", None))


class CostTests(Base):
    def test_r6_a_missing_cost_reads_null_and_with_a_cost_limit_ends_the_run_limit(self):
        self.save(check="exit 1", cost=1.0)
        executor = self.executor([{"cost": None}] * 5)
        run = self.start(executor)
        record = self.record(run)
        self.assertEqual((record["status"], len(executor.calls)), ("limit", 1))
        self.assertIn("the spend of attempt 1 is unknown, so the spend limit cannot be checked", record["reason"])
        self.assertIsNone(record["totals"]["cost"])
        self.assertEqual(record["totals"]["cost_unknown_steps"], 1)
        self.assertEqual(list(record["session_costs"].values()), [None])

    def test_r6_no_cost_limit_means_the_run_goes_on_and_says_the_total_is_partial(self):
        self.save(check="exit 1", attempts=3)
        executor = self.executor([{"cost": None}, {"cost": 0.25}, {"cost": None}])
        run = self.start(executor)
        record = self.record(run)
        self.assertEqual((record["status"], len(executor.calls)), ("failed", 3))
        self.assertAlmostEqual(record["totals"]["cost"], 0.25)  # the known part, never padded with zeros
        self.assertEqual(record["totals"]["cost_unknown_steps"], 2)
        self.assertIsNone(list(record["session_costs"].values())[0])  # one session, one of its turns unpriced: its total is unknown
        self.assertIn("cost $0.2500", self.cli("status", run, "--project", str(self.project))[1])
        self.assertIn("2 turns unpriced", self.cli("status", run, "--project", str(self.project))[1])

    def test_per_session_and_per_run_cost_when_all_is_known(self):
        self.save("duo", check="exit 1", attempts=2, session="fresh")
        executor = self.executor({"worker": [{"cost": 0.1}, {"cost": 0.2}], "reviewer": [{"cost": 0.05, "text": PASS}]})
        run = self.start(executor, "duo")
        record = self.record(run)
        self.assertAlmostEqual(record["totals"]["cost"], 0.3)
        self.assertEqual(record["totals"]["cost_unknown_steps"], 0)
        self.assertEqual(sorted(record["session_costs"].values()), [0.1, 0.2])
        self.assertEqual(set(record["session_costs"]), set(record["sessions"]))

    def test_an_unknown_cost_before_a_judge_stops_the_judge_when_a_cost_limit_is_set(self):
        self.save("duo", check="exit 0", cost=5.0)
        executor = self.executor({"worker": [{"cost": None}], "reviewer": [{"text": PASS}]})
        run = self.start(executor, "duo")
        self.assertEqual((self.record(run)["status"], [c["agent"] for c in executor.calls]), ("limit", ["worker"]))

    def test_a_cost_limit_is_an_admission_limit_one_turn_can_end_above_it(self):
        self.save(check="exit 1", cost=1.0)
        executor = self.executor([{"cost": 0.9}, {"cost": 5.0}, {"cost": 1.0}])
        run = self.start(executor)
        record = self.record(run)
        self.assertEqual((record["status"], len(executor.calls)), ("limit", 2))
        self.assertAlmostEqual(record["totals"]["cost"], 5.9)


class HistoryTrialTests(Base):
    def test_r9_every_save_and_remove_keeps_the_old_bytes_and_restore_puts_them_back(self):
        path = looprun.settings_path(self.structure, "self")
        history = path.parent / ".history" / "self"
        self.save(check="echo 1")
        self.assertFalse(history.exists())  # nothing was replaced yet
        first = path.read_bytes()
        self.save(check="echo 2")
        second = path.read_bytes()
        looprun.remove_settings(self.structure, "self")
        versions = looprun.versions(self.structure, "self")
        self.assertEqual(len(versions), 2)  # newest first
        self.assertEqual([v["sha256"] for v in versions], [hashlib.sha256(second).hexdigest(), hashlib.sha256(first).hexdigest()])
        self.assertTrue(all(set(v) == {"version", "time", "sha256"} for v in versions))
        self.assertEqual((history / f"{versions[1]['version']}.json").read_bytes(), first)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir() if p.is_file() and not p.name.startswith(".")), [])
        looprun.restore(self.structure, "self", versions[1]["version"])
        self.assertEqual(path.read_bytes(), first)
        self.assertEqual(len(looprun.versions(self.structure, "self")), 2)  # nothing to keep after a remove, the restored file is the first version
        looprun.restore(self.structure, "self", versions[0]["version"])
        self.assertEqual(path.read_bytes(), second)
        self.assertEqual(len(looprun.versions(self.structure, "self")), 3)  # what was replaced by the restore is kept too: a restore can be undone
        with self.assertRaises(looprun.LoopError):
            looprun.restore(self.structure, "self", "../../etc/passwd")
        with self.assertRaises(looprun.LoopError):
            looprun.restore(self.structure, "self", "20000101T000000.000000Z-00000000")

    def test_at_most_fifty_versions_are_kept_the_oldest_go_first(self):
        for n in range(56):
            self.save(check=f"echo {n}")
        versions = looprun.versions(self.structure, "self")
        self.assertEqual(len(versions), 50)
        newest = json.loads((looprun.settings_path(self.structure, "self").parent / ".history" / "self" / f"{versions[0]['version']}.json").read_text())
        self.assertEqual(newest["check"]["command"], "echo 54")

    def test_history_does_not_make_an_orphan_or_change_a_flow_listing(self):
        self.save(check="echo 1")
        self.save(check="echo 2")
        from agentgraph import loopapi
        doc = loopapi.listing(self.structure, self.project, None)
        self.assertEqual(doc["orphans"], [])
        self.assertEqual([len(item["versions"]) for item in doc["loops"] if item["flow"] == "self"], [1])

    def test_trial_settings_are_pinned_and_save_nothing(self):
        self.save(check="exit 1", attempts=1)
        saved = looprun.settings_path(self.structure, "self").read_bytes()
        earlier = self.start(self.executor())
        before = tree_hash(self.folder(earlier))
        trial = {"check": {"command": "echo trial; exit 0", "timeout": 20}, "limits": {"attempts": 2}}
        run = self.start(self.executor(), settings=trial)
        record = self.record(run)
        self.assertEqual((record["status"], record["trial"]), ("passed", True))
        definition = json.loads((self.folder(run) / "definition.json").read_text())
        self.assertEqual((definition["trial"], definition["settings"]["check"]["command"], definition["settings"]["limits"]["attempts"]), (True, "echo trial; exit 0", 2))
        self.assertEqual(record["settings_sha256"], definition["settings_sha256"])
        self.assertEqual(record["settings_sha256"], hashlib.sha256(looprun._json(definition["settings"])).hexdigest())
        self.assertEqual(looprun.settings_path(self.structure, "self").read_bytes(), saved)  # the saved loop is as it was
        self.assertEqual(looprun.versions(self.structure, "self"), [])
        self.assertEqual(tree_hash(self.folder(earlier)), before)
        plain = self.record(earlier)
        self.assertFalse(plain.get("trial"))
        self.assertEqual(plain["settings_sha256"], hashlib.sha256(looprun._json(looprun.load_settings(self.structure, "self"))).hexdigest())
        self.assertEqual(self.cli("list", "--project", str(self.project))[1].count("trial"), 1)

    def test_a_trial_needs_no_saved_loop_and_a_bad_trial_is_refused_before_any_run_folder(self):
        run = self.start(self.executor(), settings={"check": {"command": "exit 0"}, "limits": {"attempts": 1}})
        self.assertEqual(self.record(run)["status"], "passed")
        self.assertIsNone(looprun.load_settings(self.structure, "self"))
        runs = looprun.list_runs(self.project)
        with self.assertRaises(looprun.LoopError):
            self.start(self.executor(), settings={"limits": {}})
        with self.assertRaises(looprun.LoopError):
            self.start(self.executor(), settings={"flow": "duo", "limits": {"attempts": 1}, "check": {"command": "exit 0"}})
        self.assertEqual(looprun.list_runs(self.project), runs)

    def test_the_command_line_versions_restore_and_settings_file(self):
        base = ["--structure", str(self.structure)]
        self.save(check="echo 1")
        self.save(check="echo 2")
        code, out, _ = self.cli("versions", "self", *base)
        self.assertEqual(code, 0)
        version = looprun.versions(self.structure, "self")[0]["version"]
        self.assertIn(version, out)
        self.assertEqual(self.cli("restore", "self", version, *base)[0], 0)
        self.assertEqual(looprun.load_settings(self.structure, "self")["check"]["command"], "echo 1")
        self.assertEqual(self.cli("restore", "self", "nope", *base)[0], 2)
        trial = self.tmp / "trial.json"
        trial.write_text(json.dumps({"check": {"command": "exit 0"}, "limits": {"attempts": 1}}))
        code, out, _ = self.cli("start", "self", "--goal", "g", "--settings-file", str(trial), "--wait", "--executable", str(self.tmp / "none"), *base, "--project", str(self.project))
        self.assertEqual(code, 1)  # no opencode here: an invalid run, but a trial one
        run = looprun.list_runs(self.project)[0]
        self.assertTrue(self.record(run)["trial"])
        self.assertEqual(looprun.load_settings(self.structure, "self")["check"]["command"], "echo 1")


class StatusWordsTests(Base):
    def test_status_and_list_say_paused_interrupted_and_what_to_do(self):
        self.save(check="exit 1")
        executor = self.executor(hook=kill_at(2), answer="unknown", reason="cannot tell if it ran")
        run = self.crash(executor)
        status = self.cli("status", run, "--project", str(self.project))[1]
        self.assertIn("interrupted", status)
        self.assertIn(f"agentgraph loop resume {run}", status)
        self.resume(run, executor)
        status = self.cli("status", run, "--project", str(self.project))[1]
        self.assertIn("paused", status)
        self.assertIn("cannot tell if it ran", status)
        self.assertIn("--redo", status)
        self.assertIn("paused", self.cli("list", "--project", str(self.project))[1])
        self.assertEqual(json.loads(self.cli("status", run, "--json", "--project", str(self.project))[1])["status"], "paused")
        self.assertNotIn("paused", looprun.FINAL)


if __name__ == "__main__":
    unittest.main()
