"""The Windows snapshot is the Mac snapshot: the editors, the roster and the MCP list are all there.

Every run here happens in a child process whose home folder, config folders and data folders are a scratch folder
holding a small fixture config, so the machine's real OpenCode setup is never read (and a failure cannot print it
into a log). The assertions look at named keys and counts only; the document itself is never put in a message."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentgraph import sources
from tests.launch import ROOT, bare_env, home_env

LIVE = """
import json
from agentgraph import snapshot_json
from agentgraph.contract import Agent, Snapshot

snapshot = Snapshot(1000, roster=[Agent("orchestrator", "test/model", "", "hub")])
doc = snapshot_json.live_document(snapshot, "live")
print(json.dumps({
    "read_only": "read_only" in doc,
    "first_agent": doc["agents"][0]["name"],
    "roster_keys": sorted(set(doc["roster"]) & {"editable", "models"}),
    "platform_reason": any(word in doc["roster"].get("why", "") for word in ("POSIX", "Windows", "read-only viewer")),
    "mcp": "mcp" in doc,
    "viewer_warnings": sum(1 for w in doc["warnings"] if "read-only viewer" in w or "require POSIX" in w),
}))
"""


class Isolated(unittest.TestCase):
    """A scratch home with a one-agent fixture config; `run` starts Python there and sees nothing else."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = self.home / ".config" / "opencode"
        config.mkdir(parents=True)
        (config / "oh-my-opencode-slim.json").write_text(
            json.dumps({"preset": "test", "presets": {"test": {"orchestrator": {"model": "test/model"}}}}),
            encoding="utf-8")
        data = self.home / "data"
        self.env = bare_env(
            PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8", **home_env(self.home),
            XDG_CONFIG_HOME=str(self.home / ".config"), XDG_DATA_HOME=str(data),
            OPENCODE_CONFIG_DIR=str(config), AGENTGRAPH_HOME=str(self.home / "memory"),
            AGENTGRAPH_PERSONAL=str(self.home / "personal"), AGENTGRAPH_ROLLOVER_HOME=str(data / "rollover"),
            AGENTGRAPH_COST_CHECK_HOME=str(data / "cost"), AGENTGRAPH_LOCKDIR=str(data / "locks"),
            LOCALAPPDATA=str(data / "local"), APPDATA=str(data / "roaming"))

    def run_python(self, *args, check=True):
        done = subprocess.run([sys.executable, "-P", *args], capture_output=True, text=True, encoding="utf-8",
                              env=self.env, cwd=self.home, timeout=120)
        if check and done.returncode:
            self.fail(f"python {args[0]} {args[1] if len(args) > 1 else ''} exited {done.returncode}; "
                      f"error output ends: {done.stderr[-600:]}")
        return done


