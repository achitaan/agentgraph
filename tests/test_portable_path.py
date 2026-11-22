"""The config folder's absolute path is not shipped: sync.sh swaps it for portable.TOKEN in the mirrored preset file
and prompt files, install.sh swaps the target folder's path back in. Everything runs on a scratch copy of the toolkit,
a scratch live folder and a scratch target; the real config folder and the real mirror are never read or written."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agentgraph import portable
from tests.test_install import InstallCase, PRESET, SLIM, read_json, write_json

RULE = "{}/orchestration/bin/agentgraph review-copy *"
LINE = "Run `{}/orchestration/bin/agentgraph` from the project folder.\n"


def tree_bytes(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(Path(folder).rglob("*"))
            if p.is_file() and "backups" not in p.relative_to(folder).parts}


class Tokenize(unittest.TestCase):
    D = "/home/me/.config/opencode"

    def test_whole_path_only(self):
        text = "\n".join([
            f"a {self.D}/orchestration/bin/x",   # replaced
            f"b {self.D}-old/bin/x",             # longer folder name
            f"c {self.D}.bak/x",                 # longer name with a dot
            f"d /other{self.D}/x",               # the path only ends the same way
            f"e `{self.D}` and {self.D}.",       # end of a sentence is still the path
            f"f {self.D}2/x",
        ])
        out = portable.tokenize(text, [self.D], False).split("\n")
        self.assertEqual(out[0], f"a {portable.TOKEN}/orchestration/bin/x")
        self.assertEqual(out[1:4], text.split("\n")[1:4])
        self.assertEqual(out[4], f"e `{portable.TOKEN}` and {portable.TOKEN}.")
        self.assertEqual(out[5], text.split("\n")[5])

    def test_a_link_and_its_target_both_count_and_json_escapes_are_found(self):
        d = '/a "b"/opencode'
        escaped = json.dumps({"k": d + "/x"})
        out = portable.tokenize(escaped, [d, "/real/opencode"], True)
        self.assertEqual(json.loads(out), {"k": portable.TOKEN + "/x"})
        both = portable.tokenize("/link/oc/x /real/oc/y", ["/link/oc", "/real/oc"], False)
        self.assertEqual(both, f"{portable.TOKEN}/x {portable.TOKEN}/y")

    def test_render_escapes_for_json(self):
        d = '/a "b"/opencode'
        out = portable.render(json.dumps({"k": portable.TOKEN + "/x"}), d, True)
        self.assertEqual(json.loads(out), {"k": d + "/x"})
        self.assertEqual(portable.render(f"{portable.TOKEN}/x", d, False), d + "/x")


class RoundTrip(InstallCase):
    LIVE_NAME = "live config"  # a space in the folder name

    def setUp(self):
        super().setUp()
        for name in ("sync.sh",):
            shutil.copy2(ROOT_DIR / name, self.kit / name)
        shutil.copytree(ROOT_DIR / "agentgraph", self.kit / "agentgraph", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT_DIR / "config" / "policy", self.kit / "config" / "policy", dirs_exist_ok=True)
        self.live = self.base / self.LIVE_NAME / "opencode"
        self.scratch = self.base / "scratch target" / "opencode"
        self.t = self.live
        self.sh()  # the live folder starts as an install of the kit
        self.make_live_paths()
        self.sync()

    def make_live_paths(self):
        data = read_json(self.live / PRESET)
        for preset in data["presets"].values():
            bash = preset["reviewer"]["permission"]["bash"]
            for key in [k for k in bash if k.endswith("review-copy *")]:
                del bash[key]
            bash[RULE.format(self.live)] = "allow"
            bash[RULE.format(str(self.live) + "-old")] = "allow"  # a longer folder name: not ours
        write_json(self.live / PRESET, data)
        self.prompt = self.live / SLIM / "reviewer_append.md"
        text = self.prompt.read_text()
        self.prompt.write_text(text.rstrip("\n") + "\n\n" + LINE.format(self.live) + LINE.format(str(self.live) + "-old"))
        self.live_preset_bytes = (self.live / PRESET).read_bytes()

    def run_sh(self, script, config_dir, check=True):
        env = {**os.environ, "HOME": str(self.home), "OPENCODE_CONFIG_DIR": str(config_dir)}
        proc = subprocess.run(["sh", str(self.kit / script)], env=env, capture_output=True, text=True)
        if check:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def sync(self):
        return self.run_sh("sync.sh", self.live)

    def mirror(self):
        return self.kit / "config"

    def install_scratch(self):
        return self.run_sh("install.sh", self.scratch)

    def test_the_mirror_holds_the_token_and_nothing_else_changed(self):
        mirrored = (self.mirror() / PRESET).read_bytes()
        data = json.loads(mirrored)  # still valid JSON
        for preset in data["presets"].values():
            bash = preset["reviewer"]["permission"]["bash"]
            self.assertIn(RULE.format(portable.TOKEN), bash)
            self.assertIn(RULE.format(str(self.live) + "-old"), bash)  # the longer name is untouched
        self.assertNotIn(str(self.live).encode() + b"/", mirrored.replace(str(self.live).encode() + b"-old", b""))
        self.assertEqual(mirrored.replace(portable.TOKEN.encode(), str(self.live).encode()), self.live_preset_bytes)
        text = (self.mirror() / SLIM / "reviewer_append.md").read_text()
        self.assertIn(LINE.format(portable.TOKEN), text)
        self.assertIn(LINE.format(str(self.live) + "-old"), text)
        self.assertEqual(text.replace(portable.TOKEN, str(self.live)), self.prompt.read_text())

    def test_a_second_sync_changes_nothing(self):
        before = tree_bytes(self.mirror())
        self.sync()
        self.assertEqual(tree_bytes(self.mirror()), before)

    def test_install_puts_the_target_path_back_and_leaves_no_token(self):
        self.install_scratch()
        self.assertEqual((self.scratch / PRESET).read_bytes(),
                         self.live_preset_bytes.replace(str(self.live).encode() + b"/", str(self.scratch).encode() + b"/"))
        data = read_json(self.scratch / PRESET)
        for preset in data["presets"].values():
            self.assertIn(RULE.format(self.scratch), preset["reviewer"]["permission"]["bash"])
        text = (self.scratch / SLIM / "reviewer_append.md").read_text()
        self.assertIn(LINE.format(self.scratch), text)
        self.assertIn(LINE.format(str(self.live) + "-old"), text)
        for rel, data in tree_bytes(self.scratch).items():
            self.assertNotIn(portable.TOKEN.encode(), data, rel)

    def test_a_second_install_changes_nothing(self):
        self.install_scratch()
        before = tree_bytes(self.scratch)
        self.assertIn("Nothing to change.", self.install_scratch())
        self.assertEqual(tree_bytes(self.scratch), before)

    def test_a_file_the_user_kept_is_not_rewritten(self):
        self.install_scratch()
        mine = self.scratch / SLIM / "reviewer_append.md"
        mine.write_text(mine.read_text() + "my own line\n")
        kept = mine.read_bytes()
        # the toolkit moves on, so the user's file is kept and the toolkit's version goes beside it
        theirs = self.mirror() / SLIM / "reviewer_append.md"
        theirs.write_text(theirs.read_text() + "a new toolkit line\n")
        self.install_scratch()
        self.assertEqual(mine.read_bytes(), kept)
        beside = mine.with_name(mine.name + ".toolkit-new").read_text()
        self.assertIn("a new toolkit line", beside)
        self.assertIn(LINE.format(self.scratch), beside)
        self.assertNotIn(portable.TOKEN, beside)

    def test_the_merged_preset_file_keeps_the_users_choices_and_the_target_path(self):
        self.install_scratch()
        data = read_json(self.scratch / PRESET)
        data["preset"] = "my-own"
        write_json(self.scratch / PRESET, data)
        self.install_scratch()
        merged = read_json(self.scratch / PRESET)
        self.assertEqual(merged["preset"], "my-own")
        self.assertNotIn(portable.TOKEN, (self.scratch / PRESET).read_text())


class Links(unittest.TestCase):
    def test_a_folder_reached_through_a_link_is_known_by_both_paths(self):
        base = os.path.realpath(tempfile.mkdtemp(prefix="portable-test-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        real = Path(base) / "real"
        real.mkdir()
        (Path(base) / "link").symlink_to(real)
        names = portable.folder_names(str(Path(base) / "link"))
        self.assertEqual(names, [str(Path(base) / "link"), str(real)])
        f = Path(base) / "f.md"
        f.write_text(f"{base}/link/x {real}/y\n")
        self.assertTrue(portable.tokenize_file(f, names))
        self.assertEqual(f.read_text(), f"{portable.TOKEN}/x {portable.TOKEN}/y\n")
        self.assertFalse(portable.tokenize_file(f, names))


ROOT_DIR = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    unittest.main()
