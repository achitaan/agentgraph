"""Drawn loops: the `checked_by` setting, `agentgraph structure`, the check rule and the endpoint."""
import contextlib
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import check, server, snapshot_json, structure
from agentgraph.demo import demo_snapshot

ROOT = Path(__file__).resolve().parent.parent

FILES = {
    "00-hub/orchestrator.md": "The hub.\n",
    "10-council/council.md": "seats: councillor-\n\nThe council lead.\n",
    "10-council/councillor-a.md": "A seat.\n",
    "20-build/impl-openai.md": "pool: 3\nescalates_to: impl-claude\n\nThe default implementer.\n",
    "20-build/impl-claude.md": "escalates_to: impl-hard\n\nThe bigger implementer.\n",
    "20-build/designer.md": "Designs things.\nSecond line: with a colon.\n",
    "30-verify/reviewer.md": "Reviews a finished change.\n",
    "30-verify/verifier.md": "checks: all\n\nChecks one loop item.\n",
    "30-verify/empty.md": "",
}


def make_structure(root: Path, extra: dict | None = None) -> Path:
    for rel, text in {**FILES, **(extra or {})}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text.encode("utf-8"))
    return root


def tree(root: Path) -> dict:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class Loader(unittest.TestCase):
    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())))

    def test_checked_by_becomes_drawn_loops(self):
        (self.root / "20-build/impl-openai.md").write_text(
            "checked_by: reviewer, `verifier`\npool: 3\n\nThe default implementer.\n")
        (self.root / "20-build/designer.md").write_text("checked_by: reviewer\n\nDesigns things.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual(shape["loops"], [("designer", "reviewer"), ("impl-openai", "reviewer"), ("impl-openai", "verifier")])
        self.assertEqual(shape["pool"]["impl-openai"], 3, "a checked_by line ends the other settings")
        self.assertEqual(shape["about"]["impl-openai"], "The default implementer.")
        self.assertEqual(shape["warnings"], [])

    def test_unknown_and_forbidden_names_warn_and_never_fail(self):
        (self.root / "20-build/impl-openai.md").write_text(
            "checked_by: ghost, impl-openai, orchestrator, councillor-a, reviewer\n\nText.\n")
        (self.root / "00-hub/orchestrator.md").write_text("checked_by: reviewer\n\nThe hub.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual(shape["loops"], [("impl-openai", "reviewer")])
        warnings = " | ".join(shape["warnings"])
        for name in ("ghost", "impl-openai checked_by impl-openai", "orchestrator is the hub", "councillor-a is a council seat",
                     "checked_by in orchestrator ignored"):
            self.assertIn(name, warnings)

    def test_snapshot_carries_drawn_and_default_loops(self):
        (self.root / "20-build/impl-openai.md").write_text("checked_by: reviewer, ghost\npool: 3\n\nText.\n")
        (self.root / "30-verify/reviewer.md").write_text("checks: all\n\nReviews.\n")  # the demo has no verifier
        doc = snapshot_json.snapshot_document(demo_snapshot(1000), "demo", structure=snapshot_json.load_structure(self.root))
        loops = doc["structure"]["loops"]
        self.assertEqual(doc["structure"]["source"], "folder")
        self.assertIn({"work": "impl-openai", "check": "reviewer", "source": "drawn"}, loops)
        self.assertEqual([loop for loop in loops if loop["source"] == "drawn"], [{"work": "impl-openai", "check": "reviewer", "source": "drawn"}])
        defaults = [loop for loop in loops if loop["source"] == "default"]
        self.assertTrue(defaults and all(loop["check"] == "reviewer" for loop in defaults), defaults)
        self.assertIn({"work": "impl-claude", "check": "reviewer", "source": "default"}, defaults)
        self.assertFalse({loop["work"] for loop in defaults} & {"orchestrator", "reviewer"} or any(loop["work"].startswith("councillor-") for loop in defaults))
        self.assertTrue(any("ghost" in warning for warning in doc["warnings"]))

    def test_builtin_snapshot_has_no_drawn_loops(self):
        doc = snapshot_json.snapshot_document(demo_snapshot(1000), "demo")
        self.assertEqual(doc["structure"]["source"], "builtin")
        self.assertTrue(all(loop["source"] == "default" for loop in doc["structure"]["loops"]))


class Command(unittest.TestCase):
    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.before = tree(self.root)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = structure.main([*args, "--structure", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def test_link_unlink_round_trip_keeps_every_byte(self):
        for work, check_ in (("impl-openai", "reviewer"), ("designer", "reviewer"), ("reviewer", "impl-claude"),
                             ("empty", "reviewer"), ("council", "reviewer")):
            with self.subTest(work=work):
                original = self.before
                self.assertEqual(self.run_cli("link", work, check_)[0], 0)
                self.assertIn((work, check_), snapshot_json.load_structure(self.root)["loops"])
                self.assertEqual(self.run_cli("unlink", work, check_)[0], 0)
                self.assertEqual(tree(self.root), original)

    def test_link_edits_only_the_checked_by_line(self):
        path = self.root / "20-build/impl-openai.md"
        self.run_cli("link", "impl-openai", "reviewer")
        self.assertEqual(path.read_text(), "checked_by: reviewer\npool: 3\nescalates_to: impl-claude\n\nThe default implementer.\n")
        self.run_cli("link", "impl-openai", "verifier")
        self.assertEqual(path.read_text().splitlines()[0], "checked_by: reviewer, verifier")
        self.run_cli("unlink", "impl-openai", "reviewer")
        self.assertEqual(path.read_text(), "checked_by: verifier\npool: 3\nescalates_to: impl-claude\n\nThe default implementer.\n")
        designer = self.root / "20-build/designer.md"
        self.run_cli("link", "designer", "reviewer")
        self.assertEqual(designer.read_text(), "checked_by: reviewer\n\nDesigns things.\nSecond line: with a colon.\n")
        others = {rel: data for rel, data in tree(self.root).items() if rel not in ("20-build/impl-openai.md", "20-build/designer.md")}
        self.assertEqual(others, {rel: data for rel, data in self.before.items() if rel in others})

    def test_crlf_files_keep_their_line_endings(self):
        path = self.root / "20-build/impl-claude.md"
        path.write_bytes(b"escalates_to: impl-hard\r\n\r\nText.\r\n")
        self.run_cli("link", "impl-claude", "reviewer")
        self.assertEqual(path.read_bytes(), b"checked_by: reviewer\r\nescalates_to: impl-hard\r\n\r\nText.\r\n")
        self.run_cli("unlink", "impl-claude", "reviewer")
        self.assertEqual(path.read_bytes(), b"escalates_to: impl-hard\r\n\r\nText.\r\n")

    def test_idempotent(self):
        self.assertEqual(self.run_cli("link", "impl-openai", "reviewer")[1], "linked: reviewer checks impl-openai\n")
        once = tree(self.root)
        self.assertEqual(self.run_cli("link", "impl-openai", "reviewer")[1], "already linked: reviewer checks impl-openai\n")
        self.assertEqual(tree(self.root), once)
        self.run_cli("unlink", "impl-openai", "reviewer")
        self.assertEqual(self.run_cli("unlink", "impl-openai", "reviewer")[1], "not linked: reviewer checks impl-openai\n")
        self.assertEqual(tree(self.root), self.before)

    def test_refusals(self):
        cases = {
            ("ghost", "reviewer"): "unknown agent ghost",
            ("impl-openai", "ghost"): "unknown agent ghost",
            ("impl-openai", "impl-openai"): "cannot check its own work",
            ("orchestrator", "reviewer"): "orchestrator is the hub",
            ("impl-openai", "orchestrator"): "orchestrator is the hub",
            ("councillor-a", "reviewer"): "councillor-a is a council seat",
            ("impl-openai", "councillor-a"): "councillor-a is a council seat",
        }
        for (work, check_), message in cases.items():
            for verb in ("link", "unlink"):
                with self.subTest(verb=verb, work=work, check=check_):
                    code, out, err = self.run_cli(verb, work, check_)
                    self.assertEqual(code, 1)
                    self.assertIn(message, err)
                    self.assertEqual(out, "")
        code, out, _ = self.run_cli("link", "ghost", "reviewer", "--json")
        self.assertEqual(code, 1)
        self.assertIn("unknown agent ghost", json.loads(out)["error"])
        self.assertEqual(tree(self.root), self.before)
        with tempfile.TemporaryDirectory() as empty, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(structure.main(["loops", "--structure", empty]), 1)
        self.assertIn("no structure folder", err.getvalue())

    def test_loops_lists_the_drawn_loops(self):
        self.assertEqual(self.run_cli("loops")[1], "no drawn loops\n")
        self.run_cli("link", "impl-openai", "reviewer")
        self.assertEqual(json.loads(self.run_cli("loops", "--json")[1]),
                         {"loops": [{"work": "impl-openai", "check": "reviewer", "source": "drawn"}]})
        self.assertEqual(self.run_cli("loops")[1], "reviewer checks impl-openai\n")
        code, out, _ = self.run_cli("link", "designer", "reviewer", "--json")
        self.assertEqual((code, len(json.loads(out)["loops"])), (0, 2))

    def test_write_is_atomic_and_keeps_the_mode(self):
        path = self.root / "20-build/impl-openai.md"
        os.chmod(path, 0o640)
        inode = path.stat().st_ino
        replaced = []
        real = os.replace
        with patch.object(structure.os, "replace", side_effect=lambda a, b: (replaced.append((Path(a).parent, Path(b))), real(a, b))):
            self.run_cli("link", "impl-openai", "reviewer")
        self.assertEqual(replaced, [(path.parent, path)], "the file is not replaced by a rename next to it")
        self.assertNotEqual(path.stat().st_ino, inode)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), sorted(["designer.md", "impl-claude.md", "impl-openai.md"]))
        # A failed write leaves the file as it was and no temporary file behind.
        with patch.object(structure.os, "replace", side_effect=OSError("disk full")):
            code, _, err = self.run_cli("link", "impl-openai", "verifier")
        self.assertEqual(code, 1)
        self.assertIn("disk full", err)
        self.assertEqual(path.read_text().splitlines()[0], "checked_by: reviewer")
        self.assertEqual(len(list(path.parent.iterdir())), 3)

    def assert_refused(self, rel, message, work=None, check_="reviewer"):
        before = tree(self.root)
        for verb in ("link", "unlink"):
            code, out, err = self.run_cli(verb, work or Path(rel).stem, check_)
            self.assertEqual(code, 1, (verb, out))
            self.assertIn(message, err)
            self.assertIn("unchanged", err)
        self.assertEqual(tree(self.root), before)

    def test_a_symlinked_agent_file_is_refused(self):
        target = self.root / "outside.txt"
        target.write_text("pool: 1\n\nElsewhere.\n")
        link = self.root / "20-build/impl-openai.md"
        link.unlink()
        link.symlink_to(target)
        for verb in ("link", "unlink"):
            code, _, err = self.run_cli(verb, "impl-openai", "reviewer")
            self.assertEqual(code, 1)
            self.assertIn("not a regular file", err)
        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(), "pool: 1\n\nElsewhere.\n")

    def test_a_file_that_is_not_utf8_is_refused(self):
        (self.root / "20-build/designer.md").write_bytes(b"pool: 2\n\nCaf\xe9 designs.\n")
        self.assert_refused("20-build/designer.md", "not UTF-8")

    def test_line_breaks_the_editor_does_not_split_on_are_refused(self):
        # str.splitlines (the graph's reader) splits on these, the editor only on \n and \r\n.
        for data in (b"pool: 3\rchecked_by: verifier\r\rText.\r", "pool: 3\u2028checked_by: verifier\n\nText.\n".encode(),
                     b"pool: 3\n\nText.\x0cMore.\n"):
            with self.subTest(data=data):
                (self.root / "20-build/impl-openai.md").write_bytes(data)
                self.assert_refused("20-build/impl-openai.md", "line break other than")

    def test_settings_the_reader_would_see_cut_off_are_refused(self):
        # The graph reads only the first 4096 bytes: a checked_by line running past them reads differently.
        path = self.root / "20-build/impl-openai.md"
        path.write_text("checked_by: reviewer, " + "x" * 4100 + "\n\nText.\n")
        self.assert_refused("20-build/impl-openai.md", "cannot be edited safely", check_="verifier")

    def test_checked_by_written_twice(self):
        path = self.root / "20-build/impl-openai.md"
        path.write_text("checked_by: reviewer\npool: 3\nchecked_by: verifier, reviewer\n\nText.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual([loop for loop in shape["loops"] if loop[0] == "impl-openai"], [("impl-openai", "reviewer"), ("impl-openai", "verifier")])
        self.assertEqual(self.run_cli("link", "impl-openai", "verifier")[1], "already linked: verifier checks impl-openai\n")
        self.assertEqual(path.read_text(), "checked_by: reviewer\npool: 3\nchecked_by: verifier, reviewer\n\nText.\n")
        self.run_cli("unlink", "impl-openai", "reviewer")
        self.assertEqual(path.read_text(), "checked_by: verifier\npool: 3\n\nText.\n")
        self.run_cli("unlink", "impl-openai", "verifier")
        self.assertEqual(path.read_text(), "pool: 3\n\nText.\n")

    def test_checked_by_after_the_description_is_description_text(self):
        path = self.root / "20-build/designer.md"
        path.write_text("Designs things.\nchecked_by: reviewer\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertNotIn(("designer", "reviewer"), shape["loops"])
        self.assertEqual(shape["about"]["designer"], "Designs things. checked_by: reviewer")
        self.assertEqual(self.run_cli("unlink", "designer", "reviewer")[1], "not linked: reviewer checks designer\n")
        self.run_cli("link", "designer", "reviewer")
        self.assertEqual(path.read_text(), "checked_by: reviewer\n\nDesigns things.\nchecked_by: reviewer\n")
        self.assertIn(("designer", "reviewer"), snapshot_json.load_structure(self.root)["loops"])
        self.run_cli("unlink", "designer", "reviewer")
        self.assertEqual(path.read_text(), "Designs things.\nchecked_by: reviewer\n")

    def test_a_file_with_no_settings_and_leading_blank_lines(self):
        path = self.root / "30-verify/reviewer.md"
        path.write_text("\n\nReviews a finished change.\n")
        self.run_cli("link", "reviewer", "impl-claude")
        self.assertEqual(path.read_text(), "\n\nchecked_by: impl-claude\n\nReviews a finished change.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertIn(("reviewer", "impl-claude"), shape["loops"])
        self.assertEqual(shape["about"]["reviewer"], "Reviews a finished change.")
        self.run_cli("unlink", "reviewer", "impl-claude")
        self.assertEqual(path.read_text(), "\n\nReviews a finished change.\n")

    def test_concurrent_link_and_unlink_on_one_file_apply_both(self):
        path = self.root / "20-build/impl-openai.md"
        env = {**os.environ, "PYTHONPATH": str(ROOT)}

        def together(*commands):
            running = [subprocess.Popen([sys.executable, "-P", "-m", "agentgraph.structure", *command, "--structure", str(self.root)],
                                        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for command in commands]
            for process in running:
                _, err = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, err)

        for _ in range(3):
            together(("link", "impl-openai", "reviewer"), ("link", "impl-openai", "verifier"))
            self.assertEqual(sorted(snapshot_json.checked_by_names(snapshot_json.read_settings(path.read_bytes())[0])), ["reviewer", "verifier"])
            together(("unlink", "impl-openai", "reviewer"), ("link", "impl-openai", "impl-claude"))
            self.assertEqual(sorted(snapshot_json.checked_by_names(snapshot_json.read_settings(path.read_bytes())[0])), ["impl-claude", "verifier"])
            self.assertIn(path.read_text().split("\n")[1:], (["pool: 3", "escalates_to: impl-claude", "", "The default implementer.", ""],))
            together(("unlink", "impl-openai", "verifier"), ("unlink", "impl-openai", "impl-claude"))
            self.assertEqual(tree(self.root), self.before)
        threads = [threading.Thread(target=structure.set_loop, args=("impl-openai", name, True, self.root)) for name in ("reviewer", "verifier", "designer")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(name for work, name in snapshot_json.load_structure(self.root)["loops"] if work == "impl-openai"),
                         ["designer", "reviewer", "verifier"])

    def test_shell_entry_point_round_trip_never_runs_git(self):
        env = dict(os.environ)
        script = ROOT / "bin" / "agentgraph"
        for args in (("link", "impl-openai", "reviewer"), ("loops", "--json"), ("unlink", "impl-openai", "reviewer")):
            result = subprocess.run(["sh", str(script), "structure", *args, "--structure", str(self.root)],
                                    capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            if args[0] == "loops":
                self.assertEqual(json.loads(result.stdout)["loops"], [{"work": "impl-openai", "check": "reviewer", "source": "drawn"}])
        self.assertEqual(tree(self.root), self.before)
        self.assertNotIn("git", (ROOT / "agentgraph" / "structure.py").read_text().replace("agentgraph", ""))


def graph(nodes):
    return {"task": "loops", "goal": "Honour drawn loops", "status": "running", "created": 1, "updated": 2, "revision": 1,
            "objectives": [{"id": "o1", "text": "Deliver", "nodes": [nodes[0]["id"]]}], "nodes": nodes}


def node(id_, type_, agent, status="running", **extra):
    return {"id": id_, "type": type_, "agent": agent, "goal": id_, "status": status, "depends_on": [],
            "scope": [], "done_when": "tests pass", **extra}


class Enforcement(unittest.TestCase):
    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.graphs = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def run_check(self, nodes):
        (self.graphs / "loops.json").write_text(json.dumps(graph(nodes)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(self.graphs), "--structure", str(self.root)])
        return code, out.getvalue()

    def test_missing_gate_fails(self):
        structure.set_loop("impl-openai", "reviewer", True, self.root)
        code, out = self.run_check([node("n12", "implement", "impl-openai")])
        self.assertEqual(code, 1)
        self.assertIn("n12 (impl-openai) has no check by reviewer: the structure says reviewer checks impl-openai", out)

    def test_present_gate_passes(self):
        structure.set_loop("impl-openai", "reviewer", True, self.root)
        structure.set_loop("designer", "reviewer", True, self.root)
        code, out = self.run_check([node("n12", "implement", "impl-openai"), node("d1", "design", "designer"),
                                    node("g1", "gate", "reviewer", status="pending", depends_on=["n12", "d1"], feedback_to=["n12", "d1"])])
        self.assertEqual(code, 0, out)

    def test_a_gate_by_another_agent_or_for_another_node_does_not_count(self):
        structure.set_loop("impl-openai", "reviewer", True, self.root)
        code, out = self.run_check([node("n1", "implement", "impl-openai"), node("n2", "implement", "impl-openai"),
                                    node("g1", "gate", "verifier", status="pending", depends_on=["n1"], feedback_to=["n1"]),
                                    node("g2", "gate", "reviewer", status="pending", depends_on=["n2"], feedback_to=["n2"])])
        self.assertEqual(code, 1)
        self.assertIn("n1 (impl-openai) has no check by reviewer", out)
        self.assertNotIn("n2 (impl-openai) has no check", out)

    def test_passed_nodes_are_exempt(self):
        structure.set_loop("impl-openai", "reviewer", True, self.root)
        code, out = self.run_check([node("n12", "implement", "impl-openai", status="passed", evidence="ok")])
        self.assertEqual(code, 0, out)

    def test_returned_failed_and_blocked_nodes_need_the_gate(self):
        loops = [("impl-openai", "reviewer")]
        nodes = [node(f"n-{status}", "implement", "impl-openai", status=status) for status in ("pending", "running", "returned", "failed", "blocked", "passed")]
        problems = check.loop_problems(check.graph_from_raw(graph(nodes), self.graphs / "loops.json"), loops)
        self.assertEqual(problems, [f"n-{status} (impl-openai) has no check by reviewer: the structure says reviewer checks impl-openai"
                                    for status in ("pending", "running", "returned", "failed", "blocked")])

    def test_one_gate_for_nodes_of_several_agents_satisfies_each(self):
        structure.set_loop("impl-openai", "reviewer", True, self.root)
        structure.set_loop("impl-claude", "reviewer", True, self.root)
        structure.set_loop("designer", "reviewer", True, self.root)
        nodes = [node("a", "implement", "impl-openai"), node("b", "implement", "impl-claude"), node("c", "design", "designer"),
                 node("g", "gate", "reviewer", status="pending", depends_on=["a", "b", "c"], feedback_to=["a", "b", "c"])]
        code, out = self.run_check(nodes)
        self.assertEqual(code, 0, out)
        nodes[-1]["feedback_to"] = ["a", "c"]
        code, out = self.run_check(nodes)
        self.assertEqual(code, 1)
        self.assertIn("b (impl-claude) has no check by reviewer", out)
        self.assertNotIn("a (impl-openai) has no check", out)

    def test_a_named_structure_folder_must_exist_but_a_missing_default_is_silent(self):
        (self.graphs / "loops.json").write_text(json.dumps(graph([node("n12", "implement", "impl-openai")])))
        for missing in (self.root / "mistyped", self.graphs):
            with self.subTest(folder=missing):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = check.main(["--dir", str(self.graphs), "--structure", str(missing)])
                self.assertEqual(code, 2)
                self.assertIn(f"structure folder {missing} does not exist", out.getvalue())
        out = io.StringIO()
        with patch.object(snapshot_json, "STRUCTURE_DIR", self.root / "absent"), contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(self.graphs)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertNotIn("structure", out.getvalue())

    def test_no_drawn_loops_changes_nothing(self):
        nodes = [node("n12", "implement", "impl-openai"), node("c1", "check", "verifier", status="pending", depends_on=["n12"])]
        code, out = self.run_check(nodes)
        self.assertEqual(code, 0, out)
        self.assertEqual(check.loop_problems(check.graph_from_raw(graph(nodes), self.graphs / "loops.json"), []), [])


class Endpoint(unittest.TestCase):
    def setUp(self):
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = make_structure(temporary / "structure")
        self.before = tree(self.root)
        self.enterContext(patch.object(server, "build_snapshot", side_effect=AssertionError("unexpected live read")))
        self.httpd = server.make_server(0, graph_dir=temporary / "graphs", cwd=temporary, session="root",
                                        web_dir=temporary, structure_dir=self.root)
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(self.httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.httpd.shutdown)
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def post(self, body, headers=None, method="POST", path="/api/structure/loop"):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=2)
        try:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            sent = {"Content-Type": "application/json", "Origin": self.origin, **(headers or {})}
            conn.request(method, path, body=data if method == "POST" else None,
                         headers={key: value for key, value in sent.items() if value is not None})
            response = conn.getresponse()
            raw = response.read()
            self.assertFalse([name for name in response.headers if name.lower().startswith("access-control-")])
            try:
                return response.status, json.loads(raw) if raw else None, response
            except ValueError:
                return response.status, raw, response
        finally:
            conn.close()

    def test_success_draws_and_removes(self):
        status, body, _ = self.post({"work": "impl-openai", "check": "reviewer", "on": True})
        self.assertEqual((status, body), (200, {"loops": [{"work": "impl-openai", "check": "reviewer", "source": "drawn"}]}))
        self.assertEqual(self.post({"work": "impl-openai", "check": "reviewer", "on": True})[0], 200)
        status, body, _ = self.post({"work": "impl-openai", "check": "reviewer", "on": False}, {"Origin": None})
        self.assertEqual((status, body), (200, {"loops": []}))
        self.assertEqual(tree(self.root), self.before)

    def test_wrong_method(self):
        for method in ("GET", "PUT", "DELETE"):
            with self.subTest(method=method):
                status, _, response = self.post({}, method=method)
                self.assertEqual(status, 405)
                self.assertEqual(response.getheader("Allow"), "POST")
        status, _, response = self.post({"work": "impl-openai", "check": "reviewer", "on": True}, path="/api/snapshot")
        self.assertEqual(status, 405)
        self.assertEqual(tree(self.root), self.before)

    def test_options_and_head_are_refused_without_cors(self):
        for method in ("OPTIONS", "HEAD"):
            with self.subTest(method=method):
                status, _, response = self.post({}, {"Access-Control-Request-Method": "POST"}, method=method)
                self.assertEqual(status, 405)
                self.assertEqual(response.getheader("Allow"), "POST")
        self.assertEqual(tree(self.root), self.before)

    def raw_post(self, length_header, body=b'{"work": "impl-openai", "check": "reviewer", "on": true}'):
        port = self.httpd.server_port
        head = (f"POST /api/structure/loop HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: {self.origin}\r\n"
                f"Content-Type: application/json\r\n{length_header}\r\nConnection: close\r\n\r\n").encode()
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(head + body)
            reply = b""
            while chunk := client.recv(65536):
                reply += chunk
        status = int(reply.split(b" ", 2)[1])
        headers, _, payload = reply.partition(b"\r\n\r\n")
        self.assertNotIn(b"access-control-", headers.lower())
        return status, json.loads(payload)

    def test_chunked_negative_and_non_numeric_lengths_are_refused(self):
        body = b'{"work": "impl-openai", "check": "reviewer", "on": true}'
        chunked = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        for header, data, expected in (("Transfer-Encoding: chunked", chunked, 411), ("Content-Length: -5", body, 413),
                                       ("Content-Length: ten", body, 411), ("Content-Length: 1e3", body, 411)):
            with self.subTest(header=header):
                status, reply = self.raw_post(header, data)
                self.assertEqual(status, expected)
                self.assertIn("error", reply)
        self.assertEqual(tree(self.root), self.before)

    def test_the_snapshot_shows_the_folder_the_server_writes(self):
        self.post({"work": "impl-openai", "check": "reviewer", "on": True})
        with patch.object(server, "build_snapshot", return_value=demo_snapshot(1000)):
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
            conn.request("GET", "/api/snapshot")
            document = json.loads(conn.getresponse().read())
            conn.close()
        self.assertEqual(document["structure"]["source"], "folder")
        self.assertEqual([loop for loop in document["structure"]["loops"] if loop["source"] == "drawn"],
                         [{"work": "impl-openai", "check": "reviewer", "source": "drawn"}])
        self.post({"work": "impl-openai", "check": "reviewer", "on": False})
        self.assertEqual(tree(self.root), self.before)

    def test_non_loopback_client(self):
        with patch.object(server, "_LOOPBACK", frozenset()):
            status, body, _ = self.post({"work": "impl-openai", "check": "reviewer", "on": True})
        self.assertEqual(status, 403)
        self.assertIn("local", body["error"])
        self.assertEqual(tree(self.root), self.before)

    def test_foreign_origin(self):
        port = self.httpd.server_port
        for origin in ("http://evil.example", f"http://localhost:{port}", f"https://127.0.0.1:{port}", "null", f"http://127.0.0.1:{port + 1}"):
            with self.subTest(origin=origin):
                status, body, _ = self.post({"work": "impl-openai", "check": "reviewer", "on": True}, {"Origin": origin})
                self.assertEqual(status, 403)
                self.assertIn("error", body)
        status, _, _ = self.post({"work": "impl-openai", "check": "reviewer", "on": True}, {"Host": "evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(tree(self.root), self.before)

    def test_non_json(self):
        for headers, body in (({"Content-Type": "text/plain"}, b'{"work": "impl-openai", "check": "reviewer", "on": true}'),
                              ({"Content-Type": "application/x-www-form-urlencoded"}, b"work=impl-openai"),
                              ({"Content-Type": None}, b'{"work": "impl-openai", "check": "reviewer", "on": true}'),
                              ({}, b"not json"), ({}, b'["impl-openai", "reviewer"]'),
                              ({}, b'{"work": "impl-openai", "check": "reviewer", "on": "yes"}'), ({}, b"x" * 5000)):
            with self.subTest(headers=headers, body=body[:40]):
                status, reply, _ = self.post(body, headers)
                self.assertIn(status, (400, 413, 415))
                self.assertIn("error", reply)
        self.assertEqual(tree(self.root), self.before)

    def test_bad_names(self):
        for work, check_, message in (("ghost", "reviewer", "unknown agent"), ("impl-openai", "impl-openai", "own work"),
                                      ("orchestrator", "reviewer", "hub"), ("impl-openai", "councillor-a", "seat"),
                                      ("../00-hub/orchestrator", "reviewer", "unknown agent")):
            with self.subTest(work=work, check=check_):
                status, body, _ = self.post({"work": work, "check": check_, "on": True})
                self.assertEqual(status, 400)
                self.assertIn(message, body["error"])
        self.assertEqual(tree(self.root), self.before)

    def test_demo_server_refuses(self):
        demo = server.make_server(0, demo=True, structure_dir=self.root)
        thread = threading.Thread(target=demo.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", demo.server_port, timeout=2)
            conn.request("POST", "/api/structure/loop", body=b'{"work": "impl-openai", "check": "reviewer", "on": true}',
                         headers={"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 403)
            conn.close()
        finally:
            demo.shutdown()
            demo.server_close()
            thread.join(2)
        self.assertEqual(tree(self.root), self.before)


if __name__ == "__main__":
    unittest.main()
