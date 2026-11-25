"""Timed loops (agentgraph.looptime and its hooks in agentgraph.looprun): the interval and timebox kinds, the watch command, the notes file and
fresh sessions, resume, the supervisor, the off switch and the status. They run on the scripted executor and a fake clock that never sleeps;
the only real waits are the watch commands' own (a few milliseconds each). No test starts OpenCode."""
import contextlib
import datetime
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from agentgraph import looprun, looptime, osfs
from tests.shellskip import sh_or, sleep_command
from tests.test_looprun import Base

T0 = 1_800_000_000.0
WORK_OK = sh_or("test -f ok", "import os; raise SystemExit(0 if os.path.exists('ok') else 1)")


class Died(BaseException):
    """A controller that is killed: nothing in the controller catches it, so the record stays as it was."""


class FakeClock:
    """The time and the wait of a test: `sleep` moves the time and returns at once. `on_sleep(clock)` runs at every sleep."""

    def __init__(self, start=T0, on_sleep=None):
        self.t, self.on_sleep, self.sleeps = start, on_sleep, 0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps += 1
        if self.on_sleep:
            self.on_sleep(self)
        self.t += seconds


class TimedExecutor(looprun.ScriptedExecutor):
    """The scripted executor with the fake clock: a turn takes `takes` seconds of fake time (cut at `timeout`: then it is `stalled`) and `ok` makes
    the file the check looks for. Records every call as the scripted executor does."""

    def __init__(self, script, clock, dies_at=None):
        super().__init__(script)
        self.clock, self.dies_at = clock, dies_at

    def attempt(self, *, agent, session, first, title, message, timeout, env, cwd, auto, poll) -> dict:
        self.calls.append({"agent": agent, "session": session, "first": first, "title": title, "message": message, "timeout": timeout, "cwd": str(cwd), "auto": auto})
        item = self._item(agent)
        if self.dies_at == len(self.calls):
            raise Died()
        takes = float(item.get("takes", 0))
        stalled = bool(timeout) and takes > timeout
        self.clock.t += timeout if stalled else takes
        if item.get("ok"):
            Path(cwd, "ok").write_text("done\n")
        return {"exit": None if stalled else 0, "outcome": "stalled" if stalled else "returned", "session": session, "cost": item.get("cost", 0.01), "tokens": None,
                "sessions": None, "seconds": takes, "error": None, "text": item.get("text", ""), "final": item.get("text", "")}


def watch_script(*codes):
    """A shell command whose nth run exits with the nth code (the last one for every run after that); it prints a line naming the run."""
    cases = "".join(f"{n}) exit {code};; " for n, code in enumerate(codes[:-1], 1))
    return sh_or(f'n=$(cat .watch-count 2>/dev/null || echo 0); n=$((n+1)); echo $n > .watch-count; echo "watch run $n says news"; '
                 f"case $n in {cases}*) exit {codes[-1]};; esac",
                 "import os; n = int(open('.watch-count').read()) + 1 if os.path.exists('.watch-count') else 1; open('.watch-count', 'w').write(str(n)); "
                 f"print('watch run', n, 'says news'); codes = {list(codes)}; raise SystemExit(codes[n - 1] if n <= len(codes) else codes[-1])")


def iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()


class TimedBase(Base):
    def timed(self, timing, check=WORK_OK, attempts=3, cost=5, limits=None, **top):
        settings = {"version": 1, "flow": "self", "limits": {"attempt_seconds": 1000, **({"attempts": attempts} if attempts else {}), **({"cost": cost} if cost else {}), **(limits or {})},
                    "timing": timing, **top}
        if check:
            settings["check"] = {"command": check, "timeout": 20}
        looprun.save_settings(self.structure, "self", settings)
        return settings

    def run_timed(self, script=None, clock=None, goal="Write ok.", **kw):
        clock = clock or FakeClock()
        executor = TimedExecutor(script or [{"ok": True}] * 10, clock)
        run = looprun.start(self.project, "self", structure=self.structure, goal=goal, executor=executor, wait=True, clock=clock, supervisor=False, **kw)
        return looprun.read_run(self.folder(run)), executor, clock, run

    def events(self, run):
        return [json.loads(line) for line in (self.folder(run) / "events.jsonl").read_text().splitlines()]

    def die(self, clock, script=None, executor=None, supervisor=False):
        """Start a timed run whose controller is killed (when `clock` says so, or in the executor's turn); the run folder it left."""
        executor = executor or TimedExecutor(script or [{"ok": True}] * 10, clock)
        before = set(looprun.list_runs(self.project))
        with self.assertRaises(Died):
            looprun.start(self.project, "self", structure=self.structure, goal="Write ok.", executor=executor, wait=True, clock=clock, supervisor=supervisor)
        (new,) = set(looprun.list_runs(self.project)) - before
        return self.folder(new)

    def dying_clock(self, at):
        def kill(clock):
            if clock.t >= at:
                clock.on_sleep = None
                raise Died()
        return FakeClock(on_sleep=kill)

    def resume(self, folder, clock, script=None):
        return looprun.resume(folder, wait=True, executor=TimedExecutor(script or [{"ok": True}] * 10, clock), clock=clock)


