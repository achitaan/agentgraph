"""The loop controller (agentgraph.looprun): acceptance tests A to D of the design and the "Also" list, on the scripted
executor with real check scripts in temp folders, plus unit tests for settings, compile, outcomes, messages, the record and
the admission rules. No test starts OpenCode."""
import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.detached import reap_detached
from agentgraph import looprun, osfs
from tests.shellskip import CHECK_SECOND, HAS_SH, sh_or, sleep_command
from tests.test_looprun3 import LockHolder


def tree_hash(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        reap_detached(self)
        self.structure = self.tmp / "structure"
        (self.structure / "30-build").mkdir(parents=True)
        (self.structure / "30-build" / "worker.md").write_text("Does the work.\n")
        (self.structure / "30-build" / "reviewer.md").write_text("Reviews.\n")
        (self.structure / "flows").mkdir()
        (self.structure / "flows" / "self.md").write_text("path: worker -> worker\n\nCheck its own work.\n")
        (self.structure / "flows" / "pair.md").write_text("path: worker -> reviewer -> worker\n\nA longer path.\n")
        (self.structure / "flows" / "open.md").write_text("path: worker -> reviewer\n\nNever returns to the worker.\n")
        self.project = self.tmp / "project"
        self.project.mkdir()

    def settings(self, check=CHECK_SECOND, **limits):
        out = {"version": 1, "flow": "self", "limits": {"attempts": 5, **limits}}
        if check is not None:
            out["check"] = {"command": check, "timeout": 20}
        return out

    def save(self, **kw):
        extra = {k: kw.pop(k) for k in ("session", "allow_asks", "goal") if k in kw}
        check = kw.pop("check", CHECK_SECOND)
        settings = {**self.settings(check, **kw), **extra}
        looprun.save_settings(self.structure, "self", settings)
        return settings

    def go(self, script=None, goal="Make the check pass.", executor=None, **kw):
        executor = executor or looprun.ScriptedExecutor(script or [{}] * 10)
        run = looprun.start(self.project, "self", structure=self.structure, goal=goal, executor=executor, wait=True, **kw)
        return run, looprun.read_run(looprun.run_dir(self.project, run)), executor

    def folder(self, run):
        return looprun.run_dir(self.project, run)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = looprun.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class AcceptanceTests(Base):
    def test_a_fail_then_pass_is_two_attempts_in_one_session(self):
        self.save()
        run, record, ex = self.go([{"sessions": 1, "cost": 0.1}, {"sessions": 1, "cost": 0.02}])
        self.assertEqual(record["status"], "passed")
        self.assertEqual(len(record["attempts"]), 2)
        self.assertEqual([a["check"]["exit"] for a in record["attempts"]], [1, 0])
        self.assertEqual([a["check"]["outcome"] for a in record["attempts"]], ["fail", "pass"])
        sessions = {a["agent"]["session"] for a in record["attempts"]}
        self.assertEqual(len(sessions), 1)
        self.assertTrue(re.fullmatch(r"ses_[A-Za-z0-9]+", sessions.pop()))
        self.assertEqual(record["sessions"], [record["attempts"][0]["agent"]["session"]])
        self.assertEqual(record["totals"]["sessions"], 1)
        self.assertAlmostEqual(record["totals"]["cost"], 0.12)
        folder = self.folder(run)
        for n, call in ((1, 1), (2, 2)):
            self.assertIn(f"check call {call}", (folder / f"attempt-{n}" / "check.out").read_text())
            self.assertTrue((folder / f"attempt-{n}" / "message.md").is_file())
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
        routes = [(e["step"], e["outcome"], e["route"]) for e in events if e["step"] == "check" and e["route"] != "end"]
        self.assertEqual(routes, [("check", "fail", "work"), ("check", "pass", "done")])
        self.assertEqual(ex.calls[0]["session"], ex.calls[1]["session"])
        self.assertTrue(ex.calls[0]["first"])
        self.assertFalse(ex.calls[1]["first"])

    def test_b_always_failing_check_never_gets_a_fourth_attempt(self):
        self.save(check="echo still wrong && exit 1", attempts=3)
        run, record, ex = self.go([{"text": "PASS: everything is done"}, {"text": "PASS"}, {"text": "I need more input from you"}, {"text": "PASS"}])
        self.assertEqual(record["status"], "failed")
        self.assertEqual(len(record["attempts"]), 3)
        self.assertEqual(len(ex.calls), 3)
        for n in (1, 2, 3):
            self.assertIn("still wrong", (self.folder(run) / f"attempt-{n}" / "check.out").read_text())
        self.assertFalse((self.folder(run) / "attempt-4").exists())
        self.assertIn("PASS", record["attempts"][0]["agent"]["reply"])

    def test_c_editing_the_saved_check_changes_only_the_next_run(self):
        self.save(check="exit 1", attempts=1)
        first, record1, _ = self.go()
        self.assertEqual(record1["status"], "failed")
        before = tree_hash(self.folder(first))
        self.save(check="echo new check; exit 0", attempts=1)
        second, record2, _ = self.go()
        self.assertEqual(record2["status"], "passed")
        self.assertIn("new check", (self.folder(second) / "attempt-1" / "check.out").read_text())
        self.assertEqual(tree_hash(self.folder(first)), before)
        self.assertEqual(json.loads((self.folder(first) / "definition.json").read_text())["settings"]["check"]["command"], "exit 1")
        self.assertEqual(json.loads((self.folder(second) / "definition.json").read_text())["settings"]["check"]["command"], "echo new check; exit 0")
        looprun.remove_settings(self.structure, "self")  # deleting the saved loop changes no run folder either
        self.assertEqual(tree_hash(self.folder(first)), before)

    def test_d_none_of_the_four_failures_is_passed(self):
        self.save(check=None)
        run, record, ex = self.go()
        self.assertEqual((record["status"], record["attempts"], ex.calls), ("invalid", [], []))
        self.assertIn("check", record["reason"])

        if HAS_SH:  # cmd.exe says "not recognized" with exit code 1, the same as any failing check: only sh gives a missing command the code 127
            self.save(check="definitely-not-a-command-xyz", attempts=2)
            run, record, _ = self.go()
            self.assertEqual(record["status"], "check_error")
            self.assertEqual(len(record["attempts"]), 1)
            self.assertEqual(record["attempts"][0]["check"]["exit"], 127)

        self.save(check="exit 0", attempts=1, attempt_seconds=0.3)
        started = time.time()
        run, record, _ = self.go([{"hang": True}], )
        self.assertEqual(record["attempts"][0]["agent"]["outcome"], "stalled")
        self.assertEqual(record["attempts"][0]["check"]["exit"], 0)  # the check still ran, and its pass is the only way to `passed`
        self.assertLess(time.time() - started, 10)

        self.save(check="exit 1", attempts=1, attempt_seconds=0.3)
        run, record, _ = self.go([{"hang": True}])
        self.assertEqual(record["status"], "stalled")

        self.save(check="exit 75", attempts=3)
        run, record, _ = self.go()
        self.assertEqual((record["status"], len(record["attempts"])), ("inconclusive", 1))
        looprun_settings = self.settings(sleep_command(5), attempts=3)
        looprun_settings["check"]["timeout"] = 0.3
        looprun.save_settings(self.structure, "self", looprun_settings)
        run, record, _ = self.go()
        self.assertEqual(record["status"], "inconclusive")
        self.assertEqual(record["attempts"][0]["check"]["outcome"], "inconclusive")
        self.assertIsNone(record["attempts"][0]["check"]["exit"])

    def test_d_stalled_attempt_with_a_passing_check_is_passed_only_by_the_check(self):
        self.save(check="exit 0", attempts=2, attempt_seconds=0.2)
        run, record, _ = self.go([{"hang": True}])
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["attempts"][0]["agent"]["outcome"], "stalled")


