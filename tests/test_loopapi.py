"""The page API of a saved loop (agentgraph.loopapi): listing, run detail, save/remove with `rev`, start, cancel, on temp folders and
the scripted executor. No test starts OpenCode; runs are started with `wait` semantics through the controller or written by hand."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

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
        self.assertEqual(set(own), {"flow", "name", "path", "agent", "runnable", "reason", "settings", "problem", "rev"})
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
        self.assertIn("already interrupted", self.refused(lambda: loopapi.cancel(self.project, run), 409).message)  # no controller holds it
        from unittest.mock import patch
        with patch.object(looprun, "_held", return_value=True):
            self.assertEqual(loopapi.cancel(self.project, run), {"run": run})
        self.assertTrue((folder / "cancel").is_file())


if __name__ == "__main__":
    unittest.main()
