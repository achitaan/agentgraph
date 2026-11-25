"""The Windows viewer must not import the POSIX-only toolkit editors."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import snapshot_json, sources
from agentgraph.contract import Agent, Snapshot


class WindowsSnapshotTests(unittest.TestCase):
    def test_live_session_projection(self):
        snapshot = Snapshot(1000, roster=[Agent("orchestrator", "test/model", "", "hub")])
        with patch.object(snapshot_json, "WINDOWS", True):
            doc = snapshot_json.live_document(snapshot, "live")
        self.assertTrue(doc["read_only"])
        self.assertEqual(doc["agents"][0]["name"], "orchestrator")
        self.assertFalse(doc["roster"]["editable"])
        self.assertTrue(any("Windows" in warning for warning in doc["warnings"]))

    def test_graph_reads_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            graph = folder / "task.json"
            graph.write_text("{}", encoding="utf-8")
            with patch.object(sources, "WINDOWS", True):
                with self.assertRaisesRegex(OSError, "not supported on Windows"):
                    sources.read_graph_text(graph)
                graphs, warnings = sources.load_graphs(folder)
                self.assertEqual(graphs, [])
                self.assertTrue(any("not supported on Windows" in warning for warning in warnings))

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
                    self.assertTrue(doc["read_only"])
