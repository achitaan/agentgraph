"""The Desktop app's path (review n8 r5, major): it takes its snapshot from `python3 -P -m agentgraph.snapshot_json`
and writes through `python3 -P -m agentgraph.roster|skillset|structure|teams|skillfiles ... --json`, every one with
the default folders (none passed). A `rev` the snapshot shows must be the one each writer checks. Runs those exact
command lines in a scratch copy of the orchestration (PYTHONPATH, as the plugin sets it) with HOME in scratch, so
the default folders are scratch ones; the real preset file is copied, never written, and never printed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.launch import bare_env, home_env
from agentgraph import skillset

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = Path(tempfile.gettempdir()).resolve() / "agentgraph-tests-desktop-paths"
OLD = "0123456789abcdef"  # a rev no agent has


class DesktopPaths(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.orch = self.root / "orchestration"
        shutil.copytree(ROOT / "agentgraph", self.orch / "agentgraph",
                        ignore=shutil.ignore_patterns("__pycache__", "web"))
        # without the user's own drawn flows and groups (git-ignored): a flow with a command would block a removal
        shutil.copytree(ROOT / "structure", self.orch / "structure", ignore=shutil.ignore_patterns("flows", "teams"))
        shutil.copyfile(ROOT / "SKILLS.md", self.orch / "SKILLS.md")
        self.home = self.root / "home"
        config = self.home / ".config" / "opencode"
        for folder in ("agents", "commands", "backups"):
            (config / folder).mkdir(parents=True)
        for name in ("code-assurance", "engineering-delivery"):
            (config / "skills" / name).mkdir(parents=True)
            (config / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---\nBody\n", encoding="utf-8")
        shutil.copyfile(skillset.CONFIG, config / skillset.CONFIG.name)
        self.config_dir = config
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.env = {**bare_env(PATH=os.environ.get("PATH", "/usr/bin:/bin")), **home_env(self.home),
                    "PYTHONPATH": str(self.orch), "PYTHONIOENCODING": "utf-8", "TMPDIR": str(self.root) + "/"}

    def run_module(self, *args, stdin="", cwd=None):
        # As the plugin runs them: the snapshot in the session's folder, every writer in the orchestration folder.
        done = subprocess.run([sys.executable, "-P", "-m", *args], cwd=cwd or self.orch, env=self.env, input=stdin,
                              capture_output=True, text=True, timeout=120)
        last = [line for line in done.stdout.splitlines() if line.strip()]
        return done.returncode, json.loads(last[-1]) if last else {"stderr": done.stderr}

    def snapshot(self):
        code, document = self.run_module("agentgraph.snapshot_json", cwd=self.cwd)
        self.assertEqual(code, 0, document)
        self.assertNotIn("error", document)
        return document

    def agent_rev(self, name):
        agents = {agent["name"]: agent for agent in self.snapshot()["agents"]}
        self.assertIn(name, agents)
        return agents[name]["rev"]

    def accepted(self, *args, stdin=""):
        code, answer = self.run_module(*args, stdin=stdin)
        self.assertEqual(code, 0, answer)
        self.assertNotIn("error", answer)
        return answer

    def refused(self, *args, stdin=""):
        code, answer = self.run_module(*args, stdin=stdin)
        self.assertEqual(code, 1, answer)
        self.assertIn("reload and try again", answer.get("error", ""))
        self.assertRegex(answer.get("rev", ""), r"^[0-9a-f]{16}$")
        return answer

    def test_every_agent_writer_the_desktop_calls_takes_the_snapshots_rev(self):
        roster = ("agentgraph.roster", "agent")
        self.accepted(*roster, "add", "xavier", "--template", "verifier", "--json")
        # instruction
        rev = self.agent_rev("xavier")
        self.refused(*roster, "text", "xavier", "--text-file", "-", "--expect", OLD, "--json", stdin="Old view.\n")
        self.accepted(*roster, "text", "xavier", "--text-file", "-", "--expect", rev, "--json", stdin="Mine.\n")
        self.refused(*roster, "text", "xavier", "--text-file", "-", "--expect", rev, "--json", stdin="Again.\n")
        # skills of an added agent and of a preset agent
        rev = self.agent_rev("xavier")
        self.refused("agentgraph.skillset", "add", "xavier", "code-assurance", "--expect", OLD, "--json")
        self.accepted("agentgraph.skillset", "add", "xavier", "code-assurance", "--expect", rev, "--json")
        rev = self.agent_rev("impl-claude")
        self.refused("agentgraph.skillset", "add", "impl-claude", "engineering-delivery", "--expect", OLD, "--json")
        self.accepted("agentgraph.skillset", "add", "impl-claude", "engineering-delivery", "--expect", rev, "--json")
        # rename, then remove
        rev = self.agent_rev("xavier")
        self.refused(*roster, "rename", "xavier", "yusuf", "--expect", OLD, "--json")
        self.accepted(*roster, "rename", "xavier", "yusuf", "--expect", rev, "--json")
        rev = self.agent_rev("yusuf")
        self.refused(*roster, "remove", "yusuf", "--expect", OLD, "--json")
        self.accepted(*roster, "remove", "yusuf", "--expect", rev, "--json")
        self.assertFalse((self.config_dir / "agents" / "yusuf.md").exists())

    def test_a_preset_agents_remove_takes_the_snapshots_rev(self):
        rev = self.agent_rev("impl-hard")
        self.refused("agentgraph.roster", "agent", "remove", "impl-hard", "--expect", OLD, "--json")
        self.accepted("agentgraph.roster", "agent", "remove", "impl-hard", "--expect", rev, "--json")

    def test_flows_groups_and_skill_text_take_the_snapshots_rev(self):
        flow = self.accepted("agentgraph.structure", "flow", "add", "impl-openai", "reviewer", "--json")["flow"]
        shown = {item["id"]: item for item in self.snapshot()["structure"]["flows"]}
        self.refused("agentgraph.structure", "flow", "text", flow["id"], "--text-file", "-", "--expect", OLD, "--json",
                     stdin="Old view.")
        self.accepted("agentgraph.structure", "flow", "text", flow["id"], "--text-file", "-", "--expect",
                      shown[flow["id"]]["rev"], "--json", stdin="Mine.")
        team = self.accepted("agentgraph.teams", "add", "reviewer", "impl-openai", "--json")["team"]
        shown = {item["id"]: item for item in self.snapshot()["structure"]["teams"]}
        self.refused("agentgraph.teams", "text", team["id"], "--text-file", "-", "--expect", OLD, "--json",
                     stdin="Old view.")
        self.accepted("agentgraph.teams", "text", team["id"], "--text-file", "-", "--expect", shown[team["id"]]["rev"],
                      "--json", stdin="Mine.")
        shown = {item["name"]: item for item in self.snapshot()["skills"]}
        self.assertIn("code-assurance", shown)
        self.refused("agentgraph.skillfiles", "skill", "write", "code-assurance", "--text-file", "-", "--expect", OLD,
                     "--json", stdin="Old view.\n")
        self.accepted("agentgraph.skillfiles", "skill", "write", "code-assurance", "--text-file", "-", "--expect",
                      shown["code-assurance"]["rev"], "--json", stdin="Mine.\n")


if __name__ == "__main__":
    unittest.main()