class SettingsTests(TimedBase):
    def test_a_loop_without_timing_validates_and_runs_as_before(self):
        settings = self.save()
        self.assertNotIn("timing", looprun.validate(settings, "x"))
        run, record, ex = self.go([{"cost": 0.1}, {"cost": 0.1}])
        self.assertEqual(record["status"], "passed")
        self.assertNotIn("timing", record)
        self.assertNotIn("timing", looprun.read_definition(self.folder(run))["settings"])
        self.assertFalse((self.project / ".opencode" / "loop-notes").exists())
        self.assertNotIn("notes file", ex.calls[0]["message"])
        self.assertEqual(looprun.looptime.summary(record), "")

    def test_a_timed_loop_needs_a_spend_limit_and_an_end(self):
        for timing in ({"kind": "interval", "every": 20, "ticks": 3}, {"kind": "timebox", "for": 600}):
            with self.assertRaisesRegex(looprun.LoopError, "spend limit.*limits.cost"):
                looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2, "attempt_seconds": 100}, "timing": timing}, "x")
        with self.assertRaisesRegex(looprun.LoopError, "needs an end.*until.*for.*ticks"):
            looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2, "cost": 1, "attempt_seconds": 100}, "timing": {"kind": "interval", "every": 20}}, "x")
        with self.assertRaisesRegex(looprun.LoopError, "needs an end"):
            looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"cost": 1, "attempt_seconds": 100}, "timing": {"kind": "timebox"}}, "x")
        with self.assertRaisesRegex(looprun.LoopError, "needs a spend limit|spend limit"):
            self.timed({"kind": "interval", "every": 20, "ticks": 3}, cost=None)

    def test_the_rest_of_the_block_is_checked(self):
        base = {"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2, "cost": 1, "attempt_seconds": 100}}
        for timing, word in (({"kind": "interval", "every": 5, "ticks": 3}, "every"), ({"kind": "interval", "every": 90000, "ticks": 3}, "every"),
                             ({"kind": "interval", "every": 20, "ticks": 3, "oops": 1}, "unknown key 'oops'"), ({"kind": "daily"}, "kind"),
                             ({"kind": "interval", "every": 20, "until": "2030-01-01T00:00:00"}, "no offset"), ({"kind": "interval", "every": 20, "until": "soon"}, "ISO 8601"),
                             ({"kind": "interval", "every": 20, "ticks": 3, "wake_codes": [10]}, "needs timing.watch"),
                             ({"kind": "interval", "every": 20, "ticks": 3, "watch": "x", "wake_codes": [3], "done_codes": [3]}, "share"),
                             ({"kind": "timebox", "for": 60, "every": 20}, "interval loop"), ({"kind": "timebox", "for": 60, "notes": "../x.md"}, "inside the work folder"),
                             ({"kind": "timebox", "for": 60, "notes": "/etc/x.md"}, "inside the work folder")):
            with self.subTest(timing=timing), self.assertRaisesRegex(looprun.LoopError, word):
                looprun.validate({**base, "timing": timing}, "x")
        # an interval loop and every untimed loop still need limits.attempts; only a timebox may leave it out
        with self.assertRaisesRegex(looprun.LoopError, "attempts"):
            looprun.validate({**base, "limits": {"cost": 1, "attempt_seconds": 100}, "timing": {"kind": "interval", "every": 20, "ticks": 3}}, "x")
        with self.assertRaisesRegex(looprun.LoopError, "attempts"):
            looprun.validate({**base, "limits": {"cost": 1}}, "x")
        ok = looprun.validate({**base, "limits": {"cost": 1, "attempt_seconds": 100}, "timing": {"kind": "timebox", "until": iso(T0 + 100)}}, "x")
        self.assertEqual(ok["limits"], {"cost": 1, "attempt_seconds": 100})
        self.assertEqual(ok["timing"]["min_attempt_seconds"], 60)
        self.assertEqual(ok["timing"]["cache_seconds"], 300)
        full = looprun.validate({**base, "timing": {"kind": "interval", "every": 20, "ticks": 3, "watch": "true"}}, "x")["timing"]
        self.assertEqual((full["watch_seconds"], full["wake_codes"], full["done_codes"]), (60, [10], [11]))

    def test_show_and_set_carry_the_timing(self):
        file = self.tmp / "timing.json"
        file.write_text(json.dumps({"kind": "interval", "every": 30, "ticks": 4}))
        code, out, err = self.cli("set", "self", "--check", "true", "--attempts", "2", "--cost", "1", "--attempt-seconds", "100", "--timing-file", str(file), "--structure", str(self.structure))
        self.assertEqual(code, 0, err)
        code, out, _ = self.cli("show", "self", "--structure", str(self.structure))
        self.assertIn("timing interval every 30s end ticks 4", out)
        code, out, err = self.cli("set", "self", "--no-timing", "--structure", str(self.structure))
        self.assertEqual(code, 0, err)
        self.assertNotIn("timing", self.cli("show", "self", "--structure", str(self.structure))[1])


class IntervalTests(TimedBase):
    def test_idle_ticks_make_no_executor_call(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 3, "watch": watch_script(0)})
        record, ex, clock, run = self.run_timed()
        self.assertEqual(ex.calls, [])
        self.assertEqual((record["status"], record["timing"]["counts"]), ("limit", {"idle": 3, "woken": 0, "skipped": 0, "failed": 0}))
        self.assertIn("3 ticks: 3 idle, 0 woken, 0 skipped, 0 failed", record["reason"])
        self.assertEqual((record["sessions"], record["attempts"], record["totals"]["cost"]), ([], [], None))
        self.assertEqual(clock.t - T0, 40)  # ticks 0, 1 and 2 at 0, 20 and 40 seconds
        self.assertEqual([t["result"] for t in record["timing"]["ticks"]], ["idle"] * 3)

    def test_a_wake_code_runs_the_steps_and_hands_over_the_watch_output(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 4, "watch": watch_script(0, 0, 10, 0)})
        record, ex, clock, run = self.run_timed([{"ok": True, "cost": 0.05}])
        self.assertEqual(len(ex.calls), 1)
        self.assertEqual(record["status"], "limit")
        self.assertEqual(record["timing"]["counts"], {"idle": 3, "woken": 1, "skipped": 0, "failed": 0})
        self.assertEqual(record["timing"]["wakes"], 1)  # one wake started, counted for readers
        self.assertIn("watch run 3 says news", ex.calls[0]["message"])
        self.assertIn("exited 10", ex.calls[0]["message"])
        wake = [t for t in record["timing"]["ticks"] if t["result"] == "wake_passed"]
        self.assertEqual((len(wake), wake[0]["k"], wake[0]["attempts"], wake[0]["cost"]), (1, 2, 1, 0.05))
        self.assertEqual(record["timing"]["last"]["result"], "idle")
        self.assertAlmostEqual(record["totals"]["cost"], 0.05)
        self.assertAlmostEqual(sum(record["timing"]["spend_by_day"].values()), 0.05)

    def test_a_done_code_ends_the_run_passed(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 9, "watch": watch_script(0, 11)})
        record, ex, clock, run = self.run_timed()
        self.assertEqual((record["status"], record["passed_by"], ex.calls), ("passed", "watch", []))
        self.assertIn("exited 11", record["reason"])
        self.assertEqual(record["timing"]["counts"]["idle"], 1)

    def test_the_wake_and_done_codes_can_be_changed(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 5, "watch": watch_script(0, 7, 9), "wake_codes": [7], "done_codes": [9]})
        record, ex, clock, run = self.run_timed()
        self.assertEqual((record["status"], len(ex.calls)), ("passed", 1))

    def test_three_watch_errors_in_a_row_end_check_error(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 9, "watch": watch_script(3, 3, 3)})
        record, ex, clock, run = self.run_timed()
        self.assertEqual((record["status"], ex.calls), ("check_error", []))
        self.assertIn("3 times in a row", record["reason"])
        self.assertEqual(record["timing"]["counts"]["failed"], 3)

    def test_two_errors_then_an_idle_tick_do_not_end_the_run(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 5, "watch": watch_script(3, 3, 0, 3, 3, 0)})
        record, ex, clock, run = self.run_timed()
        self.assertEqual((record["status"], record["timing"]["counts"]), ("limit", {"idle": 1, "woken": 0, "skipped": 0, "failed": 4}))

    def test_a_watch_that_runs_too_long_is_an_error(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 9, "watch": sleep_command(5), "watch_seconds": 0.3})
        record, ex, clock, run = self.run_timed()
        self.assertEqual(record["status"], "check_error")
        self.assertIn("timed out", record["timing"]["last"]["error"])

    def test_ticks_for_and_until_each_end_the_run(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 4, "watch": watch_script(0)})
        record, _, clock, _ = self.run_timed()
        self.assertEqual((record["status"], record["timing"]["counts"]["idle"], clock.t - T0), ("limit", 4, 30))
        self.assertIn("ticks (4) reached", record["reason"])
        self.timed({"kind": "interval", "every": 10, "for": 35, "watch": watch_script(0)})
        record, _, clock, _ = self.run_timed()
        self.assertEqual((record["status"], record["timing"]["counts"]["idle"]), ("limit", 4))  # ticks at 0, 10, 20, 30; the one at 40 is past the end
        self.assertIn("for 35 seconds", record["reason"])
        self.assertEqual(record["timing"]["deadline"], T0 + 35)
        self.timed({"kind": "interval", "every": 10, "until": iso(T0 + 25), "watch": watch_script(0)})
        record, _, clock, _ = self.run_timed()
        self.assertEqual((record["status"], record["timing"]["counts"]["idle"], record["timing"]["deadline"]), ("limit", 3, T0 + 25))
        self.timed({"kind": "interval", "every": 10, "until": T0 + 15, "watch": watch_script(0)})
        record, _, clock, _ = self.run_timed()
        self.assertEqual((record["status"], record["timing"]["counts"]["idle"]), ("limit", 2))
        self.timed({"kind": "interval", "every": 10, "ticks": 50, "for": 12, "watch": watch_script(0)})  # the first end that comes wins
        record, _, clock, _ = self.run_timed()
        self.assertEqual(record["timing"]["counts"]["idle"], 2)

    def test_with_no_watch_every_tick_wakes_the_agent(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 3})
        record, ex, clock, run = self.run_timed()
        self.assertEqual((len(ex.calls), record["timing"]["counts"]["woken"]), (3, 3))
        self.assertIn("wakes you at tick 0", ex.calls[0]["message"])

    def test_a_tick_that_comes_due_during_a_wake_is_skipped_and_counted(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 5})
        record, ex, clock, run = self.run_timed([{"ok": True, "takes": 25}] * 5)
        self.assertEqual(len(ex.calls), 2)  # ticks 0 and 3 (the wake of tick 0 lasted until 25 seconds); 1 and 2 were skipped; 4 came due during the second wake
        self.assertEqual(record["timing"]["counts"], {"idle": 0, "woken": 2, "skipped": 3, "failed": 0})
        skipped = [e for e in self.events(run) if e["outcome"] == "skipped"]
        self.assertEqual([(e["first"], e["last"]) for e in skipped], [(1, 2), (4, 4)])
        self.assertIn("2 woken, 3 skipped", record["reason"])

    def test_a_failed_wake_does_not_end_the_run_unless_stop_on_fail(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 2}, check="exit 1", attempts=2)
        record, ex, clock, run = self.run_timed()
        self.assertEqual((record["status"], len(ex.calls), record["timing"]["counts"]), ("limit", 4, {"idle": 0, "woken": 0, "skipped": 0, "failed": 2}))
        self.assertEqual([a["n"] for a in record["attempts"]], [1, 2, 3, 4])
        self.assertIn("attempt 1 of 2", ex.calls[0]["message"])
        self.assertIn("attempt 1 of 2", ex.calls[2]["message"])  # the attempt limit counts per wake
        self.timed({"kind": "interval", "every": 10, "ticks": 2, "stop_on_fail": True}, check="exit 1", attempts=2)
        record, ex, clock, run = self.run_timed()
        self.assertEqual((record["status"], len(ex.calls)), ("failed", 2))
        self.assertIn("stop_on_fail", record["reason"])

    def test_the_spend_limit_holds_across_ticks(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 9}, check="exit 1", attempts=1, cost=0.15)
        record, ex, clock, run = self.run_timed([{"cost": 0.1}] * 9)
        self.assertEqual((record["status"], len(ex.calls)), ("limit", 2))
        self.assertIn("cost", record["reason"])
        self.assertAlmostEqual(record["totals"]["cost"], 0.2)
        self.assertAlmostEqual(sum(record["timing"]["spend_by_day"].values()), 0.2)

    def test_cancel_while_waiting_ends_cancelled(self):
        self.timed({"kind": "interval", "every": 100, "ticks": 5, "watch": watch_script(0)})

        def ask(clock):
            if clock.t > T0 + 130:
                (self.folder(looprun.list_runs(self.project)[0]) / "cancel").write_text("1\n")
        record, ex, clock, run = self.run_timed(clock=FakeClock(on_sleep=ask))
        self.assertEqual((record["status"], ex.calls), ("cancelled", []))
        self.assertIn("waiting for the next tick", record["reason"])

    def test_pause_while_waiting_then_resume_goes_on_with_the_schedule(self):
        self.timed({"kind": "interval", "every": 100, "ticks": 3, "watch": watch_script(0)})

        def ask(clock):
            if clock.t > T0 + 30:
                clock.on_sleep = None
                (self.folder(looprun.list_runs(self.project)[0]) / "pause").write_text("1\n")
        record, ex, clock, run = self.run_timed(clock=FakeClock(on_sleep=ask))
        self.assertEqual(record["status"], "paused")
        self.resume(self.folder(run), clock)
        record = looprun.read_run(self.folder(run))
        self.assertEqual((record["status"], record["timing"]["counts"]["idle"]), ("limit", 3))
        self.assertEqual(record["timing"]["anchor"], T0)


