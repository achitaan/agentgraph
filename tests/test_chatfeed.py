"""agentgraph.chatfeed: one session's recent chat, capped, plain, and never a tool's input or output; the
`/api/chat` route; the snapshot's `activity`. Every database here is a scratch SQLite file with the OpenCode
2.3.2 tables (`session_v2`, `session_message`, `session_inbox`)."""
import http.client
import io
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentgraph import chatfeed, server, snapshot_json
from agentgraph.demo import demo_activity, demo_chat, demo_snapshot

ROOT = Path(__file__).resolve().parent.parent
MARKER = "MARKER-7f3a9c-never-shown"

SCHEMA = (
    "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, model TEXT, title TEXT, directory TEXT, "
    "cost REAL, tokens_input INT, tokens_output INT, time_created INT, time_updated INT, time_busy INT, time_idle INT, "
    "metadata TEXT);"
    "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL, seq INT NOT NULL, "
    "time_created INT NOT NULL, time_updated INT NOT NULL, data TEXT NOT NULL);"
    "CREATE UNIQUE INDEX session_message_seq ON session_message (session_id, seq);"
    "CREATE INDEX session_message_type ON session_message (session_id, type, seq);"
    "CREATE TABLE session_inbox (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, "
    "delivery TEXT NOT NULL, enqueued_seq INT NOT NULL, time_created INT NOT NULL);"
)
T0 = 1_800_000_000_000  # ms


class Db:
    """A scratch OpenCode database."""

    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.seq: dict[str, int] = {}

    def session(self, sid, parent=None, agent="orchestrator", busy=False, updated=0):
        self.conn.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (sid, parent, agent, json.dumps({"id": "claude-x", "providerID": "anthropic", "variant": "high"}),
                           "title " + sid, "/p", 1.0, 1, 2, T0 + updated, T0 + updated,
                           T0 + 5 if busy else None, None, json.dumps({"x": MARKER})))
        self.conn.commit()

    def message(self, sid, kind, data, *, when=None):
        seq = self.seq[sid] = self.seq.get(sid, 0) + 1
        self.conn.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                          (f"msg_{sid}_{seq}", sid, kind, seq, T0 + seq * 1000 if when is None else when,
                           T0 + seq * 1000, json.dumps(data)))
        self.conn.commit()
        return seq

    def user(self, sid, text):
        return self.message(sid, "user", {"text": text, "time": {"created": 1}, "metadata": {"displayText": MARKER}})

    def assistant(self, sid, content):
        return self.message(sid, "assistant", {"agent": "x", "content": content, "cost": 0.1,
                                               "time": {"created": 1}, "tokens": {"input": 1}})

    def close(self):
        self.conn.close()


def reasoning(text):
    return {"type": "reasoning", "text": text, "time": {"created": T0 + 1500}, "native": {"blob": MARKER * 50}}


def said(text):
    return {"type": "text", "text": text}


def tool(name, status="completed"):
    return {"type": "tool", "id": "call_1", "name": name, "executed": True,
            "time": {"created": T0 + 2500, "ran": T0 + 2600, "completed": T0 + 2700},
            "state": {"status": status, "input": {"command": f"echo {MARKER}"},
                      "content": [{"type": "text", "text": f"output {MARKER}"}], "metadata": {"m": MARKER},
                      "error": {"message": MARKER, "status": 1, "type": "x"}}}


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = Db(self.dir / "opencode.db")
        self.addCleanup(self.db.close)

    def feed(self, sid="ses_a", **kw):
        return chatfeed.read_chat(sid, db_path=self.db.path, **kw)

    def texts(self, feed):
        return [item.get("text") or item.get("name") for item in feed["items"]]


