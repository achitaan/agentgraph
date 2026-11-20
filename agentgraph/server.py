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

from . import agentfiles, chatfeed, flowcommands, flowrun, mcpview, outputs, roster, skillfiles, skillset, structure, teams
from .demo import demo_activity, demo_snapshot
from .snapshot_json import (ledger_home, learning_document, live_activity, load_structure, skills_document,
                            snapshot_document)
from .sources import DB_PATH, ORCHESTRATION_GRAPH_DIR, build_snapshot, find_graph_dir

_HOST = "127.0.0.1"
_FLOW_PATH = "/api/structure/flow"
_SKILLS_PATH = "/api/skills"
_SKILLS_BODY_LIMIT = 4096
_CHAT_PATH = "/api/chat"  # GET only: one session's recent chat (agentgraph.chatfeed), a session of the shown tree only
_ROSTER_PATH = "/api/roster"
_ROSTER_BODY_LIMIT = 131072  # a prompt of 8000 characters, each up to 12 bytes as escaped JSON, and the rest
_SKILLFILE_PATH = "/api/skillfile"
_SKILLFILE_BODY_LIMIT = 12 * 200_000 + 16384  # a skill's text of 200000 characters as escaped JSON
_TEAM_PATH = "/api/structure/team"
_TEAM_BODY_LIMIT = 32768  # as a flow: an instruction of 2000 characters
_OUTPUT_PATH = "/api/output"  # a step's output: read, write (undoable), restore (agentgraph.outputs)
_OUTPUT_BODY_LIMIT = 12 * outputs.TEXT_LIMIT + 16384  # an output's text as escaped JSON
_POSTS = (_FLOW_PATH, _SKILLS_PATH, _ROSTER_PATH, _SKILLFILE_PATH, _TEAM_PATH, _OUTPUT_PATH)
_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_FLOW_BODY_LIMIT = 32768  # an instruction of 2000 characters, each up to 12 bytes as escaped JSON (and a goal of 4000)
# A write from a view that has not seen a later change (stale-contract.md): every op that names an existing thing
# takes an optional "expect", the `rev` the view was showing; a stale one changes nothing and answers 409 with
# {"error", "rev"}, the current rev. Without "expect" an op works as it always did.
_STALE = (agentfiles.StaleError, structure.StaleError)


