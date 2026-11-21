"""agentgraph.bench: every test runs on scratch folders, a fake `opencode-cli` (a script that records how it was called
and prints the host's JSON events) and a fake database. No real model call; the real config and database are never named."""
import contextlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from agentgraph import bench

SCHEMA = (
    "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, title TEXT, cost REAL, time_created INT);"
    "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL, seq INT NOT NULL, data TEXT NOT NULL);"
)
MODEL = "anthropic/claude-sonnet-5-5"


def message(i=0, o=0, r=0, cr=0, cw=0, cost=0.0):
    return json.dumps({"cost": cost, "tokens": {"input": i, "output": o, "reasoning": r, "cache": {"read": cr, "write": cw}}})


def make_db(path, sessions, messages):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for row in sessions:
        db.execute("INSERT INTO session_v2 (id, parent_id, agent, title, cost, time_created) VALUES (?,?,?,?,?,?)", row)
    for n, (sid, kind, data) in enumerate(messages):
        db.execute("INSERT INTO session_message VALUES (?,?,?,?,?)", (f"m{n}", sid, kind, n, data))
    db.commit()
    db.close()


def fake_live(base: Path) -> Path:
    live = base / "live" / ".config" / "opencode"
    (live / "agents").mkdir(parents=True)
    (live / "oh-my-opencode-slim" / "normal").mkdir(parents=True)
    (live / "orchestration" / "bin").mkdir(parents=True)
    (live / "orchestration" / "memory").mkdir()
    (live / "orchestration" / "memory-normal").mkdir()
    (live / "orchestration" / ".opencode").mkdir()
    (live / "orchestration" / "proposals").mkdir()
    (live / "orchestration" / "memory" / "runs.jsonl").write_text("LEDGER-SECRET\n")
    (live / "orchestration" / ".opencode" / "x.json").write_text("{}")
    (live / "orchestration" / "bench").mkdir()
    (live / "orchestration" / "bench" / "old.jsonl").write_text("{}\n")
    (live / "orchestration" / "bin" / "agentgraph").write_text(f"#!/bin/sh\nexec {live}/orchestration/run.sh ~/.config/opencode\n")
    (live / "AGENTS.md").write_text(f"Read ~/.config/opencode/AGENTS.md and {live}/orchestration/COSTS.md\n")
    (live / "agents" / "side-chat.md").write_text('---\ndescription: "x"\nmodel: "openai/gpt-6-luna"\nvariant: high\n---\nbody model: keep\n')
    (live / "oh-my-opencode-slim" / "normal" / "orchestrator_append.md").write_text("append ~/.config/opencode/x\n")
    (live / "oh-my-opencode-slim.json").write_text(json.dumps({
        "preset": "normal", "presets": {"normal": {"orchestrator": {"model": "openai/gpt-6-sol", "variant": "high", "skills": ["a"]},
                                                   "oracle": {"model": "anthropic/claude-opus-5-5", "variant": "xhigh"}}},
        "council": {"presets": {"ideas": {"fable": {"model": "openai/x", "variant": "v", "prompt": "p"}}}}}))
    (live / "opencode.json").write_text(json.dumps({
        "model": "openai/gpt-6-sol", "plugins": [{"package": f"{live}/orchestration/desktop-plugin"}],
        "agent": {"explore": {"model": "openai/y", "variant": "z"}},
        "providers": {"anthropic": {"models": {"claude-x": {"settings": {}}}}}}))
    (live / "cli.json").write_text(json.dumps({"plugins": [f"{live}/orchestration/tui-plugin"]}))
    return live


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.live = fake_live(self.base)
        self.root = self.base / "temp"
        self.root.mkdir()
        self.real_home = self.base / "home"
        (self.real_home / ".cache" / "opencode").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def scratch(self, name="s1", **kw):
        return bench.scratch_config(self.root / name, live=self.live, temp_root=self.root, real_home=self.real_home, **kw)


