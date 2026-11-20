"""Drawn flows: the `flows` folder, `agentgraph structure flows|flow`, the check rule and the endpoint."""
import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import check, server, snapshot_json, structure
from agentgraph.demo import demo_snapshot

ROOT = Path(__file__).resolve().parent.parent

FILES = {
    "00-hub/orchestrator.md": "The hub.\n",
    "10-council/council.md": "seats: councillor-\n\nThe council lead.\n",
    "10-council/councillor-a.md": "A seat.\n",
    "20-build/impl-openai.md": "pool: 3\nescalates_to: impl-claude\n\nThe default implementer.\n",
    "20-build/impl-claude.md": "escalates_to: impl-hard\n\nThe bigger implementer.\n",
    "20-build/designer.md": "Designs things.\nSecond line: with a colon.\n",
    "30-verify/reviewer.md": "Reviews a finished change.\n",
    "30-verify/verifier.md": "checks: all\n\nChecks one loop item.\n",
    "30-verify/empty.md": "",
    "40-understand/researcher.md": "Finds things out.\n",
}

HUB = frozenset({"orchestrator"})
TWO = "Hand A's finished work to B as its input."
CHAIN = "A's work goes to B, then B's work goes to C."
LOOP = "B checks A's work. If it does not pass, send B's findings back to A and repeat until it passes."
TRIANGLE = "A's work goes to B, then to C. If C does not accept it, send C's findings back to A and repeat until it passes."

SELF = "A checks its own work against the task's evaluation and repeats until it passes."

def make_structure(root: Path, extra: dict | None = None) -> Path:
    for rel, text in {**FILES, **(extra or {})}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text.encode("utf-8"))
    return root


def tree(root: Path) -> dict:
    return {str(path.relative_to(root)): (path.read_bytes() if path.is_file() else "dir")
            for path in sorted(root.rglob("*"))}


def write_flow(root: Path, flow_id: str, data) -> Path:
    path = root / "flows" / f"{flow_id}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


