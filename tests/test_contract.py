import unittest
from pathlib import Path

from agentgraph import contract
from agentgraph.contract import (
    Node,
    Objective,
    Session,
    Snapshot,
    TaskGraph,
    objective_coverage,
    running_by_agent,
    validate,
    waves,
)
from agentgraph.demo import demo_snapshot
from tests.test_bounds import counted


def node(node_id, *, depends_on=(), scope=(), status="pending", done_when="tests pass"):
    return Node(
        id=node_id,
        type="implement",
        agent="impl-openai",
        goal="Implement " + node_id,
        depends_on=list(depends_on),
        scope=list(scope),
        status=status,
        done_when=done_when,
        evidence="all tests passed" if status == "passed" else "",
    )


def graph(nodes=(), objectives=()):
    return TaskGraph(
        path="graph.json",
        task="test",
        goal="Test graph helpers",
        status="running",
        created=0,
        updated=0,
        nodes=list(nodes),
        objectives=list(objectives),
    )


class AgentIdentity(unittest.TestCase):
    def test_six_groups_and_council_title(self):
        from agentgraph.contract import GROUPS, GROUP_TITLES

        self.assertEqual(GROUPS, ("hub", "understand", "decide", "council", "build", "verify"))
        self.assertEqual(GROUP_TITLES["council"], "Council")

    def test_group_of_current_agents(self):
        from agentgraph.contract import group_of

        expected = {"orchestrator": "hub", "explorer": "understand", "researcher": "understand",
                    "oracle": "decide", "oracle-lite": "decide",
                    "council": "council", "councillor-carbon": "council", "councillor-new": "council",
                    "impl-openai": "build", "impl-claude": "build", "impl-hard": "build",
                    "designer": "build", "verifier": "verify", "reviewer": "verify", "unknown": "build"}
        for name, group in expected.items():
            with self.subTest(name=name):
                self.assertEqual(group_of(name), group)
        # No name is special-cased by an old team prefix or a removed agent name.
        for name in ("", "councillor", "o2-reviewer", "o2-orchestrator", "orchestrator-2", "fixer", "hard-fixer"):
            with self.subTest(name=name):
                self.assertEqual(group_of(name), "build")

    def test_roster_rules_name_only_current_agents(self):
        from agentgraph.contract import AGENT_GROUPS, ESCALATION, POOL

        self.assertEqual(POOL, {"impl-openai": 3, "impl-claude": 2})
        self.assertEqual(ESCALATION, (("impl-openai", "impl-claude"), ("impl-claude", "impl-hard"),
                                      ("impl-hard", "oracle")))
        self.assertEqual(set(AGENT_GROUPS), {"orchestrator", "explorer", "researcher", "oracle", "oracle-lite",
                                             "council", "impl-openai", "impl-claude", "impl-hard", "designer",
                                             "verifier", "reviewer"})
        removed = ("fixer", "o2-", "orchestrator-2")
        names = [*AGENT_GROUPS, *POOL, *(name for route in ESCALATION for name in route)]
        self.assertFalse([name for name in names if any(word in name for word in removed)])

    def test_every_agent_is_team_main(self):
        from agentgraph.contract import team_of

        for name in ("orchestrator", "reviewer", "council", "councillor-carbon", "", "orchestrator-2",
                     "o2-reviewer", "impl-openai"):
            with self.subTest(name=name):
                self.assertEqual(team_of(name), "main")

    def test_parent_is_council_when_present(self):
        from agentgraph.contract import parent_of

        for name in ("councillor-carbon", "councillor-new"):
            with self.subTest(name=name):
                self.assertEqual(parent_of(name, {"council"}), "council")
                self.assertEqual(parent_of(name, set()), "")
        for name in ("council", "o2-councillor-carbon", "impl-openai", "councillor", "not-councillor-carbon"):
            with self.subTest(name=name):
                self.assertEqual(parent_of(name, {"council", "o2-council"}), "")


class Waves(unittest.TestCase):
    def test_linear_chain(self):
        first = node("first")
        second = node("second", depends_on=["first"])
        third = node("third", depends_on=["second"])
        self.assertEqual(waves(graph([third, second, first])), [[first], [second], [third]])

    def test_diamond(self):
        start = node("start")
        left = node("left", depends_on=["start"])
        right = node("right", depends_on=["start"])
        end = node("end", depends_on=["left", "right"])
        self.assertEqual(waves(graph([end, left, right, start])), [[start], [left, right], [end]])

    def test_unknown_dependency_is_ignored(self):
        first = node("first", depends_on=["missing"])
        second = node("second", depends_on=["first", "also-missing"])
        self.assertEqual(waves(graph([second, first])), [[first], [second]])

    def test_cycle_is_returned_together_in_final_wave(self):
        first = node("first")
        second = node("second", depends_on=["first"])
        left = node("left", depends_on=["right"])
        right = node("right", depends_on=["left"])
        self.assertEqual(
            waves(graph([left, second, right, first])),
            [[first], [second], [left, right]],
        )


class ObjectiveCoverage(unittest.TestCase):
    def test_no_objectives(self):
        self.assertEqual(objective_coverage(graph([node("n1", status="passed")])), (0, 0))

    def test_objective_without_nodes_is_not_delivered(self):
        self.assertEqual(objective_coverage(graph(objectives=[Objective("o1", "Empty")])), (0, 1))

    def test_every_named_node_must_have_passed(self):
        task = graph(
            [node("n1", status="passed"), node("n2", status="passed")],
            [Objective("o1", "Both nodes", ["n1", "n2"])],
        )
        self.assertEqual(objective_coverage(task), (1, 1))
        for status in ("pending", "running", "returned", "failed", "blocked"):
            with self.subTest(status=status):
                task.nodes[1].status = status
                self.assertEqual(objective_coverage(task), (0, 1))

    def test_unknown_named_node_is_not_delivered(self):
        task = graph(
            [node("n1", status="passed")],
            [Objective("o1", "Missing node", ["n1", "missing"])],
        )
        self.assertEqual(objective_coverage(task), (0, 1))

    def test_counts_only_fully_delivered_objectives(self):
        task = graph(
            [node("passed", status="passed"), node("returned", status="returned")],
            [
                Objective("o1", "Delivered", ["passed"]),
                Objective("o2", "Awaiting evaluation", ["returned"]),
                Objective("o3", "Partly delivered", ["passed", "returned"]),
                Objective("o4", "Empty"),
            ],
        )
        self.assertEqual(objective_coverage(task), (1, 4))


