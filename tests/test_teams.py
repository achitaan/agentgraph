"""Teams: the `teams` folder, its rules, `problems` on task graphs and `python3 -m agentgraph.teams`."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from agentgraph import agentfiles, teams
from tests.winskip import needs_symlinks
from agentgraph.structure import StructureError

ROOT = Path(__file__).resolve().parent.parent

FILES = {
    "00-hub/orchestrator.md": "The hub.\n",
    "10-council/council.md": "seats: councillor-\n\nThe council lead.\n",
    "10-council/councillor-a.md": "A seat.\n",
    "20-build/impl-openai.md": "pool: 3\n\nThe default implementer.\n",
    "20-build/impl-claude.md": "The bigger implementer.\n",
    "20-build/designer.md": "Designs things.\n",
    "30-verify/reviewer.md": "Reviews a finished change.\n",
    "30-verify/verifier.md": "Checks one loop item.\n",
    "40-understand/researcher.md": "Finds things out.\n",
}


def make_structure(root: Path) -> Path:
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text.encode("utf-8"))
    return root


def tree(root: Path) -> dict:
    return {path.relative_to(root).as_posix(): (path.read_bytes() if path.is_file() else "dir")
            for path in sorted(root.rglob("*"))}


def node(node_id, agent, depends_on=(), status="pending"):
    return {"id": node_id, "agent": agent, "depends_on": list(depends_on), "status": status}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.start = tree(self.root)

    def refused(self, call, *args, **kwargs):
        before = tree(self.root)
        with self.assertRaises(StructureError) as caught:
            call(*args, **kwargs)
        self.assertEqual(tree(self.root), before, "a refused request changed something")
        return str(caught.exception)


class Writing(Base):
    def test_no_teams_folder_reads_as_no_teams_and_is_created_by_the_first_add(self):
        self.assertEqual(teams.teams(self.root), [])
        self.assertEqual(teams.read_teams(self.root), ([], []))
        self.assertNotIn("teams", tree(self.root))
        team, notes = teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        self.assertEqual(notes, [])
        self.assertEqual(tree(self.root)["teams"], "dir")
        self.assertEqual((self.root / "teams" / "reviewer.md").read_bytes(),
                         b"lead: reviewer\nmembers: impl-openai, impl-claude\n\n"
                         b"impl-openai and impl-claude report to reviewer. reviewer reconciles their results "
                         b"and sends one report to orchestrator.\n")
        self.assertEqual(team, {"id": "reviewer", "lead": "reviewer", "members": ["impl-openai", "impl-claude"],
                                "text": "impl-openai and impl-claude report to reviewer. reviewer reconciles their "
                                        "results and sends one report to orchestrator.", "generated": True,
                                "rev": agentfiles.rev((self.root / "teams" / "reviewer.md").read_bytes())})

    def test_the_generated_instruction_is_grammatical_for_one_two_and_more_members(self):
        self.assertEqual(teams.team_text("A", ["B"]),
                         "B reports to A. A reconciles its result and sends one report to orchestrator.")
        self.assertEqual(teams.team_text("A", ["B", "C"]),
                         "B and C report to A. A reconciles their results and sends one report to orchestrator.")
        self.assertEqual(teams.team_text("A", ["B", "C", "D"]),
                         "B, C and D report to A. A reconciles their results and sends one report to orchestrator.")

    def test_a_given_text_is_kept_verbatim_and_is_not_generated(self):
        text = "First.\n\nlead: not a setting\n\tTabbed."
        team, _ = teams.add("reviewer", ["impl-openai"], text, self.root)
        self.assertEqual((team["text"], team["generated"]), (text, False))
        self.assertEqual(teams.teams(self.root), [team])

    def test_a_blank_text_on_add_means_generated(self):
        team, _ = teams.add("reviewer", ["impl-openai"], "  \n", self.root)
        self.assertTrue(team["generated"])

    def test_set_text_replaces_and_a_blank_text_writes_it_again(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        edited = teams.set_text("reviewer", "Do it my way.", self.root)
        self.assertEqual((edited["text"], edited["generated"]), ("Do it my way.", False))
        again = teams.set_text("reviewer", " \n\t", self.root)
        self.assertTrue(again["generated"])
        self.assertIn("impl-openai and impl-claude report to reviewer", again["text"])

    def test_set_members_regenerates_a_generated_text_and_keeps_an_edited_one(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        team, _ = teams.set_members("reviewer", ["designer"], self.root)
        self.assertEqual(team["members"], ["designer"])
        self.assertEqual(team["text"], teams.team_text("reviewer", ["designer"]))
        self.assertTrue(team["generated"])
        teams.set_text("reviewer", "Mine.", self.root)
        team, _ = teams.set_members("reviewer", ["designer", "researcher"], self.root)
        self.assertEqual((team["members"], team["text"], team["generated"]),
                         (["designer", "researcher"], "Mine.", False))

    def test_add_text_members_and_remove_in_a_copy_leave_it_byte_identical(self):
        team, _ = teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        teams.set_text("reviewer", "Mine.", self.root)
        teams.set_members("reviewer", ["designer"], self.root)
        gone = teams.remove("reviewer", self.root)
        self.assertEqual(gone["members"], ["designer"])
        self.assertEqual(tree(self.root), self.start)
        self.assertNotIn("teams", tree(self.root))

    def test_remove_returns_what_undoes_it_under_the_same_id_byte_for_byte(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], "Mine.", self.root)
        teams.add("verifier", ["designer"], structure_dir=self.root)
        before = tree(self.root)
        gone = teams.remove("reviewer", self.root)
        self.assertNotIn("teams/reviewer.md", tree(self.root))
        self.assertIn("teams/verifier.md", tree(self.root))  # the folder stays while a team is in it
        back, _ = teams.add(gone["lead"], gone["members"], gone["text"], self.root)
        self.assertEqual(back, gone)
        self.assertEqual(tree(self.root), before)
        gone = teams.remove("verifier", self.root)  # a generated text is undone the same way
        teams.add(gone["lead"], gone["members"], gone["text"], self.root)
        self.assertEqual(tree(self.root), before)

    def test_a_council_seat_stands_for_the_council(self):
        team, notes = teams.add("reviewer", ["councillor-a", "designer"], structure_dir=self.root)
        self.assertEqual(team["members"], ["council", "designer"])
        self.assertEqual(notes, ["councillor-a is a seat of the council: stored as council"])
        led, notes = teams.add("councillor-a", ["researcher"], structure_dir=self.root)
        self.assertEqual((led["lead"], led["id"]), ("council", "council"))
        self.assertTrue(notes)

    def test_an_agent_may_be_in_several_teams_and_lead_one_while_a_member_of_another(self):
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        teams.add("verifier", ["impl-openai", "designer"], structure_dir=self.root)
        teams.add("researcher", ["reviewer"], structure_dir=self.root)  # reviewer leads and reports to researcher
        self.assertEqual([t["id"] for t in teams.teams(self.root)], ["researcher", "reviewer", "verifier"])

    def test_every_team_is_sorted_by_id(self):
        teams.add("verifier", ["designer"], structure_dir=self.root)
        teams.add("reviewer", ["designer"], structure_dir=self.root)
        self.assertEqual([t["id"] for t in teams.teams(self.root)], ["reviewer", "verifier"])


class FileLimit(Base):
    def test_the_limit_counts_files_not_the_teams_that_can_be_read(self):
        folder = self.root / "teams"
        folder.mkdir()
        for index in range(teams.TEAM_FILES_LIMIT):
            (folder / f"junk-{index:03d}.md").write_text("nothing readable here\n", encoding="utf-8", newline="\n")
        self.assertEqual(teams.teams(self.root), [])
        message = self.refused(teams.add, "reviewer", ["impl-openai"], None, self.root)
        self.assertIn(f"{teams.TEAM_FILES_LIMIT} team files", message)
        (folder / "junk-000.md").unlink()
        team, _ = teams.add("reviewer", ["impl-openai"], structure_dir=self.root)
        self.assertEqual(team["id"], "reviewer")


class Refusals(Base):
    def test_each_refusal_and_its_message(self):
        teams.add("reviewer", ["impl-openai"], structure_dir=self.root)
        cases = [
            ((teams.add, "reviewer", ["designer"]), "reviewer already leads a team (reviewer); change that one instead"),
            ((teams.add, "ghost", ["designer"]), "unknown agent ghost: there is no ghost.md in the structure folder"),
            ((teams.add, "verifier", ["ghost"]), "unknown agent ghost"),
            ((teams.add, "orchestrator", ["designer"]), "orchestrator is the hub: everything already reports to it, so it cannot lead a team"),
            ((teams.add, "verifier", ["orchestrator"]), "orchestrator is the hub: a team's report goes to it"),
            ((teams.add, "verifier", ["verifier", "designer"]), "verifier is the lead: it cannot also be a member of its own team"),
            ((teams.add, "verifier", ["designer", "designer"]), "designer is listed twice among the members"),
            ((teams.add, "verifier", ["councillor-a", "council"]), "council is listed twice among the members"),
            ((teams.add, "verifier", []), "a team needs at least one member"),
            ((teams.add, "verifier", [f"m{n}" for n in range(13)]), "a team has at most 12 members"),
            ((teams.add, "verifier", "designer"), "the members must be a list of agent names"),
            ((teams.add, "verifier", ["designer", 3]), "the members must be a list of agent names"),
            ((teams.add, "", ["designer"]), "the lead must be an agent name"),
            ((teams.add, "verifier", ["designer"], "x" * 2001), "the instruction is 2001 characters long"),
            ((teams.add, "verifier", ["designer"], "bad\x00text"), "control character U+0000"),
            ((teams.add, "verifier", ["designer"], 5), "the instruction must be text"),
            ((teams.set_text, "reviewer", "x" * 2001), "the instruction is 2001 characters long"),
            ((teams.set_text, "reviewer", "a\x1bb"), "control character U+001B"),
            ((teams.set_text, "ghost", "x"), "there is no team 'ghost'"),
            ((teams.set_text, 5, "x"), "the team id must be text"),
            ((teams.set_members, "reviewer", []), "a team needs at least one member"),
            ((teams.set_members, "reviewer", ["reviewer"]), "reviewer is the lead"),
            ((teams.set_members, "reviewer", ["orchestrator"]), "orchestrator is the hub"),
            ((teams.set_members, "ghost", ["designer"]), "there is no team 'ghost'"),
            ((teams.remove, "ghost"), "there is no team 'ghost'"),
            ((teams.remove, "../x"), "there is no team '../x'"),
        ]
        for (call, *args), message in cases:
            with self.subTest(call=call.__name__, args=args):
                kwargs = {"structure_dir": self.root} if call is teams.add else {}
                if call is not teams.add:
                    args = [*args, self.root]
                self.assertIn(message, self.refused(call, *args, **kwargs))

    def test_a_refusal_names_the_team_that_already_has_the_lead(self):
        teams.add("reviewer", ["impl-openai"], structure_dir=self.root)
        self.assertIn("(reviewer)", self.refused(teams.add, "reviewer", ["designer"], structure_dir=self.root))

    def test_a_cycle_of_leads_is_refused_directly_and_through_others(self):
        teams.add("reviewer", ["impl-openai"], structure_dir=self.root)  # impl-openai reports to reviewer
        message = self.refused(teams.add, "impl-openai", ["reviewer"], structure_dir=self.root)
        self.assertIn("reviewer cannot report to impl-openai", message)
        self.assertIn("impl-openai → reviewer", message)
        teams.add("verifier", ["reviewer"], structure_dir=self.root)  # reviewer reports to verifier
        message = self.refused(teams.add, "impl-openai", ["verifier"], structure_dir=self.root)
        self.assertIn("impl-openai → reviewer → verifier", message)
        self.assertIn("verifier cannot report to impl-openai", message)

    def test_set_members_cannot_close_a_cycle_either(self):
        teams.add("reviewer", ["impl-openai"], structure_dir=self.root)
        teams.add("impl-openai", ["designer"], structure_dir=self.root)  # designer → impl-openai → reviewer
        self.assertIn("cannot report to", self.refused(teams.set_members, "impl-openai", ["reviewer"], self.root))
        # its own current members are not in the way of a change
        teams.set_members("reviewer", ["impl-openai", "impl-claude"], self.root)

    @needs_symlinks
    def test_a_symlinked_teams_folder_or_file_is_refused_and_nothing_is_followed(self):
        other = self.root.parent / (self.root.name + "-elsewhere")
        other.mkdir()
        self.addCleanup(lambda: [p.unlink() for p in other.iterdir()] or other.rmdir())
        os.symlink(other, self.root / "teams", target_is_directory=True)
        self.assertEqual(teams.teams(self.root), [])
        self.assertIn("not a plain folder", self.refused(teams.add, "reviewer", ["designer"], structure_dir=self.root))
        self.assertEqual(list(other.iterdir()), [])
        os.unlink(self.root / "teams")
        teams.add("reviewer", ["designer"], structure_dir=self.root)
        (other / "elsewhere.md").write_text("lead: verifier\nmembers: designer\n\nx\n", encoding="utf-8", newline="\n")
        os.symlink(other / "elsewhere.md", self.root / "teams" / "linked.md")
        self.assertEqual([t["id"] for t in teams.teams(self.root)], ["reviewer"])
        self.assertIn("not a regular file", self.refused(teams.set_text, "linked", "changed", self.root))
        self.assertEqual((other / "elsewhere.md").read_text(encoding="utf-8"), "lead: verifier\nmembers: designer\n\nx\n")

    def test_no_structure_folder(self):
        missing = self.root / "nowhere"
        self.assertEqual(teams.teams(missing), [])
        self.assertIn("no structure folder", self.refused(teams.add, "reviewer", ["designer"], structure_dir=missing))


class Reading(Base):
    def put(self, name, data):
        path = self.root / "teams" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

    def test_teams_files_are_never_taken_for_agents(self):
        self.put("reviewer.md", "lead: reviewer\nmembers: designer\n\nx\n")
        self.put("designer.md", "lead: verifier\nmembers: researcher\n\ny\n")
        self.assertEqual([t["id"] for t in teams.teams(self.root)], ["designer", "reviewer"])
        self.assertIn("unknown agent ghost", self.refused(teams.add, "ghost", ["designer"], structure_dir=self.root))

    def test_unreadable_or_invalid_files_are_skipped_with_a_warning_never_a_failure(self):
        self.put("good.md", "lead: reviewer\nmembers: designer\n\nText.\n")
        self.put("nolead.md", "members: designer\n\nText.\n")
        self.put("latin.md", b"lead: verifier\nmembers: designer\n\nCaf\xe9\n")
        self.put("ghost.md", "lead: ghost\nmembers: designer\n\nText.\n")
        self.put("hubbed.md", "lead: orchestrator\nmembers: designer\n\nText.\n")
        self.put("twice.md", "lead: reviewer\nmembers: researcher\n\nText.\n")
        self.put("Bad Name.md", "lead: verifier\nmembers: designer\n")
        self.put("notes.txt", "lead: verifier\nmembers: designer\n")
        found, warnings = teams.read_teams(self.root)
        self.assertEqual([t["id"] for t in found], ["good"])
        text = " | ".join(warnings)
        for part in ("team ghost ignored: unknown agent ghost", "team hubbed ignored: orchestrator is the hub",
                     "team latin ignored", "team nolead ignored", "team twice ignored: reviewer already leads"):
            self.assertIn(part, text)

    def test_a_file_that_closes_a_cycle_is_skipped(self):
        self.put("a.md", "lead: reviewer\nmembers: verifier\n\nx\n")
        self.put("b.md", "lead: verifier\nmembers: reviewer\n\nx\n")
        found, warnings = teams.read_teams(self.root)
        self.assertEqual([t["id"] for t in found], ["a"])
        self.assertIn("team b ignored", warnings[0])

    def test_a_seat_in_a_file_stands_for_the_council_and_generated_is_compared_with_the_text(self):
        self.put("x.md", "lead: reviewer\nmembers: councillor-a, designer\n\n"
                         "council and designer report to reviewer. reviewer reconciles their results and sends one "
                         "report to orchestrator.\n")
        (found,) = teams.teams(self.root)
        self.assertEqual(found["members"], ["council", "designer"])
        self.assertTrue(found["generated"])

    def test_the_file_format_round_trips(self):
        data = b"lead: reviewer\nmembers: impl-openai, impl-claude\n\nFirst.\n\nlead: nobody\n\tTabbed.\n"
        self.assertEqual(teams.read_team(data), ("reviewer", ["impl-openai", "impl-claude"], "First.\n\nlead: nobody\n\tTabbed."))
        self.assertEqual(teams.team_file("reviewer", ["impl-openai", "impl-claude"],
                                         "First.\n\nlead: nobody\n\tTabbed."), data)
        self.assertIsNone(teams.read_team(b"lead: a\nmembers: \n\nx"))
        self.assertIsNone(teams.read_team(b"x" * 20000))


class Problems(unittest.TestCase):
    TEAM = [{"id": "reviewer", "lead": "reviewer", "members": ["impl-openai", "impl-claude"], "text": "", "generated": True}]

    def graph(self, *nodes):
        return {"nodes": list(nodes)}

    def test_two_member_nodes_need_a_lead_node_that_depends_on_both(self):
        graph = self.graph(node("a", "impl-openai"), node("b", "impl-claude"))
        (message,) = teams.problems(self.TEAM, graph)
        for part in ("reviewer", "a (impl-openai)", "b (impl-claude)"):
            self.assertIn(part, message)

    def test_a_lead_node_that_depends_on_both_directly_or_through_others_passes(self):
        direct = self.graph(node("a", "impl-openai"), node("b", "impl-claude"), node("r", "reviewer", ["a", "b"]))
        through = self.graph(node("a", "impl-openai"), node("b", "impl-claude", ["a"]), node("m", "designer", ["b"]),
                             node("r", "reviewer", ["m"]))
        self.assertEqual(teams.problems(self.TEAM, direct), [])
        self.assertEqual(teams.problems(self.TEAM, through), [])

    def test_a_lead_node_that_leaves_one_out_names_only_that_one(self):
        graph = self.graph(node("a", "impl-openai"), node("b", "impl-claude"), node("r", "reviewer", ["a"]))
        (message,) = teams.problems(self.TEAM, graph)
        self.assertIn("b (impl-claude)", message)
        self.assertNotIn("a (impl-openai)", message)

    def test_one_or_no_member_node_adds_no_rule(self):
        self.assertEqual(teams.problems(self.TEAM, self.graph(node("a", "impl-openai"), node("x", "designer"))), [])
        self.assertEqual(teams.problems(self.TEAM, self.graph(node("x", "designer"))), [])
        self.assertEqual(teams.problems(self.TEAM, self.graph()), [])

    def test_two_nodes_of_one_member_count(self):
        graph = self.graph(node("a", "impl-openai"), node("b", "impl-openai"))
        self.assertEqual(len(teams.problems(self.TEAM, graph)), 1)

    def test_a_seat_counts_for_its_council_when_the_seats_are_given(self):
        team = [{"id": "council", "lead": "reviewer", "members": ["council", "designer"]}]
        graph = self.graph(node("a", "councillor-a"), node("b", "designer"))
        self.assertEqual(teams.problems(team, graph), [])
        self.assertEqual(len(teams.problems(team, graph, {"council": "councillor-"})), 1)

    def test_a_graph_with_a_dependency_cycle_or_unknown_ids_never_loops_or_raises(self):
        graph = self.graph(node("a", "impl-openai", ["b", "ghost"]), node("b", "impl-claude", ["a"]),
                           node("r", "reviewer", ["a"]))
        self.assertEqual(teams.problems(self.TEAM, graph), [])

    def test_odd_input_never_raises(self):
        for team_input, graph in [(None, None), ([], {}), ("x", {"nodes": []}), ([None, 3, {}], {"nodes": "x"}),
                                  ([{"lead": 3, "members": 4}], {"nodes": [None, 1, {"id": 3}]}),
                                  (self.TEAM, {"nodes": [{"id": "a", "agent": "impl-openai", "depends_on": 5},
                                                         {"id": "b", "agent": ["x"]}, {"agent": "impl-claude"}]}),
                                  (self.TEAM, object()), ({"a": 1}, {"nodes": [{"id": "a", "agent": "x", "depends_on": [[]]}]})]:
            self.assertIsInstance(teams.problems(team_input, graph), list)

    def test_odd_seats_and_deep_or_cyclic_graphs_still_give_a_list(self):
        graph = self.graph(node("a", "impl-openai", ["a"]), node("b", "impl-claude", ["a", "r"]),
                           node("r", "reviewer", ["b"]))
        for seats in (None, [1], "x", 5, {1: 2, "reviewer": 3, "x": ""}, {"reviewer": "impl-"}):
            self.assertIsInstance(teams.problems(self.TEAM, graph, seats), list)
        chain = [node(f"n{i}", "impl-openai" if i % 2 else "impl-claude", [f"n{i - 1}"] * (i > 0))
                 for i in range(3000)]
        self.assertEqual(len(teams.problems(self.TEAM, self.graph(*chain, node("r", "reviewer", ["n2999"])))), 0)
        self.assertEqual(len(teams.problems(self.TEAM, self.graph(*chain))), 1)

    def test_a_real_bug_in_the_rule_is_not_swallowed(self):
        graph = self.graph(node("a", "impl-openai"), node("b", "impl-claude"))
        with unittest.mock.patch.object(teams, "_names", side_effect=RuntimeError("bug")):
            with self.assertRaisesRegex(RuntimeError, "bug"):
                teams.problems(self.TEAM, graph)

    def test_a_task_graph_object_is_read_like_its_json(self):
        from agentgraph.contract import Node, TaskGraph
        nodes = [Node("a", "implement", "impl-openai", "g"), Node("b", "implement", "impl-claude", "g"),
                 Node("r", "gate", "reviewer", "g", depends_on=["a"])]
        graph = TaskGraph("p", "t", "g", "running", 0.0, 0.0, nodes)
        (message,) = teams.problems(self.TEAM, graph)
        self.assertIn("b (impl-claude)", message)


class ChangedUnderneath(Base):
    """A team file another program rewrites between this write's read and its replace is refused, never overwritten."""

    def setUp(self):
        super().setUp()
        teams.add("reviewer", ["impl-openai", "impl-claude"], structure_dir=self.root)
        self.path = self.root / "teams" / "reviewer.md"

    def meanwhile(self, data=b"lead: reviewer\nmembers: designer\n\nEdited by hand.\n"):
        real = teams._load
        calls = []

        def load(*args, **kwargs):
            answer = real(*args, **kwargs)
            if not calls:
                self.path.write_bytes(data)
            calls.append(1)
            return answer
        return unittest.mock.patch.object(teams, "_load", side_effect=load), data

    def check(self, call, *args):
        patcher, data = self.meanwhile()
        with patcher, self.assertRaises(teams.TeamError) as caught:
            call(*args, structure_dir=self.root)
        self.assertIn("changed while this edit was being made", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), data, "the other program's edit was overwritten")

    def test_set_text_refuses(self):
        self.check(teams.set_text, "reviewer", "Mine.")

    def test_set_members_refuses(self):
        self.check(teams.set_members, "reviewer", ["designer", "researcher"])

    def test_remove_refuses(self):
        self.check(teams.remove, "reviewer")


