"""Undo of an agent's rename or removal never leaves a reference to a name that is gone (review n8 r4, majors 1 and
2), what the snapshot says about removing an agent agrees with the removal, failed put-backs are reported with
their record kept, and writes from a stale view (`expect`) are refused. Scratch copies only; every refusal leaves
every folder as it was."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from agentgraph import agentfiles, roster, skillset, structure, teams
from tests.test_agentfiles import Base, tree


class Undo(Base):
    def setUp(self):
        super().setUp()
        self.commands = self.root / "commands"
        self.commands.mkdir()

    def full(self):
        return self.snapshot(), tree(self.commands)

    def flow(self, path, name=None, command=False):
        return structure.add_flow(path, None, self.structure, name=name, command=command,
                                  commands_dir=self.commands)[0]

    def restore(self, record):
        return roster.restore_agent(record, commands_dir=self.commands, **self.paths)

    def refused_all(self, pattern, function, *args, **kwargs):
        """Refused with a message matching `pattern`, and every folder (agents, structure, commands, preset file,
        SKILLS.md) byte for byte as it was."""
        before = self.full()
        with self.assertRaisesRegex(skillset.SkillsetError, pattern):
            function(*args, **kwargs)
        self.assertEqual(self.full(), before, "a refused change wrote something")


class RenameUndo(Undo):
    def setUp(self):
        super().setUp()
        self.add("xavier", template="verifier")
        self.record = roster.rename_agent("xavier", "yusuf", commands_dir=self.commands, **self.paths)["record"]

    def test_a_flow_with_a_command_drawn_through_the_new_name_refuses_the_undo(self):
        flow = self.flow(["impl-openai", "yusuf"], name="rev", command=True)
        self.refused_all(rf"flow {flow['id']}.*'yusuf'", self.restore, self.record)

    def test_a_flow_drawn_through_the_new_name_refuses_the_undo(self):
        flow = self.flow(["yusuf", "reviewer"])
        self.refused_all(rf"flow {flow['id']}", self.restore, self.record)

    def test_a_group_made_with_the_new_name_refuses_the_undo(self):
        group = teams.add("reviewer", ["yusuf", "impl-openai"], structure_dir=self.structure)[0]
        self.refused_all(rf"group {group['id']}", self.restore, self.record)
        led = teams.add("yusuf", ["impl-claude"], structure_dir=self.structure)[0]
        self.refused_all(rf"group {led['id']}", self.restore, self.record)

    def test_the_undo_works_again_once_they_are_gone(self):
        before = self.full()
        flow = self.flow(["impl-openai", "yusuf"], name="rev", command=True)
        self.refused_all("rev|yusuf", self.restore, self.record)
        structure.remove_flow(flow["id"], self.structure, commands_dir=self.commands)
        self.assertEqual(self.full(), before)
        self.restore(self.record)
        self.assertTrue((self.agents / "xavier.md").is_file())
        self.assertFalse((self.agents / "yusuf.md").exists())


class RemovalUndo(Undo):
    def test_restore_after_another_flow_took_a_restored_flows_name(self):
        self.add("xavier", template="verifier")
        self.flow(["impl-openai", "xavier"], name="look-again")
        record = roster.remove_agent("xavier", **self.paths)["record"]
        other = self.flow(["reviewer", "impl-claude"], name="look-again", command=True)
        self.refused_all(rf"look-again.*{other['id']}|{other['id']}.*look-again", self.restore, record)
        structure.remove_flow(other["id"], self.structure, commands_dir=self.commands)
        self.restore(record)
        self.assertIn("look-again", [flow["name"] for flow in structure.drawn_flows(self.structure)])

    def test_out_of_order_restores_of_two_agents_that_share_a_flow(self):
        self.add("xavier", template="verifier")
        self.add("yusuf", template="verifier")
        self.flow(["xavier", "yusuf"])
        teams.add("xavier", ["yusuf"], structure_dir=self.structure)
        before = self.full()
        first = roster.remove_agent("xavier", **self.paths)["record"]
        second = roster.remove_agent("yusuf", **self.paths)["record"]
        # xavier's flow and group name yusuf, who is gone: refused, naming yusuf
        self.refused_all("yusuf", self.restore, first)
        self.restore(second)
        self.restore(first)
        self.assertEqual(self.full(), before)

    def test_a_restored_group_whose_other_agent_is_gone_is_refused(self):
        self.add("xavier", template="verifier")
        self.add("yusuf", template="verifier")
        teams.add("yusuf", ["xavier", "impl-openai"], structure_dir=self.structure)
        first = roster.remove_agent("xavier", **self.paths)["record"]  # yusuf's group keeps impl-openai
        roster.remove_agent("yusuf", **self.paths)
        self.refused_all("yusuf", self.restore, first)

    def test_a_preset_agents_restore_is_checked_the_same_way(self):
        self.flow(["impl-openai", "impl-claude"], name="look-again")
        record = roster.remove_agent("impl-claude", **self.paths)["record"]
        other = self.flow(["reviewer", "impl-openai"], name="look-again", command=True)
        self.refused_all(rf"{other['id']}", self.restore, record)


class Removable(Undo):
    def annotate(self, *names):
        document = {"agents": [{"name": name} for name in names]}
        roster.annotate(document, config=self.config, map_path=self.map, agents_dir=self.agents,
                        structure=self.structure)
        return {row["name"]: row for row in document["agents"]}

    def test_removable_agrees_with_remove_for_a_flow_whose_command_is_on(self):
        self.add("xavier", template="verifier")
        self.flow(["impl-openai", "xavier"], name="rev", command=True)
        for name in ("xavier", "impl-claude"):
            if name == "impl-claude":
                self.flow(["impl-claude", "reviewer"], name="rev2", command=True)
            row = self.annotate(name)[name]
            with self.assertRaises(skillset.SkillsetError) as caught:
                roster.remove_agent(name, **self.paths)
            self.assertFalse(row["removable"], name)
            self.assertEqual(row["removable_reason"], str(caught.exception))

    def test_without_a_structure_folder_the_documents_own_flows_decide_and_nothing_else_is_read(self):
        self.add("xavier", template="verifier")
        flow = self.flow(["impl-openai", "xavier"], name="rev", command=True)
        document = {"agents": [{"name": "xavier"}], "structure": {"flows": structure.drawn_flows(self.structure)}}
        with patch.object(roster, "_structure_fd", side_effect=AssertionError("read a structure folder")):
            roster.annotate(document, config=self.config, map_path=self.map, agents_dir=self.agents)
        with self.assertRaises(skillset.SkillsetError) as caught:
            roster.remove_agent("xavier", **self.paths)
        self.assertEqual(document["agents"][0]["removable_reason"], str(caught.exception))
        self.assertIn(flow["id"], str(caught.exception))

    def test_removable_stays_true_for_a_flow_without_a_command(self):
        self.add("xavier", template="verifier")
        self.flow(["impl-openai", "xavier"], name="rev")
        self.assertTrue(self.annotate("xavier")["xavier"]["removable"])
        roster.remove_agent("xavier", **self.paths)


class FailedPutBack(Undo):
    def failing_revert(self):
        forward = roster._change_file

        def change(root_fd, item, check=True):
            if not check:
                raise OSError("simulated put-back failure")
            return forward(root_fd, item, check)

        return patch.object(roster, "_change_file", side_effect=change)

    def test_a_preset_removal_whose_put_back_failed_keeps_its_record(self):
        with patch.object(roster, "_save", side_effect=OSError("simulated")), self.failing_revert():
            with self.assertRaisesRegex(skillset.SkillsetError, r"30-build/impl-claude\.md could not be put back.*"
                                                                r"undo record (\S+) keeps") as caught:
                roster.remove_agent("impl-claude", **self.paths)
        record = str(caught.exception).split("undo record ")[1].split(" keeps")[0]
        self.assertTrue(Path(record).is_file())
        kept = json.loads(Path(record).read_text())
        self.assertEqual([item["name"] for item in kept["structure"]], ["impl-claude.md"])

    def test_a_preset_restore_whose_take_back_failed_says_which_files(self):
        record = roster.remove_agent("impl-claude", **self.paths)["record"]
        with patch.object(roster, "_save", side_effect=OSError("simulated")), self.failing_revert():
            with self.assertRaisesRegex(skillset.SkillsetError, r"30-build/impl-claude\.md could not be taken back"):
                self.restore(record)
        self.assertTrue(Path(record).is_file())

    def test_a_rename_undo_whose_take_back_failed_says_which_files(self):
        self.add("xavier", template="verifier")
        record = roster.rename_agent("xavier", "yusuf", commands_dir=self.commands, **self.paths)["record"]
        with patch.object(agentfiles, "replace", side_effect=OSError("simulated")), self.failing_revert():
            with self.assertRaisesRegex(skillset.SkillsetError, r"yusuf\.md.*could not be taken back"):
                self.restore(record)

    def test_an_added_restore_whose_take_back_failed_says_which_files(self):
        self.add("xavier", template="verifier")
        self.flow(["impl-openai", "xavier"])
        record = roster.remove_agent("xavier", **self.paths)["record"]
        forward = roster._change_file

        def change(root_fd, item, check=True):
            if check and item["folder"] == "flows":
                raise OSError("simulated")
            if not check:
                raise OSError("simulated put-back failure")
            return forward(root_fd, item, check)

        with patch.object(roster, "_change_file", side_effect=change):
            with self.assertRaisesRegex(skillset.SkillsetError, r"could not be taken back"):
                self.restore(record)


class Stale(Undo):
    """stale-contract.md, roster side: `rev` in the snapshot and in read; `expect` on text, rename, remove and a
    skills change; a stale write is refused with nothing changed, a current one goes ahead, none given works as
    before."""

    def setUp(self):
        super().setUp()
        self.add("xavier", template="verifier")

    def rev(self, name="xavier"):
        document = {"agents": [{"name": name}]}
        roster.annotate(document, config=self.config, map_path=self.map, agents_dir=self.agents,
                        structure=self.structure)
        shown = roster.read_agent(name, **self.where()) if (self.agents / f"{name}.md").exists() else None
        if shown is not None:
            self.assertEqual(shown["rev"], document["agents"][0]["rev"])
        return document["agents"][0]["rev"]

    def where(self):
        return dict(agents_dir=self.agents, config=self.config, map_path=self.map, structure=self.structure)

    def stale(self, function, *args, **kwargs):
        before = self.full()
        with self.assertRaisesRegex(agentfiles.StaleError, "changed since you opened it; reload and try again") as caught:
            function(*args, **kwargs)
        self.assertEqual(self.full(), before, "a stale write wrote something")
        return caught.exception

    def test_the_rev_follows_the_agent_file_and_its_structure_file(self):
        first = self.rev()
        self.assertRegex(first, r"^[0-9a-f]{16}$")
        self.assertEqual(self.rev(), first)
        roster.text_agent("xavier", "New.", agents_dir=self.agents, backups=self.backups)
        second = self.rev()
        self.assertNotEqual(second, first)
        own = self.structure / "30-build" / "xavier.md"
        own.write_bytes(own.read_bytes() + b"More.\n")
        self.assertNotEqual(self.rev(), second)
        self.assertNotEqual(self.rev("impl-claude"), self.rev("impl-openai"))

    def test_text_with_a_stale_expect_is_refused_and_a_current_one_goes_ahead(self):
        old = self.rev()
        roster.text_agent("xavier", "Changed elsewhere.", agents_dir=self.agents, backups=self.backups)
        error = self.stale(roster.text_agent, "xavier", "Mine.", backups=self.backups, expect=old, **self.where())
        self.assertEqual(error.rev, self.rev())
        roster.text_agent("xavier", "Mine.", backups=self.backups, expect=self.rev(), **self.where())
        self.assertEqual(roster.read_agent("xavier", **self.where())["text"], "Mine.")

    def test_rename_with_a_stale_expect_is_refused_and_a_current_one_goes_ahead(self):
        old = self.rev()
        roster.text_agent("xavier", "Changed elsewhere.", agents_dir=self.agents, backups=self.backups)
        self.stale(roster.rename_agent, "xavier", "yusuf", commands_dir=self.commands, expect=old, **self.paths)
        roster.rename_agent("xavier", "yusuf", commands_dir=self.commands, expect=self.rev(), **self.paths)
        self.assertTrue((self.agents / "yusuf.md").is_file())

    def test_remove_with_a_stale_expect_is_refused_and_a_current_one_goes_ahead(self):
        old = self.rev()
        own = self.structure / "30-build" / "xavier.md"
        own.write_bytes(own.read_bytes() + b"Changed elsewhere.\n")
        self.stale(roster.remove_agent, "xavier", expect=old, **self.paths)
        roster.remove_agent("xavier", expect=self.rev(), **self.paths)
        self.assertFalse((self.agents / "xavier.md").exists())

    def test_a_preset_agents_remove_with_a_stale_expect_is_refused(self):
        old = self.rev("impl-claude")
        own = self.structure / "30-build" / "impl-claude.md"
        own.write_bytes(own.read_bytes() + b"Changed elsewhere.\n")
        self.stale(roster.remove_agent, "impl-claude", expect=old, **self.paths)
        roster.remove_agent("impl-claude", expect=self.rev("impl-claude"), **self.paths)

    def test_a_skills_change_with_a_stale_expect_is_refused(self):
        old = self.rev()
        roster.text_agent("xavier", "Changed elsewhere.", agents_dir=self.agents, backups=self.backups)

        def change(expect):
            with roster.expecting("xavier", expect, config=self.config, map_path=self.map,
                                  structure=self.structure) as check:
                return agentfiles.set_skills("xavier", ["code-assurance"], agents_dir=self.agents,
                                             backups=self.backups, check=check)

        self.stale(change, old)
        self.assertEqual(change(self.rev())[0]["skills"], ["code-assurance"])
        self.assertEqual(change(None)[0]["skills"], ["code-assurance"])  # no expect: as before

    def test_expect_by_command(self):
        common = ["--config", str(self.config), "--map", str(self.map), "--backups", str(self.backups),
                  "--structure", str(self.structure), "--agents", str(self.agents), "--json"]

        def cli(*args, stdin=""):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                    patch("sys.stdin", io.StringIO(stdin)):
                code = roster.agent_main([*args, *common])
            return code, json.loads(out.getvalue())

        code, shown = cli("show", "xavier")
        self.assertEqual((code, shown["rev"]), (0, self.rev()))
        roster.text_agent("xavier", "Changed elsewhere.", agents_dir=self.agents, backups=self.backups)
        before = self.full()
        code, answer = cli("text", "xavier", "--text-file", "-", "--expect", shown["rev"], stdin="Mine.\n")
        self.assertEqual((code, answer["rev"]), (1, self.rev()))
        self.assertIn("reload and try again", answer["error"])
        code, answer = cli("remove", "xavier", "--expect", shown["rev"])
        self.assertEqual(code, 1)
        self.assertEqual(self.full(), before)
        self.assertEqual(cli("remove", "xavier", "--expect", self.rev())[0], 0)


class RenameNameCheck(Undo):
    """Review n8 r5, minor 1: the new name is checked under the structure and skillset locks, so a name another
    writer takes between the check and the write is caught."""

    def test_a_preset_agent_planted_with_the_new_name_before_the_write_is_caught(self):
        self.add("xavier", template="verifier")
        real = roster._structure_locked
        planted = []
        before = {}

        def plant(root):
            # Another writer gives a preset agent the name 'yusuf' just before the rename takes its locks.
            if not planted:
                planted.append(True)
                with skillset._locked(self.config, self.map):
                    obj = json.loads(self.config.read_text())
                    preset = next(iter(obj["presets"]))
                    obj["presets"][preset]["yusuf"] = {"model": "openai/gpt-5"}
                    self.config.write_text(json.dumps(obj, indent=2) + "\n")
                before["state"] = self.full()
            return real(root)

        with patch.object(roster, "_structure_locked", plant), \
                self.assertRaisesRegex(skillset.SkillsetError, r"agent 'yusuf' already exists in preset"):
            roster.rename_agent("xavier", "yusuf", commands_dir=self.commands, **self.paths)
        self.assertTrue(planted)
        self.assertEqual(self.full(), before["state"], "a refused rename wrote something")
        self.assertTrue((self.agents / "xavier.md").is_file())
