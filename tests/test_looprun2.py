"""Loops between several agents (release 2 of agentgraph.looprun): tests E1 to E13 of `design2.md` on the scripted executor with
real check scripts in temp folders. Each agent's turns are scripted on their own (`ScriptedExecutor({"worker": [...], "reviewer":
[...]})`) and every message each agent was sent, and in which session, is read back from the executor and from the run folder.
No test starts OpenCode. E13 (every release 1 test) is `tests/test_looprun.py`."""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentgraph import looprun
from tests.shellskip import sh_or
from tests.winskip import needs_symlinks

PASS = "Looks right to me.\nVERDICT: PASS"
FAIL = "The header line is missing.\nVERDICT: FAIL"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.structure = self.tmp / "structure"
        (self.structure / "30-build").mkdir(parents=True)
        for name in ("worker", "reviewer", "verifier", "hard"):
            (self.structure / "30-build" / f"{name}.md").write_text(f"{name}.\n")
        (self.structure / "flows").mkdir()
        (self.structure / "flows" / "self.md").write_text("path: worker -> worker\n\nSelf.\n")
        (self.structure / "flows" / "duo.md").write_text("path: worker -> reviewer -> worker\n\nOne judge.\n")
        (self.structure / "flows" / "trio.md").write_text("path: worker -> reviewer -> verifier -> worker\n\nTwo judges.\n")
        (self.structure / "flows" / "open.md").write_text("path: worker -> reviewer\n\nNo way back.\n")
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.work = self.project

    def settings(self, flow="duo", check=None, attempts=4, limits=None, **extra) -> dict:
        out = {"version": 1, "flow": flow, "limits": {"attempts": attempts, **(limits or {})}, **extra}
        if check is not None:
            out["check"] = {"command": check, "timeout": 20}
        return out

    def save(self, flow="duo", **kw) -> dict:
        settings = self.settings(flow, **kw)
        looprun.save_settings(self.structure, flow, settings)
        return settings

    def raw(self, flow, settings) -> None:
        """A saved loop written without any check, as a hand edit would."""
        path = looprun.settings_path(self.structure, flow)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(settings))

    def go(self, flow, script, goal="Write report.md with a header.", hook=None, **kw):
        executor = script if isinstance(script, looprun.ScriptedExecutor) else looprun.ScriptedExecutor(script, hook=hook)
        run = looprun.start(self.project, flow, structure=self.structure, goal=goal, executor=executor, wait=True, **kw)
        return run, looprun.read_run(looprun.run_dir(self.project, run)), executor

    def folder(self, run) -> Path:
        return looprun.run_dir(self.project, run)

    def events(self, run) -> list[tuple]:
        lines = (self.folder(run) / "events.jsonl").read_text().splitlines()
        return [(e["step"], e["outcome"], e["route"]) for e in map(json.loads, lines) if e["outcome"] != "queued"]

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = looprun.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def waiting(self, run, thread, attempts=1, seconds=20) -> dict:
        end = time.time() + seconds
        while time.time() < end:
            try:
                state = looprun.read_run(self.folder(run))
            except looprun.LoopError:
                continue
            if state.get("waiting") and len(state["attempts"]) >= attempts:
                return state
            time.sleep(0.05)
        self.fail(f"run never waited: {looprun.read_run(self.folder(run))['status']}")

    def detached(self, flow, script, goal="Write report.md with a header."):
        """The run in a thread of this process (`start` with `wait`), so a test can talk to it while it runs. Returns (run, executor, thread)."""
        executor = looprun.ScriptedExecutor(script)
        thread = threading.Thread(target=looprun.start, args=(self.project, flow),
                                  kwargs=dict(structure=self.structure, goal=goal, executor=executor, wait=True))
        thread.start()
        self.addCleanup(thread.join, 20)
        loops = self.project / ".opencode" / "loops"
        end = time.time() + 20
        while time.time() < end and not (loops.is_dir() and os.listdir(loops)):
            time.sleep(0.02)
        return os.listdir(loops)[0], executor, thread

    def finished(self, run, thread, seconds=20) -> dict:
        thread.join(seconds)
        self.assertFalse(thread.is_alive())
        return looprun.read_run(self.folder(run))


def by(executor, agent):
    return [c for c in executor.calls if c["agent"] == agent]


class FeedbackTests(Base):
    def test_e1_a_failing_judge_sends_its_findings_to_the_worker(self):
        self.save()
        run, record, ex = self.go("duo", {"worker": [{"text": "first try"}, {"text": "second try"}], "reviewer": [{"text": FAIL}, {"text": PASS}]})
        self.assertEqual(record["status"], "passed")
        self.assertEqual(len(record["attempts"]), 2)
        first, second = record["attempts"]
        self.assertEqual([(s["step"], s["outcome"]) for s in first["steps"]], [("work", "returned"), ("judge:reviewer", "fail")])
        self.assertEqual(first["steps"][1]["route"], "work")
        self.assertEqual((first["feedback_from"], second["feedback_from"]), (None, "reviewer"))
        worker = by(ex, "worker")
        self.assertNotIn("The header line is missing.", worker[0]["message"])
        self.assertIn("The header line is missing.", worker[1]["message"])
        self.assertIn("reviewer", worker[1]["message"])
        self.assertIn("Write report.md with a header.", worker[1]["message"])  # the goal is in every message
        self.assertNotIn("VERDICT: FAIL", worker[1]["message"])  # the findings are what came before the verdict line
        folder = self.folder(run)
        findings = (folder / first["steps"][1]["findings"]).read_text()
        self.assertEqual(findings.strip(), "The header line is missing.")
        self.assertEqual(first["steps"][1]["verdict"], "fail")
        handoff = [h for h in record["handoffs"] if h["kind"] == "findings"]
        self.assertEqual([(h["from"], h["to"], h["file"]) for h in handoff], [("reviewer", "worker", first["steps"][1]["findings"])])
        self.assertEqual(handoff[0]["sha256"], hashlib.sha256((folder / handoff[0]["file"]).read_bytes()).hexdigest())

    def test_e2_a_passing_judge_ends_the_run_passed_and_says_what_decided(self):
        self.save()
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}]})
        self.assertEqual((record["status"], record["passed_by"], len(record["attempts"])), ("passed", "judges", 1))
        self.assertEqual(record["reason"], "the judges passed")
        self.save(check="exit 0")
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}]})
        self.assertEqual((record["status"], record["passed_by"]), ("passed", "check+judges"))
        self.assertEqual(record["attempts"][0]["check"]["exit"], 0)
        self.assertEqual(record["attempts"][0]["agent"]["outcome"], "returned")  # release 1 keys keep their meaning
        self.assertEqual(self.events(run)[-1], ("judge:reviewer", "passed", "end"))

    def test_e4_a_failing_check_spends_no_judge_turn(self):
        self.save(check=sh_or("echo bad output; exit 3", "print('bad output'); raise SystemExit(3)"), attempts=2)
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}] * 5, "worker": [{}, {}]})
        self.assertEqual((record["status"], len(record["attempts"])), ("failed", 2))
        self.assertEqual(by(ex, "reviewer"), [])
        self.assertEqual([(s["step"], s["outcome"], s["route"]) for s in record["attempts"][0]["steps"]],
                         [("work", "returned", "check"), ("check", "fail", "work")])
        self.assertIn("exit code 3", by(ex, "worker")[1]["message"])
        self.assertIn("bad output", by(ex, "worker")[1]["message"])
        self.assertEqual(record["attempts"][1]["feedback_from"], "check")
        self.assertEqual([h["kind"] for h in record["handoffs"]], ["check"])