class ScratchConfigTests(Base):
    def test_copy_rewrites_the_live_path_and_leaves_tilde_alone(self):
        info = self.scratch()
        config = Path(info["config"])
        self.assertEqual(config, self.root.resolve() / "s1" / "home" / ".config" / "opencode")
        text = (config / "AGENTS.md").read_text()
        self.assertIn("~/.config/opencode/AGENTS.md", text)             # the prompt text is unchanged: the scratch HOME resolves it
        self.assertIn(f"{config}/orchestration/COSTS.md", text)         # the absolute path points at the scratch copy
        self.assertNotIn(str(self.live), text)
        self.assertIn(str(config), (config / "opencode.json").read_text())
        self.assertIn(str(config), (config / "cli.json").read_text())
        self.assertNotIn(str(self.live), (config / "orchestration" / "bin" / "agentgraph").read_text())
        self.assertEqual(stat.S_IMODE(os.stat(self.root / "s1").st_mode), 0o700)

    def test_personal_folders_are_not_copied_and_the_ledger_is_empty(self):
        config = Path(self.scratch()["config"])
        orch = config / "orchestration"
        for name in (".opencode", "memory-normal", "proposals", "bench"):
            self.assertFalse((orch / name).exists(), name)
        self.assertEqual(list((orch / "memory").iterdir()), [])
        self.assertNotIn("LEDGER-SECRET", "".join(p.read_text() for p in config.rglob("*") if p.is_file()))

    def test_model_pinning_touches_every_model_and_drops_variants(self):
        config = Path(self.scratch()["config"])
        plugin = json.loads((config / "oh-my-opencode-slim.json").read_text())
        self.assertEqual(plugin["presets"]["normal"]["orchestrator"], {"model": MODEL, "skills": ["a"]})
        self.assertEqual(plugin["presets"]["normal"]["oracle"], {"model": MODEL})
        self.assertEqual(plugin["council"]["presets"]["ideas"]["fable"], {"model": MODEL, "prompt": "p"})
        main = json.loads((config / "opencode.json").read_text())
        self.assertEqual((main["model"], main["agent"]["explore"]), (MODEL, {"model": MODEL}))
        self.assertIn("claude-x", main["providers"]["anthropic"]["models"])   # the provider's model table is not a pin
        agent = (config / "agents" / "side-chat.md").read_text()
        self.assertIn(f'model: "{MODEL}"', agent)
        self.assertNotIn("variant", agent)
        self.assertIn("body model: keep", agent)                               # only the front matter changes

    def test_pin_none_keeps_the_models_and_a_custom_model_is_used(self):
        keep = Path(self.scratch("keep", pin_model=None)["config"])
        self.assertEqual(json.loads((keep / "opencode.json").read_text())["model"], "openai/gpt-6-sol")
        other = Path(self.scratch("other", pin_model="openai/gpt-6-luna")["config"])
        self.assertEqual(json.loads((other / "opencode.json").read_text())["model"], "openai/gpt-6-luna")
        with self.assertRaises(bench.BenchError):
            self.scratch("bad", pin_model="not a model")

    def test_hash_is_the_same_for_two_scratch_folders_and_changes_with_the_text(self):
        a, b = self.scratch("a"), self.scratch("b")
        self.assertEqual(a["prompt_hash"], b["prompt_hash"])
        self.assertEqual(len(a["prompt_hash"]), 16)
        (Path(b["config"]) / "AGENTS.md").write_text("changed\n")
        self.assertNotEqual(bench.open_scratch(b["dir"], live=self.live)["hash"], a["prompt_hash"])
        self.assertEqual(bench.open_scratch(a["dir"], live=self.live)["hash"], a["prompt_hash"])

    def test_links_to_the_real_database_and_cache_folders(self):
        data = self.real_home / ".local" / "share" / "opencode"
        data.mkdir(parents=True)
        from unittest import mock
        with mock.patch.object(bench.sources, "DB_PATH", data / "opencode.db"):
            info = self.scratch("linked")
        home = Path(info["home"])
        self.assertEqual((home / ".local" / "share" / "opencode").resolve(), data.resolve())
        self.assertEqual((home / ".cache" / "opencode").resolve(), (self.real_home / ".cache" / "opencode").resolve())

    def test_refuses_bad_destinations(self):
        for target in (self.live / "inside", self.live.parent, "relative/dir", self.base / "outside-temp-root", self.root):
            with self.subTest(target=str(target)), self.assertRaises(bench.BenchError):
                bench.scratch_config(target, live=self.live, temp_root=self.root, real_home=self.real_home)
        self.scratch("taken")
        with self.assertRaises(bench.BenchError):
            self.scratch("taken")

    def test_the_live_files_are_untouched(self):
        before = bench.live_fingerprint(self.live)
        self.scratch()
        self.assertEqual(bench.live_fingerprint(self.live), before)
        self.assertEqual(before["ledger"]["files"], 1)


