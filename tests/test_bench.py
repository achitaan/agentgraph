"""agentgraph.bench: every test runs on scratch folders, a fake `opencode-cli` (a script that records how it was called
and prints the host's JSON events) and a fake database. No real model call; the real config and database are never named."""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from agentgraph import bench, osproc
from tests.shellskip import HAS_SH, python_command, sh_or
from tests.winskip import WINDOWS, needs_symlinks

# The check that prints what its environment holds. The Windows forms read both kinds of slash (the paths there are native, with backslashes).
if not WINDOWS:
    CHECK_ENV = 'echo "${HOME#*/temp/}|${OPENCODE_CONFIG_DIR-unset}|${P4_DB##*/}|${AGENTGRAPH_HOME-unset}|${MADE_UP_KEY-unset}"'
elif HAS_SH:
    CHECK_ENV = (r"""h=$(printf %s "$HOME" | tr -c 'A-Za-z0-9_.-' /); h=${h%/home}; d=$(printf %s "$P4_DB" | tr -c 'A-Za-z0-9_.-' /); """
                 r"""echo "${h##*/}/home|${OPENCODE_CONFIG_DIR-unset}|${d##*/}|${AGENTGRAPH_HOME-unset}|${MADE_UP_KEY-unset}""" + '"')
else:
    CHECK_ENV = python_command(
        "import os; e = os.environ.get; "
        "print(os.path.basename(os.path.dirname(e('HOME'))) + '/' + os.path.basename(e('HOME')) + '|' + e('OPENCODE_CONFIG_DIR', 'unset') + '|' "
        "+ os.path.basename(e('P4_DB')) + '|' + e('AGENTGRAPH_HOME', 'unset') + '|' + e('MADE_UP_KEY', 'unset'))")

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
        patch = unittest.mock.patch.object(bench, "TEMP_ROOT", self.root)  # the run folders go under the test's temp folder
        patch.start()
        self.addCleanup(patch.stop)

    def scratch(self, name="s1", **kw):
        return bench.scratch_config(self.root / name, live=self.live, temp_root=self.root, real_home=self.real_home, **kw)


