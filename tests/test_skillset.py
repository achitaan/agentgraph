"""Exercise skillset with disposable copies; never edit the installed files."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentgraph import skillset
from tests.winskip import needs_fifo, needs_symlinks, posix_only
from agentgraph.snapshot_json import skills_document


SCRATCH = Path(tempfile.gettempdir()).resolve() / "agentgraph-skillset"
ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = [entry["name"] for entry in skills_document()]
TRIAL = "simplify" if "simplify" in CATALOGUE else "clone-any-website"


class SkillsetTest(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / skillset.CONFIG.name
        self.map = self.root / "SKILLS.md"
        self.backups = self.root / "backups"
        self.profiles = self.root / "task-profiles.md"
        for source, target in ((skillset.CONFIG, self.config), (skillset.MAP, self.map),
                               (skillset.PROFILES, self.profiles)):
            shutil.copyfile(source, target)
        self.options = dict(config=self.config, map_path=self.map, backups=self.backups, profiles=self.profiles)
        self.original = self.files()
        self.preset = self.data()["preset"]

    def files(self):
        return self.config.read_bytes(), self.map.read_bytes()

    def data(self):
        return json.loads(self.config.read_bytes())

    def arrays(self, agent):
        return {name: agents[agent].get("skills", []) for name, agents in self.data()["presets"].items()
                if agent in agents}

    def change(self, function, agent, *args, **kwargs):
        result = function(agent, *args, **self.options, **kwargs)
        self.assertTrue(skillset.map_matches(config=self.config, map_path=self.map))
        return result

    def assert_refused(self, pattern, function, *args, **kwargs):
        old_map = self.map.read_bytes()
        with self.assertRaisesRegex(skillset.SkillsetError, pattern):
            function(*args, **(self.options | kwargs))
        self.assertEqual(self.map.read_bytes(), old_map)
        self.assertFalse(self.backups.exists())

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = skillset.main([*args, "--config", str(self.config), "--map", str(self.map),
                                  "--backups", str(self.backups)])
        return code, out.getvalue(), err.getvalue()

    def test_original_map_matches(self):
        self.assertTrue(skillset.map_matches(config=self.config, map_path=self.map))

    def test_show_active_list(self):
        result = skillset.show("impl-openai", config=self.config)
        self.assertEqual(result["preset"], self.preset)
        self.assertTrue(result["editable"])
        self.assertEqual(result["skills"], self.arrays("impl-openai")[self.preset])
        self.assertEqual(result["differing_presets"], [])
        self.assertEqual(self.files(), self.original)

    def test_add_remove_round_trip(self):
        self.assertNotIn(TRIAL, self.arrays("impl-openai")[self.preset])
        self.assertIn("restart", self.change(skillset.add, "impl-openai", TRIAL)["message"])
        self.change(skillset.remove, "impl-openai", TRIAL)
        self.assertEqual(self.files(), self.original)

    def test_default_changes_only_agent_entries_that_exist(self):
        before = self.data()
        skipped = next(iter(before["presets"]))
        del before["presets"][skipped]["impl-openai"]
        self.config.write_text(json.dumps(before), encoding="utf-8", newline="\n")
        result = self.change(skillset.add, "impl-openai", TRIAL)
        expected = json.loads(json.dumps(before))
        for preset, agents in expected["presets"].items():
            if preset != skipped:
                agents["impl-openai"]["skills"].append(TRIAL)
        self.assertEqual(self.data(), expected)
        self.assertEqual(result["changed_presets"], [name for name in before["presets"] if name != skipped])

    def test_selected_presets_and_variant_map_round_trip(self):
        selected = [name for name in self.data()["presets"] if name != self.preset][:2]
        result = self.change(skillset.add, "impl-openai", TRIAL, presets=selected)
        self.assertEqual(result["skills"], self.arrays("impl-openai")[self.preset])
        self.assertEqual(result["differing_presets"], selected)
        self.assertEqual([p for p, values in self.arrays("impl-openai").items() if TRIAL in values], selected)
        self.assertIn(f"`{selected[0]}`, `{selected[1]}`: ", self.map.read_text(encoding="utf-8"))
        self.change(skillset.remove, "impl-openai", TRIAL, presets=selected)
        self.assertEqual(self.files(), self.original)

    def give_orchestrator_every_skill(self):
        # The installed presets may name the orchestrator's skills; these tests are about an agent that has `*`.
        self.change(skillset.set_all, "orchestrator")
        self.assertEqual(set(map(tuple, self.arrays("orchestrator").values())), {("*",)})
        shutil.rmtree(self.backups, ignore_errors=True)
        self.original = self.files()

    def test_wildcard_add_is_noop(self):
        self.give_orchestrator_every_skill()
        result = self.change(skillset.add, "orchestrator", TRIAL)
        self.assertFalse(result["changed"])
        self.assertFalse(self.backups.exists())
        self.assertEqual(self.files(), self.original)

    def test_wildcard_remove_expands_in_catalogue_order_and_all_restores(self):
        self.give_orchestrator_every_skill()
        self.change(skillset.remove, "orchestrator", TRIAL)
        self.assertEqual(self.arrays("orchestrator")[self.preset], [name for name in CATALOGUE if name != TRIAL])
        self.change(skillset.add, "orchestrator", TRIAL)
        skills = self.arrays("orchestrator")[self.preset]
        self.assertEqual(set(skills), set(CATALOGUE))
        self.assertNotIn("*", skills)
        self.change(skillset.set_all, "orchestrator")
        self.assertEqual(self.files(), self.original)

    def test_all_normal_agent(self):
        self.change(skillset.set_all, "verifier")
        self.assertTrue(all(values == ["*"] for values in self.arrays("verifier").values()))

    def test_idempotence(self):
        for action, agent, skills in ((skillset.add, "impl-openai", [TRIAL]),
                                      (skillset.remove, "impl-openai", [TRIAL]),
                                      (skillset.set_all, "impl-openai", [])):
            self.assertTrue(self.change(action, agent, *skills)["changed"])
            before = self.files()
            self.assertFalse(self.change(action, agent, *skills)["changed"])
            self.assertEqual(self.files(), before)

    def test_empty_council_removal_does_not_insert_key(self):
        self.assertFalse(self.change(skillset.remove, "council", TRIAL)["changed"])
        self.assertEqual(self.files(), self.original)

    def test_missing_key_insert_and_last_remove(self):
        before = self.data()
        self.assertTrue(skillset.show("council", config=self.config)["editable"])
        self.change(skillset.add, "council", TRIAL)
        for agents in before["presets"].values():
            agents["council"]["skills"] = [TRIAL]
        self.assertEqual(self.data(), before)
        self.change(skillset.remove, "council", TRIAL)
        self.assertTrue(all(agents["council"]["skills"] == [] for agents in self.data()["presets"].values()))
        self.assertEqual(self.map.read_bytes(), self.original[1])
        after_empty = self.files()
        self.change(skillset.add, "council", TRIAL)
        self.change(skillset.remove, "council", TRIAL)
        self.assertEqual(self.files(), after_empty)

    def test_council_all_can_create_key(self):
        result = self.change(skillset.set_all, "council")
        self.assertEqual(result["skills"], ["*"])

    def test_adviser_round_trip(self):
        result = self.change(skillset.add, "oracle-lite", TRIAL)
        self.assertTrue(result["editable"])
        self.change(skillset.remove, "oracle-lite", TRIAL)
        self.assertEqual(self.files(), self.original)

    def test_seat_show_and_all_mutation_refusals(self):
        seat = "councillor-fable"
        result = skillset.show(seat, config=self.config)
        self.assertFalse(result["editable"])
        self.assertEqual(result["skills"], [])
        self.assertIn("plugin does not read skills", result["reason"])
        for action, args in ((skillset.add, [TRIAL]), (skillset.remove, [TRIAL]), (skillset.set_all, [])):
            self.assert_refused("council's own table", action, seat, *args)
        self.assertEqual(self.files(), self.original)

    def test_show_all_active_agents(self):
        roster = {item["agent"]: item for item in skillset.show_all(config=self.config)}
        self.assertEqual([name for name, entry in roster.items() if not entry["editable"]],
                         ["councillor-fable", "councillor-astra", "councillor-carbon"])
        self.assertEqual(set(roster), set(self.data()["presets"][self.preset]) |
                         {"councillor-fable", "councillor-astra", "councillor-carbon"})

    def test_profile_remove_warns_and_add_clears(self):
        self.assertEqual(skillset.profile_mismatches(config=self.config, profiles=self.profiles), [])
        result = self.change(skillset.remove, "impl-openai", "code-assurance")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("task-profiles.md: the impl-openai profile still names code-assurance", result["warnings"][0])
        self.assertTrue(skillset.profile_mismatches(config=self.config, profiles=self.profiles))
        self.change(skillset.add, "impl-openai", "code-assurance")
        self.assertEqual(skillset.profile_mismatches(config=self.config, profiles=self.profiles), [])
        self.assertEqual(self.profiles.read_bytes(), skillset.PROFILES.read_bytes())

    def test_profile_wildcard_remove_warns(self):
        self.profiles.write_text("- `orchestrator`: `code-assurance`\n", encoding="utf-8", newline="\n")
        result = self.change(skillset.remove, "orchestrator", "code-assurance")
        self.assertIn("orchestrator profile still names code-assurance", result["warnings"][0])

    def test_refuse_missing_agent(self):
        self.assert_refused("not in any selected preset", skillset.add, "no-such-agent", TRIAL)
        self.assertEqual(self.files(), self.original)

    def test_refuse_agent_absent_from_selected(self):
        obj = self.data()
        del obj["presets"][self.preset]["impl-openai"]
        self.config.write_text(json.dumps(obj), encoding="utf-8", newline="\n")
        before = self.files()
        self.assert_refused("not in any selected preset", skillset.add, "impl-openai", TRIAL, presets=[self.preset])
        self.assertEqual(self.files(), before)

    def test_refuse_unknown_preset(self):
        self.assert_refused("unknown preset", skillset.add, "impl-openai", TRIAL, presets=["no-such-preset"])
        self.assertEqual(self.files(), self.original)

    def test_refuse_uninstalled_skill(self):
        self.assert_refused("not installed", skillset.add, "impl-openai", "../other-file")
        self.assert_refused("not installed", skillset.remove, "impl-openai", "not-a-skill")
        self.assertEqual(self.files(), self.original)

    def test_refuse_invalid_names(self):
        for name in ("-alias", "line\nbreak", "control\x85", "x" * 101, ""):
            with self.subTest(name=name):
                self.assert_refused("invalid agent name", skillset.add, name, TRIAL)
                self.assert_refused("invalid skill name", skillset.add, "impl-openai", name)
                self.assert_refused("invalid preset name", skillset.add, "impl-openai", TRIAL, presets=[name])
        self.assertEqual(self.files(), self.original)

    @needs_symlinks
    def test_refuse_symlink_config(self):
        moved = self.config.rename(self.root / "target")
        self.config.symlink_to(moved)
        self.assert_refused("regular file", skillset.add, "impl-openai", TRIAL)
        self.assertTrue(self.config.is_symlink())
        self.assertEqual(moved.read_bytes(), self.original[0])

    @needs_fifo
    def test_refuse_fifo_config_without_waiting(self):
        bad = self.root / "fifo"
        os.mkfifo(bad)
        self.assert_refused("regular file", skillset.add, "impl-openai", TRIAL, config=bad)

    def test_refuse_non_utf8_config(self):
        self.config.write_bytes(b"\xff")
        self.assert_refused("not UTF-8", skillset.add, "impl-openai", TRIAL)
        self.assertEqual(self.config.read_bytes(), b"\xff")

    def test_refuse_invalid_json_and_duplicate_keys(self):
        for raw in (b"{]", b'{"preset":"fast","preset":"normal"}', b'{"preset":NaN}'):
            self.config.write_bytes(raw)
            self.assert_refused("not parseable JSON", skillset.add, "impl-openai", TRIAL)
            self.assertEqual(self.config.read_bytes(), raw)

    def test_refuse_bad_shapes(self):
        for raw in ("[]", '{"preset":"fast","presets":[]}', '{"preset":"fast","presets":{"fast":[]}}',
                    '{"preset":"fast","presets":{"fast":{"impl-openai":{"model":"x","skills":[0]}}}}'):
            self.config.write_text(raw, encoding="utf-8", newline="\n")
            self.assert_refused("config shape", skillset.add, "impl-openai", TRIAL)
            self.assertEqual(self.config.read_text(encoding="utf-8"), raw)

    def test_refuse_mismatching_map(self):
        self.map.write_bytes(self.original[1].replace(b"| `council` | none |", b"| `council` | all |"))
        self.assert_refused("does not match", skillset.add, "impl-openai", TRIAL)
        self.assertEqual(self.config.read_bytes(), self.original[0])

    def test_byte_proof_with_crlf_escapes_and_unicode(self):
        before = self.original[0].decode("utf-8").replace('  "$schema"', '  "sentinel": "Snow 雪 [ ]",\n  "$schema"')
        before = before.replace('"engineering-delivery"', '"engineering-\\u0064elivery"').replace("\n", "\r\n")
        self.config.write_bytes(before.encode())
        self.change(skillset.add, "impl-openai", TRIAL)
        after = self.config.read_bytes().decode()
        masks = lambda text: skillset._outside(text, [span for path, span in skillset._spans(text)[0].items()
                                                      if path[:1] == ("presets",)
                                                      and path[-2:] == ("impl-openai", "skills")])
        self.assertEqual(masks(before), masks(after))
        self.assertIn('"engineering-\\u0064elivery",\r\n', after)
        self.change(skillset.remove, "impl-openai", TRIAL)
        self.assertEqual(self.config.read_bytes(), before.encode())

    def test_refuse_faulty_edit_proof(self):
        replace = skillset._replace
        with patch.object(skillset, "_replace", side_effect=lambda text, edits: " " + replace(text, edits)):
            self.assert_refused("proof failed", skillset.add, "impl-openai", TRIAL)
        self.assertEqual(self.files(), self.original)

    @posix_only
    def test_backup_once_and_modes_kept(self):
        self.config.chmod(0o640)
        self.map.chmod(0o600)
        first = Path(self.change(skillset.add, "impl-openai", TRIAL)["backup"])
        self.assertRegex(str(first), r"\d{4}-\d{2}-\d{2}-skills-edit/")
        self.assertEqual(first.read_bytes(), self.original[0])
        before = first.stat()
        second = Path(self.change(skillset.add, "verifier", TRIAL)["backup"])
        self.assertEqual(second, first)
        self.assertEqual(first.read_bytes(), self.original[0])
        self.assertEqual(first.stat().st_ino, before.st_ino)
        self.assertEqual(first.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(self.map.stat().st_mode), 0o600)

    def stale(self, path, other, call):
        real = skillset._backup

        def second_process(*args, **kwargs):
            path.write_bytes(other)  # between this edit's read and its write
            return real(*args, **kwargs)

        with patch.object(skillset, "_backup", side_effect=second_process):
            with self.assertRaisesRegex(skillset.SkillsetError, "changed while this edit was being made"):
                call()

    def test_a_config_changed_by_another_process_is_refused_and_nothing_is_written(self):
        other = self.original[0] + b"\n"
        self.stale(self.config, other, lambda: skillset.add("impl-openai", TRIAL, **self.options))
        self.assertEqual(self.config.read_bytes(), other)
        self.assertEqual(self.map.read_bytes(), self.original[1])
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_a_map_changed_by_another_process_is_refused_and_neither_file_is_written(self):
        other = self.original[1] + b"\nA note.\n"
        self.stale(self.map, other, lambda: skillset.add("impl-openai", TRIAL, **self.options))
        self.assertEqual(self.map.read_bytes(), other)
        self.assertEqual(self.config.read_bytes(), self.original[0])
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_backup_holds_what_each_write_replaced(self):
        first = self.change(skillset.add, "impl-openai", TRIAL)
        state = self.config.read_bytes()
        second = self.change(skillset.add, "verifier", TRIAL)
        self.assertEqual(second["backup"], first["backup"])
        self.assertEqual(Path(first["previous"]), Path(first["backup"]))
        self.assertNotEqual(second["previous"], second["backup"])
        self.assertEqual(Path(second["previous"]).read_bytes(), state)
        self.assertEqual(Path(second["backup"]).read_bytes(), self.original[0])
        # saving text that the latest backup already holds adds no copy
        folder = Path(second["backup"]).parent
        count = len(list(folder.iterdir()))
        again = skillset._backup(self.config, self.backups, state.decode(), 0o600)
        self.assertEqual((again["previous"], len(list(folder.iterdir()))), (second["previous"], count))

    def test_backup_copies_stay_bounded_on_a_busy_day(self):
        with patch.object(skillset, "MAX_BACKUPS", 3):
            for _ in range(5):
                self.change(skillset.add, "impl-openai", TRIAL)
                self.change(skillset.remove, "impl-openai", TRIAL)
        folder = next(self.backups.rglob(self.config.name)).parent
        names = sorted(path.name for path in folder.iterdir())
        self.assertEqual(len(names), 4)
        self.assertEqual((folder / self.config.name).read_bytes(), self.original[0])
        self.assertEqual(names[-1], self.config.name + ".0009")

    def test_refuse_unsafe_agent_names_before_anything_is_written(self):
        for name in ("a/b", "../x", "..", ".hidden", "a\\b", "a..b", "a\x00b", "a\nb", "a\udc80"):
            with self.subTest(name=name):
                for call in (lambda: skillset.show(name, config=self.config),
                             lambda: skillset.add(name, TRIAL, **self.options),
                             lambda: skillset.remove(name, TRIAL, **self.options),
                             lambda: skillset.set_all(name, **self.options)):
                    with self.assertRaisesRegex(skillset.SkillsetError, "invalid agent name"):
                        call()
                code, out, err = self.cli("add", name, TRIAL, "--json")
                self.assertEqual(code, 1)
                self.assertIn("invalid agent name", json.loads(out)["error"])
        self.assertEqual(self.files(), self.original)
        self.assertFalse(self.backups.exists())

    def test_failure_before_rename_leaves_both_files_unchanged(self):
        with patch.object(skillset.os, "replace", side_effect=OSError("simulated before rename")):
            with self.assertRaisesRegex(skillset.SkillsetError, "simulated before rename"):
                skillset.add("impl-openai", TRIAL, **self.options)
        self.assertEqual(self.files(), self.original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_failure_at_second_rename_rolls_back_first(self):
        real_replace = os.replace

        def fail_second(src, dst):
            if dst == self.config:
                raise OSError("simulated config rename failure")
            real_replace(src, dst)

        with patch.object(skillset.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(skillset.SkillsetError, "simulated config rename failure"):
                skillset.add("impl-openai", TRIAL, **self.options)
        self.assertEqual(self.files(), self.original)

    def test_concurrent_processes_both_land(self):
        command = [sys.executable, "-P", "-m", "agentgraph.skillset", "--config", str(self.config),
                   "--map", str(self.map), "--backups", str(self.backups), "add"]
        env = os.environ | {"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        with subprocess.Popen([*command, "impl-openai", TRIAL, "--json"], cwd=ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as one:
            with subprocess.Popen([*command, "oracle", TRIAL, "--json"], cwd=ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as two:
                for process in (one, two):
                    out, err = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, (out, err))
                    self.assertTrue(json.loads(out)["changed"])
        for agent in ("impl-openai", "oracle"):
            self.assertTrue(all(TRIAL in values for values in self.arrays(agent).values()))
        self.assertTrue(skillset.map_matches(config=self.config, map_path=self.map))

    def test_cli_status_and_json(self):
        code, out, err = self.cli("show", "impl-openai", "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(json.loads(out)["editable"])
        code, out, err = self.cli("add", "impl-openai", "not-installed", "--json")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("not installed", json.loads(out)["error"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as err:
            skillset.main(["remove", "impl-openai"])
        self.assertEqual(err.exception.code, 2)

    def test_cli_text_warns_about_profile(self):
        code, text, err = self.cli("remove", "impl-openai", "code-assurance")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Warning: task-profiles.md", text)
        self.assertIn("after a restart", text)


if __name__ == "__main__":
    unittest.main()