class AlsoTests(Base):
    def test_fresh_gives_a_new_session_per_attempt(self):
        self.save(session="fresh", attempts=3, check="exit 1")
        run, record, ex = self.go()
        ids = [a["agent"]["session"] for a in record["attempts"]]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(record["sessions"], ids)
        self.assertTrue(all(call["first"] for call in ex.calls))

    def test_spend_limit_refuses_the_next_attempt(self):
        self.save(check="exit 1", attempts=5, cost=1.5)
        run, record, ex = self.go([{"cost": 1.0}, {"cost": 1.0}, {"cost": 1.0}])
        self.assertEqual((record["status"], len(record["attempts"])), ("limit", 2))
        self.assertIn("cost", record["reason"])
        self.assertAlmostEqual(record["totals"]["cost"], 2.0)

    def test_unknown_cost_is_not_zero_and_with_a_cost_limit_ends_the_run(self):  # release 3 (R6) replaced "an unknown cost does not stop the run": the limit cannot be checked, so no further turn starts
        self.save(check="exit 1", attempts=3, cost=1.0)
        run, record, ex = self.go([{"cost": None}] * 3)
        self.assertEqual((record["status"], len(record["attempts"])), ("limit", 1))
        self.assertIn("unknown", record["reason"])
        self.assertIsNone(record["totals"]["cost"])
        self.assertIsNone(record["attempts"][0]["agent"]["cost"])

    def test_time_limit_refuses_the_next_attempt(self):
        self.save(check="exit 1", attempts=5, seconds=0.2)
        run, record, ex = self.go([{"sleep": 0.3}, {"sleep": 0.3}])
        self.assertEqual((record["status"], len(record["attempts"])), ("limit", 1))
        self.assertIn("seconds", record["reason"])

    def test_cancel_between_attempts(self):
        self.save(check="exit 1", attempts=5)
        folder = {}
        executor = looprun.ScriptedExecutor([{}, {}, {}], hook=lambda n, call: n == 2 and looprun.request_cancel(
            looprun.run_dir(self.project, next(iter(os.listdir(self.project / ".opencode" / "loops"))))))
        run, record, _ = self.go(executor=executor)
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(len(record["attempts"]), 2)  # attempt 2 had its turn; attempt 3 was refused
        self.assertEqual(record["attempts"][1]["agent"]["outcome"], "returned")

    def test_cancel_during_an_attempt_stops_the_agent(self):
        self.save(check="exit 0", attempts=5)
        executor = looprun.ScriptedExecutor([{"hang": True}])
        run_id = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=executor)
        self.assertEqual(looprun.read_run(self.folder(run_id))["status"], "queued")
        timer = threading.Timer(0.4, looprun.request_cancel, [self.folder(run_id)])
        timer.start()
        started = time.time()
        looprun.execute(self.folder(run_id), executor)
        timer.join()
        record = looprun.read_run(self.folder(run_id))
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["attempts"][0]["agent"]["outcome"], "cancelled")
        self.assertLess(time.time() - started, 10)
        self.assertIsNone(record["attempts"][0]["check"])

    def test_a_second_process_cannot_run_the_same_folder(self):
        self.save()
        with mock.patch.object(looprun.subprocess, "Popen"):  # no real controller: this test plays the one that holds the run
            run_id = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([{}]))
            other = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([{}]))
        folder = self.folder(run_id)
        with LockHolder(folder / "lock"):  # another process (a controller) holds the run
            with mock.patch.object(looprun, "LOCK_WAIT", 0.1), self.assertRaises(looprun.LoopError) as caught:
                looprun.execute(folder, looprun.ScriptedExecutor([{}]))
            self.assertIn("already", str(caught.exception))
            self.assertEqual(looprun.read_run(folder)["status"], "queued")  # the library call writes nothing
            with mock.patch.object(looprun, "LOCK_WAIT", 0.1):
                code, _, err = self.cli("run", "--run-dir", str(folder))
            self.assertEqual(code, 1)
            self.assertIn("already", err)
        self.assertEqual(looprun.read_run(folder)["status"], "queued")  # v8b 1: a held lock means a controller exists and owns the record, so the command writes nothing
        folder = self.folder(other)
        looprun.execute(folder, looprun.ScriptedExecutor([{}, {}]))
        self.assertEqual(looprun.read_run(folder)["status"], "passed")
        with self.assertRaises(looprun.LoopError):  # a finished run is never run again
            looprun.execute(folder, looprun.ScriptedExecutor([{}]))

    def test_detached_run_finishes_through_the_module_entry(self):
        self.save()
        spec = {"kind": "scripted", "attempts": [{"cost": 0.5}, {"cost": 0.25}]}
        run_id = looprun.start(self.project, "self", structure=self.structure, goal="g", executor_spec=spec)
        folder = self.folder(run_id)
        deadline = time.time() + 30
        while time.time() < deadline and looprun.read_run(folder)["status"] in ("queued", "running"):
            time.sleep(0.1)
        record = looprun.read_run(folder)
        self.assertEqual(record["status"], "passed", (folder / "run.log").read_text() if (folder / "run.log").exists() else "")
        self.assertEqual(len(record["attempts"]), 2)
        self.assertNotEqual(record["pid"], os.getpid())

    def test_agent_error_stops_the_run(self):
        self.save(check="exit 0", attempts=3)
        run, record, ex = self.go([{"error": "provider unavailable"}])
        self.assertEqual((record["status"], len(record["attempts"])), ("agent_error", 1))
        self.assertIsNone(record["attempts"][0]["check"])

    def test_unknown_agent_and_missing_goal_are_invalid(self):
        (self.structure / "flows" / "ghost.md").write_text("path: ghost -> ghost\n\nx\n")
        looprun.save_settings(self.structure, "ghost", {**self.settings(), "flow": "ghost"})
        with self.assertRaises(looprun.LoopError):  # a flow whose agent is not in the structure is not loaded at all
            looprun.start(self.project, "ghost", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]), wait=True)
        self.save()
        run, record, ex = self.go(goal=None)
        self.assertEqual((record["status"], ex.calls), ("invalid", []))
        self.assertIn("goal", record["reason"])


