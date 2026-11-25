"""POST /api/skills, the snapshot's skill fields and `bin/agentgraph skills`, on disposable copies only."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests.launch import LAUNCHER
from agentgraph import server, skillset
from agentgraph.snapshot_json import skills_document


SCRATCH = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode/skills-endpoint")
ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = [entry["name"] for entry in skills_document()]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


REAL = {path: digest(path) for path in (skillset.CONFIG, skillset.MAP)}


class Copies(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / skillset.CONFIG.name
        self.map = self.root / "SKILLS.md"
        self.backups = self.root / "backups"
        self.profiles = self.root / "task-profiles.md"
        for source, target in ((skillset.CONFIG, self.config), (skillset.MAP, self.map),
                               (skillset.PROFILES, self.profiles)):
            shutil.copyfile(source, target)
        self.original = self.files()
        self.addCleanup(self.assert_real_files_untouched)

    def assert_real_files_untouched(self):
        self.assertEqual({path: digest(path) for path in REAL}, REAL, "a real file was changed")

    def files(self):
        return self.config.read_bytes(), self.map.read_bytes()

    def active(self, agent):
        data = json.loads(self.config.read_bytes())
        return data["presets"][data["preset"]][agent].get("skills", [])

    def lacking(self, agent):
        have = self.active(agent)
        return next(name for name in CATALOGUE if name not in have and "*" not in have)


class Endpoint(Copies):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(server, "build_snapshot", side_effect=AssertionError("unexpected live read")))
        self.httpd = self.serve()
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def serve(self, **extra):
        httpd = server.make_server(0, graph_dir=self.root / "graphs", cwd=self.root, session="root", web_dir=self.root,
                                   structure_dir=self.root / "structure", skills_config=self.config, skills_map=self.map,
                                   skills_backups=self.backups, skills_profiles=self.profiles, **extra)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        return httpd

    def post(self, body, headers=None, method="POST", port=None):
        port = port or self.httpd.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            sent = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", **(headers or {})}
            conn.request(method, "/api/skills", body=data if method == "POST" else None,
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

    def test_add_then_remove_round_trips(self):
        skill = self.lacking("impl-openai")
        status, body, _ = self.post({"agent": "impl-openai", "skill": skill, "on": True})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["agent"], body["changed"], body["message"]), ("impl-openai", True, skillset.RESTART))
        self.assertIn(skill, body["skills"])
        self.assertEqual(body["skills"], self.active("impl-openai"))
        self.assertTrue(self.backups.is_dir(), "no backup was written")
        status, body, _ = self.post({"agent": "impl-openai", "skill": skill, "on": False})
        self.assertEqual(status, 200, body)
        self.assertNotIn(skill, body["skills"])
        self.assertEqual(self.files(), self.original)

    def test_remove_warns_when_a_task_profile_still_names_the_skill(self):
        self.assertIn("code-assurance", self.active("impl-openai"))
        status, body, _ = self.post({"agent": "impl-openai", "skill": "code-assurance", "on": False})
        self.assertEqual(status, 200, body)
        self.assertNotIn("code-assurance", body["skills"])
        self.assertEqual(len(body["warnings"]), 1)
        self.assertIn("impl-openai profile still names code-assurance", body["warnings"][0])
        status, body, _ = self.post({"agent": "impl-openai", "skill": "code-assurance", "on": True})
        self.assertEqual((status, body["warnings"]), (200, []))
        self.assertIn("code-assurance", self.active("impl-openai"))  # added back at the end of the list

    def test_all_and_removing_one_from_all(self):
        status, body, _ = self.post({"agent": "impl-openai", "all": True})
        self.assertEqual((status, body["skills"]), (200, ["*"]))
        status, body, _ = self.post({"agent": "impl-openai", "skill": CATALOGUE[0], "on": False})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["skills"], CATALOGUE[1:])
        status, body, _ = self.post({"agent": "impl-openai", "all": True})
        self.assertEqual(body["skills"], ["*"])

    def test_refused_requests_change_nothing(self):
        for request, message in (({"agent": "ghost", "skill": CATALOGUE[0], "on": True}, "not in any selected preset"),
                                 ({"agent": "impl-openai", "skill": "no-such-skill", "on": True}, "not installed"),
                                 ({"agent": "impl-openai", "skill": "*", "on": True}, "not installed"),
                                 ({"agent": "councillor-fable", "skill": CATALOGUE[0], "on": True}, "council's own table"),
                                 ({"agent": "councillor-fable", "all": True}, "council's own table"),
                                 ({"agent": "build", "all": True}, "not in any selected preset"),
                                 ({"agent": "-x", "all": True}, "no leading '-'"),
                                 ({"agent": "impl-openai", "skill": "-x", "on": False}, "no leading '-'")):
            with self.subTest(request=request):
                status, body, _ = self.post(request)
                self.assertEqual(status, 400)
                self.assertIn(message, body["error"])
        for request in ({}, {"agent": "impl-openai"}, {"agent": "impl-openai", "all": False}, {"agent": "impl-openai", "all": 1},
                        {"agent": "impl-openai", "skill": CATALOGUE[0]}, {"agent": "impl-openai", "skill": CATALOGUE[0], "on": "yes"},
                        {"agent": "impl-openai", "skill": CATALOGUE[0], "on": True, "extra": 1}, {"agent": 3, "all": True},
                        ["impl-openai"], "impl-openai"):
            with self.subTest(request=request):
                status, body, _ = self.post(request)
                self.assertEqual(status, 400)
                self.assertIn("expected", body["error"])
        self.assertEqual(self.files(), self.original)
        self.assertFalse(self.backups.exists())

    def test_protections(self):
        good = {"agent": "impl-openai", "all": True}
        with patch.object(server, "_LOOPBACK", frozenset()):
            status, body, _ = self.post(good)
        self.assertEqual(status, 403)
        self.assertIn("local", body["error"])
        port = self.httpd.server_port
        for origin in ("http://evil.example", f"http://localhost:{port}", "null"):
            with self.subTest(origin=origin):
                self.assertEqual(self.post(good, {"Origin": origin})[0], 403)
        self.assertEqual(self.post(good, {"Host": "evil.example"})[0], 403)
        for headers, body in (({"Content-Type": "text/plain"}, json.dumps(good).encode()), ({"Content-Type": None}, json.dumps(good).encode()),
                              ({}, b"not json"), ({}, b"\xff\xfe"), ({}, b"x" * 5000)):
            with self.subTest(headers=headers, body=body[:20]):
                status, reply, _ = self.post(body, headers)
                self.assertIn(status, (400, 413, 415))
                self.assertIn("error", reply)
        for method in ("GET", "PUT", "DELETE"):
            with self.subTest(method=method):
                status, _, response = self.post({}, method=method)
                self.assertEqual((status, response.getheader("Allow")), (405, "POST"))
        demo = self.serve(demo=True)
        status, body, _ = self.post(good, port=demo.server_port)
        self.assertEqual(status, 403)
        self.assertIn("demo", body["error"])
        self.assertEqual(self.files(), self.original)
        self.assertFalse(self.backups.exists())


class SnapshotFields(Copies):
    def test_editable_and_reasons(self):
        document = {"agents": [{"name": "impl-openai"}, {"name": "orchestrator"}, {"name": "councillor-fable"}, {"name": "build"}]}
        skillset.annotate(document, config=self.config, profiles=self.profiles)
        agents = {agent["name"]: agent for agent in document["agents"]}
        self.assertEqual((agents["impl-openai"]["skills_editable"], agents["impl-openai"]["skills_reason"]), (True, ""))
        self.assertTrue(agents["orchestrator"]["skills_editable"])
        self.assertEqual((agents["councillor-fable"]["skills_editable"], agents["councillor-fable"]["skills_reason"]),
                         (False, skillset.SEAT_REASON))
        self.assertEqual((agents["build"]["skills_editable"], agents["build"]["skills_reason"]), (False, skillset.OUTSIDE_REASON))
        self.assertEqual(document["skill_warnings"], [])
        skillset.remove("impl-openai", "code-assurance", config=self.config, map_path=self.map, backups=self.backups,
                        profiles=self.profiles)
        skillset.annotate(document, config=self.config, profiles=self.profiles)
        self.assertEqual(len(document["skill_warnings"]), 1)
        self.assertIn("impl-openai profile still names code-assurance", document["skill_warnings"][0])

    def test_an_unreadable_config_makes_nothing_editable_and_never_raises(self):
        document = {"agents": [{"name": "impl-openai"}]}
        skillset.annotate(document, config=self.root / "missing.json", profiles=self.profiles)
        self.assertEqual(document["agents"][0]["skills_editable"], False)
        self.assertIn("could not be read", document["agents"][0]["skills_reason"])
        self.assertEqual(document["skill_warnings"], [])


class Launcher(Copies):
    def test_bin_agentgraph_skills_runs_skillset(self):
        skill = self.lacking("impl-openai")
        options = ["--config", str(self.config), "--map", str(self.map), "--backups", str(self.backups)]
        run = lambda *args: subprocess.run([*LAUNCHER, "skills", *args, *options], cwd=self.root,
                                           capture_output=True, text=True, timeout=30, env={**os.environ})
        added = run("add", "impl-openai", skill, "--json")
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertIn(skill, json.loads(added.stdout)["skills"])
        shown = run("show", "impl-openai")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn(skill, shown.stdout)
        self.assertEqual(run("remove", "impl-openai", skill).returncode, 0)
        self.assertEqual(self.files(), self.original)
        refused = run("add", "councillor-fable", skill)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("council's own table", refused.stderr)


if __name__ == "__main__":
    unittest.main()