class Validate(unittest.TestCase):
    def test_large_raw_graph_has_bounded_validation_work_and_output(self):
        from agentgraph.sources import graph_from_raw

        raw = {"nodes": [{"id": f"n{i}", "scope": ["a"], "done_when": "tests pass"} for i in range(5000)]}

        def read_and_validate():
            task = graph_from_raw(raw, Path("test.json"))
            return task, validate(task)

        # Work counted in function calls, not timed (see tests/test_bounds.py): about 170 per node kept today;
        # comparing every pair of the 1000 writers would be 500,000 comparisons.
        (task, issues), calls = counted(read_and_validate)
        self.assertEqual(len(task.nodes), 1000)
        self.assertLess(calls, 250 * 1000)
        self.assertEqual(len(issues), 101)
        self.assertTrue(all("share write scope" in issue for issue in issues[:100]))
        self.assertIn("more parallel writers", issues[-1])

    def test_scope_conflict_cap_stops_scanning_but_preserves_cycle_detection(self):
        from unittest.mock import patch
        from agentgraph import contract

        task = graph([node(f"n{i}", scope=["a"]) for i in range(300)])
        task.nodes += [node("cycle-a", depends_on=["cycle-b"]), node("cycle-b", depends_on=["cycle-a"])]
        with patch.object(contract, "_contains", wraps=contract._contains) as contains:
            issues = validate(task)
        self.assertLessEqual(contains.call_count, 202)
        self.assertEqual(len([issue for issue in issues if "can run in parallel" in issue]), 100)
        self.assertIn("dependency cycle among cycle-a, cycle-b", issues)
        self.assertTrue(any("more parallel writers" in issue for issue in issues))

    def test_normal_scope_conflicts_remain_complete(self):
        workers = [node(f"n{i}", scope=["a"]) for i in range(14)]
        self.assertEqual(validate(graph(workers)), [
            f"n{i} and n{j} can run in parallel but share write scope"
            for i in range(14) for j in range(i + 1, 14)
        ])

    def test_feedback_issue_output_is_bounded(self):
        workers = [node(f"n{i}") for i in range(1000)]
        for worker in workers:
            worker.feedback_to = [worker.id]
        issues = validate(graph(workers))
        self.assertEqual(len(issues), 101)
        self.assertTrue(all("feedback to itself" in issue for issue in issues[:100]))
        self.assertIn("more feedback", issues[-1])

    def test_many_valid_upstream_feedback_edges_remain_valid(self):
        workers = [node(f"n{i}", depends_on=[f"n{i - 1}"] if i else []) for i in range(1000)]
        for worker in workers[1:]:
            worker.feedback_to = ["n0"]
        issues, calls = counted(validate, graph(workers))
        self.assertEqual(issues, [])
        self.assertLess(calls, 40 * 1000)  # about 26 per node today; walking every chain back would be 500,000

    def test_pattern_must_be_empty_or_known(self):
        from agentgraph.contract import PATTERNS

        self.assertEqual(PATTERNS, ("chain", "route", "parallel", "vote", "orchestrate", "evaluate", "synthesize"))
        worker = node("n1")
        for pattern in ("", *PATTERNS):
            worker.pattern = pattern
            self.assertEqual(validate(graph([worker])), [])
        worker.pattern = "unknown"
        self.assertEqual(validate(graph([worker])), ["n1 has unknown pattern 'unknown'"])

    def test_feedback_target_must_be_known_nonself_and_not_downstream(self):
        from agentgraph.contract import feedback_edges

        review = node("review")
        review.feedback_to = ["missing", "review", "next", "later"]
        task = graph([review, node("next", depends_on=["review"]), node("later", depends_on=["next"])])
        self.assertEqual(validate(task), [
            "review sends feedback to unknown node missing",
            "review sends feedback to itself",
            "review sends feedback to downstream node next",
            "review sends feedback to downstream node later",
        ])
        self.assertEqual(feedback_edges(task), [("review", "review"), ("review", "next"), ("review", "later")])

    def test_feedback_does_not_create_dependencies(self):
        work = node("work", status="passed")
        review = node("review", depends_on=["work"], status="running", scope=["src/"])
        other = node("other", scope=["src/"])
        task = graph([review, work, other])
        layers = waves(task)
        review.feedback_to = ["work", "other"]  # A dependency to work from review would make a cycle.
        self.assertEqual(waves(task), layers)
        self.assertEqual(validate(task), ["review and other can run in parallel but share write scope"])

    def test_demo_graph_is_valid(self):
        task = demo_snapshot(now=1000).graphs[0]
        self.assertEqual(validate(task), [])
        by_id = {n.id: n for n in task.nodes}
        self.assertIn("tests/limiter/", by_id["n4"].scope)
        self.assertEqual(by_id["n5"].status, "returned")
        self.assertEqual(by_id["n6"].status, "running")
        self.assertTrue(by_id["n6"].stalled)
        self.assertGreater(len(by_id["n6"].tiers), 1)
        self.assertEqual(by_id["n7"].status, "running")
        self.assertEqual((by_id["n9"].status, by_id["n9"].verdict), ("failed", "refuted"))
        for number in (8, 10, 11):
            self.assertEqual(by_id[f"n{number}"].status, "pending")

    def test_duplicate_node_id(self):
        self.assertEqual(validate(graph([node("n1"), node("n1")])), ["duplicate node id n1"])

    def test_unknown_dependency(self):
        self.assertEqual(
            validate(graph([node("n1", depends_on=["missing"])])),
            ["n1 depends on unknown node missing"],
        )

    def test_self_dependency(self):
        self.assertEqual(validate(graph([node("n1", depends_on=["n1"])])), ["n1 depends on itself"])

    def test_dependency_cycle(self):
        task = graph([node("n1", depends_on=["n2"]), node("n2", depends_on=["n1"])])
        self.assertEqual(validate(task), ["dependency cycle among n1, n2"])

    def test_empty_done_when(self):
        self.assertEqual(validate(graph([node("n1", done_when="")])), ["n1 has no done_when evaluation"])

    def test_blank_node_id(self):
        for value in ("", " \t"):
            with self.subTest(value=value):
                self.assertEqual(validate(graph([node(value)])), ["node has blank id"])

    def test_whitespace_only_done_when(self):
        self.assertEqual(validate(graph([node("n1", done_when=" \t")])), ["n1 has no done_when evaluation"])

    def test_objective_with_no_nodes(self):
        self.assertEqual(
            validate(graph([node("n1")], [Objective("o1", "Empty")])),
            ["objective o1 is not delivered by any node"],
        )

    def test_objective_with_unknown_node(self):
        self.assertEqual(
            validate(graph([node("n1")], [Objective("o1", "Missing", ["missing"])])),
            ["objective o1 names unknown node missing"],
        )

    def test_duplicate_objective_id(self):
        task = graph([node("n1")], [Objective("o1", "One", ["n1"]), Objective("o1", "Two", ["n1"])])
        self.assertEqual(validate(task), ["duplicate objective id o1"])

    def test_passed_graph_has_only_passed_nodes(self):
        task = graph([node("n1", status="returned")])
        task.status = "passed"
        self.assertIn("graph is passed but node n1 is returned", validate(task))
        task.nodes[0].status = "passed"
        task.nodes[0].evidence = "all tests passed"
        self.assertEqual(validate(task), [])

    def test_passed_graph_has_delivered_all_objectives(self):
        task = graph([node("n1", status="passed")], [Objective("o1", "Result", ["missing"])])
        task.status = "passed"
        self.assertIn("graph is passed but not all objectives are delivered", validate(task))
        task.objectives[0].nodes = ["n1"]
        self.assertEqual(validate(task), [])

    def test_passed_gate_requires_confirmed_verdict(self):
        gate = node("n1", status="passed")
        gate.type = "gate"
        task = graph([gate])
        for verdict in ("", "refuted", "unverifiable"):
            with self.subTest(verdict=verdict):
                gate.verdict = verdict
                self.assertIn("n1 is a passed gate without a confirmed verdict", validate(task))
        gate.verdict = "confirmed"
        self.assertEqual(validate(task), [])

    def test_passed_node_requires_evidence(self):
        worker = node("n1", status="passed")
        for evidence in ("", " \t"):
            with self.subTest(evidence=evidence):
                worker.evidence = evidence
                self.assertEqual(validate(graph([worker])), ["n1 passed with no evidence"])

    def test_running_or_returned_node_requires_passed_dependencies(self):
        first = node("n1", status="returned")
        second = node("n2", depends_on=["n1"])
        task = graph([second, first])
        self.assertEqual(validate(task), [])  # pending is allowed
        for status in ("running", "returned"):
            with self.subTest(status=status):
                second.status = status
                self.assertIn(f"n2 is {status} before dependency n1 has passed", validate(task))
        first.status = "passed"
        first.evidence = "all tests passed"
        self.assertEqual(validate(task), [])

    def test_changed_paths_must_be_within_scope(self):
        writer = node("n1", scope=[" src/./ ", " ../tests/ "])
        writer.changed_paths = [" ./src/a.py ", "../tests/test_a.py", "src/../src-extra/a.py"]
        self.assertEqual(validate(graph([writer])), ["n1 changed path 'src/../src-extra/a.py' outside its scope"])

    def test_no_scope_is_read_only(self):
        reader = node("n1")
        reader.changed_paths = ["src/a.py"]
        self.assertEqual(validate(graph([reader])), ["n1 changed path 'src/a.py' outside its scope"])

    def test_dot_scope_covers_only_project_relative_paths(self):
        writer = node("n1", scope=[" . "])
        writer.changed_paths = ["src/a.py", "../x/a.py", "src/../../x/b.py", "/src/a.py"]
        self.assertEqual(validate(graph([writer])), [
            "n1 changed path '../x/a.py' outside its scope",
            "n1 changed path 'src/../../x/b.py' outside its scope",
            "n1 changed path '/src/a.py' outside its scope",
        ])

    def test_parent_relative_scope_only_covers_its_own_subtree(self):
        writer = node("n1", scope=["../prompts/"])
        writer.changed_paths = ["../prompts/a.md", "../other/a.md"]
        self.assertEqual(validate(graph([writer])), ["n1 changed path '../other/a.md' outside its scope"])

    def test_blank_scope_entry_is_an_issue_and_does_not_grant_access(self):
        for value in ("", " \t"):
            with self.subTest(value=value):
                writer = node("n1", scope=[value])
                writer.changed_paths = ["src/a.py"]
                task = graph([writer, node("n2", scope=["src/a.py"])])
                self.assertEqual(validate(task), [
                    "n1 has a blank scope entry", "n1 changed path 'src/a.py' outside its scope",
                ])

    def test_project_absolute_and_relative_write_scopes_collide(self):
        task = graph([node("n1", scope=["src/a.py"]), node("n2", scope=[" /project/src/./a.py "])])
        task.path = "/project/.opencode/graph/test.json"
        self.assertEqual(validate(task), ["n1 and n2 can run in parallel but share write scope"])
        for path in ("", "/project/archive/test.json"):
            with self.subTest(path=path):
                task.path = path
                self.assertEqual(validate(task), [])

    def test_project_absolute_and_relative_paths_are_interchangeable_for_audit(self):
        task_path = "/project/.opencode/graph/test.json"
        for scope, changed in (("src/", "/project/src/a.py"), ("/project/src/", "src/a.py"),
                               (".", "/project/src/a.py"), ("/project/", "src/a.py"),
                               ("/project-other/src/", "/project-other/src/a.py")):
            with self.subTest(scope=scope, changed=changed):
                writer = node("n1", scope=[scope])
                writer.changed_paths = [changed]
                task = graph([writer])
                task.path = task_path
                self.assertEqual(validate(task), [])

    def test_project_sibling_absolute_paths_stay_outside_project(self):
        writer = node("n1", scope=["."])
        writer.changed_paths = ["/project-other/src/a.py", "/project/../project-other/src/b.py"]
        task = graph([writer])
        task.path = "/project/.opencode/graph/test.json"
        self.assertEqual(validate(task), [f"n1 changed path {path!r} outside its scope" for path in writer.changed_paths])

    def test_relative_graph_path_provides_project_root(self):
        writer = node("n1", scope=["src/"])
        writer.changed_paths = [str(Path.cwd() / "project/src/a.py")]
        task = graph([writer])
        task.path = "project/.opencode/graph/test.json"
        self.assertEqual(validate(task), [])

    def test_parent_scope_is_anchored_at_the_project_parent(self):
        writer = node("n1", scope=[".."])
        writer.changed_paths = ["../../outside/a", "../sibling/a"]
        task = graph([writer])
        task.path = "/work/p/.opencode/graph/test.json"
        self.assertEqual(validate(task), ["n1 changed path '../../outside/a' outside its scope"])

    def test_parent_relative_scopes_compare_with_external_absolute_scopes(self):
        for path, expected in (("/work/shared/a", ["n1 and n2 can run in parallel but share write scope"]),
                               ("/other/shared/a", [])):
            with self.subTest(path=path):
                task = graph([node("n1", scope=["../shared/a"]), node("n2", scope=[path])])
                task.path = "/work/p/.opencode/graph/test.json"
                self.assertEqual(validate(task), expected)

    def test_absolute_scope_under_project_covers_relative_change(self):
        writer = node("n1", scope=["/work/p/src"])
        writer.changed_paths = ["src/x.py"]
        task = graph([writer])
        task.path = "/work/p/.opencode/graph/test.json"
        self.assertEqual(validate(task), [])

    def test_synthetic_root_distinguishes_multiple_parent_levels(self):
        writer = node("n1", scope=[".."])
        writer.changed_paths = ["../sibling/a", "../../outside/a"]
        self.assertEqual(validate(graph([writer])), ["n1 changed path '../../outside/a' outside its scope"])

    def test_parallel_writers_to_same_path(self):
        task = graph([node("n1", scope=["src/a.py"]), node("n2", scope=["src/a.py"])])
        self.assertEqual(validate(task), ["n1 and n2 can run in parallel but share write scope"])

    def test_parallel_writers_to_directory_and_contained_file(self):
        for first_scope, second_scope in (("src/", "src/a.py"), ("src/a.py", "src/")):
            with self.subTest(first=first_scope, second=second_scope):
                task = graph([node("n1", scope=[first_scope]), node("n2", scope=[second_scope])])
                self.assertEqual(validate(task), ["n1 and n2 can run in parallel but share write scope"])

    def test_parallel_scopes_compared_after_posix_normalization(self):
        for left, right in (("src/a.py", " ./src/a.py "), (".", "src/a.py"),
                            ("../src/", "../src/other/../a.py"), ("/", "/src/a.py")):
            with self.subTest(left=left, right=right):
                task = graph([node("n1", scope=[left]), node("n2", scope=[right])])
                self.assertEqual(validate(task), ["n1 and n2 can run in parallel but share write scope"])

    def test_relative_scope_does_not_overlap_absolute_scope(self):
        task = graph([node("n1", scope=["."]), node("n2", scope=["/src/a.py"])])
        self.assertEqual(validate(task), [])

    def test_dot_scope_does_not_overlap_parent_relative_scope(self):
        task = graph([node("n1", scope=["."]), node("n2", scope=["../x/a.py"])])
        self.assertEqual(validate(task), [])

    def test_overlapping_scopes_with_direct_dependency_are_allowed(self):
        task = graph([
            node("n1", scope=["src/"]),
            node("n2", depends_on=["n1"], scope=["src/a.py"]),
        ])
        self.assertEqual(validate(task), [])

    def test_overlapping_scopes_with_transitive_dependency_are_allowed(self):
        task = graph([
            node("n3", depends_on=["n2"], scope=["src/a.py"]),
            node("n2", depends_on=["n1"]),
            node("n1", scope=["src/"]),
        ])
        self.assertEqual(validate(task), [])

    def test_deep_chain_with_writers(self):
        chain = [node("n0", scope=["src/"])]
        chain += [node(f"n{i}", depends_on=[f"n{i - 1}"]) for i in range(1, 3000)]
        chain[-1].scope = ["src/a.py"]
        task = graph(chain)
        issues, calls = counted(validate, task)
        self.assertEqual(issues, [])
        self.assertLess(calls, 30 * 3000)  # about 18 per node today; every pair of the chain would be 4.5 million
        task.nodes.append(node("independent", scope=["src/a.py"]))
        self.assertEqual(validate(task), [
            "n0 and independent can run in parallel but share write scope",
            "n2999 and independent can run in parallel but share write scope",
        ])

    def test_shared_filename_prefix_is_not_an_overlap(self):
        task = graph([node("n1", scope=["src/a.py"]), node("n2", scope=["src/ab.py"])])
        self.assertEqual(validate(task), [])


