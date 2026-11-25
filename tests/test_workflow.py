"""The GitHub workflow is written by hand and cannot be run here, so this checks its shape without a YAML library:
the triggers, the two systems, the Python and Node versions, and that it runs each test command the job lists."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github" / "workflows" / "tests.yml").read_text()


class WorkflowTests(unittest.TestCase):
    def test_no_tabs_and_indented_by_two(self):
        for number, line in enumerate(TEXT.splitlines(), 1):
            self.assertNotIn("\t", line, number)
            self.assertEqual((len(line) - len(line.lstrip(" "))) % 2, 0, f"line {number}: {line!r}")

    def test_triggers(self):
        self.assertRegex(TEXT, r"(?m)^on:\n  push:\n    branches: \[main, \"win-\*\"\]\n  pull_request:\n  workflow_dispatch:")

    def test_matrix_versions_and_no_secrets(self):
        self.assertIn("os: [windows-latest, macos-latest]", TEXT)
        self.assertIn("fail-fast: false", TEXT)
        self.assertIn('python-version: "3.12"', TEXT)
        self.assertIn('node-version: "20"', TEXT)
        self.assertNotIn("secrets.", TEXT)
        self.assertIn("contents: read", TEXT)

    def test_every_command_it_runs_exists(self):
        commands = ["python -m unittest discover -s tests -t ."] + re.findall(r"run: (node \S+)", TEXT)
        self.assertEqual(commands[1:], ["node tui-plugin/test/run.mjs", "node desktop-plugin/test/harness.mjs",
                                        "node rollover-plugin/test.js", "node output-caps-plugin/test.js"])
        self.assertIn(commands[0], TEXT)
        for command in commands[1:]:
            self.assertTrue((ROOT / command.split()[1]).is_file(), command)

    def test_a_failed_python_run_prints_the_end_of_its_log(self):
        self.assertIn("tail -n 150 ci-python.log", TEXT)
        self.assertIn('exit "$code"', TEXT)


if __name__ == "__main__":
    unittest.main()
