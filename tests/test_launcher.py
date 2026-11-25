"""The Windows way in matches the shell way in: `bin/agentgraph.cmd` runs `agentgraph.cli`, whose table must say what
`bin/agentgraph`'s `case` says; `install.cmd` installs the same commands as `install.sh`. Nothing here needs Windows:
the shell script and the table are both parsed, and the .cmd files are checked for the few things they must do."""
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from agentgraph import cli, installcli, installer

ROOT = Path(__file__).resolve().parent.parent


def parse_launcher():
    """(commands, home) from bin/agentgraph: command -> (module, shifted, extra args)."""
    text = (ROOT / "bin" / "agentgraph").read_text()
    body = text[text.index('case "$1" in'):]
    table = {}
    default = None
    for label, block in re.findall(r"^  ([a-z|*]+)\)\n(.*?);;", body, re.S | re.M):
        run = re.search(r"run_python -P -m (\S+)(.*)", block)
        module, rest = run.group(1), run.group(2)
        extra = tuple(word for word in rest.split() if word.startswith("--") and word != '"$@"')
        shifted = "shift" in block.split("run_python")[0]
        if label == "*":
            default = module
            continue
        for name in label.split("|"):
            table[name] = (module, shifted, extra)
    home = re.search(r'\[ "\$1" = "--home" \].*?\n\s+run_python -P -m (\S+)', text, re.S).group(1)
    return table, default, home


