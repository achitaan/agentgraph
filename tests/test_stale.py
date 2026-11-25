"""No write from a stale view (`.opencode/artifacts/agentgraph-desktop/n8/r4/stale-contract.md`): every thing a view
can change carries a `rev` (a short hash of the bytes it is stored as), and every write op that names an existing
thing takes an optional `expect`; a stale `expect` changes nothing and answers 409 with the current `rev`.

Flows, groups, skill text, skills changes, the endpoints and the command lines; the roster ops' module side is
tested in tests/test_roster_undo.py, their endpoint plumbing here. Scratch copies only."""
import concurrent.futures
import contextlib
import os
import subprocess
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.launch import home_env
from agentgraph import agentfiles, roster, server, skillfiles, skillset, structure, teams
from agentgraph.demo import demo_snapshot
import tests.test_agentfiles as agentfile_tests
import tests.test_structure as structure_tests
from tests.test_structure import make_structure, tree

ROOT = Path(__file__).resolve().parent.parent

LOOP = ["impl-openai", "verifier", "impl-openai"]
STALE = "changed since you opened it; reload and try again"


def cli(main, *argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")))):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Scratch(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = make_structure(self.temporary / "structure")
        self.commands = self.temporary / "commands"
        self.commands.mkdir()

    def state(self):
        return tree(self.root), tree(self.commands)

    def stale(self, function, *args, **kwargs):
        """`function` refused as stale with nothing changed; returns the rev the refusal carried."""
        before = self.state()
        with self.assertRaisesRegex(Exception, STALE) as caught:
            function(*args, **kwargs)
        self.assertEqual(self.state(), before, "a stale write changed something")
        self.assertIsInstance(caught.exception.rev, str)
        return caught.exception.rev


class Flows(Scratch):
    def flow(self, flow_id):
        return next(flow for flow in structure.drawn_flows(self.root) if flow["id"] == flow_id)

    def test_a_flow_carries_the_rev_of_its_file(self):
        flow = structure.add_flow(LOOP, None, self.root)[0]
        data = (self.root / "flows" / f"{flow['id']}.md").read_bytes()
        self.assertEqual(flow["rev"], agentfiles.rev(data))
        self.assertEqual(self.flow(flow["id"])["rev"], flow["rev"])
        changed = structure.set_flow_text(flow["id"], "Check it.", self.root)
        self.assertNotEqual(changed["rev"], flow["rev"])

    def test_each_write_refuses_a_stale_rev_and_takes_the_current_one(self):
        flow = structure.add_flow(LOOP, None, self.root)[0]
        old = flow["rev"]
        current = structure.set_flow_text(flow["id"], "Check it.", self.root)["rev"]
        where = {"commands_dir": self.commands}
        writes = [
            ("text", lambda expect: structure.set_flow_text(flow["id"], "Again.", self.root, expect=expect, **where)),
            ("name", lambda expect: structure.set_flow_name(flow["id"], "check-loop", self.root, expect=expect, **where)),
            ("command", lambda expect: structure.set_flow_command(flow["id"], True, self.root, expect=expect, **where)),
            ("prompt", lambda expect: structure.flow_prompt(flow["id"], "a goal", self.root, expect=expect)),
            ("remove", lambda expect: structure.remove_flow(flow["id"], self.root, expect=expect, **where)),
        ]
        for op, write in writes:
            with self.subTest(op=op):
                self.assertEqual(self.stale(write, old), current)
                write(current)
                if op != "remove":
                    current = self.flow(flow["id"])["rev"]
        self.assertEqual(structure.drawn_flows(self.root), [])

    def test_without_expect_every_write_works_as_before(self):
        flow = structure.add_flow(LOOP, None, self.root)[0]
        structure.set_flow_text(flow["id"], "Check it.", self.root)
        structure.set_flow_name(flow["id"], "check-loop", self.root)
        structure.set_flow_command(flow["id"], True, self.root, commands_dir=self.commands)
        self.assertIn("a goal", structure.flow_prompt(flow["id"], "a goal", self.root))
        structure.remove_flow(flow["id"], self.root, commands_dir=self.commands)
        self.assertEqual(self.state()[1], {})

    def test_a_flow_drawn_again_under_the_same_id_is_told_apart(self):
        # The review's reused-id race: a view still showing the removed flow acts on the new one with the same id.
        first = structure.add_flow(LOOP, "First.", self.root)[0]
        structure.remove_flow(first["id"], self.root)
        second = structure.add_flow(LOOP, "Second.", self.root)[0]
        self.assertEqual(second["id"], first["id"])
        self.assertNotEqual(second["rev"], first["rev"])
        self.stale(structure.set_flow_text, first["id"], "Stale.", self.root, expect=first["rev"])
        self.stale(structure.remove_flow, first["id"], self.root, expect=first["rev"])
        self.stale(structure.flow_prompt, first["id"], "a goal", self.root, expect=first["rev"])
        self.assertEqual(self.flow(second["id"])["text"], "Second.")

    def test_expect_must_be_text(self):
        flow = structure.add_flow(LOOP, None, self.root)[0]
        with self.assertRaisesRegex(structure.StructureError, "expect must be text"):
            structure.set_flow_text(flow["id"], "x", self.root, expect=3)

    def test_the_command_line_takes_expect_and_answers_with_the_rev(self):
        flow = structure.add_flow(LOOP, None, self.root)[0]
        current = flow["rev"]
        code, out, _ = cli(structure.main, "--structure", str(self.root), "flow", "text", flow["id"], "--text-file",
                           "-", "--expect", "0" * 16, "--json", stdin="Stale.")
        self.assertEqual((code, json.loads(out)), (1, {"error": f"flow {flow['id']} {STALE}", "rev": current}))
        code, _, err = cli(structure.main, "--structure", str(self.root), "flow", "remove", flow["id"], "--expect",
                           "0" * 16)
        self.assertEqual(code, 1)
        self.assertIn(f"(rev now {current})", err)
        code, out, _ = cli(structure.main, "--structure", str(self.root), "flow", "prompt", flow["id"], "--goal-file",
                           "-", "--expect", current, "--json", stdin="a goal")
        self.assertEqual(code, 0)
        self.assertIn("a goal", json.loads(out)["text"])
        code, out, _ = cli(structure.main, "--structure", str(self.root), "flow", "text", flow["id"], "--text-file",
                           "-", "--expect", current, "--json", stdin="Fresh.")
        self.assertEqual((code, json.loads(out)["flow"]["text"]), (0, "Fresh."))


class Groups(Scratch):
    def test_a_group_carries_the_rev_of_its_file(self):
        team = teams.add("impl-openai", ["designer"], None, self.root)[0]
        data = (self.root / "teams" / f"{team['id']}.md").read_bytes()
        self.assertEqual(team["rev"], agentfiles.rev(data))
        self.assertEqual(teams.teams(self.root)[0]["rev"], team["rev"])

    def test_each_write_refuses_a_stale_rev_and_takes_the_current_one(self):
        team = teams.add("impl-openai", ["designer"], None, self.root)[0]
        old = team["rev"]
        current = teams.set_text(team["id"], "Report.", self.root)["rev"]
        writes = [
            ("members", lambda expect: teams.set_members(team["id"], ["designer", "reviewer"], self.root, expect=expect)),
            ("text", lambda expect: teams.set_text(team["id"], "Report again.", self.root, expect=expect)),
            ("remove", lambda expect: teams.remove(team["id"], self.root, expect=expect)),
        ]
        for op, write in writes:
            with self.subTest(op=op):
                self.assertEqual(self.stale(write, old), current)
                write(current)
                if op != "remove":
                    current = teams.teams(self.root)[0]["rev"]
        self.assertEqual(teams.teams(self.root), [])

    def test_a_group_added_again_under_the_same_id_is_told_apart(self):
        # Members sent from an old list would drop the member someone else added.
        first = teams.add("impl-openai", ["designer"], None, self.root)[0]
        teams.remove(first["id"], self.root)
        second = teams.add("impl-openai", ["designer", "reviewer"], None, self.root)[0]
        self.assertEqual(second["id"], first["id"])
        self.stale(teams.set_members, first["id"], ["designer"], self.root, expect=first["rev"])
        self.assertEqual(teams.teams(self.root)[0]["members"], ["designer", "reviewer"])

    def test_the_command_line_takes_expect(self):
        team = teams.add("impl-openai", ["designer"], None, self.root)[0]
        code, out, _ = cli(teams.main, "--structure", str(self.root), "members", team["id"], "reviewer", "--expect",
                           "0" * 16, "--json")
        self.assertEqual((code, json.loads(out)["rev"]), (1, team["rev"]))
        code, out, _ = cli(teams.main, "--structure", str(self.root), "remove", team["id"], "--expect", team["rev"],
                           "--json")
        self.assertEqual((code, json.loads(out)["teams"]), (0, []))


class Skills(agentfile_tests.Base):
    def state(self):
        return self.snapshot(), tree(self.skills)

    def stale(self, function, *args, **kwargs):
        before = self.state()
        with self.assertRaisesRegex(agentfiles.StaleError, STALE) as caught:
            function(*args, **kwargs)
        self.assertEqual(self.state(), before, "a stale write changed something")
        return caught.exception.rev

    def skill_rev(self, skill="code-assurance"):
        return skillfiles.show(skill, skills=self.skills, bundled=())["rev"]

    def test_a_skill_carries_the_rev_of_its_skill_file(self):
        data = (self.skills / "code-assurance" / "SKILL.md").read_bytes()
        self.assertEqual(self.skill_rev(), agentfiles.rev(data))
        document = {"skills": [{"name": "code-assurance"}, {"name": "engineering-delivery"}]}
        skillfiles.annotate(document, skills=self.skills, bundled=())
        self.assertEqual(document["skills"][0]["rev"], agentfiles.rev(data))

    def test_skill_text_write_and_delete_refuse_a_stale_rev(self):
        where = {"skills": self.skills, "backups": self.backups}
        old = self.skill_rev()
        skillfiles.write("code-assurance", body="Changed\n", **where)
        current = self.skill_rev()
        self.assertEqual(self.stale(skillfiles.write, "code-assurance", body="Stale\n", expect=old, **where), current)
        skillfiles.write("code-assurance", body="Fresh\n", expect=current, **where)
        current = self.skill_rev()
        self.stale(skillfiles.delete, "code-assurance", config=self.config, wildcards_hold=False, force=True,
                   agents_dir=self.agents, expect=old, **where)
        skillfiles.delete("code-assurance", config=self.config, wildcards_hold=False, force=True, agents_dir=self.agents,
                          expect=current, **where)
        self.assertFalse((self.skills / "code-assurance").exists())

    def test_a_skills_change_refuses_a_stale_agent_rev(self):
        where = {"config": self.config, "map_path": self.map, "backups": self.backups, "agents_dir": self.agents,
                 "skills_dir": self.skills, "structure": self.structure}
        rev = lambda name: roster.agent_rev(name, config=self.config, map_path=self.map, agents_dir=self.agents,
                                            structure=self.structure)
        self.add(template="verifier")  # an added agent: its skills live in its file
        for agent in ("explorer", "checker"):
            with self.subTest(agent=agent):
                old = rev(agent)
                skillset.add(agent, "code-assurance", **where)
                current = rev(agent)
                self.assertNotEqual(current, old)
                self.assertEqual(self.stale(skillset.remove, agent, "code-assurance", expect=old, **where), current)
                self.stale(skillset.set_all, agent, expect=old, **where)
                skillset.remove(agent, "code-assurance", expect=current, **where)

    def test_the_command_lines_take_expect(self):
        code, out, _ = cli(skillfiles.main, "skill", "write", "code-assurance", "--skills", str(self.skills),
                           "--backups", str(self.backups), "--text-file", "-", "--expect", "0" * 16, "--json",
                           stdin="Stale\n")
        self.assertEqual((code, json.loads(out)["rev"]), (1, self.skill_rev()))
        code, out, _ = cli(skillset.main, "add", "explorer", "code-assurance", "--config", str(self.config), "--map",
                           str(self.map), "--backups", str(self.backups), "--agents", str(self.agents),
                           "--skills-dir", str(self.skills), "--structure", str(self.structure), "--expect", "0" * 16,
                           "--json")
        current = roster.agent_rev("explorer", config=self.config, map_path=self.map, agents_dir=self.agents,
                                   structure=self.structure)
        self.assertEqual((code, json.loads(out)["rev"]), (1, current))
        self.assertEqual(self.snapshot(), self.start)


class CommandLines(agentfile_tests.Base):
    """Every command that takes `--expect`, run as `agentgraph` runs it (`python3 -P -m agentgraph.MODULE`): a stale
    rev exits 1 with the plain message and the current rev, never a traceback; an empty one is bad input, never
    taken as no `--expect`. Nothing changes either way. Scratch folders only."""

    def setUp(self):
        super().setUp()
        self.commands = self.root / "commands"
        self.commands.mkdir()
        self.add(template="verifier")
        self.flow = structure.add_flow(["checker", "reviewer"], "Check.", self.structure, name="check-it",
                                       command=True, commands_dir=self.commands)[0]
        self.team = teams.add("checker", ["reviewer"], None, self.structure)[0]
        self.home = self.root / "home"  # no default path may lead to the real folders
        self.home.mkdir()

    def everything(self):
        return self.snapshot(), tree(self.skills), tree(self.commands), tree(self.home)

    def commands_with_expect(self):
        s, a, k, b = (str(self.structure), str(self.agents), str(self.skills), str(self.backups))
        c, m, cmd = str(self.config), str(self.map), str(self.commands)
        fid, tid = self.flow["id"], self.team["id"]
        agent_rev = lambda name: roster.agent_rev(name, config=self.config, map_path=self.map, agents_dir=self.agents,
                                                  structure=self.structure)
        flow_rev, team_rev = self.flow["rev"], self.team["rev"]
        skill_rev = skillfiles.show("code-assurance", skills=self.skills, bundled=())["rev"]
        skills = ["--config", c, "--map", m, "--backups", b, "--agents", a, "--skills-dir", k, "--structure", s]
        agents = ["--config", c, "--map", m, "--backups", b, "--agents", a, "--skills-dir", k, "--structure", s,
                  "--commands", cmd]
        files = ["--skills", k, "--backups", b, "--config", c, "--map", m, "--agents", a]
        return [  # (module, arguments, stdin, the current rev)
            ("structure", ["--structure", s, "flow", "text", fid, "--text-file", "-"], "New.", flow_rev),
            ("structure", ["--structure", s, "flow", "name", fid, "other-name", "--commands", cmd], "", flow_rev),
            ("structure", ["--structure", s, "flow", "command", fid, "--off", "--commands", cmd], "", flow_rev),
            ("structure", ["--structure", s, "flow", "prompt", fid, "--goal-file", "-"], "a goal", flow_rev),
            ("structure", ["--structure", s, "flow", "remove", fid, "--commands", cmd], "", flow_rev),
            ("teams", ["--structure", s, "members", tid, "designer"], "", team_rev),
            ("teams", ["--structure", s, "text", tid, "--text-file", "-"], "New.", team_rev),
            ("teams", ["--structure", s, "remove", tid], "", team_rev),
            ("skillfiles", ["skill", "write", "code-assurance", "--text-file", "-", *files], "New.\n", skill_rev),
            ("skillfiles", ["skill", "delete", "code-assurance", *files], "", skill_rev),
            ("skillset", ["add", "checker", "engineering-delivery", *skills], "", agent_rev("checker")),
            ("skillset", ["remove", "explorer", "code-assurance", *skills], "", agent_rev("explorer")),
            ("skillset", ["all", "explorer", *skills], "", agent_rev("explorer")),
            ("roster", ["agent", "text", "checker", "--text-file", "-", *agents], "New.", agent_rev("checker")),
            ("roster", ["agent", "rename", "checker", "auditor", *agents], "", agent_rev("checker")),
            ("roster", ["agent", "remove", "checker", *agents], "", agent_rev("checker")),
        ]

    def run_all(self, jobs):
        """Each (module, argv, stdin) as `agentgraph` runs it, in parallel: [(code, stdout, stderr)]."""
        environment = {**os.environ, "PYTHONPATH": str(ROOT), **home_env(self.home), "PYTHONIOENCODING": "utf-8"}

        def one(job):
            module, argv, stdin = job
            done = subprocess.run([sys.executable, "-P", "-m", f"agentgraph.{module}", *argv], input=stdin,
                                  capture_output=True, text=True, env=environment, cwd=str(self.root), timeout=120)
            return done.returncode, done.stdout, done.stderr

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            return list(pool.map(one, jobs))

    def test_a_stale_rev_by_command_is_a_plain_refusal_with_the_current_rev(self):
        before = self.everything()
        cases = self.commands_with_expect()
        jobs = [(module, [*argv, "--expect", "0" * 16], stdin) for module, argv, stdin, _ in cases]
        jobs += [(module, [*argv, "--expect", "0" * 16, "--json"], stdin) for module, argv, stdin, _ in cases]
        results = self.run_all(jobs)
        for (module, argv, _, current), (code, out, err) in zip(cases + cases, results):
            with self.subTest(module=module, argv=argv[:4]):
                self.assertNotIn("Traceback", err)
                self.assertEqual(code, 1, (out, err))
        for (module, argv, _, current), (code, out, err) in zip(cases, results[:len(cases)]):
            with self.subTest(module=module, argv=argv[:4], output="plain"):
                self.assertIn(STALE, err)
                self.assertIn(f"(rev now {current})", err)
        for (module, argv, _, current), (code, out, err) in zip(cases, results[len(cases):]):
            with self.subTest(module=module, argv=argv[:4], output="json"):
                answer = json.loads(out.strip().splitlines()[-1])
                self.assertEqual(set(answer), {"error", "rev"})
                self.assertIn(STALE, answer["error"])
                self.assertEqual(answer["rev"], current)
        self.assertEqual(self.everything(), before, "a stale command changed something")

    def test_an_empty_expect_is_bad_input_by_command(self):
        before = self.everything()
        cases = self.commands_with_expect()
        jobs = [(module, [*argv, "--expect", ""], stdin) for module, argv, stdin, _ in cases]
        jobs += [(module, [*argv, "--expect", "", "--json"], stdin) for module, argv, stdin, _ in cases]
        for (module, argv, _, _), (code, out, err) in zip(cases + cases, self.run_all(jobs)):
            with self.subTest(module=module, argv=argv[:4]):
                self.assertNotIn("Traceback", err)
                self.assertNotEqual(code, 0, (out, err))
                self.assertIn("expect", (out + err).lower())
                self.assertNotIn(STALE, out + err, "an empty expect was compared")
        self.assertEqual(self.everything(), before, "an empty expect changed something")


    def test_an_expect_that_is_not_a_rev_is_bad_input_by_command(self):
        # Review n8 r5, note: one rule on every surface: an `expect` must look like a rev (agentfiles.rev's 16
        # lowercase hex digits); a blank one, a short one or one in capitals is never compared.
        before = self.everything()
        cases = self.commands_with_expect()
        bads = (" ", "0" * 15, "ABCDEF0123456789")
        jobs = [(module, [*argv, "--expect", bad, *json_], stdin) for bad in bads for json_ in ([], ["--json"])
                for module, argv, stdin, _ in cases]
        labels = [(bad, bool(json_), module, argv) for bad in bads for json_ in ([], ["--json"])
                  for module, argv, _, _ in cases]
        for (bad, as_json, module, argv), (code, out, err) in zip(labels, self.run_all(jobs)):
            with self.subTest(module=module, argv=argv[:4], expect=bad, json=as_json):
                self.assertNotIn("Traceback", err)
                self.assertEqual(code, 1, (out, err))
                self.assertIn("expect must be", out if as_json else err)
                if as_json:
                    self.assertEqual(set(json.loads(out.strip().splitlines()[-1])), {"error"})
                self.assertNotIn(STALE, out + err, "a malformed expect was compared")
        self.assertEqual(self.everything(), before, "a malformed expect changed something")


class Endpoints(agentfile_tests.Base):
    """409 with {"error", "rev"} from every endpoint, the snapshot's revs taken as `expect`, old clients unchanged."""

    def setUp(self):
        super().setUp()
        self.commands = self.root / "commands"
        self.commands.mkdir()
        self.enterContext(patch.object(server, "build_snapshot", return_value=demo_snapshot(1_000_000.0)))
        self.httpd = server.make_server(
            0, graph_dir=self.root / "graphs", cwd=self.root, session="root", web_dir=self.root,
            structure_dir=self.structure, skills_config=self.config, skills_map=self.map, skills_backups=self.backups,
            skills_profiles=self.root / "profiles", skills_dir=self.skills, skills_bundled=[], agents_dir=self.agents,
            opencode_dir=self.root, ledger_root=self.root, commands_dir=self.commands,
            sessions_db=self.root / "none.db")
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(self.httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.httpd.shutdown)
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    post = structure_tests.Endpoint.post

    def document(self):
        status, body, _ = self.post(b"", method="GET", path="/api/snapshot")
        self.assertEqual(status, 200, body)
        return body

    def everything(self):
        return self.snapshot(), tree(self.skills), tree(self.commands)

    def refused(self, path, body):
        before = self.everything()
        status, answer, _ = self.post(body, path=path)
        self.assertEqual(status, 409, answer)
        self.assertEqual(set(answer), {"error", "rev"})
        self.assertIn(STALE, answer["error"])
        self.assertEqual(self.everything(), before, f"a stale {body.get('op', 'skills change')} changed something")
        return answer["rev"]

    def test_the_snapshot_carries_a_rev_for_each_thing_a_view_can_change(self):
        self.add(template="verifier")
        flow = self.post({"op": "add", "path": ["checker", "reviewer"]})[1]["flow"]
        team = self.post({"op": "add", "lead": "checker", "members": ["reviewer"]}, path="/api/structure/team")[1]["team"]
        document = self.document()
        flows = {item["id"]: item["rev"] for item in document["structure"]["flows"]}
        self.assertEqual(flows[flow["id"]], flow["rev"])
        self.assertEqual({item["id"]: item["rev"] for item in document["structure"]["teams"]}[team["id"]], team["rev"])
        skills = {item["name"]: item.get("rev") for item in document["skills"]}
        self.assertEqual(skills["code-assurance"], skillfiles.show("code-assurance", skills=self.skills, bundled=())["rev"])
        agents = {item["name"]: item.get("rev") for item in document["agents"]}  # the demo's roster
        rev = lambda name: roster.agent_rev(name, config=self.config, map_path=self.map, agents_dir=self.agents,
                                            structure=self.structure)
        for name in ("explorer", "reviewer"):
            self.assertEqual(agents.get(name), rev(name))
        status, read, _ = self.post({"op": "read", "name": "checker"}, path="/api/roster")
        self.assertEqual((status, read["rev"]), (200, rev("checker")))
        status, read, _ = self.post({"op": "read", "id": "code-assurance"}, path="/api/skillfile")
        self.assertEqual((status, read["rev"]), (200, skills["code-assurance"]))

    def test_flow_ops_answer_409_on_a_stale_rev(self):
        flow = self.post({"op": "add", "path": LOOP})[1]["flow"]
        stale = "0" * 16
        for body in ({"op": "text", "id": flow["id"], "text": "x"}, {"op": "name", "id": flow["id"], "name": "loop"},
                     {"op": "command", "id": flow["id"], "on": False}, {"op": "prompt", "id": flow["id"], "goal": "g"},
                     {"op": "remove", "id": flow["id"]}):
            with self.subTest(op=body["op"]):
                self.assertEqual(self.refused("/api/structure/flow", {**body, "expect": stale}), flow["rev"])
        status, answer, _ = self.post({"op": "text", "id": flow["id"], "text": "Fresh.", "expect": flow["rev"]})
        self.assertEqual((status, answer["flow"]["text"]), (200, "Fresh."))
        status, answer, _ = self.post({"op": "text", "id": flow["id"], "text": "Old client."})
        self.assertEqual(status, 200)
        status, answer, _ = self.post({"op": "remove", "id": flow["id"], "expect": 5})
        self.assertEqual(status, 400)
        status, answer, _ = self.post({"op": "add", "path": LOOP, "expect": flow["rev"]})
        self.assertEqual(status, 400, "an add takes no expect")

    def test_group_skill_text_and_skills_ops_answer_409_on_a_stale_rev(self):
        team = self.post({"op": "add", "lead": "impl-openai", "members": ["designer"]},
                         path="/api/structure/team")[1]["team"]
        for body in ({"op": "members", "id": team["id"], "members": ["reviewer"]},
                     {"op": "text", "id": team["id"], "text": "x"}, {"op": "remove", "id": team["id"]}):
            with self.subTest(op=body["op"]):
                self.assertEqual(self.refused("/api/structure/team", {**body, "expect": "0" * 16}), team["rev"])
        skill = skillfiles.show("code-assurance", skills=self.skills, bundled=())["rev"]
        for body in ({"op": "write", "id": "code-assurance", "text": "x"}, {"op": "delete", "id": "code-assurance"}):
            with self.subTest(op=body["op"]):
                self.assertEqual(self.refused("/api/skillfile", {**body, "expect": "0" * 16}), skill)
        agent = self.document()["agents"]
        explorer = next(item["rev"] for item in agent if item["name"] == "explorer")
        before = self.everything()
        status, answer, _ = self.post({"agent": "explorer", "skill": "code-assurance", "on": True, "expect": "0" * 16},
                                      path="/api/skills")
        self.assertEqual((status, answer.get("rev")), (409, explorer))
        self.assertEqual(self.everything(), before)
        status, answer, _ = self.post({"agent": "explorer", "skill": "code-assurance", "on": True, "expect": explorer},
                                      path="/api/skills")
        self.assertEqual(status, 200, answer)

    def round_trip(self, path, old, fresh_body, stale_body, now):
        """A write with the rev the view read is accepted; the next write with that same (now old) rev is 409,
        changes nothing, and names the rev a fresh read gives."""
        status, answer, _ = self.post({**fresh_body, "expect": old}, path=path)
        self.assertEqual(status, 200, answer)
        current = now()
        self.assertNotEqual(current, old, f"{path}: the write did not change the rev")
        self.assertEqual(self.refused(path, {**stale_body, "expect": old}), current)
        self.assertEqual(now(), current)
        return answer

    def test_end_to_end_per_kind_read_write_then_the_old_rev_is_refused(self):
        self.add(template="verifier")
        flow = self.post({"op": "add", "path": LOOP})[1]["flow"]
        team = self.post({"op": "add", "lead": "impl-openai", "members": ["designer"]},
                         path="/api/structure/team")[1]["team"]
        document = self.document()
        pick = lambda items, key, value: next(item["rev"] for item in items if item[key] == value)
        flows = lambda: pick(self.document()["structure"]["flows"], "id", flow["id"])
        teams_ = lambda: pick(self.document()["structure"]["teams"], "id", team["id"])
        skill = lambda: pick(self.document()["skills"], "name", "code-assurance")
        explorer = lambda: pick(self.document()["agents"], "name", "explorer")
        checker = lambda: self.post({"op": "read", "name": "checker"}, path="/api/roster")[1]["rev"]
        cases = [
            ("flow", "/api/structure/flow", pick(document["structure"]["flows"], "id", flow["id"]),
             {"op": "text", "id": flow["id"], "text": "Fresh."}, {"op": "remove", "id": flow["id"]}, flows),
            ("group", "/api/structure/team", pick(document["structure"]["teams"], "id", team["id"]),
             {"op": "members", "id": team["id"], "members": ["reviewer"]}, {"op": "remove", "id": team["id"]}, teams_),
            ("skill", "/api/skillfile", pick(document["skills"], "name", "code-assurance"),
             {"op": "write", "id": "code-assurance", "text": "Fresh text."},
             {"op": "write", "id": "code-assurance", "text": "Lost update."}, skill),
            ("skills", "/api/skills", pick(document["agents"], "name", "explorer"),
             {"agent": "explorer", "skill": "code-assurance", "on": True},
             {"agent": "explorer", "skill": "code-assurance", "on": False}, explorer),
            ("agent", "/api/roster", checker(), {"op": "text", "name": "checker", "text": "Check."},
             {"op": "text", "name": "checker", "text": "Lost update."}, checker),
        ]
        for kind, path, old, fresh, stale, now in cases:
            with self.subTest(kind=kind):
                self.round_trip(path, old, fresh, stale, now)
        self.assertEqual(self.post({"op": "read", "id": "code-assurance"}, path="/api/skillfile")[1]["text"],
                         "Fresh text.")
        self.assertEqual(self.post({"op": "read", "name": "checker"}, path="/api/roster")[1]["text"], "Check.")

    def test_an_empty_or_non_text_expect_is_400_on_every_endpoint(self):
        # One rule on every surface: an `expect` that is given is a rev; an empty one is bad input, never compared.
        self.add(template="verifier")
        flow = self.post({"op": "add", "path": LOOP})[1]["flow"]
        team = self.post({"op": "add", "lead": "impl-openai", "members": ["designer"]},
                         path="/api/structure/team")[1]["team"]
        requests = [("/api/structure/flow", {"op": "text", "id": flow["id"], "text": "x"}),
                    ("/api/structure/flow", {"op": "prompt", "id": flow["id"], "goal": "g"}),
                    ("/api/structure/team", {"op": "remove", "id": team["id"]}),
                    ("/api/skillfile", {"op": "write", "id": "code-assurance", "text": "x"}),
                    ("/api/skills", {"agent": "explorer", "skill": "code-assurance", "on": True}),
                    ("/api/roster", {"op": "text", "name": "checker", "text": "x"})]
        before = self.everything()
        for path, body in requests:
            for bad in ("", None, 5, []):
                with self.subTest(path=path, expect=bad):
                    status, answer, _ = self.post({**body, "expect": bad}, path=path)
                    self.assertEqual(status, 400, answer)
                    self.assertNotIn("rev", answer)
        self.assertEqual(self.everything(), before)

    def test_an_expect_that_is_not_a_rev_is_400_on_every_endpoint(self):
        # Review n8 r5, note: a blank rev (" ") was compared and answered 409; it is bad input, as on the Ipc.
        self.add(template="verifier")
        flow = self.post({"op": "add", "path": LOOP})[1]["flow"]
        team = self.post({"op": "add", "lead": "impl-openai", "members": ["designer"]},
                         path="/api/structure/team")[1]["team"]
        requests = [("/api/structure/flow", {"op": "text", "id": flow["id"], "text": "x"}),
                    ("/api/structure/flow", {"op": "remove", "id": flow["id"]}),
                    ("/api/structure/team", {"op": "remove", "id": team["id"]}),
                    ("/api/skillfile", {"op": "write", "id": "code-assurance", "text": "x"}),
                    ("/api/skills", {"agent": "explorer", "skill": "code-assurance", "on": True}),
                    ("/api/roster", {"op": "text", "name": "checker", "text": "x"}),
                    ("/api/roster", {"op": "remove", "name": "checker"})]
        before = self.everything()
        for path, body in requests:
            for bad in (" ", "0" * 15, "0" * 17, "ABCDEF0123456789", "0" * 16 + "\n"):
                with self.subTest(path=path, op=body.get("op"), expect=bad):
                    status, answer, _ = self.post({**body, "expect": bad}, path=path)
                    self.assertEqual(status, 400, answer)
                    self.assertNotIn("rev", answer)
        self.assertEqual(self.everything(), before)

    def test_roster_ops_take_expect_through_the_endpoint(self):
        self.add(template="verifier")
        current = self.post({"op": "read", "name": "checker"}, path="/api/roster")[1]["rev"]
        for body in ({"op": "text", "name": "checker", "text": "Check."}, {"op": "rename", "name": "checker", "to": "c2"},
                     {"op": "remove", "name": "checker"}):
            with self.subTest(op=body["op"]):
                self.assertEqual(self.refused("/api/roster", {**body, "expect": "0" * 16}), current)
        status, answer, _ = self.post({"op": "text", "name": "checker", "text": "Check.", "expect": current},
                                      path="/api/roster")
        self.assertEqual(status, 200, answer)


if __name__ == "__main__":
    unittest.main()