class SettingsTests(Base):
    def good(self):
        return {"version": 1, "flow": "self", "goal": "g", "check": {"command": "true", "timeout": 5, "inconclusive_codes": [75]},
                "session": "reuse", "limits": {"attempts": 3, "attempt_seconds": 60, "seconds": 600, "cost": 1.5}, "allow_asks": True}

    def test_valid_settings_round_trip_through_a_file(self):
        out = looprun.validate(self.good(), Path("x.json"))
        self.assertEqual(out["limits"]["attempts"], 3)
        looprun.save_settings(self.structure, "self", self.good())
        path = looprun.settings_path(self.structure, "self")
        self.assertEqual(path, self.structure / ".loops" / "self.json")
        self.assertEqual(looprun.load_settings(self.structure, "self"), out)
        self.assertEqual([p.name for p in path.parent.iterdir() if not p.name.startswith(".lock")], ["self.json"])
        self.assertTrue(looprun.remove_settings(self.structure, "self"))
        self.assertFalse(looprun.remove_settings(self.structure, "self"))
        self.assertIsNone(looprun.load_settings(self.structure, "self"))

    def test_unknown_keys_are_refused_at_every_level(self):
        for mutate in (lambda s: s.update(chek=1), lambda s: s["check"].update(retries=2), lambda s: s["limits"].update(tokens=5)):
            s = self.good()
            mutate(s)
            with self.assertRaises(looprun.LoopError) as caught:
                looprun.validate(s, Path("p.json"))
            self.assertIn("p.json", str(caught.exception))

    def test_attempts_are_required_between_1_and_50(self):
        for bad in (None, 0, 51, -1, 2.5, True, "3"):
            s = self.good()
            if bad is None:
                del s["limits"]["attempts"]
            else:
                s["limits"]["attempts"] = bad
            with self.assertRaises(looprun.LoopError, msg=repr(bad)):
                looprun.validate(s, Path("p.json"))
        s = self.good()
        del s["limits"]
        with self.assertRaises(looprun.LoopError):
            looprun.validate(s, Path("p.json"))
        for ok in (1, 50):
            s = self.good()
            s["limits"]["attempts"] = ok
            self.assertEqual(looprun.validate(s, Path("p.json"))["limits"]["attempts"], ok)

    def test_other_fields_are_checked(self):
        cases = [("session", "both"), ("allow_asks", "yes"), ("version", 2)]
        for key, value in cases:
            s = self.good()
            s[key] = value
            with self.assertRaises(looprun.LoopError, msg=key):
                looprun.validate(s, Path("p.json"))
        for path, value in ((("limits", "cost"), -1), (("limits", "seconds"), 0), (("limits", "attempt_seconds"), True), (("check", "command"), "  "),
                            (("check", "timeout"), 0), (("check", "inconclusive_codes"), [0]), (("check", "inconclusive_codes"), ["75"])):
            s = self.good()
            s[path[0]][path[1]] = value
            with self.assertRaises(looprun.LoopError, msg=str(path)):
                looprun.validate(s, Path("p.json"))
        s = self.good()
        s["flow"] = "../x"
        with self.assertRaises(looprun.LoopError):
            looprun.validate(s, Path("p.json"))
        with self.assertRaises(looprun.LoopError):
            looprun.settings_path(self.structure, "../escape")

    def test_defaults_and_optional_parts(self):
        out = looprun.validate({"version": 1, "flow": "self", "limits": {"attempts": 2}}, Path("p.json"))
        self.assertNotIn("check", out)
        self.assertEqual((out["session"], out["allow_asks"]), ("reuse", False))
        out = looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2}}, Path("p.json"))
        self.assertEqual((out["check"]["timeout"], out["check"]["inconclusive_codes"]), (300, [75]))

    def test_an_unreadable_settings_file_is_refused_with_its_path(self):
        looprun.save_settings(self.structure, "self", self.good())
        looprun.settings_path(self.structure, "self").write_text("{not json")
        with self.assertRaises(looprun.LoopError) as caught:
            looprun.load_settings(self.structure, "self")
        self.assertIn("self.json", str(caught.exception))


