"""The real structure/ folder gives every agent a description."""
import re
import unittest
from pathlib import Path

from agentgraph import snapshot_json

AGENTS = ("orchestrator", "council", "oracle", "oracle-lite", "designer", "impl-openai",
          "impl-claude", "impl-hard", "reviewer", "verifier", "explorer", "researcher")


class StructureDescriptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shape = snapshot_json.load_structure(snapshot_json.STRUCTURE_DIR)
        assert cls.shape is not None, "structure/ folder is missing"
        cls.about = cls.shape["about"]

    def test_every_agent_file_is_one_of_the_twelve(self):
        self.assertEqual(sorted(self.shape["group_of"]), sorted(AGENTS))

    def test_every_agent_has_a_short_plain_description(self):
        for name in self.shape["group_of"]:
            text = self.about.get(name, "")
            with self.subTest(agent=name):
                self.assertLessEqual(len(text), 320)
                self.assertNotIn("\u2014", text)
                self.assertGreaterEqual(len(re.findall(r"[.!?](?:\s|$)", text)), 2, text)

    def test_the_decide_and_research_agents_say_what_sets_them_apart(self):
        self.assertIn("depth", self.about["oracle"].lower())
        lite = self.about["oracle-lite"].lower()
        self.assertTrue("same skills as oracle" in lite and "opposite" in lite, lite)
        self.assertIn("deep research", self.about["researcher"].lower())

    def test_the_snapshot_carries_the_text(self):
        from agentgraph.demo import demo_snapshot
        doc = snapshot_json.snapshot_document(demo_snapshot(1000), "demo", structure=self.shape)
        for agent in doc["agents"]:
            self.assertEqual(agent["about"], self.about.get(agent["name"], ""))


if __name__ == "__main__":
    unittest.main()