class TimeboxTests(TimedBase):
    def test_a_time_box_stops_at_its_deadline_and_shortens_the_last_attempt(self):
        self.timed({"kind": "timebox", "for": 100, "min_attempt_seconds": 10}, check="exit 1", attempts=None, limits={"attempt_seconds": 1000})
        record, ex, clock, run = self.run_timed([{"takes": 60}, {"takes": 60}, {"takes": 60}])
        self.assertEqual(record["status"], "limit")
        self.assertTrue(record["reason"].startswith("deadline"), record["reason"])
        self.assertEqual([c["timeout"] for c in ex.calls], [100, 40])  # the second turn gets the 40 seconds that are left, not 1000
        self.assertEqual(record["attempts"][1]["agent"]["outcome"], "stalled")  # and is stopped at the deadline
        self.assertEqual(clock.t - T0, 100)
        self.assertIn("Loop run, attempt 1.", ex.calls[0]["message"])  # no attempt limit to name

    def test_no_attempt_starts_with_less_than_the_minimum_left(self):
        self.timed({"kind": "timebox", "until": iso(T0 + 100), "min_attempt_seconds": 30}, check="exit 1", attempts=None)
        record, ex, clock, run = self.run_timed([{"takes": 75}] * 3)
        self.assertEqual((record["status"], len(ex.calls)), ("limit", 1))  # 25 seconds are left: under the 30 an attempt needs
        self.assertIn("only 25 seconds are left", record["reason"])
        self.assertEqual(clock.t - T0, 75)

    def test_a_time_box_ends_passed_when_the_check_passes(self):
        self.timed({"kind": "timebox", "for": 1000}, attempts=None)
        record, ex, clock, run = self.run_timed([{"takes": 5, "ok": True}])
        self.assertEqual((record["status"], len(ex.calls)), ("passed", 1))

    def test_a_time_box_with_no_attempt_limit_repeats_until_the_check_passes(self):
        self.timed({"kind": "timebox", "for": 10000}, attempts=None)
        record, ex, clock, run = self.run_timed([{"takes": 5}] * 7 + [{"takes": 5, "ok": True}])
        self.assertEqual((record["status"], len(ex.calls)), ("passed", 8))

    def test_the_deadline_is_a_wall_clock_time_that_a_dead_controller_does_not_stop(self):
        self.timed({"kind": "timebox", "for": 100, "min_attempt_seconds": 10}, check="exit 1", attempts=None)
        clock = FakeClock()
        folder = self.die(clock, executor=TimedExecutor([{"takes": 10}] * 20, clock, dies_at=3))
        self.assertEqual(looprun.read_run(folder)["timing"]["deadline"], T0 + 100)
        clock.t = T0 + 500  # the controller was dead for a long while
        looprun.resume(folder, "redo", wait=True, executor=TimedExecutor([{"takes": 10}] * 3, clock), clock=clock)
        record = looprun.read_run(folder)
        self.assertEqual(record["status"], "limit")
        self.assertTrue(record["reason"].startswith("deadline"), record["reason"])


class SessionTests(TimedBase):
    def test_a_gap_over_cache_seconds_starts_a_fresh_session_that_names_the_notes_file(self):
        self.timed({"kind": "interval", "every": 600, "ticks": 3, "cache_seconds": 300}, check="exit 1", attempts=1)
        record, ex, clock, run = self.run_timed([{}] * 4)
        notes = f".opencode/loop-notes/{run}.md"
        self.assertEqual(len({c["session"] for c in ex.calls}), 3)  # 600 seconds between wakes is over the 300: a new session each time
        for call in ex.calls:
            self.assertIn(f"Read the notes file `{notes}`", call["message"])
            self.assertIn("update the notes file", call["message"])
        text = (self.project / notes).read_text()
        self.assertIn("Write ok.", text)
        self.assertIn("exit 1", text)

    def test_a_gap_under_cache_seconds_keeps_the_session(self):
        self.timed({"kind": "interval", "every": 100, "ticks": 3, "cache_seconds": 300}, check="exit 1", attempts=2)
        record, ex, clock, run = self.run_timed([{}] * 6)
        self.assertEqual(len({c["session"] for c in ex.calls}), 1)
        self.assertIn("Read the notes file", ex.calls[0]["message"])
        for call in ex.calls[1:]:
            self.assertNotIn("Read the notes file", call["message"])
            self.assertIn("update the notes file", call["message"])
        self.assertFalse(ex.calls[1]["first"])

    def test_the_notes_path_can_be_set_and_an_existing_file_is_kept(self):
        (self.project / "mine").mkdir()
        (self.project / "mine" / "n.md").write_text("my own notes\n")
        self.timed({"kind": "timebox", "for": 1000, "notes": "mine/n.md"}, attempts=None)
        record, ex, clock, run = self.run_timed([{"ok": True}])
        self.assertIn("`mine/n.md`", ex.calls[0]["message"])
        self.assertEqual((self.project / "mine" / "n.md").read_text(), "my own notes\n")

    def test_a_timebox_rotates_the_session_too(self):
        self.timed({"kind": "timebox", "for": 10000, "cache_seconds": 100}, check="exit 1", attempts=None)
        clock = FakeClock()
        gaps = iter([0, 200, 0])
        original = looptime.Timed.note_turn

        def note(timed, session, cost):  # time passes after a turn (the check takes long, say)
            original(timed, session, cost)
            clock.t += next(gaps, 0)
        with mock.patch.object(looptime.Timed, "note_turn", note):
            record, ex, clock, run = self.run_timed([{"takes": 60}, {"takes": 60}, {"takes": 60}, {"takes": 20000}], clock=clock)
        sessions = [c["session"] for c in ex.calls[:3]]
        self.assertEqual(sessions[0], sessions[1])  # no gap: still warm
        self.assertNotEqual(sessions[1], sessions[2])  # 200 seconds after the last turn: cold
        self.assertNotIn("Read the notes file", ex.calls[1]["message"])
        self.assertIn("Read the notes file", ex.calls[2]["message"])


class ResumeTests(TimedBase):
    def test_resume_after_a_dead_controller_keeps_the_schedule(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 6, "watch": watch_script(0)})
        clock = self.dying_clock(T0 + 15)
        folder = self.die(clock)
        dead = looprun.read_run(folder)
        self.assertEqual((dead["status"], dead["timing"]["phase"], dead["timing"]["next_k"], dead["timing"]["counts"]["idle"]), ("running", "waiting", 2, 2))
        self.assertEqual(looprun.shown_status(folder, dead), "interrupted")
        clock.t = T0 + 47  # ticks 2 and 3 came due with nobody alive, and 4 is due now
        self.resume(folder, clock)
        record = looprun.read_run(folder)
        self.assertEqual(record["status"], "limit")
        self.assertEqual(record["timing"]["anchor"], dead["timing"]["anchor"])
        self.assertEqual(record["timing"]["counts"], {"idle": 4, "woken": 0, "skipped": 2, "failed": 0})  # ticks 0, 1, 4, 5; 2 and 3 not replayed
        self.assertEqual([t["k"] for t in record["timing"]["ticks"]], [0, 1, 4, 5])
        self.assertEqual(clock.t, T0 + 50)

    def test_resume_after_a_finished_wake_goes_on_with_the_schedule(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 4, "watch": watch_script(10, 0)})
        clock = self.dying_clock(T0 + 5)
        folder = self.die(clock)
        dead = looprun.read_run(folder)
        self.assertEqual((dead["timing"]["phase"], dead["timing"]["counts"]["woken"], len(dead["attempts"])), ("waiting", 1, 1))
        clock.t = T0 + 12
        executor = TimedExecutor([{"ok": True}], clock)
        looprun.resume(folder, wait=True, executor=executor, clock=clock)
        record = looprun.read_run(folder)
        self.assertEqual((record["status"], executor.calls, len(record["attempts"])), ("limit", [], 1))
        self.assertEqual(record["timing"]["counts"], {"idle": 3, "woken": 1, "skipped": 0, "failed": 0})

    def test_resume_inside_a_wake_goes_on_from_the_cursor(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 3, "watch": watch_script(10, 0)})
        clock = FakeClock()
        executor = TimedExecutor([{"ok": True}] * 3, clock)
        executor.dies_at = 1
        folder = self.die(clock, executor=executor)
        dead = looprun.read_run(folder)
        self.assertEqual(dead["timing"]["phase"], "wake")
        shown = looprun.shown_status(folder, dead)
        self.assertEqual(shown, "interrupted")
        # a step of unknown fate pauses as it always did
        self.resume(folder, clock)
        record = looprun.read_run(folder)
        self.assertEqual(record["status"], "paused")
        self.assertEqual(looprun.read_run(folder)["timing"]["phase"], "wake")
        looprun.resume(folder, "redo", wait=True, executor=TimedExecutor([{"ok": True}] * 3, clock), clock=clock)
        record = looprun.read_run(folder)
        self.assertEqual(record["status"], "limit")
        self.assertEqual(record["timing"]["counts"]["woken"], 1)