class Reading(Case):
    def test_kinds_and_order(self):
        self.db.session("ses_a", agent="impl-claude", busy=True)
        self.db.user("ses_a", "do the thing")
        self.db.assistant("ses_a", [reasoning("think first"), said("on it"), tool("shell", "running")])
        self.db.message("ses_a", "synthetic", {"text": "shell output " + MARKER, "description": MARKER,
                                              "metadata": {"source": MARKER}})
        self.db.message("ses_a", "idle", {"outcome": "x"})
        self.db.message("ses_a", "system", {"text": "system " + MARKER})
        self.db.message("ses_a", "compaction", {"text": MARKER})
        feed = self.feed()
        self.assertEqual([item["kind"] for item in feed["items"]], ["user", "reasoning", "text", "tool", "note"])
        self.assertEqual(self.texts(feed)[:4], ["do the thing", "think first", "on it", "shell"])
        self.assertEqual((feed["session"], feed["agent"], feed["busy"], feed["truncated"], feed["more"], feed["warning"]),
                         ("ses_a", "impl-claude", True, False, False, None))
        user, thought, said_, ran, note = feed["items"]
        self.assertEqual(set(ran), {"kind", "time", "name", "status"})
        self.assertEqual((ran["name"], ran["status"]), ("shell", "running"))
        self.assertEqual(set(note), {"kind", "time"})
        self.assertEqual(set(user), {"kind", "time", "text", "cut"})
        self.assertEqual(thought["time"], (T0 + 1500) / 1000)  # a part's own time
        self.assertEqual(user["time"], (T0 + 1000) / 1000)     # else the message's
        self.assertFalse(feed["items"][1]["cut"])
        json.dumps(feed, allow_nan=False)

    def test_finished_session_is_not_busy(self):
        self.db.session("ses_a")
        self.assertFalse(self.feed()["busy"])

    def test_message_count_cap_and_more(self):
        self.db.session("ses_a")
        for i in range(60):
            self.db.assistant("ses_a", [said(f"m{i}")])
        feed = self.feed(limit=10)
        self.assertEqual(self.texts(feed), [f"m{i}" for i in range(50, 60)])
        self.assertTrue(feed["more"])
        self.assertFalse(feed["truncated"])
        self.assertEqual(len(self.feed()["items"]), 40)      # the default
        self.assertEqual(len(self.feed(limit=500)["items"]), 60)
        self.assertFalse(self.feed(limit=500)["more"])
        self.assertEqual(len(self.feed(limit=0)["items"]), 40)  # not a usable limit: the default
        self.assertEqual(len(self.feed(limit="9")["items"]), 40)
        self.assertEqual(len(self.feed(limit=1)["items"]), 1)

    def test_hard_maximum_of_200_messages(self):
        self.db.session("ses_a")
        for i in range(230):
            self.db.assistant("ses_a", [tool("read")])
        feed = self.feed(limit=10_000)
        self.assertEqual(len(feed["items"]), 200)
        self.assertTrue(feed["more"])

    def test_per_text_cap(self):
        self.db.session("ses_a")
        self.db.assistant("ses_a", [said("x" * 5000), reasoning("y" * 2000), said("z" * 2001)])
        feed = self.feed()
        long, exact, over = feed["items"]
        self.assertEqual((len(long["text"]), long["cut"]), (2000, True))
        self.assertEqual((len(exact["text"]), exact["cut"]), (2000, False))
        self.assertEqual((len(over["text"]), over["cut"]), (2000, True))
        self.assertTrue(feed["truncated"])
        self.assertFalse(feed["more"])

    def test_whole_feed_cap_keeps_the_newest(self):
        self.db.session("ses_a")
        for i in range(30):
            self.db.assistant("ses_a", [said(f"{i:02d}" + "q" * 1500)])
        feed = self.feed()
        total = sum(len(item["text"]) for item in feed["items"])
        self.assertLessEqual(total, chatfeed.TOTAL_CAP + 1500)
        self.assertLess(total, 24000 + 1)
        self.assertTrue(feed["truncated"] and feed["more"])
        self.assertTrue(feed["items"][-1]["text"].startswith("29"))
        self.assertLess(len(feed["items"]), 30)
        self.assertEqual([i["text"][:2] for i in feed["items"]],
                         [f"{n:02d}" for n in range(30 - len(feed["items"]), 30)])  # contiguous, oldest dropped

    def test_unknown_session_and_content_types(self):
        self.db.session("ses_a")
        self.db.assistant("ses_a", [{"type": "file", "text": "no"}, {"type": "mystery"}, said("   "), said("ok")])
        self.assertEqual(self.texts(self.feed()), ["ok"])
        other = self.feed("ses_nothere")
        self.assertEqual(other["items"], [])
        self.assertIn("not found", other["warning"])


