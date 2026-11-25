"""Renaming an added agent (`roster.rename_agent`, `agentgraph agent rename`, `POST /api/roster` op "rename"): one
undoable step over the agent file, its structure file, its flows and groups and their path commands."""
import contextlib
import http.client
import io
import json
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import agentfiles, flowcommands, roster, server, structure, teams
from tests.test_agentfiles import Base, tree


class Rename(Base):
    def setUp(self):
        super().setUp()
        self.commands = self.root / "commands"
        self.commands.mkdir()
        (self.commands / "mine.md").write_text("---\ndescription: mine\n---\nHand-written.\n", encoding="utf-8")
        self.add(template="verifier")  # checker

    def full(self):
        return self.snapshot(), tree(self.commands)

    def rename(self, name="checker", to="auditor", **kwargs):
        return roster.rename_agent(name, to, commands_dir=self.commands, **(self.paths | kwargs))

    def restore(self, record):
        return roster.restore_agent(record, commands_dir=self.commands, **self.paths)

    def tie_up(self):
        """checker on a generated flow with its command on, a flow with the user's text, a group it leads and a
        place in another group."""
        flow = structure.add_flow(["impl-openai", "checker", "impl-openai"], None, self.structure, name="check-loop",
                                  command=True, commands_dir=self.commands)[0]
        mine = structure.add_flow(["checker", "reviewer"], "checker hands it to reviewer, my words.", self.structure)[0]
        led = teams.add("checker", ["impl-claude"], structure_dir=self.structure)[0]
        member = teams.add("reviewer", ["checker", "impl-openai"], structure_dir=self.structure)[0]
        return flow, mine, led, member

    def test_rename_moves_everything_and_restore_gives_it_back_byte_for_byte(self):
        flow, mine, led, member = self.tie_up()
        before = self.full()
        agent_bytes = (self.agents / "checker.md").read_bytes()
        result = self.rename()
        self.assertEqual(result["action"], "renamed")
        self.assertEqual(result["message"], agentfiles.LIVE)
        self.assertEqual(result["renamed"]["flows"], sorted([flow["id"], mine["id"]]))
        self.assertEqual((result["renamed"]["teams"], result["renamed"]["memberships"]), ([led["id"]], [member["id"]]))
        self.assertEqual(result["renamed"]["commands"], ["check-loop"])
        self.assertFalse((self.agents / "checker.md").exists())
        self.assertEqual((self.agents / "auditor.md").read_bytes(), agent_bytes)
        flows = {item["id"]: item for item in structure.drawn_flows(self.structure)}
        self.assertEqual(flows[flow["id"]]["path"], ["impl-openai", "auditor", "impl-openai"])
        self.assertEqual(flows[flow["id"]]["text"], structure.flow_text(["impl-openai", "auditor", "impl-openai"],
                                                                        frozenset({"orchestrator"})))
        self.assertEqual((flows[mine["id"]]["path"], flows[mine["id"]]["text"]),
                         (["auditor", "reviewer"], "checker hands it to reviewer, my words."))
        groups = {item["id"]: item for item in teams.teams(self.structure)}
        self.assertEqual(groups[led["id"]]["lead"], "auditor")
        self.assertIn("auditor", groups[member["id"]]["members"])
        command = (self.commands / "check-loop.md").read_text(encoding="utf-8")
        self.assertIn("impl-openai -> auditor -> impl-openai", command)
        self.assertNotIn("checker", command.replace(f"(flow {flow['id']})", ""))  # a flow keeps its id (file name)
        self.assertIn(flow["id"], flows)
        own = lambda stem: [p for p in self.structure.rglob(stem + ".md") if p.parent.name not in ("flows", "teams")]
        self.assertEqual(own("checker"), [])  # a group keeps its id (file name), like a flow
        self.assertEqual(len(own("auditor")), 1)
        self.assertIn(led["id"], groups)
        self.restore(result["record"])
        self.assertEqual(self.full(), before)

    def test_an_agent_on_nothing_and_the_cli(self):
        before = self.full()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = roster.agent_main(["rename", "checker", "auditor", "--json", "--config", str(self.config), "--map",
                                      str(self.map), "--backups", str(self.backups), "--structure", str(self.structure),
                                      "--agents", str(self.agents), "--commands", str(self.commands)])
        answer = json.loads(out.getvalue())
        self.assertEqual((code, answer["agent"], answer["renamed"]["commands"]), (0, "auditor", []))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = roster.agent_main(["restore", answer["record"], "--config", str(self.config), "--map", str(self.map),
                                      "--backups", str(self.backups), "--structure", str(self.structure), "--agents",
                                      str(self.agents), "--commands", str(self.commands)])
        self.assertEqual(code, 0)
        self.assertIn("checker: restored the name", out.getvalue())
        self.assertEqual(self.full(), before)

    def test_refused_sources(self):
        (self.agents / "mine.md").write_text("---\ndescription: mine\nmodel: a/b\n---\nBody\n", encoding="utf-8")
        before = self.full()
        for name, why in (("orchestrator", "cannot be renamed"), ("council", "cannot be renamed"),
                          ("councillor", "cannot be renamed"), ("councillor-a", "cannot be renamed"),
                          ("explorer", "only an added agent can be renamed"),
                          ("impl-openai", "only an added agent can be renamed"),
                          ("mine", "written by hand"), ("ghost", "no added agent"), ("-x", "invalid")):
            with self.subTest(name=name), self.assertRaisesRegex(roster.SkillsetError, why):
                self.rename(name, "fresh-name")
            self.assertEqual(self.full(), before)

    def test_refused_new_names(self):
        self.add("other", template="verifier")
        (self.agents / "held.md.disabled").write_text("off\n", encoding="utf-8")
        before = self.full()
        for to, why in (("checker", "already has that name"), ("Bad", "invalid agent name"), ("-x", "invalid agent name"),
                        ("a" * 65, "invalid agent name"), ("councillor-x", "council seats"), ("orchestrator", "built-in"),
                        ("explorer", "built-in"), ("impl-claude", "already exists in preset"), ("user", "reserved"),
                        ("other", "already an agent file"), ("held", "already an agent file"), (3, "text")):
            with self.subTest(to=to), self.assertRaisesRegex(roster.SkillsetError, why):
                self.rename("checker", to)
            self.assertEqual(self.full(), before)

    def test_a_name_the_structure_already_uses_is_refused(self):
        group = sorted(p for p in self.structure.iterdir() if p.is_dir() and p.name not in ("flows", "teams"))[-1]
        (group / "auditor.md").write_text("A stale file.\n", encoding="utf-8")
        before = self.full()
        with self.assertRaisesRegex(roster.RosterError, "already has a file in the structure folder"):
            self.rename()
        self.assertEqual(self.full(), before)

    def test_a_hand_changed_command_file_refuses_the_rename(self):
        self.tie_up()
        path = self.commands / "check-loop.md"
        path.write_bytes(path.read_bytes() + b"by hand\n")
        before = self.full()
        with self.assertRaisesRegex(roster.RosterError, "changed by hand"):
            self.rename()
        self.assertEqual(self.full(), before)

    def test_without_a_path_command_the_commands_folder_is_never_opened(self):
        structure.add_flow(["checker", "reviewer"], None, self.structure)
        with patch.object(flowcommands, "Folder", side_effect=AssertionError("opened the commands folder")):
            roster.rename_agent("checker", "auditor", **self.paths)

    def test_a_failure_part_way_puts_everything_back_and_keeps_no_record(self):
        self.tie_up()
        before = self.full()
        real = agentfiles.replace

        def failing(name, before_, after, **kwargs):
            if name == "checker" and after is None:
                raise OSError("disk full")
            return real(name, before_, after, **kwargs)

        with patch.object(agentfiles, "replace", failing), self.assertRaisesRegex(OSError, "disk full"):
            self.rename()
        self.assertEqual(self.full(), before)
        self.assertEqual(list(self.backups.rglob("rename-*")), [])

    def test_restore_is_refused_when_anything_changed_since(self):
        flow, *_ = self.tie_up()
        result = self.rename()
        structure.set_flow_text(flow["id"], "Changed since.", self.structure, commands_dir=self.commands)
        changed = self.full()
        with self.assertRaisesRegex(roster.RosterError, "changed or added since"):
            self.restore(result["record"])
        self.assertEqual(self.full(), changed)
        structure.set_flow_text(flow["id"], " ", self.structure, commands_dir=self.commands)
        (self.agents / "auditor.md").write_bytes((self.agents / "auditor.md").read_bytes() + b"x\n")
        changed = self.full()
        with self.assertRaisesRegex(roster.RosterError, "changed or removed since the rename"):
            self.restore(result["record"])
        self.assertEqual(self.full(), changed)

    def test_restore_is_refused_when_the_old_name_was_taken_since(self):
        result = self.rename()
        obj = json.loads(self.config.read_text(encoding="utf-8"))
        preset = next(iter(obj["presets"]))
        obj["presets"][preset]["checker"] = {"model": "a/b"}
        self.config.write_text(json.dumps(obj), encoding="utf-8")
        changed = self.full()
        with self.assertRaisesRegex(roster.RosterError, "old name is taken.*already exists in preset"):
            self.restore(result["record"])
        self.assertEqual(self.full(), changed)

    def test_a_failure_part_way_through_restore_puts_the_rename_back(self):
        self.tie_up()
        result = self.rename()
        renamed = self.full()
        real = agentfiles.replace

        def failing(name, before_, after, **kwargs):
            if name == "auditor" and after is None:
                raise OSError("disk full")
            return real(name, before_, after, **kwargs)

        with patch.object(agentfiles, "replace", failing), self.assertRaisesRegex(OSError, "disk full"):
            self.restore(result["record"])
        self.assertEqual(self.full(), renamed)
        self.restore(result["record"])

    def test_a_tampered_record_is_refused(self):
        self.tie_up()
        result = self.rename()
        record = Path(result["record"])
        data = json.loads(record.read_text(encoding="utf-8"))
        before = self.full()
        for change in (lambda d: d.update(to="impl-claude"), lambda d: d["structure"].append(
                {"folder": "flows", "name": "x.md", "before": None, "after": None}),
                lambda d: d["commands"][0].update(after=agentfiles.b64(b"not ours")),
                lambda d: d.update(extra=1)):
            tampered = json.loads(json.dumps(data))
            change(tampered)
            record.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(roster.RosterError, "invalid undo record"):
                self.restore(str(record))
            self.assertEqual(self.full(), before)


