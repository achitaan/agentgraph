"""Exercise roster with disposable copies; never edit the installed files."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentgraph import roster, skillset
from agentgraph.snapshot_json import skills_document


SCRATCH = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode/roster")
ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = [entry["name"] for entry in skills_document()]
SKILLS = [name for name in ("code-assurance", "engineering-delivery") if name in CATALOGUE] or CATALOGUE[:2]
FIRST = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (skillset.CONFIG, skillset.MAP)}


def tearDownModule():
    for path, digest in FIRST.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"{path} changed during the tests"


class RosterTest(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / skillset.CONFIG.name
        self.map = self.root / "SKILLS.md"
        self.backups = self.root / "backups"
        shutil.copyfile(skillset.CONFIG, self.config)
        shutil.copyfile(skillset.MAP, self.map)
        self.paths = dict(config=self.config, map_path=self.map, backups=self.backups)
        self.original = self.files()

    def files(self):
        return self.config.read_bytes(), self.map.read_bytes()

    def data(self):
        return json.loads(self.config.read_bytes())

    def agent(self, name="scout", **kwargs):
        args = dict(model="anthropic/claude-sonnet-5-5", variant="high", description="Finds things in code.",
                    prompt="You find things.\nReport file and line.", skills=[SKILLS[0]])
        args.update(kwargs)
        return roster.add(name, args.pop("model"), args.pop("variant"), args.pop("description"),
                          args.pop("prompt"), args.pop("skills"), **(self.paths | args))

    def in_step(self):
        self.assertTrue(skillset.map_matches(config=self.config, map_path=self.map))

    def refused(self, pattern, function, *args, **kwargs):
        before = self.files()
        with self.assertRaisesRegex(skillset.SkillsetError, pattern):
            function(*args, **(self.paths | kwargs))
        self.assertEqual(self.files(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def customs(self):
        return [name for name in self.data()["presets"][self.data()["preset"]] if name not in roster.BUILTIN]

    def cli(self, *args, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch.object(sys, "stdin", io.StringIO(stdin or "")):
            code = roster.main([*args, "--config", str(self.config), "--map", str(self.map),
                                "--backups", str(self.backups)])
        return code, out.getvalue(), err.getvalue()

    # --- add ---------------------------------------------------------------------------

    def test_add_lists_in_every_preset_and_remove_restores_bytes(self):
        result = self.agent()
        self.assertEqual(result["presets"], list(self.data()["presets"]))
        self.in_step()
        shown = roster.show(config=self.config)
        self.assertEqual(len(shown["presets"]), 4)
        for rows in shown["presets"].values():
            row = {r["name"]: r for r in rows}["scout"]
            self.assertEqual((row["kind"], row["off"], row["remove"]), ("custom", False, "delete"))
        self.assertIn("schema", result)
        self.assertIn("scout", self.map.read_text())
        roster.remove("scout", **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_new_entry_shape_and_permissions(self):
        self.agent()
        self.agent("looker", read_only=True)
        for preset, agents in self.data()["presets"].items():
            self.assertEqual(list(agents["scout"]), ["model", "variant", "description", "skills", "mcps",
                                                     "permission", "prompt", "orchestratorPrompt"])
            self.assertEqual(agents["scout"]["permission"], roster.WRITER_PERMISSION)
            self.assertEqual(agents["looker"]["permission"], roster.READ_ONLY_PERMISSION)
            self.assertEqual(agents["looker"]["permission"], agents["oracle-lite"]["permission"])
            for name in ("scout", "looker"):
                self.assertNotIn("*", agents[name]["permission"])
                self.assertNotIn("allow", [v for v in agents[name]["permission"].values()
                                           if isinstance(v, dict)])
            self.assertEqual(list(agents)[-2:], ["scout", "looker"])
            self.assertTrue(agents["scout"]["orchestratorPrompt"].startswith("@scout"))
        self.in_step()

    def test_named_presets_only(self):
        first = next(iter(self.data()["presets"]))
        self.agent(presets=[first])
        self.assertEqual([p for p, agents in self.data()["presets"].items() if "scout" in agents], [first])
        self.in_step()
        roster.remove("scout", **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_add_refusals(self):
        for name, pattern in (("Scout", "invalid agent name"), ("-x", "invalid agent name"), ("a b", "invalid agent name"),
                              ("", "invalid agent name"), ("x" * 65, "invalid agent name"),
                              ("oracle", "built-in"), ("fixer", "built-in"), ("explore", "built-in"),
                              ("librarian", "built-in"), ("councillor-x", "council seats"),
                              ("councillor", "built-in"), ("agents", "plugin reads"), ("extends", "plugin reads"),
                              ("reviewer", "already exists")):
            with self.subTest(name=name):
                self.refused(pattern, self.agent, name)
        self.refused("provider/model", self.agent, model="nonsense")
        self.refused("provider/model", self.agent, model="")
        self.refused("not installed", self.agent, skills=["not-a-skill"])
        self.refused("wildcard", self.agent, skills=["*"])
        self.refused("duplicate", self.agent, skills=[SKILLS[0], SKILLS[0]])
        self.refused("control character", self.agent, description="a\x00b")
        self.refused("control character", self.agent, description="two\nlines")
        self.refused("control character", self.agent, prompt="x\x1b[0m")
        self.refused("control character", self.agent, prompt="x\u202ey")
        self.refused("too long", self.agent, description="d" * 301)
        self.refused("too long", self.agent, prompt="p" * 8001)
        self.refused("required", self.agent, prompt="  ")
        self.refused("unknown preset", self.agent, presets=["nope"])
        self.refused("invalid color", self.agent, color="red")
        self.assertFalse(self.backups.exists())
        self.agent(description="d" * 300, prompt="p" * 8000)

    def test_refuse_disabled_name(self):
        data = self.data()
        data["disabled_agents"].append("scout")
        self.config.write_text(json.dumps(data, indent=2) + "\n")
        self.original = self.files()
        self.refused("disabled_agents", self.agent)

    def test_refuse_mismatching_map(self):
        self.map.write_bytes(self.original[1].replace(b"| `council` | none |", b"| `council` | all |"))
        self.original = self.files()
        self.refused("does not match", self.agent)

    def test_schema_check_rejects_bad_entry_and_handles_missing_schema(self):
        schema = roster._find_schema()
        if schema is None:
            self.skipTest("the plugin's schema file is not installed")
        agent = roster._agent_schema(json.loads(schema.read_text()))
        good = self.data()["presets"]["normal"]["verifier"]
        self.assertEqual(roster._problems(good, agent), [])
        self.assertTrue(roster._problems({**good, "permission": {"bash": "maybe"}}, agent))
        self.assertTrue(roster._problems({**good, "surprise": 1}, agent))
        self.assertTrue(roster._problems({**good, "color": "red"}, agent))
        self.assertTrue(roster._problems({**good, "skills": "all"}, agent))
        with patch.object(roster, "_problems", return_value=["boom"]):
            self.refused("does not fit the plugin's schema: boom", self.agent)
        result = self.agent(schema=self.root / "missing.json")
        self.assertIn("not checked", result["schema"])

    # --- remove and restore --------------------------------------------------------------

    def test_remove_each_custom_agent_and_restore_bytes(self):
        names = self.customs()
        self.assertIn("verifier", names)
        for name in names:
            with self.subTest(name=name):
                result = roster.remove(name, **self.paths)
                self.assertEqual(result["action"], "deleted")
                self.assertTrue(all(name not in agents for agents in self.data()["presets"].values()))
                self.assertNotIn(f"| `{name}` |", self.map.read_text())
                self.in_step()
                saved = Path(result["record_path"])
                self.assertEqual(json.loads(saved.read_text()), result["record"])
                self.assertEqual([c["preset"] for c in result["record"]["presets"]], list(self.data()["presets"]))
                roster.restore(saved, **self.paths)
                self.assertEqual(self.files(), self.original)
                roster.remove(name, **self.paths)
                roster.restore(result["record"], **self.paths)
                self.assertEqual(self.files(), self.original)

    def test_record_keeps_key_order_and_values(self):
        record = roster.remove("verifier", **self.paths)["record"]
        want = json.loads(self.original[0])["presets"]["fast"]["verifier"]
        saved = {c["preset"]: c for c in record["presets"]}["fast"]
        self.assertEqual(list(saved["value"]), list(want))
        self.assertEqual(saved["value"], want)
        self.assertEqual(json.loads("{" + saved["member"] + "}")["verifier"], want)

    def test_remove_all_customs_and_restore_in_reverse(self):
        records = [roster.remove(name, **self.paths) for name in self.customs()]
        self.assertEqual(self.customs(), [])
        self.in_step()
        for result in reversed(records):
            roster.restore(result["record_path"], **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_restore_after_the_neighbours_changed(self):
        record = roster.remove("verifier", **self.paths)["record"]
        self.agent("late")
        roster.restore(record, **self.paths)
        self.in_step()
        for agents in self.data()["presets"].values():
            self.assertEqual(list(agents)[-2:], ["verifier", "late"])
        roster.remove("late", **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_remove_from_named_preset_only(self):
        first = next(iter(self.data()["presets"]))
        result = roster.remove("reviewer", presets=[first], **self.paths)
        self.assertEqual(result["presets"], [first])
        self.assertEqual([p for p, a in self.data()["presets"].items() if "reviewer" not in a], [first])
        self.in_step()
        roster.restore(result["record"], **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_built_in_is_turned_off_and_restored(self):
        result = roster.remove("explorer", **self.paths)
        self.assertEqual((result["action"], result["kind"]), ("turned off", "built-in"))
        data = self.data()
        self.assertEqual(data["disabled_agents"], [*json.loads(self.original[0])["disabled_agents"], "explorer"])
        self.assertEqual(data["presets"], json.loads(self.original[0])["presets"])  # override stays
        self.assertEqual(self.map.read_bytes(), self.original[1])
        shown = roster.show(config=self.config)
        self.assertTrue({b["name"]: b for b in shown["built_in"]}["explorer"]["off"])
        self.assertTrue(all({r["name"]: r for r in rows}["explorer"]["off"] for rows in shown["presets"].values()))
        roster.restore(result["record_path"], **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_built_in_without_disabled_list_creates_and_removes_the_key(self):
        data = self.data()
        del data["disabled_agents"]
        self.config.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        self.original = self.files()
        result = roster.remove("oracle", **self.paths)
        self.assertEqual(self.data()["disabled_agents"], ["oracle"])
        self.assertTrue(result["record"]["disabled"]["created"])
        roster.restore(result["record"], **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_built_in_into_empty_list(self):
        text = self.original[0].decode()
        start = text.index('"disabled_agents": [')
        end = text.index("]", start) + 1
        self.config.write_text(text[:start] + '"disabled_agents": []' + text[end:])
        self.original = self.files()
        result = roster.remove("designer", **self.paths)
        self.assertEqual(self.data()["disabled_agents"], ["designer"])
        roster.restore(result["record"], **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_remove_refusals(self):
        for name, pattern in (("orchestrator", "cannot be removed"), ("council", "cannot be removed"),
                              ("councillor", "cannot be removed"), ("councillor-anything", "cannot be removed"),
                              ("explore", "alias"), ("observer", "already turned off"),
                              ("fixer", "already turned off"), ("nobody", "not in any selected preset"),
                              ("", "invalid agent name"), ("-x", "invalid agent name")):
            with self.subTest(name=name):
                self.refused(pattern, roster.remove, name)
        seat = sorted(skillset._seat_names(self.data()))[0]
        self.refused("cannot be removed", roster.remove, seat)
        self.refused("per preset", roster.remove, "oracle", presets=["normal"])
        self.refused("unknown preset", roster.remove, "reviewer", presets=["nope"])
        self.assertFalse(self.backups.exists())

    def test_restore_refuses_taken_name_and_bad_records(self):
        result = roster.remove("reviewer", **self.paths)
        self.agent("reviewer")
        self.refused("already exists", roster.restore, result["record"])
        roster.remove("reviewer", **self.paths)
        roster.restore(result["record"], **self.paths)
        self.refused("already exists", roster.restore, result["record"])
        built = roster.remove("explorer", **self.paths)
        roster.restore(built["record"], **self.paths)
        self.refused("not turned off", roster.restore, built["record"])
        good = result["record"]
        roster.remove("reviewer", **self.paths)
        for label, change in (("version", lambda r: r.update(version=2)),
                              ("agent", lambda r: r.update(agent="oracle")),
                              ("kind", lambda r: r.update(kind="odd")),
                              ("form", lambda r: r["presets"][0].update(form="sideways")),
                              ("spacing", lambda r: r["presets"][0].update(after=" ; ")),
                              ("member", lambda r: r["presets"][0].update(member='"reviewer": {"model": "a/b"}, "x": 1')),
                              ("value", lambda r: r["presets"][0]["value"].update(extra=1)),
                              ("preset", lambda r: r["presets"][0].update(preset="gone")),
                              ("map", lambda r: r.update(map={"row": "| `x` | none |\n", "index": 0})),
                              ("empty", lambda r: r.update(presets=[]))):
            with self.subTest(label=label):
                record = json.loads(json.dumps(good))
                change(record)
                self.refused("invalid undo record|no longer exists", roster.restore, record)
        self.refused("regular file", roster.restore, self.root / "nonexistent.json")

    def test_restore_leaves_map_row_position(self):
        result = roster.remove("researcher", **self.paths)
        self.assertEqual(result["record"]["map"]["index"], 7)
        roster.restore(result["record"], **self.paths)
        self.assertEqual(self.map.read_bytes(), self.original[1])

    # --- formats and files -----------------------------------------------------------------

    def test_crlf_compact_and_escaped_files_round_trip(self):
        text = self.original[0].decode().replace("\n", "\r\n").replace('"engineering-delivery"', '"engineering-\\u0064elivery"')
        self.config.write_bytes(text.encode())
        self.map.write_bytes(self.original[1].replace(b"\n", b"\r\n"))
        self.original = self.files()
        self.agent()
        self.in_step()
        self.assertNotIn(b"\n", self.config.read_bytes().replace(b"\r\n", b""))
        roster.remove("scout", **self.paths)
        self.assertEqual(self.files(), self.original)
        record = roster.remove("verifier", **self.paths)["record"]
        roster.restore(record, **self.paths)
        self.assertEqual(self.files(), self.original)
        compact = json.dumps(self.data(), ensure_ascii=False)
        self.config.write_text(compact)
        self.original = self.files()
        self.agent("tiny")
        self.assertEqual(self.data()["presets"]["normal"]["tiny"]["skills"], [SKILLS[0]])
        roster.remove("tiny", **self.paths)
        self.assertEqual(self.files(), self.original)
        record = roster.remove("impl-claude", **self.paths)["record"]
        roster.restore(record, **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_tab_indented_file(self):
        self.config.write_text(json.dumps(self.data(), indent="\t", ensure_ascii=False) + "\n")
        self.original = self.files()
        self.agent()
        self.assertIn('\n\t\t\t"scout": {\n\t\t\t\t"model"', self.config.read_text())
        roster.remove("scout", **self.paths)
        self.assertEqual(self.files(), self.original)

    def test_refuse_symlink_and_special_files(self):
        target = self.root / "real.json"
        self.config.rename(target)
        self.config.symlink_to(target)
        self.original = self.files()
        self.refused("regular file", self.agent)
        self.refused("regular file", roster.remove, "reviewer")
        self.config.unlink()
        target.rename(self.config)
        elsewhere = self.root / "elsewhere.md"
        self.map.rename(elsewhere)
        self.map.symlink_to(elsewhere)
        self.original = self.files()
        self.refused("regular file", self.agent)

    def test_refuse_bad_config(self):
        self.config.write_text('{"preset": "x", "presets": {}}')
        self.original = self.files()
        self.refused("config shape", self.agent)
        self.config.write_text("{")
        self.original = self.files()
        self.refused("not parseable", roster.remove, "reviewer")

    def test_backup_once_per_day_and_modes_kept(self):
        self.config.chmod(0o640)
        first = Path(self.agent()["backup"])
        self.assertRegex(str(first), r"\d{4}-\d{2}-\d{2}-roster-edit/")
        self.assertEqual(first.read_bytes(), self.original[0])
        second = Path(roster.remove("scout", **self.paths)["backup"])
        self.assertEqual(second, first)
        self.assertEqual(first.read_bytes(), self.original[0])
        self.assertEqual(oct(self.config.stat().st_mode & 0o777), "0o640")
        records = list(first.parent.glob("undo-scout-*.json"))
        self.assertEqual(len(records), 1)
        roster.restore(records[0], **self.paths)
        roster.remove("scout", **self.paths)
        self.assertEqual(len(list(first.parent.glob("undo-scout-*.json"))), 2)

    def test_failures_leave_both_files_unchanged(self):
        with patch.object(skillset.os, "replace", side_effect=OSError("simulated before rename")):
            self.refused("simulated before rename", self.agent)
        real = os.replace

        def fail_config(src, dst):
            if dst == self.config:
                raise OSError("simulated config rename failure")
            real(src, dst)

        with patch.object(skillset.os, "replace", side_effect=fail_config):
            self.refused("simulated config rename failure", roster.remove, "reviewer")
        with patch.object(roster, "_save_record", side_effect=OSError("no record")):
            self.refused("no record", roster.remove, "reviewer")
            self.assertEqual(self.files(), self.original)

    def test_faulty_edit_proof_is_refused(self):
        real = roster._put
        with patch.object(roster, "_put", side_effect=lambda text, *a, **k: real(text, *a, **k).replace('"mcps"', '"mcpz"', 1)):
            self.refused("proof failed", self.agent)

    def test_concurrent_processes_both_land(self):
        command = [sys.executable, "-P", "-m", "agentgraph.roster", "--config", str(self.config),
                   "--map", str(self.map), "--backups", str(self.backups), "add"]
        env = os.environ | {"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        prompt = self.root / "p.txt"
        prompt.write_text("Do the work.\n")
        spec = ["--model", "x/y", "--description", "d", "--prompt-file", str(prompt), "--json"]
        with subprocess.Popen([*command, "one", *spec], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as one:
            with subprocess.Popen([*command, "two", *spec], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) as two:
                for process in (one, two):
                    out, err = process.communicate(timeout=20)
                    self.assertEqual(process.returncode, 0, (out, err))
                self.assertTrue({"one", "two"} <= set(self.customs()))
        self.in_step()

    # --- command ---------------------------------------------------------------------------

    def test_cli_round_trip(self):
        code, out, err = self.cli("show", "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(json.loads(out)["presets"]), 4)
        code, text, err = self.cli("show")
        self.assertIn("built-in:", text)
        prompt = self.root / "prompt.md"
        prompt.write_text("Look carefully.\n")
        spec = ["--model", "a/b", "--description", "A looker.", "--skills", f"{SKILLS[0]},{SKILLS[1]}",
                "--read-only", "--presets", "normal,fast"]
        code, out, err = self.cli("add", "looker", *spec, "--prompt-file", str(prompt), "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["presets"], ["normal", "fast"])
        entry = self.data()["presets"]["normal"]["looker"]
        self.assertEqual((entry["prompt"], entry["skills"], entry["permission"]),
                         ("Look carefully.", SKILLS[:2], roster.READ_ONLY_PERMISSION))
        self.assertNotIn("looker", self.data()["presets"]["ultra-fast"])
        code, out, err = self.cli("remove", "looker", "--json")
        self.assertEqual(code, 0)
        record_path = json.loads(out)["record_path"]
        code, text, err = self.cli("restore", record_path)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("restored", text)
        self.cli("remove", "looker")
        self.assertEqual(self.files(), self.original)

    def test_cli_prompt_from_stdin_and_errors(self):
        code, out, err = self.cli("add", "piped", "--model", "a/b", "--description", "Reads stdin.",
                                  "--prompt-file", "-", stdin="From stdin.\n\n")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.data()["presets"]["fast"]["piped"]["prompt"], "From stdin.")
        code, out, err = self.cli("add", "oracle", "--model", "a/b", "--description", "x",
                                  "--prompt-file", "-", "--json", stdin="p")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("built-in", json.loads(out)["error"])
        code, out, err = self.cli("remove", "council")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("roster: "))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            roster.main(["add", "x"])
        self.assertEqual(stop.exception.code, 2)
        code, out, err = self.cli("add", "toolong", "--model", "a/b", "--description", "x",
                                  "--prompt-file", "-", "--json", stdin="p" * 8001)
        self.assertEqual(code, 1)
        self.assertIn("too long", json.loads(out)["error"])

    def test_show_marks_built_in_custom_and_off(self):
        shown = roster.show(config=self.config)
        rows = {r["name"]: r for r in shown["presets"][shown["active_preset"]]}
        self.assertEqual(rows["oracle"]["kind"], "built-in")
        self.assertEqual(rows["oracle"]["remove"], "turn off")
        self.assertEqual(rows["orchestrator"]["remove"], None)
        self.assertEqual(rows["council"]["remove"], None)
        self.assertEqual(rows["verifier"]["kind"], "custom")
        off = {b["name"]: b["off"] for b in shown["built_in"]}
        self.assertTrue(off["observer"] and off["fixer"] and not off["oracle"])
        self.assertEqual(shown["disabled_agents"], ["observer", "fixer"])
        self.assertEqual(self.files(), self.original)


if __name__ == "__main__":
    unittest.main()
