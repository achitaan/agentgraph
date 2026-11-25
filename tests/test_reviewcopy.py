"""agentgraph review-copy: the node's own check and e2e in a throwaway copy of the project, in temp projects. No model runs."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from agentgraph import __main__ as entry
from agentgraph import check, reviewcopy

TASK = "demo"
HAVE_GIT = shutil.which("git") is not None


def graph(**node):
    data = {
        "task": TASK, "goal": "Review a copy", "status": "running", "created": 1000, "updated": 1001, "revision": 1,
        "objectives": [{"id": "o1", "text": "Deliver", "nodes": ["n1"]}], "amendments": [],
        "nodes": [{"id": "n1", "type": "implement", "agent": "impl-openai", "goal": "Make it", "status": "returned", "depends_on": [],
                   "scope": ["src"], "done_when": "it runs", "attempts": 1, "session": "ses_x", "started": 1002, "evidence": "ok",
                   "changed_paths": []}],
        "events": [{"time": 1000, "node": "", "event": "declared", "note": "1 node"},
                   {"time": 1002, "node": "n1", "event": "dispatched", "note": "impl-openai"},
                   {"time": 1003, "node": "n1", "event": "returned", "note": "ok"}],
    }
    data["nodes"][0].update(node)
    return data


def tree_of(project: Path) -> dict:
    """Everything in the project but the review-copy bookkeeping: path -> (content, mtime)."""
    found = {}
    for path in sorted(project.rglob("*")):
        rel = path.relative_to(project)
        if path.is_file() and rel.parts[:2] != (".opencode", "artifacts") and rel.parts[:2] != (".opencode", "graph") and ".git" not in rel.parts:
            found[str(rel)] = (path.read_bytes(), path.stat().st_mtime_ns)
    return found


class Base(unittest.TestCase):
    use_git = True

    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        self.temp = self.base / "tmp"  # where the copy goes: TMPDIR, outside the project
        self.temp.mkdir()
        self.enterContext(mock.patch.dict(os.environ, {"TMPDIR": str(self.temp), "AGENTGRAPH_LOCKDIR": self.enterContext(tempfile.TemporaryDirectory())}))  # AGENTGRAPH_LOCKDIR: the portable file layer's lock folder, kept out of both
        self.enterContext(mock.patch.object(tempfile, "tempdir", None))
        self.graph_dir = self.project / ".opencode" / "graph"
        self.graph_dir.mkdir(parents=True)
        self.path = self.graph_dir / f"{TASK}.json"
        here = os.getcwd()
        os.chdir(self.project)  # review-copy only works for the current folder's project
        self.addCleanup(os.chdir, here)

    def files(self, **files):
        for rel, text in files.items():
            self.put(rel.replace("__", "/"), text)

    def put(self, rel, text="x"):
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def git_init(self, *commit):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        if commit:
            subprocess.run(["git", "add", *commit], cwd=self.project, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e.x", "commit", "-q", "-m", "x"], cwd=self.project, check=True)

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def read(self):
        return json.loads(self.path.read_text())

    def run_it(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.project), *extra])
        return code, out.getvalue().strip()

    def result(self):
        found = [(json.loads(p.read_text()), p) for p in (self.project / ".opencode" / "artifacts" / TASK / "n1").glob("review-copy-*.json")]
        return max(found, key=lambda pair: pair[0]["at"])

    def output_of(self, name="check"):
        text = self.result()[1].with_suffix(".txt").read_text()
        return text


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class TheCopy(Base):
    def test_a_git_copy_has_uncommitted_and_untracked_files_and_leaves_out_the_rest(self):
        self.files(tracked="v1\n", gone="x\n", **{".gitignore": "ignored.txt\nbuild/\n"})
        self.git_init("tracked", "gone", ".gitignore")
        self.files(tracked="v2 uncommitted\n", untracked="new\n", ignored_txt="")
        (self.project / "ignored.txt").write_text("secret")
        (self.project / "build").mkdir()
        (self.project / "build" / "out.o").write_text("o")
        (self.project / "gone").unlink()
        for rel in ("node_modules__pkg__a.js", ".opencode__artifacts__old__r.md", ".opencode__loops__run__x", ".opencode__handoff__h.md",
                    "venv__pyvenv.cfg", "venv__lib__x.py", "sub__.venv__y.py"):
            self.files(**{rel: "x"})
        self.write(graph(check="cat tracked untracked"))
        code, line = self.run_it("--keep")
        self.assertEqual(code, 0, line)
        copy = Path(line.rsplit("copy ", 1)[1])
        self.addCleanup(shutil.rmtree, copy.parent, True)
        self.assertEqual((copy / "tracked").read_text(), "v2 uncommitted\n")
        self.assertEqual((copy / "untracked").read_text(), "new\n")
        for rel in ("ignored.txt", "build", "gone", "node_modules", ".git", ".opencode/artifacts", ".opencode/loops", ".opencode/handoff",
                    "venv", "sub/.venv"):
            self.assertFalse((copy / rel).exists(), rel)
        self.assertTrue((copy / ".opencode" / "graph" / f"{TASK}.json").exists())  # the graph is part of the project
        self.assertIn("v2 uncommitted", self.output_of())
        self.assertTrue(str(copy).startswith(str(self.temp)), "the copy lives under TMPDIR")

    def test_the_copy_is_deleted_unless_kept(self):
        self.files(a="1")
        self.write(graph(check="true"))
        self.assertEqual(self.run_it()[0], 0)
        self.assertEqual(list(self.temp.iterdir()), [])
        code, line = self.run_it("--keep")
        self.assertEqual(code, 0)
        self.assertEqual(len(list(self.temp.iterdir())), 1)
        self.assertIn(", copy " + str(self.temp), line)

    def test_the_project_is_unchanged_after_commands_that_write_delete_and_walk_out(self):
        self.files(a="keep me\n", dir__b="and me\n")
        self.git_init("a", "dir")
        command = ('echo more >> a; rm -f dir/b; echo new > created.txt; mkdir -p deep/er; echo z > deep/er/f; '
                   'cd ..; echo up > escaped.txt; cd ..; echo up2 > escaped2.txt; rm -rf ../tmp/escaped2.txt')
        self.write(graph(check=command, e2e="cd .. && ls && cd copy && test -e a && test ! -e dir/b && echo walked"))
        before = tree_of(self.project)
        code, line = self.run_it()
        self.assertEqual(code, 0, line)
        self.assertEqual(tree_of(self.project), before)
        self.assertTrue(self.result()[0]["project_unchanged"])
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["project", "tmp"])  # nothing escaped next to the project either

    def test_a_write_by_absolute_path_is_detected_and_fails_the_run(self):
        self.files(a="1")
        self.git_init("a")
        self.write(graph(check=f"echo leaked > '{self.project}/leak.txt'; echo edited >> '{self.project}/a'; "
                               "echo relative > ../../../project/leak2.txt"))  # copy -> scratch -> TMPDIR -> the folder beside it
        code, line = self.run_it()
        self.assertEqual(code, 1, line)
        self.assertIn("PROJECT CHANGED (3 files: a, leak.txt, leak2.txt)", line)
        result, _ = self.result()
        self.assertFalse(result["project_unchanged"])
        self.assertEqual(result["project_changed_files"], ["a", "leak.txt", "leak2.txt"])
        self.assertEqual(result["commands"][0]["exit"], 0)  # the command itself passed: it is the change that fails the run
        self.assertIs(self.read()["nodes"][0]["review_copy"]["project_unchanged"], False)

    def test_a_timeout_kills_a_child_and_its_grandchild_and_e2e_still_runs(self):
        pidfile = self.base / "grandchild.pid"
        self.write(graph(check=f"sh -c 'sleep 300 & echo $! > {pidfile}; wait'; echo not reached > {self.base}/after",
                         e2e="echo e2e ran"))
        self.files(a="1")
        began = time.monotonic()
        code, line = self.run_it("--timeout", "1")
        self.assertLess(time.monotonic() - began, 30)
        self.assertEqual(code, 1, line)
        self.assertIn("check timed out (1s)", line)
        self.assertIn("e2e exit 0", line)
        pid = int(pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the grandchild survived the timeout")
        self.assertFalse((self.base / "after").exists())
        result, _ = self.result()
        self.assertEqual([c["timed_out"] for c in result["commands"]], [True, False])
        self.assertIsNone(result["commands"][0]["exit"])
        self.assertIsNone(self.read()["nodes"][0]["review_copy"]["check_exit"])

    def test_a_command_that_leaves_a_process_behind_does_not_hold_the_run_up(self):
        pidfile = self.base / "bg.pid"
        self.files(a="1")
        self.write(graph(check=f"sleep 300 >/dev/null 2>&1 & echo $! > {pidfile}"))
        began = time.monotonic()
        code, line = self.run_it()
        self.assertLess(time.monotonic() - began, 30)
        self.assertEqual(code, 0, line)
        pid = int(pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the leftover process survived")

    def test_e2e_runs_when_check_fails_and_the_run_exits_1(self):
        self.files(a="1")
        self.write(graph(check="echo broken; exit 3", e2e="echo started"))
        code, line = self.run_it()
        self.assertEqual(code, 1, line)
        self.assertRegex(line, r"^review-copy demo/n1: check exit 3 \(\d+s\), e2e exit 0 \(\d+s\), result \.opencode/artifacts/demo/n1/review-copy-")
        result, _ = self.result()
        self.assertEqual([(c["name"], c["exit"]) for c in result["commands"]], [("check", 3), ("e2e", 0)])
        text = self.output_of()
        self.assertIn("broken", text)
        self.assertIn("started", text)

    def test_only_the_commands_the_node_has_run(self):
        self.files(a="1")
        self.write(graph(e2e="exit 4"))
        code, line = self.run_it()
        self.assertEqual(code, 1)
        self.assertNotIn("check", line)
        self.assertIn("e2e exit 4", line)
        record = self.read()["nodes"][0]["review_copy"]
        self.assertIsNone(record["check_exit"])
        self.assertEqual(record["e2e_exit"], 4)
        self.assertNotIn("check_sha256", record)


class TheEnvironment(Base):
    def test_secrets_are_not_in_the_environment_of_the_commands(self):
        self.files(a="1")
        secrets = {"MY_API_KEY": "s1", "GITHUB_TOKEN": "s2", "DB_PASSWORD": "s3", "AWS_SECRET_ACCESS": "s4", "GCP_CREDENTIALS": "s5",
                   "OPENCODE_SESSION_ID": "s6", "opencode_lower": "s7", "OPENAI_api_key": "s8", "LC_ALL_KEY": "s9", "PATH_TOKEN": "s10"}
        with mock.patch.dict(os.environ, {**secrets, "EDITOR": "vi", "LC_CTYPE": "C", "LANG": "C"}):
            self.write(graph(check="env", e2e="echo \"TMPDIR=$TMPDIR\""))
            code, line = self.run_it()
        self.assertEqual(code, 0, line)
        text = self.output_of()
        for name, value in secrets.items():
            self.assertNotIn(name + "=", text)
            self.assertNotIn(value + "\n", text)
        for kept in ("PATH=", "HOME=", "LANG=C", "LC_CTYPE=C", "TMPDIR="):
            self.assertIn(kept, text)
        self.assertNotIn("EDITOR=", text)
        self.assertIn(f"TMPDIR={self.temp}", text)  # a private folder below the temp folder, not the project
        self.assertEqual(self.result()[0]["environment"], sorted(self.result()[0]["environment"]))
        self.assertFalse(any("KEY" in name or "TOKEN" in name for name in self.result()[0]["environment"]))

    def test_reduced_environment(self):
        source = {"PATH": "/x", "HOME": "/h", "LANG": "C", "LC_ALL": "C", "SSH_AUTH_SOCK": "s", "API_KEY": "k", "X_credential": "c",
                  "OPENCODE_X": "o", "TMPDIR": "/real", "Password": "p"}
        self.assertEqual(reviewcopy.reduced_environment("/scratch", source),
                         {"PATH": "/x", "HOME": "/h", "LANG": "C", "LC_ALL": "C", "TMPDIR": "/scratch"})


class TheResult(Base):
    def test_the_result_file_and_the_node_field(self):
        self.files(a="1\n", b__c="2\n")
        check_cmd, e2e_cmd = "i=0; while [ $i -lt 300 ]; do echo line $i; i=$((i+1)); done", "echo \"\x1b[31mred\x1b[0m\""
        data = graph(check=check_cmd, e2e=e2e_cmd, status="returned", revision_note="x")
        data["revision"] = 3
        self.write(data)
        before = self.read()
        code, line = self.run_it()
        self.assertEqual(code, 0, line)
        result, json_path = self.result()
        rel = json_path.relative_to(self.project).as_posix()
        self.assertRegex(rel, r"^\.opencode/artifacts/demo/n1/review-copy-\d{8}-\d{6}(-\d+)?\.json$")
        self.assertTrue(line.endswith(f"result {rel}"), line)
        check_result, e2e_result = result["commands"]
        self.assertEqual((check_result["name"], check_result["command"], check_result["sha256"], check_result["exit"], check_result["timed_out"]),
                         ("check", check_cmd, hashlib.sha256(check_cmd.encode()).hexdigest(), 0, False))
        self.assertEqual(e2e_result["sha256"], hashlib.sha256(e2e_cmd.encode()).hexdigest())
        self.assertIsInstance(check_result["seconds"], float)
        self.assertEqual((result["graph_revision"], result["task"], result["node"], result["project_unchanged"]), (3, TASK, "n1", True))
        self.assertTrue(result["graph_commands_unchanged"])
        self.assertEqual(len(result["tree"]), 64)
        self.assertEqual(result["files"], 3)  # a, b/c and the graph
        text = json_path.with_suffix(".txt").read_text()
        self.assertIn("line 299", text)
        self.assertNotIn("line 99\n", text)  # only the last 200 lines
        self.assertIn("line 100\n", text)
        self.assertNotIn("\x1b", text)
        after = self.read()
        node = after["nodes"][0]
        self.assertEqual(node["review_copy"], {"result": rel, "check_exit": 0, "e2e_exit": 0, "tree": result["tree"], "started": result["started"],
                                              "at": result["at"], "project_unchanged": True, "check_sha256": check_result["sha256"], "e2e_sha256": e2e_result["sha256"]})
        self.assertEqual(node["status"], "returned")  # it never changes the status
        self.assertEqual(after["events"], before["events"])
        self.assertGreater(after["updated"], before["updated"])
        del node["review_copy"]
        after["updated"] = before["updated"]
        self.assertEqual(after, before)
        self.assertEqual(check.file_problems(self.path), [])

    def test_the_same_tree_gives_the_same_digest_and_a_changed_file_another(self):
        self.files(a="1\n")
        self.write(graph(check="true"))
        for _ in range(2):  # the graph and its history record are part of the copy, and a run changes both: put them back
            self.run_it()
            self.write(graph(check="true"))
            shutil.rmtree(self.graph_dir / ".history", ignore_errors=True)
        self.run_it()
        found = sorted((json.loads(p.read_text()) for p in (self.project / ".opencode/artifacts/demo/n1").glob("*.json")), key=lambda r: r["at"])
        first, second = found[1]["tree"], found[2]["tree"]
        self.assertEqual(first, second)
        self.files(a="2\n")
        self.write(graph(check="true"))
        shutil.rmtree(self.graph_dir / ".history", ignore_errors=True)
        self.run_it()
        found = sorted((json.loads(p.read_text()) for p in (self.project / ".opencode/artifacts/demo/n1").glob("*.json")), key=lambda r: r["at"])
        self.assertNotEqual(found[-1]["tree"], first)

    def test_the_result_makes_a_passed_node_with_an_e2e_pass_check(self):
        self.files(a="1\n")
        self.write(graph(check="true", e2e="true"))
        self.assertEqual(self.run_it()[0], 0)
        data = self.read()
        data["status"] = "passed"
        data["nodes"][0]["status"] = "passed"
        data["events"].append({"time": data["nodes"][0]["review_copy"]["at"], "node": "n1", "event": "evaluated", "note": "ok"})
        self.write(data)
        self.assertEqual(check.file_problems(self.path), [])
        self.write(graph(check="true", e2e="true", status="passed"))  # no review copy
        self.assertTrue(any("declares an e2e and is passed" in p for p in check.file_problems(self.path)))
        # a review copy older than the node's last returned/attempt event no longer counts
        self.write(graph(check="true", e2e="true"))
        self.run_it()
        data = self.read()
        data["events"].append({"time": data["nodes"][0]["review_copy"]["at"] + 5, "node": "n1", "event": "attempt", "note": "changed -> ok"})
        data["nodes"][0]["status"] = "passed"
        self.write(data)
        self.assertTrue(any("was taken before the node's last returned, attempt or restart" in p for p in check.file_problems(self.path)))

    def test_a_failing_e2e_is_recorded_and_does_not_satisfy_the_rule(self):
        self.files(a="1\n")
        self.write(graph(check="true", e2e="exit 1"))
        self.assertEqual(self.run_it()[0], 1)
        data = self.read()
        self.assertEqual(data["nodes"][0]["review_copy"]["e2e_exit"], 1)
        data["nodes"][0]["status"] = "passed"
        self.write(data)
        self.assertTrue(any("e2e did not exit 0" in p for p in check.file_problems(self.path)))

    def test_the_commands_changed_in_the_graph_meanwhile_are_noted(self):
        self.files(a="1\n")
        self.write(graph(check=f"python3 - <<'EOF'\nimport json\np='{self.path}'\nd=json.load(open(p))\nd['nodes'][0]['e2e']='true'\njson.dump(d,open(p,'w'))\nEOF",
                         e2e="exit 1"))
        code, line = self.run_it()
        self.assertEqual(code, 1)
        self.assertIn("the node's commands changed in the graph meanwhile", line)
        self.assertFalse(self.result()[0]["graph_commands_unchanged"])
        self.assertEqual(self.read()["nodes"][0]["review_copy"]["e2e_sha256"], hashlib.sha256(b"exit 1").hexdigest())  # what ran, not what is there now


class NonGit(Base):
    def test_outside_git_the_walk_skips_the_big_and_private_folders(self):
        for rel in ("a", "sub/b", "node_modules/x", ".git/config", "venv/pyvenv.cfg", "venv/lib", "pkg/__pycache__/m.pyc",
                    ".opencode/artifacts/t/n/r", ".opencode/loops/l", ".opencode/handoff/h"):
            self.put(rel)
        self.write(graph(check="true"))
        code, line = self.run_it("--keep")
        self.assertEqual(code, 0, line)
        copy = Path(line.rsplit("copy ", 1)[1])
        self.addCleanup(shutil.rmtree, copy.parent, True)
        found = sorted(str(p.relative_to(copy)) for p in copy.rglob("*") if p.is_file())
        self.assertEqual(found, [".opencode/graph/demo.json", "a", "sub/b"])

    def test_a_project_over_the_file_cap_is_refused_with_a_clear_message(self):
        for i in range(6):
            self.files(**{f"f{i}": "x"})
        self.write(graph(check="true"))
        with mock.patch.object(reviewcopy, "FILE_LIMIT", 4):
            code, line = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("more than 4 files", line)
        self.assertEqual(list(self.temp.iterdir()), [])
        self.assertFalse((self.project / ".opencode" / "artifacts").exists())

    def test_a_project_over_the_size_cap_is_refused(self):
        self.files(big="x" * 5000)
        self.write(graph(check="true"))
        with mock.patch.object(reviewcopy, "BYTE_LIMIT", 1024 * 1024):
            self.assertEqual(self.run_it()[0], 0)
        with mock.patch.object(reviewcopy, "BYTE_LIMIT", 4000):
            code, line = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("MiB", line.replace("0 MiB", "MiB"))

    def test_symlinks_that_leave_the_project_are_not_copied(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside")
        self.files(a="1", sub__b="2")
        os.symlink(outside, self.project / "abs_link")
        os.symlink("../../outside.txt", self.project / "sub" / "up_link")
        os.symlink("b", self.project / "sub" / "ok_link")
        self.write(graph(check="test ! -e abs_link && test ! -e sub/up_link && test -L sub/ok_link && echo hacked >> sub/ok_link"))
        code, line = self.run_it()
        self.assertEqual(code, 0, line)  # only the link that stays inside the project is there
        self.assertEqual(outside.read_text(), "outside")
        self.assertEqual(self.result()[0]["symlinks_left_out"], 2)


class Refusals(Base):
    def test_a_node_with_no_commands_is_refused(self):
        self.files(a="1")
        self.write(graph())
        before = self.path.read_bytes()
        code, line = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("no check and no e2e", line)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.temp.iterdir()), [])
        self.assertFalse((self.project / ".opencode" / "artifacts").exists())

    def test_bad_commands_in_the_graph_are_refused(self):
        self.files(a="1")
        for value in ("", "  ", 3, ["true"]):
            with self.subTest(value=value):
                self.write(graph(check=value))
                code, line = self.run_it()
                self.assertEqual(code, 2)
                self.assertIn("check must be a non-blank string", line)

    def test_unknown_task_node_and_project(self):
        self.files(a="1")
        self.write(graph(check="true"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(reviewcopy.main(["--task", "nope", "--node", "n1", "--dir", str(self.project)]), 2)
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n9", "--dir", str(self.project)]), 2)
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.base / "missing")]), 2)
            self.assertEqual(reviewcopy.main(["--task", "../x", "--node", "n1", "--dir", str(self.project)]), 2)
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.project), "--timeout", "0"]), 2)
        self.assertIn("there is no task graph 'nope'", out.getvalue())
        self.assertIn("has no node 'n9'", out.getvalue())

    def test_commands_cannot_be_supplied_from_the_command_line(self):
        self.files(a="1")
        self.write(graph(check="true"))
        marker = self.base / "marker"
        for extra in (["--check", f"touch {marker}"], ["--e2e", f"touch {marker}"], ["--command", f"touch {marker}"], ["--cmd=x"],
                      ["--ch", f"touch {marker}"], ["--", f"touch {marker}"], [f"touch {marker}"]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
                reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.project), *extra])
            self.assertEqual(stop.exception.code, 2)
        self.assertFalse(marker.exists())
        self.assertFalse((self.project / ".opencode" / "artifacts").exists())

    def test_a_temp_folder_inside_the_project_is_refused(self):
        self.files(a="1")
        self.write(graph(check="true"))
        inside = self.project / "tmp"
        inside.mkdir()
        with mock.patch.dict(os.environ, {"TMPDIR": str(inside)}), mock.patch.object(tempfile, "tempdir", None):
            code, line = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("inside the project", line)
        self.assertEqual(list(inside.iterdir()), [])

    def test_a_graph_the_write_would_make_worse_is_refused_but_the_result_is_kept(self):
        self.files(a="1")
        data = graph(check="true")
        data["nodes"][0]["review_copy"] = "broken"  # already a problem: not added by the write
        self.write(data)
        self.assertEqual(self.run_it()[0], 0)
        self.assertIsInstance(self.read()["nodes"][0]["review_copy"], dict)
        # a write that would add a problem: the task cannot be written (a node whose own type is wrong)
        with mock.patch.object(reviewcopy.graphedit, "_problems", side_effect=[[], ["n1 is wrong"]]):
            code, line = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("the node was not updated", line)
        self.assertTrue(self.result()[1].exists())


class Links(Base):
    """A symlink never lets the copy, or a command in it, reach outside the copy (round 2, finding 2)."""

    def test_chained_links_that_climb_out_of_the_copy_are_left_out(self):
        outside = self.base / "pwn"
        self.files(a="1", d__f="2")
        os.symlink("..", self.project / "d" / "up")  # points at the project root: stays inside
        os.symlink("d/up/d/up/../../..", self.project / "q")  # each step looks fine alone; the whole way leaves the copy
        self.write(graph(check=f"echo hacked > q/pwn 2>/dev/null; test ! -e '{outside}' && test ! -L q && test -L d/up"))
        code, line = self.run_it()
        self.assertEqual(code, 0, line)
        self.assertFalse(outside.exists())
        self.assertGreaterEqual(self.result()[0]["symlinks_left_out"], 1)

    def test_a_link_is_never_made_over_something_that_is_already_there(self):
        """On a case-insensitive disk `Link` lands on `link`; nothing may be written through a link made there."""
        outside = self.base / "away"
        outside.mkdir()
        target = self.base / "copy"
        (target / "link").mkdir(parents=True)
        (target / "link" / "x").write_text("x")
        placed, skipped = reviewcopy._place_links([("link", "x")], target)  # the target is inside the copy: only the guard stops it
        self.assertEqual((placed, skipped), ([], 1))
        self.assertFalse(os.path.islink(target / "link"))
        self.assertEqual(os.listdir(target / "link"), ["x"])
        self.assertEqual(os.listdir(outside), [])

    def test_links_are_made_after_every_regular_file(self):
        self.files(b__x="1", a="2")
        os.symlink("b", self.project / "alink")  # sorts before `b/x`
        self.write(graph(check="test -L alink && test -f alink/x && ! test -L b"))
        self.assertEqual(self.run_it()[0], 0)


class Signals(Base):
    """SIGTERM and SIGHUP stop the commands' whole process group, remove the copy and exit 2 (round 2, finding 3)."""

    def start(self, check_command, *extra):
        self.write(graph(check=check_command))
        env = {**os.environ, "TMPDIR": str(self.temp)}
        launcher = Path(__file__).resolve().parents[1] / "bin" / "agentgraph"
        return subprocess.Popen([str(launcher), "review-copy", "--task", TASK, "--node", "n1", "--dir", str(self.project), *extra],
                                cwd=self.project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for(self, path, proc):
        for _ in range(300):
            if path.exists() and path.read_text().strip():
                return
            if proc.poll() is not None:
                self.fail(proc.stdout.read() + proc.stderr.read())
            time.sleep(0.05)
        self.fail("the command did not start")

    def gone(self, pid):
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def test_a_signal_kills_the_group_and_removes_the_copy(self):
        for sig, name in ((signal.SIGTERM, "SIGTERM"), (signal.SIGHUP, "SIGHUP")):
            with self.subTest(signal=name):
                self.files(a="1")
                pidfile = self.base / f"pid-{name}"
                proc = self.start(f"sleep 300 & echo $! > '{pidfile}'; wait")
                self.wait_for(pidfile, proc)
                grandchild = int(pidfile.read_text())
                proc.send_signal(sig)
                out, err = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 2, out + err)
                self.assertIn(f"stopped by {name}", out)
                self.assertIn("the copy was removed", out)
                self.assertTrue(self.gone(grandchild), "the grandchild is still running")
                if self.gone(grandchild) is False:
                    os.kill(grandchild, signal.SIGKILL)
                self.assertEqual(list(self.temp.iterdir()), [])
                self.assertFalse((self.project / ".opencode" / "artifacts").exists())

    def test_the_message_with_keep_says_where_the_copy_is(self):
        self.files(a="1")
        pidfile = self.base / "pid-keep"
        proc = self.start(f"sleep 300 & echo $! > '{pidfile}'; wait", "--keep")
        self.wait_for(pidfile, proc)
        grandchild = int(pidfile.read_text())
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 2, out + err)
        self.assertNotIn("the copy was removed", out)
        self.assertIn("the copy was kept in " + str(self.temp), out)
        self.assertTrue(self.gone(grandchild))
        for folder in self.temp.iterdir():
            shutil.rmtree(folder, True)

    def test_an_interrupt_with_keep_does_not_say_the_copy_was_removed(self):
        self.files(a="1")
        self.write(graph(check="true"))
        out = io.StringIO()
        with mock.patch.object(reviewcopy, "run_command", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(out):
            code = reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.project), "--keep"])
        self.assertEqual(code, 2)
        self.assertIn("the copy was kept in", out.getvalue())
        self.assertNotIn("the copy was removed", out.getvalue())
        for folder in self.temp.iterdir():
            shutil.rmtree(folder, True)

    def test_the_help_tells_the_caller_to_allow_more_time_than_the_timeout(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            reviewcopy.main(["--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn("timeout longer than --timeout", text)
        self.assertIn("SIGTERM", text)


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class GitCalls(Base):
    def test_every_git_call_turns_off_fsmonitor_and_the_untracked_cache(self):
        self.files(a="1")
        self.git_init("a")
        self.write(graph(check="true"))
        real, calls = subprocess.run, []

        def record(args, *more, **kw):
            if args and args[0] == "git":
                calls.append(list(args))
            return real(args, *more, **kw)
        with mock.patch.object(reviewcopy.subprocess, "run", side_effect=record):
            self.assertEqual(self.run_it()[0], 0)
        self.assertTrue(calls)
        for args in calls:
            self.assertEqual(args[1:5], ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"], args)

    def test_an_fsmonitor_hook_in_the_projects_git_config_is_not_started(self):
        marker = self.base / "fsmonitor-ran"
        self.files(a="1")
        self.git_init("a")
        subprocess.run(["git", "config", "core.fsmonitor", f"touch '{marker}'; printf ''"], cwd=self.project, check=True)
        self.write(graph(check="true"))
        self.assertEqual(self.run_it()[0], 0)
        self.assertFalse(marker.exists())


class Rerun(Base):
    def test_a_failing_run_on_a_passed_node_is_recorded_and_check_then_reports_it(self):
        self.files(a="1\n")
        self.write(graph(check="true", e2e="test -f a"))
        self.assertEqual(self.run_it()[0], 0)
        data = self.read()
        data["status"] = "passed"
        data["nodes"][0]["status"] = "passed"
        data["events"].append({"time": data["nodes"][0]["review_copy"]["at"], "node": "n1", "event": "evaluated", "note": "ok"})
        self.write(data)
        self.assertEqual(check.file_problems(self.path), [])
        (self.project / "a").unlink()  # the program no longer starts
        code, line = self.run_it()
        self.assertEqual(code, 1, line)
        self.assertEqual(self.read()["nodes"][0]["review_copy"]["e2e_exit"], 1)
        problems = check.file_problems(self.path)
        self.assertTrue(any("e2e did not exit 0" in p for p in problems), problems)


class Stale(Base):
    def test_the_record_keeps_when_the_copy_was_taken(self):
        self.files(a="1")
        self.write(graph(check="true", e2e="sleep 0.3"))
        before = time.time()
        self.assertEqual(self.run_it()[0], 0)
        record = self.read()["nodes"][0]["review_copy"]
        self.assertGreaterEqual(record["started"], round(before, 3))
        self.assertGreaterEqual(record["at"] - record["started"], 0.3)
        self.assertEqual(self.result()[0]["started"], record["started"])


class Where(Base):
    """`--dir` is this folder's project or its git root, nothing else; no fall-back; no linked graph folder (round 2, finding 7)."""

    def other_project(self):
        other = self.base / "other"
        (other / ".opencode" / "graph").mkdir(parents=True)
        (other / "a").write_text("1")
        (other / ".opencode" / "graph" / f"{TASK}.json").write_text(json.dumps(graph(check="true")))
        return other

    def test_another_projects_folder_is_refused(self):
        other = self.other_project()
        self.files(a="1")
        self.write(graph(check="true"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(other)]), 2)
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.base)]), 2)
        self.assertIn("is not this folder's project", out.getvalue())
        self.assertFalse((other / ".opencode" / "artifacts").exists())

    def test_the_project_is_found_from_a_folder_below_it_and_named_by_dir_only_as_itself(self):
        self.files(a="1", sub__b="2")
        self.write(graph(check="test -f sub/b"))
        os.chdir(self.project / "sub")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1"]), 0, out.getvalue())
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.project)]), 0, out.getvalue())
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.project / "sub")]), 2)

    def test_the_git_work_trees_root_is_allowed(self):
        if not HAVE_GIT:
            self.skipTest("git is not installed")
        self.files(a="1", sub__b="2")
        self.git_init()
        inner = self.project / "sub" / ".opencode" / "graph"
        inner.mkdir(parents=True)  # the nearest project is sub; the git root is above it
        (inner / f"{TASK}.json").write_text(json.dumps(graph(check="true")))
        self.write(graph(check="true"))
        os.chdir(self.project / "sub")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(self.project)]), 0, out.getvalue())

    def test_there_is_no_fall_back_to_another_graph_folder(self):
        nowhere = self.base / "nowhere"
        nowhere.mkdir()
        os.chdir(nowhere)
        out = io.StringIO()
        with mock.patch.object(reviewcopy.outputs, "graph_dirs", side_effect=AssertionError("must not look elsewhere")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1"]), 2)
        self.assertIn("there is no .opencode/graph in the current folder or above it", out.getvalue())

    def test_a_linked_graph_folder_is_refused(self):
        real = self.base / "real_graph"
        real.mkdir()
        (real / f"{TASK}.json").write_text(json.dumps(graph(check="true")))
        linked = self.base / "linked"
        (linked / ".opencode").mkdir(parents=True)
        (linked / "a").write_text("1")
        os.symlink(real, linked / ".opencode" / "graph")
        os.chdir(linked)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1"]), 2)
            self.assertEqual(reviewcopy.main(["--task", TASK, "--node", "n1", "--dir", str(linked)]), 2)
        self.assertIn("link", out.getvalue())
        self.assertEqual(list(real.iterdir()), [real / f"{TASK}.json"])


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class Watched(Base):
    """What counts as 'the project changed' (round 2, finding 8)."""

    def run_and_read(self, command):
        self.write(graph(check=command))
        code, line = self.run_it()
        return code, line, self.result()[0]

    def test_a_same_size_rewrite_with_the_old_time_put_back_is_seen(self):
        self.files(a="x\n")
        self.git_init("a")
        command = (f"python3 -c \"import os;p='{self.project}/a';st=os.stat(p);open(p,'w').write('y\\n');"
                   "os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))\"")
        code, line, result = self.run_and_read(command)
        self.assertEqual(code, 1, line)
        self.assertFalse(result["project_unchanged"])
        self.assertIn("a", result["project_changed_files"])

    def test_git_hooks_config_head_and_refs_are_watched(self):
        self.files(a="1\n")
        self.git_init("a")
        for rel in (".git/hooks/post-checkout", ".git/config", ".git/HEAD", ".git/refs/heads/evil"):
            with self.subTest(rel=rel):
                command = f"mkdir -p '{self.project}/{os.path.dirname(rel)}'; echo '# x' >> '{self.project}/{rel}'"
                code, line, result = self.run_and_read(command)
                self.assertEqual(code, 1, line)
                self.assertFalse(result["project_unchanged"])
                self.assertIn(rel, result["project_changed_files"])
                subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
                shutil.rmtree(self.project / ".git" / "refs" / "heads" / "evil", True)
                (self.project / ".git" / "refs" / "heads" / "evil").unlink(missing_ok=True)
                (self.project / ".git" / "hooks" / "post-checkout").unlink(missing_ok=True)
                subprocess.run(["git", "checkout", "-q", "--orphan", "main"], cwd=self.project)

    def test_another_tasks_artifacts_are_watched_and_this_runs_own_files_are_not(self):
        self.files(a="1\n", **{".opencode__artifacts__other__n__r.md": "old\n"})
        self.git_init()
        code, line, result = self.run_and_read(f"echo more >> '{self.project}/.opencode/artifacts/other/n/r.md'")
        self.assertEqual(code, 1, line)
        self.assertIn(".opencode/artifacts/other/n/r.md", result["project_changed_files"])
        self.assertEqual(len(result["project_changed_files"]), 1)  # not its own result files
        code, line, result = self.run_and_read("true")
        self.assertEqual(code, 0, line)  # earlier runs' results are there before and after

    def test_a_graph_write_while_it_runs_raises_no_alarm(self):
        self.files(a="1\n")
        self.git_init()
        command = (f"python3 -c \"import json;p='{self.path}';d=json.load(open(p));d['updated']+=5;json.dump(d,open(p,'w'))\"")
        code, line, result = self.run_and_read(command)
        self.assertEqual(code, 0, line)
        self.assertTrue(result["project_unchanged"])


class Odd(Base):
    def test_attempts_that_is_not_an_int_does_not_add_an_event(self):
        self.files(a="1")
        data = graph(check="true", attempts=2.0)
        self.write(data)
        before = self.read()
        self.run_it()
        after = self.read()
        self.assertEqual(after["events"], before["events"])
        self.assertEqual(after["nodes"][0]["attempts"], 2.0)

    def test_a_file_name_that_is_not_utf8_neither_crashes_nor_leaks_a_traceback(self):
        self.files(a="1")
        self.write(graph(check="true"))
        bad = "bad\udcff"
        lists = [{bad: (1, 1, 1, 1)}, {bad: (2, 1, 1, 1)}]
        with mock.patch.object(reviewcopy, "project_listing", side_effect=lists):
            code, line = self.run_it()
        self.assertEqual(code, 1, line)  # the project "changed": the name is shown escaped
        self.assertIn("PROJECT CHANGED", line)
        line.encode("ascii")
        json.loads(self.result()[1].read_text())
        self.result()[1].with_suffix(".txt").read_text(encoding="utf-8")

    def test_a_value_error_anywhere_is_an_error_line_and_exit_2(self):
        self.files(a="1")
        self.write(graph(check="true"))
        with mock.patch.object(reviewcopy, "_copy", side_effect=ValueError("embedded null byte")):
            code, line = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("error: embedded null byte", line)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_a_linked_artifacts_folder_is_not_followed(self):
        self.files(a="1")
        self.write(graph(check="true"))
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (self.project / ".opencode" / "artifacts").mkdir()
        os.symlink(elsewhere, self.project / ".opencode" / "artifacts" / TASK)
        code, line = self.run_it()
        self.assertEqual(code, 2, line)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(list(self.temp.iterdir()), [])
        self.assertNotIn("review_copy", self.read()["nodes"][0])


class Hand(Base):
    """`check` warns about a `review_copy` that the result file it names does not bear out (round 2, finding 10)."""

    def real_run(self):
        self.files(a="1")
        self.write(graph(check="true", e2e="true"))
        self.assertEqual(self.run_it()[0], 0)

    def warnings(self):
        return check.review_copy_warnings(self.read(), self.path)

    def test_a_real_result_has_no_warning(self):
        self.real_run()
        self.assertEqual(self.warnings(), [])

    def test_a_record_that_differs_from_its_file_is_warned_about(self):
        self.real_run()
        data = self.read()
        data["nodes"][0]["review_copy"]["e2e_exit"] = 1
        self.write(data)
        self.assertTrue(any("e2e does not match" in w for w in self.warnings()), self.warnings())
        data["nodes"][0]["review_copy"].update(e2e_exit=0, check_sha256="0" * 64)
        self.write(data)
        self.assertTrue(any("check does not match" in w for w in self.warnings()), self.warnings())

    def test_a_missing_file_or_one_outside_the_nodes_folder_is_warned_about(self):
        self.real_run()
        for result in (".opencode/artifacts/demo/n1/review-copy-nothing.json", "../../../etc/hosts", "review-copy-x.json",
                       ".opencode/artifacts/demo/n2/review-copy-x.json", ".opencode/artifacts/demo/n1/other.json", 5):
            with self.subTest(result=result):
                data = self.read()
                data["nodes"][0]["review_copy"]["result"] = result
                self.write(data)
                self.assertEqual(len(self.warnings()), 1, self.warnings())

    def test_the_warning_is_part_of_the_check_output_not_an_error(self):
        self.real_run()
        data = self.read()
        data["nodes"][0]["review_copy"]["e2e_exit"] = 1
        self.write(data)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(self.graph_dir), "--no-history"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("e2e does not match", out.getvalue())


class HandRound3(Base):
    """A result file `check` must not hang on, follow or choke on (round 3, items 1 and 5)."""

    real_run = Hand.real_run
    warnings = Hand.warnings

    def result_file(self):
        return self.result()[1]

    def warned(self, seconds=5):
        """The warnings, or a failure if `check` is still reading after `seconds` (a FIFO must not hang it)."""
        found = []
        failure = []

        def work():
            try:
                found.extend(self.warnings())
            except BaseException as exc:  # noqa: BLE001 - the test reports it
                failure.append(exc)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(seconds)
        if thread.is_alive():
            with contextlib.suppress(OSError):  # let the reader go
                os.close(os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK))
            self.fail("check is waiting on the result file")
        if failure:
            raise failure[0]
        return found

    def test_a_fifo_at_the_result_path_is_a_warning_not_a_hang(self):
        self.real_run()
        path = self.result_file()
        path.unlink()
        self.fifo = path
        os.mkfifo(path)
        self.assertTrue(any("is not a result file" in w for w in self.warned()))

    def test_a_link_at_the_result_path_is_not_followed(self):
        self.real_run()
        path = self.result_file()
        real = self.base / "elsewhere.json"
        real.write_text(path.read_text())
        path.unlink()
        os.symlink(real, path)
        self.assertTrue(any("is not a result file" in w for w in self.warned()), self.warnings())

    def test_a_linked_folder_on_the_way_is_not_followed(self):
        self.real_run()
        folder = self.result_file().parent
        moved = self.base / "moved"
        folder.rename(moved)
        os.symlink(moved, folder)
        self.assertTrue(any("is not a result file" in w for w in self.warned()), self.warnings())

    def test_an_oversized_result_file_is_a_warning(self):
        self.real_run()
        path = self.result_file()
        data = json.loads(path.read_text())
        data["padding"] = "x" * (check.RESULT_LIMIT + 10)
        path.write_text(json.dumps(data))
        self.assertTrue(any("is not a result file" in w for w in self.warned()))

    def test_wrong_types_in_the_result_file_are_warnings_never_an_exception(self):
        self.real_run()
        path = self.result_file()
        good = json.loads(path.read_text())
        for odd in ({**good, "commands": 5}, {**good, "commands": [5, None, {"name": "check", "exit": "x", "timed_out": []}]},
                    {**good, "commands": {"name": "check"}}, [1, 2], "text", 7, None, {**good, "commands": [{"name": ["a"], "exit": {}}]},
                    {**good, "tree": [1], "started": {}, "at": "late", "project_unchanged": 3}):
            with self.subTest(odd=str(odd)[:60]):
                path.write_text(json.dumps(odd))
                self.assertIsInstance(self.warned(), list)
        path.write_bytes(b"\xff\xfe{")
        self.assertTrue(any("is not a result file" in w for w in self.warned()))

    def test_started_at_tree_and_project_unchanged_are_compared_with_the_file(self):
        self.real_run()
        for field, value in (("started", 5.0), ("at", 9.5), ("tree", "0" * 64), ("project_unchanged", False)):
            with self.subTest(field=field):
                data = self.read()
                data["nodes"][0]["review_copy"][field] = value
                self.write(data)
                self.assertTrue(any(f"review_copy {field} does not match" in w for w in self.warned()), self.warnings())
                data["nodes"][0]["review_copy"] = {**data["nodes"][0]["review_copy"], field: json.loads(self.result_file().read_text())[field]}
                self.write(data)
                self.assertEqual(self.warned(), [])