class FreshJudgeTests(Base):
    def test_e3_a_fresh_judge_is_a_new_session_and_never_sees_the_workers_words(self):
        self.save(check="exit 0")
        claim = "WORKER-CLAIM-4711 everything is done"
        run, record, ex = self.go("duo", {"worker": [{"text": claim}] * 3, "reviewer": [{"text": FAIL}, {"text": FAIL}, {"text": PASS}]})
        self.assertEqual(record["status"], "passed")
        worker, judge = by(ex, "worker"), by(ex, "reviewer")
        self.assertEqual(len(worker), 3)
        self.assertEqual(len({c["session"] for c in worker}), 1)  # the worker keeps its session
        self.assertEqual([c["first"] for c in worker], [True, False, False])
        self.assertEqual(len({c["session"] for c in judge}), 3)  # a judge starts a new session every time it runs
        self.assertTrue(all(c["first"] for c in judge))
        self.assertTrue(set(c["session"] for c in judge).isdisjoint({worker[0]["session"]}))
        self.assertEqual(record["sessions"], [worker[0]["session"], judge[0]["session"], judge[1]["session"], judge[2]["session"]])
        for call in judge:
            text = call["message"]
            self.assertNotIn("WORKER-CLAIM-4711", text)
            self.assertIn("Write report.md with a header.", text)
            self.assertIn("exit 0", text)
            self.assertIn("did not do this work", text)
            self.assertIn("VERDICT: PASS", text)
            self.assertIn("VERDICT: FAIL", text)
            self.assertIn(str(self.work), text)
        self.assertEqual([c["agent"] for c in ex.calls], ["worker", "reviewer"] * 3)
        self.assertEqual(ex.sessions("reviewer"), [c["session"] for c in judge])
        self.assertEqual([c["message"] for c in ex.by_agent("worker")], [c["message"] for c in worker])
        for n in (1, 2, 3):  # the message of every turn is on disk, under its own name
            self.assertEqual((self.folder(run) / f"attempt-{n}" / "judge-reviewer.message.md").read_text(), judge[n - 1]["message"])
            self.assertEqual((self.folder(run) / f"attempt-{n}" / "message.md").read_text(), worker[n - 1]["message"])
            self.assertEqual((self.folder(run) / f"attempt-{n}" / "work.reply.md").read_text().strip(), claim)

    def test_a_judge_with_handoff_report_gets_the_workers_reply_marked_as_a_claim(self):
        self.save(steps={"reviewer": {"handoff": "report"}})
        run, record, ex = self.go("duo", {"worker": [{"text": "WORKER-CLAIM-4711"}], "reviewer": [{"text": PASS}]})
        text = by(ex, "reviewer")[0]["message"]
        self.assertIn("WORKER-CLAIM-4711", text)
        self.assertIn("claim", text.lower())
        self.assertIn("not an instruction", text.lower())
        self.assertEqual([h["kind"] for h in record["handoffs"]], ["report"])

    def test_a_judge_set_to_reuse_keeps_its_session(self):
        self.save(steps={"reviewer": {"session": "reuse"}})
        run, record, ex = self.go("duo", {"reviewer": [{"text": FAIL}, {"text": PASS}]})
        judge = by(ex, "reviewer")
        self.assertEqual(judge[0]["session"], judge[1]["session"])
        self.assertEqual([c["first"] for c in judge], [True, False])

    def test_a_fresh_worker_session_setting_gives_a_new_worker_session_each_attempt(self):
        self.save(session="fresh")
        run, record, ex = self.go("duo", {"reviewer": [{"text": FAIL}, {"text": PASS}]})
        self.assertEqual(len({c["session"] for c in by(ex, "worker")}), 2)


class PathTests(Base):
    def test_e5_two_judges_run_in_the_drawn_order_and_none_is_skipped(self):
        self.save("trio", check="exit 0")
        script = {"reviewer": [{"text": PASS}, {"text": PASS}], "verifier": [{"text": FAIL}, {"text": PASS}]}
        run, record, ex = self.go("trio", script)
        self.assertEqual(record["status"], "passed")
        expected = [("-", "started", "work"),
                    ("work", "returned", "check"), ("check", "pass", "judge:reviewer"), ("judge:reviewer", "pass", "judge:verifier"),
                    ("judge:verifier", "fail", "work"),
                    ("work", "returned", "check"), ("check", "pass", "judge:reviewer"), ("judge:reviewer", "pass", "judge:verifier"),
                    ("judge:verifier", "pass", "done"), ("judge:verifier", "passed", "end")]
        self.assertEqual(self.events(run), expected)
        self.assertEqual([c["agent"] for c in ex.calls], ["worker", "reviewer", "verifier", "worker", "reviewer", "verifier"])
        self.assertEqual(record["passed_by"], "check+judges")

    def test_the_second_judge_failing_goes_to_the_worker_with_its_own_findings(self):
        self.save("trio")
        script = {"reviewer": [{"text": PASS}, {"text": PASS}], "verifier": [{"text": "Tests are absent.\nVERDICT: FAIL"}, {"text": PASS}]}
        run, record, ex = self.go("trio", script)
        self.assertIn("Tests are absent.", by(ex, "worker")[1]["message"])
        self.assertIn("verifier", by(ex, "worker")[1]["message"])
        self.assertEqual(record["attempts"][1]["feedback_from"], "verifier")

    def test_a_first_judge_failing_never_asks_the_second(self):
        self.save("trio", attempts=1)
        run, record, ex = self.go("trio", {"reviewer": [{"text": FAIL}], "verifier": [{"text": PASS}]})
        self.assertEqual(by(ex, "verifier"), [])
        self.assertEqual(record["status"], "failed")

    def test_the_definition_holds_the_compiled_steps(self):
        self.save("trio", check="exit 0")
        run, _, _ = self.go("trio", {"reviewer": [{"text": PASS}], "verifier": [{"text": PASS}]})
        steps = json.loads((self.folder(run) / "definition.json").read_text())["steps"]
        self.assertEqual([s["id"] for s in steps], ["work", "check", "judge:reviewer", "judge:verifier"])
        self.assertEqual([s["kind"] for s in steps], ["agent", "check", "agent", "agent"])
        self.assertEqual(steps[0]["on"], {"returned": "check", "stalled": "check", "agent_error": "stop", "cancelled": "stop", "no_artifact": "work"})
        self.assertEqual(steps[1]["on"], {"pass": "judge:reviewer", "fail": "work", "inconclusive": "stop", "check_error": "stop"})
        self.assertEqual(steps[2]["on"]["pass"], "judge:verifier")
        self.assertEqual(steps[3]["on"]["pass"], "done")
        self.assertEqual((steps[2]["session"], steps[2]["retries"], steps[0]["session"], steps[0]["retries"]), ("fresh", 1, "reuse", 0))

    def test_without_a_check_the_steps_are_work_and_judges(self):
        steps = looprun.compile(["worker", "reviewer", "worker"], looprun.validate(self.settings(), Path("p.json")))
        self.assertEqual([s["id"] for s in steps], ["work", "judge:reviewer"])
        self.assertEqual(steps[0]["on"]["returned"], "judge:reviewer")


