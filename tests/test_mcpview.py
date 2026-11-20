"""MCP servers in the view: which agent may use which (the plugin's own rule), and that nothing but names and
states leaves the settings. Scratch folders and scratch settings only."""
import http.client
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout

from agentgraph import mcpview, names, server

SCRATCH = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode/mcpview-tests")
ROOT = Path(__file__).resolve().parent.parent
MARKER = "PLANTED-SECRET-7f3a91"


def doc(*agent_names):
    return {"agents": [{"name": name} for name in agent_names]}


class Scratch(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / "oh-my-opencode-slim.json"
        self.opencode = self.root / "opencode.json"

    def write(self, config=None, opencode=None):
        if config is not None:
            self.config.write_text(json.dumps(config))
        if opencode is not None:
            self.opencode.write_text(json.dumps(opencode))

    def annotate(self, *agent_names):
        document = doc(*agent_names)
        mcpview.annotate(document, config=self.config, opencode=self.opencode)
        return document

    def mcps(self, document):
        return {agent["name"]: agent["mcps"] for agent in document["agents"]}

    def servers(self, document):
        return {row["name"]: row for row in document["mcp"]}


class TheRule(Scratch):
    """The plugin's own rule (dist/index.js: parseList, getAgentMcpList, DEFAULT_AGENT_MCPS, availableMcpNames)."""

    def test_defaults_when_an_agent_lists_nothing(self):
        self.write({"preset": "p", "presets": {"p": {"orchestrator": {"model": "a/b"}, "explorer": {"model": "a/b"},
                                                     "oracle": {"model": "a/b"}, "helper": {"model": "a/b"}}}},
                   {"mcp": {"docs": {"type": "remote", "url": "https://x"}, "local1": {"type": "local", "command": ["x"]}}})
        got = self.mcps(self.annotate("orchestrator", "explorer", "oracle", "helper"))
        self.assertEqual(got["orchestrator"], ["gh_grep", "docs", "local1"])  # "*" except context7: OpenCode's servers too
        self.assertEqual(got["explorer"], ["context7", "gh_grep"])
        self.assertEqual(got["oracle"], [])
        self.assertEqual(got["helper"], [])  # a custom agent has none unless it lists them

    def test_wildcards_and_exclusions(self):
        self.write({"preset": "p", "presets": {"p": {
            "a": {"model": "x/y", "mcps": ["*"]}, "b": {"model": "x/y", "mcps": ["*", "!docs", "!gh_grep"]},
            "c": {"model": "x/y", "mcps": ["docs", "!docs", "context7"]}, "d": {"model": "x/y", "mcps": ["*", "!*"]},
            "e": {"model": "x/y", "mcps": ["nothing-by-that-name", "docs"]}, "f": {"model": "x/y", "mcps": []},
            "g": {"model": "x/y", "mcps": ["!docs"]}}}}, {"mcp": {"docs": {"type": "remote"}}})
        got = self.mcps(self.annotate(*"abcdefg"))
        self.assertEqual(got["a"], ["context7", "gh_grep", "docs"])
        self.assertEqual(got["b"], ["context7"])
        self.assertEqual(got["c"], ["context7"])
        self.assertEqual(got["d"], [])
        self.assertEqual(got["e"], ["docs"])
        self.assertEqual(got["f"], [])
        self.assertEqual(got["g"], [])

    def test_an_explicit_list_overrides_the_default(self):
        self.write({"preset": "p", "presets": {"p": {"explorer": {"model": "x/y", "mcps": []},
                                                     "orchestrator": {"model": "x/y", "mcps": ["context7"]}}}})
        got = self.mcps(self.annotate("explorer", "orchestrator"))
        self.assertEqual(got, {"explorer": [], "orchestrator": ["context7"]})

    def test_extends_top_level_agents_and_aliases(self):
        self.write({"preset": "child", "agents": {"fixer": {"mcps": ["gh_grep"]}},
                    "presets": {"base": {"librarian": {"model": "x/y", "mcps": ["context7"]},
                                         "fixer": {"model": "x/y", "mcps": ["context7"]}},
                                "child": {"extends": "base", "oracle": {"model": "x/y", "mcps": ["*"]}}}})
        got = self.mcps(self.annotate("explorer", "fixer", "oracle"))
        self.assertEqual(got["explorer"], ["context7"])  # the old name `librarian` is the explorer
        self.assertEqual(got["fixer"], ["gh_grep"])  # the top-level `agents` win over the preset
        self.assertEqual(got["oracle"], ["context7", "gh_grep"])

    def test_a_preset_that_cannot_be_resolved_leaves_the_defaults(self):
        self.write({"preset": "loop", "presets": {"loop": {"extends": "loop", "explorer": {"mcps": []}}}})
        self.assertEqual(self.mcps(self.annotate("explorer"))["explorer"], ["context7", "gh_grep"])

    def test_disabled_and_off_servers(self):
        self.write({"preset": "p", "disabled_mcps": ["context7"],
                    "presets": {"p": {"explorer": {"model": "x/y"}, "orchestrator": {"model": "x/y"}}}},
                   {"mcp": {"off1": {"type": "local", "enabled": False}, "off2": {"disabled": True}, "on1": {"type": "remote"}}})
        document = self.annotate("explorer", "orchestrator")
        self.assertEqual(self.mcps(document)["explorer"], ["gh_grep"])  # a disabled built-in is not there to use
        rows = self.servers(document)
        self.assertNotIn("context7", rows)
        self.assertEqual((rows["off1"]["state"], rows["off1"]["enabled"]), ("off", False))
        self.assertEqual(rows["off2"]["state"], "off")
        self.assertEqual((rows["on1"]["state"], rows["on1"]["source"]), ("on", "opencode"))
        self.assertEqual(rows["gh_grep"]["source"], "plugin")
        self.assertEqual(rows["off1"]["kind"], "local")
        self.assertEqual(rows["on1"]["kind"], "remote")
        self.assertEqual(rows["off2"]["kind"], "")  # no type given: not claimed
        self.assertIn("off1", self.mcps(document)["orchestrator"])  # off or not, the agent's permission is the same

    def test_a_host_server_with_a_builtins_name_replaces_it(self):
        self.write({"preset": "p", "presets": {"p": {"explorer": {"model": "x/y"}}}}, {"mcp": {"context7": {"type": "local"}}})
        rows = self.servers(self.annotate("explorer"))
        self.assertEqual((rows["context7"]["source"], rows["context7"]["kind"]), ("opencode", "local"))

    def test_the_list_of_servers_says_which_agents_may_use_each(self):
        self.write({"preset": "p", "presets": {"p": {"explorer": {"model": "x/y"}, "oracle": {"model": "x/y"},
                                                     "orchestrator": {"model": "x/y"}}}}, {"mcp": {"docs": {}}})
        rows = self.servers(self.annotate("orchestrator", "explorer", "oracle"))
        self.assertEqual(rows["context7"]["agents"], ["explorer"])
        self.assertEqual(rows["gh_grep"]["agents"], ["orchestrator", "explorer"])
        self.assertEqual(rows["docs"]["agents"], ["orchestrator"])

    def test_an_agent_the_plugin_does_not_manage_has_no_list(self):
        self.write({"preset": "p", "presets": {"p": {"explorer": {"model": "x/y"}}}})
        got = self.mcps(self.annotate("explorer", "build", "reviewer", "councillor-alpha"))
        self.assertIsNone(got["build"])
        self.assertIsNone(got["reviewer"])
        self.assertEqual(got["councillor-alpha"], [])

    def test_unreadable_settings_say_nothing(self):
        self.config.write_text("{not json " + MARKER)
        self.opencode.write_text("[" + MARKER)
        document = self.annotate("orchestrator")
        self.assertEqual(document["mcp"], [{"name": "context7", "source": "plugin", "kind": "remote", "enabled": True,
                                            "state": "on", "reason": "", "live": False, "agents": []},
                                           {"name": "gh_grep", "source": "plugin", "kind": "remote", "enabled": True,
                                            "state": "on", "reason": "", "live": False, "agents": []}])
        self.assertIsNone(document["agents"][0]["mcps"])
        self.assertNotIn(MARKER, json.dumps(document))

    def test_missing_files_are_not_an_error(self):
        document = self.annotate("orchestrator")
        self.assertEqual([row["name"] for row in document["mcp"]], ["context7", "gh_grep"])

    def test_the_pattern_is_the_one_definition(self):
        for good in ("a", "A-b_c.d", "x" * 64):
            self.assertTrue(names.valid("mcp", good), good)
        for bad in ("", "x" * 65, "a b", "a/b", "a\n", "é", "a:b", "*", "!x"):
            self.assertFalse(names.valid("mcp", bad), bad)


class Secrets(Scratch):
    """The planted-secret test: MARKER is in every field of every server that is not its name."""

    def settings(self):
        local = {"type": "local", "command": ["node", MARKER, "--token=" + MARKER], "args": [MARKER],
                 "environment": {MARKER + "_NAME": MARKER, "KEY": MARKER}, "env": {MARKER: MARKER},
                 "enabled": True, "timeout": 5000, "cwd": "/" + MARKER, "extra": {"deep": [MARKER]}}
        remote = {"type": "remote", "url": "https://" + MARKER + ".example/mcp?key=" + MARKER,
                  "headers": {"Authorization": "Bearer " + MARKER, MARKER: MARKER},
                  "oauth": {"clientId": MARKER, "clientSecret": MARKER, "scope": MARKER}, "enabled": True}
        odd = {"type": MARKER, "command": MARKER, "url": MARKER, "enabled": MARKER}
        opencode = {"mcp": {"plain-local": local, "plain.remote": remote, "odd_type": odd,
                            "has space " + MARKER: {"type": "local", "command": [MARKER]},
                            "slash/" + MARKER: remote, MARKER + "\n": local, "é" + MARKER: local,
                            "x" * 65: local, "": local, "ok_name": "a string, not an entry " + MARKER},
                    "theme": MARKER, "provider": {"p": {"options": {"apiKey": MARKER}}}}
        plugin = {"preset": "p", "disabled_mcps": [MARKER], "mcp": {"plugin-side": local}, "apiKey": MARKER,
                  "presets": {"p": {"orchestrator": {"model": "a/b", "prompt": MARKER, "mcps": ["*", "!" + MARKER, "!context7"]},
                                    "explorer": {"model": "a/b", "mcps": [MARKER, "plain-local", "has space " + MARKER,
                                                                         "slash/" + MARKER, "!" + MARKER],
                                                 "options": {"headers": {"X": MARKER}}},
                                    "oracle": {"model": "a/b", "mcps": ["!*"]}}},
                  "agents": {"oracle": {"options": {"token": MARKER}}}}
        return plugin, opencode

    def test_the_marker_reaches_nothing_the_view_gets(self):
        plugin, opencode = self.settings()
        self.write(plugin, opencode)
        document = self.annotate("orchestrator", "explorer", "oracle", "build")
        body = json.dumps(document)
        self.assertNotIn(MARKER, body)
        self.assertNotIn("PLANTED", body)
        shown = self.servers(document)
        self.assertEqual(sorted(shown), ["context7", "gh_grep", "odd_type", "ok_name", "plain-local", "plain.remote"])
        self.assertEqual(shown["odd_type"]["kind"], "")
        self.assertEqual(shown["odd_type"]["state"], "on")  # `enabled` is only read when it is exactly false
        self.assertEqual(shown["plain-local"]["kind"], "local")
        self.assertEqual(shown["plain.remote"]["kind"], "remote")
        for row in document["mcp"]:  # the keys are the contract's: nothing else can ride along
            self.assertEqual(sorted(row), ["agents", "enabled", "kind", "live", "name", "reason", "source", "state"])
            self.assertTrue(names.valid("mcp", row["name"]))
        for agent in document["agents"]:
            for item in agent["mcps"] or []:
                self.assertTrue(names.valid("mcp", item))
        self.assertEqual(self.mcps(document)["explorer"], ["plain-local"])
        self.assertNotIn("has space", body)

    def test_the_marker_reaches_no_log_and_no_error(self):
        plugin, opencode = self.settings()
        self.write(plugin, opencode)
        out, err = io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(err)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        with redirect_stdout(out), redirect_stderr(err):
            self.annotate("orchestrator")
            self.config.write_text("{" + MARKER)  # a broken plugin file: the error text must not quote it
            self.annotate("orchestrator")
        self.assertEqual(out.getvalue() + err.getvalue(), "")

    def test_the_marker_reaches_no_endpoint_reply(self):
        plugin, opencode = self.settings()
        self.write(plugin, opencode)
        for folder in ("graphs", "structure", "agents", "skills", "ledger", "commands"):
            (self.root / folder).mkdir()
        httpd = server.make_server(0, graph_dir=self.root / "graphs", cwd=self.root, session="root", web_dir=self.root,
                                   structure_dir=self.root / "structure", skills_config=self.config,
                                   skills_map=self.root / "SKILLS.md", skills_backups=self.root / "backups",
                                   skills_profiles=self.root / "task-profiles.md", agents_dir=self.root / "agents",
                                   skills_dir=self.root / "skills", opencode_dir=self.root, ledger_root=self.root / "ledger",
                                   commands_dir=self.root / "commands", sessions_db=self.root / "none.db")
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        replies = []
        for path in ("/api/snapshot", "/api/snapshot?demo=1", "/api/snapshot?task=nothing", "/api/mcp", "/api/config"):
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=20)
            connection.request("GET", path)
            reply = connection.getresponse()
            replies.append((path, reply.status, reply.read().decode("utf-8", "replace")))
            connection.close()
        live = json.loads(replies[0][2])
        self.assertEqual(replies[0][1], 200, replies[0][2][:300])
        self.assertEqual(sorted(row["name"] for row in live["mcp"]),
                         ["context7", "gh_grep", "odd_type", "ok_name", "plain-local", "plain.remote"])
        for path, status, body in replies:
            self.assertNotIn(MARKER, body, path)
            self.assertNotIn("PLANTED", body, path)

    def test_the_desktop_bridge_command_carries_the_same(self):
        """The Desktop app runs `python3 -P -m agentgraph.snapshot_json`: with HOME a scratch folder, its single line of
        output and its error output hold no marker."""
        plugin, opencode = self.settings()
        home = self.root / "home"
        config_dir = home / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        (config_dir / "oh-my-opencode-slim.json").write_text(json.dumps(plugin))
        (config_dir / "opencode.json").write_text(json.dumps(opencode))
        run = subprocess.run([sys.executable, "-P", "-m", "agentgraph.snapshot_json"], cwd=ROOT, capture_output=True, text=True,
                             env={**os.environ, "HOME": str(home), "PYTHONPATH": str(ROOT)}, timeout=60)
        self.assertNotIn(MARKER, run.stdout + run.stderr)
        document = json.loads(run.stdout)
        self.assertIn("plain-local", [row["name"] for row in document["mcp"]], run.stdout[:300] + run.stderr[:300])
        self.assertNotIn("PLANTED", run.stdout)


if __name__ == "__main__":
    unittest.main()
