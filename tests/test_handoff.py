import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agentgraph import handoff

SID = "ses_testsession0001"
SECRET = "SECRET-TASK-TEXT-7731"
NOTES = "## Next step\nRun the check script, then write facts/last.txt.\n"
ROOT = Path(__file__).resolve().parent.parent


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = handoff.main(argv)
        except SystemExit as exc:  # argparse
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.db = self.tmp / "opencode.db"
        db = sqlite3.connect(self.db)
        db.execute("CREATE TABLE session_message (session_id TEXT, seq INTEGER, type TEXT, data TEXT)")
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 2, "assistant", json.dumps({"text": "assistant reply text"})))
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 7, "user", json.dumps({"time": {"created": 1_700_000_000_000}, "text": f"first task {SECRET}", "files": []})))
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 9, "user", json.dumps({"time": {"created": 1_700_000_100_000}, "text": "second message SECOND-TEXT", "files": []})))
        db.commit()
        db.close()

    def notes(self, text=NOTES, age=0):
        path = handoff.notes_path(SID, self.work)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if age:
            old = time.time() - age
            os.utime(path, (old, old))
        return path

    def graph(self, **node):
        folder = self.work / ".opencode" / "graph"
        folder.mkdir(parents=True, exist_ok=True)
        base = {"id": "n3", "type": "implement", "agent": "impl-openai", "goal": "Add the limiter", "done_when": "tests pass", "scope": ["src/"],
                "status": "running", "attempts": 1, "evidence": "ran tests: 3 failed", "session": SID}
        (folder / "limiter.json").write_text(json.dumps({"task": "limiter", "goal": "whole", "nodes": [{**base, **node}]}))

    def build(self, *extra):
        return run_main(["build", "--session", SID, "--dir", str(self.work), "--db", str(self.db), *extra])


class PathAndCheck(Base):
    def test_path_rule(self):
        code, out, _ = run_main(["path", "--session", SID, "--dir", str(self.work)])
        self.assertEqual((code, out.strip()), (0, str(self.work / ".opencode" / "handoff" / f"{SID}.md")))

    def test_path_with_notes_dir(self):
        code, out, _ = run_main(["path", "--session", SID, "--dir", str(self.work), "--notes-dir", ".opencode/notes"])
        self.assertEqual(out.strip(), str(self.work / ".opencode" / "notes" / f"{SID}.md"))
        code, out, _ = run_main(["path", "--session", SID, "--dir", str(self.work), "--notes-dir", "./.opencode\\deep//notes/"])
        self.assertEqual(out.strip(), str(self.work / ".opencode" / "deep" / "notes" / f"{SID}.md"))

    def test_notes_dir_outside_dot_opencode_falls_back_to_the_default_with_a_line(self):
        default = str(self.work / ".opencode" / "handoff" / f"{SID}.md")
        for bad in (".", "src", "notes", ".opencode", ".opencode/", "./", "..", ".opencode/../src", "x/../.opencode/y", "/tmp/.opencode/x",
                    str(self.work / ".opencode" / "x"), "C:\\.opencode\\x", "\\.opencode\\x"):
            with self.subTest(bad):
                code, out, err = run_main(["path", "--session", SID, "--dir", str(self.work), "--notes-dir", bad])
                self.assertEqual((code, out.strip()), (0, default))
                self.assertIn("--notes-dir", err)
                self.assertIn("default", err)

    def test_notes_dir_rule_is_the_same_for_build_and_check(self):
        (self.work / "src").mkdir()
        (self.work / "src" / f"{SID}.md").write_text("NOTES IN SRC")
        code, out, err = self.build("--notes-dir", "src")
        self.assertEqual(code, 3, "the notes in src/ are not read")
        self.assertIn("--notes-dir", err)
        self.assertEqual(run_main(["check", "--session", SID, "--dir", str(self.work), "--notes-dir", "src"])[0], 3)

    def test_bad_session_id_is_refused(self):
        code, out, err = run_main(["path", "--session", "../x", "--dir", str(self.work)])
        self.assertEqual((code, out), (1, ""))

    def test_check_missing_fresh_stale(self):
        self.assertEqual(run_main(["check", "--session", SID, "--dir", str(self.work)])[0], 3)
        self.notes()
        code, out, _ = run_main(["check", "--session", SID, "--dir", str(self.work)])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("fresh:"))
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.notes(age=4000)
        code, out, _ = run_main(["check", "--session", SID, "--dir", str(self.work)])
        self.assertEqual(code, 3)
        self.assertTrue(out.startswith("stale:"))


