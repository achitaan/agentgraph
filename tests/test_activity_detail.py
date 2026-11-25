"""The main chat's live detail: `activity.zone`, `target`, `status` and the `recent` trail (agentgraph.chatfeed
`zone_of`, `target_of`, `hub_activity`), and who gets them (the hub only; `agentgraph snapshot` without
`--activity-detail` leaves `target` and `recent` out). Every database is a scratch SQLite file and every value
here is made up."""
import io
import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentgraph import chatfeed, snapshot_json
from agentgraph.demo import demo_activity, demo_snapshot
from agentgraph.sources import build_snapshot

from tests.launch import LAUNCHER
from tests.test_chatfeed import MARKER, T0, Case, reasoning as reasoning_part, said as said_part

ROOT = Path(__file__).resolve().parent.parent


def call(name, inp, status="completed", at=0, ran=100, took=2100):
    """A stored tool part with a made-up input (times in ms, like OpenCode's)."""
    return {"type": "tool", "id": "call_x", "name": name, "executed": True,
            "time": {"created": T0 + at, "ran": T0 + at + ran, "completed": T0 + at + ran + took},
            "state": {"status": status, "input": inp, "content": [{"type": "text", "text": f"out {MARKER}"}],
                      "metadata": {"m": MARKER}}}


class Zones(unittest.TestCase):
    def test_the_table(self):
        cases = {
            "read": "files", "Write": "files", "edit": "files", "glob": "files", "patch": "files",
            "apply_patch": "files", "shell": "terminal", "BASH": "terminal",
            "grep": "search", "websearch": "search", "codesearch": "search",
            "webfetch": "web", "browser_navigate": "web", "playwright_click": "web",
            "question": "thinking", "skill": "thinking", "plan_enter": "thinking",
            "subagent": "spawn", "task": "spawn",
            "todowrite": "tasks", "todo_read": "tasks",
            "task_message": "messaging", "task_reply": "messaging",
            "execute": "tools", "someserver_dothing": "tools", "": "tools", None: "tools",
        }
        for name, zone in cases.items():
            with self.subTest(name=name):
                self.assertEqual(chatfeed.zone_of(name), zone)
                self.assertIn(zone, chatfeed.ZONES)

    def test_execute_goes_by_the_first_namespace_it_calls(self):
        self.assertEqual(chatfeed.zone_of("execute", "const r = await tools.browser.open({url: 'x'});"), "web")
        self.assertEqual(chatfeed.zone_of("execute", "await tools.context7['query-docs']({q: 1})"), "search")
        self.assertEqual(chatfeed.zone_of("execute", "await tools.gh_grep.search({q: 1})"), "search")
        self.assertEqual(chatfeed.zone_of("execute", "await tools.opencode.session_list({})"), "tools")
        self.assertEqual(chatfeed.zone_of("execute", "await tools.other.x(); await tools.browser.y()"), "tools")
        self.assertEqual(chatfeed.zone_of("execute", "console.log(1)"), "tools")
        self.assertEqual(chatfeed.zone_of("execute", None), "tools")