class Loader(unittest.TestCase):
    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())))

    def test_flow_file_round_trip(self):
        data = b"path: researcher -> impl-claude -> reviewer\n\nFirst line.\n\npath: not -> a setting\n\tTabbed.\n"
        write_flow(self.root, "mine", data)
        shape = snapshot_json.load_structure(self.root)
        flow = {"id": "mine", "path": ["researcher", "impl-claude", "reviewer"],
                "text": "First line.\n\npath: not -> a setting\n\tTabbed.", "name": None, "command": None}
        self.assertEqual(shape["flows"], [flow])
        self.assertEqual(snapshot_json.read_flow(data), (flow["path"], flow["text"]))
        self.assertEqual(structure.flow_file(flow["path"], flow["text"]), data)
        self.assertEqual(shape["warnings"], [])

    def test_the_flows_folder_is_never_a_group_of_agents(self):
        write_flow(self.root, "a", "path: impl-openai -> verifier -> impl-openai\n\nText.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertNotIn("flows", shape["groups"])
        self.assertNotIn("a", shape["group_of"])
        self.assertEqual(shape["groups"], ["hub", "council", "build", "verify", "understand"])

    def test_a_teams_folder_is_never_a_group_of_agents(self):
        # Teams are kept in `teams/` (see agentgraph.teams); they are not wired in here, only kept out of the groups.
        (self.root / "teams").mkdir()
        (self.root / "teams" / "builders.md").write_text("lead: council\nmembers: impl-openai, designer\n\nBuild.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertNotIn("teams", shape["groups"])
        self.assertNotIn("builders", shape["group_of"])
        self.assertEqual(shape["warnings"], [])

    def test_unreadable_or_invalid_flows_warn_and_never_fail(self):
        write_flow(self.root, "ghost", "path: impl-openai -> ghost\n\nText.\n")
        write_flow(self.root, "middle", "path: impl-openai -> orchestrator -> verifier\n\nText.\n")
        write_flow(self.root, "nopath", "Just text.\n")
        write_flow(self.root, "latin", b"path: impl-openai -> verifier\n\nCaf\xe9\n")
        write_flow(self.root, "good", "path: impl-openai -> verifier\n\nText.\n")
        (self.root / "flows" / "notes.txt").write_text("path: a -> b\n")
        (self.root / "flows" / "Bad Name.md").write_text("path: impl-openai -> verifier\n")
        os.symlink(self.root / "flows" / "good.md", self.root / "flows" / "linked.md")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual([flow["id"] for flow in shape["flows"]], ["good"])
        warnings = " | ".join(shape["warnings"])
        for text in ("flow ghost ignored: unknown agent ghost", "flow middle ignored: the hub orchestrator",
                     "flow nopath ignored", "flow latin ignored"):
            self.assertIn(text, warnings)

    def test_a_council_seat_in_a_flow_file_stands_for_the_council(self):
        write_flow(self.root, "seat", "path: impl-openai -> councillor-a\n\nText.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual([flow["path"] for flow in shape["flows"]], [["impl-openai", "council"]])

    def test_a_second_loop_of_one_agent_on_itself_is_ignored_with_a_warning(self):
        write_flow(self.root, "impl-claude-impl-claude", "path: impl-claude -> impl-claude\n\nMine.\n")
        write_flow(self.root, "impl-claude-impl-claude-2", "path: impl-claude -> impl-claude\n\nAgain.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual([flow["id"] for flow in shape["flows"]], ["impl-claude-impl-claude"])
        self.assertIn("structure: flow impl-claude-impl-claude-2 ignored: impl-claude already loops on itself in flow impl-claude-impl-claude",
                      shape["warnings"])

    def test_more_flow_files_than_are_read_are_named_in_a_warning_never_dropped_silently(self):
        for index in range(snapshot_json.FLOW_FILES_LIMIT + 2):
            write_flow(self.root, f"f{index:03d}", "path: impl-openai -> reviewer\n\nx\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual(len(shape["flows"]), snapshot_json.FLOW_FILES_LIMIT)
        self.assertIn("structure: 202 flow files, more than the 200 read; ignored: f200, f201", shape["warnings"])

    def test_checked_by_is_no_longer_a_setting(self):
        (self.root / "20-build/designer.md").write_text("checked_by: reviewer\n\nDesigns things.\n")
        shape = snapshot_json.load_structure(self.root)
        self.assertEqual(shape["about"]["designer"], "checked_by: reviewer Designs things.")
        self.assertNotIn("checked_by", snapshot_json.SETTING_KEYS)

    def test_snapshot_carries_flows_and_no_default_loops(self):
        write_flow(self.root, "impl-openai-verifier-impl-openai", f"path: impl-openai -> verifier -> impl-openai\n\n{LOOP}\n")
        # `checks: all` is no longer a setting: it connects nothing and is read as the start of the description.
        (self.root / "30-verify/reviewer.md").write_text("checks: all\n\nReviews.\n")
        doc = snapshot_json.snapshot_document(demo_snapshot(1000), "demo", structure=snapshot_json.load_structure(self.root))
        self.assertEqual(sorted(doc["structure"]), ["flows", "source", "teams"])
        self.assertEqual(doc["structure"]["flows"], [{"id": "impl-openai-verifier-impl-openai", "path": ["impl-openai", "verifier", "impl-openai"],
                                                      "text": LOOP, "name": None, "command": None, "source": "drawn"}])
        self.assertTrue(all("checks" not in agent for agent in doc["agents"]))
        self.assertNotIn("checks", snapshot_json.SETTING_KEYS)

    def test_builtin_snapshot_has_no_flows(self):
        doc = snapshot_json.snapshot_document(demo_snapshot(1000), "demo")
        self.assertEqual(doc["structure"], {"source": "builtin", "flows": [], "teams": []})


class Text(unittest.TestCase):
    def test_the_five_shapes(self):
        cases = {
            "A B": TWO,
            "A B C": CHAIN,
            "A B A": LOOP,
            "A B C A": TRIANGLE,
            "orchestrator A B": "Start with A. " + TWO,
            "A B orchestrator": TWO[:-1] + ", then report back.",
            "orchestrator A B A orchestrator": "Start with A. " + LOOP[:-1] + ", then report back.",
            "orchestrator A": "Start with A.",
            "A orchestrator": "Start with A, then report back.",
        }
        for path, text in cases.items():
            with self.subTest(path=path):
                self.assertEqual(structure.flow_text(path.split(), HUB), text)

    def test_a_loop_on_itself(self):
        for name in ("A", "orchestrator"):
            self.assertEqual(structure.flow_text([name, name], HUB), SELF.replace("A", name, 1))

    def test_longer_paths_stay_within_three_sentences(self):
        self.assertEqual(structure.flow_text("A B A C".split(), HUB), LOOP + " Then hand A's finished work to C as its input.")
        self.assertEqual(structure.flow_text("A B C D".split(), HUB), "A's work goes to B, then B's work goes to C, then C's work goes to D.")
        long = structure.flow_text("A B C B D A".split(), HUB)
        self.assertTrue(long.startswith("Pass the work along A → B → C → B → D → A"), long)
        for path in ("A B C B", "A B A C B", "orchestrator A B C B D orchestrator", "A B C D E F G"):
            with self.subTest(path=path):
                text = structure.flow_text(path.split(), HUB)
                self.assertLessEqual(text.count(". ") + 1, 3, text)

    def test_a_return_straight_after_a_return(self):
        # One sentence per return; the made-up text never fails (it used to raise IndexError here).
        self.assertEqual(structure._sentences("A B C B A".split()),
                         ["Hand A's finished work to B as its input.", "Then C checks B's work.", "If it does not pass, send C's findings back to B and repeat until it passes.",
                          "Then, if B does not accept it, send B's findings back to A and repeat until it passes."])
        self.assertIn("A → B → C → B → A", structure.flow_text("A B C B A".split(), HUB))

    def test_text_bounds_and_control_characters(self):
        self.assertEqual(structure.clean_text("x" * 2000), "x" * 2000)
        self.assertEqual(structure.clean_text("a\n\tb ✓ \u2028"), "a\n\tb ✓ \u2028")
        with self.assertRaisesRegex(structure.StructureError, "at most 2000"):
            structure.clean_text("x" * 2001)
        for bad in ("\x00", "\r", "\x1b[31m", "\x7f", "\x85", "\ud800"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(structure.StructureError, "control character"):
                    structure.clean_text("before" + bad)


class Command(unittest.TestCase):
    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.before = tree(self.root)

    def run_cli(self, *args, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        fake = io.TextIOWrapper(io.BytesIO(stdin.encode() if isinstance(stdin, str) else stdin or b""))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch.object(sys, "stdin", fake):
            code = structure.main([*args, "--structure", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def flows(self):
        return json.loads(self.run_cli("flows", "--json")[1])["flows"]

    def test_add_change_and_remove_leave_no_trace(self):
        code, out, _ = self.run_cli("flow", "add", "impl-openai", "verifier", "impl-openai")
        self.assertEqual(code, 0)
        self.assertEqual(out, "added flow impl-openai-verifier-impl-openai: impl-openai → verifier → impl-openai\n"
                              "    verifier checks impl-openai's work. If it does not pass, send verifier's findings back "
                              "to impl-openai and repeat until it passes.\n")
        self.assertEqual((self.root / "flows/impl-openai-verifier-impl-openai.md").read_text(),
                         "path: impl-openai -> verifier -> impl-openai\n\nverifier checks impl-openai's work. If it does not pass, "
                         "send verifier's findings back to impl-openai and repeat until it passes.\n")
        code, out, _ = self.run_cli("flow", "add", "researcher", "impl-claude", "reviewer", "--json")
        added = json.loads(out)
        self.assertEqual(added["flow"], {"id": "researcher-impl-claude-reviewer", "path": ["researcher", "impl-claude", "reviewer"],
                                         "text": "researcher's work goes to impl-claude, then impl-claude's work goes to reviewer.",
                                         "name": None, "command": None, "source": "drawn"})
        self.assertEqual([flow["id"] for flow in added["flows"]], ["impl-openai-verifier-impl-openai", "researcher-impl-claude-reviewer"])
        code, out, _ = self.run_cli("flow", "text", "researcher-impl-claude-reviewer", "--text-file", "-", stdin="Mine.\n")
        self.assertEqual((code, self.flows()[1]["text"]), (0, "Mine.\n"))
        self.assertEqual(self.run_cli("flow", "remove", "impl-openai-verifier-impl-openai")[1], "removed flow impl-openai-verifier-impl-openai\n")
        self.assertTrue((self.root / "flows").is_dir())
        self.run_cli("flow", "remove", "researcher-impl-claude-reviewer")
        self.assertFalse((self.root / "flows").exists(), "the empty flows folder is removed")
        self.assertEqual(tree(self.root), self.before)
        self.assertEqual(self.run_cli("flows")[1], "no drawn flows\n")

    def test_text_from_a_file_and_text_that_looks_like_settings(self):
        text = "path: impl-openai -> ghost\nchecks: all\n\n\nKeep blank lines.\n\tAnd tabs."
        source = self.root.parent / f"{self.root.name}-text.txt"
        source.write_text(text)
        self.addCleanup(source.unlink)
        code, out, _ = self.run_cli("flow", "add", "impl-openai", "reviewer", "--text-file", str(source), "--json")
        flow = json.loads(out)["flow"]
        self.assertEqual((code, flow["path"], flow["text"]), (0, ["impl-openai", "reviewer"], text))
        self.assertEqual(snapshot_json.load_structure(self.root)["flows"][0]["path"], ["impl-openai", "reviewer"])
        self.run_cli("flow", "text", flow["id"], "--text-file", "-", stdin="\n\nStarts with blank lines")
        self.assertEqual(self.flows()[0]["text"], "\n\nStarts with blank lines")

    def test_blank_text_means_the_text_made_from_the_path(self):
        code, out, _ = self.run_cli("flow", "add", "impl-openai", "reviewer", "--text-file", "-", "--json", stdin="  \n")
        flow = json.loads(out)["flow"]
        self.assertEqual(flow["text"], "Hand impl-openai's finished work to reviewer as its input.")
        self.run_cli("flow", "text", flow["id"], "--text-file", "-", stdin="Other.")
        self.run_cli("flow", "text", flow["id"], "--text-file", "-", stdin="")
        self.assertEqual(self.flows()[0]["text"], "Hand impl-openai's finished work to reviewer as its input.")

    def test_whitespace_only_edited_text_means_write_it_again_from_the_path(self):
        # Kept as behaviour (round 2, finding 3): an edit that leaves the box empty or only whitespace brings back
        # the generated text; it is never stored as whitespace.
        flow = json.loads(self.run_cli("flow", "add", "impl-openai", "reviewer", "--json")[1])["flow"]
        generated = "Hand impl-openai's finished work to reviewer as its input."
        for blank in (" ", "\n\n", " \t \n "):
            with self.subTest(blank=blank):
                self.run_cli("flow", "text", flow["id"], "--text-file", "-", stdin="Mine.")
                self.assertEqual(self.flows()[0]["text"], "Mine.")
                code, out, _ = self.run_cli("flow", "text", flow["id"], "--text-file", "-", "--json", stdin=blank)
                self.assertEqual((code, json.loads(out)["flow"]["text"], self.flows()[0]["text"]), (0, generated, generated))
                self.assertEqual((self.root / "flows" / f"{flow['id']}.md").read_text(),
                                 f"path: impl-openai -> reviewer\n\n{generated}\n")

    def test_a_loop_on_itself_once_per_agent(self):
        code, out, _ = self.run_cli("flow", "add", "impl-claude", "impl-claude", "--json")
        flow = json.loads(out)["flow"]
        self.assertEqual((code, flow["id"], flow["path"], flow["text"]),
                         (0, "impl-claude-impl-claude", ["impl-claude", "impl-claude"], SELF.replace("A", "impl-claude", 1)))
        before = tree(self.root)
        code, out, err = self.run_cli("flow", "add", "impl-claude", "impl-claude")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("impl-claude already has a loop on itself (flow impl-claude-impl-claude)", err)
        self.assertEqual(tree(self.root), before)
        # Any agent may have one: the hub too, and a seat stands for its council (whose loop it then is).
        self.assertEqual(self.run_cli("flow", "add", "orchestrator", "orchestrator")[0], 0)
        code, out, _ = self.run_cli("flow", "add", "councillor-a", "councillor-a", "--json")
        self.assertEqual((code, json.loads(out)["flow"]["path"]), (0, ["council", "council"]))
        self.assertIn("council already has a loop on itself", self.run_cli("flow", "add", "council", "council")[2])
        # Inside a longer path the same agent twice in a row is still refused; a longer path with a return is not a self-loop.
        for names in (["impl-claude", "impl-claude", "impl-claude"], ["impl-openai", "impl-claude", "impl-claude"], ["impl-claude", "impl-claude", "reviewer"]):
            with self.subTest(names=names):
                self.assertIn("follows itself", self.run_cli("flow", "add", *names)[2])
        self.assertEqual(self.run_cli("flow", "add", "impl-claude", "reviewer", "impl-claude")[0], 0)
        self.run_cli("flow", "remove", "impl-claude-impl-claude")
        self.assertEqual(self.run_cli("flow", "add", "impl-claude", "impl-claude")[0], 0, "a removed loop on itself can be drawn again")

    def test_the_flow_files_limit_is_refused_before_anything_is_written(self):
        for index in range(snapshot_json.FLOW_FILES_LIMIT):
            write_flow(self.root, f"f{index:03d}", "path: impl-openai -> reviewer\n\nx\n")
        before = tree(self.root)
        code, out, err = self.run_cli("flow", "add", "impl-claude", "reviewer")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("already holds 200 flows, the most it reads is 200; remove one first. Nothing was written", err)
        code, out, _ = self.run_cli("flow", "add", "impl-claude", "reviewer", "--json")
        self.assertEqual(code, 1)
        self.assertIn("Nothing was written", json.loads(out)["error"])
        self.assertEqual(tree(self.root), before)
        self.run_cli("flow", "remove", "f000")
        code, out, _ = self.run_cli("flow", "add", "impl-claude", "reviewer", "--json")
        self.assertEqual(code, 0, out)
        self.assertIn("impl-claude-reviewer", [flow["id"] for flow in json.loads(out)["flows"]])

    def test_refusals(self):
        for names, message in ((["impl-openai", "ghost"], "unknown agent ghost"),
                               (["impl-openai"], "at least two agents"),
                               (["impl-openai", "reviewer", "reviewer"], "reviewer follows itself"),
                               (["impl-openai", "council", "councillor-a"], "council follows itself"),
                               (["impl-openai", "orchestrator", "verifier"], "can only start or end a flow"),
                               (["impl-openai", "verifier", "impl-openai", "verifier"], "straight after it returned"),
                               (["orchestrator", "impl-openai", "verifier", "impl-openai", "reviewer", "impl-openai", "verifier"],
                                "straight after it returned"),
                               (["impl-openai", "verifier", "reviewer", "verifier", "reviewer"],
                                "reviewer cannot get the work back from verifier"),
                               (["impl-openai", "reviewer"] * 11, "at most 20 steps")):
            with self.subTest(names=names):
                code, out, err = self.run_cli("flow", "add", *names)
                self.assertEqual((code, out), (1, ""))
                self.assertIn(message, err)
                code, out, _ = self.run_cli("flow", "add", *names, "--json")
                self.assertEqual(code, 1)
                self.assertIn(message, json.loads(out)["error"])
        self.assertEqual(tree(self.root), self.before)

    def test_text_that_is_too_long_or_holds_control_characters_is_refused(self):
        for text in ("x" * 2001, "bell\x07", "carriage\r\nreturn", b"\xff\xfe not utf-8"):
            with self.subTest(text=text[:20]):
                code, _, err = self.run_cli("flow", "add", "impl-openai", "reviewer", "--text-file", "-", stdin=text)
                self.assertEqual(code, 1)
                self.assertTrue(any(word in err for word in ("2000", "control character", "UTF-8")), err)
        self.assertEqual(tree(self.root), self.before)

    def test_a_seat_stands_for_the_council(self):
        code, out, _ = self.run_cli("flow", "add", "councillor-a", "impl-openai", "--json")
        doc = json.loads(out)
        self.assertEqual((code, doc["flow"]["path"]), (0, ["council", "impl-openai"]))
        self.assertEqual(doc["notes"], ["councillor-a is a seat of the council: stored as council"])
        self.assertIn("stored as council", self.run_cli("flow", "add", "impl-openai", "councillor-a")[1])
        self.assertEqual([flow["path"] for flow in self.flows()], [["council", "impl-openai"], ["impl-openai", "council"]])

    def test_ids_are_made_unique(self):
        ids = [json.loads(self.run_cli("flow", "add", "impl-openai", "reviewer", "--json")[1])["flow"]["id"] for _ in range(3)]
        self.assertEqual(ids, ["impl-openai-reviewer", "impl-openai-reviewer-2", "impl-openai-reviewer-3"])
        flow_id = json.loads(self.run_cli("flow", "add", "orchestrator", "impl-openai", "--json")[1])["flow"]["id"]
        self.assertEqual(flow_id, "orchestrator-impl-openai")

    def test_ids_are_looked_up_never_used_as_paths(self):
        self.run_cli("flow", "add", "impl-openai", "reviewer")
        outside = self.root / "outside.md"
        outside.write_text("path: impl-openai -> reviewer\n\nOutside.\n")
        before = tree(self.root)
        for flow_id in ("ghost", "../outside", "../00-hub/orchestrator", "impl-openai-reviewer/../impl-openai-reviewer",
                        "/etc/passwd", "IMPL-OPENAI-REVIEWER", "impl-openai-reviewer.md", ""):
            for verb in (("text", flow_id, "--text-file", "-"), ("remove", flow_id)):
                with self.subTest(verb=verb):
                    code, _, err = self.run_cli("flow", *verb, stdin="New.")
                    self.assertEqual(code, 1)
                    self.assertIn("there is no flow", err)
        self.assertEqual(tree(self.root), before)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(structure.main(["flow", "remove", "ghost", "--structure", str(self.root / "missing")]), 1)
        self.assertIn("no structure folder", err.getvalue())

    def test_symlinks_and_odd_entries_are_refused(self):
        self.run_cli("flow", "add", "impl-openai", "reviewer")
        flow = self.root / "flows/impl-openai-reviewer.md"
        target = self.root.parent / f"{self.root.name}-target.md"
        target.write_text("path: impl-openai -> reviewer\n\nElsewhere.\n")
        self.addCleanup(target.unlink)
        flow.unlink()
        flow.symlink_to(target)
        for verb in (("text", "impl-openai-reviewer", "--text-file", "-"), ("remove", "impl-openai-reviewer")):
            code, _, err = self.run_cli("flow", *verb, stdin="New.")
            self.assertEqual(code, 1)
            self.assertIn("not a regular file", err)
        self.assertTrue(flow.is_symlink())
        self.assertEqual(target.read_text(), "path: impl-openai -> reviewer\n\nElsewhere.\n")
        # A symlinked flows folder, or a file named flows, is never written through.
        flow.unlink()
        (self.root / "flows").rmdir()
        elsewhere = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "flows").symlink_to(elsewhere, target_is_directory=True)
        code, _, err = self.run_cli("flow", "add", "impl-openai", "reviewer")
        self.assertEqual(code, 1)
        self.assertIn("not a plain folder", err)
        self.assertEqual(list(elsewhere.iterdir()), [])
        (self.root / "flows").unlink()
        (self.root / "flows").write_text("a file")
        code, _, err = self.run_cli("flow", "add", "impl-openai", "reviewer")
        self.assertEqual(code, 1)
        self.assertIn("not a plain folder", err)
        self.assertEqual((self.root / "flows").read_text(), "a file")

    def test_a_failed_write_leaves_the_old_file_and_no_temporary_file(self):
        self.run_cli("flow", "add", "impl-openai", "reviewer", "--text-file", "-", stdin="Old.")
        before = tree(self.root)
        with patch.object(structure.os, "replace", side_effect=OSError("disk full")):
            code, _, err = self.run_cli("flow", "text", "impl-openai-reviewer", "--text-file", "-", stdin="New.")
        self.assertEqual(code, 1)
        self.assertIn("disk full", err)
        self.assertEqual(tree(self.root), before)
        with patch.object(structure.os, "link", side_effect=OSError("disk full")):
            self.assertEqual(self.run_cli("flow", "add", "impl-openai", "verifier")[0], 1)
        self.assertEqual(tree(self.root), before)

    def test_a_flow_file_that_cannot_be_read_can_still_be_removed(self):
        write_flow(self.root, "broken", "No path here.\n")
        code, _, err = self.run_cli("flow", "text", "broken", "--text-file", "-", stdin="New.")
        self.assertEqual(code, 1)
        self.assertIn("cannot be read as a flow", err)
        self.assertEqual(self.run_cli("flow", "remove", "broken")[0], 0)
        self.assertEqual(tree(self.root), self.before)

    def test_two_processes_adding_at_once_both_land(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        for _ in range(3):
            running = [subprocess.Popen([sys.executable, "-P", "-m", "agentgraph.structure", "flow", "add", "impl-openai", "reviewer",
                                         "--structure", str(self.root)], cwd=ROOT, env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
            for process in running:
                _, err = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, err)
        self.assertEqual(sorted(flow["id"] for flow in self.flows()),
                         ["impl-openai-reviewer"] + [f"impl-openai-reviewer-{n}" for n in range(2, 7)])
        threads = [threading.Thread(target=structure.add_flow, args=(["researcher", "designer"], None, self.root)) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(self.flows()), 9)
        self.assertEqual([name for name in os.listdir(self.root / "flows") if not name.endswith(".md")], [])

    def test_shell_entry_point_round_trip_never_runs_git(self):
        script = ROOT / "bin" / "agentgraph"

        def run(*args, stdin=None):
            result = subprocess.run(["sh", str(script), "structure", *args, "--structure", str(self.root)],
                                    capture_output=True, text=True, input=stdin, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        run("flow", "add", "impl-openai", "verifier", "impl-openai")
        run("flow", "text", "impl-openai-verifier-impl-openai", "--text-file", "-", stdin="Edited\non two lines.")
        self.assertEqual(json.loads(run("flows", "--json"))["flows"][0]["text"], "Edited\non two lines.")
        run("flow", "remove", "impl-openai-verifier-impl-openai")
        self.assertEqual(tree(self.root), self.before)
        self.assertNotIn("git", (ROOT / "agentgraph" / "structure.py").read_text().replace("agentgraph", ""))


def graph(nodes):
    return {"task": "flows", "goal": "Follow drawn flows", "status": "running", "created": 1, "updated": 2, "revision": 1,
            "objectives": [{"id": "o1", "text": "Deliver", "nodes": [nodes[0]["id"]]}], "nodes": nodes}


def node(id_, type_, agent, status="running", **extra):
    return {"id": id_, "type": type_, "agent": agent, "goal": id_, "status": status, "depends_on": [],
            "scope": [], "done_when": "tests pass", **extra}


def gate(id_, agent, depends_on, feedback_to):
    return node(id_, "gate", agent, status="pending", depends_on=depends_on, feedback_to=feedback_to)


class Enforcement(unittest.TestCase):
    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.graphs = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def flow(self, *names):
        structure.add_flow(list(names), None, self.root)

    def run_check(self, nodes, structure_dir=None):
        (self.graphs / "flows.json").write_text(json.dumps(graph(nodes)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(self.graphs), "--structure", str(structure_dir or self.root)])
        return code, out.getvalue()

    def problems(self, nodes):
        shape = snapshot_json.load_structure(self.root)
        return check.flow_problems(check.graph_from_raw(graph(nodes), self.graphs / "flows.json"), shape["flows"],
                                   snapshot_json.hub_names(shape))

    def assert_passes(self, nodes):
        code, out = self.run_check(nodes)
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("OK "), out)

    def test_a_two_agent_loop(self):
        self.flow("impl-openai", "verifier", "impl-openai")
        label = "flow impl-openai → verifier → impl-openai"
        self.assertEqual(self.problems([node("n12", "implement", "impl-openai")]),
                         [f"n12 (impl-openai) is not followed by verifier: {label}"])
        self.assertEqual(self.problems([node("n12", "implement", "impl-openai"),
                                        node("c1", "check", "verifier", status="pending", depends_on=["n12"])]),
                         [f"n12 (impl-openai) is not sent back to impl-openai after verifier (c1): c1 must be a gate "
                          f"whose feedback_to names n12: {label}"])
        self.assert_passes([node("n12", "implement", "impl-openai"), gate("g1", "verifier", ["n12"], ["n12"])])

    def test_a_three_step_chain(self):
        self.flow("researcher", "impl-claude", "reviewer")
        label = "flow researcher → impl-claude → reviewer"
        nodes = [node("r1", "research", "researcher"), node("i1", "implement", "impl-claude", status="pending", depends_on=["r1"])]
        self.assertEqual(self.problems(nodes[:1]), [f"r1 (researcher) is not followed by impl-claude: {label}"])
        self.assertEqual(self.problems(nodes), [f"r1 (researcher) is not followed by reviewer after impl-claude (i1): {label}"])
        self.assert_passes(nodes + [node("c1", "check", "reviewer", status="pending", depends_on=["i1"])])

    def test_a_three_agent_loop(self):
        self.flow("impl-openai", "reviewer", "verifier", "impl-openai")
        label = "flow impl-openai → reviewer → verifier → impl-openai"
        nodes = [node("n1", "implement", "impl-openai"), node("c1", "check", "reviewer", status="pending", depends_on=["n1"])]
        self.assertEqual(self.problems(nodes), [f"n1 (impl-openai) is not followed by verifier after reviewer (c1): {label}"])
        wrong = nodes + [gate("g1", "verifier", ["c1"], ["c1"])]
        self.assertEqual(self.problems(wrong), [f"n1 (impl-openai) is not sent back to impl-openai after verifier (g1): "
                                                f"g1 must be a gate whose feedback_to names n1: {label}"])
        self.assert_passes(nodes + [gate("g1", "verifier", ["c1"], ["n1"])])

    def test_a_path_continuing_after_a_return(self):
        self.flow("impl-openai", "verifier", "impl-openai", "reviewer")
        nodes = [node("n1", "implement", "impl-openai"), gate("g1", "verifier", ["n1"], ["n1"])]
        self.assertEqual(self.problems(nodes), ["n1 (impl-openai) is not followed by reviewer: "
                                                "flow impl-openai → verifier → impl-openai → reviewer"])
        self.assert_passes(nodes + [node("c1", "check", "reviewer", status="pending", depends_on=["n1"])])

    def test_the_hub_first_and_last(self):
        self.flow("orchestrator", "impl-openai", "reviewer")
        self.flow("designer", "reviewer", "orchestrator")
        self.flow("orchestrator", "researcher")
        problems = self.problems([node("n1", "implement", "impl-openai"), node("d1", "design", "designer")])
        self.assertEqual(problems, ["d1 (designer) is not followed by reviewer: flow designer → reviewer → orchestrator",
                                    "n1 (impl-openai) is not followed by reviewer: flow orchestrator → impl-openai → reviewer"])
        self.assertEqual(self.problems([node("o1", "decision", "orchestrator")]), [], "the hub's own nodes start nothing")
        self.assert_passes([node("n1", "implement", "impl-openai"), node("d1", "design", "designer"),
                            gate("g1", "reviewer", ["n1", "d1"], ["n1", "d1"])])

    def test_only_unfinished_work_of_the_first_agent_starts_a_chain(self):
        self.flow("impl-openai", "verifier", "impl-openai")
        statuses = ("pending", "running", "returned", "failed", "blocked", "passed")
        nodes = [node(f"n-{status}", "implement", "impl-openai", status=status, **({"evidence": "ok"} if status == "passed" else {}))
                 for status in statuses]
        nodes += [node("c1", "check", "impl-openai"), gate("g0", "impl-openai", [], []), node("r1", "research", "verifier")]
        self.assertEqual(self.problems(nodes), [f"n-{status} (impl-openai) is not followed by verifier: "
                                                "flow impl-openai → verifier → impl-openai" for status in statuses[:-1]])
        self.assert_passes([node("n1", "implement", "impl-openai", status="passed", evidence="ok")])
        for type_ in ("design", "research", "decision"):
            with self.subTest(type=type_):
                self.assertEqual(len(self.problems([node("x", type_, "impl-openai")])), 1)

    def test_a_gate_reached_through_feedback_to_only(self):
        # The gate depends on the lane's predecessor, not on the lane.
        self.flow("researcher", "impl-claude", "reviewer", "impl-claude")
        nodes = [node("r1", "research", "researcher"), node("i1", "implement", "impl-claude", status="pending", depends_on=["r1"]),
                 gate("g1", "reviewer", ["r1"], ["i1"])]
        self.assert_passes(nodes)
        self.flow("impl-openai", "verifier", "impl-openai")
        self.assert_passes([node("r0", "research", "researcher", status="passed", evidence="ok"),
                            node("n1", "implement", "impl-openai", depends_on=["r0"]),
                            gate("g1", "verifier", ["r0"], ["n1"])])

    def test_a_gate_by_another_agent_or_for_another_node_does_not_count(self):
        self.flow("impl-openai", "reviewer", "impl-openai")
        problems = self.problems([node("n1", "implement", "impl-openai"), node("n2", "implement", "impl-openai"),
                                  gate("g1", "verifier", ["n1"], ["n1"]), gate("g2", "reviewer", ["n2"], ["n2"])])
        self.assertEqual(problems, ["n1 (impl-openai) is not followed by reviewer: flow impl-openai → reviewer → impl-openai"])

    def test_the_flows_are_printed_after_the_result_without_changing_it(self):
        nodes = [node("n1", "implement", "impl-openai"), gate("g1", "verifier", ["n1"], ["n1"])]
        empty = Path(self.enterContext(tempfile.TemporaryDirectory()))
        make_structure(empty)
        plain_code, plain = self.run_check(nodes, empty)
        self.flow("impl-openai", "verifier", "impl-openai")
        structure.set_flow_text("impl-openai-verifier-impl-openai", "Two lines:\nthe second.", self.root)
        code, out = self.run_check(nodes)
        self.assertEqual((code, out.splitlines()[0]), (plain_code, plain.splitlines()[0]))
        self.assertEqual(out[len(plain):], "flows: the user's standing instructions for shaping task graphs (agentgraph structure flows):\n"
                                           "  impl-openai → verifier → impl-openai  [impl-openai-verifier-impl-openai]\n"
                                           "      Two lines:\n      the second.\n")
        code, out = self.run_check(nodes[:1])
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("PROBLEMS"), out)
        self.assertIn("flows: the user's standing instructions", out)

    def test_a_loop_on_itself_adds_no_rule_but_is_printed(self):
        self.flow("impl-openai", "impl-openai")
        self.assertEqual(self.problems([node("n1", "implement", "impl-openai", status="passed")]), [])
        code, out = self.run_check([node("n1", "implement", "impl-openai")])
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("OK "), out)
        self.assertIn("  impl-openai → impl-openai  [impl-openai-impl-openai]\n      " + SELF.replace("A", "impl-openai", 1), out)

    def test_a_named_structure_folder_must_exist_but_a_missing_default_is_silent(self):
        (self.graphs / "flows.json").write_text(json.dumps(graph([node("n12", "implement", "impl-openai")])))
        for missing in (self.root / "mistyped", self.graphs):
            with self.subTest(folder=missing):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = check.main(["--dir", str(self.graphs), "--structure", str(missing)])
                self.assertEqual(code, 2)
                self.assertIn(f"structure folder {missing} does not exist", out.getvalue())
        out = io.StringIO()
        with patch.object(snapshot_json, "STRUCTURE_DIR", self.root / "absent"), contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(self.graphs)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertNotIn("structure", out.getvalue())

    def test_no_flows_changes_nothing(self):
        before = tree(self.root)
        nodes = [node("n12", "implement", "impl-openai"), node("c1", "check", "verifier", status="pending", depends_on=["n12"])]
        code, out = self.run_check(nodes)
        self.assertEqual(code, 0, out)
        self.assertNotIn("standing instructions", out)
        self.assertEqual(check.flow_problems(check.graph_from_raw(graph(nodes), self.graphs / "flows.json"), []), [])
        self.assertEqual(tree(self.root), before)


def plain_graph(drawn, stored, hubs):
    """The plain task graph a flow describes: one node per agent on the path, in order of first visit. A step
    to a new agent is a node that depends on the current one; a return makes the current node a gate whose
    feedback_to names the earlier agent's node, and the chain goes on from there. Each node is run by the
    agent as drawn (a seat runs the council's node). None when the path leaves no working pair."""
    working, _ = snapshot_json.flow_steps(stored, hubs)
    if snapshot_json.is_self_loop(stored) or len(working) < 2:
        return None
    drawn = [name for name, kept in zip(drawn, stored) if kept in working] if len(drawn) == len(stored) else drawn
    offset = 1 if stored[0] in hubs else 0
    nodes, node_of, current = [], {}, None
    for index, agent in enumerate(working):
        if agent not in node_of:
            node_id = f"n{len(nodes)}"
            nodes.append(node(node_id, "implement" if not nodes else "check", drawn_name(drawn, stored, offset + index),
                              status="running" if not nodes else "pending", depends_on=[current] if current else []))
            node_of[agent], current = node_id, node_id
        else:
            gate_node = next(item for item in nodes if item["id"] == current)
            gate_node.update(type="gate", status="pending")
            gate_node.setdefault("feedback_to", []).append(node_of[agent])
            current = node_of[agent]
    return nodes


def drawn_name(drawn, stored, index):
    return drawn[index] if index < len(drawn) else stored[index]


class Satisfiable(unittest.TestCase):
    """Every flow `add_flow` accepts can be followed: the plain task graph it describes passes `validate` and
    `flow_problems`. Every path of two to five agents over a small roster with the hub, the council and a seat."""

    NAMES = ("orchestrator", "council", "councillor-a", "impl-openai", "verifier", "reviewer")

    def setUp(self):
        self.root = make_structure(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.before = tree(self.root)
        self.shape = snapshot_json.load_structure(self.root)
        self.hubs = snapshot_json.hub_names(self.shape)

    def test_every_accepted_path_up_to_five_agents_has_a_graph_that_follows_it(self):
        import itertools
        accepted = refused = followed = 0
        for length in range(2, 6):
            for drawn in itertools.product(self.NAMES, repeat=length):
                drawn = list(drawn)
                try:
                    flow, _ = structure.add_flow(drawn, None, self.root)
                except structure.StructureError as exc:
                    refused += 1
                    self.assertTrue(str(exc).strip(), drawn)
                    continue
                accepted += 1
                structure.remove_flow(flow["id"], self.root)
                nodes = plain_graph(drawn, flow["path"], self.hubs)
                if nodes is None:
                    continue
                followed += 1
                graph_ = check.graph_from_raw(graph(nodes), Path("/nonexistent/.opencode/graph/flows.json"))
                problems = check.validate(graph_) + check.flow_problems(graph_, [flow], self.hubs, self.shape["seats"])
                self.assertEqual(problems, [], (drawn, nodes))
        self.assertEqual(accepted + refused, sum(len(self.NAMES) ** n for n in range(2, 6)))
        self.assertGreater(followed, 1000)
        self.assertEqual(tree(self.root), self.before)

    def test_a_second_return_to_work_done_after_the_check_is_refused_with_a_clear_message(self):
        for names in (["impl-openai", "verifier", "reviewer", "verifier", "reviewer"],
                      ["orchestrator", "impl-openai", "verifier", "reviewer", "verifier", "reviewer", "orchestrator"]):
            with self.subTest(names=names):
                with self.assertRaises(structure.StructureError) as caught:
                    structure.add_flow(names, None, self.root)
                self.assertIn("reviewer cannot get the work back from verifier", str(caught.exception))
                self.assertIn("came after", str(caught.exception))
        self.assertEqual(tree(self.root), self.before)

    def test_a_return_to_another_branch_is_accepted(self):
        # impl-openai → verifier → reviewer → verifier → designer → reviewer → designer: the reviewer's node and
        # the designer's both follow the verifier's, so neither is downstream of the other.
        names = ["impl-openai", "verifier", "reviewer", "verifier", "designer", "reviewer", "designer"]
        flow, _ = structure.add_flow(names, None, self.root)
        nodes = plain_graph(names, flow["path"], self.hubs)
        graph_ = check.graph_from_raw(graph(nodes), Path("/nonexistent/.opencode/graph/flows.json"))
        self.assertEqual(check.validate(graph_) + check.flow_problems(graph_, [flow], self.hubs), [])

    def test_a_node_run_by_a_seat_counts_for_its_council(self):
        structure.add_flow(["impl-openai", "council", "impl-openai"], None, self.root)
        shape = snapshot_json.load_structure(self.root)
        nodes = [node("i1", "implement", "impl-openai"), gate("g1", "councillor-a", ["i1"], ["i1"])]
        graph_ = check.graph_from_raw(graph(nodes), Path("/nonexistent/.opencode/graph/flows.json"))
        self.assertEqual(check.flow_problems(graph_, shape["flows"], self.hubs, shape["seats"]), [])
        # A seat starts a council flow too, and `agentgraph check` reads the seats from the structure folder.
        structure.add_flow(["council", "reviewer"], None, self.root)
        graphs = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (graphs / "flows.json").write_text(json.dumps(graph(nodes + [node("s1", "decision", "councillor-a")])))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(graphs), "--structure", str(self.root)])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("s1 (councillor-a) is not followed by reviewer", out.getvalue())
        self.assertNotIn("g1", out.getvalue())


ADD = {"op": "add", "path": ["impl-openai", "verifier", "impl-openai"]}


class EveryPair(unittest.TestCase):
    """The user's roster: any two different agents can be put on a path, in either order, and the four flows
    the user named are added, listed beside the default loops and enforced. Runs on a copy of the real
    structure folder (its agent files only), so the real folder is never written."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "structure"
        shutil.copytree(ROOT / "structure", self.root, ignore=lambda folder, names: [n for n in names if n == "flows"])
        self.before = tree(self.root)
        self.graphs = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.shape = snapshot_json.load_structure(self.root)
        self.names = sorted(self.shape["group_of"])
        self.hubs = snapshot_json.hub_names(self.shape)

    def test_the_roster_has_the_agents_the_user_named(self):
        for name in ("impl-openai", "impl-claude", "impl-hard", "designer", "reviewer", "verifier", "oracle",
                     "oracle-lite", "researcher", "explorer", "council", "orchestrator"):
            self.assertIn(name, self.names)

    def test_every_pair_of_different_agents_in_either_order(self):
        for a in self.names:
            for b in self.names:
                if a == b:
                    continue
                self.assertEqual(snapshot_json.flow_refusal(self.shape, [a, b]), "", (a, b))
                if a not in self.hubs and b not in self.hubs:
                    self.assertEqual(snapshot_json.flow_refusal(self.shape, [a, b, a]), "", (a, b, a))
        added = [structure.add_flow([a, b], None, self.root)[0] for a in self.names for b in self.names if a != b]
        self.assertEqual(len(structure.drawn_flows(self.root)), len(self.names) * (len(self.names) - 1))
        self.assertTrue(all(flow["text"] for flow in added))
        for flow in added:
            structure.remove_flow(flow["id"], self.root)
        self.assertEqual(tree(self.root), self.before)

    def check(self, nodes):
        (self.graphs / "flows.json").write_text(json.dumps(graph(nodes)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["--dir", str(self.graphs), "--structure", str(self.root)])
        return code, out.getvalue()

    def test_the_four_flows_the_user_named(self):
        cases = [
            (["impl-hard", "reviewer", "impl-hard"], [node("h1", "implement", "impl-hard")],
             "h1 (impl-hard) is not followed by reviewer", [gate("g1", "reviewer", ["h1"], ["h1"])]),
            (["impl-hard", "verifier", "impl-hard"], [node("h1", "implement", "impl-hard")],
             "h1 (impl-hard) is not followed by verifier", [gate("g1", "verifier", ["h1"], ["h1"])]),
            (["oracle", "impl-hard"], [node("o1", "decision", "oracle")],
             "o1 (oracle) is not followed by impl-hard", [node("h1", "implement", "impl-hard", status="pending", depends_on=["o1"])]),
            (["researcher", "oracle", "impl-claude"], [node("r1", "research", "researcher")],
             "r1 (researcher) is not followed by oracle",
             [node("o1", "decision", "oracle", status="pending", depends_on=["r1"]),
              node("i1", "implement", "impl-claude", status="pending", depends_on=["o1"])]),
        ]
        for path, start, missing, rest in cases:
            with self.subTest(path=path):
                flow, notes = structure.add_flow(path, None, self.root)
                self.assertEqual((flow["path"], notes), (path, []))
                shape = snapshot_json.load_structure(self.root)
                self.assertEqual([item["path"] for item in shape["flows"]], [path])
                code, out = self.check(start)
                self.assertEqual(code, 1, out)
                self.assertIn(f"{missing}: flow {' → '.join(path)}", out)
                code, out = self.check(start + rest)
                self.assertEqual(code, 0, out)
                self.assertTrue(out.startswith("OK "), out)
                self.assertIn(f"  {' → '.join(path)}  [{flow['id']}]", out)
                structure.remove_flow(flow["id"], self.root)
        self.assertEqual(tree(self.root), self.before)

    def test_the_shipped_structure_folder_connects_nothing(self):
        # A fresh download: agents and no connections. No agent file has a `checks` line, and the snapshot
        # carries no flows and no default loops.
        for path in sorted(self.root.rglob("*.md")):
            if path.name != "README.md":
                self.assertFalse(any(line.strip().lower().startswith("checks:") for line in path.read_text().splitlines()), path)
        doc = snapshot_json.snapshot_document(demo_snapshot(1000), "demo", structure=self.shape)
        self.assertEqual(doc["structure"], {"source": "folder", "flows": [], "teams": []})
        self.assertEqual(self.shape["warnings"], [])

    def test_the_flows_folder_is_ignored_by_git(self):
        ignored = subprocess.run(["git", "check-ignore", "-q", "structure/flows/x.md"], cwd=ROOT, capture_output=True)
        self.assertEqual(ignored.returncode, 0, "structure/flows/ is not in .gitignore")


class Endpoint(unittest.TestCase):
    def setUp(self):
        temporary = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = make_structure(temporary / "structure")
        self.before = tree(self.root)
        self.enterContext(patch.object(server, "build_snapshot", side_effect=AssertionError("unexpected live read")))
        self.httpd = server.make_server(0, graph_dir=temporary / "graphs", cwd=temporary, session="root",
                                        web_dir=temporary, structure_dir=self.root)
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(self.httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.httpd.shutdown)
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def post(self, body, headers=None, method="POST", path="/api/structure/flow"):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            sent = {"Content-Type": "application/json", "Origin": self.origin, **(headers or {})}
            conn.request(method, path, body=data if method == "POST" else None,
                         headers={key: value for key, value in sent.items() if value is not None})
            response = conn.getresponse()
            raw = response.read()
            self.assertFalse([name for name in response.headers if name.lower().startswith("access-control-")])
            try:
                return response.status, json.loads(raw) if raw else None, response
            except ValueError:
                return response.status, raw, response
        finally:
            conn.close()

    def test_each_op(self):
        status, body, _ = self.post(ADD)
        flow = {"id": "impl-openai-verifier-impl-openai", "path": ADD["path"], "source": "drawn",
                "text": structure.flow_text(ADD["path"], HUB), "name": None, "command": None}
        self.assertEqual((status, body), (200, {"flow": flow, "flows": [flow]}))
        status, body, _ = self.post({"op": "text", "id": flow["id"], "text": "Edited ✓\n\tby hand."}, {"Origin": None})
        edited = {**flow, "text": "Edited ✓\n\tby hand."}
        self.assertEqual((status, body), (200, {"flow": edited, "flows": [edited]}))
        status, body, _ = self.post({"op": "add", "path": ["councillor-a", "impl-openai"], "text": "Given."})
        self.assertEqual((status, body["flow"]["path"], body["flow"]["text"]), (200, ["council", "impl-openai"], "Given."))
        self.assertEqual(body["notes"], ["councillor-a is a seat of the council: stored as council"])
        self.assertEqual(self.post({"op": "remove", "id": "council-impl-openai"})[:2], (200, {"flows": [edited]}))
        self.assertEqual(self.post({"op": "remove", "id": flow["id"]})[:2], (200, {"flows": []}))
        self.assertEqual(tree(self.root), self.before)

    def test_a_loop_on_itself_and_whitespace_text(self):
        status, body, _ = self.post({"op": "add", "path": ["impl-claude", "impl-claude"]})
        self.assertEqual((status, body["flow"]["text"]), (200, SELF.replace("A", "impl-claude", 1)))
        status, body, _ = self.post({"op": "add", "path": ["impl-claude", "impl-claude"]})
        self.assertEqual(status, 400)
        self.assertIn("already has a loop on itself", body["error"])
        self.post({"op": "text", "id": "impl-claude-impl-claude", "text": "Mine."})
        status, body, _ = self.post({"op": "text", "id": "impl-claude-impl-claude", "text": " \n\t "})
        self.assertEqual((status, body["flow"]["text"]), (200, SELF.replace("A", "impl-claude", 1)))
        self.post({"op": "remove", "id": "impl-claude-impl-claude"})
        self.assertEqual(tree(self.root), self.before)

    def test_unknown_op_and_bad_shapes(self):
        for body in ({"op": "rename", "id": "x"}, {"op": ["add"], "path": ["impl-openai", "reviewer"]}, {"path": ["impl-openai", "reviewer"]},
                     {"op": "add", "path": ["impl-openai", "reviewer"], "extra": 1}, {"op": "text", "id": "x"},
                     {"op": "remove"}, ["add"], {"op": "add", "path": "impl-openai reviewer"}, {"op": "text", "id": 3, "text": "x"}):
            with self.subTest(body=body):
                status, reply, _ = self.post(body)
                self.assertEqual(status, 400)
                self.assertIn("error", reply)
        self.assertEqual(tree(self.root), self.before)

    def test_unknown_and_path_like_ids(self):
        self.post(ADD)
        before = tree(self.root)
        for flow_id in ("ghost", "../00-hub/orchestrator", "a/b", "..", "impl-openai-verifier-impl-openai/..", "/etc/passwd"):
            for body in ({"op": "text", "id": flow_id, "text": "New."}, {"op": "remove", "id": flow_id}):
                with self.subTest(body=body):
                    status, reply, _ = self.post(body)
                    self.assertEqual(status, 400)
                    self.assertIn("there is no flow", reply["error"])
        self.assertEqual(tree(self.root), before)

    def test_refused_paths_and_texts(self):
        for body, message in (({"op": "add", "path": ["ghost", "reviewer"]}, "unknown agent"),
                              ({"op": "add", "path": ["impl-openai", "verifier", "verifier"]}, "follows itself"),
                              ({"op": "add", "path": ["impl-openai", "orchestrator", "reviewer"]}, "hub"),
                              ({"op": "add", "path": ["impl-openai"]}, "at least two"),
                              ({"op": "add", "path": ["impl-openai", "verifier", "reviewer", "verifier", "reviewer"]},
                               "reviewer's work came after verifier's"),
                              ({"op": "add", "path": ["impl-openai", "reviewer"], "text": "x" * 2001}, "at most 2000"),
                              ({"op": "add", "path": ["impl-openai", "reviewer"], "text": "\x1b[2J"}, "control character")):
            with self.subTest(body=str(body)[:60]):
                status, reply, _ = self.post(body)
                self.assertEqual(status, 400)
                self.assertIn(message, reply["error"])
        self.assertEqual(tree(self.root), self.before)

    def test_a_text_of_2000_characters_fits_the_body_bound_and_more_does_not(self):
        self.post(ADD)
        status, body, _ = self.post({"op": "text", "id": "impl-openai-verifier-impl-openai", "text": "\U0001F600" * 2000})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.post(b'{"op": "remove", "id": "' + b"x" * 40000 + b'"}')[0], 413)

    def test_the_old_path_is_gone(self):
        for path in ("/api/structure/loop", "/api/structure/link"):
            with self.subTest(path=path):
                status, _, response = self.post({"work": "impl-openai", "check": "reviewer", "on": True}, path=path)
                self.assertEqual(status, 405)
                self.assertEqual(response.getheader("Allow"), "GET, HEAD")
        self.assertEqual(tree(self.root), self.before)

    def test_wrong_method(self):
        for method in ("GET", "PUT", "DELETE", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                status, _, response = self.post({}, {"Access-Control-Request-Method": "POST"}, method=method)
                self.assertEqual(status, 405)
                self.assertEqual(response.getheader("Allow"), "POST")
        status, _, _ = self.post(ADD, path="/api/snapshot")
        self.assertEqual(status, 405)
        self.assertEqual(tree(self.root), self.before)

    def raw_post(self, length_header, body=json.dumps(ADD).encode()):
        port = self.httpd.server_port
        head = (f"POST /api/structure/flow HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: {self.origin}\r\n"
                f"Content-Type: application/json\r\n{length_header}\r\nConnection: close\r\n\r\n").encode()
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(head + body)
            reply = b""
            while chunk := client.recv(65536):
                reply += chunk
        status = int(reply.split(b" ", 2)[1])
        headers, _, payload = reply.partition(b"\r\n\r\n")
        self.assertNotIn(b"access-control-", headers.lower())
        return status, json.loads(payload)

    def test_chunked_negative_and_non_numeric_lengths_are_refused(self):
        body = json.dumps(ADD).encode()
        chunked = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        for header, data, expected in (("Transfer-Encoding: chunked", chunked, 411), ("Content-Length: -5", body, 413),
                                       ("Content-Length: ten", body, 411), ("Content-Length: 1e3", body, 411)):
            with self.subTest(header=header):
                status, reply = self.raw_post(header, data)
                self.assertEqual(status, expected)
                self.assertIn("error", reply)
        self.assertEqual(tree(self.root), self.before)

    def test_the_snapshot_shows_the_folder_the_server_writes(self):
        self.post(ADD)
        with patch.object(server, "build_snapshot", return_value=demo_snapshot(1000)):
            conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
            conn.request("GET", "/api/snapshot")
            document = json.loads(conn.getresponse().read())
            conn.close()
        self.assertEqual(document["structure"]["source"], "folder")
        self.assertEqual([flow["path"] for flow in document["structure"]["flows"]], [ADD["path"]])
        self.post({"op": "remove", "id": "impl-openai-verifier-impl-openai"})
        self.assertEqual(tree(self.root), self.before)

    def test_non_loopback_client(self):
        with patch.object(server, "_LOOPBACK", frozenset()):
            status, body, _ = self.post(ADD)
        self.assertEqual(status, 403)
        self.assertIn("local", body["error"])
        self.assertEqual(tree(self.root), self.before)

    def test_foreign_origin(self):
        port = self.httpd.server_port
        for origin in ("http://evil.example", f"http://localhost:{port}", f"https://127.0.0.1:{port}", "null", f"http://127.0.0.1:{port + 1}"):
            with self.subTest(origin=origin):
                status, body, _ = self.post(ADD, {"Origin": origin})
                self.assertEqual(status, 403)
                self.assertIn("error", body)
        status, _, _ = self.post(ADD, {"Host": "evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(tree(self.root), self.before)

    def test_non_json(self):
        good = json.dumps(ADD).encode()
        for headers, body in (({"Content-Type": "text/plain"}, good), ({"Content-Type": "application/x-www-form-urlencoded"}, b"op=add"),
                              ({"Content-Type": None}, good), ({}, b"not json"), ({}, b'["add"]'), ({}, b"\xff\xfe"),
                              ({}, b"x" * 40000)):
            with self.subTest(headers=headers, body=body[:40]):
                status, reply, _ = self.post(body, headers)
                self.assertIn(status, (400, 413, 415))
                self.assertIn("error", reply)
        self.assertEqual(tree(self.root), self.before)

    def test_demo_server_refuses(self):
        demo = server.make_server(0, demo=True, structure_dir=self.root)
        thread = threading.Thread(target=demo.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", demo.server_port, timeout=2)
            conn.request("POST", "/api/structure/flow", body=json.dumps(ADD).encode(), headers={"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 403)
            conn.close()
        finally:
            demo.shutdown()
            demo.server_close()
            thread.join(2)
        self.assertEqual(tree(self.root), self.before)


if __name__ == "__main__":
    unittest.main()