class SupervisorTests(TimedBase):
    def resumer(self, clock):
        return lambda folder: looprun.resume(folder, wait=True, executor=TimedExecutor([{"ok": True}] * 5, clock), clock=clock)

    def test_it_resumes_a_dead_waiting_run_and_leaves_an_unknown_fate_alone(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 4, "watch": watch_script(10, 0)})
        waiting_clock = self.dying_clock(T0 + 5)
        # the first run: wakes at tick 0 and dies in the step (unknown fate)
        step_clock = FakeClock()
        step_executor = TimedExecutor([{"ok": True}], step_clock, dies_at=1)
        in_step = self.die(step_clock, executor=step_executor)
        (self.project / ".watch-count").unlink()
        self.timed({"kind": "interval", "every": 10, "ticks": 4, "watch": watch_script(0)})
        waiting = self.die(waiting_clock)
        self.assertNotEqual(in_step, waiting)
        self.assertEqual(looprun.read_run(waiting)["timing"]["phase"], "waiting")
        self.assertEqual(looprun.read_run(in_step)["timing"]["phase"], "wake")
        clock = FakeClock(T0 + 12)
        look = looptime.supervise_once(self.project, self.resumer(clock))
        self.assertEqual(look["resumed"], [waiting.name])
        self.assertIn(in_step.name, look["left"])
        self.assertIn("a person decides", look["left"][in_step.name])
        self.assertEqual(look["unfinished"], 2)
        self.assertEqual(looprun.read_run(waiting)["status"], "limit")
        self.assertEqual(looprun.read_run(in_step)["status"], "running")  # untouched
        self.assertEqual(looprun.read_run(in_step)["timing"]["phase"], "wake")
        look = looptime.supervise_once(self.project, self.resumer(clock))
        self.assertEqual((look["resumed"], look["unfinished"]), ([], 1))

    def test_the_supervisor_ends_when_no_timed_run_is_unfinished(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 3, "watch": watch_script(0)})
        clock = self.dying_clock(T0 + 5)
        folder = self.die(clock)
        out = io.StringIO()
        sleeps = []
        spare = FakeClock(T0 + 11)
        spare.sleep = lambda seconds: sleeps.append(seconds)
        code = looptime.supervise(self.project, 30, spare, self.resumer(clock), out)
        self.assertEqual(code, 0)
        self.assertEqual(looprun.read_run(folder)["status"], "limit")
        self.assertIn(f"resumed run {folder.name}", out.getvalue())
        self.assertIn("the supervisor ends", out.getvalue())
        self.assertEqual(looptime.supervise(self.project, 30, spare, self.resumer(clock), io.StringIO()), 0)  # nothing left: ends at once

    def test_one_supervisor_per_project(self):
        folder = looptime.loops_folder(self.project)
        folder.mkdir(parents=True)
        with open(folder / looptime.SUPERVISOR_LOCK, "a+") as lock:
            osfs.lock(lock.fileno(), block=False)
            self.assertTrue(looptime._supervisor_alive(self.project))
            out = io.StringIO()
            self.assertEqual(looptime.supervise(self.project, 30, FakeClock(), lambda f: None, out), 1)
            self.assertIn("already running", out.getvalue())
            with mock.patch.object(looptime.subprocess, "Popen") as popen:
                self.assertFalse(looptime.ensure_supervisor(self.project, "/repo"))
                popen.assert_not_called()
        self.assertFalse(looptime._supervisor_alive(self.project))
        with mock.patch.object(looptime.subprocess, "Popen") as popen:
            self.assertTrue(looptime.ensure_supervisor(self.project, "/repo"))
            command = popen.call_args.args[0]
            self.assertEqual(command[-3:], ["supervise", "--project", str(self.project)])
            self.assertEqual(popen.call_args.kwargs["env"]["PYTHONPATH"], "/repo")

    def test_a_detached_timed_start_starts_the_supervisor_unless_told_not_to(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 3})
        for kwargs, expected in (({}, 1), ({"supervisor": False}, 0)):
            with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor") as ensure:
                looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]), **kwargs)
                self.assertEqual(ensure.call_count, expected)
        self.save()  # an untimed loop never starts one
        with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor") as ensure:
            looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]))
            ensure.assert_not_called()

    def test_a_timed_run_is_known_from_the_moment_it_is_queued(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 2, "watch": watch_script(0)})
        with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor"):
            run = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]))
        folder = self.folder(run)
        self.assertEqual(looprun.read_run(folder)["status"], "queued")
        self.assertEqual([f.name for f, _ in looptime.unfinished(self.project)], [run])  # the supervisor must not end before it
        clock = FakeClock()
        look = looptime.supervise_once(self.project, self.resumer(clock))
        self.assertEqual((look["resumed"], list(look["left"])), ([], [run]))  # just made: a controller may be starting
        with mock.patch.object(looprun, "QUEUED_GRACE", 0):
            look = looptime.supervise_once(self.project, self.resumer(clock))
        self.assertEqual(look["resumed"], [run])
        self.assertEqual(looprun.read_run(folder)["status"], "limit")

    def test_the_cli_has_supervise_and_no_supervisor(self):
        args = looprun._parser().parse_args(["start", "self", "--no-supervisor"])
        self.assertTrue(args.no_supervisor)
        args = looprun._parser().parse_args(["supervise", "--project", "p", "--every", "5"])
        self.assertEqual((args.every, args.project), (5.0, "p"))


class OffSwitchTests(TimedBase):
    def test_off_cancels_timed_runs_and_blocks_until_on(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 5, "watch": watch_script(0)})
        folder = self.die(self.dying_clock(T0 + 5))
        code, out, err = self.cli("off", "--project", str(self.project))
        self.assertEqual(code, 0, err)
        self.assertIn(folder.name, out)
        self.assertEqual(looprun.read_run(folder)["status"], "cancelled")
        self.assertTrue(looptime.is_off(self.project))
        for attempt in (lambda: looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]), wait=True),
                        lambda: looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]))):
            with self.assertRaisesRegex(looprun.LoopError, "switched off.*agentgraph loop on"):
                attempt()
        self.assertEqual(len(looprun.list_runs(self.project)), 1)  # nothing was written for the refused starts
        code, out, err = self.cli("start", "self", "--project", str(self.project), "--structure", str(self.structure), "--goal", "g")
        self.assertNotEqual(code, 0)
        self.assertIn("switched off", err)

    def test_resuming_a_timed_run_is_refused_while_off(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 5, "watch": watch_script(0)})
        clock = self.dying_clock(T0 + 5)
        folder = self.die(clock)
        looptime.off_marker(self.project).write_text("{}")
        with self.assertRaisesRegex(looprun.LoopError, "switched off"):
            looprun.resume(folder, wait=True, executor=TimedExecutor([], clock), clock=clock)
        look = looptime.supervise_once(self.project, lambda f: self.fail("resumed"))
        self.assertEqual(look["resumed"], [])
        self.assertIn("switched off", look["left"][folder.name])
        self.assertEqual(self.cli("on", "--project", str(self.project))[0], 0)
        self.assertFalse(looptime.is_off(self.project))
        self.resume(folder, clock)
        self.assertEqual(looprun.read_run(folder)["status"], "limit")

    def test_loops_without_timing_are_not_affected(self):
        self.cli("off", "--project", str(self.project))
        self.save()
        run, record, ex = self.go([{}, {}])
        self.assertEqual(record["status"], "passed")
        self.cli("on", "--project", str(self.project))
        self.assertIn("were not switched off", self.cli("on", "--project", str(self.project))[1])

    def test_off_leaves_an_untimed_run_alone(self):
        self.save()
        run, record, ex = self.go([{}, {}])
        self.assertEqual(looptime.switch_off(self.project), [])
        self.assertEqual(looprun.read_run(self.folder(run))["status"], "passed")