class ArtifactHandoffs(unittest.TestCase):
    def setUp(self):
        from agentgraph.contract import Artifact, ArtifactInput

        self.Artifact = Artifact
        self.Input = ArtifactInput
        self.producer = node("producer", status="passed")
        self.producer.agent = "explorer"
        self.producer.artifacts = [Artifact("report", ".opencode/artifacts/test/producer/r2/report.md", 2)]
        self.consumer = node("consumer", depends_on=["producer"])
        self.consumer.inputs = [ArtifactInput("producer", "report", 2)]
        self.task = graph([self.producer, self.consumer])

    def test_defaults_are_independent_empty_lists(self):
        first, second = node("first"), node("second")
        self.assertEqual((first.artifacts, first.inputs), ([], []))
        first.artifacts.append(self.producer.artifacts[0])
        first.inputs.append(self.consumer.inputs[0])
        self.assertEqual((second.artifacts, second.inputs), ([], []))

    def test_artifact_strings_must_be_nonblank(self):
        for field in ("id", "path"):
            for value in ("", " \t", None, 42):
                with self.subTest(field=field, value=value):
                    artifact = self.Artifact("report", ".opencode/artifacts/report.md", 2)
                    setattr(artifact, field, value)
                    self.producer.artifacts = [artifact]
                    self.consumer.inputs = []
                    self.assertTrue(any(field in issue for issue in validate(self.task)))

    def test_revisions_are_positive_integers_not_booleans(self):
        for record in (self.producer.artifacts[0], self.consumer.inputs[0]):
            for value in (None, True, False, 0, -1, 1.5, "2"):
                with self.subTest(record=type(record).__name__, value=value):
                    record.revision = value
                    self.assertTrue(any("revision" in issue for issue in validate(self.task)))
            record.revision = 2
        self.assertEqual(validate(self.task), [])

    def test_duplicate_artifact_ids_are_per_node(self):
        self.consumer.artifacts = [self.Artifact("report", ".opencode/artifacts/other.md", 1)]
        self.assertEqual(validate(self.task), [])
        self.producer.artifacts.append(self.Artifact("report", ".opencode/artifacts/new.md", 3))
        self.assertIn("producer has duplicate artifact id report", validate(self.task))

    def test_writer_artifact_must_be_in_scope(self):
        self.producer.scope = ["src/"]
        self.assertIn("producer artifact 'report' path '.opencode/artifacts/test/producer/r2/report.md' outside its scope",
                      validate(self.task))
        self.producer.scope.append(".opencode/artifacts/test/producer/")
        self.assertEqual(validate(self.task), [])

    def test_read_only_artifact_must_be_under_artifact_directory(self):
        for path in ("reports/a.md", ".opencode/artifacts-other/a.md", ".opencode/artifacts/../../outside.md",
                     "../.opencode/artifacts/a.md", "/other/.opencode/artifacts/a.md"):
            with self.subTest(path=path):
                self.producer.artifacts[0].path = path
                self.assertTrue(any("outside .opencode/artifacts/" in issue for issue in validate(self.task)))

    def test_artifact_paths_use_project_root_and_normalization(self):
        self.task.path = "/project/.opencode/graph/test.json"
        for scope, path in (([], "/project/.opencode/artifacts/report.md"),
                            (["/project/reports/"], "reports/./report.md"),
                            (["reports/"], "/project/reports/a/../report.md"),
                            (["../shared/"], "/shared/report.md")):
            with self.subTest(scope=scope, path=path):
                self.producer.scope = scope
                self.producer.artifacts[0].path = path
                self.assertEqual(validate(self.task), [])

    def test_input_strings_must_be_nonblank(self):
        for field in ("from_node", "artifact"):
            for value in ("", " \t", None, 42):
                with self.subTest(field=field, value=value):
                    self.consumer.inputs = [self.Input("producer", "report", 2)]
                    setattr(self.consumer.inputs[0], field, value)
                    self.assertTrue(validate(self.task))

    def test_input_producer_must_be_a_direct_dependency(self):
        message = "consumer input from producer is not in depends_on and does not send feedback to it"
        self.consumer.depends_on = []
        self.assertIn(message, validate(self.task))
        self.consumer.depends_on = ["middle"]
        self.task.nodes.append(node("middle", depends_on=["producer"]))
        self.assertIn(message, validate(self.task))
        # Feedback to some other node does not license this input.
        self.producer.feedback_to = ["middle"]
        self.assertIn(message, validate(self.task))

    def rework(self):
        """A gate that refuted the consumer and handed its findings back."""
        gate = node("gate", depends_on=["consumer"], status="failed")
        gate.type = "gate"
        gate.agent = "reviewer"
        gate.verdict = "refuted"
        gate.feedback_to = ["consumer"]
        gate.artifacts = [self.Artifact("findings", ".opencode/artifacts/test/gate/r1/findings.md", 1)]
        self.task.nodes.append(gate)
        self.consumer.inputs.append(self.Input("gate", "findings", 1))
        return gate

    def test_input_may_come_from_a_node_that_sends_feedback_to_it(self):
        gate = self.rework()
        self.consumer.status = "running"
        self.assertEqual(validate(self.task), [])
        gate.artifacts = []
        self.assertIn("consumer input references unknown artifact 'findings' on gate", validate(self.task))

    def test_feedback_input_staleness_is_detected(self):
        from agentgraph.contract import stale_inputs

        gate = self.rework()
        gate.artifacts[0].revision = 2
        self.consumer.status = "running"
        self.assertEqual(stale_inputs(self.task), [("consumer", "gate", "findings", 1, 2)])
        self.assertEqual(validate(self.task), [
            "consumer is running with stale input 'findings' from gate: read revision 1, current revision 2"])

    def test_input_must_reference_declared_artifact(self):
        self.consumer.inputs[0].artifact = "missing"
        self.assertIn("consumer input references unknown artifact 'missing' on producer", validate(self.task))
        self.consumer.inputs[0].from_node = "missing"
        self.assertTrue(validate(self.task))

    def test_input_cannot_read_a_future_revision(self):
        self.consumer.inputs[0].revision = 3
        self.assertIn("consumer input 'report' from producer reads revision 3 above current revision 2",
                      validate(self.task))

    def test_stale_input_blocks_running_returned_and_passed_only(self):
        self.consumer.inputs[0].revision = 1
        for status in ("pending", "failed", "blocked", "running", "returned", "passed"):
            with self.subTest(status=status):
                self.consumer.status = status
                self.consumer.evidence = "checked"
                issues = validate(self.task)
                if status in ("running", "returned", "passed"):
                    self.assertEqual(issues, [f"consumer is {status} with stale input 'report' from producer: read revision 1, current revision 2"])
                else:
                    self.assertEqual(issues, [])

    def test_stale_inputs_reports_exact_revisions_without_mutation(self):
        from agentgraph.contract import stale_inputs

        self.consumer.inputs = [self.Input("producer", "report", 1), self.Input("producer", "missing", 1),
                                self.Input("unknown", "report", 1), self.Input("producer", "report", 3)]
        self.assertEqual(stale_inputs(self.task), [("consumer", "producer", "report", 1, 2)])
        self.assertEqual(self.consumer.inputs[0].revision, 1)
        self.assertEqual(stale_inputs(graph()), [])

    def test_node_busy_needs_running_status_and_busy_session(self):
        from agentgraph.contract import node_busy

        self.consumer.session = "ses_consumer"
        for status in ("pending", "running", "returned", "passed", "failed", "blocked"):
            with self.subTest(status=status):
                self.consumer.status = status
                self.assertEqual(node_busy(self.consumer, {"ses_consumer"}), status == "running")
                self.assertFalse(node_busy(self.consumer, {"ses_other"}))
        self.consumer.session = None
        self.assertFalse(node_busy(self.consumer, {None}))

    def test_flows_group_pairs_artifacts_and_exclude_same_agent_and_user(self):
        from agentgraph.contract import agent_flows

        second = node("second", depends_on=["producer", "missing"], status="returned")
        second.inputs = [self.Input("producer", "report", 2)]
        same = node("same", depends_on=["consumer"])
        human = node("human", depends_on=["consumer"])
        human.agent = "user"
        self.producer.feedback_to = ["consumer", "human", "missing"]
        self.consumer.feedback_to = ["producer", "same"]
        self.consumer.inputs.append(self.Input("producer", "another", 1))
        self.task.nodes += [second, same, human]
        # Nothing is busy and no evaluator refuted or failed, so nothing is active;
        # "returned" (second) never counts. A feedback flow carries the artifacts
        # its target reads from the feedback source.
        expected = [
            {"from": "explorer", "to": "impl-openai", "kind": "data",
             "pairs": [["producer", "consumer"], ["producer", "second"]],
             "artifacts": ["another", "report"], "active": False},
            {"from": "explorer", "to": "impl-openai", "kind": "feedback", "pairs": [["producer", "consumer"]],
             "artifacts": ["another", "report"], "active": False},
            {"from": "impl-openai", "to": "explorer", "kind": "feedback", "pairs": [["consumer", "producer"]],
             "artifacts": [], "active": False},
        ]
        self.assertEqual(agent_flows(self.task), expected)
        self.task.nodes.reverse()
        self.assertEqual(agent_flows(self.task), expected)
        self.assertEqual(agent_flows(graph()), [])

    def test_data_flow_is_active_only_while_a_consumer_session_is_busy(self):
        from agentgraph.contract import agent_flows

        self.consumer.session = "ses_consumer"
        for status in ("pending", "running", "returned", "passed", "failed", "blocked"):
            with self.subTest(status=status):
                self.consumer.status = status
                # Status alone never makes a data flow active, not even running or returned.
                self.assertFalse(agent_flows(self.task)[0]["active"])
                # A busy session counts only for a running consumer: a session is
                # often reused, so a passed node on a busy session is not busy.
                self.assertEqual(agent_flows(self.task, busy_ids={"ses_consumer"})[0]["active"],
                                 status == "running")
                self.assertFalse(agent_flows(self.task, busy_ids={"ses_other"})[0]["active"])
        # The producer's own busy session does not make its outgoing flow active.
        self.producer.session = "ses_producer"
        self.consumer.session = None
        self.assertFalse(agent_flows(self.task, busy_ids={"ses_producer"})[0]["active"])

    def test_feedback_flow_is_active_only_after_refute_or_failure_with_target_running(self):
        from agentgraph.contract import agent_flows

        gate = self.rework()  # failed, refuted, feedback to consumer
        gate.status = "passed"
        gate.verdict = ""

        def feedback():
            flows = [flow for flow in agent_flows(self.task) if flow["kind"] == "feedback"]
            self.assertEqual(len(flows), 1)
            self.assertEqual((flows[0]["from"], flows[0]["to"], flows[0]["artifacts"]),
                             ("reviewer", "impl-openai", ["findings"]))
            return flows[0]["active"]

        for target in ("pending", "running", "returned", "passed", "failed", "blocked"):
            self.consumer.status = target
            for status, verdict in (("passed", ""), ("passed", "confirmed"), ("running", "unverifiable"),
                                    ("failed", ""), ("failed", "refuted"), ("returned", "refuted"),
                                    ("returned", "")):
                with self.subTest(target=target, status=status, verdict=verdict):
                    gate.status, gate.verdict = status, verdict
                    self.assertEqual(feedback(), target == "running" and (status == "failed" or verdict == "refuted"))