class RefusalTests(Base):
    """E6: every refused definition fails before any run folder, any session and any model call."""

    def refuse(self, flow, settings, words):
        executor = looprun.ScriptedExecutor([{}] * 5)
        self.raw(flow, {"version": 1, "flow": flow, "limits": {"attempts": 3}, **settings})
        with self.assertRaises(looprun.LoopError, msg=words) as caught:
            looprun.start(self.project, flow, structure=self.structure, goal="g", executor=executor, wait=True)
        self.assertIn(words, str(caught.exception))
        self.assertFalse((self.project / ".opencode").exists(), words)
        self.assertEqual(executor.calls, [], words)

    def test_e6_a_path_that_does_not_loop_back(self):
        self.refuse("open", {}, "this path does not loop back to its first agent")
        for path in (["worker", "reviewer"], ["worker"], [], ["worker", "reviewer", "verifier"]):
            with self.assertRaises(looprun.LoopError) as caught:
                looprun.compile(path, looprun.validate(self.settings(check="true"), Path("p.json")))
            self.assertIn("does not loop back to its first agent", str(caught.exception))

    def test_e6_an_agent_drawn_twice_inside_the_loop(self):
        for path in (["worker", "reviewer", "verifier", "reviewer", "worker"], ["worker", "worker", "reviewer", "worker"],
                     ["worker", "reviewer", "reviewer", "worker"], ["worker", "worker", "worker"]):
            with self.assertRaises(looprun.LoopError) as caught:
                looprun.compile(path, looprun.validate(self.settings(check="true"), Path("p.json")))
            self.assertIn("twice", str(caught.exception))
        (self.structure / "flows" / "twice.md").write_text("path: worker -> reviewer -> verifier -> reviewer -> worker\n\nX\n")
        self.raw("twice", {"version": 1, "flow": "twice", "limits": {"attempts": 2}})
        with self.assertRaises(looprun.LoopError):  # however the page draws it, it never becomes a run
            looprun.start(self.project, "twice", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]), wait=True)
        self.assertFalse((self.project / ".opencode").exists())

    def test_e6_an_agent_that_is_not_in_the_structure(self):
        (self.structure / "flows" / "ghost.md").write_text("path: worker -> ghost -> worker\n\nX\n")
        self.raw("ghost", {"version": 1, "flow": "ghost", "limits": {"attempts": 2}})
        ex = looprun.ScriptedExecutor([])
        with self.assertRaises(looprun.LoopError):
            looprun.start(self.project, "ghost", structure=self.structure, goal="g", executor=ex, wait=True)
        self.assertFalse((self.project / ".opencode").exists())
        settings = looprun.validate(self.settings(steps={"worker": {"escalate": {"after": 1, "to": "nobody"}}}), Path("p.json"))
        with self.assertRaises(looprun.LoopError) as caught:
            looprun.compile(["worker", "reviewer", "worker"], settings, agents={"worker": "g", "reviewer": "g"})
        self.assertIn("nobody", str(caught.exception))
        self.refuse("duo", {"steps": {"worker": {"escalate": {"after": 1, "to": "nobody"}}}}, "nobody")

    def test_e6_a_route_that_jumps_forward(self):
        self.refuse("trio", {"steps": {"worker": {"on": {"returned": "verifier"}}}}, "would skip")
        self.refuse("trio", {"check": {"command": "true"}, "steps": {"worker": {"on": {"returned": "reviewer"}}}}, "would skip check")
        self.refuse("trio", {"steps": {"reviewer": {"on": {"fail": "verifier"}}}}, "later step")
        self.refuse("trio", {"steps": {"reviewer": {"on": {"pass": "done"}}}}, "done")

    def test_e6_a_route_to_an_agent_that_is_not_on_the_path(self):
        self.refuse("duo", {"steps": {"reviewer": {"on": {"fail": "hard"}}}}, "not on this path")
        self.refuse("duo", {"steps": {"reviewer": {"on": {"fail": "nobody"}}}}, "nobody")
        self.refuse("trio", {"steps": {"verifier": {"on": {"fail": "reviewer"}}}}, "judge")

    def test_e6_bad_settings_of_the_new_keys(self):
        cases = [({"steps": {"stranger": {"retries": 1}}}, "stranger"),
                 ({"steps": {"reviewer": {"on": {"returned": "next"}}}}, "returned"),
                 ({"steps": {"reviewer": {"on": {"bogus": "next"}}}}, "bogus"),
                 ({"steps": {"reviewer": {"colour": 1}}}, "colour"),
                 ({"steps": {"reviewer": {"retries": 4}}}, "retries"),
                 ({"steps": {"reviewer": {"retries": -1}}}, "retries"),
                 ({"steps": {"reviewer": {"session": "never"}}}, "session"),
                 ({"steps": {"reviewer": {"handoff": "all"}}}, "handoff"),
                 ({"steps": {"worker": {"handoff": "report"}}}, "handoff"),
                 ({"steps": {"reviewer": {"escalate": {"after": 1, "to": "hard"}}}}, "escalate"),
                 ({"steps": {"worker": {"escalate": {"after": 1, "to": "reviewer"}}}}, "on this path"),
                 ({"steps": {"worker": {"escalate": {"after": 0, "to": "hard"}}}}, "after"),
                 ({"steps": {"worker": {"escalate": {"after": 1}}}}, "to"),
                 ({"steps": {"check": {"on": {"fail": "stop"}}}}, "no check command"),
                 ({"steps": {"check": {"retries": 1}}, "check": {"command": "true"}}, "retries"),
                 ({"steps": {"check": {"on": {"fail": "human"}}}, "check": {"command": "true"}}, "failing check"),
                 ({"artifacts": ["/etc/passwd"]}, "relative"),
                 ({"artifacts": ["../x"]}, ".."),
                 ({"artifacts": ["a", "a"]}, "twice"),
                 ({"artifacts": [f"f{i}" for i in range(21)]}, "20"),
                 ({"artifacts": "report.md"}, "list"),
                 ({"approval": "always"}, "approval"),
                 ({"unknown": 1}, "unknown"),
                 ({"steps": []}, "steps")]
        for settings, words in cases:
            with self.subTest(settings=settings):
                self.refuse("duo", settings, words)

    def test_e6_the_new_keys_are_for_loops_between_agents_only(self):
        self.refuse("self", {"check": {"command": "true"}, "approval": "before_pass"}, "one agent on itself")
        self.refuse("self", {"check": {"command": "true"}, "artifacts": ["a"]}, "one agent on itself")
        self.refuse("self", {"check": {"command": "true"}, "steps": {"worker": {"retries": 1}}}, "one agent on itself")

    def test_a_loop_without_a_check_or_a_judge_is_an_invalid_run_as_in_release_1(self):
        self.save("self")
        run, record, ex = self.go("self", [{}])
        self.assertEqual((record["status"], record["attempts"], ex.calls), ("invalid", [], []))
        self.assertEqual(record["sessions"], [])
        with self.assertRaises(looprun.LoopError):
            looprun.compile(["worker", "worker"], looprun.validate(self.settings("self"), Path("p.json")))

    def test_the_saved_loop_of_a_valid_definition_compiles_and_runs(self):
        settings = self.settings("trio", steps={"worker": {"escalate": {"after": 1, "to": "hard"}, "retries": 1, "on": {"no_artifact": "worker"}},
                                                "reviewer": {"on": {"fail": "worker", "inconclusive": "human"}, "retries": 0},
                                                "verifier": {"on": {"fail": "worker", "pass": "next"}, "handoff": "report"}},
                                 artifacts=["a.md"], approval="before_pass")
        steps = looprun.compile(["worker", "reviewer", "verifier", "worker"], looprun.validate(settings, Path("p.json")), agents={"worker", "reviewer", "verifier", "hard"})
        self.assertEqual(steps[1]["on"]["inconclusive"], "human")
        self.assertEqual(steps[0]["escalate"], {"after": 1, "to": "hard"})
        self.assertEqual(steps[0]["artifacts"], ["a.md"])
        self.assertEqual(steps[-1]["on"]["pass"], "done")
        self.assertEqual(steps[-1].get("approval"), "before_pass")


