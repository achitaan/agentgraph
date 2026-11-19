import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph.sources import (
    build_snapshot,
    find_graph_dir,
    load_graphs,
    load_roster,
    load_sessions,
    pretty_model,
)

SCHEMA = (
    "CREATE TABLE session_v2 (id TEXT, parent_id TEXT, agent TEXT, model TEXT, title TEXT, "
    "directory TEXT, cost REAL, tokens_input INT, tokens_output INT, time_created INT, "
    "time_updated INT, time_busy INT, time_idle INT)"
)


def make_db(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.executemany("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


MODEL = json.dumps({"id": "claude-fable-5-2-fast", "providerID": "anthropic", "variant": "high"})


def row(sid, parent=None, agent="orchestrator", model=MODEL, directory="/p", created=1000, updated=2000, busy=None, idle=None):
    return (sid, parent, agent, model, "t-" + sid, directory, 1.5, 10, 20, created, updated, busy, idle)


class PrettyModel(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(pretty_model("openai/gpt-6.1-sol-ultrafast"), "GPT-6.1 Sol Ultrafast")
        self.assertEqual(pretty_model("anthropic/claude-fable-5-2-fast"), "Claude Fable 5.2 Fast")
        self.assertEqual(pretty_model("google/gemini-4-carbon"), "Gemini 4 Carbon")


def write_config(d: Path):
    (d / "agents").mkdir(parents=True, exist_ok=True)
    cfg = {
        "preset": "p",
        "presets": {
            "p": {
                "impl-openai": {"model": "openai/gpt-6.1-sol-ultrafast", "variant": "low"},
                "orchestrator": {"model": ["anthropic/claude-fable-5-2-fast"], "variant": "high"},
                "oracle": {"model": [{"id": "google/gemini-4-carbon", "variant": "xhigh"}]},
            }
        },
        "council": {
            "default_preset": "c",
            "presets": {"c": {"alpha": {"model": "google/gemini-4-carbon", "variant": "high"}}},
        },
    }
    (d / "oh-my-opencode-slim.json").write_text(json.dumps(cfg))
    (d / "agents" / "reviewer.md").write_text(
        "---\ndescription: x\nmodel: openai/gpt-6.2-astra-ultrafast\nvariant: high\n---\nbody\n"
    )


class Roster(unittest.TestCase):
    def test_native_agent_model_variant_and_optional_variant_field(self):
        with tempfile.TemporaryDirectory() as t:
            directory = Path(t)
            write_config(directory)
            (directory / "agents/native.md").write_text("---\nmodel: a/b#high\n---\nNative agent")
            (directory / "agents/explicit.md").write_text("---\nmodel: a/c\nvariant: low\n---\n")
            agents, warnings = load_roster(directory)
        self.assertEqual(warnings, [])
        by_name = {agent.name: agent for agent in agents}
        self.assertEqual((by_name["native"].model, by_name["native"].effort), ("B", "high"))
        self.assertEqual((by_name["explicit"].model, by_name["explicit"].effort), ("C", "low"))

    def test_native_missing_model_duplicate_and_disabled_are_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            directory = Path(t)
            write_config(directory)
            (directory / "agents/no-model.md").write_text("---\ndescription: No model\n---\nmodel: a/body")
            (directory / "agents/impl-openai.md").write_text("---\nmodel: a/replacement#high\n---\n")
            (directory / "agents/disabled.md.disabled").write_text("---\nmodel: a/disabled#high\n---\n")
            agents, warnings = load_roster(directory)
        self.assertEqual(warnings, [])
        self.assertEqual([agent.name for agent in agents],
                         ["orchestrator", "impl-openai", "oracle", "councillor-alpha", "reviewer"])
        implementer, = [agent for agent in agents if agent.name == "impl-openai"]
        self.assertEqual((implementer.model, implementer.effort), ("GPT-6.1 Sol Ultrafast", "low"))

    def test_full(self):
        with tempfile.TemporaryDirectory() as t:
            write_config(Path(t))
            agents, warnings = load_roster(Path(t))
        self.assertEqual(warnings, [])
        self.assertEqual([a.name for a in agents], ["orchestrator", "impl-openai", "oracle", "councillor-alpha", "reviewer"])
        by = {a.name: a for a in agents}
        self.assertEqual(by["orchestrator"].model, "Claude Fable 5.2 Fast")
        self.assertEqual(by["orchestrator"].group, "hub")
        self.assertEqual(by["oracle"].effort, "xhigh")
        self.assertEqual(by["impl-openai"].pool, 3)
        self.assertEqual(by["impl-openai"].group, "build")
        self.assertEqual(by["councillor-alpha"].group, "council")
        self.assertEqual(by["reviewer"].model, "GPT-6.2 Astra Ultrafast")
        self.assertEqual(by["reviewer"].effort, "high")

    def test_reviewer_file_is_optional_and_never_duplicates_the_preset(self):
        with tempfile.TemporaryDirectory() as t:
            directory = Path(t)
            write_config(directory)
            (directory / "agents" / "reviewer.md").unlink()
            agents, warnings = load_roster(directory)
            self.assertEqual(warnings, [])
            self.assertEqual([a.name for a in agents], ["orchestrator", "impl-openai", "oracle", "councillor-alpha"])
            (directory / "agents").rmdir()
            agents, warnings = load_roster(directory)
            self.assertEqual(warnings, [])
            self.assertNotIn("reviewer", [a.name for a in agents])

            write_config(directory)  # restores agents/reviewer.md
            cfg = json.loads((directory / "oh-my-opencode-slim.json").read_text())
            cfg["presets"]["p"]["reviewer"] = {"model": "anthropic/claude-opus-5-5", "variant": "xhigh"}
            (directory / "oh-my-opencode-slim.json").write_text(json.dumps(cfg))
            agents, warnings = load_roster(directory)
        self.assertEqual(warnings, [])
        reviewers = [a for a in agents if a.name == "reviewer"]
        self.assertEqual([(a.model, a.effort, a.group) for a in reviewers], [("Claude Opus 5.5", "xhigh", "verify")])

    def test_disabled_agents_are_never_in_the_roster(self):
        with tempfile.TemporaryDirectory() as t:
            directory = Path(t)
            write_config(directory)
            cfg = json.loads((directory / "oh-my-opencode-slim.json").read_text())
            cfg["presets"]["p"]["fixer"] = {"model": "openai/gpt-6.1-sol-ultrafast"}
            cfg["disabled_agents"] = ["fixer", "observer", "native", "councillor-alpha", 7]
            (directory / "oh-my-opencode-slim.json").write_text(json.dumps(cfg))
            (directory / "agents/native.md").write_text("---\nmodel: a/b#high\n---\n")
            agents, warnings = load_roster(directory)
            self.assertEqual(warnings, [])
            self.assertEqual([a.name for a in agents], ["orchestrator", "impl-openai", "oracle", "reviewer"])
            gd = directory / "graphs"
            gd.mkdir()
            snap = build_snapshot(directory, gd, directory / "no.db", now=1.0)
            self.assertEqual(snap.disabled_agents, ["fixer", "observer", "native", "councillor-alpha"])
            cfg["disabled_agents"] = "fixer"
            (directory / "oh-my-opencode-slim.json").write_text(json.dumps(cfg))
            agents, warnings = load_roster(directory)
            self.assertIn("fixer", [a.name for a in agents])
            self.assertEqual(warnings, ["roster: disabled_agents is not a list"])

    def test_missing(self):
        with tempfile.TemporaryDirectory() as t:
            agents, warnings = load_roster(Path(t))
        self.assertEqual(agents, [])
        self.assertTrue(warnings)

    def test_malformed(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "oh-my-opencode-slim.json").write_text("{nope")
            agents, warnings = load_roster(Path(t))
        self.assertEqual(agents, [])
        self.assertTrue(warnings)


class Graphs(unittest.TestCase):
    def test_graph_record_caps_and_truncation_metadata(self):
        from agentgraph.contract import MAX_NODES, MAX_EVENTS, MAX_OBJECTIVES
        from agentgraph.sources import graph_from_raw

        self.assertEqual((MAX_NODES, MAX_EVENTS, MAX_OBJECTIVES), (1000, 2000, 200))
        for field, limit in (("nodes", MAX_NODES), ("events", MAX_EVENTS), ("objectives", MAX_OBJECTIVES)):
            for count in (limit, limit + 1):
                with self.subTest(field=field, count=count):
                    raw = {field: [{"id": str(i), "note": str(i)} for i in range(count)]}
                    loaded = graph_from_raw(raw, Path("test.json"))
                    records = getattr(loaded, field)
                    self.assertEqual(len(records), min(limit, count))
                    self.assertEqual(loaded.truncated, [f"{field}: kept {limit} of {count}"] if count > limit else [])
                    self.assertEqual(records[-1].note if field == "events" else records[-1].id, str(limit - 1))
                    self.assertEqual(len(raw[field]), count)

    def test_malformed_records_cannot_bypass_caps(self):
        from agentgraph.sources import graph_from_raw

        loaded = graph_from_raw({"nodes": [None] * 1000 + [{"id": "beyond"}]}, Path("test.json"))
        self.assertEqual(loaded.nodes, [])
        self.assertEqual(loaded.truncated, ["nodes: kept 1000 of 1001"])

    def test_loading_truncated_graph_warns_for_each_field(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "large.json"
            path.write_text(json.dumps({"nodes": [{}] * 1001, "events": [{}] * 2001,
                                        "objectives": [{}] * 201}))
            graphs, warnings = load_graphs(path.parent)
        self.assertEqual(len(graphs), 1)
        self.assertEqual(graphs[0].truncated, ["nodes: kept 1000 of 1001", "events: kept 2000 of 2001",
                                              "objectives: kept 200 of 201"])
        self.assertEqual(len(warnings), 3)
        for notice, warning in zip(graphs[0].truncated, warnings):
            self.assertIn("large.json", warning)
            self.assertIn(notice, warning)

    def test_truncation_defaults_do_not_share_state(self):
        from agentgraph.sources import graph_from_raw

        first = graph_from_raw({}, Path("first.json"))
        second = graph_from_raw({}, Path("second.json"))
        first.truncated.append("test")
        self.assertEqual(second.truncated, [])

    def test_artifact_loading_skips_malformed_records_without_coercion(self):
        from dataclasses import asdict
        from agentgraph.sources import graph_from_raw

        valid = {"id": " report ", "path": " .opencode/artifacts/report.md ", "revision": 10**100}
        inputs = {"from": " producer ", "artifact": " report ", "revision": 10**100}
        for field, entry in (("artifacts", valid), ("inputs", inputs)):
            malformed = [None, "entry", [], {}]
            for key in entry:
                for value in (None, True, [], "", " \t", 0, -1, 1.5):
                    malformed.append({**entry, key: value})
                malformed.append({k: v for k, v in entry.items() if k != key})
            raw = {"nodes": [{field: [*malformed, {**entry, "unknown": "ignored"}]}, {},
                             {field: None}, {field: entry}, {field: "entry"}]}
            loaded = graph_from_raw(raw, Path("test.json"))
            records = getattr(loaded.nodes[0], field)
            expected = dict(entry)
            if field == "inputs":
                expected["from_node"] = expected.pop("from")
            self.assertEqual([asdict(record) for record in records], [expected])
            for node in loaded.nodes[1:]:
                self.assertEqual(getattr(node, field), [])

    def test_pattern_and_feedback_loading_is_tolerant(self):
        from agentgraph.sources import graph_from_raw

        raw = {"nodes": [{}, {"pattern": "route", "feedback_to": ["n1", "n2"]},
                         {"pattern": 1, "feedback_to": "n1"},
                         {"pattern": None, "feedback_to": ["n1", 2]}]}
        loaded = graph_from_raw(raw, Path("task.json"))
        self.assertEqual([(n.pattern, n.feedback_to) for n in loaded.nodes],
                         [("", []), ("route", ["n1", "n2"]), ("", []), ("", [])])

    def test_graph_from_raw_uses_display_coercions_and_defaults(self):
        from agentgraph.sources import graph_from_raw

        path = Path("/project/.opencode/graph/example.json")
        raw = {"revision": "2", "updated": "15", "session": [], "amendments": [12],
               "nodes": [None, {"id": 1, "depends_on": "missing", "scope": [12], "stalled": "false"}],
               "objectives": [None, {"id": "o1", "nodes": [1]}],
               "events": [None, {"time": "2", "event": "declared"}]}
        graph = graph_from_raw(raw, path)
        self.assertEqual((graph.path, graph.task, graph.status), (str(path), "example", "running"))
        self.assertEqual((graph.created, graph.updated, graph.revision, graph.session), (0, 15, 1, None))
        self.assertEqual(graph.amendments, ["12"])
        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual((graph.nodes[0].id, graph.nodes[0].type, graph.nodes[0].status), ("1", "implement", "pending"))
        self.assertEqual(graph.nodes[0].depends_on, [])
        self.assertEqual(graph.nodes[0].scope, ["12"])
        self.assertTrue(graph.nodes[0].stalled)
        self.assertEqual([o.nodes for o in graph.objectives], [["1"]])
        self.assertEqual([e.time for e in graph.events], [2])

    def test_graph_from_raw_does_not_raise_on_large_numeric_values(self):
        from agentgraph.sources import graph_from_raw

        huge = 10**400
        graph = graph_from_raw({"updated": huge, "nodes": [{"started": huge, "attempts": huge}],
                                "events": [{"time": huge}]}, Path("task.json"))
        self.assertEqual(graph.updated, 0)
        self.assertIsNone(graph.nodes[0].started)
        self.assertEqual(graph.nodes[0].attempts, huge)
        self.assertEqual(graph.events[0].time, 0)

    def test_load(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            (d / "old.json").write_text(json.dumps({"task": "old", "updated": 5, "status": "passed", "extra": 1}))
            (d / "bare.json").write_text(json.dumps({"updated": 9, "nodes": [{"id": "n1", "type": "check", "agent": "x", "goal": "g", "depends_on": None, "scope": None}], "events": None}))
            (d / "bad.json").write_text("{oops")
            graphs, warnings = load_graphs(d)
        self.assertEqual([g.task for g in graphs], ["bare", "old"])
        self.assertEqual(graphs[0].nodes[0].depends_on, [])
        self.assertEqual(graphs[0].nodes[0].scope, [])
        self.assertEqual(graphs[0].events, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("bad.json", warnings[0])

    def test_missing(self):
        self.assertEqual(load_graphs(None), ([], []))
        self.assertEqual(load_graphs(Path("/nonexistent/zzz")), ([], []))

    def test_find(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t).resolve()
            (base / ".opencode" / "graph").mkdir(parents=True)
            sub = base / "a" / "b"
            sub.mkdir(parents=True)
            with patch("agentgraph.sources.Path.home", return_value=base):
                self.assertEqual(find_graph_dir(sub), base / ".opencode" / "graph")


class GraphFileSafety(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_symlinks_to_valid_and_oversized_graphs_are_refused(self):
        for size in (0, 2 * 1024 * 1024):
            with self.subTest(size=size):
                target = self.directory / "target.txt"
                target.write_text('{"task":"target"}' + " " * size)
                link = self.directory / "link.json"
                link.symlink_to(target)
                try:
                    graphs, warnings = load_graphs(self.directory)
                    self.assertEqual(graphs, [])
                    self.assertEqual(len(warnings), 1)
                    self.assertIn("link.json", warnings[0])
                finally:
                    link.unlink()

    def test_fifo_is_refused_without_blocking(self):
        fifo = self.directory / "pipe.json"
        os.mkfifo(fifo)
        code = ("import json,sys; from pathlib import Path; from agentgraph.sources import load_graphs; "
                "g,w=load_graphs(Path(sys.argv[1])); print(json.dumps([len(g),w]))")
        try:
            result = subprocess.run([sys.executable, "-c", code, str(self.directory)],
                                    capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            self.fail("loading a graph FIFO blocked")
        self.assertEqual(result.returncode, 0, result.stderr)
        count, warnings = json.loads(result.stdout)
        self.assertEqual(count, 0)
        self.assertEqual(len(warnings), 1)
        self.assertIn("not a regular file", warnings[0])

    def test_byte_limit_accepts_exact_cap_and_rejects_one_extra_byte(self):
        path = self.directory / "size.json"
        cap = 2 * 1024 * 1024
        text = '{"task":"size"}'
        path.write_text(text + " " * (cap - len(text)))
        graphs, warnings = load_graphs(self.directory)
        self.assertEqual([graph.task for graph in graphs], ["size"])
        self.assertEqual(warnings, [])
        with path.open("ab") as stream:
            stream.write(b" ")
        graphs, warnings = load_graphs(self.directory)
        self.assertEqual(graphs, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("2097152", warnings[0])

    def test_growing_file_cannot_bypass_byte_limit(self):
        from agentgraph import sources
        from types import SimpleNamespace

        path = self.directory / "growing.json"
        path.write_text("{}" + " " * (2 * 1024 * 1024))
        actual = path.stat()
        with patch.object(sources.os, "fstat", return_value=SimpleNamespace(st_mode=actual.st_mode, st_size=0)):
            graphs, warnings = load_graphs(self.directory)
        self.assertEqual(graphs, [])
        self.assertTrue(warnings)

    def test_only_200_newest_files_by_mtime_are_read(self):
        for i in range(203):
            path = self.directory / f"{i:03}.json"
            path.write_text(json.dumps({"task": str(i), "updated": 203 - i}))
            os.utime(path, ns=(1_000_000_000 + i, 1_000_000_000 + i))
        graphs, warnings = load_graphs(self.directory)
        self.assertEqual({graph.task for graph in graphs}, {str(i) for i in range(3, 203)})
        self.assertEqual(len(warnings), 1)
        self.assertIn("3", warnings[0])
        self.assertIn("200", warnings[0])


class GraphDiscoverySafety(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.enterContext(patch("agentgraph.sources.Path.home", return_value=self.home))

    def graph_dir(self, directory):
        graph_dir = directory / ".opencode/graph"
        graph_dir.mkdir(parents=True)
        return graph_dir

    def test_outside_home_does_not_search_shared_ancestors(self):
        self.graph_dir(self.base)
        outside = self.base / "outside/project"
        outside.mkdir(parents=True)
        self.assertIsNone(find_graph_dir(outside))

    def test_walk_includes_home_but_never_its_parent(self):
        self.graph_dir(self.base)
        project = self.home / "project/subdir"
        project.mkdir(parents=True)
        self.assertIsNone(find_graph_dir(project))
        trusted = self.graph_dir(self.home)
        self.assertEqual(find_graph_dir(project), trusted)

    def test_foreign_owned_directory_is_skipped_with_warning(self):
        foreign = self.graph_dir(self.home / "project")
        warnings = []
        with patch("agentgraph.sources.os.getuid", return_value=os.getuid() + 1):
            self.assertIsNone(find_graph_dir(foreign.parent.parent, warnings=warnings))
        self.assertEqual(len(warnings), 1)
        self.assertIn(str(foreign), warnings[0])
        self.assertIn("owned", warnings[0])

    def test_discovery_warning_reaches_snapshot(self):
        foreign = self.graph_dir(self.home / "project")
        with (patch("agentgraph.sources.os.getuid", return_value=os.getuid() + 1),
              patch("agentgraph.sources.load_roster", return_value=([], [])),
              patch("agentgraph.sources._read_checked", return_value=([], []))):
            snapshot = build_snapshot(cwd=foreign.parent.parent, now=1)
        self.assertEqual(snapshot.graphs, [])
        self.assertTrue(any(str(foreign) in warning and "owned" in warning for warning in snapshot.warnings))


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "o.db"
        make_db(
            self.db,
            [
                row("r1", directory="/a", updated=3000),
                row("r2", directory="/b", updated=5000, busy=100, idle=None),
                row("r3", directory="/a", updated=4000),
                row("c1", parent="r1", agent="impl-openai", created=1100, busy=200, idle=100),
                row("c2", parent="r1", agent="impl-openai", created=1050, busy=100, idle=200),
                row("g1", parent="c1", agent="explorer", created=1200, model="not json"),
                row("x1", parent="r2", agent="impl-openai", created=1300),
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_latest(self):
        root, kids, w = load_sessions(self.db)
        self.assertEqual(root.id, "r2")
        self.assertEqual([k.id for k in kids], ["x1"])
        self.assertEqual(w, [])

    def test_directory(self):
        root, kids, _ = load_sessions(self.db, directory="/a")
        self.assertEqual(root.id, "r3")
        root, _, w = load_sessions(self.db, directory="/zzz")
        self.assertEqual(root.id, "r2")
        self.assertEqual(len(w), 1)

    def test_id_and_descendants(self):
        root, kids, _ = load_sessions(self.db, root_id="r1", directory="/b")
        self.assertEqual(root.id, "r1")
        self.assertEqual([k.id for k in kids], ["c2", "c1", "g1"])
        by = {k.id: k for k in kids}
        self.assertTrue(by["c1"].busy)
        self.assertFalse(by["c2"].busy)
        self.assertFalse(by["g1"].busy)
        self.assertEqual(by["c1"].created, 1.1)
        self.assertEqual(root.updated, 3.0)
        self.assertEqual(root.model, "Claude Fable 5.2 Fast")
        self.assertEqual(root.effort, "high")
        self.assertEqual(by["g1"].effort, "")
        self.assertEqual(root.cost, 1.5)
        self.assertEqual((root.tokens_in, root.tokens_out), (10, 20))
        self.assertIsNone(root.parent_id)
        self.assertEqual(by["g1"].parent_id, "c1")

    def test_subagent_id_resolves_to_its_top_level_session(self):
        for requested in ("c1", "g1"):
            with self.subTest(requested=requested):
                root, kids, warnings = load_sessions(self.db, root_id=requested, directory="/b")
                self.assertEqual(root.id, "r1")
                self.assertEqual([k.id for k in kids], ["c2", "c1", "g1"])
                self.assertEqual(warnings, [])

    def test_missing_table_and_db(self):
        empty = Path(self.tmp.name) / "e.db"
        sqlite3.connect(empty).close()
        root, kids, w = load_sessions(empty)
        self.assertEqual((root, kids), (None, []))
        self.assertEqual(len(w), 1)
        root, kids, w = load_sessions(Path(self.tmp.name) / "nope.db")
        self.assertEqual((root, kids), (None, []))
        self.assertEqual(len(w), 1)


class ReadOnlyOpen(unittest.TestCase):
    def test_odd_names(self):
        from agentgraph import sources

        for name in ("open#code.db", "q?x.db", "p%41.db", "A.db"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as t:
                d = Path(t)
                db = d / name
                make_db(db, [row("only", directory="/z")])
                before = sorted(p.name for p in d.iterdir())
                self.assertEqual([s.id for s in sources._read_sessions(db)], ["only"])
                conn = sources._connect(db)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        conn.execute("INSERT INTO session_v2 (id) VALUES ('w')")
                finally:
                    conn.close()
                self.assertEqual(sorted(p.name for p in d.iterdir()), before)

    def test_fragment_name_does_not_create_other_file(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            make_db(d / "open", [row("wrong")])  # what '#...' truncation would open
            make_db(d / "open#code.db", [row("right")])
            root, _, w = load_sessions(d / "open#code.db")
            self.assertEqual(root.id, "right")
            self.assertEqual(w, [])


class Fallbacks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "o.db"
        make_db(
            self.db,
            [
                row("r1", directory="/a", updated=3000),
                row("r2", directory="/b", updated=5000),
                row("r3", directory="/c", updated=4000),
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_root_id(self):
        root, kids, w = load_sessions(self.db, root_id="nope", directory="/a")
        self.assertIsNone(root)
        self.assertEqual(kids, [])
        self.assertEqual(len(w), 1)

    def test_unmatched_directory_warns(self):
        root, _, w = load_sessions(self.db, directory="/zzz")
        self.assertEqual(root.id, "r2")
        self.assertEqual(len(w), 1)
        self.assertIn("/zzz", w[0])
        self.assertIn("most recent", w[0])

    def test_directory_list(self):
        root, _, w = load_sessions(self.db, directory=["/zzz", "/c", "/a"])
        self.assertEqual(root.id, "r3")
        self.assertEqual(w, [])
        root, _, w = load_sessions(self.db, directory=("/a",))
        self.assertEqual(root.id, "r1")


class Snapshots(unittest.TestCase):
    def setUp(self):
        # The fixture projects are inside this test user's simulated home.
        self.enterContext(patch("agentgraph.sources.Path.home", return_value=Path(tempfile.gettempdir()).resolve()))

    def test_subdirectory_uses_project_root(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t).resolve()
            write_config(base / "cfg")
            proj = base / "proj"
            (proj / ".opencode" / "graph").mkdir(parents=True)
            sub = proj / "src" / "deep"
            sub.mkdir(parents=True)
            db = base / "o.db"
            make_db(db, [row("mine", directory=str(proj), updated=1), row("other", directory="/b", updated=9)])
            snap = build_snapshot(base / "cfg", None, db, cwd=sub, now=1.0)
            self.assertEqual(snap.root.id, "mine")
            self.assertEqual(snap.warnings, [])

    def test_unknown_graph_session_warns(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t).resolve()
            write_config(base / "cfg")
            gd = base / "proj" / ".opencode" / "graph"
            gd.mkdir(parents=True)
            (gd / "job.json").write_text(json.dumps({"updated": 10, "session": "ghost"}))
            db = base / "o.db"
            make_db(db, [row("r1", directory=str(base / "proj"), updated=1)])
            snap = build_snapshot(base / "cfg", None, db, cwd=base / "proj", now=1.0)
            self.assertEqual(snap.root.id, "r1")
            self.assertTrue(any("ghost" in w for w in snap.warnings))

    def test_malformed_session_values(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t).resolve()
            write_config(base / "cfg")
            gd = base / "proj" / ".opencode" / "graph"
            gd.mkdir(parents=True)
            (gd / "a.json").write_text(json.dumps({"updated": 10, "session": {"x": 1}, "nodes": [{"id": "n", "session": ["q"]}]}))
            (gd / "b.json").write_text(json.dumps({"updated": 5, "session": ["s"], "nodes": [{"id": "n", "session": 5}]}))
            db = base / "o.db"
            make_db(db, [row("r1", directory=str(base / "proj"), updated=1)])
            graphs, _ = load_graphs(gd)
            for g in graphs:
                self.assertIsNone(g.session)
                self.assertIsNone(g.nodes[0].session)
            snap = build_snapshot(base / "cfg", None, db, cwd=base / "proj", now=1.0)
            self.assertEqual(snap.root.id, "r1")

    def test_reads_database_once(self):
        from unittest import mock
        from agentgraph import sources

        with tempfile.TemporaryDirectory() as t:
            base = Path(t).resolve()
            db = base / "o.db"
            make_db(db, [row("r1", directory=str(base), updated=1)])
            real = sources._connect
            with mock.patch.object(sources, "_connect", side_effect=real) as m:
                build_snapshot(base / "cfg", base / "nodir", db, cwd=base, now=1.0)
            self.assertEqual(m.call_count, 1)


    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t).resolve()
            write_config(base / "cfg")
            gd = base / "proj" / ".opencode" / "graph"
            gd.mkdir(parents=True)
            (gd / "job.json").write_text(json.dumps({"updated": 10, "session": "r1"}))
            db = base / "o.db"
            make_db(db, [row("r1", directory="/a", updated=1), row("r2", directory="/b", updated=9), row("c", parent="r1", agent="impl-openai")])
            snap = build_snapshot(base / "cfg", None, db, cwd=base / "proj", now=42.0)
            self.assertEqual(snap.now, 42.0)
            self.assertEqual(len(snap.roster), 5)
            self.assertEqual(snap.root.id, "r1")
            self.assertEqual([s.id for s in snap.sessions], ["c"])
            self.assertEqual(snap.graphs[0].task, "job")
            self.assertEqual(snap.warnings, [])
            snap = build_snapshot(base / "cfg", gd, db, root_id="r2", now=1.0)
            self.assertEqual(snap.root.id, "r2")

    def test_roster_carries_each_agents_skills(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = Path(t)
            (cfg / "oh-my-opencode-slim.json").write_text(json.dumps({"preset": "p", "presets": {"p": {
                "orchestrator": {"model": "a/b", "variant": "high", "skills": ["*"]},
                "impl-openai": {"model": "a/b", "skills": ["engineering-delivery", 7, "code-assurance"]},
                "reviewer": {"model": "a/b", "skills": "code-assurance"},
                "explorer": {"model": "a/b"}}}}))
            roster, _ = load_roster(cfg)
            skills = {agent.name: agent.skills for agent in roster}
            self.assertEqual(skills, {"orchestrator": ["*"], "impl-openai": ["engineering-delivery", "code-assurance"],
                                      "reviewer": [], "explorer": []})

    def test_session_graphs_are_found_in_a_second_folder_only_when_owned(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t).resolve()
            write_config(base / "cfg")
            here = base / "proj" / ".opencode" / "graph"
            here.mkdir(parents=True)
            elsewhere = base / "tools" / ".opencode" / "graph"
            elsewhere.mkdir(parents=True)
            (elsewhere / "mine.json").write_text(json.dumps({"updated": 5, "session": "r1"}))
            (elsewhere / "other.json").write_text(json.dumps({"updated": 9, "session": "r2"}))
            (elsewhere / "nobody.json").write_text(json.dumps({"updated": 7}))
            db = base / "o.db"
            make_db(db, [row("r1", directory="/a"), row("r2", directory="/b"), row("c", parent="r1", agent="impl-openai"),
                         row("g", parent="c", agent="explorer")])

            def tasks(**kwargs):
                return [g.task for g in build_snapshot(base / "cfg", here, db, now=1.0, **kwargs).graphs]

            # The session, a child and a grandchild all reach the root's graph; nobody else's is shown.
            for requested in ("r1", "c", "g"):
                self.assertEqual(tasks(root_id=requested, session_graph_dir=elsewhere), ["mine"])
            self.assertEqual(tasks(root_id="r2", session_graph_dir=elsewhere), ["other"])
            # Not without a requested session, not without the second folder, not for an unknown session.
            self.assertEqual(tasks(session_graph_dir=elsewhere), [])
            self.assertEqual(tasks(root_id="r1"), [])
            self.assertEqual(tasks(root_id="missing", session_graph_dir=elsewhere), [])
            # Local graphs stay, and the second folder is not read twice when it is the same folder.
            (here / "local.json").write_text(json.dumps({"updated": 6, "session": "r1"}))
            self.assertEqual(tasks(root_id="r1", session_graph_dir=elsewhere), ["local", "mine"])
            self.assertEqual(tasks(root_id="r1", session_graph_dir=here), ["local"])
            # A broken file in the second folder does not warn about someone else's work.
            (elsewhere / "broken.json").write_text("{nope")
            snap = build_snapshot(base / "cfg", here, db, root_id="missing", now=1.0, session_graph_dir=elsewhere)
            self.assertEqual([w for w in snap.warnings if "broken" in w], [])
            snap = build_snapshot(base / "nocfg", base / "nodir", base / "no.db", now=1.0)
            self.assertIsNone(snap.root)
            self.assertTrue(snap.warnings)


if __name__ == "__main__":
    unittest.main()
