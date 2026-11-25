import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.launch import LAUNCHER
from agentgraph import bloat, ledger

ROOT = Path(__file__).resolve().parent.parent
SECRET = "ZEBRA-QUOKKA-0451"  # appears only inside preset prompt texts; must never be printed
DUPLICATE = ("Every worker writes its long output to an artifact file and returns the path with a short summary, "
             "so the primary context stays small.")


def skill(root, name, description):
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n", encoding="utf-8")


class Scratch(unittest.TestCase):
    """A scratch config folder: two presets, three skills, an instructions file and a session database."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "opencode"
        self.project = Path(temporary.name) / "project"
        self.home = self.root / "orchestration" / "memory"
        self.db = Path(temporary.name) / "sessions.db"
        (self.root / "oh-my-opencode-slim" / "p2").mkdir(parents=True)
        (self.root / "agents").mkdir()
        (self.root / "orchestration" / "structure" / "30-build").mkdir(parents=True)
        self.home.mkdir(parents=True)
        (self.project / ".opencode" / "graph").mkdir(parents=True)
        skill(self.root, "alpha", "Alpha helps with the first thing.")
        skill(self.root, "beta", "Beta helps with the second thing.")
        skill(self.root, "gamma", "Gamma is installed and named by no list.")
        (self.root / "AGENTS.md").write_text(
            "# Policy\n\nWork in small steps.\n\n" + DUPLICATE + "\n\n"
            "# Other project ledger\n\nUnder /elsewhere/projects/other-repo run tools/helper.py first.\n", encoding="utf-8")
        self.presets = {
            "p1": {
                "orchestrator": {"skills": ["*"]},
                "impl": {"skills": ["alpha", "beta"], "description": "Implements",
                         "prompt": f"You implement. {SECRET}\n\n{DUPLICATE}",
                         "orchestratorPrompt": f"Send bounded tasks to impl. {SECRET}"},
                "rev": {"skills": ["alpha"], "prompt": "You review."},
            },
            "p2": {"orchestrator": {"skills": ["*"]}, "impl": {"skills": ["alpha"]}},
        }
        self.write_presets()
        (self.root / "oh-my-opencode-slim" / "impl_append.md").write_text("Append for impl, every preset.\n", encoding="utf-8")
        (self.root / "oh-my-opencode-slim" / "orchestrator_append.md").write_text("base orchestrator append\n", encoding="utf-8")
        (self.root / "oh-my-opencode-slim" / "p2" / "orchestrator_append.md").write_text("p2 orchestrator " * 10, encoding="utf-8")
        (self.root / "orchestration" / "structure" / "30-build" / "impl.md").write_text("pool: 2\n\nThe implementer.\n", encoding="utf-8")
        self.make_db()

    def write_presets(self):
        (self.root / "oh-my-opencode-slim.json").write_text(json.dumps({"preset": "p1", "presets": self.presets}), encoding="utf-8")

    def make_db(self, loaded=("alpha",)):
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE session_v2 (id TEXT, agent TEXT, time_created INTEGER)")
            conn.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, data TEXT)")
            for index in range(3):
                conn.execute("INSERT INTO session_v2 VALUES (?, 'impl', ?)", (f"s{index}", index))
            conn.execute("INSERT INTO session_v2 VALUES ('r1', 'rev', 5)")
            for index, name in enumerate(loaded):
                part = {"type": "tool", "name": "skill", "state": {"input": {"name": name}}}
                conn.execute("INSERT INTO session_message VALUES (?, 's1', 'assistant', ?)",
                             (f"m{index}", json.dumps({"agent": "impl", "content": [part]})))
            conn.execute("INSERT INTO session_message VALUES ('m9', 'r1', 'assistant', ?)",
                         (json.dumps({"content": [{"type": "tool", "name": "skill",
                                                   "state": {"input": {"name": "alpha"}}}]}),))
            conn.commit()

    def paths(self):
        return bloat.Paths.under(self.root, self.project)

    def bloat_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = bloat.main(["--opencode-dir", str(self.root), "--project", str(self.project),
                               "--home", str(self.home), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def ledger_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = ledger.main([*args, "--home", str(self.home)])
        return code, stdout.getvalue(), stderr.getvalue()

    def plant_oversized(self):
        self.presets["p1"]["impl"]["prompt"] += "\n\n" + "Grow. " * 12_000
        self.write_presets()


class Measure(Scratch):
    def test_fixed_text_is_split_by_source(self):
        rows = bloat.fixed(self.paths())
        impl = {s["source"]: s["chars"] for s in rows["p1"]["impl"]["sources"]}
        agents_md = len((self.root / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertEqual(impl["AGENTS.md"], agents_md)
        self.assertEqual(impl["presets.p1.impl.prompt"], len(self.presets["p1"]["impl"]["prompt"]))
        self.assertEqual(impl["oh-my-opencode-slim/impl_append.md"], len("Append for impl, every preset.\n"))
        self.assertEqual(impl["structure/30-build/impl.md"], len("pool: 2\n\nThe implementer.\n"))
        names = len("alpha") + len("Alpha helps with the first thing.") + len("beta") + len("Beta helps with the second thing.")
        self.assertEqual(impl["skill descriptions (2)"], names)
        self.assertEqual(rows["p1"]["impl"]["chars"], sum(impl.values()))
        self.assertEqual(rows["p1"]["impl"]["tokens"], (rows["p1"]["impl"]["chars"] + 3) // 4)

    def test_orchestrator_counts_other_agents_prompts_and_the_preset_folder_first(self):
        rows = bloat.fixed(self.paths())
        p1 = {s["source"]: s["chars"] for s in rows["p1"]["orchestrator"]["sources"]}
        p2 = {s["source"]: s["chars"] for s in rows["p2"]["orchestrator"]["sources"]}
        self.assertIn("presets.p1.impl.orchestratorPrompt", p1)
        self.assertIn("oh-my-opencode-slim/orchestrator_append.md", p1)
        self.assertEqual(p2.get("oh-my-opencode-slim/p2/orchestrator_append.md"), len("p2 orchestrator " * 10))
        self.assertNotIn("oh-my-opencode-slim/orchestrator_append.md", p2)
        self.assertIn("skill descriptions (3)", p1)  # "*" is every installed skill

    def test_names_the_oversized_prompt_unused_skill_and_duplicate(self):
        self.plant_oversized()
        code, output, errors = self.bloat_cli("--db", str(self.db))
        self.assertEqual(code, 0, errors)
        self.assertIn("largest", output.lower())
        self.assertIn("presets.*.impl.prompt", output)
        self.assertIn("no budget set", output)
        impl = next(line for line in output.splitlines() if line.strip().startswith("impl "))
        self.assertTrue(impl.split(":", 1)[1].strip().startswith("presets.*.impl.prompt"), impl)
        unused = next(line for line in output.splitlines() if line.strip().startswith("impl ["))
        self.assertIn("beta", unused)
        self.assertNotIn("alpha", unused)
        self.assertIn("skills no agent's list names: gamma", output)
        duplicate = next(line for line in output.splitlines() if "saving" in line)
        self.assertIn("AGENTS.md", duplicate)
        self.assertIn("presets.*.impl.prompt", duplicate)
        self.assertIn("in one agent's text", duplicate)
        self.assertIn('"Other project ledger"', output)
        self.assertIn("/elsewhere/projects/other-repo", output)

    def test_never_prints_prompt_text(self):
        for args in ((), ("--json",), ("--db", str(self.db)), ("--json", "--db", str(self.db))):
            code, output, errors = self.bloat_cli(*args)
            self.assertEqual(code, 0, errors)
            self.assertNotIn(SECRET, output)
            self.assertNotIn("You implement", output)
            self.assertNotIn(DUPLICATE[:40], output)

    def test_unused_skills_are_unknown_without_a_database(self):
        report = bloat.measure(self.paths())
        self.assertFalse(report["dead"]["skills"]["known"])
        self.assertIn("unknown", report["dead"]["skills"]["why"])
        self.assertEqual(report["dead"]["skills"]["unlisted"], ["gamma"])
        code, output, _ = self.bloat_cli()
        self.assertIn("unknown: no --db given", output)

    def test_unused_skills_come_from_each_agents_last_sessions(self):
        report = bloat.measure(self.paths(), db=self.db)
        rows = {(r["preset"], r["agent"]): r for r in report["dead"]["skills"]["agents"]}
        self.assertEqual(rows[("p1", "impl")]["unused"], ["beta"])
        self.assertEqual(rows[("p1", "impl")]["sessions"], 3)
        self.assertEqual(rows[("p1", "rev")]["unused"], [])
        self.assertIsNone(rows[("p1", "orchestrator")]["unused"])  # no sessions: unknown, not "all unused"
        report = bloat.measure(self.paths(), db=self.db, sessions=1)  # s2 is the newest impl session, it loaded nothing
        rows = {(r["preset"], r["agent"]): r for r in report["dead"]["skills"]["agents"]}
        self.assertEqual(rows[("p1", "impl")]["unused"], ["alpha", "beta"])

    def test_json_report(self):
        code, output, errors = self.bloat_cli("--json", "--db", str(self.db))
        self.assertEqual(code, 0, errors)
        report = json.loads(output)
        self.assertEqual(report["tokens"], "tokens are estimated as characters / 4")
        self.assertIsNone(report["budget"])
        self.assertEqual(report["defaults"]["budget_chars"], bloat.BUDGET_CHARS)
        self.assertEqual(report["dead"]["foreign_sections"][0]["heading"], "Other project ledger")
        duplicate = report["dead"]["duplicates"][0]
        self.assertEqual(duplicate["sources"], ["AGENTS.md", "presets.*.impl.prompt"])
        self.assertEqual(duplicate["chars"], len(DUPLICATE))
        self.assertTrue(duplicate["same_agent"])

    def test_an_added_agent_file_is_measured(self):
        (self.root / "agents" / "helper.md").write_text("---\ndescription: helps\nmode: subagent\n---\n\nHelp.\n", encoding="utf-8")
        rows = bloat.fixed(self.paths())
        sources = {s["source"] for s in rows["p1"]["helper"]["sources"]}
        self.assertEqual(sources, {"AGENTS.md", "agents/helper.md"})

    def test_paths_inside_the_config_folder_or_project_are_not_foreign(self):
        (self.root / "AGENTS.md").write_text(
            f"# Here\n\nRun {self.root}/orchestration/bin/agentgraph and ~/.config/x/y.\n\n"
            f"# Project\n\nSee {self.project}/src/main.py.\n", encoding="utf-8")
        found = bloat.measure(self.paths())["dead"]["foreign_sections"]
        self.assertEqual([item["heading"] for item in found], ["Here"])  # ~/.config/x is not this config folder
        self.assertEqual(found[0]["paths"], ["~/.config/x/y"])

    def test_accumulation(self):
        (self.home / "runs.jsonl").write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
        (self.root / "orchestration" / "proposals-p2").mkdir()
        (self.root / "orchestration" / "proposals-p2" / "2026-01-01-x.md").write_text("proposal", encoding="utf-8")
        artifacts = self.project / ".opencode" / "artifacts" / "task-a" / "n1" / "r1"
        artifacts.mkdir(parents=True)
        (artifacts / "notes.md").write_text("x" * 100, encoding="utf-8")
        (self.project / ".opencode" / "graph" / "task-a.json").write_text("{}", encoding="utf-8")
        day = self.root / "backups" / "2026-01-01-skill-files"
        for index in range(bloat._MAX_BACKUPS + 2):
            copy = day / f"write-alpha-abc-{index:08d}"
            copy.mkdir(parents=True)
            (copy / "SKILL.md").write_text("old", encoding="utf-8")
            os.utime(copy, ns=(index * 10**9, index * 10**9))
        (self.root / "backups" / "2026-01-01-pre-something").mkdir()
        (self.root / "backups" / "2026-01-01-pre-something" / "AGENTS.md").write_text("by hand", encoding="utf-8")
        acc = bloat.measure(self.paths())["accumulation"]
        memory = next(home for home in acc["homes"] if home["home"] == "memory")
        self.assertEqual(memory["preset"], "ultra-fast-frontier")
        self.assertEqual(next(f for f in memory["files"] if f["name"] == "runs.jsonl")["rows"], 2)
        self.assertEqual(acc["proposals"], [{"folder": "proposals-p2", "proposals": 1, "bytes": 8}])
        self.assertEqual(acc["artifacts"][0]["task"], "task-a")
        self.assertEqual(acc["artifacts"][0]["bytes"], 100)
        self.assertEqual(acc["graphs"], [{"task": "task-a", "bytes": 2}])
        self.assertEqual(acc["backups"]["prune_drops"], 2)
        self.assertEqual(acc["backups"]["prune_bytes"], 6)
        self.assertEqual(acc["backups"]["hand_made"], 1)

    def test_launcher_runs_bloat(self):
        result = subprocess.run([*LAUNCHER, "bloat", "--json", "--opencode-dir", str(self.root),
                                 "--project", str(self.project), "--home", str(self.home)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("p1", json.loads(result.stdout)["presets"])


class Budget(Scratch):
    def test_no_baseline_never_nags(self):
        self.plant_oversized()
        report = ledger.signals(self.home, 10)
        self.assertFalse(report["bloat"]["due"])
        self.assertIsNone(report["bloat"]["budget_chars"])
        code, output, _ = self.ledger_cli("signals", "--no-cost")
        self.assertNotIn("bloat", output)
        self.assertFalse((self.home / bloat.BASELINE).exists())

    def test_baseline_growth_and_reset(self):
        code, output, errors = self.bloat_cli("--set-baseline", "--preset", "p1")
        self.assertEqual(code, 0, errors)
        stored = json.loads((self.home / bloat.BASELINE).read_text(encoding="utf-8"))
        self.assertEqual(stored["preset"], "p1")
        self.assertEqual(stored["budget_chars"], bloat.BUDGET_CHARS)
        self.assertNotIn(SECRET, (self.home / bloat.BASELINE).read_text(encoding="utf-8"))
        code, output, _ = self.ledger_cli("signals", "--no-cost")
        self.assertNotIn("improvement due: bloat", output)

        self.plant_oversized()
        code, output, _ = self.ledger_cli("signals", "--no-cost")
        line = next(line for line in output.splitlines() if line.startswith("improvement due: bloat"))
        self.assertIn("impl", line)
        self.assertIn(f"over the {bloat.BUDGET_CHARS:,} budget", line)
        self.assertIn("since the baseline", line)
        self.assertIn("largest source presets.p1.impl.prompt", line)
        self.assertNotIn(SECRET, output)
        graphs = self.project / ".opencode" / "graph"
        code, output, _ = self.ledger_cli("record", "--dir", str(graphs), "--db", str(self.db))
        self.assertIn("improvement due: bloat", output)
        code, output, _ = self.bloat_cli()
        self.assertIn("improvement due: bloat", output)

        self.assertEqual(self.bloat_cli("--set-baseline", "--preset", "p1")[0], 0)
        code, output, _ = self.ledger_cli("signals", "--no-cost")
        self.assertNotIn("improvement due: bloat", output)
        code, output, _ = self.ledger_cli("record", "--dir", str(graphs), "--db", str(self.db))
        self.assertNotIn("bloat", output)

    def test_growth_under_the_budget_is_flagged_past_the_threshold(self):
        bloat.set_baseline(self.home, self.paths(), preset="p1", budget=10**6, growth=0.5)
        self.presets["p1"]["rev"]["prompt"] += " more" * 4_000
        self.write_presets()
        report = bloat.signal(self.home)
        self.assertTrue(report["due"])
        self.assertEqual([a["agent"] for a in report["agents"]], ["rev"])
        self.assertEqual(report["agents"][0]["reasons"][0][:1], "+")

    def test_home_picks_its_preset(self):
        self.presets["ultra-fast"] = {"impl": {"skills": []}}
        self.write_presets()
        home = self.root / "orchestration" / "memory-o2"
        self.assertEqual(bloat.set_baseline(home, self.paths())["preset"], "ultra-fast")
        self.assertEqual(bloat.set_baseline(self.home, self.paths())["preset"], "p1")  # unknown preset: the active one


class ProposalSize(Scratch):
    def test_proposed_size_is_recorded(self):
        payload = self.home.parent / "improvement.json"
        payload.write_text(json.dumps({"file": "x.md", "summary": "Shorten", "size_change": {"impl": -120}}), encoding="utf-8")
        code, _, errors = self.ledger_cli("improved", "--json-file", str(payload))
        self.assertEqual(code, 0, errors)
        code, _, errors = self.ledger_cli("improved", "--file", "y.md", "--summary", "Trim",
                                          "--size-change", "impl=-40", "--size-change", "rev=+5")
        self.assertEqual(code, 0, errors)
        items = [json.loads(line) for line in (self.home / "improvements.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(items[0]["size_change"], {"impl": -120})
        self.assertEqual(items[1]["size_change"], {"impl": -40, "rev": 5})
        self.assertNotIn("size_actual", items[0])  # no baseline: nothing measured
        code, output, _ = self.ledger_cli("signals", "--all", "--no-cost")
        self.assertIn("Size (characters of fixed text): proposed impl -40, rev +5", output)

    def test_bad_size_change_is_refused(self):
        self.assertEqual(self.ledger_cli("improved", "--file", "y.md", "--summary", "T", "--size-change", "impl")[0], 2)
        payload = self.home.parent / "bad.json"
        payload.write_text(json.dumps({"file": "x.md", "summary": "S", "size_change": {"impl": 1.5}}), encoding="utf-8")
        self.assertEqual(self.ledger_cli("improved", "--json-file", str(payload))[0], 2)
        self.assertFalse((self.home / "improvements.jsonl").exists())

    def test_actual_size_change_is_measured_with_a_baseline(self):
        bloat.set_baseline(self.home, self.paths(), preset="p1")
        before = bloat.fixed(self.paths(), "p1")["p1"]
        self.presets["p1"]["rev"]["prompt"] = "Review."  # 11 -> 7 characters
        self.write_presets()
        first = ledger.improved(self.home, 5, "rev", "Shorter", size_change={"rev": -4})
        self.assertEqual(first["size_actual"], {"rev": -4})
        self.assertEqual(first["fixed_chars"]["rev"], before["rev"]["chars"] - 4)
        self.presets["p1"]["impl"]["orchestratorPrompt"] += " extra"
        self.write_presets()
        second = ledger.improved(self.home, 6, "impl", "Longer")
        self.assertEqual(second["size_actual"], {"orchestrator": 6})  # since the previous measured record
        code, output, _ = self.ledger_cli("signals", "--all", "--no-cost")
        self.assertIn("proposed rev -4; actual rev -4", output)


if __name__ == "__main__":
    unittest.main()