class StatusTests(TimedBase):
    def test_status_shows_the_timed_fields(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 5, "watch": watch_script(0, 10, 0)}, cost=2)
        seen = {}

        def look(clock):
            if "text" not in seen and clock.t >= T0 + 25:
                _, seen["text"], _ = self.cli("status", "--project", str(self.project))
                _, seen["json"], _ = self.cli("status", "--json", "--project", str(self.project))
        record, ex, clock, run = self.run_timed([{"ok": True, "cost": 0.03}], clock=FakeClock(on_sleep=look))
        self.assertEqual(record["status"], "limit")
        # between ticks the stored status is one every reader knows, with a hint
        stored = json.loads(seen["json"])
        self.assertEqual(stored["status"], "running")
        self.assertEqual(stored["timing"]["phase"], "waiting")
        self.assertIn("waiting for the next tick at", seen["text"])
        self.assertIn("timing interval  every 20s", seen["text"])
        self.assertIn("next tick", seen["text"])
        self.assertIn("last tick 1: wake_passed", seen["text"])
        self.assertIn("ticks 1 idle, 1 woken, 0 skipped, 0 failed", seen["text"])
        self.assertIn("ends at 5 ticks", seen["text"])
        self.assertIn("spend $0.0300", seen["text"])
        code, out, _ = self.cli("status", "--project", str(self.project))
        self.assertIn("last tick 4: idle", out)
        self.assertNotIn("next tick", out)  # a run that is over has none
        code, out, _ = self.cli("status", "--json", "--project", str(self.project))
        data = json.loads(out)
        self.assertEqual((data["status"], data["timing"]["kind"], data["timing"]["counts"]["woken"], data["timing"]["last"]["k"]), ("limit", "interval", 1, 4))
        self.assertEqual(sum(data["timing"]["spend_by_day"].values()), 0.03)
        self.assertEqual(data["timing"]["next_k"], 5)

    def test_the_stored_statuses_stay_the_known_ones(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 4, "watch": watch_script(0, 10, 0)})
        seen = set()

        def look(clock):
            for name in looprun.list_runs(self.project):
                seen.add(looprun.read_run(self.folder(name))["status"])
        self.run_timed([{"ok": True}], clock=FakeClock(on_sleep=look))
        self.assertTrue(seen <= {"queued", "running", "waiting", "paused", *looprun.FINAL}, seen)
        self.assertEqual(seen, {"running"})

    def test_the_page_bridge_shows_a_timed_run(self):
        from agentgraph import loopapi
        self.timed({"kind": "interval", "every": 20, "ticks": 2, "watch": watch_script(0)})
        record, ex, clock, run = self.run_timed()
        row = loopapi._summary(self.project, run)
        self.assertEqual((row["status"], row["timing"]["kind"], row["timing"]["counts"]["idle"]), ("limit", "interval", 2))
        self.assertEqual((row["timing"]["wakes"], row["timing"]["anchor"]), (None, T0))  # no wake yet: unknown-or-none, never a made-up 0; the run's start is there for "for N"
        detail = loopapi.run_detail(self.project, run)
        self.assertEqual(detail["run"]["timing"]["counts"]["idle"], 2)
        self.save()
        run, record, ex = self.go([{}, {}])
        self.assertNotIn("timing", loopapi._summary(self.project, run))
        clean = loopapi.looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2, "cost": 1, "attempt_seconds": 100}, "timing": {"kind": "interval", "every": 20, "ticks": 2}}, "x")
        self.assertEqual(clean["timing"]["kind"], "interval")


if __name__ == "__main__":
    unittest.main()


class ZeroTimeoutTests(TimedBase):
    """Round 2, finding 1: a timeout of 0 means "no limit" to the executors, so no turn is ever started with one."""

    def test_the_timeout_of_a_turn_is_never_under_one_second(self):
        cfg = looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2, "cost": 1, "attempt_seconds": 100},
                                "timing": {"kind": "timebox", "for": 100}}, "x")["timing"]
        timed = looptime.Timed(types.SimpleNamespace(state={}), cfg, FakeClock(T0 + 500))
        timed.rt["deadline"] = T0 + 100  # four hundred seconds ago
        self.assertEqual(timed.timeout(100), 1.0)
        self.assertEqual(timed.timeout(None), 1.0)
        self.assertEqual(timed.timeout(0), 1.0)
        timed.clock.t = T0 + 100  # exactly now
        self.assertEqual(timed.timeout(100), 1.0)
        self.assertIsNotNone(timed.admission())

    def test_min_attempt_seconds_is_at_least_one(self):
        base = {"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2, "cost": 1, "attempt_seconds": 100}}
        for minimum in (0, 0.5, -3):
            with self.subTest(minimum=minimum), self.assertRaisesRegex(looprun.LoopError, "min_attempt_seconds.*from 1 to 86400"):
                looprun.validate({**base, "timing": {"kind": "timebox", "for": 100, "min_attempt_seconds": minimum}}, "x")
        looprun.validate({**base, "timing": {"kind": "timebox", "for": 100, "min_attempt_seconds": 1}}, "x")

    def test_a_turn_is_not_started_with_a_second_or_less_left_and_none_gets_a_zero_timeout(self):
        for takes in (100, 99.5):  # nothing left, and half a second left
            with self.subTest(takes=takes):
                self.timed({"kind": "timebox", "for": 100, "min_attempt_seconds": 1}, check="exit 1", attempts=None)
                record, ex, clock, run = self.run_timed([{"takes": takes}] * 3)
                self.assertEqual((record["status"], len(ex.calls)), ("limit", 1))
                self.assertTrue(record["reason"].startswith("deadline"), record["reason"])
                self.assertTrue(all(call["timeout"] >= 1.0 for call in ex.calls), ex.calls)

    def test_the_end_that_comes_between_the_check_of_the_limits_and_the_send_ends_the_run(self):
        self.timed({"kind": "timebox", "for": 100, "min_attempt_seconds": 1}, check="exit 1", attempts=None)
        clock = FakeClock()
        original = looptime.Timed.admission
        seen = []

        def late(timed):  # the first look says there is time; by the next one (right before the send) the end has passed
            seen.append(timed.left())
            result = original(timed)
            if len(seen) == 1:
                timed.clock.t = T0 + 500
            return result
        with mock.patch.object(looptime.Timed, "admission", late):
            record, ex, _, run = self.run_timed([{"takes": 1}] * 3, clock=clock)
        self.assertEqual((record["status"], ex.calls), ("limit", []))  # no turn with a timeout of 0 (or 1) was sent past the end


class IntervalEndTests(TimedBase):
    """Round 2, finding 2: a turn of an interval loop is cut at the end of the schedule, like a time box's turn at its deadline."""

    def test_a_timed_loop_needs_attempt_seconds(self):
        for timing in ({"kind": "interval", "every": 20, "ticks": 3}, {"kind": "timebox", "for": 600}):
            for limits in ({"attempts": 2, "cost": 1}, {"attempts": 2, "cost": 1, "attempt_seconds": None}):
                with self.subTest(timing=timing, limits=limits), self.assertRaisesRegex(looprun.LoopError, "limits.attempt_seconds"):
                    looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": limits, "timing": timing}, "x")
        looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2}}, "x")  # an untimed loop needs none

    def test_an_interval_turn_is_cut_to_the_end_of_the_schedule(self):
        self.timed({"kind": "interval", "every": 20, "for": 50}, check="exit 1")
        record, ex, clock, run = self.run_timed([{"takes": 500}] * 3)
        self.assertEqual([c["timeout"] for c in ex.calls], [50])  # not the 1000 of attempt_seconds
        self.assertEqual(record["attempts"][0]["agent"]["outcome"], "stalled")
        self.assertEqual(clock.t - T0, 50)
        self.assertEqual(record["status"], "limit")
        self.assertTrue(record["reason"].startswith("deadline"), record["reason"])

    def test_with_ticks_alone_attempt_seconds_is_the_bound(self):
        self.timed({"kind": "interval", "every": 20, "ticks": 2}, check="exit 1", limits={"attempt_seconds": 30})
        record, ex, clock, run = self.run_timed([{"takes": 500}] * 3)
        self.assertEqual([c["timeout"] for c in ex.calls], [30, 30, 30])  # the three attempts of the one wake that was not skipped


