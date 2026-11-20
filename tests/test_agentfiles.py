"""Added agents as OpenCode agent files: each operation, each refusal and the byte-exact restores. Scratch copies
only; the real agents folder, preset file, SKILLS.md and structure folder are never written."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentgraph import agentfiles, roster, skillset, teams
from agentgraph import structure as structure_module
from agentgraph.snapshot_json import skills_document

SCRATCH = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode/agentfiles")
ROOT = Path(__file__).resolve().parent.parent
REAL_AGENTS = agentfiles.AGENTS_DIR
FIRST = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (skillset.CONFIG, skillset.MAP)}
FIRST_AGENTS = sorted(os.listdir(REAL_AGENTS)) if REAL_AGENTS.is_dir() else None


def tearDownModule():
    for path, digest in FIRST.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"{path} changed during the tests"
    now = sorted(os.listdir(REAL_AGENTS)) if REAL_AGENTS.is_dir() else None
    assert now == FIRST_AGENTS, "the real agents folder changed during the tests"


def _fd_path(fd):
    """The path of an open folder (macOS F_GETPATH; /proc elsewhere)."""
    import fcntl
    if hasattr(fcntl, "F_GETPATH"):
        return fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0").decode()
    return os.readlink(f"/proc/self/fd/{fd}")


def tree(folder):
    folder = Path(folder)
    if not folder.exists():
        return {}
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / "cfg.json"
        self.map = self.root / "SKILLS.md"
        self.backups = self.root / "backups"
        self.agents = self.root / "agents"
        self.agents.mkdir()
        self.skills = self.root / "skills"
        for name in ("code-assurance", "engineering-delivery"):
            (self.skills / name).mkdir(parents=True)
            (self.skills / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---\nBody\n")
        self.structure = self.root / "structure"
        shutil.copytree(ROOT / "structure", self.structure)
        shutil.copyfile(skillset.CONFIG, self.config)
        shutil.copyfile(skillset.MAP, self.map)
        self.paths = dict(config=self.config, map_path=self.map, backups=self.backups, structure=self.structure,
                          agents_dir=self.agents)
        self.start = self.snapshot()

    def snapshot(self):
        return (self.config.read_bytes(), self.map.read_bytes(), tree(self.agents), tree(self.structure))

    def add(self, name="checker", **kwargs):
        return roster.add_agent(name, skills_dir=self.skills, **(self.paths | kwargs))

    def refused(self, pattern, function, *args, **kwargs):
        before = self.snapshot()
        with self.assertRaisesRegex(skillset.SkillsetError, pattern):
            function(*args, **kwargs)
        self.assertEqual(self.snapshot(), before, "a refused change wrote something")
        self.assertEqual([p for p in self.agents.iterdir() if p.name.endswith(".tmp")], [])

    def file(self, name="checker"):
        return (self.agents / f"{name}.md").read_bytes()


class Format(Base):
    def test_the_file_is_the_frontmatter_opencode_reads_with_the_marker_first(self):
        data = agentfiles.render("Checks.", "anthropic/claude-haiku-5-5#high", agentfiles.WRITER, "Do it.\n")
        text = data.decode()
        self.assertTrue(text.startswith("---\n# agentgraph: added"))
        self.assertIn('\nmode: subagent\nmodel: "anthropic/claude-haiku-5-5#high"\npermissions:\n', text)
        self.assertTrue(text.endswith("---\nDo it.\n"))
        parsed = agentfiles.parse(data)
        self.assertEqual(parsed["description"], "Checks.")
        self.assertEqual(parsed["permissions"][0], agentfiles.FIRST)
        self.assertEqual(parsed["permissions"][-1], agentfiles.LAST)
        self.assertEqual(agentfiles.render(parsed["description"], parsed["model"], parsed["permissions"],
                                           parsed["text"]), data)

    def test_narrow_drops_wildcard_allows_and_subagent_and_ends_with_subagent_denied(self):
        rules = agentfiles.narrow([{"action": "*", "resource": "*", "effect": "allow"},
                                   {"action": "subagent", "resource": "*", "effect": "allow"},
                                   {"action": "edit", "resource": "*", "effect": "allow"}])
        self.assertEqual(rules, [agentfiles.FIRST, {"action": "edit", "resource": "*", "effect": "allow"},
                                 agentfiles.LAST])

    def test_copying_a_plugin_permission_never_widens_it(self):
        rules, notes = agentfiles.from_plugin({"*": "allow", "task": "allow", "edit": "deny",
                                               "bash": {"*": "deny", "git diff*": "allow"}}, ["code-assurance"])
        self.assertNotIn({"action": "*", "resource": "*", "effect": "allow"}, rules)
        self.assertEqual(rules[-1], agentfiles.LAST)
        self.assertNotIn("allow", [r["effect"] for r in rules if r["action"] == "subagent"])
        self.assertIn({"action": "shell", "resource": "git diff*", "effect": "allow"}, rules)
        self.assertLess(rules.index({"action": "shell", "resource": "*", "effect": "deny"}),
                        rules.index({"action": "shell", "resource": "git diff*", "effect": "allow"}))
        self.assertTrue(agentfiles.read_only(rules))
        self.assertEqual(len(notes), 2)

    def test_a_hand_written_file_is_not_added_and_not_parsed(self):
        data = b"---\ndescription: mine\nmode: subagent\n---\nBody\n"
        self.assertFalse(agentfiles.is_added(data))
        self.assertIsNone(agentfiles.parse(data))


class Add(Base):
    def test_verifier_template_takes_the_default_verifiers_model_and_permissions(self):
        result = self.add(template="verifier")
        self.assertEqual(result["kind"], "added")
        self.assertEqual(result["message"], agentfiles.LIVE)
        self.assertEqual(result["permission"], "read-only")
        verifier = json.loads(self.config.read_bytes())
        spec = verifier["presets"][verifier["preset"]]["verifier"]
        self.assertEqual(result["model"], f"{spec['model']}#{spec['variant']}" if spec.get("variant") else spec["model"])
        parsed = agentfiles.parse(self.file())
        self.assertEqual(parsed["text"].strip(), agentfiles.VERIFIER_TEXT)
        self.assertIn({"action": "shell", "resource": "*", "effect": "allow"}, parsed["permissions"])
        self.assertIn({"action": "edit", "resource": "*", "effect": "deny"}, parsed["permissions"])
        self.assertEqual(parsed["permissions"][-1], agentfiles.LAST)
        # the preset file and SKILLS.md are only read
        self.assertEqual(self.snapshot()[:2], self.start[:2])
        self.assertTrue((self.structure / "30-build" / "checker.md").is_file())

    def test_a_copy_of_impl_claude_takes_its_fields_and_may_not_start_agents(self):
        result = self.add("claude-copy", copy_of="impl-claude")
        obj = json.loads(self.config.read_bytes())
        spec = obj["presets"][obj["preset"]]["impl-claude"]
        parsed = agentfiles.parse(self.file("claude-copy"))
        self.assertEqual(parsed["description"], spec["description"])
        self.assertEqual(parsed["model"].partition("#")[0], spec["model"])
        self.assertEqual(agentfiles.skills_of(parsed["permissions"]), spec["skills"])
        self.assertEqual(result["permission"], "writer")
        self.assertEqual(parsed["permissions"][-1], agentfiles.LAST)
        self.assertNotIn({"action": "*", "resource": "*", "effect": "allow"}, parsed["permissions"])

    def test_a_copy_of_an_added_agent_and_overrides(self):
        self.add(template="verifier")
        self.add("second", copy_of="checker", description="Another checker.", model="openai/gpt-6.2-sol-fast",
                 variant="high", skills=["code-assurance"])
        parsed = agentfiles.parse(self.file("second"))
        self.assertEqual(parsed["description"], "Another checker.")
        self.assertEqual(parsed["model"], "openai/gpt-6.2-sol-fast#high")
        self.assertEqual(agentfiles.skills_of(parsed["permissions"]), ["code-assurance"])
        self.assertTrue(agentfiles.read_only(parsed["permissions"]))

    def test_blank_needs_a_model_when_the_presets_have_no_default_verifier(self):
        # A name alone takes the default verifier's model (NameAlone); with no verifier to take it from, the model
        # must be given.
        obj = json.loads(self.config.read_bytes())
        for preset in obj["presets"].values():
            preset.pop("verifier", None)
        self.config.write_text(json.dumps(obj, indent=2))
        self.refused("model is required", self.add, "plain")
        self.refused("model is required", self.add, "plain", template="blank", description="Plain.", prompt="Work.")
        result = self.add("plain", model="anthropic/claude-sonnet-5-5", description="Plain.", prompt="Work.",
                          read_only=True)
        self.assertEqual(result["permission"], "read-only")

    def test_bad_and_taken_names_are_refused_before_anything_is_written(self):
        (self.agents / "mine.md").write_text("---\ndescription: mine\n---\nBody\n")
        (self.agents / "architect.md.disabled").write_text("x")
        for name, pattern in (("Bad", "invalid agent name"), ("-x", "invalid agent name"),
                              ("orchestrator", "built-in"), ("councillor-x", "council"), ("build", "reserved"),
                              ("agents", "plugin reads"), ("impl-claude", "already exists"),
                              ("mine", "already an agent file"), ("architect", "architect.md.disabled")):
            with self.subTest(name=name):
                self.refused(pattern, self.add, name, template="verifier")
        self.refused("not installed", self.add, "free", template="verifier", skills=["nope"])
        self.refused("unknown template", self.add, "free", template="nope")
        self.refused("either copy_of or template", self.add, "free", template="verifier", copy_of="impl-claude")
        self.refused("cannot be copied", self.add, "free", copy_of="orchestrator")
        self.refused("written by hand", self.add, "free", copy_of="mine")
        self.refused("no agent", self.add, "free", copy_of="ghost")

    def test_create_never_hard_links_the_file_so_opencode_sees_its_deletion(self):
        # OpenCode 2.3.2 on macOS did not notice the deletion of an agent file made with link(): the agent stayed
        # listed until another file changed (n84 proof). New files are made by rename.
        real, agents = os.link, os.path.realpath(self.agents)

        def link(source, target, *args, dst_dir_fd=None, **kwargs):
            folder = os.path.realpath(_fd_path(dst_dir_fd)) if dst_dir_fd is not None else os.path.dirname(
                os.path.realpath(target))
            if folder == agents:
                raise AssertionError("an agent file was made with os.link")
            return real(source, target, *args, dst_dir_fd=dst_dir_fd, **kwargs)

        with patch.object(agentfiles.os, "link", side_effect=link):
            self.add(template="verifier")
        self.assertEqual(os.stat(self.agents / "checker.md").st_nlink, 1)

    def test_a_new_file_never_replaces_one_that_appeared(self):
        real = agentfiles._read_at
        calls = []

        def appear(fd, name):
            calls.append(name)
            if name == "checker.md" and len(calls) == 2:
                (self.agents / "checker.md").write_text("theirs")
            return real(fd, name)

        with patch.object(agentfiles, "_read_at", side_effect=appear):
            with self.assertRaises(skillset.SkillsetError):
                agentfiles.create("checker", description="d", model="a/b", permissions=agentfiles.WRITER, text="t",
                                  agents_dir=self.agents)
        self.assertEqual((self.agents / "checker.md").read_text(), "theirs")

    def test_a_symlinked_agents_folder_is_refused(self):
        link = self.root / "linked"
        link.symlink_to(self.agents)
        with self.assertRaisesRegex(skillset.SkillsetError, "not a plain folder"):
            agentfiles.create("checker", description="d", model="a/b", permissions=agentfiles.WRITER, text="t",
                              agents_dir=link)


class Text(Base):
    def test_read_and_replace_the_instruction_and_undo_it_exactly(self):
        self.add(template="verifier")
        before = self.file()
        shown = roster.read_agent("checker", agents_dir=self.agents)
        self.assertEqual(shown["text"], agentfiles.VERIFIER_TEXT)
        self.assertEqual(set(shown) >= {"name", "description", "model", "text"}, True)
        done = roster.text_agent("checker", "Check only the tests.", agents_dir=self.agents, backups=self.backups)
        self.assertEqual(done["text"], "Check only the tests.")
        self.assertTrue(done["changed"])
        self.assertEqual(done["message"], agentfiles.LIVE)
        roster.restore_agent(done["record"], **self.paths)
        self.assertEqual(self.file(), before)
        again = roster.text_agent("checker", agentfiles.VERIFIER_TEXT, agents_dir=self.agents, backups=self.backups)
        self.assertFalse(again["changed"])

    def test_text_undo_is_refused_once_the_file_changed_again(self):
        self.add(template="verifier")
        done = roster.text_agent("checker", "One.", agents_dir=self.agents, backups=self.backups)
        roster.text_agent("checker", "Two.", agents_dir=self.agents, backups=self.backups)
        self.refused("changed after that edit", roster.restore_agent, done["record"], **self.paths)

    def test_a_file_changed_underneath_is_not_overwritten(self):
        self.add(template="verifier")
        real = agentfiles._read_at
        seen = []

        def other(fd, name):
            seen.append(name)
            if len(seen) == 2:
                (self.agents / "checker.md").write_bytes(self.file() + b"edit\n")
            return real(fd, name)

        with patch.object(agentfiles, "_read_at", side_effect=other):
            with self.assertRaisesRegex(skillset.SkillsetError, "changed while"):
                roster.text_agent("checker", "New.", agents_dir=self.agents, backups=self.backups)
        self.assertTrue(self.file().endswith(b"edit\n"))

    def test_hand_written_and_unknown_agents_are_refused(self):
        (self.agents / "mine.md").write_text("---\ndescription: mine\n---\nBody\n")
        self.refused("written by hand", roster.text_agent, "mine", "x", agents_dir=self.agents, backups=self.backups)
        self.refused("no agent file", roster.read_agent, "ghost", agents_dir=self.agents)
        self.refused("instruction is required", roster.text_agent, "mine", "  ", agents_dir=self.agents,
                     backups=self.backups)

    def test_forged_records_are_refused(self):
        self.add(template="verifier")
        done = roster.text_agent("checker", "One.", agents_dir=self.agents, backups=self.backups)
        for record in ("/etc/passwd", str(self.root / "x.json"), done["record"].replace("text-", "texts-")):
            self.refused("invalid undo record", roster.restore_agent, record, **self.paths)
        path = Path(done["record"])
        data = json.loads(path.read_text())
        path.write_text(json.dumps({**data, "agent": "other"}))
        self.refused("invalid undo record", roster.restore_agent, done["record"], **self.paths)


class Skills(Base):
    def test_skills_of_an_added_agent_live_in_its_file(self):
        self.add(template="verifier")
        options = dict(config=self.config, map_path=self.map, backups=self.backups, agents_dir=self.agents,
                       skills_dir=self.skills)
        added = skillset.add("checker", "code-assurance", **options)
        self.assertEqual(added["skills"], ["code-assurance"])
        self.assertEqual(added["message"], agentfiles.LIVE)
        self.assertEqual(skillset.show("checker", config=self.config, agents_dir=self.agents)["skills"],
                         ["code-assurance"])
        everything = skillset.set_all("checker", **options)
        self.assertEqual(everything["skills"], ["*"])
        less = skillset.remove("checker", "code-assurance", **options)
        self.assertEqual(less["skills"], ["engineering-delivery"])
        self.assertEqual(self.snapshot()[:2], self.start[:2], "the preset file or SKILLS.md was written")
        with self.assertRaisesRegex(skillset.SkillsetError, "not installed"):
            skillset.add("checker", "nope", **options)

    def test_a_hand_written_agents_skills_are_read_only(self):
        (self.agents / "mine.md").write_text("---\ndescription: mine\nmodel: a/b\n---\nBody\n")
        shown = skillset.show("mine", config=self.config, agents_dir=self.agents)
        self.assertFalse(shown["editable"])
        self.assertIn("written by hand", shown["reason"])
        with self.assertRaisesRegex(skillset.SkillsetError, "written by hand"):
            skillset.add("mine", "code-assurance", config=self.config, map_path=self.map, backups=self.backups,
                         agents_dir=self.agents, skills_dir=self.skills)


class RemoveRestore(Base):
    def test_remove_takes_the_file_flows_group_and_memberships_and_restore_puts_them_back(self):
        self.add(template="verifier")
        self.add("claude-copy", copy_of="impl-claude")
        structure_module.add_flow(["checker", "reviewer"], None, self.structure)
        teams.add("checker", ["claude-copy"], structure_dir=self.structure)
        teams.add("reviewer", ["checker", "impl-openai"], structure_dir=self.structure)
        before = self.snapshot()
        result = roster.remove_agent("checker", **self.paths)
        self.assertEqual(result["message"], agentfiles.LIVE)
        self.assertEqual(result["removed"]["flows"], ["checker-reviewer"])
        self.assertEqual(result["removed"]["teams"], ["checker"])
        self.assertEqual(result["removed"]["memberships"], ["reviewer"])
        self.assertFalse((self.agents / "checker.md").exists())
        self.assertIsInstance(result["record"], str)
        roster.restore_agent(result["record"], **self.paths)
        self.assertEqual(self.snapshot(), before)
        # a second restore finds the agent back and changes nothing
        self.refused("already an agent file", roster.restore_agent, result["record"], **self.paths)

    def test_restore_is_refused_when_the_name_is_taken_again(self):
        self.add(template="verifier")
        record = roster.remove_agent("checker", **self.paths)["record"]
        self.add(template="verifier")
        self.refused("already an agent file", roster.restore_agent, record, **self.paths)

    def test_restore_is_refused_when_the_name_has_since_become_a_preset_agent_or_a_display_name(self):
        self.add(template="verifier")
        record = roster.remove_agent("checker", **self.paths)["record"]
        obj = json.loads(self.config.read_bytes())
        original = self.config.read_bytes()
        obj["presets"][obj["preset"]]["checker"] = {"model": "a/b"}
        self.config.write_text(json.dumps(obj, indent=2))
        self.refused("already exists|taken", roster.restore_agent, record, **self.paths)
        self.config.write_bytes(original)
        obj = json.loads(original)
        obj["presets"][obj["preset"]]["orchestrator"]["displayName"] = "@Checker"
        self.config.write_text(json.dumps(obj, indent=2))
        self.refused("display name|displayName|taken", roster.restore_agent, record, **self.paths)
        self.config.write_bytes(original)
        roster.restore_agent(record, **self.paths)
        self.assertTrue((self.agents / "checker.md").is_file())

    def test_a_failure_in_the_middle_leaves_nothing_half_done(self):
        self.add(template="verifier")
        structure_module.add_flow(["checker", "reviewer"], None, self.structure)
        before = self.snapshot()
        with patch.object(agentfiles, "replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                roster.remove_agent("checker", **self.paths)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.backups.rglob("record.json")), [])

    def test_a_hand_written_agent_cannot_be_removed(self):
        (self.agents / "mine.md").write_text("---\ndescription: mine\nmodel: a/b\n---\nBody\n")
        self.refused("written by hand", roster.remove_agent, "mine", **self.paths)

    def test_full_round_trip_ends_where_it_started(self):
        self.add(template="verifier")
        self.add("claude-copy", copy_of="impl-claude")
        structure_module.add_flow(["checker", "reviewer"], None, self.structure)
        teams.add("checker", ["claude-copy"], structure_dir=self.structure)
        record = roster.remove_agent("checker", **self.paths)["record"]
        roster.restore_agent(record, **self.paths)
        structure_module.remove_flow("checker-reviewer", self.structure)
        teams.remove("checker", self.structure)
        roster.remove_agent("checker", **self.paths)
        roster.remove_agent("claude-copy", **self.paths)
        self.assertEqual(self.snapshot(), self.start)


class Snapshot(Base):
    def test_annotate_marks_added_and_hand_written_agents(self):
        self.add(template="verifier")
        (self.agents / "mine.md").write_text("---\ndescription: mine\nmodel: a/b\n---\nBody\n")
        document = {"agents": [{"name": "checker"}, {"name": "mine"}, {"name": "impl-claude"},
                               {"name": "orchestrator"}]}
        roster.annotate(document, config=self.config, map_path=self.map, agents_dir=self.agents)
        rows = {row["name"]: row for row in document["agents"]}
        self.assertTrue(rows["checker"]["added"] and rows["checker"]["removable"])
        self.assertFalse(rows["mine"]["added"] or rows["mine"]["removable"])
        self.assertIn("written by hand", rows["mine"]["removable_reason"])
        self.assertFalse(rows["impl-claude"]["added"])
        self.assertTrue(rows["impl-claude"]["removable"])
        self.assertFalse(rows["orchestrator"]["removable"])


HAND_HOLDS = ("---\ndescription: mine\nmodel: a/b\npermissions:\n  - action: skill\n    resource: zz-new\n"
              "    effect: allow\n---\nBody\n")


class SkillDelete(Base):
    """A skill is not deleted while an agent file (added or hand-written) still has it; the agents are named."""

    def setUp(self):
        super().setUp()
        from agentgraph import skillfiles
        self.skillfiles = skillfiles
        skillfiles.create("zz-new", "A new skill.", "Body.\n", skills=self.skills)
        self.where = dict(skills=self.skills, backups=self.backups, config=self.config, agents_dir=self.agents,
                          wildcards_hold=False)

    def test_delete_is_refused_while_an_added_agent_file_holds_the_skill(self):
        self.add("beta", template="verifier")
        skillset.add("beta", "zz-new", config=self.config, map_path=self.map, backups=self.backups,
                     agents_dir=self.agents, skills_dir=self.skills)
        with self.assertRaisesRegex(skillset.SkillsetError, r"still used by agents: beta\b"):
            self.skillfiles.delete("zz-new", **self.where)
        self.assertTrue((self.skills / "zz-new" / "SKILL.md").is_file())
        self.assertIn(b'resource: "zz-new"', self.file("beta"))

    def test_delete_is_refused_while_a_hand_written_agent_file_names_the_skill(self):
        (self.agents / "mine.md").write_text(HAND_HOLDS)
        (self.agents / "other.md").write_text("---\ndescription: other\nmodel: a/b\n---\nzz-new in the body only\n")
        with self.assertRaisesRegex(skillset.SkillsetError, r"still used by agents: mine;"):
            self.skillfiles.delete("zz-new", **self.where)
        self.assertTrue((self.skills / "zz-new" / "SKILL.md").is_file())

    def test_an_added_agent_with_every_skill_is_only_warned_about(self):
        self.add("beta", template="verifier")
        skillset.set_all("beta", config=self.config, map_path=self.map, backups=self.backups, agents_dir=self.agents,
                         skills_dir=self.skills)
        done = self.skillfiles.delete("zz-new", **self.where)
        self.assertRegex(" ".join(done["warnings"]), r"\bbeta\b[^.]* every skill and will no longer have zz-new")

    def test_the_skill_command_passes_the_agents_folder(self):
        (self.agents / "mine.md").write_text(HAND_HOLDS)
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.skillfiles.main(["skill", "delete", "zz-new", "--skills", str(self.skills), "--backups",
                                         str(self.backups), "--config", str(self.config), "--agents",
                                         str(self.agents), "--json"])
        self.assertEqual(code, 1)
        self.assertIn("mine", json.loads(out.getvalue())["error"])
        self.assertTrue((self.skills / "zz-new" / "SKILL.md").is_file())


class AgentList(Base):
    def test_agent_list_shows_preset_added_and_hand_written_agents_saying_which_is_which(self):
        self.add(template="verifier")
        (self.agents / "mine.md").write_text("---\ndescription: mine\nmodel: a/b\n---\nBody\n")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(roster.main(["agent", "list", "--config", str(self.config), "--agents", str(self.agents),
                                          "--json"]), 0)
        files = {row["name"]: row for row in json.loads(out.getvalue())["agent_files"]}
        self.assertEqual((files["checker"]["kind"], files["checker"]["editable"]), ("added", True))
        self.assertEqual((files["mine"]["kind"], files["mine"]["editable"]), ("hand-written", False))
        self.assertIn("written by hand", files["mine"]["reason"])
        out = io.StringIO()
        with redirect_stdout(out):
            roster.main(["agent", "list", "--config", str(self.config), "--agents", str(self.agents)])
        text = out.getvalue()
        self.assertIn("agent files:", text)
        self.assertRegex(text, r"\n  checker  added\b")
        self.assertRegex(text, r"\n  mine  hand-written\b")
        self.assertRegex(text, r"\n  impl-claude  custom\b")


class HandChanged(Base):
    """A file that still carries the marker is the toolkit's: text, skills and remove all work on it and keep the
    lines the toolkit did not write; when the writer cannot keep them exactly, all three refuse alike."""

    def hand_edit(self, old, new):
        data = self.file().replace(old, new, 1)
        self.assertNotEqual(data, self.file())
        (self.agents / "checker.md").write_bytes(data)
        return data

    def test_a_hand_added_line_is_kept_by_text_skills_and_remove(self):
        self.add(template="verifier")
        edited = self.hand_edit(b"\nmode: subagent\n", b"\nmode: subagent\ntemperature: 0.2\n")
        options = dict(config=self.config, map_path=self.map, backups=self.backups, agents_dir=self.agents,
                       skills_dir=self.skills)
        done = roster.text_agent("checker", "Check only the tests.", agents_dir=self.agents, backups=self.backups)
        self.assertIn(b"\ntemperature: 0.2\n", self.file())
        self.assertTrue(self.file().endswith(b"---\nCheck only the tests.\n"))
        self.assertEqual(self.file().split(b"\n---\n")[0], edited.split(b"\n---\n")[0])
        roster.restore_agent(done["record"], **self.paths)
        self.assertEqual(self.file(), edited)
        skillset.add("checker", "code-assurance", **options)
        self.assertIn(b"\ntemperature: 0.2\n", self.file())
        self.assertIn(b'resource: "code-assurance"', self.file())
        skillset.remove("checker", "code-assurance", **options)
        self.assertEqual(self.file(), edited)
        record = roster.remove_agent("checker", **self.paths)["record"]
        self.assertFalse((self.agents / "checker.md").exists())
        roster.restore_agent(record, **self.paths)
        self.assertEqual(self.file(), edited)

    def test_a_change_the_writer_cannot_keep_is_refused_alike_by_text_skills_and_remove(self):
        self.add(template="verifier")
        self.hand_edit(b'    effect: deny\n---\n', b'    effect: deny\n    note: mine\n---\n')
        reasons = []
        for call in (lambda: roster.text_agent("checker", "New.", agents_dir=self.agents, backups=self.backups),
                     lambda: skillset.add("checker", "code-assurance", config=self.config, map_path=self.map,
                                          backups=self.backups, agents_dir=self.agents, skills_dir=self.skills),
                     lambda: roster.remove_agent("checker", **self.paths)):
            before = self.snapshot()
            with self.assertRaises(skillset.SkillsetError) as caught:
                call()
            self.assertEqual(self.snapshot(), before)
            reasons.append(str(caught.exception))
        self.assertEqual(len(set(reasons)), 1, reasons)
        self.assertIn("changed by hand", reasons[0])
        listed = agentfiles.listing(self.agents)["checker"]
        self.assertEqual(listed["why"], reasons[0])
        self.assertFalse(listed["editable"])


class FailedRemove(Base):
    def test_a_failed_remove_leaves_no_empty_record_folder(self):
        self.add(template="verifier")
        before = self.snapshot()
        os.chmod(self.agents, 0o555)
        self.addCleanup(os.chmod, self.agents, 0o755)
        with self.assertRaises(OSError):
            roster.remove_agent("checker", **self.paths)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([p.name for p in self.backups.rglob("remove-*")], [])
        self.assertEqual([p for p in self.backups.rglob("*-agent-files") if not any(p.iterdir())], [])


class PermissionSummary(Base):
    def test_read_returns_what_the_agent_may_do(self):
        self.add(template="verifier")
        self.add("claude-copy", copy_of="impl-claude")
        self.add("looker", model="a/b", description="Looks.", prompt="Look.", read_only=True)
        for name, writes in (("checker", False), ("claude-copy", True), ("looker", False)):
            shown = roster.read_agent(name, agents_dir=self.agents)["permissions"]
            self.assertEqual(shown["default"], "deny")
            self.assertEqual(shown["start_agents"], "deny")
            self.assertEqual(shown["read_only"], not writes)
            self.assertNotIn({"action": "*", "resource": "*", "effect": "allow"}, shown["rules"])
            self.assertIn({"action": "read", "resource": "*", "effect": "allow"}, shown["rules"])
            self.assertEqual({"action": "edit", "resource": "*", "effect": "allow"} in shown["rules"], writes)
            self.assertEqual(shown, agentfiles.summary(agentfiles.parse(self.file(name))["permissions"]))


# --- the n13 r4 security review (n99) ----------------------------------------------------------------------

# How oh-my-opencode-slim 3.0.6 gives a preset entry's `permission` to OpenCode 2.3.2, as a scratch host listed a
# plugin agent's rules (`GET /api/agent/{id}`, the n99 proof): the plugin's first rules, the entry's keys and
# patterns in order (`bash` is `shell` and `bash`, `task` is `subagent`, '*' is every action), the plugin's denies
# of its own tools and of every skill (each only when the entry does not name that key), the agent's skills, the
# plugin's denies of MCP tools the agent has not got, then the host's tail.
PLUGIN_FIRST = [("*", "*", "allow"), ("external_directory", "*", "ask"), ("read", "*.env", "ask"),
                ("read", "*.env.*", "ask"), ("read", "*.env.example", "allow")]
PLUGIN_DENIES = ("question", "task_cancel", "task_message", "task_reply", "task_status", "acp_run",
                 "interview_submit_state", "wait_for_user", "marketplace_inspect", "marketplace_manage", "skill")
PLUGIN_MCP_DENIES = ("context7_*", "gh_grep_*")
RANK = {"deny": 0, "ask": 1, "allow": 2}
PROBE_ACTIONS = ("read", "grep", "edit", "write", "shell", "bash", "external_directory", "question", "browser",
                 "skill", "subagent", "context7_resolve", "ast_grep_replace", "exec")
PROBE_RESOURCES = ("*", "x.py", "src/a.py", ".env", "git status", "code-assurance", "browser")
KEYS = ("*", "edit", "write", "bash", "shell", "read", "task", "webfetch", "question", "browser", "skill",
        "context7_x", "ast_grep_replace", "e*", "external_directory")
EFFECTS = ("allow", "ask", "deny")


def plugin_rules(permission, skills=()):
    """The rules OpenCode resolves for a plugin agent with this `permission` and these skills."""
    permission = {"*": permission} if isinstance(permission, str) else (permission or {})
    rules = list(PLUGIN_FIRST)
    for key, value in permission.items():
        actions = ["subagent"] if key == "task" else ["shell", "bash"] if key == "bash" else [key]
        for pattern, effect in ([("*", value)] if isinstance(value, str) else value.items()):
            rules += [(action, pattern, effect) for action in actions]
    rules += [(action, "*", "deny") for action in PLUGIN_DENIES if action not in permission]
    rules += [("skill", skill, "allow") for skill in skills]
    rules += [(action, "*", "deny") for action in PLUGIN_MCP_DENIES if action not in permission]
    return [{"action": a, "resource": r, "effect": e} for a, r, e in rules] + list(agentfiles.HOST_TAIL)


def values(key):
    """Every value a key gets in the generated sources: one word, and a two-pattern map in both orders."""
    pattern = {"bash": "git *", "shell": "git *", "read": "*.env"}.get(key, "src/*")
    out = list(EFFECTS)
    for first in EFFECTS:
        for second in EFFECTS:
            out += [{"*": first, pattern: second}, {pattern: second, "*": first}]
    return out


def sources():
    """Generated source permissions: each key alone with every value; the wildcard (every word, and a map) first
    and last beside every other key with one word or a map in either order; and seeded random triples."""
    import random
    out = [{key: value} for key in KEYS for value in values(key)]
    wildcards = [*EFFECTS, {"src/*": "allow", "*": "ask"}, {"*": "deny", "src/*": "allow"}]
    for wildcard in wildcards:
        for key in KEYS[1:]:
            for value in [*EFFECTS, *values(key)[3::5]]:
                out += [{"*": wildcard, key: value}, {key: value, "*": wildcard}]
    pick = random.Random(99)
    for _ in range(200):
        keys = pick.sample(KEYS, 3)
        out.append({key: pick.choice(values(key)) for key in keys})
    return out


class CopyNeverWider(Base):
    """A copy allows nothing its source does not, under the rule order OpenCode really uses (the n99 proof)."""

    def wider(self, permission, skills=()):
        """(action, resource, source effect, copy effect) where the copy is let do more than the source."""
        source = plugin_rules(permission, skills)
        rules = agentfiles.from_plugin(permission, skills)[0]
        found = []
        for action in PROBE_ACTIONS:
            for resource in PROBE_RESOURCES:
                was, now = agentfiles.resolve(source, action, resource), agentfiles.host_effect(rules, action, resource)
                if RANK[now] > RANK[was]:
                    found.append((action, resource, was, now))
        return found

    def test_the_reviewers_three_sources_give_copies_no_wider_than_they_are(self):
        rules = agentfiles.from_plugin({"edit": "allow", "*": "deny"})[0]
        self.assertTrue(agentfiles.read_only(rules))
        for action in ("edit", "read", "grep", "shell"):
            self.assertEqual(agentfiles.host_effect(rules, action, "x.py"), "deny", action)
        self.assertTrue(agentfiles.summary(rules)["read_only"])
        rules = agentfiles.from_plugin({"bash": {"git *": "allow", "*": "deny"}})[0]
        self.assertEqual(agentfiles.host_effect(rules, "shell", "git status"), "deny")
        rules = agentfiles.from_plugin({"*": "deny", "bash": "allow"})[0]
        self.assertEqual(agentfiles.host_effect(rules, "read", "x.py"), "deny")
        self.assertEqual(agentfiles.host_effect(rules, "shell", "ls"), "allow")
        for permission in ({"edit": "allow", "*": "deny"}, {"bash": {"git *": "allow", "*": "deny"}},
                           {"*": "deny", "bash": "allow"}):
            self.assertEqual(self.wider(permission), [], permission)

    def test_no_generated_source_gives_a_wider_copy(self):
        cases = sources()
        self.assertGreater(len(cases), 1000)
        for index, permission in enumerate(cases):
            for skills in ((), ("code-assurance",)) if index % 4 == 0 else ((),):
                found = self.wider(permission, skills)
                if found:
                    self.fail(f"{permission} with skills {list(skills)}: the copy may do more: {found[:3]}")

    def test_the_summary_agrees_with_what_the_host_resolves(self):
        for permission in sources()[::7]:
            rules = agentfiles.from_plugin(permission)[0]
            shown = agentfiles.summary(rules)
            self.assertEqual(shown["rules"], [r for r in rules[1:-1] if r["action"] != "skill"], permission)
            edits = any(agentfiles.host_effect(rules, action, resource) == "allow"
                        for action in agentfiles.WRITE_ACTIONS for resource in ("x.py", "src/a.py", "*"))
            self.assertEqual(shown["read_only"], not edits, permission)
            self.assertEqual(agentfiles.parse(agentfiles.render("d", "a/b", rules, "t"))["permissions"], rules)

    def test_honest_copies_keep_what_their_source_has_and_no_more(self):
        obj = json.loads(self.config.read_bytes())
        for name, options in (("impl-claude", {"copy_of": "impl-claude"}), ("reviewer", {"copy_of": "reviewer"}),
                              ("researcher", {"copy_of": "researcher"}), ("verifier", {"template": "verifier"})):
            spec = roster._source_spec(obj, name)
            if spec is None:
                continue
            with self.subTest(name=name):
                self.add(f"copy-{name}", **options)
                rules = agentfiles.parse(self.file(f"copy-{name}"))["permissions"]
                skills = [] if "template" in options else spec.get("skills", [])
                source = plugin_rules(spec.get("permission"), skills)
                for action in PROBE_ACTIONS:
                    for resource in PROBE_RESOURCES:
                        was = agentfiles.resolve(source, action, resource)
                        now = agentfiles.host_effect(rules, action, resource)
                        self.assertLessEqual(RANK[now], RANK[was], (action, resource))
                        if action in ("read", "grep", "list", "edit", "shell"):
                            self.assertEqual(now, was, (action, resource))


    def test_an_ask_on_a_pattern_stays_ask(self):
        rules = agentfiles.from_plugin({"edit": {"src/*": "ask"}, "bash": {"*": "allow", "git *": "ask"}})[0]
        self.assertIn({"action": "edit", "resource": "src/*", "effect": "ask"}, rules)
        self.assertEqual(agentfiles.host_effect(rules, "edit", "src/a.py"), "ask")
        self.assertEqual(agentfiles.host_effect(rules, "edit", "x.py"), "deny")
        self.assertEqual(agentfiles.host_effect(rules, "shell", "git status"), "ask")
        self.assertEqual(agentfiles.host_effect(rules, "shell", "ls"), "allow")

    def test_external_directory_is_never_copied_as_an_allow(self):
        # The plugin may deny it after the entry's rules (an MCP server named `external` gives `external_*` deny).
        for value in ("allow", "ask", {"/tmp/*": "allow"}):
            rules, notes = agentfiles.from_plugin({"external_directory": value})
            self.assertEqual(agentfiles.host_effect(rules, "external_directory", "/tmp/x"), "deny", value)
            self.assertTrue(notes)
        rules = agentfiles.from_plugin({"external_directory": "deny"})[0]
        self.assertIn({"action": "external_directory", "resource": "*", "effect": "deny"}, rules)

    def test_an_allow_on_any_write_action_makes_an_agent_a_writer(self):
        for action in ("write", "apply_patch", "patch", "multiedit"):
            with self.subTest(action=action):
                rules = agentfiles.from_plugin({"edit": "deny", action: "allow"})[0]
                self.assertEqual(agentfiles.host_effect(rules, action, "x.py"), "allow")
                self.assertFalse(agentfiles.read_only(rules))
                self.assertFalse(agentfiles.summary(rules)["read_only"])
                rules = agentfiles.from_plugin({"edit": "deny", action: {"*": "deny", "docs/*": "allow"}})[0]
                self.assertFalse(agentfiles.read_only(rules))

    def test_read_only_kinds_deny_every_write_action(self):
        self.add(template="verifier")
        self.add("looker", model="a/b", description="Looks.", prompt="Look.", read_only=True)
        for name in ("checker", "looker"):
            rules = agentfiles.parse(self.file(name))["permissions"]
            self.assertTrue(agentfiles.read_only(rules), name)
            for action in agentfiles.WRITE_ACTIONS:
                for resource in ("x.py", "src/a.py", "*"):
                    self.assertEqual(agentfiles.host_effect(rules, action, resource), "deny", (name, action))


class HandRights(Base):
    def test_a_hand_added_permission_or_tools_map_makes_the_file_hand_changed(self):
        # OpenCode 2.3.2 reads OpenCode 1's `permission` and `tools` maps in an agent file (the n99 proof).
        self.add(template="verifier")
        original = self.file()
        for line in (b"permission:\n  edit: allow", b"tools:\n  bash: true", b'"permission": {edit: allow}',
                     b"<<: *rights", b"'permissions': []"):
            with self.subTest(line=line):
                (self.agents / "checker.md").write_bytes(original.replace(b"\nmode: subagent\n",
                                                                          b"\nmode: subagent\n" + line + b"\n", 1))
                self.assertIsNone(agentfiles.parse(self.file()))
                self.assertFalse(agentfiles.listing(self.agents)["checker"]["editable"])
                self.refused("changed by hand", roster.read_agent, "checker", agents_dir=self.agents)
        (self.agents / "checker.md").write_bytes(original)
        self.assertIsNotNone(agentfiles.parse(self.file()))

    def test_any_other_shape_of_a_frontmatter_key_makes_the_file_hand_changed(self):
        # OpenCode's YAML reader takes each of these as the key `permission` (the n99 proof).
        self.add(template="verifier")
        original = self.file()

        def edited(line):
            data = original.replace(b"\nmode: subagent\n", b"\nmode: subagent\n" + line + b"\n", 1)
            (self.agents / "checker.md").write_bytes(data)
            return agentfiles.parse(data)

        for line in (b"? permission\n: {edit: allow}", b"!!str permission: {edit: allow}",
                     b'"permissio\\u006e": {edit: allow}', b"x: &r {edit: allow}\n? permission\n: *r",
                     b"Temperature: 0.2", b"x:1", b"  permission: {edit: allow}", b"- a", b"\tx: 1"):
            with self.subTest(line=line):
                self.assertIsNone(edited(line))
                self.refused("changed by hand", roster.read_agent, "checker", agents_dir=self.agents)
        for line in (b"temperature: 0.2", b"options:\n  top_p: 0.9", b"# a note", b"tags:\n- a\n- b", b"steps:"):
            with self.subTest(line=line):
                self.assertIsNotNone(edited(line))


class CraftedText(Base):
    def test_bidi_and_line_separator_characters_are_refused_in_agent_text(self):
        for bad in ("\u202e", "\u2066", "\u2028", "\u2029"):
            with self.subTest(char=hex(ord(bad))):
                self.refused("control character", self.add, "plain", model="a/b", description=f"Plain{bad}.",
                             prompt="Work.")
                self.refused("control character", self.add, "plain", model="a/b", description="Plain.",
                             prompt=f"Work{bad}.")
        self.add(template="verifier")
        self.refused("control character", roster.text_agent, "checker", "New\u202e.", agents_dir=self.agents,
                     backups=self.backups)

    def test_add_with_crafted_description_prompt_model_and_variant(self):
        injected = '---\npermissions:\n  - action: "*"\n    resource: "*"\n    effect: allow\n---\nmode: primary'
        result = self.add("quoted", template="verifier", description='Says "hi" \\ there',
                          prompt='Quote " and\n' + injected)
        parsed = agentfiles.parse(self.file("quoted"))
        self.assertEqual(parsed["description"], 'Says "hi" \\ there')
        self.assertEqual(parsed["text"].rstrip("\n"), 'Quote " and\n' + injected)
        self.assertEqual(parsed["permissions"], agentfiles.from_plugin(
            roster._source_spec(json.loads(self.config.read_bytes()), "verifier").get("permission"))[0])
        self.assertEqual(self.file("quoted").count(b"\npermissions:\n"), 2)  # the frontmatter's, then the text's
        self.assertEqual(result["permission"], "read-only")
        for kwargs in ({"description": "x\n---\npermissions:"}, {"description": "x\u2028mode: primary"},
                       {"model": 'a/b"'}, {"model": "a/b\u2028"}, {"model": "a/b\n---\npermissions:"},
                       {"variant": 'high"'}, {"variant": "hi gh"}, {"variant": "x\u2028"},
                       {"variant": "x\n---\npermissions:"}, {"variant": "a#b"}):
            with self.subTest(**{key: repr(value) for key, value in kwargs.items()}):
                with self.assertRaises(skillset.SkillsetError):
                    self.add("crafted", template="verifier", **kwargs)
                self.assertFalse((self.agents / "crafted.md").exists())

    def test_a_copied_model_that_opencode_would_not_read_is_refused(self):
        obj = json.loads(self.config.read_bytes())
        obj["presets"][obj["preset"]]["impl-claude"]["model"] = 'anthropic/claude "x"'
        self.config.write_text(json.dumps(obj, indent=2) + "\n")
        self.start = self.snapshot()
        self.refused("model must be provider/model", self.add, "copy", copy_of="impl-claude")

    def test_a_description_that_reads_as_a_structure_setting_is_refused(self):
        for text in ("seats: impl-", "escalates_to: x", "Pool: big", "  pool: big"):
            with self.subTest(text=text):
                self.refused("structure setting", self.add, "plain", model="a/b", description=text, prompt="Work.")
        self.add("plain", model="a/b", description="Checks: the tests.", prompt="Work.")


class Records(Base):
    def test_agent_file_undo_records_are_pruned_like_skill_backups(self):
        self.add(template="verifier")
        with patch.object(skillset, "MAX_BACKUPS", 3):
            records = [roster.text_agent("checker", f"Text {n}.", agents_dir=self.agents,
                                         backups=self.backups)["record"] for n in range(6)]
        kept = sorted(p.parent for p in self.backups.rglob("text-checker-*/record.json"))
        self.assertEqual(len(kept), 3)
        self.assertIn(Path(records[-1]).parent, kept)
        self.assertNotIn(Path(records[0]).parent, kept)
        roster.restore_agent(records[-1], **self.paths)
        self.assertTrue(self.file().endswith(b"---\nText 4.\n"))

    def test_agent_file_undo_records_are_pruned_across_days(self):
        self.add(template="verifier")
        with patch.object(skillset, "MAX_BACKUPS", 3):
            older = [roster.text_agent("checker", f"Old {n}.", agents_dir=self.agents,
                                       backups=self.backups)["record"] for n in range(2)]
            for days, record in enumerate(older):
                day = self.backups / f"2020-01-0{days + 1}-agent-files"
                day.mkdir()
                Path(record).parent.rename(day / Path(record).parent.name)
            other = roster.text_agent("checker", "Other.", agents_dir=self.agents, backups=self.backups)["record"]
            newer = [roster.text_agent("checker", f"Text {n}.", agents_dir=self.agents,
                                       backups=self.backups)["record"] for n in range(2)]
        kept = sorted(p.parent for p in self.backups.rglob("text-checker-*/record.json"))
        self.assertEqual(kept, sorted(Path(r).parent for r in [other, *newer]))
        self.assertFalse((self.backups / "2020-01-01-agent-files").exists())
        self.assertFalse((self.backups / "2020-01-02-agent-files").exists())

    def test_a_failed_add_whose_take_back_failed_too_says_which_file_stays(self):
        forward = roster._change_file

        def change(root_fd, item, check=True):
            if not check:
                raise OSError("simulated take-back failure")
            return forward(root_fd, item, check)

        with patch.object(agentfiles, "replace", side_effect=OSError("simulated")), \
                patch.object(roster, "_change_file", side_effect=change):
            with self.assertRaisesRegex(skillset.SkillsetError, r"30-build/checker\.md could not be taken back"):
                self.add(template="verifier")
        self.assertFalse((self.agents / "checker.md").exists())

    def test_a_failed_remove_whose_put_back_failed_too_keeps_its_undo_record(self):
        self.add(template="verifier")
        structure_module.add_flow(["checker", "reviewer"], None, self.structure)
        forward = roster._change_file

        def change(root_fd, item, check=True):
            if not check:
                raise OSError("simulated put-back failure")
            return forward(root_fd, item, check)

        with patch.object(agentfiles, "replace", side_effect=OSError("simulated")), \
                patch.object(roster, "_change_file", side_effect=change):
            with self.assertRaisesRegex(skillset.SkillsetError, "could not be put back"):
                roster.remove_agent("checker", **self.paths)
        records = list(self.backups.rglob("remove-checker-*/record.json"))
        self.assertEqual(len(records), 1)
        data = json.loads(records[0].read_text())
        self.assertTrue(any(item["folder"] == "flows" for item in data["structure"]))

    def test_a_forged_remove_record_is_refused(self):
        self.add(template="verifier")
        record = roster.remove_agent("checker", **self.paths)["record"]
        path = Path(record)
        data = json.loads(path.read_text())
        wide = agentfiles.unb64(data["file"], "file").replace(
            b'permissions:\n  - action: "*"\n    resource: "*"\n    effect: deny\n',
            b'permissions:\n  - action: "*"\n    resource: "*"\n    effect: allow\n', 1)
        self.assertNotEqual(wide, agentfiles.unb64(data["file"], "file"))
        for forged in ({**data, "file": agentfiles.b64(wide)},
                       {**data, "file": agentfiles.b64(b"---\ndescription: mine\n---\nBody\n")},
                       {**data, "kind": "custom"}, {**data, "op": "text"},
                       {**data, "structure": [{"folder": "30-build", "name": "reviewer.md",
                                               "before": agentfiles.b64(b"x"), "after": None}]}):
            with self.subTest(keys=sorted(k for k in forged if forged[k] != data.get(k))):
                path.write_text(json.dumps(forged))
                self.refused("invalid undo record", roster.restore_agent, record, **self.paths)
        path.write_text(json.dumps(data))
        roster.restore_agent(record, **self.paths)


class NameAlone(Base):
    """An add with a name alone (what the view sends for a blank agent) works by endpoint and by command; any field
    given overrides its default; removing the agent puts every folder back exactly."""

    def setUp(self):
        super().setUp()
        import threading
        from agentgraph import server
        self.httpd = server.make_server(
            0, graph_dir=self.root / "graphs", cwd=self.root, session="root", web_dir=self.root,
            structure_dir=self.structure, skills_config=self.config, skills_map=self.map,
            skills_backups=self.backups, skills_dir=self.skills, skills_bundled=[], agents_dir=self.agents)
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.addCleanup(self.httpd.server_close)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.httpd.shutdown)

    def post(self, body):
        import http.client
        port = self.httpd.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("POST", "/api/roster", body=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
            response = conn.getresponse()
            answer = json.loads(response.read())
        finally:
            conn.close()
        self.assertEqual(response.status, 200, answer)
        return answer

    def spec(self, name):
        obj = json.loads(self.config.read_bytes())
        return obj["presets"][obj["preset"]][name]

    def verifier_model(self):
        spec = self.spec("verifier")
        return f"{spec['model']}#{spec['variant']}" if spec.get("variant") else spec["model"]

    def blank(self, name="agent-1"):
        return agentfiles.render(agentfiles.BLANK_DESCRIPTION, self.verifier_model(),
                                 agentfiles.from_plugin(roster.WRITER_PERMISSION)[0], "")

    def removed_exactly(self, name):
        self.post({"op": "remove", "name": name})
        self.assertEqual(self.snapshot(), self.start)

    def test_a_name_alone_by_endpoint_adds_a_blank_agent(self):
        answer = self.post({"op": "add", "name": "agent-1"})
        self.assertEqual(self.file("agent-1"), self.blank())
        self.assertEqual((answer["model"], answer["permission"]), (self.verifier_model(), "writer"))
        parsed = agentfiles.parse(self.file("agent-1"))
        self.assertEqual(parsed["text"].strip(), "")
        self.assertEqual(roster.read_agent("agent-1", agents_dir=self.agents)["description"],
                         "An agent you added. Open it to say what it does.")
        self.assertTrue((self.structure / "30-build" / "agent-1.md").is_file())
        # the user then writes its instruction
        roster.text_agent("agent-1", "Do the work.", agents_dir=self.agents, backups=self.backups)
        self.assertEqual(agentfiles.parse(self.file("agent-1"))["text"].strip(), "Do the work.")
        self.removed_exactly("agent-1")

    def test_the_blank_template_with_a_name_alone_by_endpoint(self):
        self.post({"op": "add", "name": "agent-1", "template": "blank"})
        self.assertEqual(self.file("agent-1"), self.blank())
        self.removed_exactly("agent-1")

    def test_the_verifier_template_with_a_name_alone_by_endpoint(self):
        self.post({"op": "add", "name": "agent-2", "template": "verifier"})
        rules = agentfiles.from_plugin(self.spec("verifier").get("permission"), [])[0]
        self.assertEqual(self.file("agent-2"), agentfiles.render(agentfiles.VERIFIER_DESCRIPTION, self.verifier_model(),
                                                                 rules, agentfiles.VERIFIER_TEXT))
        self.removed_exactly("agent-2")

    def test_a_copy_with_a_name_alone_by_endpoint(self):
        self.post({"op": "add", "name": "agent-3", "copy_of": "impl-claude"})
        spec = self.spec("impl-claude")
        rules = agentfiles.from_plugin(spec.get("permission"), list(spec.get("skills") or []))[0]
        text = spec.get("prompt") or "You are agent-3, a copy of impl-claude."
        model = f"{spec['model']}#{spec['variant']}" if spec.get("variant") else spec["model"]
        self.assertEqual(self.file("agent-3"), agentfiles.render(spec["description"], model, rules, text))
        self.removed_exactly("agent-3")

    def test_any_field_given_overrides_its_blank_default(self):
        self.post({"op": "add", "name": "agent-1", "description": "Mine.", "prompt": "Do it.",
                   "model": "openai/gpt-6.2-sol-fast", "read_only": True})
        self.assertEqual(self.file("agent-1"), agentfiles.render(
            "Mine.", "openai/gpt-6.2-sol-fast", agentfiles.from_plugin(roster.READ_ONLY_PERMISSION)[0], "Do it."))
        self.removed_exactly("agent-1")
        self.post({"op": "add", "name": "agent-1", "template": "blank", "variant": "low"})
        model = self.verifier_model().partition("#")[0] + "#low"
        self.assertEqual(agentfiles.parse(self.file("agent-1"))["model"], model)
        self.assertEqual(agentfiles.parse(self.file("agent-1"))["description"], agentfiles.BLANK_DESCRIPTION)
        self.removed_exactly("agent-1")

    def test_agent_add_with_a_name_alone_by_command(self):
        out = io.StringIO()
        common = ["--config", str(self.config), "--map", str(self.map), "--backups", str(self.backups),
                  "--structure", str(self.structure), "--agents", str(self.agents), "--skills-dir", str(self.skills),
                  "--json"]
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = roster.main(["agent", "add", "agent-1", *common])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(self.file("agent-1"), self.blank())
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            self.assertEqual(roster.main(["agent", "remove", "agent-1", *common]), 0)
        self.assertEqual(self.snapshot(), self.start)


class Command(Base):
    def cli(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch.object(sys, "stdin", io.StringIO(stdin)):
            code = roster.main(["agent", *args, "--config", str(self.config), "--map", str(self.map), "--backups",
                                str(self.backups), "--structure", str(self.structure), "--agents", str(self.agents),
                                "--skills-dir", str(self.skills), "--json"])
        return code, out.getvalue(), err.getvalue()

    def test_add_show_text_restore_remove_by_command(self):
        code, out, _ = self.cli("add", "checker", "--template", "verifier")
        self.assertEqual(code, 0, out)
        code, out, _ = self.cli("show", "checker")
        self.assertEqual(json.loads(out)["text"], agentfiles.VERIFIER_TEXT)
        before = self.file()
        code, out, _ = self.cli("text", "checker", "--text-file", "-", stdin="New.\n")
        record = json.loads(out)["record"]
        self.assertEqual(self.cli("restore", record)[0], 0)
        self.assertEqual(self.file(), before)
        code, out, _ = self.cli("remove", "checker")
        self.assertEqual(code, 0)
        self.assertEqual(self.cli("restore", json.loads(out)["record"])[0], 0)
        self.assertEqual(self.file(), before)
        code, out, _ = self.cli("add", "Bad", "--template", "verifier")
        self.assertEqual(code, 1)
        self.assertIn("invalid agent name", json.loads(out)["error"])


if __name__ == "__main__":
    unittest.main()
