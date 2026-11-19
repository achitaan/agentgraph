import http.client
import io
import json
import signal
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agentgraph import server
from agentgraph.demo import demo_snapshot


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.cwd = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.web_dir = self.cwd / "web"
        self.web_dir.mkdir()
        (self.web_dir / "index.html").write_bytes(b"<title>agentgraph test</title>")
        self.live = self.enterContext(patch.object(server, "build_snapshot",
                                                   side_effect=AssertionError("unexpected live read")))
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