class WallClockTests(TimedBase):
    """Round 2, finding 8: the wall clock stops a turn or a check that runs past the end (a monotonic timeout stops while the machine sleeps)."""

    class Sleeper(looprun.ScriptedExecutor):
        """A turn during which the machine sleeps: the wall clock jumps by `jump` seconds and the turn then looks at its poll callback."""

        def __init__(self, clock, jump):
            super().__init__([{}] * 3)
            self.clock, self.jump = clock, jump

        def attempt(self, *, agent, session, first, title, message, timeout, env, cwd, auto, poll) -> dict:
            self.calls.append({"timeout": timeout})
            self.clock.t += self.jump
            stopped = poll()
            return {"exit": None if stopped else 0, "outcome": "cancelled" if stopped else "returned", "session": session, "cost": 0.01, "tokens": None, "sessions": None,
                    "seconds": 1, "error": None, "text": "", "final": ""}

    def test_a_turn_that_runs_past_the_deadline_by_the_wall_clock_is_stopped_as_out_of_time(self):
        self.timed({"kind": "timebox", "for": 100, "min_attempt_seconds": 10}, check="exit 1", attempts=None)
        clock = FakeClock()
        executor = self.Sleeper(clock, 5000)
        run = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=executor, wait=True, clock=clock, supervisor=False)
        record = looprun.read_run(self.folder(run))
        self.assertEqual(record["attempts"][0]["agent"]["outcome"], "stalled")  # not `cancelled`: nobody asked for that
        self.assertEqual((record["status"], len(executor.calls)), ("limit", 1))
        self.assertTrue(record["reason"].startswith("deadline"), record["reason"])

    def test_a_turn_that_is_still_inside_the_time_is_not_stopped(self):
        self.timed({"kind": "timebox", "for": 100, "min_attempt_seconds": 10}, check="exit 1", attempts=2)
        clock = FakeClock()
        executor = self.Sleeper(clock, 5)
        run = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=executor, wait=True, clock=clock, supervisor=False)
        self.assertEqual([a["agent"]["outcome"] for a in looprun.read_run(self.folder(run))["attempts"]], ["returned", "returned"])

    def test_a_check_is_cut_to_the_time_left_plus_a_short_grace(self):
        cfg = looprun.validate({"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2, "cost": 1, "attempt_seconds": 100},
                                "timing": {"kind": "timebox", "for": 100}}, "x")["timing"]
        timed = looptime.Timed(types.SimpleNamespace(state={}), cfg, FakeClock(T0 + 97))
        timed.rt["deadline"] = T0 + 100
        self.assertEqual(timed.check_timeout(600), 3 + looptime.CHECK_GRACE)
        self.assertEqual(timed.check_timeout(5), 5)
        self.assertEqual(timed.check_timeout(None), 3 + looptime.CHECK_GRACE)
        timed.clock.t = T0 + 900
        self.assertEqual(timed.check_timeout(600), looptime.CHECK_GRACE)

    def test_a_check_that_runs_past_the_end_by_the_wall_clock_is_stopped(self):
        self.timed({"kind": "timebox", "for": 100, "min_attempt_seconds": 10}, check=sleep_command(30), attempts=None)
        clock = FakeClock()
        executor = self.Sleeper(clock, 0)
        executor.attempt = lambda **kw: (setattr(clock, "t", T0 + 5000), {"exit": 0, "outcome": "returned", "session": kw["session"], "cost": 0.01, "tokens": None,
                                                                          "sessions": None, "seconds": 1, "error": None, "text": "", "final": ""})[1]
        began = time.monotonic()
        run = looprun.start(self.project, "self", structure=self.structure, goal="g", executor=executor, wait=True, clock=clock, supervisor=False)
        record = looprun.read_run(self.folder(run))
        self.assertLess(time.monotonic() - began, 8)  # the grace is 10 seconds of the clock that is past; the poll saw it at once
        self.assertEqual(record["status"], "limit")
        self.assertIn("while the check", record["reason"])


class IdleEventTests(TimedBase):
    """Round 2, finding 9: a long run of idle ticks does not write an event for each."""

    def ticks(self, run):
        return [e for e in self.events(run) if e.get("step") == "tick" and "tick" in e]

    def test_idle_ticks_in_a_row_are_one_event_and_one_summary_for_every_hundred(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 205, "watch": "exit 0"})
        record, ex, clock, run = self.run_timed()
        self.assertEqual(record["timing"]["counts"]["idle"], 205)  # exact in run.json
        events = self.ticks(run)
        self.assertEqual([e["tick"] for e in events], [0, 99, 199])  # the first, then every 100 idle ticks in a row
        self.assertEqual([e.get("idle_in_a_row") for e in events], [None, 100, 200])

    def test_a_change_of_result_is_an_event_again(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 6, "watch": watch_script(0, 0, 0, 10, 0, 0)})
        record, ex, clock, run = self.run_timed([{"ok": True}])
        self.assertEqual([(e["tick"], e["outcome"]) for e in self.ticks(run)], [(0, "idle"), (3, "wake_passed"), (4, "idle")])
        self.assertEqual(record["timing"]["counts"], {"idle": 5, "woken": 1, "skipped": 0, "failed": 0})


class UntilTests(TimedBase):
    """Round 2, finding 7."""
    BASE = {"version": 1, "flow": "self", "check": {"command": "true"}, "limits": {"attempts": 2, "cost": 1, "attempt_seconds": 100}}

    def test_until_must_be_finite_and_within_a_year(self):
        now = time.time()
        for until in (float("nan"), float("inf"), float("-inf"), iso(now + 400 * 86400), now + 400 * 86400, "9999-12-31T00:00:00+00:00"):
            with self.subTest(until=until), self.assertRaisesRegex(looprun.LoopError, "until"):
                looprun.validate({**self.BASE, "timing": {"kind": "timebox", "until": until}}, "x")
        looprun.validate({**self.BASE, "timing": {"kind": "timebox", "until": iso(now + 300 * 86400)}}, "x")
        looprun.validate({**self.BASE, "timing": {"kind": "timebox", "until": now + 600}}, "x")

    def test_no_numeric_field_takes_nan_or_infinity(self):
        nan, inf = float("nan"), float("inf")
        for timing in ({"kind": "interval", "every": nan, "ticks": 3}, {"kind": "interval", "every": inf, "ticks": 3}, {"kind": "interval", "every": 20, "ticks": nan},
                       {"kind": "interval", "every": 20, "ticks": inf}, {"kind": "timebox", "for": nan}, {"kind": "timebox", "for": inf},
                       {"kind": "timebox", "for": 100, "min_attempt_seconds": nan}, {"kind": "timebox", "for": 100, "cache_seconds": inf},
                       {"kind": "interval", "every": 20, "ticks": 3, "watch": "true", "watch_seconds": nan}):
            with self.subTest(timing=timing), self.assertRaises(looprun.LoopError):
                looprun.validate({**self.BASE, "timing": timing}, "x")


class OffRaceTests(TimedBase):
    """Round 2, finding 4: `loop off` and a start that is being made cannot miss each other."""

    def queue(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 2, "watch": watch_script(0)})
        with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor"):
            return self.folder(looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([])))

    def test_a_controller_that_starts_after_off_cancels_its_queued_run(self):
        folder = self.queue()
        looptime.off_marker(self.project).write_text(json.dumps({"off": True, "time": time.time()}))  # off came after the scan, before the controller
        with self.assertRaisesRegex(looprun.LoopError, "switched off"):
            looprun.execute(folder, looprun.ScriptedExecutor([]))
        record = looprun.read_run(folder)
        self.assertEqual(record["status"], "cancelled")
        self.assertIn("switched off", record["reason"])
        self.cli("on", "--project", str(self.project))
        with self.assertRaises(looprun.Busy):  # and it does not start after `on`
            looprun.execute(folder, looprun.ScriptedExecutor([]))
        self.assertEqual(looprun.read_run(folder)["status"], "cancelled")

    def test_a_refused_resume_of_a_running_run_is_not_a_cancel(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 5, "watch": watch_script(0)})
        clock = self.dying_clock(T0 + 5)
        folder = self.die(clock)
        looptime.off_marker(self.project).write_text(json.dumps({"off": True, "time": time.time()}))
        with self.assertRaisesRegex(looprun.LoopError, "switched off"):
            looprun.execute(folder, TimedExecutor([], clock), resume="", clock=clock)
        self.assertEqual(looprun.read_run(folder)["status"], "running")  # `off` itself cancels runs; a refused resume only refuses

    def test_the_off_marker_keeps_the_time_of_the_last_off_after_on(self):
        self.assertEqual(looptime.last_off(self.project), 0.0)
        looptime.switch_off(self.project)
        off_at = looptime.last_off(self.project)
        self.assertGreater(off_at, 0)
        self.assertTrue(looptime.is_off(self.project))
        self.assertTrue(looptime.switch_on(self.project))
        self.assertFalse(looptime.is_off(self.project))
        self.assertEqual(looptime.last_off(self.project), off_at)
        self.assertEqual(json.loads(looptime.off_marker(self.project).read_text()), {"off": False, "last_off": off_at})
        self.assertFalse(looptime.switch_on(self.project))

    def test_the_supervisor_never_starts_a_queued_run_made_before_the_last_off(self):
        folder = self.queue()
        created = looprun.read_run(folder)["created"]
        looptime.off_marker(self.project).write_text(json.dumps({"off": False, "last_off": created + 1}))  # `off` then `on`, and the run was made in between
        with mock.patch.object(looprun, "QUEUED_GRACE", 0):
            look = looptime.supervise_once(self.project, lambda f: self.fail("a run made before the last off was started"))
        self.assertEqual(look["resumed"], [])
        self.assertIn("switched off", look["left"][folder.name])
        self.assertEqual(looprun.read_run(folder)["status"], "cancelled")

    def test_the_supervisor_starts_a_queued_run_made_after_the_last_off(self):
        folder = self.queue()
        created = looprun.read_run(folder)["created"]
        looptime.off_marker(self.project).write_text(json.dumps({"off": False, "last_off": created - 100}))
        clock = FakeClock()
        with mock.patch.object(looprun, "QUEUED_GRACE", 0):
            look = looptime.supervise_once(self.project, lambda f: looprun.resume(f, wait=True, executor=TimedExecutor([], clock), clock=clock))
        self.assertEqual(look["resumed"], [folder.name])
        self.assertEqual(looprun.read_run(folder)["status"], "limit")

    def test_start_notices_an_off_that_came_while_it_wrote_the_run(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 2, "watch": watch_script(0)})
        real = looptime.is_off
        calls = []

        def off_after_the_first_look(project):  # the look before the run is written says on; the look after it says off
            calls.append(1)
            return len(calls) > 1 or real(project)
        with mock.patch.object(looptime, "is_off", off_after_the_first_look), mock.patch.object(looprun.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(looprun.LoopError, "switched off"):
                looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([]))
            popen.assert_not_called()
        (run,) = looprun.list_runs(self.project)
        self.assertEqual(looprun.read_run(self.folder(run))["status"], "cancelled")


