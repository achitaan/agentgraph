"""install.sh keeps what the user changed: first install, update, `.toolkit-new`, the merged preset file, backups
and restore. Everything runs on a scratch copy of the toolkit and a scratch config folder; the real config folder
is never read or written."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.launch import home_env
from tests.winskip import needs_symlinks, posix_only
from agentgraph import installer, portable

ROOT = Path(__file__).resolve().parent.parent
# Where there is no `sh` (native Windows), the installer runs through install.cmd's own Python entry, installcli.
# AGENTGRAPH_TEST_NO_SH=1 does the same anywhere, so the Mac can run that path too.
HAVE_SH = bool(shutil.which("sh")) and not os.environ.get("AGENTGRAPH_TEST_NO_SH")
CLI = ("import sys; from pathlib import Path; from agentgraph import installcli; "
       "sys.exit(installcli.main(sys.argv[2:], source=Path(sys.argv[1])))")
SKILLS = ("code-assurance", "engineering-delivery", "grilling")
PRESET = "oh-my-opencode-slim.json"
SLIM = "oh-my-opencode-slim"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def append(path, text):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


class InstallCase(unittest.TestCase):
    def setUp(self):
        base = os.path.realpath(tempfile.mkdtemp(prefix="install-test-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.base = Path(base)
        self.kit = self.base / "kit"
        self.home = self.base / "home"
        self.t = self.home / ".config" / "opencode"
        (self.kit / "agentgraph").mkdir(parents=True)
        for name in ("install.sh", "agentgraph/installer.py", "agentgraph/portable.py", "agentgraph/osfs.py"):
            if (ROOT / name).exists():
                shutil.copy2(ROOT / name, self.kit / name)
        shutil.copytree(ROOT / "commands", self.kit / "commands")
        shutil.copytree(ROOT / "config", self.kit / "config", ignore=shutil.ignore_patterns("side-chat"))
        (self.kit / "skills").mkdir()
        for name in SKILLS:
            shutil.copytree(ROOT / "skills" / name, self.kit / "skills" / name)
        self.home.mkdir()

    def env(self):
        return {**os.environ, **home_env(self.home), "OPENCODE_CONFIG_DIR": str(self.t)}

    def cli(self, *args, check=True):
        """What install.cmd runs (agentgraph.installcli), against the scratch kit."""
        env = {**self.env(), "PYTHONPATH": str(ROOT)}
        proc = subprocess.run([sys.executable, "-P", "-c", CLI, str(self.kit), *args], env=env,
                              capture_output=True, text=True, encoding="utf-8")
        if check and proc.returncode:
            self.fail(f"install.cmd {args} failed: {proc.stdout}{proc.stderr}")
        return proc

    def sh(self, *args, check=True):
        if not HAVE_SH:
            return self.cli(*args, check=check)
        proc = subprocess.run(["sh", str(self.kit / "install.sh"), *args], env=self.env(), capture_output=True,
                              text=True)
        if check and proc.returncode:
            self.fail(f"install.sh {args} failed: {proc.stdout}{proc.stderr}")
        return proc

    def edit_kit(self, rel, text):
        append(self.kit / rel, text)

    def kit_json(self, change):
        data = read_json(self.kit / "config" / PRESET)
        change(data)
        write_json(self.kit / "config" / PRESET, data)

    def customize(self):
        """What a user does through the view and the improvement skills."""
        data = read_json(self.t / PRESET)
        data["preset"] = "fast"
        data["presets"]["fast"]["my-agent"] = {"model": "x/y"}
        data["presets"]["normal"]["explorer"]["model"] = "user/chosen-model"
        write_json(self.t / PRESET, data)
        (self.t / SLIM / "mine_append.md").write_text("my own prompt\n", encoding="utf-8")
        (self.t / SLIM / "fast" / "mine_append.md").write_text("my own fast prompt\n", encoding="utf-8")
        append(self.t / "skills" / "code-assurance" / "SKILL.md", "\nmy tweak\n")
        (self.t / "skills" / "code-assurance" / "my-notes.md").write_text("mine\n", encoding="utf-8")
        append(self.t / SLIM / "oracle_append.md", "\nmy oracle rule\n")
        (self.t / "skills" / "my-own-skill").mkdir()
        (self.t / "skills" / "my-own-skill" / "SKILL.md").write_text("# mine\n", encoding="utf-8")
        (self.t / "agents").mkdir()
        (self.t / "agents" / "mine.md").write_text("# my agent\n", encoding="utf-8")

    def toolkit_update(self):
        self.edit_kit("commands/flow.md", "\nnew flow text\n")
        self.edit_kit("skills/grilling/SKILL.md", "\nnew grilling text\n")
        self.edit_kit("skills/code-assurance/SKILL.md", "\nnew toolkit text\n")
        self.edit_kit(f"config/{SLIM}/oracle_append.md", "\nnew toolkit oracle rule\n")
        self.edit_kit(f"config/{SLIM}/verifier_append.md", "\nnew verifier rule\n")
        (self.kit / "skills" / "brand-new").mkdir()
        (self.kit / "skills" / "brand-new" / "SKILL.md").write_text("# new skill\n", encoding="utf-8")

        def change(data):
            data["presets"]["fast"]["new-agent"] = {"model": "toolkit/new"}
            data["presets"]["normal"]["new-agent"] = {"model": "toolkit/new"}
            data["presets"]["fast"]["verifier"]["model"] = "toolkit/better-verifier"
            data["presets"]["normal"]["explorer"]["model"] = "toolkit/other-explorer"
            data["presets"]["extra"] = {"orchestrator": {"model": "toolkit/o"}}
            data["new_key"] = 1
        self.kit_json(change)


class FirstInstall(InstallCase):
    def test_first_install_copies_toolkit_files(self):
        out = self.sh().stdout
        self.assertIn("item(s) installed", out)
        # the mirror holds portable.TOKEN where the installed file names this folder
        mirror = (self.kit / "config" / PRESET).read_bytes()
        # (a JSON file holds the path escaped: a Windows path has backslashes)
        self.assertEqual((self.t / PRESET).read_bytes(),
                         portable.render(mirror.decode("utf-8"), str(self.t), True).encode("utf-8"))
        self.assertEqual((self.t / "skills" / "grilling" / "SKILL.md").read_bytes(),
                         (self.kit / "skills" / "grilling" / "SKILL.md").read_bytes())
        self.assertTrue((self.t / SLIM / "fast").is_dir())
        self.assertTrue((self.t / "commands" / "flow.md").is_file())
        self.assertTrue((self.t / installer.MANIFEST).is_file())
        self.assertFalse((self.t / "backups").exists())

    def test_dry_run_writes_nothing(self):
        out = self.sh("--dry-run").stdout
        self.assertIn("would change", out)
        self.assertFalse(self.t.exists())

    def test_second_install_changes_nothing(self):
        self.sh()
        self.assertIn("Nothing to change.", self.sh().stdout)
        self.assertFalse((self.t / "backups").exists())

    def test_existing_agents_file_is_kept_and_toolkit_copy_is_beside(self):
        self.t.mkdir(parents=True)
        (self.t / "AGENTS.md").write_text("# mine\n", encoding="utf-8")
        out = self.sh().stdout
        self.assertEqual((self.t / "AGENTS.md").read_text(encoding="utf-8"), "# mine\n")
        self.assertTrue((self.t / "AGENTS.md.toolkit-new").is_file())
        self.assertIn("AGENTS.md.toolkit-new", out)


class Update(InstallCase):
    def setUp(self):
        super().setUp()
        self.sh()
        self.customize()
        self.toolkit_update()
        self.out = self.sh().stdout

    def test_preset_agents_and_edited_values_survive(self):
        data = read_json(self.t / PRESET)
        self.assertEqual(data["preset"], "fast")
        self.assertIn("my-agent", data["presets"]["fast"])
        self.assertEqual(data["presets"]["normal"]["explorer"]["model"], "user/chosen-model")

    def test_preset_file_gets_toolkit_additions_and_untouched_values(self):
        data = read_json(self.t / PRESET)
        self.assertEqual(data["presets"]["fast"]["new-agent"]["model"], "toolkit/new")
        self.assertIn("new-agent", data["presets"]["normal"])
        self.assertEqual(data["presets"]["fast"]["verifier"]["model"], "toolkit/better-verifier")
        self.assertIn("extra", data["presets"])
        self.assertEqual(data["new_key"], 1)

    def test_user_created_files_are_never_deleted(self):
        for rel in (f"{SLIM}/mine_append.md", f"{SLIM}/fast/mine_append.md", "skills/code-assurance/my-notes.md",
                    "skills/my-own-skill/SKILL.md", "agents/mine.md"):
            self.assertTrue((self.t / rel).is_file(), rel)

    def test_edited_files_keep_the_users_text_with_toolkit_copy_beside(self):
        skill = self.t / "skills" / "code-assurance" / "SKILL.md"
        self.assertIn("my tweak", skill.read_text(encoding="utf-8"))
        self.assertNotIn("new toolkit text", skill.read_text(encoding="utf-8"))
        self.assertIn("new toolkit text", Path(str(skill) + ".toolkit-new").read_text(encoding="utf-8"))
        oracle = self.t / SLIM / "oracle_append.md"
        self.assertIn("my oracle rule", oracle.read_text(encoding="utf-8"))
        self.assertIn("new toolkit oracle rule", Path(str(oracle) + ".toolkit-new").read_text(encoding="utf-8"))
        self.assertIn("oracle_append.md.toolkit-new", self.out)

    def test_untouched_files_get_the_update_and_new_files_arrive(self):
        self.assertIn("new flow text", (self.t / "commands" / "flow.md").read_text(encoding="utf-8"))
        self.assertIn("new grilling text", (self.t / "skills" / "grilling" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("new verifier rule", (self.t / SLIM / "verifier_append.md").read_text(encoding="utf-8"))
        self.assertTrue((self.t / "skills" / "brand-new" / "SKILL.md").is_file())
        self.assertFalse((self.t / "commands" / "flow.md.toolkit-new").exists())

    def test_replaced_files_are_backed_up_and_restorable(self):
        listing = self.sh("--list-backups").stdout
        self.assertIn("pre-toolkit", listing)
        name = listing.split()[0]
        backup = self.t / "backups" / name
        self.assertIn("commands/flow.md", {p.relative_to(backup).as_posix() for p in backup.rglob("*") if p.is_file()})
        preview = self.sh("--restore", name, "--dry-run").stdout
        self.assertIn("would be restored", preview)
        self.assertIn("new flow text", (self.t / "commands" / "flow.md").read_text(encoding="utf-8"))
        self.sh("--restore", name)
        self.assertNotIn("new flow text", (self.t / "commands" / "flow.md").read_text(encoding="utf-8"))
        self.assertEqual(read_json(self.t / PRESET)["preset"], "fast")  # the backup holds the file as it was
        self.assertTrue(any("pre-restore" in p.name for p in (self.t / "backups").iterdir()))

    def test_a_third_install_is_quiet_and_keeps_the_copies_beside(self):
        again = self.sh().stdout
        self.assertIn("Nothing to change.", again)
        self.assertTrue((self.t / "skills" / "code-assurance" / "SKILL.md.toolkit-new").is_file())

    def test_merging_by_hand_clears_the_toolkit_copy(self):
        skill = self.t / "skills" / "code-assurance" / "SKILL.md"
        shutil.copy2(self.kit / "skills" / "code-assurance" / "SKILL.md", skill)
        self.sh()
        self.assertFalse(Path(str(skill) + ".toolkit-new").exists())


class Edges(InstallCase):
    def test_a_file_you_removed_is_not_brought_back_until_the_toolkit_changes_it(self):
        self.sh()
        (self.t / "commands" / "note.md").unlink()
        self.assertIn("Nothing to change.", self.sh().stdout)
        self.assertFalse((self.t / "commands" / "note.md").exists())
        self.edit_kit("commands/note.md", "\nchanged\n")
        self.sh()
        self.assertFalse((self.t / "commands" / "note.md").exists())
        self.assertTrue((self.t / "commands" / "note.md.toolkit-new").is_file())

    def test_install_over_an_older_install_without_manifest_keeps_differing_files(self):
        self.sh()
        (self.t / installer.MANIFEST).unlink()
        append(self.t / "commands" / "flow.md", "mine\n")
        before = (self.t / "commands" / "spend.md").read_text(encoding="utf-8")
        self.edit_kit("commands/spend.md", "\nnew\n")
        self.sh()
        self.assertIn("mine", (self.t / "commands" / "flow.md").read_text(encoding="utf-8"))
        self.assertEqual((self.t / "commands" / "spend.md").read_text(encoding="utf-8"), before)
        self.assertTrue((self.t / "commands" / "spend.md.toolkit-new").is_file())

    def test_invalid_preset_file_is_left_alone(self):
        self.sh()
        (self.t / PRESET).write_text("{ not json", encoding="utf-8")
        self.kit_json(lambda d: d.update(new_key=2))
        proc = self.sh()
        self.assertEqual((self.t / PRESET).read_text(encoding="utf-8"), "{ not json")
        self.assertTrue((self.t / (PRESET + ".toolkit-new")).is_file())
        self.assertIn("left alone", proc.stdout)

    @needs_symlinks
    def test_target_folder_that_is_a_link_is_installed_into(self):
        real = self.base / "real"
        real.mkdir()
        (self.home / ".config").mkdir()
        os.symlink(real, self.t, target_is_directory=True)
        self.sh()
        self.assertTrue((real / "commands" / "flow.md").is_file())
        self.assertTrue((real / installer.MANIFEST).is_file())
        self.assertIn("Nothing to change.", self.sh().stdout)

    @needs_symlinks
    def test_a_linked_parent_of_the_target_is_installed_through(self):
        real = self.base / "dotfiles"
        real.mkdir()
        os.symlink(real, self.home / ".config", target_is_directory=True)
        self.sh()
        self.assertTrue((real / "opencode" / "commands" / "flow.md").is_file())

    @needs_symlinks
    def test_links_below_the_target_are_still_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.t.mkdir(parents=True)
        os.symlink(outside, self.t / "skills", target_is_directory=True)
        self.assertNotEqual(self.sh(check=False).returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_restore_refuses_other_folders(self):
        self.sh()
        (self.t / "backups" / "2026-01-01-skill-files").mkdir(parents=True)
        proc = self.sh("--restore", "2026-01-01-skill-files", check=False)
        self.assertNotEqual(proc.returncode, 0)
        proc = self.sh("--restore", "../../../etc", check=False)
        self.assertNotEqual(proc.returncode, 0)

    def test_unknown_option_is_refused(self):
        self.assertNotEqual(self.sh("--bogus", check=False).returncode, 0)
        self.assertFalse(self.t.exists())


class Safety(InstallCase):
    """Writes land whole or not at all; a file the user started merging is never overwritten; restore is honest."""

    def setUp(self):
        super().setUp()
        self.sh()
        self.edit_kit("commands/flow.md", "\nnew flow text\n")

    def run_installer(self):
        return installer.Installer(self.kit, self.t, ["flow"], out=lambda *_: None).run()

    def fail_replace(self, only=None):
        real = os.replace

        def replace(src, dst, *args, **kw):
            if only is None or str(dst).endswith(only):
                raise OSError("injected")
            return real(src, dst, *args, **kw)
        return mock.patch.object(installer.os, "replace", replace)

    def names(self, folder):
        return sorted(p.name for p in folder.iterdir())

    def test_a_failed_file_write_leaves_the_old_file_whole(self):
        flow = self.t / "commands" / "flow.md"
        before, listing = flow.read_bytes(), self.names(flow.parent)
        with self.fail_replace(), self.assertRaises(OSError):
            self.run_installer()
        self.assertEqual(flow.read_bytes(), before)
        self.assertEqual(self.names(flow.parent), listing)

    def test_a_failed_manifest_write_leaves_the_old_manifest_whole(self):
        manifest = self.t / installer.MANIFEST
        before = manifest.read_bytes()
        with self.fail_replace(installer.MANIFEST), self.assertRaises(OSError):
            self.run_installer()
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual([n for n in self.names(self.t) if n.startswith(".toolkit-install")], [installer.MANIFEST])

    def test_a_failed_merge_write_leaves_the_preset_file_whole(self):
        self.kit_json(lambda d: d.update(new_key=1))
        preset = self.t / PRESET
        before = preset.read_bytes()
        with self.fail_replace(PRESET), self.assertRaises(OSError):
            self.run_installer()
        self.assertEqual(preset.read_bytes(), before)

    @posix_only
    def test_a_merge_keeps_the_file_mode(self):
        self.kit_json(lambda d: d.update(new_key=1))
        os.chmod(self.t / PRESET, 0o600)
        self.run_installer()
        self.assertEqual((self.t / PRESET).stat().st_mode & 0o777, 0o600)
        self.assertEqual(read_json(self.t / PRESET)["new_key"], 1)

    def test_a_failed_restore_write_leaves_the_file_whole(self):
        self.sh()
        name = self.sh("--list-backups").stdout.split()[0]
        flow = self.t / "commands" / "flow.md"
        before = flow.read_bytes()
        with self.fail_replace(), self.assertRaises(OSError):
            installer.restore(self.t, name, out=lambda *_: None)
        self.assertEqual(flow.read_bytes(), before)

    def test_a_toolkit_copy_you_started_merging_is_backed_up_not_replaced(self):
        flow = self.t / "commands" / "flow.md"
        append(flow, "my edit\n")
        self.sh()
        beside = self.t / "commands" / "flow.md.toolkit-new"
        append(beside, "my merge in progress\n")
        self.edit_kit("commands/flow.md", "\nthird version\n")
        out = self.sh().stdout
        self.assertIn("third version", beside.read_text(encoding="utf-8"))
        self.assertNotIn("my merge in progress", beside.read_text(encoding="utf-8"))
        kept = [p for p in (self.t / "backups").rglob("flow.md.toolkit-new")]
        self.assertEqual(len(kept), 1)
        self.assertIn("my merge in progress", kept[0].read_text(encoding="utf-8"))
        self.assertIn("backups", out)
        self.assertIn("flow.md.toolkit-new", out)

    def test_restore_then_install_keeps_the_restored_file_and_brings_the_toolkit_version(self):
        self.sh()  # flow.md is now the toolkit's new version; the old one is in a backup
        flow = self.t / "commands" / "flow.md"
        name = self.sh("--list-backups").stdout.split()[0]
        old_text = (self.t / "backups" / name / "commands" / "flow.md").read_text(encoding="utf-8")
        out = self.sh("--restore", name).stdout
        self.assertEqual(flow.read_text(encoding="utf-8"), old_text)
        self.assertIn("differ from the toolkit", out)
        self.assertIn(".toolkit-new", out)
        out = self.sh().stdout
        self.assertEqual(flow.read_text(encoding="utf-8"), old_text)
        self.assertEqual((self.t / "commands" / "flow.md.toolkit-new").read_bytes(),
                         (self.kit / "commands" / "flow.md").read_bytes())
        self.assertIn("flow.md.toolkit-new", out)

    @needs_symlinks
    def test_restore_refuses_a_backups_folder_that_is_a_link(self):
        self.sh()
        name = self.sh("--list-backups").stdout.split()[0]
        moved = self.base / "elsewhere"
        shutil.move(str(self.t / "backups"), str(moved))
        os.symlink(moved, self.t / "backups", target_is_directory=True)
        proc = self.sh("--restore", name, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("symlink", proc.stderr)


class InstallCmdPath(InstallCase):
    """The same work through install.cmd's Python entry (agentgraph.installcli): it runs on every system."""

    def test_first_install_is_complete_and_the_second_run_changes_nothing(self):
        self.cli()
        self.assertTrue((self.t / "commands" / "flow.md").is_file())
        self.assertTrue((self.t / installer.MANIFEST).is_file())
        self.assertIn("Nothing to change.", self.cli().stdout)

    def test_dry_run_writes_nothing(self):
        proc = self.cli("--dry-run")
        self.assertIn("Nothing was written", proc.stdout)
        self.assertFalse(self.t.exists())

    def test_a_replaced_file_is_backed_up_and_can_be_restored(self):
        self.cli()
        self.edit_kit("commands/flow.md", "\nnew toolkit line\n")
        self.cli()
        name = self.cli("--list-backups").stdout.split()[0]
        self.assertIn("-pre-toolkit.", name)
        self.cli("--restore", name)
        self.assertNotIn("new toolkit line", (self.t / "commands" / "flow.md").read_text(encoding="utf-8"))

    def test_a_bad_option_and_a_path_as_a_backup_name_are_refused(self):
        self.assertEqual(self.cli("--bogus", check=False).returncode, 2)
        self.cli()
        self.assertNotEqual(self.cli("--restore", "..\\..\\..\\etc", check=False).returncode, 0)
        self.assertNotEqual(self.cli("--restore", "../../../etc", check=False).returncode, 0)


