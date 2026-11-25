"""The Windows snapshot is the Mac snapshot: the editors, the roster and the MCP list are all there."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentgraph import snapshot_json, sources
from agentgraph.contract import Agent, Snapshot


class WindowsSnapshotTests(unittest.TestCase):
    def test_live_session_projection(self):
        """On Windows the live document is the same as on a Mac: an editable roster and an MCP list."""
        snapshot = Snapshot(1000, roster=[Agent("orchestrator", "test/model", "", "hub")])
        # No platform switch is left in the document: the same code runs on Windows (and the whole suite runs
        # against the Windows file layer with AGENTGRAPH_OSFS=portable).
        doc = snapshot_json.live_document(snapshot, "live")
        self.assertNotIn("read_only", doc)
        self.assertEqual(doc["agents"][0]["name"], "orchestrator")
        self.assertTrue(doc["roster"]["editable"])
        self.assertIn("mcp", doc)
        self.assertFalse(any("read-only viewer" in warning for warning in doc["warnings"]))
        self.assertFalse(any("require POSIX" in warning for warning in doc["warnings"]))

    def test_graph_reads_work(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            graph = folder / "task.json"
            graph.write_text("{}", encoding="utf-8")
            self.assertEqual(sources.read_graph_text(graph), "{}")
            files, warnings = sources.graph_files(folder)
            self.assertEqual(files, [graph])
            self.assertEqual(warnings, [])

    @unittest.skipUnless(sys.platform == "win32", "native Windows smoke test")
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
            args = [sys.executable, "-m", "agentgraph.structure", "--structure", str(root)]
            result = subprocess.run([*args, "flows", "--json"], capture_output=True, check=True)
            doc = json.loads(result.stdout)
            self.assertEqual(doc["flows"][0]["id"], "test")
            self.assertEqual(doc["flows"][0]["path"], ["impl-openai", "verifier"])
            refused = subprocess.run(
                [*args, "flow", "remove", "test", "--json"], capture_output=True)
            self.assertEqual(refused.returncode, 1)
            self.assertTrue((flows / "test.md").exists())

    @unittest.skipUnless(sys.platform == "win32", "native Windows smoke test")
    def test_snapshot_commands(self):
        for args in (["--demo"], []):
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, "-m", "agentgraph.snapshot_json", *args],
                    capture_output=True, text=True, encoding="utf-8", check=True,
                )
                doc = json.loads(result.stdout)
                self.assertEqual(doc["version"], 1)
                self.assertNotIn("error", doc)
                if args:
                    self.assertTrue(doc["agents"])
                else:
                    self.assertNotIn("read_only", doc)