class SupervisorRoundTwoTests(TimedBase):
    def dead_waiting(self, count=1):
        self.timed({"kind": "interval", "every": 10, "ticks": 3, "watch": watch_script(0)})
        return [self.die(self.dying_clock(T0 + 5)) for _ in range(count)]

    def test_a_run_with_a_pause_or_cancel_request_is_not_resumed(self):
        first, second, third = self.dead_waiting(3)
        (first / "pause").write_text("")
        (second / "cancel").write_text("")
        resumed = []
        look = looptime.supervise_once(self.project, resumed.append)
        self.assertEqual(resumed, [third])
        self.assertIn("pause or cancel", look["left"][first.name])
        self.assertIn("pause or cancel", look["left"][second.name])

    def test_the_supervisor_ends_when_nothing_is_resumable_and_says_what_it_left(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 4, "watch": watch_script(10, 0)})
        step_clock = FakeClock()
        in_wake = self.die(step_clock, executor=TimedExecutor([{"ok": True}], step_clock, dies_at=1))
        self.timed({"kind": "timebox", "for": 100}, check="exit 1", attempts=None)
        box = self.die(FakeClock(), executor=TimedExecutor([{"takes": 5}], FakeClock(), dies_at=1))
        out, slept = io.StringIO(), []
        clock = FakeClock(T0 + 5)
        clock.sleep = slept.append
        self.assertEqual(looptime.supervise(self.project, 30, clock, lambda f: self.fail("resumed"), out), 0)
        self.assertEqual(slept, [])  # nothing to wait for: it ends at once
        text = out.getvalue()
        self.assertIn(f"left run {in_wake.name}: its controller died inside a wake", text)
        self.assertIn(f"left run {box.name}: a time box", text)
        self.assertIn("the supervisor ends", text)

    def test_a_paused_run_and_one_waiting_for_an_answer_are_left_and_the_supervisor_ends(self):
        first, second = self.dead_waiting(2)
        for folder, status in ((first, "paused"), (second, "waiting")):
            state = looprun.read_run(folder)
            looprun._write_atomic(folder / "run.json", looprun._json({**state, "status": status}))
        out = io.StringIO()
        self.assertEqual(looptime.supervise(self.project, 30, FakeClock(T0 + 5), lambda f: self.fail("resumed"), out), 0)
        self.assertIn(f"left run {first.name}", out.getvalue())
        self.assertIn(f"left run {second.name}", out.getvalue())

    def test_a_run_is_resumed_at_most_five_times(self):
        (folder,) = self.dead_waiting()
        resumed = []
        for _ in range(8):
            look = looptime.supervise_once(self.project, resumed.append)  # a resume that does not take: the run stays interrupted
        self.assertEqual(len(resumed), looptime.MAX_RESTARTS)
        self.assertIn(f"resumed {looptime.MAX_RESTARTS} times", look["left"][folder.name])

    def test_one_run_that_cannot_be_resumed_does_not_stop_the_others(self):
        first, second = self.dead_waiting(2)
        resumed = []

        def resume(folder):
            if folder == first:
                raise RuntimeError("boom")
            resumed.append(folder)
        look = looptime.supervise_once(self.project, resume)
        self.assertEqual(resumed, [second])
        self.assertIn("RuntimeError: boom", look["left"][first.name])
        out = io.StringIO()
        clock = FakeClock(T0 + 5)
        clock.sleep = lambda seconds: (_ for _ in ()).throw(StopIteration)  # one look is enough: stop at the first wait
        with self.assertRaises(StopIteration):
            looptime.supervise(self.project, 30, clock, resume, out)  # an exception of a resume is caught inside the supervisor too

    def test_every_under_five_seconds_is_refused(self):
        for every in (0, 1, 4.9, -1, float("nan")):
            with self.subTest(every=every), self.assertRaisesRegex(looprun.LoopError, "at least 5"):
                looptime.supervise(self.project, every, FakeClock(), lambda f: None, io.StringIO())
        code, out, err = self.cli("supervise", "--project", str(self.project), "--every", "2")
        self.assertNotEqual(code, 0)
        self.assertIn("at least 5", err)

    def test_resuming_a_timed_interval_run_ensures_a_supervisor_and_a_timebox_does_not(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 3, "watch": watch_script(0)})
        folder = self.die(self.dying_clock(T0 + 5), supervisor=True)  # started the usual way: it did not ask for no supervisor
        with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor") as ensure:
            looprun.resume(folder)
            self.assertEqual(ensure.call_count, 1)
            looprun.resume(folder, supervisor=False)
            self.assertEqual(ensure.call_count, 1)
        self.timed({"kind": "timebox", "for": 100}, check="exit 1", attempts=None)
        clock = FakeClock()
        box = self.die(clock, executor=TimedExecutor([{"takes": 5}], clock, dies_at=1))
        with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor") as ensure:
            looprun.resume(box, "redo")
            ensure.assert_not_called()


class RoundThreeTests(TimedBase):
    """Round 3, m1 to m4."""

    def dead_waiting(self, **timing):
        self.timed({"kind": "interval", "every": 10, "ticks": 3, "watch": watch_script(0), **timing})
        return self.die(self.dying_clock(T0 + 5))

    def make_old(self, folder):
        """The run as it was made before `limits.attempt_seconds` was required (its pins are of the settings it had)."""
        definition = looprun.read_definition(folder)
        del definition["settings"]["limits"]["attempt_seconds"]
        digest = hashlib.sha256(looprun._json(definition["settings"])).hexdigest()
        definition["settings_sha256"] = digest
        looprun._write_atomic(folder / "definition.json", looprun._json(definition))
        state = looprun.read_run(folder)
        del state["limits"]["attempt_seconds"]
        looprun._write_atomic(folder / "run.json", looprun._json({**state, "settings_sha256": digest}))

    # m1
    def test_a_timed_run_made_without_a_turn_limit_is_refused_by_a_resume_and_left_by_the_supervisor(self):
        folder = self.dead_waiting()
        self.make_old(folder)
        clock = FakeClock(T0 + 30)
        executor = TimedExecutor([{"ok": True}], clock)
        for call in (lambda: looprun.resume(folder, wait=True, executor=executor, clock=clock), lambda: looprun.resume(folder), lambda: looprun.execute(folder, executor, resume="", clock=clock)):
            with self.assertRaisesRegex(looprun.LoopError, "before turn limits were required.*start it again"):
                call()
        self.assertEqual(executor.calls, [])
        self.assertEqual(looprun.read_run(folder)["status"], "running")  # nothing written
        look = looptime.supervise_once(self.project, lambda f: self.fail("a run with no turn limit was resumed"))
        self.assertIn("turn limits were required", look["left"][folder.name])
        self.assertFalse((folder / looptime.RESTARTS_FILE).exists())  # and it was not counted as a restart
        out = io.StringIO()
        self.assertEqual(looptime.supervise(self.project, 30, FakeClock(), lambda f: self.fail("resumed"), out), 0)
        self.assertIn(f"left run {folder.name}: made before turn limits were required", out.getvalue())

    def test_loop_status_still_shows_a_run_made_without_a_turn_limit_and_says_to_start_again(self):
        folder = self.dead_waiting()
        self.make_old(folder)
        code, out, err = self.cli("status", folder.name, "--project", str(self.project))
        self.assertEqual(code, 0, err)
        self.assertIn("interrupted", out)
        self.assertIn("start the loop again", out)
        self.assertNotIn("loop resume", out)

    # m2
    def test_the_restart_count_starts_again_once_a_resumed_controller_completes_a_tick(self):
        folder = self.dead_waiting()
        (folder / looptime.RESTARTS_FILE).write_text(f"{looptime.MAX_RESTARTS - 1}\n")
        clock = FakeClock(T0 + 12)
        looptime.supervise_once(self.project, lambda f: looprun.resume(f, wait=True, executor=TimedExecutor([{"ok": True}], clock), clock=clock))
        self.assertEqual(looprun.read_run(folder)["status"], "limit")
        self.assertEqual(looptime.restarts(folder), 0)
        self.assertFalse((folder / looptime.RESTARTS_FILE).exists())

    def test_a_resumed_controller_that_dies_before_a_tick_keeps_the_count(self):
        folder = self.dead_waiting()
        (folder / looptime.RESTARTS_FILE).write_text("2\n")

        def resume(f):
            looprun.resume(f, wait=True, executor=TimedExecutor([], FakeClock()), clock=self.dying_clock(T0))
        with self.assertRaises(Died):
            looptime.supervise_once(self.project, resume)
        self.assertEqual(looptime.restarts(folder), 3)

    def test_the_hint_says_the_supervisor_gave_up_and_what_to_run(self):
        folder = self.dead_waiting()
        state = looprun.read_run(folder)
        self.assertIn("no controller is running this run", looprun.hint(folder, state))
        self.assertNotIn("gave up", looprun.hint(folder, state))
        (folder / looptime.RESTARTS_FILE).write_text(f"{looptime.MAX_RESTARTS}\n")
        hint = looprun.hint(folder, state)
        self.assertIn(f"gave up on this run after {looptime.MAX_RESTARTS} resumes", hint)
        self.assertIn(f"agentgraph loop resume {folder.name}", hint)
        code, out, err = self.cli("status", folder.name, "--project", str(self.project))
        self.assertIn("gave up", out)

    # m3
    def test_a_turn_cut_at_the_deadline_that_is_also_the_last_attempt_ends_limit_not_stalled(self):
        self.timed({"kind": "timebox", "for": 100}, check="exit 1", attempts=1)
        record, executor, clock, _ = self.run_timed([{"takes": 500}])
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(record["status"], "limit")
        self.assertIn("deadline", record["reason"])

    def test_a_stalled_turn_before_the_deadline_still_ends_stalled(self):
        self.timed({"kind": "timebox", "for": 10000}, check="exit 1", attempts=1, limits={"attempt_seconds": 50})
        record, *_ = self.run_timed([{"takes": 500}])
        self.assertNotEqual(record["status"], "limit")
        self.assertIn(record["status"], ("stalled", "failed"))

    # m4
    def test_a_check_only_look_at_a_queued_run_while_off_writes_nothing(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 2, "watch": watch_script(0)})
        with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor"):
            folder = self.folder(looprun.start(self.project, "self", structure=self.structure, goal="g", executor=looprun.ScriptedExecutor([])))
        before = (folder / "run.json").read_bytes()
        looptime.off_marker(self.project).write_text(json.dumps({"off": True, "time": time.time()}))
        with self.assertRaisesRegex(looprun.LoopError, "switched off"):
            looprun.execute(folder, resume="", check_only=True)
        self.assertEqual((folder / "run.json").read_bytes(), before)
        self.assertEqual(looprun.read_run(folder)["status"], "queued")
        with mock.patch.object(looprun.subprocess, "Popen") as popen, self.assertRaisesRegex(looprun.LoopError, "switched off"):
            looprun.resume(folder)  # the refusal comes before a controller is started, and writes nothing
        popen.assert_not_called()
        self.assertEqual((folder / "run.json").read_bytes(), before)

    def test_a_run_started_without_a_supervisor_is_resumed_without_one(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 3, "watch": watch_script(0)})
        clock = self.dying_clock(T0 + 5)
        executor = TimedExecutor([{"ok": True}], clock)
        with self.assertRaises(Died):
            looprun.start(self.project, "self", structure=self.structure, goal="g", executor=executor, wait=True, clock=clock, supervisor=False)
        (run,) = looprun.list_runs(self.project)
        folder = self.folder(run)
        self.assertIs(looprun.read_run(folder)["timing"]["supervisor"], False)
        with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor") as ensure:
            looprun.resume(folder)
            ensure.assert_not_called()
        # a run started the usual way records no choice, and its resume does ensure one
        clock = self.dying_clock(T0 + 5)
        before = set(looprun.list_runs(self.project))
        with self.assertRaises(Died):
            looprun.start(self.project, "self", structure=self.structure, goal="g", executor=TimedExecutor([{"ok": True}], clock), wait=True, clock=clock)
        (second,) = set(looprun.list_runs(self.project)) - before
        folder2 = self.folder(second)
        self.assertNotIn("supervisor", looprun.read_run(folder2)["timing"])
        with mock.patch.object(looprun.subprocess, "Popen"), mock.patch.object(looptime, "ensure_supervisor") as ensure:
            looprun.resume(folder2)
            self.assertEqual(ensure.call_count, 1)


