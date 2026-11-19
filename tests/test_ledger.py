import contextlib
import errno
import fcntl
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import ledger


def node(node_id="n1", **overrides):
    return {
        "id": node_id, "type": "implement", "agent": "fixer", "goal": "Make a change",
        "status": "passed", "attempts": 1, "done_when": "Tests pass", "evidence": "Tests passed", **overrides,
    }


def graph(task, **overrides):
    return {
        "task": task, "goal": "Deliver the change", "status": "passed", "created": 1, "updated": 2,
        "objectives": [{"id": "o1", "text": "One objective", "nodes": ["n1"]}],
        "nodes": [node(session="ses_missing")], **overrides,
    }


class Ledger(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.directory = self.project / ".opencode" / "graph"
        self.directory.mkdir(parents=True)
        self.home = self.project / "ledger"
        self.db = self.project / "missing.sqlite"

    def write_graph(self, task, **overrides):
        path = self.directory / f"{task}.json"
        path.write_text(json.dumps(graph(task, **overrides)))
        return path

    def cli(self, *args, now=100.0, env_home=False):
        output, errors = io.StringIO(), io.StringIO()
        argv = list(args) if env_home else ["--home", str(self.home), *args]
        with (patch("agentgraph.ledger.time.time", return_value=now),
              patch.object(ledger.cost, "SETTINGS_LOG", self.project / "cost-settings.jsonl"),
              patch.object(ledger.cost, "CONFIG_PATH", self.project / "opencode.json"),
              patch.object(ledger.sources, "DB_PATH", self.project / "no-such.db"),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
            status = ledger.main(argv)
        return status, output.getvalue(), errors.getvalue()

    def record(self, *args, now=100.0):
        return self.cli("record", "--dir", str(self.directory), "--db", str(self.db), *args, now=now)

    def report(self, *args, now=100.0):
        status, output, errors = self.cli("signals", "--json", *args, now=now)
        self.assertEqual(status, 0, errors)
        return json.loads(output)

    def records(self, filename):
        return [json.loads(line) for line in (self.home / filename).read_text().splitlines()]

    def test_record_upserts_by_absolute_path_and_keeps_summary(self):
        first = self.write_graph(
            "first", revision=2, amendments=["Evaluation clarified"], lessons=["Check the baseline"],
            events=[{"time": 1, "node": "", "event": "discovered", "note": "A follow-up"},
                    {"time": 1.5, "node": "n1", "event": "attempt", "note": "Baseline failed"},
                    {"time": 2, "node": "n1", "event": "attempt", "note": "Tests passed"}],
            nodes=[node(tiers=["fixer", "fixer-opus"], session="ses_missing")],
        )
        second = self.write_graph("second")
        result, output, errors = self.record()
        self.assertEqual(result, 0, errors)
        self.assertEqual(len(output.splitlines()), 3)
        self.assertTrue(output.splitlines()[-1].startswith("not due:"), output)
        runs = {run["path"]: run for run in self.records("runs.jsonl")}
        first, second = first.resolve(), second.resolve()
        self.assertEqual(set(runs), {str(first), str(second)})
        self.assertEqual(runs[str(first)], {
            "path": str(first), "project": str(self.project.resolve()), "task": "first", "goal": "Deliver the change",
            "status": "passed", "outcome": "passed", "revision": 2, "created": 1, "updated": 2, "recorded": 100,
            "duration": 1, "nodes_total": 1, "nodes_passed": 1, "objectives_delivered": 1,
            "objectives_total": 1, "amendments": 1, "lessons": ["Check the baseline"],
            "discovered": ["A follow-up"], "events": {"discovered": 1, "attempt": 2}, "worker_cost": None,
            "worker_cost_sessions": {"referenced": 1, "known": 0},
            "nodes": [{"id": "n1", "type": "implement", "agent": "fixer", "status": "passed", "attempts": 1,
                       "tiers": ["fixer", "fixer-opus"], "escalated": True, "stalled": False,
                       "failure_kind": "", "verdict": ""}],
        })
        self.assertFalse(runs[str(second)]["nodes"][0]["escalated"])
        self.write_graph("first", updated=7)
        with contextlib.chdir(self.project):
            result, output, errors = self.cli("record", "--dir", ".opencode/graph", "--db", str(self.db),
                                              "--task", "first", now=200)
        self.assertEqual(result, 0, errors)
        self.assertEqual(len(output.splitlines()), 2)
        rows = self.records("runs.jsonl")
        self.assertEqual(len(rows), 2)
        by_task = {run["task"]: run for run in rows}
        self.assertEqual(by_task["first"]["updated"], 7)
        self.assertEqual(by_task["first"]["recorded"], 200)
        self.assertEqual(by_task["second"]["recorded"], 100)

    def test_record_uses_shared_capped_listing_and_refuses_symlinks(self):
        for i in range(203):
            path = self.write_graph(f"task-{i:03}")
            os.utime(path, ns=(1_000_000_000 + i, 1_000_000_000 + i))
        target = self.project / "target.txt"
        target.write_text(json.dumps(graph("symlink-target")))
        link = self.directory / "link.json"
        link.symlink_to(target)
        with patch.object(ledger.sources, "graph_files", wraps=ledger.sources.graph_files) as listing:
            result, output, errors = self.record()
        listing.assert_called_once_with(self.directory.resolve())
        self.assertEqual(result, 2, errors)
        self.assertIn("skipped 4", output)
        self.assertIn("link.json", output)
        self.assertEqual({run["task"] for run in self.records("runs.jsonl")},
                         {f"task-{i:03}" for i in range(4, 203)})

    def test_record_rejects_foreign_owned_graph_directory(self):
        self.write_graph("foreign")
        with patch("agentgraph.sources.os.getuid", return_value=os.getuid() + 1):
            result, output, errors = self.record()
        self.assertEqual(result, 2, output + errors)
        self.assertIn("not owned", output + errors)
        self.assertFalse((self.home / "runs.jsonl").exists())

    def test_same_path_can_hold_multiple_runs_and_abandonment_uses_mtime(self):
        self.write_graph("reused", created=1, updated=2)
        self.assertEqual(self.record(now=2)[0], 0)
        self.write_graph("reused", created=3, updated=4)
        self.assertEqual(self.record(now=4)[0], 0)
        path = self.write_graph("reused", status="running", updated=51)
        data = json.loads(path.read_text())
        del data["created"]
        path.write_text(json.dumps(data))
        os.utime(path, (50, 50))
        self.assertEqual(self.record("--task", "reused", "--abandoned", now=52)[0], 0)
        self.write_graph("reused", status="blocked", created=0, updated=61)
        os.utime(path, (60, 60))
        self.assertEqual(self.record("--task", "reused", "--abandoned", now=62)[0], 0)
        self.assertEqual({run["created"]: run["recorded"] for run in self.records("runs.jsonl")},
                         {1: 2, 3: 4, 50: 52, 60: 62})

    def test_stale_run_does_not_overwrite_newer_evidence(self):
        self.write_graph("one", updated=10, lessons=["new evidence"])
        self.assertEqual(self.record(now=11)[0], 0)
        runs = self.home / "runs.jsonl"
        original = runs.stat().st_ino, runs.read_bytes()
        self.write_graph("one", updated=9, lessons=["old evidence"])
        code, output, _ = self.record(now=12)
        self.assertEqual(code, 0)
        self.assertIn("kept newer stored run", output)
        self.assertEqual((runs.stat().st_ino, runs.read_bytes()), original)
        self.assertEqual(self.report()["lessons"], [{"task": "one", "text": "new evidence"}])

    def test_abandonment_requires_and_records_only_one_requested_task(self):
        self.write_graph("running", status="running")
        self.write_graph("blocked", status="blocked")
        self.write_graph("finished")
        code, output, _ = self.record()
        self.assertEqual(code, 0)
        self.assertIn("skipped running: unfinished", output)
        self.assertIn("skipped blocked: unfinished", output)
        self.assertEqual([run["task"] for run in self.records("runs.jsonl")], ["finished"])
        path = self.home / "runs.jsonl"
        original = path.stat().st_ino, path.read_bytes()
        code, output, _ = self.record("--task", "absent")
        self.assertEqual(code, 2)
        self.assertIn("no graph matches task 'absent'", output)
        self.assertEqual((path.stat().st_ino, path.read_bytes()), original)
        code, _, errors = self.record("--abandoned")
        self.assertEqual(code, 2)
        self.assertIn("requires --task", errors)
        self.assertEqual((path.stat().st_ino, path.read_bytes()), original)
        self.assertEqual(self.record("--abandoned", "--task", "running")[0], 0)
        self.assertEqual({run["task"]: run["outcome"] for run in self.records("runs.jsonl")},
                         {"running": "abandoned", "finished": "passed"})
        self.assertEqual(self.report()["outcomes"], {"abandoned": 1, "passed": 1, "unknown": 0})

    def test_cannot_abandon_a_passed_graph_or_overwrite_a_passed_outcome(self):
        self.write_graph("finished")
        code, output, _ = self.record("--abandoned", "--task", "finished")
        self.assertEqual(code, 2)
        self.assertIn("cannot abandon a passed graph", output)
        runs = self.home / "runs.jsonl"
        self.assertFalse(runs.exists())
        self.assertEqual(self.record()[0], 0)
        original = runs.stat().st_ino, runs.read_bytes()
        self.write_graph("finished", status="running", updated=3)
        code, output, _ = self.record("--abandoned", "--task", "finished")
        self.assertEqual(code, 2)
        self.assertIn("already recorded as passed", output)
        self.assertEqual((runs.stat().st_ino, runs.read_bytes()), original)

    def test_outcome_unknown_for_legacy_rows_and_not_in_abandonment_rate(self):
        self.write_graph("legacy", status="blocked")
        self.assertEqual(self.record("--abandoned", "--task", "legacy")[0], 0)
        runs = self.home / "runs.jsonl"
        legacy, = self.records("runs.jsonl")
        del legacy["outcome"]
        runs.write_text(json.dumps(legacy) + "\n")
        self.cli("improved", "--file", "fixer.md", "--summary", "Applied", now=10)
        self.assertIsNone(self.report()["trend"]["before"]["abandoned_rate"])
        self.write_graph("known", status="running")
        self.assertEqual(self.record("--abandoned", "--task", "known")[0], 0)
        report = self.report()
        self.assertEqual(report["outcomes"], {"passed": 0, "abandoned": 1, "unknown": 1})
        self.assertEqual(report["trend"]["before"]["abandoned_rate"], 1)

    def test_record_rejects_invalid_passed_graph_and_uses_shared_validator(self):
        self.write_graph("empty", nodes=[], objectives=[])
        self.write_graph("failed", nodes=[node(status="failed", evidence="Tests failed")])
        self.write_graph("good")
        with patch.object(ledger.check, "read_graph", wraps=ledger.check.read_graph) as validate:
            code, output, errors = self.record()
        self.assertEqual(code, 1, errors)
        self.assertEqual({call.args[0].stem for call in validate.call_args_list}, {"empty", "failed", "good"})
        for task in ("empty", "failed"):
            self.assertRegex(output, rf"not recorded {task}: [1-9]\d* problems \(run agentgraph check\)")
        self.assertIn("  - ", output)
        self.assertEqual([run["task"] for run in self.records("runs.jsonl")], ["good"])
        (self.directory / "bad.json").write_text("{bad json")
        self.assertEqual(self.record()[0], 2)

    def test_record_saves_the_validated_snapshot_with_one_open(self):
        path = self.write_graph("changing", lessons=["valid snapshot"]).resolve()
        read_graph = ledger.check.read_graph
        read_text = ledger.check.read_graph_text
        reads = []

        def counted_read(p):
            if Path(p).resolve() == path:
                reads.append(Path(p).resolve())
            return read_text(p)

        def read_then_replace(p):
            result = read_graph(p)
            replacement = p.with_suffix(".new")
            replacement.write_text(json.dumps(graph("changing", lessons=["wrong snapshot"], nodes=[])))
            replacement.replace(p)
            return result

        with patch.object(ledger.check, "read_graph_text", side_effect=counted_read), \
                patch.object(ledger.check, "read_graph", side_effect=read_then_replace):
            self.assertEqual(self.record()[0], 0)
        self.assertEqual(reads, [path])
        self.assertEqual(self.records("runs.jsonl")[0]["lessons"], ["valid snapshot"])
        self.assertEqual(json.loads(path.read_text())["lessons"], ["wrong snapshot"])

    def test_passed_run_needs_stable_created_even_if_validator_returns_no_problems(self):
        path = self.write_graph("no-identity")
        sound, problems = ledger.check.read_graph(path)
        self.assertEqual(problems, [])
        for created in (0, -1, float("inf"), float("nan")):
            with self.subTest(created=created):
                sound.created = created
                with patch.object(ledger.check, "read_graph", return_value=(sound, [])):
                    code, output, _ = self.record()
                self.assertEqual(code, 1)
                self.assertIn("created must be a finite number greater than 0", output)
                self.assertFalse((self.home / "runs.jsonl").exists())
        for created in (None, 0):
            raw = graph("no-identity", created=created)
            if created is None:
                del raw["created"]
            path.write_text(json.dumps(raw))
            self.assertEqual(self.record()[0], 1)

    def test_changed_snapshot_with_equal_updated_becomes_pending_again(self):
        self.write_graph("same", revision=1, lessons=["old"])
        self.assertEqual(self.record(now=3)[0], 0)
        self.cli("improved", "--file", "fixer.md", "--summary", "Applied", now=4)
        self.assertEqual(self.report()["pending"]["runs"], 0)
        self.write_graph("same", revision=2, lessons=["new"], nodes=[node(attempts=2)])
        self.assertEqual(self.record(now=5)[0], 0)
        run, = self.records("runs.jsonl")
        self.assertEqual((run["revision"], run["recorded"], run["lessons"]), (2, 5, ["new"]))
        self.assertEqual(self.report()["pending"]["runs"], 1)
        self.assertEqual(self.record(now=6)[0], 0)
        self.assertEqual(self.records("runs.jsonl"), [run])

    def test_record_finds_directory_and_handles_no_directory(self):
        # Discovery stops at the user's home, so treat the temp project as inside it.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(Path, "home", return_value=self.project.parent))
        with contextlib.chdir(self.project):
            result, output, errors = self.cli("record", "--db", str(self.db))
        self.assertEqual(result, 0, errors)
        self.assertTrue(output.startswith("not due:"), output)
        self.assertFalse(self.home.exists())
        self.directory.rmdir()
        with contextlib.chdir(self.project):
            result, output, errors = self.cli("record", "--db", str(self.db))
        self.assertEqual(result, 0, errors)
        self.assertIn("no .opencode/graph directory found", output)
        self.assertEqual(len(output.splitlines()), 1)

    def test_explicit_missing_directory_is_an_error(self):
        code, output, _ = self.cli("record", "--dir", str(self.project / "missing"), "--db", str(self.db))
        self.assertEqual(code, 2)
        self.assertIn("not a directory", output)
        self.assertFalse(self.home.exists())

    def test_exact_json_integers_survive_summary_and_report(self):
        huge = 10**400 + 1  # Not representable even approximately as a finite float.
        self.write_graph("exact", revision=2**53 + 1, nodes=[node(attempts=huge)])
        self.assertEqual(self.record()[0], 0)
        run, = self.records("runs.jsonl")
        self.assertEqual(run["revision"], 2**53 + 1)
        self.assertEqual(run["nodes"][0]["attempts"], huge)
        self.assertEqual(self.report()["agents"]["fixer"]["mean_attempts"], huge)
        code, output, _ = self.cli("signals")
        self.assertEqual(code, 0)
        self.assertIn(str(huge), output)
        self.write_graph("fractional", nodes=[node(attempts=huge), node("n2", attempts=huge + 1)])
        self.assertEqual(self.record("--task", "fractional", now=3)[0], 0)
        self.cli("improved", "--file", "fixer.md", "--summary", "Applied", now=4)
        report = self.report("--all")
        self.assertIsNone(report["agents"]["fixer"]["mean_attempts"])
        self.assertIsNone(report["trend"]["before"]["mean_attempts"])
        self.assertEqual(self.cli("signals", "--all")[0], 0)

    def test_record_still_writes_readable_graph_when_any_is_unreadable(self):
        self.write_graph("good")
        (self.directory / "bad.json").write_text("{unreadable")
        result, output, errors = self.record("--task", "good")
        self.assertEqual(result, 2, errors)
        self.assertIn("UNREADABLE", output)
        self.assertIn("bad.json", output)
        self.assertTrue(output.splitlines()[-1].startswith("not due:"), output)
        self.assertEqual([run["task"] for run in self.records("runs.jsonl")], ["good"])

    def make_db(self, *rows):
        with contextlib.closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("""CREATE TABLE session_v2 (
                id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, model TEXT, title TEXT, directory TEXT,
                cost REAL, tokens_input INTEGER, tokens_output INTEGER, time_created INTEGER,
                time_updated INTEGER, time_busy INTEGER, time_idle INTEGER)""")
            conn.executemany("INSERT INTO session_v2 (id, parent_id, cost) VALUES (?, ?, ?)", rows)

    def test_cost_counts_unique_referenced_sessions_including_other_trees(self):
        self.make_db(("root", None, 100), ("worker", "root", 0.25),
                     ("other-root", None, 200), ("other-worker", "other-root", 0.5),
                     ("unrelated", "root", 100))
        original = self.db.read_bytes()
        self.write_graph("priced", session="root", nodes=[
            node(session="worker"), node("n2", session="worker"), node("n3", session="other-worker"),
            node("n4", session="not-present"),
        ])
        self.write_graph("missing")
        self.assertEqual(self.record()[0], 0)
        costs = {run["task"]: run["worker_cost"] for run in self.records("runs.jsonl")}
        self.assertEqual(costs, {"priced": 0.75, "missing": None})
        self.assertEqual(self.db.read_bytes(), original)

    def test_rerecord_preserves_known_worker_cost_and_stable_evidence_time(self):
        self.make_db(("paid", None, 2.5))
        self.write_graph("priced", nodes=[node(session="paid")])
        self.assertEqual(self.record(now=3)[0], 0)
        self.db = self.project / "unavailable.sqlite"
        self.assertEqual(self.record(now=4)[0], 0)
        run, = self.records("runs.jsonl")
        self.assertEqual((run["recorded"], run["worker_cost"]), (3, 2.5))
        self.write_graph("priced", updated=3, nodes=[node(session="paid")])
        self.assertEqual(self.record(now=5)[0], 0)
        run, = self.records("runs.jsonl")
        self.assertEqual((run["recorded"], run["worker_cost"]), (5, 2.5))

    def test_worker_cost_coverage_excludes_partial_and_empty_runs_from_trend(self):
        self.make_db(("paid", None, 2.5), ("zero", None, 0), ("null", None, None))
        self.write_graph("complete", nodes=[node(session="paid"), node("n2", session="zero")])
        self.write_graph("zero", nodes=[node(session="zero")])
        self.write_graph("partial", nodes=[node(session="paid"), node("n2", session="null"),
                                            node("n3", session="missing"), node("n4", session="paid")])
        self.write_graph("empty", nodes=[node()])
        self.assertEqual(self.record(now=3)[0], 0)
        self.assertEqual({row["task"]: (row["worker_cost"], row["worker_cost_sessions"])
                          for row in self.records("runs.jsonl")}, {
            "complete": (2.5, {"referenced": 2, "known": 2}), "zero": (0, {"referenced": 1, "known": 1}),
            "partial": (2.5, {"referenced": 3, "known": 1}), "empty": (None, {"referenced": 0, "known": 0}),
        })
        self.assertEqual(self.cli("improved", "--file", "fixer.md", "--summary", "Applied", now=4)[0], 0)
        before = self.report()["trend"]["before"]
        self.assertEqual((before["mean_worker_cost"], before["cost_runs"]), (1.25, 2))

    def test_feedback_appends_and_rejects_blank_without_writing(self):
        for text in ("", " \t\n"):
            status, _, errors = self.cli("feedback", text)
            self.assertEqual(status, 2)
            self.assertIn("empty", errors)
            self.assertFalse(self.home.exists())
        self.assertEqual(self.cli("feedback", "Try this", "--task", "one", "--agent", "fixer", now=1)[0], 0)
        self.assertEqual(self.cli("feedback", "Try\nthat", now=2)[0], 0)
        self.assertEqual(self.records("feedback.jsonl"), [
            {"time": 1, "text": "Try this", "agent": "fixer", "task": "one"},
            {"time": 2, "text": "Try\nthat", "agent": None, "task": None},
        ])
        self.assertEqual(self.cli("feedback", "  ")[0], 2)
        self.assertEqual(len(self.records("feedback.jsonl")), 2)

    def test_stdin_feedback_is_literal_and_requires_exactly_one_nonblank_input(self):
        for args in ((), ("--stdin", "positional"), ("--stdin",)):
            with patch("sys.stdin", io.StringIO(" \t\n")):
                code, _, errors = self.cli("feedback", *args)
            self.assertEqual(code, 2)
            self.assertTrue(errors)
            self.assertFalse(self.home.exists())
        literal = 'use $HOME and `x` and "quotes" plus \'single quotes\''
        text = " \n" + literal + "  \n"
        with patch("sys.stdin", io.StringIO(text + "\n")):
            self.assertEqual(self.cli("feedback", "--stdin", "--agent", "fixer")[0], 0)
        self.assertEqual(self.records("feedback.jsonl")[0]["text"].encode(), text.encode())

    def test_feedback_text_file_keeps_whitespace_and_does_not_remove_input(self):
        path = self.project / "feedback data.txt"
        content = b'  keep "$HOME"\r\nFEEDBACK\n`x`  \n\n'
        path.write_bytes(content)
        for extra in (("--stdin",), ("simultaneous text",)):
            self.assertEqual(self.cli("feedback", "--text-file", str(path), *extra)[0], 2)
            self.assertFalse(self.home.exists())
        self.assertEqual(self.cli("feedback", "--text-file", str(path), "--agent", "fixer")[0], 0)
        self.assertEqual(self.records("feedback.jsonl")[0]["text"].encode(), content[:-1])
        self.assertEqual(path.read_bytes(), content)

    def test_feedback_help_describes_both_input_channels_newline_rule(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
            ledger.main(["feedback", "--help"])
        self.assertEqual(exit_code.exception.code, 0)
        help_text = " ".join(output.getvalue().split())
        self.assertEqual(help_text.count("one trailing newline is removed; everything else is stored as given"), 2)

    def test_improved_json_file_preserves_metadata_and_rejects_mixed_or_invalid_input(self):
        path = self.project / "improvement data.json"
        metadata = {"file": ["fixer.md", "reviewer.md"], "summary": '  "$HOME"\nIMPROVEMENT\n`x`',
                    "signals": ['a "quote"', "two\nlines"], "metric": "a metric", "seen_until": 9.5}
        content = json.dumps(metadata).encode()
        path.write_bytes(content)
        for option in ("--file", "--summary", "--signal", "--metric", "--seen-until"):
            self.assertEqual(self.cli("improved", "--json-file", str(path), option, "10")[0], 2)
            self.assertFalse(self.home.exists())
        self.assertEqual(self.cli("improved", "--json-file", str(path), now=12)[0], 0)
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(self.report()["last_improvement"], {"time": 12, **metadata})
        self.assertEqual(self.report()["watermark"], 9.5)
        for invalid in (b"[1]", b"broken json", b'{"file":"fixer.md"}', b'{"file":[1],"summary":"x"}'):
            path.write_bytes(invalid)
            self.assertEqual(self.cli("improved", "--json-file", str(path))[0], 2)
            self.assertEqual(path.read_bytes(), invalid)
        self.assertEqual(len(self.records("improvements.jsonl")), 1)

    def test_signals_aggregate_agents_and_gates(self):
        self.write_graph("one", status="blocked", nodes=[
            node(),
            node("n2", attempts=3, tiers=["fixer", "explorer", "explorer", "fixer-opus"], failure_kind="code", stalled=True),
            node("n3", agent="fixer", tiers=["fixer-opus"]),
            node("n4", agent="designer", status="blocked", attempts=2, stalled=True, failure_kind="input"),
            node("n5", agent="unstarted", attempts=0),
            node("n6", type="gate", agent="reviewer", verdict="confirmed"),
            node("n7", type="gate", agent="reviewer", verdict="refuted", status="failed"),
            node("n8", type="gate", agent="reviewer", status="blocked", verdict="unverifiable", attempts=0),
            node("n9", type="check", agent="reviewer"),
            node("n10", status="failed", attempts=2, failure_kind="evaluation"),
        ], amendments=["A revision"], lessons=["Record the baseline"],
            events=[{"time": 2, "node": "", "event": "discovered", "note": "A separate issue"}])
        self.assertEqual(self.record("--task", "one", "--abandoned")[0], 0)
        report = self.report(now=123)
        self.assertEqual(report["generated"], 123)
        self.assertIsNone(report["last_improvement"])
        self.assertIsNone(report["trend"])
        self.assertEqual(report["amendments"], 1)
        self.assertEqual(report["lessons"], [{"task": "one", "text": "Record the baseline"}])
        self.assertEqual(report["discovered"], [{"task": "one", "note": "A separate issue"}])
        self.assertEqual(report["gates"], {"confirmed": 1, "refuted": 1, "unverifiable": 1})
        self.assertEqual(report["outcomes"], {"abandoned": 1, "passed": 0, "unknown": 0})
        self.assertEqual(report["pending"]["runs"], 1)
        self.assertEqual(report["escalations"], [{"task": "one", "node": "n2",
            "tiers": ["fixer", "explorer", "fixer-opus"], "attempts": 3,
            "stalled": True, "failure_kind": "code"}])
        self.assertEqual(report["agents"], {
            "fixer": dict(nodes=2, passed=1, first_pass=1, first_pass_rate=1 / 3, mean_attempts=1.5,
                          escalated_away=1, escalated_to=0, stalled=0, failure_kinds={"evaluation": 1}),
            "explorer": dict(nodes=0, passed=0, first_pass=0, first_pass_rate=0, mean_attempts=None,
                             escalated_away=1, escalated_to=1, stalled=0, failure_kinds={}),
            "fixer-opus": dict(nodes=1, passed=1, first_pass=1, first_pass_rate=1, mean_attempts=1,
                               escalated_away=0, escalated_to=1, stalled=0, failure_kinds={}),
            "designer": dict(nodes=1, passed=0, first_pass=0, first_pass_rate=0, mean_attempts=2,
                              escalated_away=0, escalated_to=0, stalled=1, failure_kinds={"input": 1}),
            "reviewer": dict(nodes=3, passed=2, first_pass=2, first_pass_rate=2 / 3, mean_attempts=1,
                             escalated_away=0, escalated_to=0, stalled=0, failure_kinds={}),
        })
        result, output, errors = self.cli("signals")
        self.assertEqual(result, 0, errors)
        self.assertTrue(output.startswith("not due:"), output)
        for expected in ("Record the baseline", "fixer-opus", "A separate issue", "Trend:"):
            self.assertIn(expected, output)

    def test_resume_same_agent_is_not_a_handoff(self):
        self.write_graph("resumed", nodes=[node(tiers=["explorer", "explorer"])])
        self.assertEqual(self.record(now=3)[0], 0)
        self.assertFalse(self.records("runs.jsonl")[0]["nodes"][0]["escalated"])
        self.cli("improved", "--file", "explorer.md", "--summary", "Applied", now=4)
        report = self.report("--all")
        self.assertEqual(report["escalations"], [])
        agent = report["agents"]["explorer"]
        self.assertEqual((agent["first_pass_rate"], agent["escalated_away"], agent["escalated_to"]), (1, 0, 0))
        before = report["trend"]["before"]
        self.assertEqual((before["first_pass_rate"], before["escalation_rate"]), (1, 0))

    def test_due_thresholds_are_independent(self):
        self.assertEqual(ledger.DUE_RUNS, 5)
        self.assertEqual(ledger.DUE_FEEDBACK, 2)
        for count in range(1, ledger.DUE_RUNS + 1):
            self.write_graph(f"run-{count}")
            status, output, errors = self.record(now=10)
            self.assertEqual(status, 0, errors)
            due = count == ledger.DUE_RUNS
            self.assertEqual(self.report()["due"], due)
            self.assertTrue(output.splitlines()[-1].startswith("improvement due:" if due else "not due:"), output)
        self.assertEqual(self.cli("improved", "--file", "fixer.md", "--summary", "Applied", now=20)[0], 0)
        for count in range(1, ledger.DUE_FEEDBACK + 1):
            self.assertEqual(self.cli("feedback", "Improve this", now=20 + count)[0], 0)
            result = self.report()
            self.assertEqual(result["pending"], {"runs": 0, "feedback": count})
            self.assertEqual(result["due"], count == ledger.DUE_FEEDBACK)

    def test_improved_splits_at_latest_time_and_all_restores_history(self):
        self.make_db(("paid", None, 2.5))
        self.write_graph("early", updated=9, lessons=["old lesson"], nodes=[node(session="paid")])
        self.assertEqual(self.record(now=6)[0], 0)
        self.cli("feedback", "old feedback", now=5)
        self.cli("feedback", "report boundary feedback", now=7)
        snapshot = self.report(now=7)
        self.assertIsNone(snapshot["watermark"])
        self.cli("feedback", "between report and improvement", now=8)
        prompt_file = self.project / "fixer.md"
        prompt_file.write_text("original prompt")
        status, _, errors = self.cli("improved", "--file", str(prompt_file), "--summary", "Record a prompt change",
                                     "--signal", "old lesson", "--signal", "feedback", "--metric", "first pass",
                                     "--seen-until", str(snapshot["generated"]), now=10)
        self.assertEqual(status, 0, errors)
        self.assertEqual(prompt_file.read_text(), "original prompt")
        self.cli("improved", "--file", "older.md", "--summary", "Older", now=3)
        at_cutoff = self.report()
        self.assertEqual(at_cutoff["watermark"], 7)
        self.assertEqual(at_cutoff["pending"], {"runs": 0, "feedback": 1})
        self.assertEqual([item["text"] for item in at_cutoff["feedback"]], ["between report and improvement"])
        self.assertEqual(at_cutoff["trend"]["after"], dict(runs=0, first_pass_rate=None, mean_attempts=None,
            escalation_rate=None, stall_rate=None, unverifiable_rate=None, mean_worker_cost=None,
            abandoned_rate=None, cost_runs=0))
        self.write_graph("late-recorded", status="blocked", created=9, updated=12, lessons=["late old lesson"], nodes=[
            node(status="failed"), node("g1", type="gate", status="blocked", verdict="unverifiable", attempts=0),
            node("g2", type="gate", verdict="confirmed"), node("g3", type="gate", status="pending", attempts=0),
        ])
        self.assertEqual(self.record("--task", "late-recorded", "--abandoned", now=12)[0], 0)
        self.write_graph("new-prompt", created=10, updated=11, lessons=["new lesson"],
                         nodes=[node(type="design", attempts=1, tiers=["fixer", "designer"], stalled=True),
                                node("c", type="check", attempts=8)])
        self.assertEqual(self.record(now=13)[0], 0)
        self.cli("feedback", "latest feedback", now=12)
        self.cli("feedback", "new feedback", now=11)
        pending = self.report()
        historical = self.report("--all")
        self.assertEqual(pending["pending"], {"runs": 2, "feedback": 3})
        self.assertEqual(pending["trend"], {
            "before": dict(runs=2, first_pass_rate=0.5, mean_attempts=1, escalation_rate=0, stall_rate=0,
                           unverifiable_rate=0.5, mean_worker_cost=2.5, abandoned_rate=0.5, cost_runs=1),
            "after": dict(runs=1, first_pass_rate=0, mean_attempts=1, escalation_rate=1, stall_rate=1,
                          unverifiable_rate=None, mean_worker_cost=None, abandoned_rate=0, cost_runs=0),
        })
        self.assertEqual(pending["last_improvement"], {
            "time": 10, "file": str(prompt_file), "summary": "Record a prompt change",
            "signals": ["old lesson", "feedback"], "metric": "first pass", "seen_until": 7,
        })
        self.assertEqual(pending["improvements"], [
            {"time": 3, "file": "older.md", "summary": "Older", "signals": [], "metric": None},
            pending["last_improvement"],
        ])
        self.assertEqual([item["text"] for item in pending["feedback"]],
                         ["between report and improvement", "new feedback", "latest feedback"])
        self.assertEqual([item["text"] for item in historical["feedback"]],
                         ["old feedback", "report boundary feedback", "between report and improvement",
                          "new feedback", "latest feedback"])
        self.assertEqual({item["task"] for item in pending["lessons"]}, {"late-recorded", "new-prompt"})
        self.assertEqual(pending["outcomes"], {"passed": 1, "abandoned": 1, "unknown": 0})
        self.assertEqual(historical["outcomes"], {"passed": 2, "abandoned": 1, "unknown": 0})
        self.assertEqual({item["text"] for item in historical["lessons"]},
                         {"old lesson", "late old lesson", "new lesson"})
        for field in ("pending", "due", "trend"):
            self.assertEqual(historical[field], pending[field])
        code, human, _ = self.cli("signals")
        self.assertEqual(code, 0)
        self.assertIn("known worker sessions only", human)

    def test_malformed_jsonl_does_not_hide_valid_lines_or_break_upserts(self):
        self.write_graph("one", updated=3)
        self.record()
        self.cli("feedback", "first", now=3)
        self.cli("improved", "--file", "fixer.md", "--summary", "First", now=1)
        corrupt = b"{bad JSON\r\nnull\n\xff\n{}\n"
        tail = b"{broken tail"
        for filename in ("runs.jsonl", "feedback.jsonl", "improvements.jsonl"):
            file = self.home / filename
            file.write_bytes(corrupt + file.read_bytes() + tail)
        self.assertEqual(self.cli("feedback", "second", now=4)[0], 0)
        self.assertEqual(self.cli("improved", "--file", "fixer.md", "--summary", "Second", now=2)[0], 0)
        self.assertEqual(self.report()["pending"], {"runs": 1, "feedback": 2})
        self.write_graph("another", updated=5)
        self.assertEqual(self.record("--task", "another")[0], 0)
        rewritten = (self.home / "runs.jsonl").read_bytes()
        self.assertTrue(rewritten.startswith(corrupt))
        self.assertTrue(rewritten.endswith(tail))
        self.assertEqual({json.loads(row)["task"] for row in rewritten[len(corrupt):-len(tail)].splitlines()},
                         {"one", "another"})
        self.assertEqual(self.report()["pending"], {"runs": 2, "feedback": 2})
        self.assertEqual([item["summary"] for item in self.report()["improvements"]], ["First", "Second"])

    def test_read_errors_abort_before_replacing_runs(self):
        self.write_graph("first")
        self.assertEqual(self.record()[0], 0)
        self.assertEqual(self.cli("feedback", "one")[0], 0)
        runs = self.home / "runs.jsonl"
        original = runs.stat().st_ino, runs.read_bytes()
        self.write_graph("second")
        open_path = Path.open
        for filename in ("runs.jsonl", "feedback.jsonl"):
            def fail(path, mode="r", *args, **kwargs):
                if path.name == filename and mode == "rb":
                    raise OSError(errno.EIO, "injected read failure", str(path))
                return open_path(path, mode, *args, **kwargs)

            with self.subTest(filename=filename), patch.object(Path, "open", fail):
                code, _, errors = self.record()
                self.assertEqual(code, 2)
                self.assertIn("injected read failure", errors)
                self.assertEqual(self.cli("signals", "--json")[0], 2)
            self.assertEqual((runs.stat().st_ino, runs.read_bytes()), original)

    def test_concurrent_recorders_share_the_ledger_lock(self):
        self.write_graph("first")
        other = self.project / "other" / ".opencode" / "graph"
        other.mkdir(parents=True)
        (other / "second.json").write_text(json.dumps(graph("second", created=3, updated=4)))
        self.home.mkdir()
        prefix = [sys.executable, "-B", "-m", "agentgraph.ledger", "--home", str(self.home)]
        commands = [prefix + ["record", "--dir", str(directory), "--db", str(self.db)]
                    for directory in (self.directory, other)]
        commands.append(prefix + ["feedback", "from a separate process"])
        cwd = Path(ledger.__file__).resolve().parents[1]
        with contextlib.ExitStack() as stack:
            with (self.home / ".lock").open("wb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                processes = [stack.enter_context(subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                                                                  stderr=subprocess.PIPE, text=True))
                             for command in commands]
                for process in processes:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        process.wait(timeout=0.2)
            for process in processes:
                _, errors = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, errors)
        self.assertEqual({run["task"] for run in self.records("runs.jsonl")}, {"first", "second"})
        self.assertEqual(self.records("feedback.jsonl")[0]["text"], "from a separate process")

    def test_graph_files_are_loaded_under_the_exclusive_ledger_lock(self):
        self.write_graph("one")
        self.home.mkdir()
        lock_path = self.home / ".lock"
        lock_path.touch()
        read_graph = ledger.check.read_graph

        def check_locked(path):
            with lock_path.open("rb") as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return read_graph(path)

        with patch.object(ledger.check, "read_graph", side_effect=check_locked) as read:
            self.assertEqual(self.record()[0], 0)
            read.assert_called_once()

    def test_signals_can_read_without_creating_or_writing_the_lock(self):
        self.assertEqual(self.report()["pending"], {"runs": 0, "feedback": 0})
        self.assertFalse(self.home.exists())
        self.assertEqual(self.cli("feedback", "history")[0], 0)
        lock = self.home / ".lock"
        lock.unlink()
        self.assertEqual(self.report()["pending"]["feedback"], 1)
        self.assertEqual({path.name for path in self.home.iterdir()}, {"feedback.jsonl"})
        lock.touch()
        op = Path.open

        def read_only(path, mode="r", *args, **kwargs):
            if path.parent == self.home and any(flag in mode for flag in "awx+"):
                raise PermissionError(errno.EACCES, "read-only ledger", str(path))
            return op(path, mode, *args, **kwargs)

        with patch.object(Path, "open", read_only), patch.object(ledger.fcntl, "flock", wraps=fcntl.flock) as flock:
            self.assertEqual(self.report()["pending"]["feedback"], 1)
            self.assertEqual([call.args[1] for call in flock.call_args_list], [fcntl.LOCK_SH])

    def test_signals_warns_but_reads_when_lock_cannot_be_opened_or_acquired(self):
        self.assertEqual(self.cli("feedback", "history")[0], 0)
        op = Path.open

        def denied_lock(path, mode="r", *args, **kwargs):
            if path == self.home / ".lock":
                raise PermissionError(errno.EACCES, "lock denied")
            return op(path, mode, *args, **kwargs)

        for failing_lock in (patch.object(Path, "open", denied_lock),
                             patch.object(ledger.fcntl, "flock", side_effect=OSError(errno.EIO, "lock failure"))):
            with failing_lock:
                code, output, errors = self.cli("signals", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["pending"]["feedback"], 1)
            self.assertEqual(len(errors.splitlines()), 1)
            self.assertIn("warning: reading without a lock", errors)
        (self.home / ".lock").unlink()
        self.assertEqual(self.cli("signals", "--json")[2], "")

    def test_home_flag_overrides_environment(self):
        env_directory = self.project / "env-ledger"
        with patch.dict(os.environ, {"AGENTGRAPH_HOME": str(env_directory)}):
            self.assertEqual(self.cli("feedback", "env", env_home=True)[0], 0)
            self.assertFalse(self.home.exists())
            self.assertEqual(self.cli("feedback", "flag")[0], 0)
            self.assertEqual(self.cli("feedback", "after", "--home", str(self.home), env_home=True)[0], 0)
            code, output, errors = self.cli("signals", "--json", env_home=True)
        self.assertEqual(code, 0, errors)
        self.assertEqual([item["text"] for item in json.loads(output)["feedback"]], ["env"])
        self.assertEqual([item["text"] for item in self.report()["feedback"]], ["flag", "after"])


    def test_signals_include_the_chat_cost_trend_only_when_given_a_database(self):
        from tests.test_cost import make_db
        chats = self.project / "chats.db"
        make_db(chats, [("ses_a", None, 5.0, 99.0)], [("ses_a", n, 90.0 + n, "m", 60_000, 0) for n in range(2)])
        log = self.project / "cost-settings.jsonl"
        self.assertIsNone(self.report()["cost"])   # the command's default is the (here patched, missing) real database
        with_db = self.report("--cost-db", str(chats))
        (period,) = with_db["cost"]["periods"]
        self.assertEqual((period["turns"], period["chats"], period["since"]), (2, 1, None))
        self.assertTrue(log.exists())      # the command noted the settings
        self.assertEqual(len(log.read_text().splitlines()), 1)
        self.assertEqual(self.report("--cost-db", str(chats), "--no-cost").get("cost", "absent"), "absent")
        self.assertIsNone(self.report("--cost-db", str(self.project / "missing.db"))["cost"])
        self.assertIsNone(ledger.signals(self.home, 100.0, cost_db=self.project / "missing.db")["cost"])
        self.assertNotIn("cost", ledger.signals(self.home, 100.0))
        text = self.cli("signals", "--cost-db", str(chats))[1]
        self.assertIn("a turn over 2 turns in 1 chats", text)
        self.assertNotIn("Chat cost", self.cli("signals", "--no-cost")[1])


if __name__ == "__main__":
    unittest.main()