class LauncherTableTests(unittest.TestCase):
    def test_every_command_of_the_shell_launcher_is_in_the_python_table(self):
        table, default, home = parse_launcher()
        self.assertGreater(len(table), 20)
        self.assertEqual(set(table), set(cli.COMMANDS))
        # `python -m agentgraph` runs the package's __main__
        self.assertEqual(default + ".__main__", cli.DEFAULT_MODULE)
        self.assertEqual(home, cli.HOME_MODULE)

    def test_each_command_runs_the_same_module_with_the_same_words(self):
        table, _default, _home = parse_launcher()
        for name, (module, shifted, extra) in table.items():
            with self.subTest(command=name):
                entry = cli.COMMANDS[name]
                self.assertEqual(entry[0], module)
                self.assertEqual(entry[1], shifted)
                self.assertEqual(tuple(entry[2]), extra)
                got = cli.resolve([name, "one", "--two"])
                words = ["one", "--two"] if shifted else [name, "one", "--two"]
                self.assertEqual(got, (module, [*extra, *words]))

    def test_every_module_exists(self):
        modules = {entry[0] for entry in cli.COMMANDS.values()} | {cli.DEFAULT_MODULE, cli.HOME_MODULE}
        for module in sorted(modules):
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.util.find_spec(module))

    def test_resolve_edges_follow_the_script(self):
        self.assertEqual(cli.resolve([]), (cli.DEFAULT_MODULE, []))
        self.assertEqual(cli.resolve(["--json"]), (cli.DEFAULT_MODULE, ["--json"]))
        self.assertEqual(cli.resolve(["handoff", "x"]), (cli.DEFAULT_MODULE, ["handoff", "x"]))
        self.assertEqual(cli.resolve(["--home", "D", "record"]), (cli.HOME_MODULE, ["--home", "D", "record"]))
        # --home with fewer than three words is not the ledger form
        self.assertEqual(cli.resolve(["--home", "D"]), (cli.DEFAULT_MODULE, ["--home", "D"]))
        self.assertEqual(cli.resolve(["snapshot"]), ("agentgraph.snapshot_json", ["--no-activity-detail"]))

    def test_running_the_module_gives_the_command_its_own_help_and_status(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        done = subprocess.run([sys.executable, "-P", "-m", "agentgraph.cli", "check", "--help"],
                              capture_output=True, text=True, env=env, cwd=tempfile.gettempdir(), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("usage", (done.stdout + done.stderr).lower())
        bad = subprocess.run([sys.executable, "-P", "-m", "agentgraph.cli", "check", "--no-such-flag"],
                             capture_output=True, text=True, env=env, cwd=tempfile.gettempdir(), timeout=60)
        self.assertNotEqual(bad.returncode, 0)


@unittest.skipIf(os.name == "nt", "compares with the sh launcher")
class SameAsShellTests(unittest.TestCase):
    def test_check_help_matches_the_shell_launcher(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cwd = tempfile.gettempdir()
        sh = subprocess.run([str(ROOT / "bin" / "agentgraph"), "spend", "--help"],
                            capture_output=True, text=True, env=env, cwd=cwd, timeout=60)
        py = subprocess.run([sys.executable, "-P", "-m", "agentgraph.cli", "spend", "--help"],
                            capture_output=True, text=True, env=env, cwd=cwd, timeout=60)
        self.assertEqual((sh.returncode, sh.stdout, sh.stderr), (py.returncode, py.stdout, py.stderr))


class WindowsFilesTests(unittest.TestCase):
    def read(self, *parts):
        return (ROOT.joinpath(*parts)).read_bytes().decode()

    def test_cmd_files_are_small_and_use_crlf(self):
        for parts in (("bin", "_python.cmd"), ("bin", "agentgraph.cmd"), ("install.cmd",)):
            with self.subTest(file=parts[-1]):
                text = self.read(*parts)
                self.assertTrue(text.replace("\r\n", "").count("\n") == 0, "every line ends with CRLF")
                self.assertLess(text.count("\r\n"), 20)

    def test_launcher_hands_over_to_the_python_table(self):
        text = self.read("bin", "agentgraph.cmd")
        self.assertIn("-P -m agentgraph.cli %*", text)
        self.assertIn("_python.cmd", text)
        self.assertIn("PYTHONPATH", text)

    def test_python_picker_asks_for_3_11_and_tries_py_then_python(self):
        text = self.read("bin", "_python.cmd")
        self.assertIn("(3, 11)", text)
        self.assertLess(text.index("py -3 -c"), text.index("python -c"))
        self.assertIn("AGENTGRAPH_PYTHON", text)

    def test_install_cmd_runs_the_installer_front_door(self):
        text = self.read("install.cmd")
        self.assertIn("-P -m agentgraph.installcli %*", text)
        self.assertIn("bin\\_python.cmd", text)
        self.assertTrue((ROOT / "agentgraph" / "installcli.py").is_file())

    def test_gitattributes_keep_scripts_byte_stable(self):
        text = self.read(".gitattributes")
        self.assertIn("*.sh text eol=lf", text)
        self.assertIn("*.cmd text eol=crlf", text)
        self.assertIn("bin/agentgraph text eol=lf", text)


class InstallListTests(unittest.TestCase):
    def test_the_python_list_is_the_one_install_sh_uses(self):
        text = (ROOT / "commands" / ".toolkit.sh").read_text()
        body = re.search(r"toolkit_commands\(\) \{\n\s+printf '%s\\n' (.*)\n\}", text).group(1)
        self.assertEqual(tuple(body.split()), installer.TOOLKIT_COMMANDS)

    def test_every_listed_command_has_a_file_and_no_file_is_missing_from_the_list(self):
        files = sorted(path.stem for path in (ROOT / "commands").glob("*.md"))
        self.assertEqual(files, sorted(installer.TOOLKIT_COMMANDS))


class InstallCliTests(unittest.TestCase):
    def test_target_folder_follows_install_sh(self):
        self.assertEqual(installcli.target_folder({"OPENCODE_CONFIG_DIR": "X"}, "H"), Path("X"))
        self.assertEqual(installcli.target_folder({}, "H"), Path("H") / ".config" / "opencode")
        self.assertEqual(installcli.target_folder({"OPENCODE_CONFIG_DIR": ""}, "H"), Path("H") / ".config" / "opencode")

    def test_bad_option_prints_usage_and_exits_2(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = installcli.main(["--nonsense"], env={}, home="H")
        self.assertEqual(code, 2)
        self.assertIn("usage: install.cmd", err.getvalue())

    def test_dry_run_into_a_scratch_folder_writes_nothing(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "cfg"
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = installcli.main(["--dry-run"], env={"OPENCODE_CONFIG_DIR": str(target)}, home=base)
            self.assertEqual(code, 0, err.getvalue())
            self.assertFalse((target / "commands").exists())

    def test_install_into_a_scratch_folder_puts_the_commands_there(self):
        with tempfile.TemporaryDirectory() as base:
            target = Path(base) / "cfg"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = installcli.main([], env={"OPENCODE_CONFIG_DIR": str(target)}, home=base)
            self.assertEqual(code, 0)
            for name in installer.TOOLKIT_COMMANDS:
                self.assertTrue((target / "commands" / f"{name}.md").is_file(), name)


if __name__ == "__main__":
    unittest.main()
