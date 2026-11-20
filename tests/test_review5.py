"""Review n8 r5, minors 2 to 4 and the note on `expect`: one run counts once when two drawn flows have the same
path; an undo record whose missing agent was deleted by hand says what the user can do and is pruned like any
other; a preset agent whose name the strict pattern refuses is shown as not editable for skills, with the writer's
own reason; an `expect` that does not look like a rev is bad input on every surface. Scratch copies only."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agentgraph import agentfiles, flowrun, roster, skillset, snapshot_json, structure
from tests.test_agentfiles import Base
from tests.test_flowrun import graph_raw
from tests.test_structure import make_structure


class SamePath(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = make_structure(self.temporary / "structure")
        self.graphs = self.temporary / "graphs"
        self.graphs.mkdir()
        flowrun._CACHE.clear()

    def test_two_flows_with_the_same_path_count_one_run_once_under_the_lowest_id(self):
        first = structure.add_flow(["impl-openai", "verifier"], None, self.root)[0]
        flows = self.root / "flows"
        twin_file = flows / "impl-openai-verifier-2.md"
        twin_file.write_bytes((flows / f"{first['id']}.md").read_bytes())
        raw = graph_raw("one", [
            {"id": "a1", "type": "implement", "agent": "impl-openai", "attempts": 1},
            {"id": "v1", "type": "check", "agent": "verifier", "depends_on": ["a1"], "attempts": 1}])
        path = self.graphs / "one.json"
        path.write_text(json.dumps(raw))
        shape = snapshot_json.load_structure(self.root)
        ids = sorted(flow["id"] for flow in shape["flows"] if flow["path"] == ["impl-openai", "verifier"])
        self.assertEqual(ids, [first["id"], "impl-openai-verifier-2"], "two drawn flows with the same path")
        stats = {flow["id"]: flow["stats"] for flow in structure.flows_with_stats(
            shape, ledger=None, graph_paths=[path], db_path=None)}
        self.assertEqual(stats[ids[0]]["runs"], 1, "the lowest id counts the run")
        self.assertIsNone(stats[ids[1]], "and the other does not count it again")


if __name__ == "__main__":
    unittest.main()


class HandDeleted(Base):
    """Minor 3: a restore refused because an agent its flow names is gone says what to do when that agent was
    deleted by hand (nothing can restore it), and its record is pruned like any other."""

    def setUp(self):
        super().setUp()
        self.add("xavier", template="verifier")
        self.add("yusuf", template="verifier")
        structure.add_flow(["xavier", "yusuf"], None, self.structure)
        self.record = roster.remove_agent("xavier", **self.paths)["record"]
        # yusuf deleted by hand: his agent file and his structure file, no undo record
        (self.agents / "yusuf.md").unlink()
        for own in self.structure.glob("*/yusuf.md"):
            own.unlink()

    def restore(self):
        return roster.restore_agent(self.record, **self.paths)

    def test_the_refusal_names_the_hand_deleted_case_and_what_to_do(self):
        before = self.snapshot()
        with self.assertRaises(skillset.SkillsetError) as caught:
            self.restore()
        self.assertEqual(self.snapshot(), before)
        message = str(caught.exception)
        self.assertIn("yusuf", message)
        self.assertIn("deleted by hand", message)
        self.assertIn("add an agent named yusuf", message)
        self.assertIn("leave this record", message)
        self.assertIn(f"newest {skillset.MAX_BACKUPS}", message)
        # what it says works: an agent of that name lets the restore through
        self.add("yusuf", template="verifier")
        self.restore()
        self.assertTrue((self.agents / "xavier.md").is_file())

    def test_such_a_record_is_pruned_like_any_other(self):
        with self.assertRaises(skillset.SkillsetError):
            self.restore()
        self.assertTrue(Path(self.record).is_file())
        for _ in range(skillset.MAX_BACKUPS):
            self.add("xavier", template="verifier")
            time.sleep(0.002)  # records of one day are ordered by modification time
            roster.remove_agent("xavier", **self.paths)
        self.assertFalse(Path(self.record).exists(), "the stuck record is pruned once enough newer ones exist")


class PresetNamePattern(Base):
    """Minor 4: the strict agent name pattern stays (names become file names); a preset agent whose name it refuses
    is shown as not editable for skills, with the writer's own refusal as the reason."""

    def plant(self, name):
        obj = json.loads(self.config.read_text())
        obj["presets"][obj["preset"]][name] = {"model": "openai/gpt-5", "skills": []}
        self.config.write_text(json.dumps(obj, indent=2) + "\n")

    def test_the_snapshot_and_the_writer_agree_on_a_name_the_pattern_refuses(self):
        for name in ("Big-Agent", "big.agent"):
            with self.subTest(name=name):
                self.plant(name)
                document = {"agents": [{"name": name}, {"name": "impl-claude"}]}
                skillset.annotate(document, config=self.config, agents_dir=self.agents, skills_dir=self.skills)
                shown, fine = document["agents"]
                with self.assertRaises(skillset.SkillsetError) as caught:
                    skillset.add(name, "code-assurance", config=self.config, map_path=self.map, backups=self.backups,
                                 skills_dir=self.skills, agents_dir=self.agents, structure=self.structure)
                self.assertFalse(shown["skills_editable"])
                self.assertIn(str(caught.exception), shown["skills_reason"])
                self.assertIn("file names", shown["skills_reason"])
                self.assertTrue(fine["skills_editable"], "a valid preset name stays editable")


class ExpectShape(Base):
    """The note on `expect`, module side: every writer refuses an `expect` that does not look like a rev before it
    compares anything (agentfiles.check_expect, roster.expecting), changing nothing."""

    def test_a_malformed_expect_is_refused_by_every_agent_writer(self):
        self.add("xavier", template="verifier")
        before = self.snapshot()
        where = dict(config=self.config, map_path=self.map, structure=self.structure)
        for bad in (" ", "0" * 15, "ABCDEF0123456789", "0" * 16 + "\n"):
            with self.subTest(expect=bad):
                self.assertFalse(agentfiles.expect_ok(bad))
                for write in (lambda: roster.text_agent("xavier", "New.", agents_dir=self.agents, backups=self.backups,
                                                        expect=bad, **where),
                              lambda: roster.rename_agent("xavier", "yusuf", expect=bad, **self.paths),
                              lambda: roster.remove_agent("xavier", expect=bad, **self.paths),
                              lambda: roster.remove_agent("impl-claude", expect=bad, **self.paths),
                              lambda: skillset.add("impl-claude", "code-assurance", config=self.config,
                                                   map_path=self.map, backups=self.backups, skills_dir=self.skills,
                                                   agents_dir=self.agents, structure=self.structure, expect=bad),
                              lambda: agentfiles.check_expect(bad, bad, "x")):
                    with self.assertRaisesRegex(skillset.SkillsetError, "expect must be") as caught:
                        write()
                    self.assertNotIsInstance(caught.exception, agentfiles.StaleError)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(agentfiles.expect_ok(None))
        self.assertTrue(agentfiles.expect_ok(agentfiles.rev(b"x")))
