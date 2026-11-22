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


CHAT_NOTES = ("## Objective\nNOTES-OF-THE-ORCHESTRATOR: keep the retry limit and the sign-off word straight.\n## Requirements and limits\nThe user said never to edit protected.txt "
              "and that the limit is 9.\n## Decisions\nThe limit 9 replaced 7.\n## Work state\nDone: reading. Running: nothing. Blocked: nothing.\n## Next steps\nstep one, then step two.\n"
              "## Files\nsrc/mod01.py holds the ids.\n")


class ChatBuild(Base):
    """`handoff build --chat`: the summary of a main chat. The database has session_v2 (the chat is a root session; two child sessions) and
    the message rows of a real chat: the user's typing is type `user`; a subagent's report, a shell job and the plugin's ask are `synthetic`."""

    def setUp(self):
        super().setUp()
        db = sqlite3.connect(self.db)
        db.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, agent TEXT, title TEXT, idle_outcome TEXT, time_created INTEGER)")
        db.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?)", (SID, None, "orchestrator", "the chat", None, 1))
        db.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?)", ("ses_child_done", SID, "explorer", "inventory-scan (@explorer subagent)", "succeeded", 2))
        db.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?)", ("ses_child_run", SID, "impl-openai", "long job\nwith a break (@impl-openai subagent)", None, 3))
        db.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?)", ("ses_lane_child", "ses_other", "explorer", "of another chat", None, 4))
        db.execute("DELETE FROM session_message WHERE session_id=?", (SID,))
        rows = [(1, "user", {"text": "FIRST: the sign-off word is KESTREL-4471; never edit protected.txt; the limit is 7"}),
                (2, "assistant", {"content": [{"type": "text", "text": "ASSISTANT-REPLY-TEXT"}]}),
                (3, "synthetic", {"text": "SUBAGENT-REPORT-TEXT", "metadata": {"source": "subagent", "childID": "ses_child_done", "state": "completed"}}),
                (4, "user", {"text": "SECOND: the limit is now 9"}),
                (5, "synthetic", {"text": "ROLLOVER-ASK-TEXT", "metadata": {"source": "agentgraph-rollover"}}),
                (6, "system", {"text": "SYSTEM-REMINDER-TEXT"}),
                (7, "compaction", {"status": "done"}),
                (8, "user", {"text": "THIRD: after the compaction the user speaks again"})]
        for seq, kind, data in rows:
            db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, seq, kind, json.dumps(data)))
        db.commit()
        db.close()
        self.notes(CHAT_NOTES)

    def test_the_users_messages_come_word_for_word_in_order_and_nothing_else_does(self):
        code, out, err = self.build("--chat")
        self.assertEqual(code, 0, err)
        order = [out.index(x) for x in ("FIRST: the sign-off word is KESTREL-4471; never edit protected.txt; the limit is 7", "SECOND: the limit is now 9", "THIRD: after the compaction the user speaks again")]
        self.assertEqual(order, sorted(order))
        for left_out in ("ASSISTANT-REPLY-TEXT", "SUBAGENT-REPORT-TEXT", "ROLLOVER-ASK-TEXT", "SYSTEM-REMINDER-TEXT", SECRET):
            self.assertNotIn(left_out, out)
        self.assertLess(out.index("THIRD:"), out.index("NOTES-OF-THE-ORCHESTRATOR"), "the notes come last")
        self.assertIn("Message 3 of 3", out)
        self.assertNotIn("first task", out, "not the lane's task block")

    def test_messages_from_before_a_compaction_are_carried_again(self):
        code, out, _ = self.build("--chat")
        self.assertIn("FIRST:", out)          # its row sits before the compaction row: the database keeps it, so a second rollover carries it

    def test_one_long_message_is_cut_in_the_middle(self):
        db = sqlite3.connect(self.db)
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 9, "user", json.dumps({"text": "HEAD-" + "x" * 6000 + "-TAIL"})))
        db.commit(); db.close()
        code, out, _ = self.build("--chat", "--user-message-chars", "1000")
        self.assertIn("HEAD-", out); self.assertIn("-TAIL", out); self.assertIn("[... cut ...]", out); self.assertNotIn("x" * 1100, out)

    def test_over_the_total_cap_the_first_and_the_newest_are_kept_and_one_line_says_how_many_were_left_out(self):
        db = sqlite3.connect(self.db)
        for i in range(10, 30):
            db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, i, "user", json.dumps({"text": f"MID{i}-" + "y" * 400})))
        db.commit(); db.close()
        code, out, _ = self.build("--chat", "--user-chars", "2000")
        self.assertIn("FIRST:", out); self.assertIn("MID29-", out); self.assertNotIn("MID10-", out); self.assertNotIn("SECOND:", out)
        line = [ln for ln in out.splitlines() if ln.startswith("[messages ")]
        self.assertEqual(len(line), 1); self.assertIn("are left out", line[0]); self.assertIn("of 23", line[0])

    def test_the_chats_subagent_sessions_are_listed_with_their_state_running_first_and_no_other_chats_sessions(self):
        code, out, _ = self.build("--chat")
        section = out[out.index("## Sessions this chat started"):]
        self.assertLess(section.index("ses_child_run"), section.index("ses_child_done"))
        self.assertIn("ses_child_run (impl-openai, running): long job with a break", section)
        self.assertIn("ses_child_done (explorer, completed): inventory-scan", section)
        self.assertNotIn("ses_lane_child", out)

    def test_task_graphs_list_the_nodes_that_are_not_passed(self):
        folder = self.work / ".opencode" / "graph"; folder.mkdir(parents=True)
        (folder / "alpha.json").write_text(json.dumps({"task": "alpha", "nodes": [{"id": "a1", "status": "passed"}, {"id": "a2", "status": "running"}, {"id": "a3", "status": "failed"}]}))
        (folder / "beta.json").write_text(json.dumps({"task": "beta", "nodes": [{"id": "b1", "status": "passed"}]}))
        code, out, _ = self.build("--chat")
        section = out[out.index("## Task graphs in the project"):]
        self.assertIn("alpha: 1 of 3 nodes passed; not passed: a2 (running), a3 (failed)", section)
        self.assertNotIn("b1", section); self.assertIn("1 other graphs have passed entirely", section)

    def test_it_is_chosen_by_the_session_a_root_is_a_chat_a_child_is_a_lane_and_the_flags_win(self):
        code, out, _ = self.build()
        self.assertIn("main chat", out)
        db = sqlite3.connect(self.db)
        db.execute("UPDATE session_v2 SET parent_id='ses_parent' WHERE id=?", (SID,)); db.commit(); db.close()
        code, out, _ = self.build()
        self.assertIn("This session was rolled over", out); self.assertIn("## The task as the lane was given it", out)
        code, out, _ = self.build("--chat")
        self.assertIn("main chat", out)
        db = sqlite3.connect(self.db)
        db.execute("UPDATE session_v2 SET parent_id=NULL WHERE id=?", (SID,)); db.commit(); db.close()
        code, out, _ = self.build("--lane")
        self.assertIn("This session was rolled over", out)

    def test_a_user_message_sent_between_the_ask_and_the_compaction_is_in_the_summary(self):
        db = sqlite3.connect(self.db)
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 10, "synthetic", json.dumps({"text": "ASK-2", "metadata": {"source": "agentgraph-rollover"}})))
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 11, "user", json.dumps({"text": "LATE-USER-MESSAGE typed after the ask"})))
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 12, "synthetic", json.dumps({"text": "REPORT-2", "metadata": {"source": "subagent", "childID": "ses_child_run", "state": "completed"}})))
        db.commit(); db.close()
        code, out, _ = self.build("--chat")
        self.assertIn("LATE-USER-MESSAGE typed after the ask", out)
        self.assertNotIn("REPORT-2", out); self.assertNotIn("ASK-2", out)
        self.assertIn("ses_child_run (impl-openai, completed)", out, "a report that arrived makes the child finished")

    # ---- the fix round (x1 review): state at the roll, late reports, late messages, floor, caps, real layout ----

    def put(self, rows):
        """Replace the chat's rows: (seq, type, data)."""
        db = sqlite3.connect(self.db)
        db.execute("DELETE FROM session_message WHERE session_id=?", (SID,))
        for seq, kind, data in rows:
            db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, seq, kind, json.dumps(data)))
        db.commit(); db.close()

    ASK = {"text": "ASK", "metadata": {"source": "agentgraph-rollover"}}

    def test_the_header_says_what_to_do_at_a_finished_answer_and_does_not_stop_a_chat_that_was_rolled_mid_turn(self):
        user = lambda s, x: (s, "user", {"text": x, "time": {"created": 1}})
        asst = lambda s, f: (s, "assistant", {"finish": f, "content": []})
        self.put([user(1, "do the first thing"), asst(2, "tool-calls"), asst(3, "stop"), (4, "idle", {}), (5, "synthetic", self.ASK), asst(6, "tool-calls")])
        code, out, _ = self.build("--chat")
        self.assertIn("already answered", out); self.assertIn("start no work", out); self.assertNotIn("Carry on from the next steps", out)
        self.put([user(1, "do the first thing"), asst(2, "tool-calls"), (3, "synthetic", self.ASK), asst(4, "tool-calls")])
        code, out, _ = self.build("--chat")
        self.assertIn("middle of the work", out); self.assertIn("Carry on from the next steps in the notes", out); self.assertNotIn("start no work", out)
        self.put([user(1, "do the first thing"), asst(2, "stop"), (3, "synthetic", self.ASK), asst(4, "tool-calls"), user(5, "LATE: a new request")])
        code, out, _ = self.build("--chat")
        self.assertIn("arrived after the notes were written", out); self.assertNotIn("start no work", out)
        self.put([user(1, "do the first thing"), asst(2, "stop")])        # no ask in this cycle (OpenCode's own compaction)
        code, out, _ = self.build("--chat")
        self.assertIn("State at the roll is not known", out); self.assertIn("if its work is not finished, carry on", out)
        self.put([user(1, "do the first thing"), asst(2, "stop"), (3, "synthetic", self.ASK), (4, "compaction", {"summary": "S"}), user(5, "second request"), asst(6, "tool-calls")])
        code, out, _ = self.build("--chat")      # an ask from before the last compaction does not describe this roll
        self.assertIn("State at the roll is not known", out)

    def test_a_user_message_that_is_newer_than_the_notes_is_always_quoted_and_marked(self):
        now = time.time() * 1000
        rows = [(1, "user", {"text": "FIRST " + "a" * 3000, "time": {"created": now - 9000}})]
        for i in range(2, 12):
            rows.append((i, "user", {"text": f"OLDER{i} " + "b" * 3000, "time": {"created": now - 8000 + i}}))
        rows.append((20, "user", {"text": "NEWER-THAN-THE-NOTES: use 12", "time": {"created": now + 5000}}))
        self.put(rows)
        code, out, _ = self.build("--chat", "--user-chars", "5000", "--user-message-chars", "3500")
        self.assertEqual(code, 0)
        self.assertIn("NEWER-THAN-THE-NOTES: use 12", out); self.assertIn("(arrived after the notes were written: the notes do not mention it)", out)
        self.assertIn("FIRST", out); self.assertIn("are left out", out)
        self.assertEqual(out.count("arrived after the notes were written: the notes"), 1, "only the newer message is marked")

    def test_subagent_reports_that_arrived_after_the_notes_are_quoted_word_for_word_and_older_ones_are_not(self):
        now = time.time() * 1000
        report = lambda s, ms, child, text, state="completed": (s, "synthetic", {"text": text, "time": {"created": ms}, "metadata": {"source": "subagent", "childID": child, "agent": "explorer", "state": state}})
        self.put([(1, "user", {"text": "FIRST", "time": {"created": now - 9000}}), report(2, now - 5000, "ses_child_done", "OLD-REPORT-ALREADY-IN-THE-NOTES"),
                  report(3, now + 4000, "ses_child_run", "LATE-REPORT-ONE found the id xyz-77"), report(4, now + 5000, "ses_child_done", "LATE-REPORT-TWO failed", "error")])
        code, out, _ = self.build("--chat")
        self.assertEqual(code, 0)
        self.assertIn("LATE-REPORT-ONE found the id xyz-77", out); self.assertIn("LATE-REPORT-TWO failed", out); self.assertNotIn("OLD-REPORT-ALREADY-IN-THE-NOTES", out)
        self.assertIn("## Subagent reports that arrived after the notes were written", out)
        self.assertLess(out.index("LATE-REPORT-ONE"), out.index("NOTES-OF-THE-ORCHESTRATOR"), "the notes stay last")
        self.assertIn("ses_child_run (impl-openai, completed)", out)
        code, out, _ = self.build("--chat", "--report-chars", "30")        # they do not fit: exit 3 (a normal model summary sees them), never a summary without them
        self.assertEqual((code, out), (3, "")); 

    def test_the_newest_report_of_a_resumed_child_decides_its_state(self):
        db = sqlite3.connect(self.db)   # inserted in the wrong order on purpose: the newest seq must win, not the last row
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 30, "synthetic", json.dumps({"text": "r", "metadata": {"source": "subagent", "childID": "ses_child_done", "state": "error"}})))
        db.execute("INSERT INTO session_message VALUES (?,?,?,?)", (SID, 20, "synthetic", json.dumps({"text": "r", "metadata": {"source": "subagent", "childID": "ses_child_done", "state": "completed"}})))
        db.commit(); db.close()
        code, out, _ = self.build("--chat")
        self.assertIn("ses_child_done (explorer, error)", out)

    def test_notes_that_are_only_a_line_or_have_no_handoff_content_are_not_a_summary(self):
        for text in ("Notes saved.", "x" * 400, "This is a long enough line " * 20):
            self.notes(text)
            code, out, err = self.build("--chat")
            self.assertEqual((code, out), (3, ""), text[:20]); self.assertIn("too thin", err)
        self.notes(CHAT_NOTES)
        self.assertEqual(self.build("--chat")[0], 0)

    def test_big_user_caps_cannot_push_the_summary_over_the_limit(self):
        self.put([(1, "user", {"text": "FIRST " + "a" * 40000}), (2, "user", {"text": "SECOND " + "b" * 40000}), (3, "user", {"text": "THIRD " + "c" * 40000})])
        code, out, _ = self.build("--chat", "--user-chars", "500000", "--user-message-chars", "100000", "--limit", "8000")
        self.assertEqual(code, 0); self.assertLessEqual(len(out), 8000); self.assertIn("THIRD", out); self.assertIn("NOTES-OF-THE-ORCHESTRATOR", out)

    def test_the_summary_is_small_and_never_over_the_limit(self):
        code, out, _ = self.build("--chat")
        self.assertLess(len(out), 3000)
        self.notes(CHAT_NOTES + "n" * 60000)
        code, out, _ = self.build("--chat", "--limit", "5000")
        self.assertEqual(code, 0); self.assertLessEqual(len(out), 5000); self.assertIn("FIRST:", out)

    def test_exit_codes_stale_notes_3_and_no_user_messages_5(self):
        code, out, err = self.build("--chat", "--newer-than", str(time.time() + 100))
        self.assertEqual((code, out), (3, ""))
        db = sqlite3.connect(self.db)
        db.execute("DELETE FROM session_message WHERE session_id=? AND type='user'", (SID,)); db.commit(); db.close()
        code, out, err = self.build("--chat")
        self.assertEqual((code, out), (5, ""), "a chat without readable user messages is a one-off failure (5), never the lane's sticky 4")

    def test_a_database_that_cannot_be_read_is_exit_5_for_every_part_of_the_chat_summary(self):
        db = sqlite3.connect(self.db); db.execute("DROP TABLE session_v2"); db.commit(); db.close()
        code, out, err = self.build("--chat")                 # the child list cannot be read: the summary is not made without it
        self.assertEqual((code, out), (5, "")); self.assertIn("could not be read", err)
        code, out, err = run_main(["build", "--session", SID, "--dir", str(self.work), "--db", str(self.tmp / "nothing.db"), "--chat"])
        self.assertEqual((code, out), (5, ""))

    def test_a_chat_summary_is_not_chosen_when_the_database_has_no_session_table(self):
        db = sqlite3.connect(self.db); db.execute("DROP TABLE session_v2"); db.commit(); db.close()
        code, out, _ = self.build()
        self.assertIn("This session was rolled over", out)


