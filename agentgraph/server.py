"""Serve the agentgraph web view on localhost."""
from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

from . import skillset, structure
from .demo import demo_snapshot
from .snapshot_json import learning_document, load_structure, skills_document, snapshot_document
from .sources import ORCHESTRATION_GRAPH_DIR, build_snapshot

_HOST = "127.0.0.1"
_FLOW_PATH = "/api/structure/flow"
_SKILLS_PATH = "/api/skills"
_SKILLS_BODY_LIMIT = 4096
_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_FLOW_BODY_LIMIT = 32768  # an instruction of 2000 characters, each up to 12 bytes as escaped JSON
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}


def _read_static(directory: Path, filename: str) -> bytes:
    # Refuse symlinks without a check/open race. Nonblocking open also avoids
    # waiting on a named pipe; only regular files can be served.
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("not a regular file")
            with open(fd, "rb", closefd=False) as stream:
                return stream.read()
        finally:
            os.close(fd)
    finally:
        os.close(root)


def make_server(
    port: int, *, demo: bool = False, graph_dir: Path | None = None,
    session: str | None = None, cwd: Path | None = None, web_dir: Path | None = None,
    structure_dir: Path | None = None, skills_config: Path | None = None, skills_map: Path | None = None,
    skills_backups: Path | None = None, skills_profiles: Path | None = None,
) -> ThreadingHTTPServer:
    """Bind a server; the caller is responsible for running and closing it.
    `structure_dir` is the folder `POST /api/structure/flow` edits (default: the
    orchestration's own `structure/`). `POST /api/skills` edits `skills_config`
    (the plugin's preset file) and `skills_map` (SKILLS.md), backs up into
    `skills_backups` and reads `skills_profiles`; each defaults to skillset's own."""
    paths = {"config": skills_config or skillset.CONFIG, "map_path": skills_map or skillset.MAP,
             "backups": skills_backups or skillset.BACKUPS, "profiles": skills_profiles or skillset.PROFILES}
    directory = (web_dir if web_dir is not None else Path(__file__).with_name("web")).resolve()

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def log_message(self, format, *args):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def parse_request(self):
            if not super().parse_request():
                return False
            port = self.server.server_port
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0] not in (f"{_HOST}:{port}", f"localhost:{port}"):
                # BaseHTTPRequestHandler treats a two-token request as HTTP/0.9,
                # which cannot supply Host and would otherwise suppress status and headers.
                if self.request_version == "HTTP/0.9":
                    self.request_version = "HTTP/1.0"
                self.send_error(403)
                return False
            allowed = ("POST",) if self.path.partition("?")[0] in (_FLOW_PATH, _SKILLS_PATH) else ("GET", "HEAD")
            if self.command not in allowed:
                self.send_response(405)
                self.send_header("Allow", ", ".join(allowed))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True

        def _refuse(self, status: int, message: str):
            self._reply(status, "application/json", json.dumps({"error": message}).encode("utf-8"))

        def _json_body(self, what: str, limit: int):
            """The request's JSON body, or None after refusing it. Only this page may change anything: a
            loopback client, a JSON body of bounded length, and an Origin (browsers always send one on a
            POST) that is this server's own. Checked before the body is read."""
            if self.client_address[0] not in _LOOPBACK:
                self._refuse(403, f"only a local client may change {what}")
                return None
            origins = self.headers.get_all("Origin", [])
            if origins and (len(origins) != 1 or origins[0] != "http://" + self.headers["Host"]):
                self._refuse(403, "the request did not come from this page")
                return None
            if self.headers.get_content_type() != "application/json" or len(self.headers.get_all("Content-Type", [])) != 1:
                self._refuse(415, "the body must be JSON")
                return None
            if self.headers.get("Transfer-Encoding"):
                self._refuse(411, "the body needs a Content-Length")
                return None
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._refuse(411, "the body needs a Content-Length")
                return None
            if not 0 < length <= limit:
                self._refuse(413, "the body is empty or too large")
                return None
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._refuse(400, "the body is not valid JSON")
                return None

        def do_POST(self):
            if self.path.partition("?")[0] == _SKILLS_PATH:
                self._skills()
                return
            request = self._json_body("the structure", _FLOW_BODY_LIMIT)
            if request is None:
                return
            shapes = {"add": ({"op", "path"}, {"op", "path", "text"}), "text": ({"op", "id", "text"},), "remove": ({"op", "id"},)}
            op = request.get("op") if isinstance(request, dict) else None
            if not isinstance(op, str) or set(request) not in shapes.get(op, ()):
                self._refuse(400, 'expected {"op": "add", "path": [agents]} (and an optional "text"), '
                                  '{"op": "text", "id": flow, "text": text} or {"op": "remove", "id": flow}')
                return
            if demo:
                self._refuse(403, "the demo cannot change the structure")
                return
            try:
                answer: dict = {}
                if request["op"] == "add":
                    answer["flow"], notes = structure.add_flow(request["path"], request.get("text"), structure_dir)
                    if notes:
                        answer["notes"] = notes
                elif request["op"] == "text":
                    answer["flow"] = structure.set_flow_text(request["id"], request["text"], structure_dir)
                else:
                    structure.remove_flow(request["id"], structure_dir)
                answer["flows"] = structure.drawn_flows(structure_dir)
            except structure.StructureError as exc:
                self._refuse(400, str(exc))
                return
            except OSError as exc:
                self._refuse(500, f"could not change the structure: {exc}")
                return
            self._reply(200, "application/json", json.dumps(answer).encode("utf-8"))

        def _skills(self):
            # {"agent", "skill", "on"} adds (on: true) or removes (on: false) one installed skill;
            # {"agent", "all": true} gives the agent every skill. Same refusals as the flow endpoint.
            request = self._json_body("skills", _SKILLS_BODY_LIMIT)
            if request is None:
                return
            keys = set(request) if isinstance(request, dict) else None
            valid = (keys == {"agent", "skill", "on"} and isinstance(request["on"], bool)
                     and isinstance(request["skill"], str)) or (keys == {"agent", "all"} and request["all"] is True)
            if not valid or not isinstance(request["agent"], str):
                self._refuse(400, 'expected {"agent": name, "skill": name, "on": true|false} or {"agent": name, "all": true}')
                return
            if demo:
                self._refuse(403, "the demo cannot change skills")
                return
            try:
                if "all" in request:
                    result = skillset.set_all(request["agent"], **paths)
                else:
                    result = (skillset.add if request["on"] else skillset.remove)(request["agent"], request["skill"], **paths)
            except skillset.SkillsetError as exc:
                self._refuse(400, str(exc))
                return
            except OSError as exc:
                self._refuse(500, f"could not change the skills: {exc}")
                return
            answer = {"agent": result["agent"], "skills": result["skills"], "warnings": result.get("warnings", []),
                      "changed": bool(result.get("changed")), "message": result.get("message", "")}
            self._reply(200, "application/json", json.dumps(answer).encode("utf-8"))

        def _reply(self, status: int, content_type: str, body: bytes):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _snapshot(self, query: str):
            params = parse_qs(query, keep_blank_values=True)
            use_demo = demo or params.get("demo", [None])[0] == "1"
            task = params.get("task", [None])[0]
            try:
                if use_demo:
                    snapshot = demo_snapshot(time.time())
                else:
                    snapshot = build_snapshot(graph_dir=graph_dir, root_id=session, cwd=cwd,
                                              session_graph_dir=None if graph_dir else ORCHESTRATION_GRAPH_DIR)
                document = snapshot_document(snapshot, "demo" if use_demo else "live", task,
                                             session=None if use_demo else session,
                                             structure=None if use_demo else load_structure(structure_dir))
                if not use_demo:
                    document["learning"] = learning_document()
                    document["skills"] = skills_document()
                    skillset.annotate(document, config=paths["config"], profiles=paths["profiles"])
                body = json.dumps(document, allow_nan=False).encode("utf-8")
            except Exception as exc:
                body = json.dumps({"version": 1, "error": str(exc)}).encode("utf-8")
                self._reply(500, "application/json", body)
            else:
                self._reply(200, "application/json", body)

        def do_GET(self):
            # Use the raw target: the base class normalizes leading double slashes.
            target = self.requestline.split()[1]
            raw_path, _, query = target.partition("?")
            if not raw_path.startswith("/") or raw_path.startswith("//"):
                self.send_error(404)
                return
            try:
                path = unquote(raw_path, errors="strict")
            except UnicodeDecodeError:
                self.send_error(404)
                return
            if path == "/api/snapshot":
                self._snapshot(query)
                return
            filename = path[1:] if path != "/" else "index.html"
            if filename in ("", ".", "..") or any(c in filename for c in ("/", "\\", "\0")):
                self.send_error(404)
                return
            try:
                body = _read_static(directory, filename)
            except OSError:
                self.send_error(404)
                return
            self._reply(200, _TYPES.get(Path(filename).suffix.lower(), "application/octet-stream"), body)

        do_HEAD = do_GET

    return ThreadingHTTPServer((_HOST, port), Handler)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph serve", description=__doc__)
    parser.add_argument("--port", type=int, default=4747, help="localhost port (default: 4747)")
    parser.add_argument("--demo", action="store_true", help="use sample data")
    parser.add_argument("--dir", type=Path, help="directory of graph JSON files")
    parser.add_argument("--session", metavar="ID", help="orchestrator session to watch")
    parser.add_argument("--open", action="store_true", help="open the view in a browser")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        try:
            server = make_server(args.port, demo=args.demo, graph_dir=args.dir, session=args.session)
        except OSError as exc:
            print(f"agentgraph serve: {exc}", file=sys.stderr)
            return 1
        with server:
            address = f"http://{_HOST}:{server.server_port}/"
            print(f"agentgraph is serving at {address}", flush=True)
            if args.open:
                webbrowser.open(address)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