class NeverShown(Case):
    def setUp(self):
        super().setUp()
        self.db.session("ses_a", busy=True)
        self.db.user("ses_a", "hello")
        self.db.assistant("ses_a", [reasoning("r"), tool("shell"), tool("read", "error"), said("done")])
        self.db.message("ses_a", "synthetic", {"text": MARKER, "description": MARKER, "metadata": {"source": MARKER}})

    def test_marker_is_not_in_the_feed_or_the_digest(self):
        feed = self.feed()
        self.assertNotIn(MARKER, json.dumps(feed))
        self.assertNotIn(MARKER, chatfeed.digest("ses_a", db_path=self.db.path))
        self.assertEqual([i["kind"] for i in feed["items"]], ["user", "reasoning", "tool", "tool", "text", "note"])

    def test_the_statements_do_not_select_those_paths(self):
        statements = []
        real = chatfeed._connection

        def spy(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(chatfeed, "_connection", spy):
            self.feed()
            chatfeed.activities(["ses_a"], db_path=self.db.path)
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        self.assertGreaterEqual(len(selects), 4)
        for sql in statements:
            for banned in ("state.input", "state.content", "native", "metadata", "$.input", "error"):
                self.assertNotIn(banned, sql)
            self.assertNotIn(MARKER, sql)
        # `data` and `payload` only inside json_extract / json_each / json_array_length, never as a column.
        for sql in selects:
            rest = re.sub(r"json_(?:extract|each|array_length)\([^)]*\)", "", sql)
            rest = re.sub(r"substr\(\s*,\s*[^)]*\)", "", rest)
            outer = rest.split("FROM (SELECT time_created, data FROM")[0]  # _LAST's inner query stays inside SQLite
            self.assertNotRegex(outer, r"\b(data|payload)\b", sql)
        # the one selected column set of the items query
        self.assertTrue(any("json_each(m.data, '$.content')" in s for s in selects))

    def test_activity_never_holds_them(self):
        self.assertNotIn(MARKER, json.dumps(chatfeed.activities(["ses_a"], db_path=self.db.path)))

    def test_the_whole_json_of_a_large_message_stays_out_of_python(self):
        """A tool with 200 KB of output (and 30 KB of `native`) is read without that text being fetched."""
        self.db.assistant("ses_a", [{**tool("grep"), "state": {"status": "completed", "content": [{"type": "text",
                                                                                                   "text": MARKER * 20000}]}},
                                    reasoning("short")])
        fetched = []
        real = chatfeed._connection

        def spy(*args, **kwargs):
            conn = real(*args, **kwargs)
            original = conn.execute

            class Wrapped:
                def __init__(self, cursor):
                    self.cursor = cursor

                def fetchall(self):
                    rows = self.cursor.fetchall()
                    fetched.append(sum(len(str(value)) for row in rows for value in row))
                    return rows

                def fetchone(self):
                    row = self.cursor.fetchone()
                    fetched.append(sum(len(str(value)) for value in (row or ())))
                    return row

            conn_proxy = type("C", (), {"execute": lambda self, *a: Wrapped(original(*a)), "close": conn.close})()
            return conn_proxy

        with patch.object(chatfeed, "_connection", spy):
            feed = self.feed()
        self.assertEqual(feed["items"][-2]["name"], "grep")
        self.assertLess(max(fetched), 3 * chatfeed.TEXT_CAP * 3)
        self.assertNotIn(MARKER, json.dumps(feed))


class PlainText(Case):
    def test_escapes_and_controls_are_stripped(self):
        self.db.session("ses_a")
        raw = ("\x1b[31mred\x1b[0m \x1b]0;window title\x07ok \x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\ "
               "\x07bell\x01nul\x08back \x9b31mcsi \u202eevil\u202c a\tb\r\nsecond line \x1b(B end "
               "\x1bPdcs data\x1b\\ done")
        self.db.assistant("ses_a", [said(raw), reasoning("\x1b[2J\x1b[Hclear"), tool("sh\x1b[31mell\n")])
        feed = self.feed()
        said_, thought, ran = feed["items"]
        self.assertEqual(said_["text"], "red ok link bellnulback csi evil a\tb\nsecond line  end  done")
        self.assertEqual(thought["text"], "clear")
        self.assertEqual(ran["name"], "sh__31mell_")
        for item in feed["items"]:
            for value in (item.get("text") or "", item.get("name") or ""):
                self.assertFalse(re.search("[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e]", value), repr(value))
        self.assertEqual(chatfeed.plain("a\nb\tc"), "a\nb\tc")
        self.assertEqual(chatfeed.plain(None), "")
        self.assertEqual(chatfeed.plain("a\x00b"), "ab")
        self.assertEqual(chatfeed.plain("x\ud800y"), "x\ufffdy")

    def test_invalid_utf8_in_the_database_does_not_raise(self):
        self.db.session("ses_a")
        self.db.conn.execute("INSERT INTO session_message VALUES ('m1','ses_a','user',1,1,1,CAST(x'7b2274657874223a226162ff6364227d' AS TEXT))")
        self.db.conn.commit()
        feed = self.feed()
        self.assertIsNone(feed["warning"])
        self.assertEqual(feed["items"][0]["text"], "ab\ufffdcd")

    def test_masks_obvious_secrets(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA" + "A" * 80 + "\n-----END RSA PRIVATE KEY-----"
        secrets = {
            "bearer": "Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop.qrstuvwxyz0123456789",
            "sk": "sk-proj-abcdefghijklmnopqrstuvwx",
            "ghp": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "pat": "github_pat_11ABCDEFG0abcdefghijklmnop",
            "xox": "xoxb-1234567890-abcdefghij",
            "aws": "AKIAIOSFODNN7EXAMPLE",
            "pw": "password=hunter2hunter2",
            "json": '"api_key": "abcdefghijkl1234"',
            "env": "MY_SERVICE_TOKEN=tok_abcdefgh12345",
            "pem": pem,
        }
        text = "\n".join(f"{k}: {v}" for k, v in secrets.items()) + "\nplain: nothing secret here, token: 5"
        self.db.session("ses_a")
        self.db.assistant("ses_a", [said(text)])
        out = self.feed()["items"][0]["text"]
        for needle in ("eyJhbGci", "abcdefghijklmnopqrstuvwx", "ghp_abc", "11ABCDEFG0", "1234567890-abcdefghij", "AKIAIOSFODNN",
                       "hunter2", "abcdefghijkl1234", "tok_abcdefgh", "MIIEowIBAAKC", "END RSA PRIVATE"):
            self.assertNotIn(needle, out)
        self.assertIn("Bearer [masked]", out)
        self.assertIn("password=[masked]", out)
        self.assertIn("[masked key]", out)
        self.assertIn("MY_SERVICE_TOKEN=[masked]", out)
        self.assertIn("plain: nothing secret here, token: 5", out)
        # an unterminated key block is masked to the end
        self.assertEqual(chatfeed.mask_secrets("a -----BEGIN PRIVATE KEY-----\nabc"), "a [masked key]")

    def test_the_note_line_is_masked_and_capped(self):
        line = chatfeed.first_line("\n\n  " + "w" * 400 + "\nsecond")
        self.assertEqual(len(line), 160)
        self.assertEqual(chatfeed.first_line("\x1b[1m \nPassword=abcdefghij1\nx"), "Password=[masked]")
        self.assertIsNone(chatfeed.first_line("  \n\t\n"))
        self.assertIsNone(chatfeed.first_line(None))


class Steering(Case):
    def test_a_steered_message_shows_when_landed_and_while_pending(self):
        self.db.session("ses_a", busy=True)
        self.db.assistant("ses_a", [said("working")])
        landed = self.db.user("ses_a", "also handle the edge case")  # a steer lands as a user message
        self.db.conn.executemany(
            "INSERT INTO session_inbox VALUES (?,?,?,?,?,?,?)",
            [("i1", "ses_a", "user", json.dumps({"text": "also handle the edge case"}), "steer", landed, T0),
             ("i2", "ses_a", "user", json.dumps({"text": "then stop and report", "metadata": MARKER}), "steer", landed + 1, T0 + 9000),
             ("i3", "ses_a", "synthetic", json.dumps({"text": "shell " + MARKER, "description": MARKER}), "steer", landed + 2, T0),
             ("i4", "ses_b", "user", json.dumps({"text": "another session"}), "steer", 1, T0)])
        self.db.conn.commit()
        feed = self.feed()
        self.assertEqual([(i["kind"], i["text"], i.get("pending")) for i in feed["items"]],
                         [("text", "working", None), ("user", "also handle the edge case", None),
                          ("user", "then stop and report", True)])
        self.assertEqual(feed["items"][-1]["delivery"], "steer")
        self.assertNotIn(MARKER, json.dumps(feed))
        self.assertIn("[user (waiting)] then stop and report",
                      chatfeed.digest("ses_a", db_path=self.db.path))

    def test_a_database_without_an_inbox_still_reads(self):
        self.db.session("ses_a")
        self.db.user("ses_a", "hi")
        self.db.conn.execute("DROP TABLE session_inbox")
        self.db.conn.commit()
        feed = self.feed()
        self.assertIsNone(feed["warning"])
        self.assertEqual(self.texts(feed), ["hi"])


class Failures(Case):
    def test_missing_database(self):
        feed = chatfeed.read_chat("ses_a", db_path=self.dir / "none.db")
        self.assertEqual((feed["items"], feed["truncated"], feed["more"], feed["busy"]), ([], False, False, False))
        self.assertIn("database not found", feed["warning"])
        self.assertNotIn(str(self.dir), feed["warning"])

    def test_database_without_the_tables(self):
        bare = self.dir / "bare.db"
        sqlite3.connect(bare).close()
        feed = chatfeed.read_chat("ses_a", db_path=bare)
        self.assertEqual(feed["items"], [])
        self.assertIn("cannot read", feed["warning"])

    def test_not_a_database(self):
        junk = self.dir / "junk.db"
        junk.write_bytes(b"this is not sqlite" * 100)
        feed = chatfeed.read_chat("ses_a", db_path=junk)
        self.assertEqual(feed["items"], [])
        self.assertTrue(feed["warning"])

    def test_locked_database(self):
        self.db.session("ses_a")
        self.db.user("ses_a", "hi")
        self.db.close()
        holder = sqlite3.connect(self.db.path, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        self.addCleanup(holder.close)
        feed = chatfeed.read_chat("ses_a", db_path=self.db.path, timeout=0.05)
        self.assertEqual(feed["items"], [])
        self.assertIn("locked", feed["warning"])
        self.assertEqual(chatfeed.activities(["ses_a"], db_path=self.db.path, timeout=0.05), {})
        self.db.conn = sqlite3.connect(self.db.path)  # for the cleanup

    def test_a_bad_session_id_never_reaches_the_database(self):
        for bad in ("", "x", "ses_", "ses_a/../b", "ses_a b", "ses_a;drop", "SES_a", None, 5, "ses_" + "a" * 65, "ses_é"):
            with self.subTest(bad=bad):
                self.assertFalse(chatfeed.valid_session(bad))
                feed = chatfeed.read_chat(bad, db_path=self.dir / "none.db")
                self.assertEqual(feed["items"], [])
                self.assertEqual(feed["warning"], "chat: not a session id")
        for good in ("ses_a", "ses_e0fa781d6ffeCWBZrV5Y5F8LHG", "ses_root"):
            self.assertTrue(chatfeed.valid_session(good))

    def test_the_command_refuses_a_bad_id_with_one_line(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = chatfeed.main(["--session", "../etc"])
        self.assertEqual((code, out.getvalue()), (2, ""))
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        run = subprocess.run([str(ROOT / "bin" / "agentgraph"), "chat", "--session", "bad id"], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(len(run.stderr.strip().splitlines()), 1)
        self.assertEqual(run.stdout, "")


class Digest(Case):
    def test_plain_block_and_cap_keep_the_newest(self):
        self.db.session("ses_a", agent="impl-openai", busy=True)
        self.db.user("ses_a", "first request")
        for i in range(20):
            self.db.assistant("ses_a", [said(f"step {i:02d} " + "w" * 90), tool("read")])
        whole = chatfeed.digest("ses_a", 100_000, db_path=self.db.path)
        self.assertTrue(whole.startswith("session ses_a (impl-openai, working)\n"))
        self.assertIn("[tool] read (completed)", whole)
        small = chatfeed.digest("ses_a", 600, db_path=self.db.path)
        self.assertLessEqual(len(small), 600)
        self.assertIn("step 19", small)
        self.assertNotIn("step 05", small)
        self.assertIn("older chat left out", small)
        tiny = chatfeed.digest("ses_a", 80, db_path=self.db.path)
        self.assertLessEqual(len(tiny), 80)
        self.assertNotIn("\x1b", whole)

    def test_one_huge_line_keeps_its_end(self):
        self.db.session("ses_a")
        self.db.assistant("ses_a", [said("a" * 500 + "THE-END")])
        out = chatfeed.digest("ses_a", 200, db_path=self.db.path)
        self.assertLessEqual(len(out), 200)
        self.assertIn("THE-END", out)

    def test_warning_shows_in_the_text(self):
        out = chatfeed.digest("ses_a", db_path=self.dir / "none.db")
        self.assertIn("database not found", out)

    def test_command_text_and_json(self):
        self.db.session("ses_a", agent="explorer")
        self.db.assistant("ses_a", [said("hello there"), tool("grep")])
        base = [sys.executable, "-P", "-m", "agentgraph.chatfeed", "--session", "ses_a", "--db", str(self.db.path)]
        env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
        text = subprocess.run(base, capture_output=True, text=True, env=env, cwd=self.dir)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("[says] hello there\n[tool] grep (completed)", text.stdout)
        data = subprocess.run([*base, "--json", "--limit", "1"], capture_output=True, text=True, env=env, cwd=self.dir)
        self.assertEqual(len(data.stdout.strip().splitlines()), 1)
        self.assertEqual([i["kind"] for i in json.loads(data.stdout)["items"]], ["text", "tool"])
        both = subprocess.run([*base, "--json", "--text"], capture_output=True, text=True, env=env, cwd=self.dir)
        self.assertNotEqual(both.returncode, 0)

    def test_side_cuts_each_question_before_its_brief(self):
        from agentgraph import sidechat
        self.db.session("ses_a", agent="explorer")
        self.db.user("ses_a", "what now?" + sidechat.SEPARATOR + "\nBRIEF-MARKER the graph")
        self.db.assistant("ses_a", [reasoning("private thought"), said("this")])
        base = [sys.executable, "-P", "-m", "agentgraph.chatfeed", "--session", "ses_a", "--json", "--db", str(self.db.path)]
        env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
        plain = subprocess.run(base, capture_output=True, text=True, env=env, cwd=self.dir)
        self.assertIn("BRIEF-MARKER", plain.stdout)  # without --side the feed is the raw one
        side = subprocess.run([*base, "--side"], capture_output=True, text=True, env=env, cwd=self.dir)
        self.assertEqual(side.returncode, 0, side.stderr)
        self.assertNotIn("BRIEF-MARKER", side.stdout)
        self.assertNotIn("private thought", side.stdout)
        items = json.loads(side.stdout)["items"]
        self.assertEqual([i["kind"] for i in items], ["user", "text"])
        self.assertEqual(items[0]["text"], "what now?")
        self.assertEqual(json.loads(side.stdout), sidechat.side_feed(json.loads(plain.stdout)))


class Demo(unittest.TestCase):
    def test_the_demo_feed_is_stable_and_has_the_shapes(self):
        again = demo_chat("ses_g", 1000.0)
        self.assertEqual(again, demo_chat("ses_g", 1000.0))
        self.assertEqual(chatfeed.demo_feed("ses_g", 1000.0), again)
        self.assertEqual((again["agent"], again["busy"], again["warning"]), ("impl-claude", True, None))
        self.assertEqual([i["kind"] for i in again["items"]], ["user", "reasoning", "text", "tool", "tool", "tool"])
        self.assertEqual((again["items"][-1]["name"], again["items"][-1]["status"]), ("shell", "running"))
        self.assertEqual(again["items"], sorted(again["items"], key=lambda i: i["time"]))
        done = demo_chat("ses_a", 1000.0)
        self.assertFalse(done["busy"])
        self.assertEqual({i["kind"] for i in done["items"]}, {"user", "reasoning", "tool", "text"})
        self.assertNotIn("running", [i.get("status") for i in done["items"]])
        for session in [demo_snapshot(1000.0).root, *demo_snapshot(1000.0).sessions]:
            feed = demo_chat(session.id, 1000.0)
            self.assertEqual(feed["busy"], session.busy)
            self.assertTrue(feed["items"])
            json.dumps(feed, allow_nan=False)
        self.assertIsNone(demo_chat("ses_zzz", 1000.0))
        self.assertIsNone(chatfeed.demo_feed("ses_zzz"))

    def test_the_demo_command(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(chatfeed.main(["--demo", "--session", "ses_g", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["session"], "ses_g")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(chatfeed.main(["--demo", "--session", "ses_zzz"]), 1)

    def test_the_demo_activity_is_for_busy_sessions_only(self):
        found = demo_activity(1000.0)
        self.assertEqual(sorted(found), ["ses_g", "ses_h", "ses_root"])
        self.assertEqual(found["ses_g"], {"kind": "tool", "tool": "shell", "text": None, "time": 994.0})
        self.assertEqual((found["ses_h"]["kind"], found["ses_h"]["tool"]), ("reasoning", None))
        self.assertEqual(found["ses_h"]["text"], "Check the three widths before writing the copy.")


class Activity(Case):
    def test_last_item_of_the_newest_message(self):
        self.db.session("ses_t", busy=True)
        self.db.assistant("ses_t", [said("older")])
        self.db.assistant("ses_t", [reasoning("hm"), tool("webfetch", "running")])
        self.db.session("ses_r", busy=True)
        self.db.assistant("ses_r", [tool("read"), reasoning("first line of thought\nsecond " + "z" * 300)])
        self.db.session("ses_x", busy=True)
        self.db.assistant("ses_x", [said("Bearer abcdefghijklmnopqrstuvwxyz0123 " + "q" * 400)])
        self.db.session("ses_empty", busy=True)
        self.db.user("ses_empty", "hi")
        self.db.assistant("ses_empty", [])
        self.db.session("ses_skip", busy=True)
        self.db.assistant("ses_skip", [said("real")])
        self.db.assistant("ses_skip", [])  # a turn that has only just begun: look at the one before
        found = chatfeed.activities(["ses_t", "ses_r", "ses_x", "ses_empty", "ses_skip", "ses_none", "bad id"],
                                    db_path=self.db.path)
        self.assertEqual(sorted(found), ["ses_r", "ses_skip", "ses_t", "ses_x"])
        self.assertEqual({k: found["ses_t"][k] for k in ("kind", "tool", "text")}, {"kind": "tool", "tool": "webfetch", "text": None})
        self.assertEqual(set(found["ses_t"]), {"kind", "tool", "text", "time"})
        self.assertEqual(found["ses_t"]["time"], (T0 + 2500) / 1000)
        self.assertEqual((found["ses_r"]["kind"], found["ses_r"]["tool"], found["ses_r"]["text"]),
                         ("reasoning", None, "first line of thought"))
        self.assertEqual(len(found["ses_x"]["text"]), 160)
        self.assertNotIn("abcdefghijklmnop", found["ses_x"]["text"])
        self.assertEqual(found["ses_skip"]["text"], "real")

    def test_nothing_without_a_database(self):
        self.assertEqual(chatfeed.activities(["ses_a"], db_path=self.dir / "none.db"), {})
        self.assertEqual(chatfeed.activities([], db_path=self.db.path), {})


class SnapshotActivity(unittest.TestCase):
    def test_only_busy_agents_carry_it(self):
        snap = demo_snapshot(1000.0)
        plain_doc = snapshot_json.snapshot_document(snap, "demo")
        self.assertTrue(all("activity" not in agent for agent in plain_doc["agents"]))
        doc = snapshot_json.snapshot_document(snap, "demo", activity=demo_activity(1000.0))
        by_name = {agent["name"]: agent for agent in doc["agents"]}
        busy = {name for name, agent in by_name.items() if agent["busy"]}
        self.assertEqual({name for name, agent in by_name.items() if "activity" in agent}, busy)
        self.assertEqual(busy, {"orchestrator", "impl-claude", "designer"})
        self.assertEqual(by_name["impl-claude"]["activity"]["tool"], "shell")
        self.assertNotIn("activity", by_name["explorer"])
        for agent in by_name.values():
            self.assertEqual(set(agent) - {"activity"}, set("name group model effort pool configured skills about busy "
                                                            "sessions team parent".split()))

    def test_an_activity_for_an_idle_session_is_not_shown(self):
        snap = demo_snapshot(1000.0)
        doc = snapshot_json.snapshot_document(snap, "demo", activity={"ses_a": {"kind": "tool", "tool": "x", "text": None, "time": 1}})
        self.assertTrue(all("activity" not in agent for agent in doc["agents"]))

    def test_the_demo_command_carries_it(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(snapshot_json.main(["--demo"]), 0)
        agents = {a["name"]: a for a in json.loads(out.getvalue())["agents"]}
        self.assertEqual(agents["impl-claude"]["activity"]["kind"], "tool")
        self.assertNotIn("activity", agents["oracle"])

    def test_live_activity_reads_only_busy_sessions(self):
        snap = demo_snapshot(1000.0)
        with patch("agentgraph.chatfeed.activities", return_value={}) as read:
            snapshot_json.live_activity(snap, "x.db")
        self.assertEqual(sorted(read.call_args.args[0]), ["ses_g", "ses_h", "ses_root"])


class Route(Case):
    def setUp(self):
        super().setUp()
        d = self.db
        d.session("ses_root", agent="orchestrator", busy=True, updated=100)
        d.session("ses_work", parent="ses_root", agent="impl-openai", busy=True, updated=90)
        d.session("ses_done", parent="ses_root", agent="impl-openai", updated=80)
        d.session("ses_ghost", parent="ses_root", agent="ghost", updated=70)  # an unlisted agent, not busy
        d.session("ses_other", agent="orchestrator", busy=True, updated=10)    # another tree
        for sid in ("ses_root", "ses_work", "ses_done", "ses_ghost", "ses_other"):
            d.user(sid, f"prompt of {sid}")
        d.assistant("ses_work", [reasoning("deep thought"), tool("shell", "running")])
        d.assistant("ses_root", [said("root says hi")])
        for folder in ("graphs", "structure", "agents", "skills", "ledger", "commands"):
            (self.dir / folder).mkdir()
        (self.dir / "preset.json").write_text(json.dumps({"preset": "p", "presets": {"p": {
            "orchestrator": {"model": "anthropic/claude-x"}, "impl-openai": {"model": "openai/gpt-x"}}}}))
        self.httpd = server.make_server(
            0, graph_dir=self.dir / "graphs", cwd=self.dir, session="ses_root", web_dir=self.dir,
            structure_dir=self.dir / "structure", skills_config=self.dir / "preset.json",
            skills_map=self.dir / "SKILLS.md", skills_backups=self.dir / "backups", skills_profiles=self.dir / "profiles.md",
            agents_dir=self.dir / "agents", skills_dir=self.dir / "skills", opencode_dir=self.dir,
            ledger_root=self.dir / "ledger", commands_dir=self.dir / "commands", sessions_db=self.db.path)
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, headers=None, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=20)
        try:
            conn.request(method, path, headers=headers or {})
            reply = conn.getresponse()
            return reply.status, reply.headers, reply.read()
        finally:
            conn.close()

    def test_served_sessions_of_the_tree(self):
        for sid in ("ses_root", "ses_work", "ses_done"):
            with self.subTest(sid=sid):
                status, headers, body = self.get(f"/api/chat?session={sid}")
                self.assertEqual(status, 200, body[:200])
                self.assertEqual(headers.get_content_type(), "application/json")
                self.assertEqual(headers["Cache-Control"], "no-store")
                feed = json.loads(body)
                self.assertEqual(feed["session"], sid)
                self.assertEqual(feed["items"][0]["text"], f"prompt of {sid}")
        feed = json.loads(self.get("/api/chat?session=ses_work")[2])
        self.assertEqual([i["kind"] for i in feed["items"]], ["user", "reasoning", "tool"])
        self.assertTrue(feed["busy"])
        self.assertNotIn(MARKER, json.dumps(feed))
        self.assertEqual(len(json.loads(self.get("/api/chat?session=ses_root&limit=1")[2])["items"]), 1)
        status, _, body = self.get("/api/chat?session=ses_work", method="HEAD")
        self.assertEqual((status, body), (200, b""))

    def test_a_session_outside_the_tree_is_refused_without_detail(self):
        for sid in ("ses_other", "ses_ghost", "ses_unknown"):
            with self.subTest(sid=sid):
                status, headers, body = self.get(f"/api/chat?session={sid}")
                self.assertEqual(status, 404)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertNotIn(sid.encode(), body)
                self.assertNotIn(b"prompt of", body)

    def test_bad_requests_are_400(self):
        for query in ("", "session=", "session=bad%20id", "session=../x", "session=ses_root&session=ses_work",
                      "session=ses_root&limit=0", "session=ses_root&limit=201", "session=ses_root&limit=x",
                      "session=ses_root&limit=-1", "session=ses_root&limit=1&limit=2", "limit=5", "session=ses_%00"):
            with self.subTest(query=query):
                status, _, body = self.get("/api/chat?" + query)
                self.assertEqual(status, 400)
                self.assertNotIn(b"prompt of", body)

    def test_host_guard_and_methods(self):
        status, _, _ = self.get("/api/chat?session=ses_root", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.get("/api/chat?session=ses_root", headers={"Host": f"localhost:{self.httpd.server_port}"})
        self.assertEqual(status, 200)
        for method in ("POST", "PUT", "DELETE"):
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
            conn.request(method, "/api/chat?session=ses_root", body=b"{}", headers={"Content-Type": "application/json"})
            reply = conn.getresponse()
            reply.read()
            conn.close()
            self.assertEqual(reply.status, 405, method)
            self.assertEqual(reply.getheader("Allow"), "GET, HEAD")

    def test_demo_route(self):
        status, _, body = self.get("/api/chat?session=ses_g&demo=1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["agent"], "impl-claude")
        self.assertEqual(self.get("/api/chat?session=ses_work&demo=1")[0], 404)  # not a demo session

    def test_the_snapshot_carries_activity_for_busy_agents_only(self):
        status, _, body = self.get("/api/snapshot")
        self.assertEqual(status, 200, body[:300])
        agents = {a["name"]: a for a in json.loads(body)["agents"]}
        self.assertEqual(agents["impl-openai"]["activity"]["tool"], "shell")
        self.assertEqual(agents["impl-openai"]["activity"]["kind"], "tool")
        self.assertEqual(agents["orchestrator"]["activity"]["text"], "root says hi")
        self.assertNotIn("activity", agents.get("ghost", {}))
        self.assertNotIn(MARKER, body.decode())
        demo = {a["name"]: a for a in json.loads(self.get("/api/snapshot?demo=1")[2])["agents"]}
        self.assertEqual(demo["impl-claude"]["activity"]["tool"], "shell")
        self.assertNotIn("activity", demo["explorer"])

    def test_an_unreadable_database_gives_an_empty_feed_not_an_error(self):
        self.db.close()
        self.db.path.rename(self.dir / "moved.db")
        status, _, body = self.get("/api/chat?session=ses_root")
        self.assertEqual(status, 404)  # no session tree to belong to
        self.db.conn = sqlite3.connect(self.dir / "moved.db")


if __name__ == "__main__":
    unittest.main()
