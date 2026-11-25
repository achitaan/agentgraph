"""Who owns a folder (agentgraph.fileio), and the places that use the answer.

The Windows branch cannot run on a Mac, so it is switched on here by patching `fileio.WINDOWS` and the owner
check; the owner check itself is exercised for real by the first test on whatever system runs it."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import fileio, setups, sources, spend
from tests.winskip import needs_symlinks


class OwnerTests(unittest.TestCase):
    def test_a_folder_made_by_this_process_is_mine(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(fileio.owned_by_me(temp))
            self.assertTrue(fileio.owned_by_me(Path(temp)))

    def test_a_missing_folder_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(OSError):
                fileio.owned_by_me(Path(temp) / "missing")

    @unittest.skipUnless(hasattr(os, "getuid"), "needs os.getuid")
    def test_another_uid_is_not_mine(self):
        with tempfile.TemporaryDirectory() as temp, patch("agentgraph.fileio.osfs.getuid", return_value=os.getuid() + 1):
            self.assertFalse(fileio.owned_by_me(temp))


class WindowsBranchTests(unittest.TestCase):
    """sources.graph_files and find_graph_dir with the Windows branch switched on."""

    def setUp(self):
        self.base = Path(os.path.realpath(self.enterContext(tempfile.TemporaryDirectory())))
        self.home = self.base / "home"
        self.graphs = self.home / "project" / ".opencode" / "graph"
        self.graphs.mkdir(parents=True)
        (self.graphs / "task.json").write_text('{"task":"example"}', encoding="utf-8")
        self.enterContext(patch("agentgraph.sources.Path.home", return_value=self.home))
        self.enterContext(patch("agentgraph.fileio.WINDOWS", True))

    def test_an_owned_directory_is_used(self):
        with patch("agentgraph.fileio.owned_by_me", return_value=True) as owner:
            files, warnings = sources.graph_files(self.graphs)
            found = sources.find_graph_dir(self.graphs.parent.parent)
        self.assertEqual([path.name for path in files], ["task.json"])
        self.assertEqual(found, self.graphs)
        self.assertTrue(owner.called)

    def test_a_directory_owned_by_somebody_else_is_refused(self):
        with patch("agentgraph.fileio.owned_by_me", return_value=False):
            with self.assertRaises(PermissionError):
                sources.graph_files(self.graphs)
            warnings = []
            self.assertIsNone(sources.find_graph_dir(self.graphs.parent.parent, warnings=warnings))
        self.assertEqual(len(warnings), 1)
        self.assertIn("owned", warnings[0])

    def test_a_junction_is_refused(self):
        with patch("agentgraph.fileio.owned_by_me", return_value=True), \
                patch("agentgraph.sources.osfs.is_link", return_value=True):
            with self.assertRaises(OSError):
                sources.graph_files(self.graphs)
            warnings = []
            self.assertIsNone(sources.find_graph_dir(self.graphs.parent.parent, warnings=warnings))
        self.assertEqual(len(warnings), 1)
        self.assertIn("link", warnings[0])

    @needs_symlinks
    def test_a_symlinked_directory_is_refused(self):
        link = self.base / "linked"
        link.symlink_to(self.graphs, target_is_directory=True)
        with patch("agentgraph.fileio.owned_by_me", return_value=True):
            with self.assertRaises(OSError):
                sources.graph_files(link)


class JunctionIsNotAPlainFolder(unittest.TestCase):
    """A junction is a directory to `stat.S_ISDIR`; the spend and setup folders must be real ones."""

    def test_plain_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(spend._plain_dir(Path(temp)))
            self.assertTrue(setups._is_plain_dir(Path(temp)))

    def test_a_link_is_not_a_plain_folder(self):
        with tempfile.TemporaryDirectory() as temp, patch("agentgraph.osfs.is_link", return_value=True):
            self.assertFalse(spend._plain_dir(Path(temp)))
            self.assertFalse(setups._is_plain_dir(Path(temp)))

    @unittest.skipUnless(os.name == "nt", "needs Windows junctions")
    def test_a_real_junction(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target"
            target.mkdir()
            link = Path(temp) / "junction"
            subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)
            try:
                self.assertFalse(spend._plain_dir(link))
                self.assertFalse(setups._is_plain_dir(link))
            finally:
                os.rmdir(link)


class SpendRoundtrip(unittest.TestCase):
    def test_spend_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(spend.set_limit(50, project=temp)["changed"])
            self.assertEqual(spend.show(temp), {"limit": 50, "warn": 45})
            self.assertTrue(spend.remove(project=temp)["changed"])
            self.assertEqual(spend.show(temp), {"limit": None, "warn": None})


if __name__ == "__main__":
    unittest.main()