@unittest.skipUnless(shutil.which("rsync") and shutil.which("sh"), "sync.sh needs rsync and sh (it is a Unix-only, optional tool)")
class Sync(InstallCase):
    """sync.sh takes the author's heading and skill exclusions from sync.conf, not from its own source."""

    def setUp(self):
        super().setUp()
        shutil.copy2(ROOT / "sync.sh", self.kit / "sync.sh")
        # sync.sh also runs agentgraph.policygen against config/policy, so the copy needs them
        shutil.copytree(ROOT / "agentgraph", self.kit / "agentgraph", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "config" / "policy", self.kit / "config" / "policy", dirs_exist_ok=True)
        self.sh()
        (self.t / "skills" / "private-one").mkdir()
        (self.t / "skills" / "private-one" / "SKILL.md").write_text("private\n", encoding="utf-8")
        (self.t / "AGENTS.md").write_text("# Rules\nshared rule\n# My notes\nsecret note\n", encoding="utf-8")

    def sync(self):
        proc = subprocess.run(["sh", str(self.kit / "sync.sh")], env=self.env(), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def test_without_sync_conf_nothing_author_specific_is_assumed(self):
        before = (self.kit / "config" / "AGENTS.md").read_bytes()
        self.assertIn("AGENTS_CUT_MARKER is not set", self.sync())
        self.assertEqual((self.kit / "config" / "AGENTS.md").read_bytes(), before)
        self.assertTrue((self.kit / "skills" / "private-one").is_dir())

    def test_sync_conf_sets_the_heading_and_the_exclusions(self):
        (self.kit / "sync.conf").write_text('SEPARATE_SKILLS="private-one"\nAGENTS_CUT_MARKER="# My notes"\n', encoding="utf-8")
        self.sync()
        self.assertEqual((self.kit / "config" / "AGENTS.md").read_text(encoding="utf-8"), "# Rules\nshared rule\n")
        self.assertFalse((self.kit / "skills" / "private-one").exists())

    def test_the_authors_conf_keeps_todays_values(self):
        text = (ROOT / "sync.conf.example").read_text(encoding="utf-8")
        self.assertIn("SEPARATE_SKILLS=", text)
        self.assertIn("AGENTS_CUT_MARKER=", text)
        self.assertIn("sync.conf", (ROOT / ".gitignore").read_text(encoding="utf-8").split())


class MergeRules(unittest.TestCase):
    M = installer._MISSING

    def test_user_value_wins_when_both_changed(self):
        self.assertEqual(installer.merge3({"a": 1}, {"a": 2}, {"a": 3}), {"a": 3})

    def test_untouched_value_follows_toolkit(self):
        self.assertEqual(installer.merge3({"a": 1}, {"a": 2}, {"a": 1}), {"a": 2})

    def test_user_deletion_stays_and_new_toolkit_key_arrives(self):
        self.assertEqual(installer.merge3({"a": 1, "b": 1}, {"a": 1, "b": 1, "c": 1}, {"a": 1}), {"a": 1, "c": 1})

    def test_lists_are_the_users_when_changed(self):
        self.assertEqual(installer.merge3({"l": [1]}, {"l": [1, 2]}, {"l": [9]}), {"l": [9]})
        self.assertEqual(installer.merge3({"l": [1]}, {"l": [1, 2]}, {"l": [1]}), {"l": [1, 2]})

    def test_no_base_adds_missing_keys_and_keeps_user_values(self):
        self.assertEqual(installer.merge3(self.M, {"a": 1, "b": {"c": 2}}, {"a": 5}), {"a": 5, "b": {"c": 2}})


if __name__ == "__main__":
    unittest.main()