class RefusalTests(Base):
    def test_no_config_the_live_folder_and_a_plain_folder_are_refused(self):
        with self.assertRaisesRegex(bench.BenchError, "--config DIR is required"):
            bench.open_scratch(None, live=self.live)
        for path in (self.live, self.live / "orchestration", self.live.parent, self.root):
            with self.subTest(path=str(path)), self.assertRaises(bench.BenchError):
                bench.open_scratch(path, live=self.live)
        info = self.scratch()
        self.assertEqual(bench.open_scratch(info["config"], live=self.live)["dir"], Path(info["dir"]))  # the config folder works too
        with self.assertRaises(bench.BenchError):
            bench.open_scratch("relative", live=self.live)

    def test_run_and_probe_refuse_without_config(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(bench.main(["run", "s1-simple", "--label", "x"]), 2)
            self.assertEqual(bench.main(["probe", "explorer", "--label", "x"]), 2)
        self.assertIn("--config DIR is required", err.getvalue())

    def test_command_is_always_standalone_and_the_cli_is_an_absolute_file(self):
        cmd = bench.build_command("/x/opencode-cli", agent="orchestrator", model=MODEL, title="t")
        self.assertEqual(cmd[:4], ["/x/opencode-cli", "run", "--standalone", "--format"])
        self.assertIn("--archived", cmd)
        resumed = bench.build_command("/x/cli", agent="explorer", model=None, title=None, session="ses_abc1")
        self.assertIn("--standalone", resumed)
        self.assertNotIn("--archived", resumed)
        for bad in ({"agent": "../x"}, {"agent": "a b"}, {"agent": "ok", "model": "--x"}, {"agent": "ok", "session": "x"}):
            with self.assertRaises(bench.BenchError):
                bench.build_command("/x/cli", **{"model": MODEL, "title": "t", **bad})
        with self.assertRaises(bench.BenchError):
            bench.executable_path("opencode-cli")
        with self.assertRaises(bench.BenchError):
            bench.executable_path(str(self.base / "missing"))

    def test_run_env_sets_the_scratch_home_and_drops_the_extra_config_switches(self):
        info = self.scratch()
        scratch = bench.open_scratch(info["dir"], live=self.live)
        env = bench.run_env(scratch, Path("/w"), {"OPENCODE_CONFIG_DIR": "x", "OPENCODE_CONFIG": "y", "OPENCODE_CONFIG_CONTENT": "{}",
                                                  "HOME": "/real", "KEEP": "1"})
        self.assertEqual(env["HOME"], str(Path(info["dir"]) / "home"))
        self.assertEqual(env["XDG_CONFIG_HOME"], str(Path(info["dir"]) / "home" / ".config"))
        self.assertTrue(env["AGENTGRAPH_HOME"].endswith("opencode/orchestration/memory"))
        self.assertEqual((env["OPENCODE_CONFIG_CONTENT"], env["KEEP"]), ("{}", "1"))
        self.assertNotIn("OPENCODE_CONFIG_DIR", env)
        self.assertNotIn("OPENCODE_CONFIG", env)


FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(here, "plan.json")))
log = os.path.join(here, "calls.jsonl")
n = sum(1 for _ in open(log)) if os.path.exists(log) else 0
step = plan[min(n, len(plan) - 1)]
args = sys.argv[1:]
message = sys.stdin.read()
with open(log, "a") as f:
    f.write(json.dumps({"args": args, "message": message, "cwd": os.getcwd(), "home": os.environ.get("HOME"),
                        "xdg": os.environ.get("XDG_CONFIG_HOME"), "ledger": os.environ.get("AGENTGRAPH_HOME")}) + "\n")
