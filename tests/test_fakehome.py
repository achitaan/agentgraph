"""The tests run against a made-up home folder: nothing here may depend on, or touch, the developer's real one."""
import json
import os
from pathlib import Path
import unittest

from agentgraph import skillset, sources
from tests import fakehome
from tests.launch import bare_env


class FakeHomeTests(unittest.TestCase):
    def inside(self, path):
        self.assertTrue(Path(path).resolve().is_relative_to(fakehome.HOME), f"{path} is outside the test home")

    def test_every_home_lookup_lands_in_the_made_up_home(self):
        self.inside(Path.home())
        self.inside(Path("~").expanduser())
        for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
            self.inside(os.environ[name])
        for name in fakehome.CLEARED:
            self.assertNotIn(name, os.environ)
        self.inside(sources.CONFIG_DIR)
        self.inside(skillset.CONFIG)

    def test_the_home_holds_a_fresh_install_settings_file_and_invented_skills(self):
        self.assertTrue(skillset.CONFIG.is_file())
        self.assertIn("presets", json.loads(skillset.CONFIG.read_text(encoding="utf-8")))
        skills = sorted(p.name for p in (sources.CONFIG_DIR / "skills").iterdir())
        self.assertEqual(skills, ["code-assurance", "engineering-delivery", "simplify"])

    def test_a_child_with_a_bare_environment_gets_the_made_up_home_too(self):
        env = bare_env()
        for name in ("HOME", "USERPROFILE"):
            self.assertEqual(Path(env[name]), fakehome.HOME)


if __name__ == "__main__":
    unittest.main()
