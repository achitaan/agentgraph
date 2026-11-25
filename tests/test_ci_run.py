"""tests/ci_run.py on tiny invented test modules: a pass, a failure, a hang (with a grandchild), a bad import."""
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from agentgraph import osproc
from tests import ci_run

PASSING = """
import unittest
class T(unittest.TestCase):
    def test_one(self): pass
    @unittest.skip("not now")
    def test_two(self): pass
"""
FAILING = """
import unittest
class T(unittest.TestCase):
    def test_bad(self): self.assertEqual(1, 2)
    def test_boom(self): raise RuntimeError("boom")
    def test_fine(self): pass
"""
BROKEN = "import module_that_does_not_exist\n"
HANGING = """
import subprocess, sys, time, unittest
class T(unittest.TestCase):
    def test_fast(self): pass
    def test_sleeps_forever(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        with open(PIDFILE, "w") as stream:
            stream.write(str(child.pid))
        time.sleep(600)
"""


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.path.realpath(self.enterContext(tempfile.TemporaryDirectory())))
        self.package = self.root / "invented"
        self.package.mkdir()
        (self.package / "__init__.py").write_text("", encoding="utf-8")
        self.logs = self.root / "logs"

    def module(self, name, source):
        (self.package / f"test_{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")

    def run_runner(self, *args, timeout=60):
        env = {**os.environ, "GITHUB_STEP_SUMMARY": str(self.root / "summary.md")}
        return subprocess.run([sys.executable, str(Path(ci_run.__file__)), "--tests", str(self.package), "--logs",
                               str(self.logs), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=self.root, env=env, timeout=timeout)

    def test_finds_the_modules_discover_would(self):
        self.module("a", PASSING)
        self.module("b", PASSING)
        (self.package / "helper.py").write_text("", encoding="utf-8")
        self.assertEqual(ci_run.find_modules(self.package), ["invented.test_a", "invented.test_b"])
        real = ci_run.find_modules(Path(ci_run.__file__).parent)
        self.assertIn("tests.test_ci_run", real)
        self.assertNotIn("tests.ci_run", real)

    def test_pass_fail_and_bad_import(self):
        self.module("good", PASSING)
        self.module("bad", FAILING)
        self.module("broken", BROKEN)
        done = self.run_runner("--jobs", "2")
        out = done.stdout
        self.assertEqual(done.returncode, 1, out[-800:])
        self.assertRegex(out, r"(?m)^ok +invented\.test_good .*run=2 fail=0 err=0 skip=1")
        self.assertRegex(out, r"(?m)^FAIL +invented\.test_bad .*run=3 fail=1 err=1 skip=0")
        self.assertRegex(out, r"(?m)^IMPORT ERROR +invented\.test_broken ")
        self.assertIn("TOTALS: 3 modules", out)
        self.assertIn("ERROR: test_boom (invented.test_bad.T.test_boom)", out)
        self.assertIn("FAIL: test_bad (invented.test_bad.T.test_bad)", out)
        self.assertTrue((self.logs / "invented.test_bad.log").is_file())
        summary = (self.root / "summary.md").read_text(encoding="utf-8")
        self.assertIn("FAILED", summary)

    def test_all_passing_exits_zero(self):
        self.module("good", PASSING)
        done = self.run_runner()
        self.assertEqual(done.returncode, 0, done.stdout[-800:])
        self.assertIn("TOTALS: 1 modules, Ran 2 tests, failures=0, errors=0, skipped=1", done.stdout)

    def test_a_hang_is_killed_with_its_children_and_named(self):
        pidfile = self.root / "child.pid"
        self.module("hang", f"PIDFILE = {str(pidfile)!r}\n" + textwrap.dedent(HANGING))
        self.module("zz_after", PASSING)
        started = time.monotonic()
        done = self.run_runner("--limit", "3")
        self.assertLess(time.monotonic() - started, 40)
        out = done.stdout
        self.assertEqual(done.returncode, 1, out[-800:])
        self.assertRegex(out, r"(?m)^TIMEOUT +invented\.test_hang ")
        self.assertRegex(out, r"(?m)^ok +invented\.test_zz_after ")  # the next module still ran
        self.assertIn("last test started: test_sleeps_forever (invented.test_hang.T.test_sleeps_forever)", out)
        self.assertIn("most recent call first", out)  # the stack dump of the threads
        grandchild = int(pidfile.read_text(encoding="utf-8"))
        for _ in range(50):
            if not osproc.is_alive(grandchild):
                break
            time.sleep(0.1)
        self.assertFalse(osproc.is_alive(grandchild), "the hung test's child process is still running")

    def test_parse(self):
        text = ("test_a (m.T.test_a) ... ok\nFAIL: test_b (m.T.test_b)\n\nRan 7 tests in 0.1s\n\n"
                "FAILED (failures=2, errors=1, skipped=3)\n")
        found = ci_run.parse(text)
        self.assertEqual((found["run"], found["failures"], found["errors"], found["skips"]), (7, 2, 1, 3))
        self.assertEqual(found["names"], ["test_b (m.T.test_b)"])
        self.assertEqual(ci_run.parse("Ran 4 tests in 1s\n\nOK (skipped=1)\n")["skips"], 1)
        self.assertEqual(ci_run.last_started("test_a (m.T.test_a) ... ok\ntest_b (m.T.test_b) ... \nTimeout (0:00:03)!\n"),
                         "test_b (m.T.test_b)")


if __name__ == "__main__":
    unittest.main()
