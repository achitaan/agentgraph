import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agentgraph import contract, snapshot_json
from agentgraph.contract import Agent, Event, Node, Objective, Session, Snapshot, TaskGraph
from agentgraph.demo import demo_snapshot


class SnapshotDocumentTests(unittest.TestCase):
    def assertKeys(self, document, keys):
        self.assertEqual(set(document), set(keys.split()))

    def test_document_shape_for_demo_and_empty(self):
        session_keys = "id title model effort busy created updated cost tokens_in tokens_out context"
        group_ids = ["hub", "understand", "decide", "council", "build", "verify"]
        for snapshot, source in ((demo_snapshot(1000), "demo"), (Snapshot(1000), "live")):
            with self.subTest(source=source):
                doc = snapshot_json.snapshot_document(snapshot, source)
                self.assertKeys(doc, "version generated source warnings team totals root structure groups agents escalation graph graphs")
                self.assertEqual(doc["team"], "main")
                self.assertEqual((doc["version"], doc["generated"], doc["source"], doc["warnings"]),
                                 (1, 1000, source, []))
                self.assertKeys(doc["totals"], "sessions running cost tokens_in tokens_out")
                self.assertEqual([g["id"] for g in doc["groups"]], group_ids)
                for group in doc["groups"]:
                    self.assertKeys(group, "id title agents")
                    self.assertEqual(group["title"], contract.GROUP_TITLES[group["id"]])
                if source == "live":
                    self.assertIsNone(doc["root"])
                    self.assertIsNone(doc["graph"])
                    self.assertEqual(doc["totals"], dict.fromkeys(doc["totals"], 0))
                    self.assertEqual(doc["agents"], [])
                    self.assertEqual(doc["escalation"], [])
                    self.assertEqual(doc["graphs"], [])
                    self.assertTrue(all(group["agents"] == [] for group in doc["groups"]))
                    continue
                self.assertKeys(doc["root"], session_keys + " agent directory wear")
                self.assertIsNone(doc["root"]["wear"])
                self.assertEqual(len(doc["agents"]), 14)
                for agent in doc["agents"]:
                    self.assertKeys(agent, "name group model effort pool configured skills about busy sessions team parent")
                    self.assertEqual(agent["team"], "main")
                    self.assertEqual(agent["parent"], "council" if agent["name"].startswith("councillor-") else "")
                    for session in agent["sessions"]:
                        self.assertKeys(session, session_keys)
                for route in doc["escalation"]:
                    self.assertKeys(route, "from to used")
                for graph in doc["graphs"]:
                    self.assertKeys(graph, "task status updated")
                graph = doc["graph"]
                self.assertKeys(graph, "task goal status revision created updated path progress coverage "
                                         "objectives problems waves nodes edges advice events amendments lessons flows loops work_loops")
                self.assertKeys(graph["progress"], "passed total")
                self.assertKeys(graph["coverage"], "delivered total")
                for objective in graph["objectives"]:
                    self.assertKeys(objective, "id text nodes delivered")
                for node in graph["nodes"]:
                    self.assertKeys(node, "id type agent goal status depends_on scope done_when attempts tiers "
                                          "escalated baseline evidence outputs changed_paths failure_kind stalled "
                                          "verdict session started finished busy wave pattern feedback_to artifacts inputs")
                self.assertEqual([node["pattern"] for node in graph["nodes"]], [
                    "parallel", "parallel", "synthesize", "parallel", "parallel", "parallel", "parallel",
                    "synthesize", "evaluate", "chain", "chain",
                ])
                self.assertEqual(graph["nodes"][8]["feedback_to"], ["n4", "n5", "n6", "n7"])
                self.assertEqual(graph["advice"], [])
                self.assertEqual(graph["edges"], [
                    {"from": dep, "to": node["id"], "kind": "depends"}
                    for node in graph["nodes"] for dep in node["depends_on"]
                ] + [{"from": "n3", "to": "n1", "kind": "feedback"}] + [
                    {"from": "n9", "to": target, "kind": "feedback"}
                    for target in ("n4", "n5", "n6", "n7")
                ])
                for event in graph["events"]:
                    self.assertKeys(event, "time node event note")

    def test_root_identity_and_team_preserve_own_sessions(self):
        for root_name in ("orchestrator", "custom-root"):
            with self.subTest(root_name=root_name):
                snap = demo_snapshot(1000)
                snap.root.agent = root_name
                snap.sessions += [replace(snap.root, id="older-root-worker", parent_id=snap.root.id,
                                          updated=0, busy=False)]
                doc = snapshot_json.snapshot_document(snap, "demo")
                agents = {agent["name"]: agent for agent in doc["agents"]}
                self.assertEqual(doc["root"]["agent"], root_name)
                self.assertEqual(doc["team"], "main")
                self.assertEqual(agents[root_name]["team"], "main")
                self.assertEqual(agents[root_name]["configured"], root_name == "orchestrator")
                self.assertEqual([entry["id"] for entry in agents[root_name]["sessions"]],
                                 [snap.root.id, "older-root-worker"])
                self.assertEqual(agents[root_name]["busy"], 1)
                # Whoever runs the watched session is the hub, whatever its name.
                self.assertEqual(agents[root_name]["group"], "hub")
                self.assertEqual(doc["groups"][0]["agents"], [root_name])
                if root_name != "orchestrator":
                    # The configured hub plays no part in this session, so it is left out...
                    self.assertNotIn("orchestrator", agents)
                    # ...unless it is busy itself.
                    snap.sessions += [replace(snap.root, id="busy-orchestrator", agent="orchestrator",
                                              parent_id=snap.root.id, busy=True)]
                    busy = snapshot_json.snapshot_document(snap, "demo")
                    self.assertEqual(busy["groups"][0]["agents"][0], root_name)
                    self.assertIn("orchestrator", busy["groups"][0]["agents"])

    def test_the_graph_follows_the_structure_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.assertIsNone(snapshot_json.load_structure(root / "missing"))
            self.assertIsNone(snapshot_json.load_structure(root))  # no group folders
            def agent_file(rel, text=""):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            agent_file("00-hub/orchestrator.md")
            agent_file("10-council/council.md", "seats: councillor-\n\nprose with a colon: ignored\npool: 9\n")
            agent_file("20-build/impl-openai.md", "pool: 3\nescalates_to: impl-claude\n")
            agent_file("20-build/impl-claude.md", "escalates_to: `oracle`\n")
            # `checks: all` is no longer a setting: the line is the start of the description.
            agent_file("30-check/reviewer.md", "checks: all\n")
            agent_file("30-check/notes.txt", "not an agent")
            agent_file("README.md", "not a group")
            (root / ".hidden").mkdir()
            shape = snapshot_json.load_structure(root)
            self.assertEqual(shape["groups"], ["hub", "council", "build", "check"])
            self.assertEqual(shape["titles"]["check"], "Check")
            self.assertEqual(shape["pool"], {"impl-openai": 3})  # a setting after prose does not count
            self.assertEqual(shape["escalation"], [("impl-claude", "oracle"), ("impl-openai", "impl-claude")])
            self.assertEqual(shape["seats"], {"council": "councillor-"})
            self.assertNotIn("checkers", shape)
            self.assertEqual(shape["about"]["reviewer"], "checks: all")

            def document():
                return snapshot_json.snapshot_document(demo_snapshot(1000), "demo", structure=snapshot_json.load_structure(root))
            doc = document()
            agents = {agent["name"]: agent for agent in doc["agents"]}
            # The structure carries its drawn flows only (no default loops); tests/test_structure.py covers them.
            self.assertEqual((doc["structure"]["source"], sorted(doc["structure"])), ("folder", ["flows", "source", "teams"]))
            self.assertEqual([group["id"] for group in doc["groups"]], ["hub", "council", "build", "check"])
            self.assertEqual(agents["reviewer"]["group"], "check")
            self.assertNotIn("checks", agents["reviewer"])
            self.assertEqual((agents["impl-openai"]["pool"], agents["impl-claude"]["pool"]), (3, None))
            self.assertEqual(agents["councillor-fable"]["parent"], "council")
            self.assertEqual(agents["councillor-fable"]["group"], "council")
            # An agent with no file keeps its built-in group when that group exists, else the first group.
            self.assertEqual(agents["designer"]["group"], "build")
            self.assertEqual(agents["explorer"]["group"], "council")
            self.assertEqual([(route["from"], route["to"]) for route in doc["escalation"]],
                             [("impl-claude", "oracle"), ("impl-openai", "impl-claude")])
            # Change the structure and the graph changes with it: move an agent, add a group.
            (root / "20-build/impl-claude.md").rename(root / "30-check/impl-claude.md")
            agent_file("40-scouts/explorer.md")
            doc = document()
            agents = {agent["name"]: agent for agent in doc["agents"]}
            self.assertEqual((agents["impl-claude"]["group"], agents["explorer"]["group"]), ("check", "scouts"))
            self.assertEqual([group["id"] for group in doc["groups"]], ["hub", "council", "build", "check", "scouts"])
            self.assertEqual(doc["groups"][-1], {"id": "scouts", "title": "Scouts", "agents": ["explorer"]})
        # Without a structure the built-in table is used, unchanged.
        plain = snapshot_json.snapshot_document(demo_snapshot(1000), "demo")
        self.assertEqual((plain["structure"]["source"], sorted(plain["structure"])), ("builtin", ["flows", "source", "teams"]))
        self.assertEqual([group["id"] for group in plain["groups"]], ["hub", "understand", "decide", "council", "build", "verify"])

    def test_an_agent_carries_the_description_from_its_structure_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            def agent_file(rel, text=""):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            agent_file("00-hub/orchestrator.md", "Plans the work.\nThen dispatches it.\n")
            agent_file("10-build/impl-openai.md", "pool: 3\nescalates_to: impl-claude\n\nBuilds bounded work.  It hands back changes: a diff.\n")
            agent_file("10-build/impl-claude.md", "pool: 2\n")
            agent_file("10-build/reviewer.md")
            shape = snapshot_json.load_structure(root)
            self.assertEqual(shape["about"], {"orchestrator": "Plans the work. Then dispatches it.",
                                              "impl-openai": "Builds bounded work. It hands back changes: a diff."})
            self.assertEqual(shape["pool"], {"impl-openai": 3, "impl-claude": 2})
            doc = snapshot_json.snapshot_document(demo_snapshot(1000), "demo", structure=shape)
            about = {agent["name"]: agent["about"] for agent in doc["agents"]}
            self.assertEqual(about["orchestrator"], "Plans the work. Then dispatches it.")
            self.assertEqual(about["impl-openai"], "Builds bounded work. It hands back changes: a diff.")
            self.assertEqual((about["impl-claude"], about["reviewer"], about["designer"]), ("", "", ""))
        plain = snapshot_json.snapshot_document(demo_snapshot(1000), "demo")
        self.assertEqual({agent["about"] for agent in plain["agents"]}, {""})

    def test_skills_are_listed_with_their_descriptions(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.assertEqual(snapshot_json.skills_document(root), [])  # no skills folder
            def skill(name, text):
                (root / "skills" / name).mkdir(parents=True)
                (root / "skills" / name / "SKILL.md").write_text(text)
            skill("plain", "---\nname: plain\ndescription: Does one thing.\n---\nbody description: not this\n")
            skill("folded", "---\r\nname: folded\r\ndescription: >\r\n  Spread over\r\n  two lines.\r\nlicense: MIT\r\n---\r\n")
            skill("bare", "No frontmatter here\n")
            (root / "skills" / "empty").mkdir()
            (root / "skills" / "note.txt").write_text("not a skill")
            self.assertEqual(snapshot_json.skills_document(root), [
                {"name": "bare", "description": ""}, {"name": "folded", "description": "Spread over two lines."},
                {"name": "plain", "description": "Does one thing."}])

    def test_learning_reports_notes_changes_and_what_is_still_pending(self):
        import tempfile
        from agentgraph import ledger
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.assertIsNone(snapshot_json.learning_document(root, root))  # no ledger at all
            home = root / "memory"
            home.mkdir()
            def note(time, text, agent):
                with (home / "feedback.jsonl").open("a") as stream:
                    stream.write(json.dumps({"time": time, "text": text, "agent": agent, "task": "t"}) + "\n")
            note(1.0, "Keep diffs small", "impl-openai")
            note(2.0, "No green", "designer")
            ledger.improved(home, 3.0, "prompts/impl.md", "Ask for small diffs")
            note(5.0, "x" * 900, "impl-openai")
            doc = snapshot_json.learning_document(root, root)
            self.assertEqual((doc["total"], doc["pending"], doc["improvements"], doc["last_improvement"]), (3, 1, 1, 3.0))
            self.assertEqual([item["time"] for item in doc["notes"]], [5.0, 2.0, 1.0])  # newest first
            self.assertEqual((doc["notes"][1]["agent"], doc["notes"][1]["text"]), ("designer", "No green"))
            self.assertEqual(len(doc["notes"][0]["text"]), 400)  # long notes are cut, never dropped
            self.assertEqual(doc["changes"], [{"time": 3.0, "summary": "Ask for small diffs", "file": "prompts/impl.md"}])
            self.assertIsInstance(doc["due"], bool)
            # The active preset's own ledger wins when it exists.
            (root / "memory-fast").mkdir()
            (root / "oh-my-opencode-slim.json").write_text(json.dumps({"preset": "fast"}))
            self.assertEqual(snapshot_json.learning_document(root, root)["total"], 0)
            # ultra-fast keeps its ledger under a historical name, the one its improvement skill writes to.
            (root / "memory-o2").mkdir()
            (root / "memory-o2" / "feedback.jsonl").write_text(json.dumps({"time": 9.0, "text": "n", "agent": "a", "task": "t"}) + "\n")
            (root / "oh-my-opencode-slim.json").write_text(json.dumps({"preset": "ultra-fast"}))
            self.assertEqual(snapshot_json.learning_document(root, root)["total"], 1)

    def test_an_idle_root_outside_the_roster_is_still_the_hub(self):
        snap = demo_snapshot(1000)
        snap.root.agent = "build"
        snap.root.busy = False
        doc = snapshot_json.snapshot_document(snap, "demo")
        agents = {agent["name"]: agent for agent in doc["agents"]}
        self.assertEqual(doc["groups"][0]["agents"], ["build"])
        self.assertEqual((agents["build"]["group"], agents["build"]["configured"], agents["build"]["busy"]),
                         ("hub", False, 0))
        self.assertNotIn("orchestrator", agents)

    def test_the_hub_carries_its_skills_like_any_agent(self):
        # "*" is kept as written (the view expands it from `skills`), a list is kept in order
        # (an empty one too), and a hub outside the roster may load every skill.
        for root_name, listed, expected in (("orchestrator", ["*"], ["*"]),
                                            ("orchestrator", ["humanizer", "grilling"], ["humanizer", "grilling"]),
                                            ("orchestrator", [], []),
                                            ("build", None, ["*"]), ("plan", None, ["*"])):
            with self.subTest(root_name=root_name, listed=listed):
                snap = demo_snapshot(1000)
                snap.root.agent = root_name
                if listed is not None:
                    next(agent for agent in snap.roster if agent.name == "orchestrator").skills = list(listed)
                doc = snapshot_json.snapshot_document(snap, "demo")
                hub = doc["groups"][0]["agents"][0]
                self.assertEqual(hub, root_name)
                self.assertEqual(next(a for a in doc["agents"] if a["name"] == hub)["skills"], expected)

    def test_only_the_hub_outside_the_roster_gets_every_skill(self):
        snap = demo_snapshot(1000)
        snap.root.agent = "build"
        snap.sessions += [replace(snap.root, id="outsider", agent="stranger", parent_id=snap.root.id, busy=True)]
        for agent in snap.roster:
            agent.skills = ["code-assurance"] if agent.name != "council" else []
        doc = snapshot_json.snapshot_document(snap, "demo")
        skills = {agent["name"]: agent["skills"] for agent in doc["agents"]}
        self.assertEqual(skills["build"], ["*"])        # the hub, not in the roster
        self.assertEqual(skills["stranger"], [])        # a worker outside the roster stays as it was
        self.assertEqual(skills["council"], [])         # an empty roster list stays empty
        self.assertEqual(skills["reviewer"], ["code-assurance"])

    def test_user_or_a_disabled_agent_cannot_be_the_hub(self):
        for root_name, disabled in (("user", []), ("observer", ["observer"])):
            with self.subTest(root_name=root_name):
                snap = demo_snapshot(1000)
                snap.root.agent = root_name
                snap.disabled_agents = disabled
                doc = snapshot_json.snapshot_document(snap, "demo")
                self.assertEqual(doc["groups"][0]["agents"], ["orchestrator"])
                self.assertNotIn(root_name, [agent["name"] for agent in doc["agents"]])

    def test_agents_outside_the_roster_are_listed_only_while_busy(self):
        snap = demo_snapshot(1000)
        snap.sessions += [replace(snap.sessions[0], id="busy-leftover", agent="o2-reviewer", busy=True),
                          replace(snap.sessions[0], id="idle-leftover", agent="fixer-opus", busy=False),
                          replace(snap.sessions[0], id="idle-old-root", agent="orchestrator-2", busy=False)]
        snap.graphs[0].nodes[0].tiers = ["hard-fixer", "explorer"]  # graph-only names are not agents
        doc = snapshot_json.snapshot_document(snap, "demo")
        agents = {agent["name"]: agent for agent in doc["agents"]}
        self.assertEqual(set(agents) - {agent.name for agent in snap.roster}, {"o2-reviewer"})
        leftover = agents["o2-reviewer"]
        self.assertEqual((leftover["group"], leftover["team"], leftover["parent"], leftover["configured"],
                          leftover["busy"], leftover["pool"]), ("build", "main", "", False, 1, None))
        self.assertEqual([entry["id"] for entry in leftover["sessions"]], ["busy-leftover"])
        self.assertIn("o2-reviewer", next(group for group in doc["groups"] if group["id"] == "build")["agents"])
        # Totals still count every session, listed or not.
        self.assertEqual(doc["totals"]["sessions"], 1 + len(snap.sessions))
        self.assertEqual(doc["totals"]["cost"], sum(s.cost for s in [snap.root, *snap.sessions]))
        # Once its session finishes the leftover disappears.
        snap.sessions[-3].busy = False
        names = [agent["name"] for agent in snapshot_json.snapshot_document(snap, "demo")["agents"]]
        self.assertEqual(names, [name for group in contract.GROUPS
                                 for name in (a.name for a in snap.roster if contract.group_of(a.name) == group)])

    def test_unnamed_root_falls_back_to_orchestrator(self):
        for root_name in ("", None):
            with self.subTest(root_name=root_name):
                snap = demo_snapshot(1000)
                snap.root.agent = root_name
                doc = snapshot_json.snapshot_document(snap, "demo")
                self.assertEqual(doc["team"], "main")
                orchestrator = next(agent for agent in doc["agents"] if agent["name"] == "orchestrator")
                self.assertEqual([entry["id"] for entry in orchestrator["sessions"]], [snap.root.id])

    def test_group_lookup_overrides_roster_group_and_parents_use_listed_names(self):
        snap = Snapshot(1000, roster=[Agent("reviewer", "R", "high", "build"),
                                      Agent("orchestrator", "O", "high", "build"),
                                      Agent("councillor-astra", "A", "high", "decide"),
                                      Agent("councillor-fable", "F", "high", "decide"),
                                      Agent("impl-claude", "C", "high", "verify")])
        graph = TaskGraph("x", "task", "goal", "running", 1, 2,
                          nodes=[Node("c", "decision", "council", "goal")])
        snap.graphs = [graph]
        doc = snapshot_json.snapshot_document(snap, "live")
        agents = {agent["name"]: agent for agent in doc["agents"]}
        self.assertEqual(doc["team"], "main")
        self.assertEqual(agents["reviewer"]["group"], "verify")
        self.assertEqual(agents["orchestrator"]["group"], "hub")
        self.assertEqual(agents["impl-claude"]["group"], "build")
        # A council named only by a graph node is not listed, so it is no parent.
        self.assertNotIn("council", agents)
        for name in ("councillor-astra", "councillor-fable"):
            self.assertEqual(agents[name]["group"], "council")
            self.assertEqual(agents[name]["parent"], "")
        self.assertEqual([agent["name"] for agent in doc["agents"]],
                         [name for group in doc["groups"] for name in group["agents"]])
        snap.roster.append(Agent("council", "Council", "high", "decide"))
        agents = {agent["name"]: agent for agent in snapshot_json.snapshot_document(snap, "live")["agents"]}
        self.assertEqual(agents["council"]["group"], "council")
        self.assertEqual(agents["council"]["parent"], "")
        for name in ("councillor-astra", "councillor-fable"):
            self.assertEqual(agents[name]["parent"], "council")

    def test_empty_task_graph_has_empty_edges_and_advice(self):
        empty = TaskGraph("x", "task", "goal", "running", 1, 2)
        graph = snapshot_json.snapshot_document(Snapshot(3, graphs=[empty]), "live")["graph"]
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["advice"], [])
        self.assertEqual(graph["flows"], [])
        self.assertEqual(graph["loops"], [])

    def test_demo_review_loops(self):
        graph = snapshot_json.snapshot_document(demo_snapshot(1000), "demo")["graph"]
        self.assertEqual(graph["problems"], [])
        self.assertEqual(graph["loops"], [
            {"id": "designer~reviewer", "worker": "designer", "evaluator": "reviewer",
             "pairs": [["n7", "n9"]], "rounds": 1, "state": "working", "active": True,
             "artifacts": ["findings"]},
            {"id": "explorer~oracle", "worker": "explorer", "evaluator": "oracle",
             "pairs": [["n1", "n3"]], "rounds": 1, "state": "closed", "active": False,
             "artifacts": []},
            {"id": "impl-claude~reviewer", "worker": "impl-claude", "evaluator": "reviewer",
             "pairs": [["n6", "n9"]], "rounds": 5, "state": "working", "active": True,
             "artifacts": ["findings"]},
            {"id": "impl-openai~reviewer", "worker": "impl-openai", "evaluator": "reviewer",
             "pairs": [["n4", "n9"], ["n5", "n9"]], "rounds": 3, "state": "sent_back", "active": True,
             "artifacts": []},
        ])

    def test_review_loops_and_busy_nodes_ignore_a_reused_session(self):
        snap = demo_snapshot(1000)
        # n1 stays passed while its session is reused for other work: neither n1
        # nor its loop is busy.
        next(session for session in snap.sessions if session.id == "ses_a").busy = True
        graph = snapshot_json.snapshot_document(snap, "demo")["graph"]
        loop = next(loop for loop in graph["loops"] if loop["id"] == "explorer~oracle")
        self.assertEqual((loop["state"], loop["active"]), ("closed", False))
        self.assertFalse(next(node for node in graph["nodes"] if node["id"] == "n1")["busy"])
        # Running again on that session, it is busy and the loop is working.
        snap.graphs[0].nodes[0].status = "running"
        snap.graphs[0].nodes[0].finished = None
        graph = snapshot_json.snapshot_document(snap, "demo")["graph"]
        loop = next(loop for loop in graph["loops"] if loop["id"] == "explorer~oracle")
        self.assertEqual((loop["state"], loop["active"]), ("working", True))
        self.assertTrue(next(node for node in graph["nodes"] if node["id"] == "n1")["busy"])
        # A busy session on a pending node is not busy either.
        snap.graphs[0].nodes[0].status = "pending"
        graph = snapshot_json.snapshot_document(snap, "demo")["graph"]
        self.assertFalse(next(node for node in graph["nodes"] if node["id"] == "n1")["busy"])

    def test_demo_artifact_handoffs_and_agent_flows(self):
        snap = demo_snapshot(1000)
        graph = snapshot_json.snapshot_document(snap, "demo")["graph"]
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(len(nodes), 11)
        self.assertEqual(graph["problems"], [])
        self.assertEqual(graph["advice"], [])
        for nid in ("n1", "n2", "n3", "n4", "n5"):
            self.assertTrue(nodes[nid]["artifacts"])
        for node in nodes.values():
            for artifact in node["artifacts"]:
                self.assertKeys(artifact, "id path revision")
                self.assertTrue(artifact["path"].startswith(f".opencode/artifacts/add-rate-limit/{node['id']}/r{artifact['revision']}/"))
            for entry in node["inputs"]:
                self.assertKeys(entry, "from artifact revision stale")
                self.assertFalse(entry["stale"])
        self.assertEqual({entry["from"] for entry in nodes["n3"]["inputs"]}, {"n1", "n2"})
        decision = {"from": "n3", "artifact": "decision", "revision": 1, "stale": False}
        findings = {"from": "n9", "artifact": "findings", "revision": 1, "stale": False}
        for nid in ("n4", "n5"):
            self.assertEqual(nodes[nid]["inputs"], [decision])
        for nid in ("n6", "n7"):
            self.assertEqual(nodes[nid]["inputs"], [decision, findings])
        self.assertEqual({entry["from"] for entry in nodes["n8"]["inputs"]},
                         {nid for nid in ("n4", "n5", "n6", "n7") if nodes[nid]["artifacts"]})
        def flow(source, target, kind, pairs, artifacts, active):
            return {"from": source, "to": target, "kind": kind, "pairs": pairs, "artifacts": artifacts,
                    "active": active}

        # Data flows are active only into the busy n6 (ses_g) and n7 (ses_h); the
        # returned n5 does not count. The refuted gate's feedback is active into
        # the running n6 and n7 and carries its findings.
        self.assertEqual(graph["flows"], [
            flow("designer", "orchestrator", "data", [["n7", "n8"]], [], False),
            flow("explorer", "oracle", "data", [["n1", "n3"]], ["route-map"], False),
            flow("impl-claude", "orchestrator", "data", [["n6", "n8"]], [], False),
            flow("impl-openai", "orchestrator", "data", [["n4", "n8"], ["n5", "n8"]],
                 ["configuration", "limiter"], False),
            flow("oracle", "designer", "data", [["n3", "n7"]], ["decision"], True),
            flow("oracle", "explorer", "feedback", [["n3", "n1"]], [], False),
            flow("oracle", "impl-claude", "data", [["n3", "n6"]], ["decision"], True),
            flow("oracle", "impl-openai", "data", [["n3", "n4"], ["n3", "n5"]], ["decision"], False),
            flow("orchestrator", "reviewer", "data", [["n8", "n9"]], [], False),
            flow("researcher", "oracle", "data", [["n2", "n3"]], ["recommendation"], False),
            flow("reviewer", "designer", "feedback", [["n9", "n7"]], ["findings"], True),
            flow("reviewer", "impl-claude", "feedback", [["n9", "n6"]], ["findings"], True),
            flow("reviewer", "impl-openai", "feedback", [["n9", "n4"], ["n9", "n5"]], [], False),
        ])
        # A busy producer (n1, ses_a) activates nothing; once the consumers stop,
        # no data flow stays active.
        for item in snap.sessions:
            item.busy = item.id == "ses_a"
        graph = snapshot_json.snapshot_document(snap, "demo")["graph"]
        self.assertEqual([f["to"] for f in graph["flows"] if f["active"] and f["kind"] == "data"], [])
        self.assertEqual([f["to"] for f in graph["flows"] if f["active"] and f["kind"] == "feedback"],
                         ["designer", "impl-claude"])

    def test_snapshot_inputs_mark_only_older_known_revisions_stale(self):
        from agentgraph.contract import ArtifactInput

        snap = demo_snapshot(1000)
        producer, consumer = snap.graphs[0].nodes[2:4]
        producer.artifacts[0].revision = 2
        consumer.status = "pending"
        consumer.inputs += [ArtifactInput("n3", "decision", 2), ArtifactInput("n3", "decision", 3),
                            ArtifactInput("missing", "decision", 1), ArtifactInput("n3", "missing", 1)]
        doc = snapshot_json.snapshot_document(snap, "demo")
        self.assertEqual([entry["stale"] for entry in doc["graph"]["nodes"][3]["inputs"]],
                         [True, False, False, False, False])
        self.assertEqual(consumer.inputs[0].revision, 1)

    def test_reserved_user_is_never_an_agent(self):
        snap = demo_snapshot(1000)
        snap.roster.append(Agent("user", "U", "", "build"))
        snap.sessions.append(replace(snap.sessions[0], id="manual", agent="user"))
        doc = snapshot_json.snapshot_document(snap, "demo")
        self.assertNotIn("user", [agent["name"] for agent in doc["agents"]])
        self.assertTrue(all("user" not in group["agents"] for group in doc["groups"]))
        self.assertEqual(doc["graph"]["nodes"][9]["agent"], "user")

    def test_roster_order_session_fallback_and_busy_counts(self):
        def session(id, agent, updated, model, busy):
            return Session(id, "root", agent, "<script>untrusted text</script>", model, "high",
                           busy, 1, updated, 2.5, 10, 20)

        snap = Snapshot(100, roster=[Agent("reviewer", "R", "high", "verify"),
                                     Agent("impl-claude", "O", "max", "build", 2),
                                     Agent("explorer", "E", "low", "understand"),
                                     Agent("impl-openai", "F", "low", "build", 3)],
                        root=session("root", "orchestrator", 90, "Root", True),
                        sessions=[session("old", "session-only", 10, "Old", True),
                                  session("fix", "impl-openai", 20, "Ignored", True),
                                  session("new", "session-only", 30, "New", False),
                                  session("idle", "idle-only", 40, "Idle", False)],
                        graphs=[TaskGraph("x", "task", "goal", "running", 1, 50,
                                          nodes=[Node("n", "implement", "graph-only", "goal")])])
        doc = snapshot_json.snapshot_document(snap, "live")
        agents = {a["name"]: a for a in doc["agents"]}
        # The busy root and session-only agents are listed; idle-only (finished
        # sessions only) and graph-only are not.
        self.assertEqual(list(agents), ["orchestrator", "explorer", "impl-claude", "impl-openai",
                                        "session-only", "reviewer"])
        self.assertEqual([name for group in doc["groups"] for name in group["agents"]], list(agents))
        self.assertEqual(doc["totals"], {"sessions": 5, "running": 3, "cost": 12.5,
                                         "tokens_in": 50, "tokens_out": 100})
        self.assertEqual(agents["orchestrator"]["busy"], 1)
        self.assertEqual([s["id"] for s in agents["orchestrator"]["sessions"]], [doc["root"]["id"]])
        self.assertEqual(agents["impl-openai"]["model"], "F")
        self.assertEqual(agents["impl-openai"]["busy"], 1)
        self.assertEqual(agents["impl-openai"]["pool"], 3)
        row = agents["session-only"]
        self.assertFalse(row["configured"])
        self.assertEqual((row["model"], row["effort"], row["busy"], row["group"]), ("New", "high", 1, "build"))
        self.assertEqual([s["id"] for s in row["sessions"]], ["new", "old"])
        self.assertEqual(doc["root"]["title"], "<script>untrusted text</script>")

    def test_escalation_busy_waves_and_diagnostics(self):
        snap = demo_snapshot(1000)
        # oracle has only a finished session and graph nodes once it leaves the
        # roster, so it is not listed and its route is not shown.
        snap.roster = [agent for agent in snap.roster if agent.name != "oracle"]
        graph = snap.graphs[0]
        graph.nodes[0].tiers = ["explorer", "explorer"]
        graph.nodes[4].tiers = ["impl-claude", "designer", "impl-hard"]  # Not a direct impl-claude -> impl-hard handoff.
        graph.nodes[5].evidence = ""  # A route needs no text or event to count as used.
        graph.events.clear()
        graph.nodes[0].depends_on = ["n8"]  # A cycle must still be rendered and diagnosed.
        graph.objectives.append(Objective("delivered", "a passed item", ["n1"]))
        with patch.object(contract, "validate", wraps=contract.validate) as validate:
            doc = snapshot_json.snapshot_document(snap, "demo")
            self.assertIs(validate.call_args.args[0], graph)
        self.assertNotIn("oracle", [agent["name"] for agent in doc["agents"]])
        self.assertEqual(doc["escalation"], [{"from": "impl-openai", "to": "impl-claude", "used": True},
                                             {"from": "impl-claude", "to": "impl-hard", "used": False}])
        result = doc["graph"]
        self.assertEqual(result["problems"], contract.validate(graph))
        self.assertTrue(any("cycle" in problem for problem in result["problems"]))
        self.assertEqual(result["waves"], [[node.id for node in layer] for layer in contract.waves(graph)])
        for node in result["nodes"]:
            self.assertIn(node["id"], result["waves"][node["wave"]])
        self.assertEqual([n["id"] for n in result["nodes"] if n["busy"]], ["n6", "n7"])
        self.assertEqual([n["id"] for n in result["nodes"] if n["escalated"]], ["n5", "n6"])
        self.assertEqual(result["coverage"], {"delivered": 1, "total": 5})
        self.assertTrue(result["objectives"][-1]["delivered"])

    def test_task_selection_and_event_limit(self):
        snap = demo_snapshot(1000)
        current = snap.graphs[0]
        current.events = [Event(i, "n1", "attempt", str(i)) for i in range(220)]
        old = replace(current, task="older", updated=100, nodes=[], events=[])
        snap.graphs = [old, current]
        doc = snapshot_json.snapshot_document(snap, "demo")
        self.assertEqual(doc["graph"]["task"], current.task)
        self.assertEqual([g["task"] for g in doc["graphs"]], [current.task, "older"])
        self.assertEqual([event["time"] for event in doc["graph"]["events"]], list(range(20, 220)))
        self.assertEqual(snapshot_json.snapshot_document(snap, "demo", "older")["graph"]["task"], "older")
        self.assertIsNone(snapshot_json.snapshot_document(snap, "demo", "missing")["graph"])

    def test_session_picks_its_own_graph(self):
        snap = demo_snapshot(1000)
        current = snap.graphs[0]  # owned by ses_root, the newest
        other = replace(current, task="other-session", updated=500, session="ses_other", nodes=[], events=[])
        mine = replace(current, task="mine", updated=100, session="ses_root", nodes=[], events=[])
        current.session = "ses_elsewhere"
        snap.graphs = [other, mine, current]

        def task(**kwargs):
            return snapshot_json.snapshot_document(snap, "live", **kwargs)["graph"]["task"]

        # No session requested: the newest graph.
        self.assertEqual(task(), current.task)
        # The requested (top-level) session names an older graph.
        self.assertEqual(task(session="ses_root"), "mine")
        # A sub-agent session resolves to the top-level session it belongs to.
        self.assertEqual(task(session="ses_g"), "mine")
        # A graph naming the requested session itself also counts.
        self.assertEqual(task(session="ses_other"), "other-session")
        # An explicit task still wins.
        self.assertEqual(task(session="ses_root", task=current.task), current.task)
        self.assertEqual(snapshot_json.snapshot_document(snap, "live", current.task, session="ses_root")
                         ["graph"]["task"], current.task)
        # No graph names the session: fall back to the newest.
        mine.session = None
        self.assertEqual(task(session="ses_root"), current.task)
        self.assertEqual(task(session="unknown"), current.task)
        self.assertEqual([g["task"] for g in snapshot_json.snapshot_document(snap, "live")["graphs"]],
                         [current.task, "other-session", "mine"])

    def test_session_fallback_warns_and_ancestors_are_searched_nearest_first(self):
        snap = demo_snapshot(1000)
        # ses_root > ses_c > ses_c2 (a sub-agent that started its own sub-agent).
        snap.sessions.append(replace(snap.sessions[2], id="ses_c2", parent_id="ses_c"))
        base = snap.graphs[0]
        top = replace(base, task="top", updated=900, session="ses_root", nodes=[], events=[])
        middle = replace(base, task="middle", updated=100, session="ses_c", nodes=[], events=[])
        newest = replace(base, task="newest", updated=999, session="ses_elsewhere", nodes=[], events=[])
        snap.graphs = [top, middle, newest]

        def pick(session):
            doc = snapshot_json.snapshot_document(snap, "live", session=session)
            return doc["graph"]["task"], doc["warnings"]

        # The nearest owner wins even when an ancestor's graph is newer.
        self.assertEqual(pick("ses_c2"), ("middle", []))
        self.assertEqual(pick("ses_c"), ("middle", []))
        self.assertEqual(pick("ses_a"), ("top", []))
        self.assertEqual(pick("ses_root"), ("top", []))
        # No graph belongs to the session or any ancestor: newest, with a warning.
        snap.graphs = [middle, newest]
        self.assertEqual(pick("ses_a"), ("newest", ["graph: no graph belongs to session ses_a; showing the newest"]))
        self.assertEqual(pick("unknown"), ("newest", ["graph: no graph belongs to session unknown; showing the newest"]))
        # No warning without a requested session, with an explicit task, or with no graphs at all.
        self.assertEqual(snapshot_json.snapshot_document(snap, "live")["warnings"], [])
        self.assertEqual(snapshot_json.snapshot_document(snap, "live", "middle", session="ses_a")["warnings"], [])
        snap.graphs = []
        self.assertEqual(snapshot_json.snapshot_document(snap, "live", session="ses_a")["warnings"], [])
        # Existing warnings are kept, first.
        snap.graphs = [newest]
        snap.warnings = ["roster: earlier"]
        self.assertEqual(pick("ses_a")[1], ["roster: earlier",
                                            "graph: no graph belongs to session ses_a; showing the newest"])
        # A parent cycle ends the walk.
        snap.sessions[0].parent_id = "ses_a"
        snap.root.parent_id = "ses_a"
        self.assertEqual(pick("ses_a")[0], "newest")

    def test_disabled_agents_are_never_busy_leftovers(self):
        snap = demo_snapshot(1000)
        snap.sessions.append(replace(snap.sessions[0], id="busy-old", agent="fixer", busy=True))
        snap.sessions.append(replace(snap.sessions[0], id="busy-new", agent="newcomer", busy=True))
        names = [agent["name"] for agent in snapshot_json.snapshot_document(snap, "demo")["agents"]]
        self.assertIn("fixer", names)
        snap.disabled_agents = ["fixer", "observer"]
        doc = snapshot_json.snapshot_document(snap, "demo")
        names = [agent["name"] for agent in doc["agents"]]
        self.assertNotIn("fixer", names)
        self.assertIn("newcomer", names)
        self.assertEqual(doc["totals"]["sessions"], 1 + len(snap.sessions))

    def test_demo_work_loops(self):
        snap = demo_snapshot(1000)
        graph = snapshot_json.snapshot_document(snap, "demo")["graph"]
        self.assertEqual(graph["work_loops"], [
            {"agent": "designer", "nodes": ["n7"], "rounds": 1, "state": "working", "active": True},
            {"agent": "impl-claude", "nodes": ["n6"], "rounds": 5, "state": "working", "active": True},
            {"agent": "impl-openai", "nodes": ["n4", "n5"], "rounds": 3, "state": "waiting", "active": False},
        ])
        # Session activity comes from the snapshot: a busy session on a passed
        # node does not make it working.
        n4 = snap.graphs[0].nodes[3]
        n4.session = "ses_g"  # busy, but n4 has passed
        snap.graphs[0].nodes[4].status = "passed"  # n5 evaluated
        snap.graphs[0].nodes[4].evidence = "6 passed"
        graph = snapshot_json.snapshot_document(snap, "demo")["graph"]
        self.assertEqual(graph["work_loops"][2],
                         {"agent": "impl-openai", "nodes": ["n4", "n5"], "rounds": 3, "state": "closed",
                          "active": False})

    def test_edges_are_never_duplicated(self):
        graph = TaskGraph("x", "task", "goal", "running", 1, 2, nodes=[
            Node("a", "check", "explorer", "goal"),
            Node("b", "check", "oracle", "goal", depends_on=["a", "a", "missing", "a"]),
            Node("c", "gate", "reviewer", "goal", depends_on=["b"], feedback_to=["b", "a", "b"]),
        ])
        edges = snapshot_json.snapshot_document(Snapshot(3, graphs=[graph]), "live")["graph"]["edges"]
        self.assertEqual(edges, [{"from": "a", "to": "b", "kind": "depends"},
                                 {"from": "b", "to": "c", "kind": "depends"},
                                 {"from": "c", "to": "b", "kind": "feedback"},
                                 {"from": "c", "to": "a", "kind": "feedback"}])

    def test_nonfinite_numbers_and_null_strings(self):
        snap = demo_snapshot(float("nan"))
        snap.root.cost = float("inf")
        snap.root.title = None
        snap.sessions[0].agent = None
        snap.sessions[0].tokens_out = float("-inf")
        graph = snap.graphs[0]
        graph.path = None
        graph.nodes[0].id = None
        graph.nodes[0].agent = None
        graph.nodes[0].done_when = None
        graph.nodes[0].evidence = None
        graph.nodes[0].scope = [None]
        graph.nodes[0].changed_paths = [None]
        graph.nodes[0].started = float("-inf")
        graph.objectives[0].id = None
        graph.events[0].time = float("inf")
        graph.events[0].note = None
        doc = snapshot_json.snapshot_document(snap, "demo")
        self.assertEqual(json.loads(json.dumps(doc, allow_nan=False)), doc)
        self.assertIsNone(doc["generated"])
        self.assertIsNone(doc["root"]["cost"])
        self.assertIsNone(doc["root"]["title"])
        self.assertIsNone(doc["totals"]["cost"])
        self.assertIsNone(doc["totals"]["tokens_out"])
        self.assertIsNone(doc["graphs"][0]["updated"])
        self.assertIsNone(doc["graph"]["path"])
        self.assertIsNone(doc["graph"]["nodes"][0]["started"])
        self.assertEqual(doc["graph"]["events"][0], {"time": None, "node": "", "event": "declared", "note": None})
        self.assertIsNone(graph.path)
        self.assertEqual(graph.nodes[0].scope, [None])

    def test_duplicate_node_ids_do_not_prevent_a_snapshot(self):
        graph = TaskGraph("x", "task", "goal", "running", 1, 2,
                          nodes=[Node("dup", "check", "explorer", "goal"),
                                 Node("dup", "check", "explorer", "goal", depends_on=["dup"])])
        doc = snapshot_json.snapshot_document(Snapshot(3, graphs=[graph]), "live")
        self.assertEqual(doc["graph"]["problems"], contract.validate(graph))
        self.assertEqual([node["wave"] for node in doc["graph"]["nodes"]], [0, 0])


