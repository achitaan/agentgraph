"""Recovery, pause and cost (release 3 of agentgraph.looprun): tests R1 to R9 of `design2.md` on the scripted executor, with real check
scripts in temp folders, one test that SIGKILLs a real detached controller process, and the history / trial / CLI tests. A controller
that dies is simulated by raising a `BaseException` inside the executor (or the check, or between two steps): the record is then left
as a SIGKILL would leave it. The scripted executor's `reconcile` reads the answer from a file the tests write. Every test that sends
something counts the executor's calls. No test starts OpenCode; a real compaction is not provoked."""
import contextlib
import hashlib
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.detached import reap_detached
from agentgraph import looprun, osfs, osproc
from tests.shellskip import CHECK_SECOND, python_command, sh_or
from tests.winskip import WINDOWS, needs_symlinks
REPO = Path(__file__).resolve().parent.parent
PASS = "Looks right to me.\nVERDICT: PASS"
WRONG_HEADER = sh_or("echo wrong header; exit 1", "print('wrong header'); raise SystemExit(1)")
SLEEP_THEN_FAIL = sh_or("sleep {n}; exit 1", "import time; time.sleep({n}); raise SystemExit(1)")


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
        reap_detached(self)
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

    def status_of_first_run(self):
        """The first run's status, or None while there is no run (or its record is not written yet: the folder shows up first)."""
        try:
            runs = looprun.list_runs(self.project)
            return self.record(runs[0]).get("status") if runs else None
        except looprun.LoopError:
            return None

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
        osfs.lock(lock.fileno())
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
        self.save(check=WRONG_HEADER, attempts=3)
        executor = self.executor(hook=kill_at(2), answer="not_sent")
        run = self.crash(executor, goal="Write the report with a header.")
        self.resume(run, executor)
        for call in executor.calls[1:]:
            for needle in ("Write the report with a header.", WRONG_HEADER, "exit code 1", "wrong header"):
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
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))  # on Windows os.kill with SIGTERM is TerminateProcess
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
        osfs.lock(lock.fileno())
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


# ---- round 2: findings of the review of release 3 (see `.opencode/artifacts/agentgraph-loops/v8/r2/findings.md`) ----

def queue(base, flow="self", **kw) -> str:
    """A queued run with no controller: the detached process is not started."""
    with mock.patch.object(looprun.subprocess, "Popen"):
        return looprun.start(base.project, flow, structure=base.structure, goal="g", executor=kw.pop("executor", base.executor()), **kw)


def age(base, run, seconds):
    """The run was created `seconds` ago."""
    for name in ("run.json", "definition.json"):
        path = base.folder(run) / name
        data = json.loads(path.read_text())
        data["created"] = data["created"] - seconds
        path.write_text(json.dumps(data))


class ProbeTests(Base):
    def test_f01_probers_in_a_tight_loop_neither_see_each_other_as_held_nor_stop_a_controller(self):
        self.save(check="exit 0")
        runs = [queue(self) for _ in range(6)]
        folders = [self.folder(r) for r in runs]
        stop, held, errors = threading.Event(), [], []

        def probe():
            while not stop.is_set():
                for folder in folders:
                    if looprun._held(folder):
                        held.append(folder.name)
        probers = [threading.Thread(target=probe) for _ in range(6)]
        for thread in probers:
            thread.start()
        try:
            time.sleep(0.5)
            self.assertEqual(held, [])  # nothing is running: a probe must never see another probe
            def go(folder):
                try:
                    looprun.execute(folder, self.executor([{}]))
                except BaseException as exc:  # noqa: BLE001
                    errors.append(f"{folder.name}: {type(exc).__name__}: {exc}")
            controllers = [threading.Thread(target=go, args=(f,)) for f in folders]
            for thread in controllers:
                thread.start()
            for thread in controllers:
                thread.join(60)
        finally:
            stop.set()
            for thread in probers:
                thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual([self.record(r)["status"] for r in runs], ["passed"] * 6)

    def test_f12_probing_creates_no_lock_file_and_a_missing_lock_means_not_held(self):
        self.save()
        run = queue(self)
        folder = self.folder(run)
        self.assertFalse((folder / "lock").exists())
        self.assertFalse(looprun._held(folder))
        looprun.shown_status(folder, self.record(run))
        looprun.hint(folder, self.record(run))
        self.assertFalse((folder / "lock").exists())

    def test_f12_listing_and_reading_runs_write_nothing_in_any_run_folder(self):
        self.save()
        done = self.start(self.executor())
        dead = self.crash(self.executor(hook=kill_at(1)))
        fresh = queue(self)
        old = queue(self)
        age(self, old, 300)
        for run in (dead, fresh, old):
            (self.folder(run) / "lock").unlink(missing_ok=True)

        def snapshot():
            return {str(p.relative_to(self.project)): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(looprun.loops_folder(self.project).rglob("*"))}
        before = snapshot()
        time.sleep(0.05)
        from agentgraph import loopapi
        loopapi.listing(self.structure, self.project, None)
        for run in (done, dead, fresh, old):
            loopapi.run_detail(self.project, run)
        for argv in (("list",), ("list", "--json"), ("status", dead), ("status", fresh, "--json"), ("status", old)):
            self.assertIn(self.cli(*argv, "--project", str(self.project))[0], (0,))
        self.assertEqual(snapshot(), before)

    @needs_symlinks
    def test_f12_a_symlinked_run_folder_is_not_listed_and_not_followed(self):
        self.save()
        run = self.start(self.executor())
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        link = looprun.loops_folder(self.project) / "20990101-000000-abcd"
        link.symlink_to(self.folder(run), target_is_directory=True)
        self.assertEqual(looprun.list_runs(self.project), [run])
        self.assertFalse(looprun._held(link))
        self.assertEqual(self.cli("list", "--project", str(self.project))[1].count("\n"), 1)
        from agentgraph import loopapi
        self.assertEqual([r["run"] for r in loopapi.listing(self.structure, self.project, None)["runs"]], [run])
        with self.assertRaises(loopapi.ApiError):
            loopapi.run_detail(self.project, "20990101-000000-abcd")


class LockHolder:
    """A real process that holds a file with an exclusive flock until it is closed: the lock of a live controller, as another process sees it."""

    def __init__(self, path, seconds=60):
        self.proc = subprocess.Popen([sys.executable, "-c", "import sys,time;from agentgraph import osfs;f=open(sys.argv[1],'a+');osfs.lock(f.fileno());print('held',flush=True);time.sleep(float(sys.argv[2]))",
                                      str(path), str(seconds)], stdout=subprocess.PIPE, text=True, cwd=str(REPO))
        self.proc.stdout.readline()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()


lock_holder = LockHolder