class Late(Base):
    """What a signal, or a refused listing, does around the saving of the result (round 3, items 2 and 6)."""

    def test_a_signal_while_the_result_is_saved_waits_and_the_run_is_recorded(self):
        self.files(a="1")
        self.write(graph(check="true"))
        real = reviewcopy._record

        def record_and_get_signalled(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)  # arrives now, is held until the node is written
            return real(*args, **kwargs)
        old = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, old)
        with mock.patch.object(reviewcopy, "_record", record_and_get_signalled):
            code, line = self.run_it()
        self.assertNotIn("no result", line)
        self.assertIn("saved and recorded", line)
        self.assertEqual(self.read()["nodes"][0]["review_copy"]["result"], self.result()[0] and f".opencode/artifacts/{TASK}/n1/{self.result()[1].name}")

    def test_an_exception_while_the_result_is_written_leaves_no_half_written_file(self):
        self.files(a="1")
        self.write(graph(check="true"))
        real = json.dumps

        def refuse_the_result(obj, **kwargs):
            if kwargs.get("indent") == 2:
                raise ValueError("boom")
            return real(obj, **kwargs)
        with mock.patch.object(reviewcopy.json, "dumps", refuse_the_result):
            code, line = self.run_it()
        self.assertEqual(code, 2, line)
        folder = self.project / ".opencode" / "artifacts" / TASK / "n1"
        self.assertEqual(list(folder.glob("review-copy-*")), [])
        self.assertNotIn("review_copy", self.read()["nodes"][0])

    def test_a_listing_that_is_refused_after_the_run_is_recorded_as_a_changed_project(self):
        self.files(a="1")
        self.write(graph(check="true"))
        real = reviewcopy.project_listing
        calls = []

        def second_time_refused(*args, **kwargs):
            calls.append(1)
            if len(calls) > 1:
                raise reviewcopy.Refused("too many files")
            return real(*args, **kwargs)
        with mock.patch.object(reviewcopy, "project_listing", second_time_refused):
            code, line = self.run_it()
        self.assertEqual(code, 1, line)
        self.assertIn("NOT CHECKED", line)
        self.assertIs(self.result()[0]["project_unchanged"], False)
        self.assertIs(self.read()["nodes"][0]["review_copy"]["project_unchanged"], False)


