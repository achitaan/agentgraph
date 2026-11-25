"""`agentgraph check` reports teams.problems next to the flow problems; no teams adds nothing."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agentgraph import check, teams
from tests.test_teams import make_structure


def node(node_id, agent, depends_on=(), kind="implement"):
    return {"id": node_id, "type": kind, "agent": agent, "goal": "Do it", "status": "pending",
            "depends_on": list(depends_on), "scope": [f"src/{node_id}.py"], "done_when": "tests pass"}


def graph(nodes):
    return {"task": "team-task", "goal": "Check teams", "status": "running", "created": 1, "updated": 2, "revision": 1,
            "objectives": [{"id": "o1", "text": "Deliver", "nodes": [nodes[-1]["id"]]}], "nodes": nodes}


class TeamsInCheck(unittest.TestCase):
    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())) / "structure")
        self.graphs = self.root.parent / "graphs"
        self.graphs.mkdir()

    def run_check(self, nodes):
        (self.graphs / "team-task.json").write_text(json.dumps(graph(nodes)), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(self.graphs), "--structure", str(self.root)])
        return code, out.getvalue()

    def test_no_teams_adds_nothing(self):
        code, out = self.run_check([node("n1", "impl-openai"), node("n2", "impl-claude"),
                                    node("c1", "reviewer", ["n1"], "check")])
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("OK"), out)

    def test_a_team_the_graph_does_not_follow_is_a_problem(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        code, out = self.run_check([node("n1", "impl-openai"), node("n2", "impl-claude"),
                                    node("c1", "reviewer", ["n1"], "check")])
        self.assertEqual(code, 1, out)
        self.assertIn("PROBLEMS", out)
        self.assertIn("team reviewer: the work of n2 (impl-claude) is not reported to reviewer", out)

    def test_a_team_the_graph_follows_passes(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        code, out = self.run_check([node("n1", "impl-openai"), node("n2", "impl-claude"),
                                    node("c1", "reviewer", ["n1", "n2"], "check")])
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
