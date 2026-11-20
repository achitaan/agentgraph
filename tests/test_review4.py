"""Correctness review round 4, lane F (`.opencode/artifacts/agentgraph-desktop/n8/r4/findings.md`): a flow that
cannot be read is removable with its command file; stats and `check` share one definition of "followed", a run
counts under the longest flow it follows and the shapes no graph can show have unknown stats; the run message says
what a step after a return depends on, tested against `check` with graphs built from the message's own words; one
name definition per kind. Scratch folders only."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from agentgraph import agentfiles, check, flowcommands, flowrun, names, roster, server, skillfiles, skillset
from agentgraph import snapshot_json, structure
from agentgraph.demo import demo_snapshot
from tests.test_flowrun import graph_raw, run_cli
from tests.test_structure import gate, graph, make_structure, node, tree, write_flow

ROOT = Path(__file__).resolve().parent.parent
HUBS = frozenset({"orchestrator"})


class Scratch(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = make_structure(self.temporary / "structure")
        self.commands = self.temporary / "commands"
        self.commands.mkdir()

    def state(self):
        return tree(self.root), tree(self.commands)


class UnreadableFlows(Scratch):
    """A flow whose agent is gone cannot be read as a flow; it is listed with a way to remove it, and removing it
    takes its command file too while that file is exactly what was written for it."""

    def strand(self, name="design-it"):
        flow = structure.add_flow(["designer", "reviewer"], "Design, then review.", self.root, name=name,
                                  command=True, commands_dir=self.commands)[0]
        agent = self.root / "20-build" / "designer.md"
        self.designer = agent.read_bytes()
        agent.unlink()  # the agent goes: the flow can no longer be read
        return flow

    def test_removing_it_removes_its_command_file(self):
        flow = self.strand()
        self.assertTrue((self.commands / "design-it.md").is_file())
        self.assertEqual(structure.drawn_flows(self.root), [])
        gone = structure.remove_flow(flow["id"], self.root, commands_dir=self.commands)
        self.assertIsNone(gone)
        self.assertEqual(tree(self.commands), {})
        self.assertNotIn("flows", tree(self.root))

    def test_a_hand_changed_or_borrowed_command_file_stays(self):
        flow = self.strand()
        changed = (self.commands / "design-it.md").read_bytes() + b"by hand\n"
        (self.commands / "design-it.md").write_bytes(changed)
        structure.remove_flow(flow["id"], self.root, commands_dir=self.commands)
        self.assertEqual((self.commands / "design-it.md").read_bytes(), changed)
        # The name is a readable path's now: its command file is that path's, never the stranded flow's.
        (self.root / "20-build" / "designer.md").write_bytes(self.designer)
        flow = self.strand("design-it-2")
        other = structure.add_flow(["impl-openai", "reviewer"], None, self.root)[0]
        (self.root / "flows" / f"{other['id']}.md").write_text(
            "path: impl-openai -> reviewer\nname: design-it-2\n\nReview.\n")
        before = (self.commands / "design-it-2.md").read_bytes()
        structure.remove_flow(flow["id"], self.root, commands_dir=self.commands)
        self.assertEqual((self.commands / "design-it-2.md").read_bytes(), before)

    def test_it_is_listed_with_the_way_to_remove_it(self):
        flow = self.strand()
        shape = snapshot_json.load_structure(self.root)
        self.assertTrue(any(item.startswith(f"structure: flow {flow['id']} ignored: ")
                            and item.endswith(f"`agentgraph flow remove {flow['id']}` removes it (and its command file)")
                            for item in shape["warnings"]), shape["warnings"])
        document = snapshot_json.snapshot_document(demo_snapshot(1000), "demo", structure=shape)
        self.assertTrue(any(f"agentgraph flow remove {flow['id']}" in item for item in document["warnings"]))
        code, out, _ = run_cli("--structure", str(self.root), "flows", "--json")
        listed = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([item["id"] for item in listed["unreadable"]], [flow["id"]])
        self.assertEqual(listed["unreadable"][0]["rev"],
                         agentfiles.rev((self.root / "flows" / f"{flow['id']}.md").read_bytes()))
        code, out, _ = run_cli("--structure", str(self.root), "flows")
        self.assertIn(f"{flow['id']}: cannot be read as a flow (", out)
        self.assertIn(f"`agentgraph flow remove {flow['id']}` removes it", out)
        self.assertNotIn("no drawn flows", out)

    def test_the_command_line_and_the_endpoint_remove_it(self):
        flow = self.strand()
        code, out, _ = run_cli("--structure", str(self.root), "flow", "remove", flow["id"], "--commands",
                               str(self.commands), "--json")
        self.assertEqual((code, json.loads(out)["flows"]), (0, []))
        self.assertEqual(tree(self.commands), {})

    def test_a_stale_rev_refuses_its_removal_too(self):
        flow = self.strand()
        before = self.state()
        with self.assertRaisesRegex(structure.StaleError, "changed since you opened it"):
            structure.remove_flow(flow["id"], self.root, commands_dir=self.commands, expect=flow["rev"][::-1])
        self.assertEqual(self.state(), before)
        current = snapshot_json.load_structure(self.root)["unreadable_flows"][0]["rev"]
        structure.remove_flow(flow["id"], self.root, commands_dir=self.commands, expect=current)
        self.assertEqual(tree(self.commands), {})


class OneDefinitionOfFollowed(unittest.TestCase):
    """`check` and the stats agree: a graph `check` rejects for a flow is never counted as a run of it."""

    FLOW = {"id": "loop", "path": ["impl-openai", "verifier", "impl-openai"], "text": ""}

    def graph(self, nodes, status="running"):
        return check.graph_from_raw({**graph(nodes), "status": status}, Path("/nonexistent/.opencode/graph/g.json"))

    def test_a_graph_with_a_followed_and_an_unfollowed_start_is_rejected_and_not_counted(self):
        nodes = [node("a1", "implement", "impl-openai"), gate("g1", "verifier", ["a1"], ["a1"]),
                 node("a2", "implement", "impl-openai")]
        g = self.graph(nodes)
        self.assertEqual(len(check.flow_problems(g, [self.FLOW], HUBS)), 1)
        self.assertEqual(check.flow_chains(g, self.FLOW, HUBS), [])
        chains, problems = check.follows(g, self.FLOW, HUBS)
        self.assertEqual((len(chains), len(problems)), (1, 1))

    def test_a_passed_start_counts_for_both_alike(self):
        # A finished run: every node passed. `check` accepts it and the stats count its chain; an unfollowed passed
        # start (work done before the flow was drawn) is neither a problem nor a run.
        nodes = [node("a1", "implement", "impl-openai", status="passed"),
                 {**gate("g1", "verifier", ["a1"], ["a1"]), "status": "passed"},
                 node("a2", "implement", "impl-openai", status="passed")]
        g = self.graph(nodes, status="passed")
        self.assertEqual(check.flow_problems(g, [self.FLOW], HUBS), [])
        self.assertEqual(check.flow_chains(g, self.FLOW, HUBS), [{"impl-openai": "a1", "verifier": "g1"}])

    def test_problems_are_what_check_reported_before(self):
        nodes = [node("a1", "implement", "impl-openai"), node("c1", "check", "verifier", depends_on=["a1"])]
        g = self.graph(nodes)
        self.assertEqual(check.flow_problems(g, [self.FLOW], HUBS),
                         ["a1 (impl-openai) is not sent back to impl-openai after verifier (c1): c1 must be a gate "
                          "whose feedback_to names a1: flow impl-openai → verifier → impl-openai"])


class StatsShapes(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = make_structure(self.temporary / "structure")
        self.graphs = self.temporary / "graphs"
        self.graphs.mkdir()
        flowrun._CACHE.clear()

    def write(self, name, raw):
        path = self.graphs / f"{name}.json"
        path.write_text(json.dumps(raw))
        return path

    def stats(self, paths):
        shape = snapshot_json.load_structure(self.root)
        return {flow["id"]: flow["stats"] for flow in structure.flows_with_stats(
            shape, ledger=None, graph_paths=paths, db_path=None)}

    def test_a_run_counts_under_the_longest_flow_it_follows(self):
        loop = structure.add_flow(["impl-openai", "verifier", "impl-openai"], None, self.root)[0]
        longer = structure.add_flow(["impl-openai", "verifier", "impl-openai", "reviewer"], None, self.root)[0]
        pair = structure.add_flow(["impl-openai", "verifier"], None, self.root)[0]
        tail = structure.add_flow(["verifier", "impl-openai", "reviewer"], None, self.root)[0]
        full = graph_raw("full", [
            {"id": "a1", "type": "implement", "agent": "impl-openai", "attempts": 1},
            {"id": "g1", "type": "gate", "agent": "verifier", "depends_on": ["a1"], "feedback_to": ["a1"], "attempts": 2},
            {"id": "r1", "type": "check", "agent": "reviewer", "depends_on": ["a1"], "attempts": 1}])
        short = graph_raw("short", [
            {"id": "a1", "type": "implement", "agent": "impl-openai", "attempts": 1},
            {"id": "v1", "type": "check", "agent": "verifier", "depends_on": ["a1"], "attempts": 1}])
        stats = self.stats([self.write("full", full), self.write("short", short)])
        self.assertEqual(stats[longer["id"]]["runs"], 1)
        self.assertIsNone(stats[loop["id"]], "a prefix of the longer flow's run is not a run of the loop")
        self.assertEqual(stats[pair["id"]]["runs"], 1, "only the short graph: the full one is the longer flow's")
        self.assertIsNone(stats[tail["id"]])  # verifier's node is a gate: no node of verifier starts it

    def test_a_flow_inside_a_longer_one_from_another_node_still_counts(self):
        pair = structure.add_flow(["impl-openai", "verifier"], None, self.root)[0]
        longer = structure.add_flow(["impl-openai", "verifier", "reviewer"], None, self.root)[0]
        raw = graph_raw("two", [
            {"id": "a1", "type": "implement", "agent": "impl-openai", "attempts": 1},
            {"id": "v1", "type": "check", "agent": "verifier", "depends_on": ["a1"], "attempts": 1},
            {"id": "r1", "type": "check", "agent": "reviewer", "depends_on": ["v1"], "attempts": 1},
            {"id": "a2", "type": "implement", "agent": "impl-openai", "attempts": 1},
            {"id": "v2", "type": "check", "agent": "verifier", "depends_on": ["a2"], "attempts": 1}])
        stats = self.stats([self.write("two", raw)])
        # The pair's chain from a2 is in no longer chain; its chain from a1 is the longer flow's.
        self.assertEqual(stats[pair["id"]]["runs"], 1)
        self.assertIsNone(stats[longer["id"]], "a2 has no reviewer after it: check rejects the longer flow here")

    def test_shapes_no_graph_can_show_have_unknown_stats(self):
        loop = structure.add_flow(["designer", "designer"], None, self.root)[0]
        hub = structure.add_flow(["orchestrator", "designer"], None, self.root)[0]
        never = structure.add_flow(["designer", "reviewer"], None, self.root)[0]
        stats = self.stats([])
        for flow in (loop, hub):
            self.assertEqual(stats[flow["id"]], flowrun.UNKNOWN, flow["path"])
            self.assertIsNone(stats[flow["id"]]["runs"])
        self.assertIsNone(stats[never["id"]], "a flow that can be measured and never ran")
        code, out, _ = run_cli("--structure", str(self.root), "flows", "--stats", "--ledger", str(self.temporary),
                               "--db", str(self.temporary / "none.db"))
        self.assertEqual(code, 0)
        self.assertEqual(out.count("runs unknown: a task graph cannot show this path"), 2)
        self.assertIn("never ran", out)


class RunMessageAgainstCheck(Scratch):
    """The message's own rule, read from the message: a graph built the way it says passes `check`, for paths with a
    step after a return. The rule's other reading (the step after a return depends on the gate) does not."""

    RULE = ("a step to a new agent is a node it runs, depending on the current node; a return is no node: the current "
            "node is a gate whose feedback_to names that agent's earlier node, which becomes the current node.")

    def from_message(self, text, gate_follows=False):
        """The task graph the message describes: its numbered steps and their marks, under RULE."""
        self.assertIn(self.RULE, text.replace("\n", " "))
        steps = re.findall(r"^\d+\. (\S+)( \(return\)| \(the hub: you\))?$", text, re.M)
        nodes, node_of, current = [], {}, None
        for agent, mark in steps:
            if mark == " (the hub: you)":
                continue
            if mark == " (return)":
                held = next(item for item in nodes if item["id"] == current)
                held.update(type="gate", status="pending")
                held.setdefault("feedback_to", []).append(node_of[agent])
                if not gate_follows:
                    current = node_of[agent]  # "which becomes the current node"
                continue
            node_id = f"n{len(nodes)}"
            nodes.append(node(node_id, "implement" if not nodes else "check", agent,
                              status="running" if not nodes else "pending",
                              depends_on=[current] if current else []))
            node_of[agent], current = node_id, node_id
        return check.graph_from_raw(graph(nodes), Path("/nonexistent/.opencode/graph/run.json"))

    def test_paths_with_a_step_after_a_return(self):
        for path in (["impl-openai", "verifier", "impl-openai", "reviewer"],
                     ["impl-openai", "verifier", "reviewer", "verifier", "designer"],
                     ["orchestrator", "impl-openai", "verifier", "impl-openai", "reviewer", "orchestrator"]):
            with self.subTest(path=path):
                flow = structure.add_flow(path, None, self.root)[0]
                text = structure.flow_prompt(flow["id"], "a goal", self.root)
                shape = snapshot_json.load_structure(self.root)
                hubs = snapshot_json.hub_names(shape)
                built = self.from_message(text)
                self.assertEqual(check.validate(built) + check.flow_problems(built, [flow], hubs, shape["seats"]), [])
                wrong = self.from_message(text, gate_follows=True)
                self.assertNotEqual(check.flow_problems(wrong, [flow], hubs, shape["seats"]), [])
                structure.remove_flow(flow["id"], self.root)

    def test_the_template_still_fits(self):
        flow = {"id": "x", "path": ["a" * 64, "b" * 64, "a" * 64, "c" * 64], "text": "", "name": "n" * 32}
        self.assertLess(flowrun.template_size(flow, HUBS), 1500)


