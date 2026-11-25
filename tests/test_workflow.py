"""The GitHub workflow is written by hand and cannot be run here, so this checks its shape without a YAML library:
the triggers, the two systems, the Python and Node versions, how Windows runs (PowerShell, with and without `sh`),\nand that it runs each test command the job lists."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")


def step(name):
    """The text of the step called `name` (from its `- name:` line to the next step)."""
    found = re.search(r"(?ms)^      - name: " + re.escape(name) + r"\n(.*?)(?=^      - |\Z)", TEXT)
    assert found, f"no step called {name!r}"
    return found.group(1)


class WorkflowTests(unittest.TestCase):
    def test_no_tabs_and_indented_by_two(self):
        for number, line in enumerate(TEXT.splitlines(), 1):
            self.assertNotIn("\t", line, number)
            self.assertEqual((len(line) - len(line.lstrip(" "))) % 2, 0, f"line {number}: {line!r}")

    def test_triggers(self):
        self.assertRegex(TEXT, r"(?m)^on:\n  push:\n    branches: \[main, \"win-\*\"\]\n  pull_request:\n  workflow_dispatch:")

    def test_matrix_versions_and_no_secrets(self):
        self.assertIn("os: [windows-latest, macos-latest]", TEXT)
        self.assertIn('python-version: ["3.11", "3.12"]', TEXT)
        self.assertIn("fail-fast: false", TEXT)
        self.assertIn('node-version: "20"', TEXT)
        self.assertNotIn("secrets.", TEXT)
        self.assertIn("contents: read", TEXT)

    def test_actions_are_pinned_to_a_major_version(self):
        uses = re.findall(r"(?m)^\s+(?:- )?uses: (\S+)$", TEXT)
        self.assertEqual(sorted(uses), ["actions/checkout@v4", "actions/setup-node@v4", "actions/setup-python@v5",
                                        "actions/upload-artifact@v4"])

    def test_windows_runs_from_powershell_without_utf8_mode_or_bash(self):
        self.assertNotRegex(TEXT, r"(?m)^\s*PYTHONUTF8\s*:")  # UTF-8 mode would hide code-page bugs
        self.assertNotRegex(TEXT, r"(?m)^    defaults:")  # no job-wide bash: it would put Git's sh on PATH
        block = step("Python tests (Windows, PowerShell)")
        self.assertIn("if: runner.os == 'Windows'", block)
        self.assertIn("shell: pwsh", block)
        self.assertIn("python -m unittest discover -s tests -t .", block)
        self.assertNotIn("bash", block)

    def test_a_windows_leg_runs_without_sh_on_path(self):
        self.assertRegex(TEXT, r"(?m)^        no-sh: \[false\]$")
        self.assertRegex(TEXT, r"(?m)^        include:\n(?:          #.*\n)*          - os: windows-latest\n"
                               r"            python-version: \"3.12\"\n            no-sh: true\n")
        block = step("Python tests (Windows, PowerShell)")
        self.assertIn("matrix.no-sh", block)
        self.assertIn("'sh.exe'", block)  # every PATH folder that holds an sh is dropped
        self.assertIn("sh is still on PATH", block)  # and the step fails if one remains

    def test_every_command_it_runs_exists(self):
        commands = ["python -m unittest discover -s tests -t ."] + re.findall(r"run: (node \S+)", TEXT)
        self.assertEqual(commands[1:], ["node tui-plugin/test/run.mjs", "node desktop-plugin/test/harness.mjs",
                                        "node rollover-plugin/test.js", "node output-caps-plugin/test.js"])
        self.assertIn(commands[0], TEXT)
        for command in commands[1:]:
            self.assertTrue((ROOT / command.split()[1]).is_file(), command)

    def test_a_failed_python_run_lists_the_failing_tests_then_prints_the_end_of_its_log(self):
        unix = step("Python tests (Unix)")
        self.assertIn("shell: bash", unix)
        self.assertLess(unix.index("grep -E '^(ERROR|FAIL):'"), unix.index("tail -n 150 ci-python.log"))
        self.assertIn('exit "$code"', unix)
        windows = step("Python tests (Windows, PowerShell)")
        self.assertIn("'^(ERROR|FAIL):'", windows)
        self.assertLess(windows.index("'^(ERROR|FAIL):'"), windows.index("-Tail 150"))
        self.assertIn("exit $code", windows)

    def test_the_log_is_uploaded_when_the_job_fails(self):
        block = step("Keep the Python log")
        self.assertIn("if: failure()", block)
        self.assertIn("uses: actions/upload-artifact@v4", block)
        self.assertIn("path: ci-python.log", block)
        self.assertIn("matrix.os", block)  # one artifact name per leg
        self.assertIn("matrix.python-version", block)
        self.assertIn("matrix.no-sh", block)


if __name__ == "__main__":
    unittest.main()
