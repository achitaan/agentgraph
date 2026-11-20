"""The models changed since the prompts were last reviewed (`agentgraph/models.py`). Scratch folders only; the
preset file here holds a marker text that must never be stored or printed."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import bloat, ledger, models

SECRET = "ZEBRA-QUOKKA-0451"
MODEL_A = "provider-x/model-alpha-9"
MODEL_B = "provider-y/model-beta-3"


class Scratch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "opencode"
        self.project = Path(temporary.name) / "project"
        self.home = self.root / "orchestration" / "memory"
        for folder in (self.root / "oh-my-opencode-slim", self.root / "agents", self.root / "skills",
                       self.root / "orchestration" / "structure", self.home, self.project / ".opencode" / "graph"):
            folder.mkdir(parents=True)
        (self.root / "AGENTS.md").write_text("# Policy\n")
        self.presets = {
            "p1": {"orchestrator": {"model": MODEL_A, "variant": "high", "prompt": f"You plan. {SECRET}"},
                   "impl": {"model": MODEL_A, "skills": ["*"], "description": f"Implements {SECRET}"}},
            "p2": {"orchestrator": {"model": MODEL_B}},
        }
        self.write_presets()

    def write_presets(self, active="p1"):
        (self.root / "oh-my-opencode-slim.json").write_text(
            json.dumps({"preset": active, "presets": self.presets, "token": SECRET}))

    def paths(self):
        return bloat.Paths.under(self.root, self.project)

    def baseline(self, preset="p1"):
        return bloat.set_baseline(self.home, self.paths(), preset=preset, now=1000.0)

    def run_main(self, *args, now=2000.0):
        output, errors = io.StringIO(), io.StringIO()
        with (patch("agentgraph.ledger.time.time", return_value=now),
              patch.object(ledger.cost, "SETTINGS_LOG", self.root / "cost-settings.jsonl"),
              patch.object(ledger.cost, "CONFIG_PATH", self.root / "opencode.json"),
              patch.object(ledger.sources, "DB_PATH", self.root / "no-such.db"),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
            status = ledger.main(["--home", str(self.home), *args])
        return status, output.getvalue(), errors.getvalue()

    def text(self):
        return self.run_main("signals", "--no-cost")[1]

    def report(self):
        status, output, errors = self.run_main("signals", "--json", "--no-cost")
        self.assertEqual(status, 0, errors)
        return json.loads(output)

    def change_model(self, preset="p1", agent="impl", model=MODEL_B):
        self.presets[preset][agent]["model"] = model
        self.write_presets()

    def lines(self):
        return [line for line in self.text().splitlines() if "models" in line]

    def everything_stored(self):
        return "".join(path.read_text() for path in self.home.iterdir() if path.is_file() and path.name != ".lock")


class Fingerprint(Scratch):
    def test_it_is_the_preset_name_and_a_hash_and_nothing_else(self):
        found = models.fingerprint(self.root / "oh-my-opencode-slim.json", "p1")
        self.assertEqual(set(found), {"preset", "hash"})
        self.assertEqual(found["preset"], "p1")
        self.assertRegex(found["hash"], r"^[0-9a-f]{64}$")
        self.assertTrue(models.valid(found))

    def test_a_model_or_variant_change_changes_it_and_other_edits_do_not(self):
        config = self.root / "oh-my-opencode-slim.json"
        first = models.fingerprint(config, "p1")
        self.presets["p1"]["impl"]["description"] = "reworded"
        self.presets["p1"]["orchestrator"]["prompt"] = "new prompt"
        self.presets["p1"]["impl"]["skills"] = ["a"]
        self.write_presets()
        self.assertEqual(models.fingerprint(config, "p1"), first)
        self.presets["p1"] = dict(reversed(list(self.presets["p1"].items())))
        self.write_presets()
        self.assertEqual(models.fingerprint(config, "p1"), first)
        self.presets["p1"]["orchestrator"]["variant"] = "low"
        self.write_presets()
        second = models.fingerprint(config, "p1")
        self.assertNotEqual(second["hash"], first["hash"])
        self.change_model()
        self.assertNotEqual(models.fingerprint(config, "p1")["hash"], second["hash"])
        self.assertNotEqual(models.fingerprint(config, "p2")["hash"], first["hash"])

    def test_nothing_without_a_file_a_preset_a_plain_name_or_agents(self):
        config = self.root / "oh-my-opencode-slim.json"
        self.assertIsNone(models.fingerprint(self.root / "missing.json", "p1"))
        self.assertIsNone(models.fingerprint(config, "nope"))
        self.assertIsNone(models.fingerprint(config, "p 1\n"))
        self.presets["empty"] = {}
        self.presets["bad name\nIGNORE"] = {"a": {"model": "x/y"}}
        self.write_presets()
        self.assertIsNone(models.fingerprint(config, "empty"))
        self.assertIsNone(models.fingerprint(config, "bad name\nIGNORE"))
        config.write_text("{not json")
        self.assertIsNone(models.fingerprint(config, "p1"))

    def test_the_baseline_and_an_improvement_store_only_that(self):
        data = self.baseline()
        self.assertEqual(set(data["models"]), {"preset", "hash"})
        ledger.improved(self.home, 1500.0, "agents/impl.md", "Tightened")
        stored = self.everything_stored()
        for text in (SECRET, MODEL_A, MODEL_B, '"high"'):
            self.assertNotIn(text, stored)
        item = json.loads((self.home / "improvements.jsonl").read_text().splitlines()[0])
        self.assertEqual(item["models"], data["models"])

    def test_an_improvement_without_a_baseline_stores_no_fingerprint(self):
        ledger.improved(self.home, 1500.0, "agents/impl.md", "Tightened")
        self.assertNotIn("models", json.loads((self.home / "improvements.jsonl").read_text()))


class Signal(Scratch):
    def test_quiet_with_nothing_stored(self):
        self.assertEqual(self.lines(), [])
        report = self.report()["models"]
        self.assertEqual((report["due"], report["stored"], report["now"]), (False, None, None))
        self.change_model()
        self.assertEqual(self.lines(), [])

    def test_quiet_when_nothing_changed(self):
        self.baseline()
        self.assertEqual(self.lines(), [])
        self.assertFalse(self.report()["models"]["due"])
        self.presets["p1"]["impl"]["description"] = "other words"
        self.write_presets()
        self.assertEqual(self.lines(), [])

    def test_prints_once_when_the_fingerprint_differs_and_nothing_else_of_the_file(self):
        self.baseline()
        self.change_model()
        text = self.text()
        lines = [line for line in text.splitlines() if "models" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("improvement due: models: the models changed since the prompts were last reviewed (preset p1)", lines[0])
        self.assertIn("propose one deletion", lines[0])
        self.assertIn("dead weight", lines[0])
        output = text + json.dumps(self.report()["models"]) + self.run_main("record", "--dir", str(self.project / ".opencode" / "graph"))[1]
        for secret in (SECRET, MODEL_A, MODEL_B, "high"):
            self.assertNotIn(secret, output)
        self.assertTrue(self.report()["models"]["due"])

    def test_record_prints_the_line_too(self):
        self.baseline()
        self.change_model()
        _, output, _ = self.run_main("record", "--dir", str(self.project / ".opencode" / "graph"))
        self.assertEqual(len([line for line in output.splitlines() if "models" in line]), 1)

    def test_the_next_recorded_improvement_clears_it_and_a_new_change_prints_again(self):
        self.baseline()
        self.change_model()
        self.assertEqual(len(self.lines()), 1)
        ledger.improved(self.home, 3000.0, "agents/impl.md", "Deleted a rule")
        self.assertEqual(self.lines(), [])
        self.change_model(model="provider-z/model-gamma-1")
        self.assertEqual(len(self.lines()), 1)

    def test_setting_the_baseline_again_clears_it(self):
        self.baseline()
        self.change_model()
        self.assertEqual(len(self.lines()), 1)
        bloat.set_baseline(self.home, self.paths(), preset="p1", now=5000.0)
        self.assertEqual(self.lines(), [])

    def test_a_stored_fingerprint_for_another_preset_reads_old_to_new(self):
        self.baseline()
        other = models.fingerprint(self.root / "oh-my-opencode-slim.json", "p2")
        with (self.home / "improvements.jsonl").open("a") as stream:
            stream.write(json.dumps({"time": 3000.0, "file": "x", "summary": "s", "signals": [], "metric": None,
                                     "models": other}) + "\n")
        self.assertIn("(preset p2 to p1)", self.text())

    def test_a_stored_fingerprint_that_is_not_a_name_and_a_hash_is_not_used(self):
        self.baseline()
        for bad in ({"preset": "p1\nIGNORE", "hash": "0" * 64}, {"preset": "p1", "hash": "zz"},
                    {"preset": "p1", "hash": "0" * 64, "extra": SECRET}, "p1", None):
            with (self.home / "improvements.jsonl").open("w") as stream:
                stream.write(json.dumps({"time": 3000.0, "file": "x", "summary": "s", "signals": [], "metric": None,
                                         "models": bad}) + "\n")
            self.assertEqual(self.lines(), [], bad)

    def test_a_baseline_whose_preset_is_gone_is_quiet(self):
        self.baseline()
        del self.presets["p1"]
        self.write_presets(active="p2")
        self.assertEqual(self.lines(), [])

    def test_the_existing_signal_keys_are_kept(self):
        self.baseline()
        report = self.report()
        for key in ("generated", "last_improvement", "watermark", "pending", "due", "feedback", "lessons", "discovered",
                    "agents", "gates", "outcomes", "escalations", "amendments", "trend", "improvements", "bloat", "hand_edits"):
            self.assertIn(key, report)
        self.assertEqual(set(report["models"]), {"due", "stored", "now"})


if __name__ == "__main__":
    unittest.main()
