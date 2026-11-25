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

from agentgraph import check, flowrun, roster, routing, snapshot_json, sources, structure, teams
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


class UseWhen(Scratch):
    def test_it_is_a_setting_not_the_description(self):
        (self.root / "20-build" / "impl-openai.md").write_text(
            "pool: 3\nuse_when: Bounded tasks with a check to run\nescalates_to: impl-claude\n\nThe default.\n", encoding="utf-8")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual(shape["about"]["impl-openai"], "The default.")
        self.assertEqual(shape["pool"]["impl-openai"], 3)
        self.assertIn(("impl-openai", "impl-claude"), shape["escalation"])
        self.assertEqual(routing.use_when_of(self.file("20-build/impl-openai.md")), "Bounded tasks with a check to run")

    def test_set_replace_and_take_away_keep_every_other_byte(self):
        for rel, expected in (
                ("20-build/designer.md", "use_when: interface work\n\nDesigns things.\nSecond line: with a colon.\n"),
                ("20-build/impl-openai.md", "pool: 3\nescalates_to: impl-claude\nuse_when: interface work\n\n"
                                            "The default implementer.\n"),
                ("40-understand/researcher.md", "use_when: interface work\n\nFinds things out.\n")):
            with self.subTest(rel=rel):
                name = Path(rel).stem
                before = self.file(rel)
                about = snapshot_json.load_structure(self.root)["about"].get(name)
                done = routing.set_use_when(name, "  interface work ", self.root, backups=self.backups, **self.where)
                self.assertTrue(done["changed"])
                self.assertEqual(self.file(rel).decode(), expected)
                self.assertEqual(snapshot_json.load_structure(self.root)["about"].get(name), about)
                again = routing.set_use_when(name, "interface work", self.root, backups=self.backups, **self.where)
                self.assertFalse(again["changed"])
                routing.set_use_when(name, "other work: with a colon", self.root, backups=self.backups, **self.where)
                self.assertEqual(routing.use_when_of(self.file(rel)), "other work: with a colon")
                routing.set_use_when(name, "", self.root, backups=self.backups, **self.where)
                self.assertEqual(self.file(rel), before)

    def test_the_undo_record_puts_the_bytes_back_and_can_be_undone_in_turn(self):
        before = self.file("30-verify/reviewer.md")
        done = routing.set_use_when("reviewer", "the final gate", self.root, backups=self.backups, **self.where)
        after = self.file("30-verify/reviewer.md")
        back = routing.restore_use_when(done["record"], self.root, backups=self.backups)
        self.assertEqual(self.file("30-verify/reviewer.md"), before)
        self.assertEqual(back["use_when"], "")
        routing.restore_use_when(back["record"], self.root, backups=self.backups)
        self.assertEqual(self.file("30-verify/reviewer.md"), after)
        with self.assertRaisesRegex(routing.RouteError, "changed after that edit"):
            routing.restore_use_when(back["record"], self.root, backups=self.backups)
        for bad in ("", str(self.temporary / "x.json"), done["record"] + "x", None):
            with self.subTest(record=bad), self.assertRaisesRegex(routing.RouteError, "invalid undo record"):
                routing.restore_use_when(bad, self.root, backups=self.backups)

    def test_refusals_write_nothing(self):
        state = tree(self.root)
        for name, text, why in (("reviewer", "two\nlines", "one line"), ("reviewer", "x" * 241, "240"),
                                ("reviewer", "`quoted`", "quote"), ("reviewer", "a\x07b", "control"),
                                ("reviewer", 3, "text"), ("ghost", "work", "no ghost.md"), ("Bad", "work", "lowercase")):
            with self.subTest(text=text), self.assertRaisesRegex(routing.RouteError, why):
                routing.set_use_when(name, text, self.root, backups=self.backups, **self.where)
        self.assertEqual(tree(self.root), state)
        self.assertFalse(self.backups.exists())

    def test_a_stale_view_is_refused_with_the_current_rev(self):
        rev = roster.agent_rev("reviewer", structure=self.root, **self.where)
        stale = "0" * 16
        state = tree(self.root)
        with self.assertRaises(routing.StaleError) as caught:
            routing.set_use_when("reviewer", "the final gate", self.root, expect=stale, backups=self.backups,
                                 **self.where)
        self.assertEqual(caught.exception.rev, rev)
        self.assertEqual(tree(self.root), state)
        done = routing.set_use_when("reviewer", "the final gate", self.root, expect=rev, backups=self.backups,
                                    **self.where)
        self.assertNotEqual(done["rev"], rev)
        self.assertEqual(done["rev"], roster.agent_rev("reviewer", structure=self.root, **self.where))
        with self.assertRaisesRegex(routing.RouteError, "16 lowercase hex"):
            routing.set_use_when("reviewer", "x", self.root, expect="nope", backups=self.backups, **self.where)

    def test_the_snapshot_carries_it_as_use_when(self):
        routing.set_use_when("verifier", "one loop item, every round", self.root, backups=self.backups, **self.where)
        document = {"agents": [{"name": "verifier"}, {"name": "reviewer"}, {"name": "ghost"}]}
        roster.annotate(document, structure=self.root, **self.where)
        self.assertEqual([agent["use_when"] for agent in document["agents"]], ["one loop item, every round", "", ""])

    def test_the_command_sets_and_restores(self):
        code, out, _ = capture(routing.main, ["use-when", "reviewer", "--text", "the final gate", "--structure",
                                              str(self.root), "--backups", str(self.backups), "--json",
                                              "--config", str(self.where["config"]), "--map",
                                              str(self.where["map_path"]), "--agents-dir",
                                              str(self.where["agents_dir"])])
        self.assertEqual(code, 0, out)
        record = json.loads(out)["record"]
        self.assertEqual(routing.use_when_of(self.file("30-verify/reviewer.md")), "the final gate")
        code, out, err = capture(routing.main, ["restore", record, "--structure", str(self.root),
                                                "--backups", str(self.backups)])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.file("30-verify/reviewer.md"), b"Reviews a finished change.\n")
        code, out, _ = capture(routing.main, ["use-when", "reviewer", "--text", "a\nb", "--structure", str(self.root),
                                              "--backups", str(self.backups), "--json"])
        self.assertEqual(code, 1)
        self.assertIn("one line", json.loads(out)["error"])


