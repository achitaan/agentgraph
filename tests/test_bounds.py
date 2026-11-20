"""Costs a graph file controls stay bounded, and the fast checks agree with
the obvious slow ones."""
import contextlib
import io
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import check, contract, snapshot_json
from agentgraph.contract import (
    MAX_NODE_LIST, Artifact, Node, Snapshot, TaskGraph, _contains, _path_parts, _project_root, validate, waves,
)
from agentgraph.sources import graph_from_raw

# Each case's cost is counted in function calls (Python and built-in), not timed: a count is the same on a quiet
# machine and under load, so the suite's verdict never depends on the wall clock. Each budget is linear in what the
# file controls, about 1.5 times today's count; a step that compared every pair (writers x entries squared) would
# take thousands of times the budget, as would one that took 3 s on a quiet machine (the old wall-clock limit:
# the cases ran in one second or less, at 58 to 94 calls per scope entry and 32 per dependency edge).
CALLS_PER_ENTRY = 100  # per scope entry (writers x entries): validate, and the check command on a file
CALLS_PER_EDGE = 50  # per dependency edge: waves, advice, flows, loops, stale inputs and validate together
GRAPH_PATH = "/project/.opencode/graph/bounds.json"


def worker(node_id, scope=(), depends_on=(), changed_paths=()):
    return Node(node_id, "implement", "impl-openai", "goal", done_when="tests pass", scope=list(scope),
                depends_on=list(depends_on), changed_paths=list(changed_paths))


def task(nodes, path=GRAPH_PATH):
    return TaskGraph(path, "bounds", "goal", "running", 1, 2, nodes=list(nodes))


def counted(function, *args):
    """(result, the number of Python and built-in function calls made while running it). Deterministic: it counts
    work, not time, so load on the machine does not change it."""
    calls = 0

    def profile(frame, event, arg):
        nonlocal calls
        if event in ("call", "c_call"):
            calls += 1

    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        result = function(*args)
    finally:
        sys.setprofile(previous)
    return result, calls


# --- the slow methods, as they were before the scope index -------------------


def slow_waves(graph):
    by_id = {node.id: node for node in graph.nodes}
    placed, layers, remaining = {}, [], list(graph.nodes)
    while remaining:
        ready = [node for node in remaining
                 if all(dep in placed or dep not in by_id for dep in node.depends_on)]
        if not ready:
            layers.append(remaining)
            break
        for node in ready:
            placed[node.id] = len(layers)
        layers.append(ready)
        remaining = [node for node in remaining if node.id not in placed]
    return layers


def slow_scope_problems(graph):
    """Every scope verdict by comparing each pair of entries (no cap)."""
    root = _project_root(graph.path)
    by_id = {node.id: node for node in graph.nodes}
    scopes = {}
    problems = set()
    for node in graph.nodes:
        scope = scopes[node.id] = [_path_parts(path, root) for path in node.scope if path.strip()]
        for path in node.changed_paths:
            if not any(_contains(entry, _path_parts(path, root)) for entry in scope):
                problems.add(f"{node.id} changed path {path!r} outside its scope")
    for node in graph.nodes:
        for artifact in node.artifacts:
            allowed = scopes[node.id] if node.scope else [_path_parts(".opencode/artifacts/", root)]
            if not any(_contains(entry, _path_parts(artifact.path, root)) for entry in allowed):
                boundary = "its scope" if node.scope else ".opencode/artifacts/"
                problems.add(f"{node.id} artifact {artifact.id!r} path {artifact.path!r} outside {boundary}")
    ancestors = {}

    def upstream(node_id, seen=()):
        if node_id not in ancestors:
            found = set()
            for dep in by_id[node_id].depends_on:
                if dep in by_id and dep not in seen:
                    found |= {dep} | upstream(dep, (*seen, node_id))
            ancestors[node_id] = found
        return ancestors[node_id]

    order = [node_id for node_id in by_id if scopes[node_id]]
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if a in upstream(b) or b in upstream(a):
                continue
            if any(_contains(x, y) or _contains(y, x) for x in scopes[a] for y in scopes[b]):
                problems.add(f"{a} and {b} can run in parallel but share write scope")
    return problems


def fast_scope_problems(graph):
    words = ("changed path", "artifact", "share write scope")
    return {issue for issue in validate(graph) if any(word in issue for word in words)}