class RealProcessTests(TimedBase):
    """A real controller, a real supervisor process and the lock between them. No model: the executor is the scripted one, rebuilt in the child."""

    def teardown_process(self, proc):
        def stop():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()
        self.addCleanup(stop)

    def wait_for(self, folder, final=True, seconds=40):
        end = time.time() + seconds
        while time.time() < end:
            state = looprun.read_run(folder)
            if state["status"] in looprun.FINAL if final else state["status"] != "queued":
                return state
            time.sleep(0.1)
        return looprun.read_run(folder)

    def test_a_detached_supervisor_resumes_a_dead_run_with_a_real_controller_process(self):
        self.timed({"kind": "interval", "every": 10, "ticks": 3, "watch": "exit 0"})
        start = time.time() - 30  # a schedule that began half a minute ago: its three ticks are all due
        killed = FakeClock(start, on_sleep=lambda clock: (_ for _ in ()).throw(Died()) if clock.t >= start + 5 else None)
        folder = self.die(killed)
        self.addCleanup(lambda: [os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM)) for pid in [looprun.read_run(folder).get("pid")] if pid and looprun.read_run(folder)["status"] == "running" and pid != os.getpid()])
        self.assertEqual(looprun.read_run(folder)["status"], "running")  # the controller died: nobody holds the run
        self.assertEqual(looprun.shown_status(folder, looprun.read_run(folder)), "interrupted")
        env = {**os.environ, "PYTHONPATH": str(looprun.paths.REPO)}
        proc = subprocess.Popen([sys.executable, "-P", "-m", "agentgraph.looprun", "supervise", "--project", str(self.project), "--every", "5"], cwd=self.project, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.teardown_process(proc)
        state = self.wait_for(folder)
        self.assertEqual(state["status"], "limit", (folder / "run.log").read_text() if (folder / "run.log").exists() else "")
        self.assertNotEqual(state["pid"], os.getpid())
        self.assertNotEqual(state["pid"], proc.pid)  # the supervisor started a controller of its own: a third process
        self.assertEqual(state["timing"]["counts"]["skipped"] + state["timing"]["counts"]["idle"], 3)
        self.assertEqual(proc.wait(timeout=40), 0)  # and the supervisor ended, having nothing left
        out = proc.stdout.read()
        self.assertIn(f"resumed run {folder.name}", out)
        self.assertIn("the supervisor ends", out)

    def test_two_controllers_for_one_run_the_second_gets_busy(self):
        self.timed({"kind": "timebox", "for": 600}, check="exit 1", attempts=None)
        executor = looprun.ScriptedExecutor([{"hang": True}])
        result = []
        thread = threading.Thread(target=lambda: result.append(looprun.start(self.project, "self", structure=self.structure, goal="g", executor=executor, wait=True,
                                                                           supervisor=False)), daemon=True)
        thread.start()
        end = time.time() + 20
        while time.time() < end and not executor.calls:
            time.sleep(0.02)
        self.assertTrue(executor.calls, "the first controller never reached its turn")
        (name,) = looprun.list_runs(self.project)
        folder = self.folder(name)
        self.addCleanup(lambda: (looprun.request_cancel(folder) if looprun.read_run(folder)["status"] not in looprun.FINAL else None, thread.join(20)))
        with mock.patch.object(looprun, "LOCK_WAIT", 0.2):
            with self.assertRaises(looprun.Busy):
                looprun.execute(folder, looprun.ScriptedExecutor([]), resume="")
            with self.assertRaises(looprun.Busy):
                looprun.resume(folder, wait=True, executor=looprun.ScriptedExecutor([]))
        self.assertEqual(looprun.read_run(folder)["status"], "running")  # the loser changed nothing
        looprun.request_cancel(folder)
        thread.join(20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(looprun.read_run(folder)["status"], "cancelled")

    def run_alive(self, timing, **kw):
        """A timed run whose controller (a thread, on the real clock) is stuck in a turn; (folder, thread, executor)."""
        self.timed(timing, check="exit 1", **kw)
        executor = looprun.ScriptedExecutor([{"hang": True}])
        thread = threading.Thread(target=lambda: looprun.start(self.project, "self", structure=self.structure, goal="g", executor=executor, wait=True, supervisor=False),
                                  daemon=True)
        thread.start()
        end = time.time() + 20
        while time.time() < end and not executor.calls:
            time.sleep(0.02)
        self.assertTrue(executor.calls, "the controller never reached its turn")
        (name,) = looprun.list_runs(self.project)
        folder = self.folder(name)
        self.addCleanup(lambda: (looprun.request_cancel(folder) if looprun.read_run(folder)["status"] not in looprun.FINAL else None, thread.join(20)))
        return folder, thread

    def test_cancel_during_a_wake_stops_the_turn_and_ends_the_run_cancelled(self):
        folder, thread = self.run_alive({"kind": "interval", "every": 10, "ticks": 3})
        self.assertEqual(looprun.read_run(folder)["timing"]["phase"], "wake")
        self.assertEqual(looprun.cancel(folder), "requested")
        thread.join(20)
        self.assertFalse(thread.is_alive())
        state = looprun.read_run(folder)
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["timing"]["counts"]["woken"], 0)  # the wake did not finish, so it is not counted as one

    def test_off_against_a_run_whose_controller_is_alive_stops_it(self):
        folder, thread = self.run_alive({"kind": "timebox", "for": 600}, attempts=None)
        done = looptime.switch_off(self.project)
        self.assertEqual(done, [(folder.name, "requested")])
        thread.join(20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(looprun.read_run(folder)["status"], "cancelled")
        self.assertTrue(looptime.is_off(self.project))