class Build(Base):
    def test_missing_notes_exit_3(self):
        code, out, err = self.build()
        self.assertEqual((code, out), (3, ""))
        self.assertNotIn(SECRET, err)

    def test_empty_notes_exit_3(self):
        self.notes("  \n")
        self.assertEqual(self.build()[0], 3)

    def test_stale_notes_exit_3_unless_allowed(self):
        self.notes(age=4000)
        self.assertEqual(self.build()[0], 3)
        self.assertEqual(self.build("--max-age", "5000")[0], 0)
        code, out, _ = self.build("--allow-stale")
        self.assertEqual(code, 0)
        self.assertIn("Run the check script", out)

    def test_without_graph_node(self):
        self.notes()
        code, out, _ = self.build()
        self.assertEqual(code, 0)
        self.assertIn(SECRET, out)                    # the first user message
        self.assertNotIn("SECOND-TEXT", out)          # only the first
        self.assertNotIn("assistant reply text", out)
        self.assertIn(str(self.work), out)
        self.assertNotIn("Task graph", out)
        self.assertTrue(out.rstrip().endswith(NOTES.strip()))  # the notes come last, word for word

    def test_with_graph_node(self):
        self.notes()
        self.graph()
        code, out, _ = self.build()
        self.assertEqual(code, 0)
        for text in ("Add the limiter", "tests pass", "src/", "ran tests: 3 failed", "node `n3`"):
            self.assertIn(text, out)

    def test_graph_node_of_another_session_is_not_used(self):
        self.notes()
        self.graph(session="ses_other")
        self.assertNotIn("Add the limiter", self.build()[1])

    def test_changed_files_names_only(self):
        if not shutil.which("git"):
            self.skipTest("git missing")
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", str(self.work), *args], check=True, env=env, capture_output=True)
        (self.work / "a.py").write_text("print('FILE-BODY-9911')\n")
        self.notes()
        code, out, _ = self.build()
        self.assertIn("- a.py", out)
        self.assertNotIn("FILE-BODY-9911", out)
        self.assertNotIn(".opencode/handoff", out.split("## Notes written")[0])  # the notes folder itself is not a changed file

    def test_the_rest_of_dot_opencode_is_listed_but_not_the_ignored_notes(self):
        if not shutil.which("git"):
            self.skipTest("git missing")
        subprocess.run(["git", "-C", str(self.work), "init", "-q"], check=True, capture_output=True)
        self.notes()
        (handoff.notes_folder(self.work) / ".gitignore").write_text("*\n")      # what the plugin puts there
        self.assertIn("(none)", self.build()[1].split("## Notes written")[0])     # the notes are neither listed nor shown by git
        self.graph(scope=[])
        self.assertIn("- .opencode/", self.build()[1].split("## Notes written")[0])  # but other work under .opencode/ is

    def test_not_a_git_tree_is_said(self):
        self.notes()
        self.assertIn("not a git work tree", self.build()[1])

    def test_missing_database_is_exit_4_with_no_summary(self):
        self.notes()
        code, out, err = run_main(["build", "--session", SID, "--dir", str(self.work), "--db", str(self.tmp / "none.db")])
        self.assertEqual((code, out), (4, ""))
        self.assertNotIn(SECRET, err)

    def test_unreadable_database_is_exit_4(self):
        self.notes()
        bad = self.tmp / "bad.db"
        bad.write_bytes(b"this is not a database" * 50)
        self.assertEqual(run_main(["build", "--session", SID, "--dir", str(self.work), "--db", str(bad)])[0], 4)

    def test_locked_database_is_exit_4(self):
        self.notes()
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            code, text = handoff.build(SID, self.work, db_path=self.db, db_timeout=0.1)
        finally:
            holder.close()
        self.assertEqual((code, text), (4, ""))

    def test_session_without_a_user_message_is_exit_4(self):
        self.notes()
        code, text = handoff.build("ses_nomessages", self.work, db_path=self.db)
        self.assertEqual((code, text), (3, ""))  # no notes of its own
        path = handoff.notes_path("ses_nomessages", self.work)
        path.write_text("notes")
        self.assertEqual(handoff.build("ses_nomessages", self.work, db_path=self.db), (4, ""))

    def test_default_database_follows_xdg_data_home(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "xdg")}):
            self.assertEqual(handoff.default_db(), self.tmp / "xdg" / "opencode" / "opencode.db")
            self.assertEqual(handoff.rollover_home(), self.tmp / "xdg" / "agentgraph" / "rollover")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "relative/dir", "AGENTGRAPH_ROLLOVER_HOME": ""}):
            self.assertEqual(handoff.default_db(), Path.home() / ".local" / "share" / "opencode" / "opencode.db")
        with mock.patch.dict(os.environ, {"AGENTGRAPH_ROLLOVER_HOME": str(self.tmp / "own")}):
            self.assertEqual(handoff.rollover_home(), self.tmp / "own")

    def test_build_finds_the_database_through_xdg_data_home(self):
        self.notes()
        folder = self.tmp / "xdg" / "opencode"
        folder.mkdir(parents=True)
        shutil.copy(self.db, folder / "opencode.db")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "xdg")}):
            code, out, _ = run_main(["build", "--session", SID, "--dir", str(self.work)])
        self.assertEqual(code, 0)
        self.assertIn(SECRET, out)

    def test_task_is_kept_when_the_notes_fill_the_limit(self):
        self.notes("NOTES-HEAD\n" + "n" * 30_000 + "\nNEXT-STEP-AT-THE-END\n")
        code, out, _ = self.build()
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out), handoff.LIMIT)
        self.assertIn(SECRET, out)                     # the task is never the part that is dropped
        self.assertIn("NOTES-HEAD", out)
        self.assertIn("NEXT-STEP-AT-THE-END", out)     # the last section of the notes survives the cut
        self.assertIn("notes cut here", out)

    def test_long_task_is_cut_but_the_notes_keep_room(self):
        task_db = sqlite3.connect(self.db)
        task_db.execute("UPDATE session_message SET data=? WHERE seq=7", (json.dumps({"time": {"created": 1}, "text": "T" * 20_000, "files": []}),))
        task_db.commit()
        task_db.close()
        self.notes("x" * 10_000 + "\nLAST-LINE\n")
        code, out, _ = self.build()
        self.assertEqual(code, 0)
        self.assertLessEqual(out.count("T"), handoff.TASK_CHARS)
        self.assertIn("LAST-LINE", out)
        self.assertGreater(out.count("x"), 9_000)

    def test_cut_notes_keep_head_and_last_2000(self):
        text = "H" * 500 + "m" * 10_000 + "E" * 3000
        cut = handoff.cut_notes(text, 6000)
        self.assertLessEqual(len(cut), 6000)
        self.assertTrue(cut.startswith("H" * 500))
        self.assertTrue(cut.endswith("E" * 2000))
        self.assertEqual(handoff.cut_notes("short", 4000), "short")

    def test_newer_than_rejects_notes_from_before(self):
        path = self.notes()
        now = time.time()
        self.assertEqual(self.build("--newer-than", str(now + 60))[0], 3)       # written before the cut-off
        self.assertEqual(self.build("--newer-than", str(now - 60))[0], 0)
        self.assertEqual(self.build("--newer-than", str(int((now - 60) * 1000)))[0], 0)    # milliseconds
        self.assertEqual(self.build("--newer-than", str(int((now + 60) * 1000)))[0], 3)
        old = now - 4000
        os.utime(path, (old, old))
        self.assertEqual(self.build("--newer-than", str(old - 10))[0], 0)       # no max age with --newer-than
        self.assertEqual(self.build("--newer-than", str(old - 10), "--allow-stale")[0], 0)
        self.assertEqual(self.build("--newer-than", str(old + 10), "--allow-stale")[0], 3)  # --allow-stale does not override it
        self.assertEqual(run_main(["check", "--session", SID, "--dir", str(self.work), "--newer-than", str(old + 10)])[0], 3)
        self.assertEqual(run_main(["check", "--session", SID, "--dir", str(self.work), "--newer-than", str(old - 10)])[0], 0)

    def test_git_timeout_is_said_and_the_build_goes_on(self):
        self.notes()
        with mock.patch.object(handoff.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 5)):
            code, out, _ = self.build()
        self.assertEqual(code, 0)
        self.assertIn("git timed out", out)
        self.assertNotIn("not a git work tree", out)
        self.assertIn(SECRET, out)

    def test_size_limit_trims_code_parts_first(self):
        self.notes("NOTES-START\n" + "n" * 1200 + "\nNOTES-END\n")
        self.graph(evidence="e" * 5000)
        code, out, _ = self.build("--limit", "2500")
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out), 2500)
        self.assertIn("NOTES-START", out)
        self.assertIn("NOTES-END", out)
        self.assertIn("n" * 1200, out)                # the notes are whole
        self.assertLess(out.count("e"), 4000)         # the evidence was cut

    def test_notes_over_the_limit_lose_the_middle(self):
        self.notes("HEAD-MARK\n" + "m" * 6000 + "\nTAIL-MARK\n")
        code, out, _ = self.build("--limit", "2000")
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out), 2000)
        self.assertIn("HEAD-MARK", out)
        self.assertIn("TAIL-MARK", out)
        self.assertIn("notes cut here", out)

    def test_limit_too_small_is_refused(self):
        self.notes()
        self.assertEqual(self.build("--limit", "10")[0], 1)


