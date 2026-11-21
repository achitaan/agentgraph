import contextlib
import hashlib
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

    def test_a_passing_graph_leaves_a_history_that_is_never_listed_as_a_graph(self):
        self.write_graph(valid_graph())
        self.assertEqual(self.run_check()[0], 0)
        self.assertEqual([p.name for p in (self.directory / ".history").iterdir()], ["test-task.state"])
        result, output = self.run_check()  # the history folder is not read as a graph file
        self.assertEqual(result, 0, output)
        self.assertEqual(output.count("OK "), 1, output)

    def test_history_rule_rejects_lost_work_and_no_history_skips_it(self):
        self.write_graph(valid_graph())
        self.assertEqual(self.run_check()[0], 0)
        data = valid_graph()
        data["nodes"][0]["status"] = "running"
        data["status"] = "running"
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("was passed and is now running", output)
        self.assertEqual(self.run_check(None, "--no-history")[0], 0)

    def test_history_free_warning_for_a_task_that_differs_from_the_file_name(self):
        self.write_graph(valid_graph(), "other-name.json")
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertIn("warning: task 'test-task' differs from the file name 'other-name.json'", output)


class NodeFields(unittest.TestCase):
    """The fields `agentgraph node run` writes (check, verify, budget, loop_run, spent, ended) and the two holes it closed."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.directory = self.project / ".opencode" / "graph"
        self.directory.mkdir(parents=True)

    def write_graph(self, data):
        (self.directory / "test-task.json").write_text(json.dumps(data))

    def run_check(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--dir", str(self.directory), "--no-history", *args])
        return result, output.getvalue()

    def running_graph(self, **node):
        data = valid_graph()
        data["status"] = "running"
        data["nodes"][0].update(status="running", evidence="", session="ses_x", changed_paths=[], **node)
        return data

    def test_old_graphs_without_the_new_fields_stay_valid(self):
        self.write_graph(valid_graph())
        self.assertEqual(self.run_check()[0], 0)
        self.write_graph(self.running_graph(started=5.5))
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertNotIn("warning", output)

    def test_the_new_fields_are_accepted(self):
        self.write_graph(self.running_graph(check="python3 -m unittest", verify="reviewer", budget={"cost": 2.5, "attempts": 4},
                                            spent=0.5, started=5.5, ended=None))
        result, output = self.run_check()
        self.assertEqual(result, 0, output)

    def test_the_new_fields_are_typed(self):
        for field, value in (("check", ""), ("check", 3), ("verify", " "), ("verify", []), ("budget", 5), ("budget", {"cost": "x"}),
                             ("budget", {"cost": 0}), ("budget", {"attempts": 0}), ("budget", {"attempts": 2.5}), ("budget", {"money": 1}),
                             ("loop_run", "../x"), ("loop_run", 7), ("spent", "1"), ("spent", -1), ("spent", True), ("ended", "now"), ("ended", True)):
            with self.subTest(field=field, value=value):
                self.write_graph(self.running_graph(**{field: value}))
                result, output = self.run_check()
                self.assertEqual(result, 1, output)
                self.assertIn(f" {field}", output)

    def test_a_graph_marked_passed_may_not_have_a_running_node(self):
        data = self.running_graph()
        data["status"] = "passed"
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("graph is passed but node n1 is running", output)

    def test_a_completed_event_may_not_stand_over_a_running_node(self):
        data = self.running_graph()
        data["events"] = [{"time": 1, "node": "", "event": "completed", "note": "done"}]
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("graph is completed but node n1 is still running", output)

    def test_spent_over_the_budget_is_a_warning(self):
        self.write_graph(self.running_graph(budget={"cost": 1.0}, spent=1.5))
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertIn("warning: n1 spent $1.50, over its budget of $1.00", output)

    def test_a_loop_run_that_does_not_exist_is_a_warning(self):
        self.write_graph(self.running_graph(loop_run="20260101-000000-abcd"))
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertIn("warning: n1 names the loop run 20260101-000000-abcd, which is not in", output)
        (self.project / ".opencode" / "loops" / "20260101-000000-abcd").mkdir(parents=True)
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertNotIn("names the loop run", output)

    def test_a_graph_reopened_after_a_completed_event_may_have_a_running_node(self):
        data = self.running_graph()
        data["events"] = [{"time": 1, "node": "", "event": "completed", "note": "done"},
                          {"time": 2, "node": "n1", "event": "retried", "note": "again"}]
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertNotIn("completed but node", output)

    def test_a_graph_that_is_passed_now_with_a_running_node_is_an_error_whatever_the_events(self):
        data = self.running_graph()
        data["status"] = "passed"
        data["events"] = [{"time": 1, "node": "", "event": "completed", "note": "done"},
                          {"time": 2, "node": "n1", "event": "retried", "note": "again"}]
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("graph is passed but node n1 is running", output)

    def test_spent_may_be_null_until_a_cost_is_known(self):
        self.write_graph(self.running_graph(spent=None, unpriced=2))
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertIn("warning: n1 has 2 turns with no known cost: it spent an unknown amount", output)

    def test_unpriced_turns_with_a_known_part_say_at_least(self):
        self.write_graph(self.running_graph(spent=0.25, unpriced=1))
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertIn("warning: n1 has 1 turn with no known cost: it spent at least $0.25", output)

    def test_unpriced_is_typed(self):
        for value in (-1, 1.5, "2", True):
            with self.subTest(value=value):
                self.write_graph(self.running_graph(unpriced=value))
                result, output = self.run_check()
                self.assertEqual(result, 1, output)
                self.assertIn(" unpriced", output)

    def test_limit_is_a_failure_kind(self):
        data = valid_graph()
        data["status"] = "running"
        data["nodes"][0].update(status="failed", failure_kind="limit", evidence="ran out of money", changed_paths=[])
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 0, output)

    def test_the_stall_candidate_warning_skips_a_node_whose_run_passed(self):
        run = "20260101-000000-abcd"
        folder = self.project / ".opencode" / "loops" / run
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(json.dumps({"status": "passed", "run": run}))
        data = self.running_graph(attempts=4, loop_run=run)
        data["status"] = "running"
        data["nodes"][0]["status"] = "returned"
        data["nodes"][0]["evidence"] = "the check passed"
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertNotIn("stall candidate", output)
        (folder / "run.json").write_text(json.dumps({"status": "failed", "run": run}))
        result, output = self.run_check()
        self.assertIn("stall candidate", output)

    def test_a_paused_run_names_node_cancel_and_prefers_it_to_loop_resume(self):
        run = "20260101-000000-abcd"
        folder = self.project / ".opencode" / "loops" / run
        folder.mkdir(parents=True)
        (folder / "run.json").write_text(json.dumps({"status": "paused", "run": run}))
        self.write_graph(self.running_graph(loop_run=run))
        result, output = self.run_check()
        self.assertEqual(result, 0, output)
        self.assertIn("agentgraph node cancel --task test-task --node n1", output)
        self.assertIn("prefer it to loop resume", output)  # a resumed run has no follower: node cancel is the way out
        self.assertNotIn(f"agentgraph loop resume {run}", output)

    def test_a_check_changed_after_dispatch_needs_an_amendment(self):
        (self.directory / ".history").mkdir()
        data = self.running_graph(check="python3 -m unittest")
        self.write_graph(data)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--dir", str(self.directory)]), 0, output.getvalue())  # saves the history
        data["nodes"][0]["check"] = "true"
        self.write_graph(data)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--dir", str(self.directory)])
        self.assertEqual(result, 1, output.getvalue())
        self.assertIn("n1 check changed without an amendment", output.getvalue())

    def saved_history(self, data):
        (self.directory / ".history").mkdir(exist_ok=True)
        self.write_graph(data)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--dir", str(self.directory)]), 0, output.getvalue())  # saves the history

    def with_history(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--dir", str(self.directory)])
        return result, output.getvalue()

    def test_spent_going_down_is_an_error_against_the_history(self):
        data = self.running_graph(check="true", budget={"cost": 5.0, "attempts": 3}, spent=0.5, loop_run="")
        data["nodes"][0].pop("loop_run")
        self.saved_history(data)
        data["nodes"][0]["spent"] = 0.1
        self.write_graph(data)
        result, output = self.with_history()
        self.assertEqual(result, 1, output)
        self.assertIn("n1 spent went down from $0.5000 to $0.1000", output)
        data["nodes"][0].pop("spent")
        self.write_graph(data)
        self.assertIn("n1 spent went down from $0.5000 to nothing", self.with_history()[1])
        data["nodes"][0]["spent"] = 0.7  # it may grow
        self.write_graph(data)
        result, output = self.with_history()
        self.assertEqual(result, 0, output)

    def test_a_budget_changed_after_dispatch_needs_an_amended_event(self):
        data = self.running_graph(check="true", budget={"cost": 5.0, "attempts": 3}, spent=0.5)
        data["nodes"][0].pop("loop_run", None)
        self.saved_history(data)
        data["nodes"][0]["budget"] = {"cost": 50.0, "attempts": 3}
        self.write_graph(data)
        result, output = self.with_history()
        self.assertEqual(result, 1, output)
        self.assertIn("n1 budget changed without an amended event", output)
        data.setdefault("events", []).append({"time": data["updated"] + 1, "node": "n1", "event": "amended", "note": "budget changed by node run --raise-budget"})
        data["updated"] += 1
        self.write_graph(data)
        result, output = self.with_history()
        self.assertEqual(result, 0, output)

    def test_the_budget_a_node_gets_when_it_is_dispatched_is_not_a_change(self):
        data = valid_graph()
        data["nodes"][0].update(status="pending", evidence="", changed_paths=[])
        data["status"] = "running"
        self.saved_history(data)
        data["nodes"][0].update(status="running", evidence="", session="ses_x", changed_paths=[], check="true", budget={"cost": 5.0, "attempts": 3})
        data.setdefault("events", []).append({"time": data["updated"] + 1, "node": "n1", "event": "dispatched", "note": "x"})
        data["updated"] += 1
        data["status"] = "running"
        self.write_graph(data)
        result, output = self.with_history()
        self.assertEqual(result, 0, output)


class EndToEnd(unittest.TestCase):
    """The node's `e2e` command and the `review_copy` record `agentgraph review-copy` writes: types, the rule for a passed node, the amendment."""

    E2E = "python3 main.py | grep -q ok"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / ".opencode" / "graph"
        self.directory.mkdir(parents=True)

    def write_graph(self, data):
        (self.directory / "test-task.json").write_text(json.dumps(data))

    def run_check(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["--dir", str(self.directory), *args])
        return result, output.getvalue()

    def graph(self, review=True, **review_changes):
        """A passed graph whose node declares an e2e; `review` is the node's review_copy (True: a good one)."""
        data = valid_graph()
        data["events"] = [{"time": 5, "node": "n1", "event": "returned", "note": "ok"}]
        data["nodes"][0]["e2e"] = self.E2E
        if review:
            data["nodes"][0]["review_copy"] = {
                "result": ".opencode/artifacts/test-task/n1/review-copy-1.json", "check_exit": 0, "e2e_exit": 0, "tree": "ab" * 32, "at": 6,
                "e2e_sha256": hashlib.sha256(self.E2E.encode()).hexdigest(), "project_unchanged": True, **review_changes}
        return data

    def problem(self, data):
        self.write_graph(data)
        result, output = self.run_check("--no-history")
        return result, output

    def test_graphs_without_an_e2e_are_not_affected(self):
        data = valid_graph()
        self.write_graph(data)
        self.assertEqual(self.run_check("--no-history")[0], 0)
        data["nodes"][0]["review_copy"] = {"result": "r.json", "check_exit": 1, "e2e_exit": None, "tree": "t", "at": 1}  # recorded, never required
        self.assertEqual(self.problem(data)[0], 0)

    def test_the_fields_are_typed(self):
        for field, value in (("e2e", ""), ("e2e", 3), ("e2e", " "), ("review_copy", "x"), ("review_copy", [])):
            with self.subTest(field=field, value=value):
                data = valid_graph()
                data["nodes"][0][field] = value
                result, output = self.problem(data)
                self.assertEqual(result, 1, output)
                self.assertIn(f"node 'n1' {field} must be", output)
        for key, value in (("result", ""), ("tree", ""), ("at", "x"), ("at", None), ("check_exit", "0"), ("e2e_exit", 1.5), ("e2e_exit", True),
                           ("e2e_sha256", 5), ("project_unchanged", "yes")):
            with self.subTest(key=key, value=value):
                data = self.graph(**{key: value})
                data["nodes"][0]["status"] = "returned"  # only the types are in question
                result, output = self.problem(data)
                self.assertEqual(result, 1, output)
                self.assertIn(f"review_copy {key} must be", output)

    def test_a_passed_node_with_an_e2e_needs_a_good_review_copy(self):
        self.assertEqual(self.problem(self.graph())[0], 0)
        result, output = self.problem(self.graph(review=False))
        self.assertEqual(result, 1, output)
        self.assertIn("node 'n1' declares an e2e and is passed, but has no review copy", output)
        for changes, words in (({"e2e_exit": 1}, "e2e did not exit 0"), ({"e2e_exit": None}, "e2e did not exit 0"),
                               ({"e2e_sha256": "0" * 64}, "different e2e"), ({"project_unchanged": False}, "did not leave the project unchanged"),
                               ({"at": 4.9}, "was taken before the node's last returned, attempt or restart")):
            with self.subTest(changes=changes):
                result, output = self.problem(self.graph(**changes))
                self.assertEqual(result, 1, output)
                self.assertIn(words, output)

    def test_a_review_copy_is_not_stale_when_it_is_as_new_as_the_last_returned_or_attempt_event(self):
        data = self.graph(at=5)
        self.assertEqual(self.problem(data)[0], 0)  # not older
        data["events"].append({"time": 7, "node": "n1", "event": "attempt", "note": "fixed -> ok"})
        result, output = self.problem(data)
        self.assertEqual(result, 1, output)
        self.assertIn("was taken before the node's last returned, attempt or restart", output)
        data["events"].append({"time": 9, "node": "n2", "event": "returned", "note": "another node"})  # not this node's
        data["events"][-2:] = [{"time": 5.5, "node": "n1", "event": "evaluated", "note": "not a returned or attempt event"}]
        self.assertEqual(self.problem(data)[0], 0)

    def test_a_restart_event_makes_an_older_review_copy_stale_even_without_a_returned_event(self):
        for restart in ("invalidated", "dispatched", "retried"):
            with self.subTest(restart=restart):
                data = self.graph(at=6, started=5.5)
                data["events"] = [{"time": 5, "node": "n1", "event": "returned", "note": "ok"},
                                  {"time": 7, "node": "n1", "event": restart, "note": "rework"}]  # passed again with no new `returned`
                result, output = self.problem(data)
                self.assertEqual(result, 1, output)
                self.assertIn("was taken before the node's last returned, attempt or restart", output)
                data["nodes"][0]["review_copy"].update(started=7, at=8)
                self.assertEqual(self.problem(data)[0], 0)

    def test_the_copy_counts_from_when_it_was_taken_not_when_the_run_ended(self):
        data = self.graph(at=9, started=4.5)  # copied before the node returned at 5, finished after it
        result, output = self.problem(data)
        self.assertEqual(result, 1, output)
        self.assertIn("was taken before the node's last returned, attempt or restart", output)
        data["nodes"][0]["review_copy"]["started"] = 5
        self.assertEqual(self.problem(data)[0], 0)
        data["nodes"][0]["review_copy"]["started"] = "x"
        result, output = self.problem(data)
        self.assertEqual(result, 1, output)
        self.assertIn("review_copy started must be a finite number", output)

    def test_a_passed_node_with_no_events_still_needs_a_matching_result_but_its_age_cannot_be_told(self):
        data = self.graph(at=1, started=1)
        data["events"] = []
        self.assertEqual(self.problem(data)[0], 0)
        data["nodes"][0]["review_copy"]["e2e_exit"] = 1
        self.assertEqual(self.problem(data)[0], 1)
        data["nodes"][0].pop("review_copy")
        result, output = self.problem(data)
        self.assertEqual(result, 1, output)
        self.assertIn("has no review copy", output)

    def test_an_e2e_added_or_put_back_after_dispatch_needs_an_amendment(self):
        data = self.graph()
        data["status"] = "running"
        data["nodes"][0].update(status="running", evidence="", session="ses_x", changed_paths=[])
        data["nodes"][0].pop("review_copy")
        data["nodes"][0].pop("e2e")  # dispatched with none
        (self.directory / ".history").mkdir()
        self.write_graph(data)
        self.assertEqual(self.run_check()[0], 0)
        data["nodes"][0]["e2e"] = self.E2E
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("n1 e2e changed without an amendment", output)
        data["revision"], data["amendments"] = 2, ["n1 e2e: added (approved)"]
        self.write_graph(data)
        self.assertEqual(self.run_check()[0], 0)
        data["nodes"][0].pop("e2e")  # dropped (amended), then put back unamended: the old review copy must not come back with it
        data["revision"], data["amendments"] = 3, data["amendments"] + ["n1 e2e: dropped (approved)"]
        self.write_graph(data)
        self.assertEqual(self.run_check()[0], 0)
        data["nodes"][0]["e2e"] = self.E2E
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("n1 e2e changed without an amendment", output)

    def test_the_rule_does_not_hold_a_node_that_is_not_passed(self):
        data = self.graph(review=False)
        data["nodes"][0]["status"] = "returned"
        data["status"] = "running"
        self.assertEqual(self.problem(data)[0], 0)

    def test_an_e2e_changed_after_dispatch_needs_an_amendment(self):
        data = self.graph()
        data["status"] = "running"
        data["nodes"][0].update(status="running", evidence="", session="ses_x", changed_paths=[])
        data["nodes"][0].pop("review_copy")
        (self.directory / ".history").mkdir()
        self.write_graph(data)
        self.assertEqual(self.run_check()[0], 0)  # saves the history
        data["nodes"][0]["e2e"] = "true"
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 1, output)
        self.assertIn("n1 e2e changed without an amendment", output)
        data["revision"] = 2
        data["amendments"] = ["n1 e2e: the old command did not start the program (approved by the orchestrator)"]
        self.write_graph(data)
        result, output = self.run_check()
        self.assertEqual(result, 0, output)

    def test_an_e2e_may_be_set_before_the_node_is_dispatched(self):
        data = self.graph(review=False)
        data["status"] = "running"
        data["nodes"][0].update(status="pending", evidence="", changed_paths=[])
        (self.directory / ".history").mkdir()
        self.write_graph(data)
        self.assertEqual(self.run_check()[0], 0)
        data["nodes"][0]["e2e"] = "true"
        self.write_graph(data)
        self.assertEqual(self.run_check()[0], 0)


if __name__ == "__main__":
    unittest.main()
