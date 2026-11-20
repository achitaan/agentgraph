"""Delegation read from the structure folder (n109): the `use_when` setting and `agentgraph route`, a path's task
graph written by script (`agentgraph flow scaffold`), `check` enforcing `escalates_to` (a problem) and `pool` (a
warning), and the run message naming the groups whose rule binds a run. Scratch folders only."""
import contextlib
import io
import itertools
import json
import random
import re
import tempfile
import unittest
from pathlib import Path

from agentgraph import check, flowrun, roster, snapshot_json, sources, structure, teams
from tests.test_structure import graph as graph_of, make_structure, node, tree

ROOT = Path(__file__).resolve().parent.parent
HUBS = frozenset({"orchestrator"})
RULE = ("a step to a new agent is a node it runs, depending on the current node; a return is no node: the current "
        "node is a gate whose feedback_to names that agent's earlier node, which becomes the current node.")
OTHERS = ("These apply too; follow each from every implement, design, research or decision node of its first agent, "
          "by the same rule:")
CHECKS = "Make these check nodes, or each starts a path again without end:"
WORK = ("implement", "design", "research", "decision")


def capture(function, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = function(argv)
    return code, out.getvalue(), err.getvalue()


class Scratch(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = make_structure(self.temporary / "structure")
        self.backups = self.temporary / "backups"
        self.where = {"config": self.temporary / "none.json", "map_path": self.temporary / "none.md",
                      "agents_dir": self.temporary / "agents"}

    def file(self, rel):
        return (self.root / rel).read_bytes()


# --- check: escalates_to and pool -----------------------------------------------------------------

class FolderRulesInCheck(Scratch):
    def run_check(self, nodes):
        graphs = self.temporary / "graphs"
        graphs.mkdir(exist_ok=True)
        (graphs / "flows.json").write_text(json.dumps(graph_of(nodes)))
        return capture(check.main, ["--dir", str(graphs), "--structure", str(self.root)])[:2]

    def test_an_escalation_off_the_folder_is_a_problem(self):
        code, out = self.run_check([node("n1", "implement", "impl-openai", tiers=["impl-openai", "impl-hard"])])
        self.assertEqual(code, 1, out)
        self.assertIn("n1 was escalated from impl-openai to impl-hard; impl-openai escalates to impl-claude", out)
        for tiers in (["impl-openai", "impl-openai", "impl-claude", "impl-hard"], ["designer", "impl-hard"],
                      ["impl-openai"]):
            with self.subTest(tiers=tiers):
                code, out = self.run_check([node("n1", "implement", tiers[-1], tiers=tiers)])
                self.assertEqual(code, 0, out)

    def test_more_parallel_nodes_than_the_pool_is_a_warning(self):
        nodes = [node(f"n{index}", "implement", "impl-openai", scope=[f"src/{index}.py"]) for index in range(4)]
        code, out = self.run_check(nodes)
        self.assertEqual(code, 0, out)
        self.assertIn("warning: 4 nodes of impl-openai can run at once (n0, n1, n2, n3), more than its pool of 3", out)
        nodes[3]["depends_on"] = ["n0"]
        code, out = self.run_check(nodes)
        self.assertNotIn("warning:", out)

    def test_the_widest_set_is_a_maximum_antichain(self):
        rng = random.Random(109)
        for _ in range(60):
            count = rng.randint(1, 8)
            ids = [f"n{index}" for index in range(count)]
            raw = [node(item, "implement", "impl-openai",
                        depends_on=[other for other in ids[:index] if rng.random() < 0.3])
                   for index, item in enumerate(ids)]
            built = sources.graph_from_raw(graph_of(raw), Path("g.json"))
            after = {item: set() for item in ids}
            for entry in reversed(raw):
                for parent in entry["depends_on"]:
                    after[parent] |= {entry["id"]} | after[entry["id"]]
            best = max(len(group) for size in range(1, count + 1) for group in itertools.combinations(ids, size)
                       if all(b not in after[a] and a not in after[b] for a, b in itertools.combinations(group, 2)))
            widest = check._widest(ids, after)
            self.assertEqual(len(widest), best)
            self.assertTrue(all(b not in after[a] and a not in after[b] for a, b in itertools.combinations(widest, 2)))
            self.assertEqual(bool(check.pool_problems(built, {"impl-openai": 1})), best > 1)


if __name__ == "__main__":
    unittest.main()