class AgentLoops(unittest.TestCase):
    def setUp(self):
        self.worker = node("work", status="returned")
        self.worker.session = "ses_work"
        self.evaluator = Node("review", "gate", "reviewer", "Review work",
                              session="ses_review", feedback_to=["work"])
        self.task = graph([self.worker, self.evaluator])

    def assertLoop(self, state, active, busy_ids=frozenset()):
        self.assertEqual(contract.agent_loops(self.task, busy_ids), [
            {"id": "impl-openai~reviewer", "worker": "impl-openai", "evaluator": "reviewer",
             "pairs": [["work", "review"]], "rounds": 1, "state": state, "active": active,
             "artifacts": []},
        ])

    def test_working(self):
        # A busy session on a worker that is not running (here: returned) is a
        # reused session, not work in this loop.
        self.assertLoop("idle", False, {"ses_work"})
        self.worker.status = "running"
        self.assertLoop("working", True)
        self.assertLoop("working", True, {"ses_work"})

    def test_checking(self):
        self.assertLoop("idle", False, {"ses_review"})
        self.evaluator.status = "running"
        self.assertLoop("checking", True)
        self.assertLoop("checking", True, {"ses_review"})

    def test_sent_back(self):
        self.evaluator.status = "failed"
        self.assertLoop("sent_back", True)
        self.evaluator.status = "returned"
        self.evaluator.verdict = "refuted"
        self.assertLoop("sent_back", True)

    def test_closed(self):
        self.evaluator.status = "passed"
        self.assertLoop("closed", False)

    def test_idle(self):
        self.assertLoop("idle", False, {"ses_other"})
        self.evaluator.status = "returned"
        self.assertLoop("idle", False)

    def test_state_precedence(self):
        self.evaluator.status = "passed"
        self.evaluator.verdict = "refuted"
        # Busy sessions on nodes that are not running change nothing.
        self.assertLoop("sent_back", True, {"ses_work", "ses_review"})
        self.worker.status = "running"
        self.evaluator.status = "running"
        self.assertLoop("working", True, {"ses_work", "ses_review"})
        self.worker.status = "returned"
        self.assertLoop("checking", True, {"ses_review"})
        self.evaluator.status = "passed"
        self.assertLoop("sent_back", True)
        self.evaluator.verdict = "confirmed"
        self.assertLoop("closed", False)

    def test_state_considers_all_paired_nodes(self):
        self.evaluator.status = "passed"
        worker = node("another-work", status="running")
        evaluator = Node("another-review", "gate", "reviewer", "Review another task",
                         status="failed", feedback_to=["another-work"])
        self.task.nodes += [worker, evaluator]

        def expect(state, active):
            self.assertEqual(contract.agent_loops(self.task), [
                {"id": "impl-openai~reviewer", "worker": "impl-openai", "evaluator": "reviewer",
                 "pairs": [["another-work", "another-review"], ["work", "review"]],
                 "rounds": 1, "state": state, "active": active, "artifacts": []},
            ])

        expect("working", True)
        worker.status = "passed"
        expect("sent_back", True)
        evaluator.status = "pending"
        expect("idle", False)
        evaluator.status = "passed"
        expect("closed", False)

    def test_grouped_rounds_artifacts_and_sorting(self):
        self.worker.id = "work-z"
        self.worker.attempts = 4
        self.evaluator.feedback_to = ["work-z", "work-a", "work-z"]
        self.evaluator.attempts = 30
        earlier = node("work-a")
        earlier.attempts = 2
        unpaired = node("unpaired", status="running")
        unpaired.attempts = 40
        second = Node("a-review", "gate", "reviewer", "Another review", feedback_to=["work-a"])
        other_agent = Node("other-work", "implement", "impl-openai-next", "Other work")
        self.evaluator.feedback_to.append("other-work")
        self.worker.inputs = [contract.ArtifactInput("review", "z-notes", 1),
                              contract.ArtifactInput("a-review", "not-for-this-pair", 1)]
        earlier.inputs = [contract.ArtifactInput("review", "z-notes", 1),
                          contract.ArtifactInput("a-review", "a-notes", 1)]
        self.evaluator.inputs = [contract.ArtifactInput("work-z", "forward-handoff", 1)]
        self.evaluator.artifacts = [contract.Artifact("not-read", "not-read.md", 1)]
        self.task.nodes += [earlier, unpaired, second, other_agent]
        expected = [
            {"id": "impl-openai-next~reviewer", "worker": "impl-openai-next", "evaluator": "reviewer",
             "pairs": [["other-work", "review"]], "rounds": 1, "state": "idle", "active": False,
             "artifacts": []},
            {"id": "impl-openai~reviewer", "worker": "impl-openai", "evaluator": "reviewer",
             "pairs": [["work-a", "a-review"], ["work-a", "review"], ["work-z", "review"]],
             "rounds": 4, "state": "idle", "active": False, "artifacts": ["a-notes", "z-notes"]},
        ]
        self.assertEqual(contract.agent_loops(self.task), expected)
        self.task.nodes.reverse()
        self.evaluator.feedback_to.reverse()
        self.assertEqual(contract.agent_loops(self.task), expected)

    def test_ignores_user_same_agent_unknown_targets_and_dependency_only_edges(self):
        same_agent = Node("peer", "check", "reviewer", "Peer check")
        human = Node("human", "human", "user", "Approve", feedback_to=["work"])
        self.evaluator.feedback_to = ["human", "peer", "missing"]
        self.evaluator.depends_on = ["work"]
        self.task.nodes += [same_agent, human]
        self.assertEqual(contract.agent_loops(self.task), [])
        self.assertEqual(contract.agent_loops(graph()), [])