class ScopeIndexAgreesWithPairwise(unittest.TestCase):
    PATHS = (".", "src", "src/", "./src/a.py", "src/a.py", "src/a", "src/ab.py", "src/a/b.py", "src/../lib",
             "lib/x", "/project/src", "/project/src/a.py", "/other/x", "../outside", "../outside/y",
             ".opencode/artifacts/", ".opencode/artifacts/r.md", ".opencode/artifacts-other/r.md", "tests/")

    def test_random_graphs_give_identical_scope_verdicts(self):
        rng = random.Random(20261119)
        for trial in range(400):
            count = rng.randint(1, 9)
            nodes = []
            for i in range(count):
                deps = rng.sample([f"n{j}" for j in range(i)], k=min(i, rng.randint(0, 2)))
                node = worker(f"n{i}", rng.sample(self.PATHS, k=rng.randint(0, 3)), deps,
                              rng.sample(self.PATHS, k=rng.randint(0, 2)))
                node.artifacts = [Artifact(f"a{k}", rng.choice(self.PATHS), 1) for k in range(rng.randint(0, 2))]
                nodes.append(node)
            graph = task(nodes, path=rng.choice((GRAPH_PATH, "graph.json")))
            with self.subTest(trial=trial):
                self.assertEqual(fast_scope_problems(graph), slow_scope_problems(graph))

    def test_random_graphs_give_identical_waves(self):
        rng = random.Random(7)
        for trial in range(400):
            count = rng.randint(0, 9)
            ids = [f"n{rng.randint(0, count)}" for _ in range(count)]  # repeats on purpose
            nodes = [worker(node_id, depends_on=rng.sample(ids + ["missing"], k=min(len(ids), rng.randint(0, 3))))
                     for node_id in ids]
            graph = task(nodes)
            with self.subTest(trial=trial):
                self.assertEqual([[id(n) for n in layer] for layer in waves(graph)],
                                 [[id(n) for n in layer] for layer in slow_waves(graph)])