class Names(unittest.TestCase):
    """One definition per kind, in agentgraph.names, and every surface uses it."""

    def test_the_python_surfaces_use_the_one_definition(self):
        self.assertIs(snapshot_json.FLOW_ID, names.FLOW_ID)
        self.assertIs(snapshot_json.FLOW_NAME, names.FLOW_NAME)
        self.assertIs(structure.FLOW_NAME, names.FLOW_NAME)
        self.assertIs(skillfiles._ID, names.SKILL)
        self.assertEqual(names.COMMAND_PATTERN, names.FLOW_NAME_PATTERN)
        self.assertIs(roster.NAME, names.AGENT)
        self.assertIs(agentfiles.NAME, names.AGENT)

    def test_no_module_keeps_its_own_copy_of_a_name_pattern(self):
        # The agent name inside the undo records' folder and path patterns comes from names.py too.
        self.assertIn(names.AGENT_PATTERN, agentfiles.RECORD_PATH.pattern)
        for module in ("agentfiles", "roster", "skillset", "skillfiles", "structure", "snapshot_json", "teams",
                       "server", "flowcommands"):
            source = (ROOT / "agentgraph" / f"{module}.py").read_text()
            for pattern in (names.AGENT_PATTERN, names.SKILL_PATTERN, names.FLOW_ID_PATTERN, names.FLOW_NAME_PATTERN):
                with self.subTest(module=module, pattern=pattern):
                    self.assertNotIn(pattern, source)

    def test_each_writer_refuses_a_name_outside_its_kind(self):
        for bad in ("Impl", "impl.x", "-x", "", "a" * 65, "1abc"):
            with self.subTest(agent=bad), self.assertRaises(skillset.SkillsetError):
                skillset._agent_name(bad)
        for bad in ("Skill", "skill_x", "skill.x", "-x", "a" * 65):
            with self.subTest(skill=bad), self.assertRaises(skillset.SkillsetError):
                skillset._name(bad, "skill")
        skillset._agent_name("impl_openai-2")
        skillset._name("code-assurance", "skill")
        with self.assertRaises(flowcommands.CommandError):
            flowcommands.Folder._command("../x")
        flowcommands.Folder._command("check-loop")

    def test_the_real_roster_and_skills_fit(self):
        obj = skillset._parse(skillset._read(skillset.CONFIG, "config")[0])
        for preset in obj["presets"].values():
            for agent in preset:
                self.assertTrue(names.valid("agent", agent), agent)
        real = skillset.sources.CONFIG_DIR / "skills"
        if real.is_dir():
            for folder in real.iterdir():
                if (folder / "SKILL.md").is_file():
                    self.assertTrue(names.valid("skill", folder.name), folder.name)

    def test_the_plugin_and_the_terminal_panel_read_the_same_patterns(self):
        plugin = (ROOT / "desktop-plugin" / "src" / "plugin.cjs").read_text()
        for constant, pattern in names.PLUGIN_CONSTANTS.items():
            found = re.search(rf"^const {constant} = /(.*)/;", plugin, re.M)
            self.assertIsNotNone(found, constant)
            self.assertEqual(found.group(1), f"^{pattern}$", constant)
        for name in ("runner.js", "render.js"):
            panel = (ROOT / "tui-plugin" / "src" / name).read_text()
            found = re.search(r"^(?:export )?const FLOW_ID = /(.*)/;", panel, re.M)
            self.assertIsNotNone(found, name)
            self.assertEqual(found.group(1), f"^{names.FLOW_ID_PATTERN}$", name)

    def test_the_endpoints_refuse_through_the_same_definition(self):
        self.assertIs(server.structure.FLOW_NAME, names.FLOW_NAME)


if __name__ == "__main__":
    unittest.main()