class AgentWorkLoops(unittest.TestCase):
    def setUp(self):
        self.first = node("first", status="passed")
        self.first.attempts = 2
        self.second = node("second", status="pending")
        self.second.session = "ses_second"
        self.task = graph([self.second, self.first])

    def loops(self, busy_ids=frozenset()):
        return contract.agent_work_loops(self.task, busy_ids)

    def expect(self, state, rounds=2, busy_ids=frozenset()):
        self.assertEqual(self.loops(busy_ids), [
            {"agent": "impl-openai", "nodes": ["first", "second"], "rounds": rounds, "state": state,
             "active": state == "working"},
        ])

    def test_each_state(self):
        self.expect("idle")
        self.second.status = "passed"
        self.expect("closed")
        for status in ("failed", "blocked"):
            with self.subTest(status=status):
                self.second.status = status
                self.expect("stuck")
        self.second.status = "returned"
        self.expect("waiting")
        self.second.status = "running"
        self.expect("working")
        self.expect("working", busy_ids={"ses_second"})

    def test_busy_session_counts_only_on_a_running_node(self):
        for status in ("pending", "returned", "passed", "failed", "blocked"):
            with self.subTest(status=status):
                self.second.status = status
                self.assertNotEqual(self.loops({"ses_second"})[0]["state"], "working")

    def test_precedence(self):
        third = node("third", status="failed")
        fourth = node("fourth", status="returned")
        self.task.nodes += [third, fourth]
        self.second.status = "running"
        self.assertEqual(self.loops()[0]["state"], "working")
        self.second.status = "pending"
        self.assertEqual(self.loops()[0]["state"], "waiting")
        fourth.status = "passed"
        self.assertEqual(self.loops()[0]["state"], "stuck")
        third.status = "pending"
        self.assertEqual(self.loops()[0]["state"], "idle")  # one pending node keeps it from closed
        third.status = self.second.status = "passed"
        self.assertEqual(self.loops()[0]["state"], "closed")

    def test_rounds_is_the_largest_attempts_or_zero(self):
        self.second.attempts = 7
        self.expect("idle", rounds=7)
        self.first.attempts = self.second.attempts = 0
        self.expect("idle", rounds=0)

    def test_only_implement_and_design_nodes_of_real_agents_count(self):
        design = node("a-design", status="running")
        design.type = "design"
        design.agent = "designer"
        human = node("approve", status="running")
        human.type, human.agent = "human", "user"
        user_work = node("user-work", status="running")
        user_work.agent = "user"
        unnamed = node("unnamed", status="running")
        unnamed.agent = ""
        others = []
        for kind in ("research", "decision", "check", "gate", "join"):
            other = node(f"other-{kind}", status="running")
            other.type, other.agent = kind, "reviewer" if kind == "gate" else "impl-openai"
            others.append(other)
        self.task.nodes += [design, human, user_work, unnamed, *others]
        self.assertEqual(self.loops(), [
            {"agent": "designer", "nodes": ["a-design"], "rounds": 0, "state": "working", "active": True},
            {"agent": "impl-openai", "nodes": ["first", "second"], "rounds": 2, "state": "idle", "active": False},
        ])

    def test_sorted_by_agent_and_empty_graph(self):
        for name in ("zeta", "alpha", "impl-hard"):
            extra = node(f"{name}-work", status="passed")
            extra.agent = name
            self.task.nodes.insert(0, extra)
        self.assertEqual([loop["agent"] for loop in self.loops()], ["alpha", "impl-hard", "impl-openai", "zeta"])
        self.task.nodes.reverse()
        self.assertEqual([loop["agent"] for loop in self.loops()], ["alpha", "impl-hard", "impl-openai", "zeta"])
        self.assertEqual(contract.agent_work_loops(graph()), [])


