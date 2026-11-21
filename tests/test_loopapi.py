"""The page API of a saved loop (agentgraph.loopapi): listing, run detail, save/remove with `rev`, start, cancel, on temp folders and
the scripted executor. No test starts OpenCode; runs are started with `wait` semantics through the controller or written by hand."""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agentgraph import loopapi, looprun

CHECK_SECOND = 'n=$(cat .count 2>/dev/null || echo 0); n=$((n+1)); echo $n > .count; echo "check call $n"; [ "$n" -ge 2 ]'
GOOD = {"check": {"command": "true"}, "limits": {"attempts": 3}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.structure = self.tmp / "structure"
        (self.structure / "30-build").mkdir(parents=True)
        (self.structure / "30-build" / "worker.md").write_text("Does the work.\n")
        (self.structure / "30-build" / "reviewer.md").write_text("Reviews.\n")
        (self.structure / "flows").mkdir()
        (self.structure / "flows" / "self.md").write_text("path: worker -> worker\n\nCheck its own work.\n")
        (self.structure / "flows" / "pair.md").write_text("path: worker -> reviewer -> worker\n\nA longer path.\n")
        self.project = self.tmp / "project"
        self.project.mkdir()

    def entry(self, flow="self"):
        return next(item for item in loopapi.listing(self.structure, self.project, None)["loops"] if item["flow"] == flow)

    def finished(self, **kw):
        looprun.save_settings(self.structure, "self", {"version": 1, "flow": "self", "limits": {"attempts": 5},
                                                       "check": {"command": CHECK_SECOND, "timeout": 20}})
        run = looprun.start(self.project, "self", structure=self.structure, goal="Make it pass.", wait=True,
                            executor=looprun.ScriptedExecutor([{}, {}]), **kw)
        return run


class ListingTests(Base):
    def test_shape_of_a_self_loop_a_longer_flow_and_nothing_saved(self):
        doc = loopapi.listing(self.structure, self.project, None)
        self.assertEqual(set(doc), {"project", "scratch", "loops", "orphans", "runs"})
        self.assertEqual((doc["project"], doc["scratch"], doc["orphans"], doc["runs"]), (str(self.project), False, [], []))
        self.assertEqual([item["flow"] for item in doc["loops"]], ["pair", "self"])  # the order of the structure's flows
        pair, own = doc["loops"]
        self.assertEqual(set(own), {"flow", "name", "path", "agent", "runnable", "reason", "settings", "problem", "rev", "versions"})  # release 3: the kept earlier versions
        self.assertEqual((own["path"], own["agent"], own["runnable"], own["reason"], own["settings"], own["problem"], own["rev"]),
                         (["worker", "worker"], "worker", True, "", None, "", ""))
        self.assertEqual((pair["runnable"], pair["reason"], pair["settings"], pair["agent"]), (True, "", None, "worker"))  # release 2: a path that loops back runs
        (self.structure / "flows" / "open.md").write_text("path: worker -> reviewer\n\nNever returns to the worker.\n")
        open_flow = self.entry("open")
        self.assertEqual((open_flow["runnable"], open_flow["reason"]), (False, looprun.LOOP_BACK))

    def test_settings_rev_orphan_and_a_broken_file(self):
        looprun.save_settings(self.structure, "self", {"version": 1, "flow": "self", **GOOD})
        looprun.save_settings(self.structure, "old-flow", {"version": 1, "flow": "old-flow", **GOOD})
        looprun.settings_path(self.structure, "pair").write_text("{not json")
        doc = loopapi.listing(self.structure, self.project, "/scratch")
        self.assertTrue(doc["scratch"])
        self.assertEqual(doc["orphans"], ["old-flow"])
        pair, own = doc["loops"]
        raw = looprun.settings_path(self.structure, "self").read_bytes()
        self.assertEqual(own["rev"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(own["settings"], looprun.load_settings(self.structure, "self"))
        self.assertEqual((own["settings"]["check"]["timeout"], own["settings"]["check"]["inconclusive_codes"]), (300, [75]))
        self.assertEqual(own["problem"], "")
        self.assertIsNone(pair["settings"])
        self.assertIn("not valid JSON", pair["problem"])
        self.assertEqual(pair["rev"], hashlib.sha256(b"{not json").hexdigest())

    def test_a_pair_runs_and_its_summary_says_who_passed_it(self):
        looprun.save_settings(self.structure, "pair", {"version": 1, "flow": "pair", "limits": {"attempts": 2}})
        script = {"worker": [{"text": "done"}], "reviewer": [{"text": "Looks right.\nVERDICT: PASS"}]}
        run = looprun.start(self.project, "pair", structure=self.structure, goal="g", wait=True, executor=looprun.ScriptedExecutor(script))
        row, = loopapi.listing(self.structure, self.project, None)["runs"]
        self.assertEqual((row["run"], row["status"], row["passed_by"], row["waiting"]), (run, "passed", "judges", None))

    def test_runs_are_listed_newest_first_with_the_shown_status(self):
        run = self.finished()
        doc = loopapi.listing(self.structure, self.project, None)
        row, = doc["runs"]
        self.assertEqual((row["run"], row["flow"], row["status"], row["live"], row["attempts"], row["limit"]), (run, "self", "passed", False, 2, 5))
        self.assertEqual(row["last_check"], {"outcome": "pass", "exit": 0})
        self.assertEqual((row["check"], row["goal"], row["reason"]), (CHECK_SECOND, "Make it pass.", "the check exited 0"))
        self.assertIsNone(row["cost"])  # unknown is never 0
        self.assertEqual((row["passed_by"], row["waiting"]), ("check", None))
        json.dumps(doc, allow_nan=False)
        folder = looprun.run_dir(self.project, run)  # a run that says running with no controller is shown interrupted
        state = looprun.read_run(folder)
        (folder / "run.json").write_text(json.dumps({**state, "status": "running"}))
        self.assertEqual(loopapi.listing(self.structure, self.project, None)["runs"][0]["status"], "interrupted")

    def test_no_structure_folder_lists_nothing(self):
        doc = loopapi.listing(self.tmp / "nowhere", self.project, None)
        self.assertEqual((doc["loops"], doc["orphans"], doc["runs"]), ([], [], []))


class RunDetailTests(Base):
    def test_two_attempts_with_message_and_check_output(self):
        run = self.finished()
        doc = loopapi.run_detail(self.project, run)
        self.assertEqual((doc["run"]["status"], doc["run"]["live"], doc["run"]["run"]), ("passed", False, run))
        first, second = doc["attempts"]
        self.assertEqual((first["n"], second["n"]), (1, 2))
        self.assertIn("check call 1", first["check_output"])
        self.assertIn("check call 2", second["check_output"])
        self.assertIn("Make it pass.", first["message"])
        self.assertIn("check call 1", second["message"])  # what the agent was sent after the failed check
        self.assertFalse(first["check_output_truncated"])
        self.assertEqual(set(doc["definition"]), {"settings", "path", "steps", "steps_sha256", "goal", "workdir", "created"})
        self.assertEqual(doc["definition"]["path"], ["worker", "worker"])
        self.assertEqual([a["check"]["outcome"] for a in doc["run"]["attempts"]], ["fail", "pass"])

    def test_check_output_is_the_last_twenty_thousand_characters(self):
        run = self.finished()
        out = looprun.run_dir(self.project, run) / "attempt-1" / "check.out"
        out.write_text("x" * 5000 + "y" * loopapi.OUTPUT_CHARS, encoding="utf-8")
        first = loopapi.run_detail(self.project, run)["attempts"][0]
        self.assertEqual(first["check_output"], "y" * loopapi.OUTPUT_CHARS)
        self.assertTrue(first["check_output_truncated"])

    def test_a_bad_id_is_400_an_unknown_run_404_and_nothing_escapes(self):
        for bad in ("../x", "..", "", "20261121-043755-90AZ", "20261121-043755-90a7/..", None, 7):
            with self.subTest(bad=bad), self.assertRaises(loopapi.ApiError) as caught:
                loopapi.run_detail(self.project, bad)
            self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(loopapi.ApiError) as caught:
            loopapi.run_detail(self.project, "20261121-043755-90a7")
        self.assertEqual(caught.exception.status, 404)
        run = self.finished()
        (looprun.run_dir(self.project, run) / "run.json").write_text("{nope")
        with self.assertRaises(loopapi.ApiError) as caught:
            loopapi.run_detail(self.project, run)
        self.assertEqual(caught.exception.status, 404)


class SaveRemoveTests(Base):
    def save(self, settings=None, rev="", flow="self"):
        return loopapi.save(self.structure, flow, GOOD if settings is None else settings, rev)

    def refused(self, call, status):
        with self.assertRaises(loopapi.ApiError) as caught:
            call()
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_round_trip_with_before(self):
        first = self.save()
        self.assertEqual((first["before"], first["settings"]["flow"], first["settings"]["version"]), (None, "self", 1))
        self.assertEqual(first["rev"], self.entry()["rev"])
        self.assertEqual(looprun.load_settings(self.structure, "self"), first["settings"])
        second = self.save({**GOOD, "goal": "Do it.", "limits": {"attempts": 4}}, first["rev"])
        self.assertEqual(second["before"], first["settings"])  # what Undo saves back
        self.assertNotEqual(second["rev"], first["rev"])
        undone = self.save(second["before"], second["rev"])
        self.assertEqual(undone["settings"], first["settings"])
        self.assertEqual(undone["rev"], first["rev"])  # the same bytes as before the edit

    def test_a_stale_rev_is_409_with_the_current_rev_and_changes_nothing(self):
        first = self.save()
        error = self.refused(lambda: self.save({**GOOD, "goal": "other"}, ""), 409)
        self.assertEqual(error.body()["rev"], first["rev"])
        self.refused(lambda: self.save({**GOOD, "goal": "other"}, "0" * 64), 409)
        self.assertEqual(looprun.load_settings(self.structure, "self"), first["settings"])
        self.refused(lambda: loopapi.remove(self.structure, "self", "0" * 64), 409)
        self.assertIsNotNone(looprun.load_settings(self.structure, "self"))

    def test_the_controllers_message_comes_back_as_400(self):
        message = None
        try:
            looprun.validate({"version": 1, "flow": "self", **GOOD, "extra": 1}, looprun.settings_path(self.structure, "self"))
        except looprun.LoopError as exc:
            message = str(exc)
        error = self.refused(lambda: self.save({**GOOD, "extra": 1}), 400)
        self.assertEqual(error.message, message)
        self.assertIn("unknown key 'extra'", error.message)
        self.assertIn("limits.attempts is required", self.refused(lambda: self.save({"check": {"command": "true"}}), 400).message)
        self.assertIn("unknown key", self.refused(lambda: self.save({"limits": {"attempts": 2, "x": 1}}), 400).message)
        self.refused(lambda: self.save({**GOOD, "flow": "pair"}), 400)  # settings for another flow
        self.refused(lambda: self.save([1]), 400)
        self.refused(lambda: self.save(rev=None), 400)
        self.assertIsNone(looprun.load_settings(self.structure, "self"))

    def test_only_a_flow_that_loops_back_can_be_saved(self):
        (self.structure / "flows" / "open.md").write_text("path: worker -> reviewer\n\nNever returns to the worker.\n")
        self.assertEqual(self.refused(lambda: self.save(flow="open"), 400).message, looprun.LOOP_BACK)
        self.assertIn("not on this path", self.refused(lambda: self.save({**GOOD, "steps": {"reviewer": {"on": {"fail": "hard"}}}}, flow="pair"), 400).message)
        self.assertIn("no flow", self.refused(lambda: self.save(flow="ghost"), 400).message)
        self.refused(lambda: self.save(flow="../x"), 400)
        self.refused(lambda: self.save(flow=3), 400)
        self.assertFalse((self.structure / ".loops").exists())

    def test_remove_with_rev_returns_before_and_an_orphan_can_be_removed(self):
        first = self.save()
        gone = loopapi.remove(self.structure, "self", first["rev"])
        self.assertEqual(gone["before"], first["settings"])
        self.assertIsNone(looprun.load_settings(self.structure, "self"))
        self.assertIn("no loop is saved", self.refused(lambda: loopapi.remove(self.structure, "self", ""), 400).message)
        restored = self.save(gone["before"], "")  # Undo of a remove
        self.assertEqual(restored["rev"], first["rev"])
        looprun.save_settings(self.structure, "old-flow", {"version": 1, "flow": "old-flow", **GOOD})
        rev = loopapi.rev_of(looprun.settings_path(self.structure, "old-flow").read_bytes())
        loopapi.remove(self.structure, "old-flow", rev)
        self.assertEqual(loopapi.listing(self.structure, self.project, None)["orphans"], [])

    def test_a_broken_file_can_be_replaced_with_its_rev(self):
        path = looprun.settings_path(self.structure, "self")
        path.parent.mkdir()
        path.write_text("{broken")
        entry = self.entry()
        self.refused(lambda: self.save(rev=""), 409)
        saved = self.save(rev=entry["rev"])
        self.assertIsNone(saved["before"])
        self.assertEqual(self.entry()["problem"], "")


class StartCancelTests(Base):
    def start(self, flow="self", goal=None, workdir=None, **kw):
        return loopapi.start(self.structure, self.project, flow, goal, workdir, executor_spec={"kind": "scripted", "attempts": [{}] * 5}, **kw)

    def refused(self, call, status):
        with self.assertRaises(loopapi.ApiError) as caught:
            call()
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_no_check_is_a_run_with_status_invalid_and_no_attempt(self):
        looprun.save_settings(self.structure, "self", {"version": 1, "flow": "self", "limits": {"attempts": 3}})
        answer = self.start(goal="Do it.")
        self.assertEqual((answer["status"], answer["reason"]), ("invalid", "the loop has no check command: a loop without a check never passes"))
        doc = loopapi.run_detail(self.project, answer["run"])
        self.assertEqual((doc["run"]["status"], doc["run"]["attempts"], doc["attempts"]), ("invalid", [], []))

    def test_refusals_before_any_run_folder(self):
        self.refused(lambda: self.start(), 400)  # nothing saved
        looprun.save_settings(self.structure, "self", {"version": 1, "flow": "self", **GOOD})
        self.refused(lambda: self.start(flow="pair"), 400)
        self.refused(lambda: self.start(flow="ghost"), 400)
        self.refused(lambda: self.start(goal=4), 400)
        self.refused(lambda: self.start(goal="g", workdir="relative/folder"), 400)
        self.assertIn("does not exist", self.refused(lambda: self.start(goal="g", workdir=str(self.tmp / "missing")), 400).message)
        self.assertFalse((self.project / ".opencode").exists())

    def test_cancel_refuses_bad_unknown_and_finished_runs(self):
        run = self.finished()
        self.refused(lambda: loopapi.cancel(self.project, "../x"), 400)
        self.refused(lambda: loopapi.cancel(self.project, "20261121-043755-90a7"), 404)
        self.assertIn("already passed", self.refused(lambda: loopapi.cancel(self.project, run), 409).message)
        self.assertFalse((looprun.run_dir(self.project, run) / "cancel").exists())

    def test_cancel_of_a_held_running_run_writes_the_request(self):
        run = self.finished()
        folder = looprun.run_dir(self.project, run)
        state = looprun.read_run(folder)
        (folder / "run.json").write_text(json.dumps({**state, "status": "running"}))
        from unittest.mock import patch
        with patch.object(looprun, "_held", return_value=True):
            self.assertEqual(loopapi.cancel(self.project, run), {"run": run})
        self.assertTrue((folder / "cancel").is_file())
        (folder / "cancel").unlink()
        # release 3 (R4): no controller holds it, so it is cancelled directly instead of refused as interrupted
        self.assertEqual(loopapi.cancel(self.project, run), {"run": run, "result": "cancelled"})
        self.assertEqual(looprun.read_run(folder)["status"], "cancelled")


class Kill(BaseException):
    """A controller that dies on the spot (not an Exception): the record is left as a SIGKILL would leave it."""


class Release3Tests(Base):
    def refused(self, call, status):
        with self.assertRaises(loopapi.ApiError) as caught:
            call()
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def until_final(self, run, seconds=30):
        end = time.monotonic() + seconds
        while time.monotonic() < end and looprun.read_run(looprun.run_dir(self.project, run))["status"] not in (*looprun.FINAL, "paused"):
            time.sleep(0.05)

    def interrupted(self):
        """A run whose controller was killed in the middle of its second turn, with the executor spec the API would resume it with."""
        looprun.save_settings(self.structure, "self", {"version": 1, "flow": "self", "limits": {"attempts": 5}, "check": {"command": CHECK_SECOND, "timeout": 20}})
        def hook(count, call):
            if count == 2:
                raise Kill()
        before = set(looprun.list_runs(self.project))
        with self.assertRaises(Kill):
            looprun.start(self.project, "self", structure=self.structure, goal="Make it pass.", wait=True,
                          executor=looprun.ScriptedExecutor([{}, {}], hook=hook, reconcile=self.tmp / "none.json"))
        run, = set(looprun.list_runs(self.project)) - before
        return run

    def test_a_listed_run_carries_trial_settings_sha256_passed_by_and_waiting(self):
        run = self.finished()
        row = next(r for r in loopapi.listing(self.structure, self.project, None)["runs"] if r["run"] == run)
        self.assertEqual((row["trial"], row["passed_by"], row["waiting"]), (False, "check", None))
        self.assertEqual(row["settings_sha256"], looprun.read_run(looprun.run_dir(self.project, run))["settings_sha256"])
        self.assertEqual(len(row["settings_sha256"]), 64)

    def test_versions_are_listed_per_loop_newest_first(self):
        self.assertEqual(self.entry()["versions"], [])
        for check in ("true", "false", "echo 3"):
            looprun.save_settings(self.structure, "self", {"version": 1, "flow": "self", "limits": {"attempts": 2}, "check": {"command": check}})
        versions = self.entry()["versions"]
        self.assertEqual(len(versions), 2)
        self.assertGreaterEqual(versions[0]["version"], versions[1]["version"])
        self.assertEqual(set(versions[0]), {"version", "time", "sha256"})

    def test_the_page_save_and_remove_keep_history_and_restore_needs_the_current_rev(self):
        first = loopapi.save(self.structure, "self", {"version": 1, "flow": "self", **GOOD}, "")
        second = loopapi.save(self.structure, "self", {"version": 1, "flow": "self", "check": {"command": "false"}, "limits": {"attempts": 2}}, first["rev"])
        version = self.entry()["versions"][0]["version"]
        self.refused(lambda: loopapi.restore(self.structure, "self", version, first["rev"]), 409)  # a stale rev
        self.refused(lambda: loopapi.restore(self.structure, "self", "../../x", second["rev"]), 400)
        self.refused(lambda: loopapi.restore(self.structure, "self", "20000101T000000.000000Z-00000000", second["rev"]), 400)
        self.refused(lambda: loopapi.restore(self.structure, "self", 7, second["rev"]), 400)
        done = loopapi.restore(self.structure, "self", version, second["rev"])
        self.assertEqual((done["rev"], done["before"]["check"]["command"], done["settings"]["check"]["command"]), (first["rev"], "false", "true"))
        removed = loopapi.remove(self.structure, "self", done["rev"])
        self.assertEqual(len(self.entry()["versions"]), 3)  # the first save wrote none; the replacement, the restore and the removal each kept one
        self.assertEqual(removed["before"]["check"]["command"], "true")

    def test_a_trial_run_pins_the_given_settings_and_leaves_the_saved_loop(self):
        looprun.save_settings(self.structure, "self", {"version": 1, "flow": "self", **GOOD})
        saved = looprun.settings_path(self.structure, "self").read_bytes()
        answer = loopapi.start(self.structure, self.project, "self", "Try it.", None, executor_spec={"kind": "scripted", "attempts": [{}] * 3},
                               settings={"check": {"command": "true"}, "limits": {"attempts": 1}})
        self.until_final(answer["run"])
        row = loopapi.listing(self.structure, self.project, None)["runs"][0]
        self.assertEqual((row["run"], row["trial"], row["status"], row["limit"]), (answer["run"], True, "passed", 1))
        self.assertEqual(looprun.settings_path(self.structure, "self").read_bytes(), saved)
        self.refused(lambda: loopapi.start(self.structure, self.project, "self", "g", None, settings={"check": {"command": "true"}, "x": 1}), 400)
        self.refused(lambda: loopapi.start(self.structure, self.project, "self", "g", None, settings=[1]), 400)
        self.assertEqual(len(looprun.list_runs(self.project)), 1)

    def test_pause_resume_answer_refuse_with_the_right_status(self):
        run = self.finished()
        for call in (lambda r: loopapi.pause(self.project, r), lambda r: loopapi.resume(self.project, r), lambda r: loopapi.answer_run(self.project, r, "approve")):
            self.refused(lambda: call("20261121-043755-90a7"), 404)
            self.refused(lambda: call("../x"), 400)
            self.assertIn("passed", self.refused(lambda: call(run), 409).message)
        self.refused(lambda: loopapi.resume(self.project, run, "maybe"), 400)
        self.refused(lambda: loopapi.answer_run(self.project, run, "maybe"), 400)

    def test_resume_of_an_interrupted_run_goes_on_and_pause_of_it_is_409(self):
        run = self.interrupted()
        self.assertEqual(looprun.shown_status(looprun.run_dir(self.project, run), looprun.read_run(looprun.run_dir(self.project, run))), "interrupted")
        self.assertIn("interrupted", self.refused(lambda: loopapi.pause(self.project, run), 409).message)
        self.assertEqual(loopapi.resume(self.project, run), {"run": run, "status": "running"})
        self.until_final(run)
        record = looprun.read_run(looprun.run_dir(self.project, run))
        self.assertEqual(record["status"], "paused")  # the scripted reconcile has no file: it cannot tell, so it sends nothing and waits for the person
        self.assertIn("nothing was sent", record["reason"])
        self.refused(lambda: loopapi.resume(self.project, run, "maybe"), 400)
        self.assertEqual(loopapi.resume(self.project, run, "redo"), {"run": run, "status": "running"})
        end = time.monotonic() + 30
        while time.monotonic() < end and looprun.read_run(looprun.run_dir(self.project, run))["status"] not in looprun.FINAL:
            time.sleep(0.05)
        self.assertEqual(looprun.read_run(looprun.run_dir(self.project, run))["status"], "passed")

    def test_cancel_of_an_interrupted_run_marks_it_cancelled(self):
        run = self.interrupted()
        self.assertEqual(loopapi.cancel(self.project, run), {"run": run, "result": "cancelled"})
        self.refused(lambda: loopapi.cancel(self.project, run), 409)

    def test_the_run_detail_has_per_step_message_reply_and_findings_texts_capped(self):
        run = self.finished()
        folder = looprun.run_dir(self.project, run)
        entry = looprun.read_run(folder)["attempts"][0]["steps"][0]
        (folder / entry["reply"]).write_text("a" * 5000 + "b" * loopapi.STEP_TEXT_CHARS, encoding="utf-8")
        step = loopapi.run_detail(self.project, run)["attempts"][0]["steps"][0]
        self.assertEqual(set(step), {"step", "try", "message", "reply", "findings"})
        self.assertEqual(step["reply"], "b" * loopapi.STEP_TEXT_CHARS)
        self.assertIn("Make it pass.", step["message"])
        self.assertEqual(step["findings"], "")

    def test_shape_ok_knows_the_new_ops(self):
        good = [{"op": "pause", "run": "r"}, {"op": "resume", "run": "r"}, {"op": "resume", "run": "r", "choice": "redo"},
                {"op": "answer", "run": "r", "answer": "approve"}, {"op": "answer", "run": "r", "answer": "reject", "note": "n"},
                {"op": "restore", "flow": "f", "version": "v", "rev": ""}, {"op": "start", "flow": "f", "settings": {}}]
        bad = [{"op": "pause"}, {"op": "resume", "run": "r", "choice": 1}, {"op": "answer", "run": "r"}, {"op": "restore", "flow": "f", "version": "v"},
               {"op": "start", "flow": "f", "settings": "x"}, {"op": "pause", "run": "r", "x": "y"}]
        self.assertEqual([loopapi.shape_ok(r) for r in good], [True] * len(good))
        self.assertEqual([loopapi.shape_ok(r) for r in bad], [False] * len(bad))


class QueuedNeverStartedTests(Base):
    """A run whose detached controller never came (round 2): what the page API shows and does."""

    def queued(self, age=0):
        looprun.save_settings(self.structure, "self", {"version": 1, "flow": "self", "limits": {"attempts": 3}, "check": {"command": "true", "timeout": 20}})
        with mock.patch.object(looprun.subprocess, "Popen"):
            run = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([{}]))
        folder = looprun.run_dir(self.project, run)
        for name in ("run.json", "definition.json"):
            data = json.loads((folder / name).read_text())
            data["created"] -= age
            (folder / name).write_text(json.dumps(data))
        return run, folder

    def refused(self, call, status):
        with self.assertRaises(loopapi.ApiError) as caught:
            call()
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_a_queued_run_with_no_controller_for_30_seconds_is_listed_and_shown_interrupted(self):
        run, folder = self.queued(age=60)
        row = next(r for r in loopapi.listing(self.structure, self.project, None)["runs"] if r["run"] == run)
        self.assertEqual(row["status"], "interrupted")
        self.assertEqual(loopapi.run_detail(self.project, run)["run"]["status"], "interrupted")
        self.assertEqual(json.loads((folder / "run.json").read_text())["status"], "queued")  # shown, not stored
        self.assertFalse((folder / "lock").exists())  # looking made no file

    def test_a_young_queued_run_is_still_queued_and_cannot_be_resumed(self):
        run, _ = self.queued(age=1)
        row = next(r for r in loopapi.listing(self.structure, self.project, None)["runs"] if r["run"] == run)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(self.refused(lambda: loopapi.resume(self.project, run), 409).status, 409)

    def test_cancel_of_a_never_started_run_answers_cancelled(self):
        run, folder = self.queued(age=60)
        self.assertEqual(loopapi.cancel(self.project, run), {"run": run, "result": "cancelled"})
        self.assertEqual(looprun.read_run(folder)["status"], "cancelled")

    def test_resume_of_a_never_started_run_with_a_changed_definition_is_400_and_leaves_it(self):
        run, folder = self.queued(age=60)
        definition = json.loads((folder / "definition.json").read_text())
        definition["steps"][0]["on"]["returned"] = "done"
        (folder / "definition.json").write_text(json.dumps(definition))
        with mock.patch.object(looprun.subprocess, "Popen") as popen:
            error = self.refused(lambda: loopapi.resume(self.project, run), 400)
        self.assertIn("steps_sha256", str(error.message))
        self.assertFalse(popen.called)
        self.assertEqual(looprun.read_run(folder)["status"], "queued")


if __name__ == "__main__":
    unittest.main()