class BoundedCost(unittest.TestCase):
    def test_1000_writers_with_200_disjoint_scope_entries(self):
        nodes = [worker(f"n{i}", [f"d{i}/{k}" for k in range(MAX_NODE_LIST)]) for i in range(1000)]
        budget = CALLS_PER_ENTRY * 1000 * MAX_NODE_LIST
        problems, calls = counted(validate, task(nodes))
        self.assertEqual(problems, [])
        self.assertLess(calls, budget)
        # One real overlap among them is still found, and only that one.
        nodes[999].scope[-1] = "d0"
        problems, calls = counted(validate, task(nodes))
        self.assertEqual(problems, ["n0 and n999 can run in parallel but share write scope"])
        self.assertLess(calls, budget)

    def test_one_node_with_200_changed_paths_against_200_scope_entries(self):
        scope = [f"src/m{k}/" for k in range(MAX_NODE_LIST)]
        inside = [f"src/m{k}/file.py" for k in range(MAX_NODE_LIST)]
        outside = [f"lib/m{k}/file.py" for k in range(MAX_NODE_LIST)]
        for changed, expected in ((inside, []), (outside, [f"n0 changed path {p!r} outside its scope" for p in outside])):
            with self.subTest(inside=changed is inside):
                graph = task([worker("n0", scope, changed_paths=changed)])
                problems, calls = counted(validate, graph)
                self.assertEqual(problems, expected)
                self.assertEqual(set(problems), slow_scope_problems(graph))
                # Pairwise would compare 200 x 200 entries: 40,000 checks, beyond this budget by themselves.
                self.assertLess(calls, 150 * MAX_NODE_LIST)

    def test_dense_dependencies_and_inputs_stay_fast(self):
        nodes = []
        for i in range(1000):
            node = worker(f"n{i}", ["src/"], [f"n{j}" for j in range(max(0, i - MAX_NODE_LIST), i)])
            node.artifacts = [Artifact("out", f"src/n{i}.md", 1)]
            node.inputs = [contract.ArtifactInput(dep, "out", 1) for dep in node.depends_on]
            node.agent = "impl-openai" if i % 2 else "impl-claude"
            node.feedback_to = node.depends_on[-3:]
            nodes.append(node)
        graph = task(nodes)

        def everything():
            layers = waves(graph)
            contract.shape_advice(graph, layers)
            contract.agent_flows(graph, {"x"})
            contract.agent_loops(graph, {"x"})
            loops = contract.agent_work_loops(graph, {"x"})
            contract.stale_inputs(graph)
            validate(graph)
            return layers, loops

        (layers, loops), calls = counted(everything)
        self.assertEqual(len(loops), 2)
        self.assertLess(calls, CALLS_PER_EDGE * sum(len(node.depends_on) for node in nodes))
        self.assertEqual(len(layers), 1000)

    def write(self, directory, raw, name="bounds.json"):
        text = json.dumps(raw, separators=(",", ":"))
        self.assertLess(len(text.encode()), 2 * 1024 * 1024)
        (Path(directory) / name).write_text(text)

    def raw_graph(self, writers, entries, name="bounds", fmt="d{i}/{k}"):
        return {"task": name, "goal": "g", "status": "running", "created": 1, "updated": 2,
                "objectives": [{"id": "o1", "text": "t", "nodes": ["0"]}],
                "nodes": [{"id": str(i), "type": "implement", "agent": "impl-openai", "goal": "g",
                           "status": "pending", "done_when": "t",
                           "scope": [fmt.format(i=i, k=k) for k in range(entries)]} for i in range(writers)]}

    def run_check(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = check.main(list(args))
        return code, output.getvalue()

    def test_check_command_on_files_with_many_disjoint_writers(self):
        for writers, entries, fmt in ((300, 100, "d{i}/{k}"), (1000, MAX_NODE_LIST, "{i:x}/{k:x}")):
            with self.subTest(writers=writers, entries=entries), tempfile.TemporaryDirectory() as directory:
                self.write(directory, self.raw_graph(writers, entries, fmt=fmt))
                (code, output), calls = counted(self.run_check, "--dir", directory)
                self.assertLess(calls, CALLS_PER_ENTRY * writers * entries)
                self.assertEqual(code, 0, output)
                self.assertTrue(output.startswith(f"OK          bounds: 0/{writers} nodes passed"), output)

    def test_check_task_does_not_validate_other_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write(directory, self.raw_graph(1, 1, name="mine"), "mine.json")
            self.write(directory, self.raw_graph(300, 100, name="other"), "other.json")
            with patch.object(check, "validate", wraps=check.validate) as validated:
                code, output = self.run_check("--dir", directory, "--task", "mine")
            self.assertEqual(code, 0, output)
            self.assertEqual([call.args[0].task for call in validated.call_args_list], ["mine"])
            self.assertNotIn("other", output)

    def test_snapshot_computes_waves_once(self):
        graph = task([worker("a"), worker("b", depends_on=["a"])])
        with patch.object(contract, "waves", wraps=contract.waves) as computed:
            doc = snapshot_json.snapshot_document(Snapshot(1, graphs=[graph]), "live")
        self.assertEqual(computed.call_count, 1)
        self.assertEqual(doc["graph"]["waves"], [["a"], ["b"]])


class PerNodeListCaps(unittest.TestCase):
    def raw(self, **fields):
        node = {"id": "n1", "type": "implement", "agent": "impl-openai", "goal": "g", "status": "pending",
                "done_when": "t", **fields}
        return {"task": "caps", "goal": "g", "status": "running", "created": 1, "updated": 2,
                "objectives": [{"id": "o1", "text": "t", "nodes": ["n1"] * 201}], "nodes": [node, dict(node, id="n2")]}

    def test_loading_caps_every_list_and_reports_it(self):
        many = [f"x{k}" for k in range(250)]
        artifacts = [{"id": f"a{k}", "path": f".opencode/artifacts/a{k}.md", "revision": 1} for k in range(250)]
        inputs = [{"from": "n0", "artifact": f"a{k}", "revision": 1} for k in range(250)]
        graph = graph_from_raw(self.raw(depends_on=many, scope=many, changed_paths=many, feedback_to=many,
                                        tiers=many, artifacts=artifacts, inputs=inputs), Path("caps.json"))
        node = graph.nodes[0]
        for field in contract.NODE_LIST_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(len(getattr(node, field)), MAX_NODE_LIST)
        self.assertEqual(node.depends_on, many[:MAX_NODE_LIST])
        self.assertEqual(len(graph.objectives[0].nodes), MAX_NODE_LIST)
        self.assertEqual(graph.truncated, [
            f"nodes[].{field}: kept {MAX_NODE_LIST} entries in 2 nodes"
            for field in ("depends_on", "scope", "changed_paths", "feedback_to", "artifacts", "inputs", "tiers")
        ] + [f"objectives[].nodes: kept {MAX_NODE_LIST} entries in 1 objective"])

    def test_duplicate_dependencies_and_feedback_are_removed_in_order(self):
        graph = graph_from_raw(self.raw(depends_on=["b", "a", "b", "a", "c"], feedback_to=["z", "y", "z"]),
                               Path("caps.json"))
        self.assertEqual(graph.nodes[0].depends_on, ["b", "a", "c"])
        self.assertEqual(graph.nodes[0].feedback_to, ["z", "y"])
        # Duplicates do not count against the cap.
        graph = graph_from_raw(self.raw(depends_on=["a"] * 1000), Path("caps.json"))
        self.assertEqual(graph.nodes[0].depends_on, ["a"])
        self.assertFalse([notice for notice in graph.truncated if "depends_on" in notice])

    def test_check_reports_lists_over_the_cap(self):
        many = [f"x{k}" for k in range(201)]
        raw = self.raw(scope=many, tiers=many)
        raw["nodes"][1]["id"] = ""
        problems = check._raw_issues(raw)
        self.assertEqual(problems, [
            "node 'n1' has 201 scope; the limit is 200", "node 'n1' has 201 tiers; the limit is 200",
            "nodes[1] has 201 scope; the limit is 200", "nodes[1] has 201 tiers; the limit is 200",
            "objective 'o1' has 201 nodes; the limit is 200",
        ])
        raw = self.raw()
        raw["objectives"][0]["nodes"] = ["n1"]
        raw["nodes"] = [dict(raw["nodes"][0], id=f"n{i}", scope=many) for i in range(150)]
        problems = check._raw_issues(raw)
        self.assertEqual(len(problems), 101)
        self.assertEqual(problems[-1], "... and more lists over the limit (further checks skipped)")


if __name__ == "__main__":
    unittest.main()