class ShapeAdvice(unittest.TestCase):
    def test_single_chain_advice(self):
        from agentgraph.contract import shape_advice

        nodes = [node(f"n{i}", depends_on=[f"n{i - 1}"] if i else []) for i in range(4)]
        for worker in nodes:
            worker.type = "check"
        self.assertEqual(shape_advice(graph(nodes[:3])), [])
        self.assertEqual(shape_advice(graph(nodes)), [
            "Nodes n0, n1, n2, n3 form a single chain; what could run at the same time?",
        ])

    def test_retried_completed_gate_needs_feedback_advice(self):
        from agentgraph.contract import shape_advice

        gate = node("review", depends_on=["work"])
        gate.type = "gate"
        task = graph([node("work"), gate])
        for status in ("passed", "failed", "running"):
            gate.status = status
            for attempts in (1, 2):
                gate.attempts = attempts
                expected = ["Gate review used multiple attempts; record its rework as feedback edges to the nodes it sent back."]
                self.assertEqual(shape_advice(task), expected if attempts == 2 and status != "running" else [])
        gate.status = "passed"
        gate.feedback_to = ["work"]
        self.assertEqual(shape_advice(task), [])

    def test_repeated_write_scope_chain_advice_ignores_other_dependencies(self):
        from agentgraph.contract import shape_advice

        chain = [node("n1", scope=["src/", "tests/"]),
                 node("n2", depends_on=["n1", "other"], scope=["tests/", "src/"]),
                 node("n3", depends_on=["n2"], scope=["src/", "tests/"])]
        chain[1].type = "design"
        task = graph([chain[2], chain[0], node("other"), chain[1]])
        self.assertEqual(shape_advice(task), [
            "Nodes n1, n2, n3 look like fix rounds of one node and belong on that node as attempts.",
        ])
        chain[2].scope = ["src/"]
        self.assertEqual(shape_advice(task), [])

    def test_empty_and_demo_have_no_advice(self):
        from agentgraph.contract import shape_advice

        self.assertEqual(shape_advice(graph()), [])
        self.assertEqual(shape_advice(demo_snapshot(1000).graphs[0]), [])