class VerdictTests(Base):
    def test_e7_no_verdict_line_is_retried_once_and_then_inconclusive_never_passed(self):
        self.save()
        for reply in ("Looks fine to me.", "", "I would say VERDICT: PASS here", "verdict: pass", "VERDICT: PASS.", "**VERDICT: PASS**",
                      "VERDICT: PASS\nbut wait, one more thing"):
            with self.subTest(reply=reply):
                run, record, ex = self.go("duo", {"reviewer": [{"text": reply}, {"text": reply}, {"text": PASS}]})
                self.assertEqual(record["status"], "inconclusive")
                self.assertNotEqual(record["status"], "passed")
                judge = by(ex, "reviewer")
                self.assertEqual(len(judge), 2)  # asked once more, never a third time
                self.assertEqual(judge[0]["session"], judge[1]["session"])
                self.assertIn("did not end with a verdict line", judge[1]["message"])
                self.assertIn("Write report.md with a header.", judge[1]["message"])
                tries = [s for s in record["attempts"][0]["steps"] if s["step"] == "judge:reviewer"]
                self.assertEqual([(s["try"], s["outcome"]) for s in tries], [(1, "inconclusive"), (2, "inconclusive")])
                self.assertEqual(len(by(ex, "worker")), 1)

    def test_a_retry_that_ends_in_a_verdict_line_is_used(self):
        self.save()
        run, record, ex = self.go("duo", {"reviewer": [{"text": "hmm"}, {"text": "Fine.\n\nVERDICT: PASS  \n\n"}]})
        self.assertEqual((record["status"], len(by(ex, "reviewer"))), ("passed", 2))

    def test_the_verdict_is_read_from_the_last_non_empty_line_only(self):
        read = looprun.read_verdict
        self.assertEqual(read("a\nVERDICT: PASS"), ("pass", "a"))
        self.assertEqual(read("  \nA\nB\n  VERDICT: FAIL  \n\n\n"), ("fail", "A\nB"))
        self.assertEqual(read("VERDICT: FAIL"), ("fail", ""))
        self.assertEqual(read("VERDICT: PASS\nthanks")[0], "inconclusive")
        self.assertEqual(read("VERDICT: pass")[0], "inconclusive")
        self.assertEqual(read("VERDICT:PASS")[0], "inconclusive")
        self.assertEqual(read("")[0], "inconclusive")
        self.assertEqual(read(None)[0], "inconclusive")
        self.assertEqual(read("PASS")[0], "inconclusive")
        self.assertEqual(read("x VERDICT: PASS")[0], "inconclusive")

    def test_retries_zero_asks_once_and_a_judge_error_is_retried_once_by_default(self):
        self.save(steps={"reviewer": {"retries": 0}})
        run, record, ex = self.go("duo", {"reviewer": [{"text": "hmm"}, {"text": PASS}]})
        self.assertEqual((record["status"], len(by(ex, "reviewer"))), ("inconclusive", 1))
        self.save()
        run, record, ex = self.go("duo", {"reviewer": [{"error": "provider down"}, {"text": PASS}]})
        self.assertEqual((record["status"], len(by(ex, "reviewer"))), ("passed", 2))
        self.assertEqual([(s["try"], s["outcome"], s["route"]) for s in record["attempts"][0]["steps"][1:]],
                         [(1, "agent_error", "retry"), (2, "pass", "done")])
        run, record, ex = self.go("duo", {"reviewer": [{"error": "down"}, {"error": "still down"}, {"text": PASS}]})
        self.assertEqual((record["status"], len(by(ex, "reviewer"))), ("agent_error", 2))

    def test_a_stalled_judge_stops_the_run(self):
        self.save(limits={"attempt_seconds": 0.3})
        run, record, ex = self.go("duo", {"reviewer": [{"hang": True}, {"text": PASS}]})
        self.assertEqual((record["status"], len(by(ex, "reviewer"))), ("stalled", 1))

    def test_a_worker_error_stops_and_its_retries_are_recorded(self):
        self.save(steps={"worker": {"retries": 1}})
        run, record, ex = self.go("duo", {"worker": [{"error": "boom"}, {"text": "ok"}], "reviewer": [{"text": PASS}]})
        self.assertEqual(record["status"], "passed")
        self.assertEqual([(s["try"], s["outcome"]) for s in record["attempts"][0]["steps"][:2]], [(1, "agent_error"), (2, "returned")])
        self.assertEqual(len(record["attempts"]), 1)  # a retry is not another attempt
        self.assertEqual(by(ex, "worker")[0]["session"], by(ex, "worker")[1]["session"])
        self.save()
        run, record, ex = self.go("duo", {"worker": [{"error": "boom"}], "reviewer": [{"text": PASS}]})
        self.assertEqual((record["status"], by(ex, "reviewer")), ("agent_error", []))


