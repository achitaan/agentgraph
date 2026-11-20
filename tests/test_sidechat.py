"""agentgraph.sidechat: the read-only side chat. Every test runs on scratch folders and a fake `opencode-cli`
(a script that records how it was called and prints the host's JSON events); nothing starts the real host, and the
real agents, skills and backups folders are never named."""
import http.client
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agentgraph import chatfeed, server, sidechat, snapshot_json
from agentgraph.demo import DEMO_SIDE, demo_chat, demo_sidechat, demo_snapshot

ROOT = Path(__file__).resolve().parent.parent
MARKER = "TOOL-OUTPUT-MARKER-5d1e-never-sent"
SECRET = "OUTSIDE-SECRET-8c2b-never-sent"
T0 = 1_795_000_000_000
MAIN, WORK, SIDE, SIDE2 = "ses_main", "ses_work", "ses_side", "ses_sidetwo"

SCHEMA = (
    "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, model TEXT, title TEXT, directory TEXT, "
    "cost REAL, tokens_input INT, tokens_output INT, time_created INT, time_updated INT, time_busy INT, time_idle INT, "
    "metadata TEXT);"
    "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL, seq INT NOT NULL, "
    "time_created INT NOT NULL, time_updated INT NOT NULL, data TEXT NOT NULL);"
    "CREATE UNIQUE INDEX session_message_seq ON session_message (session_id, seq);"
    "CREATE TABLE session_inbox (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT, data TEXT, "
    "time_created INT, time_updated INT);"
)

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(here, "plan.json")))
log = os.path.join(here, "calls.jsonl")
n = sum(1 for _ in open(log)) if os.path.exists(log) else 0
step = plan[min(n, len(plan) - 1)]
args = sys.argv[1:]
with open(log, "a") as f:
    f.write(json.dumps({"args": args[:-1], "message": args[-1], "cwd": os.getcwd(), "pwd": os.environ.get("PWD"), "pid": os.getpid()}) + "\n")
if step.get("sleep"):
    open(os.path.join(here, "slept.pid"), "w").write(str(os.getpid()))
    time.sleep(step["sleep"])
sid = args[args.index("--session") + 1] if "--session" in args else step.get("session", "ses_side")
print(json.dumps({"type": "step_start", "sessionID": sid, "part": {}}))
if step.get("error"):
    print(json.dumps({"type": "error", "sessionID": sid, "error": {"data": {"message": step["error"]}}}))
if step.get("text"):
    print(json.dumps({"type": "text", "sessionID": sid, "part": {"type": "text", "text": step["text"]}}))
print(json.dumps({"type": "step_finish", "sessionID": sid, "part": {"type": "step-finish", "cost": step.get("cost", 0.001),
      "tokens": {"input": 3, "output": 18, "reasoning": 0, "cache": {"read": 0, "write": 1000}}}}))