def session(session_id, agent, busy, parent_id="root"):
    return Session(
        id=session_id,
        parent_id=parent_id,
        agent=agent,
        title=session_id,
        model="test-model",
        effort="high",
        busy=busy,
        created=0,
        updated=0,
    )


class RunningByAgent(unittest.TestCase):
    def test_counts_busy_sessions_per_agent_and_includes_busy_root(self):
        snapshot = Snapshot(
            now=0,
            root=session("root", "orchestrator", True, parent_id=None),
            sessions=[
                session("impl-1", "impl-openai", True),
                session("impl-2", "impl-openai", True),
                session("impl-idle", "impl-openai", False),
                session("reviewer-1", "reviewer", True),
                session("explorer-idle", "explorer", False),
                session("child-orchestrator", "orchestrator", True),
            ],
        )
        self.assertEqual(running_by_agent(snapshot), {"impl-openai": 2, "reviewer": 1, "orchestrator": 2})

    def test_idle_root_and_missing_root_are_not_counted(self):
        snapshot = Snapshot(now=0, root=session("root", "orchestrator", False, parent_id=None))
        self.assertEqual(running_by_agent(snapshot), {})
        snapshot.root = None
        self.assertEqual(running_by_agent(snapshot), {})


class BusyAndLivenessAgree(unittest.TestCase):
    """A node's session may be anywhere in the database (a loop run's sessions are not the orchestrator's children): `busy` and
    `liveness` in the snapshot are then one answer."""

    def session(self, session_id, busy, parent=None):
        return Session(id=session_id, parent_id=parent, agent="impl-openai", title="t", model="m", effort="high", busy=busy,
                       created=1, updated=2)

    def document(self, listed, known, status="running", session_id="ses_loop"):
        from agentgraph import snapshot_json

        owner = self.session("ses_root", False)
        work = node("n1", status=status)
        work.session = session_id
        snap = Snapshot(now=10, root=owner, sessions=listed, graphs=[graph([work])], known=known)
        return snapshot_json.snapshot_document(snap, "live")["graph"]["nodes"][0]

    def test_a_busy_session_outside_the_root_tree_is_live_and_busy(self):
        loop = self.session("ses_loop", True)
        item = self.document([], {"ses_loop": loop, "ses_root": self.session("ses_root", False)})
        self.assertEqual(item["liveness"], "live")
        self.assertTrue(item["busy"])

    def test_they_agree_for_every_status_and_session_state(self):
        for status in ("pending", "running", "returned", "passed", "failed", "blocked"):
            for busy in (True, False):
                for listed in (True, False):
                    with self.subTest(status=status, busy=busy, listed=listed):
                        loop = self.session("ses_loop", busy, "ses_root" if listed else None)
                        known = {"ses_loop": loop, "ses_root": self.session("ses_root", False)}
                        item = self.document([loop] if listed else [], known, status)
                        self.assertEqual(item["busy"], item["liveness"] == "live")


