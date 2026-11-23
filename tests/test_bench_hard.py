"""The hard bench tasks (h1 to h4): each task is proved with hand-written solutions, with no model run.

For every task, on a fresh copy made by `bench.prepare_work`: the fixture fails the visible check; with `solutions/naive`
laid over it the visible check passes and the hidden check fails; with `solutions/reference` laid over it both pass.
The hidden check is run by `bench.run_check` (it copies `hidden/` in, then runs the scenario's check command)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentgraph import bench

IDS = ["h1-page-count", "h2-contact-shape", "h3-todo-flags", "h4-duration-spec"]
SUITE = bench.BENCH_DIR / "suites" / "hard.json"


def visible(scenario, work):
    """The scenario's check command before `hidden/` is copied in (what the agent sees)."""
    done = subprocess.run(scenario["check"], shell=True, cwd=work, capture_output=True, text=True,
                          timeout=scenario["check_timeout"], env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return done.returncode == 0


class HardTasks(unittest.TestCase):
    def fresh(self, name, layer=None):
        scenario = bench.load_scenario(name)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        work = tmp / "work"
        bench.prepare_work(scenario["dir"], work)
        if layer:
            shutil.copytree(scenario["dir"] / "solutions" / layer, work, dirs_exist_ok=True)
        return scenario, work

    def hidden(self, scenario, work):
        return bench.run_check(scenario, work, {**os.environ})

    def test_suite_lists_exactly_the_four(self):
        data = json.loads(SUITE.read_text(encoding="utf-8"))
        self.assertEqual(data["scenarios"], IDS)
        self.assertEqual(data["name"], "hard")
        self.assertIsInstance(data["note"], str)
        self.assertEqual(set(data), {"name", "note", "scenarios"})

    def test_scenarios_load(self):
        for name in IDS:
            with self.subTest(name=name):
                scenario = bench.load_scenario(name)
                self.assertTrue(scenario["prompt"] and scenario["check"])
                self.assertTrue((scenario["dir"] / "hidden").is_dir())
                self.assertTrue((scenario["dir"] / "solutions" / "reference").is_dir())
                self.assertTrue((scenario["dir"] / "solutions" / "naive").is_dir())

    def test_solutions_are_not_copied_into_the_work_folder(self):
        for name in IDS:
            with self.subTest(name=name):
                _scenario, work = self.fresh(name)
                self.assertFalse((work / "solutions").exists())
                self.assertFalse((work / "hidden").exists())

    def test_solutions_hold_only_files_that_differ_from_the_fixture(self):
        for name in IDS:
            for layer in ("reference", "naive"):
                base = bench.SCENARIOS / name
                for path in sorted((base / "solutions" / layer).rglob("*")):
                    if path.is_file():
                        twin = base / "fixture" / path.relative_to(base / "solutions" / layer)
                        with self.subTest(name=name, layer=layer, file=path.name):
                            self.assertFalse(twin.is_file() and twin.read_bytes() == path.read_bytes())

    def test_untouched_fixture_fails_the_visible_check(self):
        for name in IDS:
            with self.subTest(name=name):
                scenario, work = self.fresh(name)
                self.assertFalse(visible(scenario, work))

    def test_naive_passes_visible_and_fails_hidden(self):
        for name in IDS:
            with self.subTest(name=name):
                scenario, work = self.fresh(name, "naive")
                self.assertTrue(visible(scenario, work))
                self.assertFalse(self.hidden(scenario, work)["passed"])

    def test_reference_passes_visible_and_hidden(self):
        for name in IDS:
            with self.subTest(name=name):
                scenario, work = self.fresh(name, "reference")
                self.assertTrue(visible(scenario, work))
                result = self.hidden(scenario, work)
                self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main()