class Command(Base):
    def run_main(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if stdin is not None:
                from unittest.mock import patch
                with patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin.encode()))):
                    code = teams.main(["--structure", str(self.root), *argv])
            else:
                code = teams.main(["--structure", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_the_whole_round_through_the_command(self):
        self.assertEqual(self.run_main("list"), (0, "no teams\n", ""))
        code, out, _ = self.run_main("add", "reviewer", "impl-openai", "impl-claude")
        self.assertEqual(code, 0)
        self.assertIn("added team reviewer: impl-openai, impl-claude report to reviewer", out)
        code, out, _ = self.run_main("list")
        self.assertIn("    impl-openai and impl-claude report to reviewer. reviewer reconciles", out)
        text = self.root.parent / (self.root.name + ".txt")
        self.addCleanup(lambda: text.unlink() if text.exists() else None)
        text.write_text("Mine, from a file.", encoding="utf-8", newline="\n")
        self.assertEqual(self.run_main("text", "reviewer", "--text-file", str(text))[0], 0)
        self.assertEqual(self.run_main("text", "reviewer", "--text-file", "-", stdin="From stdin.")[0], 0)
        self.assertIn("From stdin.", self.run_main("list")[1])
        self.assertEqual(self.run_main("members", "reviewer", "designer", "researcher")[0], 0)
        listing = json.loads(self.run_main("list", "--json")[1])
        self.assertEqual(listing, {"teams": [{"id": "reviewer", "lead": "reviewer", "members": ["designer", "researcher"],
                                              "text": "From stdin.", "generated": False,
                                              "rev": agentfiles.rev((self.root / "teams" / "reviewer.md").read_bytes())}]})
        code, _, err = self.run_main("add", "reviewer", "designer")
        self.assertEqual(code, 1)
        self.assertIn("reviewer already leads a team (reviewer)", err)
        code, _, err = self.run_main("add", "orchestrator", "designer")
        self.assertEqual(code, 1)
        self.assertIn("is the hub", err)
        self.assertEqual(self.run_main("remove", "reviewer")[0], 0)
        self.assertEqual(tree(self.root), self.start)

    def test_errors_are_json_with_json_and_a_usage_error_exits_two(self):
        code, out, _ = self.run_main("remove", "ghost", "--json")
        self.assertEqual((code, json.loads(out)), (1, {"error": "there is no team 'ghost'"}))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            teams.main(["nonsense"])
        self.assertEqual(caught.exception.code, 2)

    def test_no_members_is_a_refusal_not_a_usage_error(self):
        code, _, err = self.run_main("add", "reviewer")
        self.assertEqual((code, "at least one member" in err), (1, True))

    def test_text_never_comes_from_the_command_line(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            teams.main(["--structure", str(self.root), "add", "reviewer", "designer", "--text", "hello"])
        self.assertEqual(caught.exception.code, 2)

    def test_module_runs_as_a_script(self):
        done = subprocess.run([sys.executable, "-m", "agentgraph.teams", "list", "--structure", str(self.root)],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout), (0, "no teams\n"))


if __name__ == "__main__":
    unittest.main()
