"""POST /api/roster, /api/skillfile and /api/structure/team, the snapshot's read paths and the `agent`, `skill`,
`team` and `structure teams` commands, on disposable copies only. The real preset file, SKILLS.md, skills folder,
agents folder and structure folder are never written, and a server made on scratch paths never reads them."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agentgraph import agentfiles, roster, server, skillfiles, skillset, sources

SCRATCH = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode/editor-endpoints")
ROOT = Path(__file__).resolve().parent.parent
REAL_FILES = (skillset.CONFIG, skillset.MAP)
REAL_FOLDERS = (agentfiles.AGENTS_DIR, skillfiles.SKILLS, ROOT / "structure")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def listing(folder):
    folder = Path(folder)
    if not folder.exists():
        return None
    return sorted((str(p.relative_to(folder)), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file())


def tree(folder):
    folder = Path(folder)
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


FIRST = ({path: digest(path) for path in REAL_FILES}, {folder: listing(folder) for folder in REAL_FOLDERS})

# Which real files a request touched: an audit hook, on only while a test asks for it.
_WATCH = {"on": False, "seen": set()}
_REAL_ROOTS = tuple(str(path) for path in (*REAL_FILES, *REAL_FOLDERS, skillset.PROFILES,
                                           sources.CONFIG_DIR / "opencode.json", sources.MODELS_CACHE,
                                           ROOT / "memory"))  # the last also covers memory-<preset>


def _audit(event, args):
    if _WATCH["on"] and event in ("open", "os.listdir", "os.scandir") and args:
        target = args[0]
        if isinstance(target, (str, bytes, os.PathLike)):
            path = os.fsdecode(target)
            if path.startswith(_REAL_ROOTS):
                _WATCH["seen"].add(path)


sys.addaudithook(_audit)


def tearDownModule():
    files, folders = FIRST
    assert {path: digest(path) for path in REAL_FILES} == files, "a real file changed during the tests"
    assert {folder: listing(folder) for folder in REAL_FOLDERS} == folders, "a real folder changed during the tests"


class Scratch(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / skillset.CONFIG.name
        self.map = self.root / "SKILLS.md"
        self.profiles = self.root / "task-profiles.md"
        self.backups = self.root / "backups"
        self.agents = self.root / "agents"
        self.agents.mkdir()
        self.skills = self.root / "skills"
        for name in ("code-assurance", "engineering-delivery", "shopify-ruby"):
            (self.skills / name).mkdir(parents=True)
            (self.skills / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---\nBody\n")
        self.structure = self.root / "structure"
        shutil.copytree(ROOT / "structure", self.structure)
        for source, target in ((skillset.CONFIG, self.config), (skillset.MAP, self.map),
                               (skillset.PROFILES, self.profiles)):
            shutil.copyfile(source, target)
        self.start = self.state()

    def state(self):
        return (self.config.read_bytes(), self.map.read_bytes(), tree(self.agents), tree(self.skills),
                tree(self.structure))


class Served(Scratch):
    def setUp(self):
        super().setUp()
        self.httpd = self.serve()

    def serve(self, **extra):
        httpd = server.make_server(
            0, graph_dir=self.root / "graphs", cwd=self.root, session="root", web_dir=self.root,
            structure_dir=self.structure, skills_config=self.config, skills_map=self.map, skills_backups=self.backups,
            skills_profiles=self.profiles, skills_dir=self.skills, skills_bundled=[], agents_dir=self.agents, **extra)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        return httpd

    def call(self, path, body, headers=None, method="POST", port=None):
        port = port or self.httpd.server_port
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

    def ok(self, path, body):
        status, answer, _ = self.call(path, body)
        self.assertEqual(status, 200, answer)
        return answer

    def refused(self, path, body, message, status=400):
        before = self.state()
        code, answer, _ = self.call(path, body)
        self.assertEqual(code, status, answer)
        self.assertIn(message, answer["error"])
        self.assertNotIn("Traceback", answer["error"])
        self.assertEqual(self.state(), before, "a refused request wrote something")


class Protections(Served):
    """Each endpoint: loopback only, exact Host, matching Origin, JSON only, bounded body, POST only, refused by
    the demo server; nothing is written by any refused request."""
    ENDPOINTS = {
        "/api/roster": ({"op": "read", "name": "checker"}, server._ROSTER_BODY_LIMIT),
        "/api/skillfile": ({"op": "read", "id": "code-assurance"}, server._SKILLFILE_BODY_LIMIT),
        "/api/structure/team": ({"op": "remove", "id": "nobody"}, server._TEAM_BODY_LIMIT),
    }

    def check(self, path):
        good, limit = self.ENDPOINTS[path]
        with patch.object(server, "_LOOPBACK", frozenset()):
            status, body, _ = self.call(path, good)
        self.assertEqual(status, 403)
        self.assertIn("local", body["error"])
        port = self.httpd.server_port
        for origin in ("http://evil.example", f"http://localhost:{port}", "null"):
            with self.subTest(origin=origin):
                self.assertEqual(self.call(path, good, {"Origin": origin})[0], 403)
        self.assertEqual(self.call(path, good, {"Host": "evil.example"})[0], 403)
        for headers, data in (({"Content-Type": "text/plain"}, json.dumps(good).encode()),
                              ({"Content-Type": None}, json.dumps(good).encode()),
                              ({}, b"not json"), ({}, b"\xff\xfe"), ({"Content-Length": str(limit + 1)}, b"{}"),
                              ({}, b" " * min(limit + 1, 200_000)) if limit < 200_000 else ({}, b""),
                              ({}, b"[]"), ({}, b'"x"'), ({}, json.dumps({**good, "extra": 1}).encode()),
                              ({}, json.dumps({"op": "nope"}).encode())):
            with self.subTest(headers=headers, body=data[:20]):
                status, reply, _ = self.call(path, data, headers)
                self.assertIn(status, (400, 413, 415))
                self.assertIn("error", reply)
        for method in ("GET", "PUT", "DELETE"):
            with self.subTest(method=method):
                status, _, response = self.call(path, {}, method=method)
                self.assertEqual((status, response.getheader("Allow")), (405, "POST"))
        demo = self.serve(demo=True)
        status, body, _ = self.call(path, good, port=demo.server_port)
        self.assertEqual(status, 403)
        self.assertIn("demo", body["error"])
        self.assertEqual(self.state(), self.start)
        self.assertFalse(self.backups.exists())

    def test_roster_endpoint_protections(self):
        self.check("/api/roster")

    def test_skillfile_endpoint_protections(self):
        self.check("/api/skillfile")

    def test_team_endpoint_protections(self):
        self.check("/api/structure/team")


class RosterEndpoint(Served):
    def test_add_read_text_remove_restore_round_trip(self):
        added = self.ok("/api/roster", {"op": "add", "name": "checker", "template": "verifier"})
        self.assertEqual((added["agent"], added["kind"], added["message"]), ("checker", "added", agentfiles.LIVE))
        copy = self.ok("/api/roster", {"op": "add", "name": "claude-copy", "copy_of": "impl-claude"})
        self.assertEqual(copy["permission"], "writer")
        shown = self.ok("/api/roster", {"op": "read", "name": "checker"})
        self.assertEqual({key: shown[key] for key in ("name", "text")},
                         {"name": "checker", "text": agentfiles.VERIFIER_TEXT})
        self.assertEqual(shown["permissions"],
                         agentfiles.summary(agentfiles.parse((self.agents / "checker.md").read_bytes())["permissions"]))
        self.assertTrue({"name", "description", "model", "text"} <= set(shown))
        before = (self.agents / "checker.md").read_bytes()
        changed = self.ok("/api/roster", {"op": "text", "name": "checker", "text": "Check the tests only."})
        self.assertEqual(changed["text"], "Check the tests only.")
        self.ok("/api/roster", {"op": "restore", "record": changed["record"]})
        self.assertEqual((self.agents / "checker.md").read_bytes(), before)
        team = self.ok("/api/structure/team", {"op": "add", "lead": "checker", "members": ["claude-copy"]})
        self.assertEqual(team["team"]["lead"], "checker")
        middle = self.state()
        removed = self.ok("/api/roster", {"op": "remove", "name": "checker"})
        self.assertEqual(removed["removed"]["teams"], ["checker"])
        self.assertFalse((self.agents / "checker.md").exists())
        self.ok("/api/roster", {"op": "restore", "record": removed["record"]})
        self.assertEqual(self.state(), middle)
        self.ok("/api/structure/team", {"op": "remove", "id": "checker"})
        self.ok("/api/roster", {"op": "remove", "name": "checker"})
        self.ok("/api/roster", {"op": "remove", "name": "claude-copy"})
        self.assertEqual(self.state()[:3] + self.state()[4:], self.start[:3] + self.start[4:])

    def test_added_agents_appear_in_the_snapshot_without_a_restart(self):
        first = self.ok_get()
        self.assertNotIn("checker", [agent["name"] for agent in first["agents"]])
        self.ok("/api/roster", {"op": "add", "name": "checker", "template": "verifier"})
        (self.agents / "mine.md").write_text("---\ndescription: mine\nmodel: a/b\n---\nBody\n")
        rows = {agent["name"]: agent for agent in self.ok_get()["agents"]}
        self.assertTrue(rows["checker"]["added"])
        self.assertTrue(rows["checker"]["removable"])
        self.assertFalse(rows["mine"]["added"])
        self.assertFalse(rows["mine"]["removable"])
        self.assertIn("written by hand", rows["mine"]["removable_reason"])
        self.assertFalse(rows["impl-claude"]["added"])

    def ok_get(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=30)
        try:
            conn.request("GET", "/api/snapshot")
            response = conn.getresponse()
            body = json.loads(response.read())
        finally:
            conn.close()
        self.assertEqual(response.status, 200, body)
        return body

    def test_refusals_are_clear_and_change_nothing(self):
        (self.agents / "mine.md").write_text("---\ndescription: mine\nmodel: a/b\n---\nBody\n")
        for body, message in (({"op": "add", "name": "Bad", "template": "verifier"}, "invalid agent name"),
                              ({"op": "add", "name": "-x", "template": "verifier"}, "invalid agent name"),
                              ({"op": "add", "name": "impl-claude", "template": "verifier"}, "already exists"),
                              ({"op": "add", "name": "mine", "template": "verifier"}, "already an agent file"),
                              ({"op": "add", "name": "x", "template": "nope"}, "unknown template"),
                              ({"op": "add", "name": "x", "copy_of": "orchestrator"}, "cannot be copied"),
                              ({"op": "text", "name": "mine", "text": "New."}, "written by hand"),
                              ({"op": "remove", "name": "mine"}, "written by hand"),
                              ({"op": "remove", "name": "orchestrator"}, "orchestrator"),
                              ({"op": "read", "name": "ghost"}, "no agent file"),
                              ({"op": "restore", "record": "/etc/passwd"}, "undo record")):
            with self.subTest(body=body):
                self.refused("/api/roster", body, message)
        for body in ({"op": "add", "name": "x", "skills": "a"}, {"op": "add", "name": "x", "read_only": "yes"},
                     {"op": "text", "name": "x"}, {"op": "read", "name": 3}, {"op": "add", "name": "x", "skills": [1]}):
            with self.subTest(body=body):
                code, answer, _ = self.call("/api/roster", body)
                self.assertEqual(code, 400, answer)


class SkillfileEndpoint(Served):
    def test_create_edit_undo_delete_restore(self):
        created = self.ok("/api/skillfile", {"op": "create", "id": "trial-skill", "description": "A trial.",
                                             "text": "First.\n"})
        self.assertEqual(created["id"], "trial-skill")
        written = self.ok("/api/skillfile", {"op": "write", "id": "trial-skill", "text": "Second.\n"})
        self.ok("/api/skillfile", {"op": "restore", "record": written["record"]})
        self.assertEqual(self.ok("/api/skillfile", {"op": "read", "id": "trial-skill"})["text"], "First.\n")
        deleted = self.ok("/api/skillfile", {"op": "delete", "id": "trial-skill"})
        self.ok("/api/skillfile", {"op": "restore", "record": deleted["record"]})
        self.ok("/api/skillfile", {"op": "delete", "id": "trial-skill"})
        self.assertEqual(self.state()[:4], self.start[:4])

    def test_delete_is_refused_while_an_added_agent_holds_the_skill(self):
        # The verifier's reproduction: zz-new given to the added agent beta, then deleted.
        self.ok("/api/roster", {"op": "add", "name": "beta", "template": "verifier"})
        self.ok("/api/skillfile", {"op": "create", "id": "zz-new", "description": "New.", "text": "Body.\n",
                                   "assign": "beta"})
        self.assertIn(b'resource: "zz-new"', (self.agents / "beta.md").read_bytes())
        self.refused("/api/skillfile", {"op": "delete", "id": "zz-new"}, "beta")
        self.assertTrue((self.skills / "zz-new" / "SKILL.md").is_file())

    def test_read_only_skills_can_be_read_not_written(self):
        self.assertFalse(self.ok("/api/skillfile", {"op": "read", "id": "shopify-ruby"})["editable"])
        self.refused("/api/skillfile", {"op": "write", "id": "shopify-ruby", "text": "x"}, "shopify-ruby")
        self.refused("/api/skillfile", {"op": "delete", "id": "shopify-ruby"}, "shopify-ruby")
        self.refused("/api/skillfile", {"op": "read", "id": "-x"}, "")


class TeamEndpoint(Served):
    def test_add_members_text_remove(self):
        made = self.ok("/api/structure/team", {"op": "add", "lead": "reviewer", "members": ["impl-openai"]})
        self.assertEqual(made["team"]["members"], ["impl-openai"])
        changed = self.ok("/api/structure/team", {"op": "members", "id": "reviewer",
                                                  "members": ["impl-openai", "impl-claude"]})
        self.assertEqual(changed["team"]["members"], ["impl-openai", "impl-claude"])
        self.ok("/api/structure/team", {"op": "text", "id": "reviewer", "text": "Reconcile both."})
        gone = self.ok("/api/structure/team", {"op": "remove", "id": "reviewer"})
        self.assertEqual(gone["teams"], [])
        self.assertEqual(self.state(), self.start)
        self.refused("/api/structure/team", {"op": "remove", "id": "reviewer"}, "reviewer")
        self.refused("/api/structure/team", {"op": "add", "lead": "orchestrator", "members": ["impl-openai"]}, "")


class ReadPaths(Scratch):
    def test_a_server_on_scratch_paths_reads_its_own_preset_and_never_the_real_files(self):
        # A preset that differs from the real one: an agent only it has, with its own model.
        data = json.loads(self.config.read_bytes())
        data["presets"][data["preset"]]["scratch-only"] = {"model": "example/scratch-model", "description": "Only here."}
        self.config.write_text(json.dumps(data, indent=2))
        agentfiles.create("file-agent", description="From the scratch agents folder.", model="example/file-model",
                          permissions=agentfiles.WRITER, text="Work.", agents_dir=self.agents)
        # Its own opencode.json (the root session's wear) and ledger (memory-<preset>), never the real ones.
        (self.root / "opencode.json").write_text("{}")
        (self.root / "memory").mkdir()
        httpd = Served.serve(self, opencode_dir=self.root, ledger_root=self.root)
        read_json, ledgers = [], []
        real_read_json, real_signals = sources._read_json, __import__("agentgraph.ledger").ledger.signals

        def wear_too(db_path, pick=None, wear=None):
            # The scratch folder has no sessions database; act as if it found the root session, so `wear` runs.
            wear(None, sources.Session(id="root", parent_id=None, agent="orchestrator", title="", model="", effort="",
                                       busy=False, created=0, updated=0))
            return [], []

        _WATCH["seen"].clear()
        _WATCH["on"] = True
        try:
            with patch.object(sources, "_read_checked", wear_too), \
                 patch.object(sources, "_read_json", lambda path: read_json.append(path) or real_read_json(path)), \
                 patch("agentgraph.ledger.signals", lambda home, *a, **k: ledgers.append(home) or real_signals(home, *a, **k)):
                conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=30)
                conn.request("GET", "/api/snapshot")
                document = json.loads(conn.getresponse().read())
                conn.close()
        finally:
            _WATCH["on"] = False
        self.assertNotIn("error", document)
        self.assertEqual(sorted(_WATCH["seen"]), [], "the scratch server read a real file")
        self.assertEqual(read_json, [self.root / "opencode.json"])
        self.assertEqual(ledgers, [self.root / "memory"])
        names = [agent["name"] for agent in document["agents"]]
        self.assertIn("scratch-only", names)
        self.assertIn("file-agent", names)
        self.assertIn("example/file-model", document["roster"]["models"])
        self.assertEqual(sorted(entry["name"] for entry in document["skills"]),
                         ["code-assurance", "engineering-delivery", "shopify-ruby"])

    def test_load_roster_takes_the_preset_file_and_agents_folder_given(self):
        agentfiles.create("file-agent", description="d", model="a/b", permissions=agentfiles.WRITER, text="t",
                          agents_dir=self.agents)
        _WATCH["seen"].clear()
        _WATCH["on"] = True
        try:
            agents, _, _ = sources._load_roster(self.root / "nowhere", config_path=self.config, agents_dir=self.agents)
        finally:
            _WATCH["on"] = False
        self.assertEqual(sorted(_WATCH["seen"]), [])
        self.assertIn("file-agent", [agent.name for agent in agents])


class Commands(Scratch):
    def run_cli(self, *args, stdin=""):
        return subprocess.run([str(ROOT / "bin/agentgraph"), *args], cwd=self.root, input=stdin, capture_output=True,
                              text=True, timeout=60, env={**os.environ})

    def options(self):
        return ["--config", str(self.config), "--map", str(self.map), "--backups", str(self.backups),
                "--structure", str(self.structure), "--agents", str(self.agents), "--skills-dir", str(self.skills),
                "--json"]

    def test_agent_command(self):
        listed = self.run_cli("agent", "list", "--config", str(self.config), "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        added = self.run_cli("agent", "add", "checker", "--template", "verifier", *self.options())
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout)["message"], agentfiles.LIVE)
        shown = self.run_cli("agent", "show", "checker", *self.options())
        self.assertEqual(json.loads(shown.stdout)["text"], agentfiles.VERIFIER_TEXT)
        text = self.run_cli("agent", "text", "checker", "--text-file", "-", *self.options(), stdin="New.\n")
        self.assertEqual(text.returncode, 0, text.stderr)
        restored = self.run_cli("agent", "restore", json.loads(text.stdout)["record"], *self.options())
        self.assertEqual(restored.returncode, 0, restored.stderr)
        removed = self.run_cli("agent", "remove", "checker", *self.options())
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(self.state(), self.start)
        refused = self.run_cli("agent", "add", "-x", "--template", "verifier", *self.options())
        self.assertEqual(refused.returncode, 2)  # argparse: a leading "-" is an option
        refused = self.run_cli("agent", "add", "Bad", "--template", "verifier", *self.options()[:-1])
        self.assertEqual(refused.returncode, 1)
        self.assertIn("invalid agent name", refused.stderr)
        self.assertNotIn("Traceback", refused.stderr)

    def test_skill_command(self):
        options = ["--skills", str(self.skills), "--backups", str(self.backups), "--config", str(self.config),
                   "--map", str(self.map), "--profiles", str(self.profiles), "--agents", str(self.agents), "--json"]
        created = self.run_cli("skill", "create", "trial-skill", "--description", "A trial.", "--text-file", "-",
                               *options, stdin="First.\n")
        self.assertEqual(created.returncode, 0, created.stderr)
        written = self.run_cli("skill", "write", "trial-skill", "--text-file", "-", *options, stdin="Second.\n")
        self.run_cli("skill", "restore", json.loads(written.stdout)["record"], *options)
        shown = self.run_cli("skill", "show", "trial-skill", *options)
        self.assertEqual(json.loads(shown.stdout)["text"], "First.\n")
        deleted = self.run_cli("skill", "delete", "trial-skill", *options)
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertEqual(self.state(), self.start)
        refused = self.run_cli("skill", "write", "shopify-ruby", "--text-file", "-", *options[:-1], stdin="x")
        self.assertEqual(refused.returncode, 1)
        self.assertNotIn("Traceback", refused.stderr)

    def test_team_and_structure_teams_commands(self):
        made = self.run_cli("team", "--structure", str(self.structure), "add", "reviewer", "impl-openai", "--json")
        self.assertEqual(made.returncode, 0, made.stderr)
        listed = self.run_cli("structure", "--structure", str(self.structure), "teams", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual([team["id"] for team in json.loads(listed.stdout)["teams"]], ["reviewer"])
        self.assertEqual(self.run_cli("team", "--structure", str(self.structure), "text", "reviewer", "--text-file",
                                      "-", stdin="Reconcile.").returncode, 0)
        self.assertEqual(self.run_cli("team", "--structure", str(self.structure), "members", "reviewer",
                                      "impl-claude").returncode, 0)
        self.assertEqual(self.run_cli("team", "--structure", str(self.structure), "remove", "reviewer").returncode, 0)
        self.assertEqual(self.state(), self.start)
        refused = self.run_cli("team", "--structure", str(self.structure), "remove", "reviewer")
        self.assertEqual(refused.returncode, 1)
        self.assertNotIn("Traceback", refused.stderr)


if __name__ == "__main__":
    unittest.main()
