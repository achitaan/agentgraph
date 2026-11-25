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
    @unittest.skipUnless(sys.platform == "win32", "native Windows smoke test")
    def test_skill_text_inspection(self):
        from agentgraph import skillfiles
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "example"
            folder.mkdir()
            raw = "---\nname: example\ndescription: Example skill\n---\n\nRead only.\n"
            (folder / "SKILL.md").write_bytes(raw.encode("utf-8"))
            shown = skillfiles.show("example", skills=root)
            self.assertEqual(shown["description"], "Example skill")
            self.assertIn("Read only.", shown["text"])
            self.assertFalse(shown["editable"])
            with self.assertRaises(skillfiles.SkillfilesError):
                skillfiles.show("../example", skills=root)
            self.assertEqual((folder / "SKILL.md").read_bytes(), raw.encode("utf-8"))

    @unittest.skipUnless(sys.platform == "win32", "native Windows smoke test")
    def test_preset_and_saved_setup_inspection(self):
        from agentgraph import setups
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            original = json.dumps({"preset": "normal", "presets": {"normal": {}, "fast": {}}})
            config.write_text(original, encoding="utf-8")
            saved = root / "saved"
            folder = saved / "example"
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(json.dumps({
                "version": 1, "files": [], "dirs": [], "saved": "2026-01-01",
                "name": "example", "preset": "normal"
            }), encoding="utf-8")
            self.assertEqual(setups.show_preset(config=config, env={})["effective"], "normal")
            self.assertEqual(setups.show_preset(config=config, env={
                setups.ENV: "fast"})["effective"], "fast")
            self.assertEqual(setups.list_setups(setups=saved)[0]["name"], "example")
            self.assertEqual(setups.show_setup("example", setups=saved)["preset"], "normal")
            with self.assertRaisesRegex(setups.SetupError, "requires POSIX"):
                setups.switch_preset("fast", config=config)
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_live_session_projection(self):
        snapshot = Snapshot(1000, roster=[Agent("orchestrator", "test/model", "", "hub")])
        with patch.object(snapshot_json, "WINDOWS", True):
            doc = snapshot_json.live_document(snapshot, "live")
        self.assertTrue(doc["read_only"])
        self.assertEqual(doc["agents"][0]["name"], "orchestrator")
        self.assertFalse(doc["roster"]["editable"])
        self.assertTrue(any("Windows" in warning for warning in doc["warnings"]))

    @unittest.skipUnless(sys.platform == "win32", "native Windows smoke test")
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
        import os
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
                    sources.read_graph_text(link / "task.json")
                with self.assertRaises(OSError):
                    sources.graph_files(link)
            finally:
                os.rmdir(link)

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
                    self.assertTrue(doc["read_only"])
