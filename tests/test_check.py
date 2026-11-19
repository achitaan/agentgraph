import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph.check import file_problems, main


def valid_graph():
    return {
        "task": "test-task",
        "goal": "Validate a graph file",
        "status": "passed",
        "created": 1,
        "updated": 2,
        "revision": 1,
        "objectives": [{"id": "o1", "text": "Deliver the change", "nodes": ["n1"]}],
        "nodes": [{
            "id": "n1",
            "type": "implement",
            "agent": "impl-openai",
            "goal": "Make the change",
            "status": "passed",
            "depends_on": [],
            "scope": ["src/a.py"],
            "done_when": "unit tests pass",
            "evidence": "12 unit tests passed",
            "changed_paths": ["src/a.py"],
        }],
    }


class Check(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def write_graph(self, data, filename="test-task.json"):
        (self.directory / filename).write_text(json.dumps(data))

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(list(args))
        return result, output.getvalue()

    def run_check(self, directory=None, *args):
        return self.invoke("--dir", str(self.directory if directory is None else directory), *args)

    def test_valid_graph_with_objectives(self):
        self.write_graph(valid_graph())
        result, output = self.run_check()
        self.assertEqual(result, 0)
        self.assertTrue(output.splitlines()[0].startswith("OK"), output)
        self.assertIn("test-task: 1/1 nodes passed, 1/1 objectives delivered, revision 1", output)

    def test_raw_graph_record_caps_are_problems_not_silent_truncation(self):
        from agentgraph.check import read_graph

        for field, limit, record in (("nodes", 1000, valid_graph()["nodes"][0]),
                                     ("events", 2000, {"time": 1, "node": "", "event": "declared"}),
                                     ("objectives", 200, valid_graph()["objectives"][0])):
            with self.subTest(field=field):
                data = valid_graph()
                count = 5000 if field == "nodes" else limit + 1
                data[field] = [record] * count
                self.write_graph(data)
                loaded, problems = read_graph(self.directory / "test-task.json")
                self.assertEqual(problems, [f"graph has {count} {field}; the limit is {limit}"])
                self.assertEqual(len(getattr(loaded, field)), limit)
                self.assertEqual(loaded.truncated, [f"{field}: kept {limit} of {count}"])
                result, output = self.run_check()
                self.assertEqual(result, 1, output)
                self.assertIn(problems[0], output)

    def test_all_record_caps_are_reported_together(self):
        data = valid_graph()
        data.update(nodes=[None] * 1001, events=[None] * 2001, objectives=[None] * 201)
        self.write_graph(data)
        self.assertEqual(file_problems(self.directory / "test-task.json"), [
            "graph has 1001 nodes; the limit is 1000", "graph has 2001 events; the limit is 2000",
            "graph has 201 objectives; the limit is 200",
        ])

    def test_symlink_and_oversized_graphs_are_unreadable(self):
        from agentgraph.check import read_graph

        target = self.directory / "target.txt"
        link = self.directory / "linked.json"
        for size in (0, 2 * 1024 * 1024):
            with self.subTest(size=size):
                target.write_text(json.dumps(valid_graph()) + " " * size)
                link.symlink_to(target)
                try:
                    graph, problems = read_graph(link)
                    self.assertIsNone(graph)
                    self.assertEqual(len(problems), 1)
                    result, output = self.run_check()
                    self.assertEqual(result, 2, output)
                    self.assertIn("UNREADABLE  linked.json", output)
                finally:
                    link.unlink()
        target.rename(self.directory / "large.json")
        result, output = self.run_check()
        self.assertEqual(result, 2, output)
        self.assertIn("2097152", output)

    def test_fifo_graph_is_unreadable_without_blocking(self):
        os.mkfifo(self.directory / "pipe.json")
        try:
            result = subprocess.run([sys.executable, "-m", "agentgraph.check", "--dir", str(self.directory)],
                                    capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            self.fail("checking a graph FIFO blocked")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("UNREADABLE  pipe.json", result.stdout)
        self.assertIn("not a regular file", result.stdout)

    def test_directory_limit_checks_only_200_newest_and_warns(self):
        for i in range(203):
            data = valid_graph()
            data["task"] = f"task-{i:03}"
            path = self.directory / f"{i:03}.json"
            path.write_text(json.dumps(data))
            os.utime(path, ns=(1_000_000_000 + i, 1_000_000_000 + i))
        result, output = self.run_check()
        self.assertEqual(result, 2, output)
        self.assertEqual(len([line for line in output.splitlines() if line.startswith("OK")]), 200)
        self.assertNotIn("task-002:", output)
        self.assertIn("task-003:", output)
        self.assertIn("task-202:", output)
        self.assertIn("200", output)
        self.assertIn("skipped 3", output)

    def test_foreign_discovery_warning_is_printed(self):
        home = self.directory / "home"
        project = home / "project"
        (project / ".opencode/graph").mkdir(parents=True)
        with (patch("agentgraph.sources.Path.home", return_value=home),
              patch("agentgraph.check.Path.cwd", return_value=project),
              patch("agentgraph.sources.os.getuid", return_value=os.getuid() + 1)):
            result, output = self.invoke()
        self.assertEqual(result, 2, output)
        self.assertIn("owned", output)

    def test_passed_node_without_evidence(self):
        data = valid_graph()
        del data["nodes"][0]["evidence"]
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn("n1 passed with no evidence", output)
        self.assertEqual(file_problems(self.directory / "test-task.json"), ["n1 passed with no evidence"])

    def test_file_problems_distinguishes_unreadable_sound_and_invalid(self):
        path = self.directory / "test-task.json"
        self.assertIsNone(file_problems(path))
        self.write_graph(valid_graph())
        self.assertEqual(file_problems(path), [])
        data = valid_graph()
        data["updated"] = True
        self.write_graph(data)
        self.assertEqual(file_problems(path), ["graph updated must be a finite number greater than 0"])

    def test_read_graph_decodes_once_and_preserves_structural_problem_and_metadata(self):
        from agentgraph.check import read_graph

        data = valid_graph()
        data["nodes"][0]["depends_on"] = ["missing"]
        data["nodes"][0]["attempts"] = 2
        data["lessons"] = ["Regression test first"]
        self.write_graph(data)
        path = self.directory / "test-task.json"
        with patch("agentgraph.check.json.loads", wraps=json.loads) as decode:
            graph, problems = read_graph(path)
        decode.assert_called_once()
        self.assertIsNotNone(graph)
        self.assertEqual((graph.created, graph.updated), (1, 2))
        self.assertEqual(graph.nodes[0].attempts, 2)
        self.assertEqual(graph.lessons, data["lessons"])
        self.assertEqual(problems, ["n1 depends on unknown node missing"])
        self.assertEqual(file_problems(path), problems)

    def test_read_graph_returns_tolerant_graph_and_raw_problems(self):
        from agentgraph.check import read_graph

        data = valid_graph()
        data["nodes"][0]["depends_on"] = "missing"
        self.write_graph(data)
        path = self.directory / "test-task.json"
        graph, problems = read_graph(path)
        self.assertIsNotNone(graph)
        self.assertEqual(graph.nodes[0].depends_on, [])
        self.assertEqual(problems, ["node 'n1' depends_on must be a list of strings"])
        self.assertEqual(file_problems(path), problems)

    def test_read_graph_returns_no_graph_and_one_problem_when_unreadable(self):
        from agentgraph.check import read_graph

        path = self.directory / "test-task.json"
        for contents in (None, "{malformed", "[]"):
            with self.subTest(contents=contents):
                if contents is not None:
                    path.write_text(contents)
                graph, problems = read_graph(path)
                self.assertIsNone(graph)
                self.assertEqual(len(problems), 1)
                self.assertTrue(problems[0])
                self.assertIsNone(file_problems(path))

    def test_structural_problem(self):
        data = valid_graph()
        data["nodes"][0]["depends_on"] = ["missing"]
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1)
        self.assertTrue(output.splitlines()[0].startswith("PROBLEMS"), output)
        self.assertIn("n1 depends on unknown node missing", output)

    def test_no_objectives(self):
        data = valid_graph()
        data["objectives"] = []
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn("PROBLEMS", output)
        self.assertIn("no objectives declared", output)

    def test_unknown_node_type(self):
        data = valid_graph()
        data["nodes"][0]["type"] = "unknown-type"
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn("PROBLEMS", output)
        self.assertIn("n1 has unknown type 'unknown-type'", output)

    def test_unknown_node_status(self):
        data = valid_graph()
        data["nodes"][0]["status"] = "unknown-status"
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn("PROBLEMS", output)
        self.assertIn("n1 has unknown status 'unknown-status'", output)

    def test_malformed_json(self):
        (self.directory / "bad.json").write_text("{invalid json")
        result, output = self.run_check()
        self.assertEqual(result, 2)
        self.assertTrue(output.splitlines()[0].startswith("UNREADABLE"), output)
        self.assertIn("bad.json", output)

    def test_nonexistent_directory(self):
        result, output = self.run_check(self.directory / "missing")
        self.assertEqual(result, 2)
        self.assertIn("not a directory", output)

    def test_explicit_graph_file_is_not_a_directory(self):
        self.write_graph(valid_graph())
        result, output = self.run_check(self.directory / "test-task.json")
        self.assertEqual(result, 2)
        self.assertIn("not a directory", output)

    def test_no_directory_discovered_is_a_noop(self):
        with patch("agentgraph.check.find_graph_dir", return_value=None):
            result, output = self.invoke()
        self.assertEqual(result, 0)
        self.assertIn("no .opencode/graph directory found", output)

    def test_task_selects_by_declared_name_not_filename(self):
        other = valid_graph()
        other["task"] = "other-task"
        other["nodes"][0]["depends_on"] = "missing"
        self.write_graph(other)
        self.write_graph(valid_graph(), "different-filename.json")
        result, output = self.run_check(None, "--task", "test-task")
        self.assertEqual(result, 0)
        self.assertIn("test-task: 1/1 nodes passed", output)
        self.assertNotIn("other-task", output)

    def test_requested_task_not_found(self):
        self.write_graph(valid_graph())
        result, output = self.run_check(None, "--task", "missing-task")
        self.assertEqual(result, 2)
        self.assertIn("missing-task", output)
        self.assertIn("not found", output)

    def test_requested_task_does_not_match_a_coerced_name(self):
        data = valid_graph()
        del data["task"]
        self.write_graph(data)
        result, output = self.run_check(None, "--task", "test-task")
        self.assertEqual(result, 2)
        self.assertIn("not found", output)

    def test_requested_task_and_no_discovered_directory(self):
        with patch("agentgraph.check.find_graph_dir", return_value=None):
            result, output = self.invoke("--task", "missing-task")
        self.assertEqual(result, 2)
        self.assertIn("missing-task", output)

    def test_raw_graph_fields(self):
        for field, value in (("task", None), ("task", " \t"), ("goal", 42), ("status", "complete"),
                             ("nodes", {}), ("objectives", "o1"), ("events", {}),
                             ("amendments", 7), ("amendments", [1]), ("lessons", None), ("lessons", [{}]),
                             ("revision", 1.5), ("revision", True), ("session", 3), ("created", "1"),
                             ("created", None), ("updated", True)):
            with self.subTest(field=field, value=value):
                data = valid_graph()
                data[field] = value
                self.write_graph(data)
                result, output = self.run_check()
                self.assertEqual(result, 1, output)
                self.assertIn(f"graph {field}", output)

    def test_event_entries_and_fields(self):
        event = {"time": 1.5, "node": "", "event": "declared", "note": "Graph created"}
        for field, value in (("time", True), ("time", "1.5"), ("node", None), ("event", " \t"),
                             ("event", []), ("event", "deployed"), ("note", None)):
            with self.subTest(field=field, value=value):
                data = valid_graph()
                data["events"] = [event, {**event, field: value}]
                self.write_graph(data)
                result, output = self.run_check()
                self.assertEqual(result, 1, output)
                self.assertIn(f"events[1] {field}", output)

        data = valid_graph()
        data["events"] = [42, {}]
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("events[0] must be an object", output)
        for field in ("time", "node", "event"):
            self.assertIn(f"events[1] {field}", output)

    def test_graph_timestamps_are_required_and_positive(self):
        for field in ("created", "updated"):
            for value in (None, 0, -1):
                with self.subTest(field=field, value=value):
                    data = valid_graph()
                    if value is None:
                        del data[field]
                    else:
                        data[field] = value
                    self.write_graph(data)
                    result, output = self.run_check()
                    self.assertEqual(result, 1, output)
                    self.assertIn(f"graph {field}", output)

    def test_timestamps_cannot_overflow_to_infinity(self):
        for field, location, label in (
            ("created", None, "graph"), ("updated", None, "graph"),
            ("started", "nodes", "node 'n1'"), ("finished", "nodes", "node 'n1'"),
            ("time", "events", "events[0]"),
        ):
            with self.subTest(field=field):
                data = valid_graph()
                data["events"] = [{"event": "declared", "node": "", "time": 1}]
                record = data if location is None else data[location][0]
                record[field] = "NUMBER"
                (self.directory / "test-task.json").write_text(json.dumps(data).replace('"NUMBER"', "1e999"))
                result, output = self.run_check()
                self.assertEqual(result, 1, output)
                self.assertIn(f"{label} {field}", output)

    def test_well_typed_optional_metadata(self):
        data = valid_graph()
        data.update(session=None, created=1.25, updated=2,
                    amendments=["Amendment"], lessons=["Lesson"],
                    events=[{"time": 1.25, "node": "", "event": "declared", "extra": {}}])
        data["nodes"][0].update(started=1.5, finished=2, failure_kind="code", verdict="", baseline="",
                                outputs="The worker delivered a change")
        data["nodes"].append({
            "id": "n2", "type": "implement", "agent": "impl-openai", "goal": "Finish the work",
            "status": "pending", "done_when": "tests pass", "started": None, "finished": None,
            "failure_kind": "", "verdict": "unverifiable",
        })
        data["status"] = "running"
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 0, output)

    def test_non_object_graph_is_a_schema_issue(self):
        self.write_graph([valid_graph()])
        result, output = self.run_check()
        self.assertEqual(result, 1)
        self.assertIn("graph must be an object", output)

    def test_node_required_fields(self):
        for field in ("id", "type", "agent", "goal", "done_when", "status"):
            for value in (None, " \t", 42):
                with self.subTest(field=field, value=value):
                    data = valid_graph()
                    if value is None:
                        del data["nodes"][0][field]
                    else:
                        data["nodes"][0][field] = value
                    self.write_graph(data)
                    result, output = self.run_check()
                    self.assertEqual(result, 1, output)
                    label = "nodes[0]" if field == "id" else "node 'n1'"
                    self.assertIn(f"{label} {field}", output)

    def test_node_optional_fields_are_not_coerced(self):
        for field, value in (("depends_on", "missing"), ("depends_on", [42]),
                             ("scope", [42]), ("tiers", "impl-openai"), ("changed_paths", [42]),
                             ("attempts", True), ("attempts", 1.5), ("attempts", "2"),
                             ("stalled", "false"), ("session", []),
                             ("failure_kind", "timeout"), ("failure_kind", None),
                             ("verdict", "passed"), ("verdict", {}), ("evidence", []),
                             ("baseline", 1), ("outputs", None), ("started", True), ("finished", "2"),
                             ("pattern", 5), ("pattern", None), ("feedback_to", "n1"), ("feedback_to", [1])):
            with self.subTest(field=field, value=value):
                data = valid_graph()
                data["nodes"][0][field] = value
                self.write_graph(data)
                result, output = self.run_check()
                self.assertEqual(result, 1, output)
                self.assertIn(f"node 'n1' {field}", output)

    def test_artifact_and_input_raw_types_are_strict(self):
        records = {"artifacts": {"id": "report", "path": "src/a.py", "revision": 1},
                   "inputs": {"from": "n1", "artifact": "report", "revision": 1}}
        for field, valid in records.items():
            for value in (None, "report", {}, [None], [1], [[]]):
                with self.subTest(field=field, value=value):
                    data = valid_graph()
                    data["nodes"][0][field] = value
                    self.write_graph(data)
                    result, output = self.run_check()
                    self.assertEqual(result, 1, output)
                    self.assertIn(f"node 'n1' {field}", output)
            for key in valid:
                values = (None, "", " \t", 42, True) if key != "revision" else (None, "1", 0, -1, 1.5, True, False)
                for value in values:
                    with self.subTest(field=field, key=key, value=value):
                        data = valid_graph()
                        data["nodes"][0][field] = [{**valid, key: value}]
                        self.write_graph(data)
                        result, output = self.run_check()
                        self.assertEqual(result, 1, output)
                        self.assertIn(f"node 'n1' {field}[0] {key}", output)
                data = valid_graph()
                data["nodes"][0][field] = [{k: v for k, v in valid.items() if k != key}]
                self.write_graph(data)
                self.assertIn(f"node 'n1' {field}[0] {key}", self.run_check()[1])

    def test_artifacts_and_inputs_reach_structural_validation(self):
        data = valid_graph()
        data["nodes"][0]["artifacts"] = [{"id": "report", "path": "outside.md", "revision": 2}]
        self.write_graph(data)
        self.assertIn("outside its scope", self.run_check()[1])
        data["nodes"][0]["artifacts"][0]["path"] = "src/a.py"
        data["nodes"].append({**data["nodes"][0], "id": "n2", "depends_on": ["n1"],
                              "inputs": [{"from": "n1", "artifact": "report", "revision": 1}]})
        self.write_graph(data)
        self.assertIn("stale input", self.run_check()[1])
        data["nodes"][1]["inputs"][0]["revision"] = 2
        self.write_graph(data)
        self.assertEqual(self.run_check()[0], 0)

    def test_advice_prints_but_is_not_a_problem(self):
        data = valid_graph()
        first = data["nodes"][0]
        data["nodes"] = [first, *(
            {**first, "id": f"n{i}", "depends_on": [f"n{i - 1}"], "pattern": "chain"}
            for i in range(2, 5)
        )]
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertIn("\nadvice: Nodes n1, n2, n3, n4 form a single chain", output)
        self.assertIn("\nadvice: Nodes n1, n2, n3, n4 look like fix rounds", output)
        path = self.directory / "test-task.json"
        self.assertEqual(file_problems(path), [])
        from agentgraph.check import read_graph
        self.assertEqual(read_graph(path)[1], [])

    def test_objective_fields(self):
        for field, value in (("id", " \t"), ("text", None), ("nodes", "n1"), ("nodes", [1])):
            with self.subTest(field=field, value=value):
                data = valid_graph()
                data["objectives"][0][field] = value
                self.write_graph(data)
                result, output = self.run_check()
                self.assertEqual(result, 1, output)
                label = "objectives[0]" if field == "id" else "objective 'o1'"
                self.assertIn(f"{label} {field}", output)

    def test_non_object_entries_are_not_dropped_and_all_issues_are_reported(self):
        data = valid_graph()
        data["nodes"].append("n2")
        data["objectives"].append(None)
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("nodes[1] must be an object", output)
        self.assertIn("objectives[1] must be an object", output)

    def test_unknown_fields_are_ignored_and_integers_are_not_rounded(self):
        data = valid_graph()
        data["revision"] = 10**30 + 1
        data["extra"] = {"nodes": "not validated"}
        data["nodes"][0].update(extra=[1, {}], attempts=10**350, stalled=False, session=None,
                                type="gate", verdict="confirmed")
        data["objectives"][0]["extra"] = True
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertIn(f"revision {data['revision']}", output)

    def test_deep_malformed_json_does_not_raise(self):
        # Decoders have different depth limits; the missing bracket ensures
        # this input is unreadable even if the decoder can handle the depth.
        (self.directory / "bad.json").write_text("[" * 5000 + "0" + "]" * 4999)
        result, output = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("UNREADABLE", output)

    def test_decoder_recursion_error_does_not_raise(self):
        self.write_graph(valid_graph())
        with patch("agentgraph.check.json.loads", side_effect=RecursionError("decoder depth limit")):
            result, output = self.run_check()
        self.assertEqual(result, 2)
        self.assertIn("UNREADABLE", output)
        self.assertIn("decoder depth limit", output)

    def test_unencodable_json_string_does_not_raise_when_printing(self):
        data = valid_graph()
        data["task"] = "task-\ud800"
        self.write_graph(data)
        with io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict") as output:
            with contextlib.redirect_stdout(output):
                result = main(["--dir", str(self.directory)])
            self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
