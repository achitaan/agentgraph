"""The Windows snapshot is the Mac snapshot: the editors, the roster and the MCP list are all there.

Every run here happens in a child process whose home folder, config folders and data folders are a scratch folder
holding a small fixture config, so the machine's real OpenCode setup is never read (and a failure cannot print it
into a log). The assertions look at named keys and counts only; the document itself is never put in a message."""
import json
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
