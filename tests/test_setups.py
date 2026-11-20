"""The default preset and saved setups (agentgraph.setups): the commands, POST /api/setup and the snapshot's `setup`
field, on disposable copies only. The real preset file, SKILLS.md, agents folder, structure folder, commands folder
and setups folder are never written."""
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from agentgraph import agentfiles, demo, flowcommands, server, setups, skillset
from agentgraph.snapshot_json import snapshot_document

SCRATCH = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode/setups-tests")
ROOT = Path(__file__).resolve().parent.parent
REAL = (skillset.CONFIG, skillset.MAP)
REAL_FOLDERS = (agentfiles.AGENTS_DIR, flowcommands.COMMANDS_DIR, setups.SETUPS, skillset.BACKUPS)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def listing(folder):
    folder = Path(folder)
    if not folder.exists():
        return None
    return sorted((str(p.relative_to(folder)), p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file())


FIRST = ({path: digest(path) for path in REAL}, {folder: listing(folder) for folder in REAL_FOLDERS})


def tearDownModule():
    files, folders = FIRST
    assert {path: digest(path) for path in REAL} == files, "a real file changed during the tests"
    assert {folder: listing(folder) for folder in REAL_FOLDERS} == folders, "a real folder changed during the tests"


# Plain JSON with its own spacing: the presets before the active name, odd spaces, a CRLF-free tab, unicode escapes.
PRESET = (b'{ "$schema":"https://example.invalid/schema.json",\n'
          b'\t"presets" : {\n'
          b'   "normal": {"orchestrator": {"model": "a/one", "skills": ["x"]}},\n'
          b'   "fa\\u0073t":{ "orchestrator":{"model":"a/two","variant":"high"} } ,\n'
          b'   "cheap": {"orchestrator": {"model": "a/three"}}\n'
          b' },\n'
          b'   "preset"  :   "normal"  ,\n'
          b' "disabled_agents": ["z"] }\n')
MAP = (b"# Skills\n\n| Agent | Skills |\n|---|---|\n| `orchestrator` | `x` |\n")
AGENT_FILE = b"---\n# agentgraph: added\ndescription: One\n---\nDo one thing.\n"
COMMAND_FILE = b"---\n# agentgraph: flow command\ndescription: Path\n---\nRun the path.\n"


def tree_hash(root, skip=()):
    """One SHA-256 over every file under `root` (name, mode and bytes), but the top-level names in `skip`."""
    digest_ = hashlib.sha256()
    root = Path(root)
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] in skip:
            continue
        if path.is_file():
            digest_.update(f"{relative.as_posix()}\0{stat.S_IMODE(path.stat().st_mode):o}\0".encode())
            digest_.update(path.read_bytes())
        else:
            digest_.update(f"{relative.as_posix()}/\0".encode())
    return digest_.hexdigest()