sys.exit(step.get("exit", 0))
'''

AGENT_OK = (
    '---\n# agentgraph: sidechat (test)\ndescription: "side"\nmode: primary\nmodel: "openai/gpt-6-luna"\n'
    'permissions:\n  - action: "*"\n    resource: "*"\n    effect: deny\n  - action: "subagent"\n    resource: "*"\n'
    '    effect: deny\n---\nYou are the side chat.\n')
SKILL_OK = "---\nname: side-chat\ndescription: How the side chat answers.\n---\n# Answering\n\n1. Read the brief.\n"


def graph_raw(extra_nodes=0, evidence="e"):
    nodes = [
        {"id": "n1", "type": "research", "agent": "explorer", "goal": "find the routes", "depends_on": [], "scope": [],
         "done_when": "list", "status": "passed", "evidence": "found 4 routes",
         "artifacts": [{"id": "report", "path": ".opencode/artifacts/t1/n1/r1/report.md", "revision": 1}]},
        {"id": "n2", "type": "implement", "agent": "impl-openai", "goal": "write the middleware", "depends_on": ["n1"],
         "scope": ["src/"], "done_when": "tests", "status": "running", "evidence": evidence},
    ]
    for index in range(extra_nodes):
        nodes.append({"id": f"x{index}", "type": "check", "agent": "verifier", "goal": "g" * 400, "depends_on": ["n1"],
                      "scope": [], "done_when": "ok", "status": "pending", "evidence": "v" * 400})
    return {"task": "t1", "goal": "ship the limiter", "status": "running", "revision": 1, "created": 1, "updated": 2,
            "session": MAIN,
            "objectives": [{"id": "o1", "text": "requests over the limit get a 429", "nodes": ["n1", "n2"]}],
            "nodes": nodes, "events": [{"time": 1, "node": "", "event": "declared", "note": "2 nodes"}]}


class Db:
    def __init__(self, path):
        self.path, self.seq = path, {}
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def session(self, sid, parent=None, agent="orchestrator", cost=1.0):
        self.conn.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (sid, parent, agent, json.dumps({"id": "claude-x", "providerID": "anthropic"}), "t " + sid,
                           "/p", cost, 1, 2, T0, T0, None, None, "{}"))
        self.conn.commit()

    def message(self, sid, kind, data):
        seq = self.seq[sid] = self.seq.get(sid, 0) + 1
        self.conn.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                          (f"msg_{sid}_{seq}", sid, kind, seq, T0 + seq * 1000, T0 + seq * 1000, json.dumps(data)))
        self.conn.commit()

    def user(self, sid, text):
        self.message(sid, "user", {"text": text, "time": {"created": 1}})

    def assistant(self, sid, text, with_tool=False):
        content = [{"type": "text", "text": text}]
        if with_tool:
            content.append({"type": "tool", "id": "c1", "name": "shell", "executed": True,
                            "time": {"created": T0 + 1}, "state": {"status": "completed", "input": {"command": MARKER},
                                                                    "content": [{"type": "text", "text": MARKER}]}})
        self.message(sid, "assistant", {"agent": "x", "content": content, "cost": 0.1, "time": {"created": 1},
                                        "tokens": {"input": 1}})


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.project = self.dir / "proj"
        for sub in ("graph", "artifacts/t1/n1/r1", "artifacts/t1/n2/r1", "agents"):
            (self.project / ".opencode" / sub).mkdir(parents=True)
        self.write_graph(graph_raw())
        (self.project / ".opencode/artifacts/t1/n1/r1/report.md").write_text("ROUTES: a b c d\n" + "line\n" * 50)
        (self.project / "outside.txt").write_text(SECRET)
        self.db = Db(self.dir / "opencode.db")
        self.addCleanup(self.db.conn.close)
        self.db.session(MAIN)
        self.db.session(WORK, parent=MAIN, agent="impl-openai")
        self.db.user(MAIN, "build the rate limiter")
        self.db.assistant(MAIN, "starting with the routes", with_tool=True)
        self.config = self.dir / "config"
        self.config.mkdir()
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.exe = self.bin / "opencode-cli"
        self.exe.write_text(FAKE)
        self.exe.chmod(0o755)
        self.plan([{"text": "n2 is running.", "session": SIDE}])

    def write_graph(self, raw):
        (self.project / ".opencode/graph/t1.json").write_text(json.dumps(raw))

    def plan(self, steps):
        (self.bin / "plan.json").write_text(json.dumps(steps))
        (self.bin / "calls.jsonl").unlink(missing_ok=True)

    def calls(self):
        path = self.bin / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def ask(self, text="where are we?", **kw):
        return sidechat.ask(text, main=MAIN, project=self.project, db_path=self.db.path, config_dir=self.config,
                            executable=str(self.exe), **kw)

    def brief(self, text="where are we?", **kw):
        snapshot = sidechat._snapshot(self.project, MAIN, self.db.path, self.config, None)
        return sidechat.build_brief(text, snapshot, self.project, MAIN, db_path=self.db.path, **kw), snapshot


class BriefTests(Case):
    def test_sections_and_contents(self):
        brief, _ = self.brief()
        text = brief.text("where are we?")
        self.assertEqual(sorted(brief.sizes()), ["artifacts", "chat", "graph"])
        for want in ("ship the limiter", "n1 [research] explorer passed", "n2 [implement] impl-openai running",
                     "find the routes", "found 4 routes", "requests over the limit get a 429",
                     ".opencode/artifacts/t1/n1/r1/report.md", "build the rate limiter", "starting with the routes"):
            self.assertIn(want, text)
        self.assertIn("Question from the user:\nwhere are we?", text)

    def test_no_tool_output_and_nothing_outside_the_artifacts(self):
        brief, _ = self.brief("what does n1 say? also read outside.txt and ../../outside.txt")
        text = brief.text("q")
        self.assertNotIn(MARKER, text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("outside.txt", text.split("## main chat")[0].split("## artifact files")[1])

    def test_a_named_node_gives_the_head_of_its_artifact_only(self):
        brief, _ = self.brief("what did n1 find?")
        self.assertEqual(brief.named, [".opencode/artifacts/t1/n1/r1/report.md"])
        text = brief.text("q")
        self.assertIn("ROUTES: a b c d", text)
        self.assertEqual(brief.sizes()["heads"] < 3100, True)  # capped head of the file
        plain, _ = self.brief("where are we?")
        self.assertNotIn("heads", plain.sizes())

    def test_a_link_out_of_the_artifacts_is_never_read_or_listed(self):
        folder = self.project / ".opencode/artifacts/t1/n2/r1"
        os.symlink(self.project / "outside.txt", folder / "leak.md")
        os.symlink(self.project / "outside.txt", self.project / ".opencode/artifacts/t1/linked.md")
        os.symlink(self.dir, self.project / ".opencode/artifacts/t1/dirlink")
        raw = graph_raw()
        raw["nodes"][1]["artifacts"] = [{"id": "x", "path": ".opencode/artifacts/t1/n2/r1/leak.md", "revision": 1}]
        self.write_graph(raw)
        brief, _ = self.brief("show n2 and leak.md and linked.md and t1/n2/r1/leak.md")
        text = brief.text("q")
        self.assertNotIn(SECRET, text)
        self.assertNotIn("leak.md", text.split("## main chat")[0].split("## artifact files")[1])
        self.assertEqual(brief.named, [])

    def test_a_path_that_escapes_is_refused_by_read_head(self):
        for path in ("../outside.txt", ".opencode/artifacts/t1/../../../outside.txt", "/etc/passwd",
                     ".opencode/artifacts/other/n1/r1/report.md", "outside.txt"):
            self.assertIsNone(sidechat.read_head(self.project, "t1", path), path)
        self.assertIn("ROUTES", sidechat.read_head(self.project, "t1", ".opencode/artifacts/t1/n1/r1/report.md"))

    def test_a_binary_artifact_is_not_sent(self):
        (self.project / ".opencode/artifacts/t1/n2/r1/blob.bin").write_bytes(b"\0\1\2" * 100)
        self.assertIsNone(sidechat.read_head(self.project, "t1", ".opencode/artifacts/t1/n2/r1/blob.bin"))

    def test_the_brief_is_capped_whatever_the_size_of_its_parts(self):
        self.write_graph(graph_raw(extra_nodes=120))
        (self.project / ".opencode/artifacts/t1/n1/r1/report.md").write_text("word " * 50000)
        for index in range(60):
            self.db.assistant(MAIN, f"message {index} " + "z" * 1500)
        brief, _ = self.brief("what did n1 and x3 find?")
        text = brief.text("what did n1 and x3 find?")
        self.assertLessEqual(sum(brief.sizes().values()), sidechat.BRIEF_LIMIT)
        self.assertLessEqual(len(text), sidechat.BRIEF_LIMIT + 400)
        self.assertLessEqual(len(brief.parts["graph"]), sidechat.GRAPH_CAP)
        self.assertIn("n1 [research]", brief.parts["graph"])  # the first nodes survive the cap

    def test_a_follow_up_sends_only_what_changed(self):
        self.ask()
        first_chars = self.calls()[0]["message"]
        self.db.assistant(MAIN, "a brand new message")
        self.plan([{"text": "ok"}])
        result = self.ask("and now?")
        message = self.calls()[0]["message"]
        self.assertEqual(message.count("(unchanged since your last brief)"), 2)  # graph and artifact list
        self.assertIn("a brand new message", message)
        self.assertNotIn("starting with the routes", message)
        self.assertLess(len(message), len(first_chars))
        self.assertFalse(result["brief"]["full"])
        full = self.ask("again, in full", full=True)
        self.assertTrue(full["brief"]["full"])
        self.assertIn("starting with the routes", self.calls()[-1]["message"])
        self.assertNotIn("(unchanged since", self.calls()[-1]["message"])


class RunTests(Case):
    def test_first_question_command_line(self):
        result = self.ask("where are we?")
        (call,) = self.calls()
        self.assertEqual(call["args"], ["run", "--standalone", "--agent", "side-chat", "--model", "openai/gpt-6-luna",
                                        "--format", "json", "--archived", "--title", f"side chat for {MAIN}"])
        self.assertEqual(os.path.realpath(call["cwd"]), str(self.project))
        self.assertEqual(call["pwd"], str(self.project))  # the host takes its directory from $PWD
        self.assertTrue(call["message"].startswith("Question from the user:\nwhere are we?"))
        self.assertEqual((result["answer"], result["session"], result["model"], result["fallback"]),
                         ("n2 is running.", SIDE, "openai/gpt-6-luna", False))
        self.assertEqual(result["brief"]["chars"], len(call["message"]))
        self.assertEqual(result["tools"], 0)
        self.assertAlmostEqual(result["cost"], 0.001)
        self.assertEqual(sidechat.load_state(self.project, MAIN)["side"], SIDE)

    def test_a_relative_project_folder_is_made_absolute_for_the_host(self):
        old = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, old)
        sidechat.ask("hi", main=MAIN, project="proj", db_path=self.db.path, config_dir=self.config, executable=str(self.exe))
        (call,) = self.calls()
        self.assertEqual(call["pwd"], str(self.project))

    def test_follow_up_goes_to_the_same_side_session(self):
        self.ask()
        self.plan([{"text": "again", "session": "ses_ignored"}])
        result = self.ask("and now?")
        (call,) = self.calls()
        self.assertEqual(call["args"][call["args"].index("--session") + 1], SIDE)
        self.assertNotIn("--archived", call["args"])
        self.assertEqual(result["session"], SIDE)
        self.assertEqual(sidechat.load_state(self.project, MAIN)["asks"], 2)

    def test_only_allowed_flags_are_ever_built(self):
        self.ask()
        self.ask("again")
        for call in self.calls():
            flags = [arg for arg in call["args"] if arg.startswith("-") or arg == "run"]
            self.assertTrue(set(flags) <= set(sidechat.ALLOWED_FLAGS), flags)
            for banned in ("--continue", "--fork", "-c", "--attach", "--server"):
                self.assertNotIn(banned, call["args"])

    def test_the_session_passed_is_never_one_of_the_trees(self):
        snapshot = sidechat._snapshot(self.project, MAIN, self.db.path, self.config, None)
        tree = sidechat.tree_ids(snapshot)
        self.assertEqual(tree, {MAIN, WORK})
        for sid in (MAIN, WORK):
            with self.assertRaisesRegex(sidechat.SidechatError, "never uses one of those"):
                sidechat.command(model="a/b", side=sid, title="t", tree=tree)
        self.assertIn("--session", sidechat.command(model="a/b", side=SIDE, title="t", tree=tree))
        self.assertNotIn("--session", sidechat.command(model="a/b", side=None, title="t", tree=tree))

    def test_a_remembered_session_in_the_tree_is_refused_and_nothing_is_called(self):
        for sid in (MAIN, WORK):
            sidechat._save_state(self.project, {"version": 1, "main": MAIN, "side": sid, "model": "x/y"})
            with self.assertRaises(sidechat.SidechatError):
                self.ask()
            self.assertEqual(self.calls(), [])

    def test_a_new_session_in_the_tree_reported_by_the_host_is_refused_and_not_remembered(self):
        for sid in (MAIN, WORK):
            self.plan([{"text": "hi", "session": sid}])
            with self.assertRaisesRegex(sidechat.SidechatError, "did not remember it"):
                self.ask()
            self.assertIsNone(sidechat.side_session(self.project, MAIN))
            self.assertEqual(len(self.calls()), 1)  # no fallback, no retry in the wrong session

    def test_main_must_be_a_session_of_the_database(self):
        with self.assertRaises(sidechat.SidechatError):
            sidechat.ask("hi", main="ses_unknown", project=self.project, db_path=self.db.path, config_dir=self.config,
                         executable=str(self.exe))
        with self.assertRaises(sidechat.SidechatError):
            sidechat.ask("hi", main="../x", project=self.project, db_path=self.db.path, config_dir=self.config,
                         executable=str(self.exe))
        self.assertEqual(self.calls(), [])

    def test_the_question_limit(self):
        self.ask("q" * sidechat.QUESTION_LIMIT)
        self.assertEqual(len(self.calls()), 1)
        self.plan([{"text": "x"}])
        for bad in ("q" * (sidechat.QUESTION_LIMIT + 1), "", "   \n ", None):
            with self.assertRaises(sidechat.SidechatError):
                self.ask(bad)
        self.assertEqual(self.calls(), [])

    def test_a_second_question_while_one_is_answered_is_refused(self):
        with sidechat._lock(self.project):
            self.assertTrue(sidechat.is_busy(self.project))
            with self.assertRaisesRegex(sidechat.SidechatError, "another question"):
                self.ask()
            self.assertTrue(sidechat.status(project=self.project, db_path=self.db.path, agents_dir=self.dir)["busy"])
        self.assertFalse(sidechat.is_busy(self.project))
        self.assertEqual(self.calls(), [])
        self.ask()  # the lock is free again

    def test_two_real_asks_at_once_one_is_refused(self):
        self.plan([{"text": "slow", "sleep": 1.5, "session": SIDE}])
        outcome = {}

        def first():
            outcome["first"] = self.ask()
        thread = threading.Thread(target=first)
        thread.start()
        for _ in range(100):
            if self.calls():
                break
            time.sleep(0.05)
        with self.assertRaisesRegex(sidechat.SidechatError, "another question"):
            self.ask("second")
        thread.join(10)
        self.assertEqual(outcome["first"]["answer"], "slow")
        self.assertEqual(len(self.calls()), 1)

    def test_a_timeout_stops_the_process_and_says_so(self):
        self.plan([{"text": "never", "sleep": 30, "session": SIDE}])
        started = time.time()
        with self.assertRaisesRegex(sidechat.SidechatError, "did not answer within"):
            self.ask(timeout=0.6)
        self.assertLess(time.time() - started, 10)
        pid = int((self.bin / "slept.pid").read_text())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(len(self.calls()), 1)  # a timeout is not retried on the fallback model
        self.assertFalse(sidechat.is_busy(self.project))

    def test_a_failure_falls_back_once_to_the_other_model(self):
        self.plan([{"error": "blocked_service_tier", "exit": 1, "session": SIDE}, {"text": "from haiku"}])
        result = self.ask()
        calls = self.calls()
        self.assertEqual([call["args"][call["args"].index("--model") + 1] for call in calls],
                         ["openai/gpt-6-luna", "anthropic/claude-haiku-5-5"])
        self.assertTrue(result["fallback"])
        self.assertEqual((result["model"], result["answer"]), ("anthropic/claude-haiku-5-5", "from haiku"))
        # the second call goes to the session the first one made, not to a new one
        self.assertEqual(calls[1]["args"][calls[1]["args"].index("--session") + 1], SIDE)
        self.assertEqual(sidechat.load_state(self.project, MAIN)["model"], "anthropic/claude-haiku-5-5")

    def test_both_models_failing_is_an_error_after_two_calls(self):
        self.plan([{"error": "nope", "exit": 1}, {"text": "", "exit": 0}])
        with self.assertRaisesRegex(sidechat.SidechatError, "the side chat failed"):
            self.ask()
        self.assertEqual(len(self.calls()), 2)
        self.assertFalse(sidechat.is_busy(self.project))

    def test_the_fallback_model_asked_first_is_not_retried_on_itself(self):
        self.plan([{"error": "nope", "exit": 1}])
        with self.assertRaises(sidechat.SidechatError):
            self.ask(model="anthropic/claude-haiku-5-5")
        self.assertEqual(len(self.calls()), 1)

    def test_a_bad_model_name_is_refused(self):
        with self.assertRaises(sidechat.SidechatError):
            sidechat.command(model="no spaces/ allowed", side=None, title="t", tree=set())

    def test_spend_is_read_from_the_side_session(self):
        self.db.session(SIDE, agent="side-chat", cost=0.0123)
        self.assertEqual(sidechat.session_cost(self.db.path, SIDE), 0.0123)
        self.assertIsNone(sidechat.session_cost(self.db.path, "ses_nobody"))
        self.assertIsNone(sidechat.session_cost(self.dir / "missing.db", SIDE))
        result = self.ask()
        self.assertEqual(result["spent"], 0.0123)
        shown = sidechat.status(main=MAIN, project=self.project, db_path=self.db.path, agents_dir=self.dir)
        self.assertEqual((shown["session"], shown["spent"], shown["asks"]), (SIDE, 0.0123, 1))

    def test_ask_never_writes_outside_the_project_state_folder(self):
        before = {str(p.relative_to(self.dir)): p.read_bytes() for p in self.dir.rglob("*")
                  if p.is_file() and ".opencode/sidechat" not in str(p) and p.name != "calls.jsonl"}
        self.ask()
        after = {str(p.relative_to(self.dir)): p.read_bytes() for p in self.dir.rglob("*")
                 if p.is_file() and ".opencode/sidechat" not in str(p) and p.name != "calls.jsonl"}
        self.assertEqual(before, after)


class StatusTests(Case):
    def test_status_and_snapshot_field(self):
        shown = sidechat.status(main=MAIN, project=self.project, db_path=self.db.path, agents_dir=self.dir / "none")
        self.assertEqual((shown["installed"], shown["session"], shown["model"], shown["busy"]),
                         (False, None, None, False))
        self.assertNotIn("spent", shown)
        (self.project / ".opencode/agents/side-chat.md").write_text(AGENT_OK)
        self.ask()
        self.db.session(SIDE, agent="side-chat", cost=0.5)
        document = {"root": {"id": MAIN}}
        sidechat.annotate(document, project=self.project, agents_dir=self.dir / "none", db_path=self.db.path)
        self.assertEqual(document["sidechat"], {"installed": True, "session": SIDE, "model": "openai/gpt-6-luna",
                                                "busy": False, "spent": 0.5})

    def test_snapshot_without_a_root_has_the_object_and_no_session(self):
        document = {"root": None}
        sidechat.annotate(document, project=self.project, agents_dir=self.dir / "none", db_path=self.db.path)
        self.assertEqual(document["sidechat"], {"installed": False, "session": None, "model": None, "busy": False})

    def test_brief_info_gives_sizes_and_names_never_text(self):
        info = sidechat.brief_info("what did n1 find?", main=MAIN, project=self.project, db_path=self.db.path,
                                   config_dir=self.config, session_graph_dir=None)
        self.assertEqual(sorted(info["sections"]), ["artifacts", "chat", "graph", "heads"])
        self.assertTrue(info["full"])
        self.assertLessEqual(info["chars"], info["limit"])
        self.assertNotIn("ROUTES", json.dumps(info))
        self.assertNotIn("build the rate limiter", json.dumps(info))


class FeedTests(Case):
    def test_side_feed_cuts_each_question_before_its_brief_and_drops_reasoning(self):
        message = "Question from the user:\nwhere are we?\n\n--- brief (all you know) ---\n## task graph\nSECRET-BRIEF"
        feed = {"session": SIDE, "items": [{"kind": "user", "text": message, "cut": False},
                                           {"kind": "reasoning", "text": "thinking"},
                                           {"kind": "text", "text": "n2 is running."}]}
        shown = sidechat.side_feed(feed)
        self.assertEqual([item["kind"] for item in shown["items"]], ["user", "text"])
        self.assertEqual(shown["items"][0]["text"], "Question from the user:\nwhere are we?")

    def start(self, **kw):
        self.httpd = server.make_server(0, graph_dir=self.project / ".opencode/graph", cwd=self.project, session=MAIN,
                                        web_dir=self.dir, sessions_db=self.db.path, opencode_dir=self.config, **kw)
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=20)
        try:
            conn.request("GET", path)
            reply = conn.getresponse()
            return reply.status, reply.read()
        finally:
            conn.close()

    def test_api_chat_serves_the_side_session_and_only_that_one_besides_the_tree(self):
        self.ask("where are we?")
        self.db.session(SIDE, agent="side-chat", cost=0.01)
        self.db.session("ses_other", agent="impl-claude")
        self.db.session("ses_stranger", agent="side-chat")  # not the remembered one
        self.db.user(SIDE, sidechat.Brief().text("where are we?") + "\nSECRET-BRIEF-BODY")
        self.db.assistant(SIDE, "n2 is running.")
        self.start()
        status, body = self.get(f"/api/chat?session={SIDE}")
        self.assertEqual(status, 200)
        feed = json.loads(body)
        self.assertEqual(feed["session"], SIDE)
        self.assertEqual([item["kind"] for item in feed["items"]], ["user", "text"])
        self.assertEqual(feed["items"][0]["text"], "Question from the user:\nwhere are we?")
        self.assertNotIn("SECRET-BRIEF-BODY", body.decode())
        self.assertEqual(self.get(f"/api/chat?session={MAIN}")[0], 200)  # the tree still works
        self.assertEqual(self.get("/api/chat?session=ses_other")[0], 404)
        self.assertEqual(self.get("/api/chat?session=ses_stranger")[0], 404)
        status, body = self.get("/api/snapshot")
        self.assertEqual(json.loads(body)["sidechat"]["session"], SIDE)
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=20)
        conn.request("POST", "/api/chat", body=b"{}")
        self.assertIn(conn.getresponse().status, (404, 405))  # no way to ask through the server
        conn.close()

    def test_without_a_side_session_every_non_tree_id_is_404(self):
        self.db.session(SIDE, agent="side-chat")
        self.start()
        self.assertEqual(self.get(f"/api/chat?session={SIDE}")[0], 404)
        document = json.loads(self.get("/api/snapshot")[1])["sidechat"]
        self.assertEqual((document["session"], document["busy"]), (None, False))


class DemoTests(Case):
    def test_demo_object_and_feed(self):
        shown = demo_sidechat()
        self.assertEqual(set(shown), {"installed", "session", "model", "busy", "spent"})
        feed = demo_chat(DEMO_SIDE)
        self.assertEqual([item["kind"] for item in feed["items"]], ["user", "text"])
        self.assertIsNone(demo_chat("ses_not_a_demo_one"))
        document = snapshot_json.snapshot_document(demo_snapshot(time.time()), "demo")
        self.assertNotIn("sidechat", document)  # added by live_document, as `setup` is
        document = snapshot_json.live_document(demo_snapshot(time.time()), "demo")
        self.assertEqual(document["sidechat"], shown)

    def test_demo_commands_call_nothing(self):
        env = {**os.environ, "OPENCODE_CLI": "/nonexistent/opencode-cli"}
        for args, key in ((["ask", "--main", MAIN, "--text-file", "-"], "answer"), (["status"], "installed"),
                          (["brief", "--main", MAIN], "sections")):
            run = subprocess.run([str(ROOT / "bin/agentgraph"), "sidechat", *args, "--demo", "--json"], cwd=self.dir,
                                 input="hi", capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn(key, json.loads(run.stdout))

    def test_demo_over_the_server(self):
        httpd = server.make_server(0, demo=True, web_dir=self.dir)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=20)
        conn.request("GET", "/api/snapshot")
        self.assertEqual(json.loads(conn.getresponse().read())["sidechat"], demo_sidechat())
        conn.request("GET", f"/api/chat?session={DEMO_SIDE}")
        reply = conn.getresponse()
        self.assertEqual((reply.status, json.loads(reply.read())["session"]), (200, DEMO_SIDE))
        conn.close()


def files(root: Path) -> dict:
    found = {}
    for path in sorted(Path(root).rglob("*")):
        found[str(path.relative_to(root))] = "dir" if path.is_dir() else path.read_bytes()
    return found


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.agents, self.skills, self.backups = self.dir / "agents", self.dir / "skills", self.dir / "backups"
        for folder in (self.agents, self.skills):
            folder.mkdir()
        (self.skills / "other").mkdir()
        (self.skills / "other/SKILL.md").write_text("---\nname: other\ndescription: d\n---\nbody\n")
        self.config = self.dir / "preset.json"
        self.config.write_text(json.dumps({"preset": "p", "presets": {"p": {"orchestrator": {"model": "a/b"}}}}))
        self.agent_file, self.skill_file = self.dir / "agent.md", self.dir / "skill.md"
        self.agent_file.write_text(AGENT_OK)
        self.skill_file.write_text(SKILL_OK)

    def where(self):
        return dict(agents_dir=self.agents, skills_dir=self.skills, backups=self.backups)

    def install(self, **kw):
        return sidechat.install(agent_file=self.agent_file, skill_file=self.skill_file, **self.where(), **kw)

    def test_install_uninstall_undo_agent_only(self):
        before = files(self.dir)
        done = self.install()
        self.assertEqual(done["agent"], "side-chat.md")
        self.assertEqual((self.agents / "side-chat.md").read_text(), AGENT_OK)
        self.assertEqual(files(self.skills), {k[len("skills/"):]: v for k, v in before.items() if k.startswith("skills/")})
        self.assertTrue(Path(done["record"]).is_file())
        removed = sidechat.uninstall(**self.where(), config=self.config)
        self.assertFalse((self.agents / "side-chat.md").exists())
        self.assertTrue(Path(removed["record"]).is_file())
        back = sidechat.undo(removed["record"], **self.where(), config=self.config)
        self.assertEqual(back["undone"], "uninstall")
        self.assertEqual((self.agents / "side-chat.md").read_text(), AGENT_OK)
        sidechat.undo(done["record"], **self.where(), config=self.config)
        self.assertFalse((self.agents / "side-chat.md").exists())
        self.assertEqual(files(self.agents), {})

    def test_install_with_skill_adds_the_rule_and_uninstall_takes_both_away(self):
        done = self.install(with_skill=True)
        text = (self.agents / "side-chat.md").read_text()
        self.assertIn('action: "skill"\n    resource: "side-chat"\n    effect: allow', text)
        self.assertIn('action: "*"\n    resource: "*"\n    effect: deny', text)
        self.assertTrue((self.skills / "side-chat/SKILL.md").is_file())
        self.assertEqual(done["skill"], "side-chat")
        removed = sidechat.uninstall(**self.where(), config=self.config)
        self.assertFalse((self.skills / "side-chat").exists())
        self.assertFalse((self.agents / "side-chat.md").exists())
        self.assertTrue((self.skills / "other/SKILL.md").is_file())  # nothing else touched
        sidechat.undo(removed["record"], **self.where(), config=self.config)
        self.assertTrue((self.skills / "side-chat/SKILL.md").is_file())
        self.assertEqual((self.agents / "side-chat.md").read_text(), text)

    def test_refusals_leave_everything_untouched(self):
        sidechat_files = files(self.dir)
        cases = {
            "an allow rule": AGENT_OK.replace("effect: deny\n---", "effect: allow\n---"),
            "no marker": AGENT_OK.replace("# agentgraph: sidechat (test)\n", ""),
            "a subagent mode": AGENT_OK.replace("mode: primary", "mode: subagent"),
            "no deny-all first": AGENT_OK.replace('  - action: "*"\n    resource: "*"\n    effect: deny\n', ""),
            "no model": AGENT_OK.replace('model: "openai/gpt-6-luna"\n', ""),
            "a stray key": AGENT_OK.replace("permissions:", "tools: all\npermissions:"),
        }
        for label, text in cases.items():
            self.agent_file.write_text(text)
            before = files(self.dir)
            with self.assertRaises(sidechat.SidechatError, msg=label):
                self.install()
            self.assertEqual(files(self.dir), before, label)
        self.agent_file.write_text(AGENT_OK)
        self.skill_file.write_text(SKILL_OK.replace("name: side-chat", "name: other-name"))
        before = files(self.dir)
        with self.assertRaises(sidechat.SidechatError):
            self.install(with_skill=True)
        self.assertEqual(files(self.dir), before)  # the agent file was not written either
        self.assertFalse(sidechat_files == {})
        with self.assertRaises(sidechat.SidechatError):
            sidechat.install(agent_file=self.dir / "missing.md", **self.where())
        self.assertEqual(files(self.dir), before)

    def test_install_over_an_existing_file_and_uninstall_of_a_hand_written_one_are_refused(self):
        (self.agents / "side-chat.md").write_text("---\ndescription: mine\n---\nhand written\n")
        before = files(self.dir)
        with self.assertRaises(sidechat.SidechatError):
            self.install()
        with self.assertRaisesRegex(sidechat.SidechatError, "not written by"):
            sidechat.uninstall(**self.where(), config=self.config)
        self.assertEqual(files(self.dir), before)

    def test_nothing_to_uninstall_is_refused(self):
        before = files(self.dir)
        with self.assertRaisesRegex(sidechat.SidechatError, "not installed"):
            sidechat.uninstall(**self.where(), config=self.config)
        self.assertEqual(files(self.dir), before)

    def test_undo_refuses_a_changed_file_and_a_foreign_record(self):
        done = self.install()
        (self.agents / "side-chat.md").write_text(AGENT_OK + "# edited\n")
        before = files(self.dir)
        with self.assertRaises(sidechat.SidechatError):
            sidechat.undo(done["record"], **self.where(), config=self.config)
        for record in ("/etc/passwd", str(self.dir / "agent.md"), "", "x\0y"):
            with self.assertRaises(sidechat.SidechatError):
                sidechat.undo(record, **self.where(), config=self.config)
        self.assertEqual(files(self.dir), before)

    def test_the_shipped_proposals_pass_the_checks(self):
        text = sidechat.AGENT_PROPOSAL.read_text()
        sidechat.check_agent_text(text)
        self.assertLess(len(text.split("---\n", 2)[2].split()), 200)  # the prompt: under about 200 words
        self.assertLess(len(sidechat.SKILL_PROPOSAL.read_text().split()), 175)
        self.assertIn('model: "openai/gpt-6-luna"', text)
        sidechat._skill_parts(sidechat.SKILL_PROPOSAL.read_text())

    def test_the_defaults_do_not_point_at_a_scratch_folder_but_tests_never_use_them(self):
        # install/uninstall/undo take every folder as a parameter; the launcher passes none, so real use writes to
        # the real agents folder only when a person runs `agentgraph sidechat install`. Prove it on the parser.
        args = sidechat._parser().parse_args(["install"])
        self.assertIsNone(args.agents_dir)
        self.assertEqual(args.agent_file, sidechat.AGENT_PROPOSAL)


class CommandTests(Case):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sidechat.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_ask_through_main_with_json(self):
        question = self.dir / "q.txt"
        question.write_text("where are we?")
        os.environ["OPENCODE_CLI"] = str(self.exe)
        self.addCleanup(os.environ.pop, "OPENCODE_CLI", None)
        code, out, _ = self.run_main(["ask", "--main", MAIN, "--text-file", str(question), "--dir", str(self.project),
                                      "--db", str(self.db.path), "--config-dir", str(self.config), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual((data["answer"], data["session"], data["model"]), ("n2 is running.", SIDE, "openai/gpt-6-luna"))
        self.assertIn("chars", data["brief"])
        code, out, _ = self.run_main(["status", "--main", MAIN, "--dir", str(self.project), "--db", str(self.db.path),
                                      "--agents-dir", str(self.dir / "none"), "--json"])
        self.assertEqual(json.loads(out)["session"], SIDE)
        code, out, _ = self.run_main(["brief", "--main", MAIN, "--dir", str(self.project), "--db", str(self.db.path),
                                      "--config-dir", str(self.config)])
        self.assertIn("sections:", out)
        self.assertNotIn("build the rate limiter", out)

    def test_a_refusal_exits_1_with_the_reason(self):
        question = self.dir / "q.txt"
        question.write_text("q" * 4001)
        code, out, _ = self.run_main(["ask", "--main", MAIN, "--text-file", str(question), "--dir", str(self.project),
                                      "--db", str(self.db.path), "--json"])
        self.assertEqual(code, 1)
        self.assertIn("4000", json.loads(out)["error"])


if __name__ == "__main__":
    unittest.main()