class WindowsSnapshotTests(Isolated):
    def test_live_session_projection(self):
        """The live document is the same everywhere: a roster block that is not refused for the platform, and an MCP list."""
        doc = json.loads(self.run_python("-c", LIVE).stdout.strip().splitlines()[-1])
        self.assertFalse(doc["read_only"])
        self.assertEqual(doc["first_agent"], "orchestrator")
        self.assertEqual(doc["roster_keys"], ["editable", "models"])
        self.assertFalse(doc["platform_reason"])
        self.assertTrue(doc["mcp"])
        self.assertEqual(doc["viewer_warnings"], 0)

    def test_graph_reads_work(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            graph = folder / "task.json"
            graph.write_text("{}", encoding="utf-8")
            self.assertEqual(sources.read_graph_text(graph), "{}")
            files, warnings = sources.graph_files(folder)
            self.assertEqual(files, [graph])
            self.assertEqual(warnings, [])

    def test_flow_listing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = root / "01-build"
            group.mkdir()
            for agent in ("impl-openai", "verifier"):
                (group / (agent + ".md")).write_text("Agent\n", encoding="utf-8")
            flows = root / "flows"
            flows.mkdir()
            (flows / "test.md").write_text(
                "path: impl-openai -> verifier\n\nCheck the result.\n", encoding="utf-8")
            args = ["-m", "agentgraph.structure", "--structure", str(root)]
            result = self.run_python(*args, "flows", "--json")
            doc = json.loads(result.stdout)
            self.assertEqual(doc["flows"][0]["id"], "test")
            self.assertEqual(doc["flows"][0]["path"], ["impl-openai", "verifier"])
            # Windows edits the structure like the Mac does: the flow is removed, not refused as read-only
            removed = self.run_python(*args, "flow", "remove", "test", "--json", check=False)
            self.assertEqual(removed.returncode, 0)
            self.assertFalse((flows / "test.md").exists())

    def test_snapshot_commands(self):
        for args in (["--demo"], []):
            with self.subTest(args=args):
                doc = json.loads(self.run_python("-m", "agentgraph.snapshot_json", *args).stdout)
                self.assertEqual(doc["version"], 1)
                self.assertNotIn("error", doc)
                if args:
                    self.assertTrue(doc["agents"])
                else:
                    self.assertNotIn("read_only", doc)
                    self.assertIn("editable", doc["roster"])
                    self.assertNotIn("POSIX", doc["roster"].get("why", ""))

    # -- what Windows once only read, and now does fully (found and first tested on a real Windows machine) --

    def test_side_chat_status_and_transcript(self):
        import sqlite3
        from contextlib import closing
        from agentgraph import sidechat
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agents = root / "agents"
            agents.mkdir()
            (agents / "side-chat.md").write_text("Side chat agent", encoding="utf-8")
            state = root / ".opencode" / "sidechat"
            state.mkdir(parents=True)
            (state / "ses_main.json").write_text(
                json.dumps({"main": "ses_main", "side": "ses_side", "model": "test/model"}), encoding="utf-8")
            db = root / "test.db"
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("CREATE TABLE session_v2 (id TEXT, agent TEXT, cost REAL)")
                conn.execute("INSERT INTO session_v2 VALUES ('ses_side', 'side-chat', 0.25)")
                conn.commit()
            shown = sidechat.status(main="ses_main", project=root, agents_dir=agents, db_path=db, with_route=False)
            self.assertTrue(shown["installed"])
            self.assertEqual(shown["session"], "ses_side")
            self.assertEqual(shown["model"], "test/model")
            self.assertEqual(shown["spent"], 0.25)
            feed = sidechat.side_feed({"items": [
                {"kind": "user", "text": "Question\n\n--- brief private"},
                {"kind": "reasoning", "text": "hidden"},
                {"kind": "assistant", "text": "Answer"}]})
            self.assertEqual([item["text"] for item in feed["items"]], ["Question", "Answer"])

    def test_graph_check_runs_the_history_too(self):
        """`check` on a folder validates the graph and keeps its history, as it does on the Mac."""
        from tests.test_check import valid_graph
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            graph = folder / "task.json"
            raw = valid_graph()
            graph.write_text(json.dumps(raw), encoding="utf-8")
            args = ["-m", "agentgraph.check", "--dir", str(folder)]
            done = self.run_python(*args, check=False)
            self.assertEqual(done.returncode, 0, done.stdout[-400:] + done.stderr[-400:])
            self.assertNotIn("static validation only", done.stdout)
            self.assertIn("1/1 objectives delivered", done.stdout)
            self.assertTrue((folder / ".history").is_dir())
            raw["nodes"][0]["status"] = "invalid"
            graph.write_text(json.dumps(raw), encoding="utf-8")
            done = self.run_python(*args, check=False)
            self.assertEqual(done.returncode, 1)
            self.assertIn("PROBLEMS", done.stdout)
            # an existing history record catches a changed criterion
            raw = valid_graph()
            graph.write_text(json.dumps(raw), encoding="utf-8")
            record = next((folder / ".history").iterdir())
            self.assertTrue(record.is_file())
            raw["nodes"][0]["done_when"] = "different test criterion"
            graph.write_text(json.dumps(raw), encoding="utf-8")
            done = self.run_python(*args, check=False)
            self.assertEqual(done.returncode, 1, done.stdout[-400:])
            self.assertIn("done_when", done.stdout)
            self.assertEqual(self.run_python(*args, "--no-history", check=False).returncode, 0)

    def test_skill_text_is_shown_and_editable(self):
        from agentgraph import skillfiles
        with tempfile.TemporaryDirectory() as temp:
            root = Path(os.path.realpath(temp))  # a skills folder may not be reached through a link (macOS: /var)
            folder = root / "example"
            folder.mkdir()
            raw = "---\nname: example\ndescription: Example skill\n---\n\nSome text.\n"
            (folder / "SKILL.md").write_bytes(raw.encode("utf-8"))
            shown = skillfiles.show("example", skills=root, bundled=())
            self.assertEqual(shown["description"], "Example skill")
            self.assertIn("Some text.", shown["text"])
            self.assertTrue(shown["editable"], shown.get("why"))
            with self.assertRaises(skillfiles.SkillfilesError):
                skillfiles.show("../example", skills=root, bundled=())

    def test_preset_and_saved_setup_inspection_and_switch(self):
        from agentgraph import setups
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            config.write_text(json.dumps({"preset": "normal", "presets": {"normal": {}, "fast": {}}}),
                              encoding="utf-8")
            saved = root / "saved"
            folder = saved / "example"
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(json.dumps({
                "version": 1, "files": [], "dirs": [], "saved": "2026-01-01",
                "name": "example", "preset": "normal"}), encoding="utf-8")
            self.assertEqual(setups.show_preset(config=config, env={})["effective"], "normal")
            self.assertEqual(setups.show_preset(config=config, env={setups.ENV: "fast"})["effective"], "fast")
            self.assertEqual(setups.list_setups(setups=saved)[0]["name"], "example")
            self.assertEqual(setups.show_setup("example", setups=saved)["preset"], "normal")
            setups.switch_preset("fast", config=config)  # the switch is not refused for the platform
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["preset"], "fast")

    def test_graph_reads_and_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            graph = folder / "task.json"
            graph.write_text('{"task":"example","nodes":[]}', encoding="utf-8")
            self.assertEqual(json.loads(sources.read_graph_text(graph))["task"], "example")
            graphs, warnings = sources.load_graphs(folder)
            self.assertEqual([g.task for g in graphs], ["example"])
            self.assertEqual(warnings, [])
            graph.write_bytes(b"x" * (sources.MAX_GRAPH_BYTES + 1))
            with self.assertRaisesRegex(OSError, "exceeds"):
                sources.read_graph_text(graph)
            with self.assertRaises(OSError):
                sources.graph_files(graph)

    @unittest.skipUnless(sys.platform == "win32", "native Windows junction test")
    def test_graph_directory_junction_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.mkdir()
            (target / "task.json").write_text('{"task":"example"}', encoding="utf-8")
            link = root / "junction"
            subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, check=True)
            try:
                with self.assertRaises(OSError):
                    sources.graph_files(link)
            finally:
                os.rmdir(link)