class QueuedRunTests(Base):
    def test_f02_cancel_of_a_queued_run_with_no_controller_marks_it_cancelled(self):
        self.save()
        run = queue(self)
        self.assertEqual(looprun.cancel(self.folder(run)), "cancelled")
        record = self.record(run)
        self.assertEqual(record["status"], "cancelled")
        self.assertIn("controller", record["reason"])
        with self.assertRaises(looprun.Busy):  # a controller that starts late finds a run that is over
            looprun.execute(self.folder(run), self.executor())

    def test_f02_a_queued_run_with_a_controller_that_holds_the_lock_is_still_asked(self):
        self.save()
        run = queue(self)
        lock = open(self.folder(run) / "lock", "a+")
        osfs.lock(lock.fileno())
        try:
            self.assertEqual(looprun.cancel(self.folder(run)), "requested")
        finally:
            lock.close()
        self.assertEqual(self.record(run)["status"], "queued")

    def test_f02_a_run_command_that_finds_the_lock_busy_writes_nothing(self):  # v8b 1: a held lock means a controller exists, and it owns the record
        self.save()
        run = queue(self)
        before = tree_hash(self.folder(run))
        with lock_holder(self.folder(run) / "lock"), mock.patch.object(looprun, "LOCK_WAIT", 0.1):
            code, _, err = self.cli("run", "--run-dir", str(self.folder(run)))
        self.assertEqual(code, 1)
        self.assertIn("already running", err)
        self.assertEqual(self.record(run)["status"], "queued")
        self.assertEqual({k: v for k, v in tree_hash(self.folder(run)).items() if k != "lock"}, {k: v for k, v in before.items() if k != "lock"})

    def test_f02_a_failed_start_is_written_only_under_the_lock(self):  # v8b 1
        self.save()
        run = queue(self)
        before = tree_hash(self.folder(run))
        with lock_holder(self.folder(run) / "lock"), mock.patch.object(looprun, "LOCK_WAIT", 0.1):
            looprun._never_ran(self.folder(run), None, looprun.LoopError("the definition cannot be read"))
        self.assertEqual({k: v for k, v in tree_hash(self.folder(run)).items() if k != "lock"}, {k: v for k, v in before.items() if k != "lock"})
        looprun._never_ran(self.folder(run), None, looprun.LoopError("the definition cannot be read"))  # with nobody holding it, the reason is written
        self.assertEqual((self.record(run)["status"], "definition" in self.record(run)["reason"]), ("invalid", True))

    def test_f02_an_executor_that_cannot_be_made_is_written_as_invalid_with_the_reason(self):
        self.save()
        run = queue(self)
        path = self.folder(run) / "definition.json"
        data = json.loads(path.read_text())
        data["executor"] = {"kind": "nonsense"}
        path.write_text(json.dumps(data))
        code, _, err = self.cli("run", "--run-dir", str(self.folder(run)))
        record = self.record(run)
        self.assertEqual((code, record["status"]), (2, "invalid"))
        self.assertIn("executor", record["reason"])

    def test_v8b2_3_the_resume_of_a_never_started_queued_run_that_fails_late_is_written_with_the_reason(self):  # `--resume ""` is a fresh start of a queued run
        self.save()
        run = queue(self)
        path = self.folder(run) / "definition.json"
        data = json.loads(path.read_text())
        data["executor"] = {"kind": "nonsense"}
        path.write_text(json.dumps(data))
        age(self, run, 300)  # a queued run younger than QUEUED_GRACE may still be starting and is refused
        code, _, _ = self.cli("run", "--run-dir", str(self.folder(run)), "--resume")
        record = self.record(run)
        self.assertEqual((code, record["status"]), (2, "invalid"))
        self.assertIn("executor", record["reason"])

    def test_v8b2_3_an_exception_of_any_type_before_the_first_attempt_is_written_as_the_reason_never_a_silent_queued(self):
        self.save()
        for resume in ((), ("--resume",)):
            run = queue(self)
            with mock.patch.object(looprun, "execute", side_effect=RuntimeError("the disk went away")), self.assertRaises(RuntimeError):
                self.cli("run", "--run-dir", str(self.folder(run)), *resume)
            record = self.record(run)
            self.assertEqual(record["status"], "invalid", resume)
            self.assertIn("the disk went away", record["reason"])
            self.assertIsNotNone(record["ended"])

    def test_v8b2_3_a_run_that_is_not_queued_keeps_its_record_when_the_command_fails(self):
        self.save()
        run = self.start(self.executor())
        before = self.record(run)
        looprun._never_ran(self.folder(run), "", RuntimeError("late"))
        self.assertEqual(self.record(run), before)

    def test_f02_an_unreadable_definition_is_written_as_invalid_with_the_reason(self):
        self.save()
        run = queue(self)
        (self.folder(run) / "definition.json").write_text("{not json")
        code, _, _ = self.cli("run", "--run-dir", str(self.folder(run)))
        record = self.record(run)
        self.assertEqual(record["status"], "invalid")
        self.assertIn("definition", record["reason"])

    def test_f02_a_queued_run_whose_controller_never_came_shows_interrupted_and_can_be_cancelled_or_resumed(self):
        self.save()
        young, old = queue(self), queue(self)
        age(self, old, 300)
        self.assertEqual((self.shown(young), self.shown(old)), ("queued", "interrupted"))
        hint = looprun.hint(self.folder(old), self.record(old))
        self.assertIn("resume", hint)
        self.assertIn("never", hint)
        with self.assertRaises(looprun.Busy) as raised:  # a young one may still be starting
            looprun.resume(self.folder(young), wait=True, executor=self.executor())
        self.assertIn("starting", str(raised.exception))
        with self.assertRaises(looprun.LoopError):  # nothing was sent: there is no step for --redo to send again
            looprun.resume(self.folder(old), "redo", wait=True, executor=self.executor())
        executor = self.executor()
        self.assertEqual(self.resume(old, executor), "passed")  # nothing was ever sent, so it is started as a new run
        self.assertEqual(len(executor.calls), 2)
        other = queue(self)
        age(self, other, 300)
        self.assertEqual(looprun.cancel(self.folder(other)), "cancelled")

    def test_f02_a_held_queued_run_is_never_shown_interrupted(self):
        self.save()
        run = queue(self)
        age(self, run, 300)
        lock = open(self.folder(run) / "lock", "a+")
        osfs.lock(lock.fileno())
        try:
            self.assertEqual(self.shown(run), "queued")
        finally:
            lock.close()