class Log(Base):
    def write_log(self, rows):
        path = handoff.log_path(self.tmp / "home")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")

    def test_empty(self):
        code, out, _ = run_main(["log", "--home", str(self.tmp / "home")])
        self.assertEqual((code, out.strip()), (0, "no decisions logged"))

    def test_table(self):
        self.write_log([{"t": 1_700_000_000_000, "session": SID, "event": "asked", "context": 105_432, "agent": "impl-openai"},
                        {"t": 1_700_000_060_000, "session": SID, "event": "fell_back", "context": 161_000, "agent": "impl-openai", "reason": "notes stale"}])
        code, out, _ = run_main(["log", "--home", str(self.tmp / "home")])
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 3)  # header and two rows; the bad line is skipped
        self.assertTrue(lines[0].startswith("time"))
        self.assertIn("105,432", lines[1])
        self.assertIn("fell_back", lines[2])
        self.assertIn("notes stale", lines[2])

    def test_last_limits_rows(self):
        self.write_log([{"t": 1_700_000_000_000 + n, "session": SID, "event": "asked", "context": n} for n in range(10)])
        out = run_main(["log", "--home", str(self.tmp / "home"), "--last", "3"])[1]
        self.assertEqual(len(out.splitlines()), 4)

    def test_log_lives_outside_the_project_and_filters_by_session(self):
        self.write_log([{"t": 1_700_000_000_000, "session": SID, "event": "asked"}, {"t": 1_700_000_000_001, "session": "ses_other", "event": "rolled"}])
        out = run_main(["log", "--home", str(self.tmp / "home"), "--session", "ses_other"])[1]
        self.assertEqual(len(out.splitlines()), 2)
        self.assertIn("rolled", out)
        with mock.patch.dict(os.environ, {"AGENTGRAPH_ROLLOVER_HOME": str(self.tmp / "home")}):
            self.assertEqual(len(run_main(["log"])[1].splitlines()), 3)

    def test_unprintable_text_in_a_row_is_cleaned(self):
        self.write_log([{"t": 1_700_000_000_000, "session": SID, "event": "error", "reason": "bad\nline\x1b[31m"}])
        out = run_main(["log", "--home", str(self.tmp / "home")])[1]
        self.assertEqual(len(out.splitlines()), 2)
        self.assertNotIn("\x1b", out)