class EndToEndFields(unittest.TestCase):
    def test_e2e_and_review_copy_are_node_run_fields_that_default_to_empty(self):
        item = Node(id="n", type="implement", agent="impl", goal="g")
        self.assertEqual((item.e2e, item.review_copy), ("", None))
        self.assertIn("e2e", contract.RUN_FIELDS)
        self.assertIn("review_copy", contract.RUN_FIELDS)


class RunControllerLiveness(unittest.TestCase):
    """A node whose loop run has a live controller is live, whatever its session says; nothing changes for a node without a run."""

    def node(self, **kw):
        base = dict(id="n", type="implement", agent="impl", goal="g", status="running", session=None, loop_run="20260101-000000-abcd")
        return Node(**{**base, **kw})

    def test_a_controlled_run_makes_the_node_live_and_busy(self):
        item = self.node()
        controlled = {"20260101-000000-abcd"}
        self.assertEqual(contract.node_liveness(item, {}, controlled=controlled), ("live", None))
        self.assertTrue(contract.node_busy(item, set(), controlled))
        self.assertEqual(contract.graph_liveness(graph([item]), {}, controlled)["n"], ("live", None))

    def test_without_a_controller_the_old_answer_stands(self):
        item = self.node()
        self.assertEqual(contract.node_liveness(item, {}, controlled=set()), ("orphan", None))
        self.assertFalse(contract.node_busy(item, set(), set()))

    def test_a_node_without_a_run_is_not_affected(self):
        item = self.node(loop_run=None)
        self.assertEqual(contract.node_liveness(item, {}, controlled={"20260101-000000-abcd"}), ("orphan", None))
        self.assertFalse(contract.node_busy(item, set(), {"20260101-000000-abcd"}))

    def test_a_node_that_is_not_running_is_not_live_for_its_run(self):
        item = self.node(status="blocked")
        self.assertFalse(contract.node_busy(item, set(), {"20260101-000000-abcd"}))
        self.assertNotEqual(contract.node_liveness(item, {}, controlled={"20260101-000000-abcd"})[0], "live")

    def test_limit_is_a_failure_kind(self):
        self.assertIn("limit", contract.FAILURE_KINDS)


if __name__ == "__main__":
    unittest.main()


class NodeLiveness(unittest.TestCase):
    """What the database confirms about a node; the graph's own status is only a report."""

    @staticmethod
    def sessions(**states):
        return {sid: Session(sid, None, "impl", "t", "m", "", busy, 1.0, 50.0, idle_since=idle)
                for sid, (busy, idle) in states.items()}

    def liveness(self, status, session, known):
        node = Node(id="n", type="implement", agent="impl", goal="g", status=status, session=session)
        return contract.graph_liveness(graph([node]), known)

    def test_running_node_is_live_quiet_or_orphan(self):
        known = self.sessions(busy=(True, None), idle=(False, 40.0), never=(False, None))
        for session, expected in (("busy", ("live", None)), ("idle", ("quiet", 40.0)), ("never", ("quiet", 50.0)),
                                  ("gone", ("orphan", None)), (None, ("orphan", None))):
            with self.subTest(session=session):
                self.assertEqual(self.liveness("running", session, known)["n"], expected)

    def test_busy_session_of_a_node_that_is_not_running_is_unreported(self):
        known = self.sessions(busy=(True, None), idle=(False, 40.0))
        for status, expected in (("pending", "unreported"), ("returned", "unreported"), ("blocked", "unreported"),
                                 ("passed", None), ("failed", None)):
            with self.subTest(status=status):
                self.assertEqual(self.liveness(status, "busy", known)["n"], (expected, None))
        self.assertEqual(self.liveness("pending", "idle", known)["n"], (None, None))
        self.assertEqual(self.liveness("pending", None, known)["n"], (None, None))

    def test_a_session_a_running_node_or_the_owner_uses_is_not_unreported(self):
        known = self.sessions(busy=(True, None))
        node = Node(id="n", type="implement", agent="impl", goal="g", status="pending", session="busy")
        other = Node(id="m", type="implement", agent="impl", goal="g", status="running", session="busy")
        self.assertEqual(contract.graph_liveness(graph([node, other]), known)["n"], (None, None))
        owned = graph([node])
        owned.session = "busy"
        self.assertEqual(contract.graph_liveness(owned, known)["n"], (None, None))

    def test_unreadable_database_confirms_nothing(self):
        self.assertEqual(self.liveness("running", "s", None)["n"], (None, None))
        self.assertEqual(self.liveness("running", None, None)["n"], ("orphan", None))  # no id is a fact of the graph
        self.assertEqual(self.liveness("pending", "s", None)["n"], (None, None))