class RootWearTests(unittest.TestCase):
    def snapshot(self, **attrs):
        root = Session("root", None, "orchestrator", "Root", "M", "high", False, 1.0, 2.0)
        for name, value in attrs.items():
            setattr(root, name, value)
        return Snapshot(1000, root=root)

    def test_wear_is_passed_through_on_the_root_only(self):
        wear = {"context": 5, "window": 10, "level": "fresh"}
        doc = snapshot_json.snapshot_document(self.snapshot(wear=wear), "live")
        self.assertEqual(doc["root"]["wear"], wear)
        for agent in doc["agents"]:
            for session in agent["sessions"]:
                self.assertNotIn("wear", session)

    def test_wear_is_null_without_it(self):
        doc = snapshot_json.snapshot_document(self.snapshot(), "live")
        self.assertIn("wear", doc["root"])
        self.assertIsNone(doc["root"]["wear"])

    def test_non_finite_numbers_in_wear_become_null(self):
        doc = snapshot_json.snapshot_document(
            self.snapshot(wear={"per_turn": {"start": float("nan"), "recent": 0.5}}), "live")
        self.assertEqual(doc["root"]["wear"]["per_turn"], {"start": None, "recent": 0.5})


class SnapshotCommandTests(unittest.TestCase):
    def run_main(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            code = snapshot_json.main(argv)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return code, json.loads(lines[0])

    def test_demo(self):
        with patch.object(snapshot_json, "build_snapshot", side_effect=AssertionError("unexpected live read")) as live:
            code, doc = self.run_main(["--demo"])
            live.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(doc["source"], "demo")
        self.assertEqual(doc["graph"]["progress"], {"passed": 4, "total": 11})

    def test_without_a_graph_folder_the_orchestration_folder_is_searched_for_the_session(self):
        with patch.object(snapshot_json, "build_snapshot", return_value=demo_snapshot(1000)) as build:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(snapshot_json.main(["--session", "root"]), 0)
            build.assert_called_once_with(graph_dir=None, root_id="root",
                                          session_graph_dir=snapshot_json.ORCHESTRATION_GRAPH_DIR)

    def test_live_arguments_and_error_json(self):
        with patch.object(snapshot_json, "build_snapshot", return_value=demo_snapshot(1000)) as build:
            code, doc = self.run_main(["--dir", "graphs", "--session", "root", "--task", "unknown"])
            build.assert_called_once_with(graph_dir=Path("graphs"), root_id="root", session_graph_dir=None)
        self.assertEqual(code, 0)
        self.assertEqual(doc["source"], "live")
        self.assertIsNone(doc["graph"])
        snap = demo_snapshot(1000)
        snap.graphs.append(replace(snap.graphs[0], task="older", updated=1, session="ses_mine", nodes=[]))
        snap.graphs[0].session = "ses_someone_else"
        with patch.object(snapshot_json, "build_snapshot", return_value=snap):
            self.assertEqual(self.run_main(["--session", "ses_mine"])[1]["graph"]["task"], "older")
            self.assertEqual(self.run_main([])[1]["graph"]["task"], "add-rate-limit")
        with patch.object(snapshot_json, "build_snapshot", side_effect=RuntimeError("test\nerror")):
            self.assertEqual(self.run_main([]), (1, {"version": 1, "error": "test\nerror"}))