class RealLayout(unittest.TestCase):
    """The layout OpenCode 2.3 really stores (columns and row shapes taken from a scratch run and from real chats: types, flags and sizes), with two
    compactions: the summary of a compaction is a `compaction` row (text in data.summary), the plugin's ask and a subagent's report are `synthetic`
    rows, "Loaded AGENTS.md" is another `synthetic` row, the typing is `user`. The second summary must carry the typing once and nothing else."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "work"; self.work.mkdir()
        self.db = self.tmp / "opencode.db"
        db = sqlite3.connect(self.db)
        db.execute('CREATE TABLE "session_message" (`id` text PRIMARY KEY, `session_id` text NOT NULL, `type` text NOT NULL, `seq` integer NOT NULL, `time_created` integer NOT NULL, `time_updated` integer NOT NULL, `data` text NOT NULL)')
        db.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, agent TEXT, model TEXT, directory TEXT, title TEXT, cost REAL, idle_outcome TEXT, time_created INTEGER)")
        db.execute("INSERT INTO session_v2 (id, parent_id, agent, title, time_created) VALUES (?,?,?,?,?)", (SID, None, "orchestrator", "chat", 1))
        db.execute("INSERT INTO session_v2 (id, parent_id, agent, title, idle_outcome, time_created) VALUES (?,?,?,?,?,?)", ("ses_child", SID, "explorer", "inventory-scan (@explorer subagent)", "succeeded", 2))
        self.n = 0
        self.db_conn = db

    def row(self, kind, data):
        self.n += 1
        self.db_conn.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)", (f"msg_{self.n:04d}", SID, kind, self.n, 1000 + self.n, 1000 + self.n, json.dumps(data)))
        return self.n

    def layout(self, now):
        ask = {"description": "rollover: write handoff notes", "text": "ASK-TEXT", "time": {"created": now}, "metadata": {"source": "agentgraph-rollover"}}
        self.row("agent-switched", {"agent": "orchestrator"})
        self.row("synthetic", {"description": "Loaded .config/opencode/AGENTS.md", "text": "INSTRUCTIONS-TEXT " * 50, "time": {"created": now}, "metadata": {"path": "AGENTS.md"}})
        self.row("user", {"text": "REAL-TYPING-ONE the word is KESTREL", "files": [], "time": {"created": now}})
        self.row("assistant", {"finish": "tool-calls", "content": [{"type": "tool"}]})
        self.row("synthetic", ask)
        self.row("assistant", {"finish": "tool-calls", "content": [{"type": "tool"}]})
        self.row("compaction", {"status": "done", "reason": "manual", "model": {"id": "m"}, "summary": "# Handoff summary (main chat)\n\n### Message 1 of 1\n\nREAL-TYPING-ONE", "time": {"created": now}, "metadata": {"keepID": "msg_x"}})
        self.row("assistant", {"finish": "tool-calls", "content": [{"type": "tool"}]})
        self.row("user", {"text": "REAL-TYPING-TWO a new request", "files": [], "agents": [], "metadata": {"agent": "orchestrator", "attachments": [], "comments": [], "displayText": "x", "model": {}}, "time": {"created": now}})
        self.row("synthetic", {"description": "inventory-scan", "text": "<report>REPORT-TEXT</report>", "time": {"created": now - 10_000_000}, "metadata": {"source": "subagent", "childID": "ses_child", "agent": "explorer", "state": "completed"}})
        self.row("assistant", {"finish": "stop", "content": [{"type": "text"}]})
        self.row("synthetic", ask)
        self.row("assistant", {"finish": "tool-calls", "content": [{"type": "tool"}]})
        # the compaction being built: OpenCode writes its row before the hook is asked for the summary (seen in a real run), with no summary text yet
        self.row("compaction", {"status": "running", "reason": "manual", "model": {"id": "m"}, "time": {"created": now}})
        self.db_conn.commit()

    def build(self, *extra):
        return run_main(["build", "--session", SID, "--dir", str(self.work), "--db", str(self.db), "--chat", *extra])

    def test_the_second_summary_carries_the_typing_once_and_no_earlier_summary_or_instruction_text(self):
        self.layout(time.time() * 1000 - 1000)
        path = handoff.notes_path(SID, self.work); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(CHAT_NOTES, encoding="utf-8")
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("# Handoff summary"), 1, "no earlier summary inside this one")
        blocks = out.split("### Message ")[1:]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            self.assertNotIn("# Handoff summary", block.split("\n## ")[0])
        self.assertIn("REAL-TYPING-ONE the word is KESTREL", out); self.assertIn("REAL-TYPING-TWO a new request", out)
        for gone in ("INSTRUCTIONS-TEXT", "ASK-TEXT", "REPORT-TEXT"):
            self.assertNotIn(gone, out)
        self.assertIn("ses_child (explorer, completed)", out)
        self.assertIn("already answered", out, "the first ask of this cycle came after a stop step")

    def test_a_user_row_that_holds_a_summary_is_not_quoted_as_typing(self):
        self.layout(time.time() * 1000 - 1000)
        self.row("user", {"text": "# Handoff summary (main chat)\n\nstored as a user row by some other version", "time": {"created": 1}}); self.db_conn.commit()
        path = handoff.notes_path(SID, self.work); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(CHAT_NOTES, encoding="utf-8")
        code, out, _ = self.build()
        self.assertNotIn("stored as a user row", out); self.assertEqual(out.count("# Handoff summary"), 1)


@unittest.skipUnless(shutil.which("node"), "node missing")
class PluginScript(unittest.TestCase):
    def test_fake_event_test_passes(self):
        done = subprocess.run(["node", str(ROOT / "rollover-plugin" / "test.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout[-1500:] + done.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