class Folders(Base):
    def test_a_graph_folder_owned_by_another_user_is_refused_not_skipped(self):
        inner = self.project / "sub"
        (inner / ".opencode" / "graph").mkdir(parents=True)
        mine = os.getuid()
        with mock.patch.object(reviewcopy.os, "getuid", side_effect=[mine + 1, mine, mine]):
            with self.assertRaises(reviewcopy.Refused) as caught:
                reviewcopy._nearest_project(inner)
        self.assertIn("another user", str(caught.exception))

    @unittest.skipUnless(HAVE_GIT, "git is not installed")
    def test_a_linked_work_trees_common_git_folder_is_watched(self):
        self.files(a="1\n")
        self.git_init("a")
        tree = self.base / "tree"
        subprocess.run(["git", "worktree", "add", "-q", str(tree)], cwd=self.project, check=True, capture_output=True)
        self.assertTrue((tree / ".git").is_file())
        (tree / ".opencode" / "graph").mkdir(parents=True)
        self.project, self.graph_dir = tree, tree / ".opencode" / "graph"
        self.path = self.graph_dir / f"{TASK}.json"
        os.chdir(tree)
        common = self.base / "project" / ".git"
        hook = common / "hooks" / "post-checkout"
        for rel, command in (("hooks/post-checkout", f"echo '# x' >> '{hook}'"), ("config", f"echo '# x' >> '{common}/config'"),
                             ("refs/heads/evil", f"echo 0 > '{common}/refs/heads/evil'")):
            with self.subTest(rel=rel):
                self.write(graph(check=command))
                code, line = self.run_it()
                self.assertEqual(code, 1, line)
                self.assertIn(str(common / rel), self.result()[0]["project_changed_files"])
                self.assertIs(self.result()[0]["project_unchanged"], False)
                hook.unlink(missing_ok=True)
                (common / "refs" / "heads" / "evil").unlink(missing_ok=True)


