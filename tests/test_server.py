import http.client
import io
import json
import signal
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentgraph import server
from agentgraph.demo import demo_snapshot
from tests.shellskip import CHECK_SECOND, TRUE
from tests.winskip import CAN_SYMLINK


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.cwd = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.web_dir = self.cwd / "web"
        self.web_dir.mkdir()
        (self.web_dir / "index.html").write_bytes(b"<title>agentgraph test</title>")
        self.live = self.enterContext(patch.object(server, "build_snapshot",
                                                   side_effect=AssertionError("unexpected live read")))
        # The demo snapshots are dated 900: against the user's real database the spend rate would read every row of it.
        self.enterContext(patch.object(server, "DB_PATH", self.cwd / "no-sessions.db"))
        self.httpd = server.make_server(0, web_dir=self.web_dir, graph_dir=self.cwd / "graphs",
                                        cwd=self.cwd, session="root")
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(self.httpd.server_close)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=2)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
            return response.status, response.headers, body
        finally:
            conn.close()

    def test_loopback_and_index(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")
        for path in ("/", "/index.html"):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path, {"Host": f"localhost:{self.httpd.server_port}"})
                self.assertEqual(status, 200)
                self.assertEqual(headers.get_content_type(), "text/html")
                self.assertEqual(body, b"<title>agentgraph test</title>")

    def test_handler_sets_ten_second_socket_timeout(self):
        handler = self.httpd.RequestHandlerClass
        self.assertEqual(handler.timeout, 10)
        accepted, client = socket.socketpair()
        self.addCleanup(accepted.close)
        self.addCleanup(client.close)
        observed = []
        with patch.object(handler, "handle", lambda instance: observed.append(instance.connection.gettimeout())):
            handler(accepted, ("127.0.0.1", 0), self.httpd)
        self.assertEqual(observed, [10])

    def test_stalled_requests_are_closed_on_timeout(self):
        self.assertEqual(self.httpd.RequestHandlerClass.timeout, 10)
        with patch.object(self.httpd.RequestHandlerClass, "timeout", 0.05):
            for request in (b"", b"GET / HTTP/1.1\r\nHost:"):
                with self.subTest(request=request), socket.create_connection(self.httpd.server_address, timeout=1) as client:
                    if request:
                        client.sendall(request)
                    self.assertEqual(client.recv(1), b"")

    def test_static_types(self):
        for ext, content_type in (("js", "text/javascript"), ("css", "text/css"),
                                  ("json", "application/json"), ("svg", "image/svg+xml")):
            with self.subTest(ext=ext):
                filename = f"asset.{ext}"
                (self.web_dir / filename).write_bytes(b"test file")
                status, headers, body = self.request("GET", f"/{filename}?ignored=1")
                self.assertEqual(status, 200)
                self.assertEqual(headers.get_content_type(), content_type)
                self.assertEqual(body, b"test file")

    def test_static_cannot_escape_or_descend(self):
        sub = self.web_dir / "sub"
        sub.mkdir()
        (sub / "file.js").write_bytes(b"nested file")
        (self.cwd / "agentgraph").mkdir()
        (self.cwd / "agentgraph" / "contract.py").write_bytes(b"outside file")
        (self.cwd / "x").write_bytes(b"outside file")
        if CAN_SYMLINK:  # without the right to make links the request below is still refused, there is just no link to follow
            (self.web_dir / "link.js").symlink_to(self.cwd / "x")
        for path in ("/../agentgraph/contract.py", "/%2e%2e/x", "/sub/file.js", "/sub%2ffile.js",
                     f"http://127.0.0.1:{self.httpd.server_port}/index.html", str(self.cwd / "x"),
                     "//index.html", "/link.js", "/sub", "/missing.js"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_snapshot_demo_and_task_selection(self):
        status, headers, body = self.request("GET", "/api/snapshot?demo=1&task=add-rate-limit")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/json")
        doc = json.loads(body)
        self.assertEqual((doc["version"], doc["source"], len(doc["agents"])), (1, "demo", 14))
        self.assertEqual(doc["graph"]["task"], "add-rate-limit")
        status, _, body = self.request("GET", "/api/snapshot?demo=1&task=unknown%20task")
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["graph"])
        self.live.assert_not_called()

    def test_live_snapshot_shows_the_requested_sessions_graph(self):
        from dataclasses import replace

        snapshot = demo_snapshot(900)
        current = snapshot.graphs[0]
        current.session = "ses_someone_else"  # the newest graph belongs to another session
        snapshot.graphs.append(replace(current, task="mine", updated=1, session="root", nodes=[], events=[]))
        self.live.side_effect = None
        self.live.return_value = snapshot
        status, _, body = self.request("GET", "/api/snapshot")
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertEqual(doc["graph"]["task"], "mine")
        self.assertEqual(self.live.call_args.kwargs["root_id"], "root")
        # An explicit task still wins, and demo data ignores the session.
        status, _, body = self.request("GET", "/api/snapshot?task=add-rate-limit")
        self.assertEqual(json.loads(body)["graph"]["task"], "add-rate-limit")
        status, _, body = self.request("GET", "/api/snapshot?demo=1")
        self.assertEqual(json.loads(body)["graph"]["task"], "add-rate-limit")
        self.assertEqual(json.loads(body)["warnings"], [])

    def test_each_live_request_gets_a_fresh_snapshot(self):
        self.live.side_effect = [demo_snapshot(900), demo_snapshot(901)]
        for timestamp in (900, 901):
            status, _, body = self.request("GET", "/api/snapshot")
            self.assertEqual(status, 200)
            doc = json.loads(body)
            self.assertEqual((doc["source"], doc["generated"]), ("live", timestamp))
        self.assertEqual(self.live.call_count, 2)
        for call in self.live.call_args_list:
            # An explicit graph folder is the only one read.
            self.assertEqual(call.kwargs, {"graph_dir": self.cwd / "graphs", "root_id": "root", "cwd": self.cwd,
                                           "session_graph_dir": None})

    def test_host_header_and_allowed_methods(self):
        for host in ("example.com", "127.0.0.1", "localhost:0"):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", "/api/snapshot?demo=1", {"Host": host})[0], 403)
        for method in ("POST", "OPTIONS", "UNRECOGNIZED"):
            with self.subTest(method=method):
                status, headers, _ = self.request(method, "/api/snapshot?demo=1")
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], "GET, HEAD")

    def test_head(self):
        for path, content_type in (("/", "text/html"), ("/api/snapshot?demo=1", "application/json")):
            with self.subTest(path=path):
                status, headers, body = self.request("HEAD", path)
                self.assertEqual(status, 200)
                self.assertEqual(headers.get_content_type(), content_type)
                self.assertGreater(int(headers["Content-Length"]), 0)
                self.assertEqual(body, b"")

    def test_document_error(self):
        stderr = io.StringIO()
        with patch.object(server, "snapshot_document", side_effect=ValueError("document failed")), redirect_stderr(stderr):
            status, headers, body = self.request("GET", "/api/snapshot?demo=1")
        self.assertEqual(status, 500)
        self.assertEqual(headers.get_content_type(), "application/json")
        self.assertEqual(json.loads(body), {"version": 1, "error": "document failed"})
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_reports_port_already_in_use(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(server.main(["--port", str(self.httpd.server_port)]), 1)
        self.assertTrue(err.getvalue().startswith("agentgraph serve:"))
        self.assertEqual(len(err.getvalue().splitlines()), 1)


class ServeCommandTests(unittest.TestCase):
    def test_cli_opens_browser_and_handles_both_interrupts(self):
        for interrupt in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(interrupt=interrupt):
                stdout, stderr = io.StringIO(), io.StringIO()
                previous = signal.getsignal(signal.SIGTERM)
                with (patch.object(server.ThreadingHTTPServer, "serve_forever", side_effect=lambda: signal.raise_signal(interrupt)),
                      patch.object(server.webbrowser, "open") as open_browser,
                      redirect_stdout(stdout), redirect_stderr(stderr)):
                    self.assertEqual(server.main(["--port", "0", "--demo", "--open"]), 0)
                address, = open_browser.call_args.args
                self.assertRegex(address, r"^http://127\.0\.0\.1:[1-9][0-9]*/$")
                self.assertEqual(stdout.getvalue(), f"agentgraph is serving at {address}\n")
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(signal.getsignal(signal.SIGTERM), previous)


class LoopEndpointTests(unittest.TestCase):
    """GET /api/loops, GET /api/loop/run and POST /api/loop on temporary folders and the scripted executor (no OpenCode)."""
    SETTINGS = {"check": {"command": CHECK_SECOND, "timeout": 20}, "limits": {"attempts": 4}}

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.structure, self.project, self.work = self.tmp / "structure", self.tmp / "project", self.tmp / "work"
        (self.structure / "30-build").mkdir(parents=True)
        (self.structure / "30-build" / "worker.md").write_text("Does the work.\n")
        (self.structure / "30-build" / "reviewer.md").write_text("Reviews.\n")
        (self.structure / "flows").mkdir()
        (self.structure / "flows" / "self.md").write_text("path: worker -> worker\n\nCheck its own work.\n")
        (self.structure / "flows" / "pair.md").write_text("path: worker -> reviewer\n\nNever returns to the worker.\n")
        self.project.mkdir()
        self.work.mkdir()
        self.httpd = self.serve()

    def serve(self, **extra):
        self.enterContext(patch.object(server, "build_snapshot", side_effect=AssertionError("unexpected live read")))
        httpd = server.make_server(0, web_dir=self.tmp, graph_dir=self.tmp / "graphs", cwd=self.project, session="root",
                                   skills_config=self.tmp / "none.json", structure_dir=self.structure,
                                   **{"loop_executor_spec": {"kind": "scripted", "attempts": [{}] * 6}, **extra})
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        return httpd

    def call(self, method, path, body=None, headers=None, httpd=None):
        port = (httpd or self.httpd).server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            sent = {"Origin": f"http://127.0.0.1:{port}", **(headers or {})}
            data = None
            if body is not None:
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                sent.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=data, headers=sent)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None, raw
        finally:
            conn.close()

    def post(self, op, expect=200, **fields):
        status, body, _ = self.call("POST", "/api/loop", {"op": op, **fields})
        self.assertEqual(status, expect, body)
        return body

    def loops(self):
        status, body, _ = self.call("GET", "/api/loops")
        self.assertEqual(status, 200, body)
        return body

    def entry(self, flow="self"):
        return next(item for item in self.loops()["loops"] if item["flow"] == flow)

    def save(self, rev="", **settings):
        return self.post("save", flow="self", settings={**self.SETTINGS, **settings}, rev=rev)

    def wait(self, run, *final, seconds=30):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            row = next((r for r in self.loops()["runs"] if r["run"] == run), None)
            if row and row["status"] in final:
                return row
            time.sleep(0.05)
        self.fail(f"run {run} did not reach {final}: {self.loops()['runs']}")

    def test_listing_shape_before_anything_is_saved(self):
        doc = self.loops()
        self.assertEqual((doc["project"], doc["scratch"], doc["orphans"], doc["runs"]), (str(self.project), False, [], []))
        pair, own = doc["loops"]
        self.assertEqual((own["runnable"], own["settings"], own["rev"], own["problem"]), (True, None, "", ""))
        self.assertEqual((pair["runnable"], pair["settings"]), (False, None))
        self.assertIn("does not loop back", pair["reason"])
        self.assertEqual([loop["flow"] for loop in self.call("GET", "/api/loops?demo=1")[1]["loops"]], ["watch-build", "nightly-review"])  # made up, never this folder's

    def test_save_round_trip_with_before_and_undo(self):
        first = self.save()
        self.assertEqual((first["ok"], first["before"], first["settings"]["flow"]), (True, None, "self"))
        self.assertEqual(self.entry()["rev"], first["rev"])
        second = self.save(first["rev"], goal="Do it.")
        self.assertEqual(second["before"], first["settings"])
        self.assertEqual(self.entry()["settings"]["goal"], "Do it.")
        undone = self.post("save", flow="self", settings=second["before"], rev=second["rev"])
        self.assertEqual(undone["rev"], first["rev"])
        gone = self.post("remove", flow="self", rev=undone["rev"])
        self.assertEqual(gone["before"], first["settings"])
        self.assertIsNone(self.entry()["settings"])
        self.assertEqual(self.post("save", flow="self", settings=gone["before"], rev="")["rev"], first["rev"])

    def test_stale_rev_is_409_with_the_current_rev(self):
        first = self.save()
        status, body, _ = self.call("POST", "/api/loop", {"op": "save", "flow": "self", "settings": self.SETTINGS, "rev": ""})
        self.assertEqual((status, body["rev"]), (409, first["rev"]))
        self.assertEqual(self.call("POST", "/api/loop", {"op": "remove", "flow": "self", "rev": "ff"})[0], 409)
        self.assertEqual(self.entry()["rev"], first["rev"])

    def test_refusals_are_400_with_the_controllers_words(self):
        status, body, _ = self.call("POST", "/api/loop", {"op": "save", "flow": "self", "rev": "", "settings": {**self.SETTINGS, "x": 1}})
        self.assertEqual(status, 400)
        self.assertIn("unknown key 'x'", body["error"])
        self.assertEqual(self.call("POST", "/api/loop", {"op": "save", "flow": "pair", "rev": "", "settings": self.SETTINGS})[2].count(b"does not loop back"), 1)
        for body in ({"op": "nope"}, {"op": "save", "flow": "self"}, {"op": "start"}, {"op": "start", "flow": "self", "x": 1},
                     {"op": "cancel", "run": 3}, {"op": "save", "flow": "self", "rev": "", "settings": []}, [1], {"op": "start", "flow": "self", "goal": None}):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/api/loop", body)[0], 400)
        self.assertEqual(self.post("start", expect=400, flow="self", goal="g")["error"].count("no loop is saved"), 1)
        self.assertEqual(self.post("start", expect=400, flow="ghost", goal="g")["error"].count("ghost"), 1)
        self.assertEqual(self.post("start", expect=400, flow="self", goal="g", workdir="relative")["error"].count("absolute"), 1)
        self.assertFalse((self.structure / ".loops").exists())

    def test_a_loop_runs_fail_then_pass_over_http(self):
        self.save()
        started = self.post("start", flow="self", goal="Make the check pass.", workdir=str(self.work))
        run = started["run"]
        self.assertEqual((started["ok"], started["status"], started["reason"]), (True, "running", ""))
        row = self.wait(run, "passed")
        self.assertEqual((row["flow"], row["attempts"], row["limit"], row["live"]), ("self", 2, 4, False))
        self.assertEqual(row["last_check"], {"outcome": "pass", "exit": 0})
        self.assertIsNone(row["cost"])
        status, doc, raw = self.call("GET", f"/api/loop/run?id={run}")
        self.assertEqual(status, 200)
        self.assertEqual((doc["run"]["status"], doc["run"]["live"]), ("passed", False))
        first, second = doc["attempts"]
        self.assertIn("check call 1", first["check_output"])
        self.assertIn("check call 2", second["check_output"])
        self.assertIn("Make the check pass.", first["message"])
        self.assertIn("check call 1", second["message"])
        self.assertEqual((doc["definition"]["workdir"], doc["definition"]["goal"]), (str(self.work), "Make the check pass."))
        self.assertIn(b'"cost": null', raw)  # unknown is null in the JSON, never 0
        self.assertEqual((self.work / ".count").read_text().strip(), "2")

    def test_no_check_is_an_invalid_run_with_no_attempt(self):
        self.post("save", flow="self", settings={"limits": {"attempts": 3}}, rev="")
        started = self.post("start", flow="self", goal="Do it.")
        self.assertEqual(started["status"], "invalid")
        self.assertIn("no check command", started["reason"])
        row, = self.loops()["runs"]
        self.assertEqual((row["status"], row["attempts"]), ("invalid", 0))
        doc = self.call("GET", f"/api/loop/run?id={started['run']}")[1]
        self.assertEqual(doc["attempts"], [])

    def test_cancel_a_running_loop(self):
        hanging = self.serve(loop_executor_spec={"kind": "scripted", "attempts": [{"hang": True}]})  # the agent turn waits for a cancel
        self.save()
        run = self.call("POST", "/api/loop", {"op": "start", "flow": "self", "goal": "g", "workdir": str(self.work)}, httpd=hanging)[1]["run"]
        row = self.wait(run, "running")
        end = time.monotonic() + 10
        while not row["live"] and time.monotonic() < end:  # the controller takes the run's lock a moment after it starts
            time.sleep(0.05)
            row = self.wait(run, "running")
        self.assertEqual(self.post("cancel", run=run), {"ok": True, "run": run})
        self.assertEqual(self.wait(run, "cancelled")["attempts"], 1)
        self.assertEqual(self.post("cancel", expect=409, run=run)["error"].count("already cancelled"), 1)

    def settle(self, run, *final, seconds=30):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            detail = self.call("GET", f"/api/loop/run?id={run}")[1]
            if detail["run"]["status"] in final:
                return detail
            time.sleep(0.05)
        self.fail(f"run {run} did not reach {final}")

    def test_release_3_listing_run_detail_and_ops_over_http(self):
        first = self.save()
        self.save(first["rev"], goal="Second.")
        self.assertEqual(len(self.entry()["versions"]), 1)
        version = self.entry()["versions"][0]["version"]
        rev = self.entry()["rev"]
        self.assertEqual(self.post("restore", expect=409, flow="self", version=version, rev="ff")["rev"], rev)
        self.assertIn("not a version", self.post("restore", expect=400, flow="self", version="../x", rev=rev)["error"])
        restored = self.post("restore", flow="self", version=version, rev=rev)
        self.assertEqual((restored["rev"], restored["before"]["goal"]), (first["rev"], "Second."))
        trial = self.post("start", flow="self", goal="g", workdir=str(self.work), settings={"check": {"command": TRUE, "timeout": 20}, "limits": {"attempts": 1}})["run"]
        detail = self.settle(trial, "passed")
        row = next(r for r in self.loops()["runs"] if r["run"] == trial)
        self.assertEqual((row["trial"], row["passed_by"], row["waiting"], len(row["settings_sha256"])), (True, "check", None, 64))
        step = detail["attempts"][0]["steps"][0]
        self.assertEqual(set(step), {"step", "try", "message", "reply", "findings"})
        self.assertIn("g", step["message"])
        self.assertEqual(self.entry()["rev"], first["rev"])  # the saved loop is the restored one; the trial changed nothing
        self.assertEqual(self.post("pause", expect=409, run=trial)["error"].count("passed"), 1)
        self.assertEqual(self.post("resume", expect=409, run=trial)["error"].count("passed"), 1)
        self.assertEqual(self.post("resume", expect=400, run=trial, choice="maybe")["error"].count("choice"), 1)
        self.assertEqual(self.post("answer", expect=409, run=trial, answer="approve")["error"].count("passed"), 1)
        self.assertEqual(self.post("pause", expect=404, run="20261121-043755-90a7")["error"].count("no run"), 1)
        for body in ({"op": "pause"}, {"op": "resume", "run": "r", "choice": 1}, {"op": "answer", "run": "r"}, {"op": "restore", "flow": "self"}):
            self.assertEqual(self.call("POST", "/api/loop", body)[0], 400)
        demo = self.serve(demo=True)
        for body in ({"op": "pause", "run": trial}, {"op": "restore", "flow": "self", "version": version, "rev": ""}):
            self.assertEqual(self.call("POST", "/api/loop", body, httpd=demo)[0], 403)

    def test_run_ids_are_validated(self):
        for bad in ("..%2Fx", "x", "", "20261121-043755-90AZ"):
            with self.subTest(bad=bad):
                self.assertEqual(self.call("GET", f"/api/loop/run?id={bad}")[0], 400)
        self.assertEqual(self.call("GET", "/api/loop/run")[0], 400)
        self.assertEqual(self.call("GET", "/api/loop/run?id=20261121-043755-90a7")[0], 404)
        self.assertEqual(self.call("GET", "/api/loop/run?id=20261121-043755-90a7&id=20261121-043755-90a8")[0], 400)
        self.assertEqual(self.post("cancel", expect=400, run="../x")["error"].count("not a run id"), 1)
        self.assertEqual(self.post("cancel", expect=404, run="20261121-043755-90a7")["error"].count("no run"), 1)

    def test_demo_mode_refuses_posts_and_lists_made_up_loops_only(self):
        demo = self.serve(demo=True)
        for body in ({"op": "save", "flow": "self", "settings": self.SETTINGS, "rev": ""}, {"op": "start", "flow": "self", "goal": "g"},
                     {"op": "pause", "run": "20261123-080000-a1b2"}, {"op": "cancel", "run": "20261123-080000-a1b2"}):
            self.assertEqual(self.call("POST", "/api/loop", body, httpd=demo)[0], 403)
        listing = self.call("GET", "/api/loops", httpd=demo)[1]
        self.assertEqual([loop["flow"] for loop in listing["loops"]], ["watch-build", "nightly-review"])  # nothing of the project's own
        self.assertEqual([(row["flow"], row["status"], row["timing"]["kind"]) for row in listing["runs"]],
                         [("watch-build", "running", "interval"), ("nightly-review", "paused", "timebox")])
        self.assertEqual(self.call("GET", "/api/loop/run?id=20261121-043755-90a7", httpd=demo)[0], 404)
        status, detail, _ = self.call("GET", f"/api/loop/run?id={listing['runs'][0]['run']}", httpd=demo)
        self.assertEqual((status, detail["run"]["status"], detail["run"]["timing"]["wakes"]), (200, "running", 3))
        self.assertFalse((self.structure / ".loops").exists())

    def test_the_demo_snapshot_draws_the_two_demo_loops_as_flows(self):
        demo = self.serve(demo=True)
        flows = self.call("GET", "/api/snapshot", httpd=demo)[1]["structure"]["flows"]
        self.assertEqual([(flow["id"], flow["path"][0] == flow["path"][1]) for flow in flows], [("watch-build", True), ("nightly-review", True)])

    def test_protections_like_the_other_endpoints(self):
        body = {"op": "save", "flow": "self", "settings": self.SETTINGS, "rev": ""}
        self.assertEqual(self.call("POST", "/api/loop", body, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/loop", b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("POST", "/api/loop", b"{not json")[0], 400)
        self.assertEqual(self.call("GET", "/api/loop")[0], 405)
        self.assertEqual(self.call("POST", "/api/loops", body)[0], 405)
        self.assertEqual(self.call("POST", "/api/loop/run", body)[0], 405)
        self.assertFalse((self.structure / ".loops").exists())

    def test_a_valueerror_is_answered_not_dropped(self):
        from agentgraph import loopapi
        with patch.object(loopapi, "save", side_effect=ValueError("surrogates not allowed")):
            status, body, _ = self.call("POST", "/api/loop", {"op": "save", "flow": "self", "settings": self.SETTINGS, "rev": ""})
        self.assertEqual(status, 400)
        self.assertIn("surrogates", body["error"])

    def test_scratch_is_reported(self):
        scratched = self.serve(loop_scratch=self.tmp / "scratch")
        self.assertTrue(self.call("GET", "/api/loops", httpd=scratched)[1]["scratch"])


class ImproveEndpointTests(unittest.TestCase):
    """GET and POST /api/improve on temporary folders only (config root, ledger root, preset file)."""

    def setUp(self):
        from agentgraph import improve
        self.improve = improve
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root, self.ledger = self.tmp / "config", self.tmp / "ledgers"
        (self.root / "oh-my-opencode-slim").mkdir(parents=True)
        self.ledger.mkdir()
        self.rel = "oh-my-opencode-slim/designer_append.md"
        self.file = self.root / self.rel
        self.original = b"Be brief.\n"
        self.file.write_bytes(self.original)
        self.home = self.ledger / "memory"
        self.record = improve.propose(self.home, self.root, "Shorter", "too long", [(self.rel, "Be briefer.\n")])
        self.enterContext(patch.object(server, "build_snapshot", side_effect=AssertionError("unexpected live read")))
        self.httpd = server.make_server(0, web_dir=self.tmp, graph_dir=self.tmp / "graphs", cwd=self.tmp, session="root",
                                        skills_config=self.tmp / "none.json", ledger_root=self.ledger,
                                        improve_config_root=self.root)
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(self.httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path="/api/improve", body=None, headers=None):
        port = self.httpd.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            sent = {"Origin": f"http://127.0.0.1:{port}", **(headers or {})}
            data = None
            if body is not None:
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                sent.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=data, headers=sent)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
        finally:
            conn.close()

    def act(self, action, **extra):
        return self.call("POST", body={"action": action, "id": self.record["id"], **extra})

    def test_list_and_show(self):
        status, body = self.call("GET")
        self.assertEqual(status, 200)
        item, = body["proposals"]
        self.assertEqual((item["id"], item["state"], item["files"], item["added"], item["removed"]),
                         (self.record["id"], "drafted", [self.rel], 1, 1))
        self.assertEqual(item["summary"]["verdict"], "not enough runs")
        self.assertNotIn("changes", item)
        status, body = self.call("GET", f"/api/improve?id={self.record['id']}")
        self.assertEqual(status, 200)
        self.assertIn("+Be briefer.", body["files"][0]["diff"])
        self.assertEqual(body["changes"][0]["new_text"], "Be briefer.\n")
        self.assertEqual(self.call("GET", "/api/improve?id=nope")[0], 404)
        self.assertEqual(self.call("GET", "/api/improve?id=../x")[0], 400)

    def test_approve_then_revert(self):
        status, body = self.act("approve", note="yes please")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["state"], "applied")
        self.assertEqual((body["history"][-1]["who"], body["history"][-1]["note"]), ("user", "yes please"))
        self.assertEqual(self.file.read_bytes(), b"Be briefer.\n")
        self.assertTrue((self.home / "improvements.jsonl").is_file())
        self.assertEqual(self.call("GET")[1]["proposals"][0]["state"], "applied")
        status, body = self.act("revert")
        self.assertEqual((status, body["state"]), (200, "reverted"))
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_approve_refused_when_the_file_changed_and_revert_when_edited(self):
        self.file.write_bytes(b"edited\n")
        status, body = self.act("approve")
        self.assertEqual(status, 400)
        self.assertIn("changed since", body["error"])
        self.assertEqual(self.file.read_bytes(), b"edited\n")
        self.file.write_bytes(self.original)
        self.assertEqual(self.act("approve")[0], 200)
        self.file.write_bytes(b"hand edit\n")
        status, body = self.act("revert")
        self.assertEqual(status, 400)
        self.assertEqual(self.file.read_bytes(), b"hand edit\n")

    def test_a_valueerror_from_the_proposal_code_is_answered_not_dropped(self):
        with patch.object(self.improve, "approve", side_effect=ValueError("surrogates not allowed")):
            status, body = self.act("approve", note="yes")
        self.assertEqual(status, 400)
        self.assertIn("surrogates", body["error"])

    def test_reject_changes_no_file_and_needs_a_reason(self):
        self.assertEqual(self.act("reject")[0], 400)
        status, body = self.act("reject", note="not now")
        self.assertEqual((status, body["state"]), (200, "rejected"))
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(self.act("approve")[0], 400)

    def test_reopen_undoes_a_reject(self):
        self.assertEqual(self.act("reopen")[0], 400)  # not rejected
        self.act("reject", note="not now")
        status, body = self.act("reopen")
        self.assertEqual((status, body["state"], body["history"][-1]["who"]), (200, "drafted", "user"))
        self.assertEqual(self.act("approve")[0], 200)
        self.assertEqual(self.file.read_bytes(), b"Be briefer.\n")

    def test_bad_requests_and_protections(self):
        for body in ({"action": "approve"}, {"action": "force", "id": self.record["id"]},
                     {"action": "approve", "id": 3}, {"action": "approve", "id": "x", "force": True},
                     {"action": "approve", "id": "x", "note": 1}, [1]):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", body=body)[0], 400)
        self.assertEqual(self.act("approve", note="x", **{"extra": 1})[0], 400)
        self.assertEqual(self.call("POST", headers={"Origin": "http://evil.example"},
                                   body={"action": "approve", "id": self.record["id"]})[0], 403)
        self.assertEqual(self.call("POST", body=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("DELETE")[0], 405)
        self.assertEqual(self.file.read_bytes(), self.original)