class RenameEndpoint(Rename):
    def serve(self, **extra):
        httpd = server.make_server(
            0, graph_dir=self.root / "graphs", cwd=self.root, session="root", web_dir=self.root,
            structure_dir=self.structure, skills_config=self.config, skills_map=self.map, skills_backups=self.backups,
            skills_dir=self.skills, skills_bundled=[], agents_dir=self.agents, commands_dir=self.commands, **extra)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        return httpd

    def call(self, httpd, body, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
        try:
            sent = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{httpd.server_port}", **(headers or {})}
            conn.request("POST", "/api/roster", body=json.dumps(body).encode(),
                         headers={k: v for k, v in sent.items() if v is not None})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_rename_and_restore_over_the_endpoint(self):
        self.tie_up()
        before = self.full()
        httpd = self.serve()
        status, body = self.call(httpd, {"op": "rename", "name": "checker", "to": "auditor"})
        self.assertEqual((status, body["agent"], body["renamed"]["commands"]), (200, "auditor", ["check-loop"]))
        self.assertIn("auditor", (self.commands / "check-loop.md").read_text(encoding="utf-8"))
        status, _ = self.call(httpd, {"op": "restore", "record": body["record"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.full(), before)

    def test_bad_shapes_refusals_protections_and_the_demo(self):
        httpd = self.serve()
        before = self.full()
        for body, expected in (({"op": "rename", "name": "checker"}, 400), ({"op": "rename", "name": "checker", "to": 3}, 400),
                               ({"op": "rename", "name": "checker", "to": "a", "x": 1}, 400),
                               ({"op": "rename", "name": "orchestrator", "to": "fresh"}, 400),
                               ({"op": "rename", "name": "checker", "to": "Bad"}, 400)):
            with self.subTest(body=body):
                status, reply = self.call(httpd, body)
                self.assertEqual(status, expected)
                self.assertIn("error", reply)
        self.assertEqual(self.call(httpd, {"op": "rename", "name": "checker", "to": "auditor"},
                                   {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call(httpd, {"op": "rename", "name": "checker", "to": "auditor"},
                                   {"Content-Type": "text/plain"})[0], 415)
        demo = self.serve(demo=True)
        self.assertEqual(self.call(demo, {"op": "rename", "name": "checker", "to": "auditor"})[0], 403)
        self.assertEqual(self.full(), before)


if __name__ == "__main__":
    unittest.main()