class DefaultTempFolderTests(unittest.TestCase):
    def test_the_default_temp_folder_is_this_systems_not_a_fixed_path(self):
        self.assertEqual(bench.TEMP_ROOT, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(bench.TEMP_ROOT.is_dir())


class ScratchConfigTests(Base):
    def test_copy_rewrites_the_live_path_and_leaves_tilde_alone(self):
        info = self.scratch()
        config = Path(info["config"])
        self.assertEqual(config, self.root.resolve() / "s1" / "home" / ".config" / "opencode")
        text = (config / "AGENTS.md").read_text()
        self.assertIn("~/.config/opencode/AGENTS.md", text)             # the prompt text is unchanged: the scratch HOME resolves it
        self.assertIn(f"{config}/orchestration/COSTS.md", text)         # the absolute path points at the scratch copy
        self.assertNotIn(str(self.live), text)
        as_json = json.dumps(str(config))[1:-1]  # the path as a JSON string holds it (Windows backslashes are doubled)
        self.assertIn(as_json, (config / "opencode.json").read_text())
        self.assertIn(as_json, (config / "cli.json").read_text())
        self.assertNotIn(str(self.live), (config / "orchestration" / "bin" / "agentgraph").read_text())
        if not WINDOWS:  # Windows has no POSIX modes
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


class PlainConfigTests(Base):
    def setUp(self):
        super().setUp()
        (self.live / "opencode.json").write_text(json.dumps({
            "$schema": "https://example.invalid/schema.json", "model": "openai/gpt-6-sol", "small_model": "openai/gpt-6-luna",
            "provider": {"acme": {"options": {"baseURL": "https://proxy.example.invalid/v1"}, "models": {"m1": {}}}},
            "plugin": ["some-plugin"], "mcp": {"srv": {"command": ["x"]}}, "agent": {"explore": {"model": "openai/y"}},
            "command": {"c": {"template": "t"}}, "instructions": ["AGENTS.md"], "permission": {"bash": "allow"}}))
        (self.live / "skills" / "x" / "bench" / "scenarios" / "t1" / "hidden").mkdir(parents=True)
        (self.live / "skills" / "x" / "bench" / "scenarios" / "t1" / "hidden" / "h.py").write_text("HIDDEN-CHECK\n")
        (self.live / "skills" / "x" / "SKILL.md").write_text("skill text\n")
        (self.live / "orchestration" / "deep" / "bench" / "scenarios").mkdir(parents=True)
        (self.live / "orchestration" / "deep" / "bench" / "scenarios" / "s.json").write_text("HIDDEN-CHECK\n")
        (self.live / "orchestration" / "deep" / "keep.md").write_text("kept\n")

    def plain(self, name="p1", **kw):
        return bench.plain_config(self.root / name, live=self.live, temp_root=self.root, real_home=self.real_home, **kw)

    def test_plain_copy_has_only_the_allow_listed_settings(self):
        info = self.plain()
        config = Path(info["config"])
        self.assertEqual([p.name for p in config.iterdir()], ["opencode.json"])   # no agents, skills, plugins, AGENTS.md, commands
        data = json.loads((config / "opencode.json").read_text())
        self.assertEqual(sorted(data), sorted(("$schema", "model", "small_model", "provider")))
        self.assertTrue(set(data) <= set(bench.PLAIN_KEYS))
        for gone in ("plugin", "mcp", "agent", "command", "instructions", "permission"):
            self.assertNotIn(gone, data)
        self.assertEqual((data["model"], data["small_model"]), (MODEL, MODEL))
        self.assertEqual(data["provider"]["acme"]["models"], {"m1": {}})
        self.assertNotIn(str(self.live), json.dumps(data))
        if not WINDOWS:
            self.assertEqual(stat.S_IMODE(os.stat(info["dir"]).st_mode), 0o700)
        for name in ("AGENTS.md", "agents", "commands", "skills", "oh-my-opencode-slim", "orchestration", "node_modules", "cli.json"):
            self.assertFalse((config / name).exists(), name)

    def test_plain_copy_is_marked_and_the_marker_names_the_agent(self):
        info = self.plain()
        self.assertEqual(json.loads((Path(info["dir"]) / bench.SETUP_FILE).read_text()), {"kind": "plain", "agent": "build"})
        scratch = bench.open_scratch(info["dir"], live=self.live)
        self.assertEqual((scratch["kind"], scratch["agent"], scratch["pin_model"]), ("plain", "build", MODEL))
        toolkit = self.scratch("tk")
        self.assertEqual(json.loads((Path(toolkit["dir"]) / bench.SETUP_FILE).read_text()), {"kind": "toolkit", "agent": None})
        scratch = bench.open_scratch(toolkit["dir"], live=self.live)
        self.assertEqual((scratch["kind"], scratch["agent"]), ("toolkit", None))
        (Path(toolkit["dir"]) / bench.SETUP_FILE).unlink()                          # a copy made before the marker existed
        self.assertEqual(bench.open_scratch(toolkit["dir"], live=self.live)["kind"], "toolkit")

    def test_plain_copy_keeps_the_pin_option_and_the_same_refusals(self):
        keep = json.loads((Path(self.plain("k", pin_model=None)["config"]) / "opencode.json").read_text())
        self.assertEqual(keep["model"], "openai/gpt-6-sol")
        other = json.loads((Path(self.plain("o", pin_model="openai/gpt-6-luna")["config"]) / "opencode.json").read_text())
        self.assertEqual((other["model"], other["small_model"]), ("openai/gpt-6-luna",) * 2)
        for target in (self.live / "inside", "relative/dir", self.base / "outside-temp-root", self.root):
            with self.subTest(target=str(target)), self.assertRaises(bench.BenchError):
                bench.plain_config(target, live=self.live, temp_root=self.root, real_home=self.real_home)
        with self.assertRaises(bench.BenchError):
            self.plain("bad", pin_model="not a model")
        self.plain("taken")
        with self.assertRaises(bench.BenchError):
            self.plain("taken")

    def test_plain_copy_leaves_the_live_files_alone(self):
        before = bench.live_fingerprint(self.live)
        self.plain()
        self.assertEqual(bench.live_fingerprint(self.live), before)

    def test_plain_copy_refuses_settings_that_refer_to_a_file_and_names_only_the_key(self):
        cases = {"file-ref": {"provider": {"acme": {"options": {"apiKey": "{file:/made/up/secret-VALUE-1}"}}}},
                 "absolute": {"provider": {"acme": {"options": {"baseURL": f"{self.live}/proxy-VALUE-2"}}}},
                 "tilde": {"small_model": "~/.config/opencode/VALUE-3"},
                 "in a list": {"enabled_providers": ["ok", "{file:x-VALUE-4}"]}}
        for name, settings in cases.items():
            with self.subTest(name=name):
                (self.live / "opencode.json").write_text(json.dumps({"model": "openai/m", **settings}))
                with self.assertRaises(bench.BenchError) as caught:
                    self.plain(f"r-{name.replace(' ', '-')}")
                message = str(caught.exception)
                self.assertNotIn("VALUE", message)
                self.assertNotIn(str(self.live), message)
                self.assertIn(next(iter(settings)), message)
                self.assertFalse((self.root / f"r-{name.replace(' ', '-')}").exists())   # nothing was made
        self.assertIn("provider.acme.options.apiKey", self.refusal({"provider": {"acme": {"options": {"apiKey": "{file:x}"}}}}))
        self.assertIn("enabled_providers[1]", self.refusal({"enabled_providers": ["ok", "{file:x}"]}))
        (self.live / "opencode.json").write_text(json.dumps({"agent": {"x": {"prompt": "{file:ignored}"}}, "model": "a/b"}))
        self.plain("not-kept")      # a setting the plain copy drops anyway is not a reason to refuse

    def refusal(self, settings):
        (self.live / "opencode.json").write_text(json.dumps(settings))
        with self.assertRaises(bench.BenchError) as caught:
            self.plain("refused-" + str(abs(hash(json.dumps(settings)))))
        return str(caught.exception)

    def test_no_copy_holds_the_hidden_checks(self):
        for info in (self.scratch("tk"), self.plain("pl")):
            files = [p for p in Path(info["dir"]).rglob("*")]
            self.assertFalse([p for p in files if "scenarios" in p.parts], info["dir"])
            self.assertNotIn("HIDDEN-CHECK", "".join(p.read_text() for p in files if p.is_file() and not p.is_symlink()))
        config = Path(self.scratch("tk2")["config"])
        self.assertTrue((config / "skills" / "x" / "SKILL.md").is_file())             # the rest of the folder is still copied
        self.assertTrue((config / "orchestration" / "deep" / "keep.md").is_file())


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
        self.assertEqual(Path(env["AGENTGRAPH_HOME"]).parts[-3:], ("opencode", "orchestration", "memory"))
        self.assertEqual((env["OPENCODE_CONFIG_CONTENT"], env["KEEP"]), ("{}", "1"))
        self.assertNotIn("OPENCODE_CONFIG_DIR", env)
        self.assertNotIn("OPENCODE_CONFIG", env)

    def test_run_env_drops_variables_that_point_into_the_repository_or_the_real_config_folder(self):
        scratch = bench.open_scratch(self.scratch()["dir"], live=self.live)
        env = bench.run_env(scratch, Path("/w"), {
            "OLDPWD": f"{bench.HERE}/somewhere", "PWD": str(self.live / "orchestration"), "TOOL_DIR": f"{self.live}/bin",
            "PATH": os.pathsep.join(["/usr/bin", f"{bench.HERE}/bin", f"{self.live}/orchestration/bin", "/bin"]), "OPENCODE_CONFIG_CONTENT": f'{{"x": "{self.live}"}}',
            "KEEP": "/usr/local/x", "TERM": "xterm"}, live=self.live)
        for gone in ("OLDPWD", "TOOL_DIR"):
            self.assertNotIn(gone, env)
        self.assertEqual(env["PATH"], os.pathsep.join(["/usr/bin", "/bin"]))                    # a PATH loses only the entries that point there
        self.assertEqual(env["PWD"], str(Path("/w")))                                 # set again for the run
        self.assertEqual((env["KEEP"], env["TERM"]), ("/usr/local/x", "xterm"))
        self.assertIn("OPENCODE_CONFIG_CONTENT", env)

    def test_run_env_for_a_plain_copy_keeps_only_the_plain_keys_of_the_config_content(self):
        plain = bench.open_scratch(self.plain_copy()["dir"], live=self.live)
        content = json.dumps({"provider": {"acme": {"models": {}}}, "model": "a/b", "plugin": ["p"], "mcp": {"s": {}},
                              "agent": {"x": {}}, "instructions": ["i"], "provider_table": {"acme": {}}})
        env = bench.run_env(plain, Path("/w"), {"OPENCODE_CONFIG_CONTENT": content}, live=self.live)
        self.assertEqual(sorted(json.loads(env["OPENCODE_CONFIG_CONTENT"])), ["model", "provider", "provider_table"])
        for bad in ("not json", "[1, 2]", ""):
            with self.subTest(bad=bad):
                self.assertNotIn("OPENCODE_CONFIG_CONTENT", bench.run_env(plain, Path("/w"), {"OPENCODE_CONFIG_CONTENT": bad}, live=self.live))
        toolkit = bench.open_scratch(self.scratch("tk-env")["dir"], live=self.live)   # a toolkit copy keeps it whole
        self.assertEqual(bench.run_env(toolkit, Path("/w"), {"OPENCODE_CONFIG_CONTENT": content}, live=self.live)["OPENCODE_CONFIG_CONTENT"], content)

    def plain_copy(self):
        return bench.plain_config(self.root / "pe", live=self.live, temp_root=self.root, real_home=self.real_home)

    def test_mask_hides_the_home_path_and_key_like_text_and_caps_the_length(self):
        home = str(Path.home())
        text = f"failed in {home}/proj with key sk{'a1B2' * 7} and ses_{'x9' * 13} ok"
        masked = bench.mask(text)
        self.assertNotIn(home, masked)
        self.assertIn("~/proj", masked)
        self.assertEqual(masked.count("[hidden]"), 2)
        self.assertNotIn("a1B2a1B2", masked)
        self.assertEqual(bench.mask("plain words and 1234567890123456789012 (22)"), "plain words and 1234567890123456789012 (22)")
        self.assertEqual(bench.mask("x" * 24), "[hidden]")
        self.assertEqual(len(bench.mask("word " * 100)), 160)
        self.assertEqual(bench.mask(None), "")
        self.assertEqual(bench.mask(masked), masked)                       # masking twice changes nothing
        self.assertEqual(bench.mask("a/b", home=Path("/made/up/home")), "a/b")

    def test_rows_are_masked_before_they_are_written(self):
        row = {"label": "m", "scenario": "s", "error": f"boom {Path.home()}/x token{'Z' * 30}",
               "check": {"passed": False, "detail": f"{Path.home()}/f " + "K" * 40}}
        path = bench.append_row(row, self.base / "res")
        text = path.read_text()
        self.assertNotIn(str(Path.home()), text)
        self.assertNotIn("K" * 24, text)
        self.assertNotIn("Z" * 24, text)
        saved = json.loads(text)
        self.assertEqual((saved["check"]["passed"], saved["error"].startswith("boom ~/x")), (False, True))
        self.assertIn("K" * 40, row["check"]["detail"])                    # the caller's row is not changed


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
attempts = {}
if step.get("scan"):  # every folder under the given one that holds a file named like a hidden one
    attempts["scan"] = sorted(d for d, _, names in os.walk(step["scan"]) if "expect.txt" in names)
if step.get("try_read"):
    try:
        open(step["try_read"]).read()
        attempts["read"] = True
    except OSError:
        attempts["read"] = False
if step.get("try_write"):
    try:
        open(step["try_write"], "w").write("x")
        attempts["write"] = True
    except OSError:
        attempts["write"] = False
if attempts:
    with open(os.path.join(here, "attempts.jsonl"), "a") as f:
        f.write(json.dumps(attempts) + "\n")
if step.get("sleep"):
    time.sleep(step["sleep"])
sid = args[args.index("--session") + 1] if "--session" in args else "ses_root"
print(json.dumps({"type": "step_start", "sessionID": sid, "part": {}}))
if step.get("error"):
    print(json.dumps({"type": "error", "sessionID": sid, "error": {"data": {"message": step["error"]}}}))
sys.exit(step.get("exit", 0))
'''


def install_fake_cli(folder: Path) -> Path:
    """The fake `opencode-cli` in `folder` and the path to run it: the script itself (a shebang) on POSIX; on Windows, which has no
    shebang, a .cmd shim that starts the script with this interpreter (the stand-in for opencode.cmd)."""
    if WINDOWS:
        script = folder / "opencode-cli.py"
        script.write_text(FAKE, encoding="utf-8")
        shim = folder / "opencode-cli.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return shim
    cli = folder / "opencode-cli"
    cli.write_text(FAKE)
    cli.chmod(0o755)
    return cli


def check_env(**extra) -> dict:
    """A stripped-down environment for a check: PATH, what Windows needs to start programs at all, and `extra`."""
    return {**{name: os.environ[name] for name in osproc.base_env_names() if name in os.environ}, "PATH": os.environ["PATH"], **extra}


def same_answer_check(word: str) -> str:
    """The scenarios' check: passes when answer.txt equals the hidden expect.txt and then prints `word`."""
    return sh_or(f"diff -q answer.txt expect.txt >/dev/null && echo {word}",
                 f"import sys; same = open('answer.txt').read() == open('expect.txt').read(); same and print('{word}'); raise SystemExit(0 if same else 1)")


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
        self.cli = install_fake_cli(self.cli_dir)
        self.results = self.base / "results"
        self.scenarios = self.base / "scenarios"
        s = self.scenarios / "t1"
        (s / "fixture").mkdir(parents=True)
        (s / "hidden").mkdir()
        (s / "fixture" / "answer.txt").write_text("wrong\n")
        (s / "hidden" / "expect.txt").write_text("right\n")
        (s / "scenario.json").write_text(json.dumps({
            "agent": "orchestrator", "prompt": "fix the answer", "timeout": 20,
            "check": same_answer_check("matches")}))
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

    def attempts(self):
        path = self.cli_dir / "attempts.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_t1(self, **kw):
        kw.setdefault("sandbox", False)
        return bench.run_scenarios(["t1"], label="lab", config=self.scratch_dir, executable=str(self.cli), db_path=self.db,
                                   results=self.results, scenarios_root=self.scenarios, live=self.live, **kw)

    def test_row_assembly_from_the_fake_cli_and_database(self):
        self.plan([{"write": {"answer.txt": "right\n"}}])
        rows = self.run_t1(reps=1, sandbox=True)                               # the default sandbox, where there is one
        row = rows[0]
        call = self.calls()[0]
        self.assertEqual(call["message"], "fix the answer")
        self.assertEqual(call["home"], str(Path(self.scratch_dir) / "home"))
        self.assertEqual(call["xdg"], str(Path(self.scratch_dir) / "home" / ".config"))
        work = Path(call["cwd"])
        self.assertEqual((work.parent, work.name.startswith(bench.WORK_PREFIX)), (self.root, True))   # its own folder, not under the scratch folder
        self.assertFalse((work.parent / "work").exists())
        self.assertFalse(work.exists())                                        # deleted once the row was written
        self.assertEqual(row["sandbox"], bench.sandbox_available())
        self.assertNotIn("work", row)
        self.assertNotIn("models_used", row)                                   # the fake database holds no model ids
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
                                   results=self.results, scenarios_root=self.scenarios, live=self.live, sandbox=False)
        self.assertEqual((rows[0]["sessions"], rows[0]["cost"]), (0, 0.0))
        self.assertIn("not found in the database", rows[0]["error"])

    def test_the_check_runs_in_the_scratch_environment_with_only_the_database_path_passed_through(self):
        t2 = self.scenarios / "t2"
        t2.mkdir()
        (t2 / "scenario.json").write_text(json.dumps({
            "agent": "orchestrator", "prompt": "x", "timeout": 20,
            "check": CHECK_ENV}))
        self.plan([{}])
        with unittest.mock.patch.dict(os.environ, {"OPENCODE_CONFIG_DIR": "/real/config", "MADE_UP_KEY": "made-up-value"}):
            rows = bench.run_scenarios(["t2"], label="lab", config=self.scratch_dir, executable=str(self.cli), db_path=self.db,
                                       results=self.results, scenarios_root=self.scenarios, live=self.live, sandbox=False)
        name = Path(self.scratch_dir).name
        self.assertEqual(rows[0]["check"]["detail"], f"{name}/home|unset|opencode.db|unset|unset")      # a minimal environment

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
                                results=self.results, live=self.live, sandbox=False)
        call = self.calls()[0]
        self.assertEqual(call["message"], bench.PROBE_PROMPT)
        self.assertEqual(call["args"][call["args"].index("--agent") + 1], "explorer")
        self.assertNotIn("--auto", call["args"])
        self.assertEqual((rows[0]["scenario"], rows[0]["probe_agent"]), ("probe", "explorer"))
        self.assertEqual(rows[0]["first_turn_tokens"], {"explorer": 15_012})
        self.assertIsNone(rows[0]["check"]["passed"])

    def work_folders(self):
        return sorted(p for p in self.root.iterdir() if p.name.startswith(bench.WORK_PREFIX))

    def test_no_folder_that_exists_during_a_run_holds_a_hidden_file(self):
        self.plan([{"write": {"answer.txt": "right\n"}, "scan": str(self.root)}])
        rows = self.run_t1(reps=3)
        self.assertEqual([r["check"]["passed"] for r in rows], [True] * 3)      # the check still found hidden/ in its copy
        self.assertEqual(self.attempts(), [{"scan": []}] * 3)                   # a run saw no hidden file anywhere under the temp folder
        self.assertEqual(self.work_folders(), [])                               # every folder is gone once its row is written
        self.assertFalse((Path(self.scratch_dir) / "work").exists())
        self.assertEqual([p.name for p in self.root.iterdir()], [Path(self.scratch_dir).name])

    def test_the_work_folders_of_two_runs_are_different_new_folders(self):
        self.plan([{}])
        self.run_t1(reps=2, keep_work=True)
        first, second = (c["cwd"] for c in self.calls())
        self.assertNotEqual(first, second)
        for folder in (first, second):
            self.assertEqual(Path(folder).parent, self.root)

    def test_keep_work_keeps_the_models_folder_without_hidden_files(self):
        self.plan([{"write": {"answer.txt": "right\n", "notes/x.txt": "kept\n"}}])
        row = self.run_t1(keep_work=True)[0]
        kept = Path(row["work"])
        self.assertEqual(self.work_folders(), [kept])                           # only the model's folder: no aside copy, no check copy
        self.assertEqual(sorted(p.name for p in kept.iterdir()), [".git", "answer.txt", "notes"])
        self.assertFalse(list(kept.rglob("expect.txt")))
        self.assertEqual((kept / "answer.txt").read_text(), "right\n")
        self.assertEqual(json.loads((self.results / "lab.jsonl").read_text())["work"], str(kept))

    @needs_symlinks
    def test_run_check_leaves_the_folder_as_it_was_and_does_not_follow_links_out(self):
        scenario = bench.load_scenario("t1", self.scenarios)
        work = self.base / "w"
        bench.prepare_work(scenario["dir"], work)
        outside = self.base / "outside.txt"
        outside.write_text("untouched\n")
        (work / "expect.txt").symlink_to(outside)                                # a link where a hidden file will land
        (work / "answer.txt").write_text("right\n")
        result = bench.run_check(scenario, work, check_env(HOME=str(self.base)), self.db)
        self.assertEqual(result, {"passed": True, "detail": "matches"})
        self.assertEqual(outside.read_text(), "untouched\n")
        self.assertTrue((work / "expect.txt").is_symlink())
        self.assertEqual(sorted(p.name for p in work.iterdir()), [".git", "answer.txt", "expect.txt"])
        self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith(bench.WORK_PREFIX)], [])
        (work / "answer.txt").write_text("wrong\n")
        self.assertFalse(bench.run_check(scenario, work, check_env())["passed"])

    def test_a_scratch_folder_with_work_folders_of_the_old_layout_is_refused(self):
        old = Path(self.scratch_dir) / "work" / "lab-t1-r1-123"
        old.mkdir(parents=True)
        (old / "expect.txt").write_text("right\n")
        self.plan([{}])
        with self.assertRaisesRegex(bench.BenchError, "older layout.*delete that folder"):
            self.run_t1()
        with self.assertRaisesRegex(bench.BenchError, "older layout"):
            bench.run_probes(["explorer"], label="pl", config=self.scratch_dir, executable=str(self.cli), db_path=self.db,
                             results=self.results, live=self.live, sandbox=False)
        self.assertEqual(self.calls() if (self.cli_dir / "calls.jsonl").exists() else [], [])
        shutil.rmtree(Path(self.scratch_dir) / "work")
        self.run_t1()                                                           # after the delete it starts

    def test_the_sandbox_profile_denies_reading_and_writing_the_named_folders_only(self):
        profile = bench.sandbox_profile([self.base / "r one"], [self.base / 'w"two'])
        self.assertTrue(profile.startswith("(version 1)\n(allow default)\n"))
        self.assertIn('(deny file-read* (subpath "%s"))' % str(self.base / "r one").replace("\\", "\\\\"), profile)
        self.assertIn('(deny file-write* (subpath "%s"))' % str(self.base / 'w"two').replace("\\", "\\\\").replace('"', '\\"'), profile)
        self.assertEqual(profile.count("(deny file-write*"), len({str(self.base / 'w"two'), os.path.realpath(self.base / 'w"two')}))
        reads, writes = bench.protected_paths(self.live)
        self.assertIn(bench.HERE / "bench" / "scenarios", reads)
        self.assertIn(self.live / "orchestration" / "bench" / "scenarios", reads)
        self.assertIn(bench.HERE, writes)
        self.assertIn(self.live, writes)
        for path in reads:
            self.assertTrue(path.name in ("scenarios", ".git") or path.name.endswith(".git"), path)
        self.assertEqual(bench.sandboxed(["x"], False), ["x"])

    @unittest.skipUnless(bench.sandbox_available(), "needs /usr/bin/sandbox-exec")
    def test_the_sandbox_blocks_reading_a_hidden_file_and_writing_into_a_protected_folder(self):
        secret = self.base / "checkout" / "bench" / "scenarios" / "t9" / "hidden" / "expect.txt"
        secret.parent.mkdir(parents=True)
        secret.write_text("made-up hidden check\n")
        denied = self.base / "protected-write"
        denied.mkdir()
        target = denied / "planted.txt"
        step = {"try_read": str(secret), "try_write": str(target)}
        self.plan([step])
        with unittest.mock.patch.object(bench, "protected_paths", return_value=([secret.parent.parent.parent], [denied])):
            on = self.run_t1(sandbox=True)[0]
            self.assertEqual(self.attempts(), [{"read": False, "write": False}])
            self.assertFalse(target.exists())
            off = self.run_t1(sandbox=False)[0]
        self.assertEqual(self.attempts()[1], {"read": True, "write": True})     # the same attempts work without the sandbox
        self.assertTrue(target.exists())
        self.assertEqual((on["sandbox"], off["sandbox"]), (True, False))
        self.assertEqual(on["exit_code"], 0)                                    # the host itself ran normally under the profile

    def test_the_flags_reach_the_run_functions(self):
        for command, name in ((["run", "t1", "--label", "x", "--config", "/c"], "run_scenarios"),
                              (["probe", "explorer", "--label", "x", "--config", "/c"], "run_probes")):
            for flags, want in (([], (True, False)), (["--no-sandbox", "--keep-work"], (False, True))):
                with self.subTest(command=command[0], flags=flags), unittest.mock.patch.object(bench, name, return_value=[]) as fake:
                    self.assertEqual(bench.main(command + flags), 0)
                    self.assertEqual((fake.call_args.kwargs["sandbox"], fake.call_args.kwargs["keep_work"]), want)
        result = {"rows": [], "spent": 0.0, "stopped": None, "reason": None}
        with unittest.mock.patch.object(bench, "run_suite", return_value=result) as fake:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bench.main(["suite", "pair", "--setup", "A=/a", "--setup", "B=/b", "--append", "--no-sandbox"]), 0)
            self.assertEqual((fake.call_args.kwargs["append"], fake.call_args.kwargs["sandbox"]), (True, False))
        for reason, code in (("budget", 3), ("unrecorded", 4)):
            with unittest.mock.patch.object(bench, "run_suite", return_value={**result, "stopped": "why", "reason": reason}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bench.main(["suite", "pair", "--setup", "A=/a", "--setup", "B=/b"]), code)

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


class SuiteTests(Base):
    """`bench suite`: the fake executable answers every call with the same root session, so every run costs the same."""

    def setUp(self):
        super().setUp()
        self.tk = self.scratch("tk")["dir"]
        self.plain = bench.plain_config(self.root / "pl", live=self.live, temp_root=self.root, real_home=self.real_home)["dir"]
        self.cli_dir = self.base / "cli"
        self.cli_dir.mkdir()
        self.cli = install_fake_cli(self.cli_dir)
        (self.cli_dir / "plan.json").write_text(json.dumps([{"write": {"answer.txt": "right\n"}}]))
        self.results, self.scenarios, self.suites = self.base / "results", self.base / "scenarios", self.base / "suites"
        for name in ("t1", "t2"):
            s = self.scenarios / name
            (s / "fixture").mkdir(parents=True)
            (s / "hidden").mkdir()
            (s / "fixture" / "answer.txt").write_text("wrong\n")
            (s / "hidden" / "expect.txt").write_text("right\n")
            (s / "scenario.json").write_text(json.dumps({"agent": "orchestrator", "prompt": f"fix {name}", "timeout": 20,
                                                         "check": same_answer_check("ok")}))
        self.suites.mkdir()
        (self.suites / "pair.json").write_text(json.dumps({"name": "pair", "note": "two", "scenarios": ["t1", "t2"]}))
        self.db = self.base / "opencode.db"
        make_db(self.db, [("ses_root", None, "orchestrator", "x", 0.4, 1)],
                [("ses_root", "user", "{}"), ("ses_root", "assistant", message(10, 100, 0, 0, 20_000, 0.4))])

    def suite(self, setups=None, **kw):
        kw.setdefault("sandbox", False)
        return bench.run_suite("pair", setups=setups or [("A", self.tk), ("B", self.plain)], executable=str(self.cli), db_path=self.db,
                               results=self.results, scenarios_root=self.scenarios, suites_root=self.suites, live=self.live, **kw)

    def calls(self):
        path = self.cli_dir / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def titles(self):
        return [c["args"][c["args"].index("--title") + 1].removeprefix("agentgraph-bench: ") for c in self.calls()]

    def test_runs_are_interleaved_and_the_first_setup_alternates(self):
        result = self.suite(reps=2)
        self.assertEqual(self.titles(), ["t1 A r1", "t1 B r1", "t1 B r2", "t1 A r2", "t2 A r1", "t2 B r1", "t2 B r2", "t2 A r2"])
        self.assertEqual([(r["label"], r["scenario"], r["rep"]) for r in result["rows"]],
                         [(t.split()[1], t.split()[0], int(t.split()[2][1:])) for t in self.titles()])
        self.assertIsNone(result["stopped"])
        self.assertAlmostEqual(result["spent"], 3.2)

    def test_three_setups_rotate(self):
        third = bench.scratch_config(self.root / "tk3", live=self.live, temp_root=self.root, real_home=self.real_home)["dir"]
        self.suite([("A", self.tk), ("B", self.plain), ("C", third)])
        self.assertEqual(self.titles()[:6], ["t1 A r1", "t1 B r1", "t1 C r1", "t2 B r1", "t2 C r1", "t2 A r1"])

    def test_rows_carry_the_suite_and_go_through_the_row_writer_and_report(self):
        self.suite(reps=2)
        rows = [json.loads(line) for line in (self.results / "A.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 4)
        self.assertEqual({r["suite"] for r in rows}, {"pair"})
        self.assertEqual({r["setup"] for r in rows}, {"toolkit"})
        text = bench.report("A", "B", self.results)
        self.assertIn("== t1   reps 2 / 2", text)
        self.assertIn("== t2   reps 2 / 2", text)

    def test_the_markers_agent_replaces_the_scenarios_agent(self):
        self.suite()
        agents = {c["args"][c["args"].index("--title") + 1].split()[-2]: c["args"][c["args"].index("--agent") + 1] for c in self.calls()}
        self.assertEqual(agents, {"A": "orchestrator", "B": "build"})
        rows = [json.loads(line) for line in (self.results / "B.jsonl").read_text().splitlines()]
        self.assertEqual({(r["agent"], r["setup"]) for r in rows}, {("build", "plain")})
        self.assertEqual(self.calls()[1]["home"], str(Path(self.plain) / "home"))      # each run in its own scratch HOME

    def test_one_total_budget_stops_before_the_next_run_and_says_so(self):
        out = io.StringIO()
        result = self.suite(budget=0.7, reps=2, out=out)
        self.assertEqual(self.titles(), ["t1 A r1", "t1 B r1"])                          # 0.4 spent, then 0.8: the third run does not start
        self.assertIn("budget", result["stopped"])
        self.assertIn("spent $0.80 of the $0.70 budget", out.getvalue())
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(len((self.results / "A.jsonl").read_text().splitlines()) + len((self.results / "B.jsonl").read_text().splitlines()), 2)

    def test_a_bad_request_fails_before_anything_runs(self):
        (self.suites / "bad.json").write_text(json.dumps({"name": "bad", "scenarios": ["t1", "nope"]}))
        (self.suites / "empty.json").write_text(json.dumps({"name": "empty", "scenarios": []}))
        cases = [({"name": "bad"}, "no scenario"), ({"name": "missing"}, "no suite"), ({"name": "empty"}, "list of scenario ids"),
                 ({"name": "../x"}, "bad suite name"),
                 ({"setups": [("A", self.tk), ("B", self.base / "no-such-folder")]}, "not a scratch config"),
                 ({"setups": [("A", self.tk)]}, "two or more"), ({"setups": [("A", self.tk), ("A", self.plain)]}, "different labels"),
                 ({"setups": [("A", self.tk), ("../B", self.plain)]}, "--label"), ({"reps": 0}, "--reps"),
                 ({"budget": 0}, "--budget"), ({"interrupt_after": 0}, "--interrupt-after")]
        for kw, why in cases:
            with self.subTest(why=why):
                kw = {"name": "pair", **kw}
                setups = kw.pop("setups", [("A", self.tk), ("B", self.plain)])
                with self.assertRaisesRegex(bench.BenchError, why):
                    bench.run_suite(kw.pop("name"), setups=setups, executable=str(self.cli), db_path=self.db, results=self.results,
                                    scenarios_root=self.scenarios, suites_root=self.suites, live=self.live, **kw)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.results.exists())

    def test_setups_that_pin_different_models_are_refused(self):
        other = bench.scratch_config(self.root / "other", pin_model="openai/gpt-6-luna", live=self.live, temp_root=self.root,
                                     real_home=self.real_home)["dir"]
        with self.assertRaisesRegex(bench.BenchError, "pin different models"):
            self.suite([("A", self.tk), ("B", other)])
        self.assertEqual(self.calls(), [])

    def test_the_budget_is_checked_between_complete_rounds(self):
        third = bench.scratch_config(self.root / "tk3", live=self.live, temp_root=self.root, real_home=self.real_home)["dir"]
        result = self.suite([("A", self.tk), ("B", self.plain), ("C", third)], budget=0.5, reps=2)
        self.assertEqual(self.titles(), ["t1 A r1", "t1 B r1", "t1 C r1"])      # 0.4 after the first run is under 0.5; the round finishes
        self.assertEqual(result["reason"], "budget")
        self.assertIn("stopped before t1 r2", result["stopped"])
        self.assertEqual(sorted(r["label"] for r in result["rows"]), ["A", "B", "C"])

    def test_two_runs_in_a_row_without_a_session_stop_the_suite(self):
        make_db(self.base / "empty.db", [], [])
        out = io.StringIO()
        result = bench.run_suite(
            "pair", setups=[("A", self.tk), ("B", self.plain)], executable=str(self.cli), db_path=self.base / "empty.db",
            results=self.results, scenarios_root=self.scenarios, suites_root=self.suites, live=self.live, sandbox=False, reps=2, out=out)
        self.assertEqual(result["reason"], "unrecorded")
        self.assertEqual(len(result["rows"]), 2)
        self.assertIn("runs are not being recorded", result["stopped"])
        self.assertIn("cost cannot be tracked", out.getvalue())

    def test_a_suite_refuses_labels_that_already_have_rows_unless_append_is_given(self):
        self.suite()
        with self.assertRaisesRegex(bench.BenchError, "--append"):
            self.suite()
        self.assertEqual(len(self.calls()), 4)                                  # the refused call ran nothing
        self.suite(append=True, reps=2)
        titles = self.titles()[4:]
        self.assertEqual(sorted(t.split()[2] for t in titles), ["r2", "r2", "r2", "r2", "r3", "r3", "r3", "r3"])
        reps = [json.loads(line)["rep"] for line in (self.results / "A.jsonl").read_text().splitlines()]
        self.assertEqual(sorted(reps), [1, 1, 2, 2, 3, 3])
        other = bench.scratch_config(self.root / "tk4", live=self.live, temp_root=self.root, real_home=self.real_home)["dir"]
        self.suite([("A2", other), ("B2", self.plain)])                          # new labels: no flag needed

    def test_rows_list_the_models_the_sessions_used_when_the_database_has_them(self):
        turn = json.dumps({"cost": 0.1, "model": {"providerID": "anthropic", "id": "claude-test-1"},
                           "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}}})
        other = json.dumps({"cost": 0.1, "model": {"id": "bare-model"},
                            "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}}})
        make_db(self.base / "m.db", [("ses_root", None, "orchestrator", "x", 0.2, 1), ("ses_w", "ses_root", "explorer", "w", 0.1, 2)],
                [("ses_root", "user", "{}"), ("ses_root", "assistant", turn), ("ses_root", "assistant", turn),
                 ("ses_w", "user", "{}"), ("ses_w", "assistant", other)])
        tree = bench.read_tree(self.base / "m.db", "ses_root")
        self.assertEqual(tree["models_used"], ["anthropic/claude-test-1", "bare-model"])
        self.assertNotIn("models_used", bench.read_tree(self.db, "ses_root"))   # turns without a model id: the key is left out

    def test_the_shipped_suites_name_scenarios_that_exist(self):
        self.assertEqual(bench.load_suite("basic")["scenarios"], ["s1-simple", "s2-parallel", "s3-workflow"])
        for path in sorted(bench.SUITES.glob("*.json")):
            for name in bench.load_suite(path.stem)["scenarios"]:
                bench.load_scenario(name)

    def test_setup_arguments_are_parsed(self):
        self.assertEqual(bench.parse_setups(["A=/x/y", "B=/z=w"]), [("A", "/x/y"), ("B", "/z=w")])
        for bad in ("A", "A="):
            with self.assertRaises(bench.BenchError):
                bench.parse_setups([bad])


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