class FixtureDb:
    """A small sqlite file with the columns OpenCode's database really has (`session_v2`, `session_message`)."""

    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE session_v2 (id text PRIMARY KEY, parent_id text, cost real DEFAULT 0 NOT NULL, time_created integer NOT NULL, time_idle integer);
            CREATE TABLE session_message (id text PRIMARY KEY, session_id text NOT NULL, type text NOT NULL, seq integer NOT NULL, time_created integer NOT NULL,
                                          time_updated integer NOT NULL, data text NOT NULL);
            CREATE UNIQUE INDEX seq_idx ON session_message (session_id, seq);""")
        self.seq = {}

    def session(self, sid, parent=None, cost=0.0, made=1000, idle=1000):
        self.db.execute("INSERT INTO session_v2 (id, parent_id, cost, time_created, time_idle) VALUES (?,?,?,?,?)", (sid, parent, cost, made, idle))
        self.db.commit()

    def add(self, sid, kind, data, at):
        seq = self.seq.get(sid, -1) + 1
        self.seq[sid] = seq
        self.db.execute("INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) VALUES (?,?,?,?,?,?,?)",
                        (f"msg_{sid}_{seq}", sid, kind, seq, at, at, json.dumps(data)))
        self.db.commit()

    def user(self, sid, text, at):
        self.add(sid, "user", {"text": text, "time": {"created": at}}, at)

    def reply(self, sid, texts, at, cost=0.01):
        self.add(sid, "assistant", {"cost": cost, "content": [{"type": "reasoning", "text": ""}, *({"type": "text", "text": t} for t in texts)]}, at)

    def idle(self, sid, outcome, at):
        self.add(sid, "idle", {"outcome": outcome}, at)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class ReconcileRowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fx = FixtureDb(self.tmp / "o.db")
        self.sid = "ses_aaaaaaaaaaaa1111111111"
        self.addCleanup(self.fx.db.close)

    def ask(self, text="hello", sent_at=None, users=0, last_user=None):  # `users=None`: the cursor holds no count of the user messages before the send
        kw = {k: v for k, v in (("sent_at", sent_at), ("users", users), ("last_user", last_user)) if v is not None}
        return looprun.OpenCodeExecutor._reconcile_rows(self.fx.db, self.sid, sha(text), lambda why: {"answer": "unknown", "reason": why}, **kw)

    def test_a_session_that_is_not_there_was_never_sent_to(self):
        self.assertEqual(self.ask()["answer"], "not_sent")

    def test_no_user_message_is_not_sent(self):
        self.fx.session(self.sid)
        self.assertEqual(self.ask()["answer"], "not_sent")
        self.assertEqual(self.ask(sent_at=5.0, users=0)["answer"], "not_sent")

    def test_v8b2_a_missing_session_after_a_recorded_user_count_above_zero_is_unknown(self):
        answer = self.ask(users=3, sent_at=5.0)
        self.assertEqual(answer["answer"], "unknown")
        self.assertIn("3", answer["reason"])
        self.assertEqual(self.ask(users=0)["answer"], "not_sent")  # a first send into a session that was never made

    def test_v8b2_3_with_no_recorded_count_nothing_is_decided_whether_or_not_the_session_row_exists(self):  # the first line said `not_sent` for a missing row (round 3, finding 4)
        answer = self.ask(users=None, sent_at=5.0)  # the cursor says the send had started: the row may have held this message
        self.assertEqual(answer["answer"], "unknown")
        self.assertIn("no count", answer["reason"])
        self.fx.session(self.sid)
        for kw in ({}, {"sent_at": 5.0}, {"sent_at": 1e12}):  # a session that exists is never decided by the clock
            self.assertEqual(self.ask(users=None, **kw)["answer"], "unknown", kw)
        self.fx.user(self.sid, "hello", 1_000)
        self.fx.reply(self.sid, ["r"], 1_100)
        self.fx.idle(self.sid, "succeeded", 1_200)
        self.assertEqual(self.ask(users=None, sent_at=0.5)["answer"], "unknown")
        self.assertEqual(self.ask(users=None, sent_at=1e12)["answer"], "unknown")

    def test_v8b3_fewer_user_messages_than_before_the_send_is_unknown(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "a", 1_000)
        self.assertEqual(self.ask(users=2)["answer"], "unknown")

    def last(self, index):
        row = self.fx.db.execute("SELECT seq, id FROM session_message WHERE session_id=? AND type='user' ORDER BY seq LIMIT 1 OFFSET ?", (self.sid, index)).fetchone()
        return {"seq": row[0], "id": row[1]}

    def test_v8b2_4_one_user_message_removed_and_this_one_added_is_unknown_never_not_sent(self):  # an equal count with another last message
        self.fx.session(self.sid)
        self.fx.user(self.sid, "first", 1_000)
        self.fx.user(self.sid, "second", 2_000)
        before = self.last(1)  # what the cursor holds before the send: two user messages, the last one's seq and id
        self.assertEqual(self.ask(users=2, last_user=before)["answer"], "not_sent")  # nothing came since
        self.fx.db.execute("DELETE FROM session_message WHERE id=?", (before["id"],))  # the session is compacted: "second" goes ...
        self.fx.user(self.sid, "hello", 3_000)  # ... and this send's message arrives: two rows again
        self.fx.reply(self.sid, ["r"], 3_100)
        self.fx.idle(self.sid, "succeeded", 3_200)
        answer = self.ask(users=2, last_user=before)
        self.assertEqual(answer["answer"], "unknown")
        self.assertIn("removed or replaced", answer["reason"])
        self.assertEqual(self.ask(users=2)["answer"], "not_sent")  # the count alone was fooled: this is what the id adds

    def test_v8b2_4_the_message_the_count_ended_on_is_checked_and_a_cursor_without_it_keeps_the_count_alone(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "first", 1_000)
        before = self.last(0)
        self.fx.user(self.sid, "hello", 2_000)
        self.fx.reply(self.sid, ["r"], 2_100)
        self.fx.idle(self.sid, "succeeded", 2_200)
        self.assertEqual(self.ask(users=1, last_user=before)["answer"], "finished")
        self.assertEqual(self.ask(users=1, last_user={"seq": before["seq"], "id": "msg_other"})["answer"], "unknown")
        self.assertEqual(self.ask(users=1, last_user={"seq": 99, "id": before["id"]})["answer"], "unknown")
        self.assertEqual(self.ask(users=1)["answer"], "finished")  # a cursor written before `last_user` existed
        self.assertEqual(self.ask(users=0, last_user=None)["answer"], "finished")

    def test_v8b2_4_the_last_user_message_is_read_from_the_database_read_only(self):
        executor = object.__new__(looprun.OpenCodeExecutor)
        executor.db_path = self.fx.path
        self.assertIsNone(executor.last_user(self.sid))
        self.fx.session(self.sid)
        self.fx.user(self.sid, "a", 1_000)
        self.fx.user(self.sid, "b", 2_000)
        self.assertEqual(executor.last_user(self.sid), self.last(1))
        self.assertEqual(set(executor.last_user(self.sid)), {"seq", "id"})
        executor.db_path = self.tmp / "missing.db"
        self.assertIsNone(executor.last_user(self.sid))

    def test_a_newer_user_message_with_other_text_is_unknown_never_not_sent(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "someone typed this", 10_000)
        for kw in ({"sent_at": 9.0, "users": 0}, {"sent_at": 9.0}, {"users": 0}):
            answer = self.ask(**kw)
            self.assertEqual(answer["answer"], "unknown", kw)
            self.assertIn("new", answer["reason"])

    def test_a_user_message_older_than_the_send_does_not_count(self):  # v8b 3: the clock-only branches of this test encoded the defect; the count decides
        self.fx.session(self.sid)
        self.fx.user(self.sid, "hello", 1_000)
        self.fx.reply(self.sid, ["old reply"], 1_100)
        self.fx.idle(self.sid, "succeeded", 1_200)
        self.assertEqual(self.ask(sent_at=100.0, users=1)["answer"], "not_sent")  # one user message before the send, none since
        self.assertEqual(self.ask(sent_at=0.5, users=0)["answer"], "finished")  # the count says the message is new
        for at in (100.0, 0.5, 2.0):
            self.assertEqual(self.ask(sent_at=at, users=None)["answer"], "unknown", at)  # no count: the clock alone decides nothing

    def test_the_matching_message_with_no_reply_is_unknown(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "hello", 1_000)
        self.assertEqual(self.ask()["answer"], "unknown")
        self.assertEqual(self.ask(sent_at=0.5, users=0)["answer"], "unknown")

    def test_a_reply_without_idle_is_unknown(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "hello", 1_000)
        self.fx.reply(self.sid, ["half"], 1_100)
        self.assertEqual(self.ask()["answer"], "unknown")

    def test_idle_succeeded_after_a_reply_is_finished_with_its_text_cost_and_final_message(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "hello", 1_000)
        self.fx.reply(self.sid, ["thinking. VERDICT: PASS"], 1_100, cost=0.01)
        self.fx.reply(self.sid, ["and then", "the end"], 1_200, cost=0.02)
        self.fx.idle(self.sid, "succeeded", 1_300)
        answer = self.ask(sent_at=0.5, users=0)
        self.assertEqual((answer["answer"], answer["cost"], answer["sessions"]), ("finished", 0.03, 1))
        self.assertIn("VERDICT: PASS", answer["text"])
        self.assertEqual(answer["final"].splitlines()[-1], "the end")  # the last message that has text, not the parts of all of them
        self.assertNotIn("VERDICT", answer["final"])

    def test_an_idle_that_did_not_succeed_is_unknown(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "hello", 1_000)
        self.fx.reply(self.sid, ["x"], 1_100)
        self.fx.idle(self.sid, "errored", 1_200)
        self.assertEqual(self.ask()["answer"], "unknown")

    def test_two_identical_user_messages_since_the_send_are_unknown(self):
        self.fx.session(self.sid)
        for at in (1_000, 2_000):
            self.fx.user(self.sid, "hello", at)
            self.fx.reply(self.sid, ["r"], at + 100)
            self.fx.idle(self.sid, "succeeded", at + 200)
        answer = self.ask(sent_at=0.5, users=0)
        self.assertEqual(answer["answer"], "unknown")
        self.assertIn("two", answer["reason"])

    def test_a_retry_with_the_same_text_is_told_from_its_earlier_try_by_the_count_before_the_send(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "hello", 1_000)  # try 1: sent, answered, but the process failed afterwards
        self.fx.reply(self.sid, ["try one's reply"], 1_100)
        self.fx.idle(self.sid, "succeeded", 1_200)
        self.assertEqual(self.ask(sent_at=1.25, users=1)["answer"], "not_sent")  # try 2 was cut before its message arrived
        self.fx.user(self.sid, "hello", 1_300)  # try 2's message arrived, no reply yet
        answer = self.ask(sent_at=1.25, users=1)
        self.assertEqual(answer["answer"], "unknown")
        self.assertNotIn("final", answer)

    def test_a_busy_child_session_is_unknown_and_a_finished_one_is_counted(self):
        self.fx.session(self.sid, cost=0.1)
        self.fx.user(self.sid, "hello", 1_000)
        self.fx.reply(self.sid, ["x"], 1_100)
        self.fx.idle(self.sid, "succeeded", 1_200)
        self.fx.session("ses_child1", parent=self.sid, cost=0.5, made=1_050, idle=None)
        answer = self.ask()
        self.assertEqual(answer["answer"], "unknown")
        self.assertIn("ses_child1", answer["reason"])
        self.fx.db.execute("UPDATE session_v2 SET time_idle=1150 WHERE id='ses_child1'")
        self.fx.db.commit()
        answer = self.ask()
        self.assertEqual((answer["answer"], answer["sessions"], answer["cost"]), ("finished", 2, 0.51))

    def test_the_user_count_before_a_send_is_read_from_the_database_read_only(self):
        self.fx.session(self.sid)
        self.fx.user(self.sid, "a", 1_000)
        self.fx.user(self.sid, "b", 2_000)
        executor = object.__new__(looprun.OpenCodeExecutor)
        executor.db_path = self.fx.path
        self.assertEqual(executor.mark(self.sid), 2)
        self.assertEqual(executor.mark("ses_bbbbbbbbbbbb2222222222"), 0)
        executor.db_path = self.tmp / "missing.db"
        self.assertIsNone(executor.mark(self.sid))


class AliveTests(unittest.TestCase):
    def ps_says(self, answer):
        """The process list as `ps` prints it (POSIX: the real call, with subprocess.run answered); on Windows the list comes from PowerShell,
        so the same rows are handed in at the `osproc.list_processes` seam instead."""
        if not WINDOWS:
            if isinstance(answer, Exception):
                return mock.patch.object(looprun.subprocess, "run", side_effect=answer)
            return mock.patch.object(looprun.subprocess, "run", return_value=mock.Mock(stdout=answer))
        if isinstance(answer, Exception):
            return mock.patch.object(osproc, "list_processes", side_effect=answer)
        return mock.patch.object(osproc, "list_processes", return_value=osproc.parse_ps(answer, ("pid", "command")))

    def test_alive_matches_the_session_exactly_and_never_its_own_pid(self):
        ps = "\n".join([
            "  111 /bin/opencode run --standalone --format json --agent w --session ses_abc",
            f"  {os.getpid()} python -m agentgraph.looprun run --session ses_abc",
            "  222 /bin/opencode run --standalone --agent w --session ses_abcdef",
            "  333 /bin/opencode run --standalone --session ses_abc --auto",
            "  444 /bin/opencode run --standalone --session ses_xyz",
            "  not-a-pid line --session ses_abc"])
        executor = object.__new__(looprun.OpenCodeExecutor)
        with self.ps_says(ps):
            self.assertEqual(executor._alive("ses_abc"), [111, 333])
            self.assertEqual(executor._alive("ses_abcdef"), [222])
            self.assertEqual(executor._alive("ses_none"), [])
        with self.ps_says(OSError("no ps")):
            self.assertIsNone(executor._alive("ses_abc"))


class DbExecutor(looprun.ScriptedExecutor):
    """The scripted executor on top of a fixture database: every send writes what OpenCode would (the user message, a reply and an idle mark), so
    reconcile reads real rows through `OpenCodeExecutor._reconcile_rows`."""

    def __init__(self, fx, script, hook=None, replies=None):
        super().__init__(script, hook=hook)
        self.fx, self.replies, self.clock = fx, replies or {}, 1_000_000

    def mark(self, session):
        return self.fx.db.execute("SELECT count(*) FROM session_message WHERE session_id=? AND type='user'", (session,)).fetchone()[0]

    def last_user(self, session):
        row = self.fx.db.execute("SELECT seq, id FROM session_message WHERE session_id=? AND type='user' ORDER BY seq DESC LIMIT 1", (session,)).fetchone()
        return {"seq": row[0], "id": row[1]} if row else None

    def reconcile(self, session, message_sha256, sent_at=None, users=None, last_user=None):
        self.reconciles.append((session, message_sha256))
        unknown = lambda why: {"answer": "unknown", "reason": why}  # noqa: E731
        return looprun.OpenCodeExecutor._reconcile_rows(self.fx.db, session, message_sha256, unknown, **{k: v for k, v in (("sent_at", sent_at), ("users", users), ("last_user", last_user)) if v is not None})

    def attempt(self, **call):
        session = call["session"]
        if self.fx.db.execute("SELECT 1 FROM session_v2 WHERE id=?", (session,)).fetchone() is None:
            self.fx.session(session)
        self.clock += 1000
        self.fx.user(session, call["message"], int(time.time() * 1000) + self.clock - 1_000_000)
        n = len(self.calls) + 1
        if self.hook:
            self.hook(n, {**call, "n": n})
        item = self._item_for(call["agent"], n)
        if item.get("write_reply", True):
            self.fx.reply(session, [item.get("text", "ok")], int(time.time() * 1000) + self.clock - 1_000_000 + 10)
            self.fx.idle(session, "succeeded", int(time.time() * 1000) + self.clock - 1_000_000 + 20)
        self.calls.append({"agent": call["agent"], "session": session, "first": call["first"], "title": call["title"], "message": call["message"], "timeout": None, "cwd": "", "auto": False})
        error = item.get("error")
        return {"exit": 1 if error else 0, "outcome": "agent_error" if error else "returned", "session": session, "cost": 0.01, "tokens": None, "sessions": 1,
                "seconds": 0.0, "error": error, "text": item.get("text", "ok")}

    def _item_for(self, agent, n):
        items = self.script
        return items[n - 1] if n - 1 < len(items) else {}


class RetryAdoptionTests(Base):
    def setup_run(self):
        self.save("duo", check="exit 0", attempts=1, steps={"worker": {"retries": 1}, "reviewer": {"retries": 0}})
        fx = FixtureDb(self.tmp / "o.db")
        self.addCleanup(fx.db.close)  # an open database file cannot be deleted on Windows
        return fx

    def test_f04_kill_before_try_2_reached_the_session_does_not_adopt_try_1s_reply(self):
        fx = self.setup_run()
        executor = DbExecutor(fx, [{"error": "boom afterwards", "text": "try one's reply"}, {"text": "try two's reply"}, {"text": PASS}])
        original = looprun.Controller._turn

        def turn(controller, step, agent, session, title, message, attempt_try, *a, **kw):
            if attempt_try == 2 and not kw.get("result"):
                raise Kill()  # after the cursor of try 2 is written would be better; the cursor is written inside `_turn`, so die in the executor instead
            return original(controller, step, agent, session, title, message, attempt_try, *a, **kw)
        original_attempt = executor.attempt

        def attempt(**call):
            if len(executor.calls) == 1:
                raise Kill()  # try 2 is cut before its message is sent
            return original_attempt(**call)
        executor.attempt = attempt
        run = self.crash(executor, "duo")
        self.assertEqual(self.record(run)["cursor"]["try"], 2)
        executor.attempt = original_attempt
        looprun.resume(self.folder(run), wait=True, executor=executor)
        record = self.record(run)
        turns = [e for a in record["attempts"] for e in a["steps"] if e["kind"] == "agent" and e["role"] == "work"]
        self.assertEqual([t["try"] for t in turns], [1, 2])
        self.assertFalse(turns[1].get("recovered"), "try 2 adopted a reply that is try 1's")
        self.assertEqual(record["recoveries"][-1]["answer"], "not_sent")
        self.assertEqual(len(executor.calls), 3)  # try 1, then try 2 sent once after the resume, then the judge

    def test_v8b2_4_the_cursor_records_the_last_user_message_before_the_send_and_a_resume_uses_it(self):
        fx = self.setup_run()
        executor = DbExecutor(fx, [{"error": "boom afterwards", "text": "try one's reply"}, {"text": "try two's reply"}, {"text": PASS}])
        original_attempt = executor.attempt

        def attempt(**call):
            if len(executor.calls) == 1:
                raise Kill()  # try 2 is cut before its message is sent
            return original_attempt(**call)
        executor.attempt = attempt
        run = self.crash(executor, "duo")
        executor.attempt = original_attempt
        cursor = self.record(run)["cursor"]
        first = fx.db.execute("SELECT seq, id FROM session_message WHERE type='user' ORDER BY seq").fetchall()
        self.assertEqual((cursor["try"], cursor["users"], cursor["last_user"]), (2, 1, {"seq": first[0][0], "id": first[0][1]}))
        fx.db.execute("DELETE FROM session_message WHERE id=?", (first[0][1],))  # compaction takes try 1's message out ...
        fx.user(cursor["session"], "written by someone else", 9_999_999)  # ... and another message arrives: the count is 1 again
        fx.db.commit()
        looprun.resume(self.folder(run), wait=True, executor=executor)
        record = self.record(run)
        self.assertEqual((record["recoveries"][-1]["answer"], record["status"]), ("unknown", "paused"))
        self.assertEqual(len(executor.calls), 1)  # nothing was sent again

    def test_f04_kill_after_try_2s_message_arrived_waits_for_a_person(self):
        fx = self.setup_run()
        executor = DbExecutor(fx, [{"error": "boom afterwards", "text": "try one's reply"}, {"text": "try two", "write_reply": False}])

        def hook(n, call):
            if n == 2:
                raise Kill()
        executor.hook = hook
        run = self.crash(executor, "duo")
        executor.hook = None
        looprun.resume(self.folder(run), wait=True, executor=executor)
        record = self.record(run)
        self.assertEqual(record["status"], "paused")
        self.assertEqual(record["recoveries"][-1]["answer"], "unknown")
        self.assertEqual(len(executor.calls), 1)


class VerdictTests(Base):
    def duo(self, reviewer, **extra):
        self.save("duo", check="exit 0", attempts=1, steps={"reviewer": {"retries": 0}})
        return self.executor({"worker": [{}], "reviewer": [reviewer]})

    def test_f08_a_pass_in_an_earlier_message_is_not_the_verdict_when_the_last_message_says_something_else(self):
        executor = self.duo({"text": "findings\nVERDICT: PASS", "final": "I changed my mind, see below"})
        run = self.start(executor, "duo")
        record = self.record(run)
        judge = record["attempts"][0]["steps"][-1]
        self.assertEqual((judge["verdict"], record["status"]), ("inconclusive", "inconclusive"))

    def test_f08_the_verdict_is_read_from_the_last_message_that_has_text(self):
        executor = self.duo({"text": "VERDICT: FAIL\nthen the last message: VERDICT: PASS", "final": "all is well\nVERDICT: PASS"})
        run = self.start(executor, "duo")
        self.assertEqual(self.record(run)["status"], "passed")

    def test_f08_the_live_stream_is_split_into_messages_and_the_last_message_is_the_verdict(self):  # v8b 4: the `final` assertion below said "last message with text"
        def event(message, text):
            return json.dumps({"type": "text", "sessionID": "ses_x", "part": {"type": "text", "messageID": message, "text": text}})
        out = "\n".join([event("m1", "first part VERDICT: PASS"), event("m1", " ends here"), json.dumps({"type": "tool_use", "part": {}}), event("m2", "see you\nlater"),
                         json.dumps({"type": "text", "part": {"type": "text", "messageID": "m3", "text": ""}})])
        executor = object.__new__(looprun.OpenCodeExecutor)
        executor._seen = {}
        with mock.patch.object(looprun.OpenCodeExecutor, "_tree", return_value=None):
            result = executor._result("ses_x", "returned", 0, time.monotonic(), out)
        self.assertEqual(result["final"], "")  # the last message (m3) has no text
        self.assertEqual(looprun.read_verdict(result["final"])[0], "inconclusive")
        self.assertIn("VERDICT: PASS", result["text"])

    def test_v8b4_a_verdict_in_an_earlier_message_followed_by_text_less_messages_is_not_a_verdict(self):
        def event(kind, message, **part):
            return json.dumps({"type": kind, "sessionID": "ses_x", "part": {"messageID": message, "type": kind.replace("_", "-"), **part}})
        out = "\n".join([event("step_start", "m1"), event("text", "m1", text="all good\nVERDICT: PASS"), event("step_finish", "m1", reason="tool-calls"),
                         event("step_start", "m2"), event("tool_use", "m2", tool="bash"), event("step_finish", "m2", reason="stop")])
        self.assertEqual(looprun._final_message(out), "")
        executor = object.__new__(looprun.OpenCodeExecutor)
        executor._seen = {}
        with mock.patch.object(looprun.OpenCodeExecutor, "_tree", return_value=None):
            result = executor._result("ses_x", "returned", 0, time.monotonic(), out)
        self.assertEqual(looprun.read_verdict(result["final"])[0], "inconclusive")
        self.assertIn("VERDICT: PASS", result["text"])

    def test_v8b4_a_final_message_with_text_is_the_verdict_even_when_an_earlier_one_said_otherwise(self):
        def event(kind, message, **part):
            return json.dumps({"type": kind, "sessionID": "ses_x", "part": {"messageID": message, "type": kind.replace("_", "-"), **part}})
        out = "\n".join([event("text", "m1", text="VERDICT: FAIL"), event("tool_use", "m2", tool="bash"), event("step_start", "m3"), event("text", "m3", text="fixed.\nVERDICT: PASS")])
        self.assertEqual(looprun.read_verdict(looprun._final_message(out))[0], "pass")
        self.assertEqual(looprun._final_message("not json\n" + json.dumps({"type": "text", "part": {"text": "no ids"}})), "no ids")  # a stream without message ids is one message

    def test_v8b4_the_adopted_reply_ending_on_a_text_less_message_is_not_a_verdict(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        fx = FixtureDb(tmp / "o.db")
        self.addCleanup(fx.db.close)
        sid = "ses_aaaaaaaaaaaa1111111111"
        fx.session(sid)
        fx.user(sid, "hello", 1_000)
        fx.reply(sid, ["checked.\nVERDICT: PASS"], 1_100)
        fx.add(sid, "assistant", {"cost": 0.01, "content": [{"type": "tool", "name": "bash"}]}, 1_200)
        fx.idle(sid, "succeeded", 1_300)
        answer = looprun.OpenCodeExecutor._reconcile_rows(fx.db, sid, sha("hello"), lambda why: {"answer": "unknown", "reason": why}, users=0)
        self.assertEqual((answer["answer"], answer["final"]), ("finished", ""))
        self.assertIn("VERDICT: PASS", answer["text"])
        self.assertEqual(looprun.read_verdict(answer["final"])[0], "inconclusive")

    def test_v8b4_both_paths_end_inconclusive_when_the_final_message_has_no_text(self):
        self.save("duo", check="exit 0", attempts=1, steps={"reviewer": {"retries": 0}})
        run = self.start(self.executor({"worker": [{}], "reviewer": [{"text": PASS, "final": ""}]}), "duo")
        self.assertEqual(self.record(run)["status"], "inconclusive")
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}]}, hook=lambda n, call: (_ for _ in ()).throw(Kill()) if call["agent"] == "reviewer" else None,
                                 answer="finished", text=PASS, final="")
        run = self.crash(executor, "duo")
        looprun.resume(self.folder(run), wait=True, executor=executor)
        self.assertEqual(self.record(run)["status"], "inconclusive")

    def test_f08_the_adopted_reply_of_a_finished_turn_is_read_the_same_way(self):
        self.save("duo", check="exit 0", attempts=1, steps={"reviewer": {"retries": 0}})
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}]}, hook=lambda n, call: (_ for _ in ()).throw(Kill()) if call["agent"] == "reviewer" else None,
                                 answer="finished", text="found it.\nVERDICT: PASS", final="but then I said this")
        run = self.crash(executor, "duo")
        looprun.resume(self.folder(run), wait=True, executor=executor)
        record = self.record(run)
        judge = record["attempts"][0]["steps"][-1]
        self.assertEqual((judge["recovered"], judge["verdict"], record["status"]), (True, "inconclusive", "inconclusive"))

    def test_v8b4_5_a_turn_that_has_no_final_is_inconclusive_never_read_from_the_joined_text(self):
        self.save("duo", check="exit 0", attempts=1, steps={"reviewer": {"retries": 0}})
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}]}, hook=lambda n, call: (_ for _ in ()).throw(Kill()) if call["agent"] == "reviewer" else None,
                                 answer="finished", text="found it.\nVERDICT: PASS")  # an adopted turn whose answer carries no `final`
        run = self.crash(executor, "duo")
        looprun.resume(self.folder(run), wait=True, executor=executor)
        record = self.record(run)
        self.assertEqual((record["attempts"][0]["steps"][-1]["verdict"], record["status"]), ("inconclusive", "inconclusive"))

        class NoFinal(looprun.ScriptedExecutor):  # a turn read live by an executor that gives no `final`
            def attempt(self, **call):
                result = super().attempt(**call)
                result.pop("final", None)
                return result
        self.save("duo", check="exit 0", attempts=1, steps={"reviewer": {"retries": 0}})
        run = self.start(NoFinal({"worker": [{}], "reviewer": [{"text": PASS}]}), "duo")
        self.assertEqual(self.record(run)["status"], "inconclusive")
        run = self.start(self.executor({"worker": [{}], "reviewer": [{"text": PASS}]}), "duo")  # the scripted executor gives its text as the final message
        self.assertEqual(self.record(run)["status"], "passed")


class EndingTests(Base):
    def test_f06_a_check_routed_onward_that_failed_ends_failed_not_check_error(self):
        self.save("duo", check="exit 1", attempts=1, steps={"check": {"on": {"fail": "next"}}})
        run = self.start(self.executor({"worker": [{}], "reviewer": [{"text": PASS}]}), "duo")
        record = self.record(run)
        self.assertEqual(record["status"], "failed")
        self.assertIn("exit", record["reason"])
        self.assertIsNone(record.get("passed_by"))

    def test_f06_check_error_and_inconclusive_checks_routed_onward_keep_their_words(self):
        for code, status in ((127, "check_error"), (75, "inconclusive")):
            self.save("duo", check=f"exit {code}", attempts=1, steps={"check": {"on": {"check_error": "next", "inconclusive": "next"}}})
            run = self.start(self.executor({"worker": [{}], "reviewer": [{"text": PASS}]}), "duo")
            self.assertEqual(self.record(run)["status"], status, code)

    def test_f06_a_judge_that_failed_and_was_routed_onward_ends_failed(self):
        self.save("duo", check="exit 0", attempts=1, steps={"reviewer": {"on": {"fail": "next"}}})
        run = self.start(self.executor({"worker": [{}], "reviewer": [{"text": "bad\nVERDICT: FAIL"}]}), "duo")
        record = self.record(run)
        self.assertEqual(record["status"], "failed")
        self.assertIn("reviewer", record["reason"])

    def test_f06_a_judge_that_gave_no_verdict_and_was_routed_onward_ends_inconclusive(self):
        self.save("duo", check="exit 0", attempts=1, steps={"reviewer": {"on": {"inconclusive": "next"}, "retries": 0}})
        run = self.start(self.executor({"worker": [{}], "reviewer": [{"text": "no verdict"}]}), "duo")
        self.assertEqual(self.record(run)["status"], "inconclusive")

    def test_f07_a_person_cannot_pass_a_worker_outcome_that_can_never_pass(self):
        for outcome in ("agent_error", "no_artifact"):
            with self.assertRaises(looprun.LoopError) as raised:
                looprun.compile(["worker", "reviewer", "worker"], self.settings("duo", steps={"worker": {"on": {outcome: "human"}}}), {"worker", "reviewer"})
            self.assertIn(outcome, str(raised.exception))
            self.assertIn("person", str(raised.exception))
        steps = looprun.compile(["worker", "reviewer", "worker"], self.settings("duo", steps={"worker": {"on": {"stalled": "human"}}}), {"worker", "reviewer"})
        self.assertEqual(steps[0]["on"]["stalled"], "human")  # a stalled worker may have done the work


class PinTests(Base):
    def interrupted(self):
        self.save("duo", check="exit 0", attempts=3, steps={"reviewer": {"retries": 0}})
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}, {"text": PASS}]}, hook=kill_at(2), answer="not_sent")
        return self.crash(executor, "duo"), executor

    def edit(self, run, key, value):
        path = self.folder(run) / "definition.json"
        data = json.loads(path.read_text())
        data[key] = value
        path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def test_f05_changed_steps_are_refused_on_resume_and_the_run_is_left_as_it_was(self):
        run, executor = self.interrupted()
        data = json.loads((self.folder(run) / "definition.json").read_text())
        steps = data["steps"]
        steps[0]["on"]["returned"] = "done"
        self.edit(run, "steps", steps)
        before = tree_hash(self.folder(run))
        for wait in (True, False):
            with self.assertRaises(looprun.LoopError) as raised:
                looprun.resume(self.folder(run), wait=wait, executor=executor)
            self.assertIn("steps_sha256", str(raised.exception))
        self.assertEqual({k: v for k, v in tree_hash(self.folder(run)).items() if k != "lock"}, {k: v for k, v in before.items() if k != "lock"})
        self.assertEqual(self.record(run)["status"], "running")
        self.assertEqual(self.cli("resume", run, "--project", str(self.project))[0], 2)

    def test_f05_changed_settings_are_refused_on_resume(self):
        run, executor = self.interrupted()
        data = json.loads((self.folder(run) / "definition.json").read_text())
        settings = data["settings"]
        settings["limits"]["attempts"] = 40
        self.edit(run, "settings", settings)
        with self.assertRaises(looprun.LoopError) as raised:
            looprun.resume(self.folder(run), wait=True, executor=executor)
        self.assertIn("settings_sha256", str(raised.exception))
        self.assertEqual(self.record(run)["status"], "running")

    def test_f05_a_queued_run_whose_definition_changed_is_not_started_and_says_why(self):
        self.save()
        run = queue(self)
        self.edit(run, "settings", {**json.loads((self.folder(run) / "definition.json").read_text())["settings"], "allow_asks": True})
        code, _, err = self.cli("run", "--run-dir", str(self.folder(run)))
        record = self.record(run)
        self.assertEqual((code, record["status"]), (2, "invalid"))
        self.assertIn("settings_sha256", record["reason"])

    def edit_record(self, run, **fields):
        path = self.folder(run) / "run.json"
        data = json.loads(path.read_text())
        data.update(fields)
        path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def test_v8b6_a_definition_that_was_re_pinned_in_its_own_file_alone_is_refused_against_the_pin_in_run_json(self):
        run, executor = self.interrupted()
        data = json.loads((self.folder(run) / "definition.json").read_text())
        data["steps"][0]["on"]["returned"] = "done"
        self.edit(run, "steps", data["steps"])
        self.edit(run, "steps_sha256", looprun.steps_hash(data["steps"]))  # the hash agrees with the new steps, in definition.json only
        data["settings"]["limits"]["attempts"] = 40
        before = tree_hash(self.folder(run))
        for wait in (True, False):
            with self.assertRaises(looprun.LoopError) as raised:
                looprun.resume(self.folder(run), wait=wait, executor=executor)
            self.assertIn("steps_sha256", str(raised.exception))
            self.assertIn("run.json", str(raised.exception))
        self.edit(run, "steps", json.loads((self.folder(run) / "definition.json").read_text())["steps"])
        self.edit(run, "settings", data["settings"])
        self.edit(run, "settings_sha256", hashlib.sha256(looprun._json(data["settings"])).hexdigest())
        with self.assertRaises(looprun.LoopError) as raised:
            looprun.resume(self.folder(run), wait=True, executor=executor)
        self.assertIn("run.json", str(raised.exception))
        self.assertEqual(self.record(run)["status"], "running")

    def test_v8b6_run_json_keeps_the_steps_pin_from_the_start_and_both_pins_match_the_definition(self):
        self.save()
        run = queue(self)
        record, definition = self.record(run), json.loads((self.folder(run) / "definition.json").read_text())
        self.assertEqual(record["steps_sha256"], definition["steps_sha256"])
        self.assertEqual(record["steps_sha256"], looprun.steps_hash(definition["steps"]))
        self.assertEqual(record["settings_sha256"], definition["settings_sha256"])

    def test_v8b6_a_run_with_no_pin_is_refused_in_words_that_say_so_and_is_left_as_it_was(self):
        run, executor = self.interrupted()
        for where, key in (("run.json", "steps_sha256"), ("run.json", "settings_sha256"), ("definition.json", "steps_sha256"), ("definition.json", "settings_sha256")):
            path = self.folder(run) / where
            original = path.read_text()
            data = json.loads(original)
            del data[key]
            path.write_text(json.dumps(data))
            before = tree_hash(self.folder(run))
            with self.assertRaises(looprun.LoopError) as raised:
                looprun.resume(self.folder(run), wait=True, executor=executor)
            message = str(raised.exception)
            self.assertIn("no", message)
            self.assertIn(f"{key} pin in {where}", message)
            self.assertNotIn("was changed", message)
            path.write_text(original)
            self.assertEqual({k: v for k, v in tree_hash(self.folder(run)).items() if k not in ("lock", where)}, {k: v for k, v in before.items() if k not in ("lock", where)})
        self.assertEqual(self.resume(run, executor), "passed")

    def test_f05_an_untouched_definition_resumes(self):
        run, executor = self.interrupted()
        self.assertEqual(self.resume(run, executor), "passed")


class ClockTests(Base):
    def test_f09_a_kill_during_a_turn_loses_at_most_one_heartbeat_of_the_clock(self):
        self.save()
        seen = {}

        class Dies(looprun.ScriptedExecutor):
            def attempt(self, **call):
                started = time.monotonic()
                while time.monotonic() - started < 1.0:
                    call["poll"]()
                    time.sleep(0.02)
                seen["elapsed"] = time.monotonic() - started
                raise Kill()
        with mock.patch.object(looprun, "HEARTBEAT", 0.2):
            run = self.crash(Dies([{}]))
        saved = self.record(run)["totals"]["seconds"]
        # at most one heartbeat (0.2) is lost; the rest is the poll's own cadence, which a busy shared machine stretches
        self.assertGreaterEqual(saved, seen["elapsed"] - 0.2 - 0.3)

    def test_f09_a_kill_during_a_check_loses_at_most_one_heartbeat_of_the_clock(self):
        self.save(check=SLEEP_THEN_FAIL.replace("{n}", "3"))
        clock = {}
        original = looprun.Controller.poll

        def poll(controller):
            clock.setdefault("start", time.monotonic())
            if time.monotonic() - clock["start"] > 1.0:
                clock["end"] = time.monotonic()
                raise Kill()
            return original(controller)
        with mock.patch.object(looprun, "HEARTBEAT", 0.2), mock.patch.object(looprun.Controller, "poll", poll):
            run = self.crash(self.executor())
        saved = self.record(run)["totals"]["seconds"]
        self.assertGreaterEqual(saved, clock["end"] - clock["start"] - 0.2 - 0.5)  # the check polls every quarter second

    def test_f09_time_with_no_controller_is_not_counted_and_the_clock_goes_on_from_the_saved_value(self):
        self.save(check=CHECK_SECOND)
        run = self.crash(self.executor(hook=kill_at(2), answer="not_sent"))
        before = self.record(run)["totals"]["seconds"]
        time.sleep(1.2)
        executor = self.executor(answer="not_sent")
        self.assertEqual(self.resume(run, executor), "passed")
        after = self.record(run)["totals"]["seconds"]
        self.assertGreaterEqual(after, before)
        self.assertLess(after, before + 1.2)


class CancelWhilePausingTests(Base):
    def pausing(self, cancel_after_pause: bool):
        """A controller that has been asked to pause and, at the moment it pauses (its lock still held), is asked to cancel by the real `cancel`."""
        self.save(check="exit 1", attempts=3)
        box = {"calls": 0}
        original = looprun.Controller._pause

        def hook(count, call):
            box["calls"] = count
            looprun.pause(self.folder(looprun.list_runs(self.project)[0]))

        def pause(controller, reason):
            if cancel_after_pause:
                result = original(controller, reason)
                box["result"] = looprun.cancel(controller.folder)  # past its pause check: the file is left for whoever resumes
                return result
            box["result"] = looprun.cancel(controller.folder)
            return original(controller, reason)
        executor = self.executor(hook=hook)
        with mock.patch.object(looprun.Controller, "_pause", pause), mock.patch.object(looprun, "CANCEL_WAIT", 0.2):  # the lock is the pausing controller's own: the wait only runs out
            run = self.start(executor)
        return run, executor, box

    def test_v8b7_a_cancel_that_arrives_while_the_controller_pauses_ends_the_run_cancelled_at_once(self):
        run, executor, box = self.pausing(False)
        self.assertEqual(box["result"], "requested")
        self.assertEqual(self.record(run)["status"], "cancelled")  # "requested" came true
        self.assertEqual(len(executor.calls), 1)
        self.assertFalse((self.folder(run) / "pause").exists())

    def test_v8b2_5_a_cancel_answered_requested_for_a_run_that_then_ends_paused_is_marked_cancelled_once_the_lock_frees(self):
        self.save(check="exit 1", attempts=3)
        box = {}
        original = looprun.Controller._pause

        def hook(count, call):
            looprun.pause(self.folder(looprun.list_runs(self.project)[0]))

        def pause(controller, reason):
            result = original(controller, reason)  # past its cancel check: the run is `paused` and the lock is still held
            box["thread"] = threading.Thread(target=lambda: box.update(result=looprun.cancel(controller.folder)))
            box["thread"].start()
            deadline = time.monotonic() + 5
            while not (controller.folder / "cancel").exists() and time.monotonic() < deadline:
                time.sleep(0.01)  # the request is written: it is the window the controller can no longer see
            return result
        executor = self.executor(hook=hook)
        with mock.patch.object(looprun.Controller, "_pause", pause):
            run = self.start(executor)
        box["thread"].join(10)
        self.assertEqual(box["result"], "cancelled")
        record = self.record(run)
        self.assertEqual(record["status"], "cancelled")
        self.assertIn("cancel", record["reason"])
        self.assertFalse((self.folder(run) / "cancel").exists())  # no stale request is left for a later resume
        self.assertEqual(len(executor.calls), 1)

    def test_v8b2_5_a_cancel_that_finds_the_lock_still_held_after_the_wait_stays_requested(self):
        run, executor, box = self.pausing(True)
        self.assertEqual(box["result"], "requested")
        self.assertEqual(self.record(run)["status"], "paused")

    def test_v8b2_5_a_cancel_of_a_plainly_running_run_does_not_wait(self):
        self.save(check="exit 1", attempts=3)
        seen = {}

        def hook(count, call):
            start = time.monotonic()
            seen["result"] = looprun.cancel(self.folder(looprun.list_runs(self.project)[0]))
            seen["seconds"] = time.monotonic() - start
        self.start(self.executor(hook=hook))
        self.assertEqual(seen["result"], "requested")
        self.assertLess(seen["seconds"], 1.0)

    def test_v8b3_2_a_cancel_asked_while_the_controller_pauses_itself_never_leaves_a_stale_request(self):  # an unsure step: no `pause` file, status `running` when read
        self.save(check="exit 1", attempts=3)
        executor = self.executor(hook=kill_at(2), answer="unknown", reason="cannot tell if it ran")
        run = self.crash(executor)
        box, original = {}, looprun.Controller.save

        def save(controller):
            if controller.state.get("status") == "paused" and "thread" not in box:  # the pause is being written; run.json still says `running`
                box["thread"] = threading.Thread(target=lambda: box.update(result=looprun.cancel(controller.folder)))
                box["thread"].start()
                time.sleep(0.4)  # the cancel has read `running` and writes its request now (or waits for the lock it must take)
            return original(controller)
        with mock.patch.object(looprun.Controller, "save", save):
            self.assertEqual(self.resume(run, executor), "paused")
        box["thread"].join(10)
        self.assertEqual(box["result"], "cancelled")
        self.assertEqual(self.record(run)["status"], "cancelled")
        self.assertFalse((self.folder(run) / "cancel").exists())

    def test_v8b3_2_every_writer_of_paused_is_the_one_that_checks_for_a_cancel_under_the_lock(self):
        source = Path(looprun.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count('status="paused"'), 1)  # a second writer would need the same check
        self.save(check="exit 1", attempts=3)
        executor = self.executor(hook=kill_at(2), answer="unknown", reason="cannot tell")
        run = self.crash(executor)
        looprun.request_cancel(self.folder(run))  # asked before the controller reconciles: the unsure step ends cancelled, not paused
        self.assertEqual(self.resume(run, executor), "cancelled")
        self.assertFalse((self.folder(run) / "cancel").exists())

    def test_v8b3_3_a_cancel_of_a_final_run_says_so_and_leaves_no_request_file(self):
        self.save(check="exit 0", attempts=1)
        run = self.start(self.executor())
        self.assertEqual(self.record(run)["status"], "passed")
        with self.assertRaises(looprun.Busy) as caught:
            looprun.cancel(self.folder(run))
        self.assertIn("already passed", str(caught.exception))
        self.assertFalse((self.folder(run) / "cancel").exists())

    def test_v8b3_3_a_request_that_races_the_end_of_the_run_never_survives_it(self):
        self.save(check="exit 1", attempts=2)
        box = {}

        def hook(count, call):
            if count == 1:
                box["thread"] = threading.Thread(target=lambda: box.update(result=self.ask_cancel(box)))
                box["thread"].start()
                box["thread"].join(10)
        self.ask_cancel = lambda box: looprun.cancel(self.folder(looprun.list_runs(self.project)[0]))
        run = self.start(self.executor(hook=hook))
        self.assertEqual(box["result"], "requested")
        self.assertEqual(self.record(run)["status"], "cancelled")
        self.assertFalse((self.folder(run) / "cancel").exists())  # the run ended and took its own request with it

    def test_v8b3_6_a_run_cancelled_while_paused_has_the_keys_of_one_cancelled_while_running(self):
        self.save(check="exit 1", attempts=3)
        running = self.start(self.executor(hook=lambda count, call: looprun.request_cancel(self.folder(looprun.list_runs(self.project)[0]))))
        self.save(check="exit 1", attempts=3)
        executor = self.executor(hook=kill_at(2), answer="unknown", reason="cannot tell")
        paused_run = self.crash(executor)
        self.assertEqual(self.resume(paused_run, executor), "paused")
        self.assertEqual(looprun.cancel(self.folder(paused_run)), "cancelled")
        a, b = self.record(running), self.record(paused_run)
        self.assertEqual((a["status"], b["status"]), ("cancelled", "cancelled"))
        self.assertEqual(set(a) - {"pid", "resumed", "recoveries", "cursor"}, set(b) - {"pid", "resumed", "recoveries", "cursor"})
        for key in ("totals", "session_costs", "heartbeat", "ended"):
            self.assertIn(key, b)
        self.assertGreaterEqual(b["heartbeat"], b["ended"])  # `finish` saves after it sets `ended`: the heartbeat is the end's, not the pause's
        self.assertEqual(b["totals"]["attempts"], len(b["attempts"]))
        self.assertEqual(set(a["totals"]), set(b["totals"]))
        self.assertIsNone(b["waiting"])
        events = [json.loads(line) for line in (self.folder(paused_run) / "events.jsonl").read_text().splitlines()]
        self.assertEqual({k for k in events[-1]}, {k for k in [json.loads(line) for line in (self.folder(running) / "events.jsonl").read_text().splitlines()][-1]})
        for name in ("cancel", "pause", "answer.json"):
            self.assertFalse((self.folder(paused_run) / name).exists())

    def test_v8b3_6_a_queued_run_cancelled_without_a_controller_carries_totals_too(self):
        self.save()
        run = queue(self)
        self.assertEqual(looprun.cancel(self.folder(run)), "cancelled")
        record = self.record(run)
        self.assertEqual(set(record["totals"]), {"cost", "seconds", "sessions", "attempts", "cost_unknown_steps"})
        self.assertEqual(record["session_costs"], {})

    def test_v8b7_a_cancel_left_behind_by_a_pausing_controller_ends_the_next_resume_without_running_a_step(self):
        run, executor, box = self.pausing(True)
        self.assertEqual(box["result"], "requested")
        self.assertEqual(self.record(run)["status"], "paused")
        self.assertTrue((self.folder(run) / "cancel").exists())
        calls = len(executor.calls)
        self.assertEqual(self.resume(run, executor), "cancelled")
        self.assertEqual(len(executor.calls), calls)  # no step was sent
        self.assertIsNone(self.record(run)["attempts"][-1].get("check"))  # nor was the check run
        self.assertIn("cancel", self.record(run)["reason"])


class CancelInCheckTests(Base):
    def test_f10_cancel_during_a_check_stops_its_process_group_and_ends_cancelled(self):
        where = self.tmp.as_posix()
        in_python = f"import os, time; open('{where}/pid', 'w').write(str(os.getpid())); time.sleep(60)"
        # the Git sh's `$$` is not a Windows process number, so on Windows the process that writes its own number is Python (sh or no sh)
        self.save(check=python_command(in_python) if WINDOWS else sh_or(f"echo $$ > {where}/pid; sleep 60; exit 1", in_python))
        executor = self.executor()
        box = {}

        def cancel_soon():
            end = time.monotonic() + 20
            while time.monotonic() < end and not (self.tmp / "pid").exists():
                time.sleep(0.02)
            time.sleep(0.3)
            run, = looprun.list_runs(self.project)
            box["result"] = looprun.cancel(self.folder(run))
            box["at"] = time.monotonic()
        thread = threading.Thread(target=cancel_soon)
        thread.start()
        started = time.monotonic()
        run = self.start(executor)
        done = time.monotonic()
        thread.join(10)
        self.assertEqual(box["result"], "requested")
        self.assertLess(done - box["at"], 3)
        self.assertLess(done - started, 15)
        self.assertEqual(self.record(run)["status"], "cancelled")
        pid = int((self.tmp / "pid").read_text())
        time.sleep(0.2)
        self.assertFalse(osproc.is_alive(pid))

    def test_f10_a_pause_during_a_check_lets_it_finish(self):
        self.save(check=SLEEP_THEN_FAIL.replace("{n}", "1"))
        executor = self.executor()

        def pause_soon():
            end = time.monotonic() + 20
            while time.monotonic() < end and not looprun.list_runs(self.project):
                time.sleep(0.02)
            run, = looprun.list_runs(self.project)
            while time.monotonic() < end:
                try:
                    cursor = looprun.read_run(self.folder(run)).get("cursor") or {}
                except looprun.LoopError:  # the folder is listed a moment before run.json is written
                    cursor = {}
                if cursor.get("step") == "check":
                    break
                time.sleep(0.02)
            try:
                looprun.pause(self.folder(run))
            except BaseException as exc:  # a thread's exception would otherwise be lost, and the status below would be all there is to see
                errors.append(repr(exc))
        errors = []
        thread = threading.Thread(target=pause_soon)
        thread.start()
        run = self.start(executor)
        thread.join(10)
        record = self.record(run)
        self.assertEqual(errors, [])
        self.assertEqual(record["status"], "paused")
        self.assertEqual(record["attempts"][0]["check"]["exit"], 1)  # the check ran to its end


class AnswerTests(Base):
    def waiting(self, **extra):
        """A run that asked a person and whose controller died while it waited."""
        self.save("duo", check="exit 0", approval="before_pass", **extra)
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}]})

        def answer_file(controller, path):
            raise Kill()
        with mock.patch.object(looprun.Controller, "_answer_file", answer_file):
            run = self.crash(executor, "duo")
        return run, executor

    def test_f11_a_stale_answer_lock_never_wedges_a_run(self):
        run, executor = self.waiting()
        (self.folder(run) / "answer.lock").write_text("a person's command that was killed")
        looprun.answer(self.folder(run), "approve", "go")
        self.assertTrue((self.folder(run) / "answer.json").is_file())
        self.assertEqual(self.resume(run, executor), "passed")

    def test_f11_an_answer_is_decided_under_the_lock_and_never_written_into_a_run_that_stopped_waiting(self):
        run, executor = self.waiting()
        folder = self.folder(run)
        box = {}
        lock = open(folder / "answer.lock", "a+")
        osfs.lock(lock.fileno())  # the controller is in the middle of leaving the waiting state

        def answer():
            try:
                looprun.answer(folder, "approve")
            except looprun.Busy as exc:
                box["error"] = str(exc)
        thread = threading.Thread(target=answer)
        thread.start()
        time.sleep(0.3)
        state = looprun.read_run(folder)
        state.update(status="cancelled", waiting=None)
        (folder / "run.json").write_text(json.dumps(state))
        lock.close()
        thread.join(10)
        self.assertIn("not waiting", box.get("error", ""))
        self.assertFalse((folder / "answer.json").exists())

    def race(self, run, end):
        """The real `answer()` is held at the moment it writes `answer.json` (it has read `waiting` and holds the answer lock), `end` is run in
        another thread (the real `cancel`, or a controller ending the run), and only then does the answer go on: whoever lost must leave nothing."""
        folder = self.folder(run)
        at_write, go, box = threading.Event(), threading.Event(), {}
        original = looprun._write_atomic

        def write(path, data):
            if Path(path).name == "answer.json":
                at_write.set()
                go.wait(10)
            return original(path, data)

        def give():
            try:
                looprun.answer(folder, "approve")
            except looprun.Busy as exc:
                box["refused"] = str(exc)
        with mock.patch.object(looprun, "_write_atomic", write):
            giver = threading.Thread(target=give)
            giver.start()
            self.assertTrue(at_write.wait(10))
            ender = threading.Thread(target=end)
            ender.start()
            time.sleep(0.5)  # a transition that does not take the answer lock is over by now
            go.set()
            giver.join(20)
            ender.join(20)
        return box

    def test_v8b5_a_cancel_that_races_an_answer_never_leaves_answer_json_in_a_cancelled_run(self):
        run, executor = self.waiting()
        folder = self.folder(run)
        self.race(run, lambda: self.assertEqual(looprun.cancel(folder), "cancelled"))
        self.assertEqual(self.record(run)["status"], "cancelled")
        self.assertFalse((folder / "answer.json").exists())

    def test_v8b5_a_controller_that_ends_the_run_while_an_answer_is_being_written_never_leaves_answer_json_behind(self):
        self.save("duo", check="exit 0", approval="before_pass")
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}]})
        thread = threading.Thread(target=lambda: self.start(executor, "duo"))
        thread.start()
        end = time.monotonic() + 20
        while time.monotonic() < end and self.status_of_first_run() != "waiting":
            time.sleep(0.02)
        run, = looprun.list_runs(self.project)
        folder = self.folder(run)
        self.race(run, lambda: looprun.request_cancel(folder))
        thread.join(20)
        self.assertEqual(self.record(run)["status"], "cancelled")
        self.assertFalse((folder / "answer.json").exists())

    def test_f11_a_cancelled_waiting_controller_refuses_a_later_answer(self):
        self.save("duo", check="exit 0", approval="before_pass")
        executor = self.executor({"worker": [{}], "reviewer": [{"text": PASS}]})
        thread = threading.Thread(target=lambda: self.start(executor, "duo"))
        thread.start()
        end = time.monotonic() + 20
        while time.monotonic() < end and self.status_of_first_run() != "waiting":
            time.sleep(0.02)
        run, = looprun.list_runs(self.project)
        self.assertEqual(looprun.cancel(self.folder(run)), "requested")
        thread.join(20)
        self.assertEqual(self.record(run)["status"], "cancelled")
        with self.assertRaises(looprun.Busy):
            looprun.answer(self.folder(run), "approve")
        self.assertFalse((self.folder(run) / "answer.json").exists())

    def test_f11_a_kill_after_the_answer_was_taken_and_before_it_was_saved_does_not_lose_it(self):
        run, executor = self.waiting(seconds=4)
        looprun.answer(self.folder(run), "approve", "ship it")
        original = looprun._event

        def event(folder, **fields):
            if fields.get("outcome") == "approve":
                raise Kill()
            return original(folder, **fields)
        executor.hook = None
        with mock.patch.object(looprun, "_event", event), self.assertRaises(Kill):
            looprun.resume(self.folder(run), wait=True, executor=executor)
        self.assertEqual(looprun.resume(self.folder(run), wait=True, executor=executor), "passed")  # it was not asked again, and the time limit did not end it
        record = self.record(run)
        self.assertEqual([(a["answer"], a["note"]) for a in record["answers"]], [("approve", "ship it")])
        self.assertEqual(len(executor.calls), 2)