class Documented3(unittest.TestCase):
    def test_what_is_not_seen_and_the_age_rules_are_written_down(self):
        root = Path(__file__).resolve().parents[1]
        commands = (root / "COMMANDS.md").read_text()
        for words in (".opencode/loops", ".opencode/handoff", "common git folder"):
            self.assertIn(words, commands)
        fmt = (root / "GRAPH_FORMAT.md").read_text()
        for words in ("still editing", "no events"):
            self.assertIn(words, fmt)
        proposal = root / ".opencode" / "artifacts" / "agentgraph-cost-loops" / "v1" / "permission-proposal.md"
        if proposal.exists():
            self.assertIn(".opencode/handoff", proposal.read_text())


class Documented(unittest.TestCase):
    def test_the_limits_are_written_down(self):
        root = Path(__file__).resolve().parents[1]
        commands = (root / "COMMANDS.md").read_text()
        for words in ("setsid", "SIGTERM", "must be longer than `--timeout`", "git work tree", ".git/hooks"):
            self.assertIn(words, commands)
        self.assertIn("review_copy", (root / "GRAPH_FORMAT.md").read_text())


class Entry(Base):
    def test_the_command_is_forwarded_and_exits_like_it_says(self):
        self.files(a="1")
        self.write(graph(check="true", e2e="exit 1"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = entry.main(["review-copy", "--task", TASK, "--node", "n1", "--dir", str(self.project)])
        self.assertEqual(code, 1)
        self.assertTrue(out.getvalue().startswith("review-copy demo/n1: check exit 0"), out.getvalue())
        self.assertEqual(len(out.getvalue().splitlines()), 1)

    def test_the_launcher_runs_it(self):
        self.files(a="1")
        self.write(graph(e2e="echo up"))
        launcher = Path(__file__).resolve().parents[1] / "bin" / "agentgraph"
        done = subprocess.run([str(launcher), "review-copy", "--task", TASK, "--node", "n1", "--dir", str(self.project)],
                              capture_output=True, text=True, cwd=self.project)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("e2e exit 0", done.stdout)


if __name__ == "__main__":
    unittest.main()