class Targets(unittest.TestCase):
    def test_each_rule(self):
        home = str(Path.home())
        self.assertEqual(chatfeed.target_of("webfetch", "https://example.com/docs/intro"), "example.com/docs/intro")
        self.assertEqual(chatfeed.target_of("webfetch", "https://example.com/"), "example.com")
        self.assertEqual(chatfeed.target_of("webfetch", "example.org:8080/a/b"), "example.org:8080/a/b")
        self.assertEqual(chatfeed.target_of("shell", "\n\n  npm test -- limiter\necho second"), "npm test -- limiter")
        self.assertEqual(chatfeed.target_of("bash", "ls -la"), "ls -la")
        for tool in ("read", "write", "edit"):
            self.assertEqual(chatfeed.target_of(tool, f"{home}/work/app.py"), "~/work/app.py")
        self.assertEqual(chatfeed.target_of("read", "/etc/hosts"), "/etc/hosts")
        self.assertEqual(chatfeed.target_of("grep", "Retry-After", None), "Retry-After")
        self.assertEqual(chatfeed.target_of("glob", "src/**/*.ts"), "src/**/*.ts")
        self.assertEqual(chatfeed.target_of("websearch", "sqlite json_each"), "sqlite json_each")
        self.assertEqual(chatfeed.target_of("subagent", "explorer", "map the middleware"), "explorer: map the middleware")
        self.assertEqual(chatfeed.target_of("subagent", "explorer", None), "explorer")
        self.assertEqual(chatfeed.target_of("skill", "code-assurance"), "code-assurance")
        self.assertEqual(chatfeed.target_of("execute", "await tools.context7['query-docs']({})"), "context7.query-docs")
        self.assertEqual(chatfeed.target_of("execute", "await tools.browser.open({})"), "browser.open")
        self.assertIsNone(chatfeed.target_of("execute", "1 + 1"))
        self.assertIsNone(chatfeed.target_of("question", "[]"))
        self.assertIsNone(chatfeed.target_of("unknown_tool", "x"))
        self.assertIsNone(chatfeed.target_of("shell", None))
        self.assertIsNone(chatfeed.target_of("shell", "  \n "))
        self.assertIsNone(chatfeed.target_of("webfetch", ""))

    def test_url_keeps_no_query_fragment_or_userinfo(self):
        got = chatfeed.target_of("webfetch", "https://alice:pw1234@Example.com:8443/a/b?token=abc123def456&x=1#frag")
        self.assertEqual(got, "example.com:8443/a/b")
        for bad in ("alice", "pw1234", "token", "frag", "?", "#", "@", "https"):
            self.assertNotIn(bad, got)
        self.assertIsNone(chatfeed.target_of("webfetch", "http://[bad"))

    def test_secrets_are_masked(self):
        cases = ("curl -H 'Authorization: Bearer abcdefghijklmnop' https://example.com",
                 "API_KEY=hunter2 npm test", "export DB_PASSWORD='p w' && run", "GITHUB_TOKEN=abc ./go",
                 "curl --token zzz1 https://example.com", "mytool --password=letmein", "git clone https://bob:s3cret@example.com/r.git",
                 "echo sk-abcdefghijklmnopqrstuvwxyz")
        for text in cases:
            with self.subTest(text=text):
                got = chatfeed.target_of("shell", text)
                for secret in ("hunter2", "p w", "abcdefghijklmnop", "zzz1", "letmein", "s3cret", "GITHUB_TOKEN=abc", "sk-abc"):
                    self.assertNotIn(secret, got)
                self.assertIn("[masked]", got)
        self.assertEqual(chatfeed.target_of("shell", "ls -la src"), "ls -la src")
        home = str(Path.home())
        more = (  # (command, the command's name that stays, the secret that must go)
            ("curl -sSu alice:hunter2 https://example.com", "curl", "hunter2"),
            ("curl --proxy-user alice:hunter2 x", "curl", "hunter2"),
            ("curl -U alice:hunter2 x", "curl", "hunter2"),
            ("mysql -p'hunter2'", "mysql", "hunter2"),
            ('mysql -p"hunter 2"', "mysql", "hunter"),
            ("sudo mysql -phunter2", "mysql", "hunter2"),
            ("docker exec db mysql -phunter2", "mysql", "hunter2"),
            ("cd app && mysql -phunter2", "mysql", "hunter2"),
            ("MYSQL_HOST=x mysql -phunter2", "mysql", "hunter2"),
            ("MYSQL_PWD=hunter2 mysql", "mysql", "hunter2"),
            ("tool --auth-token abc123", "tool", "abc123"),
            ("tool --api-token x1", "tool", "x1"),
            ("tool --access-token x2", "tool", "x2"),
            ("tool --github-token x3", "tool", "x3"),
            ('curl -b "session=abc123" x', "curl", "abc123"),
            ('curl --cookie "session=abc123" x', "curl", "abc123"),
            ("curl example.com/reset?code=SECRET1", "curl", "SECRET1"),
            ("wget 'localhost:8080/x?sig=abc'", "wget", "sig=abc"),
            ("curl example.com/a#token=SECRET2", "curl", "SECRET2"),
            ('ssh db1 "mysql -phunter2"', "mysql", "hunter2"),  # the client inside quotes, $( or backticks
            ("bash -c 'mysql -phunter2'", "mysql", "hunter2"),
            ("x=$(mysql -phunter2 -e q)", "mysql", "hunter2"),
            ("echo `mysql -phunter2`", "mysql", "hunter2"),
            ('sh -c "curl -b sid=abc123 x"', "curl", "abc123"),
            ('curl -sSb "sid=abc123" x', "curl", "abc123"),
            ("curl 10.0.0.5:8080/cb?code=SECRET1", "curl", "SECRET1"),
            ("curl api.example.com?sig=abc", "curl", "sig=abc"),
            ('curl -u "admin:hunter2 and', "curl", "hunter2"),  # a quote the cut left open
            ('A_KEY="' + "x" * 400 + '" B_TOKEN="hunter 2 and more', "A_KEY", "hunter"),  # the cut leaves a quote open
        )
        for text, kept, secret in more:
            with self.subTest(text=text):
                got = chatfeed.target_of("shell", text)
                self.assertNotIn(secret, got)
                self.assertIn(kept, got)
        for text in ("git checkout -b feature", "PWD=/tmp ls", "mkdir -p build && ls -p", "cp -p a b"):
            with self.subTest(text=text):
                self.assertEqual(chatfeed.target_of("shell", text), text)
        for text, want in ((f"PATH={home}:/bin", "PATH=~:/bin"), (f"open file://{home}/x", "open file://~/x"),
                           (f"echo [{home}] {{{home}}} `{home}`", "echo [~] {~} `~`")):
            with self.subTest(text=text):
                self.assertEqual(chatfeed.target_of("shell", text), want)

    def test_credentials_in_flags_headers_and_urls_are_masked(self):
        cases = (
            ("curl -u alice:hunter2 https://example.com", "curl", "hunter2"),
            ("curl --user alice:hunter2 https://example.com", "curl", "hunter2"),
            ("curl --user=alice:hunter2 https://example.com", "curl", "hunter2"),
            ('mytool --password "hunter22" --verbose', "mytool", "hunter22"),
            ("mytool --token 'abc'", "mytool", "abc"),
            ('mytool --password "two words=x" go', "mytool", "words"),
            ('curl -H "Cookie: session=abcdef123456" https://example.com', "curl", "abcdef123456"),
            ('curl -H "Set-Cookie: sid=zyx987654; Path=/" https://example.com', "curl", "zyx987654"),
            ("curl -H 'Cookie: a=b; c=d' https://example.com", "curl", "c=d"),
            ("mysql -phunter2 -h localhost", "mysql", "hunter2"),
            ("mysqldump -phunter2 app", "mysqldump", "hunter2"),
            ("sshpass -p hunter2 ssh host", "sshpass", "hunter2"),
            ("sshpass -phunter2 ssh host", "sshpass", "hunter2"),
            ("curl https://u:p@ss@example.com/x", "example.com/x", "ss@"),
            ("git clone https://u:p@ss@example.com/x.git", "example.com/x.git", "p@ss"),
        )
        for text, kept, secret in cases:
            with self.subTest(text=text):
                got = chatfeed.target_of("shell", text)
                self.assertNotIn(secret, got)
                self.assertIn(kept, got)
                self.assertIn("[masked]", got)
        for text in ("mkdir -p build", "ls -p", "cp -p a b", "curl -u alice https://example.com", "mysql -p app"):
            with self.subTest(text=text):
                self.assertEqual(chatfeed.target_of("shell", text), text)

    def test_url_query_and_fragment_are_cut_in_a_command(self):
        got = chatfeed.target_of("shell", 'curl "https://example.com/reset?code=ABCDEF123&sig=XYZ"')
        self.assertEqual(got, 'curl "https://example.com/reset"')
        for bad in ("code=", "sig=", "ABCDEF123", "XYZ", "?"):
            self.assertNotIn(bad, got)
        self.assertEqual(chatfeed.target_of("shell", "open http://example.com/a#frag=1 now"), "open http://example.com/a now")

    def test_the_home_folder_is_only_rewritten_as_a_whole_path(self):
        home = str(Path.home())
        self.assertEqual(chatfeed.target_of("shell", f"cd {home} && ls"), "cd ~ && ls")
        self.assertEqual(chatfeed.target_of("shell", f"ls {home}"), "ls ~")
        self.assertEqual(chatfeed.target_of("shell", f'ls "{home}"; ls {home}/x'), 'ls "~"; ls ~/x')
        self.assertEqual(chatfeed.target_of("read", home), "~")
        self.assertEqual(chatfeed.target_of("shell", f"ls {home}x"), f"ls {home}x")
        self.assertEqual(chatfeed.target_of("shell", f"ls {home}-old/y"), f"ls {home}-old/y")

    def test_a_long_line_is_cheap_and_bidi_marks_are_dropped(self):
        started = time.perf_counter()
        got = chatfeed.target_of("shell", "echo " + "key-" * 500)
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertEqual(len(got), chatfeed.TARGET_CAP)
        started = time.perf_counter()
        chatfeed.target_of("webfetch", "example.com/" + "key-" * 500)
        self.assertLess(time.perf_counter() - started, 0.5)
        got = chatfeed.target_of("shell", "echo \u202egnp.exe \u2066x\u2069 end")
        self.assertEqual(got, "echo gnp.exe x end")
        for mark in ("\u202e", "\u2066", "\u2069"):
            self.assertNotIn(mark, got)

    def test_length_cap_and_plain_text(self):
        got = chatfeed.target_of("shell", "echo " + "x" * 400)
        self.assertEqual(len(got), chatfeed.TARGET_CAP)
        self.assertTrue(got.endswith("\u2026"))
        got = chatfeed.target_of("shell", "ls \x1b[31mred\x1b[0m\x07 dir")
        self.assertEqual(got, "ls red dir")
        self.assertEqual(len(chatfeed.target_of("shell", "y" * chatfeed.TARGET_CAP)), chatfeed.TARGET_CAP)