class LimitTests(Base):
    def test_e8_attempts_are_counted_per_worker_turn_and_judge_turns_do_not_count(self):
        self.save(attempts=2, steps={"reviewer": {"retries": 1}})
        run, record, ex = self.go("duo", {"reviewer": [{"text": "hmm"}, {"text": FAIL}, {"text": "hmm"}, {"text": FAIL}, {"text": PASS}]})
        self.assertEqual((record["status"], len(record["attempts"])), ("failed", 2))
        self.assertEqual((len(by(ex, "worker")), len(by(ex, "reviewer"))), (2, 4))
        self.assertIn("attempt limit", record["reason"])
        self.assertFalse((self.folder(run) / "attempt-3").exists())

    def test_e8_no_judge_turn_after_the_cost_limit(self):
        self.save(check="exit 0", limits={"cost": 1.0})
        run, record, ex = self.go("duo", {"worker": [{"cost": 1.5}], "reviewer": [{"text": PASS}]})
        self.assertEqual(record["status"], "limit")
        self.assertIn("cost", record["reason"])
        self.assertEqual(by(ex, "reviewer"), [])
        self.assertEqual(record["attempts"][0]["check"]["exit"], 0)  # the check ran; the judge turn was refused

    def test_e8_a_judges_own_cost_counts_before_the_second_judge(self):
        self.save("trio", limits={"cost": 1.0})
        run, record, ex = self.go("trio", {"worker": [{"cost": 0.4}], "reviewer": [{"text": PASS, "cost": 0.7}], "verifier": [{"text": PASS}]})
        self.assertEqual(record["status"], "limit")
        self.assertEqual(by(ex, "verifier"), [])
        self.assertAlmostEqual(record["totals"]["cost"], 1.1)

    def test_e8_no_judge_turn_after_the_time_limit(self):
        self.save(limits={"seconds": 0.2})
        run, record, ex = self.go("duo", {"worker": [{"sleep": 0.4}], "reviewer": [{"text": PASS}]})
        self.assertEqual(record["status"], "limit")
        self.assertIn("seconds", record["reason"])
        self.assertEqual(by(ex, "reviewer"), [])

    def test_a_cancel_between_the_worker_and_the_judge_stops_before_the_judge(self):
        self.save()
        def hook(n, call):
            if call["agent"] == "worker":
                looprun.request_cancel(looprun.run_dir(self.project, os.listdir(self.project / ".opencode" / "loops")[0]))
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}]}, hook=hook)
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(by(ex, "reviewer"), [])

    def test_a_cancel_stops_a_running_judge(self):
        self.save()
        run, executor, thread = self.detached("duo", {"reviewer": [{"hang": True}]})
        end = time.time() + 20
        while time.time() < end and not by(executor, "reviewer"):
            time.sleep(0.05)
        looprun.request_cancel(self.folder(run))
        record = self.finished(run, thread)
        self.assertEqual(record["status"], "cancelled")


class ArtifactTests(Base):
    def writer(self, files_by_call):
        calls = {"n": 0}

        def hook(n, call):
            if call["agent"] != "worker":
                return
            calls["n"] += 1
            for name, content in files_by_call.get(calls["n"], {}).items():
                path = Path(call["cwd"]) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        return hook

    def test_e9_artifacts_are_copied_per_attempt_and_a_missing_one_sends_the_worker_back(self):
        self.save(artifacts=["report.md", "out/data.txt"])
        hook = self.writer({1: {"report.md": "# one\n"}, 2: {"report.md": "# two\n", "out/data.txt": "42\n"}})
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}]}, hook=hook)
        self.assertEqual(record["status"], "passed")
        first, second = record["attempts"]
        self.assertEqual([(s["step"], s["outcome"], s["route"]) for s in first["steps"]], [("work", "no_artifact", "work")])
        self.assertEqual(by(ex, "reviewer")[0]["message"].count("attempt-2"), 2)  # the two copies, by path
        self.assertEqual(len(by(ex, "reviewer")), 1)  # no judge was spent on attempt 1
        self.assertIn("out/data.txt", by(ex, "worker")[1]["message"])
        self.assertIn("missing", by(ex, "worker")[1]["message"].lower())
        self.assertEqual(second["feedback_from"], "artifacts")
        folder = self.folder(run)
        self.assertEqual((folder / "attempt-1" / "artifacts" / "report.md").read_text(), "# one\n")
        self.assertEqual((folder / "attempt-2" / "artifacts" / "report.md").read_text(), "# two\n")
        self.assertEqual((folder / "attempt-2" / "artifacts" / "out" / "data.txt").read_text(), "42\n")
        self.assertEqual([(a["name"], a["revision"], a["bytes"]) for a in first["artifacts"]], [("report.md", 1, 6)])
        self.assertEqual([(a["name"], a["revision"], a["bytes"]) for a in second["artifacts"]], [("report.md", 2, 6), ("out/data.txt", 2, 3)])
        for a in second["artifacts"]:
            self.assertEqual(a["sha256"], hashlib.sha256((folder / "attempt-2" / "artifacts" / a["name"]).read_bytes()).hexdigest())
        kinds = [(h["attempt"], h["kind"], h["to"]) for h in record["handoffs"]]
        self.assertEqual(kinds.count((2, "artifact", "reviewer")), 2)
        self.assertEqual(self.events(run)[:3], [("-", "started", "work"), ("work", "no_artifact", "work"), ("work", "returned", "judge:reviewer")])

    @needs_symlinks
    def test_a_file_that_is_not_there_never_passes_and_a_link_out_of_the_work_folder_is_missing(self):
        outside = self.tmp / "secret.txt"
        outside.write_text("secret\n")
        self.save(artifacts=["link.md"], attempts=2)

        def hook(n, call):
            if call["agent"] == "worker":
                link = Path(call["cwd"]) / "link.md"
                link.unlink(missing_ok=True)
                link.symlink_to(outside)
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}] * 3}, hook=hook)
        self.assertEqual(by(ex, "reviewer"), [])
        self.assertEqual(record["status"], "failed")
        self.assertFalse((self.folder(run) / "attempt-1" / "artifacts" / "link.md").exists())

    def test_the_artifact_check_comes_before_the_check_command(self):
        self.save(check="exit 0", artifacts=["report.md"], attempts=1)
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}]})
        self.assertEqual(record["attempts"][0]["check"], None)
        self.assertEqual(record["status"], "failed")


class EscalationTests(Base):
    def test_e10_escalation_after_n_attempts_runs_the_named_agent_in_a_new_session(self):
        self.save(steps={"worker": {"escalate": {"after": 1, "to": "hard"}}}, attempts=4)
        run, record, ex = self.go("duo", {"reviewer": [{"text": FAIL}, {"text": FAIL}, {"text": PASS}]})
        self.assertEqual(record["status"], "passed")
        self.assertEqual([c["agent"] for c in ex.calls], ["worker", "reviewer", "hard", "reviewer", "hard", "reviewer"])
        self.assertEqual([a["escalated_to"] for a in record["attempts"]], [None, "hard", "hard"])
        hard, worker = by(ex, "hard"), by(ex, "worker")
        self.assertNotEqual(hard[0]["session"], worker[0]["session"])
        self.assertTrue(hard[0]["first"])
        self.assertEqual(hard[0]["session"], hard[1]["session"])  # the escalated agent keeps its own session like the worker would
        self.assertIn("reviewer", hard[0]["message"])
        self.assertIn("Write report.md with a header.", hard[0]["message"])
        self.assertEqual(record["attempts"][1]["agent"]["session"], hard[0]["session"])

    def test_no_escalation_before_attempt_after_plus_one(self):
        self.save(steps={"worker": {"escalate": {"after": 3, "to": "hard"}}}, attempts=3)
        run, record, ex = self.go("duo", {"reviewer": [{"text": FAIL}] * 3})
        self.assertEqual(by(ex, "hard"), [])
        self.assertEqual(len(by(ex, "worker")), 3)
        self.assertEqual([a["escalated_to"] for a in record["attempts"]], [None, None, None])


