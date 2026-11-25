"""The spend limit (agentgraph.spend): the commands, POST /api/spend and the snapshot's `spend` field, on scratch
folders and a scratch database only. The real project folder, preset file and OpenCode database are never written."""
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

from agentgraph import demo, server, skillset, spend
from tests.winskip import WINDOWS, needs_symlinks
from agentgraph.snapshot_json import live_document

SCRATCH = Path(tempfile.gettempdir()).resolve() / "agentgraph-spend-tests"
ROOT = Path(__file__).resolve().parent.parent
MARKER = "MARKER-5d1e-never-shown"
SCHEMA = (
    "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, model TEXT, title TEXT, directory TEXT, "
    "cost REAL, tokens_input INT, tokens_output INT, time_created INT, time_updated INT, time_busy INT, time_idle INT, "
    "metadata TEXT);"
    "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL, seq INT NOT NULL, "
    "time_created INT NOT NULL, time_updated INT NOT NULL, data TEXT NOT NULL);"
    "CREATE INDEX session_message_time_created_idx ON session_message (time_created);"
)
REAL_PRESET = skillset.CONFIG
FIRST = hashlib.sha256(REAL_PRESET.read_bytes()).hexdigest() if REAL_PRESET.exists() else None


def tearDownModule():
    now = hashlib.sha256(REAL_PRESET.read_bytes()).hexdigest() if REAL_PRESET.exists() else None
    assert now == FIRST, "the real preset file changed during the tests"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = spend.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Scratch(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.project = Path(directory.name) / "project"
        self.project.mkdir()
        self.db = Path(directory.name) / "opencode.db"

    def kept(self):
        return json.loads((self.project / ".opencode" / "spend.json").read_text(encoding="utf-8"))

    def make_db(self, sessions, messages, schema=SCHEMA):
        conn = sqlite3.connect(self.db)
        conn.executescript(schema)
        for sid, parent, cost in sessions:
            conn.execute("INSERT INTO session_v2 (id, parent_id, agent, cost, directory, time_created, time_updated) "
                         "VALUES (?, ?, ?, ?, ?, 0, ?)",
                         (sid, parent, "orchestrator" if parent is None else "helper", cost, str(self.project),
                          int(time.time() * 1000)))
        for number, (sid, ago, cost) in enumerate(messages):
            at = int((time.time() - ago) * 1000)
            conn.execute("INSERT INTO session_message VALUES (?, ?, 'assistant', ?, ?, ?, ?)",
                         (f"m{number}", sid, number, at, at, json.dumps({"cost": cost, "tokens": {}})))
        conn.commit()
        conn.close()


class Amounts(unittest.TestCase):
    def test_good(self):
        for value, expect in ((50, 50.0), (7.5, 7.5), ("50", 50.0), ("$12.345", 12.35), (" 3 ", 3.0), (0.01, 0.01)):
            with self.subTest(value=value):
                self.assertEqual(spend.amount(value), expect)

    def test_refused(self):
        for value in (0, -1, "0", "-5", "abc", "", "1e3", "NaN", "inf", float("nan"), float("inf"), True, None, [], {},
                      0.001, 1_000_001, "5 dollars"):
            with self.subTest(value=value):
                with self.assertRaises(spend.SpendError):
                    spend.amount(value)


class Huge(Scratch):
    """An integer too big for a float is an ordinary refusal, not an OverflowError."""
    BIG = 10 ** 400

    def test_amount_refuses_it(self):
        for value in (self.BIG, -self.BIG):
            with self.subTest(value=value > 0):
                with self.assertRaises(spend.SpendError):
                    spend.amount(value)
                with self.assertRaises(spend.SpendError):
                    spend.set_limit(50, value, project=self.project)
        with self.assertRaises(spend.SpendError):
            spend.set_limit(self.BIG, None, project=self.project)
        self.assertFalse((self.project / ".opencode" / "spend.json").exists())

    def test_a_stored_file_holding_one_reads_as_not_ours(self):
        (self.project / ".opencode").mkdir()
        target = self.project / ".opencode" / "spend.json"
        for text in ('{"version": 1, "limit": 1%s, "warn": 5}' % ("0" * 399), '{"version": 1, "limit": 50, "warn": 1%s}' % ("0" * 399)):
            target.write_text(text, encoding="utf-8", newline="\n")
            with self.assertRaises(spend.SpendError):
                spend._read(self.project)
            code, out, err = run("show", "--dir", str(self.project), "--db", str(self.db), "--json")
            self.assertIn(code, (0, 1))
            self.assertNotIn("Traceback", err)
            self.assertEqual(spend.annotate({}, project=self.project, db_path=self.db, root=None, sessions=[])["spend"]["limit"], None)
            code, out, err = run("limit", "50", "--dir", str(self.project))
            self.assertEqual((code, err), (0, ""), out)
            self.assertEqual(self.kept()["limit"], 50.0)

    def test_the_command_refuses_it(self):
        big = str(self.BIG)
        for args in (["limit", big], ["limit", "50", "--warn", big]):
            code, out, err = run(*args, "--dir", str(self.project))
            self.assertEqual((code, out), (1, ""), args)
            self.assertEqual(len(err.strip().splitlines()), 1)
            self.assertNotIn("Traceback", err)


class Commands(Scratch):
    def test_set_default_warn_show_remove(self):
        code, out, err = run("limit", "50", "--dir", str(self.project))
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(self.kept(), {"version": 1, "limit": 50.0, "warn": 45.0})
        self.assertIn("$50.00", out)
        code, out, _ = run("limit", "80", "--warn", "60.5", "--dir", str(self.project), "--json")
        answer = json.loads(out)
        self.assertEqual((code, answer["limit"], answer["warn"], answer["previous"], answer["changed"]),
                         (0, 80.0, 60.5, {"limit": 50.0, "warn": 45.0}, True))
        # the limit changed without --warn: warn is 90% again
        run("limit", "100", "--dir", str(self.project))
        self.assertEqual(self.kept()["warn"], 90.0)
        code, out, _ = run("limit", "--off", "--dir", str(self.project), "--json")
        answer = json.loads(out)
        self.assertEqual((code, answer["limit"], answer["previous"], answer["changed"]),
                         (0, None, {"limit": 100.0, "warn": 90.0}, True))
        self.assertFalse((self.project / ".opencode" / "spend.json").exists())
        code, out, _ = run("limit", "--off", "--dir", str(self.project), "--json")
        self.assertEqual((code, json.loads(out)["changed"]), (0, False))

    def test_same_limit_is_no_change(self):
        run("limit", "50", "--dir", str(self.project))
        before = (self.project / ".opencode" / "spend.json").stat().st_mtime_ns
        code, out, _ = run("limit", "50", "--dir", str(self.project), "--json")
        self.assertEqual((code, json.loads(out)["changed"]), (0, False))
        self.assertEqual((self.project / ".opencode" / "spend.json").stat().st_mtime_ns, before)

    def test_refusals_change_nothing(self):
        run("limit", "50", "--dir", str(self.project))
        before = (self.project / ".opencode" / "spend.json").read_bytes()
        for args, text in ((["limit", "0"], "more than $0"), (["limit", "-3"], "more than $0"), (["limit", "x"], "number of dollars"),
                           (["limit", "50", "--warn", "50"], "below the limit"), (["limit", "50", "--warn", "60"], "below the limit"),
                           (["limit", "50", "--warn", "0"], "warn must be more than $0"), (["limit"], "give the limit"),
                           (["limit", "5", "--off"], "no amount"), (["limit", "2000000"], "at most")):
            with self.subTest(args=args):
                code, out, err = run(*args, "--dir", str(self.project))
                self.assertEqual((code, out), (1, ""))
                self.assertIn(text, err)
                self.assertEqual(len(err.strip().splitlines()), 1)
                code, out, _ = run(*args, "--dir", str(self.project), "--json")
                self.assertEqual(code, 1)
                self.assertIn(text, json.loads(out)["error"])
        self.assertEqual((self.project / ".opencode" / "spend.json").read_bytes(), before)

    def test_damaged_file_is_not_echoed_and_can_be_replaced(self):
        folder = self.project / ".opencode"
        folder.mkdir()
        (folder / "spend.json").write_text(MARKER, encoding="utf-8", newline="\n")
        code, out, err = run("show", "--dir", str(self.project), "--db", str(self.db))
        self.assertEqual(code, 0)  # the figures are still told; the damaged limit is read as none
        self.assertNotIn(MARKER, out + err)
        with self.assertRaises(spend.SpendError) as caught:
            spend.show(self.project)
        self.assertNotIn(MARKER, str(caught.exception))
        answer = spend.set_limit(10, project=self.project)
        self.assertEqual((answer["changed"], answer["previous"]), (True, {"limit": None, "warn": None}))
        self.assertEqual(self.kept()["limit"], 10.0)

    @needs_symlinks
    def test_symlinks_are_refused(self):
        folder = self.project / ".opencode"
        folder.mkdir()
        target = self.project / "elsewhere"
        target.write_text("keep", encoding="utf-8", newline="\n")
        (folder / "spend.json").symlink_to(target)
        with self.assertRaises(spend.SpendError):
            spend.show(self.project)
        # a symlink in place of the folder
        other = Path(self.project.parent / "real-folder")
        other.mkdir()
        project = self.project.parent / "linked"
        project.mkdir()
        (project / ".opencode").symlink_to(other, target_is_directory=True)
        code, _, err = run("limit", "9", "--dir", str(project))
        self.assertEqual(code, 1)
        self.assertIn("plain folder", err)
        self.assertEqual(list(other.iterdir()), [])
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_a_write_leaves_only_the_file_and_its_lock(self):
        run("limit", "5", "--dir", str(self.project))
        run("limit", "6", "--dir", str(self.project))
        self.assertEqual(sorted(p.name for p in (self.project / ".opencode").iterdir()), ["spend.json", "spend.lock"])
        if not WINDOWS:  # a Windows file has no owner-only mode bits
            self.assertEqual(oct((self.project / ".opencode" / "spend.json").stat().st_mode & 0o777), "0o600")

    def test_launcher(self):
        done = subprocess.run([str(ROOT / "bin" / "agentgraph"), "spend", "limit", "12", "--dir", str(self.project)],
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertEqual(self.kept()["limit"], 12.0)
        done = subprocess.run([str(ROOT / "bin" / "agentgraph"), "spend", "limit", "0", "--dir", str(self.project)],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)

    def test_run_as_a_script(self):
        done = subprocess.run([sys.executable, "-P", str(ROOT / "agentgraph" / "spend.py"), "limit", "7", "--dir",
                               str(self.project)], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)

    def test_show_reads_the_tree(self):
        self.make_db([("root", None, 3.0), ("w1", "root", 1.25), ("w2", "w1", 0.75)],
                     [("root", 60, 0.2), ("w1", 120, 0.3), ("w2", 4000, 5.0)])
        run("limit", "20", "--dir", str(self.project))
        code, out, err = run("show", "--dir", str(self.project), "--db", str(self.db), "--session", "root", "--json")
        answer = json.loads(out)
        self.assertEqual(code, 0, err)
        self.assertEqual((answer["limit"], answer["warn"], answer["chat"], answer["workers"], answer["spent"]),
                         (20.0, 18.0, 3.0, 2.0, 5.0))
        self.assertAlmostEqual(answer["rate"], 1.0, places=3)  # (0.2 + 0.3) in the last half hour, times two
        code, out, _ = run("show", "--dir", str(self.project), "--db", str(self.db), "--session", "root")
        self.assertIn("limit: $20.00", out)
        self.assertIn("chat $3.00, workers $2.00", out)
        self.assertIn("$1.00 an hour", out)


class Figures(Scratch):
    def sessions(self, *rows):
        return [SimpleNamespace(id=sid, cost=cost) for sid, cost in rows]

    def test_split_adds_up_to_the_total(self):
        root, *rest = self.sessions(("r", 2.5), ("a", 1.0), ("b", 0.5))
        self.assertEqual(spend.split(root, rest), (2.5, 1.5))
        self.assertEqual(spend.split(None, rest), (0.0, 1.5))

    def test_rate_counts_the_tree_in_the_last_half_hour(self):
        self.make_db([("root", None, 0), ("w", "root", 0), ("other", None, 0)],
                     [("root", 10, 0.5), ("w", 1700, 0.25), ("w", 1900, 7.0), ("other", 10, 9.0)])
        self.assertAlmostEqual(spend.rate(self.db, ["root", "w"]), 1.5, places=3)
        self.assertEqual(spend.rate(self.db, ["nobody"]), 0.0)

    def test_rate_is_none_without_times_or_database(self):
        self.make_db([("root", None, 0)], [], schema=SCHEMA.replace("time_created INT NOT NULL, time_updated INT NOT NULL, data",
                                                                    "time_updated INT NOT NULL, data").replace(
            "CREATE INDEX session_message_time_created_idx ON session_message (time_created);", "").replace(
            "", ""))
        self.assertIsNone(spend.rate(self.db, ["root"]))
        self.assertIsNone(spend.rate(self.project / "missing.db", ["root"]))

    def test_rate_without_the_index_still_answers(self):
        self.make_db([("root", None, 0)], [("root", 5, 1.0)], schema=SCHEMA.replace(
            "CREATE INDEX session_message_time_created_idx ON session_message (time_created);", ""))
        self.assertAlmostEqual(spend.rate(self.db, ["root"]), 2.0, places=3)

    def test_figures_shape_and_no_file_means_no_limit(self):
        self.make_db([("root", None, 1.0), ("w", "root", 2.0)], [("w", 5, 0.5)])
        root, *rest = self.sessions(("root", 1.0), ("w", 2.0))
        got = spend.figures(self.project, root, rest, self.db)
        self.assertEqual(set(got), {"limit", "warn", "rate", "chat", "workers"})
        self.assertEqual((got["limit"], got["warn"], got["chat"], got["workers"]), (None, None, 1.0, 2.0))
        self.assertAlmostEqual(got["rate"], 1.0, places=3)

    def test_annotate_never_raises(self):
        document = spend.annotate({}, project=self.project / "no" / "such", root=object(), sessions=[object()],
                                  db_path=self.project / "x.db")
        self.assertEqual(set(document["spend"]), {"limit", "warn", "rate", "chat", "workers"})
        document = spend.annotate({}, project=self.project, root=None, sessions=None)
        self.assertEqual(document["spend"]["chat"], 0.0)

    def test_the_rate_read_is_quick_on_a_large_database(self):
        self.make_db([("root", None, 0)], [])
        conn = sqlite3.connect(self.db)
        old = int((time.time() - 86400) * 1000)
        conn.executemany("INSERT INTO session_message VALUES (?, 'root', 'assistant', ?, ?, ?, ?)",
                         [(f"o{n}", n, old + n, old + n, json.dumps({"cost": 1.0})) for n in range(60000)])
        conn.execute("INSERT INTO session_message VALUES ('new', 'root', 'assistant', 70000, ?, ?, ?)",
                     (int(time.time() * 1000), int(time.time() * 1000), json.dumps({"cost": 0.5})))
        conn.commit()
        conn.close()
        started = time.perf_counter()
        value = spend.rate(self.db, ["root"])
        self.assertLess(time.perf_counter() - started, 0.25)
        self.assertAlmostEqual(value, 1.0, places=3)


class Served(Scratch):
    def setUp(self):
        super().setUp()
        self.make_db([("root", None, 4.0), ("w", "root", 1.0)], [("root", 30, 0.1)])
        base = self.project.parent
        for folder in ("graphs", "structure", "agents", "commands", "setups", "backups"):
            (base / folder).mkdir()
        self.httpd = server.make_server(
            0, graph_dir=base / "graphs", cwd=self.project, session="root", web_dir=base,
            structure_dir=base / "structure", skills_config=base / "preset.json", skills_map=base / "SKILLS.md",
            skills_backups=base / "backups", skills_profiles=base / "profiles.md", skills_dir=base / "skills",
            skills_bundled=[], agents_dir=base / "agents", commands_dir=base / "commands", setups_dir=base / "setups",
            opencode_dir=base, ledger_root=base, sessions_db=self.db)
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(self.httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.httpd.shutdown)

    def call(self, body, headers=None, method="POST", path="/api/spend"):
        port = self.httpd.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            sent = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", **(headers or {})}
            conn.request(method, path, body=data if method == "POST" else None,
                         headers={key: value for key, value in sent.items() if value is not None})
            response = conn.getresponse()
            raw = response.read()
            self.assertFalse([name for name in response.headers if name.lower().startswith("access-control-")])
            try:
                return response.status, json.loads(raw) if raw else None, response
            except ValueError:
                return response.status, raw, response

        finally:
            conn.close()

    def snapshot(self):
        status, body, _ = self.call(b"", method="GET", path="/api/snapshot")
        self.assertEqual(status, 200, body)
        return body


class Endpoint(Served):
    def test_set_change_remove(self):
        status, answer, _ = self.call({"op": "limit", "limit": 50})
        self.assertEqual((status, answer["limit"], answer["warn"], answer["changed"]), (200, 50.0, 45.0, True))
        self.assertEqual(self.kept(), {"version": 1, "limit": 50.0, "warn": 45.0})
        status, answer, _ = self.call({"op": "limit", "limit": "80", "warn": 70})
        self.assertEqual((status, answer["limit"], answer["warn"], answer["previous"]), (200, 80.0, 70.0, {"limit": 50.0, "warn": 45.0}))
        status, answer, _ = self.call({"op": "off"})
        self.assertEqual((status, answer["limit"], answer["changed"]), (200, None, True))
        self.assertFalse((self.project / ".opencode" / "spend.json").exists())

    def test_refusals_change_nothing(self):
        self.call({"op": "limit", "limit": 50})
        before = (self.project / ".opencode" / "spend.json").read_bytes()
        for body, text in (({"op": "limit", "limit": 0}, "more than $0"), ({"op": "limit", "limit": -2}, "more than $0"),
                           ({"op": "limit", "limit": "x"}, "number of dollars"), ({"op": "limit", "limit": True}, "number of dollars"),
                           ({"op": "limit", "limit": 50, "warn": 50}, "below the limit"),
                           ({"op": "limit", "limit": 50, "warn": 99}, "below the limit")):
            with self.subTest(body=body):
                status, answer, _ = self.call(body)
                self.assertEqual(status, 400, answer)
                self.assertIn(text, answer["error"])
        for body in ({"op": "limit"}, {"op": "off", "limit": 1}, {"op": "nope"}, {"op": "limit", "limit": None},
                     {"op": "limit", "limit": 5, "extra": 1}, [], b"nope", {"op": "limit", "limit": [5]}):
            with self.subTest(body=body):
                status, answer, _ = self.call(body)
                self.assertEqual(status, 400, answer)
                self.assertIn("error", answer)
        self.assertEqual((self.project / ".opencode" / "spend.json").read_bytes(), before)

    def test_a_huge_integer_is_a_bad_amount(self):
        raw = b'{"op": "limit", "limit": 1%s}' % (b"0" * 399)
        warn = b'{"op": "limit", "limit": 50, "warn": 1%s}' % (b"0" * 399)
        for body in (raw, warn):
            status, answer, _ = self.call(body)
            self.assertEqual(status, 400, answer)
            self.assertIn("error", answer)
        self.assertFalse((self.project / ".opencode" / "spend.json").exists())
        self.assertEqual(self.call({"op": "limit", "limit": 5})[0], 200)  # the handler is still alive

    def test_a_stored_huge_integer_keeps_the_snapshot_well_behaved(self):
        (self.project / ".opencode").mkdir(exist_ok=True)
        (self.project / ".opencode" / "spend.json").write_text('{"version": 1, "limit": 1%s, "warn": 5}' % ("0" * 399), encoding="utf-8", newline="\n")
        body = self.snapshot()
        self.assertIsNone(body["spend"]["limit"])

    def test_guards(self):
        good = {"op": "limit", "limit": 5}
        port = self.httpd.server_port
        for origin in ("http://evil.example", f"http://localhost:{port}", "null"):
            self.assertEqual(self.call(good, {"Origin": origin})[0], 403)
        self.assertEqual(self.call(good, {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call(good, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call(b" " * (server._SPEND_BODY_LIMIT + 1))[0], 413)
        for method in ("GET", "PUT", "DELETE"):
            status, _, response = self.call({}, method=method)
            self.assertEqual(status, 405)
            self.assertEqual(response.getheader("Allow"), "POST")
        self.assertFalse((self.project / ".opencode" / "spend.json").exists())

    def test_the_demo_refuses_it(self):
        demo_server = server.make_server(0, demo=True, web_dir=self.project.parent, cwd=self.project)
        thread = threading.Thread(target=demo_server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(demo_server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(demo_server.shutdown)
        port = demo_server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/api/spend", body=json.dumps({"op": "limit", "limit": 5}),
                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()
        self.assertFalse((self.project / ".opencode" / "spend.json").exists())


class Snapshot(Served):
    def test_live_snapshot_carries_exactly_the_object(self):
        body = self.snapshot()
        self.assertEqual(list(body["spend"]), ["limit", "warn", "rate", "chat", "workers"])
        self.assertEqual((body["spend"]["limit"], body["spend"]["warn"]), (None, None))
        self.assertEqual((body["spend"]["chat"], body["spend"]["workers"]), (4.0, 1.0))
        self.assertAlmostEqual(body["spend"]["rate"], 0.2, places=3)
        self.assertAlmostEqual(body["spend"]["chat"] + body["spend"]["workers"], body["totals"]["cost"], places=6)
        self.call({"op": "limit", "limit": 20, "warn": 12})
        self.assertEqual((self.snapshot()["spend"]["limit"], self.snapshot()["spend"]["warn"]), (20.0, 12.0))
        self.call({"op": "off"})
        self.assertIsNone(self.snapshot()["spend"]["limit"])

    def test_damaged_file_reads_as_no_limit(self):
        (self.project / ".opencode").mkdir()
        (self.project / ".opencode" / "spend.json").write_text(MARKER, encoding="utf-8", newline="\n")
        status, raw, _ = self.call(b"", method="GET", path="/api/snapshot")
        self.assertEqual(status, 200)
        self.assertNotIn(MARKER, json.dumps(raw))
        self.assertIsNone(raw["spend"]["limit"])

    def test_demo_snapshot_has_a_sample(self):
        document = live_document(demo.demo_snapshot(time.time()), "demo")
        sample = document["spend"]
        self.assertEqual(list(sample), ["limit", "warn", "rate", "chat", "workers"])
        self.assertLess(sample["warn"], sample["limit"])
        self.assertAlmostEqual(sample["chat"] + sample["workers"], document["totals"]["cost"], places=6)
        self.assertGreater(document["totals"]["cost"], 0)

    def test_the_snapshot_stays_fast_with_it(self):
        started = time.perf_counter()
        for _ in range(5):
            self.snapshot()
        self.assertLess((time.perf_counter() - started) / 5, 1.0)

    def test_the_desktop_document_has_it(self):
        from agentgraph.sources import build_snapshot
        snapshot = build_snapshot(graph_dir=self.project.parent / "graphs", root_id="root", cwd=self.project,
                                  db_path=self.db, config_dir=self.project.parent)
        document = spend.annotate({}, project=self.project, db_path=self.db, root=snapshot.root, sessions=snapshot.sessions,
                                  now=snapshot.now)
        self.assertEqual(document["spend"]["chat"], 4.0)


if __name__ == "__main__":
    unittest.main()