class CompileTests(Base):
    def test_a_self_loop_compiles_to_work_and_check(self):
        settings = looprun.validate({"version": 1, "flow": "self", "check": {"command": "sh c.sh", "timeout": 9, "inconclusive_codes": [75]},
                                     "session": "fresh", "limits": {"attempts": 2}}, Path("p.json"))
        steps = looprun.compile(["worker", "worker"], settings)
        self.assertEqual([s["id"] for s in steps], ["work", "check"])
        work, check = steps
        self.assertEqual((work["kind"], work["agent"], work["session"]), ("agent", "worker", "fresh"))
        self.assertEqual(work["on"]["returned"], "check")
        self.assertEqual(work["on"]["agent_error"], "stop")
        self.assertEqual((check["kind"], check["command"], check["timeout"]), ("check", "sh c.sh", 9))
        self.assertEqual(check["on"], {"pass": "done", "fail": "work", "inconclusive": "stop", "check_error": "stop"})
        json.dumps(steps)

    def test_other_paths_are_refused_for_now(self):  # release 2: a path that returns to its first agent runs (tests/test_looprun2.py); the rest is refused
        settings = looprun.validate(self.settings(), Path("p.json"))
        for path in (["worker", "reviewer"], ["worker"], []):
            with self.assertRaises(looprun.LoopError) as caught:
                looprun.compile(path, settings)
            self.assertIn("does not loop back to its first agent", str(caught.exception))
        self.assertEqual([s["id"] for s in looprun.compile(["worker", "reviewer", "worker"], settings)], ["work", "check", "judge:reviewer"])

    def test_compile_needs_a_check_command(self):
        with self.assertRaises(looprun.LoopError):
            looprun.compile(["worker", "worker"], looprun.validate(self.settings(check=None), Path("p.json")))

    def test_start_refuses_a_multi_agent_flow_without_making_a_run(self):
        looprun.save_settings(self.structure, "pair", {**self.settings(), "flow": "pair"})
        with self.assertRaises(looprun.LoopError) as caught:
            looprun.start(self.project, "open", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]), wait=True)
        self.assertIn("does not loop back", str(caught.exception))
        self.assertFalse((self.project / ".opencode" / "loops").exists())

    def test_the_definition_holds_the_steps_and_their_hash(self):
        self.save()
        run, record, _ = self.go()
        definition = json.loads((self.folder(run) / "definition.json").read_text())
        steps = definition["steps"]
        self.assertEqual([s["id"] for s in steps], ["work", "check"])
        self.assertEqual(definition["steps_sha256"], hashlib.sha256(json.dumps(steps, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        self.assertEqual(definition["path"], ["worker", "worker"])
        self.assertEqual(definition["goal"], "Make the check pass.")


class OutcomeTests(unittest.TestCase):
    def test_check_outcome_comes_from_the_exit_code_only(self):
        f = looprun.check_outcome
        self.assertEqual(f(0, False, [75]), "pass")
        self.assertEqual(f(1, False, [75]), "fail")
        self.assertEqual(f(2, False, [75]), "fail")
        self.assertEqual(f(75, False, [75]), "inconclusive")
        self.assertEqual(f(None, True, [75]), "inconclusive")
        self.assertEqual(f(126, False, [75]), "check_error")
        self.assertEqual(f(127, False, [75]), "check_error")
        self.assertEqual(f(-9, False, [75]), "check_error")
        self.assertEqual(f(None, False, [75]), "check_error")
        self.assertEqual(f(1, False, [1]), "inconclusive")

    def test_only_a_zero_exit_can_pass(self):
        for code in list(range(1, 256)) + [-1, -15, None]:
            self.assertNotEqual(looprun.check_outcome(code, False, []), "pass")
            self.assertNotEqual(looprun.check_outcome(code, True, []), "pass")
        self.assertEqual(looprun.check_outcome(0, False, [0, 75]), "pass")

    def test_final_status_of_a_stop(self):
        self.assertEqual(looprun.status_of("stalled"), "stalled")
        self.assertEqual(looprun.status_of("agent_error"), "agent_error")
        self.assertEqual(looprun.status_of("check_error"), "check_error")
        self.assertEqual(looprun.status_of("inconclusive"), "inconclusive")
        self.assertEqual(looprun.status_of("cancelled"), "cancelled")
        self.assertEqual(looprun.status_of("fail"), "failed")
        self.assertNotEqual(looprun.status_of("anything else"), "passed")
        self.assertNotEqual(looprun.status_of("pass"), "passed")


class MessageTests(unittest.TestCase):
    GOAL = "Create answer.txt containing 42."

    def test_every_message_holds_the_goal_and_the_attempt_number(self):
        first = looprun.build_message(self.GOAL, "sh check.sh", 1, 3, None, None)
        self.assertIn(self.GOAL, first)
        self.assertIn("sh check.sh", first)
        self.assertIn("attempt 1 of 3", first.lower())
        self.assertIn("the controller runs the check", first.lower())
        later = looprun.build_message(self.GOAL, "sh check.sh", 2, 3, {"exit": 1, "outcome": "fail", "tail": "line a\nline b"}, None)
        self.assertIn(self.GOAL, later)
        self.assertIn("attempt 2 of 3", later.lower())
        self.assertIn("exit code 1", later.lower())
        self.assertIn("line b", later)
        self.assertNotIn("stopped", later.lower())

    def test_after_a_kill_the_message_says_so(self):
        text = looprun.build_message(self.GOAL, "c", 2, 3, {"exit": 1, "outcome": "fail", "tail": "x"}, "stalled", 900)
        self.assertIn("stopped", text.lower())
        self.assertIn("900", text)
        self.assertIn(self.GOAL, text)

    def test_the_tail_is_the_last_60_lines_and_bounded(self):
        output = "\n".join(f"row {i}" for i in range(200)) + "\n"
        tail = looprun.tail_of(output)
        self.assertEqual(tail.splitlines()[0], "row 140")
        self.assertEqual(tail.splitlines()[-1], "row 199")
        self.assertLessEqual(len(looprun.tail_of("x" * 100000)), looprun.TAIL_CHARS + 50)


class AdmissionTests(unittest.TestCase):
    def test_each_limit_refuses_with_its_own_status(self):
        f = looprun.admit
        limits = {"attempts": 3, "seconds": 100, "cost": 1.0}
        self.assertIsNone(f(0, limits, 0, 0.0, False))
        self.assertIsNone(f(2, limits, 99, 0.99, False))
        self.assertEqual(f(3, limits, 0, 0.0, False)[0], "failed")
        self.assertEqual(f(1, limits, 100, 0.0, False)[0], "limit")
        self.assertIn("seconds", f(1, limits, 100, 0.0, False)[1])
        self.assertEqual(f(1, limits, 0, 1.0, False)[0], "limit")
        self.assertIn("cost", f(1, limits, 0, 1.0, False)[1])
        self.assertEqual(f(1, limits, 0, 0.0, True)[0], "cancelled")
        self.assertEqual(f(9, limits, 999, 9.0, True)[0], "cancelled")  # a cancel request comes first
        self.assertIsNone(f(40, {"attempts": 50}, 10 ** 6, 10 ** 6, False))  # absent limits mean no such limit
        self.assertIsNone(f(1, {"attempts": 3, "cost": 1.0}, 0, None, False))  # unknown cost is not counted


class RecordTests(Base):
    def test_a_finished_run_is_byte_identical_after_another_run(self):
        self.save(attempts=2)
        first, _, _ = self.go([{"cost": 0.1}, {"cost": 0.2}])
        before = tree_hash(self.folder(first))
        self.assertIn("definition.json", before)
        self.assertIn("run.json", before)
        self.assertIn("events.jsonl", before)
        (self.project / ".count").unlink()
        second, record, _ = self.go([{}, {}])
        self.assertNotEqual(first, second)
        self.assertEqual(record["status"], "passed")
        self.assertEqual(tree_hash(self.folder(first)), before)
        self.assertEqual(len(list((self.project / ".opencode" / "loops").iterdir())), 2)

    def test_the_run_record_holds_what_the_design_lists(self):
        self.save()
        run, record, _ = self.go([{"cost": 0.1, "tokens": {"input": 5}, "sessions": 1}, {}])
        self.assertTrue(re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", run))
        for key in ("status", "reason", "goal", "workdir", "pid", "heartbeat", "sessions", "attempts", "totals", "flow", "run"):
            self.assertIn(key, record)
        attempt = record["attempts"][0]
        for key in ("n", "steps", "started", "ended", "agent", "check", "message", "feedback"):
            self.assertIn(key, attempt)
        for key in ("session", "exit", "seconds", "cost", "tokens", "outcome"):
            self.assertIn(key, attempt["agent"])
        for key in ("exit", "outcome", "seconds", "output", "output_sha256"):
            self.assertIn(key, attempt["check"])
        out = (self.folder(run) / attempt["check"]["output"]).read_bytes()
        self.assertEqual(hashlib.sha256(out).hexdigest(), attempt["check"]["output_sha256"])
        self.assertEqual(attempt["feedback"], "attempt-2/message.md")
        self.assertEqual(record["totals"]["attempts"], 2)
        self.assertEqual([s["step"] for s in attempt["steps"]], ["work", "check"])

    def test_the_message_of_attempt_2_carries_the_evidence_and_the_goal(self):
        self.save()
        run, _, ex = self.go([{}, {}], goal="Create answer.txt")
        first = (self.folder(run) / "attempt-1" / "message.md").read_text()
        second = (self.folder(run) / "attempt-2" / "message.md").read_text()
        self.assertIn("Create answer.txt", first)
        self.assertIn("Create answer.txt", second)
        self.assertIn("check call 1", second)
        self.assertEqual(ex.calls[1]["message"], second)

    def test_check_output_is_capped_at_one_megabyte(self):
        self.save(check=sh_or("head -c 3000000 /dev/zero | tr '\\0' x; echo; exit 1", "import sys; sys.stdout.write('x' * 3000000); print(); raise SystemExit(1)"), attempts=1)
        run, record, _ = self.go()
        size = (self.folder(run) / "attempt-1" / "check.out").stat().st_size
        self.assertLessEqual(size, looprun.OUTPUT_CAP + 200)
        self.assertGreater(size, 1000000)

    def test_no_status_other_than_a_check_pass_yields_passed(self):
        cases = [("exit 1", [{}]), ("exit 75", [{}]), ("nonexistent-zzz", [{}]), ("exit 0", [{"error": "boom"}]), ("exit 1", [{"hang": True}] * 3),
                 (sleep_command(5), [{}])]
        for check, script in cases:
            settings = self.settings(check, attempts=2, attempt_seconds=0.2)
            settings["check"]["timeout"] = 0.3
            looprun.save_settings(self.structure, "self", settings)
            run, record, _ = self.go(script)
            self.assertNotEqual(record["status"], "passed", check)
        looprun.save_settings(self.structure, "self", self.settings("exit 0", attempts=2))
        run, record, _ = self.go([{"text": "FAIL, I give up"}])
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["attempts"][-1]["check"]["exit"], 0)

    def test_a_route_to_done_without_a_passing_check_is_refused(self):
        self.save(check="exit 1", attempts=2)
        run = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([{}]))
        folder = self.folder(run)
        definition = json.loads((folder / "definition.json").read_text())
        definition["steps"][0]["on"]["returned"] = "done"  # a hand-edited definition (re-pinned: the walk's own guard is what is tested here)
        definition["steps_sha256"] = looprun.steps_hash(definition["steps"])
        (folder / "definition.json").write_text(json.dumps(definition))
        record = json.loads((folder / "run.json").read_text())
        record["steps_sha256"] = definition["steps_sha256"]  # a deliberate edit of both files: the pins do not stop it, the walk's guard does
        (folder / "run.json").write_text(json.dumps(record))
        looprun.execute(folder, looprun.ScriptedExecutor([{}]))
        self.assertNotEqual(looprun.read_run(folder)["status"], "passed")

    def test_session_id_is_in_the_record_before_the_agent_starts(self):
        self.save()
        seen = {}

        def hook(n, call):
            seen[n] = looprun.read_run(self.folder(os.listdir(self.project / ".opencode" / "loops")[0]))["sessions"]
        run, record, _ = self.go(executor=looprun.ScriptedExecutor([{}, {}], hook=hook))
        self.assertEqual(seen[1], record["sessions"])
        self.assertEqual(len(seen[1]), 1)

    def test_the_work_folder_is_where_the_agent_and_the_check_run(self):
        work = self.tmp / "elsewhere"
        work.mkdir()
        self.save(check=sh_or("test -f marker && exit 0 || exit 1", "import os, sys; sys.exit(0 if os.path.exists('marker') else 1)"), attempts=1)
        (work / "marker").write_text("x")
        run, record, ex = self.go(workdir=work)
        self.assertEqual(record["status"], "passed")
        self.assertEqual(Path(ex.calls[0]["cwd"]), work)
        self.assertEqual(record["workdir"], str(work))


class RunCheckTests(unittest.TestCase):
    def test_the_job_is_let_go_of_when_something_raises_after_it_adopted_the_check(self):
        class Job:
            closed = 0

            def adopt(self, proc):
                raise RuntimeError("adopt failed")

            def terminate(self):
                return False

            def close(self):
                Job.closed += 1

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(looprun.osproc, "Job", Job):
            with self.assertRaises(RuntimeError):
                looprun.run_check("exit 0", 5, tmp, dict(os.environ), Path(tmp) / "out.txt")
        self.assertEqual(Job.closed, 1)

    def test_a_check_still_running_is_stopped_when_something_raises_after_it_adopted_it(self):
        made = []
        real_popen = looprun.subprocess.Popen

        def popen(*args, **kwargs):
            made.append(real_popen(*args, **kwargs))
            return made[-1]

        class Job:
            def adopt(self, proc):
                raise RuntimeError("adopt failed")

            def terminate(self):
                return False  # no job object to end it with: the plain stop has to do it

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(looprun.osproc, "Job", Job), mock.patch.object(looprun.subprocess, "Popen", popen):
            started = time.monotonic()
            try:
                with self.assertRaises(RuntimeError):
                    looprun.run_check(sleep_command(60), 120, tmp, dict(os.environ), Path(tmp) / "out.txt")
                self.assertIsNotNone(made[0].poll(), "the check was left running")
                self.assertLess(time.monotonic() - started, 30)
            finally:
                for proc in made:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                    if proc.stdout:
                        proc.stdout.close()


class CommandTests(Base):
    def test_set_show_unset(self):
        code, out, err = self.cli("set", "self", "--check", "sh check.sh", "--attempts", "4", "--cost", "2", "--structure", str(self.structure))
        self.assertEqual((code, err), (0, ""), out)
        saved = json.loads(looprun.settings_path(self.structure, "self").read_text())
        self.assertEqual((saved["check"]["command"], saved["limits"]["attempts"], saved["limits"]["cost"]), ("sh check.sh", 4, 2))
        code, out, _ = self.cli("set", "self", "--attempts", "6", "--structure", str(self.structure))  # a change keeps the rest
        self.assertEqual(code, 0)
        self.assertEqual(looprun.load_settings(self.structure, "self")["check"]["command"], "sh check.sh")
        code, out, _ = self.cli("show", "self", "--structure", str(self.structure))
        self.assertEqual(code, 0)
        self.assertIn("sh check.sh", out)
        self.assertEqual(len(out.strip().splitlines()), 1)
        code, out, _ = self.cli("show", "self", "--json", "--structure", str(self.structure))
        self.assertEqual(json.loads(out)["limits"]["attempts"], 6)
        self.assertEqual(self.cli("unset", "self", "--structure", str(self.structure))[0], 0)
        self.assertEqual(self.cli("show", "self", "--structure", str(self.structure))[0], 1)
        self.assertEqual(self.cli("unset", "self", "--structure", str(self.structure))[0], 1)

    def test_set_refuses_bad_values_and_other_paths(self):
        self.assertEqual(self.cli("set", "self", "--check", "x", "--attempts", "0", "--structure", str(self.structure))[0], 2)
        self.assertEqual(self.cli("set", "self", "--check", "x", "--structure", str(self.structure))[0], 2)  # attempts are required
        code, _, err = self.cli("set", "open", "--check", "x", "--attempts", "2", "--structure", str(self.structure))
        self.assertEqual(code, 2)
        self.assertIn("does not loop back", err)
        self.assertEqual(self.cli("set", "nothere", "--check", "x", "--attempts", "2", "--structure", str(self.structure))[0], 2)
        self.assertFalse((self.structure / ".loops").exists())

    def test_start_wait_status_list_cancel(self):
        self.cli("set", "self", "--check", "exit 1", "--attempts", "2", "--structure", str(self.structure))
        goal = self.tmp / "goal.md"
        goal.write_text("Do the thing.\n")
        # the CLI never builds a fake executor; the real one needs a CLI file, so a missing one is a refusal, not a crash
        code, out, err = self.cli("start", "self", "--goal-file", str(goal), "--structure", str(self.structure), "--project", str(self.project),
                                "--wait", "--executable", str(self.tmp / "no-such-opencode"))
        self.assertEqual(code, 1)
        self.assertIn("invalid", out)
        runs = sorted((self.project / ".opencode" / "loops").iterdir())
        self.assertEqual(len(runs), 1)
        record = looprun.read_run(runs[0])
        self.assertEqual(record["status"], "invalid")
        self.assertEqual(record["attempts"], [])
        code, out, _ = self.cli("status", runs[0].name, "--project", str(self.project))
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("invalid", out)
        code, out, _ = self.cli("status", runs[0].name, "--json", "--project", str(self.project))
        self.assertEqual(json.loads(out)["status"], "invalid")
        code, out, _ = self.cli("status", "--project", str(self.project))  # no id: the newest run
        self.assertIn(runs[0].name, out)
        code, out, _ = self.cli("list", "--project", str(self.project))
        self.assertEqual((code, len(out.strip().splitlines())), (0, 1))
        self.assertEqual(self.cli("cancel", runs[0].name, "--project", str(self.project))[0], 1)  # already over
        self.assertEqual(self.cli("cancel", "nope", "--project", str(self.project))[0], 2)
        self.assertEqual(self.cli("status", "20990101-000000-abcd", "--project", str(self.project))[0], 1)

    def test_cancel_marks_a_live_run_with_a_file(self):
        self.save()
        with mock.patch.object(looprun.subprocess, "Popen"):  # no real controller: this test plays it, holding the run's lock
            run = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]))
        with open(self.folder(run) / "lock", "a+") as held:
            osfs.lock(held.fileno(), block=False)
            code, out, _ = self.cli("cancel", run, "--project", str(self.project))
        self.assertEqual(code, 0)
        self.assertTrue((self.folder(run) / "cancel").exists())
        looprun.execute(self.folder(run), looprun.ScriptedExecutor([{}]))
        record = looprun.read_run(self.folder(run))
        self.assertEqual((record["status"], record["attempts"]), ("cancelled", []))

    def test_liveness_of_a_run_that_nobody_holds(self):
        self.save()
        run = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]))
        folder = self.folder(run)
        state = looprun.read_run(folder)
        state["status"] = "running"
        (folder / "run.json").write_text(json.dumps(state))
        self.assertEqual(looprun.shown_status(folder, looprun.read_run(folder)), "interrupted")


if __name__ == "__main__":
    unittest.main()