class ApprovalTests(Base):
    def test_e11_approve_ends_the_run_passed_with_approval(self):
        self.save(approval="before_pass")
        run, executor, thread = self.detached("duo", {"reviewer": [{"text": PASS}]})
        state = self.waiting(run, thread)
        self.assertEqual(state["status"], "waiting")
        self.assertEqual((state["waiting"]["step"], state["waiting"]["outcome"]), ("judge:reviewer", "pass"))
        self.assertIn("approve", state["waiting"]["question"])
        self.assertTrue(state["waiting"]["since"])
        calls = len(executor.calls)
        time.sleep(0.5)
        self.assertEqual(len(executor.calls), calls)  # no model call while waiting
        self.assertIsNone(state.get("passed_by"))
        code, out, err = self.cli("answer", run, "approve", "--note", "ship it", "--project", str(self.project))
        self.assertEqual((code, err), (0, ""), out)
        record = self.finished(run, thread)
        self.assertEqual((record["status"], record["passed_by"], record["waiting"]), ("passed", "judges+approval", None))
        self.assertEqual([(a["answer"], a["note"], a["step"]) for a in record["answers"]], [("approve", "ship it", "judge:reviewer")])
        self.assertFalse((self.folder(run) / "answer.json").exists())
        self.assertTrue(any(p.name.startswith("answer") for p in (self.folder(run) / "attempt-1").iterdir()))
        self.assertEqual(len(executor.calls), calls)

    def test_e11_reject_sends_the_worker_back_with_the_note(self):
        self.save(approval="before_pass")
        run, executor, thread = self.detached("duo", {"reviewer": [{"text": PASS}, {"text": PASS}]})
        self.waiting(run, thread)
        self.assertEqual(self.cli("answer", run, "reject", "--note", "Use tabs, not spaces.", "--project", str(self.project))[0], 0)
        state = self.waiting(run, thread, attempts=2)
        self.assertIn("Use tabs, not spaces.", by(executor, "worker")[1]["message"])
        self.assertIn("person", by(executor, "worker")[1]["message"].lower())
        self.assertEqual(state["attempts"][1]["feedback_from"], "human")
        self.assertEqual(self.cli("answer", run, "approve", "--project", str(self.project))[0], 0)
        record = self.finished(run, thread)
        self.assertEqual((record["status"], len(record["attempts"]), record["passed_by"]), ("passed", 2, "judges+approval"))
        self.assertEqual([a["answer"] for a in record["answers"]], ["reject", "approve"])

    def test_e11_stop_ends_the_run_cancelled(self):
        self.save(approval="before_pass")
        run, executor, thread = self.detached("duo", {"reviewer": [{"text": PASS}]})
        self.waiting(run, thread)
        self.assertEqual(self.cli("answer", run, "stop", "--project", str(self.project))[0], 0)
        record = self.finished(run, thread)
        self.assertEqual((record["status"], record["waiting"], record.get("passed_by")), ("cancelled", None, None))

    def test_e11_cancel_while_waiting(self):
        self.save(approval="before_pass")
        run, executor, thread = self.detached("duo", {"reviewer": [{"text": PASS}]})
        self.waiting(run, thread)
        self.assertEqual(self.cli("cancel", run, "--project", str(self.project))[0], 0)
        record = self.finished(run, thread)
        self.assertEqual((record["status"], record["waiting"]), ("cancelled", None))
        self.assertIsNone(record.get("passed_by"))

    def test_a_route_to_human_asks_and_approve_follows_the_pass_route(self):
        self.save("trio", steps={"reviewer": {"on": {"fail": "human"}}})
        run, executor, thread = self.detached("trio", {"reviewer": [{"text": FAIL}], "verifier": [{"text": PASS}]})
        state = self.waiting(run, thread)
        self.assertEqual((state["waiting"]["step"], state["waiting"]["outcome"]), ("judge:reviewer", "fail"))
        self.assertEqual(by(executor, "verifier"), [])
        self.assertEqual(self.cli("answer", run, "approve", "--project", str(self.project))[0], 0)
        record = self.finished(run, thread)
        self.assertEqual((record["status"], record["passed_by"]), ("passed", "judges+approval"))  # the person overrode one judge; the other still ran
        self.assertEqual(len(by(executor, "verifier")), 1)

    def test_a_route_to_human_reject_goes_back_to_the_worker(self):
        self.save(steps={"reviewer": {"on": {"fail": "human"}}})
        run, executor, thread = self.detached("duo", {"reviewer": [{"text": FAIL}, {"text": PASS}]})
        self.waiting(run, thread)
        self.cli("answer", run, "reject", "--note", "try the other way", "--project", str(self.project))
        record = self.finished(run, thread)
        self.assertEqual((record["status"], len(record["attempts"]), record["passed_by"]), ("passed", 2, "judges"))
        self.assertIn("try the other way", by(executor, "worker")[1]["message"])

    def test_answer_is_refused_unless_the_run_is_waiting(self):
        self.save()
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}]})
        code, _, err = self.cli("answer", run, "approve", "--project", str(self.project))
        self.assertEqual(code, 1)
        self.assertIn("not waiting", err)
        self.assertFalse((self.folder(run) / "answer.json").exists())
        self.assertEqual(self.cli("answer", run, "maybe", "--project", str(self.project))[0], 2)

    def test_a_passed_run_with_approval_never_passes_without_the_answer(self):
        self.save(approval="before_pass", limits={"seconds": 0.6})
        run, executor, thread = self.detached("duo", {"reviewer": [{"text": PASS}]})
        record = self.finished(run, thread)
        self.assertEqual(record["status"], "limit")  # waiting time counts toward the seconds limit
        self.assertIn("waiting", record["reason"])
        self.assertIsNone(record.get("passed_by"))