for name, body in step.get("write", {}).items():
    path = os.path.join(os.getcwd(), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(body)
if step.get("sleep"):
    time.sleep(step["sleep"])
sid = args[args.index("--session") + 1] if "--session" in args else "ses_root"
print(json.dumps({"type": "step_start", "sessionID": sid, "part": {}}))
if step.get("error"):
    print(json.dumps({"type": "error", "sessionID": sid, "error": {"data": {"message": step["error"]}}}))
sys.exit(step.get("exit", 0))
'''


def graph(nodes, events, status="running"):
    return json.dumps({"task": "kv", "status": status, "nodes": [{"id": i, "status": s} for i, s in nodes],
                       "events": [{"time": 1, "node": n, "event": "dispatched", "note": ""} for n in events]})


class RunTests(Base):
    def setUp(self):
        super().setUp()
        info = self.scratch()
        self.scratch_dir = info["dir"]
        self.cli_dir = self.base / "cli"
        self.cli_dir.mkdir()
        self.cli = self.cli_dir / "opencode-cli"
        self.cli.write_text(FAKE)
        self.cli.chmod(0o755)
        self.results = self.base / "results"
        self.scenarios = self.base / "scenarios"
        s = self.scenarios / "t1"
        (s / "fixture").mkdir(parents=True)
        (s / "hidden").mkdir()
        (s / "fixture" / "answer.txt").write_text("wrong\n")
        (s / "hidden" / "expect.txt").write_text("right\n")
        (s / "scenario.json").write_text(json.dumps({
            "agent": "orchestrator", "prompt": "fix the answer", "timeout": 20,
            "check": "diff -q answer.txt expect.txt >/dev/null && echo matches"}))
        self.db = self.base / "opencode.db"
        self.title = "agentgraph-bench: t1 lab r1"
        make_db(self.db, [
            ("ses_root", None, "orchestrator", self.title, 0.5, 1),
            ("ses_w1", "ses_root", "impl-openai", "w", 0.2, 2),
            ("ses_w2", "ses_root", "impl-openai", "w", 0.1, 3),
            ("ses_g1", "ses_w1", "explorer", "g", 0.05, 4),
            ("ses_other", None, "orchestrator", "unrelated", 9.0, 5)], [
            ("ses_root", "user", "{}"), ("ses_root", "assistant", message(10, 100, 5, 0, 20_000, 0.2)),
            ("ses_root", "assistant", message(5, 50, 0, 20_000, 1_000, 0.3)),
            ("ses_w1", "user", "{}"), ("ses_w1", "user", "{}"), ("ses_w1", "assistant", message(2, 30, 0, 8_000, 500, 0.2)),
            ("ses_w2", "user", "{}"), ("ses_w2", "assistant", message(3, 10, 0, 0, 9_000, 0.1)),
            ("ses_g1", "user", "{}"), ("ses_g1", "assistant", message(1, 5, 0, 3_000, 0, 0.05)),
            ("ses_other", "assistant", message(999, 999, 0, 0, 0, 9.0))])

    def plan(self, steps):
        (self.cli_dir / "plan.json").write_text(json.dumps(steps))

    def calls(self):
        return [json.loads(line) for line in (self.cli_dir / "calls.jsonl").read_text().splitlines()]

    def run_t1(self, **kw):
        return bench.run_scenarios(["t1"], label="lab", config=self.scratch_dir, executable=str(self.cli), db_path=self.db,
                                   results=self.results, scenarios_root=self.scenarios, live=self.live, **kw)

    def test_row_assembly_from_the_fake_cli_and_database(self):
        self.plan([{"write": {"answer.txt": "right\n"}}])
        rows = self.run_t1(reps=1)
        row = rows[0]
        call = self.calls()[0]
        self.assertEqual(call["message"], "fix the answer")
        self.assertEqual(call["home"], str(Path(self.scratch_dir) / "home"))
        self.assertEqual(call["xdg"], str(Path(self.scratch_dir) / "home" / ".config"))
        self.assertTrue(call["cwd"].startswith(str(Path(self.scratch_dir) / "work")))
        for flag in ("--standalone", "--archived", "--auto"):
            self.assertIn(flag, call["args"])
        self.assertEqual(call["args"][call["args"].index("--title") + 1], self.title)
        self.assertEqual(call["args"][call["args"].index("--model") + 1], MODEL)
        self.assertEqual((row["scenario"], row["label"], row["rep"], row["agent"], row["model"]), ("t1", "lab", 1, "orchestrator", MODEL))
        self.assertEqual(row["root_session"], "ses_root")
        self.assertEqual(row["sessions"], 4)                                  # the root and its three descendants, not the other root
        self.assertEqual(row["agent_calls"], {"impl-openai": 3, "explorer": 1})  # a worker session counts one call per user message
        self.assertEqual(row["turns"], 5)
        self.assertEqual(row["tokens"], {"input": 21, "output": 195, "reasoning": 5, "cache_read": 31_000, "cache_write": 30_500})
        self.assertEqual(row["first_turn_tokens"], {"orchestrator": 20_010, "impl-openai": 8_502, "explorer": 3_001})
        self.assertEqual(row["max_context"], 21_005)
        self.assertAlmostEqual(row["cost"], 0.85)
        self.assertAlmostEqual(row["cost_sessions"], 0.85)
        self.assertEqual(row["check"], {"passed": True, "detail": "matches"})
        self.assertEqual((row["exit_code"], row["error"], row["recovery"]), (0, "", None))
        self.assertEqual(len(row["config_hash"]), 16)
        saved = [json.loads(line) for line in (self.results / "lab.jsonl").read_text().splitlines()]
        self.assertEqual(saved, [json.loads(json.dumps(row))])

    def test_a_failing_check_an_error_and_a_missing_session_are_recorded(self):
        self.plan([{"exit": 1, "error": "model not found"}, {}])
        row = self.run_t1()[0]
        self.assertFalse(row["check"]["passed"])
        self.assertEqual(row["exit_code"], 1)
        self.assertIn("model not found", row["error"])
        make_db(self.base / "empty.db", [], [])
        rows = bench.run_scenarios(["t1"], label="lab2", config=self.scratch_dir, executable=str(self.cli), db_path=self.base / "empty.db",
                                   results=self.results, scenarios_root=self.scenarios, live=self.live)
        self.assertEqual((rows[0]["sessions"], rows[0]["cost"]), (0, 0.0))
        self.assertIn("not found in the database", rows[0]["error"])

    def test_the_check_runs_in_the_scratch_environment_with_only_the_database_path_passed_through(self):
        t2 = self.scenarios / "t2"
        t2.mkdir()
        (t2 / "scenario.json").write_text(json.dumps({
            "agent": "orchestrator", "prompt": "x", "timeout": 20,
            "check": 'echo "${HOME#*/temp/}|${OPENCODE_CONFIG_DIR-unset}|${P4_DB##*/}|${AGENTGRAPH_HOME#*/temp/}"'}))
        self.plan([{}])
        with unittest.mock.patch.dict(os.environ, {"OPENCODE_CONFIG_DIR": "/real/config"}):
            rows = bench.run_scenarios(["t2"], label="lab", config=self.scratch_dir, executable=str(self.cli), db_path=self.db,
                                       results=self.results, scenarios_root=self.scenarios, live=self.live)
        name = Path(self.scratch_dir).name
        self.assertEqual(rows[0]["check"]["detail"], f"{name}/home|unset|opencode.db|{name}/home/.config/opencode/orchestration/memory")

    def test_append_row_refuses_a_label_that_is_not_a_plain_name(self):
        for label in ("../x", "a/b", "", None, "-x"):
            with self.subTest(label=label), self.assertRaises(bench.BenchError):
                bench.append_row({"label": label, "scenario": "s"}, self.results)
        self.assertFalse(self.results.exists() and any(self.results.iterdir()))
        self.assertFalse((self.base / "x.jsonl").exists())

    def test_probe_rows_have_first_turn_tokens_and_no_check(self):
        make_db(self.base / "p.db", [("ses_root", None, "explorer", "agentgraph-bench: probe-explorer pl r1", 0.01, 1)],
                [("ses_root", "user", "{}"), ("ses_root", "assistant", message(12, 1, 0, 0, 15_000, 0.01))])
        self.plan([{}])
        rows = bench.run_probes(["explorer"], label="pl", config=self.scratch_dir, executable=str(self.cli), db_path=self.base / "p.db",
                                results=self.results, live=self.live)
        call = self.calls()[0]
        self.assertEqual(call["message"], bench.PROBE_PROMPT)
        self.assertEqual(call["args"][call["args"].index("--agent") + 1], "explorer")
        self.assertNotIn("--auto", call["args"])
        self.assertEqual((rows[0]["scenario"], rows[0]["probe_agent"]), ("probe", "explorer"))
        self.assertEqual(rows[0]["first_turn_tokens"], {"explorer": 15_012})
        self.assertIsNone(rows[0]["check"]["passed"])

    def test_budget_stops_before_the_next_rep(self):
        self.plan([{}])
        with self.assertRaisesRegex(bench.BenchError, "budget"):
            self.run_t1(reps=3, budget=0.5)
        self.assertEqual(len((self.results / "lab.jsonl").read_text().splitlines()), 1)

    def test_interrupt_then_resume_records_the_recovery(self):
        done = graph([("n1", "passed"), ("n2", "passed"), ("n3", "passed")], ["n1", "n2", "n3"], "passed")
        half = graph([("n1", "passed"), ("n2", "running"), ("n3", "pending")], ["n1", "n2"])
        self.plan([{"write": {".opencode/graph/kv.json": half, "answer.txt": "wrong2\n"}, "sleep": 30},
                   {"write": {".opencode/graph/kv.json": done, "answer.txt": "right\n"}}])
        row = self.run_t1(interrupt_after=1)[0]
        first, second = self.calls()
        self.assertNotIn("--session", first["args"])
        self.assertEqual(second["args"][second["args"].index("--session") + 1], "ses_root")
        self.assertEqual(second["message"], bench.RESUME_PROMPT)
        self.assertNotIn("--archived", second["args"])
        self.assertTrue(row["check"]["passed"])
        self.assertEqual(row["recovery"], {"interrupted_at": row["recovery"]["interrupted_at"], "nodes_passed_before": 1, "nodes_total": 3,
                                           "nodes_dispatched_again": 0, "nodes_dispatched_after": 1, "resumed_ok": True})
        self.assertGreaterEqual(row["recovery"]["interrupted_at"], 1.0)

    def test_recovery_accounting_counts_passed_nodes_dispatched_again(self):
        before = {"passed": ["g:n1", "g:n2"], "dispatched": {"g:n1": 1, "g:n2": 1, "g:n3": 1}, "nodes": 4}
        after = {"passed": ["g:n1"], "dispatched": {"g:n1": 1, "g:n2": 2, "g:n3": 2, "g:n4": 1}, "nodes": 4, "status": "running"}
        row = bench.recovery_row(before, after, 12.34, False)
        self.assertEqual((row["nodes_passed_before"], row["nodes_dispatched_again"], row["nodes_dispatched_after"], row["resumed_ok"]),
                         (2, 1, 3, False))
        self.assertEqual(row["interrupted_at"], 12.3)

    def test_graph_state_reads_the_work_folders_graph_files(self):
        work = self.base / "w"
        (work / ".opencode" / "graph").mkdir(parents=True)
        (work / ".opencode" / "graph" / "a.json").write_text(graph([("n1", "passed"), ("n2", "pending")], ["n1", "n1"]))
        (work / ".opencode" / "graph" / "bad.json").write_text("{not json")
        state = bench.graph_state(work)
        self.assertEqual((state["passed"], state["nodes"], state["dispatched"], state["events"]), (["a:n1"], 2, {"a:n1": 2}, 2))
        self.assertEqual(bench.graph_state(self.base / "none")["nodes"], 0)


class ReportTests(Base):
    def rows(self, label, scenario, values, **kw):
        out = []
        for n, (cost, cache_read, passed) in enumerate(values, 1):
            out.append({"scenario": scenario, "label": label, "rep": n, "wall_seconds": 10.0 * n, "cost": cost, "turns": 4,
                        "agent_calls": {"impl-openai": 2, "explorer": 1}, "max_context": 30_000, "check": {"passed": passed, "detail": ""},
                        "tokens": {"input": 10, "output": 100, "reasoning": 0, "cache_read": cache_read, "cache_write": 5_000},
                        "first_turn_tokens": {}, **kw})
        return out

    def write(self, label, rows):
        for row in rows:
            bench.append_row(row, self.base / "res")

    def test_summaries_are_medians_and_percent_changes_are_right(self):
        self.write("a", self.rows("a", "s1", [(1.0, 100_000, True), (3.0, 300_000, True), (2.0, 200_000, False)]))
        self.write("b", self.rows("b", "s1", [(1.0, 50_000, True), (1.0, 150_000, True), (1.0, 100_000, True)]))
        summary = bench.summarize(bench.read_rows("a", self.base / "res"))
        self.assertEqual((summary["cost"], summary["cache_read"], summary["calls"], summary["wall_seconds"]), (2.0, 200_000, 3, 20.0))
        self.assertEqual(summary["passed"], "2/3")
        self.assertEqual(bench.percent(200, 100), "-50.0%")
        self.assertEqual(bench.percent(0, 0), "0%")
        self.assertEqual(bench.percent(0, 5), "new")
        self.assertEqual(bench.percent(None, 5), "-")
        text = bench.report("a", "b", self.base / "res")
        self.assertIn("== s1   reps 3 / 3", text)
        cost_line = next(line for line in text.splitlines() if line.startswith("cost $"))
        self.assertTrue(cost_line.rstrip().endswith("-50.0%"), cost_line)
        self.assertIn("3/3", next(line for line in text.splitlines() if line.startswith("check passed")))
        single = bench.report("a", None, self.base / "res")
        self.assertNotIn("change", single)

    def test_probe_rows_report_the_first_turn_tokens_and_recovery_is_summarised(self):
        for label, tokens in (("a", 26_000), ("b", 26_500)):
            bench.append_row({"scenario": "probe", "probe_agent": "orchestrator", "label": label, "rep": 1, "first_turn_tokens": {"orchestrator": tokens},
                              "check": {"passed": None}}, self.base / "res")
        text = bench.report("a", "b", self.base / "res")
        self.assertIn("== probe/orchestrator", text)
        line = next(line for line in text.splitlines() if line.startswith("1st orchestrator"))
        self.assertTrue(line.rstrip().endswith("+1.9%"), line)
        summary = bench.summarize([{"recovery": {"resumed_ok": True, "nodes_dispatched_again": 1}},
                                   {"recovery": {"resumed_ok": False, "nodes_dispatched_again": 3}}])
        self.assertEqual(summary["recovery"], "1/2 ok, 2.0 dispatched again")

    def test_a_missing_label_and_a_bad_label_are_refused(self):
        with self.assertRaises(bench.BenchError):
            bench.read_rows("nothing", self.base / "res")
        with self.assertRaises(bench.BenchError):
            bench.read_rows("../x", self.base / "res")


class ScenarioTests(unittest.TestCase):
    def test_shipped_scenarios_load_and_have_fixture_and_hidden_tests(self):
        names = bench.list_scenarios()
        self.assertTrue({"s1-simple", "s2-parallel", "s3-workflow"} <= set(names))
        for name in names:
            sc = bench.load_scenario(name)
            self.assertTrue((sc["dir"] / "fixture").is_dir() and any((sc["dir"] / "hidden").rglob("*.py")), name)
            self.assertEqual(sc["agent"], "orchestrator")
        with self.assertRaises(bench.BenchError):
            bench.load_scenario("../etc")
        with self.assertRaises(bench.BenchError):
            bench.load_scenario("nope")


class WeightedTokensTests(unittest.TestCase):
    def test_cache_reads_are_cheap_writes_dear_and_reasoning_counts_as_output(self):
        row = {"tokens": {"input": 10, "cache_read": 1000, "cache_write": 8, "output": 3, "reasoning": 1}}
        self.assertEqual(bench.weighted_tokens(row), 10 + 100 + 10 + 20)

    def test_a_row_without_all_counts_has_no_weighted_tokens(self):
        for row in ({}, {"tokens": None}, {"tokens": {"input": 1, "output": 1}}, {"tokens": {"input": -1, "cache_read": 0, "cache_write": 0, "output": 0}},
                    {"tokens": {"input": True, "cache_read": 0, "cache_write": 0, "output": 0}}):
            with self.subTest(row=row):
                self.assertIsNone(bench.weighted_tokens(row))


if __name__ == "__main__":
    unittest.main()