class Hub(Case):
    def setUp(self):
        super().setUp()
        self.db.session("ses_root", busy=True)
        self.db.user("ses_root", "hello")

    def base(self, sid="ses_root"):
        return chatfeed.activities([sid], db_path=self.db.path)[sid]

    def hub(self, **kw):
        return chatfeed.hub_activity("ses_root", self.base(), db_path=self.db.path, **kw)

    def test_current_tool_gets_zone_target_status_and_the_trail_newest_first(self):
        home = str(Path.home())
        self.db.assistant("ses_root", [call("webfetch", {"url": "https://example.com/docs/intro?q=1"}, at=1000),
                                       call("shell", {"command": "npm test", "workdir": "/x"}, at=2000, took=8400)])
        self.db.assistant("ses_root", [call("read", {"path": f"{home}/p/a.py"}, at=3000, took=200),
                                       call("write", {"path": "/p/b.py", "content": MARKER}, at=4000),
                                       call("grep", {"pattern": "needle", "path": "/p"}, "error", at=5000),
                                       call("subagent", {"agent": "explorer", "description": "look", "prompt": MARKER}, at=6000),
                                       call("websearch", {"query": "made up"}, "running", at=7000, took=0)])
        got = self.hub()
        self.assertEqual((got["kind"], got["tool"], got["zone"], got["target"], got["status"]),
                         ("tool", "websearch", "search", "made up", "running"))
        self.assertEqual([r["tool"] for r in got["recent"]], ["subagent", "grep", "write", "read", "shell"])
        self.assertEqual(len(got["recent"]), chatfeed.TRAIL)
        self.assertEqual([r["target"] for r in got["recent"]],
                         ["explorer: look", "needle", "/p/b.py", "~/p/a.py", "npm test"])
        self.assertEqual([r["status"] for r in got["recent"]], ["completed", "error", "completed", "completed", "completed"])
        self.assertEqual([r["zone"] for r in got["recent"]], ["spawn", "search", "files", "files", "terminal"])
        self.assertEqual(got["recent"][3]["seconds"], 0.2)
        self.assertEqual(got["recent"][4]["seconds"], 8.4)
        self.assertEqual(got["recent"][4]["time"], (T0 + 2000) / 1000)
        self.assertEqual(set(got["recent"][0]), {"zone", "tool", "target", "status", "time", "seconds"})
        self.assertEqual({k: got[k] for k in ("kind", "tool", "text", "time")},
                         {k: self.base()[k] for k in ("kind", "tool", "text", "time")})
        self.assertNotIn(MARKER, json.dumps(got))

    def test_a_short_trail_and_no_trail(self):
        self.db.assistant("ses_root", [call("read", {"path": "/p/a.py"}, "running", at=1000)])
        got = self.hub()
        self.assertEqual((got["tool"], got["zone"], got["target"], got["recent"]), ("read", "files", "/p/a.py", []))
        self.db.assistant("ses_root", [call("glob", {"pattern": "*.py"}, at=2000), call("edit", {"path": "/p/c.py"}, "running", at=3000)])
        self.assertEqual([r["tool"] for r in self.hub()["recent"]], ["glob", "read"])

    def test_streaming_is_running_and_seconds_can_be_null(self):
        part = call("shell", {"command": "ls"}, "streaming")
        del part["time"]["ran"], part["time"]["completed"]
        self.db.assistant("ses_root", [call("read", {"path": "/p"}, at=1000), part])
        got = self.hub()
        self.assertEqual(got["status"], "running")
        self.assertEqual(got["recent"][0]["seconds"], 2.1)
        self.db.assistant("ses_root", [call("read", {"path": "/p"}, at=5000), part])
        self.assertEqual(self.hub()["recent"][0]["tool"], "read")
        self.db.assistant("ses_root", [part, call("glob", {"pattern": "*"}, "running", at=6000)])
        self.assertIsNone(self.hub()["recent"][0]["seconds"])

    def test_a_string_input_and_missing_keys_give_a_null_target(self):
        self.db.assistant("ses_root", [call("shell", "ls -la"), call("webfetch", {}), call("question", {"questions": [MARKER]})])
        got = self.hub()
        self.assertEqual((got["tool"], got["zone"], got["target"]), ("question", "thinking", None))
        self.assertEqual([(r["tool"], r["target"]) for r in got["recent"]], [("webfetch", None), ("shell", None)])

    def test_execute_zone_and_target(self):
        self.db.assistant("ses_root", [call("execute", {"code": "const r = await tools.browser.open({url: 'u'});\nreturn r;"})])
        got = self.hub()
        self.assertEqual((got["zone"], got["target"]), ("web", "browser.open"))
        self.db.assistant("ses_root", [call("execute", {"code": "await tools.opencode['session_list']({})"})])
        got = self.hub()
        self.assertEqual((got["zone"], got["target"]), ("tools", "opencode.session_list"))

    def test_reasoning_and_text(self):
        self.db.assistant("ses_root", [call("read", {"path": "/p/a.py"}, at=1000)])
        self.db.assistant("ses_root", [{"type": "reasoning", "text": "hm", "time": {"created": T0 + 9000}}])
        got = self.hub()
        self.assertEqual((got["kind"], got["zone"], got["target"], got["status"]), ("reasoning", "thinking", None, None))
        self.assertEqual([r["tool"] for r in got["recent"]], ["read"])
        self.db.assistant("ses_root", [{"type": "text", "text": "done"}])
        got = self.hub()
        self.assertEqual((got["kind"], got["text"], got["target"]), ("text", "done", None))
        self.assertNotIn("zone", got)

    def test_without_detail_there_is_no_target_or_trail_and_no_input_is_read(self):
        self.db.assistant("ses_root", [call("shell", {"command": f"echo {MARKER}"}), call("webfetch", {"url": "https://example.com/x"})])
        statements = []
        real = chatfeed._connection

        def spy(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(chatfeed, "_connection", spy):
            got = self.hub(detail=False)
        self.assertEqual((got["zone"], got["status"]), ("web", "completed"))
        self.assertNotIn("target", got)
        self.assertNotIn("recent", got)
        self.assertNotIn(MARKER, json.dumps(got))
        sql = " ".join(statements)
        self.assertNotIn(".state.input.command", sql)
        self.assertNotIn(".state.input.url", sql)
        self.assertNotIn(".state.input.path", sql)
        self.assertNotIn(".state.content", sql)
        self.assertNotIn(".metadata", sql)
        self.assertNotIn(".native", sql)
        self.assertIn(".state.input.code", sql)  # only for an `execute`'s zone

    def test_the_detail_statement_names_only_its_input_fields(self):
        self.db.assistant("ses_root", [call("write", {"path": "/p", "content": MARKER}), call("read", {"path": "/p"})])
        statements = []
        real = chatfeed._connection

        def spy(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(chatfeed, "_connection", spy):
            self.hub()
        sql = " ".join(statements)
        for banned in ("state.content", "metadata", "native", "oldString", "newString", "prompt", "input.content", "'$.input'"):
            self.assertNotIn(banned, sql)
        self.assertNotIn(MARKER, sql)

    def test_a_broken_or_missing_database_gives_the_old_activity(self):
        self.db.assistant("ses_root", [call("read", {"path": "/p"})])
        base = self.base()
        self.assertEqual(chatfeed.hub_activity("ses_root", base, db_path=self.dir / "none.db"), base)
        (self.dir / "bad.db").write_text("not a database", encoding="utf-8")
        self.assertEqual(chatfeed.hub_activity("ses_root", base, db_path=self.dir / "bad.db"), base)
        self.assertEqual(chatfeed.hub_activity("bad id", base, db_path=self.db.path), base)
        with patch.object(chatfeed, "_call_row", side_effect=RuntimeError("x")):
            self.assertEqual(chatfeed.hub_activity("ses_root", base, db_path=self.db.path), base)

    def test_a_database_without_the_tables(self):
        import sqlite3
        path = self.dir / "empty.db"
        sqlite3.connect(path).close()
        base = {"kind": "tool", "tool": "read", "text": None, "time": 1.0}
        self.assertEqual(chatfeed.hub_activity("ses_root", base, db_path=path), base)


class Workers(Case):
    """A busy worker's activity: the same zone, status and cleaned target as the hub's, no trail."""

    def setUp(self):
        super().setUp()
        self.db.session("ses_root", busy=True)
        self.db.session("ses_w", busy=True)
        self.db.session("ses_x", busy=True)
        self.db.user("ses_root", "hello")

    def entries(self, *ids):
        found = chatfeed.activities(list(ids), db_path=self.db.path)
        return found

    def run_for(self, sid, hub_id=None, **kw):
        base = self.entries(sid)
        return chatfeed.busy_activities(base, hub_id, db_path=self.db.path, **kw)[sid]

    def test_a_worker_has_zone_status_and_target_but_no_trail(self):
        self.db.assistant("ses_w", [call("read", {"path": "/p/a.py"}, at=1000, took=200),
                                    call("shell", {"command": "make build\nsecond line"}, "running", at=2000, took=0)])
        got = self.run_for("ses_w")
        self.assertEqual((got["kind"], got["tool"], got["zone"], got["status"], got["target"]),
                         ("tool", "shell", "terminal", "running", "make build"))
        self.assertNotIn("recent", got)
        self.assertEqual(set(got), {"kind", "tool", "text", "time", "zone", "status", "target"})

    def test_reasoning_and_text_need_no_query(self):
        self.db.assistant("ses_w", [call("read", {"path": "/p/a.py"}, at=1000), reasoning_part("mulling")])
        self.db.assistant("ses_x", [said_part("all done")])
        entries = self.entries("ses_w", "ses_x")
        statements = []
        real = chatfeed._connection

        def spy(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(chatfeed, "_connection", spy):
            got = chatfeed.busy_activities(entries, None, db_path=self.db.path)
        self.assertEqual(statements, [])
        self.assertEqual((got["ses_w"]["zone"], got["ses_w"]["status"], got["ses_w"]["target"]), ("thinking", None, None))
        self.assertNotIn("zone", got["ses_x"])
        self.assertEqual((got["ses_x"]["status"], got["ses_x"]["target"]), (None, None))
        self.assertNotIn("recent", got["ses_w"])

    def test_the_target_is_cleaned_exactly_as_the_hubs_is(self):
        home = str(Path.home())
        cases = [("webfetch", {"url": "https://user:pw@example.test/docs/intro?token=abc#frag"}),
                 ("read", {"path": f"{home}/proj/a.py"}),
                 ("subagent", {"agent": "explorer", "description": "look around", "prompt": MARKER}),
                 ("shell", {"command": "curl -u alice:madeup-pass https://api.example.test/v1?key=zzz"}),
                 ("shell", {"command": "curl 10.1.2.3:8080/x?sig=abcdef -H 'Authorization: Bearer madeup123456'"}),
                 ("shell", {"command": "export MADE_UP_API_TOKEN=tok-123456 && npm test"}),
                 ("shell", {"command": "psql -h 10.0.0.5 -pmadeuppw mydb"}),
                 ("shell", {"command": "x " * 200})]
        for n, (name, inp) in enumerate(cases):
            with self.subTest(case=n):
                self.db.assistant("ses_root", [call(name, inp, "running")])
                self.db.assistant("ses_w", [call(name, inp, "running")])
                hub = chatfeed.busy_activities(self.entries("ses_root"), "ses_root", db_path=self.db.path)["ses_root"]
                worker = self.run_for("ses_w", "ses_root")
                self.assertEqual(worker["target"], hub["target"])
                self.assertEqual((worker["zone"], worker["status"]), (hub["zone"], hub["status"]))

    def test_secrets_are_masked_in_a_worker_target(self):
        cases = {
            "curl -u alice:madeup-pass https://api.example.test/v1?key=zzz": ("madeup-pass", "zzz"),
            "curl 10.1.2.3:8080/x?sig=abcdef -H 'Authorization: Bearer madeup123456'": ("abcdef", "madeup123456"),
            "export MADE_UP_API_TOKEN=tok-123456 && npm test": ("tok-123456",),
            "psql -h 10.0.0.5 -pmadeuppw mydb": ("madeuppw",),
            "mysql --password=hunter2made -u bob": ("hunter2made",),
            "wget https://bob:swordfish@files.example.test/a.zip": ("swordfish",),
            "curl localhost:3000/a?code=qwerty99": ("qwerty99",),
        }
        for n, (command, secrets) in enumerate(cases.items()):
            self.db.assistant("ses_w", [call("shell", {"command": command}, "running", at=n * 10)])
            target = self.run_for("ses_w")["target"]
            self.assertTrue(target, command)
            for secret in secrets:
                self.assertNotIn(secret, target, command)
        self.db.assistant("ses_w", [call("webfetch", {"url": "https://u:p4ss@example.test/a/b?token=t0k#x"}, "running")])
        self.assertEqual(self.run_for("ses_w")["target"], "example.test/a/b")
        self.db.assistant("ses_w", [call("write", {"path": "/p/b.py", "content": MARKER}, "running")])
        got = self.run_for("ses_w")
        self.assertEqual(got["target"], "/p/b.py")
        self.assertNotIn(MARKER, json.dumps(got))

    def test_the_opt_out_leaves_no_target_and_reads_no_input(self):
        self.db.assistant("ses_w", [call("shell", {"command": f"echo {MARKER}"}, "running")])
        self.db.assistant("ses_x", [call("execute", {"code": "await tools.browser.open({url: 'https://example.test'})"}, "running")])
        entries = self.entries("ses_w", "ses_x")
        statements = []
        real = chatfeed._connection

        def spy(*args, **kwargs):
            conn = real(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(chatfeed, "_connection", spy):
            got = chatfeed.busy_activities(entries, None, detail=False, db_path=self.db.path)
        self.assertEqual((got["ses_w"]["zone"], got["ses_w"]["status"]), ("terminal", "running"))
        self.assertEqual(got["ses_x"]["zone"], "web")
        for item in got.values():
            self.assertNotIn("target", item)
            self.assertNotIn("recent", item)
        self.assertNotIn(MARKER, json.dumps(got))
        sql = " ".join(statements)
        for banned in (".state.input.command", ".state.input.url", ".state.input.path", ".state.content", ".metadata", ".native"):
            self.assertNotIn(banned, sql)
        self.assertIn(".state.input.code", sql)

    def test_one_connection_and_named_input_fields_only(self):
        self.db.assistant("ses_w", [call("write", {"path": "/p", "content": MARKER, "oldString": MARKER}, "running")])
        self.db.assistant("ses_x", [call("read", {"path": "/q"}, "running")])
        entries = self.entries("ses_w", "ses_x")
        statements, opened = [], []
        real = chatfeed._connection

        def spy(*args, **kwargs):
            conn = real(*args, **kwargs)
            opened.append(conn)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(chatfeed, "_connection", spy):
            chatfeed.busy_activities(entries, None, db_path=self.db.path)
        self.assertEqual(len(opened), 1)
        self.assertEqual(len(statements), 2)  # one short query per worker
        sql = " ".join(statements)
        for banned in ("state.content", "metadata", "native", "oldString", "newString", "prompt", "input.content", "'$.input'"):
            self.assertNotIn(banned, sql)
        self.assertNotIn(MARKER, sql)

    def test_a_failing_entry_keeps_its_base_and_the_others_still_get_detail(self):
        self.db.assistant("ses_w", [call("read", {"path": "/p"}, "running")])
        self.db.assistant("ses_x", [call("read", {"path": "/q"}, "running")])
        entries = self.entries("ses_w", "ses_x")
        real = chatfeed._call_row

        def flaky(row, detail):
            if row[6] == "/p":
                raise RuntimeError("x")
            return real(row, detail)

        with patch.object(chatfeed, "_call_row", flaky):
            got = chatfeed.busy_activities(entries, None, db_path=self.db.path)
        self.assertEqual(got["ses_w"], entries["ses_w"])
        self.assertEqual(got["ses_x"]["target"], "/q")
        self.assertEqual(chatfeed.busy_activities(entries, None, db_path=self.dir / "none.db"), entries)
        self.assertEqual(chatfeed.busy_activities({}, None, db_path=self.db.path), {})


class Snapshot(Case):
    """Which agents get the detail, and the command's privacy split."""

    def setUp(self):
        super().setUp()
        self.db.session("ses_root", agent="orchestrator", busy=True, updated=100)
        self.db.session("ses_work", parent="ses_root", agent="impl-openai", busy=True, updated=90)
        self.db.user("ses_root", "hi")
        self.db.assistant("ses_root", [call("webfetch", {"url": "https://example.com/docs/intro?x=1"}, at=1000),
                                       call("shell", {"command": "npm test"}, "running", at=2000)])
        self.db.assistant("ses_work", [call("shell", {"command": "make build"}, "running", at=2000)])

    def snapshot(self):
        return build_snapshot(root_id="ses_root", db_path=self.db.path, graph_dir=self.dir / "g", cwd=self.dir,
                              config_dir=self.dir)

    def agents(self, detail):
        activity = snapshot_json.live_activity(self.snapshot(), self.db.path, detail=detail)
        doc = snapshot_json.snapshot_document(self.snapshot(), "live", activity=activity)
        return {a["name"]: a for a in doc["agents"]}

    def test_the_hub_alone_gets_a_trail_and_a_worker_gets_zone_status_and_target(self):
        agents = self.agents(True)
        self.assertEqual((agents["orchestrator"]["activity"]["zone"], agents["orchestrator"]["activity"]["target"],
                          agents["orchestrator"]["activity"]["status"]), ("terminal", "npm test", "running"))
        self.assertEqual([r["target"] for r in agents["orchestrator"]["activity"]["recent"]], ["example.com/docs/intro"])
        worker = agents["impl-openai"]["activity"]
        self.assertEqual(set(worker), {"kind", "tool", "text", "time", "zone", "status", "target"})
        self.assertEqual((worker["zone"], worker["status"], worker["target"]), ("terminal", "running", "make build"))
        self.assertNotIn("recent", worker)

    def test_without_detail_the_hub_has_a_zone_and_a_status_only(self):
        hub = self.agents(False)["orchestrator"]["activity"]
        self.assertEqual((hub["zone"], hub["status"]), ("terminal", "running"))
        self.assertNotIn("target", hub)
        self.assertNotIn("recent", hub)
        worker = self.agents(False)["impl-openai"]["activity"]
        self.assertEqual((worker["zone"], worker["status"]), ("terminal", "running"))
        self.assertNotIn("target", worker)
        self.assertNotIn("recent", worker)

    def test_a_missing_database_is_the_old_behaviour(self):
        self.assertEqual(snapshot_json.live_activity(self.snapshot(), self.dir / "none.db"), {})

    def test_the_module_flags(self):
        out = io.StringIO()
        with patch.object(snapshot_json, "build_snapshot", side_effect=lambda **kw: self.snapshot()), \
                patch.object(snapshot_json, "load_structure", return_value=None), \
                patch.object(snapshot_json, "live_document") as doc:
            doc.return_value = {"version": 1}
            with redirect_stdout(out):
                snapshot_json.main(["--session=ses_root"])
                snapshot_json.main(["--session=ses_root", "--no-activity-detail"])
                snapshot_json.main(["--no-activity-detail", "--activity-detail"])
        self.assertEqual([c.kwargs["detail"] for c in doc.call_args_list], [True, False, True])


class Demo(unittest.TestCase):
    def test_the_hub_has_a_zone_a_target_and_a_trail_of_five(self):
        hub = demo_activity(1000.0)["ses_root"]
        self.assertEqual((hub["zone"], hub["status"], hub["tool"]), ("spawn", "running", "subagent"))
        self.assertTrue(hub["target"])
        self.assertEqual(len(hub["recent"]), 5)
        self.assertEqual({r["zone"] for r in hub["recent"]}, {"web", "terminal", "files", "search", "spawn"})
        self.assertEqual([r["status"] for r in hub["recent"]].count("error"), 1)
        times = [r["time"] for r in hub["recent"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertTrue(all(r["zone"] in chatfeed.ZONES and r["target"] and r["time"] < hub["time"] for r in hub["recent"]))
        workers = demo_activity(1000.0)
        for sid in ("ses_g", "ses_h", "ses_j"):
            self.assertEqual(set(workers[sid]) - {"zone"}, {"kind", "tool", "text", "time", "status", "target"})
            self.assertNotIn("recent", workers[sid])
        self.assertEqual([workers[sid].get("zone") for sid in ("ses_g", "ses_h", "ses_j")], ["terminal", "thinking", "search"])
        self.assertEqual([workers[sid]["target"] for sid in ("ses_g", "ses_h", "ses_j")], ["npm test -- middleware", None, "Retry-After"])

    def test_demo_without_detail(self):
        workers = demo_activity(1000.0, detail=False)
        for sid in ("ses_g", "ses_h", "ses_j"):
            self.assertNotIn("target", workers[sid])
            self.assertNotIn("recent", workers[sid])
        self.assertEqual(workers["ses_g"]["zone"], "terminal")
        hub = workers["ses_root"]
        self.assertEqual(hub["zone"], "spawn")
        self.assertNotIn("target", hub)
        self.assertNotIn("recent", hub)

    def run_cli(self, *args):
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        done = subprocess.run(list(args), capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[:300])
        return {a["name"]: a for a in json.loads(done.stdout)["agents"]}

    def test_the_agent_command_has_no_target_or_recent_unless_asked(self):
        agents = self.run_cli(*LAUNCHER, "snapshot", "--demo")
        hub = agents["orchestrator"]["activity"]
        self.assertEqual(hub["zone"], "spawn")
        self.assertNotIn("target", hub)
        self.assertNotIn("recent", hub)
        for name in ("impl-claude", "designer", "explorer"):  # the opt-out covers every agent's target
            self.assertNotIn("target", agents[name]["activity"])
            self.assertNotIn("recent", agents[name]["activity"])
        agents = self.run_cli(*LAUNCHER, "snapshot", "--demo", "--activity-detail")
        self.assertEqual(len(agents["orchestrator"]["activity"]["recent"]), 5)
        self.assertEqual(agents["impl-claude"]["activity"]["target"], "npm test -- middleware")
        self.assertNotIn("recent", agents["impl-claude"]["activity"])

    def test_the_module_the_plugins_run_carries_them(self):
        agents = self.run_cli(sys.executable, "-P", "-m", "agentgraph.snapshot_json", "--demo")
        hub = agents["orchestrator"]["activity"]
        self.assertEqual(len(hub["recent"]), 5)
        self.assertTrue(hub["target"])
        self.assertEqual(agents["explorer"]["activity"]["target"], "Retry-After")


if __name__ == "__main__":
    unittest.main()