class GuardTests(Base):
    def test_e12_a_judge_that_says_pass_while_the_check_fails_can_never_give_passed(self):
        self.save(check="exit 1", attempts=3)
        run, record, ex = self.go("duo", {"reviewer": [{"text": PASS}] * 5})
        self.assertEqual(by(ex, "reviewer"), [])  # the judge was never asked while the check fails
        self.assertEqual((record["status"], len(record["attempts"])), ("failed", 3))
        self.assertIsNone(record.get("passed_by"))

    def edited(self, edit, check="exit 1"):
        self.save(check=check, attempts=2)
        ex = looprun.ScriptedExecutor({"reviewer": [{"text": PASS}] * 3})
        run = looprun.start(self.project, "duo", structure=self.structure, goal="g", executor=ex)
        folder = self.folder(run)
        definition = json.loads((folder / "definition.json").read_text())
        edit(definition["steps"])
        definition["steps_sha256"] = looprun.steps_hash(definition["steps"])  # re-pinned: the walk's own guard is what is tested here
        (folder / "definition.json").write_text(json.dumps(definition))
        record = json.loads((folder / "run.json").read_text())
        record["steps_sha256"] = definition["steps_sha256"]  # a deliberate edit of both files: the pins do not stop it, the walk's guard does
        (folder / "run.json").write_text(json.dumps(record))
        looprun.execute(folder, ex)
        return looprun.read_run(folder), ex

    def test_e12_a_hand_edited_definition_that_routes_a_failing_check_to_the_judge_still_cannot_pass(self):
        def edit(steps):
            steps[1]["on"]["fail"] = "judge:reviewer"
        record, ex = self.edited(edit)
        self.assertEqual(len(by(ex, "reviewer")), 1)  # the edit made the judge run
        self.assertNotEqual(record["status"], "passed")
        self.assertIn("check", record["reason"])

    def test_a_hand_edited_definition_that_skips_the_check_cannot_pass(self):
        def edit(steps):
            steps[0]["on"]["returned"] = "judge:reviewer"
        record, ex = self.edited(edit, check="exit 0")
        self.assertNotEqual(record["status"], "passed")
        self.assertIsNone(record["attempts"][0]["check"])

    def test_a_hand_edited_definition_that_skips_a_judge_cannot_pass(self):
        self.save("trio", attempts=1)
        ex = looprun.ScriptedExecutor({"reviewer": [{"text": PASS}], "verifier": [{"text": PASS}]})
        run = looprun.start(self.project, "trio", structure=self.structure, goal="g", executor=ex)
        folder = self.folder(run)
        definition = json.loads((folder / "definition.json").read_text())
        definition["steps"][1]["on"]["pass"] = "done"
        definition["steps_sha256"] = looprun.steps_hash(definition["steps"])  # re-pinned: the walk's own guard is what is tested here
        (folder / "definition.json").write_text(json.dumps(definition))
        record = json.loads((folder / "run.json").read_text())
        record["steps_sha256"] = definition["steps_sha256"]  # a deliberate edit of both files: the pins do not stop it, the walk's guard does
        (folder / "run.json").write_text(json.dumps(record))
        looprun.execute(folder, ex)
        self.assertNotEqual(looprun.read_run(folder)["status"], "passed")
        self.assertEqual(by(ex, "verifier"), [])

    def test_a_done_route_from_a_failing_judge_is_refused(self):
        def edit(steps):
            steps[1]["on"]["fail"] = "done"
        self.save(attempts=2)
        ex = looprun.ScriptedExecutor({"reviewer": [{"text": FAIL}]})
        run = looprun.start(self.project, "duo", structure=self.structure, goal="g", executor=ex)
        folder = self.folder(run)
        definition = json.loads((folder / "definition.json").read_text())
        edit(definition["steps"])
        definition["steps_sha256"] = looprun.steps_hash(definition["steps"])  # re-pinned: the walk's own guard is what is tested here
        (folder / "definition.json").write_text(json.dumps(definition))
        record = json.loads((folder / "run.json").read_text())
        record["steps_sha256"] = definition["steps_sha256"]  # a deliberate edit of both files: the pins do not stop it, the walk's guard does
        (folder / "run.json").write_text(json.dumps(record))
        looprun.execute(folder, ex)
        self.assertNotEqual(looprun.read_run(folder)["status"], "passed")

    def test_no_reply_text_or_failure_of_any_kind_yields_passed(self):
        cases = [({"worker": [{"error": "boom"}], "reviewer": [{"text": PASS}]}, "exit 0"),
                 ({"reviewer": [{"text": "VERDICT: FAIL"}] * 2}, "exit 0"), ({"reviewer": [{"error": "x"}] * 3}, "exit 0"),
                 ({"reviewer": [{"text": PASS}]}, "exit 75"), ({"reviewer": [{"text": PASS}]}, "nonexistent-zzz"),
                 ({"worker": [{"text": "VERDICT: PASS"}], "reviewer": [{"text": "VERDICT: PASS\nno"}] * 2}, "exit 0")]
        for script, check in cases:
            with self.subTest(script=script, check=check):
                self.save(check=check, attempts=2)
                run, record, ex = self.go("duo", script)
                self.assertNotEqual(record["status"], "passed")
                self.assertIsNone(record.get("passed_by"))


class ExecutorTests(unittest.TestCase):
    def test_each_agents_turns_are_scripted_separately_and_everything_sent_can_be_read_back(self):
        ex = looprun.ScriptedExecutor({"worker": [{"text": "w1"}, {"text": "w2"}], "judge": [{"text": "j1"}]})
        args = dict(first=True, title="t", timeout=None, env={}, cwd="/", auto=False, poll=lambda: False)
        self.assertEqual(ex.attempt(agent="judge", session="ses_a", message="m1", **args)["text"], "j1")
        self.assertEqual(ex.attempt(agent="worker", session="ses_b", message="m2", **args)["text"], "w1")
        self.assertEqual(ex.attempt(agent="worker", session="ses_b", message="m3", **args)["text"], "w2")
        self.assertEqual(ex.attempt(agent="judge", session="ses_c", message="m4", **args)["text"], "")  # exhausted: an empty reply
        self.assertEqual([(c["agent"], c["session"], c["message"]) for c in ex.calls],
                         [("judge", "ses_a", "m1"), ("worker", "ses_b", "m2"), ("worker", "ses_b", "m3"), ("judge", "ses_c", "m4")])
        self.assertEqual(ex.sessions("worker"), ["ses_b", "ses_b"])
        self.assertEqual([c["message"] for c in ex.by_agent("judge")], ["m1", "m4"])
        self.assertEqual(ex.spec()["kind"], "scripted")
        json.dumps(ex.spec())
        flat = looprun.ScriptedExecutor([{"text": "a"}, {"error": "x"}])  # a list is still one script in call order
        self.assertEqual(flat.attempt(agent="x", session="s", message="m", **args)["text"], "a")
        self.assertEqual(flat.attempt(agent="y", session="s", message="m", **args)["outcome"], "agent_error")
        self.assertEqual(looprun.make_executor(ex.spec()).attempt(agent="worker", session="s", message="m", **args)["text"], "w1")