class Route(Scratch):
    def skills_md(self):
        path = self.temporary / "SKILLS.md"
        path.write_text("| Agent | Skills |\n|---|---|\n| `orchestrator` | all |\n| `impl-openai` | `code-assurance`, "
                        "`engineering-delivery` |\n| `reviewer` | none |\n", encoding="utf-8")
        return path

    def test_the_table_comes_from_the_folder(self):
        routing.set_use_when("impl-openai", "bounded tasks", self.root, backups=self.backups, **self.where)
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        loop = structure.add_flow(["impl-openai", "verifier", "impl-openai"], None, self.root)[0]
        found = routing.table(self.root, self.skills_md())
        rows = {row["name"]: row for row in found["agents"]}
        self.assertNotIn("councillor-a", rows)  # a seat: its council stands for it
        self.assertEqual(rows["council"]["seats"], "councillor-")
        self.assertEqual(rows["impl-openai"], {
            "name": "impl-openai", "group": "build", "pool": 3, "escalates_to": "impl-claude",
            "use_when": "bounded tasks", "seats": None, "skills": ["code-assurance", "engineering-delivery"],
            "leads": [], "reports_to": ["reviewer"],
            "paths": [{"id": loop["id"], "name": None, "path": ["impl-openai", "verifier", "impl-openai"]}]})
        self.assertEqual(rows["reviewer"]["leads"], [{"id": "reviewer", "members": ["impl-openai", "impl-claude"]}])
        self.assertEqual(rows["reviewer"]["skills"], [])
        self.assertEqual(rows["orchestrator"]["skills"], ["all"])
        self.assertIsNone(rows["designer"]["skills"])  # not in SKILLS.md
        text = routing.text(found)
        self.assertIn("impl-openai [build, pool 3, up impl-claude]: bounded tasks; skills code-assurance, "
                      "engineering-delivery; reports to reviewer; paths impl-openai → verifier → impl-openai\n", text)
        self.assertIn("reviewer [verify]: -; skills none; leads impl-openai, impl-claude\n", text)

    def test_the_command_prints_plain_or_json(self):
        code, out, _ = capture(routing.main, ["--structure", str(self.root), "--map", str(self.skills_md())])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Routing (agentgraph route"))
        code, out, _ = capture(routing.main, ["--structure", str(self.root), "--json"])
        self.assertEqual({row["name"] for row in json.loads(out)["agents"]} >= {"impl-openai", "council"}, True)
        code, _, err = capture(routing.main, ["--structure", str(self.temporary / "none")])
        self.assertEqual(code, 1)
        self.assertIn("no structure folder", err)

    def test_the_real_folder_stays_small(self):
        found = routing.table(ROOT / "structure", ROOT / "SKILLS.md")
        self.assertEqual(len(found["agents"]), 12)
        size = len(routing.text(found))
        self.assertLess(size, 4000, size)


# --- the task graph by script ---------------------------------------------------------------------

def filled(raw):
    """The scaffold with its blanks filled: every empty goal, done_when and objective text; nothing else."""
    if isinstance(raw, dict):
        return {key: (f"{key} here" if value == "" and key in ("goal", "done_when", "text") else filled(value))
                for key, value in raw.items()}
    if isinstance(raw, list):
        return [filled(item) for item in raw]
    return raw


class Scaffold(Scratch):
    def setUp(self):
        super().setUp()
        self.graphs = self.temporary / "graphs"

    def check(self, task):
        code, out, _ = capture(check.main, ["--dir", str(self.graphs), "--structure", str(self.root), "--task", task])
        return code, out

    def scaffold_passes(self, flow_id, task):
        """Scaffold, then fill the blanks only: `check` passes with every drawn path and group (first attempt)."""
        path, raw = structure.flow_scaffold(flow_id, task, self.root, graph_dir=self.graphs, now=1000)
        self.assertEqual(path, self.graphs / f"{task}.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), raw)
        self.assertTrue(all(item["status"] == "pending" and item["goal"] == "" and item["done_when"] == ""
                            for item in raw["nodes"]))
        for item in raw["nodes"]:
            self.assertEqual(item["artifacts"][0]["path"], f".opencode/artifacts/{task}/{item['id']}/r1/handoff.md")
        code, out = self.check(task)
        self.assertEqual(code, 1, out)  # unfilled: blanks are problems
        path.write_text(json.dumps(filled(raw)), encoding="utf-8")
        code, out = self.check(task)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("OK"), out)
        return raw

    def test_a_straight_path(self):
        flow = structure.add_flow(["researcher", "impl-openai"], None, self.root)[0]
        raw = self.scaffold_passes(flow["id"], "straight")
        self.assertEqual([(item["agent"], item["depends_on"]) for item in raw["nodes"]],
                         [("researcher", []), ("impl-openai", ["n1"])])
        self.assertEqual(raw["nodes"][1]["inputs"], [{"from": "n1", "artifact": "handoff", "revision": 1}])

    def test_a_loop(self):
        flow = structure.add_flow(["impl-openai", "verifier", "impl-openai"], None, self.root, name="check-loop")[0]
        raw = self.scaffold_passes("check-loop", "loop")  # by the path's name
        gate = raw["nodes"][1]
        self.assertEqual((gate["type"], gate["feedback_to"], gate["pattern"]), ("gate", ["n1"], "evaluate"))
        self.assertIn({"from": "n2", "artifact": "handoff", "revision": 1}, raw["nodes"][0]["inputs"])
        self.assertTrue(flow["id"])

    def test_the_chain_of_three(self):
        run = structure.add_flow(["researcher", "impl-openai"], None, self.root)[0]
        structure.add_flow(["impl-openai", "designer"], None, self.root)
        structure.add_flow(["designer", "reviewer"], None, self.root)
        raw = self.scaffold_passes(run["id"], "chain")
        self.assertEqual([item["agent"] for item in raw["nodes"]], ["researcher", "impl-openai", "designer", "reviewer"])

    def test_a_cycle_between_two_paths(self):
        run = structure.add_flow(["researcher", "impl-openai"], None, self.root)[0]
        structure.add_flow(["impl-openai", "researcher"], None, self.root)
        raw = self.scaffold_passes(run["id"], "cycle")
        self.assertEqual([(item["agent"], item["type"]) for item in raw["nodes"]],
                         [("researcher", "implement"), ("impl-openai", "implement"), ("researcher", "check")])

    def test_a_path_through_a_group(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        run = structure.add_flow(["impl-openai", "verifier", "impl-claude"], None, self.root)[0]
        raw = self.scaffold_passes(run["id"], "group")
        join = raw["nodes"][-1]
        self.assertEqual((join["agent"], join["type"], join["depends_on"]), ("reviewer", "join", ["n1", "n3"]))

    def test_it_never_writes_over_a_file(self):
        flow = structure.add_flow(["researcher", "impl-openai"], None, self.root)[0]
        self.graphs.mkdir()
        (self.graphs / "taken.json").write_text("mine", encoding="utf-8")
        with self.assertRaisesRegex(structure.StructureError, "already exists"):
            structure.flow_scaffold(flow["id"], "taken", self.root, graph_dir=self.graphs)
        self.assertEqual((self.graphs / "taken.json").read_text(encoding="utf-8"), "mine")
        self.assertEqual(sorted(path.name for path in self.graphs.iterdir()), ["taken.json"])
        for bad, why in (("Bad", "task name"), ("../x", "task name"), ("", "task name")):
            with self.subTest(task=bad), self.assertRaisesRegex(structure.StructureError, why):
                structure.flow_scaffold(flow["id"], bad, self.root, graph_dir=self.graphs)
        with self.assertRaisesRegex(structure.StructureError, "no flow or path"):
            structure.flow_scaffold("ghost", "t", self.root, graph_dir=self.graphs)

    def test_the_command_and_the_message_say_how(self):
        flow = structure.add_flow(["researcher", "impl-openai"], None, self.root)[0]
        text = structure.flow_prompt(flow["id"], "a goal", self.root)
        self.assertIn("`agentgraph flow scaffold P --task T` (P: the path's name or id) writes it", text)
        code, out, _ = capture(structure.main, ["--structure", str(self.root), "flow", "scaffold", flow["id"],
                                                "--task", "by-cli", "--dir", str(self.graphs), "--json"])
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["path"], str(self.graphs / "by-cli.json"))
        code, out, _ = capture(structure.main, ["--structure", str(self.root), "flow", "scaffold", flow["id"],
                                                "--task", "by-cli", "--dir", str(self.graphs), "--json"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", json.loads(out)["error"])


# --- groups in the run message --------------------------------------------------------------------

GROUPS = "These groups apply: give each lead a join node depending on every node of its members:"


class GroupsInTheMessage(Scratch):
    def message(self, flow_id):
        return structure.flow_prompt(flow_id, "a goal", self.root)

    def groups(self, text):
        if GROUPS not in text:
            return []
        part = text.split(GROUPS + "\n", 1)[1].split("\n\n", 1)[0]
        found = []
        for line in part.splitlines():
            lead, members = re.fullmatch(r"- (\S+): (.+)", line).groups()
            found.append((lead, members.split(", ")))
        return found

    def from_message(self, text, skip=()):
        """The graph the message's words describe: the steps under RULE, each listed path followed from every work
        node of its first agent (check nodes as named), then each listed group's lead given a join node that
        depends on every node of its members, in the order listed."""
        self.assertIn(RULE, text.replace("\n", " "))
        nodes = []
        others, checks = [], set()
        if OTHERS in text:
            part = text.split(OTHERS + "\n", 1)[1].split("\n\n", 1)[0]
            in_checks = False
            for line in part.splitlines():
                if line == CHECKS:
                    in_checks = True
                elif in_checks:
                    checks.add(re.fullmatch(r"- (\S+), added for (\S+): .*", line).group(1, 2))
                else:
                    called, steps = line[2:].split(": ", 1)
                    others.append((called, steps.split(" → ")))

        def follow(agents, first=None, called=None):
            chain, current = ({agents[0]: first}, first) if first else ({}, None)
            for agent in agents[1:] if first else agents:
                if agent in chain:
                    held = next(item for item in nodes if item["id"] == current)
                    held.update(type="gate", status="pending")
                    held.setdefault("feedback_to", []).append(chain[agent])
                    current = chain[agent]
                    continue
                node_id = f"n{len(nodes)}"
                kind = "check" if (agent, called) in checks else "implement"
                nodes.append(node(node_id, kind, agent, status="pending", depends_on=[current] if current else []))
                chain[agent], current = node_id, node_id

        steps = re.findall(r"^\d+\. (\S+)( \(return\)| \(the hub: you\))?$", text, re.M)
        follow([agent for agent, mark in steps if mark != " (the hub: you)"])
        done, changed = set(), True
        while changed:
            changed = False
            for called, agents in others:
                agents = [agent for agent in agents if agent != "orchestrator"]
                for item in list(nodes):
                    if item["agent"] == agents[0] and item["type"] in WORK and (called, item["id"]) not in done:
                        done.add((called, item["id"]))
                        follow(agents, item["id"], called)
                        changed = True
        for lead, members in self.groups(text):
            if lead in skip:
                continue
            nodes.append(node(f"n{len(nodes)}", "join", lead, status="pending",
                              depends_on=[item["id"] for item in nodes if item["agent"] in members]))
        return check.graph_from_raw(graph_of(nodes), Path("/nonexistent/.opencode/graph/run.json"))

    def problems(self, flow_id, skip=()):
        shape = snapshot_json.load_structure(self.root)
        built = self.from_message(self.message(flow_id), skip)
        return (check.validate(built) + check.flow_problems(built, shape["flows"], HUBS, shape["seats"])
                + teams.problems(shape["teams"], built, shape["seats"]))

    def test_a_group_that_binds_is_named_and_the_graph_from_the_words_passes(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        run = structure.add_flow(["impl-openai", "verifier", "impl-claude"], None, self.root)[0]
        text = self.message(run["id"])
        self.assertIn("\n\n" + GROUPS + "\n- reviewer: impl-openai, impl-claude\n\n", text)
        self.assertEqual(self.problems(run["id"]), [])
        left = self.problems(run["id"], skip={"reviewer"})
        self.assertEqual(len(left), 1)
        self.assertTrue(left[0].startswith("team reviewer:"), left)

    def test_a_group_is_named_only_when_check_could_fail_on_it(self):
        teams.add("reviewer", ["impl-openai", "designer"], structure_dir=self.root)  # one member node only
        loop = structure.add_flow(["impl-openai", "verifier", "impl-openai"], None, self.root)[0]
        self.assertNotIn(GROUPS, self.message(loop["id"]))
        teams.add("impl-claude", ["impl-openai", "researcher"], structure_dir=self.root)  # its lead already follows
        led = structure.add_flow(["researcher", "impl-openai", "impl-claude"], None, self.root)[0]
        self.assertNotIn(GROUPS, self.message(led["id"]))
        self.assertEqual(self.problems(led["id"]), [])
        # Now two impl-openai nodes (the loop applies to each) and reviewer is not drawn: that group can fail.
        both = structure.add_flow(["impl-openai", "verifier", "researcher"], None, self.root)[0]
        self.assertEqual(self.groups(self.message(both["id"])), [("reviewer", ["impl-openai", "designer"])])
        self.assertEqual(self.problems(both["id"]), [])

    def test_a_lead_that_reports_to_another_group_comes_first(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        teams.add("designer", ["reviewer", "researcher"], structure_dir=self.root)
        run = structure.add_flow(["researcher", "impl-openai", "verifier", "impl-claude"], None, self.root)[0]
        text = self.message(run["id"])
        self.assertEqual(self.groups(text), [("reviewer", ["impl-openai", "impl-claude"]),
                                             ("designer", ["reviewer", "researcher"])])
        self.assertEqual(self.problems(run["id"]), [])
        self.assertNotEqual(self.problems(run["id"], skip={"designer"}), [])

    def test_the_groups_are_counted_and_capped(self):
        flow = {"id": "x", "path": ["a", "b"], "text": "", "name": None}
        bare = flowrun.template_size(flow, HUBS)
        members = [chr(ord("a") + index) * 64 for index in range(12)]
        groups = [{"id": str(index), "lead": "l" * 64, "members": members} for index in range(5)]
        for count in (1, 3, 5):
            with self.subTest(count=count):
                bound = (flowrun.GROUPS_FIXED
                         + sum(len(team["lead"]) + flowrun.GROUP_SHARE
                               + sum(len(member) + flowrun.MEMBER_SHARE for member in team["members"])
                               for team in groups[:flowrun.GROUPS_SHOWN])
                         + sum(len(team["lead"]) + flowrun.GROUP_MORE_SHARE for team in groups[flowrun.GROUPS_SHOWN:]))
                self.assertLessEqual(flowrun.template_size(flow, HUBS, groups=groups[:count]) - bare, bound)
        self.assertLessEqual(len("\n" + GROUPS + "\n") + len("- and 9999 more, by lead: ") + 1, flowrun.GROUPS_FIXED)


class TemplateBudget(unittest.TestCase):
    def test_the_fixed_text_stays_at_775_with_the_scaffold_named(self):
        self.assertLessEqual(flowrun.TEMPLATE_FIXED, 775)
        self.assertIn("`agentgraph flow scaffold P --task T`",
                      flowrun.run_message({"id": "x", "path": ["a", "b"], "text": "", "name": None}, "g", HUBS))
        for path in (["orchestrator", "a"], ["a", "orchestrator"], ["orchestrator", "a", "orchestrator"], ["a", "a"],
                     ["a", "b", "a"]):
            for called in ({"name": "n" * 32, "id": "x"}, {"name": None, "id": "i" * 80}):
                size = flowrun.template_size({"path": path, "text": "", **called}, HUBS)
                self.assertLessEqual(size, flowrun.TEMPLATE_FIXED + sum(len(agent) + flowrun.STEP_SHARE
                                                                        for agent in path)
                                     + len(called["name"] or called["id"]))


# --- check: escalates_to and pool -----------------------------------------------------------------

class FolderRulesInCheck(Scratch):
    def run_check(self, nodes):
        graphs = self.temporary / "graphs"
        graphs.mkdir(exist_ok=True)
        (graphs / "flows.json").write_text(json.dumps(graph_of(nodes)), encoding="utf-8")
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