class NoMessageText(Base):
    def test_only_build_prints_the_task_text(self):
        self.notes()
        self.graph()
        Log.write_log(self, [{"t": 1_700_000_000_000, "session": SID, "event": "asked", "context": 1}])
        outputs = []
        for argv in (["path", "--session", SID, "--dir", str(self.work)], ["check", "--session", SID, "--dir", str(self.work)],
                     ["log", "--home", str(self.tmp / "home")], ["build", "--session", "ses_none", "--dir", str(self.work), "--db", str(self.db)],
                     ["build", "--session", SID, "--dir", str(self.work), "--db", str(self.db), "--max-age", "0.0001"]):
            time.sleep(0.01)
            code, out, err = run_main(argv)
            outputs.append(out + err)
        for text in outputs:
            self.assertNotIn(SECRET, text)
            self.assertNotIn("SECOND-TEXT", text)
        self.assertIn(SECRET, self.build()[1])

    def test_the_command_runs_through_the_entry_point(self):
        self.notes()
        done = subprocess.run(["python3", "-m", "agentgraph", "handoff", "path", "--session", SID, "--dir", str(self.work)], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, str(handoff.notes_path(SID, self.work))), done.stderr)


class Leads(Base):
    def structure(self, teams=True):
        root = self.tmp / "structure"
        for folder, name, extra in (("00-hub", "orchestrator", ""), ("10-council", "council", "seats: councillor-\n\n"), ("30-build", "impl-openai", ""),
                                    ("40-verify", "reviewer", "")):
            (root / folder).mkdir(parents=True, exist_ok=True)
            (root / folder / f"{name}.md").write_text(extra + f"{name} does things.\n")
        if teams:
            (root / "teams").mkdir()
            (root / "teams" / "review-team.md").write_text("lead: reviewer\nmembers: impl-openai\n\nimpl-openai reports to reviewer.\n")
        return root

    def test_leads_are_the_hubs_the_council_and_the_team_leads(self):
        code, out, err = run_main(["leads", "--structure-dir", str(self.structure())])
        self.assertEqual((code, out.split()), (0, ["council", "orchestrator", "reviewer"]), err)

    def test_without_teams_the_hub_and_council_are_still_listed(self):
        code, out, _ = run_main(["leads", "--structure-dir", str(self.structure(teams=False))])
        self.assertEqual((code, out.split()), (0, ["council", "orchestrator"]))

    def test_no_structure_folder_is_exit_1_with_one_line(self):
        code, out, err = run_main(["leads", "--structure-dir", str(self.tmp / "nothing")])
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_the_checkout_structure_lists_its_hub_and_council(self):
        code, out, err = run_main(["leads"])
        self.assertEqual(code, 0, err)
        self.assertTrue({"council", "orchestrator"} <= set(out.split()))


@unittest.skipUnless(shutil.which("node"), "node missing")
class PluginScript(unittest.TestCase):
    def test_fake_event_test_passes(self):
        done = subprocess.run(["node", str(ROOT / "rollover-plugin" / "test.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout[-1500:] + done.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
