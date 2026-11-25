"""agentgraph.paths: which copy of structure/, SKILLS.md, task-profiles.md and setups/ is in use. Scratch folders
only; the real personal folder and the checkout's copies are never written."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.launch import home_env
from agentgraph import paths

ROOT = Path(__file__).resolve().parent.parent


class PathsCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="paths-test-")))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.repo = self.base / "repo"
        (self.repo / "structure" / "01-core").mkdir(parents=True)
        (self.repo / "structure" / "01-core" / "orchestrator.md").write_text("repo agent\n", encoding="utf-8")
        (self.repo / "SKILLS.md").write_text("repo skills\n", encoding="utf-8")
        (self.repo / "task-profiles.md").write_text("repo profiles\n", encoding="utf-8")
        self.home = self.base / "home"
        self.home.mkdir()
        self.default = self.home / ".config" / "opencode" / "agentgraph-personal"
        self.env_folder = self.base / "elsewhere"

    def resolve(self, item, env=None):
        return paths.resolve_item(item, env or {}, self.home, self.repo)

    def personal(self, folder, *names):
        for name in names:
            target = folder / name
            if name.endswith("/"):
                target.mkdir(parents=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("personal\n", encoding="utf-8")


class Precedence(PathsCase):
    def test_no_personal_folder_uses_the_checkout_for_everything(self):
        for item, name in paths.NAMES.items():
            got = self.resolve(item)
            self.assertEqual((got.path, got.source), (self.repo / name, "repo"), item)

    def test_default_folder_beats_the_checkout_item_by_item(self):
        self.personal(self.default, "SKILLS.md", "structure/")
        self.assertEqual(self.resolve("skills_map").path, self.default / "SKILLS.md")
        self.assertEqual(self.resolve("skills_map").source, "home")
        self.assertEqual(self.resolve("structure").path, self.default / "structure")
        got = self.resolve("profiles")  # not in the personal folder: the toolkit's copy
        self.assertEqual((got.path, got.source), (self.repo / "task-profiles.md", "repo"))
        self.assertIn("no task-profiles.md", got.reason)

    def test_env_folder_beats_the_default_folder(self):
        self.personal(self.default, "SKILLS.md")
        self.personal(self.env_folder, "SKILLS.md")
        got = self.resolve("skills_map", {paths.ENV: str(self.env_folder)})
        self.assertEqual((got.path, got.source), (self.env_folder / "SKILLS.md", "env"))

    def test_env_item_missing_falls_back_to_the_checkout_not_the_default_folder(self):
        self.personal(self.default, "SKILLS.md")
        self.env_folder.mkdir()
        got = self.resolve("skills_map", {paths.ENV: str(self.env_folder)})
        self.assertEqual((got.path, got.source), (self.repo / "SKILLS.md", "repo"))

    def test_env_naming_a_missing_folder_is_ignored_and_says_so(self):
        self.personal(self.default, "SKILLS.md")
        got = self.resolve("skills_map", {paths.ENV: str(self.env_folder)})
        self.assertEqual(got.path, self.default / "SKILLS.md")
        self.assertIn("not a folder", got.reason)
        got = self.resolve("profiles", {paths.ENV: str(self.env_folder)})
        self.assertEqual(got.source, "repo")
        self.assertIn("not a folder", got.reason)

    def test_setups_is_personal_as_soon_as_a_folder_is_active_even_before_it_exists(self):
        self.default.mkdir(parents=True)
        got = self.resolve("setups")
        self.assertEqual((got.path, got.source), (self.default / "setups", "home"))
        self.assertIn("not created yet", got.reason)
        self.assertEqual(self.resolve("setups").path.exists(), False)

    def test_an_empty_env_value_counts_as_unset(self):
        self.assertEqual(self.resolve("skills_map", {paths.ENV: ""}).source, "repo")

    def test_unknown_item(self):
        with self.assertRaises(KeyError):
            paths.resolve("nope")

    def test_real_resolution_in_this_checkout_is_unchanged_without_a_personal_folder(self):
        real_env = {k: v for k, v in os.environ.items() if k != paths.ENV}
        if paths.default_folder().is_dir():
            self.skipTest("this machine has a personal folder")
        self.assertEqual(paths.resolve("skills_map", real_env), ROOT / "SKILLS.md")
        self.assertEqual(paths.resolve("structure", real_env), ROOT / "structure")


class Init(PathsCase):
    def run_init(self, env=None):
        lines = []
        code = paths.init(env or {}, self.home, self.repo, out=lines.append)
        return code, lines

    def test_copies_the_checkouts_items_and_makes_them_the_active_ones(self):
        code, lines = self.run_init()
        self.assertEqual(code, 0)
        self.assertEqual((self.default / "SKILLS.md").read_text(encoding="utf-8"), "repo skills\n")
        self.assertEqual((self.default / "structure" / "01-core" / "orchestrator.md").read_text(encoding="utf-8"), "repo agent\n")
        self.assertTrue((self.default / "setups").is_dir())
        self.assertEqual(sum(line.startswith(("copied", "created")) for line in lines), 4)
        self.assertEqual(self.resolve("skills_map").source, "home")
        self.assertTrue(any("used from the next start" in line for line in lines))

    def test_refuses_to_overwrite_and_keeps_the_users_edit(self):
        self.run_init()
        (self.default / "SKILLS.md").write_text("my edit\n", encoding="utf-8")
        (self.repo / "SKILLS.md").write_text("repo skills v2\n", encoding="utf-8")
        code, lines = self.run_init()
        self.assertEqual(code, 1)
        self.assertEqual((self.default / "SKILLS.md").read_text(encoding="utf-8"), "my edit\n")
        self.assertEqual(sum(line.startswith("refused") for line in lines), 4)

    def test_copies_only_what_is_missing(self):
        self.personal(self.default, "SKILLS.md")
        code, lines = self.run_init()
        self.assertEqual(code, 1)
        self.assertEqual((self.default / "SKILLS.md").read_text(encoding="utf-8"), "personal\n")
        self.assertTrue((self.default / "task-profiles.md").is_file())

    def test_env_folder_is_created_and_filled(self):
        code, lines = self.run_init({paths.ENV: str(self.env_folder)})
        self.assertEqual(code, 0)
        self.assertTrue((self.env_folder / "structure").is_dir())
        self.assertFalse(self.default.exists())
        self.assertEqual(self.resolve("structure", {paths.ENV: str(self.env_folder)}).source, "env")


class Cli(PathsCase):
    def run_cli(self, *args, env=None):
        full = {k: v for k, v in os.environ.items() if k != paths.ENV}
        full.update(home_env(self.home), **(env or {}))
        return subprocess.run([sys.executable, "-m", "agentgraph.paths", *args], cwd=ROOT, env=full,
                              capture_output=True, text=True)

    def test_show_names_the_copy_in_use_and_why(self):
        proc = self.run_cli("show")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("repo "), 4)
        self.assertIn("no AGENTGRAPH_PERSONAL folder", proc.stdout)
        self.env_folder.mkdir()
        proc = self.run_cli("show", env={paths.ENV: str(self.env_folder)})
        self.assertIn("AGENTGRAPH_PERSONAL names it", proc.stdout)
        self.assertEqual(proc.stdout.count(" env "), 1)  # only setups: the folder holds nothing else yet

    def test_init_then_the_modules_use_the_personal_copies(self):
        proc = self.run_cli("init", env={paths.ENV: str(self.env_folder)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.run_cli("init", env={paths.ENV: str(self.env_folder)}).returncode, 1)
        code = ("from agentgraph import skillset, setups; "
                "print(skillset.MAP); print(skillset.PROFILES); print(setups.SETUPS)")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, **home_env(self.home), paths.ENV: str(self.env_folder)})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), [str(self.env_folder / n) for n in ("SKILLS.md", "task-profiles.md", "setups")])

    def test_usage(self):
        self.assertEqual(self.run_cli("bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