class SettingsTests(Base):
    def good(self):
        return {"version": 1, "flow": "trio", "limits": {"attempts": 3},
                "steps": {"verifier": {"session": "fresh", "handoff": "none", "retries": 1,
                                       "on": {"pass": "next", "fail": "worker", "inconclusive": "stop", "agent_error": "stop", "stalled": "stop"}},
                          "worker": {"session": "reuse", "retries": 0, "escalate": {"after": 2, "to": "hard"}}},
                "artifacts": ["report.md"], "approval": "before_pass"}

    def test_the_new_keys_validate_and_round_trip(self):
        out = looprun.validate(self.good(), Path("p.json"))
        self.assertEqual(out["artifacts"], ["report.md"])
        self.assertEqual(out["approval"], "before_pass")
        self.assertEqual(out["steps"]["worker"]["escalate"], {"after": 2, "to": "hard"})
        looprun.save_settings(self.structure, "trio", self.good())
        self.assertEqual(looprun.load_settings(self.structure, "trio"), out)
        plain = looprun.validate({"version": 1, "flow": "trio", "limits": {"attempts": 2}}, Path("p.json"))
        for key in ("steps", "artifacts", "approval"):
            self.assertNotIn(key, plain)
        self.assertNotIn("approval", looprun.validate({"version": 1, "flow": "trio", "limits": {"attempts": 2}, "approval": "none"}, Path("p.json")))

    def test_every_refusal_names_the_file_and_the_problem(self):
        for mutate, words in ((lambda s: s["steps"]["worker"].update(retries=9), "retries"), (lambda s: s["steps"]["worker"].update(color=1), "color"),
                              (lambda s: s.update(approval="x"), "approval"), (lambda s: s["steps"]["verifier"]["on"].update(pass_=1), "pass_")):
            s = self.good()
            mutate(s)
            with self.assertRaises(looprun.LoopError) as caught:
                looprun.validate(s, Path("p.json"))
            self.assertIn("p.json", str(caught.exception))
            self.assertIn(words, str(caught.exception))


class CommandTests(Base):
    def test_set_options_for_steps_artifacts_and_approval(self):
        s = str(self.structure)
        steps = self.tmp / "steps.json"
        steps.write_text(json.dumps({"worker": {"retries": 1}, "reviewer": {"on": {"fail": "worker"}}}))
        code, out, err = self.cli("set", "duo", "--attempts", "3", "--steps-file", str(steps), "--artifact", "report.md", "--artifact", "out/x.txt",
                                  "--approval", "before_pass", "--structure", s)
        self.assertEqual((code, err), (0, ""), out)
        saved = looprun.load_settings(self.structure, "duo")
        self.assertEqual((saved["artifacts"], saved["approval"], saved["steps"]["worker"]["retries"]), (["report.md", "out/x.txt"], "before_pass", 1))
        code, out, _ = self.cli("show", "duo", "--structure", s)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("approval", out)
        self.assertIn("report.md", out)
        self.assertEqual(self.cli("set", "duo", "--approval", "none", "--structure", s)[0], 0)  # a change keeps the rest
        saved = looprun.load_settings(self.structure, "duo")
        self.assertNotIn("approval", saved)
        self.assertEqual(saved["artifacts"], ["report.md", "out/x.txt"])
        self.assertEqual(self.cli("set", "duo", "--no-artifacts", "--structure", s)[0], 0)
        self.assertNotIn("artifacts", looprun.load_settings(self.structure, "duo"))

    def test_set_refuses_what_compile_refuses_and_saves_nothing(self):
        s = str(self.structure)
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps({"reviewer": {"on": {"fail": "hard"}}}))
        code, _, err = self.cli("set", "duo", "--attempts", "3", "--steps-file", str(bad), "--structure", s)
        self.assertEqual(code, 2)
        self.assertIn("not on this path", err)
        self.assertFalse((self.structure / ".loops").exists())
        code, _, err = self.cli("set", "open", "--attempts", "3", "--structure", s)
        self.assertEqual(code, 2)
        self.assertIn("does not loop back", err)
        self.assertEqual(self.cli("set", "self", "--check", "true", "--attempts", "3", "--approval", "before_pass", "--structure", s)[0], 2)
        bad.write_text("{nope")
        self.assertEqual(self.cli("set", "duo", "--attempts", "3", "--steps-file", str(bad), "--structure", s)[0], 2)
        self.assertEqual(self.cli("set", "duo", "--attempts", "3", "--steps-file", str(self.tmp / "none.json"), "--structure", s)[0], 2)
        self.assertEqual(self.cli("set", "duo", "--attempts", "3", "--approval", "sometimes", "--structure", s)[0], 2)

    def test_status_shows_every_step_of_every_attempt_in_order(self):
        self.save("trio", check="exit 0")
        run, record, ex = self.go("trio", {"reviewer": [{"text": PASS}, {"text": PASS}], "verifier": [{"text": FAIL}, {"text": PASS}]})
        code, out, _ = self.cli("status", run, "--project", str(self.project))
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("passed", lines[0])
        self.assertIn("check+judges", lines[0])
        rows = [ln for ln in lines[1:] if ln.strip()]
        text = "\n".join(rows)
        order = [text.index(x) for x in ("work", "check", "judge:reviewer", "judge:verifier")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(len(rows), 8)  # two attempts of four steps each, one line per step
        mine = [ln for ln in rows if ln.split()[2] == "judge:verifier"]  # the step column, not a route into it
        self.assertEqual(len(mine), 2)
        self.assertIn("fail", mine[0])
        self.assertIn("-> work", mine[0])
        self.assertIn("-> done", mine[1])
        self.assertEqual(len(self.cli("list", "--project", str(self.project))[1].strip().splitlines()), 1)

    def test_status_of_a_waiting_run_says_so(self):
        self.save(approval="before_pass")
        run, executor, thread = self.detached("duo", {"reviewer": [{"text": PASS}]})
        self.waiting(run, thread)
        code, out, _ = self.cli("status", run, "--project", str(self.project))
        self.assertIn("waiting", out.splitlines()[0])
        self.assertIn("judge:reviewer", out.splitlines()[0])
        self.assertEqual(json.loads(self.cli("status", run, "--json", "--project", str(self.project))[1])["status"], "waiting")
        self.cli("answer", run, "stop", "--project", str(self.project))
        self.finished(run, thread)

    def test_answer_twice_is_refused_and_the_first_answer_stands(self):
        self.save(approval="before_pass")
        run, executor, thread = self.detached("duo", {"reviewer": [{"text": PASS}]})
        self.waiting(run, thread)
        looprun.answer(self.folder(run), "approve", "first")
        with self.assertRaises(looprun.LoopError):
            looprun.answer(self.folder(run), "reject", "second")
        record = self.finished(run, thread)
        self.assertEqual((record["status"], [a["note"] for a in record["answers"]]), ("passed", ["first"]))

    def test_release_1_loops_still_start_by_the_cli_and_record_passed_by_check(self):
        self.save("self", check="exit 0")
        run, record, _ = self.go("self", [{}])
        self.assertEqual((record["status"], record["passed_by"]), ("passed", "check"))
        self.assertIsNone(record["waiting"])
        self.assertEqual(record["handoffs"], [])


if __name__ == "__main__":
    unittest.main()