def _expect_ok(request: dict) -> bool:
    """A request's "expect", when it has one, looks like a rev (agentfiles.expect_ok: 16 lowercase hex digits).
    Anything else (empty, blank, null, another shape) is bad input (400), never taken as no "expect" and never
    compared; the command lines and the Desktop plugin refuse it alike."""
    return "expect" not in request or (request["expect"] is not None and agentfiles.expect_ok(request["expect"]))
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
    skills_dir: Path | None = None, skills_bundled: list[Path] | None = None, agents_dir: Path | None = None,
    opencode_dir: Path | None = None, ledger_root: Path | None = None, commands_dir: Path | None = None,
    sessions_db: Path | None = None,
) -> ThreadingHTTPServer:
    """Bind a server; the caller is responsible for running and closing it.
    `structure_dir` is the folder `POST /api/structure/flow` edits (default: the
    orchestration's own `structure/`). `POST /api/skills` edits `skills_config`
    (the plugin's preset file) and `skills_map` (SKILLS.md), backs up into
    `skills_backups` and reads `skills_profiles`; each defaults to skillset's own. `POST /api/roster` adds,
    removes and restores agents in those files and the structure folder (`agents_dir`: OpenCode's agents folder,
    read only). `POST /api/skillfile` reads and edits the skill files in `skills_dir` (`skills_bundled`: where
    plugins keep the skills they bundle, read only). `POST /api/structure/team` edits the groups in
    `structure_dir`. A snapshot reads `opencode.json` from `opencode_dir` (and the models catalogue only for the
    real folder) and the ledger (`memory-<preset>`) from `ledger_root`; each defaults to the real one. A flow's
    command is written into `commands_dir` (default: OpenCode's commands folder). The sessions come from
    `sessions_db` (default: OpenCode's database), and so do the costs in each flow's `stats`."""
    paths = {"config": skills_config or skillset.CONFIG, "map_path": skills_map or skillset.MAP,
             "backups": skills_backups or skillset.BACKUPS, "profiles": skills_profiles or skillset.PROFILES}
    directory = (web_dir if web_dir is not None else Path(__file__).with_name("web")).resolve()
    structure_root = structure_dir if structure_dir is not None else roster.STRUCTURE_DIR  # what the writers use
    EXPECT = "expect"  # a shape's fourth item: the op takes an optional "expect"

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
            allowed = ("POST",) if self.path.partition("?")[0] in _POSTS else ("GET", "HEAD")
            if self.command not in allowed:
                self.send_response(405)
                self.send_header("Allow", ", ".join(allowed))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True

        def _refuse(self, status: int, message: str):
            self._reply(status, "application/json", json.dumps({"error": message}).encode("utf-8"))

        def _stale(self, exc):
            self._reply(409, "application/json", json.dumps({"error": str(exc), "rev": exc.rev}).encode("utf-8"))

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
                # Read what arrives of a body up to twice the limit first, while it keeps coming: closing with it
                # unread resets the connection, and the client may never see the answer.
                if limit < length <= 2 * limit:
                    try:
                        self.connection.settimeout(1.0)
                        while length > 0:
                            chunk = self.rfile.read1(min(length, 65536))
                            if not chunk:
                                break
                            length -= len(chunk)
                    except OSError:
                        pass
                self._refuse(413, "the body is empty or too large")
                return None
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._refuse(400, "the body is not valid JSON")
                return None

        def do_POST(self):
            target = self.path.partition("?")[0]
            handler = {_SKILLS_PATH: self._skills, _ROSTER_PATH: self._roster, _SKILLFILE_PATH: self._skillfile,
                       _TEAM_PATH: self._team, _OUTPUT_PATH: self._output}.get(target)
            if handler is not None:
                handler()
                return
            request = self._json_body("the structure", _FLOW_BODY_LIMIT)
            if request is None:
                return
            optional = {"text", "name", "command"}
            shapes = {"text": {"op", "id", "text"}, "name": {"op", "id", "name"}, "command": {"op", "id", "on"},
                      "prompt": {"op", "id", "goal"}, "remove": {"op", "id"}}
            op = request.get("op") if isinstance(request, dict) else None
            valid = isinstance(op, str) and (
                (op == "add" and {"op", "path"} <= set(request) <= {"op", "path"} | optional)
                or (op in shapes and shapes[op] <= set(request) <= shapes[op] | {"expect"}))
            if valid:
                kinds = {"id": str, "text": str, "name": str, "goal": str, "path": list, "on": bool, "command": bool,
                         "expect": str}
                valid = all(isinstance(request[key], kinds[key]) for key in request if key in kinds) and _expect_ok(request)
            if not valid:
                self._refuse(400, 'expected {"op": "add", "path": [agents]} (and optional "text", "name", "command"), '
                                  '{"op": "text", "id": flow, "text": text}, {"op": "name", "id": flow, "name": name}, '
                                  '{"op": "command", "id": flow, "on": true|false}, {"op": "prompt", "id": flow, '
                                  '"goal": text} or {"op": "remove", "id": flow}; each but "add" with an optional "expect": rev')
                return
            if demo and op != "prompt":
                self._refuse(403, "the demo cannot change the structure")
                return
            expect = request.get("expect")
            try:
                answer: dict = {}
                if op == "prompt":
                    answer["text"] = structure.flow_prompt(request["id"], request["goal"], structure_dir, expect=expect)
                elif op == "add":
                    answer["flow"], notes = structure.add_flow(
                        request["path"], request.get("text"), structure_dir, name=request.get("name") or None,
                        command=request.get("command", False), commands_dir=commands_dir)
                    if notes:
                        answer["notes"] = notes
                elif op == "text":
                    answer["flow"] = structure.set_flow_text(request["id"], request["text"], structure_dir,
                                                             commands_dir=commands_dir, expect=expect)
                elif op == "name":
                    answer["flow"] = structure.set_flow_name(request["id"], request["name"], structure_dir,
                                                             commands_dir=commands_dir, expect=expect)
                elif op == "command":
                    answer["flow"] = flow = structure.set_flow_command(request["id"], request["on"], structure_dir,
                                                                       commands_dir=commands_dir, expect=expect)
                    answer["applies"] = flowcommands.applies(flow["name"], request["on"])
                    answer["live"] = flowcommands.LIVE
                else:
                    structure.remove_flow(request["id"], structure_dir, commands_dir=commands_dir, expect=expect)
                if op != "prompt":
                    answer["flows"] = structure.drawn_flows(structure_dir)
            except structure.StaleError as exc:
                self._stale(exc)
                return
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
            keys = set(request) - {"expect"} if isinstance(request, dict) else None
            valid = (keys == {"agent", "skill", "on"} and isinstance(request["on"], bool)
                     and isinstance(request["skill"], str)) or (keys == {"agent", "all"} and request["all"] is True)
            if not valid or not isinstance(request["agent"], str) or not _expect_ok(request):
                self._refuse(400, 'expected {"agent": name, "skill": name, "on": true|false} or {"agent": name, "all": true}'
                                  ', each with an optional "expect": rev')
                return
            if demo:
                self._refuse(403, "the demo cannot change skills")
                return
            try:
                where = {**paths, "agents_dir": agents_dir or skillset.AGENTS_DIR, "skills_dir": skills_dir,
                         "structure": structure_root}
                expect = request.get("expect")
                if "all" in request:
                    result = skillset.set_all(request["agent"], expect=expect, **where)
                else:
                    result = (skillset.add if request["on"] else skillset.remove)(request["agent"], request["skill"],
                                                                                  expect=expect, **where)
                current = roster.agent_rev(request["agent"], config=paths["config"], map_path=paths["map_path"],
                                           agents_dir=where["agents_dir"], structure=structure_root)
            except agentfiles.StaleError as exc:
                self._stale(exc)
                return
            except skillset.SkillsetError as exc:
                self._refuse(400, str(exc))
                return
            except OSError as exc:
                self._refuse(500, f"could not change the skills: {exc}")
                return
            answer = {"agent": result["agent"], "skills": result["skills"], "warnings": result.get("warnings", []),
                      "changed": bool(result.get("changed")), "message": result.get("message", ""), "rev": current}
            self._reply(200, "application/json", json.dumps(answer).encode("utf-8"))

        def _request(self, what: str, limit: int, shapes: dict, expected: str):
            """The request when its body passes `_json_body` and its keys and types fit one of `shapes`
            ({op: (required keys, allowed keys, {key: types}[, EXPECT])}; with EXPECT the op also takes an optional
            "expect", text), else None after refusing it."""
            request = self._json_body(what, limit)
            if request is None:
                return None
            op = request.get("op") if isinstance(request, dict) else None
            shape = shapes.get(op) if isinstance(op, str) else None
            if (shape is None or not shape[0] <= set(request) <= shape[1] | ({"expect"} if shape[3:] else set())
                    or not all(isinstance(request[key], shape[2].get(key, str)) for key in request if key != "op")
                    or not _expect_ok(request)):
                self._refuse(400, "expected " + expected)
                return None
            if demo:
                self._refuse(403, f"the demo cannot change {what}")
                return None
            return request

        def _answer(self, call, *errors):
            """Run `call`; a stale write is a 409 with the current rev, a refusal (one of `errors`) a 400 with its
            message, a file error a 500."""
            try:
                answer = call()
            except _STALE as exc:
                self._stale(exc)
                return
            except errors as exc:
                self._refuse(400, str(exc))
                return
            except OSError as exc:
                self._refuse(500, f"could not make the change: {exc}")
                return
            self._reply(200, "application/json", json.dumps(answer).encode("utf-8"))

        def _roster(self):
            # Add an agent (an agent file, live at once; from a template, a copy or the fields given), read or
            # replace an added agent's instruction, rename an added agent with its flows, groups and path commands,
            # remove an agent with its flows and groups, or restore what a removal, a rename (or an instruction
            # change) took.
            names = lambda value: isinstance(value, list) and all(isinstance(item, str) for item in value)
            optional = (str, type(None))
            add_keys = {"op", "name", "model", "variant", "description", "prompt", "skills", "read_only", "copy_of",
                        "template"}
            types = {"model": optional, "variant": optional, "description": optional, "prompt": optional,
                     "copy_of": optional, "template": optional, "skills": (list, type(None)),
                     "read_only": (bool, type(None))}
            request = self._request("the agents", _ROSTER_BODY_LIMIT, {
                "add": ({"op", "name"}, add_keys, types), "remove": ({"op", "name"}, {"op", "name"}, {}, EXPECT),
                "restore": ({"op", "record"}, {"op", "record"}, {}), "read": ({"op", "name"}, {"op", "name"}, {}),
                "text": ({"op", "name", "text"}, {"op", "name", "text"}, {}, EXPECT),
                "rename": ({"op", "name", "to"}, {"op", "name", "to"}, {}, EXPECT)},
                '{"op": "add", "name", "model", "description", "prompt"} (or "copy_of" or "template"; optional '
                '"variant", "skills", "read_only"), {"op": "read"|"remove", "name"}, {"op": "text", "name", "text"}, '
                '{"op": "rename", "name", "to"} or {"op": "restore", "record"}; remove, text and rename with an '
                'optional "expect": rev')
            if request is None:
                return
            if request.get("skills") is not None and not names(request["skills"]):
                self._refuse(400, "skills must be a list of names")
                return
            folder = agents_dir or roster.AGENTS_DIR
            files = {"config": paths["config"], "map_path": paths["map_path"], "backups": paths["backups"],
                     "structure": structure_dir, "agents_dir": folder}
            expect = request.get("expect")

            def run():
                op = request["op"]
                if op == "add":
                    result = roster.add_agent(
                        request["name"], model=request.get("model"), variant=request.get("variant"),
                        description=request.get("description"), prompt=request.get("prompt"),
                        skills=request.get("skills"), read_only=request.get("read_only"),
                        copy_of=request.get("copy_of"), template=request.get("template"), skills_dir=skills_dir,
                        **files)
                elif op == "remove":
                    result = roster.remove_agent(request["name"], expect=expect, **files)
                elif op == "rename":
                    result = roster.rename_agent(request["name"], request["to"], commands_dir=commands_dir, expect=expect,
                                                 **files)
                elif op == "restore":
                    result = roster.restore_agent(request["record"], commands_dir=commands_dir, **files)
                elif op == "read":
                    return roster.read_agent(request["name"], agents_dir=folder, config=paths["config"],
                                             map_path=paths["map_path"], structure=structure_root)
                else:
                    result = roster.text_agent(request["name"], request["text"], agents_dir=folder,
                                               backups=paths["backups"], expect=expect, config=paths["config"],
                                               map_path=paths["map_path"], structure=structure_root)
                keys = ("agent", "kind", "action", "presets", "changed", "copy_of", "template", "model", "skills",
                        "permission", "structure_file", "record", "removed", "restored", "renamed", "name", "description",
                        "text", "warnings", "message", "rev")
                return {key: result[key] for key in keys if key in result}

            self._answer(run, skillset.SkillsetError, structure.StructureError)

        def _skillfile(self):
            # Read a skill's text, write it, create a skill (and give it to an agent), delete it or restore it.
            request = self._request("skills", _SKILLFILE_BODY_LIMIT, {
                "read": ({"op", "id"}, {"op", "id"}, {}),
                "write": ({"op", "id", "text"}, {"op", "id", "text", "description"}, {}, EXPECT),
                "create": ({"op", "id", "description", "text"}, {"op", "id", "description", "text", "assign"}, {}),
                "delete": ({"op", "id"}, {"op", "id"}, {}, EXPECT), "restore": ({"op", "record"}, {"op", "record"}, {})},
                '{"op": "read"|"delete", "id"}, {"op": "write", "id", "text"} (optional "description"), '
                '{"op": "create", "id", "description", "text"} (optional "assign") or {"op": "restore", "record"}; '
                'write and delete with an optional "expect": rev')
            if request is None:
                return
            where = {"skills": skills_dir or skillfiles.SKILLS, "backups": paths["backups"]}

            def run():
                op = request["op"]
                if op == "read":
                    return skillfiles.show(request["id"], skills=where["skills"], bundled=skills_bundled)
                if op == "write":
                    done = skillfiles.write(request["id"], body=request["text"], description=request.get("description"),
                                            expect=request.get("expect"), **where)
                    return {"id": done["id"], "description": done["frontmatter"]["description"], "text": done["body"],
                            "changed": done["changed"],
                            **{key: done[key] for key in ("record", "message", "rev") if key in done}}
                if op == "create":
                    done = skillfiles.create_skill(request["id"], request["description"], request["text"],
                                                   request.get("assign"), config=paths["config"],
                                                   map_path=paths["map_path"], profiles=paths["profiles"],
                                                   agents_dir=agents_dir or skillset.AGENTS_DIR, **where)
                    return {"id": done["id"], "description": done["frontmatter"]["description"], "text": done["body"],
                            "changed": True,
                            **{key: done[key] for key in ("assigned", "warnings", "message") if key in done}}
                if op == "delete":
                    return skillfiles.delete(request["id"], config=paths["config"], wildcards_hold=False,
                                             agents_dir=agents_dir or skillset.AGENTS_DIR, expect=request.get("expect"),
                                             **where)
                done = skillfiles.restore(request["record"], **where)
                return {"id": done["id"], "changed": True, **({"record": done["record"]} if "frontmatter" in done else {})}

            self._answer(run, skillset.SkillsetError)

        def _team(self):
            # Add a group (agents that report to one lead), replace its members or instruction, or remove it.
            names = (list,)
            request = self._request("the structure", _TEAM_BODY_LIMIT, {
                "add": ({"op", "lead", "members"}, {"op", "lead", "members", "text"}, {"members": names}),
                "members": ({"op", "id", "members"}, {"op", "id", "members"}, {"members": names}, EXPECT),
                "text": ({"op", "id", "text"}, {"op", "id", "text"}, {}, EXPECT),
                "remove": ({"op", "id"}, {"op", "id"}, {}, EXPECT)},
                '{"op": "add", "lead", "members": [agents]} (and an optional "text"), {"op": "members", "id", '
                '"members": [agents]}, {"op": "text", "id", "text"} or {"op": "remove", "id"}; each but "add" with '
                'an optional "expect": rev')
            if request is None:
                return

            def run():
                answer, notes = {}, []
                if request["op"] == "add":
                    answer["team"], notes = teams.add(request["lead"], request["members"], request.get("text"),
                                                      structure_dir)
                elif request["op"] == "members":
                    answer["team"], notes = teams.set_members(request["id"], request["members"], structure_dir,
                                                              expect=request.get("expect"))
                elif request["op"] == "text":
                    answer["team"] = teams.set_text(request["id"], request["text"], structure_dir,
                                                    expect=request.get("expect"))
                else:
                    answer["team"] = teams.remove(request["id"], structure_dir, expect=request.get("expect"))
                answer["teams"] = teams.teams(structure_dir)
                if notes:
                    answer["notes"] = notes
                return answer

            self._answer(run, structure.StructureError)

        def _output(self):
            # Read a step's output, save a new text as its next revision (its readers become stale; undoable), or
            # undo that from its record (agentgraph.outputs).
            request = self._request("outputs", _OUTPUT_BODY_LIMIT, {
                "read": ({"op", "task", "node", "artifact"}, {"op", "task", "node", "artifact"}, {}),
                "write": ({"op", "task", "node", "artifact", "text"}, {"op", "task", "node", "artifact", "text"}, {},
                          EXPECT),
                "restore": ({"op", "record"}, {"op", "record"}, {})},
                '{"op": "read", "task", "node", "artifact"}, {"op": "write", "task", "node", "artifact", "text"} '
                '(with an optional "expect": rev) or {"op": "restore", "record"}')
            if request is None:
                return
            home = outputs.edit_home(root=ledger_root, config_path=paths["config"])  # the edit log's memory home

            def run():
                # The graph folders the server shows; an undo is taken back only for a graph in one of them.
                dirs = [graph_dir] if graph_dir is not None else [
                    folder for folder in (find_graph_dir(Path(cwd) if cwd else Path.cwd()), ORCHESTRATION_GRAPH_DIR)
                    if folder is not None]
                if request["op"] == "restore":
                    return outputs.restore_output(request["record"], backups=paths["backups"], home=home, dirs=dirs)
                if request["op"] == "read":
                    return outputs.read_output(request["task"], request["node"], request["artifact"], dirs=dirs)
                return outputs.write_output(request["task"], request["node"], request["artifact"], request["text"],
                                            dirs=dirs, home=home, backups=paths["backups"],
                                            expect=request.get("expect"))

            self._answer(run, outputs.OutputError)

        def _reply(self, status: int, content_type: str, body: bytes):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _live_snapshot(self):
            # The roster is read from the same preset file and agents folder the endpoints write.
            where = {**({"roster_config": skills_config} if skills_config is not None else {}),
                     **({"agents_dir": agents_dir} if agents_dir is not None else {}),
                     **({"config_dir": opencode_dir} if opencode_dir is not None else {}),
                     **({"db_path": sessions_db} if sessions_db is not None else {})}
            return build_snapshot(graph_dir=graph_dir, root_id=session, cwd=cwd,
                                  session_graph_dir=None if graph_dir else ORCHESTRATION_GRAPH_DIR, **where)

        def _snapshot(self, query: str):
            params = parse_qs(query, keep_blank_values=True)
            use_demo = demo or params.get("demo", [None])[0] == "1"
            task = params.get("task", [None])[0]
            try:
                if use_demo:
                    now = time.time()
                    snapshot, activity = demo_snapshot(now), demo_activity(now)
                else:
                    snapshot = self._live_snapshot()
                    activity = live_activity(snapshot, sessions_db)
                document = snapshot_document(snapshot, "demo" if use_demo else "live", task,
                                             session=None if use_demo else session,
                                             structure=None if use_demo else load_structure(structure_dir),
                                             activity=activity)
                if not use_demo:
                    document["learning"] = learning_document(ledger_root, config_path=paths["config"])
                    flowrun.annotate(document, structure_dir=structure_dir,
                                     ledger=ledger_home(ledger_root, config_path=paths["config"]),
                                     graph_paths=[graph.path for graph in snapshot.graphs],
                                     db_path=sessions_db if sessions_db is not None else DB_PATH)
                    document["skills"] = skills_document(skills_dir=skills_dir)
                    skillset.annotate(document, config=paths["config"], profiles=paths["profiles"],
                                      agents_dir=agents_dir or roster.AGENTS_DIR, skills_dir=skills_dir)
                    mcpview.annotate(document, config=paths["config"],
                                     opencode=Path(opencode_dir) / "opencode.json" if opencode_dir is not None else None)
                    roster.annotate(document, config=paths["config"], map_path=paths["map_path"],
                                    agents_dir=agents_dir or roster.AGENTS_DIR, structure=structure_root)
                    skillfiles.annotate(document, skills=skills_dir or skillfiles.SKILLS, bundled=skills_bundled)
                    outputs.annotate(document)  # each output of the shown graph: rev, size, editable, why
                body = json.dumps(document, allow_nan=False).encode("utf-8")
            except Exception as exc:
                body = json.dumps({"version": 1, "error": str(exc)}).encode("utf-8")
                self._reply(500, "application/json", body)
            else:
                self._reply(200, "application/json", body)

        def _chat(self, query: str):
            """GET /api/chat?session=ID[&limit=N][&demo=1]: the chat of one session of the tree the snapshot
            shows (its root and the sessions listed under its agents); any other id is 404, without detail."""
            params = parse_qs(query, keep_blank_values=True)
            ids, limits = params.get("session", []), params.get("limit", [])
            if len(ids) != 1 or not chatfeed.valid_session(ids[0]) or len(limits) > 1 or (
                    limits and not (limits[0].isdigit() and 1 <= int(limits[0]) <= chatfeed.MAX_LIMIT)):
                self._refuse(400, "give one session id (ses_...) and, optionally, a limit from 1 to 200")
                return
            wanted, limit = ids[0], int(limits[0]) if limits else chatfeed.DEFAULT_LIMIT
            use_demo = demo or params.get("demo", [None])[0] == "1"
            try:
                if use_demo:
                    feed = chatfeed.demo_feed(wanted)
                else:
                    snapshot = self._live_snapshot()
                    document = snapshot_document(snapshot, "live", None, session=session,
                                                 structure=load_structure(structure_dir))
                    tree = {document["root"]["id"]} if document["root"] else set()
                    tree.update(item["id"] for agent in document["agents"] for item in agent["sessions"])
                    feed = chatfeed.read_chat(wanted, limit=limit,
                                              db_path=sessions_db if sessions_db is not None else DB_PATH) \
                        if wanted in tree else None
            except Exception:
                self._refuse(500, "the chat could not be read")
                return
            if feed is None:
                self.send_error(404)
                return
            self._reply(200, "application/json", json.dumps(feed, allow_nan=False).encode("utf-8"))

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
            if path == _CHAT_PATH:
                self._chat(query)
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