class Scratch(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / "oh-my-opencode-slim.json"
        self.map = self.root / "SKILLS.md"
        self.agents = self.root / "agents"
        self.structure = self.root / "structure"
        self.commands = self.root / "commands"
        self.setups = self.root / "setups"
        self.backups = self.root / "backups"
        self.project = self.root / "project"
        for folder in (self.agents, self.structure / "30-build", self.structure / "flows", self.commands,
                       self.project):
            folder.mkdir(parents=True)
        self.config.write_bytes(PRESET)
        self.map.write_bytes(MAP)
        (self.agents / "helper.md").write_bytes(AGENT_FILE)
        (self.agents / "mine.md").write_bytes(b"---\ndescription: hand written\n---\nNot ours.\n")
        (self.structure / "30-build" / "helper.md").write_bytes(b"use_when: building\n\nHelps.\n")
        (self.structure / "flows" / "ship.md").write_bytes(b"name: Ship\n\nShip it.\n")
        (self.structure / "README.md").write_bytes(b"readme\n")
        (self.commands / "ship.md").write_bytes(COMMAND_FILE)
        (self.commands / "other.md").write_bytes(b"not ours\n")
        self.addCleanup(self.assert_real_untouched)

    def assert_real_untouched(self):
        self.assertEqual({path: digest(path) for path in REAL}, FIRST[0], "a real file was changed")

    @property
    def paths(self):
        return {"config": self.config, "map_path": self.map, "agents_dir": self.agents, "structure": self.structure,
                "commands_dir": self.commands, "setups": self.setups, "backups": self.backups}

    def managed(self):
        """The hash of everything a setup covers and the rest of the scratch folder (not setups, backups, project)."""
        return tree_hash(self.root, skip=("setups", "backups", "project"))

    def calls(self):
        return {"project": self.project, "env": {}, **self.paths}

    def switch(self, name, **extra):
        return setups.switch_preset(name, **{**self.calls(), **extra})

    def preset(self):
        return json.loads(self.config.read_bytes())["preset"]


class PresetTest(Scratch):
    def test_show(self):
        self.assertEqual(setups.show_preset(**self.calls()),
                         {"preset": "normal", "presets": ["normal", "fast", "cheap"], "override": None,
                          "effective": "normal"})

    def test_show_names_the_override_by_name_only(self):
        shown = setups.show_preset(**{**self.calls(), "env": {"OH_MY_OPENCODE_SLIM_PRESET": "cheap"}})
        self.assertEqual((shown["override"], shown["effective"], shown["preset"]), ("env", "cheap", "normal"))
        (self.project / ".opencode").mkdir()
        (self.project / ".opencode" / "oh-my-opencode-slim.jsonc").write_text(
            '{\n // comment\n "preset": "fast", "secret": "do-not-print",\n}\n')
        shown = setups.show_preset(**self.calls())
        self.assertEqual((shown["override"], shown["effective"]), ("project", "fast"))
        self.assertNotIn("do-not-print", json.dumps(shown))
        # a project folder below the one that has the config sees it too
        deeper = self.project / "a" / "b"
        deeper.mkdir(parents=True)
        shown = setups.show_preset(**{**self.calls(), "project": deeper})
        self.assertEqual(shown["override"], "project")
        # the environment wins over a project config
        shown = setups.show_preset(**{**self.calls(), "env": {"OH_MY_OPENCODE_SLIM_PRESET": "cheap"}})
        self.assertEqual((shown["override"], shown["effective"]), ("env", "cheap"))

    def test_switch_changes_only_the_one_value(self):
        before = self.config.read_bytes()
        result = self.switch("cheap")
        after = self.config.read_bytes()
        self.assertEqual((result["was"], result["preset"], result["changed"]), ("normal", "cheap", True))
        self.assertIn("next start of OpenCode", result["message"])
        self.assertEqual(before.replace(b'"preset"  :   "normal"  ,', b'"preset"  :   "cheap"  ,'), after)
        self.assertEqual(len(before) - len(after), len(b"normal") - len(b"cheap"))
        self.assertEqual(self.preset(), "cheap")
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertEqual(Path(result["previous"]).read_bytes(), before)
        self.assertTrue(Path(result["record"]).is_file())

    def test_switch_keeps_the_names_spelling(self):
        # "fast" is written as "fa\u0073t" in the presets: the preset value is the spelling of the presets key
        self.switch("fast")
        self.assertIn(b'"preset"  :   "fa\\u0073t"  ,', self.config.read_bytes())

    def test_switch_on_the_real_file_shape(self):
        shutil.copyfile(skillset.CONFIG, self.config)
        before = self.config.read_bytes()
        data = json.loads(before)
        other = next(name for name in data["presets"] if name != data["preset"])
        self.switch(other)
        after = self.config.read_bytes()
        self.assertEqual(json.loads(after), {**data, "preset": other})
        self.assertEqual(len(before.splitlines()), len(after.splitlines()))
        differing = [i for i, (a, b) in enumerate(zip(before.splitlines(), after.splitlines())) if a != b]
        self.assertEqual(len(differing), 1)

    def test_refusals_write_nothing(self):
        before = self.managed()
        for name, message in (("nope", "unknown preset"), ("normal", "already the default"),
                              ("", "invalid"), ("-x", "invalid"), (7, "invalid")):
            with self.subTest(name=name):
                with self.assertRaises(setups.SetupError) as raised:
                    self.switch(name)
                self.assertIn(message, str(raised.exception))
        with self.assertRaises(setups.SetupError) as raised:
            self.switch("fast", env={"OH_MY_OPENCODE_SLIM_PRESET": "cheap"})
        self.assertIn("environment variable", str(raised.exception))
        (self.project / ".opencode").mkdir()
        (self.project / ".opencode" / "oh-my-opencode-slim.json").write_text('{"preset": "cheap"}')
        with self.assertRaises(setups.SetupError) as raised:
            self.switch("fast")
        self.assertIn("project config", str(raised.exception))
        self.assertEqual(self.managed(), before)
        self.assertFalse(self.backups.exists())

    def test_an_override_that_sets_the_same_name_does_not_block(self):
        self.switch("fast", env={"OH_MY_OPENCODE_SLIM_PRESET": "fast"})
        self.assertEqual(self.preset(), "fast")

    def test_restore_is_byte_for_byte_and_refused_when_stale(self):
        before = self.config.read_bytes()
        result = self.switch("cheap")
        restored = setups.restore_preset(result["record"], **self.paths)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(restored["changed"])
        # the file changed since the switch: refused, nothing written
        again = self.switch("cheap")
        self.config.write_bytes(self.config.read_bytes() + b"\n")
        edited = self.config.read_bytes()
        with self.assertRaises(agentfiles.StaleError):
            setups.restore_preset(again["record"], **self.paths)
        self.assertEqual(self.config.read_bytes(), edited)

    def test_restore_of_a_restore_switches_again(self):
        first = self.switch("cheap")
        back = setups.restore_preset(first["record"], **self.paths)
        self.assertEqual(self.preset(), "normal")
        setups.restore_preset(back["record"], **self.paths)
        self.assertEqual(self.preset(), "cheap")

    def test_restore_refuses_another_kind_of_record_and_stray_paths(self):
        saved = setups.save("one", **self.paths)
        with self.assertRaises(setups.SetupError):
            setups.restore_preset(saved["record"], **self.paths)
        for bad in ("", "/etc/passwd", str(self.root / "x"), "../x", None, 5, "a\0b"):
            with self.subTest(bad=bad):
                with self.assertRaises(setups.SetupError):
                    setups.restore_preset(bad, **self.paths)

    def test_a_killed_write_leaves_the_original(self):
        before = self.config.read_bytes()
        with patch("agentgraph.skillset.os.replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.switch("fast")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".")], [])
        # and no record is left to restore from
        self.assertFalse(list(self.backups.glob("*-setups/*")))

    def test_a_switch_refuses_a_file_that_is_not_json(self):
        self.config.write_bytes(b"{ not json")
        before = self.managed()
        with self.assertRaises(skillset.SkillsetError):
            self.switch("fast")
        self.assertEqual(self.managed(), before)


class SetupTest(Scratch):
    def test_save_list_show_remove(self):
        saved = setups.save("work", **self.paths)
        self.assertEqual((saved["name"], saved["preset"], saved["replaced"]), ("work", "normal", False))
        folder = self.setups / "work"
        manifest = json.loads((folder / "manifest.json").read_text())
        keys = sorted(entry["key"] for entry in manifest["files"])
        self.assertEqual(keys, ["agents/helper.md", "commands/ship.md", "map", "preset", "structure/30-build/helper.md",
                                "structure/flows/ship.md"])
        for entry in manifest["files"]:
            data = (folder / "files" / entry["key"]).read_bytes()
            self.assertEqual((entry["sha256"], entry["size"]), (hashlib.sha256(data).hexdigest(), len(data)))
        self.assertEqual((folder / "files" / "preset").read_bytes(), PRESET)
        self.assertEqual(saved["counts"], {"preset": 1, "map": 1, "agents": 1, "structure": 2, "commands": 1})
        listed = setups.list_setups(**self.paths)
        self.assertEqual([(item["name"], item["preset"], item["files"]) for item in listed], [("work", "normal", 6)])
        shown = setups.show_setup("work", **self.paths)
        self.assertEqual((shown["name"], shown["preset"], shown["files"]), ("work", "normal", 6))
        self.assertEqual(shown["counts"], saved["counts"])
        self.assertNotIn("Do one thing", json.dumps(shown) + json.dumps(listed))
        self.assertNotIn("hand written", (folder / "manifest.json").read_text())
        removed = setups.remove("work", **self.paths)
        self.assertFalse(folder.exists())
        self.assertEqual(setups.list_setups(**self.paths), [])
        with self.assertRaises(setups.SetupError):
            setups.show_setup("work", **self.paths)
        setups.undo(removed["record"], **self.paths)
        self.assertEqual(json.loads((folder / "manifest.json").read_text())["name"], "work")
        self.assertEqual([item["name"] for item in setups.list_setups(**self.paths)], ["work"])

    def test_hand_written_files_are_never_read(self):
        setups.save("work", **self.paths)
        files = [path.relative_to(self.setups / "work" / "files").as_posix()
                 for path in (self.setups / "work" / "files").rglob("*") if path.is_file()]
        self.assertNotIn("agents/mine.md", files)
        self.assertNotIn("commands/other.md", files)
        self.assertFalse([name for name in files if "README" in name])

    def test_a_name_in_use_is_refused_unless_replaced(self):
        setups.save("work", **self.paths)
        before = (self.setups / "work" / "manifest.json").read_bytes()
        with self.assertRaises(setups.SetupError) as raised:
            setups.save("work", **self.paths)
        self.assertIn("already exists", str(raised.exception))
        self.assertEqual((self.setups / "work" / "manifest.json").read_bytes(), before)
        self.switch("cheap")
        replaced = setups.save("work", replace=True, **self.paths)
        self.assertTrue(replaced["replaced"])
        self.assertEqual(setups.show_setup("work", **self.paths)["preset"], "cheap")
        # the old one is in the record: undoing the save brings it back
        setups.undo(replaced["record"], **self.paths)
        self.assertEqual(setups.show_setup("work", **self.paths)["preset"], "normal")

    def test_names_are_plain_ids(self):
        before = self.managed()
        for name in ("../x", "a/b", "..", ".", "", "A", "-x", "x y", "x\0", "a" * 81, "/abs", "x/../y", None, 3,
                     "con\u202e", ".hidden"):
            with self.subTest(name=name):
                for call in (setups.save, setups.show_setup, setups.remove, setups.restore):
                    with self.assertRaises(setups.SetupError):
                        call(name, **self.paths)
        self.assertEqual(self.managed(), before)
        self.assertFalse(self.setups.exists() and list(self.setups.iterdir()))
        self.assertEqual([p.name for p in self.root.parent.iterdir() if p.name == "x"], [])

    def test_restore_adds_changes_and_removes_files_then_undo_is_exact(self):
        (self.agents / "mine.md").write_bytes(b"hand written\n")  # never managed: stays as it is
        setups.save("a", **self.paths)
        start = self.managed()
        # now: change a file, remove one, add managed ones, move the preset
        (self.agents / "helper.md").write_bytes(AGENT_FILE + b"changed\n")
        (self.structure / "flows" / "ship.md").unlink()
        (self.agents / "newer.md").write_bytes(b"---\n# agentgraph: added\nnew\n---\nNew.\n")
        (self.structure / "teams").mkdir()
        (self.structure / "teams" / "crew.md").write_bytes(b"lead: helper\n")
        (self.commands / "newpath.md").write_bytes(COMMAND_FILE + b"x\n")
        self.map.write_bytes(MAP + b"| `helper` | none |\n")
        self.switch("cheap")
        busy = self.managed()
        self.assertNotEqual(busy, start)
        result = setups.restore("a", **self.paths)
        self.assertTrue(result["changed"])
        self.assertEqual(result["removed"], 3)
        self.assertIn("next start of OpenCode", result["message"])
        self.assertFalse((self.agents / "newer.md").exists())
        self.assertFalse((self.structure / "teams" / "crew.md").exists())
        self.assertFalse((self.structure / "teams").exists())  # a folder the setup did not have goes with its files
        self.assertFalse((self.commands / "newpath.md").exists())
        self.assertEqual((self.agents / "mine.md").read_bytes(), b"hand written\n")
        self.assertEqual(self.config.read_bytes(), PRESET)
        self.assertEqual(self.managed(), start)
        # restoring again is a no-op
        self.assertFalse(setups.restore("a", **self.paths)["changed"])
        undone = setups.undo(result["record"], **self.paths)
        self.assertTrue(undone["changed"])
        self.assertEqual(self.managed(), busy)
        self.assertEqual(self.preset(), "cheap")
        self.assertTrue((self.structure / "teams" / "crew.md").is_file())
        # the undo is undoable too: the whole tree goes round
        again = setups.undo(undone["record"], **self.paths)
        self.assertEqual(self.managed(), start)
        self.assertTrue(again["changed"])

    def test_undo_removes_a_folder_the_restore_created_and_keeps_one_that_was_there(self):
        setups.save("a", **self.paths)
        shutil.rmtree(self.structure / "flows")
        before = self.managed()
        dirs_before = sorted(p.name for p in self.structure.iterdir())
        result = setups.restore("a", **self.paths)
        self.assertTrue((self.structure / "flows" / "ship.md").is_file())
        setups.undo(result["record"], **self.paths)
        self.assertEqual(sorted(p.name for p in self.structure.iterdir()), dirs_before)
        self.assertEqual(self.managed(), before)
        (self.structure / "flows").mkdir()  # an empty folder that was there stays when the restore adds and undo removes
        before = self.managed()
        result = setups.restore("a", **self.paths)
        setups.undo(result["record"], **self.paths)
        self.assertEqual(self.managed(), before)

    def test_the_whole_tree_hash_is_identical_after_restore_and_undo(self):
        setups.save("a", **self.paths)
        self.switch("cheap")
        (self.agents / "helper.md").unlink()
        (self.commands / "ship.md").write_bytes(COMMAND_FILE + b"more\n")
        busy = tree_hash(self.root, skip=("setups",))
        result = setups.restore("a", **self.paths)
        self.assertNotEqual(tree_hash(self.root, skip=("setups", "backups")), busy)
        setups.undo(result["record"], **self.paths)
        # the backups folder holds the records, so it is left out of both sides
        self.assertEqual(tree_hash(self.root, skip=("setups", "backups")),
                         tree_hash(self.root, skip=("setups", "backups")))
        self.assertEqual(self.preset(), "cheap")
        self.assertFalse((self.agents / "helper.md").exists())
        self.assertEqual((self.commands / "ship.md").read_bytes(), COMMAND_FILE + b"more\n")

    def test_restore_keeps_modes(self):
        os.chmod(self.agents / "helper.md", 0o600)
        setups.save("a", **self.paths)
        (self.agents / "helper.md").unlink()
        setups.restore("a", **self.paths)
        self.assertEqual(stat.S_IMODE((self.agents / "helper.md").stat().st_mode), 0o600)

    def test_a_damaged_setup_is_refused_before_any_write(self):
        setups.save("a", **self.paths)
        self.switch("cheap")
        (self.agents / "helper.md").unlink()
        before = tree_hash(self.root, skip=("setups",))
        folder = self.setups / "a"
        pristine = self.root.parent / (self.root.name + "-pristine")
        self.addCleanup(shutil.rmtree, pristine, True)
        shutil.copytree(folder, pristine)
        victim = folder / "files" / "structure" / "flows" / "ship.md"

        def manifest_edit(change):
            manifest = json.loads((folder / "manifest.json").read_text())
            change(manifest)
            (folder / "manifest.json").write_text(json.dumps(manifest))

        damages = {
            "flipped": lambda: victim.write_bytes(victim.read_bytes() + b"x"),
            "missing": victim.unlink,
            "extra file": lambda: (folder / "files" / "agents" / "stray.md").write_bytes(b"x"),
            "manifest": lambda: (folder / "manifest.json").write_text("{not json"),
            "no preset": lambda: manifest_edit(lambda m: m.update(files=[e for e in m["files"] if e["key"] != "preset"])),
            "traversal": lambda: manifest_edit(lambda m: m["files"].append(
                {"key": "../../evil", "sha256": "0" * 64, "size": 0, "mode": 0o644})),
            "wrong name": lambda: manifest_edit(lambda m: m.update(name="b")),
        }
        for label, damage in damages.items():
            with self.subTest(damage=label):
                damage()
                with self.assertRaises(setups.SetupError) as raised:
                    setups.restore("a", **self.paths)
                self.assertIn("damaged", str(raised.exception))
                self.assertEqual(tree_hash(self.root, skip=("setups",)), before)
                self.assertFalse(list(self.backups.glob("*-setups/restore-*")))
                self.assertFalse((self.root.parent / "evil").exists())
                shutil.rmtree(folder)
                shutil.copytree(pristine, folder)
        setups.restore("a", **self.paths)  # the pristine copy still restores
        self.assertEqual(self.preset(), "normal")

    def test_a_killed_restore_leaves_the_files_as_they_were(self):
        setups.save("a", **self.paths)
        self.switch("cheap")
        (self.agents / "helper.md").write_bytes(AGENT_FILE + b"x")
        (self.structure / "flows" / "ship.md").unlink()
        (self.commands / "extra.md").write_bytes(COMMAND_FILE + b"extra")
        before = tree_hash(self.root, skip=("setups", "backups"))
        real = os.replace
        calls = {"n": 0}

        def killing(source, target, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt
            return real(source, target, *args, **kwargs)

        with patch("agentgraph.setups.os.replace", side_effect=killing):
            with self.assertRaises(KeyboardInterrupt):
                setups.restore("a", **self.paths)
        self.assertGreaterEqual(calls["n"], 3)
        self.assertEqual(tree_hash(self.root, skip=("setups", "backups")), before)
        self.assertFalse([p for p in self.root.rglob(".*") if p.is_file() and p.name.endswith(".tmp")])
        self.assertFalse(list(self.backups.glob("*-setups/restore-*")))  # the unused record went

    def test_a_killed_write_while_saving_leaves_no_setup(self):
        with patch("agentgraph.setups._move_dir", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                setups.save("a", **self.paths)
        self.assertEqual(setups.list_setups(**self.paths), [])
        self.assertEqual([p.name for p in self.setups.iterdir()], [])

    def test_undo_is_stale_when_a_covered_file_changed_since(self):
        setups.save("a", **self.paths)
        self.switch("cheap")
        result = setups.restore("a", **self.paths)
        (self.agents / "helper.md").write_bytes(AGENT_FILE + b"later\n")
        before = tree_hash(self.root, skip=("setups", "backups"))
        with self.assertRaises(agentfiles.StaleError):
            setups.undo(result["record"], **self.paths)
        self.assertEqual(tree_hash(self.root, skip=("setups", "backups")), before)

    def test_undo_of_a_removed_setup_is_stale_when_the_name_was_reused(self):
        setups.save("a", **self.paths)
        removed = setups.remove("a", **self.paths)
        setups.save("a", **self.paths)
        with self.assertRaises(agentfiles.StaleError):
            setups.undo(removed["record"], **self.paths)
        self.assertEqual(len(setups.list_setups(**self.paths)), 1)

    def test_a_setup_is_not_restored_over_a_file_it_does_not_manage(self):
        setups.save("a", **self.paths)
        (self.agents / "helper.md").unlink()
        (self.agents / "helper.md").write_bytes(b"hand written now\n")  # same name, no longer ours
        with self.assertRaises(setups.SetupError) as raised:
            setups.restore("a", **self.paths)
        self.assertIn("not a file this toolkit manages", str(raised.exception))
        self.assertEqual((self.agents / "helper.md").read_bytes(), b"hand written now\n")

    def test_expect_guards_a_replace_restore_and_remove(self):
        setups.save("a", **self.paths)
        rev = setups.list_setups(**self.paths)[0]["rev"]
        for call, extra in ((setups.save, {"replace": True}), (setups.restore, {}), (setups.remove, {})):
            with self.subTest(call=call.__name__):
                with self.assertRaises(agentfiles.StaleError) as raised:
                    call("a", expect="0" * 16, **extra, **self.paths)
                self.assertEqual(raised.exception.rev, rev)
                with self.assertRaises(setups.SetupError):
                    call("a", expect="nonsense", **extra, **self.paths)
        self.assertEqual(len(setups.list_setups(**self.paths)), 1)
        setups.remove("a", expect=rev, **self.paths)

    def test_symlinked_managed_files_are_refused_not_followed(self):
        outside = self.root / "outside.md"
        outside.write_bytes(AGENT_FILE)
        (self.agents / "linked.md").symlink_to(outside)
        with self.assertRaises(setups.SetupError):
            setups.save("a", **self.paths)
        self.assertFalse(list(self.setups.iterdir()) if self.setups.exists() else [])

    def test_a_missing_preset_file_is_refused(self):
        self.config.unlink()
        with self.assertRaises(skillset.SkillsetError):
            setups.save("a", **self.paths)

    def test_nothing_outside_the_set_is_read_or_written(self):
        (self.root / "opencode.json").write_bytes(b'{"secret": "credential"}')
        (self.root / "oh-my-opencode-slim").mkdir()
        (self.root / "oh-my-opencode-slim" / "orchestrator.md").write_bytes(b"prompt")
        setups.save("a", **self.paths)
        blob = b"".join(p.read_bytes() for p in self.setups.rglob("*") if p.is_file())
        self.assertNotIn(b"credential", blob)
        self.assertNotIn(b"prompt", blob.replace(b"preset", b""))


class Commands(Scratch):
    def run_main(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch.dict(os.environ, env or {}, clear=False):
            os.environ.pop("OH_MY_OPENCODE_SLIM_PRESET", None)
            if env:
                os.environ.update(env)
            code = setups.main([*argv, "--config-dir", str(self.root), "--orchestration", str(self.root),
                                "--dir", str(self.project)])
        return code, out.getvalue(), err.getvalue()

    def test_commands_round_trip_and_print_no_contents(self):
        # --config-dir implies <dir>/oh-my-opencode-slim.json, agents, commands, backups; --orchestration SKILLS.md ...
        code, out, _ = self.run_main("preset", "show")
        self.assertEqual(code, 0)
        self.assertIn("Default preset: normal", out)
        self.assertIn("Presets: normal, fast, cheap", out)
        code, out, _ = self.run_main("setup", "save", "a")
        self.assertEqual(code, 0, out)
        code, out, err = self.run_main("setup", "save", "a")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("already exists", err)
        self.assertEqual(self.run_main("setup", "save", "a", "--replace")[0], 0)
        code, out, _ = self.run_main("preset", "switch", "fast")
        self.assertEqual(code, 0)
        record = re.search(r"record: (\S+)", out).group(1)
        self.assertIn("next start of OpenCode", out)
        code, out, _ = self.run_main("setup", "list")
        self.assertIn("a  ", out)
        self.assertNotIn("Do one thing", out)
        code, out, _ = self.run_main("setup", "show", "a")
        self.assertIn("6 files", out)
        code, out, _ = self.run_main("setup", "restore", "a")
        self.assertEqual((code, self.preset()), (0, "normal"))
        undo = re.search(r"record: (\S+)", out).group(1)
        self.assertEqual(self.run_main("setup", "undo", undo)[0], 0)
        self.assertEqual(self.preset(), "fast")
        self.assertEqual(self.run_main("preset", "restore", record)[0], 0)
        self.assertEqual(self.config.read_bytes(), PRESET)
        code, out, _ = self.run_main("setup", "list", "--json")
        self.assertEqual([item["name"] for item in json.loads(out)], ["a"])
        code, out, _ = self.run_main("setup", "remove", "a", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["record"])

    def test_json_errors(self):
        code, out, err = self.run_main("preset", "switch", "nope", "--json")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("unknown preset", json.loads(out)["error"])

    def test_the_launcher_runs_the_commands(self):
        for group, op in (("preset", "show"), ("setup", "list")):
            done = subprocess.run([str(ROOT / "bin" / "agentgraph"), group, op, "--json", "--config-dir",
                                   str(self.root), "--orchestration", str(self.root), "--dir", str(self.project)],
                                  capture_output=True, text=True, env={**os.environ, "OH_MY_OPENCODE_SLIM_PRESET": ""})
            self.assertEqual(done.returncode, 0, done.stderr)
            json.loads(done.stdout)


class Served(Scratch):
    def setUp(self):
        super().setUp()
        self.httpd = self.serve()

    def serve(self, **extra):
        httpd = server.make_server(
            0, graph_dir=self.root / "graphs", cwd=self.project, session="root", web_dir=self.root,
            structure_dir=self.structure, skills_config=self.config, skills_map=self.map, skills_backups=self.backups,
            skills_profiles=self.root / "task-profiles.md", skills_dir=self.root / "skills", skills_bundled=[],
            agents_dir=self.agents, commands_dir=self.commands, setups_dir=self.setups, **extra)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        return httpd

    def call(self, body, headers=None, method="POST", port=None, path="/api/setup"):
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

    def ok(self, body):
        status, answer, _ = self.call(body)
        self.assertEqual(status, 200, answer)
        return answer

    def state(self):
        return tree_hash(self.root, skip=("setups", "backups")), tree_hash(self.setups) if self.setups.exists() else None

    def refused(self, body, message, status=400):
        before = self.state()
        code, answer, _ = self.call(body)
        self.assertEqual(code, status, answer)
        self.assertIn(message, answer["error"])
        self.assertNotIn("Traceback", answer["error"])
        self.assertEqual(self.state(), before, "a refused request wrote something")
        return answer


class Protections(Served):
    GOOD = {"op": "remove", "name": "nobody"}

    def test_guards(self):
        path = "/api/setup"
        good = {"op": "preset-restore", "record": "nothing"}
        with patch.object(server, "_LOOPBACK", frozenset()):
            status, body, _ = self.call(good)
        self.assertEqual(status, 403)
        self.assertIn("local", body["error"])
        port = self.httpd.server_port
        for origin in ("http://evil.example", f"http://localhost:{port}", "null"):
            with self.subTest(origin=origin):
                self.assertEqual(self.call(good, {"Origin": origin})[0], 403)
        # no Origin is a command-line client, as on every other route: the request is judged on its own
        self.assertEqual(self.call(good, {"Origin": None})[0], 400)
        self.assertEqual(self.call(good, {"Origin": f"http://127.0.0.1:{port}, http://evil.example"})[0], 403)
        self.assertEqual(self.call(good, {"Host": "evil.example"})[0], 403)
        limit = server._SETUP_BODY_LIMIT
        for headers, data in (({"Content-Type": "text/plain"}, json.dumps(good).encode()),
                              ({"Content-Type": None}, json.dumps(good).encode()),
                              ({}, b"not json"), ({}, b"\xff\xfe"), ({"Content-Length": str(limit + 1)}, b"{}"),
                              ({}, b" " * (limit + 1)),
                              ({}, b"[]"), ({}, b'"x"'), ({}, json.dumps({**good, "extra": 1}).encode()),
                              ({}, json.dumps({"op": "nope"}).encode()),
                              ({}, json.dumps({"op": "save", "name": "a", "replace": "yes"}).encode()),
                              ({}, json.dumps({"op": "restore", "name": "a", "expect": None}).encode()),
                              ({}, json.dumps({"op": "restore", "name": "a", "expect": "short"}).encode()),
                              ({}, json.dumps({"op": "preset-switch", "name": 5}).encode()),
                              ({}, json.dumps({"op": "preset-switch"}).encode())):
            with self.subTest(headers=headers, body=data[:20]):
                status, reply, _ = self.call(data, headers)
                self.assertIn(status, (400, 413, 415))
                self.assertIn("error", reply)
        for method in ("GET", "PUT", "DELETE"):
            with self.subTest(method=method):
                status, _, response = self.call({}, method=method)
                self.assertEqual((status, response.getheader("Allow")), (405, "POST"))
        demo_server = self.serve(demo=True)
        status, body, _ = self.call({"op": "preset-switch", "name": "fast"}, port=demo_server.server_port)
        self.assertEqual(status, 403)
        self.assertIn("demo", body["error"])
        self.assertEqual(self.preset(), "normal")
        self.assertFalse(self.backups.exists())
        self.assertFalse(self.setups.exists())


class Route(Served):
    def test_every_op(self):
        switched = self.ok({"op": "preset-switch", "name": "fast"})
        self.assertEqual((switched["preset"], switched["changed"]), ("fast", True))
        self.assertIn("next start of OpenCode", switched["message"])
        self.assertEqual(self.preset(), "fast")
        self.ok({"op": "preset-restore", "record": switched["record"]})
        self.assertEqual(self.config.read_bytes(), PRESET)
        saved = self.ok({"op": "save", "name": "a"})
        self.assertEqual((saved["name"], saved["files"]), ("a", 6))
        self.refused({"op": "save", "name": "a"}, "already exists")
        again = self.ok({"op": "save", "name": "a", "replace": True})
        rev = setups.list_setups(**self.paths)[0]["rev"]
        self.ok({"op": "preset-switch", "name": "cheap"})
        (self.agents / "helper.md").unlink()
        restored = self.ok({"op": "restore", "name": "a", "expect": rev})
        self.assertEqual((restored["written"], restored["removed"]), (2, 0))
        self.assertEqual(self.preset(), "normal")
        self.assertTrue((self.agents / "helper.md").is_file())
        self.ok({"op": "undo", "record": restored["record"]})
        self.assertEqual(self.preset(), "cheap")
        removed = self.ok({"op": "remove", "name": "a", "expect": rev})
        self.assertEqual(setups.list_setups(**self.paths), [])
        self.ok({"op": "undo", "record": removed["record"]})
        self.assertEqual([item["name"] for item in setups.list_setups(**self.paths)], ["a"])
        self.assertTrue(again["record"])

    def test_refusals_write_nothing(self):
        self.ok({"op": "save", "name": "a"})
        for body, message in (({"op": "preset-switch", "name": "nope"}, "unknown preset"),
                              ({"op": "preset-switch", "name": "normal"}, "already the default"),
                              ({"op": "save", "name": "../x"}, "invalid setup name"),
                              ({"op": "restore", "name": "missing"}, "there is no"),
                              ({"op": "remove", "name": "missing"}, "there is no"),
                              ({"op": "undo", "record": "/etc/passwd"}, "invalid undo record"),
                              ({"op": "preset-restore", "record": ""}, "invalid undo record")):
            with self.subTest(body=body):
                self.refused(body, message)
        self.refused({"op": "restore", "name": "a", "expect": "0" * 16}, "changed since", 409)
        code, answer, _ = self.call({"op": "remove", "name": "a", "expect": "0" * 16})
        self.assertEqual((code, answer["rev"]), (409, setups.list_setups(**self.paths)[0]["rev"]))

    def test_an_override_refuses_a_switch(self):
        with patch.dict(os.environ, {"OH_MY_OPENCODE_SLIM_PRESET": "cheap"}):
            self.refused({"op": "preset-switch", "name": "fast"}, "environment variable")
        (self.project / ".opencode").mkdir()
        (self.project / ".opencode" / "oh-my-opencode-slim.json").write_text('{"preset": "cheap"}')
        with patch.dict(os.environ, {"OH_MY_OPENCODE_SLIM_PRESET": ""}):
            self.refused({"op": "preset-switch", "name": "fast"}, "project config")

    def test_a_damaged_setup_is_a_400_and_writes_nothing(self):
        self.ok({"op": "save", "name": "a"})
        victim = self.setups / "a" / "files" / "map"
        victim.write_bytes(b"tampered")
        self.refused({"op": "restore", "name": "a"}, "damaged")

    def test_the_snapshot_has_the_setup_field(self):
        with patch.dict(os.environ, {"OH_MY_OPENCODE_SLIM_PRESET": ""}):
            status, document, _ = self.call({}, method="GET", path="/api/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(document["setup"], {"preset": "normal", "presets": ["normal", "fast", "cheap"],
                                             "override": None, "saved": [], "undo": None})
        saved = self.ok({"op": "save", "name": "a"})
        switched = self.ok({"op": "preset-switch", "name": "fast"})
        with patch.dict(os.environ, {"OH_MY_OPENCODE_SLIM_PRESET": "cheap"}):
            _, document, _ = self.call({}, method="GET", path="/api/snapshot")
        setup = document["setup"]
        self.assertEqual((setup["preset"], setup["override"], setup["undo"]), ("fast", "env", switched["record"]))
        self.assertEqual([(item["name"], item["preset"]) for item in setup["saved"]], [("a", "normal")])
        self.assertTrue(saved["record"])
        self.assertEqual(set(setup), {"preset", "presets", "override", "saved", "undo"})
        self.assertTrue({"name", "saved", "preset"} <= set(setup["saved"][0]))

    def test_the_snapshot_survives_an_unreadable_preset_file(self):
        self.config.write_bytes(b"{ broken")
        _, document, _ = self.call({}, method="GET", path="/api/snapshot")
        self.assertIsNone(document["setup"]["preset"])
        self.assertEqual(document["setup"]["presets"], [])
        self.assertIn("error", document["setup"])

    def test_the_demo_snapshot_has_sample_names(self):
        demo_server = self.serve(demo=True)
        _, document, _ = self.call({}, method="GET", port=demo_server.server_port, path="/api/snapshot")
        self.assertEqual(document["setup"], demo.demo_setup())
        self.assertEqual(set(document["setup"]), {"preset", "presets", "override", "saved", "undo"})
        self.assertFalse(self.setups.exists())


class Annotate(Scratch):
    def test_annotate_hashes_nothing_and_lists_names(self):
        setups.save("a", **self.paths)
        with patch("agentgraph.setups._sha", side_effect=AssertionError("hashed")):
            document = setups.annotate({}, **self.calls())
        self.assertEqual(document["setup"]["saved"][0]["name"], "a")
        self.assertEqual(document["setup"]["undo"], setups._newest_record(self.backups))
        self.assertTrue(document["setup"]["undo"])


class WithoutTheModule(unittest.TestCase):
    def test_the_module_is_the_one_under_test(self):
        self.assertEqual(Path(setups.__file__).name, "setups.py")
        for name in ("show_preset", "switch_preset", "restore_preset", "save", "list_setups", "show_setup", "restore",
                     "undo", "remove", "annotate", "main"):
            self.assertTrue(callable(getattr(setups, name)))


if __name__ == "__main__":
    unittest.main()
